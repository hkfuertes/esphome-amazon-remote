"""Asyncio /dev/hidraw (+ BlueZ ATVV GATT) client for Bluetooth TV remotes."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
import os
from pathlib import Path
from typing import Any

from .const import REMOTE_TYPE_AMAZON, REMOTE_TYPE_CHROMECAST
from .protocol import (  # noqa: TID252
    ATVV_GET_CAPS,
    ATVV_MIC_CLOSE,
    ATVV_MIC_OPEN_16K,
    CHROMECAST_BUTTON_REPORT_ID,
    DEFAULT_AUDIO_REPORT_ID,
    DEFAULT_ENABLE_REPORT_ID,
    UUID_ATVV_AUDIO,
    UUID_ATVV_CTL,
    UUID_ATVV_STATUS,
    decode_button,
    discover_hidraw_devices,
    is_button_report,
    normalize_mac,
    parse_report_ids,
)

_LOGGER = logging.getLogger(__name__)

# ponytail: 0.5s sysfs scan while disconnected avoids pyudev dependency.
RECONNECT_POLL_SECONDS = 0.5


class TVRemoteClient:
    """Tracks a bonded TV remote via /dev/hidraw and dispatches buttons + voice audio."""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        mac: str,
        *,
        remote_type: str = REMOTE_TYPE_AMAZON,
    ) -> None:
        self._loop = loop
        self.mac = normalize_mac(mac)
        self.remote_type = remote_type
        self._fd: int | None = None
        self._dev_path: str | None = None
        self._audio_id = DEFAULT_AUDIO_REPORT_ID
        self._enable_id = DEFAULT_ENABLE_REPORT_ID
        self._last_button: str | None = None
        self.output_speaker: str | None = None
        self._stopped = False
        self._reconnect_event = asyncio.Event()
        self._watch_task: asyncio.Task[None] | None = None
        self._atvv_task: asyncio.Task[None] | None = None
        self._atvv_ctl: Any = None
        self._atvv_voice_active = False
        self._atvv_closing = False
        self._atvv_session_id: int | None = None

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
        await self._stop_atvv()
        if self._watch_task is not None:
            self._watch_task.cancel()
            try:
                await self._watch_task
            except asyncio.CancelledError:
                pass
            self._watch_task = None

    def set_mic_enabled(self, enabled: bool) -> bool:
        if self.remote_type == REMOTE_TYPE_CHROMECAST:
            if self._atvv_ctl is None:
                return False
            if enabled:
                self._atvv_closing = False
                # A physical AUDIO_START already opened hold-to-talk; do not replace it.
                if self._atvv_session_id is None:
                    self._loop.create_task(self._atvv_write(ATVV_MIC_OPEN_16K))
            elif self._atvv_voice_active and not self._atvv_closing:
                # Keep the press edge until AUDIO_STOP confirms release.
                self._atvv_closing = True
                close = (
                    bytes((0x0D, self._atvv_session_id))
                    if self._atvv_session_id is not None else ATVV_MIC_CLOSE
                )
                self._loop.create_task(self._atvv_write(close))
            return True
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
        if self.remote_type == REMOTE_TYPE_CHROMECAST:
            return dev_path, -1, -1
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
            flags = os.O_RDONLY if self.remote_type == REMOTE_TYPE_CHROMECAST else os.O_RDWR
            fd = os.open(dev_path, flags | os.O_NONBLOCK)
        except OSError as err:
            _LOGGER.warning("Unable to open %s for %s: %s", dev_path, self.mac, err)
            return

        self._fd = fd
        self._dev_path = dev_path
        self._last_button = None
        self._loop.add_reader(fd, self._on_readable)
        if self.remote_type == REMOTE_TYPE_CHROMECAST:
            _LOGGER.info("Attached Chromecast remote %s on %s", self.mac, dev_path)
            await self._stop_atvv()
            self._atvv_task = self._loop.create_task(self._run_atvv())
        else:
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
        self._atvv_ctl = None
        self._atvv_voice_active = False
        self._atvv_closing = False
        self._atvv_session_id = None
        if self._atvv_task is not None and not self._atvv_task.done():
            self._atvv_task.cancel()
        try:
            self._loop.remove_reader(fd)
        except Exception:  # noqa: BLE001
            pass
        try:
            os.close(fd)
        except OSError:
            pass
        _LOGGER.info("Detached %s remote %s (%s)", self.remote_type, self.mac, dev_path)
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
        if self.remote_type == REMOTE_TYPE_CHROMECAST:
            if report_id != CHROMECAST_BUTTON_REPORT_ID:
                return
            decoded = decode_button(report_id, payload, REMOTE_TYPE_CHROMECAST)
            if decoded == "voice" and self._atvv_ctl is not None:
                return  # ATVV status drives voice press/release.
        else:
            if report_id == self._audio_id:
                for cb in list(self._audio_cbs):
                    cb(payload)
                return
            if not is_button_report(report_id):
                return
            decoded = decode_button(report_id, payload, REMOTE_TYPE_AMAZON)
        if not decoded:
            return
        if decoded == "release":
            if self._last_button is None:
                return
            key = self._last_button
            self._last_button = None
            self._emit_button(key, False)
            return
        self._last_button = decoded
        self._emit_button(decoded, True)

    def _emit_button(self, key: str, pressed: bool) -> None:
        event_str = f"{'press' if pressed else 'release'}:{key}"
        for cb in list(self._button_cbs):
            cb(event_str, key, pressed)

    async def _stop_atvv(self) -> None:
        task = self._atvv_task
        self._atvv_task = None
        self._atvv_ctl = None
        self._atvv_voice_active = False
        self._atvv_session_id = None
        self._atvv_closing = False
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _atvv_write(self, payload: bytes) -> None:
        ctl = self._atvv_ctl
        if ctl is None:
            return
        try:
            from dbus_fast import Variant  # noqa: PLC0415

            await ctl.call_write_value(payload, {"type": Variant("s", "command")})
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("ATVV write %s failed for %s: %s", payload.hex(), self.mac, err)

    def _handle_atvv_status(self, data: bytes) -> None:
        if not data:
            return
        opcode = data[0]
        if (
            opcode == 0x04 and len(data) == 4 and data[2] == 2
            and ((data[1] == 3 and 1 <= data[3] <= 128)
                 or (data[1] == 0 and data[3] == 0))
        ):
            self._atvv_session_id = data[3]
            if data[1] == 3 and not self._atvv_voice_active:
                self._atvv_voice_active = True
                self._emit_button("voice", True)
        elif opcode in (0x08, 0x0C) and len(data) == 1 and not self._atvv_voice_active:
            self._atvv_voice_active = True
            self._emit_button("voice", True)
        elif opcode == 0x00 and len(data) == 2 and data[1] in (0, 2):
            self._atvv_session_id = None
            self._atvv_closing = False
            if self._atvv_voice_active:
                self._atvv_voice_active = False
                self._emit_button("voice", False)
        elif opcode == 0x0B and len(data) >= 7:
            _LOGGER.debug("ATVV caps %s: %s", self.mac, data.hex())

    async def _run_atvv(self) -> None:
        """Keep BlueZ ATVV GATT notifications armed for Chromecast voice capture."""
        from dbus_fast import BusType, DBusError, Variant  # noqa: PLC0415
        from dbus_fast.aio import MessageBus  # noqa: PLC0415

        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        notify_chrs: list[Any] = []
        subscribed_paths: set[str] = set()
        rearm_event = asyncio.Event()
        dev_suffix = "/dev_" + self.mac.replace(":", "_")

        def on_atvv_notify(role: str) -> Callable[[str, dict[str, Any], list[str]], None]:
            def _cb(interface_name: str, changed: dict[str, Any], _invalidated: list[str]) -> None:
                if interface_name != "org.bluez.GattCharacteristic1":
                    return
                val = changed.get("Value")
                if val is None:
                    return
                data = bytes(val.value)
                if role == "audio":
                    for cb in list(self._audio_cbs):
                        cb(data)
                    return
                self._handle_atvv_status(data)
            return _cb

        async def disarm() -> None:
            self._atvv_ctl = None
            self._atvv_session_id = None
            self._atvv_closing = False
            if self._atvv_voice_active:
                self._atvv_voice_active = False
                self._emit_button("voice", False)
            while notify_chrs:
                ch = notify_chrs.pop()
                try:
                    await ch.call_stop_notify()
                except DBusError:
                    pass

        async def arm(dev_path: str, objects: dict[str, Any]) -> bool:
            chars: dict[str, str] = {}
            prefix = dev_path + "/"
            for path, ifaces in objects.items():
                if not path.startswith(prefix):
                    continue
                raw_uuid = ifaces.get("org.bluez.GattCharacteristic1", {}).get("UUID")
                uuid = raw_uuid.value if hasattr(raw_uuid, "value") else raw_uuid
                if uuid in (UUID_ATVV_CTL, UUID_ATVV_AUDIO, UUID_ATVV_STATUS):
                    chars[uuid] = path
            if len(chars) < 3:
                return False
            await disarm()
            for uuid, role in ((UUID_ATVV_AUDIO, "audio"), (UUID_ATVV_STATUS, "status")):
                char_path = chars[uuid]
                obj = bus.get_proxy_object(
                    "org.bluez", char_path, await bus.introspect("org.bluez", char_path)
                )
                if char_path not in subscribed_paths:
                    # on_properties_changed installs the D-Bus AddMatch rule once per path.
                    obj.get_interface("org.freedesktop.DBus.Properties").on_properties_changed(
                        on_atvv_notify(role)
                    )
                    subscribed_paths.add(char_path)
                ch = obj.get_interface("org.bluez.GattCharacteristic1")
                await ch.call_start_notify()
                notify_chrs.append(ch)
            ctl_obj = bus.get_proxy_object(
                "org.bluez",
                chars[UUID_ATVV_CTL],
                await bus.introspect("org.bluez", chars[UUID_ATVV_CTL]),
            )
            self._atvv_ctl = ctl_obj.get_interface("org.bluez.GattCharacteristic1")
            await self._atvv_ctl.call_write_value(
                ATVV_GET_CAPS, {"type": Variant("s", "command")}
            )
            _LOGGER.info("Armed Chromecast ATVV voice on %s", dev_path)
            return True

        def on_device_changed(
            interface_name: str, changed: dict[str, Any], _invalidated: list[str]
        ) -> None:
            if interface_name != "org.bluez.Device1":
                return
            conn = changed.get("Connected")
            resolved = changed.get("ServicesResolved")
            if conn is not None and not conn.value:
                self._atvv_ctl = None
                self._atvv_session_id = None
                self._atvv_closing = False
                if self._atvv_voice_active:
                    self._atvv_voice_active = False
                    self._emit_button("voice", False)
            elif (conn is not None and conn.value) or (
                resolved is not None and resolved.value
            ):
                rearm_event.set()

        try:
            root = bus.get_proxy_object("org.bluez", "/", await bus.introspect("org.bluez", "/"))
            mgr = root.get_interface("org.freedesktop.DBus.ObjectManager")
            dev_path: str | None = None
            watching_device = False
            while not self._stopped and self._fd is not None:
                rearm_event.clear()
                objects = await mgr.call_get_managed_objects()
                if dev_path is None:
                    dev_path = next(
                        (p for p in objects if p.endswith(dev_suffix) and "org.bluez.Device1" in objects[p]),
                        None,
                    )
                    if dev_path is None:
                        await asyncio.sleep(1.0)
                        continue
                if not watching_device:
                    dev_obj = bus.get_proxy_object(
                        "org.bluez", dev_path, await bus.introspect("org.bluez", dev_path)
                    )
                    dev_obj.get_interface("org.freedesktop.DBus.Properties").on_properties_changed(
                        on_device_changed
                    )
                    watching_device = True
                props = objects.get(dev_path, {}).get("org.bluez.Device1", {})
                connected = bool(props.get("Connected") and props["Connected"].value)
                resolved = bool(props.get("ServicesResolved") and props["ServicesResolved"].value)
                if connected and resolved and self._atvv_ctl is None:
                    try:
                        armed = await arm(dev_path, objects)
                    except DBusError as err:
                        _LOGGER.debug("ATVV arm failed for %s: %s", self.mac, err)
                        await asyncio.sleep(2.0)
                        continue
                    if not armed:
                        await asyncio.sleep(1.0)
                        continue
                await rearm_event.wait()
                await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Chromecast ATVV loop stopped for %s: %s", self.mac, err)
        finally:
            await disarm()
            bus.disconnect()
