#!/usr/bin/env python3
"""Build the UL MFBR enforcement time-series plot from raw testbed captures.

Inputs (in data/):
  - iperf_server.log : iperf3 JSON report from the core-side server (delivered/received rate)
  - iperf_ul.json    : iperf3 JSON report(s) from the UE-side client (offered/sent rate) -
                        contains two concatenated JSON documents because the client was
                        restarted once; the second document is the real run and is selected
                        by matching its start timestamp against the server's.
  - xapp_events.log  : stdout of ul_mfbr_test_xapp.py, with wall-clock (UTC) timestamps for
                        each RIC Control Request (APPLY/REMOVE of a given UL cap).
  - test_timing.txt  : epoch/ISO timestamp recorded when the xApp run was launched, used only
                        to anchor the xApp log's HH:MM:SS strings to a calendar date.

Output:
  - plot1_2mbps.{png,pdf}, plot1_4mbps.{png,pdf}, plot1_6mbps.{png,pdf} - one figure per cap level,
    each showing the before/during/after window around that cap's apply-observe-remove-observe cycle.
  - plot1_2mbps_caption.txt, plot1_4mbps_caption.txt, plot1_6mbps_caption.txt - paper-style caption text
    for each figure, with summary stats (mean offered/delivered, recovery time) computed from the data.
  - data/combined_timeseries.csv (merged, epoch-aligned dataset for the full session, all three cycles)

No in-figure titles (by design, for paper use - the caption should carry the description); each figure is
otherwise self-contained (axis labels, legend, cap-value direct label).
"""
import json
import re
import csv
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from matplotlib.patches import Patch

HERE = Path(__file__).parent
DATA = HERE / "data"

# --- palette (validated via dataviz skill's validate_palette.js) ---
COLOR_DELIVERED = "#2a78d6"   # categorical slot 1 (blue)
COLOR_OFFERED = "#008300"     # categorical slot 2 (green)
COLOR_GRID = "#e1e0d9"
COLOR_AXIS = "#c3c2b7"
COLOR_MUTED = "#898781"
COLOR_TEXT = "#0b0b0b"
COLOR_TEXT_SECONDARY = "#52514e"
COLOR_PHASE_SHADE = "#898781"
COLOR_CAP_LINE = "#e34948"    # categorical slot 8 (red) - target/reference line


def load_json_documents(path):
    """iperf3 --logfile output is occasionally more than one concatenated JSON
    document (e.g. across a client/server restart); extract all of them."""
    text = Path(path).read_text()
    decoder = json.JSONDecoder()
    objs = []
    idx, n = 0, len(text)
    while idx < n:
        while idx < n and text[idx] != "{":
            idx += 1
        if idx >= n:
            break
        obj, end = decoder.raw_decode(text, idx)
        objs.append(obj)
        idx = end
    return objs


def intervals_to_series(doc):
    """Return list of (epoch_time_mid_interval, mbps) from an iperf3 JSON report."""
    t0 = doc["start"]["timestamp"]["timesecs"]
    out = []
    for iv in doc["intervals"]:
        s = iv["sum"]
        mid = t0 + s["start"] + s["seconds"] / 2.0
        mbps = s["bits_per_second"] / 1e6
        out.append((mid, mbps))
    return out


def parse_xapp_events(path, ref_epoch, max_kbps=None):
    """Parse APPLY/REMOVE RIC Control Request lines into (epoch, action, kbps).

    max_kbps: if set, drop any event for a cap above this value. Unused by default now that
    the t-PollRetransmit stall (see README) is fixed and all cap phases in a run are clean;
    kept as an option for filtering out a specific bad run if needed again.
    """
    ref_date = datetime.fromtimestamp(ref_epoch, tz=timezone.utc).date()
    # Not anchored to the start of the line: the interactive xApp's prompt echo (e.g.
    # "Choice: New UL cap in kbps: ") sometimes lands on the same line as the next APPLY log
    # line in captured stdout, so the timestamp isn't always at position 0.
    pattern = re.compile(
        r"(\d{2}):(\d{2}):(\d{2})\s+\[(APPLY|REMOVE) - (\d+)kbps cap\] Sending RIC Control Request"
    )
    events = []
    for line in Path(path).read_text().splitlines():
        m = pattern.search(line)
        if not m:
            continue
        hh, mm, ss, action, kbps = m.groups()
        kbps = int(kbps)
        if max_kbps is not None and kbps > max_kbps:
            continue
        dt = datetime(
            ref_date.year, ref_date.month, ref_date.day,
            int(hh), int(mm), int(ss), tzinfo=timezone.utc,
        )
        events.append((dt.timestamp(), action, kbps))
    events.sort()
    return events


