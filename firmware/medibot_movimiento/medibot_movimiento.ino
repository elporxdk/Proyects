/*
 * ============================================================
 *  MEDIBOT — Firmware 
 *  ------------------------------------------------------------
 *    0,1        -> Serial (USB, comandos desde la Pi/PC)  ¡RESERVADOS!
 *                  (en el shield salen por el header WIFI/BT: NO son libres)
 *    2          -> Servo dispensador (libreria Servo)   [header Encoder3]
 *    3          -> libre 
 *    4,5        -> LIBRES (header Encoder4)
 *    6,7        -> Encoder del motor M2  (lado B)       
 *    8,9        -> Encoder del motor M1  (lado A)        
 *    10,11,12,13-> Mando PS2 (attention/command/data/clock)
 *                  Son sus pines de siempre: NO se tocan.
 *    A0..A3     -> Motor paso a paso ULN2003 (ruleta) 
 *    A4,A5      -> I2C (SDA/SCL) del Motor Shield 

 
 *   SELECT,<n> / GOTO,<n>  Coloca el compartimiento n (1..8) ARRIBA
 *   DISPENSE,<n>   Lleva n a dispensado, suelta y vuelve a HOME
 *   DISPENSE       Dispensa el compartimiento que este arriba
 *   HOME           Vuelve a HOME (compartimiento 1 arriba)
 *   SERVO,<ang>    Mueve el servo dispensador a <ang> grados (0..90)
 *   GETPOS         Responde POS,<n> = compartimiento actualmente arriba
 *   STEPTEST[,<k>] Diagnostico: gira la ruleta k compartimientos (def. 8 = 1
 *                  vuelta) para probar el paso a paso AISLADO del resto
 *
 *  ------------------- ORDENES ENCODERS -----------------------
 *  dispensador. El campo <m3> va siempre a 0.
 *   ENC            ENC,<m1>,<m2>,0,<m4>     posicion acumulada, con signo
 *   ENCRPM         ENCRPM,<r1>,<r2>,0,<r4>  velocidad de cada motor en RPM
 *   ENCRESET       Pone las cuentas a cero (calibrar / medir un recorrido)
 *
 *  4320 cuentas = 1 vuelta del eje de salida (12 PPR x 4 cuadratura x 90
 *  reductora), segun el fabricante. Constante CUENTAS_POR_VUELTA.
 *
 *  ------------------- ORDENES MOVIMIENTO / CAMARA (Vision) ----
 *   MOVE,<dir>     dir = FWD | BACK | LEFT | RIGHT | STOP
 *   FWD/BACK/...   la direccion SOLA tambien vale (para probar por el Monitor)
 *   GPIO,<pin>,<v> Protocolo de Vision: pin 17=adel,27=atras,22=izq,23=der; v=0/1
 *   GPIO,CLEANUP,0 Detiene el chasis y limpia el estado de movimiento
 *   PWM,<pin>,<d>  Servos de camara: pin 18=pan, 13=tilt; d = duty % (2.5..12.5)
 *
 *  ------------------- MANDO PS2 ------------------------------
 *   PAD ARRIBA/ABAJO   avanzar / retroceder
 *   PAD IZQ/DER        girar sobre su propio eje
 *   L1 / R1            desplazamiento lateral (sin cambiar de orientacion)
 *   L2 / R2 + PAD      giro amplio: empuja solo un lado del robot
 *   X                  vibracion del mando
 
 *
 *  Respuestas del Arduino:
 *   LISTO          al arrancar
 *   POS,<n>        compartimiento arriba tras un giro o al consultar
 *   DISPENSADO,<n> dispensado terminado (n = compartimiento que bajo y solto)
 *   OK,MOVE,<dir>  confirmacion de orden de movimiento
 *   ERR,<texto>    orden no reconocida
 * ============================================================
 */

#include <Wire.h>
#include "PS2X_lib.h"
#include "QGPMaker_MotorShield.h"
#include "QGPMaker_Encoder.h"     // encoders de los motores (libreria del shield)
#include <Servo.h>
#include <EEPROM.h>

// ════════════════════════════════════════════════════════════
//  MOVIMIENTO — Motor Shield, PS2 y servos del brazo
// ════════════════════════════════════════════════════════════

// ── Motor Shield ──────────────────────────────────────────────
QGPMaker_MotorShield AFMS = QGPMaker_MotorShield();
PS2X ps2x;

// ── Límites de servos ─────────────────────────────────────────
long ARM_MIN[] = {10,  10,  40, 10};
long ARM_MAX[] = {170, 140, 170, 102};

// ── Servos del brazo ──────────────────────────────────────────
QGPMaker_Servo *Servo1 = AFMS.getServo(0);
QGPMaker_Servo *Servo2 = AFMS.getServo(1);
QGPMaker_Servo *Servo3 = AFMS.getServo(2);
QGPMaker_Servo *Servo4 = AFMS.getServo(3);

// ── Motores DC ────────────────────────────────────────────────
QGPMaker_DCMotor *DCMotor_1 = AFMS.getMotor(1);
QGPMaker_DCMotor *DCMotor_2 = AFMS.getMotor(2);
QGPMaker_DCMotor *DCMotor_3 = AFMS.getMotor(3);
QGPMaker_DCMotor *DCMotor_4 = AFMS.getMotor(4);

//  NOTA: el movimiento del chasis llega SIEMPRE por COM (comandos MOVE/GPIO de
//  Vision). No hay pines de entrada fisicos desde la Raspberry Pi: no se cablea
//  nada hacia el Arduino para mover, asi que A0..A3 quedan libres.
//  VELOCIDAD DEL CHASIS — AHORA ES UNA VARIABLE, NO UNA CONSTANTE.
//  Era  #define VELOCIDAD 200, o sea fijada al compilar. Vision enviaba
//  VEL,<200..255> al mover el deslizador de la web y el firmware no tenia ese
//  comando: respondia ERR,VEL,231 y la velocidad no cambiaba nunca. El
//  deslizador estaba conectado a la nada.
const uint8_t VELOCIDAD_MIN = 200;   // por debajo, estos motores con reductora
const uint8_t VELOCIDAD_MAX = 255;   // apenas arrancan con carga
uint8_t velocidadActual = VELOCIDAD_MIN;

// ════════════════════════════════════════════════════════════
//  DISPENSADOR — Servo + motor paso a paso (ruleta) + EEPROM
// ════════════════════════════════════════════════════════════

// EEPROM address for storing current compartment
#define EEPROM_COMP_ADDR 0

