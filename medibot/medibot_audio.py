#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MEDIBOT - Audio del microfono de la camara
==========================================
QUE HACE
--------
Captura el microfono de la webcam (la Logitech C270 lleva uno) y lo sirve por
HTTP para poder ESCUCHAR desde el navegador lo que pasa alrededor del robot.

POR QUE arecord Y NO UNA LIBRERIA
---------------------------------
`arecord` viene con alsa-utils, que ya esta instalado en Raspberry Pi OS. No
hay que compilar nada ni anadir dependencias pesadas (pyaudio arrastra
PortAudio; WebRTC arrastra aiortc y todo su arbol). Se lanza un proceso, se
lee su salida en crudo y se reenvia al navegador. Si el dia de manana hace
falta menos latencia, el sitio donde cambiarlo es este modulo y solo este.

FORMATO: WAV EN DIRECTO
-----------------------
Se manda una cabecera WAV seguida de PCM sin parar. Un WAV normal declara en
la cabecera cuantos bytes de audio vienen detras, pero aqui no se sabe: el
audio no termina hasta que el navegador cierre. Se declara el maximo que cabe
en el campo (4 GiB) y los navegadores lo reproducen segun va llegando. Es lo
que hacen las radios por internet desde hace veinte anos.

    <audio src="/audio" autoplay>

No se usa MP3/Ogg a proposito: harian falta ffmpeg o lame, que no siempre
estan, y comprimir gastaria CPU de la Pi que hace falta para el video.

VIENE ACTIVADO
--------------
El audio esta disponible sin tener que poner nada: basta con tener
`alsa-utils` instalado y un microfono conectado. Se apaga con
MEDIBOT_AUDIO=0.

Que este activado NO quiere decir que grabe solo: el microfono no se abre
hasta que alguien pulsa Escuchar en la web, y se suelta al dejar de
escuchar. "Activado" significa que el boton funciona, no que haya un
`arecord` corriendo. Aun asi, es el microfono de la habitacion donde este
el robot: si publicas la web por internet, ponle Cloudflare Access delante.

VARIOS ESCUCHANDO A LA VEZ
--------------------------
ALSA no deja abrir el mismo microfono dos veces, asi que no se lanza un
`arecord` por navegador: se lanza UNO y se reparte. El movil y el PC pueden
escuchar a la vez y cada uno recibe el audio completo; que uno cierre la
pestana no corta a los demas, y el microfono se suelta cuando se va el
ULTIMO. Ver la clase Microfono.

HABLAR HACIA EL ROBOT (INTERCOMUNICADOR)
----------------------------------------
La otra direccion tambien: la pagina captura tu voz y la suelta por un
altavoz enchufado a la Pi (clase Altavoz, con `aplay`). Va en PCM crudo por
las mismas razones que la ida.

OJO CON EL NAVEGADOR: para coger tu microfono hace falta HTTPS. Los
navegadores solo dan getUserMedia en contexto seguro, asi que por
http://<ip-de-la-pi>:5000 NO deja hablar por mucho que el robot este listo.
Entra por el tunel de Cloudflare (que ya da HTTPS) o por http://localhost.
Escuchar no tiene ese problema: eso es solo reproducir.

LO QUE NO HACE
--------------
No cancela el eco entre el altavoz de la Pi y el microfono de la camara. Se
apana con lo que ya trae el navegador (echoCancellation) y hablando con el
boton pulsado, que ademas calla la escucha mientras hablas. Con el altavoz
pegado a la camara y el volumen alto puede acoplarse, como cualquier
megafonia.

CONFIGURACION (variables de entorno)
------------------------------------
    MEDIBOT_AUDIO=0              apagarlo (por defecto 1: ACTIVADO)
    MEDIBOT_AUDIO_DISPOSITIVO    p.ej. plughw:1,0  (vacio = autodetectar)
    MEDIBOT_AUDIO_HZ=16000       frecuencia de muestreo
    MEDIBOT_AUDIO_CANALES=1      1 = mono (la C270 es mono)
    MEDIBOT_AUDIO_BUFFER_MS=100  colchon de ALSA (0 = el de fabrica de arecord;
                                 subelo si el audio sale entrecortado)

    MEDIBOT_VOZ=0                apagar el hablar (por defecto 1: ACTIVADO)
    MEDIBOT_ALTAVOZ_DISPOSITIVO  p.ej. plughw:0,0  (vacio = autodetectar)
    MEDIBOT_VOZ_HZ=16000         frecuencia de la voz que llega
    MEDIBOT_VOZ_CANALES=1        1 = mono
    MEDIBOT_VOZ_SOLO_LAN=0       dejar hablar tambien desde fuera (por defecto
                                 1: solo desde la red de casa)

