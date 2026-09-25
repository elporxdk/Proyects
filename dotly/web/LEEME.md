# DOTLY web

Una web para **explorar, traducir y aprender braille**. Funciona sola, sin internet
y sin hardware. Si hay un DOTLY (un ESP32) cerca, se puede conectar a él: la celda
y el texto de la web aparecen en el aparato, y lo que se marque en el aparato
aparece en la web.

HTML, CSS y JavaScript sin librerías ni herramientas de compilación: unos 150 KB en total.

## Cómo abrirla

* **Como archivo:** doble clic en `index.html`. Funciona todo, también la conexión
  por WebSocket.
* **En un servidor web normal:** se copia la carpeta tal cual. Solo son archivos
  estáticos.
* **En la memoria del ESP32 (LittleFS o SPIFFS):** se copia el contenido de esta
  carpeta en la carpeta `data/` del sketch y se sube con el complemento
  *LittleFS upload* del IDE de Arduino 2. El firmware tiene que servirla, por
  ejemplo con `LittleFS.begin()` y `webServer.serveStatic("/", LittleFS, "/")`.
  El firmware actual de DOTLY todavía no lo hace: sirve su propia página
  (`pagina_web.h`).

## Qué hay en cada archivo

| Archivo | Qué hace |
|---|---|
| `index.html` | La estructura: cuatro pantallas y el diálogo de ajustes |
| `css/dotly.css` | Temas de alto contraste, la celda con puntos en relieve y el diseño para móvil |
| `js/braille.js` | **Motor braille:** la tabla de signos, el traductor y la lectura de puntos. No toca la página, así que también se usa en Node |
| `js/celda.js` | El componente «celda braille» de 6 puntos |
| `js/aprendizaje.js` | **Lógica de aprendizaje (modo profesor)** y su pantalla. Están separadas y marcadas con dos carteles |
| `js/conexion.js` | **Conexión con el ESP32** por WebSocket y HTTP. El protocolo está explicado al principio del archivo |
| `js/app.js` | Une las piezas: pantallas, ajustes, avisos y teclado |

Los scripts son clásicos, no módulos, para que la web funcione también como
archivo (`file://`) y desde la memoria del ESP32.

## Las pantallas

* **Explorar.** Una celda de 6 puntos en relieve. Al tocar los puntos se ve al
  momento qué signo forman: la letra, su nombre, sus puntos y el carácter
  Unicode. Si la combinación es una letra de la a a la j, avisa también de la
  cifra que sería detrás del signo de número. Debajo está el signo generador
  completo: tocar un signo lo lleva a la celda.
* **Traductor.** Mientras se escribe, sale el braille celda a celda, con cada
  letra debajo y los signos de mayúscula y de número resaltados. También sale en
  braille Unicode, listo para copiar o para leerlo en una línea braille.
* **Aprender.** Ocho lecciones en el orden en que se suele enseñar:
  1. Primera, segunda y tercera serie.
  2. La ñ y la w.
  3. Las tildes.
  4. Los números.
  5. Los signos de puntuación.
  6. Los signos de mayúscula, número y minúscula.

  Cada lección tiene fichas para estudiar y una práctica con dos clases de ejercicio:
  * **Formar:** sale una letra y hay que marcar sus puntos.
  * **Leer:** sale una celda y hay que elegir qué es.

  Si fallas, te dice qué punto falta y cuál sobra, y la celda los marca con «+» y
  «×». Hay pista y un resumen con estrellas. Las letras que más se fallan salen
  más a menudo. En **Práctica a medida (modo profesor)**, el profesor elige
  qué signos entran, cómo se pregunta y cuántas preguntas.
* **Conexión.** La IP del ESP32 y el botón «Conectar». Ver más abajo.

### El braille

Es la **signografía española** (ONCE):
* abecedario con ñ;
* á é í ó ú ü;
* números;
* los signos de puntuación . , ; : ¿? ¡! " ( ) -.

