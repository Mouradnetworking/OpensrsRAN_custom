# Agentic RAN telemetry/control design for srsRAN

Status: telemetry (Layers 1-2) and one control action (PRB quota) are validated live against a real gNB/UE/RIC
testbed (see §14). A second control action (UE bitrate cap) was implemented and validated live in the same way
(§14.4). All of it is opt-in or additive: no existing production code path changes behavior unless a cap/quota is
explicitly configured, or an experimental env var is set.

## 1. High-level objective

The end goal is an **Agentic RAN** deployment: agents / xApps that can

1. **observe** live RAN state (radio quality, buffers, scheduling decisions, HARQ, throughput, delay),
2. **reason** over that state (rule-based, RL, or LLM-based "grounding agent"), and
3. **influence or control** RAN behavior (PRB allocation shaping, power/link-adaptation targets, slice weighting, ...),

with srsRAN as the RAN implementation, and (eventually) a real **O-RAN near-RT RIC** hosting the xApps and
talking E2AP to the DU/CU, rather than an ad-hoc scripted control loop.

This document describes what telemetry srsRAN can expose today, what was added, why, and how it maps onto that
end goal.

## 2. What we learned from `oai-release-agentran`

The reference repository (`~/oai-release-agentran`, a modified OpenAirInterface 5G stack) is a full OAI
checkout with three bespoke Python scripts added at its root:

- **`dl_metrics_forwarder.py`** / **`ul_metrics_forwarder.py`** — each opens a named pipe
  (`/tmp/dl_metrics.pipe`, `/tmp/ul_metrics.pipe`), expects one JSON object per line written by a **Lua scheduler
  hook** (`pf_dl.lua` / `pf_ul.lua`, referenced from `UL_control_termination.py` but not present in this checkout),
  and forwards it as InfluxDB line-protocol to a local InfluxDB instance (`INFLUXDB_URL`, bucket `mwc-live`).
  Per-UE fields observed in the JSON→line-protocol mapping: RNTI, UE id, UE type, 5QI (as tags); pending bytes,
  throughput, BLER, CQI, DL RSRP, PUSCH SINR (UL only), previous/required/allocated MCS, required/allocated RBs
  and RB start, BWP start/size, HOL delay (µs), and — critically — a **PRB bitmask string** (`.`/`X` per PRB) for
  the whole cell.
- **`UL_control_termination.py`** — a FastAPI service that turns REST calls into **Telnet commands** sent to a
  live OAI process (`telnet_utils.TelnetClient`): per-UE UL SNR power-control target, scheduler throughput caps
  (FWA/MTC Mbps), and cell-wide or per-UE **PRB masking** (block/unblock/set/clear ranges or full bitmasks). It
  also offers tmux-based lifecycle control (start/stop/restart the gNB process, tail its logs).

So the OAI reference stack is: **Lua-instrumented scheduler → named pipe → Python forwarder → InfluxDB** for
telemetry, and **REST → Telnet → live Lua globals** for control. There is no near-RT RIC, no E2 interface, no
xApp SDK involved — it is a self-contained experimentation harness built because OAI's mainline does not (yet)
ship a production E2 agent equivalent to srsRAN's.

## 3. Why we are not copying `dl_metrics_forwarder.py` as-is

The OAI design is a reasonable choice *for OAI*, but three of its properties don't transfer to srsRAN and
shouldn't be reproduced mechanically:

1. **The Lua hook doesn't exist in srsRAN and shouldn't be added.** srsRAN's scheduler has no embedded scripting
   layer; building one solely to mimic OAI's instrumentation point would be a large, invasive addition for no
   benefit — srsRAN's scheduler is C++ and can be instrumented directly and more cheaply at the same conceptual
   point (the per-slot result callback).
2. **InfluxDB + a bespoke Python forwarder duplicates infrastructure srsRAN already has.** srsRAN already ships a
   production per-UE/per-cell metrics pipeline (`scheduler_metrics_notifier`, see §4) with two real consumers: a
   JSON exporter and a full O-RAN-standard E2SM-KPM service model. Adding a third, parallel, InfluxDB-specific
   telemetry stack would fragment rather than unify RAN observability.
3. **The Telnet/REST control path exists because OAI has no near-RT RIC.** srsRAN already implements E2AP plus
   E2SM-KPM, E2SM-RC and E2SM-CCC service models (`lib/e2/e2sm/*`). Since we plan to run a real near-RT RIC and
   real xApps, RIC-driven control belongs on **E2SM-RC CONTROL** messages, not a REST-to-Telnet bridge into a
   scripting VM. Building a Telnet-style side channel here would be a step *backwards* architecturally.

The reference repo *is* useful, though, as a checklist of "what a scheduler/KPM-consuming agent actually wants to
see" (see the field list in §2) — that checklist is what drove the metric-by-metric gap analysis in §7–§9.

## 4. Final design chosen for srsRAN

srsRAN already has a periodic, per-UE/per-cell metrics pipeline that is the natural backbone for telemetry:

```
scheduler internals  →  cell_metrics_handler (lib/scheduler/logging/scheduler_metrics_handler.*)
                            │  aggregates over a configurable report period (scheduler_ue_metrics / scheduler_cell_metrics)
                            ▼
                  scheduler_metrics_notifier  (report_metrics callback)
                     ├──► JSON exporter   (apps/helpers/metrics/json_generators/du_high/scheduler.cpp)
                     └──► E2SM-KPM agent  (lib/e2/e2sm/e2sm_kpm/e2sm_kpm_du_meas_provider_impl.cpp)
                                              → standard O-RAN KPM measurement names (CQI, RSRP, RRU.Prb*, DRB.*)
                                              → subscribable today by a real near-RT RIC/xApp over E2AP
```

This is **design option 3 combined with option 4**: reuse the existing metrics infrastructure, and treat the
already-implemented E2SM-KPM/E2SM-RC agent as the real near-RT RIC integration path, rather than inventing a new
one. Where this pipeline was silently dropping data srsRAN already computes (see §9, DL HOL delay), we extended
it, because that data was real and already flowing through the scheduler — it just weren't being surfaced.

On top of that backbone we keep a second, complementary layer that was already prototyped in a previous session
of this work (`scheduler_ue_state_logger`, reviewed and kept — see below): a **per-slot, per-grant** JSON stream,
opt-in via `SRSRAN_UE_STATE_PIPE`, that answers a different question than the periodic KPM/JSON report does.

**Why keep both layers instead of just one:**

