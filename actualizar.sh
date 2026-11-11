#!/usr/bin/env bash
# =====================================================================
#  MEDIBOT - actualizador para la Raspberry Pi
# =====================================================================
#
#  Pone al dia el codigo de un MEDIBOT ya instalado (con instalar.sh).
#
#  CON UN COMANDO (en una terminal de la Pi):
#
#    medibot-actualizar
#
#  (o:  bash ~/Proyects/actualizar.sh, o el icono "Actualizar MEDIBOT" del
#  menu). Baja de GitHub lo nuevo de la rama main y lo instala.
#
#  O SOLO, SIN HACER NADA:
#
#    medibot-actualizar --activar-auto
#
#  Desde ese momento la Pi mira cada hora si hay version nueva (y 2 minutos
#  despues de encenderse) y la descarga. Si MEDIBOT esta cerrado, la instala
#  en ese momento; si esta abierto, NO lo toca a mitad de uso: la instala la
#  proxima vez que se abra MEDIBOT (con el icono, la orden 'medibot' o el
#  autoarranque), antes de arrancar y sin necesitar internet.
#
#  Que hace al actualizar:
#    1. Descarga la version nueva (git fetch de la rama main).
#    2. Si alguien cambio ficheros a mano en la Pi, los guarda antes en
#       ~/.medibot/respaldos/ y, al terminar, los vuelve a poner encima de la
#       version nueva. Si chocan con ella, se queda la version nueva limpia y
#       los cambios siguen guardados (no se pierde nada).
#    3. Comprueba que la version nueva no tiene errores de sintaxis (Python y
#       los .sh). Si los tiene, vuelve a dejar la version anterior.
#    4. Si cambio instalar.sh (paquetes nuevos, iconos...), lo vuelve a pasar.
#    5. Apaga el hub serie viejo si se quedo en memoria con MEDIBOT cerrado,
#       para que el proximo arranque use el codigo nuevo.
#
#  Lo que es de cada Pi NO se toca: datos del pastillero, caras entrenadas,
#  videos, certificados... (son ficheros que git ignora, ver .gitignore).
#
#  Opciones:
#    (sin opciones)       actualizar ahora
#    --estado             version instalada, si hay una nueva, si la
#                         actualizacion automatica esta activada y el registro
#    --activar-auto       actualizar solo (temporizador de systemd)
#    --desactivar-auto    quitar la actualizacion automatica
#
#  Las usa MEDIBOT por dentro (no hace falta escribirlas a mano):
#    --auto               la que lanza el temporizador: no pregunta nada ni
#                         pide contraseña, y no toca un MEDIBOT abierto
#    --al-arrancar        la que lanza la orden 'medibot' antes de arrancar:
#                         instala lo que el temporizador ya descargo
#
#  Variables:
#    MEDIBOT_DIR=/otra/carpeta   copia de MEDIBOT a actualizar (por defecto,
#                                la carpeta de este script, o ~/Proyects)
#    MEDIBOT_SIN_ACTUALIZAR=1    'medibot' arranca sin mirar si hay version
#                                nueva descargada
#
#  Registro de lo que ha hecho:  ~/.medibot/actualizar.log
#  (y con la automatica, tambien:  journalctl -u medibot-actualizar)
# =====================================================================

#  Todo va dentro de main(), como en instalar.sh: bash lee los scripts A
#  TROZOS mientras los ejecuta, y este script se actualiza a si mismo.

set -euo pipefail

#  OJO: la rama por defecto de GitHub es 'web' (la pagina web), que NO trae
#  medibot/. El codigo de la Pi esta en 'main'.
RAMA="main"

UNIDAD="medibot-actualizar"
SERVICIO="/etc/systemd/system/$UNIDAD.service"
TEMPORIZADOR="/etc/systemd/system/$UNIDAD.timer"

ESTADO="$HOME/.medibot"
REGISTRO="$ESTADO/actualizar.log"
RESPALDOS="$ESTADO/respaldos"
#  Marca de "hay que volver a pasar instalar.sh y no se pudo": la deja el
#  modo automatico cuando no puede usar sudo sin contraseña.
INSTALAR_PENDIENTE="$ESTADO/instalar-pendiente"
#  La ultima version que se dejo para despues por estar MEDIBOT abierto
#  (para no repetir el mismo aviso en el registro cada hora).
APLAZADA="$ESTADO/aplazada"
#  La ultima version que se rechazo por tener errores: la automatica no la
#  vuelve a intentar cada hora (a mano, si; y la siguiente version, tambien).
RECHAZADA="$ESTADO/rechazada"

