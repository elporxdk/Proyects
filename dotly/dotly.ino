/*
 * =====================================================================
 *  DOTLY  -  Braille, punto a punto
 * =====================================================================
 *  Aprender a leer y escribir Braille (y el alfabeto manual en señas) con
 *  un LCD de 16x2, un teclado analogico de 5 botones y una pagina web que
 *  el propio equipo sirve por su red WiFi.
 *
 *  PLACA: ESP32-S3 (tambien vale un S2).
 *    El teclado va en el GPIO8, que en el S3 es ADC1_CH7. Eso importa: el
 *    ADC1 sigue funcionando con el WiFi encendido (el ADC2 no), y en el ESP32
 *    clasico el GPIO8 es un pin de la memoria flash. Si se compila para un
 *    ESP32 clasico, el #error de la seccion 1.1 lo dice.
 *
 *  LIBRERIAS: solo "LiquidCrystal I2C" (Frank de Brabander), del gestor de
 *    librerias. WiFi, WebServer, DNSServer y Preferences vienen con el core.
 *
 *  CONEXIONES (las del codigo base):
 *    LCD 16x2 I2C (0x27)  SDA -> GPIO4   SCL -> GPIO5   VCC 5 V   GND
 *    Teclado ADKeyboard   OUT -> GPIO8   VCC -> 3V3 (no 5 V)      GND
 *
 *  BOTONES (los papeles del codigo base, mas dos que estaban libres):
 *    SW1 = entrar / aceptar     SW4 = volver
 *    SW5 = repetir              SW2 / SW3 = anterior / siguiente
 *
 *  RED: DOTLY crea su propia WiFi, "DOTLY-XXXX" con clave "dotly1234". Al
 *    conectarse el movil abre la pagina solo; si no, http://192.168.4.1
 * =====================================================================
 */

#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include <WiFi.h>
#include <WebServer.h>
#include <DNSServer.h>
#include <Preferences.h>
#include <nvs_flash.h>
#include "pagina_web.h"

// =====================================================================
// 1. CONFIGURACION
// =====================================================================

// ---------------------------------------------------------------------
// 1.1 PINES Y PANTALLA (los del codigo base, sin cambios)
// ---------------------------------------------------------------------
#define ADKEY_PIN 8

LiquidCrystal_I2C lcd(0x27, 16, 2);

//  El GPIO8 solo sirve como entrada analogica en el ESP32-S3 / S2. En el ESP32
//  clasico es de la memoria flash (tocarlo cuelga la placa) y en el C3 no es
//  analogico. Mejor un error claro al compilar que una placa que no arranca.
#if defined(CONFIG_IDF_TARGET_ESP32) && (ADKEY_PIN >= 6) && (ADKEY_PIN <= 11)
#error "En el ESP32 clasico el GPIO8 es de la memoria flash. Usa un ESP32-S3, o pon ADKEY_PIN en 34, 35, 36 o 39."
#endif
#if defined(CONFIG_IDF_TARGET_ESP32C3) && (ADKEY_PIN > 4)
#error "En el ESP32-C3 solo los GPIO0-4 son analogicos. Cambia ADKEY_PIN."
#endif

// ---------------------------------------------------------------------
// 1.2 ADC
// ---------------------------------------------------------------------
//  El teclado se trabaja en milivoltios medidos en el pin. analogReadMilliVolts
//  aplica la calibracion de fabrica del chip, asi que no hay que ajustar nada.
#define ADC_BITS               12
#define ADC_MAX_COUNTS         ((1 << ADC_BITS) - 1)
#define ADC_ATTENUATION        ADC_11db
#define ADC_FULLSCALE_MV       3300.0f
#define USE_ESP_ADC_CAL        1

// ---------------------------------------------------------------------
// 1.3 TECLADO ANALOGICO: 5 BOTONES EN UNA SOLA ENTRADA
// ---------------------------------------------------------------------
//  Alimentado a 3V3, el ADKeyboard da 0,00 / 0,46 / 0,99 / 1,65 / 2,44 V y el
//  reposo 3,3 V: los cinco se distinguen. A 5 V el quinto boton cae a 3,70 V,
//  por encima de lo que el ADC del ESP32 puede medir (~3,1 V), y se confunde
//  con el reposo. Por eso el codigo base ya ponia el reposo en >= 3,00 V.
//
//  La tabla de abajo reproduce los umbrales del codigo base y solo se usa
//  hasta la primera calibracion: lo que mida el asistente manda sobre ella.
//  mvMin > mvMax = boton desactivado.
enum Boton : uint8_t { SW_NINGUNO = 0, SW1, SW2, SW3, SW4, SW5, SW_TOTAL };

const char *const BTN_NOMBRE[SW_TOTAL] = { "----", "SW1", "SW2", "SW3", "SW4", "SW5" };
const Boton BTN_ORDEN[] = { SW1, SW2, SW3, SW4, SW5 };      // orden del asistente
const uint8_t BTN_ORDEN_N = sizeof(BTN_ORDEN) / sizeof(BTN_ORDEN[0]);

struct KeyDef { Boton id; int16_t mvMin; int16_t mvMax; };
KeyDef KEYPAD_MAP[] = {
  { SW1,  -50,  240 },     // < 0,25 V en el codigo base
  { SW2,  260,  790 },     // < 0,80 V
  { SW3,  810, 1340 },     // < 1,35 V
  { SW4, 1360, 2090 },     // < 2,10 V
  { SW5, 2110, 2990 },     // < 3,00 V
};
const uint8_t KEYPAD_MAP_SIZE = sizeof(KEYPAD_MAP) / sizeof(KEYPAD_MAP[0]);
KeyDef KEYPAD_MAP_DEFECTO[KEYPAD_MAP_SIZE];      // copia de fabrica

// ---------------------------------------------------------------------
// 1.4 FILTRADO, ANTIRREBOTE E HISTERESIS DEL TECLADO
// ---------------------------------------------------------------------
#define KEY_POLL_MS          10      // periodo de muestreo del teclado
#define KEY_SAMPLES          9       // muestras por lectura (mediana). Impar y >= 3
#define KEY_EMA_ALPHA        0.40f   // filtro exponencial (1.0 = sin filtro)
#define KEY_DEBOUNCE_MS      40      // ms estable para aceptar una pulsacion
#define KEY_RELEASE_MS       40      // ms estable para aceptar la soltada
#define KEY_HYSTERESIS_MV    70      // el boton ya pulsado ensancha su rango
#define KEY_IDLE_GUARD_MV    120     // franja prohibida alrededor del reposo
//  Teclado desconectado: sin nada enchufado el pin flota cerca de 0 V y sus
//  lecturas bailan; un boton pulsado da poca tension pero QUIETA.
#define KEY_AIRE_MV          350
#define KEY_AIRE_DISP_MV     80
#define KEY_SPREAD_MV        120     // dispersion dentro de una lectura
#define KEY_SALTOS_VENTANA   32      // lecturas que se recuerdan
#define KEY_SALTOS_MIN       24      // ...de las cuales tantas deben bailar
#define KEY_REPEAT_ENABLED   1       // autorepeticion en SW2/SW3 (anterior/siguiente)
#define KEY_REPEAT_DELAY_MS  600
#define KEY_REPEAT_RATE_MS   180

// ---------------------------------------------------------------------
// 1.5 ASISTENTE DE CALIBRACION DEL TECLADO
// ---------------------------------------------------------------------
#define WIZ_REPOSO_MS        1500    // tiempo midiendo el reposo al empezar
#define WIZ_ESTABLE_MS       700     // pulsacion mantenida para darla por buena
#define WIZ_SALTO_MS         12000   // si no se pulsa, se omite ese boton
#define WIZ_UMBRAL_MV        150     // diferencia minima con el reposo
#define WIZ_TOLER_MV         90      // cuanto puede moverse y seguir "estable"
#define WIZ_SOLTAR_MS        12000   // gracia esperando a que se suelte el teclado
//  Cada boton se mide 64 x 25 = 1600 veces: de ahi salen la mediana (el
//  centro) y la dispersion real (percentil 10 a 90) de ese boton.
#define WIZ_SUBMUESTRAS      25      // conversiones del ADC por lectura
#define WIZ_MUESTRAS         64      // lecturas acumuladas por boton
#define WIZ_MIN_MUESTRAS     24      // con menos de estas, el boton no vale
//  Cada boton se queda con todo el sitio que haya hasta su vecino, menos una
//  franja de guarda: cuanto mas ancho, mas tolera un cable largo o el calor.
#define WIZ_MARGEN_MAX       400     // semiancho maximo del rango, en mV
#define WIZ_MARGEN_MIN       120     // por debajo se avisa de que va justo
#define WIZ_MARGEN_ABS_MIN   30      // por debajo el boton se descarta
#define WIZ_SEPARACION       20      // franja de guarda entre dos rangos
#define WIZ_DISP_FACTOR      3       // el rango debe cubrir 3x lo que baila
#define NVS_NS               "dotly"
#define CAL_MAGIC            0x4B35  // formato de la calibracion (5 botones)

// ---------------------------------------------------------------------
// 1.6 RED WIFI (modo punto de acceso)
// ---------------------------------------------------------------------
#define AP_PREFIJO           "DOTLY-"     // + 4 cifras del chip: "DOTLY-1A2B"
#define AP_CLAVE             "dotly1234"  // WPA2: minimo 8 caracteres
#define AP_CANAL             6
#define AP_MAX_CLIENTES      4
#define WEB_PUERTO           80
#define DNS_PUERTO           53

// ---------------------------------------------------------------------
// 1.7 INTERFAZ Y TIEMPOS
// ---------------------------------------------------------------------
#define RESPUESTA_MS         1600    // cuanto se ve "Bien!" antes de seguir
#define MARQ_PAUSA_MS        1200    // pausa al principio del texto que se desplaza
#define PALABRA_MAX_CELDAS   16      // celdas de una palabra (8 por pantalla)
#define ESCRITO_MAX          64      // caracteres del modo Escribir
#define PIZARRA_MAX          72      // celdas que admite la pizarra (9 pantallas)
#define SENA_MAX             120     // bytes de una descripcion de seña
#define PALABRAS_MAX         600     // bytes de la lista de palabras

// ---------------------------------------------------------------------
// 1.8 CELDA BRAILLE FISICA (opcional, apagada)
// ---------------------------------------------------------------------
//  Si algun dia se monta una celda con 6 LED, solenoides o motores de
//  vibracion (cada uno a traves de su transistor), pon CELDA_FISICA a 1: los
//  seis pines reproducen los puntos de la letra que haya en pantalla.
#define CELDA_FISICA         0
const uint8_t CELDA_PINES[6] = { 9, 10, 11, 12, 13, 14 };   // puntos 1..6
#define CELDA_ACTIVA         HIGH

// =====================================================================
// 2. BRAILLE, SEÑAS Y PALABRAS
// =====================================================================
//  Signografia braille española. Los puntos van en un byte: bit 0 = punto 1
//  ... bit 5 = punto 6, que es exactamente como los numera Unicode: el
//  caracter braille es U+2800 + ese byte.
//
//     1 o o 4
//     2 o o 5
//     3 o o 6
enum TipoSigno : uint8_t { T_LETRA, T_ACENTO, T_NUMERO, T_SIGNO };

struct Signo { uint16_t cp; uint8_t puntos; uint8_t tipo; const char *etiqueta; };

const Signo SIGNOS[] = {
  // abecedario (27 letras, con la ñ despues de la n)
  {'a', 0x01, T_LETRA, "a"}, {'b', 0x03, T_LETRA, "b"}, {'c', 0x09, T_LETRA, "c"},
  {'d', 0x19, T_LETRA, "d"}, {'e', 0x11, T_LETRA, "e"}, {'f', 0x0B, T_LETRA, "f"},
  {'g', 0x1B, T_LETRA, "g"}, {'h', 0x13, T_LETRA, "h"}, {'i', 0x0A, T_LETRA, "i"},
  {'j', 0x1A, T_LETRA, "j"}, {'k', 0x05, T_LETRA, "k"}, {'l', 0x07, T_LETRA, "l"},
  {'m', 0x0D, T_LETRA, "m"}, {'n', 0x1D, T_LETRA, "n"}, {0xF1, 0x3B, T_LETRA, "ñ"},
  {'o', 0x15, T_LETRA, "o"}, {'p', 0x0F, T_LETRA, "p"}, {'q', 0x1F, T_LETRA, "q"},
  {'r', 0x17, T_LETRA, "r"}, {'s', 0x0E, T_LETRA, "s"}, {'t', 0x1E, T_LETRA, "t"},
  {'u', 0x25, T_LETRA, "u"}, {'v', 0x27, T_LETRA, "v"}, {'w', 0x3A, T_LETRA, "w"},
  {'x', 0x2D, T_LETRA, "x"}, {'y', 0x3D, T_LETRA, "y"}, {'z', 0x35, T_LETRA, "z"},
  // vocales acentuadas y dieresis
  {0xE1, 0x37, T_ACENTO, "á"}, {0xE9, 0x2E, T_ACENTO, "é"}, {0xED, 0x0C, T_ACENTO, "í"},
  {0xF3, 0x2C, T_ACENTO, "ó"}, {0xFA, 0x3E, T_ACENTO, "ú"}, {0xFC, 0x33, T_ACENTO, "ü"},
  // numeros: la celda de a..j, precedida del signo de numero
  {'1', 0x01, T_NUMERO, "1"}, {'2', 0x03, T_NUMERO, "2"}, {'3', 0x09, T_NUMERO, "3"},
  {'4', 0x19, T_NUMERO, "4"}, {'5', 0x11, T_NUMERO, "5"}, {'6', 0x0B, T_NUMERO, "6"},
  {'7', 0x1B, T_NUMERO, "7"}, {'8', 0x13, T_NUMERO, "8"}, {'9', 0x0A, T_NUMERO, "9"},
  {'0', 0x1A, T_NUMERO, "0"},
  // signos de puntuacion
  {'.', 0x04, T_SIGNO, "."}, {',', 0x02, T_SIGNO, ","}, {';', 0x06, T_SIGNO, ";"},
  {':', 0x12, T_SIGNO, ":"}, {'?', 0x22, T_SIGNO, "¿?"}, {'!', 0x16, T_SIGNO, "¡!"},
  {'"', 0x26, T_SIGNO, "\""}, {'(', 0x23, T_SIGNO, "("}, {')', 0x1C, T_SIGNO, ")"},
  {'-', 0x24, T_SIGNO, "-"},
};
const uint8_t N_SIGNOS   = sizeof(SIGNOS) / sizeof(SIGNOS[0]);
const uint8_t N_LETRAS   = 27;   // a..z con ñ
const uint8_t N_APRENDER = 33;   // letras + vocales acentuadas: lo que entra en retos
const uint8_t I_NUMEROS  = 33;   // primer numero en SIGNOS
const uint8_t I_SIGNOS   = 43;   // primer signo de puntuacion en SIGNOS
#define SIGNO_NUMERO     0x3C    // puntos 3-4-5-6
#define SIGNO_MAYUSCULA  0x28    // puntos 4-6

//  Los bloques de "Aprender". El BLOQUE 1 es el del codigo base (A B C D E F).
struct Bloque { const char *nombre; uint8_t desde; uint8_t cuantos; uint8_t paso; };
const Bloque BLOQUES[] = {
  {"BLOQUE 1",  0,  6, 2},   // A B C D E F
  {"BLOQUE 2",  6,  6, 2},   // G H I J K L
  {"BLOQUE 3", 12,  6, 2},   // M N Ñ O P Q
  {"BLOQUE 4", 18,  6, 2},   // R S T U V W
  {"BLOQUE 5", 24,  3, 2},   // X Y Z
  {"ACENTOS",  27,  6, 2},   // Á É Í Ó Ú Ü
  {"NUMEROS",  33, 10, 1},   // 1234567890
  {"SIGNOS",   43, 10, 1},   // . , ; : ? ! " ( ) -
};
const uint8_t N_BLOQUES = sizeof(BLOQUES) / sizeof(BLOQUES[0]);

