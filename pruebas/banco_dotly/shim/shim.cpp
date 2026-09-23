// =====================================================================
//  ESTO NO ES FIRMWARE. NO SE GRABA EN EL ESP32.
// =====================================================================
//  Implementacion de los sustitutos del banco de DOTLY (ver LEEME.md).
// =====================================================================
#if defined(ARDUINO_ARCH_ESP32) || defined(ESP32)
#error "Esto es el BANCO DE PRUEBAS de PC, no firmware. Graba dotly/dotly.ino"
#endif

#include <Arduino.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include <WiFi.h>
#include <WebServer.h>
#include <Preferences.h>
#include <nvs_flash.h>

#include <chrono>
#include <thread>
#include <mutex>
#include <deque>
#include <fstream>
#include <cstdarg>
#include <cerrno>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <unistd.h>
#include <fcntl.h>
#include <poll.h>

// ===================== reloj virtual =====================
double g_speedup = 5.0;
static const auto t0 = std::chrono::steady_clock::now();
uint32_t millis() {
  const double us = (double)std::chrono::duration_cast<std::chrono::microseconds>(
                        std::chrono::steady_clock::now() - t0).count();
  return (uint32_t)(us * g_speedup / 1000.0);
}
void delay(uint32_t ms) {
  std::this_thread::sleep_for(std::chrono::microseconds((uint64_t)(ms * 1000.0 / g_speedup)));
}
long random(long m) { return m > 0 ? (long)(::rand() % m) : 0; }
long random(long a, long b) { return b > a ? a + (long)(::rand() % (b - a)) : a; }

// ===================== ADC =====================
std::atomic<int> g_adcMv{3300};
std::atomic<int> g_adcRuido{0};
static int adcMv() {
  const int r = g_adcRuido.load();
  int v = g_adcMv.load() + (r > 0 ? (::rand() % (2 * r + 1)) - r : 0);
  if (v < 0) v = 0;
  if (v > 3150) v = 3150;          // con 11 dB el ADC del ESP32 no pasa de ~3,1 V
  return v;
}
int analogRead(int) { return adcMv() * 4095 / 3300; }
uint32_t analogReadMilliVolts(int) { return (uint32_t)adcMv(); }
void analogReadResolution(int) {}
void analogSetPinAttenuation(int, int) {}
void pinMode(int, int) {}
void digitalWrite(int, int) {}

// ===================== Serie =====================
SerialSim Serial;
static std::mutex serieMtx;
static std::string serieTxt, serieEntrada;
static void serieGuardar(const char *s) {
  fputs(s, stdout);
  std::lock_guard<std::mutex> l(serieMtx);
  serieTxt += s;
}
void SerialSim::print(const char *s) { serieGuardar(s); }
void SerialSim::println(const char *s) { serieGuardar(s); serieGuardar("\n"); }
void SerialSim::printf(const char *f, ...) {
  char b[512];
  va_list a;
  va_start(a, f);
  vsnprintf(b, sizeof(b), f, a);
  va_end(a);
  serieGuardar(b);
}
int SerialSim::available() { std::lock_guard<std::mutex> l(serieMtx); return (int)serieEntrada.size(); }
int SerialSim::read() {
  std::lock_guard<std::mutex> l(serieMtx);
  if (serieEntrada.empty()) return -1;
  const int c = (uint8_t)serieEntrada[0];
  serieEntrada.erase(0, 1);
  return c;
}
void serieMeter(const char *txt) { std::lock_guard<std::mutex> l(serieMtx); serieEntrada += txt; }
std::string serieLog() { std::lock_guard<std::mutex> l(serieMtx); return serieTxt; }
void serieLimpiar() { std::lock_guard<std::mutex> l(serieMtx); serieTxt.clear(); }

// ===================== ESP =====================
EspSim ESP;
std::atomic<int> g_reinicios{0};
void EspSim::restart() { g_reinicios++; ::printf("   [esp] reinicio pedido\n"); }

TwoWire Wire;

