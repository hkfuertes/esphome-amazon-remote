# Botones del mando en Home Assistant

1. Copia `amazon_remote_buttons.yaml` a:
   `/config/blueprints/automation/amazon_remote/buttons.yaml` en Home Assistant.
2. En **Ajustes → Automatizaciones y escenas → Blueprints**, recarga los blueprints
   y crea una automatización con **Amazon Remote — botones ESPHome**.
3. Selecciona el sensor **Último botón** del puente y configura las acciones deseadas.
   Deja vacías las que no necesites.

Incluye las teclas conocidas por el decodificador, una acción específica al soltar
Voz y una acción general al soltar cualquier tecla. Las acciones pueden usar
`{{ button }}` y `{{ button_event }}`. Al soltar Voz se ejecutan ambas acciones de
liberación si están configuradas. La opción Voz solo asigna acciones: todavía no
transmite audio a Assist.

Solo funcionan las teclas que el mando envíe por Bluetooth. Los nombres de las
aplicaciones pueden variar según el modelo. No se generan repeticiones ni
pulsaciones largas artificiales: se actúa sobre los cambios `press:` / `release:`
del sensor, y se ignoran los estados de arranque/desconexión.

Comprobación local (con las dependencias ya presentes en la imagen ESPHome):

```sh
docker run --rm -v "$PWD:/config" --entrypoint python \
  ghcr.io/esphome/esphome:2026.10.2 /config/tests/test_blueprint.py
```
