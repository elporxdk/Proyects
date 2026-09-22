#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pruebas de la interfaz web de Medibot (HTML_TEMPLATE).  SIN NAVEGADOR.
======================================================================
POR QUE EXISTE ESTE FICHERO
---------------------------
La web se quedo completamente muerta (ningun boton respondia, el tema no
cambiaba, la interfaz no se refrescaba) por UN caracter:

    HTML_TEMPLATE = \"\"\"...\"\"\"          <-- cadena Python NORMAL

Dentro va JavaScript, y el JavaScript llevaba comillas escapadas:

    onclick="enviarMovimiento(\\')"

Python interpreta \\' ANTES de servir la pagina y lo convierte en '. El
navegador recibia  enviarMovimiento('')  -> "SyntaxError: Invalid or
unexpected token". Y un error de SINTAXIS anula el bloque <script> ENTERO:
ninguna funcion queda definida, ningun onclick funciona, startUpdates() no
arranca. El video seguia viendose porque <img src="/video/0"> no necesita
JavaScript, lo que hacia parecer que "la web carga pero no responde".

Lo insidioso: el fichero .py compila perfectamente y el HTML "se ve bien".
Solo se nota abriendo la consola del navegador. Estas pruebas lo detectan
sin navegador y en menos de un segundo.

    python3 pruebas/test_web_template.py
    python3 pruebas/test_web_template.py -v

Solo necesita la libreria estandar. Si hay 'node' instalado, ademas valida
la sintaxis del JavaScript de verdad; si no, esa prueba se salta sola (en una
Raspberry no hace falta instalar Node solo para esto).
"""

import os
import re
import shutil
import subprocess
import tempfile
import unittest

# Esta prueba no importa el codigo: lo LEE. Las dos interfaces viven en medibot/
# y estas pruebas en pruebas/, asi que se sube un nivel para llegar.
_MEDIBOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "medibot")

RUTA = os.path.join(_MEDIBOT, "Vision_MEDIBOT.py")


def fuente():
    with open(RUTA, encoding="utf-8") as f:
        return f.read()


def plantilla_evaluada():
    """El HTML TAL Y COMO LO RECIBE EL NAVEGADOR.

    Clave: no basta con leer el texto del fichero. Hay que evaluar la cadena
    como hace Python, porque es justo ahi donde se perdian las barras
    invertidas. Leer el fuente en crudo fue lo que hizo que las pruebas
    anteriores dieran verde con la web rota."""
    src = fuente()
    marca = 'HTML_TEMPLATE = r"""'
    if marca not in src:
        marca = 'HTML_TEMPLATE = """'
    i = src.index(marca) + len(marca)
    j = src.index('"""', i)
    literal = src[i:j]
    prefijo = 'r"""' if marca.startswith('HTML_TEMPLATE = r') else '"""'
    return eval(prefijo + literal + '"""')      # noqa: S307 - literal del repo


def bloques_script(html):
    return re.findall(r"<script>(.*?)</script>", html, re.S)


def html_sin_script(html):
    """Solo el MARCADO: fuera <script> y fuera <style>.

    Sin quitar el CSS, un comentario que mencione '<button>' para explicar una
    regla se cuenta como si fuera un boton de verdad. Ya paso: la prueba de
    botones huerfanos fallaba por el comentario que documentaba el estilo."""
    limpio = re.sub(r"<script>.*?</script>", "", html, flags=re.S)
    return re.sub(r"<style>.*?</style>", "", limpio, flags=re.S)


class PruebasCadenaRaw(unittest.TestCase):
    """La guarda directa contra la regresion que rompio la web."""

    def test_la_plantilla_es_una_cadena_raw(self):
        self.assertIn('HTML_TEMPLATE = r"""', fuente(),
                      'HTML_TEMPLATE debe declararse como cadena RAW (r\"\"\").\n'
                      'Sin la r, Python se come las barras invertidas del '
                      'JavaScript y el <script> entero deja de compilar en el '
                      'navegador: ningun boton responde.')

    def test_las_barras_invertidas_llegan_intactas(self):
        """Si alguien quita la r, este test lo caza aunque el .py compile."""
        src = fuente()
        i = src.index('HTML_TEMPLATE = r"""') + len('HTML_TEMPLATE = r"""')
        j = src.index('"""', i)
        literal = src[i:j]
        servido = plantilla_evaluada()
        self.assertEqual(literal.count("\\"), servido.count("\\"),
                         "Se han perdido barras invertidas entre el fuente y lo "
                         "que se sirve: la plantilla no es raw.")


class PruebasSintaxisJS(unittest.TestCase):

    @unittest.skipUnless(shutil.which("node"), "node no instalado: se omite")
    def test_el_javascript_servido_compila(self):
        """LA prueba que faltaba: valida el JS EVALUADO, no el del fichero."""
        for n, js in enumerate(bloques_script(plantilla_evaluada())):
            with self.subTest(bloque=n):
                with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                                 encoding="utf-8") as f:
                    f.write(js)
                    ruta = f.name
                try:
                    r = subprocess.run(["node", "--check", ruta],
                                       capture_output=True, text=True)
                finally:
                    os.unlink(ruta)
                self.assertEqual(r.returncode, 0,
                                 f"El JavaScript servido no compila:\n{r.stderr}")

    def test_hay_un_solo_bloque_de_script(self):
        self.assertEqual(len(bloques_script(plantilla_evaluada())), 1,
                         "Si hay varios <script>, un error en uno no tumba los "
                         "otros; revisa que esta prueba siga teniendo sentido.")


