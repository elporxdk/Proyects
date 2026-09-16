#pragma once
#include <Arduino.h>
#include <Wire.h>
// Termometro IR simulado: el banco de pruebas fija la temperatura del objeto.
extern std::atomic<int> g_mlxObjetoMiliC;   // milesimas de grado; <0 = sin muneca
extern std::atomic<int> g_mlxAmbienteMiliC;
extern std::atomic<bool> g_mlxPresente;
class Adafruit_MLX90614 {
 public:
  bool begin(uint8_t addr = 0x5A, TwoWire *w = &Wire) { (void)addr; (void)w; return g_mlxPresente.load(); }
  double readObjectTempC();
  double readAmbientTempC();
};
