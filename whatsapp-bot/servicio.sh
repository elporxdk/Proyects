#!/usr/bin/env bash
#
# Deja el bot corriendo como servicio de systemd: arranca solo al encender la
# Raspberry Pi y se levanta otra vez si se cae.
#
#   ./servicio.sh instalar     lo registra y lo arranca
#   ./servicio.sh estado       ve si esta corriendo
#   ./servicio.sh logs         mira la salida en vivo
#   ./servicio.sh reiniciar    tras cambiar index.js o .env
#   ./servicio.sh quitar       lo desinstala (no borra nada del proyecto)
#
# IMPORTANTE: vincula el telefono ANTES de instalar el servicio. Como servicio
# no hay terminal donde mostrar el codigo QR, asi que corre `npm start` a mano
# una vez, escanea el QR, cierra con Ctrl+C, y recien entonces instala.

set -euo pipefail

SERVICIO="medibot-whatsapp"
UNIDAD="/etc/systemd/system/${SERVICIO}.service"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

rojo()  { printf '\033[1;31m%s\033[0m\n' "$*"; }
verde() { printf '\033[1;32m%s\033[0m\n' "$*"; }
aviso() { printf '\033[1;33m%s\033[0m\n' "$*"; }

morir() { rojo "ERROR: $*"; exit 1; }

command -v systemctl >/dev/null 2>&1 || morir "Este sistema no usa systemd; el servicio solo sirve en Linux (Raspberry Pi OS, Debian, Ubuntu)."

instalar() {
  [ -f "$DIR/index.js" ] || morir "No encuentro index.js junto a este script."
  [ -f "$DIR/.env" ]     || morir "No hay .env. Corre ./setup.sh primero."
  [ -d "$DIR/node_modules" ] || morir "Faltan las dependencias. Corre ./setup.sh primero."

  # Sin sesion vinculada el servicio arrancaria pidiendo un QR que nadie puede ver.
  if [ ! -d "$DIR/.wwebjs_auth" ]; then
    aviso "Todavia no has vinculado el telefono."
    aviso "Corre primero:  cd $DIR && npm start"
    aviso "Escanea el QR, cierra con Ctrl+C, y vuelve a intentar esto."
    exit 1
  fi

  local NODE_BIN
  NODE_BIN="$(command -v node)" || morir "No encuentro node."

  echo "Creando $UNIDAD ..."
  sudo tee "$UNIDAD" >/dev/null <<EOF
[Unit]
Description=Chatbot de WhatsApp de Medibot
Documentation=https://github.com/elporxdk/Proyects/tree/whatsapp-bot-standalone
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$DIR
ExecStart=$NODE_BIN index.js
# Si el proceso muere (sin red, WhatsApp lo desconecta, un fallo), vuelve a
# levantarlo en 15 segundos en vez de quedarse caido hasta que alguien mire.
Restart=on-failure
RestartSec=15
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

  sudo systemctl daemon-reload
  sudo systemctl enable --now "$SERVICIO"

  echo
  verde "Instalado y arrancado."
  echo "Compruebalo con:  ./servicio.sh estado"
  echo "Mira los logs con: ./servicio.sh logs"
}

quitar() {
  sudo systemctl disable --now "$SERVICIO" 2>/dev/null || true
  sudo rm -f "$UNIDAD"
  sudo systemctl daemon-reload
  verde "Servicio quitado. El proyecto y tu sesion de WhatsApp siguen intactos."
}

case "${1:-}" in
  instalar)  instalar ;;
  quitar)    quitar ;;
  estado)    systemctl status "$SERVICIO" --no-pager ;;
  logs)      journalctl -u "$SERVICIO" -f -n 50 ;;
  reiniciar) sudo systemctl restart "$SERVICIO" && verde "Reiniciado." ;;
  *)
    echo "Uso: ./servicio.sh {instalar|quitar|estado|logs|reiniciar}"
    exit 1
    ;;
esac
