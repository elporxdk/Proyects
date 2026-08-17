/*
 * ============================================================
 *  MEDIBOT — Firmware unificado
 *  Movimiento (chasis + brazo) + Dispensador de pastillas
 * ============================================================
 *  Combina en un solo sketch:
 *
 *   1) "Movement v1 MEDIBOT"
 *        - Chasis con 4 motores DC y brazo con 4 servos,
 *          gestionados por el QGPMaker Motor Shield (I2C).
 *        - Control por mando PS2 (PS2X) y/o por Serial (COM)
 *          desde la Raspberry Pi (comandos MOVE/GPIO).
 *
 *   2) "Dispensador MEDIBOT" (deepseek_cpp)
 *        - Ruleta de 8 compartimientos con motor paso a paso
 *          28BYJ-48 + ULN2003.
 *        - Servo dispensador (libreria Servo estandar).
 *        - Ordenes de texto por Serial (9600 baud) y posicion
 *          guardada en EEPROM.
 *
 *  ------------------------------------------------------------
 *  MAPA DE PINES (Arduino Uno)
 *  ------------------------------------------------------------
 *    0,1        -> Serial (USB, comandos desde la Pi/PC)  ¡RESERVADOS!
 *                  (en el shield salen por el header WIFI/BT: NO son libres)
 *    2          -> Servo dispensador (libreria Servo)   [header Encoder3]
 *    3          -> libre (el resto del header Encoder3, sin usar)
 *                  ¡NO conectes el encoder de M3! Su header es D2/D3 y D2 es
 *                  la senal del servo: dos salidas sobre la misma linea.
 *    4,5        -> LIBRES (header Encoder4, sin usar: M4 era redundante)
 *    6,7        -> Encoder del motor M2  (lado B)        [header Encoder2]
 *    8,9        -> Encoder del motor M1  (lado A)        [header Encoder1]
 *    10,11,12,13-> Mando PS2 (attention/command/data/clock) - OPCIONAL
 *                  Son sus pines de siempre: NO se tocan.
 *    A0..A3     -> Motor paso a paso ULN2003 (ruleta)  <-- tu cableado
 *    A4,A5      -> I2C (SDA/SCL) del Motor Shield  -> motores DC y servos brazo
 *
 *  NUNCA cablees el ULN2003 (ni nada) a los pines 0 y 1: son el RX/TX del USB.
 *  Con Serial.begin() el UART se apodera de ellos, digitalWrite deja de valer
 *  y el motor no gira; encima las respuestas del Arduino saldrian por una
 *  bobina y las subidas de sketch pueden fallar.
 *
 *  Los servos de camara pan/tilt estan DESACTIVADOS (USAR_SERVOS_CAMARA 0)
 *  porque este robot no lleva ese soporte. Con M4 fuera, sus pines (D3 y D5)
 *  estan libres: se puede montar el pan/tilt sin perder ningun encoder.
 *
 *  Motores DC: por el Motor Shield (I2C). AFMS.begin(1600) para que giren
 *  (a 50 Hz casi no reciben potencia).
 *
 *  Todas las ordenes llegan por Serial (via el hub serial_hub.py del lado PC).
 *
 *  El mando PS2 y el Motor Shield son OPCIONALES: si no estan conectados, el
 *  Arduino arranca igual y responde por Serial (movimiento por COM + dispensador).
 *
 *  ------------------- ORDENES DISPENSADOR (Pillbox) ----------
 *  SELECT,N: coloca el compartimiento N ARRIBA (zona de seleccion/espera).
 *  DISPENSE,N: parte de HOME, lleva N a la zona de dispensado (abajo) con
 *  rot=(N<=4)?N+3:N-5, acciona el servo y vuelve a HOME. Todo hacia adelante.
 *
 *  DIRECCION UNICA: los movimientos hacia atras estan PROHIBIDOS. La ruleta
 *  SIEMPRE avanza (pasos positivos); si el destino queda "detras", completa la
 *  vuelta hacia adelante. Aplica a SELECT, HOME y DISPENSE.
 *
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
 *  Libreria oficial del shield (QGPMaker_Encoder). Habilitados M1 y M2, uno
 *  por lado del chasis (M1/M3 son un lado y M2/M4 el otro, asi que el segundo
 *  de cada lado no aporta dato nuevo).
 *  El de M3 NO se puede usar, porque su pin D2 es el unico sitio libre para el servo
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
 *  El cableado real NO coincide con la logica ingenua: M1 y M3 giran al reves
 *  y los lados son M1/M3 contra M2/M4. Se corrige por software en las seis
 *  funciones de movimiento; no hay que tocar ningun cable. Ademas los motores
 *  llegan al shield en orden invertido, lo que se arregla en las cuatro lineas
 *  de getMotor() (ver "Motores DC").
 *
 *  TRES BANDERAS DE INVERSION, una por movimiento, INDEPENDIENTES entre si.
 *  Cada funcion mira SOLO la suya, asi que se ajustan de una en una probando
 *  el robot, sin que tocar una descoloque a las otras:
 *
 *    bandera             movimiento                gestos            valor
 *    ------------------  ------------------------  ----------------  -----
 *    INVERTIR_AVANCE     adelante / atras          W/S, PAD ARR/AB   true
 *    INVERTIR_GIRO       giro sobre el eje         W+A/W+D, PAD I/D  true
 *    INVERTIR_LATERAL    desplazamiento lateral    A/D solas, L1/R1  false
 *
 *  Los valores son los del ROBOT MONTADO, sacados de probarlo. Ojo al ajustar:
 *  empieza SIEMPRE por el avance. Con el avance del reves, el desplazamiento
 *  lateral parece invertido aunque no lo este, y se acaba corrigiendo dos veces
 *  lo que era un solo fallo (aqui paso: INVERTIR_LATERAL estuvo en true hasta
 *  que se arreglo el avance, y entonces quedo bien solo).
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

// ------------- Motor paso a paso (ruleta) -------------
//  ═══ EL UNICO SITIO DONDE SE DECLARA EL CABLEADO DEL ULN2003 ═══
//  Si mueves los cables, cambia SOLO estas cuatro lineas. Antes el pinout
//  aparecia repetido en tres comentarios distintos y NO COINCIDIAN entre si
//  (uno decia A0..A3, otro "ULN2003 en 8/9/10/11", otro "el stepper en 6-9").
//  Seguir el comentario equivocado lleva a cablear las bobinas sobre los pines
//  del mando PS2 (10-13) o sobre los headers de encoder (6-9): entonces las
//  luces del ULN2003 se encienden solas —las mueve el PS2, no la ruleta— y el
//  motor no gira. Ahora hay una sola fuente de verdad y el compilador la
//  comprueba (ver COMPROBACION DE PINES mas abajo).
//  Se usan PIN_A0..PIN_A3 y no A0..A3 a proposito: en el core de Arduino, A0 es
//  una  static const uint8_t , no una macro, y el PREPROCESADOR no puede verla
//  (la tomaria como 0 y la comprobacion de choques de abajo pasaria siempre sin
//  mirar nada). PIN_A0 si es una macro, y vale exactamente lo mismo.
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

//  POR QUE EN LOS ANALOGICOS: en el Uno, A0..A5 son pines digitales completos
//  (digitalWrite funciona igual que en 0-13), asi que mueven el ULN2003 sin
//  problema. Ponerlos aqui deja LIBRES los cuatro headers Encoder del shield
//  (D2-D9) para los encoders de los motores. Excepcion: A6/A7 del Nano/Pro
//  Mini son solo entrada analogica, no servirian; en el Uno no existen.
//
//  ATENCION, NO USAR NUNCA LOS PINES 0 NI 1:
//    Son el RX/TX del puerto serie por USB (header WIFI/BT del shield) y estan
//    soldados al chip USB de la placa. En cuanto se hace Serial.begin() el
//    hardware del UART se apodera de ellos y digitalWrite() deja de tener
//    efecto, asi que las bobinas conectadas ahi NUNCA reciben la secuencia de
//    pasos y el motor solo vibra. Ademas todo lo que responde el Arduino
//    (POS, OK,MOVE...) saldria por el pin 1 hacia una bobina.
const int PIN_IN1 = RULETA_IN1;
const int PIN_IN2 = RULETA_IN2;
const int PIN_IN3 = RULETA_IN3;
const int PIN_IN4 = RULETA_IN4;

const int  PASOS_POR_VUELTA  = 2048;                                 // 28BYJ-48 (ajusta si es necesario)
const int  N_COMPARTIMIENTOS = 8;
const int  PASOS_POR_COMP    = PASOS_POR_VUELTA / N_COMPARTIMIENTOS; // 256 pasos = 45 grados

// ── COMPROBACION DE PINES EN TIEMPO DE COMPILACION ───────────────────────
//  Si alguien recablea la ruleta sobre un pin que ya tiene dueno, el sketch NO
//  COMPILA y dice cual es el choque, en vez de subirse y portarse raro. Es la
//  clase de fallo que se manifiesta como "las luces se encienden todas y el
//  motor no gira", que desde fuera parece un problema de la placa ULN2003.
//  Ojo: aqui van NUMEROS y macros, nunca  const int , por lo dicho arriba sobre
//  el preprocesador. El 2 es SERVO_PIN; que sigan siendo el mismo pin lo
//  comprueba PIN_SERVO_ESPERADO justo debajo.
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

// ── SECUENCIA DE PASOS (antes: libreria Stepper) ─────────────────────────
//  SE SUSTITUYE LA LIBRERIA Stepper POR ESTA TABLA. Motivo: Stepper::step() es
//  BLOQUEANTE — se queda dentro haciendo espera activa hasta terminar TODOS los
//  pasos. A 10 rpm son 2,93 ms por paso y 256 pasos por compartimiento, o sea
//  0,75 s por compartimiento: un SELECT lejano bloquea 5,3 s y un DISPENSE
//  hasta 19,5 s. Durante todo ese rato leerSerial() NO se llama, y el buffer de
//  recepcion del Uno son 64 bytes = 67 ms de trafico a 9600 baudios. Todo lo
//  que manda la Raspberry mientras la ruleta gira SE PIERDE, incluidos los '\n':
//  dos ordenes se pegan en una linea sin sentido. De ahi "no se envian
//  correctamente los movimientos". Ahora se avanza UN paso por vuelta de loop()
//  y el serie se atiende sin interrupcion.
//
//  La tabla reproduce EXACTAMENTE lo que generaba Stepper con el orden de pines
//  (IN1, IN3, IN2, IN4) que usaba el sketch, asi que la calibracion mecanica no
//  cambia: siguen siendo 2048 pasos por vuelta y 256 por compartimiento.
//  Bits: IN1,IN2,IN3,IN4 -> 1100, 0110, 0011, 1001 (dos bobinas CONTIGUAS).
//  Ese "contiguas" es lo importante: con el orden ingenuo (IN1,IN2,IN3,IN4)
//  saldria 1010/0101, o sea bobinas OPUESTAS, y el motor solo zumba.
//  NUNCA hay mas de dos bobinas activas: las cuatro luces encendidas a la vez
//  es imposible por construccion.
const uint8_t SECUENCIA_PASOS[4] = { 0b1100, 0b0110, 0b0011, 0b1001 };

//  Microsegundos por paso. Equivale al antiguo ruleta.setSpeed(10) rpm:
//  60e6 / 2048 pasos / 10 rpm = 2930 us.
const unsigned long US_POR_PASO = 2930;

int compActual = 1;   // compartimiento que esta ARRIBA (zona de carga/espera, 1..8)

// El mando PS2 es OPCIONAL: si no esta conectado, el robot sigue funcionando
// (movimiento por COM desde Vision y dispensador por Serial). Antes el arranque
// se colgaba esperando el PS2 y el Arduino no respondia nada.
bool ps2Presente = false;

// VERSION DEL PROTOCOLO SERIE. Tiene que coincidir con VERSION_PROTOCOLO de
// medibot_protocolo.py. Se responde a PROTO y se anuncia al arrancar, para
// que un desajuste se vea de inmediato en vez de manifestarse como "un
// comando que no hace nada".
#define VERSION_PROTOCOLO 2

// Buffer para lectura no bloqueante de comandos por Serial
String bufferSerial = "";

// Estado de movimiento recibido por COM (comandos MOVE / GPIO desde Vision).
//  Se aplica en el loop cuando el mando PS2 no tiene el control.
//  POR QUE HAY UN CODIGO Y NO SOLO CUATRO BOOLEANOS: el giro sobre el propio
//  eje NO se puede representar con adelante/atras/izquierda/derecha. Antes el
//  estado eran solo esos cuatro, asi que cuando Vision mandaba MOVE,SPINL el
//  firmware no tenia donde guardarlo: los ponia todos a false (= PARAR) y
//  encima contestaba OK,MOVE,SPINL. El robot se paraba y Vision creia que
//  estaba girando.
//  Los cuatro booleanos se conservan porque el protocolo antiguo
//  GPIO,<pin>,<val> manda un pin cada vez y necesita acumular el estado.
bool vAdelante  = false;
bool vAtras     = false;
bool vIzquierda = false;
bool vDerecha   = false;

uint8_t movComandado = 0;   // 0 = MOVC_STOP. Lo aplica el loop.

// ═════════════════════════════════════════════════════════════
//  FUNCIONES DE MOVIMIENTO (chasis)
// ═════════════════════════════════════════════════════════════
//  CORRECCION DEL CABLEADO, POR SOFTWARE (no se toca ningun cable):
//
//  En este robot los motores NO estan como daba por hecho el codigo original:
//     - M1 y M3 giran al REVES de lo que dice run(FORWARD).
//     - Los lados son M1/M3 contra M2/M4 (no M1/M2 contra M3/M4).
//
//  Se dedujo del unico dato en que coincidieron todas las pruebas: la antigua
//  moveLeft(), que enviaba (M1 atras, M2 adelante, M3 atras, M4 adelante),
//  hacia AVANZAR el robot. De ahi sale todo lo demas, y explica lo que se veia:
//     forward() mandaba los 4 hacia adelante -> los dos lados se oponian, o sea
//     el robot GIRABA sobre su eje (que sobre el suelo se ve como "tambalea").
//
//  Abajo, cada movimiento declara lo que hay que MANDAR a cada motor para que
//  el robot haga de verdad lo que dice el nombre de la funcion.
//  Si algun dia se recablea, solo hay que corregir estas seis lineas.
// ═════════════════════════════════════════════════════════════

// ── LAS TRES BANDERAS DE INVERSION (independientes a proposito) ──────────
//  Un mismo gesto NO llega a los motores por un solo camino. "Izquierda", sin
//  ir mas lejos, pasa por turnLeft() o por moveLeft() segun si vas pisando W:
//
//     gesto                  codigo         funcion        bandera
//     ---------------------  -------------  -------------  -----------------
//     W / PAD ARRIBA         MOVC_FWD       forward()      INVERTIR_AVANCE
//     A sola / L1            MOVC_MOVEL     moveLeft()     INVERTIR_LATERAL
//     W+A / PAD IZQ          MOVC_TURNL     turnLeft()     INVERTIR_GIRO
//
//  Son TRES movimientos fisicos distintos con TRES causas distintas, y por eso
//  no se pueden fundir en una sola bandera:
//
//    - forward/backward mandan a los cuatro motores a EMPUJAR IGUAL. Ninguna
//      reordenacion de los motores puede darles la vuelta (si los cuatro
//      empujes valen lo mismo, su orden da igual). Lo unico que los invierte es
//      que la POLARIDAD de los cuatro este del reves a la vez.
//    - turnLeft/turnRight mandan el MISMO sentido a los cuatro, asi que el
//      ORDEN de los argumentos de patron() tambien les da igual: son inmunes al
//      mapeo de motores (los DCMotor_N invertidos 4,3,2,1 mas arriba).
//    - moveLeft/moveRight mandan sentidos distintos por lado, asi que SI
//      dependen del mapeo: invertirlo ya intercambia una con otra.
//
//  Cada funcion mira SOLO su bandera, de modo que cambiar una no toca a las
//  otras. Si algun dia se recablea y solo uno de los tres sale del reves, se
//  corrige ese y nada mas.
//  (Lo comprueba PruebasBanderasDeInversion en pruebas/test_protocolo_serial.py)
//
//  AL AJUSTARLAS, EMPIEZA POR EL AVANCE. Con el avance invertido, el
//  desplazamiento lateral PARECE invertido aunque no lo este, y se corrige dos
//  veces lo que era un solo fallo. Aqui paso exactamente eso.

//  YA PROBADO EN EL ROBOT MONTADO: el giro sobre el eje salia al reves ("giro
//  izq." giraba a la derecha), asi que va en true.
const bool INVERTIR_GIRO = true;

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

//  TERCERA BANDERA: el avance.
//  El adelante/atras NO puede salir cambiado por como esten repartidos los
//  motores entre las salidas del shield: manda a los cuatro a empujar IGUAL, y
//  cuando los cuatro empujes valen lo mismo da lo mismo cual este delante,
//  detras, a la izquierda o a la derecha. Por eso no hacia falta bandera... si
//  el unico problema fuera el reparto.
//
//  Pero hay otra averia que si lo invierte: que la POLARIDAD de los cuatro
//  motores este del reves a la vez (los cuatro conectores al contrario). Ahi
//  los cuatro siguen empujando igual entre si, solo que hacia el otro lado, y
//  el robot va hacia atras cuando se le pide adelante. Es justo lo que pasa en
//  este robot, asi que la bandera va en true.
//
//  Es INDEPENDIENTE de las otras dos, igual que ellas entre si: cada funcion
//  mira SOLO la suya. Poner o quitar esta no toca el giro ni el desplazamiento.
const bool INVERTIR_AVANCE = true;

void forward()  { if (INVERTIR_AVANCE) patron(+1, -1, +1, -1); else patron(-1, +1, -1, +1); }
void backward() { if (INVERTIR_AVANCE) patron(-1, +1, -1, +1); else patron(+1, -1, +1, -1); }

// Giro sobre su propio eje: un lado adelante y el otro atras.
void turnLeft()  { if (INVERTIR_GIRO) patron(-1,-1,-1,-1); else patron(+1,+1,+1,+1); }
void turnRight() { if (INVERTIR_GIRO) patron(+1,+1,+1,+1); else patron(-1,-1,-1,-1); }

// Desplazamiento lateral, sin cambiar de orientacion (necesita ruedas mecanum).
//  ESTOS SI DEPENDEN DEL MAPEO DE MOTORES, al reves que los giros de arriba:
//  aqui los cuatro argumentos NO valen lo mismo, asi que su orden importa.
//  Invertir el mapeo (4,3,2,1) intercambia moveLeft con moveRight, porque
//  patron(-1,-1,+1,+1) repartido al reves da exactamente patron(+1,+1,-1,-1).
//  Ver la nota de las DOS BANDERAS junto a INVERTIR_GIRO.
//
//  YA PROBADO EN EL ROBOT MONTADO: con el avance corregido (INVERTIR_AVANCE),
//  el desplazamiento sale BIEN tal cual, asi que esta bandera va en false. Se
//  llego aqui probando: primero se puso en true, porque con el avance todavia
//  del reves L1 parecia mover a la derecha; una vez arreglado el avance, el
//  lateral quedo correcto solo.
//
//  Cubre los tres caminos del desplazamiento, porque los tres acaban aqui:
//    - mando PS2:  L1 -> MOVC_MOVEL,  R1 -> MOVC_MOVER
//    - teclas:     GPIO,22 (A) -> MOVC_MOVEL,  GPIO,23 (D) -> MOVC_MOVER
//    - texto:      MOVE,LEFT / MOVE,RIGHT
const bool INVERTIR_LATERAL = false;

void moveLeft()  { if (INVERTIR_LATERAL) patron(+1, +1, -1, -1); else patron(-1, -1, +1, +1); }
void moveRight() { if (INVERTIR_LATERAL) patron(-1, -1, +1, +1); else patron(+1, +1, -1, -1); }

// Giros amplios: solo empuja un lado, el otro queda suelto (L2/R2 del mando).
void arcoLadoA(int8_t s) { patron(-s, 0, -s, 0); }   // lado M1/M3 (van invertidos)
void arcoLadoB(int8_t s) { patron(0, s, 0, s); }     // lado M2/M4

void stopMoving();   // definida mas abajo (necesita el control de repeticion)

// ═════════════════════════════════════════════════════════════
//  APLICAR UN MOVIMIENTO SIN REPETIR ORDENES
// ═════════════════════════════════════════════════════════════
//  POR QUE: cada setSpeed()/run() es una transaccion I2C con el shield. Al
//  quitar los delay() del bucle para que el robot responda al instante, el
//  bucle pasa a dar miles de vueltas por segundo; si en cada una se
//  reenviaran las 8 ordenes I2C, el bus se saturaria y el robot respondería
//  PEOR, no mejor. Guardando cual es el movimiento que YA esta puesto, solo
//  se habla con el shield cuando de verdad cambia algo.
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

// ═════════════════════════════════════════════════════════════
//  ENCODERS DE LOS MOTORES  (libreria oficial QGPMaker_Encoder)
// ═════════════════════════════════════════════════════════════
//  Se usa la libreria del fabricante del shield en vez de leer los pines a
//  mano: ella ya sabe que pines corresponden a cada motor, da la velocidad en
//  RPM hecha, y no ata el sketch a los registros del ATmega (el codigo previo
//  usaba PCINT/PINB/PIND, que solo existen en AVR).
//
//     QGPMaker_Encoder encoderN(N);   // N = numero del motor (M1..M4)
//     encoderN.read()                 // posicion acumulada (int32_t)
//     encoderN.write(0)               // poner a cero (calibrar)
//     encoderN.getRPM()               // velocidad de giro en RPM
//
//  SOLO SE USAN DOS ENCODERS: M1 y M2. Uno por cada lado del chasis.
//
//  POR QUE UNO POR LADO Y NO MAS: los motores van emparejados por lados
//  (ver arcoLadoA/arcoLadoB mas arriba):
//        lado A = M1 y M3        lado B = M2 y M4
//  Los dos motores de un mismo lado giran SIEMPRE juntos, asi que su encoder
//  mide lo mismo. Con M1 + M2 ya se tiene el recorrido de cada lado, que es
//  todo lo que hace falta para odometria (avance = media, giro = diferencia).
//  M4 no aportaba un dato nuevo: solo repetia el de M2.
//
//  M3 NO SE PUEDE USAR, y esa es la razon de fondo: su header (Encoder3) ocupa
//  los pines D2 y D3, y D2 es donde va el SERVO DISPENSADOR. Si se conectara
//  el encoder de M3, su salida y la salida del servo estarian empujando la
//  misma linea: dos drivers peleando por un cable. Ademas de no funcionar,
//  puede danar el pin. Por eso M3 queda fuera por diseno, no por olvido.
//    -> NO conectes nada al header Encoder3.
//
//  Al dejar M4 fuera, D4 y D5 quedan LIBRES. D5 era ademas el pin del servo
//  tilt de la camara: ahora se puede montar el pan/tilt sin sacrificar un
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

// Se responde SIEMPRE con los cuatro campos para no romper a quien ya lea
// estas lineas (Python espera cuatro). Los de M3 y M4 van a 0 porque esos
// encoders no estan habilitados: M3 choca con el servo y M4 era redundante.
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
