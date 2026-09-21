#!/bin/sh
# =====================================================================
#  Banco de pruebas del firmware MEDIBOT (se ejecuta en el PC, sin placa)
# =====================================================================
#  Compila los sketches de firmware/ como un programa de PC contra unos
#  sustitutos de Arduino, U8g2, Wire y Preferences, y con un MAX30102
#  SIMULADO: el sensor falso genera una PPG sintetica con un pulso y una SpO2
#  conocidos, y el banco pulsa los botones y lee el texto de la pantalla como
#  lo haria una persona.
#
#  El algoritmo de SpO2 que se prueba es el REAL (spo2_algorithm.cpp de la
#  libreria SparkFun), no una copia.
#
#  Uso:   ./correr.sh              (todos los casos)
#         ./correr.sh normal       (un solo caso del triaje)
#
#  Librerias: usa las del IDE (~/Arduino/libraries). Si no estan, se pueden
#  indicar con MEDIBOT_LIBS=/ruta/a/libraries, o dejar que se clonen en .libs/
# =====================================================================
set -e
cd "$(dirname "$0")"

RAIZ=../..
TRIAJE=$RAIZ/firmware/medibot_triaje/medibot_triaje.ino
PANEL=$RAIZ/firmware/medibot_panel/medibot_panel.ino

