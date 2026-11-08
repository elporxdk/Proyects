# MEDIBOT

Código del proyecto MEDIBOT. Qué hay en cada carpeta:

* **`dotly/`**: DOTLY, para aprender braille y el alfabeto manual (ESP32-S3 + LCD 16x2 + 5 botones + página web). Ver `dotly/LEEME.md`.
  * **`dotly/web/`**: la web de DOTLY para explorar, traducir y aprender braille. Funciona sola, sin hardware, y se puede conectar a un ESP32. Ver `dotly/web/LEEME.md`.
* **`firmware/`**: el código de las placas (Arduino / ESP32).
* **`herramientas/`**: utilidades de simulación y depuración sin hardware.
* **`legado/`**: versiones antiguas de los scripts.
* **`medibot/`**: los scripts de la Raspberry Pi y la comunicación con el Arduino.
* **`paleta/`**: la paleta de colores de la web de MEDIBOT, la que usan las interfaces de la cámara y del pastillero, en HTML y en imagen.
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
| **DOTLY** (braille: LCD 16x2 + 5 botones + WiFi) | `dotly/dotly.ino` (con `pagina_web.h` al lado) |

`dotly/web/` tampoco se graba: es una web que se abre en el navegador (o se sube
a la memoria LittleFS del ESP32).

**`pruebas/banco_firmware/` y `pruebas/banco_dotly/` NO se graban en ninguna
placa.** Son programas de PC que ejecutan los sketches con el hardware simulado
para probarlos sin placa (ver el `LEEME.md` de cada uno). Si intentas
compilarlos en el IDE, te avisan y no te dejan.

`medibot/` es Python y corre en la Raspberry Pi, no en las placas.

## Instalar MEDIBOT en la Raspberry Pi

En la Pi (Raspberry Pi OS con escritorio y conexión a internet), abre una
terminal y pega:

```bash
curl -fsSL https://raw.githubusercontent.com/elporxdk/Proyects/main/instalar.sh | bash
```

Descarga el código en `~/Proyects`, instala todo lo necesario con `apt`
(Flask, pyserial, OpenCV con reconocimiento facial, Tkinter, Pillow, audio,
openssl), da permisos para el Arduino, las cámaras y el audio, y crea el icono
**MEDIBOT** en el escritorio y en el menú, y la orden `medibot` para la
terminal. Al final comprueba que todo está en su sitio. Si pide reiniciar,
reinicia (`sudo reboot`).

* **Actualizar:** vuelve a ejecutar el mismo comando (o `bash ~/Proyects/instalar.sh`).
* **Que se abra solo al encender la Pi:** `bash ~/Proyects/instalar.sh --autoarranque`
  (`--sin-autoarranque` lo quita).

Se usa `apt` y no `pip` porque en Raspberry Pi OS `pip install` fuera de un
entorno virtual da error, y porque el `opencv-python` de pip no trae `cv2.face`
(el reconocimiento facial).

**Ojo al clonar a mano:** la rama por defecto de GitHub es `web` (la página
web), que no trae `medibot/`. El código de la Pi está en `main`:
`git clone -b main https://github.com/elporxdk/Proyects.git`.
