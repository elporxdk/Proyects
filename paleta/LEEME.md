# Paleta de colores de las webs

Las interfaces web de la **cámara** (`medibot/Vision_MEDIBOT.py`, puerto 5000)
y del **pastillero** (`medibot/Pastillero.py`, puerto 5001) usan los colores
de la web de MEDIBOT: los mismos tokens de `src/index.css` de la rama
`web`, con el mismo valor, en tema oscuro (con el que arrancan) y claro.

* **`paleta_colores.html`**: la paleta completa, con cada pieza de las dos
  interfaces y el color que lleva. Se abre con doble clic en cualquier
  navegador; pulsando un color se copia su código.
* **`paleta_web.png`**: la paleta en imagen, para una presentación o un
  documento.

![Paleta de la web de MEDIBOT, en tema oscuro y claro](paleta_web.png)

## La paleta de la web

| Color | De la web | Oscuro (de arranque) | Claro |
|---|---|---|---|
| Fondo | `--c-surface` | `#06161F` | `#F4FAFB` |
| Tarjeta | `--c-card` | `#0E2733` | `#FFFFFF` |
| Texto | `--c-ink` | `#DBEAF3` | `#0A3D5C` |
| Marca | `--c-brand` | `#22C9F5` | `#01BAEF` |
| Profundo | `--c-deep` | `#0B4F6C` | `#0B4F6C` |
| Marca suave | `--c-brandsoft` | `#7FE9ED` | `#5EE1E6` |
| Menta | `--c-mint` | `#34D399` | `#34D399` |
| Sombra | `--c-shade` | `#05121A` | `#0A3D5C` |
| Pie | `--c-footer` | `#04121A` | `#0A3D5C` |

Para avisos y estados, la web usa el rojo, el ámbar y el verde de Tailwind
4.3.3. Los escribe en `oklch`; las interfaces llevan ese mismo valor y, para
los navegadores que no entienden `oklch`, su equivalente en hex, que es el
que sale aquí.

| Color | Tailwind (oscuro · claro) | Oscuro | Claro |
|---|---|---|---|
| Rojo | red-500 | `#FB2C36` | `#FB2C36` |
| Rojo texto | red-400 · red-600 | `#FF6467` | `#E7000B` |
| Rojo pastilla | red-300 · red-700 | `#FFA2A2` | `#C10007` |
| Ámbar | amber-500 | `#FE9A00` | `#FE9A00` |
| Ámbar texto | amber-300 · amber-700 | `#FFD230` | `#BB4D00` |
| Verde texto | emerald-300 · emerald-700 | `#5EE9B5` | `#007A55` |

Un número tras el nombre es la transparencia, como en Tailwind: `ink/60` es
el color ink al 60 %.

<details>
<summary>Cámara · Medibot: dónde va cada color (40)</summary>

