#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cliente del hub serial MEDIBOT (serial_hub.py).

Vision y Pillbox usan send_command() para mandar ordenes al Arduino a traves
del hub, en vez de abrir el puerto COM directamente. Asi ambos programas pueden
enviar sus comandos por el mismo puerto sin conflicto: el hub es el unico dueno
del COM y serializa el acceso.

Uso:
    import medibot_serial
    medibot_serial.ensure_hub()                     # lanza el hub si hace falta
    medibot_serial.send_command("MOVE,FWD")         # movimiento (sin espera)
    medibot_serial.send_command("DISPENSE,3", wait=12.0, until=["DISPENSADO", "ERR"])
"""

import json
import os
import socket
import subprocess
import sys
import time

HUB_HOST = "127.0.0.1"
HUB_PORT = 5055


def hub_running():
    """True si el hub serial esta escuchando."""
    try:
        with socket.create_connection((HUB_HOST, HUB_PORT), timeout=0.3):
            return True
    except OSError:
        return False


def ensure_hub():
    """Lanza serial_hub.py si no esta corriendo. True si el hub queda disponible."""
    if hub_running():
        return True
    base = os.path.dirname(os.path.abspath(__file__))
    script = os.path.join(base, "serial_hub.py")
    if not os.path.exists(script):
        print(f"MEDIBOT_SERIAL: no se encontro serial_hub.py en {base}")
        return False
    try:
        subprocess.Popen([sys.executable, script], cwd=base)
    except Exception as e:
        print(f"MEDIBOT_SERIAL: no se pudo lanzar el hub: {e}")
        return False
    for _ in range(50):   # esperar ~10 s a que el hub levante
        if hub_running():
            return True
        time.sleep(0.2)
    return False


def send_command(cmd, wait=0.3, until=None):
    """Envia un comando al hub y devuelve la lista de lineas de respuesta del
    Arduino. Si el hub no esta disponible, devuelve un aviso en la lista."""
    payload = json.dumps({"cmd": cmd, "wait": wait, "until": until or []}) + "\n"
    try:
        with socket.create_connection((HUB_HOST, HUB_PORT), timeout=2.0) as s:
            s.sendall(payload.encode())
            s.settimeout(wait + 3.0)
            buf = b""
            while b"\n" not in buf:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
            if not buf:
                return []
            resp = json.loads(buf.split(b"\n", 1)[0].decode(errors="ignore"))
            return resp.get("lines", [])
    except Exception as e:
        return [f"ERROR hub: {e}"]
