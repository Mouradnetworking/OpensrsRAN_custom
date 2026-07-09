# DRB QoS Configuration xApp — System Design, Protocol Propagation & Validation Guide

> **Scope of this document.** This is the master reference for the *DRB QoS Configuration* capability: the
> standard O-RAN E2SM-RC Style 1 / Action 1 control action, the srsRAN code that implements it (5 phases across
> the scheduler, DU-manager, CU-UP, CU-CP, and E2 agent), and the `drb_qos_cap_xapp.py` xApp that drives it.
> [`GBR_MFBR_QOS_ENFORCEMENT.md`](./GBR_MFBR_QOS_ENFORCEMENT.md) is this document's companion — it has the
> deep-dive on the three upstream srsRAN bugs found and fixed while live-validating this feature, with full
> root-cause traces and reproduction steps; this document focuses on the system as a whole: what it is, how
> data flows through it, where it stands against the standard, and how to stand it up and validate it from
> scratch. [`README.md`](./README.md) and [`AGENTIC_RAN_DEVELOPMENT_AUDIT.md`](./AGENTIC_RAN_DEVELOPMENT_AUDIT.md)
> cover the earlier, separate UE-aggregate bitrate-cap effort (Style 2, vendor Action 201) — see §6 for how the
> two relate.

---

## 1. The problem

A GBR (Guaranteed Bit Rate) QoS flow's Maximum Flow Bit Rate (MFBR) is a *contractual* QoS parameter — it is
signaled end-to-end by the core network (AMF/SMF/PCF) over NGAP at PDU session establishment, and srsRAN's
scheduler already had every type it needed to represent it (`gbr_qos_flow_information` with `max_br_dl`/
`max_br_ul`). But before this work, nothing in srsRAN actually *enforced* that number as a hard cap on a
specific DRB's granted bytes:

