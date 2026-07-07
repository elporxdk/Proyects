#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MEDIBOT - Lanzador unico
========================
Ejecuta SOLO este archivo y arranca todo el sistema, en orden y coexistiendo:

  1. Hub serial (serial_hub.py): el unico dueno del puerto COM/USB del
     Arduino. Si habia un hub viejo corriendo, lo apaga y arranca uno con el
     codigo actual (evita quedarse con una version vieja en memoria).
  2. Pillbox (Pastillero.py): interfaz web en  http://<ip-de-la-pi>:5001
  3. Medibot / Vision (Vision_MEDIBOT.py): interfaz de camaras y movimiento.

Al cerrar la ventana de Medibot: se manda una orden de STOP al chasis
(seguridad), se cierra Pillbox y termina todo.

Uso:
    python3 main.py

Si falta algo (un archivo o una libreria), este lanzador lo dice con un
mensaje claro y como instalarlo, en vez de un traceback.
"""

import importlib.util
import os
import runpy
import socket
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))

# Archivos que deben existir JUNTO a main.py (nombres exactos, sin espacios)
ARCHIVOS_REQUERIDOS = (
    "medibot_serial.py",
    "serial_hub.py",
    "Pastillero.py",
    "Vision_MEDIBOT.py",
)

# modulo de python -> nombre del paquete en pip
DEPS_BASE = {"flask": "flask", "serial": "pyserial"}
DEPS_VISION = {"cv2": "opencv-python", "numpy": "numpy", "PIL": "pillow"}

PILLBOX_PORT = 5001


def _pausa_si_hay_terminal():
    """Cuando se ejecuta con doble clic / Geany, deja leer el error antes de
    cerrar la ventana."""
    try:
        if sys.stdin and sys.stdin.isatty():
            input("\nPresiona Enter para salir...")
    except Exception:
        pass


def _faltan_modulos(mods):
    return {m: pip for m, pip in mods.items() if importlib.util.find_spec(m) is None}


def comprobar_archivos():
    faltan = [a for a in ARCHIVOS_REQUERIDOS
              if not os.path.exists(os.path.join(BASE, a))]
    if not faltan:
        return True
    print("ERROR: faltan archivos junto a main.py:")
    for a in faltan:
        print(f"   - {a}")
    print("\nLos nombres deben ser EXACTOS (ojo con espacios: 'medibot_serial .py'")
    print("con espacio NO sirve; debe llamarse 'medibot_serial.py').")
    print("Lo mas seguro es clonar el repositorio completo:")
    print("   git clone https://github.com/elporxdk/Proyects.git")
    return False


def reiniciar_hub(ms):
    """Apaga cualquier hub viejo en memoria y arranca uno con el codigo actual."""
    if ms.hub_running():
        print("Habia un hub serial corriendo: se apaga para usar el codigo actual...")
        ms.hub_shutdown()
        for _ in range(30):
            if not ms.hub_running():
                break
            time.sleep(0.1)
    if not ms.ensure_hub():
        print("AVISO: no se pudo iniciar el hub serial; las ordenes se simularan.")
        return False
    estado = ms.hub_status() or {}
    if estado.get("serial_open"):
        print(f"Arduino conectado en {estado.get('port')} @ {estado.get('baud')} baud")
    else:
        print("Arduino aun no detectado: el hub reintenta cada 5 s.")
        print("(Si conoces el puerto, arranca asi:  "
              "MEDIBOT_SERIAL_PORT=/dev/ttyACM0 python3 main.py)")
    return True


def _puerto_ocupado(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.4):
            return True
    except OSError:
        return False


def lanzar_pillbox():
    """Arranca Pastillero.py como proceso aparte. Devuelve el proceso, o None
    si ya habia un Pillbox corriendo (se reutiliza)."""
    if _puerto_ocupado(PILLBOX_PORT):
        print(f"Pillbox ya estaba corriendo en el puerto {PILLBOX_PORT}; se reutiliza.")
        return None
    proc = subprocess.Popen([sys.executable, os.path.join(BASE, "Pastillero.py")],
                            cwd=BASE)
    for _ in range(40):
        if _puerto_ocupado(PILLBOX_PORT):
            print(f"Pillbox listo:  http://<ip-de-la-pi>:{PILLBOX_PORT}")
            return proc
        if proc.poll() is not None:
            print("AVISO: Pillbox termino inesperadamente al arrancar; "
                  "revisa su salida ejecutando:  python3 Pastillero.py")
            return proc
        time.sleep(0.25)
    print("AVISO: Pillbox tarda en responder; sigue arrancando en segundo plano.")
    return proc


def ejecutar_vision():
    """Corre Medibot/Vision en ESTE proceso (tkinter necesita el hilo principal)."""
    print("Abriendo Medibot (Vision)...")
    runpy.run_path(os.path.join(BASE, "Vision_MEDIBOT.py"), run_name="__main__")


def main():
    os.chdir(BASE)
    if BASE not in sys.path:
        sys.path.insert(0, BASE)

    print("=" * 56)
    print("  MEDIBOT - arrancando todo el sistema")
    print("=" * 56)

    if not comprobar_archivos():
        _pausa_si_hay_terminal()
        return 1

    faltan = _faltan_modulos(DEPS_BASE)
    if faltan:
        print("ERROR: faltan librerias de Python:", ", ".join(faltan))
        print("Instala con:   pip install " + " ".join(sorted(set(faltan.values()))))
        _pausa_si_hay_terminal()
        return 1

    import medibot_serial as ms

    # 1) Hub serial fresco (con el codigo actual)
    reiniciar_hub(ms)

    # 2) Pillbox (web)
    pillbox = lanzar_pillbox()

    # 3) Medibot/Vision en el hilo principal
    faltan_vision = _faltan_modulos(DEPS_VISION)
    sin_tk = importlib.util.find_spec("tkinter") is None
    codigo_salida = 0
    try:
        if faltan_vision or sin_tk:
            print("\nAVISO: Medibot/Vision no puede arrancar todavia. Falta:")
            if faltan_vision:
                print("   pip install " + " ".join(sorted(set(faltan_vision.values()))))
            if sin_tk:
                print("   sudo apt install python3-tk")
            print(f"\nPillbox SI esta funcionando:  http://<ip-de-la-pi>:{PILLBOX_PORT}")
            print("Pulsa Ctrl+C para salir.")
            if pillbox is not None:
                pillbox.wait()
            else:
                while True:
                    time.sleep(3600)
        else:
            ejecutar_vision()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"\nERROR al ejecutar Medibot/Vision: {e}")
        codigo_salida = 1
    finally:
        print("\nCerrando MEDIBOT...")
        # Seguridad: detener el chasis por si quedo una orden de movimiento activa
        try:
            ms.send_command("GPIO,CLEANUP,0")
        except Exception:
            pass
        if pillbox is not None and pillbox.poll() is None:
            pillbox.terminate()
            try:
                pillbox.wait(timeout=5)
            except Exception:
                pillbox.kill()
        print("Listo. (El hub serial queda en memoria y se reutiliza o se "
              "renueva solo en el proximo arranque.)")
    if codigo_salida:
        _pausa_si_hay_terminal()
    return codigo_salida


if __name__ == "__main__":
    sys.exit(main())
