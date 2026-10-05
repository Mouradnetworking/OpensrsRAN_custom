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

#include "scheduler_ue_state_logger.h"
#include "../config/cell_configuration.h"
#include "../ue_context/ue_cell.h"
#include "srsran/scheduler/result/resource_block_group.h"
#include "srsran/scheduler/result/sched_result.h"
#include "srsran/scheduler/result/vrb_alloc.h"
#include "srsran/scheduler/scheduler_dl_buffer_state_indication_handler.h"
#include "srsran/scheduler/scheduler_slot_handler.h"
#include "srsran/srslog/srslog.h"
#include "../ue_scheduling/ue_scheduler.h"
#include <cerrno>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <mutex>
#include <sys/stat.h>
#include <unistd.h>

using namespace srsran;

namespace {

/// \brief srslog sink that best-effort writes formatted buffers to a named pipe (FIFO), without ever blocking.
///
/// Runs on srslog's asynchronous backend thread, never on the caller's thread, so a slow or absent reader can only
/// delay this sink, never the scheduler thread that produced the log entry.
class ue_state_pipe_sink : public srslog::sink
{
public:
  explicit ue_state_pipe_sink(std::string path_) :
    srslog::sink(srslog::create_text_formatter()), path(std::move(path_))
  {
    if (::mkfifo(path.c_str(), 0666) != 0 && errno != EEXIST) {
      fmt::print("scheduler_ue_state_logger: failed to create FIFO \"{}\": {}\n", path, ::strerror(errno));
    }
  }

  ~ue_state_pipe_sink() override
  {
    if (fd >= 0) {
      ::close(fd);
    }
  }

  srslog::detail::error_string write(srslog::detail::memory_buffer buffer) override
  {
    ensure_open();
    if (fd < 0) {
      // No reader attached (yet). Drop this sample; this is best-effort telemetry and must never block.
      return {};
    }

    if (::write(fd, buffer.data(), buffer.size()) < 0) {
      if (errno == EPIPE) {
        ::close(fd);
        fd = -1;
      }
      return {};
    }

    static constexpr char newline = '\n';
    if (::write(fd, &newline, 1) < 0 && errno == EPIPE) {
      ::close(fd);
      fd = -1;
    }
    return {};
  }

  srslog::detail::error_string flush() override { return {}; }

private:
  void ensure_open()
  {
    if (fd >= 0) {
      return;
    }
    fd = ::open(path.c_str(), O_WRONLY | O_NONBLOCK);
  }

  const std::string path;
  int                fd = -1;
};

/// Appends `,"key":value` (or `"key":value` if \c first) to \c buf, and flips \c first to false.
template <typename T>
void append_field(fmt::memory_buffer& buf, bool& first, const char* key, const T& value)
{
  fmt::format_to(std::back_inserter(buf), "{}\"{}\":{}", first ? "" : ",", key, value);
  first = false;
}

} // namespace

scheduler_ue_state_logger::scheduler_ue_state_logger()
{
  const char* pipe_path = std::getenv("SRSRAN_UE_STATE_PIPE");
  if (pipe_path == nullptr) {
    // Feature disabled. log_chan stays null; log_slot_result() becomes a single branch.
    return;
  }

  static const std::string sink_name = "ue_state_pipe_sink";
  static const std::string chan_name = "UE_STATE_PIPE_JSON";

  // Multiple cells each create one scheduler_ue_state_logger; SRSRAN_UE_STATE_PIPE names a single pipe shared by
  // the whole process, so make sure the sink (and its FIFO) are only created once.
  static std::once_flag once;
  std::call_once(once, [&pipe_path]() {
    srslog::install_custom_sink(sink_name, std::make_unique<ue_state_pipe_sink>(std::string(pipe_path)));
  });

  log_chan = &srslog::fetch_log_channel(chan_name, *srslog::find_sink(sink_name), {});
  log_chan->set_enabled(true);
}