// ---------------- Servo dispensador ----------------
const int  SERVO_PIN      = 2;    // pin del servo (libreria Servo estandar)
const int  SERVO_REPOSO   = 37;   // posicion de reposo (grados)
const int  SERVO_DISPENSA = 90;   // posicion para soltar la pastilla
Servo servoDispensador;

// ---------------- Servos de camara (pan/tilt) ----------------
//  OPCIONAL. Este robot NO lleva soporte pan/tilt, asi que vienen DESACTIVADOS
//  y sus pines (D3 y D5) quedan libres para los encoders de los motores.
//  Pon 1 aqui si algun dia montas el soporte: entonces D3/D5 pasan a los servos
//  y pierdes los encoders 3 y 4 (comparten esos pines en el shield).
#define USAR_SERVOS_CAMARA 0

#if USAR_SERVOS_CAMARA
//  Controlados por Vision via COM con  PWM,<pin>,<duty>  (pin 18 = pan, 13 = tilt).
const int PAN_PIN  = 3;
const int TILT_PIN = 5;
Servo servoPan;
Servo servoTilt;
#endif


#ifndef PIN_A0            // fuera del core de AVR (analisis estatico, tests)
#define PIN_A0 14
#define PIN_A1 15
#define PIN_A2 16
#define PIN_A3 17
#define PIN_A4 18
#define PIN_A5 19
#endif

#define RULETA_IN1 PIN_A0
#define RULETA_IN2 PIN_A1
#define RULETA_IN3 PIN_A2
#define RULETA_IN4 PIN_A3


const int PIN_IN1 = RULETA_IN1;
const int PIN_IN2 = RULETA_IN2;
const int PIN_IN3 = RULETA_IN3;
const int PIN_IN4 = RULETA_IN4;

const int  PASOS_POR_VUELTA  = 2048;                                 // 28BYJ-48 (ajusta si es necesario)
const int  N_COMPARTIMIENTOS = 8;
const int  PASOS_POR_COMP    = PASOS_POR_VUELTA / N_COMPARTIMIENTOS; // 256 pasos = 45 grados


#define PIN_SERVO_ESPERADO 2
#define RULETA_CHOCA(p) ((p) == 0 || (p) == 1 ||  /* Serial RX/TX */          \
                         (p) == PIN_SERVO_ESPERADO ||   /* servo disp. (D2) */\
                         (p) == 6 || (p) == 7 ||  /* header Encoder2 (M2) */  \
                         (p) == 8 || (p) == 9 ||  /* header Encoder1 (M1) */  \
                         (p) == 10 || (p) == 11 || (p) == 12 || (p) == 13 ||  \
                         (p) == PIN_A4 || (p) == PIN_A5)  /* I2C del shield */
#if RULETA_CHOCA(RULETA_IN1) || RULETA_CHOCA(RULETA_IN2) || \
    RULETA_CHOCA(RULETA_IN3) || RULETA_CHOCA(RULETA_IN4)
#error "Pin de la ruleta ocupado. Libres en este robot: A0,A1,A2,A3 (y D3,D4,D5). Prohibidos: 0/1 (Serial), 2 (servo), 6-9 (encoders), 10-13 (PS2), A4/A5 (I2C)."
#endif
#if (RULETA_IN1 == RULETA_IN2) || (RULETA_IN1 == RULETA_IN3) || \
    (RULETA_IN1 == RULETA_IN4) || (RULETA_IN2 == RULETA_IN3) || \
    (RULETA_IN2 == RULETA_IN4) || (RULETA_IN3 == RULETA_IN4)
#error "Dos bobinas de la ruleta declaradas en el mismo pin."
#endif


const uint8_t SECUENCIA_PASOS[4] = { 0b1100, 0b0110, 0b0011, 0b1001 };

const unsigned long US_POR_PASO = 2930;

int compActual = 1;   // compartimiento que esta ARRIBA (zona de carga/espera, 1..8)


bool ps2Presente = false;


#define VERSION_PROTOCOLO 2

// Buffer para lectura no bloqueante de comandos por Serial
String bufferSerial = "";


bool vAdelante  = false;
bool vAtras     = false;
bool vIzquierda = false;
bool vDerecha   = false;

uint8_t movComandado = 0;   // 0 = MOVC_STOP. Lo aplica el loop.

const bool INVERTIR_GIRO = false;

// Manda un sentido a un motor.  -1 = atras, +1 = adelante, 0 = suelto.
void ponerMotor(QGPMaker_DCMotor* m, int8_t sentido) {
  if (sentido == 0) {
    m->setSpeed(0);
    m->run(RELEASE);
  } else {
    m->setSpeed(velocidadActual);
    m->run(sentido > 0 ? FORWARD : BACKWARD);
  }
}

// Aplica un patron a los cuatro motores de golpe.
void patron(int8_t m1, int8_t m2, int8_t m3, int8_t m4) {
  ponerMotor(DCMotor_1, m1);
  ponerMotor(DCMotor_2, m2);
  ponerMotor(DCMotor_3, m3);
  ponerMotor(DCMotor_4, m4);
}

void forward()  { patron(-1, +1, -1, +1); }   // los 4 hacia adelante de verdad
void backward() { patron(+1, -1, +1, -1); }

// Giro sobre su propio eje: un lado adelante y el otro atras.
void turnLeft()  { if (INVERTIR_GIRO) patron(-1,-1,-1,-1); else patron(+1,+1,+1,+1); }
void turnRight() { if (INVERTIR_GIRO) patron(+1,+1,+1,+1); else patron(-1,-1,-1,-1); }

// Desplazamiento lateral, sin cambiar de orientacion (necesita ruedas mecanum).
void moveLeft()  { patron(-1, -1, +1, +1); }
void moveRight() { patron(+1, +1, -1, -1); }

// Giros amplios: solo empuja un lado, el otro queda suelto (L2/R2 del mando).
void arcoLadoA(int8_t s) { patron(-s, 0, -s, 0); }   // lado M1/M3 (van invertidos)
void arcoLadoB(int8_t s) { patron(0, s, 0, s); }     // lado M2/M4

void stopMoving();   // definida mas abajo (necesita el control de repeticion)

#define MOVC_STOP    0
#define MOVC_FWD     1
#define MOVC_BACK    2
#define MOVC_TURNL   3
#define MOVC_TURNR   4
#define MOVC_MOVEL   5
#define MOVC_MOVER   6
#define MOVC_ARC_AF  7   // arco: solo lado M1/M3, hacia adelante
#define MOVC_ARC_BF  8   // arco: solo lado M2/M4, hacia adelante
#define MOVC_ARC_AB  9   // arco: solo lado M1/M3, hacia atras
#define MOVC_ARC_BB 10   // arco: solo lado M2/M4, hacia atras

