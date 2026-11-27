"""Last Button sensor for Bluetooth TV Remotes."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import (  # noqa: TID252
    TVRemoteConfigEntry,
    device_info_for_entry,
    unique_prefix_for_mac,
)
from .const import CONF_MAC


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TVRemoteConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Last Button sensor."""
    async_add_entities([TVRemoteButtonSensor(entry)])


class TVRemoteButtonSensor(SensorEntity):
    """Publishes ready/disconnected and press:<key>/release:<key> states."""

    _attr_has_entity_name = True
    _attr_name = "Last Button"
    _attr_icon = "mdi:bluetooth"
    _attr_should_poll = False

    def __init__(self, entry: TVRemoteConfigEntry) -> None:
        self._client = entry.runtime_data
        self._attr_unique_id = f"{unique_prefix_for_mac(entry.data[CONF_MAC])}-last-button"
        self._attr_device_info = device_info_for_entry(entry)
        self._attr_native_value = "ready" if self._client.connected else "disconnected"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._client.add_button_listener(self._handle_button))
        self.async_on_remove(self._client.add_connection_listener(self._handle_connection))

    @callback
    def _handle_connection(self, connected: bool) -> None:
        self._attr_native_value = "ready" if connected else "disconnected"
        self.async_write_ha_state()

    @callback
    def _handle_button(self, event_str: str, _key: str, _pressed: bool) -> None:
        self._attr_native_value = event_str
        self.async_write_ha_state()
