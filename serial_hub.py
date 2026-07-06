#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hub serial MEDIBOT
==================
QUE ES: el UNICO programa que abre el puerto serie (COM/USB) del Arduino.

POR QUE EXISTE: dos programas (Medibot/Vision y Pillbox) no pueden abrir el
mismo puerto COM a la vez — el segundo falla con "puerto ocupado". El hub abre
el puerto UNA sola vez, y Vision y Pillbox le mandan sus ordenes por TCP local
(127.0.0.1:5055). El hub las escribe al Arduino en orden y devuelve la
respuesta. Asi ambos "hablan por COM" sin pelearse.

    Vision  ----TCP----+
                       +---> serial_hub ---USB/COM---> Arduino
    Pillbox ----TCP----+

COMO SE USA: no hace falta arrancarlo a mano; Vision y Pillbox lo autolanzan
(medibot_serial.ensure_hub()). Tambien puede correrse solo:
    python3 serial_hub.py

PUERTO SERIE:
  - Por defecto AUTODETECTA el Arduino (ttyUSB*/ttyACM* en la Pi, COMx en
    Windows).
  - Para FIJARLO a mano, define la variable de entorno MEDIBOT_SERIAL_PORT:
        MEDIBOT_SERIAL_PORT=/dev/ttyUSB0 python3 Pastillero.py
  - Si no hay Arduino al arrancar (o se desconecta), el hub REINTENTA
    conectarse cada 5 segundos, no hace falta reiniciar nada.

PROTOCOLO cliente <-> hub (una linea JSON por peticion):
  Comando al Arduino:
   ->  {"cmd": "DISPENSE,3", "wait": 12.0, "until": ["DISPENSADO", "ERR"]}
   <-  {"ok": true, "lines": ["POS,3", "DISPENSADO,3"]}
       ok=true SOLO si el comando se escribio en un Arduino real.
  Estado real de la conexion:
   ->  {"op": "status"}
   <-  {"ok": true, "serial_open": true, "port": "/dev/ttyUSB0", "baud": 9600}
  Forzar reconexion ahora:
   ->  {"op": "reconnect"}   <- responde igual que status

Requisitos: pip install pyserial
"""

import json
import os
import socket
import threading
import time

HOST = "127.0.0.1"
PORT = 5055

# Puerto serie: fija MEDIBOT_SERIAL_PORT para elegirlo a mano; vacio = autodetectar
SERIAL_PORT = os.environ.get("MEDIBOT_SERIAL_PORT") or None
SERIAL_BAUD = int(os.environ.get("MEDIBOT_SERIAL_BAUD", "9600"))
RETRY_SECONDS = 5        # cada cuanto reintenta conectar si no hay Arduino

_serial_conn = None
_serial_lock = threading.Lock()
_port_name = None


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


def serial_open():
    """True si hay un Arduino realmente conectado y el puerto esta abierto."""
    return _serial_conn is not None


def serial_connect():
    """Intenta abrir el puerto serie. Devuelve True si quedo conectado."""
    global _serial_conn, _port_name
    if serial_open():
        return True
    try:
        import serial
    except Exception as e:
        print(f"HUB: pyserial NO instalado ({e}). Instala con: pip install pyserial")
        return False
    port = SERIAL_PORT or _autodetect_serial_port()
    if not port:
        print("HUB: no se detecto ningun Arduino "
              "(fija MEDIBOT_SERIAL_PORT=/dev/ttyUSB0 si conoces el puerto).")
        return False
    try:
        conn = serial.Serial(port, SERIAL_BAUD, timeout=0.2)
        time.sleep(2.0)   # el Arduino se reinicia al abrir el puerto; esperar boot
        with _serial_lock:
            _serial_conn = conn
            _port_name = port
        print(f"HUB: Arduino CONECTADO en {port} @ {SERIAL_BAUD} baud")
        return True
    except Exception as e:
        print(f"HUB: no se pudo abrir {port}: {e}")
        return False


def _marcar_desconectado(motivo):
    """Cierra el puerto tras un error; el monitor reintentara solo."""
    global _serial_conn
    conn, _serial_conn = _serial_conn, None
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
        print(f"HUB: Arduino desconectado ({motivo}); reintentando cada {RETRY_SECONDS} s")


def _monitor_reconexion():
    """Hilo: si no hay Arduino, reintenta conectar cada RETRY_SECONDS."""
    while True:
        if not serial_open():
            serial_connect()
        time.sleep(RETRY_SECONDS)


def procesar(cmd, wait, until):
    """Escribe cmd en el Arduino y recolecta lineas de respuesta.
    Devuelve (enviado_real, lineas)."""
    with _serial_lock:
        if _serial_conn is None:
            print(f"[HUB sin Arduino] {cmd}")
            return False, [f"SIN_ARDUINO: {cmd}"]
        try:
            _serial_conn.reset_input_buffer()
            _serial_conn.write((cmd + "\n").encode())
            _serial_conn.flush()
            lineas = []
            if not until:
                # Comando "fire and forget" (movimiento / servos): no bloquear
                # esperando respuesta; solo drenar lo que ya haya llegado.
                time.sleep(0.01)
                while _serial_conn.in_waiting:
                    linea = _serial_conn.readline().decode(errors="ignore").strip()
                    if linea:
                        lineas.append(linea)
                return True, lineas
            t0 = time.time()
            while time.time() - t0 < wait:
                linea = _serial_conn.readline().decode(errors="ignore").strip()
                if linea:
                    lineas.append(linea)
                    if any(linea.startswith(u) for u in until):
                        break
            return True, lineas
        except Exception as e:
            _marcar_desconectado(str(e))
            return False, [f"ERROR serial: {e}"]


def _respuesta_status():
    return {"ok": True, "serial_open": serial_open(),
            "port": _port_name if serial_open() else None, "baud": SERIAL_BAUD}


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
                except Exception:
                    conn.sendall(b'{"ok": false, "lines": ["ERROR: json invalido"]}\n')
                    continue

                op = str(req.get("op", "")).strip().lower()
                if op == "status":
                    conn.sendall((json.dumps(_respuesta_status()) + "\n").encode())
                    continue
                if op == "reconnect":
                    serial_connect()
                    conn.sendall((json.dumps(_respuesta_status()) + "\n").encode())
                    continue

                cmd = str(req.get("cmd", "")).strip()
                try:
                    wait = float(req.get("wait", 0.3))
                except (TypeError, ValueError):
                    wait = 0.3
                until = req.get("until") or []
                if not cmd:
                    conn.sendall(b'{"ok": false, "lines": []}\n')
                    continue
                enviado, lineas = procesar(cmd, wait, until)
                conn.sendall((json.dumps({"ok": enviado, "lines": lineas}) + "\n").encode())
    except Exception:
        pass
    finally:
        conn.close()


def main():
    serial_connect()
    threading.Thread(target=_monitor_reconexion, daemon=True).start()
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind((HOST, PORT))
    except OSError as e:
        print(f"HUB: no se pudo enlazar {HOST}:{PORT} ({e}). "
              "Puede que ya haya un hub corriendo. Saliendo.")
        return
    srv.listen(8)
    print(f"HUB serial escuchando en {HOST}:{PORT} "
          f"(Arduino: {'conectado en ' + str(_port_name) if serial_open() else 'buscando...'})")
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
