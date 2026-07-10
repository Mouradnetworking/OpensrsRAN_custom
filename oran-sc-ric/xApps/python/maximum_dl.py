#!/usr/bin/env python3

import time
import datetime
import argparse
import signal
from lib.xAppBase import xAppBase


class MyXapp(xAppBase):
    def __init__(self, config, http_server_port, rmr_port):
        super(MyXapp, self).__init__(config, http_server_port, rmr_port)
        self.last_config = None

    def print_menu(self):
        print("\n========== xApp PRB Control ==========")
        print("1) Set Maximum Downlink Bitrate")
        print("2) Show last configuration")
        print("3) Exit")
        print("======================================")

    def set_max_dl_bitrate(self, e2_node_id, ue_id):
        try:
            user_input = input("Enter maximum DL bitrate in Mbps: ").strip()

            if not user_input:
                print("No value entered.")
                return

            max_dl_mbps = float(user_input)

            if max_dl_mbps <= 0:
                print("Bitrate must be greater than 0 Mbps.")
                return

            max_dl_kbps = int(max_dl_mbps * 1000)

            current_time = datetime.datetime.now().strftime("%H:%M:%S")

            print(
                "{} Sending RIC Control Request to E2 node ID: {} for UE ID: {}, max_dl_kbps: {}".format(
                    current_time,
                    e2_node_id,
                    ue_id,
                    max_dl_kbps
                )
            )

            self.e2sm_rc.control_ue_bitrate_cap(
                e2_node_id,
                ue_id,
                max_dl_kbps=max_dl_kbps,
                ack_request=1
            )

            self.last_config = {
                "time": current_time,
                "e2_node_id": e2_node_id,
                "ue_id": ue_id,
                "max_dl_mbps": max_dl_mbps,
                "max_dl_kbps": max_dl_kbps
            }

            print("Maximum downlink bitrate cap set to {} Mbps.".format(max_dl_mbps))

        except ValueError:
            print("Invalid input. Please enter a number, for example: 10 or 25.5")

        except Exception as e:
            print("Failed to send bitrate control request.")
            print("Error:", e)

    def show_last_configuration(self):
        if self.last_config is None:
            print("No configuration has been sent yet.")
            return

        print("\nLast configuration:")
        print("Time:              {}".format(self.last_config["time"]))
        print("E2 Node ID:        {}".format(self.last_config["e2_node_id"]))
        print("UE ID:             {}".format(self.last_config["ue_id"]))
        print("Max DL Bitrate:    {} Mbps".format(self.last_config["max_dl_mbps"]))
        print("Max DL Bitrate:    {} kbps".format(self.last_config["max_dl_kbps"]))

    @xAppBase.start_function
    def start(self, e2_node_id, ue_id):
        while self.running:
            self.print_menu()

            try:
                option = input("Select option: ").strip()

                if option == "1":
                    self.set_max_dl_bitrate(e2_node_id, ue_id)

                elif option == "2":
                    self.show_last_configuration()

                elif option == "3":
                    print("Exiting xApp...")
                    self.running = False
                    break

                else:
                    print("Invalid option. Please select 1, 2, or 3.")

            except KeyboardInterrupt:
                print("\nKeyboard interrupt received. Exiting xApp...")
                self.running = False
                break

            except EOFError:
                print("\nInput closed. Exiting xApp...")
                self.running = False
                break

            time.sleep(0.2)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Interactive UE bitrate cap control xApp')

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

    myXapp = MyXapp(config, args.http_server_port, args.rmr_port)

    myXapp.e2sm_rc.set_ran_func_id(ran_func_id)

    signal.signal(signal.SIGQUIT, myXapp.signal_handler)
    signal.signal(signal.SIGTERM, myXapp.signal_handler)
    signal.signal(signal.SIGINT, myXapp.signal_handler)

    myXapp.start(e2_node_id, ue_id)