uint8_t movAplicado = 255;   // 255 = todavia no se ha mandado nada

void aplicarMov(uint8_t codigo) {
  if (codigo == movAplicado) return;    // ya esta asi: no repetir el I2C
  movAplicado = codigo;
  switch (codigo) {
    case MOVC_FWD:    forward();       break;
    case MOVC_BACK:   backward();      break;
    case MOVC_TURNL:  turnLeft();      break;
    case MOVC_TURNR:  turnRight();     break;
    case MOVC_MOVEL:  moveLeft();      break;
    case MOVC_MOVER:  moveRight();     break;
    case MOVC_ARC_AF: arcoLadoA(+1);   break;
    case MOVC_ARC_BF: arcoLadoB(+1);   break;
    case MOVC_ARC_AB: arcoLadoA(-1);   break;
    case MOVC_ARC_BB: arcoLadoB(-1);   break;
    default:          patron(0,0,0,0); break;   // parado
  }
}

void stopMoving() { aplicarMov(MOVC_STOP); }

// ═════════════════════════════════════════════════════════════
//  DECISION DE MOVIMIENTO (compartida: COM virtual y RPi fisico)
// ═════════════════════════════════════════════════════════════
//  Traduce los cuatro booleanos del protocolo antiguo (GPIO) a un codigo.
uint8_t codigoDesdeBooleanos(bool adelante, bool atras, bool izquierda, bool derecha) {
  int activos = (int)adelante + (int)atras + (int)izquierda + (int)derecha;

  if (activos >= 3 || (adelante && atras) || (izquierda && derecha)) {
    return MOVC_STOP;                 // Combinaciones invalidas -> parar
  } else if (adelante && izquierda) { return MOVC_TURNL; }
  else if   (adelante && derecha)   { return MOVC_TURNR; }
  else if   (atras    && izquierda) { return MOVC_TURNL; }
  else if   (atras    && derecha)   { return MOVC_TURNR; }
  else if   (adelante)              { return MOVC_FWD;   }
  else if   (atras)                 { return MOVC_BACK;  }
  else if   (izquierda)             { return MOVC_MOVEL; }
  else if   (derecha)               { return MOVC_MOVER; }
  return MOVC_STOP;                                          // Nada activo
}

void aplicarMovimiento(bool adelante, bool atras, bool izquierda, bool derecha) {
  movComandado = codigoDesdeBooleanos(adelante, atras, izquierda, derecha);
  aplicarMov(movComandado);
}

// ═════════════════════════════════════════════════════════════
//  CONTROL POR PS2X — MOVIMIENTO
// ═════════════════════════════════════════════════════════════
// Retorna true si el PS2X tomó el control del movimiento
//  MAPA DEL MANDO (ahora cada boton hace lo que dice su nombre):
//     PAD ARRIBA / ABAJO   avanzar / retroceder
//     PAD IZQ / DER        girar sobre su propio eje
//     L1 / R1              desplazamiento lateral (sin girar)
//     L2 / R2 con el PAD   giro amplio: empuja solo un lado
bool handlePS2Movement() {
  if (ps2x.Button(PSB_PAD_UP)) {
    if      (ps2x.Button(PSB_L2)) aplicarMov(MOVC_ARC_BF);
    else if (ps2x.Button(PSB_R2)) aplicarMov(MOVC_ARC_AF);
    else                          aplicarMov(MOVC_FWD);
    return true;

  } else if (ps2x.Button(PSB_PAD_DOWN)) {
    if      (ps2x.Button(PSB_L2)) aplicarMov(MOVC_ARC_BB);
    else if (ps2x.Button(PSB_R2)) aplicarMov(MOVC_ARC_AB);
    else                          aplicarMov(MOVC_BACK);
    return true;

  } else if (ps2x.Button(PSB_PAD_LEFT)) {
    aplicarMov(MOVC_TURNL); return true;
  } else if (ps2x.Button(PSB_PAD_RIGHT)) {
    aplicarMov(MOVC_TURNR); return true;
  } else if (ps2x.Button(PSB_L1)) {
    aplicarMov(MOVC_MOVEL); return true;
  } else if (ps2x.Button(PSB_R1)) {
    aplicarMov(MOVC_MOVER); return true;
  }

  return false; // PS2X no presionó ningún botón de movimiento
}

// ═════════════════════════════════════════════════════════════
//  CONTROL POR PS2X — SERVOS DEL BRAZO
// ═════════════════════════════════════════════════════════════
void handlePS2Servos() {
  // Stick izquierdo X → Servo1
  if (ps2x.Analog(PSS_LX) > 240) {
    if (Servo1->readDegrees() > ARM_MIN[0])
      Servo1->writeServo(Servo1->readDegrees() - 1);
  } else if (ps2x.Analog(PSS_LX) < 10) {
    if (Servo1->readDegrees() < ARM_MAX[0])
      Servo1->writeServo(Servo1->readDegrees() + 1);
  }

  // Stick izquierdo Y → Servo2
  if (ps2x.Analog(PSS_LY) > 240) {
    if (Servo2->readDegrees() > ARM_MIN[1])
      Servo2->writeServo(Servo2->readDegrees() - 1);
  } else if (ps2x.Analog(PSS_LY) < 10) {
    if (Servo2->readDegrees() < ARM_MAX[1])
      Servo2->writeServo(Servo2->readDegrees() + 1);
  }

  // Stick derecho Y → Servo3
  if (ps2x.Analog(PSS_RY) > 240) {
    if (Servo3->readDegrees() > ARM_MIN[2])
      Servo3->writeServo(Servo3->readDegrees() - 1);
  } else if (ps2x.Analog(PSS_RY) < 10) {
    if (Servo3->readDegrees() < ARM_MAX[2])
      Servo3->writeServo(Servo3->readDegrees() + 1);
  }

  // Stick derecho X → Servo4
  if (ps2x.Analog(PSS_RX) > 240) {
    if (Servo4->readDegrees() > ARM_MIN[3])
      Servo4->writeServo(Servo4->readDegrees() - 1);
  } else if (ps2x.Analog(PSS_RX) < 10) {
    if (Servo4->readDegrees() < ARM_MAX[3])
      Servo4->writeServo(Servo4->readDegrees() + 1);
  }
}
ar un
//  encoder de los que se usan.
QGPMaker_Encoder encoder1(1);   // motor M1  (lado A)  [header Encoder1: D8,D9]
QGPMaker_Encoder encoder2(2);   // motor M2  (lado B)  [header Encoder2: D6,D7]

