## Instalar MEDIBOT en la Raspberry Pi

En la Pi (Raspberry Pi OS con escritorio y conexión a internet), abre una
terminal y pega:

```bash
curl -fsSL https://raw.githubusercontent.com/elporxdk/Proyects/main/instalar.sh | bash
```

Descarga el código en `~/Proyects`, instala todo lo necesario con `apt`
(Flask, pyserial, OpenCV con reconocimiento facial, Tkinter, Pillow, audio,
openssl), da permisos para el Arduino, las cámaras y el audio, y crea el icono
**MEDIBOT** en el escritorio y en el menú, y la orden `medibot` para la
terminal. Al final comprueba que todo está en su sitio. Si pide reiniciar,
reinicia (`sudo reboot`).

* **Que se abra solo al encender la Pi:** `bash ~/Proyects/instalar.sh --autoarranque`
  (`--sin-autoarranque` lo quita).

## Actualizar MEDIBOT

Con MEDIBOT ya instalado, para bajar e instalar la última versión basta con
**un comando** (o el icono **Actualizar MEDIBOT** del menú):

```bash
medibot-actualizar
```

Para que se **actualice solo** (se activa una vez):

```bash
medibot-actualizar --activar-auto
```

La Pi mira si hay versión nueva al encenderse y luego cada hora. Si MEDIBOT
está cerrado, la instala en ese momento; si está abierto, no lo toca a mitad de
uso: la instala la próxima vez que se abra MEDIBOT (sin necesitar internet).

* `medibot-actualizar --estado`: versión instalada, si hay una nueva y cómo va
  la actualización automática.
* `medibot-actualizar --desactivar-auto`: la quita.

No se pierde nada de la Pi: sus datos (pastillero, caras, vídeos) no se tocan;
los ficheros cambiados a mano se guardan en `~/.medibot/respaldos/` y se
vuelven a poner encima; y una versión con errores de sintaxis no se instala.
Lo que va haciendo queda en `~/.medibot/actualizar.log`.

Si instalaste MEDIBOT antes de que existiera el actualizador, pega una vez el
comando de instalación de arriba y ya tendrás `medibot-actualizar`.