#  Nunca pedir usuario/contraseña de GitHub: en el modo automatico no hay
#  nadie para escribirla y se quedaria colgado.
export GIT_TERMINAL_PROMPT=0

info()  { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
aviso() { printf '\033[1;33mAVISO:\033[0m %s\n' "$*" >&2; }
fallo() {
    printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2
    registrar "ERROR: $*"
    exit 1
}

#  Deja una linea en ~/.medibot/actualizar.log (solo lo importante: lo que
#  se instalo, lo que se aplazo, los errores). Nunca hace fallar al script.
registrar() {
    {
        mkdir -p "$ESTADO" \
            && printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$REGISTRO"
    } 2>/dev/null || true
}

recortar_registro() {
    #  Que no crezca sin fin: se quedan las ultimas 300 lineas.
    if [ -f "$REGISTRO" ] && [ "$(wc -l < "$REGISTRO")" -gt 500 ]; then
        tail -n 300 "$REGISTRO" > "$REGISTRO.tmp" && mv "$REGISTRO.tmp" "$REGISTRO"
    fi 2>/dev/null || true
}

ayuda() {
    cat <<'EOF'
Uso:  medibot-actualizar [opcion]        (o:  bash actualizar.sh [opcion])

  (sin opciones)       actualiza MEDIBOT ahora
  --estado             version instalada, si hay una nueva y como esta la
                       actualizacion automatica
  --activar-auto       que se actualice solo (mira cada hora; si MEDIBOT esta
                       abierto, instala lo nuevo la proxima vez que se abra)
  --desactivar-auto    quita la actualizacion automatica

  MEDIBOT_DIR=/carpeta medibot-actualizar   actualiza la copia de otra carpeta
EOF
}

# ------------------------------------------------------------- utilidades

g() { git -C "$DESTINO" "$@"; }

corto() { g rev-parse --short "$1"; }

cambios() {          # cambios 1 -> "1 cambio";  cambios 3 -> "3 cambios"
    if [ "$1" = "1" ]; then echo "1 cambio"; else echo "$1 cambios"; fi
}

describir() {        # describir <commit>  ->  "abc1234 (12/11/2026 18:30)"
    g log -1 --format='%h (%cd)' --date=format:'%d/%m/%Y %H:%M' "$1"
}

hay_systemd() {
    command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]
}

auto_activada() {
    command -v systemctl >/dev/null 2>&1 \
        && systemctl is-enabled --quiet "$UNIDAD.timer" 2>/dev/null
}

puerto_abierto() {   # puerto_abierto <puerto>  (en esta maquina)
    timeout 2 bash -c "exec 3<>/dev/tcp/127.0.0.1/$1" 2>/dev/null
}

#  ¿Esta MEDIBOT abierto? (main.py, Pastillero.py o Vision_MEDIBOT.py de esta
#  copia). El hub serie (serial_hub.py) NO cuenta: se queda en memoria al
#  cerrar MEDIBOT, a proposito.
medibot_en_marcha() {
    local pid cmd cwd
    if command -v pgrep >/dev/null 2>&1; then
        for pid in $(pgrep -u "$(id -u)" -f '(main|Pastillero|Vision_MEDIBOT)\.py' 2>/dev/null || true); do
            [ "$pid" = "$$" ] && continue
            cmd="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null)" || continue
            case "$cmd" in *python*) ;; *) continue ;; esac
            cwd="$(readlink "/proc/$pid/cwd" 2>/dev/null || true)"
            case "$cmd $cwd/" in
                *"$DESTINO/"*) return 0 ;;
            esac
        done
    fi
    #  La web del pastillero esta en el 5001 siempre que MEDIBOT esta abierto.
    puerto_abierto 5001
}