El traductor aplica estas reglas:

* **Mayúscula:** el signo 4-6 delante de la letra. Si la palabra entera va en
  mayúsculas, el signo va dos veces delante de la palabra (46 46).
* **Números:** el signo 3-4-5-6 una sola vez. La coma decimal (punto 2) y el punto
  de los millares (punto 3) no cortan el número: `3,5` y `1.000` llevan un solo
  signo de número.
* **Una letra de la a a la j pegada a un número** lleva delante el signo de
  minúscula (punto 5). Sin él, «2b» se leería «22».

El traductor del firmware de DOTLY (`/api/pizarra`) todavía no aplica las dos
últimas reglas ni la de la palabra entera en mayúsculas.

## Accesibilidad

* Cada punto es un botón de verdad, con `aria-pressed`. Se usa con el ratón, con el
  dedo, con el tabulador y con un lector de pantalla.
* **Teclado:**
  * de <kbd>1</kbd> a <kbd>6</kbd> enciende o apaga ese punto;
  * <kbd>F</kbd> <kbd>D</kbd> <kbd>S</kbd> <kbd>J</kbd> <kbd>K</kbd> <kbd>L</kbd>
    pulsadas a la vez funcionan como una máquina Perkins;
  * las flechas pasan de un punto a otro dentro de la celda;
  * <kbd>Retroceso</kbd> borra la celda;
  * <kbd>Intro</kbd> comprueba y pasa a la pregunta siguiente.
* **Lector de pantalla:** anuncia qué signo forman los puntos, las preguntas y
  las correcciones. Al cambiar de pantalla, el foco va a su título. Hay un enlace
  «Saltar al contenido».
* **Opcional:** leer en voz alta (con la voz del navegador) y vibrar al tocar un
  punto en el móvil.
* **Colores:**
  * claro y oscuro, los dos de alto contraste;
  * amarillo sobre negro, para baja visión;
  * funciona con el modo de alto contraste de Windows;
  * lo que se indica con color tiene también un símbolo (✓, ✗, +, ×).
* **Tamaño:** el texto crece hasta el 150 % sin que nada se salga de la
  pantalla. Todos los botones miden 48 px o más.
* Si el sistema pide menos animaciones, se quitan.
* Se comprueba con axe-core, con las pautas WCAG 2.2 AA y AAA (contraste de 7:1
  incluido). Con el tema claro se revisan todas las pantallas, y las cuatro
  principales también con el oscuro y con el amarillo.

## Conectar con el ESP32

En **Conexión** se escribe la IP del ESP32 (con la red propia de DOTLY suele ser
`192.168.4.1`) y se pulsa **Conectar**. La web prueba a la vez WebSocket
(`ws://IP:81/` y `ws://IP/ws`) y HTTP (`http://IP/api/estado`), y se queda con el
que conteste. En «Opciones avanzadas» se puede fijar el camino, el puerto y la ruta
del WebSocket, y cada cuánto se consulta por HTTP.

| Sentido | Qué viaja |
|---|---|
| Web → ESP32 | La celda de Explorar y la letra que forma |
| Web → ESP32 | El texto del Traductor |
| Web → ESP32 | En una práctica, la celda del ejercicio sin la respuesta, para que se pueda tocar en una celda física |
| ESP32 → Web | Los puntos que se marquen en el aparato: en Explorar mueven la celda y en una práctica son la respuesta |
| ESP32 → Web | Sus botones: aceptar, siguiente, anterior y borrar |

Si se corta la conexión, se reintenta sola. Sin conexión, la web sigue
funcionando por su cuenta.

### Por qué a veces no conecta

* **La página está en https** (por ejemplo, publicada en GitHub Pages): los
  navegadores no dejan que una página https hable con un aparato por `http://` o
  `ws://`. La web lo detecta y lo explica. La solución es abrirla como archivo,
  desde un servidor http o desde el propio ESP32.
