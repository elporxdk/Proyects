// =====================================================================
//  ESTO NO ES FIRMWARE. NO SE GRABA EN EL ESP32.
// =====================================================================
//  Sustituto del WebServer del core ESP32 para el banco de pruebas del PC.
//  Guarda las rutas que registra el firmware y, cuando el banco "pide" una
//  pagina, la sirve desde el hilo del interfaz, igual que handleClient() en
//  la placa de verdad. Asi se puede comprobar lo que el navegador veria.
// =====================================================================
#pragma once
#include <Arduino.h>
#include <functional>
#include <string>

#define CONTENT_LENGTH_UNKNOWN ((size_t) - 1)
enum { HTTP_ANY = 0, HTTP_GET = 1, HTTP_POST = 2 };
typedef int HTTPMethod;

class WebServer {
 public:
  typedef std::function<void(void)> THandlerFunction;
  explicit WebServer(uint16_t puerto) : puerto_(puerto) {}

  void begin();
  void handleClient();
  void on(const char *ruta, THandlerFunction fn);
  void on(const char *ruta, HTTPMethod metodo, THandlerFunction fn);
  void onNotFound(THandlerFunction fn);

  void setContentLength(size_t) {}
  void sendHeader(const String &, const String &, bool = false) {}
  void send(int codigo, const char *tipo, const String &cuerpo);
  void sendContent(const String &trozo);
  void sendContent(const char *trozo, size_t n);

  HTTPMethod method() const { return metodoActual_; }
  String uri() const { return String(rutaActual_.c_str()); }

  uint16_t puerto_;
  HTTPMethod metodoActual_ = HTTP_GET;
  std::string rutaActual_;
};

// ---- lo que usa el banco ----
//  Piden una pagina y esperan a que el hilo del interfaz la sirva.
std::string webPedir(const char *ruta);          // GET
std::string webEnviar(const char *ruta);         // POST
bool        webEncendido();                      // ¿ha llamado a begin()?
int         webCodigo();                         // codigo de la ultima respuesta
