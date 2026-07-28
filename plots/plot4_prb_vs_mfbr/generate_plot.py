#!/usr/bin/env python3
"""Compare the existing PRB-quota RIC action (Style 2 / Action 6) against our DRB QoS MFBR xApp
(Style 1 / Action 1), at a roughly matched operating point.

Two independent live tests, overlaid on a common "seconds since cap applied" axis:
  - PRB quota: max_prb_ratio=10 (~10% of this cell's 106 PRBs, i.e. ~10-11 PRBs for the
    20 MHz / 15 kHz SCS config in gnb_zmq.yaml), applied via prb_quota_test_xapp.py, data in
    ./data/ (this folder).
  - DRB QoS MFBR: cap=2 Mbps, the closest existing MFBR phase to what the PRB quota happened to
    produce - reused from ../plot1_enforcement_timeseries/data/ (no new test needed for this side).

The two mechanisms are NOT interchangeable in what they let an operator specify: MFBR's 2 Mbps is
the number that was actually requested (a rate contract, TS 23.501 GBR semantics); the PRB run's
~2.2 Mbps is an emergent side effect of "10% of the cell's PRBs at whatever MCS the scheduler
happens to pick" - nothing in that control action lets you ask for a bitrate directly. That gap
between "specified" and "measured after the fact" is the point of this figure, not the small
numeric difference between the two steady-state rates.

Output:
  - plot4_prb_vs_mfbr.{png,pdf}
  - plot4_prb_vs_mfbr_caption.txt
"""
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

HERE = Path(__file__).parent
PLOT1_DIR = HERE.parent / "plot1_enforcement_timeseries"
sys.path.insert(0, str(PLOT1_DIR))
import generate_plot as p1  # reuse Plot 1's parsing/phase-building logic - single source of truth

COLOR_PRB = p1.COLOR_OFFERED       # green - reuse categorical slot 2 for the second mechanism
COLOR_MFBR = p1.COLOR_DELIVERED    # blue - same "delivered throughput" identity as Plots 1-3
COLOR_CAP_LINE = p1.COLOR_CAP_LINE
COLOR_PHASE_SHADE = p1.COLOR_PHASE_SHADE
COLOR_GRID = p1.COLOR_GRID
COLOR_AXIS = p1.COLOR_AXIS
COLOR_MUTED = p1.COLOR_MUTED
COLOR_TEXT = p1.COLOR_TEXT
COLOR_TEXT_SECONDARY = p1.COLOR_TEXT_SECONDARY

CELL_TOTAL_PRBS = 106  # 20 MHz / 15 kHz SCS, per gnb_zmq.yaml's cell_cfg - see module docstring


def parse_prb_events(path, ref_epoch):
    """Parse prb_quota_test_xapp.py's APPLY/REMOVE lines into (epoch, action, max_prb_ratio)."""
    ref_date = datetime.fromtimestamp(ref_epoch, tz=timezone.utc).date()
    pattern = re.compile(
        r"(\d{2}):(\d{2}):(\d{2})\s+\[(APPLY|REMOVE) - max_prb_ratio=(\d+)\] Sending RIC Control Request"
    )
    events = []
    for line in Path(path).read_text().splitlines():
        m = pattern.search(line)
        if not m:
            continue
        hh, mm, ss, action, ratio = m.groups()
        dt = datetime(ref_date.year, ref_date.month, ref_date.day,
                      int(hh), int(mm), int(ss), tzinfo=timezone.utc)
        events.append((dt.timestamp(), action, int(ratio)))
    events.sort()
    return events


def mean(series, a, b):
    vals = [m for e, m in series if a <= e <= b]
    return sum(vals) / len(vals) if vals else float("nan")


def stdev(series, a, b):
    vals = [m for e, m in series if a <= e <= b]
    if len(vals) < 2:
        return float("nan")
    mu = sum(vals) / len(vals)
    return (sum((v - mu) ** 2 for v in vals) / (len(vals) - 1)) ** 0.5


