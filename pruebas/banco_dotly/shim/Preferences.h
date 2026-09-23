// ESTO NO ES FIRMWARE. NVS del ESP32 respaldada por un fichero (DOTLY_NVS).
#pragma once
#include <Arduino.h>
#include <map>
#include <string>
#include <vector>
class Preferences {
 public:
  bool   begin(const char *ns, bool soloLectura = false);
  void   end() {}
  size_t getBytesLength(const char *clave);
  size_t getBytes(const char *clave, void *buf, size_t len);
  size_t putBytes(const char *clave, const void *buf, size_t len);
  bool   remove(const char *clave);
 private:
  void cargar();
  void guardar();
  std::map<std::string, std::vector<uint8_t>> datos;
  std::string fichero;
};
extern bool g_nvsRota;
