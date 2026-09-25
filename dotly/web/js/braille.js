/* =====================================================================
 *  DOTLY web · MOTOR BRAILLE (signografía española)
 * =====================================================================
 *  Todo lo que sabe la web del braille está aquí: la tabla de signos, el
 *  traductor de texto a celdas y la lectura inversa (de puntos a signo).
 *  No toca la página, así que también funciona en Node (lo usan las pruebas).
 *
 *  Los puntos de una celda se guardan en un número de 6 bits:
 *
 *      1 ● ● 4        punto 1 = bit 0     punto 4 = bit 3
 *      2 ● ● 5        punto 2 = bit 1     punto 5 = bit 4
 *      3 ● ● 6        punto 3 = bit 2     punto 6 = bit 5
 *
 *  Es la misma numeración que usa Unicode: el carácter braille de una celda
 *  es U+2800 + ese número. Y es la misma que usa el firmware de DOTLY, así
 *  que una celda se puede mandar al ESP32 tal cual.
 * ===================================================================== */
(function (raiz) {
  'use strict';

  // "125" -> máscara con los puntos 1, 2 y 5
  function mascara(puntos) {
    let m = 0;
    for (const d of String(puntos)) {
      const n = d.charCodeAt(0) - 48;
      if (n >= 1 && n <= 6) m |= 1 << (n - 1);
    }
    return m;
  }

  // ---------------------------------------------------------------------
  // La tabla. [signo, puntos, nombre para leerlo en voz alta]
  // ---------------------------------------------------------------------
  const LETRAS = [
    ['a', '1', 'a'], ['b', '12', 'be'], ['c', '14', 'ce'], ['d', '145', 'de'],
    ['e', '15', 'e'], ['f', '124', 'efe'], ['g', '1245', 'ge'], ['h', '125', 'hache'],
    ['i', '24', 'i'], ['j', '245', 'jota'], ['k', '13', 'ka'], ['l', '123', 'ele'],
    ['m', '134', 'eme'], ['n', '1345', 'ene'], ['ñ', '12456', 'eñe'], ['o', '135', 'o'],
    ['p', '1234', 'pe'], ['q', '12345', 'cu'], ['r', '1235', 'erre'], ['s', '234', 'ese'],
    ['t', '2345', 'te'], ['u', '136', 'u'], ['v', '1236', 'uve'], ['w', '2456', 'uve doble'],
    ['x', '1346', 'equis'], ['y', '13456', 'ye'], ['z', '1356', 'zeta'],
  ];
  const TILDES = [
    ['á', '12356', 'a con tilde'], ['é', '2346', 'e con tilde'], ['í', '34', 'i con tilde'],
    ['ó', '346', 'o con tilde'], ['ú', '23456', 'u con tilde'], ['ü', '1256', 'u con diéresis'],
  ];
  // Las cifras son las letras de la a a la j detrás del signo de número.
  const NUMEROS = [
    ['1', '1', 'uno'], ['2', '12', 'dos'], ['3', '14', 'tres'], ['4', '145', 'cuatro'],
    ['5', '15', 'cinco'], ['6', '124', 'seis'], ['7', '1245', 'siete'], ['8', '125', 'ocho'],
    ['9', '24', 'nueve'], ['0', '245', 'cero'],
  ];
  // En braille, ¿ y ? son el mismo signo; igual que ¡ y !.
  const SIGNOS_PUNTUACION = [
    ['.', '3', 'punto'], [',', '2', 'coma'], [';', '23', 'punto y coma'],
    [':', '25', 'dos puntos'], ['¿?', '26', 'interrogación'], ['¡!', '235', 'exclamación'],
    ['"', '236', 'comillas'], ['(', '126', 'abre paréntesis'], [')', '345', 'cierra paréntesis'],
    ['-', '36', 'guion'],
  ];

  // Signos que no son letras: avisan de cómo leer lo que viene detrás.
  const INDICADORES = {
    mayuscula:  { puntos: mascara('46'),   etiqueta: 'Mayúscula', nombre: 'signo de mayúscula',
                  explica: 'La letra que va detrás es mayúscula. Dos seguidos: toda la palabra en mayúsculas.' },
    numero:     { puntos: mascara('3456'), etiqueta: 'Número',    nombre: 'signo de número',
                  explica: 'Lo que va detrás son cifras: la a es 1, la b es 2… y la j es 0.' },
    minuscula:  { puntos: mascara('5'),    etiqueta: 'Minúscula', nombre: 'signo de minúscula',
                  explica: 'Tras un número, indica que lo siguiente es una letra de la a a la j y no otra cifra.' },
  };

  function crear(lista, tipo) {
    return lista.map(([car, puntos, nombre]) => ({
      car: car.length > 1 ? car.slice(-1) : car,  // '¿?' -> '?'
      etiqueta: car.length > 1 ? car.split('').join(' ') : car.toUpperCase(),
      puntos: mascara(puntos),
      tipo,                                        // 'letra' | 'tilde' | 'numero' | 'signo'
      nombre,
    }));
  }

  const SIGNOS = [].concat(
    crear(LETRAS, 'letra'),
    crear(TILDES, 'tilde'),
    crear(NUMEROS, 'numero'),
    crear(SIGNOS_PUNTUACION, 'signo'),
  );

  // Busca por el carácter tal cual se escribe (en minúscula)
  const POR_CARACTER = new Map();
  SIGNOS.forEach((s) => POR_CARACTER.set(s.car, s));
  POR_CARACTER.set('¿', POR_CARACTER.get('?'));
  POR_CARACTER.set('¡', POR_CARACTER.get('!'));
  ['«', '»', '“', '”'].forEach((c) => POR_CARACTER.set(c, POR_CARACTER.get('"')));

  // Busca por los puntos: primero letras y signos; las cifras aparte, porque
  // comparten puntos con la a–j y solo lo son detrás del signo de número.
  const POR_PUNTOS = new Map();
  const CIFRA_POR_PUNTOS = new Map();
  SIGNOS.forEach((s) => {
    if (s.tipo === 'numero') CIFRA_POR_PUNTOS.set(s.puntos, s);
    else if (!POR_PUNTOS.has(s.puntos)) POR_PUNTOS.set(s.puntos, s);
  });

  const LETRAS_A_J = 'abcdefghij';
  const esCifra = (c) => c >= '0' && c <= '9';
  const minuscula = (c) => c.toLocaleLowerCase('es');
  const esMayuscula = (c) => c !== minuscula(c);

  // El signo de un carácter, o null si no tiene braille en esta tabla.
  function buscar(c) {
    return POR_CARACTER.get(minuscula(c)) || null;
  }
  const esLetra = (c) => {
    const s = buscar(c);
    return !!s && (s.tipo === 'letra' || s.tipo === 'tilde');
  };

  // ---------------------------------------------------------------------
  // TRADUCTOR: texto -> celdas
  // ---------------------------------------------------------------------
  //  Reglas de la signografía española que se aplican:
  //   · Mayúscula: el signo 4-6 delante de la letra. Si la palabra entera va
  //     en mayúsculas (dos letras o más), el signo se pone dos veces delante
  //     de la palabra y no se repite en cada letra.
  //   · Números: el signo 3-4-5-6 una sola vez al principio. La coma decimal
  //     (punto 2) y el punto de los millares (punto 3) no lo cortan: 3,5 y
  //     1.000 llevan un solo signo de número.
  //   · Una letra de la a a la j pegada a un número se leería como otra cifra:
  //     delante lleva el signo de minúscula (punto 5). «2b» = 3456 12 5 12.
  //
  //  Cada celda devuelta: { puntos, car, tipo }
  //    tipo: letra | tilde | numero | signo | espacio | salto |
  //          mayuscula | numero-signo | minuscula | desconocido
  //    (un carácter desconocido lleva puntos = null: no tiene braille aquí)
  function traducir(texto) {
    const cs = Array.from(String(texto || ''));
    const out = [];
    let enNumero = false;
    let mayusculasHasta = -1;          // índice hasta el que llega una palabra en mayúsculas

    for (let i = 0; i < cs.length; i++) {
      const c = cs[i];

      if (c === '\n') { out.push({ puntos: 0, car: '\n', tipo: 'salto' }); enNumero = false; continue; }
      if (/\s/.test(c)) { out.push({ puntos: 0, car: ' ', tipo: 'espacio' }); enNumero = false; continue; }

      if (esCifra(c)) {
        if (!enNumero) {
          out.push({ puntos: INDICADORES.numero.puntos, car: '', tipo: 'numero-signo' });
          enNumero = true;
        }
        out.push({ puntos: POR_CARACTER.get(c).puntos, car: c, tipo: 'numero' });
        continue;
      }

      // coma decimal o punto de millares entre dos cifras: el número sigue
      if (enNumero && (c === ',' || c === '.') && i + 1 < cs.length && esCifra(cs[i + 1])) {
        out.push({ puntos: POR_CARACTER.get(c).puntos, car: c, tipo: 'signo' });
        continue;
      }

      const s = buscar(c);
      if (!s) { out.push({ puntos: null, car: c, tipo: 'desconocido' }); enNumero = false; continue; }

      if (s.tipo === 'letra' || s.tipo === 'tilde') {
        const mayus = esMayuscula(c);
        if (enNumero && !mayus && s.tipo === 'letra' && LETRAS_A_J.includes(s.car)) {
          out.push({ puntos: INDICADORES.minuscula.puntos, car: '', tipo: 'minuscula' });
        }
        enNumero = false;

        // ¿empieza aquí una palabra entera en mayúsculas?
        if (mayus && i > mayusculasHasta && (i === 0 || !esLetra(cs[i - 1]))) {
          let j = i;
          while (j < cs.length && esLetra(cs[j])) j++;
          const palabra = cs.slice(i, j);
          if (palabra.length >= 2 && palabra.every(esMayuscula)) {
            out.push({ puntos: INDICADORES.mayuscula.puntos, car: '', tipo: 'mayusculas' });
            out.push({ puntos: INDICADORES.mayuscula.puntos, car: '', tipo: 'mayusculas' });
            mayusculasHasta = j - 1;
          }
        }
        if (mayus && i > mayusculasHasta) {
          out.push({ puntos: INDICADORES.mayuscula.puntos, car: '', tipo: 'mayuscula' });
        }
        out.push({ puntos: s.puntos, car: c, tipo: s.tipo });
        continue;
      }

      enNumero = false;
      out.push({ puntos: s.puntos, car: c, tipo: 'signo' });
    }
    return out;
  }

  // ---------------------------------------------------------------------
  // LECTURA: puntos -> qué signo son
  // ---------------------------------------------------------------------
  //  Devuelve { tipo, car, etiqueta, nombre, cifra } o null si esos puntos no
  //  son nada en la signografía básica. 'cifra' es la cifra que serían detrás
  //  del signo de número (solo en la a–j).
  function leer(puntos, opciones) {
    const m = puntos & 0x3F;
    const numero = !!(opciones && opciones.numero);
    if (m === 0) return { tipo: 'espacio', car: ' ', etiqueta: '', nombre: 'espacio' };
    for (const clave of Object.keys(INDICADORES)) {
      const ind = INDICADORES[clave];
      if (ind.puntos === m) return { tipo: 'indicador', indicador: clave, car: '', etiqueta: ind.etiqueta, nombre: ind.nombre, explica: ind.explica };
    }
    const cifra = CIFRA_POR_PUNTOS.get(m) || null;
    if (numero && cifra) return Object.assign({}, cifra, { cifra: null });
    const s = POR_PUNTOS.get(m);
    if (!s) return null;
    return Object.assign({}, s, { cifra: cifra ? cifra.car : null });
  }

  // ---------------------------------------------------------------------
  // Utilidades
  // ---------------------------------------------------------------------
  const unicode = (m) => String.fromCharCode(0x2800 + (m & 0x3F));

  // Las celdas de traducir() en braille Unicode (lo desconocido se omite)
  function aUnicode(celdas) {
    return celdas.map((c) => (c.tipo === 'salto' ? '\n' : c.puntos === null ? '' : unicode(c.puntos))).join('');
  }

  function lista(m) {
    const l = [];
    for (let d = 0; d < 6; d++) if (m & (1 << d)) l.push(d + 1);
    return l;
  }
  // 19 -> "1-2-5"
  const textoPuntos = (m) => lista(m).join('-');
  // 19 -> "puntos 1, 2 y 5"  (para leerlo en voz alta o con un lector de pantalla)
  function describir(m) {
    const l = lista(m);
    if (!l.length) return 'ningún punto';
    if (l.length === 1) return 'punto ' + l[0];
    return 'puntos ' + l.slice(0, -1).join(', ') + ' y ' + l[l.length - 1];
  }
  // cuántos puntos cambian entre dos celdas
  function distancia(a, b) {
    let x = (a ^ b) & 0x3F, n = 0;
    while (x) { n += x & 1; x >>= 1; }
    return n;
  }

  const api = {
    SIGNOS, INDICADORES, LETRAS_A_J,
    mascara, buscar, traducir, leer, unicode, aUnicode, lista, textoPuntos, describir, distancia,
    // el abecedario español en su orden (con la ñ después de la n)
    ALFABETO: SIGNOS.filter((s) => s.tipo === 'letra'),
  };

  raiz.Dotly = raiz.Dotly || {};
  raiz.Dotly.Braille = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
