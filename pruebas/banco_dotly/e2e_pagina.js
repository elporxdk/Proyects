// =====================================================================
//  ESTO NO ES FIRMWARE. Prueba de la pagina de DOTLY en un navegador.
// =====================================================================
//  Arranca el banco en modo "servidor" (el firmware real con un socket de
//  verdad), abre la pagina en Chromium con Playwright y la usa como una
//  persona: pulsa los botones, escribe en braille, cambia de modo... y mira
//  que la pantalla de DOTLY (su espejo en la pagina) responde.
//
//  Uso: NODE_PATH=$(npm root -g) node e2e_pagina.js [carpeta-de-capturas]
// =====================================================================
'use strict';
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');
const { chromium } = require('playwright');

const CAPTURAS = process.argv[2] || path.join(__dirname, '.build', 'capturas');
fs.mkdirSync(CAPTURAS, { recursive: true });

let fallos = 0;
function comprobar(ok, que) {
  console.log('   ' + (ok ? 'OK   ' : 'FALLO') + '  ' + que);
  if (!ok) fallos++;
}

function arrancarBanco() {
  return new Promise((resolve, reject) => {
    const env = Object.assign({}, process.env, { DOTLY_NVS: path.join(__dirname, '.build', 'nvs_e2e'), DOTLY_PUERTO: '0' });
    try { fs.unlinkSync(env.DOTLY_NVS + '.dotly'); } catch (e) {}
    const p = spawn(path.join(__dirname, '.build', 'banco_dotly'), ['servidor'], { env });
    let salida = '';
    const t = setTimeout(() => reject(new Error('el banco no arranca:\n' + salida)), 90000);
    p.stdout.on('data', d => {
      salida += d;
      const m = /PUERTO (\d+)/.exec(salida);
      if (m) { clearTimeout(t); resolve({ p, puerto: +m[1] }); }
    });
    p.stderr.on('data', d => { salida += d; });
  });
}

// Texto de una fila del espejo del LCD: las celdas braille como ⠿, lo demas tal cual
async function filaLcd(page, f) {
  return page.$$eval('#lcd .fila', (filas, f) => Array.from(filas[f].children).map(c => {
    const puntos = c.querySelectorAll('.celda i');
    if (puntos.length) {
      const orden = [0, 3, 1, 4, 2, 5];
      let p = 0;
      puntos.forEach((i, k) => { if (i.classList.contains('on')) p |= 1 << orden[k]; });
      return String.fromCodePoint(0x2800 + p);
    }
    return c.classList.contains('lleno') ? '█' : (c.textContent || ' ');
  }).join(''), f);
}
async function esperarLcd(page, f, prueba, ms = 6000) {
  const t = Date.now();
  let ultimo = '';
  while (Date.now() - t < ms) {
    ultimo = await filaLcd(page, f);
    if (prueba(ultimo)) return true;
    await page.waitForTimeout(100);
  }
  console.log('         espejo fila ' + f + ': |' + ultimo + '|');
  return false;
}

