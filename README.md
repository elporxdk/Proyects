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

* **Actualizar:** vuelve a ejecutar el mismo comando (o `bash ~/Proyects/instalar.sh`).
* **Que se abra solo al encender la Pi:** `bash ~/Proyects/instalar.sh --autoarranque`
  (`--sin-autoarranque` lo quita).

