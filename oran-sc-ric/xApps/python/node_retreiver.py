#!/usr/bin/env python3

import time
import datetime
import argparse
import signal
import json
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from lib.xAppBase import xAppBase


class MyXapp(xAppBase):
    def __init__(self, config, http_server_port, rmr_port, e2mgr_uri):
        super(MyXapp, self).__init__(config, http_server_port, rmr_port)

        self.e2mgr_uri = e2mgr_uri.rstrip("/")
        self.last_min_prb = None
        self.last_max_prb = None
        self.selected_node = None
        self.selected_ue = None

    # --------- E2MGR discovery helpers ---------

    def _http_get_json(self, url, timeout=2):
        req = Request(url, headers={"Accept": "application/json"})
        with urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode("utf-8")
            return json.loads(data)

    def discover_e2_nodes(self):
        """
        Query E2MGR for connected E2 nodes.
        Default API: GET {E2MGR}/v1/nodeb/states
        """
        url = f"{self.e2mgr_uri}/nodeb/states"
        try:
            payload = self._http_get_json(url)

            # Most common response is a JSON list.
            if isinstance(payload, list):
                items = payload
            elif isinstance(payload, dict):
                # Some deployments wrap the list in a key; try common keys.
                for key in ("nodebStates", "nodeb_states", "data", "items"):
                    if key in payload and isinstance(payload[key], list):
                        items = payload[key]
                        break
                else:
                    items = []
            else:
                items = []

            nodes = []
            for it in items:
                if not isinstance(it, dict):
                    continue

                ran = it.get("ranName") or it.get("inventoryName") or it.get("nodebId") or it.get("name")
                status = it.get("connectionStatus") or it.get("ConnectionStatus") or it.get("status")

                if ran:
                    nodes.append({"ranName": ran, "connectionStatus": status})

            return nodes

        except (HTTPError, URLError, TimeoutError) as e:
            print(f"[E2MGR] Cannot reach {url} -> {e}")
            return []
        except Exception as e:
            print(f"[E2MGR] Unexpected error: {e}")
            return []

    def print_nodes(self, nodes):
        if not nodes:
            print("No E2 nodes returned by E2MGR.")
            return
        print("\nDetected E2 Nodes (from E2MGR):")
        for i, n in enumerate(nodes, start=1):
            print(f"{i}) {n['ranName']}   status={n.get('connectionStatus')}")

    def select_node(self):
        nodes = self.discover_e2_nodes()
        if not nodes:
            print("No nodes available (E2MGR returned empty or unreachable).")
            return None

        # Prefer CONNECTED nodes
        connected = [n for n in nodes if str(n.get("connectionStatus", "")).upper() == "CONNECTED"]
        use_list = connected if connected else nodes

        print("\nAvailable E2 Nodes:")
        for i, n in enumerate(use_list, start=1):
            print(f"{i}) {n['ranName']}   status={n.get('connectionStatus')}")

        try:
            choice = int(input("Select node number: ").strip())
            if choice < 1 or choice > len(use_list):
                print("Invalid selection.")
                return None
            return use_list[choice - 1]["ranName"]
        except ValueError:
            print("Invalid selection (must be a number).")
            return None

    # --------- UE + PRB control helpers ---------

    def select_ue(self):
        try:
            ue_id = int(input("Enter UE ID (gnb_cu_ue_f1ap_id): ").strip())
            return ue_id
        except ValueError:
            print("Invalid UE ID (must be a number).")
            return None

    def send_prb_control(self):
        try:
            min_prb = int(input("Enter PRB MIN ratio (0-100): ").strip())
            max_prb = int(input("Enter PRB MAX ratio (0-100): ").strip())

            if min_prb < 0 or max_prb > 100 or min_prb > max_prb:
                print("Invalid range. Ensure 0 <= min <= max <= 100.")
                return

            now = datetime.datetime.now()
            print("{} Send RIC Control → Node: {} UE: {} PRB_min: {} PRB_max: {}".format(
                now.strftime("%H:%M:%S"),
                self.selected_node,
                self.selected_ue,
                min_prb,
                max_prb
            ))

            self.e2sm_rc.control_slice_level_prb_quota(
                self.selected_node,
                self.selected_ue,
                min_prb,
                max_prb,
                dedicated_prb_ratio=100,
                ack_request=1
            )

            self.last_min_prb = min_prb
            self.last_max_prb = max_prb

        except ValueError:
            print("Invalid input (must be numbers).")

    # --------- Main CLI loop ---------

    @xAppBase.start_function
    def start(self, _e2_node_id_unused, _ue_id_unused):
        while self.running:
            print("\n=========== xApp RAN Controller (CLI) ===========")
            print("1) Discover E2 Nodes (E2MGR)")
            print("2) Select E2 Node")
            print("3) Select UE")
            print("4) Send PRB Control (min/max)")
            print("5) Show Last PRB Configuration")
            print("6) Exit")
            print("================================================")

            choice = input("Select option: ").strip()

            if choice == "1":
                nodes = self.discover_e2_nodes()
                self.print_nodes(nodes)

            elif choice == "2":
                node = self.select_node()
                if node:
                    self.selected_node = node
                    print("Selected node:", node)

            elif choice == "3":
                ue = self.select_ue()
                if ue is not None:
                    self.selected_ue = ue
                    print("Selected UE:", ue)

            elif choice == "4":
                if not self.selected_node:
                    print("Select an E2 node first (option 2).")
                    continue
                if self.selected_ue is None:
                    print("Select a UE first (option 3).")
                    continue
                self.send_prb_control()

            elif choice == "5":
                if self.last_min_prb is None:
                    print("No PRB configuration sent yet.")
                else:
                    print(f"Last PRB: MIN={self.last_min_prb}, MAX={self.last_max_prb}")

            elif choice == "6":
                print("Stopping xApp...")
                self.running = False
                break

            else:
                print("Invalid option.")

            time.sleep(0.2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Interactive RIC xApp Controller (E2MGR discovery)")

    parser.add_argument("--config", type=str, default="", help="xApp config file path")
    parser.add_argument("--http_server_port", type=int, default=8090, help="HTTP server listen port")
    parser.add_argument("--rmr_port", type=int, default=4560, help="RMR port")

    # E2MGR discovery endpoint (default matches oran-sc-ric .env typical setup)
    parser.add_argument("--e2mgr_uri", type=str, default="http://10.0.2.11:3800/v1",
                        help="E2MGR base URI (example: http://10.0.2.11:3800/v1)")

    parser.add_argument("--ran_func_id", type=int, default=3, help="E2SM RC RAN function ID")

    args = parser.parse_args()

    myXapp = MyXapp(args.config, args.http_server_port, args.rmr_port, args.e2mgr_uri)
    myXapp.e2sm_rc.set_ran_func_id(args.ran_func_id)

    signal.signal(signal.SIGQUIT, myXapp.signal_handler)
    signal.signal(signal.SIGTERM, myXapp.signal_handler)
    signal.signal(signal.SIGINT, myXapp.signal_handler)

    # Start (node/ue are chosen in CLI now)
    myXapp.start("", 0)