//  Alfabeto manual (dactilologia), una descripcion por letra. Es una
//  referencia de partida: cada pais tiene variantes (LESSA, LSM...), y desde
//  la pagina web se puede reescribir cualquiera; lo reescrito se guarda.
const char *const SENAS_DEF[27] = {
  "Puño cerrado con el pulgar al costado.",
  "Mano abierta, dedos juntos hacia arriba y pulgar doblado sobre la palma.",
  "Mano curvada formando una C.",
  "Índice hacia arriba; los demás dedos tocan el pulgar formando un círculo.",
  "Dedos doblados sobre el pulgar, uñas hacia afuera.",
  "Índice y pulgar se tocan formando un círculo; los otros tres dedos extendidos.",
  "Índice y pulgar extendidos en horizontal, paralelos.",
  "Índice y medio extendidos en horizontal, juntos.",
  "Puño cerrado con el meñique hacia arriba.",
  "Meñique arriba, y se traza una J en el aire.",
  "Índice y medio arriba en V, con el pulgar entre ellos.",
  "Índice arriba y pulgar al lado: forma una L.",
  "Pulgar bajo el índice, el medio y el anular.",
  "Pulgar bajo el índice y el medio.",
  "Forma de la N con un movimiento ondulado.",
  "Todos los dedos curvados tocando el pulgar: forma una O.",
  "Como la K, pero apuntando hacia abajo.",
  "Como la G, pero apuntando hacia abajo.",
  "Índice y medio cruzados.",
  "Puño cerrado con el pulgar por delante de los dedos.",
  "Pulgar asomando entre el índice y el medio.",
  "Índice y medio juntos hacia arriba.",
  "Índice y medio separados en V.",
  "Índice, medio y anular extendidos y separados.",
  "Índice doblado en forma de gancho.",
  "Pulgar y meñique extendidos.",
  "El índice traza una Z en el aire.",
};
//  Letras que se hacen con movimiento (J, Ñ, Z), por indice en SIGNOS.
const uint8_t SENAS_CON_MOVIMIENTO[] = { 9, 14, 26 };

const char PALABRAS_DEF[] =
  "casa,mamá,papá,sol,luna,agua,pan,gato,perro,mesa,libro,flor,árbol,niño,"
  "cama,dedo,mano,ojo,boca,amigo,escuela,familia,color,azul,rojo,verde,cielo,"
  "mar,leche,queso,fruta,piña,maíz,café,lápiz,jugar,leer,oír";

// =====================================================================
// 3. TIPOS Y ESTADO GLOBAL
// =====================================================================
// Estados de la interfaz. BIENVENIDA y MENU_BLOQUES son los del codigo base;
// BLOQUE_1 ahora se llama BLOQUE porque vale para cualquier bloque.
enum Estado {
  BIENVENIDA,
  MENU_PRINCIPAL,
  MENU_BLOQUES,
  BLOQUE,
  LETRA,
  ESCRIBIR,
  RETO_LEER,
  RETO_FORMAR,
  RETO_SENAS,
  RETO_RESUMEN,
  PALABRAS,
  SENAS,
  PROGRESO,
  AJUSTES,
  AJUSTE_RED,
  CONFIRMAR_BORRAR,
  ACERCA,
  PIZARRA,
  CALIBRAR
};

Estado estado = BIENVENIDA;

const char *const MENU_TXT[] = {
  "Aprender", "Escribir", "Reto: leer", "Reto: formar",
  "Palabras", "Senas", "Reto: senas", "Progreso", "Ajustes" };
const uint8_t MENU_N = sizeof(MENU_TXT) / sizeof(MENU_TXT[0]);
enum OpcionMenu : uint8_t { OP_APRENDER, OP_ESCRIBIR, OP_RETO_LEER, OP_RETO_FORMAR,
                            OP_PALABRAS, OP_SENAS, OP_RETO_SENAS, OP_PROGRESO, OP_AJUSTES };

const char *const AJUSTES_TXT[] = {
  "Calibrar teclado", "Red WiFi", "Preguntas", "Velocidad",
  "Nivel", "Borrar progreso", "Acerca de DOTLY" };
const uint8_t AJUSTES_N = sizeof(AJUSTES_TXT) / sizeof(AJUSTES_TXT[0]);
const char *const NIVEL_TXT[5]     = { "", "a-j", "a-t", "a-z", "todo" };
const char *const VELOCIDAD_TXT[3] = { "lenta", "media", "rapida" };
const uint16_t MARQ_PASO_MS[3]     = { 450, 300, 180 };     // desplazamiento de texto
const uint16_t DELETREO_MS[3]      = { 4000, 2600, 1600 };  // una letra en señas

// --- Teclado ---
struct KeypadRuntime {
  Boton    raw           = SW_NINGUNO;  // clasificacion instantanea
  Boton    stable        = SW_NINGUNO;  // clasificacion ya antirrebotada
  uint32_t lastRawChange = 0;
  uint32_t pressStartMs  = 0;
  uint32_t lastRepeatMs  = 0;
  float    ema           = 0.0f;
  bool     emaInit       = false;
  int16_t  mv            = 0;           // tension filtrada en el pin
  uint16_t counts        = 0;           // cuentas crudas del ADC (diagnostico)
  int16_t  idleMv        = 3300;        // nivel de reposo medido al arrancar
  bool     idleOk        = false;
  bool     desconectado  = false;       // el pin flota: no hay teclado enchufado
  int16_t  spread        = 0;           // dispersion de la ultima lectura, en mV
  uint32_t saltos        = 0;           // bitmap de lecturas que bailaron
} keypad;

struct Asistente {
  uint8_t  paso;                 // indice dentro de BTN_ORDEN
  uint8_t  fase;                 // 0 reposo | 1 pidiendo | 2 soltar | 3 calcular | 4 resumen
  uint32_t t0;
  uint32_t estableDesde;
  int16_t  ultimo;
  int16_t  centro[SW_TOTAL];
  int16_t  disp[SW_TOTAL];       // cuanto baila cada boton (percentil 10-90)
  int16_t  ancho[SW_TOTAL];      // semiancho que se le ha dado al rango
  bool     hecho[SW_TOTAL];
  uint8_t  capturados;
  bool     guardado;
  bool     esperandoSoltar;
  char     aviso[17];
  int16_t  muestras[WIZ_MUESTRAS];
  uint8_t  nMuestras;
  int16_t  dispReposo;
} wiz;

struct CalEntrada { uint8_t id; int16_t mn, mx; };
struct CalBlob {
  uint16_t   magic;
  int16_t    reposo;
  uint8_t    n;
  CalEntrada e[SW_TOTAL];
};

// --- Pantalla: doble buffer + glifos en la CGRAM del LCD ---
//  Todo lo que se pinta pasa por aqui. Se compone el cuadro entero en 'tras'
//  y solo se mandan al LCD los caracteres que cambian: sin lcd.clear() no hay
//  parpadeo, y el I2C va mucho mas desahogado. De paso, 'frente' es lo que se
//  ve en el LCD, y es lo que la pagina web enseña como espejo.
//  Los 8 caracteres propios del LCD (0..7) se reparten en cada cuadro entre
//  celdas braille (clave 0x100 | puntos) y letras que el LCD no trae, como la
//  Ñ o las vocales acentuadas (clave 0x200 | glifo).
struct Pantalla {
  uint8_t  tras[2][16];
  uint8_t  frente[2][16];
  uint16_t claveTras[8];
  uint16_t cgram[8];
  int8_t   cursorCol, cursorFila;
  bool     parpadeando;
  bool     frenteValido;
} P;

struct Marquesina { char txt[180]; uint16_t len; uint16_t pos; uint32_t t; } marq;

struct Celda { uint8_t puntos; uint16_t cp; };   // cp: 0 = hueco, '#' numero, '^' mayuscula

enum TipoReto : uint8_t { RT_LEER, RT_FORMAR, RT_SENAS };
struct Reto {
  uint8_t  tipo;                 // RT_LEER: celda -> letra | RT_FORMAR: letra -> puntos
                                 // RT_SENAS: descripcion de la seña -> letra
  uint8_t  objetivo, anterior;
  uint8_t  opciones[4], nOpciones, sel;
  uint8_t  pregunta, total, aciertos, racha;
  bool     respondido, acerto;
  uint32_t tRespuesta;
} reto;

struct Progreso {
  uint16_t magic;
  uint16_t leerOk[N_APRENDER], leerTot[N_APRENDER];
  uint16_t formarOk[N_APRENDER], formarTot[N_APRENDER];
  uint16_t senasOk[N_LETRAS], senasTot[N_LETRAS];
  uint8_t  visto[N_APRENDER];
  uint16_t rachaMax;
  uint16_t rondas;
  uint32_t palabras;
} prog;
#define PROG_MAGIC  0xB702

struct Ajustes {
  uint16_t magic;
  uint8_t  preguntas;            // 5, 10 o 20 por ronda
  uint8_t  velocidad;            // 0 lenta, 1 media, 2 rapida
  uint8_t  nivel;                // 1 a-j, 2 a-t, 3 a-z, 4 todo
  char     ssid[33];
  char     clave[64];
} aj;
#define AJ_MAGIC    0xA701

// --- Estado de cada modo ---
Preferences prefs;
bool        nvsOk = false;

uint8_t  menuSel = 0, bloqueSel = 0, letraSel = 0, ajusteSel = 0, pagina = 0;
uint8_t  editorPuntos = 0, editorCursor = 0;
uint16_t escrito[ESCRITO_MAX];
uint8_t  escritoN = 0;
bool     escritoNumero = false;          // tras el signo de numero, a..j son cifras
uint16_t palabraSel = 0;
bool     palabraVista = false;
uint8_t  senaSel = 0;
char     senaTexto[27][SENA_MAX + 1];
char     palabrasLista[PALABRAS_MAX + 1];
Celda    deletreo[40];                   // palabra que se deletrea en señas
uint8_t  deletreoN = 0, deletreoPos = 0;
uint32_t deletreoT = 0;
Celda    pizarra[PIZARRA_MAX];
uint8_t  pizarraN = 0;
uint8_t  botonWeb = 0;                   // tecla pulsada desde la pagina web
uint32_t ultimoSondeo = 0;

WebServer webServer(WEB_PUERTO);
DNSServer dnsServer;
bool      apOk = false;

// =====================================================================
// 4. UTILIDADES
// =====================================================================
// Tiempo transcurrido sin desbordarse si 't' se apunto despues que 'ahora'.
static inline uint32_t desde(uint32_t t, uint32_t ahora) { return (ahora >= t) ? (ahora - t) : 0; }

static bool senaConMovimiento(uint8_t i) {
  for (uint8_t m : SENAS_CON_MOVIMIENTO) if (m == i) return true;
  return false;
}

static int cmpI16(const void *a, const void *b) {
  const int16_t x = *(const int16_t *)a, y = *(const int16_t *)b;
  return (x > y) - (x < y);
}
static inline int16_t difAbs(int16_t a, int16_t b) { return (a > b) ? (a - b) : (b - a); }

// Siguiente caracter de un texto UTF-8 (avanza el puntero). 0 al final.
static uint32_t utf8Leer(const char *&p) {
  const uint8_t c = (uint8_t)*p;
  if (c == 0) return 0;
  if (c < 0x80) { p++; return c; }
  if ((c & 0xE0) == 0xC0 && p[1]) { const uint32_t v = ((c & 0x1F) << 6) | (p[1] & 0x3F); p += 2; return v; }
  if ((c & 0xF0) == 0xE0 && p[1] && p[2]) {
    const uint32_t v = ((c & 0x0F) << 12) | ((p[1] & 0x3F) << 6) | (p[2] & 0x3F); p += 3; return v;
  }
  p++;
  return '?';
}

// Escribe un caracter en UTF-8. Devuelve los bytes usados (maximo 3).
static uint8_t utf8Poner(uint32_t cp, char *o) {
  if (cp < 0x80)  { o[0] = (char)cp; return 1; }
  if (cp < 0x800) { o[0] = (char)(0xC0 | (cp >> 6)); o[1] = (char)(0x80 | (cp & 0x3F)); return 2; }
  o[0] = (char)(0xE0 | (cp >> 12)); o[1] = (char)(0x80 | ((cp >> 6) & 0x3F));
  o[2] = (char)(0x80 | (cp & 0x3F));
  return 3;
}

// Mayusculas / minusculas para ASCII y las letras latinas que usa el español.
static uint32_t aMinuscula(uint32_t cp) {
  if (cp >= 'A' && cp <= 'Z') return cp + 32;
  if (cp >= 0xC0 && cp <= 0xDE && cp != 0xD7) return cp + 0x20;
  return cp;
}
static uint32_t aMayuscula(uint32_t cp) {
  if (cp >= 'a' && cp <= 'z') return cp - 32;
  if (cp >= 0xE0 && cp <= 0xFE && cp != 0xF7) return cp - 0x20;
  return cp;
}

// El LCD solo trae ASCII: las letras con tilde se quedan sin ella.
static char plegar(uint32_t cp) {
  if (cp >= 32 && cp < 127) return (char)cp;
  switch (aMinuscula(cp)) {
    case 0xE1: case 0xE0: case 0xE2: case 0xE4: return (cp < 0xE0) ? 'A' : 'a';
    case 0xE9: case 0xE8: case 0xEA: case 0xEB: return (cp < 0xE0) ? 'E' : 'e';
    case 0xED: case 0xEC: case 0xEE: case 0xEF: return (cp < 0xE0) ? 'I' : 'i';
    case 0xF3: case 0xF2: case 0xF4: case 0xF6: return (cp < 0xE0) ? 'O' : 'o';
    case 0xFA: case 0xF9: case 0xFB: case 0xFC: return (cp < 0xE0) ? 'U' : 'u';
    case 0xF1: return (cp < 0xE0) ? 'N' : 'n';
    default: break;
  }
  if (cp == 0xBF) return '?';
  if (cp == 0xA1) return '!';
  if (cp == 0xAB || cp == 0xBB) return '"';
  return '?';
}

// Busca en la tabla un caracter (en minuscula). -1 si no esta.
static int16_t buscarSigno(uint32_t cp) {
  cp = aMinuscula(cp);
  if (cp == 0xBF) cp = '?';          // ¿ y ? son el mismo signo en braille
  if (cp == 0xA1) cp = '!';          // ¡ y ! tambien
  for (uint8_t i = 0; i < N_SIGNOS; i++) if (SIGNOS[i].cp == cp) return i;
  return -1;
}

// Que caracter forma una combinacion de puntos. -1 si ninguno.
static int16_t signoDePuntos(uint8_t puntos, bool numero) {
  if (numero)
    for (uint8_t i = I_NUMEROS; i < I_SIGNOS; i++) if (SIGNOS[i].puntos == puntos) return i;
  for (uint8_t i = 0; i < N_SIGNOS; i++) {
    if (SIGNOS[i].tipo == T_NUMERO) continue;
    if (SIGNOS[i].puntos == puntos) return i;
  }
  return -1;
}

// "1-2-4" a partir del byte de puntos.
static void textoPuntos(uint8_t puntos, char *o, size_t n) {
  size_t k = 0;
  o[0] = 0;
  for (uint8_t d = 0; d < 6 && k + 2 < n; d++) {
    if (!(puntos & (1 << d))) continue;
    if (k) o[k++] = '-';
    o[k++] = (char)('1' + d);
    o[k] = 0;
  }
  if (!k) snprintf(o, n, "ninguno");
}

// Texto -> celdas braille. Mayusculas con su signo (4-6) y numeros con el
// suyo (3-4-5-6) al principio de cada tanda de cifras. Lo desconocido se salta.
static uint8_t textoACeldas(const char *utf8, Celda *out, uint8_t max, bool conMayusculas) {
  uint8_t n = 0;
  bool enNumero = false;
  const char *p = utf8;
  uint32_t cp;
  while ((cp = utf8Leer(p)) != 0 && n < max) {
    if (cp == ' ' || cp == '\n' || cp == '\t') {
      out[n++] = { 0, 0 };
      enNumero = false;
      continue;
    }
    const int16_t i = buscarSigno(cp);
    if (i < 0) continue;
    if (SIGNOS[i].tipo == T_NUMERO) {
      if (!enNumero) {
        out[n++] = { SIGNO_NUMERO, '#' };
        enNumero = true;
        if (n >= max) break;
      }
      out[n++] = { SIGNOS[i].puntos, (uint16_t)cp };
      continue;
    }
    enNumero = false;
    const bool mayuscula = (aMinuscula(cp) != cp);
    if (conMayusculas && mayuscula && SIGNOS[i].tipo != T_SIGNO) {
      out[n++] = { SIGNO_MAYUSCULA, '^' };
      if (n >= max) break;
    }
    out[n++] = { SIGNOS[i].puntos, (uint16_t)aMinuscula(cp) };
  }
  return n;
}

