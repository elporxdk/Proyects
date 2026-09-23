// ESTO NO ES FIRMWARE. WiFi en modo punto de acceso, simulada.
#pragma once
#include <Arduino.h>
#include <string>

#define WIFI_OFF 0
#define WIFI_STA 1
#define WIFI_AP  2

class IPAddress {
 public:
  IPAddress() { v[0] = v[1] = v[2] = v[3] = 0; }
  IPAddress(uint8_t a, uint8_t b, uint8_t c, uint8_t d) { v[0] = a; v[1] = b; v[2] = c; v[3] = d; }
  String toString() const {
    char b[20];
    snprintf(b, sizeof(b), "%u.%u.%u.%u", v[0], v[1], v[2], v[3]);
    return b;
  }
  uint8_t v[4];
};

class WiFiSim {
 public:
  void mode(int m) { modo = m; }
  bool softAPConfig(IPAddress ip, IPAddress gw, IPAddress mascara) { apIp = ip; (void)gw; (void)mascara; return true; }
  bool softAP(const char *ssid, const char *clave = nullptr, int canal = 1, int oculta = 0, int max = 4);
  IPAddress softAPIP() { return apIp; }
  uint8_t softAPgetStationNum() { return clientes; }

  int modo = WIFI_OFF;
  IPAddress apIp{192, 168, 4, 1};
  std::string ssid, clave;
  bool abierta = false;
  int canal = 0, maxClientes = 0;
  uint8_t clientes = 1;
};
extern WiFiSim WiFi;
extern std::atomic<bool> g_apFalla;       // el banco puede hacer que softAP() falle
