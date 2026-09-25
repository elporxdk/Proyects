/* =====================================================================
 *  DOTLY web · APLICACIÓN
 * =====================================================================
 *  Une las piezas: el motor braille (braille.js), la celda (celda.js),
 *  el aprendizaje (aprendizaje.js) y la conexión con el ESP32
 *  (conexion.js). Aquí están las pantallas Explorar, Traductor y Conexión,
 *  los ajustes, la navegación y los atajos de teclado.
 * ===================================================================== */
(function () {
  'use strict';
  const D = window.Dotly;
  const B = D.Braille, Celda = D.Celda, Conexion = D.Conexion, Aprendizaje = D.Aprendizaje;
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const esc = (t) => String(t).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const capital = (t) => t.charAt(0).toUpperCase() + t.slice(1);

  // ---------------------------------------------------------------------
  // Memoria del navegador. Puede no haberla (modo privado, algunos file://):
  // entonces la web funciona igual, solo que no recuerda nada.
  // ---------------------------------------------------------------------
  const memoria = {
    leer(clave, porDefecto) {
      try {
        const v = localStorage.getItem('dotly.web.' + clave);
        return v === null ? porDefecto : JSON.parse(v);
      } catch (e) { return porDefecto; }
    },
    guardar(clave, valor) {
      try { localStorage.setItem('dotly.web.' + clave, JSON.stringify(valor)); } catch (e) { /* sin memoria */ }
    },
  };

  // ---------------------------------------------------------------------
  // Ajustes: colores, tamaño, números en los puntos, voz y vibración
  // ---------------------------------------------------------------------
  const ajustes = Object.assign({ tema: 'auto', escala: 1, numeros: true, voz: false, vibrar: true }, memoria.leer('ajustes', {}));
  const oscuroSistema = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;

  function aplicarAjustes() {
    const html = document.documentElement;
    if (['claro', 'oscuro', 'amarillo'].includes(ajustes.tema)) html.dataset.tema = ajustes.tema;
    else delete html.dataset.tema;
    const escala = [1, 1.15, 1.3, 1.5].includes(+ajustes.escala) ? +ajustes.escala : 1;
    html.style.setProperty('--escala', String(escala));
    document.body.classList.toggle('sin-numeros', !ajustes.numeros);
    const oscuro = ajustes.tema === 'oscuro' || (ajustes.tema === 'auto' && oscuroSistema && oscuroSistema.matches);
    $('meta[name="theme-color"]').setAttribute('content', ajustes.tema === 'amarillo' ? '#000000' : oscuro ? '#111a2e' : '#ffffff');
  }
  function guardarAjustes() { memoria.guardar('ajustes', ajustes); aplicarAjustes(); }

  // ---------------------------------------------------------------------
  // Avisos: para el lector de pantalla, en voz alta, vibración y en pantalla
  // ---------------------------------------------------------------------
  function anunciar(texto, urgente) {
    const el = $(urgente ? '#anuncioUrgente' : '#anuncio');
    el.textContent = '';
    setTimeout(() => { el.textContent = texto; }, 60);    // así se lee aunque se repita
  }

  let voz = null;
  function elegirVoz() {
    const voces = window.speechSynthesis ? speechSynthesis.getVoices() : [];
    voz = voces.find((v) => /^es\b/i.test(v.lang) && v.localService) || voces.find((v) => /^es\b/i.test(v.lang)) || null;
  }
  if (window.speechSynthesis) {
    elegirVoz();
    speechSynthesis.onvoiceschanged = elegirVoz;
  }
  function hablar(texto) {
    if (!ajustes.voz || !window.speechSynthesis || !texto) return;
    try {
      speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(texto);
      u.lang = 'es-ES';
      if (voz) u.voice = voz;
      speechSynthesis.speak(u);
    } catch (e) { /* sin voz */ }
  }
  function vibrar(patron) {
    if (!ajustes.vibrar || !navigator.vibrate) return;
    try { navigator.vibrate(patron); } catch (e) { /* sin vibración */ }
  }

  let tTostada = null;
  function avisar(texto) {
    const t = $('#tostada');
    t.textContent = texto;
    t.classList.add('ver');
    clearTimeout(tTostada);
    tTostada = setTimeout(() => t.classList.remove('ver'), 3200);
    anunciar(texto);
  }

  // ---------------------------------------------------------------------
  // Navegación: cada pantalla es un #ancla (el botón «atrás» funciona)
  // ---------------------------------------------------------------------
  const VISTAS = { explorar: 'Explorar', traductor: 'Traductor', aprender: 'Aprender', conexion: 'Conexión' };
  let vista = null;

  function mostrarVista(nombre, conFoco) {
    if (!VISTAS[nombre]) nombre = 'explorar';
    if (nombre === vista) return;
    vista = nombre;
    Object.keys(VISTAS).forEach((v) => { $('#vista-' + v).hidden = v !== nombre; });
    $$('.menu__item').forEach((a) => {
      if (a.dataset.vista === nombre) a.setAttribute('aria-current', 'page');
      else a.removeAttribute('aria-current');
    });
    document.title = VISTAS[nombre] + ' · DOTLY';
    if (conFoco) {
      // El foco va al título de la pantalla: el lector de pantalla lo anuncia
      const titulo = $$('#vista-' + nombre + ' [tabindex="-1"]').find((el) => el.offsetParent !== null);
      if (titulo) titulo.focus();
      window.scrollTo(0, 0);
    }
    sincronizarEquipo();
  }
  window.addEventListener('hashchange', () => {
    const h = location.hash.slice(1);
    if (VISTAS[h]) mostrarVista(h, true);
  });

  // ---------------------------------------------------------------------
  // EXPLORAR: la celda interactiva y su lectura en tiempo real
  // ---------------------------------------------------------------------
  // Anterior / Siguiente: el abecedario en su orden (con la ñ tras la n), luego
  // tildes, cifras, signos y los tres indicadores
  const ORDEN = B.SIGNOS.map((s) => s.car).concat(Object.keys(B.INDICADORES));
  let idxSigno = -1;
  let tLectura = null;

  const celdaExplorar = new Celda($('#celdaExplorar'), {
    tam: 'grande',
    etiqueta: 'Celda braille de seis puntos: toca los puntos',
    alCambiar(p, origen) {
      pintarLectura(p);
      if (origen === 'inicio') return;                  // la celda de la última vez: sin avisos
      memoria.guardar('celda', p);
      if (origen === 'usuario') { vibrar(12); idxSigno = -1; }
      // → ESP32: la celda que se ve y lo que significa (si hay conexión)
      if (origen !== 'equipo') Conexion.enviarCelda(p, { caracter: caracterDe(p), revelar: true });
      clearTimeout(tLectura);
      if (origen === 'equipo' && vista !== 'explorar') return;   // ya lo avisa el panel
      tLectura = setTimeout(() => {
        const l = lecturaDe(p);
        const frase = (origen === 'equipo' ? 'El equipo ha marcado ' : '') + B.describir(p) + ': ' + l.que + '.';
        anunciar(capital(frase));
        hablar(frase);
      }, 350);
    },
  });

  // Qué significa una celda, para enseñarlo
  function lecturaDe(p) {
    if (p === 0) return { car: '·', que: 'ningún punto', extra: 'Una celda vacía es un espacio en blanco.' };
    const l = B.leer(p);
    if (!l) return { car: '?', que: 'sin significado', extra: 'Esa combinación no es ningún signo de la signografía básica.' };
    if (l.tipo === 'indicador') return { car: l.etiqueta, texto: true, que: l.nombre, extra: l.explica };
    const que = l.tipo === 'letra' ? 'letra ' + l.nombre : l.tipo === 'tilde' ? l.nombre : 'signo: ' + l.nombre;
    let extra = '';
    if (l.cifra) extra = 'Detrás del signo de número es el ' + l.cifra + '.';
    else if (l.etiqueta.includes(' ')) extra = 'Es el mismo signo para abrir y para cerrar.';
    return { car: l.etiqueta, que, extra };
  }
  // El carácter que se manda al ESP32 con la celda (solo letras y signos)
  function caracterDe(p) {
    const l = B.leer(p);
    return l && (l.tipo === 'letra' || l.tipo === 'tilde' || l.tipo === 'signo') ? l.car : '';
  }

  function pintarLectura(p) {
    const l = lecturaDe(p);
    const car = $('#lecturaCar');
    car.textContent = l.car;
    car.classList.toggle('lectura__car--texto', !!l.texto);
    $('#lecturaQue').textContent = capital(l.que);
    $('#lecturaExtra').textContent = l.extra;
    $('#lecturaPuntos').textContent = p ? 'Puntos ' + B.textoPuntos(p) : 'Ningún punto';
    $('#lecturaUni').textContent = B.unicode(p);
    $$('#alfabeto .signo').forEach((b) => b.setAttribute('aria-current', +b.dataset.puntos === p && p ? 'true' : 'false'));
  }

  function pintarAlfabeto() {
    $('#alfabeto').innerHTML = Aprendizaje.LECCIONES.map((l) =>
      '<h3 id="alf-' + l.id + '">' + esc(l.titulo) + (l.rango.length <= 12 ? ' <span class="nota">(' + esc(l.rango) + ')</span>' : '') + '</h3>' +
      '<div class="signos" role="group" aria-labelledby="alf-' + l.id + '">' + l.items.map((id) => {
        const it = Aprendizaje.item(id);
        const nombre = it.tipo === 'numero' ? it.nombre + ', con el signo de número' : it.nombre;
        return '<button type="button" class="signo" data-id="' + esc(id) + '" data-puntos="' + it.puntos + '" ' +
          'aria-label="' + esc(nombre + ': ' + B.describir(it.puntos)) + '">' +
          '<span class="signo__car' + (it.tipo === 'indicador' ? ' signo__car--texto' : '') + '" aria-hidden="true">' + esc(it.etiqueta) + '</span>' +
          Celda.html(it.puntos, { tam: 'mini', etiqueta: false }) +
          '<span class="signo__pts" aria-hidden="true">' + B.textoPuntos(it.puntos) + '</span></button>';
      }).join('') + '</div>').join('');
  }

  function ponerSigno(id) {
    idxSigno = ORDEN.indexOf(id);
    celdaExplorar.limpiarMarcas();
    celdaExplorar.poner(Aprendizaje.item(id).puntos, 'programa');
  }
  function irSigno(delta) {
    if (idxSigno < 0) {
      const k = ORDEN.findIndex((id) => Aprendizaje.item(id).puntos === celdaExplorar.puntos);
      idxSigno = k >= 0 ? k : (delta > 0 ? -1 : 0);
    }
    ponerSigno(ORDEN[(idxSigno + delta + ORDEN.length) % ORDEN.length]);
  }

  function anadirAlTraductor() {
    const p = celdaExplorar.puntos;
    const l = B.leer(p);
    let c = null;
    if (p === 0) c = ' ';
    else if (l && (l.tipo === 'letra' || l.tipo === 'tilde' || l.tipo === 'signo')) c = l.car;
    if (c === null) {
      avisar(l && l.tipo === 'indicador'
        ? 'El ' + l.nombre + ' no es una letra: en el traductor escribe directamente la mayúscula o el número.'
        : 'Esa combinación no es ninguna letra.');
      return;
    }
    const t = $('#texto');
    if (Array.from(t.value).length >= 500) { avisar('El texto del traductor está lleno'); return; }
    t.value += c;
    traducirVista();
    memoria.guardar('texto', t.value);
    avisar(c === ' ' ? 'Añadido un espacio al traductor' : 'Añadida la «' + c + '» al traductor');
  }

  // ---------------------------------------------------------------------
  // TRADUCTOR: texto -> braille, mientras se escribe
  // ---------------------------------------------------------------------
  const texto = $('#texto');
  let tEnvioTexto = null;

  function traducirVista() {
    const celdas = B.traducir(texto.value);
    const desconocidos = new Set();
    let html = '', palabra = '', n = 0;
    const cerrarPalabra = () => { if (palabra) { html += '<span class="palabra">' + palabra + '</span>'; palabra = ''; } };
    const INDICADOR = { mayuscula: '⇧', mayusculas: '⇧', 'numero-signo': '#', minuscula: 'mín' };
    for (const c of celdas) {
      if (c.tipo === 'salto') { cerrarPalabra(); html += '<span class="salto"></span>'; continue; }
      if (c.tipo === 'espacio') { cerrarPalabra(); n++; continue; }
      if (c.tipo === 'desconocido') {
        desconocidos.add(c.car);
        palabra += '<span class="tc tc--desconocido">' + Celda.html(0, { tam: 'lista', etiqueta: false }) +
          '<span class="tc__car">' + esc(c.car) + '</span></span>';
        continue;
      }
      n++;
      const ind = INDICADOR[c.tipo];
      palabra += '<span class="tc' + (ind ? ' tc--indicador' : '') + '">' + Celda.html(c.puntos, { tam: 'lista', etiqueta: false }) +
        '<span class="tc__car">' + esc(ind || c.car) + '</span></span>';
    }
    cerrarPalabra();
    $('#salida').innerHTML = html || '<p class="salida__vacia">Aquí aparecerá el braille.</p>';
    $('#unicode').value = B.aUnicode(celdas);
    $('#cuentaCeldas').textContent = n + (n === 1 ? ' celda' : ' celdas');
    $('#contador').textContent = Array.from(texto.value).length + ' de 500 caracteres';
    const aviso = $('#avisoDesconocidos');
    aviso.hidden = !desconocidos.size;
    if (desconocidos.size) aviso.textContent = 'Sin braille en esta tabla, no se traducen: ' + Array.from(desconocidos).join(' ');
  }

  // → ESP32: el texto, medio segundo después de dejar de escribir
  function programarEnvioTexto() {
    clearTimeout(tEnvioTexto);
    if (!Conexion.activa) return;
    tEnvioTexto = setTimeout(() => Conexion.enviarTexto(texto.value), 500);
  }

  async function copiarBraille() {
    const u = $('#unicode');
    if (!u.value) { avisar('No hay nada que copiar'); return; }
    try {
      await navigator.clipboard.writeText(u.value);
      avisar('Braille copiado');
    } catch (e) {
      // Sin permiso para el portapapeles (por ejemplo, en http): se selecciona
      u.focus();
      u.select();
      let ok = false;
      try { ok = document.execCommand('copy'); } catch (e2) { ok = false; }
      avisar(ok ? 'Braille copiado' : 'Texto seleccionado: cópialo con Ctrl+C o con el menú');
    }
  }

  // ---------------------------------------------------------------------
  // CONEXIÓN: el panel. La lógica está en conexion.js
  // ---------------------------------------------------------------------
  const cfgConexion = Object.assign({ ip: '', transporte: 'auto', puertoWs: 81, rutaWs: '/', intervalo: 800 }, memoria.leer('conexion', {}));
  const TITULO_ESTADO = {
    local: 'Sin equipo', conectando: 'Conectando…', conectado: 'Conectado', 'solo-envio': 'Conectado solo para enviar',
    reconectando: 'Reconectando…', error: 'No se pudo conectar',
  };
  const PILDORA = {
    local: 'Sin equipo', conectando: 'Conectando…', conectado: 'Conectado', 'solo-envio': 'Solo envío',
    reconectando: 'Reconectando…', error: 'Sin conexión',
  };
  let estadoAnterior = 'local';

  // Si esta página la sirve el propio ESP32, su dirección ya es la buena
  function direccionSugerida() {
    const h = location.hostname;
    if (location.protocol !== 'http:' || !h) return '';
    if (/^(192\.168\.|10\.|172\.(1[6-9]|2\d|3[01])\.)/.test(h) || /\.local$/.test(h)) return h + (location.port ? ':' + location.port : '');
    return '';
  }

  function prepararPanel() {
    $('#ip').value = cfgConexion.ip || direccionSugerida();
    $$('input[name="transporte"]').forEach((r) => { r.checked = r.value === cfgConexion.transporte; });
    $('#puertoWs').value = cfgConexion.puertoWs;
    $('#rutaWs').value = cfgConexion.rutaWs;
    $('#intervalo').value = cfgConexion.intervalo;
    $('#avisoHttps').hidden = location.protocol !== 'https:';
  }

  async function conectar() {
    const ip = $('#ip').value.trim();
    const transporte = ($('input[name="transporte"]:checked') || {}).value || 'auto';
    const opciones = {
      transporte,
      puertoWs: Math.min(65535, Math.max(1, parseInt($('#puertoWs').value, 10) || 81)),
      rutaWs: $('#rutaWs').value.trim() || '/',
      intervalo: Math.min(10000, Math.max(200, parseInt($('#intervalo').value, 10) || 800)),
    };
    const campo = $('#ip');
    if (!Conexion.interpretar(ip, opciones)) {
      campo.setAttribute('aria-invalid', 'true');
      campo.focus();
      avisar(ip ? 'Esa dirección no es válida. Escribe algo como 192.168.4.1' : 'Escribe la dirección IP del ESP32');
      return;
    }
    campo.removeAttribute('aria-invalid');
    Object.assign(cfgConexion, { ip }, opciones);
    memoria.guardar('conexion', cfgConexion);
    const boton = $('#btnConectar');
    boton.setAttribute('aria-busy', 'true');
    boton.disabled = true;
    try { await Conexion.conectar(ip, opciones); }
    catch (e) { avisar(e.message); }
    finally { boton.removeAttribute('aria-busy'); boton.disabled = false; }
  }

  function pintarEstado(i) {
    const conectadoYa = i.estado === 'conectado' || i.estado === 'solo-envio' || i.estado === 'reconectando';
    $('#tarjetaEstado').dataset.estado = i.estado;
    $('#estadoTitulo').textContent = TITULO_ESTADO[i.estado] + (i.estado === 'conectado' && i.equipo ? ' a ' + i.equipo.nombre : '');
    $('#estadoDetalle').textContent = i.detalle;
    const p = $('#pildora');
    p.dataset.estado = i.estado;
    $('.pildora__texto', p).textContent = PILDORA[i.estado];
    p.setAttribute('aria-label', 'Conexión: ' + TITULO_ESTADO[i.estado] + '. Ir a la pantalla de conexión');
    // en el móvil: una marca con símbolo (no solo color) sobre «Conexión»
    const item = $('.menu__item[data-vista="conexion"]');
    item.dataset.estado = i.estado;
    $('.menu__insignia', item).textContent = { conectado: '✓', 'solo-envio': '↑', conectando: '…', reconectando: '…', error: '!' }[i.estado] || '';
    $('#menuEstado').textContent = i.estado === 'local' ? '' : ' (' + PILDORA[i.estado] + ')';
    $('#btnConectar').hidden = conectadoYa;
    $('#btnDesconectar').hidden = !conectadoYa;
    $('#btnPrueba').hidden = !conectadoYa;
    $('#btnEnviarTexto').hidden = !conectadoYa;
    if (!conectadoYa) $('#tarjetaPantalla').hidden = true;
    if (i.estado !== estadoAnterior) {
      // el lector de pantalla oye cada cambio de estado (el de «conectando» ya lo dice el botón)
      if (i.estado !== 'conectando') anunciar(TITULO_ESTADO[i.estado] + '. ' + i.detalle);
      if ((i.estado === 'conectado' || i.estado === 'solo-envio') && estadoAnterior !== 'conectado' && estadoAnterior !== 'solo-envio') {
        sincronizarEquipo();
      }
      estadoAnterior = i.estado;
    }
  }

  // Lo que se está viendo, al aparato (al conectar y al cambiar de pantalla)
  function sincronizarEquipo() {
    if (!Conexion.activa) return;
    if (vista === 'explorar') Conexion.enviarCelda(celdaExplorar.puntos, { caracter: caracterDe(celdaExplorar.puntos), revelar: true });
    else if (vista === 'traductor') Conexion.enviarTexto(texto.value);
    else if (vista === 'aprender') Aprendizaje.sincronizar();
  }

  // ← ESP32: puntos marcados en el aparato
  Conexion.on('celda', ({ puntos }) => {
    if (vista === 'aprender') {
      const c = Aprendizaje.celdaActiva();
      if (c) c.poner(puntos, 'equipo');
      return;
    }
    celdaExplorar.limpiarMarcas();
    celdaExplorar.poner(puntos, 'equipo');
    if (vista !== 'explorar') avisar('El equipo ha marcado ' + B.describir(puntos) + ': ' + lecturaDe(puntos).que);
  });
  // ← ESP32: sus botones
  Conexion.on('tecla', ({ tecla }) => {
    if (vista === 'aprender') { Aprendizaje.recibirTecla(tecla); return; }
    if (vista !== 'explorar') return;
    if (tecla === 'aceptar') anadirAlTraductor();
    else if (tecla === 'siguiente') irSigno(1);
    else if (tecla === 'anterior') irSigno(-1);
    else if (tecla === 'borrar') celdaExplorar.poner(0, 'equipo');
  });
  Conexion.on('estado', pintarEstado);
  Conexion.on('pantalla', (lineas) => {
    $('#tarjetaPantalla').hidden = false;
    $('#pantallaEquipo').textContent = lineas.join('\n');
  });
  Conexion.on('registro', (r) => {
    const ol = $('#registro');
    const vacio = $('.registro__vacio', ol);
    if (vacio) vacio.remove();
    const li = document.createElement('li');
    const t = document.createElement('time');
    t.dateTime = r.hora.toISOString();
    t.textContent = r.hora.toLocaleTimeString('es', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    li.append(t, document.createTextNode(r.dir + ' ' + r.texto));
    ol.prepend(li);
    while (ol.children.length > 60) ol.lastChild.remove();
  });
  function vaciarRegistro() {
    $('#registro').innerHTML = '<li class="registro__vacio">Aún no hay mensajes.</li>';
  }

  // ---------------------------------------------------------------------
  // Teclado: 1-6 y F D S J K L (Perkins) para los puntos, en cualquier
  // pantalla con una celda activa; Intro para comprobar en la práctica.
  // ---------------------------------------------------------------------
  const PERKINS = { f: 1, d: 2, s: 3, j: 4, k: 5, l: 6 };
  const pulsadas = new Set();
  let acorde = 0;

  function celdaDelTeclado() {
    if (vista === 'explorar') return celdaExplorar;
    if (vista === 'aprender') return Aprendizaje.celdaActiva();
    return null;
  }
  function escribiendo(e) {
    if (e.ctrlKey || e.metaKey || e.altKey) return true;
    const t = e.target;
    return !!(t && t.closest && t.closest('input, textarea, select, [contenteditable="true"], dialog'));
  }
  document.addEventListener('keydown', (e) => {
    if (escribiendo(e)) return;
    const celda = celdaDelTeclado();
    const k = e.key.toLowerCase();
    if (celda && /^[1-6]$/.test(e.key)) {
      e.preventDefault();
      if (!e.repeat) celda.alternar(+e.key, 'usuario');
      return;
    }
    if (celda && Object.prototype.hasOwnProperty.call(PERKINS, k)) {
      e.preventDefault();
      if (!e.repeat) { pulsadas.add(k); acorde |= 1 << (PERKINS[k] - 1); }
      return;
    }
    if (celda && (e.key === 'Backspace' || e.key === 'Delete')) {
      e.preventDefault();
      celda.limpiarMarcas();
      celda.poner(0, 'usuario');
      return;
    }
    if (e.key === 'Enter' && vista === 'aprender' && !(e.target.closest && e.target.closest('button, a, summary'))) {
      if (Aprendizaje.accionPrincipal()) e.preventDefault();
    }
  });
  document.addEventListener('keyup', (e) => {
    const k = e.key.toLowerCase();
    if (!pulsadas.has(k)) return;
    pulsadas.delete(k);
    if (pulsadas.size || !acorde) return;
    const p = acorde;
    acorde = 0;
    const celda = celdaDelTeclado();
    if (celda && !celda.bloqueada) {
      celda.limpiarMarcas();
      celda.poner(p, 'usuario');
    }
  });
  window.addEventListener('blur', () => { pulsadas.clear(); acorde = 0; });

  // ---------------------------------------------------------------------
  // Arranque
  // ---------------------------------------------------------------------
  aplicarAjustes();
  if (oscuroSistema && oscuroSistema.addEventListener) oscuroSistema.addEventListener('change', aplicarAjustes);

  // El logo: «DOTLY» en braille
  $$('.mini-dots').forEach((el) => { el.outerHTML = Celda.html(+el.dataset.puntos, { tam: 'diminuta', etiqueta: false }); });

  // Explorar
  pintarAlfabeto();
  celdaExplorar.poner(memoria.leer('celda', 0) & 0x3F, 'inicio');
  pintarLectura(celdaExplorar.puntos);
  $('#alfabeto').addEventListener('click', (e) => {
    const b = e.target.closest('.signo');
    if (!b) return;
    ponerSigno(b.dataset.id);
    if (window.innerWidth < 960) $('#celdaExplorar').scrollIntoView({ block: 'center', behavior: 'smooth' });
  });
  $('#btnAnterior').addEventListener('click', () => irSigno(-1));
  $('#btnSiguienteSigno').addEventListener('click', () => irSigno(1));
  $('#btnLimpiar').addEventListener('click', () => { celdaExplorar.limpiarMarcas(); celdaExplorar.poner(0, 'usuario'); });
  $('#btnAlTexto').addEventListener('click', anadirAlTraductor);

  // Traductor
  texto.value = memoria.leer('texto', 'Hola, DOTLY.');
  traducirVista();
  texto.addEventListener('input', () => { traducirVista(); memoria.guardar('texto', texto.value); programarEnvioTexto(); });
  $$('[data-ejemplo]').forEach((b) => b.addEventListener('click', () => {
    texto.value = b.dataset.ejemplo;
    texto.dispatchEvent(new Event('input'));
    texto.focus();
  }));
  $('#btnBorrarTexto').addEventListener('click', () => { texto.value = ''; texto.dispatchEvent(new Event('input')); texto.focus(); });
  $('#btnCopiar').addEventListener('click', copiarBraille);
  $('#btnEnviarTexto').addEventListener('click', () => { Conexion.enviarTexto(texto.value); avisar('Texto enviado al equipo'); });

  // Aprender (modo profesor)
  Aprendizaje.montar({
    memoria, conexion: Conexion, hablar, vibrar, avisar,
    verEnExplorar(p) {
      mostrarVista('explorar', true);
      if (location.hash !== '#explorar') location.hash = '#explorar';
      idxSigno = -1;
      celdaExplorar.limpiarMarcas();
      celdaExplorar.poner(p, 'programa');
    },
  });

  // Conexión
  prepararPanel();
  vaciarRegistro();
  $('#formConexion').addEventListener('submit', (e) => { e.preventDefault(); conectar(); });
  $('#btnDesconectar').addEventListener('click', () => Conexion.desconectar());
  $('#btnPrueba').addEventListener('click', () => { Conexion.enviarTexto('DOTLY'); avisar('Prueba enviada: «DOTLY»'); });
  $('#btnVaciarRegistro').addEventListener('click', vaciarRegistro);
  $('#ip').addEventListener('input', () => $('#ip').removeAttribute('aria-invalid'));

  // Ajustes
  const dlg = $('#dlgAjustes');
  function pintarFormAjustes() {
    $$('input[name="tema"]').forEach((r) => { r.checked = r.value === ajustes.tema; });
    $$('input[name="escala"]').forEach((r) => { r.checked = +r.value === +ajustes.escala; });
    $('#ajNumeros').checked = !!ajustes.numeros;
    $('#ajVoz').checked = !!ajustes.voz;
    $('#ajVibrar').checked = !!ajustes.vibrar;
  }
  $('#btnAjustes').addEventListener('click', () => {
    pintarFormAjustes();
    if (dlg.showModal) dlg.showModal(); else dlg.setAttribute('open', '');
  });
  $$('input[name="tema"]').forEach((r) => r.addEventListener('change', () => { ajustes.tema = r.value; guardarAjustes(); }));
  $$('input[name="escala"]').forEach((r) => r.addEventListener('change', () => { ajustes.escala = +r.value; guardarAjustes(); }));
  $('#ajNumeros').addEventListener('change', (e) => { ajustes.numeros = e.target.checked; guardarAjustes(); });
  $('#ajVibrar').addEventListener('change', (e) => { ajustes.vibrar = e.target.checked; guardarAjustes(); vibrar(30); });
  $('#ajVoz').addEventListener('change', (e) => {
    ajustes.voz = e.target.checked;
    guardarAjustes();
    if (ajustes.voz) {
      if (window.speechSynthesis) hablar('Voz activada');
      else avisar('Este navegador no puede leer en voz alta');
    }
  });
  $('#btnBorrarProgreso').addEventListener('click', () => {
    if (!window.confirm('¿Borrar todo el progreso de las lecciones? No se puede deshacer.')) return;
    Aprendizaje.borrarProgreso();
    avisar('Progreso borrado');
  });

  // Primera pantalla: la del #ancla, o Explorar
  const inicial = location.hash.slice(1);
  mostrarVista(VISTAS[inicial] ? inicial : 'explorar', false);
})();
