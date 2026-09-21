/* =====================================================================
 *  MEDIBOT v6.1  |  ESP32 + ST7920 128x64 (U8g2) + MAX30102 + WiFi
 * =====================================================================
 *  Core 0 : sensor PPG y red (nunca a la vez), siempre sin bloquear.
 *  Core 1 : teclado analogico, maquina de estados, animaciones y UI.
 *
 *  Se conecta a la WiFi de MEDIBOT, localiza la Raspberry sola (mDNS y, si
 *  no, barriendo la subred) y muestra en vivo los datos de su API.
 *
 *  TODO LO QUE HAY QUE CALIBRAR ESTA EN EL BLOQUE "1. CONFIGURACION".
 *  Ver firmware/medibot_triaje/CALIBRACION.md para el procedimiento.
 * ===================================================================== */

#include <Arduino.h>
#include <U8g2lib.h>
#include <SPI.h>
#include <Wire.h>
#include <math.h>
#include <stdlib.h>
#include "MAX30105.h"          // Libreria SparkFun MAX3010x (MAX30102 / MAX30105)
#include "spo2_algorithm.h"
//  NO se usa "heartRate.h" (checkForBeat): ver el bloque 4.3. Esa funcion
//  trunca la muestra IR a 16 bits y con el dedo puesto el sensor entrega
//  muchas mas cuentas, por lo que devuelve un pulso erroneo.
#include <Preferences.h>       // memoria no volatil: calibracion del teclado
#include <nvs_flash.h>         // para recuperar la NVS si viene corrupta
#include <esp_system.h>      // esp_reset_reason(): por que se reinicio
#include <WiFi.h>
#include <ESPmDNS.h>
#include <HTTPClient.h>
#include <WebServer.h>         // la configuracion del equipo en el navegador
#include <ArduinoJson.h>

// =====================================================================
// 1. CONFIGURACION
// =====================================================================

// ---------------------------------------------------------------------
// 1.1 PINES (identicos al diseno original)
// ---------------------------------------------------------------------
#define OLED_CS_PIN        5
#define OLED_RESET_PIN     19
#define KEYPAD_PIN         34      // ADC1_CH6. Solo entrada, sin pull-up interno.
#define I2C_SDA_PIN        21
#define I2C_SCL_PIN        22

// ---------------------------------------------------------------------
// 1.2 ADC  --> AJUSTAR SEGUN PLACA, REFERENCIA Y RESOLUCION
// ---------------------------------------------------------------------
//  * ADC_BITS            : resolucion del ADC (ESP32: 9..12 bits).
//  * ADC_ATTENUATION     : ADC_11db -> rango util ~0..3.1 V (el ESP32 satura
//                          por encima de ~3.15 V aunque el pin aguante 3.3 V).
//                          ADC_6db ~0..2.2 V, ADC_2_5db ~0..1.5 V, ADC_0db ~0..1.1 V.
//  * USE_ESP_ADC_CAL = 1 : usa analogReadMilliVolts(), que aplica la calibracion
//                          de fabrica grabada en el eFuse -> es lo mas exacto y
//                          hace innecesario tocar ADC_FULLSCALE_MV.
//  * USE_ESP_ADC_CAL = 0 : conversion lineal cruda con ADC_FULLSCALE_MV.
//                          Si migras a otra placa (RP2040, AVR 5 V, STM32...),
//                          pon 0 y ajusta ADC_BITS + ADC_FULLSCALE_MV.
//  Todo el teclado trabaja con la tension MEDIDA EN EL PIN, asi que si hay un
//  divisor resistivo a la entrada no hay nada que configurar: el asistente de
//  calibracion (bloque 1.3) mide los botones tal y como llegan al ESP32.
#define ADC_BITS               12
#define ADC_MAX_COUNTS         ((1 << ADC_BITS) - 1)
#define ADC_ATTENUATION        ADC_11db
#define ADC_FULLSCALE_MV       3300.0f
#define USE_ESP_ADC_CAL        1

// ---------------------------------------------------------------------
// 1.3 TECLADO ANALOGICO (ADKeyboard: escalera resistiva en 1 sola entrada)
// ---------------------------------------------------------------------
//  ESTOS VALORES SON SOLO EL PUNTO DE PARTIDA. El asistente de calibracion
//  MIDE los botones reales y guarda los rangos en la memoria del ESP32; lo
//  guardado manda sobre esta tabla y sobrevive al apagado y a recompilar.
//  El asistente se abre de cuatro formas:
//     1. solo, en el primer arranque tras grabar (no hay nada guardado);
//     2. manteniendo cualquier boton mientras se enciende  <-- via de escape
//        si la calibracion guardada quedo mal y no se puede navegar el menu;
//     3. desde el menu -> "Calibrar teclado";
//     4. enviando 'c' por el Monitor Serie a 115200.
//
//  Por que hacia falta: con umbrales fijos basta con que el REPOSO de tu
//  modulo no caiga donde el codigo supone para que no responda ni un boton
//  (si el reposo cae dentro del rango de una tecla, el firmware la cree
//  pulsada para siempre y no genera ni un evento). Ahora el reposo se mide al
//  arrancar y se declara zona prohibida (KEY_IDLE_GUARD_MV).
//
//  *** EL TECLADO SON 4 BOTONES: ARRIBA, ABAJO, OK y ATRAS ***
//  El modulo ADKeyboard trae cinco, pero el quinto (0.01/0.70/1.50/2.50/3.70 V
//  -> el de 3.70 V) NO se puede usar con un ESP32 y no es cuestion de
//  software: su ADC satura hacia 3.15 V, asi que ese boton y el reposo (que
//  es VCC) dan los dos 4095 y son indistinguibles. Antes se dejaba declarado
//  y el asistente lo descartaba, lo que solo servia para hacer perder 12 s en
//  cada calibracion y para que el resumen dijera "4 de 5" como si algo
//  hubiera fallado. Ahora sencillamente no existe: con cuatro botones se
//  navega todo (ARRIBA/ABAJO mueven, OK entra, ATRAS sale).
//
//  ALIMENTACION: mejor 3V3 que 5 V. En reposo la salida del modulo es VCC, o
//  sea que a 5 V se le estan metiendo 5 V a GPIO34, fuera de especificacion.
//  A 3V3 los cuatro botones quedan en 0.00 / 0.46 / 0.99 / 1.65 V y el reposo
//  en 3.3 V: perfectamente distinguibles. Funciona de las dos formas porque
//  el asistente mide lo que haya, pero a 3V3 no se maltrata el pin.
enum Button : uint8_t { BTN_NONE = 0, BTN_OK, BTN_UP, BTN_DOWN, BTN_BACK, BTN_COUNT };

//  Nombre en pantalla y orden en el que el asistente pide cada boton
const char *BTN_NOMBRE[BTN_COUNT] = { "----", "OK", "ARRIBA", "ABAJO", "ATRAS" };
const Button BTN_ORDEN[] = { BTN_UP, BTN_DOWN, BTN_OK, BTN_BACK };
const uint8_t BTN_ORDEN_N = sizeof(BTN_ORDEN) / sizeof(BTN_ORDEN[0]);

//  Tabla de partida en MILIVOLTIOS MEDIDOS EN EL PIN del ESP32, para el
//  modulo a 5 V (0.01 / 0.70 / 1.50 / 2.50 V). Solo se usa hasta la primera
//  calibracion: lo que mida el asistente manda sobre esto.
//  mvMin > mvMax = boton desactivado.
struct KeyDef { Button id; int16_t mvMin; int16_t mvMax; };
KeyDef KEYPAD_MAP[] = {
  { BTN_DOWN,   -50,  300 },
  { BTN_BACK,   450,  950 },
  { BTN_OK,    1250, 1750 },
  { BTN_UP,    2250, 2750 },
};
const uint8_t KEYPAD_MAP_SIZE = sizeof(KEYPAD_MAP) / sizeof(KEYPAD_MAP[0]);
KeyDef KEYPAD_MAP_DEFECTO[KEYPAD_MAP_SIZE];      // copia de fabrica (red de seguridad)

// ---------------------------------------------------------------------
// 1.4 FILTRADO / ANTIRREBOTE / HISTERESIS DEL TECLADO
// ---------------------------------------------------------------------
#define KEY_POLL_MS          10      // periodo de muestreo del teclado
#define KEY_SAMPLES          9       // muestras por lectura (mediana). Impar y >= 3
#define KEY_EMA_ALPHA        0.40f   // filtro exponencial (1.0 = sin filtro)
#define KEY_DEBOUNCE_MS      40      // ms estable para aceptar una pulsacion
#define KEY_RELEASE_MS       40      // ms estable para aceptar la soltada
#define KEY_HYSTERESIS_MV    70      // el boton ya pulsado ensancha su rango
#define KEY_IDLE_GUARD_MV    120     // franja prohibida alrededor del reposo
//  TECLADO DESCONECTADO: el GPIO34 es solo entrada y NO tiene pull-up interno,
//  asi que sin nada enchufado flota y da lecturas que bailan cerca de 0 V. Un
//  boton pulsado de verdad tambien da poca tension, pero QUIETA. La diferencia
//  esta en la dispersion, y por eso se miran las dos cosas a la vez.
#define KEY_AIRE_MV          350     // por debajo de esto puede ser pin al aire
#define KEY_AIRE_DISP_MV     80      // ...y si ademas baila tanto, lo es
//  Y la misma vigilancia mientras el equipo funciona: un pin al aire no se
//  queda quieto NUNCA, asi que la lectura baila sin parar y acaba cayendo por
//  casualidad dentro del rango de algun boton. Sin esto el equipo se mueve
//  solo por los menus, como si hubiera un fantasma pulsando teclas.
//
//  Lo que se mira es la DISPERSION de las KEY_SAMPLES muestras de UNA lectura,
//  que se toman seguidas en menos de un milisegundo: con el teclado conectado
//  salen casi identicas (unas pocas decenas de mV), y con el pin al aire salen
//  desperdigadas. Se pide ademas que se repita en casi todas las lecturas de
//  una ventana, para que el salto de tension al pulsar un boton (que ocurre
//  una vez) no se confunda nunca con un cable suelto.
#define KEY_SPREAD_MV        120     // dispersion dentro de una lectura
#define KEY_SALTOS_VENTANA   32      // lecturas que se recuerdan
#define KEY_SALTOS_MIN       24      // ...de las cuales tantas deben bailar
#define KEY_REPEAT_ENABLED   1       // autorepeticion en UP/DOWN
#define KEY_REPEAT_DELAY_MS  600
#define KEY_REPEAT_RATE_MS   180

// ---------------------------------------------------------------------
// 1.4b ASISTENTE DE CALIBRACION
// ---------------------------------------------------------------------
#define WIZ_REPOSO_MS        1500    // tiempo midiendo el reposo al empezar
#define WIZ_ESTABLE_MS       700     // pulsacion mantenida para darla por buena
#define WIZ_SALTO_MS         12000   // si no se pulsa, se omite ese boton
#define WIZ_UMBRAL_MV        150     // diferencia minima con el reposo
#define WIZ_TOLER_MV         90      // cuanto puede moverse y seguir "estable"
#define WIZ_SOLTAR_MS        12000   // gracia esperando a que se suelte el teclado

//  CUANTAS VECES SE MIDE CADA BOTON
//  Antes el asistente se quedaba con UNA lectura: el valor que hubiera en el
//  instante en que la pulsacion se daba por estable. Un pico del ADC en ese
//  momento desplazaba el centro del boton y el rango salia torcido.
//  Ahora se acumulan WIZ_MUESTRAS lecturas mientras el boton sigue pulsado, y
//  cada una de ellas es ya la mediana de WIZ_SUBMUESTRAS conversiones del ADC:
//      64 x 25 = 1600 conversiones por boton
//  De ese monton se sacan dos cosas: la MEDIANA (el centro, inmune a picos) y
//  la DISPERSION real de ese boton (percentil 10 a 90), que es la que decide
//  cuanto margen necesita. A 10 ms por lectura son ~0,7 s aguantando la tecla.
#define WIZ_SUBMUESTRAS      25      // conversiones del ADC por lectura
#define WIZ_MUESTRAS         64      // lecturas acumuladas por boton
#define WIZ_MIN_MUESTRAS     24      // con menos de estas, el boton no vale

//  ANCHO DE LOS RANGOS  --> "que no se me escape ninguna pulsacion"
//  Cada boton se queda con TODO el sitio que haya hasta su vecino mas cercano
//  (otro boton o el propio reposo), menos una franja de guarda. Cuanto mas
//  ancho, mas tolerante es a que la tension se mueva con la temperatura, la
//  tension de alimentacion o un cable largo.
#define WIZ_MARGEN_MAX       400     // semiancho maximo del rango, en mV
#define WIZ_MARGEN_MIN       120     // por debajo se avisa de que va justo
#define WIZ_MARGEN_ABS_MIN   30      // por debajo el boton se descarta
#define WIZ_SEPARACION       20      // franja de guarda entre dos rangos
#define WIZ_DISP_FACTOR      3       // el rango debe cubrir 3x lo que baila
#define NVS_NS               "medibot"
#define CAL_MAGIC            0x4B34   // cambia al cambiar el formato: una
                                      // calibracion de 5 botones se descarta

// ---------------------------------------------------------------------
// 1.5 SENSOR MAX3010x  --> PARAMETROS AJUSTABLES
// ---------------------------------------------------------------------
//  El codigo original incluia "MAX30105.h" (libreria SparkFun MAX3010x). Esa
//  libreria vale tanto para el MAX30105 como para el MAX30102 (mismo PART ID
//  0x15); lo que cambia es que el MAX30102 NO tiene LED verde. En los modulos
//  chinos "MAX30102" lo habitual es el MAX30102, por eso se usa ledMode = 2
//  (ROJO + IR), que es exactamente lo que necesita el algoritmo de SpO2.
//  El MAX30100 es OTRO chip (PART ID 0x11) y NO funciona con esta libreria:
//  el firmware lo detecta y lo avisa por Serial y en pantalla.
#define MAX_I2C_ADDR         0x57    // el MAX30102 y el MAX30100 comparten direccion
#define MAX_LED_BRIGHTNESS   0x3F    // 0x00..0xFF. Sube si la senal es debil
#define MAX_SAMPLE_AVERAGE   4       // promediado en el propio chip
#define MAX_LED_MODE         2       // 2 = Rojo+IR (SpO2). 3 = +verde (solo MAX30105)
#define MAX_SAMPLE_RATE      400     // Hz nominales -> 400/4 = 100 Hz efectivos
#define MAX_PULSE_WIDTH      411     // us (69/118/215/411)
#define MAX_ADC_RANGE        4096    // 2048/4096/8192/16384

//  ARRANQUE DEL SENSOR (por que hay tanto reintento):
//  con cables dupont largos y los pull-ups de 4k7 que traen los modulos, el
//  bus a 400 kHz falla a ratos; y algunos modulos tardan en arrancar tras dar
//  tension. Por eso se intenta varias veces y, si a 400 kHz no contesta, se
//  baja a 100 kHz: a 100 Hz de muestreo hacen falta 600 bytes/s, asi que
//  100 kHz sobra de largo. Si aun asi no aparece, se sigue reintentando en
//  segundo plano, de modo que reconectar el sensor no exige reiniciar.
#define MAX_I2C_HZ_FAST      400000UL
#define MAX_I2C_HZ_SAFE      100000UL
#define MAX_INIT_RETRIES     5       // intentos de deteccion en el arranque
#define SENSOR_RETRY_MS      3000UL  // primer reintento en segundo plano
//  Si el sensor no esta (cable suelto, modulo sin soldar), reintentar cada 3 s
//  para siempre no arregla nada: machaca el bus, llena el Monitor Serie de
//  mensajes repetidos y roba tiempo al nucleo 0. El reintento se va espaciando
//  hasta medio minuto; en cuanto el sensor aparece, vuelve a 3 s.
#define SENSOR_RETRY_MAX_MS  30000UL // tope del reintento cuando sigue sin haber
#define SENSOR_SCAN_CADA     10      // escaneo completo del bus 1 de cada N intentos
#define PPG_STALL_MS         2500UL  // sin muestras nuevas -> reiniciar el sensor
//  Con los LED apagados en reposo se ahorra corriente, pero si la escritura
//  que vuelve a encenderlos se pierde, el sensor se queda a oscuras y "no
//  funciona". Por defecto se quedan encendidos desde el arranque, que es como
//  funciona el codigo de referencia; con 0 se apagan fuera de la medida y el
//  encendido se verifica leyendo el registro.
#define MAX_LEDS_ALWAYS_ON   1

#define PPG_EFFECTIVE_SPS    (MAX_SAMPLE_RATE / MAX_SAMPLE_AVERAGE)   // 100 Hz
#define SPO2_FS              25                                       // FS de spo2_algorithm.h
#define SPO2_DECIMATION      (PPG_EFFECTIVE_SPS / SPO2_FS)            // 4
#define SPO2_BUFFER_LEN      100     // 100 muestras a 25 Hz = 4 s de ventana
#define SPO2_SHIFT           25      // desplazamiento -> nuevo calculo cada 1 s

//  Umbrales de dedo. No todos los modulos dan lo mismo: con el dedo puesto
//  unos llegan a 150.000 cuentas y otros se quedan en 35.000. Estos valores
//  son prudentes; la pantalla de Diagnostico del menu muestra el IR en vivo
//  para ajustarlos al modulo que tengas (sin dedo suele ser < 10.000).
#define FINGER_IR_ON         30000UL // IR por encima -> hay dedo
#define FINGER_IR_OFF        18000UL // IR por debajo -> no hay dedo (histeresis)
#define FINGER_STABLE_MS     400     // ms de estabilidad antes de dar el dedo por puesto
#define MIN_PERFUSION_INDEX  0.15f   // % (AC/DC). Por debajo la senal no es fiable
#define HR_MIN_BPM           40
#define HR_MAX_BPM           180
#define SPO2_MIN_VALID       70
#define SPO2_MAX_VALID       100
#define SPO2_OFFSET          0       // correccion en puntos de %. 0 = sin trucar
#define PPG_TARGET_READINGS  8       // lecturas validas (1/s) -> ~8-12 s de medida
#define PPG_MAX_READINGS     16
#define PPG_TIMEOUT_MS       45000UL // si no se completa -> error de senal
#define NO_FINGER_TIMEOUT_MS 20000UL // sin dedo tanto tiempo -> se cancela

// ---------------------------------------------------------------------
// 1.5b DETECTOR DE LATIDOS  --> RARA VEZ HAY QUE TOCAR ESTO
// ---------------------------------------------------------------------
//  Umbral ADAPTATIVO: se calcula sobre la envolvente de la propia senal, asi
//  que funciona igual con un dedo frio (poca amplitud) que con uno caliente.
//  BEAT_TH_HIGH es la clave para NO contar la onda dicrota (el "rebote" que
//  toda PPG tiene tras la sistole y que vale un 30-50 % del pico): con 0.55
//  hace falta superar el 55 % del pico reciente para dar un latido por bueno.
#define BEAT_DC_ALPHA        0.01f   // linea de base (constante ~1 s a 100 Hz)
#define BEAT_LP_ALPHA        0.25f   // filtro paso bajo (~4 Hz a 100 Hz)
#define BEAT_ENV_DECAY       0.997f  // caida de la envolvente por muestra
#define BEAT_TH_HIGH         0.55f   // fraccion de la envolvente para disparar
#define BEAT_TH_LOW          0.25f   // fraccion por debajo de la cual se rearma
#define BEAT_MIN_AMPLITUDE   25.0f   // cuentas; por debajo solo hay ruido
#define BEAT_WARMUP_SAMPLES  80      // muestras hasta asentar la linea de base
#define BEAT_RING            8       // intervalos guardados para la mediana
#define BEAT_MIN_INTERVALS   2       // intervalos minimos para dar un BPM
#define BEAT_REFRACTORY_MS   (60000UL / HR_MAX_BPM)   // 333 ms a 180 BPM
#define BEAT_MAX_INTERVAL_MS (60000UL / HR_MIN_BPM)   // 1500 ms a 40 BPM

// ---------------------------------------------------------------------
// 1.6 RED: WIFI Y BUSQUEDA DE MEDIBOT
// ---------------------------------------------------------------------
//  La red la lleva el nucleo 0 y NUNCA a la vez que la medida: las tareas de
//  WiFi tienen prioridad alta en ese nucleo y se comerian las muestras del
//  sensor. Mientras se mide, la red se queda quieta; al terminar, sigue.
//
//  Como encuentra la Raspberry sin tocar nada en ella:
//    1. mDNS: servicio _medibot._tcp y, si no, el nombre raspberrypi.local
//    2. barrido de la subred, 4 IPs por vuelta
//  En los dos casos COMPRUEBA la identidad antes de dar una IP por buena:
//  pide /api/esp32 (puerto 5000) o HEAD / (5001) y mira las cabeceras
//  X-Medibot-Build / X-Pillbox-Build, que Vision_MEDIBOT.py y Pastillero.py
//  ya firman en todas sus respuestas. Sin eso acabarias leyendo el router.
#define WIFI_SSID         "MEDIBOT"
#define WIFI_PASS         "MEDIBOTCDB"
#define WIFI_TIMEOUT_MS   15000UL   // sin enganchar en este tiempo -> Sin WiFi
#define WIFI_RETRY_MS     20000UL   // y se reintenta cada tanto, solo

#define MEDIBOT_PORT_MAIN 5000      // Vision_MEDIBOT.py  -> X-Medibot-Build
#define MEDIBOT_PORT_ALT  5001      // Pastillero.py      -> X-Pillbox-Build
#define MEDIBOT_MDNS_SVC  "medibot" // _medibot._tcp (opcional en la Pi)
#define MEDIBOT_MDNS_HOST "raspberrypi"
#define MEDIBOT_API       "/api/esp32"
//  El propio ESP32 sirve una pagina con toda su configuracion: basta escribir
//  su IP (la que dice el arranque y la pantalla MEDIBOT) en un navegador de la
//  misma red. Tambien vale http://medibot-triaje.local/
#define WEB_PUERTO           80
#define JSON_POLL_MS      1000UL    // refresco de los datos en vivo
#define SWEEP_TIMEOUT_MS  180       // ms de espera por IP en el barrido
#define JSON_FAILS_RESCAN 5         // fallos seguidos -> MEDIBOT cambio de IP

