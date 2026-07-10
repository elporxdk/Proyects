/*
 * ============================================================
 *  PILLBOX - Dispensador (giro en una sola direccion, SELECT + DISPENSE)
 * ============================================================
 *  Firmware fiable, SOLO dispensador (sin PS2 ni Motor Shield -> nunca se
 *  cuelga en el arranque y siempre responde por Serial).
 *
 *  GEOMETRIA (segun tu especificacion):
 *   - Ruleta de 8 compartimientos, 45 grados cada uno.
 *   - ZONA DE CARGA / SELECCION: arriba (180). En HOME, el compartimiento 1
 *     esta arriba; el 2 a 135, el 3 a 90, etc., en el sentido de avance.
 *   - ZONA DE DISPENSADO: abajo (a 4 compartimientos = 180 de la de arriba).
 *
 *  DIRECCION UNICA: la ruleta SOLO gira hacia adelante (pasos positivos). El
 *  retroceso esta prohibido; para "volver", completa el circulo hacia adelante.
 *
 *  ------------------- LOGICA (tal como la pediste) -------------------
 *   SELECT,N (= GOTO,N): coloca el compartimiento N ARRIBA (posicion de
 *       espera).  value=N; if(value>=1) value--; avanzar 'value' compartimientos.
 *       Queda arriba esperando a ser dispensado.
 *
 *   DISPENSE,N: lleva el compartimiento N a la zona de dispensado y suelta.
 *       Parte de HOME.  Si N<=4 -> rot = N+3 ; si N>=5 -> rot = N-5.
 *       Avanza 'rot' compartimientos (rot*45), acciona el servo, y luego
 *       completa el giro hacia adelante para volver a HOME (si rot=0 no se
 *       mueve; si rot=4 sube media vuelta hasta su posicion final; etc.).
 *
 *  ------------------- ORDENES (Serial, 9600 baud) -------------------
 *   SELECT,<n> / GOTO,<n>   Coloca el compartimiento n (1..8) ARRIBA
 *   DISPENSE,<n>            Lleva n a dispensado, suelta y vuelve a HOME
 *   DISPENSE                Dispensa el que este arriba
 *   HOME                    Vuelve a HOME (compartimiento 1 arriba)
 *   SERVO,<ang>             Mueve el servo a <ang> grados (0..90)
 *   GETPOS                  Responde POS,<n> (compartimiento arriba)
 *
 *  Respuestas:  POS,<n>  DISPENSADO,<n>  SERVO,<ang>  ERR,<txt>  LISTO
 *
 *  Monitor Serial: pon el ajuste de linea en "Nueva linea" (NL) — cada orden
 *  se procesa al recibir '\n'.
 * ============================================================
 */

#include <Stepper.h>
#include <Servo.h>
#include <EEPROM.h>

#define EEPROM_COMP_ADDR 0

// ---------------- Servo dispensador ----------------
const int  SERVO_PIN      = 2;
const int  SERVO_REPOSO   = 37;
const int  SERVO_DISPENSA = 90;
Servo servoDispensador;

// ------------- Motor paso a paso (ruleta) -------------
const int PIN_IN1 = 8;
const int PIN_IN2 = 9;
const int PIN_IN3 = 10;
const int PIN_IN4 = 11;

const int  PASOS_POR_VUELTA  = 2048;                                 // 28BYJ-48
const int  N_COMPARTIMIENTOS = 8;
// Pasos para AVANZAR un compartimiento (45 grados). 2048/8 = 256 exactos.
// (En tu explicacion mencionaste 252; para que 8 compartimientos iguales
//  cierren la vuelta sin desalinearse conviene 256. Si tu mecanica real
//  necesita 252, cambia SOLO este numero.)
const int  PASOS_POR_COMP    = PASOS_POR_VUELTA / N_COMPARTIMIENTOS; // 256

Stepper ruleta(PASOS_POR_VUELTA, PIN_IN1, PIN_IN3, PIN_IN2, PIN_IN4);

int compArriba = 1;   // compartimiento que esta ARRIBA (1..8). HOME = 1.

