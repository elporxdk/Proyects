#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pillbox - Interfaz web + envio de ordenes al Arduino a traves del hub serial.

Funcionamiento de la ruleta:
  - Al ABRIR un compartimiento en la web, la ruleta lo coloca ARRIBA de la
    zona de dispensacion (GOTO = posicion de carga/espera).
  - Al DISPENSAR, la ruleta lo BAJA (media vuelta) hasta la zona de dispensado
    y el servo suelta la pastilla.

Persistencia (todo se guarda en la Pi, en pillbox_data.json junto al script):
  - Dosis/datos de paciente por compartimiento.
  - Horarios de dispensado automatico (hora + dias de la semana).
  - Historial de acciones (dispensados manuales/automaticos, guardados, etc.).
  El Arduino ademas recuerda en su EEPROM que compartimiento quedo arriba.

Horarios automaticos:
  - Un hilo revisa cada 15 s los horarios activos; cuando coinciden la hora
    (HH:MM) y el dia de la semana, dispensa solo y lo registra en el historial.

Puerto serial (COM):
  - Lo administra el HUB (serial_hub.py), unico dueno del puerto. Pillbox y
    Vision le envian sus ordenes por TCP, asi comparten el mismo Arduino.
  - El hub se autolanza si no esta corriendo.
Requisitos:  pip install flask pyserial
Ejecutar:    python3 Pastillero.py   ->   http://<ip>:5001
"""

import json
import os
import threading
import time
from datetime import datetime

from flask import Flask, request, jsonify

import medibot_serial   # cliente del hub serial compartido (serial_hub.py)

# ================= CONFIGURACION =================
SERIAL_BAUD = 9600       # informativo; el baud real lo fija el hub
N_COMPARTIMIENTOS = 8
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "pillbox_data.json")
MAX_HISTORIAL = 500      # entradas maximas guardadas
DIAS_NOMBRES = ["L", "M", "X", "J", "V", "S", "D"]   # weekday(): lunes=0


# ================= PERSISTENCIA EN LA PI =================
_data_lock = threading.RLock()


def _estado_vacio():
    return {
        "compartimientos": {},   # "1".."8" -> datos del paciente/dosis
        "horarios": [],          # {id, comp, hora "HH:MM", dias [0..6], activo, ultimo}
        "prox_id": 1,
        "historial": [],         # {ts, tipo, comp, detalle, resultado}
    }


def cargar_datos():
    with _data_lock:
        try:
            with open(DATA_FILE, encoding="utf-8") as f:
                datos = json.load(f)
        except (OSError, ValueError):
            return _estado_vacio()
        base = _estado_vacio()
        base.update({k: datos[k] for k in base if k in datos})
        return base


def guardar_datos(datos):
    """Escritura atomica: primero a un temporal y luego rename, para no
    corromper el archivo si se corta la luz a mitad de escritura."""
    with _data_lock:
        tmp = DATA_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(datos, f, ensure_ascii=False, indent=2)
        os.replace(tmp, DATA_FILE)


ESTADO = cargar_datos()


def registrar_historial(tipo, comp, detalle="", resultado=""):
    """Anota una accion en el historial persistente (lo mas nuevo primero)."""
    with _data_lock:
        ESTADO["historial"].insert(0, {
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "tipo": tipo,
            "comp": comp,
            "detalle": detalle,
            "resultado": resultado,
        })
        del ESTADO["historial"][MAX_HISTORIAL:]
        guardar_datos(ESTADO)


# ================= SERIAL (via hub) =================
def serial_connect():
    """Se asegura de que el hub serial (serial_hub.py) este corriendo. El hub es
    el unico dueno del puerto COM; Pillbox le envia los comandos por TCP."""
    ok = medibot_serial.ensure_hub()
    if ok:
        print(f"Hub serial disponible en {medibot_serial.HUB_HOST}:{medibot_serial.HUB_PORT}")
    else:
        print("AVISO: no se pudo iniciar el hub serial (serial_hub.py). "
              "Las ordenes se simularan.")
    return ok


def hub_disponible():
    """True SOLO si hay un Arduino fisico conectado (estado real via hub)."""
    return medibot_serial.arduino_conectado()


def serial_command(msg, wait=4.0, until=("DISPENSADO", "ERR")):
    """Envia una orden al Arduino a traves del hub serial y devuelve las
    respuestas (lista de lineas)."""
    return medibot_serial.send_command(msg, wait=wait, until=list(until))


# ================= SINCRONIZACION CON LA POSICION DEL ARDUINO =================
# El Arduino es la fuente de verdad de que compartimiento esta ARRIBA (lo
# recuerda en EEPROM). La web guarda:
#   - real:     lo que el Arduino REPORTA (respuestas POS,n / GETPOS)
#   - esperado: lo que la web COMANDO por ultima vez (GOTO n, HOME=1, tras
#               dispensar n -> el opuesto por el giro de 180)
# Sincronizado = real y esperado coinciden.
_pos_lock = threading.Lock()
_verif_lock = threading.Lock()   # evita GETPOS simultaneos
POS = {"real": None, "esperado": None, "ts": None}


def _parsear_pos(resp):
    """Extrae <n> de una respuesta 'POS,<n>'; None si no hay."""
    for l in resp or []:
        s = str(l)
        if s.startswith("POS,"):
            try:
                return int(s.split(",")[1])
            except (IndexError, ValueError):
                pass
    return None


def _set_pos(real=None, esperado=None):
    with _pos_lock:
        if real is not None:
            POS["real"] = real
        if esperado is not None:
            POS["esperado"] = esperado
        POS["ts"] = datetime.now().strftime("%H:%M:%S")


def _opuesto(comp):
    """Compartimiento que queda ARRIBA tras dispensar 'comp' (giro de 180)."""
    media = N_COMPARTIMIENTOS // 2
    return ((comp - 1 + media) % N_COMPARTIMIENTOS) + 1


def pos_estado(conectado=None):
    """Estado de sincronizacion (sin tocar el serial: usa lo ya conocido)."""
    if conectado is None:
        conectado = hub_disponible()
    with _pos_lock:
        r, e, ts = POS["real"], POS["esperado"], POS["ts"]
    sincronizado = (r is not None and e is not None and r == e)
    if not conectado:
        detalle = "Sin conexion con el Arduino"
    elif r is None:
        detalle = "Posicion del Arduino aun desconocida (pulsa Verificar)"
    elif e is None:
        detalle = f"Arduino en compartimiento {r}"
    elif sincronizado:
        detalle = f"Sincronizado en compartimiento {r}"
    else:
        detalle = f"Desincronizado: la web espera {e} y el Arduino esta en {r}"
    return {"real": r, "esperado": e, "sincronizado": sincronizado,
            "conectado": conectado, "detalle": detalle, "ts": ts}


def verificar_pos():
    """Pregunta al Arduino su posicion real (GETPOS) y actualiza POS['real'].
    Evita consultas simultaneas; si ya hay una en curso devuelve lo cacheado."""
    if not _verif_lock.acquire(blocking=False):
        return POS["real"]
    try:
        resp = serial_command("GETPOS", wait=3.0, until=("POS", "ERR"))
        real = _parsear_pos(resp)
        if real is not None:
            _set_pos(real=real)
        return real
    finally:
        _verif_lock.release()


def ejecutar_dispensado(comp, origen, detalle=""):
    """Dispensa el compartimiento 'comp' y registra la accion. Devuelve
    (ok, respuestas). Espera amplia: la ruleta solo avanza (nunca retrocede),
    asi que el peor caso es GOTO de 7 compartimientos + bajada 180 + servo
    ~ 11.5 s; se esperan 15 s de margen."""
    resp = serial_command(f"DISPENSE,{comp}", wait=15.0)
    conectado = hub_disponible()
    ack = " | ".join(resp) if resp else "sin respuesta"
    ok = conectado and any(str(l).startswith("DISPENSADO") for l in resp)
    registrar_historial(origen, comp, detalle,
                        "OK" if ok else ("SIMULADO" if not conectado else ack))
    if ok:
        # El firmware vuelve a HOME (compartimiento 1 arriba) tras dispensar.
        _set_pos(real=_parsear_pos(resp), esperado=1)
    return ok, resp


# ================= HORARIOS AUTOMATICOS =================
def revisar_horarios(ahora=None):
    """Revisa los horarios activos y dispensa los que coinciden con este
    minuto. Devuelve la lista de horarios disparados (para pruebas)."""
    ahora = ahora or datetime.now()
    clave_minuto = ahora.strftime("%Y-%m-%dT%H:%M")
    hhmm = ahora.strftime("%H:%M")
    dia = ahora.weekday()   # lunes=0 .. domingo=6
    disparados = []
    with _data_lock:
        pendientes = [h for h in ESTADO["horarios"]
                      if h.get("activo") and h.get("hora") == hhmm
                      and dia in h.get("dias", [])
                      and h.get("ultimo") != clave_minuto]
        for h in pendientes:
            h["ultimo"] = clave_minuto   # marca antes de disparar (evita dobles)
        if pendientes:
            guardar_datos(ESTADO)
    for h in pendientes:
        comp = h["comp"]
        datos = ESTADO["compartimientos"].get(str(comp), {})
        medic = datos.get("medicamento", "")
        print(f"HORARIO: dispensando comp {comp} ({medic}) programado a las {h['hora']}")
        ejecutar_dispensado(comp, "AUTO", f"{medic} (horario {h['hora']})".strip())
        disparados.append(h)
    return disparados


def bucle_horarios():
    """Hilo en segundo plano: revisa los horarios cada 15 segundos."""
    while True:
        try:
            revisar_horarios()
        except Exception as e:
            print(f"AVISO: error en el bucle de horarios: {e}")
        time.sleep(15)


# ================= SERVIDOR WEB =================
app = Flask(__name__)


@app.route("/")
def index():
    return HTML_PAGE


@app.route("/data")
def data():
    """Estado completo para la interfaz: dosis, horarios e historial."""
    with _data_lock:
        return jsonify({
            "compartimientos": ESTADO["compartimientos"],
            "horarios": ESTADO["horarios"],
            "historial": ESTADO["historial"][:50],
            "conectado": hub_disponible(),
        })


@app.route("/compartimiento/<int:comp>", methods=["POST", "DELETE"])
def compartimiento(comp):
    if not (1 <= comp <= N_COMPARTIMIENTOS):
        return jsonify({"ok": False, "message": "Compartimiento invalido"}), 400
    with _data_lock:
        if request.method == "DELETE":
            ESTADO["compartimientos"].pop(str(comp), None)
            # tambien se eliminan sus horarios
            ESTADO["horarios"] = [h for h in ESTADO["horarios"] if h["comp"] != comp]
            guardar_datos(ESTADO)
            registrar_historial("ELIMINAR", comp, "datos y horarios borrados")
            return jsonify({"ok": True})
        datos = request.get_json(silent=True) or {}
        # Registro conciso: medicamento y dosis obligatorios; el resto opcional
        registro = {k: str(datos.get(k, "")).strip() for k in
                    ("medicamento", "dosis", "nombre", "notas")}
        if not registro["medicamento"] or not registro["dosis"]:
            return jsonify({"ok": False,
                            "message": "Medicamento y dosis son obligatorios"}), 400
        registro["completado"] = True
        ESTADO["compartimientos"][str(comp)] = registro
        guardar_datos(ESTADO)
    registrar_historial("GUARDAR", comp,
                        f"{registro['medicamento']} {registro['dosis']}".strip())
    return jsonify({"ok": True, "compartimiento": comp})


@app.route("/horarios", methods=["POST"])
def horarios_add():
    datos = request.get_json(silent=True) or {}
    try:
        comp = int(datos.get("comp", 0))
        hora = str(datos.get("hora", "")).strip()
        dias = sorted({int(d) for d in datos.get("dias", []) if 0 <= int(d) <= 6})
    except (TypeError, ValueError):
        return jsonify({"ok": False, "message": "Datos invalidos"}), 400
    if not (1 <= comp <= N_COMPARTIMIENTOS):
        return jsonify({"ok": False, "message": "Compartimiento invalido"}), 400
    try:
        datetime.strptime(hora, "%H:%M")
    except ValueError:
        return jsonify({"ok": False, "message": "Hora invalida (usa HH:MM)"}), 400
    if not dias:
        return jsonify({"ok": False, "message": "Elige al menos un dia"}), 400
    with _data_lock:
        nuevo = {"id": ESTADO["prox_id"], "comp": comp, "hora": hora,
                 "dias": dias, "activo": True, "ultimo": ""}
        ESTADO["prox_id"] += 1
        ESTADO["horarios"].append(nuevo)
        guardar_datos(ESTADO)
    dias_txt = ",".join(DIAS_NOMBRES[d] for d in dias)
    registrar_historial("HORARIO", comp, f"programado {hora} ({dias_txt})")
    return jsonify({"ok": True, "horario": nuevo})


@app.route("/horarios/<int:hid>", methods=["PATCH", "DELETE"])
def horarios_mod(hid):
    with _data_lock:
        h = next((x for x in ESTADO["horarios"] if x["id"] == hid), None)
        if h is None:
            return jsonify({"ok": False, "message": "Horario no encontrado"}), 404
        if request.method == "DELETE":
            ESTADO["horarios"].remove(h)
            guardar_datos(ESTADO)
            registrar_historial("HORARIO", h["comp"], f"eliminado {h['hora']}")
            return jsonify({"ok": True})
        h["activo"] = not h.get("activo", True)
        guardar_datos(ESTADO)
    estado_txt = "activado" if h["activo"] else "pausado"
    registrar_historial("HORARIO", h["comp"], f"{estado_txt} {h['hora']}")
    return jsonify({"ok": True, "horario": h})


@app.route("/dispense", methods=["POST"])
def dispense():
    data_req = request.get_json(silent=True) or {}
    try:
        comp = int(data_req.get("compartimiento", 0))
    except (TypeError, ValueError):
        comp = 0
    if not (1 <= comp <= N_COMPARTIMIENTOS):
        return jsonify({"ok": False, "message": f"Compartimiento invalido: {comp}"}), 400

    medic = str(data_req.get("medicamento", "")).strip()
    nombre = str(data_req.get("nombre", "")).strip()

    ok, resp = ejecutar_dispensado(comp, "MANUAL", medic)
    conectado = hub_disponible()
    ack = " | ".join(resp) if resp else "sin respuesta"
    print(f"DISPENSAR comp={comp} medic='{medic}' paciente='{nombre}' -> Arduino: {ack}")

    if ok:
        # El Arduino confirmo con DISPENSADO
        msg = f"Dispensado en compartimiento {comp}"
        if medic:
            msg += f": {medic}"
        if nombre:
            msg += f" (paciente {nombre})"
    elif conectado:
        # El puerto esta abierto pero el Arduino NO confirmo el dispensado
        msg = (f"El puerto esta abierto pero el Arduino no confirmo el dispensado "
               f"(respuesta: {ack}). Revisa que sea el Arduino correcto, el firmware "
               f"y el cableado. Si el puerto es incorrecto, fija MEDIBOT_SERIAL_PORT.")
    else:
        msg = (f"No hay Arduino conectado. Orden simulada: DISPENSE,{comp}"
               f" ({medic or 'sin medicamento'}).")
    return jsonify({"ok": ok, "message": msg, "arduino": resp,
                    "compartimiento": comp, "pos": pos_estado(conectado)})


@app.route("/goto", methods=["POST"])
def goto():
    """Coloca el compartimiento ARRIBA de la zona de dispensacion (espera).
    Se llama al ABRIR un compartimiento en la web."""
    data_req = request.get_json(silent=True) or {}
    try:
        comp = int(data_req.get("compartimiento", 0))
    except (TypeError, ValueError):
        comp = 0
    if not (1 <= comp <= N_COMPARTIMIENTOS):
        return jsonify({"ok": False, "message": "Compartimiento invalido"}), 400
    resp = serial_command(f"GOTO,{comp}", wait=8.0, until=("POS", "ERR"))
    _set_pos(real=_parsear_pos(resp), esperado=comp)
    return jsonify({"ok": hub_disponible(), "arduino": resp,
                    "compartimiento": comp, "pos": pos_estado()})


@app.route("/home", methods=["POST"])
def home():
    """Devuelve la ruleta a su posicion de origen (compartimiento 1).
    Se llama al CERRAR el detalle de un compartimiento."""
    resp = serial_command("HOME", wait=8.0, until=("POS", "ERR"))
    _set_pos(real=_parsear_pos(resp), esperado=1)
    return jsonify({"ok": hub_disponible(), "arduino": resp, "pos": pos_estado()})


@app.route("/arduino/estado")
def arduino_estado():
    """Consulta la posicion REAL del Arduino (GETPOS) y la compara con la
    esperada por la web: dice con detalle si esta sincronizado."""
    verificar_pos()
    return jsonify(pos_estado())


@app.route("/serial/status")
def serial_status():
    """Estado REAL: 'conectado' es true solo si hay un Arduino fisico abierto.
    Incluye la sincronizacion de posicion (cacheada, sin tocar el serial)."""
    s = medibot_serial.hub_status()
    conectado = bool(s and s.get("serial_open"))
    return jsonify({"conectado": conectado,
                    "puerto": s.get("port") if s else None,
                    "baud": s.get("baud", SERIAL_BAUD) if s else SERIAL_BAUD,
                    "hub": s is not None,
                    "pos": pos_estado(conectado)})


@app.route("/serial/reconnect", methods=["POST"])
def serial_reconnect():
    """Relanza el hub si murio y le pide reintentar el puerto serie AHORA."""
    medibot_serial.ensure_hub()
    medibot_serial.hub_reconnect()
    return serial_status()


# ================= HTML =================
HTML_PAGE = """<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Pillbox</title>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; font-family: 'Segoe UI', Roboto, system-ui, sans-serif; }
    body { background: #f4f7fc; min-height: 100vh; display: flex; justify-content: center; align-items: flex-start; padding: 20px; }
    .app { max-width: 1200px; width: 100%; background: white; border-radius: 32px; box-shadow: 0 20px 60px rgba(0,20,40,0.12); padding: 30px 35px 40px; transition: all .3s ease; }
    .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 28px; flex-wrap: wrap; gap: 10px; }
    .header h1 { font-size: 26px; font-weight: 600; color: #0b2b4a; letter-spacing: -.3px; display: flex; align-items: center; gap: 8px; }
    .header h1 span { background: #eef3f9; padding: 4px 14px; border-radius: 40px; font-size: 16px; font-weight: 500; color: #1f5a8e; }
    .serial-pill { font-size: 13px; font-weight: 600; padding: 6px 14px; border-radius: 30px; background: #fdecea; color: #b3323d; }
    .serial-pill.ok { background: #d4edda; color: #0e6b3e; }
    .serial-pill.sync { background: #d4edda; color: #0e6b3e; }
    .serial-pill.desync { background: #fdf0d5; color: #8a5a00; }
    .serial-pill.neutro { background: #eef3f9; color: #5a728c; }
    .btn-back { background: #eef3f9; border: none; padding: 8px 18px; border-radius: 30px; font-size: 14px; font-weight: 500; color: #1f5a8e; cursor: pointer; display: flex; align-items: center; gap: 6px; transition: .2s; }
    .btn-back:hover { background: #dce6f0; }
    .btn-back.hidden { display: none; }
    .compartments-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(210px,1fr)); gap: 22px; margin-top: 10px; }
    .compartment-card { background: #fff; border-radius: 20px; padding: 18px 16px 16px; box-shadow: 0 4px 16px rgba(0,0,0,.04); border: 1px solid #e9edf4; cursor: pointer; transition: all .2s ease; display: flex; flex-direction: column; min-height: 150px; position: relative; }
    .compartment-card:hover { transform: translateY(-4px); box-shadow: 0 12px 28px rgba(0,40,80,.08); border-color: #b6cae0; }
    .compartment-number { font-size: 18px; font-weight: 700; color: #1f3a57; letter-spacing: -.2px; margin-bottom: 10px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 4px; }
    .compartment-number .badge { font-size: 12px; font-weight: 500; background: #dff0fa; color: #1f5a8e; padding: 2px 12px; border-radius: 30px; letter-spacing: .3px; }
    .compartment-number .badge.completed { background: #d4edda; color: #0e6b3e; }
    .preview-data { font-size: 14px; color: #1f3a57; line-height: 1.5; margin-top: 4px; flex: 1; }
    .preview-data .label { color: #6f8aa8; font-weight: 400; font-size: 12px; text-transform: uppercase; letter-spacing: .3px; }
    .preview-data .value { font-weight: 500; word-break: break-word; }
    .preview-empty { color: #9bb0c7; font-size: 14px; margin-top: 6px; font-style: italic; }
    .detail-view { display: none; animation: fadeIn .25s ease; }
    .detail-view.active { display: block; }
    @keyframes fadeIn { from { opacity: 0; transform: translateY(8px);} to { opacity: 1; transform: translateY(0);} }
    .detail-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; flex-wrap: wrap; gap: 10px; }
    .detail-header h2 { font-size: 24px; font-weight: 600; color: #0b2b4a; }
    .detail-header .sub { font-size: 15px; color: #6f8aa8; }
    .form-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px,1fr)); gap: 18px 22px; background: #f9fbfd; padding: 24px 26px; border-radius: 24px; border: 1px solid #e9edf4; margin-bottom: 20px; }
    .form-group { display: flex; flex-direction: column; gap: 4px; }
    .form-group label { font-size: 13px; font-weight: 500; color: #1f3a57; }
    .form-group input { padding: 10px 14px; border: 1px solid #d6dee9; border-radius: 14px; font-size: 15px; background: white; transition: .2s; outline: none; width: 100%; }
    .form-group input:focus { border-color: #1f5a8e; box-shadow: 0 0 0 3px rgba(31,90,142,.12); }
    .btn-group { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 8px; align-items: center; }
    .btn { border: none; padding: 10px 28px; border-radius: 40px; font-weight: 500; font-size: 15px; cursor: pointer; transition: .2s; display: inline-flex; align-items: center; gap: 6px; background: #eef3f9; color: #1f3a57; }
    .btn:disabled { opacity: .6; cursor: default; }
    .btn-primary { background: #1f5a8e; color: white; }
    .btn-primary:hover { background: #164a73; }
    .btn-success { background: #1f8e6b; color: white; }
    .btn-success:hover { background: #167556; }
    .btn-danger { background: #cf3e4a; color: white; }
    .btn-danger:hover { background: #b3323d; }
    .btn-warning { background: #e6a020; color: white; }
    .btn-warning:hover { background: #cc8d1a; }
    .btn-outline { background: transparent; border: 1.5px solid #cbd7e6; }
    .btn-outline:hover { background: #eef3f9; }
    .btn-mini { padding: 5px 14px; font-size: 13px; border-radius: 30px; }
    .saved-data { background: #f9fbfd; padding: 24px 26px; border-radius: 24px; border: 1px solid #e9edf4; margin-bottom: 20px; }
    .saved-data .row { display: flex; flex-wrap: wrap; gap: 8px 28px; padding: 8px 0; border-bottom: 1px solid #e9edf4; }
    .saved-data .row:last-child { border-bottom: none; }
    .saved-data .field-label { font-weight: 500; color: #1f3a57; min-width: 120px; }
    .saved-data .field-value { color: #0b2b4a; word-break: break-word; }
    .status-badge { display: inline-block; padding: 4px 16px; border-radius: 30px; font-size: 13px; font-weight: 500; background: #d4edda; color: #0e6b3e; }
    .hidden { display: none !important; }
    .flex-wrap { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
    .panel { background: #f9fbfd; border: 1px solid #e9edf4; border-radius: 24px; padding: 22px 26px; margin-top: 22px; }
    .panel h3 { font-size: 17px; font-weight: 600; color: #0b2b4a; margin-bottom: 12px; }
    .sched-row { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; padding: 8px 0; border-bottom: 1px solid #e9edf4; font-size: 14px; color: #1f3a57; }
    .sched-row:last-child { border-bottom: none; }
    .sched-row .hora { font-weight: 700; font-size: 16px; min-width: 60px; }
    .sched-row .dias { color: #1f5a8e; font-weight: 500; min-width: 130px; }
    .sched-row .estado { font-size: 12px; padding: 2px 12px; border-radius: 30px; background: #d4edda; color: #0e6b3e; }
    .sched-row .estado.off { background: #f0f2f6; color: #8a99ab; }
    .sched-form { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; margin-top: 14px; }
    .sched-form input[type=time] { padding: 8px 12px; border: 1px solid #d6dee9; border-radius: 12px; font-size: 15px; }
    .day-check { display: inline-flex; align-items: center; gap: 4px; font-size: 14px; color: #1f3a57; background: white; border: 1px solid #d6dee9; border-radius: 10px; padding: 6px 10px; cursor: pointer; user-select: none; }
    .day-check input { accent-color: #1f5a8e; }
    .hist-row { display: flex; flex-wrap: wrap; gap: 6px 16px; padding: 7px 0; border-bottom: 1px solid #e9edf4; font-size: 13.5px; color: #1f3a57; }
    .hist-row:last-child { border-bottom: none; }
    .hist-row .ts { color: #6f8aa8; min-width: 145px; }
    .hist-row .tipo { font-weight: 600; min-width: 78px; }
    .hist-row .tipo.AUTO { color: #1f8e6b; }
    .hist-row .tipo.MANUAL { color: #1f5a8e; }
    .hist-row .res { color: #6f8aa8; }
    .aviso { font-size: 13px; color: #6f8aa8; margin-top: 6px; }
    @media (max-width: 700px) {
      .app { padding: 20px 18px; }
      .compartments-grid { grid-template-columns: repeat(auto-fill, minmax(160px,1fr)); gap: 14px; }
      .form-grid { grid-template-columns: 1fr; padding: 18px; }
    }
  </style>
</head>
<body>
<div class="app" id="app">
  <div class="header">
    <h1>Pillbox <span id="globalBadge">8</span></h1>
    <div class="flex-wrap">
      <span class="serial-pill" id="serialPill">Arduino: comprobando...</span>
      <span class="serial-pill hidden" id="posPill" title="Posicion de la ruleta">Posicion: -</span>
      <button class="btn-back hidden" id="btnVerificar" onclick="verificarSync()">Verificar</button>
      <button class="btn-back hidden" id="btnReconectar" onclick="reconectarArduino()">Reconectar</button>
      <button class="btn-back hidden" id="btnBackMain" onclick="goToMain()">Volver al menu</button>
    </div>
  </div>

  <div id="mainMenu">
    <div class="compartments-grid" id="compGrid"></div>
    <div class="panel">
      <h3>Historial de acciones</h3>
      <div id="histList" class="aviso">Cargando...</div>
    </div>
  </div>

  <div id="detailView" class="detail-view">
    <div class="detail-header">
      <div>
        <h2 id="detailTitle">Compartimiento <span id="detailNumber">1</span></h2>
        <span class="sub" id="detailSub">Configura el medicamento</span>
      </div>
      <div class="flex-wrap">
        <span id="detailStatusBadge" class="status-badge hidden">Completado</span>
      </div>
    </div>

    <div id="formContainer">
      <div class="form-grid" id="formGrid">
        <div class="form-group"><label>Medicamento</label><input type="text" id="fMedicamento" placeholder="Ej: Losartan 50 mg"></div>
        <div class="form-group"><label>Dosis</label><input type="text" id="fDosis" placeholder="Ej: 1 tableta"></div>
        <div class="form-group"><label>Paciente (opcional)</label><input type="text" id="fNombre" placeholder="Nombre del paciente"></div>
        <div class="form-group"><label>Notas (opcional)</label><input type="text" id="fNotas" placeholder="Indicaciones, habitacion..."></div>
      </div>
      <div class="btn-group">
        <button class="btn btn-success" id="btnCompletar">Completado</button>
        <button class="btn btn-outline" id="btnCancelarEdicion" onclick="cancelEdit()">Cancelar</button>
      </div>
      <div class="aviso" id="formHelper">Medicamento y dosis son obligatorios; el resto es opcional.</div>
    </div>

    <div id="savedContainer" class="hidden">
      <div class="saved-data" id="savedDataDisplay"></div>
      <div class="btn-group">
        <button class="btn btn-warning" id="btnEditar">Editar</button>
        <button class="btn btn-danger" id="btnEliminar">Eliminar</button>
        <button class="btn btn-primary" id="btnDispensar">Dispensar</button>
      </div>

      <div class="panel">
        <h3>Horarios de dispensado automatico</h3>
        <div id="schedList" class="aviso">Sin horarios programados.</div>
        <div class="sched-form">
          <input type="time" id="schedHora">
          <span id="schedDias"></span>
          <button class="btn btn-primary btn-mini" id="btnAddHorario">Agregar horario</button>
        </div>
        <div class="aviso">Elige la hora y los dias; se dispensara automaticamente en ese momento.</div>
      </div>
    </div>

    <div style="margin-top:24px;">
      <button class="btn btn-back" onclick="goToMain()">Volver al menu</button>
    </div>
  </div>
</div>

<script>
  (function() {
    const TOTAL_COMPARTMENTS = 8;
    const DIAS = ['L','M','X','J','V','S','D'];
    let currentCompartment = 1;
    let isEditing = false;
    let dataStore = {};   // compartimientos (desde el servidor)
    let horarios = [];    // horarios (desde el servidor)
    let historial = [];   // historial (desde el servidor)

    // ---------- Estado persistente (guardado en la Pi via backend) ----------
    function cargarTodo(cb) {
      fetch('/data').then(r => r.json()).then(d => {
        dataStore = d.compartimientos || {};
        horarios = d.horarios || [];
        historial = d.historial || [];
        if (cb) cb();
      }).catch(() => { if (cb) cb(); });
    }
    function getCompData(num) { return dataStore[String(num)] || null; }

    function renderMainMenu() {
      const grid = document.getElementById('compGrid');
      grid.innerHTML = '';
      let completos = 0;
      for (let i = 1; i <= TOTAL_COMPARTMENTS; i++) {
        const data = getCompData(i);
        const card = document.createElement('div');
        card.className = 'compartment-card';
        let badgeClass = 'badge', badgeText = 'Pendiente';
        if (data && data.completado === true) { badgeClass += ' completed'; badgeText = 'Completado'; completos++; }
        const schedTxt = horarios.filter(h => h.comp === i && h.activo)
                                 .map(h => h.hora).join(', ');
        let previewHtml;
        if (data && data.completado === true) {
          previewHtml = '<div class="preview-data">' +
            '<div><span class="label">Medicamento</span> <span class="value">' + escapeHtml(data.medicamento) + '</span></div>' +
            '<div><span class="label">Dosis</span> <span class="value">' + escapeHtml(data.dosis) + '</span></div>' +
            '<div><span class="label">Horarios</span> <span class="value">' + (schedTxt ? escapeHtml(schedTxt) : 'Sin programar') + '</span></div>' +
            '</div>';
        } else {
          previewHtml = '<div class="preview-empty">Sin datos</div>';
        }
        card.innerHTML = '<div class="compartment-number">COMPARTIMIENTO ' + i +
          ' <span class="' + badgeClass + '">' + badgeText + '</span></div>' + previewHtml;
        card.addEventListener('click', () => openCompartment(i));
        grid.appendChild(card);
      }
      document.getElementById('globalBadge').textContent = completos + ' / ' + TOTAL_COMPARTMENTS;
      renderHistorial();
    }

    function renderHistorial() {
      const el = document.getElementById('histList');
      if (!historial.length) { el.textContent = 'Sin acciones registradas.'; return; }
      el.innerHTML = historial.slice(0, 20).map(h =>
        '<div class="hist-row"><span class="ts">' + escapeHtml(h.ts) + '</span>' +
        '<span class="tipo ' + escapeHtml(h.tipo) + '">' + escapeHtml(h.tipo) + '</span>' +
        '<span>Comp ' + h.comp + (h.detalle ? ' - ' + escapeHtml(h.detalle) : '') + '</span>' +
        '<span class="res">' + escapeHtml(h.resultado || '') + '</span></div>').join('');
    }

    // Al abrir un compartimiento, la ruleta lo coloca ARRIBA de la zona de
    // dispensacion (posicion de espera); al dispensar, baja.
    function openCompartment(num) {
      currentCompartment = num;
      isEditing = false;
      document.getElementById('mainMenu').style.display = 'none';
      const dv = document.getElementById('detailView');
      dv.classList.add('active'); dv.style.display = 'block';
      document.getElementById('detailNumber').textContent = num;
      document.getElementById('btnBackMain').classList.remove('hidden');
      const data = getCompData(num);
      if (data && data.completado === true) showSavedData(num, data);
      else showForm(num, data || null);
      document.getElementById('detailSub').textContent =
        'Moviendo la ruleta al compartimiento ' + num + '...';
      fetch('/goto', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ compartimiento: num })
      }).then(r => r.json()).then(res => {
        if (res.pos) updatePos(res.pos);
        document.getElementById('detailSub').textContent = res.ok
          ? 'Compartimiento ' + num + ' arriba, en posicion de espera'
          : 'Sin conexion con el Arduino (posicion no confirmada)';
      }).catch(() => {});
    }

    function showForm(num, existingData) {
      document.getElementById('formContainer').classList.remove('hidden');
      document.getElementById('savedContainer').classList.add('hidden');
      document.getElementById('detailStatusBadge').classList.add('hidden');
      const fields = ['medicamento','dosis','nombre','notas'];
      if (existingData) {
        fields.forEach(f => { const el = document.getElementById('f'+f.charAt(0).toUpperCase()+f.slice(1)); if (el) el.value = existingData[f] || ''; });
        document.getElementById('detailSub').textContent = 'Editando medicamento';
        document.getElementById('btnCancelarEdicion').classList.remove('hidden');
        isEditing = true;
      } else {
        fields.forEach(f => { const el = document.getElementById('f'+f.charAt(0).toUpperCase()+f.slice(1)); if (el) el.value = ''; });
        document.getElementById('detailSub').textContent = 'Configura el medicamento';
        document.getElementById('btnCancelarEdicion').classList.add('hidden');
        isEditing = false;
      }
      document.getElementById('btnCompletar').textContent = isEditing ? 'Guardar cambios' : 'Completado';
      document.getElementById('btnCompletar').onclick = function() { handleComplete(num); };
    }

    function showSavedData(num, data) {
      document.getElementById('formContainer').classList.add('hidden');
      document.getElementById('savedContainer').classList.remove('hidden');
      const badge = document.getElementById('detailStatusBadge');
      badge.classList.remove('hidden'); badge.textContent = 'Completado';
      document.getElementById('detailSub').textContent = 'Medicamento configurado';
      const fields = [
        {label:'Medicamento',key:'medicamento'},{label:'Dosis',key:'dosis'},
        {label:'Paciente',key:'nombre'},{label:'Notas',key:'notas'}
      ];
      let html = '';
      fields.forEach(f => { html += '<div class="row"><span class="field-label">' + f.label + '</span><span class="field-value">' + escapeHtml(data[f.key]) + '</span></div>'; });
      document.getElementById('savedDataDisplay').innerHTML = html;

      document.getElementById('btnEditar').onclick = function() { showForm(num, getCompData(num) || null); };
      document.getElementById('btnEliminar').onclick = function() {
        if (!confirm('Eliminar todos los datos y horarios del COMPARTIMIENTO ' + num + '?')) return;
        fetch('/compartimiento/' + num, { method: 'DELETE' })
          .then(r => r.json()).then(() => cargarTodo(goToMain))
          .catch(e => alert('Error: ' + e));
      };
      document.getElementById('btnDispensar').onclick = function() { dispensar(num, data); };
      renderHorarios(num);
    }

    // ---------- Horarios de dispensado automatico ----------
    function renderHorarios(num) {
      const list = document.getElementById('schedList');
      const propios = horarios.filter(h => h.comp === num);
      if (!propios.length) { list.textContent = 'Sin horarios programados.'; }
      else {
        list.innerHTML = propios.map(h =>
          '<div class="sched-row">' +
          '<span class="hora">' + escapeHtml(h.hora) + '</span>' +
          '<span class="dias">' + h.dias.map(d => DIAS[d]).join(' ') + '</span>' +
          '<span class="estado' + (h.activo ? '' : ' off') + '">' + (h.activo ? 'Activo' : 'Pausado') + '</span>' +
          '<button class="btn btn-outline btn-mini" onclick="toggleHorario(' + h.id + ')">' + (h.activo ? 'Pausar' : 'Activar') + '</button>' +
          '<button class="btn btn-danger btn-mini" onclick="delHorario(' + h.id + ')">Quitar</button>' +
          '</div>').join('');
      }
      const diasBox = document.getElementById('schedDias');
      if (!diasBox.childElementCount) {
        diasBox.innerHTML = DIAS.map((d, i) =>
          '<label class="day-check"><input type="checkbox" value="' + i + '">' + d + '</label>').join(' ');
      }
      document.getElementById('btnAddHorario').onclick = function() { addHorario(num); };
    }

    function addHorario(num) {
      const hora = document.getElementById('schedHora').value;
      const dias = Array.from(document.querySelectorAll('#schedDias input:checked')).map(c => parseInt(c.value));
      if (!hora) { alert('Elige una hora.'); return; }
      if (!dias.length) { alert('Elige al menos un dia.'); return; }
      fetch('/horarios', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ comp: num, hora: hora, dias: dias })
      }).then(r => r.json()).then(res => {
        if (!res.ok) { alert(res.message || 'No se pudo guardar el horario'); return; }
        cargarTodo(() => renderHorarios(num));
      }).catch(e => alert('Error: ' + e));
    }

    window.toggleHorario = function(id) {
      fetch('/horarios/' + id, { method: 'PATCH' })
        .then(r => r.json())
        .then(() => cargarTodo(() => renderHorarios(currentCompartment)))
        .catch(e => alert('Error: ' + e));
    };
    window.delHorario = function(id) {
      fetch('/horarios/' + id, { method: 'DELETE' })
        .then(r => r.json())
        .then(() => cargarTodo(() => renderHorarios(currentCompartment)))
        .catch(e => alert('Error: ' + e));
    };

    // ---------- DISPENSAR: baja el compartimiento y suelta la pastilla ----------
    function dispensar(num, data) {
      const btn = document.getElementById('btnDispensar');
      const original = btn.textContent;
      btn.disabled = true; btn.textContent = 'Dispensando...';
      fetch('/dispense', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          compartimiento: num,
          medicamento: data.medicamento || '',
          nombre: data.nombre || '',
          dosis: data.dosis || ''
        })
      })
      .then(r => r.json())
      .then(res => { if (res.pos) updatePos(res.pos); alert(res.message); cargarTodo(renderHistorial); })
      .catch(e => alert('Error de conexion con el servidor: ' + e))
      .finally(() => { btn.disabled = false; btn.textContent = original; });
    }

    function handleComplete(num) {
      const fields = ['medicamento','dosis','nombre','notas'];
      const record = {};
      fields.forEach(f => { const el = document.getElementById('f'+f.charAt(0).toUpperCase()+f.slice(1)); record[f] = el ? el.value.trim() : ''; });
      if (!record.medicamento || !record.dosis) { alert('Medicamento y dosis son obligatorios.'); return; }
      fetch('/compartimiento/' + num, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(record)
      }).then(r => r.json()).then(res => {
        if (!res.ok) { alert(res.message || 'No se pudo guardar'); return; }
        cargarTodo(() => showSavedData(num, getCompData(num)));
      }).catch(e => alert('Error: ' + e));
    }

    function cancelEdit() {
      const num = currentCompartment; const data = getCompData(num);
      if (data && data.completado === true) showSavedData(num, data); else showForm(num, null);
    }

    function goToMain() {
      // Al cerrar el detalle, la ruleta vuelve a su posicion de origen.
      fetch('/home', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
        .then(r => r.json()).then(res => { if (res.pos) updatePos(res.pos); }).catch(() => {});
      document.getElementById('mainMenu').style.display = 'block';
      const dv = document.getElementById('detailView');
      dv.classList.remove('active'); dv.style.display = 'none';
      document.getElementById('btnBackMain').classList.add('hidden');
      renderMainMenu();
    }

    function escapeHtml(text) {
      if (!text) return '-';
      const map = {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'};
      return String(text).replace(/[&<>"']/g, m => map[m]);
    }

    // ---------- Estado de conexion y sincronizacion con el Arduino ----------
    function updatePos(pos) {
      const pill = document.getElementById('posPill');
      pill.classList.remove('sync', 'desync', 'neutro');
      if (!pos || !pos.conectado) { pill.classList.add('hidden'); return; }
      pill.classList.remove('hidden');
      pill.title = pos.detalle || '';
      if (pos.real == null) {
        pill.textContent = 'Posicion: sin verificar';
        pill.classList.add('neutro');
      } else if (pos.sincronizado || pos.esperado == null) {
        pill.textContent = 'Sincronizado: comp ' + pos.real;
        pill.classList.add('sync');
      } else {
        pill.textContent = 'Desincronizado: web ' + pos.esperado + ' / Arduino ' + pos.real;
        pill.classList.add('desync');
      }
    }

    let ultimaAutoReconexion = 0;
    function refreshSerial() {
      fetch('/serial/status').then(r => r.json()).then(s => {
        const pill = document.getElementById('serialPill');
        const btnR = document.getElementById('btnReconectar');
        const btnV = document.getElementById('btnVerificar');
        if (s.conectado) {
          pill.textContent = 'Arduino: ' + s.puerto;
          pill.classList.add('ok');
          btnR.classList.add('hidden');
          btnV.classList.remove('hidden');
        } else {
          pill.classList.remove('ok');
          btnR.classList.remove('hidden');
          btnV.classList.add('hidden');
          // AUTO-RECONEXION: sin tocar botones. Si el hub esta vivo pero el
          // Arduino se cayo (p.ej. microcaida del USB al mover el motor), se
          // pide reconectar solo, como maximo una vez cada 6 s.
          const ahora = Date.now();
          if (s.hub !== false && ahora - ultimaAutoReconexion > 6000) {
            ultimaAutoReconexion = ahora;
            pill.textContent = 'Reconectando...';
            fetch('/serial/reconnect', { method: 'POST' })
              .then(r => r.json()).then(() => refreshSerial()).catch(() => {});
          } else {
            pill.textContent = s.hub === false ? 'Hub serial apagado' : 'Arduino: sin conexion';
          }
        }
        updatePos(s.pos);
      }).catch(() => {});
    }

    // Verifica la posicion REAL preguntando al Arduino (GETPOS) y la compara.
    window.verificarSync = function() {
      const pill = document.getElementById('posPill');
      pill.classList.remove('hidden'); pill.textContent = 'Verificando...';
      fetch('/arduino/estado').then(r => r.json())
        .then(pos => updatePos(pos)).catch(() => {});
    };

    window.reconectarArduino = function() {
      const pill = document.getElementById('serialPill');
      pill.textContent = 'Reconectando...';
      fetch('/serial/reconnect', { method: 'POST' })
        .then(r => r.json()).then(() => refreshSerial())
        .catch(() => refreshSerial());
    };

    // Refresco periodico: capta los dispensados automaticos en el historial
    function refreshData() {
      cargarTodo(() => {
        if (document.getElementById('mainMenu').style.display !== 'none') renderMainMenu();
      });
    }

    function init() {
      document.getElementById('detailView').style.display = 'none';
      document.getElementById('detailView').classList.remove('active');
      document.getElementById('btnBackMain').classList.add('hidden');
      cargarTodo(renderMainMenu);
      refreshSerial();
      // Consultar la posicion real del Arduino al cargar (deja el indicador listo).
      setTimeout(function() {
        fetch('/arduino/estado').then(r => r.json()).then(updatePos).catch(() => {});
      }, 600);
      setInterval(refreshSerial, 4000);
      setInterval(refreshData, 12000);
      // Verificacion periodica de la posicion real (detecta desincronizacion,
      // ej. tras un reinicio del Arduino o un dispensado automatico).
      setInterval(function() {
        fetch('/arduino/estado').then(r => r.json()).then(updatePos).catch(() => {});
      }, 20000);
    }

    window.goToMain = goToMain;
    window.cancelEdit = cancelEdit;
    init();
  })();
</script>
</body>
</html>"""


if __name__ == "__main__":
    serial_connect()
    threading.Thread(target=bucle_horarios, daemon=True).start()
    print(f"Pillbox en http://0.0.0.0:5001  (datos en {DATA_FILE})")
    app.run(host="0.0.0.0", port=5001, threaded=True, use_reloader=False)
