// =====================================================================
//  ESTO NO ES FIRMWARE. NO SE GRABA EN EL ESP32.
// =====================================================================
//  Banco de pruebas de DOTLY en el PC (ver LEEME.md). Compila el firmware
//  REAL (dotly/dotly.ino) contra los sustitutos de shim/, lo arranca en su
//  propio hilo y hace de persona: pulsa botones poniendo tensiones en el
//  pin del teclado, lee el LCD decodificando los pixeles de cada celda y
//  usa la pagina web por HTTP de verdad.
//
//  Uso:  banco_dotly <caso>        (ver la lista en main)
//        banco_dotly servidor      (deja DOTLY encendido para el navegador)
//
//  Lo que se graba en la placa es:   dotly/dotly.ino
// =====================================================================
#if defined(ARDUINO_ARCH_ESP32) || defined(ESP32)
#error "Esto es el BANCO DE PRUEBAS de PC, no firmware. Graba dotly/dotly.ino"
#endif
#include "../../dotly/dotly.ino"

#include <thread>
#include <chrono>
#include <functional>
#include <vector>
#include <set>
#include <mutex>
#include <cstdarg>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <unistd.h>

namespace banco {

// ---------------------------------------------------------------------
// Resultados
// ---------------------------------------------------------------------
int fallos = 0;

void comprobar(bool ok, const char *fmt, ...) __attribute__((format(printf, 2, 3)));
void comprobar(bool ok, const char *fmt, ...) {
  std::printf("   %s  ", ok ? "OK   " : "FALLO");
  va_list a;
  va_start(a, fmt);
  std::vprintf(fmt, a);
  va_end(a);
  std::printf("\n");
  if (!ok) fallos++;
}

// ---------------------------------------------------------------------
// Tiempo, pantalla y firmware en marcha
// ---------------------------------------------------------------------
std::atomic<bool> vivo{true}, listo{false};
std::thread ui;

void esperar(uint32_t ms) { delay(ms); }   // en tiempo virtual

std::string recortar(std::string s) {
  while (!s.empty() && s.back() == ' ') s.pop_back();
  return s;
}
std::string fila(int f) { return recortar(lcdFila(f)); }

// Los 16 caracteres de una fila, uno por posicion (en UTF-8)
std::vector<std::string> posiciones(int f) {
  const std::string s = lcdFila(f);
  std::vector<std::string> v;
  for (size_t i = 0; i < s.size();) {
    const unsigned char c = (unsigned char)s[i];
    const size_t n = c < 0x80 ? 1 : (c >> 5) == 6 ? 2 : (c >> 4) == 14 ? 3 : 4;
    v.push_back(s.substr(i, n));
    i += n;
  }
  return v;
}

void volcar() {
  std::printf("         |%s|\n         |%s|\n", lcdFila(0).c_str(), lcdFila(1).c_str());
}

bool esperarQue(const std::function<bool()> &cond, uint32_t msVirtual = 3000) {
  const uint32_t t = millis();
  while (millis() - t < msVirtual) {
    if (cond()) return true;
    std::this_thread::sleep_for(std::chrono::microseconds(500));
  }
  return cond();
}
bool esperarFila(int f, const std::string &txt, uint32_t ms = 3000) {
  return esperarQue([&] { return fila(f) == txt; }, ms);
}
bool esperarContiene(int f, const std::string &frag, uint32_t ms = 3000) {
  return esperarQue([&] { return lcdFila(f).find(frag) != std::string::npos; }, ms);
}
bool esperarPantalla(const std::string &f0, const std::string &f1, uint32_t ms = 3000) {
  return esperarQue([&] { return fila(0) == f0 && fila(1) == f1; }, ms);
}
void pantallaEs(const std::string &f0, const std::string &f1, const char *que, uint32_t ms = 3000) {
  const bool ok = esperarPantalla(f0, f1, ms);
  comprobar(ok, "%s: \"%s\" / \"%s\"", que, f0.c_str(), f1.c_str());
  if (!ok) volcar();
}

// Vigilante: guarda todas las pantallas que aparecen y avisa si alguna vez
// sale un caracter propio que no es ni celda braille ni una letra conocida.
std::mutex vigMtx;
std::set<std::string> vistas;
std::atomic<int> raras{0};
std::thread vigilante;
void vigilar() {
  while (vivo) {
    const std::string a = lcdFila(0), b = lcdFila(1);
    if (a.find("¤") != std::string::npos || b.find("¤") != std::string::npos) {
      if (raras++ == 0) std::printf("   (pantalla con un glifo desconocido: |%s| |%s|)\n", a.c_str(), b.c_str());
    }
    {
      std::lock_guard<std::mutex> l(vigMtx);
      vistas.insert(recortar(a) + "|" + recortar(b));
    }
    std::this_thread::sleep_for(std::chrono::microseconds(700));
  }
}
bool seVio(const std::string &f0, const std::string &f1) {
  std::lock_guard<std::mutex> l(vigMtx);
  return vistas.count(f0 + "|" + f1) > 0;
}

void arrancar() {
  vigilante = std::thread(vigilar);
  ui = std::thread([] {
    setup();
    listo = true;
    while (vivo) {
      loop();
      std::this_thread::sleep_for(std::chrono::microseconds(200));
    }
  });
  esperarQue([] { return listo.load(); }, 60000);
}

int terminar() {
  comprobar(lcdEscriturasCgramSueltas() == 0,
            "ningun write() con el puntero del LCD en la CGRAM (%d)", lcdEscriturasCgramSueltas());
  comprobar(raras == 0, "nunca aparece un glifo desconocido en el LCD (%d veces)", raras.load());
  vivo = false;
  if (ui.joinable()) ui.join();
  if (vigilante.joinable()) vigilante.join();
  std::printf("---\n%s (%d fallo%s)\n", fallos ? "HAY FALLOS" : "TODO OK", fallos, fallos == 1 ? "" : "s");
  return fallos;
}

// ---------------------------------------------------------------------
// Teclado: tensiones del ADKeyboard alimentado a 3V3
// ---------------------------------------------------------------------
const int MV_BOTON[6] = { 3300, 10, 460, 990, 1650, 2440 };
const int MV_REPOSO = 3300;

void soltar() { g_adcMv = MV_REPOSO; }
void pulsar(int sw, uint32_t ms = 160) {
  g_adcMv = MV_BOTON[sw];
  esperar(ms);
  soltar();
  esperar(130);
}
// Pulsacion como la de un dedo de verdad: la tension baja pasando un momento
// por la de otros botones (15 ms cada uno, menos que el antirrebote).
void pulsarConRampa(int sw) {
  for (int v = 5; v > sw; v--) { g_adcMv = MV_BOTON[v]; esperar(15); }
  pulsar(sw);
}

// El asistente de calibracion, hecho como lo haria una persona.
bool calibrar() {
  if (!esperarFila(0, "CALIBRAR TECLADO", 8000)) { volcar(); return false; }
  for (int sw = 1; sw <= 5; sw++) {
    char pide[20];
    snprintf(pide, sizeof(pide), "MANTEN: SW%d", sw);
    if (!esperarContiene(0, pide, 15000)) { std::printf("   no pide %s\n", pide); volcar(); return false; }
    g_adcMv = MV_BOTON[sw];
    if (!esperarFila(0, "Suelta el boton", 8000)) { std::printf("   no termina SW%d\n", sw); volcar(); soltar(); return false; }
    soltar();
  }
  return esperarContiene(0, "5 de 5 botones", 8000);
}

// ---------------------------------------------------------------------
// HTTP de verdad contra el servidor del firmware
// ---------------------------------------------------------------------
struct Resp { int codigo = 0; std::string cuerpo, cabeceras; };

Resp http(const char *metodo, const std::string &ruta, const std::string &cuerpo = "",
          const std::string &host = "192.168.4.1") {
  Resp r;
  const int s = socket(AF_INET, SOCK_STREAM, 0);
  sockaddr_in a{};
  a.sin_family = AF_INET;
  a.sin_port = htons((uint16_t)webPuerto());
  a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
  timeval to{8, 0};
  setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, &to, sizeof(to));
  if (connect(s, (sockaddr *)&a, sizeof(a)) != 0) { close(s); return r; }
  char cab[512];
  snprintf(cab, sizeof(cab),
           "%s %s HTTP/1.1\r\nHost: %s\r\nContent-Type: application/x-www-form-urlencoded\r\n"
           "Content-Length: %zu\r\nConnection: close\r\n\r\n",
           metodo, ruta.c_str(), host.c_str(), cuerpo.size());
  const std::string pet = std::string(cab) + cuerpo;
  ::send(s, pet.data(), pet.size(), MSG_NOSIGNAL);
  std::string resp;
  char b[4096];
  ssize_t n;
  while ((n = recv(s, b, sizeof(b), 0)) > 0) resp.append(b, (size_t)n);
  close(s);
  const size_t fin = resp.find("\r\n\r\n");
  if (resp.size() < 12 || fin == std::string::npos) return r;
  r.codigo = atoi(resp.c_str() + 9);
  r.cabeceras = resp.substr(0, fin);
  r.cuerpo = resp.substr(fin + 4);
  return r;
}
Resp GET(const std::string &ruta) { return http("GET", ruta); }
Resp POST(const std::string &ruta, const std::string &cuerpo = "") { return http("POST", ruta, cuerpo); }

