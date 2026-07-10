#!/usr/bin/env python3

from __future__ import annotations

import argparse
import datetime
import json
import signal
import sys
import time
import uuid
from pathlib import Path

# Make sure Python can import ./lib/xAppBase.py
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from lib.xAppBase import xAppBase


# ------------------------------------------------------------
# Fixed values for your setup
# ------------------------------------------------------------

E2_NODE_ID = "gnbd_001_001_00019b_0"
UE_ID = 0
RAN_FUNC_ID = 3

MIN_PRB_RATIO = 0
DEDICATED_PRB_RATIO = 100
ACK_REQUEST = 1

# Experiment values
RECOVERY_PRB = 100
DEGRADATION_LEVELS = [10, 30, 50]

DEGRADATION_DURATION_SEC = 180.0
STABILIZATION_DURATION_SEC = 180.0


class MyXapp(xAppBase):
    def __init__(self, config: str, http_server_port: int, rmr_port: int, log_path: str):
        super(MyXapp, self).__init__(config, http_server_port, rmr_port)

        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def utc_now(self) -> str:
        return (
            datetime.datetime.now(datetime.timezone.utc)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z")
        )

    def write_log(self, record: dict) -> None:
        with self.log_path.open("a", buffering=1, encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    def interruptible_sleep(self, seconds: float) -> None:
        end_time = time.time() + seconds

        while self.running and time.time() < end_time:
            remaining = end_time - time.time()
            time.sleep(min(remaining, 0.1))

    def send_prb_action(
        self,
        action_type: str,
        test_case: int,
        prb_before: int,
        prb_after: int,
    ) -> None:
        """
        Send one PRB control action.

        For this experiment:
          min_prb_ratio = 0
          max_prb_ratio = prb_after
        """

        event_id = str(uuid.uuid4())
        action_time_utc = self.utc_now()
        t0_ns = time.monotonic_ns()

        self.write_log({
            "event": "prb_action_before_send",
            "event_id": event_id,
            "action_type": action_type,
            "test_case": test_case,
            "action_time_utc": action_time_utc,

            "e2_node_id": E2_NODE_ID,
            "ue_id": UE_ID,
            "ran_func_id": RAN_FUNC_ID,

            "prb_before": prb_before,
            "prb_after": prb_after,
            "prb_delta": prb_after - prb_before,

            "min_prb_ratio": MIN_PRB_RATIO,
            "max_prb_ratio": prb_after,
            "dedicated_prb_ratio": DEDICATED_PRB_RATIO,
            "ack_request": ACK_REQUEST,
        })

        print(
            f"[{action_time_utc}] Test case {test_case} | {action_type}: "
            f"PRB max {prb_before}% -> {prb_after}%"
        )

        try:
            result = self.e2sm_rc.control_slice_level_prb_quota(
                E2_NODE_ID,
                UE_ID,
                MIN_PRB_RATIO,
                prb_after,
                dedicated_prb_ratio=DEDICATED_PRB_RATIO,
                ack_request=ACK_REQUEST,
            )

            t1_ns = time.monotonic_ns()
            after_send_time_utc = self.utc_now()
            send_duration_ms = (t1_ns - t0_ns) / 1e6

            self.write_log({
                "event": "prb_action_after_send",
                "event_id": event_id,
                "action_type": action_type,
                "test_case": test_case,
                "action_time_utc": action_time_utc,
                "after_send_time_utc": after_send_time_utc,
                "send_duration_ms": send_duration_ms,

                "e2_node_id": E2_NODE_ID,
                "ue_id": UE_ID,
                "ran_func_id": RAN_FUNC_ID,

                "prb_before": prb_before,
                "prb_after": prb_after,
                "prb_delta": prb_after - prb_before,

                "min_prb_ratio": MIN_PRB_RATIO,
                "max_prb_ratio": prb_after,
                "dedicated_prb_ratio": DEDICATED_PRB_RATIO,
                "ack_request": ACK_REQUEST,

                "result": str(result),
            })

            print(
                f"[{after_send_time_utc}] Action returned. "
                f"send_duration_ms={send_duration_ms:.3f}"
            )

        except Exception as e:
            error_time_utc = self.utc_now()

            self.write_log({
                "event": "prb_action_error",
                "event_id": event_id,
                "action_type": action_type,
                "test_case": test_case,
                "action_time_utc": action_time_utc,
                "error_time_utc": error_time_utc,

                "e2_node_id": E2_NODE_ID,
                "ue_id": UE_ID,
                "ran_func_id": RAN_FUNC_ID,

                "prb_before": prb_before,
                "prb_after": prb_after,
                "error": str(e),
            })

            print(f"[{error_time_utc}] Error during {action_type}: {e}")

    @xAppBase.start_function
    def start(self):
        experiment_id = str(uuid.uuid4())
        experiment_start_utc = self.utc_now()

        self.write_log({
            "event": "experiment_start",
            "experiment_id": experiment_id,
            "experiment_start_utc": experiment_start_utc,

            "e2_node_id": E2_NODE_ID,
            "ue_id": UE_ID,
            "ran_func_id": RAN_FUNC_ID,

            "degradation_levels": DEGRADATION_LEVELS,
            "recovery_prb": RECOVERY_PRB,
            "degradation_duration_sec": DEGRADATION_DURATION_SEC,
            "stabilization_duration_sec": STABILIZATION_DURATION_SEC,
        })

        print("\n========== Recovery Time PRB Experiment ==========")
        print("Experiment ID:", experiment_id)
        print("E2 node ID:", E2_NODE_ID)
        print("UE ID:", UE_ID)
        print("RAN function ID:", RAN_FUNC_ID)
        print("Degradation levels:", DEGRADATION_LEVELS)
        print("Recovery PRB:", RECOVERY_PRB)
        print("Degradation duration:", DEGRADATION_DURATION_SEC, "seconds")
        print("Stabilization duration:", STABILIZATION_DURATION_SEC, "seconds")
        print("Log file:", self.log_path)
        print("=================================================\n")

        current_prb = RECOVERY_PRB

        for test_case, degradation_prb in enumerate(DEGRADATION_LEVELS, start=1):
            if not self.running:
                break

            # 1. Degrade the network: 100 -> 10, 30, or 50
            self.send_prb_action(
                action_type="degradation",
                test_case=test_case,
                prb_before=current_prb,
                prb_after=degradation_prb,
            )

            current_prb = degradation_prb

            # 2. Stay degraded for 4 seconds
            print(
                f"Test case {test_case}: staying at degraded PRB max "
                f"{degradation_prb}% for {DEGRADATION_DURATION_SEC} seconds..."
            )
            self.interruptible_sleep(DEGRADATION_DURATION_SEC)

            if not self.running:
                break

            # 3. Corrective action: 10/30/50 -> 100
            #    This timestamp is the end of degradation and start of recovery.
            self.send_prb_action(
                action_type="recovery",
                test_case=test_case,
                prb_before=current_prb,
                prb_after=RECOVERY_PRB,
            )

            current_prb = RECOVERY_PRB

            # 4. Wait 3 seconds at 100 before the next degradation
            print(
                f"Test case {test_case}: waiting {STABILIZATION_DURATION_SEC} "
                f"seconds at PRB max {RECOVERY_PRB}% for stabilization..."
            )
            self.interruptible_sleep(STABILIZATION_DURATION_SEC)

        experiment_end_utc = self.utc_now()

        self.write_log({
            "event": "experiment_end",
            "experiment_id": experiment_id,
            "experiment_end_utc": experiment_end_utc,
            "final_prb_assumption": current_prb,
        })

        print("\n========== Experiment finished ==========")
        print("End time:", experiment_end_utc)
        print("Final PRB max assumption:", current_prb)
        print("Log file:", self.log_path)
        print("=========================================\n")

        self.running = False


if __name__ == "__main__":

    parser = argparse.ArgumentParser(
        description="Recovery-time PRB control xApp"
    )

    parser.add_argument("--config", type=str, default="", help="xApp config file path")
    parser.add_argument("--http_server_port", type=int, default=8090)
    parser.add_argument("--rmr_port", type=int, default=4560)
    parser.add_argument("--log_path", type=str, default="recovery_prb_events.jsonl")

    args = parser.parse_args()

    myXapp = MyXapp(
        config=args.config,
        http_server_port=args.http_server_port,
        rmr_port=args.rmr_port,
        log_path=args.log_path,
    )

    myXapp.e2sm_rc.set_ran_func_id(RAN_FUNC_ID)

    signal.signal(signal.SIGQUIT, myXapp.signal_handler)
    signal.signal(signal.SIGTERM, myXapp.signal_handler)
    signal.signal(signal.SIGINT, myXapp.signal_handler)

    myXapp.start()
