import sys, time, signal
sys.path.insert(0, '/opt/xApps')
from lib.xAppBase import xAppBase

class OneShot(xAppBase):
    def __init__(self, config, http_port, rmr_port):
        super(OneShot, self).__init__(config, http_port, rmr_port)

    @xAppBase.start_function
    def start(self, e2_node_id, amf_ue_ngap_id, gnb_cu_ue_f1ap_id, plmn, drb_id, dl_kbps, ul_kbps):
        print(f"Sending DRB QoS Configuration: drb_id={drb_id} dl_kbps={dl_kbps} ul_kbps={ul_kbps}", flush=True)
        self.e2sm_rc.control_drb_qos(e2_node_id, amf_ue_ngap_id, gnb_cu_ue_f1ap_id, plmn, drb_id,
                                     max_dl_kbps=dl_kbps, max_ul_kbps=ul_kbps, ack_request=1)
        time.sleep(2)
        self.running = False

if __name__ == '__main__':
    app = OneShot('', 8097, 4563)
    app.e2sm_rc.set_ran_func_id(3)
    signal.signal(signal.SIGTERM, app.signal_handler)
    signal.signal(signal.SIGINT, app.signal_handler)
    app.start('gnb_001_001_00019b', 14, 0, '00101', 1, 5000, 5000)
