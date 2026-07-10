#!/usr/bin/env python3

import time
import datetime
import argparse
import signal
from lib.xAppBase import xAppBase


class MyXapp(xAppBase):

    def __init__(self, config, http_server_port, rmr_port):
        super(MyXapp, self).__init__(config, http_server_port, rmr_port)

        self.last_min_prb = None
        self.last_max_prb = None
        self.selected_node = None
        self.selected_ue = None


    def discover_e2_nodes(self):
        """
        Discover E2 nodes using Subscription Manager
        """
        try:
            nodes = self.get_e2_nodes()
            return nodes
        except Exception as e:
            print("Failed to retrieve E2 nodes:", e)
            return []


    def select_node(self):

        nodes = self.discover_e2_nodes()

        if not nodes:
            print("No E2 nodes available.")
            return None

        print("\nAvailable E2 Nodes:")
        for i, node in enumerate(nodes):
            print(f"{i+1}) {node}")

        try:
            choice = int(input("Select node: "))
            return nodes[choice-1]
        except:
            print("Invalid selection.")
            return None


    def select_ue(self):

        try:
            ue_id = int(input("Enter UE ID (gnb_cu_ue_f1ap_id): "))
            return ue_id
        except:
            print("Invalid UE ID")
            return None


    def send_prb_control(self):

        try:
            min_prb = int(input("Enter PRB MIN (0-100): "))
            max_prb = int(input("Enter PRB MAX (0-100): "))

            if min_prb < 0 or max_prb > 100 or min_prb > max_prb:
                print("Invalid PRB range")
                return

            now = datetime.datetime.now()

            print("{} Sending Control → Node: {} UE: {} PRB: {}-{}".format(
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
            print("Invalid numeric input")


    @xAppBase.start_function
    def start(self, e2_node_id, ue_id):

        while self.running:

            print("\n=========== xApp RAN Controller ===========")
            print("1) Discover E2 Nodes")
            print("2) Select E2 Node")
            print("3) Select UE")
            print("4) Send PRB Control")
            print("5) Show Last Configuration")
            print("6) Exit")
            print("===========================================")

            choice = input("Select option: ")

            if choice == "1":

                nodes = self.discover_e2_nodes()

                if nodes:
                    print("\nDetected E2 Nodes:")
                    for n in nodes:
                        print("-", n)
                else:
                    print("No nodes detected")


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
                    print("Select an E2 node first")
                    continue

                if self.selected_ue is None:
                    print("Select a UE first")
                    continue

                self.send_prb_control()


            elif choice == "5":

                if self.last_min_prb is None:
                    print("No configuration sent yet.")
                else:
                    print("Last PRB configuration:")
                    print("MIN:", self.last_min_prb)
                    print("MAX:", self.last_max_prb)


            elif choice == "6":

                print("Stopping xApp...")
                self.running = False
                break


            else:
                print("Invalid option")

            time.sleep(1)


if __name__ == '__main__':

    parser = argparse.ArgumentParser(description='Interactive RIC xApp Controller')

    parser.add_argument("--config", type=str, default='', help="xApp config file path")
    parser.add_argument("--http_server_port", type=int, default=8090)
    parser.add_argument("--rmr_port", type=int, default=4560)

    parser.add_argument("--e2_node_id", type=str, default='')
    parser.add_argument("--ran_func_id", type=int, default=3)
    parser.add_argument("--gnb_cu_ue_f1ap_id", type=int, default=0)

    args = parser.parse_args()

    myXapp = MyXapp(args.config, args.http_server_port, args.rmr_port)

    myXapp.e2sm_rc.set_ran_func_id(args.ran_func_id)

    signal.signal(signal.SIGQUIT, myXapp.signal_handler)
    signal.signal(signal.SIGTERM, myXapp.signal_handler)
    signal.signal(signal.SIGINT, myXapp.signal_handler)

    myXapp.start(args.e2_node_id, args.gnb_cu_ue_f1ap_id)
