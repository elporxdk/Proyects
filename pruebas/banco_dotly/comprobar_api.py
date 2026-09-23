#!/usr/bin/env python3
"""Comprueba que lo que DOTLY llama de sus librerias EXISTE de verdad.

El banco compila contra sustitutos escritos a mano (shim/). Si un sustituto
tiene un metodo que la libreria real no tiene, el banco no se entera y el
fallo sale en el IDE, en la maquina de quien graba. Asi que aqui se bajan las
cabeceras reales (y se cachean en .libs/):

  - arduino-esp32 2.0.17 y 3.3.0 (WiFi en modo AP, WebServer, DNSServer,
    Preferences, Wire, ESP, el ADC)
  - LiquidCrystal_I2C, la del gestor de librerias (Frank de Brabander,
    github.com/johnrickman/LiquidCrystal_I2C)

y se mira que cada metodo que el sketch llama sobre esos objetos, cada
funcion suelta del ADC y cada constante esten declarados en ellas.

Sin red no se puede comprobar: avisa y no falla.
"""
import os
import re
import subprocess
import sys
import urllib.request

VERSIONES = ["2.0.17", "3.3.0"]
CORE = [
    "libraries/WiFi/src/WiFiAP.h",
    "libraries/WiFi/src/WiFiGeneric.h",
    "libraries/WiFi/src/WiFiType.h",
    "libraries/DNSServer/src/DNSServer.h",
    "libraries/WebServer/src/WebServer.h",
    "libraries/WebServer/src/HTTP_Method.h",
    "libraries/Preferences/src/Preferences.h",
    "libraries/Wire/src/Wire.h",
    "cores/esp32/Esp.h",
    "cores/esp32/esp32-hal-adc.h",
]
CORE_OPCIONAL = ["libraries/Network/src/NetworkInterface.h"]   # solo en la 3.x
LCD = "https://raw.githubusercontent.com/johnrickman/LiquidCrystal_I2C/master/LiquidCrystal_I2C.h"

OBJETOS = {"WiFi": "core", "webServer": "core", "dnsServer": "core", "prefs": "core",
           "ESP": "core", "Wire": "core", "lcd": "lcd"}
FUNCIONES_ADC = ["analogRead", "analogReadMilliVolts", "analogReadResolution", "analogSetPinAttenuation"]
# HTTP_GET y HTTP_POST no se buscan: el core los genera con una macro dentro de
# http_parser.h (HTTP_##name), asi que el nombre no aparece escrito en ninguna.
CONSTANTES = ["ADC_11db", "WIFI_AP", "CONTENT_LENGTH_UNKNOWN"]
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".libs")


def bajar(url, nombre):
    destino = os.path.join(CACHE, nombre)
    if os.path.exists(destino):
        return open(destino, encoding="utf-8", errors="replace").read()
    with urllib.request.urlopen(url, timeout=40) as r:
        texto = r.read().decode("utf-8", "replace")
    os.makedirs(CACHE, exist_ok=True)
    open(destino, "w", encoding="utf-8").write(texto)
    return texto


def cabeceras_core(version):
    textos = []
    for ruta in CORE:
        url = f"https://raw.githubusercontent.com/espressif/arduino-esp32/{version}/{ruta}"
        textos.append(bajar(url, version + "_" + ruta.replace("/", "_")))
    for ruta in CORE_OPCIONAL:
        try:
            url = f"https://raw.githubusercontent.com/espressif/arduino-esp32/{version}/{ruta}"
            textos.append(bajar(url, version + "_" + ruta.replace("/", "_")))
        except Exception:
            pass
    return "\n".join(textos)


def declaradas(texto):
    return set(re.findall(r"\b(\w+)\s*\(", texto))


def main():
    if "--" not in sys.argv:
        raise SystemExit("uso: comprobar_api.py sketch.ino -- <flags de g++>")
    corte = sys.argv.index("--")
    sketch, flags = sys.argv[1], sys.argv[corte + 1:]

    try:
        core = {v: cabeceras_core(v) for v in VERSIONES}
        lcd = bajar(LCD, "LiquidCrystal_I2C.h")
    except Exception as e:
        print(f"  (sin acceso a las cabeceras reales: {e}; no se comprueba)")
        return 0

    pre = subprocess.run(["g++", "-E", "-x", "c++"] + flags + [sketch], capture_output=True, text=True)
    if pre.returncode != 0:
        print(pre.stderr[-1500:])
        raise SystemExit("no se pudo preprocesar el sketch")
    # solo el codigo del sketch, no el de los sustitutos
    codigo = pre.stdout
    llamadas = {}
    for obj, metodo in re.findall(r"\b(%s)\s*\.\s*(\w+)\s*\(" % "|".join(OBJETOS), codigo):
        llamadas.setdefault(obj, set()).add(metodo)

    fallos = 0
    for obj, metodos in sorted(llamadas.items()):
        for m in sorted(metodos):
            if OBJETOS[obj] == "lcd":
                if m not in declaradas(lcd):
                    print(f"  lcd.{m}() NO existe en LiquidCrystal_I2C")
                    fallos += 1
                continue
            for v in VERSIONES:
                if m not in declaradas(core[v]):
                    print(f"  {obj}.{m}() NO existe en el core ESP32 {v}")
                    fallos += 1
    for f in FUNCIONES_ADC:
        if re.search(r"\b%s\s*\(" % f, codigo):
            for v in VERSIONES:
                if f not in declaradas(core[v]):
                    print(f"  {f}() NO existe en el core ESP32 {v}")
                    fallos += 1
    for c in CONSTANTES:
        if re.search(r"\b%s\b" % c, codigo):
            for v in VERSIONES:
                if not re.search(r"\b%s\b" % c, core[v]):
                    print(f"  {c} NO existe en el core ESP32 {v}")
                    fallos += 1

    # createChar() de esta libreria pide un buffer NO const: un const uint8_t[]
    # no compila en el IDE (el sustituto del banco lo dejaria pasar).
    if "createChar(uint8_t, uint8_t[])" not in lcd.replace("  ", " "):
        print("  la firma de createChar() ha cambiado en LiquidCrystal_I2C: revisa pantallaMostrar()")
        fallos += 1

    if fallos:
        return 1
    n = sum(len(m) for m in llamadas.values())
    print(f"  {n} metodos, {len(FUNCIONES_ADC)} funciones del ADC y {len(CONSTANTES)} constantes: "
          f"OK en arduino-esp32 {', '.join(VERSIONES)} y LiquidCrystal_I2C")
    return 0


if __name__ == "__main__":
    sys.exit(main())
