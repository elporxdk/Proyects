#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bench_vision: mide el pipeline de video de Medibot y compara perfiles.
======================================================================
PARA QUE SIRVE: decidir con NUMEROS, en la maquina real, si los FPS los
limita la camara o nuestro codigo, y cuanto cuesta cada perfil de emision
web. Sin esto, cualquier "optimizacion" es una corazonada.

Recorre exactamente el mismo camino que Vision_MEDIBOT.py:

    camara.read() -> detectores -> FrameHub.publicar() -> JPEG del stream

pero SIN GUI, SIN Flask y SIN Arduino, asi que se puede ejecutar por SSH en la
Raspberry mientras Medibot esta parado.

USO
---
    # Sin hardware (frames sinteticos): funciona en cualquier maquina
    python3 herramientas/bench_vision.py --fake

    # Con la camara real de la Pi, los tres perfiles, 8 s cada uno
    python3 herramientas/bench_vision.py --camara 0 --segundos 8

    # Un solo perfil, simulando 2 navegadores conectados
    python3 herramientas/bench_vision.py --camara 0 --perfil bajo --clientes 2

COMO LEER EL RESULTADO
----------------------
La columna "manda" dice donde esta el limite:
  * manda: camara   -> el cuello esta en el driver (USB, formato, exposicion).
                       Optimizar Python NO va a servir de nada. Toca bajar
                       resolucion, forzar MJPG o quitar la exposicion
                       automatica. Comprueba los modos reales con:
                           v4l2-ctl --list-formats-ext -d /dev/video0
  * manda: proceso  -> el cuello es nuestro: detectores y dibujado.
  * manda: jpeg     -> el cuello es la emision web: baja calidad o resolucion
                       del perfil web (no hace falta tocar la captura).
