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


def ejecutar_dispensado(comp, origen, detalle=""):
    """Dispensa el compartimiento 'comp' y registra la accion. Devuelve
    (ok, respuestas). Espera amplia: GOTO + bajada 180 + servo ~ 9 s peor caso."""
    resp = serial_command(f"DISPENSE,{comp}", wait=12.0)
    conectado = hub_disponible()
    ack = " | ".join(resp) if resp else "sin respuesta"
    ok = conectado and any(str(l).startswith("DISPENSADO") for l in resp)
    registrar_historial(origen, comp, detalle,
                        "OK" if ok else ("SIMULADO" if not conectado else ack))
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
    return jsonify({"ok": ok, "message": msg, "arduino": resp, "compartimiento": comp})


@app.route("/goto", methods=["POST"])
def goto():
    """Coloca el compartimiento ARRIBA de la zona de dispensacion (espera)."""
    data_req = request.get_json(silent=True) or {}
    try:
        comp = int(data_req.get("compartimiento", 0))
    except (TypeError, ValueError):
        comp = 0
    if not (1 <= comp <= N_COMPARTIMIENTOS):
        return jsonify({"ok": False, "message": "Compartimiento invalido"}), 400
    resp = serial_command(f"GOTO,{comp}", wait=8.0, until=("POS", "ERR"))
    return jsonify({"ok": hub_disponible(), "arduino": resp, "compartimiento": comp})