class PruebasCoherenciaDelDOM(unittest.TestCase):
    """Errores que solo aparecen en tiempo de ejecucion, cazados en estatico."""

    def setUp(self):
        self.html = plantilla_evaluada()
        self.js = "\n".join(bloques_script(self.html))
        self.marcado = html_sin_script(self.html)
        self.ids = set(re.findall(r'id="([^"]+)"', self.marcado))

    def test_no_hay_ids_duplicados(self):
        todos = re.findall(r'id="([^"]+)"', self.marcado)
        duplicados = {i for i in todos if todos.count(i) > 1}
        self.assertFalse(duplicados, f"ids repetidos en el HTML: {duplicados}")

    def test_todo_getElementById_apunta_a_un_id_existente(self):
        """Al recortar la interfaz es facil dejar JS apuntando a lo borrado."""
        usados = set(re.findall(r"getElementById\('([^']+)'\)", self.js))
        # ids construidos dinamicamente (cam1-view / cam2-view) via concatenacion
        dinamicos = {"cam1-view", "cam2-view"}
        faltan = {u for u in usados if u not in self.ids} - dinamicos
        self.assertFalse(faltan, f"El JS busca ids que no existen: {sorted(faltan)}")

    def test_todo_onclick_llama_a_una_funcion_definida(self):
        llamadas = set(re.findall(r'onclick="(\w+)\(', self.marcado))
        definidas = set(re.findall(r"function (\w+)\s*\(", self.js))
        faltan = llamadas - definidas
        self.assertFalse(faltan, f"onclick sin funcion definida: {sorted(faltan)}")

    def test_los_botones_con_onclick_no_quedan_huerfanos(self):
        """Cada boton debe tener onclick o un id por el que engancharlo."""
        for boton in re.findall(r"<button[^>]*>", self.marcado):
            with self.subTest(boton=boton[:70]):
                self.assertTrue("onclick=" in boton or "id=" in boton,
                                f"Boton sin onclick ni id: {boton}")


class PruebasManejoDeErrores(unittest.TestCase):
    """El usuario tiene que ENTERARSE de que algo fallo."""

    def setUp(self):
        self.js = "\n".join(bloques_script(plantilla_evaluada()))
        # Sin comentarios: si no, el propio comentario que EXPLICA por que no
        # hay que usar .catch(() => {}) hacia fallar la prueba.
        self.codigo = re.sub(r"//[^\n]*", "", self.js)

    def test_no_se_tragan_errores_en_silencio(self):
        vacios = re.findall(r"\.catch\(\s*\(\s*\)\s*=>\s*\{\s*\}\s*\)", self.codigo)
        self.assertFalse(vacios,
                         f"Hay {len(vacios)} '.catch(() => {{}})' que ocultan "
                         "fallos de API sin decir nada al usuario.")

    def test_existe_la_barra_de_avisos(self):
        self.assertIn('id="avisoBarra"', plantilla_evaluada(),
                      "Falta el elemento donde se muestran los errores.")

    def test_los_errores_de_javascript_se_muestran(self):
        self.assertIn("window.addEventListener('error'", self.js,
                      "Sin este manejador, un error de JS vuelve a ser invisible.")

    def test_se_comprueba_el_codigo_http(self):
        self.assertIn("r.ok", self.js,
                      "Hay que mirar response.ok: un 400/500 no es exito.")


class PruebasTema(unittest.TestCase):

    def setUp(self):
        self.html = plantilla_evaluada()
        self.js = "\n".join(bloques_script(self.html))

    def test_el_boton_de_tema_existe_y_esta_conectado(self):
        self.assertIn('id="themeToggle"', self.html)
        self.assertIn('onclick="toggleTheme()"', self.html)
        self.assertIn("function toggleTheme", self.js)

    def test_el_tema_se_aplica_al_cargar(self):
        self.assertIn("applyStoredTheme", self.js)

    def test_el_tema_se_guarda_y_se_recupera(self):
        self.assertIn("localStorage.setItem('medibot-theme'", self.js)
        self.assertIn("localStorage.getItem('medibot-theme')", self.js)

    def test_hay_estilos_para_el_modo_claro(self):
        claros = re.findall(r'html\[data-theme="light"\]', self.html)
        self.assertGreater(len(claros), 10,
                           "El modo claro necesita estilos propios para tarjetas, "
                           "botones, textos y controles.")


class PruebasControlesEsenciales(unittest.TestCase):
    """Lo que tiene que seguir estando para manejar el robot."""

    def setUp(self):
        self.html = plantilla_evaluada()
        self.js = "\n".join(bloques_script(self.html))

    def test_estan_los_controles_del_robot(self):
        for id_ in ("systemBtn", "recordBtn", "recognitionBtn", "fsBtn",
                    "velRange", "joyBase", "joyStick", "mov-grid",
                    "cam1-stage", "cam1-view", "videos-grid"):
            with self.subTest(id=id_):
                self.assertIn(f'id="{id_}"', self.html)

    def test_estan_las_funciones_que_hablan_con_flask(self):
        for fn in ("toggleSystem", "toggleRecording", "toggleRecognition",
                   "switchCamera", "enviarMovimiento", "fijarVelocidad",
                   "loadVideos", "fetchData", "startUpdates", "stopUpdates",
                   "toggleCameraFullscreen", "toggleTheme"):
            with self.subTest(funcion=fn):
                self.assertIn(f"function {fn}", self.js)

    def test_el_stream_de_video_apunta_a_las_dos_camaras(self):
        self.assertIn('src="/video/0"', self.html)
        self.assertIn('src="/video/1"', self.html)

    def test_las_rutas_que_usa_el_js_existen_en_flask(self):
        """Que ningun fetch apunte a una ruta que Flask no sirve."""
        src = fuente()
        rutas_flask = set(re.findall(r'@app\.route\("([^"<]+)', src))
        usadas = set(re.findall(r"(?:pedirJSON|enviarJSON|fetch)\('(/[^']*)'", self.js))
        #  Se compara solo la RUTA: '/hablar?seq=' + n es una llamada
        #  perfectamente valida a la ruta '/hablar', y los parametros no
        #  aparecen en @app.route.
        faltan = {u for u in usadas if u.split("?")[0] not in rutas_flask}
        self.assertFalse(faltan, f"El JS llama a rutas inexistentes: {sorted(faltan)}")


