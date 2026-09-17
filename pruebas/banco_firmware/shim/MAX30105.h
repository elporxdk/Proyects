// MAX30102 simulado: genera una PPG sintetica con BPM y SpO2 conocidos.
#pragma once
#include <Arduino.h>
#include <Wire.h>

#define I2C_SPEED_STANDARD 100000
#define I2C_SPEED_FAST     400000

// --- mandos del banco de pruebas ---
struct SensorSim {
  std::atomic<bool>  presente{true};     // el chip responde en el bus
  std::atomic<int>   partId{0x15};       // 0x15 MAX30102/5, 0x11 MAX30100
  std::atomic<bool>  dedo{false};        // hay dedo sobre el sensor
  std::atomic<int>   bpm{72};
  std::atomic<int>   spo2{98};           // SpO2 real que se quiere simular
  std::atomic<int>   perfusionMilli{20}; // perfusion IR en milesimas (20 = 2 %)
  std::atomic<int>   dcIr{95000};
  std::atomic<int>   dcRed{75000};
  std::atomic<int>   ruido{60};          // ruido pico a pico en cuentas
  std::atomic<long>  generadas{0};       // muestras producidas por el chip
  std::atomic<long>  entregadas{0};      // muestras que ha leido el firmware
  std::atomic<long>  perdidas{0};        // muestras perdidas en el buffer local
};
extern SensorSim sensorSim;

#define STORAGE_SIZE 4

class MAX30105 {
 public:
  bool begin(TwoWire &w, uint32_t speed = I2C_SPEED_STANDARD);
  void setup(byte powerLevel = 0x1F, byte sampleAverage = 4, byte ledMode = 3,
             int sampleRate = 400, int pulseWidth = 411, int adcRange = 4096);
  void setPulseAmplitudeRed(uint8_t v)   { ampRed = v; }
  void setPulseAmplitudeIR(uint8_t v)    { ampIr  = v; }
  void setPulseAmplitudeGreen(uint8_t v) { (void)v; }
  uint16_t check();
  uint8_t  available();
  void     nextSample();
  uint32_t getFIFOIR();
  uint32_t getFIFORed();
  void     clearFIFO();
  uint8_t  readPartID()    { return (uint8_t)sensorSim.partId.load(); }
  uint8_t  getRevisionID() { return 0x03; }

 private:
  void generar(uint32_t ahora);
  // FIFO del chip (32 muestras)
  uint32_t fifoIr[32], fifoRed[32];
  int      fifoN = 0;
  uint32_t proximaMuestraMs = 0;
  double   fase = 0.0;
  // buffer local de la libreria
  struct { uint32_t red[STORAGE_SIZE]; uint32_t IR[STORAGE_SIZE]; byte head, tail; } sense{};
  uint8_t  ampRed = 0, ampIr = 0;
  int      periodoMs = 10;
  bool     iniciado = false;
};
