// =====================================================================
//  DOTLY - pagina web que sirve el propio equipo
// =====================================================================
//  Va dentro del firmware (no hay que subir nada a la memoria aparte). No
//  usa nada de internet: la red de DOTLY no tiene salida. Todo lo que
//  muestra lo pide a /api/... (ver la seccion del servidor en dotly.ino).
// =====================================================================
#pragma once

const char PAGINA_WEB[] PROGMEM = R"DOTLY(<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#f5f1e8">
<title>DOTLY</title>
<style>
:root{
  --bg:#f5f1e8;--panel:#fffdf9;--panel2:#efe9dd;--tinta:#1b2336;--suave:#5b6477;--borde:#ddd5c5;
  --acento:#c2410c;--acento-t:#ffffff;--verde:#0f766e;--ok:#15803d;--mal:#b42318;--medio:#b45309;
  --on:#1b2336;--off:#d3cab8;--logo:#1b2336;--rango:rgba(15,118,110,.20);
  --lcd:#2350c8;--lcd-c:#2d5bd6;--lcd-t:#eef3ff;
  --sombra:0 1px 2px rgba(27,35,54,.07),0 6px 20px rgba(27,35,54,.06);
  --esc:1;
}
:root[data-tema="oscuro"]{
  --bg:#10141d;--panel:#181e2b;--panel2:#212839;--tinta:#e9ecf3;--suave:#9ca5b8;--borde:#2c3448;
  --acento:#fb923c;--acento-t:#1b1206;--verde:#2dd4bf;--ok:#4ade80;--mal:#f87171;--medio:#fbbf24;
  --on:#f3f4f7;--off:#3a4359;--logo:#232b3f;--rango:rgba(45,212,191,.18);
  --lcd:#1d3f9e;--lcd-c:#2447aa;--lcd-t:#e4ecff;
  --sombra:0 1px 2px rgba(0,0,0,.3),0 6px 20px rgba(0,0,0,.25);
}
@media (prefers-color-scheme:dark){
  :root:not([data-tema="claro"]){
    --bg:#10141d;--panel:#181e2b;--panel2:#212839;--tinta:#e9ecf3;--suave:#9ca5b8;--borde:#2c3448;
    --acento:#fb923c;--acento-t:#1b1206;--verde:#2dd4bf;--ok:#4ade80;--mal:#f87171;--medio:#fbbf24;
    --on:#f3f4f7;--off:#3a4359;--logo:#232b3f;--rango:rgba(45,212,191,.18);
    --lcd:#1d3f9e;--lcd-c:#2447aa;--lcd-t:#e4ecff;
    --sombra:0 1px 2px rgba(0,0,0,.3),0 6px 20px rgba(0,0,0,.25);
  }
}
*{box-sizing:border-box}
html{font-size:calc(16px * var(--esc));-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--tinta);font:1rem/1.5 system-ui,-apple-system,"Segoe UI",Roboto,Ubuntu,"Helvetica Neue",sans-serif}
button,input,textarea,select{font:inherit;color:inherit}
h1,h2,h3{line-height:1.2}
h2{font-size:1.1rem;margin:0}
h3{font-size:1rem;margin:0 0 4px}
p{margin:0 0 10px}
a{color:var(--acento)}
:focus-visible{outline:3px solid var(--acento);outline-offset:2px}
[hidden]{display:none!important}

/* ---- cabecera ---- */
.cab{max-width:1040px;margin:0 auto;padding:14px 16px 8px;display:flex;align-items:center;justify-content:space-between;gap:12px}
.marca{display:flex;align-items:center;gap:12px;min-width:0}
.logo{display:flex;gap:6px;padding:9px 11px;background:var(--logo);border-radius:14px;flex:none}
.logo .celda{--d:6px;--g:3px;padding:0}
.logo .celda i{background:rgba(255,255,255,.16)}
.logo .celda i.on{background:#fb923c}
.marca h1{margin:0;font-size:1.4rem;letter-spacing:.14em;font-weight:800}
.lema{margin:0;color:var(--suave);font-size:.85rem;white-space:nowrap}
.marca>div{min-width:0}
.cab-der{display:flex;align-items:center;gap:8px;flex:none}
.pill{display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border-radius:999px;background:var(--panel2);color:var(--suave);font-size:.8rem;white-space:nowrap}
.pill::before{content:"";width:8px;height:8px;border-radius:50%;background:var(--suave)}
.pill.ok::before{background:var(--ok)}
.pill.mal{color:var(--mal)}
.pill.mal::before{background:var(--mal)}
.icono{width:40px;height:40px;border-radius:12px;border:1px solid var(--borde);background:var(--panel);cursor:pointer;font-size:1.1rem}

/* ---- pestañas ---- */
.pestanas{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--borde)}
.pestanas>div{max-width:1040px;margin:0 auto;padding:0 12px;display:flex;gap:2px;overflow-x:auto;scrollbar-width:none}
.pestanas>div::-webkit-scrollbar{display:none}
.pestanas button{border:0;background:none;padding:12px 12px 10px;cursor:pointer;color:var(--suave);font-weight:600;border-bottom:3px solid transparent;white-space:nowrap}
.pestanas button[aria-selected="true"]{color:var(--tinta);border-bottom-color:var(--acento)}

main{max-width:1040px;margin:0 auto;padding:16px}
.col2{display:grid;gap:16px;grid-template-columns:1fr}
@media (min-width:860px){.col2{grid-template-columns:1.25fr 1fr;align-items:start}}
.tarjeta{background:var(--panel);border:1px solid var(--borde);border-radius:16px;padding:16px;box-shadow:var(--sombra);margin-bottom:16px;min-width:0}
.col2>.pila>.tarjeta:last-child,.col2>.tarjeta{margin-bottom:0}
.pila{display:grid;gap:16px}
.tit{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:12px;flex-wrap:wrap}
.suave{color:var(--suave)}
.nota{color:var(--suave);font-size:.85rem;margin:10px 0 0}
.etq{display:inline-block;padding:2px 9px;border-radius:999px;background:var(--panel2);color:var(--suave);font-size:.78rem;font-weight:600}
.etq.acento{background:var(--acento);color:var(--acento-t)}
.etq.verde{background:var(--rango);color:var(--verde)}
.vacio{color:var(--suave);font-style:italic;margin:6px 0}

/* ---- botones ---- */
.btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;border:1px solid var(--acento);background:var(--acento);color:var(--acento-t);padding:10px 16px;border-radius:12px;cursor:pointer;font-weight:600;min-height:44px}
.btn.sec{background:transparent;color:var(--tinta);border-color:var(--borde)}
.btn.peq{padding:6px 12px;min-height:36px;font-size:.9rem}
.btn.peligro{background:transparent;color:var(--mal);border-color:var(--mal)}
.btn:disabled{opacity:.5;cursor:default}
.botones{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}

/* ---- celda braille ---- */
.celda{display:inline-grid;grid-template-columns:repeat(2,var(--d,10px));gap:var(--g,4px);padding:3px;vertical-align:middle}
.celda i{width:var(--d,10px);height:var(--d,10px);border-radius:50%;background:var(--off)}
.celda i.on{background:var(--on)}
.celda.grande{--d:22px;--g:10px}
.celda.peq{--d:6px;--g:3px}

/* ---- espejo del LCD ---- */
.lcd{background:var(--lcd);border-radius:12px;padding:10px;display:grid;gap:4px;box-shadow:inset 0 0 0 3px rgba(0,0,0,.22)}
.fila{display:grid;grid-template-columns:repeat(16,minmax(0,1fr));gap:3px}
.lc{background:var(--lcd-c);color:var(--lcd-t);border-radius:3px;aspect-ratio:5/8;display:flex;align-items:center;justify-content:center;font:600 clamp(12px,3.6vw,24px)/1 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;overflow:hidden}
.lc .celda{--d:clamp(3px,.9vw,5px);--g:clamp(1px,.45vw,3px);padding:0}
.lc .celda i{background:rgba(255,255,255,.16)}
.lc .celda i.on{background:var(--lcd-t)}
.lc.lleno{background:var(--lcd-t)}
.lc.cur{animation:parpadeo 1s steps(1) infinite}
@keyframes parpadeo{50%{background:var(--lcd-t);color:var(--lcd)}}
.ayuda{margin:12px 0 0;padding:10px 12px;background:var(--panel2);border-radius:10px;font-size:.92rem}
.aviso-cal{margin:0 0 12px;padding:10px 12px;border-radius:10px;background:var(--acento);color:var(--acento-t);font-weight:600}

/* ---- teclado ---- */
.teclado{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:8px;margin-top:14px}
.tecla{border:1px solid var(--borde);background:var(--panel2);border-radius:14px;padding:10px 2px;display:flex;flex-direction:column;align-items:center;gap:2px;cursor:pointer;min-height:70px;touch-action:manipulation;transition:transform .08s}
.tecla b{font-size:1.35rem;line-height:1}
.tecla small{font-size:.72rem;color:var(--suave);line-height:1.2;text-align:center}
.tecla.pulsada{background:var(--acento);color:var(--acento-t);transform:translateY(2px)}
.tecla.pulsada small{color:inherit}

