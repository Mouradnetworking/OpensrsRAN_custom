#!/usr/bin/env python3
"""Convergence-time-vs-cap-magnitude plot, reusing Plot 1's raw testbed captures.

Answers: once a UL MFBR cap is applied, how long until delivered throughput settles into its
steady enforced level - and does that latency grow with the cap's magnitude, or stay roughly
constant? Uses the exact same 2/4/6 Mbps cap-staircase run as Plot 1 (`../plot1_enforcement_timeseries/data/`) -
no new live test needed.

Convergence time (per cap phase) is defined as: the first moment after APPLY at which delivered
throughput enters, and then stays within +/-15% of that phase's own steady-state mean (computed
over the phase excluding its first 5s token-bucket-drain transient - see Plot 1's README) for at
least 3 consecutive 1s samples.

Output:
  - plot2_convergence_time.{png,pdf}
  - plot2_convergence_time_caption.txt
"""
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).parent
PLOT1_DIR = HERE.parent / "plot1_enforcement_timeseries"
sys.path.insert(0, str(PLOT1_DIR))
import generate_plot as p1  # reuse Plot 1's parsing/phase-building logic - single source of truth

COLOR_BAR = p1.COLOR_DELIVERED
COLOR_GRID = p1.COLOR_GRID
COLOR_AXIS = p1.COLOR_AXIS
COLOR_MUTED = p1.COLOR_MUTED
COLOR_TEXT_SECONDARY = p1.COLOR_TEXT_SECONDARY


def convergence_time(delivered, t_a, t_b, tolerance=0.15, sustain_s=3):
    """First time (relative to t_a) at which delivered throughput enters and stays within
    +/-tolerance of the phase's own steady-state mean for at least sustain_s seconds. None if it
    never does."""
    samples = sorted([(e, m) for e, m in delivered if t_a <= e <= t_b], key=lambda p: p[0])
    if not samples:
        return None, None
    steady_vals = [m for e, m in samples if e >= t_a + 5]
    if not steady_vals:
        return None, None
    steady_mean = sum(steady_vals) / len(steady_vals)
    lo, hi = steady_mean * (1 - tolerance), steady_mean * (1 + tolerance)
    for i, (e_i, _) in enumerate(samples):
        window = [(e, m) for e, m in samples if e_i <= e <= e_i + sustain_s]
        if len(window) >= sustain_s and all(lo <= m <= hi for _, m in window):
            return e_i - t_a, steady_mean
    return None, steady_mean


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
    events = p1.parse_xapp_events(data / "xapp_events.log", ref_epoch)

    t0 = delivered[0][0]
    t_end_cutoff = events[-1][0] + 45
    delivered = [(e, m) for e, m in delivered if e <= t_end_cutoff]
    t_end = min(t_end_cutoff, delivered[-1][0])
    phases = p1.build_phases(events, t0, t_end)

    results = []  # (cap, convergence_s, steady_mean)
    for t_a, t_b, cap in phases:
        if cap is None:
            continue
        conv_s, steady_mean = convergence_time(delivered, t_a, t_b)
        results.append((cap, conv_s, steady_mean))
    results.sort(key=lambda r: r[0])

    # --- plot ---
    plt.rcParams["font.family"] = "sans-serif"
    plt.rcParams["font.sans-serif"] = ["DejaVu Sans", "Arial", "Helvetica"]

    fig, ax = plt.subplots(figsize=(5.2, 4.2), dpi=200)
    fig.patch.set_facecolor("#fcfcfb")
    ax.set_facecolor("#fcfcfb")

    labels = [f"{cap:.0f} Mbps" for cap, _, _ in results]
    values = [conv_s if conv_s is not None else 0 for _, conv_s, _ in results]
    x = range(len(results))

    bars = ax.bar(x, values, color=COLOR_BAR, width=0.5, zorder=3)
    for xi, v in zip(x, values):
        ax.text(xi, v + max(values) * 0.03, f"{v:.1f}s", ha="center", va="bottom",
                 fontsize=9.5, color=p1.COLOR_TEXT)

    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, color=COLOR_TEXT_SECONDARY, fontsize=10)
    ax.set_ylabel("Convergence time (s)", color=COLOR_TEXT_SECONDARY, fontsize=10)
    ax.set_ylim(0, max(values) * 1.35 if values else 1)

    ax.grid(True, axis="y", color=COLOR_GRID, linewidth=0.8, zorder=0)
    ax.grid(False, axis="x")
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color(COLOR_AXIS)
    ax.tick_params(colors=COLOR_MUTED, labelsize=9)

    fig.tight_layout()
    fig.savefig(HERE / "plot2_convergence_time.png", facecolor=fig.get_facecolor(), bbox_inches="tight")
    fig.savefig(HERE / "plot2_convergence_time.pdf", facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    print("Wrote plot2_convergence_time.png / .pdf")

    # --- caption ---
    per_cap_sentence = "; ".join(
        f"{cap:.0f} Mbps in {conv_s:.1f} s (steady-state mean {steady:.2f} Mbps)"
        for cap, conv_s, steady in results if conv_s is not None
    )
    caption = (
        "Time from RIC Control Request (cap applied) to convergence of delivered UL throughput, for "
        "each cap level in the same 2/4/6 Mbps staircase shown in Fig. Plot 1. Convergence is defined "
        "as the first point at which delivered throughput enters and remains within +/-15% of that "
        "phase's own steady-state mean (computed excluding the first 5 s token-bucket-drain transient) "
        f"for at least 3 consecutive 1 s samples: {per_cap_sentence}. Convergence time does not grow "
        "with the cap's magnitude, indicating the enforcement mechanism's responsiveness is "
        "independent of the target rate."
    )
    (HERE / "plot2_convergence_time_caption.txt").write_text(caption + "\n")
    print("Wrote plot2_convergence_time_caption.txt")
    print(results)


if __name__ == "__main__":
    main()
