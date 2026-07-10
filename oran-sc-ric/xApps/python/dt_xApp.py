#!/usr/bin/env python3

import time
import datetime
import argparse
import signal
import subprocess
import json

from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

from lib.xAppBase import xAppBase


class DigitalTwinXapp(xAppBase):

    def __init__(self, config, http_server_port, rmr_port,
                 influx_url, influx_token, influx_org, influx_bucket):

        super(DigitalTwinXapp, self).__init__(config, http_server_port, rmr_port)

        self.influx_url = influx_url
        self.influx_token = influx_token
        self.influx_org = influx_org
        self.influx_bucket = influx_bucket

        self.client = InfluxDBClient(
            url=self.influx_url,
            token=self.influx_token,
            org=self.influx_org
        )

        self.query_api = self.client.query_api()
        self.write_api = self.client.write_api(write_options=SYNCHRONOUS)

        self.selected_node = None
        self.selected_ue = None


    # ------------------------------------------------
    # GET UE CONTEXT FROM INFLUXDB
    # ------------------------------------------------

    def get_ue_context(self):

        query = '''
        from(bucket:"srsran")
          |> range(start: -5s)
          |> filter(fn:(r) => r._measurement == "ue")
          |> last()
        '''

        tables = self.query_api.query(query)

        context = {}

        for table in tables:
            for record in table.records:
                context[record.get_field()] = record.get_value()

        return context


    # ------------------------------------------------
    # LOG ACTION DATASET
    # ------------------------------------------------

    def store_action(self, ue_id, min_prb, max_prb, latency, context):

        point = Point("xapp_actions") \
            .tag("ue", ue_id) \
            .field("prb_min", min_prb) \
            .field("prb_max", max_prb) \
            .field("latency_ms", latency * 1000)

        for key, value in context.items():
            if isinstance(value, (int, float)):
                point.field(key, value)

        point.time(datetime.datetime.utcnow(), WritePrecision.NS)

        self.write_api.write(
            bucket=self.influx_bucket,
            record=point
        )


    # ------------------------------------------------
    # WAIT FOR METRIC CHANGE
    # ------------------------------------------------

    def wait_for_effect(self, initial_rate):

        while True:

            context = self.get_ue_context()

            if "dl_brate" in context:

                new_rate = context["dl_brate"]

                if abs(new_rate - initial_rate) > 0.1:
                    return time.time(), context

            time.sleep(0.5)


    # ------------------------------------------------
    # SEND PRB CONTROL
    # ------------------------------------------------

    def apply_prb_control(self, e2_node_id, ue_id, min_prb, max_prb):

        context_before = self.get_ue_context()

        initial_rate = context_before.get("dl_brate", 0)

        t_action = time.time()

        print("Sending PRB control:", min_prb, max_prb)

        self.e2sm_rc.control_slice_level_prb_quota(
            e2_node_id,
            ue_id,
            min_prb,
            max_prb,
            dedicated_prb_ratio=100,
            ack_request=1
        )

        t_effect, context_after = self.wait_for_effect(initial_rate)

        latency = t_effect - t_action

        print("Action latency:", latency)

        self.store_action(
            ue_id,
            min_prb,
            max_prb,
            latency,
            context_before
        )


    # ------------------------------------------------
    # CLI LOOP
    # ------------------------------------------------

    @xAppBase.start_function
    def start(self, e2_node_id, ue_id):

        self.selected_node = e2_node_id
        self.selected_ue = ue_id

        while self.running:

            print("\n------ Digital Twin xApp ------")
            print("1) Show UE context")
            print("2) Apply PRB control")
            print("3) Exit")

            choice = input("Option: ")

            if choice == "1":

                context = self.get_ue_context()

                print(json.dumps(context, indent=2))

            elif choice == "2":

                min_prb = int(input("PRB MIN: "))
                max_prb = int(input("PRB MAX: "))

                self.apply_prb_control(
                    self.selected_node,
                    self.selected_ue,
                    min_prb,
                    max_prb
                )

            elif choice == "3":

                self.running = False

            time.sleep(1)


# ------------------------------------------------
# MAIN
# ------------------------------------------------

if __name__ == '__main__':

    parser = argparse.ArgumentParser(description='Digital Twin xApp')

    parser.add_argument("--config", type=str, default='')
    parser.add_argument("--http_server_port", type=int, default=8090)
    parser.add_argument("--rmr_port", type=int, default=4560)

    parser.add_argument("--e2_node_id", type=str, default='gnbd_001_001_00019b_0')
    parser.add_argument("--ran_func_id", type=int, default=3)
    parser.add_argument("--gnb_cu_ue_f1ap_id", type=int, default=0)

    parser.add_argument("--influx_url", type=str, default="http://localhost:8086")
    parser.add_argument("--influx_token", type=str, default="fake")
    parser.add_argument("--influx_org", type=str, default="srsran")
    parser.add_argument("--influx_bucket", type=str, default="srsran")

    args = parser.parse_args()

    myXapp = DigitalTwinXapp(
        args.config,
        args.http_server_port,
        args.rmr_port,
        args.influx_url,
        args.influx_token,
        args.influx_org,
        args.influx_bucket
    )

    myXapp.e2sm_rc.set_ran_func_id(args.ran_func_id)

    signal.signal(signal.SIGQUIT, myXapp.signal_handler)
    signal.signal(signal.SIGTERM, myXapp.signal_handler)
    signal.signal(signal.SIGINT, myXapp.signal_handler)

    myXapp.start(args.e2_node_id, args.gnb_cu_ue_f1ap_id)
