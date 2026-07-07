# Agentic RAN Development Audit

Role of this document: a research-grade audit of every change made across the three repositories touched by this
effort, a step-by-step reproduction guide from clean upstream, a technical explanation of the new xApp, a novelty
check against existing work, and a set of concrete use cases. Companion document: `agentic_ran/README.md` (how to
run the whole stack). Companion patches: `agentic_ran/patches/`.

## Scope and methodology (read this first)

- All claims below are backed by `git status`, `git diff`, `git log`, and direct file reads performed during this
  audit - not recollection from earlier in the working session. Commands used are shown inline so they can be
  re-run to verify.
- Three repositories are in scope:
  1. **`srsRAN_Project`** (this repo) - remote `origin` = `https://github.com/Mouradnetworking/srsran.git`, a
     personal fork of srsRAN. Branch `main`, HEAD at commit `0adc739b95`.
  2. **`oran-sc-ric`** (`~/oran-sc-ric`) - remote `origin` = `https://github.com/srsran/oran-sc-ric.git`, the
     **official** srsRAN-maintained reference RIC/xApp repository (not a fork). Branch `main`, up to date with
     `origin/main` at the time of this audit.
  3. **`oai-release-agentran`** (`~/oai-release-agentran`) - remote `origin` =
     `https://github.com/wineslab/oai-release-agentran.git`. **Read-only reference**: consulted for design ideas
     early in this effort (see `AGENTIC_RAN_SRSRAN_TELEMETRY_DESIGN.md` §2-3), but **no files in this repository
     were changed**. `git status` there was not re-verified for this audit since it is out of scope for changes;
     if a full audit of it is required, that is a separate, explicit task.
- **Important finding on `srsRAN_Project`'s history**: the current `HEAD` commit, `0adc739b95` ("first comit"),
  is **not** a normal source commit - `git show --stat 0adc739b95` shows 5,786 changed files, of which:
  - the overwhelming majority are the contents of `build/` (compiled objects, CMake cache, generated Makefiles) -
    this appears to be an accidental commit of build artifacts into version control and should be reverted /
    added to `.gitignore` independent of this effort;
  - a small, legitimate set are testbed infrastructure files under `docker/` (`docker-compose.yml`,
    `docker-compose.ui.yml`, `docker/open5gs/*`, `docker/telegraf/*`, `docker/grafana/*`) - this is the Open5GS +
    monitoring stack (Telegraf/InfluxDB/Grafana) used by the live testbed described in `setup_run.md`.
  - The commit immediately before it, `3ed363dabf` ("Update main"), is a clean, ordinary srsRAN source commit and
    is the **recommended base commit** for reproduction (see §B).
- **All of the source-code work described in this audit exists only as uncommitted working-tree changes** on top
  of `0adc739b95` in `srsRAN_Project`, and as uncommitted working-tree changes / untracked new files in
  `oran-sc-ric`. Nothing was committed during this effort (per instructions, commits are only made when the user
  explicitly asks).