| | Layer 1 — `cell_metrics_handler` (existing, extended) | Layer 2 — `scheduler_ue_state_logger` (new, experimental) |
|---|---|---|
| Granularity | One aggregated snapshot per UE per report period (typically ≥100 ms–1 s) | One JSON entry per DL grant, every slot (sub-ms to few-ms) |
| What it answers | "How has this UE been doing over the last interval?" | "What did the scheduler just decide, and what did it know when it decided it?" |
| HARQ/grant detail | None (aggregated ACK/NACK counters only) | HARQ process id, retx count, exact RB start/length, RAN slice id, buffer occupancy *at decision time* |
| Standardization | Yes — same struct feeds O-RAN E2SM-KPM | No — native, not a reproduction of any external wire format |
| Intended consumer | A real near-RT RIC / xApp via E2, or any JSON log consumer | A local grounding agent doing decision-level analysis / RL training during development, before E2SM-RC-based control exists for this use case |
| Production safety | Already production-grade (used for real KPM reporting) | Fully opt-in (env var gate), best-effort, drops samples rather than blocking the scheduler thread |

Layer 2 is explicitly a **bridge**, not a destination: the fields it adds that aren't in Layer 1 (HARQ id,
per-grant RB allocation, RAN slice id) are exactly the kind of thing that would eventually belong in a **custom
E2 Service Model** (or extensions to E2SM-KPM/RC) once a near-RT RIC needs sub-report-interval, per-grant
visibility. Until that SM exists, Layer 2 gives a grounding agent developer that data today without waiting on
new ASN.1 definitions.

**Design options considered and rejected/deferred:**

1. *A compatibility pipe that mimics the OAI JSON format* — rejected. Locks us into OAI's field names/units and a
   PRB-bitmask-string representation that doesn't fit srsRAN's `vrb_alloc`/RBG model; buys nothing since we
   don't reuse their InfluxDB/forwarder stack anyway.
2. *A richer native srsRAN telemetry exporter* — **adopted for Layer 2**, scoped to grant-level state not already
   in Layer 1.
3. *Using existing srsRAN metrics infrastructure* — **adopted for Layer 1**, extended where real data was being
   dropped.
4. *E2/KPM/RC paths for near-RT RIC integration* — **already implemented in srsRAN**; Layer 1 already feeds it.
   Wiring an actual near-RT RIC (e.g. enabling `e2sm_rc_enabled` and issuing CONTROL messages) is the intended
   long-term control path; see §10.
5. *Hybrid: collect internally now, keep a clean path to real xApps/RIC* — this is what we built: Layer 1 (already
   RIC-ready) + Layer 2 (dev-time bridge, explicitly temporary/experimental).

## 5. Files changed (telemetry)

See §15 for the control-plane files added later in this effort.

| File | Status |
|---|---|
| `lib/scheduler/logging/scheduler_ue_state_logger.h` | new |
| `lib/scheduler/logging/scheduler_ue_state_logger.cpp` | new |
| `lib/scheduler/logging/CMakeLists.txt` | modified |
| `lib/scheduler/cell_scheduler.h` | modified |
| `lib/scheduler/cell_scheduler.cpp` | modified |
| `lib/scheduler/ue_context/ue_channel_state_manager.h` | modified |
| `lib/scheduler/ue_context/ue_channel_state_manager.cpp` | modified |
| `lib/scheduler/ue_context/ue_cell.cpp` | modified |
| `lib/scheduler/ue_scheduling/ue_scheduler.h` | modified |
| `lib/scheduler/ue_scheduling/ue_scheduler_impl.h` | modified |
| `include/srsran/scheduler/scheduler_metrics.h` | modified |
| `lib/scheduler/logging/scheduler_metrics_handler.h` | modified |
| `lib/scheduler/logging/scheduler_metrics_handler.cpp` | modified |
| `apps/helpers/metrics/json_generators/du_high/scheduler.cpp` | modified |

## 6. Per-file rationale

### Layer 2 — new per-grant telemetry exporter

- **`lib/scheduler/logging/scheduler_ue_state_logger.{h,cpp}` (new)** — Implements the opt-in per-slot exporter.
  Disabled unless the `SRSRAN_UE_STATE_PIPE` environment variable names a FIFO path; when enabled, installs a
  custom srslog sink (`ue_state_pipe_sink`) that opens the FIFO non-blocking and drops samples rather than ever
  blocking the scheduler thread (formatting happens on the caller's thread, the actual `write()` happens on
  srslog's async backend thread). Emits one `{"schema":"srsran_ue_state_v1", ...}` JSON line per slot, with one
  object per DL grant. Supports: raw grant decision (RNTI, UE index, HARQ id, MCS, RB allocation, retransmission
  count, DL buffer occupancy — all "free" from `sched_result`), plus live channel-state/HARQ enrichment looked up
  through the new `find_ue_cell()` accessor (see below).
- **`lib/scheduler/logging/CMakeLists.txt`** — Builds the new source and links `sched_ue_context` (needed for the
  `ue_cell`/channel-state/HARQ types the logger reads).
- **`lib/scheduler/cell_scheduler.{h,cpp}`** — `cell_scheduler` now owns a `scheduler_ue_state_logger` member and
  calls `log_slot_result()` once per slot, right after the existing `metrics.push_result(...)` call, so the new
  exporter sits alongside (not instead of) the existing metrics path. Zero cost when disabled (one null check).
- **`lib/scheduler/ue_scheduling/ue_scheduler.h`** — Adds `virtual const ue_cell* find_ue_cell(du_ue_index_t) const
  = 0;`. **Why a new accessor**: the logger needs read-only access to a UE's live channel-state/HARQ objects, but
  it must not become part of the scheduler's internal call graph (it's a "spectator", not a participant in
  scheduling decisions). A `const`-returning accessor keeps that separation explicit: callers can read state but
  cannot mutate it, so a telemetry bug can't corrupt scheduling.
- **`lib/scheduler/ue_scheduling/ue_scheduler_impl.h`** — Implements `find_ue_cell()` as a direct lookup into the
  existing `ue_cell_db` (`ue_cell_repository::find()`, which already existed and already returns
  `const ue_cell*`) — no new storage, just a new read path exposed on the public interface.