void scheduler_ue_state_logger::log_slot_result(slot_point                sl_tx,
                                                const cell_configuration& cell_cfg,
                                                const sched_result&       result,
                                                const ue_cell_scheduler&  ue_sched)
{
  if (log_chan == nullptr) {
    return;
  }

  const int64_t ts_ns =
      std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::system_clock::now().time_since_epoch())
          .count();

  fmt::memory_buffer buf;
  fmt::format_to(std::back_inserter(buf),
                 "{{\"schema\":\"srsran_ue_state_v1\",\"ts_ns\":{},\"sfn\":{},\"slot\":{},\"pci\":{},\"dl_grants\":[",
                 ts_ns,
                 sl_tx.sfn(),
                 sl_tx.slot_index(),
                 cell_cfg.pci);

  bool first_ue = true;
  for (const dl_msg_alloc& grant : result.dl.ue_grants) {
    if (grant.pdsch_cfg.codewords.empty()) {
      continue;
    }

    fmt::format_to(std::back_inserter(buf), "{}{{", first_ue ? "" : ",");
    first_ue = false;
    bool first_field = true;

    // --- Raw grant decision. Always present: this is what sched_result gives us "for free". ---
    append_field(buf, first_field, "rnti", to_value(grant.pdsch_cfg.rnti));
    append_field(buf, first_field, "ue_index", static_cast<unsigned>(grant.context.ue_index));
    append_field(buf, first_field, "harq_id", static_cast<unsigned>(grant.pdsch_cfg.harq_id));
    append_field(buf, first_field, "new_data", grant.pdsch_cfg.codewords[0].new_data);
    append_field(buf, first_field, "nof_retxs", grant.context.nof_retxs);
    append_field(buf, first_field, "mcs", grant.pdsch_cfg.codewords[0].mcs_index.to_uint());
    append_field(buf, first_field, "dl_buffer_occupancy_bytes", grant.context.buffer_occupancy);

    // RB allocation: only report rb_start for a contiguous (type1) grant, since a type0 RBG bitmap has no single
    // meaningful start offset - reporting 0 there would look like real data when it is not.
    if (grant.pdsch_cfg.rbs.is_type1()) {
      append_field(buf, first_field, "rb_alloc_type", "\"contiguous\"");
      append_field(buf, first_field, "rb_start", grant.pdsch_cfg.rbs.type1().start());
      append_field(buf, first_field, "rb_len", grant.pdsch_cfg.rbs.type1().length());
    } else {
      append_field(buf, first_field, "rb_alloc_type", "\"rbg_bitmap\"");
      const unsigned rb_len = convert_rbgs_to_prbs(grant.pdsch_cfg.rbs.type0(),
                                                   {0, cell_cfg.nof_dl_prbs},
                                                   get_nominal_rbg_size(cell_cfg.nof_dl_prbs, true))
                                  .count();
      append_field(buf, first_field, "rb_len", rb_len);
    }

    if (grant.pdsch_cfg.bwp_cfg != nullptr) {
      append_field(buf, first_field, "bwp_start", grant.pdsch_cfg.bwp_cfg->crbs.start());
      append_field(buf, first_field, "bwp_size", grant.pdsch_cfg.bwp_cfg->crbs.length());
    }

    // --- Live UE state enrichment. Every field below is emitted only if srsRAN actually has real data for it. ---
    if (const ue_cell* ue_cc = ue_sched.find_ue_cell(grant.context.ue_index); ue_cc != nullptr) {
      const ue_channel_state_manager& chan_state = ue_cc->channel_state_manager();

      const std::optional<csi_report_data>& csi = chan_state.get_latest_csi_report();
      if (csi.has_value() && csi->valid) {
        if (csi->first_tb_wideband_cqi.has_value()) {
          append_field(buf, first_field, "wideband_cqi", csi->first_tb_wideband_cqi->to_uint());
        }
        if (csi->ri.has_value()) {
          append_field(buf, first_field, "dl_ri", csi->ri->to_uint());
        }
      }

      if (chan_state.has_pusch_snr_report()) {
        append_field(buf, first_field, "pusch_sinr_db", chan_state.get_pusch_snr());
        append_field(buf, first_field, "pusch_avg_sinr_db", chan_state.get_pusch_average_sinr());
      }

      if (chan_state.get_pusch_rsrp().has_value()) {
        append_field(buf, first_field, "pusch_rsrp_dbfs", chan_state.get_pusch_rsrp().value());
      }

      if (std::optional<const dl_harq_process_handle> h = ue_cc->harqs.dl_harq(grant.pdsch_cfg.harq_id);
          h.has_value()) {
        append_field(buf, first_field, "harq_max_retxs", h->max_nof_retxs());
        append_field(buf, first_field, "harq_waiting_ack", h->is_waiting_ack());
        if (h->get_grant_params().slice_id.has_value()) {
          append_field(
              buf, first_field, "ran_slice_id", static_cast<unsigned>(h->get_grant_params().slice_id.value().value()));
        }
      }
    }

    fmt::format_to(std::back_inserter(buf), "}}");
  }

  fmt::format_to(std::back_inserter(buf), "],\"ul_grants\":[");

  bool first_ul_ue = true;
  for (const ul_sched_info& grant : result.ul.puschs) {
    fmt::format_to(std::back_inserter(buf), "{}{{", first_ul_ue ? "" : ",");
    first_ul_ue = false;
    bool first_field = true;

    // --- Raw grant decision. Always present: this is what sched_result gives us "for free". ---
    append_field(buf, first_field, "rnti", to_value(grant.pusch_cfg.rnti));
    append_field(buf, first_field, "ue_index", static_cast<unsigned>(grant.context.ue_index));
    append_field(buf, first_field, "harq_id", static_cast<unsigned>(grant.pusch_cfg.harq_id));
    append_field(buf, first_field, "new_data", grant.pusch_cfg.new_data);
    append_field(buf, first_field, "nof_retxs", grant.context.nof_retxs);
    append_field(buf, first_field, "mcs", grant.pusch_cfg.mcs_index.to_uint());

    // RB allocation: same type1 (contiguous) / type0 (RBG bitmap) distinction as DL grants.
    if (grant.pusch_cfg.rbs.is_type1()) {
      append_field(buf, first_field, "rb_alloc_type", "\"contiguous\"");
      append_field(buf, first_field, "rb_start", grant.pusch_cfg.rbs.type1().start());
      append_field(buf, first_field, "rb_len", grant.pusch_cfg.rbs.type1().length());
    } else {
      append_field(buf, first_field, "rb_alloc_type", "\"rbg_bitmap\"");
      const unsigned rb_len = convert_rbgs_to_prbs(grant.pusch_cfg.rbs.type0(),
                                                   {0, cell_cfg.nof_ul_prbs},
                                                   get_nominal_rbg_size(cell_cfg.nof_ul_prbs, true))
                                  .count();
      append_field(buf, first_field, "rb_len", rb_len);
    }

    if (grant.pusch_cfg.bwp_cfg != nullptr) {
      append_field(buf, first_field, "bwp_start", grant.pusch_cfg.bwp_cfg->crbs.start());
      append_field(buf, first_field, "bwp_size", grant.pusch_cfg.bwp_cfg->crbs.length());
    }

    // --- Live UE state enrichment. Every field below is emitted only if srsRAN actually has real data for it. ---
    if (const ue_cell* ue_cc = ue_sched.find_ue_cell(grant.context.ue_index); ue_cc != nullptr) {
      const ue_channel_state_manager& chan_state = ue_cc->channel_state_manager();

      if (chan_state.has_pusch_snr_report()) {
        append_field(buf, first_field, "pusch_sinr_db", chan_state.get_pusch_snr());
        append_field(buf, first_field, "pusch_avg_sinr_db", chan_state.get_pusch_average_sinr());
      }

      if (chan_state.get_pusch_rsrp().has_value()) {
        append_field(buf, first_field, "pusch_rsrp_dbfs", chan_state.get_pusch_rsrp().value());
      }

      if (std::optional<const ul_harq_process_handle> h = ue_cc->harqs.ul_harq(grant.pusch_cfg.harq_id);
          h.has_value()) {
        append_field(buf, first_field, "harq_max_retxs", h->max_nof_retxs());
        append_field(buf, first_field, "harq_waiting_ack", h->is_waiting_ack());
        if (h->get_grant_params().slice_id.has_value()) {
          append_field(
              buf, first_field, "ran_slice_id", static_cast<unsigned>(h->get_grant_params().slice_id.value().value()));
        }
      }
    }

    fmt::format_to(std::back_inserter(buf), "}}");
  }

  fmt::format_to(std::back_inserter(buf), "]}}");

  (*log_chan)("{}", fmt::to_string(buf));
}
