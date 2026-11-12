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
  void add_audio_callback(std::function<void(const uint8_t *, size_t)> callback) {
    audio_callbacks_.add(std::move(callback));
  }
  bool audio_ready() const { return authenticated_ && control_handle_ != 0 && audio_subscribed_; }
  bool set_microphone_enabled(bool enabled) {
    if (!audio_ready()) return false;
    uint8_t value = enabled ? 1 : 0;
    const auto err = esp_ble_gattc_write_char(this->parent()->get_gattc_if(), this->parent()->get_conn_id(),
                                             control_handle_, 1, &value, control_write_type_, ESP_GATT_AUTH_REQ_NONE);
    if (err != ESP_OK) ESP_LOGW("amazon_remote", "Microphone command failed: %s", esp_err_to_name(err));
    return err == ESP_OK;
  }

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
        service_uuids_.clear();
        report_map_handle_ = 0;
        reading_map_ = false;
        authenticated_ = discovered_ = started_ = audio_subscribed_ = false;
        control_handle_ = 0;
        this->node_state = esp32_ble_tracker::ClientState::IDLE;
        this->publish_state("disconnected");
        break;
      case ESP_GATTC_SEARCH_RES_EVT:
        service_uuids_.push_back(esp32_ble_tracker::ESPBTUUID::from_uuid(param->search_res.srvc_id.uuid));
        break;
      case ESP_GATTC_SEARCH_CMPL_EVT:
        discovered_ = param->search_cmpl.status == ESP_GATT_OK;
        this->node_state = esp32_ble_tracker::ClientState::DISCOVERED;
        start_();
        break;
      case ESP_GATTC_READ_CHAR_EVT:
        if (!reading_map_ || param->read.handle != report_map_handle_) break;
        this->cancel_timeout("hid");
        reading_map_ = false;
        if (param->read.status == ESP_GATT_OK) {
          ESP_LOGI("amazon_remote_diag", "HID_MAP length=%u", param->read.value_len);
          for (size_t offset = 0; offset < param->read.value_len; offset += 24) {
            const auto count = std::min(size_t(24), size_t(param->read.value_len) - offset);
            ESP_LOGI("amazon_remote_diag", "HID_MAP offset=%u data=%s", unsigned(offset),
                     format_hex_pretty(param->read.value + offset, count).c_str());
          }
        } else ESP_LOGW("amazon_remote_diag", "HID_MAP read failed: status=%d", param->read.status);
        read_next_();
        break;
      case ESP_GATTC_READ_DESCR_EVT:
        if (!reading_ || index_ >= reports_.size() || param->read.handle != reports_[index_].reference) break;
        this->cancel_timeout("hid");
        if (param->read.status == ESP_GATT_OK && param->read.value_len >= 2) {
          reports_[index_].id = param->read.value[0];
          auto &report = reports_[index_];
          report.type = param->read.value[1];
          if (report.id == 0xF2 && report.type == 2 &&
              (report.properties & (ESP_GATT_CHAR_PROP_BIT_WRITE | ESP_GATT_CHAR_PROP_BIT_WRITE_NR))) {
            control_handle_ = report.handle;
            control_write_type_ = report.properties & ESP_GATT_CHAR_PROP_BIT_WRITE
                                      ? ESP_GATT_WRITE_TYPE_RSP : ESP_GATT_WRITE_TYPE_NO_RSP;
          }
          ESP_LOGI("amazon_remote", "HID report 0x%02X type=%u at handle 0x%04X", report.id, report.type, report.handle);
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
          if (reports_[index_].id == 0xF0) audio_subscribed_ = true;
          ESP_LOGI("amazon_remote", "Subscribed to HID report 0x%02X", reports_[index_].id);
        } else ESP_LOGW("amazon_remote", "CCCD write failed: %d", param->write.status);
        ++index_;
        subscribe_next_();
        break;
      case ESP_GATTC_WRITE_CHAR_EVT:
        if (param->write.handle == control_handle_ && param->write.status != ESP_GATT_OK)
          ESP_LOGW("amazon_remote", "Microphone control write failed: %d", param->write.status);
        break;
      case ESP_GATTC_NOTIFY_EVT:
        for (auto &report : reports_) {
          if (report.handle != param->notify.handle) continue;
          if (report.id == 0xF0 && report.type == 1) {
            // GATT values contain raw Opus, without the hidraw report-ID prefix.
            audio_callbacks_.call(param->notify.value, param->notify.value_len);
            break;
          }
          if (!is_button_report(report.id)) continue;
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
    esp_gatt_char_prop_t properties;
    uint8_t id{0};
    uint8_t type{0};
    std::string last_button{"unknown"};
  };
  std::vector<Report> reports_;
  std::vector<esp32_ble_tracker::ESPBTUUID> service_uuids_;
  uint16_t report_map_handle_{0};
  bool reading_map_{false};
  size_t index_{0};
  size_t subscribed_{0};
  bool authenticated_{false};
  bool discovered_{false};
  bool started_{false};
  bool reading_{false};
  bool audio_subscribed_{false};
  uint16_t control_handle_{0};
  esp_gatt_write_type_t control_write_type_{ESP_GATT_WRITE_TYPE_RSP};
  CallbackManager<void(const uint8_t *, size_t)> audio_callbacks_;

  void start_() {
    if (!authenticated_ || !discovered_ || started_) return;
    started_ = true;
    log_gatt_inventory_();
    auto *service = this->parent()->get_service(0x1812);
    if (service == nullptr) {
      ESP_LOGE("amazon_remote", "No HID service");
      this->node_state = esp32_ble_tracker::ClientState::ESTABLISHED;
      return;
    }
    if (!service->parsed) service->parse_characteristics();
    for (auto *chr : service->characteristics) {
      if (chr->uuid != esp32_ble_tracker::ESPBTUUID::from_uint16(0x2A4D)) continue;
      auto *reference = chr->get_descriptor(0x2908);
      auto *cccd = chr->get_descriptor(0x2902);
      if (reference != nullptr)
        reports_.push_back({chr->handle, reference->handle, uint16_t(cccd ? cccd->handle : 0), chr->properties});
    }
    index_ = subscribed_ = 0;
    reading_ = true;
    ESP_LOGI("amazon_remote", "Reading %u HID report references", unsigned(reports_.size()));
    auto *map = service->get_characteristic(0x2A4B);
    if (map != nullptr) {
      report_map_handle_ = map->handle;
      const auto err = esp_ble_gattc_read_char(this->parent()->get_gattc_if(), this->parent()->get_conn_id(),
                                              report_map_handle_, ESP_GATT_AUTH_REQ_NONE);
      if (err == ESP_OK) {
        reading_map_ = true;
        this->set_timeout("hid", 5000, [this]() {
          reading_map_ = false;
          ESP_LOGW("amazon_remote_diag", "HID_MAP read timed out");
          read_next_();
        });
        return;
      }
      ESP_LOGW("amazon_remote_diag", "HID_MAP request failed: %s", esp_err_to_name(err));
    }
    read_next_();
  }

  void log_gatt_inventory_() {
    // Research only: enumerate cached metadata; never read or write vendor characteristic values.
    for (const auto &uuid : service_uuids_) {
      auto *service = this->parent()->get_service(uuid);
      if (service == nullptr) continue;
      char uuid_buf[64];
      uuid.to_str(uuid_buf);
      ESP_LOGI("amazon_remote_diag", "GATT service=%s handles=0x%04X-0x%04X", uuid_buf,
               service->start_handle, service->end_handle);
      if (!service->parsed) service->parse_characteristics();
      for (auto *chr : service->characteristics) {
        chr->uuid.to_str(uuid_buf);
        ESP_LOGI("amazon_remote_diag", "GATT characteristic=%s handle=0x%04X properties=0x%02X",
                 uuid_buf, chr->handle, chr->properties);
        chr->parse_descriptors();
        for (auto *descriptor : chr->descriptors) {
          descriptor->uuid.to_str(uuid_buf);
          ESP_LOGI("amazon_remote_diag", "GATT descriptor=%s handle=0x%04X", uuid_buf, descriptor->handle);
        }
      }
    }
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
      const auto &report = reports_[index_];
      if (report.type == 1 && report.cccd != 0 &&
          (is_button_report(report.id) || report.id == 0xF0)) {
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
    ESP_LOGI("amazon_remote", "Ready: %u HID reports subscribed, microphone=%s", unsigned(subscribed_),
             audio_ready() ? "available" : "unavailable");
    this->publish_state(subscribed_ ? "ready" : "no_button_reports");
  }
};
}  // namespace esphome::amazon_remote