// ---------------------------------------------------------------------
// 1.7 INTERFAZ Y TIEMPOS
// ---------------------------------------------------------------------
#define TAREA_STACK           16384   // pila del nucleo 0 (sensor + red)
#define UI_FRAME_MS           40      // 25 fps
#define LCD_BUS_CLOCK         600000UL// ST7920: 100 kHz daba ~80 ms por frame
#define INACTIVITY_TIMEOUT    30000UL
#define BOOT_SCREEN_MS        2200UL  // pantalla de autodiagnostico al arrancar
#define BOOT_SCREEN_FALLO_MS  6000UL  // ...mas tiempo si hay un fallo que leer
#define REQ_SCREEN_MS         2200UL  // duracion de la pantalla "coloque el dedo"
#define ERROR_SCREEN_MS       6000UL

// =====================================================================
// 2. TIPOS Y ESTADO GLOBAL
// =====================================================================
enum AppState : uint8_t {
  STATE_BOOT,
  STATE_IDLE_FACE,
  STATE_MENU,
  STATE_TRIAGE_FINGER_REQ,
  STATE_TRIAGE_FINGER_READ,
  STATE_TRIAGE_RESULT,
  STATE_SIGNAL_ERROR,
  STATE_HISTORY,
  STATE_DIAG,
  STATE_MEDIBOT,
  STATE_ABOUT,
  STATE_KEYPAD_WIZARD
};

enum Emotion : uint8_t {
  EMOTION_NORMAL, EMOTION_LOOK_DOWN,
  EMOTION_HAPPY,  EMOTION_SAD,    EMOTION_LOADING
};

// Trabajo que el nucleo 1 (UI) pide al nucleo 0. Nunca dos a la vez.
enum SensorMode : uint8_t { SENS_IDLE, SENS_PPG, SENS_NET };

// Etapas de la conexion, en orden
enum NetStage : uint8_t { NET_OFF, NET_WIFI, NET_MDNS, NET_SWEEP, NET_FOUND, NET_FAIL };

// Estado de la red. Va APARTE de Vitals a proposito: Vitals se borra entero
// en cada medida y la conexion no tiene por que perderse por eso.
struct NetInfo {
  uint8_t  etapa;
  uint8_t  progreso;      // 0..100 del barrido
  uint32_t ip;            // 0 = aun no localizado
  uint32_t ipPropia;      // la que le ha dado el router a este ESP32
  uint16_t puerto;
  int8_t   rssi;
  char     msg[24];
  // datos de /api/esp32
  bool     jsonOk;
  uint32_t jsonMs;
  int      sistema, detecciones, rojos, fps1, fps2, caraX, caraY;
  bool     grabando;
};

// Estado del detector de latidos. El algoritmo esta en el bloque 4.3; el tipo
// tiene que declararse AQUI, antes de la primera funcion del fichero, porque
// el IDE de Arduino genera solo los prototipos de todas las funciones y los
// inserta justo ahi: si el tipo llega despues, el prototipo de beatUpdate() lo
// usaria sin conocerlo ("'BeatDetector' was not declared in this scope").
struct BeatDetector {
  float    dc;
  float    lp;
  float    env;
  bool     init;
  bool     armed;
  uint16_t warmup;
  uint32_t lastBeatMs;
  uint32_t intervals[BEAT_RING];
  uint8_t  intervalCount;
  uint8_t  intervalIndex;
};

// Datos que el nucleo 0 publica y el nucleo 1 consume. Se copian SIEMPRE
// dentro de una seccion critica (spinlock) para que la UI nunca lea una
// mezcla de dos actualizaciones distintas.
struct Vitals {
  uint32_t epoch;            // epoca de medida que ha adoptado el nucleo 0:
                             // mientras no coincida con g_modeEpoch, lo que
                             // haya aqui es del modo ANTERIOR y no vale.
  bool     fingerPresent;
  bool     signalReliable;
  int      liveBPM;
  int      liveSpO2;
  bool     liveSpO2Valid;
  float    perfusion;
  uint8_t  ppgProgress;      // 0..100
  bool     ppgReady;
  bool     ppgFailed;
  int      finalBPM;
  int      finalSpO2;
  uint32_t lastBeatMs;
  uint32_t rawIR;            // ultima muestra cruda: es lo que se enseña en
  uint32_t rawRed;           // Diagnostico para saber si el sensor esta vivo
  uint32_t lastSampleMs;
  uint16_t recoveries;       // veces que ha habido que reiniciar el sensor
  uint32_t pilaLibre;        // bytes de pila sin usar del nucleo 0
};

struct Report {
  int   bpm;
  int   spo2;
  bool  recorded;
};

// --- Objetos de hardware ---
U8G2_ST7920_128X64_F_HW_SPI u8g2(U8G2_R0, OLED_CS_PIN, OLED_RESET_PIN);
MAX30105 particleSensor;
Preferences prefs;

// --- Estado de la aplicacion (propiedad EXCLUSIVA del nucleo 1) ---
AppState  currentState   = STATE_BOOT;
Emotion   currentEmotion = EMOTION_NORMAL;
uint32_t  stateEnteredMs = 0;
uint32_t  lastInteraction = 0;
uint32_t  lastFrameMs    = 0;
uint32_t  lastKeyPollMs  = 0;
uint16_t  animFrame      = 0;
bool      needsRedraw    = true;
bool      isBlinking     = false;
uint32_t  nextBlinkMs    = 0;
uint32_t  blinkEndsMs    = 0;

const char *MENU_ITEMS[] = { "Auto-Chequeo", "MEDIBOT (red)", "Historial",
                             "Diagnostico", "Calibrar teclado", "Sobre Medibot" };
const int   MENU_N = sizeof(MENU_ITEMS) / sizeof(MENU_ITEMS[0]);
#define MENU_VISIBLES 4

int  mainMenuSelection = 0;      // indice dentro de MENU_ITEMS
int  mainMenuTop  = 0;           // primera entrada visible (lista con scroll)
int  aboutPage    = 0;
int  resultPage   = 0;
int  historyPage  = 0;

int   patientBPM  = 0;
int   patientSpO2 = 0;

Report historyReports[3] = {{0,0,false},{0,0,false},{0,0,false}};
int    historyCount = 0;

char diagnosis1[40];
char diagnosis2[40];
char errorDetail[32] = "";

// --- Resultado del autotest de arranque (el nucleo 0 puede reintentarlo) ---
volatile bool hwMaxOk    = false;
volatile bool hwMaxWrong = false;      // hay un chip, pero no es un MAX3010x
volatile uint8_t hwMaxPartId = 0;
volatile uint8_t hwMaxRevId  = 0;
volatile uint32_t hwMaxBusHz = MAX_I2C_HZ_FAST;
volatile uint8_t  hwI2cCount = 0;      // dispositivos vistos en el bus
volatile uint8_t  hwI2cFirst = 0;      // direccion del primero
volatile uint32_t hwMaxIntentos = 0;   // intentos de deteccion acumulados
// Veredicto electrico del bus: dice si el problema es un cable, no el codigo.
// Los dos enum viven aqui, y no junto a las funciones que los usan, porque el
// IDE de Arduino inserta los prototipos ANTES de la primera funcion del
// fichero: un tipo declarado mas abajo da "was not declared in this scope".
enum I2cDiag : uint8_t { I2C_CON_PULLUP, I2C_SIN_PULLUP, I2C_CORTO };
enum LinNivel : uint8_t { LIN_ALTA, LIN_BAJA, LIN_AIRE };
volatile uint8_t  hwI2cDiag = I2C_SIN_PULLUP;
bool  nvsOk = false;                   // la memoria no volatil responde

// ---------------------------------------------------------------------
//  MIGAS DE PAN Y MODO SEGURO
// ---------------------------------------------------------------------
//  Cuando el equipo entra en bucle de reinicio, el mensaje del fallo pasa
//  volando y muchas veces no se llega a leer: solo se ve la cabecera de la
//  ROM, que no dice nada. Dos medidas:
//
//  1. MIGAS: en cada paso se apunta DONDE esta el programa en la memoria RTC,
//     que SOBREVIVE a un reinicio por fallo. En el arranque siguiente se
//     imprime, asi que se sabe en que se quedo aunque no se leyera nada.
//
//  2. MODO SEGURO: si el reinicio anterior fue un fallo o un watchdog, se
//     arranca SIN RED. Asi el equipo deja de reiniciarse solo, se puede usar
//     y queda claro si lo que mata al equipo es la parte de red. Desde la
//     pantalla de MEDIBOT se puede activar a mano con OK, y el siguiente
//     arranque limpio (boton de reset o encendido) vuelve a la normalidad.
#define RTC_MAGIA 0x4D454449UL          // "MEDI"
RTC_DATA_ATTR static uint32_t rtcMagia;
RTC_DATA_ATTR static char     rtcPaso[16];
bool modoSeguro = false;

// Por que se reinicio la ultima vez. Si el equipo se queda reiniciandose,
// esta linea dice si fue un fallo del programa, el watchdog o la corriente,
// que es lo primero que hay que saber y no se ve en el arranque de la ROM.
static const char *motivoReinicio() {
  switch (esp_reset_reason()) {
    case ESP_RST_POWERON:  return "encendido normal";
    case ESP_RST_EXT:      return "boton de reset";
    case ESP_RST_SW:       return "reinicio por software";
    case ESP_RST_PANIC:    return "FALLO DEL PROGRAMA (panic)";
    case ESP_RST_INT_WDT:  return "WATCHDOG de interrupciones";
    case ESP_RST_TASK_WDT: return "WATCHDOG de tarea (algo se bloqueo)";
    case ESP_RST_WDT:      return "WATCHDOG";
    case ESP_RST_BROWNOUT: return "BAJON DE TENSION (alimentacion justa)";
    case ESP_RST_DEEPSLEEP:return "salida de deep sleep";
    default:               return "desconocido";
  }
}

static void paso(const char *p) {
  rtcMagia = RTC_MAGIA;
  snprintf(rtcPaso, sizeof(rtcPaso), "%s", p);
}


// --- Comunicacion entre nucleos ---
//  REGLA DE g_vitalsMux (no es un consejo, es la causa de un reinicio real):
//  entre portENTER_CRITICAL y portEXIT_CRITICAL las INTERRUPCIONES DEL NUCLEO
//  ESTAN CORTADAS. Ahi dentro solo pueden ir asignaciones a memoria. Nada de
//  WiFi.*, MDNS.*, HTTPClient, Wire.*, Serial.*, delay(), millis() prolongado
//  ni ninguna funcion que pueda pedir un mutex o esperar: el nucleo se queda
//  colgado con las interrupciones apagadas y a los 300 ms salta el watchdog:
//      Guru Meditation Error: Core 0 panic'ed (Interrupt wdt timeout on CPU0)
//  Si hace falta un dato del WiFi, se LEE ANTES en una variable local y dentro
//  del bloqueo solo se copia. Lo vigila pruebas/banco_firmware/comprobar_criticas.py
static NetInfo       g_net;
static Vitals        g_vitals;
static portMUX_TYPE  g_vitalsMux  = portMUX_INITIALIZER_UNLOCKED;
// El nucleo 0 la pone cuando el router ya ha dado IP; el nucleo 1 la mira para
// arrancar el servidor web. Se hace asi, y no arrancandolo desde el nucleo 0,
// para que TODO lo del servidor ocurra en un solo nucleo.
volatile bool        g_wifiListo  = false;
// El servidor ya escucha. Lo mira tambien la pantalla MEDIBOT, para enseñar la
// direccion que hay que escribir en el navegador.
bool                 webArrancado = false;
volatile SensorMode  g_sensorMode = SENS_IDLE;
volatile uint32_t    g_modeEpoch  = 0;
TaskHandle_t         SensorTaskHandle = NULL;

// --- Prototipos ---
void     setState(AppState s);
Button   keypadPoll();
int16_t  keypadLastMv();
uint16_t keypadCounts();
uint8_t  keypadActiveCount();
void     keypadRestoreDefaults();
bool     keypadHasCalibration();
void     keypadLoadCalibration();
bool     keypadSaveCalibration();
Button   keypadMeasureIdle();
void     keypadWizardStart();
bool     keypadWizardStep(uint32_t now);
void     processInputs(Button btn);
Vitals   vitalsGet();
NetInfo  netGet();
bool     vitalsVigentes(const Vitals &v);
void     sensorRequest(SensorMode m);
void     sensorReposo();
void     netForzarBusqueda();
void     sensorTaskCode(void *pv);
void     evaluateDiagnoses();
void     saveReport();
void     drawAvatar(Emotion emo, int frame, int cx, int cy, float s);
void     drawCenteredStr(int y, const char *text);
void     drawProgressBar(int x, int y, int w, int h, uint8_t pct);
void     drawSpinner(int cx, int cy, int r, int frame);
void     drawHeart(int cx, int cy, int r);
void     drawFingerIcon(int cx, int cy, int frame);
void     drawBootScreen();
void     drawMenu();
void     drawAbout();
void     drawHistoryUI();
void     drawDiagScreen();
void     drawMedibotScreen();
void     drawTriageResult();
void     drawSignalError();
void     drawWizardScreen();
void     renderUI();
static bool screenIsAnimated();

// =====================================================================
// 3. TECLADO ANALOGICO (ADKeyboard en una unica entrada ADC)
// =====================================================================
//  Todo se trabaja en MILIVOLTIOS MEDIDOS EN EL PIN, que es lo unico que el
//  ESP32 puede saber de verdad: asi la calibracion es valida sea cual sea la
//  tension de alimentacion del modulo y su tolerancia de resistencias.
// =====================================================================
struct KeypadRuntime {
  Button   raw           = BTN_NONE;   // clasificacion instantanea
  Button   stable        = BTN_NONE;   // clasificacion ya antirrebotada
  uint32_t lastRawChange = 0;
  uint32_t pressStartMs  = 0;
  uint32_t lastRepeatMs  = 0;
  float    ema           = 0.0f;
  bool     emaInit       = false;
  int16_t  mv            = 0;          // tension filtrada en el pin
  uint16_t counts        = 0;          // cuentas crudas del ADC (diagnostico)
  int16_t  idleMv        = 3300;       // nivel de reposo medido al arrancar
  bool     idleOk        = false;
  bool     desconectado  = false;      // el pin flota: no hay teclado enchufado
  int16_t  spread        = 0;          // dispersion de la ultima lectura, en mV
  uint32_t saltos        = 0;          // bitmap de las ultimas lecturas que bailaron
} keypad;

static int cmpI16(const void *a, const void *b) {
  const int16_t x = *(const int16_t *)a, y = *(const int16_t *)b;
  return (x > y) - (x < y);
}
static inline int16_t difAbs(int16_t a, int16_t b) { return (a > b) ? (a - b) : (b - a); }

// Lectura filtrada: N muestras -> mediana robusta (media de las 3 centrales).
// La mediana elimina los picos impulsivos del ADC del ESP32; el EMA posterior
// alisa el ruido de baja amplitud.
static int16_t keypadReadRawMv() {
  int16_t s[KEY_SAMPLES];
  uint32_t acc = 0;
  for (uint8_t i = 0; i < KEY_SAMPLES; i++) {
    const int bruto = analogRead(KEYPAD_PIN);
    acc += bruto;
#if USE_ESP_ADC_CAL
    s[i] = (int16_t)analogReadMilliVolts(KEYPAD_PIN);
#else
    s[i] = (int16_t)((bruto * ADC_FULLSCALE_MV) / (float)ADC_MAX_COUNTS);
#endif
  }
  keypad.counts = (uint16_t)(acc / KEY_SAMPLES);
  qsort(s, KEY_SAMPLES, sizeof(int16_t), cmpI16);
  // Las muestras se han tomado una detras de otra: si salen desperdigadas es
  // que el pin no esta sujeto a nada. Lo usa keypadPoll() para saber si hay
  // teclado conectado.
  keypad.spread = (int16_t)(s[KEY_SAMPLES - 1] - s[0]);
  const uint8_t m = KEY_SAMPLES / 2;
  return (int16_t)((s[m - 1] + s[m] + s[m + 1]) / 3);
}

// Lectura PROFUNDA, solo para el asistente: mediana de WIZ_SUBMUESTRAS
// conversiones seguidas. Devuelve tambien la dispersion (el recorrido entre el
// percentil 10 y el 90), que es lo que dice cuanto baila esa tension.
static int16_t keypadReadDeepMv(int16_t *dispersion) {
  int16_t s[WIZ_SUBMUESTRAS];
  uint32_t acc = 0;
  for (uint8_t i = 0; i < WIZ_SUBMUESTRAS; i++) {
    const int bruto = analogRead(KEYPAD_PIN);
    acc += bruto;
#if USE_ESP_ADC_CAL
    s[i] = (int16_t)analogReadMilliVolts(KEYPAD_PIN);
#else
    s[i] = (int16_t)((bruto * ADC_FULLSCALE_MV) / (float)ADC_MAX_COUNTS);
#endif
  }
  keypad.counts = (uint16_t)(acc / WIZ_SUBMUESTRAS);
  qsort(s, WIZ_SUBMUESTRAS, sizeof(int16_t), cmpI16);
  if (dispersion) {
    const uint8_t bajo = WIZ_SUBMUESTRAS / 10;             // percentil 10
    const uint8_t alto = WIZ_SUBMUESTRAS - 1 - bajo;       // percentil 90
    *dispersion = (int16_t)(s[alto] - s[bajo]);
  }
  return s[WIZ_SUBMUESTRAS / 2];
}

// Mediana y dispersion (percentil 10 a 90) de un monton de lecturas ya
// tomadas. Es lo que convierte las WIZ_MUESTRAS de un boton en dos numeros:
// donde esta y cuanto se mueve.
static int16_t medianaYDispersion(int16_t *v, uint8_t n, int16_t *dispersion) {
  if (n == 0) { if (dispersion) *dispersion = 0; return 0; }
  qsort(v, n, sizeof(int16_t), cmpI16);
  if (dispersion) {
    const uint8_t bajo = n / 10;
    const uint8_t alto = (uint8_t)(n - 1 - bajo);
    *dispersion = (int16_t)(v[alto] - v[bajo]);
  }
  return v[n / 2];
}

// Clasificacion: rangos independientes + histeresis + guarda de reposo.
// Devuelve BTN_NONE si la tension cae en zona muerta, si esta pegada al
// reposo, o si (por una tabla mal puesta) encajase en dos botones a la vez:
// es imposible que se detecten dos botones simultaneos.
static Button keypadClassify(int16_t mv, Button held) {
  if (keypad.idleOk && difAbs(mv, keypad.idleMv) < KEY_IDLE_GUARD_MV) return BTN_NONE;
  Button found = BTN_NONE;
  uint8_t matches = 0;
  for (uint8_t i = 0; i < KEYPAD_MAP_SIZE; i++) {
    int16_t lo = KEYPAD_MAP[i].mvMin, hi = KEYPAD_MAP[i].mvMax;
    if (lo > hi) continue;                                  // boton desactivado
    if (KEYPAD_MAP[i].id == held) { lo -= KEY_HYSTERESIS_MV; hi += KEY_HYSTERESIS_MV; }
    if (mv >= lo && mv <= hi) { found = KEYPAD_MAP[i].id; matches++; }
  }
  return (matches == 1) ? found : BTN_NONE;
}

// Devuelve UN evento por pulsacion (flanco), o autorepeticion en UP/DOWN.
Button keypadPoll() {
  const uint32_t now = millis();
  const int16_t bruto = keypadReadRawMv();

  if (!keypad.emaInit) { keypad.ema = bruto; keypad.emaInit = true; }
  else keypad.ema = KEY_EMA_ALPHA * bruto + (1.0f - KEY_EMA_ALPHA) * keypad.ema;
  keypad.mv = (int16_t)keypad.ema;

  // ¿Sigue el teclado enchufado? Se apunta si esta lectura ha salido
  // desperdigada y se mira cuantas de las ultimas KEY_SALTOS_VENTANA lo han
  // hecho. Un cable suelto las ensucia casi todas; pulsar un boton, ninguna.
  keypad.saltos = (keypad.saltos << 1) | (keypad.spread > KEY_SPREAD_MV ? 1u : 0u);
  const uint8_t bailando = (uint8_t)__builtin_popcount(
      keypad.saltos & ((KEY_SALTOS_VENTANA >= 32) ? 0xFFFFFFFFu
                                                  : ((1u << KEY_SALTOS_VENTANA) - 1u)));
  const bool suelto = (bailando >= KEY_SALTOS_MIN);
  if (suelto != keypad.desconectado) {
    keypad.desconectado = suelto;
    if (suelto) {
      Serial.println(F("[TECLADO] La lectura no para de bailar: cable suelto en el teclado."));
      Serial.println(F("          Se ignoran las pulsaciones hasta que vuelva a estar quieta."));
    } else {
      Serial.println(F("[TECLADO] Lectura estable otra vez: teclado operativo"));
    }
  }
  // Con el pin al aire, cualquier "pulsacion" es ruido: no se devuelve ninguna.
  if (keypad.desconectado) { keypad.raw = BTN_NONE; keypad.stable = BTN_NONE; return BTN_NONE; }

  const Button raw = keypadClassify(keypad.mv, keypad.stable);
  if (raw != keypad.raw) { keypad.raw = raw; keypad.lastRawChange = now; }

  Button ev = BTN_NONE;
  const uint32_t needed = (raw == BTN_NONE) ? KEY_RELEASE_MS : KEY_DEBOUNCE_MS;

  if (raw != keypad.stable) {
    if (now - keypad.lastRawChange >= needed) {
      keypad.stable = raw;
      if (raw != BTN_NONE) {
        ev = raw;
        keypad.pressStartMs = now;
        keypad.lastRepeatMs = now;
      }
    }
  }
#if KEY_REPEAT_ENABLED
  else if (raw == BTN_UP || raw == BTN_DOWN) {
    if (now - keypad.pressStartMs > KEY_REPEAT_DELAY_MS &&
        now - keypad.lastRepeatMs > KEY_REPEAT_RATE_MS) {
      keypad.lastRepeatMs = now;
      ev = raw;
    }
  }
#endif
  return ev;
}

