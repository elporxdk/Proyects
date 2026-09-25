/* =====================================================================
 *  DOTLY web · CONEXIÓN CON EL HARDWARE (ESP32 / DOTLY)
 * =====================================================================
 *  La web funciona sola. Este módulo solo entra en juego al pulsar
 *  «Conectar»: mientras no haya conexión, enviarCelda() y enviarTexto() no
 *  hacen nada y la página sigue funcionando en local.
 *
 *  Dos caminos para hablar con el ESP32, con el mismo protocolo:
 *
 *   1. WEBSOCKET  ws://IP:81/          (o ws://IP/ws)
 *      Tiempo real y en los dos sentidos. Es el preferido. Además los
 *      navegadores no le aplican CORS, así que funciona aunque la página
 *      no la sirva el propio ESP32.
 *
 *   2. HTTP       http://IP/api/...
 *      Peticiones para enviar y una consulta periódica (/api/estado) para
 *      recibir. Es lo que ya tiene el firmware actual de DOTLY.
 *
 *  Al conectar se prueban los dos a la vez y se queda el mejor que conteste.
 *
 *  ---------------------------------------------------------------------
 *  PROTOCOLO (mensajes JSON)
 *  ---------------------------------------------------------------------
 *   Web -> ESP32
 *     {"tipo":"hola","cliente":"dotly-web","protocolo":1}   al abrir el WebSocket
 *     {"tipo":"celda","puntos":19,"caracter":"h"}            la celda que se ve en la web
 *         puntos: 0-63 (bit 0 = punto 1 … bit 5 = punto 6)
 *         caracter: lo que significa, o "" si no hay que desvelarlo
 *         (en un ejercicio de «leer la celda» no se manda la respuesta)
 *     {"tipo":"texto","texto":"Hola"}                        el texto del traductor
 *     {"tipo":"ping"}                                        cada 15 s (se puede ignorar)
 *
 *   ESP32 -> Web
 *     {"tipo":"hola","nombre":"DOTLY","protocolo":1}         opcional
 *     {"tipo":"celda","puntos":7}                            se han marcado puntos EN EL APARATO
 *     {"tipo":"tecla","tecla":"aceptar"}                     aceptar | siguiente | anterior | borrar
 *     {"tipo":"pantalla","lineas":["...","..."]}             opcional: lo que enseña su pantalla
 *
 *   Lo mismo por HTTP (formularios application/x-www-form-urlencoded):
 *     POST /api/celda    p=19&c=h
 *     POST /api/pizarra  t=Hola          ← ya existe en el firmware actual de DOTLY
 *     GET  /api/estado   -> {"protocolo":1,
 *                            "celda":{"puntos":7,"n":3},
 *                            "tecla":{"tecla":"aceptar","n":5},
 *                            "pantalla":["...","..."]}
 *         'n' cuenta los cambios hechos en el aparato: cuando cambia, hay
 *         algo nuevo. Si la página no la sirve el propio ESP32, este debe
 *         contestar con la cabecera  Access-Control-Allow-Origin: *
 *
 *  ---------------------------------------------------------------------
 *  EQUIPOS QUE SE ENTIENDEN
 *  ---------------------------------------------------------------------
 *   · Uno con este protocolo (WebSocket o HTTP): todo va y viene.
 *   · El firmware actual de DOTLY (dotly/dotly.ino): se reconoce porque su
 *     /api/estado trae "pantalla". Se le manda el texto y la letra que se
 *     está viendo por /api/pizarra, y su pantalla se ve en el panel.
 *   · Un equipo que contesta pero sin la cabecera CORS: el navegador deja
 *     ENVIARLE (peticiones «no-cors»), pero no leer lo que responde. Se queda
 *     en «solo envío».
 * ===================================================================== */
