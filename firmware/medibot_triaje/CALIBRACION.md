# MEDIBOT v6.1 — Guía de calibración

Triaje con **un solo sensor** —el MAX30102 (pulso y SpO₂)— más conexión WiFi
con MEDIBOT. Todo lo ajustable está en el bloque **`1. CONFIGURACION`** del
sketch `medibot_triaje.ino`. Este documento explica qué medir y dónde ponerlo.

> El equipo **no mide temperatura**. Ni la señal PPG (rojo/IR), ni el pulso, ni
> la SpO₂ contienen información de temperatura corporal: cualquier valor
> derivado de ellas sería inventado. Si algún día se añade un termómetro de
> verdad (MLX90614, MAX30205, DS18B20…), será con su propio sensor y su propia
> fase de medida.

## Si algo no funciona, empieza por aquí

El firmware se autodiagnostica. Tienes tres sitios donde mirar, de menos a más
detalle:

**1. La pantalla de arranque** dice si el sensor responde, cuántos botones
tiene el teclado y si la memoria funciona.

**2. Menú → `Diagnostico`.** Es la pantalla clave:

```
DIAGNOSTICO
Sensor: OK 0x15 a 400kHz      <- el chip responde y a qué velocidad
I2C: 1 disp. (1o 0x57)        <- cuántos dispositivos hay en el bus
IR: 74321  DEDO               <- la señal EN VIVO
Tecla: 3200 mV (reposo 3200)
Memoria: OK  Reinicios: 0     <- veces que hubo que reiniciar el sensor
```

Con el dedo fuera el IR baja de 10.000 y al apoyarlo sube por encima de
30.000. Si el IR **no se mueve**, el problema es el sensor o el cableado, no tu
dedo. Si dice `IR: --` o `esperando muestras`, el sensor no está entregando
datos.

**3. El Monitor Serie a 115200.** Al arrancar imprime el escaneo del bus, el
identificador del chip, a qué velocidad ha enganchado y si la configuración se
ha podido releer. Y en marcha, **cada tecla que el firmware da por pulsada**
(`[TECLA] OK (1547 mV)`) y **cada cambio de pantalla** (`[UI] -> menu`): si el
equipo "hace cosas raras", esas dos líneas dicen si es el teclado o el programa.

### Los fallos típicos y qué significan

| Lo que ves | Qué pasa |
|---|---|
| `I2C: cable suelto/sin 3V3` | Las líneas están **al aire**: no hay resistencias de pull-up, o sea que el módulo no está enchufado o no le llega corriente. Revisa VIN→3V3, GND→GND, SDA→21, SCL→22 |
| `I2C: linea a 0V (corto)` | SDA o SCL está clavada a masa: un cable tocando GND, o un módulo que ha bloqueado el bus |
| `I2C: hay 3V3, SDA/SCL?` | El módulo **sí** tiene corriente (hay pull-ups) pero nadie contesta en 0x57: lo más probable es SDA y SCL cambiadas de sitio, o que el chip no sea un MAX30102 |
| `[I2C] N direccion(es) que cambian en cada vuelta` | Direcciones fantasma (0x03, 0x46, 0x51…) distintas en cada escaneo. **No hay ningún dispositivo**: es ruido de un pin flotando |
| `Tecla: SIN CONECTAR (GPIO34)` | El teclado no está enchufado. Mientras lo esté, se ignoran las pulsaciones para que el equipo no navegue solo |
| La cara dice `Teclado sin conectar (GPIO34)` | Lo mismo, visto desde la cara de reposo. Solo salta si la lectura **baila y además anda cerca de 0 V**: un teclado conectado descansa a ~3,2 V y no puede darse por desconectado por mucho ruido que meta el ADC |
| El menú no responde pero el asistente sí mide los botones | Era un fallo del firmware (ya corregido): la detección de "desconectado" miraba solo el ruido y, en la placa real con el WiFi encendido, saltaba con el ruido normal del ADC y silenciaba el teclado. Ahora exige además que la lectura esté cerca de 0 V |
| `Sensor: MAX30100 no vale` | Es el chip antiguo (ID `0x11`). La librería MAX3010x no lo soporta: hace falta un MAX30102 o MAX30105 |
| `Sensor: NO DETECTADO` con `I2C: 1 disp.` | Hay algo en el bus pero no contesta como MAX30102: mal contacto o módulo defectuoso |
| `[MAX] Sin respuesta a 400 kHz` | Cables largos o sin pull-ups. El firmware baja solo a 100 kHz y sigue |
| `[MAX] La configuracion NO se aplico` | La escritura se perdió; el firmware lo reintenta solo |
| `Reinicios: N` con N creciendo | El sensor se cuelga: cable flojo o alimentación justa |
| `Memoria: FALLO` | La NVS no admite escrituras: la calibración del teclado no se guardará |

