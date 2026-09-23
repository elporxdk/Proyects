// =====================================================================
//  ESTO NO ES FIRMWARE. NO SE GRABA EN EL ESP32.
// =====================================================================
//  Sustituto minimo de Arduino para ejecutar DOTLY en el PC (banco de
//  pruebas). Reloj virtual acelerado, ADC simulado y Serie capturada.
// =====================================================================
#pragma once
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <string>
#include <atomic>

#define ARDUINO 200
typedef uint8_t byte;
typedef bool boolean;

#define F(x) (x)
#define PROGMEM
#define INPUT        0
#define OUTPUT       1
#define INPUT_PULLUP 2
#define HIGH 1
#define LOW  0
#define ADC_0db   0
#define ADC_2_5db 1
#define ADC_6db   2
#define ADC_11db  3

// ---- reloj virtual acelerado ----
extern double g_speedup;
uint32_t millis();
void     delay(uint32_t ms);
long     random(long max);
long     random(long min, long max);

// ---- ADC simulado: tension en el pin del teclado ----
extern std::atomic<int> g_adcMv;      // mV presentes en el pin
extern std::atomic<int> g_adcRuido;   // +-mV de ruido uniforme
int      analogRead(int pin);
uint32_t analogReadMilliVolts(int pin);
void     analogReadResolution(int bits);
void     analogSetPinAttenuation(int pin, int att);
void     pinMode(int pin, int mode);
void     digitalWrite(int pin, int v);

// ---- Serie: se imprime y ademas se guarda para que el banco la lea ----
struct SerialSim {
  void begin(unsigned long) {}
  int  available();
  int  read();
  void print(const char *s);
  void println(const char *s = "");
  void printf(const char *f, ...) __attribute__((format(printf, 2, 3)));
};
extern SerialSim Serial;
void        serieMeter(const char *txt);   // lo que "teclea" el banco en el monitor
std::string serieLog();                    // todo lo impreso hasta ahora
void        serieLimpiar();

// ---- ESP ----
struct EspSim {
  uint64_t getEfuseMac() { return 0x1A2B3C4D5E6FULL; }
  void restart();
};
extern EspSim ESP;
extern std::atomic<int> g_reinicios;

// El String de Arduino: en el PC basta std::string (c_str, length, +=).
typedef std::string String;
