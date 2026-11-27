"""Bluetooth TV Remotes native Home Assistant integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo

from .const import (
    CONF_MAC,
    CONF_REMOTE_TYPE,
    DOMAIN,
    PLATFORMS,
    REMOTE_MODELS,
    REMOTE_TYPE_AMAZON,
)
from .hid_client import TVRemoteClient

type TVRemoteConfigEntry = ConfigEntry[TVRemoteClient]


def device_info_for_entry(entry: TVRemoteConfigEntry) -> DeviceInfo:
    """Shared DeviceInfo for all entities belonging to one remote."""
    mac = entry.data[CONF_MAC]
    remote_type = entry.data.get(CONF_REMOTE_TYPE, REMOTE_TYPE_AMAZON)
    manufacturer, model, _ = REMOTE_MODELS.get(
        remote_type, REMOTE_MODELS[REMOTE_TYPE_AMAZON]
    )
    return DeviceInfo(
        identifiers={(DOMAIN, mac)},
        connections={(CONNECTION_BLUETOOTH, mac)},
        name=entry.title,
        manufacturer=manufacturer,
        model=model,
    )


def unique_prefix_for_mac(mac: str) -> str:
    """Lowercase hex MAC prefix used for entity unique IDs."""
    return mac.replace(":", "").lower()


async def async_setup_entry(hass: HomeAssistant, entry: TVRemoteConfigEntry) -> bool:
    """Set up Bluetooth TV Remotes from a config entry."""
    remote_type = entry.data.get(CONF_REMOTE_TYPE, REMOTE_TYPE_AMAZON)
    client = TVRemoteClient(hass.loop, entry.data[CONF_MAC], remote_type=remote_type)
    entry.runtime_data = client
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    client.start()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: TVRemoteConfigEntry) -> bool:
    """Unload an Bluetooth TV Remotes config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.stop()
    return unload_ok
