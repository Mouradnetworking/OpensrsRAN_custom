#!/usr/bin/env python3

import time
import datetime
import argparse
import signal
import json
import uuid
from pathlib import Path

from lib.xAppBase import xAppBase


class MyXapp(xAppBase):
    """
    Automated PRB control xApp.

    It sends alternating PRB control actions at a fixed interval.

    Example sequence with:
      initial_prb = 100
      low_prb = 10
      high_prb = 100
      num_actions = 10
      interval_sec = 4

    Sequence:
      wait 4s -> 100 to 10
      wait 4s -> 10 to 100
      wait 4s -> 100 to 10
      ...
    """

    def __init__(self, config, http_server_port, rmr_port, log_path="prb_events.jsonl"):
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
        UTC timestamp compatible with InfluxDB-style timestamps.

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

        JSONL is easy to parse later with Python, pandas, jq, etc.
        """
        with self.log_path.open("a", buffering=1, encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    def sleep_until(self, target_monotonic_ns: int) -> None:
        """
        Sleep until a precise monotonic clock target.

        This avoids accumulating too much drift across repeated actions.
        """
        while self.running:
            now_ns = time.monotonic_ns()
            remaining_sec = (target_monotonic_ns - now_ns) / 1e9

            if remaining_sec <= 0:
                return

            time.sleep(min(remaining_sec, 0.1))

    def send_prb_control(
        self,
        e2_node_id: str,
        ue_id: int,
        prb_before: int,
        prb_after: int,
        action_index: int,
    ) -> None:
        """
        Send one PRB control action and log before/after timestamps.
        """

        event_id = str(uuid.uuid4())

        min_prb_ratio = prb_after
        max_prb_ratio = prb_after

        action_time_utc = self.utc_now()
        t0_ns = time.monotonic_ns()

        before_record = {
            "event": "prb_change_before_send",
            "event_id": event_id,
            "action_index": action_index,
            "action_time_utc": action_time_utc,
            "t_monotonic_ns": t0_ns,
            "e2_node_id": e2_node_id,
            "ue_id": ue_id,
            "prb_before": prb_before,
            "prb_after": prb_after,
            "prb_delta": prb_after - prb_before,
            "min_prb_ratio": min_prb_ratio,
            "max_prb_ratio": max_prb_ratio,
            "dedicated_prb_ratio": 100,
            "ack_request": 1,
        }

        self.write_log(before_record)

        print(
            f"[{action_time_utc}] Sending action {action_index}: "
            f"{prb_before}% -> {prb_after}% PRB, event_id={event_id}"
        )

        try:
            result = self.e2sm_rc.control_slice_level_prb_quota(
                e2_node_id,
                ue_id,
                min_prb_ratio,
                max_prb_ratio,
                dedicated_prb_ratio=100,
                ack_request=1,
            )

            t1_ns = time.monotonic_ns()
            after_time_utc = self.utc_now()
            send_duration_ms = (t1_ns - t0_ns) / 1e6

            after_record = {
                "event": "prb_change_after_send",
                "event_id": event_id,
                "action_index": action_index,
                "action_time_utc": action_time_utc,
                "after_send_time_utc": after_time_utc,
                "t_monotonic_ns": t1_ns,
                "send_duration_ms": send_duration_ms,
                "e2_node_id": e2_node_id,
                "ue_id": ue_id,
                "prb_before": prb_before,
                "prb_after": prb_after,
                "prb_delta": prb_after - prb_before,
                "min_prb_ratio": min_prb_ratio,
                "max_prb_ratio": max_prb_ratio,
                "result": str(result),
            }

            self.write_log(after_record)

            print(
                f"[{after_time_utc}] Action {action_index} returned. "
                f"send_duration_ms={send_duration_ms:.3f}"
            )

            self.last_event_id = event_id
            self.last_prb_before = prb_before
            self.last_prb_after = prb_after
            self.last_action_time_utc = action_time_utc
            self.last_send_duration_ms = send_duration_ms

        except Exception as e:
            error_time_utc = self.utc_now()

            error_record = {
                "event": "prb_change_error",
                "event_id": event_id,
                "action_index": action_index,
                "action_time_utc": action_time_utc,
                "error_time_utc": error_time_utc,
                "e2_node_id": e2_node_id,
                "ue_id": ue_id,
                "prb_before": prb_before,
                "prb_after": prb_after,
                "prb_delta": prb_after - prb_before,
                "error": str(e),
            }

            self.write_log(error_record)

            print(
                f"[{error_time_utc}] Error during action {action_index}: {e}"
            )

    @xAppBase.start_function
    def start(
        self,
        e2_node_id,
        ue_id,
        initial_prb,
        low_prb,
        high_prb,
        num_actions,
        interval_sec,
        initial_wait_sec,
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
            "e2_node_id": e2_node_id,
            "ue_id": ue_id,
            "initial_prb": initial_prb,
            "low_prb": low_prb,
            "high_prb": high_prb,
            "num_actions": num_actions,
            "interval_sec": interval_sec,
            "initial_wait_sec": initial_wait_sec,
        })

        print("\n========== Automated PRB Experiment ==========")
        print("Experiment ID:", experiment_id)
        print("E2 node ID:", e2_node_id)
        print("UE ID:", ue_id)
        print("Initial PRB assumption:", initial_prb)
        print("Low PRB:", low_prb)
        print("High PRB:", high_prb)
        print("Number of actions:", num_actions)
        print("Interval between actions:", interval_sec, "seconds")
        print("Initial wait:", initial_wait_sec, "seconds")
        print("Log file:", self.log_path)
        print("==============================================\n")

        current_prb = initial_prb

        # First action happens after initial_wait_sec.
        next_action_time_ns = time.monotonic_ns() + int(initial_wait_sec * 1e9)

        for action_index in range(1, num_actions + 1):
            if not self.running:
                break

            print(f"Waiting for action {action_index}...")
            self.sleep_until(next_action_time_ns)

            if not self.running:
                break

            # Alternate between low and high PRB.
            if current_prb == high_prb:
                next_prb = low_prb
            else:
                next_prb = high_prb

            self.send_prb_control(
                e2_node_id=e2_node_id,
                ue_id=ue_id,
                prb_before=current_prb,
                prb_after=next_prb,
                action_index=action_index,
            )

            current_prb = next_prb

            # Schedule next action exactly interval_sec after this scheduled time.
            # This reduces drift compared with simply doing time.sleep(interval_sec).
            next_action_time_ns += int(interval_sec * 1e9)

        experiment_end_utc = self.utc_now()

        self.write_log({
            "event": "experiment_end",
            "experiment_id": experiment_id,
            "experiment_end_utc": experiment_end_utc,
            "completed_actions": action_index if "action_index" in locals() else 0,
            "final_prb_assumption": current_prb,
        })

        print("\n========== Experiment finished ==========")
        print("End time:", experiment_end_utc)
        print("Final PRB assumption:", current_prb)
        print("Log file:", self.log_path)
        print("=========================================\n")

        self.running = False


if __name__ == "__main__":

    parser = argparse.ArgumentParser(
        description="Automated PRB control xApp with JSONL timing logs"
    )

    parser.add_argument("--config", type=str, default="", help="xApp config file path")
    parser.add_argument("--http_server_port", type=int, default=8090, help="HTTP server listen port")
    parser.add_argument("--rmr_port", type=int, default=4560, help="RMR port")

    parser.add_argument("--e2_node_id", type=str, default="gnbd_001_001_00019b_0", help="E2 Node ID")
    parser.add_argument("--ran_func_id", type=int, default=3, help="E2SM RC RAN function ID")
    parser.add_argument("--gnb_cu_ue_f1ap_id", type=int, default=0, help="UE ID")

    parser.add_argument("--log_path", type=str, default="prb_events.jsonl", help="Path to JSONL log file")

    parser.add_argument("--initial-prb", type=int, default=100, help="Assumed PRB value before first action")
    parser.add_argument("--low-prb", type=int, default=10, help="Low PRB target")
    parser.add_argument("--high-prb", type=int, default=100, help="High PRB target")

    parser.add_argument("--num-actions", type=int, default=10, help="Total number of PRB actions")
    parser.add_argument("--interval-sec", type=float, default=4.0, help="Seconds between action start times")
    parser.add_argument("--initial-wait-sec", type=float, default=4.0, help="Seconds to wait before first action")

    args = parser.parse_args()

    if not (0 <= args.low_prb <= 100):
        raise ValueError("--low-prb must be between 0 and 100")

    if not (0 <= args.high_prb <= 100):
        raise ValueError("--high-prb must be between 0 and 100")

    if not (0 <= args.initial_prb <= 100):
        raise ValueError("--initial-prb must be between 0 and 100")

    if args.low_prb >= args.high_prb:
        raise ValueError("--low-prb must be smaller than --high-prb")

    if args.num_actions <= 0:
        raise ValueError("--num-actions must be positive")

    if args.interval_sec <= 0:
        raise ValueError("--interval-sec must be positive")

    if args.initial_wait_sec < 0:
        raise ValueError("--initial-wait-sec cannot be negative")

    myXapp = MyXapp(
        args.config,
        args.http_server_port,
        args.rmr_port,
        log_path=args.log_path,
    )

    myXapp.e2sm_rc.set_ran_func_id(args.ran_func_id)

    signal.signal(signal.SIGQUIT, myXapp.signal_handler)
    signal.signal(signal.SIGTERM, myXapp.signal_handler)
    signal.signal(signal.SIGINT, myXapp.signal_handler)

    myXapp.start(
        args.e2_node_id,
        args.gnb_cu_ue_f1ap_id,
        args.initial_prb,
        args.low_prb,
        args.high_prb,
        args.num_actions,
        args.interval_sec,
        args.initial_wait_sec,
    )
