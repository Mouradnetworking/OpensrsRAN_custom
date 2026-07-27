#!/usr/bin/env python3
#
# Interactive PRB-quota PoC (E2SM-RC Style 2 / Action 6, "Slice Level PRB Quota"): apply a
# max PRB ratio, observe, then prompt for what to do next - exit (removing the quota first)
# or apply a different max PRB ratio and observe again. Mirrors ul_mfbr_test_xapp.py's
# apply-observe-remove-observe methodology so the two mechanisms' results are directly
# comparable (same dwell time, same before/during/after structure).
#
# min_prb_ratio/max_prb_ratio are PERCENTAGES of the cell's total PRBs (0-100), not an
# absolute PRB count - see e2sm_rc_module.py's send_control_request_style_2_action_6(),
# which clamps both to [0, 100]. To target an approximate PRB count, convert via the cell's
# configured bandwidth (e.g. 20 MHz / 15 kHz SCS = 106 PRBs, so ~10 PRBs is roughly a 10%
# ratio for that config - recompute for a different channel_bandwidth_MHz/common_scs).
#
# NOTE: requires an interactive terminal to reach the prompt, e.g.:
#   docker exec -it python_xapp_runner python3 prb_quota_test_xapp.py ...
# A plain `docker exec` without `-it` has no connected stdin, and the prompt will fail.

import os
import sys
import time
import datetime
import argparse
import signal
from lib.xAppBase import xAppBase

UNCAPPED_RATIO = 100  # max_prb_ratio meaning "no limit" (min stays at whatever was configured, but
                       # min=0/max=100 dedicated=100 is the fully-unrestricted slice-level quota)


class PrbQuotaTestXapp(xAppBase):
    def __init__(self, config, http_server_port, rmr_port):
        super(PrbQuotaTestXapp, self).__init__(config, http_server_port, rmr_port)

    def _send_quota(self, e2_node_id, ue_id, min_prb_ratio, max_prb_ratio, step_label):
        current_time = datetime.datetime.now()
        print("{} [{}] Sending RIC Control Request: min_prb_ratio={} max_prb_ratio={}".format(
            current_time.strftime("%H:%M:%S"), step_label, min_prb_ratio, max_prb_ratio))
        self.e2sm_rc.control_slice_level_prb_quota(e2_node_id, ue_id, min_prb_ratio, max_prb_ratio,
                                                    dedicated_prb_ratio=100, ack_request=1)
        print("{} [{}] Control action sent.".format(datetime.datetime.now().strftime("%H:%M:%S"), step_label))

    def _observe(self, seconds, step_label):
        print("[{}] Observing for {}s - watch gNB log / MAC PRB allocation for enforcement.".format(
            step_label, seconds))
        elapsed = 0
        while self.running and elapsed < seconds:
            time.sleep(5)
            elapsed += 5

    def _prompt_next_action(self):
        while True:
            try:
                choice = input("\n[1] Exit (removes the quota first)  [2] Send another max_prb_ratio (%)\n"
                                "Choice: ").strip()
            except EOFError:
                print("\nNo interactive stdin available (did you forget `docker exec -it`?). Exiting.")
                return None
            if choice == "1":
                return None
            if choice == "2":
                try:
                    ratio_str = input("New max_prb_ratio (0-100): ").strip()
                    return int(ratio_str)
                except (EOFError, ValueError):
                    print("Not a valid integer, try again.")
                    continue
            print("Please enter 1 or 2.")

    @xAppBase.start_function
    def start(self, e2_node_id, ue_id, min_prb_ratio, max_prb_ratio, observe_seconds):
        current_max = max_prb_ratio
        while True:
            self._send_quota(e2_node_id, ue_id, min_prb_ratio, current_max,
                             "APPLY - max_prb_ratio={}".format(current_max))
            self._observe(observe_seconds, "APPLY")

            self._send_quota(e2_node_id, ue_id, 0, UNCAPPED_RATIO,
                             "REMOVE - max_prb_ratio={}".format(current_max))
            self._observe(observe_seconds, "REMOVE")

            next_max = self._prompt_next_action()
            if next_max is None:
                break
            current_max = next_max

        # See ul_mfbr_test_xapp.py's start() for why os._exit() (not the base class's
        # stop()) is used here, and why stdout is flushed explicitly first.
        print("Done. Quota already removed as part of the last cycle - terminating the xApp process now.")
        sys.stdout.flush()
        os._exit(0)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='PRB-quota interactive PoC: apply a max PRB ratio, observe, '
                                                  'then choose to exit (removing the quota) or apply a new ratio '
                                                  '(Style 2 / Action 6)')
    parser.add_argument("--config", type=str, default='')
    parser.add_argument("--http_server_port", type=int, default=8090)
    parser.add_argument("--rmr_port", type=int, default=4560)
    # DU-targeted action (control_slice_level_prb_quota packs a gNB-DU-UEID, unlike the CU-CP-targeted
    # control_drb_qos used by ul_mfbr_test_xapp.py) - default matches the DU E2 node ID convention used by
    # simple_rc_xapp.py/simple_bitrate_cap_xapp.py, not the CU-CP one used by the MFBR test script.
    parser.add_argument("--e2_node_id", type=str, default='gnbd_001_001_00019b_0')
    parser.add_argument("--ran_func_id", type=int, default=3)
    parser.add_argument("--gnb_cu_ue_f1ap_id", type=int, default=0)
    parser.add_argument("--min_prb_ratio", type=int, default=0, help="Min PRB ratio (%%), held fixed across the run")
    parser.add_argument("--max_prb_ratio", type=int, default=10, help="Initial max PRB ratio (%%) to apply")
    parser.add_argument("--observe_seconds", type=int, default=45,
                        help="Seconds to wait/observe after each control action")

    args = parser.parse_args()

    myXapp = PrbQuotaTestXapp(args.config, args.http_server_port, args.rmr_port)
    myXapp.e2sm_rc.set_ran_func_id(args.ran_func_id)

    signal.signal(signal.SIGQUIT, myXapp.signal_handler)
    signal.signal(signal.SIGTERM, myXapp.signal_handler)
    signal.signal(signal.SIGINT, myXapp.signal_handler)

    myXapp.start(args.e2_node_id, args.gnb_cu_ue_f1ap_id, args.min_prb_ratio, args.max_prb_ratio,
                 args.observe_seconds)