// Codifica un valor para un formulario (UTF-8 tal cual, en %XX)
std::string url(const std::string &s) {
  std::string o;
  char b[4];
  for (unsigned char c : s) {
    if (isalnum(c) || c == '-' || c == '_' || c == '.') o += (char)c;
    else if (c == ' ') o += '+';
    else { snprintf(b, sizeof(b), "%%%02X", c); o += b; }
  }
  return o;
}

void tecla(int b) {
  const Resp r = POST("/api/tecla", "b=" + std::to_string(b));
  if (r.codigo != 200) std::printf("   (tecla %d por la web: %d %s)\n", b, r.codigo, r.cuerpo.c_str());
}

// ---------------------------------------------------------------------
// La signografia española, escrita aqui APARTE del firmware y por puntos,
// para comprobar la tabla del firmware contra una fuente independiente.
// ---------------------------------------------------------------------
struct Esperado { const char *clave; const char *pantalla; const char *puntos; };
const Esperado ONCE[] = {
  {"a", "A", "1"}, {"b", "B", "12"}, {"c", "C", "14"}, {"d", "D", "145"}, {"e", "E", "15"},
  {"f", "F", "124"}, {"g", "G", "1245"}, {"h", "H", "125"}, {"i", "I", "24"}, {"j", "J", "245"},
  {"k", "K", "13"}, {"l", "L", "123"}, {"m", "M", "134"}, {"n", "N", "1345"}, {"ñ", "Ñ", "12456"},
  {"o", "O", "135"}, {"p", "P", "1234"}, {"q", "Q", "12345"}, {"r", "R", "1235"}, {"s", "S", "234"},
  {"t", "T", "2345"}, {"u", "U", "136"}, {"v", "V", "1236"}, {"w", "W", "2456"}, {"x", "X", "1346"},
  {"y", "Y", "13456"}, {"z", "Z", "1356"},
  {"á", "Á", "12356"}, {"é", "É", "2346"}, {"í", "Í", "34"}, {"ó", "Ó", "346"}, {"ú", "Ú", "23456"},
  {"ü", "Ü", "1256"},
  {"1", "1", "1"}, {"2", "2", "12"}, {"3", "3", "14"}, {"4", "4", "145"}, {"5", "5", "15"},
  {"6", "6", "124"}, {"7", "7", "1245"}, {"8", "8", "125"}, {"9", "9", "24"}, {"0", "0", "245"},
  {".", ".", "3"}, {",", ",", "2"}, {";", ";", "23"}, {":", ":", "25"}, {"?", "?", "26"},
  {"!", "!", "235"}, {"\"", "\"", "236"}, {"(", "(", "126"}, {")", ")", "345"}, {"-", "-", "36"},
};
const int N_ONCE = sizeof(ONCE) / sizeof(ONCE[0]);
const int ONCE_NUMERO = 0x3C, ONCE_MAYUSCULA = 0x28;   // 3-4-5-6 y 4-6

