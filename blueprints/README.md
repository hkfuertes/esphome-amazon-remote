# Remote buttons in Home Assistant

1. Install the blueprint using either method:
   - In Home Assistant, go to **Settings → Automations & scenes → Blueprints →
     Import Blueprint** and import this public URL:
     https://github.com/hkfuertes/esphome-amazon-remote/blob/main/blueprints/amazon_remote_buttons.yaml
   - Or copy `amazon_remote_buttons.yaml` to
     `/config/blueprints/automation/amazon_remote/buttons.yaml` in Home Assistant,
     then reload blueprints.
2. Create an automation using **Amazon Remote — ESPHome Buttons**.
3. Select the bridge's **Last Button** sensor and configure the desired actions.
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

Local check (using the dependencies already included in the ESPHome image):

```sh
docker run --rm -v "$PWD:/config" --entrypoint python \
  ghcr.io/esphome/esphome:2026.10.2 /config/tests/test_blueprint.py
```
