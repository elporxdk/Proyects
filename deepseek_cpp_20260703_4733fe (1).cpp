/*
 * ============================================================
 *  DISPENSADOR MEDIBOT  -  Control por puerto Serial + EEPROM
 * ============================================================
 *  Recibe ordenes de texto (una por linea, terminadas en '\n')
 *  desde la Raspberry Pi (Bullseye) o el PC y controla:
 *
 *   - Un SERVOMOTOR en el pin 2 (servo de 90 grados).
 *        Reposo = 37 grados.  Dispensa moviendose y regresando.
 *
 *   - Un MOTOR PASO A PASO 28BYJ-48 + ULN2003 en los pines 8,9,10,11.
 *        Gira una RULETA de 8 compartimientos (45 grados cada uno).
 *        El compartimiento pedido queda en la posicion de dispensado
 *        (abajo del motor) y luego el servo suelta la pastilla.
 *
 *  ------------------- ORDENES (Serial, 9600 baud) -------------------
 *   GOTO,<n>       Gira la ruleta hasta el compartimiento n (1..8)
 *   DISPENSE,<n>   Gira al compartimiento n y ademas dispensa
 *   DISPENSE       Dispensa en el compartimiento actual
 *   HOME           Vuelve al compartimiento 1
 *   SERVO,<ang>    Mueve el servo a <ang> grados (0..90)
 *   GETPOS         Responde con la posicion actual (POS,<n>)
 *
 *  Respuestas que envia el Arduino (para el PC/Pi):
 *   LISTO          al arrancar
 *   POS,<n>        compartimiento actual tras un giro o al consultar
 *   DISPENSADO,<n> dispensado terminado
 *   ERR,<texto>    orden no reconocida
 * ============================================================
 */

#include <Stepper.h>
#include <Servo.h>
#include <EEPROM.h>

// EEPROM address for storing current compartment
#define EEPROM_COMP_ADDR 0

// ---------------- Servo dispensador ----------------
const int  SERVO_PIN      = 2;   // pin del servo
const int  SERVO_REPOSO   = 37;   // posicion de reposo (grados)
const int  SERVO_DISPENSA = 90;   // posicion para soltar la pastilla
Servo servoDispensador;

// ------------- Motor paso a paso (ruleta) -------------
const int PIN_IN1 = 8;
const int PIN_IN2 = 9;
const int PIN_IN3 = 10;
const int PIN_IN4 = 11;

const int  PASOS_POR_VUELTA = 2048;                    // 28BYJ-48 (ajusta si es necesario)
const int  N_COMPARTIMIENTOS = 8;
const int  PASOS_POR_COMP   = PASOS_POR_VUELTA / N_COMPARTIMIENTOS;  // 256 pasos = 45 grados

Stepper ruleta(PASOS_POR_VUELTA, PIN_IN1, PIN_IN3, PIN_IN2, PIN_IN4);

int compActual = 1;   // compartimiento que esta ahora en la posicion de dispensado (1..8)

// ---------------- utilidades ----------------
void liberarBobinas() {
  digitalWrite(PIN_IN1, LOW);
  digitalWrite(PIN_IN2, LOW);
  digitalWrite(PIN_IN3, LOW);
  digitalWrite(PIN_IN4, LOW);
}

void irACompartimiento(int destino) {
  destino = constrain(destino, 1, N_COMPARTIMIENTOS);
  int diff = destino - compActual;
  // camino mas corto
  if (diff >  N_COMPARTIMIENTOS / 2) diff -= N_COMPARTIMIENTOS;
  if (diff < -N_COMPARTIMIENTOS / 2) diff += N_COMPARTIMIENTOS;

  if (diff != 0) {
    ruleta.step(diff * PASOS_POR_COMP);
    compActual = destino;
    liberarBobinas();
    // Guardar en EEPROM
    EEPROM.write(EEPROM_COMP_ADDR, compActual);
  }
  Serial.print("POS,");
  Serial.println(compActual);
}

void dispensar() {
  // 1. Girar 180° (media vuelta = 4 compartimentos)
  int giro = 4; // 4 compartimentos = 180°
  // Gira siempre en una dirección fija (por ejemplo, horario)
  ruleta.step(giro * PASOS_POR_COMP);
  // Actualizar posición (sumar 4 módulo 8)
  compActual = (compActual + giro - 1) % N_COMPARTIMIENTOS + 1;
  liberarBobinas();
  // Guardar nueva posición en EEPROM
  EEPROM.write(EEPROM_COMP_ADDR, compActual);

  // 2. Abrir servo por 2.5 segundos
  servoDispensador.write(SERVO_DISPENSA);
  delay(2500);
  
  // 3. Cerrar servo (volver a reposo)
  servoDispensador.write(SERVO_REPOSO);
  delay(500);
  
  Serial.print("DISPENSADO,");
  Serial.println(compActual);
}

void procesarComando(String linea) {
  linea.trim();
  if (linea.length() == 0) return;

  String cmd = linea;
  String arg = "";
  int coma = linea.indexOf(',');
  if (coma >= 0) {
    cmd = linea.substring(0, coma);
    arg = linea.substring(coma + 1);
    arg.trim();
  }
  cmd.toUpperCase();

  if (cmd == "GOTO") {
    irACompartimiento(arg.toInt());
  } else if (cmd == "DISPENSE" || cmd == "DISPENSAR") {
    if (arg.length() > 0) irACompartimiento(arg.toInt());
    dispensar();
  } else if (cmd == "HOME") {
    irACompartimiento(1);
  } else if (cmd == "SERVO") {
    servoDispensador.write(constrain(arg.toInt(), 0, 90));
    Serial.print("SERVO,");
    Serial.println(arg.toInt());
  } else if (cmd == "GETPOS") {
    Serial.print("POS,");
    Serial.println(compActual);
  } else {
    Serial.print("ERR,");
    Serial.println(linea);
  }
}

// ---------------- setup / loop ----------------
void setup() {
  Serial.begin(9600);

  pinMode(PIN_IN1, OUTPUT);
  pinMode(PIN_IN2, OUTPUT);
  pinMode(PIN_IN3, OUTPUT);
  pinMode(PIN_IN4, OUTPUT);
  liberarBobinas();

  ruleta.setSpeed(10);   // velocidad en rpm

  servoDispensador.attach(SERVO_PIN);
  servoDispensador.write(SERVO_REPOSO);

  // Leer ultima posicion guardada en EEPROM
  byte saved = EEPROM.read(EEPROM_COMP_ADDR);
  if (saved >= 1 && saved <= N_COMPARTIMIENTOS) {
    compActual = saved;
  } else {
    compActual = 1;
    EEPROM.write(EEPROM_COMP_ADDR, compActual);
  }
  // Enviar posicion actual al host
  Serial.print("POS,");
  Serial.println(compActual);
  Serial.println("LISTO");
}

void loop() {
  if (Serial.available() > 0) {
    String linea = Serial.readStringUntil('\n');
    procesarComando(linea);
  }
}