"""Amazon Fire TV remote HID protocol, button decoding, and libopus wrapper."""

from __future__ import annotations

import ctypes
import ctypes.util
import glob
import os
from pathlib import Path
import re

SYS_HIDRAW = "/sys/class/hidraw"
DEFAULT_AUDIO_REPORT_ID = 0xF0
DEFAULT_ENABLE_REPORT_ID = 0xF2

BUTTON_REPORT_IDS = frozenset((0x01, 0x02, 0xEF))

_KEYBOARD_BUTTONS: dict[int, str] = {
    0x4F: "right",
    0x50: "left",
    0x51: "down",
    0x52: "up",
    0x28: "select",
    0x58: "select",
    0x29: "back",
    0xF1: "back",
    0x66: "power",
}

_CONSUMER_BUTTONS: dict[int, str] = {
    0x0221: "voice",
    0x0223: "home",
    0x0040: "menu",
    0x00E2: "mute",
    0x00E9: "volume_up",
    0x00EA: "volume_down",
    0x00CD: "play_pause",
    0x00B4: "rewind",
    0x00B3: "fast_forward",
    0x008D: "tv",
    0x0030: "power",
    0x0066: "power",
    0x0224: "back",
}

_VENDOR_BUTTONS: dict[int, str] = {
    0xA1: "prime_video",
    0xA2: "netflix",
    0xA3: "disney_plus",
    0xA4: "app_4",
}

_MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}$")

OPUS_SAMPLE_RATE = 16000
OPUS_CHANNELS = 1
OPUS_MAX_FRAME_SAMPLES = 960  # Up to 60 ms at 16 kHz (remote sends 20 ms = 320)
_OPUS_RESET_STATE = 4028


def normalize_mac(mac: str) -> str:
    """Validate and normalize a Bluetooth MAC address to AA:BB:CC:DD:EE:FF."""
    cleaned = mac.strip().replace("-", ":").upper()
    if not _MAC_RE.match(cleaned):
        raise ValueError(f"Invalid MAC address: {mac!r}")
    return cleaned


def is_button_report(report_id: int) -> bool:
    """Return True if HID report ID carries button events."""
    return report_id in BUTTON_REPORT_IDS


def decode_button(report_id: int, payload: bytes) -> str | None:
    """Decode a button HID report payload (without leading report ID byte)."""
    if report_id not in BUTTON_REPORT_IDS:
        return None
    min_len = 2 if report_id == 0x02 else 1
    if len(payload) < min_len:
        return "invalid"
    if all(b == 0 for b in payload):
        return "release"
    if report_id == 0x01:
        code = payload[0]
        return _KEYBOARD_BUTTONS.get(code, f"unknown_01_{code:04x}")
    if report_id == 0x02:
        code = int.from_bytes(payload[:2], "little")
        return _CONSUMER_BUTTONS.get(code, f"unknown_02_{code:04x}")
    code = payload[0]
    return _VENDOR_BUTTONS.get(code, f"unknown_ef_{code:04x}")


def parse_report_ids(descriptor: bytes) -> tuple[int, int]:
    """Parse HID report descriptor to find (audio_input_id, mic_enable_output_id)."""
    reports: dict[int, dict[str, int]] = {}
    rid = rsize = rcount = 0
    i = 0
    n = len(descriptor)
    while i < n:
        b = descriptor[i]
        i += 1
        if b == 0xFE:
            if i < n:
                i += 2 + descriptor[i]
            continue
        tag, typ, sz = b >> 4, (b >> 2) & 3, b & 3
        nb = (0, 1, 2, 4)[sz]
        if i + nb > n:
            break
        data = int.from_bytes(descriptor[i : i + nb], "little") if nb else 0
        i += nb
        if typ == 1:
            if tag == 0x7:
                rsize = data
            elif tag == 0x9:
                rcount = data
            elif tag == 0x8:
                rid = data
        elif typ == 0 and tag in (0x8, 0x9):
            direction = "input" if tag == 0x8 else "output"
            reports.setdefault(rid, {"input": 0, "output": 0})[direction] += (
                rcount * rsize // 8
            )

    inputs = {r: v["input"] for r, v in reports.items() if v["input"]}
    outputs = {r: v["output"] for r, v in reports.items() if v["output"]}
    audio_id = max(inputs, key=inputs.__getitem__) if inputs else DEFAULT_AUDIO_REPORT_ID
    enable_id = min(outputs, key=outputs.__getitem__) if outputs else DEFAULT_ENABLE_REPORT_ID
    return audio_id, enable_id


def discover_hidraw_devices(sys_hidraw: str = SYS_HIDRAW) -> dict[str, dict[str, str]]:
    """Return connected Bluetooth HID devices indexed by normalized MAC."""
    found: dict[str, dict[str, str]] = {}
    for entry in sorted(glob.glob(f"{sys_hidraw}/hidraw*")):
        uevent_path = Path(entry, "device", "uevent")
        try:
            text = uevent_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        fields: dict[str, str] = {}
        for line in text.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                fields[k.strip()] = v.strip()
        raw_mac = fields.get("HID_UNIQ", "")
        try:
            mac = normalize_mac(raw_mac)
        except ValueError:
            continue
        found[mac] = {
            "dev": f"/dev/{os.path.basename(entry)}",
            "sys_dir": entry,
            "name": fields.get("HID_NAME", "HID Remote"),
            "hid_id": fields.get("HID_ID", ""),
        }
    return found


def _load_libopus() -> ctypes.CDLL:
    # ponytail: ctypes + system libopus avoids pip/C-build deps in HA Core.
    try:
        lib = ctypes.CDLL("libopus.so.0")
    except OSError:
        lib = ctypes.CDLL(ctypes.util.find_library("opus") or "libopus.so")
    lib.opus_decoder_create.argtypes = [
        ctypes.c_int32,
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_int),
    ]
    lib.opus_decoder_create.restype = ctypes.c_void_p
    lib.opus_decode.argtypes = [
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.c_int32,
        ctypes.POINTER(ctypes.c_int16),
        ctypes.c_int,
        ctypes.c_int,
    ]
    lib.opus_decode.restype = ctypes.c_int
    lib.opus_decoder_ctl.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.opus_decoder_ctl.restype = ctypes.c_int
    lib.opus_decoder_destroy.argtypes = [ctypes.c_void_p]
    lib.opus_decoder_destroy.restype = None
    return lib


class OpusDecoder:
    """Thin 16 kHz mono libopus decoder producing s16le PCM bytes."""

    def __init__(self) -> None:
        self._lib = _load_libopus()
        err = ctypes.c_int(0)
        self._dec = self._lib.opus_decoder_create(
            OPUS_SAMPLE_RATE, OPUS_CHANNELS, ctypes.byref(err)
        )
        if not self._dec or err.value != 0:
            raise RuntimeError(f"opus_decoder_create failed: {err.value}")
        self._buf = (ctypes.c_int16 * OPUS_MAX_FRAME_SAMPLES)()

    def reset(self) -> None:
        if self._dec:
            self._lib.opus_decoder_ctl(self._dec, _OPUS_RESET_STATE)

    def decode(self, packet: bytes) -> bytes:
        if not self._dec or not packet:
            return b""
        samples = self._lib.opus_decode(
            self._dec, packet, len(packet), self._buf, OPUS_MAX_FRAME_SAMPLES, 0
        )
        if samples <= 0:
            return b""
        return ctypes.string_at(self._buf, samples * 2)

    def close(self) -> None:
        if self._dec:
            self._lib.opus_decoder_destroy(self._dec)
            self._dec = None
