// =====================================================================
//  ESTO NO SE GRABA EN NINGUNA PLACA.
// =====================================================================
//  Un ESP32 de mentira que habla el protocolo de dotly/web/js/conexion.js,
//  por HTTP y por WebSocket, sin ninguna dependencia (solo Node). Sirve para
//  las pruebas y para probar la conexión de la web sin hardware:
//
//      node esp32_simulado.js                → HTTP en 8080 y WebSocket en 8081
//      node esp32_simulado.js 8080 8081 --sin-cors
//
//  En la web: Conexión → dirección 127.0.0.1:8080 y, en «Opciones
//  avanzadas», puerto WebSocket 8081. Luego, en esta terminal:
//      c 125        el «aparato» marca los puntos 1, 2 y 5
//      t aceptar    pulsa un botón (aceptar, siguiente, anterior, borrar)
//      v            enseña lo que ha llegado de la web
// =====================================================================
'use strict';
const http = require('http');
const crypto = require('crypto');

function crearEsp32(opciones) {
  const o = Object.assign({ puertoHttp: 0, puertoWs: 0, cors: true, rutaWs: '/', conWs: true, host: '127.0.0.1' }, opciones || {});
  // 'n' cuenta los cambios hechos EN EL APARATO (así la web sabe que hay algo nuevo)
  const estado = { celda: { puntos: 0, n: 0 }, tecla: { tecla: 'aceptar', n: 0 }, texto: '', pantalla: ['DOTLY simulado', 'Esperando...'] };
  const recibidos = [];
  const clientesWs = new Set();

  // ---------------- HTTP ----------------
  const servidorHttp = http.createServer((req, res) => {
    const ruta = new URL(req.url, 'http://x').pathname;
    const cab = { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' };
    if (o.cors) {
      cab['Access-Control-Allow-Origin'] = '*';
      cab['Access-Control-Allow-Private-Network'] = 'true';
    }
    if (req.method === 'OPTIONS') {
      res.writeHead(204, Object.assign(cab, { 'Access-Control-Allow-Methods': 'GET, POST', 'Access-Control-Allow-Headers': 'Content-Type' }));
      res.end();
      return;
    }
    let cuerpo = '';
    req.on('data', (d) => { cuerpo += d; });
    req.on('end', () => {
      const f = new URLSearchParams(cuerpo);
      const responder = (codigo, obj) => { res.writeHead(codigo, cab); res.end(JSON.stringify(obj)); };
      if (req.method === 'GET' && ruta === '/api/estado') {
        return responder(200, { nombre: 'DOTLY simulado', protocolo: 1, celda: estado.celda, tecla: estado.tecla, pantalla: estado.pantalla });
      }
      if (req.method === 'POST' && ruta === '/api/celda') {
        const p = Number(f.get('p'));
        if (!(Number.isInteger(p) && p >= 0 && p <= 63)) return responder(400, { ok: false });
        estado.celda.puntos = p;                      // lo manda la web: 'n' no cambia
        recibidos.push({ via: 'http', tipo: 'celda', puntos: p, caracter: f.get('c') || '' });
        return responder(200, { ok: true });
      }
      if (req.method === 'POST' && ruta === '/api/pizarra') {
        estado.texto = f.get('t') || '';
        estado.pantalla = [estado.texto.slice(0, 16), ''];
        recibidos.push({ via: 'http', tipo: 'texto', texto: estado.texto });
        return responder(200, { ok: true });
      }
      responder(404, { ok: false });
    });
  });

  // ---------------- WebSocket (RFC 6455, lo justo) ----------------
  const servidorWs = http.createServer((req, res) => { res.writeHead(426); res.end(); });
  servidorWs.on('upgrade', (req, socket) => {
    if (new URL(req.url, 'http://x').pathname !== o.rutaWs || !req.headers['sec-websocket-key']) {
      socket.end('HTTP/1.1 404 Not Found\r\n\r\n');
      return;
    }
    const acepta = crypto.createHash('sha1')
      .update(req.headers['sec-websocket-key'] + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest('base64');
    socket.write('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n' +
      'Sec-WebSocket-Accept: ' + acepta + '\r\n\r\n');
    const cli = { socket, buf: Buffer.alloc(0) };
    clientesWs.add(cli);
    socket.on('data', (d) => { cli.buf = Buffer.concat([cli.buf, d]); leerTramas(cli); });
    socket.on('close', () => clientesWs.delete(cli));
    socket.on('error', () => clientesWs.delete(cli));
  });

  function leerTramas(cli) {
    for (;;) {
      const b = cli.buf;
      if (b.length < 2) return;
      const op = b[0] & 0x0f;
      const conMascara = (b[1] & 0x80) !== 0;
      let largo = b[1] & 0x7f, pos = 2;
      if (largo === 126) { if (b.length < 4) return; largo = b.readUInt16BE(2); pos = 4; }
      else if (largo === 127) { if (b.length < 10) return; largo = Number(b.readBigUInt64BE(2)); pos = 10; }
      const mascara = conMascara ? b.subarray(pos, pos + 4) : null;
      if (conMascara) pos += 4;
      if (b.length < pos + largo) return;
      let datos = Buffer.from(b.subarray(pos, pos + largo));
      if (mascara) for (let i = 0; i < datos.length; i++) datos[i] ^= mascara[i % 4];
      cli.buf = b.subarray(pos + largo);
      if (op === 0x8) { trama(cli.socket, 0x8, Buffer.alloc(0)); cli.socket.end(); clientesWs.delete(cli); return; }
      if (op === 0x9) { trama(cli.socket, 0xA, datos); continue; }
      if (op === 0x1) mensaje(cli, datos.toString('utf8'));
    }
  }
  function trama(socket, op, datos) {
    const n = datos.length;
    let cab;
    if (n < 126) cab = Buffer.from([0x80 | op, n]);
    else if (n < 65536) { cab = Buffer.alloc(4); cab[0] = 0x80 | op; cab[1] = 126; cab.writeUInt16BE(n, 2); }
    else { cab = Buffer.alloc(10); cab[0] = 0x80 | op; cab[1] = 127; cab.writeBigUInt64BE(BigInt(n), 2); }
    if (!socket.destroyed) socket.write(Buffer.concat([cab, datos]));
  }
  const enviarWs = (cli, obj) => trama(cli.socket, 0x1, Buffer.from(JSON.stringify(obj)));

  function mensaje(cli, texto) {
    let m;
    try { m = JSON.parse(texto); } catch (e) { return; }
    recibidos.push(Object.assign({ via: 'ws' }, m));
    if (m.tipo === 'hola') enviarWs(cli, { tipo: 'hola', nombre: 'DOTLY simulado', protocolo: 1 });
    if (m.tipo === 'celda' && Number.isInteger(m.puntos)) estado.celda.puntos = m.puntos;
    if (m.tipo === 'texto') { estado.texto = String(m.texto); estado.pantalla = [estado.texto.slice(0, 16), '']; }
  }

  // ---------------- lo que hace el «aparato» ----------------
  function marcar(puntos) {
    estado.celda = { puntos, n: estado.celda.n + 1 };
    clientesWs.forEach((c) => enviarWs(c, { tipo: 'celda', puntos }));
  }
  function pulsar(tecla) {
    estado.tecla = { tecla, n: estado.tecla.n + 1 };
    clientesWs.forEach((c) => enviarWs(c, { tipo: 'tecla', tecla }));
  }
  function cortarWs() { clientesWs.forEach((c) => c.socket.destroy()); clientesWs.clear(); }

  const escuchar = (srv, puerto) => new Promise((ok, mal) => { srv.once('error', mal); srv.listen(puerto, o.host, () => ok(srv.address().port)); });
  const cerrar = (srv) => new Promise((ok) => (srv.listening ? srv.close(() => ok()) : ok()));

  return {
    estado, recibidos, clientesWs, marcar, pulsar, cortarWs,
    async arrancar() {
      const puertos = { http: await escuchar(servidorHttp, o.puertoHttp) };
      if (o.conWs) puertos.ws = await escuchar(servidorWs, o.puertoWs);
      return puertos;
    },
    async parar() {
      cortarWs();
      if (servidorHttp.closeAllConnections) servidorHttp.closeAllConnections();
      await cerrar(servidorHttp);
      await cerrar(servidorWs);
    },
  };
}

module.exports = { crearEsp32 };

// ---------------- uso a mano ----------------
if (require.main === module) {
  const args = process.argv.slice(2);
  const numeros = args.filter((a) => /^\d+$/.test(a)).map(Number);
  const esp = crearEsp32({ puertoHttp: numeros[0] || 8080, puertoWs: numeros[1] || 8081, cors: !args.includes('--sin-cors'), host: '0.0.0.0' });
  esp.arrancar().then((p) => {
    console.log('ESP32 simulado: HTTP en el puerto ' + p.http + ' y WebSocket en el ' + p.ws);
    console.log('En la web: dirección 127.0.0.1:' + p.http + ', y en «Opciones avanzadas» puerto WebSocket ' + p.ws);
    console.log('Órdenes:  c 125 (marca puntos)   t aceptar|siguiente|anterior|borrar   v (lo recibido)');
  });
  let vistos = 0;
  process.stdin.on('data', (d) => {
    const [orden, arg] = String(d).trim().split(/\s+/);
    if (orden === 'c') {
      let m = 0;
      for (const ch of arg || '') if (ch >= '1' && ch <= '6') m |= 1 << (ch.charCodeAt(0) - 49);
      esp.marcar(m);
      console.log('  el aparato marca ' + m);
    } else if (orden === 't') {
      esp.pulsar(arg || 'aceptar');
      console.log('  botón ' + (arg || 'aceptar'));
    } else if (orden === 'v') {
      esp.recibidos.slice(vistos).forEach((r) => console.log('  ←', JSON.stringify(r)));
      vistos = esp.recibidos.length;
    }
  });
}
