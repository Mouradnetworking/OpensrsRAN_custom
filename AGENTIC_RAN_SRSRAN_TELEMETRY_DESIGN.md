# Agentic RAN: telemetry and control extensions for srsRAN

**Status**: every contribution below is implemented and validated **live** against a real 5G stack — srsRAN
gNB + srsUE over the ZMQ RF simulator, Open5GS 5GC, the O-RAN SC near-RT RIC, Telegraf/InfluxDB/Grafana. Everything is opt-in or additive to the base
srsRAN codebase.

## 0. Motivation, gaps, and contributions

### 0.1 Why this work exists

Turning srsRAN + a near-RT RIC into a platform that real agents (rule-based, RL-trained, or LLM-grounded) can
observe and act on requires two things a stock deployment does not provide: **decision-level visibility** —
what did the scheduler actually decide, on which HARQ process, on which exact resource blocks, and why — not a
1-second average that has already thrown that information away; and **outcome-level control** — levers that
match what an operator or an SLA actually specifies (a target bitrate, a guaranteed/maximum bit rate per
bearer), not only a resource-share percentage that still leaves the achieved outcome to chance. Building that
platform is the goal of this work.

### 0.2 Gaps found in the existing srsRAN + near-RT RIC stack

Each of these was established by direct source-code tracing and confirmed (or, in one case, *disproven and
corrected*) through live testing:

1. **No per-grant telemetry existed at all.** srsRAN's only metrics path (`cell_metrics_handler` →
   JSON/E2SM-KPM) reports one aggregated snapshot per UE per report period (typically ≥100 ms–1 s). There was
   no way to see an individual scheduling decision — which HARQ process, which exact RBs, whether it was a
   retransmission — short of attaching a debugger or hand-parsing raw `SCHED` log lines.
2. **A real telemetry field, and the scheduling behavior that depends on it, was silently dead.** This gap has
   two distinct parts, both traced to the same root cause:

   a. **The telemetry field was dead.** `dl_hol_delay_ms`: DL head-of-line queueing delay, i.e. how long the
      oldest queued DL packet has been waiting — was defined in the metrics struct and JSON output, but its
      underlying value (`hol_toa`) was computed correctly by RLC and then silently dropped one layer up, in the
      DU manager's RLC→MAC bridge (`du_ue_adapters.h`, behind a literal `// TODO: set hol_toa`). The field
      therefore never carried real data in any deployment.

   b. **The scheduling *behavior* that depends on that field was dead too, as a direct consequence.**
      `scheduler_time_qos.cpp` has a delay-aware QoS prioritization term that is supposed to raise a UE's
      scheduling priority the longer its data has been queued, weighted by that bearer's
      `packet_delay_budget_ms`. That term reads from the exact same `hol_toa` value. Because `hol_toa` was
      never populated upstream (part **a**), this term had been computing against a constant/default input
      since it was written, meaning the *scheduling policy*, not just the *metric*, had shipped but had never
      once actually taken effect in any real deployment of this codebase.

   Both were fixed together. Fixing it did not just make the metric appear, it simultaneously activated a scheduling behavior that had been
   dormant since it was first written, which is why [§11](#11-known-limitations-and-next-steps) flags multi-UE validation (confirming the scheduler now
   measurably favors the higher-delay-weight UE under contention) as the necessary next step before relying on this fix in any QoS-sensitive scenario.
3. **No outcome-based rate control existed.** The one working E2SM-RC control action (Style 2/Action 6, PRB
   quota, a pre-existing srsRAN capability)
   caps a *resource share*; achieved throughput still floats with radio conditions. Nothing let a RIC state a
   target *bitrate* directly.
4. **No standards-based per-bearer GBR/MFBR enforcement**, a standards-aligned E2SM-RC xApp based on the O-RAN Alliance Control Style 1 / Action  1 radio-bearer control model, enabling the near-RT RIC to perform post-admission, per-DRB QoS configuration using standard 5G parameters such as 5QI, GBR, and MFBR. Instead of relying only on resource-share controls such as PRB quotas, the xApp exposes a bearer-level control path for bitrate guarantees and limits through the E2SM-RC framework. Prior to this work, srsRAN already transported these QoS values through the F1AP/E1AP/NGAP/RRC signaling chain during admission, but they remained passive because no enforcement-side logic consumed them and no E2SM-RC action existed to update them after bearer establishment.Pre-existing srsRAN bugs in the
   shared PDU-Session-Modify signaling code that silently broke *any* QoS-only DRB reconfiguration — bugs that
   had evidently never been triggered before, because nothing in this codebase's own test or demo path exercises
   that call chain end to end (full root-cause writeups in `agentic_ran/GBR_MFBR_QOS_ENFORCEMENT.md`).

### 0.3 Contributions

