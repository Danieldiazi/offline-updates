# -*- coding: utf-8 -*-
"""Pruebas de joomla_offline.py. No necesitan conexión: todas las fuentes se simulan con
ficheros locales servidos como URLs file:// (urllib las admite igual que http/https).

    python -m unittest discover -s tests -v
"""

import functools
import hashlib
import http.server
import io
import json
import os
import pathlib
import sys
import tempfile
import threading
import unittest
import xml.etree.ElementTree as ET
import zipfile
from contextlib import redirect_stdout

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import joomla_offline as jo  # noqa: E402


def zip_con(ruta, ficheros):
    """Crea un ZIP con {nombre: contenido} y devuelve sus hashes."""
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    with zipfile.ZipFile(ruta, "w") as z:
        for nombre, contenido in ficheros.items():
            z.writestr(nombre, contenido)
    datos = pathlib.Path(ruta).read_bytes()
    return {a: hashlib.new(a, datos).hexdigest() for a in ("sha256", "sha384", "sha512")}


def url_de(ruta):
    return pathlib.Path(ruta).resolve().as_uri()


def manifiesto(tipo, nombre, version, extra=""):
    return ('<?xml version="1.0" encoding="utf-8"?><extension type="%s" client="site" method="upgrade">'
            "<name>%s</name><version>%s</version>%s</extension>" % (tipo, nombre, version, extra))


# --------------------------------------------------------------------------- #
# Funciones auxiliares
# --------------------------------------------------------------------------- #

class TestNombres(unittest.TestCase):
    def test_nombre_desde_url(self):
        casos = {
            "https://x/attachments-5.0.0.zip": "attachments-5.0.0.zip",
            # downloads.joomla.org sirve los idiomas sin extensión
            "https://downloads.joomla.org/x/es-es_joomla_lang_full_6-2-0v1-zip?format=raw":
                "es-es_joomla_lang_full_6-2-0v1.zip",
            # JCE: descarga servida por un script con el nombre real en un parámetro
            "https://www.joomlacontenteditor.net/index.php?option=com_releases&task=update.download"
            "&file=pkg_jce_304.zip": "pkg_jce_304.zip",
            "https://x/descarga": "descarga.zip",
        }
        for url, esperado in casos.items():
            self.assertEqual(jo.nombre_desde_url(url), esperado, url)

    def test_slug_translitera(self):
        self.assertEqual(jo.slug("Attachments - Español"), "attachments-espanol")


class TestVersiones(unittest.TestCase):
    def test_orden(self):
        orden = ["4.2.4", "5.0.0-beta1", "5.0.0-rc1", "5.0.0", "5.0.1", "5.10.0"]
        self.assertEqual(sorted(reversed(orden), key=jo.clave_version), orden)

    def test_regex_compatible_como_joomla(self):
        # Joomla hace preg_match('/^' . targetplatform . '/', JVERSION)
        self.assertTrue(jo.regex_compatible(r"(5\.[0123]|6\.[01])", "6.1.0"))
        self.assertFalse(jo.regex_compatible(r"(5\.[0123]|6\.[01])", "6.2.0"))
        self.assertTrue(jo.regex_compatible(r"[56]\.", "6.2.0"))

    def test_compatible_con_ramas(self):
        espejo = type("E", (), {"ramas": ["6"]})()
        compatible = lambda tp: jo.Espejo._compatible_con_ramas(espejo, tp)  # noqa: E731
        self.assertTrue(compatible(r"(5\.[0123]|6\.[01])"))
        self.assertTrue(compatible(r"6\.\d+\.\d+"))
        self.assertFalse(compatible(r"4\.[012]"))
        self.assertTrue(compatible(None))