- The MAC scheduler used GBR/5QI information only for **admission** and for a *soft* scheduling-priority signal
  (`scheduler_time_qos.cpp`'s QoS-aware weighting) — a UE with spare radio conditions could exceed its
  negotiated MFBR indefinitely.
- The only bitrate-*capping* mechanism that existed was this project's own earlier work (see §6): a UE-*aggregate*
  cap, driven by an arbitrary kbps value an operator/xApp picks by hand — not derived from, or tied to, the
  UE's actual negotiated GBR QoS profile, and not addressable per-DRB.
- Nothing in the O-RAN E2SM-RC control surface let a *standard*, vendor-neutral xApp reconfigure a DRB's QoS
  (5QI, MFBR) the way it could against any other conformant E2 node — the prior capability was an srsRAN-specific
  vendor action (Style 2, Action ID 201, outside the standard's allocated range).

This work closes both gaps: it makes the scheduler actually enforce a DRB's MFBR, and it exposes that
enforcement through the **standard** O-RAN control action for it — CONTROL Service Style 1 ("Radio Bearer
Control"), Action 1 ("DRB QoS Configuration") — so any spec-compliant near-RT RIC xApp, not just an
srsRAN-specific one, can drive it.

---

## 2. System model

```
┌───────────────────────────────────────────────────────────────────────────────────────┐
│                                   Near-RT RIC (oran-sc-ric)                             │
│   ┌────────────────┐   E2AP (RMR transport, SCTP underneath)   ┌──────────────────────┐ │
│   │ drb_qos_cap_    │──────────────────────────────────────────▶  e2term / e2mgr /     │ │
│   │ xapp.py          │◀──────────────────────────────────────────  submgr / dbaas       │ │
│   └────────────────┘                                            └──────────┬───────────┘ │
└─────────────────────────────────────────────────────────────────────────────┼─────────────┘
                                                                                │ E2AP (SCTP, port 36421)
┌───────────────────────────────────────────────────────────────────────────────┼─────────────┐
│  gNB (single process, split CU-CP / CU-UP / DU)                                ▼             │
│                                                                    ┌────────────────────┐    │
│   ┌────────────┐   NGAP    ┌───────────────────────────────┐      │  E2 Agent (CU-CP)   │    │
│   │    AMF      │◀─────────▶│            CU-CP               │◀─────┤  E2SM-RC Style 1/2/3 │  │
│   │  (Open5GS)  │          │  (RRC, NGAP, F1AP-c, E1AP-c)   │      └────────────────────┘    │
│   └──────┬──────┘          └──────────┬───────────┬─────────┘                                │
│          │ N4/PFCP                    │ E1AP       │ F1AP                                     │
│   ┌──────▼──────┐          ┌──────────▼───────┐   ┌▼─────────────────────────────────────┐    │
│   │  SMF / UPF  │◀─GTP-U──▶│      CU-UP        │   │                DU                     │   │
│   │  (Open5GS)  │  (N3/F1-U)│ (PDCP-U, SDAP,    │   │  RRC-relay, F1AP-du, MAC scheduler,   │   │
│   └─────────────┘          │  F1-U bearer mgmt) │   │  PHY (ZMQ RF here)                    │   │
│                             └────────────────────┘   │  ← MFBR enforcement happens HERE →   │   │
│                                                        └───────────────┬─────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┼──────────────────────┘
                                                                            │ Uu (ZMQ RF loopback here)
                                                                 ┌──────────▼──────────┐
                                                                 │   UE (srsue)         │
                                                                 └──────────────────────┘
```

**Entities, in the sense the O-RAN architecture defines them:**
- **near-RT RIC**: `oran-sc-ric` (e2term/e2mgr/submgr/dbaas/appmgr/rtmgr_sim, docker compose).
- **xApp**: `drb_qos_cap_xapp.py`, driving the standard `e2sm_rc_module.py` SDK method `control_drb_qos()`.
- **E2 Node**: the gNB, which in srsRAN's split architecture exposes **two independent E2 agents in the same
  process** — one on the CU-CP, one on the DU — each with its own E2AP association and its own registered set
  of E2SM-RC control services. This is the single most important, and easiest to get wrong, architectural fact
  about this feature: **Style 1/Action 1 lives only on the CU-CP's E2 agent**, and the two agents have
  *different* E2 Node IDs (`gnb_001_001_00019b` for the CU-CP, `gnbd_001_001_00019b_0` for the DU — the `d`+
  suffix marks the DU). Targeting the wrong one gets you `RIC Control Service not supported` from the DU agent,
  not a routing failure (see §14/§15).
- **5GC**: Open5GS (AMF, SMF, UPF, PCF, UDM/UDR, all as one `open5gs-5gc` process/container in this testbed).
- **UE**: `srsue`, ZMQ RF loopback to the gNB.

---

## 3. Architecture — how it works

Two independent trigger paths converge on **one** enforcement point, and share almost all of their downstream
machinery:

```
                     ┌─────────────────────────────┐         ┌───────────────────────────────┐
 AMF/PCF (standard   │ NGAP PDU Session Resource     │         │  E2SM-RC Style 1/Action 1       │  xApp / near-RT
 3GPP path, not      │ Modify Request                │         │  RIC Control Request            │  RIC (this work)
 exercised live in   └──────────────┬────────────────┘         └────────────────┬────────────────┘
 this testbed - see                 │                                            │
 §8/§14)                            ▼                                            ▼
                     ┌───────────────────────────────────────────────────────────────────────┐
                     │        cu_cp_ngap_handler::handle_new_pdu_session_resource_             │
                     │        modify_request()  (the SAME entry point either way)              │
                     └──────────────────────────────┬────────────────────────────────────────┘
                                                      ▼
                     ┌───────────────────────────────────────────────────────────────────────┐
                     │        pdu_session_resource_modification_routine (CU-CP, pre-existing,  │
                     │        3 bugs found/fixed here - see GBR_MFBR_QOS_ENFORCEMENT.md §F)    │
                     │   1. up_resource_manager::calculate_update()                            │
                     │   2. E1AP BearerContextModificationRequest → CU-UP (Phase 3)             │
                     │   3. F1AP UEContextModificationRequest → DU (Phase 2, + Bug-3 fix)       │
                     │   4. RRC Reconfiguration → UE (Bugs 1+2 fix: signal only real deltas)    │
                     └──────────────────────────────┬────────────────────────────────────────┘
                                                      ▼
                     ┌───────────────────────────────────────────────────────────────────────┐
                     │  DU-Manager: du_bearer_resource_manager::modify_drbs() (Phase 2)         │
                     │  → create_scheduler_ue_config_request() → MAC "Sched UE Config"           │
                     └──────────────────────────────┬────────────────────────────────────────┘
                                                      ▼
                     ┌───────────────────────────────────────────────────────────────────────┐
                     │  MAC Scheduler: logical_channel_system::configure() (Phase 1)            │
                     │  → per-DRB (DL) / per-LCG (UL) ue_bitrate_limiter token bucket set to     │
                     │    the new MFBR cap → enforced on every subsequent PDSCH/PUSCH grant      │
                     └───────────────────────────────────────────────────────────────────────┘
```

The RIC-control path (Phase 4/5, this work's own new code) is a thin adapter on top of the standard path: it
resolves the target UE/DRB from the E2SM-RC request, builds the *exact same* `cu_cp_pdu_session_resource_
modify_request` an NGAP message would produce, and calls the *exact same* CU-CP entry point. Everything from
`pdu_session_resource_modification_routine` downward is 100% shared, pre-existing srsRAN machinery — which is
also *why* the three bugs found in this session (see §11) are real, standalone srsRAN bugs and not something
specific to the new xApp path: they'd affect a real AMF/PCF-driven QoS modification identically.

---

## 4. Protocol propagation — hop by hop

This traces one concrete control action end-to-end: `control_drb_qos(e2_node_id='gnb_001_001_00019b',
amf_ue_ngap_id=14, gnb_cu_ue_f1ap_id=0, plmn='00101', drb_id=1, max_dl_kbps=5000, max_ul_kbps=5000)`.

| # | Hop | Protocol / IE | What crosses the wire |
|---|---|---|---|
| 1 | xApp → RIC | E2AP `RICcontrolRequest` over RMR/SCTP | `RANfunctionID=3` (E2SM-RC); `ricControlHeader` = Format 1: `ueID=gNB-UEID{amf-UE-NGAP-ID, GUAMI, gNB-CU-UE-F1AP-ID-List}`, `ricStyleType=1`, `ricControlActionID=1`; `ricControlMessage` = Format 1: `ranP-List` = [`{id=1 (DRB ID), value=1}`, `{id=12 (GBR STRUCTURE), children=[{id=13 (MFBR DL), value=5000000}, {id=16 (MFBR UL), value=5000000}]}`] |
| 2 | RIC routing | e2term/RMR route table | Delivered to the E2 termination matching E2 Node ID `gnb_001_001_00019b` — the **CU-CP's** agent, not the DU's (see §2) |
| 3 | gNB E2 Agent (CU-CP) | E2SM-RC dispatch | `e2sm_rc_impl::get_e2sm_control_service()` matches `ricStyleType=1` → `e2sm_rc_control_action_1_1_cu_executor::execute_ric_control_action()` parses the RAN parameters into a `cu_qos_flow_control_config{drb_id=1, max_flow_bit_rate_dl=5,000,000 bps, max_flow_bit_rate_ul=5,000,000 bps}`, resolves `ue_index` via `cu_configurator::get_ue_index(amf_ue_id, guami, gnb_cu_ue_f1ap_id)` |
| 4 | Internal (CU-CP) | C++ call, no wire protocol | `cu_configurator_impl::trigger_qos_flow_modification()` looks up the DRB's current context via `up_resource_manager`, builds a `cu_cp_pdu_session_resource_modify_request` (copying every QoS parameter *not* supplied, so a partial MFBR-only update doesn't clobber the rest), schedules it on the UE's task executor, and calls `cu_cp_ngap_handler::handle_new_pdu_session_resource_modify_request()` — the same function an incoming NGAP message would call |
| 5 | CU-CP → CU-UP | E1AP `BearerContextModificationRequest` / `...Response` | `pdu_session_res_to_modify_list[psi=1].drb_to_modify_list_ng_ran[DRB1].flow_map_info[QFI1]` carries the updated `GBR-QoS-Information` (`maximumFlowBitRateDownlink`/`Uplink`); CU-UP's response includes `drb_modified_list_ng_ran[DRB1]` confirming it |
| 6 | CU-CP → DU | F1AP `UEContextModificationRequest` / `...Response` | `DRBs-To-Be-Modified-List[DRB1].qoSInformation.dRB-QoS.gBR-QoSFlowInformation` carries the same MFBR values (this hop was completely missing before Bug 3's fix — see §11) |
| 7 | DU-internal | C++ call | `du_bearer_resource_manager::modify_drbs()` updates `du_ue_drb_config::qos`; `create_scheduler_ue_config_request()` rebuilds the MAC scheduler's per-LC config; `logical_channel_system::configure()` sets the `ue_bitrate_limiter` cap — **this is the actual enforcement point**, nothing further needs to cross any protocol boundary for enforcement itself |
| 8 | CU-CP → UE | RRC `RRCReconfiguration` / `RRCReconfigurationComplete`, over F1AP `DLRRCMessageTransfer`/`ULRRCMessageTransfer` (SRB1) | Only sent if something RRC-*visible* actually changed (post Bugs 1+2 fix, a pure MFBR update signals nothing here — `radioBearerConfig`'s `drb-ToAddModList` omits DRB1 entirely, since neither PDCP nor SDAP config changed) |
| 9 | gNB → RIC | E2AP `RICcontrolAcknowledge` | Confirms the control action was *accepted for processing* (fire-and-forget — see the Limitations in the companion doc) |
| — | (ongoing, no further signaling) | PDSCH/PUSCH grants | Every subsequent slot, `allocate_mac_sdu()`/`handle_ul_grant()` clamp DRB1's/its LCG's granted bytes against the token bucket — this is purely internal DU-scheduler state, invisible to every other node |

The **user-plane** path (GTP-U) is unaffected in structure by any of this — `UPF —N3(GTP-U)→ CU-UP —F1-U(GTP-U)→
DU —PDSCH→ UE`. The MFBR cap changes nothing about how packets are tunneled; it only changes how many DL bytes
per slot the DU scheduler is willing to grant, which shows up as increased CU-UP/DU **queueing and PDCP-discard**
under sustained oversubscription (this is exactly what live validation observed — see the companion doc's §G).

---

## 5. Standards compliance — current situation

| Aspect | Status |
|---|---|
| Control Service Style | **Style 1 — "Radio Bearer Control"**, per O-RAN.WG3.E2SM-RC v03.00 §7.6.2.1 |
| Control Action | **Action ID 1 — "DRB QoS Configuration"**, §8.4.2.1 (verified against the actual spec text in this repo — not assumed from convention; easy to confuse with Action 2, "QoS flow mapping configuration", which is a different action) |
| RAN parameters implemented | ID 1 (DRB ID, mandatory key), ID 2 (5QI), ID 12 (GBR-info STRUCTURE container), ID 13 (MFBR DL), ID 16 (MFBR UL) |
| RAN parameters *not* implemented | IDs 3–11 (Packet Delay Budget, Packet Error Rate, ARP, Priority Level), 14–15/17–18 (GFBR, packet-loss-rate DL/UL), 19–22 (QoS monitoring, reflective QoS) — see the companion doc §D for the full per-ID table |
| Wire IEs | 100% pre-existing, standard IEs — `gbr_qos_flow_information` (TS 38.473 §9.3.1.46 / Bit Rate IE §9.3.1.30), F1AP `qos_info` DRB-modify IE, NGAP/E1AP QoS-flow-modify IEs. **No new/custom IEs were introduced anywhere.** |
| Enforcement semantics | MFBR (cap) only; GFBR (guaranteed floor) is not enforced — see Limitations |
| RRC signaling correctness | Now standards-aligned: only signals `pdcp-Config`/`cnAssociation` when something they actually depend on changed (Bugs 1+2, §11) — previously violated the "signal only deltas" RRC design principle |

---

## 6. Comparison with existing/available RIC control actions in this gNB

| | Style 2 / Action 6<br>(PRB quota) | Style 2 / Action 201<br>(UE bitrate cap) | Style 3 / Action 1<br>(Handover) | **Style 1 / Action 1<br>(this work)** |
|---|---|---|---|---|
| Standard or vendor | Standard | **Vendor** (srsRAN-specific; ID outside standard's allocated range) | Standard | **Standard** |
| E2 agent | DU | DU | CU-CP | **CU-CP** |
| UE identification | `gNB-DU-UEID` (F1AP ID only) | `gNB-DU-UEID` | `gNB-UEID` (full) | **`gNB-UEID`** (full) |
| Target scope | UE (via RRM policy / S-NSSAI) | Whole UE, both directions | UE (mobility) | **A single DRB** |
| What it controls | Resource *share* (%) | Achieved *rate*, arbitrary kbps | Serving cell | **A DRB's actual negotiated QoS (5QI, MFBR)** |
| Tied to negotiated QoS contract? | No | No | N/A | **Yes** |
| Discoverable by a generic (non-srsRAN) xApp | Yes | No | Yes | **Yes** |
| Enforcement point | DU scheduler | DU scheduler (`ue_bitrate_limiter`, UE-scoped) | N/A (RRC procedure) | **DU scheduler (`ue_bitrate_limiter`, reused, DRB/LCG-scoped)** |
| DL exactness | N/A | Exact (gNB builds PDSCH itself) | N/A | **Exact** |
| UL exactness | N/A | Exact (UE-aggregate, no per-DRB ambiguity) | N/A | **Exact only if the DRB has its own LCG** (3GPP LCP is UE-controlled — see companion doc §C) |

Style 2/Action 201 and this feature are **complementary, not redundant**: 201 is a coarse, fast, srsRAN-specific
knob useful for demos/ops; Style 1/Action 1 is the standards path that ties directly to the UE's actual QoS
contract and is what any real multi-vendor RIC deployment would need to use.

---

## 7. Current features

- Per-DRB (DL) / per-LCG (UL) MFBR enforcement in the MAC scheduler, reusing the existing `ue_bitrate_limiter`
  token bucket.
- Two independent trigger paths converging on the same enforcement code: standard NGAP/PCF-driven PDU Session
  Resource Modify, and the new E2SM-RC Style 1/Action 1 RIC control action.
- Full CU-CP↔CU-UP↔DU↔UE signaling chain (E1AP, F1AP, RRC) correctly propagates a QoS-only DRB modification,
  including graceful partial updates (an MFBR-only change doesn't touch unrelated QoS fields).
- xApp SDK method (`control_drb_qos()`) and demo xApp (`drb_qos_cap_xapp.py`) for driving it.
- Live-validated: **4.82 Mbps sustained measured against a 5.0 Mbps configured cap** under 4× UDP
  oversubscription (see companion doc §G).

## 8. Known limitations

(Full detail, including the UL/LCG-sharing 3GPP protocol constraint, is in the companion doc §C/§E — summarized
here.)

- **UL is exact only when the GBR DRB has a dedicated LCG.** TS 38.321 Logical Channel Prioritization runs in
  the UE, not the gNB — a shared LCG can only be capped in aggregate.
- **MFBR only, not GFBR.** No scheduling-floor guarantee is enforced; only a ceiling.
- **Dynamic 5QI unsupported** end-to-end (CU-UP and the RIC executor both assume non-dynamic 5QI descriptors).
- **CU-UP involvement is bookkeeping only** (5QI-keyed F1-U DSCP marking); it does not shape traffic.
- **Fire-and-forget control acknowledgment** — the E2AP ACK confirms acceptance, not completion.
- **Open5GS in this testbed cannot itself trigger the NGAP path** with real GBR values (no PCC-rule support in
  its local/static policy schema — see companion doc §D) — only the RIC-control path was exercisable live here.
  This is an Open5GS-configuration limitation, not an srsRAN one.
- **Single ZMQ RF port pair in this testbed** — only one UE can be attached at a time (see §13).
- Three RRC/F1AP signaling bugs found and fixed live (§11) were pre-existing, shared srsRAN gaps — worth
  upstreaming regardless of this feature.
- Not validated against real SDR/COTS UE hardware, or with multiple simultaneous UEs (see §12/§13).

## 9. Future work

### Priority recommendation

Of everything below, three items matter most, in this order — the rest are worth doing eventually but are
lower research value per hour spent (mostly "should just work" engineering validation, not open questions):

1. **Validate the multi-UE UL LCG-sharing scenario.** This is the one claim in the whole feature that is
   currently purely analytical: §3/§C predicts (from TS 38.321) that UL enforcement degrades to an LCG-aggregate
   cap when a GBR DRB shares its LCG with another logical channel, but this was never actually attached and
   observed with two UEs. It is also the cheapest open item to close — no new hardware required, just a second
   UE profile and either a multi-port ZMQ RF config or two RF chains — and it directly converts the one part of
   this contribution that currently rests on "trust the spec reading" into a measured result.
2. **Fix the Open5GS PCC-rule gap (§8) and re-validate the three bug fixes (§11) via the *standard* NGAP path.**
   Everything measured in this work went through the RIC-control trigger only. The claim that "a real
   AMF/PCF-driven QoS modification would hit the same three bugs identically" is architecturally sound (shared
   code, traced call graph) but was never actually exercised live end-to-end. Closing this gap would make the
   bug-fix story fully empirical rather than partly inferred.
3. **Enforce GFBR as a real scheduling floor**, not just a soft QoS-weighting signal. This is what turns the
   feature from "an MFBR cap" into genuine GBR bearer support — the more complete, more upstreamable version of
   what exists today.

### Remaining items

- Implement the remaining RAN parameters (GFBR IEs themselves, Packet Delay Budget/Error Rate, ARP, QoS
  monitoring) for full Style 1/Action 1 coverage.
- Support dynamic 5QI end-to-end.
- Make the E2SM-RC response carry real completion status (await the RRC/F1AP/E1AP round trip before ACKing, or
  add a follow-up INDICATION/notification once it completes) instead of fire-and-forget.
- Validate on real SDR + COTS UE hardware and a split DU/CU deployment (§12/§13's other open questions) — both
  expected to work unchanged architecturally, but not empirically confirmed.
- Upstream the three RRC/F1AP bugs (§11) to the srsRAN project independently of this feature.
- Implement Style 1/Action 1 on OpenAirInterface's E2 agent (confirmed absent today — it only registers Style
  1/Action 2) for genuine multi-vendor interoperability testing.
- Extend `contains_values()`'s apparent inverted-boolean-logic quirk (flagged, not fixed, in the companion doc)
  — worth a dedicated, separate investigation.

## 10. Use cases

- **Standards-compliant, vendor-neutral GBR enforcement**: any O-RAN-conformant xApp (not srsRAN-specific) can
  now enforce a UE's actual negotiated GBR contract via the standard action, useful for RIC vendors validating
  interoperability against srsRAN as an E2 node.
- **Dynamic GBR renegotiation without a full re-attach**: an xApp can react to observed network conditions
  (e.g. cell load, a KPM-reported SLA violation) by tightening or relaxing a specific UE's DRB's MFBR live,
  without tearing down the session.
- **Slice/tenant-level QoS assurance experiments**: combined with per-UE/per-slice telemetry (the earlier
  effort's KPM work), an xApp could enforce differentiated per-DRB caps across UEs sharing a cell as a substitute
  for (or complement to) PRB-quota-based slicing.
- **Research into RIC-driven vs. core-driven QoS control**: since both trigger paths converge on identical
  enforcement code, this testbed is a clean platform for comparing RIC-driven vs. AMF/PCF-driven QoS control
  timing/behavior once Open5GS's PCC-rule limitation is addressed.

---

## 11. All modifications made

### 5G/RAN side (srsRAN_Project, C++)

| Phase | Component | Files | What |
|---|---|---|---|
| 1 | MAC Scheduler | `lib/scheduler/ue_context/logical_channel_system.{h,cpp}`, `lib/scheduler/slicing/slice_ue_repository.h`, `lib/scheduler/ue_scheduling/{grant_params_selector,ue_cell_grid_allocator,ue_fallback_scheduler}.cpp` | Reuses `ue_bitrate_limiter` per-DRB (DL, exact) / per-LCG (UL, LCG-scoped); threads `slot_point` through the DL allocation chain; enforces at `allocate_mac_sdu`/`handle_ul_grant`. Diagnostic `srslog` lines added in `configure()` (kept — closes a real observability gap). |
| 2 | DU-Manager / F1AP | `include/srsran/f1ap/ue_context_management_configs.h`, `lib/f1ap/asn1_helpers.cpp`, `lib/du/du_high/du_manager/ran_resource_management/du_bearer_resource_manager.cpp` | New optional `qos_info` on `f1ap_drb_to_modify`; relaxed validation to accept a QoS-only (no new UL-TNL) modification; `modify_drbs()` updates `du_ue_drb_config::qos`/`mac_cfg`, reusing the existing setup-path helpers. |
| 3 | CU-UP | `lib/cu_up/pdu_session_manager_impl.cpp` | Replaced the `// TODO` QoS-flow-modify stub: updates stored 5QI (F1-U DSCP bookkeeping), or wires a newly-mapped flow onto an existing DRB; per-flow success/failure now reported. |
| 4 | CU-CP / E2 | `include/srsran/cu_cp/cu_configurator.h`, `lib/cu_cp/cu_configurator_impl.{h,cpp}`, `lib/cu_cp/cu_cp_impl.cpp`, `lib/e2/e2sm/e2sm_rc/e2sm_rc_control_action_cu_executor.{h,cpp}`, `lib/e2/common/e2_cu_cp_factory.cpp` | New `trigger_qos_flow_modification()` on `cu_configurator`; new `e2sm_rc_control_action_1_1_cu_executor`; new Style-1 `e2sm_control_service` registration alongside the pre-existing Style 3 one. |
| Bug 1+2 fix | CU-CP | `include/srsran/cu_cp/up_context.h`, `lib/cu_cp/up_resource_manager/up_resource_manager_helpers.cpp`, `lib/cu_cp/routines/pdu_session_routine_helpers.cpp` | `modify_qos_flow()` only re-derives/signals PDCP/SDAP config when 5QI or flow-membership actually changed; `fill_rrc_reconfig_args()` omits unchanged DRBs from `drb-ToAddModList` entirely (see companion doc §F for the full trace and why). |
| Bug 3 fix | CU-CP | `lib/cu_cp/routines/pdu_session_resource_modification_routine.cpp` | Added the missing loop over E1AP's `drb_modified_list_ng_ran`, forwarding modified-DRB QoS into the F1AP `UEContextModificationRequest` (previously only `drb_setup_list_ng_ran` was handled — a `// TODO: traverse other fields` marked exactly this gap). |

Net: **21 files changed, ~575 insertions** across scheduler, DU-manager, F1AP, CU-UP, CU-CP, and E2 — see
`git diff --stat` for exact line counts. Nothing has been committed as of this writing.

### Deployment / testbed side

| What | Why |
|---|---|
| New Open5GS subscriber, IMSI `001010000059478`, 5QI 1 (GBR), static IP `10.45.4.2` | See §14 |
| New UE config file, `~/config/ue_zmq_gbr.conf` | See §14 |
| `agentic_ran/GBR_MFBR_QOS_ENFORCEMENT.md` (this doc's companion) | Standards positioning, bug deep-dive, reproduction |
| `xApps/python/lib/e2sm_rc_module.py` (`oran-sc-ric` repo) | New `send_control_request_style_1_action_1()`/`control_drb_qos()` SDK method |
| `xApps/python/drb_qos_cap_xapp.py` (new file, `oran-sc-ric` repo) | Demo xApp |

---

## 12. Does it work on real SDR + COTS UE hardware?

**Not tested — this entire validation used ZMQ RF loopback (`srsue` ↔ `gnb`, no real RF at all).** Architecturally,
nothing in this feature touches PHY/RF: the enforcement mechanism (`ue_bitrate_limiter`) is explicitly documented
(in its own header) as "a pure scheduler-side, RF-agnostic mechanism ... works identically on the ZMQ RF simulator
and on real hardware ... it never touches PHY/RF parameters." The signaling chain (E1AP/F1AP/RRC/NGAP) is
identical regardless of the RF layer underneath. There is no known reason this wouldn't work with a real SDR +
COTS UE, but it has not been exercised that way, and COTS UE RRC stacks may have their own quirks (as `srsue`
itself did — §11's Bug 2) that weren't discovered here. **This is explicitly flagged as future work (§9).**

## 13. Does it work with several UEs simultaneously?

**Not tested live — this testbed's `ru_sdr.device_args` defines a single ZMQ TX/RX port pair, supporting only
one attached UE at a time** (a hard testbed constraint, unrelated to this feature's code). Every piece of this
feature's *code* is correctly per-UE/per-DRB scoped (keyed by `ue_index`, `drb_id`, `soa::row_id` in the
scheduler), so there's no structural reason it wouldn't work with multiple UEs on real RF or a multi-port ZMQ
setup. The one place multi-UE behavior would be *interesting* to validate, not just plausible, is the UL
LCG-sharing scenario (companion doc §C): with two UEs, each could have its own independent LCG-sharing
situation, and validating that UE A's cap doesn't leak into UE B's LCG accounting (it shouldn't, since
`ul_logical_channel_group_context` lives inside each UE's own `soa` row) would be worth an explicit test. **Flagged
as future work (§9).**

## 14. Why we needed a new UE profile and a new subscriber

The pre-existing testbed had exactly one working UE profile/subscriber, provisioned as a default (non-GBR, 5QI
9) bearer. Two reasons that couldn't be reused for this feature:

1. **5QI drives which scheduler/RRC/PDCP code paths activate.** `logical_channel_system::configure()`'s
   `qos_is_tracked` condition, `du_bearer_resource_manager`'s `make_gbr_drb_mac_lc_config()` vs.
   `make_non_gbr_drb_mac_lc_config()` branch, and the RRC PDCP config template selection are all keyed off 5QI
   being a GBR value. A non-GBR (5QI 9) UE would never exercise any of this feature's relevant code at all — a
   dedicated GBR (5QI 1) subscriber was structurally necessary, not a convenience.
2. **The testbed's single ZMQ port pair (§13) meant we couldn't just add a second subscriber and run both.**
   The new UE config (`ue_zmq_gbr.conf`) has to be used by *swapping* with the existing one (detach/attach), so
   it needed its own IMSI/K/OPc matched to a distinct subscriber document, and its own static IP
   (`10.45.4.2`, distinct from the pre-existing UE's) to avoid any ambiguity in Open5GS's UPF/UE-IP allocation.

The new subscriber was added directly against Open5GS's MongoDB via its own `add_users.py` helper (see §15,
step 1) rather than through any custom tooling — no new *service* was written, just a new *document* using an
existing provisioning mechanism.

---

## 15. Full validation runbook — from zero to a verified control action

### Step 1 — Provision the GBR subscriber (one-time)

```bash
docker exec open5gs_5gc python3 -c "
import sys; sys.path.insert(0, '/open5gs')
from add_users import add_user
from misc.db.python.Open5GS import Open5GS
imsi = '001010000059478'
assert len(imsi) == 15, f'IMSI length is {len(imsi)}, expected 15'   # a length mismatch caused a real, silent failure once — see below
sub = add_user(imsi, key='00112233445566778899aabbccddeeff',
                opc='63bfa50ee6523365ff14c1f45f88737d', amf='8000', qci='1', ip_alloc='10.45.4.2')
assert sub['imsi'] == imsi
print(Open5GS('127.0.0.1', 27017).AddSubscriber(sub))
"
```
**Verify it landed correctly** (a length-truncated IMSI silently registered as a *different*, 14-digit
subscriber once during this work, causing a totally unrelated-looking `Registration reject [11]` failure much
later — always re-read it back, don't trust the "success" print alone):
```bash
docker exec open5gs_5gc python3 -c "
import pymongo
db = pymongo.MongoClient('mongodb://127.0.0.1:27017')['open5gs']
s = db.subscribers.find_one({'imsi': '001010000059478'})
print('found:', s is not None, '| imsi len:', len(s['imsi']) if s else None)
"
```

### Step 2 — UE config

`~/config/ue_zmq_gbr.conf` — same structure as the existing `ue_zmq.conf`, with `imsi`/`k`/`opc` matching the
subscriber above, and `apn = srsapn` matching that subscriber's provisioned session/slice.

### Step 3 — Start the network

```bash
start5gc        # if not already running
startric         # if not already running
```
Bounce the RIC's E2-facing containers if this gNB has restarted before against the same RIC without a graceful
E2 release (a recurring, generic RIC-state gotcha — not specific to this feature):
```bash
docker restart ric_e2term ric_e2mgr ric_submgr ric_dbaas
```

### Step 4 — Install and start the gNB

`gnb.sh` runs `sudo gnb -c ~/config/gnb_zmq.yaml`, which resolves to `/usr/local/bin/gnb` — **this does not
auto-update from the build directory**:
```bash
sudo cp ~/srsRAN_Project/build/apps/gnb/gnb /usr/local/bin/gnb
./gnb.sh
```
**Verify in the gNB log** (`~/logs/srsgnb.log`, per `gnb_zmq.yaml`'s `log.filename`):
```
[E2-CU-CP] E2 Setup procedure successful.
[E2-DU   ] E2 Setup procedure successful.
```
If either says `failed` instead, go back to the RIC-bounce step above and restart the gNB again.

### Step 5 — Attach the GBR UE

```bash
sudo ip netns add ue1   # only if it doesn't already exist
sudo srsue ~/config/ue_zmq_gbr.conf
```
**Verify**: `srsue`'s own console should show `RRC Connected` → `PDU Session Establishment successful. IP:
10.45.4.2` → `RRC NR reconfiguration successful.`. On the gNB side:
```
[RRC] ue=0 c-rnti=0x4601: DCCH UL rrcSetupComplete
[DU-F1] Tx PDU ...: UEContextSetupResponse
[CU-UP] ue=0: ... psi=1 ...
```
Note the assigned `AMF_UE_NGAP_ID` (from either the gNB log's F1AP/NGAP lines or Open5GS AMF's own log,
`RAN_UE_NGAP_ID[0] AMF_UE_NGAP_ID[N]`) and confirm `DRB1` is the active DRB (visible in the E2SM-KPM debug log
line `Received RLC metrics: ue=0 DRB1.` once the RIC subscription is live) — both are needed for step 6.

### Step 6 — Execute the control action

```bash
docker exec python_xapp_runner python3 drb_qos_cap_xapp.py \
  --e2_node_id gnb_001_001_00019b \
  --amf_ue_ngap_id <N from step 5> \
  --gnb_cu_ue_f1ap_id 0 \
  --plmn 00101 \
  --drb_id 1
```
(`--e2_node_id` must be the **CU-CP's** ID, `gnb_001_001_00019b`, *not* `gnbd_001_001_00019b_0` — see §2/§15's
opening note; using the wrong one routes the request to the DU agent, which rejects it with `RIC Control
Service not supported` rather than a transport-level error, so it can look deceptively like it "worked.")

### Step 7 — Verify in the gNB log

```
[E2-CU-CP] Received RIC Control Request
[E2SM-RC ] ue=0: Executing E2SM-RC DRB QoS Configuration for DRB1
[CU-UP   ] ue=0: Modified DRB1. psi=1 f1u_teid=0x...
[CU-CP-F1] Tx PDU ...: UEContextModificationRequest
[MAC     ] ue=0 crnti=... proc="Sched UE Config": successfully finished
[SCHED   ] lcid=4: qos_is_tracked=true has_qos=true has_gbr_info=true
[SCHED   ] lcid=4: MFBR DL cap 5000kbps
[RRC     ] ue=0 c-rnti=... DCCH DL rrcReconfiguration      (only if something RRC-visible changed)
[RRC     ] ue=0 c-rnti=... DCCH UL rrcReconfigurationComplete
```
The `[SCHED] MFBR DL cap ...` line is the diagnostic added in Phase 1 (§11) — it is the single most direct
confirmation that the cap actually reached the enforcement point, as opposed to merely completing the signaling
round trip. **Absence of `RRC NR reconfiguration failed` / `UE Context Release` / a subsequent log freeze on
either the gNB or `srsue` side is the negative-side confirmation** that the RRC signaling bugs (§11, Bugs 1+2)
are not being triggered.

### Step 8 — Verify in PCAPs (recommended, not performed live in this session)

`gnb_zmq.yaml`'s `pcap:` section already captures MAC (`/tmp/gnb_mac.pcap`), NGAP, and E2AP (DU/CU-CP/CU-UP
separately). **F1AP and E1AP pcaps are not enabled by default** but the config keys exist
(`f1ap_enable`/`f1ap_filename`, `e1ap_enable`/`e1ap_filename` under the DU/CU-CP app config) — enable them to
inspect the exact `UEContextModificationRequest`/`BearerContextModificationRequest` IEs in Wireshark
(`ngap`/`f1ap`/`e1ap`/`nr-rrc` dissectors) rather than relying on log-line summaries. In the E2AP CU-CP pcap,
the `RICcontrolRequest` should decode with `ricStyleType=1`, `ricControlActionID=1`, and a `RANParameter-Item`
list matching §4's table. **This step was not exercised live in this validation session** — all verification
here was via `srslog` line inspection; PCAP-level inspection would be the natural next-level check, especially
for confirming the exact ASN.1 encoding of the E2SM-RC RAN parameters.

### Step 9 — Verify actual enforcement (throughput)

Real, non-padding application throughput must be measured from **delivered SDU bytes over wall-clock time**,
not the gNB's live PHY "brate" console metric (which includes MAC padding and looked like ~48 Mbps unthrottled
during this validation even when the true delivered rate was correctly capped at ~4.8 Mbps — see companion doc
§G for the exact analysis method used). With an `iperf3 -u` server on the core-network side (reachable at the
UPF's `ogstun` gateway IP for the UE's slice, e.g. `10.45.4.1`) and a client inside the UE's network namespace:
```bash
sudo ip netns exec ue1 iperf3 -c 10.45.4.1 -p 5201 -u -b <rate-above-cap> -t 15 -R -i 2
```
Note: `iperf3` run through `ip netns exec` is often fully-buffered (non-TTY), so its interval output may not
print until the process exits — let it run the full duration before concluding it "isn't working."
