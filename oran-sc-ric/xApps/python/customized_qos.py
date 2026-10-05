#!/usr/bin/env python3

import argparse
import signal
import datetime

from lib.xAppBase import xAppBase


class MyDrbQosXapp(xAppBase):

    def __init__(self, config, http_server_port, rmr_port):
        super(MyDrbQosXapp, self).__init__(config, http_server_port, rmr_port)
        self.last_bitrate = None

    # Start function of the xApp
    @xAppBase.start_function
    def start(self,
              e2_node_id,
              amf_ue_ngap_id,
              gnb_cu_ue_f1ap_id,
              plmn,
              drb_id):

        while self.running:

            print("\n========== xApp DRB QoS Control ==========")
            print("1) Set Maximum DL/UL Bitrate")
            print("2) Show Last Configuration")
            print("3) Exit")
            print("==========================================")

            choice = input("Select option: ").strip()

            if choice == "1":

                try:
                    bitrate = int(input("Enter maximum bitrate (kbps): "))

                    now = datetime.datetime.now()

                    print(
                        "\n{} Send RIC Control Request to E2 node ID: {} "
                        "for DRB ID: {}, max_dl_kbps: {}, max_ul_kbps: {}".format(
                            now.strftime("%H:%M:%S"),
                            e2_node_id,
                            drb_id,
                            bitrate,
                            bitrate
                        )
                    )

                    # Same behavior as the original code:
                    # DL and UL are always configured with the same value.
                    self.e2sm_rc.control_drb_qos(
                        e2_node_id,
                        amf_ue_ngap_id,
                        gnb_cu_ue_f1ap_id,
                        plmn,
                        drb_id,
                        max_dl_kbps=bitrate,
                        max_ul_kbps=bitrate,
                        ack_request=1
                    )

                    self.last_bitrate = bitrate

                    print("✓ Configuration successfully applied.")

                except ValueError:
                    print("Invalid bitrate. Please enter an integer.")

            elif choice == "2":

                print("\n========== Last Configuration ==========")

                if self.last_bitrate is None:
                    print("No configuration has been sent yet.")
                else:
                    print(f"Maximum DL Bitrate : {self.last_bitrate} kbps")
                    print(f"Maximum UL Bitrate : {self.last_bitrate} kbps")

                print("========================================")

            elif choice == "3":

                print("Stopping xApp...")
                self.running = False

            else:
                print("Invalid option. Please select 1, 2 or 3.")


if __name__ == '__main__':

    parser = argparse.ArgumentParser(
        description='Interactive DRB QoS Configuration xApp'
    )

    parser.add_argument(
        "--config",
        type=str,
        default='',
        help="xApp config file path"
    )

    parser.add_argument(
        "--http_server_port",
        type=int,
        default=8090,
        help="HTTP server listen port"
    )

    parser.add_argument(
        "--rmr_port",
        type=int,
        default=4560,
        help="RMR port"
    )

    parser.add_argument(
        "--e2_node_id",
        type=str,
        default='gnbd_001_001_00019b_0',
        help="E2 Node ID"
    )

    parser.add_argument(
        "--ran_func_id",
        type=int,
        default=3,
        help="E2SM RC RAN Function ID"
    )

    parser.add_argument(
        "--plmn",
        type=str,
        default='00101',
        help="PLMN"
    )

    parser.add_argument(
        "--amf_ue_ngap_id",
        type=int,
        default=1,
        help="AMF UE NGAP ID"
    )

    parser.add_argument(
        "--gnb_cu_ue_f1ap_id",
        type=int,
        default=1,
        help="gNB CU UE F1AP ID"
    )

    parser.add_argument(
        "--drb_id",
        type=int,
        default=1,
        help="DRB ID"
    )

    args = parser.parse_args()

    # Create xApp
    myXapp = MyDrbQosXapp(
        args.config,
        args.http_server_port,
        args.rmr_port
    )

    # Set RAN Function ID
    myXapp.e2sm_rc.set_ran_func_id(args.ran_func_id)

    # Register signal handlers
    signal.signal(signal.SIGQUIT, myXapp.signal_handler)
    signal.signal(signal.SIGTERM, myXapp.signal_handler)
    signal.signal(signal.SIGINT, myXapp.signal_handler)

    # Start xApp
    myXapp.start(
        args.e2_node_id,
        args.amf_ue_ngap_id,
        args.gnb_cu_ue_f1ap_id,
        args.plmn,
        args.drb_id
    )
