#!/usr/bin/env python3
#
# One-shot DL-focused MFBR test: cap only max_dl_kbps (leave UL at a high/non-binding value) to isolate and
# re-confirm DL enforcement specifically, mirroring ul_mfbr_test_xapp.py's structure.

import time
import datetime
import argparse
import signal
from lib.xAppBase import xAppBase


class DlMfbrTestXapp(xAppBase):
    def __init__(self, config, http_server_port, rmr_port):
        super(DlMfbrTestXapp, self).__init__(config, http_server_port, rmr_port)

    @xAppBase.start_function
    def start(self, e2_node_id, amf_ue_ngap_id, gnb_cu_ue_f1ap_id, plmn, drb_id, max_dl_kbps, hold_seconds):
        current_time = datetime.datetime.now()
        print("{} Send ONE-SHOT RIC Control Request: max_dl_kbps={} (UL left uncapped: 1000000)".format(
            current_time.strftime("%H:%M:%S"), max_dl_kbps))
        self.e2sm_rc.control_drb_qos(e2_node_id, amf_ue_ngap_id, gnb_cu_ue_f1ap_id, plmn, drb_id,
                                     max_dl_kbps=max_dl_kbps, max_ul_kbps=1000000, ack_request=1)
        print("Control action sent. Holding for {}s - watch gNB log/RLC DL SDU rate for enforcement.".format(hold_seconds))
        elapsed = 0
        while self.running and elapsed < hold_seconds:
            time.sleep(5)
            elapsed += 5
        print("Hold complete, exiting.")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='One-shot DL-only MFBR test xApp (Style 1 / Action 1)')
    parser.add_argument("--config", type=str, default='')
    parser.add_argument("--http_server_port", type=int, default=8090)
    parser.add_argument("--rmr_port", type=int, default=4560)
    parser.add_argument("--e2_node_id", type=str, default='gnb_001_001_00019b')
    parser.add_argument("--ran_func_id", type=int, default=3)
    parser.add_argument("--plmn", type=str, default='00101')
    parser.add_argument("--amf_ue_ngap_id", type=int, required=True)
    parser.add_argument("--gnb_cu_ue_f1ap_id", type=int, default=0)
    parser.add_argument("--drb_id", type=int, default=1)
    parser.add_argument("--max_dl_kbps", type=int, default=3000)
    parser.add_argument("--hold_seconds", type=int, default=60)

    args = parser.parse_args()

    myXapp = DlMfbrTestXapp(args.config, args.http_server_port, args.rmr_port)
    myXapp.e2sm_rc.set_ran_func_id(args.ran_func_id)

    signal.signal(signal.SIGQUIT, myXapp.signal_handler)
    signal.signal(signal.SIGTERM, myXapp.signal_handler)
    signal.signal(signal.SIGINT, myXapp.signal_handler)

    myXapp.start(args.e2_node_id, args.amf_ue_ngap_id, args.gnb_cu_ue_f1ap_id, args.plmn, args.drb_id,
                 args.max_dl_kbps, args.hold_seconds)