// Letras especiales que el LCD no trae (se dibujan en la CGRAM).
const uint16_t GLIFO_CP[7] = { 0xD1, 0xC1, 0xC9, 0xCD, 0xD3, 0xDA, 0xDC };   // Ñ Á É Í Ó Ú Ü
const uint8_t GLIFOS[7][8] = {
  {0x0D, 0x16, 0x11, 0x19, 0x15, 0x13, 0x11, 0x00},   // Ñ
  {0x02, 0x04, 0x0E, 0x11, 0x1F, 0x11, 0x11, 0x00},   // Á
  {0x02, 0x04, 0x1F, 0x10, 0x1E, 0x10, 0x1F, 0x00},   // É
  {0x02, 0x04, 0x0E, 0x04, 0x04, 0x04, 0x0E, 0x00},   // Í
  {0x02, 0x04, 0x0E, 0x11, 0x11, 0x11, 0x0E, 0x00},   // Ó
  {0x02, 0x04, 0x11, 0x11, 0x11, 0x11, 0x0E, 0x00},   // Ú
  {0x0A, 0x00, 0x11, 0x11, 0x11, 0x11, 0x0E, 0x00},   // Ü
};
static int8_t glifoDe(uint32_t cp) {
  cp = aMayuscula(cp);
  for (uint8_t g = 0; g < 7; g++) if (GLIFO_CP[g] == cp) return (int8_t)g;
  return -1;
}

// Celda braille en 5x8 pixeles: punto lleno = cuadrado de 2x2; punto vacio =
// un pixel, para que se vea la rejilla de 6 y se sepa que punto falta.
static void bitmapCelda(uint8_t puntos, uint8_t *b) {
  const uint8_t IZQ_LLENO = 0x18, IZQ_VACIO = 0x10, DER_LLENO = 0x03, DER_VACIO = 0x01;
  memset(b, 0, 8);
  for (uint8_t f = 0; f < 3; f++) {
    const bool izq = puntos & (1 << f);          // puntos 1, 2, 3
    const bool der = puntos & (1 << (f + 3));    // puntos 4, 5, 6
    const uint8_t r = f * 3;                     // filas 0, 3 y 6
    b[r]     = (izq ? IZQ_LLENO : IZQ_VACIO) | (der ? DER_LLENO : DER_VACIO);
    b[r + 1] = (izq ? IZQ_LLENO : 0)         | (der ? DER_LLENO : 0);
  }
}

// =====================================================================
// 5. PANTALLA LCD
// =====================================================================
void pantallaNueva() {
  memset(P.tras, ' ', sizeof(P.tras));
  memset(P.claveTras, 0, sizeof(P.claveTras));
  P.cursorCol = -1;
  P.cursorFila = -1;
}

static void pCaracter(uint8_t col, uint8_t fila, uint8_t c) {
  if (col < 16 && fila < 2) P.tras[fila][col] = c;
}

// Texto UTF-8, plegado a ASCII. Lo que no cabe se corta.
void pTexto(uint8_t col, uint8_t fila, const char *txt) {
  const char *p = txt;
  uint32_t cp;
  while ((cp = utf8Leer(p)) != 0 && col < 16) pCaracter(col++, fila, (uint8_t)plegar(cp));
}

void pTextoDerecha(uint8_t fila, const char *txt) {
  const size_t n = strlen(txt);
  pTexto((uint8_t)(n >= 16 ? 0 : 16 - n), fila, txt);
}

static int8_t pHueco(uint16_t clave) {
  for (uint8_t s = 0; s < 8; s++) if (P.claveTras[s] == clave) return (int8_t)s;
  for (uint8_t s = 0; s < 8; s++)
    if (P.claveTras[s] == 0 && P.cgram[s] == clave) { P.claveTras[s] = clave; return (int8_t)s; }
  for (uint8_t s = 0; s < 8; s++)
    if (P.claveTras[s] == 0) { P.claveTras[s] = clave; return (int8_t)s; }
  return -1;
}

// Una celda braille en la posicion dada.
void pCelda(uint8_t col, uint8_t fila, uint8_t puntos) {
  const int8_t s = pHueco(0x100 | (puntos & 0x3F));
  pCaracter(col, fila, s >= 0 ? (uint8_t)s : '?');
}

// Una letra, en mayuscula. Si el LCD no la trae (Ñ, Á...), se dibuja.
void pLetra(uint8_t col, uint8_t fila, uint32_t cp) {
  cp = aMayuscula(cp);
  const int8_t g = glifoDe(cp);
  if (g < 0) { pCaracter(col, fila, (uint8_t)plegar(cp)); return; }
  const int8_t s = pHueco(0x200 | (uint8_t)g);
  pCaracter(col, fila, s >= 0 ? (uint8_t)s : (uint8_t)plegar(cp));
}

void pCursor(uint8_t col, uint8_t fila) { P.cursorCol = (int8_t)col; P.cursorFila = (int8_t)fila; }

void pantallaMostrar() {
  // 1) glifos que han cambiado
  for (uint8_t s = 0; s < 8; s++) {
    const uint16_t k = P.claveTras[s];
    if (k == 0 || P.cgram[s] == k) continue;
    uint8_t b[8];
    if (k & 0x100) bitmapCelda((uint8_t)(k & 0x3F), b);
    else           memcpy(b, GLIFOS[k & 0x07], 8);
    lcd.createChar(s, b);
    P.cgram[s] = k;
  }
  // 2) solo los caracteres que cambian, por tramos seguidos
  for (uint8_t f = 0; f < 2; f++) {
    uint8_t c = 0;
    while (c < 16) {
      if (P.frenteValido && P.tras[f][c] == P.frente[f][c]) { c++; continue; }
      lcd.setCursor(c, f);
      while (c < 16 && (!P.frenteValido || P.tras[f][c] != P.frente[f][c])) {
        lcd.write((uint8_t)P.tras[f][c]);
        P.frente[f][c] = P.tras[f][c];
        c++;
      }
    }
  }
  P.frenteValido = true;
  // 3) cursor parpadeante (editor de puntos)
  if (P.cursorCol >= 0) {
    lcd.setCursor((uint8_t)P.cursorCol, (uint8_t)P.cursorFila);
    if (!P.parpadeando) { lcd.blink(); P.parpadeando = true; }
  } else if (P.parpadeando) {
    lcd.noBlink();
    P.parpadeando = false;
  }
}

// Lo que se ve en el LCD, en UTF-8, para el espejo de la pagina web.
static void pantallaTexto(uint8_t fila, char *o, size_t n) {
  size_t k = 0;
  for (uint8_t c = 0; c < 16 && k + 4 < n; c++) {
    const uint8_t b = P.frente[fila][c];
    if (b < 8) {
      const uint16_t clave = P.cgram[b];
      if (clave & 0x100) k += utf8Poner(0x2800 + (clave & 0x3F), o + k);
      else if (clave & 0x200) k += utf8Poner(GLIFO_CP[clave & 0x07], o + k);
      else o[k++] = ' ';
    } else if (b == 0xFF) {
      k += utf8Poner(0x2588, o + k);                // bloque lleno
    } else {
      o[k++] = (char)b;
    }
  }
  o[k] = 0;
}

// ---- Texto que se desplaza (descripciones largas en una linea de 16) ----
void marqIniciar(const char *utf8) {
  const char *p = utf8;
  uint32_t cp;
  uint16_t n = 0;
  while ((cp = utf8Leer(p)) != 0 && n < sizeof(marq.txt) - 1) marq.txt[n++] = plegar(cp);
  marq.txt[n] = 0;
  marq.len = n;
  marq.pos = 0;
  marq.t = millis();
}

void marqDibujar(uint8_t fila) {
  for (uint8_t c = 0; c < 16; c++) {
    const uint16_t i = marq.pos + c;
    pCaracter(c, fila, (i < marq.len) ? (uint8_t)marq.txt[i] : ' ');
  }
}

// true si ha avanzado (hay que repintar)
bool marqTick(uint32_t ahora) {
  if (marq.len <= 16) return false;
  const uint32_t espera = (marq.pos == 0) ? MARQ_PAUSA_MS : MARQ_PASO_MS[aj.velocidad];
  if (desde(marq.t, ahora) < espera) return false;
  marq.t = ahora;
  marq.pos++;
  if (marq.pos > marq.len - 16 + 3) marq.pos = 0;   // al final, un respiro y vuelta a empezar
  return true;
}

// ---- Celda braille fisica (opcional) ----
void celdaFisica(uint8_t puntos) {
#if CELDA_FISICA
  for (uint8_t d = 0; d < 6; d++)
    digitalWrite(CELDA_PINES[d], (puntos & (1 << d)) ? CELDA_ACTIVA : !CELDA_ACTIVA);
#else
  (void)puntos;
#endif
}

// ###################################################################
// ###                                                             ###
// ###   INICIO - CALIBRACION DEL TECLADO ADC                      ###
// ###   Integrada desde BMJ TRIAJE (mismo algoritmo), adaptada a  ###
// ###   5 botones (SW1..SW5) y a pantallas de 16x2.               ###
// ###                                                             ###
// ###################################################################
//
//  Por que se cambio la lectura del codigo base. Tenia dos fallos:
//
//   1) BLOQUEO DEL TECLADO. leerBoton() ponia botonLiberado = false en cada
//      pulsacion, pero solo esperarLiberacion() lo devolvia a true, y a esa
//      solo se la llamaba si el boton tenia algo que hacer en esa pantalla.
//      Pulsar SW2 o SW3 en el menu (o SW1 dentro de un bloque) dejaba
//      botonLiberado en false PARA SIEMPRE: el teclado dejaba de responder.
//
//   2) UNA SOLA LECTURA SIN FILTRO. Una muestra suelta del ADC, tomada justo
//      mientras la tension baja al pulsar, cae en el rango del boton de al
//      lado; y los umbrales fijos no se adaptan a cada teclado.
//
//  Ahora cada lectura es la mediana de 9 conversiones, se filtra, se exige
//  estabilidad (antirrebote) y se genera UN evento por pulsacion. Los rangos
//  de cada boton los mide el asistente y se guardan en la memoria.

// Lectura filtrada: N muestras -> mediana robusta (media de las 3 centrales).
static int16_t keypadReadRawMv() {
  int16_t s[KEY_SAMPLES];
  uint32_t acc = 0;
  for (uint8_t i = 0; i < KEY_SAMPLES; i++) {
    const int bruto = analogRead(ADKEY_PIN);
    acc += bruto;
#if USE_ESP_ADC_CAL
    s[i] = (int16_t)analogReadMilliVolts(ADKEY_PIN);
#else
    s[i] = (int16_t)((bruto * ADC_FULLSCALE_MV) / (float)ADC_MAX_COUNTS);
#endif
  }
  keypad.counts = (uint16_t)(acc / KEY_SAMPLES);
  qsort(s, KEY_SAMPLES, sizeof(int16_t), cmpI16);
  // Dispersion sin la muestra mas alta ni la mas baja: el ADC suelta de vez en
  // cuando UNA muestra disparatada, mas aun con el WiFi encendido.
  keypad.spread = (int16_t)(s[KEY_SAMPLES - 2] - s[1]);
  const uint8_t m = KEY_SAMPLES / 2;
  return (int16_t)((s[m - 1] + s[m] + s[m + 1]) / 3);
}

// Lectura profunda, solo para el asistente: mediana de WIZ_SUBMUESTRAS
// conversiones y su dispersion (percentil 10 a 90).
static int16_t keypadReadDeepMv(int16_t *dispersion) {
  int16_t s[WIZ_SUBMUESTRAS];
  uint32_t acc = 0;
  for (uint8_t i = 0; i < WIZ_SUBMUESTRAS; i++) {
    const int bruto = analogRead(ADKEY_PIN);
    acc += bruto;
#if USE_ESP_ADC_CAL
    s[i] = (int16_t)analogReadMilliVolts(ADKEY_PIN);
#else
    s[i] = (int16_t)((bruto * ADC_FULLSCALE_MV) / (float)ADC_MAX_COUNTS);
#endif
  }
  keypad.counts = (uint16_t)(acc / WIZ_SUBMUESTRAS);
  qsort(s, WIZ_SUBMUESTRAS, sizeof(int16_t), cmpI16);
  if (dispersion) {
    const uint8_t bajo = WIZ_SUBMUESTRAS / 10;
    const uint8_t alto = WIZ_SUBMUESTRAS - 1 - bajo;
    *dispersion = (int16_t)(s[alto] - s[bajo]);
  }
  return s[WIZ_SUBMUESTRAS / 2];
}

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

// Rangos independientes + histeresis + guarda de reposo. Si la tension
// encajase en dos botones (tabla mal puesta) no devuelve ninguno.
static Boton keypadClassify(int16_t mv, Boton held) {
  if (keypad.idleOk && difAbs(mv, keypad.idleMv) < KEY_IDLE_GUARD_MV) return SW_NINGUNO;
  Boton found = SW_NINGUNO;
  uint8_t matches = 0;
  for (uint8_t i = 0; i < KEYPAD_MAP_SIZE; i++) {
    int16_t lo = KEYPAD_MAP[i].mvMin, hi = KEYPAD_MAP[i].mvMax;
    if (lo > hi) continue;
    if (KEYPAD_MAP[i].id == held) { lo -= KEY_HYSTERESIS_MV; hi += KEY_HYSTERESIS_MV; }
    if (mv >= lo && mv <= hi) { found = KEYPAD_MAP[i].id; matches++; }
  }
  return (matches == 1) ? found : SW_NINGUNO;
}

static const char *buttonName(Boton b) { return (b < SW_TOTAL) ? BTN_NOMBRE[b] : "----"; }

// Un evento por pulsacion (flanco), y autorepeticion en SW2/SW3.
Boton keypadPoll() {
  const uint32_t now = millis();
  const int16_t bruto = keypadReadRawMv();

  if (!keypad.emaInit) { keypad.ema = bruto; keypad.emaInit = true; }
  else keypad.ema = KEY_EMA_ALPHA * bruto + (1.0f - KEY_EMA_ALPHA) * keypad.ema;
  keypad.mv = (int16_t)keypad.ema;

  // Pin al aire: baila Y anda cerca de 0 V. Un teclado conectado descansa a
  // ~3,2 V, asi que el ruido normal del ADC nunca lo da por desconectado.
  const bool alAire = (keypad.spread > KEY_SPREAD_MV) && (bruto < KEY_AIRE_MV);
  keypad.saltos = (keypad.saltos << 1) | (alAire ? 1u : 0u);
  const uint8_t bailando = (uint8_t)__builtin_popcount(
      keypad.saltos & ((KEY_SALTOS_VENTANA >= 32) ? 0xFFFFFFFFu
                                                  : ((1u << KEY_SALTOS_VENTANA) - 1u)));
  const bool suelto = (bailando >= KEY_SALTOS_MIN);
  if (suelto != keypad.desconectado) {
    keypad.desconectado = suelto;
    Serial.println(suelto ? F("[TECLADO] La lectura no para de bailar: cable suelto. Se ignoran las teclas.")
                          : F("[TECLADO] Lectura estable otra vez: teclado operativo"));
  }
  if (keypad.desconectado) { keypad.raw = SW_NINGUNO; keypad.stable = SW_NINGUNO; return SW_NINGUNO; }

  const Boton raw = keypadClassify(keypad.mv, keypad.stable);
  if (raw != keypad.raw) { keypad.raw = raw; keypad.lastRawChange = now; }

  Boton ev = SW_NINGUNO;
  const uint32_t needed = (raw == SW_NINGUNO) ? KEY_RELEASE_MS : KEY_DEBOUNCE_MS;
  if (raw != keypad.stable) {
    if (desde(keypad.lastRawChange, now) >= needed) {
      keypad.stable = raw;
      if (raw != SW_NINGUNO) {
        ev = raw;
        keypad.pressStartMs = now;
        keypad.lastRepeatMs = now;
        Serial.printf("[TECLA] %s (%d mV)\n", buttonName(raw), (int)keypad.mv);
      }
    }
  }
#if KEY_REPEAT_ENABLED
  else if (raw == SW2 || raw == SW3) {
    if (desde(keypad.pressStartMs, now) > KEY_REPEAT_DELAY_MS &&
        desde(keypad.lastRepeatMs, now) > KEY_REPEAT_RATE_MS) {
      keypad.lastRepeatMs = now;
      ev = raw;
    }
  }
#endif
  return ev;
}

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