int puntos(const char *s) { int p = 0; for (; *s; s++) p |= 1 << (*s - '1'); return p; }
std::string guiones(const char *s) { std::string o; for (; *s; s++) { if (!o.empty()) o += '-'; o += *s; } return o; }
// La letra (abecedario o tilde) que tiene esos puntos, tal como sale en pantalla
std::string letraDePuntos(int p) {
  for (int i = 0; i < 33; i++) if (puntos(ONCE[i].puntos) == p) return ONCE[i].pantalla;
  return "";
}
int puntosDeLetra(const std::string &pantalla) {
  for (int i = 0; i < 33; i++) if (pantalla == ONCE[i].pantalla) return puntos(ONCE[i].puntos);
  return -1;
}

// El LCD no tiene tildes: el texto que se desplaza va sin ellas.
std::string plegarAscii(const char *utf8) {
  std::string o;
  for (const unsigned char *p = (const unsigned char *)utf8; *p;) {
    if (*p < 0x80) { o += (char)*p++; continue; }
    if (p[0] == 0xC3 && p[1]) {
      const unsigned char c = p[1];
      const char *m = "?";
      switch (c) {
        case 0xA1: case 0xA0: m = "a"; break; case 0x81: m = "A"; break;
        case 0xA9: case 0xA8: m = "e"; break; case 0x89: m = "E"; break;
        case 0xAD: case 0xAC: m = "i"; break; case 0x8D: m = "I"; break;
        case 0xB3: case 0xB2: m = "o"; break; case 0x93: m = "O"; break;
        case 0xBA: case 0xBC: m = "u"; break; case 0x9A: case 0x9C: m = "U"; break;
        case 0xB1: m = "n"; break; case 0x91: m = "N"; break;
      }
      o += m;
      p += 2;
      continue;
    }
    while (*p >= 0x80) p++;
    o += '?';
  }
  return o;
}

// ---------------------------------------------------------------------
// CASOS
// ---------------------------------------------------------------------

// Placa de fabrica: se abre el asistente, se calibra, y al menu.
void casoFabrica() {
  arrancar();
  comprobar(seVio("   BIENVENIDO", "     DOTLY"), "bienvenida \"BIENVENIDO\" / \"DOTLY\"");
  comprobar(esperarFila(0, "CALIBRAR TECLADO", 8000), "sin calibracion guardada se abre el asistente");
  const bool ok = calibrar();
  comprobar(ok, "el asistente mide los 5 botones");
  comprobar(esperarContiene(1, "Guardado", 3000), "y lo guarda (\"%s\")", fila(1).c_str());
  pantallaEs("DOTLY        1/9", "> Aprender", "al terminar, el menu principal", 6000);
  // los rangos medidos: cada boton contiene su tension y no se pisan
  bool contiene = true, separados = true;
  for (int k = 0; k < KEYPAD_MAP_SIZE; k++) {
    const int sw = KEYPAD_MAP[k].id;
    if (!(KEYPAD_MAP[k].mvMin <= MV_BOTON[sw] && MV_BOTON[sw] <= KEYPAD_MAP[k].mvMax)) contiene = false;
    for (int j = k + 1; j < KEYPAD_MAP_SIZE; j++)
      if (!(KEYPAD_MAP[k].mvMax < KEYPAD_MAP[j].mvMin || KEYPAD_MAP[j].mvMax < KEYPAD_MAP[k].mvMin)) separados = false;
    std::printf("         %s %d..%d mV\n", BTN_NOMBRE[sw], KEYPAD_MAP[k].mvMin, KEYPAD_MAP[k].mvMax);
  }
  comprobar(contiene, "cada rango contiene la tension de su boton");
  comprobar(separados, "ningun rango se solapa con otro");
  comprobar(keypadHasCalibration(), "la calibracion queda en la memoria");
}

// Encender con un boton pulsado abre el asistente aunque haya calibracion.
void casoMantenida() {
  g_adcMv = MV_BOTON[1];
  arrancar();
  comprobar(esperarFila(0, "CALIBRAR TECLADO", 8000), "con SW1 pulsado al encender se abre el asistente");
  comprobar(esperarFila(1, "Suelta botones..", 3000), "y pide soltar primero");
  soltar();
  comprobar(calibrar(), "se puede volver a calibrar");
  pantallaEs("DOTLY        1/9", "> Aprender", "y vuelve al menu", 6000);
}

