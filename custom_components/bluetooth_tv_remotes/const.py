"""Constants for the Bluetooth TV Remotes integration."""

from homeassistant.const import Platform

DOMAIN = "bluetooth_tv_remotes"

CONF_MAC = "mac"
CONF_REMOTE_TYPE = "remote_type"
CONF_OUTPUT_SPEAKER = "output_speaker"

REMOTE_TYPE_AUTO = "auto"
REMOTE_TYPE_AMAZON = "amazon"
REMOTE_TYPE_CHROMECAST = "chromecast"

AMAZON_VENDOR_ID = "00000171"
CHROMECAST_VENDOR_ID = "000018D1"

REMOTE_MODELS: dict[str, tuple[str, str, str]] = {
    REMOTE_TYPE_AMAZON: ("Amazon", "Fire TV Remote", "Amazon Fire TV Remote"),
    REMOTE_TYPE_CHROMECAST: ("Google", "Chromecast Remote", "Chromecast Remote"),
}

MAX_VOICE_SECONDS = 30.0

PLATFORMS: list[Platform] = [
    Platform.ASSIST_SATELLITE,
    Platform.SELECT,
    Platform.SENSOR,
]
