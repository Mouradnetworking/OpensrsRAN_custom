srsRAN Project
==============

[![Build Status](https://github.com/srsran/srsRAN_Project/actions/workflows/ccpp.yml/badge.svg?branch=main)](https://github.com/srsran/srsRAN_Project/actions/workflows/ccpp.yml)
[![OpenSSF Best Practices](https://www.bestpractices.dev/projects/7868/badge)](https://www.bestpractices.dev/projects/7868)

The srsRAN Project is a complete 5G RAN solution, featuring an ORAN-native CU/DU developed by [SRS](http://www.srs.io).

The solution includes a complete L1/2/3 implementation with minimal external dependencies. Portable across processor architectures, the software has been optimized for x86 and ARM. srsRAN follows the 3GPP 5G system architecture implementing the functional splits between distributed unit (DU) and centralized unit (CU). The CU is further disaggregated into control plane (CU-CP) and user-plane (CU-UP).

See the [srsRAN Project](https://www.srsran.com/) for information, guides and project news.

Build instructions and user guides - [srsRAN Project documentation](https://docs.srsran.com/projects/project).

Community announcements and support - [Discussion board](https://www.github.com/srsran/srsran_project/discussions).

Features and roadmap - [Features](https://docs.srsran.com/projects/project/en/latest/general/source/2_features_and_roadmap.html).

> **This is a modified fork, and it bundles the whole agentic-RAN stack in one clone.** This repository is a
> fork of the official [srsRAN Project](https://github.com/srsran/srsRAN_Project), maintained at
> <https://github.com/Mouradnetworking/srsran>, extended (since July 2026) with per-slot/per-grant Layer 2
> telemetry, a UE-aggregate bitrate-cap RIC control action, and a standards-compliant per-DRB GBR
> Maximum-Flow-Bit-Rate enforcement mechanism exposed via a new O-RAN E2SM-RC control action ("DRB QoS
> Configuration", Style 1/Action 1), together with three upstream srsRAN bugs found and fixed along the way.
> Full technical documentation, protocol-level traces, and a byte-for-byte reproducible patch bundle (for
> applying just these changes on top of a clean upstream checkout) are under [`agentic_ran/`](./agentic_ran/).
>
> Unlike a typical fork, **this repo also contains the UE (`srsRAN_4G/`), the near-RT RIC platform + xApps
> (`oran-sc-ric/`), and the testbed config used to run them together (`config/`)** — a single `git clone` gets
> the entire stack instead of assembling three separate repos by hand. See
> ["Agentic RAN stack: environment, build, and run order"](#agentic-ran-stack-environment-build-and-run-order)
> below for how to set it all up.
>
> **If you want vanilla, unmodified srsRAN**, use the official upstream repository instead:
> <https://github.com/srsran/srsRAN_Project>.
>
> Like upstream, this fork is licensed under the **GNU Affero General Public License v3.0** (see
> [`LICENSE`](./LICENSE)) — the modifications described above do not change the license terms in any way.

Agentic RAN stack: environment, build, and run order
------------------------------------------------------

This repo contains four pieces that work together:

| Path | What it is |
|---|---|
| `.` (repo root) | The gNB (CU-CP/CU-UP/DU), built from source |
| `srsRAN_4G/` | The UE (`srsue`), built from source |
| `oran-sc-ric/` | The near-RT RIC platform + xApps, run via Docker |
| `config/` | gNB/UE config files for the ZMQ-RF loopback testbed used throughout `agentic_ran/`'s validation work |

You'll also need a **5G core reachable at the address configured in `config/gnb_zmq.yaml`'s `amf.addr`** (an
Open5GS-compatible AMF). The 5GC used while developing this isn't part of this repo — any Open5GS deployment
works, as long as its AMF is reachable at the IP your gNB config points to (and the gNB's `amf.bind_addr` is a
real, routable local interface, not localhost).

### 1. Environment variables

```bash
export LD_LIBRARY_PATH=/usr/local/lib
```

This is needed if you build ZMQ (`libzmq`) or other dependencies from source into `/usr/local/lib` rather than
installing them via your distro's package manager — both the gNB and `srsue` link against it at runtime for the
ZMQ RF driver. Add it to your shell profile (`~/.bashrc`) so it's always set, rather than exporting it manually
each session.

No other environment variables are required for building or running any of the three components — `oran-sc-ric`'s
own runtime environment variables (RMR ports, container IPs, image versions) are read from `oran-sc-ric/.env` by
Docker Compose automatically; you don't need to export them yourself.

### 2. Build order

Build the gNB and the UE before starting anything — the RIC doesn't need a host-side build, it runs entirely in
Docker.

**2.1 — gNB (this repo's root)**

```bash
mkdir -p build && cd build
cmake .. -DENABLE_EXPORT=ON -DENABLE_ZEROMQ=ON -DCMAKE_BUILD_TYPE=Release
make -j$(nproc)
sudo make install   # optional - installs to /usr/local/bin, otherwise run from build/apps/gnb/gnb
```

Confirm `-- Found libZEROMQ: ...` appears in the `cmake` output — if it's missing, ZMQ wasn't detected and the
RF driver used throughout this testbed won't be available.

**2.2 — UE (`srsRAN_4G/`)**

```bash
cd srsRAN_4G
mkdir -p build && cd build
cmake .. -DENABLE_ZEROMQ=ON
make -j$(nproc)
sudo make install   # optional - otherwise run from build/srsue/src/srsue
```

If you rebuild `srsue` after an earlier build (e.g. after pulling new changes), and the new binary doesn't seem
to take effect, delete the stale binary first — a known Make/CMake staleness issue in this tree:
```bash
rm -f build/srsue/src/srsue && make -C build srsue -j$(nproc)
```

**2.3 — RIC (`oran-sc-ric/`)** — no manual build step; Docker Compose builds/pulls everything it needs the first
time you bring it up (see step 2 of the run order below).

### 3. Run order

Everything below assumes the 5GC (step 1) is already up and reachable. Start things in this order — later
components depend on earlier ones being ready:

1. **5G core** — start your Open5GS-compatible 5GC first, reachable at the IP in `config/gnb_zmq.yaml`'s
   `amf.addr`.
2. **Near-RT RIC**, from `oran-sc-ric/`:
   ```bash
   cd oran-sc-ric
   ./create_ric_config_files.sh   # regenerates ric/configs/routes.rtg from .env - only needed if you changed .env
   docker compose up -d
   ```
   Wait for all containers to report healthy (`docker compose ps`) before starting the gNB — the gNB's E2 agents
   connect to the RIC's `e2term` on startup and won't retry indefinitely if it's not there yet.
3. **gNB**, from `config/`:
   ```bash
   cd config
   sudo gnb -c gnb_zmq.yaml
   ```
4. **UE**, from `config/`, once the gNB is up and the cell is broadcasting:
   ```bash
   cd config
   sudo srsue ue_zmq_gbr.conf
   ```
   Confirm attachment succeeded by checking the gNB's log (`/home/<you>/logs/srsgnb.log` per the config's `log:`
   section) for `rrcSetupComplete` / `PDUSessionResourceSetupResponse`.
5. **An xApp**, once the UE is attached — from inside the RIC's xApp runner container:
   ```bash
   docker compose exec python_xapp_runner ./ul_mfbr_test_xapp.py --amf_ue_ngap_id <ID> [other args]
   ```
   Find `<ID>` (the UE's `amf_ue_ngap_id` for this session) by grepping the gNB log for `amf_ue=`. See
   [`agentic_ran/DRB_QOS_XAPP_SYSTEM_DESIGN.md` §10.2](./agentic_ran/DRB_QOS_XAPP_SYSTEM_DESIGN.md) for the full
   list of available xApps and which ones are currently safe to run — **UL-direction DRB QoS control
   (`max_ul_kbps`) is fully validated; DL-direction (`max_dl_kbps`) has a known, unresolved reconfiguration
   issue, see that doc for current status before using it.**

Tearing down: stop in reverse order (UE, then gNB, then `docker compose down` in `oran-sc-ric/`, then the 5GC).

Build Preparation
-----------------

### Dependencies

* Build tools:
  * cmake:               <https://cmake.org/>
* Mandatory requirements:
  * libfftw:             <https://www.fftw.org/>
  * libsctp:             <https://github.com/sctp/lksctp-tools>
  * yaml-cpp:            <https://github.com/jbeder/yaml-cpp>
  * mbedTLS:             <https://www.trustedfirmware.org/projects/mbed-tls/>
* Optional requirements:
  * googletest:          <https://github.com/google/googletest/>
    * You can enable test building by using the cmake option `-DBUILD_TESTING=On`. GoogleTest is only mandatory when building with tests.

You can install the build tools and mandatory requirements for some example distributions with the commands below:

<details open>
<summary><strong>Ubuntu 22.04</strong></summary>

```bash
sudo apt-get install cmake make gcc g++ pkg-config libfftw3-dev libmbedtls-dev libsctp-dev libyaml-cpp-dev
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

#### Split-8

For Split-8 configurations, either UHD or ZMQ is required for the fronthaul interface. Both drivers are linked below, please see their respective documentation for installation instructions.

* UHD:                 <https://github.com/EttusResearch/uhd>
* ZMQ:                 <https://zeromq.org/>

#### Split-7.2

For Split-7.2 configurations no extra 3rd-party dependencies are required, only those listed above.

Optionally, DPDK can be installed for high-bandwidth low-latency scenarios. For more information on this, please see [this tutorial](https://docs.srsran.com/projects/project/en/latest/tutorials/source/dpdk/source/index.html#).

Build Instructions
------------------

Download and build srsRAN:

<details open>
<summary><strong>Vanilla Installation</strong></summary>

First, clone this fork (see the note above if you want vanilla upstream srsRAN instead):

```bash
    git clone https://github.com/Mouradnetworking/srsran.git srsRAN_Project
```

Then build the code-base:

```bash
    cd srsRAN_Project
    mkdir build
    cd build
    cmake ../ 
    make -j $(nproc)
```

You can now run the gNB from ``srsRAN_Project/build/apps/gnb/``. If you wish to install the srsRAN Project gNB, you can use the following command:

```bash
    sudo make install
```

</details>

<details>
<summary><strong>ZMQ Enabled Installation</strong></summary>

Once ZMQ has been installed you will need build of srsRAN Project with the correct flags to enable the use of ZMQ.

The following commands can be used to clone and build this fork from source (see the note near the top of this
README if you want vanilla upstream srsRAN instead). The relevant flags are added to the ``cmake`` command to
enable the use of ZMQ:

```bash
git clone https://github.com/Mouradnetworking/srsran.git srsRAN_Project
cd srsRAN_Project
mkdir build
cd build
cmake ../ -DENABLE_EXPORT=ON -DENABLE_ZEROMQ=ON
make -j $(nproc)
```

Pay extra attention to the cmake console output. Make sure you read the following line to ensure ZMQ has been correctly detected by srsRAN:

```bash
...
-- FINDING ZEROMQ.
-- Checking for module 'ZeroMQ'
--   No package 'ZeroMQ' found
-- Found libZEROMQ: /usr/local/include, /usr/local/lib/libzmq.so
...
```

</details>

<details>
<summary><strong>DPDK Enabled Installation</strong></summary>

Once DPDK has been installed and configured you will need to create a clean build of srsRAN Project to enable the use of DPDK.

If you have not done so already, download the code-base with the following command (see the note near the top
of this README if you want vanilla upstream srsRAN instead):

```bash
git clone https://github.com/Mouradnetworking/srsran.git srsRAN_Project
```

Then build the code-base, making sure to include the correct flags when running cmake:

```bash
cd srsRAN_Project
mkdir build
cd build
cmake ../ -DENABLE_DPDK=True -DASSERT_LEVEL=MINIMAL
make -j $(nproc)
```

</details>

### PHY Tests

PHY layer tests use binary test vectors and are not built by default. To enable, see the [docs](https://docs.srsran.com/projects/project/en/latest/user_manuals/source/installation.html).

Deploying srsRAN Project
------------------------

srsRAN Project can be run in two ways:

* As a monolithic gNB (combined CU & DU)
* With a split CU and DU

For exact details on running srsRAN Project in any configuration, see [the documentation](https://docs.srsran.com/projects/project/en/latest/user_manuals/source/running.html).

For information on configuring and running srsRAN for various different use cases,  check our [tutorials](https://docs.srsran.com/projects/project/en/latest/tutorials/source/index.html).