El equipo **no se queda colgado** en ninguno de esos casos: si el sensor no
aparece al arrancar se sigue buscando en segundo plano (conectarlo con el
equipo encendido basta, no hace falta reiniciar), y si deja de dar muestras a
mitad de una medida se reinicia solo y continúa. La búsqueda empieza cada 3 s y
se va espaciando hasta 30 s mientras el sensor siga sin aparecer: insistir cada
3 s eternamente no lo trae de vuelta y sólo llena el Monitor Serie.

### Cómo sabe el firmware si un cable está suelto

Antes de hablar por el bus, mira el **estado eléctrico** de SDA y SCL leyéndolas
como entradas normales:

| Lo que mide | Qué significa |
|---|---|
| Altas y quietas sin ayuda | Hay pull-ups → el módulo está conectado y con corriente |
| Bailando (ni altas ni bajas) | No hay pull-ups → **nada conectado, o sin corriente** |
| Bajas incluso con el pull-up interno del ESP32 | Algo las clava a 0 V → **cortocircuito** |

Con el **teclado** hace lo mismo pero a su manera: cada lectura son 9 muestras
seguidas del ADC tomadas en menos de un milisegundo. Con el teclado enchufado
salen casi idénticas; con el GPIO34 al aire salen desperdigadas. Si casi todas
las lecturas de una ventana salen desperdigadas, el firmware da el teclado por
desconectado y **deja de aceptar pulsaciones**.

Eso último importa más de lo que parece: el GPIO34 es sólo entrada y no tiene
pull-up interno, así que sin nada enchufado flota cerca de 0 V — justo el rango
del botón ABAJO. Sin esta comprobación el equipo se abre el asistente solo y
después va bajando por los menús como si hubiera un fantasma pulsando teclas.

### Si se reinicia en bucle: `Guru Meditation Error`

Si el Monitor Serie enseña esto una y otra vez:

```
Guru Meditation Error: Core  0 panic'ed (Interrupt wdt timeout on CPU0)
...
Rebooting...
rst:0xc (SW_CPU_RESET)
```

significa que **un núcleo se quedó colgado con las interrupciones apagadas**.
En un ESP32 eso pasa cuando entre `portENTER_CRITICAL` y `portEXIT_CRITICAL`
se llama a algo que puede esperar: `WiFi.*`, `MDNS.*`, `HTTPClient`, `Wire.*`,
`Serial.*`, `delay()`… Ahí dentro sólo pueden ir **asignaciones a memoria**. Si
hace falta un dato del WiFi, se lee ANTES en una variable local:

```cpp
const int8_t rssi = (int8_t)WiFi.RSSI();      // fuera del bloqueo
portENTER_CRITICAL(&g_vitalsMux);
g_net.rssi = rssi;                            // dentro, sólo copiar
portEXIT_CRITICAL(&g_vitalsMux);
```

Este fallo **existió de verdad** en este firmware: `g_net.rssi = WiFi.RSSI()`
estaba dentro del bloqueo y el triaje se reiniciaba en bucle justo al
conectarse a la WiFi. No lo ve el compilador y no lo ve el banco de pruebas
(en el PC no hay watchdog), así que ahora lo vigila un script:

```
python3 pruebas/banco_firmware/comprobar_criticas.py firmware/medibot_triaje/medibot_triaje.ino
```

