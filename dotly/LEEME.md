# DOTLY · Braille, punto a punto

DOTLY enseña a leer y escribir braille, y el alfabeto manual de las lenguas de
señas. Tiene una pantalla LCD de 16x2, un teclado de 5 botones y su propia red
WiFi con una página web desde la que se ve todo y se maneja todo.

⠙⠕⠞⠇⠽

## Qué se graba

Se abre **`dotly/dotly.ino`** en el IDE de Arduino y se graba. `pagina_web.h`
tiene que estar en la misma carpeta, porque es la página que sirve DOTLY y va
dentro del firmware. No hay que subir nada a SPIFFS ni a LittleFS.

* **Placa:** ESP32-S3 (vale también un S2). El teclado va al GPIO8, que en el
  S3 es analógico y sigue funcionando con el WiFi encendido. En un ESP32
  clásico el GPIO8 es de la memoria flash: el código no compila y te dice qué
  pin usar.
* **Librería:** solo *LiquidCrystal I2C* (Frank de Brabander), del gestor de
  librerías. WiFi, WebServer, DNSServer y Preferences vienen con el core ESP32.
  Solo usa funciones que existen tanto en el core 2.x como en el 3.x (el banco
  de pruebas lo comprueba contra sus cabeceras).

## Conexiones

| Componente | Pin del componente | ESP32-S3 |
|---|---|---|
| LCD 16x2 con módulo I2C (0x27) | SDA | GPIO4 |
| | SCL | GPIO5 |
| | VCC | 5 V |
| | GND | GND |
| Teclado analógico de 5 botones (ADKeyboard) | OUT | GPIO8 |
| | VCC | **3V3** |
| | GND | GND |

**El teclado va a 3V3, no a 5 V.** A 3,3 V los cinco botones dan 0 / 0,46 / 0,99 /
1,65 / 2,44 V y se distinguen bien. A 5 V el quinto botón da 3,7 V: el ADC del
ESP32 no pasa de unos 3,1 V, así que el botón se confunde con "nada pulsado".

## Los botones

| Botón | Qué hace |
|---|---|
| SW1 | Entrar / aceptar |
| SW2 | Anterior (si lo mantienes, va corriendo) |
| SW3 | Siguiente (si lo mantienes, va corriendo) |
| SW4 | Volver |
| SW5 | Repetir |

## Los modos

El menú principal lleva a nueve modos:

1. **Aprender:** el menú de bloques de siempre (`SELECCIONA` / `> BLOQUE 1`,
   `BLOQUE 1` / `A B C D E F`). Hay 8 bloques: las 27 letras en cinco bloques,
   las vocales con tilde, los números y los signos de puntuación. Desde un
   bloque, SW1 entra letra a letra y enseña cada una con su celda y sus puntos.
2. **Escribir:** una máquina de escribir braille. Marcas los puntos
   (`1 2 3 4 5 6 <`) y DOTLY dice qué letra has formado. SW5 la escribe y `<`
   borra. Entiende el signo de número (3-4-5-6).
3. **Reto: leer:** sale una celda y eliges su letra entre cuatro. Las opciones
   falsas son las que más se parecen a la buena.
4. **Reto: formar:** sale una letra y tienes que formarla punto a punto.
5. **Palabras:** palabras enteras en braille. Primero se leen y luego se
   destapan para comprobar. La lista se cambia desde la página.
6. **Señas:** el alfabeto manual letra a letra, con la celda braille de cada
   letra y una descripción que se desplaza por la pantalla.
7. **Reto: señas:** DOTLY describe una seña y adivinas de qué letra es.
8. **Progreso:** porcentajes de acierto, racha máxima, letras vistas y las tres
   letras que más cuestan.
9. **Ajustes:** calibrar el teclado, la red WiFi, preguntas por reto (5/10/20),
   velocidad del texto (lenta/media/rápida), nivel (a–j, a–t, abecedario, con
   tildes), borrar el progreso y "Acerca de".

En los retos salen más a menudo las letras que más se fallan. Cuando fallas, la
luz de la pantalla parpadea y DOTLY enseña cuál era. Todo se guarda en la
memoria del ESP32, así que el progreso sigue ahí al apagarlo.

El braille es la **signografía española** (ONCE): abecedario con ñ, á é í ó ú ü,
números con su signo (3-4-5-6), mayúsculas con el suyo (4-6) y . , ; : ¿? ¡! " ( ) -.
Las celdas se dibujan en la pantalla con los caracteres propios del LCD. Un punto
marcado es un cuadrado lleno y uno sin marcar es un puntito, para que se vea la
rejilla de 6.

## La página web

DOTLY crea su propia red WiFi: **`DOTLY-XXXX`** (con 4 cifras de la placa), con
clave **`dotly1234`**. Al conectarse, el móvil abre la página solo (portal
cautivo). Si no la abre, está en **http://192.168.4.1**. No hace falta internet.

* **Inicio:** la pantalla de DOTLY en directo, con los cinco botones para
  manejarlo desde el móvil. También funcionan las flechas, Intro, Esc y R del
  ordenador. Muestra la ayuda de cada modo y el estado del teclado y de la red.
