/* =====================================================================
 *  MEDIBOT PANEL v7.0
 *  ESP32 + ST7920 128x64 (U8g2) + MAX30102 + ADKeyboard analogico
 * =====================================================================
 *  FUNCIONES
 *    - Logo giratorio al arrancar
 *    - Auto-chequeo con MAX30102 (BPM + SpO2)     [unico sensor]
 *    - Conectar con MEDIBOT: busca la Raspberry en la red, muestra su QR
 *      y los datos en vivo de /api/esp32
 *    - Historial de lecturas guardado en memoria (sobrevive al apagado)
 *    - Asistente de calibracion del teclado analogico
 *
 *  NUCLEO 0 : sensor y red (nunca a la vez)
 *  NUCLEO 1 : teclado, animaciones e interfaz
 *
 *  LIBRERIAS
 *    U8g2 (olikraus) | SparkFun MAX3010x | QRCode (ricmoo) | ArduinoJson
 *  Si el binario no cabe: Herramientas > Partition Scheme > Huge APP
 * ===================================================================== */

#include <Arduino.h>
#include <U8g2lib.h>
#include <SPI.h>
#include <Wire.h>
#include <math.h>
#include <stdlib.h>
#include <time.h>
#include <Preferences.h>
#include <nvs_flash.h>
#include <esp_system.h>      // esp_reset_reason(): por que se reinicio
#include <WiFi.h>
#include <ESPmDNS.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include "qrcode.h"
#include "MAX30105.h"
#include "spo2_algorithm.h"
//  NO se usa "heartRate.h" (checkForBeat): trunca la muestra IR a 16 bits y
//  con el dedo puesto el sensor entrega muchas mas cuentas. Ver bloque 7.1.

// =====================================================================
// 1. CONFIGURACION   (todo lo ajustable esta aqui)
// =====================================================================

// --- 1.1 PINES -------------------------------------------------------
#define LCD_CS_PIN        5
#define LCD_RESET_PIN     19
#define KEYPAD_PIN        34        // ADC1_CH6: sigue funcionando con WiFi.
                                    // NO lo muevas a ADC2 (0,2,4,12-15,25-27):
                                    // esos pines mueren al llamar WiFi.begin()
#define I2C_SDA_PIN       21
#define I2C_SCL_PIN       22

// --- 1.2 WIFI Y MEDIBOT ----------------------------------------------
#define WIFI_SSID         "MEDIBOT"
#define WIFI_PASS         "MEDIBOTCDB"
#define WIFI_TIMEOUT_MS   15000UL

#define MEDIBOT_PORT_MAIN 5000      // Vision_MEDIBOT.py  -> X-Medibot-Build
#define MEDIBOT_PORT_ALT  5001      // Pastillero.py      -> X-Pillbox-Build
#define MEDIBOT_MDNS_SVC  "medibot" // _medibot._tcp (opcional, ver CALIBRACION)
#define MEDIBOT_MDNS_HOST "raspberrypi"
#define MEDIBOT_API       "/api/esp32"
#define JSON_POLL_MS      1000UL    // refresco de los datos en vivo
#define SWEEP_TIMEOUT_MS  180       // ms de espera por IP en el barrido
#define TZ_INFO           "CET-1CEST,M3.5.0,M10.5.0/3"   // horario de Espana
#define NTP_SERVER        "pool.ntp.org"

// --- 1.3 ADC Y TECLADO ------------------------------------------------
//  IMPORTANTE: estos valores son solo el punto de partida. El asistente de
//  calibracion (menu "Calibrar teclado", o mantener un boton al encender, o
//  enviar 'c' por Serial a 115200) MIDE tus botones reales y guarda los
//  rangos en la memoria del ESP32. Lo guardado manda sobre esta tabla.
#define ADC_BITS              12
#define ADC_ATTENUATION       ADC_11db   // ~0..3.1 V utiles
#define ADC_FULLSCALE_MV      3300.0f    // solo si USE_ESP_ADC_CAL = 0
#define USE_ESP_ADC_CAL       1          // analogReadMilliVolts() (recomendado)

#define KEY_POLL_MS           10
#define KEY_SAMPLES           9          // impar, >= 3 (mediana)
#define KEY_EMA_ALPHA         0.40f
#define KEY_DEBOUNCE_MS       40
#define KEY_RELEASE_MS       40
#define KEY_HYSTERESIS_MV     70
#define KEY_IDLE_GUARD_MV     120        // margen prohibido alrededor del reposo
#define KEY_REPEAT_DELAY_MS   600
#define KEY_REPEAT_RATE_MS    180

//  *** EL TECLADO SON 4 BOTONES: ARRIBA, ABAJO, OK y ATRAS ***
//  El ADKeyboard trae cinco, pero el de 3.70 V no se puede usar con un ESP32:
//  su ADC satura hacia 3.15 V, asi que ese boton y el reposo (que es VCC) dan
//  los dos 4095 y son indistinguibles. Con cuatro se navega todo.
//  ALIMENTACION: mejor 3V3 que 5 V. En reposo la salida es VCC, o sea que a
//  5 V se le meten 5 V a GPIO34, fuera de especificacion. A 3V3 los cuatro
//  botones quedan en 0.00 / 0.46 / 0.99 / 1.65 V y el reposo en 3.3 V.
enum Button : uint8_t { BTN_NONE = 0, BTN_OK, BTN_UP, BTN_DOWN, BTN_BACK, BTN_COUNT };

//  Orden en el que el asistente pide los botones y nombre en pantalla
const char *BTN_NOMBRE[BTN_COUNT] = { "----", "OK", "ARRIBA", "ABAJO", "ATRAS" };
const Button BTN_ORDEN[] = { BTN_UP, BTN_DOWN, BTN_OK, BTN_BACK };
const uint8_t BTN_ORDEN_N = sizeof(BTN_ORDEN) / sizeof(BTN_ORDEN[0]);

//  Tabla de partida (modulo a 5 V: 0.01/0.70/1.50/2.50 V), en milivoltios
//  medidos EN EL PIN. Solo vale hasta la primera calibracion.
struct KeyDef { Button id; int16_t mvMin; int16_t mvMax; };
KeyDef keyMap[] = {
  { BTN_DOWN,   -50,  300 },
  { BTN_BACK,   450,  950 },
  { BTN_OK,    1250, 1750 },
  { BTN_UP,    2250, 2750 },
};
const uint8_t KEYMAP_N = sizeof(keyMap) / sizeof(keyMap[0]);
KeyDef keyMapDefecto[5];          // copia de la tabla de arriba (red de seguridad)

// --- 1.4 SENSOR MAX30102 ---------------------------------------------
#define MAX_LED_BRIGHTNESS    0x3F
#define MAX_SAMPLE_AVERAGE    4
#define MAX_LED_MODE          2          // Rojo + IR (lo que necesita la SpO2)
#define MAX_SAMPLE_RATE       400        // 400/4 = 100 Hz efectivos
#define MAX_PULSE_WIDTH       411
#define MAX_ADC_RANGE         4096
#define MAX_I2C_ADDR          0x57
//  Con cables largos el bus a 400 kHz falla a ratos y algunos modulos tardan
//  en arrancar: se reintenta y, si no, se baja a 100 kHz (a 100 Hz de muestreo
//  hacen falta 600 bytes/s, asi que sobra).
#define MAX_I2C_HZ_FAST       400000UL
#define MAX_I2C_HZ_SAFE       100000UL
#define MAX_INIT_RETRIES      5
#define SENSOR_RETRY_MS       3000UL     // redeteccion en segundo plano
#define PPG_STALL_MS          2500UL     // sin muestras -> reiniciar el sensor
#define MAX_LEDS_ALWAYS_ON    1          // LED encendidos desde el arranque

#define PPG_SPS               (MAX_SAMPLE_RATE / MAX_SAMPLE_AVERAGE)  // 100
#define SPO2_FS               25         // FS que asume spo2_algorithm.h
#define SPO2_DECIM            (PPG_SPS / SPO2_FS)                     // 4
#define SPO2_BUF_LEN          100        // 4 s de ventana
#define SPO2_SHIFT            25         // recalculo cada segundo

//  No todos los modulos dan lo mismo con el dedo puesto (de 35.000 a 150.000
//  cuentas): estos umbrales son prudentes. Sin dedo suele quedarse < 10.000.
#define FINGER_IR_ON          30000UL
#define FINGER_IR_OFF         18000UL
#define FINGER_STABLE_MS      400
#define MIN_PERFUSION         0.15f
#define HR_MIN                40
#define HR_MAX                180
#define SPO2_MIN              70
#define SPO2_MAX              100
#define SPO2_OFFSET           0          // 0 = sin trucar. Ver CALIBRACION.md
#define PPG_TARGET            8          // lecturas validas (1/s) -> ~11 s
#define PPG_MAX_OK            16
#define PPG_TIMEOUT_MS        45000UL
#define NO_FINGER_TIMEOUT_MS  20000UL

//  Detector de latidos (bloque 7.1). Umbral ADAPTATIVO sobre la envolvente de
//  la propia senal: funciona igual con un dedo frio que con uno caliente, y
//  BEAT_TH_HIGH evita contar la onda dicrota (el rebote que sigue a la
//  sistole, de un 30-50 % del pico) como si fuera otro latido.
#define BEAT_DC_ALPHA         0.01f    // linea de base (~1 s a 100 Hz)
#define BEAT_LP_ALPHA         0.25f    // paso bajo (~4 Hz a 100 Hz)
#define BEAT_ENV_DECAY        0.997f   // caida de la envolvente por muestra
#define BEAT_TH_HIGH          0.55f    // fraccion de envolvente para disparar
#define BEAT_TH_LOW           0.25f    // fraccion por debajo de la cual rearma
#define BEAT_MIN_AMPLITUDE    25.0f    // cuentas; por debajo solo hay ruido
#define BEAT_WARMUP_SAMPLES   80       // muestras hasta asentar la linea base
#define BEAT_RING             8        // intervalos guardados para la mediana
#define BEAT_MIN_INTERVALS    2        // intervalos minimos para dar un BPM
#define BEAT_REFRACTARIO_MS  (60000UL / HR_MAX)   // 333 ms a 180 BPM
#define BEAT_MAX_INTERVAL_MS (60000UL / HR_MIN)   // 1500 ms a 40 BPM

// --- 1.5 CODIGO QR ----------------------------------------------------
//  QR_INVERTIDO 1 en paneles AZULES con pixeles blancos (negativos).
//  QR_INVERTIDO 0 en paneles verde/amarillo con pixeles negros (positivos).
//  Si el movil no te lo lee, cambia este 0 por un 1 y recompila.
#define QR_INVERTIDO          0
#define QR_VERSION            2          // 25x25 modulos, hasta 32 bytes
#define QR_PIXELS_POR_MODULO  2
#define QR_QUIET              2

// --- 1.6 INTERFAZ -----------------------------------------------------
#define TAREA_STACK           16384    // pila del nucleo 0 (sensor + red)
#define UI_FRAME_MS           40         // 25 fps
#define LCD_BUS_CLOCK         600000UL   // setBusClock ANTES de begin()
#define SPLASH_MS             3200UL
#define INACTIVITY_MS         45000UL
#define HOLD_SCREEN_MS        4000UL
#define ERROR_SCREEN_MS       6000UL
#define HISTORIAL_N           10         // lecturas guardadas en memoria

// =====================================================================
// 2. TIPOS Y ESTADO GLOBAL
// =====================================================================
enum AppState : uint8_t {
  ST_SPLASH, ST_IDLE, ST_MENU,
  ST_CHK_REQ, ST_CHK_READ, ST_CHK_RESULT,
  ST_NET_WIFI, ST_NET_SEARCH, ST_NET_QR, ST_NET_DATA, ST_NET_ERROR,
  ST_HIST, ST_ABOUT, ST_CAL, ST_ERROR
};

enum Emotion : uint8_t { EMO_NORMAL, EMO_DOWN, EMO_UP, EMO_HAPPY, EMO_SAD, EMO_LOAD };

// Modos que la UI (nucleo 1) pide al trabajador (nucleo 0)
enum WorkMode : uint8_t { WK_IDLE, WK_PPG, WK_NET };

