"""Bluetooth TV Remotes native Home Assistant integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo

from .const import CONF_MAC, DOMAIN, PLATFORMS
from .hid_client import TVRemoteClient

type TVRemoteConfigEntry = ConfigEntry[TVRemoteClient]


def device_info_for_entry(entry: TVRemoteConfigEntry) -> DeviceInfo:
    """Shared DeviceInfo for all entities belonging to one remote."""
    mac = entry.data[CONF_MAC]
    return DeviceInfo(
        identifiers={(DOMAIN, mac)},
        connections={(CONNECTION_BLUETOOTH, mac)},
        name=entry.title,
        manufacturer="Amazon",
        model="Fire TV Remote",
    )


def unique_prefix_for_mac(mac: str) -> str:
    """Lowercase hex MAC prefix used for entity unique IDs."""
    return mac.replace(":", "").lower()


async def async_setup_entry(hass: HomeAssistant, entry: TVRemoteConfigEntry) -> bool:
    """Set up Bluetooth TV Remotes from a config entry."""
    client = TVRemoteClient(hass.loop, entry.data[CONF_MAC])
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
