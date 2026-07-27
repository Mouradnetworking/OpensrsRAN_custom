# MFBR Enforcement — Investigation, Root Cause, and Fix

**Date:** 2026-07-09 / 2026-07-10
**Status: UL RESOLVED, DL NOT SAFE FOR USE.** UL MFBR enforcement (O-RAN E2SM-RC DRB QoS Configuration,
Style 1/Action 1) is confirmed working end to end, live-tested under sustained soak, and covered by new
regression tests. **DL MFBR enforcement's core clamp logic is confirmed correct at a single cap value, but
reconfiguring an active DRB's DL cap a second time (e.g. changing the cap value, or removing it) reliably
crashes the connection (RLF, forced UE Context Release) — root cause not yet found.** Until that is resolved,
**do not use the DL side of this feature** (`max_dl_kbps` in `control_drb_qos()`) outside of a single, one-shot,
non-repeated control action per UE session. This document supersedes the earlier "RESOLVED" status recorded for
both directions on 2026-07-10, which turned out to be premature for DL — see §2.3 for the correction and what's
known so far.

---

## 1. Executive summary

| Capability | Status |
|---|---|
| DL MFBR enforcement (Style 1/Action 1) | **NOT SAFE FOR USE.** Core clamp logic is correct (verified: 0.482 Mbps delivered against a 500 kbps cap, tight convergence, zero failures over 60s). But a *second* DL reconfiguration on the same DRB (different cap value, or removing the cap) reliably crashes the connection within 5–15 seconds — reproduced twice, once after a period of prior traffic and once on a freshly-restarted UE with no warm-up. Root cause not found; see §2.3. |
| UL MFBR enforcement (Style 1/Action 1) | **Fixed and confirmed.** Delivered throughput converges to the configured cap at multiple values, including a low binding cap (300 kbps → 237–260 kbps), a non-binding cap (8000 kbps → ~4.18 Mbps matching offered load), and a 3-minute sustained soak (3000 kbps → 2.79 Mbps, flat, zero RLF). Repeated/sequential reconfiguration of the same DRB (apply cap → observe → remove cap) has also been exercised without incident. |
| PRB/physical-capacity ceiling (uncapped, above-real-capacity case) | Working, unaffected by these fixes. |
| PRB quota (Style 2/Action 6) | Working, regression-confirmed after both fixes — no change in behavior. |
| UE aggregate bitrate cap (Style 2/Action 201) | Working, regression-confirmed after both fixes — no change in behavior. **Not implicated in the DL crash** — it's a different, lighter-weight mechanism (aggregate scheduler-side limiter) that doesn't drive the same NGAP→E1AP→F1AP→RRC Reconfiguration chain that Style 1/Action 1 does; it was cycled repeatedly (5/15/30 Mbps) with no failures. |
| `srsue` stability fixes (separate, `srsRAN_4G` repo, same overall session, still uncommitted) | Implemented, compiling, live-verified individually. Confirmed not implicated in the DL crash — audited line-by-line, see §2.3. |
| Scheduler unit test suites (`sched_ue_context_test`, `scheduler_test`) | 50 + 95 tests, 100% passing, including 3 new DL MFBR regression tests added in this session. |
| Temporary diagnostic logging | **Still present, not yet removed** (`grant_params_selector.cpp`'s UL `DIAG` line, `logical_channel_system.cpp`'s `DIAG-DL`/`DIAG-DL-ALLOC` lines) — all gated behind `is_capped()`, so zero effect on uncapped UEs, but pending cleanup once the DL crash is resolved (removing it now would lose visibility mid-investigation). |

---

## 2. DL MFBR — root cause and fix

### 2.1 Root cause

`grant_params_selector.cpp`'s DL new-tx path (`get_dl_sched_context()`) sized the PDSCH transport block using
only the *aggregate* per-UE `ue_cc.dl_bitrate_limiter()` (the limiter Style 2/Action 201 configures) — it never
consulted the *per-DRB* `mbr_limiter` that the DRB QoS xApp (Style 1/Action 1) actually sets. That per-DRB cap
was only enforced one stage later, in `allocate_mac_sdu()`, which limits how much of an *already-sized* TBS gets
filled with real data. Since the TBS size itself never shrank to match the cap, but only cap-rate bytes actually
got consumed from the DRB's queue, the real backlog kept growing (arrival > enforced drain rate), and each
subsequent slot's TBS-sizing request grew with it — a runaway feedback loop that escalated PRB/MCS allocation
toward the cell's maximum over ~15–20s, mostly as wasted padding, until it triggered a HARQ-retransmission
failure storm and a forced UE Context Release.

