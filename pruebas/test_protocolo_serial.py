#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pruebas del protocolo serie Medibot <-> Arduino.  SIN LA PLACA.
===============================================================
POR QUE EXISTE ESTE FICHERO
---------------------------
El protocolo estaba escrito TRES veces (Vision, Pillbox y firmware) y las tres
se habian separado sin que nadie se enterara, porque el lado Python DESCARTABA
las respuestas del Arduino:

  * MOVE,SPINL / MOVE,SPINR : el firmware no conocia esos nombres, caia en su
    rama "cualquier otra cosa = parar" y CONTESTABA OK,MOVE,SPINL. El robot se
    paraba y Vision recibia un ACK conforme.
  * VEL,<200..255>          : no existia en el firmware -> ERR,VEL,231. El
    deslizador de velocidad de la web no hacia absolutamente nada.
  * TRUCO,<1..4>            : tampoco existia -> ERR,TRUCO,1.

Estas pruebas cierran ese agujero por dos caminos independientes:

  1. PARIDAD CON EL FIRMWARE: se lee el propio sketch (RUTA_FIRMWARE, o sea
     firmware/medibot_movimiento/medibot_movimiento.ino) y se comprueba
     que implementa cada comando y cada movimiento de la tabla del protocolo.
     Esto se ejecuta sin placa y sin compilador.
  2. IDA Y VUELTA REAL: se levanta un Arduino simulado detras de un par de
     pseudoterminales, o sea un puerto serie de verdad, y se comprueba que un
     comando llega, se interpreta, se ejecuta y su respuesta vuelve.

    python3 pruebas/test_protocolo_serial.py
    python3 pruebas/test_protocolo_serial.py -v

