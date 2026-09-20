// Banco de pruebas del firmware de triaje MEDIBOT.
// Ejecuta el sketch en el PC con un MAX30102 simulado y recorre la interfaz
// como lo haria una persona con los botones.
#include <Arduino.h>
#include <U8g2lib.h>
#include <MAX30105.h>
#include <nvs_flash.h>
#include <WiFi.h>
#include <string>
#include <vector>

void setup();
void loop();

static std::atomic<bool> corriendo{true};
static void hiloUI() { while (corriendo.load()) loop(); }
static void esperar(uint32_t ms) { delay(ms); }

static int fallos = 0;
static void comprobar(bool cond, const char *que) {
  printf("   %s %s\n", cond ? "OK  " : "FALLO", que);
  if (!cond) fallos++;
}
// Una pulsacion tarda en registrarse: el EMA del teclado necesita ~5 muestras
// (50 ms) para llegar al rango del boton y luego hay 40 ms de antirrebote. Se
// mantiene bastante mas de ese minimo para que el banco no dependa de como
// reparta el sistema operativo los dos hilos.
static void pulsar(int mv, const char *nombre) {
  printf("   [tecla] %s\n", nombre);
  g_adcMv = mv; esperar(300);
  g_adcMv = 3200; esperar(220);
}
#define OK()   pulsar(1500, "OK")
#define UP()   pulsar(2500, "UP")
#define DOWN() pulsar(10,   "DOWN")
#define BACK() pulsar(700,  "BACK")

static std::string pantalla() {
  std::string l;
  for (auto &s : pantallaUltimoFrame()) { l += "\""; l += s; l += "\" "; }
  return l;
}
static void volcar(const char *etq) { printf("   [pantalla %s] %s\n", etq, pantalla().c_str()); }

// Espera a que un texto DESAPAREZCA de la pantalla
static bool esperarSinTexto(const char *frag, uint32_t msMax) {
  const uint32_t t0 = millis();
  while (millis() - t0 < msMax) {
    if (!pantallaContiene(frag)) return true;
    esperar(100);
  }
  printf("      (<%s> seguia en pantalla tras %u ms)\n", frag, msMax);
  return false;
}

static bool esperarTexto(const char *frag, uint32_t msMax) {
  const uint32_t t0 = millis();
  while (millis() - t0 < msMax) {
    if (pantallaContiene(frag)) return true;
    esperar(50);
  }
  printf("      (no aparecio <%s> en %u ms; pantalla: %s)\n", frag, msMax, pantalla().c_str());
  return false;
}
static int numeroTras(const char *prefijo) {          // "Latidos: 72 x min" -> 72
  for (auto &s : pantallaUltimoFrame()) {
    const size_t p = s.find(prefijo);
    if (p != std::string::npos) return atoi(s.c_str() + p + strlen(prefijo));
  }
  return -1;
}
static int numeroAntes(const char *sufijo) {         // "72 bpm" -> 72
  for (auto &s : pantallaUltimoFrame())
    if (s.find(sufijo) != std::string::npos) return atoi(s.c_str());
  return -1;
}


// --- Asistente de calibracion: hace de "usuario" pulsando lo que pide ---
static int mvDeBoton(const std::string &n) {
  if (n == "ARRIBA") return 2500;
  if (n == "ABAJO")  return 10;
  if (n == "OK")     return 1500;
  if (n == "ATRAS")  return 700;
  return -1;
}
static std::string botonPedido() {
  for (auto &s : pantallaUltimoFrame()) {
    if (mvDeBoton(s) > 0) return s;
  }
  return "";
}
// Recorre el asistente entero. Devuelve false si no estaba abierto.
static bool atenderAsistente(uint32_t msMax = 90000) {
  if (!esperarTexto("CALIBRAR TECLADO", 1500)) return false;
  printf("   [asistente] abierto; se calibran los botones\n");
  const uint32_t t0 = millis();
  std::string ultimo;
  while (millis() - t0 < msMax) {
    if (pantallaContiene("botones OK")) break;          // resumen final
    if (pantallaContiene("Pulsa y manten")) {
      const std::string b = botonPedido();
      if (!b.empty() && b != ultimo) {
        printf("   [asistente] pide %s\n", b.c_str());
        ultimo = b;
      }
      const int mv = mvDeBoton(b);
      if (mv > 0) g_adcMv = mv;
    } else {
      g_adcMv = 3200;                                    // reposo / soltar
      ultimo.clear();
    }
    esperar(100);
  }
  g_adcMv = 3200;
  volcar("resumen calibracion");
  return true;
}

