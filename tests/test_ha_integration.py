"""Self-checks for custom_components/bluetooth_tv_remotes protocol & HID client."""

from __future__ import annotations

import ctypes
import ctypes.util
import importlib.util
import json
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMP = ROOT / "custom_components" / "bluetooth_tv_remotes"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


proto = _load_module("bluetooth_tv_remotes_protocol", COMP / "protocol.py")


def test_manifest_and_translations() -> None:
    manifest = json.loads((COMP / "manifest.json").read_text())
    assert manifest["domain"] == "bluetooth_tv_remotes"
    assert manifest["name"] == "Bluetooth TV Remotes"
    assert manifest["connectivity"] == ["bluetooth"]
    assert manifest["integration_type"] == "device"
    assert manifest["config_flow"] is True
    assert "assist_satellite" in manifest["dependencies"]
    for rel in ("strings.json", "translations/en.json", "translations/es.json"):
        data = json.loads((COMP / rel).read_text())
        assert "config" in data


def test_mac_normalization() -> None:
    assert proto.normalize_mac("aa:bb:cc:dd:ee:ff") == "AA:BB:CC:DD:EE:FF"
    assert proto.normalize_mac("aa-bb-cc-dd-ee-ff") == "AA:BB:CC:DD:EE:FF"
    for bad in ("", "AA:BB:CC", "GG:BB:CC:DD:EE:FF"):
        try:
            proto.normalize_mac(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Expected ValueError for {bad!r}")


def test_remote_type_detection() -> None:
    assert proto.detect_remote_type("0005:00000171:0000042F") == "amazon"
    assert proto.detect_remote_type("0005:000018D1:00009450") == "chromecast"
    assert proto.detect_remote_type("0005:00001234:00005678") is None


def test_button_decoding() -> None:
    # Amazon Fire TV remote
    assert proto.decode_button(0x01, bytes([0x52, 0, 0])) == "up"
    assert proto.decode_button(0x01, bytes([0, 0, 0])) == "release"
    assert proto.decode_button(0x02, bytes([0x21, 0x02, 0, 0])) == "voice"
    assert proto.decode_button(0x02, bytes([0x23, 0x02, 0, 0])) == "home"
    assert proto.decode_button(0x02, bytes([0x00, 0x00])) == "release"
    assert proto.decode_button(0xEF, bytes([0xA1, 0, 0])) == "prime_video"
    assert proto.decode_button(0xEF, bytes([0xA4, 0, 0])) == "app_4"
    assert proto.decode_button(0xEF, bytes([0x99])) == "unknown_ef_0099"
    assert proto.decode_button(0xF0, bytes([0xB8] * 80)) is None
    assert proto.decode_button(0x02, bytes([0x21])) == "invalid"

    # Chromecast Remote (18D1:9450)
    assert proto.decode_button(0x01, bytes([0x01, 0, 0, 0, 0, 0, 0, 0]), "chromecast") == "power"
    assert proto.decode_button(0x01, bytes([0x06, 0, 0, 0, 0, 0, 0, 0]), "chromecast") == "right"
    assert proto.decode_button(0x01, bytes([0x0C, 0, 0, 0, 0, 0, 0, 0]), "chromecast") == "volume_up"
    assert proto.decode_button(0x01, bytes([0x0E, 0, 0, 0, 0, 0, 0, 0]), "chromecast") == "youtube"
    assert proto.decode_button(0x01, bytes([0x0F, 0, 0, 0, 0, 0, 0, 0]), "chromecast") == "netflix"
    assert proto.decode_button(0x01, bytes([0x11, 0, 0, 0, 0, 0, 0, 0]), "chromecast") == "tv"
    assert proto.decode_button(0x01, bytes([0x00, 0, 0, 0, 0, 0, 0, 0]), "chromecast") == "release"
    assert proto.decode_button(0x01, bytes([0xFF, 0, 0, 0, 0, 0, 0, 0]), "chromecast") == "unknown_01_00ff"
    assert proto.decode_button(0x01, bytes([0x00, 0, 1]), "chromecast") == "invalid"
    assert proto.decode_button(0x02, bytes([0x01, 0]), "chromecast") is None


def test_report_descriptor_parsing() -> None:
    # Synthetic descriptor matching real AR remote (F0=80B in, F2=1B out, F3=10B out)
    desc = bytes([
        0x85, 0x01, 0x75, 0x08, 0x95, 0x03, 0x81, 0x00,
        0x85, 0xF0, 0x75, 0x08, 0x95, 0x50, 0x81, 0x02,
        0x85, 0xF2, 0x75, 0x08, 0x95, 0x01, 0x91, 0x02,
        0x85, 0xF3, 0x75, 0x08, 0x95, 0x0A, 0x91, 0x02,
    ])
    assert proto.parse_report_ids(desc) == (0xF0, 0xF2)
    # Also verify archived real 149-byte descriptor if present locally
    real_map = ROOT / "diagnostics" / "remote-hid-map-1.bin"
    if real_map.exists():
        assert proto.parse_report_ids(real_map.read_bytes()) == (0xF0, 0xF2)


def test_opus_decoder_roundtrip() -> None:
    lib_name = ctypes.util.find_library("opus")
    if not lib_name:
        return
    lib = ctypes.CDLL(lib_name)
    lib.opus_encoder_create.restype = ctypes.c_void_p
    lib.opus_encode.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_int
    ]
    lib.opus_encoder_destroy.argtypes = [ctypes.c_void_p]
    err = ctypes.c_int(0)
    enc = lib.opus_encoder_create(16000, 1, 2048, ctypes.byref(err))
    pcm_in = (ctypes.c_int16 * 320)(*([1200, -1200] * 160))
    pkt = (ctypes.c_ubyte * 128)()
    n = lib.opus_encode(enc, pcm_in, 320, pkt, 128)
    lib.opus_encoder_destroy(enc)

    dec = proto.OpusDecoder()
    try:
        dec.reset()
        pcm_out = dec.decode(bytes(pkt[:n]))
        assert len(pcm_out) == 640  # 320 samples * 2 bytes
        assert any(pcm_out)
    finally:
        dec.close()


def test_ima_adpcm_decoder() -> None:
    dec = proto.ImaAdpcmDecoder()
    pcm = dec.decode(bytes([0x00] * 160))
    assert len(pcm) == 640  # 160 bytes -> 320 samples -> 640 s16le bytes
    dec.reset()
    pcm_sig = dec.decode(bytes([0x7F, 0x7F, 0x00, 0x00] * 40))
    assert len(pcm_sig) == 640
    assert any(pcm_sig)
    dec.reset()
    vector = bytes.fromhex('77777748')
    assert struct.unpack('<8h', dec.decode(vector)) == (
        11, 41, 104, 240, 533, 1164, 1978, 1869
    )
    dec.reset()
    whole = dec.decode(vector * 40)
    dec.reset()
    assert b''.join(dec.decode(bytes((b,))) for b in vector * 40) == whole
    dec.reset()
    assert dec.decode(vector * 40) == whole
    dec.close()


if __name__ == "__main__":
    test_manifest_and_translations()
    test_mac_normalization()
    test_remote_type_detection()
    test_button_decoding()
    test_report_descriptor_parsing()
    test_opus_decoder_roundtrip()
    test_ima_adpcm_decoder()
    print("OK: all bluetooth_tv_remotes HA integration checks passed")