@app.route("/serial/status")
def serial_status():
    """Estado REAL: 'conectado' es true solo si hay un Arduino fisico abierto."""
    s = medibot_serial.hub_status()
    if s is None:
        return jsonify({"conectado": False, "puerto": None,
                        "baud": SERIAL_BAUD, "hub": False})
    return jsonify({"conectado": bool(s.get("serial_open")),
                    "puerto": s.get("port"),
                    "baud": s.get("baud", SERIAL_BAUD), "hub": True})


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
    :root {
      --bg: #eef3f7; --card: #ffffff; --linea: #dde7ee;
      --texto: #14324d; --suave: #64809a;
      --primario: #1273b5; --primario-osc: #0d5e96; --primario-claro: #e3f0f9;
      --verde: #0f8a60; --verde-claro: #e0f3ec;
      --rojo: #c4403c; --rojo-claro: #fbeae9;
      --ambar: #b97a14; --ambar-claro: #fdf3e2;
      --gris-claro: #f1f5f8;
    }
    * { margin: 0; padding: 0; box-sizing: border-box;
        font-family: 'Segoe UI', Roboto, system-ui, sans-serif; }
    body { background: var(--bg); min-height: 100vh; color: var(--texto);
           display: flex; justify-content: center; padding: 22px 16px; }
    .app { width: 100%; max-width: 1080px; }

    /* ---------- Cabecera ---------- */
    .top { background: var(--card); border: 1px solid var(--linea); border-radius: 18px;
           padding: 16px 22px; display: flex; justify-content: space-between;
           align-items: center; gap: 12px; flex-wrap: wrap;
           box-shadow: 0 2px 10px rgba(16,50,77,.05); }
    .brand { display: flex; align-items: center; gap: 14px; }
    .logo { width: 46px; height: 46px; border-radius: 13px; background: var(--primario);
            color: white; font-size: 30px; font-weight: 700; line-height: 44px;
            text-align: center; flex: none; }
    .brand h1 { font-size: 22px; font-weight: 700; letter-spacing: -.3px; }
    .brand p { font-size: 13px; color: var(--suave); }
    .top-right { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .pill { font-size: 13px; font-weight: 600; padding: 6px 14px; border-radius: 30px;
            background: var(--rojo-claro); color: var(--rojo); }
    .pill.ok { background: var(--verde-claro); color: var(--verde); }

    /* ---------- Botones ---------- */
    .btn { border: none; border-radius: 12px; padding: 10px 20px; font-size: 14px;
           font-weight: 600; cursor: pointer; transition: .15s;
           background: var(--gris-claro); color: var(--texto); }
    .btn:disabled { opacity: .55; cursor: default; }
    .btn.primario { background: var(--primario); color: white; }
    .btn.primario:hover { background: var(--primario-osc); }
    .btn.verde { background: var(--verde); color: white; }
    .btn.verde:hover { filter: brightness(.93); }
    .btn.rojo { background: var(--rojo-claro); color: var(--rojo); }
    .btn.rojo:hover { background: #f5d9d8; }
    .btn.borde { background: transparent; border: 1.5px solid var(--linea); }
    .btn.borde:hover { background: var(--gris-claro); }
    .btn.chico { padding: 6px 13px; font-size: 13px; border-radius: 10px; }
    .hidden { display: none !important; }
    .muted { color: var(--suave); font-size: 14px; }

    /* ---------- Resumen ---------- */
    .stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; margin: 16px 0; }
    .stat { background: var(--card); border: 1px solid var(--linea); border-radius: 16px;
            padding: 14px 18px; }
    .stat b { display: block; font-size: 21px; font-weight: 700; color: var(--primario); }
    .stat label { font-size: 12.5px; color: var(--suave); }

    /* ---------- Tarjetas de compartimientos ---------- */
    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(235px, 1fr));
            gap: 14px; }
    .comp-card { background: var(--card); border: 1px solid var(--linea); border-radius: 16px;
                 padding: 16px; cursor: pointer; transition: .15s;
                 display: flex; flex-direction: column; gap: 7px; min-height: 138px; }
    .comp-card:hover { border-color: var(--primario); transform: translateY(-2px);
                       box-shadow: 0 8px 18px rgba(16,50,77,.08); }
    .comp-top { display: flex; justify-content: space-between; align-items: center; }
    .chip { background: var(--primario-claro); color: var(--primario); font-weight: 700;
            font-size: 14px; padding: 3px 12px; border-radius: 9px; }
    .badge { font-size: 11.5px; font-weight: 600; padding: 3px 11px; border-radius: 30px;
             background: var(--gris-claro); color: var(--suave); }
    .badge.ok { background: var(--verde-claro); color: var(--verde); }
    .med { font-size: 17px; font-weight: 700; margin-top: 2px; }
    .med.vacio { color: var(--suave); font-weight: 500; font-style: italic; }
    .dosis { font-size: 13.5px; color: var(--suave); }
    .pac { font-size: 13px; color: var(--texto); }
    .times { display: flex; flex-wrap: wrap; gap: 5px; margin-top: auto; padding-top: 6px; }
    .time { background: var(--ambar-claro); color: var(--ambar); font-size: 12.5px;
            font-weight: 600; padding: 2px 10px; border-radius: 8px; }
    .time.ninguno { background: var(--gris-claro); color: var(--suave); font-weight: 500; }

    /* ---------- Secciones ---------- */
    .card { background: var(--card); border: 1px solid var(--linea); border-radius: 18px;
            padding: 20px 22px; margin-top: 16px; box-shadow: 0 2px 10px rgba(16,50,77,.04); }
    .card h2 { font-size: 16px; font-weight: 700; margin-bottom: 12px; }

    /* ---------- Detalle ---------- */
    .detail-head { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap;
                   margin-top: 18px; }
    .detail-head h2 { font-size: 21px; font-weight: 700; }
    .two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; align-items: start; }
    @media (max-width: 760px) { .two-col { grid-template-columns: 1fr; }
                                .stats { grid-template-columns: 1fr; } }

    .form-group { margin-bottom: 12px; }
    .form-group label { display: block; font-size: 13px; font-weight: 600; margin-bottom: 4px; }
    .form-group label small { color: var(--suave); font-weight: 500; }
    .form-group input { width: 100%; padding: 10px 13px; border: 1px solid var(--linea);
                        border-radius: 11px; font-size: 15px; outline: none; transition: .15s; }
    .form-group input:focus { border-color: var(--primario);
                              box-shadow: 0 0 0 3px rgba(18,115,181,.13); }
    .botones { display: flex; flex-wrap: wrap; gap: 9px; margin-top: 12px; }

    .rows .fila { display: flex; gap: 14px; padding: 8px 0; border-bottom: 1px solid var(--linea);
                  font-size: 14.5px; }
    .rows .fila:last-child { border-bottom: none; }
    .rows .k { color: var(--suave); min-width: 110px; flex: none; }
    .rows .v { font-weight: 600; word-break: break-word; }

    /* ---------- Horarios ---------- */
    .sched-item { display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
                  padding: 9px 0; border-bottom: 1px solid var(--linea); font-size: 14px; }
    .sched-item:last-child { border-bottom: none; }
    .sched-item .hora { font-size: 17px; font-weight: 700; min-width: 56px; }
    .sched-item .dias { color: var(--primario); font-weight: 600; letter-spacing: 2px;
                        min-width: 96px; font-size: 13px; }
    .sched-item .estado { font-size: 11.5px; font-weight: 600; padding: 2px 10px;
                          border-radius: 30px; background: var(--verde-claro); color: var(--verde); }
    .sched-item .estado.off { background: var(--gris-claro); color: var(--suave); }
    .sched-form { display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
                  margin-top: 14px; padding-top: 14px; border-top: 1px solid var(--linea); }
    .sched-form input[type=time] { padding: 8px 11px; border: 1px solid var(--linea);
                                   border-radius: 10px; font-size: 15px; }
    .days { display: flex; gap: 5px; }
    .day { width: 32px; height: 32px; border-radius: 9px; border: 1.5px solid var(--linea);
           background: white; font-size: 13px; font-weight: 700; color: var(--suave);
           cursor: pointer; transition: .12s; }
    .day.on { background: var(--primario); border-color: var(--primario); color: white; }
    .hint { font-size: 12.5px; color: var(--suave); margin-top: 10px; }

    /* ---------- Historial ---------- */
    .hist-item { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap;
                 padding: 7px 0; border-bottom: 1px solid var(--linea); font-size: 13.5px; }
    .hist-item:last-child { border-bottom: none; }
    .hist-item .ts { color: var(--suave); min-width: 140px; font-variant-numeric: tabular-nums; }
    .hist-item .tag { font-size: 11px; font-weight: 700; padding: 2px 9px; border-radius: 7px;
                      min-width: 66px; text-align: center; }
    .tag.AUTO { background: var(--verde-claro); color: var(--verde); }
    .tag.MANUAL { background: var(--primario-claro); color: var(--primario); }
    .tag.HORARIO { background: var(--ambar-claro); color: var(--ambar); }
    .tag.GUARDAR, .tag.ELIMINAR { background: var(--gris-claro); color: var(--suave); }
    .hist-item .res { color: var(--suave); margin-left: auto; }
    .res.OK { color: var(--verde); font-weight: 600; }
  </style>
