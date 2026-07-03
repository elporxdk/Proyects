#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pastillero MEDIBOT - Servidor web + comunicación Serial con Arduino.
- Sirve la interfaz de 8 compartimientos.
- Al hacer clic en un compartimiento, envía GOTO al Arduino para posicionar la ruleta.
- Al dispensar, envía DISPENSE.
- Consulta la posición actual al cargar la página y la resalta en el menú.
- Los datos de pacientes se guardan en localStorage del navegador.

Requisitos: pip install flask pyserial
Ejecutar:   python3 pastillero.py
Acceso:     http://<ip>:5001
"""

import time
import threading
from flask import Flask, request, jsonify

# ================= CONFIGURACION SERIAL =================
SERIAL_ENABLED = True
SERIAL_PORT = None       # None = autodetectar (Bullseye: /dev/ttyUSB0 o /dev/ttyACM0; Windows: COMx)
SERIAL_BAUD = 9600       # DEBE coincidir con Serial.begin() del sketch de Arduino
N_COMPARTIMIENTOS = 8

_serial_conn = None
_serial_lock = threading.Lock()


def _autodetect_serial_port():
    """Elige el puerto del Arduino. Prioriza por nombre y descripción."""
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
            s += 40      # Arduino Uno/Mega nativo (Bullseye)
        elif "ttyusb" in dev:
            s += 35      # CH340 / FTDI (Bullseye)
        elif dev.startswith("com"):
            s += 30      # Windows
        # descartar puertos internos de la Raspberry (UART de la placa)
        if "ttyama" in dev or "ttys0" in dev or "serial0" in dev:
            s -= 50
        return s

    ports.sort(key=score, reverse=True)
    best = ports[0]
    return best.device if score(best) > 0 else best.device


def serial_connect():
    """Abre el puerto serial (autodetecta si SERIAL_PORT es None)."""
    global _serial_conn
    if not SERIAL_ENABLED:
        return None
    try:
        import serial
    except Exception as e:
        print(f"AVISO: pyserial no instalado ({e}). Instala con: pip install pyserial")
        return None

    port = SERIAL_PORT or _autodetect_serial_port()
    if port is None:
        print("AVISO: no se detectó ningún Arduino. En Bullseye suele ser "
              "/dev/ttyUSB0 o /dev/ttyACM0; en Windows COMx. Fija SERIAL_PORT a mano.")
        return None
    try:
        _serial_conn = serial.Serial(port, SERIAL_BAUD, timeout=1)
        time.sleep(2)  # el Arduino se reinicia al abrir el puerto; esperar a que arranque
        print(f"Arduino conectado en {port} @ {SERIAL_BAUD} baudios")
    except Exception as e:
        print(f"AVISO: no se pudo abrir {port}: {e}")
        _serial_conn = None
    return _serial_conn


def serial_command(msg, wait=4.0, until=("DISPENSADO", "ERR", "POS")):
    """Envía una orden y devuelve las respuestas del Arduino (lista de líneas).
    Termina de leer al recibir una línea que empiece por alguno de 'until'."""
    with _serial_lock:
        if _serial_conn is None:
            print(f"[SIMULADO] {msg}")
            return ["(sin arduino) " + msg]
        try:
            _serial_conn.reset_input_buffer()
            _serial_conn.write((msg + "\n").encode())
            respuestas = []
            t0 = time.time()
            while time.time() - t0 < wait:
                linea = _serial_conn.readline().decode(errors="ignore").strip()
                if linea:
                    respuestas.append(linea)
                    if any(linea.startswith(u) for u in until):
                        break
            return respuestas
        except Exception as e:
            return [f"ERROR serial: {e}"]


# ================= SERVIDOR WEB =================
app = Flask(__name__)


@app.route("/")
def index():
    return HTML_PAGE


@app.route("/dispense", methods=["POST"])
def dispense():
    data = request.get_json(silent=True) or {}
    try:
        comp = int(data.get("compartimiento", 0))
    except (TypeError, ValueError):
        comp = 0
    if not (1 <= comp <= N_COMPARTIMIENTOS):
        return jsonify({"ok": False, "message": f"Compartimiento inválido: {comp}"}), 400

    medic = str(data.get("medicamento", "")).strip()
    nombre = str(data.get("nombre", "")).strip()

    resp = serial_command(f"DISPENSE,{comp}")
    conectado = _serial_conn is not None
    ack = " | ".join(resp) if resp else "sin respuesta"
    print(f"DISPENSAR comp={comp} medic='{medic}' paciente='{nombre}' -> Arduino: {ack}")

    if conectado:
        msg = f"Dispensado en compartimiento {comp}"
        if medic:
            msg += f": {medic}"
        if nombre:
            msg += f" (paciente {nombre})"
        msg += f".  Arduino: {ack}"
    else:
        msg = (f"No hay Arduino conectado. Orden simulada: DISPENSE,{comp}"
               f" ({medic or 'sin medicamento'}).")
    return jsonify({"ok": conectado, "message": msg, "arduino": resp, "compartimiento": comp})


@app.route("/goto", methods=["POST"])
def goto():
    data = request.get_json(silent=True) or {}
    try:
        comp = int(data.get("compartimiento", 0))
    except (TypeError, ValueError):
        comp = 0
    if not (1 <= comp <= N_COMPARTIMIENTOS):
        return jsonify({"ok": False, "message": "Compartimiento inválido"}), 400
    resp = serial_command(f"GOTO,{comp}", until=("POS", "ERR"))
    return jsonify({"ok": _serial_conn is not None, "arduino": resp, "compartimiento": comp})


@app.route("/position", methods=["GET"])
def get_position():
    """Devuelve la posición actual del motor (compartimiento activo)."""
    resp = serial_command("GETPOS", wait=1, until=("POS", "ERR"))
    comp = None
    for line in resp:
        if line.startswith("POS,"):
            try:
                comp = int(line.split(",")[1])
            except:
                pass
            break
    if comp is None:
        return jsonify({"ok": False, "position": None}), 404
    return jsonify({"ok": True, "position": comp})


@app.route("/serial/status")
def serial_status():
    port = None
    try:
        if _serial_conn is not None:
            port = _serial_conn.port
    except Exception:
        pass
    return jsonify({"conectado": _serial_conn is not None, "puerto": port, "baud": SERIAL_BAUD})


@app.route("/serial/reconnect", methods=["POST"])
def serial_reconnect():
    serial_connect()
    return serial_status()


# ================= HTML (interfaz completa con sincronización y resaltado) =================
HTML_PAGE = """<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Pastillero MEDIBOT - Compartimientos</title>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; font-family: 'Segoe UI', Roboto, system-ui, sans-serif; }
    body { background: #f4f7fc; min-height: 100vh; display: flex; justify-content: center; align-items: center; padding: 20px; }
    .app { max-width: 1200px; width: 100%; background: white; border-radius: 32px; box-shadow: 0 20px 60px rgba(0,20,40,0.12); padding: 30px 35px 40px; transition: all .3s ease; }
    .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 28px; flex-wrap: wrap; gap: 10px; }
    .header h1 { font-size: 26px; font-weight: 600; color: #0b2b4a; letter-spacing: -.3px; display: flex; align-items: center; gap: 8px; }
    .header h1 span { background: #eef3f9; padding: 4px 14px; border-radius: 40px; font-size: 16px; font-weight: 500; color: #1f5a8e; }
    .serial-pill { font-size: 13px; font-weight: 600; padding: 6px 14px; border-radius: 30px; background: #fdecea; color: #b3323d; }
    .serial-pill.ok { background: #d4edda; color: #0e6b3e; }
    .btn-back { background: #eef3f9; border: none; padding: 8px 18px; border-radius: 30px; font-size: 14px; font-weight: 500; color: #1f5a8e; cursor: pointer; display: flex; align-items: center; gap: 6px; transition: .2s; }
    .btn-back:hover { background: #dce6f0; }
    .btn-back.hidden { display: none; }
    .compartments-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(210px,1fr)); gap: 22px; margin-top: 10px; }
    .compartment-card { background: #fff; border-radius: 20px; padding: 18px 16px 16px; box-shadow: 0 4px 16px rgba(0,0,0,.04); border: 1px solid #e9edf4; cursor: pointer; transition: all .2s ease; display: flex; flex-direction: column; min-height: 150px; position: relative; }
    .compartment-card:hover { transform: translateY(-4px); box-shadow: 0 12px 28px rgba(0,40,80,.08); border-color: #b6cae0; }
    .compartment-number { font-size: 22px; font-weight: 700; color: #1f3a57; letter-spacing: -.2px; margin-bottom: 10px; display: flex; justify-content: space-between; align-items: center; }
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
    .saved-data { background: #f9fbfd; padding: 24px 26px; border-radius: 24px; border: 1px solid #e9edf4; margin-bottom: 20px; }
    .saved-data .row { display: flex; flex-wrap: wrap; gap: 8px 28px; padding: 8px 0; border-bottom: 1px solid #e9edf4; }
    .saved-data .row:last-child { border-bottom: none; }
    .saved-data .field-label { font-weight: 500; color: #1f3a57; min-width: 120px; }
    .saved-data .field-value { color: #0b2b4a; word-break: break-word; }
    .status-badge { display: inline-block; padding: 4px 16px; border-radius: 30px; font-size: 13px; font-weight: 500; background: #d4edda; color: #0e6b3e; }
    .hidden { display: none !important; }
    .flex-wrap { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
    /* Resaltado de compartimiento activo */
    .compartment-card.active { border: 2px solid #1f5a8e; box-shadow: 0 8px 24px rgba(31,90,142,0.2); }
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
    <h1>&#129658; Pastillero MEDIBOT <span id="globalBadge">8</span></h1>
    <div class="flex-wrap">
      <span class="serial-pill" id="serialPill">Arduino: comprobando...</span>
      <button class="btn-back" id="btnBackMain" onclick="goToMain()">&#8592; Volver al menú</button>
    </div>
  </div>

  <div id="mainMenu">
    <div class="compartments-grid" id="compGrid"></div>
  </div>

  <div id="detailView" class="detail-view">
    <div class="detail-header">
      <div>
        <h2 id="detailTitle">Compartimiento <span id="detailNumber">1</span></h2>
        <span class="sub" id="detailSub">Completa los datos del paciente</span>
      </div>
      <div class="flex-wrap">
        <span id="detailStatusBadge" class="status-badge hidden">Completado</span>
      </div>
    </div>

    <div id="formContainer">
      <div class="form-grid" id="formGrid">
        <div class="form-group"><label>Nombre</label><input type="text" id="fNombre" placeholder="Nombre del paciente"></div>
        <div class="form-group"><label>Edad</label><input type="text" id="fEdad" placeholder="Ej: 45 años"></div>
        <div class="form-group"><label>Peso y altura</label><input type="text" id="fPesoAltura" placeholder="70 kg / 1.75 m"></div>
        <div class="form-group"><label>Diagnóstico</label><input type="text" id="fDiagnostico" placeholder="Diagnóstico"></div>
        <div class="form-group"><label>Dosis</label><input type="text" id="fDosis" placeholder="Dosis"></div>
        <div class="form-group"><label>Medicamento</label><input type="text" id="fMedicamento" placeholder="Medicamento"></div>
        <div class="form-group"><label>Hora</label><input type="text" id="fHora" placeholder="Ej: 08:00"></div>
        <div class="form-group"><label>Habitación / Área</label><input type="text" id="fHabitacion" placeholder="Habitación o área"></div>
      </div>
      <div class="btn-group">
        <button class="btn btn-success" id="btnCompletar">&#10004; Completado</button>
        <button class="btn btn-outline" id="btnCancelarEdicion" onclick="cancelEdit()">Cancelar</button>
      </div>
      <div style="margin-top:6px;font-size:13px;color:#6f8aa8;" id="formHelper">Completa todos los campos y presiona "Completado" para guardar.</div>
    </div>

    <div id="savedContainer" class="hidden">
      <div class="saved-data" id="savedDataDisplay"></div>
      <div class="btn-group">
        <button class="btn btn-warning" id="btnEditar">&#9998; Editar</button>
        <button class="btn btn-danger" id="btnEliminar">&#128465; Eliminar</button>
        <button class="btn btn-primary" id="btnDispensar">&#128138; Dispensar</button>
      </div>
    </div>

    <div style="margin-top:24px;">
      <button class="btn btn-back" onclick="goToMain()">&#8592; Volver al menú</button>
    </div>
  </div>
</div>

<script>
  (function() {
    const STORAGE_KEY = 'compartimentosData';
    const TOTAL_COMPARTMENTS = 8;
    let currentCompartment = 1;
    let isEditing = false;
    let dataStore = loadData();
    let activeCompartment = null;   // para resaltado

    function loadData() {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) { try { return JSON.parse(raw); } catch (e) { return {}; } }
      return {};
    }
    function saveData() { localStorage.setItem(STORAGE_KEY, JSON.stringify(dataStore)); }
    function getCompData(num) { return dataStore[num] || null; }
    function setCompData(num, data) { dataStore[num] = data; saveData(); }
    function deleteCompData(num) { delete dataStore[num]; saveData(); }

    function escapeHtml(text) {
      if (!text) return '—';
      const map = {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'};
      return String(text).replace(/[&<>"']/g, m => map[m]);
    }

    // Resaltar compartimiento activo
    function highlightActive(comp) {
      document.querySelectorAll('.compartment-card').forEach(el => {
        el.classList.remove('active');
      });
      if (comp) {
        const card = document.querySelector(`.compartment-card[data-comp="${comp}"]`);
        if (card) {
          card.classList.add('active');
          card.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
        activeCompartment = comp;
      }
    }

    // Renderizar menú principal
    function renderMainMenu() {
      const grid = document.getElementById('compGrid');
      grid.innerHTML = '';
      for (let i = 1; i <= TOTAL_COMPARTMENTS; i++) {
        const data = getCompData(i);
        const card = document.createElement('div');
        card.className = 'compartment-card';
        card.dataset.comp = i;
        let badgeClass = 'badge', badgeText = 'Pendiente';
        if (data && data.completado === true) {
          badgeClass += ' completed';
          badgeText = '✔ Completado';
        }
        let previewHtml;
        if (data && data.completado === true) {
          previewHtml = `<div class="preview-data">
            <div><span class="label">Nombre</span> <span class="value">${escapeHtml(data.nombre || '—')}</span></div>
            <div><span class="label">Medicamento</span> <span class="value">${escapeHtml(data.medicamento || '—')}</span></div>
            <div><span class="label">Hora</span> <span class="value">${escapeHtml(data.hora || '—')}</span></div>
          </div>`;
        } else {
          previewHtml = '<div class="preview-empty">Sin datos</div>';
        }
        card.innerHTML = `<div class="compartment-number">COMPARTIMIENTO ${i} <span class="${badgeClass}">${badgeText}</span></div>${previewHtml}`;
        card.addEventListener('click', () => openCompartment(i));
        grid.appendChild(card);
      }
      // Aplicar resaltado si hay uno activo
      if (activeCompartment) {
        highlightActive(activeCompartment);
      }
    }

    // Abrir compartimiento: primero mover motor, luego abrir detalle
    function openCompartment(num) {
      // Mostrar feedback visual (opcional)
      const card = document.querySelector(`.compartment-card[data-comp="${num}"]`);
      if (card) card.style.opacity = '0.7';

      fetch('/goto', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ compartimiento: num })
      })
      .then(r => r.json())
      .then(data => {
        if (data.ok) {
          highlightActive(num);
        } else {
          alert('No se pudo mover el motor. Verifica la conexión serial.');
        }
      })
      .catch(err => {
        alert('Error de conexión: ' + err);
      })
      .finally(() => {
        if (card) card.style.opacity = '1';
        // Abrir la vista detalle (siempre)
        currentCompartment = num;
        isEditing = false;
        document.getElementById('mainMenu').style.display = 'none';
        const dv = document.getElementById('detailView');
        dv.classList.add('active');
        dv.style.display = 'block';
        document.getElementById('detailNumber').textContent = num;
        document.getElementById('btnBackMain').classList.remove('hidden');

        const data = getCompData(num);
        if (data && data.completado === true) {
          showSavedData(num, data);
        } else {
          showForm(num, data || null);
        }
      });
    }

    // Mostrar formulario
    function showForm(num, existingData) {
      document.getElementById('formContainer').classList.remove('hidden');
      document.getElementById('savedContainer').classList.add('hidden');
      document.getElementById('detailStatusBadge').classList.add('hidden');
      const fields = ['nombre','edad','pesoAltura','diagnostico','dosis','medicamento','hora','habitacion'];
      if (existingData) {
        fields.forEach(f => {
          const el = document.getElementById('f'+f.charAt(0).toUpperCase()+f.slice(1));
          if (el) el.value = existingData[f] || '';
        });
        document.getElementById('detailSub').textContent = 'Editando datos del paciente';
        document.getElementById('btnCancelarEdicion').classList.remove('hidden');
        isEditing = true;
      } else {
        fields.forEach(f => {
          const el = document.getElementById('f'+f.charAt(0).toUpperCase()+f.slice(1));
          if (el) el.value = '';
        });
        document.getElementById('detailSub').textContent = 'Completa los datos del paciente';
        document.getElementById('btnCancelarEdicion').classList.add('hidden');
        isEditing = false;
      }
      document.getElementById('btnCompletar').textContent = isEditing ? '💾 Guardar cambios' : '✔ Completado';
      document.getElementById('btnCompletar').onclick = function() { handleComplete(num); };
    }

    // Mostrar datos guardados
    function showSavedData(num, data) {
      document.getElementById('formContainer').classList.add('hidden');
      document.getElementById('savedContainer').classList.remove('hidden');
      const badge = document.getElementById('detailStatusBadge');
      badge.classList.remove('hidden');
      badge.textContent = '✔ Completado';
      document.getElementById('detailSub').textContent = 'Datos del paciente';
      const fields = [
        {label:'Nombre',key:'nombre'},{label:'Edad',key:'edad'},{label:'Peso y altura',key:'pesoAltura'},
        {label:'Diagnóstico',key:'diagnostico'},{label:'Dosis',key:'dosis'},{label:'Medicamento',key:'medicamento'},
        {label:'Hora',key:'hora'},{label:'Habitación / Área',key:'habitacion'}
      ];
      let html = '';
      fields.forEach(f => {
        html += `<div class="row"><span class="field-label">${f.label}</span><span class="field-value">${escapeHtml(data[f.key] || '—')}</span></div>`;
      });
      document.getElementById('savedDataDisplay').innerHTML = html;

      document.getElementById('btnEditar').onclick = function() { showForm(num, getCompData(num) || null); };
      document.getElementById('btnEliminar').onclick = function() {
        if (confirm('Eliminar todos los datos del COMPARTIMIENTO ' + num + '?')) {
          deleteCompData(num);
          goToMain();
        }
      };
      document.getElementById('btnDispensar').onclick = function() { dispensar(num, data); };
    }

    // Dispensar: llama al endpoint /dispense
    function dispensar(num, data) {
      const btn = document.getElementById('btnDispensar');
      const original = btn.textContent;
      btn.disabled = true;
      btn.textContent = '⏳ Dispensando...';
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
      .then(res => {
        alert((res.ok ? '💊 ' : '⚠️ ') + res.message);
      })
      .catch(e => alert('Error de conexión: ' + e))
      .finally(() => {
        btn.disabled = false;
        btn.textContent = original;
      });
    }

    function handleComplete(num) {
      const fields = ['nombre','edad','pesoAltura','diagnostico','dosis','medicamento','hora','habitacion'];
      const record = {};
      let allFilled = true;
      fields.forEach(f => {
        const el = document.getElementById('f'+f.charAt(0).toUpperCase()+f.slice(1));
        const val = el ? el.value.trim() : '';
        record[f] = val;
        if (val === '') allFilled = false;
      });
      if (!allFilled) {
        alert('Por favor, completa todos los campos antes de guardar.');
        return;
      }
      record.completado = true;
      setCompData(num, record);
      showSavedData(num, record);
    }

    function cancelEdit() {
      const num = currentCompartment;
      const data = getCompData(num);
      if (data && data.completado === true) showSavedData(num, data);
      else showForm(num, null);
    }

    function goToMain() {
      document.getElementById('mainMenu').style.display = 'block';
      const dv = document.getElementById('detailView');
      dv.classList.remove('active');
      dv.style.display = 'none';
      document.getElementById('btnBackMain').classList.add('hidden');
      renderMainMenu();
      // Actualizar posición y resaltado al volver
      fetch('/position')
        .then(r => r.json())
        .then(data => {
          if (data.ok && data.position) {
            highlightActive(data.position);
          }
        })
        .catch(() => {});
    }

    // Estado de conexión serial
    function refreshSerial() {
      fetch('/serial/status')
        .then(r => r.json())
        .then(s => {
          const pill = document.getElementById('serialPill');
          if (s.conectado) {
            pill.textContent = 'Arduino: ' + s.puerto + ' ✔';
            pill.classList.add('ok');
          } else {
            pill.textContent = 'Arduino: sin conexión';
            pill.classList.remove('ok');
          }
        })
        .catch(() => {});
    }

    // Inicialización
    function init() {
      renderMainMenu();
      document.getElementById('detailView').style.display = 'none';
      document.getElementById('detailView').classList.remove('active');
      document.getElementById('btnBackMain').classList.add('hidden');
      // Obtener posición actual y resaltar
      fetch('/position')
        .then(r => r.json())
        .then(data => {
          if (data.ok && data.position) {
            highlightActive(data.position);
          }
        })
        .catch(() => {});
      refreshSerial();
      setInterval(refreshSerial, 4000);
    }

    // Exponer funciones globales
    window.goToMain = goToMain;
    window.cancelEdit = cancelEdit;
    window.openCompartment = openCompartment;

    init();
  })();
</script>
</body>
</html>"""


if __name__ == "__main__":
    serial_connect()
    print("Pastillero MEDIBOT en http://0.0.0.0:5001")
    app.run(host="0.0.0.0", port=5001, threaded=True, use_reloader=False)