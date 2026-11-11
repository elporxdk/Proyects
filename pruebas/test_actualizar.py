#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pruebas del actualizador de la Raspberry Pi (actualizar.sh)
===========================================================
Se ejecuta el actualizar.sh DE VERDAD contra un "GitHub" de mentira (un
repositorio git en una carpeta temporal) y una "Pi" de mentira (un clon de
ese repositorio, con su propio HOME). No toca nada de la maquina: ni el
~/.medibot de verdad, ni systemd, ni sudo (se sustituyen por unos falsos).

    python3 pruebas/test_actualizar.py
"""

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import textwrap
import unittest

_RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACTUALIZAR = os.path.join(_RAIZ, "actualizar.sh")

#  systemctl falso: 'is-enabled' dice que si solo con FALSO_AUTO=1.
SYSTEMCTL_FALSO = """#!/bin/sh
case "$1" in
    is-enabled) [ "${FALSO_AUTO:-}" = "1" ] ;;
    *) exit 0 ;;
esac
"""

#  sudo falso: solo funciona sin contraseña con FALSO_SUDO=1.
SUDO_FALSO = """#!/bin/sh
[ "$1" = "-n" ] && shift
[ "${FALSO_SUDO:-}" = "1" ] || exit 1
exec "$@"
"""

#  instalar.sh de mentira: deja constancia de que se ha ejecutado.
INSTALAR_FALSO = """#!/usr/bin/env bash
# version {version}
echo ejecutado >> "$HOME/instalador-ejecutado"
"""

MAIN_PY = 'print("MEDIBOT")\n'
OTRO_PY = textwrap.dedent("""\
    VELOCIDAD = 100
    NOMBRE = "medibot"
    """)


def puerto_abierto(puerto):
    try:
        with socket.create_connection(("127.0.0.1", puerto), timeout=0.3):
            return True
    except OSError:
        return False


class PruebasActualizar(unittest.TestCase):

    def setUp(self):
        #  En una Pi con MEDIBOT abierto, el actualizador lo veria (puerto
        #  5001) y apagaria el hub (5055): mejor no probar ahi.
        if puerto_abierto(5001) or puerto_abierto(5055):
            self.skipTest("hay un MEDIBOT corriendo en esta maquina (puertos 5001/5055)")
        #  Como root el actualizador se niega a todo (a proposito).
        if os.geteuid() == 0 and self._testMethodName != "test_como_root_no":
            self.skipTest("como root el actualizador no corre: prueba con un usuario normal")

        self.tmp = tempfile.mkdtemp(prefix="medibot-actualizar-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = os.path.join(self.tmp, "home")
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.home)
        os.makedirs(self.bin)
        for nombre, texto in (("systemctl", SYSTEMCTL_FALSO), ("sudo", SUDO_FALSO)):
            ruta = os.path.join(self.bin, nombre)
            with open(ruta, "w") as f:
                f.write(texto)
            os.chmod(ruta, 0o755)

        gitconfig = os.path.join(self.tmp, "gitconfig")
        with open(gitconfig, "w") as f:
            f.write("[user]\n\tname = Prueba\n\temail = prueba@example.com\n"
                    "[commit]\n\tgpgsign = false\n"
                    "[init]\n\tdefaultBranch = main\n")
        self.env = {
            "PATH": self.bin + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": self.home,
            "LANG": "C.UTF-8",
            "GIT_CONFIG_GLOBAL": gitconfig,
            "GIT_CONFIG_NOSYSTEM": "1",
        }

        #  "GitHub": un repositorio desnudo, con lo minimo de MEDIBOT.
        self.origen = os.path.join(self.tmp, "github.git")
        self.trabajo = os.path.join(self.tmp, "trabajo")   # donde se "programa"
        self.git(None, "init", "-q", "--bare", "-b", "main", self.origen)
        self.git(None, "clone", "-q", self.origen, self.trabajo)
        self.escribir(self.trabajo, "medibot/main.py", MAIN_PY)
        self.escribir(self.trabajo, "medibot/otro.py", OTRO_PY)
        self.escribir(self.trabajo, "instalar.sh", INSTALAR_FALSO.format(version=1))
        shutil.copy(ACTUALIZAR, os.path.join(self.trabajo, "actualizar.sh"))
        self.subir("primera version")

        #  La "Pi": un clon de la rama main, como el que deja instalar.sh.
        self.pi = os.path.join(self.tmp, "pi", "Proyects")
        self.git(None, "clone", "-q", "-b", "main", self.origen, self.pi)

    # ------------------------------------------------------------ ayudas

    def git(self, carpeta, *args):
        base = ["git"] if carpeta is None else ["git", "-C", carpeta]
        r = subprocess.run(base + list(args), env=self.env, check=True,
                           capture_output=True, text=True)
        return r.stdout.strip()

    def escribir(self, carpeta, ruta, texto):
        completa = os.path.join(carpeta, ruta)
        os.makedirs(os.path.dirname(completa), exist_ok=True)
        with open(completa, "w") as f:
            f.write(texto)

    def leer(self, carpeta, ruta):
        with open(os.path.join(carpeta, ruta)) as f:
            return f.read()

    def subir(self, mensaje):
        """Hace un commit en 'trabajo' y lo sube al GitHub de mentira."""
        self.git(self.trabajo, "add", "-A")
        self.git(self.trabajo, "commit", "-q", "-m", mensaje)
        self.git(self.trabajo, "push", "-q", "origin", "HEAD:main")
        return self.git(self.trabajo, "rev-parse", "HEAD")

    def actualizar(self, *opciones, **env_extra):
        env = dict(self.env, **env_extra)
        return subprocess.run(
            ["bash", os.path.join(self.pi, "actualizar.sh"), *opciones],
            env=env, capture_output=True, text=True, timeout=120,
            stdin=subprocess.DEVNULL)

    def head_pi(self):
        return self.git(self.pi, "rev-parse", "HEAD")

    def registro(self):
        ruta = os.path.join(self.home, ".medibot", "actualizar.log")
        if not os.path.exists(ruta):
            return ""
        with open(ruta) as f:
            return f.read()

    def respaldos(self):
        ruta = os.path.join(self.home, ".medibot", "respaldos")
        return sorted(os.listdir(ruta)) if os.path.isdir(ruta) else []

    def instalador_ejecutado(self):
        ruta = os.path.join(self.home, "instalador-ejecutado")
        if not os.path.exists(ruta):
            return 0
        with open(ruta) as f:
            return len(f.read().splitlines())

    def medibot_abierto(self):
        """Un proceso que parece MEDIBOT (python ... main.py, en la carpeta
        de la Pi), como el que deja la orden 'medibot'."""
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(120)", "main.py"],
            cwd=os.path.join(self.pi, "medibot"))
        self.addCleanup(proc.wait)
        self.addCleanup(proc.kill)
        return proc

    def explicar(self, r):
        return f"\n--- salida ---\n{r.stdout}\n--- errores ---\n{r.stderr}"

    # ------------------------------------------------------------ pruebas

    def test_si_ya_esta_al_dia_no_cambia_nada(self):
        antes = self.head_pi()
        r = self.actualizar()
        self.assertEqual(r.returncode, 0, self.explicar(r))
        self.assertIn("ya esta al dia", r.stdout)
        self.assertEqual(self.head_pi(), antes)
        self.assertEqual(self.instalador_ejecutado(), 0)

    def test_baja_la_version_nueva(self):
        self.escribir(self.trabajo, "medibot/otro.py",
                      OTRO_PY.replace("100", "120"))
        nuevo = self.subir("velocidad 120")

        r = self.actualizar()
        self.assertEqual(r.returncode, 0, self.explicar(r))
        self.assertEqual(self.head_pi(), nuevo)
        self.assertIn("VELOCIDAD = 120", self.leer(self.pi, "medibot/otro.py"))
        self.assertIn("velocidad 120", r.stdout)          # lista de cambios
        self.assertIn("actualizado", self.registro())
        #  instalar.sh no cambio: no hace falta volver a pasarlo.
        self.assertEqual(self.instalador_ejecutado(), 0)

    def test_los_cambios_a_mano_se_mantienen(self):
        self.escribir(self.pi, "medibot/main.py", 'print("MEDIBOT de mi Pi")\n')
        self.escribir(self.trabajo, "medibot/otro.py",
                      OTRO_PY.replace("100", "120"))
        nuevo = self.subir("velocidad 120")

        r = self.actualizar()
        self.assertEqual(r.returncode, 0, self.explicar(r))
        self.assertEqual(self.head_pi(), nuevo)
        self.assertIn("VELOCIDAD = 120", self.leer(self.pi, "medibot/otro.py"))
        self.assertIn("de mi Pi", self.leer(self.pi, "medibot/main.py"))
        #  Copia de seguridad del cambio, y nada olvidado en el stash.
        self.assertTrue(any(f.startswith("cambios-") for f in self.respaldos()))
        self.assertEqual(self.git(self.pi, "stash", "list"), "")

    def test_si_los_cambios_a_mano_chocan_gana_la_version_nueva_sin_perderlos(self):
        self.escribir(self.pi, "medibot/otro.py", OTRO_PY.replace("100", "80"))
        self.escribir(self.trabajo, "medibot/otro.py",
                      OTRO_PY.replace("100", "120"))
        nuevo = self.subir("velocidad 120")

        r = self.actualizar()
        self.assertEqual(r.returncode, 0, self.explicar(r))
        self.assertEqual(self.head_pi(), nuevo)
        otro = self.leer(self.pi, "medibot/otro.py")
        self.assertIn("VELOCIDAD = 120", otro)
        self.assertNotIn("<<<<<<<", otro)                # sin marcas de conflicto
        self.assertEqual(self.git(self.pi, "status", "--porcelain",
                                  "--untracked-files=no"), "")
        #  El cambio a mano sigue guardado: en el parche y en el stash.
        parches = [f for f in self.respaldos() if f.startswith("cambios-")]
        self.assertEqual(len(parches), 1)
        parche = self.leer(os.path.join(self.home, ".medibot", "respaldos"), parches[0])
        self.assertIn("VELOCIDAD = 80", parche)
        self.assertIn("medibot-actualizar", self.git(self.pi, "stash", "list"))
        self.assertIn("NO aplicados", self.registro())

    def test_una_version_rota_no_se_instala(self):
        self.escribir(self.pi, "medibot/main.py", 'print("MEDIBOT de mi Pi")\n')
        antes = self.head_pi()
        self.escribir(self.trabajo, "medibot/otro.py", "def roto(:\n")
        self.subir("version con un error")

        r = self.actualizar()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("errores", r.stderr)
        #  Todo como estaba: la version anterior y el cambio a mano.
        self.assertEqual(self.head_pi(), antes)
        self.assertEqual(self.leer(self.pi, "medibot/otro.py"), OTRO_PY)
        self.assertIn("de mi Pi", self.leer(self.pi, "medibot/main.py"))
        self.assertEqual(self.git(self.pi, "stash", "list"), "")
        self.assertEqual(self.registro().count("ERROR"), 1)
        #  La automatica no la reintenta cada hora (ni llena el registro)...
        r = self.actualizar("--auto")
        self.assertEqual(r.returncode, 0, self.explicar(r))
        self.assertIn("ya se rechazo", r.stdout)
        self.assertEqual(self.head_pi(), antes)
        self.assertEqual(self.registro().count("ERROR"), 1)
        #  ...pero en cuanto se arregla en GitHub, se instala.
        self.escribir(self.trabajo, "medibot/otro.py", OTRO_PY.replace("100", "120"))
        nuevo = self.subir("arreglado")
        r = self.actualizar("--auto")
        self.assertEqual(r.returncode, 0, self.explicar(r))
        self.assertEqual(self.head_pi(), nuevo)
        self.assertIn("de mi Pi", self.leer(self.pi, "medibot/main.py"))

    def test_un_actualizador_roto_tampoco(self):
        """Si se instalara un actualizar.sh roto, la Pi ya no podria
        actualizarse para arreglarlo."""
        antes = self.head_pi()
        self.escribir(self.trabajo, "actualizar.sh", "if then fi\n")
        self.subir("actualizador roto")

        r = self.actualizar()
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.head_pi(), antes)
        with open(ACTUALIZAR) as f:
            self.assertEqual(self.leer(self.pi, "actualizar.sh"), f.read())

    def test_los_commits_hechos_en_la_pi_se_guardan_en_una_rama(self):
        self.escribir(self.pi, "medibot/notas.py", "# notas de la Pi\n")
        self.git(self.pi, "add", "-A")
        self.git(self.pi, "commit", "-q", "-m", "commit hecho en la Pi")
        local = self.head_pi()
        self.escribir(self.trabajo, "medibot/otro.py",
                      OTRO_PY.replace("100", "120"))
        nuevo = self.subir("velocidad 120")

        r = self.actualizar()
        self.assertEqual(r.returncode, 0, self.explicar(r))
        self.assertEqual(self.head_pi(), nuevo)
        ramas = self.git(self.pi, "branch", "--format=%(refname:short)").split()
        respaldo = [b for b in ramas if b.startswith("respaldo-")]
        self.assertEqual(len(respaldo), 1, ramas)
        self.assertEqual(self.git(self.pi, "rev-parse", respaldo[0]), local)

    def test_un_fichero_suelto_con_el_nombre_de_uno_nuevo_se_aparta(self):
        self.escribir(self.pi, "medibot/nuevo.py", "# mio\n")
        self.escribir(self.trabajo, "medibot/nuevo.py", "# de GitHub\n")
        nuevo = self.subir("fichero nuevo")

        r = self.actualizar()
        self.assertEqual(r.returncode, 0, self.explicar(r))
        self.assertEqual(self.head_pi(), nuevo)
        self.assertEqual(self.leer(self.pi, "medibot/nuevo.py"), "# de GitHub\n")
        apartados = [f for f in self.respaldos() if f.startswith("archivos-")]
        self.assertEqual(len(apartados), 1)
        self.assertEqual(
            self.leer(os.path.join(self.home, ".medibot", "respaldos", apartados[0]),
                      "medibot/nuevo.py"), "# mio\n")

    def test_los_datos_de_la_pi_no_se_tocan(self):
        """Lo que git ignora (datos del pastillero, caras...) se queda igual."""
        self.escribir(self.trabajo, ".gitignore", "pillbox_data.json\n")
        self.subir("gitignore")
        self.assertEqual(self.actualizar().returncode, 0)
        self.escribir(self.pi, "medibot/pillbox_data.json", '{"dosis": 3}\n')
        self.escribir(self.trabajo, "medibot/otro.py",
                      OTRO_PY.replace("100", "120"))
        self.subir("velocidad 120")

        r = self.actualizar()
        self.assertEqual(r.returncode, 0, self.explicar(r))
        self.assertEqual(self.leer(self.pi, "medibot/pillbox_data.json"),
                         '{"dosis": 3}\n')

    def test_si_cambia_instalar_sh_se_vuelve_a_pasar(self):
        self.escribir(self.trabajo, "instalar.sh", INSTALAR_FALSO.format(version=2))
        self.subir("instalador nuevo")
        r = self.actualizar()
        self.assertEqual(r.returncode, 0, self.explicar(r))
        self.assertEqual(self.instalador_ejecutado(), 1)

    def test_la_automatica_no_toca_un_medibot_abierto(self):
        antes = self.head_pi()
        self.escribir(self.trabajo, "medibot/otro.py",
                      OTRO_PY.replace("100", "120"))
        nuevo = self.subir("velocidad 120")
        self.medibot_abierto()

        r = self.actualizar("--auto")
        self.assertEqual(r.returncode, 0, self.explicar(r))
        self.assertEqual(self.head_pi(), antes)
        #  ...pero ya lo ha descargado.
        self.assertEqual(self.git(self.pi, "rev-parse", "origin/main"), nuevo)
        self.assertIn("se instalara", r.stdout)
        self.assertEqual(self.registro().count("se instalara"), 1)
        #  Comprobando otra vez (la hora siguiente) no se repite en el registro.
        self.actualizar("--auto")
        self.assertEqual(self.registro().count("se instalara"), 1)

    def test_la_automatica_instala_si_medibot_esta_cerrado(self):
        self.escribir(self.trabajo, "medibot/otro.py",
                      OTRO_PY.replace("100", "120"))
        nuevo = self.subir("velocidad 120")
        r = self.actualizar("--auto")
        self.assertEqual(r.returncode, 0, self.explicar(r))
        self.assertEqual(self.head_pi(), nuevo)

    def test_al_arrancar_instala_lo_descargado_sin_internet(self):
        antes = self.head_pi()
        self.escribir(self.trabajo, "medibot/otro.py",
                      OTRO_PY.replace("100", "120"))
        nuevo = self.subir("velocidad 120")
        abierto = self.medibot_abierto()
        self.assertEqual(self.actualizar("--auto").returncode, 0)
        self.assertEqual(self.head_pi(), antes)
        abierto.kill()
        abierto.wait()
        #  Sin internet: el "GitHub" desaparece.
        os.rename(self.origen, self.origen + ".desconectado")

        #  Sin la automatica activada, 'medibot' arranca sin tocar nada...
        r = self.actualizar("--al-arrancar")
        self.assertEqual(r.returncode, 0, self.explicar(r))
        self.assertEqual(self.head_pi(), antes)
        #  ...y con ella, instala lo que ya estaba descargado.
        r = self.actualizar("--al-arrancar", FALSO_AUTO="1")
        self.assertEqual(r.returncode, 0, self.explicar(r))
        self.assertEqual(self.head_pi(), nuevo)

    def test_al_arrancar_no_dice_nada_si_no_hay_nada(self):
        r = self.actualizar("--al-arrancar", FALSO_AUTO="1")
        self.assertEqual(r.returncode, 0, self.explicar(r))
        self.assertEqual(r.stdout.strip(), "")

    def test_la_automatica_sin_sudo_deja_pendiente_el_instalador(self):
        self.escribir(self.trabajo, "instalar.sh", INSTALAR_FALSO.format(version=2))
        nuevo = self.subir("instalador nuevo")
        pendiente = os.path.join(self.home, ".medibot", "instalar-pendiente")

        #  Sin sudo sin contraseña: el codigo si se actualiza, el instalador
        #  queda pendiente (no se queda esperando una contraseña).
        r = self.actualizar("--auto")
        self.assertEqual(r.returncode, 0, self.explicar(r))
        self.assertEqual(self.head_pi(), nuevo)
        self.assertEqual(self.instalador_ejecutado(), 0)
        self.assertTrue(os.path.exists(pendiente))
        #  'medibot' lo recuerda al arrancar.
        r = self.actualizar("--al-arrancar", FALSO_AUTO="1")
        self.assertIn("medibot-actualizar", r.stderr)

        #  La siguiente vez a mano (aunque no haya version nueva) se pasa.
        r = self.actualizar()
        self.assertEqual(r.returncode, 0, self.explicar(r))
        self.assertEqual(self.instalador_ejecutado(), 1)
        self.assertFalse(os.path.exists(pendiente))

    def test_la_automatica_con_sudo_pasa_el_instalador(self):
        self.escribir(self.trabajo, "instalar.sh", INSTALAR_FALSO.format(version=2))
        self.subir("instalador nuevo")
        r = self.actualizar("--auto", FALSO_SUDO="1")
        self.assertEqual(r.returncode, 0, self.explicar(r))
        self.assertEqual(self.instalador_ejecutado(), 1)

    def test_en_otra_rama_no_se_actualiza(self):
        self.git(self.pi, "checkout", "-q", "-b", "pruebas")
        antes = self.head_pi()
        self.escribir(self.trabajo, "medibot/otro.py",
                      OTRO_PY.replace("100", "120"))
        self.subir("velocidad 120")

        r = self.actualizar()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("pruebas", r.stderr)
        #  La automatica tampoco, pero sin darlo por error cada hora.
        r = self.actualizar("--auto")
        self.assertEqual(r.returncode, 0, self.explicar(r))
        self.assertEqual(self.head_pi(), antes)

    def test_estado(self):
        self.escribir(self.trabajo, "medibot/otro.py",
                      OTRO_PY.replace("100", "120"))
        self.subir("velocidad 120")
        r = self.actualizar("--estado")
        self.assertEqual(r.returncode, 0, self.explicar(r))
        self.assertIn("HAY VERSION NUEVA", r.stdout)
        self.assertIn("desactivada", r.stdout)

    def test_como_root_no(self):
        if os.geteuid() != 0:
            self.skipTest("solo se puede probar siendo root")
        r = self.actualizar()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("root", r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
