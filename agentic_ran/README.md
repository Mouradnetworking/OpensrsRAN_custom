# Agentic RAN on srsRAN + O-RAN SC RIC

> This README documents the **Agentic RAN** work layered on top of srsRAN and the O-RAN SC near-RT RIC. It does
> **not** replace the main [srsRAN Project README](../README.md) at the repository root - that remains the
> official upstream documentation for building/running srsRAN in general. This document is scoped to the
> telemetry and control additions described here and in
> [`AGENTIC_RAN_SRSRAN_TELEMETRY_DESIGN.md`](../AGENTIC_RAN_SRSRAN_TELEMETRY_DESIGN.md), and to running the
> specific multi-repository testbed they were validated against.
>
> For the full change audit, reproduction patches, novelty analysis, and use cases, see
> [`AGENTIC_RAN_DEVELOPMENT_AUDIT.md`](./AGENTIC_RAN_DEVELOPMENT_AUDIT.md) in this same directory.
>
> A second, later control capability - standards-based per-DRB/per-QoS-flow GBR MFBR enforcement via the
> standard E2SM-RC "DRB QoS Configuration" action (Style 1, Action 1), as opposed to this document's
> UE-aggregate vendor bitrate cap (Style 2, Action 201) - is documented separately in
> [`GBR_MFBR_QOS_ENFORCEMENT.md`](./GBR_MFBR_QOS_ENFORCEMENT.md) (standards positioning, UL Logical-Channel-Group
> sharing limitation, and a 3-bug deep-dive) and [`DRB_QOS_XAPP_SYSTEM_DESIGN.md`](./DRB_QOS_XAPP_SYSTEM_DESIGN.md)
> (system architecture, protocol propagation, use cases, and a from-scratch validation runbook).

## A. Project overview

**What this project is.** An extension of srsRAN's existing O-RAN near-RT RIC integration (E2AP, E2SM-KPM,
E2SM-RC) to (1) expose richer, more granular RAN telemetry than srsRAN's periodic metrics report already
provides, and (2) add a new RIC control capability - a direct per-UE bitrate cap - that srsRAN did not have
before. The work was motivated by comparing srsRAN's telemetry/control surface against a reference OAI-based
"Agentic RAN" design (`oai-release-agentran`), while deliberately choosing srsRAN-native mechanisms (E2SM-RC,
existing metrics pipelines) over reproducing OAI's Lua/Telnet/InfluxDB-specific approach.

**Repositories involved:**