int16_t  keypadLastMv()   { return keypad.mv; }
uint16_t keypadCounts()   { return keypad.counts; }

static const char *buttonName(Button b) {
  return (b < BTN_COUNT) ? BTN_NOMBRE[b] : "----";
}

// Cuantos botones tienen ahora mismo un rango utilizable
uint8_t keypadActiveCount() {
  uint8_t n = 0;
  for (uint8_t i = 0; i < KEYPAD_MAP_SIZE; i++)
    if (KEYPAD_MAP[i].mvMin <= KEYPAD_MAP[i].mvMax) n++;
  return n;
}

void keypadRestoreDefaults() {
  memcpy(KEYPAD_MAP, KEYPAD_MAP_DEFECTO, sizeof(KEYPAD_MAP));
  Serial.println(F("[TECLADO] Tabla de fabrica restaurada"));
}

// ---------------------------------------------------------------------
// 3.1 CALIBRACION GUARDADA EN LA MEMORIA DEL ESP32 (NVS)
// ---------------------------------------------------------------------
struct CalEntrada { uint8_t id; int16_t mn, mx; };
struct CalBlob {
  uint16_t   magic;
  int16_t    reposo;
  uint8_t    n;
  CalEntrada e[BTN_COUNT];
};

bool keypadHasCalibration() {
  if (!nvsOk) return false;
  CalBlob b;
  if (prefs.getBytesLength("keycal") != sizeof(b)) return false;
  prefs.getBytes("keycal", &b, sizeof(b));
  return (b.magic == CAL_MAGIC && b.n > 0);
}

void keypadLoadCalibration() {
  if (!nvsOk) return;
  CalBlob b;
  if (prefs.getBytesLength("keycal") != sizeof(b)) return;
  prefs.getBytes("keycal", &b, sizeof(b));
  if (b.magic != CAL_MAGIC || b.n == 0 || b.n > BTN_COUNT) return;

  for (uint8_t i = 0; i < KEYPAD_MAP_SIZE; i++) { KEYPAD_MAP[i].mvMin = 1; KEYPAD_MAP[i].mvMax = 0; }
  for (uint8_t i = 0; i < b.n; i++)
    for (uint8_t k = 0; k < KEYPAD_MAP_SIZE; k++)
      if (KEYPAD_MAP[k].id == b.e[i].id) {
        KEYPAD_MAP[k].mvMin = b.e[i].mn;
        KEYPAD_MAP[k].mvMax = b.e[i].mx;
      }
  keypad.idleMv = b.reposo;
  keypad.idleOk = true;
  Serial.printf("[TECLADO] Calibracion cargada. Reposo %d mV\n", (int)b.reposo);
  for (uint8_t k = 0; k < KEYPAD_MAP_SIZE; k++)
    if (KEYPAD_MAP[k].mvMin <= KEYPAD_MAP[k].mvMax)
      Serial.printf("   %-7s %d..%d mV\n", buttonName(KEYPAD_MAP[k].id),
                    (int)KEYPAD_MAP[k].mvMin, (int)KEYPAD_MAP[k].mvMax);
}

// Guarda y COMPRUEBA releyendo: si la NVS no admite la escritura, hay que
// decirlo en pantalla, no dejar que el asistente vuelva a salir en cada
// arranque sin explicar por que.
bool keypadSaveCalibration() {
  if (!nvsOk) {
    Serial.println(F("[TECLADO] NO se guarda: la memoria no volatil no responde"));
    return false;
  }
  CalBlob b;
  memset(&b, 0, sizeof(b));
  b.magic  = CAL_MAGIC;
  b.reposo = keypad.idleMv;
  for (uint8_t k = 0; k < KEYPAD_MAP_SIZE && b.n < BTN_COUNT; k++) {
    if (KEYPAD_MAP[k].mvMin <= KEYPAD_MAP[k].mvMax) {
      b.e[b.n].id = KEYPAD_MAP[k].id;
      b.e[b.n].mn = KEYPAD_MAP[k].mvMin;
      b.e[b.n].mx = KEYPAD_MAP[k].mvMax;
      b.n++;
    }
  }
  const size_t escritos = prefs.putBytes("keycal", &b, sizeof(b));

  CalBlob v;
  memset(&v, 0, sizeof(v));
  const bool ok = (escritos == sizeof(b)) &&
                  (prefs.getBytesLength("keycal") == sizeof(b)) &&
                  (prefs.getBytes("keycal", &v, sizeof(v)) == sizeof(v)) &&
                  (memcmp(&v, &b, sizeof(b)) == 0);
  if (ok) Serial.printf("[TECLADO] Calibracion guardada y releida (%u botones)\n", (unsigned)b.n);
  else    Serial.printf("[TECLADO] FALLO al guardar (escritos %u de %u bytes)\n",
                        (unsigned)escritos, (unsigned)sizeof(b));
  return ok;
}

// Al encender: mide el nivel de reposo. Si ese nivel coincide con un boton de
// la tabla es que el usuario esta MANTENIENDO una tecla -> se devuelve, y esa
// es la via de escape para abrir el asistente cuando la calibracion guardada
// quedo mal y no se puede navegar el menu.
Button keypadMeasureIdle() {
  int16_t m[24];
  for (uint8_t i = 0; i < 24; i++) { m[i] = keypadReadRawMv(); delay(20); }
  qsort(m, 24, sizeof(int16_t), cmpI16);
  const int16_t mediana = m[12];
  const int16_t disp = m[21] - m[2];

  keypad.idleOk = false;                       // sin guarda, para poder clasificar
  const Button coincide = keypadClassify(mediana, BTN_NONE);
  keypad.ema = mediana; keypad.emaInit = true; keypad.mv = mediana;

  Serial.printf("[TECLADO] Nivel en reposo: %d mV (ADC %u, dispersion %d mV)\n",
                (int)mediana, (unsigned)keypad.counts, (int)disp);

  // Casi a 0 V y ademas inestable = pin flotando, no un boton mantenido. Sin
  // esta comprobacion el firmware lo toma por una tecla pulsada, abre el
  // asistente de calibracion y se queda un minuto esperando pulsaciones que
  // no pueden llegar porque el teclado no esta conectado.
  keypad.desconectado = (mediana < KEY_AIRE_MV && disp > KEY_AIRE_DISP_MV);
  if (keypad.desconectado) {
    Serial.println(F("[TECLADO] Lecturas casi a 0 V y saltando: el teclado NO esta conectado."));
    Serial.printf( "          Revisa VCC->3V3, GND->GND y la salida analogica -> GPIO%d\n",
                  (int)KEYPAD_PIN);
    keypad.idleMv = mediana;
    keypad.idleOk = true;
    return BTN_NONE;
  }

  if (coincide != BTN_NONE) {
    Serial.printf("[TECLADO] Coincide con %s: hay un boton pulsado al arrancar\n",
                  buttonName(coincide));
    return coincide;
  }
  keypad.idleMv = mediana;
  keypad.idleOk = true;
  return BTN_NONE;
}

// ---------------------------------------------------------------------
// 3.2 ASISTENTE DE CALIBRACION
// ---------------------------------------------------------------------
//  Mide los botones REALES y calcula los rangos: no hay que adivinar ningun
//  umbral ni copiar numeros a mano en el codigo.
struct Asistente {
  uint8_t  paso;                 // indice dentro de BTN_ORDEN
  uint8_t  fase;                 // 0 reposo | 1 pidiendo | 2 soltar | 3 calcular | 4 resumen
  uint32_t t0;
  uint32_t estableDesde;
  int16_t  ultimo;
  int16_t  centro[BTN_COUNT];
  int16_t  disp[BTN_COUNT];      // cuanto baila cada boton (percentil 10-90)
  int16_t  ancho[BTN_COUNT];     // semiancho que se le ha dado al rango
  bool     hecho[BTN_COUNT];
  uint8_t  capturados;
  bool     guardado;
  bool     esperandoSoltar;
  char     aviso[30];
  int16_t  muestras[WIZ_MUESTRAS];   // lecturas acumuladas del boton en curso
  uint8_t  nMuestras;
  int16_t  dispReposo;
} wiz;

void keypadWizardStart() {
  memset(&wiz, 0, sizeof(wiz));
  wiz.t0 = millis();
  wiz.estableDesde = wiz.t0;     // OJO: sin esto, la fase 0 terminaria al instante
  wiz.ultimo = keypad.mv;
  keypad.idleOk = false;         // durante el asistente no se filtra por reposo
  Serial.println(F("\n[ASISTENTE] Calibracion del teclado. Suelta todos los botones..."));
}

// Devuelve true cuando ha terminado (y ya ha guardado)
bool keypadWizardStep(uint32_t now) {
  int16_t dispAhora = 0;
  const int16_t mv = keypadReadDeepMv(&dispAhora);   // mediana de 25 conversiones
  keypad.ema = KEY_EMA_ALPHA * mv + (1.0f - KEY_EMA_ALPHA) * keypad.ema;
  keypad.mv = (int16_t)keypad.ema;

  if (difAbs(keypad.mv, wiz.ultimo) > WIZ_TOLER_MV) { wiz.ultimo = keypad.mv; wiz.estableDesde = now; }

  switch (wiz.fase) {
    case 0: {
      // ---- medir el reposo ----
      // OJO con como se entra aqui: al asistente se llega MANTENIENDO un boton
      // al encender (la via de escape), o pulsando OK en el menu. En los dos
      // casos, al empezar hay una tecla pulsada. Si se midiera el reposo en
      // ese momento se tomaria el nivel del BOTON como reposo y despues
      // ninguna pulsacion pareceria distinta de el: el asistente terminaria
      // sin capturar lo que toca y guardando una tabla que deja el teclado
      // inservible (el reposo real se leeria como una tecla pulsada siempre).
      //
      // Por eso se espera a dos cosas: que la lectura no encaje con ningun
      // boton (= teclado suelto) y que lleve quieta WIZ_REPOSO_MS. Con una
      // gracia de WIZ_SOLTAR_MS por si la tabla de partida esta tan mal que
      // el propio reposo cae dentro de un rango.
      const bool pareceBoton = (keypadClassify(keypad.mv, BTN_NONE) != BTN_NONE);
      wiz.esperandoSoltar = pareceBoton && (now - wiz.t0 < WIZ_SOLTAR_MS);
      if (wiz.esperandoSoltar || now - wiz.estableDesde < WIZ_REPOSO_MS) {
        wiz.nMuestras = 0;              // aun no esta quieto: se empieza de cero
        break;
      }
      // El reposo tambien se mide muchas veces: es la referencia contra la que
      // se compara TODO lo demas, asi que una sola lectura no basta.
      if (wiz.nMuestras < WIZ_MUESTRAS) wiz.muestras[wiz.nMuestras++] = mv;
      if (wiz.nMuestras >= WIZ_MUESTRAS) {
        keypad.idleMv = medianaYDispersion(wiz.muestras, wiz.nMuestras, &wiz.dispReposo);
        wiz.nMuestras = 0;
        wiz.fase = 1; wiz.paso = 0; wiz.t0 = now; wiz.estableDesde = now;
        Serial.printf("[ASISTENTE] Reposo = %d mV (%u lecturas, baila %d mV)\n",
                      (int)keypad.idleMv, (unsigned)WIZ_MUESTRAS, (int)wiz.dispReposo);
      }
      break;
    }

    case 1: {                                        // ---- capturar un boton ----
      const Button b = BTN_ORDEN[wiz.paso];
      const bool pulsado = difAbs(keypad.mv, keypad.idleMv) >= WIZ_UMBRAL_MV;

      // Si se suelta a mitad, lo acumulado no sirve: mezclaria la tension del
      // boton con la del reposo y el centro saldria entre los dos.
      if (!pulsado) wiz.nMuestras = 0;
      else if (now - wiz.estableDesde >= WIZ_ESTABLE_MS && wiz.nMuestras < WIZ_MUESTRAS)
        wiz.muestras[wiz.nMuestras++] = mv;

      const bool seAcaboElTiempo = (now - wiz.t0 >= WIZ_SALTO_MS);
      if (wiz.nMuestras >= WIZ_MUESTRAS ||
          (seAcaboElTiempo && wiz.nMuestras >= WIZ_MIN_MUESTRAS)) {
        wiz.centro[b] = medianaYDispersion(wiz.muestras, wiz.nMuestras, &wiz.disp[b]);
        wiz.hecho[b]  = true;
        wiz.capturados++;
        Serial.printf("[ASISTENTE] %-7s = %d mV  (%u lecturas, baila %d mV)\n",
                      buttonName(b), (int)wiz.centro[b],
                      (unsigned)wiz.nMuestras, (int)wiz.disp[b]);
        wiz.nMuestras = 0;
        wiz.fase = 2; wiz.t0 = now;
      } else if (seAcaboElTiempo) {
        Serial.printf("[ASISTENTE] %s omitido (sin pulsacion sostenida)\n", buttonName(b));
        wiz.nMuestras = 0;
        wiz.fase = 2; wiz.t0 = now;
      }
      break;
    }

    case 2:                                          // ---- esperar a que suelte ----
      if (difAbs(keypad.mv, keypad.idleMv) < WIZ_UMBRAL_MV / 2 || (now - wiz.t0 > 8000)) {
        wiz.paso++;
        if (wiz.paso >= BTN_ORDEN_N) { wiz.fase = 3; wiz.t0 = now; }
        else { wiz.fase = 1; wiz.t0 = now; wiz.estableDesde = now; }
      }
      break;

    case 3:                                          // ---- calcular y guardar ----
      // A cada boton se le da TODO el sitio que haya hasta su vecino mas
      // cercano (otro boton capturado o el propio reposo), menos una franja de
      // guarda: cuanto mas ancho el rango, mas tolera que la tension se mueva
      // con la temperatura, la alimentacion o un cable largo. Solo se recorta
      // si choca con el vecino, y se avisa si queda mas estrecho de lo que
      // pide la dispersion que se acaba de medir en ese mismo boton.
      for (uint8_t k = 0; k < KEYPAD_MAP_SIZE; k++) { KEYPAD_MAP[k].mvMin = 1; KEYPAD_MAP[k].mvMax = 0; }
      wiz.aviso[0] = '\0';
      for (uint8_t k = 0; k < KEYPAD_MAP_SIZE; k++) {
        const Button b = KEYPAD_MAP[k].id;
        if (!wiz.hecho[b]) continue;
        int16_t hueco = difAbs(wiz.centro[b], keypad.idleMv);
        for (uint8_t j = 0; j < KEYPAD_MAP_SIZE; j++) {
          const Button o = KEYPAD_MAP[j].id;
          if (o == b || !wiz.hecho[o]) continue;
          const int16_t d = difAbs(wiz.centro[b], wiz.centro[o]);
          if (d < hueco) hueco = d;
        }
        int16_t medio = hueco / 2 - WIZ_SEPARACION;      // todo lo que cabe
        if (medio > WIZ_MARGEN_MAX) medio = WIZ_MARGEN_MAX;
        if (medio < WIZ_MARGEN_ABS_MIN) {
          snprintf(wiz.aviso, sizeof(wiz.aviso), "%s se confunde", buttonName(b));
          Serial.printf("[ASISTENTE] %s descartado: solo %d mV hasta su vecino\n",
                        buttonName(b), (int)hueco);
          continue;                                  // se queda desactivado
        }
        // Lo que ese boton NECESITA para no escaparse: lo que baila, con
        // factor de seguridad. Si no cabe, el boton sigue valiendo, pero se
        // dice para que quien calibre sepa que va justo.
        const int16_t necesario = (int16_t)((wiz.disp[b] * WIZ_DISP_FACTOR) / 2);
        if (medio < necesario || medio < WIZ_MARGEN_MIN) {
          snprintf(wiz.aviso, sizeof(wiz.aviso), "%s va justo (%d mV)",
                   buttonName(b), (int)medio);
          Serial.printf("[ASISTENTE] %s: rango de +-%d mV, y baila %d mV. Va justo.\n",
                        buttonName(b), (int)medio, (int)wiz.disp[b]);
        }
        wiz.ancho[b] = medio;
        KEYPAD_MAP[k].mvMin = wiz.centro[b] - medio;
        KEYPAD_MAP[k].mvMax = wiz.centro[b] + medio;
        Serial.printf("[ASISTENTE] %-7s rango %d..%d mV (centro %d, +-%d)\n",
                      buttonName(b), (int)KEYPAD_MAP[k].mvMin, (int)KEYPAD_MAP[k].mvMax,
                      (int)wiz.centro[b], (int)medio);
      }
      if (keypadActiveCount() == 0) {
        // Ningun boton utilizable: no se guarda nada (no habria que guardar) y
        // se vuelve a la tabla de fabrica, para no dejar el equipo sin teclado.
        keypadRestoreDefaults();
        snprintf(wiz.aviso, sizeof(wiz.aviso), "Revisa el cableado");
        wiz.guardado = false;
        Serial.println(F("[ASISTENTE] Ningun boton valido: no hay nada que guardar"));
      } else {
        wiz.guardado = keypadSaveCalibration();
        if (!wiz.guardado) snprintf(wiz.aviso, sizeof(wiz.aviso), "NO se pudo guardar");
      }
      keypad.idleOk = true;
      wiz.fase = 4; wiz.t0 = now;
      break;

    default:
      return (now - wiz.t0 > (wiz.guardado ? 3000UL : 6000UL));   // resumen
  }
  return false;
}

// =====================================================================
// 4. SENSOR MAX30102 (SE EJECUTA EN EL NUCLEO 0)
// =====================================================================
// Tras setup(), el bus I2C lo usa EXCLUSIVAMENTE el nucleo 0 (la pantalla va
// por SPI), asi que no hace falta un mutex de bus. Lo unico compartido entre
// nucleos es la estructura g_vitals, protegida con spinlock.
// =====================================================================

// ---------------------------------------------------------------------
// 4.1 ARRANQUE, VERIFICACION Y RECUPERACION DEL SENSOR
// ---------------------------------------------------------------------
//  Todo lo que toca el bus I2C vive aqui. Se llama desde setup() (nucleo 1,
//  antes de crear la tarea) y desde la tarea del nucleo 0, que es quien
//  reintenta si el sensor no aparecio o si deja de dar muestras. Nunca a la
//  vez: despues de setup() el bus es exclusivo del nucleo 0.

// Lectura cruda de un registro. false = el dispositivo no contesta.
static bool rawRead(uint8_t addr, uint8_t reg, uint8_t &valor) {
  Wire.beginTransmission(addr);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(addr, (uint8_t)1) != 1) return false;
  valor = Wire.read();
  return true;
}

// ---- Estado ELECTRICO de las lineas, antes de hablar por el bus ----
//  Sin esto, "no contesta nadie" puede ser un cable suelto, un cruce de SDA y
//  SCL o un cortocircuito, y no hay forma de distinguirlos. Se mide mirando el
//  pin como entrada normal: un bus I2C sano esta en reposo ALTO y quieto,
//  porque los modulos llevan resistencias de pull-up a 3V3.
static LinNivel i2cNivel(uint8_t pin, bool conPullup) {
  pinMode(pin, conPullup ? INPUT_PULLUP : INPUT);
  delayMicroseconds(400);
  uint8_t altos = 0;
  for (uint8_t i = 0; i < 16; i++) {
    if (digitalRead(pin)) altos++;
    delayMicroseconds(60);
  }
  if (altos >= 15) return LIN_ALTA;      // firme arriba: hay pull-up
  if (altos <= 1)  return LIN_BAJA;      // firme abajo
  return LIN_AIRE;                       // bailando: el pin flota
}

// Suelta el bus, mide las dos lineas y lo devuelve. Deja el veredicto en
// hwI2cDiag (lo lee tambien la pantalla de Diagnostico).
static uint8_t i2cRevisarLineas() {
  Wire.end();                                     // soltar los pines
  const LinNivel sdaSin = i2cNivel(I2C_SDA_PIN, false);
  const LinNivel sdaCon = i2cNivel(I2C_SDA_PIN, true);
  const LinNivel sclSin = i2cNivel(I2C_SCL_PIN, false);
  const LinNivel sclCon = i2cNivel(I2C_SCL_PIN, true);
  Wire.begin(I2C_SDA_PIN, I2C_SCL_PIN);           // y devolverlo al periferico
  Wire.setClock(hwMaxBusHz);

  uint8_t d;
  // Sigue abajo aunque el propio ESP32 tire hacia arriba -> algo la clava a GND.
  if (sdaCon == LIN_BAJA || sclCon == LIN_BAJA)        d = I2C_CORTO;
  // Arriba y quieta sin ayuda -> hay pull-up externo: el modulo tiene corriente.
  else if (sdaSin == LIN_ALTA && sclSin == LIN_ALTA)   d = I2C_CON_PULLUP;
  else                                                 d = I2C_SIN_PULLUP;
  hwI2cDiag = d;
  return d;
}

// Explica el veredicto en castellano. Solo se imprime cuando CAMBIA, para no
// llenar el Monitor Serie con la misma frase cada pocos segundos.
static void i2cExplicar(uint8_t d) {
  switch (d) {
    case I2C_CORTO:
      Serial.println(F("[I2C] SDA o SCL clavada a 0 V. Suele ser un cable tocando GND"));
      Serial.println(F("      o un modulo que ha bloqueado el bus. Desconectalos y"));
      Serial.println(F("      vuelve a conectarlos de uno en uno."));
      break;
    case I2C_SIN_PULLUP:
      Serial.println(F("[I2C] Lineas AL AIRE: no hay pull-up, o sea que el modulo NO"));
      Serial.println(F("      esta conectado o NO le llega corriente. Comprueba:"));
      Serial.println(F("        VIN -> 3V3    GND -> GND    SDA -> GPIO21    SCL -> GPIO22"));
      Serial.println(F("      (si el ESP32 y el modulo no comparten GND, tampoco funciona)"));
      break;
    default:
      Serial.println(F("[I2C] Hay pull-up (el modulo tiene corriente) pero nadie contesta"));
      Serial.println(F("      en 0x57: mira si SDA y SCL estan cambiadas de sitio, o si"));
      Serial.println(F("      el chip no es un MAX30102."));
      break;
  }
}

