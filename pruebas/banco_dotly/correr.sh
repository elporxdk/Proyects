#!/bin/sh
# =====================================================================
#  Banco de pruebas de DOTLY (se ejecuta en el PC, sin placa)
# =====================================================================
#  Compila dotly/dotly.ino, EL MISMO que se graba, como un programa de PC
#  contra los sustitutos de shim/ (LCD, WiFi, servidor web con socket de
#  verdad, NVS en un fichero, ADC simulado) y lo maneja como una persona.
#
#  Uso:   ./correr.sh            (todo: comprobaciones + casos + navegador)
#         ./correr.sh letras     (un solo caso)
# =====================================================================
set -e
cd "$(dirname "$0")"

INO=../../dotly/dotly.ino
CXX=${CXX:-g++}
FLAGS="-std=c++17 -O1 -g -Wall -Wextra -Wno-unused-parameter -Wno-missing-field-initializers -Werror=format -Ishim"

echo "Comprobando los prototipos que genera el IDE de Arduino..."
python3 ../banco_firmware/comprobar_prototipos.py "$INO"

echo "Comprobando las APIs contra arduino-esp32 y LiquidCrystal_I2C reales..."
python3 comprobar_api.py "$INO" -- $FLAGS

mkdir -p .build
echo "Compilando el banco..."
$CXX $FLAGS banco_dotly.cpp shim/shim.cpp -lpthread -o .build/banco_dotly 2> .build/avisos.txt || { cat .build/avisos.txt; exit 1; }
if grep -q "dotly.ino" .build/avisos.txt; then
  echo "Avisos del compilador en el firmware:"; grep -A3 "dotly.ino" .build/avisos.txt; exit 1
fi

export DOTLY_NVS=.build/nvs
# Orden con intencion: los casos se apoyan en la memoria que deja el anterior
# (calibracion, ajustes, progreso), como una placa que se usa dia tras dia.
CASOS=${1:-"fabrica mantenida menubase letras escribir retos palabras senas web persistencia ruido sinteclado"}
[ -n "$1" ] || rm -f .build/nvs.dotly

fallos=0
for caso in $CASOS; do
  case "$caso" in fabrica|sinteclado) rm -f .build/nvs.dotly ;; esac
  if ./.build/banco_dotly "$caso" > ".build/salida_$caso.txt" 2>&1; then
    echo "  OK    $caso"
  else
    echo "  FALLO $caso   (ver .build/salida_$caso.txt)"
    grep -E "^   FALLO" ".build/salida_$caso.txt" | sed 's/^/       /'
    fallos=$((fallos + 1))
  fi
done

if [ -z "$1" ]; then
  echo "La pagina en un navegador de verdad (Chromium + Playwright)..."
  rm -f .build/nvs.dotly
  if NODE_PATH=${NODE_PATH:-$(npm root -g 2>/dev/null)} node e2e_pagina.js > .build/salida_navegador.txt 2>&1; then
    echo "  OK    navegador"
  else
    echo "  FALLO navegador   (ver .build/salida_navegador.txt)"
    grep -E "FALLO" .build/salida_navegador.txt | sed 's/^/       /'
    fallos=$((fallos + 1))
  fi
fi

echo "---"
if [ $fallos -eq 0 ]; then echo "TODO OK"; else echo "$fallos caso(s) con fallos"; fi
exit $fallos
