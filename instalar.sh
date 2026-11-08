#!/usr/bin/env bash
# =====================================================================
#  MEDIBOT - instalador para la Raspberry Pi
# =====================================================================
#
#  Deja la Pi lista para correr MEDIBOT con UN comando:
#
#    curl -fsSL https://raw.githubusercontent.com/elporxdk/Proyects/main/instalar.sh | bash
#
#  o, si ya tienes el repositorio descargado:
#
#    bash instalar.sh
#
#  Que hace:
#    1. Instala con apt todo lo que usa medibot/ (Flask, pyserial, OpenCV
#       con el modulo 'face', Tkinter, Pillow, arecord/aplay, openssl...).
#    2. Descarga el codigo en ~/Proyects (o lo actualiza si ya estaba).
#    3. Da permiso a tu usuario para el puerto del Arduino, las camaras y
#       el audio (grupos dialout, video y audio).
#    4. Crea el icono "MEDIBOT" en el menu y en el escritorio, y la orden
#       'medibot' para arrancarlo desde una terminal.
#    5. Comprueba que todo se puede importar y dice que falta, si falta algo.
#
#  Volver a ejecutarlo = ACTUALIZAR (baja el codigo nuevo y repasa todo).
#
#  Opciones:
#    --autoarranque       MEDIBOT se abre solo al encender la Pi
#                         (al entrar al escritorio).
#    --sin-autoarranque   quita el autoarranque.
#
#  Con curl, las opciones van despues de 'bash -s --':
#    curl -fsSL https://raw.githubusercontent.com/elporxdk/Proyects/main/instalar.sh | bash -s -- --autoarranque
#
#  Variables:
#    MEDIBOT_DIR=/otra/carpeta   donde descargar el codigo (por defecto ~/Proyects)
#
#  POR QUE apt Y NO pip: en Raspberry Pi OS (Bookworm y Trixie) 'pip install'
#  fuera de un entorno virtual falla con "externally-managed-environment".
#  Ademas, el python3-opencv de apt ya trae cv2.face (el reconocimiento facial
#  LBPH), que el opencv-python de pip NO trae, y opencv-data pone el
#  clasificador de caras en /usr/share/opencv4/haarcascades, que es donde lo
#  busca Vision_MEDIBOT.py. Y no compila nada: en la Pi tarda un par de minutos.
# =====================================================================

#  Todo va dentro de main(): bash lee los scripts A TROZOS mientras los
#  ejecuta, y el paso 2 puede actualizar este mismo fichero con git pull. Con
#  la funcion, bash lo ha leido entero antes de empezar.

set -euo pipefail

REPO_URL="https://github.com/elporxdk/Proyects.git"
#  OJO: la rama por defecto de GitHub es 'web' (la pagina web), que NO trae
#  medibot/. El codigo de la Pi esta en 'main': hay que pedirla siempre.
RAMA="main"

PAQUETES=(
    git
    python3
    python3-flask        # web del pastillero (Pastillero.py) y de la camara
    python3-serial       # pyserial: el hub serie habla con el Arduino
    python3-opencv       # vision; incluye cv2.face (reconocimiento LBPH)
    opencv-data          # haarcascade_frontalface_default.xml
    python3-numpy
    python3-pil          # Pillow
    python3-pil.imagetk  # PIL.ImageTk (en Debian va en un paquete aparte)
    python3-tk           # la ventana de Medibot/Vision
    alsa-utils           # arecord / aplay: microfono y altavoz desde la web
    openssl              # certificado propio para el HTTPS (medibot_tls.py)
    v4l-utils            # v4l2-ctl: ver los modos reales de cada camara
)