.datos{display:grid;grid-template-columns:auto 1fr;gap:6px 14px;margin:0}
.datos dt{color:var(--suave)}
.datos dd{margin:0;font-weight:600;overflow-wrap:anywhere}

/* ---- modos ---- */
.modos{display:grid;gap:12px;grid-template-columns:repeat(auto-fill,minmax(250px,1fr))}
.modo{border:1px solid var(--borde);border-radius:14px;padding:14px;background:var(--panel);display:flex;flex-direction:column;gap:6px}
.modo.activo{border-color:var(--acento);box-shadow:0 0 0 2px var(--acento) inset}
.modo p{margin:0;color:var(--suave);font-size:.92rem}
.modo .teclas{font-size:.8rem;color:var(--suave);border-top:1px dashed var(--borde);padding-top:6px;margin-top:auto}
.modo .btn{align-self:flex-start;margin-top:4px}
.rapidos{display:flex;flex-wrap:wrap;gap:8px}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:6px 0}
.chip{border:1px solid var(--borde);background:var(--panel);border-radius:999px;padding:6px 12px;cursor:pointer;font-size:.88rem;min-height:36px}
.chip.activo{border-color:var(--acento);box-shadow:0 0 0 1px var(--acento) inset}
.chip[aria-pressed="true"]{background:var(--tinta);color:var(--panel);border-color:var(--tinta)}

.seg{border:0;padding:0;margin:0 0 14px;min-width:0}
.seg legend{font-weight:600;padding:0;margin-bottom:6px}
.seg .ops{display:flex;flex-wrap:wrap;gap:6px}
.seg label{position:relative;cursor:pointer}
.seg input{position:absolute;inset:0;opacity:0;margin:0;cursor:pointer}
.seg span{display:inline-block;padding:7px 14px;border:1px solid var(--borde);border-radius:999px;background:var(--panel)}
.seg input:checked+span{background:var(--tinta);color:var(--panel);border-color:var(--tinta)}
.seg input:focus-visible+span{outline:3px solid var(--acento);outline-offset:2px}

label.campo{display:block;font-weight:600;margin:0 0 4px}
input[type=text],input[type=password],textarea{width:100%;border:1px solid var(--borde);background:var(--panel);border-radius:10px;padding:10px 12px}
textarea{resize:vertical;min-height:84px}
.contador{font-size:.8rem;color:var(--suave);text-align:right;margin-top:2px}
.contador.mal{color:var(--mal);font-weight:600}

/* ---- braille ---- */
.traduccion{display:flex;flex-wrap:wrap;gap:10px 6px;margin-top:12px;min-height:60px}
.tc{margin:0;display:flex;flex-direction:column;align-items:center;gap:2px;min-width:32px}
.tc figcaption{font-size:.8rem;color:var(--suave)}
.tc .celda{--d:9px;--g:4px}
.uni{font-size:1.6rem;letter-spacing:.08em;overflow-wrap:anywhere;margin-top:8px}
.perkins{display:flex;gap:22px;align-items:center;flex-wrap:wrap}
.celda-edit{display:grid;grid-template-columns:repeat(2,64px);gap:12px;padding:14px;background:var(--panel2);border-radius:18px}
.pd{width:64px;height:64px;border-radius:50%;border:2px solid var(--borde);background:var(--panel);cursor:pointer;font-weight:700;color:var(--suave);touch-action:manipulation}
.pd[aria-pressed="true"]{background:var(--on);border-color:var(--on);color:var(--panel)}
.perk-car{font-size:3rem;font-weight:800;line-height:1}
.escrito{margin-top:14px;padding:10px 12px;border-radius:10px;background:var(--panel2);display:flex;flex-wrap:wrap;align-items:center;gap:8px}
.escrito output{font:700 1.2rem ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;letter-spacing:.06em;overflow-wrap:anywhere;flex:1;min-width:120px}
.signos{display:grid;gap:8px;grid-template-columns:repeat(auto-fill,minmax(84px,1fr));margin-top:10px}
.sig{border:1px solid var(--borde);background:var(--panel);border-radius:12px;padding:8px 4px;display:flex;flex-direction:column;align-items:center;gap:4px;cursor:pointer}
.sig b{font-size:1.25rem}
.sig small{color:var(--suave);font-size:.72rem}
.sig .celda{--d:9px;--g:4px}