// ---- Calibracion guardada en la memoria del ESP32 (NVS) ----
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
  if (b.magic != CAL_MAGIC || b.n == 0 || b.n > SW_TOTAL) return;
  for (uint8_t i = 0; i < KEYPAD_MAP_SIZE; i++) { KEYPAD_MAP[i].mvMin = 1; KEYPAD_MAP[i].mvMax = 0; }
  for (uint8_t i = 0; i < b.n; i++)
    for (uint8_t k = 0; k < KEYPAD_MAP_SIZE; k++)
      if (KEYPAD_MAP[k].id == b.e[i].id) { KEYPAD_MAP[k].mvMin = b.e[i].mn; KEYPAD_MAP[k].mvMax = b.e[i].mx; }
  keypad.idleMv = b.reposo;
  keypad.idleOk = true;
  Serial.printf("[TECLADO] Calibracion cargada. Reposo %d mV\n", (int)b.reposo);
  for (uint8_t k = 0; k < KEYPAD_MAP_SIZE; k++)
    if (KEYPAD_MAP[k].mvMin <= KEYPAD_MAP[k].mvMax)
      Serial.printf("   %-4s %d..%d mV\n", buttonName(KEYPAD_MAP[k].id),
                    (int)KEYPAD_MAP[k].mvMin, (int)KEYPAD_MAP[k].mvMax);
}

