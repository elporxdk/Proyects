// WiFi simulada. El banco decide si hay red, cuanto tarda en enganchar y que
// IP da el router, para poder probar el camino completo y tambien los fallos.
#pragma once
#include <Arduino.h>
#include <string>

#define WL_CONNECTED    3
#define WL_DISCONNECTED 6
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

// --- mandos del banco ---
extern std::atomic<bool>     g_wifiHayRed;      // existe la red MEDIBOT
extern std::atomic<int>      g_wifiRetardoMs;   // lo que tarda en enganchar
extern std::atomic<bool>     g_mdnsResponde;    // la Pi contesta por mDNS
extern std::atomic<bool>     g_medibotVivo;     // la API responde
extern std::atomic<uint32_t> g_medibotIp;       // donde esta la Raspberry
extern std::atomic<int>      g_medibotPuerto;
extern std::atomic<int>      g_apiSistema, g_apiDetecciones, g_apiRojos,
                             g_apiFps1, g_apiFps2, g_apiCaraX, g_apiCaraY;
extern std::atomic<bool>     g_apiGrabando;
extern std::atomic<int>      g_ipsProbadas;     // cuantas IPs ha mirado el barrido
std::string ipTexto(uint32_t ip);

class WiFiClientSim {
 public:
  bool connect(IPAddress ip, uint16_t puerto, int) ;
  void stop() {}
};
typedef WiFiClientSim WiFiClient;

class WiFiSim {
 public:
  int  status();
  int  RSSI() { return -57; }
  void mode(int) {}
  void setSleep(bool) {}
  void begin(const char *ssid, const char *pass);
  IPAddress localIP();
  IPAddress gatewayIP() { return IPAddress(192, 168, 1, 1); }
  IPAddress subnetMask() { return IPAddress(255, 255, 255, 0); }
  IPAddress dnsIP() { return IPAddress(192, 168, 1, 1); }
  String    BSSIDstr() { return String("AA:BB:CC:DD:EE:FF"); }
  int32_t   channel() { return 6; }
  String    macAddress() { return String("A0:B7:65:11:22:33"); }
  std::string ssidPedida, passPedida;
  uint32_t    t0 = 0;
  bool        arrancada = false;
};
extern WiFiSim WiFi;
void configTzTime(const char *tz, const char *ntp);
