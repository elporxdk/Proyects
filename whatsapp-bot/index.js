require("dotenv").config();

const { Client, LocalAuth } = require("whatsapp-web.js");
const qrcode = require("qrcode-terminal");
const Anthropic = require("@anthropic-ai/sdk");

const ANTHROPIC_API_KEY = process.env.ANTHROPIC_API_KEY;
if (!ANTHROPIC_API_KEY) {
  console.error(
    "Falta ANTHROPIC_API_KEY. Copia .env.example a .env y pon tu clave de https://console.anthropic.com/settings/keys"
  );
  process.exit(1);
}

const CLAUDE_MODEL = process.env.CLAUDE_MODEL || "claude-haiku-4-5-20251001";

// Cuantos mensajes (usuario + bot) se guardan por chat antes de recortar.
// Cada mensaje cuesta tokens en cada turno, asi que no crece sin limite.
const MAX_HISTORIAL = 20;

const SYSTEM_PROMPT = `Hablas por WhatsApp EN PRIMERA PERSONA como si fueras Medibot, el robot en
persona. No eres "un asistente que representa a Medibot": eres Medibot, sobre
ruedas, con un compartimento termico y muchas ganas de hacer bromas sobre tu
propia condicion de robot.

Tono: gracioso, cercano y un poco fanfarron con tu propio trabajo, pero sin
payasear tanto que dejes de ser util. Un chiste corto por respuesta como
mucho, nunca una respuesta hecha solo de chistes. Si la pregunta es tecnica o
seria (por ejemplo salud, seguridad de pacientes, o alguien con un problema
real), bajas el tono de broma y respondes claro y directo primero.

Datos reales sobre ti, para no inventar nada:
- Eres un robot movil teleoperado para transporte hospitalario, con control
  termico activo. Prototipo de 4 estudiantes de Electronica del Colegio Don
  Bosco, hecho para la feria de innovacion CREA-J 2026.
- Tu mision: trasladar medicamentos e insumos entre farmacia y areas de
  paciente SIN que una persona te acompane. Asi evitas dos problemas: el
  transito de personal que ayuda a propagar infecciones asociadas a la
  atencion medica (IAAS), y la perdida de la cadena de frio en farmacos
  termolabiles.
- Un operador humano te conduce a distancia desde el navegador: ve por tu
  camara y escucha por tu microfono mientras tu compartimento mantiene la
  temperatura solo, en un rango de 13 a 25°C.
- Por dentro tienes tres subsistemas integrados trabajando juntos, coordinados
  por una Raspberry Pi 4 que habla con un Arduino por un unico puerto serie
  (un hub interno se encarga de que nadie choque intentando usarlo a la vez).
- No tienes brazos ni haces diagnosticos: transportas. Si alguien te pide algo
  fuera de eso (una urgencia medica real, por ejemplo), dejas la broma y le
  dices que llame a personal humano o a emergencias, sin dar consejo medico.
- Si te preguntan algo del proyecto que no sabes con certeza, lo dices con
  honestidad en vez de inventarlo; no hace falta que sea con chiste.

Formato: mensajes cortos, como en un chat real de WhatsApp. Sin markdown, sin
tablas, sin listas con guiones largas. Respondes en el mismo idioma en el que
te escriben.`;

const anthropic = new Anthropic({ apiKey: ANTHROPIC_API_KEY });

// Historial de conversacion por chat, en memoria: se pierde al reiniciar el bot.
const historiales = new Map();

function historialDe(chatId) {
  if (!historiales.has(chatId)) {
    historiales.set(chatId, []);
  }
  return historiales.get(chatId);
}

async function responder(chatId, textoUsuario) {
  const historial = historialDe(chatId);
  historial.push({ role: "user", content: textoUsuario });

  const respuesta = await anthropic.messages.create({
    model: CLAUDE_MODEL,
    max_tokens: 500,
    system: SYSTEM_PROMPT,
    messages: historial,
  });

  const texto = respuesta.content
    .filter((bloque) => bloque.type === "text")
    .map((bloque) => bloque.text)
    .join("\n")
    .trim();

  historial.push({ role: "assistant", content: texto });
  // Recorta por los dos extremos para no romper la alternancia user/assistant.
  while (historial.length > MAX_HISTORIAL) {
    historial.shift();
    historial.shift();
  }

  return texto;
}

const client = new Client({
  authStrategy: new LocalAuth(),
  puppeteer: {
    headless: true,
    executablePath: process.env.CHROME_PATH || undefined,
    args: ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
  },
});

client.on("qr", (qr) => {
  console.log("Escanea este QR desde WhatsApp > Dispositivos vinculados:\n");
  qrcode.generate(qr, { small: true });
});

client.on("ready", () => {
  console.log("Bot conectado y escuchando mensajes de WhatsApp.");
});

client.on("auth_failure", (mensaje) => {
  console.error("Fallo de autenticacion:", mensaje);
});

client.on("disconnected", (razon) => {
  console.warn("WhatsApp se desconecto:", razon);
});

client.on("message", async (msg) => {
  // Ignora grupos, difusiones/estados y mensajes propios: el bot solo
  // conversa uno a uno con quien le escribe.
  if (msg.from.endsWith("@g.us") || msg.from === "status@broadcast" || msg.fromMe) {
    return;
  }
  if (!msg.body || !msg.body.trim()) {
    return;
  }

  // El indicador de "escribiendo..." es puramente cosmetico, y obtenerlo
  // exige un getChat() que falla con las direcciones nuevas de WhatsApp
  // (las que terminan en @lid). Si no se puede, se sigue adelante sin el:
  // mejor responder sin aviso que no responder.
  try {
    const chat = await msg.getChat();
    await chat.sendStateTyping();
  } catch {
    // Sin indicador de escritura, no pasa nada.
  }

  try {
    const texto = await responder(msg.from, msg.body.trim());
    await msg.reply(texto);
  } catch (error) {
    console.error(`Error respondiendo a ${msg.from}:`, error);
    await msg
      .reply("Tuve un problema respondiendo justo ahora. Intenta de nuevo en un momento.")
      .catch(() => {});
  }
});

// whatsapp-web.js a veces lanza errores async fuera de nuestros manejadores
// (por ejemplo, evaluaciones internas de Puppeteer justo tras conectar). Sin
// esto, uno de esos errores tumba el proceso entero en vez de solo avisar.
process.on("unhandledRejection", (error) => {
  console.error("Promesa rechazada sin atrapar:", error);
});

client.initialize();
