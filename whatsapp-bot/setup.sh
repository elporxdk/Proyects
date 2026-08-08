#!/usr/bin/env bash
#
# Instalador del chatbot de WhatsApp de Medibot.
#
# Deja el bot listo para arrancar en un solo comando: descarga el codigo,
# instala Chromium y las dependencias, y crea el .env con tu clave.
#
# Uso (desde cero, sin haber clonado nada):
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/elporxdk/Proyects/whatsapp-bot-standalone/setup.sh)"
#
# Se puede volver a correr las veces que haga falta: no borra tu .env ni la
# sesion de WhatsApp ya vinculada.

set -euo pipefail

REPO="https://github.com/elporxdk/Proyects.git"
RAMA="whatsapp-bot-standalone"
DESTINO="${MEDIBOT_DIR:-$HOME/medibot-bot}"

rojo()  { printf '\033[1;31m%s\033[0m\n' "$*"; }
verde() { printf '\033[1;32m%s\033[0m\n' "$*"; }
azul()  { printf '\033[1;34m%s\033[0m\n' "$*"; }
aviso() { printf '\033[1;33m%s\033[0m\n' "$*"; }

morir() { rojo "ERROR: $*"; exit 1; }

# ---------------------------------------------------------------- 1. Sistema

azul "==> 1/5  Comprobando lo que ya tienes instalado"

ES_LINUX=false
[ "$(uname -s)" = "Linux" ] && ES_LINUX=true

# `apt update` puede fallar por un repositorio ajeno caido o sin firma. Eso no
# es motivo para abortar la instalacion, asi que nunca lo dejamos matar al
# script: avisamos y seguimos con los paquetes que apt ya conoce.
apt_update() {
  sudo apt update -qq 2>/dev/null || aviso "    ('apt update' dio errores; sigo con lo que apt ya tiene en cache.)"
}

if ! command -v git >/dev/null 2>&1; then
  $ES_LINUX && command -v apt >/dev/null 2>&1 || morir "Falta git. Instalalo desde https://git-scm.com y vuelve a correr esto."
  echo "    Instalando git..."
  apt_update
  sudo apt install -y -qq git 2>/dev/null || true
  command -v git >/dev/null 2>&1 || morir "No pude instalar git. Instalalo a mano con: sudo apt install git"
fi

command -v node >/dev/null 2>&1 || morir "Falta Node.js. Instala la version LTS desde https://nodejs.org y vuelve a correr esto."

NODE_MAYOR=$(node -p 'process.versions.node.split(".")[0]')
[ "$NODE_MAYOR" -ge 18 ] || morir "Node.js $(node -v) es muy viejo; hace falta 18 o superior."

echo "    git $(git --version | awk '{print $3}')  |  node $(node -v)"

# --------------------------------------------------------------- 2. Chromium
#
# whatsapp-web.js controla WhatsApp Web con un Chromium de verdad. Puppeteer
# intenta descargarse el suyo, pero en Raspberry Pi (ARM) esa descarga suele
# quedar rota, asi que preferimos el Chromium del sistema cuando existe.

azul "==> 2/5  Buscando Chromium"

CHROME_BIN="$(command -v chromium-browser || command -v chromium || command -v google-chrome || true)"

if [ -z "$CHROME_BIN" ] && $ES_LINUX && command -v apt >/dev/null 2>&1; then
  echo "    No hay ninguno instalado; instalando desde apt..."
  apt_update
  # El paquete se llama distinto segun la version de Raspberry Pi OS / Debian.
  sudo apt install -y -qq chromium-browser 2>/dev/null \
    || sudo apt install -y -qq chromium 2>/dev/null \
    || true
  CHROME_BIN="$(command -v chromium-browser || command -v chromium || true)"
fi

if [ -n "$CHROME_BIN" ]; then
  echo "    Usando: $CHROME_BIN"
