#pragma once
#include <Arduino.h>
#include <string>
#define WL_CONNECTED 3
#define WIFI_STA 1
class IPAddress {
 public:
  IPAddress() { v[0]=v[1]=v[2]=v[3]=0; }
  IPAddress(uint32_t raw) { for (int i=0;i<4;i++) v[i] = (raw >> (8*i)) & 0xFF; }
  IPAddress(uint8_t a, uint8_t b, uint8_t c, uint8_t d) { v[0]=a;v[1]=b;v[2]=c;v[3]=d; }
  uint8_t operator[](int i) const { return v[i]; }
  operator uint32_t() const { return v[0] | (v[1]<<8) | (v[2]<<16) | ((uint32_t)v[3]<<24); }
  std::string toString() const { char b[20]; snprintf(b,sizeof(b),"%u.%u.%u.%u",v[0],v[1],v[2],v[3]); return b; }
  uint8_t v[4];
};
class WiFiClientSim { public: bool connect(IPAddress, uint16_t, int) { return false; } void stop() {} };
typedef WiFiClientSim WiFiClient;
class WiFiSim {
 public:
  int status() { return WL_CONNECTED; }
  int RSSI() { return -55; }
  void mode(int) {} void setSleep(bool) {} void begin(const char*, const char*) {}
  IPAddress localIP() { return IPAddress(192,168,1,50); }
};
extern WiFiSim WiFi;
void configTzTime(const char *tz, const char *ntp);
