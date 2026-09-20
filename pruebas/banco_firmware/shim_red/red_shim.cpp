// =====================================================================
//  ESTO NO ES FIRMWARE. NO SE GRABA EN EL ESP32.
// =====================================================================
//  Es parte del banco de pruebas que corre en el PC (ver LEEME.md). Lleva
//  int main(), hilos y sensores simulados: en un ESP32 no tiene ningun
//  sentido y no compila.
//
//  Lo que se graba en la placa es:   firmware/medibot_triaje/medibot_triaje.ino
// =====================================================================
#if defined(ARDUINO_ARCH_ESP32) || defined(ARDUINO_ARCH_AVR) || defined(ESP32)
#error "Esto es el BANCO DE PRUEBAS de PC, no firmware. Graba firmware/medibot_triaje/medibot_triaje.ino"
#endif

#include <WiFi.h>
#include <ESPmDNS.h>
#include <HTTPClient.h>

WiFiSim WiFi;
MDNSSim MDNS;
void configTzTime(const char*, const char*) {}

std::atomic<bool>     g_wifiHayRed{true};
std::atomic<int>      g_wifiRetardoMs{1200};
std::atomic<bool>     g_mdnsResponde{true};
std::atomic<bool>     g_medibotVivo{true};
std::atomic<uint32_t> g_medibotIp{0};
std::atomic<int>      g_medibotPuerto{5000};
std::atomic<int>      g_apiSistema{1}, g_apiDetecciones{3}, g_apiRojos{2},
                      g_apiFps1{28}, g_apiFps2{27}, g_apiCaraX{160}, g_apiCaraY{120};
std::atomic<bool>     g_apiGrabando{false};
std::atomic<int>      g_ipsProbadas{0};

std::string ipTexto(uint32_t ip) { return IPAddress(ip).toString(); }

void WiFiSim::begin(const char *ssid, const char *pass) {
  ssidPedida = ssid ? ssid : "";
  passPedida = pass ? pass : "";
  t0 = millis();
  arrancada = true;
  printf("   [wifi] begin(\"%s\", \"%s\")\n", ssidPedida.c_str(), passPedida.c_str());
}
int WiFiSim::status() {
  if (!arrancada || !g_wifiHayRed.load()) return WL_DISCONNECTED;
  return (millis() - t0 >= (uint32_t)g_wifiRetardoMs.load()) ? WL_CONNECTED : WL_DISCONNECTED;
}
IPAddress WiFiSim::localIP() {
  if (status() != WL_CONNECTED) return IPAddress();
  const IPAddress medibot(g_medibotIp.load());
  return IPAddress(medibot[0], medibot[1], medibot[2], 45);   // misma subred
}

bool WiFiClientSim::connect(IPAddress ip, uint16_t puerto, int msEspera) {
  g_ipsProbadas++;
  const bool hay = g_medibotVivo.load() && (uint32_t)ip == g_medibotIp.load() &&
                   puerto == (uint16_t)g_medibotPuerto.load();
  // Una IP vacia no contesta al instante: se agota el tiempo de espera. Sin
  // esto el barrido simulado seria instantaneo y no se pareceria en nada al
  // de la red de verdad (254 intentos a ~180 ms).
  if (!hay) delay(msEspera > 0 ? (uint32_t)msEspera / 4 : 40);
  return hay;
}

bool HTTPClient::esMedibot() const {
  return g_medibotVivo.load() && host_ == ipTexto(g_medibotIp.load()) &&
         puerto_ == (uint16_t)g_medibotPuerto.load();
}
bool HTTPClient::begin(const std::string &host, uint16_t puerto, const char *ruta) {
  host_ = host; puerto_ = puerto; ruta_ = ruta ? ruta : "";
  return true;
}
int HTTPClient::GET()  { ultimo_ = esMedibot() ? 200 : -1; return ultimo_; }
int HTTPClient::sendRequest(const char*) { ultimo_ = esMedibot() ? 200 : -1; return ultimo_; }
bool HTTPClient::hasHeader(const char *h) {
  if (ultimo_ != 200) return false;
  return (puerto_ == 5000) ? (std::string(h) == "X-Medibot-Build")
                           : (std::string(h) == "X-Pillbox-Build");
}
std::string HTTPClient::getString() {
  if (ultimo_ != 200) return "";
  char b[220];
  snprintf(b, sizeof(b),
           "{\"s\":%d,\"d\":%d,\"fx\":%d,\"fy\":%d,\"r\":%d,\"ro\":%d,"
           "\"f1\":%d,\"f2\":%d,\"t\":1700000000}",
           g_apiSistema.load(), g_apiDetecciones.load(), g_apiCaraX.load(),
           g_apiCaraY.load(), g_apiGrabando.load() ? 1 : 0, g_apiRojos.load(),
           g_apiFps1.load(), g_apiFps2.load());
  return b;
}