// ===================== LCD =====================
namespace {
std::mutex lcdMtx;
uint8_t ddram[2][40];
uint8_t cgram[8][8];
// Al encender, el HD44780 tiene la DDRAM en blanco (espacios).
struct DdramEnBlanco { DdramEnBlanco() { memset(ddram, ' ', sizeof(ddram)); } } ddramEnBlanco;
bool enCgram = false;
int cgDir = 0, fila = 0, col = 0;
bool luz = false, parpadea = false;
int apagones = 0, cgSueltas = 0;

// Glifos que se esperan para las letras que el LCD no trae
const struct { const char *utf8; uint8_t b[8]; } GLIFOS_ESPERADOS[] = {
  {"Ñ", {0x0D, 0x16, 0x11, 0x19, 0x15, 0x13, 0x11, 0x00}},
  {"Á", {0x02, 0x04, 0x0E, 0x11, 0x1F, 0x11, 0x11, 0x00}},
  {"É", {0x02, 0x04, 0x1F, 0x10, 0x1E, 0x10, 0x1F, 0x00}},
  {"Í", {0x02, 0x04, 0x0E, 0x04, 0x04, 0x04, 0x0E, 0x00}},
  {"Ó", {0x02, 0x04, 0x0E, 0x11, 0x11, 0x11, 0x0E, 0x00}},
  {"Ú", {0x02, 0x04, 0x11, 0x11, 0x11, 0x11, 0x0E, 0x00}},
  {"Ü", {0x0A, 0x00, 0x11, 0x11, 0x11, 0x11, 0x0E, 0x00}},
};

// Lee los pixeles: una celda braille son 2x3 puntos en las filas 0-1, 3-4 y
// 6-7 (las filas 2 y 5 separan). Cada punto lleno es un bloque de 2x2
// pixeles; uno vacio, un solo pixel arriba que marca su sitio. La columna del
// medio (0x04) va siempre apagada. Cualquier otra cosa no es una celda.
int decodificarCelda(const uint8_t *b) {
  if (b[2] || b[5]) return -1;
  int puntos = 0;
  for (int f = 0; f < 3; f++) {
    const int r = f * 3;
    if ((b[r] | b[r + 1]) & 0x04) return -1;
    for (int lado = 0; lado < 2; lado++) {
      const uint8_t lleno = lado ? 0x03 : 0x18, vacio = lado ? 0x01 : 0x10;
      const uint8_t a = b[r] & lleno, c = b[r + 1] & lleno;
      if (a == lleno && c == lleno) puntos |= 1 << (f + lado * 3);
      else if (!(a == vacio && c == 0)) return -1;
    }
  }
  return puntos;
}
}  // namespace

LiquidCrystal_I2C::LiquidCrystal_I2C(uint8_t, uint8_t, uint8_t) {}
void LiquidCrystal_I2C::init() {
  std::lock_guard<std::mutex> l(lcdMtx);
  memset(ddram, ' ', sizeof(ddram));
  memset(cgram, 0, sizeof(cgram));
  enCgram = false; fila = col = 0;
}
void LiquidCrystal_I2C::clear() {
  std::lock_guard<std::mutex> l(lcdMtx);
  memset(ddram, ' ', sizeof(ddram));
  enCgram = false; fila = col = 0;
}
void LiquidCrystal_I2C::setCursor(uint8_t c, uint8_t f) {
  std::lock_guard<std::mutex> l(lcdMtx);
  enCgram = false; col = c; fila = f & 1;
}
size_t LiquidCrystal_I2C::write(uint8_t c) {
  std::lock_guard<std::mutex> l(lcdMtx);
  if (enCgram) {                      // el puntero sigue en la CGRAM: se pisa un glifo
    cgram[(cgDir >> 3) & 7][cgDir & 7] = c & 0x1F;
    cgDir = (cgDir + 1) & 0x3F;
    cgSueltas++;
    return 1;
  }
  if (col < 40) ddram[fila][col] = c;
  col++;
  return 1;
}
void LiquidCrystal_I2C::createChar(uint8_t pos, uint8_t mapa[]) {
  std::lock_guard<std::mutex> l(lcdMtx);
  pos &= 7;
  for (int i = 0; i < 8; i++) cgram[pos][i] = mapa[i] & 0x1F;
  enCgram = true;                     // como el chip: el puntero queda en la CGRAM
  cgDir = (pos << 3) + 8;
}
void LiquidCrystal_I2C::backlight()   { std::lock_guard<std::mutex> l(lcdMtx); luz = true; }
void LiquidCrystal_I2C::noBacklight() { std::lock_guard<std::mutex> l(lcdMtx); if (luz) apagones++; luz = false; }
void LiquidCrystal_I2C::blink()       { std::lock_guard<std::mutex> l(lcdMtx); parpadea = true; }
void LiquidCrystal_I2C::noBlink()     { std::lock_guard<std::mutex> l(lcdMtx); parpadea = false; }

