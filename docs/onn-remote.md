# ONN remote: button investigation

Branch: `research/generic-tv-remote`. Investigation performed on **dungeon**,
not the Home Assistant host. The integration has not been changed.

## Identified device

- Advertised name: `Onn-Remote`.
- Device Information manufacturer string: `Google lnc.` (literal spelling).
- Device Information model string: `ABBEY`.
- White Android TV G10-style layout; the housing alone does not establish an exact model.
- HID Report Map: 225 bytes; SHA-256
  `3e2822f519d77090b07a43a78c6d0c4f5c0195e78e409eb63de4d6535b86c187`.
- Reference: [atv-remote-hid-bridge button map](https://github.com/hkfuertes/atv-remote-hid-bridge/blob/1b176f5977246a7097bf285918d2adc2cabe8eee/BUTTON_MAP.md).

## Live button verification

A complete physical-button pass produced **22 distinct buttons**, 23 presses
(including the final held arrow), and 23 releases. All button codes agree with
the reference bridge. One additional vendor notification is not a button report.

| Buttons | Consumer usage(s), little-endian |
| --- | --- |
| Power | `0030` |
| User/profile | `01BB` |
| TV source/input | `019C` |
| Voice | `0221` |
| Settings | `009F` |
| Up / down / left / right | `0042` / `0043` / `0044` / `0045` |
| OK/select | `0041` |
| Back / home | `0224` / `0223` |
| TV guide | `008D` |
| Mute | `00E2` |
| Channel up / down | `009C` / `009D` |
| App 1 / 2 / 3 / 4 | `0077` / `0078` / `0079` / `007A` |

Consumer input is HID report ID `01`. Observed notifications arrive at GATT
handle `0035` as **four bytes**, despite the descriptor declaring a two-byte
usage: the first two bytes contain the usage and the remaining two were zero.
For example, left is `44 00 00 00`; release is `00 00 00 00`.

Volume uses keyboard report ID `0B`, GATT handle `002E`, with eight-byte
notifications. Keycodes start at byte 2 (after modifier and reserved bytes):
`80` is volume up; `81` is volume down. Release is eight zero bytes.

The final left-arrow hold lasted **2.560 seconds**, with one press and one
release and **no repeated notifications**. This single test does not establish
repeat behavior for every key. The existing bridge synthesizes USB releases;
that behavior must not be copied into push-to-talk or hold handling.

Voice press was followed by a separate notification at handle `0055` containing
`08`. Its meaning is unknown. No microphone/vendor enable commands were sent;
no audio support has been established.

## Dungeon transport findings

Host stack: BlueZ 5.82, Linux 6.12.113. An inconsistent initial bond produced
`PIN or Key Missing`. Removing only this remote's dungeon bond and pairing
again with a registered `NoInputNoOutput` agent produced a successful AES-CCM
encrypted connection. The Amazon remote's bond was left alone.

Initially, normal BlueZ discovery did **not** produce a usable `/dev/hidraw`
device or exported GATT service objects for this remote, even though
`ServicesResolved` became true. The patched-daemon test below subsequently
created hidraw and populated a cache usable by the original daemon. This
research is still **not integration support**: no ONN protocol has been added.

The capture shows a problematic characteristic-discovery response spanning
both standard and vendor services: it advertises a fixed seven-byte entry size
but includes 128-bit vendor characteristic declarations. The resulting handle
sequence becomes nonsensical. The replay and patched-daemon tests below
confirm this parser incompatibility for the captured discovery sequence;
they do not establish a universal fix for other firmware variants.

A separate, default-small-MTU GATT client could read the HID map, Device
Information, and discover characteristics restricted to the HID service. That
client rejected the remote's own MTU exchange request (251 bytes), then the
connection ended. A custom direct-ATT attempt was refused while the host stack
owned the link; it did not validate a replacement transport.

All 22 buttons above were ultimately verified through **read-only HCI
monitoring of the encrypted connection**, not through hidraw. Original BlueZ
configuration was restored after the temporary experiments. Research captures
stay local: they may contain bond keys and must not be published.

## Controlled BlueZ MTU tests

Both tests were authorized on dungeon, used automatic rollback, and left the
original Bluetooth configuration restored and the adapter operational.

- **MTU 23:** BlueZ failed to register the adapter:
  `setsockopt(L2CAP_OPTIONS): Invalid argument (22)`. No remote negotiation
  occurred, so this is not a successful low-MTU device test.
- **MTU 48:** the adapter started, the existing bond encrypted successfully,
  and packet capture confirmed a server receive MTU of **48**. Discovery
  progressed further, but another malformed mixed-width response appeared:
  an advertised 21-byte entry length with 35 bytes of entries (response length
  36 excluding the opcode). No hidraw device appeared. Ordinary direction
  notifications still arrived.

Reducing the global MTU is therefore **not a demonstrated workaround**. Do not
recommend keeping this configuration in Home Assistant. The remaining problem
is transport/discovery compatibility, not the now-verified button map.

## Public-source research

- [Google/Telink reference remote firmware](https://android.googlesource.com/platform/hardware/telink/atv/refDesignRcu/+/refs/heads/master/vendor/827x_ble_remote/app_att.c):
  published HID attribute definitions for the reference design. This does not
  prove this particular clone runs that exact firmware.
- [Genius1237/g10-ir](https://github.com/Genius1237/g10-ir): documents using G10/G20
  vendor GATT services with Bleak on desktop Linux. It does **not** establish
  working hidraw for the ABBEY unit tested here. No IR commands were run.
- [BlueZ issue 604, mixed-width response workaround](https://github.com/bluez/bluez/issues/604#issuecomment-4673330327):
  a contributor reports the same malformed 7/21-byte characteristic response
  pattern on Roland BLE MIDI devices, and provides a host-side parser patch
  tested against BlueZ 5.72. Its GATT-only portion was also tested locally
  against BlueZ 5.82 as described below. The issue is closed as not planned;
  this comment is not evidence the patch was accepted upstream.
- [BlueZ 5.82 parser](https://github.com/bluez/bluez/blob/5.82/src/shared/gatt-helpers.c):
  `discover_chrcs_cb()` requires a uniform seven- or 21-byte record size and
  validates divisibility, then obtains the last handle using that size. This
  matches our capture: some mixed responses are divisible by seven and are
  mis-sliced, while the MTU-48 response fails the divisibility check.

## Temporary patched-daemon test

Tested on dungeon with the existing bond; no HAOS changes:

1. Replayed the captured 238-byte characteristic list through the public
   patch's actual C helpers, compiled with AddressSanitizer, UBSan and strict
   warnings. It recovered **30 declarations**, ending at `0051`, including
   the two 21-byte vendor declarations. Uniform 7/21-byte and truncated-input
   checks also passed.
2. Built BlueZ **5.82** in an isolated Docker image, applying only the
   `src/shared/gatt-helpers.c` changes, not the unrelated MIDI changes.
3. Temporarily runtime-masked/stopped the host service to prevent D-Bus
   reactivation, then ran the patched daemon using the existing host bond and
   configuration. A 180-second watchdog and `finally` cleanup restored the
   original service; no host daemon binary or configuration was replaced.
4. The patched daemon completed GATT discovery and created **`/dev/hidraw2`**
   through BlueZ's normal HOG/uHID path. The 225-byte descriptor exactly matched
   the previously captured SHA-256. The remote remained paired/bonded/trusted.
5. Its normal BlueZ cache grew from 26 bytes (name only) to 3,784 bytes,
   including **74 cached attributes**.
6. Stopped the patched daemon, removed the temporary runtime mask and restarted
   the original installed BlueZ. It also recreated **`/dev/hidraw2`** using the
   repaired cache. The host service is active/enabled and `main.conf` is
   unchanged. No bonds were deleted.

7. Explicitly disconnected using stock BlueZ, then requested a reconnect.
   After the user woke the remote, `/dev/hidraw2` reappeared. A read-only
   capture verified left, select, volume up/down and real releases directly
   from hidraw. Desktop volume also reacted, confirming normal OS HID input.
   The bounded capture later ended with `EIO`; its exact disconnect cause was
   not investigated.

This demonstrates a cache-bootstrap workaround on this host, **not yet a
permanent fix**. Additional restarts, repeated reconnects and cache-invalidation
behavior still need validation.
In particular, a new host or a cleared/invalidated cache may hit the original
parser bug again. This does not establish a supported HAOS deployment method.

## HAOS version check and direct pairing attempt

The actual host daemon on the tested Raspberry Pi is **BlueZ 5.85 on HAOS
18.5**, verified by running the host binary's `--version` through a temporary
read-only container. The SSH add-on's `bluetoothctl` reports 5.86; that is not
the host daemon version.

HAOS 18.5 pins Buildroot revision
`5c344b3789e3f6d620cf11ffab60e522a875cc79`. Its
[BlueZ package recipe](https://github.com/home-assistant/buildroot/blob/5c344b3789e3f6d620cf11ffab60e522a875cc79/package/bluez5_utils/bluez5_utils.mk)
selects 5.85. Its only package patch modifies `src/device.c`, not the discovery
parser. The upstream 5.85 `discover_chrcs_cb()` is byte-for-byte identical to
the unpatched 5.82 callback used in our desktop comparison; the mixed-response
workaround is absent.

At the user's request, disconnected the desktop link without removing its
bond and attempted pairing directly on HA with a temporary, target-only
NoInputNoOutput D-Bus agent. HA observed pairing flags `05` and RSSI -64 dBm.
The first attempt returned `Page Timeout`. A later Pair request exceeded its
25-second deadline; subsequent calls returned `In Progress`, not additional
independent authentication failures. No HA bond or ONN hidraw was established,
so this test did **not** reach the GATT discovery bug.

The temporary agent and its discovery session were removed. No HA daemon,
configuration or cache was replaced. The existing Amazon remote remained
paired, bonded and connected.

A subsequent instrumented attempt captured the target's LE connection command
and a **successful LE Connection Complete (status 0)**. About three seconds
later Pair returned `Authentication Timeout`; the controller reported
Disconnection Complete reason **0x08 (Connection Timeout)**. No bond, GATT
services or hidraw appeared, and existing bonds were retained. This establishes
that the link can start; it does not identify why it times out or establish
whether HA would hit the malformed GATT response afterward.

### Reproduce without modifying HAOS

From a computer with this checkout and authorized SSH access to the SSH add-on
(with Docker access):

```sh
python3 tools/probe_ha_remote.py --self-test
ssh root@HA_ADDRESS 'docker exec -i homeassistant python3 - --mac AA:BB:CC:DD:EE:FF --pair --monitor --wait 180' < tools/probe_ha_remote.py
```

Activate the target's pairing mode after the JSON `ready` event. The command
executes via stdin: it installs no packages or files in HA and does not restart
services, edit configuration/cache files, remove bonds or replace BlueZ. The
normal pairing operation may save a new bond. The agent is restricted to the
target and is not made the default. Discovery is scoped to this D-Bus client;
cleanup removes only this client's session and agent. A pending Pair operation
is explicitly cancelled if its deadline expires.

The optional read-only HCI monitor reports target bearer, connection/encryption
status and SMP opcodes, **never raw packets or key material**. It needs the
Bluetooth socket permissions normally available inside HA Core. If monitoring
is unavailable, the remaining D-Bus checks still run. Logs can distinguish
link/authentication failures from successful bonding followed by failed GATT
or missing hidraw. With HCI monitoring available, pairing is gated on GAP flags
from a target advertisement received within the last five seconds, not cached
Device1 flags. Legacy and extended advertising parsers have runnable checks.
Only ATT opcodes, lengths and discovery-range metadata are logged, not values.

After the user moved the remote closer and explicitly reactivated pairing, the
fresh-advertisement probe observed **flags 05 at -64 dBm** and initiated LE.
The controller reported connection status 0, then `Page Timeout` and disconnect
reason **0x3e** roughly 250 ms later. No target ATT/SMP metadata was observed;
final paired/bonded flags were false, with no GATT services or hidraw. Thus
cached advertising flags do not explain away this reproduced failure. The
Amazon remote remained paired/bonded/connected. Further identical pairing
attempts alone are not a demonstrated workaround; comparing controller/link
behavior or testing another HA Bluetooth adapter is a separate next step.

## Reproduce capture analysis

The parser uses only the Python standard library, recognizes the two verified
button handles, and preserves unknown notifications without guessing:

```sh
python3 tools/inspect_onn_buttons.py --test
python3 tools/inspect_onn_buttons.py /path/to/btmon-text.log
```

Next step before porting: assess whether cache bootstrap is an acceptable
installation requirement, especially on HAOS, and test longer-term reconnect
behavior. Audio remains out of scope for this pass.
