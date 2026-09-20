#!/usr/bin/env python3
"""Comprueba que las APIs del core ESP32 que usa un sketch EXISTEN de verdad.

El banco compila contra sustitutos escritos a mano (shim/, shim_red/), asi que
si un sustituto tiene un metodo que el core real NO tiene, o que cambio de
nombre entre versiones, aqui no se entera nadie: el fallo sale en el IDE, en la
maquina de quien graba. Paso justo eso con mDNS:

    core 2.x:  MDNS.IP(i)
    core 3.x:  MDNS.address(i)      <- 'class MDNSResponder' has no member named 'IP'

Este comprobador baja las cabeceras de verdad de arduino-esp32 (y las cachea),
preprocesa el sketch con cada version -para que los #if de version se resuelvan
igual que en el IDE- y verifica que cada metodo que se llama sobre MDNS, WiFi,
HTTPClient o Preferences aparece en ellas.

Sin red no se puede comprobar: avisa y no falla, para poder trabajar sin
conexion.
"""
import os
import re
import subprocess
import sys
import urllib.request

VERSIONES = {"2": "2.0.17", "3": "3.3.0"}
CABECERAS = [
    "libraries/ESPmDNS/src/ESPmDNS.h",
    "libraries/WiFi/src/WiFiSTA.h",
    "libraries/WiFi/src/WiFiGeneric.h",
    "libraries/WiFi/src/WiFiType.h",
    "libraries/HTTPClient/src/HTTPClient.h",
    "libraries/Preferences/src/Preferences.h",
]
# Cabeceras que solo existen en una de las dos ramas (el cliente TCP se movio
# a la libreria Network en la 3.x). Si una no esta, se ignora.
CABECERAS_OPCIONALES = [
    "libraries/WiFi/src/WiFiClient.h",
    "libraries/Network/src/NetworkClient.h",
]
OBJETOS = {"MDNS": "mDNS", "WiFi": "WiFi", "http": "HTTPClient", "prefs": "Preferences"}
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".libs", "esp32api")


def bajar(version, ruta):
    destino = os.path.join(CACHE, version, ruta.replace("/", "_"))
    if os.path.exists(destino):
        return open(destino, encoding="utf-8", errors="replace").read()
    url = f"https://raw.githubusercontent.com/espressif/arduino-esp32/{version}/{ruta}"
    with urllib.request.urlopen(url, timeout=40) as r:
        texto = r.read().decode("utf-8", "replace")
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    open(destino, "w", encoding="utf-8").write(texto)
    return texto


def metodos_del_core(version):
    """Todos los identificadores declarados como funcion en esas cabeceras."""
    nombres = set()
    for ruta in CABECERAS:
        nombres |= set(re.findall(r"\b(\w+)\s*\(", bajar(version, ruta)))
    for ruta in CABECERAS_OPCIONALES:
        try:
            nombres |= set(re.findall(r"\b(\w+)\s*\(", bajar(version, ruta)))
        except Exception:
            pass
    return nombres


def llamadas_del_sketch(sketch, mayor, flags):
    """Metodos llamados sobre los objetos del core, con los #if ya resueltos."""
    cmd = ["g++", "-E", "-x", "c++", f"-DESP_ARDUINO_VERSION_MAJOR={mayor}"] + flags + [sketch]
    pre = subprocess.run(cmd, capture_output=True, text=True)
    if pre.returncode != 0:
        print(pre.stderr[-1500:])
        raise SystemExit(f"no se pudo preprocesar {sketch}")
    usados = {}
    for obj, metodo in re.findall(r"\b(%s)\s*\.\s*(\w+)\s*\(" % "|".join(OBJETOS), pre.stdout):
        usados.setdefault(obj, set()).add(metodo)
    return usados


def main():
    if "--flags" not in sys.argv:
        raise SystemExit("uso: comprobar_api_esp32.py sketch.ino [...] --flags <flags de g++>")
    corte = sys.argv.index("--flags")
    sketches = sys.argv[1:corte]
    flags = sys.argv[corte + 1:]

    try:
        core = {v: metodos_del_core(tag) for v, tag in VERSIONES.items()}
    except Exception as e:
        print(f"  (sin acceso a las cabeceras del core ESP32: {e}; no se comprueba)")
        return 0

    fallos = 0
    for sketch in sketches:
        for mayor, tag in VERSIONES.items():
            for obj, metodos in llamadas_del_sketch(sketch, mayor, flags).items():
                faltan = sorted(m for m in metodos if m not in core[mayor])
                for m in faltan:
                    print(f"  {os.path.basename(sketch)}: {obj}.{m}() NO existe en el core "
                          f"ESP32 {tag} ({OBJETOS[obj]})")
                    fallos += 1
    if fallos:
        print("  Solucion: usar el metodo que si exista, o elegirlo con "
              "#if ESP_ARDUINO_VERSION_MAJOR >= 3")
        return 1
    print(f"  API del core ESP32 OK en {', '.join(VERSIONES.values())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