else
  aviso "    No encontre Chromium del sistema; dejare que Puppeteer descargue el suyo."
  aviso "    (Normal en Windows y Mac. En Raspberry Pi suele fallar: instala"
  aviso "     'chromium' con apt y vuelve a correr este script.)"
fi

# ----------------------------------------------------------------- 3. Codigo

azul "==> 3/5  Descargando el codigo del bot"

# Si el script vive junto al proyecto, trabajamos ahi mismo en vez de clonar.
AQUI=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
  AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

if [ -n "$AQUI" ] && [ -f "$AQUI/index.js" ] && [ -f "$AQUI/package.json" ]; then
  DESTINO="$AQUI"
  echo "    Ya estoy dentro del proyecto: $DESTINO"
elif [ -f "$DESTINO/index.js" ]; then
  echo "    Ya existe en $DESTINO; actualizando..."
  git -C "$DESTINO" pull --quiet origin "$RAMA" || aviso "    No pude actualizar (sin red?); sigo con lo que hay."
else
  # --depth 1 --single-branch: baja menos de 1 MB en vez del repositorio entero.
  echo "    Clonando en $DESTINO..."
  git clone --quiet --single-branch --branch "$RAMA" --depth 1 "$REPO" "$DESTINO"
fi

cd "$DESTINO"

# ----------------------------------------------------------- 4. Dependencias

azul "==> 4/5  Instalando dependencias de Node (puede tardar unos minutos)"

if [ -n "$CHROME_BIN" ]; then
  # Con Chromium del sistema no hace falta que Puppeteer baje el suyo.
  PUPPETEER_SKIP_DOWNLOAD=true npm install --no-audit --no-fund --loglevel=error
else
  npm install --no-audit --no-fund --loglevel=error
fi

# ------------------------------------------------------------------ 5. .env

azul "==> 5/5  Configurando la clave de la API"

[ -f .env ] || cp .env.example .env

# Lee el valor actual de una variable del .env, si lo hay.
leer_env() { grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2- || true; }

# Escribe una variable en el .env: reemplaza la linea si existe, si no la añade.
escribir_env() {
  local clave="$1" valor="$2" tmp
  tmp="$(mktemp)"
  grep -vE "^$clave=" .env > "$tmp" 2>/dev/null || true
  printf '%s=%s\n' "$clave" "$valor" >> "$tmp"
  mv "$tmp" .env
  chmod 600 .env
}

API_KEY="$(leer_env ANTHROPIC_API_KEY)"

if [ -n "$API_KEY" ]; then
  echo "    Ya tienes una clave guardada; la dejo como esta."
elif [ -t 0 ]; then
  echo "    Sacala de https://console.anthropic.com/settings/keys"
  printf '    Pega tu ANTHROPIC_API_KEY (no se vera al escribir): '
  read -rs API_KEY
  echo
  [ -n "$API_KEY" ] && escribir_env ANTHROPIC_API_KEY "$API_KEY" \
    || aviso "    No pegaste nada; tendras que editar .env a mano antes de arrancar."
else
  aviso "    Sin terminal interactiva: edita .env y pon tu ANTHROPIC_API_KEY a mano."
fi

[ -n "$CHROME_BIN" ] && escribir_env CHROME_PATH "$CHROME_BIN"

# ------------------------------------------------------------------- Listo

echo
verde "================================================================"
verde " Listo. El bot esta instalado en:"
verde "   $DESTINO"
verde "================================================================"
echo
echo "Para arrancarlo:"
echo
echo "    cd $DESTINO && npm start"
echo
echo "La primera vez sale un codigo QR en la terminal. Escanealo desde tu"
echo "telefono: WhatsApp > Ajustes > Dispositivos vinculados > Vincular un"
echo "dispositivo. Despues de eso ya no lo vuelve a pedir."
echo
echo "Cuando ya lo hayas vinculado, si quieres que arranque solo al encender"
echo "la Raspberry Pi:"
echo
echo "    cd $DESTINO && ./servicio.sh instalar"
echo
