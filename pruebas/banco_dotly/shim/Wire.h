// ESTO NO ES FIRMWARE. Bus I2C del banco: el LCD simulado no lo necesita.
#pragma once
#include <Arduino.h>
class TwoWire {
 public:
  void begin(int sda = -1, int scl = -1) { sda_ = sda; scl_ = scl; }
  int sda_ = -1, scl_ = -1;
};
extern TwoWire Wire;
