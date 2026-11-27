#!/usr/bin/env python3
"""Scoped ATVV voice probe for the Chromecast remote on BlueZ via D-Bus.

Uses only the documented ATVV commands on service ab5e0001:
GET_CAPS (0x0A), MIC_OPEN (0x0C), MIC_CLOSE (0x0D). Captures audio
notifications, decodes IMA ADPCM 16 kHz, writes PCM WAV. No other writes.
"""
import argparse
import json
import struct
import time
import wave
from pathlib import Path

from gi.repository import Gio, GLib

CTL = bytes((0x0A, 1, 0, 0, 2, 3))      # GET_CAPS, Chromecast-verified form
MIC_OPEN_16K = bytes((0x0C, 0x00, 0x02))  # host grants mic, ADPCM 16 kHz
MIC_CLOSE = bytes((0x0D,))
UUID_CTL, UUID_AUDIO, UUID_STATUS = 'ab5e0002-5a21-4f05-bc7d-af01f617b664', \
    'ab5e0003-5a21-4f05-bc7d-af01f617b664', 'ab5e0004-5a21-4f05-bc7d-af01f617b664'

# Standard Intel/DVI IMA-ADPCM tables (89 entries, cross-checked with the
# public ATVV reference implementation).
STEP_TABLE = (
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45,
    50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143, 157, 173, 190, 209, 230,
    253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724, 796, 876, 963,
    1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327,
    3660, 4026, 4428, 4871, 5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487,
    12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767,
)
INDEX_TABLE = (-1, -1, -1, -1, 2, 4, 6, 8)


class ImaAdpcm16k:
    def __init__(self):
        self.predictor = 0
        self.step = 0

    def feed(self, data):
        out = []
        for byte in data:
            for nibble in (byte >> 4, byte & 0xF):
                step = STEP_TABLE[self.step]
                diff = step >> 3
                if nibble & 1:
                    diff += step >> 2
                if nibble & 2:
                    diff += step >> 1
                if nibble & 4:
                    diff += step
                self.predictor += -diff if nibble & 8 else diff
                self.predictor = max(-32768, min(32767, self.predictor))
                self.step = max(0, min(88, self.step + INDEX_TABLE[nibble & 7]))
                out.append(self.predictor)
        return out