</head>
<body>
<div class="app">
  <header class="top">
    <div class="brand">
      <div class="logo">+</div>
      <div>
        <h1>Pillbox</h1>
        <p>Dispensador automatico de medicamentos</p>
      </div>
    </div>
    <div class="top-right">
      <span class="pill" id="serialPill">Comprobando...</span>
      <button class="btn borde chico hidden" id="btnReconectar" onclick="reconectarArduino()">Reconectar</button>
      <button class="btn borde chico hidden" id="btnBackMain" onclick="goToMain()">Volver al menu</button>
    </div>
  </header>

  <div id="mainMenu">
    <div class="stats">
      <div class="stat"><b id="statComps">0 / 8</b><label>Compartimientos configurados</label></div>
      <div class="stat"><b id="statHor">0</b><label>Horarios activos</label></div>
      <div class="stat"><b id="statUlt">-</b><label>Ultima accion</label></div>
    </div>
    <div class="grid" id="compGrid"></div>
    <section class="card">
      <h2>Historial</h2>
      <div id="histList" class="muted">Cargando...</div>
    </section>
  </div>

  <div id="detailView" class="hidden">
    <div class="detail-head">
      <h2>Compartimiento <span id="detailNumber">1</span></h2>
      <span class="muted" id="detailSub"></span>
    </div>
    <div class="two-col">
      <section class="card">
        <h2>Medicamento</h2>
        <div id="formContainer">
          <div class="form-group"><label>Medicamento</label>
            <input type="text" id="fMedicamento" placeholder="Ej: Losartan 50 mg"></div>
          <div class="form-group"><label>Dosis</label>
            <input type="text" id="fDosis" placeholder="Ej: 1 tableta"></div>
          <div class="form-group"><label>Paciente <small>(opcional)</small></label>
            <input type="text" id="fNombre" placeholder="Nombre del paciente"></div>
          <div class="form-group"><label>Notas <small>(opcional)</small></label>
            <input type="text" id="fNotas" placeholder="Indicaciones, habitacion..."></div>
          <div class="botones">
            <button class="btn verde" id="btnCompletar">Guardar</button>
            <button class="btn borde hidden" id="btnCancelarEdicion" onclick="cancelEdit()">Cancelar</button>
          </div>
        </div>
        <div id="savedContainer" class="hidden">
          <div class="rows" id="savedDataDisplay"></div>
          <div class="botones">
            <button class="btn primario" id="btnDispensar">Dispensar ahora</button>
            <button class="btn borde" id="btnEditar">Editar</button>
            <button class="btn rojo" id="btnEliminar">Vaciar</button>
          </div>
        </div>
      </section>
      <section class="card">
        <h2>Horarios de dispensado</h2>
        <div id="schedList" class="muted">Sin horarios programados.</div>
        <div class="sched-form">
          <input type="time" id="schedHora">
          <div class="days" id="schedDias"></div>
          <button class="btn primario chico" id="btnAddHorario">Agregar</button>
        </div>
        <p class="hint">Se dispensara automaticamente a la hora elegida, los dias marcados.
        El historial de cada dispensado queda guardado.</p>
      </section>
    </div>
    <div style="margin-top:16px;">
      <button class="btn borde" onclick="goToMain()">Volver al menu</button>
    </div>
  </div>
