#!/usr/bin/env python3
"""Packet-loss-during-enforcement plot, reusing Plot 1's raw testbed captures.

Answers: is excess offered load actively shed by the RAN once a UL MFBR cap is enforced (visible
as core-side UDP packet loss), rather than silently buffered/delayed? Uses the same 2/4/6 Mbps
cap-staircase run as Plot 1 (`../plot1_enforcement_timeseries/data/`) - no new live test needed.
iperf3's UDP server-side report already includes per-interval `lost_percent` and `jitter_ms`
(receiver-computed, since only the receiver can detect gaps in the UDP sequence numbers), so this
reuses the exact same `iperf_server.log` capture as Plot 1's delivered-throughput line - just a
different field from the same JSON.

Output (one per cap level, same before/during/after windowing as Plot 1):
  - plot3_2mbps_loss.{png,pdf}, plot3_4mbps_loss.{png,pdf}, plot3_6mbps_loss.{png,pdf}
  - plot3_{2,4,6}mbps_loss_caption.txt
"""
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

HERE = Path(__file__).parent
PLOT1_DIR = HERE.parent / "plot1_enforcement_timeseries"
sys.path.insert(0, str(PLOT1_DIR))
import generate_plot as p1  # reuse Plot 1's parsing/phase-building logic - single source of truth

COLOR_LOSS = p1.COLOR_DELIVERED
COLOR_PHASE_SHADE = p1.COLOR_PHASE_SHADE
COLOR_GRID = p1.COLOR_GRID
COLOR_AXIS = p1.COLOR_AXIS
COLOR_MUTED = p1.COLOR_MUTED
COLOR_TEXT = p1.COLOR_TEXT
COLOR_TEXT_SECONDARY = p1.COLOR_TEXT_SECONDARY


def server_metric_series(doc, field):
    """Like p1.intervals_to_series, but pulls an arbitrary field (e.g. lost_percent, jitter_ms)
    from the server-side iperf3 report instead of bits_per_second."""
    t0 = doc["start"]["timestamp"]["timesecs"]
    out = []
    for iv in doc["intervals"]:
        s = iv["sum"]
        mid = t0 + s["start"] + s["seconds"] / 2.0
        out.append((mid, s[field]))
    return out


