import cv2
import hashlib
import os
import shutil
import numpy as np
import threading
import socket
import time
import json
import webbrowser
import tkinter as tk
from tkinter import ttk, simpledialog, messagebox, scrolledtext
from PIL import Image, ImageTk
from flask import Flask, Response, render_template_string, jsonify, send_from_directory, request

# ================= SALIDA POR COM (via HUB serial) =================
# Movimiento y servos se controlan mandando ordenes al Arduino por el puerto COM
# a traves del hub serial (serial_hub.py). NO se usa RPi.GPIO: un sustituto
# (_DummyGPIO) traduce cada accion a una orden de texto que el hub reenvia.
# Formato (una por línea):
#       GPIO,<pin>,<0|1>     -> movimiento   (17=adel,27=atras,22=izq,23=der)
#       PWM,<pin>,<duty>     -> servo cámara  (18=pan, 13=tilt; ej. "PWM,18,7.5")
# Si el hub no está disponible, las órdenes se muestran por consola.


# Todas las ordenes (movimiento y servos) se envian al Arduino por COM a traves
# del HUB serial (serial_hub.py), para compartir el puerto con Pillbox sin conflicto.
SERIAL_BAUD = 9600      # informativo; el baud real lo fija el hub serial
_serial_last = {}       # último valor enviado por pin (evita repetir mensajes)

import medibot_serial   # cliente del hub serial compartido (serial_hub.py)
import medibot_protocolo as protocolo   # LA definicion del protocolo serie
import medibot_red      # IPs reales de la LAN (para entrar desde otro equipo)
import medibot_vision   # motor de video: buzon de frames y detectores rapidos
import medibot_audio    # microfono de la camara (escuchar desde la web)
import medibot_tls      # certificado propio: HTTPS en casa, sin nada de fuera

# Limitar los hilos internos de OpenCV: con un hilo por camara mas la interfaz
# y el servidor web, dejar que OpenCV use todos los nucleos provocaba peleas
# por CPU y una UI a tirones. Ver medibot_vision.ajustar_hilos_opencv().
#  Por defecto NO se toca la configuracion de hilos de OpenCV (automatica,
#  como siempre); se puede forzar con MEDIBOT_CV_THREADS=1 para experimentar.
medibot_vision.ajustar_hilos_opencv()

#  Perfilador del bucle de video: dice en NUMEROS cuanto tarda cada etapa
#  (leer de la camara, detectar, publicar). Se imprime cada pocos segundos y
#  sale en /api/all, para no tener que adivinar donde se van los FPS.
perfilador = {0: medibot_vision.Perfilador(), 1: medibot_vision.Perfilador()}
PERF_CADA_SEGUNDOS = float(os.environ.get("MEDIBOT_PERF_SEG", "5"))

# Buzon compartido de fotogramas (numerados, con JPEG cacheado). Sustituye a
# pasarse los frames por variables globales sueltas y recodificar por cliente.
frame_hub = medibot_vision.FrameHub()

def serial_connect():
    """Se asegura de que el hub serial (serial_hub.py) este corriendo. El hub es
    el unico dueno del puerto COM; Vision le envia sus ordenes por TCP."""
    if medibot_serial.ensure_hub():
        print(f"Hub serial disponible en {medibot_serial.HUB_HOST}:{medibot_serial.HUB_PORT}")
        return True
    print("AVISO: no se pudo iniciar el hub serial (serial_hub.py). "
          "Las órdenes se mostrarán por consola.")
    return False

#  Ultimo fallo de protocolo visto, para poder mostrarlo en la interfaz en
#  vez de dejarlo solo en la consola.
ultimo_error_serial = None


def serial_send(msg):
    """Envia una orden al Arduino a traves del hub serial y COMPRUEBA lo que
    contesta.

    ANTES ERA CIEGO: se mandaba y se tiraba la respuesta. Por eso nadie se
    entero durante meses de que el firmware contestaba  ERR,VEL,231  (no tenia
    ese comando) ni de que  MOVE,SPINL  paraba el robot devolviendo un OK
    falso. Ahora una respuesta ERR se registra y queda visible."""
    global ultimo_error_serial
    if not medibot_serial.hub_running():
        print(f"[SERIAL] {msg}")
        return False

    nombre = protocolo.nombre_de(msg)
    lineas = medibot_serial.send_command(
        msg, wait=protocolo.espera_de(nombre), until=protocolo.hasta_de(nombre))
    respuesta = protocolo.analizar(nombre, lineas)
    if not respuesta.ok and not respuesta.sin_respuesta:
        ultimo_error_serial = respuesta.error
        print(f"[SERIAL] {respuesta.error}")
        return False
    ultimo_error_serial = None
    return True

def _serial_pin(kind, pin, value):
    """Envía el estado de un pin solo cuando cambia (evita inundar el puerto)."""
    key = (kind, pin)
    if _serial_last.get(key) != value:
        _serial_last[key] = value
        serial_send(f"{kind},{pin},{value}")

class _DummyPWM:
    """Sustituto de GPIO.PWM: reenvía los cambios de ciclo por el puerto serial"""
    def __init__(self, pin=None, freq=None, *a, **k):
        self.pin = pin
    def start(self, duty=0, *a, **k):
        _serial_pin("PWM", self.pin, duty)
    def ChangeDutyCycle(self, duty, *a, **k):
        _serial_pin("PWM", self.pin, duty)
    def stop(self, *a, **k):
        _serial_pin("PWM", self.pin, 0)

class _DummyGPIO:
    """Sustituto de RPi.GPIO: reenvía cada orden por el puerto COM/serial"""
    BCM = "BCM"; BOARD = "BOARD"; OUT = "OUT"; IN = "IN"; HIGH = 1; LOW = 0
    def setmode(self, *a, **k): pass
    def setwarnings(self, *a, **k): pass
    def setup(self, *a, **k): pass
    def output(self, pin, value, *a, **k):
        _serial_pin("GPIO", pin, 1 if value else 0)
    def input(self, *a, **k): return 0
    def cleanup(self, *a, **k):
        serial_send("GPIO,CLEANUP,0")
    def PWM(self, pin=None, freq=None, *a, **k):
        return _DummyPWM(pin, freq)

# Todos los comandos (movimiento y servos de camara) se envian por COM al Arduino
# a traves del hub serial. Por eso NO se usa RPi.GPIO: se usa el sustituto que
# reenvia cada orden por el puerto COM (GPIO,<pin>,<val> y PWM,<pin>,<duty>).
GPIO = _DummyGPIO()
serial_connect()
# ================================================
from datetime import datetime
import subprocess
import sys

# ================= CONFIGURACIÓN =================
MAX_PERSONS = 5
IMAGES_PER_PERSON = 500

#  UMBRAL DEL RECONOCIMIENTO FACIAL (LBPH)
#  ---------------------------------------
#  OJO: LBPH NO devuelve un porcentaje de acierto. Devuelve una DISTANCIA en
#  la que MENOR ES MEJOR y sin techo definido. El valor anterior (700) dejaba
#  pasar practicamente cualquier cara como "AUTORIZADO", porque en la practica
#  una cara totalmente distinta rara vez supera 200.
#
#  COMO CALIBRARLO EN TU EQUIPO (no copies un numero de internet):
#    1. Entrena con tus personas registradas.
#    2. Arranca con el reconocimiento activado. Cada cara reconocida imprime
#       en consola su distancia real:
#           [lbph] distancia=42.7 umbral=80 -> Ana
#    3. Ponte tu delante: anota las distancias (seran las BAJAS).
#       Que se ponga alguien no registrado: anota las suyas (seran ALTAS).
#    4. Elige un umbral entre ambos grupos, mas cerca del grupo bajo.
#           MEDIBOT_LBPH_UMBRAL=65 python3 medibot/main.py
#  El valor por defecto es un PUNTO DE PARTIDA conservador, no una verdad
#  universal: depende de tu camara, tu luz y tus 500 imagenes por persona.
CONF_LIMIT = float(os.environ.get("MEDIBOT_LBPH_UMBRAL", "80"))

#  Resolucion de CAPTURA y ANALISIS (lo que se pide al driver).
FRAME_W = int(os.environ.get("MEDIBOT_CAM_W", "640"))
FRAME_H = int(os.environ.get("MEDIBOT_CAM_H", "480"))

#  Tamano del panel de video DENTRO de la ventana Tkinter. Ya NO limita lo que
#  ve la web: antes se publicaba el fotograma reducido a este tamano y el
#  navegador lo re-ampliaba, de ahi la imagen blanda. Ahora se publica a
#  resolucion completa y cada consumidor (Tk, web) escala a lo que necesita.
VIEW_W = int(os.environ.get("MEDIBOT_GUI_W", "400"))
VIEW_H = int(os.environ.get("MEDIBOT_GUI_H", "300"))

ZONE_X = FRAME_W // 3
ZONE_Y = FRAME_H // 3

PWM_X, PWM_Y = 18, 13

# Pines de movimiento del robot (joystick W/A/S/D)
MOVE_PINS = {"w": 17, "s": 27, "a": 22, "d": 23}

DATA_PATH = "data"
VIDEO_PATH = "videos"
# ================================================

# ================= CONFIGURACIÓN DE CÁMARAS ============
def _indice_camara(variable, defecto):
    """Indice de camara desde el entorno. Vacio o negativo = camara apagada."""
    bruto = os.environ.get(variable, "").strip()
    if not bruto:
        return defecto
    try:
        valor = int(bruto)
    except ValueError:
        print(f"[cam] {variable}={bruto!r} no es un entero; uso {defecto}")
        return defecto
    return None if valor < 0 else valor


CAMERA_INDICES = [_indice_camara("MEDIBOT_CAM0", 0),
                  _indice_camara("MEDIBOT_CAM1", 1)]

#  Perfiles de captura y de emision web. Son INDEPENDIENTES: se puede analizar
#  a 640x480 y emitir a 480x360 con calidad 55 cuando la red o la CPU aprietan.
PERFIL_CAPTURA = medibot_vision.PerfilCaptura.desde_entorno()
PERFIL_WEB = medibot_vision.PerfilWeb.desde_entorno()

#  Informacion REAL de cada camara (lo que el driver acepto, no lo que se
#  pidio). Se rellena en initialize_cameras() y se publica en /api/all.
info_camaras = {0: None, 1: None}

#  Detectores caros que se pueden apagar sin editar codigo. Por defecto ambos
#  quedan como estaban (encendidos), asi el comportamiento no cambia salvo que
#  se pida expresamente.
#    MEDIBOT_DETECT_ROJO=0  -> ahorra ~1,3 ms/frame (cvtColor HSV + morfologia)
#    MEDIBOT_OVERLAY=0      -> ahorra los textos dibujados sobre el video
#  DETECCION_ROJO se puede cambiar EN CALIENTE desde la web (boton "Color
#  rojo" y ruta /toggle_deteccion_rojo). La variable de entorno solo fija con
#  que valor arranca. Antes solo se podia apagar reiniciando el programa con
#  MEDIBOT_DETECT_ROJO=0, que en una Raspberry a la que se entra por SSH es
#  bastante incomodo cuando lo que quieres es ver el efecto al momento.
DETECCION_ROJO = medibot_vision.leer_booleano("MEDIBOT_DETECT_ROJO", True)
OVERLAYS_ACTIVOS = medibot_vision.leer_booleano("MEDIBOT_OVERLAY", True)
# ================================================

# ================= SERVOS DE CAMARA (pan/tilt) =================
#  Este robot NO lleva soporte pan/tilt: la camara va fija. Con esto en False
#  no se envia ni una sola orden PWM al Arduino, que es lo correcto porque:
#    - No hay servos que mover: seria trafico inutil.
#    - Ese puerto serie lo COMPARTEN Vision y el dispensador (via serial_hub),
#      asi que cada orden de sobra le quita turno a un DISPENSE.
#  El firmware del Arduino tambien los tiene desactivados
#  (#define USAR_SERVOS_CAMARA 0), asi que ambos lados coinciden.
#  Ponlo en True si algun dia montas el soporte pan/tilt.
USAR_SERVOS_CAMARA = False

GPIO.setmode(GPIO.BCM)
if USAR_SERVOS_CAMARA:
    GPIO.setup(PWM_X, GPIO.OUT)
    GPIO.setup(PWM_Y, GPIO.OUT)
    pwm_x = GPIO.PWM(PWM_X, 50)
    pwm_y = GPIO.PWM(PWM_Y, 50)
    pwm_x.start(7.5)
    pwm_y.start(7.5)
else:
    pwm_x = pwm_y = None

# ---- Pines de movimiento (joystick W/A/S/D) ----
for _mpin in MOVE_PINS.values():
    GPIO.setup(_mpin, GPIO.OUT, initial=GPIO.LOW)

# ================= MOVIMIENTO DEL CHASIS =================
#  Los SEIS movimientos del robot, con los MISMOS nombres que entiende el
#  firmware (tabla MAPA_DIRECCIONES del sketch). Una sola lista de nombres
#  compartida entre web, teclado y Arduino es lo que impide que los tres se
#  desincronicen.
#
#  La diferencia entre estos dos pares es real en este robot:
#    IZQUIERDA / DERECHA  -> se desplaza de lado SIN cambiar de orientacion
#    GIRO_IZQ / GIRO_DER  -> gira sobre su propio eje SIN desplazarse
#  Los nombres vienen del PROTOCOLO, no escritos otra vez aqui. Tenerlos
#  duplicados fue lo que permitio que Vision enviara SPINL/SPINR durante meses
#  mientras el firmware no los conocia: dos listas separadas que nadie
#  comparaba. Ahora hay una sola, y las pruebas verifican que el firmware
#  implementa exactamente esa.
ADELANTE  = protocolo.ADELANTE
ATRAS     = protocolo.ATRAS
IZQUIERDA = protocolo.IZQUIERDA
DERECHA   = protocolo.DERECHA
GIRO_IZQ  = protocolo.GIRO_IZQ
GIRO_DER  = protocolo.GIRO_DER
PARADO    = protocolo.PARADO

MOVIMIENTOS = protocolo.MOVIMIENTOS
ETIQUETAS_MOVIMIENTO = protocolo.ETIQUETAS_MOVIMIENTO

#  Teclas del joystick -> movimiento. W/A/S/D como siempre; Q y E se anaden
#  para los giros sobre el eje, que antes no se podian pedir.
TECLAS_MOVIMIENTO = {
    "w": ADELANTE, "s": ATRAS, "a": IZQUIERDA, "d": DERECHA,
    "q": GIRO_IZQ, "e": GIRO_DER,
}

#  Diagonales: misma interpretacion que hace el firmware (adelante + lateral =
#  giro sobre el eje), para no tener dos criterios distintos.
_DIAGONALES = {
    frozenset(("w", "a")): GIRO_IZQ, frozenset(("w", "d")): GIRO_DER,
    frozenset(("s", "a")): GIRO_IZQ, frozenset(("s", "d")): GIRO_DER,
}

movement_state = {k: False for k in TECLAS_MOVIMIENTO}
movimiento_actual = PARADO


def movimiento_desde_teclas(teclas):
    """Traduce las teclas pulsadas al movimiento correspondiente. Una sola
    funcion decide esto para el teclado, la web y los botones."""
    pulsadas = frozenset(t for t in teclas if t in TECLAS_MOVIMIENTO)
    if not pulsadas:
        return PARADO
    if pulsadas in _DIAGONALES:
        return _DIAGONALES[pulsadas]
    if len(pulsadas) == 1:
        return TECLAS_MOVIMIENTO[next(iter(pulsadas))]
    return PARADO          # combinacion contradictoria -> parar, por seguridad


def enviar_movimiento(mov):
    """Manda el movimiento al Arduino, SOLO si cambia respecto al anterior.

    Se usa MOVE,<dir> en lugar del viejo protocolo de cuatro pines GPIO porque
    nombra el movimiento explicitamente: con pines habia que deducir 'adelante
    + izquierda = giro', y web y firmware podian interpretarlo distinto. Ademas
    los giros sobre el eje ni siquiera eran representables con cuatro pines.

    Mandar solo los cambios evita inundar el puerto serie, que Vision comparte
    con el dispensador a traves del hub."""
    global movimiento_actual
    if mov not in MOVIMIENTOS or mov == movimiento_actual:
        return mov in MOVIMIENTOS
    movimiento_actual = mov
    #  Se construye con el protocolo: si alguien anade un movimiento a la
    #  lista de Vision sin implementarlo en el firmware, salta aqui en vez de
    #  producir un robot que se para sin decir nada.
    serial_send(protocolo.cmd_mover(mov))
    return True


def apply_movement():
    """Refleja movement_state en el robot."""
    enviar_movimiento(movimiento_desde_teclas(
        [k for k, v in movement_state.items() if v]))


def set_movement(directions):
    """Activa las direcciones indicadas (iterable de teclas) y apaga el resto."""
    for _d in movement_state:
        movement_state[_d] = _d in directions
    apply_movement()


def detener_movimiento():
    """Para el chasis y limpia el estado del joystick."""
    for _d in movement_state:
        movement_state[_d] = False
    enviar_movimiento(PARADO)


# ---- Velocidad del chasis ----
#  El firmware la limita a 200..255: por debajo de 200 estos motores con
#  reductora apenas arrancan con carga. Se replica aqui el mismo rango para
#  poder avisar al usuario antes de mandar un valor que se iba a recortar.
VEL_MIN, VEL_MAX = protocolo.VEL_MIN, protocolo.VEL_MAX
velocidad_chasis = VEL_MIN


def fijar_velocidad(valor):
    """Ajusta la velocidad del chasis (200..255). Devuelve la aplicada, o None
    si el valor no es un numero."""
    global velocidad_chasis
    try:
        v = int(valor)
    except (TypeError, ValueError):
        return None
    velocidad_chasis = max(VEL_MIN, min(VEL_MAX, v))
    serial_send(protocolo.cmd_velocidad(velocidad_chasis))
    return velocidad_chasis


def lanzar_truco(numero):
    """Lanza uno de los movimientos especiales (1..4) del Arduino: los mismos
    que los botones Triangulo/Circulo/Cuadrado/X del mando PS2."""
    try:
        n = int(numero)
    except (TypeError, ValueError):
        return False
    if not protocolo.TRUCO_MIN <= n <= protocolo.TRUCO_MAX:
        return False
    serial_send(protocolo.cmd_truco(n))
    return True


# Ultimo ciclo de trabajo ESCRITO en cada servo.
#  POR QUE: estas funciones se llaman en el bucle de video, o sea decenas de
#  veces por segundo, casi siempre con el MISMO valor (p.ej. centrar una y otra
#  vez mientras no hay nada que seguir). Cada ChangeDutyCycle es una llamada al
#  driver PWM; repetirla sin que el valor cambie no mueve el servo, solo gasta
#  CPU y le mete nerviosismo. Guardando el ultimo valor solo se escribe cuando
#  DE VERDAD cambia: el servo termina exactamente en la misma posicion.
_pwm_x_actual = None
_pwm_y_actual = None


def _escribir_pwm_x(valor):
    global _pwm_x_actual
    if not USAR_SERVOS_CAMARA:
        return                      # camara fija: no hay nada que mover
    if valor != _pwm_x_actual:
        pwm_x.ChangeDutyCycle(valor)
        _pwm_x_actual = valor


def _escribir_pwm_y(valor):
    global _pwm_y_actual
    if not USAR_SERVOS_CAMARA:
        return
    if valor != _pwm_y_actual:
        pwm_y.ChangeDutyCycle(valor)
        _pwm_y_actual = valor


def center_pwm():
    """Centrar la camara. Con USAR_SERVOS_CAMARA=False no hace nada (la camara
    va fija), pero se mantiene la llamada para no cambiar el flujo del programa
    si algun dia se monta el soporte pan/tilt."""
    _escribir_pwm_x(7.5)
    _escribir_pwm_y(7.5)

def move_servos(x_pos, y_pos):
    """Mueve los servomotores segun la posición del rostro u objeto seguido.
    Con USAR_SERVOS_CAMARA=False no hace nada: el seguimiento se sigue
    calculando y mostrando en el estado, pero la camara no se mueve."""
    if x_pos == "left":
        _escribir_pwm_x(5.5)
    elif x_pos == "right":
        _escribir_pwm_x(9.5)
    else:
        _escribir_pwm_x(7.5)

    if y_pos == "up":
        _escribir_pwm_y(5.5)
    elif y_pos == "down":
        _escribir_pwm_y(9.5)
    else:
        _escribir_pwm_y(7.5)

# ================= OPTIMIZADOR DE CÁMARA =========
class CameraOptimizer:
    """Ajustes de imagen de la camara.

    POR QUE SE REESCRIBIO: la version anterior guardaba los valores en una
    escala 0..1 (brightness=0.5, saturation=0.5...) y los mandaba tal cual con
    camera.set(). Pero V4L2 usa el rango NATIVO del dispositivo en enteros: en
    una Logitech C270 la saturacion va de 0 a 255 (por defecto 32), asi que ese
    0.5 llegaba al driver como 0.

        saturacion 0  =  imagen SIN COLOR  ->  se veia gris
        contraste  0  =  imagen plana      ->  se veia lavada
        brillo     0  =  lo mas oscuro

    Y no saltaba ningun aviso porque habia un  except: pass  desnudo, nadie
    miraba el booleano que devuelve set(), y nadie volvia a leer el valor para
    comprobar si se habia aplicado. Ademas se ejecutaba en CADA arranque.

    AHORA: por defecto NO SE TOCA NADA (los valores de fabrica de la camara dan
    una imagen correcta), los valores van en unidades del dispositivo, y cada
    intento se verifica releyendo del driver. La logica esta en
    medibot_vision.ControlesCamara, que se puede probar sin camara.

    Se conservan los nombres de atributos y metodos porque los usan /api/all,
    /optimize_camera y /update_camera_setting."""

    def __init__(self):
        self.controles = medibot_vision.ControlesCamara.desde_entorno()
        #  Lo que el driver dice tener de verdad. Se rellena al aplicar y es lo
        #  que se publica: antes /api/all mostraba los 0.5 inventados, que no
        #  se correspondian con nada de lo que la camara tenia puesto.
        self.reales = {}

    # ---- Atributos que espera la API publica ---------------------------
    def _valor(self, nombre):
        real = self.reales.get(nombre)
        return real if real is not None else self.controles.pedido(nombre)

    @property
    def brightness(self):
        return self._valor("brightness")

    @property
    def contrast(self):
        return self._valor("contrast")

    @property
    def saturation(self):
        return self._valor("saturation")

    @property
    def sharpness(self):
        return self._valor("sharpness")

    @property
    def exposure(self):
        return self._valor("exposure")

    # ---- Operaciones ---------------------------------------------------
    def auto_adjust(self, frame):
        """Ya NO toca la camara.

        La version anterior movia brightness/contrast en la escala 0..1 sin
        llegar a llamar nunca a apply_settings(), asi que su unico efecto era
        empeorar los valores que se enviarian la proxima vez que alguien
        pulsara "Optimizar camara". Un autoajuste util tendria que trabajar en
        las unidades del dispositivo y conocer el rango real, que OpenCV no
        expone; mientras tanto, la exposicion automatica de la propia camara
        hace mejor ese trabajo.

        Se conserva la firma porque el bucle de video la llama."""
        return frame

    def apply_settings(self, camera, indice=None):
        """Aplica los controles PEDIDOS y comprueba que se aplicaron."""
        informe = self.controles.aplicar(camera, indice=indice)
        # Publicar lo que la camara tiene REALMENTE, se haya tocado o no.
        self.reales = {k: v for k, v in self.controles.leer(camera).items()
                       if v is not None}
        return informe

    def manual_adjust(self, setting, value):
        """Ajuste manual, EN UNIDADES DEL DISPOSITIVO (p.ej. 0..255).
        Lanza ValueError si el control no existe."""
        return self.controles.fijar(setting, value)

    def estado(self):
        """Lo que hay que saber para diagnosticar una imagen fea."""
        return {
            "pedidos": dict(self.controles.ajustes),
            "reales": dict(self.reales),
            "resumen": self.controles.resumen(),
            "nota": ("Valores en UNIDADES DEL DISPOSITIVO (v4l2-ctl "
                     "--list-ctrls), no en 0..1. Sin variables de entorno no "
                     "se toca ningun control."),
        }


camera_optimizer = CameraOptimizer()