static void ponerUtf8(std::string &o, uint32_t cp) {
  char b[4];
  if (cp < 0x80) { o += (char)cp; return; }
  if (cp < 0x800) { b[0] = (char)(0xC0 | (cp >> 6)); b[1] = (char)(0x80 | (cp & 0x3F)); o.append(b, 2); return; }
  b[0] = (char)(0xE0 | (cp >> 12)); b[1] = (char)(0x80 | ((cp >> 6) & 0x3F)); b[2] = (char)(0x80 | (cp & 0x3F));
  o.append(b, 3);
}

std::string lcdFila(int f) {
  std::lock_guard<std::mutex> l(lcdMtx);
  std::string o;
  for (int c = 0; c < 16; c++) {
    const uint8_t b = ddram[f & 1][c];
    if (b < 8) {
      const int p = decodificarCelda(cgram[b]);
      if (p >= 0) { ponerUtf8(o, 0x2800 + p); continue; }
      const char *g = nullptr;
      for (const auto &e : GLIFOS_ESPERADOS) if (!memcmp(e.b, cgram[b], 8)) g = e.utf8;
      o += g ? g : "¤";                // un glifo que no se reconoce
    } else if (b == 0xFF) {
      o += "█";
    } else if (b >= 0x20 && b < 0x7F) {
      o += (char)b;
    } else {
      o += "¤";
    }
  }
  return o;
}
uint8_t lcdByte(int c, int f) { std::lock_guard<std::mutex> l(lcdMtx); return ddram[f & 1][c]; }
int lcdCeldaEn(int c, int f) {
  std::lock_guard<std::mutex> l(lcdMtx);
  const uint8_t b = ddram[f & 1][c];
  return b < 8 ? decodificarCelda(cgram[b]) : -1;
}
bool lcdLuz() { std::lock_guard<std::mutex> l(lcdMtx); return luz; }
int  lcdApagones() { std::lock_guard<std::mutex> l(lcdMtx); return apagones; }
bool lcdParpadeo(int *c, int *f) { std::lock_guard<std::mutex> l(lcdMtx); if (c) *c = col; if (f) *f = fila; return parpadea; }
int  lcdEscriturasCgramSueltas() { std::lock_guard<std::mutex> l(lcdMtx); return cgSueltas; }

// ===================== WiFi =====================
WiFiSim WiFi;
std::atomic<bool> g_apFalla{false};
bool WiFiSim::softAP(const char *s, const char *c, int ch, int, int max) {
  if (g_apFalla.load()) return false;
  ssid = s ? s : "";
  abierta = (c == nullptr || !*c);
  clave = abierta ? "" : c;
  canal = ch; maxClientes = max;
  if (ssid.empty() || ssid.size() > 32) return false;
  if (!abierta && (clave.size() < 8 || clave.size() > 63)) return false;   // como el ESP32
  return true;
}

// ===================== NVS =====================
bool g_nvsRota = false;
int nvs_flash_erase() { g_nvsRota = false; return 0; }
int nvs_flash_init() { return 0; }

