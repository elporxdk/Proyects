#pragma once
#include <Arduino.h>
class TwoWire {
 public:
  void begin(int sda = 0, int scl = 0) { (void)sda; (void)scl; }
  void setClock(unsigned long hz) { clock = hz; }
  void beginTransmission(uint8_t a) { addr = a; }
  size_t write(uint8_t) { return 1; }
  uint8_t endTransmission(bool stop = true) { (void)stop; return ack ? 0 : 2; }
  uint8_t requestFrom(uint8_t a, uint8_t n) { (void)a; pending = ack ? n : 0; return pending; }
  int read() { if (!pending) return 0; pending--; return 0x11; }   // MAX30100 falso
  unsigned long clock = 100000;
  uint8_t addr = 0, pending = 0;
  bool ack = false;              // el banco de pruebas no responde a I2C crudo
};
extern TwoWire Wire;
