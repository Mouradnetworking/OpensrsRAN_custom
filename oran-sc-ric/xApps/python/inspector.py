#!/usr/bin/env python3

import argparse
import signal
import inspect
import datetime
from lib.xAppBase import xAppBase


class CapabilityProbeXapp(xAppBase):
    def __init__(self, config, http_server_port, rmr_port):
        super(CapabilityProbeXapp, self).__init__(config, http_server_port, rmr_port)

    def _safe_getattr(self, obj, name):
        try:
            return getattr(obj, name)
        except Exception as e:
            return f"<error reading attribute: {e}>"

    def _format_doc(self, doc):
        if not doc:
            return "No docstring available."
        doc = doc.strip()
        if len(doc) > 800:
            return doc[:800] + "\n... [docstring truncated]"
        return doc

    def inspect_helper(self, helper_name, helper_obj):
        print("\n" + "=" * 80)
        print(f"Inspecting helper: {helper_name}")
        print("=" * 80)

        if helper_obj is None:
            print(f"{helper_name} is None")
            return

        # Raw dir() output
        print(f"\ndir({helper_name}):")
        try:
            names = dir(helper_obj)
            for name in names:
                print(f"  {name}")
        except Exception as e:
            print(f"Failed to run dir({helper_name}): {e}")
            return

        # Public methods
        public_names = [n for n in dir(helper_obj) if not n.startswith("_")]
        control_methods = []
        other_methods = []

        for name in public_names:
            attr = self._safe_getattr(helper_obj, name)
            if callable(attr):
                if name.startswith("control_"):
                    control_methods.append(name)
                else:
                    other_methods.append(name)

        print("\n" + "-" * 80)
        print(f"Public callable methods in {helper_name}:")
        print("-" * 80)

        if not public_names:
            print("No public names found.")
        else:
            for name in sorted(public_names):
                attr = self._safe_getattr(helper_obj, name)
                if callable(attr):
                    print(f"  [METHOD] {name}")
                else:
                    print(f"  [ATTR]   {name} = {attr}")

        print("\n" + "-" * 80)
        print(f"Detected control methods in {helper_name}:")
        print("-" * 80)

        if not control_methods:
            print("  No methods starting with 'control_' found.")
        else:
            for method_name in sorted(control_methods):
                method = self._safe_getattr(helper_obj, method_name)

                print(f"\n  Control method: {method_name}")

                try:
                    sig = inspect.signature(method)
                    print(f"    Signature: {method_name}{sig}")
                except Exception as e:
                    print(f"    Signature: <unable to inspect: {e}>")

                try:
                    doc = inspect.getdoc(method)
                    print("    Docstring:")
                    for line in self._format_doc(doc).splitlines():
                        print(f"      {line}")
                except Exception as e:
                    print(f"    Docstring: <unable to inspect: {e}>")

        print("\n" + "-" * 80)
        print(f"Other public callable methods in {helper_name}:")
        print("-" * 80)

        if not other_methods:
            print("  None")
        else:
            for method_name in sorted(other_methods):
                method = self._safe_getattr(helper_obj, method_name)
                print(f"\n  Method: {method_name}")
                try:
                    sig = inspect.signature(method)
                    print(f"    Signature: {method_name}{sig}")
                except Exception as e:
                    print(f"    Signature: <unable to inspect: {e}>")

    def summarize_capabilities(self):
        print("\n" + "=" * 80)
        print("SUMMARY")
        print("=" * 80)

        rc_controls = []
        ccc_controls = []

        try:
            rc_controls = sorted([
                name for name in dir(self.e2sm_rc)
                if not name.startswith("_")
                and callable(getattr(self.e2sm_rc, name))
                and name.startswith("control_")
            ])
        except Exception as e:
            print(f"Could not inspect e2sm_rc: {e}")

        try:
            ccc_controls = sorted([
                name for name in dir(self.e2sm_ccc)
                if not name.startswith("_")
                and callable(getattr(self.e2sm_ccc, name))
                and name.startswith("control_")
            ])
        except Exception as e:
            print(f"Could not inspect e2sm_ccc: {e}")

        print("\nE2SM-RC control methods found:")
        if rc_controls:
            for name in rc_controls:
                print(f"  - {name}")
        else:
            print("  None found")

        print("\nE2SM-CCC control methods found:")
        if ccc_controls:
            for name in ccc_controls:
                print(f"  - {name}")
        else:
            print("  None found")

        print("\nInterpretation:")
        if "control_slice_level_prb_quota" in rc_controls and len(rc_controls) == 1:
            print("  RC appears to expose only PRB quota control in this SDK build.")
        elif "control_slice_level_prb_quota" in rc_controls and len(rc_controls) > 1:
            print("  RC exposes PRB quota control AND other control methods.")
            print("  So you can likely control more than PRB, depending on gNB support.")
        elif rc_controls:
            print("  RC exposes control methods, but PRB quota is not the only visible one.")
        else:
            print("  No RC control methods were detected by introspection.")

        if "control_handover" in rc_controls:
            print("  Handover control is available through E2SM-RC.")
        if ccc_controls:
            print("  CCC also exposes control methods, so control is not limited to RC only.")

    @xAppBase.start_function
    def start(self, e2_node_id):
        now = datetime.datetime.now().strftime("%H:%M:%S")
        print(f"{now} Starting capability probe for E2 node: {e2_node_id}")

        # Inspect RC helper
        try:
            self.inspect_helper("e2sm_rc", self.e2sm_rc)
        except Exception as e:
            print(f"Error while inspecting e2sm_rc: {e}")

        # Inspect CCC helper
        try:
            self.inspect_helper("e2sm_ccc", self.e2sm_ccc)
        except Exception as e:
            print(f"Error while inspecting e2sm_ccc: {e}")

        # Final summary
        try:
            self.summarize_capabilities()
        except Exception as e:
            print(f"Error while building summary: {e}")

        self.running = False
        return


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Capability probe xApp for RC/CCC controls')

    parser.add_argument("--config", type=str, default='', help="xApp config file path")
    parser.add_argument("--http_server_port", type=int, default=8090, help="HTTP server listen port")
    parser.add_argument("--rmr_port", type=int, default=4560, help="RMR port")

    parser.add_argument("--e2_node_id", type=str, default='gnbd_001_001_00019b_0', help="E2 Node ID")
    parser.add_argument("--rc_ran_func_id", type=int, default=3, help="E2SM RC RAN function ID")
    parser.add_argument("--ccc_ran_func_id", type=int, default=4, help="E2SM CCC RAN function ID")

    args = parser.parse_args()

    myXapp = CapabilityProbeXapp(args.config, args.http_server_port, args.rmr_port)

    # Set both RAN function IDs if both helpers exist in your SDK
    try:
        myXapp.e2sm_rc.set_ran_func_id(args.rc_ran_func_id)
    except Exception as e:
        print(f"Warning: failed to set RC RAN function ID: {e}")

    try:
        myXapp.e2sm_ccc.set_ran_func_id(args.ccc_ran_func_id)
    except Exception as e:
        print(f"Warning: failed to set CCC RAN function ID: {e}")

    # Connect exit signals
    signal.signal(signal.SIGQUIT, myXapp.signal_handler)
    signal.signal(signal.SIGTERM, myXapp.signal_handler)
    signal.signal(signal.SIGINT, myXapp.signal_handler)

    # Start probe
    myXapp.start(args.e2_node_id)
