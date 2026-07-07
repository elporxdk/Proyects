#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
medibot_serial: el "cartero" entre Vision/Pillbox y el Arduino.
================================================================
QUE HACE (en simple): Vision y Pillbox NO abren el puerto COM directamente
(dos programas no pueden abrir el mismo puerto a la vez). En su lugar, ambos
usan este modulo, que entrega cada orden al HUB serial (serial_hub.py) por
TCP local; el hub es el unico que habla con el Arduino por USB/COM.

    Vision  --- medibot_serial ---+
                                  +--TCP--> serial_hub --USB--> Arduino
    Pillbox --- medibot_serial ---+

FUNCIONES:
  ensure_hub()          Lanza serial_hub.py si no esta corriendo (autoarranque).
  hub_running()         True si el hub esta escuchando (proceso vivo).
  hub_status()          Estado REAL: {"serial_open": bool, "port": ..., "baud": ...}
                        serial_open=True significa "hay un Arduino conectado".
  arduino_conectado()   Atajo: True solo si hay Arduino fisico conectado.
  hub_reconnect()       Pide al hub reintentar la conexion serie AHORA.
  send_command(cmd, wait, until)
                        Envia un comando (p.ej. "DISPENSE,3") y devuelve las
                        lineas que respondio el Arduino.

Para fijar el puerto a mano (si la autodeteccion falla), exporta antes de
arrancar Vision o Pillbox:
    MEDIBOT_SERIAL_PORT=/dev/ttyUSB0   (Pi)      MEDIBOT_SERIAL_PORT=COM3  (Windows)
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
    """True si el hub serial esta escuchando (el proceso esta vivo)."""
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


def _peticion(payload, timeout, _reintentar=True):
    """Envia un dict JSON al hub y devuelve el dict de respuesta (o None).
    Si el hub esta caido (conexion rechazada), intenta relanzarlo y reintenta
    una vez. Un timeout de lectura NO relanza (el hub esta vivo pero ocupado)."""
    try:
        with socket.create_connection((HUB_HOST, HUB_PORT), timeout=2.0) as s:
            s.sendall((json.dumps(payload) + "\n").encode())
            s.settimeout(timeout)
            buf = b""
            while b"\n" not in buf:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
            if not buf:
                return None
            return json.loads(buf.split(b"\n", 1)[0].decode(errors="ignore"))
    except ConnectionRefusedError:
        # El hub no esta escuchando: relanzarlo y reintentar una sola vez.
        if _reintentar and ensure_hub():
            return _peticion(payload, timeout, _reintentar=False)
        return None
    except Exception:
        return None


def hub_status():
    """Estado REAL de la conexion con el Arduino, preguntandole al hub.
    Devuelve {"serial_open": bool, "port": str|None, "baud": int} o None si
    el hub no responde."""
    return _peticion({"op": "status"}, timeout=3.0)


def arduino_conectado():
    """True SOLO si el hub tiene un Arduino fisico conectado por USB/COM."""
    s = hub_status()
    return bool(s and s.get("serial_open"))


def hub_reconnect():
    """Pide al hub que reintente abrir el puerto serie ahora mismo."""
    return _peticion({"op": "reconnect"}, timeout=8.0)


def hub_shutdown():
    """Apaga el hub si esta corriendo (para relanzarlo con codigo actualizado).
    No relanza nada si no habia hub."""
    return _peticion({"op": "shutdown"}, timeout=3.0, _reintentar=False)


def send_command(cmd, wait=0.3, until=None):
    """Envia un comando al Arduino a traves del hub y devuelve la lista de
    lineas de respuesta. Si el hub no responde, devuelve un aviso en la lista.
    El margen del timeout es amplio (wait + 15 s) para que un dispensado lento
    o con el puerto ocupado no de un falso 'sin respuesta'."""
    resp = _peticion({"cmd": cmd, "wait": wait, "until": until or []},
                     timeout=wait + 15.0)
    if resp is None:
        return [f"ERROR hub: sin respuesta ({cmd})"]
    return resp.get("lines", [])