class PruebasMovil(unittest.TestCase):

    def setUp(self):
        self.html = plantilla_evaluada()

    def test_hay_viewport_para_movil(self):
        self.assertIn('name="viewport"', self.html)

    def test_no_se_pide_favicon_al_servidor(self):
        """Flask no sirve /favicon.ico: sin icono en linea queda un 404 en la
        consola en cada carga."""
        self.assertIn('rel="icon"', self.html)


class PruebasCacheDeLaInterfaz(unittest.TestCase):
    """Que el navegador no pueda quedarse con una version vieja de la web.

    EL FALLO QUE VIGILAN
    --------------------
    Toda la interfaz es UNA pagina con el JavaScript escrito dentro del HTML.
    Flask no mandaba ninguna cabecera de cache, asi que el navegador podia
    guardarse el HTML y reutilizarlo durante dias. Al corregir un fallo del
    JavaScript, el movil seguia ejecutando el guardado: el servidor estaba bien
    y la pantalla seguia rota.

    Da exactamente el cuadro que se reportaba: el VIDEO se ve (porque
    /video/<n> es una peticion nueva cada vez y no se cachea) pero los botones
    y el selector de tema no responden (porque el HTML guardado lleva dentro la
    version antigua del JavaScript)."""

    def setUp(self):
        self.src = fuente()
        self.html = plantilla_evaluada()

    def test_flask_prohibe_cachear(self):
        self.assertIn("no-store", self.src,
                      "Sin Cache-Control: no-store, un movil puede quedarse "
                      "con la interfaz antigua indefinidamente.")

    def test_la_cabecera_se_aplica_a_todas_las_respuestas(self):
        self.assertIn("@app.after_request", self.src,
                      "La cabecera debe ponerse en un solo sitio, no ruta a ruta")

    def test_hay_huella_de_la_interfaz(self):
        """Permite saber que version esta viendo un navegador, sin adivinar."""
        self.assertIn("BUILD_WEB", self.src)
        self.assertIn("X-Medibot-Build", self.src)

    def test_la_pagina_compara_su_huella_con_la_del_robot(self):
        self.assertIn("BUILD_PAGINA", self.html)
        self.assertIn("comprobarBuild", self.html)

    def test_el_marcador_se_sustituye_al_servir(self):
        """Si el reemplazo desaparece, BUILD_PAGINA valdria el literal y el
        aviso saltaria siempre: peor que no tenerlo."""
        self.assertIn('.replace("__BUILD_WEB__", BUILD_WEB)', self.src)
        self.assertIn("__BUILD_WEB__", self.html,
                      "La plantilla debe llevar el marcador que se sustituye")

    def test_la_huella_se_publica_en_la_api(self):
        self.assertIn('"build_web": BUILD_WEB', self.src)


# =============================================================================
def fuente_pastillero():
    ruta = os.path.join(_MEDIBOT, "Pastillero.py")
    with open(ruta, encoding="utf-8") as f:
        return f.read()


def plantilla_pastillero():
    """El HTML del Pastillero como lo recibe el navegador."""
    src = fuente_pastillero()
    marca = 'HTML_PAGE = """'
    i = src.index(marca) + len(marca)
    j = src.index('"""', i)
    return eval('"""' + src[i:j] + '"""')       # noqa: S307 - literal del repo


