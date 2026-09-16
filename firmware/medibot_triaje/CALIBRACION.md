# MEDIBOT v6.0 — Guía de calibración

Todo lo ajustable está en el bloque **`1. CONFIGURACION`** del sketch
`medibot_triaje.ino`. Este documento explica qué medir y dónde ponerlo.

## Librerías necesarias

| Librería | Para qué |
|---|---|
| `U8g2` (olikraus) | pantalla ST7920 128x64 por SPI hardware |
| `SparkFun MAX3010x Pulse and Proximity Sensor Library` | MAX30102 / MAX30105 |
| `Adafruit MLX90614` | termómetro IR sin contacto |

El MAX30100 **no** funciona con la librería MAX3010x (es otro chip, PART ID
`0x11`); el firmware lo detecta y lo avisa por Serial y en la pantalla de
arranque.

## 1. Los botones: se calibran solos

**No hay que adivinar ningún umbral ni copiar números al código.** El asistente
mide tus botones reales, calcula los rangos y los guarda en la memoria del
ESP32 (sobreviven al apagado y a recompilar).

Se abre de cuatro formas, y siempre hay una disponible:

1. **Automáticamente** en el primer arranque tras grabar (no hay nada guardado).
2. **Manteniendo cualquier botón mientras enciendes.** Esta es la vía de escape:
   funciona aunque la calibración guardada haya quedado mal y no puedas navegar.
3. Menú → **Calibrar teclado**.
4. Enviando `c` por el Monitor Serie a 115200.

El proceso: no toques nada 1,5 s (mide el reposo) → pulsa y mantén cada botón
cuando te lo pida. Si un botón no se puede usar, a los 12 s lo omite y sigue.
En pantalla siempre se ve la lectura en vivo (`ADC / mV / reposo`), así que si
algo va mal se ve al instante.

Al terminar guarda y muestra cuántos botones quedaron activos. Si ninguno
sirve, restaura la tabla de fábrica en vez de dejarte sin teclado.

### Por qué antes no funcionaban

Con umbrales fijos basta con que el **reposo** de tu módulo no esté donde el
código supone para que todo deje de responder: si el reposo cae dentro del
rango de un botón, el firmware cree que está pulsado permanentemente y no
genera ni un evento. Y si alimentas el módulo a 3V3 con la tabla de 5 V, las
teclas salen cambiadas (el botón de ARRIBA se lee como OK, etc.). Ahora el
reposo se mide al arrancar y se declara zona prohibida (`KEY_IDLE_GUARD_MV`),
y los rangos salen de una medida real, no de una suposición.

### Aviso de hardware: el botón de 3,7 V

Alimentado a **5 V**, el botón de 3,70 V y el reposo (5 V) leen los dos 4095
en el ESP32 (el ADC satura hacia 3,15 V) y **son indistinguibles**; además
metes sobretensión en GPIO34. El asistente lo detecta y lo deja
`DESACTIVADO` en vez de provocar pulsaciones erráticas.

Para recuperar ese quinto botón, alimenta el módulo con **3V3**: la escalera
es ratiométrica y todas las tensiones se multiplican por 0,66
(`0,00 / 0,46 / 0,99 / 1,65 / 2,44 V`). Luego repite la calibración. No hace
falta tocar el código: el asistente mide lo que haya.

Ninguna función imprescindible depende de ese botón: es sólo un atajo al menú.

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

## 3. Corrección de temperatura

**Limitación real:** ni la señal PPG (rojo/IR), ni el pulso, ni la SpO₂
contienen información de temperatura corporal. Cualquier “temperatura”
derivada de ellas sería inventada. Hace falta un sensor térmico.

El MAX3010x **sí** tiene termómetro interno, pero mide la temperatura del
**silicio del chip** (sirve para compensar la deriva de los LED). Aquí se lee
sólo como diagnóstico (`chipTempC`, corrección `MAX_CHIP_TEMP_OFFSET_C`) y
nunca se presenta como temperatura del paciente.

| Constante | Qué es |
|---|---|
| `TEMP_SOURCE` | `TEMP_SOURCE_MLX90614` (actual), `TEMP_SOURCE_MAX30205`, `TEMP_SOURCE_DS18B20`, `TEMP_SOURCE_NONE` |
| `TEMP_SKIN_OFFSET_C` | corrección del sensor de piel; se suma a la lectura |
| `TEMP_SKIN_TO_CORE_C` | offset piel → núcleo. **Se deja en 0.0 a propósito**: un offset fijo no es clínicamente válido. Si se activa, la pantalla marca el valor con `~` (estimado) |
| `TEMP_SKIN_MIN_C` / `TEMP_SKIN_MAX_C` | 28..43 °C, rango físicamente posible; fuera de él la lectura se descarta |
| `TEMP_TARGET_READINGS` | muestras promediadas (media recortada) |
| `TEMP_FEVER_C` / `TEMP_LOW_C` | umbrales del texto de resultado |

Procedimiento para `TEMP_SKIN_OFFSET_C`: medir la muñeca 5 veces con el
MEDIBOT y 5 veces con un termómetro clínico de referencia en el mismo punto y
a la misma distancia; `offset = media_referencia − media_medibot`. Repetirlo a
la distancia real de uso: el MLX90614 tiene un campo de visión de ~90°, así que
si está lejos promedia piel + ropa + fondo y lee bajo.

**Añadir otro sensor**: sólo hay que implementar dos funciones,
`tempSensorBegin()` y `tempSensorRead(float &skinC, float &ambientC)`
(sección 4.2). El driver del MAX30205 ya está escrito (I2C directo) y el del
DS18B20 está dejado como plantilla comentada con las líneas exactas.

## 4. Parámetros del sensor MAX

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

### 4.1 El pulso NO se mide con `checkForBeat()`

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

## 5. Pantalla

`LCD_BUS_CLOCK` = 600 kHz. El original usaba 100 kHz **y además llamaba a
`setBusClock()` después de `begin()`**, donde ya no surte efecto. A 100 kHz un
frame completo del ST7920 tarda ~80 ms (≈12 fps como mucho). El ST7920 admite
hasta ~1 MHz; si la pantalla se ve con basura, bajar a 400 kHz.

## Aviso

Este dispositivo es orientativo y de uso educativo. No es un producto sanitario
y no sustituye a un pulsioxímetro ni a un termómetro clínico certificados, ni a
una valoración médica.