//  Cuentas por vuelta COMPLETA del eje de salida, segun el fabricante:
//     12 PPR x 4 (cuadratura) x 90 (reductora) = 4320
//  Sirve para pasar de cuentas a vueltas o a distancia recorrida:
//     vueltas = encoder1.read() / (float)CUENTAS_POR_VUELTA;
const long CUENTAS_POR_VUELTA = 4320;

void reiniciarEncoders() {
  encoder1.write(0);
  encoder2.write(0);
}


void responderEncoders() {
  Serial.print(F("ENC,"));
  Serial.print(encoder1.read());
  Serial.print(F(","));
  Serial.print(encoder2.read());
  Serial.println(F(",0,0"));
}

void responderRPM() {
  Serial.print(F("ENCRPM,"));
  Serial.print(encoder1.getRPM());
  Serial.print(F(","));
  Serial.print(encoder2.getRPM());
  Serial.println(F(",0,0"));
}

// ═════════════════════════════════════════════════════════════
//  DISPENSADOR — utilidades
// ═════════════════════════════════════════════════════════════
//  Suelta las cuatro bobinas: apaga las cuatro luces del ULN2003 y deja de
//  calentar el motor. Se llama SIEMPRE al terminar de girar. Es lo unico que
//  evita que la ruleta se quede consumiendo ~200 mA parada, que es corriente
//  robada al Motor Shield y a los servos.
void liberarBobinas() {
  digitalWrite(PIN_IN1, LOW);
  digitalWrite(PIN_IN2, LOW);
  digitalWrite(PIN_IN3, LOW);
  digitalWrite(PIN_IN4, LOW);
}

// Escribe UN estado de la secuencia en las cuatro bobinas.
//  Por construccion enciende como mucho dos: si alguna vez se ven las cuatro
//  luces fijas, no lo ha hecho este codigo (mira el cableado con PINTEST).
void escribirBobinas(uint8_t patronBits) {
  digitalWrite(PIN_IN1, (patronBits & 0b1000) ? HIGH : LOW);
  digitalWrite(PIN_IN2, (patronBits & 0b0100) ? HIGH : LOW);
  digitalWrite(PIN_IN3, (patronBits & 0b0010) ? HIGH : LOW);
  digitalWrite(PIN_IN4, (patronBits & 0b0001) ? HIGH : LOW);
}

// ═════════════════════════════════════════════════════════════
//  RULETA - GIRO EN UNA SOLA DIRECCION (retroceso PROHIBIDO)
//  Logica SELECT + DISPENSE (misma que Pillbox_Dispensador.ino)
// ═════════════════════════════════════════════════════════════
//  GIRO NO BLOQUEANTE. Ver la nota larga junto a SECUENCIA_PASOS: la libreria
//  Stepper se quedaba dentro hasta 19,5 s y el Arduino se volvia sordo al
//  puerto serie. Aqui se da un paso y se vuelve, de modo que leerSerial() sigue
//  corriendo mientras la ruleta gira.

long pasosPendientes = 0;         // > 0 mientras queda giro por hacer
uint8_t faseRuleta   = 0;         // indice dentro de SECUENCIA_PASOS
unsigned long ultimoPasoUs = 0;

bool ruletaGirando() { return pasosPendientes > 0; }

// Da un paso si ya toca por tiempo. Devuelve true si aun queda giro pendiente.
bool servirRuleta() {
  if (pasosPendientes <= 0) return false;
  unsigned long ahora = micros();
  //  Resta sin signo: se comporta bien cuando micros() da la vuelta (~70 min).
  if (ahora - ultimoPasoUs < US_POR_PASO) return true;
  ultimoPasoUs = ahora;
  faseRuleta = (faseRuleta + 1) & 0x03;
  escribirBobinas(SECUENCIA_PASOS[faseRuleta]);
  pasosPendientes--;
  if (pasosPendientes == 0) {
    liberarBobinas();
    return false;
  }
  return true;
}

//  Cierto mientras hay un giro en curso. Sirve para dos cosas: rechazar una
//  segunda orden de ruleta a mitad de giro (descuadraria compActual y la EEPROM)
//  y no conducir el chasis con el mando mientras cae la pastilla.
bool ruletaOcupada = false;

// Encolan trabajo y esperan a que termine SIN dejar de atender el puerto serie.
//  Se mantiene la forma bloqueante hacia quien llama (SELECT/DISPENSE siguen
//  siendo secuenciales, que es lo que espera el Pillbox), pero por dentro el
//  serie se sigue leyendo: ya no se pierde ninguna orden mientras gira.
//  Se declaran aqui y se definen tras leerSerial(): se usan mutuamente. NO se
//  confia en los prototipos que genera el IDE de Arduino, porque solo los crea
//  al preprocesar el .ino y cualquier otro compilador (o un .cpp) fallaria.
void girarPasos(long pasos);
void esperarAtendiendo(unsigned long ms);

// Avanza 'k' compartimientos HACIA ADELANTE (solo adelante; k normalizado 0..7).
void avanzarComps(int k) {
  k = ((k % N_COMPARTIMIENTOS) + N_COMPARTIMIENTOS) % N_COMPARTIMIENTOS;
  if (k > 0) {
    girarPasos((long)k * PASOS_POR_COMP);
  }
}

// Vuelve a HOME (compartimiento 1 arriba) completando el giro hacia adelante.
void irAHome() {
  avanzarComps((N_COMPARTIMIENTOS - (compActual - 1)) % N_COMPARTIMIENTOS);
  compActual = 1;
  EEPROM.write(EEPROM_COMP_ADDR, compActual);
}

// SELECT,N / GOTO,N: coloca el compartimiento N ARRIBA (posicion de espera),
// avanzando solo lo necesario hacia adelante. No dispensa.
void irACompartimiento(int destino) {
  destino = constrain(destino, 1, N_COMPARTIMIENTOS);
  avanzarComps((destino - compActual + N_COMPARTIMIENTOS) % N_COMPARTIMIENTOS);
  compActual = destino;
  EEPROM.write(EEPROM_COMP_ADDR, compActual);
  Serial.print(F("POS,"));
  Serial.println(compActual);
}

// DISPENSE,N: parte de HOME, lleva N a la zona de dispensado (abajo) con la
// formula rot = (N<=4)?N+3:N-5, acciona el servo y vuelve a HOME. Todo adelante.
void dispensar(int n) {
  stopMoving();                                // seguridad: chasis detenido
  n = constrain(n, 1, N_COMPARTIMIENTOS);
  irAHome();

  int rot = (n <= 4) ? (n + 3) : (n - 5);      // 1..8 -> 4,5,6,7,0,1,2,3
  avanzarComps(rot);                           // comp N a la zona de dispensado

  servoDispensador.write(SERVO_DISPENSA);
  esperarAtendiendo(2500);          // antes delay(): 3 s sordo por pastilla
  servoDispensador.write(SERVO_REPOSO);
  esperarAtendiendo(500);

  avanzarComps((N_COMPARTIMIENTOS - rot) % N_COMPARTIMIENTOS);   // vuelve a HOME
  compActual = 1;
  EEPROM.write(EEPROM_COMP_ADDR, compActual);

  Serial.print(F("DISPENSADO,"));
  Serial.println(n);
  Serial.print(F("POS,"));
  Serial.println(compActual);
}

