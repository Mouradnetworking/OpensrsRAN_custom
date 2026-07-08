# Per-DRB/Per-QoS-Flow GBR MFBR Enforcement

> This document is scoped to a second, distinct control capability layered on top of the same srsRAN + O-RAN SC
> RIC testbed described in [`README.md`](./README.md) and
> [`AGENTIC_RAN_DEVELOPMENT_AUDIT.md`](./AGENTIC_RAN_DEVELOPMENT_AUDIT.md). Those documents cover the **first**
> control action built in this project: a UE-aggregate DL/UL bitrate cap (E2SM-RC Style 2, vendor action ID 201).
> This document covers the **second, later** one: standards-based enforcement of a DRB/QoS-flow's actual 3GPP
> GBR QoS profile (5QI + Maximum Flow Bit Rate), controllable both the standard way (NGAP/PCF-driven QoS
> modification) and via a new, standards-compliant E2SM-RC control action (Style 1, Action 1). The two features
> are complementary, not competing - see §D for how they relate.
>
> For the system-level view of this second capability - architecture, protocol propagation hop-by-hop, use
> cases, comparison with the other actions, and a from-scratch validation runbook - see
> [`DRB_QOS_XAPP_SYSTEM_DESIGN.md`](./DRB_QOS_XAPP_SYSTEM_DESIGN.md). This document stays focused on the
> standards positioning and the three-bug deep-dive found while validating it live.

## A. What this is and why it exists

srsRAN's scheduler already accepts a UE's GBR QoS profile (5QI, Maximum Flow Bit Rate DL/UL, Guaranteed Flow Bit
Rate DL/UL) end-to-end through NGAP -> CU-CP -> F1AP -> DU at bearer setup time: the types are all there
(`gbr_qos_flow_information` in `include/srsran/ran/qos/qos_parameters.h`) and are used for **admission**
decisions and QoS-aware scheduling *weighting* (`scheduler_time_qos.cpp`). What it did **not** do before this
work is *enforce* the Maximum Flow Bit Rate (MFBR) as a hard cap on a specific DRB's granted bytes - the
existing bitrate-cap mechanism (§ in `README.md`) caps the whole UE, not a single DRB, and it is driven by an
out-of-band RIC decision (kbps chosen by an xApp), not by the UE's actual negotiated GBR QoS profile.

This gap matters for two reasons:
1. **Standards fidelity.** A 5QI=1 (Conversational Voice) or similar GBR flow's MFBR is a QoS *contract* signaled
   over NGAP from the AMF/PCF, not a value an operator picks by hand. Without enforcement, a misbehaving or
   simply best-effort-scheduled UE could exceed its negotiated GBR flow's MFBR indefinitely if it happened to
   share cell capacity favorably, breaking the guarantee the QoS profile is supposed to represent.
2. **RIC control granularity.** The existing bitrate cap is a *vendor* E2SM-RC action (Style 2, Action ID 201) -
   useful, but not something a third-party xApp built against the O-RAN spec (rather than against srsRAN
   specifically) could discover or use. Exposing the *standard* "DRB QoS Configuration" control action (Style 1,
   Action 1) lets any spec-compliant xApp reconfigure a DRB's QoS - including its MFBR - the same way it would
   against any other vendor's E2 node.

## B. Architecture and the five phases