// Recorre el bus y apunta cuantos dispositivos contestan. Es lo primero que
// hay que mirar cuando "el sensor no funciona": 0 dispositivos significa
// cableado o alimentacion, no software.
static void i2cScan(bool verboso) {
  const uint8_t diag = i2cRevisarLineas();      // primero la electricidad
  uint8_t n = 0, primera = 0;
  for (uint8_t a = 1; a < 127; a++) {
    Wire.beginTransmission(a);
    if (Wire.endTransmission() == 0) {
      if (n == 0) primera = a;
      n++;
      if (verboso) Serial.printf("[I2C] responde 0x%02X\n", a);
    }
  }
  hwI2cCount = n;
  hwI2cFirst = primera;

  // El veredicto se cuenta entero la primera vez y luego solo si CAMBIA: si no,
  // el Monitor Serie se llena con la misma frase y no se ve nada mas.
  static uint8_t ultimoDiag = 0xFF;
  const bool cambio = (diag != ultimoDiag);
  ultimoDiag = diag;
  if (!verboso && !cambio) return;

  if (n == 0) {
    Serial.println(F("[I2C] NADIE contesta: revisa VIN, GND, SDA(21) y SCL(22)"));
  } else if (diag == I2C_SIN_PULLUP) {
    // Con las lineas al aire el escaneo "encuentra" direcciones distintas cada
    // vez: son lecturas al azar del pin flotando, no dispositivos de verdad.
    Serial.printf("[I2C] %u direccion(es) que cambian en cada vuelta: es RUIDO de\n"
                  "      lineas al aire, no hay ningun dispositivo conectado.\n",
                  (unsigned)n);
  }
  i2cExplicar(diag);
}

// Aplica la configuracion y la COMPRUEBA releyendo los registros. Sin esta
// verificacion, una escritura perdida deja el sensor mudo (o a oscuras) y el
// firmware convencido de que todo va bien.
static bool sensorConfigure(bool ledsOn) {
  particleSensor.setup(MAX_LED_BRIGHTNESS, MAX_SAMPLE_AVERAGE, MAX_LED_MODE,
                       MAX_SAMPLE_RATE, MAX_PULSE_WIDTH, MAX_ADC_RANGE);
  const uint8_t amp = ledsOn ? MAX_LED_BRIGHTNESS : 0x00;
  particleSensor.setPulseAmplitudeRed(amp);
  particleSensor.setPulseAmplitudeIR(amp);
  particleSensor.setPulseAmplitudeGreen(0x00);
  particleSensor.clearFIFO();

  uint8_t modo = 0, led1 = 0, led2 = 0;
  const bool leido = rawRead(MAX_I2C_ADDR, 0x09, modo) &&    // MODE_CONFIG
                     rawRead(MAX_I2C_ADDR, 0x0C, led1) &&    // LED1 (rojo)
                     rawRead(MAX_I2C_ADDR, 0x0D, led2);      // LED2 (IR)
  if (!leido) {
    Serial.println(F("[MAX] No se pueden releer los registros: el bus no responde"));
    return false;
  }
  const uint8_t modoEsperado = (MAX_LED_MODE == 3) ? 0x07 : (MAX_LED_MODE == 2 ? 0x03 : 0x02);
  if ((modo & 0x07) != modoEsperado || led1 != amp || led2 != amp) {
    Serial.printf("[MAX] La configuracion NO se aplico: modo=0x%02X (esperado 0x%02X), "
                  "LED rojo=0x%02X IR=0x%02X (esperado 0x%02X)\n",
                  modo & 0x07, modoEsperado, led1, led2, amp);
    return false;
  }
  Serial.printf("[MAX] Configurado y verificado: modo 0x%02X, LED 0x%02X, %d Hz\n",
                modoEsperado, amp, PPG_EFFECTIVE_SPS);
  return true;
}

// Deteccion completa: escaneo del bus, identificacion del chip y
// configuracion. Prueba a 400 kHz y, si no contesta, a 100 kHz.
static bool sensorBegin(bool conEscaneo) {
  hwMaxWrong = false;
  const uint32_t intento = hwMaxIntentos++;
  // Los primeros intentos se cuentan enteros; a partir de ahi solo un
  // recordatorio de vez en cuando.
  const bool verboso = (intento < 2) || (intento % SENSOR_SCAN_CADA == 0);
  if (conEscaneo) i2cScan(verboso);

  const uint32_t velocidades[2] = { MAX_I2C_HZ_FAST, MAX_I2C_HZ_SAFE };
  for (uint8_t v = 0; v < 2; v++) {
    Wire.setClock(velocidades[v]);
    for (uint8_t intento = 0; intento < MAX_INIT_RETRIES; intento++) {
      uint8_t id = 0;
      if (rawRead(MAX_I2C_ADDR, 0xFF, id)) {          // PART ID
        if (id == 0x11) {                             // MAX30100: otro chip
          hwMaxWrong = true;
          hwMaxPartId = id;
          Serial.println(F("[MAX] Es un MAX30100 (ID 0x11): NO vale con la libreria"));
          Serial.println(F("      MAX3010x. Hace falta un MAX30102 o un MAX30105."));
          return false;
        }
        if (id == 0x15 && particleSensor.begin(Wire, velocidades[v], MAX_I2C_ADDR)) {
          hwMaxPartId = id;
          hwMaxRevId  = particleSensor.getRevisionID();
          hwMaxBusHz  = velocidades[v];
          Wire.setClock(velocidades[v]);              // begin() la reajusta
          if (sensorConfigure(MAX_LEDS_ALWAYS_ON != 0)) {
            hwMaxOk = true;
            hwMaxIntentos = 0;                   // volver al reintento rapido
            Serial.printf("[MAX] Sensor OK en 0x%02X (ID 0x%02X rev 0x%02X) a %lu kHz, "
                          "intento %u\n", MAX_I2C_ADDR, id, hwMaxRevId,
                          (unsigned long)(velocidades[v] / 1000), (unsigned)(intento + 1));
            return true;
          }
        }
      }
      delay(80);
    }
    if (v == 0 && verboso)
      Serial.println(F("[MAX] Sin respuesta a 400 kHz: se reintenta a 100 kHz"));
  }
  hwMaxOk = false;
  if (verboso)
    Serial.printf("[MAX] Sensor de pulso NO detectado en 0x57 (intento %lu)\n",
                  (unsigned long)(intento + 1));
  return false;
}

// Reinicio en caliente cuando el sensor deja de entregar muestras.
static bool sensorRecover() {
  Serial.println(F("[MAX] El sensor ha dejado de dar muestras: reiniciandolo"));
  portENTER_CRITICAL(&g_vitalsMux);
  g_vitals.recoveries++;
  portEXIT_CRITICAL(&g_vitalsMux);
  Wire.setClock(hwMaxBusHz);
  if (particleSensor.begin(Wire, hwMaxBusHz, MAX_I2C_ADDR) && sensorConfigure(true)) return true;
  hwMaxOk = false;                       // a partir de aqui, redeteccion de fondo
  return false;
}

// ---------------------------------------------------------------------
// 4.2 Media recortada (descarta el minimo y el maximo) -> robusta a outliers
// ---------------------------------------------------------------------
static float trimmedMean(const float *src, uint8_t n) {
  if (n == 0) return 0.0f;
  if (n > 32) n = 32;
  float v[32];
  memcpy(v, src, n * sizeof(float));
  for (uint8_t i = 1; i < n; i++) {          // insertion sort
    float key = v[i];
    int8_t j = (int8_t)i - 1;
    while (j >= 0 && v[j] > key) { v[j + 1] = v[j]; j--; }
    v[j + 1] = key;
  }
  if (n <= 3) {
    float s = 0.0f;
    for (uint8_t i = 0; i < n; i++) s += v[i];
    return s / n;
  }
  float s = 0.0f;
  for (uint8_t i = 1; i < n - 1; i++) s += v[i];
  return s / (float)(n - 2);
}

// ---------------------------------------------------------------------
// 4.3 DETECTOR DE LATIDOS PROPIO
// ---------------------------------------------------------------------
//  POR QUE NO SE USA checkForBeat() DE LA LIBRERIA SPARKFUN:
//  esa funcion pasa la muestra por
//        averageDCEstimator(int32_t *p, uint16_t x)   <-- uint16_t
//        lowPassFIRFilter(int16_t din)                <-- int16_t
//  es decir, TRUNCA la muestra IR a 16 bits. Con el dedo puesto el MAX30102
//  entrega entre 60.000 y 250.000 cuentas, muy por encima de 65.535: la linea
//  de base "da la vuelta" y el detector dispara latidos falsos. Ademas cuenta
//  la onda dicrota (el rebote que sigue a cada sistole) como un latido mas.
//  Midiendo una PPG sintetica de 72 BPM, aquel camino devolvia ~150 BPM.
//
//  Este detector trabaja en coma flotante sobre la muestra completa:
//    1) linea de base DC por media exponencial;
//    2) senal AC = DC - IR  (la IR BAJA en la sistole) y filtro paso bajo;
//    3) envolvente con decaimiento -> umbral ADAPTATIVO (no depende de la
//       amplitud absoluta, que cambia con cada dedo y cada perfusion);
//    4) disparo por cruce de umbral con histeresis + periodo refractario;
//    5) BPM = MEDIANA de los ultimos intervalos, robusta a un latido perdido
//       o a uno de mas (la media aritmetica no lo es).
static void beatReset(BeatDetector &b) {
  memset(&b, 0, sizeof(b));
  b.armed = true;
}

// Devuelve true en la muestra exacta en la que se detecta un latido.
static bool beatUpdate(BeatDetector &b, uint32_t ir, uint32_t now) {
  const float x = (float)ir;
  if (!b.init) { b.dc = x; b.lp = 0.0f; b.env = 0.0f; b.init = true; b.armed = true; }

  b.dc += (x - b.dc) * BEAT_DC_ALPHA;
  const float ac = b.dc - x;                 // positiva durante la sistole
  b.lp += (ac - b.lp) * BEAT_LP_ALPHA;

  b.env *= BEAT_ENV_DECAY;
  if (b.lp > b.env) b.env = b.lp;

  if (b.warmup < BEAT_WARMUP_SAMPLES) { b.warmup++; return false; }
  if (b.env < BEAT_MIN_AMPLITUDE) { b.armed = true; return false; }   // solo ruido

  const float thHigh = b.env * BEAT_TH_HIGH;
  const float thLow  = b.env * BEAT_TH_LOW;

  if (!b.armed) {
    if (b.lp < thLow) b.armed = true;
    return false;
  }
  if (b.lp < thHigh) return false;
  if (b.lastBeatMs != 0 && (now - b.lastBeatMs) < BEAT_REFRACTORY_MS) return false;

  if (b.lastBeatMs != 0) {
    const uint32_t d = now - b.lastBeatMs;
    if (d >= BEAT_REFRACTORY_MS && d <= BEAT_MAX_INTERVAL_MS) {
      b.intervals[b.intervalIndex] = d;
      b.intervalIndex = (b.intervalIndex + 1) % BEAT_RING;
      if (b.intervalCount < BEAT_RING) b.intervalCount++;
    } else {
      b.intervalCount = 0;                   // intervalo imposible: se descarta
      b.intervalIndex = 0;                   // la serie entera y se empieza de nuevo
    }
  }
  b.lastBeatMs = now;
  b.armed = false;
  return true;
}

// Pulso en BPM a partir de la MEDIANA de los intervalos. 0 = aun no hay dato.
static float beatBPM(const BeatDetector &b) {
  if (b.intervalCount < BEAT_MIN_INTERVALS) return 0.0f;
  uint32_t v[BEAT_RING];
  memcpy(v, b.intervals, b.intervalCount * sizeof(uint32_t));
  for (uint8_t i = 1; i < b.intervalCount; i++) {       // insertion sort
    const uint32_t key = v[i];
    int8_t j = (int8_t)i - 1;
    while (j >= 0 && v[j] > key) { v[j + 1] = v[j]; j--; }
    v[j + 1] = key;
  }
  const uint8_t n = b.intervalCount;
  const uint32_t med = (n & 1) ? v[n / 2] : (v[n / 2 - 1] + v[n / 2]) / 2;
  return med ? (60000.0f / (float)med) : 0.0f;
}

// ---------------------------------------------------------------------
// 4.4 Estado interno de la adquisicion PPG
// ---------------------------------------------------------------------
struct PpgState {
  uint32_t irBuf[SPO2_BUFFER_LEN];
  uint32_t redBuf[SPO2_BUFFER_LEN];
  int16_t  fill;
  uint8_t  decim;
  bool     fingerRaw;
  bool     fingerStable;
  uint32_t fingerChangeMs;
  BeatDetector beat;
  float    okBPM[PPG_MAX_READINGS];
  float    okSpO2[PPG_MAX_READINGS];
  uint8_t  okCount;
  uint32_t startMs;
  uint32_t lastFingerSeenMs;
} ppg;

static void ppgResetBuffers() {
  ppg.fill = 0;
  ppg.decim = 0;
  ppg.okCount = 0;
  beatReset(ppg.beat);
}

static void ppgResetAll(uint32_t now) {
  ppgResetBuffers();
  ppg.fingerRaw = false;
  ppg.fingerStable = false;
  ppg.fingerChangeMs = now;
  ppg.startMs = now;
  ppg.lastFingerSeenMs = now;
}

// ---------------------------------------------------------------------
// 4.5 Publicacion atomica hacia la UI
// ---------------------------------------------------------------------
Vitals vitalsGet() {
  Vitals copy;
  portENTER_CRITICAL(&g_vitalsMux);
  copy = g_vitals;
  portEXIT_CRITICAL(&g_vitalsMux);
  return copy;
}

// true solo si lo que trae 'v' pertenece a la medida que se esta pidiendo
// ahora mismo (ver el comentario del campo Vitals::epoch).
bool vitalsVigentes(const Vitals &v) { return v.epoch == g_modeEpoch; }

// Fuera de la medida el nucleo 0 se dedica a la red... salvo en modo seguro,
// donde se queda parado para descartar que el problema venga de ahi.
void sensorReposo() { sensorRequest(modoSeguro ? SENS_IDLE : SENS_NET); }

NetInfo netGet() {
  NetInfo copy;
  portENTER_CRITICAL(&g_vitalsMux);
  copy = g_net;
  portEXIT_CRITICAL(&g_vitalsMux);
  return copy;
}

static void vitalsClear() {
  portENTER_CRITICAL(&g_vitalsMux);
  memset((void *)&g_vitals, 0, sizeof(g_vitals));
  portEXIT_CRITICAL(&g_vitalsMux);
}

// La UI es la unica que cambia de modo; el nucleo 0 detecta el cambio por el
// contador de epoca y reinicia sus acumuladores. Asi el nucleo 0 NUNCA toca
// la maquina de estados (esa era la carrera de datos del codigo original).
// Fuera de una medida el nucleo 0 se dedica a la red: asi la conexion con
// MEDIBOT se mantiene viva y los datos siguen actualizandose solos.
void sensorReposo();

void sensorRequest(SensorMode m) {
  portENTER_CRITICAL(&g_vitalsMux);
  memset((void *)&g_vitals, 0, sizeof(g_vitals));
  g_sensorMode = m;
  g_modeEpoch++;
  portEXIT_CRITICAL(&g_vitalsMux);
}

// ---------------------------------------------------------------------
// 4.6 Indice de perfusion: amplitud pulsatil (AC) frente a continua (DC)
// ---------------------------------------------------------------------
static float perfusionIndex(const uint32_t *buf, int16_t n) {
  if (n < 8) return 0.0f;
  uint32_t mn = buf[0], mx = buf[0];
  double sum = 0;
  for (int16_t i = 0; i < n; i++) {
    if (buf[i] < mn) mn = buf[i];
    if (buf[i] > mx) mx = buf[i];
    sum += buf[i];
  }
  double dc = sum / n;
  if (dc <= 0) return 0.0f;
  return (float)(((double)(mx - mn) / dc) * 100.0);
}

// ---------------------------------------------------------------------
// 4.7 Procesado de una muestra PPG (100 Hz)
// ---------------------------------------------------------------------
static void ppgProcessSample(uint32_t ir, uint32_t red, uint32_t now) {
  // Muestra cruda: la pantalla de Diagnostico la enseña tal cual, que es la
  // forma mas rapida de saber si el sensor esta vivo y si detecta el dedo.
  portENTER_CRITICAL(&g_vitalsMux);
  g_vitals.rawIR        = ir;
  g_vitals.rawRed       = red;
  g_vitals.lastSampleMs = now;
  portEXIT_CRITICAL(&g_vitalsMux);

  // --- Deteccion de dedo con histeresis + tiempo de estabilidad ---
  bool raw = ppg.fingerRaw;
  if (!raw && ir > FINGER_IR_ON)  raw = true;
  if ( raw && ir < FINGER_IR_OFF) raw = false;
  if (raw != ppg.fingerRaw) { ppg.fingerRaw = raw; ppg.fingerChangeMs = now; }

  bool stable = ppg.fingerStable;
  if (ppg.fingerRaw != ppg.fingerStable &&
      (now - ppg.fingerChangeMs) >= FINGER_STABLE_MS) {
    stable = ppg.fingerRaw;
  }

  if (stable != ppg.fingerStable) {
    ppg.fingerStable = stable;
    if (!stable) {                       // dedo retirado -> todo a cero
      ppgResetBuffers();
      portENTER_CRITICAL(&g_vitalsMux);
      g_vitals.fingerPresent  = false;
      g_vitals.signalReliable = false;
      g_vitals.liveBPM        = 0;
      g_vitals.liveSpO2       = 0;
      g_vitals.liveSpO2Valid  = false;
      g_vitals.perfusion      = 0.0f;
      g_vitals.ppgProgress    = 0;
      portEXIT_CRITICAL(&g_vitalsMux);
    } else {
      portENTER_CRITICAL(&g_vitalsMux);
      g_vitals.fingerPresent = true;
      portEXIT_CRITICAL(&g_vitalsMux);
    }
  }
  if (!ppg.fingerStable) return;
  ppg.lastFingerSeenMs = now;

  // --- Latido (detector propio, bloque 4.3) ---
  if (beatUpdate(ppg.beat, ir, now)) {
    portENTER_CRITICAL(&g_vitalsMux);
    g_vitals.lastBeatMs = now;
    portEXIT_CRITICAL(&g_vitalsMux);
  }

  // --- Diezmado a 25 Hz para el algoritmo de Maxim (FS = 25 en la libreria) ---
  if (++ppg.decim < SPO2_DECIMATION) return;
  ppg.decim = 0;

  if (ppg.fill < SPO2_BUFFER_LEN) {
    ppg.irBuf[ppg.fill]  = ir;
    ppg.redBuf[ppg.fill] = red;
    ppg.fill++;
    if (ppg.fill < SPO2_BUFFER_LEN) return;
  }

  // Ventana completa (4 s): calcular SpO2 y desplazar 1 s
  int32_t spo2v = 0, hrv = 0;
  int8_t  spo2Valid = 0, hrValid = 0;
  maxim_heart_rate_and_oxygen_saturation(ppg.irBuf, SPO2_BUFFER_LEN, ppg.redBuf,
                                         &spo2v, &spo2Valid, &hrv, &hrValid);

  const float pi = perfusionIndex(ppg.irBuf, SPO2_BUFFER_LEN);

  // Pulso: mediana de los intervalos propios. Mientras no haya suficientes
  // latidos se acepta el HR que devuelve el algoritmo de Maxim como respaldo.
  float bpm = beatBPM(ppg.beat);
  if (bpm < HR_MIN_BPM && hrValid == 1 && hrv >= HR_MIN_BPM && hrv <= HR_MAX_BPM)
    bpm = (float)hrv;

  const int spo2Corrected = (int)spo2v + SPO2_OFFSET;
  const bool spo2Ok = (spo2Valid == 1 &&
                       spo2Corrected >= SPO2_MIN_VALID &&
                       spo2Corrected <= SPO2_MAX_VALID);
  const bool hrOk   = (bpm >= HR_MIN_BPM && bpm <= HR_MAX_BPM);
  const bool piOk   = (pi >= MIN_PERFUSION_INDEX);
  const bool reliable = spo2Ok && hrOk && piOk;

  if (reliable && ppg.okCount < PPG_MAX_READINGS) {
    ppg.okBPM[ppg.okCount]  = bpm;
    ppg.okSpO2[ppg.okCount] = (float)spo2Corrected;
    ppg.okCount++;
  }

  portENTER_CRITICAL(&g_vitalsMux);
  g_vitals.perfusion      = pi;
  g_vitals.liveBPM        = hrOk ? (int)(bpm + 0.5f) : 0;
  g_vitals.liveSpO2       = spo2Ok ? spo2Corrected : 0;
  g_vitals.liveSpO2Valid  = spo2Ok && piOk;
  g_vitals.signalReliable = reliable;
  g_vitals.ppgProgress    = (uint8_t)((ppg.okCount * 100UL) / PPG_TARGET_READINGS);
  portEXIT_CRITICAL(&g_vitalsMux);

  if (ppg.okCount >= PPG_TARGET_READINGS) {
    const float mBPM  = trimmedMean(ppg.okBPM,  ppg.okCount);
    const float mSpO2 = trimmedMean(ppg.okSpO2, ppg.okCount);
    portENTER_CRITICAL(&g_vitalsMux);
    g_vitals.finalBPM    = (int)(mBPM + 0.5f);
    g_vitals.finalSpO2   = (int)(mSpO2 + 0.5f);
    g_vitals.ppgProgress = 100;
    g_vitals.ppgReady    = true;
    portEXIT_CRITICAL(&g_vitalsMux);
    return;
  }

  // Desplazar la ventana 1 segundo (25 muestras)
  for (int16_t i = SPO2_SHIFT; i < SPO2_BUFFER_LEN; i++) {
    ppg.irBuf[i - SPO2_SHIFT]  = ppg.irBuf[i];
    ppg.redBuf[i - SPO2_SHIFT] = ppg.redBuf[i];
  }
  ppg.fill = SPO2_BUFFER_LEN - SPO2_SHIFT;
}

