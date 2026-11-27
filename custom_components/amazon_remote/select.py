"""Assist pipeline and output speaker selectors for Amazon Remote."""

from __future__ import annotations

from homeassistant.components.assist_pipeline import AssistPipelineSelect
from homeassistant.components.select import SelectEntity
from homeassistant.const import (
    EVENT_HOMEASSISTANT_STARTED,
    EVENT_STATE_CHANGED,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    EntityCategory,
)
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import (  # noqa: TID252
    AmazonRemoteConfigEntry,
    device_info_for_entry,
    unique_prefix_for_mac,
)
from .const import CONF_MAC, CONF_OUTPUT_SPEAKER, DOMAIN

SPEAKER_NONE = "None"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AmazonRemoteConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Assist pipeline and output speaker selector entities."""
    async_add_entities(
        [
            AmazonRemotePipelineSelect(hass, entry),
            AmazonRemoteOutputSpeakerSelect(entry),
        ]
    )


class AmazonRemotePipelineSelect(AssistPipelineSelect):
    """Select which Assist pipeline runs for this remote."""

    _attr_has_entity_name = True
    _attr_name = "Assistant"

    def __init__(self, hass: HomeAssistant, entry: AmazonRemoteConfigEntry) -> None:
        mac = entry.data[CONF_MAC]
        super().__init__(hass, DOMAIN, unique_prefix_for_mac(mac))
        self._attr_device_info = device_info_for_entry(entry)


class AmazonRemoteOutputSpeakerSelect(SelectEntity, RestoreEntity):
    """Dynamic dropdown of all media_player entities for Assist TTS output."""

    _attr_has_entity_name = True
    _attr_name = "Assist Output Speaker"
    _attr_icon = "mdi:speaker"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_should_poll = False

    def __init__(self, entry: AmazonRemoteConfigEntry) -> None:
        self._entry = entry
        self._client = entry.runtime_data
        self._attr_unique_id = f"{unique_prefix_for_mac(entry.data[CONF_MAC])}-output-speaker"
        self._attr_device_info = device_info_for_entry(entry)
        initial = (entry.options.get(CONF_OUTPUT_SPEAKER) or "").strip()
        self._current = initial or SPEAKER_NONE
        self._sync_client()

    def _sync_client(self) -> None:
        self._client.output_speaker = None if self._current == SPEAKER_NONE else self._current

    @property
    def current_option(self) -> str:
        return self._current

    @property
    def options(self) -> list[str]:
        if self.hass is None:
            return [SPEAKER_NONE] if self._current == SPEAKER_NONE else [SPEAKER_NONE, self._current]
        players = sorted(self.hass.states.async_entity_ids("media_player"))
        if self._current != SPEAKER_NONE and self._current not in players:
            players.append(self._current)
            players.sort()
        return [SPEAKER_NONE, *players]

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is not None:
            val = (last_state.state or "").strip()
            if val and val not in (STATE_UNKNOWN, STATE_UNAVAILABLE):
                self._current = val
        self._sync_client()
        self.async_on_remove(
            self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._handle_state_changed)
        )
        if not self.hass.is_running:
            self.async_on_remove(
                self.hass.bus.async_listen_once(
                    EVENT_HOMEASSISTANT_STARTED, self._handle_ha_started
                )
            )
        self.async_write_ha_state()

    @callback
    def _handle_state_changed(self, event: Event) -> None:
        entity_id: str = event.data.get("entity_id", "")
        if not entity_id.startswith("media_player."):
            return
        if event.data.get("old_state") is None or event.data.get("new_state") is None:
            self.async_write_ha_state()

    @callback
    def _handle_ha_started(self, _event: Event) -> None:
        self.async_write_ha_state()

    async def async_select_option(self, option: str) -> None:
        self._current = option
        self._sync_client()
        self.async_write_ha_state()