```
                                    ┌───────────────────────────────┐
                                    │        near-RT RIC / xApp      │
                                    │  drb_qos_cap_xapp.py            │
                                    │  control_drb_qos() (Style1/Act1)│
                                    └───────────────┬────────────────┘
                                                     │ E2AP RIC Control (Style 1, Action 1)
┌───────────────────────────── gNB ──────────────────┼──────────────────────────────────────┐
│                                                     ▼                                      │
│   ┌─────────────┐   Style1/Act1   ┌────────────────────────────┐                           │
│   │  E2 Agent   │────────────────▶│ e2sm_rc_control_action_1_1_ │  Phase 4                  │
│   │  (CU-CP)    │                 │      cu_executor            │                          │
│   └─────────────┘                 └──────────────┬─────────────┘                           │
│                                                    ▼                                        │
│                                    ┌────────────────────────────┐                           │
│  AMF/PCF ──NGAP PDU Session────────▶  cu_configurator_impl::     │                          │
│  Resource Modify (standard path)   │  trigger_qos_flow_          │                          │
│                                    │  modification()             │                          │
│                                    └──────────────┬─────────────┘                           │
│                                                    │ cu_cp_pdu_session_resource_modify_request│
│                                                    ▼                                        │
│                          ┌─────────────────────────────────────────┐                        │
│                          │  pdu_session_resource_modification_      │  (pre-existing routine)│
│                          │  routine (unchanged)                     │                        │
│                          └──────────┬───────────────────┬──────────┘                        │
│                                     │ E1AP                │ F1AP                             │
│                                     ▼                     ▼                                 │
│                     ┌───────────────────────┐   ┌─────────────────────────┐                  │
│                     │  CU-UP:                │   │  DU-Manager:             │                 │
│                     │  pdu_session_manager_   │   │  du_bearer_resource_     │                 │
│                     │  impl::modify_pdu_       │   │  manager::modify_drbs   │  Phase 2        │
│                     │  session()  Phase 3      │   └────────────┬────────────┘                 │
│                     └───────────────────────┘                │                                │
│                                                                 ▼                               │
│                                                   ┌─────────────────────────────┐               │
│                                                   │  MAC Scheduler:               │              │
│                                                   │  logical_channel_system::     │  Phase 1      │
│                                                   │  configure() + per-DRB/LCG    │              │
│                                                   │  ue_bitrate_limiter enforcement│             │
│                                                   └─────────────────────────────┘               │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
```

The actual **enforcement** always happens in exactly one place (the DU scheduler, Phase 1); Phases 2-5 are all
different ways of getting an updated MFBR value *into* that one enforcement point - either through the standard
NGAP path (Phases 2+3, which any core network already exercises when it sends a PDU Session Resource Modify) or
through the new RIC control path (Phases 4+5). Phase 0 (not code - see §F) is testbed/subscriber setup.

### Phase 0 - Testbed: GBR subscriber

A GBR subscriber (IMSI `001010000059478`, 5QI 1) was added to Open5GS via `add_users.py`, with a matching UE ZMQ
config. Not a code change; recorded here only because the rest of this feature is meaningless to test without a
real GBR-QoS UE attached. Note: the current testbed's `ru_sdr.device_args` defines a single ZMQ TX/RX port pair,
so it supports one attached UE at a time - the GBR UE must be swapped in (detach/attach), not run concurrently
with another UE.

### Phase 1 - Scheduler enforcer (DL exact, UL LCG-scoped)

Files: `lib/scheduler/ue_context/logical_channel_system.{h,cpp}`, `lib/scheduler/slicing/slice_ue_repository.h`,
`lib/scheduler/ue_scheduling/{grant_params_selector,ue_cell_grid_allocator,ue_fallback_scheduler}.cpp`.

The existing `ue_bitrate_limiter` (a token bucket already used for the UE-aggregate cap, see `README.md`) is
reused - not reimplemented - as a new per-DRB (DL) / per-LCG (UL) member, because it was already a generic,
direction-agnostic rate limiter with no dependency on being scoped to a whole UE.

- **`configure()`** now sets/clears each limiter's cap from the DRB's existing
  `gbr_qos_flow_information::max_br_dl`/`max_br_ul` (already flowing in via F1AP/NGAP; no new IE, no new storage
  field) every time a DRB is (re)configured. This is also the entry point Phase 2's DU-manager QoS-update code
  drives - a dynamic MFBR change (whether triggered by NGAP or by the RIC) simply calls the *same*
  `configure()` a normal DRB setup would, needing no separate "update" API.
