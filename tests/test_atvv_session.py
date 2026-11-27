"""Run with python3 tests/test_atvv_session.py; no HA or Bluetooth required."""
import asyncio
import importlib.util
from pathlib import Path
import sys
import types

COMP = Path(__file__).resolve().parents[1] / 'custom_components/bluetooth_tv_remotes'
package = types.ModuleType('atvv_check')
package.__path__ = [str(COMP)]
sys.modules[package.__name__] = package
const = types.ModuleType('atvv_check.const')
const.REMOTE_TYPE_AMAZON = 'amazon'
const.REMOTE_TYPE_CHROMECAST = 'chromecast'
sys.modules[const.__name__] = const
spec = importlib.util.spec_from_file_location('atvv_check.hid_client', COMP / 'hid_client.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


async def check():
    client = module.TVRemoteClient(asyncio.get_running_loop(), 'AA:BB:CC:DD:EE:FF',
                                   remote_type='chromecast')
    client._atvv_ctl = object()
    writes, edges = [], []

    async def write(data):
        writes.append(data)

    client._atvv_write = write

    def button(event, key, pressed):
        edges.append(event)
        assert client.set_mic_enabled(pressed)

    client.add_button_listener(button)
    for invalid in ('04', '04030101', '04030200', '040302ff', '04010201', '0800'):
        client._handle_atvv_status(bytes.fromhex(invalid))
    assert edges == [] and client._atvv_session_id is None
    # Physical stream must start once, with no software MIC_OPEN replacing it.
    client._handle_atvv_status(bytes.fromhex('0403020b'))
    client._handle_atvv_status(bytes.fromhex('08'))
    client._handle_atvv_status(bytes.fromhex('0403020b'))
    await asyncio.sleep(0)
    assert edges == ['press:voice'] and writes == []
    for invalid in ('00', '00ff', '000200'):
        client._handle_atvv_status(bytes.fromhex(invalid))
    assert client._atvv_voice_active and client._atvv_session_id == 11
    client._handle_atvv_status(bytes.fromhex('0002'))
    client._handle_atvv_status(bytes.fromhex('0002'))
    await asyncio.sleep(0)
    assert edges == ['press:voice', 'release:voice'] and writes == []

    # A safety stop closes the active physical session, not a different stream.
    client._handle_atvv_status(bytes.fromhex('0403020c'))
    assert client.set_mic_enabled(False)
    assert client.set_mic_enabled(False)  # Finish/drain must not send a second close.
    await asyncio.sleep(0)
    assert writes == [bytes.fromhex('0d0c')]
    assert client._atvv_voice_active and client._atvv_closing
    count = len(edges)
    client._handle_atvv_status(bytes.fromhex('0002'))
    assert len(edges) == count + 1 and edges[-1] == 'release:voice'
    assert not client._atvv_voice_active and not client._atvv_closing

    # Retain on-request fallback for remotes that do not start hold-to-talk.
    client._handle_atvv_status(bytes.fromhex('08'))
    await asyncio.sleep(0)
    assert writes[-1] == bytes.fromhex('0c0002')
    client._handle_atvv_status(bytes.fromhex('04000200'))
    assert client.set_mic_enabled(False)
    await asyncio.sleep(0)
    assert writes[-1] == bytes.fromhex('0d00')
    client._handle_atvv_status(bytes.fromhex('0002'))
    client._handle_atvv_status(b'')
    client._handle_atvv_status(b'\x04')
    assert not client._atvv_voice_active


if __name__ == '__main__':
    asyncio.run(check())
    print('ATVV physical/on-request session checks passed')
