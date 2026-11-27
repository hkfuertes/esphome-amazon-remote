# Chromecast with Google TV remote: initial desktop test

The user's Chromecast remote paired on dungeon with the **original installed
BlueZ 5.82**, without a patched daemon or manually imported cache for this
remote. The earlier ONN cache-bootstrap experiment is a separate device.
No HAOS changes were made for this test.

Verified:

- Name: `Chromecast Remote`.
- HID vendor/product: **18D1:9450**.
- Paired, bonded, trusted and connected using a temporary NoInputNoOutput agent.
- Native BlueZ HOG created `/dev/hidraw3`; report descriptor is **57 bytes**.
- The user's all-button pass produced **14 distinct array selectors**, with
  34 all-zero release reports and no undecodable packet shapes: directions,
  select, back, home, mute, volume up/down, two app selectors, `consumer_019e`
  and `consumer_0089`.
- No `voice` selector was observed in that pass. Its descriptor entry alone
  does not establish working voice-button delivery or microphone support.
- The two unnamed consumer usages still need isolated physical-label checks;
  reconnect testing also remains pending.

## Report format

The descriptor declares consumer report **01** as a **two-slot, eight-bit array**.
Each nonzero byte selects an entry in the descriptor's ordered usage list;
these bytes are **not** little-endian consumer usage IDs.

The tested unit actually sends eight-byte payloads. The first two bytes are
array slots and the remaining six were zero padding in observed reports.
The diagnostic accepts only zero padding; unexpected shapes remain undecoded.

Examples, including hidraw report ID:

```text
01 06 00 00 00 00 00 00 00  -> right held
01 00 00 00 00 00 00 00 00  -> released
```

`tools/inspect_chromecast_buttons.py` is a bounded, read-only probe with
standalone decoder checks:

```sh
python3 tools/inspect_chromecast_buttons.py --self-test
python3 tools/inspect_chromecast_buttons.py --device /dev/hidrawN
```

Choose the node matching this remote's `HID_UNIQ`, not a fixed node number.
Opening it may require suitable permissions. This probe performs no microphone
or vendor writes. Desktop input/volume shortcuts may also react normally.

Descriptor usages include voice, navigation, volume, app selectors and two
usages whose physical labels are not yet verified.

## Voice capture (ATVV) on desktop BlueZ

`tools/probe_chromecast_voice.py` exercises the remote's ATVV voice service
(service `ab5e0001`, control `ab5e0002` write, audio `ab5e0003` notify, status
`ab5e0004` notify) through BlueZ D-Bus, using only the documented protocol:
`GET_CAPS`, `MIC_OPEN`, `MIC_CLOSE` and notification subscriptions.

Verified exchange with the installed stock BlueZ 5.82:

1. `GET_CAPS` answered `CAPS` **version 1.0, codecs 0x0203 (ADPCM 16 kHz),
   frame size 160 bytes**.
2. Holding the voice button produced `START_SEARCH` (0x08); the probe granted
   the mic with `MIC_OPEN` (0x0C, codec 0x02).
3. The remote streamed **387 uniform 160-byte audio notifications**
   (61,920 bytes; 20 ms frames) and periodic `AUDIO_SYNC` heartbeats whose
   predictor/step fields are zero (host-managed decoder state).
4. Releasing the button produced `AUDIO_STOP` (0x00).

The probe decodes a continuous IMA/DVI ADPCM 16 kHz stream (89-entry step
table, decoder self-checks included) and writes PCM WAV. First live capture:
**123,840 samples (~7.7 s), speech-like energy structure**
(`diagnostics/chromecast-voice.wav`, plus `.raw`/`.json` evidence). Final
listening validation was performed by playing the WAV on desktop audio.

This is a **working voice pipeline over stock BlueZ**, unlike the G10 path,
which required a patched daemon. No microphone-enable writes were sent outside
the documented ATVV opcodes.

## Home Assistant integration status

**Experimental, not validated for reliable Assist use.** Amazon remains the
validated integration path; each remote has a separate MAC-scoped config entry.

On stock HAOS BlueZ, ATVV capability negotiation and standalone microphone capture
also worked: v1.0, ADPCM 16 kHz, 160-byte notifications. Both remotes coexisted.
However, subsequent integrated Assist tests produced missing or incorrect text.

The desktop recording, decoded with the integration's decoder, transcribed
correctly through Home Assistant Cloud STT. HA's own voice detector also detected
speech in it. This validates that recording and decoder, not every live stream.

An unattended STT-only Assist replay of that same complete recording returned
identical correct text both with 20 ms packet spacing (8.15 seconds to completion)
and with 80 ms spacing (31.43 seconds). These replays explicitly disabled VAD
cutoff to model button-release EOF, and never ran intent/TTS stages. Slow delivery
alone therefore does not reproduce the observed distortion. This does not prove
that live audio packets, encoder state, or live VAD behavior are correct.

A passive observer of a live HAOS attempt received 56 complete 160-byte frames
(1.12 seconds of PCM) over 2.40 seconds of packet arrivals. An independent STT
request also misrecognized that recording. The observed frame counts at
`AUDIO_SYNC` checkpoints matched the observer's counts; arrival delays alone do
not establish D-Bus packet loss or identify the bottleneck.

The integration now respects physical hold-to-talk `AUDIO_START` notifications
(`04 03 02 <session>`) instead of replacing an already-open stream with another
`MIC_OPEN`. A safety stop closes the actual session (`0D <session>`) only once,
while retaining the pending press edge so the remote's `AUDIO_STOP` still emits
`release:voice`. Malformed status packets are ignored. These cases are covered
by `tests/test_atvv_session.py`. This session correction did **not** resolve the live problem:
a second attempt delivered 55 frames (1.10 seconds of PCM) over 5.10 seconds.

A diagnostic disconnect was followed by failed reconnects. Target-only re-pairing
briefly recreated hidraw, but the host then repeatedly recreated the HID device
and logged a kernel warning in `hci_conn_timeout`. BlueZ temporarily reported
`Connected=true` with no matching kernel link or hidraw device. These observations
do not prove that the Python integration caused the kernel warning.

Early recovery restarted the host Bluetooth service and power-cycled the adapter,
retaining both bonds and speaker/pipeline selections. Subsequent target-only
Chromecast re-pairing and a host reboot did not establish reliable reconnection.
Amazon recovered with an ordinary wake press.

### Latest radio evidence and stopping point

A correctly parsed, sanitized HCI trace captured a fresh Chromecast pairing with
SMP, successful encryption, GATT discovery and a matching hidraw device. About
18 seconds into the observation, the remote terminated the connection with
reason `0x13` (Remote User Terminated Connection). Subsequent connections reached
the controller but failed encryption with status `0x06` (PIN or Key Missing),
then disconnected locally with reason `0x16`. This identifies a bond/key failure
on those reconnects, but does not establish why the keys stopped matching.
Earlier ad-hoc monitors used an incorrect event offset; their absence of radio
events is not reliable evidence.

Temporarily increasing the controller's default supervision timeout from 42 to
400 units did not prevent the failure; the original value of 42 was restored.
No ATVV control writes preceded that captured disconnect. Immediate ATVV
capability negotiation after pairing is an **untested hypothesis**, not a fix.
The last retry did not see a fresh pairing advertisement.

Testing is paused. The Chromecast config entry and its four entities were
removed through Home Assistant's API (`require_restart: false`), and its BlueZ
entry was confirmed absent. Home Assistant reports `RUNNING`; Amazon remains
bonded, connected, services-resolved and ready with an idle Assist satellite.
No unattended speaker playback is performed. Do not treat this experimental
Chromecast path as production-ready.
