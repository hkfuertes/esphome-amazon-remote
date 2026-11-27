"""Assist Satellite entity driven by the remote's push-to-talk voice button."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import logging

from homeassistant.components.assist_pipeline import (
    PipelineEvent,
    PipelineEventType,
    PipelineStage,
)
from homeassistant.components.assist_satellite import (
    AssistSatelliteConfiguration,
    AssistSatelliteEntity,
)
from homeassistant.components.media_player import async_process_play_media_url
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import (  # noqa: TID252
    TVRemoteConfigEntry,
    device_info_for_entry,
    unique_prefix_for_mac,
)
from .const import (
    CONF_MAC,
    CONF_REMOTE_TYPE,
    DOMAIN,
    MAX_VOICE_SECONDS,
    REMOTE_TYPE_AMAZON,
    REMOTE_TYPE_CHROMECAST,
)
from .protocol import ImaAdpcmDecoder, OpusDecoder

_LOGGER = logging.getLogger(__name__)

_AUDIO_QUEUE_MAX = 64  # ~1.28 s of 20 ms frames
_RELEASE_DRAIN_SECONDS = 0.06


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TVRemoteConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Assist Satellite entity."""
    remote_type = entry.data.get(CONF_REMOTE_TYPE, REMOTE_TYPE_AMAZON)
    if remote_type == REMOTE_TYPE_CHROMECAST:
        decoder: OpusDecoder | ImaAdpcmDecoder = ImaAdpcmDecoder()
    else:
        decoder = await hass.async_add_executor_job(OpusDecoder)
    async_add_entities([TVRemoteAssistSatellite(entry, decoder)])