Si el equipo llega a reiniciarse por un fallo así, arranca en **MODO SEGURO**
(sin red) y la pantalla de arranque dice en qué paso murió: `Fallo en: red: wifi`.

## Si la interfaz se queda "bloqueada en la cara"

Era un fallo del firmware, ya corregido, y conviene entenderlo: el menú se caía
solo a la cara de reposo **en casi cada pulsación**, así que parecía que no
respondía.

La causa: el tiempo de inactividad se calculaba `now - lastInteraction` con
enteros **sin signo**. `lastInteraction` se apunta con `millis()` dentro del
manejo de teclas, que se lee unos microsegundos *después* que el `now` del
principio del bucle. Cuando el milisegundo cambiaba justo en medio,
`lastInteraction` quedaba 1 ms **por delante** de `now`, y la resta sin signo
se desbordaba a ~4.290 millones — muchísimo más que el tope de 30 s —, así que
el temporizador saltaba al instante y mandaba a la cara de reposo.

Ahora se calcula con guarda: si `lastInteraction` va por delante de `now`, el
tiempo inactivo es 0. (En el Monitor Serie se ve cada cambio de pantalla con
`[UI] -> menu` y cada tecla con `[TECLA] OK (1547 mV)`, por si vuelve a pasar
algo raro.)

## Cómo se empieza una medida

Hay **cuatro** formas, y todas hacen exactamente lo mismo (`empezarMedida()`):

| Desde dónde | Qué hacer |
|---|---|
| La cara de reposo | pulsar **OK**. La cara lo dice abajo: `[OK] Medir   [otro] Menu`. Cualquier otra tecla abre el menú |
| El menú | `Auto-Chequeo` → OK |
| Un **pulsador aparte** | entre **GPIO32 y GND**. Usa el pull-up interno: si no se monta, no molesta. Arranca la medida desde cualquier pantalla que no sea una medida en curso, Diagnóstico o el asistente; por Serial sale `[MEDIR] pulsado` en cada pulsación aunque se ignore |
| El navegador | botón **Medir ahora** en `http://<ip>/` |

## El código QR

> **No hay que instalar ninguna librería de QR.** El codificador va dentro del
> propio sketch, en `medibot_qr.h` / `medibot_qr.cpp`, y el IDE de Arduino los
> compila solos por estar junto al `.ino`.
>
> Va así a propósito: el core ESP32 3.x **ya trae un fichero llamado
> `qrcode.h`** (el componente de Espressif, con `esp_qrcode_generate()`), de
> modo que un `#include "qrcode.h"` se resuelve a *ése* y no a la librería de
> ricmoo aunque la instales, y el sketch no compila:
>
> ```
> error: 'QRCode' does not name a type
> error: 'qrcode_getModule' ... did you mean 'esp_qrcode_get_module'?
> ```
>
> Con el codificador dentro y con otro nombre no hay choque ni nada que
> instalar. Es la librería de Richard Moore (MIT), sin tocar salvo el nombre
> del `#include`; su licencia va íntegra en la cabecera del fichero.

Menú → **`Codigo QR`**. Dos páginas (ARRIBA/ABAJO cambia):

- **Este equipo**: la dirección de la página de configuración (`http://<ip>/`), para abrirla con el móvil sin teclearla. Si aún no hay WiFi lo dice.
- **MEDIBOT**: la dirección de la Raspberry (`http://<ip>:<puerto>`). Si aún no la ha localizado, remite a `Menú → MEDIBOT (red)`.

Y al terminar un auto-chequeo, la **tercera página** de resultados (ABAJO dos
veces) lleva el resultado en QR (`MEDIBOT 72bpm SpO2 98%`), para llevárselo en
el móvil sin apuntarlo.

Es QR versión 2 (25×25 módulos) a 2 px por módulo: 58 px, que es lo que cabe
en los 64 de alto. Si el móvil no lo lee, la pantalla es de las azules (píxel
blanco sobre fondo oscuro): pon `QR_INVERTIDO 1` y recompila.

