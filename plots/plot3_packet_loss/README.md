# Plot 3 — Packet loss during enforcement

## What it shows

Three figures — `plot3_2mbps_loss`, `plot3_4mbps_loss`, `plot3_6mbps_loss` — one per cap level, each showing
core-side UDP packet loss (%) over the same before/during/after window as Plot 1's matching figure, for the
same UL MFBR cap-staircase run.

## What it demonstrates

Excess offered load is **actively shed**, not silently buffered or delayed, once a cap is enforced: packet loss
jumps from a low single-digit baseline (~5–7%, ordinary UDP/RF loss) to a level consistent with the cap's
shortfall against the fixed ~10 Mbps offered load — roughly `1 - cap/offered`, e.g. ~80% loss at a 2 Mbps cap,
~65% at 4 Mbps, ~46% at 6 Mbps — and drops straight back to baseline within seconds of the cap being removed.
This is complementary, independent evidence to Plot 1's throughput convergence: Plot 1 shows *what got through*,
this shows *what didn't*, and that the RAN doesn't try to queue the excess (which would show as growing latency
or a delayed throughput bump instead of steady-state loss). Mean UDP jitter is also reported per phase in each
caption and stays low (sub-2 ms) throughout, including while capped.

## Data provenance

No new live test — reuses `../plot1_enforcement_timeseries/data/iperf_server.log`'s existing JSON, which
already includes iperf3's receiver-computed `lost_percent` and `jitter_ms` per 1 s interval alongside the
`bits_per_second` field Plot 1 uses. Only the receiver (core-side server) can detect gaps in the UDP sequence
numbers, so both metrics are inherently server-side — same measurement point as Plot 1's delivered-throughput
line, just a different field from the same capture.

## Reproducing

```bash
python3 generate_plot.py
```

Regenerates `plot3_{2,4,6}mbps_loss.{png,pdf}` and `plot3_{2,4,6}mbps_loss_caption.txt`.