// Estado del detector de latidos. El algoritmo esta en el bloque 7.1; el tipo
// tiene que declararse AQUI, antes de la primera funcion del fichero, porque
// el IDE de Arduino genera solo los prototipos de todas las funciones y los
// inserta justo ahi: si el tipo llega despues, el prototipo de beatUpdate() lo
// usaria sin conocerlo ("'BeatDetector' was not declared in this scope").
struct BeatDetector {
  float    dc, lp, env;
  bool     init, armed;
  uint16_t warmup;
  uint32_t ultimoMs;
  uint32_t intervalos[BEAT_RING];
  uint8_t  n, idx;
};

// Etapas de la conexion con MEDIBOT
enum NetStage : uint8_t { NET_OFF, NET_WIFI, NET_MDNS, NET_SWEEP, NET_FOUND, NET_FAIL };

struct Shared {
  uint32_t epoca;            // epoca de medida adoptada por el nucleo 0: si no
                             // coincide con g_epoca, lo que hay aqui es del
                             // modo ANTERIOR y la UI lo ignora.
  // --- PPG ---
  bool     dedo;
  bool     senalOk;
  int      liveBPM, liveSpO2;
  bool     spo2Valido;
  float    perfusion;
  uint8_t  ppgProgreso;
  bool     ppgListo, ppgFallo;
  int      finalBPM, finalSpO2;
  uint32_t ultimoLatido;
  // --- Red ---
  uint8_t  etapa;            // NetStage
  uint8_t  netProgreso;      // 0..100 del barrido
  uint32_t ip;               // 0 = no encontrado
  uint16_t puerto;
  int8_t   rssi;
  char     netMsg[26];
  // --- JSON de /api/esp32 ---
  bool     jsonOk;
  uint32_t jsonMs;
  int      j_sistema, j_detecciones, j_rojos, j_fps1, j_fps2, j_fx, j_fy;
  bool     j_grabando;
};

struct Lectura { uint32_t epoch; uint16_t bpm; uint8_t spo2; uint8_t usado; };

// --- Objetos ---
U8G2_ST7920_128X64_F_HW_SPI u8g2(U8G2_R0, LCD_CS_PIN, LCD_RESET_PIN);
MAX30105 max3010x;
Preferences prefs;

// --- Estado de la app (solo lo toca el nucleo 1) ---
AppState  estado       = ST_SPLASH;
Emotion   emocion      = EMO_NORMAL;
uint32_t  estadoDesde  = 0;
uint32_t  ultimaTecla  = 0;
uint32_t  ultimoFrame  = 0;
uint32_t  ultimoSondeo = 0;
uint16_t  frame        = 0;
bool      repintar     = true;
bool      parpadeo     = false;
uint32_t  proxParpadeo = 0, finParpadeo = 0;

int   menuSel = 0, menuTop = 0;
int   aboutPag = 0, histPag = 0, resPag = 0;
int   pacienteBPM = 0, pacienteSpO2 = 0;
char  diag1[40], diag2[40], errMsg[32] = "";

Lectura historial[HISTORIAL_N];
uint8_t histN = 0;

volatile bool hwMaxOk = false, hwMaxRaro = false;
volatile uint8_t hwMaxId = 0;
volatile uint32_t hwMaxBusHz = MAX_I2C_HZ_FAST;
volatile uint8_t  hwI2cCount = 0;
bool  horaOk = false;
bool  nvsOk  = false;                  // la memoria no volatil responde

// --- Compartido entre nucleos ---
static Shared        g_sh;
static portMUX_TYPE  g_mux = portMUX_INITIALIZER_UNLOCKED;
volatile WorkMode    g_modo  = WK_IDLE;
volatile uint32_t    g_epoca = 0;
TaskHandle_t         g_tarea = NULL;

// --- QR ya generado (se guarda para no recalcularlo en cada frame) ---
char     qrTexto[64] = "";
uint8_t  qrDatos[256];
QRCode   qrCodigo;
bool     qrListo = false;

// --- Prototipos ---
void  irA(AppState s);
Shared shGet();
void  pedirModo(WorkMode m);
Button tecladoLeer();
void  tecladoCargarCal();
bool  tecladoGuardarCal();
bool  tecladoHayCal();
void  histCargar();
void  histGuardar(int bpm, int spo2);
void  pintar();
void  entradaUsuario(Button b);
bool  qrGenerar(const char *txt);
void  dibujarQR(int x, int y);
static bool pantallaAnimada();

// =====================================================================
// 3. MEMORIA NO VOLATIL (calibracion del teclado + historial)
// =====================================================================
#define NVS_NS      "medibot"
#define CAL_MAGIC   0x4B34      // cambia con el formato: una calibracion de
                                // 5 botones se descarta

struct CalEntrada { uint8_t id; int16_t mn, mx; };
struct CalBlob {
  uint16_t   magic;
  int16_t    reposo;
  uint8_t    n;
  CalEntrada e[BTN_COUNT];
};

bool tecladoHayCal() {
  if (!nvsOk) return false;
  CalBlob b;
  if (prefs.getBytesLength("keycal") != sizeof(b)) return false;
  prefs.getBytes("keycal", &b, sizeof(b));
  return (b.magic == CAL_MAGIC && b.n > 0);
}

void tecladoCargarCal();   // definida tras el estado del teclado

void histCargar() {
  if (!nvsOk) { memset(historial, 0, sizeof(historial)); histN = 0; return; }
  size_t n = prefs.getBytesLength("hist");
  if (n == sizeof(historial)) {
    prefs.getBytes("hist", historial, sizeof(historial));
    histN = 0;
    for (uint8_t i = 0; i < HISTORIAL_N; i++) if (historial[i].usado) histN++;
  } else {
    memset(historial, 0, sizeof(historial));
    histN = 0;
  }
}

void histGuardar(int bpm, int spo2) {
  for (int8_t i = HISTORIAL_N - 1; i > 0; i--) historial[i] = historial[i - 1];
  historial[0].epoch = horaOk ? (uint32_t)time(nullptr) : 0;
  historial[0].bpm   = (uint16_t)bpm;
  historial[0].spo2  = (uint8_t)spo2;
  historial[0].usado = 1;
  if (histN < HISTORIAL_N) histN++;
  if (nvsOk) prefs.putBytes("hist", historial, sizeof(historial));
}

// =====================================================================
// 4. TECLADO ANALOGICO
// =====================================================================
struct KeyRT {
  Button   raw          = BTN_NONE;
  Button   estable      = BTN_NONE;
  uint32_t cambioRaw    = 0;
  uint32_t inicioPulsa  = 0;
  uint32_t ultimaRep    = 0;
  float    ema          = 0.0f;
  bool     emaInit      = false;
  int16_t  mv           = 0;
  uint16_t cuentas      = 0;
  int16_t  reposoMv     = 3300;
  bool     reposoOk     = false;
} kb;

static int cmpI16(const void *a, const void *b) {
  int16_t x = *(const int16_t *)a, y = *(const int16_t *)b;
  return (x > y) - (x < y);
}

static inline int16_t difAbs(int16_t a, int16_t b) { return (a > b) ? (a - b) : (b - a); }

// Lectura cruda filtrada por mediana (quita los picos del ADC del ESP32)
static int16_t leerMvCrudo() {
  int16_t s[KEY_SAMPLES];
  uint32_t acc = 0;
  for (uint8_t i = 0; i < KEY_SAMPLES; i++) {
    int bruto = analogRead(KEYPAD_PIN);
    acc += bruto;
#if USE_ESP_ADC_CAL
    s[i] = (int16_t)analogReadMilliVolts(KEYPAD_PIN);
#else
    s[i] = (int16_t)((bruto * ADC_FULLSCALE_MV) / (float)((1 << ADC_BITS) - 1));
#endif
  }
  kb.cuentas = (uint16_t)(acc / KEY_SAMPLES);
  qsort(s, KEY_SAMPLES, sizeof(int16_t), cmpI16);
  const uint8_t m = KEY_SAMPLES / 2;
  return (int16_t)((s[m - 1] + s[m] + s[m + 1]) / 3);
}

// Clasificacion: rangos independientes + histeresis + guarda de reposo.
// Devuelve BTN_NONE si la tension esta en zona muerta, cerca del reposo, o si
// (por una tabla mal puesta) encajara en dos botones a la vez. Es imposible
// que salgan dos botones simultaneos.
static Button clasificar(int16_t mv, Button mantenido) {
  if (kb.reposoOk && difAbs(mv, kb.reposoMv) < KEY_IDLE_GUARD_MV) return BTN_NONE;
  Button hallado = BTN_NONE;
  uint8_t coincidencias = 0;
  for (uint8_t i = 0; i < KEYMAP_N; i++) {
    int16_t lo = keyMap[i].mvMin, hi = keyMap[i].mvMax;
    if (lo > hi) continue;                        // entrada desactivada
    if (keyMap[i].id == mantenido) { lo -= KEY_HYSTERESIS_MV; hi += KEY_HYSTERESIS_MV; }
    if (mv >= lo && mv <= hi) { hallado = keyMap[i].id; coincidencias++; }
  }
  return (coincidencias == 1) ? hallado : BTN_NONE;
}

// Un evento por pulsacion (flanco) + autorepeticion en ARRIBA/ABAJO
Button tecladoLeer() {
  const uint32_t ahora = millis();
  const int16_t bruto = leerMvCrudo();

  if (!kb.emaInit) { kb.ema = bruto; kb.emaInit = true; }
  else kb.ema = KEY_EMA_ALPHA * bruto + (1.0f - KEY_EMA_ALPHA) * kb.ema;
  kb.mv = (int16_t)kb.ema;

  const Button raw = clasificar(kb.mv, kb.estable);
  if (raw != kb.raw) { kb.raw = raw; kb.cambioRaw = ahora; }

  Button ev = BTN_NONE;
  const uint32_t espera = (raw == BTN_NONE) ? KEY_RELEASE_MS : KEY_DEBOUNCE_MS;

  if (raw != kb.estable) {
    if (ahora - kb.cambioRaw >= espera) {
      kb.estable = raw;
      if (raw != BTN_NONE) { ev = raw; kb.inicioPulsa = ahora; kb.ultimaRep = ahora; }
    }
  } else if (raw == BTN_UP || raw == BTN_DOWN) {
    if (ahora - kb.inicioPulsa > KEY_REPEAT_DELAY_MS &&
        ahora - kb.ultimaRep  > KEY_REPEAT_RATE_MS) {
      kb.ultimaRep = ahora;
      ev = raw;
    }
  }
  return ev;
}

// Cuantos botones tienen un rango utilizable ahora mismo
static uint8_t tecladoActivos() {
  uint8_t n = 0;
  for (uint8_t k = 0; k < KEYMAP_N; k++) if (keyMap[k].mvMin <= keyMap[k].mvMax) n++;
  return n;
}

static void tecladoRestaurarDefecto() {
  memcpy(keyMap, keyMapDefecto, sizeof(keyMap));
  Serial.println(F("[TECLADO] Tabla por defecto restaurada"));
}

void tecladoCargarCal() {
  if (!nvsOk) return;
  CalBlob b;
  if (prefs.getBytesLength("keycal") != sizeof(b)) return;
  prefs.getBytes("keycal", &b, sizeof(b));
  if (b.magic != CAL_MAGIC || b.n == 0 || b.n > BTN_COUNT) return;

  for (uint8_t i = 0; i < KEYMAP_N; i++) { keyMap[i].mvMin = 1; keyMap[i].mvMax = 0; }  // todo off
  for (uint8_t i = 0; i < b.n; i++) {
    for (uint8_t k = 0; k < KEYMAP_N; k++) {
      if (keyMap[k].id == b.e[i].id) { keyMap[k].mvMin = b.e[i].mn; keyMap[k].mvMax = b.e[i].mx; }
    }
  }
  kb.reposoMv = b.reposo;
  kb.reposoOk = true;
  Serial.printf("[TECLADO] Calibracion cargada. Reposo %d mV\n", (int)b.reposo);
  for (uint8_t k = 0; k < KEYMAP_N; k++)
    if (keyMap[k].mvMin <= keyMap[k].mvMax)
      Serial.printf("   %-7s %d..%d mV\n", BTN_NOMBRE[keyMap[k].id], (int)keyMap[k].mvMin, (int)keyMap[k].mvMax);
}

