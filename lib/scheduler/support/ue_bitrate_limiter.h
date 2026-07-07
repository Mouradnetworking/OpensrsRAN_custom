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
#include <optional>

namespace srsran {

/// \brief Enforces an optional, externally-configurable cap on a UE's achieved bitrate, in one direction (DL or
/// UL).
///
/// This is a pure scheduler-side, RF-agnostic mechanism: it works identically on the ZMQ RF simulator and on real
/// hardware (SDR + COTS UE), since it only shapes how many new-data bytes the grant-sizing logic is allowed to
/// request per slot - it never touches PHY/RF parameters.
///
/// Implemented as a token bucket: tokens (bytes) refill continuously at the configured cap rate, up to a small
/// burst allowance, and are consumed as grants are actually made. When no cap is configured (the default), every
/// query returns "unrestricted" at negligible cost, so UEs without a cap pay no overhead and see no behavior
/// change.
///
/// \remark Split into a \c const "peek" (\ref get_max_grantable_bytes) and a mutating "commit" (\ref consume) on
/// purpose: the scheduler's grant-sizing helpers (e.g. \c grant_params_selector.cpp) operate on a \c const
/// ue_cell& and must stay side-effect-free, while the actual token deduction only happens once a grant is
/// finalized (in \c ue_cell_grid_allocator.cpp), where the real granted TBS is known and the UE is non-const.
class ue_bitrate_limiter
{
public:
  /// \brief Sets (or clears, via \c std::nullopt) the maximum sustained bitrate for this UE/direction, in kbps.
  /// \remark Resets the token bucket to full on any change (including the first time a cap is set), so
  /// enabling/raising a cap never stalls the UE waiting for the bucket to refill from empty.
  void set_cap(std::optional<unsigned> max_kbps);

  /// \brief Maximum number of new-data bytes that may be granted in \c sl_tx without exceeding the configured cap.
  /// Does not modify state; see \ref consume for that.
  /// \return \c std::numeric_limits<unsigned>::max() if no cap is configured.
  unsigned get_max_grantable_bytes(slot_point sl_tx) const;

  /// \brief Registers that \c bytes_granted bytes of new data were actually scheduled in \c sl_tx, deducting them
  /// from the token bucket (after accounting for refill up to \c sl_tx). No-op if no cap is configured.
  void consume(slot_point sl_tx, unsigned bytes_granted);

  /// \brief Whether a cap is currently configured for this UE/direction.
  bool is_capped() const { return cap_kbps.has_value(); }

private:
  /// Burst allowance, expressed as a duration of sustained-rate traffic the bucket can hold. Small enough to
  /// converge to the target average quickly, large enough to avoid single-slot grant starvation/fragmentation.
  static constexpr unsigned burst_window_ms = 20;

  /// Bytes held by a full bucket for the currently configured cap; 0 if uncapped.
  double max_tokens_bytes() const
  {
    return cap_kbps.has_value() ? static_cast<double>(cap_kbps.value()) * burst_window_ms / 8.0 : 0.0;
  }

  /// \brief Available tokens (bytes), as of \c sl_tx, accounting for refill since \c last_update_slot. Pure
  /// function of the stored state; does not mutate it.
  double tokens_available_at(slot_point sl_tx) const;

  std::optional<unsigned> cap_kbps;
  /// Tokens (bytes) available as of \c last_update_slot.
  double tokens_bytes = 0.0;
  /// Slot at which \c tokens_bytes was last settled (by a call to \ref consume, or \ref set_cap).
  slot_point last_update_slot;
};

} // namespace srsran
