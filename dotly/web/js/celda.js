/* =====================================================================
 *  DOTLY web · COMPONENTE «CELDA BRAILLE»
 * =====================================================================
 *  Una celda de 6 puntos. Dos formas de usarla:
 *
 *   · Interactiva:  new Dotly.Celda(elemento, { alCambiar(puntos, origen) })
 *     Cada punto es un <button> con aria-pressed: se usa con el ratón, con
 *     el dedo, con el tabulador y con un lector de pantalla. Dentro de la
 *     celda las flechas mueven el foco de un punto a otro.
 *
 *   · Solo para ver:  Dotly.Celda.html(puntos, { tam, etiqueta })
 *     Devuelve el HTML de una celda estática (para listas y el traductor).
 *
 *  'origen' dice quién cambió la celda: 'usuario' (tocó un punto),
 *  'equipo' (llegó del ESP32) o 'programa'. Así la conexión no le reenvía
 *  al ESP32 lo que acaba de llegar de él.
 * ===================================================================== */
(function (raiz) {
  'use strict';
  const Dotly = raiz.Dotly = raiz.Dotly || {};

  // Dónde va cada punto en la rejilla de 2 columnas x 3 filas
  const SITIO = { 1: [1, 1], 2: [1, 2], 3: [1, 3], 4: [2, 1], 5: [2, 2], 6: [2, 3] };
  // Para moverse con las flechas: vecino de cada punto
  const VECINO = {
    ArrowUp:    { 1: 1, 2: 1, 3: 2, 4: 4, 5: 4, 6: 5 },
    ArrowDown:  { 1: 2, 2: 3, 3: 3, 4: 5, 5: 6, 6: 6 },
    ArrowLeft:  { 1: 1, 2: 2, 3: 3, 4: 1, 5: 2, 6: 3 },
    ArrowRight: { 1: 4, 2: 5, 3: 6, 4: 4, 5: 5, 6: 6 },
  };

  class Celda {
    constructor(elemento, opciones) {
      const o = opciones || {};
      this.el = elemento;
      this.puntos = 0;
      this.bloqueada = false;
      this.alCambiar = o.alCambiar || null;
      this.el.classList.add('celda', 'celda--' + (o.tam || 'grande'));
      this.el.setAttribute('role', 'group');
      this.el.setAttribute('aria-label', o.etiqueta || 'Celda braille: toca los puntos');
      this.botones = [];
      for (let n = 1; n <= 6; n++) {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'punto';
        b.dataset.punto = String(n);
        b.style.gridColumn = String(SITIO[n][0]);
        b.style.gridRow = String(SITIO[n][1]);
        b.setAttribute('aria-pressed', 'false');
        b.innerHTML = '<span class="punto__n" aria-hidden="true">' + n + '</span>';
        this._etiqueta(b, n, '');
        b.addEventListener('click', () => this.alternar(n, 'usuario'));
        b.addEventListener('keydown', (e) => this._teclaFlecha(e, n));
        this.el.appendChild(b);
        this.botones.push(b);
      }
    }

    _etiqueta(b, n, extra) {
      b.setAttribute('aria-label', 'Punto ' + n + (extra ? ', ' + extra : ''));
    }

    _teclaFlecha(e, n) {
      const destino = VECINO[e.key] && VECINO[e.key][n];
      if (!destino) return;
      e.preventDefault();
      this.botones[destino - 1].focus();
    }

    // Pone la celda en unos puntos concretos
    poner(puntos, origen) {
      const m = (puntos | 0) & 0x3F;
      const cambia = m !== this.puntos;
      this.puntos = m;
      this.botones.forEach((b, i) => b.setAttribute('aria-pressed', (m >> i) & 1 ? 'true' : 'false'));
      if (cambia && this.alCambiar) this.alCambiar(m, origen || 'programa');
    }

    alternar(n, origen) {
      if (this.bloqueada) return;
      this.limpiarMarcas();
      this.poner(this.puntos ^ (1 << (n - 1)), origen || 'usuario');
    }

    // Mientras se corrige una respuesta, la celda no se puede tocar
    bloquear(si) {
      this.bloqueada = !!si;
      this.el.classList.toggle('celda--bloqueada', this.bloqueada);
      this.botones.forEach((b) => b.setAttribute('aria-disabled', si ? 'true' : 'false'));
    }

    // Enseña la diferencia con la respuesta buena: los puntos que faltan
    // (un «+») y los que sobran (una «×»). No solo con color: con un
    // símbolo y en la etiqueta que lee el lector de pantalla.
    marcarDiferencias(esperada) {
      this.limpiarMarcas();
      this.botones.forEach((b, i) => {
        const debe = (esperada >> i) & 1, esta = (this.puntos >> i) & 1;
        if (debe && !esta) { b.classList.add('punto--falta'); this._etiqueta(b, i + 1, 'falta marcarlo'); }
        if (!debe && esta) { b.classList.add('punto--sobra'); this._etiqueta(b, i + 1, 'sobra'); }
      });
    }

    // Pista: señala un punto que hay que marcar, sin marcarlo
    marcarPista(n) {
      const b = this.botones[n - 1];
      b.classList.add('punto--pista');
      this._etiqueta(b, n, 'pista: márcalo');
    }

    limpiarMarcas() {
      this.botones.forEach((b, i) => {
        b.classList.remove('punto--falta', 'punto--sobra', 'punto--pista');
        this._etiqueta(b, i + 1, '');
      });
    }

    enfocar() { this.botones[0].focus(); }
  }

  // Celda estática: HTML listo para meter en la página
  Celda.html = function (puntos, opciones) {
    const o = opciones || {};
    const m = (puntos | 0) & 0x3F;
    const B = Dotly.Braille;
    const etiqueta = o.etiqueta === false ? '' : (o.etiqueta || (B ? B.describir(m) : ''));
    let h = '<span class="celda celda--' + (o.tam || 'mini') + (o.clase ? ' ' + o.clase : '') + '"' +
      (etiqueta ? ' role="img" aria-label="' + etiqueta + '"' : ' aria-hidden="true"') + '>';
    for (let n = 1; n <= 6; n++) {
      h += '<span class="punto' + ((m >> (n - 1)) & 1 ? ' punto--on' : '') + '" style="grid-column:' +
        SITIO[n][0] + ';grid-row:' + SITIO[n][1] + '"><span class="punto__n">' + n + '</span></span>';
    }
    return h + '</span>';
  };

  Dotly.Celda = Celda;
})(typeof window !== 'undefined' ? window : globalThis);