// El menu del codigo base, con sus mismas pantallas y teclas.
void casoMenuBase() {
  arrancar();
  pantallaEs("DOTLY        1/9", "> Aprender", "con calibracion guardada, directo al menu");
  pulsar(SW1);
  pantallaEs("SELECCIONA", "> BLOQUE 1", "SW1: el menu de bloques del codigo base");
  pulsar(SW5);
  pantallaEs("SELECCIONA", "> BLOQUE 1", "SW5 repite el menu");
  pulsar(SW1);
  pantallaEs("BLOQUE 1", "A B C D E F", "SW1: la pantalla del BLOQUE 1 del codigo base");
  pulsar(SW5);
  pantallaEs("BLOQUE 1", "A B C D E F", "SW5 repite el bloque");
  pulsar(SW4);
  pantallaEs("SELECCIONA", "> BLOQUE 1", "SW4 vuelve al menu de bloques");

  // El fallo del codigo base: SW2 en el menu no tenia accion y dejaba
  // botonLiberado a false para siempre. Ahora cambia de bloque y el teclado
  // sigue vivo.
  pulsar(SW2);
  pantallaEs("SELECCIONA", "> SIGNOS", "SW2 en el menu ya no bloquea: bloque anterior");
  pulsar(SW3);
  pulsar(SW3);
  pantallaEs("SELECCIONA", "> BLOQUE 2", "SW3 siguiente bloque");
  pulsar(SW1);
  pantallaEs("BLOQUE 2", "G H I J K L", "el teclado sigue respondiendo");
  pulsar(SW3);
  comprobar(esperarQue([] { return fila(1) == "Puntos 1-2-4-5"; }), "SW3: letra a letra, primero la G (1-2-4-5)");
  pulsar(SW4);
  pantallaEs("BLOQUE 2", "G H I J K L", "SW4 vuelve al bloque");
  pulsar(SW4);
  pulsar(SW4);
  pantallaEs("DOTLY        1/9", "> Aprender", "y SW4 otra vez, al menu principal");

  // Una pulsacion larga es UNA pulsacion (en SW1 no hay autorepeticion)
  g_adcMv = MV_BOTON[1];
  esperar(2500);
  soltar();
  esperar(200);
  pantallaEs("SELECCIONA", "> BLOQUE 2", "SW1 mantenido 2,5 s entra una sola vez (y recuerda el bloque)");
  pulsar(SW4);
  // ...y en SW3 se repite sola, para correr por las listas
  serieLimpiar();
  g_adcMv = MV_BOTON[3];
  esperar(1600);
  soltar();
  esperar(200);
  const std::string log = serieLog();
  int n = 0;
  for (size_t p = 0; (p = log.find("[TECLA] SW3", p)) != std::string::npos; p++) n++;
  const std::string f1 = fila(1);
  comprobar(n == 1 && f1 != "> Aprender", "SW3 mantenido avanza varias opciones (\"%s\")", f1.c_str());
}

// Cada signo del firmware, contra la tabla independiente del banco.
void casoLetras() {
  arrancar();
  int bien = 0, mal = 0;
  for (int b = 0; b < N_BLOQUES; b++) {
    const Resp r = POST("/api/modo", "m=aprender&b=" + std::to_string(b));
    if (r.codigo != 200 || !esperarFila(0, BLOQUES[b].nombre)) { mal++; volcar(); continue; }
    // la fila de abajo del bloque: sus letras
    std::string esperado;
    for (int k = 0; k < BLOQUES[b].cuantos; k++) {
      const Esperado &e = ONCE[BLOQUES[b].desde + k];
      esperado += e.pantalla;
      if (BLOQUES[b].paso == 2 && k + 1 < BLOQUES[b].cuantos) esperado += ' ';
    }
    comprobar(esperarFila(1, esperado), "%s: \"%s\"", BLOQUES[b].nombre, esperado.c_str());
    tecla(SW3);
    for (int k = 0; k < BLOQUES[b].cuantos; k++) {
      const Esperado &e = ONCE[BLOQUES[b].desde + k];
      const bool numero = (BLOQUES[b].desde + k) >= 33 && (BLOQUES[b].desde + k) < 43;
      char cuenta[8];
      snprintf(cuenta, sizeof(cuenta), "%d/%d", k + 1, BLOQUES[b].cuantos);
      esperarQue([&] { return posiciones(0)[0] == e.pantalla && lcdFila(0).find(cuenta) != std::string::npos; });
      const std::string texto = numero ? "3456 + " + guiones(e.puntos) : "Puntos " + guiones(e.puntos);
      const bool okLetra = posiciones(0)[0] == e.pantalla;
      const bool okCelda = numero ? (lcdCeldaEn(3, 0) == ONCE_NUMERO && lcdCeldaEn(4, 0) == puntos(e.puntos))
                                  : lcdCeldaEn(3, 0) == puntos(e.puntos);
      const bool okTexto = esperarFila(1, texto, 500);
      if (okLetra && okCelda && okTexto) bien++;
      else { mal++; std::printf("   FALLO  signo \"%s\": se esperaba %s\n", e.clave, texto.c_str()); volcar(); fallos++; }
      tecla(SW3);
    }
  }
  comprobar(mal == 0 && bien == N_ONCE, "los %d signos coinciden en letra, celda y puntos (%d bien)", N_ONCE, bien);
}

