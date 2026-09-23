# Banco de pruebas de DOTLY

**Esto NO se graba en ninguna placa.** Es un programa de PC que compila
`dotly/dotly.ino`, el mismo que se graba, contra unos sustitutos del hardware
(`shim/`) y lo maneja como lo haría una persona:

* **Pulsa botones** poniendo en el pin del teclado la tensión de cada uno
  (0 / 460 / 990 / 1650 / 2440 mV, como un ADKeyboard a 3V3). Puede añadir
  ruido o hacer que la tensión pase un momento por la de otros botones, como
  al pulsar de verdad.
* **Lee la pantalla** decodificando los píxeles de los caracteres propios del
  LCD: las celdas braille se leen punto a punto, no se fía de lo que el
  firmware "cree" que ha pintado.
* **Usa la página** por HTTP de verdad: el sustituto del WebServer abre un
  socket en 127.0.0.1 y el firmware lo atiende desde su `loop()`, igual que
  en la placa.
* **Recuerda** la memoria NVS en un fichero, para probar lo que se guarda y lo
  que sobrevive a un reinicio.

## Uso

```sh
./correr.sh            # todo
./correr.sh letras     # un solo caso
```

Hace falta `g++` y `python3`. Para la prueba en el navegador, `node` con
Playwright y Chromium.

## Qué comprueba

Antes de compilar:

* **Prototipos del IDE** (`../banco_firmware/comprobar_prototipos.py`): el IDE
  de Arduino inserta los prototipos antes de la primera función. Un tipo
  declarado después de esa función rompe la compilación en el IDE, aunque en
  el PC compile.
* **APIs reales** (`comprobar_api.py`): cada método que el sketch llama sobre
  `WiFi`, `webServer`, `dnsServer`, `prefs`, `ESP`, `Wire` y `lcd` tiene que
  existir en las cabeceras reales de arduino-esp32 2.0.17 y 3.3.0 y de
  LiquidCrystal_I2C. Si no, un sustituto demasiado generoso lo taparía.

Casos (se apoyan en la memoria que deja el anterior, como una placa que se usa
día tras día):

| Caso | Qué hace |
|---|---|
| `fabrica` | placa nueva: bienvenida, se abre el asistente, se calibran los 5 botones y los rangos no se solapan |
| `mantenida` | encender con SW1 pulsado abre el asistente aunque ya haya calibración |
| `menubase` | las pantallas y teclas del código base (`SELECCIONA` / `> BLOQUE 1`, `BLOQUE 1` / `A B C D E F`), el fallo del teclado bloqueado, pulsación larga y autorrepetición |
| `letras` | los 53 signos del firmware (letra, celda y puntos) contra una tabla de la signografía española escrita aparte en el banco |
| `escribir` | el editor de puntos, el signo de número, una combinación que no existe, el borrado |
| `retos` | los tres retos respondidos leyendo la pantalla (uno mal a propósito) y su cuenta en el progreso |
| `palabras` | lista nueva desde la web con ñ, tildes y números; los glifos Ñ y Ú; los límites de tamaño |
| `senas` | la descripción que se desplaza, cambiarla desde la web, el deletreo automático |
| `web` | la página entera, que cada `/api/` que usa exista, el portal cautivo, la pizarra, los ajustes de red, calibrar desde la web |
| `persistencia` | tras reiniciar siguen la red nueva, los ajustes, una seña propia y el progreso |
| `ruido` | 10 pulsaciones con ±40 mV de ruido y rampa dan exactamente 10 eventos; `c` por el Monitor Serie |
| `sinteclado` | pin al aire: no se abre el asistente, el ruido no se toma por pulsaciones y la web funciona |
| navegador | `e2e_pagina.js`: la página en Chromium (móvil y escritorio, claro y oscuro), sin scroll horizontal, con los botones, el traductor, la máquina Perkins, las señas, los modos, el progreso y los ajustes |

En todos los casos se comprueba además que nunca se escribe en el LCD con el
puntero en la CGRAM (después de `createChar()` hay que hacer `setCursor()`) y
que nunca aparece en pantalla un carácter propio desconocido.

Las capturas de la prueba del navegador quedan en `.build/capturas/`.

## Modo servidor

```sh
DOTLY_NVS=.build/nvs DOTLY_PUERTO=8080 ./.build/banco_dotly servidor
```

Deja DOTLY encendido (con el teclado ya calibrado) y la página en
http://127.0.0.1:8080 para abrirla en el navegador. El reloj va 5 veces más
rápido que el real.