def load_prb_run():
    ref_epoch = None
    for line in (HERE / "data" / "test_timing.txt").read_text().splitlines():
        if line.startswith("test_start_epoch="):
            ref_epoch = int(line.split("=", 1)[1])
    assert ref_epoch is not None

    server_docs = [d for d in p1.load_json_documents(PLOT1_DIR / "data" / "iperf_server.log")
                   if "timestamp" in d.get("start", {})]
    server_doc = min(server_docs, key=lambda d: abs(d["start"]["timestamp"]["timesecs"] - ref_epoch))
    delivered = p1.intervals_to_series(server_doc)
    events = parse_prb_events(HERE / "data" / "xapp_events.log", ref_epoch)

    apply_epoch = next(e for e, a, _ in events if a == "APPLY")
    remove_epoch = next(e for e, a, _ in events if a == "REMOVE")
    ratio = next(r for _, a, r in events if a == "APPLY")

    window_start = apply_epoch - 45
    window_end = remove_epoch + 45
    delivered = [(e - apply_epoch, m) for e, m in delivered if window_start <= e <= window_end]
    return delivered, 0.0, remove_epoch - apply_epoch, ratio


def load_mfbr_run(cap_mbps=2.0):
    ref_epoch = None
    for line in (PLOT1_DIR / "data" / "test_timing.txt").read_text().splitlines():
        if line.startswith("test_start_epoch="):
            ref_epoch = int(line.split("=", 1)[1])
    assert ref_epoch is not None

    server_docs = [d for d in p1.load_json_documents(PLOT1_DIR / "data" / "iperf_server.log")
                   if "timestamp" in d.get("start", {})]
    server_doc = min(server_docs, key=lambda d: abs(d["start"]["timestamp"]["timesecs"] - ref_epoch))
    delivered_full = p1.intervals_to_series(server_doc)
    events = p1.parse_xapp_events(PLOT1_DIR / "data" / "xapp_events.log", ref_epoch)

    t0 = delivered_full[0][0]
    t_end_cutoff = events[-1][0] + 45
    delivered_full = [(e, m) for e, m in delivered_full if e <= t_end_cutoff]
    t_end = min(t_end_cutoff, delivered_full[-1][0])
    phases = p1.build_phases(events, t0, t_end)

    idx = next(i for i, (_, _, c) in enumerate(phases) if c == cap_mbps)
    t_a, t_b, _ = phases[idx]
    window_start = t_a - 45
    window_end = t_b + 45
    delivered = [(e - t_a, m) for e, m in delivered_full if window_start <= e <= window_end]
    return delivered, 0.0, t_b - t_a