// Entradas del menu del firmware, en orden
enum { M_CHEQUEO = 0, M_MEDIBOT, M_HISTORIAL, M_DIAG, M_CALIBRAR, M_ABOUT };

// Desde el menu (seleccion en la primera entrada), abre la que se pida.
static void abrirDelMenu(int indice) {
  for (int i = 0; i < indice; i++) DOWN();
  OK();
}

// Llega al menu y deja la seleccion en la primera entrada (sin pulsar OK).
static void lanzarChequeoNo() {
  atenderAsistente();
  esperar(3500);
  if (!pantallaContiene("Auto-Chequeo")) OK();
  esperarTexto("Auto-Chequeo", 3000);
}

// Lleva la interfaz desde la cara de reposo hasta el menu y lanza el chequeo.
static void lanzarChequeo() {
  atenderAsistente();
  esperar(3500);                                 // deja pasar el autodiagnostico
  if (!pantallaContiene("Auto-Chequeo")) OK();   // cara de reposo -> menu
  esperarTexto("Auto-Chequeo", 3000);
  OK();
}

int main(int argc, char **argv) {
  const std::string caso = argc > 1 ? argv[1] : "normal";
  const int bpmReal  = argc > 2 ? atoi(argv[2]) : 72;
  const int spo2Real = argc > 3 ? atoi(argv[3]) : 98;

  sensorSim.bpm = bpmReal;
  sensorSim.spo2 = spo2Real;
  sensorSim.dedo = false;
  g_medibotIp = (uint32_t)IPAddress(192, 168, 1, 77);
  if (caso == "sinsensor" || caso == "sensorlento") sensorSim.presente = false;
  if (caso == "max30100")  sensorSim.partId = 0x11;
  if (caso == "sinmemoria") { g_nvsRota = true; g_nvsReparable = false; }
  if (caso == "botonpulsado") g_adcMv = 2500;      // ARRIBA mantenido al encender

  printf("== CASO %s (BPM=%d SpO2=%d) ==\n", caso.c_str(), bpmReal, spo2Real);
  setup();
  std::thread(hiloUI).detach();

  if (caso == "sinsensor" || caso == "max30100") {
    comprobar(esperarTexto("NO DETECTADO", 4000) || esperarTexto("CHIP NO COMPAT", 4000),
              "el arranque avisa de que no hay sensor de pulso");
    volcar("arranque");
    esperar(3000);
    OK(); esperarTexto("Auto-Chequeo", 3000); OK();
    comprobar(esperarTexto("Sensor de pulso ausente", 3000),
              "el menu no deja medir sin sensor y lo explica");
    volcar("error");
    comprobar(esperarTexto("MEDIBOT", 12000), "vuelve solo al menu tras el aviso");
  } else if (caso == "sindedo") {
    lanzarChequeo();
    comprobar(esperarTexto("Coloque su dedo", 4000), "pide el dedo");
    comprobar(esperarTexto("Esperando dedo", 8000), "avisa de que no detecta dedo");
    comprobar(esperarTexto("No se detecto el dedo", 30000),
              "cancela la medida con un motivo claro");
    volcar("error");
    // Reintento INMEDIATO: el fallo anterior no puede cancelar la medida nueva
    comprobar(esperarTexto("Auto-Chequeo", 12000), "vuelve al menu solo");
    printf("   -- reintento inmediato --\n");
    OK();
    comprobar(esperarTexto("Coloque su dedo", 4000), "acepta empezar otra vez");
    esperar(2500);
    sensorSim.dedo = true;
    comprobar(!esperarTexto("Senal no fiable", 6000),
              "el reintento NO se cancela por el fallo anterior");
    comprobar(esperarTexto("TUS RESULTADOS", 45000), "el reintento completa la medida");
    volcar("reintento");
    comprobar(numeroAntes(" bpm") == bpmReal, "el pulso del reintento es correcto");
  } else if (caso == "dedofuera") {
    lanzarChequeo();
    esperarTexto("Coloque su dedo", 4000);
    esperar(2500);
    sensorSim.dedo = true;
    esperarTexto("BPM", 10000);
    esperar(4000);
    printf("   [usuario] levanta el dedo\n");
    sensorSim.dedo = false;
    comprobar(esperarTexto("Esperando dedo", 8000), "detecta que se ha retirado el dedo");
    esperar(2000);
    printf("   [usuario] vuelve a ponerlo\n");
    sensorSim.dedo = true;
    comprobar(esperarTexto("TUS RESULTADOS", 45000), "termina la medida tras recolocar el dedo");
    volcar("resultado");
    comprobar(abs(numeroAntes(" bpm") - bpmReal) <= 2, "el pulso sigue siendo correcto");
  } else if (caso == "calibrar") {
    comprobar(atenderAsistente(), "sin calibracion guardada, el asistente se abre solo");
    comprobar(pantallaContiene("4 de 4 botones OK"),
              "calibra los 4 botones del teclado");
    comprobar(esperarTexto("Auto-Chequeo", 8000), "al terminar deja el menu listo");
    DOWN();
    volcar("menu");
    comprobar(pantallaContiene("MEDIBOT (red)") && pantallaContiene("Historial"),
              "el menu tiene la entrada de MEDIBOT");
    DOWN(); OK();
    comprobar(esperarTexto("HISTORIAL", 3000), "OK entra en la opcion elegida");
    BACK();
    comprobar(esperarTexto("Auto-Chequeo", 3000), "ATRAS vuelve");
  } else if (caso == "yacalibrado") {
    comprobar(esperarTexto("MEDIBOT v6.1", 3000), "arranca en el autodiagnostico");
    comprobar(!pantallaContiene("CALIBRAR TECLADO"),
              "con la calibracion guardada NO se repite el asistente");
    esperar(3000);
    OK();
    comprobar(esperarTexto("Auto-Chequeo", 3000), "los botones guardados funcionan");
    abrirDelMenu(M_CALIBRAR);
    comprobar(esperarTexto("CALIBRAR TECLADO", 3000),
              "desde el menu se puede repetir la calibracion");
  } else if (caso == "wifi") {
    // Conexion completa: engancha a la WiFi MEDIBOT, localiza la Raspberry
    // por mDNS y enseña los datos que sirve /api/esp32.
    lanzarChequeoNo();
    abrirDelMenu(M_MEDIBOT);
    comprobar(esperarTexto("MEDIBOT", 4000), "el menu abre la pantalla de red");
    comprobar(WiFi.ssidPedida == "MEDIBOT" && WiFi.passPedida == "MEDIBOTCDB",
              "se conecta a la red MEDIBOT con su contrasena");
    comprobar(esperarTexto("192.168.1.77:5000", 20000),
              "localiza la Raspberry y enseña su direccion");
    esperar(2500);
    volcar("datos");
    comprobar(pantallaContiene("Sistema: ON   Caras: 3"), "muestra los datos de la API");
    comprobar(pantallaContiene("FPS: 28 / 27   Rojos: 2"), "y los contadores de vision");
    printf("   [medibot] empieza a grabar y detecta 7 caras\n");
    g_apiGrabando = true; g_apiDetecciones = 7;
    esperar(3000);
    volcar("datos actualizados");
    comprobar(pantallaContiene("Caras: 7") && pantallaContiene("Grabando: SI"),
              "los datos se refrescan solos");
    DOWN(); esperar(400);
    volcar("segunda pagina");
    comprobar(pantallaContiene("Red: MEDIBOT"), "la segunda pagina dice a que red esta");
    comprobar(pantallaContiene("Este ESP32: 192.168.1.45"), "y que IP le ha tocado");
    BACK();
    comprobar(esperarTexto("Auto-Chequeo", 4000), "se sale al menu");
  } else if (caso == "wifibarrido") {
    // Sin mDNS (lo normal si no se toca la Raspberry): hay que barrer la
    // subred hasta dar con ella, comprobando la identidad de cada IP.
    g_mdnsResponde = false;
    lanzarChequeoNo();
    abrirDelMenu(M_MEDIBOT);
    comprobar(esperarTexto("Explorando la red", 30000), "al fallar mDNS barre la subred");
    volcar("barriendo");
    comprobar(esperarTexto("192.168.1.77:5000", 120000), "y acaba encontrando a MEDIBOT");
    volcar("encontrado");
    comprobar(g_ipsProbadas.load() > 10, "ha probado las IPs una a una");
    comprobar(esperarTexto("Sistema:", 5000), "y ya lee la API");
  } else if (caso == "sinwifi") {
    g_wifiHayRed = false;
    lanzarChequeoNo();
    abrirDelMenu(M_MEDIBOT);
    comprobar(esperarTexto("Sin WiFi", 25000), "avisa de que no hay red");
    volcar("sin wifi");
    comprobar(pantallaContiene("Se reintenta solo"), "y dice que lo seguira intentando");
    printf("   [red] se enciende el router\n");
    g_wifiHayRed = true;
    comprobar(esperarTexto("192.168.1.77:5000", 60000),
              "cuando aparece la red se conecta solo, sin reiniciar");
  } else if (caso == "apicaida") {
    lanzarChequeoNo();
    abrirDelMenu(M_MEDIBOT);
    comprobar(esperarTexto("192.168.1.77:5000", 20000), "primero localiza a MEDIBOT");
    esperar(2000);
    printf("   [medibot] se apaga Vision_MEDIBOT.py\n");
    g_medibotVivo = false;
    comprobar(esperarSinTexto("Sistema:", 30000),
              "se entera de que la API ha dejado de responder");
    volcar("api caida");
    printf("   [medibot] se vuelve a encender\n");
    g_medibotVivo = true;
    comprobar(esperarTexto("Sistema:", 120000), "y la recupera sola, sin tocar nada");
  } else if (caso == "redymedida") {
    // La red no puede estorbar a la medida: mientras se mide, el nucleo 0 es
    // del sensor, y al terminar la conexion sigue viva.
    lanzarChequeo();
    esperarTexto("Coloque su dedo", 4000);
    esperar(2500);
    sensorSim.dedo = true;
    comprobar(esperarTexto("TUS RESULTADOS", 45000), "la medida termina con la red activa");
    comprobar(abs(numeroAntes(" bpm") - bpmReal) <= 2, "y el pulso sale bien");
    comprobar(sensorSim.perdidas.load() == 0, "sin perder muestras del sensor");
    OK();                                  // resultados -> menu
    abrirDelMenu(M_MEDIBOT);
    comprobar(esperarTexto("192.168.1.77:5000", 25000),
              "y al salir la conexion con MEDIBOT sigue");
  } else if (caso == "diagnostico") {
    lanzarChequeoNo();
    abrirDelMenu(M_DIAG);
    comprobar(esperarTexto("DIAGNOSTICO", 4000), "el menu abre la pantalla de diagnostico");
    esperar(1500);
    volcar("diagnostico sin dedo");
    comprobar(pantallaContiene("Sensor: OK"), "dice que el sensor responde");
    comprobar(pantallaContiene("sin dedo"), "con el sensor libre indica que no hay dedo");
    sensorSim.dedo = true;
    esperar(3000);
    volcar("diagnostico con dedo");
    comprobar(pantallaContiene("DEDO"), "al poner el dedo lo refleja en vivo");
    BACK();
    comprobar(esperarTexto("Auto-Chequeo", 3000), "se sale al menu");
  } else if (caso == "sensorlento") {
    // El sensor no responde al arrancar y aparece despues (mal contacto que se
    // asienta, modulo que tarda en dar tension...): tiene que recuperarse solo.
    comprobar(esperarTexto("NO DETECTADO", 5000), "avisa de que no hay sensor al arrancar");
    printf("   [hardware] se conecta el sensor 4 s despues\n");
    esperar(4000);
    sensorSim.presente = true;
    esperar(5000);
    lanzarChequeoNo();
    OK();
    comprobar(esperarTexto("Coloque su dedo", 6000),
              "sin reiniciar, el equipo ya deja medir");
    esperar(2500);
    sensorSim.dedo = true;
    comprobar(esperarTexto("TUS RESULTADOS", 45000), "y la medida se completa");
    comprobar(abs(numeroAntes(" bpm") - bpmReal) <= 2, "con el pulso correcto");
  } else if (caso == "sensorcuelga") {
    lanzarChequeo();
    esperarTexto("Coloque su dedo", 4000);
    esperar(2500);
    sensorSim.dedo = true;
    esperarTexto("BPM", 12000);
    esperar(2000);
    printf("   [hardware] el sensor deja de entregar muestras\n");
    sensorSim.colgado = true;
    comprobar(esperarTexto("TUS RESULTADOS", 60000),
              "el firmware reinicia el sensor y termina la medida");
    volcar("resultado");
    comprobar(!sensorSim.colgado.load(), "el sensor ha quedado desatascado");
  } else if (caso == "sinmemoria") {
    comprobar(atenderAsistente(), "sin calibracion el asistente se abre igual");
    comprobar(pantallaContiene("NO se pudo guardar") || pantallaContiene("Sin guardar"),
              "avisa de que la calibracion NO se ha guardado");
    comprobar(pantallaContiene("Se repetira al encender"), "explica que se repetira");
    comprobar(esperarTexto("Auto-Chequeo", 10000), "aun asi deja usar el menu");
  } else if (caso == "botonpulsado") {
    // Al asistente se entra MANTENIENDO un boton al encender. Si el reposo se
    // midiera con el boton pulsado, ninguna pulsacion pareceria distinta del
    // reposo y no se capturaria (ni guardaria) nada.
    comprobar(esperarTexto("CALIBRAR TECLADO", 4000),
              "mantener un boton al encender abre el asistente");
    // Se mantiene mas de WIZ_REPOSO_MS: es lo que hace cualquiera al encender
    // con el boton pulsado. Si el reposo se midiera aqui, saldria 2500 mV.
    esperar(2500);
    printf("   [usuario] suelta el boton\n");
    g_adcMv = 3200;
    comprobar(atenderAsistente(), "el asistente sigue su curso");
    comprobar(pantallaContiene("4 de 4 botones OK"),
              "mide el reposo DESPUES de soltar y captura los 4 botones");
    comprobar(pantallaContiene("Guardado en memoria"), "y los guarda");
    comprobar(esperarTexto("Auto-Chequeo", 8000), "el menu queda operativo");
    // Prueba de fuego: ARRIBA desde la primera entrada da la vuelta a la
    // ultima. Si el reposo se hubiera guardado como si fuera un boton, ARRIBA
    // no existiria (se confundio con el reposo) y ademas el teclado creeria
    // que hay una tecla pulsada todo el rato.
    UP();
    OK();
    comprobar(esperarTexto("INFO MEDIBOT", 4000),
              "ARRIBA y OK navegan: la calibracion guardada es la buena");
    BACK();
    // Vuelve al menu con la seleccion en la ultima entrada, asi que la ventana
    // visible empieza en "Historial": "Auto-Chequeo" queda fuera de pantalla.
    comprobar(esperarTexto("Calibrar teclado", 4000), "y se vuelve al menu");
  } else {                                   // normal
    lanzarChequeo();
    comprobar(esperarTexto("Coloque su dedo", 4000), "pide el dedo");
    esperar(2500);
    sensorSim.dedo = true;
    comprobar(esperarTexto("BPM", 12000), "muestra el pulso en vivo");
    comprobar(esperarTexto("TUS RESULTADOS", 45000),
              "al terminar la medida pasa directo a los resultados");
    volcar("resultados");
    comprobar(abs(numeroAntes(" bpm") - bpmReal) <= 2, "el pulso medido coincide con el real");
    comprobar(numeroTras("SpO2 ") > 0, "muestra la SpO2");
    UP(); esperar(400);
    volcar("posibles causas");
    comprobar(pantallaContiene("Pulso"), "la segunda pagina explica el resultado");
    BACK();
    comprobar(esperarTexto("Auto-Chequeo", 3000), "BACK vuelve al menu");
    abrirDelMenu(M_HISTORIAL);
    comprobar(esperarTexto("HISTORIAL", 3000), "el historial se abre");
    comprobar(pantallaContiene("Latidos"), "el historial guardo la medida");
    volcar("historial");
  }

  printf("   muestras: generadas=%ld entregadas=%ld perdidas=%ld\n",
         sensorSim.generadas.load(), sensorSim.entregadas.load(), sensorSim.perdidas.load());
  corriendo = false;
  esperar(200);
  printf("== %s: %s ==\n", caso.c_str(), fallos == 0 ? "OK" : "CON FALLOS");
  return fallos == 0 ? 0 : 1;
}
