// Shim de Arduino para ejecutar el firmware en el PC (banco de pruebas).
#pragma once
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <cmath>
#include <string>
#include <mutex>
#include <thread>
#include <chrono>
#include <atomic>

#define ARDUINO 200
typedef uint8_t byte;
typedef bool boolean;

#ifndef PI
#define PI 3.1415926535897932384626433832795
#endif
#define F(x) (x)
#define PROGMEM
#define INPUT 0
#define OUTPUT 1
#define HIGH 1
#define LOW 0

// ---- reloj virtual acelerado ----
extern double g_speedup;
uint32_t millis();
uint32_t micros();
void     delay(uint32_t ms);
void     delayMicroseconds(uint32_t us);
long     random(long max);
long     random(long min, long max);

// ---- ADC simulado ----
extern std::atomic<int> g_adcMv;      // tension presente en el pin, en mV
extern std::atomic<int> g_adcRuido;   // +-mV de ruido: >0 simula el pin al aire
int  analogRead(int pin);
uint32_t analogReadMilliVolts(int pin);
void analogReadResolution(int bits);
void analogSetPinAttenuation(int pin, int att);
void pinMode(int pin, int mode);
int  digitalRead(int pin);
#define INPUT        0
#define OUTPUT       1
#define INPUT_PULLUP 2

// ---- Estado ELECTRICO simulado del bus I2C ----
//  Reproduce lo que hace una placa de verdad, que es lo que el firmware mira
//  antes de hablar por el bus:
//    LIN_CONECTADO : el modulo tiene corriente -> sus pull-ups dejan SDA/SCL
//                    firmemente altas.
//    LIN_AL_AIRE   : no hay nada enchufado -> los pines flotan, las lecturas
//                    bailan y el escaneo "encuentra" direcciones al azar.
//    LIN_CORTO     : una linea tocando GND -> se queda baja aunque el ESP32
//                    tire de ella hacia arriba.
enum LineasI2C { LIN_CONECTADO = 0, LIN_AL_AIRE = 1, LIN_CORTO = 2 };
extern std::atomic<int> g_i2cLineas;
#define ADC_11db  3
#define ADC_6db   2
#define ADC_2_5db 1
#define ADC_0db   0

#ifdef isnan
#undef isnan
#endif
#ifdef isinf
#undef isinf
#endif
using std::isnan;
using std::isinf;

#ifdef min
#undef min
#endif
#ifdef max
#undef max
#endif
template <typename T> inline T min(T a, T b) { return a < b ? a : b; }
template <typename T> inline T max(T a, T b) { return a > b ? a : b; }

// ---- Serie ----
struct SerialSim {
  void begin(unsigned long) {}
  int  available();
  int  read();
  void print(const char *s)   { fputs(s, stdout); }
  void print(int v)           { printf("%d", v); }
  void print(float v)         { printf("%f", v); }
  void println()              { puts(""); }
  void println(const char *s) { puts(s); }
  void println(int v)         { printf("%d\n", v); }
  template <typename... A> void printf(const char *f, A... a) { std::printf(f, a...); }
  void flush() { fflush(stdout); }
};
extern SerialSim Serial;

// ---- FreeRTOS ----
typedef void *TaskHandle_t;
#define portTICK_PERIOD_MS 1
typedef std::recursive_mutex portMUX_TYPE;
#define portMUX_INITIALIZER_UNLOCKED {}
void portENTER_CRITICAL(portMUX_TYPE *m);
void portEXIT_CRITICAL(portMUX_TYPE *m);
void vTaskDelay(uint32_t ticks);
int  xTaskCreatePinnedToCore(void (*fn)(void *), const char *name, uint32_t stack,
                             void *param, int prio, TaskHandle_t *handle, int core);

// --- trozos del ESP32 que usa el firmware para diagnosticarse ---
#define RTC_DATA_ATTR            // en la placa vive en la memoria RTC
typedef uint32_t StackType_t;
uint32_t uxTaskGetStackHighWaterMark(void *tarea);
enum { ESP_RST_UNKNOWN = 0, ESP_RST_POWERON, ESP_RST_EXT, ESP_RST_SW, ESP_RST_PANIC,
       ESP_RST_INT_WDT, ESP_RST_TASK_WDT, ESP_RST_WDT, ESP_RST_DEEPSLEEP,
       ESP_RST_BROWNOUT, ESP_RST_SDIO };
extern int g_motivoReinicio;     // el banco decide como fue el ultimo reinicio
int esp_reset_reason();
struct EspSim {
  uint32_t getFreeHeap() { return 210000; }
  void restart();                 // en el banco solo se apunta, no se reinicia nada
};
extern EspSim ESP;
extern std::atomic<bool> g_reinicioPedido;

// El firmware usa el String de Arduino en las funciones de red. En el PC basta
// con std::string: tiene c_str(), concatenacion y construccion desde const char*.
typedef std::string String;
