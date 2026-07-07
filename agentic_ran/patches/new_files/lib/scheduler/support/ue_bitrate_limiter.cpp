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

#include "ue_bitrate_limiter.h"
#include <algorithm>
#include <cmath>
#include <limits>

using namespace srsran;

void ue_bitrate_limiter::set_cap(std::optional<unsigned> max_kbps)
{
  if (max_kbps == cap_kbps) {
    // No change.
    return;
  }
  cap_kbps = max_kbps;
  // Start the bucket full, so that (re)configuring a cap never causes an immediate stall while it "refills".
  tokens_bytes     = max_tokens_bytes();
  last_update_slot = {};
}

double ue_bitrate_limiter::tokens_available_at(slot_point sl_tx) const
{
  if (not cap_kbps.has_value()) {
    return 0.0;
  }
  if (not last_update_slot.valid() or sl_tx <= last_update_slot) {
    return std::min(tokens_bytes, max_tokens_bytes());
  }
  const slot_difference elapsed_slots = sl_tx - last_update_slot;
  const double           elapsed_ms   = static_cast<double>(elapsed_slots) / sl_tx.nof_slots_per_subframe();
  // kbps * ms / 8 = bytes refilled over the elapsed time.
  const double refilled = tokens_bytes + (cap_kbps.value() * elapsed_ms / 8.0);
  return std::min(refilled, max_tokens_bytes());
}

unsigned ue_bitrate_limiter::get_max_grantable_bytes(slot_point sl_tx) const
{
  if (not cap_kbps.has_value()) {
    return std::numeric_limits<unsigned>::max();
  }
  return static_cast<unsigned>(std::max(0.0, std::floor(tokens_available_at(sl_tx))));
}

void ue_bitrate_limiter::consume(slot_point sl_tx, unsigned bytes_granted)
{
  if (not cap_kbps.has_value()) {
    return;
  }
  tokens_bytes      = std::max(0.0, tokens_available_at(sl_tx) - static_cast<double>(bytes_granted));
  last_update_slot = sl_tx;
}