bool tecladoGuardarCal() {
  if (!nvsOk) {
    Serial.println(F("[TECLADO] NO se guarda: la memoria no volatil no responde"));
    return false;
  }
  CalBlob b;
  memset(&b, 0, sizeof(b));
  b.magic = CAL_MAGIC;
  b.reposo = kb.reposoMv;
  b.n = 0;
  for (uint8_t k = 0; k < KEYMAP_N && b.n < BTN_COUNT; k++) {
    if (keyMap[k].mvMin <= keyMap[k].mvMax) {
      b.e[b.n].id = keyMap[k].id;
      b.e[b.n].mn = keyMap[k].mvMin;
      b.e[b.n].mx = keyMap[k].mvMax;
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

// Al encender: mide el nivel de reposo. Si resulta que coincide con un boton
// de la tabla, es que el usuario esta MANTENIENDO un boton -> lo devuelve
// (asi, mantener cualquier tecla al encender abre el asistente aunque la
// calibracion guardada este mal y no se pueda navegar el menu).
static Button tecladoMedirReposo() {
  int16_t m[24];
  for (uint8_t i = 0; i < 24; i++) { m[i] = leerMvCrudo(); delay(20); }
  qsort(m, 24, sizeof(int16_t), cmpI16);
  const int16_t mediana = m[12];
  const int16_t disp = m[21] - m[2];              // dispersion

  kb.reposoOk = false;                            // sin guarda para clasificar
  const Button coincide = clasificar(mediana, BTN_NONE);
  kb.ema = mediana; kb.emaInit = true; kb.mv = mediana;

  Serial.printf("[TECLADO] Nivel en reposo: %d mV (ADC %u, dispersion %d mV)\n",
                (int)mediana, (unsigned)kb.cuentas, (int)disp);
  if (coincide != BTN_NONE) {
    Serial.printf("[TECLADO] Coincide con %s: hay un boton pulsado al arrancar\n",
                  BTN_NOMBRE[coincide]);
    return coincide;
  }
  kb.reposoMv = mediana;
  kb.reposoOk = true;
  return BTN_NONE;
}

// =====================================================================
// 5. ASISTENTE DE CALIBRACION DEL TECLADO
// =====================================================================
//  Mide los botones REALES y calcula los rangos. Es la respuesta a "los
//  botones no funcionan": no hay que adivinar ningun umbral.
struct Asistente {
  uint8_t  paso;                 // indice dentro de BTN_ORDEN
  uint8_t  fase;                 // 0 reposo, 1 pidiendo, 2 soltar, 3 resumen
  uint32_t t0;
  uint32_t estableDesde;
  int16_t  ultimo;
  int16_t  centro[BTN_COUNT];
  bool     hecho[BTN_COUNT];
  uint8_t  capturados;
  bool     guardado;
  bool     esperandoSoltar;
  char     aviso[30];
} wiz;

#define WIZ_REPOSO_MS    1500
#define WIZ_ESTABLE_MS   700
#define WIZ_SALTO_MS     12000
#define WIZ_UMBRAL_MV    150     // diferencia minima con el reposo
#define WIZ_TOLER_MV     45      // cuanto puede moverse y seguir siendo "estable"
#define WIZ_SOLTAR_MS    12000   // gracia esperando a que se suelte el teclado

void wizIniciar() {
  memset(&wiz, 0, sizeof(wiz));
  wiz.fase = 0;
  wiz.t0 = millis();
  wiz.estableDesde = wiz.t0;     // sin esto la fase 0 terminaria al instante
  wiz.ultimo = kb.mv;
  kb.reposoOk = false;           // durante el asistente no se filtra por reposo
  Serial.println(F("\n[ASISTENTE] Calibracion del teclado. No toques nada..."));
}

// Devuelve true cuando termina y ya ha guardado
bool wizPaso(uint32_t ahora) {
  const int16_t mv = leerMvCrudo();
  kb.ema = KEY_EMA_ALPHA * mv + (1.0f - KEY_EMA_ALPHA) * kb.ema;
  kb.mv = (int16_t)kb.ema;

  if (difAbs(kb.mv, wiz.ultimo) > WIZ_TOLER_MV) { wiz.ultimo = kb.mv; wiz.estableDesde = ahora; }

  switch (wiz.fase) {
    case 0: {                                   // ---- medir reposo ----
      // Al asistente se entra MANTENIENDO un boton al encender, o pulsando OK
      // en el menu: al empezar siempre hay una tecla pulsada. Si se midiera el
      // reposo ahi, se tomaria el nivel del BOTON como reposo y despues
      // ninguna pulsacion pareceria distinta de el: la tabla guardada dejaria
      // el teclado inservible. Se espera a que la lectura no encaje con ningun
      // boton y lleve quieta WIZ_REPOSO_MS (con WIZ_SOLTAR_MS de gracia).
      const bool pareceBoton = (clasificar(kb.mv, BTN_NONE) != BTN_NONE);
      wiz.esperandoSoltar = pareceBoton && (ahora - wiz.t0 < WIZ_SOLTAR_MS);
      if (!wiz.esperandoSoltar && ahora - wiz.estableDesde >= WIZ_REPOSO_MS) {
        kb.reposoMv = kb.mv;
        wiz.fase = 1;
        wiz.paso = 0;
        wiz.t0 = ahora;
        wiz.estableDesde = ahora;
        Serial.printf("[ASISTENTE] Reposo = %d mV\n", (int)kb.reposoMv);
      }
      break;
    }

    case 1: {                                   // ---- capturar un boton ----
      const bool pulsado = difAbs(kb.mv, kb.reposoMv) >= WIZ_UMBRAL_MV;
      if (pulsado && (ahora - wiz.estableDesde >= WIZ_ESTABLE_MS)) {
        const Button b = BTN_ORDEN[wiz.paso];
        wiz.centro[b] = kb.mv;
        wiz.hecho[b]  = true;
        wiz.capturados++;
        Serial.printf("[ASISTENTE] %-7s = %d mV (ADC %u)\n", BTN_NOMBRE[b], (int)kb.mv, (unsigned)kb.cuentas);
        wiz.fase = 2;
        wiz.t0 = ahora;
      } else if (ahora - wiz.t0 >= WIZ_SALTO_MS) {
        Serial.printf("[ASISTENTE] %s omitido (sin pulsacion)\n", BTN_NOMBRE[BTN_ORDEN[wiz.paso]]);
        wiz.fase = 2;
        wiz.t0 = ahora;
      }
      break;
    }

    case 2:                                     // ---- esperar que suelte ----
      if (difAbs(kb.mv, kb.reposoMv) < WIZ_UMBRAL_MV / 2 || (ahora - wiz.t0 > 8000)) {
        wiz.paso++;
        if (wiz.paso >= BTN_ORDEN_N) { wiz.fase = 3; wiz.t0 = ahora; }
        else { wiz.fase = 1; wiz.t0 = ahora; wiz.estableDesde = ahora; }
      }
      break;

    case 3:                                     // ---- calcular y guardar ----
      // Cada boton recibe medio hueco hasta su vecino mas cercano (otro boton
      // capturado o el propio reposo), con tope de 250 mV y minimo de 40 mV.
      for (uint8_t k = 0; k < KEYMAP_N; k++) { keyMap[k].mvMin = 1; keyMap[k].mvMax = 0; }
      wiz.aviso[0] = '\0';
      for (uint8_t k = 0; k < KEYMAP_N; k++) {
        const Button b = keyMap[k].id;
        if (!wiz.hecho[b]) continue;
        int16_t hueco = difAbs(wiz.centro[b], kb.reposoMv);
        for (uint8_t j = 0; j < KEYMAP_N; j++) {
          const Button o = keyMap[j].id;
          if (o == b || !wiz.hecho[o]) continue;
          const int16_t d = difAbs(wiz.centro[b], wiz.centro[o]);
          if (d < hueco) hueco = d;
        }
        int16_t medio = hueco / 2 - 25;
        if (medio > 250) medio = 250;
        if (medio < 40) {
          snprintf(wiz.aviso, sizeof(wiz.aviso), "%s se confunde", BTN_NOMBRE[b]);
          Serial.printf("[ASISTENTE] %s descartado: solo %d mV hasta su vecino\n",
                        BTN_NOMBRE[b], (int)hueco);
          continue;                              // queda desactivado
        }
        keyMap[k].mvMin = wiz.centro[b] - medio;
        keyMap[k].mvMax = wiz.centro[b] + medio;
      }
      if (tecladoActivos() == 0) {
        // Ningun boton utilizable: no se guarda nada y se vuelve a la tabla de
        // fabrica, para no dejar el equipo sin teclado.
        tecladoRestaurarDefecto();
        snprintf(wiz.aviso, sizeof(wiz.aviso), "Revisa el cableado");
        wiz.guardado = false;
        Serial.println(F("[ASISTENTE] Ningun boton valido: no hay nada que guardar"));
      } else {
        wiz.guardado = tecladoGuardarCal();
        if (!wiz.guardado) snprintf(wiz.aviso, sizeof(wiz.aviso), "NO se pudo guardar");
      }
      kb.reposoOk = true;
      wiz.fase = 4;
      wiz.t0 = ahora;
      break;

    default:
      return (ahora - wiz.t0 > 3500);            // resumen en pantalla
  }
  return false;
}

// =====================================================================
// 6. ESTADO COMPARTIDO ENTRE NUCLEOS
// =====================================================================
Shared shGet() {
  Shared c;
  portENTER_CRITICAL(&g_mux);
  c = g_sh;
  portEXIT_CRITICAL(&g_mux);
  return c;
}

// La UI es la unica que cambia de modo. El nucleo 0 detecta el cambio por el
// contador de epoca. Asi el nucleo 0 NUNCA toca la maquina de estados.
void pedirModo(WorkMode m) {
  portENTER_CRITICAL(&g_mux);
  g_sh.dedo = false;  g_sh.senalOk = false;
  g_sh.liveBPM = 0;   g_sh.liveSpO2 = 0;  g_sh.spo2Valido = false;
  g_sh.perfusion = 0; g_sh.ppgProgreso = 0;
  g_sh.ppgListo = false; g_sh.ppgFallo = false;
  g_sh.finalBPM = 0;  g_sh.finalSpO2 = 0;
  g_modo = m;
  g_epoca++;
  portEXIT_CRITICAL(&g_mux);
}

// =====================================================================
// 7. SENSOR MAX30102 (unico sensor del equipo)
// =====================================================================

// ---------------------------------------------------------------------
// 7.0 ARRANQUE, VERIFICACION Y RECUPERACION
// ---------------------------------------------------------------------
static bool regLeer(uint8_t dir, uint8_t reg, uint8_t &valor) {
  Wire.beginTransmission(dir);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(dir, (uint8_t)1) != 1) return false;
  valor = Wire.read();
  return true;
}

// Escaneo del bus: 0 dispositivos = cableado o alimentacion, no software.
static void i2cScan() {
  uint8_t n = 0;
  for (uint8_t a = 1; a < 127; a++) {
    Wire.beginTransmission(a);
    if (Wire.endTransmission() == 0) { n++; Serial.printf("[I2C] dispositivo en 0x%02X\n", a); }
  }
  hwI2cCount = n;
  if (n == 0) Serial.println(F("[I2C] NADIE contesta: revisa VIN, GND, SDA(21) y SCL(22)"));
}

// Configura y COMPRUEBA releyendo los registros: una escritura perdida deja
// el sensor mudo o a oscuras sin que el firmware se entere.
static bool sensorConfigurar(bool ledsOn) {
  max3010x.setup(MAX_LED_BRIGHTNESS, MAX_SAMPLE_AVERAGE, MAX_LED_MODE,
                 MAX_SAMPLE_RATE, MAX_PULSE_WIDTH, MAX_ADC_RANGE);
  const uint8_t amp = ledsOn ? MAX_LED_BRIGHTNESS : 0x00;
  max3010x.setPulseAmplitudeRed(amp);
  max3010x.setPulseAmplitudeIR(amp);
  max3010x.setPulseAmplitudeGreen(0x00);
  max3010x.clearFIFO();

  uint8_t modo = 0, led1 = 0, led2 = 0;
  if (!(regLeer(MAX_I2C_ADDR, 0x09, modo) && regLeer(MAX_I2C_ADDR, 0x0C, led1) &&
        regLeer(MAX_I2C_ADDR, 0x0D, led2))) {
    Serial.println(F("[MAX] No se pueden releer los registros"));
    return false;
  }
  const uint8_t modoEsperado = (MAX_LED_MODE == 3) ? 0x07 : (MAX_LED_MODE == 2 ? 0x03 : 0x02);
  if ((modo & 0x07) != modoEsperado || led1 != amp || led2 != amp) {
    Serial.printf("[MAX] La configuracion NO se aplico (modo 0x%02X, LED 0x%02X/0x%02X)\n",
                  modo & 0x07, led1, led2);
    return false;
  }
  Serial.printf("[MAX] Configurado y verificado: modo 0x%02X, LED 0x%02X, %d Hz\n",
                modoEsperado, amp, PPG_SPS);
  return true;
}

static bool sensorArrancar(bool conEscaneo) {
  hwMaxRaro = false;
  if (conEscaneo) i2cScan();
  const uint32_t velocidades[2] = { MAX_I2C_HZ_FAST, MAX_I2C_HZ_SAFE };
  for (uint8_t v = 0; v < 2; v++) {
    Wire.setClock(velocidades[v]);
    for (uint8_t i = 0; i < MAX_INIT_RETRIES; i++) {
      uint8_t id = 0;
      if (regLeer(MAX_I2C_ADDR, 0xFF, id)) {
        if (id == 0x11) {
          hwMaxRaro = true; hwMaxId = id;
          Serial.println(F("[MAX] Es un MAX30100: incompatible con esta libreria"));
          return false;
        }
        if (id == 0x15 && max3010x.begin(Wire, velocidades[v], MAX_I2C_ADDR)) {
          hwMaxId = id;
          hwMaxBusHz = velocidades[v];
          Wire.setClock(velocidades[v]);
          if (sensorConfigurar(MAX_LEDS_ALWAYS_ON != 0)) {
            hwMaxOk = true;
            Serial.printf("[MAX] Sensor OK (ID 0x%02X) a %lu kHz, intento %u\n", id,
                          (unsigned long)(velocidades[v] / 1000), (unsigned)(i + 1));
            return true;
          }
        }
      }
      delay(80);
    }
    if (v == 0) Serial.println(F("[MAX] Sin respuesta a 400 kHz: se prueba a 100 kHz"));
  }
  hwMaxOk = false;
  Serial.println(F("[MAX] Sensor de pulso NO detectado en 0x57"));
  return false;
}

static bool sensorRecuperar() {
  Serial.println(F("[MAX] El sensor ha dejado de dar muestras: reiniciandolo"));
  Wire.setClock(hwMaxBusHz);
  if (max3010x.begin(Wire, hwMaxBusHz, MAX_I2C_ADDR) && sensorConfigurar(true)) return true;
  hwMaxOk = false;
  return false;
}

// ---------------------------------------------------------------------
// 7.1 DETECTOR DE LATIDOS PROPIO
// ---------------------------------------------------------------------
//  POR QUE NO SE USA checkForBeat() DE LA LIBRERIA SPARKFUN: pasa la muestra
//  por averageDCEstimator(int32_t*, uint16_t) y lowPassFIRFilter(int16_t), o
//  sea que la TRUNCA a 16 bits. Con el dedo puesto el MAX30102 entrega entre
//  60.000 y 250.000 cuentas, muy por encima de 65.535: la linea de base da la
//  vuelta y salen latidos falsos. Ademas cuenta la onda dicrota como un latido
//  mas. Con una PPG sintetica de 72 BPM aquel camino devolvia ~150 BPM.
//
//  Este detector trabaja en coma flotante sobre la muestra completa:
//    1) linea de base DC por media exponencial;
//    2) senal AC = DC - IR (la IR BAJA en la sistole) y filtro paso bajo;
//    3) envolvente con decaimiento -> umbral ADAPTATIVO;
//    4) disparo por cruce de umbral con histeresis + periodo refractario;
//    5) BPM = MEDIANA de los ultimos intervalos (robusta a un latido perdido).
static void beatReset(BeatDetector &b) { memset(&b, 0, sizeof(b)); b.armed = true; }

static bool beatUpdate(BeatDetector &b, uint32_t ir, uint32_t ahora) {
  const float x = (float)ir;
  if (!b.init) { b.dc = x; b.lp = 0.0f; b.env = 0.0f; b.init = true; b.armed = true; }

  b.dc += (x - b.dc) * BEAT_DC_ALPHA;
  const float ac = b.dc - x;                    // positiva durante la sistole
  b.lp += (ac - b.lp) * BEAT_LP_ALPHA;

  b.env *= BEAT_ENV_DECAY;
  if (b.lp > b.env) b.env = b.lp;

  if (b.warmup < BEAT_WARMUP_SAMPLES) { b.warmup++; return false; }
  if (b.env < BEAT_MIN_AMPLITUDE) { b.armed = true; return false; }

  const float thAlto = b.env * BEAT_TH_HIGH;
  const float thBajo = b.env * BEAT_TH_LOW;

  if (!b.armed) { if (b.lp < thBajo) b.armed = true; return false; }
  if (b.lp < thAlto) return false;
  if (b.ultimoMs != 0 && (ahora - b.ultimoMs) < BEAT_REFRACTARIO_MS) return false;

  if (b.ultimoMs != 0) {
    const uint32_t d = ahora - b.ultimoMs;
    if (d >= BEAT_REFRACTARIO_MS && d <= BEAT_MAX_INTERVAL_MS) {
      b.intervalos[b.idx] = d;
      b.idx = (b.idx + 1) % BEAT_RING;
      if (b.n < BEAT_RING) b.n++;
    } else {
      b.n = 0; b.idx = 0;                       // intervalo imposible: se reinicia
    }
  }
  b.ultimoMs = ahora;
  b.armed = false;
  return true;
}

static float beatBPM(const BeatDetector &b) {
  if (b.n < BEAT_MIN_INTERVALS) return 0.0f;
  uint32_t v[BEAT_RING];
  memcpy(v, b.intervalos, b.n * sizeof(uint32_t));
  for (uint8_t i = 1; i < b.n; i++) {
    const uint32_t k = v[i];
    int8_t j = (int8_t)i - 1;
    while (j >= 0 && v[j] > k) { v[j + 1] = v[j]; j--; }
    v[j + 1] = k;
  }
  const uint32_t med = (b.n & 1) ? v[b.n / 2] : (v[b.n / 2 - 1] + v[b.n / 2]) / 2;
  return med ? (60000.0f / (float)med) : 0.0f;
}

// ---------------------------------------------------------------------
// 7.2 Adquisicion
// ---------------------------------------------------------------------
struct PpgRT {
  uint32_t ir[SPO2_BUF_LEN], red[SPO2_BUF_LEN];
  int16_t  llenado;
  uint8_t  decim;
  bool     dedoRaw, dedoEstable;
  uint32_t dedoCambio, inicio, ultimoDedo;
  BeatDetector latido;
  float    okBPM[PPG_MAX_OK], okSpO2[PPG_MAX_OK];
  uint8_t  okN;
} pg;

static float mediaRecortada(const float *src, uint8_t n) {
  if (n == 0) return 0.0f;
  if (n > 32) n = 32;
  float v[32];
  memcpy(v, src, n * sizeof(float));
  for (uint8_t i = 1; i < n; i++) {
    float k = v[i]; int8_t j = (int8_t)i - 1;
    while (j >= 0 && v[j] > k) { v[j + 1] = v[j]; j--; }
    v[j + 1] = k;
  }
  float s = 0.0f;
  if (n <= 3) { for (uint8_t i = 0; i < n; i++) s += v[i]; return s / n; }
  for (uint8_t i = 1; i < n - 1; i++) s += v[i];
  return s / (float)(n - 2);
}

static float indicePerfusion(const uint32_t *b, int16_t n) {
  if (n < 8) return 0.0f;
  uint32_t mn = b[0], mx = b[0];
  double suma = 0;
  for (int16_t i = 0; i < n; i++) {
    if (b[i] < mn) mn = b[i];
    if (b[i] > mx) mx = b[i];
    suma += b[i];
  }
  const double dc = suma / n;
  return (dc <= 0) ? 0.0f : (float)(((double)(mx - mn) / dc) * 100.0);
}

static void ppgLimpiarBuffers() {
  pg.llenado = 0; pg.decim = 0; pg.okN = 0;
  beatReset(pg.latido);
}

static void ppgReiniciar(uint32_t ahora) {
  ppgLimpiarBuffers();
  pg.dedoRaw = false; pg.dedoEstable = false;
  pg.dedoCambio = ahora; pg.inicio = ahora; pg.ultimoDedo = ahora;
}

static void ppgMuestra(uint32_t ir, uint32_t red, uint32_t ahora) {
  // --- deteccion de dedo con histeresis y tiempo de estabilidad ---
  bool raw = pg.dedoRaw;
  if (!raw && ir > FINGER_IR_ON)  raw = true;
  if ( raw && ir < FINGER_IR_OFF) raw = false;
  if (raw != pg.dedoRaw) { pg.dedoRaw = raw; pg.dedoCambio = ahora; }

  if (pg.dedoRaw != pg.dedoEstable && (ahora - pg.dedoCambio) >= FINGER_STABLE_MS) {
    pg.dedoEstable = pg.dedoRaw;
    if (!pg.dedoEstable) ppgLimpiarBuffers();
    portENTER_CRITICAL(&g_mux);
    g_sh.dedo = pg.dedoEstable;
    if (!pg.dedoEstable) {
      g_sh.senalOk = false; g_sh.liveBPM = 0; g_sh.liveSpO2 = 0;
      g_sh.spo2Valido = false; g_sh.perfusion = 0; g_sh.ppgProgreso = 0;
    }
    portEXIT_CRITICAL(&g_mux);
  }
  if (!pg.dedoEstable) return;
  pg.ultimoDedo = ahora;

  // --- latido (detector propio, bloque 7.1) ---
  if (beatUpdate(pg.latido, ir, ahora)) {
    portENTER_CRITICAL(&g_mux);
    g_sh.ultimoLatido = ahora;
    portEXIT_CRITICAL(&g_mux);
  }

  // --- diezmado a 25 Hz para el algoritmo de Maxim ---
  if (++pg.decim < SPO2_DECIM) return;
  pg.decim = 0;

  if (pg.llenado < SPO2_BUF_LEN) {
    pg.ir[pg.llenado] = ir;
    pg.red[pg.llenado] = red;
    pg.llenado++;
    if (pg.llenado < SPO2_BUF_LEN) return;
  }

  int32_t spo2v = 0, hrv = 0;
  int8_t  spo2Val = 0, hrVal = 0;
  maxim_heart_rate_and_oxygen_saturation(pg.ir, SPO2_BUF_LEN, pg.red,
                                         &spo2v, &spo2Val, &hrv, &hrVal);
  const float pi = indicePerfusion(pg.ir, SPO2_BUF_LEN);

  // Pulso: mediana de los intervalos propios. Mientras no haya suficientes
  // latidos vale como respaldo el HR que devuelve el algoritmo de Maxim.
  float bpmMedia = beatBPM(pg.latido);
  if (bpmMedia < HR_MIN && hrVal == 1 && hrv >= HR_MIN && hrv <= HR_MAX) bpmMedia = (float)hrv;

  const int  spo2c   = (int)spo2v + SPO2_OFFSET;
  const bool spo2Ok  = (spo2Val == 1 && spo2c >= SPO2_MIN && spo2c <= SPO2_MAX);
  const bool hrOk    = (bpmMedia >= HR_MIN && bpmMedia <= HR_MAX);
  const bool piOk    = (pi >= MIN_PERFUSION);
  const bool fiable  = spo2Ok && hrOk && piOk;

  if (fiable && pg.okN < PPG_MAX_OK) {
    pg.okBPM[pg.okN]  = bpmMedia;
    pg.okSpO2[pg.okN] = (float)spo2c;
    pg.okN++;
  }

  portENTER_CRITICAL(&g_mux);
  g_sh.perfusion   = pi;
  g_sh.liveBPM     = hrOk ? (int)(bpmMedia + 0.5f) : 0;
  g_sh.liveSpO2    = spo2Ok ? spo2c : 0;
  g_sh.spo2Valido  = spo2Ok && piOk;
  g_sh.senalOk     = fiable;
  g_sh.ppgProgreso = (uint8_t)((pg.okN * 100UL) / PPG_TARGET);
  portEXIT_CRITICAL(&g_mux);

  if (pg.okN >= PPG_TARGET) {
    const int b = (int)(mediaRecortada(pg.okBPM,  pg.okN) + 0.5f);
    const int s = (int)(mediaRecortada(pg.okSpO2, pg.okN) + 0.5f);
    portENTER_CRITICAL(&g_mux);
    g_sh.finalBPM = b; g_sh.finalSpO2 = s;
    g_sh.ppgProgreso = 100; g_sh.ppgListo = true;
    portEXIT_CRITICAL(&g_mux);
    return;
  }

  for (int16_t i = SPO2_SHIFT; i < SPO2_BUF_LEN; i++) {
    pg.ir[i - SPO2_SHIFT]  = pg.ir[i];
    pg.red[i - SPO2_SHIFT] = pg.red[i];
  }
  pg.llenado = SPO2_BUF_LEN - SPO2_SHIFT;
}

static uint32_t ppgUltimaMuestraMs = 0;

static void ppgTrabajo(uint32_t ahora) {
  if (!hwMaxOk) {
    portENTER_CRITICAL(&g_mux); g_sh.ppgFallo = true; portEXIT_CRITICAL(&g_mux);
    return;
  }
  max3010x.check();                              // no bloqueante
  // Las muestras que esperan en el buffer se tomaron ANTES de "ahora", una
  // cada 1000/PPG_SPS ms. Fecharlas todas igual desplazaria los intervalos
  // entre latidos y con ellos el pulso; se reconstruye el instante de cada una.
  const uint8_t pendientes = max3010x.available();
  const uint32_t periodoMs = 1000UL / PPG_SPS;
  uint8_t idx = 0, guardia = 0;
  while (max3010x.available() && guardia++ < 32) {
    const uint32_t ir  = max3010x.getFIFOIR();
    const uint32_t red = max3010x.getFIFORed();
    max3010x.nextSample();
    const uint32_t atraso = (idx < pendientes) ? (pendientes - 1 - idx) * periodoMs : 0;
    idx++;
    ppgUltimaMuestraMs = ahora;
    ppgMuestra(ir, red, ahora - atraso);
    if (pg.okN >= PPG_TARGET) return;
  }

  // El sensor ha dejado de entregar muestras: se reinicia en vez de quedarse
  // esperando un dedo que nunca llega.
  if (ahora - ppgUltimaMuestraMs > PPG_STALL_MS) {
    ppgUltimaMuestraMs = ahora;
    if (sensorRecuperar()) ppgReiniciar(ahora);
    else { portENTER_CRITICAL(&g_mux); g_sh.ppgFallo = true; portEXIT_CRITICAL(&g_mux); }
    return;
  }

  const bool sinDedo = !pg.dedoEstable && (ahora - pg.ultimoDedo > NO_FINGER_TIMEOUT_MS);
  if (sinDedo || (ahora - pg.inicio > PPG_TIMEOUT_MS)) {
    portENTER_CRITICAL(&g_mux); g_sh.ppgFallo = true; portEXIT_CRITICAL(&g_mux);
  }
}

// =====================================================================
// 8. RED: WIFI, BUSQUEDA DE MEDIBOT Y LECTURA DEL JSON
// =====================================================================
struct NetRT {
  uint8_t   etapa = NET_OFF;
  uint16_t  host = 1;
  uint32_t  t0 = 0;
  uint32_t  ultimoJson = 0;
  IPAddress ip;
  uint16_t  puerto = MEDIBOT_PORT_MAIN;
  uint8_t   fallosJson = 0;
} nt;

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
  portENTER_CRITICAL(&g_mux);
  snprintf(g_sh.netMsg, sizeof(g_sh.netMsg), "%s", m);
  portEXIT_CRITICAL(&g_mux);
}

static void netEtapa(uint8_t e) {
  nt.etapa = e;
  portENTER_CRITICAL(&g_mux);
  g_sh.etapa = e;
  portEXIT_CRITICAL(&g_mux);
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
  portENTER_CRITICAL(&g_mux);
  g_sh.ip = (uint32_t)nt.ip;
  g_sh.puerto = nt.puerto;
  g_sh.netProgreso = 100;
  portEXIT_CRITICAL(&g_mux);
  Serial.printf("[RED] MEDIBOT en %s:%u\n", nt.ip.toString().c_str(), (unsigned)nt.puerto);
}

static void netLeerJson() {
  if (nt.puerto != MEDIBOT_PORT_MAIN) {          // el Pastillero no tiene /api/esp32
    portENTER_CRITICAL(&g_mux); g_sh.jsonOk = false; portEXIT_CRITICAL(&g_mux);
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
      portENTER_CRITICAL(&g_mux);
      g_sh.j_sistema     = doc["s"]  | 0;
      g_sh.j_detecciones = doc["d"]  | 0;
      g_sh.j_rojos       = doc["ro"] | 0;
      g_sh.j_fps1        = doc["f1"] | 0;
      g_sh.j_fps2        = doc["f2"] | 0;
      g_sh.j_fx          = doc["fx"] | 0;
      g_sh.j_fy          = doc["fy"] | 0;
      g_sh.j_grabando    = ((doc["r"] | 0) != 0);
      g_sh.jsonOk = true;
      g_sh.jsonMs = millis();
      portEXIT_CRITICAL(&g_mux);
      ok = true;
    }
  }
  http.end();
  if (ok) {
    nt.fallosJson = 0;
  } else {
    portENTER_CRITICAL(&g_mux); g_sh.jsonOk = false; portEXIT_CRITICAL(&g_mux);
    // Cinco fallos seguidos: probablemente MEDIBOT cambio de IP -> buscar otra vez
    if (++nt.fallosJson >= 5) {
      nt.fallosJson = 0;
      netMsg("Buscando MEDIBOT");
      netEtapa(NET_MDNS);
      portENTER_CRITICAL(&g_mux); g_sh.ip = 0; portEXIT_CRITICAL(&g_mux);
    }
  }
}