| Repository | Role | Modified in this effort? |
|---|---|---|
| `srsRAN_Project` (this repo) | The RAN implementation (gNB: DU, CU-CP, CU-UP), including the MAC scheduler and E2 agent | **Yes** - see §C |
| [`oran-sc-ric`](https://github.com/srsran/oran-sc-ric) | The near-RT RIC platform + reference xApps | **Yes** (one new xApp, one library extension) - see §C |
| [`oai-release-agentran`](https://github.com/wineslab/oai-release-agentran) | OAI-based reference design, consulted for requirements | No - read-only reference |

**What the new xApp does.** `simple_bitrate_cap_xapp.py` demonstrates a new E2SM-RC control action that caps a
UE's sustained DL/UL bitrate directly (in kbps), as opposed to srsRAN's pre-existing PRB-quota control action
which caps resource *share* (%). See [§C in the audit document](./AGENTIC_RAN_DEVELOPMENT_AUDIT.md#c-new-xapp-technical-explanation)
for the full technical explanation.

**Main contributions:**
1. A native srsRAN per-slot, per-grant telemetry exporter (opt-in, `SRSRAN_UE_STATE_PIPE`).
2. A real gap fix in srsRAN's existing periodic metrics pipeline (`dl_hol_delay_ms`), plus discovery and
   documentation of a further pre-existing upstream gap that keeps it from populating today.
3. Live validation that srsRAN's existing PRB-quota E2SM-RC control action works end-to-end against a real
   near-RT RIC (nothing to build here - proof, not new code).
4. A new E2SM-RC control action (Style 2, vendor action ID 201) plus a from-scratch token-bucket scheduler
   mechanism enforcing a direct per-UE DL/UL bitrate cap. The DL direction was validated live with a
   repeatable throughput staircase matching requested targets within ~1-6%; the UL direction used the
   identical, direction-agnostic enforcement path but was not exercised by the demo xApp until a later
   session - see the design doc's UL extension note (top of file) for current live-validation status.
5. A demonstration xApp exercising the new action (now cycling both `max_dl_kbps` and `max_ul_kbps`), plus
   the necessary xApp-SDK-side wire-format support. A native srsRAN per-grant UL telemetry extension
   (`"ul_grants"`, mirroring the existing `"dl_grants"`) was added in the same later session.

## B. Architecture

```
                        ┌─────────────────────────────┐
                        │   O-RAN SC near-RT RIC       │
                        │  (oran-sc-ric, docker compose)│
                        │  e2term, e2mgr, submgr,       │
                        │  dbaas, appmgr, rtmgr_sim     │
                        └───────────────▲───────────────┘
                                        │ E2AP (SCTP)
                        ┌───────────────┴───────────────┐
                        │        srsRAN gNB (DU+CU)      │
                        │  MAC scheduler ── E2 agent      │
                        │  (E2SM-KPM, E2SM-RC, E2SM-CCC) │
                        └──────┬───────────────┬─────────┘
                    F1AP/NGAP  │               │ ZMQ (RF sim) or real SDR
                        ┌──────▼──────┐  ┌─────▼─────┐
                        │  Open5GS 5GC │  │   srsUE /  │
                        │ (docker)     │  │  COTS UE   │
                        └──────────────┘  └────────────┘

  gNB's remote_control (ws://<host>:8001) ──► Telegraf ──► InfluxDB ──► Grafana
                                                     (existing srsRAN metrics pipeline)

  xApps (python_xapp_runner container)
     simple_rc_xapp.py            ──E2SM-RC Style2/Action6──► PRB quota (pre-existing)
     simple_bitrate_cap_xapp.py   ──E2SM-RC Style2/Action201─► UE bitrate cap (NEW, this effort)
```

**srsRAN components involved:** the MAC scheduler (`lib/scheduler/`), the E2 agent and E2SM-RC service model
(`lib/e2/`), the DU manager's RIC-configuration procedure (`lib/du/du_high/du_manager/`), and the app-level JSON
metrics exporter (`apps/helpers/metrics/`).

**Open5GS**: used as the 5G Core (AMF/SMF/UPF/etc.), started via `docker/docker-compose.yml` in this repo (not
modified by this effort - pre-existing testbed infrastructure).

**O-RAN SC RIC components**: `e2term` (E2AP termination), `e2mgr` (E2 node bookkeeping), `submgr` (subscription
manager), `dbaas` (Redis-backed shared state), `appmgr`, `rtmgr_sim` (RMR route table simulator), and the
`python_xapp_runner` container hosting the Python xApps.

**Communication flow**: gNB ↔ RIC over E2AP/SCTP for both telemetry (E2SM-KPM subscribe/indicate) and control
(E2SM-RC RIC Control Request/Acknowledge). gNB ↔ 5GC over NGAP/N2 and GTP-U. gNB → Telegraf over a WebSocket
(`remote_control`), a separate, non-standard, srsRAN-native path used for the JSON metrics → InfluxDB → Grafana
pipeline (pre-existing, untouched by this effort, but the new `dl_hol_delay_ms` field flows through it with zero
config changes since Telegraf's field selection is generic).

## C. Repository modifications

Full per-file table with purpose/rationale: [`AGENTIC_RAN_DEVELOPMENT_AUDIT.md`, §A](./AGENTIC_RAN_DEVELOPMENT_AUDIT.md#a-repository-change-audit).
Summary:

- **`srsRAN_Project`**: 22 modified files + 4 new source files + 1 new design document, all currently uncommitted
  on top of commit `0adc739b95`. Two feature areas: (1) telemetry (`scheduler_ue_state_logger`, `dl_hol_delay_ms`)
  and (2) control (`ue_bitrate_limiter`, new E2SM-RC action, DU-manager threading).
- **`oran-sc-ric`**: 1 modified file (`lib/e2sm_rc_module.py`, purely additive) + 1 new xApp
  (`simple_bitrate_cap_xapp.py`). Note: the working tree also contains several **pre-existing, uncommitted local
  changes not made by this effort** - see the audit document for the full, honest breakdown of what is and isn't
  attributable to this work.
- **`oai-release-agentran`**: unmodified, read-only reference.

## D. Installation

### System requirements

Per upstream srsRAN (`to verify` against the root [`README.md`](../README.md) / `docs/BUILD.md` for your exact
OS/compiler version matrix): a Linux host, CMake ≥ 3.15, a C++20-capable compiler, and srsRAN's usual third-party
libraries (mbedTLS, fftw3, and either UHD or ZeroMQ if you intend to run against those RF drivers). This effort
adds **no new third-party dependencies**.

For the RIC and 5GC: Docker + Docker Compose (the `oran-sc-ric` and this repo's `docker/` testbed infra are both
Compose-based).

For the xApps: Python 3 with the packages already baked into the `python_xapp_runner` container image built by
`oran-sc-ric`'s own `ric/images/ric-plt-xapp-frame-py/Dockerfile` - no additional Python packages were introduced
by this effort's xApp.

### Cloning

```sh
git clone https://github.com/Mouradnetworking/srsran.git srsRAN_Project   # this fork
git clone https://github.com/srsran/oran-sc-ric.git
git clone https://github.com/wineslab/oai-release-agentran.git            # optional, reference only
```

See [`AGENTIC_RAN_DEVELOPMENT_AUDIT.md`, §B](./AGENTIC_RAN_DEVELOPMENT_AUDIT.md#b-step-by-step-reproduction-guide)
for exact base commits and how to apply this effort's patches on top of a clean checkout.

### Environment setup

Testbed-specific config files (gNB YAML, UE `.conf`) are **not** part of any of these git repositories in this
deployment - they live in a plain, non-version-controlled directory (`~/config/` in the reference environment).
`to verify`: decide whether to bring these under version control for your own reproduction; they are referenced
by absolute path from the `gnb.sh`/`ue.sh` wrapper scripts documented in `setup_run.md` (also not part of this
effort).

## E. Build instructions

### srsRAN

```sh
cd srsRAN_Project
mkdir -p build && cd build
cmake ..
cmake --build . --target gnb -j"$(nproc)"
```

Install where your testbed's wrapper scripts expect it (adjust to your own setup; the reference environment uses
`/usr/local/bin/gnb`, which requires `sudo`):

```sh
sudo cp build/apps/gnb/gnb /usr/local/bin/gnb.bak-$(date +%Y%m%d)   # back up first
sudo cp build/apps/gnb/gnb /usr/local/bin/gnb
sudo chmod 755 /usr/local/bin/gnb
```

### RIC / O-RAN SC components

```sh
cd oran-sc-ric
./create_ric_config_files.sh   # to verify: only if required by your checkout/version
docker compose build
```

### xApp

No separate build step - the xApps are plain Python scripts executed inside the already-built
`python_xapp_runner` container (see §G). If the container image itself needs rebuilding after a base-image or
dependency change:

```sh
docker compose build python_xapp_runner
```

## F. Testbed execution

All commands below assume the testbed layout documented in `setup_run.md` (not part of this effort, but the
environment this work was validated against). Adjust paths/IPs to your own deployment.

**1. Start the 5G core:**

```sh
cd srsRAN_Project/docker   # or wherever your docker-compose.yml for the 5GC lives
docker compose up 5gc -d
docker compose -f docker-compose.ui.yml up -d   # Telegraf/InfluxDB/Grafana monitoring stack
```

**2. Start the RIC:**

```sh
cd oran-sc-ric
docker compose up -d
```

**3. Start the gNB:**

```sh
export SRSRAN_UE_STATE_PIPE=/tmp/srsran_ue_state.pipe   # enables the new Layer 2 telemetry pipe (optional)
sudo SRSRAN_UE_STATE_PIPE=/tmp/srsran_ue_state.pipe gnb -c /path/to/gnb_zmq.yaml
```

Expected log lines confirming success: `N2: Connection to AMF ... completed`, `E2AP: Connection to
Near-RT-RIC ... completed`, `Remote control server listening on 0.0.0.0:8001`.

**4. Start the UE:**

```sh
sudo ip netns add ue1 2>/dev/null
sudo srsue /path/to/ue_zmq.conf
```

Expected: `PDU Session Establishment successful. IP: <ue-ip>`.

**5. Deploy/run an xApp:** see §G below.

**6. Monitoring/logging tools:** Grafana (pre-existing dashboard, reads from InfluxDB), or query InfluxDB
directly:

```sh
docker exec influxdb sh -c 'curl -s -X POST "http://127.0.0.1:8081/api/v3/query_sql" \
  -H "Authorization: Bearer <your InfluxDB token>" -H "Content-Type: application/json" \
  -d "{\"db\":\"srsran\",\"q\":\"SELECT * FROM ue ORDER BY time DESC LIMIT 5\"}"'
```

(InfluxDB is typically not published to the host in this testbed - hence querying from inside its own container.
`to verify` for your deployment.)

**7. Generate traffic:**

<!-- TODO(user): insert your traffic-generation command(s) here, e.g. iperf3 / ping invocations against the
     UE's PDU session IP. During this effort's validation, a combination of `ping` (light traffic) and `iperf3`
     (sustained DL saturation, both TCP and reverse-mode) against the UE's assigned PDU session IP was used -
     see AGENTIC_RAN_DEVELOPMENT_AUDIT.md §B.8 and the design document §14 for the exact invocations and the
     throughput results they produced. -->

**8. Verify connectivity:** confirm the UE's PDU session IP is reachable/routable per your UPF's NAT/routing
configuration (`to verify` - platform-specific), and confirm E2 Setup succeeded in the gNB log (step 3 above).

**9. Collect logs/metrics:** gNB log (path set in the gNB YAML config, `log.filename`), InfluxDB (step 6), and -
if `SRSRAN_UE_STATE_PIPE` is set - the Layer 2 telemetry FIFO (`cat` it from a reader process).

## G. Running the new xApp

**Configuration**: none required beyond command-line arguments - the xApp has no separate config file.

**How to start it**, from inside the RIC's xApp runner container:

```sh
docker compose exec python_xapp_runner bash
cd /opt/xApps
python3 simple_bitrate_cap_xapp.py \
  --e2_node_id <your gNB's E2 node ID> \
  --ran_func_id 3 \
  --gnb_cu_ue_f1ap_id 0
```

Find your gNB's E2 node ID via the E2 Manager REST API (from inside the `ric_e2mgr` container or wherever it's
reachable): `curl http://localhost:3800/v1/nodeb/states`. In the reference environment this was
`gnbd_001_001_00019b_0` (the script's default).

**Required environment variables**: none specific to this xApp. The container's own `RMR_SEED_RT`,
`RMR_SRC_ID`, `PYTHONUNBUFFERED`, `PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION` are set by `oran-sc-ric`'s
`docker-compose.yml` (pre-existing, not specific to this xApp).

**Required RIC services**: `e2term`, `e2mgr`, `submgr`, `rtmgr_sim`, `dbaas` all running (i.e. `docker compose up
-d` in `oran-sc-ric` completed successfully) - this xApp uses RMR directly for the control request and does not
need `appmgr` or a REST-based subscription manager interaction (it performs no subscription, only control).

**Expected logs**: on the gNB side (most reliable check - the xApp's own stdout may be buffered when redirected,
see the troubleshooting note below):

```
[E2-DU   ] [I] E2AP SDU, "initiatingMessage.RICcontrolRequest"
[E2-DU   ] [I] Received RIC Control Request
[E2SM-RC ] [I] UE Bitrate Cap Control Request for UE gNB-DU-UE-F1AP-ID=0: max_dl_kbps=5000 max_ul_kbps=unset
[E2-DU   ] [I] Sending E2 RIC Control Acknowledge
```

repeating every 5 seconds with `max_dl_kbps` cycling through 5000 → 15000 → 30000 → 60000.

**How to verify it's working**: with a saturating DL flow active (see §F.7), query InfluxDB for `dl_brate` over
the same time window and confirm it steps to match each requested cap (within roughly ±1-6%, per the validation
results in the audit document §A.1/§C.8).

## H. Experiment scenarios

| Scenario | What to change | What to observe |
|---|---|---|
| **Baseline, no xApp** | No control xApp running | `dl_brate` limited only by radio conditions/PRB availability - this is the reference throughput to compare all other scenarios against |
| **PRB quota xApp enabled** (pre-existing feature, good sanity check) | Run `simple_rc_xapp.py` | `dl_brate` steps with the PRB ratio, but the *exact* achieved value depends on current MCS/CQI, not just the ratio |
| **Bitrate cap xApp enabled** (new) | Run `simple_bitrate_cap_xapp.py` | `dl_brate`/`ul_brate` both step to match the requested `max_dl_kbps`/`max_ul_kbps`, largely independent of MCS/CQI (see §14.4 results; the xApp now cycles both directions together) |
| **UL traffic / UL telemetry** (new) | Generate UL traffic (non-reverse `iperf3` from the UE, or bidirectional), with `SRSRAN_UE_STATE_PIPE` set | Layer 2 pipe output now includes a populated `"ul_grants"` array (RNTI, HARQ id, MCS, RB allocation, retx count, PUSCH SINR/RSRP, HARQ state) alongside `"dl_grants"` - see the design doc §6/§7 |
| **Traffic variation** | Vary `iperf3` bandwidth/protocol (TCP vs UDP), or light (`ping`) vs. saturating traffic | With no traffic queued, `dl_bs`/`dl_hol_delay_ms` should be absent (not zero); under saturation, `dl_bs` should be consistently non-zero. There is no UL equivalent of `dl_hol_delay_ms` - the closest UL signal is `avg_sr_to_pusch_delay`/`max_sr_to_pusch_delay` (see design doc §9/§14.3) |
| **PRB variation** | Combine PRB-quota and bitrate-cap xApps on the same UE | Achieved rate should track the *lower* of what the PRB quota permits and the explicit kbps cap (see Use Case E.4) - note the full-replace caveat (§C.9): both must be sent together |
| **Congestion scenario** | Multiple UEs, all saturating | Confirm per-UE caps are enforced independently; no cross-UE interference from the token bucket (it's per-UE state) |
| **Control action scenario** | Toggle the cap on/off (`max_dl_kbps=None` is rejected by the xApp SDK - re-send a PRB-quota-only or very high cap to effectively lift it) | Achieved rate should return to the uncapped ceiling within one report interval |
| **Multi-user scenario** | Attach 2+ UEs, apply different caps to each | Verify via InfluxDB that each UE's `rnti`-tagged `dl_brate` series independently matches its own assigned cap |

## I. Troubleshooting

See also [`AGENTIC_RAN_DEVELOPMENT_AUDIT.md`, §B.9](./AGENTIC_RAN_DEVELOPMENT_AUDIT.md#b9-troubleshooting-notes)
for patch-application-specific issues.

- **Build errors after applying the patch**: confirm all 4 new files (`scheduler_ue_state_logger.{h,cpp}`,
  `ue_bitrate_limiter.{h,cpp}`) were copied in, not just the patch applied (`git diff` doesn't cover untracked
  new files).
- **Missing dependencies**: this effort introduced none beyond srsRAN's existing build requirements; a failure
  here points to your base srsRAN checkout/toolchain, not this patch.
- **RIC connection problems**: confirm `e2: enable_du_e2: true` and the RIC's E2 term address/port match your
  `docker-compose.yml`'s exposed IP (typically on the `oran-sc-ric_ric_network` Docker bridge, `to verify` its
  address in your deployment via `docker network inspect`).
- **srsRAN startup issues**: check the gNB log for the specific failing subsystem (N2/NGAP, E2AP, ZMQ RF) before
  assuming it's related to this patch - none of this effort's changes are on the startup/init path (they're all
  slot-processing or RIC-Control-Request-triggered).
- **xApp not receiving/sending messages**: the xApp's own stdout can appear empty when redirected to a file
  (Python's default full-buffering on non-TTY stdout) even though it's working correctly - **check the gNB log
  for the `RICcontrolRequest`/`Control Acknowledge` lines** rather than trusting an apparently-silent xApp log;
  run with `python3 -u` to disable buffering if you need to see xApp-side prints live.
- **Metrics not appearing** (`dl_hol_delay_ms` specifically): expected - see the pre-existing upstream gap
  documented in the audit §A.1 note and design doc §14.3. Other fields (CQI, SINR, `dl_brate`, etc.) should
  appear normally; if they don't, check `metrics.enable_json`/`metrics.layers.enable_sched` in the gNB config
  and confirm Telegraf's `ws_adapter.py` is connected to the gNB's `remote_control` port (8001 by default).
- **Permission/networking issues**: most privileged operations in this testbed (installing the `gnb` binary,
  starting `gnb`/`srsue`, creating network namespaces) require `sudo` and were run manually by a human operator
  during this effort's validation, since the automation environment used had no interactive sudo access -
  budget for manual steps if scripting this end-to-end.

## J. Reproducibility

To reproduce this exact development state from the original repositories:

1. Follow [`AGENTIC_RAN_DEVELOPMENT_AUDIT.md`, §B](./AGENTIC_RAN_DEVELOPMENT_AUDIT.md#b-step-by-step-reproduction-guide)
   to check out the base commits and apply the provided patches (`agentic_ran/patches/`).
2. Follow §D-§G above to build and run the resulting testbed.
3. Follow §F.7/§G to reproduce the live validation evidence cited in this README and the audit document (the
   InfluxDB throughput-staircase results for both the pre-existing PRB-quota action and the new bitrate-cap
   action).
4. Cross-check your results against the specific numeric evidence recorded in
   `AGENTIC_RAN_SRSRAN_TELEMETRY_DESIGN.md` §14.1 and §14.4 - exact throughput values will differ by environment
   (RF path, host performance, iperf3 parameters), but the *causal, repeatable staircase pattern* tracking the
   requested caps should reproduce.

All claims in this document and its companion audit are grounded in `git status`/`git diff`/`git log` output and
live-system log/database evidence gathered during this effort, not assumption - see the audit document's
"Scope and methodology" section for what was and wasn't independently re-verified for this specific write-up.
