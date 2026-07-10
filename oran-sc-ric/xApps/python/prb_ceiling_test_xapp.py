#!/usr/bin/env python3
#
# One-shot test: set a single DRB's MFBR DL/UL cap to a value clearly above this cell's real,
# PRB-derived throughput ceiling (previously measured ~53.6 Mbps at less-than-100% PRB allocation
# for this test cell), then hold it - no repeated cycling, to keep this test isolated from the
# separately-characterized reconfiguration-triggered srsue buffer-pool issue.
#
# Goal: confirm the achieved throughput plateaus at the real PRB/MCS ceiling, not at the requested
# MFBR value, with no crash/assertion failure/scheduler misbehavior on the gNB side.

import time
import datetime
import argparse
import signal
from lib.xAppBase import xAppBase


class PrbCeilingTestXapp(xAppBase):
    def __init__(self, config, http_server_port, rmr_port):
        super(PrbCeilingTestXapp, self).__init__(config, http_server_port, rmr_port)

    @xAppBase.start_function
    def start(self, e2_node_id, amf_ue_ngap_id, gnb_cu_ue_f1ap_id, plmn, drb_id, max_kbps, hold_seconds):
        current_time = datetime.datetime.now()
        print("{} Send ONE-SHOT RIC Control Request to E2 node ID: {} for DRB ID: {}, max_dl_kbps: {}, max_ul_kbps: {}".format(
            current_time.strftime("%H:%M:%S"), e2_node_id, drb_id, max_kbps, max_kbps))
        self.e2sm_rc.control_drb_qos(e2_node_id, amf_ue_ngap_id, gnb_cu_ue_f1ap_id, plmn, drb_id,
                                     max_dl_kbps=max_kbps, max_ul_kbps=max_kbps, ack_request=1)
        print("Control action sent. Holding for {}s with no further reconfiguration - watch InfluxDB/gNB log "
              "for achieved throughput plateauing at the PRB ceiling, not at {} kbps.".format(hold_seconds, max_kbps))
        elapsed = 0
        while self.running and elapsed < hold_seconds:
            time.sleep(5)
            elapsed += 5
        print("Hold complete, exiting.")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='One-shot high-MFBR PRB-ceiling test xApp (Style 1 / Action 1)')
    parser.add_argument("--config", type=str, default='', help="xApp config file path")
    parser.add_argument("--http_server_port", type=int, default=8090, help="HTTP server listen port")
    parser.add_argument("--rmr_port", type=int, default=4560, help="RMR port")
    parser.add_argument("--e2_node_id", type=str, default='gnb_001_001_00019b', help="E2 Node ID (CU-CP)")
    parser.add_argument("--ran_func_id", type=int, default=3, help="E2SM RC RAN function ID")
    parser.add_argument("--plmn", type=str, default='00101', help="PLMN (e.g., 00101)")
    parser.add_argument("--amf_ue_ngap_id", type=int, required=True, help="AMF UE NGAP ID")
    parser.add_argument("--gnb_cu_ue_f1ap_id", type=int, default=0, help="gNB CU UE F1AP ID")
    parser.add_argument("--drb_id", type=int, default=1, help="DRB ID to reconfigure")
    parser.add_argument("--max_kbps", type=int, default=200000, help="MFBR DL/UL cap in kbps (default 200 Mbps)")
    parser.add_argument("--hold_seconds", type=int, default=60, help="How long to hold before exiting")

    args = parser.parse_args()

    myXapp = PrbCeilingTestXapp(args.config, args.http_server_port, args.rmr_port)
    myXapp.e2sm_rc.set_ran_func_id(args.ran_func_id)

    signal.signal(signal.SIGQUIT, myXapp.signal_handler)
    signal.signal(signal.SIGTERM, myXapp.signal_handler)
    signal.signal(signal.SIGINT, myXapp.signal_handler)

    myXapp.start(args.e2_node_id, args.amf_ue_ngap_id, args.gnb_cu_ue_f1ap_id, args.plmn, args.drb_id,
                 args.max_kbps, args.hold_seconds)
