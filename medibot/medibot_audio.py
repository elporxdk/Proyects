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

VARIOS ESCUCHANDO A LA VEZ
--------------------------
ALSA no deja abrir el mismo microfono dos veces, asi que no se lanza un
`arecord` por navegador: se lanza UNO y se reparte. El movil y el PC pueden
escuchar a la vez y cada uno recibe el audio completo; que uno cierre la
pestana no corta a los demas, y el microfono se suelta cuando se va el
ULTIMO. Ver la clase Microfono.

LO QUE NO HACE
--------------
No manda audio del navegador HACIA el robot (hablar por un altavoz). Eso
necesita un altavoz conectado a la Pi y captura de microfono en el navegador,
que solo funciona en HTTPS. Se puede anadir despues sin tocar esto.

CONFIGURACION (variables de entorno)
------------------------------------
    MEDIBOT_AUDIO=1              activarlo (por defecto 0: desactivado)
    MEDIBOT_AUDIO_DISPOSITIVO    p.ej. plughw:1,0  (vacio = autodetectar)
    MEDIBOT_AUDIO_HZ=16000       frecuencia de muestreo
    MEDIBOT_AUDIO_CANALES=1      1 = mono (la C270 es mono)

Para ver que microfonos hay:   arecord -l
"""

import os
import queue
import re
import shutil
import struct
import subprocess
import threading

#  Tamano que se declara en la cabecera cuando el audio no termina nunca.
#  Es el maximo de un entero de 32 bits sin signo: el navegador entiende
#  "esto es larguisimo" y va reproduciendo segun llega.
TAM_STREAMING = 0xFFFFFFFF

#  Cuanto se lee de golpe del proceso de captura. A 16 kHz mono de 16 bits son
#  32000 bytes/s, asi que 4096 son ~128 ms: suficientemente pequeno para que
#  no se note retraso y suficientemente grande para no freir la CPU a
#  llamadas al sistema.
TROZO = 4096


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


def dispositivos(salida=None):
    """Lista los microfonos que ve ALSA, a partir de `arecord -l`.

    Se acepta 'salida' ya hecha para poder probarlo sin tarjeta de sonido."""
    if salida is None:
        if not hay_arecord():
            return []
        try:
            r = subprocess.run(["arecord", "-l"], capture_output=True,
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


# ------------------------------------------------------------- captura ----
#  Cuantos trozos se le guardan a cada oyente antes de empezar a tirar los
#  mas viejos. 16 trozos de 4096 bytes son ~2 s de audio a 16 kHz mono:
#  bastante para aguantar un bache de wifi y poco para que el retraso no se
#  vaya acumulando hasta oirse todo con medio minuto de diferencia.
COLA_MAXIMA = 16


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
        return bool(_booleano("MEDIBOT_AUDIO")) and hay_arecord() \
            and self.dispositivo is not None

    def motivo_no_disponible(self):
        if not _booleano("MEDIBOT_AUDIO"):
            return ("el audio esta desactivado; arranca con MEDIBOT_AUDIO=1 "
                    "para habilitarlo")
        if not hay_arecord():
            return ("falta 'arecord'; instalalo con: "
                    "sudo apt install alsa-utils")
        if self.dispositivo is None:
            return ("no se encontro ningun microfono; comprueba 'arecord -l' "
                    "y fija MEDIBOT_AUDIO_DISPOSITIVO si hace falta")
        return ""

    def orden(self):
        """El comando exacto. Aparte para poder comprobarlo sin ejecutarlo."""
        return ["arecord",
                "-D", str(self.dispositivo),
                "-f", f"S{self.bits}_LE",
                "-r", str(self.hz),
                "-c", str(self.canales),
                "-t", "raw",           # sin cabecera: la ponemos nosotros
                "-q"]                  # sin cháchara por stderr

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
        cola = queue.Queue(maxsize=COLA_MAXIMA)
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
        try:
            while True:
                datos = captura.proceso.stdout.read(TROZO)
                if not datos:
                    motivo = self._ultimo_error(captura.proceso) or \
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

    @staticmethod
    def _ultimo_error(proceso):
        """Lo que arecord dejo dicho por stderr, para poder explicarlo.

        Sin esto, una camara desenchufada a mitad o un microfono ocupado por
        otro programa se veria en la web como un 'no se pudo escuchar' pelado,
        y el motivo de verdad ("No such device", "Device or resource busy")
        se perderia.

        Se lee SOLO cuando arecord ya ha muerto: sobre un tubo que sigue
        abierto, read() se quedaria esperando para siempre y los oyentes no
        se enterarian nunca de que se acabo el audio (peticion colgada y
        boton de Escuchar encendido sin sonido)."""
        try:
            proceso.wait(timeout=1)
        except Exception:                                    # noqa: BLE001
            return ""            # sigue vivo: mejor sin motivo que colgados
        try:
            bruto = proceso.stderr.read() or b""
        except Exception:                                    # noqa: BLE001
            return ""
        texto = bruto.decode("utf-8", "replace").strip()
        #  arecord se explaya en varias lineas; la ultima es la que dice el
        #  problema de verdad.
        return texto.splitlines()[-1].strip() if texto else ""

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
