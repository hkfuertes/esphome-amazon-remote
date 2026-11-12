"""Check optional speaker wiring and execute the actual YAML entity validation."""
from pathlib import Path
import subprocess
import tempfile
import yaml


class Loader(yaml.SafeLoader):
    pass


for tag in ("!secret", "!lambda"):
    Loader.add_constructor(tag, lambda loader, node: loader.construct_scalar(node))
config = yaml.load((Path(__file__).resolve().parents[1] / "amazon-remote.yaml").read_text(), Loader=Loader)
speaker = next(t for t in config["text"] if t["id"] == "assist_output_speaker")
assert speaker["initial_value"] == "" and speaker["restore_value"]
action = config["voice_assistant"]["on_tts_end"][0]["if"]
play = action["then"][0]["homeassistant.action"]
assert play["action"] == "media_player.play_media"
assert play["data"]["entity_id"] == "return id(assist_output_speaker).state;"
assert play["data"]["media_content_id"] == "return x;"
condition = action["condition"]["lambda"].replace("id(assist_output_speaker).state", "value")
source = '#include <string>\n#include <cassert>\nbool valid(const std::string &value) {\n' + condition + '''
}
int main() {
  assert(valid("media_player.salon"));
  assert(valid("media_player.echo_2"));
  for (const auto *value : {"", "media_player.", "light.salon", "all", "media_player.Salon",
                            "media_player.salon,media_player.cocina", "media_player.salon ",
                            "media_player.salon;", " media_player.salon"}) {
    assert(!valid(value));
  }
}
'''
with tempfile.TemporaryDirectory() as tmp:
    cpp, executable = Path(tmp) / "speaker.cpp", Path(tmp) / "speaker"
    cpp.write_text(source)
    subprocess.run(["g++", "-std=c++17", "-Wall", "-Wextra", "-Werror", str(cpp), "-o", str(executable)], check=True)
    subprocess.run([str(executable)], check=True)
print("Output speaker checks passed: optional playback, TTS URL and entity validation")
