#!/usr/bin/env python3
"""DL MFBR enforcement time series - the DL counterpart to Plot 1, and also the live validation
data for the "second reconfiguration" crash fix (see agentic_ran/UL_MFBR_ENFORCEMENT_STATUS.md §8).
This is the post-cleanup confirmation run, captured after redeploying the diagnostic-free binaries
built from the cleaned source (checksums verified to match the build directory before this test).

Unlike Plot 1's UL setup, this test used iperf3 in *reverse* mode (`-R`): the core-side server is
the actual data SENDER and the UE-side client is the actual RECEIVER, so the role mapping flips
relative to Plot 1:
  - offered load  = core-side server's report (`iperf_server.log`, "sender": true entries)
  - delivered     = UE-side client's report (`iperf_ue.json`, "sender": false entries)

The APPLY/REMOVE timestamps for this run were only reliably captured in the gNB log (the DL test
xApp's own stdout never flushed before the process had to be force-killed - it doesn't exit its
own process after the one-shot hold, unlike ul_mfbr_test_xapp.py), so they're taken directly from
srsgnb.log's "Executing E2SM-RC DRB QoS Configuration for DRB1" lines for this run, hardcoded below
with their provenance noted.

Output:
  - plot5_dl_enforcement.{png,pdf}
  - plot5_dl_enforcement_caption.txt
"""
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
import generate_plot as p1  # reuse Plot 1's JSON-parsing helpers - single source of truth

COLOR_DELIVERED = p1.COLOR_DELIVERED
COLOR_OFFERED = p1.COLOR_OFFERED
COLOR_CAP_LINE = p1.COLOR_CAP_LINE
COLOR_PHASE_SHADE = p1.COLOR_PHASE_SHADE
COLOR_GRID = p1.COLOR_GRID
COLOR_AXIS = p1.COLOR_AXIS
COLOR_MUTED = p1.COLOR_MUTED
COLOR_TEXT_SECONDARY = p1.COLOR_TEXT_SECONDARY

DL_CAP_MBPS = 2.0
TEST_TIMING_FILE = "test_timing_confirm.txt"
# Truncated to 250s: the raw capture runs to ~290s, but the last few seconds are a partial interval
# from the client being interrupted when iperf3 was stopped ("error": "interrupt - the client has
# terminated" in iperf_ue.json) - not part of the enforcement story, so cut before it.
PLOT_WINDOW_S = 250

# From srsgnb.log, this run (2026-07-28, the post-cleanup confirmation run with the properly
# redeployed, diagnostic-free binaries): the DL test xApp's own stdout never flushed (see module
# docstring), so these are read directly from the gNB's own "Executing E2SM-RC DRB QoS
# Configuration for DRB1" log lines for this run.
APPLY_UTC = "2026-07-28T13:19:15.212144"
REMOVE_UTC = "2026-07-28T13:20:51.123560"


def to_epoch(iso_utc):
    return datetime.strptime(iso_utc, "%Y-%m-%dT%H:%M:%S.%f").replace(tzinfo=timezone.utc).timestamp()


def mean(series, a, b):
    vals = [m for e, m in series if a <= e <= b]
    return sum(vals) / len(vals) if vals else float("nan")