info()  { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
aviso() { printf '\033[1;33mAVISO:\033[0m %s\n' "$*" >&2; }
fallo() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

ayuda() {
    cat <<'EOF'
Uso:  bash instalar.sh [--autoarranque | --sin-autoarranque]

  (sin opciones)       instala o actualiza MEDIBOT
  --autoarranque       ademas, MEDIBOT se abre solo al encender la Pi
  --sin-autoarranque   quita el autoarranque

  MEDIBOT_DIR=/carpeta bash instalar.sh   descarga el codigo en otra carpeta
EOF
}

main() {
    local autoarranque=""
    for arg in "$@"; do
        case "$arg" in
            --autoarranque)     autoarranque="si" ;;
            --sin-autoarranque) autoarranque="no" ;;
            -h|--help)          ayuda; exit 0 ;;
            *)                  ayuda; fallo "opcion desconocida: $arg" ;;
        esac
    done

    #  Como root, el icono, el autoarranque y los grupos acabarian en la
    #  cuenta de root y no en la del usuario que usa la Pi.
    if [ "$(id -u)" -eq 0 ]; then
        fallo "no lo ejecutes con sudo ni como root. Ejecutalo como tu usuario normal: ya pedira la contraseña cuando haga falta."
    fi
    command -v apt-get >/dev/null 2>&1 \
        || fallo "este instalador es para Raspberry Pi OS (o Debian/Ubuntu): no encuentro apt-get."

    local usuario
    usuario="$(id -un)"

    if [ -r /proc/device-tree/model ]; then
        echo "Equipo: $(tr -d '\0' < /proc/device-tree/model)"
    else
        aviso "esto no parece una Raspberry Pi; se instala igualmente."
    fi

    # ---------------------------------------------------------------- 1
    info "1/5  Instalando los paquetes del sistema (pedira tu contraseña)"
    sudo apt-get update
    sudo apt-get install -y "${PAQUETES[@]}"

    #  Los paquetes de apt son para el Python DEL SISTEMA. Si hay otro python3
    #  delante en el PATH (un venv activado, pyenv...), no los veria.
    local py=/usr/bin/python3
    [ -x "$py" ] || py="$(command -v python3)"

    # ---------------------------------------------------------------- 2
    info "2/5  Descargando el codigo de MEDIBOT"
    #  Si este script se ejecuta desde dentro del repositorio, se usa ese.
    #  Con 'curl | bash' no hay fichero (BASH_SOURCE vacio) y se clona.
    local dir_script="" destino
    if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
        dir_script="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    fi
    if [ -n "${MEDIBOT_DIR:-}" ]; then
        destino="$MEDIBOT_DIR"
    elif [ -n "$dir_script" ] && [ -f "$dir_script/medibot/main.py" ]; then
        destino="$dir_script"
    else
        destino="$HOME/Proyects"
    fi

    if [ -d "$destino/.git" ]; then
        echo "Ya estaba en $destino: actualizando..."
        #  Un 'git clone' sin -b deja la rama 'web', sin medibot/: se cambia
        #  a 'main' (git se niega si eso pisara cambios hechos a mano).
        if [ ! -f "$destino/medibot/main.py" ]; then
            echo "Esa copia no tiene medibot/ (rama $(git -C "$destino" branch --show-current)): cambiando a la rama $RAMA..."
            if ! { git -C "$destino" fetch origin "$RAMA" \
                    && git -C "$destino" checkout "$RAMA"; }; then
                fallo "no se pudo cambiar a la rama $RAMA en $destino."
            fi
        fi
        if ! git -C "$destino" pull --ff-only origin "$RAMA"; then
            aviso "no se pudo actualizar $destino (¿sin internet, o hay cambios hechos a mano?). Se sigue con el codigo que ya habia."
        fi
    elif [ -e "$destino" ]; then
        fallo "$destino ya existe y no es una copia de git. Muevela, o elige otra carpeta:  MEDIBOT_DIR=/otra/carpeta bash instalar.sh"
    else
        git clone --branch "$RAMA" "$REPO_URL" "$destino"
    fi
    [ -f "$destino/medibot/main.py" ] \
        || fallo "no encuentro $destino/medibot/main.py: la descarga no salio bien."
    destino="$(cd "$destino" && pwd)"

    # ---------------------------------------------------------------- 3
    info "3/5  Permisos para el Arduino (USB serie), las camaras y el audio"
    local g grupos_nuevos=() mis_grupos
    mis_grupos=" $(id -nG "$usuario") "
    for g in dialout video audio; do
        if getent group "$g" >/dev/null; then
            case "$mis_grupos" in
                *" $g "*) ;;
                *) grupos_nuevos+=("$g") ;;
            esac
        fi
    done
    local hay_que_reiniciar="no"
    if [ "${#grupos_nuevos[@]}" -gt 0 ]; then
        sudo usermod -aG "$(IFS=,; echo "${grupos_nuevos[*]}")" "$usuario"
        echo "Añadido $usuario a: ${grupos_nuevos[*]}"
        hay_que_reiniciar="si"
    else
        echo "Ya tenia los permisos (dialout, video, audio)."
    fi

    # ---------------------------------------------------------------- 4
    info "4/5  Creando el icono de MEDIBOT y la orden 'medibot'"
    #  La orden 'medibot': arranca main.py con el Python del sistema.
    mkdir -p "$HOME/.local/bin"
    local orden="$HOME/.local/bin/medibot"
    cat > "$orden" <<EOF