| Pieza | Dónde | Token | Oscuro | Claro |
|---|---|---|---|---|
| **Superficies** | | | | |
| Fondo de la página | Detrás de todo | `--c-surface` = surface | `#06161F` | `#F4FAFB` |
| Contenedor | El recuadro que lo agrupa todo | `--c-card` = card | `#0E2733` | `#FFFFFF` |
| Recuadros | Las cámaras, «Estado Sistema» y las tarjetas de vídeo | `--c-surface` = surface | `#06161F` | `#F4FAFB` |
| Casillas de dato | «Estado» y «Grabando», debajo de cada cámara; también Reproducir y Descargar | `--c-ink-5` = ink/5 | `rgba(219, 234, 243, 0.05)` | `rgba(10, 61, 92, 0.05)` |
| Bordes | Contenedor, recuadros y marco del vídeo; Reproducir y Descargar al pasar el ratón | `--c-ink-10` = ink/10 | `rgba(219, 234, 243, 0.1)` | `rgba(10, 61, 92, 0.1)` |
| Borde de los botones | Botones de control y de movimiento | `--c-ink-15` = ink/15 | `rgba(219, 234, 243, 0.15)` | `rgba(10, 61, 92, 0.15)` |
| Fondo del vídeo | Detrás de la imagen y en pantalla completa | `--c-shade` = shade | `#05121A` | `#0A3D5C` |
| Miniatura de vídeo | Como el vídeo de la web: de deep a shade | deep → shade | degradado deep → shade | degradado deep → shade |
| **Texto** | | | | |
| Texto principal | Valores, títulos de cámara y de vídeo, y el «BOT» del logotipo | `--c-ink` = ink | `#DBEAF3` | `#0A3D5C` |
| Letra de botones y etiquetas | Botones de control, de movimiento, Pastillero y Modo; «Estado», «Velocidad» | `--c-ink-70` = ink/70 | `rgba(219, 234, 243, 0.7)` | `rgba(10, 61, 92, 0.7)` |
| Texto secundario | Datos de cada vídeo, «VISIÓN ARTIFICIAL» y el pie | `--c-ink-60` = ink/60 | `rgba(219, 234, 243, 0.6)` | `rgba(10, 61, 92, 0.6)` |
| Rótulo «ESTADO SISTEMA» | Como los rótulos en mayúsculas de la web | `--c-brand` = brand | `#22C9F5` | `#01BAEF` |
| **Marca** | | | | |
| Logo y «MEDI» | El círculo del logo y la primera mitad del nombre | `--c-brandsoft` = brandsoft | `#7FE9ED` | `#5EE1E6` |
| Brillo del logo | Halo alrededor del logo | `--c-brandsoft-45` = brandsoft/45 | `rgba(127, 233, 237, 0.45)` | `rgba(94, 225, 230, 0.45)` |
| Brillo del contenedor | Halo alrededor del recuadro principal | `--c-brand-5` = brand/5 | `rgba(34, 201, 245, 0.05)` | `rgba(1, 186, 239, 0.05)` |
| Icono de la pestaña | Sigue al tema del sistema, como el de la web; el punto del centro es ink #0A3D5C | brandsoft · brand | `#7FE9ED` | `#01BAEF` |
| **Botones y mandos** | | | | |
| Botón activo | «Detener Sistema», «Reconocimiento: ON», un movimiento pulsado, el altavoz escuchando | brand → deep | degradado brand → deep | degradado brand → deep |
| Letra sobre el degradado | Siempre blanca, como en la web | `--c-blanco` = white | `#FFFFFF` | `#FFFFFF` |
| Borde al pasar el ratón | Botones de control y de movimiento | `--c-brand-30` = brand/30 | `rgba(34, 201, 245, 0.3)` | `rgba(1, 186, 239, 0.3)` |
| Pastillero y Modo al pasar el ratón | Fondo | `--c-ink-5` = ink/5 | `rgba(219, 234, 243, 0.05)` | `rgba(10, 61, 92, 0.05)` |
| Botonera de movimiento | Fondo de los botones | `--c-card` = card | `#0E2733` | `#FFFFFF` |
| Barra de velocidad y aro del joystick | La bola del joystick lleva el degradado | `--c-brand` = brand | `#22C9F5` | `#01BAEF` |
| Brillo del joystick | Halo de la bola | `--c-brand-40` = brand/40 | `rgba(34, 201, 245, 0.4)` | `rgba(1, 186, 239, 0.4)` |
| **Estados y avisos** | | | | |
| Cámara en marcha | Título de la cámara activa | `--c-exito-texto` = emerald-300 · emerald-700 | `#5EE9B5` | `#007A55` |
| Punto: cámara activa | Junto al título | `--c-mint` = mint | `#34D399` | `#34D399` |
| Punto: cámara parada | El mismo punto; también PARAR pulsado | `--c-rojo` = red-500 | `#FB2C36` | `#FB2C36` |
| Letra roja | «Sí» en Grabando, PARAR y el aviso de la velocidad | `--c-rojo-texto` = red-400 · red-600 | `#FF6467` | `#E7000B` |
| Botón grabando | Fondo de «Detener Grabación» | `--c-rojo-15` = red-500/15 | `rgba(251, 44, 54, 0.15)` | `rgba(251, 44, 54, 0.15)` |
| Letra del botón grabando | Como las pastillas de alerta de la web | `--c-rojo-pastilla` = red-300 · red-700 | `#FFA2A2` | `#C10007` |
| Borde rojo | PARAR y el botón grabando | `--c-rojo-30` = red-500/30 | `rgba(251, 44, 54, 0.3)` | `rgba(251, 44, 54, 0.3)` |
| PARAR al pasar el ratón | Fondo | `--c-rojo-10` = red-500/10 | `rgba(251, 44, 54, 0.1)` | `rgba(251, 44, 54, 0.1)` |
| Barra de avisos | Franja de arriba cuando algo falla | `--c-rojo-fuerte` = red-600 | `#E7000B` | `#E7000B` |
| Barra de avisos grave | Cuando falla la propia página | `--c-rojo-grave` = red-700 | `#C10007` | `#C10007` |
| **Sobre el vídeo** | | | | |
| Botones sobre el vídeo | Altavoz, micrófono, Pantalla completa y «Dir:» del joystick; sombra de la barra de avisos | `--c-velo` = footer oscuro al 60 % | `rgba(4, 18, 26, 0.6)` | `rgba(4, 18, 26, 0.6)` |
| Botones sobre el vídeo al pasar el ratón | Los mismos | `--c-velo-fuerte` = footer oscuro al 80 % | `rgba(4, 18, 26, 0.8)` | `rgba(4, 18, 26, 0.8)` |
| Borde de esos botones | Al pasar el ratón o escuchando, brandsoft | `--c-blanco-35` = white/35 | `rgba(255, 255, 255, 0.35)` | `rgba(255, 255, 255, 0.35)` |
| Texto de la miniatura | Nombre de la cámara en cada vídeo | `--c-blanco-70` = white/70 | `rgba(255, 255, 255, 0.7)` | `rgba(255, 255, 255, 0.7)` |
| Hablando | Micrófono mientras mantienes pulsado | `--c-rojo-90` = red-500/90 | `rgba(251, 44, 54, 0.9)` | `rgba(251, 44, 54, 0.9)` |
| Borde al hablar | El mismo botón | `--c-rojo-claro` = red-300 | `#FFA2A2` | `#FFA2A2` |
| Latido al hablar | Anillo que late alrededor del botón | `--c-rojo-55` = red-500/55 | `rgba(251, 44, 54, 0.55)` | `rgba(251, 44, 54, 0.55)` |