// Escribir: el editor de puntos, el signo de numero y el borrado.
void casoEscribir() {
  arrancar();
  pulsar(SW3);
  pantallaEs("DOTLY        2/9", "> Escribir", "SW3: Escribir");
  pulsar(SW1);
  comprobar(esperarQue([] { return lcdFila(1).rfind("1 2 3 4 5 6 <", 0) == 0; }), "el editor: \"1 2 3 4 5 6 <\"");
  int c, f;
  comprobar(lcdParpadeo(&c, &f) && c == 0 && f == 1, "el cursor parpadea sobre el punto 1 (%d,%d)", c, f);
  // H = 1-2-5
  pulsar(SW1); pulsar(SW3); pulsar(SW1); pulsar(SW3); pulsar(SW3); pulsar(SW3); pulsar(SW1);
  comprobar(esperarQue([] { return lcdFila(1).rfind("█ █ 3 4 █ 6 <", 0) == 0; }), "marcados 1, 2 y 5");
  comprobar(esperarQue([] { return posiciones(1)[15] == "H"; }), "y dice que es la H");
  comprobar(lcdCeldaEn(15, 0) == 0x13, "con su celda arriba a la derecha");
  pulsar(SW5);
  comprobar(esperarQue([] { return lcdFila(0).rfind("H_", 0) == 0; }), "SW5 la escribe");
  // Numero: 3-4-5-6 y luego la a -> "1"
  comprobar(POST("/api/escribir", "p=60").codigo == 200, "signo de numero por la web");
  comprobar(esperarContiene(0, "#"), "se marca el modo numero");
  comprobar(POST("/api/escribir", "p=1").codigo == 200, "y la a vale 1");
  comprobar(esperarQue([] { return lcdFila(0).rfind("H1_", 0) == 0; }), "\"H1\"");
  comprobar(POST("/api/escribir", "p=0").codigo == 200 && POST("/api/escribir", "p=1").codigo == 200,
            "tras un espacio la a vuelve a ser letra");
  comprobar(esperarQue([] { return lcdFila(0).rfind("H1 A_", 0) == 0; }), "\"H1 A\"");
  // Una combinacion que no existe: parpadea la luz y no se escribe nada
  const int apagones = lcdApagones();
  pulsar(SW3); pulsar(SW3); pulsar(SW3); pulsar(SW1);      // solo el punto 4
  pulsar(SW5);
  comprobar(esperarQue([&] { return lcdApagones() >= apagones + 2; }),
            "el punto 4 solo no es ninguna letra: la luz parpadea");
  comprobar(esperarQue([] { return lcdFila(0).rfind("H1 A_", 0) == 0; }), "y no se escribe nada");
  const Resp r = POST("/api/escribir", "p=8");
  comprobar(r.codigo == 400, "por la web, la misma combinacion da 400 (%d)", r.codigo);
  // '<' borra: el cursor del editor va a la ultima posicion con SW2
  pulsar(SW1);                                              // desmarca el 4
  pulsar(SW2); pulsar(SW2); pulsar(SW2); pulsar(SW2);       // 3 -> 2 -> 1 -> 0 -> '<'
  pulsar(SW1);
  comprobar(esperarQue([] { return lcdFila(0).rfind("H1 _", 0) == 0; }), "\"<\" borra la ultima letra");
  comprobar(POST("/api/escrito/borrar").codigo == 200 && esperarQue([] { return lcdFila(0).rfind("_", 0) == 0; }),
            "borrar todo desde la web");
  pulsar(SW4);
  pantallaEs("DOTLY        2/9", "> Escribir", "SW4 sale al menu");
}

// Los tres retos, respondiendo como una persona que sabe (y una vez mal).
void casoRetos() {
  arrancar();
  comprobar(POST("/api/ajustes", "preguntas=5&velocidad=2&nivel=4").codigo == 200, "5 preguntas, velocidad rapida");

  // ---- Reto: leer ----
  comprobar(POST("/api/modo", "m=reto_leer").codigo == 200, "empieza el reto de leer");
  for (int q = 1; q <= 5; q++) {
    char cuenta[8];
    snprintf(cuenta, sizeof(cuenta), "%d/5", q);
    if (!esperarQue([&] { return lcdFila(0).find("= ?") != std::string::npos && lcdFila(0).find(cuenta) != std::string::npos; })) {
      comprobar(false, "pregunta %d", q); volcar(); return;
    }
    const int celda = lcdCeldaEn(0, 0);
    const std::string buena = letraDePuntos(celda);
    const std::vector<std::string> v = posiciones(1);
    int j = -1, sel = -1;
    for (int k = 0; k < 4; k++) {
      if (v[k * 4 + 1] == buena) j = k;
      if (v[k * 4] == ">") sel = k;
    }
    if (j < 0 || sel < 0 || buena.empty()) { comprobar(false, "la respuesta %s esta entre las opciones", buena.c_str()); volcar(); return; }
    const int elegir = (q == 2) ? (j + 1) % 4 : j;          // la segunda, mal a proposito
    for (int k = 0; k < (elegir - sel + 4) % 4; k++) pulsar(SW3);
    const int apagones = lcdApagones();
    pulsar(SW1);
    if (q == 2) {
      comprobar(esperarContiene(1, "Era"), "respuesta mala: \"%s\"", fila(1).c_str());
      comprobar(esperarQue([&] { return lcdApagones() >= apagones + 2; }), "y la luz parpadea");
      comprobar(posiciones(1)[4] == buena, "ensena cual era (%s)", buena.c_str());
    } else {
      comprobar(esperarContiene(1, "Bien!"), "pregunta %d: %s bien", q, buena.c_str());
    }
  }
  comprobar(esperarFila(0, "Resultado 4/5", 5000), "al final: \"Resultado 4/5\" (%s)", fila(0).c_str());
  comprobar(suma(prog.leerTot, N_APRENDER) == 5 && suma(prog.leerOk, N_APRENDER) == 4, "se apunta en el progreso");

  // ---- Reto: formar ----
  pulsar(SW4);
  comprobar(POST("/api/modo", "m=reto_formar").codigo == 200, "empieza el reto de formar");
  for (int q = 1; q <= 5; q++) {
    char cuenta[8];
    snprintf(cuenta, sizeof(cuenta), "%d/5", q);
    if (!esperarQue([&] { return lcdFila(0).rfind("Forma:", 0) == 0 && lcdFila(0).find(cuenta) != std::string::npos &&
                                 lcdFila(1).rfind("1 2 3 4 5 6", 0) == 0; })) {
      comprobar(false, "pregunta de formar %d", q); volcar(); return;
    }
    const std::string letra = posiciones(0)[7];
    const int p = puntosDeLetra(letra);
    for (int d = 0; d < 6; d++) { if (p & (1 << d)) tecla(SW1); tecla(SW3); }
    comprobar(esperarQue([&] { return lcdCeldaEn(15, 1) == p; }), "formada la %s con sus puntos", letra.c_str());
    tecla(SW5);
    comprobar(esperarContiene(1, "Bien!"), "y la da por buena");
  }
  comprobar(esperarFila(0, "Resultado 5/5", 5000), "\"Resultado 5/5\"");

  // ---- Reto: señas ----
  pulsar(SW4);
  comprobar(POST("/api/modo", "m=reto_senas").codigo == 200, "empieza el reto de senas");
  for (int q = 1; q <= 5; q++) {
    char num[4];
    snprintf(num, sizeof(num), "%d", q);
    if (!esperarQue([&] { return posiciones(1)[15] == num && lcdFila(1).find(">") != std::string::npos; })) {
      comprobar(false, "pregunta de senas %d", q); volcar(); return;
    }
    // Lee la descripcion mientras se desplaza, hasta que solo encaje en una opcion
    std::vector<int> ops;
    const std::vector<std::string> v = posiciones(1);
    for (int k = 0; k < 4; k++) {
      for (int i = 0; i < N_LETRAS; i++) if (ONCE[i].pantalla == v[k * 4 + 1]) ops.push_back(i);
    }
    std::string leido = lcdFila(0), previo = leido;
    int unica = -1;
    esperarQue([&] {
      const std::string ahora = lcdFila(0);
      if (ahora != previo && ahora.substr(0, 15) == previo.substr(1)) leido += ahora.substr(15);
      previo = ahora;
      int n = 0;
      for (int i : ops) {
        const std::string d = plegarAscii(senaTexto[i]) + std::string(20, ' ');
        if (d.rfind(recortar(leido), 0) == 0) { n++; unica = i; }
      }
      return n == 1;
    }, 20000);
    if (unica < 0) { comprobar(false, "la descripcion encaja con una opcion"); volcar(); return; }
    int j = 0, sel = 0;
    const std::vector<std::string> w = posiciones(1);
    for (int k = 0; k < 4; k++) { if (w[k * 4 + 1] == ONCE[unica].pantalla) j = k; if (w[k * 4] == ">") sel = k; }
    for (int k = 0; k < (j - sel + 4) % 4; k++) tecla(SW3);
    tecla(SW1);
    comprobar(esperarContiene(0, "Bien!"), "sena %d: \"%.24s...\" es la %s", q, senaTexto[unica], ONCE[unica].pantalla);
  }
  comprobar(esperarFila(0, "Resultado 5/5", 5000), "\"Resultado 5/5\" en senas");
  comprobar(suma(prog.senasTot, N_LETRAS) == 5, "las senas llevan su propia cuenta");
  pulsar(SW4);
}

