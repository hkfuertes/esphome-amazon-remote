#!/usr/bin/env python3
"""Chromecast Remote pairing + ATVV voice probe, run inside HA Core container.

Run via stdin (no files installed):
  docker exec -i homeassistant python3 - < tools/probe_ha_chromecast_voice.py

Pairs with a target-only NoInputNoOutput agent if needed (bond persists),
then exercises the documented ATVV opcodes (GET_CAPS, MIC_OPEN, MIC_CLOSE)
on service ab5e0001 and captures IMA ADPCM 16 kHz audio to /tmp.
"""
import asyncio
import json
import struct
import time
import wave

MAC = '47:54:51:27:9F:5B'
UUID_CTL = 'ab5e0002-5a21-4f05-bc7d-af01f617b664'
UUID_AUDIO = 'ab5e0003-5a21-4f05-bc7d-af01f617b664'
UUID_STATUS = 'ab5e0004-5a21-4f05-bc7d-af01f617b664'
GET_CAPS = bytes((0x0A, 1, 0, 0, 2, 3))
MIC_OPEN_16K = bytes((0x0C, 0x00, 0x02))
MIC_CLOSE = bytes((0x0D,))

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


async def main(pair_seconds=60, voice_seconds=25):
    from dbus_fast import BusType, DBusError, MessageType, Variant
    from dbus_fast.aio import MessageBus
    from dbus_fast.service import ServiceInterface, method

    target = '/org/bluez/hci0/dev_' + MAC.replace(':', '_')
    agent_path = '/org/bluetooth_tv_remotes/ha_voice_agent'

    class Agent(ServiceInterface):
        def __init__(self):
            super().__init__('org.bluez.Agent1')

        def check(self, device):
            if device != target:
                raise DBusError('org.bluez.Error.Rejected', 'Not the diagnostic target')

        @method()
        def Release(self):
            pass

        @method()
        def Cancel(self):
            pass

        @method()
        def RequestConfirmation(self, device: 'o', passkey: 'u'):
            self.check(device)

        @method()
        def RequestAuthorization(self, device: 'o'):
            self.check(device)

        @method()
        def AuthorizeService(self, device: 'o', uuid: 's'):
            self.check(device)

        @method()
        def RequestPinCode(self, device: 'o') -> 's':
            raise DBusError('org.bluez.Error.Rejected', 'No keyboard')

        @method()
        def RequestPasskey(self, device: 'o') -> 'u':
            raise DBusError('org.bluez.Error.Rejected', 'No keyboard')

    def emit(event, **fields):
        print(json.dumps({'time': round(time.time(), 3), 'event': event, **fields}), flush=True)

    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

    async def interface(path, name):
        obj = bus.get_proxy_object('org.bluez', path, await bus.introspect('org.bluez', path))
        return obj.get_interface(name)

    manager = await interface('/', 'org.freedesktop.DBus.ObjectManager')
    agents = await interface('/org/bluez', 'org.bluez.AgentManager1')
    adapter = await interface('/org/bluez/hci0', 'org.bluez.Adapter1')
    device = None
    registered = discovery = False
    chars = {}
    audio = bytearray()
    pcm = []
    events = []
    decoder = ImaAdpcm16k()
    mic_open_sent = False
    voice_started = asyncio.Event()
    voice_stopped = asyncio.Event()

    def make_on_changed(uuid):
        def on_changed(interface_name, changed, _invalidated):
            nonlocal mic_open_sent
            if interface_name != 'org.bluez.GattCharacteristic1':
                return
            value = changed.get('Value')
            if value is None:
                return
            data = bytes(value.value)
            events.append({'time': round(time.time(), 3), 'direction': 'in', 'uuid': uuid[:8], 'hex': data.hex()})
            if uuid == UUID_STATUS:
                emit('status', hex=data.hex(' '))
                opcode = data[0] if data else None
                if opcode == 0x0B and len(data) >= 7:
                    emit('caps', version=f'{data[1]}.{data[2]}', codecs=f'0x{data[3]:02x}{data[4]:02x}',
                         frame_size=int.from_bytes(data[5:7], 'big'))
                elif opcode in (0x08, 0x0C) and not mic_open_sent:
                    mic_open_sent = True
                    voice_started.set()
                elif opcode == 0x00 and mic_open_sent:
                    voice_stopped.set()
            elif uuid == UUID_AUDIO:
                audio.extend(data)
                pcm.extend(decoder.feed(data))
                if len(audio) % 3200 == 0:
                    emit('audio', bytes=len(audio))
        return on_changed

    def device_props(objects):
        props = objects.get(target, {}).get('org.bluez.Device1', {})
        return {k: v.value for k, v in props.items()}

    notify_chrs = []
    try:
        objects = await manager.call_get_managed_objects()
        props = device_props(objects)
        if props.get('Paired'):
            emit('already_paired')
        else:
            bus.export(agent_path, Agent())
            await agents.call_register_agent(agent_path, 'NoInputNoOutput')
            registered = True
            await adapter.call_set_discovery_filter({'Transport': Variant('s', 'le')})
            await adapter.call_start_discovery()
            discovery = True
            emit('pairing_window', seconds=pair_seconds,
                 hint='hold the remote pairing combo (Back+Home) now')
            deadline = time.monotonic() + pair_seconds
            last = None
            while time.monotonic() < deadline:
                objects = await manager.call_get_managed_objects()
                props = device_props(objects)
                if not props:
                    await asyncio.sleep(.5)
                    continue
                flags = list(props.get('AdvertisingFlags', []))
                pairing_flags = any(f & 0x01 for f in flags if isinstance(f, int))
                observation = (props.get('RSSI'), tuple(flags), pairing_flags)
                if observation != last:
                    emit('advertisement', rssi=props.get('RSSI'), flags=[hex(f) for f in flags if isinstance(f, int)])
                    last = observation
                if pairing_flags:
                    device = await interface(target, 'org.bluez.Device1')
                    try:
                        await asyncio.wait_for(device.call_pair(), 25)
                        emit('pair_success')
                        break
                    except asyncio.TimeoutError:
                        emit('pair_timeout_retry')
                    except DBusError as exc:
                        emit('pair_error', error=str(exc))
                        await asyncio.sleep(1)
                await asyncio.sleep(.5)
            objects = await manager.call_get_managed_objects()
            props = device_props(objects)
            if not props.get('Paired'):
                emit('pair_failed')
                return
        device = device or await interface(target, 'org.bluez.Device1')
        device_obj = bus.get_proxy_object('org.bluez', target, await bus.introspect('org.bluez', target))
        try:
            await device_obj.get_interface('org.freedesktop.DBus.Properties').call_set(
                'org.bluez.Device1', 'Trusted', Variant('b', True))
        except DBusError as exc:
            emit('trust_error', error=str(exc))
        if not props.get('Connected'):
            await asyncio.wait_for(device.call_connect(), 20)
            emit('connected')
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            objects = await manager.call_get_managed_objects()
            props = device_props(objects)
            if props.get('ServicesResolved'):
                break
            await asyncio.sleep(.5)
        emit('state', **{k: props.get(k) for k in
                        ('Paired', 'Bonded', 'Connected', 'ServicesResolved')})

        # Locate the ATVV characteristics.
        objects = await manager.call_get_managed_objects()
        for path, interfaces in objects.items():
            if not path.startswith(target + '/'):
                continue
            uuid = interfaces.get('org.bluez.GattCharacteristic1', {}).get('UUID')
            uuid = uuid.value if hasattr(uuid, 'value') else uuid
            if uuid and uuid in (UUID_CTL, UUID_AUDIO, UUID_STATUS):
                chars[uuid] = path
        if set(chars) != {UUID_CTL, UUID_AUDIO, UUID_STATUS}:
            emit('missing_characteristics', found=sorted(u.split('-')[0] for u in chars))
            return
        ctl = await interface(chars[UUID_CTL], 'org.bluez.GattCharacteristic1')
        for uuid in (UUID_AUDIO, UUID_STATUS):
            obj = bus.get_proxy_object('org.bluez', chars[uuid], await bus.introspect('org.bluez', chars[uuid]))
            # on_properties_changed installs the D-Bus AddMatch rule; add_message_handler alone does not.
            obj.get_interface('org.freedesktop.DBus.Properties').on_properties_changed(make_on_changed(uuid))
            ch = obj.get_interface('org.bluez.GattCharacteristic1')
            await ch.call_start_notify()
            notify_chrs.append(ch)
        await ctl.call_write_value(GET_CAPS, {'type': Variant('s', 'command')})
        events.append({'time': round(time.time(), 3), 'direction': 'out', 'hex': GET_CAPS.hex()})
        emit('get_caps_sent', hint='now hold the voice button and speak')

        try:
            await asyncio.wait_for(voice_started.wait(), 20)
        except asyncio.TimeoutError:
            emit('no_voice_press')
            return
        # The remote requested the mic; grant it for 16 kHz ADPCM.
        await ctl.call_write_value(MIC_OPEN_16K, {'type': Variant('s', 'command')})
        events.append({'time': round(time.time(), 3), 'direction': 'out', 'hex': MIC_OPEN_16K.hex()})
        emit('mic_open_sent')
        try:
            await asyncio.wait_for(voice_stopped.wait(), min(voice_seconds, 25))
            emit('audio_stop_received')
        except asyncio.TimeoutError:
            await ctl.call_write_value(MIC_CLOSE, {'type': Variant('s', 'command')})
            events.append({'time': round(time.time(), 3), 'direction': 'out', 'hex': MIC_CLOSE.hex()})
            emit('mic_close_sent')
        peak = max((abs(s) for s in pcm), default=0)
        rms = int((sum(s * s for s in pcm) / len(pcm)) ** 0.5) if pcm else 0
        emit('capture', audio_bytes=len(audio), samples=len(pcm), seconds=round(len(pcm) / 16000, 2),
             peak=peak, rms=rms)
        if pcm:
            with wave.open('/tmp/chromecast-voice-ha.wav', 'wb') as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(16000)
                wav.writeframes(struct.pack(f'<{len(pcm)}h', *pcm))
            with open('/tmp/chromecast-voice-ha.json', 'w') as fh:
                json.dump(events, fh, indent=2)
            emit('wrote', wav='/tmp/chromecast-voice-ha.wav', samples=len(pcm))
    finally:
        for ch in notify_chrs:
            try:
                await ch.call_stop_notify()
            except DBusError:
                pass
        if discovery:
            try:
                await adapter.call_stop_discovery()
            except DBusError:
                pass
        if registered:
            try:
                await agents.call_unregister_agent(agent_path)
            except DBusError:
                pass
        bus.disconnect()
        emit('cleanup_done')


if __name__ == '__main__':
    import sys
    if '--self-test' in sys.argv:
        decoder = ImaAdpcm16k()
        silence = decoder.feed(b'\x00' * 64)
        assert all(-32768 <= s <= 32767 for s in silence) and len(silence) == 128
        assert len(STEP_TABLE) == 89
        assert 0 <= decoder.step <= 88
        print('IMA ADPCM decoder checks passed')
    else:
        asyncio.run(main())
