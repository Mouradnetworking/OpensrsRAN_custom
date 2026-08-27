srsRAN Project — Agentic RAN Research Extension
================================================

This repository is a research extension of the official **[srsRAN Project](https://github.com/srsran/srsRAN_Project)**,
the open-source, O-RAN-native 4G/5G RAN implementation maintained by [SRS](http://www.srs.io). It keeps the
full srsRAN gNB (CU-CP, CU-UP, DU, L1/2/3) intact and adds a set of telemetry and RAN-control extensions on top
of it, built for experimenting with **programmable, closed-loop, and eventually autonomous/agentic RAN control**
via the O-RAN near-RT RIC and E2 interface.

If you are looking for vanilla, unmodified srsRAN, use the official upstream repository instead:
<https://github.com/srsran/srsRAN_Project>.

Unlike a typical fork, this repository bundles everything needed to run the whole experimental stack from a
single clone:

| Path | What it is |
|---|---|
| `srsRAN_4G/` | The UE (`srsue`), built from source |
| `oran-sc-ric/` | The O-RAN SC near-RT RIC platform and its Python xApps, run via Docker |
| `config/` | gNB/UE configuration files |
| `docker/` | Docker Compose files for the 5G Core (Open5GS) and the optional telemetry stack (Telegraf/InfluxDB/Grafana) |

You will also need a 5G Core reachable at the address configured in `config/gnb_zmq.yaml`'s `cu_cp.amf.addr` —
an Open5GS-compatible AMF. `docker/docker-compose.yml` provides one; any other Open5GS-compatible deployment
works as long as it is reachable at that address.

## 1. Motivation

Standard srsRAN already exposes a working O-RAN E2 interface (E2SM-KPM for telemetry, E2SM-RC for control), but
that interface has two practical limits for RAN-intelligence research:

- **Telemetry granularity.** The stock metrics pipeline reports one aggregated snapshot per UE per report
  period (typically ≥100 ms–1 s). That is enough to *observe* a cell, but not enough to see an individual
  scheduling decision — which HARQ process, which exact resource blocks, whether a grant was a
  retransmission — the kind of detail a learning-based or reasoning-based controller needs to *ground* its
  decisions in.
- **Control granularity.** The only pre-existing RIC control lever is a PRB-quota action (a *resource-share*
  cap, in %). Achieved throughput at a given PRB share still depends on channel quality, so nothing lets a RIC
  or an automated policy state an *outcome* directly — "cap this UE/bearer at X kbps," or "enforce this
  bearer's already-negotiated QoS guarantee" — without going through the full core-network signaling chain.

This project closes both gaps: a native, per-slot telemetry exporter for decision-level visibility, and two new
RIC control actions (a UE-aggregate bitrate cap and a standards-based per-DRB QoS enforcement mechanism) for
outcome-level control. Together they turn srsRAN + the O-RAN SC RIC into a platform suited for research on:

- Closed-loop network control and optimization
- Programmable and intelligent RANs
- Automated, per-bearer QoS management
- AI-driven or agentic network optimization (rule-based, RL-trained, or LLM-grounded controllers)
- Experimental O-RAN control applications (xApps) beyond the reference demos shipped with the RIC
- Telemetry-driven network-intelligence research more broadly, including adaptive 5G/6G network concepts

**What this repository is not**: it is not itself an autonomous or AI-driven RAN. No agent, LLM, or closed-loop
decision logic ships here. What is provided is the underlying *platform*, the telemetry export, the standards-
based and vendor-specific control actions, and the xApps needed to drive them, that such research would sit on
top of. Where a capability described below is a future direction rather than something implemented, it is
marked explicitly as such.

## 2. What this project adds

Everything below was verified against this repository's actual source and configuration files, not assumed from
prior familiarity with srsRAN.

- **Per-slot, per-grant Layer 2 telemetry**: (`lib/scheduler/logging/scheduler_ue_state_logger.{h,cpp}`) — an
  opt-in exporter, gated behind the `SRSRAN_UE_STATE_PIPE` environment variable, that writes one JSON line per
  slot to a named pipe: HARQ process id, exact RB allocation, retransmission count, live PUSCH SINR/RSRP, DL
  buffer occupancy, and RAN slice id, for every DL and UL grant. This did not exist in stock srsRAN — the
  existing metrics pipeline only reports aggregated, per-report-period snapshots. See [§5](#5-installation-and-dependencies)
  for build flags and [§6](#6-build-instructions) for how it's wired in.
  above.
- **DL Head-of-Line (HoL) delay telemetry fix**: `dl_hol_delay_ms` (`include/srsran/scheduler/scheduler_metrics.h`)
  reports the age of a UE's oldest queued DL PDU. The underlying value (`hol_toa`) was already computed by RLC
  but was silently dropped one layer up before reaching the metrics report or the scheduler's own delay-aware
  QoS prioritization logic (`scheduler_time_qos.cpp`). This project traces `hol_toa` end-to-end
  (RLC → `scheduler_metrics_handler` → JSON export), so the field now populates with real data and the
  pre-existing QoS scheduling policy that depends on it is active for the first time.
- **UE-aggregate bitrate cap**: a new, vendor-specific E2SM-RC control action (Style 2, Action ID 201,
  `lib/e2/e2sm/e2sm_rc/e2sm_rc_control_action_du_executor.h`) backed by a token-bucket rate limiter
  (`lib/scheduler/support/ue_bitrate_limiter.{h,cpp}`). Unlike the pre-existing PRB-quota action, this caps a
  UE's sustained DL/UL bitrate directly, in kbps, independent of channel conditions. See
  [§14](#14-optional-non-standardized-ue-bitrate-cap-xapp-style-2action-201) for how to run it.
- **Standards-based per-DRB QoS enforcement**: a second, standard E2SM-RC control action (Style 1, Action 1,
  "DRB QoS Configuration") that enforces a DRB's actual negotiated 5QI / Maximum Flow Bit Rate (MFBR) as a hard
  scheduler-side cap. This is this project's primary QoS mechanism; see [§11](#11-the-new-qos-mechanism-standards-based-per-drb-gbrmfbr-enforcement)
  for the full technical explanation.


## 3. Repository layout for this fork

```
srsRAN_Project/
├── apps/, lib/, include/       srsRAN gNB source (this project's changes live inside lib/scheduler,
│                               lib/e2, lib/cu_cp, include/srsran/scheduler)
├── config/                     gNB/UE config for the ZMQ RF-simulator testbed
├── docker/                     Open5GS (5G Core) and telemetry-stack (Telegraf/InfluxDB/Grafana) Compose files
├── srsRAN_4G/                  The UE (srsue), a separate srsRAN codebase bundled here for convenience
├── oran-sc-ric/                O-RAN SC near-RT RIC platform + Python xApps (run via Docker)
└── docs/                       Standards Documentations
```

## 4. What is stable vs. experimental

| Mechanism | Standard? | Status |
|---|---|---|
| PRB-quota control (Style 2 / Action 6) | Yes, pre-existing O-RAN standard action | Stable (unmodified upstream capability) |
| DL Layer 1 telemetry incl. `dl_hol_delay_ms` | N/A (telemetry) | Stable, live-verified |
| Layer 2 per-grant telemetry (`SRSRAN_UE_STATE_PIPE`) | N/A (experimental, srsRAN-native format) | Opt-in, functional, not wired to any long-term storage |
| UE-aggregate bitrate cap (Style 2 / Action 201) | No — vendor-specific extension | Functional, live-validated for DL |
| Per-DRB GBR/MFBR enforcement (Style 1 / Action 1) | Yes, standard O-RAN action | UL and DL both functional and live-validated. |

## 5. Installation and dependencies

These are the packages needed to build the gNB (this repository's root) and the UE (`srsRAN_4G/`). The RIC
(`oran-sc-ric/`) needs no host-side build — it runs entirely inside Docker.

**Build tools**
* CMake ≥ 3.15 — <https://cmake.org/>
* A C++20-capable compiler (GCC or Clang)
* `make`, `pkg-config`

**Mandatory libraries**
* `libfftw3` — <https://www.fftw.org/>
* `libsctp` — <https://github.com/sctp/lksctp-tools>
* `yaml-cpp` — <https://github.com/jbeder/yaml-cpp>
* `mbedTLS` — <https://www.trustedfirmware.org/projects/mbed-tls/>

**Radio-frequend front-end (this testbed uses ZeroMQ, not real hardware)**
* `ZeroMQ` (`libzmq`) — needed by both the gNB and `srsue` for the ZMQ RF-simulator driver used throughout this
  README's testbed. See [§7](#7-zeromq-installation) below.

**Optional**
* `Docker` + `Docker Compose` — required for the 5G Core and the RIC, both of which run as containers
* `googletest` — only if you build with `-DBUILD_TESTING=On`

Install the build tools and mandatory libraries for your distribution:

<details open>
<summary><strong>Ubuntu 22.04 / 24.04</strong></summary>

```bash
sudo apt-get update
sudo apt-get install -y cmake make gcc g++ pkg-config \
    libfftw3-dev libmbedtls-dev libsctp-dev libyaml-cpp-dev
```

</details>

<details>
<summary><strong>Fedora</strong></summary>

```bash
sudo yum install cmake make gcc gcc-c++ fftw-devel lksctp-tools-devel yaml-cpp-devel mbedtls-devel
```

</details>

<details>
<summary><strong>Arch Linux</strong></summary>

```bash
sudo pacman -S cmake make base-devel fftw mbedtls yaml-cpp lksctp-tools
```

</details>

## 6. Build instructions

### 6.1 Clone the repository

```bash
git clone https://github.com/Mouradnetworking/srsran.git srsRAN_Project
cd srsRAN_Project
```

This single clone includes `srsRAN_4G/` (the UE) and `oran-sc-ric/` (the RIC + xApps) as plain subdirectories —
there is nothing else to clone separately for the software components.

### 6.2 Build the gNB (repository root)

```bash
mkdir -p build && cd build
cmake .. -DENABLE_EXPORT=ON -DENABLE_ZEROMQ=ON -DCMAKE_BUILD_TYPE=Release
make -j"$(nproc)"
```

`-DENABLE_ZEROMQ=ON` is required for the ZMQ RF-simulator testbed documented below. `-DENABLE_EXPORT=ON` enables
the JSON metrics exporter that this project's telemetry additions (including `dl_hol_delay_ms`) flow through.

Check the `cmake` output for this line — if it's missing, ZMQ was not detected and the RF driver used
throughout this README will not be available:
```
-- Found libZEROMQ: /usr/local/include, /usr/local/lib/libzmq.so
```

Install the resulting binary system-wide so it can be run from any directory (see
[§8](#8-install-the-gnb-binary-and-set-up-environment-variables) for why this matters and how to keep it in
sync after a rebuild):

```bash
sudo make install
```

Other build configurations (Split-7.2/DPDK, real-SDR Split-8) are unchanged from upstream srsRAN and are not
this project's focus — see the upstream [build documentation](https://docs.srsran.com/projects/project/en/latest/general/source/1_installation.html)
if you need them.

### 6.3 Build the UE (`srsRAN_4G/`)

```bash
cd srsRAN_4G
mkdir -p build && cd build
cmake .. -DENABLE_ZEROMQ=ON
make -j"$(nproc)"
sudo make install   # optional — otherwise run from build/srsue/src/srsue
```

If you rebuild `srsue` after pulling new changes and the new binary doesn't seem to take effect, delete the
stale binary first before rebuilding:
```bash
rm -f build/srsue/src/srsue && make -C build srsue -j"$(nproc)"
```

### 6.4 The RIC (`oran-sc-ric/`)

No manual build step — Docker Compose builds/pulls everything the first time you bring it up. See
[§8](#8-install-the-gnb-binary-and-set-up-environment-variables) and [§10](#10-starting-the-network) for the
run order.

## 7. ZeroMQ installation

ZeroMQ (`libzmq`) is the RF driver this entire testbed is built around — both the gNB and `srsue` link against
it to communicate over a local loopback "radio" instead of real RF hardware.

Most distributions package it directly:

```bash
sudo apt-get install -y libzmq3-dev      # Debian/Ubuntu
```

If you need to build it from source instead (e.g. for a newer version), follow the official instructions at
<https://zeromq.org/> and install into `/usr/local`. If you do so, add the following to your shell profile so
both the gNB and `srsue` can find it at runtime:

```bash
echo 'export LD_LIBRARY_PATH=/usr/local/lib' >> ~/.bashrc
source ~/.bashrc
```

If ZMQ was installed via your distribution's package manager instead, this step is not necessary.

## 8. Install the gNB binary and set up environment variables

### 8.1 Environment variables

Only one environment variable is required, and only if you built ZeroMQ (or another dependency) from source
into `/usr/local/lib` rather than via your package manager (see [§7](#7-zeromq-installation)):

```bash
export LD_LIBRARY_PATH=/usr/local/lib
```

Add this to `~/.bashrc` so it persists across sessions rather than exporting it manually every time. 

### 8.2 Put the gNB binary on your `PATH`

`sudo make install` (§6.2) installs the `gnb` binary to `/usr/local/bin/gnb`, which is on the default `PATH` on
most Linux distributions — after this, `gnb` (or `sudo gnb`, since binding to the AMF/E2 interfaces and running
with real-time scheduling typically requires elevated privileges) can be invoked from any directory.

**Important**: `/usr/local/bin/gnb` does **not** auto-update when you rebuild. After every rebuild, either
re-run `sudo make install` from `build/`, or copy the binary manually:

```bash
sudo cp build/apps/gnb/gnb /usr/local/bin/gnb
```

Forgetting this step is a common source of "I made a change but nothing happened" confusion — always reinstall
after a rebuild before starting a new experiment.

### 8.3 Recommended: `start5gc` / `startric` shell functions

To bring the 5G Core and the RIC up with a single command each (rather than remembering the exact `docker
compose` invocation and directory each time), add functions like these to `~/.bashrc`, adjusting the paths to
match where you cloned this repository:

```bash
start5gc() {
    (cd ~/srsRAN_Project/docker && docker compose up -d)
}

startric() {
    (cd ~/srsRAN_Project/oran-sc-ric && ./create_ric_config_files.sh && docker compose up -d)
}
```

`create_ric_config_files.sh` regenerates `oran-sc-ric/ric/configs/routes.rtg` from `oran-sc-ric/.env` — it only
needs to actually change anything if you've edited `.env`, but it is cheap to run every time.

After `source ~/.bashrc`, starting the network (§10) becomes:

```bash
start5gc
startric
```

## 9. UE provisioning: adding the GBR test user

The QoS mechanism documented in [§11](#11-the-new-qos-mechanism-standards-based-per-drb-gbrmfbr-enforcement)
only has something meaningful to enforce if the attached UE has a real GBR (Guaranteed Bit Rate) QoS profile
negotiated at PDU session establishment — a default best-effort subscriber has no GBR/MFBR value to enforce.
This step provisions such a subscriber in Open5GS's subscriber database.

### 9.1 Add the subscriber

With the 5G Core container running (`start5gc`, see [§10](#10-starting-the-network)):

```bash
docker exec open5gs_5gc python3 -c "
import sys; sys.path.insert(0, '/open5gs')
from add_users import add_user
from misc.db.python.Open5GS import Open5GS
imsi = '001010000059478'
assert len(imsi) == 15, f'IMSI length is {len(imsi)}, expected 15'
sub = add_user(imsi, key='00112233445566778899aabbccddeeff',
                opc='63bfa50ee6523365ff14c1f45f88737d', amf='8000', qci='1', ip_alloc='10.45.1.2')
assert sub['imsi'] == imsi
print(Open5GS('127.0.0.1', 27017).AddSubscriber(sub))
"
```

`qci='1'` provisions this subscriber's default bearer with 5QI 1 (Conversational Voice), a standard GBR QoS
class — this is what gives the subscriber a real GBR/MFBR profile for the enforcement mechanism to act on.

### 9.2 Verify the subscriber was provisioned correctly

**Always re-verify by reading the record back — do not trust the success message alone.** An IMSI with an
incorrect length can silently register as a *different* subscriber, producing a confusing `Registration reject`
failure much later that looks unrelated to provisioning:

```bash
docker exec open5gs_5gc python3 -c "
import pymongo
db = pymongo.MongoClient('mongodb://127.0.0.1:27017')['open5gs']
s = db.subscribers.find_one({'imsi': '001010000059478'})
print('found:', s is not None, '| imsi len:', len(s['imsi']) if s else None)
"
```

Expect `found: True | imsi len: 15`.

Alternatively, the Open5GS WebUI (see [`docker/open5gs/README.md`](./docker/open5gs/README.md)) is reachable at
`http://localhost:9999` and lets you inspect/add subscribers interactively.

The matching UE configuration is already provided at `config/ue_zmq_gbr.conf` — its `imsi`, `k`, and `opc`
fields match the subscriber above, and `apn = srsapn` matches that subscriber's provisioned session.

## 10. Starting the network

Start components in this order — each depends on the one before it being ready.

### 10.1 Start the 5G Core

```bash
start5gc
```
(equivalent to `cd docker && docker compose up -d`, see [§8.3](#83-recommended-start5gc-startric-shell-functions))

### 10.2 Start the near-RT RIC

```bash
startric
```

Wait for all containers to report healthy before starting the gNB:
```bash
cd oran-sc-ric && docker compose ps
```
The gNB's E2 agents connect to the RIC's `e2term` on startup and do not retry indefinitely if it isn't ready.

If this gNB has previously connected to the same RIC without a graceful E2 release (a recurring RIC-state
issue, not specific to this project), bounce the RIC's E2-facing containers first:
```bash
docker restart ric_e2term ric_e2mgr ric_submgr ric_dbaas
```

### 10.3 Start the gNB

```bash
sudo gnb -c config/gnb_zmq.yaml
```

Confirm the E2 connection came up in the gNB log (`filename:` under `log:` in `gnb_zmq.yaml`, default
`/home/<you>/logs/srsgnb.log`) — look for the E2 Setup Response and, for the QoS mechanism in
[§11](#11-the-new-qos-mechanism-standards-based-per-drb-gbrmfbr-enforcement) specifically, confirm
`e2.enable_cu_cp_e2: true` and `e2.e2sm_rc_enabled: true` are set in the config (they are, by default, in
`config/gnb_zmq.yaml`).

### 10.4 Attach the UE

For the GBR test UE provisioned in [§9](#9-ue-provisioning-adding-the-gbr-test-user):
```bash
sudo ip netns add ue1   # only if it doesn't already exist
sudo srsue config/ue_zmq_gbr.conf
```

Confirm attachment on the UE console (`RRC Connected` → `PDU Session Establishment successful` → `RRC NR
reconfiguration successful`) and on the gNB log (`rrcSetupComplete`, `UEContextSetupResponse`,
`PDUSessionResourceSetupResponse`).

### 10.5 Using the network

Once the UE is attached, you can:
- Retrieve the AMF UE NGAP ID needed by the QoS xApp — [§12](#12-amf-id-retrieval)
- Launch the QoS xApp — [§13](#13-running-the-qos-xapp)
- Generate traffic through the network — [§15](#15-generating-traffic-with-iperf3)

Tear down in reverse order: UE, then gNB, then `docker compose down` in `oran-sc-ric/`, then in `docker/`.

## 11. The new QoS mechanism: standards-based per-DRB GBR/MFBR enforcement

### 11.1 What it is

A control mechanism that enforces a Data Radio Bearer's (DRB's) already-negotiated 3GPP QoS profile, its 5QI
and Maximum Flow Bit Rate (MFBR), DL and UL, as a hard, real-time cap on that specific bearer's scheduler
grants. It is reachable through the standard O-RAN E2SM-RC control service: **Style 1 (Radio Bearer Control),
Action 1 ("DRB QoS Configuration")**, as defined in O-RAN.WG3.E2SM-RC.

### 11.2 What problem it addresses

srsRAN already carries a UE's GBR QoS profile end-to-end at bearer setup time (the 5QI and MFBR values flow
through the DU/CU signaling chain and are used for admission decisions and for weighting the scheduler's QoS
policy), but before this project nothing in the scheduler *enforced* that MFBR as a hard cap on the bearer's
granted bytes. 

The pre-existing bitrate cap in this project (Style 2 / Action 201, [§14](#14-optional-non-standardized-ue-bitrate-cap-xapp-style-2action-201))
does not close this gap: it caps an entire UE, not a specific bearer, it is a vendor-specific action a
standards-only RIC controller would not recognize, and its value is an arbitrary operator-chosen number rather
than the bearer's actual negotiated QoS contract.

### 11.3 Role in the overall architecture

The mechanism lives at the intersection of three architectural layers:

- **The RAN**: the DU's MAC scheduler is where enforcement actually happens — a per-DRB (downlink) / per
  Logical-Channel-Group (uplink) token-bucket rate limiter clamps how many bytes that bearer's grants may
  contain per slot, reusing the same limiter mechanism the UE-aggregate bitrate cap already uses, scoped
  narrower.
- **The RIC/E2 control plane**: an xApp issues a standard E2SM-RC `RIC CONTROL REQUEST` (Style 1, Action 1) that
  the gNB's CU-CP-side E2 agent receives and translates into an internal DU-manager reconfiguration request.
  This is the path documented and used in this README.
- **Telemetry**: the effect of a change is observable both through the existing Layer 1 metrics pipeline
  (achieved throughput..) and, at finer granularity, through the Layer 2
  per-grant telemetry ([§2](#2-what-this-project-adds)) if it is enabled or using iperf.

### 11.4 Workflow

1. An xApp running on the near-RT RIC sends an E2SM-RC `RIC CONTROL REQUEST` (Style 1, Action 1) identifying the
   target UE (by its full gNB-UEID tuple, see [§12](#12-amf-id-retrieval)) and DRB, carrying the DRB ID and,
   optionally, the 5QI and/or Maximum Flow Bit Rate DL/UL to set. Unset fields retain their current value,
   this action supports partial updates.
2. The gNB's CU-CP-side E2 agent dispatches the request to the corresponding control-action executor, which
   translates it into an internal QoS-flow modification request.
3. That request is applied through the same signaling and configuration path the gNB already uses for any
   bearer QoS update: it reaches the CU-UP (updating the flow's stored QoS context) and the DU manager
   (updating the DRB's MAC-layer logical-channel configuration) via the existing internal control-plane
   procedures, no new protocol messages were introduced for this step.
4. The DU manager pushes the updated MFBR value into the MAC scheduler's per-DRB/per-LCG rate limiter. This is
   the single point where the value actually takes effect; every subsequent slot, the scheduler clamps that
   bearer's grant size against the limiter before allocating any bytes.
5. Whether an over-the-air RRC reconfiguration is needed depends on what changed: a pure QoS/MFBR update (no
   change to PDCP/SDAP configuration) does not require signaling the UE at all, enforcement is entirely
   internal to the gNB's scheduler from that point forward.

<!-- TODO: Add QoS architecture/workflow diagram here -->

### 11.5 Downlink vs. uplink enforcement precision

This distinction matters for interpreting results and is a genuine 3GPP protocol property, not an
implementation shortcut:

- **Downlink is exact.** The gNB itself builds the PDSCH transport block, subPDU by subPDU, per logical
  channel, so the DRB-level cap applies to that DRB alone, with no effect on any other bearer belonging to the
  same UE.
- **Uplink is scoped to the Logical Channel Group (LCG), not the DRB.** TS 38.321's Logical Channel
  Prioritization procedure, which decides, within an uplink grant, how many bytes come from which logical
  channel, runs in the **UE**, not the gNB. The gNB can only grant an LCG a certain number of bytes in total;
  it cannot single out one DRB's share of that grant if the LCG is shared with other logical channels. If the
  GBR DRB has its LCG entirely to itself, the LCG cap is an exact per-DRB cap. If the LCG is shared, the cap
  still correctly bounds the LCG's aggregate uplink rate, but cannot guarantee that one DRB's individual share
  of it.

## 12. AMF ID retrieval

### 12.1 What it represents

The **AMF UE NGAP ID** is the identifier the AMF assigns to a UE's NGAP association when it registers with the
core network. It is not a fixed configuration value, it is assigned per UE, per attach, at runtime. The Style
1/Action 1 control action ([§11](#11-the-new-qos-mechanism-standards-based-per-drb-gbrmfbr-enforcement)) needs
it because that action identifies its target UE by the full CU-side `gNB-UEID` tuple (AMF UE NGAP ID + GUAMI +
gNB-CU-UE-F1AP-ID), unlike the DU-side Style 2 actions, which only need the local `gNB-CU-UE-F1AP-ID`.

### 12.2 Where to obtain it

After the UE attaches ([§10.4](#104-attach-the-ue)), find the assigned AMF UE NGAP ID in either:

- **The gNB log** — search for the F1AP/NGAP association lines around the UE's attach, or grep for `amf_ue=`
  in the gNB's log file (`filename:` under `log:` in `gnb_zmq.yaml`).
- **Open5GS's AMF log**, which prints the association explicitly:
  ```
  RAN_UE_NGAP_ID[0] AMF_UE_NGAP_ID[<N>]
  ```
  where `<N>` is the value you need. Inside the 5GC container:
  ```bash
  docker logs open5gs_5gc | grep AMF_UE_NGAP_ID
  ```

### 12.3 How it's passed to the QoS xApp

The retrieved integer `<N>` is passed as the `--amf_ue_ngap_id` argument documented in
[§13](#13-running-the-qos-xapp) below.

## 13. Running the QoS xApp

For exercising the per-DRB GBR/MFBR mechanism ([§11](#11-the-new-qos-mechanism-standards-based-per-drb-gbrmfbr-enforcement))
the files are **`ul_mfbr_test_xapp.py`** (uplink) and **`dl_mfbr_test_xapp.py`** (downlink), both in
`oran-sc-ric/xApps/python/`.

### 13.1 Prerequisites

- The network is up and the UE is attached ([§10](#10-starting-the-network)).
- You have the UE's `--amf_ue_ngap_id` ([§12](#12-amf-id-retrieval)).
- You know the active DRB ID (default `1` for the single-DRB testbed configuration used throughout this
  README).

### 13.2 Launch

Both scripts run inside the RIC's xApp runner container:

**Downlink, one-shot** (`dl_mfbr_test_xapp.py`), applies a DL cap, leaving UL uncapped:
```bash
docker compose exec python_xapp_runner python3 dl_mfbr_test_xapp.py \
    --amf_ue_ngap_id <N> \
    --max_dl_kbps 3000 \
    --hold_seconds 60
```

**Uplink, interactive** (`ul_mfbr_test_xapp.py`), applies a UL cap, leaving DL uncapped:
```bash
docker compose exec -it python_xapp_runner python3 ul_mfbr_test_xapp.py \
    --amf_ue_ngap_id <N> \
    --max_ul_kbps 2000 \
    --observe_seconds 60
```


### 13.3 Verify it's running correctly

On launch, both scripts print the RIC Control Request they sent, e.g.:
```
[APPLY - 2000kbps cap] Sending RIC Control Request: max_ul_kbps=2000 (DL left uncapped: 1000000)
```
On the gNB side, confirm the request was received and applied in the gNB log:
```
[E2-CU-CP] Received RIC Control Request
[E2SM-RC ] ue=0: Executing E2SM-RC DRB QoS Configuration for DRB1
```

### 13.4 Observe its effect

Generate traffic through the UE ([§15](#15-generating-traffic-with-iperf3)) and observe the achieved rate
converge toward the configured cap. **Do not use the gNB's live PHY throughput console metric** as your
measurement — it includes MAC padding and can look unthrottled even when the true delivered application rate
is correctly capped. Measure delivered bytes over wall-clock time at the application layer (e.g. `iperf3`'s own
reported throughput) instead.

## 14. Optional: non-standardized UE bitrate-cap xApp (Style 2/Action 201)

### 14.1 What it does

`simple_bitrate_cap_xapp.py` (`oran-sc-ric/xApps/python/`) drives the other control action added by this
project: a direct cap on a UE's *entire* sustained DL/UL bitrate (not scoped to a single DRB), via E2SM-RC
Style 2, Action ID 201.

### 14.2 Why it's non-standardized/experimental

There is no standard Style 2 action for a direct bitrate cap (the closest standard action, PRB quota, caps a resource share, not a rate). It is
explicitly a vendor-specific extension: an xApp built against a different vendor's E2 node would not recognize
it.

### 14.3 How to launch

```bash
docker compose exec python_xapp_runner python3 simple_bitrate_cap_xapp.py \
    --e2_node_id <your gNB's DU-side E2 node id> \
    --gnb_cu_ue_f1ap_id <ID>
```

This action identifies the UE by its DU-local `gNB-CU-UE-F1AP-ID` only (unlike the DRB QoS action, it does
**not** need an AMF UE NGAP ID) — find that ID the same way you would find any DU-side UE index, e.g. in the
gNB log around attach.

### 14.4 How it differs from the main QoS xApp

| | `simple_bitrate_cap_xapp.py` (Style 2/201) | `ul_mfbr_test_xapp.py` / `dl_mfbr_test_xapp.py` (Style 1/1) |
|---|---|---|
| Scope | Whole UE (DL and/or UL aggregate) | A single DRB |
| Standard? | No, vendor extension | Yes, standard O-RAN action |
| Value source | Arbitrary, operator/xApp-chosen | Meant to reflect the bearer's negotiated QoS profile |
| UE identification | `gNB-CU-UE-F1AP-ID` only | Full `gNB-UEID` tuple (needs AMF UE NGAP ID) |

## 15. Generating traffic with iperf3

To run the server: ```stdbuf -oL iperf3 -s -i 1 -B 10.53.1.1```
To run the client: ```sudo ip netns exec ue1 iperf3 -c 10.53.1.1 -u -b 10M -t 60 -i 1 2>&```


## 16. Experiment workflow

End-to-end summary of the steps above, in order:

1. Install dependencies ([§5](#5-installation-and-dependencies)).
2. Build the gNB and the UE ([§6](#6-build-instructions)).
3. Install ZeroMQ if needed ([§7](#7-zeromq-installation)).
4. Install the gNB binary and set up environment variables / shell functions ([§8](#8-install-the-gnb-binary-and-set-up-environment-variables)).
5. Provision the GBR test subscriber ([§9](#9-ue-provisioning-adding-the-gbr-test-user)).
6. Start the 5G Core, the RIC, the gNB, and attach the UE, in order ([§10](#10-starting-the-network)).
7. Retrieve the UE's AMF UE NGAP ID ([§12](#12-amf-id-retrieval)).
8. Launch the stable QoS xApp for the direction you want to test ([§13](#13-running-the-qos-xapp)).
9. Generate traffic through the UE ([§15](#15-generating-traffic-with-iperf3)).
10. Observe the RAN-side telemetry and the achieved application-layer throughput to confirm enforcement.
11. Analyze the resulting behavior (e.g., cap accuracy, convergence time, any RLC/PDCP-level side effects).

**Future work and extensions:**

- A forwarder for the Layer 2 per-grant telemetry pipe into a networked time-series store, matching the
  existing Layer 1 → Telegraf → InfluxDB → Grafana path.
- Multi-UE and multi-cell validation of both control actions.
- A thin control-plane API (REST/gRPC, or a long-running service) wrapping the existing E2SM-RC control actions,
  making them directly callable by an external decision layer (e.g., an RL-trained, or LLM-grounded
  agent) instead of requiring a bespoke Python xApp script per experiment.