* **El ESP32 no manda la cabecera CORS:** el navegador deja enviarle datos, pero no
  leer sus respuestas. La web se queda en **solo envío** y lo explica. Es lo que
  pasa con el firmware actual de DOTLY: el texto y las letras aparecen en su
  pantalla (por `/api/pizarra`), pero la web no recibe nada de él.
* **La red:** el móvil o el ordenador tienen que estar en la misma WiFi que el
  ESP32.

### El protocolo

Mensajes JSON. Por WebSocket van tal cual; por HTTP van como formularios. La
especificación completa está al principio de `js/conexion.js`.

| Mensaje | Por WebSocket | Por HTTP |
|---|---|---|
| Celda (web → ESP32) | `{"tipo":"celda","puntos":19,"caracter":"h"}` | `POST /api/celda` `p=19&c=h` |
| Texto (web → ESP32) | `{"tipo":"texto","texto":"Hola"}` | `POST /api/pizarra` `t=Hola` |
| Celda del aparato (ESP32 → web) | `{"tipo":"celda","puntos":7}` | `GET /api/estado` → `"celda":{"puntos":7,"n":3}` |
| Botón del aparato (ESP32 → web) | `{"tipo":"tecla","tecla":"aceptar"}` | `GET /api/estado` → `"tecla":{"tecla":"aceptar","n":5}` |

`puntos` va de 0 a 63: el bit 0 es el punto 1 y el bit 5 el punto 6, igual que en
Unicode (U+2800 + puntos). Por HTTP, `n` cuenta los cambios hechos en el aparato:
cuando cambia, hay algo nuevo.

### Lo que le haría falta al firmware para la conexión completa

Esto es una guía para cuando se programe el ESP32. Esta web no lo necesita para
funcionar.

1. **CORS en su API HTTP**, para que la web pueda leer sus respuestas aunque no la
   sirva él. En cada respuesta:
   `webServer.sendHeader("Access-Control-Allow-Origin", "*");`
2. **`POST /api/celda`** (`p` = puntos) para mover una celda física, y los campos
   `celda` y `tecla` con su contador `n` en `/api/estado`.
3. **Opcional, WebSocket** para ir en tiempo real, por ejemplo con la librería
   *WebSockets* de Markus Sattler en el puerto 81:

   ```cpp
   #include <WebSocketsServer.h>
   WebSocketsServer ws(81);

   void alMensaje(uint8_t cliente, WStype_t tipo, uint8_t *datos, size_t largo) {
     if (tipo != WStype_TEXT) return;
     // {"tipo":"celda","puntos":19,...} -> subir los puntos de la celda física
   }
   // setup():  ws.begin(); ws.onEvent(alMensaje);
   // loop():   ws.loop();
   // al marcar puntos en el aparato:
   //   ws.broadcastTXT("{\"tipo\":\"celda\",\"puntos\":7}");
   ```

## Probarla sin hardware

* `node pruebas/web_dotly/esp32_simulado.js` arranca un ESP32 simulado que habla
  este protocolo: HTTP en el puerto 8080 y WebSocket en el 8081.
  1. En la web, escribe `127.0.0.1:8080` como dirección y, en «Opciones
     avanzadas», `8081` como puerto WebSocket.
  2. En la terminal, `c 125` hace que «el aparato» marque los puntos 1, 2 y 5, y
     `t aceptar` pulsa un botón.
* `pruebas/web_dotly/correr.sh` pasa todas las pruebas:
  * la lógica en Node;
  * la página en Chromium, con el ratón y con el teclado;
  * lecciones enteras;
  * axe-core en cada pantalla y cada tema;
  * el móvil y el texto enorme;
  * la conexión con el ESP32 simulado (WebSocket, HTTP y sin CORS);
  * la conexión con el firmware real de DOTLY, corriendo en su banco de pruebas.
