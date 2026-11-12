# Amazon Remote → ESPHome → Home Assistant

Puente Bluetooth para usar los botones de un mando Amazon Fire TV con Home
Assistant. Probado con un ESP32-S3 Zero (4 MB de flash y 2 MB de PSRAM) y un mando
Amazon 0171:042F.

## Funciona

- Emparejamiento BLE con bonding y sin PIN.
- Informes HID de navegación, multimedia, voz y accesos a aplicaciones.
- Sensor **Último botón**, con estados `press:up`, `release:up`, etc.
- Web local v3 de ESPHome con logs en directo.
- Blueprint para asignar acciones a 21 teclas, soltar Voz y soltar cualquier tecla.
- OTA con contraseña, reinicio normal/seguro, diagnóstico Wi-Fi y AP de rescate.

Esta rama añade **Assist nativo por pulsación de Alexa**: F2 controla el micrófono,
F0 entrega Opus y el ESP32 lo convierte a PCM de 16 kHz mono. Implementación
compilada y captura/conversión PCM verificadas con el mando real, sin errores de
decodificación ni pérdidas en las pruebas. El pipeline completo de Assist sigue
en validación. IR queda fuera de alcance.
La reconexión tras dormir/reiniciar necesita más pruebas; una primera pulsación
puede servir solo para despertar el mando.

## Configuración

1. Copia `secrets.example.yaml` a `secrets.yaml`, completa los valores y ejecuta
   `chmod 600 secrets.yaml`. No publiques este archivo.
2. En `amazon-remote.yaml`, cambia la MAC del mando en **ambos** sitios:
   `ble_client.mac_address` y el array `remote` de la lambda Wi-Fi.
3. Compila y sube el firmware con ESPHome 2026.10.2:

   ```sh
   docker run --rm -v "$PWD:/config" ghcr.io/esphome/esphome:2026.10.2 \
     compile amazon-remote.yaml
   docker run --rm --device=/dev/ttyACM0 -v "$PWD:/config" \
     ghcr.io/esphome/esphome:2026.10.2 \
     upload amazon-remote.yaml --device /dev/ttyACM0
   ```

   Ajusta el puerto USB si es necesario. Si la placa permanece en modo descarga,
   pulsa RESET sin mantener BOOT.
4. Mantén Home unos 10 segundos para emparejar el mando. Emparejarlo con otro
   host puede sustituir el vínculo anterior.
5. Abre `http://amazon-remote-bridge.local/` o la IP asignada por DHCP. En HA añade
   la integración ESPHome usando esa IP y el puerto 6053.
6. Instala el [blueprint de botones](blueprints/README.md).

**Durante el desarrollo, la API de HA no usa cifrado ni contraseña y la web no
requiere autenticación. Úsalo solo en una LAN de confianza; no expongas los
puertos a Internet.** OTA y el AP de rescate sí tienen contraseña.

Para actualizar por OTA, utiliza `upload amazon-remote.yaml --device <IP>`.
Comprueba la entidad **ESPHome Version** tras actualizar: durante las pruebas
hubo cargas OTA aceptadas que dejaron ejecutándose el firmware anterior; la
carga por USB permitió instalar la versión nueva.

## Assist: pulsar para hablar

1. Actualiza el firmware y comprueba que HA descubre el satélite Assist.
2. En la página del dispositivo de ESPHome en HA, selecciona el asistente/pipeline
   deseado, con reconocimiento de voz y TTS configurados.
3. Deja vacías las acciones **Voz — pulsar / soltar** del blueprint si utilizas
   este control directo del firmware, para no duplicar acciones en HA.
4. Mantén Alexa pulsado, espera a **Assist Status: listening**, habla y suelta.
   La grabación tiene un límite de seguridad de 30 segundos. Al soltar se envía
   `VoiceAssistantAudio.end`: `voice_assistant.stop` abortaría el pipeline en HA
   y descartaría la transcripción.
5. Revisa **Assist Transcript**, **Assist Response** y los logs de la web. No hay
   altavoz conectado: esta configuración no reproduce la respuesta en el mando.

El micrófono solo se habilita durante una sesión iniciada por el botón. Si HA no
está conectado al satélite, la pulsación avisa `not_connected` sin grabar. Los
logs muestran el primer paquete Opus, los frames decodificados, pérdidas y pico
PCM para poder diagnosticar silencio o problemas de transporte. La web y la API
sin autenticar también exponen las transcripciones: mantén la red de confianza.

## Notas de hardware

BLE se activa después de conectar el Wi-Fi. Los parámetros de conexión del
mando son 15 ms, latencia 4 y timeout 4 s, tomados del bridge de referencia.
Una primera placa dio fallos de autenticación Wi-Fi; una segunda placa idéntica
funcionó. Esto no demuestra un defecto físico ni una causa de coexistencia BLE.

## Pruebas

```sh
g++ -std=c++17 -Wall -Wextra -Werror tests/button_decoder.cpp \
  -o /tmp/amazon-remote-test && /tmp/amazon-remote-test

g++ -std=c++17 -Wall -Wextra -Werror tests/audio_packet_queue.cpp \
  -o /tmp/amazon-audio-test && /tmp/amazon-audio-test

docker run --rm -v "$PWD:/config" --entrypoint python \
  ghcr.io/esphome/esphome:2026.10.2 /config/tests/test_blueprint.py

docker run --rm -v "$PWD:/config" --entrypoint python \
  ghcr.io/esphome/esphome:2026.10.2 /config/tests/test_assist_protocol.py
```

## Referencias

- [atv-remote-hid-bridge](https://github.com/hkfuertes/atv-remote-hid-bridge):
  configuración de seguridad y tiempos BLE.
- [fire-remote-client](https://github.com/justanotheruseer/fire-remote-client):
  documentación del protocolo e identificación de teclas HID.