**El límite son 32 bytes** y hay que respetarlo: pasarse **no da error** en la
librería, genera un código que se lee *mal* (el último carácter sale cambiado).
Por eso `qrGenerar()` comprueba la longitud y lo rechaza avisando por Serial
(`[QR] "..." no cabe en un QR version 2 (33 de 32 bytes)`). Las direcciones más
largas que puede producir el equipo se quedan cortas de sobra:
`http://255.255.255.255:65535` son 28.

## La configuración en el navegador

El ESP32 **sirve su propia página**. Con el equipo conectado a la red `MEDIBOT`,
escribe su IP en cualquier navegador de la misma red:

```
http://192.168.1.45/          <- la IP sale en el arranque y en Menú → MEDIBOT
http://medibot-triaje.local/  <- si tu router/sistema resuelve mDNS
```

La IP se ve en dos sitios sin tocar el cable USB: en la pantalla **Menú →
`MEDIBOT (red)` → ABAJO**, que la enseña como `Web: http://…/`, y por el Monitor
Serie al conectarse:

```
[WEB] Configuracion del equipo en http://192.168.1.45/  (o http://medibot-triaje.local/)
```

La página enseña, y se refresca sola cada 2 s sin recargar:

| Bloque | Qué sale |
|---|---|
| Sensor de pulso | si responde, chip y revisión, velocidad del bus, patillas, infrarrojo en vivo, reinicios |
| Medida | pulso y SpO₂ en vivo, progreso, si la señal es fiable, y el historial |
| Red | SSID, IP, puerta de enlace, MAC, señal, en qué punto va la búsqueda, dónde está MEDIBOT y los datos de `/api/esp32` |
| Teclado | conectado o no, lectura actual, reposo, dispersión y **los rangos calibrados de los 4 botones** |
| Equipo | tiempo encendido, memoria libre, pila del núcleo 0, motivo del último reinicio, modo seguro |
| Configuración | muestreo, brillo de los LED, duración de la medida, puertos de búsqueda, refresco de la API |

Y tres botones: **volver a buscar MEDIBOT**, **calibrar el teclado** (abre el
asistente en la pantalla del equipo) y **reiniciar el ESP32**.

Además de la página hay `http://<ip>/api`, que devuelve lo mismo en JSON por si
quieres leer el triaje desde otro programa.

### Por qué el servidor vive en el núcleo 1

El núcleo 0 alterna entre leer el sensor y hablar por la red: **mientras mide un
pulso no atiende a nada más**, así que un servidor ahí se quedaría colgado medio
minuto en cada chequeo. El núcleo 1 (el del interfaz) está siempre libre, así
que la página responde incluso a mitad de una medida. Hay un caso del banco de
pruebas (`web`) que comprueba exactamente eso.

## Librerías necesarias

| Librería | Para qué |
|---|---|
| `U8g2` (olikraus) | pantalla ST7920 128x64 por SPI hardware |
| `SparkFun MAX3010x Pulse and Proximity Sensor Library` | MAX30102 / MAX30105 |
| `ArduinoJson` (Benoit Blanchon) | leer la API de MEDIBOT |

`Preferences`, `WiFi`, `ESPmDNS` y `HTTPClient` vienen con el core de ESP32.
Vale tanto con el core **2.x** como con el **3.x**: alguna API cambió de nombre
entre ellos (por ejemplo `MDNS.IP()` pasó a ser `MDNS.address()`) y el sketch
elige la que toca con `#if ESP_ARDUINO_VERSION_MAJOR`.

> Si el IDE avisa de **«Multiple libraries were found for WiFi.h»**, comprueba
> que usa la del core ESP32 (`packages/esp32/hardware/esp32/.../libraries/WiFi`).
> Las carpetas `WiFi` y `WiFiNINA` que puedas tener en `Documentos/Arduino/
> libraries` son de otras placas: no hacen falta aquí y conviene borrarlas para
> que no se cuelen.

## La red: se conecta y busca MEDIBOT solo

Nada más encender se conecta a la WiFi y localiza la Raspberry. Los datos de
su API se ven en **Menú → `MEDIBOT (red)`**:

