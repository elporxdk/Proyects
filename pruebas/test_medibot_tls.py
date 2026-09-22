#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pruebas del certificado propio de la Pi
=======================================
Sirve la web por HTTPS sin depender de nada de fuera, que es lo unico que
hace falta para poder HABLAR por el altavoz desde otro equipo de casa.

    python3 pruebas/test_medibot_tls.py
"""

import datetime
import os
import shutil
import ssl
import sys
import tempfile
import unittest

_RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(_RAIZ, "medibot")]

import medibot_tls as tls

hay_openssl = tls.hay_openssl()


class PruebasSinOpenssl(unittest.TestCase):
    """Lo que se puede comprobar sin generar nada."""

    def test_el_certificado_vale_para_las_IPs_de_la_Pi(self):
        nombres = tls._nombres(["192.168.1.50", "10.0.0.7"])
        self.assertIn("IP:192.168.1.50", nombres)
        self.assertIn("IP:10.0.0.7", nombres)

    def test_y_tambien_para_localhost_y_medibot_local(self):
        """Para que valga desde la propia Pi y por mDNS sin avisos de mas."""
        nombres = tls._nombres([])
        self.assertIn("IP:127.0.0.1", nombres)
        self.assertIn("DNS:localhost", nombres)
        self.assertIn("DNS:medibot.local", nombres)

    def test_no_repite_una_IP(self):
        nombres = tls._nombres(["127.0.0.1", "127.0.0.1"])
        self.assertEqual(nombres.count("IP:127.0.0.1"), 1)

    def test_sin_openssl_dice_como_instalarlo(self):
        real = tls.hay_openssl
        tls.hay_openssl = lambda: False
        try:
            ok, motivo = tls.generar(["127.0.0.1"], "/tmp/no.crt", "/tmp/no.key")
            self.assertFalse(ok)
            self.assertIn("openssl", motivo)
        finally:
            tls.hay_openssl = real

    def test_sin_certificado_hay_que_hacerlo(self):
        hace_falta, motivo = tls.hace_falta_rehacerlo(
            ["127.0.0.1"], "/tmp/no-existe.crt", "/tmp/no-existe.key")
        self.assertTrue(hace_falta)
        self.assertIn("no habia", motivo)

    def test_la_clave_privada_esta_fuera_del_repositorio(self):
        """Una clave privada no se sube a ningun sitio, ni por descuido."""
        reglas = open(os.path.join(_RAIZ, ".gitignore"), encoding="utf-8").read()
        self.assertIn("medibot/certificados/", reglas)
        self.assertIn("*.key", reglas)


@unittest.skipUnless(hay_openssl, "necesita openssl")
class PruebasGenerando(unittest.TestCase):
    """Generando de verdad, que es donde se ve si funciona."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.crt = os.path.join(self.dir, "m.crt")
        self.key = os.path.join(self.dir, "m.key")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_genera_certificado_y_clave(self):
        ok, motivo = tls.generar(["192.168.1.50"], self.crt, self.key)
        self.assertTrue(ok, motivo)
        self.assertTrue(os.path.exists(self.crt))
        self.assertTrue(os.path.exists(self.key))

    def test_la_clave_solo_la_lee_su_dueno(self):
        tls.generar(["192.168.1.50"], self.crt, self.key)
        self.assertEqual(os.stat(self.key).st_mode & 0o777, 0o600)

    def test_lleva_dentro_la_IP_de_la_Pi(self):
        tls.generar(["192.168.1.50"], self.crt, self.key)
        ips = tls.ips_del_certificado(self.crt)
        self.assertIn("192.168.1.50", ips)
        self.assertIn("127.0.0.1", ips)

    def test_no_caduca_manana(self):
        """Que caduque solo sirve para que un dia deje de funcionar sin que
        nadie se acuerde de por que."""
        tls.generar(["192.168.1.50"], self.crt, self.key)
        fin = tls.caduca(self.crt)
        self.assertIsNotNone(fin)
        self.assertGreater(fin, datetime.datetime.utcnow()
                           + datetime.timedelta(days=365 * 5))

    def test_se_reutiliza_el_que_haya(self):
        """Rehacerlo en cada arranque tarda y obligaria a aceptar el aviso
        del navegador otra vez en cada dispositivo."""
        tls.asegurar(["192.168.1.50"], self.crt, self.key)
        antes = open(self.crt, "rb").read()
        rutas, motivo = tls.asegurar(["192.168.1.50"], self.crt, self.key)
        self.assertIsNotNone(rutas)
        self.assertEqual(motivo, "", "no deberia haberlo rehecho")
        self.assertEqual(open(self.crt, "rb").read(), antes)

    def test_si_la_Pi_cambia_de_IP_se_rehace(self):
        """Los routers reparten por DHCP y la IP cambia cada dos por tres."""
        tls.asegurar(["192.168.1.50"], self.crt, self.key)
        antes = open(self.crt, "rb").read()
        rutas, motivo = tls.asegurar(["192.168.1.77"], self.crt, self.key)
        self.assertIsNotNone(rutas)
        self.assertIn("direcciones nuevas", motivo)
        self.assertNotEqual(open(self.crt, "rb").read(), antes)
        self.assertIn("192.168.1.77", tls.ips_del_certificado(self.crt))

    def test_si_esta_por_caducar_se_rehace(self):
        tls.generar(["192.168.1.50"], self.crt, self.key)
        fin = tls.caduca(self.crt)
        #  Se pregunta como si estuvieramos a una semana de que caduque.
        hace_falta, motivo = tls.hace_falta_rehacerlo(
            ["192.168.1.50"], self.crt, self.key,
            ahora=fin - datetime.timedelta(days=7))
        self.assertTrue(hace_falta)
        self.assertIn("caducar", motivo)

    def test_un_certificado_roto_se_rehace(self):
        open(self.crt, "w").write("esto no es un certificado")
        open(self.key, "w").write("ni esto una clave")
        hace_falta, motivo = tls.hace_falta_rehacerlo(
            ["192.168.1.50"], self.crt, self.key)
        self.assertTrue(hace_falta)
        self.assertIn("no se pudo leer", motivo)

    def test_sirve_DE_VERDAD_para_levantar_un_HTTPS(self):
        """LA prueba: que un servidor TLS real lo acepte, no que 'parezca'
        un certificado."""
        import socket
        import threading
        rutas, _ = tls.asegurar(["127.0.0.1"], self.crt, self.key)
        self.assertIsNotNone(rutas)

        contexto = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        contexto.load_cert_chain(self.crt, self.key)   # lanza si no sirve

        escucha = socket.socket()
        escucha.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        escucha.bind(("127.0.0.1", 0))
        escucha.listen(1)
        puerto = escucha.getsockname()[1]
        visto = {}

        def servir():
            try:
                bruto, _ = escucha.accept()
                with contexto.wrap_socket(bruto, server_side=True) as tls_sock:
                    tls_sock.recv(16)
                    tls_sock.sendall(b"hola")
            except Exception as e:                       # noqa: BLE001
                visto["error"] = e

        hilo = threading.Thread(target=servir, daemon=True)
        hilo.start()

        cliente_ctx = ssl.create_default_context()
        cliente_ctx.check_hostname = False       # se lo firma la Pi a si misma
        cliente_ctx.verify_mode = ssl.CERT_NONE
        with socket.create_connection(("127.0.0.1", puerto), timeout=10) as cru:
            with cliente_ctx.wrap_socket(cru) as seguro:
                version = seguro.version()
                seguro.sendall(b"hey")
                respuesta = seguro.recv(16)
        hilo.join(timeout=5)

        self.assertIsNone(visto.get("error"))
        self.assertEqual(respuesta, b"hola")
        self.assertTrue(version.startswith("TLS"), version)


if __name__ == "__main__":
    unittest.main(verbosity=2)
