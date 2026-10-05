#!/usr/bin/env python3
"""Non-interactive, single-shot PRB quota control action (E2SM-RC Style2/Action6).

Unlike prb_quota_test_xapp.py / time_to_impact_xapp.py, this script makes exactly one control
call and exits - no interactive loop, no input(). It is meant to be invoked by an episode
orchestrator via:

    docker compose exec -T python_xapp_runner python3 run_prb_action.py <args>

Every invocation appends one JSON line to --log_path recording the before/after PRB parameters,
timestamps, and whether a RIC_CONTROL_ACK/FAILURE was actually observed - PRB_before is always
caller-supplied (there is no PRB telemetry field to read it back from), and action_result is only
ever "ack_received"/"failure_received" if a real RMR message of that type arrived; otherwise it is
"sent_no_response_observed", never fabricated as success.

Runs inside the python_xapp_runner container. Only one instance of this script (or any other xApp
here) may run at a time - they all bind the same RMR port 4560 / HTTP port 8090.
"""

import argparse
import datetime
import json
import sys
import time
import uuid
from pathlib import Path

from ricxappframe.xapp_frame import rmr
from lib.xAppBase import xAppBase

RIC_CONTROL_ACK = 12041
RIC_CONTROL_FAILURE = 12042

DEFAULT_LOG_PATH = str(Path(__file__).resolve().parent / "logs" / "action_events.jsonl")


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def wait_for_ack(xapp, hold_seconds):
    """Poll the xApp's own RMR client for a control ack/failure for up to hold_seconds.
    Returns one of: 'ack_received', 'failure_received', 'sent_no_response_observed'."""
    deadline = time.monotonic() + hold_seconds
    while time.monotonic() < deadline:
        try:
            sbuf = rmr.rmr_torcv_msg(xapp.rmr_client, None, 100)
            summary = rmr.message_summary(sbuf)
        except Exception:
            continue
        if summary[rmr.RMR_MS_MSG_STATE] == 0:
            mtype = summary["message type"]
            if mtype == RIC_CONTROL_ACK:
                rmr.rmr_free_msg(sbuf)
                return "ack_received"
            if mtype == RIC_CONTROL_FAILURE:
                rmr.rmr_free_msg(sbuf)
                return "failure_received"
        rmr.rmr_free_msg(sbuf)
    return "sent_no_response_observed"


def append_event(log_path, event):
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(event) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=str, default="")
    parser.add_argument("--http_server_port", type=int, default=8090)
    parser.add_argument("--rmr_port", type=int, default=4560)
    parser.add_argument("--e2_node_id", type=str, default="gnbd_001_001_00019b_0")
    parser.add_argument("--ran_func_id", type=int, default=3)
    parser.add_argument("--gnb_cu_ue_f1ap_id", type=int, default=0)

    parser.add_argument("--min_prb_ratio", type=int, required=True)
    parser.add_argument("--max_prb_ratio", type=int, required=True)
    parser.add_argument("--dedicated_prb_ratio", type=int, default=100)
    parser.add_argument("--prb_before_min", type=int, required=True,
                         help="Caller-supplied last-known min_prb_ratio (no telemetry readback exists)")
    parser.add_argument("--prb_before_max", type=int, required=True)
    parser.add_argument("--prb_before_dedicated", type=int, required=True)

    parser.add_argument("--action_role", choices=["primary", "reset"], default="primary")
    parser.add_argument("--episode_id", type=str, required=True)
    parser.add_argument("--event_id", type=str, default=None)
    parser.add_argument("--ack_request", type=int, default=1)
    parser.add_argument("--post_send_hold_seconds", type=float, default=2.0)
    parser.add_argument("--log_path", type=str, default=DEFAULT_LOG_PATH)

    args = parser.parse_args()

    event = {
        "event_id": args.event_id or str(uuid.uuid4()),
        "action_type": "PRB",
        "action_role": args.action_role,
        "episode_id": args.episode_id,
        "e2_node_id": args.e2_node_id,
        "gnb_cu_ue_f1ap_id": args.gnb_cu_ue_f1ap_id,
        "PRB_before": {
            "min": args.prb_before_min,
            "max": args.prb_before_max,
            "dedicated": args.prb_before_dedicated,
        },
        "PRB_after": {
            "min": args.min_prb_ratio,
            "max": args.max_prb_ratio,
            "dedicated": args.dedicated_prb_ratio,
        },
    }

    try:
        xapp = xAppBase(args.config, args.http_server_port, args.rmr_port)
        xapp.e2sm_rc.set_ran_func_id(args.ran_func_id)

        t_wallclock_before = now_iso()
        t_mono_before = time.monotonic_ns()

        xapp.e2sm_rc.control_slice_level_prb_quota(
            args.e2_node_id,
            args.gnb_cu_ue_f1ap_id,
            args.min_prb_ratio,
            args.max_prb_ratio,
            dedicated_prb_ratio=args.dedicated_prb_ratio,
            ack_request=args.ack_request,
        )

        action_result = wait_for_ack(xapp, args.post_send_hold_seconds)
        t_mono_after = time.monotonic_ns()

        event.update({
            "action_timestamp": t_wallclock_before,
            "t_monotonic_ns_before": t_mono_before,
            "t_monotonic_ns_after": t_mono_after,
            "send_duration_ms": (t_mono_after - t_mono_before) / 1e6,
            "action_result": action_result,
            "error": None,
        })

        try:
            xapp.httpServer.stop()
            rmr.rmr_close(xapp.rmr_client)
        except Exception:
            pass

        append_event(args.log_path, event)
        print(json.dumps(event, indent=2))
        sys.exit(0 if action_result != "failure_received" else 1)

    except Exception as exc:
        event.update({
            "action_timestamp": now_iso(),
            "action_result": "error",
            "error": str(exc),
        })
        append_event(args.log_path, event)
        print(json.dumps(event, indent=2), file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
