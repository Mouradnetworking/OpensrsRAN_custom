# Reproduction guide — UL/DL MFBR scheduler fixes + srsue stability fixes

This patch bundle captures every change made in the 2026-07-10 session on top of the earlier
[`gbr_mfbr_xapp`](../gbr_mfbr_xapp/README.md) bundle. Read
[`../../UL_MFBR_ENFORCEMENT_STATUS.md`](../../UL_MFBR_ENFORCEMENT_STATUS.md) first for *what* this is and *why*
(root causes, fixes, live-validation results, and — importantly — the still-open DL reconfiguration crash); this
file is only the mechanical "how to get the same tree" guide.

**Status: UL-side changes are fully validated. DL-side scheduler changes are correct but the feature as a whole
is not yet safe for repeated DL use — see the status doc §2.3.** Every `srsue` change here is a general stability
fix (not MFBR-specific) and is independently useful regardless of MFBR.

## Contents of this bundle

```
ul_dl_mfbr_fixes_and_srsue_stability/
├── README.md                          (this file)
├── srsran_modified_files.patch         7 modified files: scheduler (UL/DL MFBR clamp+fix), 1 test file
├── srsran_4g_modified_files.patch      10 modified files: srsue stability fixes (see below)
└── new_files/
    └── agentic_ran/
        └── UL_MFBR_ENFORCEMENT_STATUS.md   Full investigation writeup, root causes, live-validation numbers
```

## Base repositories and commits

| Repo | Base commit/branch | Role |
|---|---|---|
| `srsRAN_Project` | `3a46b6ab58` ("adding futur enhancements", branch `dev2`) | The RAN implementation — `srsran_modified_files.patch` applies on top. All of it is **uncommitted** as of this writing. |
| `srsRAN_4G` | `6bcbd9e5b` ("Merge branch 'next' into agpl_next") | The UE implementation — `srsran_4g_modified_files.patch` applies on top. Also **entirely uncommitted**; no commits exist on this repo across the whole 3-day working period. Currently checked out at `srsRAN_Project/srsRAN_4G/` (moved there alongside `config/` and `oran-sc-ric/` at some point during this work — check your own layout if paths differ). |

## Reproduce the srsRAN_Project (gNB scheduler) changes

```sh
cd srsRAN_Project
git checkout 3a46b6ab58   # or whatever your current dev2 HEAD is, if later
git apply agentic_ran/patches/ul_dl_mfbr_fixes_and_srsue_stability/srsran_modified_files.patch
```

7 files: `lib/scheduler/{slicing/slice_ue_repository.h, support/ue_bitrate_limiter.cpp,
ue_context/logical_channel_system.{h,cpp}, ue_scheduling/{grant_params_selector.cpp,
intra_slice_scheduler.cpp}}` + `tests/unittests/scheduler/ue_context/logical_channel_system_test.cpp`. Every new
code path is gated behind `is_capped()`/`has_ul_mfbr_cap()` — verified line-by-line, see the status doc §5. Two
of the changes are temporary diagnostic `srslog` lines (`DIAG` in `grant_params_selector.cpp`, `DIAG-DL`/
`DIAG-DL-ALLOC` in `logical_channel_system.cpp`) still present pending the DL crash investigation — safe to leave
in (read-only, gated, no state mutation) or strip out if you don't need them.

## Reproduce the srsRAN_4G (UE) changes

```sh
cd srsRAN_4G
git checkout 6bcbd9e5b   # or your current base, if later
git apply /path/to/srsRAN_Project/agentic_ran/patches/ul_dl_mfbr_fixes_and_srsue_stability/srsran_4g_modified_files.patch
```

10 files, five logically-independent fixes:

| Fix | Files | What it does |
|---|---|---|
| RLC log-variable fix | `lib/src/rlc/rlc_um_nr.cc` | One-line fix: an error-log statement inside an existing error path was reading the wrong variable (`rx_sdu`→`pdu.sdu`). No decision-logic change. |
| UL HARQ concurrency fix | `srsue/{hdr,src}/stack/mac_nr/ul_harq_nr.{h,cc}` | Adds a `pthread_rwlock_t` around UL HARQ process state, protecting against a data race between PHY worker threads and reset/reconfig calls. |
| Bounded PDU queue | `srsue/{hdr,src}/stack/mac_nr/demux_nr.{h,cc}`, `srsue/src/stack/{ue_stack_lte,ue_stack_nr}.cc` | Bounds the PHY→Stack PDU handoff queue to 512 items; switches from a blocking push to a non-blocking drop-and-log when full, instead of unbounded growth. Part of the buffer-pool-leak investigation — evidently didn't fully close it, see status doc §2.3. |
| UL BSR reservation | `srsue/src/stack/mac_nr/mux_nr.cc` | Reserves 2 bytes unconditionally in every UL PDU for a potential Short BSR MAC CE, so the opportunistic padding-BSR check always has room. This was the fix that resolved the UL MFBR BSR-staleness issue (status doc §3.1). |
| `leave_connected()` + reconfiguration-failure surfacing | `srsue/{hdr,src}/stack/rrc_nr/rrc_nr.{h,cc}` | Implements a previously-missing RRC state-teardown path, now called both from `rrc_release()` and from a standalone-NR `ra_problem()` (previously a bare `// TODO`, meaning repeated RA failure left the UE silently stuck). Also adds warning logs when `rlc->add_bearer()`/`pdcp->add_bearer()` silently fail to apply a reconfiguration to an already-active bearer — this is the leading unconfirmed lead for the DL crash, see status doc §2.3. |

None of the `srsue` changes are gated behind MFBR configuration (they're general fixes), but each is individually
low-risk — see `UL_MFBR_ENFORCEMENT_STATUS.md` §5 for the full per-file safety audit.

## Apply the new doc

```sh
cp agentic_ran/patches/ul_dl_mfbr_fixes_and_srsue_stability/new_files/agentic_ran/UL_MFBR_ENFORCEMENT_STATUS.md \
   agentic_ran/UL_MFBR_ENFORCEMENT_STATUS.md
```

(Already present if you're working from this same checkout — this is only needed when reproducing on a fresh
tree from just the patches.)
