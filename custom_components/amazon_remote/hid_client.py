"""Asyncio /dev/hidraw client for an Amazon Fire TV Bluetooth remote."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
import os
from pathlib import Path

from .protocol import (  # noqa: TID252
    DEFAULT_AUDIO_REPORT_ID,
    DEFAULT_ENABLE_REPORT_ID,
    decode_button,
    discover_hidraw_devices,
    is_button_report,
    normalize_mac,
    parse_report_ids,
)

_LOGGER = logging.getLogger(__name__)

# ponytail: 0.5s sysfs scan while disconnected avoids pyudev dependency.
RECONNECT_POLL_SECONDS = 0.5


class AmazonRemoteClient:
    """Tracks a bonded Amazon remote via /dev/hidraw and dispatches buttons + Opus audio."""

    def __init__(self, loop: asyncio.AbstractEventLoop, mac: str) -> None:
        self._loop = loop
        self.mac = normalize_mac(mac)
        self._fd: int | None = None
        self._dev_path: str | None = None
        self._audio_id = DEFAULT_AUDIO_REPORT_ID
        self._enable_id = DEFAULT_ENABLE_REPORT_ID
        self._last_button: str | None = None
        self.output_speaker: str | None = None
        self._stopped = False
        self._reconnect_event = asyncio.Event()
        self._watch_task: asyncio.Task[None] | None = None

        self._button_cbs: list[Callable[[str, str, bool], None]] = []
        self._audio_cbs: list[Callable[[bytes], None]] = []
        self._conn_cbs: list[Callable[[bool], None]] = []

    @property
    def connected(self) -> bool:
        return self._fd is not None

    def add_button_listener(self, cb: Callable[[str, str, bool], None]) -> Callable[[], None]:
        self._button_cbs.append(cb)
        return lambda: self._button_cbs.remove(cb) if cb in self._button_cbs else None

    def add_audio_listener(self, cb: Callable[[bytes], None]) -> Callable[[], None]:
        self._audio_cbs.append(cb)
        return lambda: self._audio_cbs.remove(cb) if cb in self._audio_cbs else None

    def add_connection_listener(self, cb: Callable[[bool], None]) -> Callable[[], None]:
        self._conn_cbs.append(cb)
        return lambda: self._conn_cbs.remove(cb) if cb in self._conn_cbs else None

    def start(self) -> None:
        if self._watch_task is None:
            self._stopped = False
            self._watch_task = self._loop.create_task(self._watch_loop())

    async def stop(self) -> None:
        self._stopped = True
        self._reconnect_event.set()
        self._detach(notify=False)
        if self._watch_task is not None:
            self._watch_task.cancel()
            try:
                await self._watch_task
            except asyncio.CancelledError:
                pass
            self._watch_task = None

    def set_mic_enabled(self, enabled: bool) -> bool:
        if self._fd is None:
            return False
        try:
            os.write(self._fd, bytes((self._enable_id, 1 if enabled else 0)))
            return True
        except OSError as err:
            _LOGGER.warning("Failed writing mic enable=%s to %s: %s", enabled, self._dev_path, err)
            return False

    async def _watch_loop(self) -> None:
        while not self._stopped:
            if self._fd is None:
                await self._try_attach()
            if self._fd is not None:
                self._reconnect_event.clear()
                await self._reconnect_event.wait()
            else:
                await asyncio.sleep(RECONNECT_POLL_SECONDS)

    def _probe_hidraw(self) -> tuple[str, int, int] | None:
        info = discover_hidraw_devices().get(self.mac)
        if info is None:
            return None
        dev_path = info["dev"]
        try:
            desc = Path(info["sys_dir"], "device", "report_descriptor").read_bytes()
            audio_id, enable_id = parse_report_ids(desc)
        except OSError as err:
            _LOGGER.debug("Could not read report_descriptor for %s: %s", dev_path, err)
            audio_id, enable_id = DEFAULT_AUDIO_REPORT_ID, DEFAULT_ENABLE_REPORT_ID
        return dev_path, audio_id, enable_id

    async def _try_attach(self) -> None:
        found = await self._loop.run_in_executor(None, self._probe_hidraw)
        if found is None or self._stopped:
            return
        dev_path, self._audio_id, self._enable_id = found

        try:
            fd = os.open(dev_path, os.O_RDWR | os.O_NONBLOCK)
        except OSError as err:
            _LOGGER.warning("Unable to open %s for %s: %s", dev_path, self.mac, err)
            return

        self._fd = fd
        self._dev_path = dev_path
        self._last_button = None
        self._loop.add_reader(fd, self._on_readable)
        _LOGGER.info(
            "Attached Amazon remote %s on %s (audio=0x%02x enable=0x%02x)",
            self.mac,
            dev_path,
            self._audio_id,
            self._enable_id,
        )
        for cb in list(self._conn_cbs):
            cb(True)

    def _detach(self, *, notify: bool = True) -> None:
        fd = self._fd
        if fd is None:
            return
        self._fd = None
        dev_path = self._dev_path
        self._dev_path = None
        self._last_button = None
        try:
            self._loop.remove_reader(fd)
        except Exception:  # noqa: BLE001
            pass
        try:
            os.close(fd)
        except OSError:
            pass
        _LOGGER.info("Detached Amazon remote %s (%s)", self.mac, dev_path)
        if notify:
            for cb in list(self._conn_cbs):
                cb(False)
        self._reconnect_event.set()

    def _on_readable(self) -> None:
        fd = self._fd
        if fd is None:
            return
        while True:
            try:
                data = os.read(fd, 256)
            except BlockingIOError:
                return
            except OSError as err:
                _LOGGER.debug("HID read ended on %s: %s", self._dev_path, err)
                self._detach(notify=True)
                return
            if not data:
                self._detach(notify=True)
                return
            self._handle_packet(data)

    def _handle_packet(self, data: bytes) -> None:
        report_id = data[0]
        payload = data[1:]
        if report_id == self._audio_id:
            for cb in list(self._audio_cbs):
                cb(payload)
            return
        if not is_button_report(report_id):
            return
        decoded = decode_button(report_id, payload)
        if not decoded:
            return
        if decoded == "release":
            if self._last_button is None:
                return
            key = self._last_button
            self._last_button = None
            event_str = f"release:{key}"
            for cb in list(self._button_cbs):
                cb(event_str, key, False)
            return
        self._last_button = decoded
        event_str = f"press:{decoded}"
        for cb in list(self._button_cbs):
            cb(event_str, decoded, True)
