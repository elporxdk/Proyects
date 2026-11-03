#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pillbox - Interfaz web + envio de ordenes al Arduino a traves del hub serial.

Funcionamiento de la ruleta:
  - Al ABRIR un compartimiento en la web, la ruleta lo coloca ARRIBA de la
    zona de dispensacion (GOTO = posicion de carga/espera).
  - Al DISPENSAR, la ruleta lo BAJA (media vuelta) hasta la zona de dispensado
    y el servo suelta la pastilla.

Interfaz web (estilo minimalista):
  - En el centro, el pastillero MEDIBOT tal cual es el diseno. Su ruleta gira
    como la de verdad (solo hacia delante) al elegir un compartimiento y al
    dispensar, y al dispensar se ve caer la pastilla hacia el cajon.
  - A los lados, los 8 botones COMPARTIMIENTO; abajo, DISPENSAR y MEDIBOT.
  - La tuerca (arriba a la izquierda) abre la configuracion: datos medicos,
    dosis, horarios, historial y el estado de la conexion con el Arduino.

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
Ejecutar:    python3 medibot/Pastillero.py   ->   http://<ip>:5001
"""

import hashlib
import json
import os
import threading
import time
from datetime import datetime

from flask import Flask, request, jsonify

import medibot_serial   # cliente del hub serial compartido (serial_hub.py)
import medibot_red      # IPs reales de la LAN (para entrar desde otro equipo)

# ================= CONFIGURACION =================
SERIAL_BAUD = 9600       # informativo; el baud real lo fija el hub
N_COMPARTIMIENTOS = 8
WEB_HOST = "0.0.0.0"     # escuchar en TODAS las interfaces (LAN incluida)
WEB_PORT = 5001
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "pillbox_data.json")
MAX_HISTORIAL = 500      # entradas maximas guardadas
DIAS_NOMBRES = ["L", "M", "X", "J", "V", "S", "D"]   # weekday(): lunes=0


def obtener_ip_local():
    """IP LAN real de la maquina: la que se teclea en el navegador de OTRO
    dispositivo (0.0.0.0 no es navegable). Si no hay red devuelve None; ver
    medibot_red.py para por que no se devuelve '127.0.0.1' como antes."""
    return medibot_red.ip_lan_principal()


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


#  URL de la otra interfaz. Vacia = el navegador la deduce del mismo host con
#  el puerto 5000, que es lo correcto en la red local. Se fija por entorno
#  cuando cada interfaz tiene su propio subdominio (p.ej. detras de un tunel
#  de Cloudflare, donde los puertos no valen).
URL_MEDIBOT = os.environ.get("MEDIBOT_URL_VISION", "").strip()


@app.route("/")
def index():
    return HTML_PAGE.replace("__URL_MEDIBOT__", URL_MEDIBOT)


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
  <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
  <title>Pillbox</title>
  <!--  Icono en linea (una pastilla). Sin esto el navegador pide /favicon.ico,
        Flask no lo sirve y queda un 404 en la consola en cada carga. -->
  <link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Cstyle%3Erect%7Bfill:%2301baef%7Dpath%7Bstroke:%23fff%7D@media (prefers-color-scheme:dark)%7Brect%7Bfill:%237fe9ed%7Dpath%7Bstroke:%2306161f%7D%7D%3C/style%3E%3Crect x='4' y='11' width='24' height='10' rx='5'/%3E%3Cpath d='M16 11v10' stroke-width='2'/%3E%3C/svg%3E">
  <script>
    /*  El tema se aplica AQUI, antes de que se pinte nada. Si se hiciera al
        final del <body>, la pagina se dibujaria primero en claro y luego
        saltaria a oscuro: el fogonazo blanco que molesta de noche.

        El PREDETERMINADO es CLARO: quien no haya elegido nunca ve la
        pastillera en claro, entre desde donde entre (no se sigue al tema del
        sistema). Solo se respeta lo que el usuario haya elegido a mano con
        el boton, que es lo unico que se guarda. */
    (function () {
      var t = null;
      try { t = localStorage.getItem('pillbox-theme'); } catch (e) {}
      if (t !== 'light' && t !== 'dark') { t = 'light'; }
      document.documentElement.setAttribute('data-theme', t);
    })();
  </script>
  <style>
    /*  PANEL DEL PASTILLERO, ESTILO MINIMALISTA
        ========================================
        Fondo celeste pastel, una tarjeta blanca y, en el centro, el
        pastillero MEDIBOT tal cual es el diseno (proporciones del modelo 3D,
        90 x 205 mm): tolva, ruleta, placa MEDIBOT y cajon. Botones planos de
        color solido. Lo unico que se mueve del aparato es la ruleta (y la
        pastilla que cae al dispensar).

        Cada color tiene un nombre por lo que SIGNIFICA y el tema oscuro solo
        redefine esos nombres. Los de la web de MEDIBOT (rama web,
        src/index.css) llevan al lado de que color salen; "ink/60" es el azul
        ink al 60 %, igual que text-ink/60 en Tailwind. */
    :root {
      /*  color-scheme se lo dice al NAVEGADOR, no al CSS: con el pone en su
          tono los trozos que dibuja el sistema y que ninguna variable
          alcanza (el reloj del input de hora, las casillas, la barra de
          desplazamiento). */
      color-scheme: light;
      --fondo: #f4fafb;                        /* surface */
      --tarjeta: #ffffff;                      /* card */
      --panel: #f4fafb;                        /* surface */
      --suave: rgba(10, 61, 92, 0.05);         /* ink/5 */
      --suave-hover: rgba(10, 61, 92, 0.1);    /* ink/10 */
      --borde: rgba(10, 61, 92, 0.1);          /* ink/10 */
      --borde-fuerte: rgba(10, 61, 92, 0.15);  /* ink/15 */
      --borde-activo: rgba(1, 186, 239, 0.3);  /* brand/30 */
      --borde-hover: rgba(1, 186, 239, 0.4);   /* brand/40 */
      --texto-titulo: #0a3d5c;                 /* ink */
      --texto: rgba(10, 61, 92, 0.7);          /* ink/70 */
      --texto-tenue: rgba(10, 61, 92, 0.6);    /* ink/60 */
      --texto-pastilla: rgba(10, 61, 92, 0.5); /* ink/50 */
      --texto-debil: rgba(10, 61, 92, 0.4);    /* ink/40 */
      --acento: #01baef;                       /* brand */
      --acento-fin: #0b4f6c;                   /* deep: final del degradado */
      --acento-suave: rgba(1, 186, 239, 0.1);  /* brand/10 */
      --marca-suave: #5ee1e6;                  /* brandsoft: la rueda y "MEDI" */
      --sobre-acento: #ffffff;                 /* white */
      --exito: #34d399;                        /* mint */
      --exito-suave: rgba(52, 211, 153, 0.15); /* mint/15 */
      --sombra-tarjeta: rgba(10, 61, 92, 0.12);/* shade/12 */
      /*  Rojo, ambar y verde de Tailwind 4, los que usa la web. En hex;
          su valor exacto (oklch) va mas abajo, en @supports. */
      --exito-texto: #007a55;                  /* emerald-700 */
      --peligro-suave: rgba(251, 44, 54, 0.15);/* red-500/15 */
      --peligro-hover: rgba(251, 44, 54, 0.1); /* red-500/10 */
      --peligro-borde: rgba(251, 44, 54, 0.3); /* red-500/30 */
      --peligro-texto: #e7000b;                /* red-600 */
      --peligro-pastilla: #c10007;             /* red-700 */
      --aviso-suave: rgba(254, 154, 0, 0.15);  /* amber-500/15 */
      --aviso-texto: #bb4d00;                  /* amber-700 */
      /*  La pagina y sus botones: colores planos. */
      --cielo: #d5f1f4;                        /* celeste pastel */
      --velo: rgba(10, 61, 92, 0.2);           /* ink/20 */
      --pildora: #6fd6da;                      /* turquesa */
      --pildora-hover: #5dcbd0;
      --pildora-texto: #0a3d5c;                /* ink */
      --pildora-activa: #0b4f6c;               /* deep */
      --pildora-activa-texto: #ffffff;         /* white */
      --rojo: #fb2c36;                         /* red-500 */
      --rojo-hover: #e7000b;                   /* red-600 */
      --boton-claro: #ffffff;                  /* white */
      --boton-claro-texto: #0a3d5c;            /* ink */
      --boton-claro-borde: #0a3d5c;            /* ink */
      --tuerca-metal: #c5ccd5;
      --tuerca-radios: #ffffff;
      /*  EL APARATO, con los colores del diseno. Es un objeto fisico: tiene
          los mismos colores en los dos temas (se repiten en el oscuro porque
          cada tema define TODAS sus variables). */
      --metal-1: #a6a6a6;
      --metal-2: #5e5e5e;
      --metal-3: #2a2a2a;
      --metal-4: #121212;
      --metal-filo: rgba(255, 255, 255, 0.16);
      --metal-sombra: rgba(0, 0, 0, 0.45);
      --sombra-aparato: rgba(10, 40, 50, 0.35);
      --tolva-marco: #cacaca;
      --tolva-linea: #6f6f6f;
      --tolva-1: #4f4f4f;
      --tolva-2: #8c8c8c;
      --tolva-3: #f2f2f2;
      --tolva-4: #a9a9a9;
      --cromo-1: #ececf8;
      --cromo-2: #cbcbe7;
      --cromo-3: #a3a4cc;
      --cromo-4: #77789f;
      --disco-1: #4b4b4b;
      --disco-2: #222222;
      --radio: #d2d2d2;
      --placa-1: #bdbdbd;
      --placa-2: #959595;
      --placa-borde: #3d3d3d;
      --placa-texto: #a3a3a3;
      --cajon-1: #a3a3a3;
      --cajon-2: #7d7d7d;
      --cajon-logo: #4a4a4a;
      --tirador: #cfcfcf;
      --pastilla-a: #ffffff;
      --pastilla-b: #22c9f5;
    }
    /*  En oscuro la web no se limita a invertir: fondos de azul muy
        desaturado (no gris) y el texto de las pastillas en el tono claro de
        cada color, que es lo que mantiene el contraste. */
    html[data-theme="dark"] {
      color-scheme: dark;
      --fondo: #06161f;                        /* surface */
      --tarjeta: #0e2733;                      /* card */
      --panel: #06161f;                        /* surface */
      --suave: rgba(219, 234, 243, 0.05);      /* ink/5 */
      --suave-hover: rgba(219, 234, 243, 0.1); /* ink/10 */
      --borde: rgba(219, 234, 243, 0.1);       /* ink/10 */
      --borde-fuerte: rgba(219, 234, 243, 0.15); /* ink/15 */
      --borde-activo: rgba(34, 201, 245, 0.3); /* brand/30 */
      --borde-hover: rgba(34, 201, 245, 0.4);  /* brand/40 */
      --texto-titulo: #dbeaf3;                 /* ink */
      --texto: rgba(219, 234, 243, 0.7);       /* ink/70 */
      --texto-tenue: rgba(219, 234, 243, 0.6); /* ink/60 */
      --texto-pastilla: rgba(219, 234, 243, 0.5); /* ink/50 */
      --texto-debil: rgba(219, 234, 243, 0.4); /* ink/40 */
      --acento: #22c9f5;                       /* brand */
      --acento-fin: #0b4f6c;                   /* deep */
      --acento-suave: rgba(34, 201, 245, 0.1); /* brand/10 */
      --marca-suave: #7fe9ed;                  /* brandsoft */
      --sobre-acento: #ffffff;                 /* white */
      --exito: #34d399;                        /* mint */
      --exito-suave: rgba(52, 211, 153, 0.15); /* mint/15 */
      --sombra-tarjeta: rgba(0, 0, 0, 0.4);
      --exito-texto: #5ee9b5;                  /* emerald-300 */
      --peligro-suave: rgba(251, 44, 54, 0.15);/* red-500/15 */
      --peligro-hover: rgba(251, 44, 54, 0.1); /* red-500/10 */
      --peligro-borde: rgba(251, 44, 54, 0.3); /* red-500/30 */
      --peligro-texto: #ff6467;                /* red-400 */
      --peligro-pastilla: #ffa2a2;             /* red-300 */
      --aviso-suave: rgba(254, 154, 0, 0.15);  /* amber-500/15 */
      --aviso-texto: #ffd230;                  /* amber-300 */
      --cielo: #06161f;                        /* surface */
      --velo: rgba(2, 10, 15, 0.55);
      --pildora: #3fcad0;
      --pildora-hover: #35b8be;
      --pildora-texto: #032029;
      --pildora-activa: #dbeaf3;               /* ink */
      --pildora-activa-texto: #06161f;         /* surface */
      --rojo: #fb2c36;                         /* red-500 */
      --rojo-hover: #e7000b;                   /* red-600 */
      --boton-claro: #0e2733;                  /* card */
      --boton-claro-texto: #dbeaf3;            /* ink */
      --boton-claro-borde: rgba(219, 234, 243, 0.5); /* ink/50 */
      --tuerca-metal: #6e818f;
      --tuerca-radios: #e2fafb;
      --metal-1: #a6a6a6;
      --metal-2: #5e5e5e;
      --metal-3: #2a2a2a;
      --metal-4: #121212;
      --metal-filo: rgba(255, 255, 255, 0.16);
      --metal-sombra: rgba(0, 0, 0, 0.45);
      --sombra-aparato: rgba(0, 0, 0, 0.6);
      --tolva-marco: #cacaca;
      --tolva-linea: #6f6f6f;
      --tolva-1: #4f4f4f;
      --tolva-2: #8c8c8c;
      --tolva-3: #f2f2f2;
      --tolva-4: #a9a9a9;
      --cromo-1: #ececf8;
      --cromo-2: #cbcbe7;
      --cromo-3: #a3a4cc;
      --cromo-4: #77789f;
      --disco-1: #4b4b4b;
      --disco-2: #222222;
      --radio: #d2d2d2;
      --placa-1: #bdbdbd;
      --placa-2: #959595;
      --placa-borde: #3d3d3d;
      --placa-texto: #a3a3a3;
      --cajon-1: #a3a3a3;
      --cajon-2: #7d7d7d;
      --cajon-logo: #4a4a4a;
      --tirador: #cfcfcf;
      --pastilla-a: #ffffff;
      --pastilla-b: #22c9f5;
    }
    @supports (color: oklch(0% 0 0)) {
      :root {
        --exito-texto: oklch(50.8% 0.118 165.612);
        --peligro-suave: oklch(63.7% 0.237 25.331 / 0.15);
        --peligro-hover: oklch(63.7% 0.237 25.331 / 0.1);
        --peligro-borde: oklch(63.7% 0.237 25.331 / 0.3);
        --peligro-texto: oklch(57.7% 0.245 27.325);
        --peligro-pastilla: oklch(50.5% 0.213 27.518);
        --aviso-suave: oklch(76.9% 0.188 70.08 / 0.15);
        --aviso-texto: oklch(55.5% 0.163 48.998);
      }
      html[data-theme="dark"] {
        --exito-texto: oklch(84.5% 0.143 164.978);
        --peligro-suave: oklch(63.7% 0.237 25.331 / 0.15);
        --peligro-hover: oklch(63.7% 0.237 25.331 / 0.1);
        --peligro-borde: oklch(63.7% 0.237 25.331 / 0.3);
        --peligro-texto: oklch(70.4% 0.191 22.216);
        --peligro-pastilla: oklch(80.8% 0.114 19.571);
        --aviso-suave: oklch(76.9% 0.188 70.08 / 0.15);
        --aviso-texto: oklch(87.9% 0.169 91.605);
      }
    }

    /* ================= BASE ================= */
    * { margin: 0; padding: 0; box-sizing: border-box; }
    html { -webkit-text-size-adjust: 100%; }
    body {
      font-family: 'Segoe UI', Roboto, system-ui, -apple-system, sans-serif;
      color: var(--texto-titulo); background: var(--cielo);
      min-height: 100vh; padding: clamp(14px, 2vw, 24px) clamp(16px, 3vw, 40px) 32px;
    }
    button, input { font: inherit; color: inherit; }
    button { -webkit-tap-highlight-color: transparent; }
    :focus-visible { outline: 3px solid var(--acento); outline-offset: 3px; }
    .hidden { display: none !important; }

    /* ================= CABECERA: la tuerca y el titulo ================= */
    .cabecera {
      max-width: 1240px; margin: 0 auto 14px;
      display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; gap: 14px;
    }
    .tuerca {
      justify-self: start; width: 76px; height: 76px; border-radius: 50%; border: 0;
      display: grid; place-items: center; cursor: pointer;
      background: var(--tarjeta); box-shadow: 0 8px 22px -8px var(--sombra-tarjeta);
      transition: box-shadow .25s ease;
    }
    .tuerca:hover { box-shadow: 0 12px 28px -8px var(--sombra-tarjeta); }
    .tuerca svg { width: 60px; height: 60px; transition: transform .6s cubic-bezier(.3, 1.4, .5, 1); }
    .tuerca:hover svg { transform: rotate(45deg); }
    .tuerca[aria-expanded="true"] svg { transform: rotate(90deg); }
    .tuerca-dientes { fill: var(--tuerca-metal); }
    .tuerca-aro { fill: var(--tuerca-radios); }
    .tuerca-disco { fill: var(--marca-suave); }
    .tuerca-radios { stroke: var(--tuerca-radios); stroke-width: 2.6; stroke-linecap: round; }
    .tuerca-centro { fill: var(--tuerca-radios); }
    .titulo {
      font-size: clamp(2.6rem, 6vw, 4.4rem); font-weight: 900; line-height: 1;
      letter-spacing: .04em; color: var(--texto-titulo); text-align: center;
    }

    /* ================= LA TARJETA ================= */
    .tablero {
      max-width: 1240px; margin: 0 auto;
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
      grid-template-rows: 1fr auto;
      grid-template-areas: "izq aparato der" "dispensar aparato medibot";
      column-gap: clamp(1.5rem, 5vw, 5rem); row-gap: 28px;
      padding: clamp(22px, 2.6vw, 34px) clamp(20px, 3.6vw, 56px);
      border-radius: 40px; background: var(--tarjeta);
      box-shadow: 0 24px 60px -28px var(--sombra-tarjeta);
    }
    .columna { display: flex; flex-direction: column; justify-content: space-around; gap: 16px; width: 100%; max-width: 20rem; }
    .columna-izq { grid-area: izq; justify-self: end; }
    .columna-der { grid-area: der; justify-self: start; }

    /*  COMPARTIMIENTO: una pildora de color solido. Elegido: color profundo. */
    .comp {
      display: flex; flex-direction: column; justify-content: center; align-items: flex-start; gap: 2px;
      width: 100%; min-height: 76px; padding: 12px 30px; border: 0; border-radius: 999px;
      background: var(--pildora); color: var(--pildora-texto); text-align: left; cursor: pointer;
      transition: background-color .2s ease, color .2s ease, transform .15s ease;
    }
    .comp:hover { background: var(--pildora-hover); }
    .comp:active { transform: scale(.98); }
    .comp[aria-pressed="true"] { background: var(--pildora-activa); color: var(--pildora-activa-texto); }
    .comp-titulo { font-size: 15px; font-weight: 800; letter-spacing: .08em; white-space: nowrap; }
    .comp-med { max-width: 100%; font-size: 13px; font-weight: 600; opacity: .75; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

    /*  DISPENSAR (rojo) y MEDIBOT (blanco con borde), planos. */
    .acciones { display: contents; }
    .accion { width: 100%; max-width: 20rem; display: flex; justify-content: center; }
    .accion-izq { grid-area: dispensar; justify-self: end; }
    .accion-der { grid-area: medibot; justify-self: start; }
    .btn-dispensar, .btn-medibot {
      display: inline-flex; align-items: center; justify-content: center;
      min-width: 15rem; height: 64px; padding: 0 34px; border-radius: 999px;
      font-size: 19px; font-weight: 800; letter-spacing: .08em; cursor: pointer;
      transition: background-color .2s ease, transform .15s ease;
    }
    .btn-dispensar { border: 0; background: var(--rojo); color: var(--sobre-acento); }
    .btn-dispensar:hover { background: var(--rojo-hover); }
    .btn-dispensar:active, .btn-medibot:active { transform: scale(.98); }
    .btn-dispensar:disabled { opacity: .7; cursor: progress; }
    .btn-medibot {
      background: var(--boton-claro); color: var(--boton-claro-texto);
      border: 2px solid var(--boton-claro-borde); text-decoration: none;
    }
    .btn-medibot:hover { background: var(--suave); }

    /* ================= EL PASTILLERO (tal cual el diseno) ================= */
    /*  Se mide en em: cambiando el font-size de .aparato se escala entero.
        20 x 45,2 em, la proporcion del modelo 3D. */
    .aparato { grid-area: aparato; justify-self: center; align-self: center; font-size: clamp(9px, min(1.56vh, 1.38vw), 16px); }
    .aparato-cuerpo {
      position: relative; width: 20em; height: 45.2em; border-radius: .3em;
      background:
        linear-gradient(180deg, transparent 55%, var(--metal-sombra) 100%),
        linear-gradient(100deg, var(--metal-4) 0%, var(--metal-3) 38%, var(--metal-2) 74%, var(--metal-1) 100%);
      box-shadow: inset -.3em 0 0 var(--metal-filo), inset 0 1px 0 var(--metal-filo),
                  .6em .9em 1.8em var(--sombra-aparato);
    }
    /*  Tolva (arriba). */
    .tolva {
      position: absolute; top: 1.1em; left: 6.5em; width: 7em; height: 6.9em;
      padding: 1.1em .25em; background: var(--tolva-marco); border: .1em solid var(--tolva-linea);
      box-shadow: .2em .3em .5em var(--metal-sombra);
    }
    .tolva-boca {
      height: 100%; border: .1em solid var(--tolva-linea);
      background: linear-gradient(90deg, var(--tolva-1) 0%, var(--tolva-2) 40%, var(--tolva-linea) 41%, var(--tolva-3) 46%, var(--tolva-3) 57%, var(--tolva-linea) 58%, var(--tolva-4) 60%, var(--tolva-4) 100%);
    }
    /*  Ruleta: bisel, aro y el disco que gira (SVG). */
    .ruleta { position: absolute; z-index: 1; top: 11.1em; left: .25em; width: 19.5em; height: 19.5em; border-radius: 50%; }
    .ruleta-bisel {
      position: absolute; inset: 0; border-radius: 50%;
      background: conic-gradient(from 200deg, var(--cromo-2), var(--cromo-1) 12%, var(--cromo-3) 30%, var(--cromo-2) 50%, var(--cromo-1) 64%, var(--cromo-3) 82%, var(--cromo-2));
      box-shadow: 0 .25em .6em var(--metal-sombra), inset 0 0 0 .08em var(--cromo-4);
    }
    .ruleta-anillo {
      position: absolute; inset: 1.45em; border-radius: 50%;
      background: repeating-radial-gradient(circle, var(--cromo-2) 0 .3em, var(--cromo-3) .3em .36em);
      box-shadow: 0 0 0 .1em var(--cromo-4), inset 0 .1em .3em var(--metal-sombra);
    }
    .ruleta-disco {
      position: absolute; inset: 2.55em; border-radius: 50%;
      box-shadow: 0 0 0 .1em var(--cromo-4);
      transition-property: transform; transition-duration: 0s;
      transition-timing-function: cubic-bezier(.45, .05, .3, 1);
      will-change: transform;
    }
    .ruleta-disco svg { display: block; width: 100%; height: 100%; }
    .st-disco1 { stop-color: var(--disco-1); }
    .st-disco2 { stop-color: var(--disco-2); }
    .llanta { fill: none; stroke: var(--radio); stroke-width: 2; }
    .radios { stroke: var(--radio); stroke-width: 2.2; stroke-linecap: round; }
    .cubo { fill: var(--disco-2); stroke: var(--radio); stroke-width: 1.6; }
    .cubo-roseta circle { fill: none; stroke: var(--radio); stroke-width: 1; }
    /*  La pastilla: solo se ve al dispensar, cayendo por detras de la placa. */
    .pastilla-cae {
      position: absolute; left: 50%; top: 14.2em; width: 1.5em; height: .72em; margin-left: -.75em;
      border-radius: .36em; opacity: 0; pointer-events: none;
      background: linear-gradient(90deg, var(--pastilla-a) 50%, var(--pastilla-b) 50%);
    }
    .pastilla-cae.cayendo { animation: caer 1.2s cubic-bezier(.5, 0, .9, .6) forwards; }
    @keyframes caer {
      0% { opacity: 0; transform: translateY(0) rotate(90deg) scale(.5); }
      15% { opacity: 1; transform: translateY(0) rotate(90deg) scale(1); }
      80% { opacity: 1; transform: translateY(7.4em) rotate(230deg); }
      100% { opacity: 0; transform: translateY(9.4em) rotate(280deg); }
    }
    /*  Placa MEDIBOT y cajon. */
    .placa {
      position: absolute; z-index: 2; top: 31em; left: 1.9em; width: 16.2em; height: 5.5em;
      display: grid; place-items: center;
      background: linear-gradient(180deg, var(--placa-1), var(--placa-2));
      border: .28em solid var(--placa-borde);
    }
    .placa span {
      font-size: 3em; font-weight: 900; letter-spacing: .01em; line-height: 1;
      color: var(--placa-texto); -webkit-text-stroke: .045em var(--placa-borde);
      text-shadow: .03em .05em 0 var(--placa-borde);
    }
    .cajon {
      position: absolute; z-index: 2; top: 36.8em; left: 2.2em; width: 15.6em; height: 8.1em;
      background: linear-gradient(180deg, var(--cajon-1), var(--cajon-2));
      border: .18em solid var(--placa-borde); box-shadow: inset 0 .2em 0 var(--tirador);
    }
    .cajon-tirador {
      position: absolute; top: .9em; left: 50%; width: 5em; height: .55em; margin-left: -2.5em;
      background: var(--tirador); border: .1em solid var(--placa-borde); border-radius: .3em;
    }
    .cajon-logo { position: absolute; top: 2em; left: 50%; width: 4.6em; height: 4.6em; margin-left: -2.3em; }
    .cajon-logo circle, .cajon-logo path { fill: none; stroke: var(--cajon-logo); stroke-width: 4; stroke-linecap: round; }

    /* ================= PANEL DE CONFIGURACION (la tuerca) ================= */
    .velo { position: fixed; inset: 0; z-index: 900; background: var(--velo); opacity: 0; pointer-events: none; transition: opacity .35s ease; }
    .velo.abierto { opacity: 1; pointer-events: auto; }
    .ajustes {
      position: fixed; z-index: 901; top: 12px; left: 12px; bottom: 12px;
      width: min(30rem, calc(100vw - 24px)); display: flex; flex-direction: column;
      border-radius: 28px; background: var(--tarjeta); border: 1px solid var(--borde);
      box-shadow: 0 30px 80px -20px var(--sombra-tarjeta);
      transform: translateX(calc(-100% - 40px)); visibility: hidden;
      transition: transform .5s cubic-bezier(.2, .9, .25, 1), visibility 0s linear .5s;
    }
    .ajustes.abierto { transform: none; visibility: visible; transition: transform .5s cubic-bezier(.2, .9, .25, 1), visibility 0s; }
    .ajustes-cabeza { display: flex; align-items: center; gap: 12px; padding: 20px 18px 14px 22px; }
    .ajustes-cabeza h2 { font-size: 21px; font-weight: 800; }
    .ajustes-cabeza p { font-size: 12.5px; color: var(--texto-tenue); }
    .cerrar {
      margin-left: auto; flex: none; width: 40px; height: 40px; border-radius: 50%;
      border: 0; background: var(--suave); color: var(--texto-titulo);
      display: grid; place-items: center; cursor: pointer; transition: background .2s;
    }
    .cerrar:hover { background: var(--suave-hover); }
    .cerrar svg { width: 18px; height: 18px; }
    .pestanas { display: flex; gap: 4px; margin: 0 18px 4px; padding: 4px; border-radius: 999px; background: var(--suave); }
    .pestana {
      flex: 1; border: 0; background: transparent; padding: 9px 8px; border-radius: 999px;
      font-size: 13.5px; font-weight: 700; color: var(--texto); cursor: pointer; transition: background .2s, color .2s;
    }
    .pestana:hover { color: var(--texto-titulo); }
    .pestana[aria-selected="true"] { background: var(--tarjeta); color: var(--texto-titulo); box-shadow: 0 1px 3px var(--sombra-tarjeta); }
    .ajustes-cuerpo { flex: 1; overflow-y: auto; padding: 14px 18px 26px; overscroll-behavior: contain; }
    .intro { font-size: 13px; color: var(--texto-tenue); margin: 2px 2px 12px; line-height: 1.45; }

    .fila-comp {
      width: 100%; display: flex; align-items: center; gap: 12px; padding: 11px 14px 11px 11px;
      border-radius: 18px; border: 1px solid var(--borde); background: var(--tarjeta); color: var(--texto-titulo);
      text-align: left; cursor: pointer; transition: border-color .2s, background .2s;
    }
    .fila-comp + .fila-comp { margin-top: 8px; }
    .fila-comp:hover { border-color: var(--borde-activo); background: var(--panel); }
    .fila-num { flex: none; width: 38px; height: 38px; border-radius: 50%; display: grid; place-items: center; font-weight: 800; background: var(--pildora); color: var(--pildora-texto); }
    .fila-comp.vacia .fila-num { background: var(--suave); color: var(--texto-tenue); }
    .fila-datos { flex: 1; min-width: 0; line-height: 1.3; }
    .fila-titulo, .fila-sub { display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .fila-titulo { font-size: 14.5px; font-weight: 700; }
    .fila-sub { font-size: 12.5px; color: var(--texto-tenue); }
    .fila-comp.vacia .fila-titulo { color: var(--texto-debil); font-weight: 600; }
    .badge { flex: none; font-size: 12px; font-weight: 600; padding: 3px 11px; border-radius: 999px; background: var(--acento-suave); color: var(--acento); }
    .badge.completed { background: var(--exito-suave); color: var(--exito-texto); }

    .volver { display: inline-flex; align-items: center; gap: 6px; border: 0; background: none; color: var(--texto); font-size: 13.5px; font-weight: 700; cursor: pointer; padding: 6px 4px; border-radius: 10px; }
    .volver:hover { color: var(--acento); }
    .volver svg { width: 16px; height: 16px; }
    .editor-cabeza { display: flex; align-items: center; gap: 12px; margin: 8px 0 4px; }
    .editor-cabeza .fila-num { width: 46px; height: 46px; font-size: 19px; }
    .editor-cabeza h3 { font-size: 19px; font-weight: 800; }
    .editor-cabeza p { font-size: 12.5px; color: var(--texto-tenue); }
    .editor-cabeza .badge { margin-left: auto; }

    .bloque { background: var(--panel); border: 1px solid var(--borde); border-radius: 20px; padding: 16px 16px 18px; margin-top: 14px; }
    .bloque h4 { font-size: 12.5px; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; color: var(--texto-tenue); margin-bottom: 12px; }
    .form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    .form-group { display: flex; flex-direction: column; gap: 5px; }
    .form-group.ancho { grid-column: 1 / -1; }
    .form-group label { font-size: 12.5px; font-weight: 700; color: var(--texto); }
    .form-group label b { color: var(--peligro-texto); font-weight: 700; }
    .form-group input {
      width: 100%; padding: 11px 14px; border-radius: 14px; border: 1px solid var(--borde-fuerte);
      background: var(--tarjeta); color: var(--texto-titulo); font-size: 15px; outline: none; transition: border-color .2s;
    }
    .form-group input::placeholder { color: var(--texto-debil); }
    .form-group input:focus { border-color: var(--acento); }
    .form-group input[aria-invalid="true"] { border-color: var(--peligro-texto); }
    .form-error { min-height: 1.2em; margin-top: 10px; font-size: 13px; font-weight: 600; color: var(--peligro-texto); }
    .btn-group { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 6px; align-items: center; }
    .btn { border: 0; padding: 10px 22px; border-radius: 999px; font-weight: 700; font-size: 14px; cursor: pointer; transition: background .2s, border-color .2s, color .2s; display: inline-flex; align-items: center; gap: 6px; background: var(--suave); color: var(--texto-titulo); }
    .btn:disabled { opacity: .6; cursor: default; }
    .btn-primary { background: var(--acento-fin); color: var(--sobre-acento); }
    .btn-primary:not(:disabled):hover { background: var(--texto-titulo); color: var(--tarjeta); }
    .btn-danger { background: transparent; border: 1px solid var(--peligro-borde); color: var(--peligro-texto); }
    .btn-danger:hover, .btn-danger.confirmar { background: var(--peligro-hover); }
    .btn-danger.confirmar { border-color: var(--peligro-texto); }
    .btn-outline { background: transparent; border: 1px solid var(--borde-fuerte); color: var(--texto); }
    .btn-outline:hover { border-color: var(--borde-hover); color: var(--texto-titulo); }
    .btn-mini { padding: 6px 13px; font-size: 12.5px; }
    .aviso { font-size: 12.5px; color: var(--texto-tenue); margin-top: 10px; line-height: 1.45; }
    .vacio-txt { font-size: 13.5px; color: var(--texto-debil); }

    .sched-row { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 10px; padding: 9px 0; border-bottom: 1px solid var(--borde); font-size: 13.5px; color: var(--texto); }
    .sched-row:last-child { border-bottom: none; }
    .sched-row .hora { font-weight: 800; font-size: 17px; color: var(--texto-titulo); min-width: 56px; font-variant-numeric: tabular-nums; }
    .sched-row .dias { color: var(--acento); font-weight: 700; letter-spacing: .08em; }
    .sched-row .estado { font-size: 12px; font-weight: 600; padding: 2px 10px; border-radius: 999px; background: var(--exito-suave); color: var(--exito-texto); }
    .sched-row .estado.off { background: var(--suave); color: var(--texto-pastilla); }
    .sched-acciones { margin-left: auto; display: flex; gap: 6px; }
    .sched-form { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin-top: 14px; }
    .sched-form input[type=time] { padding: 9px 12px; border: 1px solid var(--borde-fuerte); border-radius: 12px; font-size: 15px; background: var(--tarjeta); color: var(--texto-titulo); }
    .dias-sel { display: flex; gap: 5px; flex-wrap: wrap; }
    .day-check { position: relative; cursor: pointer; user-select: none; }
    .day-check input { position: absolute; opacity: 0; width: 1px; height: 1px; }
    .day-check span {
      display: grid; place-items: center; width: 34px; height: 34px; border-radius: 50%;
      font-size: 13px; font-weight: 800; color: var(--texto); background: var(--tarjeta); border: 1px solid var(--borde-fuerte);
      transition: background .2s, color .2s, border-color .2s;
    }
    .day-check:hover span { border-color: var(--borde-activo); color: var(--texto-titulo); }
    .day-check input:checked + span { background: var(--pildora); color: var(--pildora-texto); border-color: transparent; }
    .day-check input:focus-visible + span { outline: 3px solid var(--acento); outline-offset: 2px; }

    .hist-row { display: flex; gap: 12px; padding: 11px 2px; border-bottom: 1px solid var(--borde); }
    .hist-row:last-child { border-bottom: none; }
    .hist-marca { flex: none; width: 8px; height: 8px; margin-top: 6px; border-radius: 50%; background: var(--texto-debil); }
    .hist-marca.AUTO { background: var(--exito); }
    .hist-marca.MANUAL { background: var(--acento); }
    .hist-marca.ELIMINAR { background: var(--peligro-texto); }
    .hist-cuerpo { flex: 1; min-width: 0; font-size: 13.5px; color: var(--texto); }
    .hist-linea { display: flex; flex-wrap: wrap; gap: 4px 10px; align-items: baseline; }
    .hist-row .tipo { font-weight: 800; letter-spacing: .04em; color: var(--texto-titulo); }
    .hist-row .tipo.AUTO { color: var(--exito-texto); }
    .hist-row .tipo.MANUAL { color: var(--acento); }
    .hist-comp { font-weight: 600; }
    .hist-row .res { margin-left: auto; font-size: 12px; font-weight: 700; color: var(--texto-tenue); }
    .hist-detalle { margin-top: 2px; word-break: break-word; }
    .hist-row .ts { margin-top: 2px; font-size: 12px; color: var(--texto-tenue); font-variant-numeric: tabular-nums; }

    /*  Estado del sistema: las pildoras de siempre (Conectado / Desconectado,
        Sincronizado / Desincronizado), ahora dentro de la configuracion. */
    .dato { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 9px 0; border-bottom: 1px solid var(--borde); font-size: 13.5px; }
    .dato:last-of-type { border-bottom: none; }
    .dato-k { color: var(--texto-tenue); font-weight: 600; }
    .dato-v { color: var(--texto-titulo); font-weight: 700; text-align: right; word-break: break-word; }
    .serial-pill { font-size: 13px; font-weight: 600; padding: 4px 12px; border-radius: 999px; background: var(--peligro-suave); color: var(--peligro-pastilla); }
    .serial-pill.ok { background: var(--exito-suave); color: var(--exito-texto); }
    .serial-pill.sync { background: var(--exito-suave); color: var(--exito-texto); }
    .serial-pill.desync { background: var(--aviso-suave); color: var(--aviso-texto); }
    .serial-pill.neutro { background: var(--suave); color: var(--texto-pastilla); }

    /* ================= AVISOS ================= */
    .avisos { position: fixed; z-index: 1100; right: 18px; top: 18px; width: min(92vw, 25rem); display: flex; flex-direction: column; gap: 8px; pointer-events: none; }
    .toast {
      pointer-events: auto; display: flex; align-items: center; gap: 10px; padding: 11px 10px 11px 14px;
      border-radius: 14px; background: var(--tarjeta); color: var(--texto-titulo);
      border: 1px solid var(--borde); border-left: 4px solid var(--acento);
      box-shadow: 0 14px 34px -14px var(--sombra-tarjeta); font-size: 14px; font-weight: 600;
      animation: entrar .3s ease;
    }
    .toast.ok { border-left-color: var(--exito); }
    .toast.error { border-left-color: var(--peligro-texto); }
    .toast.aviso-t { border-left-color: var(--aviso-texto); }
    .toast.fuera { opacity: 0; transition: opacity .3s; }
    .toast-texto { flex: 1; line-height: 1.35; }
    .toast-accion { flex: none; border: 0; border-radius: 999px; padding: 7px 14px; font-size: 13px; font-weight: 800; cursor: pointer; color: var(--sobre-acento); background: var(--acento-fin); }
    .toast-cerrar { flex: none; width: 30px; height: 30px; border: 0; border-radius: 50%; background: transparent; color: var(--texto-tenue); cursor: pointer; display: grid; place-items: center; }
    .toast-cerrar:hover { background: var(--suave); }
    .toast-cerrar svg { width: 14px; height: 14px; }
    @keyframes entrar { from { opacity: 0; } }

    /*  PANTALLA DE CARGA: la rueda de MEDIBOT (la de la web, MedibotLogo.tsx)
        mientras llega la pagina. La quita quitarPantallaCarga() al arrancar,
        con un minimo de medio segundo para que no sea un parpadeo. Si el
        JavaScript fallara, la animacion pc-fuera la quita igual a los 4 s. */
    .rueda path { fill: var(--marca-suave); }
    @keyframes girar-ruleta { to { transform: rotate(360deg); } }
    .ruleta-marca { animation: girar-ruleta 1.6s linear infinite; transform-origin: 50% 50%; will-change: transform; }
    .pantalla-carga { position: fixed; inset: 0; z-index: 2000; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 16px; padding: 20px; background: var(--fondo); transition: opacity .35s ease, visibility .35s ease; animation: pc-fuera .35s ease 4s forwards; }
    .pantalla-carga.fuera { opacity: 0; visibility: hidden; pointer-events: none; }
    @keyframes pc-fuera { to { opacity: 0; visibility: hidden; pointer-events: none; } }
    .pantalla-carga .rueda { width: 96px; height: 96px; }
    .pc-marca { font-size: 30px; font-weight: 800; letter-spacing: -.3px; line-height: 1; color: var(--texto-titulo); }
    .pc-marca .medi { color: var(--marca-suave); }
    .pc-tag { font-size: 12px; font-weight: 600; letter-spacing: 3px; color: var(--texto-tenue); }

    /* ================= PANTALLAS PEQUENAS ================= */
    @media (max-width: 900px) {
      .cabecera { grid-template-columns: auto 1fr; }
      .titulo { text-align: left; font-size: clamp(2.2rem, 10vw, 3.2rem); }
      .tuerca { width: 60px; height: 60px; }
      .tuerca svg { width: 48px; height: 48px; }
      .tablero {
        grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
        grid-template-rows: auto;
        grid-template-areas: "aparato aparato" "izq der" "dispensar medibot";
        column-gap: 10px; row-gap: 18px; padding: 24px 14px 18px; border-radius: 28px;
      }
      .aparato { font-size: clamp(8px, 2.4vw, 12px); }
      .columna { max-width: none; justify-self: stretch; gap: 10px; }
      .comp { min-height: 60px; padding: 10px 18px; }
      .comp-titulo { font-size: 11.5px; letter-spacing: .03em; }
      .comp-med { font-size: 12px; }
      .accion { max-width: none; }
      .btn-dispensar, .btn-medibot { min-width: 0; width: 100%; height: 56px; padding: 0 12px; font-size: 16px; }
      .avisos { top: auto; right: auto; left: 50%; bottom: 16px; translate: -50% 0; flex-direction: column-reverse; }
    }
    @media (max-width: 520px) {
      .ajustes { top: 0; left: 0; bottom: 0; width: 100vw; border-radius: 0 24px 24px 0; }
      .form-grid { grid-template-columns: 1fr; }
    }
    @media (prefers-reduced-motion: reduce) {
      .ruleta-marca { animation: none; }
    }
  </style>
</head>
<body>
<!--  Pantalla de carga: la rueda de MEDIBOT, la de la web, mientras llega la
      pagina. -->
<div class="pantalla-carga" id="pantallaCarga" aria-hidden="true">
  <svg class="rueda" viewBox="0 0 100 100"><g class="ruleta-marca"><path d="M 53.209 4.112 A 46 46 0 0 1 80.179 15.283 L 63.121 34.906 A 20 20 0 0 0 51.395 30.049 Z"/><path d="M 84.717 19.821 A 46 46 0 0 1 95.888 46.791 L 69.951 48.605 A 20 20 0 0 0 65.094 36.879 Z"/><path d="M 95.888 53.209 A 46 46 0 0 1 84.717 80.179 L 65.094 63.121 A 20 20 0 0 0 69.951 51.395 Z"/><path d="M 80.179 84.717 A 46 46 0 0 1 53.209 95.888 L 51.395 69.951 A 20 20 0 0 0 63.121 65.094 Z"/><path d="M 46.791 95.888 A 46 46 0 0 1 19.821 84.717 L 36.879 65.094 A 20 20 0 0 0 48.605 69.951 Z"/><path d="M 15.283 80.179 A 46 46 0 0 1 4.112 53.209 L 30.049 51.395 A 20 20 0 0 0 34.906 63.121 Z"/><path d="M 4.112 46.791 A 46 46 0 0 1 15.283 19.821 L 34.906 36.879 A 20 20 0 0 0 30.049 48.605 Z"/><path d="M 19.821 15.283 A 46 46 0 0 1 46.791 4.112 L 48.605 30.049 A 20 20 0 0 0 36.879 34.906 Z"/></g></svg>
  <div class="pc-marca"><span class="medi">MEDI</span>BOT</div>
  <div class="pc-tag">PASTILLERO</div>
</div>

<header class="cabecera" id="cabecera">
  <!--  LA TUERCA: abre la configuracion (datos medicos, dosis, horarios,
        historial y estado del sistema). -->
  <button class="tuerca" id="btnAjustes" type="button" aria-label="Abrir la configuración"
          aria-controls="panelAjustes" aria-expanded="false" title="Configuración">
    <svg viewBox="-50 -50 100 100" aria-hidden="true">
      <path class="tuerca-dientes" d="M-7.3 -37.8L-5.3 -46.7A47 47 0 0 1 5.3 -46.7L7.3 -37.8A38.5 38.5 0 0 1 21.5 -31.9L29.3 -36.8A47 47 0 0 1 36.8 -29.3L31.9 -21.5A38.5 38.5 0 0 1 37.8 -7.3L46.7 -5.3A47 47 0 0 1 46.7 5.3L37.8 7.3A38.5 38.5 0 0 1 31.9 21.5L36.8 29.3A47 47 0 0 1 29.3 36.8L21.5 31.9A38.5 38.5 0 0 1 7.3 37.8L5.3 46.7A47 47 0 0 1 -5.3 46.7L-7.3 37.8A38.5 38.5 0 0 1 -21.5 31.9L-29.3 36.8A47 47 0 0 1 -36.8 29.3L-31.9 21.5A38.5 38.5 0 0 1 -37.8 7.3L-46.7 5.3A47 47 0 0 1 -46.7 -5.3L-37.8 -7.3A38.5 38.5 0 0 1 -31.9 -21.5L-36.8 -29.3A47 47 0 0 1 -29.3 -36.8L-21.5 -31.9A38.5 38.5 0 0 1 -7.3 -37.8Z"/>
      <circle class="tuerca-aro" r="31"/>
      <circle class="tuerca-disco" r="27.5"/>
      <path class="tuerca-radios" d="M0 -26L0 26M-26 0L26 0M-18.4 -18.4L18.4 18.4M18.4 -18.4L-18.4 18.4"/>
      <circle class="tuerca-centro" r="3.6"/>
    </svg>
  </button>
  <h1 class="titulo">PILLBOX</h1>
</header>

<main class="tablero" id="app">
  <div class="columna columna-izq" id="colIzq" role="group" aria-label="Compartimientos 1 a 4"></div>

  <!--  EL PASTILLERO, tal cual el diseno. Solo gira la ruleta. -->
  <section class="aparato" aria-label="Pastillero MEDIBOT">
    <div class="aparato-cuerpo">
      <div class="tolva" aria-hidden="true"><div class="tolva-boca"></div></div>
      <div class="ruleta" id="ruleta">
        <div class="ruleta-bisel"></div>
        <div class="ruleta-anillo"></div>
        <div class="ruleta-disco" id="ruletaDisco">
          <svg viewBox="-100 -100 200 200" role="img" aria-label="Ruleta interna de 8 compartimientos">
            <defs>
              <radialGradient id="gDisco" cx="42%" cy="38%" r="70%">
                <stop offset="0" class="st-disco1"/>
                <stop offset="1" class="st-disco2"/>
              </radialGradient>
            </defs>
            <circle r="100" fill="url(#gDisco)"/>
            <circle class="llanta" r="90"/>
            <path class="radios" d="M4.97 -12.01L34.44 -83.15M12.01 -4.97L83.15 -34.44M12.01 4.97L83.15 34.44M4.97 12.01L34.44 83.15M-4.97 12.01L-34.44 83.15M-12.01 4.97L-83.15 34.44M-12.01 -4.97L-83.15 -34.44M-4.97 -12.01L-34.44 -83.15"/>
            <circle class="cubo" r="13"/>
            <g class="cubo-roseta"><circle cx="0.00" cy="-7.00" r="2"/><circle cx="4.95" cy="-4.95" r="2"/><circle cx="7.00" cy="0.00" r="2"/><circle cx="4.95" cy="4.95" r="2"/><circle cx="0.00" cy="7.00" r="2"/><circle cx="-4.95" cy="4.95" r="2"/><circle cx="-7.00" cy="0.00" r="2"/><circle cx="-4.95" cy="-4.95" r="2"/><circle r="2.2"/></g>
          </svg>
        </div>
        <span class="pastilla-cae" id="pastillaCae" aria-hidden="true"></span>
      </div>
      <div class="placa" aria-hidden="true"><span>MEDIBOT</span></div>
      <div class="cajon" aria-hidden="true">
        <span class="cajon-tirador"></span>
        <svg class="cajon-logo" viewBox="-50 -50 100 100">
          <circle r="44"/>
          <circle r="16"/>
          <path d="M0.00 -16.00L0.00 -44.00M11.31 -11.31L31.11 -31.11M16.00 0.00L44.00 0.00M11.31 11.31L31.11 31.11M0.00 16.00L0.00 44.00M-11.31 11.31L-31.11 31.11M-16.00 0.00L-44.00 0.00M-11.31 -11.31L-31.11 -31.11"/>
        </svg>
      </div>
    </div>
  </section>

  <div class="columna columna-der" id="colDer" role="group" aria-label="Compartimientos 5 a 8"></div>

  <div class="acciones">
    <div class="accion accion-izq">
      <button class="btn-dispensar" id="btnDispensar" type="button">DISPENSAR</button>
    </div>
    <div class="accion accion-der">
      <!--  MEDIBOT. Es un <a> y no un boton con window.location: asi se
            puede abrir en otra pestaña con el boton central o con una
            pulsacion larga en el movil. Sin emoji: solo texto. -->
      <a class="btn-medibot" id="linkMedibot" href="#" rel="noopener"
         title="Ir a la interfaz de camaras y movimiento de Medibot">MEDIBOT</a>
    </div>
  </div>
</main>

<div class="avisos" id="avisos" aria-live="polite"></div>

<!--  PANEL DE CONFIGURACION. Se desliza desde la tuerca. Aqui vive toda la
      informacion del sistema: datos de cada compartimiento, dosis,
      horarios, historial y el estado de la conexion. -->
<div class="velo" id="velo"></div>
<aside class="ajustes" id="panelAjustes" role="dialog" aria-modal="true" aria-labelledby="ajustesTitulo" aria-hidden="true" inert>
  <div class="ajustes-cabeza">
    <div>
      <h2 id="ajustesTitulo">Configuración</h2>
      <p>Datos médicos, dosis, horarios, historial y estado</p>
    </div>
    <button class="cerrar" id="btnCerrarAjustes" type="button" aria-label="Cerrar la configuración">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>
    </button>
  </div>
  <div class="pestanas" role="tablist" aria-label="Secciones de la configuración">
    <button class="pestana" role="tab" type="button" id="tabComp" data-pestana="comp" aria-controls="pestanaComp" aria-selected="true">Compartimientos</button>
    <button class="pestana" role="tab" type="button" id="tabHist" data-pestana="hist" aria-controls="pestanaHist" aria-selected="false" tabindex="-1">Historial</button>
    <button class="pestana" role="tab" type="button" id="tabSis" data-pestana="sis" aria-controls="pestanaSis" aria-selected="false" tabindex="-1">Sistema</button>
  </div>
  <div class="ajustes-cuerpo">
    <section id="pestanaComp" role="tabpanel" aria-labelledby="tabComp">
      <div id="listaComp">
        <p class="intro">Elige un compartimiento para registrar su medicamento, la dosis y los horarios de dispensado automático.</p>
        <div id="listaFilas"></div>
      </div>
      <div id="editorComp" class="hidden">
        <button class="volver" id="btnVolverLista" type="button">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 18l-6-6 6-6"/></svg>
          Todos los compartimientos
        </button>
        <div class="editor-cabeza">
          <span class="fila-num" id="editorNum">1</span>
          <div>
            <h3>Compartimiento <span id="editorNumTxt">1</span></h3>
            <p id="editorSub">La ruleta lo sube a la zona de carga para llenarlo.</p>
          </div>
          <span class="badge" id="editorBadge">Pendiente</span>
        </div>
        <form class="bloque" id="formComp" novalidate>
          <h4>Datos médicos</h4>
          <div class="form-grid">
            <div class="form-group ancho"><label for="fMedicamento">Medicamento <b>*</b></label><input type="text" id="fMedicamento" placeholder="Ej: Losartán 50 mg" autocomplete="off"></div>
            <div class="form-group"><label for="fDosis">Dosis <b>*</b></label><input type="text" id="fDosis" placeholder="Ej: 1 tableta" autocomplete="off"></div>
            <div class="form-group"><label for="fNombre">Paciente</label><input type="text" id="fNombre" placeholder="Nombre del paciente" autocomplete="off"></div>
            <div class="form-group ancho"><label for="fNotas">Notas</label><input type="text" id="fNotas" placeholder="Indicaciones, habitación…" autocomplete="off"></div>
          </div>
          <div class="form-error" id="formError" role="alert"></div>
          <div class="btn-group">
            <button class="btn btn-primary" id="btnGuardar" type="submit">Guardar</button>
            <button class="btn btn-danger hidden" id="btnEliminar" type="button">Eliminar</button>
          </div>
        </form>
        <div class="bloque">
          <h4>Horarios de dispensado automático</h4>
          <div id="schedBloqueo" class="vacio-txt hidden">Guarda primero el medicamento para poder programar horarios.</div>
          <div id="schedBloque">
            <div id="schedList"></div>
            <div class="sched-form">
              <input type="time" id="schedHora" aria-label="Hora del dispensado">
              <div class="dias-sel" id="schedDias" role="group" aria-label="Días de la semana"></div>
              <button class="btn btn-primary btn-mini" id="btnAddHorario" type="button">Agregar horario</button>
            </div>
            <p class="aviso">Elige la hora y los días: el pastillero dispensará solo en ese momento y quedará anotado en el historial.</p>
          </div>
        </div>
      </div>
    </section>
    <section id="pestanaHist" role="tabpanel" aria-labelledby="tabHist" class="hidden">
      <p class="intro">Últimas acciones: guardados, horarios y dispensados manuales (MANUAL) y automáticos (AUTO).</p>
      <div class="bloque"><div id="histList" class="vacio-txt">Cargando…</div></div>
    </section>
    <section id="pestanaSis" role="tabpanel" aria-labelledby="tabSis" class="hidden">
      <div class="bloque">
        <h4>Estado</h4>
        <!--  La pildora solo dice "Conectado" o "Desconectado". El puerto y
              el motivo de la caida viajan en el title. Arranca en
              "Desconectado" -en rojo, como lo pinta .serial-pill- porque
              hasta que responda /serial/status no hay ningun Arduino
              confirmado. -->
        <div class="dato"><span class="dato-k">Arduino</span>
          <span class="serial-pill" id="serialPill"
                title="Comprobando la conexion con el Arduino...">Desconectado</span></div>
        <div class="dato"><span class="dato-k">Ruleta</span>
          <span class="dato-v"><span class="serial-pill hidden" id="posPill" title="Posicion de la ruleta">Posición: -</span><span id="sisPosicion">-</span></span></div>
        <div class="dato"><span class="dato-k">Puerto</span><span class="dato-v" id="sisPuerto">-</span></div>
        <div class="dato"><span class="dato-k">Compartimientos configurados</span><span class="dato-v" id="globalBadge">0 / 8</span></div>
        <div class="btn-group" style="margin-top:12px">
          <button class="btn btn-outline btn-mini hidden" id="btnVerificar" type="button" onclick="verificarSync()">Verificar</button>
          <button class="btn btn-outline btn-mini hidden" id="btnReconectar" type="button" onclick="reconectarArduino()">Reconectar</button>
          <button class="btn btn-outline btn-mini" id="btnSisInicio" type="button">Ruleta al inicio</button>
        </div>
      </div>
      <div class="bloque">
        <h4>Apariencia</h4>
        <!--  El texto es la ACCION del boton, no el tema puesto: con el tema
              claro (el de arranque) pone "Modo Oscuro", que es lo que pasa al
              pulsarlo. Lo repinta pintarBotonTema() al cargar. -->
        <button class="btn btn-outline" id="themeToggle" type="button" onclick="alternarTema()"
                title="Cambiar tema claro/oscuro">Modo Oscuro</button>
      </div>
      <div class="bloque">
        <h4>Cómo funciona la ruleta</h4>
        <p class="aviso" style="margin-top:0">Al elegir un compartimiento, la ruleta lo sube arriba, bajo la tolva: es la zona de carga. Al dispensar, parte del inicio (compartimiento 1 arriba), lo baja media vuelta hasta la salida y la pastilla cae al cajón; después completa la vuelta hasta el inicio. La ruleta solo gira hacia delante.</p>
      </div>
    </section>
  </div>
</aside>

<script>
  (function() {
    const TOTAL_COMPARTMENTS = 8;
    const MITAD = TOTAL_COMPARTMENTS / 2;
    const GRADOS = 360 / TOTAL_COMPARTMENTS;
    const DIAS = ['L','M','X','J','V','S','D'];
    const DIAS_LARGOS = ['lunes','martes','miércoles','jueves','viernes','sábado','domingo'];
    const CAMPOS = ['medicamento','dosis','nombre','notas'];
    const REDUCIR = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    //  Segundos de animacion por compartimiento. El motor de verdad
    //  (28BYJ-48, 256 pasos por compartimiento) tarda algo mas; aqui se
    //  acorta para que la animacion no aburra, sin perder el sentido del giro.
    const SEG_POR_COMP = REDUCIR ? 0.08 : 0.5;

    let dataStore = {};   // compartimientos (desde el servidor)
    let horarios = [];    // horarios (desde el servidor)
    let historial = [];   // historial (desde el servidor)
    let seleccionado = null;  // compartimiento elegido (el que se sube a la carga)
    let arriba = 1;           // compartimiento que la ruleta DIBUJADA tiene arriba
    let pasos = 0;            // giro acumulado en compartimientos: solo crece
    let posConocida = false;  // ya se coloco la ruleta donde dice el Arduino
    let enMarcha = 0;         // movimientos de la ruleta en curso o en cola
    let dispensando = false;
    let cola = Promise.resolve();
    let editando = null;      // compartimiento abierto en el panel
    let claveHistorial = null;
    let ultimoFoco = null;
    let confirmarEliminar = 0;

    function el(id) { return document.getElementById(id); }
    function esperar(ms) { return new Promise(res => setTimeout(res, ms)); }
    function mayus(t) { return t.charAt(0).toUpperCase() + t.slice(1); }

    function esc(texto) {
      if (texto === null || texto === undefined) { return ''; }
      const mapa = {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'};
      return String(texto).replace(/[&<>"']/g, m => mapa[m]);
    }

    // ---------- Datos (guardados en la Pi, via backend) ----------
    function cargarTodo() {
      return fetch('/data').then(r => r.json()).then(d => {
        dataStore = d.compartimientos || {};
        horarios = d.horarios || [];
        historial = d.historial || [];
        return d;
      });
    }
    function datosDe(num) {
      const d = dataStore[String(num)];
      return (d && d.completado === true) ? d : null;
    }
    function pedir(url, metodo, cuerpo) {
      const opciones = { method: metodo || 'GET' };
      if (cuerpo !== undefined) {
        opciones.headers = { 'Content-Type': 'application/json' };
        opciones.body = JSON.stringify(cuerpo);
      }
      return fetch(url, opciones).then(r => r.json());
    }

    //  Proxima toma programada de un compartimiento, en texto, o ''.
    function proximaToma(num) {
      const ahora = new Date();
      const hoy = (ahora.getDay() + 6) % 7;   // lunes = 0, como weekday() en Python
      const minAhora = ahora.getHours() * 60 + ahora.getMinutes();
      let mejor = null;
      horarios.forEach(h => {
        if (h.comp !== num || !h.activo) { return; }
        const partes = String(h.hora).split(':');
        const m = parseInt(partes[0], 10) * 60 + parseInt(partes[1], 10);
        (h.dias || []).forEach(d => {
          let dias = (d - hoy + 7) % 7;
          if (dias === 0 && m <= minAhora) { dias = 7; }
          const falta = dias * 1440 + m - minAhora;
          if (!mejor || falta < mejor.falta) { mejor = { falta: falta, dias: dias, dia: d, hora: h.hora }; }
        });
      });
      if (!mejor) { return ''; }
      if (mejor.dias === 0) { return 'hoy ' + mejor.hora; }
      if (mejor.dias === 1) { return 'mañana ' + mejor.hora; }
      return DIAS_LARGOS[mejor.dia] + ' ' + mejor.hora;
    }

    // ---------- Avisos ----------
    function avisar(texto, tipo, accion) {
      const caja = el('avisos');
      const t = document.createElement('div');
      t.className = 'toast ' + (tipo === 'aviso' ? 'aviso-t' : (tipo || 'info'));
      if (tipo === 'error') { t.setAttribute('role', 'alert'); }
      const span = document.createElement('span');
      span.className = 'toast-texto';
      span.textContent = texto;
      t.appendChild(span);
      let quitado = false;
      const quitar = () => {
        if (quitado) { return; }
        quitado = true;
        t.classList.add('fuera');
        setTimeout(() => t.remove(), 320);
      };
      if (accion) {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'toast-accion';
        b.textContent = accion.texto;
        b.addEventListener('click', () => { quitar(); accion.fn(); });
        t.appendChild(b);
      }
      const x = document.createElement('button');
      x.type = 'button';
      x.className = 'toast-cerrar';
      x.setAttribute('aria-label', 'Cerrar aviso');
      x.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>';
      x.addEventListener('click', quitar);
      t.appendChild(x);
      caja.appendChild(t);
      while (caja.children.length > 3) { caja.removeChild(caja.firstChild); }
      setTimeout(quitar, accion ? 8000 : (tipo === 'error' ? 7000 : 4500));
    }

    // ---------- LA RULETA ----------
    //  El compartimiento i esta a (i-1)*45 grados del de arriba, en el
    //  sentido del reloj. Avanzar un compartimiento gira el disco 45 grados
    //  en sentido contrario: sube el siguiente a la zona de carga. Como el
    //  firmware, SOLO avanza: para "volver" completa la vuelta.
    function aplicarGiro(segundos) {
      const disco = el('ruletaDisco');
      disco.style.transitionDuration = segundos + 's';
      disco.style.transform = 'rotate(' + (-pasos * GRADOS) + 'deg)';
    }
    function colocarSinAnimar(n) {
      pasos = ((n - 1) % TOTAL_COMPARTMENTS + TOTAL_COMPARTMENTS) % TOTAL_COMPARTMENTS;
      arriba = n;
      aplicarGiro(0);
    }
    function avanzar(k) {
      k = ((k % TOTAL_COMPARTMENTS) + TOTAL_COMPARTMENTS) % TOTAL_COMPARTMENTS;
      if (!k) { return Promise.resolve(); }
      pasos += k;
      arriba = ((arriba - 1 + k) % TOTAL_COMPARTMENTS) + 1;
      const seg = Math.max(REDUCIR ? 0.05 : 0.4, k * SEG_POR_COMP);
      aplicarGiro(seg);
      return esperar(seg * 1000 + 80);
    }
    function irA(n) { return avanzar(n - arriba); }

    //  Los movimientos van EN FILA: si se pulsan tres compartimientos
    //  seguidos, la ruleta hace los tres giros uno detras de otro.
    function encolar(op) {
      enMarcha++;
      const p = cola.then(op);
      const fin = () => { enMarcha--; };
      cola = p.then(fin, fin);
      return p;
    }

    //  Tras la respuesta del servidor: si hay Arduino y su posicion no es la
    //  dibujada, la ruleta dibujada le sigue (hacia delante).
    function reconciliar(r) {
      if (r && r.pos) { updatePos(r.pos); }
      if (r && r.pos && r.pos.conectado && r.pos.real && r.pos.real !== arriba) {
        return irA(r.pos.real);
      }
      return null;
    }

    // ---------- Elegir un compartimiento: GOTO (sube a la zona de carga) ----------
    function seleccionar(n) {
      if (dispensando) { avisar('Espera a que termine el dispensado.', 'aviso'); return; }
      if (seleccionado === n) { volverAInicio(); return; }
      seleccionado = n;
      pintarBotones();
      const peticion = pedir('/goto', 'POST', { compartimiento: n }).catch(e => ({ error: String(e) }));
      encolar(() => Promise.all([irA(n), peticion]).then(res => {
        const r = res[1];
        if (r && r.error) { avisar('Sin respuesta del servidor: ' + r.error, 'error'); return null; }
        return reconciliar(r);
      })).catch(e => avisar('No se pudo mover la ruleta: ' + e, 'error'));
    }

    // ---------- HOME: compartimiento 1 arriba ----------
    function volverAInicio() {
      if (dispensando) { return; }
      seleccionado = null;
      pintarBotones();
      const peticion = pedir('/home', 'POST', {}).catch(e => ({ error: String(e) }));
      encolar(() => Promise.all([irA(1), peticion]).then(res => {
        const r = res[1];
        if (r && r.error) { avisar('Sin respuesta del servidor: ' + r.error, 'error'); return null; }
        return reconciliar(r);
      })).catch(e => avisar('No se pudo mover la ruleta: ' + e, 'error'));
    }

    // ---------- DISPENSAR ----------
    //  Igual que el firmware: parte del inicio, avanza 'rot' compartimientos
    //  hasta dejar el elegido ABAJO (salida), la pastilla cae al cajon y
    //  luego completa la vuelta hasta el inicio.
    function soltarPastilla() {
      const p = el('pastillaCae');
      p.classList.remove('cayendo');
      void p.offsetWidth;
      p.classList.add('cayendo');
      return esperar(REDUCIR ? 300 : 1200);
    }
    function secuenciaDispensar(n) {
      const rot = (n - 1 + MITAD) % TOTAL_COMPARTMENTS;
      return irA(1)
        .then(() => avanzar(rot))
        .then(soltarPastilla)
        .then(() => avanzar(TOTAL_COMPARTMENTS - rot));
    }
    function pintarBotonDispensar() {
      const b = el('btnDispensar');
      b.disabled = dispensando;
      b.textContent = dispensando ? 'DISPENSANDO…' : 'DISPENSAR';
    }
    function dispensar() {
      const n = seleccionado;
      if (dispensando) { return; }
      if (!n) { avisar('Elige primero el compartimiento que quieres dispensar.', 'aviso'); return; }
      const data = datosDe(n);
      if (!data) {
        avisar('El compartimiento ' + n + ' no tiene medicamento configurado.', 'aviso',
               { texto: 'Configurar', fn: () => abrirAjustes('comp', n) });
        return;
      }
      dispensando = true;
      pintarBotonDispensar();
      const peticion = pedir('/dispense', 'POST', {
        compartimiento: n,
        medicamento: data.medicamento || '',
        nombre: data.nombre || '',
        dosis: data.dosis || ''
      }).catch(e => ({ error: String(e) }));
      encolar(() => Promise.all([secuenciaDispensar(n), peticion]))
        .then(res => {
          const r = res[1];
          if (r.error) { avisar('Error de conexión con el servidor: ' + r.error, 'error'); }
          else {
            const conectado = !!(r.pos && r.pos.conectado);
            avisar(r.message || 'Listo.', r.ok ? 'ok' : (conectado ? 'error' : 'info'));
            if (r.pos) { updatePos(r.pos); }
          }
          seleccionado = null;
          dispensando = false;
          pintarBotones();
          return refreshData();
        })
        .catch(e => avisar('No se pudo dispensar: ' + e, 'error'))
        .finally(() => { dispensando = false; pintarBotonDispensar(); });
    }

    // ---------- Botones de los compartimientos ----------
    function crearBotones() {
      for (let i = 1; i <= TOTAL_COMPARTMENTS; i++) {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'comp';
        b.id = 'comp' + i;
        b.setAttribute('aria-pressed', 'false');
        b.innerHTML = '<span class="comp-titulo">COMPARTIMIENTO ' + i + '</span>' +
          '<span class="comp-med" id="compMed' + i + '">Vacío</span>';
        b.addEventListener('click', () => seleccionar(i));
        el(i <= MITAD ? 'colIzq' : 'colDer').appendChild(b);
      }
    }
    function pintarBotones() {
      let completos = 0;
      for (let i = 1; i <= TOTAL_COMPARTMENTS; i++) {
        const d = datosDe(i);
        const b = el('comp' + i);
        if (d) { completos++; }
        b.setAttribute('aria-pressed', seleccionado === i ? 'true' : 'false');
        el('compMed' + i).textContent = d ? d.medicamento : 'Vacío';
        b.title = 'Compartimiento ' + i + ': ' + (d ? d.medicamento + ', ' + d.dosis : 'sin medicamento') +
          (seleccionado === i ? ' (pulsa otra vez para devolver la ruleta al inicio)' : '');
      }
      el('globalBadge').textContent = completos + ' / ' + TOTAL_COMPARTMENTS;
    }
    function pintarTodo() {
      pintarBotones();
      pintarListaAjustes();
      pintarHistorial();
      if (editando) { pintarEditor(); }
    }

    // ---------- Panel de configuracion (la tuerca) ----------
    function abrirAjustes(pestana, comp) {
      const panel = el('panelAjustes');
      if (!panel.classList.contains('abierto')) {
        ultimoFoco = document.activeElement;
        panel.classList.add('abierto');
        panel.removeAttribute('inert');
        panel.setAttribute('aria-hidden', 'false');
        el('velo').classList.add('abierto');
        el('app').setAttribute('inert', '');
        el('cabecera').setAttribute('inert', '');
        el('btnAjustes').setAttribute('aria-expanded', 'true');
      }
      cambiarPestana(pestana || 'comp');
      if (comp) { abrirEditor(comp); }
      else if ((pestana || 'comp') === 'comp') { cerrarEditor(); }
      setTimeout(() => {
        const destino = comp ? el('fMedicamento') : el('btnCerrarAjustes');
        if (destino) { destino.focus({ preventScroll: true }); }
      }, 80);
    }
    function cerrarAjustes() {
      const panel = el('panelAjustes');
      if (!panel.classList.contains('abierto')) { return; }
      panel.classList.remove('abierto');
      panel.setAttribute('inert', '');
      panel.setAttribute('aria-hidden', 'true');
      el('velo').classList.remove('abierto');
      el('app').removeAttribute('inert');
      el('cabecera').removeAttribute('inert');
      el('btnAjustes').setAttribute('aria-expanded', 'false');
      cerrarEditor();
      const volver = (ultimoFoco && document.body.contains(ultimoFoco)) ? ultimoFoco : el('btnAjustes');
      volver.focus({ preventScroll: true });
    }
    function cambiarPestana(nombre) {
      [['comp', 'tabComp', 'pestanaComp'], ['hist', 'tabHist', 'pestanaHist'], ['sis', 'tabSis', 'pestanaSis']].forEach(t => {
        const activa = t[0] === nombre;
        el(t[1]).setAttribute('aria-selected', activa ? 'true' : 'false');
        el(t[1]).tabIndex = activa ? 0 : -1;
        el(t[2]).classList.toggle('hidden', !activa);
      });
    }
    function pintarListaAjustes() {
      let html = '';
      for (let i = 1; i <= TOTAL_COMPARTMENTS; i++) {
        const d = datosDe(i);
        const prox = d ? proximaToma(i) : '';
        html += '<button type="button" class="fila-comp' + (d ? '' : ' vacia') + '" data-comp="' + i + '">' +
          '<span class="fila-num">' + i + '</span>' +
          '<span class="fila-datos"><span class="fila-titulo">' + (d ? esc(d.medicamento) : 'Sin datos') + '</span>' +
          '<span class="fila-sub">' + (d ? 'Dosis ' + esc(d.dosis) + ' · ' + (prox ? 'próxima ' + prox : 'sin horarios') : 'Compartimiento ' + i) + '</span></span>' +
          '<span class="badge' + (d ? ' completed' : '') + '">' + (d ? 'Completado' : 'Pendiente') + '</span>' +
          '</button>';
      }
      el('listaFilas').innerHTML = html;
    }
    //  Abrir un compartimiento en el panel lo sube a la zona de carga, como
    //  antes: es donde se rellena.
    function abrirEditor(num) {
      editando = num;
      el('listaComp').classList.add('hidden');
      el('editorComp').classList.remove('hidden');
      const d = dataStore[String(num)] || null;
      CAMPOS.forEach(f => {
        const inp = el('f' + mayus(f));
        inp.value = d && d[f] ? d[f] : '';
        inp.removeAttribute('aria-invalid');
      });
      el('formError').textContent = '';
      restaurarEliminar();
      pintarEditor();
      if (seleccionado !== num && !dispensando) { seleccionar(num); }
    }
    function cerrarEditor() {
      editando = null;
      el('editorComp').classList.add('hidden');
      el('listaComp').classList.remove('hidden');
    }
    function pintarEditor() {
      const num = editando;
      if (!num) { return; }
      const d = datosDe(num);
      el('editorNum').textContent = num;
      el('editorNumTxt').textContent = num;
      el('editorBadge').textContent = d ? 'Completado' : 'Pendiente';
      el('editorBadge').classList.toggle('completed', !!d);
      el('btnGuardar').textContent = d ? 'Guardar cambios' : 'Guardar';
      el('btnEliminar').classList.toggle('hidden', !d);
      el('schedBloqueo').classList.toggle('hidden', !!d);
      el('schedBloque').classList.toggle('hidden', !d);
      pintarHorarios();
    }
    function guardarComp(e) {
      if (e) { e.preventDefault(); }
      const num = editando;
      if (!num) { return; }
      const reg = {};
      CAMPOS.forEach(f => { reg[f] = el('f' + mayus(f)).value.trim(); });
      let primero = null;
      ['medicamento', 'dosis'].forEach(f => {
        const inp = el('f' + mayus(f));
        if (!reg[f]) { inp.setAttribute('aria-invalid', 'true'); primero = primero || inp; }
        else { inp.removeAttribute('aria-invalid'); }
      });
      if (primero) {
        el('formError').textContent = 'Medicamento y dosis son obligatorios.';
        primero.focus();
        return;
      }
      el('formError').textContent = '';
      const b = el('btnGuardar');
      b.disabled = true;
      pedir('/compartimiento/' + num, 'POST', reg).then(res => {
        if (!res.ok) { el('formError').textContent = res.message || 'No se pudo guardar.'; return null; }
        avisar('Compartimiento ' + num + ' guardado: ' + reg.medicamento + ' (' + reg.dosis + ').', 'ok');
        return refreshData();
      }).catch(err => { el('formError').textContent = 'Error de conexión: ' + err; })
        .finally(() => { b.disabled = false; });
    }
    function restaurarEliminar() {
      confirmarEliminar = 0;
      const b = el('btnEliminar');
      b.textContent = 'Eliminar';
      b.classList.remove('confirmar');
    }
    function eliminarComp() {
      const num = editando;
      const b = el('btnEliminar');
      if (!num) { return; }
      //  Dos pulsaciones: la primera pide confirmacion durante 4 segundos.
      if (Date.now() - confirmarEliminar > 4000) {
        confirmarEliminar = Date.now();
        b.textContent = '¿Seguro? Pulsa otra vez';
        b.classList.add('confirmar');
        setTimeout(() => { if (confirmarEliminar && Date.now() - confirmarEliminar >= 4000) { restaurarEliminar(); } }, 4100);
        return;
      }
      restaurarEliminar();
      pedir('/compartimiento/' + num, 'DELETE').then(res => {
        if (res && res.ok === false) { avisar(res.message || 'No se pudo eliminar.', 'error'); return null; }
        CAMPOS.forEach(f => { el('f' + mayus(f)).value = ''; });
        avisar('Compartimiento ' + num + ' vaciado: datos y horarios borrados.', 'info');
        return refreshData();
      }).catch(err => avisar('Error: ' + err, 'error'));
    }

    // ---------- Horarios de dispensado automatico ----------
    function crearDias() {
      el('schedDias').innerHTML = DIAS.map((d, i) =>
        '<label class="day-check" title="' + mayus(DIAS_LARGOS[i]) + '"><input type="checkbox" value="' + i + '"><span>' + d + '</span></label>').join('');
    }
    function pintarHorarios() {
      const num = editando;
      const lista = el('schedList');
      if (!num) { return; }
      const propios = horarios.filter(h => h.comp === num);
      if (!propios.length) { lista.innerHTML = '<p class="vacio-txt">Sin horarios programados.</p>'; return; }
      lista.innerHTML = propios.map(h =>
        '<div class="sched-row">' +
        '<span class="hora">' + esc(h.hora) + '</span>' +
        '<span class="dias">' + (h.dias || []).map(d => DIAS[d]).join(' ') + '</span>' +
        '<span class="estado' + (h.activo ? '' : ' off') + '">' + (h.activo ? 'Activo' : 'Pausado') + '</span>' +
        '<span class="sched-acciones">' +
        '<button type="button" class="btn btn-outline btn-mini" data-accion="pausar" data-id="' + h.id + '">' + (h.activo ? 'Pausar' : 'Activar') + '</button>' +
        '<button type="button" class="btn btn-danger btn-mini" data-accion="quitar" data-id="' + h.id + '">Quitar</button>' +
        '</span></div>').join('');
    }
    function addHorario() {
      const num = editando;
      const hora = el('schedHora').value;
      const dias = Array.from(document.querySelectorAll('#schedDias input:checked')).map(c => parseInt(c.value, 10));
      if (!hora) { avisar('Elige una hora.', 'aviso'); el('schedHora').focus(); return; }
      if (!dias.length) { avisar('Elige al menos un día.', 'aviso'); return; }
      pedir('/horarios', 'POST', { comp: num, hora: hora, dias: dias }).then(res => {
        if (!res.ok) { avisar(res.message || 'No se pudo guardar el horario.', 'error'); return null; }
        avisar('Horario programado: ' + hora + ' (' + dias.map(d => DIAS[d]).join(' ') + ').', 'ok');
        document.querySelectorAll('#schedDias input:checked').forEach(c => { c.checked = false; });
        return refreshData();
      }).catch(e => avisar('Error: ' + e, 'error'));
    }
    function accionHorario(e) {
      const b = e.target.closest('button[data-accion]');
      if (!b) { return; }
      const id = b.getAttribute('data-id');
      const quitar = b.getAttribute('data-accion') === 'quitar';
      pedir('/horarios/' + id, quitar ? 'DELETE' : 'PATCH').then(res => {
        if (res && res.ok === false) { avisar(res.message || 'No se pudo cambiar el horario.', 'error'); return null; }
        return refreshData();
      }).catch(err => avisar('Error: ' + err, 'error'));
    }

    // ---------- Historial ----------
    function pintarHistorial() {
      const caja = el('histList');
      if (!historial.length) { caja.className = 'vacio-txt'; caja.textContent = 'Sin acciones registradas.'; return; }
      caja.className = '';
      caja.innerHTML = historial.slice(0, 50).map(h =>
        '<div class="hist-row"><span class="hist-marca ' + esc(h.tipo) + '"></span>' +
        '<div class="hist-cuerpo"><div class="hist-linea">' +
        '<span class="tipo ' + esc(h.tipo) + '">' + esc(h.tipo) + '</span>' +
        '<span class="hist-comp">Comp ' + esc(h.comp) + '</span>' +
        '<span class="res">' + esc(h.resultado || '') + '</span></div>' +
        (h.detalle ? '<div class="hist-detalle">' + esc(h.detalle) + '</div>' : '') +
        '<div class="ts">' + esc(h.ts) + '</div></div></div>').join('');
    }
    function claveDe(h) { return h ? [h.ts, h.tipo, h.comp, h.detalle].join('|') : ''; }
    //  Entradas que han aparecido desde la ultima lectura (lo nuevo va primero).
    function entradasNuevas() {
      if (claveHistorial === null) { claveHistorial = claveDe(historial[0]); return []; }
      const nuevas = [];
      for (const h of historial) {
        if (claveDe(h) === claveHistorial) { break; }
        nuevas.push(h);
      }
      claveHistorial = claveDe(historial[0]);
      return nuevas;
    }

    // ---------- Estado de conexion y sincronizacion con el Arduino ----------
    function updatePos(pos) {
      const pill = document.getElementById('posPill');
      pill.classList.remove('sync', 'desync', 'neutro');
      el('sisPosicion').textContent = pos && pos.detalle ? pos.detalle : '-';
      el('sisPosicion').classList.toggle('hidden', !!(pos && pos.conectado));
      if (!pos || !pos.conectado) { pill.classList.add('hidden'); return; }
      pill.classList.remove('hidden');
      pill.title = pos.detalle || '';
      if (pos.real == null) {
        pill.textContent = 'Posición: sin verificar';
        pill.classList.add('neutro');
      } else if (pos.sincronizado || pos.esperado == null) {
        pill.textContent = 'Sincronizado: comp ' + pos.real;
        pill.classList.add('sync');
      } else {
        pill.textContent = 'Desincronizado: web ' + pos.esperado + ' / Arduino ' + pos.real;
        pill.classList.add('desync');
      }
      seguirAlArduino(pos);
    }
    //  La ruleta dibujada sigue a la de verdad cuando esta quieta: al cargar
    //  la pagina se coloca sin animar; despues, si el Arduino se movio por su
    //  cuenta (horario automatico, reinicio...), gira hasta alli.
    function seguirAlArduino(pos) {
      if (!pos || !pos.conectado || !pos.real) { return; }
      if (enMarcha > 0 || dispensando) { return; }
      if (seleccionado && seleccionado !== pos.real) { seleccionado = null; pintarBotones(); }
      if (!posConocida) {
        posConocida = true;
        colocarSinAnimar(pos.real);
        return;
      }
      if (pos.real !== arriba) { encolar(() => irA(pos.real)); }
    }
    function pintarSistema(s) {
      el('sisPuerto').textContent = s && s.puerto ? s.puerto + ' · ' + (s.baud || '') + ' baud' :
        (!s ? 'Sin respuesta del servidor' : (s.hub === false ? 'Hub serial apagado' : 'Ninguno'));
    }

    let ultimaAutoReconexion = 0;
    function refreshSerial() {
      fetch('/serial/status').then(r => r.json()).then(s => {
        const pill = document.getElementById('serialPill');
        const btnR = document.getElementById('btnReconectar');
        const btnV = document.getElementById('btnVerificar');
        //  La pildora solo dice CONECTADO o DESCONECTADO. El detalle (que
        //  puerto es, si el hub esta apagado, si se esta reintentando) va al
        //  title: quien lo necesite lo tiene encima al pasar el raton.
        if (s.conectado) {
          pill.textContent = 'Conectado';
          pill.title = 'Arduino conectado en ' + (s.puerto || 'puerto desconocido');
          pill.classList.add('ok');
          btnR.classList.add('hidden');
          btnV.classList.remove('hidden');
        } else {
          pill.textContent = 'Desconectado';
          pill.classList.remove('ok');
          btnR.classList.remove('hidden');
          btnV.classList.add('hidden');
          // AUTO-RECONEXION: sin tocar botones. Si el hub esta vivo pero el
          // Arduino se cayo (p.ej. microcaida del USB al mover el motor), se
          // pide reconectar solo, como maximo una vez cada 6 s.
          const ahora = Date.now();
          if (s.hub !== false && ahora - ultimaAutoReconexion > 6000) {
            ultimaAutoReconexion = ahora;
            pill.title = 'Sin conexion con el Arduino: reintentando...';
            fetch('/serial/reconnect', { method: 'POST' })
              .then(r => r.json()).then(() => refreshSerial())
              .catch(e => console.warn('[pillbox] reconexion automatica', e));
          } else {
            pill.title = s.hub === false
              ? 'El hub serial esta apagado'
              : 'No se encuentra ningun Arduino en el puerto serie';
          }
        }
        pintarSistema(s);
        updatePos(s.pos);
      }).catch(e => {
        const pill = document.getElementById('serialPill');
        pill.textContent = 'Desconectado';
        pill.classList.remove('ok');
        pill.title = 'Sin respuesta del servidor del pastillero';
        pintarSistema(null);
        console.warn('[pillbox] /serial/status', e);
      });
    }

    // Verifica la posicion REAL preguntando al Arduino (GETPOS) y la compara.
    window.verificarSync = function() {
      const pill = document.getElementById('posPill');
      pill.classList.remove('hidden'); pill.textContent = 'Verificando...';
      pedir('/arduino/estado').then(pos => updatePos(pos))
        .catch(e => avisar('No se pudo verificar la posición: ' + e, 'error'));
    };

    window.reconectarArduino = function() {
      //  El texto sigue siendo "Desconectado" -que es la verdad hasta que el
      //  Arduino conteste-; que se esta reintentando se cuenta en el title.
      const pill = document.getElementById('serialPill');
      pill.title = 'Reconectando con el Arduino...';
      fetch('/serial/reconnect', { method: 'POST' })
        .then(r => r.json()).then(() => refreshSerial())
        .catch(() => refreshSerial());
    };

    // Refresco periodico: capta los dispensados automaticos y los ANIMA.
    function refreshData() {
      return cargarTodo().then(() => {
        const nuevas = entradasNuevas();
        pintarTodo();
        nuevas.filter(h => h.tipo === 'AUTO').reverse().forEach(h => {
          const comp = Number(h.comp);
          avisar('Dispensado automático: compartimiento ' + comp + (h.detalle ? ' · ' + h.detalle : ''), 'ok');
          if (!dispensando && comp >= 1 && comp <= TOTAL_COMPARTMENTS) {
            if (seleccionado) { seleccionado = null; pintarBotones(); }
            encolar(() => secuenciaDispensar(comp));
          }
        });
      }).catch(e => console.warn('[pillbox] /data', e));
    }

    function prepararEventos() {
      el('btnAjustes').addEventListener('click', () => abrirAjustes('comp'));
      el('btnCerrarAjustes').addEventListener('click', cerrarAjustes);
      el('velo').addEventListener('click', cerrarAjustes);
      el('btnDispensar').addEventListener('click', dispensar);
      el('btnSisInicio').addEventListener('click', volverAInicio);
      el('btnVolverLista').addEventListener('click', () => { cerrarEditor(); el('tabComp').focus(); });
      el('listaFilas').addEventListener('click', e => {
        const f = e.target.closest('.fila-comp');
        if (f) { abrirEditor(Number(f.getAttribute('data-comp'))); }
      });
      el('formComp').addEventListener('submit', guardarComp);
      el('btnEliminar').addEventListener('click', eliminarComp);
      el('btnAddHorario').addEventListener('click', addHorario);
      el('schedList').addEventListener('click', accionHorario);
      const tabs = ['tabComp', 'tabHist', 'tabSis'];
      tabs.forEach((id, i) => {
        el(id).addEventListener('click', () => cambiarPestana(el(id).getAttribute('data-pestana')));
        el(id).addEventListener('keydown', e => {
          if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') { return; }
          const otro = el(tabs[(i + (e.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length]);
          cambiarPestana(otro.getAttribute('data-pestana'));
          otro.focus();
        });
      });
      document.addEventListener('keydown', e => {
        if (e.key === 'Escape') { cerrarAjustes(); }
      });
    }

    // ---------- Pantalla de carga ----------
    //  Se quita en cuanto arranca la interfaz, pero no antes de medio segundo
    //  desde que empezo a cargar: en la red de casa seria un parpadeo.
    function quitarPantallaCarga() {
      const p = document.getElementById('pantallaCarga');
      if (!p) return;
      setTimeout(() => p.classList.add('fuera'), Math.max(0, 500 - performance.now()));
    }

    function init() {
      //  Lo primero: si algo de lo de abajo fallara, que no se quede tapado.
      quitarPantallaCarga();
      crearBotones();
      crearDias();
      prepararEventos();
      colocarSinAnimar(1);
      pintarTodo();
      cargarTodo().then(() => { entradasNuevas(); pintarTodo(); })
        .catch(e => avisar('No se pudieron cargar los datos del pastillero: ' + e, 'error'));
      refreshSerial();
      // Consultar la posicion real del Arduino al cargar (deja el indicador listo).
      setTimeout(function() {
        pedir('/arduino/estado').then(updatePos).catch(e => console.warn('[pillbox] /arduino/estado', e));
      }, 600);
      setInterval(refreshSerial, 4000);
      setInterval(refreshData, 12000);
      // Verificacion periodica de la posicion real (detecta desincronizacion,
      // ej. tras un reinicio del Arduino o un dispensado automatico).
      setInterval(function() {
        pedir('/arduino/estado').then(updatePos).catch(e => console.warn('[pillbox] /arduino/estado', e));
      }, 20000);
    }

    // ===== Tema claro / oscuro (arranca en CLARO) =====
    //  Se aplica ANTES de nada (ver el script de la cabeza) para que no haya
    //  un fogonazo blanco al cargar con el tema oscuro puesto. Aqui solo queda
    //  el cambio manual y poner bien el texto del boton.
    const CLAVE_TEMA = 'pillbox-theme';
    const TEMA_POR_DEFECTO = 'light';

    function temaActual() {
      return document.documentElement.getAttribute('data-theme') ||
             TEMA_POR_DEFECTO;
    }

    function pintarBotonTema() {
      const b = document.getElementById('themeToggle');
      if (b) { b.textContent = temaActual() === 'dark' ? 'Modo Claro' : 'Modo Oscuro'; }
    }

    function alternarTema() {
      const nuevo = temaActual() === 'dark' ? 'light' : 'dark';
      document.documentElement.setAttribute('data-theme', nuevo);
      try { localStorage.setItem(CLAVE_TEMA, nuevo); }
      catch (e) { console.warn('[pillbox] no se pudo recordar el tema', e); }
      pintarBotonTema();
    }

    // ===== Enlace a Medibot =====
    //  El servidor puede fijar la URL (util detras de un tunel, donde cada
    //  interfaz tiene su propio subdominio y los puertos no valen). Si no la
    //  fija, se deduce del mismo host con el puerto 5000, que es lo correcto
    //  en la red local.
    const URL_MEDIBOT = '__URL_MEDIBOT__';
    function prepararEnlaceMedibot() {
      const a = document.getElementById('linkMedibot');
      if (!a) { return; }
      a.href = URL_MEDIBOT ||
               (location.protocol + '//' + location.hostname + ':5000/');
    }

    window.alternarTema = alternarTema;
    pintarBotonTema();
    prepararEnlaceMedibot();
    init();
  })();
</script>
</body>
</html>"""


