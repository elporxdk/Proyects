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

_serial_conn = None
# RLock: permite que un comando en curso reconecte el puerto sin soltarlo
# (reintento inmediato tras una microcaida del USB).
_serial_lock = threading.RLock()
_port_name = None
_aviso_sin_puerto = False   # para no repetir el mismo aviso en cada reintento


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
    best = ports[0]
    # NO caer en un puerto no deseado (p.ej. el UART interno de la Pi ttyS0/
    # ttyAMA0): si ninguno parece un Arduino, mejor devolver None y avisar,
    # antes que "conectar" a un puerto que nunca respondera.
    if score(best) <= 0:
        return None
    return best.device


def serial_open():
    """True si hay un Arduino realmente conectado y el puerto esta abierto."""
    return _serial_conn is not None


def serial_connect():
    """Intenta abrir el puerto serie. Devuelve True si quedo conectado."""
    global _serial_conn, _port_name, _aviso_sin_puerto
    if serial_open():
        return True
    try:
        import serial
    except Exception as e:
        print(f"HUB: pyserial NO instalado ({e}). Instala con: pip install pyserial")
        return False
    port = SERIAL_PORT or _autodetect_serial_port()
    if not port:
        if not _aviso_sin_puerto:
            print("HUB: no se detecto ningun Arduino; sigo buscando cada "
                  f"{RETRY_SECONDS} s (fija MEDIBOT_SERIAL_PORT=/dev/ttyUSB0 "
                  "si conoces el puerto).")
            _aviso_sin_puerto = True
        return False
    try:
        conn = serial.Serial(port, SERIAL_BAUD, timeout=SERIAL_TIMEOUT)
        time.sleep(2.0)   # el Arduino se reinicia al abrir el puerto; esperar boot
        with _serial_lock:
            _serial_conn = conn
            _port_name = port

        return True
    except Exception as e:
        if not _aviso_sin_puerto:
            print(f"HUB: no se pudo abrir {port}: {e}")
            _aviso_sin_puerto = True
        return False


def _marcar_desconectado(motivo):
    """Cierra el puerto tras un error; se reintenta reconectar enseguida."""
    global _serial_conn
    conn, _serial_conn = _serial_conn, None
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
        print(f"HUB: Arduino desconectado ({motivo}); reconectando...")


def _monitor_reconexion():
    """Hilo: si no hay Arduino, reintenta conectar cada RETRY_SECONDS.
    Con 2 s, una microcaida del USB (p.ej. bajon de tension al mover el
    motor) se recupera sola casi al instante, sin tocar ningun boton."""
    while True:
        if not serial_open():
            serial_connect()
        time.sleep(RETRY_SECONDS)


def procesar(cmd, wait, until):
    """Escribe cmd en el Arduino y recolecta lineas de respuesta.
    Devuelve (enviado_real, lineas).

    JUSTICIA: los comandos "fire and forget" (movimiento/servos de Vision, sin
    'until') NO bloquean el puerto. Si esta ocupado (p.ej. dispensando o hay un
    comando con respuesta en curso), se DESCARTAN en vez de encolarse. Asi un
    DISPENSE nunca se queda sin turno aunque Vision inunde el hub de comandos.
    Los comandos con respuesta (DISPENSE/GOTO) SI esperan su turno."""
    fire_and_forget = not until

    if fire_and_forget:
        if not _serial_lock.acquire(blocking=False):
            return True, []          # puerto ocupado: descartar el comando transitorio
    else:
        _serial_lock.acquire()       # esperar turno para un comando con respuesta
    try:
        if _serial_conn is None:
            if not fire_and_forget:
                print(f"[HUB sin Arduino] {cmd}")
            return False, [f"SIN_ARDUINO: {cmd}"]
        try:
            _serial_conn.reset_input_buffer()
            _serial_conn.write((cmd + "\n").encode())
            _serial_conn.flush()
            lineas = []
            if fire_and_forget:
                # No esperar respuesta; solo drenar lo que ya haya llegado.
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
            print(f"[HUB] {cmd} -> {lineas} ({time.time()-t0:.1f}s)")
            return True, lineas
        except Exception as e:
            _marcar_desconectado(str(e))
            # REINTENTO INMEDIATO en la misma peticion, solo para comandos
            # seguros de repetir (GOTO/HOME/GETPOS). Cubre las microcaidas del
            # USB al arrancar el motor: se reconecta y reenvia sin que el
            # usuario toque nada. DISPENSE NO se reintenta (riesgo de doble
            # dosis); para el, el monitor reconecta en ~2 s y el usuario ve el
            # aviso honesto.
            base = cmd.split(",")[0].strip().upper()
            if until and base in ("GOTO", "HOME", "GETPOS") and serial_connect():
                try:
                    _serial_conn.reset_input_buffer()
                    _serial_conn.write((cmd + "\n").encode())
                    _serial_conn.flush()
                    lineas = []
                    t0 = time.time()
                    while time.time() - t0 < wait:
                        linea = _serial_conn.readline().decode(errors="ignore").strip()
                        if linea:
                            lineas.append(linea)
                            if any(linea.startswith(u) for u in until):
                                break
                    print(f"[HUB] {cmd} (reintento tras reconexion) -> {lineas}")
                    return True, lineas
                except Exception as e2:
                    _marcar_desconectado(str(e2))
                    return False, [f"ERROR serial: {e2}"]
            return False, [f"ERROR serial: {e}"]
    finally:
        _serial_lock.release()


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
                if op == "shutdown":
                    # Permite a main.py apagar un hub viejo que quedo en
                    # memoria y relanzar uno con el codigo actual.
                    conn.sendall(b'{"ok": true, "bye": true}\n')
                    print("HUB: apagado a peticion de un cliente (relanzo con codigo nuevo).")
                    os._exit(0)

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