// ---------------------------------------------------------------------
// 4.8 Lectura no bloqueante del FIFO del MAX3010x
// ---------------------------------------------------------------------
static uint32_t ppgLastSampleMs = 0;          // ultima muestra leida del FIFO

static void ppgUpdate(uint32_t now) {
  if (!hwMaxOk) {
    portENTER_CRITICAL(&g_vitalsMux);
    g_vitals.ppgFailed = true;
    portEXIT_CRITICAL(&g_vitalsMux);
    return;
  }

  particleSensor.check();                     // no bloquea (a diferencia de getIR())
  // Las muestras que esperan en el buffer se tomaron ANTES de "now", una cada
  // 1000/PPG_EFFECTIVE_SPS ms. Fecharlas todas igual desplazaria los intervalos
  // entre latidos y, con ellos, el pulso; se reconstruye el instante de cada una.
  const uint8_t pendientes = particleSensor.available();
  const uint32_t periodoMs = 1000UL / PPG_EFFECTIVE_SPS;
  uint8_t idx = 0, guard = 0;
  while (particleSensor.available() && guard++ < 32) {
    const uint32_t ir  = particleSensor.getFIFOIR();
    const uint32_t red = particleSensor.getFIFORed();
    particleSensor.nextSample();
    const uint32_t atraso = (idx < pendientes) ? (pendientes - 1 - idx) * periodoMs : 0;
    idx++;
    ppgLastSampleMs = now;
    ppgProcessSample(ir, red, now - atraso);
    if (ppg.okCount >= PPG_TARGET_READINGS) return;   // medida completada
  }

  // El sensor ha dejado de entregar muestras (un pico de ruido en el bus, un
  // cable que baila, el chip colgado). En vez de quedarse en "Esperando
  // dedo..." para siempre, se reinicia y se sigue.
  if (now - ppgLastSampleMs > PPG_STALL_MS) {
    ppgLastSampleMs = now;
    if (sensorRecover()) {
      ppgResetAll(now);
    } else {
      portENTER_CRITICAL(&g_vitalsMux);
      g_vitals.ppgFailed = true;
      portEXIT_CRITICAL(&g_vitalsMux);
    }
    return;
  }

  // Vigilancia de tiempos: sin dedo demasiado tiempo, o medida interminable
  if (!ppg.fingerStable && (now - ppg.lastFingerSeenMs > NO_FINGER_TIMEOUT_MS)) {
    portENTER_CRITICAL(&g_vitalsMux);
    g_vitals.ppgFailed = true;
    portEXIT_CRITICAL(&g_vitalsMux);
  } else if (now - ppg.startMs > PPG_TIMEOUT_MS) {
    portENTER_CRITICAL(&g_vitalsMux);
    g_vitals.ppgFailed = true;
    portEXIT_CRITICAL(&g_vitalsMux);
  }
}

// ---------------------------------------------------------------------
// 4.9 RED: WIFI, BUSQUEDA DE MEDIBOT Y LECTURA DE SU API
// ---------------------------------------------------------------------
//  Corre en el nucleo 0, en el modo SENS_NET, y se detiene mientras se mide
//  (las tareas de WiFi tienen prioridad alta en ese nucleo y se comerian las
//  muestras del sensor).
struct NetRT {
  uint8_t   etapa = NET_OFF;
  uint16_t  host = 1;
  uint32_t  t0 = 0;
  uint32_t  ultimoJson = 0;
  uint32_t  ultimoFallo = 0;
  IPAddress ip;
  uint16_t  puerto = MEDIBOT_PORT_MAIN;
  uint8_t   fallosJson = 0;
} nt;

// La UI (nucleo 1) no toca la red: solo deja esta peticion y el nucleo 0 la
// atiende en su siguiente vuelta.
volatile bool g_netRetry = false;
void netForzarBusqueda() { g_netRetry = true; }

// El accesor del resultado de mDNS CAMBIO DE NOMBRE en el core 3.x del ESP32:
// hasta la 2.x era MDNS.IP(i) y desde la 3.x es MDNS.address(i). Se elige en
// tiempo de compilacion para que el sketch valga con las dos.
static inline IPAddress mdnsDireccion(int i) {
#if defined(ESP_ARDUINO_VERSION_MAJOR) && ESP_ARDUINO_VERSION_MAJOR >= 3
  return MDNS.address(i);
#else
  return MDNS.IP(i);
#endif
}

static void netMsg(const char *m) {
  portENTER_CRITICAL(&g_vitalsMux);
  snprintf(g_net.msg, sizeof(g_net.msg), "%s", m);
  portEXIT_CRITICAL(&g_vitalsMux);
  Serial.printf("[RED] %s\n", m);
}

static void netEtapa(uint8_t e) {
  nt.etapa = e;
  portENTER_CRITICAL(&g_vitalsMux);
  g_net.etapa = e;
  portEXIT_CRITICAL(&g_vitalsMux);
}

// Confirma que en esa IP esta MEDIBOT y no el router o una impresora.
// Vision_MEDIBOT.py y Pastillero.py firman TODAS sus respuestas con una
// cabecera propia: es la huella perfecta y no hay que tocar la Raspberry.
static bool huellaMedibot(IPAddress ip, uint16_t puerto) {
  HTTPClient http;
  http.setConnectTimeout(500);
  http.setTimeout(900);
  http.setReuse(false);
  const char *cabeceras[] = { "X-Medibot-Build", "X-Pillbox-Build" };
  const char *ruta = (puerto == MEDIBOT_PORT_MAIN) ? MEDIBOT_API : "/";
  if (!http.begin(ip.toString(), puerto, ruta)) return false;
  http.collectHeaders(cabeceras, 2);
  const int code = (puerto == MEDIBOT_PORT_MAIN) ? http.GET() : http.sendRequest("HEAD");
  const bool ok = (code == 200) &&
                  (http.hasHeader("X-Medibot-Build") || http.hasHeader("X-Pillbox-Build"));
  http.end();
  return ok;
}

static bool netProbarIP(IPAddress ip) {
  if (huellaMedibot(ip, MEDIBOT_PORT_MAIN)) { nt.ip = ip; nt.puerto = MEDIBOT_PORT_MAIN; return true; }
  if (huellaMedibot(ip, MEDIBOT_PORT_ALT))  { nt.ip = ip; nt.puerto = MEDIBOT_PORT_ALT;  return true; }
  return false;
}

static void netEncontrado() {
  netEtapa(NET_FOUND);
  portENTER_CRITICAL(&g_vitalsMux);
  g_net.ip = (uint32_t)nt.ip;
  g_net.puerto = nt.puerto;
  g_net.progreso = 100;
  portEXIT_CRITICAL(&g_vitalsMux);
  Serial.printf("[RED] MEDIBOT en %s:%u\n", nt.ip.toString().c_str(), (unsigned)nt.puerto);
  netMsg("MEDIBOT localizado");
}

static void netLeerJson() {
  if (nt.puerto != MEDIBOT_PORT_MAIN) {      // el Pastillero no tiene /api/esp32
    portENTER_CRITICAL(&g_vitalsMux); g_net.jsonOk = false; portEXIT_CRITICAL(&g_vitalsMux);
    netMsg("Solo Pastillero (5001)");
    return;
  }
  HTTPClient http;
  http.setConnectTimeout(600);
  http.setTimeout(1200);
  http.setReuse(true);
  if (!http.begin(nt.ip.toString(), nt.puerto, MEDIBOT_API)) return;
  const int code = http.GET();
  bool ok = false;
  if (code == 200) {
    JsonDocument doc;
    if (!deserializeJson(doc, http.getString())) {
      portENTER_CRITICAL(&g_vitalsMux);
      g_net.sistema     = doc["s"]  | 0;
      g_net.detecciones = doc["d"]  | 0;
      g_net.rojos       = doc["ro"] | 0;
      g_net.fps1        = doc["f1"] | 0;
      g_net.fps2        = doc["f2"] | 0;
      g_net.caraX       = doc["fx"] | 0;
      g_net.caraY       = doc["fy"] | 0;
      g_net.grabando    = ((doc["r"] | 0) != 0);
      g_net.jsonOk      = true;
      g_net.jsonMs      = millis();
      portEXIT_CRITICAL(&g_vitalsMux);
      ok = true;
    }
  }
  http.end();
  if (ok) {
    nt.fallosJson = 0;
  } else {
    portENTER_CRITICAL(&g_vitalsMux); g_net.jsonOk = false; portEXIT_CRITICAL(&g_vitalsMux);
    // Varios fallos seguidos: lo normal es que MEDIBOT haya cambiado de IP
    // (DHCP), asi que se vuelve a buscar en vez de insistir en la vieja.
    if (++nt.fallosJson >= JSON_FAILS_RESCAN) {
      nt.fallosJson = 0;
      netMsg("Buscando MEDIBOT");
      netEtapa(NET_MDNS);
      portENTER_CRITICAL(&g_vitalsMux); g_net.ip = 0; portEXIT_CRITICAL(&g_vitalsMux);
    }
  }
}

static void netArrancar(uint32_t ahora) {
  if (nt.etapa == NET_FOUND) return;         // ya localizado: solo refrescar
  nt.t0 = ahora;
  nt.host = 1;
  portENTER_CRITICAL(&g_vitalsMux);
  g_net.progreso = 0; g_net.ip = 0; g_net.jsonOk = false;
  portEXIT_CRITICAL(&g_vitalsMux);
  if (WiFi.status() == WL_CONNECTED) {
    netMsg("Buscando MEDIBOT");
    netEtapa(NET_MDNS);
  } else {
    WiFi.mode(WIFI_STA);
    WiFi.setSleep(true);
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    netMsg("Conectando a " WIFI_SSID);
    netEtapa(NET_WIFI);
  }
}

static void netTrabajo(uint32_t ahora) {
  if (g_netRetry) {                          // el usuario ha pulsado OK
    g_netRetry = false;
    netEtapa(NET_OFF);
  }
  switch (nt.etapa) {
    case NET_OFF:
      netArrancar(ahora);
      break;

    case NET_WIFI:
      paso("red: wifi");
      if (WiFi.status() == WL_CONNECTED) {
        // Se leen FUERA del bloqueo a proposito: WiFi.RSSI() y WiFi.localIP()
        // piden un mutex al driver de WiFi, y pedir un mutex con las
        // interrupciones cortadas cuelga el nucleo -> "Interrupt wdt timeout
        // on CPU0". Ver la nota de g_vitalsMux.
        const int8_t   rssi = (int8_t)WiFi.RSSI();
        const uint32_t mia  = (uint32_t)WiFi.localIP();
        portENTER_CRITICAL(&g_vitalsMux);
        g_net.rssi = rssi;
        g_net.ipPropia = mia;
        portEXIT_CRITICAL(&g_vitalsMux);
        g_wifiListo = true;               // el nucleo 1 arrancara el servidor web
        Serial.printf("[RED] WiFi OK, IP de este ESP32: %s\n", WiFi.localIP().toString().c_str());
        netMsg("Buscando MEDIBOT");
        netEtapa(NET_MDNS);
        nt.t0 = ahora;
      } else if (ahora - nt.t0 > WIFI_TIMEOUT_MS) {
        netMsg("Sin WiFi (" WIFI_SSID ")");
        netEtapa(NET_FAIL);
        nt.ultimoFallo = ahora;
      }
      break;

    case NET_MDNS: {
      paso("red: mdns");
      static bool mdnsListo = false;
      if (!mdnsListo) {
        mdnsListo = MDNS.begin("medibot-triaje");
        // Asi el equipo se puede abrir por nombre (http://medibot-triaje.local/)
        // sin tener que saberse la IP de memoria.
        if (mdnsListo) MDNS.addService("http", "tcp", WEB_PUERTO);
      }
      const int n = MDNS.queryService(MEDIBOT_MDNS_SVC, "tcp");
      for (int i = 0; i < n; i++)
        if (netProbarIP(mdnsDireccion(i))) { netEncontrado(); return; }
      const IPAddress h = MDNS.queryHost(MEDIBOT_MDNS_HOST);
      if ((uint32_t)h != 0 && netProbarIP(h)) { netEncontrado(); return; }
      netMsg("Explorando la red");
      nt.host = 1;
      netEtapa(NET_SWEEP);
      break;
    }

    case NET_SWEEP: {
      paso("red: barrido");
      const IPAddress base = WiFi.localIP();
      for (uint8_t k = 0; k < 4 && nt.host <= 254; k++, nt.host++) {
        if (nt.host == base[3]) continue;
        const IPAddress ip(base[0], base[1], base[2], (uint8_t)nt.host);
        WiFiClient c;
        if (c.connect(ip, MEDIBOT_PORT_MAIN, SWEEP_TIMEOUT_MS)) {
          c.stop();
          if (netProbarIP(ip)) { netEncontrado(); return; }
        } else if (c.connect(ip, MEDIBOT_PORT_ALT, SWEEP_TIMEOUT_MS)) {
          c.stop();
          if (netProbarIP(ip)) { netEncontrado(); return; }
        }
      }
      portENTER_CRITICAL(&g_vitalsMux);
      g_net.progreso = (uint8_t)((nt.host * 100UL) / 254UL);
      portEXIT_CRITICAL(&g_vitalsMux);
      if (nt.host > 254) {
        netMsg("MEDIBOT no responde");
        netEtapa(NET_FAIL);
        nt.ultimoFallo = ahora;
      }
      break;
    }

    case NET_FOUND:
      if (WiFi.status() != WL_CONNECTED) {
        netMsg("WiFi caido");
        netEtapa(NET_FAIL);
        nt.ultimoFallo = ahora;
        break;
      }
      if (ahora - nt.ultimoJson >= JSON_POLL_MS) {
        nt.ultimoJson = ahora;
        paso("red: json");
        netLeerJson();
        const int8_t rssi = (int8_t)WiFi.RSSI();   // fuera del bloqueo (ver NET_WIFI)
        portENTER_CRITICAL(&g_vitalsMux); g_net.rssi = rssi; portEXIT_CRITICAL(&g_vitalsMux);
      }
      break;

    case NET_FAIL:
      // No se abandona: se reintenta solo cada WIFI_RETRY_MS, asi que encender
      // el router o la Raspberry despues basta para que aparezca.
      if (ahora - nt.ultimoFallo >= WIFI_RETRY_MS) {
        netEtapa(NET_OFF);
      }
      break;

    default:
      break;
  }
}

// ---------------------------------------------------------------------
// 4.10 Tarea del nucleo 0
// ---------------------------------------------------------------------
void sensorTaskCode(void *pv) {
  (void)pv;
  uint32_t myEpoch = 0xFFFFFFFF;
  SensorMode myMode = SENS_IDLE;
  uint32_t ultimoReintento = 0;
  uint32_t esperaReintento = SENSOR_RETRY_MS;   // crece si el sensor no aparece
  uint32_t ultimoAvisoPila = 0;

  for (;;) {
    const uint32_t now = millis();

    // Vigilancia de la pila: se publica lo que queda sin usar. Si baja de
    // 1 KB es que falta poco para un desbordamiento (= reinicio seco), asi
    // que ademas se avisa por Serial.
    if (now - ultimoAvisoPila > 5000) {
      ultimoAvisoPila = now;
      const uint32_t libre = uxTaskGetStackHighWaterMark(NULL) * sizeof(StackType_t);
      portENTER_CRITICAL(&g_vitalsMux);
      g_vitals.pilaLibre = libre;
      portEXIT_CRITICAL(&g_vitalsMux);
      if (libre < 1024) Serial.printf("[AVISO] Pila de la tarea al limite: %u bytes\n",
                                      (unsigned)libre);
    }

    if (g_modeEpoch != myEpoch) {
      myEpoch = g_modeEpoch;
      myMode  = g_sensorMode;
      ppgResetAll(now);
      ppgLastSampleMs = now;
      if (hwMaxOk) {
        if (myMode == SENS_PPG) {
          // Encender los LED y comprobar que la escritura ha entrado de
          // verdad: si no, se reconfigura el sensor entero. Sin esto, una
          // escritura perdida deja el sensor a oscuras toda la medida.
          particleSensor.setPulseAmplitudeRed(MAX_LED_BRIGHTNESS);
          particleSensor.setPulseAmplitudeIR(MAX_LED_BRIGHTNESS);
          uint8_t led1 = 0, led2 = 0;
          const bool encendidos = rawRead(MAX_I2C_ADDR, 0x0C, led1) &&
                                  rawRead(MAX_I2C_ADDR, 0x0D, led2) &&
                                  led1 == MAX_LED_BRIGHTNESS && led2 == MAX_LED_BRIGHTNESS;
          if (!encendidos) sensorRecover();
          else             particleSensor.clearFIFO();
#if !MAX_LEDS_ALWAYS_ON
        } else {                                  // en reposo, LED apagados
          particleSensor.setPulseAmplitudeRed(0x00);
          particleSensor.setPulseAmplitudeIR(0x00);
#endif
        }
      }
      if (myMode == SENS_NET) netArrancar(now);
      // A partir de aqui lo que se publique ya pertenece a esta epoca. La UI
      // descarta todo lo que llegue con una epoca distinta, de modo que un
      // resultado (o un fallo) de la medida anterior nunca contamina la nueva.
      portENTER_CRITICAL(&g_vitalsMux);
      g_vitals.epoch = myEpoch;
      portEXIT_CRITICAL(&g_vitalsMux);
    }

    // Sin sensor: se reintenta la deteccion en segundo plano. Asi, si estaba
    // mal conectado o tardo en arrancar, el equipo se recupera solo y no hay
    // que reiniciarlo para poder medir.
    //
    // La espera CRECE (3 s, 6, 12... hasta 30 s) mientras siga sin aparecer:
    // insistir cada 3 s eternamente no lo trae de vuelta, y en cambio machaca
    // el bus, llena el Monitor Serie y le roba tiempo a la red. El escaneo
    // completo del bus, que es lo caro, solo se hace 1 de cada SENSOR_SCAN_CADA.
    if (!hwMaxOk && now - ultimoReintento >= esperaReintento) {
      ultimoReintento = now;
      const bool escanear = (hwMaxIntentos < 3) || (hwMaxIntentos % SENSOR_SCAN_CADA == 0);
      if (sensorBegin(escanear)) {
        Serial.println(F("[MAX] Sensor recuperado: ya se puede medir"));
        esperaReintento = SENSOR_RETRY_MS;
        ppgResetAll(now);
        ppgLastSampleMs = now;
      } else if (esperaReintento < SENSOR_RETRY_MAX_MS) {
        esperaReintento *= 2;
        if (esperaReintento > SENSOR_RETRY_MAX_MS) esperaReintento = SENSOR_RETRY_MAX_MS;
      }
    }

    if (myMode == SENS_PPG) {
      paso("midiendo ppg");
      ppgUpdate(now);
      vTaskDelay(2 / portTICK_PERIOD_MS);
    } else if (myMode == SENS_NET) {
      netTrabajo(now);
      vTaskDelay(20 / portTICK_PERIOD_MS);
    } else {
      vTaskDelay(50 / portTICK_PERIOD_MS);
    }
  }
}

// =====================================================================
// 5. INTERFAZ (NUCLEO 1): ANIMACIONES NO BLOQUEANTES
// =====================================================================
void setState(AppState s) {
  // Diagnostico deja el sensor midiendo para enseñar el IR en vivo; al salir
  // hay que devolverlo a reposo, se salga por donde se salga (boton, tiempo
  // de inactividad o atajo al menu).
  if (currentState == STATE_DIAG && s != STATE_DIAG) sensorReposo();
  currentState   = s;
  stateEnteredMs = millis();
  animFrame      = 0;
  needsRedraw    = true;
}

static inline uint32_t stateElapsed() { return millis() - stateEnteredMs; }

// Pantallas sin animacion: no necesitan refresco continuo
static bool screenIsAnimated() {
  return !(currentState == STATE_ABOUT || currentState == STATE_HISTORY);
}

void drawCenteredStr(int y, const char *text) {
  u8g2.drawStr((128 - u8g2.getStrWidth(text)) / 2, y, text);
}

void drawProgressBar(int x, int y, int w, int h, uint8_t pct) {
  if (pct > 100) pct = 100;
  u8g2.drawRFrame(x, y, w, h, 2);
  int inner = ((w - 4) * pct) / 100;
  if (inner > 0) u8g2.drawBox(x + 2, y + 2, inner, h - 4);
}

void drawSpinner(int cx, int cy, int r, int frame) {
  const int active = (frame / 2) % 8;
  for (int i = 0; i < 8; i++) {
    const float a = i * (PI / 4.0f);
    const int px = cx + (int)(cos(a) * r);
    const int py = cy + (int)(sin(a) * r);
    if (i == active)                u8g2.drawDisc(px, py, 2);
    else if (i == (active + 7) % 8) u8g2.drawDisc(px, py, 1);
    else                            u8g2.drawPixel(px, py);
  }
}

void drawHeart(int cx, int cy, int r) {
  if (r < 3) r = 3;
  u8g2.drawDisc(cx - r / 2, cy - r / 3, r / 2);
  u8g2.drawDisc(cx + r / 2, cy - r / 3, r / 2);
  u8g2.drawTriangle(cx - r, cy - r / 3, cx + r, cy - r / 3, cx, cy + r);
}

void drawFingerIcon(int cx, int cy, int frame) {
  const int off = ((frame / 4) % 2 == 0) ? 0 : 2;      // pequeno vaiven
  u8g2.drawRBox(cx - 4, cy - 10 + off, 8, 14, 3);
  u8g2.setDrawColor(0);
  u8g2.drawHLine(cx - 3, cy - 6 + off, 6);
  u8g2.setDrawColor(1);
  u8g2.drawHLine(cx - 7, cy + 7, 14);                  // sensor
  u8g2.drawHLine(cx - 7, cy + 8, 14);
}

