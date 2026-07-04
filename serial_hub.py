#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hub serial MEDIBOT
==================
Unico proceso dueno del puerto COM/serie del Arduino. Vision y Pillbox
(Pastillero) NO abren el puerto directamente: se conectan por TCP a este hub
(127.0.0.1:5055) y le mandan sus comandos; el hub los reenvia al Arduino por el
UNICO puerto serie y devuelve la respuesta.

Asi ambos programas pueden "hablar" por COM al mismo tiempo sin pelearse por el
puerto (dos procesos no pueden abrir el mismo COM a la vez).

Protocolo cliente <-> hub (una linea JSON por peticion):
   ->  {"cmd": "DISPENSE,3", "wait": 12.0, "until": ["DISPENSADO", "ERR"]}
   <-  {"ok": true, "lines": ["POS,3", "DISPENSADO,7"]}

Ejecutar:   python3 serial_hub.py     (tambien se autolanza desde Vision/Pillbox)
Requisitos: pip install pyserial
"""

import json
import socket
import threading
import time

HOST = "127.0.0.1"
PORT = 5055

SERIAL_PORT = None      # None = autodetectar; o fija "COM3" (Windows) / "/dev/ttyUSB0"
SERIAL_BAUD = 9600      # DEBE coincidir con Serial.begin() del firmware

_serial_conn = None
_serial_lock = threading.Lock()


def _autodetect_serial_port():
    """Elige el puerto del Arduino priorizando por nombre/descripcion."""
    try:
        from serial.tools import list_ports
    except Exception:
        return None
    ports = list(list_ports.comports())
    if not ports:
        return None

    def score(p):
        dev = (p.device or "").lower()
        desc = ((p.description or "") + " " + (getattr(p, "manufacturer", "") or "")).lower()
        s = 0
        if any(k in desc for k in ("arduino", "ch340", "ch910", "cp210", "ftdi", "usb-serial", "wch")):
            s += 100
        if "ttyacm" in dev:
            s += 40
        elif "ttyusb" in dev:
            s += 35
        elif dev.startswith("com"):
            s += 30
        if "ttyama" in dev or "ttys0" in dev or "serial0" in dev:
            s -= 50   # descartar UART interno de la Raspberry
        return s

    ports.sort(key=score, reverse=True)
    return ports[0].device


def serial_connect():
    """Abre el unico puerto serie (autodetecta si SERIAL_PORT es None)."""
    global _serial_conn
    try:
        import serial
    except Exception as e:
        print(f"HUB: pyserial no instalado ({e}). Modo SIMULADO (sin Arduino).")
        return None
    port = SERIAL_PORT or _autodetect_serial_port()
    if not port:
        print("HUB: no se detecto ningun Arduino. Modo SIMULADO.")
        return None
    try:
        _serial_conn = serial.Serial(port, SERIAL_BAUD, timeout=0.2)
        time.sleep(2.0)   # el Arduino se reinicia al abrir el puerto; esperar boot
        print(f"HUB: Arduino conectado en {port} @ {SERIAL_BAUD} baud")
    except Exception as e:
        print(f"HUB: no se pudo abrir {port}: {e}. Modo SIMULADO.")
        _serial_conn = None
    return _serial_conn


def procesar(cmd, wait, until):
    """Envia cmd al Arduino y recolecta lineas de respuesta hasta 'until' o 'wait'."""
    with _serial_lock:
        if _serial_conn is None:
            print(f"[HUB SIMULADO] {cmd}")
            return [f"(sin arduino) {cmd}"]
        try:
            _serial_conn.reset_input_buffer()
            _serial_conn.write((cmd + "\n").encode())
            lineas = []
            if not until:
                # Comando "fire and forget" (movimiento / servos): no bloquear
                # esperando respuesta; solo drenar lo que ya haya llegado.
                time.sleep(0.01)
                while _serial_conn.in_waiting:
                    linea = _serial_conn.readline().decode(errors="ignore").strip()
                    if linea:
                        lineas.append(linea)
                return lineas
            t0 = time.time()
            while time.time() - t0 < wait:
                linea = _serial_conn.readline().decode(errors="ignore").strip()
                if linea:
                    lineas.append(linea)
                    if any(linea.startswith(u) for u in until):
                        break
            return lineas
        except Exception as e:
            return [f"ERROR serial: {e}"]


def handle_client(conn, addr):
    conn.settimeout(120)
    buf = b""
    try:
        while True:
            data = conn.recv(4096)
            if not data:
                break
            buf += data
            while b"\n" in buf:
                linea, buf = buf.split(b"\n", 1)
                if not linea.strip():
                    continue
                try:
                    req = json.loads(linea.decode(errors="ignore"))
                    cmd = str(req.get("cmd", "")).strip()
                    wait = float(req.get("wait", 0.3))
                    until = req.get("until") or []
                except Exception:
                    conn.sendall(b'{"ok": false, "lines": ["ERROR: json invalido"]}\n')
                    continue
                if not cmd:
                    conn.sendall(b'{"ok": false, "lines": []}\n')
                    continue
                lineas = procesar(cmd, wait, until)
                conn.sendall((json.dumps({"ok": True, "lines": lineas}) + "\n").encode())
    except Exception:
        pass
    finally:
        conn.close()


def main():
    serial_connect()
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind((HOST, PORT))
    except OSError as e:
        print(f"HUB: no se pudo enlazar {HOST}:{PORT} ({e}). "
              "Puede que ya haya un hub corriendo. Saliendo.")
        return
    srv.listen(8)
    print(f"HUB serial escuchando en {HOST}:{PORT}")
    try:
        while True:
            conn, addr = srv.accept()
            threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()
    except KeyboardInterrupt:
        print("HUB: cerrando.")
    finally:
        srv.close()


if __name__ == "__main__":
    main()