- **Attribution discipline**: `oran-sc-ric`'s working tree contains several modified/untracked files that were
  **not** touched during this effort (pre-existing local changes, likely from the user's own earlier work). These
  are listed and clearly marked "not part of this effort" in §A - they are documented for completeness and
  honesty, not claimed as part of the Agentic RAN contribution.

---

## A. Repository change audit

### A.1 `srsRAN_Project` - summary

22 tracked files modified, 4 new source files, 1 new design document, all uncommitted on top of `0adc739b95`.
Verified via `git status --short -- . ':!build'` and `git diff --stat -- . ':!build'`
(`22 files changed, 320 insertions(+), 38 deletions(-)` for tracked files; new files counted separately below).

| Repository | File/Folder | Change Type | Purpose | Why Needed | Essential? |
|---|---|---|---|---|---|
| srsRAN_Project | `lib/scheduler/logging/scheduler_ue_state_logger.h` | New (83 lines) | Declares `scheduler_ue_state_logger`: an opt-in, per-slot, per-DL-grant JSON telemetry exporter | Give a future "grounding agent" sub-report-interval visibility (HARQ id, exact RB allocation, RAN slice id) that the existing periodic metrics report can't provide | Yes - core of telemetry Layer 2 |
| srsRAN_Project | `lib/scheduler/logging/scheduler_ue_state_logger.cpp` | New (238 lines) | Implements the exporter: a custom non-blocking FIFO srslog sink, JSON serialization of one line per slot | Same as above; non-blocking design so a slow/absent reader can never stall the scheduler thread | Yes |
| srsRAN_Project | `lib/scheduler/logging/CMakeLists.txt` | Modified (+5/-2) | Adds the new `.cpp` to the build, links `sched_ue_context` | Required for the new files above to compile | Yes (build dependency of the above) |
| srsRAN_Project | `lib/scheduler/cell_scheduler.h` | Modified (+6/-1) | Adds a `scheduler_ue_state_logger` member to `cell_scheduler` | Gives the per-cell scheduler an owned instance of the exporter | Yes |
| srsRAN_Project | `lib/scheduler/cell_scheduler.cpp` | Modified (+3) | Calls `log_slot_result()` once per slot, after the existing metrics push | Wires the exporter into the real per-slot call path | Yes |
| srsRAN_Project | `lib/scheduler/ue_context/ue_channel_state_manager.h` | Modified (+22/-1) | Adds `has_pusch_snr_report()` and `update_pusch_rsrp()`/`get_pusch_rsrp()` | Layer 2 must distinguish a real PHY-reported SINR from the config-seeded default, and needs a live RSRP snapshot separate from the periodic averaged one | Yes - prevents fabricated-looking telemetry |
| srsRAN_Project | `lib/scheduler/ue_context/ue_channel_state_manager.cpp` | Modified (+3/-1) | Sets the "real report received" flag inside `update_pusch_snr()` | Implementation of the above | Yes |
| srsRAN_Project | `lib/scheduler/ue_context/ue_cell.cpp` | Modified (+7) | (a) forwards `crc_pdu.ul_rsrp_dBFS` into channel-state (telemetry); (b) calls `dl_bitrate_limiter_.set_cap()`/`ul_bitrate_limiter_.set_cap()` on reconfiguration (control) | (a) that field already existed on the CRC indication but was never read; (b) applies a RIC-configured bitrate cap to the per-UE token bucket | Yes (dual-purpose file) |
| srsRAN_Project | `lib/scheduler/ue_scheduling/ue_scheduler.h` | Modified (+10) | Adds `virtual const ue_cell* find_ue_cell(du_ue_index_t) const = 0;` | Read-only accessor so the telemetry exporter (a "spectator") can read live UE state without joining the scheduler's internal call graph or being able to mutate it | Yes |
| srsRAN_Project | `lib/scheduler/ue_scheduling/ue_scheduler_impl.h` | Modified (+2) | Implements `find_ue_cell()` via the existing `ue_cell_db.find()` | Implementation of the above; no new storage | Yes |
| srsRAN_Project | `include/srsran/scheduler/scheduler_metrics.h` | Modified (+4) | Adds `std::optional<float> dl_hol_delay_ms` to `scheduler_ue_metrics` | Surface the DL head-of-line queueing delay, a metric already computed by RLC but silently dropped before this change (see §A.1 finding below) | Yes for the field; **currently unpopulated end-to-end** - see the note below the table |
| srsRAN_Project | `lib/scheduler/logging/scheduler_metrics_handler.h` | Modified (+26 net) | Adds a `last_dl_hol_toa` array (parallel to the existing `last_dl_bs`), changes `compute_report()`'s signature to take the current slot | Needed to compute HOL delay = current slot − oldest pending PDU's arrival slot | Yes |
| srsRAN_Project | `lib/scheduler/logging/scheduler_metrics_handler.cpp` | Modified (+23 net) | Stores `hol_toa` alongside `bs` on every DL buffer-state indication; computes the age of the oldest non-empty logical channel at report time | Implementation of the above; **no fabricated zero** - the field is only set when real pending data exists | Yes |
| srsRAN_Project | `apps/helpers/metrics/json_generators/du_high/scheduler.cpp` | Modified (+3) | Emits `dl_hol_delay_ms` in the JSON metrics report, omitted (not defaulted to 0) when absent | Exposes the new field to the existing JSON→Telegraf→InfluxDB→Grafana pipeline with zero new infrastructure | Yes |
| srsRAN_Project | `lib/scheduler/support/ue_bitrate_limiter.h` | New (88 lines) | Declares `ue_bitrate_limiter`: a per-UE, per-direction token-bucket bitrate cap | Core of the new "UE bitrate cap" control action - the missing OAI capability (FWA/MTC-style throughput caps) | Yes - core of the new control feature |
| srsRAN_Project | `lib/scheduler/support/ue_bitrate_limiter.cpp` | New (72 lines) | Implements the token bucket: `const` "peek" (`get_max_grantable_bytes`), mutating "commit" (`consume`) | Split to keep the read side callable from `const` grant-sizing code while consumption happens once, at the real commit point | Yes |
| srsRAN_Project | `lib/scheduler/support/CMakeLists.txt` | Modified (+1) | Adds `ue_bitrate_limiter.cpp` to the build | Build dependency | Yes |
| srsRAN_Project | `lib/scheduler/ue_context/ue_cell.h` | Modified (+10) | Adds `dl_bitrate_limiter()`/`ul_bitrate_limiter()` accessors (const and non-const) and the two member instances | Exposes the limiter on the per-UE-cell object, mirroring the existing `pusch_power_controller`/`pucch_power_controller` pattern | Yes |
| srsRAN_Project | `include/srsran/scheduler/scheduler_configurator.h` | Modified (+5) | Adds `max_dl_bitrate_kbps`/`max_ul_bitrate_kbps` (`std::optional<unsigned>`) to `sched_ue_resource_alloc_config` | Config surface for the cap, alongside the existing `pdsch_grant_size_limits`/`pusch_grant_size_limits` (PRB quota) fields | Yes |
| srsRAN_Project | `lib/scheduler/ue_scheduling/grant_params_selector.cpp` | Modified (+7) | Clamps `pending_bytes` to `ue_cc.{dl,ul}_bitrate_limiter().get_max_grantable_bytes(slot)` for new-data grants only (never retransmissions) | Actual enforcement point; a `const`, side-effect-free "peek" since this code operates on a `const ue_cell&` | Yes - this is where the cap actually takes effect |
| srsRAN_Project | `lib/scheduler/ue_scheduling/ue_cell_grid_allocator.cpp` | Modified (+8) | Calls `{dl,ul}_bitrate_limiter().consume(slot, tbs_bytes)` once the real final TBS is known, for both DL (`set_pdsch_params`) and UL (`set_pusch_params`) | Token deduction against the *actual* granted bytes, not an estimate | Yes |
| srsRAN_Project | `include/srsran/du/du_high/du_manager/du_configurator.h` | Modified (+10) | Adds `max_dl_bitrate_kbps`/`max_ul_bitrate_kbps` to `du_mac_sched_control_config`; adds `bitrate_cap_result` to `du_mac_sched_control_config_response` | Carries the RIC-requested cap value from the E2SM-RC executor down to the DU manager; dedicated response field rather than reusing an unrelated one for clarity | Yes |
| srsRAN_Project | `lib/du/du_high/du_manager/procedures/du_ue_ric_configuration_procedure.cpp` | Modified (+46 net, restructured) | (a) threads the two new fields into `sched_ue_resource_alloc_config`; (b) **fixes two pre-existing gates** that would otherwise reject a bitrate-cap-only request: an unconditional `request.rrm_policy_ratio_list[0]` index (undefined behavior on an empty list) and a hard `if (rrm_policy_ratio_list.empty()) return failure;` guard | Without (b), any RIC Control Request that only sets a bitrate cap (no PRB quota) would either crash or be silently rejected | Yes - both the new feature and the correctness fix are required together |
| srsRAN_Project | `lib/e2/e2sm/e2sm_rc/e2sm_rc_control_action_du_executor.h` | Modified (+30) | Declares `e2sm_rc_control_action_2_201_du_executor`, a new E2SM-RC control-action executor class | New control action for the bitrate cap, following the exact pattern of the existing `e2sm_rc_control_action_2_6_du_executor` (PRB quota) | Yes |
| srsRAN_Project | `lib/e2/e2sm/e2sm_rc/e2sm_rc_control_action_du_executor.cpp` | Modified (+122) | Implements the new executor: RAN-parameter parsing (ID 1 = Max DL Bitrate, ID 2 = Max UL Bitrate, both plain integers), request validation, logging, and dispatch to `du_configurator::configure_ue_mac_scheduler()` | Bridges the E2AP/E2SM-RC wire format to the DU config struct above | Yes |
| srsRAN_Project | `lib/e2/common/e2_du_factory.cpp` | Modified (+3) | Constructs and registers the new executor under the same RC Style 2 service as the PRB-quota action | Without this, the new action exists in code but is never advertised to or reachable by a RIC | Yes |
| srsRAN_Project | `AGENTIC_RAN_SRSRAN_TELEMETRY_DESIGN.md` | New (575 lines) | Full design document: objective, OAI-reference review, design rationale, per-file rationale, metric-by-metric source table, control-plane sections (§14), live validation evidence | Primary technical record of *why* each change was made, referenced throughout this audit rather than duplicated | Yes (documentation) |
| srsRAN_Project | `setup_run.md` | Pre-existing, untracked | Documents the live testbed's architecture (config file locations, `gnb.sh`/`ue.sh`, RIC/5GC start commands) | **Not authored during this effort** - it existed in the working tree before this session and was read, not written, by this work | N/A - not part of this effort's changes |

**Note on `dl_hol_delay_ms`**: this field is correctly wired end-to-end on the scheduler-metrics side, but **live
testing showed it never actually populates**, even under sustained non-zero DL buffer occupancy. Root cause
(found via live validation, not by re-reading this change's own code): `lib/du/du_high/du_manager/du_ue/
du_ue_adapters.h`, class `rlc_tx_mac_buffer_state_updater::on_buffer_state_update()` - the real production RLC→MAC
bridge - has a `// TODO: set hol_toa` and never forwards the real `rlc_buffer_state::hol_toa` that RLC computes.
**This is a pre-existing srsRAN gap, not introduced by this effort**, and is documented in
`AGENTIC_RAN_SRSRAN_TELEMETRY_DESIGN.md` §14.3 together with the reason it wasn't fixed here (it also silently
disables the scheduler's own HOL-delay-based QoS prioritization, a live-behavior-affecting fix outside this
effort's telemetry scope, and needs a three-subsystem clock-type reconciliation).

### A.2 `oran-sc-ric` - summary

Two categories: (1) changes made during this effort, verified line-by-line; (2) pre-existing local changes/files
found in the working tree that were **not** made during this effort, listed for completeness only.

#### A.2.a Changes made during this effort

| Repository | File/Folder | Change Type | Purpose | Why Needed | Essential? |
|---|---|---|---|---|---|
| oran-sc-ric | `xApps/python/lib/e2sm_rc_module.py` | Modified (+30/-0, purely additive) | Adds `send_control_request_style_2_action_201()` (aliased `control_ue_bitrate_cap`), building and sending the RIC Control Request for the new action | xApp-side counterpart to the new srsRAN executor - without it, no Python code could construct the E2AP message for Action 201 | Yes |
| oran-sc-ric | `xApps/python/simple_bitrate_cap_xapp.py` | New (55 lines) | Standalone demo/test xApp: cycles a UE's DL bitrate cap through 5/15/30/60 Mbps, holding each for 5 s, mirroring `simple_rc_xapp.py`'s structure for the PRB-quota case | Used to validate the new control action live (see `AGENTIC_RAN_SRSRAN_TELEMETRY_DESIGN.md` §14.4 for the InfluxDB throughput-staircase results this produced) | Yes for validation; not required for the srsRAN-side feature itself to exist |

#### A.2.b Pre-existing changes found in the working tree - NOT made during this effort

Listed for audit completeness and honesty. Verified via `git diff <file>` per file (see the design doc's earlier
validation session for the exact diffs); provenance/authorship prior to this effort was not further investigated
since it is out of scope.

| File/Folder | State | Note |
|---|---|---|
| `docker-compose.yml` | Modified (-2 lines) | Removes an obsolete `version: '3.9'` key. Not part of this effort. |
| `xApps/python/kpm_mon_xapp.py` | Modified | Changes `report_period`/`granul_period` from 1000 ms to 30 ms. Not part of this effort. |
| `xApps/python/simple_xapp.py` | Modified | Changes default `--http_server_port` from 8090 to 8092. Not part of this effort. |
| `xApps/python/advanced_kpm.py`, `controlled_rc_xApp.py`, `dt_xApp.py`, `inspector.py`, `mswim.py`, `node_retreiver.py`, `rlc.py`, `test_degradation.py`, `test_rc.xapp.py`, `time_to_impact_xapp.py`, `xApp_time.py` | Untracked (never committed to this repo) | Not referenced in the official `srsran/oran-sc-ric` README's documented xApp list (verified: `git ls-files xApps/python/` shows only `simple_rc_xapp.py`, `simple_rc_ho_xapp.py`, `simple_ccc_xapp.py`, `simple_mon_xapp.py`, `kpm_mon_xapp.py`, `simple_xapp.py`, and the `lib/` modules as tracked). Some filenames (`mswim.py`, `time_to_impact_xapp.py`) match the names of the user's own separate personal repositories found on this machine (`~/mswim_measurements` → `time_to_impact_estimation`), suggesting these are local copies of prior, separate research work, not part of this effort. |
| `mswim.py` (repo root), `prb_events_4.jsonl`, `xApps/python/prb_events.jsonl`, `xApps/python/prb_actions.log`, `xApps/python/recovery_prb_events.jsonl`, `xApps/python/.tti_log.jsonl.swp`, `lib/__pycache__/`, `lib/asn1/__pycache__/` | Untracked | Experiment output/log artifacts and Python bytecode caches, not source changes. `prb_events.jsonl`'s earliest entries predate this session by roughly a month (June 2026 timestamps observed during an earlier part of this session), confirming they are leftovers from prior, unrelated experimentation. |

### A.3 `oai-release-agentran` - no changes

**No files were modified in this repository.** It was used exclusively as a read-only reference to understand
what telemetry/control fields a comparable OAI-based "Agentic RAN" stack exposes (`dl_metrics_forwarder.py`,
`ul_metrics_forwarder.py`, `UL_control_termination.py`) before deciding on an srsRAN-native design. Full findings
are in `AGENTIC_RAN_SRSRAN_TELEMETRY_DESIGN.md` §2-3.

---

## B. Step-by-step reproduction guide

This reproduces the current development state starting from clean upstream checkouts. Exact patches are provided
in `agentic_ran/patches/` in this repo (copy that whole directory out before resetting anything, since it lives
inside the tree these instructions modify).

### B.1 Base repositories and commits

| Repository | Clone URL | Base ref to check out |
|---|---|---|
| srsRAN | `https://github.com/Mouradnetworking/srsran.git` (this fork) or `https://github.com/srsRAN-Project/srsRAN_Project.git` (true upstream, **to verify**: confirm this fork's `3ed363dabf` exists on true upstream too, since it's a fork and may have unique commits) | `3ed363dabf` ("Update main") - the last ordinary commit before the `build/`+`docker/` dump at `0adc739b95` |
| O-RAN SC RIC | `https://github.com/srsran/oran-sc-ric.git` | `main` at the commit this was cloned at (record `git rev-parse HEAD` from your clone before applying anything; not independently re-verified for this audit beyond "up to date with origin/main" at the time of writing) |
| OAI AgentRAN reference (read-only, not modified) | `https://github.com/wineslab/oai-release-agentran.git` | Any commit - used only for reading `dl_metrics_forwarder.py`, `ul_metrics_forwarder.py`, `UL_control_termination.py` |

### B.2 Reproduce the srsRAN changes

```sh
# 1. Clone and pin to the base commit
git clone https://github.com/Mouradnetworking/srsran.git srsRAN_Project
cd srsRAN_Project
git checkout 3ed363dabf

# 2. (Optional but recommended) bring in the testbed docker/ infra from the "first comit" without its
#    accidental build/ dump - cherry-pick just the docker/ paths:
git checkout 0adc739b95 -- docker/
git status   # confirm only docker/* is staged, not build/*

# 3. Apply the source-code patch for this effort (copy agentic_ran/patches/ out of the working tree first
#    if you're reproducing from scratch, since it doesn't exist at 3ed363dabf)
git apply --check agentic_ran/patches/srsran_modified_files.patch   # dry run
git apply agentic_ran/patches/srsran_modified_files.patch

# 4. Add the four new files (not covered by the patch above, since `git diff` doesn't include untracked files)
mkdir -p lib/scheduler/logging lib/scheduler/support
cp agentic_ran/patches/new_files/lib/scheduler/logging/scheduler_ue_state_logger.{h,cpp} lib/scheduler/logging/
cp agentic_ran/patches/new_files/lib/scheduler/support/ue_bitrate_limiter.{h,cpp} lib/scheduler/support/

# 5. Verify the tree matches this audit
git status --short -- . ':!build'   # should show exactly the 22 modified + 4 new files listed in §A.1
```

### B.3 Dependencies (srsRAN build)

As documented by upstream srsRAN (`docs/BUILD.md` / the root `README.md`); this effort added **no new
third-party dependencies** - only new `.cpp`/`.h` files within the existing scheduler/E2 modules, using only the
standard library (`<optional>`, `<chrono>`-adjacent `slot_point`, `<cmath>`, `<algorithm>`) and existing srsRAN
types. `to verify`: confirm your environment already satisfies srsRAN's normal build prerequisites (CMake ≥ 3.15,
a C++20 compiler, the usual srsRAN third-party libs - libfftw3, uhd/zmq radio drivers if you intend to run
against those, mbedtls, etc.) per the upstream `docs/BUILD.md`.

### B.4 Build

```sh
mkdir -p build && cd build
cmake ..
cmake --build . --target gnb -j"$(nproc)"
```

Verify a clean build with **no new warnings/errors** attributable to this patch - the original development
session rebuilt `srsran_sched`, `srsran_du_manager`, `srsran_e2`, and the full `gnb` target individually after
each logical group of changes and confirmed clean compiles each time (see the conversation history / this
document's provenance for the exact target list). `to verify`: your toolchain/compiler version may surface
warnings not seen in the original environment (GCC/Clang version, `-Wall`/`-Wextra` flags); none are expected
given the changes are additive and type-safe, but this has not been independently re-verified outside the
original session's environment.

### B.5 Install the binary where your testbed expects it

The testbed's `gnb.sh` wrapper (documented in `setup_run.md`, not part of this effort) invokes
`/usr/local/bin/gnb`. If your testbed follows the same convention:

```sh
sudo cp build/apps/gnb/gnb /usr/local/bin/gnb.bak-$(date +%Y%m%d)   # back up whatever is there first
sudo cp build/apps/gnb/gnb /usr/local/bin/gnb
sudo chmod 755 /usr/local/bin/gnb
```

### B.6 Reproduce the `oran-sc-ric` changes

```sh
git clone https://github.com/srsran/oran-sc-ric.git
cd oran-sc-ric
# record the commit you cloned at, for your own reproducibility record:
git rev-parse HEAD

# apply the xApp-side patch
git apply --check ../srsRAN_Project/agentic_ran/patches/oran_sc_ric_modified_files.patch
git apply ../srsRAN_Project/agentic_ran/patches/oran_sc_ric_modified_files.patch

# add the new xApp
cp ../srsRAN_Project/agentic_ran/patches/new_files_oran_sc_ric/xApps/python/simple_bitrate_cap_xapp.py \
   xApps/python/

git status --short   # should show exactly xApps/python/lib/e2sm_rc_module.py modified, plus the new xApp file
```

### B.7 Configuration changes

No configuration file *format* changed. The new E2SM-RC control action (Style 2, Action 201) requires no new YAML
keys - it's registered unconditionally in `e2_du_factory.cpp` whenever `e2sm_rc_enabled: true` is already set
(the same flag that enables the pre-existing PRB-quota action). Confirm your `gnb` YAML config has:

```yaml
e2:
  enable_du_e2: true
  addr: <near-RT RIC IP>
  port: 36421
  e2sm_kpm_enabled: true
  e2sm_rc_enabled: true
```

For telemetry Layer 2 (per-grant JSON pipe), set the environment variable before starting the gNB:

```sh
export SRSRAN_UE_STATE_PIPE=/tmp/srsran_ue_state.pipe
```

### B.8 Run and verify

See `agentic_ran/README.md` §F/G for the full testbed startup sequence. Minimal verification of *this specific
patch*, once the full stack (5GC, RIC, gNB, UE) is up and a UE is attached:

1. **PRB quota control (pre-existing srsRAN functionality, not new, but a good end-to-end sanity check that your
   build/config is correct)**:
   ```sh
   # inside the RIC's python_xapp_runner container
   cd /opt/xApps && python3 simple_rc_xapp.py --e2_node_id <your gNB's E2 node id> --ran_func_id 3 --gnb_cu_ue_f1ap_id 0
   ```
   Expected gNB log: `E2SM-RC: Slice-level PRB quota Control Request ...` followed by `Sending E2 RIC Control
   Acknowledge`, repeating every 5 s with different Min/Max PRB Policy Ratio values.

2. **New UE bitrate cap control action**:
   ```sh
   python3 simple_bitrate_cap_xapp.py --e2_node_id <your gNB's E2 node id> --ran_func_id 3 --gnb_cu_ue_f1ap_id 0
   ```
   Expected gNB log: `E2SM-RC: UE Bitrate Cap Control Request for UE gNB-DU-UE-F1AP-ID=0: max_dl_kbps=5000
   max_ul_kbps=unset` (then 15000, 30000, 60000, repeating), each followed by `Sending E2 RIC Control
   Acknowledge`. With a saturating DL flow running (e.g. `iperf3 -R`) and InfluxDB queryable, `dl_brate` should
   step to match each cap within roughly ±1-6%.

3. **Telemetry Layer 2 pipe**:
   ```sh
   cat /tmp/srsran_ue_state.pipe | head -5
   ```
   Expected: one JSON object per line, `"schema":"srsran_ue_state_v1"`, with `dl_grants` populated whenever the
   UE is actually scheduled.

### B.9 Troubleshooting notes

- **`git apply` fails with a context mismatch**: the patch was generated against `0adc739b95`'s working tree
  state, which itself sits on `3ed363dabf`. If your checkout of `3ed363dabf` differs (e.g. you're on the true
  upstream `srsRAN-Project/srsRAN_Project` rather than this fork and the two have diverged even at that commit),
  apply with `git apply --3way` or `patch -p1 < ...` and resolve any conflicts manually - they should be confined
  to the exact hunks described in §A.1.
- **Build fails with "unknown type `ue_bitrate_limiter`"**: step B.2.4 (copying the new files) was skipped, or
  `lib/scheduler/support/CMakeLists.txt`'s patch hunk didn't apply - check the file lists `ue_bitrate_limiter.cpp`
  in its `SOURCES` (or equivalent) list.
- **`gnb` won't start / config rejected**: this patch adds no new mandatory YAML keys; a config that worked before
  should still work. If it doesn't, `git diff` your config against a known-good one first - this is very unlikely
  to be caused by this patch.
- **RIC Control Request for Action 201 is never ACKed / times out**: confirm `e2sm_rc_enabled: true` in the gNB
  config and that the gNB log shows `Added supported RAN function with id 3 and OID
  1.3.6.1.4.1.53148.1.1.2.3` (RC) at startup - E2 Setup succeeding is a precondition, unrelated to this specific
  action.
- **`dl_hol_delay_ms` never appears in metrics**: expected, not a bug in this patch - see the note in §A.1 and
  `AGENTIC_RAN_SRSRAN_TELEMETRY_DESIGN.md` §14.3 for the pre-existing upstream gap that causes this.
- **Sudo/permissions issues installing the binary or running `gnb`/`srsue`**: this effort's automation
  environment had no interactive sudo access; every privileged step (installing the binary, starting `gnb`,
  starting `srsue`, creating the network namespace) had to be run manually by a human operator. Budget for that
  if you're scripting reproduction end-to-end.

---

## C. New xApp technical explanation

### C.1 What problem it solves

`simple_bitrate_cap_xapp.py` (plus its supporting `e2sm_rc_module.py` addition and the new srsRAN-side control
action) closes a specific, concrete gap: **srsRAN's near-RT RIC control surface had no way to cap a UE's achieved
data rate directly.** It had (and has) PRB-quota control (`simple_rc_xapp.py`, pre-existing) - capping the
*resource share* (%) a UE may use - but resource-share caps do not translate predictably to rate caps, because
achieved throughput at a given PRB share still depends on channel quality (MCS/CQI). An operator or automated
policy that needs "this UE/slice gets at most 10 Mbps, full stop, regardless of radio conditions" - the same
requirement OAI's `UL_control_termination.py` addresses with its `fwa_value`/`mtc_value` Mbps caps for its
FWA/MTC traffic classes - had no srsRAN equivalent before this xApp and its backing control action.

### C.2 Role in the Open RAN / near-RT RIC environment

It is a **RIC Control (E2SM-RC) xApp**, in the same architectural role as the existing `simple_rc_xapp.py` and
`controlled_rc_xApp.py`: a near-RT (10 ms-1 s loop budget) closed-loop control application that runs on the RIC
platform and issues E2AP `RIC CONTROL REQUEST` messages toward one or more E2 Nodes (here, the srsRAN DU) via the
standard E2SM-RC service model. It does not consume E2 telemetry itself in the current implementation (see
Limitations, C.9) - it is a pure control-command generator, structured as a minimal, readable reference/demo
xApp rather than a production autonomous controller.

### C.3 Inputs it consumes

- Command-line arguments: E2 Node ID, RAN function ID (3, the RC function), target UE's `gNB-CU-UE-F1AP-ID`.
- A fixed, hardcoded schedule of DL bitrate targets (`[5000, 15000, 30000, 60000]` kbps) - **no live RAN telemetry
  is consumed in this version**; it is a scripted demonstration, not a closed loop reacting to measured
  conditions (see C.9).

### C.4 Metrics/messages it receives

None at runtime beyond the RIC platform's own RMR transport acknowledgement plumbing (inherited from
`xAppBase`). It does not subscribe to E2SM-KPM indications. This is a deliberate simplification to keep the demo
focused on proving the new control action works, matching the style of the existing `simple_rc_xapp.py` it was
modeled on (which is likewise open-loop/scripted, not telemetry-driven).

### C.5 Decisions/actions it produces

One action type: an E2SM-RC `RIC CONTROL REQUEST`, Style 2 / Action 201 ("UE Bitrate Cap"), carrying one or two
RAN Parameters (Maximum DL Bitrate, Maximum UL Bitrate, both in kbps, both optional but at least one required),
targeting a specific UE by `gNB-DU-UE-F1AP-ID`.

### C.6 How it interacts with srsRAN and/or the RIC

```
simple_bitrate_cap_xapp.py
   │  control_ue_bitrate_cap(e2_node_id, ue_id, max_dl_kbps, ...)
   ▼
e2sm_rc_module.py: send_control_request_style_2_action_201()
   │  packs RIC Control Header (Style 2, Action 201, UE ID) + Control Message (RAN Parameter list)
   ▼
RMR (RIC Message Router) → E2 Term → srsRAN DU's E2 agent (E2AP)
   ▼
lib/e2/common/e2_impl.cpp: handle_ric_control_request()
   ▼
lib/e2/procedures/e2_ric_control_procedure.cpp → e2sm_manager → e2sm_rc_impl → e2sm_rc_control_service (Style 2)
   ▼
e2sm_rc_control_action_2_201_du_executor::execute_ric_control_action()
   │  parses RAN Parameters, builds du_mac_sched_control_config{max_dl_bitrate_kbps, ...}
   ▼
du_configurator::configure_ue_mac_scheduler()  →  du_ue_ric_configuration_procedure.cpp
   ▼
sched_ue_resource_alloc_config::max_dl_bitrate_kbps  (per-UE scheduler config)
   ▼
ue_cell::dl_bitrate_limiter().set_cap()  →  enforced live in grant_params_selector.cpp / ue_cell_grid_allocator.cpp
   ▼
RIC CONTROL ACKNOWLEDGE sent back to the xApp via the same E2AP/RMR path
```

### C.7 How it differs from existing xApps in this repository

| xApp | Control style/action | What it caps | Telemetry-driven? |
|---|---|---|---|
| `simple_rc_xapp.py` (existing) | Style 2 / Action 6 | PRB resource share (%) | No (scripted) |
| `controlled_rc_xApp.py` (pre-existing, not part of this effort) | Style 2 / Action 6 | PRB resource share (%), interactively via stdin | No |
| `simple_rc_ho_xapp.py` (existing) | Style 3 / Action 1 | Handover target cell | No |
| `simple_ccc_xapp.py` (existing) | E2SM-CCC | Cell-level RRM policy ratio | No |
| **`simple_bitrate_cap_xapp.py` (new)** | **Style 2 / Action 201 (new, vendor-specific)** | **Achieved DL/UL bitrate (kbps), directly** | No (scripted, same style as `simple_rc_xapp.py`) |

The key structural difference from every existing RC xApp in this repository is not in the xApp's own Python
logic (which deliberately follows the same minimal scripted-loop pattern as `simple_rc_xapp.py`) but in **what
srsRAN-side mechanism it drives**: it is the first xApp in this repository whose control action reaches a
scheduler mechanism that did not exist before this effort (the token-bucket bitrate limiter), rather than reusing
an existing srsRAN capability through a new xApp wrapper.

### C.8 Why it is useful

- Closes a concrete parity gap with the OAI AgentRAN reference design (§2 of the design doc) using a
  standards-shaped (if vendor-extended) E2SM-RC mechanism rather than a bespoke out-of-band channel.
- Demonstrates, with live measured evidence (§14.4 of the design doc), that the new srsRAN-side control action
  works causally and repeatably - not just that it compiles.
- Provides a minimal, readable template (mirroring `simple_rc_xapp.py`'s structure closely) for anyone extending
  this to a real closed-loop controller (see Use Cases, §E).

### C.9 Limitations

- **Open-loop / scripted, not adaptive**: the cap schedule is hardcoded; it does not read any KPM telemetry (CQI,
  buffer occupancy, current achieved throughput) to decide what cap to set. A production SLA-enforcement or
  congestion-mitigation xApp would need to add an E2SM-KPM subscription and a decision policy.
  - **This is squarely fillable by the E2SM-KPM telemetry (Layer 1) and native per-grant telemetry (Layer 2)
    already built in this same effort** - see `AGENTIC_RAN_SRSRAN_TELEMETRY_DESIGN.md` §4-§10 - which already
    expose CQI, SINR, MCS, PRB usage, throughput, BSR, and (once the pre-existing upstream `hol_toa` gap in
    §14.3 is fixed) queueing delay, either via standard E2SM-KPM subscription or the experimental per-slot pipe.
    A closed-loop version of this xApp is a natural, well-supported next step, not a redesign.
- **Single UE, single cell**: the demo targets one hardcoded `gnb_cu_ue_f1ap_id` at a time; no multi-UE fairness
  or per-slice aggregation logic.
- **DL-focused**: the demo script only ever sets `max_dl_kbps`; `max_ul_kbps` is supported by the underlying
  `send_control_request_style_2_action_201()` function and the srsRAN-side enforcement, but not exercised by the
  demo.
- **Full-replace semantics not accounted for**: as documented in `du_configurator.h` (§A.1), a bitrate-cap-only
  request resets any previously-set PRB quota to unrestricted (and vice versa), since `sched_ue_resource_alloc_
  config` is fully replaced, not merged, on every RIC Control Request. A real controller managing both levers
  simultaneously must resend both parameter sets together.
- **Action ID 201 is a vendor-specific extension**, not a real O-RAN-assigned Style 2 action ID (see §D) - an
  xApp built against a different E2 node implementation would not recognize this action.

---

## D. Novelty analysis

Methodology: targeted web searches were run against public sources (arXiv, GitHub, O-RAN-adjacent technical
sites) during this audit; results are summarized with sources below. This is **not exhaustive** - it does not
constitute a formal prior-art/patent-style search, and absence of a match in these searches is evidence of
"not found," not proof of "does not exist."

### D.1 What already exists and is closely related

| Name | Link | What it does | Closeness to ours | Similarities | Differences |
|---|---|---|---|---|---|
| **`simple_rc_xapp.py`** (this same repo, pre-existing, official) | [github.com/srsran/oran-sc-ric](https://github.com/srsran/oran-sc-ric) | Cycles a UE's **PRB quota** (Style 2 / Action 6, standard O-RAN action) | High (same repo, same style, same scripted-loop pattern) | Scripted, non-telemetry-driven; targets the same `e2_node_id`/`ue_id` CLI pattern; same 5 s step cadence style | Controls resource *share* (%), a standard action ID, not a *rate* cap; uses an already-existing srsRAN control action, not a new one |
| **`UL_control_termination.py`** (`oai-release-agentran`, read-only reference) | `~/oai-release-agentran` (private/local checkout; `wineslab/oai-release-agentran` on GitHub) | REST API that, via Telnet into a running OAI process, sets per-traffic-class (`fwa_value`/`mtc_value`) throughput caps in Mbps | High in *intent*, low in *mechanism* | Same underlying goal: a direct rate cap, not a resource-share cap | Not E2/RIC-based at all - Telnet into OAI's Lua scheduler globals, no standards-based control plane; per-traffic-class rather than per-UE |
| **"An Open RAN Framework for the Dynamic Control of 5G Service Level Agreements"** | [arXiv:2309.07508](https://arxiv.org/pdf/2309.07508) | An "SLA xApp" using E2SM-RC to enforce **Guaranteed Bitrate (GBR)** per UE, driven by cell resource utilization and channel-quality telemetry | Medium-high (closest academic match found) | Same general goal (RIC-driven per-UE bitrate guarantee/control via E2SM-RC) | Closed-loop (telemetry-driven), targets *guaranteed minimum* bitrate (GBR/SLA enforcement) rather than a hard *maximum* cap; implementation platform (RIC, gNB stack, exact E2SM-RC action used) not confirmed to be srsRAN or O-RAN SC RIC from the search result alone - `to verify` by reading the full paper |
| **O-RAN E2SM-RC Style 2 standard action catalogue** (spec-level, not a specific xApp) | [techplayon.com E2SM-RC overview](https://www.techplayon.com/e2sm-rc-e2-service-model-ran-control-procedure/); O-RAN.WG3.E2SM-RC-R003-v03.00 | Defines 12 control action *types* including handover, scheduling policy, and slice-level PRB quota | N/A (this is the spec our new action deliberately sits *outside* of) | Confirms no standard Style 2 action exists for a direct bitrate cap (closest is PRB quota, a resource-share mechanism) - **this absence is the basis for choosing a vendor-specific action ID (201) rather than misusing a standard one**, not a novelty claim in itself | — |
| **Token-bucket rate limiting** (general networking technique, not RAN/RIC-specific) | e.g. [intronetworks.cs.luc.edu](https://intronetworks.cs.luc.edu/current/uhtml/tokenbucket.html); [arXiv:1911.12079 "Token Bucket-based Throughput Constraining in Cross-layer Schedulers"](https://arxiv.org/pdf/1911.12079) | The token-bucket algorithm itself, and its use in cross-layer (non-RIC) schedulers | The *algorithm* is well-established and not novel; our contribution is applying it as the enforcement mechanism behind a specific new E2SM-RC action in a specific open-source RAN scheduler | Same core algorithm (bucket, refill rate, burst allowance, consume-on-send) | Not RIC/E2-driven in the cited prior art; ours is specifically wired to a live E2SM-RC control action and srsRAN's real-time scheduler grant path |

### D.2 What could not be confirmed either way

- Whether any other **srsRAN-specific** fork or branch (beyond this one) has already implemented an equivalent
  E2SM-RC bitrate-cap action. Searches did not surface one, but srsRAN forks/branches are not comprehensively
  indexed by general web search - `to verify` by searching GitHub directly for forks of `srsran/srsRAN_Project`
  or `srsran/oran-sc-ric` with scheduler/E2SM-RC changes matching this pattern.
- Whether the specific combination "O-RAN SC RIC + srsRAN + a custom Style 2 action ID for a direct UE bitrate
  cap, enforced via a token bucket in the MAC scheduler's grant-sizing path" has been published in any paper not
  surfaced by the queries run. The closest match found (§D.1, arXiv:2309.07508) targets *guaranteed minimum*
  bitrate via a closed loop, which is related but not identical in goal (max cap vs. min guarantee) or in
  confirmed implementation stack.
- Whether O-RAN's own RC action catalogue has, in a version newer than what was checked, added a standard action
  for direct bitrate capping. `to verify` against the latest O-RAN.WG3.E2SM-RC specification directly (not done
  in this audit - only secondary/summary sources were consulted).

### D.3 Positioning statement (honest, not a bare novelty claim)

Do **not** claim "no one has done this before." What can be said with the confidence the above research
supports:

- The general **problem** (direct per-UE bitrate capping via a near-RT RIC) is addressed in at least one
  published academic framework (arXiv:2309.07508), which targets the related-but-distinct goal of guaranteed
  minimum bitrate via a closed loop.
- The specific **mechanism** (a new, clearly-labeled vendor-specific E2SM-RC Style 2 action, enforced by a
  from-scratch token bucket wired into srsRAN's actual scheduler grant-sizing/allocation code, validated live
  against a real O-RAN SC RIC deployment) was not found duplicated in the sources searched.
- The **xApp itself** is intentionally minimal/demonstrative, closely patterned on the repository's own existing
  `simple_rc_xapp.py` - its novelty is not claimed to be in xApp-side sophistication, but in the srsRAN-side
  control action and enforcement path it exercises, which did not exist in this codebase before this effort
  (confirmed directly via `git diff`/`git status`, §A.1).
- This should be positioned as **an incremental, verified extension of srsRAN's existing E2SM-RC control surface**
  - filling a gap between "resource-share control" (already present) and "no rate control" - rather than as a
  claim of being the first system anywhere to do RIC-driven bitrate capping.

---

## E. Use cases

### E.1 Fixed Wireless Access (FWA) subscriber tiering

- **Scenario**: an operator sells FWA service at multiple speed tiers (e.g. 25/50/100 Mbps) over a shared cell.
- **Input data**: subscriber tier (from OSS/BSS, out of band), UE identity (RNTI/F1AP UE ID, obtainable from
  either the E2SM-KPM UE-level report or F1AP signaling).
- **xApp decision/action**: on UE attach (or periodically), issue a `control_ue_bitrate_cap()` request matching
  the subscriber's purchased tier.
- **Expected benefit**: enforces the commercial tiering without relying on core-network-side rate limiting (which
  can't react to instantaneous radio conditions) or resource-share-only PRB quota (which under-delivers a "100
  Mbps" promise in poor radio conditions and over-delivers it in excellent ones).
- **Why existing solutions may not cover it**: PRB quota (already in this repo) caps resource *share*, not rate -
  a UE in excellent radio conditions with a 20% PRB quota may still exceed a "50 Mbps tier" commitment; a hard
  kbps cap is needed for a literal commercial SLA.
- **How to validate experimentally**: repeat the §14.4 methodology (iperf3 DL saturation + InfluxDB `dl_brate`
  query) across a range of simulated channel qualities (if using a channel-modeling RF path) or across multiple
  concurrent UEs at different quota tiers, confirming each UE's achieved rate tracks its assigned tier
  independent of the others' radio conditions.

### E.2 Machine-Type Communication (MTC) / IoT traffic de-prioritization

- **Scenario**: a cell serves a mix of latency/throughput-sensitive human traffic and bursty, low-priority IoT/MTC
  traffic that should never be allowed to starve the former.
- **Input data**: UE type/traffic-class tag (from core-network subscriber data or a 5QI/slice mapping), current
  cell load (from E2SM-KPM `RRU.PrbUsedDl`/`RRU.PrbTotDl`, already available per this effort's telemetry work).
- **xApp decision/action**: apply a low, static bitrate cap (e.g. 1-2 Mbps) to UEs tagged as MTC, unconditionally
  or only when cell load crosses a threshold.
- **Expected benefit**: bounds the worst-case resource consumption of low-priority traffic without needing
  MTC-specific scheduler policy changes.
- **Why existing solutions may not cover it**: srsRAN's existing RAN slicing (PRB-ratio-based) operates at the
  slice level and requires S-NSSAI-based traffic separation to already be configured; a direct per-UE cap is a
  lighter-weight lever usable even without full network slicing deployed.
- **How to validate experimentally**: attach two UEs, saturate both with `iperf3`, cap one via the new xApp,
  confirm (via InfluxDB `dl_brate` for both UEs, plus `RRU.PrbUsedDl` from E2SM-KPM) that the uncapped UE's
  throughput is unaffected by the capped one's presence.

### E.3 Congestion-responsive rate shaping (closed-loop extension)

- **Scenario**: cell-wide congestion (measured via E2SM-KPM `RRU.PrbUsedDl` approaching `RRU.PrbTotDl`) should
  trigger automatic, temporary rate caps on the heaviest consumers rather than degrading all UEs uniformly.
- **Input data**: E2SM-KPM cell-level PRB utilization (existing), per-UE `DRB.UEThpDl` (existing E2SM-KPM
  measurement), or the new Layer 2 per-grant telemetry for finer-grained heavy-hitter identification.
- **xApp decision/action**: subscribe to KPM reports (not implemented in the current demo xApp - see C.9), rank
  UEs by recent throughput, issue `control_ue_bitrate_cap()` to the top consumer(s) when a utilization threshold
  is crossed, release the cap when utilization drops.
- **Expected benefit**: automated, fair, low-latency congestion response without operator intervention.
- **Why existing solutions may not cover it**: this is exactly the class of closed-loop control the current xApp
  deliberately does *not* implement (§C.9); it requires combining this effort's new control action with this
  effort's telemetry work (E2SM-KPM already live, per §14.1) plus new decision logic - a natural, well-scoped
  follow-up rather than a gap in what was built.
- **How to validate experimentally**: three or more concurrent saturating UEs; observe whether an extended
  version of the xApp correctly identifies and throttles the heaviest consumer(s) as aggregate PRB usage
  approaches 100%, and that throttling releases cleanly when load drops.

### E.4 Slice-level throughput SLA alongside PRB quota (combined lever)

- **Scenario**: a network slice has both a *minimum resource guarantee* (via existing PRB quota, dedicated ratio)
  and a *maximum rate ceiling* (via the new bitrate cap) - e.g. an enterprise slice guaranteed 20% of PRBs but
  capped at 200 Mbps even if radio conditions would allow more.
- **Input data**: slice identity (S-NSSAI, already parsed by the existing PRB-quota action), desired min PRB %
  and max kbps.
- **xApp decision/action**: issue *both* a PRB-quota control request and a bitrate-cap control request for the
  UE(s) in the slice, resent together (per the full-replace caveat in §A.1/§C.9).
- **Expected benefit**: two independent, complementary levers instead of one, closer to how real commercial SLAs
  are often structured (a resource floor plus a rate ceiling).
- **Why existing solutions may not cover it**: neither lever alone expresses both constraints; this repo had only
  the resource-floor lever before this effort.
- **How to validate experimentally**: configure both caps on a UE with excellent radio conditions; confirm
  achieved throughput respects the *lower* of "what the PRB quota's max ratio would allow at current MCS" and
  "the explicit kbps cap."

---

## Future enhancements

These are explicitly **not implemented** in this effort; listed here as scoped next steps, distinguishing what
requires new design work from what is a small follow-up to something already built:

1. **Fix the pre-existing `hol_toa` gap** (`du_ue_adapters.h`, §A.1/§14.3) so `dl_hol_delay_ms` actually
   populates, and so the scheduler's own HOL-delay-based QoS prioritization becomes live. Requires reconciling
   three different clock types across RLC, MAC, and the scheduler adapter - flagged as a live-behavior-affecting
   change needing explicit sign-off, not a drop-in fix.
2. **Closed-loop version of the bitrate-cap xApp** (E.3): subscribe to E2SM-KPM, add a decision policy, remove
   the hardcoded schedule - directly enabled by telemetry already built in this effort.
3. **UL-side per-grant telemetry** (Layer 2 currently DL-only) and **pre-trim link-adaptation values** (required
   vs. granted RBs/MCS) - both documented as scoped-but-undone in `AGENTIC_RAN_SRSRAN_TELEMETRY_DESIGN.md` §9.
4. **5QI/QoS-flow tagging** on scheduler metrics - a cross-layer join with the DU manager's QoS config, not a
   scheduler instrumentation gap; own design pass needed (§9 of the design doc).
5. **E2SM-KPM measurement name for `dl_hol_delay_ms`** and for the Layer 2 per-grant fields (HARQ id, exact RB
   allocation, RAN slice id) - currently only reachable via JSON/pipe, not standard E2AP, per §10 of the design
   doc.
6. **Merge, not replace, semantics for `sched_ue_resource_alloc_config`** - so a bitrate-cap-only RIC Control
   Request doesn't silently reset a previously-configured PRB quota (and vice versa). Would need the DU manager
   to track and re-apply the UE's last-known full config rather than starting from struct defaults on every
   request.
7. **UL power-control target and TX/RX gain control over E2SM-RC** - both investigated and deliberately not
   built in this effort (§14.2 of the design doc): the former is blocked by srsRAN's Enterprise/OSS split, the
   latter is real but unprovable on the ZMQ testbed. Revisit if an Enterprise build or real/channel-modeling RF
   hardware becomes available.
8. **Multi-UE / slice-aggregate bitrate caps**, rather than the current strictly-per-UE token bucket.
9. **Formal novelty verification**: a full literature/patent-style search (§D.2) was out of scope for this audit;
   recommended before any publication claim.
