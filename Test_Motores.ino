/*
 * ============================================================
 *  TEST DE MOTORES DC  -  Motor Shield (QGPMaker / Adafruit v2)
 * ============================================================
 *  Sketch minimo y AISLADO: solo el Motor Shield y los 4 motores DC.
 *  No hay dispensador, ni PS2, ni servos, ni Serial de comandos: asi nada
 *  mas puede interferir. Subilo y observa si los motores giran.
 *
 *  Que hace:
 *   1. Escanea el bus I2C al arrancar y dice si encuentra el shield (0x60).
 *   2. En bucle, prueba los 4 motores uno por uno:
 *        - 1.5 s hacia ADELANTE
 *        - 1.5 s hacia ATRAS
 *        - freno
 *      imprimiendo por Serial (9600) que motor esta probando.
 *
 *  Interpretacion:
 *   - Si los motores GIRAN -> el shield, la libreria, el cableado y la
 *     alimentacion estan bien; el problema estaba en el firmware combinado.
 *   - Si NINGUNO gira y el I2CSCAN no ve 0x60 -> el shield no se comunica
 *     (revisar encastre / SDA-SCL / que la libreria sea la de TU shield).
 *   - Si ve 0x60 pero no giran -> revisar motores en M1..M4 y alimentacion.
 *
 *  Requiere la MISMA libreria del Motor Shield que usa el proyecto.
 * ============================================================
 */

#include <Wire.h>
#include "QGPMaker_MotorShield.h"

QGPMaker_MotorShield AFMS = QGPMaker_MotorShield();

QGPMaker_DCMotor *M1 = AFMS.getMotor(1);
QGPMaker_DCMotor *M2 = AFMS.getMotor(2);
QGPMaker_DCMotor *M3 = AFMS.getMotor(3);
QGPMaker_DCMotor *M4 = AFMS.getMotor(4);
QGPMaker_DCMotor *MOTORES[4] = { M1, M2, M3, M4 };

const int VELOCIDAD = 200;   // 0..255

void escanearI2C() {
  Serial.println("Escaneando bus I2C...");
  int n = 0;
  for (byte addr = 1; addr < 127; addr++) {
    Wire.beginTransmission(addr);
    if (Wire.endTransmission() == 0) {
      Serial.print("  encontrado 0x");
      if (addr < 16) Serial.print("0");
      Serial.println(addr, HEX);
      n++;
    }
  }
  if (n == 0) Serial.println("  NINGUN dispositivo I2C. El shield NO se comunica.");
  else        Serial.println("  (el Motor Shield suele ser 0x60)");
}

void setup() {
  Serial.begin(9600);
  Serial.println("=== TEST DE MOTORES DC ===");

  Wire.begin();
  escanearI2C();

  AFMS.begin(1600);   // 1600 Hz: buena frecuencia PWM para motores DC
  Serial.println("Motor Shield iniciado. Probando motores en bucle...");
}

void probarMotor(int i) {
  Serial.print("Motor "); Serial.print(i + 1); Serial.println(": ADELANTE");
  MOTORES[i]->setSpeed(VELOCIDAD);
  MOTORES[i]->run(FORWARD);
  delay(1500);

  Serial.print("Motor "); Serial.print(i + 1); Serial.println(": ATRAS");
  MOTORES[i]->run(BACKWARD);
  delay(1500);

  MOTORES[i]->run(RELEASE);   // freno / suelta
  delay(500);
}

void loop() {
  for (int i = 0; i < 4; i++) {
    probarMotor(i);
  }
  Serial.println("--- vuelta completa, repitiendo ---");
  delay(1000);
}