#  HUELLA de la interfaz: cambia en cuanto cambia una sola letra del HTML.
#  Sirve para saber, sin adivinar, que version esta viendo un navegador:
#      curl -sI http://<ip>:5001/ | grep Pillbox-Build
#  Si el navegador enseña una huella distinta a la del servidor, esta viendo
#  una copia guardada en cache y no la que sirve la Pi.
BUILD_WEB = hashlib.sha256(HTML_PAGE.encode("utf-8")).hexdigest()[:8]


@app.after_request
def _sin_cache(respuesta):
    """Prohibe guardar en cache la interfaz y las respuestas de la API.

    EL FALLO QUE ARREGLA
    --------------------
    La web es UNA sola pagina con el JavaScript escrito DENTRO del HTML. Flask
    no mandaba ninguna cabecera de cache, asi que el navegador era libre de
    guardarse esa pagina y reutilizarla durante horas o dias. Al actualizar el
    codigo en la Pi, el navegador seguia ejecutando el HTML viejo: el servidor
    esta bien y la interfaz sale exactamente igual que antes.

    Se ve clarisimo en el Pastillero, porque los datos SI se refrescan (/data
    y /serial/status son peticiones nuevas que no se cachean) mientras que los
    textos, los colores y el tema se quedan como estaban: lo que cambia viene
    del servidor, lo que no cambia viene de la copia guardada.

    Un Ctrl+F5 lo arregla a mano, pero hay que repetirlo en cada dispositivo y
    en un movil casi nadie sabe hacerlo. Con no-store el navegador no puede
    quedarse una copia vieja.

    Vision_MEDIBOT.py ya llevaba esta cabecera desde que le paso lo mismo; el
    Pastillero se habia quedado sin ella.
    """
    respuesta.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    respuesta.headers["Pragma"] = "no-cache"          # proxies y HTTP/1.0
    respuesta.headers["Expires"] = "0"
    respuesta.headers["X-Pillbox-Build"] = BUILD_WEB
    return respuesta


