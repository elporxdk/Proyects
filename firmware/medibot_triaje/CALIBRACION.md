# MEDIBOT v6.0 — Guía de calibración

Triaje con **un solo sensor**: el MAX30102 (pulso y SpO₂). Todo lo ajustable
está en el bloque **`1. CONFIGURACION`** del sketch `medibot_triaje.ino`. Este
documento explica qué medir y dónde ponerlo.

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
ha podido releer.

### Los fallos típicos y qué significan

| Lo que ves | Qué pasa |
|---|---|
| `I2C: nadie responde` / `[I2C] NADIE contesta` | El módulo no está alimentado o SDA/SCL no llegan. Revisa VIN, GND, SDA→21, SCL→22 |
| `Sensor: MAX30100 no vale` | Es el chip antiguo (ID `0x11`). La librería MAX3010x no lo soporta: hace falta un MAX30102 o MAX30105 |
| `Sensor: NO DETECTADO` con `I2C: 1 disp.` | Hay algo en el bus pero no contesta como MAX30102: mal contacto o módulo defectuoso |
| `[MAX] Sin respuesta a 400 kHz` | Cables largos o sin pull-ups. El firmware baja solo a 100 kHz y sigue |
| `[MAX] La configuracion NO se aplico` | La escritura se perdió; el firmware lo reintenta solo |
| `Reinicios: N` con N creciendo | El sensor se cuelga: cable flojo o alimentación justa |
| `Memoria: FALLO` | La NVS no admite escrituras: la calibración del teclado no se guardará |

El equipo **no se queda colgado** en ninguno de esos casos: si el sensor no
aparece al arrancar se sigue buscando cada 3 s (conectarlo con el equipo
encendido basta, no hace falta reiniciar), y si deja de dar muestras a mitad de
una medida se reinicia solo y continúa.

## Librerías necesarias

| Librería | Para qué |
|---|---|
| `U8g2` (olikraus) | pantalla ST7920 128x64 por SPI hardware |
| `SparkFun MAX3010x Pulse and Proximity Sensor Library` | MAX30102 / MAX30105 |

`Preferences` viene con el core de ESP32 (guarda la calibración del teclado).

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

El proceso: **suelta todos los botones** (el asistente espera a que lo hagas y
mide el reposo 1,5 s después) → pulsa y mantén cada botón cuando te lo pida. Si
un botón no se puede usar, a los 12 s lo omite y sigue. En pantalla siempre se
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
