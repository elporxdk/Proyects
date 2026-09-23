// ESTO NO ES FIRMWARE. DNS del portal cautivo: en el banco no hace nada.
#pragma once
#include <WiFi.h>
class DNSServer {
 public:
  bool start(uint16_t puerto, const String &dominio, const IPAddress &ip) {
    puerto_ = puerto; dominio_ = dominio; ip_ = ip; return true;
  }
  void processNextRequest() {}
  void stop() {}
  uint16_t puerto_ = 0;
  String dominio_;
  IPAddress ip_;
};