// Guarda y lo COMPRUEBA releyendo.
bool keypadSaveCalibration() {
  if (!nvsOk) { Serial.println(F("[TECLADO] NO se guarda: la memoria no responde")); return false; }
  CalBlob b;
  memset(&b, 0, sizeof(b));
  b.magic = CAL_MAGIC;
  b.reposo = keypad.idleMv;
  for (uint8_t k = 0; k < KEYPAD_MAP_SIZE && b.n < SW_TOTAL; k++) {
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
  else    Serial.println(F("[TECLADO] FALLO al guardar la calibracion"));
  return ok;
}

// Al encender: mide el reposo. Si coincide con un boton es que se esta
// MANTENIENDO una tecla: esa es la via de escape para abrir el asistente
// cuando la calibracion guardada quedo mal y no se puede navegar el menu.
Boton keypadMeasureIdle() {
  int16_t m[24];
  for (uint8_t i = 0; i < 24; i++) { m[i] = keypadReadRawMv(); delay(20); }
  qsort(m, 24, sizeof(int16_t), cmpI16);
  const int16_t mediana = m[12];
  const int16_t disp = m[21] - m[2];

  keypad.idleOk = false;
  const Boton coincide = keypadClassify(mediana, SW_NINGUNO);
  keypad.ema = mediana; keypad.emaInit = true; keypad.mv = mediana;
  Serial.printf("[TECLADO] Nivel en reposo: %d mV (ADC %u, dispersion %d mV)\n",
                (int)mediana, (unsigned)keypad.counts, (int)disp);

  keypad.desconectado = (mediana < KEY_AIRE_MV && disp > KEY_AIRE_DISP_MV);
  // El historial de lecturas tiene que decir lo mismo: si empezase vacio,
  // el primer sondeo daria el teclado por conectado y el ruido del pin al
  // aire podria colarse como una pulsacion hasta llenarse la ventana.
  keypad.saltos = keypad.desconectado ? 0xFFFFFFFFu : 0u;
  if (keypad.desconectado) {
    Serial.printf("[TECLADO] Lecturas casi a 0 V y saltando: el teclado NO esta conectado (GPIO%d)\n",
                  (int)ADKEY_PIN);
    keypad.idleMv = mediana;
    keypad.idleOk = true;
    return SW_NINGUNO;
  }
  if (coincide != SW_NINGUNO) {
    Serial.printf("[TECLADO] Coincide con %s: hay un boton pulsado al arrancar\n", buttonName(coincide));
    return coincide;
  }
  keypad.idleMv = mediana;
  keypad.idleOk = true;
  return SW_NINGUNO;
}

// ---- Asistente de calibracion ----
void keypadWizardStart() {
  memset(&wiz, 0, sizeof(wiz));
  wiz.t0 = millis();
  wiz.estableDesde = wiz.t0;
  wiz.ultimo = keypad.mv;
  keypad.idleOk = false;
  Serial.println(F("\n[ASISTENTE] Calibracion del teclado. Suelta todos los botones..."));
}

// true cuando ha terminado (y ya ha guardado)
bool keypadWizardStep(uint32_t now) {
  int16_t dispAhora = 0;
  const int16_t mv = keypadReadDeepMv(&dispAhora);
  keypad.ema = KEY_EMA_ALPHA * mv + (1.0f - KEY_EMA_ALPHA) * keypad.ema;
  keypad.mv = (int16_t)keypad.ema;
  if (difAbs(keypad.mv, wiz.ultimo) > WIZ_TOLER_MV) { wiz.ultimo = keypad.mv; wiz.estableDesde = now; }

  switch (wiz.fase) {
    case 0: {
      // Reposo. Al asistente se entra con una tecla pulsada (la de escape o
      // la de "Calibrar"), asi que primero hay que esperar a que se suelte.
      const bool pareceBoton = (keypadClassify(keypad.mv, SW_NINGUNO) != SW_NINGUNO);
      wiz.esperandoSoltar = pareceBoton && desde(wiz.t0, now) < WIZ_SOLTAR_MS;
      if (wiz.esperandoSoltar || desde(wiz.estableDesde, now) < WIZ_REPOSO_MS) { wiz.nMuestras = 0; break; }
      if (wiz.nMuestras < WIZ_MUESTRAS) wiz.muestras[wiz.nMuestras++] = mv;
      if (wiz.nMuestras >= WIZ_MUESTRAS) {
        keypad.idleMv = medianaYDispersion(wiz.muestras, wiz.nMuestras, &wiz.dispReposo);
        wiz.nMuestras = 0;
        wiz.fase = 1; wiz.paso = 0; wiz.t0 = now; wiz.estableDesde = now;
        Serial.printf("[ASISTENTE] Reposo = %d mV (baila %d mV)\n", (int)keypad.idleMv, (int)wiz.dispReposo);
      }
      break;
    }
    case 1: {
      const Boton b = BTN_ORDEN[wiz.paso];
      const bool pulsado = difAbs(keypad.mv, keypad.idleMv) >= WIZ_UMBRAL_MV;
      // Si se suelta a mitad, lo acumulado mezclaria boton y reposo: fuera.
      if (!pulsado) wiz.nMuestras = 0;
      else if (desde(wiz.estableDesde, now) >= WIZ_ESTABLE_MS && wiz.nMuestras < WIZ_MUESTRAS)
        wiz.muestras[wiz.nMuestras++] = mv;
      const bool seAcabo = desde(wiz.t0, now) >= WIZ_SALTO_MS;
      if (wiz.nMuestras >= WIZ_MUESTRAS || (seAcabo && wiz.nMuestras >= WIZ_MIN_MUESTRAS)) {
        wiz.centro[b] = medianaYDispersion(wiz.muestras, wiz.nMuestras, &wiz.disp[b]);
        wiz.hecho[b] = true;
        wiz.capturados++;
        Serial.printf("[ASISTENTE] %-4s = %d mV (baila %d mV)\n", buttonName(b),
                      (int)wiz.centro[b], (int)wiz.disp[b]);
        wiz.nMuestras = 0;
        wiz.fase = 2; wiz.t0 = now;
      } else if (seAcabo) {
        Serial.printf("[ASISTENTE] %s omitido (sin pulsacion sostenida)\n", buttonName(b));
        wiz.nMuestras = 0;
        wiz.fase = 2; wiz.t0 = now;
      }
      break;
    }
    case 2:
      if (difAbs(keypad.mv, keypad.idleMv) < WIZ_UMBRAL_MV / 2 || desde(wiz.t0, now) > 8000) {
        wiz.paso++;
        if (wiz.paso >= BTN_ORDEN_N) { wiz.fase = 3; wiz.t0 = now; }
        else { wiz.fase = 1; wiz.t0 = now; wiz.estableDesde = now; }
      }
      break;
    case 3:
      // A cada boton, todo el sitio hasta su vecino mas cercano menos la guarda.
      for (uint8_t k = 0; k < KEYPAD_MAP_SIZE; k++) { KEYPAD_MAP[k].mvMin = 1; KEYPAD_MAP[k].mvMax = 0; }
      wiz.aviso[0] = '\0';
      for (uint8_t k = 0; k < KEYPAD_MAP_SIZE; k++) {
        const Boton b = KEYPAD_MAP[k].id;
        if (!wiz.hecho[b]) continue;
        int16_t hueco = difAbs(wiz.centro[b], keypad.idleMv);
        for (uint8_t j = 0; j < KEYPAD_MAP_SIZE; j++) {
          const Boton o = KEYPAD_MAP[j].id;
          if (o == b || !wiz.hecho[o]) continue;
          const int16_t d = difAbs(wiz.centro[b], wiz.centro[o]);
          if (d < hueco) hueco = d;
        }
        int16_t medio = hueco / 2 - WIZ_SEPARACION;
        if (medio > WIZ_MARGEN_MAX) medio = WIZ_MARGEN_MAX;
        if (medio < WIZ_MARGEN_ABS_MIN) {
          snprintf(wiz.aviso, sizeof(wiz.aviso), "%s se confunde", buttonName(b));
          Serial.printf("[ASISTENTE] %s descartado: solo %d mV hasta su vecino\n", buttonName(b), (int)hueco);
          continue;
        }
        const int16_t necesario = (int16_t)((wiz.disp[b] * WIZ_DISP_FACTOR) / 2);
        if (medio < necesario || medio < WIZ_MARGEN_MIN) {
          snprintf(wiz.aviso, sizeof(wiz.aviso), "%s justo %dmV", buttonName(b), (int)medio);
          Serial.printf("[ASISTENTE] %s: rango de +-%d mV y baila %d mV. Va justo.\n",
                        buttonName(b), (int)medio, (int)wiz.disp[b]);
        }
        wiz.ancho[b] = medio;
        KEYPAD_MAP[k].mvMin = wiz.centro[b] - medio;
        KEYPAD_MAP[k].mvMax = wiz.centro[b] + medio;
        Serial.printf("[ASISTENTE] %-4s rango %d..%d mV\n", buttonName(b),
                      (int)KEYPAD_MAP[k].mvMin, (int)KEYPAD_MAP[k].mvMax);
      }
      if (keypadActiveCount() == 0) {
        keypadRestoreDefaults();
        snprintf(wiz.aviso, sizeof(wiz.aviso), "Revisa el cable");
        wiz.guardado = false;
      } else {
        wiz.guardado = keypadSaveCalibration();
        if (!wiz.guardado) snprintf(wiz.aviso, sizeof(wiz.aviso), "NO se guardo");
      }
      keypad.idleOk = true;
      wiz.fase = 4; wiz.t0 = now;
      break;
    default:
      return desde(wiz.t0, now) > (wiz.guardado ? 3000UL : 6000UL);
  }
  return false;
}

// Pantallas del asistente en 16x2. La primera linea no cambia mientras se
// mide un boton ("MANTEN: SW1"): que se sepa que hay que seguir pulsando.
void dibujarAsistente() {
  char l[20];
  pantallaNueva();
  switch (wiz.fase) {
    case 0:
      pTexto(0, 0, "CALIBRAR TECLADO");
      if (wiz.esperandoSoltar) pTexto(0, 1, "Suelta botones..");
      else if (wiz.nMuestras == 0) pTexto(0, 1, "No toques nada");
      else { snprintf(l, sizeof(l), "Reposo %2u/%u", (unsigned)wiz.nMuestras, (unsigned)WIZ_MUESTRAS); pTexto(0, 1, l); }
      break;
    case 1: {
      snprintf(l, sizeof(l), "MANTEN: %s", buttonName(BTN_ORDEN[wiz.paso]));
      pTexto(0, 0, l);
      snprintf(l, sizeof(l), "%u/%u", (unsigned)(wiz.paso + 1), (unsigned)BTN_ORDEN_N);
      pTextoDerecha(0, l);
      const uint32_t t = desde(wiz.t0, millis());
      const unsigned resta = (t < WIZ_SALTO_MS) ? (unsigned)((WIZ_SALTO_MS - t) / 1000) : 0u;
      if (wiz.nMuestras) snprintf(l, sizeof(l), "%4dmV med %2u/64", (int)keypad.mv, (unsigned)wiz.nMuestras);
      else               snprintf(l, sizeof(l), "%4dmV omite%3us", (int)keypad.mv, resta);
      pTexto(0, 1, l);
      break;
    }
    case 2:
      pTexto(0, 0, "Suelta el boton");
      snprintf(l, sizeof(l), "%4dmV", (int)keypad.mv);
      pTexto(0, 1, l);
      break;
    case 3:
      pTexto(0, 0, "Calculando...");
      break;
    default: {
      snprintf(l, sizeof(l), "%u de %u botones", (unsigned)wiz.capturados, (unsigned)BTN_ORDEN_N);
      pTexto(0, 0, l);
      int16_t peor = 0;
      for (uint8_t b = SW1; b < SW_TOTAL; b++)
        if (wiz.hecho[b] && wiz.ancho[b] > 0 && (peor == 0 || wiz.ancho[b] < peor)) peor = wiz.ancho[b];
      if (wiz.guardado && !wiz.aviso[0]) { snprintf(l, sizeof(l), "Guardado +-%dmV", (int)peor); pTexto(0, 1, l); }
      else pTexto(0, 1, wiz.aviso[0] ? wiz.aviso : "Sin guardar");
      break;
    }
  }
  pantallaMostrar();
}

// leerBoton(): la MISMA funcion del codigo base (0 = nada, 1..5 = SW1..SW5),
// pero por dentro usa el teclado calibrado. Da un evento por pulsacion.
int leerBoton() {
  return (int)keypadPoll();
}

// En el codigo base esperaba (bloqueando) a que se soltara el boton. Ya no
// hace falta: keypadPoll() solo da un evento por pulsacion y no vuelve a dar
// otro hasta que se suelta. Se deja vacia para no tocar el flujo del menu, y
// porque bloquear aqui dejaria la pagina web sin atender.
void esperarLiberacion() {}

// ###################################################################
// ###   FIN - CALIBRACION DEL TECLADO ADC                         ###
// ###################################################################

// =====================================================================
// 6. MEMORIA: AJUSTES, PROGRESO, SEÑAS Y PALABRAS
// =====================================================================
void ajustesPorDefecto() {
  memset(&aj, 0, sizeof(aj));
  aj.magic = AJ_MAGIC;
  aj.preguntas = 10;
  aj.velocidad = 1;
  aj.nivel = 3;
  const uint64_t mac = ESP.getEfuseMac();
  snprintf(aj.ssid, sizeof(aj.ssid), "%s%04X", AP_PREFIJO, (unsigned)((mac >> 32) & 0xFFFF));
  snprintf(aj.clave, sizeof(aj.clave), "%s", AP_CLAVE);
}

void cargarAjustes() {
  ajustesPorDefecto();
  if (!nvsOk || prefs.getBytesLength("ajustes") != sizeof(aj)) return;
  Ajustes a;
  prefs.getBytes("ajustes", &a, sizeof(a));
  if (a.magic != AJ_MAGIC) return;
  a.ssid[sizeof(a.ssid) - 1] = 0;
  a.clave[sizeof(a.clave) - 1] = 0;
  if (a.preguntas != 5 && a.preguntas != 10 && a.preguntas != 20) a.preguntas = 10;
  if (a.velocidad > 2) a.velocidad = 1;
  if (a.nivel < 1 || a.nivel > 4) a.nivel = 3;
  if (!a.ssid[0]) memcpy(a.ssid, aj.ssid, sizeof(a.ssid));
  aj = a;
}

void guardarAjustes() {
  if (nvsOk) prefs.putBytes("ajustes", &aj, sizeof(aj));
}

void cargarProgreso() {
  memset(&prog, 0, sizeof(prog));
  prog.magic = PROG_MAGIC;
  if (!nvsOk || prefs.getBytesLength("progreso") != sizeof(prog)) return;
  Progreso p;
  prefs.getBytes("progreso", &p, sizeof(p));
  if (p.magic == PROG_MAGIC) prog = p;
}

void guardarProgreso() {
  if (nvsOk) prefs.putBytes("progreso", &prog, sizeof(prog));
}

void borrarProgreso() {
  memset(&prog, 0, sizeof(prog));
  prog.magic = PROG_MAGIC;
  guardarProgreso();
  Serial.println(F("[PROGRESO] Borrado"));
}

// Las descripciones de señas se guardan solo si se han reescrito: la clave
// "s<n>" no existe = se usa la de SENAS_DEF.
void cargarSenas() {
  for (uint8_t i = 0; i < N_LETRAS; i++) {
    char clave[6];
    snprintf(clave, sizeof(clave), "s%u", (unsigned)i);
    const size_t n = nvsOk ? prefs.getBytesLength(clave) : 0;
    if (n > 1 && n <= SENA_MAX + 1) {
      prefs.getBytes(clave, senaTexto[i], n);
      senaTexto[i][SENA_MAX] = 0;
    } else {
      snprintf(senaTexto[i], sizeof(senaTexto[i]), "%s", SENAS_DEF[i]);
    }
  }
}

bool senaPropia(uint8_t i) {
  return strcmp(senaTexto[i], SENAS_DEF[i]) != 0;
}

void guardarSena(uint8_t i, const char *txt) {
  char clave[6];
  snprintf(clave, sizeof(clave), "s%u", (unsigned)i);
  if (!txt || !txt[0]) {                         // vacia = volver a la de referencia
    snprintf(senaTexto[i], sizeof(senaTexto[i]), "%s", SENAS_DEF[i]);
    if (nvsOk) prefs.remove(clave);
    return;
  }
  snprintf(senaTexto[i], sizeof(senaTexto[i]), "%s", txt);
  if (nvsOk) prefs.putBytes(clave, senaTexto[i], strlen(senaTexto[i]) + 1);
}

void cargarPalabras() {
  snprintf(palabrasLista, sizeof(palabrasLista), "%s", PALABRAS_DEF);
  const size_t n = nvsOk ? prefs.getBytesLength("palabras") : 0;
  if (n > 1 && n <= PALABRAS_MAX + 1) {
    prefs.getBytes("palabras", palabrasLista, n);
    palabrasLista[PALABRAS_MAX] = 0;
  }
}

void guardarPalabras(const char *lista) {
  if (!lista || !lista[0]) {
    snprintf(palabrasLista, sizeof(palabrasLista), "%s", PALABRAS_DEF);
    if (nvsOk) prefs.remove("palabras");
    return;
  }
  snprintf(palabrasLista, sizeof(palabrasLista), "%s", lista);
  if (nvsOk) prefs.putBytes("palabras", palabrasLista, strlen(palabrasLista) + 1);
}

uint16_t palabrasCuantas() {
  uint16_t n = 0;
  bool dentro = false;
  for (const char *p = palabrasLista; *p; p++) {
    if (*p == ',' || *p == '\n') { dentro = false; continue; }
    if (*p != ' ' && !dentro) { dentro = true; n++; }
  }
  return n;
}

// Copia la palabra numero i de la lista (sin espacios alrededor).
bool palabraN(uint16_t i, char *o, size_t n) {
  uint16_t k = 0;
  const char *p = palabrasLista;
  while (*p) {
    while (*p == ',' || *p == ' ' || *p == '\n') p++;
    if (!*p) break;
    const char *ini = p;
    while (*p && *p != ',' && *p != '\n') p++;
    const char *fin = p;
    while (fin > ini && fin[-1] == ' ') fin--;
    if (k == i) {
      size_t len = (size_t)(fin - ini);
      if (len >= n) len = n - 1;
      memcpy(o, ini, len);
      o[len] = 0;
      return true;
    }
    k++;
  }
  o[0] = 0;
  return false;
}

// =====================================================================
// 7. MODOS DE APRENDIZAJE
// =====================================================================
static void registrarVisto(uint8_t i) {
  if (i < N_APRENDER && !prog.visto[i]) { prog.visto[i] = 1; guardarProgreso(); }
}

static bool enNivel(uint8_t i, uint8_t nivel) {
  switch (nivel) {
    case 1:  return i <= 9;                     // a-j: 1ª serie
    case 2:  return i <= 20 && i != 14;         // a-t: 1ª y 2ª serie (sin ñ)
    case 3:  return i < N_LETRAS;               // abecedario completo
    default: return i < N_APRENDER;             // + vocales acentuadas
  }
}

// ---- Pantallas que ya estaban en el codigo base ----
// El codigo base ponia "BRAILLEXA": el proyecto es DOTLY.
void mostrarBienvenida() {
  pantallaNueva();
  pTexto(0, 0, "   BIENVENIDO   ");
  pTexto(0, 1, "     DOTLY      ");
  pantallaMostrar();
  delay(1000);

  // DOTLY escrito en braille, cada celda debajo de su letra
  pantallaNueva();
  const char *nombre = "DOTLY";
  for (uint8_t i = 0; i < 5; i++) {
    const uint8_t col = 3 + i * 2;
    pLetra(col, 0, (uint8_t)nombre[i]);
    const int16_t s = buscarSigno((uint8_t)nombre[i]);
    if (s >= 0) pCelda(col, 1, SIGNOS[s].puntos);
  }
  pantallaMostrar();
  delay(1000);
}

void mostrarMenuBloques() {
  pantallaNueva();
  pTexto(0, 0, "SELECCIONA");
  char l[20];
  snprintf(l, sizeof(l), "> %s", BLOQUES[bloqueSel].nombre);
  pTexto(0, 1, l);
  pantallaMostrar();
}

// Antes mostrarBloque1(): ahora pinta el bloque elegido. El BLOQUE 1 sale
// igual que en el codigo base: "BLOQUE 1" / "A B C D E F".
void mostrarBloque() {
  const Bloque &b = BLOQUES[bloqueSel];
  pantallaNueva();
  pTexto(0, 0, b.nombre);
  for (uint8_t i = 0; i < b.cuantos; i++)
    pLetra(i * b.paso, 1, SIGNOS[b.desde + i].cp);
  pantallaMostrar();
  celdaFisica(0);
}

// ---- Detalle de una letra ----
void dibujarLetra() {
  const Bloque &b = BLOQUES[bloqueSel];
  const uint8_t i = b.desde + letraSel;
  const Signo &s = SIGNOS[i];
  char l[24], pts[16];
  pantallaNueva();
  pLetra(0, 0, s.cp);
  if (s.tipo == T_NUMERO) { pCelda(3, 0, SIGNO_NUMERO); pCelda(4, 0, s.puntos); }
  else pCelda(3, 0, s.puntos);
  snprintf(l, sizeof(l), "%u/%u", (unsigned)(letraSel + 1), (unsigned)b.cuantos);
  pTextoDerecha(0, l);
  textoPuntos(s.puntos, pts, sizeof(pts));
  if (s.tipo == T_NUMERO) snprintf(l, sizeof(l), "3456 + %s", pts);
  else                    snprintf(l, sizeof(l), "Puntos %s", pts);
  pTexto(0, 1, l);
  pantallaMostrar();
  celdaFisica(s.puntos);
  registrarVisto(i);
}

// ---- Menu principal ----
void dibujarMenuPrincipal() {
  char l[20];
  pantallaNueva();
  pTexto(0, 0, "DOTLY");
  snprintf(l, sizeof(l), "%u/%u", (unsigned)(menuSel + 1), (unsigned)MENU_N);
  pTextoDerecha(0, l);
  snprintf(l, sizeof(l), "> %s", MENU_TXT[menuSel]);
  pTexto(0, 1, l);
  pantallaMostrar();
  celdaFisica(0);
}

// ---- Editor de puntos (Escribir y Reto: formar) ----
//  "1 2 3 4 5 6 <": los puntos marcados salen como un bloque lleno y el cursor
//  parpadea sobre el que se va a cambiar. '<' borra la ultima letra.
static void dibujarEditor(uint8_t fila, bool conBorrar) {
  for (uint8_t d = 0; d < 6; d++)
    pCaracter(d * 2, fila, (editorPuntos & (1 << d)) ? 0xFF : (uint8_t)('1' + d));
  if (conBorrar) pCaracter(12, fila, '<');
  pCursor(editorCursor < 6 ? editorCursor * 2 : 12, fila);
}

static void editorTecla(int boton, bool conBorrar) {
  const uint8_t posiciones = conBorrar ? 7 : 6;
  if (boton == SW2) editorCursor = (editorCursor + posiciones - 1) % posiciones;
  if (boton == SW3) editorCursor = (editorCursor + 1) % posiciones;
  if (boton == SW1 && editorCursor < 6) editorPuntos ^= (uint8_t)(1 << editorCursor);
}

// ---- Escribir: maquina de escribir braille ----
void dibujarEscribir() {
  pantallaNueva();
  const uint8_t visibles = 13;
  const uint8_t desdeCar = (escritoN > visibles) ? escritoN - visibles : 0;
  uint8_t col = 0;
  for (uint8_t i = desdeCar; i < escritoN; i++) {
    if (escrito[i] == ' ') pCaracter(col, 0, ' ');
    else pLetra(col, 0, escrito[i]);
    col++;
  }
  pCaracter(col, 0, '_');
  if (escritoNumero) pCaracter(14, 0, '#');
  pCelda(15, 0, editorPuntos);
  dibujarEditor(1, true);
  pCaracter(14, 1, '=');
  if (editorPuntos == 0) pCaracter(15, 1, ' ');
  else if (editorPuntos == SIGNO_NUMERO) pCaracter(15, 1, '#');
  else if (editorPuntos == SIGNO_MAYUSCULA) pCaracter(15, 1, '^');
  else {
    const int16_t s = signoDePuntos(editorPuntos, escritoNumero);
    if (s >= 0) pLetra(15, 1, SIGNOS[s].cp);
    else pCaracter(15, 1, '?');
  }
  pantallaMostrar();
  celdaFisica(editorPuntos);
}

// "Escribe" lo que hay en el editor. false si la combinacion no existe.
bool escribirCelda() {
  const uint8_t p = editorPuntos;
  if (p == SIGNO_NUMERO)    { escritoNumero = true; editorPuntos = 0; editorCursor = 0; return true; }
  if (p == SIGNO_MAYUSCULA) { editorPuntos = 0; editorCursor = 0; return true; }  // todo va en mayuscula
  if (escritoN >= ESCRITO_MAX) return false;
  if (p == 0) {
    escrito[escritoN++] = ' ';
    escritoNumero = false;
  } else {
    const int16_t s = signoDePuntos(p, escritoNumero);
    if (s < 0) return false;
    if (SIGNOS[s].tipo != T_NUMERO) escritoNumero = false;
    escrito[escritoN++] = (uint16_t)aMayuscula(SIGNOS[s].cp);
  }
  editorPuntos = 0;
  editorCursor = 0;
  return true;
}

// Aviso visual de error: la luz de fondo parpadea (no hace falta zumbador).
void parpadeoError() {
  lcd.noBacklight(); delay(90); lcd.backlight(); delay(90);
  lcd.noBacklight(); delay(90); lcd.backlight();
}

// ---- Retos ----
//  Tres clases: leer (se ve la celda, se elige la letra), formar (se pide la
//  letra, se marcan los puntos) y señas (se lee como se hace la seña, se
//  elige la letra). Cada letra lleva su cuenta de aciertos por separado.
static bool retoConOpciones() { return reto.tipo != RT_FORMAR; }

static uint16_t *retoTot(uint8_t i) {
  if (reto.tipo == RT_LEER)   return &prog.leerTot[i];
  if (reto.tipo == RT_FORMAR) return &prog.formarTot[i];
  return &prog.senasTot[i];
}
static uint16_t *retoOk(uint8_t i) {
  if (reto.tipo == RT_LEER)   return &prog.leerOk[i];
  if (reto.tipo == RT_FORMAR) return &prog.formarOk[i];
  return &prog.senasOk[i];
}

// Que letras entran en el reto: las del nivel elegido. Las señas son solo del
// abecedario (las vocales con tilde se deletrean igual que sin ella).
static bool entraEnReto(uint8_t i) {
  if (reto.tipo == RT_SENAS && i >= N_LETRAS) return false;
  return enNivel(i, aj.nivel);
}

static uint8_t elegirObjetivo() {
  uint16_t pesos[N_APRENDER];
  uint32_t total = 0;
  for (uint8_t pasada = 0; pasada < 2 && total == 0; pasada++) {
    for (uint8_t i = 0; i < N_APRENDER; i++) {
      pesos[i] = 0;
      if (!entraEnReto(i)) continue;
      if (pasada == 0 && i == reto.anterior) continue;          // no repetir seguida
      const uint16_t tot = *retoTot(i), ok = *retoOk(i);
      // Las que fallan salen mas; las que nunca han salido, tambien.
      uint32_t w = 2 + (uint32_t)(tot - ok) * 3 + (tot == 0 ? 3 : 0);
      if (w > 40) w = 40;
      pesos[i] = (uint16_t)w;
      total += w;
    }
  }
  uint32_t r = (uint32_t)random((long)total);
  for (uint8_t i = 0; i < N_APRENDER; i++) {
    if (r < pesos[i]) return i;
    r -= pesos[i];
  }
  return 0;
}

// Tres opciones falsas. Leyendo braille, las MAS PARECIDAS a la buena (menos
// puntos de diferencia): asi el reto obliga a fijarse en cada punto. En
// señas no hay forma de medir el parecido: se eligen al azar.
static void elegirOpciones() {
  uint8_t cand[N_APRENDER];
  uint16_t clave[N_APRENDER];
  uint8_t nc = 0;
  const uint8_t objetivo = SIGNOS[reto.objetivo].puntos;
  for (uint8_t i = 0; i < N_APRENDER; i++) {
    if (i == reto.objetivo || !entraEnReto(i)) continue;
    cand[nc] = i;
    const uint16_t parecido = (reto.tipo == RT_LEER) ? (uint16_t)__builtin_popcount(SIGNOS[i].puntos ^ objetivo) : 0;
    clave[nc] = (uint16_t)(parecido * 256 + random(256));
    nc++;
  }
  for (uint8_t a = 1; a < nc; a++) {                       // orden por parecido
    const uint8_t ci = cand[a]; const uint16_t ki = clave[a];
    int8_t b = (int8_t)a - 1;
    while (b >= 0 && clave[b] > ki) { cand[b + 1] = cand[b]; clave[b + 1] = clave[b]; b--; }
    cand[b + 1] = ci; clave[b + 1] = ki;
  }
  reto.nOpciones = 0;
  reto.opciones[reto.nOpciones++] = reto.objetivo;
  for (uint8_t k = 0; k < nc && reto.nOpciones < 4; k++) reto.opciones[reto.nOpciones++] = cand[k];
  for (uint8_t a = reto.nOpciones - 1; a > 0; a--) {       // barajar
    const uint8_t b = (uint8_t)random(a + 1);
    const uint8_t t = reto.opciones[a]; reto.opciones[a] = reto.opciones[b]; reto.opciones[b] = t;
  }
  reto.sel = 0;
}

void retoSiguiente() {
  reto.anterior = reto.objetivo;
  reto.objetivo = elegirObjetivo();
  reto.respondido = false;
  editorPuntos = 0;
  editorCursor = 0;
  if (retoConOpciones()) elegirOpciones();
  if (reto.tipo == RT_SENAS) marqIniciar(senaTexto[reto.objetivo]);
}

void retoEmpezar(uint8_t tipo) {
  memset(&reto, 0, sizeof(reto));
  reto.tipo = tipo;
  reto.total = aj.preguntas;
  reto.anterior = 0xFF;
  reto.pregunta = 1;
  retoSiguiente();
}

void retoResponder(bool acierto) {
  const uint8_t i = reto.objetivo;
  (*retoTot(i))++;
  if (acierto) (*retoOk(i))++;
  reto.respondido = true;
  reto.acerto = acierto;
  reto.tRespuesta = millis();
  if (acierto) {
    reto.aciertos++;
    reto.racha++;
    if (reto.racha > prog.rachaMax) prog.rachaMax = reto.racha;
  } else {
    reto.racha = 0;
    parpadeoError();
  }
  Serial.printf("[RETO] %s: %s\n", SIGNOS[i].etiqueta, acierto ? "bien" : "mal");
}

static void dibujarOpciones(uint8_t fila) {
  for (uint8_t k = 0; k < reto.nOpciones; k++) {
    if (k == reto.sel) pCaracter(k * 4, fila, '>');
    pLetra(k * 4 + 1, fila, SIGNOS[reto.opciones[k]].cp);
  }
}

void dibujarReto() {
  char l[24], pts[16];
  const Signo &s = SIGNOS[reto.objetivo];
  pantallaNueva();

  if (reto.tipo == RT_SENAS) {
    // Arriba, como se hace la seña (se desplaza); abajo, las cuatro letras.
    if (!reto.respondido) {
      marqDibujar(0);
      dibujarOpciones(1);
      snprintf(l, sizeof(l), "%u", (unsigned)reto.pregunta);
      pTextoDerecha(1, l);
    } else {
      if (reto.acerto) snprintf(l, sizeof(l), "Bien! Racha %u", (unsigned)reto.racha);
      else             snprintf(l, sizeof(l), "Era la");
      pTexto(0, 0, l);
      if (!reto.acerto) pLetra(7, 0, s.cp);
      pLetra(0, 1, s.cp);
      pCelda(2, 1, s.puntos);
      snprintf(l, sizeof(l), "%u/%u", (unsigned)reto.pregunta, (unsigned)reto.total);
      pTextoDerecha(1, l);
    }
    pantallaMostrar();
    celdaFisica(reto.respondido ? s.puntos : 0);
    return;
  }

  if (reto.tipo == RT_LEER) {
    // "⠓ = ?        3/10": la celda, y abajo las cuatro letras
    pCelda(0, 0, s.puntos);
    pTexto(2, 0, "= ?");
    snprintf(l, sizeof(l), "%u/%u", (unsigned)reto.pregunta, (unsigned)reto.total);
    pTextoDerecha(0, l);
    celdaFisica(s.puntos);
  } else {
    pTexto(0, 0, "Forma:");
    pLetra(7, 0, s.cp);
    snprintf(l, sizeof(l), "%u/%u", (unsigned)reto.pregunta, (unsigned)reto.total);
    pTextoDerecha(0, l);
    celdaFisica(editorPuntos);
  }
  if (reto.respondido) {
    textoPuntos(s.puntos, pts, sizeof(pts));
    if (reto.acerto) snprintf(l, sizeof(l), "Bien! Racha %u", (unsigned)reto.racha);
    else             snprintf(l, sizeof(l), "Era  : %s", pts);
    pTexto(0, 1, l);
    if (!reto.acerto) pLetra(4, 1, s.cp);
  } else if (reto.tipo == RT_LEER) {
    dibujarOpciones(1);
  } else {
    dibujarEditor(1, false);
    pCelda(15, 1, editorPuntos);
  }
  pantallaMostrar();
}

void dibujarResumen() {
  char l[24];
  pantallaNueva();
  snprintf(l, sizeof(l), "Resultado %u/%u", (unsigned)reto.aciertos, (unsigned)reto.total);
  pTexto(0, 0, l);
  pTexto(0, 1, "SW1 otra SW4 sal");
  pantallaMostrar();
  celdaFisica(0);
}

// ---- Palabras ----
static uint8_t celdasPalabra(Celda *c, char *texto, size_t n) {
  palabraN(palabraSel, texto, n);
  return textoACeldas(texto, c, PALABRA_MAX_CELDAS, false);
}

void dibujarPalabra() {
  char texto[40], l[24];
  Celda c[PALABRA_MAX_CELDAS];
  const uint8_t n = celdasPalabra(c, texto, sizeof(texto));
  const uint8_t paginas = (n + 7) / 8;
  if (pagina >= paginas) pagina = 0;
  pantallaNueva();
  if (palabraVista) {
    uint8_t col = 0;
    const char *p = texto;
    uint32_t cp;
    while ((cp = utf8Leer(p)) != 0 && col < 12) pLetra(col++, 0, cp);
  } else {
    pTexto(0, 0, "Lee la palabra");
  }
  if (paginas > 1) { snprintf(l, sizeof(l), "%u/%u", (unsigned)(pagina + 1), (unsigned)paginas); pTextoDerecha(0, l); }
  for (uint8_t k = 0; k < 8 && pagina * 8 + k < n; k++) pCelda(k * 2, 1, c[pagina * 8 + k].puntos);
  pantallaMostrar();
  celdaFisica(0);
}

void palabraOtra(int delta) {
  const uint16_t total = palabrasCuantas();
  if (total == 0) return;
  palabraSel = (uint16_t)((palabraSel + total + delta) % total);
  palabraVista = false;
  pagina = 0;
}

// ---- Señas (alfabeto manual) ----
void senaMostrarLetra(uint8_t i) {
  senaSel = i;
  marqIniciar(senaTexto[i]);
}

void dibujarSenas() {
  char l[24];
  pantallaNueva();
  if (deletreoN) {
    // Deletreo de una palabra enviada desde la pagina web
    const Celda &c = deletreo[deletreoPos];
    pLetra(0, 0, c.cp);
    pCelda(2, 0, c.puntos);
    snprintf(l, sizeof(l), "%u/%u", (unsigned)(deletreoPos + 1), (unsigned)deletreoN);
    pTexto(4, 0, l);
    uint8_t col = 9;
    for (uint8_t k = 0; k < deletreoN && col < 16; k++, col++)
      pCaracter(col, 0, (uint8_t)plegar(aMayuscula(deletreo[k].cp)));
  } else {
    pTexto(0, 0, "Senas:");
    pLetra(7, 0, SIGNOS[senaSel].cp);
    pCelda(9, 0, SIGNOS[senaSel].puntos);
    if (senaConMovimiento(senaSel)) pTexto(13, 0, "mov");
  }
  marqDibujar(1);
  pantallaMostrar();
  celdaFisica(deletreoN ? deletreo[deletreoPos].puntos : SIGNOS[senaSel].puntos);
}

void deletreoIr(uint8_t pos) {
  deletreoPos = pos;
  deletreoT = millis();
  const int16_t i = buscarSigno(deletreo[pos].cp);
  if (i >= 0) marqIniciar(senaTexto[i]);
}

// Carga una palabra para deletrear en señas: solo letras (sin acentos).
// Si no hay ninguna letra no se toca nada: lo que se estuviera deletreando sigue.
uint8_t prepararDeletreo(const char *texto) {
  const uint8_t MAX = sizeof(deletreo) / sizeof(deletreo[0]);
  Celda nuevo[MAX];
  uint8_t n = 0;
  const char *p = texto;
  uint32_t cp;
  while ((cp = utf8Leer(p)) != 0 && n < MAX) {
    int16_t i = buscarSigno(cp);
    if (i < 0) continue;
    if (SIGNOS[i].tipo == T_ACENTO) i = buscarSigno((uint8_t)plegar(aMinuscula(cp)));
    if (i < 0 || SIGNOS[i].tipo != T_LETRA) continue;
    nuevo[n++] = { SIGNOS[i].puntos, SIGNOS[i].cp };
  }
  if (!n) return 0;
  memcpy(deletreo, nuevo, n * sizeof(Celda));
  deletreoN = n;
  deletreoIr(0);
  return n;
}

// ---- Progreso ----
static uint16_t suma(const uint16_t *v, uint8_t n) {
  uint32_t s = 0;
  for (uint8_t i = 0; i < n; i++) s += v[i];
  return (uint16_t)(s > 65535 ? 65535 : s);
}

// Las 3 letras que mas cuestan (peor proporcion de aciertos, con intentos).
static uint8_t letrasARepasar(uint8_t *o) {
  uint8_t n = 0;
  bool usado[N_APRENDER] = { false };
  for (uint8_t k = 0; k < 3; k++) {
    int16_t mejor = -1;
    uint16_t peor = 1001;
    for (uint8_t i = 0; i < N_APRENDER; i++) {
      uint16_t tot = prog.leerTot[i] + prog.formarTot[i];
      uint16_t ok = prog.leerOk[i] + prog.formarOk[i];
      if (i < N_LETRAS) { tot += prog.senasTot[i]; ok += prog.senasOk[i]; }
      if (usado[i] || tot < 2) continue;
      const uint16_t tasa = (uint16_t)((uint32_t)ok * 1000 / tot);
      if (tasa < peor && tasa < 800) { peor = tasa; mejor = i; }
    }
    if (mejor < 0) break;
    usado[mejor] = true;
    o[n++] = (uint8_t)mejor;
  }
  return n;
}

#define PROG_PAGINAS 5

static unsigned porcentaje(uint16_t ok, uint16_t tot) {
  return tot ? (unsigned)((uint32_t)ok * 100 / tot) : 0u;
}

void dibujarProgreso() {
  char l[24];
  pantallaNueva();
  switch (pagina) {
    case 0:
      snprintf(l, sizeof(l), "Leer   %3u%%", porcentaje(suma(prog.leerOk, N_APRENDER), suma(prog.leerTot, N_APRENDER)));
      pTexto(0, 0, l);
      snprintf(l, sizeof(l), "Formar %3u%%", porcentaje(suma(prog.formarOk, N_APRENDER), suma(prog.formarTot, N_APRENDER)));
      pTexto(0, 1, l);
      break;
    case 1:
      snprintf(l, sizeof(l), "Senas  %3u%%", porcentaje(suma(prog.senasOk, N_LETRAS), suma(prog.senasTot, N_LETRAS)));
      pTexto(0, 0, l);
      snprintf(l, sizeof(l), "Racha max %u", (unsigned)prog.rachaMax);
      pTexto(0, 1, l);
      break;
    case 2: {
      uint8_t vistas = 0;
      for (uint8_t i = 0; i < N_APRENDER; i++) if (prog.visto[i]) vistas++;
      snprintf(l, sizeof(l), "Vistas %u/%u", (unsigned)vistas, (unsigned)N_APRENDER);
      pTexto(0, 0, l);
      snprintf(l, sizeof(l), "Rondas %u", (unsigned)prog.rondas);
      pTexto(0, 1, l);
      break;
    }
    case 3:
      pTexto(0, 0, "Palabras");
      snprintf(l, sizeof(l), "%lu leidas", (unsigned long)prog.palabras);
      pTexto(0, 1, l);
      break;
    default: {
      pTexto(0, 0, "Repasa:");
      uint8_t r[3];
      const uint8_t n = letrasARepasar(r);
      if (n == 0) pTexto(0, 1, "nada, vas bien!");
      for (uint8_t k = 0; k < n; k++) pLetra(k * 2, 1, SIGNOS[r[k]].cp);
      break;
    }
  }
  snprintf(l, sizeof(l), "%u/%u", (unsigned)(pagina + 1), (unsigned)PROG_PAGINAS);
  pTextoDerecha(0, l);
  pantallaMostrar();
  celdaFisica(0);
}

// ---- Ajustes ----
void dibujarAjustes() {
  char l[24];
  pantallaNueva();
  pTexto(0, 0, "AJUSTES");
  snprintf(l, sizeof(l), "%u/%u", (unsigned)(ajusteSel + 1), (unsigned)AJUSTES_N);
  pTextoDerecha(0, l);
  switch (ajusteSel) {
    case 2:  snprintf(l, sizeof(l), "> Preguntas: %u", (unsigned)aj.preguntas); break;
    case 3:  snprintf(l, sizeof(l), "> Veloc: %s", VELOCIDAD_TXT[aj.velocidad]); break;
    case 4:  snprintf(l, sizeof(l), "> Nivel: %s", NIVEL_TXT[aj.nivel]); break;
    default: snprintf(l, sizeof(l), "> %s", AJUSTES_TXT[ajusteSel]); break;
  }
  pTexto(0, 1, l);
  pantallaMostrar();
}

void dibujarRed() {
  char l[24];
  pantallaNueva();
  if (pagina == 0) {
    pTexto(0, 0, aj.ssid);
    pTexto(0, 1, apOk ? WiFi.softAPIP().toString().c_str() : "WiFi sin arrancar");
  } else if (pagina == 1) {
    pTexto(0, 0, "Clave:");
    pTexto(0, 1, aj.clave[0] ? aj.clave : "(sin clave)");
  } else {
    pTexto(0, 0, "Conectados:");
    snprintf(l, sizeof(l), "%u", (unsigned)(apOk ? WiFi.softAPgetStationNum() : 0));
    pTexto(0, 1, l);
  }
  pantallaMostrar();
}

void dibujarConfirmar() {
  pantallaNueva();
  pTexto(0, 0, "Borrar progreso?");
  pTexto(0, 1, "SW1 si   SW4 no");
  pantallaMostrar();
}

void dibujarAcerca() {
  pantallaNueva();
  const char *nombre = "DOTLY";
  for (uint8_t i = 0; i < 5; i++) {
    pLetra(i * 2, 0, (uint8_t)nombre[i]);
    const int16_t s = buscarSigno((uint8_t)nombre[i]);
    if (s >= 0) pCelda(i * 2, 1, SIGNOS[s].puntos);
  }
  pTexto(10, 0, "punto");
  pTexto(9, 1, "a punto");
  pantallaMostrar();
}

// ---- Pizarra: lo que se manda desde la pagina web ----
void dibujarPizarra() {
  char l[8];
  const uint8_t paginas = pizarraN ? (pizarraN + 7) / 8 : 1;
  if (pagina >= paginas) pagina = 0;
  pantallaNueva();
  for (uint8_t k = 0; k < 8; k++) {
    const uint8_t i = pagina * 8 + k;
    if (i >= pizarraN) break;
    const Celda &c = pizarra[i];
    if (c.cp == '#' || c.cp == '^') pCaracter(k * 2, 0, (uint8_t)c.cp);
    else if (c.cp) pLetra(k * 2, 0, c.cp);
    pCelda(k * 2, 1, c.puntos);
  }
  if (paginas > 1) { snprintf(l, sizeof(l), "%u", (unsigned)(pagina + 1)); pTexto(15, 0, l); }
  pantallaMostrar();
  celdaFisica(pizarraN ? pizarra[pagina * 8].puntos : 0);
}

// ---- Todas las pantallas ----
void mostrar() {
  switch (estado) {
    case BIENVENIDA:       break;
    case MENU_PRINCIPAL:   dibujarMenuPrincipal(); break;
    case MENU_BLOQUES:     mostrarMenuBloques(); break;
    case BLOQUE:           mostrarBloque(); break;
    case LETRA:            dibujarLetra(); break;
    case ESCRIBIR:         dibujarEscribir(); break;
    case RETO_LEER:
    case RETO_FORMAR:
    case RETO_SENAS:       dibujarReto(); break;
    case RETO_RESUMEN:     dibujarResumen(); break;
    case PALABRAS:         dibujarPalabra(); break;
    case SENAS:            dibujarSenas(); break;
    case PROGRESO:         dibujarProgreso(); break;
    case AJUSTES:          dibujarAjustes(); break;
    case AJUSTE_RED:       dibujarRed(); break;
    case CONFIRMAR_BORRAR: dibujarConfirmar(); break;
    case ACERCA:           dibujarAcerca(); break;
    case PIZARRA:          dibujarPizarra(); break;
    case CALIBRAR:         dibujarAsistente(); break;
  }
}

const char *nombreEstado(Estado e) {
  switch (e) {
    case BIENVENIDA:       return "bienvenida";
    case MENU_PRINCIPAL:   return "menu";
    case MENU_BLOQUES:     return "bloques";
    case BLOQUE:           return "bloque";
    case LETRA:            return "letra";
    case ESCRIBIR:         return "escribir";
    case RETO_LEER:        return "reto_leer";
    case RETO_FORMAR:      return "reto_formar";
    case RETO_SENAS:       return "reto_senas";
    case RETO_RESUMEN:     return "resumen";
    case PALABRAS:         return "palabras";
    case SENAS:            return "senas";
    case PROGRESO:         return "progreso";
    case AJUSTES:          return "ajustes";
    case AJUSTE_RED:       return "red";
    case CONFIRMAR_BORRAR: return "confirmar";
    case ACERCA:           return "acerca";
    case PIZARRA:          return "pizarra";
    case CALIBRAR:         return "calibrar";
  }
  return "?";
}

void cambiarEstado(Estado e) {
  if (e != estado) Serial.printf("[UI] -> %s\n", nombreEstado(e));
  estado = e;
  pagina = 0;
  mostrar();
}

// Entrada a cada modo (la usan el menu y la pagina web).
void entrarModo(uint8_t op) {
  switch (op) {
    case OP_APRENDER:    cambiarEstado(MENU_BLOQUES); break;
    case OP_ESCRIBIR:    editorPuntos = 0; editorCursor = 0; cambiarEstado(ESCRIBIR); break;
    case OP_RETO_LEER:   retoEmpezar(RT_LEER);   cambiarEstado(RETO_LEER); break;
    case OP_RETO_FORMAR: retoEmpezar(RT_FORMAR); cambiarEstado(RETO_FORMAR); break;
    case OP_RETO_SENAS:  retoEmpezar(RT_SENAS);  cambiarEstado(RETO_SENAS); break;
    case OP_PALABRAS:
      palabraSel = palabrasCuantas() ? (uint16_t)random((long)palabrasCuantas()) : 0;
      palabraVista = false;
      cambiarEstado(PALABRAS);
      break;
    case OP_SENAS:       deletreoN = 0; senaMostrarLetra(senaSel); cambiarEstado(SENAS); break;
    case OP_PROGRESO:    cambiarEstado(PROGRESO); break;
    default:             ajusteSel = 0; cambiarEstado(AJUSTES); break;
  }
}

void iniciarAsistente() {
  keypadWizardStart();
  cambiarEstado(CALIBRAR);
}

// ---- Teclas de cada modo nuevo ----
void teclaMenuPrincipal(int boton) {
  if (boton == SW2) { menuSel = (menuSel + MENU_N - 1) % MENU_N; dibujarMenuPrincipal(); }
  else if (boton == SW3) { menuSel = (menuSel + 1) % MENU_N; dibujarMenuPrincipal(); }
  else if (boton == SW1) entrarModo(menuSel);
  else if (boton == SW5) dibujarMenuPrincipal();                // repetir
}

void teclaLetra(int boton) {
  const uint8_t n = BLOQUES[bloqueSel].cuantos;
  if (boton == SW2) letraSel = (letraSel + n - 1) % n;
  else if (boton == SW3 || boton == SW1) letraSel = (letraSel + 1) % n;
  else if (boton == SW4) { cambiarEstado(BLOQUE); return; }
  dibujarLetra();                                                 // SW5 = repetir
}

void teclaEscribir(int boton) {
  if (boton == SW4) { cambiarEstado(MENU_PRINCIPAL); return; }
  if (boton == SW1 && editorCursor == 6) {                        // '<' borra la ultima
    if (escritoN) escritoN--;
    if (!escritoN) escritoNumero = false;
  } else if (boton == SW5) {
    if (!escribirCelda()) parpadeoError();
  } else {
    editorTecla(boton, true);
  }
  dibujarEscribir();
}

void avanzarReto();

void teclaReto(int boton) {
  if (boton == SW4) { guardarProgreso(); cambiarEstado(MENU_PRINCIPAL); return; }
  if (reto.respondido) { avanzarReto(); return; }                 // cualquier tecla sigue
  if (retoConOpciones()) {
    if (boton == SW2) reto.sel = (reto.sel + reto.nOpciones - 1) % reto.nOpciones;
    else if (boton == SW3) reto.sel = (reto.sel + 1) % reto.nOpciones;
    else if (boton == SW1) retoResponder(reto.opciones[reto.sel] == reto.objetivo);
    else if (boton == SW5 && reto.tipo == RT_SENAS) marqIniciar(senaTexto[reto.objetivo]);  // repetir
  } else {
    if (boton == SW5) retoResponder(editorPuntos == SIGNOS[reto.objetivo].puntos);
    else editorTecla(boton, false);
  }
  dibujarReto();
}

// Tras enseñar la respuesta: siguiente pregunta, o el resumen de la ronda.
void avanzarReto() {
  if (reto.pregunta >= reto.total) {
    prog.rondas++;
    guardarProgreso();
    cambiarEstado(RETO_RESUMEN);
    return;
  }
  reto.pregunta++;
  retoSiguiente();
  dibujarReto();
}

void teclaResumen(int boton) {
  if (boton == SW1) {
    const uint8_t tipo = reto.tipo;
    retoEmpezar(tipo);
    cambiarEstado(tipo == RT_LEER ? RETO_LEER : tipo == RT_FORMAR ? RETO_FORMAR : RETO_SENAS);
  }
  else if (boton == SW4) cambiarEstado(MENU_PRINCIPAL);
  else dibujarResumen();
}

void teclaPalabras(int boton) {
  if (boton == SW4) { guardarProgreso(); cambiarEstado(MENU_PRINCIPAL); return; }
  if (boton == SW1) {
    if (!palabraVista) { palabraVista = true; prog.palabras++; if (prog.palabras % 5 == 0) guardarProgreso(); }
    else palabraOtra(1);
  } else if (boton == SW3) {
    char texto[40]; Celda c[PALABRA_MAX_CELDAS];
    const uint8_t n = celdasPalabra(c, texto, sizeof(texto));
    if (n > 8 && pagina * 8 + 8 < n) pagina++;                   // palabra larga: siguiente trozo
    else palabraOtra(1);
  } else if (boton == SW2) {
    if (pagina > 0) pagina--;
    else palabraOtra(-1);
  } else if (boton == SW5) {
    palabraVista = false;                                          // repetir: se vuelve a tapar
  }
  dibujarPalabra();
}

void teclaSenas(int boton) {
  if (boton == SW4) {
    if (deletreoN) { deletreoN = 0; senaMostrarLetra(senaSel); dibujarSenas(); }
    else cambiarEstado(MENU_PRINCIPAL);
    return;
  }
  if (deletreoN) {
    if (boton == SW2) deletreoIr((deletreoPos + deletreoN - 1) % deletreoN);
    else if (boton == SW3 || boton == SW1) deletreoIr((deletreoPos + 1) % deletreoN);
    else if (boton == SW5) deletreoIr(deletreoPos);
  } else {
    if (boton == SW2) senaMostrarLetra((senaSel + N_LETRAS - 1) % N_LETRAS);
    else if (boton == SW3 || boton == SW1) senaMostrarLetra((senaSel + 1) % N_LETRAS);
    else if (boton == SW5) senaMostrarLetra(senaSel);
  }
  dibujarSenas();
}

void teclaProgreso(int boton) {
  if (boton == SW4) { cambiarEstado(MENU_PRINCIPAL); return; }
  if (boton == SW2) pagina = (pagina + PROG_PAGINAS - 1) % PROG_PAGINAS;
  if (boton == SW3 || boton == SW1) pagina = (pagina + 1) % PROG_PAGINAS;
  dibujarProgreso();
}

void teclaAjustes(int boton) {
  if (boton == SW4) { cambiarEstado(MENU_PRINCIPAL); return; }
  if (boton == SW2) ajusteSel = (ajusteSel + AJUSTES_N - 1) % AJUSTES_N;
  else if (boton == SW3) ajusteSel = (ajusteSel + 1) % AJUSTES_N;
  else if (boton == SW1) {
    switch (ajusteSel) {
      case 0: iniciarAsistente(); return;
      case 1: cambiarEstado(AJUSTE_RED); return;
      case 2: aj.preguntas = (aj.preguntas == 5) ? 10 : (aj.preguntas == 10) ? 20 : 5; guardarAjustes(); break;
      case 3: aj.velocidad = (aj.velocidad + 1) % 3; guardarAjustes(); break;
      case 4: aj.nivel = (aj.nivel % 4) + 1; guardarAjustes(); break;
      case 5: cambiarEstado(CONFIRMAR_BORRAR); return;
      default: cambiarEstado(ACERCA); return;
    }
  }
  dibujarAjustes();
}

void teclaPizarra(int boton) {
  const uint8_t paginas = pizarraN ? (pizarraN + 7) / 8 : 1;
  if (boton == SW4) { cambiarEstado(MENU_PRINCIPAL); return; }
  if (boton == SW2) pagina = (pagina + paginas - 1) % paginas;
  if (boton == SW3 || boton == SW1) pagina = (pagina + 1) % paginas;
  dibujarPizarra();
}

// ---- Lo que avanza solo, sin pulsar nada ----
void tickEstado(uint32_t ahora) {
  const bool enReto = (estado == RETO_LEER || estado == RETO_FORMAR || estado == RETO_SENAS);
  if (enReto && reto.respondido && desde(reto.tRespuesta, ahora) >= RESPUESTA_MS) {
    avanzarReto();
    return;
  }
  if (estado == RETO_SENAS && !reto.respondido && marqTick(ahora)) dibujarReto();
  if (estado == SENAS) {
    bool repintar = marqTick(ahora);
    if (deletreoN && desde(deletreoT, ahora) >= DELETREO_MS[aj.velocidad]) {
      deletreoIr((deletreoPos + 1) % deletreoN);
      repintar = true;
    }
    if (repintar) dibujarSenas();
  }
}

// ###################################################################
// ###                                                             ###
// ###   INICIO - SERVIDOR WEB EN MODO PUNTO DE ACCESO (AP)        ###
// ###                                                             ###
// ###################################################################
//  DOTLY crea su propia red. Un DNS que contesta a todo con la IP del equipo
//  hace de "portal cautivo": al conectarse, el movil abre la pagina solo.
//  La pagina (pagina_web.h) va dentro del firmware: no hay que subir nada a
//  la memoria aparte. Todo lo demas son peticiones /api/... en JSON.

// ---- JSON a mano (sin librerias) ----
static void jsonTexto(String &o, const char *s) {
  o += '"';
  for (const char *p = s; *p; p++) {
    const char c = *p;
    if (c == '"' || c == '\\') { o += '\\'; o += c; }
    else if (c == '\n') o += "\\n";
    else if ((uint8_t)c < 0x20) o += ' ';
    else o += c;
  }
  o += '"';
}

static void jsonNum(String &o, long v) {
  char b[16];
  snprintf(b, sizeof(b), "%ld", v);
  o += b;
}

static void responderJson(const String &o) {
  webServer.sendHeader("Cache-Control", "no-store");
  webServer.send(200, "application/json", o);
}

static void responderOk() { responderJson(String("{\"ok\":true}")); }

static void responderError(int codigo, const char *msg) {
  String o("{\"ok\":false,\"error\":");
  jsonTexto(o, msg);
  o += '}';
  webServer.send(codigo, "application/json", o);
}

static int argNum(const char *nombre, int porDefecto) {
  if (!webServer.hasArg(nombre)) return porDefecto;
  return atoi(webServer.arg(nombre).c_str());
}

// ---- GET / : la pagina ----
void webPagina() {
  const size_t n = strlen(PAGINA_WEB);
  webServer.sendHeader("Cache-Control", "no-store");
  webServer.setContentLength(CONTENT_LENGTH_UNKNOWN);
  webServer.send(200, "text/html; charset=utf-8", "");
  for (size_t i = 0; i < n; i += 2048) {
    const size_t trozo = (n - i < 2048) ? (n - i) : 2048;
    webServer.sendContent(PAGINA_WEB + i, trozo);
  }
  webServer.sendContent("");
}

// ---- GET /api/estado : lo que pasa ahora (la pagina lo pide cada segundo) ----
void webEstado() {
  char linea[80];
  String o;
  o.reserve(700);
  o += "{\"modo\":";
  jsonTexto(o, nombreEstado(estado));
  o += ",\"pantalla\":[";
  pantallaTexto(0, linea, sizeof(linea)); jsonTexto(o, linea);
  o += ',';
  pantallaTexto(1, linea, sizeof(linea)); jsonTexto(o, linea);
  o += "],\"cursor\":";
  if (P.cursorCol >= 0) { o += '['; jsonNum(o, P.cursorCol); o += ','; jsonNum(o, P.cursorFila); o += ']'; }
  else o += "null";
  // lo escrito en el modo Escribir, en UTF-8
  char esc[ESCRITO_MAX * 3 + 1];
  size_t k = 0;
  for (uint8_t i = 0; i < escritoN; i++) k += utf8Poner(escrito[i], esc + k);
  esc[k] = 0;
  o += ",\"escrito\":"; jsonTexto(o, esc);
  o += ",\"escritoNumero\":"; o += escritoNumero ? "true" : "false";
  o += ",\"teclado\":{\"conectado\":"; o += keypad.desconectado ? "false" : "true";
  o += ",\"mv\":"; jsonNum(o, keypad.mv);
  o += ",\"reposo\":"; jsonNum(o, keypad.idleMv);
  o += ",\"botones\":"; jsonNum(o, keypadActiveCount());
  o += ",\"calibrado\":"; o += keypadHasCalibration() ? "true" : "false";
  o += ",\"rangos\":[";
  for (uint8_t i = 0; i < KEYPAD_MAP_SIZE; i++) {
    if (i) o += ',';
    o += '['; jsonNum(o, KEYPAD_MAP[i].mvMin); o += ','; jsonNum(o, KEYPAD_MAP[i].mvMax); o += ']';
  }
  o += "]},\"red\":{\"ssid\":"; jsonTexto(o, aj.ssid);
  o += ",\"ip\":"; jsonTexto(o, apOk ? WiFi.softAPIP().toString().c_str() : "");
  o += ",\"clientes\":"; jsonNum(o, apOk ? WiFi.softAPgetStationNum() : 0);
  o += "},\"reto\":{\"pregunta\":"; jsonNum(o, reto.pregunta);
  o += ",\"total\":"; jsonNum(o, reto.total);
  o += ",\"aciertos\":"; jsonNum(o, reto.aciertos);
  o += ",\"racha\":"; jsonNum(o, reto.racha);
  o += "},\"bloque\":"; jsonNum(o, bloqueSel);
  o += ",\"encendido\":"; jsonNum(o, (long)(millis() / 1000));
  o += '}';
  responderJson(o);
}

// ---- GET /api/tabla : la signografia (una sola fuente: la del firmware) ----
void webTabla() {
  static const char *const TIPOS[] = { "letra", "acento", "numero", "signo" };
  String o;
  o.reserve(2600);
  o += "{\"numero\":"; jsonNum(o, SIGNO_NUMERO);
  o += ",\"mayuscula\":"; jsonNum(o, SIGNO_MAYUSCULA);
  o += ",\"signos\":[";
  for (uint8_t i = 0; i < N_SIGNOS; i++) {
    if (i) o += ',';
    o += "{\"t\":"; jsonTexto(o, SIGNOS[i].etiqueta);
    o += ",\"p\":"; jsonNum(o, SIGNOS[i].puntos);
    o += ",\"k\":"; jsonTexto(o, TIPOS[SIGNOS[i].tipo]);
    o += '}';
  }
  o += "],\"bloques\":[";
  for (uint8_t b = 0; b < N_BLOQUES; b++) {
    if (b) o += ',';
    o += "{\"n\":"; jsonTexto(o, BLOQUES[b].nombre);
    o += ",\"desde\":"; jsonNum(o, BLOQUES[b].desde);
    o += ",\"cuantos\":"; jsonNum(o, BLOQUES[b].cuantos);
    o += '}';
  }
  o += "]}";
  responderJson(o);
}

// ---- /api/senas : descripciones del alfabeto manual ----
void webSenas() {
  String o;
  o.reserve(3800);
  o += '[';
  for (uint8_t i = 0; i < N_LETRAS; i++) {
    if (i) o += ',';
    o += "{\"l\":"; jsonTexto(o, SIGNOS[i].etiqueta);
    o += ",\"d\":"; jsonTexto(o, senaTexto[i]);
    o += ",\"m\":"; o += senaConMovimiento(i) ? "true" : "false";
    o += ",\"propia\":"; o += senaPropia(i) ? "true" : "false";
    o += '}';
  }
  o += ']';
  responderJson(o);
}

void webSenasGuardar() {
  const int i = argNum("i", -1);
  if (i < 0 || i >= N_LETRAS) { responderError(400, "letra no valida"); return; }
  const String d = webServer.arg("d");
  if (d.length() > SENA_MAX) { responderError(400, "La descripcion es demasiado larga"); return; }
  guardarSena((uint8_t)i, d.c_str());
  if (estado == SENAS && !deletreoN && senaSel == i) { senaMostrarLetra((uint8_t)i); dibujarSenas(); }
  responderOk();
}

// ---- /api/palabras ----
void webPalabras() {
  String o("{\"lista\":");
  jsonTexto(o, palabrasLista);
  o += ",\"cuantas\":"; jsonNum(o, palabrasCuantas());
  o += '}';
  responderJson(o);
}

void webPalabrasGuardar() {
  const String lista = webServer.arg("lista");
  if (lista.length() > PALABRAS_MAX) { responderError(400, "La lista es demasiado larga"); return; }
  guardarPalabras(lista.c_str());
  palabraSel = 0;
  palabraVista = false;
  if (estado == PALABRAS) dibujarPalabra();
  responderOk();
}

// ---- /api/ajustes ----
void webAjustes() {
  String o("{\"preguntas\":");
  jsonNum(o, aj.preguntas);
  o += ",\"velocidad\":"; jsonNum(o, aj.velocidad);
  o += ",\"nivel\":"; jsonNum(o, aj.nivel);
  o += ",\"ssid\":"; jsonTexto(o, aj.ssid);
  o += ",\"clave\":"; jsonTexto(o, aj.clave);
  o += '}';
  responderJson(o);
}

void webAjustesGuardar() {
  const int preguntas = argNum("preguntas", aj.preguntas);
  const int velocidad = argNum("velocidad", aj.velocidad);
  const int nivel = argNum("nivel", aj.nivel);
  if (preguntas == 5 || preguntas == 10 || preguntas == 20) aj.preguntas = (uint8_t)preguntas;
  if (velocidad >= 0 && velocidad <= 2) aj.velocidad = (uint8_t)velocidad;
  if (nivel >= 1 && nivel <= 4) aj.nivel = (uint8_t)nivel;
  bool redCambia = false;
  if (webServer.hasArg("ssid")) {
    const String s = webServer.arg("ssid");
    if (s.length() < 1 || s.length() > 32) { responderError(400, "El nombre de la red debe tener de 1 a 32 caracteres"); return; }
    if (strcmp(s.c_str(), aj.ssid) != 0) { snprintf(aj.ssid, sizeof(aj.ssid), "%s", s.c_str()); redCambia = true; }
  }
  if (webServer.hasArg("clave")) {
    const String c = webServer.arg("clave");
    if (c.length() != 0 && (c.length() < 8 || c.length() > 63)) {
      responderError(400, "La clave debe tener de 8 a 63 caracteres (o dejarse vacia: red abierta)");
      return;
    }
    if (strcmp(c.c_str(), aj.clave) != 0) { snprintf(aj.clave, sizeof(aj.clave), "%s", c.c_str()); redCambia = true; }
  }
  guardarAjustes();
  if (estado == AJUSTES) dibujarAjustes();
  String o("{\"ok\":true,\"reiniciar\":");
  o += redCambia ? "true" : "false";
  o += '}';
  responderJson(o);
}

// ---- /api/progreso ----
void webProgreso() {
  String o;
  o.reserve(2600);
  o += "{\"letras\":[";
  for (uint8_t i = 0; i < N_APRENDER; i++) {
    if (i) o += ',';
    o += "{\"t\":"; jsonTexto(o, SIGNOS[i].etiqueta);
    o += ",\"p\":"; jsonNum(o, SIGNOS[i].puntos);
    o += ",\"lo\":"; jsonNum(o, prog.leerOk[i]);
    o += ",\"lt\":"; jsonNum(o, prog.leerTot[i]);
    o += ",\"fo\":"; jsonNum(o, prog.formarOk[i]);
    o += ",\"ft\":"; jsonNum(o, prog.formarTot[i]);
    o += ",\"so\":"; jsonNum(o, i < N_LETRAS ? prog.senasOk[i] : 0);
    o += ",\"st\":"; jsonNum(o, i < N_LETRAS ? prog.senasTot[i] : 0);
    o += ",\"v\":"; jsonNum(o, prog.visto[i]);
    o += '}';
  }
  o += "],\"rachaMax\":"; jsonNum(o, prog.rachaMax);
  o += ",\"rondas\":"; jsonNum(o, prog.rondas);
  o += ",\"palabras\":"; jsonNum(o, (long)prog.palabras);
  o += '}';
  responderJson(o);
}

void webProgresoBorrar() {
  borrarProgreso();
  if (estado == PROGRESO) dibujarProgreso();
  responderOk();
}

// ---- Mandos: cambiar de modo, pulsar teclas, mandar texto ----
void webModo() {
  if (estado == CALIBRAR) { responderError(409, "DOTLY esta calibrando el teclado"); return; }
  const String m = webServer.arg("m");
  const char *s = m.c_str();
  if (!strcmp(s, "menu"))              cambiarEstado(MENU_PRINCIPAL);
  else if (!strcmp(s, "aprender")) {
    const int b = argNum("b", -1);
    if (b >= 0 && b < N_BLOQUES) { bloqueSel = (uint8_t)b; cambiarEstado(BLOQUE); }
    else entrarModo(OP_APRENDER);
  }
  else if (!strcmp(s, "escribir"))     entrarModo(OP_ESCRIBIR);
  else if (!strcmp(s, "reto_leer"))    entrarModo(OP_RETO_LEER);
  else if (!strcmp(s, "reto_formar"))  entrarModo(OP_RETO_FORMAR);
  else if (!strcmp(s, "reto_senas"))   entrarModo(OP_RETO_SENAS);
  else if (!strcmp(s, "palabras"))     entrarModo(OP_PALABRAS);
  else if (!strcmp(s, "senas"))        entrarModo(OP_SENAS);
  else if (!strcmp(s, "progreso"))     entrarModo(OP_PROGRESO);
  else if (!strcmp(s, "ajustes"))      entrarModo(OP_AJUSTES);
  else { responderError(400, "modo desconocido"); return; }
  responderOk();
}

void webTecla() {
  const int b = argNum("b", 0);
  if (b < SW1 || b > SW5) { responderError(400, "tecla de 1 a 5"); return; }
  if (estado == CALIBRAR) { responderError(409, "DOTLY esta calibrando el teclado"); return; }
  botonWeb = (uint8_t)b;
  responderOk();
}

void webPizarra() {
  if (estado == CALIBRAR) { responderError(409, "DOTLY esta calibrando el teclado"); return; }
  pizarraN = textoACeldas(webServer.arg("t").c_str(), pizarra, PIZARRA_MAX, true);
  if (!pizarraN) { responderError(400, "no hay nada que se pueda escribir en braille"); return; }
  cambiarEstado(PIZARRA);
  responderOk();
}

void webDeletrear() {
  if (estado == CALIBRAR) { responderError(409, "DOTLY esta calibrando el teclado"); return; }
  if (!prepararDeletreo(webServer.arg("t").c_str())) { responderError(400, "no hay letras que deletrear"); return; }
  cambiarEstado(SENAS);
  responderOk();
}

// Escribir desde la pagina: cada celda llega ya formada (teclado Perkins).
void webEscribir() {
  if (estado == CALIBRAR) { responderError(409, "DOTLY esta calibrando el teclado"); return; }
  if (estado != ESCRIBIR) { editorPuntos = 0; editorCursor = 0; cambiarEstado(ESCRIBIR); }
  if (webServer.hasArg("borrar")) {
    if (escritoN) escritoN--;
    if (!escritoN) escritoNumero = false;
  } else {
    const int p = argNum("p", -1);
    if (p < 0 || p > 63) { responderError(400, "puntos de 0 a 63"); return; }
    const uint8_t antes = editorPuntos;
    editorPuntos = (uint8_t)p;
    if (!escribirCelda()) {
      editorPuntos = antes;
      responderError(400, escritoN >= ESCRITO_MAX ? "El texto esta lleno"
                                                  : "Esa combinacion no es ninguna letra");
      return;
    }
  }
  dibujarEscribir();
  responderOk();
}

void webEscritoBorrar() {
  escritoN = 0;
  escritoNumero = false;
  if (estado == ESCRIBIR) dibujarEscribir();
  responderOk();
}

void webCalibrar() {
  iniciarAsistente();
  responderOk();
}

void webReiniciar() {
  responderOk();
  delay(300);
  ESP.restart();
}

// Portal cautivo: cualquier direccion que no sea la nuestra (las que usan los
// moviles para ver si "hay internet") se manda a la pagina de DOTLY.
void webNoEncontrado() {
  const String host = webServer.hostHeader();
  const String ip = WiFi.softAPIP().toString();
  if (host.length() && strcmp(host.c_str(), ip.c_str()) != 0) {
    String destino("http://");
    destino += ip.c_str();
    destino += '/';
    webServer.sendHeader("Location", destino);
    webServer.send(302, "text/plain", "");
    return;
  }
  responderError(404, "no existe");
}

void iniciarRed() {
  WiFi.mode(WIFI_AP);
  const IPAddress ip(192, 168, 4, 1), mascara(255, 255, 255, 0);
  WiFi.softAPConfig(ip, ip, mascara);
  apOk = WiFi.softAP(aj.ssid, aj.clave[0] ? aj.clave : NULL, AP_CANAL, 0, AP_MAX_CLIENTES);
  if (!apOk) {
    Serial.println(F("[RED] No se pudo crear la red WiFi"));
    return;
  }
  dnsServer.start(DNS_PUERTO, "*", WiFi.softAPIP());

  webServer.on("/", webPagina);
  webServer.on("/api/estado", webEstado);
  webServer.on("/api/tabla", webTabla);
  webServer.on("/api/senas", HTTP_GET, webSenas);
  webServer.on("/api/senas", HTTP_POST, webSenasGuardar);
  webServer.on("/api/palabras", HTTP_GET, webPalabras);
  webServer.on("/api/palabras", HTTP_POST, webPalabrasGuardar);
  webServer.on("/api/ajustes", HTTP_GET, webAjustes);
  webServer.on("/api/ajustes", HTTP_POST, webAjustesGuardar);
  webServer.on("/api/progreso", HTTP_GET, webProgreso);
  webServer.on("/api/progreso/borrar", HTTP_POST, webProgresoBorrar);
  webServer.on("/api/modo", HTTP_POST, webModo);
  webServer.on("/api/tecla", HTTP_POST, webTecla);
  webServer.on("/api/pizarra", HTTP_POST, webPizarra);
  webServer.on("/api/deletrear", HTTP_POST, webDeletrear);
  webServer.on("/api/escribir", HTTP_POST, webEscribir);
  webServer.on("/api/escrito/borrar", HTTP_POST, webEscritoBorrar);
  webServer.on("/api/calibrar", HTTP_POST, webCalibrar);
  webServer.on("/api/reiniciar", HTTP_POST, webReiniciar);
  webServer.onNotFound(webNoEncontrado);
  webServer.begin();
  Serial.printf("[RED] WiFi \"%s\" creada. Pagina en http://%s/\n", aj.ssid,
                WiFi.softAPIP().toString().c_str());
}

// ###################################################################
// ###   FIN - SERVIDOR WEB EN MODO PUNTO DE ACCESO (AP)           ###
// ###################################################################

// =====================================================================
// 8. SETUP
// =====================================================================
void setup() {
  Serial.begin(115200);

  Wire.begin(4, 5);                 // codigo base
  lcd.init();                       // codigo base
  lcd.backlight();                  // codigo base
  lcd.clear();
  pantallaNueva();

  analogReadResolution(12);         // codigo base
  analogSetPinAttenuation(ADKEY_PIN, ADC_ATTENUATION);
  pinMode(ADKEY_PIN, INPUT);
  memcpy(KEYPAD_MAP_DEFECTO, KEYPAD_MAP, sizeof(KEYPAD_MAP));
#if CELDA_FISICA
  for (uint8_t d = 0; d < 6; d++) pinMode(CELDA_PINES[d], OUTPUT);
#endif
  celdaFisica(0);
  Serial.println(F("\n=== DOTLY ==="));

  mostrarBienvenida();              // codigo base (2 s)

  // Memoria. Si la particion viene corrupta se reinicia: sin ella no se
  // guardaria ni la calibracion ni el progreso.
  nvsOk = prefs.begin(NVS_NS, false);
  if (!nvsOk) {
    Serial.println(F("[MEMORIA] La NVS no abre: se reinicia la particion"));
    nvs_flash_erase();
    nvs_flash_init();
    nvsOk = prefs.begin(NVS_NS, false);
  }
  cargarAjustes();
  cargarProgreso();
  cargarSenas();
  cargarPalabras();

  // Teclado: calibracion guardada, reposo y via de escape
  const bool hayCal = keypadHasCalibration();
  if (hayCal) keypadLoadCalibration();
  else Serial.println(F("[TECLADO] Sin calibracion guardada: se abre el asistente"));
  if (keypadActiveCount() == 0) keypadRestoreDefaults();
  const Boton mantenida = keypadMeasureIdle();

  iniciarRed();

  if (keypad.desconectado) {
    // Sin teclado no hay nada que calibrar: al menu, que se maneja desde la web
    keypadRestoreDefaults();
    cambiarEstado(MENU_PRINCIPAL);
  } else if (!hayCal || mantenida != SW_NINGUNO) {
    iniciarAsistente();
  } else {
    cambiarEstado(MENU_PRINCIPAL);
  }
}

// =====================================================================
// 9. LOOP
// =====================================================================
void loop() {

  // La red primero: asi la pagina responde pase lo que pase en la pantalla
  if (apOk) {
    dnsServer.processNextRequest();
    webServer.handleClient();
  }

  // Consola: 'c' abre el asistente de calibracion del teclado
  while (Serial.available()) {
    const int c = Serial.read();
    if (c == 'c' || c == 'C') iniciarAsistente();
  }

  const uint32_t ahora = millis();
  int boton = 0;
  if (desde(ultimoSondeo, ahora) >= KEY_POLL_MS) {
    ultimoSondeo = ahora;
    if (estado == CALIBRAR) {
      if (keypadWizardStep(ahora)) cambiarEstado(MENU_PRINCIPAL);
      else dibujarAsistente();
      return;
    }
    boton = leerBoton();
  }
  if (boton == 0 && botonWeb) { boton = botonWeb; botonWeb = 0; }   // teclado de la pagina

  tickEstado(ahora);

  if (boton == 0) {
    return;
  }

  // =========================
  // MENU PRINCIPAL (nuevo)
  // =========================

  if (estado == MENU_PRINCIPAL) {
    teclaMenuPrincipal(boton);
  }

  // =========================
  // MENU DE BLOQUES (codigo base)
  // =========================

  else if (estado == MENU_BLOQUES) {

    // SW1 = entrar al bloque
    if (boton == 1) {

      estado = BLOQUE;

      mostrarBloque();

      esperarLiberacion();
    }

    // SW5 = repetir menú
    else if (boton == 5) {

      mostrarMenuBloques();

      esperarLiberacion();
    }

    // SW2 / SW3 = bloque anterior / siguiente (nuevo)
    else if (boton == 2 || boton == 3) {

      bloqueSel = (boton == 2) ? (bloqueSel + N_BLOQUES - 1) % N_BLOQUES
                               : (bloqueSel + 1) % N_BLOQUES;

      mostrarMenuBloques();
    }

    // SW4 = volver al menu principal (nuevo)
    else if (boton == 4) {

      cambiarEstado(MENU_PRINCIPAL);
    }
  }

  // =========================
  // BLOQUE (codigo base)
  // =========================

  else if (estado == BLOQUE) {

    // SW4 = volver
    if (boton == 4) {

      estado = MENU_BLOQUES;

      mostrarMenuBloques();

      esperarLiberacion();
    }

    // SW5 = repetir
    else if (boton == 5) {

      mostrarBloque();

      esperarLiberacion();
    }

    // SW1 / SW3 = primera letra, SW2 = ultima: detalle letra a letra (nuevo)
    else {

      letraSel = (boton == 2) ? BLOQUES[bloqueSel].cuantos - 1 : 0;

      cambiarEstado(LETRA);
    }
  }

  // =========================
  // MODOS NUEVOS
  // =========================

  else if (estado == LETRA)            teclaLetra(boton);
  else if (estado == ESCRIBIR)         teclaEscribir(boton);
  else if (estado == RETO_LEER ||
           estado == RETO_FORMAR ||
           estado == RETO_SENAS)       teclaReto(boton);
  else if (estado == RETO_RESUMEN)     teclaResumen(boton);
  else if (estado == PALABRAS)         teclaPalabras(boton);
  else if (estado == SENAS)            teclaSenas(boton);
  else if (estado == PROGRESO)         teclaProgreso(boton);
  else if (estado == AJUSTES)          teclaAjustes(boton);
  else if (estado == AJUSTE_RED) {
    if (boton == SW4) cambiarEstado(AJUSTES);
    else { pagina = (boton == SW2) ? (pagina + 2) % 3 : (pagina + 1) % 3; dibujarRed(); }
  }
  else if (estado == CONFIRMAR_BORRAR) {
    if (boton == SW1) borrarProgreso();
    cambiarEstado(AJUSTES);
  }
  else if (estado == ACERCA)           cambiarEstado(AJUSTES);
  else if (estado == PIZARRA)          teclaPizarra(boton);
}