(function (raiz) {
  'use strict';
  const Dotly = raiz.Dotly = raiz.Dotly || {};

  const ESPERA_PRUEBA_MS = 3500;          // lo que se espera a cada camino al conectar
  const ESPERA_ENVIO_MS = 4000;
  const PING_MS = 15000;
  const REINTENTOS_MS = [1000, 2000, 4000, 8000, 10000];
  const TECLAS = ['aceptar', 'siguiente', 'anterior', 'borrar'];

  // ---------------------------------------------------------------------
  // Eventos hacia la aplicación
  //   'estado'   {estado, transporte, detalle, equipo, direccion}
  //   'celda'    {puntos}      el aparato ha cambiado su celda
  //   'tecla'    {tecla}       se ha pulsado un botón del aparato
  //   'pantalla' [lineas]      lo que enseña la pantalla del aparato
  //   'registro' {dir, texto}  para el registro de mensajes del panel
  // ---------------------------------------------------------------------
  const oyentes = {};
  function on(evento, fn) { (oyentes[evento] = oyentes[evento] || []).push(fn); }
  function emitir(evento, dato) {
    (oyentes[evento] || []).forEach((fn) => { try { fn(dato); } catch (e) { console.error(e); } });
  }

  // estado:  local | conectando | conectado | solo-envio | reconectando | error
  // transporte: websocket | http | http-envio | null
  const s = {
    estado: 'local', detalle: '', transporte: null, equipo: null,
    destino: null, opciones: null, generacion: 0, porUsuario: false,
    ws: null, sondeo: null, ping: null, reintento: null, nReintento: 0,
    pendiente: { celda: null, texto: null }, enviando: false, fallos: 0,
    nCelda: null, nTecla: null, pantalla: '',
  };

  function info() {
    return {
      estado: s.estado, detalle: s.detalle, transporte: s.transporte,
      equipo: s.equipo, direccion: s.destino ? s.destino.texto : '',
    };
  }
  function ponerEstado(estado, detalle) {
    s.estado = estado;
    s.detalle = detalle || '';
    emitir('estado', info());
  }
  function registro(dir, texto) { emitir('registro', { dir, texto, hora: new Date() }); }

  const valido = (p) => Number.isInteger(p) && p >= 0 && p <= 63;
  const limpio = (x) => String(x == null ? '' : x).replace(/[\u0000-\u001f]/g, ' ').slice(0, 80);
  const puntosTexto = (p) => (Dotly.Braille ? Dotly.Braille.describir(p) : String(p));

  // ---------------------------------------------------------------------
  // Dirección: "192.168.4.1", "192.168.4.1:8080", "http://dotly.local/"
  // ---------------------------------------------------------------------
  function interpretar(entrada, opciones) {
    const o = opciones || {};
    const t = String(entrada || '').trim().toLowerCase()
      .replace(/^[a-z][a-z0-9+.-]*:\/\//, '').replace(/[/?#].*$/, '');
    const m = /^([a-z0-9](?:[a-z0-9.-]*[a-z0-9])?)(?::(\d{1,5}))?$/.exec(t);
    if (!m || m[1].length > 253 || m[1].includes('..')) return null;
    const host = m[1];
    const puertoHttp = m[2] ? +m[2] : 80;
    const puertoWs = o.puertoWs ? +o.puertoWs : 81;
    const rutaWs = '/' + String(o.rutaWs || '').replace(/^\/+/, '');
    if (!(puertoHttp >= 1 && puertoHttp <= 65535 && puertoWs >= 1 && puertoWs <= 65535)) return null;
    if (!/^\/[a-z0-9/_.-]*$/i.test(rutaWs)) return null;
    const conPuerto = (p) => (p === 80 ? host : host + ':' + p);
    const ws = ['ws://' + host + ':' + puertoWs + rutaWs];
    // si no se ha elegido otra ruta, prueba también la de ESPAsyncWebServer
    if (!o.rutaWs || o.rutaWs === '/') ws.push('ws://' + conPuerto(puertoHttp) + '/ws');
    return { texto: conPuerto(puertoHttp), http: 'http://' + conPuerto(puertoHttp), ws };
  }

  function pedir(url, opciones, espera) {
    const control = new AbortController();
    const t = setTimeout(() => control.abort(), espera || ESPERA_PRUEBA_MS);
    return fetch(url, Object.assign({ signal: control.signal }, opciones)).finally(() => clearTimeout(t));
  }

  // ---------------------------------------------------------------------
  // CONECTAR: se prueban los caminos a la vez y se queda el primero que
  // funcione, por orden de preferencia (WebSocket antes que HTTP).
  // ---------------------------------------------------------------------
  async function conectar(entrada, opciones) {
    const o = Object.assign({ transporte: 'auto', puertoWs: 81, rutaWs: '/', intervalo: 800 }, opciones || {});
    const destino = interpretar(entrada, o);
    if (!destino) throw new Error('Escribe la dirección del ESP32, por ejemplo 192.168.4.1');
    desconectar(true);
    s.destino = destino;
    s.opciones = o;
    s.porUsuario = false;
    return intentar(++s.generacion);
  }

  async function intentar(gen) {
    const d = s.destino, o = s.opciones;
    ponerEstado(s.nReintento ? 'reconectando' : 'conectando', 'Buscando el equipo en ' + d.texto + '…');
    const pruebas = [];
    if (o.transporte !== 'http') d.ws.forEach((url) => pruebas.push({ tipo: 'websocket', url, p: probarWebSocket(url) }));
    if (o.transporte !== 'websocket') pruebas.push({ tipo: 'http', url: d.http, p: probarHttp(d.http) });

    let elegida = null;
    for (const pr of pruebas) {
      const r = await pr.p;
      pr.r = r;
      if (gen !== s.generacion) break;
      if (r.ok) { elegida = pr; break; }
    }
    // Los WebSocket que se abran tarde y no se usen, se cierran
    pruebas.forEach((pr) => pr.p.then((r) => { if (r.ws && pr !== elegida) try { r.ws.close(); } catch (e) { /* nada */ } }));
    if (gen !== s.generacion) return false;
    if (!elegida) { fallo(pruebas); return false; }

    s.nReintento = 0;
    s.fallos = 0;
    if (elegida.tipo === 'websocket') usarWebSocket(elegida.r.ws, elegida.url);
    else if (elegida.r.cors) usarHttp(elegida.r.estado);
    else usarSoloEnvio();
    return true;
  }

  function fallo(pruebas) {
    if (s.nReintento) { programarReintento(); return; }
    let detalle;
    const http = pruebas.find((p) => p.tipo === 'http');
    if (typeof location !== 'undefined' && location.protocol === 'https:') {
      detalle = 'Esta página está abierta con https, y los navegadores no dejan que una página https hable con un ' +
        'aparato por http o ws. Ábrela desde el propio ESP32 (http://' + s.destino.texto + '), desde un servidor http ' +
        'o como archivo en este ordenador.';
    } else if (http && http.r && http.r.alcanzable) {
      detalle = 'Hay algo en ' + s.destino.texto + ', pero no parece un DOTLY: ' + http.r.error + '.';
    } else {
      detalle = 'No contesta nada en ' + s.destino.texto + '. Comprueba que el ESP32 está encendido y que este ' +
        'aparato está conectado a su misma red WiFi.';
    }
    ponerEstado('error', detalle);
    registro('!', 'No se pudo conectar con ' + s.destino.texto);
  }

  function probarWebSocket(url) {
    return new Promise((resolve) => {
      let ws, t = null, hecho = false;
      const fin = (r) => { if (hecho) return; hecho = true; clearTimeout(t); resolve(r); };
      try { ws = new WebSocket(url); } catch (e) { fin({ ok: false, error: e.message }); return; }
      t = setTimeout(() => { try { ws.close(); } catch (e) { /* nada */ } fin({ ok: false, error: 'sin respuesta' }); }, ESPERA_PRUEBA_MS);
      ws.onopen = () => fin({ ok: true, ws });
      ws.onerror = () => fin({ ok: false, error: 'rechazada' });
      ws.onclose = () => fin({ ok: false, error: 'cerrada' });
    });
  }

  async function probarHttp(base) {
    let r;
    try {
      r = await pedir(base + '/api/estado', { cache: 'no-store' });
    } catch (e) {
      // O no hay nadie, o el equipo contesta pero el navegador no deja leer la
      // respuesta (le falta la cabecera CORS). Una petición «no-cors» no se
      // puede leer, pero si no falla es que al otro lado hay alguien.
      try {
        await pedir(base + '/api/estado', { mode: 'no-cors', cache: 'no-store' });
        return { ok: true, cors: false };
      } catch (e2) {
        return { ok: false, alcanzable: false, error: 'sin respuesta' };
      }
    }
    if (!r.ok) return { ok: false, alcanzable: true, error: 'contesta ' + r.status + ' en /api/estado' };
    try {
      const estado = await r.json();
      if (!estado || typeof estado !== 'object') throw new Error('no es un objeto');
      return { ok: true, cors: true, estado };
    } catch (e) {
      return { ok: false, alcanzable: true, error: 'no devuelve JSON en /api/estado' };
    }
  }

  // ---------------------------------------------------------------------
  // Camino 1: WEBSOCKET
  // ---------------------------------------------------------------------
  function usarWebSocket(ws, url) {
    s.ws = ws;
    s.transporte = 'websocket';
    s.equipo = { nombre: 'Equipo', celda: true, pantalla: false };
    ws.onmessage = (e) => recibir(e.data);
    ws.onerror = () => { /* llega también un close */ };
    ws.onclose = () => perdida();
    enviarWs({ tipo: 'hola', cliente: 'dotly-web', protocolo: 1 });
    s.ping = setInterval(() => enviarWs({ tipo: 'ping' }), PING_MS);
    ponerEstado('conectado', 'Por WebSocket (' + url + '): la celda y los botones del equipo van y vienen al momento.');
    registro('·', 'Conectado por WebSocket a ' + url);
    vaciar();
  }

  function enviarWs(obj) {
    if (!s.ws || s.ws.readyState !== 1) return false;
    try { s.ws.send(JSON.stringify(obj)); return true; } catch (e) { return false; }
  }

  // Lo que llega del ESP32. Se valida todo: es un aparato de fuera.
  function recibir(datos) {
    let m;
    try { m = JSON.parse(datos); } catch (e) { return; }
    if (!m || typeof m !== 'object') return;
    if (m.tipo === 'hola') {
      s.equipo = Object.assign({}, s.equipo, { nombre: limpio(m.nombre) || 'Equipo', protocolo: m.protocolo | 0 });
      registro('←', 'Saludo de ' + s.equipo.nombre);
      emitir('estado', info());
    } else if (m.tipo === 'celda' && valido(m.puntos)) {
      registro('←', 'Celda del equipo: ' + puntosTexto(m.puntos));
      emitir('celda', { puntos: m.puntos });
    } else if (m.tipo === 'tecla' && TECLAS.includes(m.tecla)) {
      registro('←', 'Botón del equipo: ' + m.tecla);
      emitir('tecla', { tecla: m.tecla });
    } else if (m.tipo === 'pantalla' && Array.isArray(m.lineas)) {
      emitir('pantalla', m.lineas.slice(0, 4).map(limpio));
    }
  }

  // ---------------------------------------------------------------------
  // Camino 2: HTTP (con lectura de respuestas)
  // ---------------------------------------------------------------------
  function usarHttp(estado) {
    s.transporte = 'http';
    s.equipo = {
      nombre: limpio(estado.nombre) || (Array.isArray(estado.pantalla) ? 'DOTLY' : 'Equipo'),
      // ¿sabe mover una celda (/api/celda)? Si no, es el firmware actual de DOTLY
      celda: estado.celda !== undefined || (estado.protocolo | 0) >= 1,
      pantalla: Array.isArray(estado.pantalla),
    };
    s.nCelda = estado.celda && Number.isInteger(estado.celda.n) ? estado.celda.n : null;
    s.nTecla = estado.tecla && Number.isInteger(estado.tecla.n) ? estado.tecla.n : null;
    leerEstado(estado);
    ponerEstado('conectado', detalleHttp());
    registro('·', 'Conectado por HTTP a ' + s.destino.http);
    sondear();
    vaciar();
  }

  function detalleHttp() {
    const cada = (s.opciones.intervalo / 1000).toLocaleString('es') + ' s';
    if (s.equipo.celda) return 'Por HTTP: se envía al momento y se consulta el equipo cada ' + cada + '.';
    return 'Conectado a ' + s.equipo.nombre + ' por HTTP. Su firmware enseña en pantalla el texto y las letras ' +
      'que mandes; para mover una celda física le hace falta /api/celda.';
  }

  function sondear() {
    const gen = s.generacion;
    const espera = (typeof document !== 'undefined' && document.hidden) ? 5000 : s.opciones.intervalo;
    s.sondeo = setTimeout(() => {
      pedir(s.destino.http + '/api/estado', { cache: 'no-store' }, 3000)
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error('HTTP ' + r.status))))
        .then((j) => {
          if (gen !== s.generacion) return;
          if (s.fallos >= 3) ponerEstado('conectado', detalleHttp());
          s.fallos = 0;
          leerEstado(j);
        })
        .catch(() => {
          if (gen !== s.generacion) return;
          if (++s.fallos === 3) ponerEstado('reconectando', 'El equipo ha dejado de contestar. Se sigue intentando…');
        })
        .finally(() => { if (gen === s.generacion && s.transporte === 'http') sondear(); });
    }, espera);
  }

  function leerEstado(j) {
    if (!j || typeof j !== 'object') return;
    const c = j.celda;
    if (c && typeof c === 'object' && valido(c.puntos) && Number.isInteger(c.n)) {
      if (s.nCelda !== null && c.n !== s.nCelda) {
        registro('←', 'Celda del equipo: ' + puntosTexto(c.puntos));
        emitir('celda', { puntos: c.puntos });
      }
      s.nCelda = c.n;
    }
    const t = j.tecla;
    if (t && typeof t === 'object' && TECLAS.includes(t.tecla) && Number.isInteger(t.n)) {
      if (s.nTecla !== null && t.n !== s.nTecla) {
        registro('←', 'Botón del equipo: ' + t.tecla);
        emitir('tecla', { tecla: t.tecla });
      }
      s.nTecla = t.n;
    }
    if (Array.isArray(j.pantalla)) {
      const lineas = j.pantalla.slice(0, 4).map(limpio);
      const texto = lineas.join('\n');
      if (texto !== s.pantalla) { s.pantalla = texto; emitir('pantalla', lineas); }
    }
  }

  // ---------------------------------------------------------------------
  // Camino 2b: HTTP SOLO ENVÍO (el equipo no manda la cabecera CORS)
  // ---------------------------------------------------------------------
  function usarSoloEnvio() {
    s.transporte = 'http-envio';
    s.equipo = { nombre: 'Equipo', celda: false, pantalla: false };
    ponerEstado('solo-envio', 'El equipo recibe lo que mandes, pero el navegador no deja leer sus respuestas ' +
      '(no manda la cabecera CORS). Si abres esta página desde el propio equipo, la conexión es completa.');
    registro('·', 'Conectado solo para enviar a ' + s.destino.http);
    vaciar();
  }

  // ---------------------------------------------------------------------
  // ENVIAR: la web le cuenta al ESP32 qué celda o qué texto se está viendo.
  // Si se manda mucho seguido, solo viaja lo último (no se atasca el ESP32).
  // ---------------------------------------------------------------------
  function enviarCelda(puntos, opciones) {
    if (!s.transporte) return;                       // sin conexión: la web va sola
    const o = opciones || {};
    const revelar = o.revelar !== false;
    s.pendiente.celda = { puntos: puntos & 0x3F, caracter: revelar ? String(o.caracter || '') : '', revelar };
    vaciar();
  }

  function enviarTexto(texto) {
    if (!s.transporte) return;
    s.pendiente.texto = String(texto || '').slice(0, 300);
    vaciar();
  }

  // Qué peticiones HTTP corresponden a cada mensaje, según lo que sepa el equipo
  function peticionesHttp(tipo, dato) {
    if (tipo === 'texto') return dato.trim() ? [['/api/pizarra', { t: dato }]] : [];
    const lista = [];
    // Con /api/celda (o si no sabemos, en solo envío) se manda la celda tal cual
    if (s.equipo.celda || s.transporte === 'http-envio') lista.push(['/api/celda', { p: dato.puntos, c: dato.caracter }]);
    // Sin /api/celda, el firmware actual de DOTLY enseña la letra en su pizarra
    if (!s.equipo.celda && dato.revelar && dato.caracter.trim()) lista.push(['/api/pizarra', { t: dato.caracter }]);
    return lista;
  }

  function vaciar() {
    if (!s.transporte) return;
    if (s.transporte === 'websocket') {
      const c = s.pendiente.celda, t = s.pendiente.texto;
      if (c && enviarWs({ tipo: 'celda', puntos: c.puntos, caracter: c.caracter })) {
        s.pendiente.celda = null;
        registro('→', 'Celda: ' + puntosTexto(c.puntos) + (c.caracter ? ' (' + c.caracter + ')' : ''));
      }
      if (t !== null && enviarWs({ tipo: 'texto', texto: t })) {
        s.pendiente.texto = null;
        registro('→', 'Texto: «' + t + '»');
      }
      return;
    }
    if (s.enviando) return;                          // por HTTP, de una en una
    let tipo, dato;
    if (s.pendiente.celda) { tipo = 'celda'; dato = s.pendiente.celda; s.pendiente.celda = null; }
    else if (s.pendiente.texto !== null) { tipo = 'texto'; dato = s.pendiente.texto; s.pendiente.texto = null; }
    else return;
    const peticiones = peticionesHttp(tipo, dato);
    if (!peticiones.length) { vaciar(); return; }
    s.enviando = true;
    const gen = s.generacion;
    (async () => {
      for (const [ruta, datos] of peticiones) {
        if (gen !== s.generacion) return;
        const cuerpo = new URLSearchParams();
        Object.keys(datos).forEach((k) => cuerpo.set(k, String(datos[k])));
        const op = { method: 'POST', body: cuerpo, cache: 'no-store' };
        if (s.transporte === 'http-envio') op.mode = 'no-cors';
        try {
          const r = await pedir(s.destino.http + ruta, op, ESPERA_ENVIO_MS);
          if (r.type !== 'opaque' && !r.ok) registro('!', ruta + ' contestó ' + r.status);
          else registro('→', tipo === 'texto' ? 'Texto: «' + dato + '»'
            : 'Celda: ' + puntosTexto(dato.puntos) + (dato.caracter ? ' (' + dato.caracter + ')' : '') + ' → ' + ruta);
        } catch (e) {
          registro('!', 'No se pudo enviar a ' + ruta);
        }
      }
    })().finally(() => {
      if (gen !== s.generacion) return;
      s.enviando = false;
      vaciar();
    });
  }

  // ---------------------------------------------------------------------
  // Pérdida de conexión y desconexión
  // ---------------------------------------------------------------------
  function perdida() {
    if (s.porUsuario) return;
    clearInterval(s.ping);
    s.ws = null;
    s.transporte = null;
    registro('!', 'Se ha perdido la conexión');
    programarReintento();
  }

  function programarReintento() {
    const espera = REINTENTOS_MS[Math.min(s.nReintento, REINTENTOS_MS.length - 1)];
    s.nReintento++;
    ponerEstado('reconectando', 'Se ha perdido la conexión. Nuevo intento en ' + Math.round(espera / 1000) + ' s…');
    const gen = s.generacion;
    s.reintento = setTimeout(() => { if (gen === s.generacion) intentar(gen); }, espera);
  }

  function desconectar(silencioso) {
    s.generacion++;
    s.porUsuario = true;
    clearTimeout(s.sondeo);
    clearInterval(s.ping);
    clearTimeout(s.reintento);
    if (s.ws) {
      s.ws.onclose = null;
      s.ws.onmessage = null;
      try { s.ws.close(); } catch (e) { /* nada */ }
    }
    const habia = !!s.transporte;
    Object.assign(s, {
      ws: null, transporte: null, equipo: null, nReintento: 0, fallos: 0, enviando: false,
      pendiente: { celda: null, texto: null }, nCelda: null, nTecla: null, pantalla: '',
    });
    if (!silencioso) {
      ponerEstado('local', 'Sin conectar: la web funciona por su cuenta.');
      if (habia) registro('·', 'Desconectado');
    }
  }

  Dotly.Conexion = {
    on, conectar, desconectar, enviarCelda, enviarTexto, interpretar,
    estado: info,
    get activa() { return !!s.transporte; },
  };
})(typeof window !== 'undefined' ? window : globalThis);