* **Braille:** un traductor de texto a braille (con mayúsculas y números) que
  lo puede mostrar en la pantalla de DOTLY. Tiene el signo generador por
  bloques y una máquina Perkins: marcas los puntos, o pulsas F D S J K L a la
  vez como en una Perkins, y la letra se escribe en DOTLY.
* **Señas:** deletreo de una palabra, en la página o en DOTLY, y las 27
  descripciones del alfabeto manual, que se pueden cambiar.
* **Modos:** el panel para abrir cualquier modo o bloque, configurar los retos
  y editar la lista de palabras.
* **Progreso:** cómo vas, letra a letra, con colores.
* **Ajustes:** tema claro u oscuro, tamaño del texto, el medidor del teclado
  (la tensión que llega ahora y el rango de cada botón), el botón para calibrar
  y el nombre y la clave de la red.

### Sobre las señas

Las descripciones de partida siguen el alfabeto manual más extendido. **Cada
país tiene el suyo** (LSE, LSM, LSA…), así que se pueden reescribir desde la
pestaña Señas y se guardan en DOTLY. Si dejas una vacía, vuelve la original.

## El teclado se calibra solo la primera vez

La lectura del teclado es la misma que usa BMJ TRIAJE. Cada lectura es la mediana
de 9 conversiones del ADC, filtrada y con antirrebote, y cada botón tiene el
rango de tensión que mide un asistente. La primera vez que se enciende, la
pantalla pide mantener pulsado cada botón (SW1 a SW5) y guarda lo que mide.

Para volver a calibrar: *Ajustes → Calibrar teclado*, el botón de la página,
encender DOTLY con un botón pulsado, o enviar `c` por el Monitor Serie (115200).

Si no hay teclado enchufado, DOTLY lo detecta, no abre el asistente y se maneja
desde la página.

## Dónde está cada cosa en el código

`dotly.ino` va por secciones numeradas (1. Configuración … 9. Loop). Dos bloques
están marcados con carteles de `INICIO` y `FIN`:

* **`INICIO - CALIBRACION DEL TECLADO ADC`**: la lectura del teclado de BMJ
  TRIAJE, adaptada a 5 botones y a la pantalla de 16x2. `leerBoton()` sigue
  existiendo y devuelve lo mismo que antes (0 nada, 1…5 = SW1…SW5).
* **`INICIO - SERVIDOR WEB EN MODO PUNTO DE ACCESO (AP)`**: la red, el portal
  cautivo y todas las rutas `/api/...`.

El `loop()` conserva la estructura del código base: los bloques `MENU_BLOQUES` y
`BLOQUE` están donde estaban y con las mismas teclas, con el menú principal
delante.

El código base tenía un fallo que dejaba el teclado muerto. `leerBoton()` ponía
`botonLiberado = false` en cada pulsación, pero solo `esperarLiberacion()` lo
devolvía a `true`, y a esa función solo se la llamaba si el botón tenía algo que
hacer en esa pantalla. Así, pulsar SW2 en el menú bloqueaba el teclado para
siempre. La lectura nueva da un evento por pulsación y no tiene ese problema.

### Una celda de verdad (opcional)

Si algún día montas una celda física con 6 LED, solenoides o vibradores (cada uno
con su transistor), pon `CELDA_FISICA` a 1 en la sección 1.8. Los pines de
`CELDA_PINES` (GPIO9 a GPIO14) reproducirán los puntos de lo que haya en
pantalla.

## API de la página

Todo en JSON. Los `POST` llevan los datos como formulario
(`application/x-www-form-urlencoded`).

| Ruta | Método | Qué hace |
|---|---|---|
| `/api/estado` | GET | modo, pantalla, teclado, red, reto |
| `/api/tabla` | GET | la signografía y los bloques |
| `/api/senas` | GET / POST `i`, `d` | descripciones de las señas (`d` vacía = la original) |
| `/api/palabras` | GET / POST `lista` | lista de palabras (vacía = la de DOTLY) |
| `/api/ajustes` | GET / POST `preguntas`, `velocidad`, `nivel`, `ssid`, `clave` | ajustes |
| `/api/progreso` | GET | aciertos por letra |
| `/api/progreso/borrar` | POST | borra el progreso |
| `/api/modo` | POST `m` (y `b` para un bloque) | abre un modo |
| `/api/tecla` | POST `b` = 1…5 | pulsa un botón |
| `/api/escribir` | POST `p` = 0…63, o `borrar` | escribe una celda en el modo Escribir |
| `/api/escrito/borrar` | POST | borra lo escrito |
| `/api/pizarra` | POST `t` | muestra un texto en braille en la pantalla |
| `/api/deletrear` | POST `t` | deletrea una palabra en señas |
| `/api/calibrar` | POST | abre el asistente del teclado |
| `/api/reiniciar` | POST | reinicia DOTLY |

## Probarlo sin placa

`pruebas/banco_dotly/` compila este mismo `dotly.ino` en el PC con el hardware
simulado. Lo maneja pulsando botones y leyendo la pantalla, y prueba la página
en un navegador de verdad. Ver `pruebas/banco_dotly/LEEME.md`.