static void netTrabajo(uint32_t ahora) {
  switch (nt.etapa) {
    case NET_WIFI:
      if (WiFi.status() == WL_CONNECTED) {
        portENTER_CRITICAL(&g_mux); g_sh.rssi = (int8_t)WiFi.RSSI(); portEXIT_CRITICAL(&g_mux);
        configTzTime(TZ_INFO, NTP_SERVER);       // hora real para el historial
        netMsg("Buscando MEDIBOT");
        netEtapa(NET_MDNS);
        nt.t0 = ahora;
      } else if (ahora - nt.t0 > WIFI_TIMEOUT_MS) {
        netMsg("Sin WiFi");
        netEtapa(NET_FAIL);
      }
      break;

    case NET_MDNS: {
      if (!horaOk && (uint32_t)time(nullptr) > 1600000000UL) horaOk = true;
      static bool mdnsListo = false;
      if (!mdnsListo) mdnsListo = MDNS.begin("medibot-panel");
      const int n = MDNS.queryService(MEDIBOT_MDNS_SVC, "tcp");
      for (int i = 0; i < n; i++) {
        if (netProbarIP(mdnsDireccion(i))) { netEncontrado(); return; }
      }
      const IPAddress h = MDNS.queryHost(MEDIBOT_MDNS_HOST);
      if ((uint32_t)h != 0 && netProbarIP(h)) { netEncontrado(); return; }
      netMsg("Explorando la red");
      nt.host = 1;
      netEtapa(NET_SWEEP);
      break;
    }

    case NET_SWEEP: {
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
      portENTER_CRITICAL(&g_mux);
      g_sh.netProgreso = (uint8_t)((nt.host * 100UL) / 254UL);
      portEXIT_CRITICAL(&g_mux);
      if (nt.host > 254) { netMsg("No se encontro"); netEtapa(NET_FAIL); }
      break;
    }

    case NET_FOUND:
      if (WiFi.status() != WL_CONNECTED) { netMsg("WiFi caido"); netEtapa(NET_FAIL); break; }
      if (ahora - nt.ultimoJson >= JSON_POLL_MS) {
        nt.ultimoJson = ahora;
        netLeerJson();
        portENTER_CRITICAL(&g_mux); g_sh.rssi = (int8_t)WiFi.RSSI(); portEXIT_CRITICAL(&g_mux);
      }
      break;

    default:
      break;
  }
}

