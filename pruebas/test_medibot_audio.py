#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pruebas del audio de Medibot  -  SIN tarjeta de sonido
======================================================
Todo lo comprobable sin hardware: la cabecera WAV (que se valida con el
modulo `wave` de la libreria estandar, no a ojo), la deteccion de microfonos
a partir de la salida real de `arecord -l`, la orden que se ejecuta, y que
cuando falta algo se diga POR QUE en vez de fallar en silencio.

    python3 pruebas/test_medibot_audio.py
"""

import io
import os
import struct
import sys
import threading
import time
import unittest
import wave

# Las pruebas viven en pruebas/ y el codigo en medibot/ y herramientas/.
# Python solo mira la carpeta del script, asi que hay que anadir las otras.
_RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(_RAIZ, "medibot"), os.path.join(_RAIZ, "herramientas")]

import medibot_audio as ma


class PruebasCabeceraWAV(unittest.TestCase):
    """La cabecera es lo unico que separa 'suena' de 'no suena'."""

    def test_un_wav_de_tamano_conocido_lo_lee_el_modulo_wave(self):
        """LA prueba de verdad: que un WAV real la acepte, no que 'parezca'."""
        pcm = b"\x00\x01" * 800                       # 1600 bytes de PCM
        datos = ma.cabecera_wav(16000, 1, 16, len(pcm)) + pcm
        with wave.open(io.BytesIO(datos), "rb") as w:
            self.assertEqual(w.getnchannels(), 1)
            self.assertEqual(w.getframerate(), 16000)
            self.assertEqual(w.getsampwidth(), 2)
            self.assertEqual(w.getnframes(), 800)
            self.assertEqual(w.readframes(800), pcm)

    def test_estereo_a_44100_tambien(self):
        pcm = b"\x00\x01\x02\x03" * 100
        datos = ma.cabecera_wav(44100, 2, 16, len(pcm)) + pcm
        with wave.open(io.BytesIO(datos), "rb") as w:
            self.assertEqual(w.getnchannels(), 2)
            self.assertEqual(w.getframerate(), 44100)
            self.assertEqual(w.getnframes(), 100)

    def test_la_cabecera_mide_44_bytes(self):
        self.assertEqual(len(ma.cabecera_wav()), 44)

    def test_empieza_por_RIFF_y_lleva_WAVE(self):
        c = ma.cabecera_wav()
        self.assertTrue(c.startswith(b"RIFF"))
        self.assertEqual(c[8:12], b"WAVE")
        self.assertEqual(c[12:16], b"fmt ")
        self.assertEqual(c[36:40], b"data")

    def test_en_directo_declara_el_maximo(self):
        """Sin saber cuanto dura, se declara 4 GiB: el navegador lo trata como
        una emision continua en vez de cortar al primer trozo."""
        c = ma.cabecera_wav(bytes_de_audio=None)
        self.assertEqual(struct.unpack("<I", c[4:8])[0], ma.TAM_STREAMING)
        self.assertEqual(struct.unpack("<I", c[40:44])[0], ma.TAM_STREAMING)

    def test_la_tasa_de_bytes_cuadra(self):
        """16 kHz mono 16 bits = 32000 bytes/s. Si esto se descuadra, el audio
        se oye acelerado o a camara lenta."""
        c = ma.cabecera_wav(16000, 1, 16, 0)
        tasa, alineacion = struct.unpack("<IH", c[28:34])
        self.assertEqual(tasa, 32000)
        self.assertEqual(alineacion, 2)

    def test_es_PCM_sin_comprimir(self):
        self.assertEqual(struct.unpack("<H", ma.cabecera_wav()[20:22])[0], 1)

    def test_parametros_absurdos_se_rechazan(self):
        for kwargs in ({"canales": 0}, {"bits": 12}):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    ma.cabecera_wav(**kwargs)


SALIDA_ARECORD = """**** List of CAPTURE Hardware Devices ****
card 1: U0x46d0x825 [USB Device 0x46d:0x825], device 0: USB Audio [USB Audio]
  Subdevices: 1/1
  Subdevice #0: subdevice #0
