#include "../components/amazon_remote/button_decoder.h"
#include <cassert>
#include <initializer_list>
#include <iostream>

int main() {
  using esphome::amazon_remote::decode_button;
  auto decode = [](uint8_t report, std::initializer_list<uint8_t> bytes) {
    return decode_button(report, bytes.begin(), bytes.size());
  };
  assert(decode(1, {0x52}) == "up");
  assert(decode(1, {0x58}) == "select");
  assert(decode(1, {0xF1}) == "back");
  assert(decode(2, {0x21, 0x02}) == "voice");
  assert(decode(2, {0x23, 0x02}) == "home");
  assert(decode(2, {0xE9, 0x00}) == "volume_up");
  assert(decode(0xEF, {0xA2}) == "netflix");
  assert(decode(0xEF, {0xA4}) == "app_4");
  assert(decode(1, {0}) == "release");
  assert(decode(2, {0, 0}) == "release");
  assert(decode(2, {0}) == "invalid");
  assert(decode(1, {}) == "invalid");
  assert(decode(0xF0, {0xB8}) == "");
  assert(decode(1, {0xFE}) == "unknown_01_00fe");
  std::cout << "Button decoder checks passed\n";
}
