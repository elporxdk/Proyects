#pragma once
#include <WiFi.h>
class MDNSSim {
 public:
  bool begin(const char*) { return true; }
  int queryService(const char*, const char*) { return 0; }
  IPAddress IP(int) { return IPAddress(); }
  IPAddress queryHost(const char*) { return IPAddress(); }
};
extern MDNSSim MDNS;
