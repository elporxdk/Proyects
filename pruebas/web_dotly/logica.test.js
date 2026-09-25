// =====================================================================
//  Pruebas de la lógica de la web de DOTLY (sin navegador):
//    node --test pruebas/web_dotly/logica.test.js
//  El motor braille se contrasta con una tabla escrita aquí APARTE, por
//  puntos, igual que hace el banco del firmware.
// =====================================================================
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('path');

const WEB = path.resolve(__dirname, '../../dotly/web/js');
const B = require(path.join(WEB, 'braille.js'));
require(path.join(WEB, 'aprendizaje.js'));          // se cuelga de globalThis.Dotly
const A = globalThis.Dotly.Aprendizaje;

const p = (s) => [...s].reduce((m, d) => m | (1 << (d - 1)), 0);    // "125" -> máscara

// La signografía española, escrita a mano y por puntos
const ONCE = {
  a: '1', b: '12', c: '14', d: '145', e: '15', f: '124', g: '1245', h: '125', i: '24', j: '245',
  k: '13', l: '123', m: '134', n: '1345', 'ñ': '12456', o: '135', p: '1234', q: '12345', r: '1235',
  s: '234', t: '2345', u: '136', v: '1236', w: '2456', x: '1346', y: '13456', z: '1356',
  'á': '12356', 'é': '2346', 'í': '34', 'ó': '346', 'ú': '23456', 'ü': '1256',
  1: '1', 2: '12', 3: '14', 4: '145', 5: '15', 6: '124', 7: '1245', 8: '125', 9: '24', 0: '245',
  '.': '3', ',': '2', ';': '23', ':': '25', '?': '26', '!': '235', '"': '236', '(': '126', ')': '345', '-': '36',
};
const MAYUS = p('46'), NUM = p('3456'), MINUS = p('5');
const celdas = (t) => B.traducir(t).map((c) => c.puntos);

test('cada signo de la tabla tiene los puntos de la signografía española', () => {
  assert.equal(B.SIGNOS.length, Object.keys(ONCE).length);
  for (const s of B.SIGNOS) assert.equal(s.puntos, p(ONCE[s.car]), 'signo ' + s.car);
});

test('ninguna combinación significa dos cosas (salvo cifras y a-j)', () => {
  const vistos = new Map();
  for (const s of B.SIGNOS.filter((x) => x.tipo !== 'numero')) {
    assert.ok(!vistos.has(s.puntos), s.car + ' repite los puntos de ' + vistos.get(s.puntos));
    vistos.set(s.puntos, s.car);
  }
  for (const k of Object.keys(B.INDICADORES)) assert.ok(!vistos.has(B.INDICADORES[k].puntos), 'indicador ' + k);
});

test('traductor: letras, mayúsculas y palabras en mayúsculas', () => {
  assert.deepEqual(celdas('hola'), [p('125'), p('135'), p('123'), p('1')]);
  assert.deepEqual(celdas('Hola'), [MAYUS, p('125'), p('135'), p('123'), p('1')]);
  assert.deepEqual(celdas('A'), [MAYUS, p('1')], 'una sola letra: un signo');
  assert.deepEqual(celdas('DOTLY'), [MAYUS, MAYUS, p('145'), p('135'), p('2345'), p('123'), p('13456')]);
  assert.deepEqual(celdas('ÁRBOL'), [MAYUS, MAYUS, p('12356'), p('1235'), p('12'), p('135'), p('123')]);
  assert.deepEqual(celdas('McD'), [MAYUS, p('134'), p('14'), MAYUS, p('145')], 'mezcla: una a una');
  assert.deepEqual(celdas('ÑU'), [MAYUS, MAYUS, p('12456'), p('136')]);
});