#!/usr/bin/env bash
# Arranca MEDIBOT (lo genera instalar.sh; se rehace al volver a instalar).
cd "$destino" || exit 1
exec "$py" medibot/main.py "\$@"
EOF
    chmod +x "$orden"

    #  El icono abre una terminal con MEDIBOT dentro: ahi salen las
    #  direcciones para entrar al pastillero desde el movil y los avisos.
    local lanzador_exec="$orden"
    if command -v x-terminal-emulator >/dev/null 2>&1; then
        lanzador_exec="x-terminal-emulator -e $orden"
    fi
    local apps="$HOME/.local/share/applications"
    mkdir -p "$apps"
    local lanzador="$apps/medibot.desktop"
    cat > "$lanzador" <<EOF
[Desktop Entry]
Type=Application
Name=MEDIBOT
Comment=Arranca el hub serie, el pastillero (web) y la vision
Exec=$lanzador_exec
Path=$destino
Icon=camera-web
Terminal=false
Categories=Utility;
EOF
    chmod +x "$lanzador"

    local escritorio=""
    if command -v xdg-user-dir >/dev/null 2>&1; then
        escritorio="$(xdg-user-dir DESKTOP 2>/dev/null || true)"
    fi
    [ -n "$escritorio" ] || escritorio="$HOME/Desktop"
    if [ -d "$escritorio" ] && [ "$escritorio" != "$HOME" ]; then
        cp "$lanzador" "$escritorio/medibot.desktop"
        chmod +x "$escritorio/medibot.desktop"
        echo "Icono en el escritorio: $escritorio/medibot.desktop"
    fi
    echo "Icono en el menu: $lanzador"
    echo "Orden de terminal: medibot"

    #  Autoarranque: un .desktop en ~/.config/autostart, que el escritorio de
    #  Raspberry Pi OS abre al iniciar sesion. Sin opcion, se respeta lo que
    #  hubiera (y se rehace por si la carpeta del codigo ha cambiado).
    local autostart="$HOME/.config/autostart/medibot.desktop"
    if [ "$autoarranque" = "si" ] || { [ -z "$autoarranque" ] && [ -f "$autostart" ]; }; then
        mkdir -p "$(dirname "$autostart")"
        cp "$lanzador" "$autostart"
        echo "Autoarranque ACTIVADO: MEDIBOT se abrira al entrar al escritorio."
    elif [ "$autoarranque" = "no" ]; then
        rm -f "$autostart"
        echo "Autoarranque desactivado."
    fi

    # ---------------------------------------------------------------- 5
    info "5/5  Comprobando la instalacion"
    local estado=0
    "$py" - <<'PY' || estado=$?