static void netArrancar(uint32_t ahora) {
  if (nt.etapa == NET_FOUND) return;             // ya localizado: solo refrescar
  nt.t0 = ahora;
  nt.host = 1;
  portENTER_CRITICAL(&g_mux);
  g_sh.netProgreso = 0; g_sh.ip = 0; g_sh.jsonOk = false;
  portEXIT_CRITICAL(&g_mux);
  if (WiFi.status() == WL_CONNECTED) {
    netMsg("Buscando MEDIBOT");
    netEtapa(NET_MDNS);
  } else {
    WiFi.mode(WIFI_STA);
    WiFi.setSleep(true);
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    netMsg("Conectando al WiFi");
    netEtapa(NET_WIFI);
  }
}

// =====================================================================
// 9. TAREA DEL NUCLEO 0 (sensor y red, nunca a la vez)
// =====================================================================
void tareaTrabajo(void *pv) {
  (void)pv;
  uint32_t miEpoca = 0xFFFFFFFF;
  WorkMode miModo = WK_IDLE;
  uint32_t ultimoReintentoSensor = 0;
  uint32_t ultimoAvisoPila = 0;

  for (;;) {
    const uint32_t ahora = millis();

    // Si la pila libre baja de 1 KB, falta poco para un reinicio seco
    if (ahora - ultimoAvisoPila > 5000) {
      ultimoAvisoPila = ahora;
      const uint32_t libre = uxTaskGetStackHighWaterMark(NULL) * sizeof(StackType_t);
      if (libre < 1024) Serial.printf("[AVISO] Pila de la tarea al limite: %u bytes\n",
                                      (unsigned)libre);
    }

    if (g_epoca != miEpoca) {
      miEpoca = g_epoca;
      miModo  = g_modo;
      if (miModo == WK_PPG) {
        ppgReiniciar(ahora);
        ppgUltimaMuestraMs = ahora;
        if (hwMaxOk) {
          // Encender los LED y comprobar que la escritura ha entrado: si no,
          // se reconfigura el sensor entero. Sin esto, una escritura perdida
          // deja el sensor a oscuras toda la medida.
          max3010x.setPulseAmplitudeRed(MAX_LED_BRIGHTNESS);
          max3010x.setPulseAmplitudeIR(MAX_LED_BRIGHTNESS);
          uint8_t l1 = 0, l2 = 0;
          const bool encendidos = regLeer(MAX_I2C_ADDR, 0x0C, l1) &&
                                  regLeer(MAX_I2C_ADDR, 0x0D, l2) &&
                                  l1 == MAX_LED_BRIGHTNESS && l2 == MAX_LED_BRIGHTNESS;
          if (!encendidos) sensorRecuperar();
          else             max3010x.clearFIFO();
        }
      } else {
#if !MAX_LEDS_ALWAYS_ON
        if (hwMaxOk) {                            // LED apagados fuera de medida
          max3010x.setPulseAmplitudeRed(0x00);
          max3010x.setPulseAmplitudeIR(0x00);
        }
#endif
        if (miModo == WK_NET) netArrancar(ahora);
        else netEtapa(nt.etapa == NET_FOUND ? NET_FOUND : NET_OFF);
      }
      // Desde aqui, lo que se publique pertenece a esta epoca.
      portENTER_CRITICAL(&g_mux);
      g_sh.epoca = miEpoca;
      portEXIT_CRITICAL(&g_mux);
    }

    // Sin sensor se reintenta la deteccion en segundo plano: si estaba mal
    // conectado o tardo en arrancar, el equipo se recupera sin reiniciarlo.
    if (!hwMaxOk && ahora - ultimoReintentoSensor >= SENSOR_RETRY_MS) {
      ultimoReintentoSensor = ahora;
      if (sensorArrancar(true)) {
        Serial.println(F("[MAX] Sensor recuperado: ya se puede medir"));
        ppgReiniciar(ahora);
        ppgUltimaMuestraMs = ahora;
      }
    }

    switch (miModo) {
      case WK_PPG:  ppgTrabajo(ahora);  vTaskDelay(2  / portTICK_PERIOD_MS); break;
      case WK_NET:  netTrabajo(ahora);  vTaskDelay(20 / portTICK_PERIOD_MS); break;
      default:                          vTaskDelay(50 / portTICK_PERIOD_MS); break;
    }
  }
}