def main():
    prb_delivered, prb_t_a, prb_duration, prb_ratio = load_prb_run()
    mfbr_cap = 2.0
    mfbr_delivered, mfbr_t_a, mfbr_duration = load_mfbr_run(mfbr_cap)

    prb_steady = mean(prb_delivered, 5, prb_duration)
    prb_std = stdev(prb_delivered, 5, prb_duration)
    mfbr_steady = mean(mfbr_delivered, 5, mfbr_duration)
    mfbr_std = stdev(mfbr_delivered, 5, mfbr_duration)
    approx_prbs = prb_ratio / 100.0 * CELL_TOTAL_PRBS

    # --- plot ---
    plt.rcParams["font.family"] = "sans-serif"
    plt.rcParams["font.sans-serif"] = ["DejaVu Sans", "Arial", "Helvetica"]

    fig, ax = plt.subplots(figsize=(8.4, 4.4), dpi=200)
    fig.patch.set_facecolor("#fcfcfb")
    ax.set_facecolor("#fcfcfb")

    win_end = max(prb_duration + 45, mfbr_duration + 45)
    ax.set_xlim(-10, win_end)

    ax.axvspan(0, mfbr_duration, color=COLOR_PHASE_SHADE, alpha=0.10, lw=0)
    # Full-width reference line (not just the cap-active span) so it's easy to compare against
    # both throughput lines throughout, not only inside the shaded region.
    ax.hlines(mfbr_cap, -10, win_end, colors=COLOR_CAP_LINE, linestyles=(0, (4, 3)),
               linewidth=1.6, zorder=3)
    ax.text(mfbr_duration / 2, mfbr_cap + 0.35, f"MFBR target = {mfbr_cap:.0f} Mbps",
             ha="center", va="bottom", fontsize=8.5, color=COLOR_TEXT_SECONDARY)

    t_prb = [e for e, _ in prb_delivered]
    y_prb = [m for _, m in prb_delivered]
    t_mfbr = [e for e, _ in mfbr_delivered]
    y_mfbr = [m for _, m in mfbr_delivered]

    ax.plot(t_prb, y_prb, color=COLOR_PRB, linewidth=1.8, zorder=4,
             label=f"PRB quota (Style 2/Action 6, max_prb_ratio={prb_ratio}%)")
    ax.plot(t_mfbr, y_mfbr, color=COLOR_MFBR, linewidth=1.8, zorder=4,
             label=f"DRB QoS MFBR (Style 1/Action 1, cap={mfbr_cap:.0f} Mbps)")

    ax.set_ylim(0, max(y_prb + y_mfbr) * 1.2)
    ax.set_xlabel("Time since cap applied (s)", color=COLOR_TEXT_SECONDARY, fontsize=10)
    ax.set_ylabel("UL throughput (Mbps)", color=COLOR_TEXT_SECONDARY, fontsize=10)

    ax.grid(True, axis="y", color=COLOR_GRID, linewidth=0.8, zorder=0)
    ax.grid(False, axis="x")
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color(COLOR_AXIS)
    ax.tick_params(colors=COLOR_MUTED, labelsize=9)

    legend_handles = [
        plt.Line2D([0], [0], color=COLOR_MFBR, linewidth=1.8,
                    label=f"DRB QoS MFBR (Style 1/Action 1, cap={mfbr_cap:.0f} Mbps)"),
        plt.Line2D([0], [0], color=COLOR_PRB, linewidth=1.8,
                    label=f"PRB quota (Style 2/Action 6, max_prb_ratio={prb_ratio}%)"),
        plt.Line2D([0], [0], color=COLOR_CAP_LINE, linewidth=1.6, linestyle=(0, (4, 3)),
                    label="Configured MFBR target"),
        Patch(facecolor=COLOR_PHASE_SHADE, alpha=0.10, label="Cap/quota active"),
    ]
    ax.legend(handles=legend_handles, loc="upper left", frameon=False, fontsize=8,
               labelcolor=COLOR_TEXT_SECONDARY, bbox_to_anchor=(0.0, -0.18), ncol=1)

    fig.tight_layout()
    fig.savefig(HERE / "plot4_prb_vs_mfbr.png", facecolor=fig.get_facecolor(), bbox_inches="tight")
    fig.savefig(HERE / "plot4_prb_vs_mfbr.pdf", facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    print("Wrote plot4_prb_vs_mfbr.png / .pdf")

    # --- caption ---
    caption = (
        f"Delivered UL throughput under two different RIC control actions, each applied to the same DRB and "
        f"observed for {mfbr_duration:.0f} s, aligned on a common \"time since cap applied\" axis (shaded "
        f"region). DRB QoS MFBR (Style 1/Action 1, blue) is configured for a {mfbr_cap:.0f} Mbps rate target "
        f"and converges to a steady-state mean of {mfbr_steady:.2f} Mbps (std {mfbr_std:.2f} Mbps, t > 5 s). "
        f"PRB quota (Style 2/Action 6, green) caps this UE to {prb_ratio}% of the cell's PRBs "
        f"(~{approx_prbs:.0f} of {CELL_TOTAL_PRBS} PRBs for this cell's 20 MHz/15 kHz-SCS configuration) and "
        f"happens to settle at a steady-state mean of {prb_steady:.2f} Mbps (std {prb_std:.2f} Mbps) under the "
        f"channel conditions and MCS in effect during this run - a number the operator could not have "
        f"specified directly, since the control action only accepts a PRB percentage, not a bitrate. The two "
        f"steady-state rates happen to be numerically close here, but only one of them (MFBR) is the value "
        f"that was actually requested; the PRB-quota outcome is a downstream side effect of the current radio "
        f"conditions and would shift if the UE's achievable MCS changed, without any change to the {prb_ratio}% "
        f"configuration itself. A second, mechanistic difference is also visible: MFBR shows a brief near-zero "
        f"transient in the first ~2 s (its token-bucket rate limiter draining the pre-existing uncapped "
        f"backlog, see Plot 1's README), while the PRB quota - a hard resource ceiling enforced from the next "
        f"scheduling opportunity, with no equivalent token bucket - transitions directly to its steady level."
    )
    (HERE / "plot4_prb_vs_mfbr_caption.txt").write_text(caption + "\n")
    print("Wrote plot4_prb_vs_mfbr_caption.txt")
    print(f"MFBR steady: {mfbr_steady:.3f} +/- {mfbr_std:.3f} Mbps")
    print(f"PRB steady:  {prb_steady:.3f} +/- {prb_std:.3f} Mbps (~{approx_prbs:.1f} PRBs)")


if __name__ == "__main__":
    main()
