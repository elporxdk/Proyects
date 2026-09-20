// Bus I2C simulado: contesta en la direccion del MAX30102 (0x57) cuando el
// sensor falso esta "conectado", y sirve su banco de registros. Asi el banco
// prueba tambien el escaneo del bus y la verificacion por relectura que hace
// el firmware al configurar el sensor.
#pragma once
#include <Arduino.h>

#define MAXSIM_I2C_ADDR 0x57
extern uint8_t  maxSimRegs[256];     // registros del sensor falso
extern bool     maxSimPresente();    // responde en el bus

class TwoWire {
 public:
  void begin(int sda = -1, int scl = -1) { (void)sda; (void)scl; }
  bool end() { return true; }
  void setClock(unsigned long hz) { clock = hz; }
  void beginTransmission(uint8_t a) { addr = a; nEscrito = 0; }
  size_t write(uint8_t b) { if (nEscrito < 4) escrito[nEscrito++] = b; return 1; }
  uint8_t endTransmission(bool stop = true);
  uint8_t requestFrom(uint8_t a, uint8_t n);
  int read();
  unsigned long clock = 100000;
  uint8_t addr = 0, escrito[4] = {0}, nEscrito = 0;
  uint8_t puntero = 0, pendiente = 0;
};
extern TwoWire Wire;
