# Plot 2 — Convergence time vs. cap magnitude

## What it shows

One bar per cap level (2 / 4 / 6 Mbps) in the same UL MFBR cap-staircase run used for Plot 1: how long after
the RIC Control Request (cap applied) does delivered UL throughput settle into its steady enforced level?

## What it demonstrates

Convergence time does not grow with the cap's magnitude (3.5 s at 2 Mbps, 1.5 s at both 4 and 6 Mbps) —
the enforcement mechanism's responsiveness is independent of the target rate, not just its correctness at a
single value (which Plot 1 already shows). The slightly longer time at 2 Mbps is consistent with Plot 1's
noted token-bucket drain transient being more visible at tighter caps.

## Method

Convergence time is the first moment after cap application at which delivered throughput enters, and then
stays within ±15% of that phase's own steady-state mean (mean computed over the phase excluding its first 5 s
transient), for at least 3 consecutive 1 s samples. Both the tolerance and sustain window are conservative,
fixed constants (`generate_plot.py`'s `convergence_time()`), not tuned per cap.

## Data provenance

No new live test — reuses `../plot1_enforcement_timeseries/data/` (`iperf_server.log`, `xapp_events.log`,
`test_timing.txt`) via that folder's `generate_plot.py`, imported as a module for the parsing/phase-building
logic (single source of truth, see that script's docstring for details on the raw inputs).

## Reproducing

```bash
python3 generate_plot.py
```

Regenerates `plot2_convergence_time.{png,pdf}` and `plot2_convergence_time_caption.txt`.
