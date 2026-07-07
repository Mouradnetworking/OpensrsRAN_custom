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

#include "srsran/ran/slot_point.h"

namespace srslog {
class log_channel;
}

namespace srsran {

class cell_configuration;
class ue_cell_scheduler;
struct sched_result;

/// \brief Experimental per-slot, per-UE RAN-state telemetry exporter.
///
/// This is a native srsRAN telemetry surface, not a reproduction of any external stack's wire format. On every
/// slot, for every DL grant decided by the scheduler, it emits a self-describing JSON line combining:
///  - the raw grant decision (from \c sched_result: RNTI, HARQ id, MCS, RB allocation, retransmission count,
///    post-grant DL buffer occupancy);
///  - live per-UE channel state (wideband CQI, DL RI, PUSCH SINR/RSRP), read via the new read-only
///    \c ue_cell_scheduler::find_ue_cell() accessor;
///  - live HARQ process state (max retransmissions, waiting-ACK status) and the grant's RAN slice id.
///
/// \remark Design principle: a field is present in the JSON only when srsRAN actually has real data for it. There
/// are no fabricated zero/default placeholders - see scheduler_ue_state_logger.cpp for exactly which conditions
/// gate each field, and AGENTIC_RAN_SRSRAN_TELEMETRY_DESIGN.md for the full per-metric table (source, unit,
/// real/derived/unavailable).
///
/// \remark Real-time note: unlike the periodic \c cell_metrics_handler report, this component formats and
/// serializes data synchronously on the scheduler's slot-processing call path (only the final pipe write is
/// deferred to srslog's async backend thread - see .cpp). This is acceptable for a research/observability build
/// but is a deliberate trade-off, not a hard real-time guarantee; see the design doc's "Known limitations".
///
/// \remark Fully opt-in: disabled (a single null check) unless the SRSRAN_UE_STATE_PIPE environment variable is
/// set (to the desired FIFO path) before the process starts.
///
/// \remark Scope: DL grants only in this iteration. The UL-side equivalent (against sched_result.ul.puschs and
/// ul_harq_process_handle) follows the identical pattern and is a documented next step, not implemented here.
class scheduler_ue_state_logger
{
public:
  scheduler_ue_state_logger();

  /// \brief Logs the DL grants and associated live UE state for a given slot.
  /// \param[in] sl_tx Slot for which \c result was computed.
  /// \param[in] cell_cfg Configuration of the cell \c result belongs to (PCI, PRB-grid dimensions).
  /// \param[in] result Scheduler result for this cell and slot.
  /// \param[in] ue_sched Per-cell UE scheduler handle, used to look up live channel-state/HARQ objects for each
  /// grant's UE. Read-only: this component never mutates scheduler state through it.
  void log_slot_result(slot_point                sl_tx,
                       const cell_configuration& cell_cfg,
                       const sched_result&       result,
                       const ue_cell_scheduler&  ue_sched);

private:
  /// Log channel feeding the pipe sink. Null when the feature is disabled.
  srslog::log_channel* log_chan = nullptr;
};

} // namespace srsran
