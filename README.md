this is a repository made for different proyects
made by my team, incluiding proyects are: Pillbox and Medibot



made by 2027 ECA/CDB.

---

## Como funciona el serial (COM)

Dos programas no pueden abrir el mismo puerto COM a la vez, asi que el acceso
al Arduino esta centralizado en un "hub":

```
Vision (Medibot) ---+
                    +--- TCP local ---> serial_hub.py --- USB/COM ---> Arduino
Pillbox ------------+
```

- **`serial_hub.py`**: el UNICO programa que abre el puerto serie. Recibe las
  ordenes de Vision y Pillbox por TCP (127.0.0.1:5055), las escribe al Arduino
  en orden y devuelve las respuestas. Si el Arduino no esta (o se desconecta),
  reintenta conectarse cada 5 segundos automaticamente.
- **`medibot_serial.py`**: el "cartero" que usan Vision y Pillbox para hablar
  con el hub. Lo autolanza si no esta corriendo. No hay que arrancarlo a mano.

### Uso normal (Raspberry Pi)

```bash
pip install flask pyserial
python3 Pastillero.py        # Pillbox en http://<ip>:5001 (el hub arranca solo)
python3 Vision_MEDIBOT.py    # Medibot (usa el mismo hub)
```

### Si la autodeteccion no encuentra el Arduino

Fija el puerto a mano con una variable de entorno antes de arrancar:

```bash
MEDIBOT_SERIAL_PORT=/dev/ttyUSB0 python3 Pastillero.py    # Pi
set MEDIBOT_SERIAL_PORT=COM3 && python Pastillero.py      # Windows
```

En la web de Pillbox, la pastilla de estado dice la verdad: "Arduino:
/dev/ttyUSB0" solo cuando hay un Arduino fisico conectado; si no, muestra
"sin conexion" y un boton **Reconectar**.

### Datos persistentes

`pillbox_data.json` (junto a los scripts) guarda dosis, horarios y el
historial de acciones. El Arduino recuerda en su EEPROM el compartimiento
que quedo arriba.