void casoPalabras() {
  arrancar();
  comprobar(POST("/api/modo", "m=palabras").codigo == 200, "modo Palabras");
  comprobar(POST("/api/palabras", "lista=" + url("sol,ñandú, Árbol 2")).codigo == 200, "lista nueva desde la web");
  const Resp r = GET("/api/palabras");
  comprobar(r.cuerpo.find("\"cuantas\":3") != std::string::npos, "3 palabras (%s)", r.cuerpo.c_str());
  comprobar(esperarFila(0, "Lee la palabra"), "la palabra sale tapada");
  comprobar(esperarQue([] { return lcdCeldaEn(0, 1) == 0x0E && lcdCeldaEn(2, 1) == 0x15 && lcdCeldaEn(4, 1) == 0x07; }),
            "en braille: s o l");
  pulsar(SW1);
  comprobar(esperarFila(0, "SOL"), "SW1 la destapa: SOL");
  pulsar(SW1);
  comprobar(esperarQue([] { return lcdCeldaEn(0, 1) == 0x3B && lcdCeldaEn(8, 1) == 0x3E; }), "la siguiente: ñ ... ú");
  pulsar(SW1);
  comprobar(esperarFila(0, "ÑANDÚ"), "destapada: \"ÑANDÚ\" con sus glifos (%s)", fila(0).c_str());
  pulsar(SW3);
  comprobar(esperarQue([] { return lcdCeldaEn(0, 1) == 0x37 && lcdCeldaEn(10, 1) == 0x00 &&
                             lcdCeldaEn(12, 1) == 0x3C && lcdCeldaEn(14, 1) == 0x03; }),
            "\"Árbol 2\": á r b o l, signo de numero y 2");
  std::string larga;
  while (larga.size() < 620) larga += "palabra,";
  comprobar(POST("/api/palabras", "lista=" + url(larga)).codigo == 400, "una lista de mas de 600 bytes se rechaza");
  comprobar(POST("/api/palabras", "lista=").codigo == 200 &&
            GET("/api/palabras").cuerpo.find("\"cuantas\":38") != std::string::npos, "lista vacia = la de DOTLY (38)");
  pulsar(SW4);
}