test('traductor: números, coma decimal, millares y signo de minúscula', () => {
  assert.deepEqual(celdas('12'), [NUM, p('1'), p('12')]);
  assert.deepEqual(celdas('2b'), [NUM, p('12'), MINUS, p('12')], '«2b» = 3456 12 5 12');
  assert.deepEqual(celdas('2k'), [NUM, p('12'), p('13')], 'la k no se confunde con una cifra');
  assert.deepEqual(celdas('2B'), [NUM, p('12'), MAYUS, p('12')], 'la mayúscula ya lo distingue');
  assert.deepEqual(celdas('3,5'), [NUM, p('14'), p('2'), p('15')], 'coma decimal: un solo signo de número');
  assert.deepEqual(celdas('1.000'), [NUM, p('1'), p('3'), p('245'), p('245'), p('245')]);
  assert.deepEqual(celdas('3, 5'), [NUM, p('14'), p('2'), 0, NUM, p('15')], 'con espacio son dos números');
  assert.deepEqual(celdas('1-2'), [NUM, p('1'), p('36'), NUM, p('12')]);
  assert.deepEqual(celdas('3.'), [NUM, p('14'), p('3')], 'punto final de frase');
});

test('traductor: signos, espacios, saltos y lo desconocido', () => {
  assert.deepEqual(celdas('¿Qué?'), [p('26'), MAYUS, p('12345'), p('136'), p('2346'), p('26')]);
  assert.deepEqual(celdas('¡sí!'), [p('235'), p('234'), p('34'), p('235')]);
  assert.deepEqual(celdas('«a»'), [p('236'), p('1'), p('236')]);
  const t = B.traducir('a b\nc@');
  assert.deepEqual(t.map((c) => c.tipo), ['letra', 'espacio', 'letra', 'salto', 'letra', 'desconocido']);
  assert.equal(t[5].puntos, null);
  assert.equal(B.aUnicode(t), '⠁⠀⠃\n⠉', 'lo desconocido no sale en braille');
  assert.equal(B.aUnicode(B.traducir('Hola 2b')), '⠨⠓⠕⠇⠁⠀⠼⠃⠐⠃');
});

test('lectura: de puntos a signo', () => {
  assert.equal(B.leer(p('12')).car, 'b');
  assert.equal(B.leer(p('12')).cifra, '2');
  assert.equal(B.leer(p('12'), { numero: true }).car, '2');
  assert.equal(B.leer(p('13')).cifra, null, 'la k no es ninguna cifra');
  assert.equal(B.leer(MAYUS).indicador, 'mayuscula');
  assert.equal(B.leer(NUM).indicador, 'numero');
  assert.equal(B.leer(MINUS).indicador, 'minuscula');
  assert.equal(B.leer(p('26')).etiqueta, '¿ ?');
  assert.equal(B.leer(0).tipo, 'espacio');
  assert.equal(B.leer(p('4')), null, 'el punto 4 solo no es nada');
});

test('textos para leer en voz alta', () => {
  assert.equal(B.describir(p('125')), 'puntos 1, 2 y 5');
  assert.equal(B.describir(p('1')), 'punto 1');
  assert.equal(B.describir(0), 'ningún punto');
  assert.equal(B.textoPuntos(p('3456')), '3-4-5-6');
  assert.equal(B.unicode(p('1')), '⠁');
});

test('lecciones: cubren todo el catálogo y sus pistas son ciertas', () => {
  const enLecciones = new Set(A.LECCIONES.flatMap((l) => l.items));
  for (const id of A.CATALOGO.keys()) assert.ok(enLecciones.has(id), id + ' no está en ninguna lección');
  for (const id of enLecciones) assert.ok(A.CATALOGO.has(id), id + ' no existe');
  for (const [id, pista] of Object.entries(A.PISTAS)) {
    const obj = A.item(String(id)).puntos;
    let m = /^(\S+) \+ punto (\d)$/.exec(pista);
    if (m) { assert.equal(A.item(m[1]).puntos | (1 << (m[2] - 1)), obj, id + ': ' + pista); continue; }
    m = /^número \+ (\S)$/.exec(pista);
    assert.ok(m, 'pista rara: ' + pista);
    assert.equal(A.item(m[1]).puntos, obj, id + ': ' + pista);
  }
});

