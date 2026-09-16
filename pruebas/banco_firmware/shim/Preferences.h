// Shim de Preferences (NVS del ESP32) respaldado por un fichero, para poder
// simular en el banco de pruebas un arranque "en frio" y uno ya calibrado.
#pragma once
#include <Arduino.h>
#include <map>
#include <string>
#include <vector>

class Preferences {
 public:
  bool   begin(const char *ns, bool ro = false);
  size_t getBytesLength(const char *key);
  size_t getBytes(const char *key, void *buf, size_t len);
  size_t putBytes(const char *key, const void *buf, size_t len);
 private:
  void cargar();
  void guardar();
  std::map<std::string, std::vector<uint8_t>> datos;
  std::string fichero;
};
