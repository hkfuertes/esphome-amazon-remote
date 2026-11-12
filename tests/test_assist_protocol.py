"""Exercise the same API callbacks HA uses: stop aborts, audio.end finishes."""
import asyncio
from pathlib import Path

from aioesphomeapi import APIClient
from aioesphomeapi.api_pb2 import VoiceAssistantAudio, VoiceAssistantRequest
import yaml


class Loader(yaml.SafeLoader):
    pass


Loader.add_constructor("!secret", lambda loader, node: loader.construct_scalar(node))
Loader.add_constructor("!lambda", lambda loader, node: loader.construct_scalar(node))
config = yaml.load((Path(__file__).resolve().parents[1] / "amazon-remote.yaml").read_text(), Loader=Loader)
finish = next(s for s in config["script"] if s["id"] == "assist_finish_recording")
code = finish["then"][-1]["lambda"]
assert "VoiceAssistantAudio" in code and "end.end = true;" in code
assert "VOICE_ASSISTANT_STT_VAD_END" in code


class Connection:
    is_connected = True

    def __init__(self):
        self.callbacks = {}

    def send_message(self, message):
        pass

    def add_message_callback(self, callback, types):
        for kind in types:
            self.callbacks[kind] = callback
        return lambda: None


class Client(APIClient):
    def _get_connection(self):
        return self.connection


async def check():
    client = Client("localhost", 6053, "")
    connection = client.connection = Connection()
    stops, audio = [], []

    async def start(*args):
        return 0

    async def stop(abort):
        stops.append(abort)

    async def receive(data, data2):
        audio.append(data)

    client.subscribe_voice_assistant(handle_start=start, handle_stop=stop, handle_audio=receive)
    connection.callbacks[VoiceAssistantAudio](VoiceAssistantAudio(data=b"\x01\x00"))
    connection.callbacks[VoiceAssistantAudio](VoiceAssistantAudio(end=True))
    connection.callbacks[VoiceAssistantRequest](VoiceAssistantRequest(start=False))
    await asyncio.sleep(0)
    assert audio == [b"\x01\x00"]
    assert stops == [False, True], stops
    print("Assist protocol checks passed: audio.end finishes; request.stop aborts")


asyncio.run(check())