import importlib
import os
import shutil

faltan_nucleo = False

def ok(texto):
    print(f"  [ OK ] {texto}")

def mal(texto, remedio):
    print(f"  [FALTA] {texto}  ->  {remedio}")

def modulo(nombre, texto, paquete, nucleo=False):
    global faltan_nucleo
    try:
        importlib.import_module(nombre)
        ok(texto)
        return True
    except Exception as e:                                   # noqa: BLE001
        mal(f"{texto} ({e})", f"sudo apt install {paquete}")
        if nucleo:
            faltan_nucleo = True
        return False

print("Pastillero y hub serie (imprescindibles):")
modulo("flask", "Flask", "python3-flask", nucleo=True)
modulo("serial.tools.list_ports", "pyserial", "python3-serial", nucleo=True)

print("Medibot / Vision:")
if modulo("cv2", "OpenCV", "python3-opencv"):
    import cv2
    if hasattr(cv2, "face"):
        ok(f"OpenCV {cv2.__version__} con cv2.face (reconocimiento facial)")
    else:
        mal("cv2.face (reconocimiento facial)",
            "usa el python3-opencv de apt, no el opencv-python de pip")
modulo("numpy", "NumPy", "python3-numpy")
modulo("tkinter", "Tkinter", "python3-tk")
modulo("PIL.ImageTk", "Pillow + ImageTk", "python3-pil python3-pil.imagetk")
cascada = "/usr/share/opencv4/haarcascades/haarcascade_frontalface_default.xml"
if os.path.exists(cascada):
    ok("clasificador de caras (Haar)")
else:
    mal("clasificador de caras " + cascada, "sudo apt install opencv-data")

print("Audio y HTTPS:")
for prog, paquete in (("arecord", "alsa-utils"), ("aplay", "alsa-utils"),
                      ("openssl", "openssl")):
    if shutil.which(prog):
        ok(prog)
    else:
        mal(prog, f"sudo apt install {paquete}")

print("Hardware conectado ahora mismo (solo informativo):")
camaras = sorted(f for f in os.listdir("/dev") if f.startswith("video"))
serie = sorted(f for f in os.listdir("/dev")
               if f.startswith(("ttyACM", "ttyUSB")))
print("  camaras: " + (", ".join("/dev/" + c for c in camaras) or "ninguna"))
print("  Arduino: " + (", ".join("/dev/" + s for s in serie)
                       or "ninguno (no pasa nada: el hub lo busca cada 2 s)"))

raise SystemExit(1 if faltan_nucleo else 0)
PY

    echo
    if [ "$estado" -ne 0 ]; then
        fallo "falta algo imprescindible (mira la lista de arriba). Vuelve a ejecutar el instalador cuando tengas internet."
    fi

    info "MEDIBOT esta instalado en $destino"
    cat <<EOF

  Para arrancarlo:
    - doble clic en el icono MEDIBOT (escritorio o menu), o
    - en una terminal:  medibot
      (o:  $py $destino/medibot/main.py)

  El pastillero se abre desde el movil o el PC, en la misma red:
EOF
    local ip
    for ip in $(hostname -I 2>/dev/null || true); do
        case "$ip" in
            *:*) ;;                          # las IPv6 se saltan
            *)   echo "    http://$ip:5001" ;;
        esac
    done
    cat <<EOF

  Para actualizar a la ultima version: vuelve a ejecutar este instalador.
EOF
    if [ "$hay_que_reiniciar" = "si" ]; then
        printf '\n\033[1;33m  IMPORTANTE: reinicia la Pi (sudo reboot) para que los permisos nuevos\n  (Arduino, camaras, audio) se apliquen.\033[0m\n'
    fi
    if [ "$autoarranque" != "si" ] && [ ! -f "$autostart" ]; then
        echo
        echo "  Si quieres que se abra solo al encender la Pi:  bash $destino/instalar.sh --autoarranque"
    fi
}

main "$@"
