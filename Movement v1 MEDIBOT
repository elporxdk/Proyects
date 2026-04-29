#include <Wire.h>
#include "PS2X_lib.h"
#include "QGPMaker_MotorShield.h"

// ── Motor Shield ──────────────────────────────────────────────
QGPMaker_MotorShield AFMS = QGPMaker_MotorShield();
PS2X ps2x;

// ── Límites de servos ─────────────────────────────────────────
long ARM_MIN[] = {10,  10,  40, 10};
long ARM_MAX[] = {170, 140, 170, 102};

// ── Servos ────────────────────────────────────────────────────
QGPMaker_Servo *Servo1 = AFMS.getServo(0);
QGPMaker_Servo *Servo2 = AFMS.getServo(1);
QGPMaker_Servo *Servo3 = AFMS.getServo(2);
QGPMaker_Servo *Servo4 = AFMS.getServo(3);

// ── Motores DC ────────────────────────────────────────────────
QGPMaker_DCMotor *DCMotor_1 = AFMS.getMotor(1);
QGPMaker_DCMotor *DCMotor_2 = AFMS.getMotor(2);
QGPMaker_DCMotor *DCMotor_3 = AFMS.getMotor(3);
QGPMaker_DCMotor *DCMotor_4 = AFMS.getMotor(4);

// ── Pines de entrada desde Raspberry Pi ──────────────────────
//    ⚠️ Usar level shifter o divisor de voltaje (3.3V → 5V)
#define PIN_ADELANTE   6
#define PIN_ATRAS      7
#define PIN_IZQUIERDA  8
#define PIN_DERECHA    9

#define VELOCIDAD 200

// ═════════════════════════════════════════════════════════════
//  FUNCIONES DE MOVIMIENTO
// ═════════════════════════════════════════════════════════════
void forward() {
  DCMotor_1->setSpeed(VELOCIDAD); DCMotor_1->run(FORWARD);
  DCMotor_2->setSpeed(VELOCIDAD); DCMotor_2->run(FORWARD);
  DCMotor_3->setSpeed(VELOCIDAD); DCMotor_3->run(FORWARD);
  DCMotor_4->setSpeed(VELOCIDAD); DCMotor_4->run(FORWARD);
}

void backward() {
  DCMotor_1->setSpeed(VELOCIDAD); DCMotor_1->run(BACKWARD);
  DCMotor_2->setSpeed(VELOCIDAD); DCMotor_2->run(BACKWARD);
  DCMotor_3->setSpeed(VELOCIDAD); DCMotor_3->run(BACKWARD);
  DCMotor_4->setSpeed(VELOCIDAD); DCMotor_4->run(BACKWARD);
}

void turnLeft() {
  DCMotor_1->setSpeed(VELOCIDAD); DCMotor_1->run(BACKWARD);
  DCMotor_2->setSpeed(VELOCIDAD); DCMotor_2->run(BACKWARD);
  DCMotor_3->setSpeed(VELOCIDAD); DCMotor_3->run(FORWARD);
  DCMotor_4->setSpeed(VELOCIDAD); DCMotor_4->run(FORWARD);
}

void turnRight() {
  DCMotor_1->setSpeed(VELOCIDAD); DCMotor_1->run(FORWARD);
  DCMotor_2->setSpeed(VELOCIDAD); DCMotor_2->run(FORWARD);
  DCMotor_3->setSpeed(VELOCIDAD); DCMotor_3->run(BACKWARD);
  DCMotor_4->setSpeed(VELOCIDAD); DCMotor_4->run(BACKWARD);
}

void moveLeft() {
  DCMotor_1->setSpeed(VELOCIDAD); DCMotor_1->run(BACKWARD);
  DCMotor_2->setSpeed(VELOCIDAD); DCMotor_2->run(FORWARD);
  DCMotor_3->setSpeed(VELOCIDAD); DCMotor_3->run(BACKWARD);
  DCMotor_4->setSpeed(VELOCIDAD); DCMotor_4->run(FORWARD);
}

void moveRight() {
  DCMotor_1->setSpeed(VELOCIDAD); DCMotor_1->run(FORWARD);
  DCMotor_2->setSpeed(VELOCIDAD); DCMotor_2->run(BACKWARD);
  DCMotor_3->setSpeed(VELOCIDAD); DCMotor_3->run(FORWARD);
  DCMotor_4->setSpeed(VELOCIDAD); DCMotor_4->run(BACKWARD);
}

void stopMoving() {
  DCMotor_1->setSpeed(0); DCMotor_1->run(RELEASE);
  DCMotor_2->setSpeed(0); DCMotor_2->run(RELEASE);
  DCMotor_3->setSpeed(0); DCMotor_3->run(RELEASE);
  DCMotor_4->setSpeed(0); DCMotor_4->run(RELEASE);
}

