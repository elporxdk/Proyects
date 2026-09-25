// =====================================================================
//  Prueba de la web de DOTLY en un navegador de verdad (Chromium)
// =====================================================================
//  La abre como la abriría una persona (servida por http y como archivo),
//  la usa con el ratón y con el teclado, practica lecciones enteras, la
//  conecta a un ESP32 simulado (WebSocket, HTTP y sin CORS) y al firmware
//  REAL de DOTLY corriendo en su banco, y pasa axe-core (accesibilidad) por
//  cada pantalla y cada tema.
//
//  Uso: NODE_PATH=$(npm root -g) node e2e.js [carpeta-de-capturas]
// =====================================================================
'use strict';
const path = require('path');
const fs = require('fs');
const http = require('http');
const { spawn } = require('child_process');
const { chromium } = require('playwright');
const { crearEsp32 } = require('./esp32_simulado');

const WEB = path.resolve(__dirname, '../../dotly/web');
const BANCO = path.resolve(__dirname, '../banco_dotly/.build/banco_dotly');
const CAPTURAS = process.argv[2] || path.join(__dirname, '.build', 'capturas');
fs.mkdirSync(CAPTURAS, { recursive: true });

let fallos = 0, total = 0;
function comprobar(ok, que) {
  total++;
  console.log('   ' + (ok ? 'OK   ' : 'FALLO') + '  ' + que);
  if (!ok) fallos++;
  return ok;
}
const seccion = (t) => console.log('\n== ' + t);
const dormir = (ms) => new Promise((r) => setTimeout(r, ms));
async function esperarQue(fn, ms = 6000) {
  const t = Date.now();
  while (Date.now() - t < ms) { if (await fn()) return true; await dormir(50); }
  return !!(await fn());
}

// axe-core: del NODE_PATH o de .libs (lo instala correr.sh)
let AXE = null;
for (const f of [() => require.resolve('axe-core/axe.min.js'), () => path.join(__dirname, '.libs/node_modules/axe-core/axe.min.js')]) {
  try { AXE = fs.readFileSync(f(), 'utf8'); break; } catch (e) { /* siguiente */ }
}

// ---- un servidor http «normal» para la web ----
function servirWeb() {
  const TIPOS = { '.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8', '.js': 'text/javascript; charset=utf-8' };
  const srv = http.createServer((req, res) => {
    let ruta = decodeURIComponent(new URL(req.url, 'http://x').pathname);
    if (ruta.endsWith('/')) ruta += 'index.html';
    const f = path.join(WEB, path.normalize(ruta));
    if (!f.startsWith(WEB) || !fs.existsSync(f) || fs.statSync(f).isDirectory()) { res.writeHead(404); res.end(); return; }
    res.writeHead(200, { 'Content-Type': TIPOS[path.extname(f)] || 'application/octet-stream' });
    fs.createReadStream(f).pipe(res);
  });
  return new Promise((ok) => srv.listen(0, '127.0.0.1', () => ok({ srv, url: 'http://127.0.0.1:' + srv.address().port + '/' })));
}

// ---- ayudas para leer la página ----
const leerCelda = (page, sel) => page.$$eval(sel + ' .punto', (ps) =>
  ps.reduce((m, b) => m | (b.getAttribute('aria-pressed') === 'true' ? 1 << (+b.dataset.punto - 1) : 0), 0));
const leerCeldaEstatica = (page, sel) => page.$eval(sel, (el) => {
  const cs = el.querySelectorAll('.celda');
  return Array.from(cs[cs.length - 1].querySelectorAll('.punto')).reduce((m, p, i) => m | (p.classList.contains('punto--on') ? 1 << i : 0), 0);
});
async function marcar(page, sel, puntos) {
  for (let d = 1; d <= 6; d++) if (puntos & (1 << (d - 1))) await page.click(sel + ' .punto[data-punto="' + d + '"]');
}
const estadoConexion = (page) => page.$eval('#tarjetaEstado', (e) => e.dataset.estado);

// Contesta la pregunta que haya en pantalla (bien, o mal a propósito)
async function contestar(page, bien = true) {
  const formar = await page.$('#celdaRespuesta');
  if (formar) {
    const etiqueta = (await page.textContent('#objetivo .objetivo__car')).trim();
    const puntos = await page.evaluate((e) => {
      for (const it of Dotly.Aprendizaje.CATALOGO.values()) if (it.etiqueta === e) return it.puntos;
      return -1;
    }, etiqueta);
    await marcar(page, '#celdaRespuesta', bien ? puntos : (puntos ^ 0b000100) || 0b000100);
    await page.click('#btnComprobar');
    return { modo: 'formar', puntos };
  }
  const puntos = await leerCeldaEstatica(page, '#objetivo');
  const ops = await page.$$eval('[data-opcion]', (bs) => bs.map((b) => Dotly.Aprendizaje.item(b.dataset.opcion).puntos));
  let k = ops.indexOf(puntos);
  if (!bien) k = (k + 1) % ops.length;
  await page.locator('.opcion').nth(k).click();
  return { modo: 'leer', puntos };
}