// --- CARA DEL ROBOT (se conserva el diseno original) ---
void drawAvatar(Emotion emo, int frame, int cx, int cy, float s) {
  if (emo == EMOTION_NORMAL || emo == EMOTION_HAPPY) {
    cy += (int)(sin(millis() / 300.0) * (3.0 * s));    // respiracion, con millis()
  }

  const int eW = max(1, (int)(22 * s));
  const int eH = max(1, (int)(26 * s));
  const int lx = cx - (int)(23 * s) - eW / 2;
  const int rx = cx + (int)(23 * s) - eW / 2;
  const int ey = cy - (int)(14 * s);
  const int mx = cx;
  const int my = cy + (int)(18 * s);
  const int rad = max(1, min((int)(5 * s), min(eW, eH) / 2));
  const int ebY = cy - (int)(20 * s);

  u8g2.setDrawColor(1);
  if (emo == EMOTION_SAD) {
    u8g2.drawLine(lx, ebY, lx + eW, ebY + (int)(4 * s));
    u8g2.drawLine(rx, ebY + (int)(4 * s), rx + eW, ebY);
  } else if (emo == EMOTION_HAPPY) {
    u8g2.drawBox(lx + (int)(2 * s), ebY - (int)(3 * s), eW - (int)(4 * s), max(1, (int)(3 * s)));
    u8g2.drawBox(rx + (int)(2 * s), ebY - (int)(3 * s), eW - (int)(4 * s), max(1, (int)(3 * s)));
  } else {
    u8g2.drawBox(lx + (int)(2 * s), ebY, eW - (int)(4 * s), max(1, (int)(2 * s)));
    u8g2.drawBox(rx + (int)(2 * s), ebY, eW - (int)(4 * s), max(1, (int)(2 * s)));
  }

  if (isBlinking) {
    u8g2.drawRBox(lx, ey + eH / 2 - (int)(3 * s), eW, max(1, (int)(6 * s)), max(1, (int)(2 * s)));
    u8g2.drawRBox(rx, ey + eH / 2 - (int)(3 * s), eW, max(1, (int)(6 * s)), max(1, (int)(2 * s)));
  } else {
    u8g2.drawRBox(lx, ey, eW, eH, rad);
    u8g2.drawRBox(rx, ey, eW, eH, rad);

    u8g2.setDrawColor(0);
    if (emo == EMOTION_HAPPY) {
      u8g2.drawBox(lx - 1, ey + eH / 2, eW + 2, eH / 2 + 2);
      u8g2.drawBox(rx - 1, ey + eH / 2, eW + 2, eH / 2 + 2);
    }

    const int pW = max(1, (int)(8 * s));
    int pxOff = (eW - pW) / 2;
    int pyOff = (eH - pW) / 2;

    int lookX = 0;
    if (emo == EMOTION_NORMAL) {
      const int ciclo = (frame / 20) % 10;
      if (ciclo == 1)      lookX = -(int)(3 * s);
      else if (ciclo == 5) lookX =  (int)(3 * s);
    }
    if (emo == EMOTION_LOOK_DOWN) pyOff = eH - pW - (int)(2 * s);

    if (emo == EMOTION_LOADING) {
      const int animOff = (int)(((frame % 20) - 10) * s);
      u8g2.drawBox(lx + pxOff, ey + pyOff + animOff, pW, pW);
      u8g2.drawBox(rx + pxOff, ey + pyOff - animOff, pW, pW);
    } else if (emo != EMOTION_HAPPY) {
      u8g2.drawBox(lx + pxOff + lookX, ey + pyOff, pW, pW);
      u8g2.drawBox(rx + pxOff + lookX, ey + pyOff, pW, pW);
    }
  }

  u8g2.setDrawColor(1);
  const int mRad = max(1, (int)(6 * s));
  if (emo == EMOTION_HAPPY) {
    u8g2.drawDisc(mx, my, mRad);
    u8g2.setDrawColor(0);
    u8g2.drawBox(mx - mRad - 1, my - mRad - 1, (mRad * 2) + 2, mRad + 2);
  } else if (emo == EMOTION_SAD) {
    u8g2.drawDisc(mx, my + (int)(3 * s), mRad);
    u8g2.setDrawColor(0);
    u8g2.drawBox(mx - mRad - 1, my + (int)(3 * s), (mRad * 2) + 2, mRad + 1);
  } else if (emo == EMOTION_LOOK_DOWN || emo == EMOTION_LOADING) {
    u8g2.drawCircle(mx, my + (int)(2 * s), max(1, (int)(3 * s)));
  } else {
    u8g2.drawDisc(mx, my, max(1, (int)(5 * s)));
    u8g2.setDrawColor(0);
    u8g2.drawBox(mx - (int)(6 * s), my - (int)(6 * s), (int)(12 * s), (int)(7 * s));
  }
  u8g2.setDrawColor(1);
}

// --- PANTALLAS ---
void drawBootScreen() {
  u8g2.setFont(u8g2_font_helvB08_tr);
  drawCenteredStr(11, "MEDIBOT v6.1");
  u8g2.drawHLine(0, 13, 128);

  u8g2.setFont(u8g2_font_5x7_tr);
  char buf[36];
  if (hwMaxWrong)      snprintf(buf, sizeof(buf), "Sensor : CHIP NO COMPAT.");
  else if (hwMaxOk)    snprintf(buf, sizeof(buf), "Sensor : OK (ID 0x%02X)", hwMaxPartId);
  else                 snprintf(buf, sizeof(buf), "Sensor : NO DETECTADO");
  u8g2.drawStr(4, 24, buf);

  snprintf(buf, sizeof(buf), "Teclado: %u botones", (unsigned)keypadActiveCount());
  u8g2.drawStr(4, 34, buf);

  // Si el arranque anterior se fue al garete, se dice AQUI: en un bucle de
  // reinicio el Monitor Serie pasa volando y esto se lee en la pantalla.
  u8g2.setFont(u8g2_font_4x6_tr);
  if (modoSeguro) {
    if (rtcMagia == RTC_MAGIA) {
      snprintf(buf, sizeof(buf), "Fallo en: %s", rtcPaso);
      drawCenteredStr(44, buf);
    }
    drawCenteredStr(51, "MODO SEGURO: arrancado sin red");
  } else if (!nvsOk) {
    drawCenteredStr(51, "MEMORIA KO (no guarda ajustes)");
  }

  uint32_t pct = (stateElapsed() * 100UL) / (uint32_t)BOOT_SCREEN_MS;
  if (pct > 100) pct = 100;
  drawProgressBar(4, 55, 120, 9, (uint8_t)pct);
}

void drawMenu() {
  u8g2.setFont(u8g2_font_helvB08_tr);
  drawCenteredStr(10, "MEDIBOT");
  u8g2.drawHLine(0, 12, 128);

  // Lista con scroll: caben MENU_VISIBLES entradas y la seleccion arrastra
  // la ventana, de modo que anadir opciones no rompe la pantalla.
  if (mainMenuSelection < mainMenuTop) mainMenuTop = mainMenuSelection;
  if (mainMenuSelection >= mainMenuTop + MENU_VISIBLES)
    mainMenuTop = mainMenuSelection - MENU_VISIBLES + 1;

  u8g2.setFont(u8g2_font_6x10_tr);
  for (int i = 0; i < MENU_VISIBLES && (mainMenuTop + i) < MENU_N; i++) {
    const int idx = mainMenuTop + i;
    const int y = 14 + i * 12;
    if (idx == mainMenuSelection) {
      u8g2.drawRBox(8, y, 112, 12, 2);
      u8g2.setDrawColor(0);
      drawCenteredStr(y + 9, MENU_ITEMS[idx]);
      u8g2.setDrawColor(1);
    } else {
      u8g2.drawRFrame(8, y, 112, 12, 2);
      drawCenteredStr(y + 9, MENU_ITEMS[idx]);
    }
  }
  if (mainMenuTop > 0)                      u8g2.drawTriangle(124, 18, 120, 22, 124, 22);
  if (mainMenuTop + MENU_VISIBLES < MENU_N) u8g2.drawTriangle(124, 61, 120, 57, 124, 57);
}

void drawAbout() {
  u8g2.setFont(u8g2_font_helvB08_tr);
  drawCenteredStr(10, "INFO MEDIBOT");
  u8g2.drawHLine(0, 13, 128);

  u8g2.setFont(u8g2_font_5x7_tr);
  if (aboutPage == 0) {
    u8g2.drawStr(2, 25, "MEDIBOT ayuda a");
    u8g2.drawStr(2, 35, "medir tus signos");
    u8g2.drawStr(2, 45, "vitales de forma");
    u8g2.drawStr(2, 55, "rapida y sencilla.");
    u8g2.drawTriangle(120, 52, 126, 52, 123, 56);
  } else {
    u8g2.drawStr(2, 25, "Este robot no");
    u8g2.drawStr(2, 35, "reemplaza a un");
    u8g2.drawStr(2, 45, "doctor real.");
    u8g2.drawStr(2, 55, "Uso referencial.");
    u8g2.drawTriangle(120, 26, 126, 26, 123, 22);
  }
  u8g2.setFont(u8g2_font_4x6_tr);
  u8g2.drawStr(70, 63, "[BACK] Salir");
}

void drawHistoryUI() {
  u8g2.setFont(u8g2_font_helvB08_tr);
  char buf[32];
  snprintf(buf, sizeof(buf), "HISTORIAL (%d/3)", historyPage + 1);
  drawCenteredStr(10, buf);
  u8g2.drawHLine(0, 12, 128);

  u8g2.setFont(u8g2_font_6x10_tr);
  if (historyCount == 0 || !historyReports[historyPage].recorded) {
    drawCenteredStr(38, "Sin registros aun");
  } else {
    const Report &r = historyReports[historyPage];
    snprintf(buf, sizeof(buf), "Latidos: %d x min", r.bpm);
    u8g2.drawStr(4, 32, buf);
    snprintf(buf, sizeof(buf), "Oxigeno: %d%%", r.spo2);
    u8g2.drawStr(4, 48, buf);
  }
  u8g2.setFont(u8g2_font_4x6_tr);
  u8g2.drawStr(20, 63, "[UP/DWN] Ver  [BACK] Salir");
}

// Pantalla de MEDIBOT: mientras busca enseña en que va; cuando lo encuentra,
// los datos en vivo de /api/esp32. Dos paginas con ARRIBA/ABAJO.
void drawMedibotScreen() {
  const NetInfo n = netGet();
  char buf[34];

  u8g2.setFont(u8g2_font_helvB08_tr);
  drawCenteredStr(9, "MEDIBOT");
  u8g2.drawHLine(0, 11, 128);
  u8g2.setFont(u8g2_font_5x7_tr);

  if (modoSeguro) {
    // Se arranco sin red porque el reinicio anterior fue un fallo. Aqui se
    // explica y se deja activarla a mano, por si se quiere probar.
    drawCenteredStr(24, "MODO SEGURO");
    drawCenteredStr(36, "Red desactivada tras");
    drawCenteredStr(45, "un reinicio por fallo");
    u8g2.setFont(u8g2_font_4x6_tr);
    drawCenteredStr(56, "Apaga y enciende para volver a la normalidad");
    drawCenteredStr(63, "[OK] Activar la red ahora  [ATRAS] Salir");
    return;
  }

  if (n.etapa != NET_FOUND) {
    // --- todavia no esta localizado: se cuenta por donde va ---
    drawCenteredStr(24, n.msg[0] ? n.msg : "Arrancando la red");
    switch (n.etapa) {
      case NET_WIFI:
        drawCenteredStr(36, "Red: " WIFI_SSID);
        drawSpinner(64, 48, 7, animFrame);
        break;
      case NET_MDNS:
        drawCenteredStr(36, "Preguntando por mDNS");
        drawSpinner(64, 48, 7, animFrame);
        break;
      case NET_SWEEP:
        snprintf(buf, sizeof(buf), "Mirando IP .%u de 254",
                 (unsigned)((n.progreso * 254UL) / 100UL));
        drawCenteredStr(36, buf);
        drawProgressBar(14, 42, 100, 9, n.progreso);
        break;
      case NET_FAIL:
        drawCenteredStr(36, "Se reintenta solo");
        drawCenteredStr(46, "Enciende MEDIBOT y espera");
        break;
      default:
        drawSpinner(64, 44, 7, animFrame);
        break;
    }
    u8g2.setFont(u8g2_font_4x6_tr);
    drawCenteredStr(63, "[OK] Reintentar  [ATRAS] Salir");
    return;
  }

  const IPAddress ip(n.ip);
  if (resultPage == 0) {
    snprintf(buf, sizeof(buf), "%s:%u", ip.toString().c_str(), (unsigned)n.puerto);
    drawCenteredStr(21, buf);
    if (!n.jsonOk) {
      drawCenteredStr(36, "Conectado, sin datos");
      drawCenteredStr(46, "(la API no responde)");
    } else {
      snprintf(buf, sizeof(buf), "Sistema: %s   Caras: %d",
               n.sistema ? "ON" : "off", n.detecciones);
      u8g2.drawStr(2, 32, buf);
      snprintf(buf, sizeof(buf), "FPS: %d / %d   Rojos: %d", n.fps1, n.fps2, n.rojos);
      u8g2.drawStr(2, 42, buf);
      snprintf(buf, sizeof(buf), "Grabando: %s   WiFi %d dBm",
               n.grabando ? "SI" : "no", (int)n.rssi);
      u8g2.drawStr(2, 52, buf);
    }
    u8g2.setFont(u8g2_font_4x6_tr);
    drawCenteredStr(63, "[ARR/ABA] Mas  [ATRAS] Salir");
  } else {
    snprintf(buf, sizeof(buf), "Red: %s", WIFI_SSID);
    u8g2.drawStr(2, 22, buf);
    const IPAddress propia(n.ipPropia);
    snprintf(buf, sizeof(buf), "Este ESP32: %s", propia.toString().c_str());
    u8g2.drawStr(2, 32, buf);
    // Esta linea es la que hay que leer para abrir la configuracion del equipo
    // en el movil o el ordenador: es la direccion que se escribe en el navegador.
    if (webArrancado) snprintf(buf, sizeof(buf), "Web: http://%s/", propia.toString().c_str());
    else              snprintf(buf, sizeof(buf), "Cara en x=%d y=%d", n.caraX, n.caraY);
    u8g2.drawStr(2, 42, buf);
    const uint32_t desde = n.jsonMs ? (millis() - n.jsonMs) / 1000UL : 0;
    if (n.jsonOk) snprintf(buf, sizeof(buf), "Ultimo dato hace %lus", (unsigned long)desde);
    else          snprintf(buf, sizeof(buf), "Sin datos de la API");
    u8g2.drawStr(2, 52, buf);
    u8g2.setFont(u8g2_font_4x6_tr);
    drawCenteredStr(63, "[ARR/ABA] Mas  [ATRAS] Salir");
  }
}

// Pantalla de diagnostico: todo lo que hace falta para saber si el equipo
// esta bien conectado, sin abrir el Monitor Serie. El IR en vivo es la clave:
// sin dedo baja de 10.000 y con el dedo sube de 30.000; si se queda clavado
// en 0 el sensor no esta leyendo.
void drawDiagScreen() {
  const Vitals v = vitalsGet();
  char buf[34];

  u8g2.setFont(u8g2_font_helvB08_tr);
  drawCenteredStr(9, "DIAGNOSTICO");
  u8g2.drawHLine(0, 11, 128);

  u8g2.setFont(u8g2_font_5x7_tr);
  if (hwMaxWrong)   snprintf(buf, sizeof(buf), "Sensor: MAX30100 no vale");
  else if (hwMaxOk) snprintf(buf, sizeof(buf), "Sensor: OK 0x%02X a %lukHz", hwMaxPartId,
                             (unsigned long)(hwMaxBusHz / 1000));
  else              snprintf(buf, sizeof(buf), "Sensor: NO DETECTADO");
  u8g2.drawStr(2, 21, buf);

  if (hwMaxOk)                          snprintf(buf, sizeof(buf), "I2C: %u disp. (1o 0x%02X)",
                                                 (unsigned)hwI2cCount, hwI2cFirst);
  else if (hwI2cDiag == I2C_CORTO)      snprintf(buf, sizeof(buf), "I2C: linea a 0V (corto)");
  else if (hwI2cDiag == I2C_SIN_PULLUP) snprintf(buf, sizeof(buf), "I2C: cable suelto/sin 3V3");
  else                                  snprintf(buf, sizeof(buf), "I2C: hay 3V3, SDA/SCL?");
  u8g2.drawStr(2, 30, buf);

  if (!hwMaxOk)               snprintf(buf, sizeof(buf), "IR: --");
  else if (v.lastSampleMs == 0) snprintf(buf, sizeof(buf), "IR: esperando muestras");
  else snprintf(buf, sizeof(buf), "IR: %lu  %s", (unsigned long)v.rawIR,
                v.fingerPresent ? "DEDO" : "sin dedo");
  u8g2.drawStr(2, 39, buf);

  if (keypad.desconectado)
    snprintf(buf, sizeof(buf), "Tecla: SIN CONECTAR (GPIO%d)", (int)KEYPAD_PIN);
  else
    snprintf(buf, sizeof(buf), "Tecla: %d mV (reposo %d)", (int)keypadLastMv(), (int)keypad.idleMv);
  u8g2.drawStr(2, 48, buf);

  const NetInfo n = netGet();
  const char *red = (n.etapa == NET_FOUND) ? "MEDIBOT"
                  : (n.etapa == NET_FAIL)  ? "no"
                  : (n.etapa == NET_WIFI || n.etapa == NET_OFF) ? "..." : "buscando";
  snprintf(buf, sizeof(buf), "Mem:%s Red:%s Pila:%u",
           nvsOk ? "OK" : "KO", red, (unsigned)v.pilaLibre);
  u8g2.drawStr(2, 57, buf);

  u8g2.setFont(u8g2_font_4x6_tr);
  drawCenteredStr(64, "[BACK] Salir");
}

void drawTriageResult() {
  u8g2.setFont(u8g2_font_helvB08_tr);
  char buf[36];
  if (resultPage == 0) {
    drawCenteredStr(10, "TUS RESULTADOS");
    u8g2.drawHLine(0, 12, 128);

    // La cara va aqui: es la unica pantalla de resultado que queda, asi que
    // es donde el robot reacciona (contento o preocupado) a lo medido.
    drawAvatar(currentEmotion, animFrame, 26, 36, 0.45f);

    u8g2.setFont(u8g2_font_6x10_tr);
    u8g2.drawStr(56, 26, "Pulso");
    snprintf(buf, sizeof(buf), "%d bpm", patientBPM);
    u8g2.drawStr(56, 37, buf);
    snprintf(buf, sizeof(buf), "SpO2 %d%%", patientSpO2);
    u8g2.drawStr(56, 52, buf);

    u8g2.setFont(u8g2_font_4x6_tr);
    drawCenteredStr(63, "[UP/DWN] Posibles causas");
  } else {
    drawCenteredStr(10, "POSIBLES CAUSAS");
    u8g2.drawHLine(0, 12, 128);
    u8g2.setFont(u8g2_font_5x7_tr);
    u8g2.drawStr(2, 27, diagnosis1);
    u8g2.drawStr(2, 41, diagnosis2);
    u8g2.setFont(u8g2_font_4x6_tr);
    drawCenteredStr(55, "Orientativo, no es diagnostico");
    drawCenteredStr(63, "[OK] Inicio  [UP/DWN] Valores");
  }
}

void drawSignalError() {
  drawAvatar(EMOTION_SAD, animFrame, 64, 20, 0.6f);
  u8g2.setFont(u8g2_font_6x10_tr);
  drawCenteredStr(50, "Senal no fiable");
  u8g2.setFont(u8g2_font_5x7_tr);
  drawCenteredStr(60, errorDetail[0] ? errorDetail : "Intentalo de nuevo");
}

