#pragma once
#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>

namespace esphome::amazon_remote {
// ponytail: 16 packets (~320 ms at 20 ms/frame); increase only if measured scheduling stalls need it.
class AudioPacketQueue {
 public:
  static constexpr size_t MAX_PACKET = 512;  // Maximum BLE attribute value, not an HID report ID prefix.
  static constexpr size_t CAPACITY = 16;
  struct Packet {
    std::array<uint8_t, MAX_PACKET> data;
    size_t size{0};
  };
  bool push(const uint8_t *data, size_t size) {
    if (size == 0 || size > MAX_PACKET || count_ == CAPACITY) return false;
    auto &packet = packets_[(head_ + count_) % CAPACITY];
    std::memcpy(packet.data.data(), data, size);
    packet.size = size;
    ++count_;
    return true;
  }
  const Packet *front() const { return count_ ? &packets_[head_] : nullptr; }
  void pop() {
    if (!count_) return;
    head_ = (head_ + 1) % CAPACITY;
    --count_;
  }
  void clear() { head_ = count_ = 0; }

 protected:
  std::array<Packet, CAPACITY> packets_;
  size_t head_{0};
  size_t count_{0};
};
}  // namespace esphome::amazon_remote
