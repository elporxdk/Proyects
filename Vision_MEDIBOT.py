import cv2
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

# ================= GPIO OPCIONAL + SALIDA SERIAL (COM) =================
# Funciona en Raspberry Pi (Bullseye Raspbian) y también sin hardware.
# - Si RPi.GPIO no está disponible, se desactiva automáticamente.
# - Pon GPIO_ENABLED = False para forzar la desactivación (ej. probar en PC
#   o en una Raspberry sin servos/motores conectados).
# - Cuando el GPIO está SIMULADO, cada orden se envía por el puerto COM/serial
#   para que un Arduino/ESP conectado por USB la ejecute. Formato (una por línea):
#       GPIO,<pin>,<0|1>     -> pin digital LOW/HIGH   (ej. "GPIO,17,1")
#       PWM,<pin>,<duty>     -> ciclo de trabajo servo (ej. "PWM,18,7.5")
#   Si no hay puerto o falta pyserial, las órdenes se muestran por consola.
GPIO_ENABLED = True

SERIAL_ENABLED = True   # Enviar las órdenes del GPIO simulado por el puerto serial
SERIAL_PORT = None      # None = autodetectar; o fija uno: "COM3" (Windows) / "/dev/ttyUSB0"
SERIAL_BAUD = 115200    # Debe coincidir con el Serial.begin() del Arduino/ESP

_serial_conn = None
_serial_lock = threading.Lock()
_serial_last = {}       # último valor enviado por pin (evita repetir mensajes)

def serial_connect():
    """Abre el puerto COM/serial (autodetecta si SERIAL_PORT es None)."""
    global _serial_conn
    if not SERIAL_ENABLED:
        return None
    try:
        import serial
        from serial.tools import list_ports
    except Exception as e:
        print(f"AVISO: pyserial no instalado ({e}).")
        print("       Las órdenes GPIO se mostrarán por consola. Instala con: pip install pyserial")
        return None
    port = SERIAL_PORT
    if port is None:
        found = [p.device for p in list_ports.comports()]
        port = found[0] if found else None
    if port is None:
        print("AVISO: no se detectó ningún puerto COM/serial; "
              "las órdenes GPIO se mostrarán por consola.")
        return None
    try:
        _serial_conn = serial.Serial(port, SERIAL_BAUD, timeout=0.1)
        print(f"Puerto serial abierto: {port} @ {SERIAL_BAUD} baudios")
    except Exception as e:
        print(f"AVISO: no se pudo abrir el puerto serial {port}: {e}")
        _serial_conn = None
    return _serial_conn

def serial_send(msg):
    """Envía una orden por el puerto serial; sin puerto, la muestra por consola."""
    with _serial_lock:
        if _serial_conn is not None:
            try:
                _serial_conn.write((msg + "\n").encode())
                return
            except Exception as e:
                print(f"AVISO: error escribiendo en el serial: {e}")
        print(f"[SERIAL] {msg}")

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

if GPIO_ENABLED:
    try:
        import RPi.GPIO as GPIO
    except Exception as _e:
        print(f"AVISO: RPi.GPIO no disponible ({_e}). GPIO simulado: "
              f"las órdenes se enviarán por el puerto COM/serial.")
        GPIO = _DummyGPIO()
        GPIO_ENABLED = False
        serial_connect()
else:
    print("AVISO: GPIO desactivado por configuración (GPIO_ENABLED = False).")
    GPIO = _DummyGPIO()
    serial_connect()
# ================================================
from datetime import datetime
import subprocess
import sys

# ================= CONFIGURACIÓN =================
MAX_PERSONS = 5
IMAGES_PER_PERSON = 500
CONF_LIMIT = 700

FRAME_W, FRAME_H = 640, 480
VIEW_W, VIEW_H = 400, 300

ZONE_X = FRAME_W // 3
ZONE_Y = FRAME_H // 3

PWM_X, PWM_Y = 18, 13

# Pines de movimiento del robot (joystick W/A/S/D)
MOVE_PINS = {"w": 17, "s": 27, "a": 22, "d": 23}

DATA_PATH = "data"
VIDEO_PATH = "videos"
# ================================================

# ================= CONFIGURACIÓN DE CÁMARAS ============
CAMERA_INDICES = [0, 1] 
# ================================================

# ================= GPIO ========================= QUITALO SI NO LO USARAS CHAN
GPIO.setmode(GPIO.BCM)
GPIO.setup(PWM_X, GPIO.OUT)
GPIO.setup(PWM_Y, GPIO.OUT)
pwm_x = GPIO.PWM(PWM_X, 50)
pwm_y = GPIO.PWM(PWM_Y, 50)
pwm_x.start(7.5)
pwm_y.start(7.5)

# ---- Pines de movimiento (joystick W/A/S/D) ----
for _mpin in MOVE_PINS.values():
    GPIO.setup(_mpin, GPIO.OUT, initial=GPIO.LOW)

movement_state = {"w": False, "a": False, "s": False, "d": False}

def apply_movement():
    """Refleja movement_state en los pines GPIO de movimiento"""
    for _d, _pin in MOVE_PINS.items():
        try:
            GPIO.output(_pin, GPIO.HIGH if movement_state[_d] else GPIO.LOW)
        except Exception:
            pass

def set_movement(directions):
    """Activa las direcciones indicadas (iterable de 'w','a','s','d') y apaga el resto"""
    for _d in movement_state:
        movement_state[_d] = _d in directions
    apply_movement()

def center_pwm():
    """Centrar la camara"""
    pwm_x.ChangeDutyCycle(7.5)
    pwm_y.ChangeDutyCycle(7.5)

def move_servos(x_pos, y_pos):
    """Mueve los servomotores basado en la posición del rostro"""
    if x_pos == "left":
        pwm_x.ChangeDutyCycle(5.5)
    elif x_pos == "right":
        pwm_x.ChangeDutyCycle(9.5)
    else:
        pwm_x.ChangeDutyCycle(7.5)
    
    if y_pos == "up":
        pwm_y.ChangeDutyCycle(5.5)
    elif y_pos == "down":
        pwm_y.ChangeDutyCycle(9.5)
    else:
        pwm_y.ChangeDutyCycle(7.5)

# ================= OPTIMIZADOR DE CÁMARA =========
class CameraOptimizer:
    def __init__(self):
        self.brightness = 0.5
        self.contrast = 0.5
        self.saturation = 0.5
        self.sharpness = 0.5
        self.exposure = 0.0
        
    def auto_adjust(self, frame):
        """Ajusta la cámara"""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        
        # Ajustar brillo con el histograma
        hist = cv2.calcHist([gray], [0], None, [256], [0, 256])
        hist_percent = np.cumsum(hist) / np.sum(hist)
        
        # Encontrar percentiles para ajuste
        dark_thresh = np.where(hist_percent > 0.05)[0][0]
        bright_thresh = np.where(hist_percent > 0.95)[0][0]
        
        # Ajustar parámetros
        if dark_thresh < 50:
            self.brightness = min(1.0, self.brightness + 0.1)
        elif bright_thresh > 200:
            self.brightness = max(0.0, self.brightness - 0.1)
            
        # Ajustar contraste basado en desviación estándar
        std_dev = np.std(gray)
        if std_dev < 30:
            self.contrast = min(1.0, self.contrast + 0.1)
        elif std_dev > 100:
            self.contrast = max(0.0, self.contrast - 0.1)
            
        return frame
    
    def apply_settings(self, camera):
        """Aplica los ajustes a la cámara"""
        try:
            camera.set(cv2.CAP_PROP_BRIGHTNESS, self.brightness)
            camera.set(cv2.CAP_PROP_CONTRAST, self.contrast)
            camera.set(cv2.CAP_PROP_SATURATION, self.saturation)
            camera.set(cv2.CAP_PROP_SHARPNESS, self.sharpness)
            camera.set(cv2.CAP_PROP_EXPOSURE, self.exposure)
        except:
            pass
    
    def manual_adjust(self, setting, value):
        """Ajuste manual de parámetros"""
        if setting == "brightness":
            self.brightness = value
        elif setting == "contrast":
            self.contrast = value
        elif setting == "saturation":
            self.saturation = value
        elif setting == "sharpness":
            self.sharpness = value
        elif setting == "exposure":
            self.exposure = value

