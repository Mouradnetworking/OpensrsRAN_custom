#!/usr/bin/env python3

"""
Automated PRB control xApp for repeated PRB adaptation experiments.

Goal:
  Send PRB control actions every fixed interval.

Experiment sequence:
  wait 4s
  PRB max: 100 -> 10

  wait 4s
  PRB max: 10 -> 100

  wait 4s
  PRB max: 100 -> 10

  ... repeated N times

Important:
  For PRB attribution, we always send:
    min_prb_ratio = 0
    max_prb_ratio = target value, either 10 or 100

Why:
  In your setup, the value we control is the maximum PRB quota.
  The minimum stays fixed at 0.

Logs:
  The xApp writes JSONL logs to prb_events.jsonl.
  Use action_time_utc from "prb_change_before_send" as the action timestamp
  for retrieving internal metrics from InfluxDB.
"""

from __future__ import annotations

import argparse
import datetime
import json
import signal
import sys
import time
import uuid
from pathlib import Path

# Make sure Python can import ./lib/xAppBase.py when mswim.py is in this repo.
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from lib.xAppBase import xAppBase


# -------------------------------------------------------------------
# Hardcoded values for your single-gNB setup
# -------------------------------------------------------------------

E2_NODE_ID = "gnbd_001_001_00019b_0"
UE_ID = 0
RAN_FUNC_ID = 3

FIXED_MIN_PRB_RATIO = 0
DEDICATED_PRB_RATIO = 100
ACK_REQUEST = 1


