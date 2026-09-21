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

## Y que nada bloquee dentro de una sección crítica

`comprobar_criticas.py` recorre los dos sketches y comprueba que entre
`portENTER_CRITICAL` y `portEXIT_CRITICAL` **sólo haya asignaciones a
memoria**.

En un ESP32, `portENTER_CRITICAL` corta las interrupciones del núcleo. Si ahí
dentro se llama a algo que pide un mutex o que espera, el núcleo se queda
colgado con las interrupciones apagadas y a los 300 ms la placa se reinicia
sola:

```
Guru Meditation Error: Core  0 panic'ed (Interrupt wdt timeout on CPU0)
```

Pasó de verdad, y en los dos firmwares a la vez:

```cpp
portENTER_CRITICAL(&g_vitalsMux);
g_net.rssi = (int8_t)WiFi.RSSI();     // <-- pide el mutex del driver de WiFi
portEXIT_CRITICAL(&g_vitalsMux);
```

El triaje se reiniciaba en bucle justo al conectarse a la WiFi, siempre
después de `[RED] Conectando a MEDIBOT`. **El compilador no lo ve**, y el banco
tampoco: en el PC no hay watchdog de interrupciones, así que el caso pasaba
verde. Por eso hace falta un comprobador estático aparte.

Regla: el dato se lee ANTES, en una variable local, y dentro del bloqueo sólo
se copia.

## Y que las APIs del core ESP32 existan

`correr.sh` compila los sketches con las **dos** ramas del core (2.x y 3.x,
porque el código elige API con `#if`) y luego pasa `comprobar_api_esp32.py`,
que contrasta cada método usado sobre `MDNS`, `WiFi`, `HTTPClient` y
`Preferences` contra las **cabeceras de verdad** de arduino-esp32, bajadas y
cacheadas en `.libs/`.

Hace falta porque compilar contra los sustitutos de `shim/` no demuestra nada
si un sustituto tiene un método que el core real no tiene. Pasó exactamente
eso con mDNS:

```
core 2.x:  MDNS.IP(i)
core 3.x:  MDNS.address(i)   -> 'class MDNSResponder' has no member named 'IP'
```

El banco lo daba por bueno y el fallo salía en el IDE. Sin red, el comprobador
avisa y no falla, para poder trabajar sin conexión.

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
| `busalaire` | el sensor no está cableado y el bus flota: el equipo dice que es un cable suelto (no sólo "no detectado") y sigue usable |
| `buscorto` | SDA o SCL tocando GND: lo distingue del caso anterior y lo dice |
| `tecladosuelto` | GPIO34 al aire: **no** abre el asistente, **no** navega solo, y al enchufar el teclado la selección sigue donde estaba |
| `web` | la IP del ESP32 sirve la página de configuración entera, `/api` la da en JSON, y **ambas siguen respondiendo mientras el equipo mide** |
| `calibruido` | calibrar con ±55 mV de ruido: centra los 4 botones con ≤2 mV de error, los rangos salen de ±325 mV o más, y **los cuatro botones siguen funcionando con el ruido puesto** |
| `ruidoteclado` | **el bloqueo de la cara**: teclado conectado y ADC ruidoso (lo que mete el WiFi en la placa real) → no se da por desconectado, el menú responde y OK sobre la cara arranca la medida |
| `botonmedir` | el pulsador físico de MEDIR (GPIO32) arranca el chequeo desde la cara, no interrumpe una medida en curso, el resultado tiene su página en QR, y el botón *Medir* de la web también arranca |
| `qr` | Menú → Código QR codifica la web de este ESP32 y, en la otra página, la dirección de MEDIBOT |
| `panel/calibruido` | lo mismo en el firmware del panel |
| `panel/normal`, `panel/botonpulsado`, `panel/tecladosuelto` | lo mismo en el firmware del panel |

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