```
MEDIBOT
192.168.1.77:5000
Sistema: ON   Caras: 3
FPS: 28 / 27   Rojos: 2
Grabando: no   WiFi -57 dBm
```

Con `ARRIBA`/`ABAJO` pasas a la segunda página (a qué red está conectado, qué
IP le ha tocado, posición de la cara y cuándo llegó el último dato).

**La red que busca** está en el bloque `1.6` del sketch:

```cpp
#define WIFI_SSID         "MEDIBOT"
#define WIFI_PASS         "MEDIBOTCDB"
```

**Cómo encuentra la Raspberry** (sin tocar nada en ella):

1. **mDNS** — servicio `_medibot._tcp` y, si no, `raspberrypi.local`.
2. **Barrido** de la subred, 4 IPs por vuelta, con barra de progreso.

En los dos casos **comprueba la identidad** antes de dar una IP por buena:
pide `/api/esp32` (puerto 5000) o `HEAD /` (5001) y mira las cabeceras
`X-Medibot-Build` / `X-Pillbox-Build`, que `Vision_MEDIBOT.py` y
`Pastillero.py` ya firman en todas sus respuestas. Sin eso acabarías leyendo
el router.

Para que el mDNS sea instantáneo, opcionalmente en la Pi:

```xml
<!-- /etc/avahi/services/medibot.service  →  sudo systemctl restart avahi-daemon -->
<service-group>
  <name replace-wildcards="yes">MEDIBOT en %h</name>
  <service><type>_medibot._tcp</type><port>5000</port></service>
</service-group>
```

**No se rinde nunca:** si no hay WiFi o MEDIBOT está apagado, lo dice y
reintenta cada 20 s, así que encender el router o la Raspberry después basta.
Si la API deja de responder cinco veces seguidas da por hecho que ha cambiado
de IP y la vuelve a buscar. `OK` en esa pantalla fuerza un reintento inmediato.

**La red se para mientras mides.** Las tareas de WiFi tienen prioridad alta en
el núcleo 0, que es donde se capturan las muestras del sensor: durante el
auto-chequeo la búsqueda se detiene y continúa al terminar.

El ESP32 sólo va en **2,4 GHz** y tiene que estar en la **misma subred** que la
Pi. Si el router tiene aislamiento de clientes, no funciona nada de esto.

El MAX30100 **no** funciona con la librería MAX3010x (es otro chip, PART ID
`0x11`); el firmware lo detecta y lo avisa por Serial y en la pantalla de
arranque.

## 1. El teclado: 4 botones que se calibran solos

El equipo se maneja con **cuatro botones**: `ARRIBA` y `ABAJO` mueven, `OK`
entra y `ATRAS` sale.

El módulo ADKeyboard trae cinco, pero el quinto (el de 3,70 V) **no se puede
usar con un ESP32**, y no es cuestión de software: su ADC satura hacia 3,15 V,
así que ese botón y el reposo (que es VCC) leen los dos 4095 y son
indistinguibles. Antes se dejaba declarado y el asistente lo descartaba, lo que
sólo servía para hacerte perder 12 s en cada calibración y para que el resumen
dijera «4 de 5» como si algo hubiera fallado. Ahora no existe: el asistente
pide cuatro y termina con **`4 de 4 botones OK`**.

### Aliméntalo a 3V3, no a 5 V

En reposo la salida del módulo es VCC, así que **a 5 V le estás metiendo 5 V a
GPIO34**, fuera de especificación del ESP32. A 3V3 la escalera es ratiométrica
y los cuatro botones quedan en `0,00 / 0,46 / 0,99 / 1,65 V`, con el reposo en
3,3 V: perfectamente distinguibles.

Funciona de las dos formas —el asistente mide lo que haya y no hay que tocar el
código—, pero a 3V3 no maltratas el pin. Si cambias la alimentación, repite la
calibración.

### Se calibran solos

**No hay que adivinar ningún umbral ni copiar números al código.** El asistente
mide tus botones reales, calcula los rangos y los guarda en la memoria del
ESP32 (sobreviven al apagado y a recompilar).

Se abre de cuatro formas, y siempre hay una disponible:

1. **Automáticamente** en el primer arranque tras grabar (no hay nada guardado).
2. **Manteniendo cualquier botón mientras enciendes.** Esta es la vía de escape:
   funciona aunque la calibración guardada haya quedado mal y no puedas navegar.
3. Menú → **Calibrar teclado**.
4. Enviando `c` por el Monitor Serie a 115200.

El proceso: **suelta todos los botones** (el asistente espera a que lo hagas y
mide el reposo 1,5 s después) → pulsa y mantén cada uno de los cuatro cuando te
lo pida. Si un botón no responde, a los 12 s lo omite y sigue. En pantalla siempre se
ve la lectura en vivo (`ADC / mV / reposo`), así que si algo va mal se ve al
instante.

Al terminar **guarda y lo relee para confirmarlo**: verás `Guardado en memoria`
o, si la NVS no admite la escritura, `NO se pudo guardar` y el aviso de que se
repetirá al encender. Si ningún botón sirve, restaura la tabla de fábrica en
vez de dejarte sin teclado.

> **Por qué espera a que sueltes.** Al asistente se entra manteniendo un botón
> al encender, o pulsando OK en el menú: al empezar siempre hay una tecla
> pulsada. Si midiera el reposo en ese momento tomaría el nivel del *botón*
> como reposo, y después ninguna pulsación parecería distinta de él: acabaría
> guardando una tabla en la que el reposo real cuenta como tecla pulsada, o sea
> un teclado inservible.

### Cuántas veces se mide cada botón

Cada botón se mide **1600 veces**, no una:

```
64 lecturas  ×  25 conversiones del ADC cada una  =  1600 por botón
```

Cada lectura es ya la mediana de 25 conversiones seguidas, y de las 64 lecturas
se sacan dos cosas:

- la **mediana** → el centro del botón, inmune a un pico suelto del ADC;
- la **dispersión** (percentil 10 a 90) → cuánto baila ese botón en concreto,
  que es lo que dice cuánto margen necesita.

Se tarda unos **0,7 s aguantando cada tecla**; la pantalla lo indica con
`SIGUE PULSANDO` y una barra `midiendo 40/64`. **Si sueltas a mitad se descarta
lo acumulado** y vuelve a empezar: mezclar la tensión del botón con la del
reposo dejaría el centro entre los dos.

Antes se guardaba **una sola lectura**, la que hubiera en el instante exacto en
que la pulsación se daba por estable. Medido en el banco de pruebas con ±55 mV
de ruido (lo que da un cable dupont largo):

| | Antes (1 lectura) | Ahora (1600) |
|---|---|---|
| ARRIBA (real 2500 mV) | 2492 | **2502** |
| OK (real 1500 mV) | 1491 | **1500** |
| ATRÁS (real 700 mV) | 699 | **702** |
| Reposo (real 3200 mV) | 3191 | **3200** |

Y con la tabla vieja los botones **fallaban** al navegar con ese mismo ruido;
con la nueva funcionan los cuatro.

### Cómo de anchos salen los rangos

A cada botón se le da **todo el sitio que haya** hasta su vecino más cercano
(otro botón o el propio reposo), menos una franja de guarda de 20 mV. Cuanto
más ancho el rango, más tolera que la tensión se mueva con la temperatura, la
alimentación o un cable largo.

| Ajuste | Valor | Qué hace |
|---|---|---|
| `WIZ_MARGEN_MAX` | 400 mV | tope del semiancho (antes eran 250) |
| `WIZ_SEPARACION` | 20 mV | franja de guarda entre dos rangos |
| `WIZ_MARGEN_MIN` | 120 mV | por debajo se avisa de que va justo |
| `WIZ_MARGEN_ABS_MIN` | 30 mV | por debajo el botón se descarta |
| `WIZ_DISP_FACTOR` | 3 | el rango debería cubrir 3× lo que baila |

Con el teclado a 3V3 salen así:

```
ABAJO   -314..336 mV   (centro 11,   +-325)
ATRAS    377..1027 mV  (centro 702,  +-325)
OK      1121..1879 mV  (centro 1500, +-379)
ARRIBA  2173..2831 mV  (centro 2502, +-329)
```