def render_loss_figure(loss, phases, window_start, window_end, out_stem):
    l = [(e, v) for e, v in loss if window_start <= e <= window_end]

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

    t_l = [e - window_start for e, _ in l]
    y_l = [v for _, v in l]
    ax.set_xlim(0, window_end - window_start)
    ax.set_ylim(0, max(max(y_l) * 1.25, 1.0))

    for t_a, t_b, cap in win_phases:
        a, b = t_a - window_start, t_b - window_start
        if cap is not None:
            ax.axvspan(a, b, color=COLOR_PHASE_SHADE, alpha=0.10, lw=0)
            ax.text((a + b) / 2, ax.get_ylim()[1] * 0.96, f"cap = {cap:.0f} Mbps",
                     ha="center", va="top", fontsize=8.5, color=COLOR_TEXT_SECONDARY)

    ax.plot(t_l, y_l, color=COLOR_LOSS, linewidth=1.6, zorder=4, label="Packet loss (core-side, received)")

    ax.set_xlabel("Time since window start (s)", color=COLOR_TEXT_SECONDARY, fontsize=10)
    ax.set_ylabel("Packet loss (%)", color=COLOR_TEXT_SECONDARY, fontsize=10)

    ax.grid(True, axis="y", color=COLOR_GRID, linewidth=0.8, zorder=0)
    ax.grid(False, axis="x")
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color(COLOR_AXIS)
    ax.tick_params(colors=COLOR_MUTED, labelsize=9)

    legend_handles = [
        plt.Line2D([0], [0], color=COLOR_LOSS, linewidth=1.6, label="Packet loss (core-side, received)"),
        Patch(facecolor=COLOR_PHASE_SHADE, alpha=0.10, label="Cap active"),
    ]
    ax.legend(handles=legend_handles, loc="upper left", frameon=False, fontsize=8.5,
               labelcolor=COLOR_TEXT_SECONDARY, bbox_to_anchor=(0.0, -0.18), ncol=2)

    fig.tight_layout()
    fig.savefig(HERE / f"{out_stem}.png", facecolor=fig.get_facecolor(), bbox_inches="tight")
    fig.savefig(HERE / f"{out_stem}.pdf", facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    print(f"Wrote {out_stem}.png / {out_stem}.pdf")


def write_loss_caption(loss, jitter, phases, window_start, window_end, cap_phase_idx, out_stem):
    t_a, t_b, cap = phases[cap_phase_idx]
    before_a, before_b, _ = phases[cap_phase_idx - 1] if cap_phase_idx > 0 else (window_start, t_a, None)
    after_a, after_b, _ = phases[cap_phase_idx + 1] if cap_phase_idx + 1 < len(phases) else (t_b, window_end, None)

    def mean(series, a, b):
        vals = [v for e, v in series if a <= e <= b]
        return sum(vals) / len(vals) if vals else float("nan")

    loss_before = mean(loss, before_a, before_b)
    loss_during = mean(loss, t_a, t_b)
    loss_after = mean(loss, after_a, after_b)
    jitter_before = mean(jitter, before_a, before_b)
    jitter_during = mean(jitter, t_a, t_b)

    caption = (
        f"Core-side UDP packet loss (%) for the same {cap:.0f} Mbps cap window as Fig. Plot 1, from iperf3's "
        f"receiver-computed `lost_percent` per 1 s interval (only the receiver can detect gaps in the UDP "
        f"sequence numbers, so this is measured server-side, same source as the delivered-throughput line). "
        f"Mean loss is {loss_before:.2f}% before the cap, rising to {loss_during:.2f}% while the {cap:.0f} Mbps "
        f"cap is active (offered load of ~10 Mbps exceeds the cap, so the excess is shed rather than "
        f"buffered/delayed), and returns to {loss_after:.2f}% after the cap is removed. Mean jitter over the "
        f"same window is {jitter_before:.3f} ms before the cap vs. {jitter_during:.3f} ms during enforcement."
    )
    (HERE / f"{out_stem}_caption.txt").write_text(caption + "\n")
    print(f"Wrote {out_stem}_caption.txt")


def main():
    ref_epoch = None
    for line in (PLOT1_DIR / "data" / "test_timing.txt").read_text().splitlines():
        if line.startswith("test_start_epoch="):
            ref_epoch = int(line.split("=", 1)[1])
    assert ref_epoch is not None

    data = PLOT1_DIR / "data"
    server_docs = [d for d in p1.load_json_documents(data / "iperf_server.log") if "timestamp" in d.get("start", {})]
    server_doc = min(server_docs, key=lambda d: abs(d["start"]["timestamp"]["timesecs"] - ref_epoch))
    delivered = p1.intervals_to_series(server_doc)
    loss = server_metric_series(server_doc, "lost_percent")
    jitter = server_metric_series(server_doc, "jitter_ms")
    events = p1.parse_xapp_events(data / "xapp_events.log", ref_epoch)

    t0 = delivered[0][0]
    t_end_cutoff = events[-1][0] + 45
    delivered = [(e, m) for e, m in delivered if e <= t_end_cutoff]
    loss = [(e, v) for e, v in loss if e <= t_end_cutoff]
    jitter = [(e, v) for e, v in jitter if e <= t_end_cutoff]
    t_end = min(t_end_cutoff, delivered[-1][0])
    phases = p1.build_phases(events, t0, t_end)

    cap_stems = {2.0: "plot3_2mbps_loss", 4.0: "plot3_4mbps_loss", 6.0: "plot3_6mbps_loss"}
    for i, (t_a, t_b, cap) in enumerate(phases):
        if cap is None:
            continue
        window_start = phases[i - 1][0] if i > 0 else t_a
        window_end = phases[i + 1][1] if i + 1 < len(phases) else t_b
        out_stem = cap_stems.get(cap, f"plot3_{cap:.0f}mbps_loss")
        render_loss_figure(loss, phases, window_start, window_end, out_stem)
        write_loss_caption(loss, jitter, phases, window_start, window_end, i, out_stem)


if __name__ == "__main__":
    main()