// Aplica una direccion de movimiento a partir de un texto. Acepta ingles y
// espanol. Sirve tanto para "MOVE,<dir>" como para escribir la direccion sola.
//  Devuelve TRUE si la direccion se reconocio.
//
//  CAMBIO IMPORTANTE: antes cualquier palabra desconocida caia en "las cuatro
//  a false" (o sea PARAR) y AUN ASI se contestaba OK,MOVE,<lo que fuera>. Ese
//  ACK falso es lo que impedia detectar el desajuste: Vision mandaba SPINL,
//  el robot se paraba, y Vision recibia un OK. Ahora una direccion
//  desconocida devuelve false y quien llama responde ERR.
bool moverDireccion(String dir) {
  dir.toUpperCase();
  vAdelante = vAtras = vIzquierda = vDerecha = false;
  bool conocida = true;
  uint8_t codigo = MOVC_STOP;

  if      (dir == "FWD"  || dir == "FORWARD"  || dir == "ADELANTE") { vAdelante  = true; codigo = MOVC_FWD;   }
  else if (dir == "BACK" || dir == "BACKWARD" || dir == "ATRAS")    { vAtras     = true; codigo = MOVC_BACK;  }
  else if (dir == "LEFT" || dir == "IZQUIERDA"|| dir == "IZQ")      { vIzquierda = true; codigo = MOVC_MOVEL; }
  else if (dir == "RIGHT"|| dir == "DERECHA"  || dir == "DER")      { vDerecha   = true; codigo = MOVC_MOVER; }
  // Giro sobre el propio eje. Las primitivas turnLeft()/turnRight() ya
  // existian; lo que faltaba era poder PEDIRLAS por nombre.
  else if (dir == "SPINL" || dir == "GIROIZQ" || dir == "SPINLEFT")  codigo = MOVC_TURNL;
  else if (dir == "SPINR" || dir == "GIRODER" || dir == "SPINRIGHT") codigo = MOVC_TURNR;
  else if (dir == "STOP"  || dir == "PARAR")                         codigo = MOVC_STOP;
  else conocida = false;               // desconocida: se para, pero se avisa

  movComandado = codigo;
  aplicarMov(codigo);
  return conocida;
}

// True si 'cmd' es una direccion de movimiento suelta (sin el prefijo MOVE).
bool esDireccion(const String &cmd) {
  return cmd == "FWD" || cmd == "FORWARD" || cmd == "ADELANTE" ||
         cmd == "BACK" || cmd == "BACKWARD" || cmd == "ATRAS" ||
         cmd == "LEFT" || cmd == "IZQUIERDA" || cmd == "IZQ" ||
         cmd == "RIGHT" || cmd == "DERECHA" || cmd == "DER" ||
         cmd == "SPINL" || cmd == "GIROIZQ" || cmd == "SPINLEFT" ||
         cmd == "SPINR" || cmd == "GIRODER" || cmd == "SPINRIGHT" ||
         cmd == "STOP" || cmd == "PARAR";
}