static std::string rutaNVS() { const char *e = getenv("DOTLY_NVS"); return e ? e : "nvs.dat"; }
void Preferences::cargar() {
  datos.clear();
  std::ifstream f(fichero, std::ios::binary);
  while (f) {
    uint32_t kl = 0, vl = 0;
    if (!f.read((char *)&kl, 4) || !f.read((char *)&vl, 4)) break;
    std::string k(kl, '\0');
    std::vector<uint8_t> v(vl);
    f.read(&k[0], kl);
    f.read((char *)v.data(), vl);
    datos[k] = v;
  }
}
void Preferences::guardar() {
  std::ofstream f(fichero, std::ios::binary | std::ios::trunc);
  for (auto &kv : datos) {
    const uint32_t kl = (uint32_t)kv.first.size(), vl = (uint32_t)kv.second.size();
    f.write((const char *)&kl, 4); f.write((const char *)&vl, 4);
    f.write(kv.first.data(), kl); f.write((const char *)kv.second.data(), vl);
  }
}
bool Preferences::begin(const char *ns, bool) {
  if (g_nvsRota) return false;
  if (strlen(ns) > 15) return false;               // limite real de la NVS
  fichero = rutaNVS() + "." + ns;
  cargar();
  return true;
}
size_t Preferences::getBytesLength(const char *k) {
  auto it = datos.find(k);
  return it == datos.end() ? 0 : it->second.size();
}
size_t Preferences::getBytes(const char *k, void *buf, size_t len) {
  auto it = datos.find(k);
  if (it == datos.end() || it->second.size() > len) return 0;    // como la NVS: no cabe = 0
  memcpy(buf, it->second.data(), it->second.size());
  return it->second.size();
}
size_t Preferences::putBytes(const char *k, const void *buf, size_t len) {
  if (strlen(k) > 15) return 0;                    // las claves de la NVS: 15 como mucho
  datos[k] = std::vector<uint8_t>((const uint8_t *)buf, (const uint8_t *)buf + len);
  guardar();
  return len;
}
bool Preferences::remove(const char *k) {
  const bool habia = datos.erase(k) > 0;
  guardar();
  return habia;
}

// ===================== servidor web con socket de verdad =====================
static WebServer *g_servidor = nullptr;
int webPuerto() { return g_servidor ? g_servidor->puertoReal() : 0; }
std::vector<std::pair<std::string, int>> webRutas() {
  return g_servidor ? g_servidor->rutas() : std::vector<std::pair<std::string, int>>();
}
std::vector<std::pair<std::string, int>> WebServer::rutas() const {
  std::vector<std::pair<std::string, int>> v;
  for (auto &r : rutas_) v.push_back({r.ruta, (int)r.metodo});
  return v;
}

void WebServer::begin() {
  escucha_ = socket(AF_INET, SOCK_STREAM, 0);
  int si = 1;
  setsockopt(escucha_, SOL_SOCKET, SO_REUSEADDR, &si, sizeof(si));
  sockaddr_in a{};
  a.sin_family = AF_INET;
  a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
  const char *e = getenv("DOTLY_PUERTO");
  a.sin_port = htons((uint16_t)(e ? atoi(e) : 0));
  if (bind(escucha_, (sockaddr *)&a, sizeof(a)) != 0 || listen(escucha_, 16) != 0) {
    ::printf("   [web] no se puede abrir el puerto: %s\n", strerror(errno));
    exit(3);
  }
  socklen_t n = sizeof(a);
  getsockname(escucha_, (sockaddr *)&a, &n);
  puertoReal_ = ntohs(a.sin_port);
  fcntl(escucha_, F_SETFL, O_NONBLOCK);
  g_servidor = this;
}

static std::string urlDecodificar(const std::string &s) {
  std::string o;
  for (size_t i = 0; i < s.size(); i++) {
    if (s[i] == '+') o += ' ';
    else if (s[i] == '%' && i + 2 < s.size()) { o += (char)strtol(s.substr(i + 1, 2).c_str(), nullptr, 16); i += 2; }
    else o += s[i];
  }
  return o;
}
static void leerArgs(const std::string &q, std::vector<std::pair<std::string, std::string>> &args) {
  size_t i = 0;
  while (i < q.size()) {
    size_t f = q.find('&', i);
    if (f == std::string::npos) f = q.size();
    const std::string par = q.substr(i, f - i);
    if (!par.empty()) {
      const size_t igual = par.find('=');
      if (igual == std::string::npos) args.push_back({urlDecodificar(par), ""});
      else args.push_back({urlDecodificar(par.substr(0, igual)), urlDecodificar(par.substr(igual + 1))});
    }
    i = f + 1;
  }
}

String WebServer::arg(const String &n) const {
  for (auto &a : args_) if (a.first == n) return a.second;
  return "";
}
bool WebServer::hasArg(const String &n) const {
  for (auto &a : args_) if (a.first == n) return true;
  return false;
}
void WebServer::sendHeader(const String &n, const String &v, bool) { cabeceras_ += n + ": " + v + "\r\n"; }
void WebServer::send(int codigo, const char *tipo, const String &cuerpo) {
  codigo_ = codigo;
  tipo_ = tipo ? tipo : "text/plain";
  cuerpo_ += cuerpo;
}

