#!/usr/bin/env python3
"""Inspect ONN button notifications in a btmon text capture (stdlib only).

Consumer handle 0x0035 and keyboard handle 0x002e were verified live.
Other handles are preserved as unknown rather than guessed.
This tool sends no Bluetooth commands.
"""
import argparse
from collections import Counter
import json
import re

CONSUMER = {
    0x42: 'up', 0x43: 'down', 0x44: 'left', 0x45: 'right',
    0x41: 'select', 0x224: 'back', 0x223: 'home', 0xe2: 'mute',
    0x221: 'voice', 0x9c: 'channel_up', 0x9d: 'channel_down',
    0x30: 'power', 0x19c: 'tv_source', 0x1bb: 'user',
    0x9f: 'settings', 0x8d: 'tv_guide',
    0x77: 'app_1', 0x78: 'app_2', 0x79: 'app_3', 0x7a: 'app_4',
}
ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')


def decode(handle, payload):
    if handle == 0x0035 and len(payload) >= 2:
        code = int.from_bytes(payload[:2], 'little')
        return 'release' if not code else 'press:' + CONSUMER.get(code, hex(code))
    if handle == 0x002e and len(payload) == 8:
        keys = [key for key in payload[2:] if key]
        return 'release' if not keys else 'press:' + ','.join(
            {0x80: 'volume_up', 0x81: 'volume_down'}.get(key, hex(key)) for key in keys)
    return 'unknown'


def notifications(lines):
    pending = False
    handle = None
    timestamp = None
    for line in lines:
        line = ANSI.sub('', line).strip()
        clock = re.search(r'\[hci\d+\]\s+(\d+\.\d+)$', line)
        if clock:
            timestamp = float(clock[1])
        if 'ATT: Handle Value Notification' in line:
            pending, handle = True, None
        elif pending:
            match = re.fullmatch(r'Handle: (0x[0-9a-fA-F]+)(?: .*|)', line)
            if match:
                handle = int(match[1], 16)
                continue
            match = re.fullmatch(r'Data\[(\d+)\]: ([0-9a-fA-F]+)', line)
            if match:
                if len(match[2]) % 2:
                    pending = False
                    continue
                payload = bytes.fromhex(match[2])
                if handle is not None and len(payload) == int(match[1]):
                    event = {'handle': f'0x{handle:04x}', 'raw': payload.hex(),
                             'event': decode(handle, payload)}
                    if timestamp is not None:
                        event['time_seconds'] = timestamp
                    yield event
                pending = False
            elif line and not line.startswith('Type:'):
                # ponytail: btmon text only; add a btsnoop parser if formats change.
                pending = False


def self_test():
    assert decode(0x35, bytes.fromhex('44 00 00 00')) == 'press:left'
    assert decode(0x35, bytes.fromhex('45 00 00 00')) == 'press:right'
    assert decode(0x35, bytes.fromhex('24 02 00 00')) == 'press:back'
    assert decode(0x35, bytes(4)) == 'release'
    assert decode(0x35, b'') == 'unknown'
    assert decode(0x2e, bytes(8)) == 'release'
    assert decode(0x2e, bytes.fromhex('00 00 80 00 00 00 00 00')) == 'press:volume_up'
    assert decode(0x2e, bytes.fromhex('00 00 81 00 00 00 00 00')) == 'press:volume_down'
    assert decode(0x2e, bytes(7)) == 'unknown'
    assert decode(0x55, b'\x08') == 'unknown'
    text = (' ATT: Handle Value Notification (0x1b) len 6\n'
            ' Handle: 0x0035 Type: Report (0x2a4d)\n'
            ' Data[4]: 44000000\n')
    assert list(notifications(text.splitlines())) == [
        {'handle': '0x0035', 'raw': '44000000', 'event': 'press:left'}]
    assert not list(notifications(text.replace('Data[4]', 'Data[5]').splitlines()))
    assert not list(notifications(text.replace('44000000', '4400000').splitlines()))
    clocked = '> ACL Data RX: Handle 2048 #1 [hci0] 12.345\n' + text
    assert list(notifications(clocked.splitlines()))[0]['time_seconds'] == 12.345
    assert not list(notifications(text.replace(' Handle: 0x0035 Type: Report (0x2a4d)\n', '').splitlines()))
    assert not list(notifications(text.replace(' Data[4]', '> New HCI event\n Data[4]').splitlines()))
    assert list(notifications(('\x1b[32m' + text + '\x1b[0m').splitlines()))[0]['event'] == 'press:left'
    print('ONN capture parser checks passed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', nargs='?')
    parser.add_argument('--test', action='store_true')
    args = parser.parse_args()
    if args.test:
        self_test()
        return
    if not args.capture:
        parser.error('provide a btmon text capture, or --test')
    counts = Counter()
    with open(args.capture, encoding='utf-8', errors='replace') as capture:
        for event in notifications(capture):
            print(json.dumps(event))
            counts[event['event']] += 1
    print(json.dumps({'counts': dict(counts)}))


if __name__ == '__main__':
    main()