</div>

<script>
  (function() {
    const TOTAL = 8;
    const DIAS = ['L','M','X','J','V','S','D'];
    const CAMPOS = ['medicamento','dosis','nombre','notas'];
    let actual = 1;
    let dataStore = {}, horarios = [], historial = [];

    // ---------- Datos (persistidos en la Pi via backend) ----------
    function cargarTodo(cb) {
      fetch('/data').then(r => r.json()).then(d => {
        dataStore = d.compartimientos || {};
        horarios = d.horarios || [];
        historial = d.historial || [];
        if (cb) cb();
      }).catch(() => { if (cb) cb(); });
    }
    function comp(n) { return dataStore[String(n)] || null; }

    // ---------- Menu principal ----------
    function renderMainMenu() {
      const grid = document.getElementById('compGrid');
      grid.innerHTML = '';
      let configurados = 0;
      for (let i = 1; i <= TOTAL; i++) {
        const d = comp(i);
        const activos = horarios.filter(h => h.comp === i && h.activo);
        const card = document.createElement('div');
        card.className = 'comp-card';
        let html = '<div class="comp-top"><span class="chip">' + i + '</span>';
        if (d && d.completado) {
          configurados++;
          html += '<span class="badge ok">Configurado</span></div>';
          html += '<div class="med">' + esc(d.medicamento) + '</div>';
          html += '<div class="dosis">' + esc(d.dosis) + '</div>';
          if (d.nombre) html += '<div class="pac">' + esc(d.nombre) + '</div>';
        } else {
          html += '<span class="badge">Vacio</span></div>';
          html += '<div class="med vacio">Sin medicamento</div>';
        }
        html += '<div class="times">';
        if (activos.length) {
          activos.forEach(h => { html += '<span class="time">' + esc(h.hora) + '</span>'; });
        } else {
          html += '<span class="time ninguno">Sin horario</span>';
        }
        html += '</div>';
        card.innerHTML = html;
        card.addEventListener('click', () => abrir(i));
        grid.appendChild(card);
      }
      document.getElementById('statComps').textContent = configurados + ' / ' + TOTAL;
      document.getElementById('statHor').textContent =
        horarios.filter(h => h.activo).length;
      document.getElementById('statUlt').textContent =
        historial.length ? historial[0].ts.slice(5, 16) : '-';
      renderHistorial();
    }

    function renderHistorial() {
      const el = document.getElementById('histList');
      if (!historial.length) { el.textContent = 'Sin acciones registradas.'; return; }
      el.innerHTML = historial.slice(0, 25).map(h =>
        '<div class="hist-item"><span class="ts">' + esc(h.ts) + '</span>' +
        '<span class="tag ' + esc(h.tipo) + '">' + esc(h.tipo) + '</span>' +
        '<span>Comp ' + h.comp + (h.detalle ? ' &middot; ' + esc(h.detalle) : '') + '</span>' +
        '<span class="res ' + (h.resultado === 'OK' ? 'OK' : '') + '">' +
        esc(h.resultado || '') + '</span></div>').join('');
    }

    // ---------- Detalle: al abrir, la ruleta deja el compartimiento ARRIBA ----------
    function abrir(n) {
      actual = n;
      document.getElementById('mainMenu').classList.add('hidden');
      document.getElementById('detailView').classList.remove('hidden');
      document.getElementById('btnBackMain').classList.remove('hidden');
      document.getElementById('detailNumber').textContent = n;
      const d = comp(n);
      if (d && d.completado) verGuardado(n, d); else verFormulario(n, d);
      renderHorarios(n);
      fetch('/goto', { method: 'POST', headers: {'Content-Type': 'application/json'},
                       body: JSON.stringify({ compartimiento: n }) })
        .then(r => r.json()).then(res => {
          if (res.ok) document.getElementById('detailSub').textContent =
            'En posicion de espera, listo para dispensar';
        }).catch(() => {});
    }

    function verFormulario(n, d) {
      document.getElementById('formContainer').classList.remove('hidden');
      document.getElementById('savedContainer').classList.add('hidden');
      document.getElementById('detailSub').textContent = d ? 'Editando medicamento'
                                                           : 'Configura el medicamento';
      CAMPOS.forEach(c => {
        const el = document.getElementById('f' + c.charAt(0).toUpperCase() + c.slice(1));
        if (el) el.value = (d && d[c]) || '';
      });
      document.getElementById('btnCancelarEdicion').classList.toggle('hidden', !d);
      document.getElementById('btnCompletar').onclick = function() { guardar(n); };
    }

    function verGuardado(n, d) {
      document.getElementById('formContainer').classList.add('hidden');
      document.getElementById('savedContainer').classList.remove('hidden');
      document.getElementById('detailSub').textContent = 'Configurado';
      const filas = [['Medicamento','medicamento'], ['Dosis','dosis'],
                     ['Paciente','nombre'], ['Notas','notas']];
      document.getElementById('savedDataDisplay').innerHTML = filas
        .filter(f => d[f[1]])
        .map(f => '<div class="fila"><span class="k">' + f[0] + '</span>' +
                  '<span class="v">' + esc(d[f[1]]) + '</span></div>').join('');
      document.getElementById('btnEditar').onclick = function() { verFormulario(n, comp(n)); };
      document.getElementById('btnEliminar').onclick = function() {
        if (!confirm('Vaciar el compartimiento ' + n + '? Se borran sus datos y horarios.')) return;
        fetch('/compartimiento/' + n, { method: 'DELETE' })
          .then(r => r.json()).then(() => cargarTodo(goToMain))
          .catch(e => alert('Error: ' + e));
      };
      document.getElementById('btnDispensar').onclick = function() { dispensar(n, d); };
    }

    function guardar(n) {
      const rec = {};
      CAMPOS.forEach(c => {
        const el = document.getElementById('f' + c.charAt(0).toUpperCase() + c.slice(1));
        rec[c] = el ? el.value.trim() : '';
      });
      if (!rec.medicamento || !rec.dosis) {
        alert('Medicamento y dosis son obligatorios.'); return;
      }
      fetch('/compartimiento/' + n, {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(rec)
      }).then(r => r.json()).then(res => {
        if (!res.ok) { alert(res.message || 'No se pudo guardar'); return; }
        cargarTodo(() => verGuardado(n, comp(n)));
      }).catch(e => alert('Error: ' + e));
    }

    // ---------- Dispensar: baja el compartimiento y suelta la dosis ----------
    function dispensar(n, d) {
      const btn = document.getElementById('btnDispensar');
      const txt = btn.textContent;
      btn.disabled = true; btn.textContent = 'Dispensando...';
      fetch('/dispense', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ compartimiento: n, medicamento: d.medicamento || '',
                               nombre: d.nombre || '', dosis: d.dosis || '' })
      }).then(r => r.json())
        .then(res => { alert(res.message); cargarTodo(renderHistorial); })
        .catch(e => alert('Error de conexion: ' + e))
        .finally(() => { btn.disabled = false; btn.textContent = txt; });
    }

    // ---------- Horarios ----------
    function renderHorarios(n) {
      const list = document.getElementById('schedList');
      const propios = horarios.filter(h => h.comp === n);
      if (!propios.length) { list.textContent = 'Sin horarios programados.'; }
      else {
        list.innerHTML = propios.map(h =>
          '<div class="sched-item">' +
          '<span class="hora">' + esc(h.hora) + '</span>' +
          '<span class="dias">' + h.dias.map(d => DIAS[d]).join(' ') + '</span>' +
          '<span class="estado' + (h.activo ? '' : ' off') + '">' +
            (h.activo ? 'Activo' : 'Pausado') + '</span>' +
          '<button class="btn borde chico" onclick="toggleHorario(' + h.id + ')">' +
            (h.activo ? 'Pausar' : 'Activar') + '</button>' +
          '<button class="btn rojo chico" onclick="delHorario(' + h.id + ')">Quitar</button>' +
          '</div>').join('');
      }
      const dias = document.getElementById('schedDias');
      if (!dias.childElementCount) {
        DIAS.forEach((d, i) => {
          const b = document.createElement('button');
          b.type = 'button'; b.className = 'day'; b.textContent = d; b.dataset.dia = i;
          b.onclick = function() { b.classList.toggle('on'); };
          dias.appendChild(b);
        });
      }
      document.getElementById('btnAddHorario').onclick = function() { addHorario(n); };
    }

    function addHorario(n) {
      const hora = document.getElementById('schedHora').value;
      const dias = Array.from(document.querySelectorAll('#schedDias .day.on'))
                        .map(b => parseInt(b.dataset.dia));
      if (!hora) { alert('Elige una hora.'); return; }
      if (!dias.length) { alert('Marca al menos un dia.'); return; }
      fetch('/horarios', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ comp: n, hora: hora, dias: dias })
      }).then(r => r.json()).then(res => {
        if (!res.ok) { alert(res.message || 'No se pudo guardar el horario'); return; }
        cargarTodo(() => renderHorarios(n));
      }).catch(e => alert('Error: ' + e));
    }

    window.toggleHorario = function(id) {
      fetch('/horarios/' + id, { method: 'PATCH' })
        .then(r => r.json()).then(() => cargarTodo(() => renderHorarios(actual)))
        .catch(e => alert('Error: ' + e));
    };
    window.delHorario = function(id) {
      fetch('/horarios/' + id, { method: 'DELETE' })
        .then(r => r.json()).then(() => cargarTodo(() => renderHorarios(actual)))
        .catch(e => alert('Error: ' + e));
    };

    // ---------- Navegacion ----------
    function goToMain() {
      document.getElementById('detailView').classList.add('hidden');
      document.getElementById('mainMenu').classList.remove('hidden');
      document.getElementById('btnBackMain').classList.add('hidden');
      renderMainMenu();
    }
    function cancelEdit() {
      const d = comp(actual);
      if (d && d.completado) verGuardado(actual, d); else verFormulario(actual, null);
    }

    function esc(t) {
      if (t === 0) t = '0';
      if (!t) return '';
      const m = {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'};
      return String(t).replace(/[&<>"']/g, c => m[c]);
    }

    // ---------- Estado del Arduino ----------
    function refreshSerial() {
      fetch('/serial/status').then(r => r.json()).then(s => {
        const pill = document.getElementById('serialPill');
        const btnR = document.getElementById('btnReconectar');
        if (s.conectado) {
          pill.textContent = 'Arduino: ' + s.puerto;
          pill.classList.add('ok');
          btnR.classList.add('hidden');
        } else {
          pill.textContent = s.hub === false ? 'Hub serial apagado' : 'Arduino: sin conexion';
          pill.classList.remove('ok');
          btnR.classList.remove('hidden');
        }
      }).catch(() => {});
    }
    window.reconectarArduino = function() {
      document.getElementById('serialPill').textContent = 'Reconectando...';
      fetch('/serial/reconnect', { method: 'POST' })
        .then(r => r.json()).then(() => refreshSerial())
        .catch(() => refreshSerial());
    };

    // Refresco periodico: capta los dispensados automaticos en el historial
    function refreshData() {
      cargarTodo(() => {
        if (!document.getElementById('mainMenu').classList.contains('hidden')) renderMainMenu();
      });
    }

    window.goToMain = goToMain;
    window.cancelEdit = cancelEdit;
    cargarTodo(renderMainMenu);
    refreshSerial();
    setInterval(refreshSerial, 4000);
    setInterval(refreshData, 12000);
  })();
</script>
</body>
</html>"""


if __name__ == "__main__":
    serial_connect()
    threading.Thread(target=bucle_horarios, daemon=True).start()
    print(f"Pillbox en http://0.0.0.0:5001  (datos en {DATA_FILE})")
    app.run(host="0.0.0.0", port=5001, threaded=True, use_reloader=False)
