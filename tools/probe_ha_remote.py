#!/usr/bin/env python3
"""Scoped BlueZ diagnostic, run inside HA Core. No extra packages or cache edits.

python3 probe_ha_remote.py --self-test
python3 probe_ha_remote.py --mac AA:BB:CC:DD:EE:FF --pair --monitor
Pairing may save a normal bond. No existing bond is removed or overwritten by
this script. Output deliberately excludes raw HCI/SMP packets and key material.
"""
import argparse
import asyncio
import ctypes
import errno
import hashlib
import json
from pathlib import Path
import re
import socket
import struct
import time


def emit(event, **fields):
    print(json.dumps({'time': round(time.time(), 3), 'event': event, **fields}), flush=True)


def advertising_flags(data):
    offset = 0
    while offset < len(data):
        size = data[offset]
        if not size or offset + size + 1 > len(data):
            break
        if size >= 2 and data[offset + 1] == 1:
            return data[offset + 2]
        offset += size + 1
    return None


def hci_events(packet, mac, handles):
    """Decode only target connection/security status, never key payloads."""
    if len(packet) < 6:
        return []
    opcode, index, size = struct.unpack_from('<HHH', packet)
    data = packet[6:]
    if len(data) != size:
        return []
    target = bytes.fromhex(mac.replace(':', ''))[::-1]
    if opcode == 2 and len(data) >= 3:  # HCI command
        command = int.from_bytes(data[:2], 'little')
        params = data[3:]
        offsets = {0x0405: (0, 'bredr'), 0x200D: (6, 'le'), 0x2043: (3, 'le')}
        if command in offsets:
            off, bearer = offsets[command]
            if params[off:off + 6] == target:
                return [{'event': 'connect_command', 'bearer': bearer, 'adapter_index': index}]
    if opcode == 3 and len(data) >= 2:  # HCI event
        event, length = data[:2]
        p = data[2:]
        if len(p) != length:
            return []
        if event == 0x3E and len(p) >= 2 and p[0] in (2, 0x0D):
            records = []
            offset = 2
            for _ in range(p[1]):
                extended = p[0] == 0x0D
                header = 24 if extended else 9
                if offset + header > len(p):
                    break
                address = p[offset + (3 if extended else 2):offset + (9 if extended else 8)]
                size = p[offset + header - 1]
                end = offset + header + size
                if end + (0 if extended else 1) > len(p):
                    break
                if address == target:
                    rssi = p[offset + 13] if extended else p[end]
                    records.append({'event': 'live_advertisement', 'flags': advertising_flags(p[offset + header:end]),
                                    'rssi': rssi - 256 if rssi > 127 else rssi})
                offset = end + (0 if extended else 1)
            return records
        if event == 0x03 and len(p) >= 11 and p[3:9] == target:
            handle = int.from_bytes(p[1:3], 'little')
            if p[0] == 0:
                handles.add(handle)
            return [{'event': 'connection_complete', 'bearer': 'bredr', 'status': p[0]}]
        if event == 0x3E and len(p) >= 12 and p[0] in (1, 0x0A, 0x29):
            if p[6:12] == target:
                handle = int.from_bytes(p[2:4], 'little')
                if p[1] == 0:
                    handles.add(handle)
                return [{'event': 'connection_complete', 'bearer': 'le', 'status': p[1]}]
            if p[1] != 0:
                return [{'event': 'unattributed_le_failure', 'status': p[1]}]
        if event in (0x05, 0x08, 0x59) and len(p) >= 4:
            handle = int.from_bytes(p[1:3], 'little')
            if handle in handles:
                if event == 0x05:
                    handles.discard(handle)
                    return [{'event': 'disconnected', 'status': p[0], 'reason': p[3]}]
                return [{'event': 'encryption_change', 'status': p[0], 'enabled': p[3]}]
    if opcode in (4, 5) and len(data) >= 9:  # ACL: only SMP opcode/status
        acl_handle = int.from_bytes(data[:2], 'little')
        if (acl_handle >> 12) & 3 == 1:  # Continuation has no L2CAP header.
            return []
        handle = acl_handle & 0xFFF
        cid = int.from_bytes(data[6:8], 'little')
        if handle in handles and cid == 4:
            payload = data[8:]
            result = {'event': 'att', 'direction': 'tx' if opcode == 4 else 'rx',
                      'opcode': payload[0], 'captured_bytes': len(payload),
                      'complete': len(payload) == int.from_bytes(data[4:6], 'little')}
            if payload[0] == 8 and len(payload) == 7:
                result.update(start=int.from_bytes(payload[1:3], 'little'),
                              end=int.from_bytes(payload[3:5], 'little'),
                              type_uuid=int.from_bytes(payload[5:7], 'little'))
            if payload[0] == 9 and len(payload) >= 2:
                size = payload[1]
                result.update(record_size=size, record_bytes=len(payload) - 2)
                if result['complete']:
                    result['uniform_length'] = bool(size and (len(payload) - 2) % size == 0)
            if payload[0] == 1 and len(payload) == 5:
                result.update(request_opcode=payload[1], error=payload[4])
            return [result]
        if handle in handles and cid == 6:
            result = {'event': 'smp', 'direction': 'tx' if opcode == 4 else 'rx', 'opcode': data[8]}
            if data[8] == 5 and len(data) >= 10:
                result['failure_reason'] = data[9]
            return [result]
    return []


