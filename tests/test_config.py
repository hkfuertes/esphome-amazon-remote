"""Check the portable YAML uses one remote address and the public component."""
from pathlib import Path
import yaml


class Loader(yaml.SafeLoader):
    pass


for tag in ("!secret", "!lambda"):
    Loader.add_constructor(tag, lambda loader, node: loader.construct_scalar(node))
config = yaml.load((Path(__file__).resolve().parents[1] / "amazon-remote.yaml").read_text(), Loader=Loader)
assert config["esphome"]["name"] == "${device_name}"
assert config["esphome"]["friendly_name"] == "${friendly_name}"
assert config["substitutions"]["remote_mac"] == "AA:BB:CC:DD:EE:FF"
assert config["ble_client"][0]["mac_address"] == "${remote_mac}"
assert config["external_components"] == [{
    "source": "github://hkfuertes/esphome-amazon-remote@main",
    "components": ["amazon_remote"],
}]
setup = next(step["lambda"] for step in config["wifi"]["on_connect"]["then"] if "lambda" in step)
assert "uint64_to_mac_msb_first(id(fire_remote).get_address(), remote)" in setup
assert "esp_ble_gap_set_prefer_conn_params(remote, 12, 12, 4, 400)" in setup
assert "0x20, 0xBE" not in setup
assert next(s for s in config["text_sensor"] if s.get("id") == "remote_buttons")["name"] == "Last Button"
print("Config checks passed: substitutions, single MAC source, native conversion and public component")
