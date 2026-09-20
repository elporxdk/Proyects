// Cliente HTTP simulado: contesta como MEDIBOT (cabecera X-Medibot-Build y
// el JSON de /api/esp32) solo en la IP y el puerto donde el banco lo ha
// puesto. En cualquier otra direccion falla, como en la red de verdad.
#pragma once
#include <Arduino.h>
#include <WiFi.h>
#include <string>
class HTTPClient {
 public:
  void setConnectTimeout(int) {} void setTimeout(int) {} void setReuse(bool) {}
  void collectHeaders(const char**, size_t) {}
  bool begin(const std::string &host, uint16_t puerto, const char *ruta);
  int  GET();
  int  sendRequest(const char *metodo);
  bool hasHeader(const char *h);
  std::string getString();
  void end() {}
 private:
  bool esMedibot() const;
  std::string host_, ruta_;
  uint16_t puerto_ = 0;
  int ultimo_ = -1;
};
