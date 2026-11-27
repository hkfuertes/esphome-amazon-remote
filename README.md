# Bluetooth TV Remotes

Native Home Assistant integration for Bluetooth TV remotes: buttons, push-to-talk
Assist, and optional spoken replies on an existing media player. No ESP32,
ESPHome, MQTT bridge, or extra add-on is required.

**Support status:**
- **Amazon Fire TV Remote** (`0171:042F`): validated buttons and Assist push-to-talk via `/dev/hidraw` (Opus).
- **Chromecast with Google TV Remote** (`18D1:9450`): experimental buttons via `/dev/hidraw` and IMA ADPCM audio via BlueZ ATVV GATT (D-Bus). Standalone capture works, but integrated Assist recognition and HAOS reconnection are not yet reliable. See [test results](docs/chromecast-remote.md#home-assistant-integration-status).

Multiple remotes can be added at the same time; each config entry is scoped to one Bluetooth MAC and creates its own independent device and entities.

<img src="assets/tested-remote.webp" alt="The only tested Amazon Fire TV remote" width="150">

Photo source: [Amazon product image](https://m.media-amazon.com/images/I/71rUQOC9BwL._AC_SL1500_.jpg).

## Requirements

- Linux with BlueZ and a local Bluetooth adapter. Tested on HAOS, Raspberry Pi 4,
  Home Assistant Core 2026.11.3.
- A remote bonded to that host, exposing `/dev/hidraw*` to Home Assistant.
- System `libopus.so.0` for Amazon Opus decoding (already present in tested HAOS; Chromecast uses pure-Python IMA ADPCM over BlueZ D-Bus).
- An Assist pipeline with speech-to-text, plus text-to-speech if using a speaker.

Home Assistant Container installations must explicitly provide device access.
Bluetooth proxies are **not supported**: this integration reads local HID devices,
not generic GATT notifications through Home Assistant's Bluetooth manager.

## Install

1. Copy `custom_components/bluetooth_tv_remotes/` from this repository into
   `/config/custom_components/bluetooth_tv_remotes/` on Home Assistant.
2. Restart Home Assistant.
3. Bond the remote to the host using BlueZ (see below).
4. Open **Settings → Devices & services → Add integration → Bluetooth TV Remotes**.
5. Select the connected remote (model is auto-detected from `HID_ID`) or enter its Bluetooth MAC address. Repeat **Add integration** for additional remotes.

This is a custom integration, not a built-in integration or a published HACS store
entry. Home Assistant's standard custom-integration warning is expected.

### Pairing the tested Amazon remote

Disconnect the old ESP32 bridge or other host first. Hold **Home for about ten
seconds** to enter pairing mode, then use BlueZ with a `NoInputNoOutput` agent to
pair, trust, and connect the remote. Wake advertising before those ten seconds is
not necessarily pairing-mode advertising.

A stale trusted BlueZ entry caused premature connections during testing. Removing
that remote's stale entry and pairing only after pairing-mode advertising began
worked. Do not remove unrelated Bluetooth devices. The integration does not yet
implement pairing itself.

Successful pairing exposes a HID device whose `HID_UNIQ` in
`/sys/class/hidraw/hidraw*/device/uevent` matches the remote's MAC address. On the
tested HAOS setup this device also appeared inside the Home Assistant container.
Pairing to another host can replace the remote's existing bond.

## Use

The integration creates:

- **Last Button**: `press:<key>` and `release:<key>`, plus connection status.
- **Assistant**: select the Home Assistant Assist pipeline.
- **Assist Satellite**: hold the Alexa/voice button to speak; release to finish.
- **Assist Output Speaker**: dynamic dropdown of `media_player.*` entities.
  Choose `None` for text-only Assist. Selection is restored after restart.

Voice capture stops after 30 seconds. The remote has no supported speaker output;
replies play on the selected external media player. That player must be able to
reach Home Assistant's generated TTS URL. Playback completion is not synchronized
with the satellite's state.

### Button automations

Install [the button blueprint](blueprints/README.md) and choose the integration's
**Last Button** sensor. Leave Voice press/release actions empty when using native
Assist so the blueprint does not duplicate voice handling.

## Migrating from Amazon Remote / ESPHome

This branch replaces the former `amazon_remote` custom integration with
`bluetooth_tv_remotes`. Home Assistant does not migrate config-entry domains
in place:

1. Note the pipeline, selected speaker, and automations you want to retain.
2. Remove the old **Amazon Remote** integration entry, then its
   `/config/custom_components/amazon_remote/` directory.
3. Install **Bluetooth TV Remotes** as above. Keep the host's BlueZ bond; there is
   no need to unpair just because the integration name changed.
4. Re-select the pipeline and speaker. Update automations to the new entities and
   install the renamed `tv_remote_buttons.yaml` blueprint.

For an old ESPHome bridge, unplug it before pairing the remote directly to the
Home Assistant host. Remove only that bridge's ESPHome device/integration entry
when no longer needed, not Home Assistant's entire ESPHome integration.

The ESPHome implementation remains available in Git history before this cleanup.

## Bluetooth metadata and icon

The manifest declares `connectivity: [bluetooth]` and integration type `device`.
The Last Button entity uses the standard `mdi:bluetooth` icon. Connectivity metadata
does **not** automatically give a custom integration the built-in Bluetooth
integration's brand logo; that integration-card logo requires separate Home
Assistant brands support.

## Development

```sh
python3 -m venv .venv
.venv/bin/pip install PyYAML Jinja2
.venv/bin/python tests/test_ha_integration.py
.venv/bin/python tests/test_atvv_session.py
.venv/bin/python tests/test_blueprint.py
```

`test_ha_integration.py` checks metadata, MAC validation, remote-type detection,
Amazon and Chromecast button reports, HID descriptor parsing, IMA ADPCM decoding,
and an Opus roundtrip when system libopus is available.
`test_atvv_session.py` checks physical/on-request microphone sessions, release handling,
session-specific safety stops, and malformed status rejection without HA or Bluetooth.
The blueprint check covers all 23 keys, releases, and ignored connection states.
