#!/usr/bin/env python3
#
# Minimal UL MFBR PoC: apply one UL cap, observe, then remove the cap (uncapped), observe, exit.
# Fires exactly two RIC Control Requests total - not periodic. DL is left uncapped throughout,
# to isolate UL enforcement specifically.

import time
import datetime
import argparse
import signal
from lib.xAppBase import xAppBase

UNCAPPED_KBPS = 1000000  # placeholder value meaning "no limit", matching convention used elsewhere


class UlMfbrTestXapp(xAppBase):
    def __init__(self, config, http_server_port, rmr_port):
        super(UlMfbrTestXapp, self).__init__(config, http_server_port, rmr_port)

    def _send_cap(self, e2_node_id, amf_ue_ngap_id, gnb_cu_ue_f1ap_id, plmn, drb_id, max_ul_kbps, step_label):
        current_time = datetime.datetime.now()
        print("{} [{}] Sending RIC Control Request: max_ul_kbps={} (DL left uncapped: {})".format(
            current_time.strftime("%H:%M:%S"), step_label, max_ul_kbps, UNCAPPED_KBPS))
        self.e2sm_rc.control_drb_qos(e2_node_id, amf_ue_ngap_id, gnb_cu_ue_f1ap_id, plmn, drb_id,
                                     max_dl_kbps=UNCAPPED_KBPS, max_ul_kbps=max_ul_kbps, ack_request=1)
        print("{} [{}] Control action sent.".format(datetime.datetime.now().strftime("%H:%M:%S"), step_label))

    def _observe(self, seconds, step_label):
        print("[{}] Observing for {}s - watch gNB log / RLC UL SDU rate for enforcement.".format(step_label, seconds))
        elapsed = 0
        while self.running and elapsed < seconds:
            time.sleep(5)
            elapsed += 5

    @xAppBase.start_function
    def start(self, e2_node_id, amf_ue_ngap_id, gnb_cu_ue_f1ap_id, plmn, drb_id, max_ul_kbps, observe_seconds):
        self._send_cap(e2_node_id, amf_ue_ngap_id, gnb_cu_ue_f1ap_id, plmn, drb_id, max_ul_kbps,
                       "STEP 1 - apply {}kbps cap".format(max_ul_kbps))
        self._observe(observe_seconds, "STEP 1")

        self._send_cap(e2_node_id, amf_ue_ngap_id, gnb_cu_ue_f1ap_id, plmn, drb_id, UNCAPPED_KBPS,
                       "STEP 2 - remove cap")
        self._observe(observe_seconds, "STEP 2")

        print("Both steps complete, exiting. No further control actions will be sent.")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='UL MFBR PoC: apply a cap, then remove it (Style 1 / Action 1)')
    parser.add_argument("--config", type=str, default='')
    parser.add_argument("--http_server_port", type=int, default=8090)
    parser.add_argument("--rmr_port", type=int, default=4560)
    parser.add_argument("--e2_node_id", type=str, default='gnb_001_001_00019b')
    parser.add_argument("--ran_func_id", type=int, default=3)
    parser.add_argument("--plmn", type=str, default='00101')
    parser.add_argument("--amf_ue_ngap_id", type=int, required=True)
    parser.add_argument("--gnb_cu_ue_f1ap_id", type=int, default=0)
    parser.add_argument("--drb_id", type=int, default=1)
    parser.add_argument("--max_ul_kbps", type=int, default=2000, help="UL cap to apply in Step 1 (kbps)")
    parser.add_argument("--observe_seconds", type=int, default=60,
                        help="Seconds to wait/observe after each of the two control actions")

    args = parser.parse_args()

    myXapp = UlMfbrTestXapp(args.config, args.http_server_port, args.rmr_port)
    myXapp.e2sm_rc.set_ran_func_id(args.ran_func_id)

    signal.signal(signal.SIGQUIT, myXapp.signal_handler)
    signal.signal(signal.SIGTERM, myXapp.signal_handler)
    signal.signal(signal.SIGINT, myXapp.signal_handler)

    myXapp.start(args.e2_node_id, args.amf_ue_ngap_id, args.gnb_cu_ue_f1ap_id, args.plmn, args.drb_id,
                 args.max_ul_kbps, args.observe_seconds)