void WebServer::handleClient() {
  if (escucha_ < 0) return;
  const int cli = accept(escucha_, nullptr, nullptr);
  if (cli < 0) return;
  timeval to{2, 0};
  setsockopt(cli, SOL_SOCKET, SO_RCVTIMEO, &to, sizeof(to));
  std::string pet;
  char b[4096];
  size_t finCab = std::string::npos;
  while ((finCab = pet.find("\r\n\r\n")) == std::string::npos) {
    const ssize_t n = recv(cli, b, sizeof(b), 0);
    if (n <= 0) { close(cli); return; }
    pet.append(b, (size_t)n);
  }
  // cabeceras
  std::string tipoCuerpo;
  size_t largo = 0;
  host_.clear();
  size_t ini = pet.find("\r\n") + 2;
  while (ini < finCab) {
    const size_t f = pet.find("\r\n", ini);
    const std::string l = pet.substr(ini, f - ini);
    const size_t dp = l.find(':');
    if (dp != std::string::npos) {
      std::string nombre = l.substr(0, dp), valor = l.substr(dp + 1);
      while (!valor.empty() && valor[0] == ' ') valor.erase(0, 1);
      for (auto &c : nombre) c = (char)tolower(c);
      if (nombre == "host") host_ = valor;
      if (nombre == "content-type") tipoCuerpo = valor;
      if (nombre == "content-length") largo = (size_t)atol(valor.c_str());
    }
    ini = f + 2;
  }
  std::string cuerpo = pet.substr(finCab + 4);
  while (cuerpo.size() < largo) {
    const ssize_t n = recv(cli, b, sizeof(b), 0);
    if (n <= 0) break;
    cuerpo.append(b, (size_t)n);
  }
  // linea de peticion
  const std::string linea = pet.substr(0, pet.find("\r\n"));
  const size_t e1 = linea.find(' '), e2 = linea.find(' ', e1 + 1);
  const std::string metodo = linea.substr(0, e1), destino = linea.substr(e1 + 1, e2 - e1 - 1);
  metodo_ = metodo == "GET" ? HTTP_GET : metodo == "POST" ? HTTP_POST : HTTP_OTRO;
  const size_t q = destino.find('?');
  uri_ = urlDecodificar(destino.substr(0, q));
  args_.clear();
  if (q != std::string::npos) leerArgs(destino.substr(q + 1), args_);
  if (tipoCuerpo.rfind("application/x-www-form-urlencoded", 0) == 0) leerArgs(cuerpo, args_);
  else if (!cuerpo.empty()) args_.push_back({"plain", cuerpo});
  host_ = host_.substr(0, host_.find(':'));        // sin el puerto, como en la placa (puerto 80)

  // atender
  codigo_ = 0; tipo_.clear(); cuerpo_.clear(); cabeceras_.clear(); largo_ = 0;
  THandlerFunction fn;
  for (auto &r : rutas_)
    if (r.ruta == uri_ && (r.metodo == HTTP_ANY || r.metodo == metodo_)) { fn = r.fn; break; }
  if (!fn) fn = noEncontrado_;
  if (fn) fn();
  else { codigo_ = 404; tipo_ = "text/plain"; cuerpo_ = "Not found"; }

  char cab[256];
  snprintf(cab, sizeof(cab), "HTTP/1.1 %d %s\r\nContent-Type: %s\r\nContent-Length: %zu\r\nConnection: close\r\n",
           codigo_, codigo_ == 200 ? "OK" : codigo_ == 302 ? "Found" : "Error", tipo_.c_str(), cuerpo_.size());
  std::string resp = std::string(cab) + cabeceras_ + "\r\n" + cuerpo_;
  size_t hecho = 0;
  while (hecho < resp.size()) {
    const ssize_t n = ::send(cli, resp.data() + hecho, resp.size() - hecho, MSG_NOSIGNAL);
    if (n <= 0) break;
    hecho += (size_t)n;
  }
  shutdown(cli, SHUT_WR);
  close(cli);
}