Solo necesita la libreria estandar; las pruebas de ida y vuelta requieren
pyserial y openpty (Unix) y se saltan solas si no estan.
"""

import os
import re
import sys
import threading
import time
import unittest

# Las pruebas viven en pruebas/ y el codigo en medibot/ y herramientas/.
# Python solo mira la carpeta del script, asi que hay que anadir las otras.
_RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(_RAIZ, "medibot"), os.path.join(_RAIZ, "herramientas")]

import medibot_protocolo as protocolo
from arduino_falso import (RUTA_FIRMWARE, ArduinoFalso, PuertoSimulado,
                           comandos_del_firmware, direcciones_del_firmware,
                           version_del_firmware)

try:
    import serial            # noqa: F401
    HAY_PYSERIAL = True
except ImportError:
    HAY_PYSERIAL = False

HAY_PTY = hasattr(os, "openpty")
PUEDE_IDA_Y_VUELTA = HAY_PYSERIAL and HAY_PTY


# =============================================================================
class PruebasParidadFirmware(unittest.TestCase):
    """LA prueba que faltaba: que ambos lados hablen el mismo idioma.

    Compara la tabla del protocolo contra el codigo del sketch. No necesita
    ni la placa ni el compilador de Arduino."""

    def test_el_firmware_implementa_todos_los_comandos(self):
        implementados = comandos_del_firmware()
        faltan = sorted(set(protocolo.COMANDOS) - implementados)
        self.assertFalse(
            faltan,
            "El firmware NO implementa estos comandos que Python envía: "
            f"{faltan}. Cada uno responderá ERR y su función no hará nada.")

    def test_el_firmware_acepta_todos_los_movimientos(self):
        """Incluye SPINL y SPINR, que era justo lo que faltaba."""
        acepta = direcciones_del_firmware()
        faltan = [m for m in protocolo.MOVIMIENTOS
                  if m != protocolo.PARADO and m not in acepta]
        self.assertFalse(
            faltan,
            f"El firmware no reconoce estos movimientos: {faltan}. "
            "Los interpretaría como 'parar' en vez de ejecutarlos.")

    def test_los_giros_sobre_el_eje_estan_soportados(self):
        acepta = direcciones_del_firmware()
        self.assertIn(protocolo.GIRO_IZQ, acepta)
        self.assertIn(protocolo.GIRO_DER, acepta)

    def test_la_version_de_protocolo_coincide(self):
        version = version_del_firmware()
        self.assertIsNotNone(version, "El firmware no define VERSION_PROTOCOLO")
        self.assertEqual(
            version, protocolo.VERSION_PROTOCOLO,
            "La versión del protocolo del firmware no coincide con la de "
            "Python: uno de los dos se quedó atrás.")

    def test_los_baudios_coinciden(self):
        with open(RUTA_FIRMWARE, encoding="utf-8", errors="ignore") as f:
            codigo = f.read()
        self.assertIn(f"Serial.begin({protocolo.BAUDIOS})", codigo,
                      f"El firmware no arranca a {protocolo.BAUDIOS} baud")

    def test_el_firmware_termina_las_lineas_en_LF(self):
        with open(RUTA_FIRMWARE, encoding="utf-8", errors="ignore") as f:
            codigo = f.read()
        self.assertIn(r"if (c == '\n')", codigo,
                      "El firmware debe cortar los comandos por LF")

    def test_el_firmware_acota_la_longitud_de_linea(self):
        """Sin tope, un flujo sin '\\n' agota los 2 KB de RAM del Arduino."""
        with open(RUTA_FIRMWARE, encoding="utf-8", errors="ignore") as f:
            codigo = f.read()
        self.assertIn("LINEA_MAX", codigo)


# =============================================================================
class PruebasEncoders(unittest.TestCase):
    """Guardan la decision de cableado, que es facil de deshacer sin querer.

    El encoder de M3 comparte el header (D2/D3) con la SENAL DEL SERVO
    dispensador (D2). Instanciarlo pondria la salida del encoder y la del
    Arduino sobre la misma linea: dos drivers peleando por un cable, que ni
    funciona ni es sano para el pin. Si alguien lo anade por comodidad, esta
    prueba lo para antes de que llegue al robot."""

    def setUp(self):
        with open(RUTA_FIRMWARE, encoding="utf-8", errors="ignore") as f:
            self.codigo = f.read()
        # Numero de motor de cada encoder instanciado: QGPMaker_Encoder encoderN(N)
        import re
        self.instanciados = sorted(
            int(n) for n in re.findall(r'QGPMaker_Encoder\s+encoder\d+\s*\(\s*(\d+)\s*\)',
                                       self.codigo))

    def test_hay_exactamente_dos_encoders(self):
        self.assertEqual(len(self.instanciados), 2,
                         f"Se esperaban 2 encoders y hay {self.instanciados}")

    def test_son_M1_y_M2_uno_por_lado(self):
        """M1/M3 son un lado y M2/M4 el otro: hace falta uno de cada."""
        self.assertEqual(self.instanciados, [1, 2],
                         "Los encoders habilitados deben ser M1 y M2, uno por "
                         f"lado del chasis; hay {self.instanciados}")

    def test_el_encoder_de_M3_NO_esta_instanciado(self):
        """Su header ocupa D2, que es la señal del servo dispensador."""
        self.assertNotIn(3, self.instanciados,
                         "El encoder de M3 comparte pin (D2) con el servo "
                         "dispensador: dos salidas sobre la misma línea.")

    def test_el_pin_del_servo_sigue_siendo_D2(self):
        """Si el servo se mueve de pin, la razón para excluir M3 cambia."""
        self.assertIn("SERVO_PIN      = 2", self.codigo,
                      "El servo ya no está en D2: revisa si M3 sigue vetado.")

    def test_las_respuestas_llevan_cuatro_campos(self):
        """Se mantienen 4 campos aunque solo haya 2 encoders: Python los lee
        asi y romperlo obligaria a tocar los dos lados a la vez."""
        self.assertIn('Serial.println(F(",0,0"))', self.codigo)

    def test_el_simulador_tambien_devuelve_M3_y_M4_a_cero(self):
        ard = ArduinoFalso()
        ard.encoders = [100, 200, 999, 999]      # M3/M4 no deben salir
        linea = ard.procesar("ENC")[0]
        self.assertEqual(protocolo.enteros(linea, 4), [100, 200, 0, 0])


# =============================================================================
class PruebasBanderasDeInversion(unittest.TestCase):
    """Que INVERTIR_GIRO e INVERTIR_LATERAL sigan siendo INDEPENDIENTES.

    "Izquierda" no llega a los motores por un solo camino: la tecla A sola (y
    L1) desplaza de lado -> moveLeft(); pero W+A (y el PAD) giran sobre el eje
    -> turnLeft(). Son dos movimientos fisicos distintos con dos causas
    distintas, asi que hacen falta dos banderas:

      - turnLeft/turnRight mandan el MISMO sentido a los cuatro motores, luego
        el orden de los argumentos les da igual: inmunes al mapeo de motores.
      - moveLeft/moveRight mandan sentidos distintos por lado: SI dependen del
        mapeo, invertirlo ya las intercambia.

    Es facil "simplificar" esto a una sola bandera y, al hacerlo, condenar a
    que uno de los dos movimientos quede al reves para siempre. Estas pruebas
    lo impiden sin necesidad de tener el robot delante."""

    def setUp(self):
        with open(RUTA_FIRMWARE, encoding="utf-8", errors="ignore") as f:
            self.codigo = f.read()

    def _cuerpo(self, funcion):
        """La linea de definicion de una funcion de movimiento de una linea."""
        m = re.search(r"^void\s+%s\s*\(\s*\)\s*\{(.*)\}\s*$" % funcion,
                      self.codigo, re.M)
        self.assertIsNotNone(m, f"No se encuentra la definicion de {funcion}()")
        return m.group(1)

    def _ramas(self, funcion, bandera):
        """(rama_con_la_bandera_true, rama_con_la_bandera_false) de un
        `if (BANDERA) patron(...); else patron(...);`, como listas de enteros."""
        cuerpo = self._cuerpo(funcion)
        m = re.search(r"if\s*\(\s*%s\s*\)\s*patron\(([^)]*)\)\s*;\s*"
                      r"else\s*patron\(([^)]*)\)\s*;" % bandera, cuerpo)
        self.assertIsNotNone(
            m, f"{funcion}() ya no es un 'if ({bandera}) patron(..) else "
               f"patron(..)'. Si se ha reescrito, revisa que siga siendo un "
               f"intercambio real de izquierda/derecha.")
        return ([int(x) for x in m.group(1).split(",")],
                [int(x) for x in m.group(2).split(",")])

    # Lo que se midio sobre el robot montado. Si se recablea, se cambia AQUI y
    # en el sketch: tenerlo en dos sitios es justo lo que hace que la prueba
    # sirva de algo (avisa de un cambio hecho sin querer).
    VALORES = {"INVERTIR_AVANCE": "true",
               "INVERTIR_GIRO": "true",
               "INVERTIR_LATERAL": "false"}

    # ---- las tres banderas existen, una sola vez, y estan como toca ----
    def test_las_tres_banderas_se_declaran_una_sola_vez(self):
        for bandera in self.VALORES:
            with self.subTest(bandera=bandera):
                declaraciones = re.findall(
                    r"^const\s+bool\s+%s\s*=" % bandera, self.codigo, re.M)
                self.assertEqual(
                    len(declaraciones), 1,
                    f"{bandera} debe declararse exactamente una vez "
                    f"(hay {len(declaraciones)}).")

    def test_este_robot_tiene_los_valores_medidos(self):
        """Los tres se ajustaron probando el robot, no por teoria.

        El avance y el giro salian del reves; el desplazamiento lateral, una vez
        arreglado el avance, quedo bien solo. Ojo con ese ultimo: mientras el
        avance estuvo invertido, el lateral PARECIA invertido tambien y llego a
        ponerse en true. Es el motivo de que el orden para ajustarlas sea
        siempre avance -> giro -> lateral."""
        for bandera, esperado in self.VALORES.items():
            with self.subTest(bandera=bandera):
                m = re.search(r"const\s+bool\s+%s\s*=\s*(\w+)\s*;" % bandera,
                              self.codigo)
                self.assertEqual(
                    m.group(1), esperado,
                    f"{bandera} deberia estar en {esperado} en este robot.")

    # ---- INDEPENDENCIA: cada funcion mira SOLO su bandera ----
    #  Que funcion le toca a cada bandera.
    DUENO = {"INVERTIR_AVANCE": ("forward", "backward"),
             "INVERTIR_GIRO": ("turnLeft", "turnRight"),
             "INVERTIR_LATERAL": ("moveLeft", "moveRight")}

    def test_cada_funcion_mira_solo_su_bandera(self):
        """LA propiedad que permite ajustarlas de una en una probando el robot.

        Si una funcion mirara dos banderas, corregir un movimiento descolocaria
        otro y no habria forma de dejar los tres bien a la vez."""
        for bandera, funciones in self.DUENO.items():
            ajenas = [b for b in self.DUENO if b != bandera]
            for funcion in funciones:
                cuerpo = self._cuerpo(funcion)
                with self.subTest(funcion=funcion):
                    self.assertIn(
                        bandera, cuerpo,
                        f"{funcion}() deberia mirar {bandera}")
                    for otra in ajenas:
                        self.assertNotIn(
                            otra, cuerpo,
                            f"{funcion}() no debe depender de {otra}: son "
                            "movimientos distintos con causas distintas, y "
                            "acoplarlos impide corregirlos por separado.")

    # ---- cada bandera es un INTERCAMBIO de verdad, no un apano ----
    def test_invertir_giro_intercambia_izquierda_con_derecha(self):
        """Lo que hace turnLeft con la bandera puesta tiene que ser
        exactamente lo que hace turnRight sin ella, y al reves. Si no, la
        bandera no invierte: inventa un tercer comportamiento."""
        izq_si, izq_no = self._ramas("turnLeft", "INVERTIR_GIRO")
        der_si, der_no = self._ramas("turnRight", "INVERTIR_GIRO")
        self.assertEqual(izq_si, der_no, "turnLeft(true) != turnRight(false)")
        self.assertEqual(izq_no, der_si, "turnLeft(false) != turnRight(true)")

    def test_invertir_lateral_intercambia_izquierda_con_derecha(self):
        izq_si, izq_no = self._ramas("moveLeft", "INVERTIR_LATERAL")
        der_si, der_no = self._ramas("moveRight", "INVERTIR_LATERAL")
        self.assertEqual(izq_si, der_no, "moveLeft(true) != moveRight(false)")
        self.assertEqual(izq_no, der_si, "moveLeft(false) != moveRight(true)")

    def test_el_lateral_empuja_los_dos_lados_en_sentidos_opuestos(self):
        """Un desplazamiento lateral necesita un lado a cada sentido (ruedas
        mecanum). Si los cuatro fueran iguales seria un giro, no un lateral."""
        for funcion in ("moveLeft", "moveRight"):
            for rama in self._ramas(funcion, "INVERTIR_LATERAL"):
                with self.subTest(funcion=funcion, rama=rama):
                    self.assertEqual(
                        sorted(rama), [-1, -1, 1, 1],
                        f"{funcion} deberia mandar dos motores a cada "
                        "sentido; asi no desplaza de lado.")

    def test_el_giro_manda_los_cuatro_motores_al_mismo_sentido(self):
        """Es la razon POR LA QUE el giro es inmune al mapeo de motores: si los
        cuatro argumentos valen lo mismo, su orden no importa. Si esto cambia,
        la nota de las dos banderas deja de ser cierta."""
        for funcion in ("turnLeft", "turnRight"):
            for rama in self._ramas(funcion, "INVERTIR_GIRO"):
                with self.subTest(funcion=funcion, rama=rama):
                    self.assertEqual(
                        len(set(rama)), 1,
                        f"{funcion} ya no manda el mismo sentido a los cuatro "
                        "motores: ahora SI depende del mapeo.")

    # ---- el adelante/atras NO puede verse arrastrado por las banderas ----
    #  M1 y M3 giran al reves de lo que dice run(FORWARD) (ver la cabecera del
    #  sketch), asi que el EMPUJE fisico de cada motor es  T_i = POLARIDAD_i * s_i
    #  siendo s_i lo que se le manda con patron().
    POLARIDAD = (-1, +1, -1, +1)

    def _empuje(self, patron):
        return [p * s for p, s in zip(self.POLARIDAD, patron)]

    def test_el_avance_empuja_los_cuatro_motores_igual(self):
        """LA propiedad que hace que el avance sea inmune al REPARTO de motores:
        si los cuatro EMPUJAN igual, da lo mismo cual este a la izquierda, a la
        derecha, delante o detras. Ninguna de las 24 reordenaciones posibles
        puede darle la vuelta; solo puede invertirlo que la polaridad de los
        cuatro este del reves, que es para lo que esta INVERTIR_AVANCE.

        Se comprueba en las DOS ramas de la bandera. Es ademas la regresion
        historica del sketch: alguien "arreglo" forward() a patron(+1,+1,+1,+1)
        pensando que era lo natural y, como M1/M3 giran al reves, los dos lados
        se oponian y el robot GIRABA sobre su eje en vez de avanzar."""
        for funcion in ("forward", "backward"):
            for rama in self._ramas(funcion, "INVERTIR_AVANCE"):
                empuje = self._empuje(rama)
                with self.subTest(funcion=funcion, rama=rama):
                    self.assertEqual(
                        len(set(empuje)), 1,
                        f"{funcion}() deberia empujar los cuatro motores al "
                        f"mismo lado y da {empuje}. Con empujes mezclados el "
                        "robot gira o se tuerce en vez de ir recto.")

    def test_invertir_avance_intercambia_adelante_con_atras(self):
        """Igual que en las otras dos: lo que hace forward con la bandera puesta
        tiene que ser exactamente lo que hace backward sin ella."""
        ade_si, ade_no = self._ramas("forward", "INVERTIR_AVANCE")
        atr_si, atr_no = self._ramas("backward", "INVERTIR_AVANCE")
        self.assertEqual(ade_si, atr_no, "forward(true) != backward(false)")
        self.assertEqual(ade_no, atr_si, "forward(false) != backward(true)")

    def test_adelante_y_atras_son_opuestos_exactos(self):
        """Si alguien toca uno y se olvida del otro, dejan de ser simetricos."""
        for i, rama in enumerate(("con la bandera", "sin la bandera")):
            adelante = self._ramas("forward", "INVERTIR_AVANCE")[i]
            atras = self._ramas("backward", "INVERTIR_AVANCE")[i]
            with self.subTest(rama=rama):
                self.assertEqual(
                    [-x for x in adelante], atras,
                    "backward() deberia ser exactamente forward() al reves")


# =============================================================================
class PruebasRuleta(unittest.TestCase):
    """La ruleta (28BYJ-48 + ULN2003) y su secuencia de bobinas.

    EL FALLO QUE VIGILAN
    --------------------
    Se reporto que "no se envian correctamente los movimientos a la placa del
    ULN2003 y se encienden todas las luces". Dos cosas distintas:

      1. Las cuatro luces encendidas A LA VEZ es IMPOSIBLE por codigo: la
         secuencia enciende siempre exactamente dos bobinas. Si se ven las
         cuatro fijas, o el motor gira (85 Hz al 50%: el ojo las ve todas) o
         los cables no estan en los pines que cree el firmware.
      2. Lo que SI era un fallo real: Stepper::step() bloqueaba hasta 19,5 s en
         un DISPENSE. El buffer serie del UNO son 64 bytes = 67 ms a 9600
         baudios, asi que todo lo que mandaba la Raspberry mientras la ruleta
         giraba se perdia. De ahi "no se envian correctamente los movimientos".

    Estas pruebas fijan las dos propiedades para que no vuelvan."""

    @classmethod
    def setUpClass(cls):
        with open(RUTA_FIRMWARE, encoding="utf-8", errors="ignore") as f:
            cls.codigo = f.read()
        m = re.search(r'SECUENCIA_PASOS\[4\]\s*=\s*\{([^}]*)\}', cls.codigo)
        assert m, "No se encontro la tabla SECUENCIA_PASOS en el firmware"
        cls.secuencia = [int(v.strip(), 0) for v in m.group(1).split(",")]

    # ---- La secuencia de bobinas ------------------------------------
    def test_la_secuencia_tiene_cuatro_estados(self):
        self.assertEqual(len(self.secuencia), 4)

    def test_nunca_hay_mas_de_dos_bobinas_encendidas(self):
        """Las cuatro luces a la vez no las puede producir este codigo."""
        for i, paso in enumerate(self.secuencia):
            self.assertEqual(bin(paso).count("1"), 2,
                             f"El paso {i} enciende {bin(paso).count('1')} "
                             f"bobinas ({paso:04b}); deben ser exactamente 2.")

    def test_las_bobinas_activas_son_CONTIGUAS_no_opuestas(self):
        """El fallo clasico del 28BYJ-48.

        Con el orden ingenuo (IN1,IN2,IN3,IN4) la libreria Stepper genera
        1010 y 0101: bobinas OPUESTAS, que se anulan. El motor zumba, se
        calienta y no gira. Tienen que ser contiguas (1100, 0110, 0011, 1001),
        y 1001 cuenta como contigua porque el anillo cierra."""
        opuestas = {0b1010, 0b0101}
        for i, paso in enumerate(self.secuencia):
            self.assertNotIn(paso, opuestas,
                             f"El paso {i} ({paso:04b}) activa bobinas opuestas: "
                             "el motor zumbaria sin girar.")

    def test_la_secuencia_recorre_el_anillo_sin_repetir(self):
        self.assertEqual(len(set(self.secuencia)), 4, "Hay pasos repetidos")

    def test_cada_bobina_se_usa_lo_mismo(self):
        """Si una bobina se activa mas veces que otra, el giro es irregular."""
        for bit in range(4):
            veces = sum(1 for p in self.secuencia if p & (1 << bit))
            self.assertEqual(veces, 2, f"La bobina {bit} se activa {veces} veces")

    def test_coincide_con_lo_que_generaba_la_libreria_Stepper(self):
        """La calibracion mecanica no cambia al dejar de usar Stepper.

        El sketch la llamaba como Stepper(2048, IN1, IN3, IN2, IN4). Se
        reproduce aqui su tabla interna y se traduce a IN1..IN4."""
        interna = [(1, 0, 1, 0), (0, 1, 1, 0), (0, 1, 0, 1), (1, 0, 0, 1)]
        destino = ["IN1", "IN3", "IN2", "IN4"]      # orden de los argumentos
        esperado = []
        for paso in interna:
            v = dict(zip(destino, paso))
            esperado.append((v["IN1"] << 3) | (v["IN2"] << 2) |
                            (v["IN3"] << 1) | v["IN4"])
        self.assertEqual(self.secuencia, esperado)

    def test_siguen_siendo_2048_pasos_y_256_por_compartimiento(self):
        self.assertIn("PASOS_POR_VUELTA  = 2048", self.codigo)
        self.assertIn("PASOS_POR_VUELTA / N_COMPARTIMIENTOS", self.codigo)

    def test_la_velocidad_equivale_a_las_10_rpm_de_antes(self):
        m = re.search(r'US_POR_PASO\s*=\s*(\d+)', self.codigo)
        self.assertIsNotNone(m, "No se encontro US_POR_PASO")
        rpm = 60e6 / 2048 / int(m.group(1))
        self.assertAlmostEqual(rpm, 10.0, delta=0.1,
                               msg=f"La ruleta iria a {rpm:.1f} rpm, no a 10")

    # ---- Que no vuelva a quedarse sordo -----------------------------
    def test_ya_no_se_usa_la_libreria_Stepper(self):
        """Su step() es bloqueante: es la causa de perder ordenes."""
        self.assertNotIn("#include <Stepper.h>", self.codigo)
        self.assertNotIn("ruleta.step(", self.codigo)

    def test_el_giro_atiende_el_serie_mientras_gira(self):
        cuerpo = re.search(r'void girarPasos\(long pasos\)\s*\{(.*?)\n\}',
                           self.codigo, re.S)
        self.assertIsNotNone(cuerpo, "No se encontro girarPasos()")
        self.assertIn("leerSerial()", cuerpo.group(1),
                      "girarPasos() debe leer el serie mientras gira o se "
                      "pierden ordenes (buffer de 64 bytes = 67 ms).")

    def test_el_dispensador_no_usa_delay(self):
        """delay(2500) dejaba al Arduino sordo 3 s por cada pastilla."""
        cuerpo = re.search(r'void dispensar\(int n\)\s*\{(.*?)\n\}',
                           self.codigo, re.S)
        self.assertIsNotNone(cuerpo, "No se encontro dispensar()")
        #  Sin los comentarios: un  // antes delay(...)  que explica el cambio
        #  no es una llamada, y hacer fallar la prueba por el comentario que
        #  documenta el arreglo seria absurdo.
        codigo = re.sub(r'//[^\n]*', '', cuerpo.group(1))
        self.assertNotIn("delay(", codigo,
                         "Usa esperarAtendiendo(), que sigue leyendo el serie.")

    def test_toda_espera_del_dispensador_atiende_el_serie(self):
        cuerpo = re.search(r'void esperarAtendiendo\(unsigned long ms\)\s*\{(.*?)\n\}',
                           self.codigo, re.S)
        self.assertIsNotNone(cuerpo, "No se encontro esperarAtendiendo()")
        self.assertIn("leerSerial()", cuerpo.group(1))

    # ---- Seguridad y diagnostico ------------------------------------
    def test_las_bobinas_se_sueltan_al_terminar(self):
        """Sin esto se quedan las cuatro luces fijas y el motor calentandose."""
        for funcion in ("girarPasos", "servirRuleta"):
            cuerpo = re.search(r'\b' + funcion + r'\([^)]*\)\s*\{(.*?)\n\}',
                               self.codigo, re.S)
            self.assertIsNotNone(cuerpo, f"No se encontro {funcion}()")
            self.assertIn("liberarBobinas()", cuerpo.group(1),
                          f"{funcion}() debe soltar las bobinas al acabar")

    def test_una_segunda_orden_de_ruleta_a_mitad_de_giro_se_rechaza(self):
        """Dos giros solapados descuadran compActual y la EEPROM."""
        self.assertIn("ruletaOcupada", self.codigo)
        self.assertIn('ERR,OCUPADO', self.codigo)

    def test_STOP_sigue_llegando_con_la_ruleta_girando(self):
        """La seguridad del chasis no se sacrifica: solo se filtran las
        ordenes de RULETA, no las de movimiento."""
        guardia = re.search(r'if \(ruletaOcupada && \((.*?)\)\) \{',
                            self.codigo, re.S)
        self.assertIsNotNone(guardia, "No se encontro el guardia de reentrada")
        filtrados = guardia.group(1)
        for prohibido in ("STOP", "MOVE", "GPIO", "PING", "VEL"):
            self.assertNotIn(f'"{prohibido}"', filtrados,
                             f"{prohibido} NO puede bloquearse mientras gira")

    def test_existe_el_diagnostico_de_cableado(self):
        """PINTEST enciende una bobina cada vez: distingue firmware de cables."""
        self.assertIn('cmd == "PINTEST"', self.codigo)

    def test_el_pinout_esta_declarado_una_sola_vez(self):
        """Antes el pinout aparecia en tres comentarios que no coincidian."""
        for macro in ("RULETA_IN1", "RULETA_IN2", "RULETA_IN3", "RULETA_IN4"):
            definiciones = re.findall(r'^#define ' + macro + r'\s', self.codigo,
                                      re.M)
            self.assertEqual(len(definiciones), 1,
                             f"{macro} definido {len(definiciones)} veces")

    def test_el_compilador_vigila_los_choques_de_pines(self):
        """Cablear la ruleta sobre el PS2 o los encoders no debe compilar."""
        self.assertIn("RULETA_CHOCA", self.codigo)
        self.assertIn("#error", self.codigo)
        #  El preprocesador no ve  const int : usar A0 en vez de PIN_A0 dejaria
        #  la comprobacion siempre en falso sin avisar.
        guardia = re.search(r'#define RULETA_CHOCA.*?\n(?:.*?\\\n)*.*?\n',
                            self.codigo)
        self.assertIsNotNone(guardia)
        self.assertNotIn("SERVO_PIN", guardia.group(0),
                         "SERVO_PIN es const int: el preprocesador lo lee como 0")
        self.assertIn("PIN_A4", guardia.group(0),
                      "Usa PIN_A4 (macro), no A4 (const int)")


# =============================================================================
class PruebasConstruccion(unittest.TestCase):
    """Validar ANTES de escribir en el puerto, no despues de un ERR."""

    def test_comando_simple(self):
        self.assertEqual(protocolo.construir("PING"), "PING")
        self.assertEqual(protocolo.construir("MOVE", "FWD"), "MOVE,FWD")
        self.assertEqual(protocolo.construir("VEL", 231), "VEL,231")

    def test_rechaza_comandos_inexistentes(self):
        with self.assertRaises(protocolo.ErrorProtocolo):
            protocolo.construir("BAILAR")

    def test_rechaza_numero_de_argumentos_incorrecto(self):
        with self.assertRaises(protocolo.ErrorProtocolo):
            protocolo.construir("MOVE")
        with self.assertRaises(protocolo.ErrorProtocolo):
            protocolo.construir("MOVE", "FWD", "extra")

    def test_rechaza_movimientos_desconocidos(self):
        """Si alguien añade un movimiento a la web sin implementarlo abajo."""
        with self.assertRaises(protocolo.ErrorProtocolo):
            protocolo.cmd_mover("DIAGONAL")

    def test_acepta_los_seis_movimientos_reales(self):
        for mov in protocolo.MOVIMIENTOS:
            with self.subTest(mov=mov):
                self.assertEqual(protocolo.cmd_mover(mov), f"MOVE,{mov}")

    def test_velocidad_fuera_de_rango(self):
        with self.assertRaises(protocolo.ErrorProtocolo):
            protocolo.cmd_velocidad(150)
        with self.assertRaises(protocolo.ErrorProtocolo):
            protocolo.cmd_velocidad(300)
        with self.assertRaises(protocolo.ErrorProtocolo):
            protocolo.cmd_velocidad("rapido")
        self.assertEqual(protocolo.cmd_velocidad(231), "VEL,231")

    def test_truco_fuera_de_rango(self):
        with self.assertRaises(protocolo.ErrorProtocolo):
            protocolo.cmd_truco(0)
        with self.assertRaises(protocolo.ErrorProtocolo):
            protocolo.cmd_truco(5)
        self.assertEqual(protocolo.cmd_truco(3), "TRUCO,3")

    def test_un_argumento_con_coma_no_puede_partir_la_trama(self):
        """Una coma dentro de un argumento convertiría un comando en dos."""
        with self.assertRaises(protocolo.ErrorProtocolo):
            protocolo.construir("SERVO", "45,90")

    def test_un_argumento_con_salto_de_linea_tampoco(self):
        with self.assertRaises(protocolo.ErrorProtocolo):
            protocolo.construir("SERVO", "45\nDISPENSE")

    def test_rechaza_lo_que_no_sea_ascii(self):
        """El firmware compara con String de Arduino: un carácter de 2 bytes
        rompe la comparación en silencio."""
        with self.assertRaises(protocolo.ErrorProtocolo):
            protocolo.construir("SERVO", "45º")

    def test_los_bytes_terminan_en_un_solo_LF(self):
        crudo = protocolo.codificar("MOVE", "FWD")
        self.assertEqual(crudo, b"MOVE,FWD\n")
        self.assertFalse(crudo.endswith(b"\r\n"))

    def test_el_nombre_se_normaliza_a_mayusculas(self):
        self.assertEqual(protocolo.construir("move", "FWD"), "MOVE,FWD")


# =============================================================================
class PruebasAnalisisRespuesta(unittest.TestCase):
    """Lo que el lado Python NO hacia: mirar lo que contesta el Arduino."""

    def test_ok_se_reconoce(self):
        r = protocolo.analizar("MOVE", ["OK,MOVE,FWD"])
        self.assertTrue(r.ok)
        self.assertIsNone(r.error)

    def test_err_se_detecta(self):
        """El caso real: el firmware antiguo no conocía VEL."""
        r = protocolo.analizar("VEL", ["ERR,VEL,231"])
        self.assertFalse(r.ok)
        self.assertIn("ERR,VEL,231", r.error)

    def test_sin_respuesta_se_distingue_de_un_error(self):
        r = protocolo.analizar("PING", [])
        self.assertFalse(r.ok)
        self.assertTrue(r.sin_respuesta)

    def test_una_respuesta_que_no_es_la_esperada_no_cuela(self):
        """Si llega el ACK de OTRO comando, no debe darse por bueno."""
        r = protocolo.analizar("VEL", ["POS,3"])
        self.assertFalse(r.ok)
        self.assertIn("OK,VEL", r.error)

    def test_los_datos_se_extraen(self):
        r = protocolo.analizar("GETPOS", ["POS,5"])
        self.assertTrue(r.ok)
        self.assertEqual(r.datos, ("POS", ["5"]))

    def test_los_enteros_de_los_encoders(self):
        self.assertEqual(protocolo.enteros("ENC,100,-200,0,300"),
                         [100, -200, 0, 300])
        self.assertIsNone(protocolo.enteros("ENC,a,b,c,d"))

    def test_se_toleran_CR_y_espacios(self):
        r = protocolo.analizar("MOVE", ["  OK,MOVE,FWD\r\n"])
        self.assertTrue(r.ok)


# =============================================================================
class PruebasSeguridad(unittest.TestCase):
    """STOP no se puede perder: si se pierde, el robot sigue andando."""

    def test_stop_es_critico(self):
        self.assertTrue(protocolo.es_critico("MOVE,STOP"))

    def test_la_velocidad_es_critica(self):
        self.assertTrue(protocolo.es_critico("VEL,255"))

    def test_avanzar_no_es_critico(self):
        """Un FWD perdido se corrige solo con el siguiente; un STOP no."""
        self.assertFalse(protocolo.es_critico("MOVE,FWD"))
        self.assertFalse(protocolo.es_critico("PWM,18,7.5"))


# =============================================================================
class PruebasArduinoFalso(unittest.TestCase):
    """El simulador debe comportarse como el firmware (y como el antiguo)."""

    def setUp(self):
        self.ard = ArduinoFalso()

    def test_los_giros_mueven_de_verdad(self):
        self.assertEqual(self.ard.procesar("MOVE,SPINL"), ["OK,MOVE,SPINL"])
        self.assertEqual(self.ard.movimiento, protocolo.GIRO_IZQ)

    def test_la_velocidad_se_aplica_y_se_recorta(self):
        self.assertEqual(self.ard.procesar("VEL,231"), ["OK,VEL,231"])
        self.assertEqual(self.ard.velocidad, 231)
        self.ard.procesar("VEL,999")
        self.assertEqual(self.ard.velocidad, protocolo.VEL_MAX)

    def test_un_comando_desconocido_da_ERR(self):
        self.assertEqual(self.ard.procesar("BAILAR,3"), ["ERR,BAILAR,3"])

    def test_el_firmware_ANTIGUO_reproduce_el_fallo(self):
        """Demuestra el problema original: el robot se PARA y contesta OK."""
        viejo = ArduinoFalso(antiguo=True)
        self.assertEqual(viejo.procesar("MOVE,SPINL"), ["OK,MOVE,SPINL"])
        self.assertEqual(viejo.movimiento, protocolo.PARADO,
                         "El firmware antiguo debía pararse ante SPINL")
        self.assertTrue(viejo.procesar("VEL,231")[0].startswith("ERR"))
        self.assertTrue(viejo.procesar("TRUCO,1")[0].startswith("ERR"))

    def test_con_el_firmware_antiguo_el_analisis_LO_DETECTA(self):
        """Y con la corrección, ese caso ya no pasa desapercibido."""
        viejo = ArduinoFalso(antiguo=True)
        r = protocolo.analizar("VEL", viejo.procesar("VEL,231"))
        self.assertFalse(r.ok)
        self.assertIn("rechazo", r.error)

    def test_dispensar_responde_en_orden(self):
        lineas = self.ard.procesar("DISPENSE,3")
        self.assertEqual(lineas[0], "OK,DISPENSE,3")
        self.assertIn("DISPENSADO,3", lineas)

    def test_una_linea_demasiado_larga_se_rechaza(self):
        largo = "SERVO," + "9" * (protocolo.LINEA_MAX + 10)
        self.assertEqual(self.ard.procesar(largo), ["ERR,LINEA_DEMASIADO_LARGA"])


# =============================================================================
@unittest.skipUnless(PUEDE_IDA_Y_VUELTA, "necesita pyserial y openpty (Unix)")
class PruebasIdaYVuelta(unittest.TestCase):
    """Ida y vuelta por un puerto serie DE VERDAD (pseudoterminal).

    Es la prueba que responde a "¿de verdad llega y de verdad vuelve?": se
    escribe con pyserial en un dispositivo real y se lee la respuesta."""

    def setUp(self):
        self.ard = ArduinoFalso(retardo_truco=0.02, retardo_dispense=0.02)
        self.puerto = PuertoSimulado(self.ard)
        dispositivo = self.puerto.arrancar()
        import serial as pyserial
        self.con = pyserial.Serial(dispositivo, protocolo.BAUDIOS, timeout=1.0)
        time.sleep(0.15)
        self.con.reset_input_buffer()

    def tearDown(self):
        try:
            self.con.close()
        finally:
            self.puerto.parar()

    def _intercambiar(self, linea, hasta=None, espera=1.5):
        """Escribe un comando y recoge respuesta, como hace el hub."""
        self.con.write((linea + protocolo.TERMINADOR).encode(protocolo.CODIFICACION))
        self.con.flush()
        hasta = hasta or protocolo.hasta_de(protocolo.nombre_de(linea))
        lineas, t0 = [], time.time()
        while time.time() - t0 < espera:
            cruda = self.con.readline().decode(protocolo.CODIFICACION,
                                               errors="replace").strip()
            if not cruda:
                continue
            lineas.append(cruda)
            if any(cruda.startswith(u) for u in hasta):
                break
        return lineas

    def test_mover_va_y_vuelve(self):
        r = protocolo.analizar("MOVE", self._intercambiar(protocolo.cmd_mover("FWD")))
        self.assertTrue(r.ok, r.error)
        self.assertEqual(self.ard.movimiento, protocolo.ADELANTE)

    def test_los_seis_movimientos_van_y_vuelven(self):
        """Incluidos SPINL y SPINR: el caso que estaba roto."""
        esperado = {
            protocolo.ADELANTE: protocolo.ADELANTE, protocolo.ATRAS: protocolo.ATRAS,
            protocolo.IZQUIERDA: protocolo.IZQUIERDA, protocolo.DERECHA: protocolo.DERECHA,
            protocolo.GIRO_IZQ: protocolo.GIRO_IZQ, protocolo.GIRO_DER: protocolo.GIRO_DER,
            protocolo.PARADO: protocolo.PARADO,
        }
        for mov, efecto in esperado.items():
            with self.subTest(movimiento=mov):
                r = protocolo.analizar("MOVE",
                                       self._intercambiar(protocolo.cmd_mover(mov)))
                self.assertTrue(r.ok, f"{mov}: {r.error}")
                self.assertEqual(self.ard.movimiento, efecto,
                                 f"{mov} no produjo el movimiento esperado")

    def test_la_velocidad_llega_al_arduino(self):
        r = protocolo.analizar("VEL", self._intercambiar(protocolo.cmd_velocidad(231)))
        self.assertTrue(r.ok, r.error)
        self.assertEqual(self.ard.velocidad, 231)

    def test_el_truco_confirma_el_final(self):
        r = protocolo.analizar("TRUCO", self._intercambiar(protocolo.cmd_truco(2)))
        self.assertTrue(r.ok, r.error)
        self.assertIn(2, self.ard.trucos)

    def test_ping_pong(self):
        r = protocolo.analizar("PING", self._intercambiar("PING"))
        self.assertTrue(r.ok, r.error)

    def test_la_version_de_protocolo_va_y_vuelve(self):
        r = protocolo.analizar("PROTO", self._intercambiar("PROTO"))
        self.assertTrue(r.ok, r.error)
        self.assertEqual(int(r.datos[1][0]), protocolo.VERSION_PROTOCOLO)

    def test_muchos_comandos_seguidos_no_se_mezclan(self):
        """Ráfaga de comandos: cada respuesta debe corresponder al suyo.

        Es el caso que rompía la sincronización: sin correlación, el ACK de un
        comando se atribuía al siguiente."""
        for i, mov in enumerate(protocolo.MOVIMIENTOS * 3):
            with self.subTest(n=i, mov=mov):
                lineas = self._intercambiar(protocolo.cmd_mover(mov), espera=1.0)
                self.assertTrue(lineas, f"sin respuesta en el envío {i}")
                self.assertTrue(lineas[-1].endswith(mov),
                                f"la respuesta {lineas[-1]!r} no corresponde a {mov}")

    def test_los_encoders_devuelven_cuatro_numeros(self):
        r = protocolo.analizar("ENC", self._intercambiar("ENC"))
        self.assertTrue(r.ok, r.error)
        self.assertEqual(len(protocolo.enteros(r.lineas[-1], 4)), 4)

    def test_dispensar_completo(self):
        lineas = self._intercambiar("DISPENSE,3", espera=3.0)
        r = protocolo.analizar("DISPENSE", lineas)
        self.assertTrue(r.ok, r.error)
        self.assertTrue(any(x.startswith("DISPENSADO") for x in lineas))

    def test_un_comando_desconocido_devuelve_ERR_y_se_detecta(self):
        lineas = self._intercambiar("BAILAR,3", hasta=["ERR"])
        self.assertTrue(lineas[0].startswith("ERR"))

    def test_el_saludo_de_arranque_llega(self):
        """READY permite saber que el Arduino se reinició (bajón de tensión).

        Se emite DESPUÉS de abrir el puerto, que es lo que pasa de verdad: el
        Arduino se reinicia al abrirse el puerto y saluda entonces."""
        self.puerto.emitir_saludo()
        t0 = time.time()
        linea = ""
        while time.time() - t0 < 2.0:
            linea = self.con.readline().decode(protocolo.CODIFICACION,
                                               errors="replace").strip()
            if linea:
                break
        self.assertTrue(linea.startswith("READY,MEDIBOT"), repr(linea))
        self.assertEqual(int(linea.split(",")[2]), protocolo.VERSION_PROTOCOLO)


# =============================================================================
@unittest.skipUnless(PUEDE_IDA_Y_VUELTA, "necesita pyserial y openpty (Unix)")
class PruebasHubCompleto(unittest.TestCase):
    """Cadena completa: cliente -> hub (TCP) -> puerto serie -> Arduino.

    Prueba el serial_hub REAL, no una imitación."""

    @classmethod
    def setUpClass(cls):
        cls.ard = ArduinoFalso(retardo_truco=0.02, retardo_dispense=0.02)
        cls.puerto = PuertoSimulado(cls.ard)
        dispositivo = cls.puerto.arrancar()

        os.environ["MEDIBOT_SERIAL_PORT"] = dispositivo
        import importlib
        import serial_hub
        importlib.reload(serial_hub)          # releer el puerto del entorno
        cls.hub = serial_hub
        cls.hub.PORT = 5099                   # no chocar con un hub real
        serial_hub.serial_connect()
        cls.hilo = threading.Thread(target=serial_hub.main, daemon=True)
        cls.hilo.start()
        time.sleep(1.0)

    @classmethod
    def tearDownClass(cls):
        cls.puerto.parar()
        os.environ.pop("MEDIBOT_SERIAL_PORT", None)

    def _pedir(self, payload, timeout=8.0):
        import json
        import socket as sk
        with sk.create_connection(("127.0.0.1", 5099), timeout=3.0) as s:
            s.sendall((json.dumps(payload) + "\n").encode())
            s.settimeout(timeout)
            buf = b""
            while b"\n" not in buf:
                trozo = s.recv(4096)
                if not trozo:
                    break
                buf += trozo
        return json.loads(buf.split(b"\n", 1)[0].decode())

    def test_el_hub_entrega_un_movimiento(self):
        r = self._pedir({"cmd": "MOVE,SPINR", "wait": 1.0, "until": ["OK,MOVE", "ERR"]})
        self.assertTrue(r["ok"])
        self.assertEqual(self.ard.movimiento, protocolo.GIRO_DER)

    def test_el_hub_entrega_la_velocidad(self):
        r = self._pedir({"cmd": "VEL,244", "wait": 1.0, "until": ["OK,VEL", "ERR"]})
        self.assertTrue(r["ok"])
        self.assertEqual(self.ard.velocidad, 244)

    def test_el_hub_informa_de_la_version_de_protocolo(self):
        r = self._pedir({"op": "status"})
        self.assertEqual(r["protocolo"], protocolo.VERSION_PROTOCOLO)


if __name__ == "__main__":
    print(f"Protocolo v{protocolo.VERSION_PROTOCOLO} | "
          f"pyserial={'si' if HAY_PYSERIAL else 'NO'} | "
          f"pty={'si' if HAY_PTY else 'NO'} | Python {sys.version.split()[0]}")
    print("Sin Arduino fisico: todo con un puerto serie simulado.\n")
    unittest.main(verbosity=2)
