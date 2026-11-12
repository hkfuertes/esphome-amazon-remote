"""Run with Python + PyYAML + Jinja2 (already included in the ESPHome image)."""
from pathlib import Path
import re
from types import SimpleNamespace

import yaml
from jinja2.nativetypes import NativeEnvironment


class Loader(yaml.SafeLoader):
    pass


Loader.add_constructor("!input", lambda loader, node: {"input": loader.construct_scalar(node)})
root = Path(__file__).resolve().parents[1]
blueprint = yaml.load((root / "blueprints/amazon_remote_buttons.yaml").read_text(), Loader=Loader)
inputs = blueprint["blueprint"]["input"]
buttons = set(re.findall(r'return "([^"]+)";', (root / "components/amazon_remote/button_decoder.h").read_text()))
buttons -= {"", "invalid", "release"}
assert set(inputs) == buttons | {"remote", "release", "voice_release"}
assert blueprint["triggers"][0]["entity_id"] == {"input": "remote"}
assert blueprint["triggers"][0]["to"] is None  # Ignore attribute-only updates.
assert blueprint["mode"] == "parallel"
for name, config in inputs.items():
    if name != "remote":
        assert config["default"] == []
        assert config["selector"] == {"action": {}}

env = NativeEnvironment()


def route(state):
    context = {"trigger": SimpleNamespace(to_state=SimpleNamespace(state=state) if state is not None else None)}
    for name, template in blueprint["variables"].items():
        context[name] = env.from_string(template).render(context)
    if not env.from_string(blueprint["conditions"][0]["value_template"]).render(context):
        return []
    actions = []
    for step in blueprint["actions"]:
        for branch in step["choose"]:
            if env.from_string(branch["conditions"]).render(context):
                actions.append(branch["sequence"]["input"])
                break
    return actions


for button in buttons:
    assert route("press:" + button) == [button], button
    expected = ["voice_release", "release"] if button == "voice" else ["release"]
    assert route("release:" + button) == expected, button
for state in [None, "unknown", "unavailable", "disconnected", "ready", "no_button_reports", "press:unknown_01_00fe"]:
    assert route(state) == [], state
print(f"Blueprint checks passed: {len(buttons)} buttons, releases and unavailable states")
