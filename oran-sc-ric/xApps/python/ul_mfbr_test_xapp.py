#!/usr/bin/env python3
#
# Interactive UL MFBR PoC: apply a UL cap, observe, then prompt for what to do next -
# exit (removing the cap first) or apply a different cap and observe again. DL is left
# uncapped throughout, to isolate UL enforcement specifically.
#
# NOTE: requires an interactive terminal to reach the prompt, e.g.:
#   docker exec -it python_xapp_runner python3 ul_mfbr_test_xapp.py ...
# A plain `docker exec` without `-it` has no connected stdin, and the prompt will fail.

import os
import sys
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

    def _prompt_next_action(self):
        while True:
            try:
                choice = input("\n[1] Exit (removes the cap first)  [2] Send another cap (kbps)\nChoice: ").strip()
            except EOFError:
                print("\nNo interactive stdin available (did you forget `docker exec -it`?). Exiting.")
                return None
            if choice == "1":
                return None
            if choice == "2":
                try:
                    kbps_str = input("New UL cap in kbps: ").strip()
                    return int(kbps_str)
                except (EOFError, ValueError):
                    print("Not a valid integer, try again.")
                    continue
            print("Please enter 1 or 2.")

    @xAppBase.start_function
    def start(self, e2_node_id, amf_ue_ngap_id, gnb_cu_ue_f1ap_id, plmn, drb_id, max_ul_kbps, observe_seconds):
        current_kbps = max_ul_kbps
        while True:
            self._send_cap(e2_node_id, amf_ue_ngap_id, gnb_cu_ue_f1ap_id, plmn, drb_id, current_kbps,
                           "APPLY - {}kbps cap".format(current_kbps))
            self._observe(observe_seconds, "APPLY")

            self._send_cap(e2_node_id, amf_ue_ngap_id, gnb_cu_ue_f1ap_id, plmn, drb_id, UNCAPPED_KBPS,
                           "REMOVE - {}kbps cap".format(current_kbps))
            self._observe(observe_seconds, "REMOVE")

            next_kbps = self._prompt_next_action()
            if next_kbps is None:
                break
            current_kbps = next_kbps

        # start() runs in a background thread spawned by the xAppBase framework, so we
        # cannot call the base class's stop() here (it joins that same thread and would
        # deadlock). os._exit() terminates the whole process immediately regardless of
        # which thread calls it, which is what "exiting" actually needs to mean here.
        print("Done. Cap already removed as part of the last cycle - terminating the xApp process now.")
        # os._exit() skips normal Python cleanup, including stdout's buffer flush. When
        # stdout isn't a real TTY (e.g., piped through `docker exec -i`), this print can
        # otherwise be silently lost, since the process dies before the buffered bytes
        # are ever written out. Flush explicitly first so the message is never lost.
        sys.stdout.flush()
        os._exit(0)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='UL MFBR interactive PoC: apply a cap, observe, then choose to '
                                                  'exit (removing the cap) or apply a new cap (Style 1 / Action 1)')
    parser.add_argument("--config", type=str, default='')
    parser.add_argument("--http_server_port", type=int, default=8090)
    parser.add_argument("--rmr_port", type=int, default=4560)
    parser.add_argument("--e2_node_id", type=str, default='gnb_001_001_00019b')
    parser.add_argument("--ran_func_id", type=int, default=3)
    parser.add_argument("--plmn", type=str, default='00101')
    parser.add_argument("--amf_ue_ngap_id", type=int, required=True)
    parser.add_argument("--gnb_cu_ue_f1ap_id", type=int, default=0)
    parser.add_argument("--drb_id", type=int, default=1)
    parser.add_argument("--max_ul_kbps", type=int, default=2000, help="Initial UL cap to apply (kbps)")
    parser.add_argument("--observe_seconds", type=int, default=60,
                        help="Seconds to wait/observe after each control action")

    args = parser.parse_args()

    myXapp = UlMfbrTestXapp(args.config, args.http_server_port, args.rmr_port)
    myXapp.e2sm_rc.set_ran_func_id(args.ran_func_id)

    signal.signal(signal.SIGQUIT, myXapp.signal_handler)
    signal.signal(signal.SIGTERM, myXapp.signal_handler)
    signal.signal(signal.SIGINT, myXapp.signal_handler)

    myXapp.start(args.e2_node_id, args.amf_ue_ngap_id, args.gnb_cu_ue_f1ap_id, args.plmn, args.drb_id,
                 args.max_ul_kbps, args.observe_seconds)
