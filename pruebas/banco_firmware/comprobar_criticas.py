#!/usr/bin/env python3
"""Comprueba que dentro de portENTER_CRITICAL/portEXIT_CRITICAL solo haya
asignaciones a memoria.

Por que existe este script
--------------------------
En un ESP32, portENTER_CRITICAL corta las interrupciones del nucleo. Si ahi
dentro se llama a algo que pide un mutex o que espera (WiFi.RSSI(),
WiFi.localIP(), Wire, Serial, delay...), el nucleo se queda colgado CON LAS
INTERRUPCIONES APAGADAS y a los 300 ms el equipo se reinicia solo:

    Guru Meditation Error: Core 0 panic'ed (Interrupt wdt timeout on CPU0)

Eso paso de verdad: g_net.rssi = WiFi.RSSI() estaba dentro del bloqueo y el
triaje se reiniciaba en bucle nada mas conectarse a la WiFi. El fallo no lo
ve el compilador y no lo ve el banco de pruebas (en el PC no hay watchdog de
interrupciones), asi que lo vigila este script.

Uso:  python3 comprobar_criticas.py fichero.ino [fichero2.ino ...]
"""
import re
import sys
import pathlib

ENTER = "portENTER_CRITICAL"
EXIT = "portEXIT_CRITICAL"

# Lo que NO puede aparecer dentro del bloqueo, y por que.
PROHIBIDO = [
    (r"\bWiFi\s*\.", "WiFi.*: pide el mutex del driver de WiFi"),
    (r"\bMDNS\s*\.", "MDNS.*: consulta la red y espera"),
    (r"\bWire\s*\.", "Wire.*: el bus I2C espera al periferico"),
    (r"\bSerial\s*\.", "Serial.*: escribe por UART y puede bloquear"),
    (r"\bu8g2\s*\.", "u8g2.*: habla por SPI con la pantalla"),
    (r"\bprefs\s*\.", "prefs.*: escribe en la flash"),
    (r"\bparticleSensor\s*\.", "particleSensor.*: habla por I2C"),
    (r"\bhttp\s*\.", "HTTPClient: abre sockets y espera"),
    (r"\bdelay\s*\(", "delay(): espera con las interrupciones cortadas"),
    (r"\bvTaskDelay\s*\(", "vTaskDelay(): cede el turno; prohibido aqui"),
    (r"\bdeserializeJson\s*\(", "deserializeJson(): reserva memoria"),
    (r"\bxSemaphore\w*\s*\(", "xSemaphore*: pedir un semaforo bloquea"),
    (r"\bmalloc\s*\(|\bcalloc\s*\(|\brealloc\s*\(|\bfree\s*\(", "reserva de memoria: usa un mutex interno"),
    (r"\bString\s*\(|\.c_str\s*\(", "String: reserva memoria"),
    (r"\bnetProbarIP\s*\(|\bnetLeerJson\s*\(|\bnetArrancar\s*\(", "funcion de red: habla por WiFi"),
    (r"\bsensorBegin\s*\(|\bsensorConfigure\s*\(|\bsensorRecover\s*\(|\bi2cScan\s*\(", "funcion del sensor: habla por I2C"),
]


def sin_comentarios(texto):
    """Deja el codigo pero conserva las posiciones (los comentarios pasan a
    espacios), para que los numeros de linea sigan cuadrando."""
    fuera = []
    i, n = 0, len(texto)
    while i < n:
        if texto.startswith("//", i):
            j = texto.find("\n", i)
            j = n if j < 0 else j
            fuera.append(" " * (j - i))
            i = j
        elif texto.startswith("/*", i):
            j = texto.find("*/", i + 2)
            j = n if j < 0 else j + 2
            fuera.append("".join(c if c == "\n" else " " for c in texto[i:j]))
            i = j
        elif texto[i] in "\"'":
            comilla = texto[i]
            j = i + 1
            while j < n and texto[j] != comilla:
                j += 2 if texto[j] == "\\" else 1
            j = min(j + 1, n)
            fuera.append("".join(c if c == "\n" else " " for c in texto[i:j]))
            i = j
        else:
            fuera.append(texto[i])
            i += 1
    return "".join(fuera)


def revisar(ruta):
    texto = pathlib.Path(ruta).read_text(encoding="utf-8")
    codigo = sin_comentarios(texto)
    fallos = []

    marcas = sorted(
        [(m.start(), "in") for m in re.finditer(re.escape(ENTER), codigo)]
        + [(m.start(), "out") for m in re.finditer(re.escape(EXIT), codigo)]
    )
    abierto = None
    for pos, tipo in marcas:
        if tipo == "in":
            if abierto is None:
                abierto = codigo.find(")", pos) + 1
        else:
            if abierto is None:
                fallos.append((codigo.count("\n", 0, pos) + 1,
                               "portEXIT_CRITICAL sin su portENTER_CRITICAL"))
                continue
            tramo = codigo[abierto:pos]
            for patron, motivo in PROHIBIDO:
                for m in re.finditer(patron, tramo):
                    linea = codigo.count("\n", 0, abierto + m.start()) + 1
                    fallos.append((linea, "%s  ->  %s" % (m.group(0).strip(), motivo)))
            abierto = None
    if abierto is not None:
        fallos.append((codigo.count("\n", 0, abierto) + 1,
                       "portENTER_CRITICAL sin cerrar"))

    lineas = texto.splitlines()
    for linea, motivo in sorted(set(fallos)):
        cruda = lineas[linea - 1].strip() if 0 < linea <= len(lineas) else ""
        print("%s:%d: DENTRO DEL BLOQUEO: %s" % (ruta, linea, motivo))
        if cruda:
            print("        %s" % cruda)
    return len(set(fallos))


def main():
    if len(sys.argv) < 2:
        print("uso: comprobar_criticas.py fichero.ino [...]")
        return 2
    total = sum(revisar(r) for r in sys.argv[1:])
    if total:
        print("\n%d problema(s). Entre portENTER_CRITICAL y portEXIT_CRITICAL solo"
              " pueden ir asignaciones a memoria: lee el dato ANTES en una"
              " variable local y dentro del bloqueo copialo." % total)
        return 1
    print("Secciones criticas: OK (solo asignaciones a memoria)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