Para ver que microfonos hay:   arecord -l
Para ver que altavoces hay:    aplay -l
"""

import ipaddress
import os
import queue
import re
import shutil
import struct
import subprocess
import threading
import time

#  Tamano que se declara en la cabecera cuando el audio no termina nunca.
#  Es el maximo de un entero de 32 bits sin signo: el navegador entiende
#  "esto es larguisimo" y va reproduciendo segun llega.
TAM_STREAMING = 0xFFFFFFFF

#  LO MAXIMO que se lee de una vez del proceso de captura. A 16 kHz mono de
#  16 bits son 32000 bytes/s, asi que 1024 son 32 ms.
#
#  Antes eran 4096 (128 ms) y se leian con read(), que ESPERA a tener los
#  4096 enteros: cada sonido se quedaba parado hasta llenar el bloque. Ahora
#  se lee con read1(), que devuelve lo que haya en cuanto lo hay, asi que
#  esto es solo un tope, no una espera.
TROZO = 1024

#  Colchon de cada oyente, en segundos. Es lo maximo que puede quedarse
#  atras uno al que se le atasque la conexion.
#
#  Antes era de 2 s, y ese era EL problema del retraso: un tiron de wifi (o
#  la pestana en segundo plano un momento) llenaba el colchon, se le soltaban
#  al navegador 2 s de audio de golpe, y como el <audio> reproduce a 1x sin
#  saltarse nada, ese retraso se quedaba puesto PARA SIEMPRE y se sumaba
#  tiron tras tiron. Con 0,25 s, lo peor que puede meter un tiron es un
#  cuarto de segundo. Ver tambien la vigilancia del directo en la pagina.
COLCHON_SEGUNDOS = 0.25

#  Por defecto, cuanto puede guardar ALSA en su propio buffer antes de que
#  arecord nos lo entregue. El valor de fabrica de arecord ronda el medio
#  segundo, que para escuchar en directo es una eternidad. Se puede subir si
#  el audio sale entrecortado en una Pi muy cargada, o poner 0 para dejar el
#  de fabrica.
BUFFER_MS_POR_DEFECTO = 100


def _entero(nombre, por_defecto):
    try:
        return int(os.environ.get(nombre, "").strip() or por_defecto)
    except ValueError:
        return por_defecto


def _booleano(nombre, por_defecto=False):
    bruto = os.environ.get(nombre, "").strip().lower()
    if not bruto:
        return por_defecto
    return bruto in ("1", "true", "si", "sí", "yes", "on")


def _decimal_arranque(nombre, por_defecto):
    """Como _decimal, pero se usa para constantes del modulo (se lee al
    importar, no por cada Altavoz)."""
    try:
        return float(os.environ.get(nombre, "").strip() or por_defecto)
    except ValueError:
        return por_defecto


def _decimal(nombre, por_defecto):
    try:
        return float(os.environ.get(nombre, "").strip() or por_defecto)
    except ValueError:
        return por_defecto


def _con_ganancia(datos, factor, ancho=2):
    """Sube el volumen del PCM SATURANDO, nunca dando la vuelta.

    Lo importante es el recorte: multiplicar sin mas hace que una muestra
    que se pase del entero de 16 bits aparezca con el signo cambiado, y eso
    no se oye como "mas alto", se oye como un chasquido asqueroso. Justo la
    distorsion que se quiere evitar.

    Es el remedio de ultima hora: lo primero es subir el volumen de la
    tarjeta con amixer, que no gasta CPU ni añade ruido."""
    if factor == 1.0 or not datos:
        return datos
    try:
        import audioop                                       # noqa: PLC0415
        return audioop.mul(datos, ancho, factor)     # en C, y ya recorta
    except Exception:                                        # noqa: BLE001
        #  audioop desaparecio en Python 3.13. A mano, que para trozos de
        #  128 ms se nota poco.
        import array                                         # noqa: PLC0415
        muestras = array.array("h")
        muestras.frombytes(datos[:len(datos) // 2 * 2])
        for i, v in enumerate(muestras):
            w = int(v * factor)
            muestras[i] = 32767 if w > 32767 else (-32768 if w < -32768 else w)
        return muestras.tobytes()


def _ultimo_error(proceso):
    """Lo que arecord o aplay dejaron dicho por stderr, para explicarlo.

    Sin esto, una camara desenchufada a mitad, un altavoz que no existe o un
    dispositivo ocupado por otro programa se verian en la web como un "no se
    pudo escuchar" pelado, y el motivo de verdad ("No such device", "Device
    or resource busy") se perderia.

    Se lee SOLO cuando el proceso ya ha muerto: sobre un tubo que sigue
    abierto, read() se quedaria esperando para siempre y quien espera el
    audio no se enteraria nunca de que se acabo (peticion colgada y boton
    encendido sin sonido)."""
    if proceso is None:
        #  Pasa si el propio Popen fallo (alsa-utils desinstalado a mitad):
        #  no hay proceso al que preguntarle, y quien llama ya tiene su
        #  propio mensaje.
        return ""
    try:
        proceso.wait(timeout=1)
    except Exception:                                        # noqa: BLE001
        return ""                # sigue vivo: mejor sin motivo que colgados
    try:
        bruto = proceso.stderr.read() or b""
    except Exception:                                        # noqa: BLE001
        return ""
    texto = bruto.decode("utf-8", "replace").strip()
    #  Se explayan en varias lineas; la ultima es la que dice el problema.
    return texto.splitlines()[-1].strip() if texto else ""


# ------------------------------------------------------------- cabecera ----
def cabecera_wav(hz=16000, canales=1, bits=16, bytes_de_audio=None):
    """Cabecera WAV (RIFF/PCM).

    bytes_de_audio=None significa "no se sabe cuanto dura": se declara el
    maximo y el navegador lo trata como una emision continua. Con un numero
    concreto sale un WAV normal y corriente, que es lo que permite
    comprobarlo con el modulo `wave` de la libreria estandar."""
    if canales < 1:
        raise ValueError("canales debe ser >= 1")
    if bits % 8:
        raise ValueError("bits debe ser multiplo de 8")

    bytes_por_muestra = bits // 8
    alineacion = canales * bytes_por_muestra
    tasa = hz * alineacion

    if bytes_de_audio is None:
        tam_datos = TAM_STREAMING
        tam_riff = TAM_STREAMING
    else:
        tam_datos = bytes_de_audio
        tam_riff = 36 + bytes_de_audio      # 36 = lo que ocupa el resto

    return b"".join((
        b"RIFF", struct.pack("<I", tam_riff), b"WAVE",
        b"fmt ", struct.pack("<IHHIIHH",
                             16,            # tamano del bloque fmt
                             1,             # 1 = PCM sin comprimir
                             canales, hz, tasa, alineacion, bits),
        b"data", struct.pack("<I", tam_datos),
    ))


# ---------------------------------------------------------- dispositivo ----
def hay_arecord():
    return shutil.which("arecord") is not None


def hay_aplay():
    return shutil.which("aplay") is not None


def _listar(programa, salida=None):
    """Lo que ve ALSA segun `arecord -l` (microfonos) o `aplay -l` (altavoces).

    Los dos programas imprimen el mismo formato, asi que se parsea una sola
    vez. Se acepta 'salida' ya hecha para poder probarlo sin tarjeta de
    sonido."""
    if salida is None:
        if shutil.which(programa) is None:
            return []
        try:
            r = subprocess.run([programa, "-l"], capture_output=True,
                               text=True, timeout=5)
            salida = r.stdout
        except (OSError, subprocess.SubprocessError):
            return []

    encontrados = []
    #  tarjeta 1: U0x46d0x825 [USB Device 0x46d:0x825], dispositivo 0: ...
    patron = re.compile(
        r"^(?:card|tarjeta)\s+(\d+):\s*(\S+)\s*\[([^\]]*)\].*?"
        r"(?:device|dispositivo)\s+(\d+)", re.I | re.M)
    for m in patron.finditer(salida):
        tarjeta, corto, descripcion, disp = m.groups()
        encontrados.append({
            "tarjeta": int(tarjeta),
            "dispositivo": int(disp),
            "id": f"plughw:{tarjeta},{disp}",
            "nombre": descripcion.strip() or corto,
        })
    return encontrados


def dispositivos(salida=None):
    """Los microfonos que ve ALSA (`arecord -l`)."""
    return _listar("arecord", salida)


def dispositivos_salida(salida=None):
    """Los altavoces que ve ALSA (`aplay -l`)."""
    return _listar("aplay", salida)


def elegir_dispositivo(lista=None):
    """El que se pida por entorno; si no, el primero que parezca una webcam.

    Se prefiere uno con 'USB' o 'Cam' en el nombre porque en una Raspberry la
    tarjeta 0 suele ser la salida HDMI/jack, que NO graba."""
    pedido = os.environ.get("MEDIBOT_AUDIO_DISPOSITIVO", "").strip()
    if pedido:
        return pedido
    lista = dispositivos() if lista is None else lista
    if not lista:
        return None
    for d in lista:
        if re.search(r"usb|cam|webcam|c270", d["nombre"], re.I):
            return d["id"]
    return lista[0]["id"]


def elegir_salida(lista=None):
    """El altavoz que se pida por entorno; si no, el que mas probablemente
    tenga algo enchufado.

    Orden: USB primero (si alguien pincho un altavoz o un dongle USB, es
    para esto), luego el jack analogico de la Pi, luego cualquiera que NO
    sea HDMI. El HDMI queda ultimo a proposito: en un robot la Pi suele ir
    sin pantalla, o con un monitor sin altavoces, y ahi el audio se va a
    ningun sitio sin dar ningun error."""
    pedido = os.environ.get("MEDIBOT_ALTAVOZ_DISPOSITIVO", "").strip()
    if pedido:
        return pedido
    lista = dispositivos_salida() if lista is None else lista
    if not lista:
        return None
    for patron in (r"usb|uac|speaker|altavoz",          # USB enchufado
                   r"headphone|analog|jack|auricular",  # jack de 3,5 mm
                   r"^(?!.*hdmi).*$"):                  # cualquiera menos HDMI
        for d in lista:
            if re.search(patron, d["nombre"], re.I):
                return d["id"]
    return lista[0]["id"]


# ------------------------------------------------------------- captura ----
def cola_maxima(hz=16000, canales=1, bits=16):
    """Cuantos trozos caben en el colchon de un oyente.

    Se calcula en SEGUNDOS y no con un numero fijo de trozos: lo que importa
    es cuanto retraso puede acumular, y eso depende de la frecuencia y del
    tamano del trozo. Con un numero fijo, cambiar cualquiera de los dos movia
    el retraso sin que se notara al leer el codigo."""
    por_segundo = hz * canales * (bits // 8)
    return max(2, int(COLCHON_SEGUNDOS * por_segundo / TROZO))


#  El caso normal (16 kHz mono): 8 trozos = 0,25 s.
COLA_MAXIMA = cola_maxima()


class _Captura:
    """Un `arecord` corriendo y LOS OYENTES QUE DEPENDEN DE EL.

    Los oyentes se apuntan aqui y no en el Microfono para que una captura
    que se esta muriendo no pueda avisar de "se acabo el audio" a un oyente
    que acaba de llegar y que ya pertenece a la captura SIGUIENTE. Ese era
    el fallo de "cierro la pagina, la vuelvo a abrir y no se oye nada": el
    hilo de la captura vieja tardaba un instante en enterarse y, al
    despedirse, cortaba al recien llegado."""

    def __init__(self, proceso):
        self.proceso = proceso
        self.oyentes = []
        self.hilo = None


class Microfono:
    """El microfono de la camara, repartido entre todos los que escuchan.

    UNA sola captura para TODOS
    ---------------------------
    ALSA no deja abrir `plughw:` dos veces: el segundo `arecord` muere con
    "Device or resource busy". Asi que no vale lanzar uno por navegador. Se
    lanza UNO y un hilo va repartiendo cada trozo a todos los que escuchan,
    cada uno con su propia cola.

    Antes todas las peticiones leian del MISMO tubo, y eso daba tres fallos
    que salian en cuanto habia dos oyentes (el movil y el PC, o una simple
    recarga de pagina, porque el navegador abre la peticion nueva antes de
    soltar la vieja):

      - cada uno se llevaba solo una parte de las muestras -> los dos oian
        el audio troceado, con saltos;
      - al cerrar uno la pestana se mataba la captura compartida y al otro
        se le cortaba el sonido a mitad;
      - el generador del que se quedaba reventaba con AttributeError al
        leer de un proceso que ya era None.

    Ahora cada oyente recibe TODO el audio, irse uno no molesta a los
    demas, y el microfono se suelta cuando se va el ULTIMO.
    """

    def __init__(self, dispositivo=None, hz=None, canales=None, log=print):
        self.hz = hz or _entero("MEDIBOT_AUDIO_HZ", 16000)
        self.canales = canales or _entero("MEDIBOT_AUDIO_CANALES", 1)
        self.bits = 16
        self.dispositivo = dispositivo or elegir_dispositivo()
        self.buffer_ms = _entero("MEDIBOT_AUDIO_BUFFER_MS", BUFFER_MS_POR_DEFECTO)
        self.log = log
        self.proceso = None
        #  Protege 'proceso', la captura en curso y sus oyentes: los tocan a
        #  la vez el hilo de cada peticion web (Flask atiende en paralelo) y
        #  el hilo del reparto.
        self._candado = threading.Lock()
        self._captura = None
        self._fallo = ""          # por que se corto la ultima captura

    def disponible(self):
        """Se puede intentar capturar. NO garantiza que haya sonido: eso solo
        se sabe abriendo el dispositivo, y hacerlo aqui robaria el microfono."""
        return bool(_booleano("MEDIBOT_AUDIO", True)) and hay_arecord() \
            and self.dispositivo is not None

    def motivo_no_disponible(self):
        if not _booleano("MEDIBOT_AUDIO", True):
            #  Viene activado, asi que si se llega aqui es porque alguien lo
            #  apago a mano: lo que hace falta decirle es donde.
            return ("el audio esta apagado a mano (MEDIBOT_AUDIO="
                    f"{os.environ.get('MEDIBOT_AUDIO', '').strip()}); quita esa "
                    "variable o ponla a 1 para poder escuchar")
        if not hay_arecord():
            return ("falta 'arecord'; instalalo con: "
                    "sudo apt install alsa-utils")
        if self.dispositivo is None:
            return ("no se encontro ningun microfono; comprueba 'arecord -l' "
                    "y fija MEDIBOT_AUDIO_DISPOSITIVO si hace falta")
        return ""

    def orden(self):
        """El comando exacto. Aparte para poder comprobarlo sin ejecutarlo."""
        orden = ["arecord",
                 "-D", str(self.dispositivo),
                 "-f", f"S{self.bits}_LE",
                 "-r", str(self.hz),
                 "-c", str(self.canales),
                 "-t", "raw",          # sin cabecera: la ponemos nosotros
                 "-q"]                 # sin cháchara por stderr
        #  Buffer corto de ALSA. Sin esto arecord usa el de fabrica, que
        #  ronda el medio segundo: el sonido se queda ahi dentro antes de
        #  que nos lo den, y eso es retraso puro que no se recupera despues.
        #  Con 0 se deja el de fabrica (para depurar, o si una Pi muy
        #  cargada entrecorta el audio).
        if self.buffer_ms > 0:
            #  Un periodo es lo que arecord espera antes de entregar nada.
            #  A un cuarto del buffer entrega cuatro veces por colchon, que
            #  es fluido sin freir la CPU a interrupciones.
            periodo_ms = max(10, self.buffer_ms // 4)
            orden += ["--buffer-time", str(self.buffer_ms * 1000),
                      "--period-time", str(periodo_ms * 1000)]
        return orden

    def abrir(self):
        """Lanza arecord. Idempotente: con uno ya en marcha no hace nada."""
        if self.proceso is not None:
            return
        self.proceso = subprocess.Popen(
            self.orden(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def cerrar(self):
        """Corta la captura y suelta el microfono, haya oyentes o no."""
        with self._candado:
            captura, self._captura = self._captura, None
            proceso, self.proceso = self.proceso, None
        self._matar(proceso)
        #  Normalmente es el mismo proceso; se comprueba por si una carrera
        #  dejo la captura y 'proceso' descolgados uno del otro.
        if captura is not None and captura.proceso is not proceso:
            self._matar(captura.proceso)

    @staticmethod
    def _matar(p):
        """Mata un arecord. NADA de aqui puede lanzar: se llama desde los
        `finally`, y una excepcion aqui taparia el error de verdad (p.ej. que
        el navegador colgo la conexion) dejando ademas el proceso vivo."""
        if p is None:
            return
        try:
            p.terminate()
            p.wait(timeout=2)
        except Exception:                                    # noqa: BLE001
            try:
                p.kill()
            except Exception:                                # noqa: BLE001
                pass
        finally:
            for flujo in (getattr(p, "stdout", None), getattr(p, "stderr", None)):
                try:
                    flujo.close()
                except Exception:                            # noqa: BLE001
                    pass

    # ------------------------------------------------------ oyentes ----
    def _suscribir(self):
        """Apunta un oyente y arranca la captura si no habia ninguna.

        Devuelve (captura, cola): la cola por la que le llegara su audio y
        la captura a la que pertenece, que hace falta para darse de baja en
        la de VERDAD y no en la que haya en ese momento."""
        cola = queue.Queue(maxsize=cola_maxima(self.hz, self.canales, self.bits))
        with self._candado:
            captura = self._captura
            if captura is None:
                self._fallo = ""
                self.abrir()
                captura = _Captura(self.proceso)
                self._captura = captura
                captura.hilo = threading.Thread(
                    target=self._repartir, args=(captura,),
                    name="medibot-audio", daemon=True)
                captura.hilo.start()
            captura.oyentes.append(cola)
        return captura, cola

    def _desuscribir(self, captura, cola):
        """Da de baja un oyente y suelta el microfono si era el ultimo."""
        with self._candado:
            try:
                captura.oyentes.remove(cola)
            except ValueError:
                pass
            if captura.oyentes:
                return                   # quedan oyentes: la captura sigue
            #  Era el ultimo. Se desengancha la captura AQUI DENTRO (con el
            #  candado) para que un oyente que llegue en este mismo instante
            #  -una recarga de pagina- empiece una captura nueva en vez de
            #  engancharse a este arecord que esta muriendo. Matarlo se hace
            #  FUERA: tarda hasta 2 s y con el candado cogido bloquearia la
            #  web entera.
            if self._captura is captura:
                self._captura = None
                self.proceso = None
        self._matar(captura.proceso)

    def _reparte_a(self, captura, datos):
        """Una copia de 'datos' para cada oyente DE ESA captura.

        'datos' None significa "ya no viene mas audio" y hace que las
        peticiones terminen en vez de quedarse esperando para siempre."""
        with self._candado:
            colas = list(captura.oyentes)
        for cola in colas:
            try:
                cola.put_nowait(datos)
            except queue.Full:
                #  Oyente lento (un movil con mala cobertura). Se le tira el
                #  trozo mas viejo en vez de esperarle: en un directo el
                #  audio de hace dos segundos ya no sirve, y bloquear aqui
                #  congelaria tambien a los demas y llenaria el tubo de
                #  arecord hasta atascar la captura.
                try:
                    cola.get_nowait()
                    cola.put_nowait(datos)
                except (queue.Empty, queue.Full):            # noqa: BLE001
                    pass

    def _repartir(self, captura):
        """Hilo: lee de arecord y reparte. Uno solo para toda la casa.

        Va en un hilo aparte y no dentro del generador de cada peticion
        porque el tubo de arecord solo se puede leer UNA vez: si cada
        peticion leyera por su cuenta, cada una se llevaria unas muestras y
        todas oirian el audio a trozos."""
        motivo = ""
        #  read1() devuelve lo que haya en cuanto lo hay; read() esperaria a
        #  juntar TROZO bytes enteros y le sumaria esa espera al retraso de
        #  todo el mundo. Los dobles de las pruebas solo tienen read(), de
        #  ahi el respaldo.
        leer = getattr(captura.proceso.stdout, "read1", None) \
            or captura.proceso.stdout.read
        try:
            while True:
                datos = leer(TROZO)
                if not datos:
                    motivo = _ultimo_error(captura.proceso) or \
                        "la captura termino (¿camara desenchufada?)"
                    break
                self._reparte_a(captura, datos)
        except Exception as e:                               # noqa: BLE001
            #  Pasa tambien al cerrar nosotros el proceso (lo normal cuando
            #  se va el ultimo oyente): el tubo queda cerrado a media lectura.
            motivo = f"se corto la captura: {e}"
        finally:
            with self._candado:
                #  Si esta captura sigue siendo la actual, se descuelga: asi
                #  el siguiente oyente abre una nueva en vez de engancharse a
                #  un arecord muerto. Si ya hay otra, no se toca nada de ella
                #  (ni su motivo): esto es el hilo viejo despidiendose.
                if self._captura is captura:
                    self._captura = None
                    self.proceso = None
                    self._fallo = motivo
            self._reparte_a(captura, None)

    # -------------------------------------------------------- servir ----
    def trozos(self):
        """Generador: cabecera WAV y despues PCM hasta que se corte.

        Cada peticion tiene SU cola, asi que dos navegadores oyen lo mismo y
        completo. Se da de baja SIEMPRE al terminar (aunque el navegador
        cuelgue la conexion a mitad): si no, cada recarga de la pagina
        dejaria un arecord vivo agarrado al microfono y a la segunda ya no se
        oiria nada."""
        captura, cola = self._suscribir()
        try:
            yield cabecera_wav(self.hz, self.canales, self.bits)
            while True:
                datos = cola.get()
                if datos is None:            # se acabo el audio
                    break
                yield datos
        finally:
            self._desuscribir(captura, cola)

    def estado(self):
        with self._candado:
            captura = self._captura
            oyentes = len(captura.oyentes) if captura is not None else 0
            fallo = self._fallo
        #  'motivo' es lo que la web ensena cuando no se oye. Si se puede
        #  capturar pero la ultima captura se corto, el motivo util es ese
        #  (el error de ALSA), no un hueco en blanco.
        return {
            "disponible": self.disponible(),
            "motivo": self.motivo_no_disponible() or fallo,
            "dispositivo": self.dispositivo,
            "hz": self.hz,
            "canales": self.canales,
            "bits": self.bits,
            "oyentes": oyentes,
            #  Aparte de 'motivo' porque son cosas distintas: 'motivo' es lo
            #  que se le ensena al usuario (y manda la indisponibilidad), y
            #  esto es el error crudo de la ultima captura, para diagnosticar.
            "ultimo_fallo": fallo,
        }


# ------------------------------------------------------- solo en casa ----
#  Cabeceras que pone un proxy al reenviar. Si viene alguna, la peticion ha
#  dado un salto por fuera aunque llegue desde 127.0.0.1.
CABECERAS_DE_PROXY = ("CF-Connecting-IP", "CF-Ray", "X-Forwarded-For",
                      "X-Forwarded-Host", "X-Real-IP", "Forwarded")


def es_local(ip, cabeceras=None):
    """True si la peticion viene de la red de casa y no de internet.

    NO BASTA CON MIRAR LA IP: el tunel de Cloudflare corre en la propia Pi,
    asi que todo lo que llega por el aparece como 127.0.0.1. Mirando solo la
    direccion, cualquiera de internet pasaria por vecino. Lo que le delata
    son las cabeceras que anade el propio tunel al reenviar.

    Que alguien de la LAN se las invente solo consigue que le digamos que no,
    y de la LAN ya le dejabamos entrar: el engano no lleva a ningun sitio.
    Al reves no se puede: a nadie de internet le llega la peticion sin pasar
    por el proxy que las pone."""
    for nombre in CABECERAS_DE_PROXY:
        if (cabeceras or {}).get(nombre):
            return False
    try:
        direccion = ipaddress.ip_address((ip or "").strip())
    except ValueError:
        return False
    if direccion.is_loopback or direccion.is_private or direccion.is_link_local:
        return True
    return False


# ------------------------------------------------------------ altavoz ----
#  Segundos sin recibir voz tras los que se suelta el altavoz, SOLO en el
#  modo no persistente (ver ALTAVOZ_PERSISTENTE).
INACTIVO_VOZ = 2.0

#  Dejar la tarjeta de sonido ABIERTA aunque nadie hable.
#
#  Parece desperdicio y es lo contrario. Abrir un dispositivo de audio USB
#  reserva ancho de banda isocrono en el bus; si la camara UVC ya tiene
#  reservado lo suyo, ESE es el momento en que el kernel puede tirar la
#  camara ("sin conexion", o saltando entre camara 1 y 2). Abriendo y
#  cerrando en cada frase, una conversacion normal de 14 s renegociaba el
#  bus CUATRO veces: cuatro oportunidades de tirar el video, y justo al
#  pulsar para hablar, que es cuando se notaba.
#
#  Manteniendola abierta se renegocia UNA vez y ya. De paso se quitan los
#  cortes al empezar cada frase (la tarjeta ya esta lista) y no hay
#  underrun, porque el hilo escritor le da silencio cuando no hay voz.
ALTAVOZ_PERSISTENTE = True

#  Segundos de audio que el hilo escritor procura tener siempre entregados
#  por delante. Es el colchon que absorbe los baches de red: sin el, el
#  primer tropiezo deja a la tarjeta sin nada que reproducir y se oye el
#  corte. Se mantiene por debajo del buffer de aplay para que sea este quien
#  marque el ritmo bloqueando la escritura.
COLCHON_ALTAVOZ = _decimal_arranque("MEDIBOT_VOZ_COLCHON_MS", 150) / 1000.0

#  Tope de la cola de reproduccion. Pasado eso se tiran los trozos VIEJOS:
#  en una conversacion, la voz de hace un segundo ya no sirve de nada.
VOZ_COLA_SEGUNDOS = 0.6

#  Cada cuanto se comprueba la cola cuando no hay voz. Es tambien el tamano
#  del trozo de silencio que se suelta para mantener viva la tarjeta.
SILENCIO_SEGUNDOS = 0.064

#  Lo mas grande que se acepta de una vez: 2 s de audio a 16 kHz mono de 16
#  bits. Un trozo mas gordo que eso no es voz en directo, es alguien
#  mandando un fichero, y no hay por que tragarselo en memoria.
MAX_TROZO_VOZ = 64000


class Altavoz:
    """El altavoz de la Pi: hablar desde el navegador (intercomunicador).

    AL REVES QUE Microfono
    ----------------------
    Microfono saca audio de la Pi hacia el navegador con `arecord`. Esto lo
    mete: recibe PCM del navegador y lo escribe en la entrada de `aplay`,
    que tambien viene con alsa-utils. Misma idea y ninguna dependencia
    nueva: ni WebRTC, ni aiortc, ni ffmpeg, ni pyaudio.

    POR QUE PCM EN CRUDO Y NO WEBM/OPUS
    -----------------------------------
    MediaRecorder, que es lo comodo en el navegador, entrega WebM/Opus, y
    descomprimirlo en la Pi pediria ffmpeg u opus-tools. En vez de eso la
    pagina saca las muestras con la Web Audio API y las manda ya en S16_LE
    a 16 kHz: `aplay` las toca tal cual y la Pi no descodifica nada. Es la
    misma decision que en la otra direccion, y por lo mismo: en una Pi que
    ya va justa con dos camaras, la CPU se gasta en el video.

    SE ABRE Y SE CIERRA SOLO
    ------------------------
    `aplay` se lanza con el primer trozo de voz y se suelta tras
    INACTIVO_VOZ segundos sin recibir nada. Dejarlo abierto agarraria la
    tarjeta de sonido para siempre y nadie mas podria usarla; en algunos
    montajes ademas se oye un siseo de fondo mientras esta abierta.

    UNA PETICION CADA VEZ
    ---------------------
    Las escrituras van con candado: dos peticiones escribiendo a la vez en
    el mismo tubo entrelazarian las muestras y saldria ruido en vez de voz.
    """

    def __init__(self, dispositivo=None, hz=None, canales=None, log=print):
        self.hz = hz or _entero("MEDIBOT_VOZ_HZ", 16000)
        self.canales = canales or _entero("MEDIBOT_VOZ_CANALES", 1)
        self.bits = 16
        self.dispositivo = dispositivo or elegir_salida()
        #  Por defecto solo se puede hablar desde la red de casa: un altavoz
        #  por el que cualquiera de internet pueda soltar voz dentro de tu
        #  casa no es algo que deba quedar abierto sin querer.
        self.solo_lan = _booleano("MEDIBOT_VOZ_SOLO_LAN", True)
        self.persistente = _booleano("MEDIBOT_VOZ_PERSISTENTE",
                                     ALTAVOZ_PERSISTENTE)
        #  Ganancia por software, por si la tarjeta no tiene control de
        #  volumen usable. Lo PRIMERO es subirla con amixer; esto es el
        #  remedio de ultima hora, y pasado de 2 empieza a saturar.
        self.ganancia = _decimal("MEDIBOT_VOZ_GANANCIA", 1.0)
        self.buffer_ms = _entero("MEDIBOT_VOZ_BUFFER_MS", 200)
        self.log = log
        self.proceso = None
        self._candado = threading.Lock()
        self._ultimo_audio = 0.0
        self._ultimo_seq = 0
        self._vigilante = None
        self._escritor = None
        #  Sin tope de items: el tope se lleva en BYTES (ver _encolar), que
        #  es lo que de verdad acota el retraso.
        self._cola = queue.Queue()
        self._bytes_cola = 0
        self._candado_cola = threading.Lock()
        self._bytes = 0           # cuanta voz se ha soltado (diagnostico)
        self._tirados = 0         # trozos tirados por ir demasiado atras
        self._fallo = ""

    def _tope_cola(self):
        """Cuantos BYTES de voz se guardan como mucho esperando turno.

        En bytes y no en numero de trozos: el tope existe para acotar el
        RETRASO, y un tope por trozos solo acota el retraso si todos miden
        lo mismo. Contando trozos, unos trozos mas pequenos de lo previsto
        hacian que se tirara voz muchisimo antes de tiempo."""
        por_segundo = self.hz * self.canales * (self.bits // 8)
        return max(4096, int(VOZ_COLA_SEGUNDOS * por_segundo))

    def _encolar(self, datos):
        """Mete un trozo y tira los VIEJOS si se pasa del tope."""
        with self._candado_cola:
            self._cola.put_nowait(datos)
            self._bytes_cola += len(datos)
            tope = self._tope_cola()
            #  qsize() > 1: nunca se tira el trozo que acaba de llegar. Uno
            #  solo mas grande que el tope se tiraba a si mismo nada mas
            #  entrar, asi que se aceptaba y no sonaba nunca.
            while self._bytes_cola > tope and self._cola.qsize() > 1:
                try:
                    viejo = self._cola.get_nowait()
                except queue.Empty:
                    break
                self._bytes_cola -= len(viejo)
                self._tirados += 1

    def _silencio(self):
        n = int(self.hz * SILENCIO_SEGUNDOS) * self.canales * (self.bits // 8)
        return b"\x00" * n

    def disponible(self):
        """Se puede intentar hablar. NO garantiza que se oiga: eso depende
        de que haya un altavoz enchufado y con volumen."""
        return bool(_booleano("MEDIBOT_VOZ", True)) and hay_aplay() \
            and self.dispositivo is not None

    def permite(self, ip, cabeceras=None):
        """(puede_hablar, motivo) segun de donde venga la peticion."""
        if not self.solo_lan:
            return True, ""
        if es_local(ip, cabeceras):
            return True, ""
        return False, ("solo se puede hablar desde la red de casa; desde "
                       "fuera (tunel) esta cerrado a proposito. Se abre con "
                       "MEDIBOT_VOZ_SOLO_LAN=0")

    def motivo_no_disponible(self):
        if not _booleano("MEDIBOT_VOZ", True):
            return ("hablar esta apagado a mano (MEDIBOT_VOZ="
                    f"{os.environ.get('MEDIBOT_VOZ', '').strip()}); quita esa "
                    "variable o ponla a 1 para poder hablar")
        if not hay_aplay():
            return ("falta 'aplay'; instalalo con: "
                    "sudo apt install alsa-utils")
        if self.dispositivo is None:
            return ("no se encontro ningun altavoz; comprueba 'aplay -l' y "
                    "fija MEDIBOT_ALTAVOZ_DISPOSITIVO si hace falta")
        return ""

    def orden(self):
        """El comando exacto. Aparte para poder comprobarlo sin ejecutarlo."""
        orden = ["aplay",
                 "-D", str(self.dispositivo),
                 "-f", f"S{self.bits}_LE",
                 "-r", str(self.hz),
                 "-c", str(self.canales),
                 "-t", "raw",          # PCM pelado: no lleva cabecera
                 "-q"]                 # sin cháchara por stderr
        #  Un colchon holgado en la tarjeta: al reproducir, quedarse corto se
        #  oye como chasquidos (underrun). Aqui 200 ms no molestan, porque el
        #  retraso que importa es el de escuchar, no el de hablar.
        if self.buffer_ms > 0:
            periodo_ms = max(10, self.buffer_ms // 4)
            orden += ["--buffer-time", str(self.buffer_ms * 1000),
                      "--period-time", str(periodo_ms * 1000)]
        return orden

    # --------------------------------------------------------- hablar ----
    def preparar(self):
        """Abre la tarjeta ANTES de que nadie hable. Devuelve (ok, motivo).

        Se llama al arrancar: asi el bus USB se reparte UNA vez, junto con
        las camaras, y no en mitad de una frase con el video en marcha."""
        if not self.persistente or not self.disponible():
            return False, self.motivo_no_disponible()
        with self._candado:
            try:
                self._asegurar()
                return True, ""
            except Exception as e:                           # noqa: BLE001
                self._fallo = f"no se pudo abrir el altavoz: {e}"
                return False, self._fallo

    def reproducir(self, datos, seq=None):
        """Encola un trozo de PCM para el altavoz. Devuelve (ok, motivo).

        NO ESCRIBE EN aplay: solo deja el trozo en la cola y vuelve. Antes
        escribia aqui mismo, con el candado cogido, asi que cualquier atasco
        de la tarjeta se comia el hilo de la peticion web; y con suficientes
        peticiones atascadas, el servidor que tambien sirve el video se
        quedaba sin sitio. De escribir se encarga un hilo aparte."""
        if not datos:
            return True, ""
        if len(datos) > MAX_TROZO_VOZ:
            return False, (f"trozo de {len(datos)} bytes: demasiado grande "
                           f"(el maximo son {MAX_TROZO_VOZ})")
        if not self.disponible():
            return False, self.motivo_no_disponible()

        with self._candado:
            if seq is not None:
                #  seq 1 es "empiezo a hablar": reinicia la cuenta.
                if seq <= 1:
                    self._ultimo_seq = 0
                elif seq <= self._ultimo_seq:
                    return True, "trozo atrasado o repetido; se descarta"
                self._ultimo_seq = seq
            try:
                self._asegurar()
            except Exception as e:                           # noqa: BLE001
                self._fallo = f"no se pudo abrir el altavoz: {e}"
                return False, self._fallo
            self._ultimo_audio = time.monotonic()

        #  Encolar sin bloquear NUNCA: si la cola esta llena es que el
        #  altavoz no da abasto, y en ese caso se tira lo viejo (la voz de
        #  hace un segundo ya no sirve) en vez de hacer esperar a la web.
        if self.ganancia != 1.0:
            datos = _con_ganancia(datos, self.ganancia, self.bits // 8)
        self._encolar(datos)
        return True, ""

    def _lanzar(self):
        """Arranca aplay. Aparte para poder sustituirlo en las pruebas por un
        doble sin tocar nada mas."""
        return subprocess.Popen(
            self.orden(), stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    def _asegurar(self):
        """Deja aplay y el hilo escritor en marcha. Con el candado cogido."""
        if self.proceso is None:
            self.proceso = self._lanzar()
            #  OJO: aqui NO se toca _ultimo_audio. Significa "cuando llego
            #  voz por ultima vez", y abrir la tarjeta no es que llegue voz:
            #  si se tocara, preparar() (que abre al arrancar, sin que nadie
            #  hable) dejaria el estado diciendo que esta sonando.
            #  Quien si lo pone al dia es reproducir(), en la misma seccion
            #  protegida, asi que el vigilante nunca ve un valor viejo.
        if self._escritor is None or not self._escritor.is_alive():
            self._escritor = threading.Thread(
                target=self._escribir_sin_parar, args=(self.proceso,),
                name="medibot-altavoz", daemon=True)
            self._escritor.start()
        if not self.persistente and self._vigilante is None:
            self._vigilante = threading.Thread(
                target=self._vigilar, name="medibot-voz", daemon=True)
            self._vigilante.start()

    def _descontar(self, datos):
        with self._candado_cola:
            self._bytes_cola = max(0, self._bytes_cola - len(datos))

    def _escribir_sin_parar(self, proceso):
        """Hilo: saca trozos de la cola y los mete en aplay. El UNICO que
        escribe en la tarjeta.

        LLEVA LA CUENTA DE LO QUE LE HA DADO. Se sabe exactamente a que
        velocidad consume aplay (hz x canales x ancho), asi que restando el
        tiempo transcurrido sale el COLCHON: cuantos segundos de audio le
        quedan a la tarjeta por delante.

          - Colchon holgado -> espera voz de verdad, sin rellenar.
          - Colchon a punto de agotarse -> suelta silencio YA.

        Sin esa cuenta, rellenar "cuando la cola tarda" mete silencio ENTRE
        trozos de voz y parte las palabras: la primera version hacia eso y
        la pureza del tono medido bajaba del 100% al 98%.

        El silencio no es para oirlo: es para que la tarjeta no se quede
        seca (eso se oye como chasquidos y cortes, el underrun) y para no
        tener que cerrarla, porque reabrirla renegocia el bus USB y es lo
        que tiraba la camara.

        La escritura BLOQUEA cuando el colchon de aplay se llena, y eso es
        justo lo que se quiere: marca el ritmo sola. Por eso va en su propio
        hilo y nunca en la peticion web."""
        silencio = self._silencio()
        por_segundo = float(self.hz * self.canales * (self.bits // 8))
        inicio = time.monotonic()
        entregados = 0

        #  Llenar el colchon con silencio ANTES de soltar nada de voz.
        #
        #  Hay que hacerlo aqui y no sobre la marcha: la voz llega a tiempo
        #  real, ni un byte de mas, asi que nunca puede llenar el colchon
        #  ella sola. Si se empieza con el colchon vacio, tras el primer
        #  trozo toca rellenar, y ese silencio cae DENTRO de la frase y se
        #  oye como un tartamudeo al empezar a hablar. Lleno de antemano, la
        #  voz entra seguida y no se rellena mas mientras siga llegando.
        #  CON TOPE. Si la tarjeta consume tan deprisa como se le escribe (un
        #  dispositivo sin apenas buffer), el colchon medido no sube nunca y
        #  sin tope esto giraria para siempre metiendo silencio: la voz no
        #  sonaria JAMAS. Con tope, lo peor que pasa es que no haya colchon,
        #  y el bucle de abajo ya reparte voz en cuanto la hay.
        for _ in range(int(COLCHON_ALTAVOZ / SILENCIO_SEGUNDOS) + 1):
            if entregados / por_segundo - (time.monotonic() - inicio) >= COLCHON_ALTAVOZ:
                break
            try:
                proceso.stdin.write(silencio)
                proceso.stdin.flush()
            except Exception:                                # noqa: BLE001
                break
            entregados += len(silencio)

        while True:
            colchon = entregados / por_segundo - (time.monotonic() - inicio)
            if colchon > COLCHON_ALTAVOZ:
                #  Va sobrada: esperar voz de verdad hasta que el colchon
                #  baje al objetivo. Si llega voz antes, entra al momento.
                try:
                    datos = self._cola.get(timeout=colchon - COLCHON_ALTAVOZ)
                except queue.Empty:
                    continue                      # a mirar el colchon otra vez
                self._descontar(datos)
            else:
                #  Se esta quedando seca: lo que haya, y si no hay nada,
                #  silencio ahora mismo.
                try:
                    datos = self._cola.get_nowait()
                    self._descontar(datos)
                except queue.Empty:
                    datos = silencio

            with self._candado:
                if self.proceso is not proceso:
                    return               # nos han jubilado: hay otro aplay
            try:
                proceso.stdin.write(datos)
                proceso.stdin.flush()
            except Exception as e:                           # noqa: BLE001
                moribundo = None
                with self._candado:
                    if self.proceso is proceso:
                        moribundo, self.proceso = self.proceso, None
                        self._escritor = None
                        self._fallo = (_ultimo_error(proceso)
                                       or f"se corto el altavoz: {e}")
                if moribundo is not None:
                    self._despedir(moribundo)
                return
            entregados += len(datos)
            if datos is not silencio:
                self._bytes += len(datos)

    def _vigilar(self):
        """Suelta el altavoz tras un rato sin voz. SOLO en modo no
        persistente: con la tarjeta persistente no hace falta, y es
        justamente lo que se quiere evitar (cada cierre obliga a renegociar
        el bus USB en la frase siguiente)."""
        while True:
            time.sleep(0.25)
            moribundo = None
            with self._candado:
                if self.proceso is None:
                    self._vigilante = None
                    return
                if time.monotonic() - self._ultimo_audio >= INACTIVO_VOZ:
                    moribundo, self.proceso = self.proceso, None
                    self._vigilante = None
                    self._escritor = None
            if moribundo is not None:
                #  Fuera del candado: despedirse tarda hasta 2 s y con el
                #  candado cogido bloquearia a quien vuelva a hablar.
                self._despedir(moribundo)
                return

    def cerrar(self):
        """Suelta el altavoz ya, sin esperar a nadie."""
        with self._candado:
            moribundo, self.proceso = self.proceso, None
            self._escritor = None
            self._vigilante = None
        #  Vaciar la cola: lo que quedara era para un altavoz que ya no esta.
        with self._candado_cola:
            while True:
                try:
                    self._cola.get_nowait()
                except queue.Empty:
                    break
            self._bytes_cola = 0
        self._despedir(moribundo)

    @staticmethod
    def _despedir(p):
        """Cierra aplay. NADA de aqui puede lanzar: se llama desde `finally`.

        Se cierra la ENTRADA primero y se espera: asi aplay termina de tocar
        lo que ya tiene guardado y sale solo. Matarlo a secas cortaria la
        ultima media palabra."""
        if p is None:
            return
        try:
            p.stdin.close()
        except Exception:                                    # noqa: BLE001
            pass
        try:
            p.wait(timeout=2)
        except Exception:                                    # noqa: BLE001
            try:
                p.terminate()
                p.wait(timeout=1)
            except Exception:                                # noqa: BLE001
                try:
                    p.kill()
                except Exception:                            # noqa: BLE001
                    pass
        try:
            if getattr(p, "stderr", None) is not None:
                p.stderr.close()
        except Exception:                                    # noqa: BLE001
            pass

    def estado(self):
        """Como va el altavoz. SIN COGER EL CANDADO, a proposito.

        La pagina pide /api/all cada segundo y ahi va esto. Si se esperase
        al candado, un aplay atascado (tarjeta ocupada, USB con problemas)
        dejaria la escritura bloqueada y con ella TODA la interfaz: los
        botones, los FPS y el estado de las camaras congelados por culpa del
        altavoz. Medido: 29 s de bloqueo.

        Leer los atributos sueltos puede dar una foto de hace un instante,
        que para un indicador de estado sobra."""
        #  'abierto' y 'sonando' son cosas distintas desde que la tarjeta se
        #  queda abierta: tenerla cogida NO significa que este saliendo voz.
        #  Con un solo campo, el diagnostico decia "sonando" todo el rato y
        #  no servia para nada.
        abierto = self.proceso is not None
        sonando = abierto and (self._bytes_cola > 0
                               or time.monotonic() - self._ultimo_audio < 1.0)
        fallo = self._fallo
        bytes_soltados = self._bytes
        por_segundo = self.hz * self.canales * (self.bits // 8)
        return {
            "disponible": self.disponible(),
            "motivo": self.motivo_no_disponible() or fallo,
            "dispositivo": self.dispositivo,
            "hz": self.hz,
            "canales": self.canales,
            "bits": self.bits,
            "abierto": abierto,          # la tarjeta esta cogida
            "sonando": sonando,          # esta saliendo voz ahora mismo
            "solo_lan": self.solo_lan,
            "persistente": self.persistente,
            "ganancia": self.ganancia,
            #  Lo que hay esperando turno, en segundos. Si esto crece y no
            #  baja, el altavoz no da abasto: se oiria como voz cortada.
            "en_cola": round(self._bytes_cola / float(por_segundo), 2),
            "tirados": self._tirados,
            "segundos": round(bytes_soltados / float(por_segundo), 1),
            "ultimo_fallo": fallo,
        }


if __name__ == "__main__":
    print("Microfonos que ve ALSA:")
    for d in dispositivos() or []:
        print(f"  {d['id']:<16} {d['nombre']}")
    m = Microfono()
    print("\nEstado:", m.estado())
    if m.disponible():
        print("Orden:", " ".join(m.orden()))
    else:
        print("No disponible:", m.motivo_no_disponible())

    print("\nAltavoces que ve ALSA:")
    for d in dispositivos_salida() or []:
        print(f"  {d['id']:<16} {d['nombre']}")
    a = Altavoz()
    print("\nEstado:", a.estado())
    if a.disponible():
        print("Orden:", " ".join(a.orden()))
        tarjeta = "1"
        if a.dispositivo and ":" in str(a.dispositivo):
            tarjeta = str(a.dispositivo).split(":")[1].split(",")[0]
        print("\nSi se oye bajo, sube el volumen DE LA TARJETA (no en el")
        print("codigo: ahi no gasta CPU ni mete ruido):")
        print(f"  amixer -c {tarjeta} scontrols          # que controles tiene")
        for control in ("PCM", "Speaker", "Master", "Headphone"):
            print(f"  amixer -c {tarjeta} sset '{control}' 100% unmute")
        print("  sudo alsactl store                 # que aguante el reinicio")
        print(f"  alsamixer -c {tarjeta}                   # o a ojo, con flechas")
        print("\nProbar que suena:")
        print(f"  speaker-test -D {a.dispositivo} -c {a.canales} -t sine -l 1")
        print("\nSolo si aun asi se oye bajo:  MEDIBOT_VOZ_GANANCIA=2.0")
    else:
        print("No disponible:", a.motivo_no_disponible())