void casoSenas() {
  arrancar();
  for (int i = 0; i < 5; i++) pulsar(SW3);
  pantallaEs("DOTLY        6/9", "> Senas", "el menu llega a Senas");
  pulsar(SW1);
  comprobar(esperarQue([] { return lcdFila(0).rfind("Senas: A", 0) == 0 && lcdCeldaEn(9, 0) == 0x01; }),
            "\"Senas: A\" con su celda");
  comprobar(esperarQue([] { return fila(1).rfind("Puno cerrado con", 0) == 0; }), "y como se hace (\"%s\")", fila(1).c_str());
  comprobar(esperarQue([] { return fila(1).rfind("uno cerrado con", 0) == 0; }, 4000), "el texto se desplaza");
  const std::string nuevo = "Mano cerrada con el pulgar al lado (LSE).";
  comprobar(POST("/api/senas", "i=0&d=" + url(nuevo)).codigo == 200, "se cambia la de la A desde la web");
  comprobar(esperarQue([] { return fila(1).rfind("Mano cerrada con", 0) == 0; }), "y se ve al momento");
  comprobar(GET("/api/senas").cuerpo.find("\"propia\":true") != std::string::npos, "marcada como cambiada");
  comprobar(POST("/api/senas", "i=0&d=").codigo == 200 && esperarQue([] { return fila(1).rfind("Puno", 0) == 0; }),
            "vacia = vuelve la original");
  comprobar(POST("/api/senas", "i=0&d=" + url(std::string(121, 'x'))).codigo == 400, "mas de 120 bytes se rechaza");
  comprobar(POST("/api/senas", "i=27&d=hola").codigo == 400, "letra fuera de rango se rechaza");
  pulsar(SW3);
  comprobar(esperarQue([] { return lcdFila(0).rfind("Senas: B", 0) == 0; }), "SW3: la B");
  // Deletreo
  comprobar(POST("/api/deletrear", "t=" + url("¡Hola!")).codigo == 200, "deletrear \"¡Hola!\"");
  comprobar(esperarQue([] { return lcdFila(0).rfind("H", 0) == 0 && lcdFila(0).find("1/4") != std::string::npos &&
                                   lcdFila(0).find("HOLA") != std::string::npos && lcdCeldaEn(2, 0) == 0x13; }),
            "H, 1/4, HOLA");
  comprobar(esperarQue([] { return lcdFila(0).find("2/4") != std::string::npos; }, 3500), "pasa sola a la O");
  comprobar(POST("/api/deletrear", "t=123").codigo == 400, "sin letras no hay deletreo");
  pulsar(SW4);
  comprobar(esperarQue([] { return lcdFila(0).rfind("Senas:", 0) == 0; }), "SW4 deja el deletreo");
  pulsar(SW4);
}

void casoWeb() {
  arrancar();
  const Resp pag = GET("/");
  comprobar(pag.codigo == 200 && pag.cuerpo.size() == strlen(PAGINA_WEB), "GET / sirve la pagina entera (%zu bytes)", pag.cuerpo.size());
  comprobar(pag.cuerpo.find("<title>DOTLY</title>") != std::string::npos, "es la de DOTLY");
  comprobar(pag.cabeceras.find("text/html; charset=utf-8") != std::string::npos, "como HTML en UTF-8");

  // Cada /api/... que usa la pagina existe en el firmware
  std::set<std::string> usadas;
  for (size_t p = 0; (p = pag.cuerpo.find("'/api/", p)) != std::string::npos; p++)
    usadas.insert(pag.cuerpo.substr(p + 1, pag.cuerpo.find('\'', p + 1) - p - 1));
  int faltan = 0;
  for (const auto &u : usadas) {
    bool hay = false;
    for (const auto &r : webRutas()) if (r.first == u) hay = true;
    if (!hay) { std::printf("   FALLO  la pagina usa %s y el firmware no la tiene\n", u.c_str()); faltan++; }
  }
  comprobar(faltan == 0 && usadas.size() >= 13, "las %zu rutas que usa la pagina existen", usadas.size());

  // Portal cautivo
  const Resp android = http("GET", "/generate_204", "", "connectivitycheck.gstatic.com");
  comprobar(android.codigo == 302 && android.cabeceras.find("Location: http://192.168.4.1/") != std::string::npos,
            "portal cautivo: Android va a la pagina (%d)", android.codigo);
  const Resp apple = http("GET", "/hotspot-detect.html", "", "captive.apple.com");
  comprobar(apple.codigo == 302, "y un iPhone tambien");
  comprobar(GET("/no-existe").codigo == 404, "una ruta que no existe en nuestra IP da 404");

  // Estado
  const Resp e = GET("/api/estado");
  comprobar(e.codigo == 200 && e.cuerpo.find("\"modo\":\"menu\"") != std::string::npos, "estado: en el menu");
  comprobar(e.cuerpo.find("\"pantalla\":[\"DOTLY        1/9\",\"> Aprender") != std::string::npos,
            "estado: el espejo de la pantalla");
  comprobar(e.cuerpo.find("\"ssid\":\"DOTLY-1A2B\"") != std::string::npos, "la red se llama DOTLY-1A2B");
  comprobar(WiFi.ssid == "DOTLY-1A2B" && WiFi.clave == "dotly1234" && WiFi.modo == WIFI_AP, "punto de acceso con clave");

  // Teclas y modos
  comprobar(POST("/api/tecla", "b=9").codigo == 400, "tecla 9 no existe");
  tecla(SW3);
  pantallaEs("DOTLY        2/9", "> Escribir", "la tecla SW3 de la pagina mueve el menu");
  comprobar(POST("/api/modo", "m=nada").codigo == 400, "modo desconocido: 400");

  // Pizarra
  comprobar(POST("/api/pizarra", "t=" + url("Hola 12")).codigo == 200, "pizarra \"Hola 12\"");
  const int esperadas[8] = { 0x28, 0x13, 0x15, 0x07, 0x01, 0x00, 0x3C, 0x01 };
  comprobar(esperarQue([&] { for (int k = 0; k < 8; k++) if (lcdCeldaEn(k * 2, 1) != esperadas[k]) return false; return true; }),
            "mayuscula, h o l a, espacio, numero, 1");
  comprobar(posiciones(0)[15] == "1", "pagina 1 de 2");
  tecla(SW3);
  comprobar(esperarQue([] { return lcdCeldaEn(0, 1) == 0x03 && posiciones(0)[0] == "2"; }), "en la segunda: el 2");
  comprobar(POST("/api/pizarra", "t=" + url("@@@")).codigo == 400, "sin nada escribible: 400");

  // Ajustes y red
  comprobar(POST("/api/ajustes", "clave=123").codigo == 400, "clave de 3 caracteres: rechazada");
  comprobar(POST("/api/ajustes", "ssid=").codigo == 400, "nombre vacio: rechazado");
  comprobar(POST("/api/ajustes", "preguntas=10").codigo == 200 && POST("/api/ajustes", "preguntas=7").codigo == 200 &&
            GET("/api/ajustes").cuerpo.find("\"preguntas\":10") != std::string::npos,
            "7 preguntas no se acepta: se quedan 10");
  const Resp red = POST("/api/ajustes", "ssid=" + url("Aula 3") + "&clave=braille2024");
  comprobar(red.cuerpo.find("\"reiniciar\":true") != std::string::npos, "red nueva: pide reiniciar");
  comprobar(POST("/api/ajustes", "ssid=" + url("Aula 3") + "&clave=braille2024").cuerpo.find("\"reiniciar\":false") != std::string::npos,
            "la misma otra vez: no hace falta");
  comprobar(POST("/api/senas", "i=1&d=" + url("Palma al frente, dedos juntos (seña propia).")).codigo == 200,
            "una sena propia para la B (se mira tras reiniciar)");
  const int antes = g_reinicios;
  comprobar(POST("/api/reiniciar").codigo == 200 && esperarQue([&] { return g_reinicios > antes; }), "reiniciar desde la web");

  // Calibrar desde la web bloquea las teclas de la pagina hasta que acabe
  comprobar(POST("/api/calibrar").codigo == 200 && esperarFila(0, "CALIBRAR TECLADO"), "calibrar desde la web");
  comprobar(POST("/api/tecla", "b=1").codigo == 409, "mientras, la pagina no manda teclas (409)");
  comprobar(calibrar(), "y se completa con el teclado");
}

