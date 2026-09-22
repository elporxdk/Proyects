#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MEDIBOT - Certificado propio para servir la web por HTTPS en casa
=================================================================
PARA QUE HACE FALTA
-------------------
Para HABLAR por el altavoz del robot, la pagina necesita tu microfono, y los
navegadores solo lo dan en "contexto seguro": HTTPS, o localhost. Por
http://<ip-de-la-pi>:5000 no hay forma, por mucho que el robot este listo.

Hasta ahora eso obligaba a entrar por el tunel de Cloudflare, que es un
servicio de fuera. Con esto no hace falta NADA externo: la propia Pi se
genera un certificado y sirve tambien por HTTPS en la red de casa.

EL AVISO DEL NAVEGADOR
----------------------
El certificado se lo firma la Pi a si misma, asi que la primera vez el
navegador avisa ("la conexion no es privada"). Se acepta una vez
(Configuracion avanzada -> Continuar) y ya queda aceptado para ese
dispositivo. A partir de ahi la pagina ES contexto seguro y el microfono
funciona. No es un fallo: es lo que pasa con cualquier certificado que no
haya firmado una autoridad de internet, y firmarlo por fuera es justo lo
que no queremos.

POR QUE openssl Y NO UNA LIBRERIA
---------------------------------
Misma razon que `arecord` para el audio: openssl ya viene en Raspberry Pi
OS. La alternativa (el paquete `cryptography`) son decenas de megas que hay
que compilar en una Pi. Aqui se lanza un comando y ya.

SE REHACE SOLO
--------------
El certificado lleva dentro las IPs de la Pi. Si cambia de IP (un router
que reparte por DHCP lo hace cada dos por tres) el certificado se vuelve a
generar solo, para que siga cuadrando con la direccion por la que entras.
"""

import datetime
import os
import re
import shutil
import subprocess

#  Donde se guardan. Junto al codigo, en una carpeta aparte para no
#  mezclarlos con los .py (y que .gitignore los deje fuera: una clave
#  privada no se sube a ningun repositorio).
CARPETA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "certificados")
CERTIFICADO = os.path.join(CARPETA, "medibot.crt")
CLAVE = os.path.join(CARPETA, "medibot.key")

#  Diez anos. Es un certificado de casa: que caduque solo sirve para que un
#  dia deje de funcionar sin que nadie se acuerde de por que.
DIAS = 3650


def hay_openssl():
    return shutil.which("openssl") is not None


def _nombres(ips):
    """El subjectAltName: por que direcciones vale el certificado.

    Van las IPs de la Pi y tambien 'medibot.local' y localhost, para que
    valga por mDNS y desde la propia Pi sin avisos extra."""
    partes = ["DNS:medibot", "DNS:medibot.local", "DNS:localhost", "IP:127.0.0.1"]
    for ip in ips:
        entrada = f"IP:{ip}"
        if entrada not in partes:
            partes.append(entrada)
    return ",".join(partes)


def ips_del_certificado(certificado=CERTIFICADO):
    """Las IPs que lleva dentro un certificado ya hecho.

    Sirve para saber si sigue valiendo o si la Pi ha cambiado de direccion.
    Si no se puede leer, se devuelve None y quien llama lo rehace: es mas
    barato generar otro que quedarse con uno que no cuadra."""
    if not os.path.exists(certificado) or not hay_openssl():
        return None
    try:
        r = subprocess.run(["openssl", "x509", "-in", certificado, "-noout", "-text"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return set(re.findall(r"IP Address:([0-9.]+)", r.stdout))


def caduca(certificado=CERTIFICADO):
    """Cuando caduca, o None si no se sabe."""
    if not os.path.exists(certificado) or not hay_openssl():
        return None
    try:
        r = subprocess.run(["openssl", "x509", "-in", certificado, "-noout", "-enddate"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"notAfter=(.+)", r.stdout or "")
    if not m:
        return None
    try:
        return datetime.datetime.strptime(m.group(1).strip(), "%b %d %H:%M:%S %Y %Z")
    except ValueError:
        return None


def hace_falta_rehacerlo(ips, certificado=CERTIFICADO, clave=CLAVE, ahora=None):
    """(hace_falta, motivo). Aparte para poder probarlo sin generar nada."""
    if not (os.path.exists(certificado) and os.path.exists(clave)):
        return True, "no habia certificado"
    tenia = ips_del_certificado(certificado)
    if tenia is None:
        return True, "el certificado no se pudo leer"
    faltan = {str(i) for i in ips} - tenia
    if faltan:
        return True, f"la Pi tiene direcciones nuevas ({', '.join(sorted(faltan))})"
    fin = caduca(certificado)
    ahora = ahora or datetime.datetime.utcnow()
    if fin is not None and fin <= ahora + datetime.timedelta(days=30):
        return True, "estaba a punto de caducar"
    return False, ""


def generar(ips, certificado=CERTIFICADO, clave=CLAVE):
    """Hace un certificado nuevo. Devuelve (ok, motivo)."""
    if not hay_openssl():
        return False, ("falta 'openssl'; instalalo con: "
                       "sudo apt install openssl")
    os.makedirs(os.path.dirname(certificado), exist_ok=True)
    orden = [
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
        "-keyout", clave, "-out", certificado,
        "-days", str(DIAS), "-subj", "/CN=medibot",
        "-addext", f"subjectAltName={_nombres(ips)}",
        #  Sin esto, algunos navegadores se quejan de un certificado de CA
        #  usado como certificado de servidor.
        "-addext", "basicConstraints=critical,CA:FALSE",
        "-addext", "keyUsage=critical,digitalSignature,keyEncipherment",
        "-addext", "extendedKeyUsage=serverAuth",
    ]
    try:
        r = subprocess.run(orden, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"no se pudo ejecutar openssl: {e}"
    if r.returncode != 0:
        ultima = (r.stderr or "").strip().splitlines()
        return False, (ultima[-1] if ultima else "openssl fallo sin decir nada")
    #  La clave privada, solo para su dueno. Aunque sea un cacharro de casa,
    #  una clave legible por todo el mundo no tiene ninguna gracia.
    try:
        os.chmod(clave, 0o600)
    except OSError:
        pass
    return True, ""


def asegurar(ips, certificado=CERTIFICADO, clave=CLAVE):
    """Deja un certificado listo para usar. Devuelve (rutas, motivo).

    rutas es (certificado, clave), o None si no se pudo. Reutiliza el que
    haya mientras cuadre con las IPs de ahora y no este por caducar."""
    hace_falta, porque = hace_falta_rehacerlo(ips, certificado, clave)
    if not hace_falta:
        return (certificado, clave), ""
    ok, motivo = generar(ips, certificado, clave)
    if not ok:
        return None, motivo
    return (certificado, clave), porque


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import medibot_red
    ips = [ip for _, ip in medibot_red.listar_ips_lan()]
    print("IPs de esta maquina:", ", ".join(ips) or "(ninguna)")
    rutas, motivo = asegurar(ips)
    if rutas:
        print("Certificado:", rutas[0])
        print("Clave:      ", rutas[1])
        if motivo:
            print("Se rehizo porque:", motivo)
        print("Caduca:", caduca(rutas[0]))
        print("Vale para IPs:", ", ".join(sorted(ips_del_certificado(rutas[0]) or [])))
    else:
        print("No se pudo:", motivo)