Confirmed via `git log` this was not a regression: DL MFBR never had this clamp since it was first introduced
(commit `66b57dc40c`); a later commit (`2f186679bc`, "extend Layer 2 telemetry and bitrate-cap xApp to UL") added
the equivalent second clamp only to the UL branch of the same function, and the DL side was never backported.

### 2.2 Fix

Mirrors the UL branch's already-correct two-clamp pattern:

1. `lib/scheduler/ue_context/logical_channel_system.h` / `.cpp`: new `dl_pending_bytes_mfbr_capped(slot_point)`,
   mirroring the existing `ul_pending_bytes_mfbr_capped()` one-for-one — sums each active DRB's
   `min(pending_bytes(lcid), mbr_limiter.get_max_grantable_bytes(sl_tx))`.
2. `lib/scheduler/slicing/slice_ue_repository.h`: new `slice_ue::pending_dl_newtx_bytes_mfbr_capped(slot_point)`
   wrapper, mirroring `pending_ul_newtx_bytes_mfbr_capped()`.
3. `lib/scheduler/ue_scheduling/grant_params_selector.cpp`: `get_dl_sched_context()`'s NewTx branch now also
   clamps `pending_bytes` by this new per-DRB value before sizing the TBS/PRB allocation.

**Live validation:** sustained ≥15–20s DL load clearly above a configured cap; granted PDSCH TBS converges to
and stays near the cap; no HARQ storm, no RLF.

**Unit tests added** (`tests/unittests/scheduler/ue_context/logical_channel_system_test.cpp`):
`dl_pending_bytes_mfbr_capped_is_a_no_op_when_no_mfbr_is_configured`,
`dl_pending_bytes_mfbr_capped_clamps_large_backlog_to_the_mfbr_budget`,
`dl_pending_bytes_mfbr_capped_does_not_clamp_a_small_backlog`.

### 2.3 Correction (2026-07-10, later same day): reconfiguration crash found — DL is NOT fully resolved

The §2.2 fix and its live validation are still accurate as far as they go — they confirm the *clamp logic itself*
is correct once a cap is applied. What §2.1/§2.2 did **not** test was applying a *second* DL reconfiguration to
the same DRB (a different cap value, or removing the cap). That scenario was tested afterwards and reliably
crashes the connection:

- **First occurrence**: cap changed 500 kbps → 5000 kbps after ~100s of prior 500 kbps traffic. UL PUSCH began
  failing (CRC=KO, erratic/degenerate SINR readings) ~200ms after the RRC Reconfiguration completed, cascading
  into HARQ retransmission exhaustion across multiple processes and a full RLF (`UEContextReleaseCommand`, `F1 UE
  context removed`) within ~5 seconds. The UE's own log showed `Couldn't allocate PDU in decode_pdsch_dl()` /
  `pdu_get_nolock()` — its `byte_buffer_pool` (fixed 4096 buffers) was exhausted.
- **Second occurrence**: same cap change, but on a **freshly-restarted UE process with no prior traffic** — crash
  recurred within **15 seconds**. This disproves "slow leak from sustained high-frequency grants" as the sole
  explanation, since a fresh process shouldn't have had time to leak 4096 buffers in 15s.

**Hypotheses investigated and ruled out** (don't re-check these — confirmed via direct code reading):
- DRB re-created with a new LCID on reconfigure (would explain SN/state desync) — ruled out; `du_bearer_resource_manager.cpp`'s `modify_drbs()` correctly updates the existing DRB's config in place for an already-active bearer, doesn't go through the LCID-(re)allocating `setup_drbs()` path.
- Forced PDCP/RLC re-establishment on every reconfig — ruled out; `pdu_session_routine_helpers.cpp`'s `fill_rrc_reconfig_args()` has an explicit guard (`pdcp_and_sdap_reconfig_required`) that omits a DRB from `drb-ToAddModList` entirely when nothing PDCP/SDAP-relevant changed, which is the case for an MFBR-only update.
- Buffer pool exhaustion as the *sole*/*initiating* cause — the symptom is real (confirmed in the UE log) but doesn't explain the 15-second fresh-process crash on its own; more likely a downstream symptom of something else going wrong first, not the root trigger.

**Leading unexamined hypothesis, found during a later documentation-driven code audit** (not yet tested): a
comment in `srsue`'s `rrc_nr.cc` (`apply_rlc_add_mod()`, part of the separate, already-uncommitted `srsue`
stability-fix set — see §5) states that `rlc->add_bearer()` fails when `lc_ch_id` already exists, "because
in-place RLC reconfiguration isn't supported" on the UE side — and that this is exactly what happens "on a
QoS-only reconfiguration of an already-active DRB". That is precisely our scenario: DRB1 already exists (from
initial PDU session setup) and DL MFBR reconfiguration is a QoS-only change to it. If the UE's RLC entity isn't
actually being reconfigured on a second control action (only a warning logged, per the fix now in place), the
gNB and UE may end up with inconsistent bearer state after the second reconfiguration — a plausible root cause
for the observed SN/state desync-like symptoms. **Not yet confirmed** — next step is checking the `srsue` log for
`"RLC reconfiguration for lcid=%d was not applied (bearer already exists)"` at the moment a second DL
reconfiguration is applied, to see if it fires.

**Current guidance until this is resolved**: treat DL MFBR (`max_dl_kbps`) as safe for a single, one-shot control
action per UE session only. Do not reconfigure the same DRB's DL cap a second time (different value or removal)
in the same session. UL MFBR (`max_ul_kbps`) has no such restriction — repeated/sequential UL reconfiguration of
the same DRB has been exercised without incident (see §3).

---

## 3. UL MFBR — root cause and fix

UL required two independent, layered fixes; the second is the more architecturally significant one.

### 3.1 Root cause, layer 1: BSR staleness under tight, exact-fit grants

Unlike DL, the gNB has no ground truth of a UE's real UL backlog — it relies entirely on the UE's own Buffer
Status Reports (BSR). Live instrumentation (`pending_ul_newtx_bytes()`'s internal breakdown) showed the
scheduler correctly computing the MFBR token-bucket budget, but repeatedly re-granting the *exact same* stale
residual backlog value, because:

- TS 38.321's **Regular BSR** only re-triggers when new data arrives at a *previously empty* queue — not while a
  continuously-fed queue stays non-empty.
- **Periodic/Padding BSR**, the standards-compliant fallback for exactly this situation, requires an *existing
  grant* to be transmitted at all.
- Once an MFBR cap made grants small enough to exactly fit the currently-known backlog byte-for-byte
  (`byte_len` payload leaving zero bytes of the TBS unused, confirmed via a full subPDU trace showing no
  `SBSR:`/`PAD:` entries at all in the tightest grants), there was no leftover space for a Padding BSR either.

Net effect: the gNB's backlog estimate froze at whatever the last BSR reported, and kept re-granting that same
(stale) amount indefinitely instead of tracking real, continuously-arriving traffic.

**Fix** (`lib/scheduler/ue_context/logical_channel_system.cpp`,
`ue_logical_channel_repository::ul_pending_bytes_mfbr_capped()`): whenever an LCG is MFBR-capped, add a small
(4-byte) request headroom, plus (in `grant_params_selector.cpp`'s UL branch) a guaranteed +1 PRB of allocation
headroom beyond the byte-exact size when the LCG is MFBR-capped (`has_ul_mfbr_cap()`, new method) — a few extra
requested bytes alone weren't always enough to change the selected PRB count, especially at low MCS where a
single PRB's TBS ceiling is itself tiny (e.g. 19 bytes). Both are no-ops for any LCG without a configured MFBR
cap.

### 3.2 Root cause, layer 2 (the deeper one): reactive SR/BSR scheduling can't sustain a GBR target

Fixing layer 1 alone was **not sufficient** — live re-testing still showed throughput stuck at a fraction of the
cap, with grants collapsing to a fixed periodic cadence matching the cell's configured SR-PUCCH-Resource
periodicity (confirmed via config inspection: `sr_period_msec = 20.0` default, `common_scs: 15` → 1 slot/ms,
consistent with the observed SR-PUCCH opportunity spacing). Two prior hypotheses (H1: UE stuck in scheduler
fallback mode; H2: `ul_logical_channel_group_context::active` flag stuck false) were both live-instrumented and
**disconfirmed** — fallback cleared in ~30ms via the normal `rrcReconfigurationComplete` round trip, and the
active flag correctly stayed true throughout.

The actual mechanism: `intra_slice_scheduler.cpp`'s outer candidate-eligibility filter
(`get_ues_with_pending_newtx_data` → `logical_channel_system::get_ues_with_ul_pending_data`) excludes a UE from
even being offered to `create_newtx_ul_candidate()` whenever its tracked backlog bit reads zero. Once layer 1's
fix let a grant fully drain the known backlog, this bit correctly went to zero — but with it, the UE became
**entirely invisible to the scheduler** until either its own SR/BSR logic re-signaled (bounded by the cell's SR
periodicity, designed for best-effort responsiveness, not GBR guarantees) or new data happened to arrive. This is
a fundamental mismatch: **a purely reactive SR/BSR-driven scheduler cannot sustain a smooth GBR/MFBR target**;
real deployments solve this with Configured Grants (TS 38.321 Type 1/2, bypassing SR/BSR entirely) or by
proactively scheduling GBR flows independent of SR.

**Fix** — the lightweight, scheduler-side analogue of a Configured Grant, scoped strictly to MFBR-capped LCGs:

1. `lib/scheduler/ue_context/logical_channel_system.h` / `.cpp`: new
   `ue_logical_channel_repository::has_ul_mfbr_cap()` — whether any active UL LCG for the UE has a configured
   MFBR cap.
2. `lib/scheduler/ue_context/logical_channel_system.cpp`, `configure()`: when a UL MFBR cap is applied to an
   LCG, immediately force that UE's slice-level `pending_ul_ues` bit to `true`, independent of real backlog.
3. Same file, `on_single_lcg_buf_st_update()`: on a backlog zero-crossing, only let the bit go back to `false`
   if *no* LCG on the UE is still MFBR-capped — preventing a legitimate backlog-drain event from re-hiding an
   MFBR-capped UE from the scheduler.
4. `lib/scheduler/ue_scheduling/intra_slice_scheduler.cpp`, `create_newtx_ul_candidate()`: when the (now
   correctly non-zero-forced) candidacy check still resolves `pending_bytes == 0` for an MFBR-capped LCG, don't
   bail out — offer a small (16-byte) probe grant instead, guaranteeing the UE an immediate opportunity every
   slot to report an updated buffer status or send real data, rather than waiting on SR.

This set of changes is intentionally scoped to only fire for LCGs with an explicitly configured MFBR cap
(`has_ul_mfbr_cap()` gates every one of them) — a UE with no GBR/MFBR configuration sees zero behavior change,
same as the DL fix.

**Live validation:** UL cap of 3000 kbps against a genuine, sustained 4 Mbps offered UL load (well above the
cap, so it has to bind) — **2.47 Mbps average / 2.61 Mbps steady-state**, sustained for the full 88-second test
window, zero HARQ-triggered RLF, zero forced UE Context Release, gNB and UE both stable throughout.

---

## 4. Regression testing performed after both fixes

- **PRB quota (Style 2/Action 6)**: `simple_rc_xapp.py` against the DU E2 agent — 5 successful
  `MAC UE Reconfiguration` cycles, no crash, no change in behavior.
- **UE aggregate bitrate cap (Style 2/Action 201)**: `simple_bitrate_cap_xapp.py` against the DU E2 agent —
  clean cycling through 5/15/30 Mbps, successful reconfiguration each time, no crash.
- **Scheduler unit tests**: `ctest -R 'sched_ue_context_test|^scheduler_test$'` — 50 + 95 tests, 100% passing.
  This required first fixing four pre-existing, unrelated broken `allocate_mac_sdu()` call sites in
  `logical_channel_system_test.cpp` (a stale test signature mismatch dating back to when the MFBR feature was
  first introduced, never caught before because `BUILD_TESTING` was off in this environment until this session).
  `scheduler_test` includes `scheduler_ue_fallback_mode_test.cpp` (directly exercises the fallback path touched
  by the H1 investigation) and RB-fairness distribution tests across up to 512 simulated UEs
  (`jain_rb_fairness`/`jain_brate_fairness` ~0.997–1.0 throughout) — confirming the new UL eligibility-bitset
  change didn't introduce a fairness regression in multi-UE scenarios.

---

## 5. Confirmation: scope and safety of all changes

- **No source changes outside the six files listed in §2.2/§3.1/§3.2**, all within `lib/scheduler/`. No changes
  to NGAP, F1AP, RRC, or PHY code.
- **Every fix is gated on an explicit MFBR cap being configured** (`is_capped()`/`has_ul_mfbr_cap()` checks
  throughout) — a UE or DRB with no GBR/MFBR QoS configuration exercises none of this new code and sees zero
  behavior change, verified by the regression tests in §4 and the new unit tests' explicit
  "is a no-op when no MFBR is configured" cases.
- **Correction, 2026-07-10 later same day**: the original H1/H2 UL diagnostic logging *was* removed as stated
  above, but **new diagnostic logging was subsequently added** while investigating the DL reconfiguration crash
  (§2.3) and is **still present**: one `DIAG` line in `grant_params_selector.cpp` (UL, gated on
  `has_ul_mfbr_cap()`) and two lines in `logical_channel_system.cpp` (`DIAG-DL` in
  `dl_pending_bytes_mfbr_capped()`, `DIAG-DL-ALLOC` in `allocate_mac_sdu()`, both gated on `mbr_limiter.is_capped()`).
  All three are read-only (no state mutation), gated to fire only for MFBR-capped UEs, and pending removal once
  §2.3 is resolved — kept for now since removing them would lose visibility mid-investigation.
- **`srsue` (`srsRAN_4G`) changes, all uncommitted, audited line-by-line on 2026-07-10** (in response to a direct
  question about whether any change made across this project risks altering normal, non-MFBR 5G behavior):
  - `lib/src/rlc/rlc_um_nr.cc`: one-line fix, corrected a wrong variable read inside an already-existing error-log
    statement (`rx_sdu`→`pdu.sdu`). Doesn't change any decision logic, only what gets printed.
  - `srsue/{hdr,src}/stack/mac_nr/ul_harq_nr.{h,cc}`: adds a `pthread_rwlock_t` around UL HARQ process state
    (read-locks on `new_grant_ul()`/`get_current_tbs()`, write-locks on `reset()`/`reset_ndi()`/`set_config()`).
    Universal (not MFBR-gated), but purely a concurrency-safety fix — protects against a data race, doesn't change
    any scheduling/retransmission decision.
  - `srsue/{hdr,src}/stack/mac_nr/demux_nr.{h,cc}`: bounds the PHY→Stack PDU handoff queue to 512 items and
    switches from a blocking push to a non-blocking `try_push()` that drops-and-logs when full, instead of growing
    without limit. Universal, but only changes behavior once the Stack thread is already dangerously behind —
    trades an eventual uncontrolled crash for controlled, visible data loss. Part of the same buffer-pool-leak
    investigation as §2.3's open lead; evidently did not fully close it (see §2.3 — a fresh buffer-pool exhaustion
    was found again in this session, at a different allocation site than this queue).
  - `srsue/src/stack/mac_nr/mux_nr.cc`: today's UL BSR-reservation fix (see §3.1's fix description). Universal
    (applies to all UL PDU construction, not gated), small (2 bytes), standards-motivated.
  - `srsue/{hdr,src}/stack/rrc_nr/rrc_nr.{h,cc}`: implements previously-missing `leave_connected()`, called both
    from `rrc_release()` (refactor, no behavior change) and newly from `ra_problem()` in standalone-NR mode (was a
    bare `// TODO: handle RA problem` — a repeated Random Access failure previously left the UE silently stuck
    believing it was connected; now it correctly tears down and can recover). Also adds warning logs when
    `rlc->add_bearer()` / `pdcp->add_bearer()` silently fail to apply a reconfiguration to an already-active
    bearer — see §2.3's leading hypothesis, this is what would need to fire to confirm it.
- Full gNB-side diff: `lib/scheduler/{slicing/slice_ue_repository.h, ue_context/logical_channel_system.{h,cpp},
  ue_scheduling/{grant_params_selector.cpp, intra_slice_scheduler.cpp}}` +
  `tests/unittests/scheduler/ue_context/logical_channel_system_test.cpp` — 6 files, ~194 insertions / 13
  deletions, all gated as described above.

---

## 6. Investigation history (for context)

This document previously recorded UL MFBR as a "known limitation" after H1/H2 were proposed as hypotheses but
not yet confirmed, and after an initial BSR-headroom-only fix (§3.1) proved necessary but insufficient on its
own. The deeper root cause (§3.2) was only found by re-testing with live instrumentation after that first fix,
observing that grant frequency — not grant size — was now the binding constraint, and tracing it back through
`intra_slice_scheduler.cpp`'s outer eligibility gate. That full investigation trail (including the disconfirmed
H1/H2 hypotheses and the reasoning that ruled each one out) is preserved in this repository's conversation
history for anyone extending this work; this document reflects only the final, confirmed state.