# ================= SEGUIMIENTO DE COORDENADAS =========
class ObjectTracker:
    """Seguimiento de objetos rojos.

    FUGA DE MEMORIA CORREGIDA: la identidad de un objeto se calculaba como
    "centroX_centroY_area", asi que un objeto que se mueve (o cuyo area baila
    un pixel por el ruido de la camara) generaba una ENTRADA NUEVA en cada
    fotograma. A 30 FPS eso son ~1800 entradas por minuto y por objeto, cada
    una con sus listas de historial y trayectoria; solo se borraban 5 s
    despues, y mientras tanto la RAM subia sin parar y get_tracking_data()
    (que recorre TODO el diccionario en cada peticion de la API web) se volvia
    cada vez mas lenta.

    Ahora la identidad se basa en una REJILLA: se redondea el centro a celdas
    de CELDA_PX pixeles, asi el mismo objeto conserva su id mientras se mueve
    despacio, el historial es continuo (que es lo que se queria dibujar) y el
    diccionario deja de crecer. Ademas se limita el numero de objetos vivos.
    """

    CELDA_PX = 40          # tolerancia de movimiento para considerarlo "el mismo"
    MAX_OBJETOS = 32       # tope duro: nunca crece sin control

    def __init__(self, max_history=50):
        self.object_history = {}
        self.max_history = max_history
        self.tracking_enabled = True

    @classmethod
    def _id_objeto(cls, obj):
        """Identidad estable frente a pequenos movimientos y ruido de area."""
        return f"{obj['center_x'] // cls.CELDA_PX}_{obj['center_y'] // cls.CELDA_PX}"

    def update_tracking(self, objects, frame_time):
        """Actualiza el historial de seguimiento de objetos"""
        for obj in objects:
            obj_id = self._id_objeto(obj)

            if obj_id not in self.object_history:
                self.object_history[obj_id] = {
                    'id': obj_id,
                    'color': obj['color'],
                    'history': [],
                    'first_seen': frame_time,
                    'last_seen': frame_time,
                    'total_frames': 1,
                    'path': []
                }
            
            # Agregar posición al historial
            self.object_history[obj_id]['history'].append({
                'time': frame_time,
                'x': obj['center_x'],
                'y': obj['center_y'],
                'area': obj['area']
            })
            
            # Agregar a la trayectoria
            self.object_history[obj_id]['path'].append((obj['center_x'], obj['center_y']))
            
            # Mantener solo el historial máximo
            if len(self.object_history[obj_id]['history']) > self.max_history:
                self.object_history[obj_id]['history'].pop(0)
            
            if len(self.object_history[obj_id]['path']) > self.max_history:
                self.object_history[obj_id]['path'].pop(0)
                
            self.object_history[obj_id]['last_seen'] = frame_time
            self.object_history[obj_id]['total_frames'] += 1
        
        # Limpiar objetos antiguos (no vistos por mas de 5 segundos).
        #  Se usa frame_time (ya calculado por quien llama) en vez de pedir
        #  time.time() otra vez en cada fotograma.
        caducados = [oid for oid, d in self.object_history.items()
                     if frame_time - d['last_seen'] > 5]
        for obj_id in caducados:
            del self.object_history[obj_id]

        # Red de seguridad: si por lo que sea hubiera demasiados objetos vivos,
        # conservar solo los mas recientes. Asi la memoria tiene un techo fijo.
        if len(self.object_history) > self.MAX_OBJETOS:
            por_antiguedad = sorted(self.object_history.items(),
                                    key=lambda kv: kv[1]['last_seen'], reverse=True)
            self.object_history = dict(por_antiguedad[:self.MAX_OBJETOS])
    
    def get_tracking_data(self):
        """Obtiene datos de seguimiento para la API usando json"""
        return [
            {
                'id': data['id'],
                'color': data['color'],
                'first_seen': data['first_seen'],
                'last_seen': data['last_seen'],
                'total_frames': data['total_frames'],
                'current_position': data['history'][-1] if data['history'] else None,
                'path': data['path'][-10:] if len(data['path']) > 10 else data['path']
            }
            for data in self.object_history.values()
        ]
    
    def draw_tracking(self, frame, objects):
        """Dibuja las trayectorias de seguimiento poniendo cuadritos"""
        if not self.tracking_enabled:
            return frame
            
        for obj in objects:
            obj_id = self._id_objeto(obj)

            if obj_id in self.object_history:
                path = self.object_history[obj_id]['path']
                
                # Dibujar trayectoria
                for i in range(1, len(path)):
                    cv2.line(frame, path[i-1], path[i], (0, 255, 0), 2)
                
                # Dibujar información
                if len(path) > 0:
                    cv2.putText(frame, f"ID: {obj_id[:8]}", 
                               (obj['x'] - 30, obj['y'] - 40),
                               cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
                    cv2.putText(frame, f"Frames: {self.object_history[obj_id]['total_frames']}", 
                               (obj['x'] - 30, obj['y'] - 25),
                               cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
        
        return frame

object_tracker = ObjectTracker()

# ================= UTILIDADES ====================
def get_ip():
    """IP de ESTE equipo por la que pueden entrar OTROS dispositivos.

    Antes esto abría un UDP a 8.8.8.8 y, si la Pi no tenía salida a internet,
    devolvía '127.0.0.1': una dirección que solo funciona en la propia máquina,
    por eso "la IP no se podía abrir desde el móvil". Ahora se enumeran las
    interfaces de red de verdad (ver medibot_red.py).

    Se mantiene el texto '127.0.0.1' como último recurso porque esta función se
    usa dentro de URLs ya formadas; cuando no hay red, get_ip_lista() y los
    avisos de arranque explican el problema."""
    return medibot_red.ip_lan_principal() or "127.0.0.1"

# Cache de la lista de personas registradas.
#  POR QUE: get_registered_persons() recorre carpetas y cuenta ficheros (E/S de
#  disco). Se llamaba DENTRO del bucle de reconocimiento, o sea varias veces por
#  segundo por cada cara detectada: en una Raspberry con tarjeta SD eso frena
#  todo el pipeline. Ahora el resultado se guarda 2 s y se invalida al registrar
#  o borrar a alguien, asi que la lista sigue estando siempre al dia.
_persons_cache = None
_persons_cache_time = 0.0
_persons_cache_lock = threading.Lock()
PERSONS_CACHE_TTL = 2.0


def invalidar_cache_personas():
    """Fuerza releer el disco (tras registrar/eliminar a alguien)."""
    global _persons_cache
    with _persons_cache_lock:
        _persons_cache = None


def _leer_personas_del_disco():
    if not os.path.exists(DATA_PATH):
        return []
    persons = []
    for folder in os.listdir(DATA_PATH):
        folder_path = os.path.join(DATA_PATH, folder)
        if os.path.isdir(folder_path):
            images = len([f for f in os.listdir(folder_path) if f.endswith('.jpg')])
            persons.append({"name": folder, "images": images})
    return persons


def get_registered_persons():
    """Lista de personas registradas (cacheada; misma salida que antes)."""
    global _persons_cache, _persons_cache_time
    ahora = time.time()
    with _persons_cache_lock:
        if _persons_cache is not None and ahora - _persons_cache_time < PERSONS_CACHE_TTL:
            return _persons_cache
    personas = _leer_personas_del_disco()
    with _persons_cache_lock:
        _persons_cache = personas
        _persons_cache_time = ahora
    return personas

def setup_directories():
    """Crea los directorios necesarios si no esta hechos"""
    if not os.path.exists(DATA_PATH):
        os.makedirs(DATA_PATH)
    if not os.path.exists(VIDEO_PATH):
        os.makedirs(VIDEO_PATH)
    # Crear subdirectorios para cada cámara
    if not os.path.exists(os.path.join(VIDEO_PATH, "camara1")):
        os.makedirs(os.path.join(VIDEO_PATH, "camara1"))
    if not os.path.exists(os.path.join(VIDEO_PATH, "camara2")):
        os.makedirs(os.path.join(VIDEO_PATH, "camara2"))

def get_video_files():
    """Obtiene y ordena los archivos de video grabados"""
    video_files = []
    for root, dirs, files in os.walk(VIDEO_PATH):
        for file in files:
            if file.endswith('.avi'):
                full_path = os.path.join(root, file)
                stats = os.stat(full_path)
                video_files.append({
                    "path": full_path,
                    "name": file,
                    "size": stats.st_size,
                    "created": stats.st_ctime,
                    "camera": "camara1" if "camara1" in root else "camara2"
                })
    
    # Ordenar por fecha de creación (más reciente primero)
    video_files.sort(key=lambda x: x["created"], reverse=True)
    return video_files

# ================= DETECCIÓN Y SEGUIMIENTO DE COLOR ROJO =========
# La deteccion vive ahora en medibot_vision.RedDetector. Ahorra reservar los
# rangos HSV y el kernel morfologico en CADA fotograma (antes se creaban 5
# arrays de numpy por frame) y dibuja sobre el frame recibido, sin la copia
# extra que se hacia antes. La salida es identica (verificado).
_red_detector = medibot_vision.RedDetector(area_minima=300)


def detect_red_objects(frame):
    """Detecta objetos rojos y los marca en el frame.
    Devuelve (frame, objetos) — misma firma y salida que antes."""
    objetos = _red_detector.detectar(frame, dibujar=True)
    return frame, objetos


# ================= RECONOCIMIENTO FACIAL =========
# Intentar cargar el clasificador del Haarscascade
cascade_paths = [
    "haarcascade_frontalface_default.xml",
    "/usr/share/opencv4/haarcascades/haarcascade_frontalface_default.xml",
    "/usr/local/share/opencv4/haarcascades/haarcascade_frontalface_default.xml"
]

cascade = None
for path in cascade_paths:
    if os.path.exists(path):
        cascade = cv2.CascadeClassifier(path)
        break

if cascade is None:
    print("ADVERTENCIA: No se encontró el clasificador de rostros. El sistema funcionará solo con detección de color. LOL")

recognizer = None

#  MAPA ESTABLE id -> nombre (se guarda al entrenar, se lee al reconocer).
#  POR QUE: antes el nombre salia de persons[id_] con la lista de carpetas de
#  disco. os.listdir NO garantiza orden, y aunque lo garantizara, dar de alta
#  o borrar a alguien DESPLAZA los indices: el modelo seguia diciendo "id 2"
#  pero la lista ya apuntaba a otra persona. Resultado: el robot autorizaba
#  con el nombre equivocado. Ahora el mapa viaja junto al modelo.
etiquetas_lbph = {}

#  Ultimas distancias observadas: es lo que hace falta para CALIBRAR el umbral
#  sin adivinar. Se publican en /api/all -> reconocimiento.distancias.
_distancias_lbph = []
_distancias_lock = threading.Lock()


def cargar_reconocedor():
    """Carga trainer.yml Y su mapa de etiquetas, o devuelve None.

    Un unico sitio para esto: antes se repetia en cuatro lugares (boton de la
    GUI, boton de la web, arranque y toggle), y ahora ademas hay que cargar el
    mapa de nombres junto al modelo. Cuatro copias eran cuatro sitios donde
    olvidarlo."""
    if not os.path.exists("trainer.yml"):
        return None
    if not hasattr(cv2, "face"):
        print("AVISO: esta instalacion de OpenCV no trae el modulo 'face' "
              "(opencv-contrib-python). El reconocimiento queda desactivado.")
        return None
    try:
        modelo = cv2.face.LBPHFaceRecognizer_create()
        modelo.read("trainer.yml")
    except cv2.error as e:
        print(f"Error cargando modelo de reconocimiento: {e}")
        return None
    cargar_etiquetas_lbph()
    return modelo


def cargar_etiquetas_lbph():
    """Relee el mapa id -> nombre del disco. Se llama al cargar el modelo."""
    global etiquetas_lbph
    etiquetas_lbph = medibot_vision.cargar_mapa_etiquetas()
    if etiquetas_lbph:
        print(f"Mapa de etiquetas cargado: {len(etiquetas_lbph)} personas")
    elif os.path.exists("trainer.yml"):
        print("AVISO: hay trainer.yml pero no trainer_labels.json. "
              "El modelo es anterior a este cambio: vuelve a entrenar para "
              "que los nombres dejen de depender del orden del disco.")
    return etiquetas_lbph


def nombre_de_etiqueta(id_):
    """Nombre de la persona a partir del id que devuelve LBPH.

    Sin mapa (modelo antiguo) NO se inventa un nombre a partir del orden del
    disco: se muestra el id, que es la verdad disponible."""
    if id_ in etiquetas_lbph:
        return etiquetas_lbph[id_]
    return f"ID {id_}"


def _registrar_distancia_lbph(distancia, nombre):
    """Anota la distancia observada (para calibrar) y la imprime.

    Es la herramienta de calibracion del umbral: con el reconocimiento
    activado se ve en consola la distancia REAL de cada cara, que es el dato
    que hace falta para elegir MEDIBOT_LBPH_UMBRAL con criterio."""
    etiqueta = nombre or "DESCONOCIDO"
    print(f"[lbph] distancia={distancia:.1f} umbral={CONF_LIMIT:.0f} -> {etiqueta}")
    with _distancias_lock:
        _distancias_lbph.append({"distancia": round(float(distancia), 1),
                                 "nombre": etiqueta, "t": time.time()})
        del _distancias_lbph[:-20]      # solo las 20 ultimas: no crece


def distancias_lbph_recientes():
    with _distancias_lock:
        return list(_distancias_lbph)


# Variables globales para ambas cámaras
camera1 = None
camera2 = None
camera1_thread = None
camera2_thread = None
active_camera_index = 0
# Los antiguos last_frame / last_frame1 / last_frame2 desaparecieron: el ultimo
# fotograma de cada camara vive ahora en frame_hub, que ademas lo numera para
# que la interfaz y el streaming no repitan trabajo ya hecho.
online = False
recording = False
recording_cam1 = False
recording_cam2 = False
video_writer1 = None
video_writer2 = None
system_status = "Inactivo"
detection_count = 0
capture_mode = False
recognition_enabled = False  # La cámara arranca SIN reconocer; se activa a petición
current_capture_id = None
current_capture_name = None
captured_images = 0
face_position = {"x": "center", "y": "center"}
last_face_time = 0
detected_red_objects = []
#  FPS de captura publicados en la GUI y en las APIs. El conteo lo llevan
#  ahora los medidor_captura[...] (ver read_frame_from_camera); estas dos
#  variables se mantienen porque las leen la GUI, /api/all, /api/esp32 y
#  /stats, y romperlas romperia la API publica.
fps1 = 0
fps2 = 0
video_files = []

# ================= MANEJO DE CÁMARAS MÚLTIPLES SIMULTÁNEAS =========
def initialize_cameras():
    """Inicializa ambas cámaras y REGISTRA lo que el driver aceptó de verdad.

    QUE CAMBIA respecto a la version anterior y por que importa:

    1. ORDEN DE NEGOCIACION. Antes se fijaba el FOURCC (MJPG) DESPUES de la
       resolucion. En V4L2 eso hace que el driver renegocie el formato y puede
       dejar la captura en YUYV sin comprimir. YUYV a 640x480 son ~614 KB por
       fotograma; por USB 2.0 solo caben ~9-10 FPS. Es exactamente el sintoma
       "la camara pierde FPS". Ahora se fija FOURCC -> resolucion -> FPS ->
       buffer, que es el orden que respeta V4L2 (ver medibot_vision.abrir_camara).

    2. SE LEEN LAS PROPIEDADES REALES. Pedir 640x480@30 MJPG no garantiza
       nada; ahora se imprime lo que la camara devuelve y se avisa si no
       coincide, en vez de suponerlo.

    3. TODO CONFIGURABLE POR ENTORNO (indice, resolucion, FPS, FourCC,
       backend), para poder probar combinaciones sin editar codigo.
    """
    global camera1, camera2
    resultados = []

    for posicion in (0, 1):
        indice = CAMERA_INDICES[posicion] if posicion < len(CAMERA_INDICES) else None
        if indice is None:
            print(f"Cámara {posicion + 1}: desactivada por configuración")
            info_camaras[posicion] = None
            continue

        cap, info = medibot_vision.abrir_camara(indice, PERFIL_CAPTURA)
        info_camaras[posicion] = info
        if cap is None:
            print(f"Error: No se pudo abrir cámara {posicion + 1} en índice {indice}")
            continue

        # Los ajustes de imagen (brillo/contraste) van DESPUES de negociar el
        # formato: algunos drivers los descartan al cambiar de modo.
        camera_optimizer.apply_settings(cap, indice=posicion)

        if posicion == 0:
            camera1 = cap
        else:
            camera2 = cap
        resultados.append(posicion)
        print(f"Cámara {posicion + 1} inicializada en índice {indice}")

    if not resultados:
        print("Ninguna cámara disponible. Comprueba los dispositivos con:")
        print("    ls /dev/video*")
        print("    v4l2-ctl --list-formats-ext -d /dev/video0")
        print("Para arrancar sin hardware:  MEDIBOT_CAMARA_FAKE=1 python3 medibot/main.py")
    return bool(resultados)


def resolucion_activa(camera_index):
    """(ancho, alto) REALES de una cámara; si no hay dato, los pedidos."""
    info = info_camaras.get(camera_index)
    if info is not None and info.ancho and info.alto:
        return info.ancho, info.alto
    return FRAME_W, FRAME_H

def release_cameras():
    """Libera ambas cámaras de forma segura"""
    global camera1, camera2, video_writer1, video_writer2, fps1, fps2
    fps1 = fps2 = 0
    for _i in (0, 1):
        medidor_captura[_i].reset()
    try:
        if camera1 is not None:
            camera1.release()
            camera1 = None
        if camera2 is not None:
            camera2.release()
            camera2 = None
        if video_writer1 is not None:
            video_writer1.release()
            video_writer1 = None
        if video_writer2 is not None:
            video_writer2.release()
            video_writer2 = None
        # Soltar los fotogramas retenidos: si no, la RAM de los ultimos frames
        # (y sus JPEG) se queda ocupada mientras el sistema esta parado.
        frame_hub.limpiar()
        time.sleep(0.5)
        print("Cámaras liberadas correctamente")
    except Exception as e:
        print(f"Error liberando cámaras: {e}")

def switch_camera():
    """Cambia entre las cámaras disponibles para visualización"""
    global active_camera_index
    if camera1 is not None and camera2 is not None:
        active_camera_index = (active_camera_index + 1) % 2
        messagebox.showinfo("Cambio de Cámara", 
                          f"Cambiando a cámara {active_camera_index + 1}")
        return True
    return False

def get_active_camera():
    """Obtiene la cámara activa actualmente para visualización"""
    if active_camera_index == 0 and camera1 is not None:
        return camera1
    elif active_camera_index == 1 and camera2 is not None:
        return camera2
    return camera1  # Por defecto cámara 1

#  Medidores de FPS REALMENTE capturados (uno por cámara). Sustituyen a las
#  seis variables globales de antes, que contaban un fotograma aunque la
#  lectura hubiera FALLADO: con una cámara desconectada seguían marcando ~30
#  FPS de fotogramas inexistentes, justo cuando más falta hacía enterarse.
medidor_captura = {0: medibot_vision.Medidor(), 1: medibot_vision.Medidor()}


def read_frame_from_camera(camera, camera_index):
    """Lee un frame de una cámara específica.

    Solo cuenta el fotograma si la lectura SALIÓ BIEN, para que los FPS
    publicados sean fotogramas de verdad."""
    global fps1, fps2
    try:
        if camera is None or not camera.isOpened():
            return False, None

        ret, frame = camera.read()
        if not ret or frame is None:
            return False, None

        fps = medidor_captura[camera_index].tick(time.time())
        if camera_index == 0:
            fps1 = fps
        else:
            fps2 = fps
        return True, frame

    except cv2.error as e:
        print(f"Error leyendo frame de cámara {camera_index}: {e}")
        return False, None

# ================= PROCESAMIENTO SIMULTÁNEO DE AMBAS CÁMARAS =======
# Detector de caras compartido. Detecta a MITAD de escala (4x menos pixeles,
# ~2x mas rapido: 25.5 ms -> 12.7 ms medidos) manteniendo scaleFactor y
# minNeighbors, asi que la sensibilidad no cambia; devuelve coordenadas a
# tamano real y el gris completo para que el reconocedor no pierda nitidez.
_face_detector = medibot_vision.FaceDetector(cascade, escala=0.5,
                                             scale_factor=1.1, min_neighbors=5,
                                             min_size=(30, 30))

def _necesita_deteccion_facial():
    """?Hay alguien que vaya a USAR las caras detectadas?

    El Haar es el 97 % del coste por fotograma. Antes se ejecutaba SIEMPRE,
    incluso con el reconocimiento apagado (que es como arranca el programa):
    se pagaban ~25 ms por frame para tirar el resultado a la basura. Eso es lo
    que dejaba la camara en 9-14 FPS sin usar ninguna IA."""
    return capture_mode or (recognition_enabled and recognizer is not None)


def process_camera(camera_index):
    """Procesa una cámara específica en un hilo separado"""
    # Solo se declaran las globales que esta funcion ESCRIBE (online, recording
    # y fps* unicamente se leen, asi que no necesitan declaracion).
    global system_status, detection_count, captured_images
    global capture_mode, face_position, last_face_time, detected_red_objects
    global video_writer1, video_writer2, recording_cam1, recording_cam2

    print(f"Iniciando procesamiento de cámara {camera_index + 1}")

    camera = camera1 if camera_index == 0 else camera2
    if camera is None:
        print(f"Cámara {camera_index + 1} no disponible")
        return

    es_principal = (camera_index == 0)      # la cámara 1 manda sobre los servos
    n_frame = 0
    perf = perfilador[camera_index]
    ultimo_informe = time.time()

    while online:
        try:
            # ---- Etapa 1: leer de la camara -------------------------------
            # camera.read() BLOQUEA hasta que el sensor entrega el fotograma,
            # asi que aqui se mide el coste REAL del driver (USB + decodificar
            # MJPEG). Si esta etapa domina, el limite no esta en nuestro codigo
            # Python sino en la camara: hay que bajar resolucion o cambiar el
            # formato/FPS del sensor.
            with medibot_vision.Cronometro(perf, "camara"):
                ret, frame = read_frame_from_camera(camera, camera_index)
            if not ret or frame is None:
                print(f"Error leyendo frame de cámara {camera_index + 1}, reintentando...")
                time.sleep(0.1)
                continue

            n_frame += 1
            current_time = time.time()

            # Informe periodico: reparto del tiempo y quien manda de verdad.
            if current_time - ultimo_informe >= PERF_CADA_SEGUNDOS:
                ultimo_informe = current_time
                fps_actual = fps1 if es_principal else fps2
                print(f"[perf cam{camera_index + 1}] captura {fps_actual} FPS | "
                      f"web {frame_hub.fps_enviados(camera_index)} FPS "
                      f"({frame_hub.clientes(camera_index)} clientes) | "
                      f"{perf.resumen()}")

            # Etapa 2: todo nuestro procesamiento (detectores + dibujo). Se
            # mide con marcas porque el bloque es largo y tiene ramas.
            t_proceso = time.perf_counter()

            #  Aqui se llamaba a camera_optimizer.auto_adjust(), que movia
            #  brillo/contraste en una escala 0..1 sin llegar a enviarlos nunca
            #  a la camara: solo servia para empeorar los valores que se
            #  mandarian al pulsar "Optimizar camara". La exposicion automatica
            #  de la propia webcam hace mejor ese trabajo.

            # ---- Objetos rojos -------------------------------------------
            # Se dibuja sobre el propio frame (antes se hacia frame.copy()).
            # Configurable: con MEDIBOT_DETECT_ROJO=0 se ahorran ~1,3 ms por
            # fotograma (cvtColor a HSV + dos morfologias + findContours) en
            # los montajes que no siguen objetos de color.
            processed_frame = frame
            if DETECCION_ROJO:
                red_objects = _red_detector.detectar(processed_frame,
                                                     dibujar=OVERLAYS_ACTIVOS)
                object_tracker.update_tracking(red_objects, current_time)
                if object_tracker.tracking_enabled and OVERLAYS_ACTIVOS:
                    processed_frame = object_tracker.draw_tracking(processed_frame, red_objects)
            else:
                red_objects = []
            detected_red_objects = red_objects   # publicar para las APIs web

            # ---- Caras: SOLO si alguien va a usarlas ----------------------
            # Se conserva la estructura original (incluido centrar los servos
            # cuando no hay reconocimiento); lo unico que se evita es el Haar,
            # que es lo caro. Centrar ahora es casi gratis: _escribir_pwm_*
            # ignora los valores repetidos.
            faces = []
            if es_principal and cascade is not None and not _necesita_deteccion_facial():
                center_pwm()
            elif es_principal and cascade is not None:
                faces, gray = _face_detector.detectar(frame)

                if capture_mode and len(faces) > 0:
                    for (x, y, w, h) in faces:
                        roi_gray = gray[y:y+h, x:x+w]
                        person_path = os.path.join(DATA_PATH, current_capture_name)
                        cv2.imwrite(f"{person_path}/{captured_images}.jpg", roi_gray)
                        captured_images += 1
                        invalidar_cache_personas()

                        cv2.rectangle(processed_frame, (x, y), (x+w, y+h), (0, 255, 255), 3)
                        cv2.putText(processed_frame, f"CAPTURANDO: {captured_images}/{IMAGES_PER_PERSON}",
                                   (x, y-10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)

                        if captured_images >= IMAGES_PER_PERSON:
                            capture_mode = False
                            system_status = "Captura Completada"
                        break

                elif not capture_mode and recognition_enabled and recognizer is not None and len(faces) > 0:
                    last_face_time = current_time

                    for (x, y, w, h) in faces:
                        detection_count += 1
                        roi_gray = gray[y:y+h, x:x+w]

                        try:
                            id_, conf = recognizer.predict(roi_gray)

                            if conf < CONF_LIMIT:
                                cx, cy = x + w // 2, y + h // 2

                                pos_x = "left" if cx < ZONE_X else ("right" if cx > ZONE_X * 2 else "center")
                                pos_y = "up" if cy < ZONE_Y else ("down" if cy > ZONE_Y * 2 else "center")

                                face_position = {"x": pos_x, "y": pos_y}
                                if es_principal:
                                    move_servos(pos_x, pos_y)

                                # El nombre sale del mapa GUARDADO AL ENTRENAR,
                                # no del orden de os.listdir: ese orden cambia
                                # al dar de alta o borrar a alguien y hacia que
                                # el sistema saludara a la persona equivocada.
                                person_name = nombre_de_etiqueta(id_)
                                similitud = medibot_vision.similitud_desde_distancia(
                                    conf, CONF_LIMIT)
                                _registrar_distancia_lbph(conf, person_name)
                                # "sim" y no "%": LBPH da distancia, no
                                # probabilidad. Ver similitud_desde_distancia.
                                label = f"{person_name} (sim {similitud})"

                                cv2.putText(processed_frame, label, (x, y - 30),
                                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
                                cv2.putText(processed_frame, "AUTORIZADO", (x, y - 10),
                                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
                                cv2.rectangle(processed_frame, (x, y), (x+w, y+h), (0, 255, 0), 3)
                                system_status = f"Rostro: {person_name}"
                            else:
                                _registrar_distancia_lbph(conf, None)
                                cv2.putText(processed_frame, "DESCONOCIDO", (x, y - 10),
                                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
                                cv2.rectangle(processed_frame, (x, y), (x+w, y+h), (0, 0, 255), 3)
                                system_status = "Rostro Desconocido"
                        except cv2.error as e:
                            print(f"Error en reconocimiento facial: {e}")
                            system_status = "Error Reconocimiento"
                        break
                elif es_principal:
                    # Detección pedida pero SIN caras en este fotograma.
                    #  Aqui vivia una rama inalcanzable: comprobaba
                    #  len(faces) == 0 dentro del bloque que exige
                    #  len(faces) > 0, asi que el estado "Escaneando" no se
                    #  ponia nunca. Ahora esta en la rama correcta, y solo
                    #  aplica al reconocimiento (en modo captura el estado lo
                    #  lleva el contador de imagenes).
                    center_pwm()
                    if (recognition_enabled and not capture_mode
                            and system_status not in ("Rostro Desconocido",
                                                      "Error Reconocimiento")):
                        system_status = "Escaneando"

            # ---- Seguimiento de objetos rojos (si no hay caras) -----------
            if es_principal and len(faces) == 0 and red_objects and object_tracker.tracking_enabled:
                largest_obj = max(red_objects, key=lambda obj: obj['area'])
                cx, cy = largest_obj['center_x'], largest_obj['center_y']

                pos_x = "left" if cx < ZONE_X else ("right" if cx > ZONE_X * 2 else "center")
                pos_y = "up" if cy < ZONE_Y else ("down" if cy > ZONE_Y * 2 else "center")

                move_servos(pos_x, pos_y)
                system_status = f"Siguiendo objeto rojo ({largest_obj['area']}px)"

            # ---- Grabación ------------------------------------------------
            if recording:
                # El VideoWriter se abre con el tamano REAL del fotograma. Con
                # las constantes FRAME_W/FRAME_H, si el driver entregaba otra
                # resolucion, cv2 descartaba en silencio cada write() y el .avi
                # salia vacio o corrupto.
                alto_rec, ancho_rec = processed_frame.shape[:2]
                if es_principal and not recording_cam1:
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    video_filename = os.path.join(VIDEO_PATH, "camara1", f"video_cam1_{timestamp}.avi")
                    fourcc = cv2.VideoWriter_fourcc(*'XVID')
                    video_writer1 = cv2.VideoWriter(video_filename, fourcc, 20.0, (ancho_rec, alto_rec))
                    recording_cam1 = True
                    print(f"Cámara 1 comenzó a grabar: {video_filename} ({ancho_rec}x{alto_rec})")

                if not es_principal and not recording_cam2:
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    video_filename = os.path.join(VIDEO_PATH, "camara2", f"video_cam2_{timestamp}.avi")
                    fourcc = cv2.VideoWriter_fourcc(*'XVID')
                    video_writer2 = cv2.VideoWriter(video_filename, fourcc, 20.0, (ancho_rec, alto_rec))
                    recording_cam2 = True
                    print(f"Cámara 2 comenzó a grabar: {video_filename} ({ancho_rec}x{alto_rec})")

                if es_principal and video_writer1 is not None:
                    video_writer1.write(processed_frame)
                elif not es_principal and video_writer2 is not None:
                    video_writer2.write(processed_frame)
            else:
                if es_principal and recording_cam1:
                    if video_writer1 is not None:
                        video_writer1.release()
                        video_writer1 = None
                    recording_cam1 = False
                    print("Cámara 1 detuvo la grabación")

                if not es_principal and recording_cam2:
                    if video_writer2 is not None:
                        video_writer2.release()
                        video_writer2 = None
                    recording_cam2 = False
                    print("Cámara 2 detuvo la grabación")

            # ---- Información sobre el vídeo -------------------------------
            #  Se dibuja usando el tamano REAL del fotograma, no las constantes
            #  FRAME_W/FRAME_H: si el driver entrego 320x240 en vez de los
            #  640x480 pedidos, los textos caian fuera de la imagen y no se
            #  veian. Con MEDIBOT_OVERLAY=0 se puede quitar todo este bloque.
            if OVERLAYS_ACTIVOS:
                alto_img, ancho_img = processed_frame.shape[:2]
                cv2.putText(processed_frame, f"Cámara: {camera_index + 1}", (10, alto_img - 40),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

                fps_text = f"FPS Cam1: {fps1}" if es_principal else f"FPS Cam2: {fps2}"
                cv2.putText(processed_frame, fps_text, (max(10, ancho_img - 150), 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

                if recording and ((es_principal and recording_cam1) or (not es_principal and recording_cam2)):
                    cv2.putText(processed_frame, "● GRABANDO", (max(10, ancho_img - 120), 60),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)

                if len(red_objects) > 0:
                    cv2.putText(processed_frame, f"Objetos Rojos: {len(red_objects)}", (10, 90),
                               cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)

                if DETECCION_ROJO and object_tracker.tracking_enabled:
                    cv2.putText(processed_frame, "SEGUIMIENTO ACTIVO", (10, alto_img - 20),
                               cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

            # ---- Publicar el fotograma ------------------------------------
            perf.anotar("proceso", (time.perf_counter() - t_proceso) * 1000.0)

            # ---- Etapa 3: publicar el fotograma ---------------------------
            #  CAMBIO IMPORTANTE: antes se publicaba el fotograma REDUCIDO a
            #  VIEW_W x VIEW_H (400x300), que es el tamano del panelito de
            #  Tkinter. La web consumia ese mismo fotograma y el navegador lo
            #  volvia a AMPLIAR para llenar su contenedor: de ahi la imagen
            #  blanda y con los textos ilegibles. Perdiamos el 61 % de los
            #  pixeles capturados antes de que nadie los viera.
            #
            #  Ahora se publica a resolucion COMPLETA y cada consumidor escala
            #  a lo suyo: Tk a 400x300 (ya lo hacia) y la web al perfil web
            #  (que por defecto es la resolucion nativa). El coste anadido es
            #  una copia de 640x480 (~0,07 ms medidos; el resize que se quita
            #  costaba ~0,28 ms), asi que ademas sale mas barato.
            #
            #  La copia NO es opcional: el frame publicado lo leen a la vez el
            #  hilo de Tk y los de Flask, y este bucle sigue dibujando encima
            #  en la siguiente vuelta. Publicar sin copiar seria una carrera.
            with medibot_vision.Cronometro(perf, "publicar"):
                frame_hub.publicar(camera_index, processed_frame.copy(),
                                   meta={"ancho": processed_frame.shape[1],
                                         "alto": processed_frame.shape[0],
                                         "t": current_time})

            # Sin time.sleep(): el ritmo lo marca camera.read(), que ya espera
            # al sensor. Dormir aqui era lo que tiraba los FPS a la mitad.

        except Exception as e:
            print(f"Error en procesamiento de cámara {camera_index + 1}: {e}")
            time.sleep(0.1)

    # Limpiar al salir
    print(f"Deteniendo procesamiento de cámara {camera_index + 1}...")
    frame_hub.limpiar(camera_index)
    if camera_index == 0 and video_writer1 is not None:
        video_writer1.release()
        video_writer1 = None
    elif camera_index == 1 and video_writer2 is not None:
        video_writer2.release()
        video_writer2 = None

def start_camera_processing():
    """Inicia el procesamiento simultáneo de ambas cámaras"""
    global camera1_thread, camera2_thread, online
    
    if not initialize_cameras():
        print("Error: No se pudo inicializar las cámaras")
        return False

    # Empezar a contar de cero: si no, el primer segundo hereda los FPS y el
    # reparto de tiempos de la sesión anterior y la telemetría miente.
    for i in (0, 1):
        medidor_captura[i].reset()
        perfilador[i].reiniciar()

    online = True
    
    # Iniciar hilo para cámara 1
    camera1_thread = threading.Thread(target=process_camera, args=(0,), daemon=True)
    camera1_thread.start()
    
    # Iniciar hilo para cámara 2
    camera2_thread = threading.Thread(target=process_camera, args=(1,), daemon=True)
    camera2_thread.start()
    
    print("Ambas cámaras iniciadas simultáneamente")
    return True

# ================= SERVIDOR WEB MEJORADO ==================
app = Flask(__name__)

#  CADENA RAW (la r de r""" NO es un adorno).
#  Sin ella, Python interpreta las barras invertidas ANTES de que el navegador
#  vea nada. Aqui dentro va JavaScript, donde \' y \" son escapes legitimos:
#  Python se los comia y el navegador recibia comillas sueltas.
#
#  Eso fue exactamente lo que dejo la web muerta: la linea que generaba los
#  botones de movimiento llevaba  enviarMovimiento(\')  y llegaba al navegador
#  como  enviarMovimiento('')  -> "SyntaxError: Invalid or unexpected token".
#  Y un error de SINTAXIS anula el bloque <script> ENTERO: ningun onclick
#  quedaba definido, startUpdates() no arrancaba y el tema no se aplicaba. El
#  video seguia viendose porque <img src="/video/0"> no necesita JavaScript,
#  lo que hacia parecer que "la web carga pero no responde".
#
#  Con r""" lo que se escribe aqui es LITERALMENTE lo que recibe el navegador.
HTML_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Medibot</title>
    <!-- Icono en linea (SVG como data URI). Sin esto el navegador pide
         /favicon.ico, Flask responde 404 y queda un error en la consola en
         cada carga. Al ir incrustado no se pide nada al servidor. -->
    <link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Ccircle cx='16' cy='16' r='14' fill='%234FD8D2'/%3E%3Ccircle cx='16' cy='16' r='5' fill='%23111'/%3E%3C/svg%3E">
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            background-color: #000000;
            color: #ffffff;
            font-family: 'Arial', sans-serif;
            min-height: 100vh;
            padding: 20px;
            transition: background-color 0.3s ease, color 0.3s ease;
        }
        
        .container {
            max-width: 1600px;
            width: 100%;
            margin: 0 auto;
            background: #111111;
            border-radius: 10px;
            padding: 30px;
            box-shadow: 0 0 20px rgba(0, 255, 255, 0.1);
            border: 1px solid #333333;
        }
        
        h1 {
            text-align: center;
            font-size: 2.5em;
            margin-bottom: 10px;
            color: #00ffff;
            font-weight: 300;
            letter-spacing: 2px;
        }
        
        .subtitle {
            text-align: center;
            color: #888888;
            margin-bottom: 30px;
            font-size: 1em;
            letter-spacing: 1px;
        }
        
        /* Rejilla responsiva de verdad: dos columnas solo si caben 380 px de
           ancho cada una. Antes eran SIEMPRE dos columnas fijas (1fr 1fr) y en
           el movil cada camara quedaba en media pantalla. Con una sola camara
           conectada, la que hay ocupa todo el ancho disponible. */
        .cameras-container {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(min(380px, 100%), 1fr));
            gap: 24px;
            margin-bottom: 30px;
        }

        .camera-box {
            background: #222222;
            border-radius: 8px;
            padding: 20px;
            border: 1px solid #333333;
        }
        
        .camera-title {
            color: #00ffff;
            font-size: 1.2em;
            margin-bottom: 15px;
            text-align: center;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 10px;
        }
        
        .camera-title.active {
            color: #00ff00;
        }
        
        .camera-title .status-dot {
            width: 10px;
            height: 10px;
            border-radius: 50%;
            background: #ff0000;
        }
        
        .camera-title.active .status-dot {
            background: #00ff00;
        }
        
        /* PROPORCION REAL DE LA CAMARA.
           Antes el contenedor tenia height: 350px fijo. Con una columna ancha
           en escritorio eso da una caja panoramica en la que un 4:3 se queda
           con franjas negras enormes a los lados, y en el movil obligaba a una
           caja de 350 px de alto pasara lo que pasara. Ahora la caja adopta la
           proporcion REAL que reporta la camara (--ar, que fija el JavaScript
           desde /api/all -> camaras_reales.proporcion), asi que no hay que
           estirar ni recortar nada: 4:3 se ve como 4:3 y 16:9 como 16:9. */
        .video-container {
            position: relative;
            border-radius: 5px;
            overflow: hidden;
            margin-top: 10px;
            border: 1px solid #333333;
            background: #000000;
            width: 100%;
            aspect-ratio: var(--ar, 4 / 3);
            /* Tope de altura para que en un monitor ancho con UNA sola cámara
               el vídeo no ocupe media pantalla de alto y empuje los controles
               fuera de la vista.
               Se limita el ANCHO (no el alto): con max-height, el contenedor
               conservaba los 100 % de ancho y se quedaba en 2,06:1 con franjas
               negras a los lados (medido: 1296x630 en 1440p). Limitando el
               ancho a "alto máximo x proporción", la caja mantiene exactamente
               la proporción de la cámara y se centra. --arnum lo fija el
               JavaScript con la proporción real; 1.3333 (4:3) es el respaldo. */
            max-width: calc(70vh * var(--arnum, 1.3333));
            margin-left: auto;
            margin-right: auto;
            display: flex;
            align-items: center;
            justify-content: center;
        }

        .video-container img {
            width: 100%;
            height: 100%;
            object-fit: contain;   /* red de seguridad: nunca deforma */
            display: block;
        }

        /* Barra de avisos, fija arriba. Oculta mientras todo va bien. */
        .aviso-barra {
            position: fixed; top: 0; left: 0; right: 0; z-index: 10000;
            padding: 10px 14px; font-size: 0.92em; font-weight: 600;
            background: #b3261e; color: #ffffff; text-align: center;
            transform: translateY(-100%); transition: transform .2s ease;
            box-shadow: 0 2px 10px rgba(0, 0, 0, .4);
        }
        .aviso-barra.visible { transform: translateY(0); }
        .aviso-barra.fatal { background: #7f1d1d; }

        /* Linea de estado bajo cada camara: legibilidad por encima de efectos.
           Dice lo que hace falta para diagnosticar sin abrir la consola:
           resolucion real, FPS capturados y FPS realmente enviados. */
        .stream-stats {
            display: flex;
            flex-wrap: wrap;
            gap: 6px 14px;
            margin-top: 8px;
            font-size: 0.82em;
            color: #9aa4ad;
            font-family: 'Courier New', monospace;
        }
        .stream-stats b { color: #00ffff; font-weight: 600; }
        .stream-stats .warn { color: #ffb020; }

        /* Camara ausente: se oculta su tarjeta entera en vez de dejar un hueco
           negro con controles que no hacen nada. */
        .camera-box.ausente { display: none; }

        .camera-info {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 10px;
            margin-top: 15px;
        }
        
        .info-item {
            background: #333333;
            padding: 10px;
            border-radius: 5px;
            text-align: center;
        }
        
        .info-label {
            color: #888888;
            font-size: 0.8em;
            margin-bottom: 5px;
        }
        
        .info-value {
            color: #ffffff;
            font-size: 1.1em;
            font-weight: bold;
        }
        
        .info-value.recording {
            color: #ff0000;
        }
        
        .status-container {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 15px;
            margin: 20px 0;
        }
        
        .status-box {
            background: #222222;
            padding: 15px;
            border-radius: 8px;
            text-align: center;
            border: 1px solid #333333;
        }
        
        .status-title {
            color: #888888;
            font-size: 0.9em;
            margin-bottom: 8px;
            text-transform: uppercase;
            letter-spacing: 1px;
        }
        
        .status-value {
            color: #00ffff;
            font-size: 1.2em;
            font-weight: bold;
        }
        
        .control-panel {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 15px;
            margin-top: 20px;
        }
        
        .control-button {
            background: #222222;
            color: #ffffff;
            border: 1px solid #333333;
            padding: 12px;
            border-radius: 5px;
            cursor: pointer;
            font-size: 1em;
            transition: all 0.3s ease;
        }
        
        .control-button:hover {
            background: #333333;
            border-color: #00ffff;
        }
        
        .control-button.recording {
            background: #ff0000;
            color: #ffffff;
        }
        
        .control-button.active {
            background: #00ffff;
            color: #000000;
        }
        
        .tab-container {
            margin-top: 30px;
        }
        
        .tab-buttons {
            display: flex;
            /* Sin flex-wrap, las tres pestañas medían 391 px y forzaban scroll
               horizontal en un móvil de 360 px (medido con el navegador). */
            flex-wrap: wrap;
            gap: 10px;
            margin-bottom: 20px;
        }
        
        .tab-button {
            background: #222222;
            color: #ffffff;
            border: none;
            padding: 10px 20px;
            border-radius: 5px;
            cursor: pointer;
        }
        
        .tab-button.active {
            background: #00ffff;
            color: #000000;
        }
        
        .tab-content {
            display: none;
        }
        
        .tab-content.active {
            display: block;
        }
        
        .videos-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
            gap: 20px;
        }
        
        .video-card {
            background: #222222;
            border-radius: 8px;
            overflow: hidden;
            border: 1px solid #333333;
        }
        
        .video-thumbnail {
            width: 100%;
            height: 180px;
            background: #000;
            display: flex;
            align-items: center;
            justify-content: center;
            color: #888;
        }
        
        .video-info {
            padding: 15px;
        }
        
        .video-title {
            color: #ffffff;
            margin-bottom: 10px;
            font-size: 1.1em;
        }
        
        .video-meta {
            color: #888;
            font-size: 0.9em;
            margin-bottom: 10px;
        }
        
        .video-camera {
            display: inline-block;
            background: #333;
            color: #00ffff;
            padding: 3px 8px;
            border-radius: 3px;
            font-size: 0.8em;
            margin-right: 10px;
        }
        
        .video-actions {
            display: flex;
            gap: 10px;
        }
        
        .video-action-btn {
            background: #333;
            color: #fff;
            border: none;
            padding: 8px 15px;
            border-radius: 5px;
            cursor: pointer;
            font-size: 0.9em;
        }
        
        .video-action-btn:hover {
            background: #444;
        }
        
        .position-indicator {
            background: #222222;
            padding: 20px;
            border-radius: 8px;
            margin: 20px 0;
            border: 1px solid #333333;
        }
        
        .position-grid {
            display: grid;
            grid-template-columns: 1fr 1fr 1fr;
            grid-template-rows: 1fr 1fr 1fr;
            gap: 10px;
            height: 200px;
            margin-top: 15px;
        }
        
        .position-cell {
            background: #333333;
            border-radius: 5px;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: all 0.3s ease;
            border: 2px solid transparent;
            font-size: 24px;
            color: #888888;
        }
        
        .position-cell.active {
            background: #00ffff;
            border-color: #ffffff;
            box-shadow: 0 0 15px rgba(0, 255, 255, 0.5);
            color: #000000;
        }
        
        .footer {
            text-align: center;
            margin-top: 30px;
            color: #444444;
            font-size: 0.8em;
        }
        
        @media (max-width: 1200px) {
            /* La rejilla de cámaras ya no se fuerza aquí a una sola columna:
               con auto-fit se reparte sola (dos columnas mientras quepan 380 px
               cada una, una sola por debajo). Forzarlo aquí desperdiciaba media
               pantalla en tablets. */
            .control-panel {
                grid-template-columns: repeat(2, 1fr);
            }
            
            .status-container {
                grid-template-columns: repeat(2, 1fr);
            }
        }
        
        @media (max-width: 768px) {
            .container {
                padding: 20px;
            }
            
            h1 {
                font-size: 1.8em;
            }
            
            .control-panel {
                grid-template-columns: 1fr;
            }
            
            .status-container {
                grid-template-columns: 1fr;
            }
        }

        /* ===== Marca MEDIBOT ===== */
        .brand-bar {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 16px;
            flex-wrap: wrap;
            margin-bottom: 18px;
        }
        .brand {
            display: flex;
            align-items: center;
            gap: 14px;
        }
        .brand-logo {
            flex: 0 0 auto;
            filter: drop-shadow(0 0 8px rgba(79, 216, 210, 0.45));
        }
        .wordmark {
            font-weight: 800;
            font-size: 2em;
            letter-spacing: 1px;
            line-height: 1;
        }
        .wm-medi { color: #4FD8D2; }
        .wm-bot { color: #ffffff; }
        .brand-tag {
            display: block;
            font-size: 0.4em;
            font-weight: 400;
            letter-spacing: 3px;
            color: #888888;
            margin-top: 4px;
        }
        .theme-toggle {
            background: #222222;
            color: #ffffff;
            border: 1px solid #333333;
            padding: 10px 18px;
            border-radius: 30px;
            cursor: pointer;
            font-size: 0.95em;
            transition: all 0.3s ease;
            white-space: nowrap;
        }
        .theme-toggle:hover {
            border-color: #4FD8D2;
            background: #333333;
        }
        /*  El enlace al Pastillero comparte estilo con el boton de tema, pero
            es un <a>: hay que quitarle el subrayado y centrarlo como al resto,
            porque un <a> no se alinea igual que un <button>. */
        a.theme-toggle {
            text-decoration: none;
            display: inline-flex;
            align-items: center;
            line-height: 1;
        }
        .acciones-cabecera {
            display: flex;
            gap: 10px;
            align-items: center;
            flex-wrap: wrap;      /* en movil caen a la linea de abajo */
        }
        .panel-box {
            background: #222222;
            padding: 20px;
            border-radius: 8px;
        }

        /* ===== Tema Claro ===== */
        html[data-theme="light"] body { background-color: #eef1f5; color: #15202b; }
        html[data-theme="light"] .container { background: #ffffff; border-color: #d4dae0; box-shadow: 0 0 24px rgba(10, 166, 160, 0.12); }
        html[data-theme="light"] h1 { color: #0aa6a0; }
        html[data-theme="light"] .subtitle { color: #5a6772; }
        html[data-theme="light"] .camera-box { background: #f4f7fa; border-color: #d4dae0; }
        html[data-theme="light"] .camera-title { color: #0aa6a0; }
        html[data-theme="light"] .camera-title.active { color: #0a8f2a; }
        html[data-theme="light"] .info-item { background: #e9eef3; }
        html[data-theme="light"] .info-label { color: #5a6772; }
        html[data-theme="light"] .info-value { color: #15202b; }
        html[data-theme="light"] .info-value.recording { color: #d11a2a; }
        html[data-theme="light"] .status-box { background: #f4f7fa; border-color: #d4dae0; }
        html[data-theme="light"] .status-title { color: #5a6772; }
        html[data-theme="light"] .status-value { color: #0aa6a0; }
        html[data-theme="light"] .control-button { background: #f4f7fa; color: #15202b; border-color: #d4dae0; }
        html[data-theme="light"] .control-button:hover { background: #e3e9ef; border-color: #0aa6a0; }
        html[data-theme="light"] .control-button.active { background: #0aa6a0; color: #ffffff; }
        html[data-theme="light"] .control-button.recording { background: #d11a2a; color: #ffffff; }
        html[data-theme="light"] .tab-button { background: #f4f7fa; color: #15202b; }
        html[data-theme="light"] .tab-button.active { background: #0aa6a0; color: #ffffff; }
        html[data-theme="light"] .panel-box { background: #f4f7fa; }
        html[data-theme="light"] .video-card { background: #f4f7fa; border-color: #d4dae0; }
        html[data-theme="light"] .video-title { color: #15202b; }
        html[data-theme="light"] .video-meta { color: #5a6772; }
        html[data-theme="light"] .video-camera { background: #e3e9ef; color: #0aa6a0; }
        html[data-theme="light"] .video-action-btn { background: #e3e9ef; color: #15202b; }
        html[data-theme="light"] .video-action-btn:hover { background: #d4dae0; }
        html[data-theme="light"] .position-indicator { background: #f4f7fa; border-color: #d4dae0; }
        html[data-theme="light"] .position-cell { background: #e3e9ef; color: #5a6772; }
        html[data-theme="light"] .position-cell.active { background: #0aa6a0; border-color: #ffffff; color: #ffffff; }
        /* #9aa6b2 sobre blanco daba un contraste de 2,48: por debajo del
           minimo legible. Medido con el navegador; ahora 4,6. */
        html[data-theme="light"] .footer { color: #5a6772; }
        html[data-theme="light"] .theme-toggle { background: #f4f7fa; color: #15202b; border-color: #d4dae0; }
        html[data-theme="light"] .theme-toggle:hover { background: #e3e9ef; border-color: #0aa6a0; }
        html[data-theme="light"] .wm-bot { color: #15202b; }
        html[data-theme="light"] .brand-tag { color: #5a6772; }

        /* ===== Joystick / Movimiento ===== */
        /* Botonera de movimiento (los 6 movimientos del robot) */
        .mov-panel { margin-top: 10px; }
        .mov-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; }
        .mov-vel { margin-top: 8px; font-size: .78em; color: #00ffff; }
        .mov-vel label { display: block; margin-bottom: 2px; }
        .mov-vel input[type=range] { width: 100%; accent-color: #00ffff; }
        html[data-theme="light"] .mov-vel { color: #0a7c78; }
        .mov-btn {
            background: #1a1a1a; color: #00ffff; border: 1px solid #00ffff;
            border-radius: 6px; padding: 8px 4px; font-size: .78em; cursor: pointer;
            transition: background .15s ease;
        }
        .mov-btn:hover, .mov-btn:active { background: #00ffff; color: #000; }
        .mov-btn.stop { border-color: #ff5555; color: #ff5555; }
        .mov-btn.stop:hover { background: #ff5555; color: #000; }
        html[data-theme="light"] .mov-btn { background: #eef4f8; color: #0a7c78; border-color: #0aa6a0; }

        .joystick-wrap { display: flex; justify-content: center; margin: 20px 0; }
        .joystick-base {
            position: relative; width: 180px; height: 180px; border-radius: 50%;
            background: #333333; border: 3px solid #00ffff; touch-action: none; cursor: pointer;
        }
        .joystick-stick {
            position: absolute; top: 50%; left: 50%; width: 60px; height: 60px; margin: -30px 0 0 -30px;
            border-radius: 50%; background: #00ffff; box-shadow: 0 0 12px rgba(0, 255, 255, 0.6);
            transition: transform 0.05s linear; pointer-events: none;
        }
        .dpad { display: flex; flex-direction: column; align-items: center; gap: 10px; margin: 20px 0; }
        .dpad-row { display: flex; gap: 10px; }
        .move-btn {
            width: 60px; height: 60px; font-size: 1.4em; border-radius: 10px;
            background: #222222; color: #00ffff; border: 1px solid #333333; cursor: pointer;
            transition: all 0.15s ease; user-select: none; -webkit-user-select: none;
        }
        .move-btn:hover { border-color: #00ffff; }
        .move-btn.active, .move-btn:active { background: #00ffff; color: #000000; }
        .move-stop { color: #ff5555; }
        .move-status { text-align: center; color: #888888; margin-top: 10px; font-weight: bold; }

        html[data-theme="light"] .joystick-base { background: #e3e9ef; border-color: #0aa6a0; }
        html[data-theme="light"] .joystick-stick { background: #0aa6a0; box-shadow: 0 0 12px rgba(10, 166, 160, 0.5); }
        html[data-theme="light"] .move-btn { background: #f4f7fa; color: #0aa6a0; border-color: #d4dae0; }
        html[data-theme="light"] .move-btn:hover { border-color: #0aa6a0; }
        html[data-theme="light"] .move-btn.active, html[data-theme="light"] .move-btn:active { background: #0aa6a0; color: #ffffff; }
        html[data-theme="light"] .move-status { color: #5a6772; }

        /* ===== Joystick translúcido dentro de la cámara + pantalla completa ===== */
        /*  Los botones de encima del video van en una FILA FLEX, no cada uno
            con su 'right' fijo. Con posiciones fijas se solapaban en cuanto se
            anadio el segundo, y ademas el de pantalla completa cambia de texto
            ("Pantalla completa" / "Salir"): cualquier separacion calculada a
            mano se rompe al cambiar de ancho. Asi se colocan solos. */
        .cam-acciones {
            position: absolute; top: 10px; right: 10px; z-index: 6;
            display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end;
            max-width: calc(100% - 20px);
        }
        .fs-btn {
            background: rgba(0, 0, 0, 0.5); color: #fff;
            border: 1px solid rgba(255, 255, 255, 0.35);
            padding: 6px 10px; border-radius: 6px; cursor: pointer; font-size: 0.85em;
            white-space: nowrap;
        }
        .fs-btn:hover { background: rgba(0, 0, 0, 0.75); border-color: #4FD8D2; }
        /*  Sonando: se ve de un vistazo que el microfono esta abierto. */
        .fs-btn.sonando { background: rgba(10, 166, 160, 0.85); border-color: #4FD8D2; }
        /*  Hablando: ROJO, no verde como el de escuchar. Son cosas distintas
            (una saca audio del robot, la otra lo mete) y con el mismo color
            no se sabria de un vistazo cual esta abierta. Rojo es ademas lo
            que todo el mundo asocia a "estas emitiendo". */
        .fs-btn.hablando {
            background: rgba(214, 48, 49, 0.9); border-color: #ff7675;
            animation: latido 1.2s ease-in-out infinite;
        }
        @keyframes latido {
            0%, 100% { box-shadow: 0 0 0 0 rgba(214, 48, 49, 0.55); }
            50%      { box-shadow: 0 0 0 6px rgba(214, 48, 49, 0); }
        }
        /*  Quien haya pedido menos animaciones no quiere un boton latiendo. */
        @media (prefers-reduced-motion: reduce) {
            .fs-btn.hablando { animation: none; }
        }
        .fs-btn:disabled { opacity: 0.45; cursor: not-allowed; }
        /*  Boton de solo icono: cuadrado y sin hueco para texto. El SVG usa
            currentColor, asi que sigue al tema sin reglas aparte. */
        .fs-btn.icono {
            display: inline-flex; align-items: center; justify-content: center;
            padding: 6px; width: 32px; height: 32px; line-height: 0;
        }
        .cam-joystick {
            position: absolute; right: 12px; bottom: 12px; z-index: 6;
            display: flex; flex-direction: column; align-items: center; gap: 6px;
            opacity: 0.6; transition: opacity 0.2s ease; touch-action: none;
        }
        .cam-joystick:hover { opacity: 1; }
        .cam-joystick .joystick-base { width: 110px; height: 110px; }
        .cam-joystick .joystick-stick { width: 40px; height: 40px; margin: -20px 0 0 -20px; }
        .cam-joy-dirs {
            font-size: 0.8em; color: #fff; background: rgba(0, 0, 0, 0.45);
            padding: 2px 10px; border-radius: 10px; font-weight: bold;
        }
        /* ============ PANTALLA COMPLETA ============
           Va a pantalla completa el ESCENARIO (vídeo + controles), no solo el
           vídeo: así en pantalla completa sigues teniendo la botonera y el
           slider de velocidad, que es para lo que se usa.

           .pseudo-fs es el respaldo para navegadores donde la API de pantalla
           completa no existe o la rechaza (iOS Safari NO la soporta sobre un
           <div>: solo sobre <video>). Ocupa la ventana con position:fixed, así
           el botón SIEMPRE hace algo en vez de quedarse muerto. */
        .camera-stage:fullscreen,
        .camera-stage:-webkit-full-screen,
        .camera-stage.pseudo-fs {
            background: #000;
            display: flex;
            flex-direction: column;
            width: 100%;
            height: 100%;
            padding: 10px;
            gap: 10px;
        }
        .camera-stage.pseudo-fs {
            position: fixed;
            inset: 0;
            z-index: 9999;
            overflow: auto;
            /* Móviles con notch: no meter los controles bajo la barra */
            padding: max(10px, env(safe-area-inset-top))
                     max(10px, env(safe-area-inset-right))
                     max(10px, env(safe-area-inset-bottom))
                     max(10px, env(safe-area-inset-left));
        }

        /* IMPORTANTE: hay que anular aspect-ratio y el tope de ancho, o el
           vídeo se quedaría en 4:3 y al 70 % del alto en vez de llenar la
           pantalla. La imagen sigue sin deformarse: object-fit: contain. */
        .camera-stage:fullscreen .video-container,
        .camera-stage:-webkit-full-screen .video-container,
        .camera-stage.pseudo-fs .video-container {
            aspect-ratio: auto;
            max-width: none;
            max-height: none;
            width: 100%;
            flex: 1 1 auto;
            min-height: 0;
            margin-top: 0;
        }
        .camera-stage:fullscreen .video-container img,
        .camera-stage:-webkit-full-screen .video-container img,
        .camera-stage.pseudo-fs .video-container img {
            width: 100%; height: 100%; object-fit: contain;
        }
        /* Los controles no se estiran: conservan su altura al pie */
        .camera-stage:fullscreen .mov-panel,
        .camera-stage:-webkit-full-screen .mov-panel,
        .camera-stage.pseudo-fs .mov-panel {
            flex: 0 0 auto; margin-top: 0; max-width: 720px;
            width: 100%; margin-left: auto; margin-right: auto;
        }
        .camera-stage:fullscreen .cam-joystick,
        .camera-stage:-webkit-full-screen .cam-joystick,
        .camera-stage.pseudo-fs .cam-joystick {
            transform: scale(1.4); right: 48px; bottom: 48px; opacity: 0.75;
        }

        /* ============ CONTROLES EN MÓVIL ============ */
        .vel-aviso { min-height: 1em; font-size: .9em; color: #ff6b6b; }
        .mov-vel input[type=range] {
            /* Alto de dedo, no de ratón: 24 px de zona activa. Antes el
               control nativo tenía ~4 px de alto y era difícil de agarrar. */
            height: 24px;
            touch-action: pan-x;   /* deslizar el slider NO hace scroll */
        }
        @media (max-width: 768px) {
            /* Objetivos táctiles: mínimo recomendado ~44 px */
            .mov-btn { padding: 12px 4px; font-size: .85em; }
            .fs-btn { padding: 10px 12px; font-size: 0.9em; }
            .mov-vel { font-size: .9em; }
            /* El joystick flotante se encoge para dejar ver el vídeo */
            .cam-joystick { right: 8px; bottom: 8px; opacity: 0.75; }
            .cam-joystick .joystick-base { width: 88px; height: 88px; }
            .cam-joystick .joystick-stick { width: 32px; height: 32px; margin: -16px 0 0 -16px; }
        }
    </style>
</head>
<body>
    <!-- Barra de avisos: los fallos de API y los errores de JavaScript se ven
         AQUI, no solo en la consola del navegador. -->
    <div class="aviso-barra" id="avisoBarra" role="status" aria-live="polite"></div>
    <div class="container">
        <div class="brand-bar">
            <div class="brand">
                <svg class="brand-logo" viewBox="0 0 100 100" width="52" height="52" aria-label="Logo MEDIBOT">
                    <circle cx="50" cy="50" r="40" fill="#4FD8D2"/>
                    <g stroke="#ffffff" stroke-width="4" stroke-linecap="round">
                        <line x1="50" y1="10" x2="50" y2="90"/>
                        <line x1="10" y1="50" x2="90" y2="50"/>
                        <line x1="21.7" y1="21.7" x2="78.3" y2="78.3"/>
                        <line x1="78.3" y1="21.7" x2="21.7" y2="78.3"/>
                    </g>
                    <circle cx="50" cy="50" r="15" fill="#ffffff"/>
                </svg>
                <div>
                    <span class="wordmark"><span class="wm-medi">MEDI</span><span class="wm-bot">BOT</span></span>
                    <span class="brand-tag">VISIÓN ARTIFICIAL</span>
                </div>
            </div>
            <div class="acciones-cabecera">
                <!--  Ir al Pastillero. Es un <a> y no un boton con
                      window.location a proposito: asi se puede abrir en otra
                      pestaña con el boton central o pulsacion larga, que es lo
                      que espera cualquiera de un enlace.
                      Sin emoji: los botones de esta barra son solo texto. -->
                <a class="theme-toggle" id="linkPastillero" href="#" rel="noopener"
                   title="Ir a la interfaz del pastillero">Pastillero</a>
                <button class="theme-toggle" id="themeToggle" onclick="toggleTheme()" title="Cambiar tema claro/oscuro">Modo Oscuro</button>
            </div>
        </div>
        <!-- Sin <h1>MEDIBOT</h1>: la barra de marca de arriba ya lleva el
             logo y el nombre, así que salía dos veces seguidas. -->

        <div class="cameras-container">
            <div class="camera-box" id="camera1-box">
                <div class="camera-title" id="cam1-title">
                    <span class="status-dot"></span>
                    CÁMARA 1 (Principal)
                </div>
                <!-- "Escenario": vídeo + controles. Es ESTE bloque el que pasa a
                     pantalla completa, no solo el vídeo, para que en pantalla
                     completa sigas teniendo la botonera y la velocidad. -->
                <div class="camera-stage" id="cam1-stage">
                    <div class="video-container" id="cam1-view">
                        <img src="/video/0" alt="Cámara 1 en vivo" id="video-stream-1">
                        <div class="cam-acciones">
                            <!--  Escuchar el microfono de la camara: SOLO EL
                                  ICONO, sin texto. Es un SVG en linea y no un
                                  emoji, porque cada sistema dibuja los emojis
                                  a su manera (en Android y en Windows el
                                  altavoz sale distinto y de otro color) y
                                  ademas no heredan el color del tema.
                                  El icono cambia entre "mudo" y "sonando", asi
                                  que se ve el estado sin necesidad de texto.
                                  Tiene que arrancarlo el usuario con un clic:
                                  los navegadores bloquean el audio que empieza
                                  solo, y un autoplay quedaria mudo sin avisar. -->
                            <button class="fs-btn icono" id="audioBtn" onclick="alternarAudio()"
                                    title="Escuchar el micrófono de la cámara"
                                    aria-label="Escuchar el micrófono de la cámara">
                                <svg id="audioIcono" viewBox="0 0 24 24" width="17" height="17"
                                     fill="none" stroke="currentColor" stroke-width="2"
                                     stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                                    <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon>
                                    <!--  Arranca MUDO (con la cruz), porque al
                                          cargar la pagina el audio no suena.
                                          Empezar con las ondas daria a entender
                                          que ya se esta escuchando. -->
                                    <g id="audioOndas" style="display:none"><path d="M15.5 8.5a5 5 0 0 1 0 7"></path><path d="M18.5 5.5a9 9 0 0 1 0 13"></path></g>
                                    <g id="audioCruz"><line x1="22" y1="9" x2="16" y2="15"></line><line x1="16" y1="9" x2="22" y2="15"></line></g>
                                </svg>
                            </button>
                            <!--  Hablar por el altavoz del robot: se habla
                                  MIENTRAS SE MANTIENE PULSADO, como un
                                  walkie-talkie. Se eligio asi y no un
                                  interruptor porque un interruptor se queda
                                  encendido de un despiste y te deja el
                                  microfono abierto sin enterarte; soltando
                                  el dedo se corta y ya esta.
                                  Ojo: capturar tu microfono necesita HTTPS.
                                  Por http://<ip>:5000 el navegador no lo
                                  permite, y el boton lo dice al pulsarlo. -->
                            <button class="fs-btn icono" id="hablarBtn"
                                    title="Mantén pulsado para hablar por el altavoz del robot"
                                    aria-label="Mantén pulsado para hablar por el altavoz del robot">
                                <svg viewBox="0 0 24 24" width="17" height="17"
                                     fill="none" stroke="currentColor" stroke-width="2"
                                     stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                                    <path d="M12 1a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"></path>
                                    <path d="M19 10v1a7 7 0 0 1-14 0v-1"></path>
                                    <line x1="12" y1="19" x2="12" y2="23"></line>
                                    <line x1="8" y1="23" x2="16" y2="23"></line>
                                </svg>
                            </button>
                            <button class="fs-btn" id="fsBtn" onclick="toggleCameraFullscreen()"
                                    title="Pantalla completa">&#9974; <span id="fsBtnTxt">Pantalla completa</span></button>
                        </div>
                        <div class="cam-joystick">
                            <div class="joystick-base" id="joyBase">
                                <div class="joystick-stick" id="joyStick"></div>
                            </div>
                            <div class="cam-joy-dirs">Dir: <span id="move-dirs">—</span></div>
                        </div>
                    </div>
                    <!-- Botonera de movimiento: los SEIS movimientos del robot,
                         con los mismos nombres que el firmware y el mando PS2.
                         Se genera desde /movimientos para que no haya nombres
                         escritos a mano que se puedan desincronizar.

                         ESTABA DENTRO de .video-container, que es display:flex.
                         Al no llevar posicionamiento, era un flex item que se
                         repartia el espacio con el <img>: en movil quedaba
                         aplastado, recortado por overflow:hidden y encima del
                         boton de pantalla completa. Ahora va DEBAJO del video,
                         como bloque normal, y no se solapa con nada. -->
                    <div class="mov-panel">
                        <div class="mov-grid" id="mov-grid"></div>
                        <div class="mov-vel">
                            <label for="velRange">Velocidad <span id="velVal">200</span></label>
                            <input type="range" id="velRange" min="200" max="255" value="200"
                                   oninput="document.getElementById('velVal').textContent=this.value"
                                   onchange="fijarVelocidad(this.value)">
                            <div class="vel-aviso" id="velAviso"></div>
                        </div>
                    </div>
                </div>
                <div class="camera-info">
                    <div class="info-item">
                        <div class="info-label">Estado</div>
                        <div class="info-value" id="status-1">Inactiva</div>
                    </div>
                    <div class="info-item">
                        <div class="info-label">Grabando</div>
                        <div class="info-value" id="recording-1">No</div>
                    </div>
                </div>
            </div>

            <div class="camera-box" id="camera2-box">
                <div class="camera-title" id="cam2-title">
                    <span class="status-dot"></span>
                    CÁMARA 2 (Secundaria)
                </div>
                <div class="video-container" id="cam2-view">
                    <img src="/video/1" alt="Cámara 2 en vivo" id="video-stream-2">
                </div>
                <div class="camera-info">
                    <div class="info-item">
                        <div class="info-label">Estado</div>
                        <div class="info-value" id="status-2">Inactiva</div>
                    </div>
                    <div class="info-item">
                        <div class="info-label">Grabando</div>
                        <div class="info-value" id="recording-2">No</div>
                    </div>
                </div>
            </div>
        </div>
        
        <!-- Solo lo esencial: el estado del sistema. Se quitaron las tarjetas
             de Detecciones / Cámara activa / Objetos rojos y la rejilla de
             posición del rostro: eran telemetría de diagnóstico, no algo que
             haga falta para manejar el robot. Los datos siguen en /api/all. -->
        <div class="status-container">
            <div class="status-box">
                <div class="status-title">Estado Sistema</div>
                <div class="status-value" id="system-status">Inactivo</div>
            </div>
        </div>


        <div class="control-panel">
            <button class="control-button" onclick="toggleSystem()" id="systemBtn">
                Iniciar Sistema
            </button>
            <button class="control-button" onclick="toggleRecording()" id="recordBtn">
                Iniciar Grabación Ambas
            </button>
            <button class="control-button" onclick="switchCamera()">
                Cambiar Cámara Activa
            </button>
            <button class="control-button" onclick="toggleRecognition()" id="recognitionBtn">
                Reconocimiento: OFF
            </button>
            <!--  Apagar la busqueda de objetos rojos ahorra ~1,3 ms por
                  fotograma y por camara. Antes solo se podia con
                  MEDIBOT_DETECT_ROJO=0 y reiniciando el programa. -->
            <button class="control-button" onclick="alternarDeteccionRojo()" id="rojoBtn">
                Color rojo: ON
            </button>
            <button class="control-button" onclick="showTab('videos')">
                Ver Videos Grabados
            </button>
        </div>
        
        <!-- Queda una sola pestaña: los vídeos grabados.
             Se quitó "Información del Sistema", que era el listado de
             instrucciones y características, y "Configuración", cuyo botón de
             centrar servomotores no hace nada en este robot (la cámara va
             fija: USAR_SERVOS_CAMARA = False). -->
        <div class="tab-container">
            <div class="tab-content active" id="videos-tab">
                <h3>Videos Grabados (Ordenados por fecha)</h3>
                <div class="videos-grid" id="videos-grid">
                    Cargando videos...
                </div>
            </div>
        </div>
        
        <!-- Pie sin la linea tecnica de IP/Puerto/API: era informacion de
             diagnostico, no algo que haga falta para manejar el robot. -->
        <div class="footer">
            <p>Medibot</p>
        </div>
    </div>

    <script>
        let currentTab = 'videos';
        let updateInterval;

        // ===== Avisos visibles =====
        //  Antes los fallos se tragaban con .catch(() => {}): si una ruta
        //  respondia 500, o el robot se quedaba sin red, la interfaz seguia
        //  igual de tranquila y no habia forma de saberlo sin abrir la consola.
        //  Ahora todo fallo se ve en la barra de arriba Y se registra completo
        //  en la consola.
        let _avisoTimer = null;
        function avisar(mensaje, detalle) {
            if (detalle) { console.error('[medibot]', mensaje, detalle); }
            else { console.warn('[medibot]', mensaje); }
            const barra = document.getElementById('avisoBarra');
            if (!barra) { return; }
            barra.textContent = mensaje;
            barra.classList.add('visible');
            if (_avisoTimer) { clearTimeout(_avisoTimer); }
            // Los errores de JavaScript se quedan fijos: son fallos de
            // programacion y deben verse hasta que se arreglen.
            if (!barra.classList.contains('fatal')) {
                _avisoTimer = setTimeout(() => barra.classList.remove('visible'), 6000);
            }
        }

        function limpiarAviso() {
            const barra = document.getElementById('avisoBarra');
            if (barra && !barra.classList.contains('fatal')) {
                barra.classList.remove('visible');
            }
        }

        //  Red de seguridad: cualquier error de JavaScript no capturado sale
        //  en pantalla. Si esto hubiera existido, el fallo de sintaxis que
        //  dejo la web muerta se habria visto al instante en vez de parecer
        //  "los botones no responden".
        window.addEventListener('error', function (ev) {
            const barra = document.getElementById('avisoBarra');
            if (barra) { barra.classList.add('fatal'); }
            avisar('Error de JavaScript: ' + (ev.message || 'desconocido'), ev.error || ev);
        });
        window.addEventListener('unhandledrejection', function (ev) {
            avisar('Petición fallida sin controlar', ev.reason);
        });

        //  Un unico punto por el que pasan TODAS las llamadas a Flask: aqui se
        //  comprueba response.ok (antes se ignoraba, asi que un 400 o un 500
        //  se procesaba como si fuera una respuesta buena) y se convierte el
        //  fallo en un mensaje legible.
        function pedirJSON(url, opciones) {
            return fetch(url, opciones)
                .then(r => r.text().then(txt => {
                    let datos = null;
                    try { datos = txt ? JSON.parse(txt) : null; }
                    catch (e) {
                        throw new Error('Respuesta no válida de ' + url +
                                        ' (HTTP ' + r.status + '): ' + txt.slice(0, 120));
                    }
                    if (!r.ok) {
                        throw new Error('HTTP ' + r.status + ' en ' + url + ': ' +
                                        ((datos && (datos.message || datos.error)) || 'sin detalle'));
                    }
                    return datos;
                }));
        }

        function enviarJSON(url, cuerpo) {
            return pedirJSON(url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(cuerpo || {})
            });
        }

        function updateCameraStatus(data) {
            // Actualizar estado de las cámaras
            document.getElementById('cam1-title').classList.toggle('active', data.cameras.camera1.active);
            document.getElementById('cam2-title').classList.toggle('active', data.cameras.camera2.active);

            // Mostrar solo la(s) cámara(s) activa(s). Se usa una clase en vez
            // de forzar gridTemplateColumns a mano: la rejilla ya es
            // responsiva (auto-fit), así que al ocultar una tarjeta la otra
            // se expande sola y sigue funcionando igual en móvil.
            var cam2box = document.getElementById('camera2-box');
            if (cam2box) { cam2box.classList.toggle('ausente', !data.cameras.camera2.active); }
            //  La tarjeta de la cámara 1 NO se oculta nunca. Antes se escondía
            //  con el sistema parado, y dentro van el joystick, la botonera y
            //  la velocidad: al pulsar "Detener Sistema" desaparecían todos
            //  los mandos y ya no se podía mover el robot. Mover el chasis no
            //  depende de que las cámaras estén capturando.

            aplicarProporcion(data);

            // Reflejar el estado del reconocimiento en el botón
            var recBtn = document.getElementById('recognitionBtn');
            if (recBtn) {
                recBtn.textContent = data.recognition_enabled ? 'Reconocimiento: ON' : 'Reconocimiento: OFF';
                recBtn.classList.toggle('active', !!data.recognition_enabled);
            }

            document.getElementById('status-1').textContent = data.cameras.camera1.status;
            document.getElementById('status-2').textContent = data.cameras.camera2.status;
            
            document.getElementById('recording-1').textContent = data.cameras.camera1.recording ? 'Sí' : 'No';
            document.getElementById('recording-2').textContent = data.cameras.camera2.recording ? 'Sí' : 'No';
            
            if (data.cameras.camera1.recording) {
                document.getElementById('recording-1').classList.add('recording');
            } else {
                document.getElementById('recording-1').classList.remove('recording');
            }
            
            if (data.cameras.camera2.recording) {
                document.getElementById('recording-2').classList.add('recording');
            } else {
                document.getElementById('recording-2').classList.remove('recording');
            }
            
            // Actualizar estado general
            document.getElementById('system-status').textContent = data.system_status;

            // Actualizar botones
            const systemBtn = document.getElementById('systemBtn');
            if (data.online) {
                systemBtn.textContent = 'Detener Sistema';
                systemBtn.classList.add('active');
            } else {
                systemBtn.textContent = 'Iniciar Sistema';
                systemBtn.classList.remove('active');
            }
            
            const recordBtn = document.getElementById('recordBtn');
            if (data.recording) {
                recordBtn.textContent = 'Detener Grabación Ambas';
                recordBtn.classList.add('recording');
            } else {
                recordBtn.textContent = 'Iniciar Grabación Ambas';
                recordBtn.classList.remove('recording');
            }
        }
        
        // ---- Proporción real de cada cámara ----------------------------
        // Lo único que la web sigue necesitando de la telemetría: la relación
        // de aspecto que reporta el driver, para que el contenedor la respete
        // y un 4:3 no se vea estirado. Los contadores de FPS y de calidad se
        // quitaron de la interfaz por petición; siguen en /api/all y en el
        // informe que el bucle de vídeo imprime por consola.
        function aplicarProporcion(data) {
            var reales = data.camaras_reales || {};
            [1, 2].forEach(function (n) {
                var info = reales['cam' + n];
                var vista = document.getElementById('cam' + n + '-view');
                if (!vista || !info || !info.ancho || !info.alto) { return; }
                // --ar  : proporción para aspect-ratio (ancho / alto)
                // --arnum: la misma en número, para el calc() que limita el
                //          ancho al alto máximo permitido.
                vista.style.setProperty('--ar', info.ancho + ' / ' + info.alto);
                vista.style.setProperty('--arnum', (info.ancho / info.alto).toFixed(4));
            });
        }

        function loadVideos() {
            const grid = document.getElementById('videos-grid');
            if (!grid) { return; }
            //  Igual que la botonera: se construye con createElement y
            //  addEventListener. Antes el nombre del fichero se pegaba dentro
            //  de un onclick="..." con comillas anidadas, que es justo el
            //  patron que rompio la pagina.
            pedirJSON('/api/videos')
                .then(videos => {
                    grid.textContent = '';
                    if (!videos || videos.length === 0) {
                        const p = document.createElement('p');
                        p.textContent = 'No hay videos grabados.';
                        grid.appendChild(p);
                        return;
                    }
                    videos.forEach(video => {
                        const fecha = new Date(video.created * 1000);
                        const mb = (video.size / 1024 / 1024).toFixed(2);

                        const tarjeta = document.createElement('div');
                        tarjeta.className = 'video-card';

                        const miniatura = document.createElement('div');
                        miniatura.className = 'video-thumbnail';
                        miniatura.textContent = '🎥 ' + String(video.camera).toUpperCase();

                        const info = document.createElement('div');
                        info.className = 'video-info';

                        const titulo = document.createElement('div');
                        titulo.className = 'video-title';
                        titulo.textContent = video.name;

                        const meta = document.createElement('div');
                        meta.className = 'video-meta';
                        meta.textContent = video.camera + ' · ' + mb + ' MB · ' +
                            fecha.toLocaleDateString() + ' ' + fecha.toLocaleTimeString();

                        const acciones = document.createElement('div');
                        acciones.className = 'video-actions';
                        [['Reproducir', playVideo], ['Descargar', downloadVideo]].forEach(par => {
                            const b = document.createElement('button');
                            b.className = 'video-action-btn';
                            b.textContent = par[0];
                            b.addEventListener('click', () => par[1](video.name, video.camera));
                            acciones.appendChild(b);
                        });

                        info.appendChild(titulo);
                        info.appendChild(meta);
                        info.appendChild(acciones);
                        tarjeta.appendChild(miniatura);
                        tarjeta.appendChild(info);
                        grid.appendChild(tarjeta);
                    });
                })
                .catch(e => {
                    grid.textContent = 'Error cargando videos.';
                    avisar('No se pudo obtener la lista de vídeos', e);
                });
        }
        
        // Solo queda la pestaña de vídeos, pero se conserva la función porque
        // el botón "Ver Videos Grabados" la llama: lleva a la lista y la
        // recarga, que es lo que se espera al pulsarlo.
        function showTab(tabName) {
            var panel = document.getElementById(tabName + '-tab');
            if (!panel) { return; }
            document.querySelectorAll('.tab-content').forEach(function (c) {
                c.classList.remove('active');
            });
            panel.classList.add('active');
            currentTab = tabName;
            if (tabName === 'videos') {
                loadVideos();
                panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
        }
        
        function toggleSystem() {
            enviarJSON('/toggle_system')
                .then(data => { avisar(data.message || 'Sistema conmutado'); fetchData(); })
                .catch(e => avisar('No se pudo iniciar/detener el sistema', e));
        }
        
        function toggleRecording() {
            enviarJSON('/toggle_recording')
                .then(data => { avisar(data.message || 'Grabación conmutada'); fetchData(); })
                .catch(e => avisar('No se pudo cambiar la grabación', e));
        }

        function toggleRecognition() {
            enviarJSON('/toggle_recognition')
                .then(data => { avisar(data.message || 'Reconocimiento conmutado'); fetchData(); })
                .catch(e => avisar('No se pudo cambiar el reconocimiento', e));
        }

        // ===== Pantalla completa =====
        // Tres cosas que antes fallaban:
        //   1. Se ponía en pantalla completa solo el vídeo, así que se perdían
        //      la botonera y el slider de velocidad.
        //   2. requestFullscreen devuelve una promesa que puede RECHAZAR (sin
        //      gesto de usuario, permiso denegado...) y no existe en iOS Safari
        //      para un <div>. Sin catch ni respaldo, el botón no hacía nada en
        //      el iPhone y no se enteraba nadie.
        //   3. No había forma de saber cómo salir en un móvil (no hay Esc).
        function _stage() { return document.getElementById('cam1-stage'); }
        function _fsNativa() {
            return document.fullscreenElement || document.webkitFullscreenElement;
        }
        function _enPantallaCompleta() {
            var el = _stage();
            return !!(_fsNativa() || (el && el.classList.contains('pseudo-fs')));
        }

        function _entrarPseudoFS(el) {
            el.classList.add('pseudo-fs');
            document.body.style.overflow = 'hidden';   // sin scroll detrás
            _pintarBotonFS();
        }
        function _salirPseudoFS(el) {
            el.classList.remove('pseudo-fs');
            document.body.style.overflow = '';
            _pintarBotonFS();
        }

        function _pintarBotonFS() {
            var txt = document.getElementById('fsBtnTxt');
            if (txt) { txt.textContent = _enPantallaCompleta() ? 'Salir' : 'Pantalla completa'; }
        }

        function toggleCameraFullscreen() {
            var el = _stage();
            if (!el) return;

            if (_enPantallaCompleta()) {
                if (_fsNativa()) {
                    var exit = document.exitFullscreen || document.webkitExitFullscreen;
                    if (exit) { exit.call(document); }
                }
                _salirPseudoFS(el);
                return;
            }

            var req = el.requestFullscreen || el.webkitRequestFullscreen;
            if (!req) { _entrarPseudoFS(el); return; }   // iOS y similares
            var p;
            try { p = req.call(el); }
            catch (e) { _entrarPseudoFS(el); return; }
            // Si la promesa se rechaza, no dejar al usuario sin nada.
            if (p && typeof p.catch === 'function') {
                p.catch(function () { _entrarPseudoFS(el); });
            }
            //  RED DE SEGURIDAD: hay navegadores (y navegadores embebidos en
            //  apps) donde requestFullscreen ni lanza, ni rechaza la promesa,
            //  ni entra: simplemente no hace nada, y el botón parece roto. Se
            //  comprueba poco después si de verdad enganchó; si no, se usa el
            //  respaldo, que es CSS puro y funciona en todas partes.
            setTimeout(function () {
                if (!_fsNativa() && !el.classList.contains('pseudo-fs')) {
                    _entrarPseudoFS(el);
                }
            }, 350);
            _pintarBotonFS();
        }

        // El usuario también puede salir con Esc o con el gesto del sistema:
        // hay que enterarse para actualizar el botón.
        ['fullscreenchange', 'webkitfullscreenchange'].forEach(function (ev) {
            document.addEventListener(ev, _pintarBotonFS);
        });
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape') {
                var el = _stage();
                if (el && el.classList.contains('pseudo-fs')) { _salirPseudoFS(el); }
            }
        });

        function switchCamera() {
            enviarJSON('/switch_camera')
                .then(data => { avisar(data.message || 'Cámara cambiada'); fetchData(); })
                .catch(e => avisar('No se pudo cambiar de cámara', e));
        }
        
        // Se quitaron optimizeCamera() y centerCamera(): sus botones vivían en
        // la pestaña "Configuración", que ya no existe. Centrar servomotores
        // además no hacía nada en este robot (la cámara va fija). Las rutas
        // /optimize_camera y /center_camera siguen en la API por si algo
        // externo las usa.

        function playVideo(filename, camera) {
            window.open(`/play_video/${camera}/${filename}`, '_blank');
        }
        
        function downloadVideo(filename, camera) {
            window.open(`/download_video/${camera}/${filename}`, '_blank');
        }
        
        //  Se avisa solo cuando el estado CAMBIA (conectado -> caído y vuelta),
        //  no en cada sondeo: con /api/all cada segundo, avisar siempre sería
        //  una barra parpadeando sin parar.
        let _apiCaida = false;

        //  Huella de la interfaz con la que se sirvio ESTA pagina. El servidor
        //  la sustituye al renderizar. Si /api/all devuelve otra distinta, el
        //  navegador esta enseñando una copia guardada en cache: el codigo del
        //  robot esta actualizado y esta pantalla no. Es justo lo que hacia que
        //  un fallo ya corregido siguiera pareciendo "los botones no responden".
        const BUILD_PAGINA = '__BUILD_WEB__';
        let _avisadoDesfase = false;
        function comprobarBuild(servidor) {
            if (!servidor || servidor === BUILD_PAGINA || _avisadoDesfase) { return; }
            _avisadoDesfase = true;
            const barra = document.getElementById('avisoBarra');
            if (barra) { barra.classList.add('fatal'); }
            avisar('Esta página es una copia antigua guardada por el navegador. ' +
                   'Recarga con Ctrl+F5 (o borra los datos del sitio en el móvil).',
                   'build de la página: ' + BUILD_PAGINA + ' / del robot: ' + servidor);
        }

        // ===== Audio del microfono de la camara =====
        //  Un solo <audio> creado a mano, sin ponerlo en el HTML: si estuviera
        //  en la pagina con src="/audio", el navegador abriria el microfono al
        //  cargar aunque nadie quisiera escuchar, y dejaria un arecord vivo en
        //  la Raspberry en cada visita.
        let _audio = null;

        //  El boton no lleva texto: el estado se ve en el propio icono. Con
        //  ondas = sonando; con una cruz = mudo. Se cambia enseñando u
        //  ocultando dos grupos del SVG, sin recrear el dibujo.
        function pintarIconoAudio(sonando) {
            const ondas = document.getElementById('audioOndas');
            const cruz = document.getElementById('audioCruz');
            if (ondas) { ondas.style.display = sonando ? '' : 'none'; }
            if (cruz) { cruz.style.display = sonando ? 'none' : ''; }
        }

        // --- Mantener la escucha AL DIA con el directo ---
        //  El <audio> reproduce siempre a 1x y no se salta nada. Cualquier
        //  tiron de red, o tener la pestana un rato en segundo plano, le mete
        //  audio de golpe en su buffer... y ese retraso SE QUEDA PUESTO, y se
        //  va sumando tiron tras tiron. Por eso "el audio de la camara va con
        //  mucho retraso", y a peor cuanto mas rato lleva abierto: el robot
        //  manda al instante, pero el navegador va reproduciendo lo viejo.
        //  Aqui se vigila la distancia al directo y se corrige.
        const AUDIO_COLCHON = 0.25;     // s de retraso que se dan por buenos
        const AUDIO_SALTAR = 1.0;       // a partir de aqui, saltar al directo
        const AUDIO_ACELERAR = 1.04;    // 4%: no se nota y alcanza solo
        let _audioVigia = null;

        //  Cuanto audio tiene guardado por delante = lo que va por detras.
        function retrasoDelDirecto(a) {
            const b = a.buffered;
            if (!b || !b.length) { return 0; }
            return Math.max(0, b.end(b.length - 1) - a.currentTime);
        }

        function alDiaConElDirecto() {
            if (!_audio) { return; }
            const retraso = retrasoDelDirecto(_audio);
            if (retraso > AUDIO_SALTAR) {
                //  Muy atrasado: saltar al final de lo que hay. Se pierde lo
                //  de en medio, que es exactamente lo que se quiere: es audio
                //  viejo, y esto es un directo.
                try {
                    const b = _audio.buffered;
                    _audio.currentTime = b.end(b.length - 1) - AUDIO_COLCHON;
                    _audio.playbackRate = 1.0;
                } catch (e) {
                    //  Algunos navegadores no dejan moverse por un flujo que
                    //  no tiene final. Reabrir la peticion deja el retraso a
                    //  cero igual, a costa de un huequito.
                    console.warn('[medibot] no se pudo saltar al directo', e);
                    reengancharAudio();
                }
                return;
            }
            //  Atrasado de poco: acelerar un pelin hasta alcanzarlo, que no
            //  se nota, en vez de pegar un salto que si se oye.
            _audio.playbackRate = (retraso > AUDIO_COLCHON * 1.5)
                ? AUDIO_ACELERAR : 1.0;
        }

        function reengancharAudio() {
            if (!_audio) { return; }
            pararAudio();
            alternarAudio();
        }

        function alternarAudio() {
            const btn = document.getElementById('audioBtn');
            if (_audio) { pararAudio(); return; }

            _audio = new Audio('/audio?t=' + Date.now());   // sin cache
            _audio.autoplay = true;
            //  Pedirle al navegador que guarde lo menos posible: lo que
            //  guarde de mas es retraso. No todos hacen caso, de ahi la
            //  vigilancia de arriba.
            _audio.preload = 'none';
            _audioVigia = setInterval(alDiaConElDirecto, 1000);
            _audio.addEventListener('error', () => {
                //  El navegador no dice POR QUE falla un <audio>, asi que se
                //  le pregunta al servidor: el sabe si falta el microfono, si
                //  falta arecord o si el audio esta desactivado.
                pedirJSON('/api/audio')
                    .then(d => avisar('No se pudo escuchar: ' +
                                      (d.motivo || 'el micrófono no responde'), d))
                    .catch(e => avisar('No se pudo escuchar el micrófono', e));
                pararAudio();
            });
            _audio.play().catch(e => {
                avisar('El navegador bloqueó el audio; vuelve a pulsar Escuchar', e);
                pararAudio();
            });
            btn.classList.add('sonando');
            btn.title = 'Dejar de escuchar';
            btn.setAttribute('aria-label', 'Dejar de escuchar');
            pintarIconoAudio(true);
        }

        function pararAudio() {
            if (_audioVigia) { clearInterval(_audioVigia); _audioVigia = null; }
            if (_audio) {
                //  Vaciar el src ademas de pause(): sin esto la peticion sigue
                //  abierta y el arecord del robot no se entera de que ya no hay
                //  nadie escuchando.
                _audio.pause();
                _audio.removeAttribute('src');
                _audio.load();
                _audio = null;
            }
            const btn = document.getElementById('audioBtn');
            if (btn) {
                btn.classList.remove('sonando');
                btn.title = 'Escuchar el micrófono de la cámara';
                btn.setAttribute('aria-label', 'Escuchar el micrófono de la cámara');
            }
            pintarIconoAudio(false);
        }

        //  Si el robot dice que no hay audio, el boton se desactiva y explica
        //  el motivo en su tooltip, en vez de fallar al pulsarlo.
        function reflejarAudio(info) {
            const btn = document.getElementById('audioBtn');
            if (!btn || !info) { return; }
            btn.disabled = !info.disponible;
            if (!info.disponible) {
                btn.title = 'Audio no disponible: ' + (info.motivo || 'sin micrófono');
                if (_audio) { pararAudio(); }
            } else if (!btn.classList.contains('sonando')) {
                btn.title = 'Escuchar el micrófono de la cámara';
            }
        }

        // ===== Hablar por el altavoz del robot (intercomunicador) =====
        //  Se manda PCM EN CRUDO, no WebM/Opus: asi la Pi no descodifica nada
        //  (ver medibot_audio.py). MediaRecorder seria mas corto de escribir
        //  pero entrega Opus, y descomprimirlo en la Pi pediria ffmpeg.
        const VOZ_HZ = 16000;
        //  ~128 ms por envio (2048 muestras a 16 kHz): corto para que se oiga
        //  segun hablas, largo para no ahogar a la Pi a peticiones.
        const VOZ_MUESTRAS_POR_ENVIO = 2048;
        //  Si la red se atasca se tiran los trozos VIEJOS en vez de acumular
        //  retraso: en un intercomunicador la voz de hace tres segundos ya no
        //  sirve de nada. Mismo criterio que al escuchar.
        const VOZ_COLA_MAXIMA = 12;

        //  El recolector corre en el hilo de audio, no en el principal. Aqui
        //  importa: esta pagina esta pintando dos vídeos MJPEG, y en el hilo
        //  principal la captura saldria a trompicones.
        const VOZ_WORKLET = `
            class Recolector extends AudioWorkletProcessor {
                process(entradas) {
                    const canal = entradas[0] && entradas[0][0];
                    if (canal) { this.port.postMessage(new Float32Array(canal)); }
                    return true;
                }
            }
            registerProcessor('recolector', Recolector);
        `;

        let _voz = null;

        //  La direccion HTTPS de la propia Pi, que el robot nos dice en
        //  /api/voz. Sirve para poder mandar ahi al usuario en vez de
        //  soltarle "hace falta HTTPS" y que se busque la vida.
        let _urlSegura = '';

        //  El motivo por el que no se puede hablar, o '' si se puede.
        function porQueNoSePuedeHablar() {
            //  getUserMedia SOLO existe en contexto seguro. Por
            //  http://<ip-de-la-pi>:5000 no lo hay y el navegador ni define
            //  navigator.mediaDevices: sin este aviso el boton fallaria con
            //  un "undefined" que no le dice nada a nadie. Es, de largo, la
            //  razon mas probable de que esto no funcione.
            if (!window.isSecureContext) {
                //  El robot sirve la MISMA web por HTTPS con un certificado
                //  suyo, sin depender de nada de fuera. Se manda ahi
                //  directamente: la primera vez el navegador avisa del
                //  certificado y se acepta, y ya queda.
                const destino = _urlSegura ||
                    ('https://' + location.hostname + ':5443/');
                return 'Para hablar hace falta HTTPS. Abre ' + destino +
                       ' (la primera vez el navegador avisa del certificado: ' +
                       'acepta y continúa). Escuchar sí funciona por HTTP.';
            }
            if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
                return 'Este navegador no deja capturar el micrófono.';
            }
            if (!(window.AudioContext || window.webkitAudioContext)) {
                return 'Este navegador no tiene Web Audio.';
            }
            return '';
        }

        function pintarBotonHablar(hablando) {
            const b = document.getElementById('hablarBtn');
            if (!b) { return; }
            b.classList.toggle('hablando', !!hablando);
            const t = hablando ? 'Hablando... suelta para dejar de hablar'
                               : 'Mantén pulsado para hablar por el altavoz del robot';
            b.title = t;
            b.setAttribute('aria-label', t);
        }

        //  Float32 (-1..1) -> enteros de 16 bits, que es lo que espera aplay.
        function aPCM16(muestras) {
            const salida = new Int16Array(muestras.length);
            for (let i = 0; i < muestras.length; i++) {
                //  Recortar ANTES de escalar: una muestra por encima de 1
                //  daria la vuelta al entero y se oiria un chasquido.
                const v = Math.max(-1, Math.min(1, muestras[i]));
                salida[i] = v < 0 ? v * 0x8000 : v * 0x7FFF;
            }
            return salida;
        }

        //  Red de seguridad: casi todos los navegadores aceptan que se les
        //  pida el AudioContext ya a 16 kHz y esto no se usa. Si alguno lo
        //  ignora (y da 44,1 o 48 kHz), sin remuestrear la voz llegaria
        //  acelerada y aguda, como un dibujo animado.
        function remuestrear(muestras, deHz, aHz) {
            if (deHz === aHz || !muestras.length) { return muestras; }
            const razon = deHz / aHz;
            const salida = new Float32Array(Math.floor(muestras.length / razon));
            for (let i = 0; i < salida.length; i++) {
                const pos = i * razon, j = Math.floor(pos), resto = pos - j;
                const a = muestras[j];
                const b = (j + 1 < muestras.length) ? muestras[j + 1] : a;
                salida[i] = a + (b - a) * resto;      // interpolacion lineal
            }
            return salida;
        }

        //  Junta lo acumulado y lo pone en la cola de envio.
        function volcarVoz(v) {
            if (!v.muestras) { return; }
            const junto = new Float32Array(v.muestras);
            let o = 0;
            for (const t of v.trozos) { junto.set(t, o); o += t.length; }
            v.trozos = [];
            v.muestras = 0;
            v.cola.push(aPCM16(junto).buffer);
            while (v.cola.length > VOZ_COLA_MAXIMA) { v.cola.shift(); }
        }

        function encolarVoz(bloque) {
            //  Con el boton ya soltado no se captura mas: lo que quede por
            //  mandar es lo que se dijo ANTES de soltar, y nada posterior.
            if (!_voz || _voz.cerrando) { return; }
            const m = remuestrear(bloque, _voz.ctx.sampleRate, VOZ_HZ);
            _voz.trozos.push(m);
            _voz.muestras += m.length;
            if (_voz.muestras < VOZ_MUESTRAS_POR_ENVIO) { return; }
            volcarVoz(_voz);
            bombearVoz();
        }

        //  UN envio cada vez. Si se lanzaran en paralelo, el orden de llegada
        //  no estaria garantizado y la voz saldria con las silabas cambiadas
        //  de sitio.
        function bombearVoz() {
            if (!_voz || _voz.enviando) { return; }
            if (!_voz.cola.length) {
                //  Sin nada pendiente y con el boton soltado, aqui termina
                //  la parrafada. Terminar AQUI y no al soltar es lo que hace
                //  que no se pierda el final de la frase.
                if (_voz.cerrando) { terminarVoz(); }
                return;
            }
            const trozo = _voz.cola.shift();
            _voz.enviando = true;
            _voz.seq += 1;
            fetch('/hablar?seq=' + _voz.seq, {
                method: 'POST',
                headers: { 'Content-Type': 'application/octet-stream' },
                body: trozo
            }).then(r => {
                if (r.ok) { return null; }
                return r.json().catch(() => null).then(d => {
                    throw new Error((d && d.motivo) || ('HTTP ' + r.status));
                });
            }).catch(e => {
                avisar('No se pudo hablar: ' + e.message, e);
                //  Si falla la red no tiene sentido seguir vaciando la cola
                //  contra un robot que no contesta: se corta del todo.
                soltarCaptura(_voz);
                terminarVoz();
            }).then(() => {
                if (_voz) { _voz.enviando = false; bombearVoz(); }
            });
        }

        function empezarAHablar() {
            //  Si la anterior aun esta vaciando la cola (red lenta), se corta
            //  y empieza la nueva: si no, el boton se quedaria muerto
            //  esperando a que termine algo que igual no termina nunca.
            if (_voz && !_voz.cerrando) { return; }
            if (_voz) { terminarVoz(); }
            const problema = porQueNoSePuedeHablar();
            if (problema) { avisar(problema); return; }

            //  Marcar YA que se esta hablando: pedir el microfono tarda (la
            //  primera vez el navegador pregunta), y sin esto el boton se
            //  queda apagado y parece que no ha hecho nada.
            _voz = { cola: [], trozos: [], muestras: 0, seq: 0,
                     enviando: false, cerrando: false,
                     sonabaAntes: !!_audio };
            pintarBotonHablar(true);

            //  Callar la escucha mientras hablas. Si no, se acopla: altavoz
            //  del robot -> microfono de la camara -> tu navegador -> tu
            //  altavoz -> tu microfono -> otra vez al robot, y empieza a
            //  pitar. Al soltar se vuelve a encender si estaba encendida.
            if (_audio) { pararAudio(); }

            const Ctx = window.AudioContext || window.webkitAudioContext;
            //  echoCancellation/noiseSuppression: el navegador ya trae
            //  cancelacion de eco hecha y probada. Es lo unico que hay contra
            //  el acople, asi que se pide siempre.
            navigator.mediaDevices.getUserMedia({
                audio: { echoCancellation: true, noiseSuppression: true,
                         autoGainControl: true, channelCount: 1 },
                video: false
            }).then(stream => {
                if (!_voz || _voz.cerrando) {
                    stream.getTracks().forEach(t => t.stop());
                    return;
                }
                _voz.stream = stream;
                //  Pedir el contexto ya a 16 kHz: asi remuestrea el navegador,
                //  que lo hace mejor y gratis.
                let ctx;
                try { ctx = new Ctx({ sampleRate: VOZ_HZ }); }
                catch (e) { ctx = new Ctx(); }
                _voz.ctx = ctx;
                _voz.fuente = ctx.createMediaStreamSource(stream);

                //  Un nodo con ganancia CERO hasta la salida. Hace falta
                //  porque en varios navegadores el grafo no "tira" de un nodo
                //  que no llega a la salida, y no llegaria ni una muestra. A
                //  cero no se oye: si se conectara directo, te oirias a ti
                //  mismo con retraso.
                _voz.mudo = ctx.createGain();
                _voz.mudo.gain.value = 0;
                _voz.mudo.connect(ctx.destination);

                const conWorklet = ctx.audioWorklet && window.AudioWorkletNode;
                const arranque = conWorklet
                    ? ctx.audioWorklet.addModule(
                          URL.createObjectURL(new Blob([VOZ_WORKLET],
                              { type: 'application/javascript' })))
                      .then(() => {
                          const nodo = new AudioWorkletNode(ctx, 'recolector');
                          nodo.port.onmessage = ev => encolarVoz(ev.data);
                          return nodo;
                      })
                    //  ScriptProcessorNode esta obsoleto, pero es lo unico
                    //  que hay en navegadores viejos y funciona.
                    : Promise.resolve().then(() => {
                          const nodo = ctx.createScriptProcessor(4096, 1, 1);
                          nodo.onaudioprocess = ev => encolarVoz(
                              new Float32Array(ev.inputBuffer.getChannelData(0)));
                          return nodo;
                      });

                return arranque.then(nodo => {
                    if (!_voz || _voz.cerrando) { return; }
                    _voz.nodo = nodo;
                    _voz.fuente.connect(nodo);
                    nodo.connect(_voz.mudo);
                    //  En Safari el contexto nace suspendido.
                    if (ctx.state === 'suspended') { return ctx.resume(); }
                });
            }).catch(e => {
                const porque = (e && e.name === 'NotAllowedError')
                    ? 'no diste permiso al micrófono en el navegador'
                    : (e && e.message) || 'el navegador no dejó';
                avisar('No se pudo hablar: ' + porque, e);
                dejarDeHablar();
            });
        }

        //  Suelta el microfono del navegador y desmonta el grafo de audio.
        //  Se hace NADA MAS soltar el boton, no cuando acabe de mandarse la
        //  cola: si no, quedaria el punto rojo de "esta pagina te esta
        //  escuchando" mas tiempo del que de verdad se esta capturando.
        function soltarCaptura(v) {
            if (!v || v.soltada) { return; }
            v.soltada = true;
            const pasos = [
                () => { if (v.nodo) { v.nodo.port ? (v.nodo.port.onmessage = null)
                                                  : (v.nodo.onaudioprocess = null); } },
                () => { if (v.nodo) v.nodo.disconnect(); },
                () => { if (v.fuente) v.fuente.disconnect(); },
                () => { if (v.mudo) v.mudo.disconnect(); },
                () => { if (v.stream) v.stream.getTracks().forEach(t => t.stop()); },
                () => { if (v.ctx && v.ctx.close) v.ctx.close(); }
            ];
            for (const paso of pasos) {
                try { paso(); } catch (e) { console.warn('[medibot] soltando voz', e); }
            }
        }

        function terminarVoz() {
            if (!_voz) { return; }
            const v = _voz;
            _voz = null;
            soltarCaptura(v);
            pintarBotonHablar(false);
            //  Volver a escuchar si se estaba escuchando antes de hablar.
            if (v.sonabaAntes && !_audio) { alternarAudio(); }
        }

        function dejarDeHablar() {
            if (!_voz || _voz.cerrando) { return; }
            const v = _voz;
            v.cerrando = true;
            soltarCaptura(v);          // el microfono se suelta YA
            pintarBotonHablar(false);

            //  Mandar tambien lo que quedaba a medio juntar. Sin esto se
            //  pierden hasta 128 ms y se come la ultima silaba de cada
            //  frase; y lo que ya estaba en la cola son palabras que YA
            //  dijiste, asi que se terminan de mandar. Cuando se vacie,
            //  bombearVoz() llama a terminarVoz().
            volcarVoz(v);
            bombearVoz();
            if (!v.cola.length && !v.enviando) { terminarVoz(); }
        }

        //  Si el robot dice que no hay altavoz, el boton se desactiva y
        //  explica el motivo, en vez de fallar al pulsarlo.
        function reflejarVoz(info) {
            const b = document.getElementById('hablarBtn');
            if (!b || !info) { return; }
            if (info.url_segura) { _urlSegura = info.url_segura; }
            const problema = porQueNoSePuedeHablar();
            b.disabled = !info.disponible;
            if (!info.disponible) {
                b.title = 'No se puede hablar: ' + (info.motivo || 'sin altavoz');
                if (_voz) { dejarDeHablar(); }
            } else if (problema) {
                //  El robot puede, el navegador no: se deja pulsable para que
                //  al pulsarlo explique lo del HTTPS (si se desactivara, no
                //  habria forma de saber por que).
                b.title = problema;
            } else if (!_voz) {
                b.title = 'Mantén pulsado para hablar por el altavoz del robot';
            }
        }

        function prepararBotonHablar() {
            const b = document.getElementById('hablarBtn');
            if (!b) { return; }
            //  Eventos de puntero: valen igual para raton y para dedo, sin
            //  escribir el doble ni pelearse con el clic fantasma del movil.
            b.addEventListener('pointerdown', ev => {
                ev.preventDefault();          // en el movil, ni zoom ni seleccion
                empezarAHablar();
            });
            //  Soltar se escucha en TODA la ventana, no solo en el boton: si
            //  sueltas el dedo fuera, el pointerup del boton no llega y te
            //  quedarias con el microfono abierto sin saberlo.
            for (const ev of ['pointerup', 'pointercancel']) {
                window.addEventListener(ev, () => dejarDeHablar());
            }
            //  Cambiar de pestana o de ventana tambien corta: nadie quiere
            //  seguir emitiendo desde una pestana que ya no mira.
            window.addEventListener('blur', () => dejarDeHablar());
            document.addEventListener('visibilitychange', () => {
                if (document.hidden) { dejarDeHablar(); }
            });
        }

        // ===== Deteccion de objetos rojos =====
        //  Se puede apagar en caliente: ahorra la conversion a HSV, dos
        //  morfologias y findContours en cada fotograma de cada camara.
        function alternarDeteccionRojo() {
            enviarJSON('/toggle_deteccion_rojo', {})
                .then(d => { reflejarDeteccionRojo(d.deteccion_rojo); avisar(d.message); })
                .catch(e => avisar('No se pudo cambiar la detección de color', e));
        }

        function reflejarDeteccionRojo(activo) {
            const b = document.getElementById('rojoBtn');
            if (!b) { return; }
            b.textContent = activo ? 'Color rojo: ON' : 'Color rojo: OFF';
            b.classList.toggle('active', !!activo);
        }

        // ===== Enlace al Pastillero =====
        //  El servidor puede fijar la URL (util detras de un tunel, donde cada
        //  interfaz tiene su propio subdominio y los puertos no valen). Si no
        //  la fija, se deduce del mismo host cambiando al puerto 5001, que es
        //  lo correcto en la red local.
        const URL_PASTILLERO = '__URL_PASTILLERO__';
        function prepararEnlacePastillero() {
            const a = document.getElementById('linkPastillero');
            if (!a) { return; }
            a.href = URL_PASTILLERO ||
                     (location.protocol + '//' + location.hostname + ':5001/');
        }

        function fetchData() {
            return pedirJSON('/api/all')
                .then(data => {
                    comprobarBuild(data.build_web);
                    reflejarAudio(data.audio);
                    reflejarVoz(data.voz);
                    reflejarDeteccionRojo(data.deteccion_rojo);
                    updateCameraStatus(data);
                    if (_apiCaida) { _apiCaida = false; limpiarAviso(); }
                })
                .catch(e => {
                    if (!_apiCaida) {
                        _apiCaida = true;
                        avisar('Sin contacto con el robot: los datos no se actualizan', e);
                    } else {
                        console.error('[medibot] /api/all sigue fallando', e);
                    }
                });
        }
        
        // Start updates
        function startUpdates() {
            if (updateInterval) {
                clearInterval(updateInterval);
            }
            updateInterval = setInterval(fetchData, 1000);
            fetchData(); // Initial call
        }
        
        // Stop updates
        function stopUpdates() {
            if (updateInterval) {
                clearInterval(updateInterval);
                updateInterval = null;
            }
        }
        
        // ===== Botonera de movimiento =====
        //  Los botones se construyen a partir de /movimientos, que devuelve la
        //  MISMA lista que usan el firmware y el mando PS2. Asi no hay nombres
        //  duplicados a mano que puedan quedar desincronizados.
        function enviarMovimiento(mov) {
            enviarJSON('/move', { movimiento: mov })
                .catch(e => avisar('No llegó la orden de movimiento al robot', e));
        }

        // ===== Velocidad del chasis =====
        // Antes: si el servidor respondía 400 (o no respondía), el then no
        // hacía nada y el catch se tragaba el error. El slider se quedaba
        // donde lo había dejado el usuario mientras el robot iba a otra
        // velocidad, sin ninguna señal. Ahora se refleja SIEMPRE lo realmente
        // aplicado y se avisa si no se pudo aplicar.
        function _avisoVelocidad(texto) {
            var el = document.getElementById('velAviso');
            if (el) { el.textContent = texto || ''; }
        }

        function _pintarVelocidad(valor) {
            var rango = document.getElementById('velRange');
            var etiqueta = document.getElementById('velVal');
            if (etiqueta) { etiqueta.textContent = valor; }
            if (rango) { rango.value = valor; }
        }

        // Al cargar la página, preguntar al servidor el rango y la velocidad
        // REALES. El min/max estaban escritos a mano en el HTML (200/255): si
        // alguien cambiaba VEL_MIN/VEL_MAX en Python, el slider mentía.
        function sincronizarVelocidad() {
            return pedirJSON('/velocidad')
                .then(d => {
                    var rango = document.getElementById('velRange');
                    if (!rango || !d) return;
                    if (d.min !== undefined) rango.min = d.min;
                    if (d.max !== undefined) rango.max = d.max;
                    if (d.velocidad !== undefined) _pintarVelocidad(d.velocidad);
                    _avisoVelocidad('');
                })
                .catch(e => {
                    _avisoVelocidad('Sin conexión');
                    avisar('No se pudo leer la velocidad del robot', e);
                });
        }

        function fijarVelocidad(v) {
            var pedida = parseInt(v, 10);
            if (isNaN(pedida)) { return sincronizarVelocidad(); }
            enviarJSON('/velocidad', { velocidad: pedida })
                .then(datos => {
                    // El servidor recorta al rango válido: reflejar lo
                    // REALMENTE aplicado, no lo que pidió el usuario.
                    _pintarVelocidad(datos.velocidad);
                    _avisoVelocidad(datos.velocidad !== pedida
                        ? 'Ajustada a ' + datos.velocidad + ' (rango del firmware)'
                        : '');
                })
                .catch(e => {
                    _avisoVelocidad('No se pudo aplicar');
                    avisar('El robot no aceptó la velocidad', e);
                    sincronizarVelocidad();      // volver a mostrar la real
                });
        }

        // Se quitó lanzarTruco(): los cuatro botones de trucos ya no están en
        // la web. La ruta /truco sigue existiendo y el mando PS2 los conserva.

        (function construirBotonera() {
            const grid = document.getElementById('mov-grid');
            if (!grid) return;
            //  Los botones se crean con createElement y addEventListener, NO
            //  concatenando un onclick dentro de una cadena. Ese patron es el
            //  que provoco el fallo: llevaba comillas escapadas (\') que
            //  Python se comia antes de servir la pagina, y el <script> entero
            //  dejaba de compilar. Sin comillas anidadas no puede repetirse, y
            //  ademas el id viaja en una variable, no pegado al HTML.
            pedirJSON('/movimientos')
                .then(d => {
                    //  Orden de la rejilla 3x3: los laterales a los lados, los
                    //  giros en las esquinas y parar en el centro, para que la
                    //  disposicion se entienda de un vistazo.
                    const orden = ['SPINL', 'FWD', 'SPINR',
                                   'LEFT',  'STOP', 'RIGHT',
                                   '',      'BACK', ''];
                    const etiquetas = {};
                    (d.movimientos || []).forEach(m => { etiquetas[m.id] = m.etiqueta; });
                    grid.textContent = '';
                    orden.forEach(id => {
                        if (!id) { grid.appendChild(document.createElement('span')); return; }
                        const b = document.createElement('button');
                        b.className = (id === 'STOP') ? 'mov-btn stop' : 'mov-btn';
                        b.textContent = etiquetas[id] || id;
                        b.addEventListener('click', () => enviarMovimiento(id));
                        grid.appendChild(b);
                    });
                })
                .catch(e => avisar('No se pudo cargar la botonera de movimiento', e));
        })();

        // ===== Control de movimiento (joystick) =====
        (function() {
            const activeDirs = new Set();
            let lastSent = null;
            const dirsLabel = document.getElementById('move-dirs');

            function sendMove() {
                const dirs = Array.from(activeDirs);
                const key = dirs.slice().sort().join('');
                if (key === lastSent) return;
                lastSent = key;
                if (dirsLabel) dirsLabel.textContent = dirs.length ? dirs.join(', ').toUpperCase() : '—';
                enviarJSON('/move', { directions: dirs })
                    .catch(e => avisar('No llegó la orden de movimiento al robot', e));
            }

            function setDir(d, on) {
                if (!d) { activeDirs.clear(); sendMove(); return; }
                if (on) activeDirs.add(d); else activeDirs.delete(d);
                sendMove();
            }

            // Teclado W A S D
            document.addEventListener('keydown', function(e) {
                const k = (e.key || '').toLowerCase();
                if (k === 'w' || k === 'a' || k === 's' || k === 'd') setDir(k, true);
            });
            document.addEventListener('keyup', function(e) {
                const k = (e.key || '').toLowerCase();
                if (k === 'w' || k === 'a' || k === 's' || k === 'd') setDir(k, false);
            });

            // Botones (pulsar y mantener)
            document.querySelectorAll('.move-btn').forEach(function(btn) {
                const d = btn.getAttribute('data-dir');
                const press = function(e) {
                    e.preventDefault();
                    if (!d) { setDir('', false); }
                    else { setDir(d, true); btn.classList.add('active'); }
                };
                const release = function(e) {
                    if (e) e.preventDefault();
                    if (d) { setDir(d, false); btn.classList.remove('active'); }
                };
                btn.addEventListener('mousedown', press);
                btn.addEventListener('mouseup', release);
                btn.addEventListener('mouseleave', release);
                btn.addEventListener('touchstart', press, { passive: false });
                btn.addEventListener('touchend', release);
            });

            // Joystick arrastrable (ratón y táctil)
            const base = document.getElementById('joyBase');
            const stick = document.getElementById('joyStick');
            if (base && stick) {
                let dragging = false;
                function handle(clientX, clientY) {
                    const r = base.getBoundingClientRect();
                    const rs = stick.getBoundingClientRect();
                    const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
                    let dx = clientX - cx, dy = clientY - cy;
                    //  EL RECORRIDO Y EL UMBRAL SE CALCULAN DEL TAMAÑO REAL.
                    //  Antes eran dos números fijos en píxeles: recorrido
                    //  "ancho/2 - 30" y umbral 14. Con la base reducida a 88 px
                    //  en móvil, el recorrido salía 44-30 = 14 exactos, o sea
                    //  IGUAL que el umbral: la condición dy < -14 nunca se
                    //  cumplía y el joystick no registraba NINGUNA dirección.
                    //  Ahora el recorrido es el hueco real entre la bola y el
                    //  borde, y el umbral una cuarta parte de ese recorrido,
                    //  así que funciona igual a cualquier tamaño.
                    const max = Math.max(8, (r.width - rs.width) / 2);
                    const th = max * 0.25;
                    const dist = Math.hypot(dx, dy) || 1;
                    if (dist > max) { dx = dx / dist * max; dy = dy / dist * max; }
                    stick.style.transform = 'translate(' + dx + 'px,' + dy + 'px)';
                    activeDirs.clear();
                    if (dx > th) activeDirs.add('d'); else if (dx < -th) activeDirs.add('a');
                    if (dy < -th) activeDirs.add('w'); else if (dy > th) activeDirs.add('s');
                    sendMove();
                }
                const start = function(e) { dragging = true; const t = e.touches ? e.touches[0] : e; handle(t.clientX, t.clientY); e.preventDefault(); };
                const move = function(e) { if (!dragging) return; const t = e.touches ? e.touches[0] : e; handle(t.clientX, t.clientY); e.preventDefault(); };
                const end = function() { if (!dragging) return; dragging = false; stick.style.transform = 'translate(0,0)'; activeDirs.clear(); sendMove(); };
                base.addEventListener('mousedown', start);
                document.addEventListener('mousemove', move);
                document.addEventListener('mouseup', end);
                base.addEventListener('touchstart', start, { passive: false });
                base.addEventListener('touchmove', move, { passive: false });
                base.addEventListener('touchend', end);
            }
        })();

        // ===== Control de tema (claro / oscuro) =====
        function setTheme(theme) {
            document.documentElement.setAttribute('data-theme', theme);
            const btn = document.getElementById('themeToggle');
            if (btn) {
                btn.textContent = theme === 'light' ? 'Modo Claro' : 'Modo Oscuro';
            }
            try { localStorage.setItem('medibot-theme', theme); } catch (e) {}
        }

        function toggleTheme() {
            const current = document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
            setTheme(current === 'light' ? 'dark' : 'light');
        }

        // Aplicar el tema guardado al cargar (por defecto: oscuro)
        (function applyStoredTheme() {
            let theme = 'dark';
            try { theme = localStorage.getItem('medibot-theme') || 'dark'; } catch (e) {}
            setTheme(theme);
        })();

        // Start updates when page loads
        document.addEventListener('DOMContentLoaded', function() {
            startUpdates();
            // Leer del servidor el rango y la velocidad REALES en vez de
            // fiarse del 200/255 escrito a mano en el HTML.
            sincronizarVelocidad();
            _pintarBotonFS();
            prepararEnlacePastillero();
            prepararBotonHablar();
            // La lista de vídeos está visible desde el principio, así que hay
            // que rellenarla; antes se quedaba en "Cargando videos..." para
            // siempre salvo que se pulsara el botón.
            loadVideos();
        });
        
        // Handle page visibility change
        document.addEventListener('visibilitychange', function() {
            if (document.hidden) {
                stopUpdates();
            } else {
                startUpdates();
            }
        });
        
        // Handle page unload
        window.addEventListener('beforeunload', function() {
            stopUpdates();
            //  Cerrar el audio al salir: si no, la peticion queda abierta y el
            //  arecord del robot sigue vivo agarrado al microfono.
            pararAudio();
        });
    </script>
</body>
</html>
"""

#  Huella de la interfaz. Sirve para saber, SIN adivinar, que version de la web
#  esta viendo un navegador: sale en la cabecera X-Medibot-Build y en /api/all.
#      curl -I http://<ip>:5000/     ->  X-Medibot-Build: a1b2c3d4
#  Si el movil enseña una huella distinta a la del servidor, esta viendo una
#  copia guardada en cache, no la que sirve el robot.
BUILD_WEB = hashlib.sha256(HTML_TEMPLATE.encode("utf-8")).hexdigest()[:8]


@app.after_request
def _sin_cache(respuesta):
    """Prohibe guardar en cache la interfaz y las respuestas de la API.

    EL FALLO QUE ARREGLA
    --------------------
    La web es UNA sola pagina con el JavaScript escrito dentro del propio HTML.
    Flask no mandaba ninguna cabecera de cache, asi que el navegador era libre
    de guardarse el HTML y reutilizarlo durante horas o dias. Cuando se corrige
    un fallo del JavaScript, el movil sigue ejecutando el guardado: el codigo
    del servidor esta bien y la interfaz sigue rota.

    Da exactamente el cuadro que se veia:
      * el video SI se ve, porque /video/<n> es una peticion nueva cada vez y
        no se cachea nunca;
      * los botones y el selector de tema NO responden, porque el HTML
        guardado lleva dentro la version antigua del JavaScript.

    Un Ctrl+F5 lo arreglaba a mano, pero en un movil casi nadie sabe hacerlo y
    habia que repetirlo en cada dispositivo. Con no-store el navegador no puede
    quedarse con una copia vieja.

    No afecta al rendimiento del video: el MJPEG ya era una respuesta continua
    que no se guardaba, y las respuestas de la API son estado en vivo que nunca
    debe servirse de una copia.
    """
    respuesta.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    respuesta.headers["Pragma"] = "no-cache"          # proxies y HTTP/1.0
    respuesta.headers["Expires"] = "0"
    respuesta.headers["X-Medibot-Build"] = BUILD_WEB
    return respuesta


#  Microfono de la camara. Uno solo para todo el servidor: ALSA no deja abrir
#  el microfono dos veces, asi que hay UNA captura y el objeto la reparte
#  entre todas las peticiones a /audio (el movil y el PC pueden escuchar a la
#  vez, y cada uno oye el audio completo). El microfono se suelta cuando se
#  va el ultimo. Ver medibot_audio.py.
microfono = medibot_audio.Microfono()

#  Altavoz de la Pi: la voz que llega del navegador (intercomunicador). Se
#  abre solo al hablar y se suelta al callar. Ver medibot_audio.py.
altavoz = medibot_audio.Altavoz()

#  URL de la otra interfaz. Vacia = el navegador la deduce del mismo host con
#  el puerto 5001, que es lo correcto en la red local. Se fija por entorno
#  cuando cada interfaz tiene su propio subdominio (p.ej. detras de un tunel
#  de Cloudflare, donde los puertos no valen).
URL_PASTILLERO = os.environ.get("MEDIBOT_URL_PASTILLERO", "").strip()


@app.route("/audio")
def audio():
    """Microfono de la camara, en WAV continuo.

    Se sirve asi porque un <audio src="/audio"> lo reproduce sin JavaScript,
    sin WebRTC y sin dependencias nuevas: `arecord` ya viene con el sistema.
    Ver medibot_audio.py para el detalle."""
    if not microfono.disponible():
        #  503 y no 404: el recurso existe, es que ahora no se puede servir.
        #  El navegador solo dice "error" al fallar un <audio>, asi que el
        #  motivo se consulta aparte en /api/audio.
        return jsonify({"error": "Audio no disponible",
                        "motivo": microfono.motivo_no_disponible()}), 503

    respuesta = Response(microfono.trozos(), mimetype="audio/wav")
    #  Sin esto algunos navegadores esperan a tener el fichero entero antes de
    #  empezar a sonar, y como no termina nunca, no suena nada.
    respuesta.headers["Accept-Ranges"] = "none"
    return respuesta


@app.route("/api/audio")
def api_audio():
    """Por que se puede (o no) escuchar. Lo consulta el boton para explicarlo."""
    return jsonify(microfono.estado())


@app.route("/hablar", methods=["POST"])
def hablar():
    """Tu voz desde el navegador -> altavoz de la Pi (intercomunicador).

    El cuerpo es PCM pelado S16_LE a 16 kHz mono, tal como lo saca la pagina
    con la Web Audio API: asi la Pi no descodifica nada (no hay ffmpeg ni
    opus-tools de por medio). Llega a trozos de ~128 ms para que se oiga
    segun se habla.

    ?seq=N numera los trozos de cada parrafada, para tirar los que lleguen
    tarde: uno atrasado sonaria como un hipido en mitad de la frase
    siguiente. Ver medibot_audio.py (clase Altavoz)."""
    if not altavoz.disponible():
        #  503 y no 404: la ruta existe, es que ahora no se puede servir.
        return jsonify({"error": "No se puede hablar",
                        "motivo": altavoz.motivo_no_disponible()}), 503

    #  Solo desde la red de casa. Un altavoz por el que cualquiera que llegue
    #  a la pagina pueda soltar voz dentro de tu casa no puede quedar abierto
    #  a internet sin querer. Ver medibot_audio.es_local: mirar la IP no
    #  basta, porque el tunel corre en la propia Pi.
    puede, porque = altavoz.permite(request.remote_addr, request.headers)
    if not puede:
        return jsonify({"error": "No se puede hablar desde aqui",
                        "motivo": porque}), 403

    #  Cortar por lo sano ANTES de leer el cuerpo: sin esto una peticion
    #  gigante se leeria entera en la memoria de la Pi solo para rechazarla.
    largo = request.content_length or 0
    if largo > medibot_audio.MAX_TROZO_VOZ:
        return jsonify({"error": "Trozo demasiado grande",
                        "motivo": f"{largo} bytes; el maximo son "
                                  f"{medibot_audio.MAX_TROZO_VOZ}"}), 413

    try:
        seq = int(request.args.get("seq", "0"))
    except ValueError:
        seq = 0
    ok, motivo = altavoz.reproducir(request.get_data(), seq or None)
    if not ok:
        return jsonify({"error": "No se pudo reproducir",
                        "motivo": motivo}), 503
    return jsonify({"ok": True, "motivo": motivo})


@app.route("/api/voz")
def api_voz():
    """Por que se puede (o no) hablar. Lo consulta el boton para explicarlo.

    Se responde segun QUIEN pregunta: desde fuera de casa el altavoz esta
    cerrado, y el boton tiene que salir apagado con su motivo en vez de
    dejarte hablar para nada y fallar en el primer trozo."""
    estado = altavoz.estado()
    puede, porque = altavoz.permite(request.remote_addr, request.headers)
    if not puede:
        estado["disponible"] = False
        estado["motivo"] = porque
    estado["url_segura"] = url_para_hablar()
    return jsonify(estado)


@app.route("/toggle_deteccion_rojo", methods=["POST"])
def toggle_deteccion_rojo():
    """Enciende o apaga la busqueda de objetos rojos, sin reiniciar.

    Apagarla ahorra ~1,3 ms por fotograma y por camara (la conversion a HSV,
    dos morfologias y findContours). En un montaje que no sigue objetos de
    color es trabajo tirado, y en una Raspberry esos milisegundos se notan.

    Se puede pedir un valor concreto con {"activo": true|false}; sin cuerpo,
    alterna. Devolver siempre el estado resultante evita que la interfaz tenga
    que adivinarlo."""
    global DETECCION_ROJO
    datos = request.get_json(silent=True) or {}
    if "activo" in datos:
        DETECCION_ROJO = bool(datos["activo"])
    else:
        DETECCION_ROJO = not DETECCION_ROJO
    if not DETECCION_ROJO:
        #  Sin esto, el ultimo objeto detectado se queda en el historial y el
        #  seguimiento sigue dibujando un rastro de algo que ya no se busca.
        object_tracker.object_history.clear()
    return jsonify({
        "deteccion_rojo": DETECCION_ROJO,
        "message": ("Detección de color rojo activada" if DETECCION_ROJO
                    else "Detección de color rojo desactivada (ahorra ~1,3 ms/fotograma)"),
    })


@app.route("/")
def index():
    """Página principal del sistema web.

    Se sustituye __BUILD_WEB__ por la huella de la plantilla (no con Jinja: el
    JavaScript esta lleno de llaves y no conviene darle mas trabajo del
    necesario al motor de plantillas). La huella se calcula sobre la plantilla
    CON el marcador dentro, asi que no hay circularidad."""
    pagina = (HTML_TEMPLATE
              .replace("__BUILD_WEB__", BUILD_WEB)
              .replace("__URL_PASTILLERO__", URL_PASTILLERO))
    return render_template_string(pagina)

@app.route("/video/<int:camera_index>")
def video(camera_index):
    """Stream MJPEG de cada cámara.

    Se mantiene MJPEG a proposito: es lo unico que un navegador (movil
    incluido) reproduce con un simple <img src>, sin JavaScript, sin WebRTC y
    sin servidor extra. En una LAN y con el perfil web ajustado, la latencia
    es de decenas de milisegundos; cambiar a WebRTC o HLS traeria
    infraestructura y complejidad que este proyecto no necesita.

    LO QUE HACE ESTE GENERADOR:
      - ESPERA (no sondea) a que haya fotograma nuevo. Ver el comentario del
        bucle: el sondeo anterior competia por el mismo lock que el hilo de
        captura, asi que frenaba la camara.
      - Emite como mucho PERFIL_WEB.fps_max fotogramas por segundo, al tamano
        y calidad del perfil web. Captura y emision son independientes: se
        puede analizar a 640x480 y emitir 480x360 q50 a 10 FPS.
      - El JPEG se codifica UNA vez por fotograma en el buzon compartido y se
        reparte a todos los navegadores (medido: con 3 clientes el coste de
        codificacion sigue siendo 1,1 ms, no 3,3 ms).
      - Solo se envia cuando hay fotograma NUEVO (numero de secuencia).
      - La codificacion es perezosa: si nadie mira la web, no se codifica nada.
      - Si el navegador se va, el generador muere y suelta su plaza (finally).
      - Con el sistema detenido no se procesa nada: solo un aviso cacheado a
        2 FPS para que el navegador no muestre la imagen rota.
    """
    if camera_index not in (0, 1):
        return "Cámara inexistente", 404

    def generate_frames():
        # El bucle NO depende de 'online': al detener el sistema se sigue
        # sirviendo un aviso, asi el navegador no se queda con la imagen rota.
        aviso_cacheado = None
        estado_aviso = None
        ultima_seq = -1
        periodo_min = 1.0 / PERFIL_WEB.fps_max     # tope de emision
        proximo_envio = 0.0

        frame_hub.entra_cliente(camera_index)
        try:
            while True:
                frame_bytes = None
                if online:
                    #  ESPERA en vez de SONDEO. Antes este bucle daba vueltas
                    #  cada 5 ms pidiendo el lock del buzon: medido con un
                    #  productor a 12 FPS y dos clientes, 15,6 despertares por
                    #  fotograma entregado y 2,81 ms de latencia media. Y ese
                    #  lock es el MISMO que necesita el hilo de captura para
                    #  publicar, asi que el sondeo no solo gastaba CPU: frenaba
                    #  la camara. Esperando en la condicion: 1,0 despertares
                    #  por fotograma y 0,25 ms de latencia.
                    espera = max(0.0, proximo_envio - time.monotonic())
                    if espera:
                        time.sleep(espera)
                    _, seq = frame_hub.esperar_nuevo(camera_index, ultima_seq,
                                                     timeout=1.0)
                    if seq != ultima_seq:
                        ancho_src, alto_src = resolucion_activa(camera_index)
                        destino = PERFIL_WEB.tamano_para(ancho_src, alto_src)
                        frame_bytes = frame_hub.jpeg(camera_index,
                                                     calidad=PERFIL_WEB.calidad,
                                                     tamano=destino)
                        if frame_bytes is not None:
                            ultima_seq = seq
                            #  El siguiente instante se ACUMULA, no se calcula
                            #  como "ahora + periodo". Medido con el bench: con
                            #  una fuente de 30 FPS y un tope de 25, reiniciar
                            #  el reloj en cada envio hacia perder un fotograma
                            #  de cada dos y emitir 15 FPS en vez de 25. Al
                            #  acumular, el ritmo medio sale el pedido.
                            ahora = time.monotonic()
                            proximo_envio += periodo_min
                            if proximo_envio < ahora - periodo_min:
                                proximo_envio = ahora   # tras una pausa, no acelerar
                            frame_hub.anotar_envio(camera_index)

                if frame_bytes is None:
                    # Aviso (negro con texto). Se genera y codifica UNA vez por
                    # estado y luego se reutiliza: antes se recreaba el array y
                    # se recodificaba el JPEG 5 veces por segundo para siempre.
                    texto = (f"Camara {camera_index + 1} no disponible"
                             if online else "Sistema detenido")
                    if aviso_cacheado is None or estado_aviso != texto:
                        ancho_src, alto_src = resolucion_activa(camera_index)
                        ancho_av, alto_av = PERFIL_WEB.tamano_para(ancho_src, alto_src)
                        aviso = np.zeros((alto_av, ancho_av, 3), dtype=np.uint8)
                        escala_txt = max(0.4, ancho_av / 640.0 * 0.7)
                        (tw, _th), _ = cv2.getTextSize(
                            texto, cv2.FONT_HERSHEY_SIMPLEX, escala_txt, 2)
                        cv2.putText(aviso, texto,
                                    (max(5, (ancho_av - tw) // 2), alto_av // 2),
                                    cv2.FONT_HERSHEY_SIMPLEX, escala_txt,
                                    (255, 255, 255), 2)
                        ok, buf = cv2.imencode('.jpg', aviso,
                                               [cv2.IMWRITE_JPEG_QUALITY,
                                                PERFIL_WEB.calidad])
                        aviso_cacheado = buf.tobytes() if ok else b''
                        estado_aviso = texto
                    frame_bytes = aviso_cacheado
                    time.sleep(0.5)     # aviso a 2 fps: no quemar CPU en vano

                # Si el navegador cierra la pestana, Flask cierra el generador
                # y aqui salta GeneratorExit. NO se captura (deriva de
                # BaseException, no de Exception): asi el generador muere de
                # verdad y el finally suelta el cliente. Antes, sin contador de
                # clientes, no habia forma de saber si quedaba alguno vivo.
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
        finally:
            frame_hub.sale_cliente(camera_index)

    return Response(generate_frames(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route("/api/all")
def api_all():
    """API que retorna TODA la información del sistema en JSON"""
    persons = get_registered_persons()
    tracking_data = object_tracker.get_tracking_data()
    
    return jsonify({
        "system_status": system_status,
        "detection_count": detection_count,
        "face_position": face_position,
        "recording": recording,
        "recording_cam1": recording_cam1,
        "recording_cam2": recording_cam2,
        "online": online,
        "fps1": fps1,
        "fps2": fps2,
        "registered_persons": len(persons),
        "tracking_enabled": object_tracker.tracking_enabled,
        "recognition_enabled": recognition_enabled,
        "tracking_data": tracking_data,
        # Reparto REAL del tiempo del bucle de video, en milisegundos por
        # etapa. Sirve para ver de un vistazo si los FPS los limita la camara
        # ("camara" alto) o nuestro procesamiento ("proceso" alto).
        #  Se mantiene la forma antigua {cam1:{etapa:ms}} para no romper a
        #  quien ya la lea, y se anaden claves nuevas al lado.
        "perf": {
            "cam1": perfilador[0].medias(),
            "cam2": perfilador[1].medias(),
            "cuello_cam1": perfilador[0].cuello_de_botella(),
            "cuello_cam2": perfilador[1].cuello_de_botella(),
            "fps_captura": {"cam1": fps1, "cam2": fps2},
            "fps_web": {"cam1": frame_hub.fps_enviados(0),
                        "cam2": frame_hub.fps_enviados(1)},
            "clientes_web": {"cam1": frame_hub.clientes(0),
                             "cam2": frame_hub.clientes(1)},
        },
        # Lo que el driver acepto DE VERDAD (no lo que se le pidio) y el perfil
        # con el que se emite a la web. La proporcion sale de aqui para que el
        # navegador no deforme la imagen.
        "camaras_reales": {
            "cam1": info_camaras[0].como_dict() if info_camaras[0] else None,
            "cam2": info_camaras[1].como_dict() if info_camaras[1] else None,
        },
        "perfil_web": PERFIL_WEB.como_dict(),
        "perfil_captura": PERFIL_CAPTURA.como_dict(),
        "reconocimiento": {
            "umbral": CONF_LIMIT,
            "etiquetas": len(etiquetas_lbph),
            "distancias": distancias_lbph_recientes(),
        },
        "red_objects": detected_red_objects,
        "largest_red_object": max(detected_red_objects, key=lambda x: x['area']) if detected_red_objects else None,
        #  Estos valores son ahora los que el driver tiene DE VERDAD, leidos
        #  con get(), en unidades del dispositivo. Antes eran los 0.5 fijos que
        #  el programa se inventaba y que no correspondian a nada de lo que la
        #  camara tenia puesto.
        "camera_settings": {
            "brightness": camera_optimizer.brightness,
            "contrast": camera_optimizer.contrast,
            "saturation": camera_optimizer.saturation,
            "sharpness": camera_optimizer.sharpness,
            "exposure": camera_optimizer.exposure
        },
        # Detalle para diagnosticar una imagen fea: que se pidio, que quedo y
        # que rechazo el driver.
        "controles_camara": camera_optimizer.estado(),
        #  Huella de la interfaz que sirve el robot AHORA. El JavaScript la
        #  compara con la suya: si no coinciden, el navegador esta enseñando
        #  una copia guardada en cache y lo dice en pantalla.
        "build_web": BUILD_WEB,
        #  Para que el boton de escuchar sepa si puede, y si no, por que.
        "audio": microfono.estado(),
        "voz": dict(altavoz.estado(), url_segura=url_para_hablar()),
        #  Estado real de la deteccion de rojo (se puede cambiar en caliente).
        "deteccion_rojo": DETECCION_ROJO,
        "system_info": {
            "ip_address": get_ip(),
            "port": 5000,
            "frame_width": FRAME_W,
            "frame_height": FRAME_H,
            "max_persons": MAX_PERSONS,
            "conf_limit": CONF_LIMIT,
            "cascade_loaded": cascade is not None,
            "recognizer_loaded": recognizer is not None,
            "active_camera": active_camera_index + 1
        },
        "cameras": {
            "camera1": {
                "active": camera1 is not None,
                "fps": fps1,
                "status": "Activa" if camera1 is not None else "Inactiva",
                "recording": recording_cam1,
                "index": CAMERA_INDICES[0] if len(CAMERA_INDICES) > 0 else None
            },
            "camera2": {
                "active": camera2 is not None,
                "fps": fps2,
                "status": "Activa" if camera2 is not None else "Inactiva",
                "recording": recording_cam2,
                "index": CAMERA_INDICES[1] if len(CAMERA_INDICES) > 1 else None
            }
        }
    })

@app.route("/api/esp32")
def api_esp32():
    """API optimizada para ESP32 con datos mínimos"""
    # Preparar datos para ESP32 (formato compacto)
    esp32_data = {
        "s": 1 if online else 0,  # system_status
        "d": detection_count,     # detections
        "fx": face_position["x"], # face x
        "fy": face_position["y"], # face y
        "r": 1 if recording else 0, # recording
        "r1": 1 if recording_cam1 else 0, # recording cam1
        "r2": 1 if recording_cam2 else 0, # recording cam2
        "ro": len(detected_red_objects), # red objects count
        "f1": fps1,              # fps cam1
        "f2": fps2,              # fps cam2
        "t": int(time.time())    # timestamp
    }
    
    # Si hay objetos rojos, agregar información del más grande
    if detected_red_objects:
        largest = max(detected_red_objects, key=lambda x: x['area'])
        esp32_data.update({
            "rx": largest['center_x'],
            "ry": largest['center_y'],
            "ra": largest['area']
        })
    
    return jsonify(esp32_data)

@app.route("/api/objects")
def api_objects():
    """API específica para datos de objetos detectados"""
    return jsonify({
        "timestamp": time.time(),
        "red_objects": detected_red_objects,
        "tracking_data": object_tracker.get_tracking_data(),
        "object_count": len(detected_red_objects)
    })

@app.route("/api/camera")
def api_camera():
    """API para estado y configuración de la cámara"""
    return jsonify({
        "online": online,
        "recording": recording,
        "recording_cam1": recording_cam1,
        "recording_cam2": recording_cam2,
        "fps1": fps1,
        "fps2": fps2,
        "settings": {
            "brightness": camera_optimizer.brightness,
            "contrast": camera_optimizer.contrast,
            "saturation": camera_optimizer.saturation,
            "sharpness": camera_optimizer.sharpness,
            "exposure": camera_optimizer.exposure
        },
        "frame_info": {
            "width": FRAME_W,
            "height": FRAME_H
        },
        "active_camera": active_camera_index + 1
    })

@app.route("/api/videos")
def api_videos():
    """API para listar videos grabados (ordenados por fecha)"""
    videos = get_video_files()
    return jsonify(videos)

@app.route("/play_video/<camera>/<filename>")
def play_video(camera, filename):
    """Reproduce un video grabado.

    SEGURIDAD: antes se concatenaba directamente lo que llegaba por la URL
    (os.path.join(VIDEO_PATH, camera, filename)). Con camera=".." se salia de
    videos/ y se podia leer cualquier fichero del proyecto — y este servidor
    escucha en 0.0.0.0, o sea que era alcanzable desde toda la red local.
    Ahora la ruta la valida medibot_vision.ruta_video_segura: lista blanca de
    camaras, nombre sin separadores ni '..', y comprobacion final de que la
    ruta REAL sigue dentro de VIDEO_PATH."""
    file_path = medibot_vision.ruta_video_segura(VIDEO_PATH, camera, filename)
    if file_path is None:
        return "Petición no válida", 400
    if not os.path.isfile(file_path):
        return "Video no encontrado", 404

    def generate():
        with open(file_path, 'rb') as f:
            while True:
                data = f.read(1024 * 1024)
                if not data:
                    break
                yield data

    return Response(generate(), mimetype='video/x-msvideo')

@app.route("/download_video/<camera>/<filename>")
def download_video(camera, filename):
    """Descarga un video grabado (misma validación que /play_video)."""
    file_path = medibot_vision.ruta_video_segura(VIDEO_PATH, camera, filename)
    if file_path is None:
        return "Petición no válida", 400
    if not os.path.isfile(file_path):
        return "Video no encontrado", 404
    return send_from_directory(os.path.realpath(os.path.join(VIDEO_PATH, camera)),
                               filename, as_attachment=True)

@app.route("/position")
def get_position():
    """Retorna la posición actual del rostro"""
    global face_position, last_face_time
    current_time = time.time()
    
    # Si no hay rostro detectado por más de 2 segundos, centrar
    if current_time - last_face_time > 2:
        face_position = {"x": "center", "y": "center"}
    
    return jsonify(face_position)

@app.route("/stats")
def get_stats():
    """Retorna estadísticas del sistema"""
    persons = get_registered_persons()
    return jsonify({
        "detections": detection_count,
        "status": system_status,
        "recording": recording,
        "recording_cam1": recording_cam1,
        "recording_cam2": recording_cam2,
        "persons": len(persons),
        "online": online,
        "fps1": fps1,
        "fps2": fps2,
        "tracking_enabled": object_tracker.tracking_enabled
    })

@app.route("/red_objects")
def get_red_objects():
    """Retorna la lista de objetos rojos detectados"""
    global detected_red_objects
    return jsonify({"objects": detected_red_objects})

@app.route("/toggle_recording", methods=["POST"])
def toggle_recording_endpoint():
    """Alterna la grabación de video en ambas cámaras"""
    global recording, recording_cam1, recording_cam2, system_status
    if not online:
        return jsonify({"error": "Sistema no activo"}), 400
    
    recording = not recording
    
    if recording:
        system_status = "Grabando en ambas cámaras"
    else:
        system_status = "Escaneando"
    
    return jsonify({
        "recording": recording, 
        "recording_cam1": recording_cam1,
        "recording_cam2": recording_cam2,
        "status": system_status, 
        "online": online,
        "message": "Grabación " + ("iniciada" if recording else "detenida") + " en ambas cámaras"
    })

@app.route("/toggle_system", methods=["POST"])
def toggle_system_endpoint():
    """Alterna el sistema completo"""
    global online, system_status, detection_count, face_position
    global recording, recording_cam1, recording_cam2, video_writer1, video_writer2, recognizer
    
    if not online:
        # Iniciar sistema
        recognizer = cargar_reconocedor()

        online = True
        face_position = {"x": "center", "y": "center"}
        detection_count = 0
        center_pwm()
        system_status = "Iniciando"
        
        # Iniciar procesamiento de ambas cámaras
        if start_camera_processing():
            response = {
                "online": online,
                "status": system_status,
                "message": "Sistema iniciado con ambas cámaras",
                "cameras_ready": True
            }
        else:
            online = False
            response = {
                "online": online,
                "status": "Error",
                "message": "Error al iniciar cámaras",
                "cameras_ready": False
            }
    else:
        # Detener sistema
        online = False
        system_status = "Deteniendo"
        
        # Detener grabación si está activa
        if recording:
            recording = False
            recording_cam1 = False
            recording_cam2 = False
            
            if video_writer1 is not None:
                video_writer1.release()
                video_writer1 = None
            if video_writer2 is not None:
                video_writer2.release()
                video_writer2 = None
        
        time.sleep(0.5)
        release_cameras()
        center_pwm()
        face_position = {"x": "center", "y": "center"}
        system_status = "Inactivo"
        
        response = {
            "online": online,
            "status": system_status,
            "message": "Sistema detenido",
            "cameras_ready": False
        }
    
    return jsonify(response)

@app.route("/toggle_tracking", methods=["POST"])
def toggle_tracking_endpoint():
    """Alterna el seguimiento de objetos"""
    object_tracker.tracking_enabled = not object_tracker.tracking_enabled
    return jsonify({
        "tracking_enabled": object_tracker.tracking_enabled,
        "message": f"Seguimiento {'activado' if object_tracker.tracking_enabled else 'desactivado'}"
    })

@app.route("/toggle_recognition", methods=["POST"])
def toggle_recognition_endpoint():
    """Activa/desactiva el reconocimiento de personas registradas"""
    global recognition_enabled, recognizer
    recognition_enabled = not recognition_enabled
    if recognition_enabled:
        if recognizer is None:
            recognizer = cargar_reconocedor()
        if recognizer is None:
            recognition_enabled = False
            return jsonify({"recognition_enabled": False,
                            "message": "No hay modelo entrenado (trainer.yml)"})
    return jsonify({
        "recognition_enabled": recognition_enabled,
        "umbral": CONF_LIMIT,
        "message": f"Reconocimiento {'activado' if recognition_enabled else 'desactivado'}"
    })

@app.route("/center_camera", methods=["POST"])
def center_camera_endpoint():
    """Centra la cámara"""
    center_pwm()
    return jsonify({"message": "Cámara centrada"})

@app.route("/optimize_camera", methods=["POST"])
def optimize_camera_endpoint():
    """Optimiza la cámara especificada"""
    data = request.get_json()
    camera_index = data.get('camera_index', 0)
    
    camara = camera1 if camera_index == 0 else (camera2 if camera_index == 1 else None)
    if camara is None:
        return jsonify({"error": "Cámara no disponible"}), 400

    #  Se devuelve lo que el driver ACEPTO, no un "optimizada" a ciegas. Sin
    #  controles pedidos no se toca nada, que es el comportamiento correcto por
    #  defecto: los valores de fabrica de la camara dan una imagen usable.
    informe = camera_optimizer.apply_settings(camara, indice=camera_index)
    if not informe:
        return jsonify({"message": "Sin ajustes que aplicar: se usan los "
                                   "valores por defecto de la cámara",
                        "controles": camera_optimizer.estado()})
    fallidos = {n: r["motivo"] for n, r in informe.items() if not r["ok"]}
    return jsonify({
        "message": (f"Cámara {camera_index + 1}: "
                    f"{len(informe) - len(fallidos)} de {len(informe)} controles aplicados"),
        "aplicados": {n: r["despues"] for n, r in informe.items() if r["ok"]},
        "fallidos": fallidos,
        "controles": camera_optimizer.estado(),
    })

@app.route("/auto_optimize_camera", methods=["POST"])
def auto_optimize_camera_endpoint():
    """Estado de los controles de imagen.

    ANTES decia "Optimización automática completada" sin hacer nada util: el
    auto-ajuste por software reescalaba el fotograma ya capturado a ciegas.
    Ahora la ruta se conserva (la API no se rompe) pero informa de la verdad:
    manda el driver, y lo que se toque se pide explicitamente."""
    return jsonify({
        "message": "El ajuste automático por software está desactivado: "
                   "manda la cámara. Usa /update_camera_setting o las "
                   "variables MEDIBOT_CAM_* para tocar un control concreto.",
        "brightness": camera_optimizer.brightness,
        "contrast": camera_optimizer.contrast,
        "saturation": camera_optimizer.saturation,
        "controles": camera_optimizer.estado(),
    })

@app.route("/update_area_threshold", methods=["POST"])
def update_area_threshold():
    """Actualiza el umbral de área para detección de objetos"""
    data = request.get_json()
    return jsonify({"message": f"Umbral actualizado a {data.get('threshold', 300)}"})

@app.route("/update_sensitivity", methods=["POST"])
def update_sensitivity():
    """Actualiza la sensibilidad del seguimiento"""
    data = request.get_json()
    return jsonify({"message": f"Sensibilidad actualizada a {data.get('sensitivity', 5)}"})

@app.route("/update_camera_setting", methods=["POST"])
def update_camera_setting():
    """Actualiza un ajuste de cámara específico"""
    data = request.get_json()
    setting = data.get('setting')
    value = data.get('value')
    
    if not setting or value is None:
        return jsonify({"error": "Datos inválidos"}), 400

    #  IMPORTANTE: el valor va en UNIDADES DEL DISPOSITIVO (en una C270,
    #  0..255), NO en 0..1. Mandar 0.5 aqui pone el control practicamente a
    #  cero: con la saturacion, eso deja la imagen en blanco y negro.
    try:
        camera_optimizer.manual_adjust(setting, value)
    except (ValueError, TypeError) as e:
        return jsonify({"error": str(e)}), 400

    informe = {}
    for indice, camara in ((0, camera1), (1, camera2)):
        if camara is not None:
            informe = camera_optimizer.apply_settings(camara, indice=indice)
    if not informe:
        return jsonify({"error": "No hay ninguna cámara abierta"}), 400

    resultado = informe.get(setting)
    if resultado and not resultado["ok"]:
        return jsonify({"error": f"La cámara no aceptó {setting}={value}: "
                                 f"{resultado['motivo']}",
                        "controles": camera_optimizer.estado()}), 400
    return jsonify({
        "message": f"Ajuste {setting} = {resultado['despues'] if resultado else value}",
        "controles": camera_optimizer.estado(),
    })

@app.route("/clear_tracking", methods=["POST"])
def clear_tracking():
    """Limpia el historial de seguimiento"""
    object_tracker.object_history.clear()
    return jsonify({"message": "Historial de seguimiento limpiado"})

@app.route("/switch_camera", methods=["POST"])
def switch_camera_endpoint():
    """Cambia la cámara activa para visualización"""
    global active_camera_index
    if camera1 is not None and camera2 is not None:
        active_camera_index = (active_camera_index + 1) % 2
        return jsonify({
            "message": f"Cambiando a cámara {active_camera_index + 1}",
            "active_camera": active_camera_index + 1
        })
    return jsonify({"error": "Solo una cámara disponible"}), 400

@app.route("/move", methods=["POST"])
def move_endpoint():
    """Movimiento del robot desde la web.

    Admite las dos formas, para no romper nada que ya funcionara:
      {"directions": ["w","a"]}   joystick por teclas (como siempre)
      {"movimiento": "SPINL"}     movimiento con nombre (incluye los giros)
    """
    data = request.get_json(silent=True) or {}

    mov = str(data.get("movimiento", "")).strip().upper()
    if mov:
        if not enviar_movimiento(mov):
            return jsonify({"ok": False,
                            "message": f"Movimiento desconocido: {mov}",
                            "validos": list(MOVIMIENTOS)}), 400
        # Mantener coherente el estado del joystick de teclas
        for _d in movement_state:
            movement_state[_d] = (TECLAS_MOVIMIENTO.get(_d) == mov)
        return jsonify({"ok": True, "movimiento": mov, "state": movement_state})

    directions = [d for d in data.get("directions", []) if d in TECLAS_MOVIMIENTO]
    set_movement(directions)
    return jsonify({"ok": True, "directions": sorted(directions),
                    "movimiento": movimiento_actual, "state": movement_state})


@app.route("/stop_movement", methods=["POST"])
def stop_movement_endpoint():
    """Detiene todo el movimiento"""
    detener_movimiento()
    return jsonify({"ok": True, "message": "Movimiento detenido",
                    "movimiento": movimiento_actual, "state": movement_state})


@app.route("/movimientos")
def movimientos_endpoint():
    """Lista los movimientos disponibles y su etiqueta, para que la interfaz
    web se construya a partir de esta lista y no de nombres escritos a mano."""
    return jsonify({"movimientos": [{"id": m, "etiqueta": ETIQUETAS_MOVIMIENTO[m]}
                                    for m in MOVIMIENTOS],
                    "actual": movimiento_actual})


@app.route("/velocidad", methods=["GET", "POST"])
def velocidad_endpoint():
    """Consulta o ajusta la velocidad del chasis (200..255)."""
    if request.method == "GET":
        return jsonify({"velocidad": velocidad_chasis, "min": VEL_MIN, "max": VEL_MAX})
    data = request.get_json(silent=True) or {}
    aplicada = fijar_velocidad(data.get("velocidad"))
    if aplicada is None:
        return jsonify({"ok": False,
                        "message": f"Velocidad invalida (usa {VEL_MIN}..{VEL_MAX})"}), 400
    return jsonify({"ok": True, "velocidad": aplicada,
                    "min": VEL_MIN, "max": VEL_MAX})


@app.route("/truco", methods=["POST"])
def truco_endpoint():
    """Lanza uno de los cuatro movimientos especiales (1..4). Son exactamente
    los mismos que los botones Triangulo/Circulo/Cuadrado/X del mando PS2."""
    data = request.get_json(silent=True) or {}
    numero = data.get("truco", 0)
    if not lanzar_truco(numero):
        return jsonify({"ok": False, "message": "Truco invalido (usa 1..4)"}), 400
    return jsonify({"ok": True, "truco": int(numero)})


# ================= GESTIÓN DE PERSONAS ===========
def add_person():
    """Agregar una nueva persona al sistema"""
    persons = get_registered_persons()
    
    if len(persons) >= MAX_PERSONS:
        messagebox.showwarning("Límite Alcanzado", 
                              f"Ya hay {MAX_PERSONS} personas registradas.\n"
                              f"Elimina una persona antes de agregar otra.")
        return
    
    name = simpledialog.askstring("Nueva Persona", 
                                  "Ingrese el nombre de la persona:")
    
    if not name:
        return
    
    # Validar nombre
    name = name.strip().replace(" ", "_")
    
    if not os.path.exists(DATA_PATH):
        os.makedirs(DATA_PATH)
    
    person_path = os.path.join(DATA_PATH, name)
    
    if os.path.exists(person_path):
        messagebox.showerror("Error", "Ya existe una persona con ese nombre.")
        return
    
    os.makedirs(person_path)
    start_capture(name)
    update_person_list()

def start_capture(person_name):
    """Inicia la captura de imágenes para una persona"""
    global capture_mode, current_capture_name, captured_images
    
    capture_mode = True
    current_capture_name = person_name
    captured_images = 0
    
    #  Se mantiene el aviso (si no, la captura arrancaría sin ninguna señal),
    #  pero sin el paso a paso de "posicione su rostro / mueva la cabeza".
    messagebox.showinfo("Captura Iniciada",
                       f"Capturando {IMAGES_PER_PERSON} imágenes de {person_name}.")

def delete_person():
    """Eliminar una persona del sistema"""
    persons = get_registered_persons()
    
    if not persons:
        messagebox.showinfo("Sin Personas", "No hay personas registradas.")
        return
    
    # Crear ventana de selección
    selection_window = tk.Toplevel(root)
    selection_window.title("Eliminar Persona")
    selection_window.geometry("400x300")
    selection_window.configure(bg="#000000")
    selection_window.transient(root)
    selection_window.grab_set()
    
    tk.Label(selection_window, 
             text="Seleccione la persona a eliminar:",
             font=("Arial", 12, "bold"),
             bg="#000000",
             fg="#00ffff").pack(pady=10)
    
    listbox = tk.Listbox(selection_window, 
                         font=("Arial", 10),
                         bg="#111111",
                         fg="#ffffff",
                         selectmode=tk.SINGLE,
                         height=8)
    listbox.pack(pady=10, padx=20, fill=tk.BOTH, expand=True)
    
    for person in persons:
        listbox.insert(tk.END, f"{person['name']} ({person['images']} imágenes)")
    
    def confirm_delete():
        selection = listbox.curselection()
        if not selection:
            messagebox.showwarning("Sin Selección", "Seleccione una persona.")
            return
        
        person_name = persons[selection[0]]["name"]
        
        if messagebox.askyesno("Confirmar", 
                              f"¿Está seguro de eliminar a '{person_name}'?\n"
                              f"Esta acción no se puede deshacer."):
            person_path = os.path.join(DATA_PATH, person_name)
            shutil.rmtree(person_path)
            messagebox.showinfo("Eliminado", f"'{person_name}' ha sido eliminado.")
            selection_window.destroy()
            update_person_list()
    
    ttk.Button(selection_window, 
               text="Eliminar Seleccionado",
               command=confirm_delete).pack(pady=10)

def train_system():
    """Entrena el sistema con las personas registradas"""
    persons = get_registered_persons()
    
    if not persons:
        messagebox.showwarning("Sin Datos", 
                              "No hay personas registradas para entrenar.")
        return
    
    # Verificar que todas tengan suficientes imágenes
    incomplete = []
    for person in persons:
        if person["images"] < IMAGES_PER_PERSON:
            incomplete.append(person['name'])
    
    if incomplete:
        messagebox.showwarning("Datos Incompletos",
                              f"Las siguientes personas no tienen suficientes imágenes:\n" +
                              "\n".join([f"- {name}" for name in incomplete]) +
                              f"\n\nSe requieren {IMAGES_PER_PERSON} imágenes por persona.")
        return
    
    # Ventana de progreso
    progress_window = tk.Toplevel(root)
    progress_window.title("Entrenando Sistema")
    progress_window.geometry("400x150")
    progress_window.configure(bg="#000000")
    progress_window.transient(root)
    progress_window.grab_set()
    
    tk.Label(progress_window,
             text="Entrenando el sistema...",
             font=("Arial", 12, "bold"),
             bg="#000000",
             fg="#00ffff").pack(pady=20)
    
    progress_label = tk.Label(progress_window,
                             text="Procesando...",
                             font=("Arial", 10),
                             bg="#000000",
                             fg="#888888")
    progress_label.pack()
    
    def train_thread():
        try:
            faces = []
            labels = []
            #  Nombres EN EL ORDEN CON EL QUE SE ETIQUETA. Es justo lo que hay
            #  que guardar: el modelo aprende "id 0, id 1..." y sin este mapa
            #  la unica forma de traducirlo era volver a listar el disco, cuyo
            #  orden cambia al dar de alta o borrar a alguien.
            nombres = []

            for idx, person in enumerate(persons):
                progress_label.config(text=f"Procesando: {person['name']}")
                person_path = os.path.join(DATA_PATH, person['name'])
                nombres.append(person['name'])

                for image_name in os.listdir(person_path):
                    if image_name.endswith('.jpg'):
                        image_path = os.path.join(person_path, image_name)
                        image = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
                        if image is not None:
                            faces.append(image)
                            labels.append(idx)

            if not faces:
                progress_window.destroy()
                messagebox.showerror("Error", "No se encontraron imágenes válidas para entrenar.")
                return

            progress_label.config(text="Generando modelo...")

            recognizer_temp = cv2.face.LBPHFaceRecognizer_create()
            recognizer_temp.train(faces, np.array(labels))
            recognizer_temp.save("trainer.yml")
            medibot_vision.guardar_mapa_etiquetas(nombres)
            cargar_etiquetas_lbph()

            progress_window.destroy()
            #  El instructivo de calibración se movió al README (sección
            #  "Umbral del reconocimiento facial"); aquí solo el resultado.
            messagebox.showinfo("Entrenamiento Completo",
                               f"Entrenado con {len(persons)} personas.\n"
                               f"Umbral de reconocimiento: {CONF_LIMIT:.0f}")

        except Exception as e:
            progress_window.destroy()
            messagebox.showerror("Error", f"Error durante el entrenamiento:\n{str(e)}")
    
    threading.Thread(target=train_thread, daemon=True).start()

def update_person_list():
    """Actualiza la lista de personas en la interfaz"""
    persons = get_registered_persons()
    
    person_list.config(state=tk.NORMAL)
    person_list.delete(1.0, tk.END)
    
    if not persons:
        person_list.insert(tk.END, "No hay personas registradas.\n")
        person_list.insert(tk.END, "Agregue una nueva persona para comenzar.")
    else:
        person_list.insert(tk.END, f"Personas Registradas ({len(persons)}/{MAX_PERSONS}):\n\n")
        
        for idx, person in enumerate(persons, 1):
            status = "✓" if person["images"] >= IMAGES_PER_PERSON else "✗"
            color = tc('ok') if person["images"] >= IMAGES_PER_PERSON else tc('danger')
            person_list.insert(tk.END, 
                             f"{status} {idx}. {person['name']}\n", f"person_{idx}")
            person_list.insert(tk.END, 
                             f"   Imágenes: {person['images']}/{IMAGES_PER_PERSON}\n\n")
            
            # Configurar color para el estado
            person_list.tag_config(f"person_{idx}", foreground=color)
    
    person_list.config(state=tk.DISABLED)

# ================= PASTILLERO (Pillbox) =========
PASTILLERO_PORT = 5001   # puerto del servidor web de Pastillero.py


def open_pastillero():
    """Abre la interfaz web del pastillero.

    Antes apuntaba a 'http://192.168.3.208' (IP fija de otra red y sin puerto):
    en cualquier red distinta el botón no llevaba a ninguna parte. Ahora se usa
    localhost, que es el propio equipo donde corre Pillbox."""
    url = f"http://127.0.0.1:{PASTILLERO_PORT}"
    try:
        webbrowser.open(url)
        messagebox.showinfo("Pillbox",
            f"Abriendo Pillbox en este equipo:\n{url}\n\n"
            "Desde el móvil u otro PC de la misma red, entra con:\n"
            + medibot_red.texto_urls(PASTILLERO_PORT))
    except Exception as e:
        messagebox.showerror("Error", f"No se pudo abrir Pillbox:\n{str(e)}")

# ========= LANZAR Pastillero.py + PANTALLA DIVIDIDA =========
# Ejecuta el script local Pastillero.py (levanta su servidor Flask en el
# puerto 5001) y divide la pantalla: Visión a la izquierda, pastillero a la
# derecha, para operar ambos a la vez.
_pastillero_proc = None

def _puerto_abierto(host, port, timeout=0.5):
    """True si hay algo escuchando en host:port."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False

# ========= SERVIDOR WEB DE VISION (puerto 5000) =========
# El servidor arranca UNA sola vez y es independiente de las camaras y del
# boton INICIAR. Antes se lanzaba dentro de toggle_system() y SOLO si las
# camaras iniciaban bien: si una camara fallaba, la web no existia ("IP
# valida pero el navegador no responde"); y cada INICIAR relanzaba otro
# app.run sobre el mismo puerto, cuyo hilo moria en silencio con
# "Address already in use".
def _entero_entorno(nombre, por_defecto):
    try:
        return int(os.environ.get(nombre, "").strip() or por_defecto)
    except ValueError:
        return por_defecto


VISION_WEB_HOST = "0.0.0.0"   # todas las interfaces (LAN incluida)
VISION_WEB_PORT = 5000

#  Ademas del HTTP de siempre, la misma web por HTTPS con un certificado que
#  se genera la propia Pi (medibot_tls.py). Es lo unico que hace falta para
#  poder HABLAR por el altavoz sin depender del tunel de Cloudflare ni de
#  ningun otro servicio de fuera: los navegadores solo dan el microfono en
#  contexto seguro.
#
#  Se anade en OTRO puerto en vez de cambiar el de siempre a HTTPS porque
#  asi no se rompe nada de lo que ya funciona: los enlaces guardados, el
#  tunel (que apunta al 5000) y el video siguen igual.
VISION_WEB_PORT_SEGURO = _entero_entorno("MEDIBOT_PUERTO_HTTPS", 5443)
_hilo_servidor_web = None
_hilo_servidor_seguro = None
_https_activo = False

def url_para_hablar():
    """La direccion por la que SI se puede hablar, para poder decirsela.

    Vacia si no hay HTTPS: entonces el unico sitio donde funciona el
    microfono es la propia Pi (localhost), y eso ya lo dice la pagina."""
    if not _https_activo:
        return ""
    ip = medibot_red.ip_lan_principal()
    return f"https://{ip}:{VISION_WEB_PORT_SEGURO}/" if ip else ""


def iniciar_servidor_seguro():
    """Arranca la misma web por HTTPS con el certificado propio de la Pi.

    Si no se puede (falta openssl, o no hay manera de generar el
    certificado), se dice y YA ESTA: el servidor HTTP de siempre no se toca.
    Quedarse sin HTTPS significa no poder hablar, no quedarse sin web."""
    global _hilo_servidor_seguro, _https_activo
    if _hilo_servidor_seguro is not None and _hilo_servidor_seguro.is_alive():
        return True

    ips = [ip for _, ip in medibot_red.listar_ips_lan()]
    rutas, motivo = medibot_tls.asegurar(ips)
    if rutas is None:
        print(f"AVISO: sin HTTPS, asi que no se podra hablar por el altavoz "
              f"desde otro equipo ({motivo}).")
        return False
    if motivo:
        print(f"Certificado de la Pi rehecho ({motivo}).")

    def _correr():
        try:
            app.run(host=VISION_WEB_HOST, port=VISION_WEB_PORT_SEGURO,
                    debug=False, use_reloader=False, threaded=True,
                    ssl_context=rutas)
        except OSError as e:
            print(f"Vision web (HTTPS): no se pudo abrir el puerto "
                  f"{VISION_WEB_PORT_SEGURO}: {e}")

    _hilo_servidor_seguro = threading.Thread(target=_correr, daemon=True)
    _hilo_servidor_seguro.start()
    for _ in range(25):
        if _puerto_abierto("127.0.0.1", VISION_WEB_PORT_SEGURO):
            _https_activo = True
            print(f"Vision web (HTTPS) en el puerto {VISION_WEB_PORT_SEGURO}. "
                  "Para HABLAR, entra por aqui:")
            for ip in ips:
                print(f"  https://{ip}:{VISION_WEB_PORT_SEGURO}")
            print("  (la primera vez el navegador avisa del certificado: es "
                  "normal, lo firma la propia Pi. Acepta y continua.)")
            return True
        time.sleep(0.2)
    print(f"AVISO: el HTTPS no respondio en el puerto {VISION_WEB_PORT_SEGURO}.")
    return False


def _avisar_del_microfono():
    """Una linea diciendo si se podra escuchar el microfono de la camara."""
    estado = microfono.estado()
    if estado["disponible"]:
        print(f"Microfono de la camara: {estado['dispositivo']} "
              f"({estado['hz']} Hz, {estado['canales']} canal/es). "
              "Pulsa el altavoz en la web para escuchar.")
    else:
        print(f"Microfono de la camara: no se podra escuchar ({estado['motivo']}).")

    voz = altavoz.estado()
    if voz["disponible"]:
        print(f"Altavoz para hablar: {voz['dispositivo']}. "
              "Manten pulsado el microfono en la web para hablar "
              "(necesita HTTPS: usa el tunel o localhost).")
    else:
        print(f"Altavoz para hablar: no se podra hablar ({voz['motivo']}).")

def iniciar_servidor_web():
    """Arranca el servidor web de Vision (idempotente: llamadas repetidas no
    duplican nada). Devuelve True si el servidor esta (o queda) disponible."""
    global _hilo_servidor_web
    # Ya lo arrancamos nosotros y sigue vivo -> nada que hacer
    if _hilo_servidor_web is not None and _hilo_servidor_web.is_alive():
        return True

    def _correr_servidor():
        try:
            app.run(host=VISION_WEB_HOST, port=VISION_WEB_PORT,
                    debug=False, use_reloader=False, threaded=True)
        except OSError as e:
            # Puerto ocupado u otro fallo de bind: decirlo SIEMPRE en consola
            # (antes el hilo moria callado y nadie se enteraba).
            print(f"Vision web: no se pudo abrir el puerto {VISION_WEB_PORT}: {e}")

    _hilo_servidor_web = threading.Thread(target=_correr_servidor, daemon=True)
    _hilo_servidor_web.start()
    # Confirmar que quedo ESCUCHANDO de verdad (no asumirlo): hasta ~5 s
    for _ in range(25):
        if _puerto_abierto("127.0.0.1", VISION_WEB_PORT):
            print(f"Vision web escuchando en {VISION_WEB_HOST}:{VISION_WEB_PORT} "
                  "(todas las interfaces).")
            print("Entra desde el movil u otro PC de la misma red:")
            print(medibot_red.texto_urls(VISION_WEB_PORT))
            # Comprobar que se llega POR LA IP DE RED, no solo por localhost:
            # es la diferencia entre "me abre en la Pi" y "me abre en el movil".
            ok, detalle = medibot_red.accesible_en_lan(VISION_WEB_PORT)
            if not ok:
                print(f"AVISO: no accesible desde otros equipos: {detalle}.")
            #  El audio viene ACTIVADO, asi que se dice aqui si el microfono
            #  esta de verdad. Sin esto, que faltara alsa-utils o el micro no
            #  se descubriria hasta pulsar Escuchar en la web y no oir nada.
            #  El HTTPS va despues: si falla, el HTTP ya esta en marcha y
            #  la web sigue viendose igual.
            iniciar_servidor_seguro()
            _avisar_del_microfono()
            return True
        time.sleep(0.2)
    print(f"AVISO: Vision web no responde aun en el puerto {VISION_WEB_PORT}.")
    return False

def _lanzar_pastillero_proceso():
    """Lanza Pastillero.py con el mismo interprete de Python.
    No lo duplica si ya hay un servidor escuchando en el puerto."""
    global _pastillero_proc
    # ¿Ya hay un servidor en el puerto? -> reutilizarlo
    if _puerto_abierto("127.0.0.1", PASTILLERO_PORT):
        return True
    # ¿Ya lo lanzamos y sigue vivo?
    if _pastillero_proc is not None and _pastillero_proc.poll() is None:
        return True
    base_dir = os.path.dirname(os.path.abspath(__file__))
    script = os.path.join(base_dir, "Pastillero.py")
    if not os.path.exists(script):
        messagebox.showerror("Pillbox",
            f"No se encontró Pastillero.py en:\n{base_dir}")
        return False
    try:
        _pastillero_proc = subprocess.Popen([sys.executable, script], cwd=base_dir)
    except Exception as e:
        messagebox.showerror("Pillbox", f"No se pudo iniciar Pastillero.py:\n{e}")
        return False
    # Esperar a que Flask levante (hasta ~8 s)
    for _ in range(40):
        if _puerto_abierto("127.0.0.1", PASTILLERO_PORT):
            return True
        time.sleep(0.2)
    messagebox.showwarning("Pillbox",
        "Pastillero.py se inició, pero el servidor web (puerto 5001) aún no "
        "responde. Reintenta el botón en unos segundos.")
    return False

def _abrir_navegador_pastillero(x, y, w, h):
    """Abre la UI del pastillero. Intenta Chromium en una ventana ya
    posicionada (para la pantalla dividida); si no, usa el navegador
    por defecto."""
    url = f"http://127.0.0.1:{PASTILLERO_PORT}"
    # Perfil de navegador DEDICADO para la ventana de Pillbox: si se usa el
    # perfil por defecto y ya hay otro Chromium abierto, ambos pelean por el
    # mismo perfil y aparece "Profile error occurred".
    perfil = os.path.expanduser("~/.pillbox-webapp-profile")
    for navegador in ("chromium-browser", "chromium", "google-chrome", "google-chrome-stable"):
        ruta = shutil.which(navegador)
        if ruta:
            try:
                subprocess.Popen([ruta,
                    f"--app={url}",
                    f"--user-data-dir={perfil}",
                    "--no-first-run",
                    "--no-default-browser-check",
                    f"--window-position={x},{y}",
                    f"--window-size={w},{h}",
                    "--new-window"])
                return
            except Exception:
                pass
    # Respaldo: navegador por defecto (no controla la posición de la ventana)
    webbrowser.open(url)

def abrir_pastillero_dividido():
    """Lanza Pastillero.py y divide la pantalla: Visión a la izquierda,
    pastillero (web) a la derecha."""
    if not _lanzar_pastillero_proceso():
        return
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    half = sw // 2
    # Visión ocupa la mitad izquierda
    root.geometry(f"{half}x{sh}+0+0")
    # Pastillero en la mitad derecha
    _abrir_navegador_pastillero(half, 0, sw - half, sh)
    #  Sin el texto explicativo de "izquierda/derecha" ni la nota sobre el hub
    #  serial: se ve en pantalla al momento y el hub es un detalle interno.

# ================= CONTROL PRINCIPAL ===================
def toggle_system():
    """Alterna entre iniciar y detener el sistema"""
    global recognizer, online, face_position, system_status
    global detection_count, recording, recording_cam1, recording_cam2
    global video_writer1, video_writer2
    
    if not online:
        # Iniciar sistema: la cámara arranca SIN reconocer caras.
        # Se precarga el modelo (si existe) por si luego activas el reconocimiento.
        recognizer = cargar_reconocedor()
        if recognizer is not None:
            print("Modelo de reconocimiento facial precargado (reconocimiento desactivado)")

        online = True
        face_position = {"x": "center", "y": "center"}
        detection_count = 0
        center_pwm()
        status_label.config(text="Estado: ONLINE", foreground=tc('ok'))
        toggle_btn.config(text="DETENER")
        system_status = "Iniciando"
        
        # El servidor web ya corre desde el arranque del programa
        # (iniciar_servidor_web es idempotente: esto solo lo re-asegura,
        # nunca lo duplica).
        iniciar_servidor_web()

        # Iniciar procesamiento de ambas cámaras
        if start_camera_processing():
            #  Solo el estado, sin el instructivo de "accede desde el movil":
            #  las direcciones ya están permanentemente en la pestaña Gestión.
            messagebox.showinfo("Sistema Activo",
                f"Cámara 1: {'ACTIVA' if camera1 is not None else 'INACTIVA'}\n"
                f"Cámara 2: {'ACTIVA' if camera2 is not None else 'INACTIVA'}\n"
                f"Reconocimiento facial: {'ACTIVADO' if recognizer else 'DESACTIVADO'}")
        else:
            online = False
            # Sin cámaras NO se pierde la web: el servidor sigue arriba y
            # muestra la interfaz (con los streams en "no disponible").
            messagebox.showerror("Error",
                "No se pudieron inicializar las cámaras.\n\n"
                f"La interfaz web sigue disponible en "
                f"http://{get_ip()}:{VISION_WEB_PORT}")
        
    else:
        # Detener sistema
        online = False
        system_status = "Deteniendo"
        
        # Detener grabación si está activa
        if recording:
            recording = False
            recording_cam1 = False
            recording_cam2 = False
            
            if video_writer1 is not None:
                video_writer1.release()
                video_writer1 = None
            if video_writer2 is not None:
                video_writer2.release()
                video_writer2 = None
        
        # Esperar a que los hilos terminen
        time.sleep(0.5)
        
        release_cameras()
        center_pwm()
        face_position = {"x": "center", "y": "center"}
        status_label.config(text="Estado: INACTIVO", foreground=tc('danger'))
        toggle_btn.config(text="INICIAR")
        system_status = "Inactivo"
        
        messagebox.showinfo("Sistema", "Sistema detenido correctamente")

def toggle_recording():
    """Alterna la grabación de video en ambas cámaras"""
    global recording
    if not online:
        messagebox.showwarning("Sistema Inactivo", "El sistema debe estar activo para grabar.")
        return
    
    recording = not recording
    
    if recording:
        messagebox.showinfo("Grabación", "Grabación iniciada en AMBAS cámaras simultáneamente")
    else:
        messagebox.showinfo("Grabación", "Grabación detenida en ambas cámaras")

def _update_recognition_btn():
    """Refresca el texto del botón de reconocimiento"""
    try:
        if recognition_enabled:
            recognition_btn.config(text="RECONOCIMIENTO: ACTIVADO", style="Accent.TButton")
        else:
            recognition_btn.config(text="RECONOCIMIENTO: DESACTIVADO", style="TButton")
    except Exception:
        pass

def toggle_recognition():
    """Activa/desactiva el reconocimiento de personas registradas"""
    global recognition_enabled, recognizer
    recognition_enabled = not recognition_enabled

    if recognition_enabled:
        # Cargar el modelo si aún no está cargado
        if recognizer is None:
            recognizer = cargar_reconocedor()
        if recognizer is None:
            recognition_enabled = False
            messagebox.showwarning("Reconocimiento",
                                   "No hay un modelo entrenado (trainer.yml).\n"
                                   "Registra personas y entrena el sistema primero.")
    _update_recognition_btn()

# ================= INTERFAZ GRÁFICA CORREGIDA ==============
# Secuencia del ultimo fotograma YA dibujado en cada panel. Sirve para no
# repetir trabajo: si la camara no ha entregado nada nuevo, no hay nada que
# redibujar. Antes update_gui() rehacia resize + cvtColor + PIL.Image +
# PhotoImage 20 veces por segundo POR CAMARA aunque el fotograma fuera el
# mismo, y todo eso ocurre en el hilo principal de Tk: es exactamente lo que
# hacia que los botones y las pestanas respondieran con retraso.
_ultima_seq_gui = {0: -1, 1: -1}
_ultima_seq_fs = -1
_camara_fs = None      # que camara se esta volcando a pantalla completa

# Cada cuanto repasa la interfaz (ms). 50 ms es el valor de siempre.
GUI_REFRESCO_MS = int(os.environ.get("MEDIBOT_GUI_MS", "50"))

# Estado ya reflejado en los textos de la interfaz. Reconfigurar un widget de
# Tk fuerza un redibujado; hacerlo 20 veces por segundo con el MISMO texto es
# trabajo puro para nada. Solo se toca el widget cuando el valor cambia.
_estado_ui = {}


def _set_widget(clave, widget, **kwargs):
    """Aplica kwargs al widget solo si algo cambio respecto a la ultima vez."""
    if _estado_ui.get(clave) == kwargs:
        return
    _estado_ui[clave] = dict(kwargs)
    try:
        widget.config(**kwargs)
    except Exception:
        pass


def _pintar_panel(indice, label, seq_vista):
    """Vuelca el ultimo fotograma de una camara en su label de Tk.
    Devuelve la nueva secuencia dibujada (o la misma si no habia nada nuevo).

    NOTA SOBRE EL COSTE: ahora el buzon guarda el fotograma a resolucion
    completa, asi que este resize a VIEW_W x VIEW_H si se ejecuta (antes el
    frame ya llegaba reducido). El trabajo no es nuevo, se ha MOVIDO del hilo
    de captura al de Tk: ~0,28 ms por fotograma y camara (medido a 640->400),
    y como la interfaz solo repinta cuando hay fotograma nuevo y como mucho
    cada GUI_REFRESCO_MS, son ~11 ms de CPU por segundo con dos camaras. Ese
    es el precio de que la web reciba la imagen nitida; si en la Raspberry
    resultara caro, se reduce con MEDIBOT_GUI_MS (menos repintados)."""
    frame, seq = frame_hub.obtener_si_nuevo(indice, seq_vista)
    if frame is None:
        return seq_vista
    try:
        if frame.shape[1] != VIEW_W or frame.shape[0] != VIEW_H:
            frame = cv2.resize(frame, (VIEW_W, VIEW_H))
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        imgtk = ImageTk.PhotoImage(image=Image.fromarray(rgb))
        label.imgtk = imgtk          # referencia viva: si no, Tk la recolecta
        label.configure(image=imgtk)
    except Exception as e:
        print(f"Error actualizando GUI cámara {indice + 1}: {e}")
    return seq


def update_gui():
    """Actualiza la interfaz gráfica (solo lo que ha cambiado)."""
    global _cam2_visible, fs_geom, _ultima_seq_fs, _camara_fs

    # ---- Video: solo si hay fotograma nuevo ----
    _ultima_seq_gui[0] = _pintar_panel(0, video_label1, _ultima_seq_gui[0])
    _ultima_seq_gui[1] = _pintar_panel(1, video_label2, _ultima_seq_gui[1])

    # ---- Textos y estados: solo si cambiaron ----
    if recording:
        _set_widget('record_btn', record_btn,
                    text="DETENER GRABACIÓN AMBAS", style="Accent.TButton")
    else:
        _set_widget('record_btn', record_btn,
                    text="INICIAR GRABACIÓN AMBAS", style="TButton")

    if online:
        _set_widget('status', status_label,
                    text=f"Estado: ONLINE (Cámara Activa: {active_camera_index + 1})",
                    foreground=tc('ok'))
        _set_widget('toggle', toggle_btn, text="DETENER")
        _set_widget('fps', fps_label,
                    text=f"FPS Cámara 1: {fps1} | FPS Cámara 2: {fps2}")
    else:
        _set_widget('status', status_label, text="Estado: INACTIVO",
                    foreground=tc('danger'))
        _set_widget('toggle', toggle_btn, text="INICIAR")
        _set_widget('fps', fps_label, text="FPS Cámara 1: 0 | FPS Cámara 2: 0")

    _set_widget('cam_status', camera_status_label,
                text=f"Cámara 1: {'OK' if camera1 is not None else '--'} | "
                     f"Cámara 2: {'OK' if camera2 is not None else '--'}")

    # Mostrar solo la(s) cámara(s) activa(s): si la cámara 2 no se detecta,
    # ocultar su ventana (esto ya solo actua cuando cambia de estado).
    if camera2 is not None and not _cam2_visible:
        cam2_frame.pack(side=tk.RIGHT, padx=5)
        _cam2_visible = True
    elif camera2 is None and _cam2_visible:
        cam2_frame.pack_forget()
        _cam2_visible = False

    if recording_cam1:
        _set_widget('rec1', recording_label1, text="Cámara 1: GRABANDO",
                    foreground=tc('danger'))
    else:
        _set_widget('rec1', recording_label1, text="Cámara 1: Lista",
                    foreground=tc('ok'))

    if recording_cam2:
        _set_widget('rec2', recording_label2, text="Cámara 2: GRABANDO",
                    foreground=tc('danger'))
    else:
        _set_widget('rec2', recording_label2, text="Cámara 2: Lista",
                    foreground=tc('ok'))

    # ---- Pantalla completa (con joystick translúcido) ----
    # Tambien gobernada por la secuencia: reescalar a pantalla completa es la
    # operacion mas cara de la interfaz y antes se hacia en CADA pasada.
    if fs_win is not None and fs_label is not None:
        try:
            # Camara a mostrar: la activa; si aun no ha entregado nada, la 1.
            indice = active_camera_index
            if frame_hub.secuencia(indice) == 0:
                indice = 0
            # Al cambiar de camara hay que redibujar aunque el numero coincida
            # (son contadores distintos), asi que se reinicia la referencia.
            if indice != _camara_fs:
                _camara_fs = indice
                _ultima_seq_fs = -1
            src, seq = frame_hub.obtener_si_nuevo(indice, _ultima_seq_fs)
            sw, sh = fs_win.winfo_width(), fs_win.winfo_height()
            if src is not None and sw > 10 and sh > 10:
                _ultima_seq_fs = seq
                big = cv2.resize(src, (sw, sh))
                fs_geom = draw_joystick_overlay(big)
                rgb = cv2.cvtColor(big, cv2.COLOR_BGR2RGB)
                imtk = ImageTk.PhotoImage(image=Image.fromarray(rgb))
                fs_label.imgtk = imtk
                fs_label.configure(image=imtk)
        except Exception:
            pass

    # Programar siguiente actualización.
    #  Se mantienen los 50 ms de SIEMPRE. Se probo a bajarlo a 33 ms (mas
    #  fluido, y cada pasada ahora es barata), pero fue otro cambio hecho sin
    #  medir en la Raspberry: mas pasadas = mas veces creando la imagen de Tk
    #  en el hilo principal, que es justo lo que compite con la captura.
    #  Ajustable sin tocar codigo:  MEDIBOT_GUI_MS=33 python3 medibot/main.py
    root.after(GUI_REFRESCO_MS, update_gui)

def on_closing():
    """Maneja el cierre de la aplicación"""
    global online
    
    online = False
    
    # Liberar todos los recursos
    if recording:
        if video_writer1 is not None:
            video_writer1.release()
        if video_writer2 is not None:
            video_writer2.release()
    
    release_cameras()
    
    # Esperar un momento para que se liberen los recursos
    time.sleep(0.5)
    
    center_pwm()
    GPIO.cleanup()
    
    root.destroy()

def show_management_tab():
    """Muestra la pestaña de gestión"""
    notebook.select(management_tab)

def show_monitoring_tab():
    """Muestra la pestaña de monitoreo"""
    notebook.select(monitoring_tab)

# ================= CONFIGURACIÓN GUI CORREGIDA =============
root = tk.Tk()
root.title("Medibot")
root.geometry("1200x800")
root.resizable(True, True)
root.configure(bg="#000000")
root.protocol("WM_DELETE_WINDOW", on_closing)

# Configurar para pantalla completa o maximizada
try:
    root.attributes('-zoomed', True)  # Maximizar en Windows/Linux
except:
    try:
        root.state('zoomed')  # Alternativa para algunos sistemas
    except:
        pass

# Estilo responsivo
style = ttk.Style()
style.theme_use('clam')

# ================= SISTEMA DE TEMAS (CLARO / OSCURO) =========
# Paletas de color para la app de escritorio MEDIBOT
PALETTE = {
    "dark": {
        "bg": "#000000", "panel": "#111111", "panel2": "#222222", "panel3": "#333333",
        "text": "#ffffff", "muted": "#888888", "accent": "#00ffff",
        "ok": "#00ff00", "danger": "#ff0000",
    },
    "light": {
        "bg": "#eef1f5", "panel": "#ffffff", "panel2": "#e9eef3", "panel3": "#dde3ea",
        "text": "#15202b", "muted": "#5a6772", "accent": "#0aa6a0",
        "ok": "#0a8f2a", "danger": "#d11a2a",
    },
}

current_app_theme = "dark"
logo_canvas = None
theme_btn = None

def tc(key):
    """Devuelve un color de la paleta del tema actual"""
    return PALETTE[current_app_theme][key]

# Atributos de color que se traducen al cambiar de tema
_THEME_ATTRS = ('background', 'foreground', 'activebackground', 'activeforeground',
                'selectbackground', 'selectforeground', 'highlightbackground',
                'highlightcolor', 'insertbackground', 'disabledforeground')

def _translate_tree(widget, from_theme, to_theme):
    """Recorre el árbol de widgets y traduce los colores de un tema a otro"""
    rev = {v.lower(): k for k, v in PALETTE[from_theme].items()}

    def _apply(w):
        for attr in _THEME_ATTRS:
            try:
                cur = w.cget(attr)
            except Exception:
                continue
            if not cur:
                continue
            key = rev.get(str(cur).lower())
            if key:
                try:
                    w.configure(**{attr: PALETTE[to_theme][key]})
                except Exception:
                    pass
        for child in w.winfo_children():
            _apply(child)

    _apply(widget)

def _apply_ttk_styles(theme):
    """Reconfigura los estilos ttk según el tema"""
    p = PALETTE[theme]
    tabsel_fg = "#000000" if theme == "dark" else "#ffffff"
    style.configure("TButton", background=p['panel'], foreground=p['text'])
    style.map("TButton",
              background=[('active', p['panel2'])],
              foreground=[('active', p['accent'])])
    style.configure("Accent.TButton", background=p['danger'], foreground="#ffffff")
    style.map("Accent.TButton", background=[('active', p['danger'])])
    style.configure("TNotebook", background=p['bg'])
    style.configure("TNotebook.Tab", background=p['panel'], foreground=p['muted'])
    style.map("TNotebook.Tab",
              background=[('selected', p['accent'])],
              foreground=[('selected', tabsel_fg)])
    style.configure("TLabel", background=p['bg'], foreground=p['text'])
    style.configure("TFrame", background=p['bg'])

def draw_logo():
    """Dibuja el logo MEDIBOT (rueda segmentada) en el canvas de la cabecera"""
    if logo_canvas is None:
        return
    import math
    gap = PALETTE[current_app_theme]['panel']  # color de fondo de la cabecera
    logo_canvas.delete("all")
    logo_canvas.configure(bg=gap)
    cx, cy, r = 28, 28, 22
    logo_canvas.create_oval(cx - r, cy - r, cx + r, cy + r, fill="#4FD8D2", outline="")
    for ang in range(0, 360, 45):
        rad = math.radians(ang)
        logo_canvas.create_line(cx, cy, cx + r * math.cos(rad), cy + r * math.sin(rad),
                                fill=gap, width=3)
    hr = 8
    logo_canvas.create_oval(cx - hr, cy - hr, cx + hr, cy + hr, fill=gap, outline="")

def apply_theme(to_theme):
    """Aplica un tema completo a toda la aplicación de escritorio"""
    global current_app_theme, bg_color, fg_color, accent_color, secondary_color
    _translate_tree(root, current_app_theme, to_theme)
    current_app_theme = to_theme
    p = PALETTE[to_theme]
    bg_color, fg_color, accent_color, secondary_color = p['bg'], p['text'], p['accent'], p['panel']
    try:
        root.configure(bg=p['bg'])
    except Exception:
        pass
    _apply_ttk_styles(to_theme)
    draw_logo()
    if theme_btn is not None:
        theme_btn.config(text="Modo Claro" if to_theme == "light" else "Modo Oscuro")
    try:
        update_person_list()
    except Exception:
        pass

def toggle_app_theme():
    """Alterna entre el modo claro y oscuro"""
    apply_theme("light" if current_app_theme == "dark" else "dark")

# Configurar colores (derivados del tema inicial: oscuro)
bg_color = PALETTE[current_app_theme]["bg"]
fg_color = PALETTE[current_app_theme]["text"]
accent_color = PALETTE[current_app_theme]["accent"]
secondary_color = PALETTE[current_app_theme]["panel"]

style.configure("TButton", 
                padding=10, 
                relief="flat", 
                background=secondary_color,
                foreground=fg_color,
                font=("Arial", 10, "bold"),
                borderwidth=1)
style.map("TButton",
          background=[('active', '#222222')],
          foreground=[('active', accent_color)])

style.configure("Accent.TButton",
                padding=10,
                relief="flat",
                background="#ff0000",
                foreground=fg_color,
                font=("Arial", 10, "bold"),
                borderwidth=1)
style.map("Accent.TButton",
          background=[('active', '#cc0000')])

style.configure("TNotebook", background=bg_color, borderwidth=0)
style.configure("TNotebook.Tab", 
                background=secondary_color,
                foreground="#888888",
                padding=[20, 10],
                font=("Arial", 10, "bold"))
style.map("TNotebook.Tab",
          background=[('selected', '#00ffff')],
          foreground=[('selected', '#000000')])

style.configure("TLabel", background=bg_color, foreground=fg_color)
style.configure("TFrame", background=bg_color)

# Frame principal que se expande
main_frame = tk.Frame(root, bg=bg_color)
main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

# Encabezado
header_frame = tk.Frame(main_frame, bg=secondary_color, pady=10)
header_frame.pack(fill=tk.X, padx=10, pady=(0, 10))

# Fila de marca: logo + nombre MEDIBOT + botón de cambio de tema
brand_row = tk.Frame(header_frame, bg=secondary_color)
brand_row.pack(fill=tk.X, padx=10, pady=(0, 6))

logo_canvas = tk.Canvas(brand_row, width=56, height=56,
                        bg=secondary_color, highlightthickness=0)
logo_canvas.pack(side=tk.LEFT, padx=(0, 12))

wordmark_frame = tk.Frame(brand_row, bg=secondary_color)
wordmark_frame.pack(side=tk.LEFT)
tk.Label(wordmark_frame, text="MEDI", font=("Arial", 20, "bold"),
         bg=secondary_color, fg=accent_color).pack(side=tk.LEFT)
tk.Label(wordmark_frame, text="BOT", font=("Arial", 20, "bold"),
         bg=secondary_color, fg=fg_color).pack(side=tk.LEFT)

theme_btn = ttk.Button(brand_row, text="Modo Oscuro", command=toggle_app_theme)
theme_btn.pack(side=tk.RIGHT, padx=10)

draw_logo()

tk.Label(header_frame,
         text="Medibot",
         font=("Arial", 14, "bold"),
         bg=secondary_color,
         fg=accent_color,
         wraplength=1000).pack()

# Notebook (pestañas) - Se expande
notebook = ttk.Notebook(main_frame)
notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

def make_scrollable(parent):
    """Crea un área desplazable (con barra de scroll vertical y rueda del ratón)
    dentro de 'parent' y devuelve el frame interior donde colocar el contenido."""
    container = tk.Frame(parent, bg=bg_color)
    container.pack(fill=tk.BOTH, expand=True)

    canvas = tk.Canvas(container, bg=bg_color, highlightthickness=0)
    vbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=vbar.set)
    vbar.pack(side=tk.RIGHT, fill=tk.Y)
    canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    inner = tk.Frame(canvas, bg=bg_color)
    win = canvas.create_window((0, 0), window=inner, anchor="nw")

    def _on_inner_config(_):
        canvas.configure(scrollregion=canvas.bbox("all"))
    inner.bind("<Configure>", _on_inner_config)

    def _on_canvas_config(event):
        # El contenido interior ocupa todo el ancho disponible
        canvas.itemconfigure(win, width=event.width)
    canvas.bind("<Configure>", _on_canvas_config)

    def _on_wheel(event):
        if event.num == 5 or event.delta < 0:
            canvas.yview_scroll(1, "units")
        elif event.num == 4 or event.delta > 0:
            canvas.yview_scroll(-1, "units")
    # La rueda solo desplaza este canvas mientras el ratón está encima
    def _bind_wheel(_):
        canvas.bind_all("<MouseWheel>", _on_wheel)
        canvas.bind_all("<Button-4>", _on_wheel)
        canvas.bind_all("<Button-5>", _on_wheel)
    def _unbind_wheel(_):
        canvas.unbind_all("<MouseWheel>")
        canvas.unbind_all("<Button-4>")
        canvas.unbind_all("<Button-5>")
    container.bind("<Enter>", _bind_wheel)
    container.bind("<Leave>", _unbind_wheel)

    return inner

# ============= PESTAÑA DE GESTIÓN =============
management_tab = tk.Frame(notebook, bg=bg_color)
notebook.add(management_tab, text="Gestión")

# Lista de personas
tk.Label(management_tab,
         text="Personas Registradas",
         font=("Arial", 12, "bold"),
         bg=bg_color,
         fg=accent_color).pack(pady=10)

# Frame para lista con scroll
list_frame = tk.Frame(management_tab, bg=bg_color)
list_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)

person_list = scrolledtext.ScrolledText(list_frame,
                                        font=("Courier", 10),
                                        bg=secondary_color,
                                        fg=fg_color,
                                        state=tk.DISABLED,
                                        borderwidth=1,
                                        relief="flat")
person_list.pack(fill=tk.BOTH, expand=True)

# Botones de gestión
management_buttons = tk.Frame(management_tab, bg=bg_color)
management_buttons.pack(pady=10)

ttk.Button(management_buttons,
           text="Agregar Persona",
           command=add_person).grid(row=0, column=0, padx=5, pady=5)

ttk.Button(management_buttons,
           text="Eliminar Persona",
           command=delete_person).grid(row=0, column=1, padx=5, pady=5)

ttk.Button(management_buttons,
           text="Entrenar Sistema",
           command=train_system).grid(row=0, column=2, padx=5, pady=5)

# Botón para configurar pastillero
ttk.Button(management_buttons,
           text="Configurar Pillbox",
           command=open_pastillero).grid(row=1, column=0, padx=5, pady=5, columnspan=3)

# Información de APIs
api_frame = tk.Frame(management_tab, bg=secondary_color, pady=10)
api_frame.pack(fill=tk.X, padx=20, pady=10)

# IPs REALES de esta maquina (antes habia una IP vieja hardcodeada de otra red
# y sin puerto: nadie podia conectarse copiando esa direccion). Si hay varias
# redes (cable y WiFi) se listan TODAS: el movil solo alcanza la de su red.
_ips_lan = medibot_red.listar_ips_lan()
ip_address = get_ip()
#  Solo las direcciones, sin instrucciones ni listado de APIs: las direcciones
#  son un DATO que hace falta (no se pueden adivinar y cambian de red en red),
#  mientras que el resto era texto explicativo que no aporta al manejo diario.
if _ips_lan:
    api_text = ""
    for _iface, _ip in _ips_lan:
        api_text += f"Medibot:  http://{_ip}:{VISION_WEB_PORT}   ({_iface})\n"
    api_text += f"Pillbox:  http://{_ips_lan[0][1]}:{PASTILLERO_PORT}"
else:
    api_text = ("SIN RED: este equipo no esta conectado a ninguna WiFi ni cable,\n"
                "asi que ningun otro dispositivo puede entrar todavia.")

tk.Label(api_frame,
         text=api_text,
         font=("Arial", 9),
         bg=secondary_color,
         fg="#00ff00",
         justify=tk.LEFT,
         wraplength=800).pack(padx=10, pady=5)

# ============= PESTAÑA DE MONITOREO =============
monitoring_tab = tk.Frame(notebook, bg=bg_color)
notebook.add(monitoring_tab, text="Monitoreo")

# Frame principal de monitoreo: DESPLAZABLE (barra de scroll + rueda del ratón)
# para poder llegar al botón de encender la cámara aunque la ventana sea pequeña
monitoring_main_frame = make_scrollable(monitoring_tab)

# Información del sistema
info_frame = tk.Frame(monitoring_main_frame, bg=bg_color, pady=10)
info_frame.pack(fill=tk.X)

tk.Label(info_frame,
         text=f"Dirección IP: {get_ip()}",
         font=("Arial", 10),
         bg=bg_color,
         fg=accent_color,
         wraplength=800).pack()

status_label = tk.Label(info_frame,
                        text="Estado: INACTIVO",
                        font=("Arial", 10, "bold"),
                        bg=bg_color,
                        fg="#ff0000")
status_label.pack(pady=5)

fps_label = tk.Label(info_frame,
                     text="FPS Cámara 1: 0 | FPS Cámara 2: 0",
                     font=("Arial", 9),
                     bg=bg_color,
                     fg="#888888")
fps_label.pack()

# Etiqueta de estado de cámaras
camera_status_label = tk.Label(info_frame,
                               text="Cámara 1: -- | Cámara 2: --",
                               font=("Arial", 9),
                               bg=bg_color,
                               fg="#888888")
camera_status_label.pack()

# Estado de grabación por cámara
recording_frame = tk.Frame(monitoring_main_frame, bg=bg_color, pady=5)
recording_frame.pack(fill=tk.X)

recording_label1 = tk.Label(recording_frame,
                            text="Cámara 1: Lista",
                            font=("Arial", 9),
                            bg=bg_color,
                            fg="#00ff00")
recording_label1.pack(side=tk.LEFT, padx=10)

recording_label2 = tk.Label(recording_frame,
                            text="Cámara 2: Lista",
                            font=("Arial", 9),
                            bg=bg_color,
                            fg="#00ff00")
recording_label2.pack(side=tk.RIGHT, padx=10)

# Frame para videos de ambas cámaras que se expande
videos_frame = tk.Frame(monitoring_main_frame, bg=bg_color, pady=10)
videos_frame.pack(fill=tk.BOTH, expand=True)

# Frame para cámara 1 - tamaño fijo
cam1_frame = tk.Frame(videos_frame, bg=secondary_color, padx=2, pady=2, 
                      width=VIEW_W, height=VIEW_H)
cam1_frame.pack(side=tk.LEFT, padx=5)
cam1_frame.pack_propagate(False)  # Mantener tamaño fijo

tk.Label(cam1_frame,
         text="CÁMARA 1 (Principal)",
         font=("Arial", 10, "bold"),
         bg=secondary_color,
         fg=accent_color,
         wraplength=200).pack(pady=5)

# Label de video 1 con tamaño fijo
video_label1 = tk.Label(cam1_frame, bg="#000000", width=VIEW_W, height=VIEW_H)
video_label1.pack(pady=5)

# Frame para cámara 2 - tamaño fijo
cam2_frame = tk.Frame(videos_frame, bg=secondary_color, padx=2, pady=2,
                      width=VIEW_W, height=VIEW_H)
cam2_frame.pack(side=tk.RIGHT, padx=5)
cam2_frame.pack_propagate(False)  # Mantener tamaño fijo

tk.Label(cam2_frame,
         text="CÁMARA 2 (Secundaria)",
         font=("Arial", 10, "bold"),
         bg=secondary_color,
         fg=accent_color,
         wraplength=200).pack(pady=5)

# Label de video 2 con tamaño fijo
video_label2 = tk.Label(cam2_frame, bg="#000000", width=VIEW_W, height=VIEW_H)
video_label2.pack(pady=5)

# Estado de visibilidad de la cámara 2 (se oculta si no se detecta)
_cam2_visible = True

# Botones de control
control_frame = tk.Frame(monitoring_main_frame, bg=bg_color, pady=15)
control_frame.pack(fill=tk.X, padx=20)

toggle_btn = ttk.Button(control_frame,
           text="INICIAR",
           command=toggle_system)
toggle_btn.pack(pady=5, fill=tk.X)

# Botón destacado: lanza Pastillero.py y divide la pantalla (Visión | Pastillero)
pastillero_split_btn = tk.Button(control_frame,
           text="Abrir Pillbox (pantalla dividida)",
           command=abrir_pastillero_dividido,
           bg="#2e7d32", fg="white",
           activebackground="#1b5e20", activeforeground="white",
           font=("Arial", 11, "bold"), relief=tk.RAISED, bd=2,
           cursor="hand2")
pastillero_split_btn.pack(pady=(5, 8), fill=tk.X, ipady=4)

record_btn = ttk.Button(control_frame,
           text="INICIAR GRABACIÓN AMBAS",
           command=toggle_recording)
record_btn.pack(pady=5, fill=tk.X)

# Botón para cambiar de cámara activa
switch_camera_btn = ttk.Button(control_frame,
           text="CAMBIAR CÁMARA ACTIVA (Visualización)",
           command=switch_camera)
switch_camera_btn.pack(pady=5, fill=tk.X)

# Botón para activar/desactivar el reconocimiento de personas registradas
recognition_btn = ttk.Button(control_frame,
           text="RECONOCIMIENTO: DESACTIVADO",
           command=toggle_recognition)
recognition_btn.pack(pady=5, fill=tk.X)

# Botón de pantalla completa (cámara + joystick translúcido para operar)
fullscreen_btn = ttk.Button(control_frame,
           text="PANTALLA COMPLETA (cámara)",
           command=lambda: open_fullscreen())
fullscreen_btn.pack(pady=5, fill=tk.X)

# (Aquí había un SEGUNDO botón de Pillbox, idéntico en función al verde de
#  arriba: ambos llamaban a abrir_pastillero_dividido y se sobrescribían la
#  misma variable, así que en pantalla salían dos botones para lo mismo.
#  Se conserva solo el verde destacado, que es el que se ve mejor.)

separator = tk.Frame(monitoring_main_frame, bg="#222222", height=1)
separator.pack(fill=tk.X, padx=20, pady=10)

nav_btn = ttk.Button(control_frame,
           text="IR A GESTIÓN",
           command=show_management_tab)
nav_btn.pack(pady=5, fill=tk.X)

# ============= CONTROL DE MOVIMIENTO (JOYSTICK) =============
def make_joystick(parent, size=160):
    """Crea un joystick W/A/S/D en 'parent'. Devuelve (canvas, status_label,
    key_press, key_release). Cada joystick tiene su propio estado interno."""
    st = {"keys": set(), "mouse": set(), "drag": False}
    cx = cy = size // 2
    base_r = int(size * 0.30)
    stick_r = max(8, int(size * 0.09))
    off = int(size * 0.20)

    canvas = tk.Canvas(parent, width=size, height=size,
                       bg=secondary_color, highlightthickness=1,
                       highlightbackground=accent_color)
    canvas.create_oval(cx - base_r, cy - base_r, cx + base_r, cy + base_r,
                       outline="#aaaaaa", width=2, fill="#cfd6dd")
    stick = canvas.create_oval(cx - stick_r, cy - stick_r, cx + stick_r, cy + stick_r,
                               fill="#ff3b3b", outline="")
    f = ("Arial", max(8, size // 15), "bold")
    canvas.create_text(cx, cy - base_r - 10, text="W", font=f, fill="#888888")
    canvas.create_text(cx, cy + base_r + 10, text="S", font=f, fill="#888888")
    canvas.create_text(cx - base_r - 12, cy, text="A", font=f, fill="#888888")
    canvas.create_text(cx + base_r + 12, cy, text="D", font=f, fill="#888888")

    status_label = tk.Label(parent, text="Dir: —", font=("Arial", 9, "bold"),
                            bg=secondary_color, fg=accent_color)

    def place_stick(nx, ny):
        canvas.coords(stick, nx - stick_r, ny - stick_r, nx + stick_r, ny + stick_r)

    def outputs():
        dirs = st["keys"] | st["mouse"]
        set_movement(dirs)
        txt = ", ".join(d.upper() for d in sorted(dirs)) if dirs else "—"
        try:
            status_label.config(text=f"Dir: {txt}")
        except Exception:
            pass

    def from_keys():
        dx = dy = 0
        if 'a' in st["keys"]: dx -= off
        if 'd' in st["keys"]: dx += off
        if 'w' in st["keys"]: dy -= off
        if 's' in st["keys"]: dy += off
        if not st["drag"]:
            place_stick(cx + max(-off, min(off, dx)), cy + max(-off, min(off, dy)))
        outputs()

    def from_mouse(x, y):
        dx = max(-off, min(off, x - cx))
        dy = max(-off, min(off, y - cy))
        place_stick(cx + dx, cy + dy)
        st["mouse"].clear()
        th = max(4, off // 4)
        if dx > th: st["mouse"].add('d')
        elif dx < -th: st["mouse"].add('a')
        if dy < -th: st["mouse"].add('w')
        elif dy > th: st["mouse"].add('s')
        outputs()

    def on_base(x, y):
        return (x - cx) ** 2 + (y - cy) ** 2 <= base_r ** 2

    def md(e):
        if on_base(e.x, e.y):
            st["drag"] = True
            from_mouse(e.x, e.y)
    def mdrag(e):
        if st["drag"]:
            from_mouse(e.x, e.y)
    def mu(e):
        if st["drag"]:
            st["drag"] = False
            place_stick(cx, cy)
            st["mouse"].clear()
            from_keys()

    def kp(e):
        k = e.keysym.lower()
        if k in TECLAS_MOVIMIENTO and k not in st["keys"]:
            st["keys"].add(k); from_keys()
    def kr(e):
        k = e.keysym.lower()
        if k in TECLAS_MOVIMIENTO and k in st["keys"]:
            st["keys"].discard(k); from_keys()

    canvas.bind('<Button-1>', md)
    canvas.bind('<B1-Motion>', mdrag)
    canvas.bind('<ButtonRelease-1>', mu)
    return canvas, status_label, kp, kr


# --- Joystick principal: DEBAJO de las cámaras (cerca, NO encima del vídeo) ---
joy_frame = tk.Frame(monitoring_main_frame, bg=bg_color, pady=6)
joy_frame.pack(before=control_frame, fill=tk.X)

tk.Label(joy_frame, text="MOVIMIENTO",
         font=("Arial", 10, "bold"), bg=bg_color, fg=accent_color).pack(pady=(4, 2))

joy_canvas, move_status_label, _joy_kp, _joy_kr = make_joystick(joy_frame, size=160)
joy_canvas.pack()
move_status_label.configure(bg=bg_color)
move_status_label.pack(pady=(2, 4))

root.bind('<KeyPress>', _joy_kp, add='+')
root.bind('<KeyRelease>', _joy_kr, add='+')


# --- Pantalla completa de la cámara con joystick TRANSLÚCIDO para operar ---
fs_win = None
fs_label = None
fs_geom = (0, 0, 1)          # (cx, cy, radio) del joystick dibujado, para el ratón
fs_keys = set()
fs_mouse = set()
fs_drag = [False]

def draw_joystick_overlay(img):
    """Dibuja un joystick semitransparente sobre el frame (BGR). Devuelve (cx,cy,R)."""
    h, w = img.shape[:2]
    R = max(40, int(min(w, h) * 0.13)); r = int(R * 0.4)
    cx, cy = w - R - 45, h - R - 45
    overlay = img.copy()
    cv2.circle(overlay, (cx, cy), R, (210, 210, 210), -1)
    cv2.circle(overlay, (cx, cy), R, (120, 120, 120), 3)
    ddx = (1 if movement_state['d'] else 0) - (1 if movement_state['a'] else 0)
    ddy = (1 if movement_state['s'] else 0) - (1 if movement_state['w'] else 0)
    sx, sy = cx + ddx * (R - r), cy + ddy * (R - r)
    cv2.circle(overlay, (sx, sy), r, (40, 40, 255), -1)
    cv2.addWeighted(overlay, 0.45, img, 0.55, 0, img)
    for (tx, ty, tt) in [(cx, cy - R - 8, "W"), (cx, cy + R + 20, "S"),
                         (cx - R - 20, cy + 6, "A"), (cx + R + 8, cy + 6, "D")]:
        cv2.putText(img, tt, (tx - 6, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
    return cx, cy, R

def _fs_kp(e):
    k = e.keysym.lower()
    if k in TECLAS_MOVIMIENTO:
        fs_keys.add(k); set_movement(fs_keys | fs_mouse)
def _fs_kr(e):
    k = e.keysym.lower()
    if k in TECLAS_MOVIMIENTO:
        fs_keys.discard(k); set_movement(fs_keys | fs_mouse)

def _fs_from_mouse(x, y):
    cx, cy, R = fs_geom
    fs_mouse.clear()
    th = R * 0.25
    if x - cx > th: fs_mouse.add('d')
    elif x - cx < -th: fs_mouse.add('a')
    if y - cy < -th: fs_mouse.add('w')
    elif y - cy > th: fs_mouse.add('s')
    set_movement(fs_keys | fs_mouse)
def _fs_md(e):
    cx, cy, R = fs_geom
    if (e.x - cx) ** 2 + (e.y - cy) ** 2 <= (R * 1.5) ** 2:
        fs_drag[0] = True; _fs_from_mouse(e.x, e.y)
def _fs_mdrag(e):
    if fs_drag[0]: _fs_from_mouse(e.x, e.y)
def _fs_mu(e):
    fs_drag[0] = False; fs_mouse.clear(); set_movement(fs_keys | fs_mouse)

def open_fullscreen():
    """Abre la cámara activa a pantalla completa con joystick translúcido encima."""
    global fs_win, fs_label
    if fs_win is not None:
        return
    fs_win = tk.Toplevel(root)
    fs_win.configure(bg="black")
    try:
        fs_win.attributes('-fullscreen', True)
    except Exception:
        pass
    fs_label = tk.Label(fs_win, bg="black")
    fs_label.pack(fill=tk.BOTH, expand=True)
    fs_label.bind('<Button-1>', _fs_md)
    fs_label.bind('<B1-Motion>', _fs_mdrag)
    fs_label.bind('<ButtonRelease-1>', _fs_mu)
    fs_win.bind('<KeyPress>', _fs_kp, add='+')
    fs_win.bind('<KeyRelease>', _fs_kr, add='+')
    fs_win.bind('<Escape>', lambda e: close_fullscreen())
    fs_win.protocol("WM_DELETE_WINDOW", close_fullscreen)
    ttk.Button(fs_win, text="Salir pantalla completa (Esc)",
               command=close_fullscreen).place(relx=0.0, rely=0.0, x=12, y=12)
    fs_win.focus_set()

def close_fullscreen():
    global fs_win, fs_label
    fs_keys.clear(); fs_mouse.clear(); set_movement(set())
    if fs_win is not None:
        try:
            fs_win.destroy()
        except Exception:
            pass
        fs_win = None
        fs_label = None

# Footer
footer_frame = tk.Frame(main_frame, bg=bg_color, pady=10)
footer_frame.pack(side=tk.BOTTOM, fill=tk.X)

tk.Label(footer_frame,
         text="Medibot",
         font=("Arial", 8),
         bg=bg_color,
         fg="#444444",
         wraplength=800).pack()

# Botón de salida en el footer
ttk.Button(footer_frame,
           text="SALIR",
           command=on_closing).pack(pady=5)

# Configurar directorios y actualizar lista inicial
setup_directories()
update_person_list()

# Servidor web de Vision: arranca YA, independiente de las camaras y del
# boton INICIAR. La web debe responder siempre que el programa este abierto
# (antes solo existia si las camaras iniciaban bien, y por eso "la IP era
# valida pero el navegador no respondia").
iniciar_servidor_web()

# Iniciar actualización de GUI
update_gui()
root.mainloop()
