# Chatbot de WhatsApp (local)

Bot que conecta tu numero de WhatsApp y responde automaticamente a quien te
escriba, en el personaje de Medibot (el robot del proyecto): habla en primera
persona, con humor, pero con los datos reales del proyecto para no inventar
nada. Usa Claude para generar las respuestas. Corre solo en tu computadora:
no se despliega en Cloudflare ni forma parte del sitio web.

Usa `whatsapp-web.js` (la misma libreria que `verificar.js` en la rama
`main`), que controla WhatsApp Web mediante un Chromium local.

> **Para clonar solo esto:** el repositorio completo pesa varias decenas de
> MB por el modelo 3D y las fotos del sitio web, que no hacen falta para
> correr el bot. La rama `whatsapp-bot-standalone` tiene *unicamente* estos
> archivos, en la raiz, sin esa historia pesada — clonarla pesa menos de
> 1 MB. Las instrucciones de abajo ya usan esa rama.

## Requisitos

- Node.js 18 o superior.
- Git (para clonar el repositorio).
- Chromium o Google Chrome instalado (en Raspberry Pi hace falta instalarlo
  tu mismo; en Windows normalmente no, ver mas abajo).
- Una clave de la API de Anthropic: https://console.anthropic.com/settings/keys

## Instalacion en Raspberry Pi / Linux

### 1. Clonar el repositorio

```bash
cd ~/Desktop
git clone --single-branch --branch whatsapp-bot-standalone --depth 1 https://github.com/elporxdk/Proyects.git numeros
cd numeros
```

`--single-branch --depth 1` es lo que hace que la descarga sea de menos de
1 MB en vez de decenas: solo trae la rama `whatsapp-bot-standalone` y su
ultimo commit, sin el resto del repositorio. (Cambia `numeros` por el nombre
de carpeta que prefieras — como es una carpeta nueva, no hay problema de que
ya exista.)

### 2. Instalar Chromium del sistema

Puppeteer intenta descargar su propio Chromium al instalar las dependencias,
pero en Raspberry Pi (ARM) esa descarga suele quedar incompleta o no tener un
binario compatible, y `npm start` falla mas adelante con un error del tipo
`The browser folder ... exists but the executable ... is missing`. La
solucion es usar el Chromium del sistema en vez del que Puppeteer intenta
bajar:

```bash
sudo apt update
sudo apt install -y chromium-browser
```

Si el paquete no existe (pasa en versiones nuevas de Raspberry Pi OS, tipo
Bookworm), usa este nombre en su lugar:

```bash
sudo apt install -y chromium
```

Anota la ruta donde quedo instalado:

```bash
which chromium-browser || which chromium
```

### 3. Instalar las dependencias del bot sin que Puppeteer descargue su Chromium

```bash
PUPPETEER_SKIP_DOWNLOAD=true npm install
```

Si ya habias corrido `npm install` antes y fallo, limpia primero los restos
de la descarga rota:

```bash
rm -rf ~/.cache/puppeteer node_modules
PUPPETEER_SKIP_DOWNLOAD=true npm install
```

### 4. Configurar el `.env`

```bash
cp .env.example .env
nano .env
```

Rellena:

- `ANTHROPIC_API_KEY`: tu clave de https://console.anthropic.com/settings/keys
- `CHROME_PATH`: la ruta que anotaste en el paso 2, por ejemplo
  `/usr/bin/chromium-browser`

Guarda con `Ctrl+O`, Enter, y sal con `Ctrl+X`.

### 5. Arrancar

```bash
npm start
```

## Instalacion en Windows

### 1. Instalar Node.js y Git

Descarga e instala Node.js LTS desde https://nodejs.org y Git desde
https://git-scm.com/download/win (siguiente, siguiente, siguiente en ambos).
Verifica en PowerShell:

```powershell
node -v
git --version
```

### 2. Clonar el repositorio

```powershell
cd C:\Users\TU_USUARIO\Desktop
git clone --single-branch --branch whatsapp-bot-standalone --depth 1 https://github.com/elporxdk/Proyects.git numeros
cd numeros
```

### 3. Instalar las dependencias

```powershell
npm install
```

En Windows Puppeteer si suele bajar su propio Chromium sin problema (a
diferencia de Raspberry Pi), asi que normalmente no hace falta instalar
Chrome aparte ni tocar `CHROME_PATH`. Puede tardar un rato porque descarga
varios cientos de MB.

### 4. Configurar el `.env`

```powershell
copy .env.example .env
notepad .env
```

Pega tu `ANTHROPIC_API_KEY` en la linea correspondiente (queda
`ANTHROPIC_API_KEY=sk-ant-...`). Deja `CHROME_PATH` vacio. Guarda y cierra.

### 5. Arrancar

```powershell
npm start
```

## Uso

La primera vez aparece un codigo QR en la terminal. Escanealo desde tu
telefono: WhatsApp > Ajustes > Dispositivos vinculados > Vincular un
dispositivo. La sesion queda guardada en `.wwebjs_auth/` (ignorada por git),
asi que las siguientes veces arranca sin pedir QR de nuevo.

