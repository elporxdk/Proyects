#!/bin/sh
# =====================================================================
#  Pruebas de la web de DOTLY (dotly/web)
# =====================================================================
#  1. La lógica en Node: el motor braille contra una tabla escrita aparte,
#     las reglas de mayúsculas y números, las lecciones y la dirección IP.
#  2. La página en Chromium (Playwright): con el ratón y con el teclado,
#     lecciones enteras, los temas, el móvil, axe-core (accesibilidad), un
#     ESP32 simulado (WebSocket, HTTP y sin CORS) y el firmware REAL de
#     DOTLY corriendo en su banco de pruebas.
#
#  Uso:   ./correr.sh
# =====================================================================
set -e
cd "$(dirname "$0")"

echo "Lógica (braille, lecciones, conexión)..."
node --test logica.test.js

# axe-core, para auditar la accesibilidad (se instala una vez en .libs/)
if [ ! -f .libs/node_modules/axe-core/axe.min.js ]; then
  echo "Instalando axe-core en .libs/ ..."
  (mkdir -p .libs && cd .libs && npm install --silent --no-audit --no-fund axe-core@4) ||
    echo "(sin axe-core: no se auditará la accesibilidad)"
fi

# el firmware de DOTLY en el PC, para probar la conexión con él
if [ ! -x ../banco_dotly/.build/banco_dotly ]; then
  echo "Compilando el banco de DOTLY..."
  (cd ../banco_dotly && mkdir -p .build &&
    g++ -std=c++17 -O1 -Ishim banco_dotly.cpp shim/shim.cpp -lpthread -o .build/banco_dotly) ||
    echo "(no se pudo compilar: se omite la prueba con el firmware)"
fi

echo "La página en Chromium..."
NODE_PATH=${NODE_PATH:-$(npm root -g 2>/dev/null)} node e2e.js
