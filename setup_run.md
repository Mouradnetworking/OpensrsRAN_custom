# Project Architecture and Execution Guide

## Configuration Files

### gNB Configuration

* **Configuration file:** `/home/mourad6g/config/gnb_zmq.yaml`

### UE Configuration

* **Configuration file:** `/home/mourad6g/config/ue_zmq.conf`

---

## Running the RAN Components

Open **two separate terminals** from the home directory (`~/`).
# Project Architecture and Execution Guide

## Configuration Files

### gNB Configuration

* **Configuration file:** `/home/mourad6g/config/gnb_zmq.yaml`

### UE Configuration

* **Configuration file:** `/home/mourad6g/config/ue_zmq.conf`

---

## Running the RAN Components

Open **two separate terminals** from the home directory (`~/`).

### Start the gNB

```bash
./gnb.sh
```

### Start the UE

```bash
./ue.sh
```

---

## O-RAN SC RIC

### Project Directory

```text
/home/mourad6g/oran-sc-ric
```

### Start the RIC

```bash
startric
```

`startric` is available through an environment variable.

---

## 5G Core Network

Start the core network using:

```bash
start5gc
```

This command is also available through an environment variable.

---

# xApp Execution

There are two ways to run the xApp.

## Method 1: Enter the Python xApp Container

Open a terminal and execute:

```bash
cd ~/oran-sc-ric
docker compose exec python_xapp_runner bash
```

Inside the container, run the Advanced KPM xApp:

```bash
python3 advanced_kpm.py
```

---

## Method 2: Run the KPM Monitoring xApp Directly

From the `~/oran-sc-ric` directory, execute:

```bash
docker compose exec python_xapp_runner ./kpm_mon_xapp.py --kpm_report_style=5
```

---

# Execution Order

A typical startup sequence is:

1. Start the 5G Core:

   ```bash
   start5gc
   ```
2. Start the O-RAN SC RIC:

   ```bash
   startric
   ```
3. Start the gNB:

   ```bash
   ./gnb.sh
   ```
4. Start the UE:

   ```bash
   ./ue.sh
   ```
5. Launch the desired xApp using one of the methods described above.

---

# Notes for AI Agents

* The commands `start5gc` and `startric` are available through environment variables and can be executed directly from a terminal.
* The gNB and UE are started using wrapper scripts (`gnb.sh` and `ue.sh`) located in the home directory (`~/`).
* The O-RAN SC RIC project is located at:

  ```
  /home/mourad6g/oran-sc-ric
  ```
* The RAN configuration files are located in:

  ```
  /home/mourad6g/config/
  ```
* When testing xApps, ensure that the 5G Core, RIC, gNB, and UE are already running before launching the xApp.

### Start the gNB

```bash
./gnb.sh
```

### Start the UE

```bash
./ue.sh
```

---

## O-RAN SC RIC

### Project Directory

```text
/home/mourad6g/oran-sc-ric
```

### Start the RIC

```bash
startric
```

`startric` is available through an environment variable.

---

## 5G Core Network

Start the core network using:

```bash
start5gc
```

This command is also available through an environment variable.

---

# xApp Execution

There are two ways to run the xApp.

## Method 1: Enter the Python xApp Container

Open a terminal and execute:

```bash
cd ~/oran-sc-ric
docker compose exec python_xapp_runner bash
```

Inside the container, run the Advanced KPM xApp:

```bash
python3 advanced_kpm.py
```

---

## Method 2: Run the KPM Monitoring xApp Directly

From the `~/oran-sc-ric` directory, execute:

```bash
docker compose exec python_xapp_runner ./kpm_mon_xapp.py --kpm_report_style=5
```

---

# Execution Order

A typical startup sequence is:

1. Start the 5G Core:

   ```bash
   start5gc
   ```
2. Start the O-RAN SC RIC:

   ```bash
   startric
   ```
3. Start the gNB:

   ```bash
   ./gnb.sh
   ```
4. Start the UE:

   ```bash
   ./ue.sh
   ```
5. Launch the desired xApp using one of the methods described above.

---

# Notes for AI Agents

* The commands `start5gc` and `startric` are available through environment variables and can be executed directly from a terminal.
* The gNB and UE are started using wrapper scripts (`gnb.sh` and `ue.sh`) located in the home directory (`~/`).
* The O-RAN SC RIC project is located at:

  ```
  /home/mourad6g/oran-sc-ric
  ```
* The RAN configuration files are located in:

  ```
  /home/mourad6g/config/
  ```
* When testing xApps, ensure that the 5G Core, RIC, gNB, and UE are already running before launching the xApp.
