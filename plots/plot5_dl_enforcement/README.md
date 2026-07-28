# Plot 5 — DL MFBR enforcement + second-reconfiguration crash fix validation

## What it shows

Downlink throughput for a single UE/DRB, before, during, and after a 2 Mbps per-DRB MFBR cap applied via the
DRB QoS xApp (E2SM-RC Style 1 / Action 1), with a genuine **second reconfiguration** — the cap is applied, held,
then explicitly **removed** (an uncapped control action) on the same DRB/UE session, no restart in between.

- **Offered load** (green, dashed): core-side iperf3, run in reverse mode (`-R`) so the *server* is the actual
  data sender — held constant at ~40 Mbps throughout (~20x oversubscribed against the cap).
- **Delivered throughput** (blue, solid): UE-side iperf3, the actual receiver in reverse mode.

## Why this plot matters

This exact sequence — apply, then a second reconfiguration on the same DRB — is the scenario that reliably
crashed the connection (RLF, forced UE Context Release) from when it was first found (2026-07-10) until
2026-07-28. Two independent bugs, both in `srsRAN_4G` UE-side code, were root-caused and fixed:

1. **PDCP `t-Reordering` timer never armed for `t_reordering=ms0`** — a config-value edge case where a
   legitimate, standard TS 38.331 value (`ms0`, 0ms — distinct from `infinity`) was mistakenly treated as
   "timer disabled."
2. **A timer-wheel off-by-one for duration-1 timers that re-arm themselves from their own expiry callback** —
   found while fixing #1, in the shared timer utility used by every timer in `srsRAN_4G`, not just PDCP.

Full root-cause writeup: `agentic_ran/UL_MFBR_ENFORCEMENT_STATUS.md` §8 (§2.3 has the original, then-unresolved
crash report).

## What it demonstrates

- **Enforcement**: delivered throughput drops from ~40 Mbps to a steady ~2.19 Mbps within a few seconds of the
  cap being applied, tracking the 2 Mbps target.
- **Clean removal — the previously-crashing step**: after the cap is removed (the second reconfiguration),
  delivered throughput recovers to ~40 Mbps within well under a second — no RLF, no buffer-pool exhaustion, no
  dropped connection.
- Zero new allocation failures and zero RLF across the entire capture (confirmed via `srsue`'s buffer-pool and
  gNB's RLF counters during the live test), matching the caption's numbers.

## Data provenance

This is the **post-cleanup confirmation run**: captured after redeploying the diagnostic-free `srsue`/`gnb`
binaries built from the cleaned source (checksums verified identical to the build directory before this test),
re-proving the fix with the exact binaries that will actually ship.

- `data/iperf_server.log`, `data/iperf_ue.json` — iperf3 JSON reports (`--logfile`), core-side (sender, reverse
  mode) and UE-side (receiver) respectively. Continuous capture spanning baseline through the full apply→remove
  cycle; truncated to 250s in the plot (`PLOT_WINDOW_S` in `generate_plot.py`) to cut a partial final interval
  from the client being interrupted when iperf3 was stopped at the end of the test (harmless, but not part of
  the enforcement story).
- `data/test_timing_confirm.txt` — epoch anchor for this run.
- The APPLY/REMOVE timestamps are read from `srsgnb.log`'s "Executing E2SM-RC DRB QoS Configuration for DRB1"
  lines for this run (hardcoded with provenance in `generate_plot.py`) — the DL test xApp
  (`dl_mfbr_test_xapp.py`) doesn't exit its own process after its one-shot hold (unlike `ul_mfbr_test_xapp.py`),
  so its stdout never flushed before the process had to be force-killed between phases; `xapp_events_apply_confirm.log`/
  `xapp_events_remove_confirm.log` are kept for reference but only contain the RMR init line.

## Reproducing

```bash
python3 generate_plot.py
```

Regenerates `plot5_dl_enforcement.{png,pdf}` and `plot5_dl_enforcement_caption.txt` from the raw data files.