// =====================================================================
// 10. CODIGO QR
// =====================================================================
bool qrGenerar(const char *txt) {
  if (qrListo && strcmp(txt, qrTexto) == 0) return true;
  if (qrcode_getBufferSize(QR_VERSION) > (int)sizeof(qrDatos)) return false;
  if (qrcode_initText(&qrCodigo, qrDatos, QR_VERSION, ECC_LOW, txt) != 0) {
    qrListo = false;
    return false;                                 // no cabe: acorta la URL
  }
  snprintf(qrTexto, sizeof(qrTexto), "%s", txt);
  qrListo = true;
  return true;
}

void dibujarQR(int x, int y) {
  if (!qrListo) return;
  const uint8_t px = QR_PIXELS_POR_MODULO;
  const int lado = (qrCodigo.size + QR_QUIET * 2) * px;
  //  En paneles negativos (azules de pixel blanco) hay que invertirlo: los
  //  modulos oscuros del QR deben ser pixeles APAGADOS.
  u8g2.setDrawColor(QR_INVERTIDO ? 1 : 0);
  u8g2.drawBox(x, y, lado, lado);                 // zona de silencio, clara
  u8g2.setDrawColor(QR_INVERTIDO ? 0 : 1);
  for (uint8_t my = 0; my < qrCodigo.size; my++)
    for (uint8_t mx = 0; mx < qrCodigo.size; mx++)
      if (qrcode_getModule(&qrCodigo, mx, my))
        u8g2.drawBox(x + (QR_QUIET + mx) * px, y + (QR_QUIET + my) * px, px, px);
  u8g2.setDrawColor(1);
}

// =====================================================================
// 11. DIBUJO: PRIMITIVAS, LOGO Y CARA
// =====================================================================
void irA(AppState s) { estado = s; estadoDesde = millis(); frame = 0; repintar = true; }
static inline uint32_t enEstado() { return millis() - estadoDesde; }

static bool pantallaAnimada() {
  return !(estado == ST_ABOUT || estado == ST_HIST ||
           estado == ST_CHK_RESULT || estado == ST_NET_QR);
}

static void txtCentrado(int y, const char *t) {
  u8g2.drawStr((128 - u8g2.getStrWidth(t)) / 2, y, t);
}

static void barra(int x, int y, int w, int h, uint8_t pct) {
  if (pct > 100) pct = 100;
  u8g2.drawRFrame(x, y, w, h, 2);
  const int dentro = ((w - 4) * pct) / 100;
  if (dentro > 0) u8g2.drawBox(x + 2, y + 2, dentro, h - 4);
}

static void ruleta(int cx, int cy, int r, int f) {
  const int activo = (f / 2) % 8;
  for (int i = 0; i < 8; i++) {
    const float a = i * (PI / 4.0f);
    const int px = cx + (int)(cosf(a) * r), py = cy + (int)(sinf(a) * r);
    if (i == activo)                u8g2.drawDisc(px, py, 2);
    else if (i == (activo + 7) % 8) u8g2.drawDisc(px, py, 1);
    else                            u8g2.drawPixel(px, py);
  }
}

static void corazon(int cx, int cy, int r) {
  if (r < 3) r = 3;
  u8g2.drawDisc(cx - r / 2, cy - r / 3, r / 2);
  u8g2.drawDisc(cx + r / 2, cy - r / 3, r / 2);
  u8g2.drawTriangle(cx - r, cy - r / 3, cx + r, cy - r / 3, cx, cy + r);
}

// Rectangulo girado un angulo cualquiera (dos triangulos rellenos)
static void rectGirado(float cx, float cy, float w, float h, float ang) {
  const float c = cosf(ang), s = sinf(ang), hx = w / 2, hy = h / 2;
  const float px[4] = { -hx,  hx, hx, -hx };
  const float py[4] = { -hy, -hy, hy,  hy };
  int16_t X[4], Y[4];
  for (uint8_t i = 0; i < 4; i++) {
    X[i] = (int16_t)(cx + px[i] * c - py[i] * s);
    Y[i] = (int16_t)(cy + px[i] * s + py[i] * c);
  }
  u8g2.drawTriangle(X[0], Y[0], X[1], Y[1], X[2], Y[2]);
  u8g2.drawTriangle(X[0], Y[0], X[2], Y[2], X[3], Y[3]);
}

// Logo: cruz medica girando dentro de un anillo, con un satelite orbitando.
// El angulo es continuo (no fotogramas sueltos), asi que el giro es suave.
static void dibujarLogo(int cx, int cy, float ang, float esc) {
  const int R = (int)(24 * esc);
  u8g2.drawCircle(cx, cy, R);
  u8g2.drawCircle(cx, cy, R - 1);
  rectGirado(cx, cy, 28 * esc, 9 * esc, ang);
  rectGirado(cx, cy,  9 * esc, 28 * esc, ang);
  const float ao = ang * 1.7f;
  u8g2.drawDisc(cx + (int)(cosf(ao) * (R + 4)), cy + (int)(sinf(ao) * (R + 4)), 2);
}

// Cara del robot (se conserva el diseno original)
static void dibujarCara(Emotion emo, int f, int cx, int cy, float s) {
  if (emo == EMO_NORMAL || emo == EMO_HAPPY) cy += (int)(sinf(millis() / 300.0f) * (3.0f * s));

  const int eW = max(1, (int)(22 * s)), eH = max(1, (int)(26 * s));
  const int lx = cx - (int)(23 * s) - eW / 2, rx = cx + (int)(23 * s) - eW / 2;
  const int ey = cy - (int)(14 * s), mx = cx, my = cy + (int)(18 * s);
  const int rad = max(1, min((int)(5 * s), min(eW, eH) / 2));
  const int ebY = cy - (int)(20 * s);

  u8g2.setDrawColor(1);
  if (emo == EMO_SAD) {
    u8g2.drawLine(lx, ebY, lx + eW, ebY + (int)(4 * s));
    u8g2.drawLine(rx, ebY + (int)(4 * s), rx + eW, ebY);
  } else if (emo == EMO_HAPPY) {
    u8g2.drawBox(lx + (int)(2 * s), ebY - (int)(3 * s), eW - (int)(4 * s), max(1, (int)(3 * s)));
    u8g2.drawBox(rx + (int)(2 * s), ebY - (int)(3 * s), eW - (int)(4 * s), max(1, (int)(3 * s)));
  } else {
    u8g2.drawBox(lx + (int)(2 * s), ebY, eW - (int)(4 * s), max(1, (int)(2 * s)));
    u8g2.drawBox(rx + (int)(2 * s), ebY, eW - (int)(4 * s), max(1, (int)(2 * s)));
  }

  if (parpadeo) {
    u8g2.drawRBox(lx, ey + eH / 2 - (int)(3 * s), eW, max(1, (int)(6 * s)), max(1, (int)(2 * s)));
    u8g2.drawRBox(rx, ey + eH / 2 - (int)(3 * s), eW, max(1, (int)(6 * s)), max(1, (int)(2 * s)));
  } else {
    u8g2.drawRBox(lx, ey, eW, eH, rad);
    u8g2.drawRBox(rx, ey, eW, eH, rad);
    u8g2.setDrawColor(0);
    if (emo == EMO_HAPPY) {
      u8g2.drawBox(lx - 1, ey + eH / 2, eW + 2, eH / 2 + 2);
      u8g2.drawBox(rx - 1, ey + eH / 2, eW + 2, eH / 2 + 2);
    }
    const int pW = max(1, (int)(8 * s));
    int pxOff = (eW - pW) / 2, pyOff = (eH - pW) / 2, lookX = 0;
    if (emo == EMO_NORMAL) {
      const int ciclo = (f / 20) % 10;
      if (ciclo == 1) lookX = -(int)(3 * s); else if (ciclo == 5) lookX = (int)(3 * s);
    }
    if (emo == EMO_DOWN)    pyOff = eH - pW - (int)(2 * s);
    else if (emo == EMO_UP) pyOff = (int)(2 * s);

    if (emo == EMO_LOAD) {
      const int off = (int)(((f % 20) - 10) * s);
      u8g2.drawBox(lx + pxOff, ey + pyOff + off, pW, pW);
      u8g2.drawBox(rx + pxOff, ey + pyOff - off, pW, pW);
    } else if (emo != EMO_HAPPY) {
      u8g2.drawBox(lx + pxOff + lookX, ey + pyOff, pW, pW);
      u8g2.drawBox(rx + pxOff + lookX, ey + pyOff, pW, pW);
    }
  }

  u8g2.setDrawColor(1);
  const int mR = max(1, (int)(6 * s));
  if (emo == EMO_HAPPY) {
    u8g2.drawDisc(mx, my, mR);
    u8g2.setDrawColor(0);
    u8g2.drawBox(mx - mR - 1, my - mR - 1, (mR * 2) + 2, mR + 2);
  } else if (emo == EMO_SAD) {
    u8g2.drawDisc(mx, my + (int)(3 * s), mR);
    u8g2.setDrawColor(0);
    u8g2.drawBox(mx - mR - 1, my + (int)(3 * s), (mR * 2) + 2, mR + 1);
  } else if (emo == EMO_DOWN || emo == EMO_UP || emo == EMO_LOAD) {
    u8g2.drawCircle(mx, my + (int)(2 * s), max(1, (int)(3 * s)));
  } else {
    u8g2.drawDisc(mx, my, max(1, (int)(5 * s)));
    u8g2.setDrawColor(0);
    u8g2.drawBox(mx - (int)(6 * s), my - (int)(6 * s), (int)(12 * s), (int)(7 * s));
  }
  u8g2.setDrawColor(1);
}

// =====================================================================
// 12. PANTALLAS
// =====================================================================
const char *MENU_ITEMS[] = {
  "Auto-Chequeo", "Conectar MEDIBOT", "Historial", "Calibrar teclado", "Sobre Medibot"
};
const int MENU_N = sizeof(MENU_ITEMS) / sizeof(MENU_ITEMS[0]);
#define MENU_VISIBLES 4

static void pantSplash() {
  const float ang = frame * 0.16f;   // ~2 vueltas durante el arranque
  dibujarLogo(64, 27, ang, 1.0f);
  if (enEstado() > 1300) {
    u8g2.setFont(u8g2_font_helvB08_tr);
    txtCentrado(60, "M E D I B O T");
  }
}

static void pantMenu() {
  u8g2.setFont(u8g2_font_helvB08_tr);
  txtCentrado(10, "MEDIBOT");
  u8g2.drawHLine(0, 12, 128);

  if (menuSel < menuTop) menuTop = menuSel;
  if (menuSel >= menuTop + MENU_VISIBLES) menuTop = menuSel - MENU_VISIBLES + 1;

  u8g2.setFont(u8g2_font_6x10_tr);
  for (int i = 0; i < MENU_VISIBLES && (menuTop + i) < MENU_N; i++) {
    const int idx = menuTop + i;
    const int y = 14 + i * 12;
    if (idx == menuSel) {
      u8g2.drawRBox(8, y, 112, 12, 2);
      u8g2.setDrawColor(0);
      txtCentrado(y + 9, MENU_ITEMS[idx]);
      u8g2.setDrawColor(1);
    } else {
      u8g2.drawRFrame(8, y, 112, 12, 2);
      txtCentrado(y + 9, MENU_ITEMS[idx]);
    }
  }
  if (menuTop > 0)                        u8g2.drawTriangle(124, 18, 120, 22, 128 - 4, 22);
  if (menuTop + MENU_VISIBLES < MENU_N)   u8g2.drawTriangle(124, 61, 120, 57, 128 - 4, 57);
}