// ═════════════════════════════════════════════════════════════
//  CONTROL POR RASPBERRY PI
// ═════════════════════════════════════════════════════════════
void handleRPi() {
  bool adelante  = digitalRead(PIN_ADELANTE);
  bool atras     = digitalRead(PIN_ATRAS);
  bool izquierda = digitalRead(PIN_IZQUIERDA);
  bool derecha   = digitalRead(PIN_DERECHA);

  int activos = (int)adelante + (int)atras + (int)izquierda + (int)derecha;

  if (activos >= 3 || (adelante && atras) || (izquierda && derecha)) {
    stopMoving();                     // Combinaciones inválidas → stop

  } else if (adelante && izquierda) { turnLeft();   }
  else if   (adelante && derecha)   { turnRight();  }
  else if   (atras    && izquierda) { turnLeft();   }
  else if   (atras    && derecha)   { turnRight();  }
  else if   (adelante)              { forward();    }
  else if   (atras)                 { backward();   }
  else if   (izquierda)             { moveLeft();   }
  else if   (derecha)               { moveRight();  }
  else                              { stopMoving(); } // Ningún pin activo
}

// ═════════════════════════════════════════════════════════════
//  CONTROL POR PS2X — MOVIMIENTO
// ═════════════════════════════════════════════════════════════
// Retorna true si el PS2X tomó el control del movimiento
bool handlePS2Movement() {
  if (ps2x.Button(PSB_PAD_UP)) {
    if (ps2x.Button(PSB_L2)) {
      DCMotor_2->setSpeed(VELOCIDAD); DCMotor_2->run(FORWARD);
      DCMotor_4->setSpeed(VELOCIDAD); DCMotor_4->run(FORWARD);
    } else if (ps2x.Button(PSB_R2)) {
      DCMotor_1->setSpeed(VELOCIDAD); DCMotor_1->run(FORWARD);
      DCMotor_3->setSpeed(VELOCIDAD); DCMotor_3->run(FORWARD);
    } else {
      forward();
    }
    return true;

  } else if (ps2x.Button(PSB_PAD_DOWN)) {
    if (ps2x.Button(PSB_L2)) {
      DCMotor_2->setSpeed(VELOCIDAD); DCMotor_2->run(BACKWARD);
      DCMotor_4->setSpeed(VELOCIDAD); DCMotor_4->run(BACKWARD);
    } else if (ps2x.Button(PSB_R2)) {
      DCMotor_1->setSpeed(VELOCIDAD); DCMotor_1->run(BACKWARD);
      DCMotor_3->setSpeed(VELOCIDAD); DCMotor_3->run(BACKWARD);
    } else {
      backward();
    }
    return true;

  } else if (ps2x.Button(PSB_PAD_LEFT)) {
    turnLeft();  return true;
  } else if (ps2x.Button(PSB_PAD_RIGHT)) {
    turnRight(); return true;
  } else if (ps2x.Button(PSB_L1)) {
    moveLeft();  return true;
  } else if (ps2x.Button(PSB_R1)) {
    moveRight(); return true;
  }

  return false; // PS2X no presionó ningún botón de movimiento
}

// ═════════════════════════════════════════════════════════════
//  CONTROL POR PS2X — SERVOS
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

// ═════════════════════════════════════════════════════════════
//  SETUP
// ═════════════════════════════════════════════════════════════
void setup() {
  AFMS.begin(50);

  // Pines RPi como entrada
  pinMode(PIN_ADELANTE,  INPUT);
  pinMode(PIN_ATRAS,     INPUT);
  pinMode(PIN_IZQUIERDA, INPUT);
  pinMode(PIN_DERECHA,   INPUT);

  // Inicializar PS2X
  int error = 0;
  do {
    error = ps2x.config_gamepad(13, 11, 10, 12, true, true);
    if (error == 0) break;
    else delay(100);
  } while (1);

  // Posición inicial de servos
  Servo1->writeServo(90);
  Servo2->writeServo(90);
  Servo3->writeServo(90);
  Servo4->writeServo(60);

  stopMoving();
}

// ═════════════════════════════════════════════════════════════
//  LOOP PRINCIPAL
// ═════════════════════════════════════════════════════════════
void loop() {
  ps2x.read_gamepad(false, 0);
  delay(30);

  // ── Botón X: vibración ───────────────────────────────────
  if (ps2x.Button(PSB_CROSS)) {
    ps2x.read_gamepad(true, 200);
    delay(300);
    ps2x.read_gamepad(false, 0);
  }

  // ── Control de movimiento (PS2X tiene prioridad) ─────────
  bool ps2xActivo = handlePS2Movement();
  if (!ps2xActivo) {
    // Solo usa RPi si el mando no está enviando comandos
    handleRPi();
  }

  // ── Control de servos (siempre PS2X) ─────────────────────
  handlePS2Servos();

  delay(2);
}
