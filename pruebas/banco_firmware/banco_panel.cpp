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

// Banco de pruebas del firmware del PANEL: calibra el teclado, entra en
// Auto-Chequeo y comprueba que el pulso medido coincide con el simulado.
#include <Arduino.h>
#include <U8g2lib.h>
#include <MAX30105.h>
#include <string>
#include <vector>

void setup();
void loop();
static std::atomic<bool> corriendo{true};
static void hiloUI() { while (corriendo.load()) loop(); }
static void esperar(uint32_t ms) { delay(ms); }
static int fallos = 0;
static void comprobar(bool c, const char *q) { printf("   %s %s\n", c ? "OK  " : "FALLO", q); if (!c) fallos++; }
// Holgura de sobra sobre el minimo real (EMA ~50 ms + 40 ms de antirrebote)
static void pulsar(int mv, const char *n) { printf("   [tecla] %s\n", n); g_adcMv = mv; esperar(300); g_adcMv = 3200; esperar(220); }
#define OK()   pulsar(1500, "OK")
#define DOWN() pulsar(10, "ABAJO")
static std::string pantalla() { std::string l; for (auto &s : pantallaUltimoFrame()) { l += "\""; l += s; l += "\" "; } return l; }
static void volcar(const char *e) { printf("   [pantalla %s] %s\n", e, pantalla().c_str()); }
static bool esperarTexto(const char *f, uint32_t t) {
  const uint32_t t0 = millis();
  while (millis() - t0 < t) { if (pantallaContiene(f)) return true; esperar(50); }
  printf("      (no aparecio <%s>; pantalla: %s)\n", f, pantalla().c_str());
  return false;
}
static int numeroTras(const char *p) {
  for (auto &s : pantallaUltimoFrame()) { const size_t i = s.find(p); if (i != std::string::npos) return atoi(s.c_str() + i + strlen(p)); }
  return -1;
}
static int mvDeBoton(const std::string &n) {
  if (n == "ARRIBA") return 2500;
  if (n == "ABAJO")  return 10;
  if (n == "OK")     return 1500;
  if (n == "ATRAS")  return 700;
  return -1;
}
static void atenderAsistente() {
  if (!esperarTexto("CALIBRAR TECLADO", 4000)) return;
  const uint32_t t0 = millis();
  while (millis() - t0 < 90000) {
    if (pantallaContiene("botones OK")) break;
    // El asistente pide la tecla ("Pulsa y manten") y, una vez empieza a
    // acumular muestras, cambia el rotulo a "SIGUE PULSANDO". En los dos casos
    // hay que mantener el boton: soltarlo a mitad tira las muestras.
    if (pantallaContiene("Pulsa y manten") || pantallaContiene("SIGUE PULSANDO")) {
      int mv = -1;
      for (auto &s : pantallaUltimoFrame()) if (mvDeBoton(s) > 0) mv = mvDeBoton(s);
      if (mv > 0) g_adcMv = mv;
    } else g_adcMv = 3200;
    esperar(100);
  }
  g_adcMv = 3200;
  volcar("calibracion");
}

int main(int argc, char **argv) {
  const int bpmReal = argc > 1 ? atoi(argv[1]) : 72;
  const std::string caso = argc > 2 ? argv[2] : "normal";
  const bool botonPulsado = (caso == "botonpulsado");
  const bool tecladoSuelto = (caso == "tecladosuelto");
  sensorSim.bpm = bpmReal;
  sensorSim.dedo = false;
  if (botonPulsado) g_adcMv = 2500;          // ARRIBA mantenido al encender
  if (tecladoSuelto) { g_adcMv = 150; g_adcRuido = 200; }   // GPIO34 al aire
  const bool calibRuido = (caso == "calibruido");
  if (calibRuido) g_adcRuido = 55;          // cable largo: la lectura no para quieta
  printf("== PANEL: auto-chequeo con pulso simulado de %d BPM%s ==\n", bpmReal,
         botonPulsado ? " (boton mantenido al encender)" : "");
  setup();
  std::thread(hiloUI).detach();

  if (tecladoSuelto) {
    // Con el pin al aire las lecturas caen dentro del rango de un boton: el
    // panel NO puede abrir el asistente ni navegar solo por los menus.
    comprobar(!esperarTexto("CALIBRAR TECLADO", 6000),
              "con el teclado desconectado NO se abre el asistente");
    printf("   [hardware] se conecta el teclado\n");
    g_adcRuido = 0;
    g_adcMv = 3200;
    esperar(1500);
  }
  if (botonPulsado) {
    // Se mantiene mas de WIZ_REPOSO_MS: si el reposo se midiera aqui, saldria
    // 2500 mV y la tabla guardada dejaria el teclado inservible.
    comprobar(esperarTexto("CALIBRAR TECLADO", 5000),
              "mantener un boton al encender abre el asistente");
    esperar(2500);
    printf("   [usuario] suelta el boton\n");
    g_adcMv = 3200;
  }
  if (!tecladoSuelto) atenderAsistente();
  if (calibRuido) {
    // Calibrar con la lectura bailando: el asistente promedia 64 lecturas de
    // 25 conversiones, asi que el centro sale bien y el rango ancho.
    comprobar(pantallaContiene("4 de 4 botones OK"), "captura los 4 botones pese al ruido");
    comprobar(pantallaContiene("Guardado en memoria"), "y los guarda");
  }
  if (botonPulsado) {
    comprobar(pantallaContiene("4 de 4 botones OK"),
              "mide el reposo DESPUES de soltar y captura los 4 botones");
    comprobar(pantallaContiene("Guardado en memoria"), "y los guarda");
  }
  esperar(3000);
  if (!pantallaContiene("Auto-Chequeo")) OK();          // cara de reposo -> menu
  comprobar(esperarTexto("Auto-Chequeo", 12000), "el menu queda operativo");
  OK();
  comprobar(esperarTexto("Coloque el dedo", 6000), "pide el dedo");
  esperar(1500);
  printf("   [usuario] pone el dedo\n");
  sensorSim.dedo = true;
  comprobar(esperarTexto("BPM", 12000), "muestra el pulso en vivo");
  comprobar(esperarTexto("TUS RESULTADOS", 45000), "llega a los resultados");
  volcar("resultados");
  const int visto = numeroTras("Latidos: ");
  comprobar(abs(visto - bpmReal) <= 2, "el pulso medido coincide con el real");
  printf("   muestras: generadas=%ld entregadas=%ld perdidas=%ld\n",
         sensorSim.generadas.load(), sensorSim.entregadas.load(), sensorSim.perdidas.load());
  corriendo = false;
  esperar(200);
  printf("== panel: %s ==\n", fallos == 0 ? "OK" : "CON FALLOS");
  return fallos == 0 ? 0 : 1;
}