Con el bot corriendo, cualquiera que te escriba un mensaje individual recibe
respuesta generada por Claude. Los mensajes de grupos y los tuyos propios se
ignoran a proposito, para que no responda en conversaciones donde no
corresponde.

## Reinstalar todo desde cero (Raspberry Pi)

Si ya intentaste varias veces y no estas seguro de que version de los
archivos tienes en tu carpeta, lo mas rapido es borrar todo y clonar de
nuevo. Copia y pega este bloque completo en la terminal de la Pi (cambia
`Whats` por el nombre de tu carpeta si es otro):

```bash
cp ~/Desktop/Whats/.env ~/Desktop/env-backup 2>/dev/null
rm -rf ~/Desktop/Whats
cd ~/Desktop
git clone --single-branch --branch whatsapp-bot-standalone --depth 1 https://github.com/elporxdk/Proyects.git Whats
cd Whats
cp ~/Desktop/env-backup .env 2>/dev/null || cp .env.example .env
sudo apt update
sudo apt install -y chromium-browser || sudo apt install -y chromium
CHROME_BIN=$(which chromium-browser || which chromium)
echo "Chromium encontrado en: $CHROME_BIN"
PUPPETEER_SKIP_DOWNLOAD=true npm install
nano .env
```

La primera linea guarda tu `.env` si ya tenias uno con la API key puesta
(si no existe, no pasa nada). El `nano .env` al final es el unico paso
manual: revisa que `ANTHROPIC_API_KEY` tenga tu clave y que `CHROME_PATH`
tenga la ruta que imprimio `echo "Chromium encontrado en: ..."` un poco mas
arriba. Guarda con `Ctrl+O`, Enter, sal con `Ctrl+X`, y despues:

```bash
npm start
```

## Solucion de problemas

**`Error: Cannot find module 'dotenv'`** (o de cualquier otro paquete) al
correr `npm start`: falta correr `npm install` primero en esa carpeta. Este
error tambien sale si copiaste solo `index.js` a mano en vez de traerte toda
la carpeta `whatsapp-bot/` completa (con `package.json` incluido).

**`npm error Missing script: "start"`**: la carpeta donde estas parado no
tiene el `package.json` real del bot, solo paquetes instalados sueltos.
Revisa que exista `index.js` en esa misma carpeta (`ls` en Linux, `dir` en
Windows); si no esta, te falta clonar el proyecto ahi (ver instalacion mas
arriba) en vez de instalar las librerias a mano una por una.

**`The browser folder ... exists but the executable ... is missing`** al
correr `npm start` (tipico en Raspberry Pi): Puppeteer no pudo descargar un
Chromium que funcione para tu arquitectura. Sigue los pasos 2 y 3 de
"Instalacion en Raspberry Pi / Linux" de mas arriba: instalar Chromium del
sistema, reinstalar con `PUPPETEER_SKIP_DOWNLOAD=true`, y apuntar
`CHROME_PATH` en el `.env` a esa ruta.

**El bot responde "Tuve un problema respondiendo" y en la consola sale
`Error respondiendo a ...@lid` con un stack de `getChatById`**: las
direcciones nuevas de WhatsApp (las que terminan en `@lid` en vez de
`@c.us`) rompen el `getChat()` de `whatsapp-web.js`. Solo lo usabamos para
el indicador de "escribiendo...", asi que ahora esa parte es opcional y su
fallo no impide responder. Si tienes una copia vieja del `index.js`,
actualizala.

**`git clone` dice que la carpeta ya existe y no esta vacia**: no se puede
clonar directo dentro de una carpeta con archivos dentro (por ejemplo un
intento anterior fallido). Usa un nombre de carpeta nuevo al clonar, o borra
el contenido de la carpeta destino primero si estas seguro de que no tiene
nada que quieras conservar (ojo con tu `.env`, haz una copia antes si ya
habias puesto tu API key).

## Configuracion

Todo se ajusta por variables de entorno en `.env` (ver `.env.example`):

| Variable | Para que sirve |
|---|---|
| `ANTHROPIC_API_KEY` | Obligatoria. Sin ella el bot no arranca. |
| `CLAUDE_MODEL` | Modelo de Claude a usar. Por defecto uno rapido y barato. |
| `CHROME_PATH` | Ruta a tu Chromium/Chrome. Necesaria en Raspberry Pi; en Windows se puede dejar vacia. |

El texto que define como se comporta el bot esta en `SYSTEM_PROMPT`, dentro
de `index.js`. Editalo ahi para cambiar el tono o la informacion que da.

## Limitaciones a proposito

- El historial de conversacion vive en memoria (`Map` en `index.js`): se
  pierde si reinicias el bot. No hay base de datos porque no hacia falta para
  un bot personal en local.
- No hay limite de uso ni filtro de numeros: cualquiera que te escriba
  consume tu cuota de la API de Anthropic. Si eso es un problema, lo primero
  que conviene anadir es una lista blanca de numeros permitidos (como hace
  `verificar.js` con su archivo de entrada).
- Usar WhatsApp Web de forma automatizada no es un uso oficialmente
  soportado por WhatsApp; es el mismo enfoque que ya usa `verificar.js` en
  este repositorio, pensado para pruebas y uso personal, no para volumen alto.