(async () => {
  const { p: banco, puerto } = await arrancarBanco();
  const base = 'http://127.0.0.1:' + puerto + '/';
  const browser = await chromium.launch();
  const errores = [];
  try {
    // ------------------------------------------------ móvil, tema claro
    const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, colorScheme: 'light', deviceScaleFactor: 2 });
    const page = await ctx.newPage();
    page.on('pageerror', e => errores.push('pageerror: ' + e.message));
    // Los 400 y 409 son respuestas buscadas (p. ej. una combinacion que no es letra)
    page.on('console', m => {
      if (m.type() === 'error' && !/status of (400|409)/.test(m.text())) errores.push('console: ' + m.text());
    });
    await page.goto(base);

    comprobar(await esperarLcd(page, 0, t => t.startsWith('DOTLY')), 'el espejo del LCD muestra el menu de DOTLY');
    comprobar(await esperarLcd(page, 1, t => t.startsWith('> Aprender')), '"> Aprender"');
    comprobar((await page.textContent('#con')).includes('Conectado'), 'la pagina dice que esta conectada');

    // JSON de todas las rutas de lectura
    const rutas = ['/api/estado', '/api/tabla', '/api/senas', '/api/palabras', '/api/ajustes', '/api/progreso'];
    for (const r of rutas) {
      const ok = await page.evaluate(async r => { try { await (await fetch(r)).json(); return true; } catch (e) { return false; } }, r);
      comprobar(ok, r + ' devuelve JSON valido');
    }

    // Teclado virtual
    await page.click('.tecla[data-b="3"]');
    comprobar(await esperarLcd(page, 1, t => t.startsWith('> Escribir')), 'el boton SW3 de la pagina mueve el menu de DOTLY');
    comprobar((await page.textContent('#ayuda')).includes('Entrar lo abre'), 'la ayuda explica las teclas del menu');
    await page.keyboard.press('ArrowRight');
    comprobar(await esperarLcd(page, 1, t => t.startsWith('> Reto: leer')), 'y la flecha derecha del teclado del ordenador tambien');
    await page.keyboard.press('Escape');
    await page.keyboard.press('ArrowLeft');
    await page.keyboard.press('ArrowLeft');
    comprobar(await esperarLcd(page, 1, t => t.startsWith('> Aprender')), 'flecha izquierda');
    await page.screenshot({ path: path.join(CAPTURAS, 'movil_inicio.png'), fullPage: true });

    // Sin desbordes horizontales en el movil, en ninguna pestaña
    for (const t of ['inicio', 'braille', 'senas', 'modos', 'progreso', 'ajustes']) {
      await page.click('#t-' + t);
      await page.waitForTimeout(400);
      const ancho = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]);
      comprobar(ancho[0] <= ancho[1], 'pestaña ' + t + ' sin scroll horizontal en 390 px (' + ancho.join(' / ') + ')');
    }

    // ---- Braille: traductor y pizarra
    await page.click('#t-braille');
    await page.fill('#tx', 'Hola 12');
    comprobar(await page.$$eval('#txCeldas .tc', e => e.length) === 9, 'traductor: "Hola 12" son 9 celdas (mayuscula, numero...)');
    comprobar((await page.textContent('#txUni')) === '⠨⠓⠕⠇⠁⠀⠼⠁⠃', 'y en Unicode: ⠨⠓⠕⠇⠁⠀⠼⠁⠃');
    await page.click('#txMostrar');
    await page.click('#t-inicio');
    comprobar(await esperarLcd(page, 1, t => t.startsWith('⠨ ⠓ ⠕ ⠇ ⠁ ⠀ ⠼ ⠁')), 'la pizarra en el espejo del LCD');
    comprobar((await page.textContent('#modoTxt')).includes('Pizarra'), 'modo: Pizarra');

    // ---- Maquina Perkins: F+D a la vez = puntos 1 y 2 = b
    await page.click('#t-braille');
    await page.click('#escritoBorrar');
    await page.keyboard.down('f'); await page.keyboard.down('d');
    comprobar((await page.textContent('#perkCar')) === 'B', 'F+D pulsadas: la pagina dice B');
    await page.keyboard.up('f'); await page.keyboard.up('d');
    await page.waitForFunction(() => document.querySelector('#escrito').textContent === 'B', null, { timeout: 5000 }).catch(() => {});
    comprobar((await page.textContent('#escrito')) === 'B', 'al soltarlas, DOTLY escribe la B');
    await page.keyboard.press(' ');
    // puntos 1 y 4 con el raton = c
    await page.click('.pd[data-d="0"]'); await page.click('.pd[data-d="3"]');
    comprobar((await page.textContent('#perkCar')) === 'C', 'puntos 1 y 4 tocados: C');
    await page.click('#perkEscribir');
    await page.waitForFunction(() => document.querySelector('#escrito').textContent === 'B C', null, { timeout: 5000 }).catch(() => {});
    comprobar((await page.textContent('#escrito')) === 'B C', 'escrito en DOTLY: "B C"');
    await page.keyboard.press('Backspace');
    await page.waitForFunction(() => document.querySelector('#escrito').textContent === 'B ', null, { timeout: 5000 }).catch(() => {});
    comprobar((await page.textContent('#escrito')) === 'B ', 'Retroceso borra la ultima');
    await page.click('.pd[data-d="3"]');
    await page.click('#perkEscribir');
    await page.waitForTimeout(600);
    comprobar((await page.textContent('#aviso')).includes('ninguna letra'), 'el punto 4 solo: avisa de que no es ninguna letra');
    await page.click('#perkLimpiar');
    // signo generador: tocar la ñ la pone en la maquina
    await page.click('#bloquesChips [data-bv="2"]');
    await page.click('#signos [data-s="14"]');
    comprobar((await page.textContent('#perkCar')) === 'Ñ' && (await page.textContent('#perkPts')).includes('1-2-4-5-6'), 'signo generador: la Ñ, puntos 1-2-4-5-6');
    await page.screenshot({ path: path.join(CAPTURAS, 'movil_braille.png'), fullPage: true });

    // ---- Señas
    await page.click('#t-senas');
    await page.waitForSelector('#senasLista .sena');
    comprobar(await page.$$eval('#senasLista .sena', e => e.length) === 27, 'las 27 letras del alfabeto manual');
    const b = page.locator('#senasLista .sena[data-i="1"]');
    await b.locator('[data-acc="editar"]').click();
    await b.locator('textarea').fill('Palma al frente, dedos juntos y pulgar doblado.');
    await b.locator('[data-acc="guardar"]').click();
    await page.waitForSelector('#senasLista .sena[data-i="1"] .etq.verde', { timeout: 5000 }).catch(() => {});
    comprobar(await page.locator('#senasLista .sena[data-i="1"] .etq.verde').count() === 1, 'la B cambiada queda marcada');
    await page.locator('#senasLista .sena[data-i="1"] [data-acc="editar"]').click();
    await page.locator('#senasLista .sena[data-i="1"] [data-acc="restaurar"]').click();
    await page.waitForFunction(() => !document.querySelector('#senasLista .sena[data-i="1"] .etq.verde'), null, { timeout: 5000 }).catch(() => {});
    comprobar(await page.locator('#senasLista .sena[data-i="1"] .etq.verde').count() === 0, 'y vuelve a la original');
    await page.fill('#dlTxt', 'ñú');
    await page.click('#dlAqui');
    comprobar((await page.textContent('#dlLetra')) === 'Ñ', 'deletrear aqui: empieza por la Ñ');
    await page.click('#dlSig');
    comprobar((await page.textContent('#dlLetra')) === 'U', 'la ú se deletrea como U');
    await page.fill('#dlTxt', 'sol');
    await page.click('#dlDotly');
    await page.click('#t-inicio');
    comprobar(await esperarLcd(page, 0, t => t.startsWith('S') && t.includes('SOL')), 'deletrear en DOTLY: S ... SOL');
    await page.click('#t-senas');
    await page.screenshot({ path: path.join(CAPTURAS, 'movil_senas.png'), fullPage: true });

    // ---- Modos
    await page.click('#t-modos');
    comprobar(await page.$$eval('#modos .modo', e => e.length) === 9, '9 modos en el panel');
    await page.click('#modos [data-modo="reto_leer"] .btn');
    await page.waitForSelector('#modos .modo.activo[data-modo="reto_leer"]', { timeout: 5000 }).catch(() => {});
    comprobar(await page.locator('#modos .modo.activo[data-modo="reto_leer"]').count() === 1, 'el reto de leer queda marcado "En marcha"');
    await page.click('#modos [data-modo="aprender"] .chip[data-b="6"]');
    await page.click('#t-inicio');
    comprobar(await esperarLcd(page, 1, t => t.startsWith('1234567890')), 'el bloque NUMEROS abierto desde el panel');
    await page.click('#t-modos');
    await page.check('input[name="preguntas"][value="20"]', { force: true });
    await page.waitForTimeout(500);
    const aj = await page.evaluate(async () => (await fetch('/api/ajustes')).json());
    comprobar(aj.preguntas === 20, 'preguntas por ronda = 20, guardado en DOTLY');
    await page.screenshot({ path: path.join(CAPTURAS, 'movil_modos.png'), fullPage: true });

    // ---- Progreso
    await page.click('#t-progreso');
    await page.waitForSelector('#progGrid .pl');
    comprobar(await page.$$eval('#progGrid .pl', e => e.length) === 33, 'progreso: 33 casillas (letras y tildes)');
    comprobar(await page.$$eval('#stats .stat', e => e.length) === 7, 'y 7 cifras resumen');

    // ---- Ajustes
    await page.click('#t-ajustes');
    await page.waitForSelector('#medidor .r');
    comprobar(await page.$$eval('#medidor .r', e => e.length) === 5, 'el medidor del teclado dibuja los 5 rangos');
    comprobar((await page.textContent('#tecCal')).includes('guardada'), 'calibracion guardada');
    await page.check('input[name="tema"][value="oscuro"]', { force: true });
    comprobar(await page.evaluate(() => document.documentElement.dataset.tema) === 'oscuro', 'tema oscuro');
    await page.screenshot({ path: path.join(CAPTURAS, 'movil_ajustes_oscuro.png'), fullPage: true });
    await page.fill('#clave', '123');
    await page.click('#formRed button[type=submit]');
    await page.waitForTimeout(300);
    comprobar((await page.textContent('#aviso')).includes('8 a 63'), 'una clave de 3 caracteres no se manda');
    await page.reload();
    comprobar(await page.evaluate(() => document.documentElement.dataset.tema) === 'oscuro', 'el tema se recuerda al recargar');
    comprobar(await page.evaluate(() => document.querySelector('[role=tab][aria-selected=true]').dataset.p) === 'ajustes',
      'y la pestaña abierta');

    // ------------------------------------------------ escritorio, oscuro
    const ctx2 = await browser.newContext({ viewport: { width: 1280, height: 900 }, colorScheme: 'dark' });
    const p2 = await ctx2.newPage();
    p2.on('pageerror', e => errores.push('pageerror: ' + e.message));
    await p2.goto(base);
    await p2.click('#t-inicio');
    await esperarLcd(p2, 0, t => t.trim().length > 0);
    await p2.screenshot({ path: path.join(CAPTURAS, 'escritorio_inicio_oscuro.png') });
    await p2.click('#t-braille');
    await p2.screenshot({ path: path.join(CAPTURAS, 'escritorio_braille_oscuro.png') });
    const ctx3 = await browser.newContext({ viewport: { width: 1280, height: 900 }, colorScheme: 'light' });
    const p3 = await ctx3.newPage();
    await p3.goto(base);
    await p3.click('#t-modos');
    await p3.waitForTimeout(600);
    await p3.screenshot({ path: path.join(CAPTURAS, 'escritorio_modos_claro.png'), fullPage: true });
    await p3.click('#t-progreso');
    await p3.waitForTimeout(600);
    await p3.screenshot({ path: path.join(CAPTURAS, 'escritorio_progreso_claro.png'), fullPage: true });

    comprobar(errores.length === 0, 'sin errores de JavaScript' + (errores.length ? ': ' + errores.join(' | ') : ''));
  } catch (e) {
    comprobar(false, 'la prueba se ha roto: ' + e.message);
  } finally {
    await browser.close();
    banco.stdin.end();
    banco.kill();
  }
  console.log('---\n' + (fallos ? 'HAY FALLOS (' + fallos + ')' : 'TODO OK') + '\ncapturas en ' + CAPTURAS);
  process.exit(fallos ? 1 : 0);
})();
