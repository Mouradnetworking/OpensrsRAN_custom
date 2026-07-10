#!/usr/bin/env python3

import time
import datetime
import argparse
import signal
from lib.xAppBase import xAppBase

class MyXapp(xAppBase):
    def __init__(self, config, http_server_port, rmr_port):
        super(MyXapp, self).__init__(config, http_server_port, rmr_port)
        pass

    # Mark the function as xApp start function using xAppBase.start_function decorator.
    # It is required to start the internal msg receive loop.
    @xAppBase.start_function
    def start(self, e2_node_id, ue_id):
        # Cycles the UE's DL and UL bitrate caps together through a few steps, holding each for 5s, so the
        # effect on achieved throughput can be observed (e.g. via the dl_brate/ul_brate fields in
        # InfluxDB/Grafana). DL and UL are capped to the same value at each step for simplicity; they are
        # independent RIC parameters (max_dl_kbps/max_ul_kbps) and could be driven separately if needed.
        steps_kbps = [5000, 15000, 30000, 60000]
        while self.running:
            for kbps in steps_kbps:
                current_time = datetime.datetime.now()
                print("{} Send RIC Control Request to E2 node ID: {} for UE ID: {}, max_dl_kbps: {}, max_ul_kbps: {}".format(
                    current_time.strftime("%H:%M:%S"), e2_node_id, ue_id, kbps, kbps))
                self.e2sm_rc.control_ue_bitrate_cap(e2_node_id, ue_id, max_dl_kbps=kbps, max_ul_kbps=kbps, ack_request=1)
                time.sleep(5)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='UE bitrate cap test xApp')
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

    # Create MyXapp.
    myXapp = MyXapp(config, args.http_server_port, args.rmr_port)
    myXapp.e2sm_rc.set_ran_func_id(ran_func_id)

    # Connect exit signals.
    signal.signal(signal.SIGQUIT, myXapp.signal_handler)
    signal.signal(signal.SIGTERM, myXapp.signal_handler)
    signal.signal(signal.SIGINT, myXapp.signal_handler)

    # Start xApp.
    myXapp.start(e2_node_id, ue_id)