// ---------------- utilidades ----------------
void liberarBobinas() {
  digitalWrite(PIN_IN1, LOW);
  digitalWrite(PIN_IN2, LOW);
  digitalWrite(PIN_IN3, LOW);
  digitalWrite(PIN_IN4, LOW);
}

void guardarPos() {
  EEPROM.write(EEPROM_COMP_ADDR, compArriba);
}

// Avanza 'k' compartimientos HACIA ADELANTE (solo adelante; k se normaliza 0..7).
void avanzarComps(int k) {
  k = ((k % N_COMPARTIMIENTOS) + N_COMPARTIMIENTOS) % N_COMPARTIMIENTOS;
  if (k > 0) {
    ruleta.step((long)k * PASOS_POR_COMP);
    liberarBobinas();
  }
}

// Vuelve a HOME (compartimiento 1 arriba) completando el giro hacia adelante.
void irAHome() {
  avanzarComps((N_COMPARTIMIENTOS - (compArriba - 1)) % N_COMPARTIMIENTOS);
  compArriba = 1;
  guardarPos();
}

// SELECT,N / GOTO,N: coloca el compartimiento N ARRIBA y lo deja en espera.
// Avanza solo lo necesario hacia adelante desde donde este (mismo resultado
// que value-1 desde HOME, pero sin retroceder).
void seleccionar(int n) {
  n = constrain(n, 1, N_COMPARTIMIENTOS);
  avanzarComps((n - compArriba + N_COMPARTIMIENTOS) % N_COMPARTIMIENTOS);
  compArriba = n;
  guardarPos();
  Serial.print("POS,");
  Serial.println(compArriba);
}

// DISPENSE,N: parte de HOME, lleva el compartimiento N a la zona de dispensado
// (abajo) con la formula pedida, acciona el servo y vuelve a HOME.
void dispensar(int n) {
  n = constrain(n, 1, N_COMPARTIMIENTOS);
  irAHome();                                   // parte siempre desde HOME

  int rot = (n <= 4) ? (n + 3) : (n - 5);      // 1..8 -> 4,5,6,7,0,1,2,3
  avanzarComps(rot);                           // comp N a la zona de dispensado

  // Accionar el servo (soltar la pastilla)
  servoDispensador.write(SERVO_DISPENSA);
  delay(2500);
  servoDispensador.write(SERVO_REPOSO);
  delay(500);

  // Volver a HOME hacia adelante (rot=0 -> no se mueve; rot=4 -> media vuelta)
  avanzarComps((N_COMPARTIMIENTOS - rot) % N_COMPARTIMIENTOS);
  compArriba = 1;
  guardarPos();

  Serial.print("DISPENSADO,");
  Serial.println(n);
  Serial.print("POS,");
  Serial.println(compArriba);
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

  if (cmd == "SELECT" || cmd == "GOTO") {
    seleccionar(arg.toInt());
  } else if (cmd == "DISPENSE" || cmd == "DISPENSAR") {
    int n = (arg.length() > 0) ? arg.toInt() : compArriba;
    dispensar(n);
  } else if (cmd == "HOME") {
    irAHome();
    Serial.print("POS,");
    Serial.println(compArriba);
  } else if (cmd == "SERVO") {
    servoDispensador.write(constrain(arg.toInt(), 0, 90));
    Serial.print("SERVO,");
    Serial.println(arg.toInt());
  } else if (cmd == "GETPOS") {
    Serial.print("POS,");
    Serial.println(compArriba);
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

  ruleta.setSpeed(10);   // rpm

  servoDispensador.attach(SERVO_PIN);
  servoDispensador.write(SERVO_REPOSO);

  byte saved = EEPROM.read(EEPROM_COMP_ADDR);
  if (saved >= 1 && saved <= N_COMPARTIMIENTOS) {
    compArriba = saved;
  } else {
    compArriba = 1;
    guardarPos();
  }

  Serial.print("POS,");
  Serial.println(compArriba);
  Serial.println("LISTO");
}

void loop() {
  if (Serial.available() > 0) {
    String linea = Serial.readStringUntil('\n');
    procesarComando(linea);
  }
}