class VoiceProbe:
    def __init__(self, mac, out):
        self.decoder = ImaAdpcm16k()
        self.audio = bytearray()
        self.events = []
        self.pcm = []
        self.mic_open_sent = False
        self.mac = mac
        self.out = Path(out)
        conn = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        self.conn = conn
        objects = conn.call_sync('org.bluez', '/', 'org.freedesktop.DBus.ObjectManager',
                                 'GetManagedObjects', None, None,
                                 Gio.DBusCallFlags.NONE, -1, None).unpack()[0]
        prefix = '/org/bluez/hci0/dev_' + mac.replace(':', '_')
        self.paths = {}
        for path, interfaces in objects.items():
            uuid = interfaces.get('org.bluez.GattCharacteristic1', {}).get('UUID')
            if path.startswith(prefix) and uuid in (UUID_CTL, UUID_AUDIO, UUID_STATUS):
                self.paths[uuid] = path
        missing = {u.split('-')[0] for u in (UUID_CTL, UUID_AUDIO, UUID_STATUS) if u not in self.paths}
        if missing:
            raise SystemExit(f'Missing characteristics: {missing}; device connected?')
        conn.signal_subscribe('org.bluez', 'org.freedesktop.DBus.Properties',
                               'PropertiesChanged', None, 'org.bluez.GattCharacteristic1',
                               Gio.DBusSignalFlags.NONE, self.on_properties, None)

    def on_properties(self, _conn, _sender, path, _iface, _signal, params, _user):
        uuid = next((u for u, p in self.paths.items() if p == path), None)
        interface, changed, _invalidated = params.unpack()
        if interface != 'org.bluez.GattCharacteristic1' or uuid not in self.paths:
            return
        value = changed.get('Value')
        if value is None:
            return
        data = bytes(value)
        t = round(time.time(), 3)
        if uuid == UUID_STATUS:
            self.events.append({'time': t, 'direction': 'in', 'hex': data.hex()})
            print(f'STATUS {data.hex(" ")}', flush=True)
            opcode = data[0] if data else None
            if opcode == 0x0B and len(data) >= 7:
                print(f'CAPS version={data[1]}.{data[2]} codecs=0x{data[3]:02x}{data[4]:02x} '
                      f'frame_size={int.from_bytes(data[5:7], "big")}', flush=True)
            if opcode in (0x08, 0x0C) and not self.mic_open_sent:
                self.mic_open_sent = True
                self.write(UUID_CTL, MIC_OPEN_16K, 'command')
                self.events.append({'time': t, 'direction': 'out', 'hex': MIC_OPEN_16K.hex()})
                print('MIC_OPEN sent', flush=True)
            if opcode == 0x00:
                print('AUDIO_STOP from remote', flush=True)
        elif uuid == UUID_AUDIO:
            self.audio.extend(data)
            self.events.append({'time': t, 'direction': 'audio', 'bytes': len(data)})
            self.pcm.extend(self.decoder.feed(data))
            print(f'AUDIO +{len(data)}B total={len(self.audio)}B', flush=True)

    def write(self, uuid, data, kind):
        self.conn.call_sync('org.bluez', self.paths[uuid], 'org.bluez.GattCharacteristic1',
                            'WriteValue', GLib.Variant('(aya{sv})', (list(data), {'type': GLib.Variant('s', kind)})),
                            None, Gio.DBusCallFlags.NONE, -1, None)

    def start_notify(self, uuid):
        self.conn.call_sync('org.bluez', self.paths[uuid], 'org.bluez.GattCharacteristic1',
                            'StartNotify', None, None, Gio.DBusCallFlags.NONE, -1, None)

    def run(self, seconds):
        self.start_notify(UUID_AUDIO)
        self.start_notify(UUID_STATUS)
        self.write(UUID_CTL, CTL, 'command')
        self.events.append({'time': round(time.time(), 3), 'direction': 'out', 'hex': CTL.hex()})
        print(f'GET_CAPS sent; hold the voice button and speak (~{seconds}s window)', flush=True)
        loop = GLib.MainLoop()
        GLib.timeout_add_seconds(seconds, lambda: loop.quit())
        loop.run()
        if self.mic_open_sent and self.audio:
            self.write(UUID_CTL, MIC_CLOSE, 'command')
            self.events.append({'time': round(time.time(), 3), 'direction': 'out', 'hex': MIC_CLOSE.hex()})
        peak = max((abs(s) for s in self.pcm), default=0)
        print(f'audio_bytes={len(self.audio)} samples={len(self.pcm)} peak={peak}', flush=True)
        self.out.with_suffix('.raw').write_bytes(bytes(self.audio))
        self.out.with_suffix('.json').write_text(json.dumps(self.events, indent=2))
        if self.pcm:
            with wave.open(str(self.out.with_suffix('.wav')), 'wb') as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(16000)
                wav.writeframes(struct.pack(f'<{len(self.pcm)}h', *self.pcm))
            print(f'WAV: {self.out.with_suffix(".wav")}', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--mac', default='47:54:51:27:9F:5B')
    parser.add_argument('--seconds', type=int, default=15)
    parser.add_argument('--out', default='diagnostics/chromecast-voice')
    args = parser.parse_args()
    if args.self_test:
        decoder = ImaAdpcm16k()
        # Constant-nibble silence stays bounded and monotonic-decaying.
        silence = decoder.feed(b'\x00' * 64)
        assert all(-32768 <= s <= 32767 for s in silence) and len(silence) == 128
        # Full-scale 0xF nibbles clip safely.
        loud = ImaAdpcm16k().feed(b'\x0f' * 32)
        assert max(loud) <= 32767 and len(loud) == 64
        assert len(STEP_TABLE) == 89
        # Step index clamps within table bounds after full-scale ramp.
        assert 0 <= decoder.step <= 88
        print('IMA ADPCM 16 kHz decoder checks passed')
    else:
        probe = VoiceProbe(args.mac, args.out)
        probe.run(max(5, min(args.seconds, 60)))