class PruebasEnlacesEntreInterfaces(unittest.TestCase):
    """Ir de una interfaz a la otra sin teclear la IP a mano."""

    def setUp(self):
        self.vision = plantilla_evaluada()
        self.vision_src = fuente()
        self.pill = plantilla_pastillero()
        self.pill_src = fuente_pastillero()

    def test_medibot_enlaza_al_pastillero(self):
        self.assertIn('id="linkPastillero"', self.vision)

    def test_el_pastillero_enlaza_a_medibot(self):
        self.assertIn('id="linkMedibot"', self.pill)

    def test_son_enlaces_de_verdad_y_no_botones(self):
        """Un <a> se puede abrir en otra pestaña con el boton central o con
        una pulsacion larga; un boton con window.location, no."""
        for html, ident in ((self.vision, "linkPastillero"),
                            (self.pill, "linkMedibot")):
            with self.subTest(ident=ident):
                etiqueta = re.search(r'<(\w+)[^>]*id="' + ident + r'"', html)
                self.assertIsNotNone(etiqueta)
                self.assertEqual(etiqueta.group(1), "a")

    def test_los_marcadores_se_sustituyen_al_servir(self):
        """Si el reemplazo desaparece, el enlace apuntaria al literal."""
        self.assertIn('replace("__URL_PASTILLERO__", URL_PASTILLERO)',
                      self.vision_src)
        self.assertIn('replace("__URL_MEDIBOT__", URL_MEDIBOT)', self.pill_src)
        self.assertIn("__URL_PASTILLERO__", self.vision)
        self.assertIn("__URL_MEDIBOT__", self.pill)

    def test_sin_configurar_se_deduce_del_mismo_host(self):
        """En la red local basta con cambiar de puerto; la URL fija solo hace
        falta detras de un tunel, donde cada interfaz tiene su subdominio."""
        self.assertIn("location.hostname + ':5001", self.vision)
        self.assertIn("location.hostname + ':5000", self.pill)

    def test_se_pueden_fijar_por_entorno(self):
        self.assertIn('MEDIBOT_URL_PASTILLERO', self.vision_src)
        self.assertIn('MEDIBOT_URL_VISION', self.pill_src)

    def test_los_botones_de_la_barra_NO_llevan_emojis(self):
        """Se pidieron minimalistas, solo texto.

        Ademas de la estetica hay una razon practica: cada sistema dibuja los
        emojis a su manera (el robot y la pastilla salen distintos en Android,
        en Windows y en iOS) y no heredan el color del tema, asi que en modo
        oscuro se quedan con su color de siempre y desentonan."""
        emojis = re.compile(
            "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF]")
        for html, ident, nombre in (
                (self.vision, "linkPastillero", "Medibot"),
                (self.pill, "linkMedibot", "Pastillero")):
            with self.subTest(interfaz=nombre):
                m = re.search(r'<a[^>]*id="' + ident + r'"[^>]*>(.*?)</a>',
                              html, re.S)
                self.assertIsNotNone(m, f"No se encontro #{ident}")
                self.assertFalse(emojis.findall(m.group(1)),
                                 f"#{ident} lleva emoji: {m.group(1).strip()!r}")

    def test_el_enlace_del_pastillero_se_llama_MEDIBOT(self):
        """Se pidio que pusiera MEDIBOT, tal cual.

        Antes decia "Volver a Medibot", que da por hecho que se ha llegado
        DESDE Medibot; a la pastillera se entra igual de a menudo por su
        propia direccion, y entonces ese "volver" no lleva a ningun sitio del
        que se venga."""
        m = re.search(r'<a[^>]*id="linkMedibot"[^>]*>(.*?)</a>', self.pill, re.S)
        self.assertIsNotNone(m, "No se encontro #linkMedibot")
        self.assertEqual(m.group(1).strip(), "MEDIBOT")


class PruebasDeteccionRojo(unittest.TestCase):
    """Poder apagar la busqueda de objetos rojos sin reiniciar el programa."""

    def setUp(self):
        self.html = plantilla_evaluada()
        self.src = fuente()

    def test_existe_el_boton_y_la_ruta(self):
        self.assertIn('id="rojoBtn"', self.html)
        self.assertIn("function alternarDeteccionRojo", self.html)
        self.assertIn('@app.route("/toggle_deteccion_rojo"', self.src)

    def test_la_ruta_es_POST(self):
        """Cambia el estado del robot: con GET lo dispararia un prefetch del
        navegador o cualquier rastreador que siguiera el enlace."""
        m = re.search(r'@app\.route\("/toggle_deteccion_rojo"([^)]*)\)', self.src)
        self.assertIsNotNone(m)
        self.assertIn('methods=["POST"]', m.group(1))

    def test_se_puede_pedir_un_valor_concreto_y_no_solo_alternar(self):
        """Alternar a ciegas descuadra la interfaz si se pulsa dos veces
        rapido o si hay dos navegadores abiertos."""
        self.assertIn('"activo" in datos', self.src)

    def test_devuelve_el_estado_resultante(self):
        self.assertIn('"deteccion_rojo": DETECCION_ROJO', self.src)

    def test_el_estado_se_publica_en_la_api(self):
        """Sin esto, un segundo navegador mostraria un estado inventado."""
        cuerpo = re.search(r"def api_all\(\).*?\n@app\.route", self.src, re.S)
        self.assertIsNotNone(cuerpo)
        self.assertIn('"deteccion_rojo"', cuerpo.group(0))

    def test_al_apagarla_se_limpia_el_rastro_del_seguimiento(self):
        """Si no, el ultimo objeto detectado se queda dibujado para siempre."""
        cuerpo = re.search(
            r"def toggle_deteccion_rojo\(\):(.*?)\n@app\.route", self.src, re.S)
        self.assertIsNotNone(cuerpo)
        self.assertIn("object_history.clear()", cuerpo.group(1))


class PruebasBotonAudioSoloIcono(unittest.TestCase):
    """El boton de audio se pidio como icono, sin texto."""

    def setUp(self):
        self.html = plantilla_evaluada()

    def _boton(self):
        m = re.search(r'<button[^>]*id="audioBtn".*?</button>', self.html, re.S)
        self.assertIsNotNone(m, "No se encontro #audioBtn")
        return m.group(0)

    def test_no_tiene_texto_visible(self):
        boton = self._boton()
        sin_marcado = re.sub(r"<[^>]+>", "", boton)
        sin_comentarios = re.sub(r"<!--.*?-->", "", sin_marcado, flags=re.S)
        self.assertEqual(sin_comentarios.strip(), "",
                         f"El boton de audio deberia ser solo icono: "
                         f"{sin_comentarios.strip()!r}")

    def test_el_icono_es_un_SVG_y_no_un_emoji(self):
        """Un emoji lo dibuja cada sistema a su manera y no sigue al tema; un
        SVG con currentColor hereda el color del boton."""
        boton = self._boton()
        self.assertIn("<svg", boton)
        self.assertIn("currentColor", boton)
        emojis = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF]")
        self.assertFalse(emojis.findall(boton), "El boton de audio lleva emoji")

    def test_el_icono_refleja_el_estado(self):
        """Sin texto, el icono es lo unico que dice si esta sonando."""
        self.assertIn('id="audioOndas"', self.html)
        self.assertIn('id="audioCruz"', self.html)
        self.assertIn("function pintarIconoAudio", self.html)

    def test_arranca_con_el_icono_MUDO(self):
        """Al cargar la pagina el audio no suena: si saliera con las ondas,
        pareceria que ya se esta escuchando."""
        ondas = re.search(r'<g id="audioOndas"([^>]*)>', self.html)
        cruz = re.search(r'<g id="audioCruz"([^>]*)>', self.html)
        self.assertIsNotNone(ondas)
        self.assertIsNotNone(cruz)
        self.assertIn("display:none", ondas.group(1).replace(" ", ""),
                      "Las ondas deben estar ocultas al cargar")
        self.assertNotIn("display:none", cruz.group(1).replace(" ", ""),
                         "La cruz debe verse al cargar (el audio esta parado)")

    def test_sigue_siendo_accesible_sin_texto(self):
        """Un boton sin texto necesita nombre para un lector de pantalla."""
        boton = self._boton()
        self.assertIn("aria-label", boton)
        self.assertIn("title=", boton)


