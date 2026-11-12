#include "../components/amazon_remote/audio_packet_queue.h"
#include <cassert>
#include <iostream>

int main() {
  using esphome::amazon_remote::AudioPacketQueue;
  AudioPacketQueue queue;
  const uint8_t opus[] = {0xB8, 0xFF, 0xFE};
  assert(queue.front() == nullptr);
  queue.pop();
  assert(!queue.push(opus, 0));
  assert(!queue.push(opus, AudioPacketQueue::MAX_PACKET + 1));
  for (size_t i = 0; i < AudioPacketQueue::CAPACITY; ++i) {
    const uint8_t bytes[] = {0xB8, uint8_t(i)};
    assert(queue.push(bytes, sizeof(bytes)));
  }
  assert(!queue.push(opus, sizeof(opus)));
  for (size_t i = 0; i < AudioPacketQueue::CAPACITY; ++i) {
    const auto *packet = queue.front();
    assert(packet != nullptr && packet->size == 2);
    assert(packet->data[0] == 0xB8 && packet->data[1] == i);
    queue.pop();
  }
  assert(queue.front() == nullptr);
  for (size_t i = 0; i < 3 * AudioPacketQueue::CAPACITY; ++i) {
    assert(queue.push(opus, sizeof(opus)));
    assert(queue.front()->data[0] == 0xB8);  // Raw TOC must not be stripped as a report ID.
    queue.pop();
  }
  uint8_t maximum[AudioPacketQueue::MAX_PACKET] = {};
  assert(queue.push(maximum, sizeof(maximum)));
  assert(queue.front()->size == sizeof(maximum));
  queue.clear();
  assert(queue.front() == nullptr);
  std::cout << "Audio packet queue checks passed\n";
}