async function auditar(page, que, opciones) {
  if (!AXE) return;
  await page.addScriptTag({ content: AXE });
  const r = await page.evaluate(async (o) => {
    // WCAG 2.2 AA, buenas prácticas y AAA (contraste de 7:1 incluido)
    const res = await axe.run(document, o || { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag2aaa', 'wcag21a', 'wcag21aa', 'wcag22aa', 'best-practice'] } });
    return res.violations.map((v) => v.id + ' (' + v.nodes.length + '): ' + v.nodes.slice(0, 2).map((n) => n.target.join(' ')).join(' | '));
  }, opciones || null);
  comprobar(r.length === 0, 'axe sin problemas: ' + que + (r.length ? '\n            ' + r.join('\n            ') : ''));
}

async function sinScrollHorizontal(page) {
  return page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth);
}

(async () => {
  const { srv, url: BASE } = await servirWeb();
  const navegador = await chromium.launch();
  const errores = [];
  const vigilar = (page, tolerarRed) => {
    page.on('pageerror', (e) => errores.push('pageerror: ' + e.message));
    page.on('console', (m) => {
      if (m.type() !== 'error') return;
      // Al buscar el equipo, los intentos fallidos salen en la consola: es lo esperado
      if (tolerarRed && /WebSocket|Failed to load resource|CORS|net::ERR_|blocked/i.test(m.text())) return;
      errores.push('console: ' + m.text());
    });
  };
  let banco = null;
  try {
    // =================================================================
    seccion('Explorar: la celda interactiva (móvil, servida por http)');
    const ctx = await navegador.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, colorScheme: 'light' });
    await ctx.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: BASE.replace(/\/$/, '') });
    const page = await ctx.newPage();
    vigilar(page, true);
    await page.goto(BASE);
    comprobar(await page.title() === 'Explorar · DOTLY', 'arranca en Explorar');
    comprobar(await page.getAttribute('.menu__item[data-vista="explorar"]', 'aria-current') === 'page', 'el menú marca la pantalla actual');
    await marcar(page, '#celdaExplorar', 0b000011);
    comprobar(await page.textContent('#lecturaCar') === 'B' && await page.textContent('#lecturaQue') === 'Letra be',
      'puntos 1 y 2: «B», letra be');
    comprobar((await page.textContent('#lecturaExtra')).includes('el 2'), 'y avisa de que tras el signo de número es el 2');
    comprobar(await page.textContent('#lecturaUni') === '⠃', 'y su carácter Unicode ⠃');
    await page.focus('#celdaExplorar .punto[data-punto="1"]');
    await page.keyboard.press('ArrowRight');
    comprobar(await page.evaluate(() => document.activeElement.dataset.punto) === '4', 'flecha derecha: del punto 1 al 4');
    await page.keyboard.press('ArrowDown');
    await page.keyboard.press('Space');
    comprobar(await leerCelda(page, '#celdaExplorar') === 0b010011, 'Espacio sobre el punto 5 lo enciende (h = 1-2-5)');
    comprobar(await page.textContent('#lecturaCar') === 'H', '«H»');
    await page.focus('#t-explorar');
    await page.keyboard.press('Backspace');
    comprobar(await leerCelda(page, '#celdaExplorar') === 0, 'Retroceso borra la celda');
    await page.keyboard.press('1');
    await page.keyboard.press('4');
    comprobar(await page.textContent('#lecturaCar') === 'C', 'teclas 1 y 4: C');
    await page.keyboard.down('d'); await page.keyboard.down('j'); await page.keyboard.down('k');
    await page.keyboard.up('d'); await page.keyboard.up('j'); await page.keyboard.up('k');
    comprobar(await leerCelda(page, '#celdaExplorar') === 0b011010 && await page.textContent('#lecturaCar') === 'J',
      'D+J+K a la vez (Perkins): puntos 2-4-5, la J');
    await page.click('#alfabeto .signo[data-id="ñ"]');
    comprobar(await page.textContent('#lecturaCar') === 'Ñ' && await page.textContent('#lecturaPuntos') === 'Puntos 1-2-4-5-6',
      'el signo generador pone la Ñ (1-2-4-5-6)');
    comprobar(await page.getAttribute('#alfabeto .signo[data-id="ñ"]', 'aria-current') === 'true', 'y la marca en la lista');
    await page.click('#btnSiguienteSigno');
    comprobar(await page.textContent('#lecturaCar') === 'O', 'Siguiente: la O');
    await page.click('#btnAnterior');
    await page.click('#btnAnterior');
    comprobar(await page.textContent('#lecturaCar') === 'N', 'Anterior dos veces: la N');
    await page.click('#alfabeto .signo[data-id="mayuscula"]');
    comprobar((await page.textContent('#lecturaQue')) === 'Signo de mayúscula', 'los indicadores se explican: signo de mayúscula');
    await marcar(page, '#celdaExplorar', 0b101000);           // quita 4 y 6: queda vacía
    await page.keyboard.press('4');
    comprobar(await page.textContent('#lecturaQue') === 'Sin significado', 'el punto 4 solo: sin significado');
    await page.click('#btnLimpiar');
    await marcar(page, '#celdaExplorar', 0b000101);
    await page.click('#btnAlTexto');
    comprobar((await page.inputValue('#texto')).endsWith('k'), '«Añadir al traductor» añade la k');
    await page.screenshot({ path: path.join(CAPTURAS, 'movil_explorar.png') });
    await auditar(page, 'Explorar');

    // =================================================================
    seccion('Traductor');
    await page.click('.menu__item[data-vista="traductor"]');
    comprobar(await esperarQue(() => page.evaluate(() => document.activeElement.id === 't-traductor'), 2000),
      'al cambiar de pantalla, el foco va a su título');
    await page.fill('#texto', 'Hola 2b');
    comprobar(await page.inputValue('#unicode') === '⠨⠓⠕⠇⠁⠀⠼⠃⠐⠃', '«Hola 2b» = ⠨⠓⠕⠇⠁⠀⠼⠃⠐⠃ (mayúscula, número, minúscula)');
    comprobar(await page.textContent('#cuentaCeldas') === '10 celdas', '10 celdas');
    comprobar(await page.$$eval('#salida .tc--indicador', (e) => e.length) === 3, 'tres signos indicadores resaltados');
    await page.fill('#texto', 'DOTLY @ 3,5');
    comprobar(await page.inputValue('#unicode') === '⠨⠨⠙⠕⠞⠇⠽⠀⠀⠼⠉⠂⠑', 'palabra en mayúsculas (46 46) y coma decimal');
    comprobar(!(await page.isHidden('#avisoDesconocidos')) && (await page.textContent('#avisoDesconocidos')).includes('@'),
      'avisa de lo que no tiene braille (@)');
    await page.click('[data-ejemplo^="¿Qué"]');
    comprobar((await page.inputValue('#texto')).startsWith('¿Qué tal?'), 'los ejemplos rellenan el texto');
    await page.click('#btnCopiar');
    comprobar(await esperarQue(async () => (await page.textContent('#tostada')) === 'Braille copiado', 3000) &&
      await page.evaluate(() => navigator.clipboard.readText()) === await page.inputValue('#unicode'),
      'Copiar braille: el portapapeles tiene el braille Unicode');
    await page.screenshot({ path: path.join(CAPTURAS, 'movil_traductor.png') });
    await auditar(page, 'Traductor');

    // =================================================================
    seccion('Aprender: lecciones y práctica');
    await page.click('.menu__item[data-vista="aprender"]');
    comprobar(await page.$$eval('#lecciones > li', (e) => e.length) === 8, '8 lecciones');
    await auditar(page, 'Aprender (lista y modo profesor)');
    await page.click('[data-estudiar="serie2"]');
    comprobar(await page.$$eval('#fichas .ficha', (e) => e.length) === 10, 'la segunda serie tiene 10 fichas');
    comprobar((await page.textContent('#fichas')).includes('a + punto 3'), 'con su pista: k = a + punto 3');
    await auditar(page, 'Fichas de estudio');
    await page.click('#fichas [data-probar="q"]');
    comprobar(await page.title() === 'Explorar · DOTLY' && await page.textContent('#lecturaCar') === 'Q', '«Verla en la celda» lleva la Q a Explorar');
    await page.click('.menu__item[data-vista="aprender"]');
    await page.click('#estudioVolver');
    await page.click('[data-practicar="serie1"]');
    comprobar(!(await page.isHidden('#ap-sesion')), 'empieza la práctica de la primera serie');
    let vistas = { formar: 0, leer: 0 };
    for (let k = 0; k < 10; k++) {
      const r = await contestar(page, true);
      vistas[r.modo]++;
      if (!await esperarQue(async () => (await page.getAttribute('#retro', 'class')).includes('retro--bien'), 3000)) {
        comprobar(false, 'pregunta ' + (k + 1) + ' (' + r.modo + ') contestada bien');
        break;
      }
      if (k === 0 && r.modo === 'formar') await auditar(page, 'Pregunta de formar, contestada');
      if (k === 0 && r.modo === 'leer') await auditar(page, 'Pregunta de leer, contestada');
      await page.click('#btnSiguientePregunta');
    }
    comprobar(vistas.formar > 0 && vistas.leer > 0, 'salen preguntas de los dos tipos (' + vistas.formar + ' de formar, ' + vistas.leer + ' de leer)');
    comprobar(!(await page.isHidden('#ap-resumen')) && (await page.textContent('#resumenCifra')).startsWith('10 de 10'),
      'resumen: 10 de 10 a la primera');
    comprobar(await page.getAttribute('#resumenEstrellas [role="img"]', 'aria-label') === '3 de 3 estrellas', 'tres estrellas');
    await auditar(page, 'Resumen de la práctica');
    await page.click('#resumenVolver');
    comprobar((await page.getAttribute('#lecciones li:first-child .estrellas', 'aria-label')) === 'Mejor resultado: 3 de 3 estrellas',
      'la lección guarda su mejor resultado');

    // Fallos: la corrección dice qué falta y qué sobra
    await page.click('[data-practicar="serie3"]');
    let q = null;
    for (let k = 0; k < 10 && !q; k++) {
      if (await page.$('#celdaRespuesta')) { q = true; break; }
      await contestar(page, true);
      await page.click('#btnSiguientePregunta');
    }
    comprobar(!!q, 'aparece una pregunta de formar');
    if (q) {
      const etiqueta = (await page.textContent('#objetivo .objetivo__car')).trim();
      const puntos = await page.evaluate((e) => [...Dotly.Aprendizaje.CATALOGO.values()].find((it) => it.etiqueta === e).puntos, etiqueta);
      await page.click('#btnPista');
      comprobar(await page.$$eval('#celdaRespuesta .punto--pista', (e) => e.length) === 1, 'la pista señala un punto');
      // marca todos menos uno y uno de más
      const falta = puntos & -puntos;
      await marcar(page, '#celdaRespuesta', (puntos & ~falta) | (~puntos & 0b111111 & -(~puntos & 0b111111)));
      await page.click('#btnComprobar');
      const retro = await page.textContent('#retro');
      comprobar(/^Casi\. Te falta el punto \d y te sobra el punto \d\./.test(retro), 'primer fallo: «' + retro + '»');
      comprobar(await page.$$eval('#celdaRespuesta .punto--falta', (e) => e.length) === 1 &&
        await page.$$eval('#celdaRespuesta .punto--sobra', (e) => e.length) === 1, 'la celda marca el que falta (+) y el que sobra (×)');
      comprobar((await page.getAttribute('#celdaRespuesta .punto--falta', 'aria-label')).includes('falta'), 'y lo dice su etiqueta accesible');
      await page.screenshot({ path: path.join(CAPTURAS, 'movil_correccion.png') });
      await page.click('#btnComprobar');
      comprobar((await page.textContent('#retro')).includes('La respuesta era'), 'segundo fallo: enseña la respuesta');
      comprobar(await leerCelda(page, '#celdaRespuesta') === puntos, 'y la deja puesta en la celda');
    }
    await page.click('#sesionSalir');

    // Modo profesor: práctica a medida con el teclado
    await page.click('[data-prof="nada"]');
    await page.click('#formProfesor button[type="submit"]');
    comprobar((await page.textContent('#tostada')).includes('al menos un signo'), 'sin signos no empieza');
    await page.click('[data-prof-grupo="numeros"]');
    comprobar((await page.textContent('#profCuenta')) === '10 signos elegidos', 'el grupo de los números: 10 signos');
    await page.check('input[name="profTipo"][value="formar"]');
    await page.check('input[name="profTotal"][value="5"]');
    await page.click('#formProfesor button[type="submit"]');
    comprobar((await page.textContent('#sesionCuenta')).startsWith('Pregunta 1 de 5'), 'práctica a medida de 5 preguntas');
    comprobar(await page.$$eval('#respuesta .celda-fija', (e) => e.length) === 1, 'los números llevan el signo de número ya puesto');
    let bienTeclado = 0;
    for (let k = 0; k < 5; k++) {
      const etiqueta = (await page.textContent('#objetivo .objetivo__car')).trim();
      const puntos = await page.evaluate((e) => Dotly.Aprendizaje.item(e).puntos, etiqueta);
      for (let d = 1; d <= 6; d++) if (puntos & (1 << (d - 1))) await page.keyboard.press(String(d));
      await page.keyboard.press('Enter');                 // comprobar
      if ((await page.getAttribute('#retro', 'class')).includes('retro--bien')) bienTeclado++;
      await page.keyboard.press('Enter');                 // siguiente (el foco está en «Siguiente»)
    }
    comprobar(bienTeclado === 5, 'las 5 contestadas solo con el teclado (1-6 e Intro)');
    comprobar(!(await page.isHidden('#ap-resumen')), 'y el resumen al final');
    await page.click('#resumenVolver');

    // =================================================================
    seccion('Ajustes, temas y responsive');
    await page.click('#btnAjustes');
    comprobar(await page.evaluate(() => document.querySelector('#dlgAjustes').open), 'Ajustes se abre como diálogo');
    await auditar(page, 'Diálogo de ajustes');
    await page.check('input[name="tema"][value="amarillo"]');
    await page.check('input[name="escala"][value="1.5"]');
    await page.keyboard.press('Escape');
    comprobar(!(await page.evaluate(() => document.querySelector('#dlgAjustes').open)) &&
      await page.evaluate(() => document.activeElement.id) === 'btnAjustes', 'Escape lo cierra y el foco vuelve al botón');
    comprobar(await page.evaluate(() => document.documentElement.dataset.tema) === 'amarillo', 'tema amarillo sobre negro');
    await page.reload();
    comprobar(await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--escala').trim()) === '1.5',
      'los ajustes se recuerdan al recargar (texto enorme)');
    for (const v of ['explorar', 'traductor', 'aprender', 'conexion']) {
      await page.click('.menu__item[data-vista="' + v + '"]');
      comprobar(await sinScrollHorizontal(page), 'texto enorme, 390 px, ' + v + ': sin scroll horizontal');
      await auditar(page, 'tema amarillo, ' + v);
    }
    await page.click('.menu__item[data-vista="explorar"]');
    await page.screenshot({ path: path.join(CAPTURAS, 'movil_amarillo_enorme.png') });
    await page.evaluate(() => { localStorage.setItem('dotly.web.ajustes', JSON.stringify({ tema: 'oscuro', escala: 1 })); });
    await page.reload();
    for (const v of ['explorar', 'traductor', 'aprender', 'conexion']) {
      await page.click('.menu__item[data-vista="' + v + '"]');
      await auditar(page, 'tema oscuro, ' + v);
    }
    await page.evaluate(() => { localStorage.setItem('dotly.web.ajustes', JSON.stringify({ tema: 'claro', escala: 1 })); });
    for (const ancho of [320, 768, 1280]) {
      await page.setViewportSize({ width: ancho, height: 900 });
      await page.reload();
      let ok = true;
      for (const v of ['explorar', 'traductor', 'aprender', 'conexion']) {
        await page.click('.menu__item[data-vista="' + v + '"]');
        if (!await sinScrollHorizontal(page)) { ok = false; console.log('         desborda: ' + v); }
      }
      comprobar(ok, ancho + ' px: ninguna pantalla desborda');
    }
    // contraste reforzado (AAA, 7:1) en el tema claro
    await page.click('.menu__item[data-vista="explorar"]');
    await auditar(page, 'contraste AAA, Explorar', { runOnly: { type: 'rule', values: ['color-contrast-enhanced'] } });
    await page.click('.menu__item[data-vista="aprender"]');
    await auditar(page, 'contraste AAA, Aprender', { runOnly: { type: 'rule', values: ['color-contrast-enhanced'] } });
    await page.click('.menu__item[data-vista="explorar"]');
    await page.screenshot({ path: path.join(CAPTURAS, 'escritorio_explorar.png') });

    // Saltar al contenido
    await page.reload();
    await page.keyboard.press('Tab');
    comprobar(await page.evaluate(() => document.activeElement.className) === 'saltar', 'lo primero del tabulador: «Saltar al contenido»');
    await page.keyboard.press('Enter');
    comprobar(await page.evaluate(() => document.activeElement.id) === 'contenido', 'y lleva al contenido');

    // =================================================================
    seccion('Conexión: ESP32 simulado por WebSocket');
    await page.setViewportSize({ width: 390, height: 844 });
    await page.click('.menu__item[data-vista="conexion"]');
    await page.fill('#ip', 'hola mundo');
    await page.click('#btnConectar');
    comprobar(await page.getAttribute('#ip', 'aria-invalid') === 'true', 'una dirección mala se marca como no válida');
    await page.fill('#ip', '127.0.0.1:9');
    await page.click('#btnConectar');
    await esperarQue(async () => (await estadoConexion(page)) === 'error', 8000);
    comprobar((await page.textContent('#estadoDetalle')).startsWith('No contesta nada'), 'donde no hay nada: «' + (await page.textContent('#estadoDetalle')).slice(0, 40) + '…»');
    await auditar(page, 'Conexión con error');

    const esp = crearEsp32();
    const pe = await esp.arrancar();
    await page.fill('#ip', '127.0.0.1:' + pe.http);
    await page.click('#formConexion summary');
    await page.fill('#puertoWs', String(pe.ws));
    await page.click('#btnConectar');
    comprobar(await esperarQue(async () => (await estadoConexion(page)) === 'conectado', 8000), 'conecta');
    comprobar((await page.textContent('#estadoDetalle')).startsWith('Por WebSocket'), 'por WebSocket');
    comprobar(await esperarQue(() => esp.recibidos.some((m) => m.via === 'ws' && m.tipo === 'hola')), 'y saluda al ESP32');
    comprobar(await esperarQue(async () => (await page.textContent('#estadoTitulo')) === 'Conectado a DOTLY simulado', 2000),
      'con el nombre que da el equipo');
    comprobar(await page.textContent('.menu__item[data-vista="conexion"] .menu__insignia') === '✓' &&
      await page.evaluate(() => {
        const muestra = document.createElement('i');
        muestra.style.background = 'var(--ok)';
        document.body.append(muestra);
        const ok = getComputedStyle(muestra).backgroundColor;
        muestra.remove();
        return getComputedStyle(document.querySelector('.menu__item[data-vista="conexion"] .menu__insignia')).backgroundColor === ok;
      }), 'en el móvil, ✓ en verde sobre «Conexión»');
    await page.screenshot({ path: path.join(CAPTURAS, 'movil_conexion.png') });
    await auditar(page, 'Conexión conectada');

    await page.click('.menu__item[data-vista="explorar"]');
    await page.click('#btnLimpiar');
    esp.recibidos.length = 0;
    await marcar(page, '#celdaExplorar', 0b000011);
    comprobar(await esperarQue(() => esp.recibidos.some((m) => m.tipo === 'celda' && m.puntos === 3 && m.caracter === 'b')),
      '→ la celda de Explorar llega al ESP32 (puntos 1-2, «b»)');
    const antes = esp.recibidos.length;
    esp.marcar(0b000111);
    comprobar(await esperarQue(async () => (await page.textContent('#lecturaCar')) === 'L'), '← el aparato marca 1-2-3: la web enseña la L');
    await dormir(400);
    comprobar(!esp.recibidos.slice(antes).some((m) => m.tipo === 'celda'), 'y no se lo devuelve (sin eco)');
    esp.pulsar('siguiente');
    comprobar(await esperarQue(async () => (await page.textContent('#lecturaCar')) === 'M'), '← botón «siguiente» del aparato: la M');
    esp.pulsar('aceptar');
    comprobar(await esperarQue(async () => (await page.inputValue('#texto')).endsWith('m')), '← «aceptar»: añade la m al traductor');

    await page.click('.menu__item[data-vista="traductor"]');
    await page.fill('#texto', 'Hola DOTLY');
    comprobar(await esperarQue(() => esp.recibidos.some((m) => m.tipo === 'texto' && m.texto === 'Hola DOTLY'), 3000),
      '→ el texto del traductor llega (medio segundo después de escribir)');

    await page.click('.menu__item[data-vista="aprender"]');
    await page.click('[data-practicar="serie1"]');
    let comprobadas = 0;
    for (let k = 0; k < 6 && comprobadas < 3; k++) {
      await dormir(250);
      const ult = [...esp.recibidos].reverse().find((m) => m.tipo === 'celda');
      if (await page.$('#celdaRespuesta')) {
        const etiqueta = (await page.textContent('#objetivo .objetivo__car')).trim();
        const puntos = await page.evaluate((e) => Dotly.Aprendizaje.item(e.toLowerCase()).puntos, etiqueta);
        comprobar(ult && ult.puntos === 0 && ult.caracter === '', 'formar: el aparato empieza con la celda vacía');
        esp.marcar(puntos);                               // el alumno la forma en el aparato
        await esperarQue(async () => (await leerCelda(page, '#celdaRespuesta')) === puntos, 2000);
        esp.pulsar('aceptar');                            // y pulsa aceptar en el aparato
        comprobar(await esperarQue(async () => (await page.getAttribute('#retro', 'class')).includes('retro--bien'), 3000),
          '← formada y aceptada desde el aparato: correcta');
      } else {
        const puntos = await leerCeldaEstatica(page, '#objetivo');
        comprobar(ult && ult.puntos === puntos && ult.caracter === '', 'leer: el aparato recibe la celda SIN la respuesta');
        await contestar(page, true);
      }
      comprobadas++;
      esp.pulsar('aceptar');                              // aceptar = siguiente
      await esperarQue(async () => (await page.getAttribute('#retro', 'class')) === 'retro', 2000);
    }
    await page.click('#sesionSalir');

    esp.cortarWs();
    comprobar(await esperarQue(async () => (await estadoConexion(page)) === 'reconectando', 3000), 'si se corta, se ve «Reconectando»');
    comprobar(await esperarQue(async () => (await estadoConexion(page)) === 'conectado', 8000), 'y vuelve sola');
    await page.click('.menu__item[data-vista="conexion"]');
    await page.click('#btnDesconectar');
    comprobar(await estadoConexion(page) === 'local' && await esperarQue(() => esp.clientesWs.size === 0, 2000),
      'Desconectar cierra la conexión y la web sigue sola');
    await esp.parar();

    // =================================================================
    seccion('Conexión: ESP32 simulado solo por HTTP');
    const esh = crearEsp32({ conWs: false });
    const ph = await esh.arrancar();
    await page.check('input[name="transporte"][value="http"]');
    await page.fill('#ip', '127.0.0.1:' + ph.http);
    await page.fill('#intervalo', '300');
    await page.click('#btnConectar');
    comprobar(await esperarQue(async () => (await estadoConexion(page)) === 'conectado', 8000) &&
      (await page.textContent('#estadoDetalle')).startsWith('Por HTTP'), 'conecta por HTTP');
    comprobar(await esperarQue(async () => (await page.textContent('#pantallaEquipo')).includes('DOTLY simulado')), 'y enseña la pantalla del equipo');
    await page.click('.menu__item[data-vista="explorar"]');
    await page.click('#btnLimpiar');
    await marcar(page, '#celdaExplorar', 0b001001);
    comprobar(await esperarQue(() => esh.recibidos.some((m) => m.via === 'http' && m.tipo === 'celda' && m.puntos === 9 && m.caracter === 'c')),
      '→ POST /api/celda con los puntos y la letra');
    esh.marcar(0b011001);
    comprobar(await esperarQue(async () => (await page.textContent('#lecturaCar')) === 'D', 3000), '← la consulta periódica trae la celda del aparato (D)');
    esh.pulsar('siguiente');
    comprobar(await esperarQue(async () => (await page.textContent('#lecturaCar')) === 'E', 3000), '← y sus botones (siguiente: E)');
    await page.click('.menu__item[data-vista="conexion"]');
    await page.click('#btnDesconectar');
    await esh.parar();

    // =================================================================
    seccion('Conexión: equipo sin CORS (solo envío)');
    const esn = crearEsp32({ conWs: false, cors: false });
    const pn = await esn.arrancar();
    await page.check('input[name="transporte"][value="auto"]');
    await page.fill('#ip', '127.0.0.1:' + pn.http);
    await page.click('#btnConectar');
    comprobar(await esperarQue(async () => (await estadoConexion(page)) === 'solo-envio', 8000), 'sin cabecera CORS: «solo envío»');
    comprobar((await page.textContent('#estadoDetalle')).includes('CORS'), 'y explica por qué');
    await page.click('.menu__item[data-vista="traductor"]');
    await page.fill('#texto', 'sin cors');
    comprobar(await esperarQue(() => esn.recibidos.some((m) => m.tipo === 'texto' && m.texto === 'sin cors'), 3000), '→ aun así, el texto llega');
    await page.click('.menu__item[data-vista="conexion"]');
    await page.click('#btnDesconectar');
    await esn.parar();

    // =================================================================
    seccion('Conexión: el firmware REAL de DOTLY (banco de pruebas)');
    if (!fs.existsSync(BANCO)) {
      console.log('   (no está compilado ' + path.relative(process.cwd(), BANCO) + ': ./pruebas/banco_dotly/correr.sh lo compila; se omite)');
    } else {
      const nvs = path.join(__dirname, '.build', 'nvs_web');
      try { fs.unlinkSync(nvs + '.dotly'); } catch (e) { /* no estaba */ }
      banco = spawn(BANCO, ['servidor'], { env: Object.assign({}, process.env, { DOTLY_NVS: nvs, DOTLY_PUERTO: '0' }) });
      let salida = '';
      banco.stdout.on('data', (d) => { salida += d; });
      await esperarQue(() => /PUERTO (\d+)/.test(salida), 90000);
      const puerto = +/PUERTO (\d+)/.exec(salida)[1];
      const estadoDotly = async () => JSON.parse(await (await fetch('http://127.0.0.1:' + puerto + '/api/estado')).text());
      await page.fill('#ip', '127.0.0.1:' + puerto);
      await page.fill('#puertoWs', '81');
      await page.click('#btnConectar');
      comprobar(await esperarQue(async () => (await estadoConexion(page)) === 'solo-envio', 10000),
        'el firmware actual no manda CORS: «solo envío»');
      await page.click('.menu__item[data-vista="traductor"]');
      await page.fill('#texto', 'Hola');
      comprobar(await esperarQue(async () => {
        const e = await estadoDotly();
        return e.modo === 'pizarra' && e.pantalla[1].startsWith('⠨ ⠓ ⠕ ⠇ ⠁');
      }, 5000), '→ «Hola» aparece en braille en la pantalla del DOTLY (su /api/pizarra)');
      await page.click('.menu__item[data-vista="explorar"]');
      await page.click('#btnLimpiar');
      await marcar(page, '#celdaExplorar', 0b001001);
      comprobar(await esperarQue(async () => (await estadoDotly()).pantalla[1].startsWith('⠉'), 5000),
        '→ la letra de la celda (c) también');
      await page.click('.menu__item[data-vista="conexion"]');
      await page.click('#btnDesconectar');
    }

    comprobar(errores.length === 0, 'sin errores de JavaScript' + (errores.length ? ':\n            ' + errores.join('\n            ') : ''));
    await ctx.close();

    // =================================================================
    seccion('Abierta como archivo (file://), sin servidor');
    const ctx2 = await navegador.newContext({ viewport: { width: 1280, height: 900 }, colorScheme: 'dark' });
    const p2 = await ctx2.newPage();
    const errores2 = [];
    p2.on('pageerror', (e) => errores2.push(e.message));
    await p2.goto('file://' + path.join(WEB, 'index.html') + '#aprender');
    comprobar(await p2.title() === 'Aprender · DOTLY', 'abre directamente en la pantalla del #ancla');
    await p2.click('[data-practicar="tildes"]');
    await contestar(p2, true);
    comprobar(await esperarQue(async () => (await p2.getAttribute('#retro', 'class')).includes('retro--bien'), 3000), 'se puede practicar');
    await p2.screenshot({ path: path.join(CAPTURAS, 'escritorio_oscuro_practica.png') });
    const esf = crearEsp32();
    const pf = await esf.arrancar();
    await p2.click('.menu__item[data-vista="conexion"]');
    await p2.fill('#ip', '127.0.0.1:' + pf.http);
    await p2.click('#formConexion summary');
    await p2.fill('#puertoWs', String(pf.ws));
    await p2.click('#btnConectar');
    comprobar(await esperarQue(async () => (await estadoConexion(p2)) === 'conectado', 8000) &&
      (await p2.textContent('#estadoDetalle')).startsWith('Por WebSocket'), 'desde un archivo también conecta por WebSocket');
    await p2.click('#btnDesconectar');
    await esf.parar();
    comprobar(errores2.length === 0, 'sin errores de JavaScript' + (errores2.length ? ': ' + errores2.join(' | ') : ''));
    await ctx2.close();
  } catch (e) {
    comprobar(false, 'la prueba se ha roto: ' + (e.stack || e.message));
  } finally {
    if (banco) { banco.stdin.end(); banco.kill(); }
    await navegador.close();
    srv.close();
  }
  if (!AXE) console.log('\n(axe-core no está instalado: no se ha auditado la accesibilidad)');
  const bytes = fs.readdirSync(WEB, { recursive: true }).map((f) => path.join(WEB, f))
    .filter((f) => fs.statSync(f).isFile() && /\.(html|css|js)$/.test(f)).reduce((a, f) => a + fs.statSync(f).size, 0);
  console.log('\nLa web pesa ' + Math.round(bytes / 1024) + ' KB (html + css + js)');
  console.log('---\n' + (fallos ? 'HAY FALLOS (' + fallos + ' de ' + total + ')' : 'TODO OK (' + total + ' comprobaciones)') + '\ncapturas en ' + CAPTURAS);
  process.exit(fallos ? 1 : 0);
})();