// El asistente se ve entero en pantalla: en cada paso dice que hacer y, abajo,
// la lectura en vivo (ADC / mV / reposo), asi que si algo va mal se ve al momento.
void drawWizardScreen() {
  u8g2.setFont(u8g2_font_helvB08_tr);
  drawCenteredStr(10, "CALIBRAR TECLADO");
  u8g2.drawHLine(0, 12, 128);

  char buf[36];
  u8g2.setFont(u8g2_font_6x10_tr);
  switch (wiz.fase) {
    case 0:
      if (wiz.esperandoSoltar) {
        drawCenteredStr(30, "Suelta los botones");
        drawCenteredStr(42, "y espera...");
      } else {
        drawCenteredStr(28, "No toques nada");
        // Dos tramos: primero esperar a que la lectura se quede quieta, y
        // despues acumular las WIZ_MUESTRAS lecturas del reposo.
        if (wiz.nMuestras == 0) {
          drawCenteredStr(40, "midiendo reposo...");
          const uint32_t quieto = millis() - wiz.estableDesde;
          drawProgressBar(14, 45, 100, 9,
                          (uint8_t)(quieto >= WIZ_REPOSO_MS ? 100
                                    : (quieto * 100UL / WIZ_REPOSO_MS)));
        } else {
          snprintf(buf, sizeof(buf), "promediando %u/%u",
                   (unsigned)wiz.nMuestras, (unsigned)WIZ_MUESTRAS);
          drawCenteredStr(40, buf);
          drawProgressBar(14, 45, 100, 9,
                          (uint8_t)((uint32_t)wiz.nMuestras * 100UL / WIZ_MUESTRAS));
        }
      }
      break;
    case 1: {
      drawCenteredStr(26, wiz.nMuestras ? "SIGUE PULSANDO:" : "Pulsa y manten:");
      u8g2.setFont(u8g2_font_helvB08_tr);
      drawCenteredStr(40, buttonName(BTN_ORDEN[wiz.paso]));
      u8g2.setFont(u8g2_font_4x6_tr);
      if (wiz.nMuestras) {
        // Ya esta midiendo: lo que importa es que no suelte hasta el final.
        snprintf(buf, sizeof(buf), "midiendo %u/%u",
                 (unsigned)wiz.nMuestras, (unsigned)WIZ_MUESTRAS);
        drawCenteredStr(48, buf);
        drawProgressBar(24, 50, 80, 6,
                        (uint8_t)((uint32_t)wiz.nMuestras * 100UL / WIZ_MUESTRAS));
      } else {
        const uint32_t transcurrido = millis() - wiz.t0;
        const unsigned resta = (transcurrido < WIZ_SALTO_MS)
                               ? (unsigned)((WIZ_SALTO_MS - transcurrido) / 1000) : 0u;
        snprintf(buf, sizeof(buf), "%u/%u  se omite en %us",
                 (unsigned)(wiz.paso + 1), (unsigned)BTN_ORDEN_N, resta);
        drawCenteredStr(50, buf);
      }
      break;
    }
    case 2: drawCenteredStr(34, "Suelta el boton"); break;
    case 3: drawCenteredStr(34, "Calculando...");   break;
    default:
      snprintf(buf, sizeof(buf), "%u de %u botones OK",
               (unsigned)wiz.capturados, (unsigned)BTN_ORDEN_N);
      drawCenteredStr(28, buf);
      if (wiz.guardado)        drawCenteredStr(40, "Guardado en memoria");
      else if (wiz.aviso[0])   drawCenteredStr(40, wiz.aviso);
      else                     drawCenteredStr(40, "Sin guardar");
      if (wiz.guardado) {
        // El rango mas estrecho de los cuatro: es el que decide si alguna
        // pulsacion se puede escapar.
        int16_t peor = 0;
        for (uint8_t k = 0; k < KEYPAD_MAP_SIZE; k++) {
          const Button b = KEYPAD_MAP[k].id;
          if (!wiz.hecho[b] || wiz.ancho[b] <= 0) continue;
          if (peor == 0 || wiz.ancho[b] < peor) peor = wiz.ancho[b];
        }
        u8g2.setFont(u8g2_font_4x6_tr);
        snprintf(buf, sizeof(buf), "margen minimo +-%d mV", (int)peor);
        drawCenteredStr(50, buf);
        u8g2.setFont(u8g2_font_6x10_tr);
      }
      if (!wiz.guardado) {
        u8g2.setFont(u8g2_font_4x6_tr);
        drawCenteredStr(50, "Se repetira al encender");
        u8g2.setFont(u8g2_font_6x10_tr);
      }
      break;
  }
  u8g2.setFont(u8g2_font_4x6_tr);
  snprintf(buf, sizeof(buf), "ADC %u  %d mV  reposo %d",
           (unsigned)keypadCounts(), (int)keypadLastMv(), (int)keypad.idleMv);
  drawCenteredStr(62, buf);
}

void renderUI() {
  const uint32_t now = millis();
  u8g2.clearBuffer();

  switch (currentState) {
    case STATE_BOOT:
      drawBootScreen();
      break;

    case STATE_IDLE_FACE:
      drawAvatar(currentEmotion, animFrame, 64, 32, 1.0f);
      break;

    case STATE_MENU:    drawMenu();        break;
    case STATE_ABOUT:   drawAbout();       break;
    case STATE_HISTORY: drawHistoryUI();   break;
    case STATE_DIAG:    drawDiagScreen();  break;
    case STATE_MEDIBOT: drawMedibotScreen(); break;
    case STATE_KEYPAD_WIZARD: drawWizardScreen(); break;
    case STATE_SIGNAL_ERROR: drawSignalError(); break;

    case STATE_TRIAGE_FINGER_REQ:
      drawAvatar(EMOTION_LOOK_DOWN, animFrame, 64, 20, 0.65f);
      u8g2.setFont(u8g2_font_6x10_tr);
      drawCenteredStr(50, "Coloque su dedo");
      drawCenteredStr(62, "sobre el sensor");
      break;

    case STATE_TRIAGE_FINGER_READ: {
      const Vitals v = vitalsGet();
      if (!v.fingerPresent || !vitalsVigentes(v)) {
        drawAvatar(EMOTION_LOOK_DOWN, animFrame, 48, 20, 0.55f);
        drawFingerIcon(108, 24, animFrame);
        u8g2.setFont(u8g2_font_6x10_tr);
        drawCenteredStr(50, "Esperando dedo...");
        // El IR en vivo: si sube al apoyar el dedo, el sensor va bien; si se
        // queda clavado, el problema es el sensor y no el dedo.
        u8g2.setFont(u8g2_font_4x6_tr);
        char b[24];
        if (v.lastSampleMs) snprintf(b, sizeof(b), "IR %lu", (unsigned long)v.rawIR);
        else                snprintf(b, sizeof(b), "sin datos del sensor");
        drawCenteredStr(58, b);
        drawCenteredStr(64, "[BACK] Cancelar");
      } else {
        drawAvatar(EMOTION_LOADING, animFrame, 40, 18, 0.5f);
        // Corazon sincronizado con el ultimo latido detectado
        const uint32_t since = now - v.lastBeatMs;
        drawHeart(104, 18, (v.lastBeatMs && since < 180) ? 9 : 6);

        u8g2.setFont(u8g2_font_6x10_tr);
        char buf[20];
        if (v.liveBPM > 0) snprintf(buf, sizeof(buf), "BPM %d", v.liveBPM);
        else               snprintf(buf, sizeof(buf), "BPM --");
        u8g2.drawStr(4, 44, buf);
        if (v.liveSpO2Valid) snprintf(buf, sizeof(buf), "SpO2 %d%%", v.liveSpO2);
        else                 snprintf(buf, sizeof(buf), "SpO2 --");
        u8g2.drawStr(62, 44, buf);

        drawProgressBar(4, 48, 120, 9, v.ppgProgress);
        u8g2.setFont(u8g2_font_4x6_tr);
        if (v.ppgProgress == 0) drawCenteredStr(63, "Estabilizando senal...");
        else                    drawCenteredStr(63, "Midiendo, no se mueva");
      }
      break;
    }

    case STATE_TRIAGE_RESULT:
      drawTriageResult();
      break;
  }

  u8g2.sendBuffer();
  needsRedraw = false;
}

// =====================================================================
// 6. DIAGNOSTICOS E HISTORIAL
// =====================================================================
void evaluateDiagnoses() {
  if (patientBPM > 100)      snprintf(diagnosis1, sizeof(diagnosis1), "1. Pulso acelerado (%d)", patientBPM);
  else if (patientBPM < 60)  snprintf(diagnosis1, sizeof(diagnosis1), "1. Pulso lento (%d)", patientBPM);
  else                       snprintf(diagnosis1, sizeof(diagnosis1), "1. Pulso normal (%d)", patientBPM);

  if (patientSpO2 < 92)       snprintf(diagnosis2, sizeof(diagnosis2), "2. Oxigeno bajo: alerta (%d%%)", patientSpO2);
  else if (patientSpO2 <= 94) snprintf(diagnosis2, sizeof(diagnosis2), "2. Oxigeno algo bajo (%d%%)", patientSpO2);
  else                        snprintf(diagnosis2, sizeof(diagnosis2), "2. Oxigeno normal (%d%%)", patientSpO2);
}

void saveReport() {
  historyReports[2] = historyReports[1];
  historyReports[1] = historyReports[0];
  historyReports[0].bpm      = patientBPM;
  historyReports[0].spo2     = patientSpO2;
  historyReports[0].recorded = true;
  if (historyCount < 3) historyCount++;
}

static void abortMeasurement(const char *motivo) {
  sensorReposo();
  snprintf(errorDetail, sizeof(errorDetail), "%s", motivo);
  currentEmotion = EMOTION_SAD;
  setState(STATE_SIGNAL_ERROR);
}

// =====================================================================
// 7. ENTRADA DE USUARIO
// =====================================================================
void processInputs(Button btn) {
  if (btn == BTN_NONE) return;
  lastInteraction = millis();
  needsRedraw = true;

  switch (currentState) {
    case STATE_BOOT:
      break;

    case STATE_IDLE_FACE:
      setState(STATE_MENU);
      break;

    case STATE_MENU:
      if (btn == BTN_UP)   mainMenuSelection = (mainMenuSelection == 0) ? MENU_N - 1 : mainMenuSelection - 1;
      if (btn == BTN_DOWN) mainMenuSelection = (mainMenuSelection == MENU_N - 1) ? 0 : mainMenuSelection + 1;
      if (btn == BTN_BACK) { currentEmotion = EMOTION_NORMAL; setState(STATE_IDLE_FACE); }
      if (btn == BTN_OK) {
        switch (mainMenuSelection) {
          case 0:
            if (!hwMaxOk) { snprintf(errorDetail, sizeof(errorDetail), "Sensor de pulso ausente");
                            currentEmotion = EMOTION_SAD; setState(STATE_SIGNAL_ERROR); }
            else { patientBPM = 0; patientSpO2 = 0;
                   currentEmotion = EMOTION_LOOK_DOWN; setState(STATE_TRIAGE_FINGER_REQ); }
            break;
          case 1: resultPage = 0; setState(STATE_MEDIBOT); break;
          case 2: historyPage = 0; setState(STATE_HISTORY); break;
          case 3:
            // Diagnostico enciende el sensor para poder enseñar el IR en vivo
            sensorRequest(SENS_PPG);
            setState(STATE_DIAG);
            break;
          case 4: keypadWizardStart(); setState(STATE_KEYPAD_WIZARD); break;
          default: aboutPage = 0; setState(STATE_ABOUT); break;
        }
      }
      break;

    case STATE_HISTORY:
      if (btn == BTN_DOWN) historyPage = (historyPage + 1) % 3;
      if (btn == BTN_UP)   historyPage = (historyPage + 2) % 3;
      if (btn == BTN_BACK || btn == BTN_OK) setState(STATE_MENU);
      break;

    case STATE_DIAG:
      if (btn == BTN_BACK || btn == BTN_OK) setState(STATE_MENU);
      break;

    case STATE_MEDIBOT:
      if (btn == BTN_UP || btn == BTN_DOWN) resultPage = (resultPage == 0) ? 1 : 0;
      if (btn == BTN_OK && modoSeguro) {        // activar la red a mano
        modoSeguro = false;
        Serial.println(F("[ARRANQUE] Modo seguro desactivado a mano: se arranca la red"));
        sensorReposo();
      } else if (btn == BTN_OK && netGet().etapa != NET_FOUND) {
        netForzarBusqueda();                  // reintento inmediato
      } else if (btn == BTN_OK || btn == BTN_BACK) {
        setState(STATE_MENU);
      }
      break;

    case STATE_ABOUT:
      if (btn == BTN_DOWN) aboutPage = 1;
      if (btn == BTN_UP)   aboutPage = 0;
      if (btn == BTN_BACK || btn == BTN_OK) setState(STATE_MENU);
      break;

    case STATE_TRIAGE_RESULT:
      if (btn == BTN_UP || btn == BTN_DOWN) resultPage = (resultPage == 0) ? 1 : 0;
      if (btn == BTN_BACK || btn == BTN_OK) { currentEmotion = EMOTION_NORMAL; setState(STATE_MENU); }
      break;

    case STATE_SIGNAL_ERROR:
      currentEmotion = EMOTION_NORMAL;
      setState(STATE_MENU);
      break;

    // Durante la medida solo se permite cancelar
    case STATE_TRIAGE_FINGER_REQ:
    case STATE_TRIAGE_FINGER_READ:
      if (btn == BTN_BACK) {
        sensorReposo();
        currentEmotion = EMOTION_NORMAL;
        setState(STATE_MENU);
      }
      break;

    default:
      break;
  }
}

// =====================================================================
// 8. SERVIDOR WEB (NUCLEO 1): LA CONFIGURACION EN EL NAVEGADOR
// =====================================================================
//  Escribiendo la IP del ESP32 en un navegador sale todo lo que el equipo
//  sabe de si mismo: red, sensor, teclado, memoria, ultima medida y con que
//  configuracion se compilo. Es la pantalla de Diagnostico entera, sin tener
//  que estar delante del aparato ni enchufar el cable USB.
//
//  Vive en el NUCLEO 1 (el del interfaz), no en el 0. El nucleo 0 alterna
//  entre leer el sensor y hablar por la red: mientras mide un pulso no
//  atenderia al navegador y la pagina se quedaria colgada medio minuto. El
//  nucleo 1 esta siempre libre, asi que responde hasta a mitad de una medida.
//
//  Ninguna funcion de aqui toca el bus I2C ni el sensor: solo lee COPIAS del
//  estado hechas con vitalsGet()/netGet(), que ya hacen el bloqueo bien.

WebServer webServer(WEB_PUERTO);

// Envia un trozo de pagina sin construir un String intermedio: en un ESP32 la
// memoria es poca y la pagina entera de golpe no cabria comoda.
static void webTexto(const char *s) { webServer.sendContent(s, strlen(s)); }

static void webFila(const char *clave, const char *valor, const char *id) {
  char buf[320];
  if (id && id[0])
    snprintf(buf, sizeof(buf), "<tr><th>%s</th><td id=\"%s\">%s</td></tr>", clave, id, valor);
  else
    snprintf(buf, sizeof(buf), "<tr><th>%s</th><td>%s</td></tr>", clave, valor);
  webTexto(buf);
}

static const char *webTextoI2c() {
  if (hwMaxOk)                          return "con dispositivos";
  if (hwI2cDiag == I2C_CORTO)           return "una linea clavada a 0 V (cortocircuito)";
  if (hwI2cDiag == I2C_SIN_PULLUP)      return "lineas al aire: modulo sin conectar o sin 3V3";
  return "hay 3V3 pero nadie contesta: revisa si SDA y SCL estan cambiadas";
}

static const char *webTextoEtapa(uint8_t etapa) {
  switch (etapa) {
    case NET_OFF:   return "apagada";
    case NET_WIFI:  return "conectando al WiFi";
    case NET_MDNS:  return "buscando MEDIBOT (mDNS)";
    case NET_SWEEP: return "explorando la red IP a IP";
    case NET_FOUND: return "MEDIBOT localizado";
    case NET_FAIL:  return "sin conexion (se reintenta solo)";
    default:        return "-";
  }
}

// "01:23:45" desde el encendido
static void webTiempo(char *dst, size_t n, uint32_t ms) {
  const uint32_t s = ms / 1000;
  snprintf(dst, n, "%02u:%02u:%02u", (unsigned)(s / 3600),
           (unsigned)((s / 60) % 60), (unsigned)(s % 60));
}

// ---------------------------------------------------------------------
// 8.1 /api : los valores que cambian, en JSON
// ---------------------------------------------------------------------
//  La pagina se refresca sola pidiendo esto cada 2 s, asi no hay que recargar
//  entera ni parpadea. Tambien sirve para leer el triaje desde otro programa.
static void webApi() {
  const Vitals  v = vitalsGet();
  const NetInfo n = netGet();
  const IPAddress ipMedibot(n.ip);
  char tiempo[16];
  webTiempo(tiempo, sizeof(tiempo), millis());

  char buf[768];
  snprintf(buf, sizeof(buf),
    "{\"sensor\":{\"ok\":%s,\"id\":%u,\"rev\":%u,\"khz\":%lu,\"ir\":%lu,\"rojo\":%lu,"
    "\"dedo\":%s,\"reinicios\":%u,\"intentos\":%lu},"
    "\"pulso\":{\"bpm\":%d,\"spo2\":%d,\"fiable\":%s,\"progreso\":%u},"
    "\"teclado\":{\"conectado\":%s,\"mv\":%d,\"reposo\":%d,\"dispersion\":%d,\"botones\":%u},"
    "\"red\":{\"etapa\":%u,\"rssi\":%d,\"ip\":\"%s\",\"medibot\":\"%s\",\"puerto\":%u,"
    "\"api\":%s,\"sistema\":%d,\"caras\":%d,\"rojos\":%d,\"fps1\":%d,\"fps2\":%d,"
    "\"grabando\":%s},"
    "\"equipo\":{\"heap\":%u,\"pila\":%lu,\"encendido\":\"%s\",\"modoseguro\":%s}}",
    hwMaxOk ? "true" : "false", (unsigned)hwMaxPartId, (unsigned)hwMaxRevId,
    (unsigned long)(hwMaxBusHz / 1000), (unsigned long)v.rawIR, (unsigned long)v.rawRed,
    v.fingerPresent ? "true" : "false", (unsigned)v.recoveries,
    (unsigned long)hwMaxIntentos,
    v.liveBPM, v.liveSpO2Valid ? v.liveSpO2 : 0,
    v.signalReliable ? "true" : "false", (unsigned)v.ppgProgress,
    keypad.desconectado ? "false" : "true", (int)keypadLastMv(), (int)keypad.idleMv,
    (int)keypad.spread, (unsigned)keypadActiveCount(),
    (unsigned)n.etapa, (int)n.rssi, IPAddress(n.ipPropia).toString().c_str(),
    n.ip ? ipMedibot.toString().c_str() : "-", (unsigned)n.puerto,
    n.jsonOk ? "true" : "false", n.sistema, n.detecciones, n.rojos, n.fps1, n.fps2,
    n.grabando ? "true" : "false",
    (unsigned)ESP.getFreeHeap(), (unsigned long)v.pilaLibre, tiempo,
    modoSeguro ? "true" : "false");
  webServer.send(200, "application/json", buf);
}

