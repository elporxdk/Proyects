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
            def __init__(self, audio):
                self.stdout = io.BytesIO(audio)
                self.stderr = io.BytesIO()
                self.terminado = False

            def terminate(self):
                self.terminado = True

            def wait(self, timeout=None):
                return 0

        m = ma.Microfono(dispositivo="plughw:1,0")
        #  Que quepa en el colchon del oyente: de golpe, sin ritmo real, un
        #  audio mas largo que el colchon se recorta a proposito (ver
        #  COLCHON_SEGUNDOS). Eso se prueba aparte; aqui interesa el formato.
        audio = b"\x01\x02" * (ma.TROZO * (ma.COLA_MAXIMA - 1) // 2)
        falso = ProcesoFalso(audio)
        m.abrir = lambda: setattr(m, "proceso", falso)

        trozos = list(m.trozos())
        self.assertTrue(trozos[0].startswith(b"RIFF"))
        self.assertEqual(len(trozos[0]), 44)
        self.assertEqual(b"".join(trozos[1:]), audio)
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

        TAM = ma.TROZO

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

        #  Lo que quepa en el colchon: mas de eso se recorta a proposito.
        cuantos = ma.COLA_MAXIMA - 1
        grifo.dejar_pasar(cuantos)
        esperado = [grifo.trozo(i) for i in range(1, cuantos + 1)]
        self.assertEqual([next(a) for _ in range(cuantos)], esperado,
                         "al oyente A le falta audio")
        self.assertEqual([next(b) for _ in range(cuantos)], esperado,
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


class PruebasRetraso(unittest.TestCase):
    """Que el audio de la camara se oiga AL MOMENTO.

    Iba con mucho retraso y a peor cuanto mas rato llevaba abierto. Eran
    tres cosas sumandose, y cada una tiene aqui su prueba para que no se
    vuelvan a colar sin querer."""

    def test_el_colchon_de_cada_oyente_es_pequeno(self):
        """Era de 2 s: un tiron de wifi lo llenaba, se le soltaban al
        navegador 2 s de golpe y ese retraso se quedaba puesto para siempre,
        sumandose tiron tras tiron."""
        segundos = ma.COLA_MAXIMA * ma.TROZO / 32000.0
        self.assertLessEqual(segundos, 0.4,
                             f"el colchon son {segundos:.2f} s: demasiado "
                             f"retraso si se llena")
        self.assertGreaterEqual(segundos, 0.1,
                                "tan corto que cualquier hipo cortaria el audio")

    def test_el_colchon_se_mide_en_segundos_no_en_trozos(self):
        """Con un numero fijo de trozos, cambiar TROZO movia el retraso sin
        que se notara al leer el codigo."""
        self.assertEqual(ma.cola_maxima(16000, 1, 16), ma.COLA_MAXIMA)

        #  Lo que tiene que salir igual son los SEGUNDOS, no el numero de
        #  trozos: a mas frecuencia, mas trozos para el mismo colchon.
        def segundos(hz, canales=1):
            trozos = ma.cola_maxima(hz, canales, 16)
            return trozos * ma.TROZO / float(hz * canales * 2)

        for hz, canales in ((8000, 1), (16000, 1), (32000, 1), (44100, 2)):
            with self.subTest(hz=hz, canales=canales):
                self.assertAlmostEqual(
                    segundos(hz, canales), ma.COLCHON_SEGUNDOS, delta=0.08,
                    msg=f"a {hz} Hz el colchon se va a "
                        f"{segundos(hz, canales):.2f} s")
        self.assertGreaterEqual(ma.cola_maxima(8000, 1, 16), 2,
                                "nunca menos de dos, o no hay colchon")

    def test_los_trozos_son_cortos(self):
        """4096 bytes son 128 ms que el sonido se pasaba esperando."""
        ms = ma.TROZO / 32000.0 * 1000
        self.assertLessEqual(ms, 40, f"trozos de {ms:.0f} ms: mucha espera")

    def test_se_lee_sin_esperar_a_llenar_el_trozo(self):
        """read() espera a juntar TROZO bytes enteros; read1() devuelve lo
        que haya en cuanto lo hay. La diferencia, medida, eran 60 ms de
        retraso medio."""
        codigo = io.open(ma.__file__, encoding="utf-8").read()
        self.assertIn('getattr(captura.proceso.stdout, "read1", None)', codigo)

    def test_los_dobles_sin_read1_siguen_valiendo(self):
        """El respaldo a read() tiene que funcionar: si no, esta prueba y
        las demas estarian probando otra cosa."""
        class SoloRead:
            def __init__(self):
                self.stdout = io.BytesIO(b"\x07\x08" * 200)
                self.stderr = io.BytesIO()
            def terminate(self): pass
            def wait(self, timeout=None): return 0

        self.assertFalse(hasattr(SoloRead().stdout, "read1_inexistente"))
        m = ma.Microfono(dispositivo="plughw:1,0")
        m.abrir = lambda: setattr(m, "proceso", SoloRead())
        trozos = list(m.trozos())
        self.assertEqual(b"".join(trozos[1:]), b"\x07\x08" * 200)

    def test_arecord_no_se_guarda_medio_segundo_de_audio(self):
        """El buffer de fabrica de ALSA ronda el medio segundo, y eso es
        retraso puro: el sonido ya esta capturado pero no nos lo dan."""
        m = ma.Microfono(dispositivo="plughw:1,0")
        orden = m.orden()
        self.assertIn("--buffer-time", orden)
        buffer_us = int(orden[orden.index("--buffer-time") + 1])
        self.assertLessEqual(buffer_us, 200000,
                             f"{buffer_us/1000:.0f} ms de colchon en ALSA")
        self.assertIn("--period-time", orden)
        periodo_us = int(orden[orden.index("--period-time") + 1])
        self.assertLess(periodo_us, buffer_us,
                        "el periodo tiene que caber varias veces en el buffer")
        self.assertGreaterEqual(periodo_us, 10000,
                                "un periodo minusculo frie la CPU de la Pi")

    def test_se_puede_dejar_el_buffer_de_fabrica(self):
        """Si una Pi muy cargada entrecorta el audio, hay que poder volver
        atras sin tocar el codigo."""
        previo = os.environ.get("MEDIBOT_AUDIO_BUFFER_MS")
        os.environ["MEDIBOT_AUDIO_BUFFER_MS"] = "0"
        try:
            orden = ma.Microfono(dispositivo="plughw:1,0").orden()
            self.assertNotIn("--buffer-time", orden)
        finally:
            os.environ.pop("MEDIBOT_AUDIO_BUFFER_MS", None)
            if previo is not None:
                os.environ["MEDIBOT_AUDIO_BUFFER_MS"] = previo

    def test_y_tambien_subirlo(self):
        previo = os.environ.get("MEDIBOT_AUDIO_BUFFER_MS")
        os.environ["MEDIBOT_AUDIO_BUFFER_MS"] = "300"
        try:
            orden = ma.Microfono(dispositivo="plughw:1,0").orden()
            self.assertEqual(orden[orden.index("--buffer-time") + 1], "300000")
        finally:
            os.environ.pop("MEDIBOT_AUDIO_BUFFER_MS", None)
            if previo is not None:
                os.environ["MEDIBOT_AUDIO_BUFFER_MS"] = previo


class PruebasSoloEnCasa(unittest.TestCase):
    """Hablar, solo desde la red de casa."""

    def test_los_de_casa_pueden(self):
        for ip in ("192.168.1.30", "10.0.0.5", "172.16.4.9", "127.0.0.1",
                   "169.254.3.1"):
            with self.subTest(ip=ip):
                self.assertTrue(ma.es_local(ip, {}))

    def test_los_de_fuera_no(self):
        #  Direcciones publicas de verdad. Ojo: Python considera "privados"
        #  tambien los rangos reservados para documentacion (203.0.113.x y
        #  compania), que no son internet ni son una LAN; como nunca llega
        #  trafico real desde ellos, da igual de que lado caigan.
        for ip in ("8.8.8.8", "1.1.1.1", "93.184.216.34", "140.82.121.4"):
            with self.subTest(ip=ip):
                self.assertFalse(ma.es_local(ip, {}),
                                 f"{ip} es una direccion de internet")

    def test_por_el_tunel_NO_cuenta_como_local(self):
        """LO IMPORTANTE: el tunel corre en la propia Pi, asi que todo lo
        que llega por el se ve como 127.0.0.1. Mirando solo la IP,
        cualquiera de internet pasaria por vecino."""
        for cabecera in ("CF-Connecting-IP", "CF-Ray", "X-Forwarded-For",
                         "X-Real-IP", "Forwarded", "X-Forwarded-Host"):
            with self.subTest(cabecera=cabecera):
                self.assertFalse(ma.es_local("127.0.0.1", {cabecera: "8.8.8.8"}),
                                 f"{cabecera} delata que hubo un salto por fuera")

    def test_una_ip_ilegible_no_cuela(self):
        for ip in ("", None, "no-es-una-ip", "999.1.1.1"):
            with self.subTest(ip=ip):
                self.assertFalse(ma.es_local(ip, {}))

    def test_el_altavoz_lo_aplica(self):
        a = ma.Altavoz(dispositivo="plughw:0,0")
        self.assertTrue(a.solo_lan, "viene cerrado a internet por defecto")
        self.assertEqual(a.permite("192.168.1.30", {}), (True, ""))
        puede, motivo = a.permite("127.0.0.1", {"CF-Connecting-IP": "8.8.8.8"})
        self.assertFalse(puede)
        self.assertIn("red de casa", motivo)
        self.assertIn("MEDIBOT_VOZ_SOLO_LAN=0", motivo,
                      "hay que decir como abrirlo si de verdad se quiere")

    def test_se_puede_abrir_a_proposito(self):
        previo = os.environ.get("MEDIBOT_VOZ_SOLO_LAN")
        os.environ["MEDIBOT_VOZ_SOLO_LAN"] = "0"
        try:
            a = ma.Altavoz(dispositivo="plughw:0,0")
            self.assertFalse(a.solo_lan)
            self.assertEqual(a.permite("8.8.8.8", {}), (True, ""))
            self.assertFalse(a.estado()["solo_lan"])
        finally:
            os.environ.pop("MEDIBOT_VOZ_SOLO_LAN", None)
            if previo is not None:
                os.environ["MEDIBOT_VOZ_SOLO_LAN"] = previo


class PruebasAltavoces(unittest.TestCase):
    """Elegir por donde sale la voz. En una Pi hay varias salidas y casi
    todas callan: el HDMI no suena si el monitor no tiene altavoces, o si no
    hay monitor."""

    SALIDA_PI = """**** List of PLAYBACK Hardware Devices ****
card 0: Headphones [bcm2835 Headphones], device 0: bcm2835 Headphones [bcm2835 Headphones]
  Subdevices: 8/8
card 1: vc4hdmi [vc4-hdmi], device 0: MAI PCM i2s-hifi-0 [MAI PCM i2s-hifi-0]
  Subdevices: 1/1
card 2: UACDemoV10 [UACDemoV1.0], device 0: USB Audio [USB Audio]
  Subdevices: 1/1
"""

    def setUp(self):
        self.previo = os.environ.get("MEDIBOT_ALTAVOZ_DISPOSITIVO")
        os.environ.pop("MEDIBOT_ALTAVOZ_DISPOSITIVO", None)

    def tearDown(self):
        os.environ.pop("MEDIBOT_ALTAVOZ_DISPOSITIVO", None)
        if self.previo is not None:
            os.environ["MEDIBOT_ALTAVOZ_DISPOSITIVO"] = self.previo

    def test_lee_la_salida_de_aplay(self):
        lista = ma.dispositivos_salida(self.SALIDA_PI)
        self.assertEqual([d["id"] for d in lista],
                         ["plughw:0,0", "plughw:1,0", "plughw:2,0"])

    def test_prefiere_el_altavoz_USB(self):
        """Si alguien pincho un altavoz USB es que quiere oirlo por ahi."""
        self.assertEqual(ma.elegir_salida(ma.dispositivos_salida(self.SALIDA_PI)),
                         "plughw:2,0")

    def test_sin_USB_coge_el_jack_y_NO_el_HDMI(self):
        """El HDMI es el ultimo a proposito: en un robot la Pi suele ir sin
        pantalla, y ahi la voz se iria a ningun sitio sin dar ningun error."""
        solo_pi = ma.dispositivos_salida("""**** List of PLAYBACK Hardware Devices ****
card 0: vc4hdmi [vc4-hdmi], device 0: MAI PCM i2s-hifi-0 [MAI PCM i2s-hifi-0]
card 1: Headphones [bcm2835 Headphones], device 0: bcm2835 Headphones [bcm2835 Headphones]
""")
        self.assertEqual(ma.elegir_salida(solo_pi), "plughw:1,0")

    def test_solo_HDMI_es_mejor_que_nada(self):
        solo_hdmi = ma.dispositivos_salida("""**** List of PLAYBACK Hardware Devices ****
card 0: vc4hdmi [vc4-hdmi], device 0: MAI PCM i2s-hifi-0 [MAI PCM i2s-hifi-0]
""")
        self.assertEqual(ma.elegir_salida(solo_hdmi), "plughw:0,0")

    def test_la_variable_de_entorno_manda(self):
        os.environ["MEDIBOT_ALTAVOZ_DISPOSITIVO"] = "plughw:9,9"
        self.assertEqual(ma.elegir_salida(), "plughw:9,9")

    def test_sin_altavoces_devuelve_None(self):
        self.assertIsNone(ma.elegir_salida([]))


class PruebasAltavoz(unittest.TestCase):
    """Hablar desde la web por el altavoz de la Pi.

    Lo escribe todo un hilo aparte, asi que reproducir() solo ENCOLA: las
    comprobaciones esperan a que llegue en vez de mirar al instante."""

    class AplayFalso:
        """Doble de aplay que guarda lo que le meten."""

        def __init__(self):
            self.recibido = bytearray()
            self.stdin = self
            self.stderr = io.BytesIO()
            self.cerrado = False
            self.matado = False
            self._candado = threading.Lock()

        def write(self, datos):
            if self.cerrado:
                raise BrokenPipeError("aplay ya no escucha")
            with self._candado:
                self.recibido += datos
            return len(datos)

        def flush(self):
            pass

        def close(self):
            self.cerrado = True

        def terminate(self):
            self.matado = True

        def wait(self, timeout=None):
            return 0

        def copia(self):
            with self._candado:
                return bytes(self.recibido)

        def con_voz(self):
            """Lo recibido quitando el silencio de relleno."""
            return bytes(self.copia()).replace(b"\x00", b"")

    def _altavoz(self, **kw):
        a = ma.Altavoz(dispositivo="plughw:0,0", **kw)
        falso = self.AplayFalso()
        a._lanzar = lambda: falso
        self.altavoces.append(a)
        return a, falso

    def _esperar(self, condicion, mensaje, limite=5.0):
        t0 = time.time()
        while time.time() - t0 < limite:
            if condicion():
                return
            time.sleep(0.01)
        self.fail(mensaje)

    def setUp(self):
        self.altavoces = []
        self.previo = {k: os.environ.get(k) for k in
                       ("MEDIBOT_VOZ", "MEDIBOT_VOZ_GANANCIA",
                        "MEDIBOT_VOZ_PERSISTENTE")}
        for k in self.previo:
            os.environ.pop(k, None)
        self.hay_aplay = ma.hay_aplay
        ma.hay_aplay = lambda: True        # aqui no hay tarjeta de sonido

    def tearDown(self):
        for a in self.altavoces:
            try:
                a.cerrar()
            except Exception:                                # noqa: BLE001
                pass
        ma.hay_aplay = self.hay_aplay
        for k, v in self.previo.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v

    # ------------------------------------------------------ lo basico ----
    def test_por_defecto_se_puede_hablar(self):
        a, _ = self._altavoz()
        self.assertTrue(a.disponible())
        self.assertEqual(a.motivo_no_disponible(), "")

    def test_se_puede_apagar_a_mano(self):
        os.environ["MEDIBOT_VOZ"] = "0"
        a, _ = self._altavoz()
        self.assertFalse(a.disponible())
        self.assertIn("apagado a mano", a.motivo_no_disponible())
        ok, motivo = a.reproducir(b"\x01" * 100, 1)
        self.assertFalse(ok)
        self.assertIn("apagado a mano", motivo)

    def test_sin_aplay_dice_como_instalarlo(self):
        ma.hay_aplay = lambda: False
        a, _ = self._altavoz()
        self.assertFalse(a.disponible())
        self.assertIn("alsa-utils", a.motivo_no_disponible())

    def test_sin_altavoz_dice_como_buscarlo(self):
        a, _ = self._altavoz()
        a.dispositivo = None
        self.assertFalse(a.disponible())
        self.assertIn("aplay -l", a.motivo_no_disponible())

    def test_la_orden_lleva_los_parametros_correctos(self):
        a, _ = self._altavoz()
        orden = a.orden()
        self.assertEqual(orden[0], "aplay")
        self.assertIn("plughw:0,0", orden)
        self.assertIn("S16_LE", orden)
        self.assertIn("16000", orden)
        self.assertIn("raw", orden,
                      "El navegador manda PCM pelado: aplay debe esperarlo asi")

    def test_la_voz_llega_intacta_y_en_orden(self):
        #  Trozos del tamano real (~64 ms cada uno). Con trozos minusculos
        #  el colchon se vacia entre uno y otro y el hilo mete silencio, que
        #  es lo correcto pero no es lo que se esta probando aqui.
        a, falso = self._altavoz()
        trozos = [bytes([n]) * 2048 for n in range(1, 6)]
        for i, t in enumerate(trozos, start=1):
            ok, motivo = a.reproducir(t, i)
            self.assertTrue(ok, motivo)
        entera = b"".join(trozos)
        self._esperar(lambda: entera in falso.copia(),
                      "por el altavoz tiene que salir lo mismo que se mando, "
                      "seguido y en orden")

    def test_no_se_abre_el_altavoz_sin_voz(self):
        a, _ = self._altavoz()
        self.assertIsNone(a.proceso, "no hay que agarrar la tarjeta de sonido")
        self.assertFalse(a.estado()["abierto"])
        self.assertFalse(a.estado()["sonando"])
        a.reproducir(b"", 1)                     # un trozo vacio no abre nada
        self.assertIsNone(a.proceso)

    def test_un_trozo_atrasado_se_descarta(self):
        a, falso = self._altavoz()
        a.reproducir(b"A" * 32, 1)
        a.reproducir(b"B" * 32, 2)
        ok, motivo = a.reproducir(b"X" * 32, 2)   # numero ya usado
        self.assertTrue(ok)
        self.assertIn("atrasado", motivo)
        self._esperar(lambda: b"B" * 32 in falso.copia(), "no llego la voz")
        self.assertNotIn(b"X", falso.copia(),
                         "un trozo atrasado sonaria como un hipido")

    def test_seq_1_empieza_parrafada_nueva(self):
        a, falso = self._altavoz()
        for i in range(1, 6):
            a.reproducir(b"A" * 16, i)
        ok, _ = a.reproducir(b"NUEVA", 1)         # segunda pulsacion
        self.assertTrue(ok)
        self._esperar(lambda: b"NUEVA" in falso.copia(),
                      "al volver a pulsar tiene que seguir sonando")

    def test_un_trozo_gigante_se_rechaza(self):
        a, falso = self._altavoz()
        ok, motivo = a.reproducir(b"\x01" * (ma.MAX_TROZO_VOZ + 1), 1)
        self.assertFalse(ok)
        self.assertIn("demasiado grande", motivo)
        self.assertIsNone(a.proceso, "ni siquiera deberia abrir la tarjeta")

    def test_si_aplay_muere_se_dice_por_que(self):
        a, falso = self._altavoz()
        a.reproducir(b"A" * 16, 1)
        self._esperar(lambda: b"A" * 16 in falso.copia(), "no arranco")
        falso.close()                             # el altavoz se desenchufa
        self._esperar(lambda: a.proceso is None,
                      "hay que soltarlo para que el siguiente abra otro")
        self.assertIn("se corto el altavoz", a.estado()["ultimo_fallo"])

    def test_si_ni_siquiera_arranca_aplay_se_dice_y_no_revienta(self):
        """alsa-utils desinstalado a mitad: la web tiene que ver el motivo,
        no un 500."""
        a, _ = self._altavoz()

        def no_arranca():
            raise FileNotFoundError("aplay desaparecio")

        a._lanzar = no_arranca
        ok, motivo = a.reproducir(b"\x01" * 64, 1)
        self.assertFalse(ok)
        self.assertIn("aplay desaparecio", motivo)
        self.assertIsNone(a.proceso)

    def test_cerrar_sin_haber_abierto_no_revienta(self):
        ma.Altavoz(dispositivo="plughw:0,0").cerrar()     # no debe lanzar

    def test_cerrar_cierra_la_entrada_antes_de_matar(self):
        """Matar a secas cortaria la ultima media palabra: aplay tiene que
        poder terminar de soltar lo que ya tiene guardado."""
        a, falso = self._altavoz()
        a.reproducir(b"A" * 16, 1)
        self._esperar(lambda: falso.copia(), "no arranco el escritor")
        a.cerrar()
        self.assertTrue(falso.cerrado, "hay que cerrar la entrada de aplay")
        self.assertFalse(falso.matado, "y no hace falta matarlo si sale solo")
        self.assertIsNone(a.proceso)

    def test_un_trozo_mas_grande_que_la_cola_SUENA_igual(self):
        """Se aceptaba y luego se tiraba a si mismo al no caber: aceptar algo
        y no reproducirlo nunca es lo peor de los dos mundos."""
        a, falso = self._altavoz()
        gordo = b"\x01" * (a._tope_cola() + 4096)
        ok, motivo = a.reproducir(gordo, 1)
        self.assertTrue(ok, motivo)
        self._esperar(lambda: gordo in falso.copia(),
                      "un trozo grande tambien tiene que sonar")

    def test_el_estado_cuenta_lo_reproducido(self):
        a, falso = self._altavoz()
        a.reproducir(b"\x01" * 32000, 1)          # 1 s a 16 kHz mono 16 bits
        self._esperar(lambda: a.estado()["segundos"] >= 1.0,
                      "no llego a contarse")
        self.assertEqual(a.estado()["dispositivo"], "plughw:0,0")
        self.assertTrue(a.estado()["abierto"], "la tarjeta esta cogida")
        self.assertTrue(a.estado()["sonando"], "y acaba de salir voz")

    # ------------------------- que el audio no se lleve por delante al video
    def test_la_peticion_NO_espera_al_altavoz(self):
        """LO IMPORTANTE. Antes se escribia en aplay desde el hilo de la
        peticion: una tarjeta atascada se comia ese hilo, y con varias
        atascadas el servidor que tambien sirve el video se quedaba sin
        sitio. Ahora la peticion solo encola."""
        class Atascado(self.AplayFalso):
            def write(self, datos):
                time.sleep(30)
                return len(datos)

        a = ma.Altavoz(dispositivo="plughw:0,0")
        self.altavoces.append(a)
        a._lanzar = lambda: Atascado()
        t0 = time.time()
        for i in range(1, 9):
            ok, motivo = a.reproducir(b"\x01" * 4096, i)
            self.assertTrue(ok, motivo)
        tardo = time.time() - t0
        self.assertLess(tardo, 1.0,
                        f"ocho peticiones tardaron {tardo:.1f} s esperando "
                        f"al altavoz: eso bloquea el servidor del video")

    def test_el_estado_no_se_bloquea_con_el_altavoz_atascado(self):
        """/api/all pide esto cada segundo. Si esperase al candado, un aplay
        atascado congelaria la interfaz entera."""
        class Atascado(self.AplayFalso):
            def write(self, datos):
                time.sleep(5)
                return len(datos)

        a = ma.Altavoz(dispositivo="plughw:0,0")
        self.altavoces.append(a)
        a._lanzar = lambda: Atascado()
        a.reproducir(b"\x01" * 32, 1)
        time.sleep(0.3)                           # que se quede atascado
        t0 = time.time()
        estado = a.estado()
        self.assertLess(time.time() - t0, 1.0,
                        "estado() no puede esperar al altavoz")
        self.assertTrue(estado["sonando"])

    def test_si_la_cola_se_llena_se_tira_lo_VIEJO(self):
        """En una conversacion, la voz de hace un segundo ya no sirve: mejor
        perderla que acumular retraso o hacer esperar a la web."""
        class Atascado(self.AplayFalso):
            def write(self, datos):
                time.sleep(30)
                return len(datos)

        a = ma.Altavoz(dispositivo="plughw:0,0")
        self.altavoces.append(a)
        a._lanzar = lambda: Atascado()
        tope_bytes = a._tope_cola()
        trozos = (tope_bytes // 4096) * 3 + 2      # el triple de lo que cabe
        for i in range(1, trozos + 1):
            ok, _ = a.reproducir(b"\x01" * 4096, i)
            self.assertTrue(ok, "encolar nunca debe fallar por estar llena")
        en_cola = a.estado()["en_cola"]            # en segundos
        self.assertLessEqual(en_cola, ma.VOZ_COLA_SEGUNDOS + 0.2,
                             f"{en_cola} s esperando: demasiado retraso")
        self.assertGreater(a.estado()["tirados"], 0,
                           "hay que contar lo tirado para poder diagnosticarlo")

    # ------------------------------------- la tarjeta, abierta y alimentada
    def test_la_tarjeta_se_queda_ABIERTA_entre_frases(self):
        """Cada apertura de un dispositivo USB de audio renegocia el ancho de
        banda isocrono del bus, y ahi es donde se cae la camara. Medido: una
        conversacion de 14 s abria la tarjeta CUATRO veces."""
        a, falso = self._altavoz()
        self.assertTrue(a.persistente)
        a.reproducir(b"A" * 64, 1)
        self._esperar(lambda: a.proceso is not None, "no abrio")
        primero = a.proceso
        time.sleep(ma.INACTIVO_VOZ + 0.5)         # mas de lo que duraba antes
        self.assertIs(a.proceso, primero,
                      "no puede cerrarse entre frase y frase")
        a.reproducir(b"B" * 64, 1)
        self.assertIs(a.proceso, primero, "ni reabrirse en la frase siguiente")

    def test_mientras_nadie_habla_se_le_da_SILENCIO(self):
        """Sin nada que reproducir, ALSA se queda seca y eso se oye como
        chasquidos (underrun)."""
        a, falso = self._altavoz()
        a.reproducir(b"A" * 64, 1)
        self._esperar(lambda: b"A" * 64 in falso.copia(), "no arranco")
        antes = len(falso.copia())
        time.sleep(0.6)
        despues = len(falso.copia())
        self.assertGreater(despues, antes,
                           "hay que seguir alimentando la tarjeta")
        relleno = falso.copia()[antes:]
        self.assertEqual(set(relleno), {0},
                         "y el relleno tiene que ser silencio, no ruido")

    def test_el_silencio_NO_se_cuela_dentro_de_la_frase(self):
        """El relleno va antes, no en medio: si cae dentro, se oye como un
        tartamudeo al empezar a hablar."""
        a, falso = self._altavoz()
        frase = bytes(range(1, 256)) * 8          # reconocible y sin ceros
        trozo = len(frase) // 4
        for i in range(4):
            a.reproducir(frase[i * trozo:(i + 1) * trozo], i + 1)
            time.sleep(0.02)
        self._esperar(lambda: frase[:trozo] in falso.copia(), "no llego")
        self._esperar(lambda: frase in falso.copia(),
                      "la frase tiene que salir SEGUIDA, sin silencio dentro")

    def test_se_puede_volver_a_abrir_y_cerrar_en_cada_frase(self):
        """Por si alguien necesita compartir la tarjeta con otro programa."""
        os.environ["MEDIBOT_VOZ_PERSISTENTE"] = "0"
        a, falso = self._altavoz()
        self.assertFalse(a.persistente)
        a.reproducir(b"A" * 64, 1)
        self._esperar(lambda: a.proceso is not None, "no abrio")
        self._esperar(lambda: a.proceso is None,
                      "en este modo si tiene que soltarla al callar",
                      limite=ma.INACTIVO_VOZ + 3)

    def test_abierto_no_es_lo_mismo_que_sonando(self):
        """Con la tarjeta siempre abierta, un solo campo decia "sonando" todo
        el rato y no servia para diagnosticar nada."""
        a, falso = self._altavoz()
        a.preparar()
        self.assertTrue(a.estado()["abierto"], "la tarjeta esta cogida")
        self.assertFalse(a.estado()["sonando"], "pero no esta saliendo voz")
        a.reproducir(b"\x01" * 4096, 1)
        self.assertTrue(a.estado()["sonando"], "ahora si")

    def test_preparar_abre_la_tarjeta_antes_de_hablar(self):
        """Se llama al arrancar, con las camaras recien puestas en marcha."""
        a, falso = self._altavoz()
        listo, motivo = a.preparar()
        self.assertTrue(listo, motivo)
        self.assertIsNotNone(a.proceso)
        self._esperar(lambda: falso.copia(),
                      "tiene que empezar a alimentarla ya")

    # --------------------------------------------------------- volumen ----
    def test_la_ganancia_sube_el_volumen(self):
        os.environ["MEDIBOT_VOZ_GANANCIA"] = "2.0"
        a, falso = self._altavoz()
        self.assertEqual(a.ganancia, 2.0)
        bajito = struct.pack("<4h", 1000, -1000, 500, -500)
        a.reproducir(bajito, 1)
        self._esperar(lambda: struct.pack("<4h", 2000, -2000, 1000, -1000)
                      in falso.copia(), "la ganancia no se aplico")

    def test_la_ganancia_RECORTA_en_vez_de_dar_la_vuelta(self):
        """Sin recortar, una muestra que se pasa aparece con el signo
        cambiado y no suena "mas alto": suena como un chasquido. Es
        exactamente la distorsion que se quiere evitar."""
        subido = ma._con_ganancia(struct.pack("<4h", 30000, -30000, 20000, -20000),
                                  4.0)
        self.assertEqual(struct.unpack("<4h", subido),
                         (32767, -32768, 32767, -32768))

    def test_sin_ganancia_no_se_toca_el_audio(self):
        original = struct.pack("<4h", 1234, -1234, 0, 32767)
        self.assertIs(ma._con_ganancia(original, 1.0), original)


if __name__ == "__main__":
    unittest.main(verbosity=2)