static void pantChequeoLeer(const Shared &v) {
  if (!v.dedo) {
    dibujarCara(EMO_DOWN, frame, 46, 20, 0.55f);
    // dedo animado
    const int off = ((frame / 5) % 2) ? 0 : 2;
    u8g2.drawRBox(102, 12 + off, 9, 15, 4);
    u8g2.drawHLine(96, 32, 21);
    u8g2.drawHLine(96, 33, 21);
    u8g2.setFont(u8g2_font_6x10_tr);
    txtCentrado(50, "Coloque el dedo");
    u8g2.setFont(u8g2_font_4x6_tr);
    txtCentrado(61, "[ATRAS] Cancelar");
  } else {
    dibujarCara(EMO_LOAD, frame, 38, 18, 0.5f);
    const uint32_t desde = millis() - v.ultimoLatido;
    corazon(102, 18, (v.ultimoLatido && desde < 180) ? 9 : 6);
    u8g2.setFont(u8g2_font_6x10_tr);
    char b[20];
    if (v.liveBPM > 0) snprintf(b, sizeof(b), "BPM %d", v.liveBPM);
    else               snprintf(b, sizeof(b), "BPM --");
    u8g2.drawStr(4, 44, b);
    if (v.spo2Valido) snprintf(b, sizeof(b), "SpO2 %d%%", v.liveSpO2);
    else              snprintf(b, sizeof(b), "SpO2 --");
    u8g2.drawStr(62, 44, b);
    barra(4, 48, 120, 9, v.ppgProgreso);
    u8g2.setFont(u8g2_font_4x6_tr);
    txtCentrado(63, v.ppgProgreso == 0 ? "Estabilizando senal..." : "Midiendo, no se mueva");
  }
}

static void pantResultado() {
  u8g2.setFont(u8g2_font_helvB08_tr);
  char b[36];
  if (resPag == 0) {
    txtCentrado(10, "TUS RESULTADOS");
    u8g2.drawHLine(0, 12, 128);
    u8g2.setFont(u8g2_font_6x10_tr);
    snprintf(b, sizeof(b), "Latidos: %d x min", pacienteBPM);
    u8g2.drawStr(6, 30, b);
    snprintf(b, sizeof(b), "Oxigeno: %d %%", pacienteSpO2);
    u8g2.drawStr(6, 46, b);
    u8g2.setFont(u8g2_font_4x6_tr);
    txtCentrado(62, "[ARR/ABA] Detalle  [OK] Salir");
  } else {
    txtCentrado(10, "INTERPRETACION");
    u8g2.drawHLine(0, 12, 128);
    u8g2.setFont(u8g2_font_5x7_tr);
    u8g2.drawStr(2, 26, diag1);
    u8g2.drawStr(2, 40, diag2);
    u8g2.setFont(u8g2_font_4x6_tr);
    txtCentrado(54, "Orientativo, no es diagnostico");
    txtCentrado(62, "[ARR/ABA] Valores  [OK] Salir");
  }
}

static void pantRed(const Shared &v) {
  u8g2.setFont(u8g2_font_helvB08_tr);
  txtCentrado(10, "CONECTAR MEDIBOT");
  u8g2.drawHLine(0, 12, 128);
  ruleta(64, 32, 11, frame);
  u8g2.setFont(u8g2_font_6x10_tr);
  txtCentrado(52, v.netMsg[0] ? v.netMsg : "Iniciando...");
  if (v.etapa == NET_SWEEP) barra(4, 55, 120, 8, v.netProgreso);
  else { u8g2.setFont(u8g2_font_4x6_tr); txtCentrado(62, "[ATRAS] Cancelar"); }
}

static void pantQR(const Shared &v) {
  const IPAddress ip(v.ip);
  dibujarQR(1, 3);
  u8g2.setFont(u8g2_font_5x7_tr);
  u8g2.drawStr(64, 14, "Escanea para");
  u8g2.drawStr(64, 24, "abrir MEDIBOT");
  u8g2.setFont(u8g2_font_4x6_tr);
  u8g2.drawStr(64, 38, ip.toString().c_str());
  char b[20];
  snprintf(b, sizeof(b), "puerto %u", (unsigned)v.puerto);
  u8g2.drawStr(64, 46, b);
  snprintf(b, sizeof(b), "WiFi %d dBm", v.rssi);
  u8g2.drawStr(64, 54, b);
  u8g2.drawStr(64, 62, "[ABAJO] Datos");
}

static void pantDatos(const Shared &v) {
  u8g2.setFont(u8g2_font_helvB08_tr);
  txtCentrado(10, "MEDIBOT EN VIVO");
  u8g2.drawHLine(0, 12, 128);
  u8g2.setFont(u8g2_font_5x7_tr);
  if (!v.jsonOk) {
    txtCentrado(32, v.netMsg[0] ? v.netMsg : "Sin datos");
    txtCentrado(44, "Reintentando...");
  } else {
    char b[28];
    snprintf(b, sizeof(b), "Sistema: %s", v.j_sistema ? "ACTIVO" : "PARADO");
    u8g2.drawStr(4, 24, b);
    snprintf(b, sizeof(b), "Detecciones: %d", v.j_detecciones);
    u8g2.drawStr(4, 34, b);
    snprintf(b, sizeof(b), "Obj. rojos: %d", v.j_rojos);
    u8g2.drawStr(4, 44, b);
    snprintf(b, sizeof(b), "FPS: %d / %d", v.j_fps1, v.j_fps2);
    u8g2.drawStr(4, 54, b);
    if (v.j_grabando) { u8g2.drawDisc(118, 51, 3); u8g2.drawStr(100, 54, "REC"); }
  }
  u8g2.setFont(u8g2_font_4x6_tr);
  u8g2.drawStr(30, 62, "[ARRIBA] QR  [ATRAS] Salir");
}

static void pantHistorial() {
  u8g2.setFont(u8g2_font_helvB08_tr);
  char b[32];
  snprintf(b, sizeof(b), "HISTORIAL %d/%d", histPag + 1, histN ? histN : 1);
  txtCentrado(10, b);
  u8g2.drawHLine(0, 12, 128);
  u8g2.setFont(u8g2_font_6x10_tr);
  if (histN == 0 || !historial[histPag].usado) {
    txtCentrado(38, "Sin lecturas aun");
  } else {
    const Lectura &r = historial[histPag];
    snprintf(b, sizeof(b), "Latidos: %u x min", (unsigned)r.bpm);
    u8g2.drawStr(4, 28, b);
    snprintf(b, sizeof(b), "Oxigeno: %u %%", (unsigned)r.spo2);
    u8g2.drawStr(4, 42, b);
    u8g2.setFont(u8g2_font_5x7_tr);
    if (r.epoch > 1600000000UL) {
      const time_t t = (time_t)r.epoch;
      struct tm tmv;
      localtime_r(&t, &tmv);
      strftime(b, sizeof(b), "%d/%m/%Y  %H:%M", &tmv);
    } else {
      snprintf(b, sizeof(b), "Lectura %d (sin hora)", histPag + 1);
    }
    u8g2.drawStr(4, 54, b);
  }
  u8g2.setFont(u8g2_font_4x6_tr);
  u8g2.drawStr(16, 62, "[ARR/ABA] Ver   [ATRAS] Salir");
}

static void pantAbout() {
  u8g2.setFont(u8g2_font_helvB08_tr);
  txtCentrado(10, "INFO MEDIBOT");
  u8g2.drawHLine(0, 12, 128);
  u8g2.setFont(u8g2_font_5x7_tr);
  if (aboutPag == 0) {
    u8g2.drawStr(2, 24, "Panel de signos vitales");
    u8g2.drawStr(2, 34, "con sensor MAX30102 y");
    u8g2.drawStr(2, 44, "enlace con el robot");
    u8g2.drawStr(2, 54, "MEDIBOT por WiFi.");
    u8g2.drawTriangle(120, 50, 126, 50, 123, 54);
  } else {
    u8g2.drawStr(2, 24, "Uso educativo. No es");
    u8g2.drawStr(2, 34, "un producto sanitario");
    u8g2.drawStr(2, 44, "y no sustituye a un");
    u8g2.drawStr(2, 54, "medico ni a un pulsi-");
    u8g2.drawStr(2, 62, "oximetro certificado.");
    u8g2.drawTriangle(120, 26, 126, 26, 123, 22);
  }
}

static void pantCalibracion() {
  u8g2.setFont(u8g2_font_helvB08_tr);
  txtCentrado(10, "CALIBRAR TECLADO");
  u8g2.drawHLine(0, 12, 128);

  char b[34];
  u8g2.setFont(u8g2_font_6x10_tr);
  switch (wiz.fase) {
    case 0:
      if (wiz.esperandoSoltar) {
        txtCentrado(30, "Suelta los botones");
        txtCentrado(42, "y espera...");
      } else {
        txtCentrado(28, "No toques nada");
        txtCentrado(40, "midiendo reposo...");
        barra(14, 46, 100, 8,
              (uint8_t)((millis() - wiz.estableDesde) * 100 / WIZ_REPOSO_MS));
      }
      break;
    case 1: {
      txtCentrado(26, "Pulsa y manten:");
      u8g2.setFont(u8g2_font_helvB08_tr);
      txtCentrado(40, BTN_NOMBRE[BTN_ORDEN[wiz.paso]]);
      u8g2.setFont(u8g2_font_4x6_tr);
      const uint32_t resta = (millis() - wiz.t0 < WIZ_SALTO_MS)
                             ? (WIZ_SALTO_MS - (millis() - wiz.t0)) / 1000 : 0;
      snprintf(b, sizeof(b), "%u/%u  se omite en %lus",
               (unsigned)(wiz.paso + 1), (unsigned)BTN_ORDEN_N, (unsigned long)resta);
      txtCentrado(50, b);
      break;
    }
    case 2:
      txtCentrado(34, "Suelta el boton");
      break;
    case 3:
      txtCentrado(34, "Calculando...");
      break;
    default:
      snprintf(b, sizeof(b), "%u de %u botones OK", (unsigned)wiz.capturados, (unsigned)BTN_ORDEN_N);
      txtCentrado(28, b);
      if (wiz.guardado)      txtCentrado(40, "Guardado en memoria");
      else if (wiz.aviso[0]) txtCentrado(40, wiz.aviso);
      else                   txtCentrado(40, "Sin guardar");
      break;
  }
  // Lectura en vivo siempre visible: si algo va mal, se ve aqui
  u8g2.setFont(u8g2_font_4x6_tr);
  snprintf(b, sizeof(b), "ADC %u  %d mV  rep %d", (unsigned)kb.cuentas, (int)kb.mv, (int)kb.reposoMv);
  txtCentrado(62, b);
}

static void pantErrorRed(const Shared &v) {
  dibujarCara(EMO_SAD, frame, 64, 20, 0.6f);
  u8g2.setFont(u8g2_font_6x10_tr);
  txtCentrado(50, v.netMsg[0] ? v.netMsg : "Sin conexion");
  u8g2.setFont(u8g2_font_4x6_tr);
  txtCentrado(61, "[OK] Reintentar  [ATRAS] Salir");
}

void pintar() {
  const Shared v = shGet();
  u8g2.clearBuffer();
  switch (estado) {
    case ST_SPLASH:  pantSplash(); break;
    case ST_IDLE:    dibujarCara(emocion, frame, 64, 32, 1.0f); break;
    case ST_MENU:    pantMenu(); break;
    case ST_CHK_REQ:
      dibujarCara(EMO_DOWN, frame, 64, 20, 0.65f);
      u8g2.setFont(u8g2_font_6x10_tr);
      txtCentrado(50, "Coloque su dedo");
      txtCentrado(62, "sobre el sensor");
      break;
    case ST_CHK_READ:   pantChequeoLeer(v); break;
    case ST_CHK_RESULT: pantResultado(); break;
    case ST_NET_WIFI:
    case ST_NET_SEARCH: pantRed(v); break;
    case ST_NET_QR:     pantQR(v); break;
    case ST_NET_DATA:   pantDatos(v); break;
    case ST_NET_ERROR:  pantErrorRed(v); break;
    case ST_HIST:       pantHistorial(); break;
    case ST_ABOUT:      pantAbout(); break;
    case ST_CAL:        pantCalibracion(); break;
    default:
      dibujarCara(EMO_SAD, frame, 64, 20, 0.6f);
      u8g2.setFont(u8g2_font_6x10_tr);
      txtCentrado(54, errMsg[0] ? errMsg : "Error");
      break;
  }
  u8g2.sendBuffer();
  repintar = false;
}