// ---------------------------------------------------------------------
// 8.2 / : la pagina
// ---------------------------------------------------------------------
static void webPagina() {
  const Vitals  v = vitalsGet();
  const NetInfo n = netGet();
  char b[320], t[16];

  webServer.setContentLength(CONTENT_LENGTH_UNKNOWN);
  webServer.send(200, "text/html; charset=utf-8", "");

  webTexto(
    "<!DOCTYPE html><html lang=\"es\"><head><meta charset=\"utf-8\">"
    "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
    "<title>MEDIBOT - Triaje</title><style>"
    ":root{--bg:#0f1115;--card:#171a21;--bd:#262b36;--tx:#e6e9ef;--dim:#98a2b3;"
    "--ok:#3ddc84;--mal:#ff6b6b;--av:#ffd166}"
    "*{box-sizing:border-box}"
    "body{margin:0;padding:16px;background:var(--bg);color:var(--tx);"
    "font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}"
    "h1{font-size:20px;margin:0 0 4px}h2{font-size:15px;margin:0 0 10px;color:var(--dim);"
    "text-transform:uppercase;letter-spacing:.06em}"
    ".sub{color:var(--dim);margin:0 0 18px;font-size:13px}"
    ".rej{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px;"
    "max-width:1100px}"
    ".c{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:14px 16px}"
    "table{width:100%;border-collapse:collapse}"
    "th{text-align:left;font-weight:400;color:var(--dim);padding:5px 0;width:45%;"
    "vertical-align:top}"
    "td{padding:5px 0;text-align:right;font-variant-numeric:tabular-nums;"
    "word-break:break-word}"
    ".ok{color:var(--ok)}.mal{color:var(--mal)}.av{color:var(--av)}"
    "form{display:inline}"
    "button{background:#222836;color:var(--tx);border:1px solid var(--bd);border-radius:8px;"
    "padding:9px 14px;margin:4px 6px 0 0;font-size:14px;cursor:pointer}"
    "button:hover{border-color:#3a4354}"
    "code{background:#0b0d11;padding:1px 5px;border-radius:4px;font-size:13px}"
    "</style></head><body>");

  webTexto("<h1>MEDIBOT &middot; Triaje</h1>");
  snprintf(b, sizeof(b), "<p class=\"sub\">v6.1 &middot; %s &middot; se actualiza solo cada 2 s</p>",
           modoSeguro ? "<span class=\"av\">MODO SEGURO (arrancado sin red tras un fallo)</span>"
                      : "funcionamiento normal");
  webTexto(b);
  webTexto("<div class=\"rej\">");

  // ---- Sensor de pulso ----
  webTexto("<div class=\"c\"><h2>Sensor de pulso</h2><table>");
  if (hwMaxWrong)   snprintf(b, sizeof(b), "<span class=\"mal\">MAX30100: no vale</span>");
  else if (hwMaxOk) snprintf(b, sizeof(b), "<span class=\"ok\">responde</span>");
  else              snprintf(b, sizeof(b), "<span class=\"mal\">NO detectado</span>");
  webFila("Estado", b, "s_ok");
  snprintf(b, sizeof(b), "0x%02X (ID 0x%02X rev 0x%02X)", MAX_I2C_ADDR, hwMaxPartId, hwMaxRevId);
  webFila("Chip", b, "s_chip");
  snprintf(b, sizeof(b), "%lu kHz", (unsigned long)(hwMaxBusHz / 1000));
  webFila("Velocidad del bus", b, "s_khz");
  snprintf(b, sizeof(b), "%s (%u en el bus)", webTextoI2c(), (unsigned)hwI2cCount);
  webFila("Bus I2C", b, "");
  snprintf(b, sizeof(b), "SDA GPIO%d &middot; SCL GPIO%d", I2C_SDA_PIN, I2C_SCL_PIN);
  webFila("Patillas", b, "");
  snprintf(b, sizeof(b), "%lu %s", (unsigned long)v.rawIR, v.fingerPresent ? "(DEDO)" : "(sin dedo)");
  webFila("Infrarrojo en vivo", b, "s_ir");
  snprintf(b, sizeof(b), "%u", (unsigned)v.recoveries);
  webFila("Reinicios del sensor", b, "s_rec");
  webTexto("</table></div>");

  // ---- Pulso ----
  webTexto("<div class=\"c\"><h2>Medida</h2><table>");
  snprintf(b, sizeof(b), "%d BPM", v.liveBPM);
  webFila("Pulso en vivo", b, "p_bpm");
  if (v.liveSpO2Valid) snprintf(b, sizeof(b), "%d %%", v.liveSpO2);
  else                 snprintf(b, sizeof(b), "--");
  webFila("SpO2 en vivo", b, "p_spo2");
  snprintf(b, sizeof(b), "%u %%", (unsigned)v.ppgProgress);
  webFila("Progreso", b, "p_prog");
  snprintf(b, sizeof(b), "%s", v.signalReliable ? "<span class=\"ok\">si</span>"
                                                : "<span class=\"dim\">aun no</span>");
  webFila("Senal fiable", b, "p_fiable");
  for (int i = 0; i < historyCount && i < 3; i++) {
    char k[24];
    snprintf(k, sizeof(k), "Historial %d", i + 1);
    snprintf(b, sizeof(b), "%d BPM &middot; %d %%", historyReports[i].bpm, historyReports[i].spo2);
    webFila(k, b, "");
  }
  if (historyCount == 0) webFila("Historial", "sin registros aun", "");
  webTexto("</table></div>");

  // ---- Red ----
  webTexto("<div class=\"c\"><h2>Red</h2><table>");
  webFila("Red WiFi", WIFI_SSID, "");
  webFila("IP de este ESP32", WiFi.localIP().toString().c_str(), "");
  webFila("Puerta de enlace", WiFi.gatewayIP().toString().c_str(), "");
  webFila("MAC", WiFi.macAddress().c_str(), "");
  webFila("Nombre mDNS", "medibot-triaje.local", "");
  snprintf(b, sizeof(b), "%d dBm", (int)n.rssi);
  webFila("Senal WiFi", b, "r_rssi");
  webFila("Busqueda", webTextoEtapa(n.etapa), "r_etapa");
  if (n.ip) snprintf(b, sizeof(b), "%s:%u", IPAddress(n.ip).toString().c_str(), (unsigned)n.puerto);
  else      snprintf(b, sizeof(b), "sin localizar");
  webFila("MEDIBOT", b, "r_ip");
  snprintf(b, sizeof(b), "%s", n.jsonOk ? "<span class=\"ok\">responde</span>"
                                        : "<span class=\"mal\">sin datos</span>");
  webFila("API " MEDIBOT_API, b, "r_api");
  snprintf(b, sizeof(b), "sistema %d &middot; caras %d &middot; rojos %d",
           n.sistema, n.detecciones, n.rojos);
  webFila("Datos de MEDIBOT", b, "r_datos");
  snprintf(b, sizeof(b), "%d / %d fps %s", n.fps1, n.fps2, n.grabando ? "&middot; GRABANDO" : "");
  webFila("Camaras", b, "r_fps");
  webTexto("</table></div>");

  // ---- Teclado ----
  webTexto("<div class=\"c\"><h2>Teclado</h2><table>");
  snprintf(b, sizeof(b), "%s", keypad.desconectado
           ? "<span class=\"mal\">SIN CONECTAR</span>" : "<span class=\"ok\">conectado</span>");
  webFila("Estado", b, "t_ok");
  snprintf(b, sizeof(b), "GPIO%d (solo entrada)", KEYPAD_PIN);
  webFila("Patilla", b, "");
  snprintf(b, sizeof(b), "%d mV", (int)keypadLastMv());
  webFila("Lectura ahora", b, "t_mv");
  snprintf(b, sizeof(b), "%d mV", (int)keypad.idleMv);
  webFila("Nivel de reposo", b, "t_rep");
  snprintf(b, sizeof(b), "%d mV", (int)keypad.spread);
  webFila("Dispersion", b, "t_disp");
  for (uint8_t k = 0; k < KEYPAD_MAP_SIZE; k++) {
    if (KEYPAD_MAP[k].mvMin > KEYPAD_MAP[k].mvMax) continue;
    snprintf(b, sizeof(b), "%d .. %d mV", (int)KEYPAD_MAP[k].mvMin, (int)KEYPAD_MAP[k].mvMax);
    webFila(buttonName(KEYPAD_MAP[k].id), b, "");
  }
  snprintf(b, sizeof(b), "%s", nvsOk ? "<span class=\"ok\">si</span>"
                                     : "<span class=\"mal\">NO (memoria)</span>");
  webFila("Se guarda al calibrar", b, "");
  webTexto("</table></div>");

  // ---- Equipo ----
  webTexto("<div class=\"c\"><h2>Equipo</h2><table>");
  webTiempo(t, sizeof(t), millis());
  webFila("Encendido desde", t, "e_tiempo");
  snprintf(b, sizeof(b), "%u bytes", (unsigned)ESP.getFreeHeap());
  webFila("Memoria libre", b, "e_heap");
  snprintf(b, sizeof(b), "%lu bytes", (unsigned long)v.pilaLibre);
  webFila("Pila libre (nucleo 0)", b, "e_pila");
  webFila("Ultimo reinicio", motivoReinicio(), "");
  if (rtcMagia == RTC_MAGIA) webFila("Se quedo en el paso", rtcPaso, "");
  snprintf(b, sizeof(b), "%s", modoSeguro ? "<span class=\"av\">SI (sin red)</span>" : "no");
  webFila("Modo seguro", b, "");
  webTexto("</table></div>");

  // ---- Configuracion de compilacion ----
  webTexto("<div class=\"c\"><h2>Configuracion</h2><table>");
  snprintf(b, sizeof(b), "%d Hz nominales / %d efectivos", MAX_SAMPLE_RATE, PPG_EFFECTIVE_SPS);
  webFila("Muestreo del sensor", b, "");
  snprintf(b, sizeof(b), "0x%02X", MAX_LED_BRIGHTNESS);
  webFila("Brillo de los LED", b, "");
  snprintf(b, sizeof(b), "%d us &middot; rango %d", MAX_PULSE_WIDTH, MAX_ADC_RANGE);
  webFila("Pulso / ADC", b, "");
  snprintf(b, sizeof(b), "%d lecturas validas (corte a %lu s)",
           PPG_TARGET_READINGS, (unsigned long)(PPG_TIMEOUT_MS / 1000));
  webFila("Duracion de la medida", b, "");
  snprintf(b, sizeof(b), "puertos %d y %d", MEDIBOT_PORT_MAIN, MEDIBOT_PORT_ALT);
  webFila("Busqueda de MEDIBOT", b, "");
  snprintf(b, sizeof(b), "cada %lu ms", (unsigned long)JSON_POLL_MS);
  webFila("Refresco de la API", b, "");
  webTexto("</table></div>");

  webTexto("</div>");   // fin de la rejilla

  // ---- Acciones ----
  webTexto(
    "<div class=\"c\" style=\"margin-top:14px;max-width:1100px\"><h2>Acciones</h2>"
    "<form method=\"POST\" action=\"/buscar\"><button>Volver a buscar MEDIBOT</button></form>"
    "<form method=\"POST\" action=\"/calibrar\"><button>Calibrar el teclado</button></form>"
    "<form method=\"POST\" action=\"/reiniciar\"><button>Reiniciar el ESP32</button></form>"
    "</div>");

  // ---- Refresco sin recargar ----
  webTexto(
    "<script>"
    "function t(i,v){var e=document.getElementById(i);if(e)e.innerHTML=v;}"
    "async function r(){try{var d=await(await fetch('/api')).json();"
    "t('s_ir',d.sensor.ir+(d.sensor.dedo?' (DEDO)':' (sin dedo)'));"
    "t('s_rec',d.sensor.reinicios);"
    "t('s_ok',d.sensor.ok?'<span class=\"ok\">responde</span>'"
    ":'<span class=\"mal\">NO detectado</span>');"
    "t('p_bpm',d.pulso.bpm+' BPM');"
    "t('p_spo2',d.pulso.spo2?d.pulso.spo2+' %':'--');"
    "t('p_prog',d.pulso.progreso+' %');"
    "t('p_fiable',d.pulso.fiable?'<span class=\"ok\">si</span>':'aun no');"
    "t('t_ok',d.teclado.conectado?'<span class=\"ok\">conectado</span>'"
    ":'<span class=\"mal\">SIN CONECTAR</span>');"
    "t('t_mv',d.teclado.mv+' mV');t('t_disp',d.teclado.dispersion+' mV');"
    "t('r_rssi',d.red.rssi+' dBm');t('r_ip',d.red.medibot);"
    "t('r_api',d.red.api?'<span class=\"ok\">responde</span>'"
    ":'<span class=\"mal\">sin datos</span>');"
    "t('r_datos','sistema '+d.red.sistema+' &middot; caras '+d.red.caras"
    "+' &middot; rojos '+d.red.rojos);"
    "t('r_fps',d.red.fps1+' / '+d.red.fps2+' fps'+(d.red.grabando?' &middot; GRABANDO':''));"
    "t('e_tiempo',d.equipo.encendido);t('e_heap',d.equipo.heap+' bytes');"
    "t('e_pila',d.equipo.pila+' bytes');"
    "}catch(e){}}"
    "setInterval(r,2000);r();"
    "</script></body></html>");

  webServer.sendContent("");       // fin del envio por trozos
}

// ---------------------------------------------------------------------
// 8.3 Acciones
// ---------------------------------------------------------------------
//  Corren en el nucleo 1, dentro de loop(), asi que pueden tocar el interfaz
//  igual que lo haria una pulsacion de teclado.
static void webVolver(const char *aviso) {
  char b[512];
  snprintf(b, sizeof(b),
    "<!DOCTYPE html><html lang=\"es\"><head><meta charset=\"utf-8\">"
    "<meta http-equiv=\"refresh\" content=\"2;url=/\"></head>"
    "<body style=\"background:#0f1115;color:#e6e9ef;font:15px system-ui;padding:24px\">"
    "%s<p><a style=\"color:#3ddc84\" href=\"/\">volver</a></p></body></html>", aviso);
  webServer.send(200, "text/html; charset=utf-8", b);
}

static void webAccionBuscar() {
  g_netRetry = true;
  webVolver("<p>Buscando MEDIBOT otra vez...</p>");
}

static void webAccionCalibrar() {
  sensorReposo();
  keypadWizardStart();
  setState(STATE_KEYPAD_WIZARD);
  webVolver("<p>Asistente abierto <b>en la pantalla del equipo</b>. "
            "Sigue las instrucciones alli.</p>");
}

static void webAccionReiniciar() {
  webVolver("<p>Reiniciando...</p>");
  delay(300);
  ESP.restart();
}

static void webNoEncontrado() {
  webServer.send(404, "text/plain; charset=utf-8",
                 "No existe. La pagina del triaje esta en /");
}

// Se arranca una sola vez, desde loop() (nucleo 1), cuando el WiFi ya tiene
// IP. Hacerlo desde el nucleo 0 dejaria a los dos nucleos tocando el mismo
// servidor, que es justo lo que no queremos.
static void webArrancar() {
  webServer.on("/", webPagina);
  webServer.on("/api", webApi);
  webServer.on("/buscar", HTTP_POST, webAccionBuscar);
  webServer.on("/calibrar", HTTP_POST, webAccionCalibrar);
  webServer.on("/reiniciar", HTTP_POST, webAccionReiniciar);
  webServer.onNotFound(webNoEncontrado);
  webServer.begin();
  webArrancado = true;
  Serial.printf("[WEB] Configuracion del equipo en http://%s/  (o http://medibot-triaje.local/)\n",
                WiFi.localIP().toString().c_str());
}

// =====================================================================
// 9. SETUP (NUCLEO 1)
// =====================================================================

void setup() {
  Serial.begin(115200);
  delay(50);
  Serial.println(F("\n=== MEDIBOT v6.1 ==="));

  const int motivo = esp_reset_reason();
  const bool huboFallo = (motivo == ESP_RST_PANIC || motivo == ESP_RST_TASK_WDT ||
                          motivo == ESP_RST_INT_WDT || motivo == ESP_RST_WDT);
  Serial.printf("[ARRANQUE] Ultimo reinicio: %s\n", motivoReinicio());
  if (huboFallo && rtcMagia == RTC_MAGIA)
    Serial.printf("[ARRANQUE] Se quedo en el paso: %s\n", rtcPaso);
  Serial.printf("[ARRANQUE] Memoria libre: %u bytes\n", (unsigned)ESP.getFreeHeap());

  // Tras un fallo se arranca sin red, para no repetir el bucle de reinicio
  modoSeguro = huboFallo;
  if (modoSeguro)
    Serial.println(F("[ARRANQUE] MODO SEGURO: se arranca SIN RED. Menu -> MEDIBOT"
                     " -> OK para activarla a mano."));
  paso("arranque");

  // --- Pantalla (setBusClock ANTES de begin: si no, no surte efecto) ---
  paso("pantalla");
  u8g2.setBusClock(LCD_BUS_CLOCK);
  u8g2.begin();
  u8g2.enableUTF8Print();
  u8g2.setFontMode(0);

  // --- ADC y teclado ---
  paso("teclado/adc");
  analogReadResolution(ADC_BITS);
  analogSetPinAttenuation(KEYPAD_PIN, ADC_ATTENUATION);
  pinMode(KEYPAD_PIN, INPUT);

  memcpy(KEYPAD_MAP_DEFECTO, KEYPAD_MAP, sizeof(KEYPAD_MAP));   // red de seguridad

  // La calibracion del teclado vive en la NVS. Si no se puede abrir, no se
  // guardara nada y el asistente saldria en cada arranque sin explicar por
  // que: se intenta reiniciar la particion y, si tampoco, se avisa.
  paso("memoria nvs");
  nvsOk = prefs.begin(NVS_NS, false);
  if (!nvsOk) {
    Serial.println(F("[MEMORIA] La NVS no abre: se reinicia la particion"));
    nvs_flash_erase();
    nvs_flash_init();
    nvsOk = prefs.begin(NVS_NS, false);
  }
  Serial.printf("[MEMORIA] %s\n", nvsOk ? "OK (la calibracion se guarda)"
                                         : "FALLO: la calibracion NO se podra guardar");

  const bool hayCal = keypadHasCalibration();
  if (hayCal) keypadLoadCalibration();
  else        Serial.println(F("[TECLADO] Sin calibracion guardada: se abre el asistente"));
  if (keypadActiveCount() == 0) keypadRestoreDefaults();
  // Medir el reposo ANTES de nada: si coincide con un boton es que se esta
  // manteniendo una tecla al encender -> se abre el asistente (via de escape).
  paso("reposo teclado");
  const Button teclaMantenida = keypadMeasureIdle();

  // --- I2C y sensor de pulso (con escaneo del bus y reintentos, bloque 4.1) ---
  paso("i2c/sensor");
  Wire.begin(I2C_SDA_PIN, I2C_SCL_PIN);
  if (!sensorBegin(true))
    Serial.println(F("[MAX] Se seguira reintentando en segundo plano cada 3 s"));

  vitalsClear();
  lastInteraction = millis();
  nextBlinkMs = millis() + random(2000, 5000);

  // 16 KB de pila, no 8: esta tarea ya no solo lee el sensor, tambien abre
  // conexiones HTTP y parsea JSON, y eso gasta pila de sobra para desbordar
  // los 8 KB que bastaban antes. Un desbordamiento aqui es un reinicio seco
  // ("A stack overflow in task SensorTask has been detected" -> Rebooting).
  // Cuanta queda de verdad se ve en Diagnostico y por Serial.
  paso("tarea nucleo 0");
  xTaskCreatePinnedToCore(sensorTaskCode, "SensorTask", TAREA_STACK, NULL, 1,
                          &SensorTaskHandle, 0);
  sensorReposo();          // en cuanto arranca, a conectarse a la WiFi (si no
                           // estamos en modo seguro)

  // Sin calibracion guardada, o con un boton mantenido al encender -> asistente.
  // Pero si el teclado ni siquiera esta conectado no hay nada que calibrar: el
  // asistente se quedaria esperando pulsaciones imposibles, asi que se avisa y
  // se sigue al menu con la tabla de fabrica.
  if (keypad.desconectado) {
    Serial.println(F("[TECLADO] Asistente NO abierto: conecta el teclado y reinicia"));
    keypadRestoreDefaults();
    setState(STATE_BOOT);
  } else if (!hayCal || teclaMantenida != BTN_NONE) {
    keypadWizardStart();
    setState(STATE_KEYPAD_WIZARD);
  } else {
    setState(STATE_BOOT);
  }
  paso("listo");
}

// =====================================================================
// 10. LOOP (NUCLEO 1): SOLO UI, SIN NINGUN delay() BLOQUEANTE
// =====================================================================
void loop() {
  const uint32_t now = millis();

  // --- 10.0 Servidor web: se arranca en cuanto hay IP y se atiende siempre ---
  //  handleClient() no bloquea cuando no hay nadie pidiendo nada, asi que
  //  puede ir en cada vuelta sin estropear las animaciones. Y al estar aqui,
  //  en el nucleo 1, la pagina sigue respondiendo mientras el nucleo 0 mide.
  if (g_wifiListo && !webArrancado) webArrancar();
  if (webArrancado) webServer.handleClient();

  // --- 10.1 Consola: 'c' abre el asistente de calibracion del teclado ---
  while (Serial.available()) {
    const int c = Serial.read();
    if (c == 'c' || c == 'C') {
      sensorReposo();
      keypadWizardStart();
      setState(STATE_KEYPAD_WIZARD);
    }
  }

  // --- 9.2 Teclado (o asistente): muestreo periodico no bloqueante ---
  if (now - lastKeyPollMs >= KEY_POLL_MS) {
    lastKeyPollMs = now;
    if (currentState == STATE_KEYPAD_WIZARD) {
      if (keypadWizardStep(now)) {
        currentEmotion = EMOTION_NORMAL;
        mainMenuSelection = 0;
        mainMenuTop = 0;
        lastInteraction = now;
        setState(STATE_MENU);
      }
      needsRedraw = true;
    } else {
      const Button ev = keypadPoll();
      if (ev != BTN_NONE) processInputs(ev);
    }
  }

  // --- 9.3 Reloj de animacion (independiente de la logica de estados) ---
  if (now - lastFrameMs >= UI_FRAME_MS) {
    lastFrameMs = now;
    animFrame++;
    // Parpadeo temporizado: mucho mas natural que random() por frame
    if (currentEmotion == EMOTION_NORMAL || currentEmotion == EMOTION_HAPPY) {
      if (!isBlinking && now >= nextBlinkMs) { isBlinking = true;  blinkEndsMs = now + 120; }
      else if (isBlinking && now >= blinkEndsMs) { isBlinking = false; nextBlinkMs = now + random(2200, 6000); }
    } else {
      isBlinking = false;
    }
    // Solo las pantallas con animacion piden refresco por frame; las estaticas
    // se redibujan unicamente cuando cambia algo (ahorra bus SPI del ST7920).
    if (screenIsAnimated()) needsRedraw = true;
  }

  // --- 9.4 Transiciones de la maquina de estados (siempre con millis()) ---
  const Vitals v = vitalsGet();
  // Solo valen los datos de la medida EN CURSO: si el nucleo 0 todavia no ha
  // adoptado el modo que se le acaba de pedir, lo que hay en 'v' es del modo
  // anterior (por ejemplo el ppgFailed de un intento que caduco) y aceptarlo
  // cancelaria la medida nueva nada mas empezar.
  const bool vDeEstaMedida = vitalsVigentes(v);

  switch (currentState) {
    case STATE_BOOT:
      if (stateElapsed() > (modoSeguro ? BOOT_SCREEN_FALLO_MS : BOOT_SCREEN_MS)) {
        currentEmotion = EMOTION_NORMAL;
        setState(STATE_IDLE_FACE);
      }
      break;

    case STATE_TRIAGE_FINGER_REQ:
      if (stateElapsed() > REQ_SCREEN_MS) {
        sensorRequest(SENS_PPG);
        currentEmotion = EMOTION_LOADING;
        setState(STATE_TRIAGE_FINGER_READ);
      }
      break;

    case STATE_TRIAGE_FINGER_READ:
      if (!vDeEstaMedida) break;              // el nucleo 0 aun no ha arrancado
      if (v.ppgReady) {
        patientBPM  = v.finalBPM;
        patientSpO2 = v.finalSpO2;
        sensorReposo();
        evaluateDiagnoses();
        // La cuenta de inactividad arranca AQUI, no en la ultima tecla: si no,
        // el tiempo que ha durado la medida se come el rato para leer esto.
        lastInteraction = millis();
        saveReport();
        resultPage = 0;
        currentEmotion = (patientBPM > 100 || patientBPM < 60 || patientSpO2 < 92)
                         ? EMOTION_SAD : EMOTION_HAPPY;
        setState(STATE_TRIAGE_RESULT);
      } else if (v.ppgFailed) {
        abortMeasurement(v.fingerPresent ? "Senal debil o movimiento" : "No se detecto el dedo");
      }
      break;

    case STATE_SIGNAL_ERROR:
      if (stateElapsed() > ERROR_SCREEN_MS) { currentEmotion = EMOTION_NORMAL; setState(STATE_MENU); }
      break;

    default:
      break;
  }

  // --- 9.5 Vuelta a reposo por inactividad (nunca durante una medida) ---
  const bool midida = (currentState == STATE_TRIAGE_FINGER_REQ ||
                       currentState == STATE_TRIAGE_FINGER_READ);
  // Diagnostico y MEDIBOT enseñan datos EN VIVO y se salen con ATRAS: no
  // tiene sentido que caduquen mientras alguien las esta mirando (buscar la
  // Raspberry por la red puede pasar del minuto, y probar el sensor con el
  // dedo tambien lleva su rato).
  const bool mirando = (currentState == STATE_DIAG || currentState == STATE_MEDIBOT);
  if (!midida && !mirando && currentState != STATE_IDLE_FACE &&
      currentState != STATE_BOOT && currentState != STATE_KEYPAD_WIZARD &&
      (now - lastInteraction > INACTIVITY_TIMEOUT)) {
    currentEmotion = EMOTION_NORMAL;
    // Al quedarse solo, el menu vuelve al principio: el siguiente que llegue
    // se lo encuentra en la primera entrada y no donde lo dejo el anterior.
    mainMenuSelection = 0;
    mainMenuTop = 0;
    setState(STATE_IDLE_FACE);
  }

  // --- 9.6 Dibujado ---
  if (needsRedraw) renderUI();

  // Cede el nucleo 1 al planificador (no es un delay bloqueante: libera la CPU
  // y alimenta el watchdog). El nucleo 0 sigue capturando muestras sin pausa.
  vTaskDelay(1 / portTICK_PERIOD_MS);
}