# ---- localizar las librerias de Arduino que hacen falta ----------------
buscar() {                       # buscar <nombre-de-carpeta> <fichero-clave>
  for base in "$MEDIBOT_LIBS" "$HOME/Arduino/libraries" "$HOME/Documents/Arduino/libraries" ".libs"; do
    [ -n "$base" ] || continue
    for d in "$base"/*"$1"*; do
      [ -f "$d/src/$2" ] && { echo "$d/src"; return 0; }
    done
  done
  return 1
}

MAX3010X=$(buscar MAX3010x spo2_algorithm.cpp || true)
JSON=$(buscar ArduinoJson ArduinoJson.h || true)

if [ -z "$MAX3010X" ]; then
  echo "No encuentro la libreria SparkFun MAX3010x: la clono en .libs/"
  mkdir -p .libs
  git clone --depth 1 https://github.com/sparkfun/SparkFun_MAX3010x_Sensor_Library .libs/SparkFun_MAX3010x >/dev/null 2>&1
  MAX3010X=.libs/SparkFun_MAX3010x/src
fi
if [ -z "$JSON" ]; then
  echo "No encuentro ArduinoJson: la clono en .libs/"
  mkdir -p .libs
  git clone --depth 1 -b 7.x https://github.com/bblanchon/ArduinoJson .libs/ArduinoJson >/dev/null 2>&1
  JSON=.libs/ArduinoJson/src
fi

CXX=${CXX:-g++}
COMUN="-std=c++17 -O1 -g -DARDUINO=200 -Wno-narrowing -Werror=format-truncation -Ishim -I$MAX3010X"
JSONDEF="-DARDUINOJSON_ENABLE_ARDUINO_STRING=0 -DARDUINOJSON_ENABLE_ARDUINO_STREAM=0 \
         -DARDUINOJSON_ENABLE_ARDUINO_PRINT=0 -DARDUINOJSON_ENABLE_PROGMEM=0 \
         -DARDUINOJSON_ENABLE_STD_STRING=1"

# ---- el .ino tiene que sobrevivir al preprocesador del IDE de Arduino -------
echo "Comprobando los prototipos que genera el IDE..."
python3 comprobar_prototipos.py "$TRIAJE" "$PANEL"

# ---- y no puede bloquear el nucleo con las interrupciones cortadas ---------
#  Esto no lo ve el compilador ni el banco (en el PC no hay watchdog): un
#  WiFi.RSSI() dentro de portENTER_CRITICAL reinicia la placa en bucle.
echo "Comprobando las secciones criticas..."
python3 comprobar_criticas.py "$TRIAJE" "$PANEL"

# ---- y compilar con las DOS versiones del core ESP32 -----------------------
#  Varias APIs cambiaron de la 2.x a la 3.x (por ejemplo MDNS.IP() paso a
#  llamarse MDNS.address()). El sketch elige una u otra con #if, asi que hay
#  que compilar las dos ramas: con una sola, la otra puede estar rota y no
#  enterarse nadie hasta que alguien la graba.
for v in 2 3; do
  echo "Comprobando la sintaxis con el core ESP32 $v.x..."
  for sk in "$TRIAJE" "$PANEL"; do
    $CXX $COMUN $JSONDEF -DESP_ARDUINO_VERSION_MAJOR=$v -Ishim_red -I"$JSON" \
         -fsyntax-only -x c++ "$sk"
  done
done

# ---- y las APIs del core tienen que existir DE VERDAD ----------------------
#  Compilar contra los sustitutos no demuestra nada si un sustituto tiene un
#  metodo que el core real no tiene: eso solo se ve en el IDE. Aqui se
#  contrasta contra las cabeceras de arduino-esp32 (se cachean en .libs/).
echo "Comprobando las APIs contra el core ESP32 real..."
python3 comprobar_api_esp32.py "$TRIAJE" "$PANEL" --flags \
  $COMUN $JSONDEF -Ishim_red -I"$JSON"

mkdir -p .build
echo "Compilando el banco del triaje..."
$CXX $COMUN $JSONDEF -Ishim_red -I"$JSON" -x c++ "$TRIAJE" banco_triaje.cpp \
     shim/shim.cpp shim_red/red_shim.cpp \
     "$MAX3010X/spo2_algorithm.cpp" -lpthread -o .build/banco_triaje

echo "Compilando el banco del panel..."
$CXX $COMUN $JSONDEF -Ishim_red -I"$JSON" -x c++ "$PANEL" banco_panel.cpp \
     shim/shim.cpp shim_red/red_shim.cpp \
     "$MAX3010X/spo2_algorithm.cpp" -lpthread -o .build/banco_panel

export MEDIBOT_NVS=.build/nvs
CASOS=${1:-"calibrar calibruido yacalibrado normal modoseguro wifi wifibarrido sinwifi apicaida redymedida diagnostico web sindedo dedofuera sensorcuelga sinsensor sensorlento sinmemoria botonpulsado busalaire buscorto tecladosuelto"}
[ -n "$1" ] || rm -f .build/nvs.medibot     # placa "de fabrica" al empezar

fallos=0
for caso in $CASOS; do
  # Los casos que prueban el ASISTENTE necesitan la placa "de fabrica": con una
  # calibracion ya guardada el asistente no se abre solo y no habria nada que
  # probar. Los demas se apoyan en lo que dejo el anterior, a proposito.
  case "$caso" in calibrar|calibruido) rm -f .build/nvs.medibot ;; esac
  if ./.build/banco_triaje "$caso" 72 98 > ".build/salida_$caso.txt" 2>&1; then
    echo "  OK    triaje/$caso"
  else
    echo "  FALLO triaje/$caso   (ver .build/salida_$caso.txt)"
    grep -E "^   FALLO" ".build/salida_$caso.txt" | sed 's/^/       /'
    fallos=$((fallos + 1))
  fi
done

if [ -z "$1" ]; then
  rm -f .build/nvs.medibot
  for pcaso in normal botonpulsado tecladosuelto calibruido; do
    rm -f .build/nvs.medibot
    if ./.build/banco_panel 72 "$pcaso" > ".build/salida_panel_$pcaso.txt" 2>&1; then
      echo "  OK    panel/$pcaso"
    else
      echo "  FALLO panel/$pcaso   (ver .build/salida_panel_$pcaso.txt)"
      grep -E "^   FALLO" ".build/salida_panel_$pcaso.txt" | sed 's/^/       /'
      fallos=$((fallos + 1))
    fi
  done
fi

echo "---"
if [ $fallos -eq 0 ]; then echo "TODO OK"; else echo "$fallos caso(s) con fallos"; fi
exit $fallos
