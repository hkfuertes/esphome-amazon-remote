#pragma once
#include "button_decoder.h"
#include "esphome/core/component.h"
#include "esphome/core/helpers.h"
#include "esphome/core/log.h"
#include "esphome/components/ble_client/ble_client.h"
#include "esphome/components/text_sensor/text_sensor.h"
#include <cstring>
#include <vector>

namespace esphome::amazon_remote {
class AmazonRemote : public Component, public ble_client::BLEClientNode, public text_sensor::TextSensor {
 public:
  void setup() override {
    this->node_state = esp32_ble_tracker::ClientState::IDLE;
    this->disable_loop();
  }

  void gap_event_handler(esp_gap_ble_cb_event_t event, esp_ble_gap_cb_param_t *param) override {
    if (event != ESP_GAP_BLE_AUTH_CMPL_EVT ||
        std::memcmp(param->ble_security.auth_cmpl.bd_addr, this->parent()->get_remote_bda(), 6) != 0) return;
    authenticated_ = param->ble_security.auth_cmpl.success;
    if (authenticated_) start_();
    else ESP_LOGW("amazon_remote", "HID waiting for successful authentication");
  }

  void gattc_event_handler(esp_gattc_cb_event_t event, esp_gatt_if_t, esp_ble_gattc_cb_param_t *param) override {
    switch (event) {
      case ESP_GATTC_DISCONNECT_EVT:
        this->cancel_timeout("hid");
        reports_.clear();
        authenticated_ = discovered_ = started_ = false;
        this->node_state = esp32_ble_tracker::ClientState::IDLE;
        this->publish_state("disconnected");
        break;
      case ESP_GATTC_SEARCH_CMPL_EVT:
        discovered_ = param->search_cmpl.status == ESP_GATT_OK;
        this->node_state = esp32_ble_tracker::ClientState::DISCOVERED;
        start_();
        break;
      case ESP_GATTC_READ_DESCR_EVT:
        if (!reading_ || index_ >= reports_.size() || param->read.handle != reports_[index_].reference) break;
        this->cancel_timeout("hid");
        if (param->read.status == ESP_GATT_OK && param->read.value_len >= 2 && param->read.value[1] == 1) {
          reports_[index_].id = param->read.value[0];
          ESP_LOGI("amazon_remote", "HID input report 0x%02X at handle 0x%04X", reports_[index_].id, reports_[index_].handle);
        } else {
          ESP_LOGW("amazon_remote", "Cannot read report reference: status=%d length=%u", param->read.status, param->read.value_len);
        }
        ++index_;
        read_next_();
        break;
      case ESP_GATTC_REG_FOR_NOTIFY_EVT:
        if (index_ >= reports_.size() || param->reg_for_notify.handle != reports_[index_].handle) break;
        if (param->reg_for_notify.status != ESP_GATT_OK) {
          ESP_LOGW("amazon_remote", "Notification registration failed: %d", param->reg_for_notify.status);
          this->cancel_timeout("hid");
          ++index_;
          subscribe_next_();
        }
        // The native BLE client writes the CCCD; wait for WRITE_DESCR before continuing.
        break;
      case ESP_GATTC_WRITE_DESCR_EVT:
        if (index_ >= reports_.size() || param->write.handle != reports_[index_].cccd) break;
        this->cancel_timeout("hid");
        if (param->write.status == ESP_GATT_OK) {
          ++subscribed_;
          ESP_LOGI("amazon_remote", "Subscribed to button report 0x%02X", reports_[index_].id);
        } else ESP_LOGW("amazon_remote", "CCCD write failed: %d", param->write.status);
        ++index_;
        subscribe_next_();
        break;
      case ESP_GATTC_NOTIFY_EVT:
        for (auto &report : reports_) {
          if (report.handle != param->notify.handle || !is_button_report(report.id)) continue;
          const auto name = decode_button(report.id, param->notify.value, param->notify.value_len);
          const bool release = name == "release";
          const auto state = release ? "release:" + report.last_button : "press:" + name;
          if (!release && name != "invalid") report.last_button = name;
          ESP_LOGI("amazon_remote", "%s | report=0x%02X raw=%s", state.c_str(), report.id,
                   format_hex_pretty(param->notify.value, param->notify.value_len).c_str());
          this->publish_state(state);
          break;
        }
        break;
      default: break;
    }
  }

 protected:
  struct Report {
    uint16_t handle;
    uint16_t reference;
    uint16_t cccd;
    uint8_t id{0};
    std::string last_button{"unknown"};
  };
  std::vector<Report> reports_;
  size_t index_{0};
  size_t subscribed_{0};
  bool authenticated_{false};
  bool discovered_{false};
  bool started_{false};
  bool reading_{false};

  void start_() {
    if (!authenticated_ || !discovered_ || started_) return;
    started_ = true;
    auto *service = this->parent()->get_service(0x1812);
    if (service == nullptr) {
      ESP_LOGE("amazon_remote", "No HID service");
      this->node_state = esp32_ble_tracker::ClientState::ESTABLISHED;
      return;
    }
    service->parse_characteristics();
    for (auto *chr : service->characteristics) {
      if (chr->uuid != esp32_ble_tracker::ESPBTUUID::from_uint16(0x2A4D) ||
          !(chr->properties & ESP_GATT_CHAR_PROP_BIT_NOTIFY)) continue;
      auto *reference = chr->get_descriptor(0x2908);
      auto *cccd = chr->get_descriptor(0x2902);
      if (reference != nullptr && cccd != nullptr) reports_.push_back({chr->handle, reference->handle, cccd->handle});
    }
    index_ = subscribed_ = 0;
    reading_ = true;
    ESP_LOGI("amazon_remote", "Reading %u HID report references", unsigned(reports_.size()));
    read_next_();
  }

  void read_next_() {
    while (index_ < reports_.size()) {
      const auto err = esp_ble_gattc_read_char_descr(this->parent()->get_gattc_if(), this->parent()->get_conn_id(),
                                                   reports_[index_].reference, ESP_GATT_AUTH_REQ_NONE);
      if (err == ESP_OK) {
        this->set_timeout("hid", 5000, [this]() {
          ESP_LOGW("amazon_remote", "Report reference read timed out");
          ++index_;
          read_next_();
        });
        return;
      }
      ESP_LOGW("amazon_remote", "Report reference request failed: %s", esp_err_to_name(err));
      ++index_;
    }
    index_ = 0;
    reading_ = false;
    subscribe_next_();
  }

  void subscribe_next_() {
    while (index_ < reports_.size()) {
      if (is_button_report(reports_[index_].id)) {
        const auto err = this->parent()->register_for_notify(reports_[index_].handle);
        if (err == ESP_OK) {
          this->set_timeout("hid", 5000, [this]() {
            ESP_LOGW("amazon_remote", "Notification setup timed out");
            ++index_;
            subscribe_next_();
          });
          return;
        }
        ESP_LOGW("amazon_remote", "Notification request failed: %s", esp_err_to_name(err));
      }
      ++index_;
    }
    this->node_state = esp32_ble_tracker::ClientState::ESTABLISHED;
    ESP_LOGI("amazon_remote", "Ready: %u button reports subscribed", unsigned(subscribed_));
    this->publish_state(subscribed_ ? "ready" : "no_button_reports");
  }
};
}  // namespace esphome::amazon_remote
