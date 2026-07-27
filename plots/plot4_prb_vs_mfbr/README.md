# Plot 4 — PRB quota vs. DRB QoS MFBR

## What it shows

Delivered UL throughput under two different RIC control actions applied to the same DRB, overlaid on a
common "time since cap applied" axis:

- **DRB QoS MFBR** (blue) — our xApp, O-RAN E2SM-RC Style 1 / Action 1, configured for a 2 Mbps rate target.
- **PRB quota** (green) — the existing E2SM-RC Style 2 / Action 6 ("Slice Level PRB Quota"), capping this UE
  to 10% of the cell's PRBs (`min_prb_ratio=0`, `max_prb_ratio=10`).

## What it demonstrates

The two mechanisms operate at different abstraction levels, not just different numeric values:

- **MFBR specifies the outcome directly.** 2 Mbps is exactly the number that was requested (a QoS rate
  contract, TS 23.501 GBR semantics), and delivered throughput converges tightly to it (1.77 Mbps mean,
  std 0.01 Mbps).
- **PRB quota specifies a resource share, not an outcome.** 10% of this cell's 106 PRBs (~11 PRBs) happens to
  settle at 2.23 Mbps (std 0.03 Mbps) under the channel conditions and MCS in effect during this run — but
  that number was never requested; it's a side effect of "10% of PRBs at whatever MCS the scheduler picks."
  There is no way to ask this control action for "2 Mbps" directly, and the *same* 10% ratio would produce a
  different delivered rate under different channel/MCS conditions, without any change to its own configuration.
- **A visible mechanistic difference**: MFBR shows a brief near-zero transient in the first ~2s (its
  token-bucket rate limiter draining the pre-existing backlog — see Plot 1's README), while the PRB quota, a
  hard resource ceiling enforced from the next scheduling opportunity with no equivalent token bucket,
  transitions directly to its steady level.

The two steady-state rates land close together here largely by coincidence of the ratio chosen for this run —
the point of the figure is the *gap between "specified" and "measured after the fact,"* not the small numeric
difference between them.

## Data provenance

- `data/iperf_server.log`'s PRB-quota-run data is **not captured here** — this run reused the already-running
  iperf3 server/client pointed at `../plot1_enforcement_timeseries/data/`, so `generate_plot.py` reads directly
  from that folder (via `sys.path` import of Plot 1's script) rather than duplicating the file.
- `data/xapp_events.log`, `data/test_timing.txt` — this run's own PRB-quota control-action log and epoch
  anchor, from `oran-sc-ric/xApps/python/prb_quota_test_xapp.py --min_prb_ratio 0 --max_prb_ratio 10
  --observe_seconds 45`.
- The MFBR side reuses `../plot1_enforcement_timeseries/data/`'s existing 2 Mbps phase (its own `xapp_events.log`
  and `test_timing.txt`) — no new test needed for that half of the comparison.

## Caveat

iperf3's `--logfile` only writes a complete, parseable JSON report when the session ends (naturally at its
configured duration, or on a graceful interrupt) — not incrementally. The live iperf3 client/server had to be
interrupted (Ctrl-C / `SIGINT`) after this test for its data to become available; if repeating this experiment,
stop iperf3 right after the observe window closes rather than leaving it running for its full configured
duration.

## Reproducing

```bash
python3 generate_plot.py
```

Regenerates `plot4_prb_vs_mfbr.{png,pdf}` and `plot4_prb_vs_mfbr_caption.txt` from the raw data files
(this folder's `data/` plus Plot 1's `data/`).
