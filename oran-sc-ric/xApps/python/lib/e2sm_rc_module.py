import datetime
from enum import Enum
from .asn1.e2sm_rc_packer import e2sm_rc_packer
from .utils import plmn_string_to_bcd, plmn_to_bytes
from .asn1.nr_cgi_packer import nr_cgi_packer

class e2sm_rc_module(object):
    def __init__(self, parent):
        super(e2sm_rc_module, self).__init__()
        self.parent = parent
        self.ran_func_id = 3;
        self.e2sm_rc_compiler = e2sm_rc_packer()

        # helper variables
        self.requestorID = 0

    def set_ran_func_id(self, ran_func_id):
        self.ran_func_id = ran_func_id

    def get_requestor_id(self):
        self.requestorID += 1
        self.requestorID %= 255
        return self.requestorID

    def _build_ric_control_request(self, control_header, control_msg, ack_request):
        requestorID = [0x00, self.get_requestor_id()]
        ran_func_id = [0x00, self.ran_func_id]
        control_header_len = len(control_header)
        control_mgs_len = len(control_msg)
        ric_control_ack_request = ack_request
        # asn1tools has some issue to generate RIC-Control-Request from asn1 files, therefore we need to build it manually
        total_len = 33 + control_header_len + control_mgs_len
        msg = [0x00, 0x04, 0x00, total_len, 0x00, 0x00, 0x05, 0x00, 0x1d, 0x00, 0x05, 0x00, *requestorID, 0x00, 0x00, 0x00, 0x05,
               0x00, 0x02, *ran_func_id,
               0x00, 0x16, 0x00, control_header_len+1, control_header_len, *control_header,
               0x00, 0x17, 0x00, control_mgs_len+1, control_mgs_len, *control_msg,
               0x00, 0x15, 0x00, 0x01, ric_control_ack_request  << 6]

        payload = bytes(hex_num for hex_num in msg)
        return payload


    def send_control_request_style_3_action_1(self, e2_node_id, amf_ue_ngap_id, gnb_cu_ue_f1ap_id, plmn_string, target_nr_cell_id):
        # NR CGI encoding = (PLMN Identity + NR Cell Identity)
        packed_target_cgi = nr_cgi_packer.pack_nrcgi(plmn_string, target_nr_cell_id)

        PLMN = plmn_string_to_bcd(plmn_string)
        PLMN = plmn_to_bytes(PLMN)

        ue_id = ('gNB-UEID', {
            'amf-UE-NGAP-ID': amf_ue_ngap_id,
            'guami': {
                'pLMNIdentity': PLMN,
                'aMFRegionID': (b'\x00', 8),  # dummy value
                'aMFSetID':(b'\x00\x00', 10),  # dummy value
                'aMFPointer': (b'\x00', 6)  # dummy value
            },
            'gNB-CU-UE-F1AP-ID-List': [{'gNB-CU-UE-F1AP-ID': gnb_cu_ue_f1ap_id}]
        })

        control_header = self.e2sm_rc_compiler.pack_ric_control_header_f1(style_type=3, control_action_id=1, ue_id_tuple=ue_id)
        handover_msg_dict = {
            "ric-controlMessage-formats": (
                "controlMessage-Format1",
                {
                    "ranP-List": [
                        {
                            "ranParameter-ID": 1,
                            "ranParameter-valueType": (
                                "ranP-Choice-Structure",
                                {
                                    "ranParameter-Structure": {
                                        "sequence-of-ranParameters": [
                                            {
                                                "ranParameter-ID": 2,
                                                "ranParameter-valueType": (
                                                    "ranP-Choice-Structure",
                                                    {
                                                        "ranParameter-Structure": {
                                                            "sequence-of-ranParameters": [
                                                                {
                                                                    "ranParameter-ID": 3,
                                                                    "ranParameter-valueType": (
                                                                        "ranP-Choice-Structure",
                                                                        {
                                                                            "ranParameter-Structure": {
                                                                                "sequence-of-ranParameters": [
                                                                                    {
                                                                                        "ranParameter-ID": 4,
                                                                                        "ranParameter-valueType": (
                                                                                            "ranP-Choice-ElementFalse",
                                                                                            {
                                                                                                "ranParameter-value": 
                                                                                                    ("valueOctS", packed_target_cgi)
                                                                                            }
                                                                                        )
                                                                                    }
                                                                                ]
                                                                            }
                                                                        }
                                                                    )
                                                                }
                                                            ]
                                                        }
                                                    }
                                                )
                                            }
                                        ]
                                    }
                                }
                            )
                        }
                    ]
                }
            )
        }
        control_msg = self.e2sm_rc_compiler.pack_ric_control_msg(handover_msg_dict)
        payload = self._build_ric_control_request(control_header, control_msg, 1)
        self.parent.rmr_send(e2_node_id, payload, 12040, retries=1)

    def send_control_request_style_1_action_1(self, e2_node_id, amf_ue_ngap_id, gnb_cu_ue_f1ap_id, plmn_string,
                                              drb_id, five_qi=None, max_dl_kbps=None, max_ul_kbps=None, ack_request=1):
        """E2SM-RC Style 1 (Radio Bearer Control), Action 1 (DRB QoS Configuration), per
        O-RAN.WG3.E2SM-RC v03.00 Section 7.6.2.1/8.4.2.1. Unlike the Style 2 actions (which identify the UE by
        its DU-local gNB-CU-UE-F1AP-ID only), this is a CU-side control action, so the UE must be identified by
        the full gNB-UEID tuple (AMF UE NGAP ID + GUAMI + gNB-CU-UE-F1AP-ID), same as control_handover.

        Sets the 5QI and/or Maximum Flow Bit Rate DL/UL (RAN Parameter IDs 2, 13, 16) for every QoS flow
        currently mapped to the given DRB ID (RAN Parameter ID 1, the mandatory key). At least one of
        five_qi/max_dl_kbps/max_ul_kbps must be provided. Any left as None is omitted from the request and
        retains its current value at the gNB (this action supports partial updates, unlike the Style 2 actions).
        max_dl_kbps/max_ul_kbps are given in kbps for consistency with control_ue_bitrate_cap; they are packed
        as the wire-format Bit Rate IE, which is in bps.
        """
        if five_qi is None and max_dl_kbps is None and max_ul_kbps is None:
            print("ERROR: E2SM-RC Control Request - DRB QoS Configuration: at least one of five_qi/max_dl_kbps/max_ul_kbps is required")
            return

        PLMN = plmn_string_to_bcd(plmn_string)
        PLMN = plmn_to_bytes(PLMN)

        ue_id = ('gNB-UEID', {
            'amf-UE-NGAP-ID': amf_ue_ngap_id,
            'guami': {
                'pLMNIdentity': PLMN,
                'aMFRegionID': (b'\x00', 8),  # dummy value
                'aMFSetID':(b'\x00\x00', 10),  # dummy value
                'aMFPointer': (b'\x00', 6)  # dummy value
            },
            'gNB-CU-UE-F1AP-ID-List': [{'gNB-CU-UE-F1AP-ID': gnb_cu_ue_f1ap_id}]
        })

        control_header = self.e2sm_rc_compiler.pack_ric_control_header_f1(style_type=1, control_action_id=1, ue_id_tuple=ue_id)

        ran_p_list = [
            # DRB ID, ELEMENT (mandatory key)
            {'ranParameter-ID': 1, 'ranParameter-valueType': ('ranP-Choice-ElementFalse', {'ranParameter-value': ('valueInt', int(drb_id))})}
        ]
        if five_qi is not None:
            ran_p_list.append({'ranParameter-ID': 2, 'ranParameter-valueType': ('ranP-Choice-ElementFalse', {'ranParameter-value': ('valueInt', int(five_qi))})})
        if max_dl_kbps is not None or max_ul_kbps is not None:
            gbr_params = []
            if max_dl_kbps is not None:
                gbr_params.append({'ranParameter-ID': 13, 'ranParameter-valueType': ('ranP-Choice-ElementFalse', {'ranParameter-value': ('valueInt', int(max_dl_kbps) * 1000)})})
            if max_ul_kbps is not None:
                gbr_params.append({'ranParameter-ID': 16, 'ranParameter-valueType': ('ranP-Choice-ElementFalse', {'ranParameter-value': ('valueInt', int(max_ul_kbps) * 1000)})})
            # >QoS parameters for GBR flows in NG-RAN Bearer, STRUCTURE
            ran_p_list.append({'ranParameter-ID': 12, 'ranParameter-valueType': ('ranP-Choice-Structure', {'ranParameter-Structure': {'sequence-of-ranParameters': gbr_params}})})

        control_msg_dict = {'ric-controlMessage-formats': ('controlMessage-Format1', {'ranP-List': ran_p_list})}

        control_msg = self.e2sm_rc_compiler.pack_ric_control_msg(control_msg_dict)
        payload = self._build_ric_control_request(control_header, control_msg, ack_request)
        self.parent.rmr_send(e2_node_id, payload, 12040, retries=1)

    def send_control_request_style_2_action_6(self, e2_node_id, ue_id, min_prb_ratio, max_prb_ratio, dedicated_prb_ratio, ack_request=1):
        plmn_string = "00101"
        sst = 1
        sd = 1

        # PLMN encoding
        PLMN = plmn_string_to_bcd(plmn_string)
        PLMN = plmn_to_bytes(PLMN)
        # S-NSSAI encoding
        sst = sst.to_bytes(1, byteorder='big')
        sd = sd.to_bytes(3, byteorder='big')

        # PRB ratio limits, i.e., [0-100]
        min_prb_ratio = max(0, min(min_prb_ratio, 100))
        max_prb_ratio = max(0, min(max_prb_ratio, 100))
        dedicated_prb_ratio = max(0, min(dedicated_prb_ratio, 100))

        if (max_prb_ratio < min_prb_ratio):
            print("ERROR: E2SM-RC Control Request - Slice Level PRB Quota: max_prb_ratio ({}) cannot be smaller than min_prb_ratio ({})".format(max_prb_ratio, min_prb_ratio))
            return

        ue_id = ('gNB-DU-UEID', {'gNB-CU-UE-F1AP-ID': ue_id})
        control_header = self.e2sm_rc_compiler.pack_ric_control_header_f1(style_type=2, control_action_id=6, ue_id_tuple=ue_id)

        control_msg_dict = {'ric-controlMessage-formats': ('controlMessage-Format1',
                                {'ranP-List': [
                                    # RRM Policy Ratio List, LIST
                                    {'ranParameter-ID': 1, 'ranParameter-valueType': ('ranP-Choice-List', {'ranParameter-List': {'list-of-ranParameter': [{'sequence-of-ranParameters': [
                                        #>RRM Policy Ratio Group, STRUCTURE
                                        {'ranParameter-ID': 2, 'ranParameter-valueType': ('ranP-Choice-Structure', {'ranParameter-Structure': {'sequence-of-ranParameters': [
                                            #>>RRM Policy, STRUCTURE
                                            {'ranParameter-ID': 3, 'ranParameter-valueType': ('ranP-Choice-Structure', {'ranParameter-Structure': {'sequence-of-ranParameters': [
                                                #Note that ID = 4 is missing in the spec.
                                                #>>RRM Policy Member List, LIST
                                                {'ranParameter-ID': 5, 'ranParameter-valueType': ('ranP-Choice-List', {'ranParameter-List': {'list-of-ranParameter': [{'sequence-of-ranParameters': [
                                                    #>>>>RRM Policy Member, STRUCTURE
                                                    {'ranParameter-ID': 6, 'ranParameter-valueType': ('ranP-Choice-Structure', {'ranParameter-Structure': {'sequence-of-ranParameters': [
                                                        #>>>>>PLMN Identity, ELEMENT
                                                        {'ranParameter-ID': 7, 'ranParameter-valueType': ('ranP-Choice-ElementFalse', {'ranParameter-value': ('valueOctS', PLMN)})},
                                                        #>>>>>S-NSSAI, STRUCTURE
                                                        {'ranParameter-ID': 8, 'ranParameter-valueType': ('ranP-Choice-Structure', {'ranParameter-Structure': {'sequence-of-ranParameters': [
                                                                #>>>>>>SST, ELEMENT
                                                                {'ranParameter-ID': 9, 'ranParameter-valueType': ('ranP-Choice-ElementFalse', {'ranParameter-value': ('valueOctS', sst)})},
                                                                #>>>>>>SD, ELEMENT
                                                                {'ranParameter-ID': 10, 'ranParameter-valueType': ('ranP-Choice-ElementFalse', {'ranParameter-value': ('valueOctS', sd)})}]
                                                            }})}]}})}]}]}})}]}})},
                                            #>>Min PRB Policy Ratio, ELEMENT
                                            {'ranParameter-ID': 11, 'ranParameter-valueType': ('ranP-Choice-ElementFalse', {'ranParameter-value': ('valueInt', min_prb_ratio)})},
                                            #>>Max PRB Policy Ratio, ELEMENT
                                            {'ranParameter-ID': 12, 'ranParameter-valueType': ('ranP-Choice-ElementFalse', {'ranParameter-value': ('valueInt', max_prb_ratio)})},
                                            #>>Dedicated PRB Policy Ratio, ELEMENT
                                            {'ranParameter-ID': 13, 'ranParameter-valueType': ('ranP-Choice-ElementFalse', {'ranParameter-value': ('valueInt', dedicated_prb_ratio)})}
                                        ]}})}
                                    ]}]}})}
                                ]}
                            )}

        control_msg = self.e2sm_rc_compiler.pack_ric_control_msg(control_msg_dict)
        payload = self._build_ric_control_request(control_header, control_msg, ack_request)
        self.parent.rmr_send(e2_node_id, payload, 12040, retries=1)

    def send_control_request_style_2_action_201(self, e2_node_id, ue_id, max_dl_kbps=None, max_ul_kbps=None, ack_request=1):
        """srsRAN-specific extension (not a standard O-RAN E2SM-RC action): caps a UE's sustained DL/UL
        bitrate directly, in kbps. Unlike control_slice_level_prb_quota (Style 2, Action 6), which caps the
        *resource share* percentage, this caps the achieved *rate* regardless of channel conditions.

        At least one of max_dl_kbps / max_ul_kbps must be provided. Passing None for one of them leaves that
        direction's cap unset in this request - note that, like the PRB-quota action, this request fully
        replaces the UE's resource-allocation config, so a DL-only request will not preserve a UL cap set by
        a previous request (send both together if you want both to persist).
        """
        if max_dl_kbps is None and max_ul_kbps is None:
            print("ERROR: E2SM-RC Control Request - UE Bitrate Cap: at least one of max_dl_kbps/max_ul_kbps is required")
            return

        ue_id = ('gNB-DU-UEID', {'gNB-CU-UE-F1AP-ID': ue_id})
        control_header = self.e2sm_rc_compiler.pack_ric_control_header_f1(style_type=2, control_action_id=201, ue_id_tuple=ue_id)

        ran_p_list = []
        if max_dl_kbps is not None:
            ran_p_list.append({'ranParameter-ID': 1, 'ranParameter-valueType': ('ranP-Choice-ElementFalse', {'ranParameter-value': ('valueInt', int(max_dl_kbps))})})
        if max_ul_kbps is not None:
            ran_p_list.append({'ranParameter-ID': 2, 'ranParameter-valueType': ('ranP-Choice-ElementFalse', {'ranParameter-value': ('valueInt', int(max_ul_kbps))})})

        control_msg_dict = {'ric-controlMessage-formats': ('controlMessage-Format1', {'ranP-List': ran_p_list})}

        control_msg = self.e2sm_rc_compiler.pack_ric_control_msg(control_msg_dict)
        payload = self._build_ric_control_request(control_header, control_msg, ack_request)
        self.parent.rmr_send(e2_node_id, payload, 12040, retries=1)

    # Alias with a nice name
    control_slice_level_prb_quota = send_control_request_style_2_action_6
    control_handover = send_control_request_style_3_action_1
    control_ue_bitrate_cap = send_control_request_style_2_action_201
    control_drb_qos = send_control_request_style_1_action_1