def monitor_socket():
    sock = socket.socket(31, socket.SOCK_RAW, 1)  # AF_BLUETOOTH, BTPROTO_HCI
    try:
        address = ctypes.create_string_buffer(struct.pack('=HHH', 31, 0xFFFF, 2))
        libc = ctypes.CDLL(None, use_errno=True)
        libc.bind.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint]
        if libc.bind(sock.fileno(), address, 6) != 0:
            code = ctypes.get_errno()
            raise OSError(code, errno.errorcode.get(code, 'bind failed'))
        sock.setblocking(False)
        return sock
    except BaseException:
        sock.close()
        raise


async def probe(args):
    # Already installed in Home Assistant; self-tests do not import HA packages.
    from dbus_fast import BusType, DBusError, Variant
    from dbus_fast.aio import MessageBus
    from dbus_fast.service import ServiceInterface, method

    target = f'/org/bluez/{args.adapter}/dev_' + args.mac.replace(':', '_')
    agent_path = '/org/bluetooth_tv_remotes/diagnostic_agent'

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

    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    async def interface(path, name):
        obj = bus.get_proxy_object('org.bluez', path, await bus.introspect('org.bluez', path))
        return obj.get_interface(name)

    manager = await interface('/', 'org.freedesktop.DBus.ObjectManager')
    agents = await interface('/org/bluez', 'org.bluez.AgentManager1')
    adapter = await interface(f'/org/bluez/{args.adapter}', 'org.bluez.Adapter1')
    loop = asyncio.get_running_loop()
    registered = scanning = False
    sock = None
    handles = set()
    live = None
    live_time = last_live_log = 0
    before = await manager.call_get_managed_objects()
    initial_bonds = {p for p, i in before.items() if i.get('org.bluez.Device1', {}).get('Paired', Variant('b', False)).value}
    try:
        if args.monitor:
            try:
                sock = monitor_socket()
                def read_monitor():
                    nonlocal live, live_time, last_live_log
                    try:
                        packet = sock.recv(65535)
                    except BlockingIOError:
                        return
                    for record in hci_events(packet, args.mac, handles):
                        event = record.pop('event')
                        if event == 'live_advertisement':
                            if record['flags'] is None:
                                continue  # Scan responses need not contain GAP flags.
                            changed = live is None or live['flags'] != record['flags']
                            live, live_time = record, loop.time()
                            if not changed and live_time - last_live_log < 5:
                                continue
                            last_live_log = live_time
                        emit(event, **record)
                loop.add_reader(sock.fileno(), read_monitor)
                emit('monitor_ready')
            except OSError as exc:
                emit('monitor_unavailable', error=str(exc))
        bus.export(agent_path, Agent())
        await agents.call_register_agent(agent_path, 'NoInputNoOutput')
        registered = True  # Not made the system default agent.
        await adapter.call_set_discovery_filter({'Transport': Variant('s', 'le')})
        await adapter.call_start_discovery()
        scanning = True
        emit('ready', mac=args.mac, wait_seconds=args.wait, pairing_allowed=args.pair)
        deadline = loop.time() + args.wait
        last_observation = None
        while loop.time() < deadline:
            objects = await manager.call_get_managed_objects()
            props = objects.get(target, {}).get('org.bluez.Device1', {})
            if not props:
                await asyncio.sleep(.5)
                continue
            values = {k: v.value for k, v in props.items()}
            flags = list(values.get('AdvertisingFlags', []))
            if sock is not None and not values.get('Paired', False):
                if live is None or loop.time() - live_time > 5:
                    await asyncio.sleep(.5)
                    continue
                flags = [live['flags']]
                values['RSSI'] = live['rssi']
            observation = (values.get('RSSI'), tuple(flags), values.get('Paired'))
            if observation != last_observation:
                emit('advertisement', rssi=values.get('RSSI'), flags=flags, paired=values.get('Paired'))
                last_observation = observation
            paired = values.get('Paired', False)
            if not paired and (not args.pair or not flags or not flags[0] & 3):
                await asyncio.sleep(.5)
                continue
            dev = await interface(target, 'org.bluez.Device1')
            if not paired:
                emit('pair_start')
                try:
                    await asyncio.wait_for(dev.call_pair(), timeout=args.pair_timeout)
                    emit('pair_success')
                except (DBusError, TimeoutError) as exc:
                    emit('pair_failed', kind=type(exc).__name__, error=str(exc))
                    if isinstance(exc, TimeoutError):
                        try:
                            await dev.call_cancel_pairing()
                            emit('pending_pair_cancelled')
                        except DBusError as cancel_error:
                            emit('cancel_result', error=str(cancel_error))
                    break
            if not values.get('Connected', False):
                try:
                    await asyncio.wait_for(dev.call_connect(), timeout=20)
                    emit('connect_success')
                except (DBusError, TimeoutError) as exc:
                    emit('connect_failed', error=str(exc))
            # Existing discovery may take time, especially with a malformed server.
            await asyncio.sleep(12)
            break
        objects = await manager.call_get_managed_objects()
        props = objects.get(target, {}).get('org.bluez.Device1', {})
        state = {k: v.value for k, v in props.items() if k in ['Paired', 'Bonded', 'Connected', 'ServicesResolved']}
        services = sum(p.startswith(target + '/') and 'org.bluez.GattService1' in i for p, i in objects.items())
        nodes = []
        for p in Path('/sys/class/hidraw').glob('hidraw*'):
            try:
                if ('hid_uniq=' + args.mac.lower()) not in (p / 'device/uevent').read_text().lower():
                    continue
                data = (p / 'device/report_descriptor').read_bytes()
            except FileNotFoundError:
                continue
            nodes.append({'path': '/dev/' + p.name, 'descriptor_bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
        retained = all(objects.get(p, {}).get('org.bluez.Device1', {}).get('Paired', Variant('b', False)).value for p in initial_bonds)
        emit('result', state=state, gatt_services=services, hidraw=nodes, existing_bonds_retained=retained)
    finally:
        if sock is not None:
            loop.remove_reader(sock.fileno())
            sock.close()
        if scanning:
            try:
                await adapter.call_stop_discovery()
            except DBusError as exc:
                emit('cleanup_error', step='stop_own_discovery', error=str(exc))
        if registered:
            try:
                await agents.call_unregister_agent(agent_path)
            except DBusError as exc:
                emit('cleanup_error', step='unregister_own_agent', error=str(exc))
        bus.disconnect()
        emit('cleanup_complete')


def self_test():
    mac = 'AA:BB:CC:DD:EE:FF'
    addr = bytes.fromhex('ffeeddccbbaa')
    def packet(opcode, data):
        return struct.pack('<HHH', opcode, 0, len(data)) + data
    handles = set()
    assert hci_events(b'', mac, handles) == []
    assert advertising_flags(b'\x02\x01\x05') == 5
    assert advertising_flags(b'\x02\x01') is None
    assert advertising_flags(b'\x00') is None
    adv = b'\x02\x01\x00\x00' + addr + b'\x03\x02\x01\x05\xc0'
    assert hci_events(packet(3, bytes([0x3e, len(adv)]) + adv), mac, handles) == [
        {'event': 'live_advertisement', 'flags': 5, 'rssi': -64}]
    ext = b'\x0d\x01' + b'\x00\x00\x00' + addr + b'\x01\x00\x00\x00\xc0' + bytes(9) + b'\x03\x02\x01\x04'
    assert hci_events(packet(3, bytes([0x3e, len(ext)]) + ext), mac, handles) == [
        {'event': 'live_advertisement', 'flags': 4, 'rssi': -64}]
    params = b'\x00\x00\x00' + addr
    command = packet(2, struct.pack('<HB', 0x2043, len(params)) + params)
    assert hci_events(command, mac, handles)[0]['bearer'] == 'le'
    p = b'\x0a\x00\x01\x00\x00\x00' + addr
    assert hci_events(packet(3, bytes([0x3e, len(p)]) + p), mac, handles)[0]['status'] == 0
    assert handles == {1}
    encryption = packet(3, b'\x08\x04\x00\x01\x00\x01')
    assert hci_events(encryption, mac, handles)[0]['enabled'] == 1
    secret = bytes(range(16))
    acl = struct.pack('<HHHH', 1, 21, 17, 6) + b'\x06' + secret
    records = hci_events(packet(5, acl), mac, handles)
    assert records == [{'event': 'smp', 'direction': 'rx', 'opcode': 6}]
    assert hci_events(packet(5, acl), mac, set()) == []
    att = b'\x09\x07\x02\x00\x02\x03\x00\x00\x2a'
    acl = struct.pack('<HHHH', 1, len(att) + 4, len(att), 4) + att
    assert hci_events(packet(5, acl), mac, handles)[0]['uniform_length'] is True
    assert hci_events(packet(3, b'\x05\x04\x00\x01\x00\x13'), mac, handles)[0]['reason'] == 0x13
    assert not handles
    p = b'\x04\x00\x00' + addr + b'\x01\x00'
    assert hci_events(packet(3, bytes([3, len(p)]) + p), mac, handles)[0] == {'event': 'connection_complete', 'bearer': 'bredr', 'status': 4}
    print('Connection/encryption/SMP parser checks passed; no raw key material emitted')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--mac', type=str.upper)
    parser.add_argument('--adapter', default='hci0')
    parser.add_argument('--pair', action='store_true')
    parser.add_argument('--monitor', action='store_true')
    parser.add_argument('--wait', type=int, default=120)
    parser.add_argument('--pair-timeout', type=int, default=40)
    args = parser.parse_args()
    if args.self_test:
        self_test()
    else:
        if not args.mac or not re.fullmatch(r'(?:[0-9A-F]{2}:){5}[0-9A-F]{2}', args.mac):
            parser.error('Provide a valid --mac')
        if not re.fullmatch(r'hci\d+', args.adapter) or not 1 <= args.wait <= 180 or not 1 <= args.pair_timeout <= 60:
            parser.error('Invalid adapter or timeout')
        asyncio.run(probe(args))