Al terminar, el asistente enseña el **margen mínimo** de los cuatro: es el que
decide si alguna pulsación se puede escapar. Si algún botón queda más estrecho
de lo que pide su propia dispersión, lo dice (`OK va justo (85 mV)`) en vez de
callarse.

### Por qué antes no funcionaban

Con umbrales fijos basta con que el **reposo** de tu módulo no esté donde el
código supone para que todo deje de responder: si el reposo cae dentro del
rango de un botón, el firmware cree que está pulsado permanentemente y no
genera ni un evento. Y si alimentas el módulo a 3V3 con la tabla de 5 V, las
teclas salen cambiadas (el botón de ARRIBA se lee como OK, etc.). Ahora el
reposo se mide al arrancar y se declara zona prohibida (`KEY_IDLE_GUARD_MV`),
y los rangos salen de una medida real, no de una suposición.

### Otros parámetros del teclado

| Constante | Qué hace | Cuándo tocarla |
|---|---|---|
| `KEY_SAMPLES` | muestras por lectura (mediana) | subir si hay mucho ruido |
| `KEY_EMA_ALPHA` | filtro exponencial (1.0 = sin filtro) | bajar si sigue temblando |
| `KEY_DEBOUNCE_MS` / `KEY_RELEASE_MS` | antirrebote | subir si se cuelan dobles pulsaciones |
| `KEY_HYSTERESIS_MV` | ensancha el rango del botón ya pulsado | subir si una pulsación larga "se corta" |
| `KEY_IDLE_GUARD_MV` | franja prohibida alrededor del reposo | subir si el reposo es ruidoso |
| `KEY_REPEAT_*` | autorepetición en ARRIBA/ABAJO | gusto personal |

## 2. Referencia y resolución del ADC

| Constante | Valor por defecto | Nota |
|---|---|---|
| `ADC_BITS` | 12 | ESP32 admite 9..12 |
| `ADC_ATTENUATION` | `ADC_11db` | ~0..3.1 V. `ADC_6db` ≈ 0..2.2 V, `ADC_2_5db` ≈ 0..1.5 V, `ADC_0db` ≈ 0..1.1 V |
| `USE_ESP_ADC_CAL` | 1 | usa `analogReadMilliVolts()`, que aplica la calibración de fábrica del eFuse: es lo más exacto y hace innecesario tocar `ADC_FULLSCALE_MV` |
| `ADC_FULLSCALE_MV` | 3300 | sólo se usa con `USE_ESP_ADC_CAL 0` (o al portar a otra placa) |

El teclado trabaja siempre con la tensión **medida en el pin**, así que si hay
un divisor resistivo a la entrada no hay nada que configurar: el asistente mide
los botones tal y como llegan al ESP32.

Para otra placa (AVR de 5 V, RP2040, STM32): `USE_ESP_ADC_CAL 0`,
`ADC_BITS 10` y `ADC_FULLSCALE_MV 5000` (AVR), y repetir la calibración.

## 3. Parámetros del sensor MAX

| Constante | Por defecto | Cuándo tocarla |
|---|---|---|
| `MAX_LED_BRIGHTNESS` | `0x3F` | subir si el IR queda por debajo de 60000 con el dedo puesto; bajar si satura |
| `MAX_SAMPLE_RATE` / `MAX_SAMPLE_AVERAGE` | 400 / 4 | dan **100 Hz efectivos**. Si se cambian, hay que mantener `PPG_EFFECTIVE_SPS / SPO2_DECIMATION = 25 Hz`, que es la `FS` que asume `spo2_algorithm.h` |
| `MAX_PULSE_WIDTH` | 411 µs | 411 da la mejor relación señal/ruido |
| `MAX_ADC_RANGE` | 4096 | subir si la señal satura |
| `FINGER_IR_ON` / `FINGER_IR_OFF` | 60000 / 40000 | umbrales de dedo con histéresis. Ajustar mirando el IR real de tu módulo |
| `MIN_PERFUSION_INDEX` | 0.15 % | umbral de calidad de señal (AC/DC). Subir para ser más exigente |
| `PPG_TARGET_READINGS` | 8 | cada lectura válida es 1 s ⇒ ~8–12 s de medida |
| `SPO2_OFFSET` | 0 | **en el código original había un `-3` fijo**. Es una corrección arbitraria: sólo debe usarse si se ha comparado contra un pulsioxímetro certificado |
| `HR_MIN_BPM` / `HR_MAX_BPM` | 40 / 180 | rango de pulso aceptado |

