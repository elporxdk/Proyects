#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import tkinter as tk
import RPi.GPIO as GPIO

# -------------------- CONFIGURACIÓN DE PINES --------------------
UP_PIN    = 17   # W
DOWN_PIN  = 27   # S
LEFT_PIN  = 22   # A
RIGHT_PIN = 23   # D

pines = [UP_PIN, DOWN_PIN, LEFT_PIN, RIGHT_PIN]

GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)
for pin in pines:
    GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)

tecla_a_pin = {
    'w': UP_PIN,
    's': DOWN_PIN,
    'a': LEFT_PIN,
    'd': RIGHT_PIN
}

teclas_presionadas = set()
mouse_dirs = set()
mouse_drag = False

def actualizar_gpio():
    print(f"-> GPIO: teclas={sorted(teclas_presionadas)}, mouse={sorted(mouse_dirs)}")
    for tecla, pin in tecla_a_pin.items():
        activo = (tecla in teclas_presionadas) or (tecla in mouse_dirs)
        if tecla == 's':   # destaco el pin 27 para depurar
            print(f"   * Pin 27 (S): {'HIGH' if activo else 'LOW'}")
        GPIO.output(pin, GPIO.HIGH if activo else GPIO.LOW)

# -------------------- INTERFAZ --------------------
ventana = tk.Tk()
ventana.title("Joystick Debug")
ventana.resizable(False, False)
ANCHO = 200
ALTO  = 200
ventana.geometry(f"{ANCHO}x{ALTO}")
try:
    ventana.attributes('-alpha', 0.7)
except:
    pass
ventana.attributes('-topmost', True)

lienzo = tk.Canvas(ventana, width=ANCHO, height=ALTO, bg='white', highlightthickness=0)
lienzo.pack()

centro_x, centro_y = ANCHO//2, ALTO//2
paso = 8
offset_max = 22
radio_base = 40
radio_stick = 7

lienzo.create_oval(centro_x-radio_base, centro_y-radio_base,
                   centro_x+radio_base, centro_y+radio_base,
                   outline='gray', width=2, fill='lightgray')
stick = lienzo.create_oval(centro_x-radio_stick, centro_y-radio_stick,
                           centro_x+radio_stick, centro_y+radio_stick,
                           fill='red')
fuente = ("Arial", 12, "bold")
lienzo.create_text(centro_x, centro_y-radio_base-12, text="W", font=fuente)
lienzo.create_text(centro_x, centro_y+radio_base+12, text="S", font=fuente)
lienzo.create_text(centro_x-radio_base-15, centro_y, text="A", font=fuente)
lienzo.create_text(centro_x+radio_base+15, centro_y, text="D", font=fuente)

pos_stick = [centro_x, centro_y]

def set_stick_position(x, y):
    dx = max(-offset_max, min(offset_max, x - centro_x))
    dy = max(-offset_max, min(offset_max, y - centro_y))
    nx = centro_x + dx
    ny = centro_y + dy
    lienzo.coords(stick, nx-radio_stick, ny-radio_stick,
                  nx+radio_stick, ny+radio_stick)
    pos_stick[0], pos_stick[1] = nx, ny
    update_mouse_dirs()
    actualizar_gpio()

def update_mouse_dirs():
    dx = pos_stick[0] - centro_x
    dy = pos_stick[1] - centro_y
    mouse_dirs.clear()
    umbral = 3
    if dx > umbral:
        mouse_dirs.add('d')
    elif dx < -umbral:
        mouse_dirs.add('a')
    if dy < -umbral:
        mouse_dirs.add('w')
    elif dy > umbral:
        mouse_dirs.add('s')
    print(f"   Mouse dirs: {sorted(mouse_dirs)}  (dx={dx}, dy={dy})")

def mover_stick():
    dx, dy = 0, 0
    if 'a' in teclas_presionadas: dx -= paso
    if 'd' in teclas_presionadas: dx += paso
    if 'w' in teclas_presionadas: dy -= paso
    if 's' in teclas_presionadas: dy += paso
    nx = centro_x + max(-offset_max, min(offset_max, dx))
    ny = centro_y + max(-offset_max, min(offset_max, dy))
    lienzo.coords(stick, nx-radio_stick, ny-radio_stick,
                  nx+radio_stick, ny+radio_stick)
    pos_stick[0], pos_stick[1] = nx, ny
    actualizar_gpio()

# -------------------- TECLADO --------------------
def al_presionar(event):
    tecla = event.keysym.lower()
    print(f"KeyPress: '{tecla}'")
    if tecla in tecla_a_pin and tecla not in teclas_presionadas:
        teclas_presionadas.add(tecla)
        if not mouse_drag:
            mover_stick()
        else:
            actualizar_gpio()

def al_soltar(event):
    tecla = event.keysym.lower()
    print(f"KeyRelease: '{tecla}'")
    if tecla in tecla_a_pin and tecla in teclas_presionadas:
        teclas_presionadas.remove(tecla)
        if not mouse_drag:
            mover_stick()
        else:
            actualizar_gpio()

ventana.bind('<KeyPress>', al_presionar)
ventana.bind('<KeyRelease>', al_soltar)
ventana.focus_set()

# -------------------- RATÓN --------------------
def clic_sobre_base(x, y):
    return (x - centro_x)**2 + (y - centro_y)**2 <= radio_base**2

def pulsar_raton(event):
    global mouse_drag
    if clic_sobre_base(event.x, event.y):
        mouse_drag = True
        print(f"Mouse drag INICIO en ({event.x},{event.y})")
        set_stick_position(event.x, event.y)

def arrastrar_raton(event):
    if mouse_drag:
        set_stick_position(event.x, event.y)

def soltar_raton(event):
    global mouse_drag
    if mouse_drag:
        mouse_drag = False
        print("Mouse drag FIN")
        set_stick_position(centro_x, centro_y)
        mover_stick()

lienzo.bind('<Button-1>', pulsar_raton)
lienzo.bind('<B1-Motion>', arrastrar_raton)
lienzo.bind('<ButtonRelease-1>', soltar_raton)

def cerrar():
    GPIO.cleanup()
    ventana.destroy()

ventana.protocol("WM_DELETE_WINDOW", cerrar)

print("=== Joystick Debug iniciado ===")
ventana.mainloop()