- **`lib/scheduler/ue_context/ue_channel_state_manager.{h,cpp}`** — Two additions:
  - `has_pusch_snr_report()` — distinguishes a UE for which a real PUSCH SINR measurement has arrived from one
    still showing the cell's configured `initial_ul_sinr` seed value. Needed because `get_pusch_snr()` cannot
    itself express "no real data yet" (it's a plain `float`, seeded to a config default) — without this flag the
    logger would report a fabricated SINR that looks exactly like a real one.
  - `update_pusch_rsrp()` / `get_pusch_rsrp()` (returns `std::optional<float>`) — a live "last known PUSCH RSRP"
    value, separate from the periodic averaged `pusch_rsrp_db` already computed in `cell_metrics_handler` (see
    Layer 1). `std::nullopt` until the first report arrives, so it is omitted rather than fabricated.
- **`lib/scheduler/ue_context/ue_cell.cpp`** — `handle_crc_pdu()` already read `crc_pdu.ul_sinr_dB`; it now also
  reads `crc_pdu.ul_rsrp_dBFS` (a field that already existed on the CRC indication, just wasn't wired to
  channel-state) and forwards it to `update_pusch_rsrp()`.

### Layer 1 — closing a real gap in the existing metrics pipeline

- **`include/srsran/scheduler/scheduler_metrics.h`** — Added `std::optional<float> dl_hol_delay_ms` to
  `scheduler_ue_metrics`: the instantaneous DL head-of-line queueing delay (age of the oldest pending DL PDU
  across the UE's logical channels), `nullopt` when the UE has no pending DL data.
- **`lib/scheduler/logging/scheduler_metrics_handler.h` / `.cpp`** — `dl_buffer_state_indication_message` already
  carries a real `hol_toa` (time-of-arrival slot of the oldest queued PDU), computed in the RLC Tx entities
  (`rlc_tx_{tm,um,am}_entity.cpp`) and delivered all the way to the scheduler (it's already used live by the QoS
  scheduler policy, `scheduler_time_qos.cpp`, to prioritize UEs). `cell_metrics_handler::handle_dl_buffer_state_
  indication()`, however, was only storing the byte count (`last_dl_bs`) and silently discarding `hol_toa`. We
  added a parallel `last_dl_hol_toa` array (same lifetime/reset semantics as `last_dl_bs` — persists across
  reports, per the existing "keep the last value" convention) and, in `compute_report()`, compute the age of the
  oldest *currently non-empty* logical channel at report time. This is real data that was already being computed
  and used internally — we only added the read path, not new instrumentation.
- **`apps/helpers/metrics/json_generators/du_high/scheduler.cpp`** — Exposes `dl_hol_delay_ms` in the JSON report,
  omitted when absent (no fabricated zero for "no pending data").

## 7. Metrics currently emitted

### Layer 1 (periodic, per report interval; JSON + E2SM-KPM)

RNTI, UE index, PCI, CQI (mean over interval), DL/UL RI, DL/UL MCS (mean), DL/UL PRB usage (total), DL/UL bit
rate, DL/UL HARQ ACK/NACK counts, PUSCH SINR/RSRP (mean over interval), PUCCH SINR (mean), BSR, SR count, DL
buffer occupancy (`dl_bs`), **DL HOL delay (new)**, invalid-UCI counters, PHR, TA/PUSCH-TA/PUCCH-TA/SRS-TA
statistics, several turnaround-delay statistics (UL CE, CRC, PUSCH/PUCCH HARQ, SR-to-PUSCH), plus cell-wide
counters (PRACH preambles, MSG3 OK/NOK, failed PDCCH/UCI allocations, decision latency histogram, PUCCH RB usage,
per-TDD-slot PRB usage).

### Layer 2 (per slot, per DL grant; opt-in JSON pipe)

`rnti`, `ue_index`, `harq_id`, `new_data`, `nof_retxs`, `mcs`, `dl_buffer_occupancy_bytes`, `rb_alloc_type` +
(`rb_start`,`rb_len` for contiguous grants, or `rb_len` alone for RBG-bitmap grants), `bwp_start`, `bwp_size`,
`wideband_cqi`, `dl_ri` (both only if the latest CSI report is valid), `pusch_sinr_db` + `pusch_avg_sinr_db`
(only once a real report has arrived), `pusch_rsrp_dbfs` (once reported), `harq_max_retxs`, `harq_waiting_ack`,
`ran_slice_id` (if the grant belongs to a configured slice). Envelope: `schema`, `ts_ns`, `sfn`, `slot`, `pci`.

## 8. Metric-by-metric source table

| Metric | Source (file : class/function) | Real / Derived / Unavailable | Unit | DL/UL | Scope |
|---|---|---|---|---|---|
| RNTI | `scheduler_metrics.h` / `sched_result` grants | Real | — | both | per-UE |
| UE index | same | Real | — | both | per-UE |
| PCI | `cell_configuration::pci` | Real | — | both | per-cell |
| CQI | `ue_channel_state_manager::handle_csi_report` → `cell_metrics_handler` (`cqi_stats`) | Real | 0–15 index | DL | per-UE |
| DL RI / UL RI | same CSI path; UL RI via SRS (`handle_srs_indication`) | Real | rank (1–8) | DL / UL | per-UE |
| PUSCH SINR/RSRP (interval mean) | `ue_cell::handle_crc_pdu` → `cell_metrics_handler::handle_crc_indication` | Real (when PHY reports it) | dB / dBFS | UL | per-UE |
| PUSCH SINR/RSRP (live snapshot) | `ue_channel_state_manager` (`get_pusch_snr`/`get_pusch_rsrp`), read via new `scheduler_ue_state_logger` | Real once first report arrives (`has_pusch_snr_report()` / `optional` gates it); seeded default beforehand | dB / dBFS | UL | per-UE |
| PUCCH SINR | `cell_metrics_handler::handle_pucch_sinr` | Real | dB | UL | per-UE |
| DL/UL MCS (interval mean) | `cell_metrics_handler` (`dl_mcs`/`ul_mcs` accumulators) | Real (averaged) | MCS index | both | per-UE |
| MCS (per grant) | `scheduler_ue_state_logger` from `sched_result` | Real | MCS index | DL | per-UE, per-grant |
| PRB allocation (total used) | `cell_metrics_handler` (`tot_dl_prbs_used`/`tot_ul_prbs_used`) | Real (aggregated) | # PRBs | both | per-UE |
| PRB allocation (exact, per grant) | `scheduler_ue_state_logger` from `dl_msg_alloc::pdsch_cfg.rbs` | Real | RB index/count | DL | per-UE, per-grant |
| HARQ process id | `scheduler_ue_state_logger` from `pdsch_cfg.harq_id` | Real | id | DL | per-UE, per-grant |
| Retransmission count | `scheduler_ue_state_logger` from `grant.context.nof_retxs`; also `harq_max_retxs`/`is_waiting_ack` from `dl_harq_process_handle` | Real | count | DL | per-UE, per-grant |
| BLER / ACK-NACK derived | `cell_metrics_handler` (`dl_nof_ok`/`dl_nof_nok`, `ul_nof_ok`/`ul_nof_nok`) | Real (counts; BLER = nok/(ok+nok) computed by consumer) | count | both | per-UE |
| Throughput / bit rate | `cell_metrics_handler` (`dl_brate_kbps`/`ul_brate_kbps`, from acknowledged TB bytes / report period) | Real | kbps | both | per-UE |
| BSR / pending UL bytes | `cell_metrics_handler::handle_ul_bsr_indication` | Real | bytes | UL | per-UE |
| DL buffer occupancy | `cell_metrics_handler::handle_dl_buffer_state_indication` (`dl_bs`, aggregated) / `scheduler_ue_state_logger` (`dl_buffer_occupancy_bytes`, per-grant post-allocation value) | Real | bytes | DL | per-UE (both scopes) |
| DL HOL delay (**new**) | RLC Tx entities (`lib/rlc/rlc_tx_{tm,um,am}_entity.cpp`) → `dl_buffer_state_indication_message::hol_toa` → `cell_metrics_handler` (this change) | Real | ms | DL | per-UE |
| RAN slice id | `scheduler_ue_state_logger` from `dl_harq_process_handle::get_grant_params().slice_id` | Real (when the UE belongs to a configured slice) | id | DL | per-UE, per-grant |
| PCI / cell id | `cell_configuration::pci`, carried on every Layer 1/Layer 2 record | Real | — | both | per-cell |
| TA / PUSCH-TA / PUCCH-TA / SRS-TA | `cell_metrics_handler` | Real | ns (converted) | UL | per-UE |
| PHR | `cell_metrics_handler::handle_ul_phr_indication` | Real | dB | UL | per-UE |
| Various turnaround delays (CE, CRC, HARQ, SR-to-PUSCH) | `cell_metrics_handler` | Real | ms | UL | per-UE |
| 5QI / QoS flow info | — | **Unavailable in this pass** — see §9 | — | — | — |

## 9. Metrics still missing, and exactly where to add them

- **5QI / DRB QoS flow identity per UE**, requested by the reference design as a tag. srsRAN tracks QoS flow →
  DRB → logical channel mapping in the DU manager / F1AP UE context (`lib/du_manager`, `du_ue` QoS configuration),
  not in the scheduler. **Hook point**: either (a) push 5QI down into `sched_ue_config_request` so the scheduler
  can tag it directly (bigger change, couples scheduler to QoS semantics), or (b) — preferred — join scheduler
  metrics with DU-manager-held QoS config *outside* the scheduler, at the metrics-consumption layer (JSON/E2
  generator), keyed by `ue_index`, which is already common to both sides. Not implemented here: it's a
  cross-layer join, not a scheduler instrumentation gap, and deserves its own design pass.
- **Per-grant UL telemetry (Layer 2)**: the header of `scheduler_ue_state_logger.h` documents this as scope for a
  future iteration — the UL-side equivalent (`sched_result.ul.puschs`, `ul_harq_process_handle`) follows the
  identical pattern used for DL and was intentionally left out of this iteration to keep the first version
  reviewable.
- **Pre-trim required RBs/MCS vs. final allocation** (the OAI reference's `rbs_req/mcs_req` vs. `rbs_out/mcs_out`
  distinction, i.e. what link adaptation *asked for* before PRB availability trimmed it down). srsRAN's link
  adaptation / grant sizing happens inside `lib/scheduler/policy/scheduler_time_qos.cpp` and
  `lib/scheduler/ue_scheduling/grant_params_selector.cpp` / `intra_slice_scheduler.cpp`, before the final grant is
  written into `sched_result`. **Hook point**: instrument `grant_params_selector` (or wherever the pre-trim
  MCS/RB count is first computed) to stash the pre-trim values on the UE's transient per-slot state, then have
  `scheduler_ue_state_logger` read them back when it later sees the final grant in `sched_result`. Not implemented
  here — it requires touching core allocation logic, which is exactly the kind of "avoid touching production
  scheduler logic unless clearly justified" trade-off called out in the task; documented as the next step rather
  than done speculatively.
- **UL HARQ id / retx count / RB allocation at grant level** — same pattern as DL, just not implemented yet (see
  above).

## 10. Mapping to a real near-RT RIC / xApp architecture

- Layer 1 (`scheduler_ue_metrics`/`scheduler_cell_metrics`) is **already** the data source for srsRAN's E2SM-KPM
  agent (`lib/e2/e2sm/e2sm_kpm/e2sm_kpm_du_meas_provider_impl.cpp`), which exposes standard O-RAN measurement
  names (`CQI`, `RSRP`, `RRU.PrbUsedDl/Ul`, `RRU.PrbAvailDl/Ul`, `DRB.UEThpDl/Ul`, `DRB.RlcSduDelayDl`, etc.) over
  E2AP. **A real near-RT RIC pointed at srsRAN's E2 agent today already gets most of the telemetry list in this
  task** (CQI, RSRP, PRB usage, throughput, RLC-side delay) with zero additional code — it's a matter of enabling
  the E2 agent (`e2: enable_du_e2: true`, `e2sm_kpm_enabled: true`) and pointing it at a RIC.
- The new `dl_hol_delay_ms` field is on the same struct, so it is automatically available to be added as a new
  KPM measurement name (there is no existing O-RAN standard name for "scheduler-side instantaneous DL HOL delay";
  `DRB.RlcSduDelayDl` is a related-but-different, already-existing RLC-side *average* SDU pull latency). Adding a
  vendor-specific KPM measurement name for it is a small, self-contained follow-up in
  `e2sm_kpm_du_meas_provider_impl.cpp`, not attempted here to keep this change's blast radius limited to the
  scheduler metrics path.
- Layer 2's per-grant fields (HARQ id, exact RB allocation, RAN slice id) have **no equivalent E2 Service Model
  today**. The architecturally correct long-term home for them is a custom E2SM (or an extension of E2SM-KPM's
  style/label mechanism) subscribed to at a fast reporting period, so a near-RT RIC/xApp gets this at the same
  cadence a real control-loop xApp needs (E2's near-RT budget is ~10 ms–1 s). Until that SM is designed, Layer 2's
  pipe is the pragmatic stand-in for local agent development.
- **Control**: srsRAN's E2SM-RC implementation (`lib/e2/e2sm/e2sm_rc`) already had a fully working DU-side control
  action - **Style 2 / Action 6, "Slice-level PRB quota"** - equivalent to (and better than) OAI's Telnet-driven
  PRB masking, since it's RIC-mediated over standard E2AP rather than a REST-to-Telnet bridge. We proved this
  live (§14.1). We added one more control action, a **UE bitrate cap** (§14.4), for the one OAI capability
  (`UL_control_termination.py`'s FWA/MTC throughput caps) that had no existing srsRAN equivalent. The third OAI
  capability, UL power-control target, turned out to be blocked by srsRAN's Enterprise/OSS split, not by a missing
  E2SM-RC action - see §14.2.

## 11. How to build and run

Build (from `build/`, already configured):

```sh
cmake --build . --target gnb -j"$(nproc)"
```

**Layer 1** (JSON + E2 KPM) — enable in the gNB YAML config:

```yaml
metrics:
  enable_json: true      # writes periodic JSON reports to the JSON log channel
  layers:
    enable_sched: true
  periodicity:
    du_report_period: 1000   # ms

e2:
  enable_du_e2: true
  addr: <near-RT RIC IP>
  port: 36421
  e2sm_kpm_enabled: true
```

**Layer 2** (experimental per-grant pipe), before starting the gNB:

```sh
export SRSRAN_UE_STATE_PIPE=/tmp/srsran_ue_state.pipe
./gnb -c gnb_config.yml &

# in another terminal, once the gNB is running and the FIFO exists:
cat /tmp/srsran_ue_state.pipe | jq .
```

The FIFO is created by the gNB process itself (`mkfifo`, 0666) if it doesn't already exist; if no reader is
attached, writes are dropped silently (never block the scheduler).

**Control** (E2SM-RC, PRB quota - already worked; UE bitrate cap - new, §14.4), from inside the RIC's
`python_xapp_runner` container (`docker compose exec python_xapp_runner bash`, from `~/oran-sc-ric`):

```sh
cd /opt/xApps
# PRB quota (existing srsRAN functionality, cycles 30/50/70/100%):
python3 simple_rc_xapp.py --e2_node_id <your gNB's E2 node id> --ran_func_id 3 --gnb_cu_ue_f1ap_id 0
# UE bitrate cap (new, cycles 5/15/30/60 Mbps DL):
python3 simple_bitrate_cap_xapp.py --e2_node_id <your gNB's E2 node id> --ran_func_id 3 --gnb_cu_ue_f1ap_id 0
```

The gNB's E2 node ID can be found via the E2 Manager's REST API: `curl http://<e2mgr-host>:3800/v1/nodeb/states`
(inside the `ric_e2mgr` container, or wherever it's reachable from). In this testbed it was
`gnbd_001_001_00019b_0` (both test xApps default to this).

## 12. How to validate the metrics are correct

- **Build verification performed**: `srsran_sched`, `scheduler_logger`, `ue_sched`, `sched_ue_context`, the JSON
  generator target, and the full `gnb` executable were all rebuilt from a clean touch of every changed file — all
  compiled and linked with no errors or warnings introduced.
- **Unit tests**: `tests/unittests/scheduler/scheduler_metrics_handler_test.cpp` already exercises
  `cell_metrics_handler`; it should be extended to feed a `dl_buffer_state_indication_message` with a non-default
  `hol_toa` and assert `scheduler_ue_metrics::dl_hol_delay_ms` matches the expected slot-to-ms conversion, and a
  zero-pending-bytes case asserting `nullopt`. **Not run in this environment**: GTest is not installed here
  (`find_package(GTest)` fails), so `-DBUILD_TESTING=ON` cannot currently configure on this machine; this is an
  environment limitation, not a code issue.
- **Runtime validation - executed live against the real testbed** (srsRAN gNB + srsUE over ZMQ, Open5GS 5GC,
  O-RAN SC near-RT RIC, Telegraf/InfluxDB/Grafana - see `setup_run.md`):
  1. Attached a real UE, generated DL traffic (`ping`, then sustained `iperf3` saturation), and confirmed via
     `docker exec influxdb ... SELECT * FROM ue ...` that real per-UE fields (CQI, SINR, MCS, `dl_brate`,
     `dl_nof_ok`, `bsr`, TA stats) update live and match the attached UE's actual RNTI/PCI. Confirmed
     `E2SM-KPM: Received RLC metrics: ue=0 DRB1` logged continuously on the gNB side, i.e. the RIC is actively
     being fed.
  2. **Found `dl_hol_delay_ms` never actually populates** even under sustained non-zero `dl_bs` - root-caused to
     a pre-existing upstream gap, not a bug in this change (§14.3). This is the one telemetry item in this
     document whose "should work" analysis (§8) turned out to be wrong on live testing, and the write-up above
     has been corrected accordingly.
  3. With `SRSRAN_UE_STATE_PIPE` set, captured the FIFO during attach and during traffic tests: confirmed one
     JSON line per slot, non-empty `dl_grants` with real RNTI/HARQ id/MCS/RB allocation matching the gNB's own
     `SCHED`/`PHY` log lines for the same slot, and empty arrays (not fabricated entries) on idle slots.
  4. Validated both control actions live end-to-end - see §14.1 (PRB quota) and §14.4 (new UE bitrate cap) for
     the detailed results (InfluxDB throughput staircases matching the exact xApp-driven targets).

## 13. Known limitations and next steps

- Layer 2 is single-pipe, single-consumer, best-effort, DL-only, and has no schema versioning beyond the
  `"schema":"srsran_ue_state_v1"` tag — treat it as a development tool, not an API contract.
- 5QI/QoS-flow tagging, UL-side per-grant telemetry, and pre-trim link-adaptation values are documented gaps (§9),
  not implemented.
- No E2SM extension was added for the new `dl_hol_delay_ms` field or for any Layer 2 field; both remain
  accessible only via JSON/pipe until a near-RT RIC needs them over E2. The pre-existing srsRAN gap that
  previously kept it unpopulated end-to-end (§14.3) has since been fixed and **live-validated** - `dl_hol_delay_ms`
  now has a real, wired-through value confirmed in InfluxDB under DL saturation (§14.3).
- The `scheduler_metrics_handler_test` unit test should be extended for the new field but this could not be
  verified by running it in this environment (`BUILD_TESTING=OFF`/no GTest installed here, see §14.5) - this
  remains true after the §14.3 fix as well.
- The UE bitrate cap (§14.4) is enforced with a 20 ms token-bucket burst window; this was chosen for
  responsiveness and validated live, but is a tunable, not a value derived from a spec or requirement.
- UE bitrate cap requests and PRB-quota requests both fully replace the UE's `sched_ue_resource_alloc_config` on
  every RIC Control Request (no merge) - see the caveat on `du_mac_sched_control_config` in
  `include/srsran/du/du_high/du_manager/du_configurator.h`. An xApp that wants both a PRB quota and a bitrate cap
  active simultaneously must resend both parameters in every request.
- TX/RX gain control (`ru_gain_controller`) is real, live, non-Enterprise-gated srsRAN functionality, but is not
  yet exposed over E2SM-RC (only via the gNB's interactive stdin console) - see §14.2 for why we didn't pursue it
  as the next control action.

## 14. Control: implemented and validated E2SM-RC extensions

This section covers the control-plane work: what already existed, what we found blocked, and what we built new -
all verified against a **live testbed** (srsRAN gNB + srsUE over the ZMQ RF simulator, Open5GS 5GC, the O-RAN SC
near-RT RIC, and Telegraf/InfluxDB/Grafana), not just by reading code or unit-testing in isolation.

### 14.1 PRB quota control - already implemented, proven live, zero new code

Investigation found that srsRAN's E2SM-RC agent already fully implements **O-RAN Style 2 / Action 6, "Slice-level
PRB quota"**, end to end: `lib/e2/e2sm/e2sm_rc/e2sm_rc_control_action_du_executor.cpp`
(`e2sm_rc_control_action_2_6_du_executor`) parses the RIC Control Request's RRM Policy Ratio Group (min/max/
dedicated PRB ratio, keyed by PLMN + S-NSSAI) and calls `du_configurator::configure_ue_mac_scheduler()`, which
flows through `du_ue_ric_configuration_procedure.cpp` into the scheduler's `sched_ue_resource_alloc_config::
pdsch_grant_size_limits`/`pusch_grant_size_limits`, enforced in `grant_params_selector.cpp`. This is registered
in `lib/e2/common/e2_du_factory.cpp`.

We validated this live using the reference RIC's own example xApp (`~/oran-sc-ric/xApps/python/
simple_rc_xapp.py`), which cycles a UE's PRB quota through 30% → 50% → 70% → 100%, holding each for 5 seconds,
against a real attached UE pulling saturating DL traffic via `iperf3`. Querying InfluxDB (`SELECT time, dl_brate,
dl_mcs FROM ue ORDER BY time DESC`) during the run showed three clean, distinct throughput plateaus - **~23.3
Mbps → ~39.9 Mbps → ~53.6 Mbps** - each held for exactly the ~5 s window the xApp was setting that PRB ratio,
confirmed via the gNB log (`E2SM-RC: Slice-level PRB quota Control Request ... Min PRB Policy Ratio: 10 Max PRB
Policy Ratio: 70 ...` immediately followed by `MAC UE Reconfiguration: finished successfully` and `Sending E2 RIC
Control Acknowledge`). This is a working, real-time, RIC-mediated equivalent of OAI's Telnet-driven PRB masking,
with no code changes required.

### 14.2 UL power-control target and TX/RX gain - investigated, not implemented, here's why

Before building a third control action, we checked whether srsRAN could actually *do* anything with a UL SINR
target or a gain value, since building E2SM-RC plumbing to a dead end would be worse than not building it:

- **UL power control target** (`pusch_sinr_target_dB` in `lib/scheduler/support/pusch_power_controller.{h,cpp}`)
  is read once from static config and then immediately `static_cast<void>`'d to silence an unused-variable
  warning - it is **never read anywhere else in the codebase**. The function that would actually use it,
  `compute_tpc_command()`, is compiled `#ifndef SRSRAN_HAS_ENTERPRISE` with a hardcoded stub in the open-source
  build (`// Dummy function. This feature is only available in the SRSRAN 5G Enterprise version.` -
  `return default_tpc = 1;`). This build has no Enterprise plugin. Building an E2SM-RC action for this would let a
  RIC send a command that gets ACKed and then has zero observable effect - so we didn't build it.
- **TX/RX gain** (`ru_gain_controller::set_tx_gain/set_rx_gain`, `include/srsran/ru/ru_controller.h`) is real,
  live, functional code on real hardware (`lib/radio/uhd/radio_uhd_impl.cpp` calls into the actual UHD driver),
  and is *not* Enterprise-gated. It's already reachable, but only via an interactive stdin command typed into the
  gNB's own console (`apps/units/flexible_o_du/flexible_o_du_commands.h`, `tx_gain`/`rx_gain`). However, our ZMQ
  RF simulator driver hardcodes `set_tx_gain`/`set_rx_gain` to `return false;`
  (`lib/radio/zmq/radio_session_zmq_impl.cpp`) - it's a lossless loopback with no amplitude/noise model at all
  (which is also why every RSRP/SINR/CQI reading throughout this session's testing was suspiciously ideal: CQI
  pinned at 15, RSRP at 0 dBFS). So on *this* testbed, wiring gain control over E2SM-RC would be real code with no
  way to prove it works - only real hardware (or a channel-impairment RF driver, which srsRAN doesn't ship outside
  test-only code) would show a measurable effect. We chose to build the UE bitrate cap instead (§14.4), which is
  pure scheduler-side logic with no RF dependency, and so is just as demonstrable on ZMQ as it will be on real
  hardware.

### 14.3 A pre-existing srsRAN gap found via live validation: DL HOL delay never actually populates - now fixed

While validating `dl_hol_delay_ms` (§6, Layer 1) under a real 46 Mbps DL saturation test, `dl_bs` (DL buffer
occupancy) was confirmed non-zero on every single InfluxDB sample, yet `dl_hol_delay_ms` never appeared even
once - InfluxDB's schema-on-write literally had no column for it. Tracing this down (not merely re-reading our
own code, but the full path from RLC to the DU manager) found the real cause one layer further upstream than
where we'd made our change: `lib/du/du_high/du_manager/du_ue/du_ue_adapters.h`, class
`rlc_tx_mac_buffer_state_updater::on_buffer_state_update()` - the actual production RLC→MAC bridge - has a
`// TODO: set hol_toa` and never copies the real `rlc_buffer_state::hol_toa` (correctly computed by
`lib/rlc/rlc_tx_am_entity.cpp` etc.) into the MAC-layer message. This is a **pre-existing gap in srsRAN itself**,
not introduced by this work, and it means `lib/scheduler/policy/scheduler_time_qos.cpp`'s HOL-delay-based UE
prioritization is *also* silently a no-op in the real DU today, independent of any telemetry concerns. At the time
this section was first written, it was deliberately left unfixed (see §9/§13, original wording below) since it
spans two subsystems, needs a clock-type reconciliation, and can change live scheduling behavior - a decision we
felt belonged to the repo owner rather than a silent fix:

> "Not fixed here (see §9/§13): it spans two subsystems, needs a clock-type reconciliation (`steady_clock` in RLC
> vs. `system_clock` declared on the MAC message vs. `high_resolution_clock` used by the scheduler adapter's
> conversion function), and can change live scheduling behavior, so we treated it as a decision for the repo
> owner rather than a silent fix."

**Update: fixed, at the repo owner's request.** Two intertwined gaps were closed, plus a second silent drop point
found while implementing the fix that this section's original investigation had missed:

1. **The TODO itself** - `du_ue_adapters.h`'s `on_buffer_state_update()` now does `bs.hol_toa = rlc_bs.hol_toa;`
   instead of dropping it.
2. **The three-way clock mismatch**, resolved by standardizing on `steady_clock` throughout (rather than
   converting into `system_clock`/`high_resolution_clock`), since `steady_clock` is what RLC already produces and
   is the semantically correct choice for an elapsed-time measurement - unlike `system_clock` (wall-clock, can
   jump backward on NTP adjustment). Changed: `mac_dl_buffer_state_indication_message::hol_toa`
   (`include/srsran/mac/mac_ue_control_information_handler.h`) from `system_clock` to `steady_clock`; and in
   `lib/mac/mac_sched/srsran_scheduler_adapter.{h,cpp}`, the `last_slot_tp` member and the `chrono_to_slot_point()`
   helper's parameters, from `high_resolution_clock` to `steady_clock`.
3. **A second, previously unidentified drop point** - `lib/mac/mac_dl/mac_cell_processor.cpp`'s
   `update_logical_channel_dl_buffer_states()` also reconstructs a `mac_dl_buffer_state_indication_message` after
   every DL grant (to refresh buffer state post-allocation), and its 3-arg aggregate initializer was likewise
   omitting `hol_toa` even though the same `rlc_buffer_state` it reads `pending_bytes` from also carries the real
   `hol_toa`. Fixed by explicitly setting `bs.hol_toa = rlc_bs.hol_toa;` after construction.

No changes were needed downstream of the MAC boundary - `dl_buffer_state_indication_message`
(`include/srsran/scheduler/scheduler_dl_buffer_state_indication_handler.h`) was already clock-free (`slot_point`),
and every consumer from there on (`ue_event_manager.cpp`, `ue.cpp`, `logical_channel_system.{h,cpp}`,
`slice_ue_repository.h`, `scheduler_time_qos.cpp`, `scheduler_metrics_handler.{h,cpp}`,
`json_generators/du_high/scheduler.cpp`) was already correctly implemented, just starved of a valid input.

`lib/du/du_high/test_mode/mac_test_mode_adapter.cpp`'s fake RLC has its own, separate `// TODO: set bs.hol_toa`
(a synthetic RLC used only by `du_test_mode`, not the production bridge) - left as-is, since a fake RLC has no
notion of a real arrival time to report; out of scope for this fix.

**Verification status**: `srsran_mac` and `srsran_du_manager` rebuild clean, and the full `gnb` binary builds and
links successfully.

**Live-validated, post-fix**: re-ran the same DL-saturation setup as the original bug report (real UE attached
over ZMQ, `iperf3` reverse-mode UDP saturating DL at ~43 Mbps requested / ~46 Mbps achieved) against a gNB running
the rebuilt binary (confirmed via `md5sum` against the installed `/usr/local/bin/gnb` before restarting). Log
confirmed a clean startup (`N2: Connection to AMF ... established`, `E2 Setup procedure successful`) and zero
`[E]`/assert lines across the run. Querying InfluxDB directly:

```sql
SELECT time, dl_brate, dl_bs, dl_hol_delay_ms FROM ue ORDER BY time DESC LIMIT 5
```

`dl_hol_delay_ms` now populates on every sample (previously: present in the schema-on-write sense not at all,
per §12 item 2) - typical values were 1 ms, with a 3 ms spike observed alongside a `dl_bs` bump to ~16 KB,
tracking buffer occupancy as expected. Over a 60 s window: 56/57 samples at 1 ms, 1 sample at 3 ms - consistent,
plausible values, not a fixed/fabricated constant. This testbed only had a single attached UE, so
`scheduler_time_qos.cpp`'s delay-based *prioritization* (as opposed to the delay measurement itself) was not
separately observed under multi-UE contention - see the behavioral note below for what to watch for once that's
tested.

**Behavioral note, not just telemetry**: since `scheduler_time_qos.cpp`'s HOL-delay-based UE prioritization weight
reads from the same now-populated `hol_toa`, this fix also activates a previously-dormant scheduling behavior -
UEs with QoS policies configured (`packet_delay_budget_ms`) will now actually be delay-prioritized against each
other under contention, where before this term was always zero. This is presumably the intended effect of fixing
the gap, but it is a live scheduling-behavior change, not a metrics-only fix - worth watching for during
multi-UE contention testing.

### 14.4 New control action: UE bitrate cap (throughput cap)

**What it is**: a direct cap on a UE's sustained DL and/or UL bitrate, in kbps - the missing third OAI capability
(`UL_control_termination.py`'s `fwa_value`/`mtc_value` Mbps caps). This is a different lever than PRB quota:
PRB quota caps a *resource share* (%), so achieved throughput still floats with channel quality; a bitrate cap
targets the *outcome* (kbps) directly, useful for SLA-style policies.

**Design - a token bucket, not a per-slot heuristic.** We considered (and rejected) computing an instantaneous
per-slot RB-count ceiling from the kbps target and the UE's current spectral efficiency; this would only
approximate the target rate, since it ignores how many slots per second the UE is actually granted at all
(TDD pattern, contention with other UEs). Instead: a token bucket (`lib/scheduler/support/ue_bitrate_limiter.
{h,cpp}`, new) tracks available "byte budget" per UE per direction, refilling continuously at the configured kbps
rate up to a 20 ms burst allowance, consumed as real grants are made. New caps start with a *full* bucket so
enabling/raising a cap never causes an artificial initial stall.

**Enforcement hook.** New-data (never retransmission) grant sizing in `lib/scheduler/ue_scheduling/
grant_params_selector.cpp` (`get_dl_sched_context`/`get_ul_sched_context`) clamps `pending_bytes` to
`ue_cell::dl_bitrate_limiter().get_max_grantable_bytes(slot)` (a `const`, side-effect-free "peek", since this
grant-sizing code operates on a `const ue_cell&`) before computing MCS/PRB count - when the budget is 0, the
existing "0 pending bytes → no valid allocation" path naturally skips the grant, so no new "should I throttle"
branch was needed anywhere in the sizing logic. The actual token deduction happens once, at the real commit point
where final TBS is known - `lib/scheduler/ue_scheduling/ue_cell_grid_allocator.cpp`, `set_pdsch_params()` /
`set_pusch_params()` - via `consume(slot, tbs_bytes)` (mutating, called on the non-`const` `ue_cell&` available
there). Retransmissions are never throttled or counted against the budget, matching normal traffic-shaping
practice (skipping a retx would only waste a HARQ process and hurt reliability, not reduce the counted rate).

**Config threading**: `max_dl_bitrate_kbps`/`max_ul_bitrate_kbps` (`std::optional<unsigned>`, `nullopt` = no cap)
were added to `sched_ue_resource_alloc_config` (`include/srsran/scheduler/scheduler_configurator.h`) and to
`du_mac_sched_control_config` (`include/srsran/du/du_high/du_manager/du_configurator.h`), threaded through
`du_ue_ric_configuration_procedure.cpp` alongside (not replacing) the existing PRB-ratio threading. Two
pre-existing gates in that procedure would otherwise have rejected a bitrate-cap-only request outright - an
unconditional `request.rrm_policy_ratio_list[0]` index (undefined behavior on an empty list) in
`handle_mac_config()`, and a hard `if (request.rrm_policy_ratio_list.empty()) return failure;` guard in
`dispatch_ue_config_task()` - both were relaxed to also accept a request that only carries a bitrate cap, without
changing behavior for existing PRB-quota-only requests.

**New E2SM-RC control action**: `e2sm_rc_control_action_2_201_du_executor`
(`lib/e2/e2sm/e2sm_rc/e2sm_rc_control_action_du_executor.{h,cpp}`), registered under the same Style 2 service as
the PRB-quota action in `lib/e2/common/e2_du_factory.cpp`. Action ID **201** is deliberately outside the O-RAN
Style 2 standard catalogue's range: there is no standard action for a direct bitrate cap (the closest, PRB quota,
caps resource share, not rate), so this is explicitly a vendor/srsRAN-specific extension, not a misuse of a
real standard action ID. RAN parameters: ID 1 = "Maximum DL Bitrate" (kbps, integer), ID 2 = "Maximum UL Bitrate"
(kbps, integer), following the exact same flat leaf-parameter pattern the existing code uses for min/max PRB
ratio.

**xApp side**: `~/oran-sc-ric/xApps/python/lib/e2sm_rc_module.py` gained
`send_control_request_style_2_action_201()` (aliased `control_ue_bitrate_cap`), and a new standalone test xApp,
`~/oran-sc-ric/xApps/python/simple_bitrate_cap_xapp.py`, cycling a UE's DL cap through 5/15/30/60 Mbps every 5 s
(mirroring `simple_rc_xapp.py`'s structure for the PRB-quota demo).

**Live validation result**: with `iperf3` saturating the DL link, running `simple_bitrate_cap_xapp.py` against
the real gNB produced (confirmed via the gNB log's `E2SM-RC: UE Bitrate Cap Control Request ... max_dl_kbps=...`
+ `Sending E2 RIC Control Acknowledge` lines, and independently via InfluxDB) a clean, **repeating** staircase in
`dl_brate`:

| Cap requested | Achieved `dl_brate` (steady-state) | Error |
|---|---|---|
| 5,000 kbps | ~5.30 Mbps | ~+6% |
| 15,000 kbps | ~15.29 Mbps | ~+2% |
| 30,000 kbps | ~30.57 Mbps | ~+2% |
| 60,000 kbps | ~60.30 Mbps | ~+1% |

The cycle repeated identically on a second pass through the same four steps, confirming this is real, causal,
reproducible control, not a one-off coincidence. The small consistent overshoot is the token bucket's 20 ms burst
allowance plus TBS-granularity rounding, both expected and bounded.

**Real-hardware / COTS UE compatibility**: this mechanism is deliberately pure scheduler-side arithmetic (byte
budgets, PRB/MCS selection) with no RF-layer dependency anywhere in the enforcement path - it does not touch
`ru_gain_controller`, PHY gain, or any driver-specific code, unlike the gain-control option discussed in §14.2.
Nothing in `ue_bitrate_limiter` or its call sites references the ZMQ driver, a simulator, or any test-only code
path, so the same enforcement logic applies identically whether the RF path is the ZMQ simulator, a UHD/USRP
front end, or any other supported radio, and identically whether the far end is `srsUE` or a COTS UE (the
scheduler has no visibility into which - grant sizing is UE-implementation-agnostic). This was a design
constraint from the start (§14.4 "Design"), not a retrofit.

### 14.5 Build and validation evidence for the control-plane changes

- `srsran_sched`, `srsran_du_manager`, `srsran_e2`, and the full `gnb` executable were rebuilt from scratch after
  every control-plane change in this section; all compiled and linked cleanly.
- No unit tests were run (GTest is not installed in this environment; `-DBUILD_TESTING=ON` fails to configure -
  see §12/§13). All validation is therefore live-system evidence (gNB log lines, InfluxDB query results),
  documented inline in §14.1 and §14.4, gathered against the actual testbed described in `setup_run.md` rather
  than assumed from code inspection.
- One gNB crash was observed earlier in this session (before the control-plane work), coinciding with an
  unbounded UDP `iperf3 -R` flood; no error was logged and no core dump was found. It was not reproduced during
  any of the control-plane testing (which used bounded TCP `iperf3` runs), and its cause remains unconfirmed -
  flagged here for visibility, not because it's known to relate to any of these changes.

## 15. Files changed (control-plane additions, this session)

| File | Status |
|---|---|
| `lib/scheduler/support/ue_bitrate_limiter.h` | new |
| `lib/scheduler/support/ue_bitrate_limiter.cpp` | new |
| `lib/scheduler/support/CMakeLists.txt` | modified |
| `lib/scheduler/ue_context/ue_cell.h` | modified |
| `lib/scheduler/ue_context/ue_cell.cpp` | modified |
| `include/srsran/scheduler/scheduler_configurator.h` | modified |
| `lib/scheduler/ue_scheduling/grant_params_selector.cpp` | modified |
| `lib/scheduler/ue_scheduling/ue_cell_grid_allocator.cpp` | modified |
| `include/srsran/du/du_high/du_manager/du_configurator.h` | modified |
| `lib/du/du_high/du_manager/procedures/du_ue_ric_configuration_procedure.cpp` | modified |
| `lib/e2/e2sm/e2sm_rc/e2sm_rc_control_action_du_executor.h` | modified |
| `lib/e2/e2sm/e2sm_rc/e2sm_rc_control_action_du_executor.cpp` | modified |
| `lib/e2/common/e2_du_factory.cpp` | modified |
| `~/oran-sc-ric/xApps/python/lib/e2sm_rc_module.py` | modified (separate repo) |
| `~/oran-sc-ric/xApps/python/simple_bitrate_cap_xapp.py` | new (separate repo) |