/* ---- señas ---- */
.escenario{display:grid;grid-template-columns:auto 1fr;gap:6px 18px;align-items:center;padding:14px;border-radius:14px;background:var(--panel2);margin-top:12px}
.dl-letra{font-size:4rem;font-weight:800;line-height:1;text-align:center;min-width:72px}
.dl-desc{grid-column:1/-1;margin:0;font-size:1.1rem}
.dl-palabra{grid-column:1/-1;display:flex;flex-wrap:wrap;gap:4px}
.dl-palabra span{padding:2px 8px;border-radius:8px;background:var(--panel);font-weight:700}
.dl-palabra span.ahora{background:var(--acento);color:var(--acento-t)}
.senas{display:grid;gap:12px;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));margin-top:12px}
.sena{border:1px solid var(--borde);border-radius:14px;padding:12px;background:var(--panel);display:flex;flex-direction:column;gap:8px}
.sena-cab{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.sena-cab .letra{font-size:1.8rem;font-weight:800;min-width:34px}
.sena .desc{margin:0}
.sena .acc{display:flex;gap:6px;flex-wrap:wrap;margin-top:auto}

/* ---- progreso ---- */
.stats{display:grid;gap:10px;grid-template-columns:repeat(auto-fill,minmax(140px,1fr))}
.stat{border:1px solid var(--borde);border-radius:12px;padding:10px 12px;background:var(--panel)}
.stat b{display:block;font-size:1.5rem;line-height:1.2}
.stat span{color:var(--suave);font-size:.82rem}
.prog{display:grid;gap:8px;grid-template-columns:repeat(auto-fill,minmax(76px,1fr));margin-top:12px}
.pl{border:1px solid var(--borde);border-radius:12px;padding:8px 4px 6px;display:flex;flex-direction:column;align-items:center;gap:3px;background:var(--panel);border-top-width:5px}
.pl b{font-size:1.2rem}
.pl small{font-size:.72rem;color:var(--suave)}
.pl .celda{--d:7px;--g:3px}
.pl.bien{border-top-color:var(--ok)}
.pl.medio{border-top-color:var(--medio)}
.pl.mal{border-top-color:var(--mal)}
.pl.visto{border-top-color:var(--verde)}
.leyenda{display:flex;flex-wrap:wrap;gap:12px;font-size:.8rem;color:var(--suave);margin-top:10px}
.leyenda i{display:inline-block;width:12px;height:12px;border-radius:3px;margin-right:4px;vertical-align:-1px}

/* ---- ajustes ---- */
.medidor{position:relative;height:54px;border-radius:10px;background:var(--panel2);border:1px solid var(--borde);overflow:hidden;margin:8px 0 4px}
.medidor .r{position:absolute;top:8px;bottom:8px;background:var(--rango);border:1px solid var(--verde);border-radius:6px;display:flex;align-items:center;justify-content:center;font-size:.66rem;font-weight:700;color:var(--verde);white-space:nowrap}
.medidor .rep{position:absolute;top:0;bottom:0;width:0;border-left:2px dashed var(--suave)}
.medidor .aguja{position:absolute;top:0;bottom:0;width:4px;margin-left:-2px;background:var(--acento);transition:left .35s}
.escala{display:flex;justify-content:space-between;font-size:.72rem;color:var(--suave)}
.fila-form{display:grid;gap:12px;grid-template-columns:1fr}
@media (min-width:600px){.fila-form{grid-template-columns:1fr 1fr}}
.ver-clave{display:flex;align-items:center;gap:6px;font-size:.88rem;margin-top:6px;color:var(--suave)}
.caja-aviso{margin-top:12px;padding:12px;border-radius:12px;border:1px solid var(--acento)}

.aviso{position:fixed;left:50%;bottom:18px;transform:translate(-50%,24px);opacity:0;visibility:hidden;max-width:calc(100% - 32px);background:var(--tinta);color:var(--panel);padding:10px 16px;border-radius:12px;box-shadow:var(--sombra);transition:transform .25s,opacity .25s,visibility .25s;z-index:20;font-weight:600}
.aviso.ver{transform:translate(-50%,0);opacity:1;visibility:visible}
.aviso.mal{background:var(--mal);color:#fff}
.aviso.ok{background:var(--ok);color:#fff}
footer{max-width:1040px;margin:0 auto;padding:8px 16px 32px;color:var(--suave);font-size:.85rem;text-align:center}
footer .br{font-size:1.2rem;letter-spacing:.1em}
@media (max-width:520px){
  .cab{padding:12px 16px 6px}
  .logo{padding:7px 8px;gap:4px;border-radius:12px}
  .logo .celda{--d:4px;--g:2px}
  .marca h1{font-size:1.2rem}
  .pill{padding:8px}
  .pill span{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
  .pestanas button{padding:11px 9px 9px}
}
@media (max-width:420px){
  .celda-edit{grid-template-columns:repeat(2,56px)}
  .pd{width:56px;height:56px}
  .tecla small{font-size:.66rem}
}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
</style>
</head>
<body>
<header class="cab">
  <div class="marca">
    <div class="logo" id="logo" aria-hidden="true"></div>
    <div><h1>DOTLY</h1><p class="lema">Braille, punto a punto</p></div>
  </div>
  <div class="cab-der">
    <span class="pill" id="con" role="status"><span>Conectando…</span></span>
    <button class="icono" id="btnTema" type="button" aria-label="Cambiar tema">◐</button>
  </div>
</header>

<nav class="pestanas" aria-label="Secciones">
  <div role="tablist">
    <button role="tab" type="button" id="t-inicio" data-p="inicio" aria-controls="p-inicio" aria-selected="true">Inicio</button>
    <button role="tab" type="button" id="t-braille" data-p="braille" aria-controls="p-braille" aria-selected="false" tabindex="-1">Braille</button>
    <button role="tab" type="button" id="t-senas" data-p="senas" aria-controls="p-senas" aria-selected="false" tabindex="-1">Señas</button>
    <button role="tab" type="button" id="t-modos" data-p="modos" aria-controls="p-modos" aria-selected="false" tabindex="-1">Modos</button>
    <button role="tab" type="button" id="t-progreso" data-p="progreso" aria-controls="p-progreso" aria-selected="false" tabindex="-1">Progreso</button>
    <button role="tab" type="button" id="t-ajustes" data-p="ajustes" aria-controls="p-ajustes" aria-selected="false" tabindex="-1">Ajustes</button>
  </div>
</nav>

<main>
<noscript><p class="tarjeta">Esta página necesita JavaScript para hablar con DOTLY.</p></noscript>

<!-- ===================== INICIO ===================== -->
<section id="p-inicio" role="tabpanel" aria-labelledby="t-inicio">
  <div class="col2">
    <div class="tarjeta">
      <div class="tit"><h2>Pantalla de DOTLY</h2><span class="etq acento" id="modoTxt">—</span></div>
      <p class="aviso-cal" id="avisoCal" hidden>DOTLY está calibrando el teclado: mantén pulsado cada botón que pida su pantalla.</p>
      <div class="lcd" id="lcd" role="img" aria-label="Pantalla de DOTLY">
        <div class="fila"></div><div class="fila"></div>
      </div>
      <p class="ayuda" id="ayuda">Conectando con DOTLY…</p>
      <div class="teclado" role="group" aria-label="Botones de DOTLY">
        <button class="tecla" type="button" data-b="1"><b>●</b><small>SW1<br>Entrar</small></button>
        <button class="tecla" type="button" data-b="2"><b>◀</b><small>SW2<br>Anterior</small></button>
        <button class="tecla" type="button" data-b="3"><b>▶</b><small>SW3<br>Siguiente</small></button>
        <button class="tecla" type="button" data-b="4"><b>↩</b><small>SW4<br>Volver</small></button>
        <button class="tecla" type="button" data-b="5"><b>↻</b><small>SW5<br>Repetir</small></button>
      </div>
      <p class="nota">Con el teclado del ordenador: Intro entra, ← → anterior y siguiente, Esc vuelve, R repite.</p>
    </div>
    <div class="pila">
      <div class="tarjeta">
        <h2>Empezar un modo</h2>
        <p class="suave" style="margin-top:4px">DOTLY cambia de modo al momento. Todo lo que hagas aquí lo verás también en su pantalla.</p>
        <div class="rapidos" id="rapidos"></div>
        <p class="nota" id="retoInfo" hidden></p>
      </div>
      <div class="tarjeta">
        <h2 style="margin-bottom:10px">Estado</h2>
        <dl class="datos">
          <dt>Teclado</dt><dd id="dTeclado">—</dd>
          <dt>Red WiFi</dt><dd id="dRed">—</dd>
          <dt>Encendido</dt><dd id="dEncendido">—</dd>
        </dl>
      </div>
    </div>
  </div>
</section>

<!-- ===================== BRAILLE ===================== -->
<section id="p-braille" role="tabpanel" aria-labelledby="t-braille" hidden>
  <div class="col2">
    <div class="pila">
      <div class="tarjeta">
        <div class="tit"><h2>Traductor</h2><span class="etq" id="txCuenta">0 celdas</span></div>
        <label class="campo" for="tx">Escribe un texto</label>
        <textarea id="tx" maxlength="200" placeholder="Hola, me llamo Ana y tengo 12 años">Hola DOTLY</textarea>
        <div class="traduccion" id="txCeldas" aria-live="polite"></div>
        <div class="uni" id="txUni" aria-label="Texto en braille Unicode"></div>
        <div class="botones">
          <button class="btn" type="button" id="txMostrar">Mostrar en DOTLY</button>
          <button class="btn sec" type="button" id="txCopiar">Copiar braille</button>
        </div>
        <p class="nota">Las mayúsculas llevan delante su signo (puntos 4-6) y cada número el signo de número (puntos 3-4-5-6), como en la signografía española.</p>
      </div>
      <div class="tarjeta">
        <h2>Signo generador</h2>
        <p class="suave" style="margin-top:4px">Toca un signo para verlo en grande en la máquina de escribir.</p>
        <div class="chips" id="bloquesChips" role="group" aria-label="Bloques"></div>
        <div class="signos" id="signos"></div>
        <div class="botones"><button class="btn sec" type="button" id="verBloque">Ver este bloque en DOTLY</button></div>
      </div>
    </div>
    <div class="tarjeta" id="perkinsTarjeta">
      <h2>Máquina de escribir braille</h2>
      <p class="suave" style="margin-top:4px">Marca los puntos y escribe la letra en DOTLY, como en una máquina Perkins.</p>
      <div class="perkins">
        <div class="celda-edit" role="group" aria-label="Puntos de la celda" id="perkPuntos"></div>
        <div>
          <div class="perk-car" id="perkCar" aria-live="polite">␣</div>
          <div id="perkQue" class="suave">espacio</div>
          <div id="perkPts" class="suave">Ningún punto</div>
        </div>
      </div>
      <div class="botones">
        <button class="btn" type="button" id="perkEscribir">Escribir en DOTLY</button>
        <button class="btn sec" type="button" id="perkEspacio">Espacio</button>
        <button class="btn sec" type="button" id="perkBorrar">Borrar letra</button>
        <button class="btn sec" type="button" id="perkLimpiar">Soltar puntos</button>
      </div>
      <div class="escrito"><span class="suave">En DOTLY:</span><output id="escrito">—</output><button class="btn sec peq" type="button" id="escritoBorrar">Borrar todo</button></div>
      <p class="nota">Con el teclado del ordenador, como una Perkins: F D S son los puntos 1 2 3 y J K L los puntos 4 5 6. Púlsalas a la vez y suéltalas para escribir la letra. Espacio escribe un espacio y Retroceso borra.</p>
    </div>
  </div>
</section>

<!-- ===================== SEÑAS ===================== -->
<section id="p-senas" role="tabpanel" aria-labelledby="t-senas" hidden>
  <div class="tarjeta">
    <h2>Deletrear con las manos</h2>
    <p class="suave" style="margin-top:4px">Escribe una palabra y DOTLY la deletrea letra a letra: cada una con su seña y su celda braille.</p>
    <div class="fila-form">
      <div><label class="campo" for="dlTxt">Palabra</label><input type="text" id="dlTxt" maxlength="40" value="hola" autocomplete="off"></div>
      <div class="botones" style="align-items:flex-end;margin-top:0">
        <button class="btn" type="button" id="dlAqui">Deletrear aquí</button>
        <button class="btn sec" type="button" id="dlDotly">Deletrear en DOTLY</button>
      </div>
    </div>
    <div class="escenario" id="dlEsc" hidden>
      <div class="dl-letra" id="dlLetra"></div>
      <div id="dlCelda"></div>
      <p class="dl-desc" id="dlDesc" aria-live="polite"></p>
      <div class="dl-palabra" id="dlPalabra"></div>
      <div class="botones" style="grid-column:1/-1;margin-top:4px">
        <button class="btn sec peq" type="button" id="dlAnt" aria-label="Letra anterior">◀</button>
        <button class="btn sec peq" type="button" id="dlPlay">Pausa</button>
        <button class="btn sec peq" type="button" id="dlSig" aria-label="Letra siguiente">▶</button>
      </div>
    </div>
  </div>
  <div class="tarjeta">
    <div class="tit"><h2>Alfabeto manual</h2><button class="btn sec peq" type="button" id="retoSenas">Hacer el reto de señas</button></div>
    <p class="suave">Las descripciones de partida siguen el alfabeto manual más extendido, y cada país tiene el suyo (LSE, LSM, LSA…). Cámbialas por las de tu lengua de señas: se guardan en DOTLY y las usan el modo Señas y su reto.</p>
    <div class="senas" id="senasLista"><p class="vacio">Cargando…</p></div>
  </div>
</section>

<!-- ===================== MODOS ===================== -->
<section id="p-modos" role="tabpanel" aria-labelledby="t-modos" hidden>
  <div class="tarjeta">
    <h2>Modos de aprendizaje</h2>
    <p class="suave" style="margin-top:4px">Elige uno y DOTLY lo abre. El marcado es el que está usando ahora.</p>
    <div class="modos" id="modos"></div>
  </div>
  <div class="col2">
    <div class="tarjeta">
      <h2 style="margin-bottom:12px">Cómo son los retos</h2>
      <fieldset class="seg"><legend>Preguntas por ronda</legend><div class="ops">
        <label><input type="radio" name="preguntas" value="5"><span>5</span></label>
        <label><input type="radio" name="preguntas" value="10"><span>10</span></label>
        <label><input type="radio" name="preguntas" value="20"><span>20</span></label>
      </div></fieldset>
      <fieldset class="seg"><legend>Letras que entran</legend><div class="ops">
        <label><input type="radio" name="nivel" value="1"><span>a – j</span></label>
        <label><input type="radio" name="nivel" value="2"><span>a – t</span></label>
        <label><input type="radio" name="nivel" value="3"><span>abecedario</span></label>
        <label><input type="radio" name="nivel" value="4"><span>+ tildes</span></label>
      </div></fieldset>
      <fieldset class="seg"><legend>Velocidad del texto y del deletreo</legend><div class="ops">
        <label><input type="radio" name="velocidad" value="0"><span>lenta</span></label>
        <label><input type="radio" name="velocidad" value="1"><span>media</span></label>
        <label><input type="radio" name="velocidad" value="2"><span>rápida</span></label>
      </div></fieldset>
      <p class="nota" style="margin-top:0">Los retos sacan más a menudo las letras que más se fallan, y en el de leer las opciones falsas se parecen a la buena: hay que fijarse en cada punto.</p>
    </div>
    <div class="tarjeta">
      <h2>Palabras</h2>
      <p class="suave" style="margin-top:4px">Las que salen en el modo Palabras, separadas por comas.</p>
      <textarea id="palabras" rows="6" aria-describedby="palabrasCuenta"></textarea>
      <div class="contador" id="palabrasCuenta"></div>
      <div class="botones">
        <button class="btn" type="button" id="palabrasGuardar">Guardar lista</button>
        <button class="btn sec" type="button" id="palabrasRestaurar">Volver a la lista de DOTLY</button>
      </div>
    </div>
  </div>
</section>

<!-- ===================== PROGRESO ===================== -->
<section id="p-progreso" role="tabpanel" aria-labelledby="t-progreso" hidden>
  <div class="tarjeta">
    <div class="tit"><h2>Cómo vas</h2><button class="btn sec peq" type="button" id="progAct">Actualizar</button></div>
    <div class="stats" id="stats"></div>
  </div>
  <div class="tarjeta">
    <h2>Letra a letra</h2>
    <p class="suave" style="margin-top:4px" id="repasar"></p>
    <div class="leyenda">
      <span><i style="background:var(--ok)"></i>80 % o más</span>
      <span><i style="background:var(--medio)"></i>50–79 %</span>
      <span><i style="background:var(--mal)"></i>menos del 50 %</span>
      <span><i style="background:var(--verde)"></i>vista, sin retos</span>
      <span><i style="background:var(--borde)"></i>sin empezar</span>
    </div>
    <div class="prog" id="progGrid"></div>
    <div class="botones"><button class="btn peligro" type="button" id="progBorrar">Borrar progreso</button></div>
  </div>
</section>

<!-- ===================== AJUSTES ===================== -->
<section id="p-ajustes" role="tabpanel" aria-labelledby="t-ajustes" hidden>
  <div class="col2">
    <div class="pila">
      <div class="tarjeta">
        <h2 style="margin-bottom:12px">Apariencia</h2>
        <fieldset class="seg"><legend>Tema</legend><div class="ops">
          <label><input type="radio" name="tema" value="auto"><span>Automático</span></label>
          <label><input type="radio" name="tema" value="claro"><span>Claro</span></label>
          <label><input type="radio" name="tema" value="oscuro"><span>Oscuro</span></label>
        </div></fieldset>
        <fieldset class="seg" style="margin-bottom:0"><legend>Tamaño del texto</legend><div class="ops">
          <label><input type="radio" name="tam" value="0.9"><span style="font-size:.85rem">A</span></label>
          <label><input type="radio" name="tam" value="1"><span>A</span></label>
          <label><input type="radio" name="tam" value="1.15"><span style="font-size:1.15rem">A</span></label>
          <label><input type="radio" name="tam" value="1.3"><span style="font-size:1.3rem">A</span></label>
        </div></fieldset>
      </div>
      <div class="tarjeta">
        <div class="tit" style="margin-bottom:4px"><h2>Teclado</h2><span class="etq" id="tecEstado">—</span></div>
        <p class="suave">Los cinco botones van por un solo cable: cada uno da una tensión distinta. Aquí se ve la que llega ahora y el rango que DOTLY tiene apuntado para cada botón.</p>
        <div class="medidor" id="medidor" role="img" aria-label="Tensión del teclado"></div>
        <div class="escala"><span>0 V</span><span>1,1 V</span><span>2,2 V</span><span>3,3 V</span></div>
        <dl class="datos" style="margin-top:12px">
          <dt>Ahora</dt><dd id="tecMv">—</dd>
          <dt>En reposo</dt><dd id="tecReposo">—</dd>
          <dt>Calibración</dt><dd id="tecCal">—</dd>
        </dl>
        <div class="botones"><button class="btn" type="button" id="calibrar">Calibrar el teclado</button></div>
        <p class="nota">DOTLY pide en su pantalla que mantengas pulsado cada botón (SW1 a SW5) y guarda lo que mide. También se abre encendiendo DOTLY con un botón pulsado.</p>
      </div>
    </div>
    <div class="pila">
      <div class="tarjeta">
        <h2>Red WiFi de DOTLY</h2>
        <p class="suave" style="margin-top:4px">DOTLY crea su propia red. Si le cambias el nombre o la clave, hay que reiniciarlo y volver a conectarse.</p>
        <form id="formRed" class="fila-form" autocomplete="off">
          <div><label class="campo" for="ssid">Nombre de la red</label><input type="text" id="ssid" maxlength="32" required></div>
          <div><label class="campo" for="clave">Clave</label><input type="password" id="clave" maxlength="63">
            <label class="ver-clave"><input type="checkbox" id="verClave"> Mostrar la clave</label></div>
          <div class="botones" style="grid-column:1/-1;margin-top:0"><button class="btn" type="submit">Guardar red</button></div>
        </form>
        <p class="nota">De 8 a 63 caracteres. Déjala vacía para una red abierta (cualquiera podrá conectarse).</p>
        <div class="caja-aviso" id="reiniciarCaja" hidden>
          <p><b>Red guardada.</b> Se usará al reiniciar DOTLY.</p>
          <button class="btn" type="button" id="reiniciar">Reiniciar ahora</button>
        </div>
      </div>
      <div class="tarjeta">
        <h2>Acerca de DOTLY</h2>
        <p class="suave" style="margin-top:6px">DOTLY enseña a leer y escribir braille, y el alfabeto manual de las lenguas de señas, con una pantalla, cinco botones y esta página. Funciona sin internet: la página la sirve el propio equipo.</p>
        <p class="suave">El braille es la signografía española: abecedario con ñ, vocales con tilde, números y signos de puntuación.</p>
      </div>
    </div>
  </div>
</section>
</main>

<footer><div class="br" aria-hidden="true">⠙⠕⠞⠇⠽</div>DOTLY · Braille, punto a punto</footer>
<div class="aviso" id="aviso" role="status" aria-live="polite"></div>

<script>
'use strict';
const $=(s,r)=>(r||document).querySelector(s);
const $$=(s,r)=>Array.from((r||document).querySelectorAll(s));

// ---------- utilidades ----------
const ORDEN=[0,3,1,4,2,5];      // posicion en la rejilla: puntos 1 4 / 2 5 / 3 6
function celda(p,cls){
  let h='<span class="celda'+(cls?' '+cls:'')+'" aria-hidden="true">';
  for(const d of ORDEN) h+='<i'+((p>>d)&1?' class="on"':'')+'></i>';
  return h+'</span>';
}
function puntosTxt(p){const a=[];for(let d=0;d<6;d++)if((p>>d)&1)a.push(d+1);return a.length?a.join('-'):'ninguno';}
function esc(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function bytes(s){return new TextEncoder().encode(s).length;}
function leerL(k){try{return localStorage.getItem('dotly.'+k);}catch(e){return null;}}
function guardarL(k,v){try{localStorage.setItem('dotly.'+k,v);}catch(e){}}

async function api(ruta,datos){
  const op={cache:'no-store'};
  if(datos){op.method='POST';op.body=new URLSearchParams(datos);}
  const r=await fetch(ruta,op);
  let j=null;
  try{j=await r.json();}catch(e){}
  if(!r.ok) throw new Error((j&&j.error)||('DOTLY respondió '+r.status));
  return j;
}
function aviso(txt,tipo){
  const a=$('#aviso');
  a.textContent=txt;a.className='aviso ver'+(tipo?' '+tipo:'');
  clearTimeout(aviso.t);aviso.t=setTimeout(()=>{a.className='aviso';},2800);
}
async function mandar(ruta,datos,ok){
  try{const j=await api(ruta,datos);if(ok)aviso(ok,'ok');refrescar(120);return j||{};}
  catch(e){aviso(e.message,'mal');refrescar(120);return null;}
}

// ---------- tabla braille (la manda DOTLY) ----------
let T=null;
const POR_CAR={},POR_PUNTOS={},NUM_PUNTOS={};
const TIPO_TXT={letra:'letra',acento:'vocal con tilde',numero:'número',signo:'signo de puntuación'};
async function cargarTabla(){
  if(T) return;
  try{T=await api('/api/tabla');}catch(e){return;}
  T.signos.forEach((s,i)=>{
    s.i=i;
    const cars=s.t==='¿?'?['¿','?']:s.t==='¡!'?['¡','!']:[s.t];
    cars.forEach(c=>{POR_CAR[c]=s;});
    if(s.k==='numero'){if(!(s.p in NUM_PUNTOS))NUM_PUNTOS[s.p]=s;}
    else if(!(s.p in POR_PUNTOS))POR_PUNTOS[s.p]=s;
  });
  pintarSignos();pintarModos();traducirVista();perkPintar();
  if(SENAS.length) pintarSenas();
}

// Texto -> celdas, con las mismas reglas que el firmware.
function traducir(txt){
  const out=[];let num=false;
  for(const ch of Array.from(txt)){
    if(/\s/.test(ch)){out.push({p:0,l:' '});num=false;continue;}
    const lo=ch.toLowerCase(),s=POR_CAR[lo];
    if(!s) continue;
    if(s.k==='numero'){
      if(!num){out.push({p:T.numero,l:'#',x:'signo de número'});num=true;}
      out.push({p:s.p,l:ch});continue;
    }
    num=false;
    if(ch!==lo&&s.k!=='signo') out.push({p:T.mayuscula,l:'↑',x:'signo de mayúscula'});
    out.push({p:s.p,l:ch});
  }
  return out;
}
function significado(p,num){
  if(!T) return {c:'',d:''};
  if(p===0) return {c:'␣',d:'espacio'};
  if(p===T.numero) return {c:'#',d:'signo de número'};
  if(p===T.mayuscula) return {c:'↑',d:'signo de mayúscula'};
  if(num&&NUM_PUNTOS[p]) return {c:NUM_PUNTOS[p].t,d:'número'};
  const s=POR_PUNTOS[p];
  if(s) return {c:s.t.toUpperCase(),d:TIPO_TXT[s.k]};
  return {c:'?',d:'no es ninguna letra'};
}

// ---------- pestañas ----------
let pestana='inicio';
function abrir(p,foco){
  pestana=p;
  $$('[role=tab]').forEach(b=>{const s=b.dataset.p===p;b.setAttribute('aria-selected',s);b.tabIndex=s?0:-1;if(s&&foco)b.focus();});
  $$('main>section').forEach(s=>{s.hidden=s.id!=='p-'+p;});
  guardarL('pestana',p);
  if(p==='senas') cargarSenas(false);
  if(p==='modos'){cargarAjustes();cargarPalabras();}
  if(p==='progreso') cargarProgreso();
  if(p==='ajustes') cargarAjustes();
}
$$('[role=tab]').forEach(b=>b.addEventListener('click',()=>abrir(b.dataset.p)));
$('[role=tablist]').addEventListener('keydown',e=>{
  const tabs=$$('[role=tab]'),i=tabs.findIndex(b=>b.dataset.p===pestana);
  let n=-1;
  if(e.key==='ArrowRight')n=(i+1)%tabs.length;
  if(e.key==='ArrowLeft')n=(i+tabs.length-1)%tabs.length;
  if(e.key==='Home')n=0;
  if(e.key==='End')n=tabs.length-1;
  if(n>=0){e.preventDefault();abrir(tabs[n].dataset.p,true);}
});

// ---------- tema y tamaño ----------
const TEMAS=['auto','claro','oscuro'];
function aplicarTema(t){
  if(!TEMAS.includes(t)) t='auto';
  const h=document.documentElement;
  if(t==='auto') h.removeAttribute('data-tema'); else h.setAttribute('data-tema',t);
  guardarL('tema',t);
  $$('input[name=tema]').forEach(r=>{r.checked=r.value===t;});
  const b=$('#btnTema');
  b.textContent=t==='claro'?'☀':t==='oscuro'?'☾':'◐';
  b.setAttribute('aria-label','Tema '+(t==='auto'?'automático':t)+'. Pulsa para cambiarlo');
  const oscuro=t==='oscuro'||(t==='auto'&&window.matchMedia&&matchMedia('(prefers-color-scheme: dark)').matches);
  $('meta[name=theme-color]').setAttribute('content',oscuro?'#10141d':'#f5f1e8');
}
function aplicarTam(v){
  v=[0.9,1,1.15,1.3].includes(v)?v:1;
  document.documentElement.style.setProperty('--esc',v);
  guardarL('tam',v);
  $$('input[name=tam]').forEach(r=>{r.checked=+r.value===v;});
}
$('#btnTema').addEventListener('click',()=>{
  const actual=leerL('tema')||'auto';
  aplicarTema(TEMAS[(TEMAS.indexOf(actual)+1)%TEMAS.length]);
});
$$('input[name=tema]').forEach(r=>r.addEventListener('change',()=>aplicarTema(r.value)));
$$('input[name=tam]').forEach(r=>r.addEventListener('change',()=>aplicarTam(+r.value)));

// ---------- estado de DOTLY ----------
const MODO_NOMBRE={bienvenida:'Arrancando',menu:'Menú principal',bloques:'Aprender · bloques',bloque:'Aprender · bloque',
  letra:'Aprender · letra a letra',escribir:'Escribir',reto_leer:'Reto: leer',reto_formar:'Reto: formar',reto_senas:'Reto: señas',
  resumen:'Resultado del reto',palabras:'Palabras',senas:'Señas',progreso:'Progreso',ajustes:'Ajustes',red:'Ajustes · red WiFi',
  confirmar:'Ajustes · borrar progreso',acerca:'Acerca de DOTLY',pizarra:'Pizarra',calibrar:'Calibrando el teclado'};
const AYUDA={
  menu:'◀ ▶ elige un modo · Entrar lo abre',
  bloques:'◀ ▶ elige el bloque · Entrar lo abre · Volver al menú',
  bloque:'Entrar: letra a letra · Repetir: lo vuelve a mostrar · Volver: bloques',
  letra:'◀ ▶ letra anterior o siguiente · Volver: el bloque',
  escribir:'◀ ▶ mueve el cursor · Entrar marca el punto (sobre «<» borra la última letra) · Repetir escribe la letra · Volver sale',
  reto_leer:'◀ ▶ elige la letra de la celda · Entrar responde · Volver sale',
  reto_formar:'◀ ▶ mueve el cursor · Entrar marca el punto · Repetir comprueba · Volver sale',
  reto_senas:'Lee cómo se hace la seña · ◀ ▶ elige la letra · Entrar responde · Repetir vuelve a leerla',
  resumen:'Entrar: otra ronda · Volver: al menú',
  palabras:'Entrar destapa la palabra (otra vez: la siguiente) · ◀ ▶ cambia · Repetir la vuelve a tapar',
  senas:'◀ ▶ letra anterior o siguiente · Repetir vuelve a leer la seña · Volver sale',
  progreso:'◀ ▶ cambia de página · Volver sale',
  ajustes:'◀ ▶ elige · Entrar cambia o abre · Volver sale',
  red:'◀ ▶ nombre, clave y conectados · Volver sale',
  confirmar:'Entrar borra todo el progreso · cualquier otro botón cancela',
  acerca:'Cualquier botón vuelve',
  pizarra:'◀ ▶ cambia de pantalla · Volver sale',
  calibrar:'Mantén pulsado cada botón que pida la pantalla hasta que pase al siguiente',
  bienvenida:''};
const MODO_DE={bloques:'aprender',bloque:'aprender',letra:'aprender',escribir:'escribir',reto_leer:'reto_leer',
  reto_formar:'reto_formar',reto_senas:'reto_senas',palabras:'palabras',senas:'senas',progreso:'progreso',
  ajustes:'ajustes',red:'ajustes',confirmar:'ajustes',acerca:'ajustes'};

let E=null,tRef=null,pidiendo=false,lcdClave='',conectado=null;
function refrescar(ms){clearTimeout(tRef);tRef=setTimeout(pedirEstado,ms||0);}
async function pedirEstado(){
  if(pidiendo) return;
  pidiendo=true;
  try{E=await api('/api/estado');conexion(true);pintarEstado(E);if(!T)cargarTabla();}
  catch(e){conexion(false);}
  finally{pidiendo=false;}
}
function conexion(ok){
  if(ok===conectado) return;
  conectado=ok;
  const c=$('#con');
  c.className='pill '+(ok?'ok':'mal');
  c.firstChild.textContent=ok?'Conectado':'Sin conexión';
  c.title=c.firstChild.textContent;
  if(!ok) $('#ayuda').textContent='No llega respuesta de DOTLY. ¿Sigue encendido y este aparato conectado a su red WiFi?';
}
function pintarLcd(e){
  const clave=JSON.stringify([e.pantalla,e.cursor]);
  if(clave===lcdClave) return;
  lcdClave=clave;
  const filas=$$('#lcd .fila');
  for(let f=0;f<2;f++){
    const cs=Array.from(e.pantalla[f]||'');
    let h='';
    for(let c=0;c<16;c++){
      const ch=cs[c]||' ',cp=ch.codePointAt(0);
      const cur=e.cursor&&e.cursor[0]===c&&e.cursor[1]===f?' cur':'';
      if(cp>=0x2800&&cp<=0x283F) h+='<span class="lc'+cur+'">'+celda(cp-0x2800)+'</span>';
      else if(cp===0x2588) h+='<span class="lc lleno'+cur+'"></span>';
      else h+='<span class="lc'+cur+'">'+(ch===' '?'':esc(ch))+'</span>';
    }
    filas[f].innerHTML=h;
  }
  $('#lcd').setAttribute('aria-label','Pantalla de DOTLY: '+e.pantalla.map(l=>l.trim()).join(' / '));
}
function duracion(s){
  const h=Math.floor(s/3600),m=Math.floor(s%3600/60);
  return h?h+' h '+m+' min':m?m+' min':s+' s';
}
function pintarEstado(e){
  pintarLcd(e);
  $('#modoTxt').textContent=MODO_NOMBRE[e.modo]||e.modo;
  $('#ayuda').textContent=AYUDA[e.modo]||'';
  $('#avisoCal').hidden=e.modo!=='calibrar';
  const k=e.teclado;
  $('#dTeclado').textContent=k.conectado?('Conectado · '+k.botones+' botones'):'No detectado · usa los botones de esta página';
  $('#dRed').textContent=e.red.ssid+' · '+e.red.clientes+(e.red.clientes===1?' aparato':' aparatos');
  $('#dEncendido').textContent=duracion(e.encendido);
  const enReto=/^reto_/.test(e.modo);
  $('#retoInfo').hidden=!enReto;
  if(enReto) $('#retoInfo').textContent='Pregunta '+e.reto.pregunta+' de '+e.reto.total+' · '+e.reto.aciertos+' aciertos · racha '+e.reto.racha;
  $('#escrito').textContent=e.escrito||'—';
  if(e.escritoNumero!==perkNumero){perkNumero=e.escritoNumero;perkPintar();}
  const activo=MODO_DE[e.modo]||'';
  $$('[data-modo]').forEach(el=>{
    const si=el.dataset.modo===activo;
    el.classList.toggle('activo',si);
    const t=el.querySelector('.en-marcha');if(t)t.hidden=!si;
  });
  if(pestana==='ajustes') pintarTeclado(k);
}

// ---------- teclado virtual ----------
async function tecla(b){
  const btn=$('.tecla[data-b="'+b+'"]');
  if(btn){btn.classList.add('pulsada');setTimeout(()=>btn.classList.remove('pulsada'),160);}
  await mandar('/api/tecla',{b:b});
}
$$('.tecla').forEach(b=>b.addEventListener('click',()=>tecla(+b.dataset.b)));

// ---------- modos ----------
const MODOS=[
  {m:'aprender',t:'Aprender',d:'El signo generador por bloques: abecedario, vocales con tilde, números y signos. Letra a letra, con sus puntos.',k:'◀ ▶ bloque · Entrar abre · Entrar otra vez: letra a letra'},
  {m:'escribir',t:'Escribir',d:'Máquina de escribir braille: marca los puntos de cada letra y DOTLY te dice cuál has formado.',k:'◀ ▶ punto · Entrar marca · Repetir escribe'},
  {m:'reto_leer',t:'Reto: leer',d:'DOTLY enseña una celda y eliges su letra entre cuatro parecidas.',k:'◀ ▶ elige · Entrar responde'},
  {m:'reto_formar',t:'Reto: formar',d:'DOTLY pide una letra y la formas punto a punto.',k:'◀ ▶ punto · Entrar marca · Repetir comprueba'},
  {m:'palabras',t:'Palabras',d:'Lee palabras enteras en braille y destápalas para comprobar.',k:'Entrar destapa · ◀ ▶ otra palabra'},
  {m:'senas',t:'Señas',d:'El alfabeto manual letra a letra: cómo se hace cada seña y su celda braille.',k:'◀ ▶ letra · Repetir vuelve a leer'},
  {m:'reto_senas',t:'Reto: señas',d:'DOTLY describe una seña y adivinas de qué letra es.',k:'◀ ▶ elige · Entrar responde · Repetir relee'},
  {m:'progreso',t:'Progreso',d:'Aciertos, rachas, letras vistas y las que más cuestan.',k:'◀ ▶ página'},
  {m:'ajustes',t:'Ajustes',d:'Calibrar el teclado, red WiFi, preguntas, velocidad y nivel.',k:'◀ ▶ elige · Entrar cambia'}];
function pintarModos(){
  $('#rapidos').innerHTML=MODOS.map(m=>'<button class="chip" type="button" data-ir="'+m.m+'" data-modo="'+m.m+'">'+esc(m.t)+'</button>').join('');
  const chips=T?T.bloques.map((b,i)=>'<button class="chip" type="button" data-ir="aprender" data-b="'+i+'">'+esc(b.n)+'</button>').join(''):'';
  $('#modos').innerHTML=MODOS.map(m=>'<article class="modo" data-modo="'+m.m+'"><div class="tit" style="margin:0"><h3>'+esc(m.t)+
    '</h3><span class="etq acento en-marcha" hidden>En marcha</span></div><p>'+esc(m.d)+'</p>'+
    (m.m==='aprender'&&chips?'<div class="chips">'+chips+'</div>':'')+
    '<div class="teclas">'+esc(m.k)+'</div><button class="btn peq" type="button" data-ir="'+m.m+'">Empezar en DOTLY</button></article>').join('');
  if(E) pintarEstado(E);
}
document.addEventListener('click',e=>{
  const b=e.target.closest('[data-ir]');
  if(!b) return;
  const d={m:b.dataset.ir};
  if(b.dataset.b!==undefined) d.b=b.dataset.b;
  mandar('/api/modo',d,'DOTLY: '+(MODO_NOMBRE[d.m==='aprender'?'bloques':d.m]||d.m));
});

// ---------- traductor ----------
function traducirVista(){
  const cs=T?traducir($('#tx').value):[];
  $('#txCeldas').innerHTML=cs.length?cs.map(c=>'<figure class="tc">'+celda(c.p)+'<figcaption>'+
    (c.x?'<abbr title="'+c.x+'">'+c.l+'</abbr>':(c.l===' '?'␣':esc(c.l)))+'</figcaption></figure>').join('')
    :'<p class="vacio">Escribe algo y aparecerá aquí en braille.</p>';
  $('#txUni').textContent=cs.map(c=>String.fromCodePoint(0x2800+c.p)).join('');
  $('#txCuenta').textContent=cs.length+(cs.length===1?' celda':' celdas')+(cs.length>72?' · DOTLY enseña 72':'');
}
$('#tx').addEventListener('input',traducirVista);
$('#txMostrar').addEventListener('click',()=>{
  const t=$('#tx').value.trim();
  if(!t){aviso('Escribe algo primero','mal');return;}
  mandar('/api/pizarra',{t:t},'En la pantalla de DOTLY');
});
$('#txCopiar').addEventListener('click',async()=>{
  const t=$('#txUni').textContent;
  try{await navigator.clipboard.writeText(t);aviso('Braille copiado','ok');}
  catch(e){
    const r=document.createRange();r.selectNodeContents($('#txUni'));
    const s=getSelection();s.removeAllRanges();s.addRange(r);
    aviso('Seleccionado: cópialo con el menú del navegador');
  }
});

// ---------- signo generador ----------
let bloqueVista=0;
function pintarSignos(){
  if(!T) return;
  $('#bloquesChips').innerHTML=T.bloques.map((b,i)=>'<button class="chip" type="button" data-bv="'+i+'" aria-pressed="'+(i===bloqueVista)+'">'+esc(b.n)+'</button>').join('');
  const b=T.bloques[bloqueVista];
  $('#signos').innerHTML=T.signos.slice(b.desde,b.desde+b.cuantos).map(s=>
    '<button class="sig" type="button" data-s="'+s.i+'" aria-label="'+esc(s.t)+', puntos '+puntosTxt(s.p)+'"><b>'+esc(s.t.toUpperCase())+'</b>'+
    (s.k==='numero'?'<span>'+celda(T.numero)+celda(s.p)+'</span>':celda(s.p))+'<small>'+puntosTxt(s.p)+'</small></button>').join('');
}
$('#bloquesChips').addEventListener('click',e=>{
  const b=e.target.closest('[data-bv]');if(!b)return;
  bloqueVista=+b.dataset.bv;pintarSignos();
});
$('#signos').addEventListener('click',e=>{
  const b=e.target.closest('[data-s]');if(!b)return;
  const s=T.signos[+b.dataset.s];
  perk=s.p;perkPintar();
  aviso((s.k==='numero'?'Número '+s.t+': signo de número y ':s.t.toUpperCase()+': ')+'puntos '+puntosTxt(s.p));
  if(window.innerWidth<860) $('#perkinsTarjeta').scrollIntoView({behavior:'smooth',block:'start'});
});
$('#verBloque').addEventListener('click',()=>mandar('/api/modo',{m:'aprender',b:bloqueVista},'Bloque abierto en DOTLY'));

// ---------- máquina Perkins ----------
let perk=0,perkNumero=false,acorde=0;
const pulsadas=new Set();
const TECLAS_PERKINS={f:0,d:1,s:2,j:3,k:4,l:5};
$('#perkPuntos').innerHTML=ORDEN.map(d=>'<button class="pd" type="button" data-d="'+d+'" aria-pressed="false" aria-label="Punto '+(d+1)+'">'+(d+1)+'</button>').join('');
function perkPintar(){
  const p=acorde||perk;
  $$('.pd').forEach(b=>b.setAttribute('aria-pressed',(p>>+b.dataset.d)&1?'true':'false'));
  const s=significado(p,perkNumero);
  $('#perkCar').textContent=s.c;
  $('#perkQue').textContent=s.d;
  $('#perkPts').textContent=p?'Puntos '+puntosTxt(p):'Ningún punto';
}
$('#perkPuntos').addEventListener('click',e=>{
  const b=e.target.closest('.pd');if(!b)return;
  perk^=1<<+b.dataset.d;perkPintar();
});
async function escribirCelda(p){
  const j=await mandar('/api/escribir',{p:p});
  if(j){perk=0;perkPintar();}
}
$('#perkEscribir').addEventListener('click',()=>escribirCelda(perk));
$('#perkEspacio').addEventListener('click',()=>escribirCelda(0));
$('#perkBorrar').addEventListener('click',()=>mandar('/api/escribir',{borrar:1}));
$('#perkLimpiar').addEventListener('click',()=>{perk=0;perkPintar();});
$('#escritoBorrar').addEventListener('click',()=>mandar('/api/escrito/borrar',{},'Texto borrado'));

// ---------- teclas del ordenador ----------
function escribiendo(e){
  const t=e.target;
  if(e.ctrlKey||e.metaKey||e.altKey) return true;
  if(t.closest&&t.closest('input,textarea,select,[role=tablist]')) return true;
  if(!(t.closest&&t.closest('button'))) return false;
  // Dentro de la maquina de escribir, Espacio escribe un espacio aunque haya un
  // boton con el foco (si no, repetiria el ultimo boton pulsado).
  if(e.key===' '&&pestana==='braille'&&t.closest('#perkinsTarjeta')) return false;
  return e.key==='Enter'||e.key===' ';
}
document.addEventListener('keydown',e=>{
  if(escribiendo(e)) return;
  if(pestana==='braille'){
    const k=e.key.toLowerCase();
    if(k in TECLAS_PERKINS){
      e.preventDefault();
      if(!e.repeat){pulsadas.add(k);acorde|=1<<TECLAS_PERKINS[k];perkPintar();}
      return;
    }
    if(e.key===' '){e.preventDefault();escribirCelda(0);return;}
    if(e.key==='Backspace'){e.preventDefault();mandar('/api/escribir',{borrar:1});return;}
  }else if(pestana==='inicio'){
    const m={Enter:1,ArrowLeft:2,ArrowUp:2,ArrowRight:3,ArrowDown:3,Escape:4,Backspace:4,r:5,R:5};
    if(m[e.key]){e.preventDefault();tecla(m[e.key]);}
  }
});
document.addEventListener('keyup',e=>{
  const k=e.key.toLowerCase();
  if(!pulsadas.has(k)) return;
  pulsadas.delete(k);
  if(pulsadas.size===0&&acorde){const p=acorde;acorde=0;escribirCelda(p);}
});
window.addEventListener('blur',()=>{pulsadas.clear();acorde=0;perkPintar();});

// ---------- señas ----------
let SENAS=[];
async function cargarSenas(forzar){
  if(SENAS.length&&!forzar){pintarSenas();return;}
  try{SENAS=await api('/api/senas');pintarSenas();}
  catch(e){$('#senasLista').innerHTML='<p class="vacio">No se pudieron leer las señas: '+esc(e.message)+'</p>';}
}
function pintarSenas(){
  if(!T){$('#senasLista').innerHTML='<p class="vacio">Cargando…</p>';return;}
  $('#senasLista').innerHTML=SENAS.map((s,i)=>'<article class="sena" data-i="'+i+'"><div class="sena-cab"><span class="letra">'+
    esc(s.l.toUpperCase())+'</span>'+celda(T.signos[i].p)+(s.m?'<span class="etq">con movimiento</span>':'')+
    (s.propia?'<span class="etq verde">cambiada</span>':'')+'</div><p class="desc">'+esc(s.d)+'</p>'+
    '<div class="editor" hidden><label class="campo" for="sd'+i+'">Cómo se hace la '+esc(s.l.toUpperCase())+'</label><textarea id="sd'+i+'" rows="3"></textarea><div class="contador"></div>'+
    '<div class="acc"><button class="btn peq" type="button" data-acc="guardar">Guardar</button><button class="btn sec peq" type="button" data-acc="cancelar">Cancelar</button>'+
    (s.propia?'<button class="btn sec peq" type="button" data-acc="restaurar">Volver a la original</button>':'')+'</div></div>'+
    '<div class="acc ver"><button class="btn sec peq" type="button" data-acc="editar">Cambiar</button><button class="btn sec peq" type="button" data-acc="dotly">Ver en DOTLY</button></div></article>').join('');
}
function contarSena(ta){
  const n=bytes(ta.value),c=ta.parentNode.querySelector('.contador');
  c.textContent=n+' / 120';c.classList.toggle('mal',n>120);
}
$('#senasLista').addEventListener('input',e=>{if(e.target.tagName==='TEXTAREA')contarSena(e.target);});
$('#senasLista').addEventListener('click',async e=>{
  const b=e.target.closest('[data-acc]');if(!b)return;
  const art=b.closest('[data-i]'),i=+art.dataset.i,ed=$('.editor',art),ta=$('textarea',art);
  const acc=b.dataset.acc;
  if(acc==='editar'){ed.hidden=false;$('.desc',art).hidden=true;$('.acc.ver',art).hidden=true;ta.value=SENAS[i].d;contarSena(ta);ta.focus();}
  else if(acc==='cancelar'){ed.hidden=true;$('.desc',art).hidden=false;$('.acc.ver',art).hidden=false;}
  else if(acc==='guardar'){
    const d=ta.value.trim();
    if(!d){aviso('Escribe cómo se hace la seña','mal');return;}
    if(bytes(d)>120){aviso('Demasiado larga: como mucho 120 caracteres','mal');return;}
    if(await mandar('/api/senas',{i:i,d:d},'Seña guardada en DOTLY')) cargarSenas(true);
  }
  else if(acc==='restaurar'){if(await mandar('/api/senas',{i:i,d:''},'Seña original de vuelta')) cargarSenas(true);}
  else if(acc==='dotly') mandar('/api/deletrear',{t:SENAS[i].l},'En la pantalla de DOTLY');
});
$('#retoSenas').addEventListener('click',()=>mandar('/api/modo',{m:'reto_senas'},'DOTLY: Reto: señas'));

// Deletreo en esta página
const DELETREO_MS=[4000,2600,1600];
const dl={letras:[],pos:0,timer:null};
function plegar(ch){return ({'á':'a','é':'e','í':'i','ó':'o','ú':'u','ü':'u','à':'a','è':'e','ì':'i','ò':'o','ù':'u'})[ch]||ch;}
function dlPreparar(txt){
  const letras=[];
  for(const ch of Array.from(txt.toLowerCase())){
    const s=POR_CAR[plegar(ch)];
    if(s&&s.k==='letra') letras.push(s.i);
  }
  return letras;
}
function dlMostrar(){
  const i=dl.letras[dl.pos],s=T.signos[i],sd=SENAS[i];
  $('#dlLetra').textContent=s.t.toUpperCase();
  $('#dlCelda').innerHTML=celda(s.p,'grande')+'<div class="suave" style="margin-top:4px">Puntos '+puntosTxt(s.p)+(sd&&sd.m?' · con movimiento':'')+'</div>';
  $('#dlDesc').textContent=sd?sd.d:'';
  $('#dlPalabra').innerHTML=dl.letras.map((k,n)=>'<span'+(n===dl.pos?' class="ahora"':'')+'>'+esc(T.signos[k].t.toUpperCase())+'</span>').join('');
}
function dlParar(){clearInterval(dl.timer);dl.timer=null;$('#dlPlay').textContent='Seguir';}
function dlSeguir(){
  clearInterval(dl.timer);
  const v=AJ?AJ.velocidad:1;
  dl.timer=setInterval(()=>{dl.pos=(dl.pos+1)%dl.letras.length;dlMostrar();},DELETREO_MS[v]||2600);
  $('#dlPlay').textContent='Pausa';
}
$('#dlAqui').addEventListener('click',async()=>{
  if(!T){aviso('Aún no hay conexión con DOTLY','mal');return;}
  await cargarSenas(false);
  const letras=dlPreparar($('#dlTxt').value);
  if(!letras.length){aviso('Escribe una palabra con letras','mal');return;}
  dl.letras=letras;dl.pos=0;$('#dlEsc').hidden=false;dlMostrar();dlSeguir();
});
$('#dlDotly').addEventListener('click',()=>{
  const t=$('#dlTxt').value.trim();
  if(!t){aviso('Escribe una palabra','mal');return;}
  mandar('/api/deletrear',{t:t},'DOTLY la está deletreando');
});
$('#dlPlay').addEventListener('click',()=>{if(dl.timer)dlParar();else dlSeguir();});
$('#dlAnt').addEventListener('click',()=>{dlParar();dl.pos=(dl.pos+dl.letras.length-1)%dl.letras.length;dlMostrar();});
$('#dlSig').addEventListener('click',()=>{dlParar();dl.pos=(dl.pos+1)%dl.letras.length;dlMostrar();});

// ---------- ajustes de aprendizaje ----------
let AJ=null,redSucia=false;
async function cargarAjustes(){
  try{AJ=await api('/api/ajustes');}catch(e){return;}
  ['preguntas','nivel','velocidad'].forEach(n=>$$('input[name='+n+']').forEach(r=>{r.checked=+r.value===AJ[n];}));
  if(!redSucia){$('#ssid').value=AJ.ssid;$('#clave').value=AJ.clave;}
}
['preguntas','nivel','velocidad'].forEach(n=>$$('input[name='+n+']').forEach(r=>r.addEventListener('change',async()=>{
  const d={};d[n]=r.value;
  if(await mandar('/api/ajustes',d,'Guardado')) cargarAjustes();
})));

// ---------- palabras ----------
let palabrasSucia=false;
function contarPalabras(){
  const v=$('#palabras').value,n=v.split(/[,\n]/).filter(p=>p.trim()).length,b=bytes(v);
  const c=$('#palabrasCuenta');
  c.textContent=n+(n===1?' palabra':' palabras')+' · '+b+' / 600';
  c.classList.toggle('mal',b>600);
}
async function cargarPalabras(){
  try{const j=await api('/api/palabras');if(!palabrasSucia)$('#palabras').value=j.lista;contarPalabras();}catch(e){}
}
$('#palabras').addEventListener('input',()=>{palabrasSucia=true;contarPalabras();});
$('#palabrasGuardar').addEventListener('click',async()=>{
  const v=$('#palabras').value.trim();
  if(!v){aviso('La lista está vacía','mal');return;}
  if(bytes(v)>600){aviso('La lista es demasiado larga (600 como mucho)','mal');return;}
  if(await mandar('/api/palabras',{lista:v},'Lista guardada en DOTLY')){palabrasSucia=false;cargarPalabras();}
});
$('#palabrasRestaurar').addEventListener('click',async()=>{
  if(await mandar('/api/palabras',{lista:''},'Lista de DOTLY de vuelta')){palabrasSucia=false;cargarPalabras();}
});

// ---------- progreso ----------
async function cargarProgreso(){
  try{pintarProgreso(await api('/api/progreso'));}catch(e){aviso(e.message,'mal');}
}
function pintarProgreso(p){
  const L=p.letras,sum=k=>L.reduce((a,l)=>a+l[k],0);
  const pct=(o,t)=>t?Math.round(o*100/t)+' %':'—';
  const vistas=L.filter(l=>l.v).length;
  const st=[['Leer',pct(sum('lo'),sum('lt')),sum('lo')+' de '+sum('lt')+' aciertos'],
    ['Formar',pct(sum('fo'),sum('ft')),sum('fo')+' de '+sum('ft')+' aciertos'],
    ['Señas',pct(sum('so'),sum('st')),sum('so')+' de '+sum('st')+' aciertos'],
    ['Racha máxima',p.rachaMax,'aciertos seguidos'],['Rondas',p.rondas,'retos terminados'],
    ['Palabras',p.palabras,'leídas'],['Vistas',vistas+' / '+L.length,'letras estudiadas']];
  $('#stats').innerHTML=st.map(s=>'<div class="stat"><span>'+s[0]+'</span><b>'+s[1]+'</b><span>'+s[2]+'</span></div>').join('');
  const malas=[];
  $('#progGrid').innerHTML=L.map(l=>{
    const tot=l.lt+l.ft+l.st,ok=l.lo+l.fo+l.so,r=tot?ok/tot:null;
    const cls=r===null?(l.v?'visto':''):r>=.8?'bien':r>=.5?'medio':'mal';
    if(tot>=2&&r<.8) malas.push([r,l.t]);
    const txt=r===null?(l.v?'vista':'—'):Math.round(r*100)+' %';
    return '<div class="pl '+cls+'" title="Leer '+l.lo+'/'+l.lt+' · Formar '+l.fo+'/'+l.ft+' · Señas '+l.so+'/'+l.st+'"><b>'+
      esc(l.t.toUpperCase())+'</b>'+celda(l.p)+'<small>'+txt+'</small></div>';
  }).join('');
  malas.sort((a,b)=>a[0]-b[0]);
  $('#repasar').textContent=malas.length?'Para repasar: '+malas.slice(0,3).map(m=>m[1].toUpperCase()).join(', ')+'.':
    'Cada casilla es una letra: el color dice cuánto aciertas con ella.';
}
$('#progAct').addEventListener('click',cargarProgreso);
$('#progBorrar').addEventListener('click',async()=>{
  if(!confirm('¿Borrar todo el progreso guardado en DOTLY? No se puede deshacer.')) return;
  if(await mandar('/api/progreso/borrar',{},'Progreso borrado')) cargarProgreso();
});

// ---------- teclado y red ----------
function pintarTeclado(k){
  const pos=mv=>Math.max(0,Math.min(100,mv/3300*100));
  let h='';
  k.rangos.forEach((r,i)=>{
    if(r[0]>r[1]) return;
    const a=pos(r[0]),b=pos(r[1]);
    const ancho=Math.max(1,b-a);
    h+='<div class="r" style="left:'+a+'%;width:'+ancho+'%" title="SW'+(i+1)+': '+r[0]+' a '+r[1]+' mV">'+(ancho<9?'':'SW')+(i+1)+'</div>';
  });
  if(k.conectado) h+='<div class="rep" style="left:'+pos(k.reposo)+'%"></div>';
  h+='<div class="aguja" style="left:'+pos(k.mv)+'%"></div>';
  $('#medidor').innerHTML=h;
  $('#medidor').setAttribute('aria-label','Tensión del teclado: '+k.mv+' milivoltios');
  $('#tecEstado').textContent=k.conectado?'conectado':'no detectado';
  $('#tecMv').textContent=k.mv+' mV';
  $('#tecReposo').textContent=k.conectado?k.reposo+' mV':'—';
  $('#tecCal').textContent=k.calibrado?('guardada · '+k.botones+' botones'):'sin calibrar (tabla de fábrica)';
}
$('#calibrar').addEventListener('click',async()=>{
  if(!confirm('DOTLY va a pedir en su pantalla que mantengas pulsado cada botón. ¿Empezar?')) return;
  if(await mandar('/api/calibrar',{},'Calibración en marcha: mira la pantalla de DOTLY')) abrir('inicio');
});
$('#verClave').addEventListener('change',e=>{$('#clave').type=e.target.checked?'text':'password';});
['ssid','clave'].forEach(id=>$('#'+id).addEventListener('input',()=>{redSucia=true;}));
$('#formRed').addEventListener('submit',async e=>{
  e.preventDefault();
  const ssid=$('#ssid').value.trim(),clave=$('#clave').value;
  if(!ssid||bytes(ssid)>32){aviso('El nombre debe tener de 1 a 32 caracteres','mal');return;}
  if(clave&&(clave.length<8||bytes(clave)>63)){aviso('La clave debe tener de 8 a 63 caracteres, o ninguno','mal');return;}
  const j=await mandar('/api/ajustes',{ssid:ssid,clave:clave},'Red guardada');
  if(j){redSucia=false;$('#reiniciarCaja').hidden=!j.reiniciar;}
});
$('#reiniciar').addEventListener('click',async()=>{
  const ssid=$('#ssid').value.trim();
  if(await mandar('/api/reiniciar',{})){
    $('#reiniciarCaja').innerHTML='<p><b>DOTLY se está reiniciando.</b> Conéctate a la red «'+esc(ssid)+'» y vuelve a abrir esta página.</p>';
  }
});

// ---------- logo y arranque ----------
$('#logo').innerHTML=[0x19,0x15,0x1E,0x07,0x3D].map(p=>celda(p)).join('');
aplicarTema(leerL('tema')||'auto');
aplicarTam(+(leerL('tam')||1));
if(window.matchMedia) matchMedia('(prefers-color-scheme: dark)').addEventListener&&matchMedia('(prefers-color-scheme: dark)').addEventListener('change',()=>aplicarTema(leerL('tema')||'auto'));
pintarModos();traducirVista();perkPintar();
{const p=leerL('pestana');abrir(['inicio','braille','senas','modos','progreso','ajustes'].includes(p)?p:'inicio');}
cargarTabla();cargarAjustes();
refrescar(0);
setInterval(()=>{if(!document.hidden)refrescar(0);},1000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refrescar(0);});
</script>
</body>
</html>
)DOTLY";
