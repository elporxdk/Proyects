#pragma once
#include <WiFi.h>
class MDNSSim {
 public:
  bool begin(const char*) { return true; }
  int queryService(const char*, const char*) { return g_mdnsResponde.load() ? 1 : 0; }
  IPAddress IP(int) { return IPAddress(g_medibotIp.load()); }
  IPAddress queryHost(const char*) {
    return g_mdnsResponde.load() ? IPAddress(g_medibotIp.load()) : IPAddress();
  }
};
extern MDNSSim MDNS;
