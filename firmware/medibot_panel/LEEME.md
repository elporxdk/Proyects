# MEDIBOT PANEL v7.0 — guía rápida

Panel ESP32 con pantalla ST7920 128x64: logo giratorio, auto-chequeo con
MAX30102, enlace WiFi con MEDIBOT (QR + datos en vivo) e historial guardado.

## Antes de compilar

**Librerías** (Gestor de librerías del IDE):

| Librería | Autor |
|---|---|
| U8g2 | olikraus |
| SparkFun MAX3010x Pulse and Proximity Sensor Library | SparkFun |
| QRCode | Richard Moore (ricmoo) |
| ArduinoJson | Benoit Blanchon |

`Preferences`, `WiFi`, `ESPmDNS` y `HTTPClient` vienen con el core de ESP32.
Ya **no** hace falta Adafruit_MLX90614.

**Lo único obligatorio de editar** — bloque `1.2` del sketch:

```cpp
#define WIFI_SSID   "TU_RED_WIFI"
#define WIFI_PASS   "TU_CONTRASENA"
```

Si el binario no cabe: *Herramientas → Partition Scheme → Huge APP (3MB No OTA)*.

## Los botones: se calibran solos

**No hay que adivinar ningún umbral.** El asistente mide tus botones reales,
calcula los rangos y los guarda en la memoria del ESP32 (sobreviven al
apagado y a recompilar).

Se abre de tres formas, y siempre hay una disponible:

1. **Automáticamente** en el primer arranque tras grabar (no hay calibración guardada).
2. **Manteniendo cualquier botón mientras enciendes.** Esta es la vía de escape:
   funciona aunque la calibración guardada haya quedado mal y no puedas navegar.
3. Menú → **Calibrar teclado**, o enviando `c` por el Monitor Serie a 115200.

El proceso: no toques nada 1,5 s (mide el reposo) → pulsa y mantén cada botón
cuando te lo pida. Si un botón no se puede usar, a los 12 s lo omite y sigue.
En pantalla siempre se ve la lectura en vivo (`ADC / mV / reposo`), así que si
algo va mal se ve al instante.

Al terminar guarda y muestra cuántos botones quedaron activos. Si ninguno
sirve, restaura la tabla de fábrica en vez de dejarte sin teclado.

### Por qué antes no funcionaban

Con umbrales fijos, basta con que el **reposo** de tu módulo no esté donde el
código supone para que todo deje de responder: si el reposo cae dentro del
rango de un botón, el firmware cree que está pulsado permanentemente y no
genera ni un evento. Ahora el reposo se mide al arrancar y se declara zona
prohibida (`KEY_IDLE_GUARD_MV`).

## Aviso de hardware: el botón de 3,7 V

Alimentado a **5 V**, el botón de 3,70 V y el reposo (5 V) leen los dos 4095
en el ESP32 (el ADC satura hacia 3,15 V) y **son indistinguibles**; además
metes sobretensión en GPIO34. El asistente lo detecta y lo deja
`DESACTIVADO` en vez de provocar pulsaciones erráticas.

Para recuperar ese quinto botón, alimenta el módulo con **3V3**: la escalera
es ratiométrica y todas las tensiones se multiplican por 0,66
(`0,00 / 0,46 / 0,99 / 1,65 / 2,44 V`). Luego repite la calibración.

Ninguna función imprescindible depende de ese botón: es sólo un atajo al menú.

## Código QR

Lleva `http://<ip>:<puerto>` de MEDIBOT (24–27 caracteres), que entra en un QR
de versión 2 a 2 px por módulo: **58×58 px de los 64 de alto**, con los otros
70 px para el texto y la IP escrita, por si el escaneo falla.

**Si el móvil no te lo lee**, cambia una línea:

```cpp
#define QR_INVERTIDO 0    // 1 en paneles AZULES con pixeles blancos
```

En un panel negativo (azul, píxeles blancos) el QR sale invertido y muchos
lectores lo rechazan. Es la causa nº 1 de que un QR en LCD no escanee. La nº 2
son los reflejos: sube el contraste con el potenciómetro del módulo.