#  Comprueba que el codigo de la carpeta se puede ejecutar: que ningun .py
#  de medibot/ ni los .sh tienen errores de sintaxis. Asi una version rota
#  no deja la Pi sin MEDIBOT (ni sin actualizador para arreglarlo).
verificar_codigo() {
    local sh
    for sh in "$DESTINO"/*.sh; do
        [ -f "$sh" ] || continue
        if ! bash -n "$sh"; then
            echo "  error de sintaxis en $(basename "$sh")"
            return 1
        fi
    done
    "$PY" - "$DESTINO/medibot" <<'PY'
import os
import sys

raiz = sys.argv[1]
malos = 0
for carpeta, subcarpetas, ficheros in os.walk(raiz):
    subcarpetas[:] = [d for d in subcarpetas if not d.startswith((".", "__"))]
    for nombre in sorted(ficheros):
        if not nombre.endswith(".py"):
            continue
        ruta = os.path.join(carpeta, nombre)
        try:
            with open(ruta, "rb") as f:
                compile(f.read(), ruta, "exec")
        except (SyntaxError, ValueError) as e:
            print(f"  error de sintaxis en {os.path.relpath(ruta, os.path.dirname(raiz))}: {e}")
            malos += 1
raise SystemExit(1 if malos else 0)
PY
}

descargar() {
    #  3 intentos: al encender la Pi la red puede tardar un poco en estar.
    local intento
    for intento in 1 2 3; do
        if timeout 180 git -C "$DESTINO" fetch --quiet origin \
                "+refs/heads/$RAMA:refs/remotes/origin/$RAMA"; then
            date '+%Y-%m-%d %H:%M:%S' > "$ESTADO/ultima-comprobacion" 2>/dev/null || true
            return 0
        fi
        [ "$intento" -lt 3 ] && sleep $((intento * 5))
    done
    return 1
}

#  Vuelve a pasar instalar.sh (paquetes nuevos, iconos, la orden 'medibot').
#  Sin nadie delante solo se puede si sudo no pide contraseña (en Raspberry
#  Pi OS, el primer usuario lo tiene asi); si no, se deja anotado para la
#  proxima vez que se actualice a mano.
pasar_instalador() {
    local motivo="$1"
    if [ "$MODO" = "manual" ]; then
        info "Repasando la instalacion ($motivo): ejecutando instalar.sh"
        if bash "$DESTINO/instalar.sh"; then
            rm -f "$INSTALAR_PENDIENTE"
            return 0
        fi
    elif sudo -n true 2>/dev/null; then
        info "Repasando la instalacion ($motivo): ejecutando instalar.sh"
        if bash "$DESTINO/instalar.sh" < /dev/null; then
            rm -f "$INSTALAR_PENDIENTE"
            registrar "instalar.sh pasado sin problemas ($motivo)"
            return 0
        fi
    fi
    mkdir -p "$ESTADO"
    echo "$motivo" > "$INSTALAR_PENDIENTE"
    aviso "falta repasar la instalacion ($motivo). Ejecuta en una terminal:  medibot-actualizar"
    registrar "pendiente: hay que volver a pasar instalar.sh ($motivo); se hara en la proxima 'medibot-actualizar'"
    return 1
}

#  El hub serie se queda en memoria al cerrar MEDIBOT. Si MEDIBOT esta
#  cerrado, se apaga para que el proximo arranque use el codigo nuevo.
#  (main.py ya lo renueva al arrancar; esto es para quien abre Pastillero.py
#  o Vision suelto.)
apagar_hub_viejo() {
    puerto_abierto 5055 || return 0
    "$PY" - "$DESTINO/medibot" <<'PY' || true
import sys
sys.path.insert(0, sys.argv[1])
try:
    import medibot_serial as ms
    if ms.hub_running():
        ms.hub_shutdown()
        print("Hub serie viejo apagado (el proximo arranque usa el codigo nuevo).")
except Exception:
    pass
PY
}

# ------------------------------------------------------------ actualizar

actualizar() {
    #  Que no se pisen dos actualizaciones (la automatica y una a mano).
    exec 9> "$DESTINO/.git/medibot-actualizar.lock"
    if ! flock -n 9; then
        [ "$MODO" = "manual" ] || return 0
        echo "Hay otra actualizacion en marcha; esperando a que termine..."
        flock -w 1800 9 || fallo "la otra actualizacion no termina; prueba mas tarde."
    fi

    #  Al arrancar MEDIBOT solo se instala lo que la automatica ya descargo,
    #  y solo si esta activada (si no, 'medibot' arranca sin tocar nada).
    if [ "$MODO" = "al-arrancar" ]; then
        if [ -f "$INSTALAR_PENDIENTE" ]; then
            aviso "la ultima actualizacion necesita repasar la instalacion. Cuando puedas, ejecuta en una terminal:  medibot-actualizar"
        fi
        auto_activada || return 0
    fi

    local rama
    rama="$(g branch --show-current)"
    if [ "$rama" != "$RAMA" ]; then
        [ "$MODO" = "al-arrancar" ] && return 0
        if [ "$MODO" = "auto" ]; then
            echo "$DESTINO esta en la rama '${rama:-(ninguna)}' y no en '$RAMA': no se actualiza."
            return 0
        fi
        fallo "$DESTINO esta en la rama '${rama:-(ninguna)}' y no en '$RAMA'. El actualizador solo sigue '$RAMA'. Para volver a ella:  git -C \"$DESTINO\" checkout $RAMA"
    fi

    # ------------------------------------------------- 1. descargar
    if [ "$MODO" != "al-arrancar" ]; then
        info "Buscando la version nueva de MEDIBOT en GitHub"
        if ! descargar; then
            if [ "$MODO" = "auto" ]; then
                echo "Sin conexion con GitHub; se probara en la proxima comprobacion."
                return 0
            fi
            aviso "no se pudo conectar con GitHub (¿hay internet?). Se mira si quedo algo descargado de antes."
        fi
    fi

    local actual nuevo
    actual="$(g rev-parse HEAD)"
    if ! nuevo="$(g rev-parse --verify -q "refs/remotes/origin/$RAMA^{commit}")"; then
        [ "$MODO" = "manual" ] || return 0
        fallo "no hay nada descargado de GitHub todavia; conecta la Pi a internet y vuelve a probar."
    fi

    if [ "$actual" = "$nuevo" ]; then
        if [ "$MODO" != "al-arrancar" ]; then
            echo "MEDIBOT ya esta al dia: version $(describir HEAD)."
        fi
        if [ "$MODO" = "manual" ] && [ -f "$INSTALAR_PENDIENTE" ]; then
            pasar_instalador "$(cat "$INSTALAR_PENDIENTE")" || true
        fi
        return 0
    fi
    if g merge-base --is-ancestor "$nuevo" "$actual"; then
        [ "$MODO" = "al-arrancar" ] \
            || echo "Esta copia ya tiene todo lo de GitHub, y ademas commits propios que GitHub no tiene. Nada que actualizar."
        return 0
    fi

    if [ "$MODO" != "manual" ] && [ "$(cat "$RECHAZADA" 2>/dev/null || true)" = "$nuevo" ]; then
        [ "$MODO" = "al-arrancar" ] \
            || echo "La version $(corto "$nuevo") de GitHub tiene errores y ya se rechazo; se espera a la siguiente."
        return 0
    fi

    local cuantos divergido="no"
    cuantos="$(g rev-list --count "$actual..$nuevo")"
    g merge-base --is-ancestor "$actual" "$nuevo" || divergido="si"

    #  Con MEDIBOT abierto, la automatica no le cambia el codigo debajo: lo
    #  nuevo ya esta descargado y se instala la proxima vez que se abra.
    local abierto="no"
    medibot_en_marcha && abierto="si"
    if [ "$abierto" = "si" ] && [ "$MODO" != "manual" ]; then
        if [ "$MODO" = "auto" ]; then
            echo "Hay version nueva ($(cambios "$cuantos"), $(describir "$nuevo")), pero MEDIBOT esta abierto: se instalara la proxima vez que se abra."
            if [ "$(cat "$APLAZADA" 2>/dev/null || true)" != "$nuevo" ]; then
                echo "$nuevo" > "$APLAZADA"
                registrar "version nueva $(corto "$nuevo") descargada; MEDIBOT abierto: se instalara al volver a abrirlo"
            fi
        fi
        return 0
    fi

    info "Instalando la version nueva: $(cambios "$cuantos")"
    echo "  ahora:  $(describir "$actual")"
    echo "  nueva:  $(describir "$nuevo")"
    echo
    #  (-n y no '| head': con pipefail, head cortando a git haria fallar todo)
    g log -n 15 --format='  - %s' --no-decorate "$actual..$nuevo"
    [ "$cuantos" -gt 15 ] && echo "  ... y $((cuantos - 15)) mas"

    # ------------------------------------------------- 2. guardar lo local
    local fecha
    fecha="$(date '+%Y%m%d-%H%M%S')"

    if [ "$divergido" = "si" ]; then
        #  Hay commits hechos en esta Pi que GitHub no tiene (o GitHub
        #  rehizo su historia). Se guardan en una rama para no perderlos.
        g branch -f "respaldo-$fecha" "$actual"
        echo
        echo "Esta copia tenia commits propios que no estan en GitHub: guardados en la rama 'respaldo-$fecha'."
        registrar "commits locales guardados en la rama respaldo-$fecha"
    fi

    local guardado="no" parche=""
    if ! g diff --quiet HEAD --; then
        mkdir -p "$RESPALDOS"
        parche="$RESPALDOS/cambios-$fecha.patch"
        g diff --binary HEAD -- > "$parche"
        g -c user.name="MEDIBOT" -c user.email="medibot@localhost" \
            stash push --quiet -m "medibot-actualizar $fecha"
        guardado="si"
        echo
        echo "Habia ficheros cambiados a mano en la Pi; copia guardada en: $parche"
    fi

    #  Ficheros sueltos (que git no sigue) con el mismo nombre que uno nuevo
    #  de GitHub: git se negaria a ponerlo. Se apartan a los respaldos.
    local apartados=() f
    while IFS= read -r -d '' f; do
        if [ -e "$DESTINO/$f" ] || [ -L "$DESTINO/$f" ]; then
            mkdir -p "$(dirname "$RESPALDOS/archivos-$fecha/$f")"
            mv "$DESTINO/$f" "$RESPALDOS/archivos-$fecha/$f"
            apartados+=("$f")
        fi
    done < <(g diff -z --name-only --no-renames --diff-filter=A "$actual" "$nuevo")
    if [ "${#apartados[@]}" -gt 0 ]; then
        echo "Apartados a $RESPALDOS/archivos-$fecha/ (la version nueva trae ficheros con ese nombre):"
        printf '  - %s\n' "${apartados[@]}"
    fi

    #  Si algo sale mal a partir de aqui, se deja todo como estaba.
    deshacer() {
        g checkout --quiet -f -B "$RAMA" "$actual" || true
        for f in ${apartados[@]+"${apartados[@]}"}; do
            if [ ! -e "$DESTINO/$f" ]; then
                mkdir -p "$(dirname "$DESTINO/$f")"
                mv "$RESPALDOS/archivos-$fecha/$f" "$DESTINO/$f" || true
            fi
        done
        if [ "$guardado" = "si" ]; then
            g stash pop --quiet || aviso "tus cambios a mano no se pudieron volver a poner; estan en $parche (y en 'git stash list')."
        fi
    }

    # ------------------------------------------------- 3. poner lo nuevo
    if ! g checkout --quiet -B "$RAMA" "$nuevo"; then
        deshacer
        fallo "git no pudo poner la version nueva (mira el mensaje de arriba). Se queda la que habia."
    fi

    if ! verificar_codigo; then
        deshacer
        echo "$nuevo" > "$RECHAZADA" 2>/dev/null || true
        fallo "la version nueva $(corto "$nuevo") tiene errores (arriba): se vuelve a la anterior, $(corto "$actual"). Se volvera a probar en la proxima actualizacion."
    fi

    # ------------------------------------------------- 4. lo local, encima
    if [ "$guardado" = "si" ]; then
        if g stash apply --quiet 2>/dev/null; then
            g stash drop --quiet
            echo "Tus cambios a mano se han vuelto a poner encima de la version nueva."
        else
            g reset --quiet --hard
            aviso "tus cambios a mano chocan con la version nueva, asi que se ha dejado la version nueva limpia. No se han perdido: estan en $parche (y en 'git stash list')."
            registrar "cambios a mano NO aplicados (chocan con la version nueva); guardados en $parche"
        fi
    fi

    rm -f "$APLAZADA" "$RECHAZADA"
    registrar "actualizado $(corto "$actual") -> $(corto "$nuevo") ($(cambios "$cuantos"), modo $MODO)"

    # ------------------------------------------------- 5. despues
    if ! g diff --quiet "$actual" "$nuevo" -- instalar.sh || [ -f "$INSTALAR_PENDIENTE" ]; then
        pasar_instalador "instalar.sh ha cambiado" || true
    fi

    [ "$abierto" = "si" ] || apagar_hub_viejo

    info "MEDIBOT actualizado a la version $(describir "$nuevo")"
    if [ "$abierto" = "si" ]; then
        printf '\n\033[1;33m  MEDIBOT esta abierto: cierralo y vuelve a abrirlo para usar la version nueva.\033[0m\n'
    fi
    if [ "$MODO" = "manual" ] && ! auto_activada; then
        echo
        echo "  Para que se actualice solo:  medibot-actualizar --activar-auto"
    fi
}

# ---------------------------------------------------------------- estado

estado() {
    echo "Carpeta:            $DESTINO"
    echo "Version instalada:  $(describir HEAD)  $(g log -1 --format='%s' HEAD)"

    local remoto local_origin
    printf 'En GitHub:          '
    if remoto="$(timeout 30 git -C "$DESTINO" ls-remote origin "refs/heads/$RAMA" 2>/dev/null | cut -f1)" \
            && [ -n "$remoto" ]; then
        if [ "$remoto" = "$(g rev-parse HEAD)" ]; then
            echo "estas al dia"
        elif g merge-base --is-ancestor "$remoto" HEAD 2>/dev/null; then
            echo "estas al dia (y con commits propios)"
        else
            local_origin="$(g rev-parse -q --verify "refs/remotes/origin/$RAMA" 2>/dev/null || true)"
            if [ "$remoto" = "$local_origin" ]; then
                echo "HAY VERSION NUEVA, ya descargada: se instala al abrir MEDIBOT (o ahora con: medibot-actualizar)"
            else
                echo "HAY VERSION NUEVA ($(printf '%.7s' "$remoto")). Para instalarla:  medibot-actualizar"
            fi
        fi
    else
        echo "no se pudo comprobar (¿hay internet?)"
    fi

    local cambios
    cambios="$(g status --porcelain --untracked-files=no)"
    if [ -n "$cambios" ]; then
        echo "Cambios a mano:     si (se mantienen al actualizar si no chocan):"
        printf '%s\n' "$cambios" | sed -n '1,10s/^/                      /p'
    fi

    printf 'Automatica:         '
    if auto_activada; then
        echo "ACTIVADA (mira cada hora y al encender la Pi)"
    else
        echo "desactivada  (para activarla:  medibot-actualizar --activar-auto)"
    fi
    if [ -f "$ESTADO/ultima-comprobacion" ]; then
        echo "Ultima descarga:    $(cat "$ESTADO/ultima-comprobacion")"
    fi
    if [ -f "$INSTALAR_PENDIENTE" ]; then
        echo "Pendiente:          repasar la instalacion ($(cat "$INSTALAR_PENDIENTE")): ejecuta  medibot-actualizar"
    fi
    if [ -s "$REGISTRO" ]; then
        echo
        echo "Ultimo del registro ($REGISTRO):"
        tail -n 8 "$REGISTRO" | sed 's/^/  /'
    fi
}

# ------------------------------------------------- actualizacion automatica

activar_auto() {
    hay_systemd || fallo "esta maquina no usa systemd; la actualizacion automatica no se puede activar aqui."
    local usuario ruta
    usuario="$(id -un)"
    #  En los ficheros de systemd, '%' es especial: se escribe '%%'.
    ruta="${DESTINO//%/%%}"

    info "Activando la actualizacion automatica (pedira tu contraseña)"
    sudo tee "$SERVICIO" > /dev/null <<EOF
# Lo genera $DESTINO/actualizar.sh --activar-auto (no editar a mano).
[Unit]
Description=MEDIBOT: actualizacion automatica del codigo
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
User=$usuario
ExecStart=/bin/bash "$ruta/actualizar.sh" --auto
Nice=10
IOSchedulingClass=idle
TimeoutStartSec=30min
EOF
    sudo tee "$TEMPORIZADOR" > /dev/null <<EOF
# Lo genera $DESTINO/actualizar.sh --activar-auto (no editar a mano).
[Unit]
Description=MEDIBOT: buscar actualizaciones cada hora

[Timer]
OnBootSec=2min
OnUnitActiveSec=1h
RandomizedDelaySec=5min

[Install]
WantedBy=timers.target
EOF
    sudo systemctl daemon-reload
    sudo systemctl enable --now "$UNIDAD.timer"
    registrar "actualizacion automatica activada"
    cat <<EOF
Actualizacion automatica ACTIVADA.
  - Mira si hay version nueva 2 minutos despues de encender la Pi y luego
    cada hora.
  - Con MEDIBOT cerrado, la instala en el momento. Con MEDIBOT abierto, la
    deja descargada y la instala la proxima vez que se abra MEDIBOT.
  - Ver como va:   medibot-actualizar --estado
  - Quitarla:      medibot-actualizar --desactivar-auto
EOF
}

desactivar_auto() {
    if [ ! -e "$TEMPORIZADOR" ] && [ ! -e "$SERVICIO" ]; then
        echo "La actualizacion automatica no estaba activada."
        return 0
    fi
    info "Quitando la actualizacion automatica (pedira tu contraseña)"
    sudo systemctl disable --now "$UNIDAD.timer" 2>/dev/null || true
    sudo rm -f "$TEMPORIZADOR" "$SERVICIO"
    sudo systemctl daemon-reload
    registrar "actualizacion automatica desactivada"
    echo "Actualizacion automatica desactivada. Para actualizar a mano:  medibot-actualizar"
}

# ------------------------------------------------------------------ main

main() {
    MODO="manual"
    local accion="actualizar"
    for arg in "$@"; do
        case "$arg" in
            --auto)             MODO="auto" ;;
            --al-arrancar)      MODO="al-arrancar" ;;
            --estado)           accion="estado" ;;
            --activar-auto)     accion="activar" ;;
            --desactivar-auto)  accion="desactivar" ;;
            -h|--help)          ayuda; exit 0 ;;
            *)                  ayuda; fallo "opcion desconocida: $arg" ;;
        esac
    done

    #  Como root, los ficheros nuevos quedarian a nombre de root y luego
    #  MEDIBOT (que corre como el usuario) no podria tocarlos.
    if [ "$(id -u)" -eq 0 ]; then
        fallo "no lo ejecutes con sudo ni como root. Ejecutalo como tu usuario normal: ya pedira la contraseña cuando haga falta."
    fi
    command -v git >/dev/null 2>&1 || fallo "no esta git instalado. Instala MEDIBOT con instalar.sh."

    #  Al terminar (bien o mal), esperar a que lo lean si se abrio desde el
    #  icono del menu: si no, la ventana se cerraria sin dejar ver nada.
    if [ -n "${MEDIBOT_PAUSA:-}" ] && [ -t 0 ]; then
        trap 'echo; read -r -p "Pulsa Enter para cerrar esta ventana..." _ || true' EXIT
    fi

    #  Que copia se actualiza: MEDIBOT_DIR, o la de este script, o ~/Proyects.
    local dir_script=""
    if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
        dir_script="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
    fi
    if [ -n "${MEDIBOT_DIR:-}" ]; then
        DESTINO="$MEDIBOT_DIR"
    elif [ -n "$dir_script" ] && [ -f "$dir_script/medibot/main.py" ]; then
        DESTINO="$dir_script"
    else
        DESTINO="$HOME/Proyects"
    fi
    if [ ! -d "$DESTINO/.git" ] || [ ! -f "$DESTINO/medibot/main.py" ]; then
        fallo "no encuentro MEDIBOT instalado en $DESTINO. Instalalo con:  curl -fsSL https://raw.githubusercontent.com/elporxdk/Proyects/main/instalar.sh | bash"
    fi
    DESTINO="$(cd "$DESTINO" && pwd)"

    #  El mismo Python que usa la orden 'medibot' (el del sistema, con los
    #  paquetes de apt).
    PY=/usr/bin/python3
    [ -x "$PY" ] || PY="$(command -v python3 || true)"
    [ -n "$PY" ] || fallo "no encuentro python3."

    mkdir -p "$ESTADO"
    recortar_registro

    case "$accion" in
        estado)     estado ;;
        activar)    activar_auto ;;
        desactivar) desactivar_auto ;;
        actualizar) actualizar ;;
    esac
}

main "$@"
exit $?
