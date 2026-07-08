# Reproduction guide — Per-DRB GBR MFBR Enforcement + DRB QoS Configuration xApp

This patch bundle reproduces every change made for the feature documented in
[`../../GBR_MFBR_QOS_ENFORCEMENT.md`](../../GBR_MFBR_QOS_ENFORCEMENT.md) and
[`../../DRB_QOS_XAPP_SYSTEM_DESIGN.md`](../../DRB_QOS_XAPP_SYSTEM_DESIGN.md) — read those two first for *what*
this is and *why*; this file is only the mechanical "how to get the same tree" guide, mirroring the convention
already established by [`../../AGENTIC_RAN_DEVELOPMENT_AUDIT.md`](../../AGENTIC_RAN_DEVELOPMENT_AUDIT.md) §B for
the earlier, separate bitrate-cap effort.

As of this writing, all of this is **uncommitted** on top of `srsRAN_Project` commit `2f186679bc` ("sched:
extend Layer 2 telemetry and bitrate-cap xApp to UL") on branch `dev2` — i.e. these changes sit directly on top
of, and are independent of, the earlier bitrate-cap effort's own commit.

## Contents of this bundle

```
gbr_mfbr_xapp/
├── README.md                          (this file)
├── srsran_modified_files.patch         21 modified files: scheduler, DU-manager, F1AP, CU-UP, CU-CP, E2, README
├── oran_sc_ric_modified_files.patch    1 modified file: xApps/python/lib/e2sm_rc_module.py
├── new_files/
│   └── agentic_ran/
│       ├── GBR_MFBR_QOS_ENFORCEMENT.md
│       └── DRB_QOS_XAPP_SYSTEM_DESIGN.md
└── new_files_oran_sc_ric/
    └── xApps/python/
        └── drb_qos_cap_xapp.py
```

## B.1 Base repositories and commits

| Repo | Base commit/branch | Role |
|---|---|---|
| `srsRAN_Project` | `2f186679bc` (branch `dev2`) | The RAN implementation - this bundle's `srsran_modified_files.patch` applies on top |
| `oran-sc-ric` | whatever commit you have checked out under `~/oran-sc-ric` | RIC platform + xApps |

## B.2 Reproduce the srsRAN changes

```sh
# 1. Clone and pin to the base commit (skip if you already have this exact tree)
git clone <your srsRAN_Project remote> srsRAN_Project
cd srsRAN_Project
git checkout 2f186679bc

# 2. Apply the source-code patch (copy this patches/ dir out of the working tree first if reproducing
#    from a bare base checkout, since it doesn't exist at 2f186679bc)
git apply --check agentic_ran/patches/gbr_mfbr_xapp/srsran_modified_files.patch   # dry run
git apply agentic_ran/patches/gbr_mfbr_xapp/srsran_modified_files.patch

# 3. Add the two new documentation files (not covered by the patch, since `git diff` doesn't include
#    untracked files)
cp agentic_ran/patches/gbr_mfbr_xapp/new_files/agentic_ran/GBR_MFBR_QOS_ENFORCEMENT.md agentic_ran/
cp agentic_ran/patches/gbr_mfbr_xapp/new_files/agentic_ran/DRB_QOS_XAPP_SYSTEM_DESIGN.md agentic_ran/

# 4. Verify the tree matches
git status --short -- . ':!build'   # should show exactly the 21 modified files + 2 new agentic_ran/*.md files
```

**No new third-party dependencies.** All changes are within existing scheduler/CU-CP/CU-UP/E2/F1AP modules,
using only the standard library and existing srsRAN types.

## B.3 Build

```sh
mkdir -p build && cd build
cmake ..
cmake --build . --target srsran_sched -j"$(nproc)"      # verify each logical group independently, as the
cmake --build . --target srsran_cu_cp -j"$(nproc)"       # original session did - see the two main docs for
cmake --build . --target srsran_cu_up -j"$(nproc)"       # the exact per-phase target list and commit-by-commit
cmake --build . --target srsran_e2 -j"$(nproc)"          # rationale
cmake --build . --target gnb -j"$(nproc)"
```
All five targets built clean (no new warnings/errors) in the original session. `to verify`: your toolchain may
differ (GCC/Clang version, warning flags); none are expected given the changes are additive and type-safe, but
this was not independently re-verified outside the original environment.

## B.4 Install the binary where your testbed expects it

The testbed's `gnb.sh` wrapper invokes `/usr/local/bin/gnb` — **this does not auto-update from the build
directory**, a real gotcha hit repeatedly during live validation:
```sh
sudo cp build/apps/gnb/gnb /usr/local/bin/gnb.bak-$(date +%Y%m%d)   # back up whatever is there first
sudo cp build/apps/gnb/gnb /usr/local/bin/gnb
sudo chmod 755 /usr/local/bin/gnb
```

## B.5 Reproduce the `oran-sc-ric` changes

```sh
cd ~/oran-sc-ric   # or wherever your checkout lives
git rev-parse HEAD   # record for your own reproducibility record

git apply --check ~/srsRAN_Project/agentic_ran/patches/gbr_mfbr_xapp/oran_sc_ric_modified_files.patch
git apply ~/srsRAN_Project/agentic_ran/patches/gbr_mfbr_xapp/oran_sc_ric_modified_files.patch

cp ~/srsRAN_Project/agentic_ran/patches/gbr_mfbr_xapp/new_files_oran_sc_ric/xApps/python/drb_qos_cap_xapp.py \
   xApps/python/

git status --short   # should show exactly xApps/python/lib/e2sm_rc_module.py modified, plus the new xApp
```
No container rebuild needed — `xApps/python` is bind-mounted into the `python_xapp_runner` container.

## B.6 Configuration prerequisite

Unlike the earlier bitrate-cap effort, this feature's control path is registered on the **CU-CP's** E2 agent,
which needs to be explicitly enabled (it may be off by default in an older config):
```yaml
e2:
  enable_cu_cp_e2: true    # required for E2SM-RC Style 1 / Radio Bearer Control
  enable_du_e2: true
  e2sm_rc_enabled: true
```

## B.7 Run and verify

Do **not** improvise this part — [`DRB_QOS_XAPP_SYSTEM_DESIGN.md`](../../DRB_QOS_XAPP_SYSTEM_DESIGN.md) §14-15
is a complete, step-by-step runbook covering exactly this (why a dedicated GBR subscriber/UE profile is needed,
the precise Open5GS subscriber-provisioning commands including a real IMSI-length pitfall hit during
development, the CU-CP-vs-DU E2-Node-ID distinction that silently misroutes the control action if gotten wrong,
and the exact gNB log lines that confirm the MFBR cap actually reached the scheduler vs. just completed the
signaling round trip). [`GBR_MFBR_QOS_ENFORCEMENT.md`](../../GBR_MFBR_QOS_ENFORCEMENT.md) §F has the three
RRC/F1AP signaling bugs this patch also fixes, with their own reproduction steps if you want to confirm the
*absence* of the original failures on your tree.
