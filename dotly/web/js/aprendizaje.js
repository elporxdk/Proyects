/* =====================================================================
 *  DOTLY web · APRENDER (MODO PROFESOR)
 * =====================================================================
 *  Dos mitades:
 *
 *   1. LÓGICA DE APRENDIZAJE  (sin tocar la página)
 *      Las lecciones, cómo se elige la siguiente pregunta, cómo se corrige
 *      y el progreso guardado. Se puede probar aparte.
 *
 *   2. PANTALLA DEL MODO PROFESOR
 *      La lista de lecciones, las fichas de estudio, la práctica pregunta a
 *      pregunta, el resumen y la práctica a medida que prepara el profesor.
 *
 *  Dos clases de ejercicio:
 *    · FORMAR: se ve una letra ("B") y hay que marcar sus puntos en la celda.
 *    · LEER:   se ve una celda en braille y hay que elegir qué letra es.
 * ===================================================================== */
(function (raiz) {
  'use strict';
  const Dotly = raiz.Dotly = raiz.Dotly || {};
  const B = Dotly.Braille;

  // #####################################################################
  // 1. LÓGICA DE APRENDIZAJE
  // #####################################################################

  // ---- Catálogo: todo lo que se puede practicar, por identificador ----
  //  Letras, cifras y signos por su carácter ('b', '3', '?'); los tres
  //  signos indicadores por su nombre ('mayuscula', 'numero', 'minuscula').
  const CATALOGO = new Map();
  B.SIGNOS.forEach((s) => CATALOGO.set(s.car, {
    id: s.car, etiqueta: s.etiqueta, nombre: s.nombre, puntos: s.puntos, tipo: s.tipo,
  }));
  Object.keys(B.INDICADORES).forEach((k) => {
    const i = B.INDICADORES[k];
    CATALOGO.set(k, { id: k, etiqueta: i.etiqueta, nombre: i.nombre, puntos: i.puntos, tipo: 'indicador', explica: i.explica });
  });
  const item = (id) => CATALOGO.get(id);

  // ---- Lecciones, en el orden en que se suele enseñar ----
  const LECCIONES = [
    { id: 'serie1', titulo: 'Primera serie', rango: 'a – j', items: 'abcdefghij'.split(''),
      explica: 'Las diez primeras letras solo usan los puntos de arriba de la celda: 1, 2, 4 y 5. Son la base de todo lo demás.' },
    { id: 'serie2', titulo: 'Segunda serie', rango: 'k – t', items: 'klmnopqrst'.split(''),
      explica: 'Son las letras de la primera serie con el punto 3 añadido: la k es la a con el punto 3, la l es la b con el punto 3… hasta la t.' },
    { id: 'serie3', titulo: 'Tercera serie', rango: 'u v x y z', items: ['u', 'v', 'x', 'y', 'z'],
      explica: 'Las cinco primeras de la segunda serie con el punto 6 añadido: la u es la k con el 6, la v es la l con el 6… y la z es la o con el 6.' },
    { id: 'especiales', titulo: 'La ñ y la w', rango: 'ñ w', items: ['ñ', 'w'],
      explica: 'No siguen las series: la ñ es la g con el punto 6 y la w es la j con el punto 6.' },
    { id: 'tildes', titulo: 'Vocales con tilde', rango: 'á é í ó ú ü', items: ['á', 'é', 'í', 'ó', 'ú', 'ü'],
      explica: 'Cada vocal con tilde tiene su propio signo: no se forma añadiendo puntos a la vocal. La ü, también.' },
    { id: 'numeros', titulo: 'Los números', rango: '1 – 0', items: '1234567890'.split(''),
      explica: 'Un número se escribe con el signo de número (puntos 3-4-5-6) y las letras de la a a la j: la a es el 1, la b el 2… y la j el 0. El signo va una sola vez, delante de la primera cifra.' },
    { id: 'signos', titulo: 'Signos de puntuación', rango: '. , ; : ¿? ¡! " ( ) -', items: ['.', ',', ';', ':', '?', '!', '"', '(', ')', '-'],
      explica: 'Casi todos usan los puntos de abajo de la celda (2, 3, 5 y 6). ¿ y ? son el mismo signo, igual que ¡ y !.' },
    { id: 'indicadores', titulo: 'Mayúsculas, números y minúsculas', rango: 'signos que avisan', items: ['mayuscula', 'numero', 'minuscula'],
      explica: 'No son letras: avisan de cómo leer lo que viene detrás. Mayúscula (4-6): la letra siguiente es mayúscula, y dos seguidos ponen en mayúsculas la palabra entera. Número (3-4-5-6): la a–j pasan a ser cifras. Minúscula (punto 5): entre un número y una letra de la a a la j.' },
  ];

  // Cómo se forma cada letra a partir de otra (lo comprueban las pruebas)
  const PISTAS = {
    k: 'a + punto 3', l: 'b + punto 3', m: 'c + punto 3', n: 'd + punto 3', o: 'e + punto 3',
    p: 'f + punto 3', q: 'g + punto 3', r: 'h + punto 3', s: 'i + punto 3', t: 'j + punto 3',
    u: 'k + punto 6', v: 'l + punto 6', x: 'm + punto 6', y: 'n + punto 6', z: 'o + punto 6',
    'ñ': 'g + punto 6', w: 'j + punto 6',
    1: 'número + a', 2: 'número + b', 3: 'número + c', 4: 'número + d', 5: 'número + e',
    6: 'número + f', 7: 'número + g', 8: 'número + h', 9: 'número + i', 0: 'número + j',
  };

  // Cómo se nombra cada cosa en una frase
  function nombreLargo(it) {
    switch (it.tipo) {
      case 'letra': return 'letra ' + it.nombre;
      case 'tilde': return it.nombre;
      case 'numero': return 'número ' + it.nombre;
      case 'signo': return 'signo ' + it.nombre;
      default: return it.nombre;
    }
  }
  // Lo que se enseña en grande: la letra, o la palabra si es un indicador
  const esTexto = (it) => it.tipo === 'indicador';

  // ---- Progreso: por cada signo, intentos y aciertos a la primera ----
  const nuevoProgreso = () => ({ v: 1, signos: {}, lecciones: {} });
  let progreso = nuevoProgreso();
  let guardarProgreso = () => {};

  function tasa(id) {
    const st = progreso.signos[id];
    return st && st.i ? st.p / st.i : null;
  }
  function registrar(id, alaPrimera) {
    const st = progreso.signos[id] || (progreso.signos[id] = { i: 0, p: 0 });
    st.i++;
    if (alaPrimera) st.p++;
    guardarProgreso();
  }

  // ---- Elegir la siguiente pregunta ----
  //  Salen más las que más se fallan y las que aún no han salido; nunca la
  //  misma dos veces seguidas (si hay más de una).
  function elegir(ids, anterior, azar) {
    const r = azar || Math.random;
    const pesos = ids.map((id) => {
      if (ids.length > 1 && id === anterior) return 0;
      const t = tasa(id);
      return t === null ? 4 : 1 + Math.round((1 - t) * 6);
    });
    const total = pesos.reduce((a, b) => a + b, 0);
    let x = r() * total;
    for (let k = 0; k < ids.length; k++) {
      x -= pesos[k];
      if (x < 0) return ids[k];
    }
    return ids[ids.length - 1];
  }

  // ---- Opciones de un ejercicio de LEER ----
  //  La buena y tres falsas de la misma familia (letras con letras, cifras
  //  con cifras…), las que menos puntos cambian: obligan a fijarse bien.
  const familia = (it) => (it.tipo === 'tilde' ? 'letra' : it.tipo);
  function opciones(id, pool, azar) {
    const r = azar || Math.random;
    const it = item(id);
    const cand = new Set(pool.filter((x) => x !== id && familia(item(x)) === familia(it)));
    if (cand.size < 3) CATALOGO.forEach((x) => { if (x.id !== id && familia(x) === familia(it)) cand.add(x.id); });
    const orden = Array.from(cand)
      .map((x) => ({ x, d: B.distancia(item(x).puntos, it.puntos) + r() * 1.5 }))
      .sort((a, b) => a.d - b.d)
      .slice(0, 3)
      .map((o) => o.x);
    const todas = [id].concat(orden);
    for (let k = todas.length - 1; k > 0; k--) {
      const j = Math.floor(r() * (k + 1));
      [todas[k], todas[j]] = [todas[j], todas[k]];
    }
    return todas;
  }

  // ---- Corregir un ejercicio de FORMAR ----
  //  Dice qué puntos faltan y cuáles sobran, no solo «bien» o «mal».
  function evaluar(objetivo, puntos) {
    const faltan = [], sobran = [];
    for (let d = 0; d < 6; d++) {
      const bit = 1 << d;
      if ((objetivo & bit) && !(puntos & bit)) faltan.push(d + 1);
      if (!(objetivo & bit) && (puntos & bit)) sobran.push(d + 1);
    }
    return { ok: !faltan.length && !sobran.length, faltan, sobran };
  }

  // ---- Una sesión de práctica ----
  function nuevaSesion(cfg) {
    return {
      titulo: cfg.titulo, leccion: cfg.leccion || null, ids: cfg.ids.slice(),
      tipo: cfg.tipo || 'mixto', total: cfg.total || 10,
      n: 0, bien: 0, primera: 0, racha: 0, mejorRacha: 0, falladas: [], actual: null, anterior: null,
    };
  }
  function siguientePregunta(se, azar) {
    const r = azar || Math.random;
    const id = elegir(se.ids, se.anterior, r);
    const modo = se.tipo === 'mixto' ? (r() < 0.5 ? 'formar' : 'leer') : se.tipo;
    se.actual = { id, modo, intentos: 0, pista: false, resuelta: false, opciones: modo === 'leer' ? opciones(id, se.ids, r) : null };
    se.anterior = id;
    return se.actual;
  }
  // Cierra la pregunta: cuenta como «a la primera» solo sin fallos ni pistas
  function cerrarPregunta(se, acierto) {
    const a = se.actual;
    a.resuelta = true;
    const primera = acierto && a.intentos === 1 && !a.pista;
    se.n++;
    if (acierto) se.bien++;
    if (primera) {
      se.primera++;
      se.racha++;
      se.mejorRacha = Math.max(se.mejorRacha, se.racha);
    } else {
      se.racha = 0;
      if (!se.falladas.includes(a.id)) se.falladas.push(a.id);
    }
    registrar(a.id, primera);
  }
  function estrellas(proporcion) {
    return proporcion >= 0.9 ? 3 : proporcion >= 0.7 ? 2 : proporcion >= 0.5 ? 1 : 0;
  }
  function terminarSesion(se) {
    const p = se.total ? se.primera / se.total : 0;
    if (se.leccion) {
      const l = progreso.lecciones[se.leccion] || (progreso.lecciones[se.leccion] = { mejor: 0 });
      l.mejor = Math.max(l.mejor, p);
      guardarProgreso();
    }
    return { proporcion: p, estrellas: estrellas(p) };
  }

  // #####################################################################
  // 2. PANTALLA DEL MODO PROFESOR
  // #####################################################################
  let A = null;              // lo que presta la aplicación (avisos, voz, conexión…)
  let se = null;             // la sesión en curso
  let ultimaConfig = null;   // para «Repetir»
  let celdaRespuesta = null;
  let pantalla = 'inicio';   // inicio | estudio | sesion | resumen
  let leccionAbierta = null;

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));
  const esc = (t) => String(t).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const celdaHtml = (p, o) => Dotly.Celda.html(p, o);
  const corto = (it) => (it.tipo === 'indicador' ? { mayuscula: 'Mayús.', numero: 'Núm.', minuscula: 'Minús.' }[it.id] : it.etiqueta);
  const leccion = (id) => LECCIONES.find((l) => l.id === id);

  function htmlEstrellas(n, practicada) {
    if (!practicada) return '<span class="estrellas estrellas--vacia">Sin practicar</span>';
    return '<span class="estrellas" role="img" aria-label="Mejor resultado: ' + n + ' de 3 estrellas">' +
      '★'.repeat(n) + '☆'.repeat(3 - n) + '</span>';
  }

  function mostrar(cual, foco) {
    pantalla = cual;
    ['inicio', 'estudio', 'sesion', 'resumen'].forEach((p) => { $('#ap-' + p).hidden = p !== cual; });
    if (foco) {
      const f = $(foco);
      if (f) f.focus({ preventScroll: false });
    }
    if (cual !== 'sesion' && A) A.conexion.enviarCelda(0, { revelar: false });
  }

  // ---------- lista de lecciones ----------
  function pintarLecciones() {
    $('#lecciones').innerHTML = LECCIONES.map((l, k) => {
      const reg = progreso.lecciones[l.id];
      return '<li class="tarjeta leccion">' +
        '<div class="leccion__cab"><h3 id="lec-' + l.id + '">' + (k + 1) + '. ' + esc(l.titulo) +
        '<span class="leccion__rango">' + esc(l.rango) + '</span></h3>' +
        htmlEstrellas(reg ? estrellas(reg.mejor) : 0, !!reg) + '</div>' +
        '<p>' + esc(l.explica) + '</p>' +
        '<div class="leccion__signos" aria-hidden="true">' + l.items.map((id) => {
          const it = item(id);
          return '<span class="minisigno">' + celdaHtml(it.puntos, { tam: 'mini', etiqueta: false }) + '<span>' + esc(corto(it)) + '</span></span>';
        }).join('') + '</div>' +
        '<div class="botonera">' +
        '<button type="button" class="btn btn--secundario" data-estudiar="' + l.id + '" aria-describedby="lec-' + l.id + '">Estudiar</button>' +
        '<button type="button" class="btn btn--primario" data-practicar="' + l.id + '" aria-describedby="lec-' + l.id + '">Practicar</button>' +
        '</div></li>';
    }).join('');
  }

  // ---------- fichas de estudio ----------
  function abrirEstudio(id) {
    const l = leccion(id);
    leccionAbierta = l;
    $('#estudioTitulo').textContent = l.titulo + ' · ' + l.rango;
    $('#estudioExplica').textContent = l.explica;
    $('#fichas').innerHTML = l.items.map((iid) => {
      const it = item(iid);
      const celdas = it.tipo === 'numero'
        ? celdaHtml(B.INDICADORES.numero.puntos, { tam: 'lista', etiqueta: 'signo de número' }) + celdaHtml(it.puntos, { tam: 'lista' })
        : celdaHtml(it.puntos, { tam: 'lista' });
      return '<li class="ficha">' +
        '<span class="ficha__car' + (esTexto(it) ? ' ficha__car--texto' : '') + '" aria-hidden="true">' + esc(it.etiqueta) + '</span>' +
        '<span class="sr-only">' + esc(nombreLargo(it)) + '</span>' +
        '<span class="ficha__celdas">' + celdas + '</span>' +
        '<span class="ficha__puntos">Puntos ' + B.textoPuntos(it.puntos) + '</span>' +
        (PISTAS[iid] ? '<span class="ficha__pista">' + esc(PISTAS[iid]) + '</span>' : '') +
        (it.explica ? '<span class="ficha__pista">' + esc(it.explica) + '</span>' : '') +
        '<button type="button" class="btn btn--peq btn--secundario" data-probar="' + esc(iid) + '">Verla en la celda<span class="sr-only">: ' + esc(nombreLargo(it)) + '</span></button>' +
        '</li>';
    }).join('');
    mostrar('estudio', '#estudioTitulo');
  }

  // ---------- empezar una práctica ----------
  function empezar(cfg) {
    ultimaConfig = cfg;
    se = nuevaSesion(cfg);
    $('#sesionTitulo').textContent = cfg.titulo;
    $('#sesionBarra').setAttribute('aria-valuemax', String(se.total));
    mostrar('sesion');
    nuevaPregunta();
  }
  function practicarLeccion(id) {
    const l = leccion(id);
    empezar({ titulo: l.titulo, leccion: l.id, ids: l.items, tipo: 'mixto', total: l.items.length <= 3 ? 6 : 10 });
  }

  function pintarCuenta() {
    $('#sesionBarra').setAttribute('aria-valuenow', String(se.n));
    $('#sesionBarra span').style.width = (100 * se.n / se.total) + '%';
    $('#sesionCuenta').textContent = 'Pregunta ' + Math.min(se.n + 1, se.total) + ' de ' + se.total +
      ' · ' + se.primera + (se.primera === 1 ? ' acierto' : ' aciertos') + ' a la primera · racha ' + se.racha;
  }

  // ---------- una pregunta ----------
  function nuevaPregunta() {
    const q = siguientePregunta(se);
    const it = item(q.id);
    pintarCuenta();
    const retro = $('#retro');
    retro.textContent = '';
    retro.className = 'retro';
    $('#btnSiguientePregunta').hidden = true;
    const numero = it.tipo === 'numero';

    if (q.modo === 'formar') {
      // Se ve la letra; hay que marcar sus puntos
      $('#consigna').innerHTML = '<span class="sr-only">Pregunta ' + (se.n + 1) + ' de ' + se.total + '. </span>' +
        'Marca los puntos de<span class="sr-only">: ' + esc(nombreLargo(it)) + (numero ? ', detrás del signo de número' : '') + '</span>';
      $('#objetivo').innerHTML = '<span class="objetivo__car' + (esTexto(it) ? ' objetivo__car--texto' : '') + '">' + esc(it.etiqueta) + '</span>' +
        '<span class="objetivo__nombre">' + esc(nombreLargo(it)) + '</span>';
      $('#respuesta').innerHTML = '<div class="respuesta__celdas">' +
        (numero ? '<span class="celda-fija">' + celdaHtml(B.INDICADORES.numero.puntos, { tam: 'media', etiqueta: 'signo de número, ya puesto' }) + 'signo de número</span>' : '') +
        '<div id="celdaRespuesta"></div></div>';
      celdaRespuesta = new Dotly.Celda($('#celdaRespuesta'), {
        tam: 'grande',
        etiqueta: 'Celda de respuesta: marca los puntos',
        alCambiar: (p, origen) => {
          if (origen === 'usuario') A.vibrar(12);
          if (origen !== 'equipo') A.conexion.enviarCelda(p, { revelar: false });
        },
      });
      $('#btnComprobar').hidden = false;
      $('#btnPista').hidden = false;
      $('#btnBorrarRespuesta').hidden = false;
      A.hablar('Marca los puntos de: ' + nombreLargo(it));
      A.conexion.enviarCelda(0, { revelar: false });
    } else {
      // Se ve la celda; hay que elegir qué es
      celdaRespuesta = null;
      $('#consigna').innerHTML = '<span class="sr-only">Pregunta ' + (se.n + 1) + ' de ' + se.total + '. </span>' +
        (numero ? '¿Qué número es?' : it.tipo === 'indicador' ? '¿Qué signo es?' : it.tipo === 'signo' ? '¿Qué signo de puntuación es?' : '¿Qué letra es?') +
        '<span class="sr-only"> ' + (numero ? 'Signo de número y ' : '') + esc(B.describir(it.puntos)) + '.</span>';
      $('#objetivo').innerHTML = '<div class="objetivo__celdas">' +
        (numero ? celdaHtml(B.INDICADORES.numero.puntos, { tam: 'media', etiqueta: false }) : '') +
        celdaHtml(it.puntos, { tam: 'media', etiqueta: false }) + '</div>';
      $('#respuesta').innerHTML = '<div class="opciones" role="group" aria-label="Elige la respuesta">' +
        q.opciones.map((oid) => {
          const o = item(oid);
          return '<button type="button" class="opcion" data-opcion="' + esc(oid) + '">' +
            '<span class="opcion__car' + (esTexto(o) ? ' opcion__car--texto' : '') + '" aria-hidden="true">' + esc(o.etiqueta) + '</span>' +
            '<span class="opcion__nombre">' + esc(o.nombre) + '</span></button>';
        }).join('') + '</div>';
      $('#btnComprobar').hidden = true;
      $('#btnPista').hidden = true;
      $('#btnBorrarRespuesta').hidden = true;
      A.hablar((numero ? '¿Qué número es? Signo de número y ' : '¿Qué es? ') + B.describir(it.puntos));
      A.conexion.enviarCelda(it.puntos, { revelar: false });   // en una celda física se puede tocar
    }
    $('#consigna').focus();
  }

  function frasePuntos(lista) {
    return lista.length === 1 ? 'el punto ' + lista[0] : 'los puntos ' + lista.slice(0, -1).join(', ') + ' y ' + lista[lista.length - 1];
  }

  function comprobar() {
    const q = se && se.actual;
    if (!q || q.resuelta || q.modo !== 'formar') return;
    const it = item(q.id);
    q.intentos++;
    const r = evaluar(it.puntos, celdaRespuesta.puntos);
    const retro = $('#retro');
    if (r.ok) {
      celdaRespuesta.bloquear(true);
      cerrarPregunta(se, true);
      retro.className = 'retro retro--bien';
      retro.textContent = '¡Correcto! ' + capital(nombreLargo(it)) + ': ' + B.describir(it.puntos) + '.';
      A.hablar('¡Correcto!');
      A.conexion.enviarCelda(it.puntos, { caracter: caracterDe(it), revelar: true });
      terminarPregunta();
      return;
    }
    celdaRespuesta.marcarDiferencias(it.puntos);
    retro.className = 'retro retro--mal';
    const partes = [];
    if (r.faltan.length) partes.push('te falta ' + frasePuntos(r.faltan));
    if (r.sobran.length) partes.push('te sobra ' + frasePuntos(r.sobran));
    const frase = capital(partes.join(' y ')) + '.';
    if (q.intentos < 2) {
      retro.textContent = 'Casi. ' + frase + ' Corrígelo y vuelve a comprobar.';
      A.hablar('Casi. ' + frase);
      A.vibrar([30, 60, 30]);
      return;
    }
    // Segundo fallo: se enseña la respuesta
    cerrarPregunta(se, false);
    celdaRespuesta.limpiarMarcas();
    celdaRespuesta.poner(it.puntos, 'programa');
    celdaRespuesta.bloquear(true);
    retro.textContent = frase + ' La respuesta era ' + B.describir(it.puntos) + ': ya está en la celda.';
    A.hablar('La respuesta era ' + B.describir(it.puntos));
    A.conexion.enviarCelda(it.puntos, { caracter: caracterDe(it), revelar: true });
    terminarPregunta();
  }

  function elegirOpcion(oid) {
    const q = se && se.actual;
    if (!q || q.resuelta || q.modo !== 'leer') return;
    const it = item(q.id), o = item(oid);
    q.intentos = 1;
    const ok = oid === q.id;
    $$('.opcion').forEach((b) => {
      b.setAttribute('aria-disabled', 'true');
      if (b.dataset.opcion === q.id) b.classList.add('opcion--bien');
      else if (b.dataset.opcion === oid) b.classList.add('opcion--mal');
    });
    cerrarPregunta(se, ok);
    const retro = $('#retro');
    retro.className = 'retro ' + (ok ? 'retro--bien' : 'retro--mal');
    retro.textContent = ok
      ? '¡Correcto! ' + capital(B.describir(it.puntos)) + ': ' + nombreLargo(it) + '.'
      : 'No: ' + B.describir(it.puntos) + ' es ' + nombreLargo(it) + '. Elegiste ' + nombreLargo(o) + '.';
    A.hablar(ok ? '¡Correcto!' : 'No. Era ' + nombreLargo(it));
    if (!ok) A.vibrar([30, 60, 30]);
    terminarPregunta();
  }

  function terminarPregunta() {
    pintarCuenta();
    $('#btnComprobar').hidden = true;
    $('#btnPista').hidden = true;
    $('#btnBorrarRespuesta').hidden = true;
    const sig = $('#btnSiguientePregunta');
    sig.textContent = se.n >= se.total ? 'Ver el resultado' : 'Siguiente';
    sig.hidden = false;
    sig.focus();
  }

  function siguiente() {
    if (!se || !se.actual || !se.actual.resuelta) return;
    if (se.n >= se.total) { resumen(); return; }
    nuevaPregunta();
  }

  function pista() {
    const q = se && se.actual;
    if (!q || q.resuelta || q.modo !== 'formar') return;
    const it = item(q.id);
    const r = evaluar(it.puntos, celdaRespuesta.puntos);
    q.pista = true;
    const retro = $('#retro');
    retro.className = 'retro';
    if (r.faltan.length) {
      celdaRespuesta.marcarPista(r.faltan[0]);
      retro.textContent = 'Pista: marca el punto ' + r.faltan[0] + '. En total lleva ' + B.lista(it.puntos).length + '.';
    } else if (r.sobran.length) {
      retro.textContent = 'Pista: tienes todos los que hacen falta, pero sobra alguno.';
    } else {
      retro.textContent = 'Ya está bien: pulsa Comprobar.';
    }
    A.hablar(retro.textContent);
  }

  function resumen() {
    const r = terminarSesion(se);
    $('#resumenEstrellas').innerHTML = '<span role="img" aria-label="' + r.estrellas + ' de 3 estrellas">' +
      '★'.repeat(r.estrellas) + '☆'.repeat(3 - r.estrellas) + '</span>';
    $('#resumenCifra').textContent = se.primera + ' de ' + se.total + ' a la primera' +
      (se.mejorRacha > 1 ? ' · mejor racha: ' + se.mejorRacha : '');
    $('#resumenRepasar').textContent = se.falladas.length
      ? 'Para repasar: ' + se.falladas.map((id) => item(id).etiqueta).join(', ') + '.'
      : '¡Sin fallos! Prueba con la lección siguiente.';
    $('#resumenFalladas').hidden = !se.falladas.length;
    $('#resumenTitulo').textContent = r.estrellas === 3 ? '¡Muy bien!' : r.estrellas >= 1 ? '¡Bien hecho!' : 'Práctica terminada';
    A.hablar($('#resumenTitulo').textContent + ' ' + $('#resumenCifra').textContent);
    pintarLecciones();
    mostrar('resumen', '#resumenTitulo');
  }

  const capital = (t) => t.charAt(0).toUpperCase() + t.slice(1);
  const caracterDe = (it) => (it.tipo === 'indicador' ? '' : it.id);

  // ---------- práctica a medida (profesor) ----------
  const prof = { ids: new Set(LECCIONES[0].items), tipo: 'mixto', total: 10 };

  function pintarProfesor() {
    $('#profGrupos').innerHTML =
      '<button type="button" class="chip chip--grupo" data-prof="todo">Todos</button>' +
      '<button type="button" class="chip chip--grupo" data-prof="nada">Ninguno</button>';
    $('#profSignos').innerHTML = LECCIONES.map((l) =>
      '<div class="prof-grupo" role="group" aria-labelledby="pg-' + l.id + '">' +
      '<span class="prof-grupo__nombre" id="pg-' + l.id + '">' + esc(l.titulo) + '</span>' +
      '<button type="button" class="chip chip--grupo" data-prof-grupo="' + l.id + '" aria-pressed="false">Todos</button>' +
      l.items.map((id) => {
        const it = item(id);
        return '<button type="button" class="chip" data-prof-id="' + esc(id) + '" aria-pressed="false" aria-label="' + esc(nombreLargo(it)) + '">' + esc(corto(it)) + '</button>';
      }).join('') + '</div>').join('');
    $$('input[name="profTipo"]').forEach((r) => { r.checked = r.value === prof.tipo; });
    $$('input[name="profTotal"]').forEach((r) => { r.checked = +r.value === prof.total; });
    marcarProfesor();
  }

  function marcarProfesor() {
    $$('[data-prof-id]').forEach((b) => b.setAttribute('aria-pressed', prof.ids.has(b.dataset.profId) ? 'true' : 'false'));
    $$('[data-prof-grupo]').forEach((b) => {
      const l = leccion(b.dataset.profGrupo);
      b.setAttribute('aria-pressed', l.items.every((id) => prof.ids.has(id)) ? 'true' : 'false');
      b.setAttribute('aria-label', 'Todos los de ' + l.titulo);
    });
    const n = prof.ids.size;
    $('#profCuenta').textContent = n ? n + (n === 1 ? ' signo elegido' : ' signos elegidos') : 'Elige al menos un signo';
    A.memoria.guardar('profesor', { ids: Array.from(prof.ids), tipo: prof.tipo, total: prof.total });
  }

  // ---------- enlaces con la página ----------
  function enlazar() {
    $('#lecciones').addEventListener('click', (e) => {
      const b = e.target.closest('button');
      if (!b) return;
      if (b.dataset.estudiar) abrirEstudio(b.dataset.estudiar);
      if (b.dataset.practicar) practicarLeccion(b.dataset.practicar);
    });
    $('#estudioVolver').addEventListener('click', () => mostrar('inicio', '#t-aprender'));
    $('#estudioPracticar').addEventListener('click', () => practicarLeccion(leccionAbierta.id));
    $('#fichas').addEventListener('click', (e) => {
      const b = e.target.closest('[data-probar]');
      if (b) A.verEnExplorar(item(b.dataset.probar).puntos);
    });

    $('#respuesta').addEventListener('click', (e) => {
      const b = e.target.closest('[data-opcion]');
      if (b) elegirOpcion(b.dataset.opcion);
    });
    $('#btnComprobar').addEventListener('click', comprobar);
    $('#btnPista').addEventListener('click', pista);
    $('#btnBorrarRespuesta').addEventListener('click', () => {
      if (celdaRespuesta && !celdaRespuesta.bloqueada) { celdaRespuesta.limpiarMarcas(); celdaRespuesta.poner(0, 'usuario'); }
    });
    $('#btnSiguientePregunta').addEventListener('click', siguiente);
    $('#sesionSalir').addEventListener('click', () => { se = null; celdaRespuesta = null; mostrar('inicio', '#t-aprender'); });

    $('#resumenRepetir').addEventListener('click', () => empezar(ultimaConfig));
    $('#resumenFalladas').addEventListener('click', () => {
      const ids = se.falladas.slice();
      empezar({ titulo: 'Repaso de las falladas', ids, tipo: ultimaConfig.tipo, total: Math.max(5, ids.length * 2) });
    });
    $('#resumenVolver').addEventListener('click', () => mostrar('inicio', '#t-aprender'));

    $('#formProfesor').addEventListener('click', (e) => {
      const b = e.target.closest('button[type="button"]');
      if (!b) return;
      if (b.dataset.prof === 'todo') CATALOGO.forEach((it) => prof.ids.add(it.id));
      if (b.dataset.prof === 'nada') prof.ids.clear();
      if (b.dataset.profGrupo) {
        const l = leccion(b.dataset.profGrupo);
        const todos = l.items.every((id) => prof.ids.has(id));
        l.items.forEach((id) => (todos ? prof.ids.delete(id) : prof.ids.add(id)));
      }
      if (b.dataset.profId) {
        const id = b.dataset.profId;
        if (prof.ids.has(id)) prof.ids.delete(id); else prof.ids.add(id);
      }
      marcarProfesor();
    });
    $$('input[name="profTipo"]').forEach((r) => r.addEventListener('change', () => { prof.tipo = r.value; marcarProfesor(); }));
    $$('input[name="profTotal"]').forEach((r) => r.addEventListener('change', () => { prof.total = +r.value; marcarProfesor(); }));
    $('#formProfesor').addEventListener('submit', (e) => {
      e.preventDefault();
      if (!prof.ids.size) { A.avisar('Elige al menos un signo para la práctica'); return; }
      // En el orden del catálogo, para que la práctica sea estable
      const ids = Array.from(CATALOGO.keys()).filter((id) => prof.ids.has(id));
      empezar({ titulo: 'Práctica a medida', ids, tipo: prof.tipo, total: prof.total });
    });
  }

  // #####################################################################
  // Lo que usa la aplicación
  // #####################################################################
  Dotly.Aprendizaje = {
    // lógica (también para las pruebas)
    LECCIONES, CATALOGO, PISTAS, item, elegir, opciones, evaluar, nuevaSesion, siguientePregunta, cerrarPregunta, estrellas,
    get progreso() { return progreso; },

    // Monta la pantalla. 'app' trae: memoria, conexion, hablar, vibrar, avisar, verEnExplorar
    montar(app) {
      A = app;
      const guardado = A.memoria.leer('progreso', null);
      progreso = Object.assign(nuevoProgreso(), guardado && guardado.v === 1 ? guardado : {});
      guardarProgreso = () => A.memoria.guardar('progreso', progreso);
      const p = A.memoria.leer('profesor', null);
      if (p && Array.isArray(p.ids)) {
        prof.ids = new Set(p.ids.filter((id) => CATALOGO.has(id)));
        if (['formar', 'leer', 'mixto'].includes(p.tipo)) prof.tipo = p.tipo;
        if ([5, 10, 20].includes(p.total)) prof.total = p.total;
      }
      pintarLecciones();
      pintarProfesor();
      enlazar();
    },
    borrarProgreso() {
      progreso = nuevoProgreso();
      guardarProgreso();
      pintarLecciones();
    },
    // La celda que manejan el teclado y el ESP32 en esta pantalla (o null)
    celdaActiva() {
      return pantalla === 'sesion' && se && se.actual && se.actual.modo === 'formar' && !se.actual.resuelta ? celdaRespuesta : null;
    },
    // Intro, o «aceptar» en el aparato: comprobar o pasar a la siguiente
    accionPrincipal() {
      if (pantalla !== 'sesion' || !se || !se.actual) return false;
      if (se.actual.resuelta) siguiente(); else if (se.actual.modo === 'formar') comprobar(); else return false;
      return true;
    },
    // Botones del aparato
    recibirTecla(t) {
      if (pantalla !== 'sesion' || !se || !se.actual) return;
      if (t === 'aceptar') this.accionPrincipal();
      else if (t === 'siguiente' && se.actual.resuelta) siguiente();
      else if (t === 'borrar' && this.celdaActiva()) celdaRespuesta.poner(0, 'equipo');
    },
    // Al volver a conectar: que el aparato enseñe la celda del ejercicio
    sincronizar() {
      if (pantalla !== 'sesion' || !se || !se.actual) { A.conexion.enviarCelda(0, { revelar: false }); return; }
      const it = item(se.actual.id);
      if (se.actual.resuelta) A.conexion.enviarCelda(it.puntos, { caracter: caracterDe(it), revelar: true });
      else if (se.actual.modo === 'leer') A.conexion.enviarCelda(it.puntos, { revelar: false });
      else A.conexion.enviarCelda(celdaRespuesta ? celdaRespuesta.puntos : 0, { revelar: false });
    },
    enSesion: () => pantalla === 'sesion',
  };
})(typeof window !== 'undefined' ? window : globalThis);