Una URL de túnel Cloudflare (~46 caracteres) **no cabe**: necesitaría versión 3,
que ocupa 66 px y se sale de la pantalla. Para acceso remoto haría falta un
dominio corto fijo (≤32 caracteres).

## Búsqueda de MEDIBOT

Sin tocar nada en la Raspberry. Orden de intentos:

1. **mDNS** — `_medibot._tcp`, y si no, `raspberrypi.local`.
2. **Barrido** de la subred, 4 IPs por vuelta, con barra de progreso.

En ambos casos **verifica identidad** antes de dar por buena una IP: pide
`/api/esp32` (puerto 5000) o `HEAD /` (5001) y comprueba las cabeceras
`X-Medibot-Build` / `X-Pillbox-Build`, que `Vision_MEDIBOT.py` y
`Pastillero.py` ya emiten en todas sus respuestas. Sin eso acabarías generando
un QR que apunta al router.

Si el JSON falla 5 veces seguidas (MEDIBOT cambió de IP), vuelve a buscar solo.

Para que mDNS sea instantáneo, opcionalmente en la Pi:

```xml
<!-- /etc/avahi/services/medibot.service  →  sudo systemctl restart avahi-daemon -->
<?xml version="1.0" standalone='no'?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name replace-wildcards="yes">MEDIBOT en %h</name>
  <service><type>_medibot._tcp</type><port>5000</port></service>
</service-group>
```

## Qué calibrar del MAX30102

| Constante | Cuándo tocarla |
|---|---|
| `MAX_LED_BRIGHTNESS` | Si el IR no llega a 60000 con el dedo puesto |
| `FINGER_IR_ON` / `FINGER_IR_OFF` | Umbrales de dedo con histéresis, según tu módulo |
| `MIN_PERFUSION` | Exigencia de calidad de señal (AC/DC) |
| `PPG_TARGET` | Lecturas válidas; cada una es 1 s → 8 ≈ 11 s de medida |
| `SPO2_OFFSET` | **0 por defecto.** Sólo si has comparado contra un pulsioxímetro certificado |
| `BEAT_TH_HIGH` | Detector de latidos: bajar si **pierde** latidos, subir si cuenta de más |

Si cambias `MAX_SAMPLE_RATE` o `MAX_SAMPLE_AVERAGE`, mantén
`PPG_SPS / SPO2_DECIM = 25 Hz`, que es la `FS` que asume `spo2_algorithm.h`.

### El pulso NO se mide con `checkForBeat()`

`heartRate.h` (de la misma librería SparkFun) trae `checkForBeat()`, que es lo
que usan casi todos los ejemplos. **Aquí no se usa, y es a propósito:** esa
función pasa la muestra por `averageDCEstimator(int32_t*, uint16_t)` y
`lowPassFIRFilter(int16_t)`, o sea que la **trunca a 16 bits**. Con el dedo
puesto el MAX30102 entrega entre 60.000 y 250.000 cuentas, muy por encima de
65.535: la línea de base da la vuelta y salen latidos falsos. Además cuenta la
onda dicrota (el rebote que sigue a cada sístole) como un latido más. Midiendo
una PPG sintética de 72 BPM devolvía **150 BPM**.

El detector propio (bloque `7.1` del sketch) filtra en coma flotante sobre la
muestra completa, usa un umbral **adaptativo** sobre la envolvente de la señal
con histéresis y periodo refractario, y da el pulso como **mediana** de los
últimos intervalos.

## Red y ADC

- **GPIO34 es ADC1**: sigue leyendo con el WiFi encendido. Si mueves el teclado
  a un pin de ADC2 (0, 2, 4, 12–15, 25–27) dejará de funcionar en cuanto se
  llame a `WiFi.begin()`.
- El ESP32 sólo va en **2,4 GHz** y tiene que estar en la **misma subred** que la
  Pi. Si el router tiene aislamiento de clientes, no funciona nada de esto.
- La búsqueda de red **se detiene durante la medición**: las tareas de WiFi
  tienen prioridad alta en el núcleo 0, que es donde se capturan las muestras.

## Aviso

Uso educativo. No es un producto sanitario y no sustituye a un pulsioxímetro
certificado ni a una valoración médica.
