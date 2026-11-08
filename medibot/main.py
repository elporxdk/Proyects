#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MEDIBOT - Lanzador unico
========================
Ejecuta SOLO este archivo y arranca todo el sistema, en orden y coexistiendo:

  1. Hub serial (serial_hub.py): el unico dueno del puerto COM/USB del
     Arduino. Si habia un hub viejo corriendo, lo apaga y arranca uno con el
     codigo actual (evita quedarse con una version vieja en memoria).
  2. Pillbox (Pastillero.py): interfaz web en  http://<ip>:5001
  3. Medibot / Vision (Vision_MEDIBOT.py): interfaz de camaras y movimiento.

REGLA DE ORO (disponibilidad del pastillero): el servidor web de Pillbox
NUNCA se cae por culpa de Vision. Si Vision falta, le faltan librerias, no
hay entorno grafico (SSH sin DISPLAY) o truena al arrancar, Pillbox sigue
sirviendo la web igual. Solo se cierra todo cuando el usuario cierra la
ventana de Medibot normalmente o pulsa Ctrl+C.

Uso:
    python3 medibot/main.py

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

# NUCLEO (obligatorio): sin estos archivos no hay hub ni web del pastillero.
ARCHIVOS_NUCLEO = (
    "medibot_serial.py",
    "medibot_red.py",
    "serial_hub.py",
    "Pastillero.py",
)
# Vision es OPCIONAL: si falta (o no puede arrancar), Pillbox funciona igual.
ARCHIVO_VISION = "Vision_MEDIBOT.py"

# modulo de python -> nombre del paquete en pip
DEPS_BASE = {"flask": "flask", "serial": "pyserial"}
DEPS_VISION = {"cv2": "opencv-python", "numpy": "numpy", "PIL": "pillow"}

PILLBOX_PORT = 5001


# ================= utilidades =================

def urls_pillbox():
    """Todas las direcciones por las que se puede abrir Pillbox desde otro
    dispositivo. Ver medibot_red.py: antes se adivinaba UNA sola IP y, sin
    salida a internet, salia '127.0.0.1' (solo valida en esta maquina)."""
    import medibot_red
    return medibot_red.texto_urls(PILLBOX_PORT)


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


def _puerto_ocupado(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.4):
            return True
    except OSError:
        return False


# ================= comprobaciones =================

def comprobar_nucleo():
    """Verifica SOLO lo imprescindible para el hub + la web del pastillero."""
    faltan = [a for a in ARCHIVOS_NUCLEO
              if not os.path.exists(os.path.join(BASE, a))]
    if faltan:
        print("ERROR: faltan archivos junto a main.py:")
        for a in faltan:
            print(f"   - {a}")
        print("\nLos nombres deben ser EXACTOS (ojo con espacios: 'medibot_serial .py'")
        print("con espacio NO sirve; debe llamarse 'medibot_serial.py').")
        print("Lo mas seguro es clonar el repositorio completo:")
        print("   git clone -b main https://github.com/elporxdk/Proyects.git")
        print("(sin '-b main' se baja la rama 'web', que no trae medibot/)")
        return False
    faltan_libs = _faltan_modulos(DEPS_BASE)
    if faltan_libs:
        print("ERROR: faltan librerias de Python:", ", ".join(faltan_libs))
        print("En la Raspberry Pi:   bash instalar.sh   (lo instala todo con apt)")
        print("En otro equipo:       pip install " + " ".join(sorted(set(faltan_libs.values()))))
        return False
    return True


