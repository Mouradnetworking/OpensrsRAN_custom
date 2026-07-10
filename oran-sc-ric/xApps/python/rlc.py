#!/usr/bin/env python3

import argparse
import signal
from lib.xAppBase import xAppBase


RLC_METRICS = [
    "DRB.RlcSduDelayDl",
    "DRB.RlcDelayUl",
    "DRB.RlcPacketDropRateDl",
    "DRB.RlcSduTransmittedVolumeDL",
    "DRB.RlcSduTransmittedVolumeUL",
    "DRB.AirIfDelayUl",
]

METRIC_PRESETS = {
    "rlc": RLC_METRICS,
    "rlc+thp": RLC_METRICS + ["DRB.UEThpDl", "DRB.UEThpUl"],
    "rlc+radio": RLC_METRICS + [
        "DRB.UEThpDl",
        "DRB.UEThpUl",
        "RRU.PrbAvailDl",
        "RRU.PrbAvailUl",
        "RRU.PrbTotDl",
        "RRU.PrbTotUl",
    ],
}


def expand_metrics(metric_tokens):
    expanded = []
    for token in metric_tokens:
        key = token.strip()
        if not key:
            continue
        if key in METRIC_PRESETS:
            expanded.extend(METRIC_PRESETS[key])
        else:
            expanded.append(key)

    # deduplicate while preserving order
    seen = set()
    deduped = []
    for m in expanded:
        if m not in seen:
            seen.add(m)
            deduped.append(m)
    return deduped


def pretty_value(value):
    if isinstance(value, list) and len(value) == 1:
        return value[0]
    return value