"""

import argparse
import os
import sys
import time

try:
    import cv2
    import numpy as np
except ImportError as e:                                  # pragma: no cover
    print(f"Falta una libreria: {e}")
    print("Instala con:  pip install numpy opencv-python")
    sys.exit(1)

# El motor de video vive en medibot/ y esta herramienta en herramientas/:
# Python solo mira la carpeta del script, asi que hay que anadir la otra.
_RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(_RAIZ, "medibot") not in sys.path:
    sys.path.insert(0, os.path.join(_RAIZ, "medibot"))

import medibot_vision as mv


#  Los tres perfiles que se comparan. Son PUNTOS DE PARTIDA razonados, no
#  verdades: el bench existe justo para elegir con datos de tu equipo.
#    calidad     -> LAN cableada o WiFi buena, se quiere ver bien.
#    balanceado  -> por defecto del proyecto; buen compromiso en WiFi normal.
#    bajo        -> WiFi flojo, 4G, o la Pi va justa de CPU.
PERFILES = {
    "calidad":    mv.PerfilWeb(ancho=None, alto=None, calidad=85, fps_max=25),
    "balanceado": mv.PerfilWeb(ancho=None, alto=None, calidad=70, fps_max=15),
    "bajo":       mv.PerfilWeb(ancho=480,  alto=None, calidad=50, fps_max=10),
}


def abrir(indice, ancho, alto, fps, fourcc, fake):
    if fake:
        os.environ["MEDIBOT_CAMARA_FAKE"] = "1"
    perfil = mv.PerfilCaptura(ancho, alto, fps, fourcc)
    cap, info = mv.abrir_camara(indice, perfil)
    if cap is None:
        print(f"\nNo se pudo abrir la camara {indice}.")
        print("  - Comprueba que existe:      ls /dev/video*")
        print(f"  - Y sus modos reales:        v4l2-ctl --list-formats-ext -d /dev/video{indice}")
        print("  - O corre sin hardware:      python3 herramientas/bench_vision.py --fake")
        sys.exit(2)
    return cap, info


def medir_perfil(cap, info, perfil_web, segundos, clientes, rojo, caras, cascade):
    """Corre el pipeline durante 'segundos' y devuelve las metricas."""
    perf = mv.Perfilador(ventana=10**9)      # sin media movil: media total
    hub = mv.FrameHub()
    detector = mv.RedDetector(area_minima=300)
    detector_caras = mv.FaceDetector(cascade, escala=0.5) if caras else None
    medidor = mv.Medidor()

    destino = perfil_web.tamano_para(info.ancho or 640, info.alto or 480)
    periodo_web = 1.0 / perfil_web.fps_max
    proximo_envio = 0.0
    bytes_totales = 0
    enviados = 0
    leidos = 0
    fallos = 0

    fin = time.perf_counter() + segundos
    while time.perf_counter() < fin:
        t = time.perf_counter()
        ok, frame = cap.read()
        perf.anotar("camara", (time.perf_counter() - t) * 1000.0)
        if not ok or frame is None:
            fallos += 1
            time.sleep(0.05)
            continue
        leidos += 1
        medidor.tick(time.time())

        t = time.perf_counter()
        if rojo:
            detector.detectar(frame, dibujar=True)
        if detector_caras is not None:
            detector_caras.detectar(frame)
        perf.anotar("proceso", (time.perf_counter() - t) * 1000.0)

        t = time.perf_counter()
        hub.publicar(0, frame.copy())
        perf.anotar("publicar", (time.perf_counter() - t) * 1000.0)

        # Emision web: respeta el tope de FPS del perfil. Cada fotograma NO
        # enviado es una codificacion JPEG que no se paga.
        ahora = time.perf_counter()
        if ahora >= proximo_envio:
            t = time.perf_counter()
            datos = hub.jpeg(0, calidad=perfil_web.calidad, tamano=destino)
            # Clientes adicionales: deben reutilizar la cache, no recodificar.
            for _ in range(max(0, clientes - 1)):
                hub.jpeg(0, calidad=perfil_web.calidad, tamano=destino)
            perf.anotar("jpeg", (time.perf_counter() - t) * 1000.0)
            if datos:
                enviados += 1
                bytes_totales += len(datos) * max(1, clientes)
            # Se ACUMULA el instante del siguiente envio (igual que en la ruta
            # /video de Vision_MEDIBOT.py). Reiniciarlo a "ahora + periodo"
            # hace que un tope de 25 FPS sobre una fuente de 30 emita 15.
            proximo_envio += periodo_web
            if proximo_envio < ahora - periodo_web:
                proximo_envio = ahora

    medias = perf.medias()
    total_ms = sum(medias.values())
    return {
        "medias": medias,
        "manda": perf.cuello_de_botella(),
        "fps_captura": leidos / segundos,
        "fps_web": enviados / segundos,
        "kb_frame": (bytes_totales / enviados / 1024.0) if enviados else 0.0,
        "kbps": bytes_totales * 8 / 1000.0 / segundos,
        "total_ms": total_ms,
        "techo_fps": (1000.0 / total_ms) if total_ms else 0.0,
        "destino": destino,
        "fallos": fallos,
    }


def main():
    p = argparse.ArgumentParser(description="Mide el pipeline de video de Medibot")
    p.add_argument("--camara", type=int, default=0, help="indice de camara (def. 0)")
    p.add_argument("--fake", action="store_true",
                   help="camara sintetica: no necesita hardware")
    p.add_argument("--segundos", type=float, default=5.0, help="por perfil (def. 5)")
    p.add_argument("--perfil", choices=sorted(PERFILES), default=None,
                   help="solo uno; por defecto se comparan los tres")
    p.add_argument("--clientes", type=int, default=1,
                   help="navegadores simultaneos simulados (def. 1)")
    p.add_argument("--ancho", type=int, default=640)
    p.add_argument("--alto", type=int, default=480)
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--fourcc", default="MJPG", help="MJPG, YUYV o vacio")
    p.add_argument("--sin-rojo", action="store_true",
                   help="no ejecutar la deteccion de objetos rojos")
    p.add_argument("--con-caras", action="store_true",
                   help="ejecutar tambien el Haar (mide el coste del reconocimiento)")
    args = p.parse_args()

    mv.ajustar_hilos_opencv()

    cascade = None
    if args.con_caras:
        for ruta in ("haarcascade_frontalface_default.xml",
                     "/usr/share/opencv4/haarcascades/haarcascade_frontalface_default.xml",
                     "/usr/local/share/opencv4/haarcascades/haarcascade_frontalface_default.xml"):
            if os.path.exists(ruta):
                cascade = cv2.CascadeClassifier(ruta)
                break
        if cascade is None:
            print("AVISO: --con-caras pedido pero no se encontro el haarcascade; se omite.\n")

    print("=" * 78)
    print("BENCH DE VIDEO MEDIBOT")
    print("=" * 78)
    print(f"OpenCV {cv2.__version__} | hilos OpenCV: {cv2.getNumThreads()}")

    cap, info = abrir(args.camara, args.ancho, args.alto, args.fps,
                      args.fourcc, args.fake)
    print(f"Camara: {info.resumen()}")
    if not args.fake and info.fourcc and info.fourcc != args.fourcc:
        print(f"  OJO: pediste {args.fourcc} y el driver dio {info.fourcc}.")
        print(f"       v4l2-ctl --list-formats-ext -d /dev/video{args.camara}")
    print(f"Deteccion rojo: {'no' if args.sin_rojo else 'si'} | "
          f"Haar: {'si' if cascade is not None else 'no'} | "
          f"clientes web simulados: {args.clientes}")
    print()

    # Calentamiento: los primeros fotogramas de una webcam son atipicos
    # (autoexposicion, autoenfoque) y contaminarian la media.
    print("Calentando (1 s)...", flush=True)
    t_fin = time.perf_counter() + 1.0
    while time.perf_counter() < t_fin:
        cap.read()

    nombres = [args.perfil] if args.perfil else ["calidad", "balanceado", "bajo"]
    filas = []
    for nombre in nombres:
        print(f"Midiendo perfil '{nombre}' durante {args.segundos:.0f} s...", flush=True)
        r = medir_perfil(cap, info, PERFILES[nombre], args.segundos,
                         args.clientes, not args.sin_rojo,
                         cascade is not None, cascade)
        filas.append((nombre, r))
    cap.release()

    print("\n" + "=" * 78)
    print("RESULTADOS  (ms = milisegundos medios por fotograma)")
    print("=" * 78)
    cab = (f"{'perfil':<11}{'emite':>10}{'cam ms':>8}{'proc ms':>9}{'jpeg ms':>9}"
           f"{'fps cap':>9}{'fps web':>9}{'KB/frm':>8}{'kbit/s':>9}  manda")
    print(cab)
    print("-" * 78)
    for nombre, r in filas:
        m = r["medias"]
        print(f"{nombre:<11}"
              f"{r['destino'][0]}x{r['destino'][1]:<5}"
              f"{m.get('camara', 0):>8.1f}"
              f"{m.get('proceso', 0):>9.1f}"
              f"{m.get('jpeg', 0):>9.1f}"
              f"{r['fps_captura']:>9.1f}"
              f"{r['fps_web']:>9.1f}"
              f"{r['kb_frame']:>8.1f}"
              f"{r['kbps']:>9.0f}"
              f"  {r['manda']}")
    print("-" * 78)

    peor = max(filas, key=lambda f: f[1]["medias"].get("camara", 0))[1]
    cam_ms = peor["medias"].get("camara", 0)
    print("\nLECTURA DEL RESULTADO")
    if peor["manda"] == "camara":
        print(f"  El limite esta en la CAMARA ({cam_ms:.1f} ms por fotograma, "
              f"techo ~{1000.0 / cam_ms if cam_ms else 0:.0f} FPS).")
        print("  Optimizar el codigo Python NO subira los FPS. Que probar:")
        print(f"    1. Modos reales:  v4l2-ctl --list-formats-ext -d /dev/video{args.camara}")
        print("    2. Forzar MJPG:   --fourcc MJPG   (YUYV satura el USB 2.0)")
        print("    3. Bajar captura: --ancho 480 --alto 360")
        print("    4. Exposicion automatica: en interiores baja los FPS a la mitad.")
        print("       MEDIBOT_CAM_AUTOEXP=0 python3 medibot/main.py")
    else:
        print(f"  El limite es NUESTRO (etapa '{peor['manda']}'). Que probar:")
        print("    Color rojo apagado     (ya viene asi; encendido cuesta ~1,3 ms/frame)")
        print("    MEDIBOT_OVERLAY=0       (quita los textos dibujados)")
        print("    perfil web 'bajo'       (menos pixeles que comprimir)")

    total_fallos = sum(r["fallos"] for _, r in filas)
    if total_fallos:
        print(f"\n  AVISO: {total_fallos} lecturas fallidas: cable USB, alimentacion")
        print("  o la camara desconectandose. Revisalo antes de fiarte de las medias.")

    print("\nPara aplicar un perfil a Medibot, ver README.md ('Perfiles de video').")


if __name__ == "__main__":
    main()