class TestManifiestos(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def leer(self, ficheros):
        ruta = os.path.join(self.dir, "p.zip")
        zip_con(ruta, ficheros)
        return jo.leer_manifiesto_zip(ruta)

    def test_plantilla_quita_espacios_como_joomla(self):
        info = self.leer({"templateDetails.xml": manifiesto("template", "JL Prueba Plantilla", "1.1.0")})
        self.assertEqual((info["type"], info["element"], info["version"]), ("template", "jlpruebaplantilla", "1.1.0"))

    def test_element_explicito(self):
        info = self.leer({"templateDetails.xml": manifiesto("template", "Wilson", "2.1.1", "<element>jl_wilson</element>")})
        self.assertEqual(info["element"], "jl_wilson")

    def test_paquete_componente_y_file(self):
        self.assertEqual(self.leer({"pkg_x.xml": manifiesto("package", "X", "1", "<packagename>gantry5</packagename>")})["element"],
                         "pkg_gantry5")
        self.assertEqual(self.leer({"x.xml": manifiesto("component", "COM_USERTOOLS", "1")})["element"], "com_usertools")
        self.assertEqual(self.leer({"attachments_es_es_language_pack.xml": manifiesto("file", "X", "1")})["element"],
                         "attachments_es_es_language_pack")

    def test_manifiesto_en_subcarpeta(self):
        info = self.leer({"plantilla/templateDetails.xml": manifiesto("template", "Sub", "1.0.0")})
        self.assertEqual(info["element"], "sub")

    def test_ignora_xml_enormes(self):
        enorme = manifiesto("template", "Enorme", "1.0.0") + "<!--" + "x" * (jo.MAX_MANIFIESTO + 1) + "-->"
        with self.assertRaises(jo.ErrorEspejo):
            self.leer({"templateDetails.xml": enorme})


class TestChangelog(unittest.TestCase):
    def items(self, xml, categoria):
        raiz = ET.fromstring(xml.split("\n", 1)[1])
        return [i.text for i in raiz.findall("changelog/%s/item" % categoria)]

    def test_un_item_por_linea(self):
        xml = jo.xml_changelog("tpl", "template", "1.1.0", "Corregido el menú móvil\n\nNueva posición\n")
        self.assertEqual(self.items(xml, "note"), ["Corregido el menú móvil", "Nueva posición"])

    def test_markdown_de_github_por_categorias(self):
        notas = ("## What's Changed\n* Mejora de rendimiento by @dev in https://x/pull/1\n\n"
                 "### Fixed\n- **Error** al guardar [#12](https://x/issues/12)\n\n"
                 "### Security\n1. Escapado de `title`\n---\n")
        xml = jo.xml_changelog("com_x", "component", "2.0", notas)
        self.assertEqual(self.items(xml, "change"), ["Mejora de rendimiento by @dev in https://x/pull/1"])
        self.assertEqual(self.items(xml, "fix"), ["Error al guardar #12 (https://x/issues/12)"])
        self.assertEqual(self.items(xml, "security"), ["Escapado de title"])

    def test_items_en_cdata(self):
        # Joomla parte en varios puntos los <item> con caracteres no ASCII si no van en CDATA
        xml = jo.xml_changelog("x", "file", "1", "- Menú corregido ñ")
        self.assertIn("<item><![CDATA[Menú corregido ñ]]></item>", xml)

    def test_items_escapan_html(self):
        # Joomla pinta los <item> como HTML sin escapar: el texto debe llegar ya escapado
        xml = jo.xml_changelog("x", "file", "1", "- Fallo & <script>alert(1)</script> ]]> fin")
        self.assertEqual(self.items(xml, "note"), ["Fallo &amp; &lt;script&gt;alert(1)&lt;/script&gt; ]]&gt; fin"])
        self.assertNotIn("<script>", xml)

    def test_vacio(self):
        self.assertIsNone(jo.xml_changelog("x", "file", "1", "\n  \n## Solo un título\n"))

    def test_xml_update_es_valido(self):
        xml = jo.xml_update("pkg_x", "package", "1.0 & <b>", "http://h/x.zip", {"sha256": "ab"}, r"6\.",
                            changelogurl="http://h/c.xml", folder="system")
        u = ET.fromstring(xml.split("\n", 1)[1]).find("update")
        self.assertEqual(u.findtext("version"), "1.0 & <b>")
        self.assertEqual(u.findtext("changelogurl"), "http://h/c.xml")
        self.assertEqual(u.find("targetplatform").get("version"), r"6\.")
        self.assertIsNone(u.find("sha512"))


# --------------------------------------------------------------------------- #
# Espejo completo con fuentes simuladas
# --------------------------------------------------------------------------- #

class _Silencioso(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class TestEspejoCompleto(unittest.TestCase):
    URL_BASE = "http://intranet/joomla-offline"

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = cls.d = cls.tmp.name
        f = os.path.join(d, "fuentes")

        # Core: lista TUF con una versión 5.x y dos 6.x
        targets = {}
        for v in ("5.4.10", "6.1.0", "6.2.0"):
            ruta = os.path.join(f, "core", "Joomla_%s-Stable-Update_Package.zip" % v)
            h = zip_con(ruta, {"index.php": v})
            targets["Joomla_%s" % v] = {"hashes": {"sha512": h["sha512"]}, "custom": {
                "name": "Joomla! %s" % v, "version": v, "stability": "Stable",
                "targetplatform": {"name": "joomla", "version": v[0] + r"\."},
                "downloads": [{"url": url_de(ruta)}]}}
        pathlib.Path(f, "targets.json").write_text(json.dumps({"signed": {"targets": targets}}))

        # XML con varias ramas (estilo Gantry/Weblinks) y una beta más alta que hay que ignorar
        bloques = []
        for v, tp, tag in (("4.5.0", r"4\.", "stable"), ("5.2.0", r"(5\.[01234]|6\.[012])", "stable"),
                           ("5.3.0-beta1", r"6\.", "beta")):
            ruta = os.path.join(f, "weblinks", "pkg_weblinks_%s.zip" % v)
            h = zip_con(ruta, {"pkg_weblinks.xml": manifiesto("package", "Weblinks", v)})
            bloques.append("<update><name>Weblinks</name><element>pkg_weblinks</element><type>package</type>"
                           "<version>%s</version><downloads><downloadurl type=\"full\" format=\"zip\">%s</downloadurl>"
                           "</downloads><tags><tag>%s</tag></tags><targetplatform name=\"joomla\" version=\"%s\"/>"
                           "<sha256>%s</sha256></update>" % (v, url_de(ruta), tag, tp, h["sha256"]))
        pathlib.Path(f, "weblinks.xml").write_text("<updates>%s</updates>" % "".join(bloques))

        # XML sin hash publicado (debe avisar)
        ruta = os.path.join(f, "sinhash", "pkg_sinhash.zip")
        zip_con(ruta, {"pkg_sinhash.xml": manifiesto("package", "SinHash", "1.0.0")})
        pathlib.Path(f, "sinhash.xml").write_text(
            "<updates><update><name>SinHash</name><element>pkg_sinhash</element><type>package</type>"
            "<version>1.0.0</version><downloads><downloadurl>%s</downloadurl></downloads>"
            "<targetplatform name=\"joomla\" version=\"6\\.\"/></update></updates>" % url_de(ruta))

        # Colección (estilo Gantry): framework y plantilla, para Joomla 4 y 6
        entradas = []
        for el, tipo in (("pkg_gantry5", "package"), ("g5_helium", "template")):
            ruta = os.path.join(f, "gantry", "%s_5.6.4.zip" % el)
            h = zip_con(ruta, {"%s.xml" % el: manifiesto(tipo, el, "5.6.4")})
            det = os.path.join(f, "gantry", "%s.xml" % el)
            pathlib.Path(det).write_text(
                "<updates><update><name>%s</name><element>%s</element><type>%s</type><version>5.6.4</version>"
                "<downloads><downloadurl>%s</downloadurl></downloads><targetplatform name=\"joomla\" version=\"[56]\\.\"/>"
                "<sha256>%s</sha256></update></updates>" % (el, el, tipo, url_de(ruta), h["sha256"]))
            for tpv in ("4.*", "6.*"):
                entradas.append('<extension name="%s" element="%s" type="%s" version="5.6.4" targetplatformversion="%s" '
                                'detailsurl="%s"/>' % (el, el, tipo, tpv, url_de(det)))
        pathlib.Path(f, "gantry_list.xml").write_text("<extensionset>%s</extensionset>" % "".join(entradas))

        # GitHub: varias releases (una por idioma), con notas en Markdown
        ruta = os.path.join(f, "gh", "attachments-Spanish-es-ES-language-pack-5.0.0.zip")
        h = zip_con(ruta, {"attachments_es_es_language_pack.xml": manifiesto("file", "Attachments es-ES", "5.0.0")})
        releases = [
            {"tag_name": "French-5.1.0", "draft": False, "prerelease": False, "published_at": "2026-11-02",
             "assets": [{"name": "attachments-French-fr-FR-language-pack-5.1.0.zip", "browser_download_url": "x"}]},
            {"tag_name": "Spanish-5.0.0", "draft": False, "prerelease": False, "published_at": "2026-11-01",
             "html_url": "https://github.com/x/y/releases/tag/Spanish-5.0.0", "body": "### Added\n- Cadenas nuevas",
             "assets": [{"name": ruta and os.path.basename(ruta), "digest": "sha256:" + h["sha256"],
                         "browser_download_url": url_de(ruta)}]},
        ]
        api = os.path.join(f, "api", "repos", "jmcameron", "attachments_translations")
        os.makedirs(api)
        pathlib.Path(api, "releases").write_text(json.dumps(releases))
        # La API se consulta con "?per_page=100": con file:// la consulta formaría parte del
        # nombre del fichero, así que se sirve con un servidor HTTP local (ignora la consulta)
        os.environ["no_proxy"] = os.environ["NO_PROXY"] = "127.0.0.1,localhost"
        manejador = functools.partial(_Silencioso, directory=f)
        cls.servidor = http.server.ThreadingHTTPServer(("127.0.0.1", 0), manejador)
        threading.Thread(target=cls.servidor.serve_forever, daemon=True).start()
        url_api = "http://127.0.0.1:%d/api" % cls.servidor.server_address[1]

        # Paquetes manuales: plantilla 1.0 y 1.1 (con notas), un ZIP que no es de Joomla
        man = os.path.join(d, "manual", "plantillas")
        zip_con(os.path.join(man, "wilson_2.1.0.zip"), {"templateDetails.xml": manifiesto("template", "jl_wilson", "2.1.0")})
        zip_con(os.path.join(man, "wilson_2.1.1.zip"), {"templateDetails.xml": manifiesto("template", "jl_wilson", "2.1.1")})
        pathlib.Path(man, "wilson_2.1.1.changelog.txt").write_text("- Compatible con Gantry 5.6.4\n- Menú corregido\n",
                                                                   encoding="utf-8")
        zip_con(os.path.join(d, "manual", "notas.zip"), {"leeme.txt": "no es Joomla"})

        cls.config = os.path.join(d, "config.json")
        pathlib.Path(cls.config).write_text(json.dumps({
            "url_base": cls.URL_BASE,
            "carpeta_salida": "salida",
            "ramas_joomla": ["6"],
            "idiomas": [],
            "urls_targets_core": [url_de(os.path.join(f, "targets.json"))],
            "url_api_github": url_api,
            "extensiones": [
                {"nombre": "Weblinks", "tipo": "xml", "url": url_de(os.path.join(f, "weblinks.xml"))},
                {"nombre": "Sin hash", "tipo": "xml", "url": url_de(os.path.join(f, "sinhash.xml"))},
                {"nombre": "Gantry 5", "tipo": "coleccion", "url": url_de(os.path.join(f, "gantry_list.xml")),
                 "elementos": ["pkg_gantry5"]},
                {"nombre": "Attachments - Español", "tipo": "github", "repo": "jmcameron/attachments_translations",
                 "patron_tag": "^Spanish-", "patron_zip": r"es-ES.*\.zip$"},
            ],
        }))
        cls.salida = os.path.join(d, "salida")
        cls.codigo = cls.ejecutar()

    @classmethod
    def tearDownClass(cls):
        cls.servidor.shutdown()
        cls.servidor.server_close()
        cls.tmp.cleanup()

    @classmethod
    def ejecutar(cls):
        with open(cls.config, encoding="utf-8") as fh:
            cfg = json.load(fh)
        cls.log = io.StringIO()
        with redirect_stdout(cls.log):
            return jo.Espejo(cfg, cls.config).ejecutar()

    def xml(self, rel):
        return ET.parse(os.path.join(self.salida, *rel.split("/"))).getroot()

    def test_termina_sin_errores(self):
        self.assertEqual(self.codigo, 0, self.log.getvalue())

    def test_core_ultima_de_la_rama(self):
        u = self.xml("core/joomla6.xml").find("update")
        self.assertEqual(u.findtext("version"), "6.2.0")
        self.assertTrue(u.findtext("downloads/downloadurl").startswith(self.URL_BASE + "/core/"))

    def test_xml_elige_estable_compatible(self):
        u = self.xml("extensiones/weblinks/update.xml").find("update")
        self.assertEqual(u.findtext("version"), "5.2.0")
        self.assertEqual(u.findtext("downloads/downloadurl"),
                         self.URL_BASE + "/extensiones/weblinks/pkg_weblinks_5.2.0.zip")

    def test_coleccion_filtrada(self):
        lista = self.xml("extensiones/gantry-5/list.xml").findall("extension")
        self.assertEqual([(e.get("element"), e.get("targetplatformversion")) for e in lista], [("pkg_gantry5", "6.*")])
        self.assertTrue(lista[0].get("detailsurl").startswith(self.URL_BASE + "/extensiones/gantry-5/"))

    def test_github_por_tag_con_changelog(self):
        u = self.xml("extensiones/attachments-espanol/update.xml").find("update")
        self.assertEqual((u.findtext("element"), u.findtext("version")), ("attachments_es_es_language_pack", "5.0.0"))
        cambios = self.xml("extensiones/attachments-espanol/update-changelog.xml")
        self.assertEqual([i.text for i in cambios.findall("changelog/addition/item")], ["Cadenas nuevas"])

    def test_manuales_version_mas_alta_y_notas(self):
        lista = self.xml("manual_xml/list.xml").findall("extension")
        self.assertEqual([(e.get("element"), e.get("version")) for e in lista], [("jl_wilson", "2.1.1")])
        u = self.xml("manual_xml/template-jl-wilson.xml").find("update")
        # Joomla toma el changelog de la entrada de la lista, no del XML de detalle
        self.assertEqual(lista[0].get("changelogurl"), u.findtext("changelogurl"))
        cambios = self.xml(u.findtext("changelogurl")[len(self.URL_BASE) + 1:])
        self.assertEqual([i.text for i in cambios.findall("changelog/note/item")],
                         ["Compatible con Gantry 5.6.4", "Menú corregido"])

    def test_changelog_txt_no_se_lista_ni_se_copia(self):
        self.assertFalse(os.path.exists(os.path.join(self.salida, "manual", "plantillas", "wilson_2.1.1.changelog.txt")))
        self.assertNotIn("changelog.txt", self.log.getvalue())

    def test_avisa_de_paquetes_sin_hash(self):
        leeme = pathlib.Path(self.salida, "LEEME.txt").read_text(encoding="utf-8")
        bloque = leeme.split("* Sin hash")[1].split("\n*")[0]
        self.assertIn("integridad sin verificar", bloque)
        self.assertNotIn("integridad sin verificar", leeme.split("* Weblinks")[1].split("\n*")[0])

    def test_paquete_del_espejo(self):
        with zipfile.ZipFile(os.path.join(self.salida, "joomla-offline-updates.zip")) as z:
            raiz = ET.fromstring(z.read(jo.Espejo.ELEMENTO_PAQUETE + ".xml"))
            self.assertIn("script.php", z.namelist())
        servidores = [s.text for s in raiz.findall("updateservers/server")]
        self.assertIn(self.URL_BASE + "/manual_xml/list.xml", servidores)
        self.assertIn(self.URL_BASE + "/" + jo.Espejo.XML_PAQUETE, servidores)
        propio = self.xml(jo.Espejo.XML_PAQUETE).find("update")
        self.assertEqual(propio.findtext("version"), raiz.findtext("version"))

    def test_segunda_ejecucion_estable(self):
        version = self.xml(jo.Espejo.XML_PAQUETE).find("update").findtext("version")
        self.assertEqual(self.ejecutar(), 0)
        self.assertEqual(self.xml(jo.Espejo.XML_PAQUETE).find("update").findtext("version"), version)
        self.assertNotIn("NUEVO", self.log.getvalue().split("Carpeta lista")[1])


if __name__ == "__main__":
    unittest.main()