class TVRemoteAssistSatellite(AssistSatelliteEntity):
    """Push-to-talk Assist Satellite for a Bluetooth TV remote."""

    _attr_has_entity_name = True
    _attr_name = "Assist Satellite"

    def __init__(
        self, entry: TVRemoteConfigEntry, decoder: OpusDecoder | ImaAdpcmDecoder
    ) -> None:
        self._entry = entry
        self._client = entry.runtime_data
        self._prefix = unique_prefix_for_mac(entry.data[CONF_MAC])
        self._attr_unique_id = f"{self._prefix}-assist-satellite"
        self._attr_device_info = device_info_for_entry(entry)

        self._decoder = decoder
        self._audio_queue: asyncio.Queue[bytes | None] | None = None
        self._voice_active = False
        self._frames_captured = 0
        self._max_timer: asyncio.TimerHandle | None = None
        self._finish_task: asyncio.Task[None] | None = None
        self._run_task: asyncio.Task[None] | None = None

    @property
    def pipeline_entity_id(self) -> str | None:
        ent_reg = er.async_get(self.hass)
        return ent_reg.async_get_entity_id("select", DOMAIN, f"{self._prefix}-pipeline")

    @property
    def available(self) -> bool:
        return self._client.connected

    @callback
    def _resolve_vad_sensitivity(self) -> float:
        # ponytail: push-to-talk ends on button release (EOF); long silence avoids mid-sentence VAD cuts.
        return MAX_VOICE_SECONDS

    async def async_get_configuration(self) -> AssistSatelliteConfiguration:
        return AssistSatelliteConfiguration(
            available_wake_words=[],
            active_wake_words=[],
            max_active_wake_words=0,
        )

    async def async_set_configuration(self, config: AssistSatelliteConfiguration) -> None:
        raise NotImplementedError

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._client.add_button_listener(self._handle_button))
        self.async_on_remove(self._client.add_audio_listener(self._handle_audio))
        self.async_on_remove(self._client.add_connection_listener(self._handle_connection))

    async def async_will_remove_from_hass(self) -> None:
        self._stop_capture(send_eof=True)
        if self._run_task and not self._run_task.done():
            self._run_task.cancel()
        self._decoder.close()
        await super().async_will_remove_from_hass()

    @callback
    def _handle_connection(self, connected: bool) -> None:
        if not connected:
            self._stop_capture(send_eof=True)
        self.async_write_ha_state()

    @callback
    def _handle_button(self, _event_str: str, key: str, pressed: bool) -> None:
        if key != "voice":
            return
        if pressed:
            self._start_voice()
        else:
            self._schedule_finish()

    @callback
    def _handle_audio(self, packet: bytes) -> None:
        if not self._voice_active or self._audio_queue is None:
            return
        pcm = self._decoder.decode(packet)
        if not pcm:
            return
        self._frames_captured += 1
        if self._audio_queue.full():
            try:
                self._audio_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        self._audio_queue.put_nowait(pcm)

    def _start_voice(self) -> None:
        self._cancel_timers()
        if self._voice_active:
            self._stop_capture(send_eof=True)

        self._decoder.reset()
        queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=_AUDIO_QUEUE_MAX)
        self._audio_queue = queue
        self._voice_active = True
        self._frames_captured = 0

        if not self._client.set_mic_enabled(True):
            self._voice_active = False
            self._audio_queue = None
            return

        self._max_timer = self.hass.loop.call_later(MAX_VOICE_SECONDS, self._schedule_finish)
        speaker = self._client.output_speaker or ""
        end_stage = PipelineStage.TTS if speaker else PipelineStage.INTENT
        _LOGGER.debug("Starting Assist push-to-talk (end_stage=%s speaker=%s)", end_stage, speaker or "none")
        self._run_task = self._entry.async_create_background_task(
            self.hass,
            self.async_accept_pipeline_from_satellite(
                audio_stream=self._audio_stream(queue),
                start_stage=PipelineStage.STT,
                end_stage=end_stage,
            ),
            f"{self.entity_id}_ptt",
        )

    def _schedule_finish(self) -> None:
        if not self._voice_active:
            return
        self._cancel_timers()
        self._client.set_mic_enabled(False)
        self._finish_task = self.hass.async_create_task(self._finish_after_drain())

    async def _finish_after_drain(self) -> None:
        try:
            await asyncio.sleep(_RELEASE_DRAIN_SECONDS)
        except asyncio.CancelledError:
            return
        _LOGGER.debug("Finishing Assist audio stream (%d frames)", self._frames_captured)
        self._stop_capture(send_eof=True)

    def _stop_capture(self, *, send_eof: bool) -> None:
        self._cancel_timers()
        if self._voice_active:
            self._client.set_mic_enabled(False)
        self._voice_active = False
        queue = self._audio_queue
        self._audio_queue = None
        if send_eof and queue is not None:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(None)

    def _cancel_timers(self) -> None:
        if self._max_timer is not None:
            self._max_timer.cancel()
            self._max_timer = None
        if self._finish_task is not None and not self._finish_task.done():
            self._finish_task.cancel()
        self._finish_task = None

    async def _audio_stream(self, queue: asyncio.Queue[bytes | None]) -> AsyncIterator[bytes]:
        while True:
            chunk = await queue.get()
            if chunk is None:
                break
            yield chunk

    @callback
    def on_pipeline_event(self, event: PipelineEvent) -> None:
        if event.type == PipelineEventType.ERROR:
            _LOGGER.warning("Assist pipeline error: %s", event.data)
            self._stop_capture(send_eof=True)
            self.tts_response_finished()
            return
        if event.type != PipelineEventType.TTS_END:
            return
        speaker = self._client.output_speaker or ""
        tts_output = (event.data or {}).get("tts_output") or {}
        url = tts_output.get("url")
        if not speaker or not url:
            self.tts_response_finished()
            return
        self._entry.async_create_background_task(
            self.hass,
            self._play_tts(speaker, url),
            f"{self.entity_id}_tts",
        )

    async def _play_tts(self, speaker: str, url: str) -> None:
        try:
            media_url = async_process_play_media_url(self.hass, url)
            await self.hass.services.async_call(
                "media_player",
                "play_media",
                {
                    "entity_id": speaker,
                    "media_content_id": media_url,
                    "media_content_type": "music",
                },
                blocking=True,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Failed playing Assist TTS on %s: %s", speaker, err)
        finally:
            self.tts_response_finished()
