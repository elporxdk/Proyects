// =====================================================================
//  ESTO NO ES FIRMWARE. NO SE GRABA EN EL ESP32.
// =====================================================================
//  Sustituto del WebServer del core ESP32 con un SOCKET DE VERDAD en
//  127.0.0.1. El firmware lo atiende desde loop() con handleClient(), igual
//  que en la placa, asi que un navegador de verdad (o el banco) puede abrir
//  la pagina de DOTLY y pulsar sus botones contra la logica real.
//
//  Imita lo que usa el firmware del WebServer real: rutas por metodo (varias
//  rutas iguales con metodos distintos se buscan en orden), argumentos de la
//  URL y del cuerpo application/x-www-form-urlencoded, cabecera Host,
//  cabeceras extra y respuesta en trozos.
// =====================================================================
#pragma once
#include <Arduino.h>
#include <functional>
#include <string>
#include <vector>
#include <utility>

#define CONTENT_LENGTH_UNKNOWN ((size_t)-1)
enum HTTPMethod { HTTP_ANY = 0, HTTP_GET = 1, HTTP_POST = 2, HTTP_OTRO = 3 };

class WebServer {
 public:
  typedef std::function<void(void)> THandlerFunction;
  explicit WebServer(int puerto = 80) : puerto_(puerto) {}

  void begin();
  void handleClient();
  void on(const String &ruta, THandlerFunction fn) { rutas_.push_back({ruta, HTTP_ANY, fn}); }
  void on(const String &ruta, HTTPMethod metodo, THandlerFunction fn) { rutas_.push_back({ruta, metodo, fn}); }
  void onNotFound(THandlerFunction fn) { noEncontrado_ = fn; }

  String arg(const String &nombre) const;
  bool   hasArg(const String &nombre) const;
  String hostHeader() const { return host_; }
  HTTPMethod method() const { return metodo_; }
  String uri() const { return uri_; }

  void setContentLength(size_t n) { largo_ = n; }
  void sendHeader(const String &nombre, const String &valor, bool primero = false);
  void send(int codigo, const char *tipo, const String &cuerpo);
  void sendContent(const String &trozo) { cuerpo_ += trozo; }
  void sendContent(const char *trozo, size_t n) { cuerpo_.append(trozo, n); }

  // ---- lo que usa el banco ----
  int puertoReal() const { return puertoReal_; }
  std::vector<std::pair<std::string, int>> rutas() const;

 private:
  struct Ruta { std::string ruta; HTTPMethod metodo; THandlerFunction fn; };
  int puerto_, puertoReal_ = 0, escucha_ = -1;
  std::vector<Ruta> rutas_;
  THandlerFunction noEncontrado_;
  // peticion en curso
  HTTPMethod metodo_ = HTTP_GET;
  std::string uri_, host_;
  std::vector<std::pair<std::string, std::string>> args_;
  // respuesta en curso
  int codigo_ = 0;
  std::string tipo_, cuerpo_, cabeceras_;
  size_t largo_ = 0;
};

// El banco pregunta en que puerto ha quedado (DOTLY_PUERTO, o uno libre).
int webPuerto();
std::vector<std::pair<std::string, int>> webRutas();
