#include <Arduino.h>
#include <Wire.h>
#include <U8g2lib.h>
#include <MAX30105.h>

// ===================== reloj virtual =====================
double g_speedup = 8.0;     // 8 s simulados por cada segundo real
static std::chrono::steady_clock::time_point t0 = std::chrono::steady_clock::now();

static uint64_t realUs() {
  return (uint64_t)std::chrono::duration_cast<std::chrono::microseconds>(
             std::chrono::steady_clock::now() - t0).count();
}
uint32_t micros() { return (uint32_t)(realUs() * g_speedup); }
uint32_t millis() { return (uint32_t)((realUs() * g_speedup) / 1000.0); }
void delay(uint32_t ms) {
  std::this_thread::sleep_for(std::chrono::microseconds((uint64_t)(ms * 1000.0 / g_speedup)));
}
void delayMicroseconds(uint32_t us) {
  std::this_thread::sleep_for(std::chrono::microseconds((uint64_t)(us / g_speedup) + 1));
}
long random(long m) { return m > 0 ? (long)(::rand() % m) : 0; }
long random(long a, long b) { return b > a ? a + (long)(::rand() % (b - a)) : a; }

// ===================== ADC / pines =====================
std::atomic<int> g_adcMv{3200};          // reposo del teclado
int  analogRead(int) { long v = (long)g_adcMv.load() * 4095 / 3300; return (int)(v > 4095 ? 4095 : v); }
uint32_t analogReadMilliVolts(int) { int v = g_adcMv.load(); return (uint32_t)(v > 3200 ? 3200 : v); }
void analogReadResolution(int) {}
void analogSetPinAttenuation(int, int) {}
void pinMode(int, int) {}

// ===================== Serie =====================
SerialSim Serial;
int SerialSim::available() { return 0; }
int SerialSim::read() { return -1; }

// ===================== FreeRTOS =====================
void portENTER_CRITICAL(portMUX_TYPE *m) { m->lock(); }
void portEXIT_CRITICAL(portMUX_TYPE *m) { m->unlock(); }
void vTaskDelay(uint32_t ticks) { delay(ticks ? ticks : 1); }
int xTaskCreatePinnedToCore(void (*fn)(void *), const char *, uint32_t, void *p, int,
                            TaskHandle_t *h, int) {
  std::thread(fn, p).detach();
  if (h) *h = (TaskHandle_t)1;
  return 1;
}

// ===================== I2C =====================
TwoWire Wire;

// ===================== U8g2 =====================
u8g2_font_t u8g2_font_4x6_tr = "4x6", u8g2_font_5x7_tr = "5x7",
            u8g2_font_6x10_tr = "6x10", u8g2_font_helvB08_tr = "helvB08";
static std::mutex             frameMux;
static std::vector<std::string> frameActual;
void U8G2_ST7920_128X64_F_HW_SPI::sendBuffer() {
  std::lock_guard<std::mutex> g(frameMux);
  frameActual = pend;
}
std::vector<std::string> pantallaUltimoFrame() {
  std::lock_guard<std::mutex> g(frameMux);
  return frameActual;
}
bool pantallaContiene(const char *frag) {
  std::lock_guard<std::mutex> g(frameMux);
  for (const auto &s : frameActual)
    if (s.find(frag) != std::string::npos) return true;
  return false;
}

// ===================== MAX30102 simulado =====================
SensorSim sensorSim;

bool MAX30105::begin(TwoWire &, uint32_t) {
  iniciado = sensorSim.presente.load() && sensorSim.partId.load() == 0x15;
  return iniciado;
}

void MAX30105::setup(byte powerLevel, byte sampleAverage, byte, int sampleRate, int, int) {
  ampRed = ampIr = powerLevel;
  const int ef = sampleRate / (sampleAverage ? sampleAverage : 1);
  periodoMs = ef > 0 ? 1000 / ef : 10;
  proximaMuestraMs = millis();
}

// Forma de onda PPG: pico sistolico + onda dicrota.
static double ondaPPG(double f) {
  f = f - floor(f);
  const double a = exp(-pow((f - 0.16) / 0.085, 2.0));
  const double b = 0.42 * exp(-pow((f - 0.42) / 0.13, 2.0));
  return a + b;                       // ~0..1.05
}