card 2: Micro [Otro Micro], device 0: USB Audio [USB Audio]
  Subdevices: 1/1
"""

SALIDA_EN_ESPANOL = """**** Lista de dispositivos CAPTURE Hardware ****
tarjeta 1: U0x46d0x825 [USB Device 0x46d:0x825], dispositivo 0: USB Audio [USB Audio]
  Subdispositivos: 1/1
"""


class PruebasDispositivos(unittest.TestCase):

    def test_lee_la_salida_de_arecord(self):
        d = ma.dispositivos(SALIDA_ARECORD)
        self.assertEqual(len(d), 2)
        self.assertEqual(d[0]["id"], "plughw:1,0")
        self.assertIn("USB Device", d[0]["nombre"])

    def test_tambien_con_arecord_en_espanol(self):
        """En una Pi con el idioma en espanol, arecord traduce la salida."""
        d = ma.dispositivos(SALIDA_EN_ESPANOL)
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0]["id"], "plughw:1,0")

    def test_sin_microfonos_devuelve_lista_vacia(self):
        self.assertEqual(ma.dispositivos("no hay nada aqui"), [])
        self.assertEqual(ma.dispositivos(""), [])

    def test_prefiere_el_dispositivo_USB(self):
        """En una Raspberry la tarjeta 0 suele ser la salida HDMI, que no
        graba: coger 'el primero' a ciegas daria silencio."""
        lista = [{"id": "plughw:0,0", "nombre": "bcm2835 HDMI"},
                 {"id": "plughw:1,0", "nombre": "USB Device 0x46d:0x825"}]
        entorno = os.environ.pop("MEDIBOT_AUDIO_DISPOSITIVO", None)
        try:
            self.assertEqual(ma.elegir_dispositivo(lista), "plughw:1,0")
        finally:
            if entorno is not None:
                os.environ["MEDIBOT_AUDIO_DISPOSITIVO"] = entorno

    def test_la_variable_de_entorno_manda(self):
        os.environ["MEDIBOT_AUDIO_DISPOSITIVO"] = "plughw:9,9"
        try:
            self.assertEqual(ma.elegir_dispositivo([{"id": "plughw:1,0",
                                                     "nombre": "USB"}]),
                             "plughw:9,9")
        finally:
            os.environ.pop("MEDIBOT_AUDIO_DISPOSITIVO", None)

    def test_sin_nada_devuelve_None(self):
        entorno = os.environ.pop("MEDIBOT_AUDIO_DISPOSITIVO", None)
        try:
            self.assertIsNone(ma.elegir_dispositivo([]))
        finally:
            if entorno is not None:
                os.environ["MEDIBOT_AUDIO_DISPOSITIVO"] = entorno


class PruebasMicrofono(unittest.TestCase):

    def setUp(self):
        self.previo = {k: os.environ.get(k) for k in
                       ("MEDIBOT_AUDIO", "MEDIBOT_AUDIO_DISPOSITIVO",
                        "MEDIBOT_AUDIO_HZ", "MEDIBOT_AUDIO_CANALES")}
        for k in self.previo:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self.previo.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v

    def test_por_defecto_esta_ACTIVADO(self):
        """Sin poner ninguna variable, el audio esta disponible: solo hace
        falta arecord y un microfono conectado."""
        original = ma.hay_arecord
        ma.hay_arecord = lambda: True          # aqui no hay tarjeta de sonido
        try:
            m = ma.Microfono(dispositivo="plughw:1,0")
            self.assertTrue(m.disponible(),
                            "el audio tiene que venir activado de fabrica")
            self.assertEqual(m.motivo_no_disponible(), "")
        finally:
            ma.hay_arecord = original

    def test_se_puede_apagar_a_mano(self):
        """MEDIBOT_AUDIO=0 lo apaga, y el motivo dice donde esta apagado (no
        'pon MEDIBOT_AUDIO=1', que es justo lo que ya viene puesto)."""
        original = ma.hay_arecord
        ma.hay_arecord = lambda: True
        try:
            for apagado in ("0", "false", "no", "off"):
                os.environ["MEDIBOT_AUDIO"] = apagado
                m = ma.Microfono(dispositivo="plughw:1,0")
                self.assertFalse(m.disponible(), f"{apagado} deberia apagarlo")
                self.assertIn("apagado a mano", m.motivo_no_disponible())
                self.assertIn(apagado, m.motivo_no_disponible(),
                              "el motivo tiene que decir el valor puesto")
        finally:
            ma.hay_arecord = original

    def test_no_graba_solo_aunque_venga_activado(self):
        """Activado significa que el boton funciona, NO que haya un arecord
        corriendo: el microfono no se abre hasta que alguien escucha."""
        original = ma.hay_arecord
        ma.hay_arecord = lambda: True
        try:
            m = ma.Microfono(dispositivo="plughw:1,0")
            self.assertTrue(m.disponible())
            self.assertIsNone(m.proceso,
                              "no puede haber captura sin que nadie escuche")
            self.assertEqual(m.estado()["oyentes"], 0)
        finally:
            ma.hay_arecord = original

    def test_la_orden_lleva_los_parametros_correctos(self):
        os.environ["MEDIBOT_AUDIO_HZ"] = "22050"
        os.environ["MEDIBOT_AUDIO_CANALES"] = "2"
        m = ma.Microfono(dispositivo="plughw:1,0")
        orden = m.orden()
        self.assertEqual(orden[0], "arecord")
        self.assertIn("plughw:1,0", orden)
        self.assertIn("22050", orden)
        self.assertIn("S16_LE", orden)
        self.assertIn("raw", orden,
                      "Debe pedirse PCM en crudo: la cabecera la ponemos aqui")

    def test_sin_arecord_dice_como_instalarlo(self):
        original = ma.hay_arecord
        ma.hay_arecord = lambda: False
        try:
            m = ma.Microfono(dispositivo="plughw:1,0")
            self.assertFalse(m.disponible())
            self.assertIn("alsa-utils", m.motivo_no_disponible())
        finally:
            ma.hay_arecord = original

    def test_sin_dispositivo_dice_como_buscarlo(self):
        original = ma.hay_arecord
        ma.hay_arecord = lambda: True          # aqui no hay tarjeta de sonido
        try:
            m = ma.Microfono(dispositivo="x")
            m.dispositivo = None
            self.assertFalse(m.disponible())
            self.assertIn("arecord -l", m.motivo_no_disponible())
        finally:
            ma.hay_arecord = original

    def test_el_estado_no_miente_sobre_la_disponibilidad(self):
        m = ma.Microfono(dispositivo="plughw:1,0")
        e = m.estado()
        self.assertEqual(e["disponible"], m.disponible())
        self.assertEqual(e["dispositivo"], "plughw:1,0")
        self.assertTrue(e["motivo"] or e["disponible"])

    def test_cerrar_sin_haber_abierto_no_revienta(self):
        ma.Microfono(dispositivo="plughw:1,0").cerrar()   # no debe lanzar

    def test_los_trozos_empiezan_por_la_cabecera(self):
        """Se sustituye arecord por un doble: se comprueba el flujo sin
        tarjeta de sonido."""
        class ProcesoFalso:
            def __init__(self):
                self.stdout = io.BytesIO(b"\x01\x02" * 4000)
                self.stderr = io.BytesIO()
                self.terminado = False

            def terminate(self):
                self.terminado = True

            def wait(self, timeout=None):
                return 0

        m = ma.Microfono(dispositivo="plughw:1,0")
        falso = ProcesoFalso()
        m.abrir = lambda: setattr(m, "proceso", falso)

        trozos = list(m.trozos())
        self.assertTrue(trozos[0].startswith(b"RIFF"))
        self.assertEqual(len(trozos[0]), 44)
        self.assertEqual(b"".join(trozos[1:]), b"\x01\x02" * 4000)
        self.assertTrue(falso.terminado,
                        "Hay que matar arecord al cortar: si no, se queda "
                        "agarrado al microfono y a la segunda no se oye nada")

    def test_se_cierra_aunque_la_captura_falle(self):
        """Si arecord truena, la peticion TERMINA (no se queda colgada) y el
        microfono se suelta. El motivo se guarda para poder explicarlo."""
        class ProcesoQueFalla:
            def __init__(self):
                self.stdout = self
                self.stderr = None
                self.terminado = False

            def read(self, n):
                raise OSError("el cliente colgo")

            def terminate(self):
                self.terminado = True

            def wait(self, timeout=None):
                return 0

        m = ma.Microfono(dispositivo="plughw:1,0")
        falso = ProcesoQueFalla()
        m.abrir = lambda: setattr(m, "proceso", falso)
        #  No debe colgarse esperando audio que ya no va a llegar.
        self.assertEqual(len(list(m.trozos())), 1)   # solo la cabecera
        self.assertTrue(falso.terminado, "arecord debe morir igualmente")
        self.assertIn("el cliente colgo", m.estado()["ultimo_fallo"],
                      "el motivo del corte tiene que llegar a la web")

    def test_el_motivo_de_arecord_llega_a_la_web(self):
        """Un microfono ocupado o una camara desenchufada tienen que salir
        con el error de ALSA, no como un 'no se pudo escuchar' pelado."""
        class ProcesoQueMuere:
            def __init__(self):
                self.stdout = io.BytesIO(b"")        # muere sin dar audio
                self.stderr = io.BytesIO(
                    b"arecord: main:830: audio open error: "
                    b"Device or resource busy\n")

            def terminate(self):
                pass

            def wait(self, timeout=None):
                return 1

        m = ma.Microfono(dispositivo="plughw:1,0")
        m.abrir = lambda: setattr(m, "proceso", ProcesoQueMuere())
        list(m.trozos())
        self.assertIn("Device or resource busy", m.estado()["ultimo_fallo"])
        #  Y en una Pi de verdad (audio activado y arecord instalado) ESE es
        #  el motivo que se ensena en la web al pulsar Escuchar.
        hay_arecord_real = ma.hay_arecord
        ma.hay_arecord = lambda: True
        try:
            self.assertTrue(m.disponible())
            self.assertIn("Device or resource busy", m.estado()["motivo"])
        finally:
            ma.hay_arecord = hay_arecord_real


class PruebasVariosOyentes(unittest.TestCase):
    """Dos navegadores a la vez: el movil y el PC, o una recarga de pagina
    (el navegador abre la peticion nueva antes de soltar la vieja).

    Antes las dos peticiones leian del MISMO tubo de arecord, y eso daba tres
    fallos: cada una se llevaba solo parte de las muestras (audio a saltos),
    al cerrar una se mataba la captura de la otra, y al que se quedaba le
    reventaba el generador. Estas pruebas fijan ese comportamiento.
    """

    class Grifo:
        """Doble de arecord que entrega audio SOLO cuando se le pide.

        Un doble que suelte audio a toda velocidad haria las pruebas una
        loteria (segun quien llegue antes se pierde un trozo o no). Con un
        semaforo, la prueba decide exactamente cuando entra sonido y cuanto,
        y el resultado es siempre el mismo."""

        TAM = 4096

        def __init__(self):
            self.stdout = self
            self.stderr = io.BytesIO()
            self.permisos = threading.Semaphore(0)
            self.entregados = 0
            self.terminado = False
            self._fin = False

        def dejar_pasar(self, cuantos=1):
            for _ in range(cuantos):
                self.permisos.release()

        def cortar(self):
            """Como una camara desenchufada: se acaba el audio."""
            self._fin = True
            self.permisos.release()

        def trozo(self, n):
            """Trozo numero n, reconocible: relleno con el byte n."""
            return bytes([n % 251 + 1]) * self.TAM

        def read(self, n):
            self.permisos.acquire()
            if self._fin or self.terminado:
                return b""
            self.entregados += 1
            return self.trozo(self.entregados)

        def terminate(self):
            self.terminado = True
            self.permisos.release()      # desbloquear al hilo de reparto

        def wait(self, timeout=None):
            return 0

    def _microfono(self):
        grifo = self.Grifo()
        m = ma.Microfono(dispositivo="plughw:1,0")
        m.abrir = lambda: setattr(m, "proceso", grifo)
        return m, grifo

    def _esperar(self, condicion, mensaje, limite=5.0):
        t0 = time.time()
        while time.time() - t0 < limite:
            if condicion():
                return
            time.sleep(0.01)
        self.fail(mensaje)

    def test_cada_oyente_recibe_TODO_el_audio(self):
        """El fallo que se oia: con dos oyentes, cada uno recibia la mitad de
        las muestras y los dos oian el audio troceado."""
        m, grifo = self._microfono()
        a, b = m.trozos(), m.trozos()
        self.assertTrue(next(a).startswith(b"RIFF"))   # suscrito
        self.assertTrue(next(b).startswith(b"RIFF"))   # suscrito
        self.assertEqual(m.estado()["oyentes"], 2)

        grifo.dejar_pasar(8)
        esperado = [grifo.trozo(i) for i in range(1, 9)]
        self.assertEqual([next(a) for _ in range(8)], esperado,
                         "al oyente A le falta audio")
        self.assertEqual([next(b) for _ in range(8)], esperado,
                         "al oyente B le falta audio (¿se lo reparten?)")
        a.close(); b.close()

    def test_una_sola_captura_para_los_dos(self):
        """ALSA no deja abrir plughw: dos veces: el segundo arecord moriria
        con 'Device or resource busy'."""
        m, grifo = self._microfono()
        abiertas = []
        abrir_real = m.abrir
        m.abrir = lambda: (abiertas.append(1), abrir_real())
        oyentes = [m.trozos() for _ in range(3)]
        for o in oyentes:
            next(o)
        self.assertEqual(sum(abiertas), 1,
                         "solo el primer oyente abre el microfono")
        self.assertEqual(m.estado()["oyentes"], 3)
        for o in oyentes:
            o.close()

    def test_que_se_vaya_uno_no_corta_al_otro(self):
        """El fallo de antes: al cerrar una pestana se mataba la captura
        compartida, al otro se le cortaba el sonido y su peticion reventaba
        con AttributeError."""
        m, grifo = self._microfono()
        queda = m.trozos()
        next(queda)
        grifo.dejar_pasar(2)
        self.assertEqual(next(queda), grifo.trozo(1))
        self.assertEqual(next(queda), grifo.trozo(2))

        de_paso = m.trozos()          # el segundo entra...
        next(de_paso)
        de_paso.close()               # ...y cierra la pestana enseguida

        self.assertFalse(grifo.terminado,
                         "no hay que soltar el microfono: queda un oyente")
        grifo.dejar_pasar(2)
        self.assertEqual(next(queda), grifo.trozo(3),
                         "al irse el otro oyente se corto el audio")
        self.assertEqual(next(queda), grifo.trozo(4))
        queda.close()

    def test_el_microfono_se_suelta_al_irse_el_ULTIMO(self):
        m, grifo = self._microfono()
        uno, dos = m.trozos(), m.trozos()
        next(uno); next(dos)
        self.assertEqual(m.estado()["oyentes"], 2)

        uno.close()
        self.assertFalse(grifo.terminado, "aun queda uno escuchando")
        self.assertEqual(m.estado()["oyentes"], 1)

        dos.close()
        self.assertTrue(grifo.terminado,
                        "al irse el ultimo hay que soltar el microfono")
        self.assertEqual(m.estado()["oyentes"], 0)
        self.assertIsNone(m.proceso, "no puede quedar un arecord colgando")

    def test_una_recarga_de_pagina_vuelve_a_abrir_el_microfono(self):
        """Tras irse todos, el siguiente que llegue tiene que abrir una
        captura NUEVA y no engancharse a la que acaba de morir."""
        m, primer_grifo = self._microfono()
        uno = m.trozos(); next(uno); uno.close()
        self.assertTrue(primer_grifo.terminado)

        segundo_grifo = self.Grifo()
        m.abrir = lambda: setattr(m, "proceso", segundo_grifo)
        dos = m.trozos(); next(dos)
        segundo_grifo.dejar_pasar(1)
        self.assertEqual(next(dos), segundo_grifo.trozo(1),
                         "la segunda escucha no suena")
        dos.close()

    def test_un_oyente_lento_no_atasca_a_los_demas(self):
        """Un movil con mala cobertura no puede congelar al PC ni llenar el
        tubo de arecord: se le tiran los trozos viejos y se sigue."""
        m, grifo = self._microfono()
        lento, rapido = m.trozos(), m.trozos()
        next(lento); next(rapido)          # los dos suscritos
        total = ma.COLA_MAXIMA + 5         # mas de lo que cabe en una cola

        #  El oyente rapido consume a la vez que entra el audio, como un
        #  navegador de verdad; el lento no consume NADA.
        recibido = []
        listo = threading.Event()

        def consumir():
            for _ in range(total):
                recibido.append(next(rapido))
            listo.set()

        hilo = threading.Thread(target=consumir, daemon=True)
        hilo.start()
        #  Trozo a trozo, esperando a que el rapido lo recoja: arecord
        #  entrega el audio a 32 kB/s, no de golpe, y asi la prueba mide lo
        #  que interesa (que el lento no atasca) y no una carrera.
        for i in range(1, total + 1):
            grifo.dejar_pasar(1)
            self._esperar(lambda i=i: len(recibido) >= i,
                          f"el oyente rapido se quedo en el trozo {i}")

        self.assertTrue(listo.wait(timeout=10),
                        f"el oyente lento atasco al rapido: solo le llegaron "
                        f"{len(recibido)} de {total} trozos")
        self.assertEqual(recibido, [grifo.trozo(i) for i in range(1, total + 1)],
                         "el oyente rapido tiene que recibirlo todo igual")
        self.assertEqual(grifo.entregados, total,
                         "la captura no puede quedarse parada por el lento")

        #  Al lento se le tiraron los trozos mas viejos (audio de hace dos
        #  segundos, que ya no sirve), pero sigue recibiendo lo ULTIMO.
        suyos = [next(lento) for _ in range(ma.COLA_MAXIMA)]
        self.assertEqual(suyos[-1], grifo.trozo(total),
                         "el oyente lento tiene que recibir el audio reciente")
        self.assertNotIn(grifo.trozo(1), suyos,
                         "al lento se le tiran los trozos viejos, no los nuevos")
        lento.close(); rapido.close()

    def test_si_se_corta_la_captura_se_enteran_todos(self):
        """Camara desenchufada: las peticiones tienen que TERMINAR, no
        quedarse colgadas esperando audio que ya no llega."""
        m, grifo = self._microfono()
        a, b = m.trozos(), m.trozos()
        next(a); next(b)
        grifo.cortar()
        self.assertEqual(list(a), [], "la peticion de A no termino")
        self.assertEqual(list(b), [], "la peticion de B no termino")
        self.assertEqual(m.estado()["oyentes"], 0)
        self.assertIsNone(m.proceso)


if __name__ == "__main__":
    unittest.main(verbosity=2)