class PruebasTemaPastillero(unittest.TestCase):
    """El Pastillero tenia los 30 colores escritos a mano: no habia tema."""

    def setUp(self):
        self.html = plantilla_pastillero()
        i = self.html.index("<style>")
        self.css = self.html[i:self.html.index("</style>", i)]

    def test_existe_el_boton(self):
        self.assertIn('id="themeToggle"', self.html)

    def test_no_queda_ningun_color_escrito_a_mano_en_el_css(self):
        """Un color fijo no cambia con el tema: en oscuro quedaria ilegible."""
        fijos = sorted(set(re.findall(r"#[0-9a-fA-F]{3,6}\b", self.css)))
        #  Los de :root y los del tema oscuro SI son literales: son la
        #  definicion de la paleta. Se descuentan mirando solo las reglas.
        sin_paleta = re.sub(r"(:root|html\[data-theme=\"dark\"\])\s*\{[^}]*\}",
                            "", self.css)
        fijos = sorted(set(re.findall(r"#[0-9a-fA-F]{3,6}\b", sin_paleta)))
        self.assertFalse(fijos, f"Colores fijos fuera de la paleta: {fijos}")

    def test_toda_variable_usada_esta_definida(self):
        usadas = set(re.findall(r"var\((--[\w-]+)\)", self.css))
        definidas = set(re.findall(r"(--[\w-]+)\s*:", self.css))
        self.assertFalse(usadas - definidas,
                         f"Variables sin definir: {sorted(usadas - definidas)}")

    def test_el_tema_oscuro_redefine_TODAS_las_variables(self):
        """Si falta una, ese trozo se queda con el color claro sobre fondo
        negro: texto azul marino sobre negro, ilegible."""
        raiz = self.css[self.css.index(":root {"):]
        raiz = raiz[:raiz.index("}")]
        osc = self.css[self.css.index('data-theme="dark"'):]
        osc = osc[:osc.index("}")]
        claras = set(re.findall(r"(--[\w-]+)\s*:", raiz))
        oscuras = set(re.findall(r"(--[\w-]+)\s*:", osc))
        self.assertFalse(claras - oscuras,
                         f"Sin equivalente oscuro: {sorted(claras - oscuras)}")

    def test_el_tema_se_aplica_ANTES_de_pintar(self):
        """Si se aplicara al final, la pagina se dibujaria en claro y saltaria
        a oscuro: el fogonazo blanco que molesta de noche."""
        cabeza = self.html[:self.html.index("</head>")]
        self.assertIn("data-theme", cabeza)
        self.assertIn("pillbox-theme", cabeza)

    def test_se_recuerda_en_localStorage(self):
        self.assertIn("CLAVE_TEMA = 'pillbox-theme'", self.html)
        self.assertIn("localStorage.setItem(CLAVE_TEMA", self.html)

    def _script_de_la_cabeza(self):
        cabeza = self.html[:self.html.index("</head>")]
        m = re.search(r"<script>(.*?)</script>", cabeza, re.S)
        self.assertIsNotNone(m, "La cabeza ya no aplica el tema")
        return m.group(1)

    def test_sin_nada_guardado_arranca_en_OSCURO(self):
        """El tema por defecto es el oscuro, se pidio asi.

        Antes se seguia al sistema operativo: en un movil o un portatil en
        modo claro -que es como vienen de fabrica- la pastillera abria en
        blanco, que es justo lo que no se queria."""
        cabeza = self._script_de_la_cabeza()
        self.assertNotIn("prefers-color-scheme", cabeza,
                         "El tema del sistema ya no decide: manda el oscuro.")
        self.assertIn("= 'dark'", cabeza,
                      "Sin tema guardado hay que caer en 'dark'.")

    def test_un_valor_raro_guardado_no_deja_la_pagina_sin_tema(self):
        """localStorage es texto libre: lo puede dejar sucio una version
        antigua o el propio usuario desde la consola. Con un 'if (!t)' un
        valor como 'auto' pasaba tal cual a data-theme y no casaba con
        ninguna paleta: la pagina se quedaba a medias."""
        cabeza = self._script_de_la_cabeza()
        self.assertIn("t !== 'light'", cabeza)
        self.assertIn("t !== 'dark'", cabeza)

    def test_el_boton_arranca_ofreciendo_el_modo_CLARO(self):
        """El boton dice lo que HACE. Si arranca en oscuro y el boton pone
        'Modo Oscuro', parece que el tema esta al reves."""
        m = re.search(r'<button[^>]*id="themeToggle"[^>]*>(.*?)</button>',
                      self.html, re.S)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1).strip(), "Modo Claro")

    def test_los_controles_del_sistema_siguen_al_tema(self):
        """El reloj del <input type=time> y las casillas de los dias los pinta
        el navegador, no el CSS: sin color-scheme salia un reloj blanco en
        medio de la pagina oscura."""
        self.assertIn("color-scheme: dark", self.css)
        self.assertIn("color-scheme: light", self.css)


