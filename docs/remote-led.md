# Investigación del LED del mando

Estado: **sin comando de LED identificado**. Solo se dispone del mando y del
ESP32; no hay Fire TV para capturar tráfico de referencia. No se han probado
escrituras propietarias desconocidas ni modificado tablas/configuración del mando.

## Evidencia leída del mando

- Mapa HID completo: característica `1812/2A4B`, handle `0x002E`, **149 bytes**.
- SHA-256: `ab7d72f6bf71722076be91dfcc52954784a6298b998a32d82e4706d302fa5083`.
- No aparece la página de usos estándar **LEDs (`0x0008`)**.

| ID | Dirección | Página de usos | Bytes de datos declarados |
| --- | --- | --- | --- |
| `01` | entrada | teclado `0007` | 3 |
| `02` | entrada | consumer `000C` | 4 |
| `F0` | entrada | propietaria `00FF` | 80 |
| `F1` | entrada | propietaria `00FF` | 3 |
| `F2` | salida | propietaria `00FF` | 1 |
| `F3` | salida | propietaria `00FF` | 10 |
| `EF` | entrada | propietaria `00FF` | 3 |
| `03` | entrada | controles genéricos `0006` | 1 |

**Declaración HID no equivale a endpoint GATT.** Los descriptores Report Reference
`2908` de las cinco características Report `2A4D` solo identifican:

- `0030`: `01`, entrada.
- `0034`: `F0`, entrada.
- `0038`: `F2`, salida; control de micrófono ya conocido.
- `003B`: `02`, entrada.
- `003F`: `EF`, entrada.

No se encontró una característica Report con referencia `F3`. Por tanto, ni su
finalidad ni una ruta para escribirlo están establecidas. **No enviar 10 bytes a
F2 ni anteponer F3 a ese control por suposición.**

Servicios propietarios observados (UUID abreviado, no comandos):

| Servicio | Características | Propiedades GATT |
| --- | --- | --- |
| `FE151500-5E8D-11E6-8B77-86F30CA893D3` | `FE151501`, `FE151502`, `FE151503`, `FE151504` | `3A`, `0A`, `3A`, `02` |
| `5DE20000-5E8D-11E6-8B77-86F30CA893D3` | `5DE24A17`, `5DD24A18`, `5DE24A19` | `08`, `10`, `12` |
| `CFBFA000-762C-4912-A043-20E3ECDE0A2D` | `CFBFA001`, `CFBFA003`, `CFBFA002`, `CFBFA004` | `18`, `08`, `14`, `14` |

Las propiedades indican operaciones posibles, **no su semántica**. La existencia
de una característica escribible no prueba que controle el LED.

## Comprobaciones reproducibles

```sh
python3 tools/inspect_hid_map.py --self-test
python3 tools/inspect_hid_map.py diagnostics/remote-hid-map-1.bin
```

La autoprueba incluye el mapa real completo, sin identificadores personales,
y casos de página LEDs, pila de globales y datos truncados. El binario y los logs
locales permanecen en `diagnostics/` (excluido de Git).

El diagnóstico de esta rama enumera metadatos GATT y lee únicamente el mapa HID.
Las suscripciones y el control F2 existentes para botones/micrófono se conservan.
Se evita volver a parsear las características que ya están en la caché de
ESPHome: hacerlo duplicaba los informes durante la primera captura. La corrección
compila y está flasheada; su nueva captura de reconexión está pendiente.

## Referencias y límites

- [fire-remote-client: remotes.md](https://github.com/justanotheruseer/fire-remote-client/blob/35ba9e05febe12392ca5ee7115fd3c078571001a/docs/remotes.md)
  describe estos mismos IDs adicionales, pero no asigna a F3 un comando de LED.
- No se encontró un comando de LED en los repositorios de referencia revisados.
  Esto **no demuestra que sea imposible**: puede ser una función propietaria o
  autónoma del firmware.
- Sin un comando documentado, firmware analizable o tráfico de referencia, no hay
  base para integrar el LED con los estados de Assist. No se hará fuerza bruta.
