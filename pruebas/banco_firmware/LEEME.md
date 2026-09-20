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

## Antes de compilar: los prototipos del IDE

`correr.sh` empieza pasando `comprobar_prototipos.py` por los dos sketches.

El IDE de Arduino **no compila el `.ino` tal cual**: genera un prototipo de
cada función del fichero y los inserta todos juntos justo antes de la primera
función. Si un tipo propio (`struct`, `enum`) se declara *después* de ese punto
y aparece en la firma de alguna función, el prototipo generado lo usa antes de
que exista:

```
error: variable or field 'beatReset' declared void
error: 'BeatDetector' was not declared in this scope
```

...y encima señala la línea de la **definición**, que es adonde apunta el
`#line` generado, así que el mensaje despista. Compilando el `.ino` como C++
normal —que es lo que hace este banco— el fallo **no aparece**: sólo sale en el
IDE. De ahí el comprobador.

Regla: **todo tipo que se use en la firma de una función va declarado antes de
la primera función del fichero** (en MEDIBOT, en el bloque de tipos).

## Qué es real y qué está simulado

| Pieza | Qué se usa aquí |
|---|---|
| `medibot_triaje.ino`, `medibot_panel.ino` | **el código de verdad**, sin tocar |
| `spo2_algorithm.cpp` (Maxim/SparkFun) | **el de verdad**, de la librería instalada |
| Arduino, U8g2, Wire, Preferences | sustitutos mínimos en `shim/` |
| WiFi, mDNS, HTTP y la API de MEDIBOT | simulados en `shim_red/`: se puede apagar la red, quitar el mDNS, mover la Raspberry de IP o tirarle la API |
| MAX30102 | simulado: genera una PPG sintética con BPM y SpO2 elegidos, y puede "colgarse" o desaparecer del bus |
| Bus I2C | simulado con su banco de registros: el escaneo y la verificación por relectura son reales |
| NVS (Preferences) | simulada en un fichero, y se puede romper para probar el aviso |
| Pantalla | no dibuja, pero **anota el texto** de cada frame, y eso es lo que se comprueba |
| Teclado | la prueba fija la tensión del pin, como si pulsara de verdad |

El reloj va acelerado (`g_speedup` en `shim/shim.cpp`), así que una medida de
12 s tarda algo más de un segundo real.

## Casos

| Caso | Qué comprueba |
|---|---|
| `calibrar` | sin calibración guardada, el asistente se abre solo, mide los 4 botones y deja el menú navegable |
| `yacalibrado` | con la calibración guardada no se repite el asistente y los botones siguen funcionando |
| `normal` | chequeo completo: el pulso y la SpO2 mostrados coinciden con los simulados y quedan en el historial |
| `sindedo` | sin dedo cancela con un motivo claro, y el **reintento inmediato** no hereda el fallo anterior |
| `dedofuera` | si se levanta el dedo a mitad, lo detecta, se recupera y termina bien |
| `sinsensor` | sin MAX30102 lo avisa al arrancar y no deja empezar una medida |
| `sensorlento` | el sensor aparece 4 s después de arrancar: se detecta solo y se puede medir **sin reiniciar** |
| `sensorcuelga` | el sensor deja de dar muestras a mitad de la medida: se reinicia solo y la medida termina |
| `diagnostico` | la pantalla de Diagnostico enseña el sensor, el bus I2C y el IR en vivo, y refleja el dedo al ponerlo |
| `sinmemoria` | con la NVS rota, el asistente avisa de que **no** se ha guardado en vez de repetirse sin explicar nada |
| `botonpulsado` | se entra al asistente **manteniendo un botón**: el reposo se mide al soltar y la tabla guardada es la buena |
| `wifi` | se conecta a la red `MEDIBOT`, localiza la Raspberry por mDNS y enseña los datos de `/api/esp32`, refrescándolos solos |
| `wifibarrido` | sin mDNS, barre la subred IP a IP comprobando identidades hasta dar con MEDIBOT |
| `sinwifi` | sin red avisa y reintenta: cuando el router aparece, se conecta **sin reiniciar** |
| `apicaida` | si la API deja de responder lo detecta, la vuelve a buscar y la recupera sola |
| `redymedida` | midiendo con la red activa: el pulso sale bien y **no se pierde ni una muestra** |
| `panel/normal`, `panel/botonpulsado` | lo mismo en el firmware del panel |

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
