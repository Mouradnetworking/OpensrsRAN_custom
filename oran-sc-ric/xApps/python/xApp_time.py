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

        # action log file
        self.log_file = "prb_actions.log"

        # create log file header
        with open(self.log_file, "w") as f:
            f.write("timestamp_epoch,action,prb_min,prb_max\n")


    def log_action(self, min_prb, max_prb):
        """
        Save PRB control action timestamp
        """
        epoch_time = time.time()

        with open(self.log_file, "a") as f:
            f.write(f"{epoch_time},PRB_CHANGE,{min_prb},{max_prb}\n")

        return epoch_time


    # xApp start function
    @xAppBase.start_function
    def start(self, e2_node_id, ue_id):

        while self.running:

            print("\n========== xApp PRB Control ==========")
            print("1) Set PRB quota")
            print("2) Show last configuration")
            print("3) Exit")
            print("======================================")

            choice = input("Select option: ")

            if choice == "1":

                try:
                    min_prb_ratio = int(input("Enter PRB MIN ratio (0-100): "))
                    max_prb_ratio = int(input("Enter PRB MAX ratio (0-100): "))

                    if min_prb_ratio < 0 or max_prb_ratio > 100 or min_prb_ratio > max_prb_ratio:
                        print("Invalid PRB values. Ensure 0 <= min <= max <= 100")
                        continue

                    now = datetime.datetime.now()
                    epoch_time = time.time()

                    print(
                        "{} Send RIC Control Request to E2 node ID: {} for UE ID: {}, PRB_min_ratio: {}, PRB_max_ratio: {}".format(
                            now.strftime("%H:%M:%S"),
                            e2_node_id,
                            ue_id,
                            min_prb_ratio,
                            max_prb_ratio
                        )
                    )

                    # send control command
                    self.e2sm_rc.control_slice_level_prb_quota(
                        e2_node_id,
                        ue_id,
                        min_prb_ratio,
                        max_prb_ratio,
                        dedicated_prb_ratio=100,
                        ack_request=1
                    )

                    # log timestamp
                    action_time = self.log_action(min_prb_ratio, max_prb_ratio)

                    print(f"Action timestamp (epoch): {action_time}")

                    self.last_min_prb = min_prb_ratio
                    self.last_max_prb = max_prb_ratio

                except ValueError:
                    print("Invalid input. Please enter numeric values.")

            elif choice == "2":

                if self.last_min_prb is None:
                    print("No configuration sent yet.")
                else:
                    print("Last PRB configuration:")
                    print("PRB MIN =", self.last_min_prb)
                    print("PRB MAX =", self.last_max_prb)

            elif choice == "3":
                print("Stopping xApp...")
                self.running = False
                break

            else:
                print("Invalid option. Try again.")

            time.sleep(1)


if __name__ == '__main__':

    parser = argparse.ArgumentParser(description='My example xApp')

    parser.add_argument("--config", type=str, default='', help="xApp config file path")
    parser.add_argument("--http_server_port", type=int, default=8090, help="HTTP server listen port")
    parser.add_argument("--rmr_port", type=int, default=4560, help="RMR port")

    parser.add_argument("--e2_node_id", type=str, default='gnbd_001_001_00019b_0', help="E2 Node ID")
    parser.add_argument("--ran_func_id", type=int, default=3, help="E2SM RC RAN function ID")
    parser.add_argument("--gnb_cu_ue_f1ap_id", type=int, default=0, help="UE ID")

    args = parser.parse_args()

    config = args.config
    e2_node_id = args.e2_node_id
    ran_func_id = args.ran_func_id
    ue_id = args.gnb_cu_ue_f1ap_id

    # Create MyXapp
    myXapp = MyXapp(config, args.http_server_port, args.rmr_port)

    # Set RAN Function ID
    myXapp.e2sm_rc.set_ran_func_id(ran_func_id)

    # Connect exit signals
    signal.signal(signal.SIGQUIT, myXapp.signal_handler)
    signal.signal(signal.SIGTERM, myXapp.signal_handler)
    signal.signal(signal.SIGINT, myXapp.signal_handler)

    # Start xApp
    myXapp.start(e2_node_id, ue_id)
