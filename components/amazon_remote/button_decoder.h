#pragma once
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <string>

namespace esphome::amazon_remote {
inline bool is_button_report(uint8_t report) { return report == 1 || report == 2 || report == 0xEF; }

inline std::string decode_button(uint8_t report, const uint8_t *data, size_t size) {
  if (!is_button_report(report)) return "";
  if (size < (report == 2 ? 2U : 1U)) return "invalid";
  bool released = true;
  for (size_t i = 0; i < size; ++i) released &= data[i] == 0;
  if (released) return "release";
  const uint16_t code = report == 2 ? uint16_t(data[0]) | (uint16_t(data[1]) << 8) : data[0];
  if (report == 1) {
    switch (code) {
      case 0x4F: return "right";
      case 0x50: return "left";
      case 0x51: return "down";
      case 0x52: return "up";
      case 0x28: case 0x58: return "select";
      case 0x29: case 0xF1: return "back";
      case 0x66: return "power";
    }
  } else if (report == 2) {
    switch (code) {
      case 0x0221: return "voice";
      case 0x0223: return "home";
      case 0x0040: return "menu";
      case 0x00E2: return "mute";
      case 0x00E9: return "volume_up";
      case 0x00EA: return "volume_down";
      case 0x00CD: return "play_pause";
      case 0x00B4: return "rewind";
      case 0x00B3: return "fast_forward";
      case 0x008D: return "tv";
      case 0x0030: case 0x0066: return "power";
      case 0x0224: return "back";
    }
  } else {
    switch (code) {
      case 0xA1: return "prime_video";
      case 0xA2: return "netflix";
      case 0xA3: return "disney_plus";
      case 0xA4: return "app_4";  // Printed app varies by remote SKU.
    }
  }
  char unknown[32];
  std::snprintf(unknown, sizeof(unknown), "unknown_%02x_%04x", report, code);
  return unknown;
}
}  // namespace esphome::amazon_remote