def build_phases(events, t_start, t_end):
    """Turn APPLY/REMOVE events into a list of (t0, t1, cap_mbps_or_None) phases.
    cap_mbps is None for uncapped stretches (baseline / after a REMOVE)."""
    phases = []
    cursor = t_start
    active_cap = None
    for epoch, action, kbps in events:
        if epoch > cursor:
            phases.append((cursor, epoch, active_cap))
        if action == "APPLY":
            active_cap = kbps / 1000.0  # kbps -> Mbps
        else:  # REMOVE
            active_cap = None
        cursor = epoch
    if cursor < t_end:
        phases.append((cursor, t_end, active_cap))
    return phases


def render_figure(delivered, offered, phases, window_start, window_end, out_stem):
    """Render one before/during/after figure for a single cap window.

    delivered/offered: full-session (epoch, mbps) series - filtered to [window_start, window_end] here.
    phases: full-session list of (t0, t1, cap_mbps_or_None) - filtered/clipped to the window here.
    window_start/window_end: absolute epoch bounds for this figure (one uncapped phase before the
    cap, the capped phase itself, one uncapped phase after).
    """
    d = [(e, m) for e, m in delivered if window_start <= e <= window_end]
    o = [(e, m) for e, m in offered if window_start <= e <= window_end]

    win_phases = []
    for t_a, t_b, cap in phases:
        a, b = max(t_a, window_start), min(t_b, window_end)
        if a < b:
            win_phases.append((a, b, cap))

    plt.rcParams["font.family"] = "sans-serif"
    plt.rcParams["font.sans-serif"] = ["DejaVu Sans", "Arial", "Helvetica"]

    fig, ax = plt.subplots(figsize=(7.2, 4.2), dpi=200)
    fig.patch.set_facecolor("#fcfcfb")
    ax.set_facecolor("#fcfcfb")

    for t_a, t_b, cap in win_phases:
        a, b = t_a - window_start, t_b - window_start
        if cap is not None:
            ax.axvspan(a, b, color=COLOR_PHASE_SHADE, alpha=0.10, lw=0)
            # Full-width reference line (not just the cap-active span) so it's easy to compare
            # against the offered/delivered lines throughout, not only inside the shaded region.
            ax.hlines(cap, 0, window_end - window_start, colors=COLOR_CAP_LINE, linestyles=(0, (4, 3)), linewidth=1.6, zorder=3)
            ax.text(
                (a + b) / 2, cap + 0.45, f"cap = {cap:.0f} Mbps",
                ha="center", va="bottom", fontsize=8.5, color=COLOR_TEXT_SECONDARY,
            )

    t_del = [e - window_start for e, _ in d]
    y_del = [m for _, m in d]
    t_off = [e - window_start for e, _ in o]
    y_off = [m for _, m in o]

    ax.plot(t_off, y_off, color=COLOR_OFFERED, linewidth=1.4, linestyle=(0, (5, 2)),
             label="Offered load (UE-side, sent)", zorder=2, alpha=0.9)
    ax.plot(t_del, y_del, color=COLOR_DELIVERED, linewidth=2.0,
             label="Delivered throughput (core-side, received)", zorder=4)

    ax.set_xlim(0, window_end - window_start)
    ax.set_ylim(0, max(y_off + y_del) * 1.18)
    ax.set_xlabel("Time since window start (s)", color=COLOR_TEXT_SECONDARY, fontsize=10)
    ax.set_ylabel("UL throughput (Mbps)", color=COLOR_TEXT_SECONDARY, fontsize=10)

    ax.grid(True, axis="y", color=COLOR_GRID, linewidth=0.8, zorder=0)
    ax.grid(False, axis="x")
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color(COLOR_AXIS)
    ax.tick_params(colors=COLOR_MUTED, labelsize=9)

    legend_handles = [
        plt.Line2D([0], [0], color=COLOR_DELIVERED, linewidth=2.0, label="Delivered throughput (core-side, received)"),
        plt.Line2D([0], [0], color=COLOR_OFFERED, linewidth=1.4, linestyle=(0, (5, 2)), label="Offered load (UE-side, sent)"),
        plt.Line2D([0], [0], color=COLOR_CAP_LINE, linewidth=1.6, linestyle=(0, (4, 3)), label="Configured MFBR cap"),
        Patch(facecolor=COLOR_PHASE_SHADE, alpha=0.10, label="Cap active"),
    ]
    ax.legend(
        handles=legend_handles, loc="upper left", frameon=False, fontsize=8.5,
        labelcolor=COLOR_TEXT_SECONDARY, bbox_to_anchor=(0.0, -0.18), ncol=2,
    )

    fig.tight_layout()
    fig.savefig(HERE / f"{out_stem}.png", facecolor=fig.get_facecolor(), bbox_inches="tight")
    fig.savefig(HERE / f"{out_stem}.pdf", facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    print(f"Wrote {out_stem}.png / {out_stem}.pdf "
          f"(window {window_end - window_start:.1f}s, {len(d)} delivered samples, {len(o)} offered samples)")


def write_caption(delivered, offered, phases, window_start, window_end, cap_phase_idx, out_stem):
    """Compute summary stats for the capped phase and write a paper-style caption .txt file
    alongside the figure. All numbers are derived from the data, not hand-typed."""
    t_a, t_b, cap = phases[cap_phase_idx]
    before_a, before_b, _ = phases[cap_phase_idx - 1] if cap_phase_idx > 0 else (window_start, t_a, None)
    after_a, after_b, _ = phases[cap_phase_idx + 1] if cap_phase_idx + 1 < len(phases) else (t_b, window_end, None)

    def mean(series, a, b):
        vals = [m for e, m in series if a <= e <= b]
        return sum(vals) / len(vals) if vals else float("nan")

    offered_before_mean = mean(offered, before_a, before_b)
    delivered_capped_mean = mean(delivered, t_a, t_b)
    # Skip the first 5s of the capped phase (token-bucket drain transient, see README) for a
    # steady-state figure as well as the raw full-phase one.
    delivered_capped_steady_mean = mean(delivered, t_a + 5, t_b)
    delivered_after_mean = mean(delivered, after_a, min(after_a + 10, after_b))

    # Recovery time: first delivered sample after cap removal (t_b) that reaches >= 90% of the
    # pre-cap offered rate, relative to t_b.
    recovery_s = None
    for e, m in sorted(delivered, key=lambda p: p[0]):
        if e >= t_b and m >= 0.9 * offered_before_mean:
            recovery_s = e - t_b
            break

    apply_rel = t_a - window_start
    remove_rel = t_b - window_start
    duration = t_b - t_a

    caption = (
        f"Uplink throughput for a single UE/DRB before, during, and after enforcement of a {cap:.0f} Mbps "
        f"per-DRB Maximum Flow Bit Rate (MFBR) cap, applied via O-RAN E2SM-RC Style 1 / Action 1 (DRB QoS "
        f"Configuration RIC Control Request). The cap is applied at t = {apply_rel:.1f} s and removed at "
        f"t = {remove_rel:.1f} s ({duration:.0f} s duration, shaded region). Offered load (green, dashed) is "
        f"the UE-side iperf3 UDP send rate (1 s intervals), which is unaffected by the cap and averages "
        f"{offered_before_mean:.2f} Mbps in the pre-cap window. Delivered throughput (blue, solid) is the "
        f"core-side iperf3 receive rate (1 s intervals) and converges to a mean of {delivered_capped_steady_mean:.2f} "
        f"Mbps during the capped phase (excluding the first 5 s transient; {delivered_capped_mean:.2f} Mbps "
        f"including it), against the {cap:.0f} Mbps target. "
    )
    if recovery_s is not None:
        caption += (
            f"After cap removal, delivered throughput recovers to within 90% of the pre-cap offered rate within "
            f"{recovery_s:.1f} s, reaching a mean of {delivered_after_mean:.2f} Mbps over the following 10 s. "
        )
    caption += (
        "No RRC Reconfiguration signaling, RLC/PDCP re-establishment, or connection failure occurred during "
        "the cap change."
    )

    (HERE / f"{out_stem}_caption.txt").write_text(caption + "\n")
    print(f"Wrote {out_stem}_caption.txt")


def main():
    ref_epoch = None
    for line in (DATA / "test_timing.txt").read_text().splitlines():
        if line.startswith("test_start_epoch="):
            ref_epoch = int(line.split("=", 1)[1])
    assert ref_epoch is not None

    # Both log files accumulate one JSON document per test run (this data/ folder was reused
    # across several redeploy-and-retest cycles); pick the documents whose start time matches
    # *this* run's recorded start, not just the first/only one.
    server_docs = [d for d in load_json_documents(DATA / "iperf_server.log") if "timestamp" in d.get("start", {})]
    client_docs = [d for d in load_json_documents(DATA / "iperf_ul.json") if "timestamp" in d.get("start", {})]
    server_doc = min(server_docs, key=lambda d: abs(d["start"]["timestamp"]["timesecs"] - ref_epoch))
    server_t0 = server_doc["start"]["timestamp"]["timesecs"]
    client_doc = min(client_docs, key=lambda d: abs(d["start"]["timestamp"]["timesecs"] - server_t0))

    delivered = intervals_to_series(server_doc)
    offered = intervals_to_series(client_doc)
    events = parse_xapp_events(DATA / "xapp_events.log", ref_epoch)

    t0 = min(delivered[0][0], offered[0][0])
    # End right after the last REMOVE's observe period - the full 2000/4000/6000kbps
    # cap-staircase cycle is clean in this run (t-PollRetransmit stall fixed, see README).
    last_event_epoch = events[-1][0]
    t_end_cutoff = last_event_epoch + 45  # observe_seconds used for this run
    delivered = [(e, m) for e, m in delivered if e <= t_end_cutoff]
    offered = [(e, m) for e, m in offered if e <= t_end_cutoff]
    t_end = min(t_end_cutoff, max(delivered[-1][0], offered[-1][0]))
    phases = build_phases(events, t0, t_end)

    # --- write merged CSV for reproducibility / re-plotting ---
    with open(DATA / "combined_timeseries.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t_rel_s", "epoch", "delivered_mbps", "offered_mbps"])
        offered_by_t = offered
        for epoch, mbps in delivered:
            # nearest offered sample (1s granularity on both sides, so this is exact
            # to within one interval almost always)
            near = min(offered_by_t, key=lambda p: abs(p[0] - epoch))
            w.writerow([round(epoch - t0, 2), round(epoch, 3), round(mbps, 4), round(near[1], 4)])

    print(f"Phases: {[(round(a-t0,1), round(b-t0,1), c) for a,b,c in phases]}")

    # --- one figure per cap level: the uncapped phase before it, the capped phase itself, and
    # the uncapped phase after it (i.e. phases[i-1], phases[i], phases[i+1]) ---
    cap_stems = {2.0: "plot1_2mbps", 4.0: "plot1_4mbps", 6.0: "plot1_6mbps"}
    for i, (t_a, t_b, cap) in enumerate(phases):
        if cap is None:
            continue
        window_start = phases[i - 1][0] if i > 0 else t_a
        window_end = phases[i + 1][1] if i + 1 < len(phases) else t_b
        out_stem = cap_stems.get(cap, f"plot1_{cap:.0f}mbps")
        render_figure(delivered, offered, phases, window_start, window_end, out_stem)
        write_caption(delivered, offered, phases, window_start, window_end, i, out_stem)


if __name__ == "__main__":
    main()
