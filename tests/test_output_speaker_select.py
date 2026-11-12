"""Render the actual HA dropdown templates with changing media_player entities."""
import ast
from pathlib import Path
from types import SimpleNamespace

from jinja2 import Environment, StrictUndefined
import yaml


config = yaml.safe_load((Path(__file__).resolve().parents[1] / "home_assistant/amazon_remote_output_speaker.yaml").read_text())
select = config["template"][0]["select"][0]
action = select["select_option"][0]
assert action["action"] == "text.set_value"
target = action["target"]["entity_id"]
env = Environment(undefined=StrictUndefined)


class States:
    def __init__(self, players, selected):
        self.media_player = [SimpleNamespace(entity_id=p) for p in players]
        self.selected = selected

    def __call__(self, entity_id):
        assert entity_id == target
        return self.selected


def render(field, players, selected):
    states = States(players, selected)
    return env.from_string(select[field]).render(
        states=states, has_value=lambda entity: states(entity) not in ("unknown", "unavailable")
    ).strip()


players = ["media_player.salon", "media_player.cocina"]
assert ast.literal_eval(render("options", players, "")) == ["Sin altavoz", *sorted(players)]
assert render("state", players, "") == "Sin altavoz"
assert render("state", players, players[0]) == players[0]
assert render("availability", players, "") == "True"
for missing in ("unknown", "unavailable"):
    assert render("availability", players, missing) == "False"
assert ast.literal_eval(render("options", [], "")) == ["Sin altavoz"]
assert ast.literal_eval(render("options", [players[0]], players[0])) == ["Sin altavoz", players[0]]
assert ast.literal_eval(render("options", [], players[0])) == ["Sin altavoz", players[0]]
assert ast.literal_eval(render("options", players + ["media_player.dormitorio"], "")) == [
    "Sin altavoz", "media_player.cocina", "media_player.dormitorio", "media_player.salon"
]
for option, expected in [("Sin altavoz", ""), (players[0], players[0])]:
    assert env.from_string(action["data"]["value"]).render(option=option) == expected
print("Speaker dropdown checks passed: discovery, selection, clearing, unavailable and changing players")