class MyXapp(xAppBase):
    def __init__(self, config: str, http_server_port: int, rmr_port: int, log_path: str):
        super(MyXapp, self).__init__(config, http_server_port, rmr_port)

        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

        self.last_event_id = None
        self.last_prb_before = None
        self.last_prb_after = None
        self.last_action_time_utc = None
        self.last_send_duration_ms = None

    def utc_now(self) -> str:
        """
        Return UTC time with microsecond precision.

        Example:
          2026-05-18T10:12:45.788488Z
        """
        return (
            datetime.datetime.now(datetime.timezone.utc)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z")
        )

    def write_log(self, record: dict) -> None:
        """
        Append one JSON record per line.

        JSONL is easy to parse later with Python or pandas.
        """
        with self.log_path.open("a", buffering=1, encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    def sleep_until(self, target_monotonic_ns: int) -> None:
        """
        Sleep until a monotonic-clock target.

        This avoids accumulating timing drift across repeated actions.
        """
        while self.running:
            now_ns = time.monotonic_ns()
            remaining_sec = (target_monotonic_ns - now_ns) / 1e9

            if remaining_sec <= 0:
                return

            time.sleep(min(remaining_sec, 0.1))

    def send_prb_control(
        self,
        prb_before: int,
        prb_after: int,
        action_index: int,
    ) -> None:
        """
        Send one PRB control action.

        In this experiment:
          min_prb_ratio = 0
          max_prb_ratio = prb_after

        Example:
          prb_after = 10  -> min=0, max=10
          prb_after = 100 -> min=0, max=100
        """

        event_id = str(uuid.uuid4())

        min_prb_ratio = FIXED_MIN_PRB_RATIO
        max_prb_ratio = prb_after

        action_time_utc = self.utc_now()
        t0_ns = time.monotonic_ns()

        self.write_log({
            "event": "prb_change_before_send",
            "event_id": event_id,
            "action_index": action_index,
            "action_time_utc": action_time_utc,
            "t_monotonic_ns": t0_ns,

            "e2_node_id": E2_NODE_ID,
            "ue_id": UE_ID,
            "ran_func_id": RAN_FUNC_ID,

            "prb_before": prb_before,
            "prb_after": prb_after,
            "prb_delta": prb_after - prb_before,

            "min_prb_ratio": min_prb_ratio,
            "max_prb_ratio": max_prb_ratio,
            "dedicated_prb_ratio": DEDICATED_PRB_RATIO,
            "ack_request": ACK_REQUEST,
        })

        print(
            f"[{action_time_utc}] Sending action {action_index}: "
            f"PRB max {prb_before}% -> {prb_after}% "
            f"(min={min_prb_ratio}, max={max_prb_ratio}), "
            f"event_id={event_id}"
        )

        try:
            result = self.e2sm_rc.control_slice_level_prb_quota(
                E2_NODE_ID,
                UE_ID,
                min_prb_ratio,
                max_prb_ratio,
                dedicated_prb_ratio=DEDICATED_PRB_RATIO,
                ack_request=ACK_REQUEST,
            )

            t1_ns = time.monotonic_ns()
            after_send_time_utc = self.utc_now()
            send_duration_ms = (t1_ns - t0_ns) / 1e6

            self.write_log({
                "event": "prb_change_after_send",
                "event_id": event_id,
                "action_index": action_index,
                "action_time_utc": action_time_utc,
                "after_send_time_utc": after_send_time_utc,
                "t_monotonic_ns": t1_ns,
                "send_duration_ms": send_duration_ms,

                "e2_node_id": E2_NODE_ID,
                "ue_id": UE_ID,
                "ran_func_id": RAN_FUNC_ID,

                "prb_before": prb_before,
                "prb_after": prb_after,
                "prb_delta": prb_after - prb_before,

                "min_prb_ratio": min_prb_ratio,
                "max_prb_ratio": max_prb_ratio,
                "dedicated_prb_ratio": DEDICATED_PRB_RATIO,
                "ack_request": ACK_REQUEST,

                "result": str(result),
            })

            print(
                f"[{after_send_time_utc}] Action {action_index} returned. "
                f"send_duration_ms={send_duration_ms:.3f}"
            )

            self.last_event_id = event_id
            self.last_prb_before = prb_before
            self.last_prb_after = prb_after
            self.last_action_time_utc = action_time_utc
            self.last_send_duration_ms = send_duration_ms

        except Exception as e:
            error_time_utc = self.utc_now()

            self.write_log({
                "event": "prb_change_error",
                "event_id": event_id,
                "action_index": action_index,
                "action_time_utc": action_time_utc,
                "error_time_utc": error_time_utc,

                "e2_node_id": E2_NODE_ID,
                "ue_id": UE_ID,
                "ran_func_id": RAN_FUNC_ID,

                "prb_before": prb_before,
                "prb_after": prb_after,
                "prb_delta": prb_after - prb_before,

                "min_prb_ratio": min_prb_ratio,
                "max_prb_ratio": max_prb_ratio,

                "error": str(e),
            })

            print(f"[{error_time_utc}] Error during action {action_index}: {e}")

    @xAppBase.start_function
    def start(
        self,
        initial_prb: int,
        low_prb: int,
        high_prb: int,
        num_actions: int,
        interval_sec: float,
        initial_wait_sec: float,
    ):
        """
        Main automated experiment loop.
        """

        experiment_id = str(uuid.uuid4())
        experiment_start_utc = self.utc_now()

        self.write_log({
            "event": "experiment_start",
            "experiment_id": experiment_id,
            "experiment_start_utc": experiment_start_utc,

            "e2_node_id": E2_NODE_ID,
            "ue_id": UE_ID,
            "ran_func_id": RAN_FUNC_ID,

            "initial_prb": initial_prb,
            "low_prb": low_prb,
            "high_prb": high_prb,
            "num_actions": num_actions,
            "interval_sec": interval_sec,
            "initial_wait_sec": initial_wait_sec,

            "fixed_min_prb_ratio": FIXED_MIN_PRB_RATIO,
            "dedicated_prb_ratio": DEDICATED_PRB_RATIO,
            "ack_request": ACK_REQUEST,
        })

        print("\n========== Automated PRB Experiment ==========")
        print("Experiment ID:", experiment_id)
        print("E2 node ID:", E2_NODE_ID)
        print("UE ID:", UE_ID)
        print("RAN function ID:", RAN_FUNC_ID)
        print("Initial PRB max:", initial_prb)
        print("Low PRB max:", low_prb)
        print("High PRB max:", high_prb)
        print("Fixed PRB min:", FIXED_MIN_PRB_RATIO)
        print("Number of actions:", num_actions)
        print("Interval between actions:", interval_sec, "seconds")
        print("Initial wait:", initial_wait_sec, "seconds")
        print("Log file:", self.log_path)
        print("==============================================\n")

        current_prb = initial_prb

        # First action happens after initial_wait_sec.
        next_action_time_ns = time.monotonic_ns() + int(initial_wait_sec * 1e9)

        completed_actions = 0

        for action_index in range(1, num_actions + 1):
            if not self.running:
                break

            print(f"Waiting for action {action_index}...")
            self.sleep_until(next_action_time_ns)

            if not self.running:
                break

            # Alternate PRB max between low and high.
            if current_prb == high_prb:
                next_prb = low_prb
            else:
                next_prb = high_prb

            self.send_prb_control(
                prb_before=current_prb,
                prb_after=next_prb,
                action_index=action_index,
            )

            completed_actions += 1
            current_prb = next_prb

            # Schedule next action relative to the planned time, not sleep completion.
            next_action_time_ns += int(interval_sec * 1e9)

        experiment_end_utc = self.utc_now()

        self.write_log({
            "event": "experiment_end",
            "experiment_id": experiment_id,
            "experiment_end_utc": experiment_end_utc,
            "completed_actions": completed_actions,
            "final_prb_assumption": current_prb,
        })

        print("\n========== Experiment finished ==========")
        print("End time:", experiment_end_utc)
        print("Completed actions:", completed_actions)
        print("Final PRB max assumption:", current_prb)
        print("Log file:", self.log_path)
        print("=========================================\n")

        self.running = False


if __name__ == "__main__":

    parser = argparse.ArgumentParser(
        description="Automated PRB control xApp with fixed min PRB and JSONL logs"
    )

    parser.add_argument("--config", type=str, default="", help="xApp config file path")
    parser.add_argument("--http_server_port", type=int, default=8090)
    parser.add_argument("--rmr_port", type=int, default=4560)

    parser.add_argument("--log_path", type=str, default="prb_events.jsonl")

    parser.add_argument("--initial-prb", type=int, default=100)
    parser.add_argument("--low-prb", type=int, default=10)
    parser.add_argument("--high-prb", type=int, default=100)

    parser.add_argument("--num-actions", type=int, default=10)
    parser.add_argument("--interval-sec", type=float, default=4.0)
    parser.add_argument("--initial-wait-sec", type=float, default=4.0)

    args = parser.parse_args()

    if not (0 <= args.initial_prb <= 100):
        raise ValueError("--initial-prb must be between 0 and 100")

    if not (0 <= args.low_prb <= 100):
        raise ValueError("--low-prb must be between 0 and 100")

    if not (0 <= args.high_prb <= 100):
        raise ValueError("--high-prb must be between 0 and 100")

    if args.low_prb >= args.high_prb:
        raise ValueError("--low-prb must be smaller than --high-prb")

    if args.num_actions <= 0:
        raise ValueError("--num-actions must be positive")

    if args.interval_sec <= 0:
        raise ValueError("--interval-sec must be positive")

    if args.initial_wait_sec < 0:
        raise ValueError("--initial-wait-sec cannot be negative")

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

    myXapp.start(
        args.initial_prb,
        args.low_prb,
        args.high_prb,
        args.num_actions,
        args.interval_sec,
        args.initial_wait_sec,
    )