def _puerto_web_ocupado():
    """True si YA hay algo escuchando en el puerto web (doble arranque)."""
    import socket
    try:
        with socket.create_connection(("127.0.0.1", WEB_PORT), timeout=0.4):
            return True
    except OSError:
        return False


def _avisar_cuando_este_listo():
    """Hilo: espera a que Flask levante y COMPRUEBA que se llega desde la red,
    no solo desde esta maquina. Asi el usuario sabe al instante si podra
    abrirlo en el movil (y si no, por que)."""
    for _ in range(40):
        time.sleep(0.25)
        if _puerto_web_ocupado():
            break
    else:
        return
    ok, detalle = medibot_red.accesible_en_lan(WEB_PORT)
    if ok:
        print("Comprobado: Pillbox responde desde la red " + f"({detalle}).")
    else:
        print(f"AVISO: Pillbox NO parece accesible desde otros equipos: {detalle}.")


def iniciar_servidor_web():
    """Arranca el servidor web y VERIFICA los fallos tipicos en vez de morir
    con un traceback: si el puerto ya esta ocupado (doble arranque) lo dice
    claro y con las URLs correctas para abrir el Pillbox que ya corre."""
    if _puerto_web_ocupado():
        print(f"ERROR: el puerto {WEB_PORT} ya esta ocupado: ya hay OTRO "
              "Pillbox corriendo en esta maquina.")
        print("Abrelo en el navegador con una de estas direcciones:")
        print(medibot_red.texto_urls(WEB_PORT))
        print("Para reiniciarlo, cierra antes el proceso viejo.")
        raise SystemExit(1)

    print(f"Pillbox arrancando (datos en {DATA_FILE})")
    #  La huella de la interfaz, para poder comprobar si el navegador esta
    #  viendo ESTA version o una guardada en cache (F12 > Red > cabecera
    #  X-Pillbox-Build, o curl -sI http://<ip>:5001/).
    print(f"Version de la interfaz: {BUILD_WEB}")
    print(f"Escuchando en {WEB_HOST}:{WEB_PORT} (todas las interfaces).")
    print("Entra desde ESTE equipo o desde el movil/otro PC de la misma red:")
    print(medibot_red.texto_urls(WEB_PORT))

    threading.Thread(target=_avisar_cuando_este_listo, daemon=True).start()
    try:
        # threaded=True: cada peticion va en su hilo; un dispensado lento (que
        # espera al Arduino) NO bloquea el resto de la interfaz web.
        app.run(host=WEB_HOST, port=WEB_PORT, threaded=True, use_reloader=False)
    except OSError as e:
        print(f"\nERROR: no se pudo abrir el puerto {WEB_PORT} ({e}).")
        print("Si otro programa usa el puerto, cierralo y reintenta.")
        raise SystemExit(1)


if __name__ == "__main__":
    serial_connect()
    threading.Thread(target=bucle_horarios, daemon=True).start()
    iniciar_servidor_web()