void casoPersistencia() {
  arrancar();
  comprobar(WiFi.ssid == "Aula 3" && WiFi.clave == "braille2024", "tras reiniciar, la red nueva (\"%s\")", WiFi.ssid.c_str());
  comprobar(aj.preguntas == 10 && aj.velocidad == 2 && aj.nivel == 4, "los ajustes siguen ahi");
  comprobar(strstr(senaTexto[1], "propia") != nullptr, "y la sena propia de la B");
  comprobar(suma(prog.leerTot, N_APRENDER) == 5 && suma(prog.formarOk, N_APRENDER) == 5 &&
            suma(prog.senasOk, N_LETRAS) == 5, "y el progreso de los retos");
  const Resp p = GET("/api/progreso");
  comprobar(p.codigo == 200 && p.cuerpo.find("\"rondas\":3") != std::string::npos, "3 rondas terminadas");
  comprobar(POST("/api/progreso/borrar").codigo == 200 && suma(prog.leerTot, N_APRENDER) == 0, "borrar el progreso");
  comprobar(POST("/api/senas", "i=1&d=").codigo == 200, "la B vuelve a la original");
}

// Ruido en el ADC y pulsaciones que pasan por la tension de otros botones.
void casoRuido() {
  arrancar();
  g_adcRuido = 40;
  serieLimpiar();
  for (int i = 0; i < 10; i++) pulsarConRampa(SW3);
  esperar(300);
  const std::string log = serieLog();
  int sw3 = 0, otras = 0;
  for (size_t p = 0; (p = log.find("[TECLA] ", p)) != std::string::npos; p++) {
    if (log.compare(p + 8, 3, "SW3") == 0) sw3++; else otras++;
  }
  comprobar(sw3 == 10 && otras == 0, "10 pulsaciones con +-40 mV de ruido y rampa: 10 SW3, %d otras (%d SW3)", otras, sw3);
  pantallaEs("DOTLY        2/9", "> Escribir", "el menu avanza 10 (vuelta y una)");
  g_adcRuido = 0;
  serieMeter("c");
  comprobar(esperarFila(0, "CALIBRAR TECLADO"), "'c' por el monitor serie abre el asistente");
  comprobar(calibrar(), "y se completa");
}

// Sin teclado enchufado (pin al aire): no hay asistente; se maneja por la web.
void casoSinTeclado() {
  g_adcMv = 150;
  g_adcRuido = 300;
  arrancar();
  pantallaEs("DOTLY        1/9", "> Aprender", "pin al aire: directo al menu, sin asistente");
  const Resp e = GET("/api/estado");
  comprobar(e.cuerpo.find("\"conectado\":false") != std::string::npos, "la pagina sabe que no hay teclado");
  esperar(1500);
  pantallaEs("DOTLY        1/9", "> Aprender", "el ruido no se toma por pulsaciones");
  tecla(SW3);
  pantallaEs("DOTLY        2/9", "> Escribir", "los botones de la pagina si funcionan");
}

// Deja DOTLY encendido para el navegador (e2e_pagina.js).
void servidor() {
  g_adcRuido = 4;
  arrancar();
  if (fila(0) == "CALIBRAR TECLADO") calibrar();
  esperarFila(0, "DOTLY        1/9", 8000);
  std::printf("PUERTO %d\n", webPuerto());
  std::fflush(stdout);
  char b[64];
  while (fgets(b, sizeof(b), stdin)) {}          // hasta que cierren la entrada
  vivo = false;
  if (ui.joinable()) ui.join();
  if (vigilante.joinable()) vigilante.join();
}

}  // namespace banco

int main(int argc, char **argv) {
  setvbuf(stdout, nullptr, _IOLBF, 0);
  srand(12345);
  const std::string caso = argc > 1 ? argv[1] : "";
  using namespace banco;
  if (caso == "servidor") { servidor(); return 0; }
  if (caso == "fabrica") casoFabrica();
  else if (caso == "mantenida") casoMantenida();
  else if (caso == "menubase") casoMenuBase();
  else if (caso == "letras") casoLetras();
  else if (caso == "escribir") casoEscribir();
  else if (caso == "retos") casoRetos();
  else if (caso == "palabras") casoPalabras();
  else if (caso == "senas") casoSenas();
  else if (caso == "web") casoWeb();
  else if (caso == "persistencia") casoPersistencia();
  else if (caso == "ruido") casoRuido();
  else if (caso == "sinteclado") casoSinTeclado();
  else {
    std::printf("caso desconocido: %s\n", caso.c_str());
    return 2;
  }
  return terminar();
}
