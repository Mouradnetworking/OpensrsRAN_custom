/*
 *
 * Copyright 2021-2026 Software Radio Systems Limited
 *
 * This file is part of srsRAN.
 *
 * srsRAN is free software: you can redistribute it and/or modify
 * it under the terms of the GNU Affero General Public License as
 * published by the Free Software Foundation, either version 3 of
 * the License, or (at your option) any later version.
 *
 * srsRAN is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU Affero General Public License for more details.
 *
 * A copy of the GNU Affero General Public License can be found in
 * the LICENSE file in the top-level directory of this distribution
 * and at http://www.gnu.org/licenses/.
 *
 */

#include "cu_configurator_impl.h"
#include "du_processor/du_processor_repository.h"
#include "up_resource_manager/up_resource_manager_impl.h"
#include <algorithm>

using namespace srsran;
using namespace srs_cu_cp;

ue_index_t cu_configurator_impl::get_ue_index(const amf_ue_id_t&         amf_ue_id,
                                              const guami_t&             guami,
                                              const gnb_cu_ue_f1ap_id_t& gnb_cu_ue_f1ap_id) const
{
  auto* ngap = ngap_db.find_ngap(guami.plmn);
  if (ngap == nullptr) {
    return ue_index_t::invalid;
  }
  return ngap->get_ngap_ue_id_translator().get_ue_index(amf_ue_id);
}

du_index_t cu_configurator_impl::get_du_index(const ue_index_t& ue_index) const
{
  auto* ue = ue_mng.find_du_ue(ue_index);
  return ue->get_du_index();
}

du_index_t cu_configurator_impl::get_du_index(const nr_cell_global_id_t& nr_cgi) const
{
  return du_db.find_du(nr_cgi);
}

pci_t cu_configurator_impl::get_pci(const nr_cell_global_id_t& nr_cgi) const
{
  du_index_t du_index = du_db.find_du(nr_cgi);
  if (du_index == du_index_t::invalid) {
    return INVALID_PCI;
  }
  return du_db.get_du_processor(du_index).get_context()->find_cell(nr_cgi)->pci;
}

async_task<cu_cp_intra_cu_handover_response> cu_configurator_impl::return_handover_response(bool ack)
{
  return launch_async([ack](coro_context<async_task<cu_cp_intra_cu_handover_response>>& ctx) {
    CORO_BEGIN(ctx);
    cu_cp_intra_cu_handover_response response;
    response.success = ack;
    CORO_RETURN(response);
  });
}

async_task<cu_cp_intra_cu_handover_response>
cu_configurator_impl::trigger_handover(const du_index_t&                      source_du_index,
                                       const cu_cp_intra_cu_handover_request& handover_req)
{
  cu_cp_ue* u = ue_mng.find_du_ue(handover_req.source_ue_index);
  if (not u) {
    return return_handover_response(false);
  }

  cu_cp_ue_context& ue_ctxt = u->get_ue_context();
  if (ue_ctxt.reconfiguration_disabled) {
    return return_handover_response(false);
  }

  // Handover is going ahead.
  // Disable new reconfigurations from now on (except for the Handover Command).
  ue_ctxt.reconfiguration_disabled = true;

  auto ho_trigger = ([this, handover_req, source_du_index, response = cu_cp_intra_cu_handover_response{}](
                         coro_context<async_task<void>>& ctx) mutable {
    CORO_BEGIN(ctx);
    CORO_AWAIT_VALUE(
        response,
        mobility_notif.on_intra_cu_handover_required(handover_req, source_du_index, handover_req.target_du_index));
    CORO_RETURN();
  });

  if (not u->get_task_sched().schedule_async_task(launch_async(std::move(ho_trigger)))) {
    return return_handover_response(false);
  }

  // Only confirm that the HO request was accepted, no info about the HO outcome.
  return return_handover_response(true);
}

async_task<bool> cu_configurator_impl::return_qos_flow_modification_response(bool ack)
{
  return launch_async([ack](coro_context<async_task<bool>>& ctx) {
    CORO_BEGIN(ctx);
    CORO_RETURN(ack);
  });
}

async_task<bool> cu_configurator_impl::trigger_qos_flow_modification(const ue_index_t&                 ue_index,
                                                                     const cu_qos_flow_control_config& cfg)
{
  cu_cp_ue* u = ue_mng.find_du_ue(ue_index);
  if (u == nullptr) {
    return return_qos_flow_modification_response(false);
  }

  up_resource_manager&  up_mng = u->get_up_resource_manager();
  std::vector<drb_id_t> drbs   = up_mng.get_drbs();
  if (std::find(drbs.begin(), drbs.end(), cfg.drb_id) == drbs.end()) {
    return return_qos_flow_modification_response(false);
  }

  const up_drb_context& drb_ctxt = up_mng.get_drb_context(cfg.drb_id);
  if (drb_ctxt.qos_flows.empty()) {
    return return_qos_flow_modification_response(false);
  }

  cu_cp_pdu_session_resource_modify_request modify_req;
  modify_req.ue_index = ue_index;

  cu_cp_pdu_session_res_modify_item_mod_req mod_item;
  mod_item.pdu_session_id = drb_ctxt.pdu_session_id;

  // Apply the (partial) update to every QoS flow currently mapped to this DRB, keeping all other QoS
  // parameters as they currently are.
  for (const auto& flow : drb_ctxt.qos_flows) {
    cu_cp_qos_flow_add_or_mod_item flow_item;
    flow_item.qos_flow_id               = flow.first;
    flow_item.qos_flow_level_qos_params = drb_ctxt.qos_params;

    if (cfg.five_qi.has_value()) {
      flow_item.qos_flow_level_qos_params.qos_desc = qos_characteristics(non_dyn_5qi_descriptor{cfg.five_qi.value()});
    }

    if (cfg.max_flow_bit_rate_dl.has_value() or cfg.max_flow_bit_rate_ul.has_value()) {
      gbr_qos_flow_information gbr_info =
          flow_item.qos_flow_level_qos_params.gbr_qos_info.value_or(gbr_qos_flow_information{});
      if (cfg.max_flow_bit_rate_dl.has_value()) {
        gbr_info.max_br_dl = cfg.max_flow_bit_rate_dl.value();
      }
      if (cfg.max_flow_bit_rate_ul.has_value()) {
        gbr_info.max_br_ul = cfg.max_flow_bit_rate_ul.value();
      }
      flow_item.qos_flow_level_qos_params.gbr_qos_info = gbr_info;
    }

    mod_item.transfer.qos_flow_add_or_modify_request_list.insert(flow_item.qos_flow_id, flow_item);
  }

  modify_req.pdu_session_res_modify_items.insert(mod_item.pdu_session_id, mod_item);

  auto qos_trigger = ([this, modify_req](coro_context<async_task<void>>& ctx) mutable {
    CORO_BEGIN(ctx);
    CORO_AWAIT(ngap_handler.handle_new_pdu_session_resource_modify_request(modify_req));
    CORO_RETURN();
  });

  if (not u->get_task_sched().schedule_async_task(launch_async(std::move(qos_trigger)))) {
    return return_qos_flow_modification_response(false);
  }

  // Only confirm that the request was accepted, no info about the modification outcome.
  return return_qos_flow_modification_response(true);
}
