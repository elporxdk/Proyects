# Chatbot de WhatsApp (local)

Bot que conecta tu numero de WhatsApp y responde automaticamente a quien te
escriba, en el personaje de Medibot (el robot del proyecto): habla en primera
persona, con humor, pero con los datos reales del proyecto para no inventar
nada. Usa Claude para generar las respuestas. Corre solo en tu computadora:
no se despliega en Cloudflare ni forma parte del sitio web.

Usa `whatsapp-web.js` (la misma libreria que `verificar.js` en la rama
`main`), que controla WhatsApp Web mediante un Chromium local.

## Requisitos

- Node.js 18 o superior.
- Chromium o Google Chrome instalado (o dejar que Puppeteer descargue uno la
  primera vez que instales las dependencias).
- Una clave de la API de Anthropic: https://console.anthropic.com/settings/keys

## Uso

```bash
cd whatsapp-bot
npm install
cp .env.example .env    # y pegar tu ANTHROPIC_API_KEY
npm start
```

La primera vez aparece un codigo QR en la terminal. Escanealo desde tu
telefono: WhatsApp > Ajustes > Dispositivos vinculados > Vincular un
dispositivo. La sesion queda guardada en `.wwebjs_auth/` (ignorada por git),
asi que las siguientes veces arranca sin pedir QR de nuevo.

Con el bot corriendo, cualquiera que te escriba un mensaje individual recibe
respuesta generada por Claude. Los mensajes de grupos y los tuyos propios se
ignoran a proposito, para que no responda en conversaciones donde no
corresponde.

## Configuracion

Todo se ajusta por variables de entorno en `.env` (ver `.env.example`):

| Variable | Para que sirve |
|---|---|
| `ANTHROPIC_API_KEY` | Obligatoria. Sin ella el bot no arranca. |
| `CLAUDE_MODEL` | Modelo de Claude a usar. Por defecto uno rapido y barato. |
| `CHROME_PATH` | Ruta a tu Chromium/Chrome, si no quieres que Puppeteer descargue uno propio. |

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