</details>

<details>
<summary>Pastillero · Pillbox: sus variables y de qué color de la web salen (33)</summary>

| Variable | Para qué | De la web | Oscuro | Claro |
|---|---|---|---|---|
| **Superficies** | | | | |
| `--fondo` | Detrás de todo | surface | `#06161F` | `#F4FAFB` |
| `--tarjeta` | Recuadro principal, tarjetas de compartimiento, campos y casillas de días | card | `#0E2733` | `#FFFFFF` |
| `--panel` | Formulario, datos guardados, historial y horarios | surface | `#06161F` | `#F4FAFB` |
| `--suave` | Botones de arriba, «Volver al menu», horario en pausa y pastilla «sin verificar» | ink/5 | `rgba(219, 234, 243, 0.05)` | `rgba(10, 61, 92, 0.05)` |
| `--suave-hover` | Los botones de arriba al pasar el ratón | ink/10 | `rgba(219, 234, 243, 0.1)` | `rgba(10, 61, 92, 0.1)` |
| **Bordes** | | | | |
| `--borde` | Tarjetas, paneles y líneas entre filas | ink/10 | `rgba(219, 234, 243, 0.1)` | `rgba(10, 61, 92, 0.1)` |
| `--borde-fuerte` | Campos, hora, casillas de días y botones de contorno | ink/15 | `rgba(219, 234, 243, 0.15)` | `rgba(10, 61, 92, 0.15)` |
| `--borde-activo` | Tarjeta de compartimiento y casillas al pasar el ratón | brand/30 | `rgba(34, 201, 245, 0.3)` | `rgba(1, 186, 239, 0.3)` |
| `--borde-hover` | Editar, Cancelar y Pausar al pasar el ratón | brand/40 | `rgba(34, 201, 245, 0.4)` | `rgba(1, 186, 239, 0.4)` |
| **Texto** | | | | |
| `--texto-titulo` | Títulos, número de compartimiento, datos guardados y letra de los botones de arriba | ink | `#DBEAF3` | `#0A3D5C` |
| `--texto` | Texto normal, etiquetas del formulario y botones de contorno | ink/70 | `rgba(219, 234, 243, 0.7)` | `rgba(10, 61, 92, 0.7)` |
| `--texto-tenue` | Subtítulos, etiquetas en mayúsculas, fechas y avisos | ink/60 | `rgba(219, 234, 243, 0.6)` | `rgba(10, 61, 92, 0.6)` |
| `--texto-pastilla` | Pastillas neutras: «Pausado» y «sin verificar» | ink/50 | `rgba(219, 234, 243, 0.5)` | `rgba(10, 61, 92, 0.5)` |
| `--texto-debil` | «Sin datos» y el texto de ejemplo de los campos | ink/40 | `rgba(219, 234, 243, 0.4)` | `rgba(10, 61, 92, 0.4)` |
| **Marca** | | | | |
| `--acento` | Contador, «Pendiente», días del horario, campo activo, acciones MANUAL | brand | `#22C9F5` | `#01BAEF` |
| `--acento-fin` | Final del degradado | deep | `#0B4F6C` | `#0B4F6C` |
| Botón principal | Dispensar, Completado y Agregar horario | brand → deep | degradado brand → deep | degradado brand → deep |
| `--sobre-acento` | Letra de esos botones | white | `#FFFFFF` | `#FFFFFF` |
| `--acento-suave` | Fondo del contador y de «Pendiente» | brand/10 | `rgba(34, 201, 245, 0.1)` | `rgba(1, 186, 239, 0.1)` |
| **Todo bien** | | | | |
| `--exito` | Acciones AUTO del historial | mint | `#34D399` | `#34D399` |
| `--exito-suave` | Fondo de «Conectado», «Sincronizado», «Completado» y horario activo | mint/15 | `rgba(52, 211, 153, 0.15)` | `rgba(52, 211, 153, 0.15)` |
| `--exito-texto` | Letra de esas pastillas | emerald-300 · emerald-700 | `#5EE9B5` | `#007A55` |
| **Peligro** | | | | |
| `--peligro-suave` | Fondo de «Desconectado» | red-500/15 | `rgba(251, 44, 54, 0.15)` | `rgba(251, 44, 54, 0.15)` |
| `--peligro-pastilla` | Letra de «Desconectado» | red-300 · red-700 | `#FFA2A2` | `#C10007` |
| `--peligro-borde` | Borde de Eliminar y Quitar | red-500/30 | `rgba(251, 44, 54, 0.3)` | `rgba(251, 44, 54, 0.3)` |
| `--peligro-texto` | Letra de Eliminar y Quitar | red-400 · red-600 | `#FF6467` | `#E7000B` |
| `--peligro-hover` | Eliminar y Quitar al pasar el ratón | red-500/10 | `rgba(251, 44, 54, 0.1)` | `rgba(251, 44, 54, 0.1)` |
| **Aviso** | | | | |
| `--aviso-suave` | Fondo de «Desincronizado» | amber-500/15 | `rgba(254, 154, 0, 0.15)` | `rgba(254, 154, 0, 0.15)` |
| `--aviso-texto` | Letra de «Desincronizado» | amber-300 · amber-700 | `#FFD230` | `#BB4D00` |
| **Sombras** | | | | |
| `--sombra` | Recuadro principal y tarjetas | shade/5 | `rgba(5, 18, 26, 0.05)` | `rgba(10, 61, 92, 0.05)` |
| `--sombra-acento` | Tarjeta de compartimiento al pasar el ratón | brand/5 | `rgba(34, 201, 245, 0.05)` | `rgba(1, 186, 239, 0.05)` |
| **Icono de la pestaña** | | | | |
| Pastilla | Sigue al tema del sistema, como el de la web | brandsoft · brand | `#7FE9ED` | `#01BAEF` |
| Raya | La línea del centro | surface · white | `#06161F` | `#FFFFFF` |

</details>

## Si la web cambia su paleta

Hay que cambiar a mano los tokens de `Vision_MEDIBOT.py`, las variables de
`Pastillero.py` y esta carpeta. La ventana de escritorio de Medibot (Tkinter)
no está incluida: esta es la paleta de las webs.