class PruebasCacheDelPastillero(unittest.TestCase):
    """Que el navegador no pueda servir una interfaz vieja del Pastillero.

    EL FALLO QUE VIGILAN
    --------------------
    Es el mismo que ya le paso a Vision, y el Pastillero se habia quedado sin
    el arreglo: toda la interfaz es UNA pagina con el JavaScript escrito
    dentro del HTML, y sin cabeceras de cache el navegador puede guardarsela
    durante dias. Se actualiza el codigo en la Pi, se reinicia, y la pantalla
    sale EXACTAMENTE igual que antes.

    Enganya especialmente aqui porque los datos si se refrescan: /data y
    /serial/status son peticiones nuevas que nadie cachea, asi que los
    compartimientos y el estado del Arduino se ven al dia mientras los textos,
    los colores y el tema siguen siendo los viejos."""

    def setUp(self):
        self.src = fuente_pastillero()

    def test_prohibe_cachear(self):
        self.assertIn("no-store", self.src,
                      "Sin Cache-Control: no-store, un navegador puede "
                      "quedarse con la interfaz antigua indefinidamente.")

    def test_la_cabecera_se_aplica_a_todas_las_respuestas(self):
        """En un solo sitio, no ruta a ruta: si no, la que se olvide vuelve a
        poder cachearse."""
        self.assertIn("@app.after_request", self.src)

    def test_hay_huella_de_la_interfaz(self):
        """Permite saber que version esta viendo un navegador, sin adivinar."""
        self.assertIn("BUILD_WEB", self.src)
        self.assertIn("X-Pillbox-Build", self.src)

    def test_la_huella_sale_del_HTML_de_verdad(self):
        """Si fuera un numero escrito a mano habria que acordarse de subirlo, y
        justamente se olvida cuando mas falta hace."""
        self.assertIn("hashlib.sha256(HTML_PAGE", self.src)

    def test_la_huella_se_anuncia_al_arrancar(self):
        """Para poder compararla con la que enseña el navegador."""
        self.assertIn("Version de la interfaz", self.src)


class PruebasConexionArduinoPastillero(unittest.TestCase):
    """La pildora de arriba solo dice si hay Arduino o no.

    Antes mostraba el puerto ('Arduino: /dev/ttyUSB0'), 'Hub serial apagado'
    o 'Reconectando...': cuatro textos distintos de anchos distintos que
    hacian bailar la barra, y ninguno contestaba de un vistazo la unica
    pregunta que importa. El detalle no se pierde, se mueve al title."""

    def setUp(self):
        self.html = plantilla_pastillero()
        #  El script del cuerpo: el primero DESPUES de </head>. Buscarlo por el
        #  final no vale, porque la palabra <script> tambien sale dentro de un
        #  comentario del propio codigo.
        self.js = self.html[self.html.index("<script>",
                                            self.html.index("</head>")):]

    def _pildora(self):
        m = re.search(r'<span[^>]*id="serialPill"[^>]*>(.*?)</span>',
                      self.html, re.S)
        self.assertIsNotNone(m, "No se encontro #serialPill")
        return m

    def test_arranca_diciendo_desconectado(self):
        """Y no 'comprobando...': hasta que /serial/status conteste no hay
        ningun Arduino confirmado, y .serial-pill ya lo pinta en rojo."""
        self.assertEqual(self._pildora().group(1).strip(), "Desconectado")

    def _js_de_la_pildora(self):
        """Solo los trozos que tocan #serialPill.

        La pildora de POSICION se maneja aparte y con una variable que se
        llama igual ('pill'), asi que hay que separarlas: sus textos
        ('Sincronizado: comp 3'...) son de las opciones que se pidio
        mantener, no sobran."""
        trozos = re.split(r"\n    (?=function |window\.|let |const )", self.js)
        propios = [t for t in trozos if "serialPill" in t]
        self.assertTrue(propios, "Nadie toca #serialPill")
        return "\n".join(propios)

    def test_solo_se_le_escriben_esas_dos_palabras(self):
        textos = set(re.findall(r"pill\.textContent\s*=\s*'([^']*)'",
                                self._js_de_la_pildora()))
        self.assertTrue(textos, "Nadie escribe en la pildora")
        self.assertEqual(textos, {"Conectado", "Desconectado"},
                         f"Textos de mas en la pildora: {sorted(textos)}")

    def test_el_detalle_no_se_pierde_va_al_title(self):
        """Saber que puerto es o si el hub esta caido sigue haciendo falta
        para diagnosticar; deja de ocupar la barra, nada mas."""
        self.assertIn("s.puerto", self.js, "Ya no se dice cual es el puerto")
        self.assertIn("hub serial esta apagado", self.js)
        self.assertIn("pill.title", self.js)
        self.assertIn('title="Comprobando la conexion', self.html,
                      "Antes de la primera respuesta hay que decir que se "
                      "esta comprobando, aunque sea en el title.")

    def test_siguen_estando_los_botones_de_siempre(self):
        """Se pidio cambiar el texto SIN quitar nada de lo que ya habia."""
        for ident in ("btnReconectar", "btnVerificar", "posPill"):
            with self.subTest(id=ident):
                self.assertIn(f'id="{ident}"', self.html)

    def test_sigue_reconectando_sola(self):
        self.assertIn("ultimaAutoReconexion", self.js)
        self.assertIn("/serial/reconnect", self.js)

    def test_la_funcion_del_onclick_esta_expuesta(self):
        """El script va dentro de un IIFE: sin window.x, el onclick no la ve."""
        self.assertIn('onclick="alternarTema()"', self.html)
        self.assertIn("window.alternarTema = alternarTema", self.html)

    def test_no_pide_favicon_al_servidor(self):
        """Flask no sirve /favicon.ico: sin icono en linea queda un 404 en la
        consola en cada carga."""
        self.assertIn('rel="icon"', self.html)


