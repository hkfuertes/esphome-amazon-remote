"""Bluetooth TV remote HID protocols, button decoding, and audio decoders."""

from __future__ import annotations

import ctypes
import ctypes.util
import glob
import os
from pathlib import Path
import re
import struct

SYS_HIDRAW = "/sys/class/hidraw"
DEFAULT_AUDIO_REPORT_ID = 0xF0
DEFAULT_ENABLE_REPORT_ID = 0xF2

BUTTON_REPORT_IDS = frozenset((0x01, 0x02, 0xEF))
CHROMECAST_BUTTON_REPORT_ID = 0x01

# Google ATVV GATT UUIDs and opcodes used by Chromecast with Google TV remote.
UUID_ATVV_CTL = "ab5e0002-5a21-4f05-bc7d-af01f617b664"
UUID_ATVV_AUDIO = "ab5e0003-5a21-4f05-bc7d-af01f617b664"
UUID_ATVV_STATUS = "ab5e0004-5a21-4f05-bc7d-af01f617b664"
ATVV_GET_CAPS = bytes((0x0A, 1, 0, 0, 2, 3))
ATVV_MIC_OPEN_16K = bytes((0x0C, 0x00, 0x02))
ATVV_MIC_CLOSE = bytes((0x0D,))

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

# Chromecast Remote (18D1:9450) report 01 is a 2-slot array indexed 1..17 into
# the descriptor's consumer usage list (not little-endian usage IDs).
_CHROMECAST_BUTTONS: tuple[str, ...] = (
    "power",        # 0x01: usage 0x019E
    "play_pause",   # 0x02: usage 0x00CD
    "up",           # 0x03: usage 0x0042
    "down",         # 0x04: usage 0x0043
    "left",         # 0x05: usage 0x0044
    "right",        # 0x06: usage 0x0045
    "select",       # 0x07: usage 0x0041
    "mute",         # 0x08: usage 0x00E2
    "voice",        # 0x09: usage 0x0221
    "home",         # 0x0A: usage 0x0223
    "back",         # 0x0B: usage 0x0224
    "volume_up",    # 0x0C: usage 0x00E9
    "volume_down",  # 0x0D: usage 0x00EA
    "youtube",      # 0x0E: usage 0x0077
    "netflix",      # 0x0F: usage 0x0078
    "app_3",        # 0x10: usage 0x0079
    "tv",           # 0x11: usage 0x0089
)

ALL_BUTTONS: frozenset[str] = frozenset(
    (*_KEYBOARD_BUTTONS.values(), *_CONSUMER_BUTTONS.values(), *_VENDOR_BUTTONS.values(), *_CHROMECAST_BUTTONS)
)

_MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}$")

OPUS_SAMPLE_RATE = 16000
OPUS_CHANNELS = 1
OPUS_MAX_FRAME_SAMPLES = 960  # Up to 60 ms at 16 kHz (remote sends 20 ms = 320)
_OPUS_RESET_STATE = 4028

_ADPCM_STEP_TABLE = (
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45,
    50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143, 157, 173, 190, 209, 230,
    253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724, 796, 876, 963,
    1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327,
    3660, 4026, 4428, 4871, 5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487,
    12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767,
)
_ADPCM_INDEX_TABLE = (-1, -1, -1, -1, 2, 4, 6, 8)


def detect_remote_type(hid_id: str) -> str | None:
    """Identify supported remote type from sysfs HID_ID (e.g. 0005:000018D1:00009450)."""
    upper = hid_id.upper()
    if "000018D1:00009450" in upper:
        return "chromecast"
    if "00000171:" in upper:
        return "amazon"
    return None


def normalize_mac(mac: str) -> str:
    """Validate and normalize a Bluetooth MAC address to AA:BB:CC:DD:EE:FF."""
    cleaned = mac.strip().replace("-", ":").upper()
    if not _MAC_RE.match(cleaned):
        raise ValueError(f"Invalid MAC address: {mac!r}")
    return cleaned


def is_button_report(report_id: int) -> bool:
    """Return True if HID report ID carries button events."""
    return report_id in BUTTON_REPORT_IDS


def decode_button(report_id: int, payload: bytes, remote_type: str = "amazon") -> str | None:
    """Decode a button HID report payload (without leading report ID byte)."""
    if remote_type == "chromecast":
        if report_id != CHROMECAST_BUTTON_REPORT_ID:
            return None
        if len(payload) < 2 or len(payload) > 8 or any(payload[2:]):
            return "invalid"
        if payload[0] == 0 and payload[1] == 0:
            return "release"
        code = payload[0] or payload[1]
        if 1 <= code <= len(_CHROMECAST_BUTTONS):
            return _CHROMECAST_BUTTONS[code - 1]
        return f"unknown_01_{code:04x}"
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


class ImaAdpcmDecoder:
    """Continuous 16 kHz mono IMA/DVI ADPCM decoder producing s16le PCM bytes."""

    def __init__(self) -> None:
        self._predictor = 0
        self._step = 0

    def reset(self) -> None:
        self._predictor = 0
        self._step = 0

    def decode(self, packet: bytes) -> bytes:
        if not packet:
            return b""
        pred = self._predictor
        step_idx = self._step
        out: list[int] = []
        for byte in packet:
            for nibble in (byte >> 4, byte & 0xF):
                step = _ADPCM_STEP_TABLE[step_idx]
                diff = step >> 3
                if nibble & 1:
                    diff += step >> 2
                if nibble & 2:
                    diff += step >> 1
                if nibble & 4:
                    diff += step
                pred += -diff if nibble & 8 else diff
                if pred > 32767:
                    pred = 32767
                elif pred < -32768:
                    pred = -32768
                step_idx += _ADPCM_INDEX_TABLE[nibble & 7]
                if step_idx < 0:
                    step_idx = 0
                elif step_idx > 88:
                    step_idx = 88
                out.append(pred)
        self._predictor = pred
        self._step = step_idx
        return struct.pack(f"<{len(out)}h", *out)

    def close(self) -> None:
        pass