### 3.2 Arranque y vigilancia del sensor

| Constante | Por defecto | Qué hace |
|---|---|---|
| `MAX_I2C_HZ_FAST` / `MAX_I2C_HZ_SAFE` | 400k / 100k | Se intenta a 400 kHz y, si no contesta, a 100 kHz. A 100 Hz de muestreo hacen falta 600 bytes/s, así que 100 kHz sobra: si tienes cables largos, puedes poner las dos a 100000 |
| `MAX_INIT_RETRIES` | 5 | Intentos de detección por velocidad |
| `SENSOR_RETRY_MS` | 3000 | Cada cuánto se reintenta si no hay sensor |
| `PPG_STALL_MS` | 2500 | Sin muestras durante este tiempo → reiniciar el sensor |
| `MAX_LEDS_ALWAYS_ON` | 1 | LED encendidos desde el arranque. Con 0 se apagan fuera de la medida (ahorra corriente) y el encendido se verifica leyendo el registro |

### 3.1 El pulso NO se mide con `checkForBeat()`

`heartRate.h` (de la misma librería SparkFun) trae `checkForBeat()`, que es lo
que usaban las versiones anteriores y casi todos los ejemplos. **No se usa
aquí, y es a propósito:**

```cpp
int16_t averageDCEstimator(int32_t *p, uint16_t x);   // <-- uint16_t
int16_t lowPassFIRFilter(int16_t din);                // <-- int16_t
```

La muestra IR se trunca a 16 bits. Con el dedo puesto, el MAX30102 entrega
entre 60.000 y 250.000 cuentas: muy por encima de 65.535. La línea de base da
la vuelta y el detector dispara latidos falsos; además cuenta la **onda
dicrota** (el rebote que sigue a cada sístole, un 30–50 % del pico) como si
fuera otro latido. Midiendo una PPG sintética de 72 BPM, ese camino devolvía
**150 BPM** — el síntoma clásico de "el pulso sale casi al doble".

El detector propio (bloque `4.2b` del sketch) trabaja en coma flotante sobre la
muestra completa: línea de base exponencial, señal AC invertida y filtrada,
**umbral adaptativo** sobre la envolvente, histéresis, periodo refractario y
**mediana** de los últimos intervalos.

| Constante | Por defecto | Cuándo tocarla |
|---|---|---|
| `BEAT_TH_HIGH` | 0.55 | fracción del pico reciente para aceptar un latido. Bajar si **pierde** latidos; subir si cuenta de más (onda dicrota muy marcada) |
| `BEAT_TH_LOW` | 0.25 | por debajo de esto se rearma el disparo |
| `BEAT_LP_ALPHA` | 0.25 | filtro paso bajo (~4 Hz). Bajar si hay mucho ruido |
| `BEAT_DC_ALPHA` | 0.01 | seguimiento de la línea de base (~1 s) |
| `BEAT_MIN_AMPLITUDE` | 25 cuentas | por debajo se considera ruido y no dispara |
| `BEAT_RING` | 8 | intervalos que entran en la mediana |

## 4. Pantalla

`LCD_BUS_CLOCK` = 600 kHz. El original usaba 100 kHz **y además llamaba a
`setBusClock()` después de `begin()`**, donde ya no surte efecto. A 100 kHz un
frame completo del ST7920 tarda ~80 ms (≈12 fps como mucho). El ST7920 admite
hasta ~1 MHz; si la pantalla se ve con basura, bajar a 400 kHz.

## Aviso

Este dispositivo es orientativo y de uso educativo. No es un producto sanitario
y no sustituye a un pulsioxímetro certificado ni a una valoración médica.