class MyXapp(xAppBase):
    def __init__(self, config, http_server_port, rmr_port):
        super(MyXapp, self).__init__(config, http_server_port, rmr_port)

    def print_metric_block(self, metric_map, indent=""):
        for metric_name, value in metric_map.items():
            print(f"{indent}Metric: {metric_name}, Value: {pretty_value(value)}")

    def my_subscription_callback(
        self,
        e2_agent_id,
        subscription_id,
        indication_hdr,
        indication_msg,
        kpm_report_style,
        ue_id,
    ):
        if kpm_report_style == 2:
            print(
                f"\nRIC Indication Received from {e2_agent_id} "
                f"for Subscription ID: {subscription_id}, "
                f"KPM Report Style: {kpm_report_style}, UE ID: {ue_id}"
            )
        else:
            print(
                f"\nRIC Indication Received from {e2_agent_id} "
                f"for Subscription ID: {subscription_id}, "
                f"KPM Report Style: {kpm_report_style}"
            )

        indication_hdr = self.e2sm_kpm.extract_hdr_info(indication_hdr)
        meas_data = self.e2sm_kpm.extract_meas_data(indication_msg)

        print("E2SM_KPM RIC Indication Content:")
        print(f"-ColletStartTime: {indication_hdr['colletStartTime']}")
        print("-Measurements Data:")

        granul_period = meas_data.get("granulPeriod", None)
        if granul_period is not None:
            print(f"-granulPeriod: {granul_period}")

        if kpm_report_style in [1, 2]:
            self.print_metric_block(meas_data["measData"], indent="--")
        else:
            for ue_id, ue_meas_data in meas_data["ueMeasData"].items():
                print(f"--UE_id: {ue_id}")

                ue_granul_period = ue_meas_data.get("granulPeriod", None)
                if ue_granul_period is not None:
                    print(f"---granulPeriod: {ue_granul_period}")

                self.print_metric_block(ue_meas_data["measData"], indent="---")

    @xAppBase.start_function
    def start(
        self,
        e2_node_id,
        kpm_report_style,
        ue_ids,
        metric_names,
        report_period,
        granul_period,
    ):
        # default callback
        subscription_callback = (
            lambda agent, sub, hdr, msg:
            self.my_subscription_callback(agent, sub, hdr, msg, kpm_report_style, None)
        )

        if kpm_report_style == 1:
            print(
                f"Subscribe to E2 node ID: {e2_node_id}, "
                f"RAN func: e2sm_kpm, Report Style: {kpm_report_style}, "
                f"metrics: {metric_names}"
            )
            self.e2sm_kpm.subscribe_report_service_style_1(
                e2_node_id,
                report_period,
                metric_names,
                granul_period,
                subscription_callback,
            )

        elif kpm_report_style == 2:
            subscription_callback = (
                lambda agent, sub, hdr, msg:
                self.my_subscription_callback(agent, sub, hdr, msg, kpm_report_style, ue_ids[0])
            )

            print(
                f"Subscribe to E2 node ID: {e2_node_id}, "
                f"RAN func: e2sm_kpm, Report Style: {kpm_report_style}, "
                f"UE_id: {ue_ids[0]}, metrics: {metric_names}"
            )
            self.e2sm_kpm.subscribe_report_service_style_2(
                e2_node_id,
                report_period,
                ue_ids[0],
                metric_names,
                granul_period,
                subscription_callback,
            )

        elif kpm_report_style == 3:
            if len(metric_names) > 1:
                metric_names = [metric_names[0]]
                print(
                    f"INFO: Report Style 3 currently supports only 1 metric here, "
                    f"selected: {metric_names[0]}"
                )

            matching_conds = [
                {
                    "matchingCondChoice": (
                        "testCondInfo",
                        {
                            "testType": ("ul-rSRP", "true"),
                            "testExpr": "lessthan",
                            "testValue": ("valueInt", 1000),
                        },
                    )
                }
            ]

            print(
                f"Subscribe to E2 node ID: {e2_node_id}, "
                f"RAN func: e2sm_kpm, Report Style: {kpm_report_style}, "
                f"metrics: {metric_names}"
            )
            self.e2sm_kpm.subscribe_report_service_style_3(
                e2_node_id,
                report_period,
                matching_conds,
                metric_names,
                granul_period,
                subscription_callback,
            )

        elif kpm_report_style == 4:
            matching_ue_conds = [
                {
                    "testCondInfo": {
                        "testType": ("ul-rSRP", "true"),
                        "testExpr": "lessthan",
                        "testValue": ("valueInt", 1000),
                    }
                }
            ]

            print(
                f"Subscribe to E2 node ID: {e2_node_id}, "
                f"RAN func: e2sm_kpm, Report Style: {kpm_report_style}, "
                f"metrics: {metric_names}"
            )
            self.e2sm_kpm.subscribe_report_service_style_4(
                e2_node_id,
                report_period,
                matching_ue_conds,
                metric_names,
                granul_period,
                subscription_callback,
            )

        elif kpm_report_style == 5:
            if len(ue_ids) < 2:
                dummy_ue_id = ue_ids[0] + 1
                ue_ids.append(dummy_ue_id)
                print(
                    "INFO: Report Style 5 requires at least two UE IDs in this xApp/lib path; "
                    f"added dummy UE ID: {dummy_ue_id}"
                )

            print(
                f"Subscribe to E2 node ID: {e2_node_id}, "
                f"RAN func: e2sm_kpm, Report Style: {kpm_report_style}, "
                f"UE_ids: {ue_ids}, metrics: {metric_names}"
            )
            self.e2sm_kpm.subscribe_report_service_style_5(
                e2_node_id,
                report_period,
                ue_ids,
                metric_names,
                granul_period,
                subscription_callback,
            )

        else:
            print(f"INFO: Report Style {kpm_report_style} is not supported")
            raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="KPM xApp with RLC defaults")
    parser.add_argument("--config", type=str, default="", help="xApp config file path")
    parser.add_argument("--http_server_port", type=int, default=8092, help="HTTP server listen port")
    parser.add_argument("--rmr_port", type=int, default=4562, help="RMR port")
    parser.add_argument("--e2_node_id", type=str, default="gnbd_001_001_00019b_0", help="E2 Node ID")
    parser.add_argument("--ran_func_id", type=int, default=2, help="RAN function ID")
    parser.add_argument("--kpm_report_style", type=int, default=5, help="KPM Report Style ID")
    parser.add_argument("--ue_ids", type=str, default="0", help="UE IDs as comma-separated list")
    parser.add_argument(
        "--metrics",
        type=str,
        default="rlc",
        help=(
            "Metrics as comma-separated string. "
            "You can also use presets: rlc, rlc+thp, rlc+radio"
        ),
    )
    parser.add_argument("--report_period", type=int, default=1000, help="KPM report period in ms")
    parser.add_argument("--granul_period", type=int, default=1000, help="KPM granularity period in ms")

    args = parser.parse_args()

    ue_ids = [int(x.strip()) for x in args.ue_ids.split(",") if x.strip()]
    metric_tokens = [x.strip() for x in args.metrics.split(",")]
    metrics = expand_metrics(metric_tokens)

    print(f"Selected metrics: {metrics}")

    myXapp = MyXapp(args.config, args.http_server_port, args.rmr_port)
    myXapp.e2sm_kpm.set_ran_func_id(args.ran_func_id)

    signal.signal(signal.SIGQUIT, myXapp.signal_handler)
    signal.signal(signal.SIGTERM, myXapp.signal_handler)
    signal.signal(signal.SIGINT, myXapp.signal_handler)

    myXapp.start(
        args.e2_node_id,
        args.kpm_report_style,
        ue_ids,
        metrics,
        args.report_period,
        args.granul_period,
    )