camera_optimizer = CameraOptimizer()

# ================= SEGUIMIENTO DE COORDENADAS =========
class ObjectTracker:
    def __init__(self, max_history=50):
        self.object_history = {}
        self.max_history = max_history
        self.tracking_enabled = True
        
    def update_tracking(self, objects, frame_time):
        """Actualiza el historial de seguimiento de objetos"""
        for obj in objects:
            obj_id = f"{obj['center_x']}_{obj['center_y']}_{obj['area']}"
            
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
        
        # Limpiar objetos antiguos (no vistos por mass de 5 segundos)
        current_time = time.time()
        to_remove = []
        for obj_id, data in self.object_history.items():
            if current_time - data['last_seen'] > 5:
                to_remove.append(obj_id)
        
        for obj_id in to_remove:
            del self.object_history[obj_id]
    
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
            obj_id = f"{obj['center_x']}_{obj['center_y']}_{obj['area']}"
            
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
    """Obtiene la dirección IP local del dispositivo"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip

def get_registered_persons():
    """Obtiene la lista de personas registradas"""
    if not os.path.exists(DATA_PATH):
        return []
    persons = []
    for folder in os.listdir(DATA_PATH):
        folder_path = os.path.join(DATA_PATH, folder)
        if os.path.isdir(folder_path):
            images = len([f for f in os.listdir(folder_path) if f.endswith('.jpg')])
            persons.append({"name": folder, "images": images})
    return persons

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
def detect_red_objects(frame):
    """Detecta objetos de color rojo en el frame"""
    # Convertir de BGR a HSV
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    
    # Definir rangos para el color rojo (en HSV)
    lower_red1 = np.array([0, 120, 70])
    upper_red1 = np.array([10, 255, 255])
    lower_red2 = np.array([170, 120, 70])
    upper_red2 = np.array([180, 255, 255])
    
    # Crear máscaras para ambos rangos
    mask1 = cv2.inRange(hsv, lower_red1, upper_red1)
    mask2 = cv2.inRange(hsv, lower_red2, upper_red2)
    
    # Combinar las máscaras
    red_mask = cv2.bitwise_or(mask1, mask2)
    
    # Aplicar operaciones morfológicas para eliminar ruido
    kernel = np.ones((5,5), np.uint8)
    red_mask = cv2.morphologyEx(red_mask, cv2.MORPH_OPEN, kernel)
    red_mask = cv2.morphologyEx(red_mask, cv2.MORPH_CLOSE, kernel)
    red_mask = cv2.dilate(red_mask, kernel, iterations=1)
    
    # Encontrar contornos
    contours, _ = cv2.findContours(red_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    red_objects = []
    
    for contour in contours:
        area = cv2.contourArea(contour)
        if area > 300:  # Filtrar contornos pequeños
            x, y, w, h = cv2.boundingRect(contour)
            
            # Calcular centro
            center_x = x + w // 2
            center_y = y + h // 2
            
            # Dibujar cuadrito alrededor del objeto rojo
            cv2.rectangle(frame, (x, y), (x+w, y+h), (0, 0, 255), 2)
            cv2.circle(frame, (center_x, center_y), 5, (255, 255, 255), -1)
            cv2.putText(frame, f"ROJO {int(area)}", (x, y-10), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
            
            red_objects.append({
                "x": x,
                "y": y,
                "width": w,
                "height": h,
                "area": int(area),
                "color": "rojo",
                "center_x": center_x,
                "center_y": center_y
            })
    
    return frame, red_objects

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

# Variables globales para ambas cámaras
camera1 = None
camera2 = None
camera1_thread = None
camera2_thread = None
active_camera_index = 0
last_frame = None
last_frame1 = None
last_frame2 = None
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
fps_counter1 = 0
fps_counter2 = 0
fps1 = 0
fps2 = 0
last_fps_time1 = time.time()
last_fps_time2 = time.time()
video_files = []

# ================= MANEJO DE CÁMARAS MÚLTIPLES SIMULTÁNEAS =========
def initialize_cameras():
    """Inicializa ambas cámaras xd"""
    global camera1, camera2
    success1 = False
    success2 = False
    
    try:
        # Inicializar cámara 1
        if CAMERA_INDICES[0] is not None:
            cap1 = cv2.VideoCapture(CAMERA_INDICES[0], cv2.CAP_V4L2)
            if cap1.isOpened():
                cap1.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_W)
                cap1.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_H)
                cap1.set(cv2.CAP_PROP_FPS, 30)
                cap1.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                cap1.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc('M', 'J', 'P', 'G'))
                camera_optimizer.apply_settings(cap1)
                camera1 = cap1
                success1 = True
                print(f"Cámara 1 inicializada en índice {CAMERA_INDICES[0]}")
            else:
                print(f"Error: No se pudo abrir cámara 1 en índice {CAMERA_INDICES[0]}")
        
        # Inicializar cámara 2
        if len(CAMERA_INDICES) > 1 and CAMERA_INDICES[1] is not None:
            cap2 = cv2.VideoCapture(CAMERA_INDICES[1], cv2.CAP_V4L2)
            if cap2.isOpened():
                cap2.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_W)
                cap2.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_H)
                cap2.set(cv2.CAP_PROP_FPS, 30)
                cap2.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                cap2.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc('M', 'J', 'P', 'G'))
                camera_optimizer.apply_settings(cap2)
                camera2 = cap2
                success2 = True
                print(f"Cámara 2 inicializada en índice {CAMERA_INDICES[1]}")
            else:
                print(f"Error: No se pudo abrir cámara 2 en índice {CAMERA_INDICES[1]}")
        
        return success1 or success2
        
    except Exception as e:
        print(f"Error inicializando cámaras: {e}")
        return False

def release_cameras():
    """Libera ambas cámaras de forma segura"""
    global camera1, camera2, video_writer1, video_writer2
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

def read_frame_from_camera(camera, camera_index):
    """Lee un frame de una cámara específica"""
    global fps_counter1, fps1, last_fps_time1, fps_counter2, fps2, last_fps_time2
    try:
        if camera is None or not camera.isOpened():
            return False, None
        
        ret, frame = camera.read()
        
        # Calcular FPS para la cámara específica
        if camera_index == 0:
            fps_counter1 += 1
            current_time = time.time()
            if current_time - last_fps_time1 >= 1.0:
                fps1 = fps_counter1
                fps_counter1 = 0
                last_fps_time1 = current_time
        else:
            fps_counter2 += 1
            current_time = time.time()
            if current_time - last_fps_time2 >= 1.0:
                fps2 = fps_counter2
                fps_counter2 = 0
                last_fps_time2 = current_time
        
        return ret, frame
        
    except Exception as e:
        print(f"Error leyendo frame de cámara {camera_index}: {e}")
        return False, None

# ================= PROCESAMIENTO SIMULTÁNEO DE AMBAS CÁMARAS =======
def process_camera(camera_index):
    """Procesa una cámara específica en un hilo separado"""
    global last_frame1, last_frame2, system_status, detection_count, captured_images
    global capture_mode, face_position, last_face_time, detected_red_objects
    global online, recording, video_writer1, video_writer2, recording_cam1, recording_cam2
    global fps1, fps2
    
    print(f"Iniciando procesamiento de cámara {camera_index + 1}")
    
    camera = camera1 if camera_index == 0 else camera2
    if camera is None:
        print(f"Cámara {camera_index + 1} no disponible")
        return
    
    frame_time = time.time()
    local_video_writer = None
    is_recording = False
    
    while online:
        try:
            # Leer frame de la cámara
            ret, frame = read_frame_from_camera(camera, camera_index)
            
            if not ret or frame is None:
                print(f"Error leyendo frame de cámara {camera_index + 1}, reintentando...")
                time.sleep(0.1)
                continue
            
            # Aplicar optimización automática de cámara usando la función pasada
            frame = camera_optimizer.auto_adjust(frame)
            
            # Detección de objetos rojos con seguimiento un poco optimizado
            processed_frame, red_objects = detect_red_objects(frame.copy())
            
            # Actualizar seguimiento de objetos
            current_time = time.time()
            object_tracker.update_tracking(red_objects, current_time)
            
            # Dibujar trayectorias de seguimiento
            if object_tracker.tracking_enabled:
                processed_frame = object_tracker.draw_tracking(processed_frame, red_objects)
            
            # Detección facial si el clasificador está disponible
            if cascade is not None and camera_index == 0:  # Solo en cámara 1 para control de servos
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30))
                
                # Modo captura de imágenes
                if capture_mode and len(faces) > 0:
                    for (x, y, w, h) in faces:
                        roi_gray = gray[y:y+h, x:x+w]
                        
                        # Guardar imagen
                        person_path = os.path.join(DATA_PATH, current_capture_name)
                        cv2.imwrite(f"{person_path}/{captured_images}.jpg", roi_gray)
                        captured_images += 1
                        
                        # Dibujar rectángulo
                        cv2.rectangle(processed_frame, (x, y), (x+w, y+h), (0, 255, 255), 3)
                        cv2.putText(processed_frame, f"CAPTURANDO: {captured_images}/{IMAGES_PER_PERSON}", 
                                   (x, y-10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
                        
                        if captured_images >= IMAGES_PER_PERSON:
                            capture_mode = False
                            system_status = "Captura Completada"
                        
                        break
                
                # Modo reconocimiento facial (solo si está activado por el usuario)
                elif not capture_mode and recognition_enabled and recognizer is not None and len(faces) > 0:
                    known = False
                    last_face_time = time.time()
                    
                    for (x, y, w, h) in faces:
                        detection_count += 1
                        roi_gray = gray[y:y+h, x:x+w]
                        
                        try:
                            id_, conf = recognizer.predict(roi_gray)
                            
                            if conf < CONF_LIMIT:
                                known = True
                                cx, cy = x + w // 2, y + h // 2

                                # Determinar posición
                                if cx < ZONE_X:
                                    pos_x = "left"
                                elif cx > ZONE_X * 2:
                                    pos_x = "right"
                                else:
                                    pos_x = "center"

                                if cy < ZONE_Y:
                                    pos_y = "up"
                                elif cy > ZONE_Y * 2:
                                    pos_y = "down"
                                else:
                                    pos_y = "center"

                                # Actualizar posición global
                                face_position = {"x": pos_x, "y": pos_y}
                                
                                # Mover servomotores (solo cámara 1 controla servos)
                                if camera_index == 0:
                                    move_servos(pos_x, pos_y)

                                # Etiqueta para rostro conocido
                                persons = get_registered_persons()
                                if id_ < len(persons):
                                    person_name = persons[id_]["name"]
                                else:
                                    person_name = f"ID {id_}"
                                    
                                label = f"{person_name} ({int(100-conf)}%)"
                                
                                cv2.putText(processed_frame, label, (x, y - 30),
                                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
                                cv2.putText(processed_frame, "AUTORIZADO", (x, y - 10),
                                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
                                cv2.rectangle(processed_frame, (x, y), (x+w, y+h), (0, 255, 0), 3)
                                system_status = f"Rostro: {person_name}"
                            else:
                                # Etiqueta para rostro desconocido
                                cv2.putText(processed_frame, "DESCONOCIDO", (x, y - 10),
                                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
                                cv2.rectangle(processed_frame, (x, y), (x+w, y+h), (0, 0, 255), 3)
                                system_status = "Rostro Desconocido"
                        except Exception as e:
                            print(f"Error en reconocimiento facial: {e}")
                            system_status = "Error Reconocimiento"

                        break

                    if not known and not capture_mode:
                        if len(faces) == 0:
                            if camera_index == 0:
                                center_pwm()
                            if system_status not in ["Rostro Conocido", "Rostro Desconocido", "Error Reconocimiento"]:
                                system_status = "Escaneando"
                else:
                    if camera_index == 0:
                        center_pwm()
            
            # Seguimiento automático de objetos rojos si no hay rostros (solo cámara 1)
            if camera_index == 0 and len(faces) == 0 and red_objects and object_tracker.tracking_enabled:
                # Seguir el objeto rojo más grande
                largest_obj = max(red_objects, key=lambda obj: obj['area'])
                cx, cy = largest_obj['center_x'], largest_obj['center_y']
                
                # Determinar posición del objeto
                if cx < ZONE_X:
                    pos_x = "left"
                elif cx > ZONE_X * 2:
                    pos_x = "right"
                else:
                    pos_x = "center"

                if cy < ZONE_Y:
                    pos_y = "up"
                elif cy > ZONE_Y * 2:
                    pos_y = "down"
                else:
                    pos_y = "center"

                # Mover servomotores para seguir el objeto
                move_servos(pos_x, pos_y)
                system_status = f"Siguiendo objeto rojo ({largest_obj['area']}px)"
            
            # Manejo de grabación para esta cámara
            if recording:
                if camera_index == 0 and not recording_cam1:
                    # Iniciar grabación cámara 1
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    video_filename = os.path.join(VIDEO_PATH, "camara1", f"video_cam1_{timestamp}.avi")
                    fourcc = cv2.VideoWriter_fourcc(*'XVID')
                    video_writer1 = cv2.VideoWriter(video_filename, fourcc, 20.0, (FRAME_W, FRAME_H))
                    recording_cam1 = True
                    print(f"Cámara 1 comenzó a grabar: {video_filename}")
                
                if camera_index == 1 and not recording_cam2:
                    # Iniciar grabación cámara 2
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    video_filename = os.path.join(VIDEO_PATH, "camara2", f"video_cam2_{timestamp}.avi")
                    fourcc = cv2.VideoWriter_fourcc(*'XVID')
                    video_writer2 = cv2.VideoWriter(video_filename, fourcc, 20.0, (FRAME_W, FRAME_H))
                    recording_cam2 = True
                    print(f"Cámara 2 comenzó a grabar: {video_filename}")
                
                # Escribir frame si estamos grabando
                if camera_index == 0 and video_writer1 is not None:
                    video_writer1.write(processed_frame)
                elif camera_index == 1 and video_writer2 is not None:
                    video_writer2.write(processed_frame)
            else:
                # Detener grabación si está activa
                if camera_index == 0 and recording_cam1:
                    if video_writer1 is not None:
                        video_writer1.release()
                        video_writer1 = None
                    recording_cam1 = False
                    print("Cámara 1 detuvo la grabación")
                
                if camera_index == 1 and recording_cam2:
                    if video_writer2 is not None:
                        video_writer2.release()
                        video_writer2 = None
                    recording_cam2 = False
                    print("Cámara 2 detuvo la grabación")
            
            # Información en pantalla mejorada
            cv2.putText(processed_frame, f"Cámara: {camera_index + 1}", (10, FRAME_H - 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
            
            if camera_index == 0:
                fps_text = f"FPS Cam1: {fps1}"
            else:
                fps_text = f"FPS Cam2: {fps2}"
            
            cv2.putText(processed_frame, fps_text, (FRAME_W - 150, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
            
            if recording and ((camera_index == 0 and recording_cam1) or (camera_index == 1 and recording_cam2)):
                cv2.putText(processed_frame, "● GRABANDO", (FRAME_W - 120, 60),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
            
            if len(red_objects) > 0:
                cv2.putText(processed_frame, f"Objetos Rojos: {len(red_objects)}", (10, 90),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
            
            if object_tracker.tracking_enabled:
                cv2.putText(processed_frame, "SEGUIMIENTO ACTIVO", (10, FRAME_H - 20),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

            # REDIMENSIONAMIENTO CORREGIDO: Solo una vez a VIEW_W x VIEW_H
            # Guardar el frame redimensionado para la interfaz
            if camera_index == 0:
                # Verificar que el frame tenga el tamaño correcto antes de redimensionar
                if processed_frame.shape[1] != VIEW_W or processed_frame.shape[0] != VIEW_H:
                    last_frame1 = cv2.resize(processed_frame, (VIEW_W, VIEW_H))
                else:
                    last_frame1 = processed_frame.copy()
                    
                if active_camera_index == 0:
                    global last_frame
                    last_frame = last_frame1.copy() if last_frame1 is not None else None
            else:
                # Verificar que el frame tenga el tamaño correcto antes de redimensionar
                if processed_frame.shape[1] != VIEW_W or processed_frame.shape[0] != VIEW_H:
                    last_frame2 = cv2.resize(processed_frame, (VIEW_W, VIEW_H))
                else:
                    last_frame2 = processed_frame.copy()
                    
                if active_camera_index == 1:
                    last_frame = last_frame2.copy() if last_frame2 is not None else None
            
            # Control de FPS optimizado
            elapsed = time.time() - frame_time
            sleep_time = max(0.0, 0.033 - elapsed)  # Objetivo: 30 FPS
            time.sleep(sleep_time)
            frame_time = time.time()
            
        except Exception as e:
            print(f"Error en procesamiento de cámara {camera_index + 1}: {e}")
            time.sleep(0.1)
    
    # Limpiar al salir
    print(f"Deteniendo procesamiento de cámara {camera_index + 1}...")
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

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Sistema de Visión Artificial - Dos Cámaras</title>
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
        
        .cameras-container {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 30px;
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
        
        .video-container {
            position: relative;
            border-radius: 5px;
            overflow: hidden;
            margin-top: 10px;
            border: 1px solid #333333;
            background: #000000;
            height: 350px;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        
        .video-container img {
            width: 100%;
            height: 100%;
            object-fit: contain;
            display: block;
        }
        
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
            .cameras-container {
                grid-template-columns: 1fr;
            }
            
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
        html[data-theme="light"] .footer { color: #9aa6b2; }
        html[data-theme="light"] .theme-toggle { background: #f4f7fa; color: #15202b; border-color: #d4dae0; }
        html[data-theme="light"] .theme-toggle:hover { background: #e3e9ef; border-color: #0aa6a0; }
        html[data-theme="light"] .wm-bot { color: #15202b; }
        html[data-theme="light"] .brand-tag { color: #5a6772; }

        /* ===== Joystick / Movimiento ===== */
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
        .fs-btn {
            position: absolute; top: 10px; right: 10px; z-index: 6;
            background: rgba(0, 0, 0, 0.5); color: #fff;
            border: 1px solid rgba(255, 255, 255, 0.35);
            padding: 6px 10px; border-radius: 6px; cursor: pointer; font-size: 0.85em;
        }
        .fs-btn:hover { background: rgba(0, 0, 0, 0.75); border-color: #4FD8D2; }
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
        #cam1-view:fullscreen, #cam1-view:-webkit-full-screen {
            background: #000; display: flex; align-items: center; justify-content: center;
        }
        #cam1-view:fullscreen img { width: auto; height: 100%; max-width: 100%; }
        #cam1-view:fullscreen .cam-joystick { transform: scale(1.7); right: 60px; bottom: 60px; opacity: 0.75; }
    </style>
</head>
<body>
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
            <button class="theme-toggle" id="themeToggle" onclick="toggleTheme()" title="Cambiar tema claro/oscuro">🌙 Modo Oscuro</button>
        </div>
        <h1>SISTEMA DE VISIÓN ARTIFICIAL - DOS CÁMARAS</h1>
        <p class="subtitle">Detección facial, seguimiento de objetos y grabación simultánea con dos cámaras</p>
        
        <div class="cameras-container">
            <div class="camera-box">
                <div class="camera-title" id="cam1-title">
                    <span class="status-dot"></span>
                    CÁMARA 1 (Principal)
                </div>
                <div class="video-container" id="cam1-view">
                    <img src="/video/0" alt="Cámara 1 en vivo" id="video-stream-1">
                    <button class="fs-btn" onclick="toggleCameraFullscreen()" title="Pantalla completa">&#9974; Pantalla completa</button>
                    <div class="cam-joystick">
                        <div class="joystick-base" id="joyBase">
                            <div class="joystick-stick" id="joyStick"></div>
                        </div>
                        <div class="cam-joy-dirs">Dir: <span id="move-dirs">—</span></div>
                    </div>
                </div>
                <div class="camera-info">
                    <div class="info-item">
                        <div class="info-label">FPS</div>
                        <div class="info-value" id="fps-1">0</div>
                    </div>
                    <div class="info-item">
                        <div class="info-label">Estado</div>
                        <div class="info-value" id="status-1">Inactiva</div>
                    </div>
                    <div class="info-item">
                        <div class="info-label">Grabando</div>
                        <div class="info-value" id="recording-1">No</div>
                    </div>
                    <div class="info-item">
                        <div class="info-label">Resolución</div>
                        <div class="info-value">640x480</div>
                    </div>
                </div>
            </div>
            
            <div class="camera-box" id="camera2-box">
                <div class="camera-title" id="cam2-title">
                    <span class="status-dot"></span>
                    CÁMARA 2 (Secundaria)
                </div>
                <div class="video-container">
                    <img src="/video/1" alt="Cámara 2 en vivo" id="video-stream-2">
                </div>
                <div class="camera-info">
                    <div class="info-item">
                        <div class="info-label">FPS</div>
                        <div class="info-value" id="fps-2">0</div>
                    </div>
                    <div class="info-item">
                        <div class="info-label">Estado</div>
                        <div class="info-value" id="status-2">Inactiva</div>
                    </div>
                    <div class="info-item">
                        <div class="info-label">Grabando</div>
                        <div class="info-value" id="recording-2">No</div>
                    </div>
                    <div class="info-item">
                        <div class="info-label">Resolución</div>
                        <div class="info-value">640x480</div>
                    </div>
                </div>
            </div>
        </div>
        
        <div class="status-container">
            <div class="status-box">
                <div class="status-title">Estado Sistema</div>
                <div class="status-value" id="system-status">Inactivo</div>
            </div>
            <div class="status-box">
                <div class="status-title">Detecciones Totales</div>
                <div class="status-value" id="total-detections">0</div>
            </div>
            <div class="status-box">
                <div class="status-title">Cámara Activa</div>
                <div class="status-value" id="active-camera">1</div>
            </div>
            <div class="status-box">
                <div class="status-title">Objetos Rojos</div>
                <div class="status-value" id="red-objects">0</div>
            </div>
        </div>
        
        <div class="position-indicator">
            <div class="status-title">Posición del Rostro (Cámara 1)</div>
            <div class="position-grid">
                <div class="position-cell" id="pos-tl">↖</div>
                <div class="position-cell" id="pos-tc">↑</div>
                <div class="position-cell" id="pos-tr">↗</div>
                <div class="position-cell" id="pos-cl">←</div>
                <div class="position-cell center" id="pos-cc">●</div>
                <div class="position-cell" id="pos-cr">→</div>
                <div class="position-cell" id="pos-bl">↙</div>
                <div class="position-cell" id="pos-bc">↓</div>
                <div class="position-cell" id="pos-br">↘</div>
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
            <button class="control-button" onclick="showTab('videos')">
                Ver Videos Grabados
            </button>
        </div>
        
        <div class="tab-container">
            <div class="tab-buttons">
                <button class="tab-button active" onclick="showTab('info')">Información</button>
                <button class="tab-button" onclick="showTab('videos')">Videos Grabados</button>
                <button class="tab-button" onclick="showTab('settings')">Configuración</button>
            </div>
            
            <div class="tab-content active" id="info-tab">
                <div class="panel-box">
                    <h3>Información del Sistema</h3>
                    <p>Sistema de visión artificial con dos cámaras funcionando simultáneamente.</p>
                    <p><strong>Características:</strong></p>
                    <ul style="margin-left: 20px; margin-top: 10px;">
                        <li>Dos cámaras funcionando en paralelo</li>
                        <li>Grabación simultánea en ambas cámaras</li>
                        <li>Detección facial y seguimiento de objetos</li>
                        <li>Control de servomotores para seguimiento</li>
                        <li>Interfaz web en tiempo real</li>
                        <li>API REST para integración</li>
                    </ul>
                    <p><strong>Estado actual:</strong> <span id="current-status">Inactivo</span></p>
                </div>
            </div>


            <div class="tab-content" id="videos-tab">
                <h3>Videos Grabados (Ordenados por fecha)</h3>
                <div class="videos-grid" id="videos-grid">
                    Cargando videos...
                </div>
            </div>
            
            <div class="tab-content" id="settings-tab">
                <div class="panel-box">
                    <h3>Configuración del Sistema</h3>
                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-top: 20px;">
                        <div>
                            <h4>Cámara 1</h4>
                            <button class="control-button" onclick="optimizeCamera(0)" style="width: 100%; margin-top: 10px;">
                                Optimizar Cámara 1
                            </button>
                        </div>
                        <div>
                            <h4>Cámara 2</h4>
                            <button class="control-button" onclick="optimizeCamera(1)" style="width: 100%; margin-top: 10px;">
                                Optimizar Cámara 2
                            </button>
                        </div>
                    </div>
                    <button class="control-button" onclick="centerCamera()" style="width: 100%; margin-top: 20px;">
                        Centrar Servomotores
                    </button>
                </div>
            </div>
        </div>
        
        <div class="footer">
            <p>Sistema de visión artificial con dos cámaras | Detección facial y seguimiento de objetos</p>
            <p>IP: <span id="current-ip">127.0.0.1</span> | Puerto: 5000 | API: /api/all</p>
        </div>
    </div>

    <script>
        let currentTab = 'info';
        let updateInterval;
        
        function updateCameraStatus(data) {
            // Actualizar estado de las cámaras
            document.getElementById('cam1-title').classList.toggle('active', data.cameras.camera1.active);
            document.getElementById('cam2-title').classList.toggle('active', data.cameras.camera2.active);

            // Mostrar solo la(s) cámara(s) activa(s): ocultar la 2 si no se detecta
            var cam2box = document.getElementById('camera2-box');
            if (cam2box) {
                cam2box.style.display = data.cameras.camera2.active ? '' : 'none';
                document.querySelector('.cameras-container').style.gridTemplateColumns =
                    data.cameras.camera2.active ? '1fr 1fr' : '1fr';
            }

            // Reflejar el estado del reconocimiento en el botón
            var recBtn = document.getElementById('recognitionBtn');
            if (recBtn) {
                recBtn.textContent = data.recognition_enabled ? 'Reconocimiento: ON' : 'Reconocimiento: OFF';
                recBtn.classList.toggle('active', !!data.recognition_enabled);
            }

            document.getElementById('fps-1').textContent = data.cameras.camera1.fps;
            document.getElementById('fps-2').textContent = data.cameras.camera2.fps;
            
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
            document.getElementById('total-detections').textContent = data.detection_count;
            document.getElementById('active-camera').textContent = data.active_camera;
            document.getElementById('red-objects').textContent = data.red_objects ? data.red_objects.length : 0;
            document.getElementById('current-status').textContent = data.system_status;
            
            // Actualizar posición del rostro
            updatePosition(data.face_position);
            
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
        
        function updatePosition(data) {
            // Reset all cells
            document.querySelectorAll('.position-cell').forEach(cell => {
                cell.classList.remove('active');
            });
            
            // Activate corresponding cell
            let cellId = 'pos-';
            if (data.y === 'up') cellId += 't';
            else if (data.y === 'center') cellId += 'c';
            else if (data.y === 'down') cellId += 'b';
            
            if (data.x === 'left') cellId += 'l';
            else if (data.x === 'center') cellId += 'c';
            else if (data.x === 'right') cellId += 'r';
            
            const cell = document.getElementById(cellId);
            if (cell) cell.classList.add('active');
        }
        
        function loadVideos() {
            fetch('/api/videos')
                .then(response => response.json())
                .then(videos => {
                    const grid = document.getElementById('videos-grid');
                    
                    if (!videos || videos.length === 0) {
                        grid.innerHTML = '<p>No hay videos grabados.</p>';
                        return;
                    }
                    
                    let html = '';
                    videos.forEach(video => {
                        const date = new Date(video.created * 1000);
                        const sizeMB = (video.size / 1024 / 1024).toFixed(2);
                        html += `
                        <div class="video-card">
                            <div class="video-thumbnail">
                                <span>🎥 ${video.camera.toUpperCase()}</span>
                            </div>
                            <div class="video-info">
                                <div class="video-title">${video.name}</div>
                                <div class="video-meta">
                                    <span class="video-camera">${video.camera}</span>
                                    ${sizeMB} MB<br>
                                    ${date.toLocaleDateString()} ${date.toLocaleTimeString()}
                                </div>
                                <div class="video-actions">
                                    <button class="video-action-btn" onclick="playVideo('${video.name}', '${video.camera}')">Reproducir</button>
                                    <button class="video-action-btn" onclick="downloadVideo('${video.name}', '${video.camera}')">Descargar</button>
                                </div>
                            </div>
                        </div>`;
                    });
                    
                    grid.innerHTML = html;
                })
                .catch(error => {
                    console.error('Error cargando videos:', error);
                    document.getElementById('videos-grid').innerHTML = '<p>Error cargando videos.</p>';
                });
        }
        
        function showTab(tabName) {
            // Update tab buttons
            document.querySelectorAll('.tab-button').forEach(btn => {
                btn.classList.remove('active');
            });
            
            document.querySelectorAll('.tab-button').forEach(btn => {
                if (btn.textContent.includes(tabName.charAt(0).toUpperCase() + tabName.slice(1))) {
                    btn.classList.add('active');
                }
            });
            
            // Update tab content
            document.querySelectorAll('.tab-content').forEach(content => {
                content.classList.remove('active');
            });
            
            document.getElementById(tabName + '-tab').classList.add('active');
            currentTab = tabName;
            
            // Load videos if showing videos tab
            if (tabName === 'videos') {
                loadVideos();
            }
        }
        
        function toggleSystem() {
            fetch('/toggle_system', { method: 'POST' })
                .then(response => response.json())
                .then(data => {
                    console.log('Sistema:', data.message);
                    fetchData();
                })
                .catch(error => console.error('Error:', error));
        }
        
        function toggleRecording() {
            fetch('/toggle_recording', { method: 'POST' })
                .then(response => response.json())
                .then(data => {
                    console.log('Grabación:', data.message);
                    fetchData();
                })
                .catch(error => console.error('Error:', error));
        }

        function toggleRecognition() {
            fetch('/toggle_recognition', { method: 'POST' })
                .then(response => response.json())
                .then(data => {
                    console.log('Reconocimiento:', data.message);
                    fetchData();
                })
                .catch(error => console.error('Error:', error));
        }

        function toggleCameraFullscreen() {
            var el = document.getElementById('cam1-view');
            if (!el) return;
            var fsEl = document.fullscreenElement || document.webkitFullscreenElement;
            if (!fsEl) {
                var req = el.requestFullscreen || el.webkitRequestFullscreen;
                if (req) req.call(el);
            } else {
                var exit = document.exitFullscreen || document.webkitExitFullscreen;
                if (exit) exit.call(document);
            }
        }

        function switchCamera() {
            fetch('/switch_camera', { method: 'POST' })
                .then(response => response.json())
                .then(data => {
                    console.log('Cámara cambiada:', data.message);
                    fetchData();
                })
                .catch(error => console.error('Error:', error));
        }
        
        function optimizeCamera(cameraIndex) {
            fetch('/optimize_camera', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ camera_index: cameraIndex })
            })
            .then(response => response.json())
            .then(data => {
                alert('Cámara ' + (cameraIndex + 1) + ' optimizada: ' + data.message);
            })
            .catch(error => console.error('Error:', error));
        }
        
        function centerCamera() {
            fetch('/center_camera', { method: 'POST' })
                .then(response => response.json())
                .then(data => {
                    alert('Servomotores centrados');
                })
                .catch(error => console.error('Error:', error));
        }
        
        function playVideo(filename, camera) {
            window.open(`/play_video/${camera}/${filename}`, '_blank');
        }
        
        function downloadVideo(filename, camera) {
            window.open(`/download_video/${camera}/${filename}`, '_blank');
        }
        
        function fetchData() {
            fetch('/api/all')
                .then(response => response.json())
                .then(data => {
                    updateCameraStatus(data);
                })
                .catch(error => console.error('Error obteniendo datos:', error));
        }
        
        // Set current IP
        document.getElementById('current-ip').textContent = window.location.hostname;
        
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
                fetch('/move', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ directions: dirs })
                }).catch(() => {});
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
                    const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
                    let dx = clientX - cx, dy = clientY - cy;
                    const max = r.width / 2 - 30;
                    const dist = Math.hypot(dx, dy) || 1;
                    if (dist > max) { dx = dx / dist * max; dy = dy / dist * max; }
                    stick.style.transform = 'translate(' + dx + 'px,' + dy + 'px)';
                    const th = 14;
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
                btn.textContent = theme === 'light' ? '☀️ Modo Claro' : '🌙 Modo Oscuro';
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
        });
    </script>
</body>
</html>
"""