void MAX30105::generar(uint32_t ahora) {
  if (!iniciado) return;
  while ((int32_t)(ahora - proximaMuestraMs) >= 0) {
    proximaMuestraMs += periodoMs;
    const bool luz = (ampIr > 0);
    uint32_t ir, red;
    if (!luz) {                                    // LED apagados -> oscuridad
      ir = 300 + (::rand() % 40);
      red = 300 + (::rand() % 40);
    } else if (!sensorSim.dedo.load()) {           // sin dedo -> poca luz reflejada
      ir = 2500 + (::rand() % 300);
      red = 2000 + (::rand() % 300);
    } else {
      const double bpm = (double)sensorSim.bpm.load();
      fase += (bpm / 60.0) * (periodoMs / 1000.0);
      const double w = ondaPPG(fase);
      const double dcIr = sensorSim.dcIr.load(), dcRed = sensorSim.dcRed.load();
      const double piIr = sensorSim.perfusionMilli.load() / 1000.0;
      // R = (ACred/DCred)/(ACir/DCir) -> despeja SpO2 con la curva de Maxim
      const double s = sensorSim.spo2.load();
      double disc = 30.354 * 30.354 - 4 * 45.060 * (94.845 - s);
      if (disc < 0) disc = 0;
      double R = (30.354 - sqrt(disc))
                 / (2 * 45.060);
      if (!(R > 0.03 && R < 1.8)) R = 0.5;
      const double acIr = dcIr * piIr;
      const double acRed = dcRed * piIr * R;
      const double n = (double)(::rand() % (sensorSim.ruido.load() + 1)) - sensorSim.ruido.load() / 2.0;
      double vIr = dcIr - acIr * w + n;  if (vIr < 0) vIr = 0;
      double vRed = dcRed - acRed * w + n; if (vRed < 0) vRed = 0;
      ir  = (uint32_t)vIr;
      red = (uint32_t)vRed;
    }
    if (ir > 262143) ir = 262143;                  // el FIFO del chip son 18 bits
    if (red > 262143) red = 262143;
    if (fifoN < 32) { fifoIr[fifoN] = ir; fifoRed[fifoN] = red; fifoN++; }
    sensorSim.generadas++;
  }
}

uint16_t MAX30105::check() {
  if (!iniciado) return 0;
  generar(millis());
  const int n = fifoN;
  for (int i = 0; i < n; i++) {
    sense.head = (byte)((sense.head + 1) % STORAGE_SIZE);
    if (sense.head == sense.tail) sensorSim.perdidas++;   // se pisa una muestra
    sense.red[sense.head] = fifoRed[i];
    sense.IR[sense.head]  = fifoIr[i];
  }
  fifoN = 0;
  return (uint16_t)n;
}

uint8_t MAX30105::available() {
  int8_t n = (int8_t)(sense.head - sense.tail);
  if (n < 0) n += STORAGE_SIZE;
  return (uint8_t)n;
}
void     MAX30105::nextSample() { if (available()) { sense.tail = (byte)((sense.tail + 1) % STORAGE_SIZE); sensorSim.entregadas++; } }
uint32_t MAX30105::getFIFOIR()  { return sense.IR[sense.tail]; }
uint32_t MAX30105::getFIFORed() { return sense.red[sense.tail]; }
void     MAX30105::clearFIFO()  { fifoN = 0; sense.head = sense.tail = 0; proximaMuestraMs = millis(); }

// ===================== Preferences (NVS simulada) =====================
#include <Preferences.h>
#include <fstream>

static std::string rutaNVS() {
  const char *e = getenv("MEDIBOT_NVS");
  return e ? e : "nvs.dat";
}

void Preferences::cargar() {
  datos.clear();
  std::ifstream f(fichero, std::ios::binary);
  if (!f) return;
  while (f) {
    uint32_t kl = 0, vl = 0;
    if (!f.read((char *)&kl, 4)) break;
    if (!f.read((char *)&vl, 4)) break;
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
    uint32_t kl = (uint32_t)kv.first.size(), vl = (uint32_t)kv.second.size();
    f.write((char *)&kl, 4); f.write((char *)&vl, 4);
    f.write(kv.first.data(), kl);
    f.write((char *)kv.second.data(), vl);
  }
}
bool Preferences::begin(const char *ns, bool) { fichero = rutaNVS() + "." + ns; cargar(); return true; }
size_t Preferences::getBytesLength(const char *key) {
  auto it = datos.find(key);
  return it == datos.end() ? 0 : it->second.size();
}
size_t Preferences::getBytes(const char *key, void *buf, size_t len) {
  auto it = datos.find(key);
  if (it == datos.end()) return 0;
  const size_t n = it->second.size() < len ? it->second.size() : len;
  memcpy(buf, it->second.data(), n);
  return n;
}
size_t Preferences::putBytes(const char *key, const void *buf, size_t len) {
  datos[key] = std::vector<uint8_t>((const uint8_t *)buf, (const uint8_t *)buf + len);
  guardar();
  return len;
}