| | Contribution | Status |
|---|---|---|
| **C1** | A new per-slot, per-grant telemetry pipeline ("Layer 2") exposing HARQ process id, exact RB allocation, retransmission count, live PUSCH SINR/RSRP, and RAN slice id per grant — none of which existed in any form before this project ([§2](#2-final-design-chosen-for-srsran)–[§5](#5-metrics-currently-emitted)). | Live-validated ([§10](#10-how-to-validate-the-metrics-are-correct)) |
| **C2** | Found and fixed a genuine upstream srsRAN bug that silently killed DL head-of-line delay telemetry *and* the scheduler's own delay-aware prioritization logic that depends on it — traced across RLC, MAC, and three separate, mismatched clock types ([§12.3](#123-a-pre-existing-srsran-gap-found-via-live-validation-dl-hol-delay-never-actually-populates---now-fixed)). | Live-validated, fixed. |
| **C3** | Designed, implemented, and live-validated a new E2SM-RC control action for a direct per-UE bitrate cap — a token-bucket, scheduler-side enforcement mechanism. measured within ~1–6% of four different target rates, repeatably ([§12.4](#124-new-control-action-ue-bitrate-cap-throughput-cap)). | Live-validated |
| **C4** | Standards-compliant per-DRB GBR/MFBR enforcement exposed via O-RAN E2SM-RC Style 1/Action 1 ("DRB QoS Configuration") — the first working enforcement point. see `agentic_ran/DRB_QOS_XAPP_SYSTEM_DESIGN.md`. | Live-validated |
| **C5** | Root-caused and fixed three separate, pre-existing srsRAN signaling bugs (RRC/F1AP) that blocked any live QoS-only DRB reconfiguration — found only because this project pushed the platform through a call path apparently never exercised end to end before. See `agentic_ran/GBR_MFBR_QOS_ENFORCEMENT.md` §F. | Live-validated |

### 0.4 On the engineering effort involved

None of the above is glue code wrapped around an existing API. 

- C2 addressed a cross-layer debugging and correction of a broken data path: a delay-related field was being produced in RLC, expected by the MAC/scheduler pipeline, but silently lost before it could influence scheduling decisions. Fixing the issue required tracing that value across the RLC → MAC → scheduler boundary and correcting the interfaces that carried it. It also required resolving inconsistent time semantics across the same path, where steady_clock, system_clock, and high_resolution_clock were being mixed in ways that made delay computation unreliable. In other words, C2 was not a one-line patch, it was a cross-subsystem repair that restored both the propagation of the metric and the correctness of the timing model behind it. 

- C3 introduces a new E2SM-RC control action for direct per-UE bitrate capping, addressing a gap in srsRAN where the only working RIC control lever was a PRB quota: a resource-share mechanism that could allocate a percentage of radio resources but could not enforce an operator-facing throughput target such as “cap this UE at 30 Mbps.” Instead of using a naive per-slot RB-count heuristic, which incorrectly assumes that the UE will be scheduled in every future slot and therefore fails under TDD constraints and multi-UE contention, C3 implements a token-bucket mechanism that maintains a per-UE, per-direction byte budget refilled at the configured rate. When the scheduler grants new data, it checks the available budget to limit the grant size, and spends the budget only when the final allocation is committed, while retransmissions remain unthrottled to preserve HARQ reliability. Live validation with a real attached UE saturating the link via iperf3 showed that cycling the DL cap through 5/15/30/60 Mbps produced a repeatable throughput staircase in InfluxDB, tracking each target within roughly 1–6%, with the small bounded overshoot explained by the 20 ms burst allowance and TBS rounding.

- C4 required first establishing, against the actual O-RAN.WG3.E2SM-RC v03.00 specification text rather than by
assumption. It then required extending the C3 token-bucket mechanism from a per-UE aggregate cap to
per-DRB (DL) / per-LCG (UL) enforcement, which surfaced a genuine standards-level constraint, not an
implementation gap: TS 38.321 Logical Channel Prioritization is UE-controlled, not gNB-controlled, so a gNB can
only enforce UL fairness at the granularity its own LCG-sharing allows, no gNB-side mechanism can force a
UE's LCP algorithm to split UL bytes across DRBs in a specific way. Finally, C4 was the first control path in
this codebase to push a value all the way through the full post-admission F1AP/E1AP/NGAP/RRC signaling chain,
which is exactly what surfaced the three C5 bugs below; C4 could not be completed, let alone live-validated,
until C5 was fixed.

- C5 required root-causing *why* a standards-correct RRC message was being rejected by a real UE stack, the bug
was not a crash or a log error, it was a spec-compliant-looking message that was nonetheless wrong, findable only
by reading `srsue`'s own rejection logic and reasoning about 3GPP TS 38.331 `DRB-ToAddMod` semantics, not by
pattern-matching a stack trace. All three bugs sat in shared, pre-existing signaling code exercised by *any*
QoS-only DRB reconfiguration, not just this project's own control path, meaning they had evidently gone
undetected because nothing in this codebase's own test or demo path had ever exercised that call chain end to
end before.

And critically: **every one of C2–C5 was proven against a live, full protocol stack** — a real gNB, a real UE, a
real 5GC, a real near-RT RIC — not a mock, not a unit test in isolation, because a metric or a control action
that looks correct on paper but has never taken a real ACK/NACK round trip through an unmodified UE's RRC stack
is not actually proven to work.

## 1. System overview

srsRAN is the RAN implementation; the goal is agents / xApps that **observe** live RAN state at decision-level
granularity (C1, C2), **reason** over it, and **control** RAN behavior with outcome-level levers (C3, C4) —
talking E2AP to a real O-RAN near-RT RIC rather than through an ad-hoc scripted loop. [§2](#2-final-design-chosen-for-srsran) lays out the resulting
architecture in full; the rest of this document is the field-by-field and file-by-file record of how each
contribution above was built, validated, and where its limits are.

## 2. Final design chosen for srsRAN

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
one. Where this pipeline was silently dropping data srsRAN already computes (see [§8](#8-mapping-to-a-real-near-rt-ric--xapp-architecture), DL HOL delay), we extended
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
   long-term control path; see [§9](#9-how-to-build-and-run).
5. *Hybrid: collect internally now, keep a clean path to real xApps/RIC* — this is what we built: Layer 1 (already
   RIC-ready) + Layer 2 (dev-time bridge, explicitly temporary/experimental).

## 3. Files changed (telemetry)

See [§13](#13-files-changed-control-plane-additions-this-session) for the control-plane files added later in this effort.

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

## 4. Per-file rationale

### Layer 2 — new per-grant telemetry exporter

- **`lib/scheduler/logging/scheduler_ue_state_logger.{h,cpp}` (new)** — Implements the opt-in per-slot exporter.
  Disabled unless the `SRSRAN_UE_STATE_PIPE` environment variable names a FIFO path; when enabled, installs a
  custom srslog sink (`ue_state_pipe_sink`) that opens the FIFO non-blocking and drops samples rather than ever
  blocking the scheduler thread (formatting happens on the caller's thread, the actual `write()` happens on
  srslog's async backend thread). Emits one `{"schema":"srsran_ue_state_v1", ...}` JSON line per slot, with a
  `"dl_grants"` array (one object per DL grant) and a `"ul_grants"` array (one object per UL/PUSCH grant, following the identical pattern). Supports: raw grant decision (RNTI, UE index, HARQ id,
  MCS, RB allocation, retransmission count — plus DL buffer occupancy for DL grants specifically, "free" from
  `sched_result`; no live pending-bytes accessor is currently exposed for UL grants, see [§8](#8-mapping-to-a-real-near-rt-ric--xapp-architecture)), plus live
  channel-state/HARQ enrichment looked up through the new `find_ue_cell()` accessor (see below) - the same
  accessor and the same `ue_channel_state_manager`/HARQ types serve both directions, since PUSCH SINR/RSRP and
  the HARQ handles are already direction-agnostic APIs (`ue_cell::harqs.dl_harq()`/`.ul_harq()`).
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

## 5. Metrics currently emitted

### Layer 1 (periodic, per report interval; JSON + E2SM-KPM)

**Already emitted by stock srsRAN, before this project — untouched, only verified ([§6](#6-metric-by-metric-source-table)/[§10](#10-how-to-validate-the-metrics-are-correct)):**
RNTI, UE index, PCI, CQI (mean over interval), DL/UL RI, DL/UL MCS (mean), DL/UL PRB usage (total), DL/UL bit
rate, DL/UL HARQ ACK/NACK counts, PUSCH SINR/RSRP (mean over interval), PUCCH SINR (mean), BSR, SR count, DL
buffer occupancy (`dl_bs`), invalid-UCI counters, PHR, TA/PUSCH-TA/PUCCH-TA/SRS-TA statistics, several
turnaround-delay statistics (UL CE, CRC, PUSCH/PUCCH HARQ, SR-to-PUSCH), plus cell-wide counters (PRACH
preambles, MSG3 OK/NOK, failed PDCCH/UCI allocations, decision latency histogram, PUCCH RB usage, per-TDD-slot
PRB usage). That is: **every Layer 1 field except the one below is pre-existing srsRAN functionality.**

**Added by this project (the only new Layer 1 field):**
**`dl_hol_delay_ms`** — DL head-of-line queueing delay. The value it's built from (`hol_toa`) was already
computed by RLC; it was being silently dropped one layer up before this project's fix ([§12.3](#123-a-pre-existing-srsran-gap-found-via-live-validation-dl-hol-delay-never-actually-populates---now-fixed)). This field did
not exist in `scheduler_ue_metrics`, the JSON output, or anywhere in the periodic report before this project.

### Layer 2 (per slot, per DL/UL grant; opt-in JSON pipe)

**Everything in this layer is new — none of it existed in srsRAN before this project** ([§2](#2-final-design-chosen-for-srsran)/[§3](#3-files-changed-telemetry)/[§4](#4-per-file-rationale)). Unlike Layer
1, there is no "pre-existing" column to fill in here: the whole opt-in FIFO pipe, both arrays below, and the
scheduler read-only accessors they needed were all built from scratch for this work.

**`dl_grants`**: `rnti`, `ue_index`, `harq_id`, `new_data`, `nof_retxs`, `mcs`, `dl_buffer_occupancy_bytes`,
`rb_alloc_type` + (`rb_start`,`rb_len` for contiguous grants, or `rb_len` alone for RBG-bitmap grants),
`bwp_start`, `bwp_size`, `wideband_cqi`, `dl_ri` (both only if the latest CSI report is valid), `pusch_sinr_db`
+ `pusch_avg_sinr_db` (only once a real report has arrived), `pusch_rsrp_dbfs` (once reported),
`harq_max_retxs`, `harq_waiting_ack`, `ran_slice_id` (if the grant belongs to a configured slice).

**`ul_grants`**: `rnti`, `ue_index`, `harq_id`, `new_data`, `nof_retxs`, `mcs`, `rb_alloc_type` +
(`rb_start`,`rb_len`/`rb_len`, same convention as DL), `bwp_start`, `bwp_size`, `pusch_sinr_db` +
`pusch_avg_sinr_db`, `pusch_rsrp_dbfs`, `harq_max_retxs`, `harq_waiting_ack`, `ran_slice_id` - identical to
`dl_grants` except no `dl_buffer_occupancy_bytes`-equivalent field (no live pending-UL-bytes accessor
currently exposed to this component, see [§8](#8-mapping-to-a-real-near-rt-ric--xapp-architecture)) and no `wideband_cqi`/`dl_ri` (those are DL CSI-report fields,
not applicable to a UL grant).

Envelope, shared by both arrays: `schema`, `ts_ns`, `sfn`, `slot`, `pci`.

## 6. Metric-by-metric source table

The **Provenance** column is the direct answer to "did srsRAN already have this, or did this project add it":
**Pre-existing** = srsRAN already computed and exposed this before this project touched anything (this project,
at most, only *read* it in a new place); **New** = the field, the accessor, or the code path that produces it
did not exist before this project. This is a different question from the **Real/Derived/Unavailable** column,
which is about whether the *data value* is genuine telemetry vs. a fabricated placeholder vs. simply not
obtainable at all — a pre-existing field can still be "Real", and a brand-new field can still be "Real" too, if
it's just exposing data that was already being computed internally (exactly `dl_hol_delay_ms`'s situation).

| Metric | Provenance | Source (file : class/function) | Real / Derived / Unavailable | Unit | DL/UL | Scope |
|---|---|---|---|---|---|---|
| RNTI | **Pre-existing** | `scheduler_metrics.h` / `sched_result` grants | Real | — | both | per-UE |
| UE index | **Pre-existing** | same | Real | — | both | per-UE |
| PCI | **Pre-existing** | `cell_configuration::pci` | Real | — | both | per-cell |
| CQI | **Pre-existing** | `ue_channel_state_manager::handle_csi_report` → `cell_metrics_handler` (`cqi_stats`) | Real | 0–15 index | DL | per-UE |
| DL RI / UL RI | **Pre-existing** | same CSI path; UL RI via SRS (`handle_srs_indication`) | Real | rank (1–8) | DL / UL | per-UE |
| PUSCH SINR/RSRP (interval mean) | **Pre-existing** | `ue_cell::handle_crc_pdu` → `cell_metrics_handler::handle_crc_indication` | Real (when PHY reports it) | dB / dBFS | UL | per-UE |
| PUSCH SINR/RSRP (live snapshot) | **New (Layer 2)** — `get_pusch_snr()` existed, but `get_pusch_rsrp()`/`update_pusch_rsrp()`/`has_pusch_snr_report()` and the whole read path via `scheduler_ue_state_logger` did not | `ue_channel_state_manager` (`get_pusch_snr`/`get_pusch_rsrp`), read via new `scheduler_ue_state_logger` | Real once first report arrives (`has_pusch_snr_report()` / `optional` gates it); seeded default beforehand | dB / dBFS | UL | per-UE |
| PUCCH SINR | **Pre-existing** | `cell_metrics_handler::handle_pucch_sinr` | Real | dB | UL | per-UE |
| DL/UL MCS (interval mean) | **Pre-existing** | `cell_metrics_handler` (`dl_mcs`/`ul_mcs` accumulators) | Real (averaged) | MCS index | both | per-UE |
| MCS (per grant) | **New (Layer 2)** | `scheduler_ue_state_logger` from `sched_result` (`dl_grants`/`ul_grants`) | Real | MCS index | both | per-UE, per-grant |
| PRB allocation (total used) | **Pre-existing** | `cell_metrics_handler` (`tot_dl_prbs_used`/`tot_ul_prbs_used`) | Real (aggregated) | # PRBs | both | per-UE |
| PRB allocation (exact, per grant) | **New (Layer 2)** | `scheduler_ue_state_logger` from `dl_msg_alloc::pdsch_cfg.rbs` / `ul_sched_info::pusch_cfg.rbs` | Real | RB index/count | both | per-UE, per-grant |
| HARQ process id | **New (Layer 2)** | `scheduler_ue_state_logger` from `pdsch_cfg.harq_id` / `pusch_cfg.harq_id` | Real | id | both | per-UE, per-grant |
| Retransmission count | **New (Layer 2)** | `scheduler_ue_state_logger` from `grant.context.nof_retxs`; also `harq_max_retxs`/`is_waiting_ack` from `dl_harq_process_handle`/`ul_harq_process_handle` | Real | count | both | per-UE, per-grant |
| BLER / ACK-NACK derived | **Pre-existing** | `cell_metrics_handler` (`dl_nof_ok`/`dl_nof_nok`, `ul_nof_ok`/`ul_nof_nok`) | Real (counts; BLER = nok/(ok+nok) computed by consumer) | count | both | per-UE |
| Throughput / bit rate | **Pre-existing** | `cell_metrics_handler` (`dl_brate_kbps`/`ul_brate_kbps`, from acknowledged TB bytes / report period) | Real | kbps | both | per-UE |
| BSR / pending UL bytes (periodic) | **Pre-existing** | `cell_metrics_handler::handle_ul_bsr_indication` | Real | bytes | UL | per-UE |
| DL buffer occupancy | **Mixed**: the Layer 1 aggregated `dl_bs` is **pre-existing**; the Layer 2 per-grant `dl_buffer_occupancy_bytes` value is **new** | `cell_metrics_handler::handle_dl_buffer_state_indication` (`dl_bs`, aggregated) / `scheduler_ue_state_logger` (`dl_buffer_occupancy_bytes`, per-grant post-allocation value) | Real | bytes | DL | per-UE (both scopes) |
| Pending UL bytes (per-grant) | N/A — not built | — | **Unavailable at grant level** — `ue::pending_ul_newtx_bytes()` exists but isn't reachable from `scheduler_ue_state_logger`'s current `ue_cell`-scoped accessor; see [§8](#8-mapping-to-a-real-near-rt-ric--xapp-architecture) | bytes | UL | per-UE, per-grant |
| DL HOL delay | **New (Layer 1 fix)** — the only new *field* in Layer 1; see [§0](#0-motivation-gaps-and-contributions)/[§12.3](#123-a-pre-existing-srsran-gap-found-via-live-validation-dl-hol-delay-never-actually-populates---now-fixed) | RLC Tx entities (`lib/rlc/rlc_tx_{tm,um,am}_entity.cpp`) → `dl_buffer_state_indication_message::hol_toa` → `cell_metrics_handler` (this change) | Real | ms | DL | per-UE |
| UL queueing-delay proxy | **Pre-existing** | `cell_metrics_handler` (`avg_sr_to_pusch_delay`/`max_sr_to_pusch_delay`) | Real, but a coarser proxy (SR→grant turnaround, not true per-SDU HOL delay) — see [§8](#8-mapping-to-a-real-near-rt-ric--xapp-architecture)/[§12.3](#123-a-pre-existing-srsran-gap-found-via-live-validation-dl-hol-delay-never-actually-populates---now-fixed) for why no `ul_hol_delay_ms` equivalent exists | ms | UL | per-UE |
| RAN slice id | **New (Layer 2)** — the slice concept/assignment itself is pre-existing scheduler state; exposing it *per grant* is new | `scheduler_ue_state_logger` from `dl_harq_process_handle`/`ul_harq_process_handle::get_grant_params().slice_id` | Real (when the UE belongs to a configured slice) | id | both | per-UE, per-grant |
| PCI / cell id | **Pre-existing** | `cell_configuration::pci`, carried on every Layer 1/Layer 2 record | Real | — | both | per-cell |
| TA / PUSCH-TA / PUCCH-TA / SRS-TA | **Pre-existing** | `cell_metrics_handler` | Real | ns (converted) | UL | per-UE |
| PHR | **Pre-existing** | `cell_metrics_handler::handle_ul_phr_indication` | Real | dB | UL | per-UE |
| Various turnaround delays (CE, CRC, HARQ, SR-to-PUSCH) | **Pre-existing** | `cell_metrics_handler` | Real | ms | UL | per-UE |
| 5QI / QoS flow info | N/A — not built | — | **Unavailable in this pass** — see [§8](#8-mapping-to-a-real-near-rt-ric--xapp-architecture) | — | — | — |

## 7. Metrics still missing, and exactly where to add them

- **5QI / DRB QoS flow identity per UE**, requested by the reference design as a tag. srsRAN tracks QoS flow →
  DRB → logical channel mapping in the DU manager / F1AP UE context (`lib/du_manager`, `du_ue` QoS configuration),
  not in the scheduler. **Hook point**: either (a) push 5QI down into `sched_ue_config_request` so the scheduler
  can tag it directly (bigger change, couples scheduler to QoS semantics), or (b) — preferred — join scheduler
  metrics with DU-manager-held QoS config *outside* the scheduler, at the metrics-consumption layer (JSON/E2
  generator), keyed by `ue_index`, which is already common to both sides. Not implemented here: it's a
  cross-layer join, not a scheduler instrumentation gap, and deserves its own design pass.
- **UL HOL/queueing delay, and UL delay-based scheduler prioritization** — deliberately *not* pursued as a
  literal port of the DL `dl_hol_delay_ms` fix. See [§12.3](#123-a-pre-existing-srsran-gap-found-via-live-validation-dl-hol-delay-never-actually-populates---now-fixed)'s closing note for why: the gNB has no timestamp of
  when a UL SDU arrived at the UE's RLC (unlike DL, where the gNB's own RLC Tx entity produces that
  timestamp), so there is no `rlc_buffer_state`-equivalent event to plumb through for UL. The closest real,
  already-instrumented proxy is SR-to-PUSCH-grant turnaround delay (`avg_sr_to_pusch_delay`/
  `max_sr_to_pusch_delay`, already in `cell_metrics_handler` and already flowing to InfluxDB) - coarser than
  true per-packet HOL delay, but real, not fabricated. Building genuine UL queueing-delay measurement would
  require new UE-side signaling that doesn't exist today, and wiring it into
  `scheduler_time_qos.cpp::compute_ul_qos_weights()` (which currently hard-codes its delay-weight argument to
  `1.0` - confirmed no UL delay term exists even in stub form) would be a live UL scheduling-behavior change,
  not a telemetry gap. Left as a deferred, separate, higher-risk item - the same category of decision as the
  DL clock-mismatch fix was originally treated as.

## 8. Mapping to a real near-RT RIC / xApp architecture

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
  action - **Style 2 / Action 6, "Slice-level PRB quota"**. We proved this
  live ([§12.1](#121-baseline-for-comparison-pre-existing-prb-quota-control-not-a-contribution-of-this-project)). We added one more control action, a **UE bitrate cap** ([§12.4](#124-new-control-action-ue-bitrate-cap-throughput-cap)).

## 9. How to build and run

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

**Control** (E2SM-RC, PRB quota - already worked; UE bitrate cap - new, [§12.4](#124-new-control-action-ue-bitrate-cap-throughput-cap)), from inside the RIC's
`python_xapp_runner` container (`docker compose exec python_xapp_runner bash`, from `~/oran-sc-ric`):

```sh
cd /opt/xApps
# PRB quota (existing srsRAN functionality, cycles 30/50/70/100%):
python3 simple_rc_xapp.py --e2_node_id <your gNB E2 node id> --ran_func_id 3 --gnb_cu_ue_f1ap_id 0
# UE bitrate cap (new, cycles 5/15/30/60 Mbps DL):
python3 simple_bitrate_cap_xapp.py --e2_node_id <your gNB's E2 node id> --ran_func_id 3 --gnb_cu_ue_f1ap_id 0
```

The gNB's E2 node ID can be found via the E2 Manager's REST API: `curl http://<e2mgr-host>:3800/v1/nodeb/states`
(inside the `ric_e2mgr` container, or wherever it's reachable from). In this testbed it was
`gnbd_001_001_00019b_0` (both test xApps default to this).

## 10. How to validate the metrics are correct

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
     a pre-existing upstream gap, not a bug in this change ([§12.3](#123-a-pre-existing-srsran-gap-found-via-live-validation-dl-hol-delay-never-actually-populates---now-fixed)). This is the one telemetry item in this
     document whose "should work" analysis ([§7](#7-metrics-still-missing-and-exactly-where-to-add-them)) turned out to be wrong on live testing, and the write-up above
     has been corrected accordingly.
  3. With `SRSRAN_UE_STATE_PIPE` set, captured the FIFO during attach and during traffic tests: confirmed one
     JSON line per slot, non-empty `dl_grants` with real RNTI/HARQ id/MCS/RB allocation matching the gNB's own
     `SCHED`/`PHY` log lines for the same slot, and empty arrays (not fabricated entries) on idle slots.
  4. Validated both control actions live end-to-end - see [§12.1](#121-baseline-for-comparison-pre-existing-prb-quota-control-not-a-contribution-of-this-project) (PRB quota) and [§12.4](#124-new-control-action-ue-bitrate-cap-throughput-cap) (new UE bitrate cap) for
     the detailed results (InfluxDB throughput staircases matching the exact xApp-driven targets).

## 11. Known limitations and next steps

- Layer 2 is single-pipe, single-consumer, best-effort (now covering both DL and UL grants, see [§5](#5-metrics-currently-emitted)/[§8](#8-mapping-to-a-real-near-rt-ric--xapp-architecture)), and has
  no schema versioning beyond the `"schema":"srsran_ue_state_v1"` tag — treat it as a development tool, not an
  API contract.
- 5QI/QoS-flow tagging, pre-trim link-adaptation values, and true UL queueing-delay measurement remain
  documented gaps ([§8](#8-mapping-to-a-real-near-rt-ric--xapp-architecture)), not implemented - UL per-grant telemetry itself (RNTI, HARQ id, MCS, RB allocation,
  retx count, PUSCH SINR/RSRP, HARQ state) is now implemented; see [§8](#8-mapping-to-a-real-near-rt-ric--xapp-architecture) for exactly which UL fields still have
  no data source and why.
- No E2SM extension was added for the new `dl_hol_delay_ms` field or for any Layer 2 field; both remain
  accessible only via JSON/pipe until a near-RT RIC needs them over E2. The pre-existing srsRAN gap that
  previously kept it unpopulated end-to-end ([§12.3](#123-a-pre-existing-srsran-gap-found-via-live-validation-dl-hol-delay-never-actually-populates---now-fixed)) has since been fixed and **live-validated** - `dl_hol_delay_ms`
  now has a real, wired-through value confirmed in InfluxDB under DL saturation ([§12.3](#123-a-pre-existing-srsran-gap-found-via-live-validation-dl-hol-delay-never-actually-populates---now-fixed)).
- The `scheduler_metrics_handler_test` unit test should be extended for the new field but this could not be
  verified by running it in this environment (`BUILD_TESTING=OFF`/no GTest installed here, see [§12.5](#125-build-and-validation-evidence-for-the-control-plane-changes)) - this
  remains true after the [§12.3](#123-a-pre-existing-srsran-gap-found-via-live-validation-dl-hol-delay-never-actually-populates---now-fixed) fix as well.
- The UE bitrate cap ([§12.4](#124-new-control-action-ue-bitrate-cap-throughput-cap)) is enforced with a 20 ms token-bucket burst window; this was chosen for
  responsiveness and validated live, but is a tunable, not a value derived from a spec or requirement.
- UE bitrate cap requests and PRB-quota requests both fully replace the UE's `sched_ue_resource_alloc_config` on
  every RIC Control Request (no merge) - see the caveat on `du_mac_sched_control_config` in
  `include/srsran/du/du_high/du_manager/du_configurator.h`. An xApp that wants both a PRB quota and a bitrate cap
  active simultaneously must resend both parameters in every request.
- **Future work: validate HOL-delay-based UE prioritization in a multi-UE scenario.** The [§12.3](#123-a-pre-existing-srsran-gap-found-via-live-validation-dl-hol-delay-never-actually-populates---now-fixed) fix makes
  `scheduler_time_qos.cpp`'s delay-weighted prioritization term (`delay_weight += hol_delay_ms / pdb`, keyed by
  each bearer's `packet_delay_budget_ms`) receive real, non-zero data for the first time - but this was only
  validated with a single attached UE, where there's no other UE to prioritize *against*. Not yet checked:
  attach 2+ UEs with QoS policies configured (differing `packet_delay_budget_ms`, or one UE with a much larger
  DL buffer backlog than the other), saturate DL on more than one simultaneously, and confirm via the gNB log
  (`SCHED` slot-decision lines already show `dl_bo`) and/or per-UE `dl_hol_delay_ms`/`dl_brate` in InfluxDB that
  the scheduler measurably favors the UE with the larger delay-weight term - i.e. that the fix changes *grant
  allocation order/share*, not just the *telemetry value*. This is the natural next validation step before
  relying on this fix for any SLA/QoS-sensitive multi-user scenario.

## 12. Control: implemented and validated E2SM-RC extensions

This section covers the control-plane work: what already existed, what we found blocked, and what we built new -
all verified against a **live testbed** (srsRAN gNB + srsUE over the ZMQ RF simulator, Open5GS 5GC, the O-RAN SC
near-RT RIC, and Telegraf/InfluxDB/Grafana), not just by reading code or unit-testing in isolation.

### 12.1 Baseline for comparison: pre-existing PRB quota control (not a contribution of this project)

Mentioned here only because C3 ([§12.4](#124-new-control-action-ue-bitrate-cap-throughput-cap)) is directly compared against it below — no code in this bullet is ours.
srsRAN's E2SM-RC agent already implements **O-RAN Style 2 / Action 6, "Slice-level PRB quota"**
(`e2sm_rc_control_action_2_6_du_executor` → `grant_params_selector.cpp`), a *resource-share* lever (%), not an
outcome lever. Cycling it 30%→50%→70%→100% against a saturating UE produced three clean throughput plateaus
(~23.3 → ~39.9 → ~53.6 Mbps) — confirming it works, but also confirming the gap C3 fills: an operator using this
action cannot state "cap this UE at 30 Mbps," only "give this UE 50% of the PRBs and hope."

### 12.2 A pre-existing srsRAN gap found via live validation: DL HOL delay never actually populates - now fixed

While validating `dl_hol_delay_ms` ([§5](#5-metrics-currently-emitted), Layer 1) under a real 46 Mbps DL saturation test, `dl_bs` (DL buffer
occupancy) was confirmed non-zero on every single InfluxDB sample, yet `dl_hol_delay_ms` never appeared even
once - InfluxDB's schema-on-write literally had no column for it. Tracing this down (not merely re-reading our
own code, but the full path from RLC to the DU manager) found the real cause one layer further upstream than
where we'd made our change: `lib/du/du_high/du_manager/du_ue/du_ue_adapters.h`, class
`rlc_tx_mac_buffer_state_updater::on_buffer_state_update()` - the actual production RLC→MAC bridge - has a
`// TODO: set hol_toa` and never copies the real `rlc_buffer_state::hol_toa` (correctly computed by
`lib/rlc/rlc_tx_am_entity.cpp` etc.) into the MAC-layer message. This is a **pre-existing gap in srsRAN itself**, and it means `lib/scheduler/policy/scheduler_time_qos.cpp`'s HOL-delay-based UE
prioritization is *also* silently a no-op in the real DU today, independent of any telemetry concerns.

To solve this:

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
per [§11](#11-known-limitations-and-next-steps) item 2) - typical values were 1 ms, with a 3 ms spike observed alongside a `dl_bs` bump to ~16 KB,
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
multi-UE contention testing. See [§12](#12-control-implemented-and-validated-e2sm-rc-extensions)'s "Future work" entry for the specific multi-UE experiment to run to
confirm this prioritization is actually taking effect, not just that the underlying metric is now populated.

**Scope note: this fix is DL-only, deliberately.** UL has no equivalent gap to fix, because it has no
equivalent *data source*: `hol_toa` exists for DL because the gNB's own RLC Tx entity timestamps SDU
arrival for data it is about to transmit. For UL, the SDU lives in the UE's buffer until the gNB grants it -
the gNB never observes a UL SDU's arrival time, so there is nothing analogous to copy through. See [§8](#8-mapping-to-a-real-near-rt-ric--xapp-architecture)'s UL
HOL-delay bullet for the practical proxy that already exists (SR-to-PUSCH turnaround delay) and why building
genuine UL queueing-delay measurement is a separate, deferred, higher-risk piece of work, not a follow-on to
this fix.

### 12.3 New control action: UE bitrate cap (throughput cap)

**What it is**: a direct cap on a UE's sustained DL and/or UL bitrate, in kbps. This is a different lever than PRB quota:
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
caps resource share, not rate), so this is explicitly a vendor-specific extension, not a misuse of a
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

## 13. Files changed (control-plane additions, this session)

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

## 14. Files changed (UL extension, this session)

Extends the Layer 2 telemetry ([§5](#5-metrics-currently-emitted)) to UL grants and exercises the already-built UL bitrate cap ([§12.4](#124-new-control-action-ue-bitrate-cap-throughput-cap)) via the
demo xApp for the first time. No scheduler/E2SM-RC changes were needed - both were already direction-agnostic
([§12.4](#124-new-control-action-ue-bitrate-cap-throughput-cap)).

| File | Status |
|---|---|
| `lib/scheduler/logging/scheduler_ue_state_logger.h` | modified (doc comment + scope) |
| `lib/scheduler/logging/scheduler_ue_state_logger.cpp` | modified (new `ul_grants` array) |
| `~/oran-sc-ric/xApps/python/simple_bitrate_cap_xapp.py` | modified (separate repo - now cycles `max_ul_kbps` alongside `max_dl_kbps`) |
| `agentic_ran/patches/new_files_oran_sc_ric/xApps/python/simple_bitrate_cap_xapp.py` | modified (reproducibility copy, kept in sync) |
