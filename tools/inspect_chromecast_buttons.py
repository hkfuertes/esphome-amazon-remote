#!/usr/bin/env python3
"""Read-only Chromecast HID button probe; no microphone or vendor writes."""
import argparse
import os
import select
import time

# Report 01 is a two-slot array, indexed into the descriptor's usage list.
# These are not little-endian consumer usage numbers like the ONN reports.
KEYS = ('consumer_019e', 'play_pause', 'up', 'down', 'left', 'right',
        'select', 'mute', 'voice', 'home', 'back', 'volume_up', 'volume_down',
        'app_1', 'app_2', 'app_3', 'consumer_0089')


def decode(packet):
    # This unit sends eight-byte payloads despite declaring two array slots.
    if not 3 <= len(packet) <= 9 or packet[0] != 1 or any(packet[3:]):
        return None
    return [KEYS[n - 1] if n <= len(KEYS) else f'selector_{n:02x}'
            for n in packet[1:3] if n]


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--device')
    args = parser.parse_args()
    if args.self_test:
        assert decode(bytes.fromhex('01 03 00')) == ['up']
        assert decode(bytes.fromhex('01 0c 00')) == ['volume_up']
        assert decode(bytes.fromhex('01 03 09')) == ['up', 'voice']
        assert decode(bytes.fromhex('01 00 00')) == []
        assert decode(bytes.fromhex('01 ff 00')) == ['selector_ff']
        assert decode(bytes.fromhex('01 0f 00 00 00 00 00 00 00')) == ['app_2']
        assert decode(bytes.fromhex('01 00 00 01')) is None
        assert decode(b'') is None
        print('Chromecast array-report decoder checks passed')
    elif args.device:
        fd = os.open(args.device, os.O_RDONLY | os.O_NONBLOCK)
        try:
            print('READ-ONLY CHROMECAST CAPTURE READY', flush=True)
            end = time.monotonic() + 180
            while time.monotonic() < end:
                if not select.select([fd], [], [], .5)[0]:
                    continue
                packet = os.read(fd, 4096)
                if not packet:
                    break
                print(f'{time.time():.3f} {packet.hex(" ")} held={decode(packet)}', flush=True)
        except OSError as exc:
            print(f'Capture stopped: {exc}', flush=True)
        finally:
            os.close(fd)
    else:
        parser.error('Provide --self-test or --device')