// =====================================================================
// 13. INTERPRETACION DE RESULTADOS
// =====================================================================
static void evaluar() {
  if (pacienteBPM > 100)     snprintf(diag1, sizeof(diag1), "1. Pulso acelerado (%d)", pacienteBPM);
  else if (pacienteBPM < 60) snprintf(diag1, sizeof(diag1), "1. Pulso lento (%d)", pacienteBPM);
  else                       snprintf(diag1, sizeof(diag1), "1. Pulso normal (%d)", pacienteBPM);

  if (pacienteSpO2 < 92)       snprintf(diag2, sizeof(diag2), "2. Oxigeno bajo: alerta");
  else if (pacienteSpO2 <= 94) snprintf(diag2, sizeof(diag2), "2. Oxigeno algo bajo");
  else                         snprintf(diag2, sizeof(diag2), "2. Oxigeno normal (%d%%)", pacienteSpO2);
}

static void fallarCon(const char *m) {
  pedirModo(WK_IDLE);
  snprintf(errMsg, sizeof(errMsg), "%s", m);
  emocion = EMO_SAD;
  irA(ST_ERROR);
}

// =====================================================================
// 14. ENTRADA DE USUARIO
// =====================================================================
void entradaUsuario(Button b) {
  if (b == BTN_NONE) return;
  ultimaTecla = millis();
  repintar = true;

  switch (estado) {
    case ST_SPLASH:
      emocion = EMO_NORMAL;
      irA(ST_IDLE);
      break;

    case ST_IDLE:
      irA(ST_MENU);
      break;

    case ST_MENU:
      if (b == BTN_UP)   menuSel = (menuSel == 0) ? MENU_N - 1 : menuSel - 1;
      if (b == BTN_DOWN) menuSel = (menuSel == MENU_N - 1) ? 0 : menuSel + 1;
      if (b == BTN_BACK) { emocion = EMO_NORMAL; irA(ST_IDLE); }
      if (b == BTN_OK) {
        switch (menuSel) {
          case 0:
            if (!hwMaxOk) fallarCon(hwMaxRaro ? "Chip no compatible" : "Sensor no detectado");
            else { pacienteBPM = 0; pacienteSpO2 = 0; emocion = EMO_DOWN; irA(ST_CHK_REQ); }
            break;
          case 1: pedirModo(WK_NET); irA(ST_NET_WIFI); break;
          case 2: histPag = 0; irA(ST_HIST); break;
          case 3: wizIniciar(); irA(ST_CAL); break;
          default: aboutPag = 0; irA(ST_ABOUT); break;
        }
      }
      break;

    case ST_CHK_REQ:
    case ST_CHK_READ:
      if (b == BTN_BACK) { pedirModo(WK_IDLE); emocion = EMO_NORMAL; irA(ST_MENU); }
      break;

    case ST_CHK_RESULT:
      if (b == BTN_UP || b == BTN_DOWN) resPag = (resPag == 0) ? 1 : 0;
      if (b == BTN_OK || b == BTN_BACK) { emocion = EMO_NORMAL; irA(ST_MENU); }
      break;

    case ST_NET_WIFI:
    case ST_NET_SEARCH:
      if (b == BTN_BACK) { pedirModo(WK_IDLE); irA(ST_MENU); }
      break;

    case ST_NET_QR:
      if (b == BTN_DOWN) irA(ST_NET_DATA);
      if (b == BTN_BACK || b == BTN_OK) { pedirModo(WK_IDLE); irA(ST_MENU); }
      break;

    case ST_NET_DATA:
      if (b == BTN_UP) irA(ST_NET_QR);
      if (b == BTN_BACK || b == BTN_OK) { pedirModo(WK_IDLE); irA(ST_MENU); }
      break;

    case ST_NET_ERROR:
      if (b == BTN_OK) { pedirModo(WK_IDLE); pedirModo(WK_NET); irA(ST_NET_WIFI); }
      if (b == BTN_BACK) { pedirModo(WK_IDLE); irA(ST_MENU); }
      break;

    case ST_HIST:
      if (histN > 0) {
        if (b == BTN_DOWN) histPag = (histPag + 1) % histN;
        if (b == BTN_UP)   histPag = (histPag + histN - 1) % histN;
      }
      if (b == BTN_BACK || b == BTN_OK) irA(ST_MENU);
      break;

    case ST_ABOUT:
      if (b == BTN_DOWN) aboutPag = 1;
      if (b == BTN_UP)   aboutPag = 0;
      if (b == BTN_BACK || b == BTN_OK) irA(ST_MENU);
      break;

    case ST_ERROR:
      emocion = EMO_NORMAL;
      irA(ST_MENU);
      break;

    default:
      break;
  }
}

// =====================================================================
// 15. SETUP (NUCLEO 1)
// =====================================================================
// Por que se reinicio la ultima vez: lo primero que hay que saber si el
// equipo se queda reiniciandose.
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
    default:               return "desconocido";
  }
}

void setup() {
  Serial.begin(115200);
  delay(80);
  Serial.println(F("\n=== MEDIBOT PANEL v7.0 ==="));
  Serial.printf("[ARRANQUE] Ultimo reinicio: %s\n", motivoReinicio());
  Serial.printf("[ARRANQUE] Memoria libre: %u bytes\n", (unsigned)ESP.getFreeHeap());

  // --- Pantalla: setBusClock ANTES de begin() o no surte efecto ---
  u8g2.setBusClock(LCD_BUS_CLOCK);
  u8g2.begin();
  u8g2.enableUTF8Print();
  u8g2.setFontMode(0);

  // --- ADC del teclado ---
  analogReadResolution(ADC_BITS);
  analogSetPinAttenuation(KEYPAD_PIN, ADC_ATTENUATION);
  pinMode(KEYPAD_PIN, INPUT);

  // --- Memoria: calibracion e historial ---
  memcpy(keyMapDefecto, keyMap, sizeof(keyMap));   // red de seguridad
  nvsOk = prefs.begin(NVS_NS, false);
  if (!nvsOk) {
    Serial.println(F("[MEMORIA] La NVS no abre: se reinicia la particion"));
    nvs_flash_erase();
    nvs_flash_init();
    nvsOk = prefs.begin(NVS_NS, false);
  }
  Serial.printf("[MEMORIA] %s\n", nvsOk ? "OK (se guardan calibracion e historial)"
                                         : "FALLO: no se guardara nada");
  const bool hayCal = tecladoHayCal();
  if (hayCal) tecladoCargarCal();
  else        Serial.println(F("[TECLADO] Sin calibracion guardada: se abre el asistente"));
  if (tecladoActivos() == 0) tecladoRestaurarDefecto();
  const Button mantenido = tecladoMedirReposo();
  histCargar();
  Serial.printf("[MEMORIA] %u lecturas guardadas\n", (unsigned)histN);

  // --- Sensor MAX30102 (con escaneo del bus y reintentos, bloque 7.0) ---
  Wire.begin(I2C_SDA_PIN, I2C_SCL_PIN);
  if (!sensorArrancar(true))
    Serial.println(F("[MAX] Se seguira reintentando en segundo plano cada 3 s"));

  memset((void *)&g_sh, 0, sizeof(g_sh));
  ultimaTecla = millis();
  proxParpadeo = millis() + 2500;

  // 16 KB, no 8: esta tarea hace sensor, WiFi, HTTP, JSON y QR. Con 8 KB se
  // desborda la pila y eso es un reinicio seco ("A stack overflow in task
  // worker has been detected" -> Rebooting).
  xTaskCreatePinnedToCore(tareaTrabajo, "worker", TAREA_STACK, NULL, 1, &g_tarea, 0);

  // Sin calibracion, o con un boton mantenido al encender -> asistente.
  // Esa es la via de escape si los rangos guardados quedaron mal y no se
  // puede navegar el menu para repetirlos.
  if (!hayCal || mantenido != BTN_NONE) { wizIniciar(); irA(ST_CAL); }
  else irA(ST_SPLASH);
}

// =====================================================================
// 16. LOOP (NUCLEO 1): interfaz, sin ningun delay bloqueante
// =====================================================================
void loop() {
  const uint32_t ahora = millis();

  // --- Consola: 'c' abre el asistente de calibracion en cualquier momento ---
  while (Serial.available()) {
    const int c = Serial.read();
    if (c == 'c' || c == 'C') {
      pedirModo(WK_IDLE);
      wizIniciar();
      irA(ST_CAL);
    }
  }

  // --- Teclado (o asistente) cada KEY_POLL_MS ---
  if (ahora - ultimoSondeo >= KEY_POLL_MS) {
    ultimoSondeo = ahora;
    if (estado == ST_CAL) {
      if (wizPaso(ahora)) { emocion = EMO_NORMAL; menuSel = 0; irA(ST_MENU); }
      repintar = true;
    } else {
      const Button ev = tecladoLeer();
      if (ev != BTN_NONE) entradaUsuario(ev);
    }
  }

  // --- Reloj de animacion, independiente de la logica de estados ---
  if (ahora - ultimoFrame >= UI_FRAME_MS) {
    ultimoFrame = ahora;
    frame++;
    if (emocion == EMO_NORMAL || emocion == EMO_HAPPY) {
      if (!parpadeo && ahora >= proxParpadeo) { parpadeo = true; finParpadeo = ahora + 120; }
      else if (parpadeo && ahora >= finParpadeo) { parpadeo = false; proxParpadeo = ahora + random(2200, 6000); }
    } else parpadeo = false;
    if (pantallaAnimada()) repintar = true;
  }

  // --- Transiciones de estado (siempre con millis()) ---
  const Shared v = shGet();

  switch (estado) {
    case ST_SPLASH:
      if (enEstado() > SPLASH_MS) { emocion = EMO_NORMAL; irA(ST_IDLE); }
      break;

    case ST_CHK_REQ:
      if (enEstado() > 2200) { pedirModo(WK_PPG); emocion = EMO_LOAD; irA(ST_CHK_READ); }
      break;

    case ST_CHK_READ:
      if (v.epoca != g_epoca) break;         // el nucleo 0 aun no ha arrancado
      if (v.ppgListo) {
        pacienteBPM  = v.finalBPM;
        pacienteSpO2 = v.finalSpO2;
        pedirModo(WK_IDLE);
        evaluar();
        histGuardar(pacienteBPM, pacienteSpO2);
        resPag = 0;
        emocion = (pacienteBPM > 100 || pacienteBPM < 60 || pacienteSpO2 < 92) ? EMO_SAD : EMO_HAPPY;
        irA(ST_CHK_RESULT);
      } else if (v.ppgFallo) {
        fallarCon(v.dedo ? "Senal debil o movimiento" : "No se detecto el dedo");
      }
      break;

    case ST_NET_WIFI:
    case ST_NET_SEARCH:
      if (v.etapa == NET_FOUND) {
        char url[64];
        const IPAddress ip(v.ip);
        snprintf(url, sizeof(url), "http://%s:%u", ip.toString().c_str(), (unsigned)v.puerto);
        if (qrGenerar(url)) irA(ST_NET_QR);
        else { snprintf(errMsg, sizeof(errMsg), "URL demasiado larga"); irA(ST_NET_DATA); }
      } else if (v.etapa == NET_FAIL) {
        irA(ST_NET_ERROR);
      } else if (estado == ST_NET_WIFI && (v.etapa == NET_MDNS || v.etapa == NET_SWEEP)) {
        irA(ST_NET_SEARCH);
      }
      break;

    case ST_NET_QR:
    case ST_NET_DATA:
      if (v.etapa == NET_FAIL) irA(ST_NET_ERROR);
      break;

    case ST_ERROR:
      if (enEstado() > ERROR_SCREEN_MS) { emocion = EMO_NORMAL; irA(ST_MENU); }
      break;

    default:
      break;
  }

  // --- Vuelta a reposo por inactividad (nunca durante una medida ni la red) ---
  const bool ocupado = (estado == ST_CHK_REQ || estado == ST_CHK_READ ||
                        estado == ST_NET_WIFI || estado == ST_NET_SEARCH ||
                        estado == ST_CAL || estado == ST_SPLASH);
  if (!ocupado && estado != ST_IDLE && (ahora - ultimaTecla > INACTIVITY_MS)) {
    pedirModo(WK_IDLE);
    emocion = EMO_NORMAL;
    irA(ST_IDLE);
  }

  if (repintar) pintar();

  // Cede el nucleo al planificador: NO es un delay bloqueante, libera la CPU
  // y alimenta el watchdog. El nucleo 0 sigue capturando sin pausa.
  vTaskDelay(1 / portTICK_PERIOD_MS);
}
