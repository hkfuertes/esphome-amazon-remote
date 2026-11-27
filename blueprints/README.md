# Remote buttons in Home Assistant

1. Install the blueprint using either method:
   - In Home Assistant, go to **Settings → Automations & scenes → Blueprints →
     Import Blueprint** and import this public URL:
     https://github.com/hkfuertes/ha-bluetooth-tv-remotes/blob/main/blueprints/tv_remote_buttons.yaml
   - Or copy `tv_remote_buttons.yaml` to
     `/config/blueprints/automation/bluetooth_tv_remotes/buttons.yaml` in Home Assistant,
     then reload blueprints.
2. Create an automation using **Bluetooth TV Remotes — Buttons**.
3. Select the remote's **Last Button** sensor from the `bluetooth_tv_remotes` integration and configure the desired actions.
   Leave any actions you do not need empty.

The blueprint includes the buttons recognized by the decoder, a dedicated Voice
release action, and a general action for releasing any button. Actions can use
`{{ button }}` and `{{ button_event }}`. Releasing Voice runs both release actions
if configured. Native Assist already handles voice actions: when using Assist,
leave the blueprint's **Voice — press** and **Voice — release** actions empty to
avoid duplicate control. The blueprint itself does not transport audio.

Only buttons sent by the remote over Bluetooth work. App names may vary by
model. No artificial repeats or long presses are generated: actions respond to
`press:` / `release:` sensor state changes and ignore startup/disconnection states.

Local check (requires PyYAML and Jinja2):

```sh
python3 tests/test_blueprint.py
```