def main():
    data = HERE / "data"
    server_docs = [d for d in p1.load_json_documents(data / "iperf_server.log") if "timestamp" in d.get("start", {})]
    client_docs = [d for d in p1.load_json_documents(data / "iperf_ue.json") if "timestamp" in d.get("start", {})]

    ref_epoch = None
    for line in (data / TEST_TIMING_FILE).read_text().splitlines():
        if line.startswith("test_start_epoch="):
            ref_epoch = int(line.split("=", 1)[1])
    assert ref_epoch is not None

    server_doc = min(server_docs, key=lambda d: abs(d["start"]["timestamp"]["timesecs"] - ref_epoch))
    client_doc = min(client_docs, key=lambda d: abs(d["start"]["timestamp"]["timesecs"] - ref_epoch))

    offered = p1.intervals_to_series(server_doc)   # server is the sender in reverse mode
    delivered = p1.intervals_to_series(client_doc)  # UE is the receiver in reverse mode

    apply_epoch = to_epoch(APPLY_UTC)
    remove_epoch = to_epoch(REMOVE_UTC)

    t0 = min(offered[0][0], delivered[0][0])
    t_end = min(max(offered[-1][0], delivered[-1][0]), t0 + PLOT_WINDOW_S)

    phases = [(t0, apply_epoch, None), (apply_epoch, remove_epoch, DL_CAP_MBPS), (remove_epoch, t_end, None)]

    # --- plot ---
    plt.rcParams["font.family"] = "sans-serif"
    plt.rcParams["font.sans-serif"] = ["DejaVu Sans", "Arial", "Helvetica"]

    fig, ax = plt.subplots(figsize=(9, 4.2), dpi=200)
    fig.patch.set_facecolor("#fcfcfb")
    ax.set_facecolor("#fcfcfb")

    for t_a, t_b, cap in phases:
        a, b = t_a - t0, t_b - t0
        if cap is not None:
            ax.axvspan(a, b, color=COLOR_PHASE_SHADE, alpha=0.10, lw=0)
            # Full-width reference line (not just the cap-active span) so it's easy to compare
            # against the offered-load line throughout, not only inside the shaded region.
            ax.hlines(cap, 0, t_end - t0, colors=COLOR_CAP_LINE, linestyles=(0, (4, 3)), linewidth=1.6, zorder=3)
            ax.text((a + b) / 2, cap + 0.45, f"cap = {cap:.0f} Mbps", ha="center", va="bottom",
                     fontsize=8.5, color=COLOR_TEXT_SECONDARY)

    t_del = [e - t0 for e, _ in delivered if e <= t_end]
    y_del = [m for e, m in delivered if e <= t_end]
    t_off = [e - t0 for e, _ in offered if e <= t_end]
    y_off = [m for e, m in offered if e <= t_end]

    ax.plot(t_off, y_off, color=COLOR_OFFERED, linewidth=1.4, linestyle=(0, (5, 2)),
             label="Offered load (core-side, sent)", zorder=2, alpha=0.9)
    ax.plot(t_del, y_del, color=COLOR_DELIVERED, linewidth=2.0,
             label="Delivered throughput (UE-side, received)", zorder=4)

    ax.set_xlim(0, t_end - t0)
    ax.set_ylim(0, max(y_off + y_del) * 1.15)
    ax.set_xlabel("Time since test start (s)", color=COLOR_TEXT_SECONDARY, fontsize=10)
    ax.set_ylabel("DL throughput (Mbps)", color=COLOR_TEXT_SECONDARY, fontsize=10)

    ax.grid(True, axis="y", color=COLOR_GRID, linewidth=0.8, zorder=0)
    ax.grid(False, axis="x")
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color(COLOR_AXIS)
    ax.tick_params(colors=COLOR_MUTED, labelsize=9)

    legend_handles = [
        plt.Line2D([0], [0], color=COLOR_DELIVERED, linewidth=2.0, label="Delivered throughput (UE-side, received)"),
        plt.Line2D([0], [0], color=COLOR_OFFERED, linewidth=1.4, linestyle=(0, (5, 2)), label="Offered load (core-side, sent)"),
        plt.Line2D([0], [0], color=COLOR_CAP_LINE, linewidth=1.6, linestyle=(0, (4, 3)), label="Configured MFBR cap"),
        Patch(facecolor=COLOR_PHASE_SHADE, alpha=0.10, label="Cap active"),
    ]
    ax.legend(handles=legend_handles, loc="upper left", frameon=False, fontsize=8.5,
               labelcolor=COLOR_TEXT_SECONDARY, bbox_to_anchor=(0.0, -0.18), ncol=2)

    fig.tight_layout()
    fig.savefig(HERE / "plot5_dl_enforcement.png", facecolor=fig.get_facecolor(), bbox_inches="tight")
    fig.savefig(HERE / "plot5_dl_enforcement.pdf", facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    print("Wrote plot5_dl_enforcement.png / .pdf")

    # --- caption ---
    offered_before_mean = mean(offered, t0, apply_epoch)
    delivered_capped_mean = mean(delivered, apply_epoch, remove_epoch)
    delivered_capped_steady_mean = mean(delivered, apply_epoch + 5, remove_epoch)
    delivered_after_mean = mean(delivered, remove_epoch, min(remove_epoch + 10, t_end))
    duration = remove_epoch - apply_epoch

    recovery_s = None
    for e, m in sorted(delivered, key=lambda p: p[0]):
        if e >= remove_epoch and m >= 0.9 * offered_before_mean:
            recovery_s = e - remove_epoch
            break

    caption = (
        f"Downlink throughput for a single UE/DRB before, during, and after enforcement of a {DL_CAP_MBPS:.0f} Mbps "
        f"per-DRB Maximum Flow Bit Rate (MFBR) cap, applied via O-RAN E2SM-RC Style 1 / Action 1 (DRB QoS "
        f"Configuration RIC Control Request), with iperf3 in reverse mode (offered load ~{offered_before_mean:.1f} "
        f"Mbps from the core-side server, ~20x oversubscribed against the cap). The cap is applied at "
        f"t = {apply_epoch - t0:.1f} s and removed at t = {remove_epoch - t0:.1f} s ({duration:.0f} s duration, "
        f"shaded region) - the second reconfiguration on the same DRB/UE session (apply, then remove) that "
        f"previously crashed the connection reliably (see UL_MFBR_ENFORCEMENT_STATUS.md §2.3/§8) completes here "
        f"with zero RLF and zero buffer-pool exhaustion, following two srsRAN_4G UE-side timer fixes (§8.1, §8.2). "
        f"Delivered throughput converges to a mean of {delivered_capped_steady_mean:.2f} Mbps during the capped "
        f"phase (excluding the first 5 s transient; {delivered_capped_mean:.2f} Mbps including it), against the "
        f"{DL_CAP_MBPS:.0f} Mbps target."
    )
    if recovery_s is not None:
        caption += (
            f" After cap removal, delivered throughput recovers to within 90% of the pre-cap offered rate within "
            f"{recovery_s:.1f} s, reaching a mean of {delivered_after_mean:.2f} Mbps over the following 10 s."
        )
    (HERE / "plot5_dl_enforcement_caption.txt").write_text(caption + "\n")
    print("Wrote plot5_dl_enforcement_caption.txt")
    print(f"apply-t0={apply_epoch-t0:.1f}s remove-t0={remove_epoch-t0:.1f}s t_end-t0={t_end-t0:.1f}s")


if __name__ == "__main__":
    main()
