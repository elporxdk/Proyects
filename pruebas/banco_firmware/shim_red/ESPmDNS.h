#pragma once
#include <WiFi.h>
class MDNSSim {
 public:
  bool begin(const char*) { return true; }
  void addService(const char*, const char*, uint16_t) {}
  int queryService(const char*, const char*) { return g_mdnsResponde.load() ? 1 : 0; }
  IPAddress IP(int) { return IPAddress(g_medibotIp.load()); }        // core 2.x
  IPAddress address(int) { return IPAddress(g_medibotIp.load()); }   // core 3.x
  IPAddress queryHost(const char*) {
    return g_mdnsResponde.load() ? IPAddress(g_medibotIp.load()) : IPAddress();
  }
};
extern MDNSSim MDNS;