class PruebasAudioUI(unittest.TestCase):
    """Escuchar el microfono de la camara desde el navegador."""

    def setUp(self):
        self.html = plantilla_evaluada()
        self.src = fuente()

    def test_existe_el_boton_y_su_funcion(self):
        self.assertIn('id="audioBtn"', self.html)
        self.assertIn("function alternarAudio", self.html)
        self.assertIn("function pararAudio", self.html)

    def test_las_rutas_existen_en_flask(self):
        self.assertIn('@app.route("/audio")', self.src)
        self.assertIn('@app.route("/api/audio")', self.src)

    def test_no_se_abre_el_microfono_al_cargar_la_pagina(self):
        """Un <audio src="/audio"> en el HTML dejaria un arecord vivo en la
        Raspberry en cada visita, aunque nadie quisiera escuchar."""
        sin_script = html_sin_script(self.html)
        self.assertNotIn("<audio", sin_script.lower())

    def test_al_parar_se_suelta_la_conexion(self):
        """Con pause() a secas la peticion sigue abierta y el arecord del
        robot no se entera de que ya no hay nadie escuchando."""
        cuerpo = re.search(r"function pararAudio\(\)\s*\{(.*?)\n        \}",
                           self.html, re.S)
        self.assertIsNotNone(cuerpo)
        self.assertIn("removeAttribute('src')", cuerpo.group(1))

    def test_se_corta_el_audio_al_salir_de_la_pagina(self):
        self.assertIn("pararAudio();", self.html)
        descarga = re.search(r"beforeunload[^}]*\}", self.html, re.S)
        self.assertIsNotNone(descarga)
        self.assertIn("pararAudio", descarga.group(0))

    def test_los_botones_sobre_el_video_no_se_pisan(self):
        """Audio y pantalla completa tenian los dos 'right: 10px': se
        solapaban. Van en una fila flex, que los coloca sola."""
        self.assertIn(".cam-acciones", self.html)
        fs = re.search(r"\.fs-btn \{([^}]*)\}", self.html)
        self.assertIsNotNone(fs)
        self.assertNotIn("position: absolute", fs.group(1),
                         "Los botones deben posicionarse por el contenedor "
                         "flex, no cada uno por su cuenta")

    def test_el_estado_del_audio_se_publica_en_la_api(self):
        self.assertIn('"audio": microfono.estado()', self.src)

    def test_si_no_hay_microfono_el_boton_lo_explica(self):
        self.assertIn("function reflejarAudio", self.html)
        self.assertIn("Audio no disponible", self.html)