@app.route("/")
def index():
    """Página principal del sistema web"""
    return render_template_string(HTML_TEMPLATE)

@app.route("/video/<int:camera_index>")
def video(camera_index):
    """Stream de video para cada cámara"""
    def generate_frames():
        global last_frame1, last_frame2, online
        while online:
            frame = None
            if camera_index == 0 and last_frame1 is not None:
                frame = last_frame1.copy() if last_frame1 is not None else None
            elif camera_index == 1 and last_frame2 is not None:
                frame = last_frame2.copy() if last_frame2 is not None else None
            
            if frame is None:
                # Crear frame negro si no hay cámara
                frame = np.zeros((VIEW_H, VIEW_W, 3), dtype=np.uint8)
                cv2.putText(frame, f"Cámara {camera_index + 1} no disponible", 
                           (50, VIEW_H//2), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
                time.sleep(0.1)
            
            try:
                # Convertir frame a JPEG
                ret, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                if not ret:
                    continue
                
                frame_bytes = buffer.tobytes()
                
                # Enviar frame
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
            except Exception as e:
                print(f"Error generando frame cámara {camera_index}: {e}")
                time.sleep(0.1)
    
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
        "red_objects": detected_red_objects,
        "largest_red_object": max(detected_red_objects, key=lambda x: x['area']) if detected_red_objects else None,
        "camera_settings": {
            "brightness": camera_optimizer.brightness,
            "contrast": camera_optimizer.contrast,
            "saturation": camera_optimizer.saturation,
            "sharpness": camera_optimizer.sharpness,
            "exposure": camera_optimizer.exposure
        },
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
    """Reproduce un video grabado"""
    file_path = os.path.join(VIDEO_PATH, camera, filename)
    if not os.path.exists(file_path):
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
    """Descarga un video grabado"""
    return send_from_directory(os.path.join(VIDEO_PATH, camera), filename, as_attachment=True)

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
        if not os.path.exists("trainer.yml"):
            recognizer = None
        else:
            try:
                recognizer = cv2.face.LBPHFaceRecognizer_create()
                recognizer.read("trainer.yml")
            except:
                recognizer = None
        
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
        if recognizer is None and os.path.exists("trainer.yml"):
            try:
                recognizer = cv2.face.LBPHFaceRecognizer_create()
                recognizer.read("trainer.yml")
            except Exception:
                recognizer = None
        if recognizer is None:
            recognition_enabled = False
            return jsonify({"recognition_enabled": False,
                            "message": "No hay modelo entrenado (trainer.yml)"})
    return jsonify({
        "recognition_enabled": recognition_enabled,
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
    
    if camera_index == 0 and camera1 is not None:
        camera_optimizer.apply_settings(camera1)
        return jsonify({"message": "Cámara 1 optimizada"})
    elif camera_index == 1 and camera2 is not None:
        camera_optimizer.apply_settings(camera2)
        return jsonify({"message": "Cámara 2 optimizada"})
    
    return jsonify({"error": "Cámara no disponible"}), 400

@app.route("/auto_optimize_camera", methods=["POST"])
def auto_optimize_camera_endpoint():
    """Optimiza automáticamente la cámara"""
    return jsonify({
        "message": "Optimización automática completada",
        "brightness": camera_optimizer.brightness,
        "contrast": camera_optimizer.contrast,
        "saturation": camera_optimizer.saturation
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
    
    if setting and value is not None:
        camera_optimizer.manual_adjust(setting, value)
        # Aplicar a ambas cámaras
        if camera1 is not None:
            camera_optimizer.apply_settings(camera1)
        if camera2 is not None:
            camera_optimizer.apply_settings(camera2)
        return jsonify({"message": f"Ajuste {setting} actualizado a {value}"})
    
    return jsonify({"error": "Datos inválidos"}), 400

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
    """Recibe comandos de movimiento del joystick web y los aplica a los GPIO"""
    data = request.get_json(silent=True) or {}
    directions = [d for d in data.get("directions", []) if d in MOVE_PINS]
    set_movement(directions)
    return jsonify({"directions": sorted(directions), "state": movement_state})

@app.route("/stop_movement", methods=["POST"])
def stop_movement_endpoint():
    """Detiene todo el movimiento"""
    set_movement([])
    return jsonify({"message": "Movimiento detenido", "state": movement_state})

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
    
    messagebox.showinfo("Captura Iniciada", 
                       f"Se capturarán {IMAGES_PER_PERSON} imágenes de {person_name}.\n"
                       f"Posicione su rostro frente a la cámara 1.\n"
                       f"Mueva su cabeza ligeramente para diferentes ángulos.")

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
            
            for idx, person in enumerate(persons):
                progress_label.config(text=f"Procesando: {person['name']}")
                person_path = os.path.join(DATA_PATH, person['name'])
                
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
            
            progress_window.destroy()
            messagebox.showinfo("Entrenamiento Completo",
                               f"El sistema ha sido entrenado exitosamente con {len(persons)} personas.")
            
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

# ================= FUNCIÓN PARA ABRIR PASTILLERO =========
def open_pastillero():
    """Abre la interfaz web del pastillero"""
    url = "http://192.168.3.208"
    try:
        webbrowser.open(url)
        messagebox.showinfo("Pastillero", f"Abriendo configuración del pastillero en:\n{url}")
    except Exception as e:
        messagebox.showerror("Error", f"No se pudo abrir el pastillero:\n{str(e)}")

# ========= LANZAR Pastillero.py + PANTALLA DIVIDIDA =========
# Ejecuta el script local Pastillero.py (levanta su servidor Flask en el
# puerto 5001) y divide la pantalla: Visión a la izquierda, pastillero a la
# derecha, para operar ambos a la vez.
_pastillero_proc = None
PASTILLERO_PORT = 5001

def _puerto_abierto(host, port, timeout=0.5):
    """True si hay algo escuchando en host:port."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
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
        messagebox.showerror("Pastillero",
            f"No se encontró Pastillero.py en:\n{base_dir}")
        return False
    try:
        _pastillero_proc = subprocess.Popen([sys.executable, script], cwd=base_dir)
    except Exception as e:
        messagebox.showerror("Pastillero", f"No se pudo iniciar Pastillero.py:\n{e}")
        return False
    # Esperar a que Flask levante (hasta ~8 s)
    for _ in range(40):
        if _puerto_abierto("127.0.0.1", PASTILLERO_PORT):
            return True
        time.sleep(0.2)
    messagebox.showwarning("Pastillero",
        "Pastillero.py se inició, pero el servidor web (puerto 5001) aún no "
        "responde. Reintenta el botón en unos segundos.")
    return False

def _abrir_navegador_pastillero(x, y, w, h):
    """Abre la UI del pastillero. Intenta Chromium en una ventana ya
    posicionada (para la pantalla dividida); si no, usa el navegador
    por defecto."""
    url = f"http://127.0.0.1:{PASTILLERO_PORT}"
    for navegador in ("chromium-browser", "chromium", "google-chrome", "google-chrome-stable"):
        ruta = shutil.which(navegador)
        if ruta:
            try:
                subprocess.Popen([ruta,
                    f"--app={url}",
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
    messagebox.showinfo("Pastillero",
        "Pastillero iniciado en pantalla dividida.\n\n"
        "Izquierda: Visión MEDIBOT\n"
        f"Derecha: Pastillero (http://127.0.0.1:{PASTILLERO_PORT})\n\n"
        "Nota COM: en la Raspberry Pi, Visión maneja el movimiento por los "
        "pines GPIO y el pastillero usa el puerto serie (USB), así que NO "
        "compiten por el mismo puerto.")

# ================= CONTROL PRINCIPAL ===================
def toggle_system():
    """Alterna entre iniciar y detener el sistema"""
    global recognizer, online, face_position, system_status
    global detection_count, recording, recording_cam1, recording_cam2
    global video_writer1, video_writer2
    
    if not online:
        # Iniciar sistema: la cámara arranca SIN reconocer caras.
        # Se precarga el modelo (si existe) por si luego activas el reconocimiento.
        if os.path.exists("trainer.yml"):
            try:
                recognizer = cv2.face.LBPHFaceRecognizer_create()
                recognizer.read("trainer.yml")
                print("Modelo de reconocimiento facial precargado (reconocimiento desactivado)")
            except Exception as e:
                print(f"Error cargando modelo de reconocimiento: {e}")
                recognizer = None
        else:
            recognizer = None

        online = True
        face_position = {"x": "center", "y": "center"}
        detection_count = 0
        center_pwm()
        status_label.config(text="Estado: ONLINE", foreground=tc('ok'))
        toggle_btn.config(text="DETENER")
        system_status = "Iniciando"
        
        # Iniciar procesamiento de ambas cámaras
        if start_camera_processing():
            # Iniciar servidor web
            flask_thread = threading.Thread(
                target=lambda: app.run(host="0.0.0.0", port=5000,
                                       debug=False, use_reloader=False, threaded=True),
                daemon=True
            )
            flask_thread.start()
            
            messagebox.showinfo("Sistema Activo",
                f"Sistema iniciado correctamente con DOS CÁMARAS.\n\n"
                f"Accede desde tu navegador:\n"
                f"http://{get_ip()}:5000\n\n"
                f"Cámara 1: {'ACTIVA' if camera1 is not None else 'INACTIVA'}\n"
                f"Cámara 2: {'ACTIVA' if camera2 is not None else 'INACTIVA'}\n"
                f"Reconocimiento facial: {'ACTIVADO' if recognizer else 'DESACTIVADO'}\n"
                f"API para ESP32: http://{get_ip()}:5000/api/esp32")
        else:
            online = False
            messagebox.showerror("Error", "No se pudieron inicializar las cámaras.")
        
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
        if recognizer is None and os.path.exists("trainer.yml"):
            try:
                recognizer = cv2.face.LBPHFaceRecognizer_create()
                recognizer.read("trainer.yml")
            except Exception as e:
                print(f"Error cargando modelo: {e}")
                recognizer = None
        if recognizer is None:
            recognition_enabled = False
            messagebox.showwarning("Reconocimiento",
                                   "No hay un modelo entrenado (trainer.yml).\n"
                                   "Registra personas y entrena el sistema primero.")
    _update_recognition_btn()

# ================= INTERFAZ GRÁFICA CORREGIDA ==============
def update_gui():
    """Actualiza la interfaz gráfica CORREGIDA"""
    global last_frame1, last_frame2, _cam2_visible, fs_geom
    
    # Tamaño fijo para los labels de video (VIEW_W x VIEW_H)
    fixed_width = VIEW_W
    fixed_height = VIEW_H
    
    # Actualizar vista de cámara 1
    if last_frame1 is not None:
        try:
            # Verificar que el frame tenga el tamaño correcto
            if last_frame1.shape[1] != fixed_width or last_frame1.shape[0] != fixed_height:
                # Redimensionar solo si es necesario
                frame_resized = cv2.resize(last_frame1, (fixed_width, fixed_height))
                frame_rgb1 = cv2.cvtColor(frame_resized, cv2.COLOR_BGR2RGB)
            else:
                frame_rgb1 = cv2.cvtColor(last_frame1, cv2.COLOR_BGR2RGB)
            
            img1 = Image.fromarray(frame_rgb1)
            imgtk1 = ImageTk.PhotoImage(image=img1)
            
            video_label1.imgtk = imgtk1
            video_label1.configure(image=imgtk1)
        except Exception as e:
            print(f"Error actualizando GUI cámara 1: {e}")
    
    # Actualizar vista de cámara 2
    if last_frame2 is not None:
        try:
            # Verificar que el frame tenga el tamaño correcto
            if last_frame2.shape[1] != fixed_width or last_frame2.shape[0] != fixed_height:
                # Redimensionar solo si es necesario
                frame_resized = cv2.resize(last_frame2, (fixed_width, fixed_height))
                frame_rgb2 = cv2.cvtColor(frame_resized, cv2.COLOR_BGR2RGB)
            else:
                frame_rgb2 = cv2.cvtColor(last_frame2, cv2.COLOR_BGR2RGB)
            
            img2 = Image.fromarray(frame_rgb2)
            imgtk2 = ImageTk.PhotoImage(image=img2)
            
            video_label2.imgtk = imgtk2
            video_label2.configure(image=imgtk2)
        except Exception as e:
            print(f"Error actualizando GUI cámara 2: {e}")
    
    # Actualizar estado de grabación
    if recording:
        record_btn.config(text="DETENER GRABACIÓN AMBAS", style="Accent.TButton")
    else:
        record_btn.config(text="INICIAR GRABACIÓN AMBAS", style="TButton")
    
    # Actualizar estado del sistema
    if online:
        status_label.config(text=f"Estado: ONLINE (Cámara Activa: {active_camera_index + 1})", foreground=tc('ok'))
        toggle_btn.config(text="DETENER")

        # Actualizar información de FPS
        fps_label.config(text=f"FPS Cámara 1: {fps1} | FPS Cámara 2: {fps2}")
    else:
        status_label.config(text="Estado: INACTIVO", foreground=tc('danger'))
        toggle_btn.config(text="INICIAR")
        fps_label.config(text="FPS Cámara 1: 0 | FPS Cámara 2: 0")
    
    # Actualizar información de cámaras
    camera_status = f"Cámara 1: {'✓' if camera1 is not None else '✗'} | Cámara 2: {'✓' if camera2 is not None else '✗'}"
    camera_status_label.config(text=camera_status)

    # Mostrar solo la(s) cámara(s) activa(s): si la cámara 2 no se detecta, ocultar su ventana
    if camera2 is not None and not _cam2_visible:
        cam2_frame.pack(side=tk.RIGHT, padx=5)
        _cam2_visible = True
    elif camera2 is None and _cam2_visible:
        cam2_frame.pack_forget()
        _cam2_visible = False
    
    # Actualizar estado de grabación por cámara
    if recording_cam1:
        recording_label1.config(text="Cámara 1: GRABANDO", foreground=tc('danger'))
    else:
        recording_label1.config(text="Cámara 1: Lista", foreground=tc('ok'))

    if recording_cam2:
        recording_label2.config(text="Cámara 2: GRABANDO", foreground=tc('danger'))
    else:
        recording_label2.config(text="Cámara 2: Lista", foreground=tc('ok'))

    # Volcar la cámara activa a la ventana de pantalla completa (con joystick translúcido)
    if fs_win is not None and fs_label is not None:
        try:
            src = last_frame1 if active_camera_index == 0 else last_frame2
            if src is None:
                src = last_frame1 if last_frame1 is not None else last_frame2
            sw, sh = fs_win.winfo_width(), fs_win.winfo_height()
            if src is not None and sw > 10 and sh > 10:
                big = cv2.resize(src, (sw, sh))
                fs_geom = draw_joystick_overlay(big)
                rgb = cv2.cvtColor(big, cv2.COLOR_BGR2RGB)
                imtk = ImageTk.PhotoImage(image=Image.fromarray(rgb))
                fs_label.imgtk = imtk
                fs_label.configure(image=imtk)
        except Exception:
            pass

    # Programar siguiente actualización
    root.after(50, update_gui)

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
root.title("Sistema de Visión Artificial - Dos Cámaras Simultáneas")
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
        theme_btn.config(text="☀️ Modo Claro" if to_theme == "light" else "🌙 Modo Oscuro")
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

theme_btn = ttk.Button(brand_row, text="🌙 Modo Oscuro", command=toggle_app_theme)
theme_btn.pack(side=tk.RIGHT, padx=10)

draw_logo()

tk.Label(header_frame,
         text="SISTEMA DE VISIÓN ARTIFICIAL - DOS CÁMARAS SIMULTÁNEAS",
         font=("Arial", 14, "bold"),
         bg=secondary_color,
         fg=accent_color,
         wraplength=1000).pack()

tk.Label(header_frame,
         text="Detección facial, seguimiento de objetos y grabación simultánea con dos cámaras",
         font=("Arial", 9),
         bg=secondary_color,
         fg="#888888",
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
           text="Configurar Pastillero",
           command=open_pastillero).grid(row=1, column=0, padx=5, pady=5, columnspan=3)

# Información de APIs
api_frame = tk.Frame(management_tab, bg=secondary_color, pady=10)
api_frame.pack(fill=tk.X, padx=20, pady=10)

ip_address = get_ip()
api_text = f"APIs disponibles:\n"
api_text += f"• http://{ip_address}:5000 (Interfaz web)\n"
api_text += f"• http://{ip_address}:5000/api/all (todos los datos)\n"
api_text += f"• http://{ip_address}:5000/api/esp32 (datos compactos ESP32)\n"
api_text += f"• http://{ip_address}:5000/api/videos (lista videos ordenados)\n"
api_text += f"Pastillero: http://192.168.3.208"

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
                               text="Cámara 1: ✗ | Cámara 2: ✗",
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

# Botón: lanza Pastillero.py y divide la pantalla (Visión | Pastillero)
pastillero_split_btn = ttk.Button(control_frame,
           text="ABRIR PASTILLERO (pantalla dividida)",
           command=abrir_pastillero_dividido)
pastillero_split_btn.pack(pady=5, fill=tk.X)

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
        if k in MOVE_PINS and k not in st["keys"]:
            st["keys"].add(k); from_keys()
    def kr(e):
        k = e.keysym.lower()
        if k in MOVE_PINS and k in st["keys"]:
            st["keys"].discard(k); from_keys()

    canvas.bind('<Button-1>', md)
    canvas.bind('<B1-Motion>', mdrag)
    canvas.bind('<ButtonRelease-1>', mu)
    return canvas, status_label, kp, kr


# --- Joystick principal: DEBAJO de las cámaras (cerca, NO encima del vídeo) ---
joy_frame = tk.Frame(monitoring_main_frame, bg=bg_color, pady=6)
joy_frame.pack(before=control_frame, fill=tk.X)

tk.Label(joy_frame, text="CONTROL DE MOVIMIENTO (teclas W / A / S / D o ratón)",
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
    if k in MOVE_PINS:
        fs_keys.add(k); set_movement(fs_keys | fs_mouse)
def _fs_kr(e):
    k = e.keysym.lower()
    if k in MOVE_PINS:
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
         text="Sistema de Visión Artificial v7.0 | Dos cámaras simultáneas + Pastillero",
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

# Iniciar actualización de GUI
update_gui()
root.mainloop()
