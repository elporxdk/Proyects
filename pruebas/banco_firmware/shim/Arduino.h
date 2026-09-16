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
int  analogRead(int pin);
uint32_t analogReadMilliVolts(int pin);
void analogReadResolution(int bits);
void analogSetPinAttenuation(int pin, int att);
void pinMode(int pin, int mode);
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
