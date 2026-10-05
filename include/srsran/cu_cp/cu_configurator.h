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

#pragma once

#include "srsran/cu_cp/mobility_manager_config.h"
#include "srsran/f1ap/f1ap_ue_id_types.h"
#include "srsran/ngap/ngap_types.h"
#include "srsran/ran/cu_types.h"
#include "srsran/ran/nr_cgi.h"
#include "srsran/ran/qos/five_qi.h"
#include "srsran/ran/qos/qos_flow_id.h"
#include "srsran/ran/rb_id.h"
#include <map>
#include <optional>

namespace srsran {

namespace srs_cu_cp {
class du_processor_repository;
class cu_cp_mobility_command_handler;
} // namespace srs_cu_cp

struct cu_handover_control_config {
  nr_cell_global_id_t                                    target_cell_id;
  std::map<pdu_session_id_t, std::vector<qos_flow_id_t>> pdu_sessions_for_ho;
  std::map<drb_id_t, std::vector<qos_flow_id_t>>         drbs_for_ho;
  std::vector<nr_cell_global_id_t>                       secondary_cells_to_be_added;
};

/// \brief RAN parameters for the E2SM-RC Style 1 ("Radio Bearer Control"), Action 1 ("DRB QoS Configuration")
/// control action (O-RAN.WG3.E2SM-RC v03.00, Section 8.4.2.1, RAN Parameter IDs 1, 2, 13, 16). Only the DRB ID
/// (key), 5QI and Maximum Flow Bit Rate DL/UL are supported; the remaining RAN parameters from that action's
/// table (Packet Delay Budget, ARP, GFBR, packet loss rate, QoS monitoring, etc.) are left unimplemented,
/// matching the existing Style 3/Action 1 executor's partial RAN parameter coverage.
struct cu_qos_flow_control_config {
  drb_id_t                 drb_id = drb_id_t::invalid;
  std::optional<five_qi_t> five_qi;
  std::optional<uint64_t>  max_flow_bit_rate_dl; // RAN Parameter ID 13, in bps.
  std::optional<uint64_t>  max_flow_bit_rate_ul; // RAN Parameter ID 16, in bps.
};

class cu_configurator
{
public:
  virtual ~cu_configurator() = default;

  /// Get UE index for a given GNB-CU-UE-F1AP-ID.
  virtual srs_cu_cp::ue_index_t get_ue_index(const srs_cu_cp::amf_ue_id_t& amf_ue_id,
                                             const srs_cu_cp::guami_t&     guami,
                                             const gnb_cu_ue_f1ap_id_t&    gnb_cu_ue_f1ap_id) const = 0;

  /// Get index of the DU that serves the given UE.
  virtual srs_cu_cp::du_index_t get_du_index(const srs_cu_cp::ue_index_t& ue_index) const = 0;

  /// Get DU index for a given NR Cell Global ID.
  virtual srs_cu_cp::du_index_t get_du_index(const nr_cell_global_id_t& nr_cgi) const = 0;

  /// Get PCI for a given NR Cell Global ID.
  virtual pci_t get_pci(const nr_cell_global_id_t& nr_cgi) const = 0;

  /// Trigger UE Handover.
  virtual async_task<srs_cu_cp::cu_cp_intra_cu_handover_response>
  trigger_handover(const srs_cu_cp::du_index_t&                      source_du_index,
                   const srs_cu_cp::cu_cp_intra_cu_handover_request& handover_req) = 0;

  /// \brief Trigger a dynamic DRB QoS (re)configuration for an existing UE, applying it to every QoS flow
  /// currently mapped to the target DRB. Returns true if the request was accepted for processing (fire-and-
  /// forget, mirroring trigger_handover); it does not wait for the F1AP/E1AP/RRC round trip to complete.
  virtual async_task<bool> trigger_qos_flow_modification(const srs_cu_cp::ue_index_t&      ue_index,
                                                          const cu_qos_flow_control_config& cfg) = 0;
};

} // namespace srsran