test('corrección de FORMAR: qué falta y qué sobra', () => {
  assert.deepEqual(A.evaluar(p('125'), p('125')), { ok: true, faltan: [], sobran: [] });
  assert.deepEqual(A.evaluar(p('125'), p('13')), { ok: false, faltan: [2, 5], sobran: [3] });
  assert.deepEqual(A.evaluar(p('1'), 0), { ok: false, faltan: [1], sobran: [] });
});

test('opciones de LEER: cuatro distintas, de la misma familia y con la buena', () => {
  for (let k = 0; k < 200; k++) {
    const letras = A.LECCIONES[0].items;
    const id = letras[k % letras.length];
    const o = A.opciones(id, letras);
    assert.equal(o.length, 4);
    assert.equal(new Set(o).size, 4);
    assert.ok(o.includes(id));
    o.forEach((x) => assert.ok(['letra', 'tilde'].includes(A.item(x).tipo)));
  }
  const n = A.opciones('3', ['3']);
  assert.ok(n.every((x) => A.item(x).tipo === 'numero'), 'con una sola cifra, las falsas son cifras');
  assert.equal(A.opciones('numero', ['numero']).length, 3, 'solo hay tres indicadores');
});

test('elegir: nunca repite seguida y saca más las que se fallan', () => {
  const ids = ['a', 'b', 'c'];
  for (let k = 0; k < 300; k++) assert.notEqual(A.elegir(ids, 'b'), 'b');
  A.progreso.signos = { a: { i: 20, p: 20 }, b: { i: 20, p: 0 }, c: { i: 20, p: 20 } };
  const cuenta = { a: 0, b: 0, c: 0 };
  for (let k = 0; k < 3000; k++) cuenta[A.elegir(ids, null)]++;
  assert.ok(cuenta.b > cuenta.a * 3, 'la fallada sale mucho más: ' + JSON.stringify(cuenta));
  A.progreso.signos = {};
});

test('una sesión: aciertos a la primera, rachas y estrellas', () => {
  const se = A.nuevaSesion({ titulo: 'x', ids: ['a', 'b'], tipo: 'formar', total: 4 });
  for (let k = 0; k < 4; k++) {
    const q = A.siguientePregunta(se);
    assert.equal(q.modo, 'formar');
    q.intentos = k === 2 ? 2 : 1;              // la tercera, al segundo intento
    A.cerrarPregunta(se, true);
  }
  assert.equal(se.n, 4);
  assert.equal(se.bien, 4);
  assert.equal(se.primera, 3);
  assert.equal(se.mejorRacha, 2);
  assert.equal(se.falladas.length, 1);
  assert.equal(A.estrellas(1), 3);
  assert.equal(A.estrellas(0.75), 2);
  assert.equal(A.estrellas(0.5), 1);
  assert.equal(A.estrellas(0.2), 0);
});

test('la dirección del ESP32 se interpreta y se valida', () => {
  require(path.join(WEB, 'conexion.js'));
  const C = globalThis.Dotly.Conexion;
  const d = C.interpretar('192.168.4.1');
  assert.equal(d.http, 'http://192.168.4.1');
  assert.deepEqual(d.ws, ['ws://192.168.4.1:81/', 'ws://192.168.4.1/ws']);
  assert.equal(C.interpretar('http://DOTLY.local:8080/api').http, 'http://dotly.local:8080');
  assert.deepEqual(C.interpretar('10.0.0.5', { puertoWs: 9000, rutaWs: 'ws' }).ws, ['ws://10.0.0.5:9000/ws']);
  for (const mal of ['', 'hola mundo', '1.2.3.4:99999', '<script>', 'a..b', 'http://', '192.168.4.1:0']) {
    assert.equal(C.interpretar(mal), null, JSON.stringify(mal));
  }
  assert.equal(C.interpretar('192.168.4.1', { rutaWs: '/a b' }), null, 'ruta con espacios');
});
