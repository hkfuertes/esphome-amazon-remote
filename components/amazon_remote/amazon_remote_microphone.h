#pragma once
#include "amazon_remote.h"
#include "audio_packet_queue.h"
#include "esphome/components/microphone/microphone.h"
#include <opus.h>
#include <array>
#include <algorithm>

namespace esphome::amazon_remote {
class AmazonRemoteMicrophone : public Component, public microphone::Microphone {
 public:
  void set_remote(AmazonRemote *remote) {
    remote_ = remote;
    remote_->add_audio_callback([this](const uint8_t *data, size_t size) {
      // ESPHome dispatches GATT events on its main loop, not the Bluetooth task.
      if (this->is_running() && !queue_.push(data, size)) ++dropped_;
    });
  }

  void setup() override {
    int error;
    decoder_ = opus_decoder_create(16000, 1, &error);
    if (decoder_ == nullptr || error != OPUS_OK) {
      ESP_LOGE("amazon_remote.mic", "Cannot create Opus decoder: %d", error);
      this->mark_failed();
    }
    this->disable_loop();
  }

  bool is_ready() const { return decoder_ != nullptr && !this->is_failed() && remote_->audio_ready(); }

  void start() override {
    if (this->is_running()) return;
    if (!is_ready()) {
      ESP_LOGW("amazon_remote.mic", "Microphone not ready: wait for authenticated HID discovery");
      return;
    }
    opus_decoder_ctl(decoder_, OPUS_RESET_STATE);
    queue_.clear();
    frames_ = dropped_ = errors_ = peak_ = 0;
    if (!remote_->set_microphone_enabled(true)) return;
    this->state_ = microphone::STATE_RUNNING;
    this->enable_loop();
    ESP_LOGI("amazon_remote.mic", "Microphone enabled: Opus -> PCM 16000 Hz mono");
  }

  void stop() override {
    if (this->is_stopped()) return;
    remote_->set_microphone_enabled(false);
    queue_.clear();
    this->state_ = microphone::STATE_STOPPED;
    this->disable_loop();
    ESP_LOGI("amazon_remote.mic", "Microphone stopped: frames=%u errors=%u dropped=%u peak=%u",
             unsigned(frames_), unsigned(errors_), unsigned(dropped_), unsigned(peak_));
  }

  void loop() override {
    if (!this->is_running()) return;
    if (!remote_->audio_ready()) {
      stop();
      return;
    }
    // Decode a bounded batch so Wi-Fi, BLE and the Assist stream keep getting airtime.
    for (size_t i = 0; i < 4; ++i) {
      const auto *packet = queue_.front();
      if (packet == nullptr) break;
      const int samples = opus_decode(decoder_, packet->data.data(), packet->size, pcm_.data(), pcm_.size(), 0);
      if (samples > 0) {
        if (frames_ == 0)
          ESP_LOGI("amazon_remote.mic", "First Opus packet: %u bytes -> %d PCM samples", unsigned(packet->size), samples);
        ++frames_;
        for (int j = 0; j < samples; ++j) {
          const uint32_t level = pcm_[j] < 0 ? -int32_t(pcm_[j]) : pcm_[j];
          peak_ = std::max(peak_, level);
        }
        const auto *bytes = reinterpret_cast<const uint8_t *>(pcm_.data());
        pcm_bytes_.assign(bytes, bytes + samples * sizeof(int16_t));
        this->data_callbacks_.call(pcm_bytes_);
      } else {
        ++errors_;
        if (errors_ <= 3) ESP_LOGW("amazon_remote.mic", "Invalid Opus packet: %s", opus_strerror(samples));
      }
      queue_.pop();
    }
  }

 protected:
  AmazonRemote *remote_{nullptr};
  OpusDecoder *decoder_{nullptr};
  AudioPacketQueue queue_;
  std::array<int16_t, 1920> pcm_;  // Opus maximum: 120 ms at 16 kHz.
  std::vector<uint8_t> pcm_bytes_;
  uint32_t frames_{0};
  uint32_t dropped_{0};
  uint32_t errors_{0};
  uint32_t peak_{0};
};
}  // namespace esphome::amazon_remote