- **DL** (`allocate_mac_sdu` -> `allocate_mac_sdus` -> `build_dl_transport_block_info`/
  `build_dl_fallback_transport_block_info`): `slot_point` was threaded through this call chain so the per-DRB
  limiter can be consulted (`get_max_grantable_bytes`) and updated (`consume`) at the exact point PDSCH subPDU
  bytes are allocated to a logical channel. This composes automatically with the pre-existing UE-aggregate cap
  in `grant_params_selector.cpp`, since both are just successive `std::min()` clamps on the same `rem_bytes`.
- **UL** (`handle_ul_grant`, `ul_pending_bytes_mfbr_capped`, consulted from `get_ul_sched_context` in
  `grant_params_selector.cpp`): the limiter is scoped to the **Logical Channel Group (LCG)**, not the DRB. See
  §C for why this is a hard 3GPP limit, not an implementation shortcut.

### Phase 2 - F1AP DRB-modify plumbing (DU side)

Files: `include/srsran/f1ap/ue_context_management_configs.h`, `lib/f1ap/asn1_helpers.cpp`,
`lib/du/du_high/du_manager/ran_resource_management/du_bearer_resource_manager.cpp`.

Added an optional `qos_info` field to `f1ap_drb_to_modify` (wired to/from the existing ASN.1 `qos_info_present`/
`qos_info` IE - a standard F1AP field, not a new one), relaxed `validate_drb_modification_request()` to accept a
modification that changes *only* QoS (previously it required a new UL-TNL-info, i.e. assumed every modification
was a reestablishment), and made `modify_drbs()` update `du_ue_drb_config::qos`/`mac_cfg` from it - reusing the
same `make_gbr_drb_mac_lc_config()`/`make_non_gbr_drb_mac_lc_config()` helpers the DRB-*setup* path already uses,
so no new MAC-config-building logic was needed. This update is picked up by the scheduler through the same
`create_scheduler_ue_config_request()` -> `logical_channel_system::configure()` path as setup, per Phase 1.

### Phase 3 - CU-UP QoS flow modification

File: `lib/cu_up/pdu_session_manager_impl.cpp`.

