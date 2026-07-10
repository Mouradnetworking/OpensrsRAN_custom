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

    def __init__(self, config, http_server_port, rmr_port, log_path="prb_events.jsonl"):
        super(MyXapp, self).__init__(config, http_server_port, rmr_port)

        self.last_min_prb = None
        self.last_max_prb = None
        self.last_action_timestamp = None
        self.last_event_id = None
        self.last_send_duration_ms = None

        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def write_log(self, record: dict) -> None:
        """
        Append one JSON record per line.
        This is much easier to parse later than plain print output.
        """
        with self.log_path.open("a", buffering=1, encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    # xApp start function
    @xAppBase.start_function
    def start(self, e2_node_id, ue_id):

        while self.running:

            print("\n========== xApp PRB Control ==========")
            print("1) Set PRB quota")
            print("2) Show last configuration")
            print("3) Exit")
            print("======================================")

            choice = input("Select option: ").strip()

            if choice == "1":
                try:
                    min_prb_ratio = int(input("Enter PRB MIN ratio (0-100): ").strip())
                    max_prb_ratio = int(input("Enter PRB MAX ratio (0-100): ").strip())

                    if not (0 <= min_prb_ratio <= 100 and 0 <= max_prb_ratio <= 100):
                        print("Invalid PRB values. Both must be between 0 and 100.")
                        continue

                    if min_prb_ratio > max_prb_ratio:
                        print("Invalid PRB values. Ensure min_prb_ratio <= max_prb_ratio.")
                        continue

                    event_id = str(uuid.uuid4())

                    # Human-readable timestamp for logs and terminal
                    human_time_before = datetime.datetime.now().isoformat(timespec="microseconds")

                    print(
                        f"[{human_time_before}] About to send RIC Control Request "
                        f"to E2 node ID: {e2_node_id}, UE ID: {ue_id}, "
                        f"PRB_min_ratio: {min_prb_ratio}, PRB_max_ratio: {max_prb_ratio}, "
                        f"event_id: {event_id}"
                    )

                    # IMPORTANT:
                    # Use monotonic_ns for latency measurement.
                    # Put this immediately before the control call.
                    t0_ns = time.monotonic_ns()

                    self.write_log({
                        "event": "prb_change_before_send",
                        "event_id": event_id,
                        "human_time": human_time_before,
                        "t_monotonic_ns": t0_ns,
                        "e2_node_id": e2_node_id,
                        "ue_id": ue_id,
                        "min_prb_ratio": min_prb_ratio,
                        "max_prb_ratio": max_prb_ratio,
                        "dedicated_prb_ratio": 100,
                        "ack_request": 1
                    })

                    result = self.e2sm_rc.control_slice_level_prb_quota(
                        e2_node_id,
                        ue_id,
                        min_prb_ratio,
                        max_prb_ratio,
                        dedicated_prb_ratio=100,
                        ack_request=1
                    )

                    t1_ns = time.monotonic_ns()
                    human_time_after = datetime.datetime.now().isoformat(timespec="microseconds")
                    send_duration_ms = (t1_ns - t0_ns) / 1e6

                    self.write_log({
                        "event": "prb_change_after_send",
                        "event_id": event_id,
                        "human_time": human_time_after,
                        "t_monotonic_ns": t1_ns,
                        "send_duration_ms": send_duration_ms,
                        "e2_node_id": e2_node_id,
                        "ue_id": ue_id,
                        "min_prb_ratio": min_prb_ratio,
                        "max_prb_ratio": max_prb_ratio,
                        "result": str(result)
                    })

                    print(
                        f"[{human_time_after}] Control request returned. "
                        f"event_id: {event_id}, send_duration_ms: {send_duration_ms:.3f}"
                    )

                    self.last_min_prb = min_prb_ratio
                    self.last_max_prb = max_prb_ratio
                    self.last_action_timestamp = human_time_before
                    self.last_event_id = event_id
                    self.last_send_duration_ms = send_duration_ms

                except ValueError:
                    print("Invalid input. Please enter numeric values.")
                except Exception as e:
                    error_time = datetime.datetime.now().isoformat(timespec="microseconds")
                    print(f"[{error_time}] Error while sending control request: {e}")

                    self.write_log({
                        "event": "prb_change_error",
                        "human_time": error_time,
                        "e2_node_id": e2_node_id,
                        "ue_id": ue_id,
                        "error": str(e)
                    })

            elif choice == "2":
                if self.last_min_prb is None:
                    print("No configuration sent yet.")
                else:
                    print("Last PRB configuration:")
                    print("Event ID =", self.last_event_id)
                    print("PRB MIN =", self.last_min_prb)
                    print("PRB MAX =", self.last_max_prb)
                    print("Timestamp =", self.last_action_timestamp)
                    print("Send duration (ms) =", f"{self.last_send_duration_ms:.3f}")

            elif choice == "3":
                print("Stopping xApp...")
                self.running = False
                break

            else:
                print("Invalid option. Try again.")

            time.sleep(1)


if __name__ == '__main__':

    parser = argparse.ArgumentParser(description='PRB control xApp with timing instrumentation')

    parser.add_argument("--config", type=str, default='', help="xApp config file path")
    parser.add_argument("--http_server_port", type=int, default=8090, help="HTTP server listen port")
    parser.add_argument("--rmr_port", type=int, default=4560, help="RMR port")

    parser.add_argument("--e2_node_id", type=str, default='gnbd_001_001_00019b_0', help="E2 Node ID")
    parser.add_argument("--ran_func_id", type=int, default=3, help="E2SM RC RAN function ID")
    parser.add_argument("--gnb_cu_ue_f1ap_id", type=int, default=0, help="UE ID")
    parser.add_argument("--log_path", type=str, default="prb_events.jsonl", help="Path to JSONL log file")

    args = parser.parse_args()

    config = args.config
    e2_node_id = args.e2_node_id
    ran_func_id = args.ran_func_id
    ue_id = args.gnb_cu_ue_f1ap_id

    # Create MyXapp
    myXapp = MyXapp(config, args.http_server_port, args.rmr_port, log_path=args.log_path)

    # Set RAN Function ID
    myXapp.e2sm_rc.set_ran_func_id(ran_func_id)

    # Connect exit signals
    signal.signal(signal.SIGQUIT, myXapp.signal_handler)
    signal.signal(signal.SIGTERM, myXapp.signal_handler)
    signal.signal(signal.SIGINT, myXapp.signal_handler)

    # Start xApp
    myXapp.start(e2_node_id, ue_id)
