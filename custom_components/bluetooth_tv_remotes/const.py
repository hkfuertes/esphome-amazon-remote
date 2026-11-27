"""Constants for the Bluetooth TV Remotes integration."""

from homeassistant.const import Platform

DOMAIN = "bluetooth_tv_remotes"

CONF_MAC = "mac"
CONF_OUTPUT_SPEAKER = "output_speaker"

AMAZON_VENDOR_ID = "00000171"
MAX_VOICE_SECONDS = 30.0

PLATFORMS: list[Platform] = [
    Platform.ASSIST_SATELLITE,
    Platform.SELECT,
    Platform.SENSOR,
]
