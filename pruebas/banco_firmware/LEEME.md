# Banco de pruebas del firmware (sin placa)

Ejecuta los sketches de `firmware/` **en el PC**, con el MAX30102 simulado, y
comprueba el recorrido completo de la interfaz: menú, auto-chequeo, resultados,
historial y asistente de calibración del teclado.

```sh
./correr.sh              # todos los casos
./correr.sh normal       # un solo caso del triaje
```

Necesita `g++` y las librerías `SparkFun MAX3010x` y `ArduinoJson`. Usa las
del IDE (`~/Arduino/libraries`); si no las encuentra, las clona en `.libs/`.
Con `MEDIBOT_LIBS=/ruta/a/libraries` se le puede indicar otra carpeta.

## Qué es real y qué está simulado

| Pieza | Qué se usa aquí |
|---|---|
| `medibot_triaje.ino`, `medibot_panel.ino` | **el código de verdad**, sin tocar |
| `spo2_algorithm.cpp` (Maxim/SparkFun) | **el de verdad**, de la librería instalada |
| Arduino, U8g2, Wire, Preferences, WiFi… | sustitutos mínimos en `shim/` y `shim_panel/` |
| MAX30102 | simulado: genera una PPG sintética con BPM y SpO2 elegidos |
| Pantalla | no dibuja, pero **anota el texto** de cada frame, y eso es lo que se comprueba |
| Teclado | la prueba fija la tensión del pin, como si pulsara de verdad |

El reloj va acelerado (`g_speedup` en `shim/shim.cpp`), así que una medida de
12 s tarda algo más de un segundo real.

## Casos

| Caso | Qué comprueba |
|---|---|
| `calibrar` | sin calibración guardada, el asistente se abre solo, mide los 4 botones útiles, descarta MENU (a 5 V satura el ADC) y deja el menú navegable |
| `yacalibrado` | con la calibración guardada no se repite el asistente y los botones siguen funcionando |
| `normal` | chequeo completo: el pulso y la SpO2 mostrados coinciden con los simulados y quedan en el historial |
| `sindedo` | sin dedo cancela con un motivo claro, y el **reintento inmediato** no hereda el fallo anterior |
| `dedofuera` | si se levanta el dedo a mitad, lo detecta, se recupera y termina bien |
| `sinsensor` | sin MAX30102 lo avisa al arrancar y no deja empezar una medida |
| `panel` | el mismo auto-chequeo en el firmware del panel |

## Por qué existe

Este banco es el que destapó que el pulso salía mal: con una PPG sintética de
**72 BPM** el firmware mostraba **150**. La causa era `checkForBeat()` de
`heartRate.h`, que trunca la muestra IR a 16 bits cuando el sensor entrega
entre 60.000 y 250.000 cuentas, y que además cuenta la onda dicrota como un
latido más. Con el detector propio la lectura es correcta entre 48 y 170 BPM.

Para volver a ver ese fallo:

```sh
git show <commit-anterior>:firmware/medibot_triaje/medibot_triaje.ino > /tmp/viejo.ino
# y compilar /tmp/viejo.ino en lugar del sketch actual
```