`modify_pdu_session()`'s per-QoS-flow modification loop was a `// TODO` stub that only logged a warning. It now:
updates the CU-UP-side `qos_flow_context`'s stored 5QI for an existing flow (used for F1-U DSCP marking, *not*
enforcement - CU-UP does not shape traffic in this architecture, DU-scheduler does, per Phase 1), or creates and
wires a new flow onto an already-established DRB (mirroring the DRB-setup path's flow-creation logic), and
reports success/failure per flow via the pre-existing but previously-unused `qos_flow_results` result field.

### Phase 4 - `cu_configurator` extension + E2SM-RC Style 1/Action 1 CU-CP executor

Files: `include/srsran/cu_cp/cu_configurator.h`, `lib/cu_cp/cu_configurator_impl.{h,cpp}`,
`lib/cu_cp/cu_cp_impl.cpp`, `lib/e2/e2sm/e2sm_rc/e2sm_rc_control_action_cu_executor.{h,cpp}`,
`lib/e2/common/e2_cu_cp_factory.cpp`.

This is the new **standards-compliant** control path (see §D for spec citations). `cu_configurator` (the
existing CU-CP-facing interface E2SM-RC CU-side executors use - previously only exposing UE/DU/PCI lookups and
`trigger_handover()`) gained `trigger_qos_flow_modification(ue_index, cu_qos_flow_control_config)`. Its
implementation resolves the UE's `up_resource_manager` DRB context to find the owning PDU session and every QoS
flow currently mapped to the target DRB, builds a `cu_cp_pdu_session_resource_modify_request` (copying every
QoS parameter the RIC did *not* specify from the DRB's current config, so a partial update - e.g. MFBR only -
doesn't clobber unrelated QoS state), and fires it through `cu_cp_ngap_handler::
handle_new_pdu_session_resource_modify_request()` - **the exact same routine an AMF-driven NGAP PDU Session
Resource Modify already uses** (Phases 2+3 are shared, not duplicated, by both trigger paths). Like the existing
`trigger_handover()`, this is fire-and-forget: it schedules the modification on the UE's task executor and
returns "accepted" immediately, without waiting for the F1AP/E1AP/RRC round trip to finish.

A new `e2sm_rc_control_action_1_1_cu_executor` parses the RIC Control message's RAN parameters (see §D) into a
`cu_qos_flow_control_config` and calls the above. It resolves the UE index from the full `gNB-UEID` tuple (AMF
UE NGAP ID + GUAMI + gNB-CU-UE-F1AP-ID) exactly as the existing Style 3/Action 1 (handover) CU executor does -
this is a CU-side action, so (unlike the Style 2 DU-side actions) a DU-local F1AP ID alone isn't sufficient to
resolve the UE. A second `e2sm_control_service` (style ID 1) was registered in `e2_cu_cp_factory.cpp` alongside
the pre-existing style-3 one.

### Phase 5 - xApp / SDK

Files (in `oran-sc-ric`): `xApps/python/lib/e2sm_rc_module.py`, `xApps/python/drb_qos_cap_xapp.py` (new).

`send_control_request_style_1_action_1()` (aliased `control_drb_qos()`) packs the RAN parameters described in
§D and sends the RIC Control Request, following the existing `send_control_request_style_3_action_1()`
(`control_handover()`) pattern for UE identification (full `gNB-UEID`, not `gNB-DU-UEID`). `drb_qos_cap_xapp.py`
is a demo xApp cycling a DRB's DL/UL MFBR through a few steps, mirroring `simple_bitrate_cap_xapp.py`'s
structure.

## C. The UL LCG-sharing limitation (why UL is not exact like DL)

This is a **3GPP protocol property**, not a gap in this implementation, and it is the most important limitation
to understand before relying on this feature for UL:

- **DL**: the gNB constructs the PDSCH transport block itself, subPDU by subPDU, per logical channel (DRB). It
  therefore has perfect, per-DRB visibility and control over exactly how many bytes go to which DRB in any given
  grant. The Phase 1 DL enforcer is consequently **exact**: a DRB's MFBR is enforced on that DRB alone, with no
  effect on any other DRB belonging to the same UE.

- **UL**: TS 38.321's Logical Channel Prioritization (LCP) procedure - which decides *within a UL grant* how many
  bytes come from which logical channel - runs **in the UE**, not the gNB. The gNB only grants a UL LCG (Logical
  Channel Group) a certain number of bytes; the UE's own LCP then decides which of the LCG's logical channels
  (DRBs) that grant's bytes actually come from, according to each LC's configured priority/PBR/BSR, entirely
  outside the gNB's control.

  **Consequence:** if a GBR DRB shares its LCG with one or more other logical channels, the gNB's only lever is
  the LCG's *aggregate* granted bytes - it cannot single out "only this DRB's bytes" within that grant. The
  Phase 1 UL enforcer's `ul_logical_channel_group_context::mbr_limiter` is therefore scoped to the **LCG**, and:
  - If the GBR DRB has an LCG **entirely to itself** (no other logical channel mapped to it), the LCG cap *is*
    an exact per-DRB cap, because the LCG's aggregate rate and the DRB's rate are the same thing.
  - If the LCG is **shared**, the cap still correctly bounds the LCG's total UL rate (so it never silently
    exceeds the configured MFBR budget in aggregate), but it cannot guarantee *that specific DRB's* share of it
    - another logical channel in the same LCG could consume most or all of the capped budget in a given window,
    or the GBR DRB's own traffic could be constrained more (or less) than its individually-negotiated MFBR,
    depending on how the UE's own LCP happens to prioritize between them.
  - `logical_channel_system::configure()`'s multi-LCID-per-LCG case is explicitly "last write wins" for which
    DRB's MFBR value ends up governing a shared LCG's cap - documented in code, not silently arbitrary, but
    still a real degradation versus the DL case.

  **Practical recommendation**: for UL MFBR enforcement to be exact, admission/DU-manager RRC configuration
  should assign each GBR DRB its own dedicated LCG (not shared with any non-GBR or other-GBR logical channel).
  This is a configuration policy, not something this patch enforces automatically - doing so automatically would
  require RRC bearer-configuration changes outside this feature's scope.

## D. Standards-compliance positioning

| | Style 2 / Action 201 (existing, see `README.md`) | Style 1 / Action 1 (this document) |
|---|---|---|
| O-RAN E2SM-RC status | **Vendor-specific** (action ID 201 is outside the standard's allocated range) | **Standard**: O-RAN.WG3.E2SM-RC v03.00, CONTROL Service Style 1 ("Radio Bearer Control"), Action ID 1 ("DRB QoS Configuration"), §7.6.2.1/8.4.2.1 |
| Scope | Whole UE (DL/UL aggregate) | A single DRB's QoS flows |
| Value semantics | Arbitrary kbps chosen by the RIC, independent of any negotiated QoS profile | The DRB's actual GBR QoS profile (5QI, MFBR) - the same IEs the 3GPP QoS architecture already defines |
| UE identification | DU-local `gNB-DU-UEID` (`gNB-CU-UE-F1AP-ID`) | Full `gNB-UEID` (AMF UE NGAP ID + GUAMI + F1AP ID) - required because this executor is CU-side |
| Discoverable by a generic (non-srsRAN-aware) xApp | No | Yes |

RAN parameters implemented for Style 1/Action 1 (IDs per the spec table in §8.4.2.1 of the same document,
verified against the actual spec text, not assumed from convention):

| RAN Parameter ID | Name | Status |
|---|---|---|
| 1 | DRB ID | Implemented (mandatory key) |
| 2 | 5QI | Implemented |
| 3-11 | Packet Delay Budget, Packet Error Rate, ARP, Priority Level of mapped QoS flows | **Not implemented** |
| 12 | QoS parameters for GBR flows in NG-RAN Bearer (STRUCTURE) | Implemented (container only; recursion handles children) |
| 13 | Maximum Flow Bit Rate Downlink | **Implemented** (this feature's DL cap) |
| 14 | Guaranteed Flow Bit Rate Downlink | Not implemented (GBR is not enforced as a floor - see §E) |
| 15 | Maximum Packet Loss Rate Downlink | Not implemented |
| 16 | Maximum Flow Bit Rate Uplink | **Implemented** (this feature's UL cap, subject to §C) |
| 17 | Guaranteed Flow Bit Rate Uplink | Not implemented |
| 18 | Maximum Packet Loss Rate Uplink | Not implemented |
| 19-22 | QoS Monitoring (enable/frequency/disable), Reflective QoS Mapping | Not implemented |

This mirrors how the pre-existing Style 3/Action 1 (handover) CU executor only implements a subset of its own
spec's RAN parameters (target cell ID; PDU session/DRB/secondary-cell parameters 7-21 are a documented `TODO`)
- partial RAN-parameter coverage per action is an established pattern in this codebase, not unique to this
feature.

The underlying wire IEs are all pre-existing, standard ones - no new/custom IEs were introduced anywhere in this
feature: `gbr_qos_flow_information` (TS 38.473 §9.3.1.46, "Bit Rate" IE per §9.3.1.30), the F1AP `qos_info`/
`qos_info_present` DRB-modify IE, and the NGAP/E1AP QoS-flow-modify IEs already present in srsRAN's ASN.1 and
internal type system.

## E. Other known limitations

- **MFBR is a cap, not a guarantee.** Only the *maximum* flow bit rate is enforced. The *guaranteed* flow bit
  rate (GFBR - RAN Parameter IDs 14/17) is not enforced as a scheduling floor; true GBR delivery assurance would
  require additional scheduler-side prioritization logic (e.g. guaranteeing the GFBR is scheduled ahead of
  non-GBR traffic under contention), which is out of scope for this feature. `scheduler_time_qos.cpp`'s existing
  QoS-aware weighting is a *soft* prioritization signal, not a hard guarantee.
- **Dynamic 5QI is not supported.** Both `qos_flow_context` (CU-UP) and this feature's Phase 4 control-action
  parsing assume/require a non-dynamic 5QI descriptor (`srsran_assert(not is_dyn_5qi())` elsewhere in the
  codebase; Phase 4's RAN-parameter-1-1 executor always constructs a `non_dyn_5qi_descriptor`). A dynamic-5QI
  flow's MFBR is not currently controllable through this path.
- **CU-UP changes (Phase 3) are bookkeeping, not enforcement.** Nothing in the CU-UP shapes traffic; it only
  keeps its 5QI-keyed F1-U DSCP-marking config consistent with the DU/scheduler's view. All actual rate
  enforcement is 100% DU-scheduler-side (Phase 1), for both the standard-NGAP and RIC-control trigger paths.
  Traffic still passes through CU-UP/GTP-U unmodified in either direction; enforcement solely gates how many
  bytes the DU scheduler *grants* in the first place.
- **Fire-and-forget control acknowledgment.** Phase 4's `trigger_qos_flow_modification()` (and the E2SM-RC
  response it produces) only confirms the request was *accepted for processing*, not that the F1AP/E1AP/RRC
  reconfiguration completed successfully. Same limitation as the pre-existing `trigger_handover()`.
- **One attached UE at a time in this testbed** (Phase 0's ZMQ RF constraint) - not a feature limitation, but it
  does mean DL and UL cannot currently be validated with two simultaneous GBR UEs sharing real contention.

## F. Upstream bug found and fixed during live validation

Driving `drb_qos_cap_xapp.py` against a live, attached UE for the first time surfaced two real, pre-existing
srsRAN bugs in the shared PDU-Session-Modify → RRC-Reconfiguration path - **not specific to this feature's new
code**, but only reachable live through it in this testbed, because Open5GS's local policy config cannot itself
emit a real NGAP PDU Session Resource Modify with GBR values (see §D's PCC-rule limitation). Any future NGAP/PCF
path that reaches `pdu_session_resource_modification_routine` would hit the same bugs. Both are now fixed.

### Symptom (100% reproducible, 3/3 before the fix)

```
gNB:   [E2SM-RC] ue=0: Executing E2SM-RC DRB QoS Configuration for DRB1     ← Phase 4 code: correct
       [RRC]     ue=0 c-rnti=0x4601: DCCH DL rrcReconfiguration
srsue: [RRC-NR]  SRB1 - Rx rrcReconfiguration (105 B)
       [RRC-NR]  Proc "NR Connection Reconfiguration" - Reconfiguration failure not implemented for initiator 2
       [RRC-NR]  Proc "NR Connection Reconfiguration" - Finished NR Connection Reconfiguration with failure
gNB:   [RRC]     "RRC Reconfiguration Procedure" timed out after 4000ms
       [CU-CP]   "PDU Session Resource Modification Routine" RRC reconfiguration failed
       → gNB requests UE context release; srsue receives rrcRelease and drops.
```
A full `gdb -p <pid> thread apply all bt full` on the resulting "stuck" gNB process (taken while it looked
hung) showed every one of its 13 threads legitimately idle - the apparent hang was the ZMQ RF `radio` thread
spin-polling for samples from a UE that had gone quiet after the release, not a deadlock in any new C++ code.
This confirmed the fault was in the RRC message content itself, not in Phase 4's async task handling.

### Root cause

Call graph (all confirmed by reading the source, not inferred):
```
pdu_session_resource_modification_routine::operator()
  → up_resource_manager::calculate_update(modify_request)
    → modify_qos_flow()                    [lib/cu_cp/up_resource_manager/up_resource_manager_helpers.cpp]
  → fill_rrc_reconfig_args(...)             [lib/cu_cp/routines/pdu_session_routine_helpers.cpp]
  → rrc_ue->handle_rrc_reconfiguration_request(...)   → srsue rejects it
```

**Bug 1**: `modify_qos_flow()` unconditionally *regenerated* `pdcp_cfg`/`sdap_cfg` from scratch on every
QoS-flow modify - even when nothing PDCP/SDAP-relevant (5QI, mapped-QFI set) had actually changed (our test:
same 5QI, same single QFI, only the MFBR value differed). `fill_rrc_reconfig_args()`'s `drb_to_modify` loop then
*unconditionally* forwarded that regenerated config into the outgoing `DRB-ToAddMod` IE's `pdcp-Config`/
`cnAssociation` fields. Per the general 3GPP RRC design principle of signaling only what changes,
`cnAssociation`/`pdcp-Config` carry (re)establishment/reconfiguration semantics and shouldn't be resent
unchanged on a plain modify - `srsue` (and plausibly other UE implementations) can reject this outright.

**Bug 2** (found only after fixing Bug 1): once Bug 1's fix correctly stopped populating `pdcp-Config`/
`cnAssociation`, the code still emitted a *bare* `DRB-ToAddMod` entry (just `drb-Identity`, nothing else) to
represent "no change." That's spec-legal per TS 38.331 (all `DRB-ToAddMod` sub-fields besides `drb-Identity`
are optional), but `srsue`'s own radio-bearer-config application logic doesn't handle it - it hits an
explicitly-unimplemented internal error path (`srsue`'s own log: `"Reconfiguration failure not implemented for
initiator 2"`).

### Fix

1. `include/srsran/cu_cp/up_context.h` - added `bool pdcp_and_sdap_reconfig_required = true;` to
   `up_drb_context` (default `true` preserves today's behavior for every other producer - DRB setup, handover,
   reestablishment - none of which were touched).
2. `lib/cu_cp/up_resource_manager/up_resource_manager_helpers.cpp`, `modify_qos_flow()` - now starts from a
   *copy* of the DRB's existing context instead of a fresh one, compares old vs. new 5QI and mapped-QFI set,
   and only re-derives `pdcp_cfg`/`rlc_mod`/`sdap_cfg` (and sets the new flag) when something they actually
   depend on changed. (SDAP comparison is computed generically, not hardcoded false, so it stays correct if the
   current 1:1 QoS-flow-to-DRB mapping is ever relaxed.)
3. `lib/cu_cp/routines/pdu_session_routine_helpers.cpp`, `fill_rrc_reconfig_args()` - in the `drb_to_modify`
   loop only (setup/`drb_to_add` untouched), when neither `reestablish_drbs` nor
   `pdcp_and_sdap_reconfig_required` applies, the DRB is skipped from `drb_to_add_mod_list` entirely (addressing
   Bug 2 - working around `srsue`'s unimplemented bare-entry path, and more spec-idiomatic besides: the IE only
   needs to list DRBs actually being added or modified).

**Explicitly not touched**: the parallel E1AP path (`fill_drb_to_modify_list`, same file) feeding CU-UP's
`BearerContextModificationRequest` still unconditionally resends `pdcp_cfg`/`sdap_cfg`. CU-UP is a trusted
internal E1AP peer, not a third-party UE stack - reconfiguring its own PDCP entity to values it already has is
a harmless no-op there, so leaving it alone keeps the fix minimal.

**Blast radius**, confirmed by call-graph tracing: `modify_qos_flow()` has exactly one call site, reachable
only from `pdu_session_resource_modification_routine`. DRB setup, handover, and reestablishment are structurally
unaffected (different code path, or `reestablish_drbs=true` already takes the untouched branch).

### Reproduction

1. Phase 0 setup: GBR subscriber IMSI `001010000059478` in Open5GS (5QI 1), `~/config/ue_zmq_gbr.conf`.
2. Start gNB (`./gnb.sh`) and attach the UE (`sudo srsue ~/config/ue_zmq_gbr.conf`); confirm `DRB1` is up.
3. From the RIC side, send a Style 1/Action 1 DRB QoS Configuration control request for that DRB with only
   `max_dl_kbps`/`max_ul_kbps` set (no `five_qi`) - e.g. via `e2sm_rc_module.py`'s `control_drb_qos(e2_node_id,
   amf_ue_ngap_id, gnb_cu_ue_f1ap_id, plmn, drb_id=1, max_dl_kbps=5000, max_ul_kbps=5000)`.
4. Before the fix: `srsue` logs `RRC NR reconfiguration failed` within ~4s of the request, followed by
   `rrcRelease`. After the fix: `srsue` logs `Finished NR Connection Reconfiguration successfully` within
   tens of milliseconds, and the connection remains stable.

### Bug 3 (found after fixing Bugs 1 & 2): CU-CP never forwards a modified DRB's QoS to the DU

Even with a clean RRC Reconfiguration round trip, the scheduler still wasn't enforcing anything - a `qos_desc`
comparison via new diagnostic logging (`logical_channel_system::configure()`) showed the DL logical channel
tracked `has_gbr_info=false` even after a successful control action.

**Root cause**: `update_modify_list_with_bearer_ctxt_mod_response()`
(`lib/cu_cp/routines/pdu_session_resource_modification_routine.cpp`) only reads
`e1ap_pdu_session_resource_modified_item::drb_setup_list_ng_ran` (newly-added DRBs) and `drb_failed_list_ng_ran`
when building the outgoing F1AP `UEContextModificationRequest`. It never reads
`drb_modified_list_ng_ran` - the field CU-UP actually uses to report an *existing* DRB's successful
modification (confirmed present: the gNB log shows `[CU-UP] Modified DRB1`). A `// TODO: traverse other fields`
comment a few lines below marks exactly this gap. Consequently `f1ap_ue_context_modification_request
.drbs_to_be_modified_list` - the field Phase 2's DU-side code (`du_bearer_resource_manager.cpp`) already knew
how to consume - was always empty for a QoS-only modify, so Phase 1's scheduler enforcer was structurally
unreachable via this path, regardless of how correct the RRC/E1AP signaling was.

**Fix**: added a loop over `e1ap_item.drb_modified_list_ng_ran` in
`update_modify_list_with_bearer_ctxt_mod_response()`, building a `f1ap_drb_to_modify` entry (`qos_info.drb_qos`
= the recomputed `up_drb_context::qos_params`, `qos_info.s_nssai` from the same) for each modified DRB and
pushing it into `ue_context_mod_request.drbs_to_be_modified_list` - mirroring the existing `drb_to_add` handling
a few lines above, just for the modify case that handling never covered.

**Confirmed via added diagnostics** (temporary `srslog` lines in `logical_channel_system::configure()`, kept in
the tree as they close a real observability gap flagged in §E): after the fix, the same control action produces
```
[SCHED] lcid=4: qos_is_tracked=true has_qos=true has_gbr_info=true
[SCHED] lcid=4: MFBR DL cap 5000kbps
```

## G. Testing status - live-validated

All five phases build clean, and the full RIC-control chain has been validated live end-to-end against a real
attached UE through the §F/Bug-3 fix cycle above: control action -> CU-CP -> F1AP -> DU scheduler config -> MFBR
cap actually installed and enforced.

**Throughput confirmation**: with a 5000 kbps DL cap active on DRB1, `iperf3 -u -b 20M` (4x oversubscribed,
deliberately) measured via server-side GTPU/RLC/MAC log analysis (summing real - non-padding - DRB1 SDU bytes
against wall-clock time, since the raw PHY "brate" console metric includes MAC padding and does not reflect
delivered throughput) showed:

| Window | Duration | Real DRB1 bytes | Measured rate |
|---|---|---|---|
| Dense burst | 4.15 s | 2,497,845 B | **4.82 Mbps** |
| Full active window | 12.44 s | 7,497,798 B | **4.82 Mbps** |

Both consistent, sustained, and just under the 5.0 Mbps cap - exactly the expected result for a token-bucket
enforcer under sustained oversubscription. The excess offered load (20 Mbps requested vs. ~4.8 Mbps delivered)
was shed via PDCP's SDU discard-on-timeout mechanism (`RLC ... Discarded SDU` in the log) as the RLC transmit
queue backed up beyond the discard timer - the expected, standards-correct consequence of enforcing a rate cap
against UDP traffic with no congestion control, not a bug.

UL enforcement (LCG-scoped, see §C) was not separately throughput-validated in this session - DL was the focus
since it's the direction with an exact per-DRB guarantee.