def diagnostico_vision():
    """Devuelve None si Vision puede arrancar; si no, el motivo (texto).
    Se comprueba TODO por adelantado para no descubrir el fallo a mitad
    de arranque (archivo, librerias, tkinter y entorno grafico)."""
    if not os.path.exists(os.path.join(BASE, ARCHIVO_VISION)):
        return f"falta el archivo {ARCHIVO_VISION}"
    faltan = _faltan_modulos(DEPS_VISION)
    if faltan:
        return ("faltan librerias. En la Raspberry Pi:  bash instalar.sh  "
                "(lo instala todo con apt). En otro equipo:  pip install "
                + " ".join(sorted(set(faltan.values()))))
    if importlib.util.find_spec("tkinter") is None:
        return "falta tkinter:  sudo apt install python3-tk"
    # En Linux, sin servidor grafico (SSH puro, arranque sin escritorio),
    # tkinter muere con TclError al crear la ventana. Detectarlo ANTES.
    if sys.platform.startswith("linux") and not (
            os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return ("no hay entorno grafico (variable DISPLAY vacia). "
                "Si entras por SSH usa 'ssh -X' o ejecuta en el escritorio de la Pi")
    return None


# ================= arranque de cada pieza =================

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
        print("Arduino aun no detectado: el hub reintenta cada 2 s.")
        print("(Si conoces el puerto, arranca asi:  "
              "MEDIBOT_SERIAL_PORT=/dev/ttyACM0 python3 medibot/main.py)")
    return True


def lanzar_pillbox():
    """Arranca Pastillero.py como proceso aparte. Devuelve el proceso, o None
    si ya habia un Pillbox corriendo (se reutiliza)."""
    if _puerto_ocupado(PILLBOX_PORT):
        print(f"Pillbox ya estaba corriendo en el puerto {PILLBOX_PORT}; se reutiliza.")
        print("Pillbox:")
        print(urls_pillbox())
        return None
    proc = subprocess.Popen([sys.executable, os.path.join(BASE, "Pastillero.py")],
                            cwd=BASE)
    for _ in range(40):
        if _puerto_ocupado(PILLBOX_PORT):
            print("Pillbox listo. Entra desde el movil u otro PC de la misma red:")
            print(urls_pillbox())
            return proc
        if proc.poll() is not None:
            print("AVISO: Pillbox termino inesperadamente al arrancar; "
                  "revisa su salida ejecutando:  python3 medibot/Pastillero.py")
            return proc
        time.sleep(0.25)
    print("AVISO: Pillbox tarda en responder; sigue arrancando en segundo plano.")
    return proc


def ejecutar_vision():
    """Corre Medibot/Vision en ESTE proceso (tkinter necesita el hilo principal).

    Devuelve True si Vision LLEGO A CORRER y termino con normalidad (el
    usuario cerro la ventana): en ese caso toca apagar todo el sistema.
    Devuelve False si TRONO al arrancar: en ese caso Pillbox debe SEGUIR
    VIVO (el error de Vision no puede tumbar la web del pastillero)."""
    print("Abriendo Medibot (Vision)...")
    try:
        runpy.run_path(os.path.join(BASE, ARCHIVO_VISION), run_name="__main__")
        return True
    except KeyboardInterrupt:
        return True
    except Exception as e:
        print(f"\nERROR al ejecutar Medibot/Vision: {e}")
        print("Vision queda desactivado, pero el pastillero SIGUE funcionando.")
        return False


def esperar_pillbox(pillbox):
    """Modo 'solo pastillero': mantiene el proceso vivo sirviendo la web
    hasta que el usuario pulse Ctrl+C (o Pillbox termine)."""
    print("\nPillbox SI esta funcionando:")
    print(urls_pillbox())
    print("Pulsa Ctrl+C para salir.")
    try:
        if pillbox is not None:
            pillbox.wait()
        else:
            while True:
                time.sleep(3600)
    except KeyboardInterrupt:
        pass


def apagar_todo(ms, pillbox):
    """Cierre ordenado: STOP al chasis (seguridad) y cierre de Pillbox."""
    print("\nCerrando MEDIBOT...")
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


# ================= orquestacion =================

def main():
    os.chdir(BASE)
    if BASE not in sys.path:
        sys.path.insert(0, BASE)

    print("=" * 56)
    print("  MEDIBOT - arrancando todo el sistema")
    print("=" * 56)

    # El NUCLEO (hub + web del pastillero) es lo unico obligatorio.
    if not comprobar_nucleo():
        _pausa_si_hay_terminal()
        return 1

    import medibot_serial as ms

    # 1) Hub serial fresco (con el codigo actual)
    reiniciar_hub(ms)

    # 2) Pillbox (web) - arranca SIEMPRE, pase lo que pase con Vision
    pillbox = lanzar_pillbox()

    # 3) Medibot/Vision: opcional. Se diagnostica ANTES de intentar arrancar
    #    y, si aun asi truena, Pillbox sigue sirviendo la web.
    motivo = diagnostico_vision()
    if motivo is not None:
        print(f"\nAVISO: Medibot/Vision no puede arrancar: {motivo}")
        esperar_pillbox(pillbox)
    else:
        vision_termino_bien = ejecutar_vision()
        if not vision_termino_bien:
            # Vision trono al arrancar: NO tumbar el pastillero.
            esperar_pillbox(pillbox)

    apagar_todo(ms, pillbox)
    return 0


if __name__ == "__main__":
    sys.exit(main())