// ═════════════════════════════════════════════════════════════
//  MOVIMIENTOS ESPECIALES ("trucos" 1..4)
// ═════════════════════════════════════════════════════════════
//  Vision los enviaba como TRUCO,<1..4> y el firmware NO tenia ese comando:
//  contestaba ERR,TRUCO,1 y no pasaba nada. Se implementan con las primitivas
//  que ya existian (aplicarMov), sin tocar el cableado.
//
//  Duran unos segundos y BLOQUEAN el bucle. Es aceptable porque son una
//  accion pedida a proposito, igual que dispensar; al terminar se para el
//  chasis y se devuelve el control. La respuesta FIN,TRUCO,<n> le dice a
//  Python que ya acabo.
void ejecutarTruco(int n) {
  switch (n) {
    case 1:   // Trompo: una vuelta sobre el eje
      aplicarMov(MOVC_TURNR); delay(1500);
      break;
    case 2:   // Zig-zag
      for (int i = 0; i < 2; i++) {
        aplicarMov(MOVC_MOVEL); delay(400);
        aplicarMov(MOVC_MOVER); delay(400);
      }
      break;
    case 3:   // Baile: adelante/atras alternando
      for (int i = 0; i < 2; i++) {
        aplicarMov(MOVC_FWD);  delay(350);
        aplicarMov(MOVC_BACK); delay(350);
      }
      break;
    case 4:   // Celebrar: giro a un lado y al otro
      aplicarMov(MOVC_TURNL); delay(700);
      aplicarMov(MOVC_TURNR); delay(700);
      break;
    default:
      break;
  }
  // Volver SIEMPRE al estado que estaba pedido por COM (normalmente parado).
  aplicarMov(movComandado);
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

  //  Ordenes que mueven la ruleta. Si llega una mientras ya esta girando, se
  //  rechaza en vez de reentrar: dos giros solapados descuadrarian compActual y
  //  la posicion guardada en EEPROM dejaria de corresponder con la realidad.
  //  El resto de ordenes (MOVE, STOP, PING, ENC...) SI se atienden girando.
  if (ruletaOcupada && (cmd == "SELECT" || cmd == "GOTO" || cmd == "HOME" ||
                        cmd == "DISPENSE" || cmd == "DISPENSAR" ||
                        cmd == "STEPTEST" || cmd == "PINTEST")) {
    Serial.print(F("ERR,OCUPADO,")); Serial.println(cmd);
    return;
  }

  if (cmd == "SELECT" || cmd == "GOTO") {
    // ACK inmediato: confirma que el comando LLEGO y el giro va a empezar. Asi
    // se distingue "no llego" de "llego pero el Arduino se reinicio a mitad de
    // giro" (bajon de tension). El POS,<n> final llega al terminar de girar.
    Serial.print(F("OK,GOTO,")); Serial.println(arg.toInt());
    irACompartimiento(arg.toInt());
  } else if (cmd == "DISPENSE" || cmd == "DISPENSAR") {
    int n = (arg.length() > 0) ? arg.toInt() : compActual;
    Serial.print(F("OK,DISPENSE,")); Serial.println(n);   // ACK inmediato (ver arriba)
    dispensar(n);
  } else if (cmd == "HOME") {
    Serial.println(F("OK,HOME"));                          // ACK inmediato (ver arriba)
    irAHome();
    Serial.print(F("POS,"));
    Serial.println(compActual);
  } else if (cmd == "SERVO") {
    servoDispensador.write(constrain(arg.toInt(), 0, 90));
    Serial.print(F("SERVO,"));
    Serial.println(arg.toInt());
  } else if (cmd == "GETPOS") {
    Serial.print(F("POS,"));
    Serial.println(compActual);

  } else if (cmd == "MOVE") {
    // MOVE,<dir>  dir = FWD | BACK | LEFT | RIGHT | SPINL | SPINR | STOP
    if (moverDireccion(arg)) {
      Serial.print(F("OK,MOVE,")); Serial.println(arg);
    } else {
      // Direccion desconocida: el robot queda parado Y se dice la verdad.
      Serial.print(F("ERR,")); Serial.println(linea);
    }

  } else if (esDireccion(cmd)) {
    // Direccion escrita SOLA (sin el prefijo MOVE)
    moverDireccion(cmd);
    Serial.print(F("OK,MOVE,")); Serial.println(cmd);

  } else if (cmd == "VEL") {
    // VEL,<200..255>  velocidad del chasis. Se recorta al rango util: por
    // debajo de 200 estos motores con reductora apenas arrancan con carga.
    int v = arg.toInt();
    if (arg.length() == 0 || v == 0) {
      Serial.print(F("ERR,")); Serial.println(linea);
    } else {
      velocidadActual = (uint8_t)constrain(v, VELOCIDAD_MIN, VELOCIDAD_MAX);
      // Forzar que el proximo aplicarMov REENVIE las ordenes al shield: si no,
      // al no cambiar el codigo de movimiento se saltaria el I2C y la
      // velocidad nueva no llegaria hasta el siguiente cambio de direccion.
      movAplicado = 255;
      aplicarMov(movComandado);
      Serial.print(F("OK,VEL,")); Serial.println(velocidadActual);
    }

  } else if (cmd == "TRUCO") {
    // TRUCO,<1..4>  movimiento especial. ACK inmediato + FIN al terminar,
    // igual que DISPENSE: asi Python distingue "no llego" de "llego y esta
    // en marcha" de "ya termino".
    int n = arg.toInt();
    if (n < 1 || n > 4) {
      Serial.print(F("ERR,")); Serial.println(linea);
    } else {
      Serial.print(F("OK,TRUCO,")); Serial.println(n);
      ejecutarTruco(n);
      Serial.print(F("FIN,TRUCO,")); Serial.println(n);
    }

  } else if (cmd == "PING") {
    // Comprobar que el enlace serie esta vivo, sin mover nada.
    Serial.println(F("PONG"));

  } else if (cmd == "PROTO") {
    // Version del protocolo que implementa ESTE firmware. Python la compara
    // con la suya al arrancar y avisa si no coinciden, en vez de descubrirlo
    // cuando un comando no hace nada.
    Serial.print(F("PROTO,")); Serial.print(VERSION_PROTOCOLO);
    Serial.println(F(",MEDIBOT"));

  } else if (cmd == "GPIO") {
    // GPIO,<pin>,<val>  (protocolo de Vision). pin 17=adel, 27=atras, 22=izq, 23=der
    int coma2 = arg.indexOf(',');
    String pinStr = (coma2 >= 0) ? arg.substring(0, coma2) : arg;
    String valStr = (coma2 >= 0) ? arg.substring(coma2 + 1) : "0";
    pinStr.trim(); valStr.trim();
    if (pinStr.equalsIgnoreCase("CLEANUP")) {
      vAdelante = vAtras = vIzquierda = vDerecha = false;
      stopMoving();
    } else {
      int  pin = pinStr.toInt();
      bool val = (valStr.toInt() != 0);
      if      (pin == 17) vAdelante  = val;
      else if (pin == 27) vAtras     = val;
      else if (pin == 22) vIzquierda = val;
      else if (pin == 23) vDerecha   = val;
      aplicarMovimiento(vAdelante, vAtras, vIzquierda, vDerecha);
    }
    // ACK: en el protocolo TODA orden contesta algo. Sin esto, Python no
    // podia distinguir "lo recibio" de "se perdio por el cable".
    Serial.print(F("OK,GPIO,")); Serial.println(arg);

  } else if (cmd == "PWM") {
#if USAR_SERVOS_CAMARA
    // PWM,<pin>,<duty>  (protocolo de Vision para servos de camara).
    //  pin 18 = pan, 13 = tilt.  duty 2.5..12.5 % -> angulo 0..180 grados
    int coma2 = arg.indexOf(',');
    if (coma2 >= 0) {
      int   pin  = arg.substring(0, coma2).toInt();
      float duty = arg.substring(coma2 + 1).toFloat();
      int   ang  = (int)((duty - 2.5) / 10.0 * 180.0);
      ang = constrain(ang, 0, 180);
      if      (pin == 18) servoPan.write(ang);
      else if (pin == 13) servoTilt.write(ang);
    }
#endif
    // Sin soporte pan/tilt montado (USAR_SERVOS_CAMARA 0) el comando se acepta
    // y se ignora: Vision lo envia igualmente al seguir una cara y no debe
    // recibir un ERR por algo que no es un fallo. Pero SI se confirma, para
    // que Python sepa que llego.
    Serial.print(F("OK,PWM,")); Serial.println(arg);

  } else if (cmd == "ENC") {
    // Posicion acumulada de los encoders (cuentas).
    responderEncoders();

  } else if (cmd == "ENCRPM") {
    // Velocidad de giro de cada motor, en RPM (la calcula la libreria).
    responderRPM();

  } else if (cmd == "ENCRESET") {
    // Poner los contadores a cero (p.ej. antes de medir un recorrido).
    reiniciarEncoders();
    responderEncoders();

  } else if (cmd == "MOTORTEST") {
    // Diagnostico: prueba cada motor DC por separado, 1 s hacia adelante.
    // Sirve para aislar si el problema es el Motor Shield, el cableado o la
    // alimentacion (si NINGUNO gira, casi seguro falta alimentacion externa
    // al shield: los motores no arrancan solo con el USB del Arduino).
    Serial.println(F("MOTORTEST: probando motores 1..4 (1 s c/u)"));
    QGPMaker_DCMotor* motores[4] = { DCMotor_1, DCMotor_2, DCMotor_3, DCMotor_4 };
    for (int i = 0; i < 4; i++) {
      Serial.print(F("  motor ")); Serial.println(i + 1);
      motores[i]->setSpeed(velocidadActual);
      motores[i]->run(FORWARD);
      delay(1000);
      motores[i]->run(RELEASE);
      delay(300);
    }
    Serial.println(F("MOTORTEST: fin"));

  } else if (cmd == "STEPTEST") {
    // Diagnostico del PASO A PASO, aislado del resto (como MOTORTEST para los DC).
    // Gira la ruleta 'k' compartimientos (por defecto 8 = una vuelta completa),
    // imprimiendo cada paso. Uso: STEPTEST  o  STEPTEST,3
    //  - Si GIRA aqui pero NO con SELECT/DISPENSE -> el stepper y su cableado
    //    estan bien; el problema esta fuera del firmware (tipicamente un bajon
    //    de tension al mover a la vez motores DC / servos por el mismo USB:
    //    alimenta el ULN2003 / el shield con una fuente aparte).
    //  - Si NO gira ni aqui -> usa PINTEST, que dice cual es el problema.
    int comps = (arg.length() > 0) ? arg.toInt() : N_COMPARTIMIENTOS;
    comps = constrain(comps, 1, 64);
    Serial.print(F("STEPTEST: girando "));
    Serial.print(comps);
    Serial.println(F(" compartimiento(s) hacia adelante..."));
    for (int i = 0; i < comps; i++) {
      girarPasos(PASOS_POR_COMP);
      Serial.print(F("  comp "));
      Serial.println(i + 1);
    }
    Serial.println(F("STEPTEST: fin"));

  } else if (cmd == "PINTEST") {
    // Diagnostico de CABLEADO del ULN2003: enciende UNA bobina cada vez, 1,2 s,
    // diciendo cual deberia iluminarse. Es la prueba que distingue un problema
    // de firmware de uno de cables, y responde justo a "se encienden todas las
    // luces": aqui tiene que verse UNA sola encendida en cada tramo.
    //
    //   4 luces encendidas a la vez  -> los cables NO estan en A0..A3; algo mas
    //       esta moviendo esos pines (tipico: bobinas puestas en 10-13, que son
    //       del mando PS2, o en 6-9, que son los headers de encoder).
    //   se enciende otra distinta    -> el orden IN1..IN4 esta cruzado; corrige
    //       los cables o las cuatro lineas RULETA_INx del principio del sketch.
    //   NINGUNA se enciende          -> falta el 5 V del ULN2003 o su GND no
    //       esta unido al GND del Arduino (el mas frecuente de todos).
    Serial.println(F("PINTEST: 1 bobina cada vez. Debe encenderse UNA sola luz."));
    const uint8_t soloUna[4] = { 0b1000, 0b0100, 0b0010, 0b0001 };
    for (int i = 0; i < 4; i++) {
      Serial.print(F("  IN")); Serial.print(i + 1);
      Serial.print(F(" (pin A")); Serial.print(i); Serial.println(F(") ENCENDIDA"));
      escribirBobinas(soloUna[i]);
      esperarAtendiendo(1200);
    }
    liberarBobinas();
    Serial.println(F("PINTEST: fin (todas apagadas)"));

  } else if (cmd == "I2CSCAN") {
    // Diagnostico: escanea el bus I2C y lista las direcciones que responden.
    // El Motor Shield (tipo Adafruit v2 / QGPMaker) suele estar en 0x60.
    // Si NO aparece 0x60, el shield no se comunica (revisar SDA/SCL, encastre
    // o que la libreria sea la correcta para tu shield).
    Serial.println(F("I2CSCAN: buscando dispositivos I2C..."));
    int encontrados = 0;
    for (byte addr = 1; addr < 127; addr++) {
      Wire.beginTransmission(addr);
      if (Wire.endTransmission() == 0) {
        Serial.print(F("  encontrado 0x"));
        if (addr < 16) Serial.print(F("0"));
        Serial.println(addr, HEX);
        encontrados++;
      }
    }
    Serial.print(F("I2CSCAN: "));
    Serial.print(encontrados);
    Serial.println(F(" dispositivo(s). El Motor Shield suele estar en 0x60."));

  } else {
    Serial.print(F("ERR,"));
    Serial.println(linea);
  }
}

