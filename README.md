# MEDIBOT

Código del proyecto MEDIBOT. Qué hay en cada carpeta:

* **`firmware/`**: el código de las placas (Arduino / ESP32).
* **`herramientas/`**: utilidades de simulación y depuración sin hardware.
* **`legado/`**: versiones antiguas de los scripts.
* **`medibot/`**: los scripts de la Raspberry Pi y la comunicación con el Arduino.
* **`pruebas/`**: bancos de pruebas y módulos de depuración.
* **`verificador-whatsapp/`**: comprueba con `whatsapp-web.js` qué números de la lista de doctores tienen WhatsApp.

## Qué se graba en cada placa

Cada carpeta de `firmware/` es un programa **independiente** para un aparato
distinto. Se abre su `.ino` en el IDE de Arduino y se graba; no hay que juntar
ficheros de varias carpetas.

| Quiero grabar... | Abro este fichero |
|---|---|
| El **triaje** (pantalla + MAX30102 + teclado + WiFi) | `firmware/medibot_triaje/medibot_triaje.ino` |
| El **panel** (otro ESP32: QR y datos de MEDIBOT) | `firmware/medibot_panel/medibot_panel.ino` |
| El **chasis** (motores) | `firmware/medibot_movimiento/medibot_movimiento.ino` |
| El **pastillero** | `firmware/pillbox_*/…` |

**`pruebas/banco_firmware/` NO se graba en ninguna placa.** Es un programa de
PC que ejecuta los sketches con el hardware simulado para probarlos sin placa
(ver `pruebas/banco_firmware/LEEME.md`). Si intentas compilarlo en el IDE, te
avisa y no te deja.

`medibot/` es Python y corre en la Raspberry Pi, no en las placas.
