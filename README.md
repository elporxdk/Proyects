# MediBot Repository

Welcome to the **MediBot** codebase repository. Below is an overview of the folder structure and components:

* **`firmware/`**: Arduino source code and firmware versions.
* **`herramientas/`**: Simulation and debugging tools for offline hardware testing.
* **`legado/`**: Legacy code and previous script implementations.
* **`medibot/`**: Core scripts for Raspberry Pi (Raspbian) and communication logic with Arduino.
* **`pruebas/`**: Test scripts and debugging modules.
* **`verificador-whatsapp/`**: WhatsApp bot integration powered by the Claude API.

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