// ---------- Lectura NO bloqueante de comandos por Serial ----------
//  Se evita Serial.readStringUntil() para no congelar el control
//  del mando/chasis hasta 1 s cuando llega una linea incompleta.
//  LONGITUD MAXIMA DE LINEA. El Arduino UNO tiene 2 KB de RAM: si por ruido
//  en el cable llegara un flujo sin ningun '\n', el String creceria hasta
//  agotar la memoria y la placa se reiniciaria sola. Con un tope, una linea
//  demasiado larga se descarta y se avisa, que es un fallo recuperable.
#define LINEA_MAX 96

void leerSerial() {
  while (Serial.available() > 0) {
    char c = Serial.read();
    if (c == '\n') {
      procesarComando(bufferSerial);
      bufferSerial = "";
    } else if (c != '\r') {
      if (bufferSerial.length() < LINEA_MAX) {
        bufferSerial += c;
      } else {
        // Linea desbordada: tirarla entera y avisar. Procesar un trozo seria
        // peor: un "MOVE,FW" cortado se interpretaria como direccion invalida.
        bufferSerial = "";
        Serial.println(F("ERR,LINEA_DEMASIADO_LARGA"));
      }
    }
  }
}

// ---------- Giro de la ruleta atendiendo el serie ----------
//  Encola 'pasos' y no vuelve hasta darlos todos, PERO sin dejar sordo al
//  Arduino: en cada vuelta se llama a leerSerial(). Asi el Pillbox sigue
//  siendo secuencial (SELECT termina antes de contestar POS) y a la vez no se
//  pierde ni una orden de la Raspberry mientras el motor gira.
//
//  El guardia ruletaOcupada impide que una orden que llegue a mitad de giro
//  (otro SELECT, un DISPENSE) vuelva a entrar aqui y se enrede consigo misma.
//  Las ordenes de chasis SI se atienden: un STOP tiene que funcionar siempre,
//  incluso con la ruleta girando.
void girarPasos(long pasos) {
  if (pasos <= 0) return;
  pasosPendientes = pasos;
  ultimoPasoUs = micros() - US_POR_PASO;   // el primer paso sale ya
  ruletaOcupada = true;
  while (servirRuleta()) {
    leerSerial();
  }
  ruletaOcupada = false;
  liberarBobinas();
}

