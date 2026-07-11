const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const fs = require('fs');
const path = require('path');

// ================== CONFIGURACIÓN ==================
const ARCHIVO_ENTRADA = './doctores.txt';   // un número por línea
const ARCHIVO_SALIDA_CSV = './resultados.csv';
const ARCHIVO_SALIDA_TXT = './resultados.txt';
const CODIGO_PAIS = '503';                  // El Salvador
const PAUSA_MS = 1500;                      // pausa entre consultas (evita bloqueos)
const TAMANO_LOTE = 150;                    // cada cuántos números hacer una pausa larga
const PAUSA_LOTE_MS = 60000;                // pausa larga entre lotes (1 minuto)
// =====================================================

const client = new Client({
    authStrategy: new LocalAuth(),
    puppeteer: {
        headless: true,
        executablePath: '/usr/bin/chromium', // Chromium del sistema (confirmado con 'which chromium')
        args: [
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-dev-shm-usage'  // evita errores de memoria compartida en la Pi
        ]
    }
});

// Limpia y normaliza un número: quita espacios, guiones, +, etc.
function normalizarNumero(numeroCrudo) {
    let n = numeroCrudo.trim().replace(/[\s\-()]/g, '');
    n = n.replace(/^\+/, '');

    // Si ya viene con código de país (empieza con 503 y tiene 11 dígitos), lo dejamos
    if (n.startsWith(CODIGO_PAIS) && n.length === 11) {
        return n;
    }
    // Si son 8 dígitos (número local sin código de país), le anteponemos el código
    if (n.length === 8) {
        return CODIGO_PAIS + n;
    }
    // Cualquier otro caso, lo devolvemos tal cual para que quede registrado como raro
    return n;
}

function leerNumeros(archivo) {
    const contenido = fs.readFileSync(archivo, 'utf-8');
    return contenido
        .split('\n')
        .map(l => l.trim())
        .filter(l => l.length > 0);
}

function cargarResultadosPrevios() {
    if (!fs.existsSync(ARCHIVO_SALIDA_CSV)) {
        return [];
    }
    const contenido = fs.readFileSync(ARCHIVO_SALIDA_CSV, 'utf-8');
    const lineas = contenido.split('\n').map(l => l.trim()).filter(l => l.length > 0);
    lineas.shift(); // quitar encabezado

    return lineas.map(linea => {
        const [original, normalizado, tieneWhatsappStr, error] = linea.split(',');
        let tieneWhatsapp;
        if (tieneWhatsappStr === 'true') tieneWhatsapp = true;
        else if (tieneWhatsappStr === 'false') tieneWhatsapp = false;
        else tieneWhatsapp = null;
        return { original, normalizado, tieneWhatsapp, error: error || null };
    });
}

function guardarResultados(resultados) {
    // CSV
    const encabezado = 'numero_original,numero_normalizado,tiene_whatsapp,error\n';
    const filasCsv = resultados.map(r =>
        `${r.original},${r.normalizado},${r.tieneWhatsapp === null ? 'ERROR' : r.tieneWhatsapp},${r.error || ''}`
    );
    fs.writeFileSync(ARCHIVO_SALIDA_CSV, encabezado + filasCsv.join('\n'), 'utf-8');

    // TXT legible
    const lineasTxt = resultados.map(r => {
        const estado = r.tieneWhatsapp === null ? 'ERROR' : (r.tieneWhatsapp ? 'SI' : 'NO');
        return `${r.original} -> ${estado}`;
    });
    fs.writeFileSync(ARCHIVO_SALIDA_TXT, lineasTxt.join('\n'), 'utf-8');

    console.log(`\nResultados guardados en:\n- ${ARCHIVO_SALIDA_CSV}\n- ${ARCHIVO_SALIDA_TXT}`);
}

async function verificarTodos(numeros, resultadosPrevios) {
    const resultados = [...resultadosPrevios];
    const yaVerificados = new Set(resultadosPrevios.map(r => r.original));

    const pendientes = numeros.filter(n => !yaVerificados.has(n));
    const saltados = numeros.length - pendientes.length;

    if (saltados > 0) {
        console.log(`Saltando ${saltados} número(s) ya verificados anteriormente.`);
    }
    console.log(`Números pendientes por verificar: ${pendientes.length}\n`);

    for (let i = 0; i < pendientes.length; i++) {
        const original = pendientes[i];
        const normalizado = normalizarNumero(original);
        const idWhatsapp = `${normalizado}@c.us`;

        process.stdout.write(`[${i + 1}/${pendientes.length}] Verificando ${original} (${normalizado})... `);

        try {
            const tieneWhatsapp = await client.isRegisteredUser(idWhatsapp);
            console.log(tieneWhatsapp ? 'SI tiene WhatsApp' : 'NO tiene WhatsApp');
            resultados.push({ original, normalizado, tieneWhatsapp, error: null });
        } catch (err) {
            console.log('ERROR ->', err.message);
            resultados.push({ original, normalizado, tieneWhatsapp: null, error: err.message });
        }

        // Guardado incremental por si el proceso se corta a la mitad
        guardarResultados(resultados);

        if (i < pendientes.length - 1) {
            const esFinDeLote = (i + 1) % TAMANO_LOTE === 0;
            if (esFinDeLote) {
                console.log(`\nPausa larga de ${PAUSA_LOTE_MS / 1000}s para evitar bloqueos (lote completado)...\n`);
                await new Promise(resolve => setTimeout(resolve, PAUSA_LOTE_MS));
            } else {
                await new Promise(resolve => setTimeout(resolve, PAUSA_MS));
            }
        }
    }

    return resultados;
}

client.on('qr', qr => {
    console.log('Escanea este código QR con WhatsApp (Dispositivos vinculados):\n');
    qrcode.generate(qr, { small: true });
});

client.on('authenticated', () => {
    console.log('Sesión autenticada correctamente.');
});

client.on('ready', async () => {
    console.log('Cliente de WhatsApp listo.\n');

    if (!fs.existsSync(ARCHIVO_ENTRADA)) {
        console.error(`No se encontró el archivo ${ARCHIVO_ENTRADA}`);
        process.exit(1);
    }

    const numeros = leerNumeros(ARCHIVO_ENTRADA);
    const resultadosPrevios = cargarResultadosPrevios();
    console.log(`Se encontraron ${numeros.length} números en el archivo de entrada.`);
    console.log(`Resultados previos cargados: ${resultadosPrevios.length}\n`);

    const yaVerificados = new Set(resultadosPrevios.map(r => r.original));
    const hayPendientes = numeros.some(n => !yaVerificados.has(n));

    if (!hayPendientes) {
        console.log('Todos los números del archivo ya fueron verificados anteriormente. Nada que hacer.');
        await client.destroy();
        process.exit(0);
    }

    const resultados = await verificarTodos(numeros, resultadosPrevios);

    const totalSi = resultados.filter(r => r.tieneWhatsapp === true).length;
    const totalNo = resultados.filter(r => r.tieneWhatsapp === false).length;
    const totalError = resultados.filter(r => r.tieneWhatsapp === null).length;

    console.log('\n===== RESUMEN =====');
    console.log(`Total verificados: ${resultados.length}`);
    console.log(`Con WhatsApp: ${totalSi}`);
    console.log(`Sin WhatsApp: ${totalNo}`);
    console.log(`Errores: ${totalError}`);

    await client.destroy();
    process.exit(0);
});

client.on('auth_failure', msg => {
    console.error('Fallo de autenticación:', msg);
});

client.on('disconnected', reason => {
    console.log('Cliente desconectado:', reason);
});

client.initialize();