class PruebasHablarUI(unittest.TestCase):
    """Hablar por el altavoz del robot desde el navegador."""

    def setUp(self):
        self.html = plantilla_evaluada()
        self.src = fuente()

    def test_existe_el_boton_y_sus_funciones(self):
        self.assertIn('id="hablarBtn"', self.html)
        for fn in ("empezarAHablar", "dejarDeHablar", "encolarVoz",
                   "bombearVoz", "aPCM16", "prepararBotonHablar"):
            with self.subTest(funcion=fn):
                self.assertIn(f"function {fn}", self.html)

    def test_las_rutas_existen_en_flask(self):
        self.assertIn('@app.route("/hablar", methods=["POST"])', self.src)
        self.assertIn('@app.route("/api/voz")', self.src)

    def test_el_estado_se_publica_en_la_api(self):
        self.assertIn('"voz": altavoz.estado()', self.src)
        self.assertIn("reflejarVoz(data.voz)", self.html)

    def test_avisa_de_que_hace_falta_HTTPS(self):
        """getUserMedia solo existe en contexto seguro. Es LA razon por la
        que esto no va a funcionar por http://<ip>:5000, asi que tiene que
        decirse con todas las letras y no fallar con un 'undefined'."""
        self.assertIn("isSecureContext", self.html)
        self.assertIn("HTTPS", self.html)
        self.assertIn("function porQueNoSePuedeHablar", self.html)

    def test_no_pide_el_microfono_al_cargar_la_pagina(self):
        """getUserMedia al cargar sacaria el cartel de permiso a cualquiera
        que entre a mirar las camaras, sin haber pedido hablar."""
        arranque = re.search(r"DOMContentLoaded'?,\s*function\s*\(\)\s*\{(.*?)\n        \}",
                             self.html, re.S)
        self.assertIsNotNone(arranque)
        self.assertNotIn("getUserMedia", arranque.group(1))
        #  Solo se prepara el boton; el microfono se pide al pulsarlo.
        self.assertIn("prepararBotonHablar()", arranque.group(1))

    def test_se_habla_manteniendo_pulsado(self):
        """Un interruptor se queda encendido de un despiste y te deja el
        microfono abierto; soltando el dedo se corta."""
        self.assertIn("pointerdown", self.html)
        self.assertIn("pointerup", self.html)
        self.assertIn("pointercancel", self.html)

    def test_soltar_fuera_del_boton_tambien_corta(self):
        """Si sueltas el dedo fuera, el pointerup del boton no llega: hay
        que escucharlo en toda la ventana o te quedas emitiendo sin saberlo."""
        m = re.search(r"for \(const ev of \['pointerup', 'pointercancel'\]\)"
                      r"\s*\{\s*window\.addEventListener", self.html)
        self.assertIsNotNone(m, "pointerup/pointercancel deben ir en window")

    def test_cambiar_de_pestana_corta_la_voz(self):
        self.assertIn("document.hidden) { dejarDeHablar(); }", self.html)
        self.assertIn("'blur', () => dejarDeHablar()", self.html)

    def test_al_dejar_de_hablar_se_suelta_el_microfono(self):
        """Sin parar las pistas queda el punto rojo de 'esta pagina te esta
        escuchando' aunque hayas soltado el boton."""
        soltar = re.search(r"function soltarCaptura\(v\)\s*\{(.*?)\n        \}",
                           self.html, re.S)
        self.assertIsNotNone(soltar)
        self.assertIn("getTracks().forEach(t => t.stop())", soltar.group(1))

        #  Y se suelta NADA MAS soltar el boton, antes de terminar de mandar
        #  la cola: si no, seguiria el punto rojo mientras se vacia.
        cuerpo = re.search(r"function dejarDeHablar\(\)\s*\{(.*?)\n        \}",
                           self.html, re.S)
        self.assertIsNotNone(cuerpo)
        lineas = [l.strip() for l in cuerpo.group(1).splitlines() if l.strip()
                  and not l.strip().startswith("//")]
        self.assertLess(lineas.index("soltarCaptura(v);          // el microfono se suelta YA"),
                        lineas.index("bombearVoz();"),
                        "el microfono se suelta antes de vaciar la cola")

    def test_no_se_pierde_el_final_de_la_frase(self):
        """Al soltar el boton quedan hasta 128 ms a medio juntar y lo que
        hubiera en la cola. Tirarlo se come la ultima silaba: son palabras
        que YA se dijeron, asi que se terminan de mandar."""
        cuerpo = re.search(r"function dejarDeHablar\(\)\s*\{(.*?)\n        \}",
                           self.html, re.S)
        self.assertIsNotNone(cuerpo)
        self.assertIn("volcarVoz(v)", cuerpo.group(1),
                      "hay que mandar lo que quedaba a medio juntar")
        self.assertNotIn("_voz = null", cuerpo.group(1),
                         "tirar _voz aqui perderia la cola sin mandar")
        #  La parrafada se cierra cuando la cola queda vacia, no al soltar.
        bomba = re.search(r"function bombearVoz\(\)\s*\{(.*?)\n        \}",
                          self.html, re.S)
        self.assertIsNotNone(bomba)
        self.assertIn("_voz.cerrando) { terminarVoz(); }", bomba.group(1))

    def test_el_boton_no_se_queda_atascado(self):
        """Si la parrafada anterior no termina de vaciarse (red muerta), el
        boton tiene que seguir funcionando."""
        cuerpo = re.search(r"function empezarAHablar\(\)\s*\{(.*?)\n        \}",
                           self.html, re.S)
        self.assertIsNotNone(cuerpo)
        self.assertIn("if (_voz && !_voz.cerrando) { return; }", cuerpo.group(1))
        self.assertIn("terminarVoz()", cuerpo.group(1))

    def test_se_calla_la_escucha_mientras_se_habla(self):
        """Si no, se acopla: altavoz del robot -> microfono de la camara ->
        navegador -> altavoz -> microfono, y empieza a pitar."""
        cuerpo = re.search(r"function empezarAHablar\(\)\s*\{(.*?)\n        \}",
                           self.html, re.S)
        self.assertIsNotNone(cuerpo)
        self.assertIn("pararAudio()", cuerpo.group(1))
        self.assertIn("echoCancellation: true", cuerpo.group(1))

    def test_se_manda_PCM_crudo_y_no_webm(self):
        """MediaRecorder daria Opus, y descomprimirlo en la Pi pediria
        ffmpeg. La Pi no descodifica nada."""
        #  Se busca el USO, no la palabra: en el codigo hay un comentario
        #  que explica justamente por que NO se usa MediaRecorder.
        self.assertNotIn("new MediaRecorder", self.html)
        self.assertIn("application/octet-stream", self.html)
        self.assertIn("Int16Array", self.html)

    def test_un_envio_cada_vez_y_numerado(self):
        """En paralelo no se garantiza el orden de llegada y la voz saldria
        con las silabas cambiadas de sitio."""
        self.assertIn("'/hablar?seq=' + _voz.seq", self.html)
        cuerpo = re.search(r"function bombearVoz\(\)\s*\{(.*?)\n        \}",
                           self.html, re.S)
        self.assertIsNotNone(cuerpo)
        self.assertIn("_voz.enviando", cuerpo.group(1))

    def test_se_tiran_los_trozos_viejos_si_la_red_se_atasca(self):
        """La voz de hace tres segundos no sirve: mejor perderla que
        acumular retraso."""
        self.assertIn("VOZ_COLA_MAXIMA", self.html)
        self.assertIn("_voz.cola.shift()", self.html)

    def test_hablar_y_escuchar_se_distinguen_de_un_vistazo(self):
        self.assertIn(".fs-btn.hablando", self.html)
        self.assertIn(".fs-btn.sonando", self.html)

    def test_respeta_a_quien_pidio_menos_animaciones(self):
        self.assertIn("prefers-reduced-motion", self.html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