//  Espera 'ms' sin dejar de atender el serie. Sustituye a los delay() del
//  dispensador, que dejaban al Arduino sordo 3 s por cada pastilla.
void esperarAtendiendo(unsigned long ms) {
  unsigned long inicio = millis();
  while (millis() - inicio < ms) {
    leerSerial();
  }
}

// ═════════════════════════════════════════════════════════════
//  SETUP
// ═════════════════════════════════════════════════════════════
void setup() {
  Serial.begin(9600);
  // Anunciarse en cuanto arranca: Python detecta asi que el Arduino se
  // reinicio (p.ej. por un bajon de tension) y con que protocolo habla.
  Serial.print(F("READY,MEDIBOT,")); Serial.println(VERSION_PROTOCOLO);

  // ---- Motor Shield / Movimiento ----
  //  1600 Hz: frecuencia PWM adecuada para MOTORES DC (a 50 Hz casi no
  //  reciben potencia y no giran). NOTA: los servos del brazo por el shield
  //  (Servo1..4) necesitan 50 Hz, asi que a 1600 no funcionan; los servos que
  //  SI se usan (dispensador y camara pan/tilt) van por la libreria Servo
  //  estandar en pines 2/3/5, no por el shield, asi que no se ven afectados.
  AFMS.begin(1600);

  // Inicializar PS2X (OPCIONAL). Se intenta unas veces; si NO hay mando
  // conectado se CONTINUA igual (antes se colgaba en un bucle infinito y el
  // Arduino nunca respondia por Serial).
  //  PS2 en 13(clock), 11(command), 10(attention), 12(data): son sus pines de
  //  SIEMPRE y NO se tocan (el mando esta cableado asi de fabrica en el robot).
  //  No chocan con la ruleta: esta va en A0..A3, y el compilador lo
  //  comprueba (ver COMPROBACION DE PINES).
  ps2Presente = false;
  for (int intento = 0; intento < 10; intento++) {
    if (ps2x.config_gamepad(13, 11, 10, 12, true, true) == 0) {
      ps2Presente = true;
      break;
    }
    delay(100);
  }

  // Posición inicial de servos del brazo
  Servo1->writeServo(90);
  Servo2->writeServo(90);
  Servo3->writeServo(90);
  Servo4->writeServo(60);

  stopMoving();

  // ---- Dispensador ----
  pinMode(PIN_IN1, OUTPUT);
  pinMode(PIN_IN2, OUTPUT);
  pinMode(PIN_IN3, OUTPUT);
  pinMode(PIN_IN4, OUTPUT);
  liberarBobinas();

  //  La velocidad de la ruleta ya no se fija aqui: es US_POR_PASO, arriba,
  //  junto a la secuencia de pasos. Sigue equivaliendo a 10 rpm.

  servoDispensador.attach(SERVO_PIN);
  servoDispensador.write(SERVO_REPOSO);

#if USAR_SERVOS_CAMARA
  // ---- Servos de camara (pan/tilt) ----
  servoPan.attach(PAN_PIN);
  servoTilt.attach(TILT_PIN);
  servoPan.write(90);
  servoTilt.write(90);
#endif

  // ---- Encoders de los motores ----
  //  La libreria QGPMaker_Encoder se encarga de configurarlos; aqui solo se
  //  ponen las cuentas a cero para partir de un origen conocido.
  reiniciarEncoders();

  // Leer ultima posicion guardada en EEPROM
  byte saved = EEPROM.read(EEPROM_COMP_ADDR);
  if (saved >= 1 && saved <= N_COMPARTIMIENTOS) {
    compActual = saved;
  } else {
    compActual = 1;
    EEPROM.write(EEPROM_COMP_ADDR, compActual);
  }

  // Enviar posicion actual al host
  Serial.print(F("POS,"));
  Serial.println(compActual);
  Serial.println(F("LISTO"));
}

// ═════════════════════════════════════════════════════════════
//  LOOP PRINCIPAL — sin delay(), todo por millis()
// ═════════════════════════════════════════════════════════════
//  POR QUE ERA POCO SENSIBLE: el bucle hacia delay(30) + delay(2) en cada
//  vuelta, y ademas delay(300) al pulsar X. Durante esos milisegundos el
//  Arduino no leia el puerto serie ni miraba el mando: de ahi el retraso al
//  responder. Ahora el bucle no se detiene nunca; el mando se consulta cada
//  20 ms (que es el ritmo que necesita la libreria PS2X) y el resto del
//  tiempo se dedica a atender el serie al instante.
//
//  ps2xActivo es GLOBAL a proposito: entre lectura y lectura del mando hay
//  vueltas en las que no se consulta, y si la variable se reiniciase a false
//  en cada vuelta el robot se pararia a ratos (movimiento a tirones).
const unsigned long PERIODO_PS2_MS = 20;

unsigned long ultimaLecturaPS2 = 0;
bool ps2xActivo = false;

void loop() {
  // ── Dispensador: comandos de la RPi/PC por Serial (no bloqueante) ──
  leerSerial();

  // ── Ruleta: red de seguridad ──────────────────────────────
  //  girarPasos() ya sirve el giro entero, asi que normalmente no queda nada
  //  pendiente aqui. Se llama igualmente para que ningun camino pueda dejar
  //  las bobinas a medias: si quedaran pasos sin dar, las cuatro luces se
  //  quedarian fijas y el motor calentandose parado.
  servirRuleta();

  // ── Mando PS2, a su propio ritmo y sin bloquear ───────────
  //  Mientras se dispensa NO se conduce con el mando: el chasis tiene que
  //  estar quieto para que la pastilla caiga donde debe. Las ordenes por COM
  //  (incluido STOP) se siguen atendiendo siempre.
  if (ps2Presente && !ruletaGirando() &&
      (millis() - ultimaLecturaPS2 >= PERIODO_PS2_MS)) {
    ultimaLecturaPS2 = millis();

    // La vibracion del boton X se pide en la MISMA lectura, en vez de con
    // tres llamadas y un delay(300) que congelaba el robot al pulsarlo.
    bool vibrar = ps2x.Button(PSB_CROSS);
    ps2x.read_gamepad(vibrar, vibrar ? 200 : 0);

    ps2xActivo = handlePS2Movement();
    handlePS2Servos();     // servos del brazo (solo con mando PS2)
  }

  // ── Sin mando (o mando inactivo): movimiento recibido por COM ──
  //  Se llama en cada vuelta, pero aplicarMov() no habla con el shield si el
  //  movimiento no ha cambiado, asi que no cuesta nada.
  if (!ps2xActivo) {
    // Se aplica el CODIGO pedido por COM, no los cuatro booleanos: estos no
    // pueden representar el giro sobre el eje y lo convertirian en parada.
    aplicarMov(movComandado);
  }
}
