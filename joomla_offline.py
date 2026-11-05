#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
joomla_offline.py — Espejo de actualizaciones de Joomla para entornos sin internet.

Se ejecuta en una máquina CON internet. Descarga las últimas versiones del core
de Joomla, de los paquetes de idioma y de las extensiones configuradas, verifica
sus hashes y genera una carpeta autocontenida que:

  1. Sirve para subir los ZIP a mano ("Subir y actualizar" / "Instalar extensión").
  2. Funciona como servidor de actualizaciones local: una vez servida por HTTP en
     la red aislada y registrado con el paquete instalable, cada Joomla detecta
     las actualizaciones solo y las instala con el botón habitual.

Solo usa la biblioteca estándar de Python 3.8+. Funciona en Windows y Linux.

Uso:
    python joomla_offline.py                 # usa config.json junto al script
    python joomla_offline.py -c otra.json
"""

import argparse
import datetime as _dt
import hashlib
import html
import json
import os
import re
import shutil
import sys
import tempfile
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from xml.sax.saxutils import escape, quoteattr

USER_AGENT = "Joomla/6.0 joomla-offline-mirror/1.0"

URLS_TARGETS_CORE = [
    "https://update.joomla.org/cms/targets.json",
    # Copia pública del mismo repositorio TUF que mantiene el proyecto Joomla
    "https://raw.githubusercontent.com/joomla/updates/main/repository/targets.json",
]
URL_TRADUCCIONES = "https://update.joomla.org/language/translationlist_{rama}.xml"

# Sitio de actualización propio del componente Joomla Update (viene con el core)
URL_SITIO_COM_JOOMLAUPDATE = "https://update.joomla.org/core/extensions/com_joomlaupdate.xml"

# client_id por defecto de cada tipo de extensión en #__extensions
CLIENTE_POR_TIPO = {
    "component": "administrator",
    "package": "site",
    "plugin": "site",
    "library": "site",
    "file": "site",
    "language": "site",
}


# --------------------------------------------------------------------------- #
# Utilidades
# --------------------------------------------------------------------------- #

class ErrorEspejo(Exception):
    pass


def log(msg):
    print(msg, flush=True)


def http_get(url, accept=None, timeout=60):
    cabeceras = {"User-Agent": USER_AGENT}
    if accept:
        cabeceras["Accept"] = accept
    if "api.github.com" in url and os.environ.get("GITHUB_TOKEN"):
        cabeceras["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
    req = urllib.request.Request(url, headers=cabeceras)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        raise ErrorEspejo("HTTP %s al leer %s" % (e.code, url))
    except (urllib.error.URLError, OSError) as e:
        raise ErrorEspejo("No se pudo conectar con %s (%s)" % (url, getattr(e, "reason", e)))


def descargar(url, destino, hashes_esperados=None, timeout=300):
    """Descarga url a destino verificando los hashes indicados ({'sha512': '...'}).
    Devuelve un dict con los hashes calculados."""
    hashes_esperados = {k.lower(): v.strip().lower() for k, v in (hashes_esperados or {}).items() if v}
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    calculados = {a: hashlib.new(a) for a in ("sha256", "sha384", "sha512")}
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(destino), suffix=".part")
    try:
        with os.fdopen(fd, "wb") as f, urllib.request.urlopen(req, timeout=timeout) as r:
            while True:
                bloque = r.read(1024 * 256)
                if not bloque:
                    break
                f.write(bloque)
                for h in calculados.values():
                    h.update(bloque)
        resultado = {a: h.hexdigest() for a, h in calculados.items()}
        for alg, valor in hashes_esperados.items():
            if alg in resultado and resultado[alg] != valor:
                raise ErrorEspejo("El %s de %s no coincide con el publicado (descarga corrupta o manipulada)"
                                  % (alg.upper(), os.path.basename(destino)))
        os.replace(tmp, destino)
        return resultado
    except urllib.error.HTTPError as e:
        raise ErrorEspejo("HTTP %s al descargar %s" % (e.code, url))
    except (urllib.error.URLError, OSError) as e:
        if isinstance(e, ErrorEspejo):
            raise
        raise ErrorEspejo("Fallo al descargar %s (%s)" % (url, getattr(e, "reason", e)))
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def hashes_de_fichero(ruta):
    calculados = {a: hashlib.new(a) for a in ("sha256", "sha384", "sha512")}
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1024 * 256), b""):
            for h in calculados.values():
                h.update(bloque)
    return {a: h.hexdigest() for a, h in calculados.items()}


def descargar_de_alguna(urls, destino, hashes_esperados):
    """Prueba varias URLs hasta que una descarga con hash correcto.
    Si el fichero ya existe y su hash cuadra, no lo vuelve a bajar."""
    if os.path.exists(destino):
        actuales = hashes_de_fichero(destino)
        esperados = {k.lower(): v.lower() for k, v in (hashes_esperados or {}).items() if v}
        if esperados and all(actuales.get(k) == v for k, v in esperados.items() if k in actuales):
            return actuales, None
        if not esperados:
            return actuales, None
    errores = []
    for url in urls:
        try:
            return descargar(url, destino, hashes_esperados), url
        except ErrorEspejo as e:
            errores.append(str(e))
    raise ErrorEspejo("; ".join(errores) or "sin URLs de descarga")


_ORDEN_ESTABILIDAD = {"dev": 0, "alpha": 1, "a": 1, "beta": 2, "b": 2, "rc": 3, "stable": 4}


def clave_version(v):
    """Clave ordenable aproximando version_compare de PHP."""
    partes = re.findall(r"\d+|[A-Za-z]+", str(v))
    clave = []
    for p in partes:
        if p.isdigit():
            clave.append((2, int(p)))
        else:
            clave.append((1, _ORDEN_ESTABILIDAD.get(p.lower(), 0)))
    clave.append((1, 4))  # sin sufijo == estable
    return clave


def slug(texto):
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^A-Za-z0-9]+", "-", texto).strip("-").lower()
    return s or "extension"


def nombre_desde_url(url):
    nombre = os.path.basename(url.split("?")[0].split("#")[0])
    # Descargas servidas por un script (p. ej. JCE: index.php?...&file=pkg_jce_299912.zip):
    # si algún parámetro de la URL es un nombre de paquete, ese es el nombre real.
    if not re.search(r"\.(zip|tar\.gz|tgz|tar\.bz2|tbz2)$", nombre, re.I):
        consulta = urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query)
        for _, valor in consulta:
            cand = os.path.basename(valor.replace("\\", "/"))
            if re.search(r"\.(zip|tar\.gz|tgz|tar\.bz2|tbz2)$", cand, re.I):
                nombre = cand
                break
    nombre = re.sub(r"[^A-Za-z0-9._-]", "_", nombre) or "paquete.zip"
    # downloads.joomla.org sirve los idiomas como ".../es-es_..._6-2-0v1-zip?format=raw", sin
    # extensión. Joomla elige cómo descomprimir por la extensión, así que se fuerza ".zip".
    if not re.search(r"\.(zip|tar\.gz|tgz|tar\.bz2|tbz2)$", nombre, re.I):
        nombre = re.sub(r"[-_.]zip$", "", nombre, flags=re.I) + ".zip"
    return nombre


def texto(el, ruta, defecto=""):
    hijo = el.find(ruta)
    return (hijo.text or "").strip() if hijo is not None and hijo.text else defecto


def regex_compatible(regex_tp, version):
    """Replica la comprobación de Joomla: preg_match('/^' . targetplatform . '/', JVERSION)."""
    try:
        return re.match("^" + regex_tp, version) is not None
    except re.error:
        return True


def leer_manifiesto_zip(ruta_zip):
    """Lee el XML de instalación de la raíz del ZIP y deduce element/type/client/folder."""
    with zipfile.ZipFile(ruta_zip) as z:
        # Como el instalador de Joomla: primero la raíz del ZIP, luego un nivel de carpeta
        xmls = [n for n in z.namelist() if n.lower().endswith(".xml") and not n.endswith("/")]
        candidatos = ([n for n in xmls if n.count("/") == 0] + [n for n in xmls if n.count("/") == 1])
        for nombre in candidatos:
            try:
                raiz = ET.fromstring(z.read(nombre))
            except ET.ParseError:
                continue
            if raiz.tag != "extension":
                continue
            tipo = raiz.get("type", "").lower()
            nombre_ext = texto(raiz, "name")
            # InstallerAdapter::getElement(): <element> del manifiesto o, si no hay, el nombre,
            # filtrado como 'cmd' (solo A-Z 0-9 _ . -) y en minúsculas
            base = re.sub(r"[^A-Za-z0-9_.-]", "", texto(raiz, "element") or nombre_ext).lstrip(".").lower()
            element, folder = "", ""
            if tipo == "package":
                element = "pkg_" + (texto(raiz, "packagename") or slug(nombre_ext).replace("-", ""))
            elif tipo == "component":
                element = base
                if not element.startswith("com_"):
                    element = "com_" + element
            elif tipo in ("module", "plugin"):
                for f in raiz.iter("filename"):
                    if f.get(tipo):
                        element = f.get(tipo)
                        break
                if tipo == "plugin":
                    folder = raiz.get("group", "")
            elif tipo == "library":
                element = texto(raiz, "libraryname")
            elif tipo == "template":
                element = base
            elif tipo == "file":
                # Joomla usa el nombre del fichero de manifiesto como element de las extensiones "file"
                element = os.path.splitext(os.path.basename(nombre))[0]
            else:
                element = base
            cliente = raiz.get("client") or CLIENTE_POR_TIPO.get(tipo, "site")
            servidores = []
            us = raiz.find("updateservers")
            if us is not None:
                servidores = [(s.text or "").strip() for s in us.findall("server") if (s.text or "").strip()]
            return {
                "nombre": nombre_ext,
                "type": tipo,
                "element": element,
                "client": cliente,
                "folder": folder,
                "version": texto(raiz, "version"),
                "servidores": servidores,
            }
    raise ErrorEspejo("No se encontró el manifiesto XML de Joomla dentro de %s" % os.path.basename(ruta_zip))


# --------------------------------------------------------------------------- #
# Espejo
# --------------------------------------------------------------------------- #

# Script de instalación del paquete del espejo. Al actualizar el paquete, Joomla añade los
# servidores nuevos del manifiesto pero no borra los que ya no están; este postflight (que se
# ejecuta ANTES de que Joomla registre los nuevos) quita los sitios enlazados al paquete que el
# manifiesto nuevo ya no declara, para que no queden sitios huérfanos dando errores.
SCRIPT_PAQUETE_PHP = r"""<?php
defined('_JEXEC') or die;

use Joomla\CMS\Factory;
use Joomla\CMS\Installer\InstallerAdapter;
use Joomla\CMS\Installer\InstallerScriptInterface;

return new class () implements InstallerScriptInterface {
    public function install(InstallerAdapter $adapter): bool { return true; }
    public function update(InstallerAdapter $adapter): bool { return true; }
    public function uninstall(InstallerAdapter $adapter): bool { return true; }
    public function preflight(string $type, InstallerAdapter $adapter): bool { return true; }

    public function postflight(string $type, InstallerAdapter $adapter): bool
    {
        if ($type !== 'update') {
            return true;
        }
        try {
            $manifest = $adapter->getManifest();
            $validas  = [];
            if ($manifest && isset($manifest->updateservers->server)) {
                foreach ($manifest->updateservers->server as $server) {
                    $validas[] = trim((string) $server);
                }
            }
            $db = Factory::getContainer()->get('DatabaseDriver');
            $q  = static fn () => method_exists($db, 'createQuery') ? $db->createQuery() : $db->getQuery(true);
            $eid = (int) $db->setQuery($q()->select($db->quoteName('extension_id'))->from($db->quoteName('#__extensions'))
                ->where($db->quoteName('element') . ' = ' . $db->quote('%(element)s'))
                ->where($db->quoteName('type') . ' = ' . $db->quote('file')))->loadResult();
            if (!$eid) {
                return true;
            }
            $sitios = $db->setQuery($q()->select(['s.update_site_id', 's.location'])
                ->from($db->quoteName('#__update_sites', 's'))
                ->join('INNER', $db->quoteName('#__update_sites_extensions', 'x') . ' ON x.update_site_id = s.update_site_id')
                ->where('x.extension_id = ' . $eid))->loadObjectList();
            foreach ($sitios as $sitio) {
                if (\in_array(trim($sitio->location), $validas, true)) {
                    continue;
                }
                $id = (int) $sitio->update_site_id;
                $db->setQuery($q()->delete($db->quoteName('#__update_sites_extensions'))
                    ->where('update_site_id = ' . $id)->where('extension_id = ' . $eid))->execute();
                $quedan = (int) $db->setQuery($q()->select('COUNT(*)')->from($db->quoteName('#__update_sites_extensions'))
                    ->where('update_site_id = ' . $id))->loadResult();
                if ($quedan === 0) {
                    $db->setQuery($q()->delete($db->quoteName('#__updates'))->where('update_site_id = ' . $id))->execute();
                    $db->setQuery($q()->delete($db->quoteName('#__update_sites'))->where('update_site_id = ' . $id))->execute();
                }
            }
        } catch (\Throwable $e) {
            // Limpiar sitios es un extra: nunca debe impedir la actualización del paquete
        }
        return true;
    }
};
"""


class Espejo:
    def __init__(self, config, ruta_config):
        self.cfg = config
        base_dir = os.path.dirname(os.path.abspath(ruta_config))
        self.salida = os.path.abspath(os.path.join(base_dir, config.get("carpeta_salida", "joomla-offline")))
        self.url_base = config["url_base"].rstrip("/")
        self.ramas = [str(r) for r in config.get("ramas_joomla", ["6"])]
        self.idiomas = config.get("idiomas", [])
        self.conservar = bool(config.get("conservar_versiones_anteriores", False))
        self.origen_manual = os.path.abspath(os.path.join(base_dir, config.get("carpeta_manual", "manual")))
        self.manuales = []          # (ruta relativa en el espejo, bytes, fecha, estado)
        self.ruta_estado = os.path.join(self.salida, "estado.json")
        self.estado_anterior = self._cargar_estado()
        self.estado = {"items": {}}
        self.resultados = []        # filas para LEEME
        self.errores = []
        self.sitios_espejo = []     # (nombre, url_espejo, tipo_sitio, [urls_originales])
        self.version_core = {}      # rama -> última versión del core

    # ---- estado ----------------------------------------------------------- #

    def _cargar_estado(self):
        try:
            with open(self.ruta_estado, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {"items": {}}

    def _registrar(self, clave, nombre, version, archivos, avisos=None, ok=True, detalle=""):
        previo = self.estado_anterior.get("items", {}).get(clave, {})
        version_previa = previo.get("version")
        if not ok:
            cambio = "ERROR"
        elif not version_previa:
            cambio = "NUEVO"
        elif clave_version(version) > clave_version(version_previa):
            cambio = "ACTUALIZADO (antes %s)" % version_previa
        elif version == version_previa:
            cambio = "sin cambios"
        else:
            cambio = "cambiado (antes %s)" % version_previa
        if ok:
            self.estado["items"][clave] = {"nombre": nombre, "version": version, "archivos": archivos}
            # Borrar ficheros de versiones anteriores que ya no se usan
            if not self.conservar:
                for rel in previo.get("archivos", []):
                    if rel not in archivos:
                        ruta = os.path.join(self.salida, rel)
                        if os.path.isfile(ruta):
                            os.remove(ruta)
        elif previo:
            # Si falla, se conserva lo que había
            self.estado["items"][clave] = previo
        self.resultados.append({
            "nombre": nombre, "version": version or (previo.get("version", "?") if previo else "?"),
            "cambio": cambio, "archivos": archivos or previo.get("archivos", []),
            "avisos": avisos or [], "detalle": detalle,
        })

    def url(self, rel):
        return self.url_base + "/" + rel.replace(os.sep, "/")

    def escribir(self, rel, contenido):
        ruta = os.path.join(self.salida, rel)
        os.makedirs(os.path.dirname(ruta), exist_ok=True)
        with open(ruta, "w", encoding="utf-8", newline="\n") as f:
            f.write(contenido)

    # ---- core ------------------------------------------------------------- #

    def core(self):
        log("== Core de Joomla")
        datos, origen, errores = None, None, []
        for u in self.cfg.get("urls_targets_core", URLS_TARGETS_CORE):
            try:
                datos = json.loads(http_get(u))
                origen = u
                break
            except (ErrorEspejo, ValueError) as e:
                errores.append(str(e))
        if not datos:
            for rama in self.ramas:
                self._registrar("core-%s" % rama, "Joomla %s.x (core)" % rama, None, [], ok=False,
                                detalle="; ".join(errores))
                self.errores.append("Core: " + "; ".join(errores))
            return
        log("   lista de versiones: %s" % origen)
        targets = datos.get("signed", {}).get("targets", {})
        entradas_lista = []
        for rama in self.ramas:
            clave = "core-%s" % rama
            nombre = "Joomla %s.x (core)" % rama
            candidatos = []
            for _, t in targets.items():
                c = t.get("custom", {})
                if str(c.get("version", "")).startswith(rama + ".") and c.get("stability", "Stable").lower() == "stable" \
                        and c.get("downloads"):
                    candidatos.append(t)
            if not candidatos:
                self._registrar(clave, nombre, None, [], ok=False, detalle="no hay versiones estables de la rama %s" % rama)
                self.errores.append("Core %s.x: no hay versiones estables publicadas" % rama)
                continue
            t = max(candidatos, key=lambda x: clave_version(x["custom"]["version"]))
            c = t["custom"]
            version = c["version"]
            urls = [d["url"] for d in c["downloads"] if d.get("url")]
            fichero = nombre_desde_url(urls[0])
            rel = os.path.join("core", fichero)
            try:
                log("   %s -> %s" % (version, fichero))
                hashes, _ = descargar_de_alguna(urls, os.path.join(self.salida, rel), t.get("hashes", {}))
            except ErrorEspejo as e:
                self._registrar(clave, nombre, version, [], ok=False, detalle=str(e))
                self.errores.append("Core %s: %s" % (version, e))
                continue
            self.version_core[rama] = version
            rel_det = "core/joomla%s.xml" % rama
            self.escribir(rel_det, self._xml_detalle_core(c, self.url(rel), hashes))
            entradas_lista.append((rama, version, rel_det))
            self._registrar(clave, nombre, version, [rel.replace(os.sep, "/")])
        if entradas_lista:
            lineas = ['<?xml version="1.0" encoding="utf-8"?>',
                      '<extensionset name="Joomla Core (espejo local)" description="Actualizaciones del core servidas desde el espejo local">']
            for rama, version, rel_det in entradas_lista:
                lineas.append('  <extension name="Joomla" element="joomla" type="file" client="site" version=%s '
                              'targetplatformversion=%s detailsurl=%s/>'
                              % (quoteattr(version), quoteattr(rama + r"\."), quoteattr(self.url(rel_det))))
            lineas.append("</extensionset>")
            self.escribir("core/list.xml", "\n".join(lineas) + "\n")

    def _xml_detalle_core(self, c, url_zip, hashes):
        info = c.get("infourl") or {}
        tp = c.get("targetplatform") or {"name": "joomla", "version": ".*"}
        dbs = c.get("supported_databases") or {}
        partes = [
            '<?xml version="1.0" encoding="utf-8"?>',
            "<updates>",
            "  <update>",
            "    <name>%s</name>" % escape(c.get("name", "Joomla! " + c["version"])),
            "    <description>%s</description>" % escape(c.get("description", "")),
            "    <element>joomla</element>",
            "    <type>file</type>",
            "    <client>site</client>",
            "    <version>%s</version>" % escape(c["version"]),
        ]
        if info.get("url"):
            partes.append("    <infourl title=%s>%s</infourl>" % (quoteattr(info.get("title", "")), escape(info["url"])))
        partes += [
            "    <downloads>",
            '      <downloadurl type="full" format="zip">%s</downloadurl>' % escape(url_zip),
            "    </downloads>",
            "    <tags>",
            "      <tag>stable</tag>",
            "    </tags>",
            "    <maintainer>%s</maintainer>" % escape(c.get("maintainer", "Joomla! Production Department")),
            "    <maintainerurl>%s</maintainerurl>" % escape(c.get("maintainerurl", "https://www.joomla.org")),
            "    <targetplatform name=%s version=%s/>" % (quoteattr(tp.get("name", "joomla")), quoteattr(tp.get("version", ".*"))),
        ]
        if c.get("php_minimum"):
            partes.append("    <php_minimum>%s</php_minimum>" % escape(c["php_minimum"]))
        if dbs:
            attrs = " ".join("%s=%s" % (k, quoteattr(str(v))) for k, v in sorted(dbs.items()))
            partes.append("    <supported_databases %s/>" % attrs)
        for alg in ("sha256", "sha384", "sha512"):
            partes.append("    <%s>%s</%s>" % (alg, hashes[alg], alg))
        partes += ["  </update>", "</updates>"]
        return "\n".join(partes) + "\n"

    # ---- idiomas ---------------------------------------------------------- #

    def idiomas_joomla(self):
        if not self.idiomas:
            return
        plantilla = self.cfg.get("url_traducciones", URL_TRADUCCIONES)
        for rama in self.ramas:
            log("== Idiomas Joomla %s.x" % rama)
            url_lista = plantilla.format(rama=rama)
            try:
                raiz = ET.fromstring(http_get(url_lista))
            except (ErrorEspejo, ET.ParseError) as e:
                for idioma in self.idiomas:
                    self._registrar("idioma-%s-%s" % (rama, idioma), "Idioma %s (Joomla %s.x)" % (idioma, rama),
                                    None, [], ok=False, detalle=str(e))
                self.errores.append("Lista de idiomas %s.x: %s" % (rama, e))
                continue
            nuevas = []
            for idioma in self.idiomas:
                clave = "idioma-%s-%s" % (rama, idioma)
                nombre = "Idioma %s (Joomla %s.x)" % (idioma, rama)
                ext = None
                for e in raiz.findall("extension"):
                    if e.get("element", "").lower() == ("pkg_" + idioma).lower():
                        ext = e
                        break
                if ext is None:
                    self._registrar(clave, nombre, None, [], ok=False,
                                    detalle="el idioma no aparece en la lista oficial de Joomla %s" % rama)
                    self.errores.append("%s: no está en la lista oficial" % nombre)
                    continue
                try:
                    rel_det = "idiomas/%s/%s_details.xml" % (rama, idioma)
                    version, rel_zip = self._espejar_xml_updates(
                        ext.get("detailsurl"), "idiomas/%s" % rama, rel_det, nombre)
                except ErrorEspejo as e:
                    self._registrar(clave, nombre, ext.get("version"), [], ok=False, detalle=str(e))
                    self.errores.append("%s: %s" % (nombre, e))
                    continue
                nueva = ET.Element("extension", dict(ext.attrib))
                nueva.set("detailsurl", self.url(rel_det))
                nueva.set("version", version)
                nuevas.append(nueva)
                self._registrar(clave, nombre, version, [rel_zip])
            if nuevas:
                lista = ET.Element("extensionset", dict(raiz.attrib))
                for n in nuevas:
                    n.tail = "\n  "
                    lista.append(n)
                lista.text = "\n  "
                nuevas[-1].tail = "\n"
                rel_lista = "idiomas/translationlist_%s.xml" % rama
                self.escribir(rel_lista, '<?xml version="1.0" encoding="utf-8"?>\n'
                              + ET.tostring(lista, encoding="unicode") + "\n")
                self.sitios_espejo.append(("Idiomas Joomla %s.x" % rama, self.url(rel_lista), "collection", [url_lista]))

    # ---- XML de actualización genérico ----------------------------------- #

    def _changelog_github_desde_xml(self, update, rel_xml, nombre):
        """Usa el tag exacto de la descarga GitHub, sin consultar latest."""
        for d in update.findall("downloads/downloadurl"):
            url = urllib.parse.urlsplit((d.text or "").strip())
            m = re.match(r"^/([^/]+)/([^/]+)/releases/download/([^/]+)/", url.path)
            if url.hostname != "github.com" or not m:
                continue
            owner, repo, tag = m.groups()
            api = self.cfg.get("url_api_github", "https://api.github.com").rstrip("/")
            try:
                release = json.loads(http_get(api + "/repos/%s/%s/releases/tags/%s" % (owner, repo, tag),
                                              accept="application/vnd.github+json"))
                version = texto(update, "version")
                if urllib.parse.unquote(tag).lstrip("vV") != version:
                    return
                notas = (release.get("body") or "").strip()
                if not notas or release.get("draft") or release.get("prerelease"):
                    return
                raiz = ET.Element("changelogs")
                cambio = ET.SubElement(raiz, "changelog")
                for k in ("element", "type", "version"):
                    ET.SubElement(cambio, k).text = texto(update, k)
                ET.SubElement(ET.SubElement(cambio, "note"), "item").text = notas
                rel = os.path.splitext(rel_xml)[0] + "-changelog.xml"
                self.escribir(rel, '<?xml version="1.0" encoding="utf-8"?>\n'
                              + ET.tostring(raiz, encoding="unicode") + "\n")
                ET.SubElement(update, "changelogurl").text = self.url(rel)
            except (ErrorEspejo, ValueError, OSError) as e:
                aviso = "%s: notas de GitHub no disponibles (%s)" % (nombre, e)
                log("   AVISO: " + aviso)
                self.errores.append(aviso)
            return

    def _espejar_changelog(self, update, url_xml, rel_xml, nombre):
        """Copia notas de la versión; su ausencia o fallo no bloquea el ZIP."""
        nodos = list(update.findall("changelogurl"))
        if not nodos:
            self._changelog_github_desde_xml(update, rel_xml, nombre)
            return
        origen = urllib.parse.urljoin(url_xml, (nodos[0].text or "").strip())
        for nodo in nodos:
            update.remove(nodo)
        try:
            raiz = ET.fromstring(http_get(origen))
            if raiz.tag != "changelogs":
                raise ErrorEspejo("la raíz no es <changelogs>")
            identidad = tuple(texto(update, k) for k in ("element", "type", "version"))
            coincidentes = [c for c in raiz.findall("changelog")
                           if tuple(texto(c, k) for k in ("element", "type", "version")) == identidad]
            if not coincidentes:
                raise ErrorEspejo("no contiene notas para esta extensión y versión")
            salida = ET.Element("changelogs")
            for c in coincidentes:
                # Las notas del proveedor se publican como texto, sin HTML activo.
                for item in c.findall(".//item"):
                    contenido = "".join(item.itertext())
                    item.clear()
                    item.text = contenido
                salida.append(c)
            rel = os.path.splitext(rel_xml)[0] + "-changelog.xml"
            self.escribir(rel, '<?xml version="1.0" encoding="utf-8"?>\n'
                          + ET.tostring(salida, encoding="unicode") + "\n")
            ET.SubElement(update, "changelogurl").text = self.url(rel)
        except (ErrorEspejo, ET.ParseError, OSError) as e:
            aviso = "%s: changelog no disponible (%s)" % (nombre, e)
            log("   AVISO: " + aviso)
            self.errores.append(aviso)

    def _espejar_xml_updates(self, url_xml, carpeta_rel, rel_xml, nombre):
        """Descarga un XML <updates>, se queda con la versión más alta, baja su ZIP,
        reescribe la URL de descarga y guarda el XML en rel_xml.
        Devuelve (version, ruta_relativa_zip)."""
        try:
            raiz = ET.fromstring(http_get(url_xml))
        except ET.ParseError as e:
            raise ErrorEspejo("XML no válido en %s (%s)" % (url_xml, e))
        updates = [u for u in raiz.findall("update") if texto(u, "version")]
        if not updates:
            raise ErrorEspejo("%s no contiene ninguna versión" % url_xml)
        # Solo versiones estables (si declaran etiqueta)
        estables = [u for u in updates
                    if not u.findall("tags/tag")
                    or any((t.text or "").strip().lower() == "stable" for t in u.findall("tags/tag"))]
        candidatas = estables or updates
        # Un mismo XML puede traer versiones para varias ramas de Joomla (p. ej. Gantry 5:
        # una para 3.x, otra para 4.x, otra para 5.x/6.x). Solo interesan las de nuestras ramas.
        compatibles = [x for x in candidatas if self._compatible_con_ramas(
            x.find("targetplatform").get("version") if x.find("targetplatform") is not None else None)]
        if not compatibles:
            raise ErrorEspejo("%s no tiene ninguna versión para Joomla %s"
                              % (url_xml, "/".join(r + ".x" for r in self.ramas)))
        u = max(compatibles, key=lambda x: clave_version(texto(x, "version")))
        version = texto(u, "version")
        urls = [(d.text or "").strip() for d in u.findall("downloads/downloadurl") if (d.text or "").strip()]
        if not urls:
            raise ErrorEspejo("la versión %s no tiene URL de descarga" % version)
        esperados = {alg: texto(u, alg) for alg in ("sha256", "sha384", "sha512") if texto(u, alg)}
        fichero = nombre_desde_url(urls[0])
        rel_zip = "%s/%s" % (carpeta_rel, fichero)
        log("   %s %s -> %s" % (nombre, version, fichero))
        hashes, _ = descargar_de_alguna(urls, os.path.join(self.salida, rel_zip), esperados)
        self._espejar_changelog(u, url_xml, rel_xml, nombre)
        # Reescribir descargas: una sola, local
        descargas = u.find("downloads")
        for d in list(descargas):
            descargas.remove(d)
        nueva = ET.SubElement(descargas, "downloadurl", {"type": "full", "format": "zip"})
        nueva.text = self.url(rel_zip)
        # Asegurar hashes (si el original no los traía, se añaden los calculados)
        for alg in ("sha256", "sha384", "sha512"):
            el = u.find(alg)
            if el is None:
                el = ET.SubElement(u, alg)
            el.text = hashes[alg]
        salida = ET.Element("updates")
        salida.append(u)
        ET.indent(salida) if hasattr(ET, "indent") else None
        self.escribir(rel_xml, '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(salida, encoding="unicode") + "\n")
        tp = u.find("targetplatform")
        self._ultimo_tp = tp.get("version") if tp is not None else None
        return version, rel_zip

    # ---- extensiones ------------------------------------------------------ #

    def extensiones(self):
        for ext in self.cfg.get("extensiones", []):
            nombre = ext.get("nombre") or ext.get("repo") or ext.get("url")
            clave = "ext-" + slug(nombre)
            carpeta = "extensiones/" + slug(nombre)
            rel_xml = carpeta + "/update.xml"
            log("== %s" % nombre)
            self._ultimo_tp = None
            if ext.get("tipo") == "coleccion":
                try:
                    self._extension_coleccion(ext, nombre)
                except ErrorEspejo as e:
                    self._registrar(clave, nombre, None, [], ok=False, detalle=str(e))
                    self.errores.append("%s: %s" % (nombre, e))
                continue
            try:
                if ext.get("tipo") == "github":
                    version, rel_zip, originales, info = self._extension_github(ext, carpeta, rel_xml, nombre)
                else:
                    version, rel_zip = self._espejar_xml_updates(ext["url"], carpeta, rel_xml, nombre)
                    info = leer_manifiesto_zip(os.path.join(self.salida, rel_zip))
                    originales = [ext["url"]] + [s for s in info["servidores"] if s != ext["url"]]
            except (ErrorEspejo, zipfile.BadZipFile, KeyError) as e:
                self._registrar(clave, nombre, None, [], ok=False, detalle=str(e))
                self.errores.append("%s: %s" % (nombre, e))
                continue
            avisos = self._avisos_compatibilidad(self._ultimo_tp)
            self.sitios_espejo.append((nombre, self.url(rel_xml), "extension", originales))
            self._registrar(clave, nombre, version, [rel_zip], avisos=avisos)

    def _extension_github(self, ext, carpeta, rel_xml, nombre):
        repo = ext["repo"]
        base_api = self.cfg.get("url_api_github", "https://api.github.com").rstrip("/")
        patron = re.compile(ext.get("patron_zip", r".*\.zip$"), re.I)
        if ext.get("patron_tag"):
            # Repos con releases de varias "familias" (p. ej. una por idioma): se elige la
            # release estable más reciente cuyo tag cumpla el patrón y tenga un ZIP válido.
            patron_tag = re.compile(ext["patron_tag"], re.I)
            releases = json.loads(http_get(base_api + "/repos/%s/releases?per_page=100" % repo,
                                           accept="application/vnd.github+json"))
            candidatas = [r for r in releases
                          if not r.get("draft") and not r.get("prerelease")
                          and patron_tag.search(r.get("tag_name", ""))
                          and any(patron.search(a.get("name", "")) for a in r.get("assets", []))]
            if not candidatas:
                raise ErrorEspejo("ninguna release de %s tiene un tag que cumpla '%s' con un ZIP que cumpla '%s'"
                                  % (repo, patron_tag.pattern, patron.pattern))
            rel = max(candidatas, key=lambda r: r.get("published_at") or r.get("created_at") or "")
        else:
            rel = json.loads(http_get(base_api + "/repos/%s/releases/latest" % repo,
                                      accept="application/vnd.github+json"))
        assets = [a for a in rel.get("assets", []) if patron.search(a.get("name", ""))]
        if not assets:
            raise ErrorEspejo("la release %s de %s no tiene ningún ZIP que cumpla '%s'"
                              % (rel.get("tag_name"), repo, patron.pattern))
        a = assets[0]
        esperados = {}
        if str(a.get("digest", "")).startswith("sha256:"):
            esperados["sha256"] = a["digest"].split(":", 1)[1]
        fichero = nombre_desde_url(a["name"])
        rel_zip = "%s/%s" % (carpeta, fichero)
        log("   %s %s -> %s" % (nombre, rel.get("tag_name"), fichero))
        hashes, _ = descargar_de_alguna([a["browser_download_url"]], os.path.join(self.salida, rel_zip), esperados)
        info = leer_manifiesto_zip(os.path.join(self.salida, rel_zip))
        for k in ("element", "type", "client", "folder"):
            if ext.get(k):
                info[k] = ext[k]
        version = info["version"] or str(rel.get("tag_name", "")).lstrip("vV")
        tp = ext.get("targetplatform") or "(%s)\\." % "|".join(re.escape(r) for r in self.ramas)
        self._ultimo_tp = tp
        partes = [
            '<?xml version="1.0" encoding="utf-8"?>',
            "<updates>",
            "  <update>",
            "    <name>%s</name>" % escape(info["nombre"] or nombre),
            "    <description>%s</description>" % escape(nombre),
            "    <element>%s</element>" % escape(info["element"]),
            "    <type>%s</type>" % escape(info["type"]),
            "    <client>%s</client>" % escape(info["client"]),
        ]
        if info["folder"]:
            partes.append("    <folder>%s</folder>" % escape(info["folder"]))
        partes += [
            "    <version>%s</version>" % escape(version),
            "    <infourl title=%s>%s</infourl>" % (quoteattr(nombre), escape(rel.get("html_url", ""))),
            "    <downloads>",
            '      <downloadurl type="full" format="zip">%s</downloadurl>' % escape(self.url(rel_zip)),
            "    </downloads>",
            "    <tags>",
            "      <tag>stable</tag>",
            "    </tags>",
            '    <targetplatform name="joomla" version=%s/>' % quoteattr(tp),
        ]
        for alg in ("sha256", "sha384", "sha512"):
            partes.append("    <%s>%s</%s>" % (alg, hashes[alg], alg))
        partes += ["  </update>", "</updates>"]
        notas = (rel.get("body") or "").strip()
        if notas:
            cambios = ET.Element("changelogs")
            cambio = ET.SubElement(cambios, "changelog")
            for k, valor in (("element", info["element"]), ("type", info["type"]), ("version", version)):
                ET.SubElement(cambio, k).text = valor
            # GitHub entrega Markdown; se conserva como texto sin ejecutar HTML.
            ET.SubElement(ET.SubElement(cambio, "note"), "item").text = notas
            rel_cambios = os.path.splitext(rel_xml)[0] + "-changelog.xml"
            self.escribir(rel_cambios, '<?xml version="1.0" encoding="utf-8"?>\n'
                          + ET.tostring(cambios, encoding="unicode") + "\n")
            partes.insert(-2, "    <changelogurl>%s</changelogurl>" % escape(self.url(rel_cambios)))
        self.escribir(rel_xml, "\n".join(partes) + "\n")
        return version, rel_zip, info["servidores"], info

    def _compatible_con_ramas(self, regex_tp):
        """¿Acepta regex_tp alguna versión de las ramas configuradas? (6.0.0 … 6.9.0, etc.)"""
        if not regex_tp:
            return True
        return any(regex_compatible(regex_tp, "%s.%d.0" % (rama, menor))
                   for rama in self.ramas for menor in range(10))

    def _extension_coleccion(self, ext, nombre):
        """Servidor de tipo 'collection' (list.xml) con varias extensiones: p. ej. Gantry 5
        (framework + plantillas). Se replica la lista filtrada por rama de Joomla, cada XML de
        detalle y su ZIP. Devuelve el número de extensiones replicadas."""
        url_lista = ext["url"]
        try:
            raiz = ET.fromstring(http_get(url_lista))
        except ET.ParseError as e:
            raise ErrorEspejo("XML no válido en %s (%s)" % (url_lista, e))
        filtro = {e.lower() for e in ext.get("elementos", [])}
        carpeta = "extensiones/" + slug(nombre)
        entradas = []
        for e in raiz.findall("extension"):
            if filtro and e.get("element", "").lower() not in filtro:
                continue
            # Joomla compara targetplatformversion con su versión igual que targetplatform
            if not self._compatible_con_ramas(e.get("targetplatformversion")):
                continue
            entradas.append(e)
        if not entradas:
            raise ErrorEspejo("%s no tiene ninguna extensión para Joomla %s"
                              % (url_lista, "/".join(r + ".x" for r in self.ramas)))
        replicados = {}   # detailsurl original -> (url local, version, rel_zip, tp) o excepción
        nuevas = []
        for e in entradas:
            det = (e.get("detailsurl") or "").strip()
            sub = "%s › %s" % (nombre, e.get("name") or e.get("element"))
            clave = "ext-%s-%s" % (slug(nombre), slug(e.get("element", "")))
            if det not in replicados:
                base = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(det.split("?")[0])) or "detalle.xml"
                if not base.lower().endswith(".xml"):
                    base += ".xml"
                rel_det = "%s/%s" % (carpeta, base)
                try:
                    self._ultimo_tp = None
                    version, rel_zip = self._espejar_xml_updates(det, carpeta, rel_det, sub)
                    replicados[det] = (self.url(rel_det), version, rel_zip, self._ultimo_tp)
                except (ErrorEspejo, KeyError) as err:
                    replicados[det] = err
            r = replicados[det]
            if isinstance(r, Exception):
                self._registrar(clave, sub, None, [], ok=False, detalle=str(r))
                self.errores.append("%s: %s" % (sub, r))
                continue
            url_local, version, rel_zip, tp = r
            nueva = ET.Element("extension", dict(e.attrib))
            nueva.set("detailsurl", url_local)
            nueva.set("version", version)
            nuevas.append(nueva)
            if not any(x["nombre"] == sub for x in self.resultados):
                self._registrar(clave, sub, version, [rel_zip], avisos=self._avisos_compatibilidad(tp))
        if not nuevas:
            return 0
        lista = ET.Element("extensionset", dict(raiz.attrib))
        lista.text = "\n  "
        for n in nuevas:
            n.tail = "\n  "
            lista.append(n)
        nuevas[-1].tail = "\n"
        rel_lista = carpeta + "/list.xml"
        self.escribir(rel_lista, '<?xml version="1.0" encoding="utf-8"?>\n'
                      + ET.tostring(lista, encoding="unicode") + "\n")
        originales = [url_lista] + [u for u in ext.get("otras_urls", []) if u != url_lista]
        self.sitios_espejo.append((nombre, self.url(rel_lista), "collection", originales))
        return len(nuevas)

    def _avisos_compatibilidad(self, regex_tp):
        avisos = []
        if not regex_tp:
            return avisos
        for rama, v in self.version_core.items():
            if not regex_compatible(regex_tp, v):
                avisos.append("no declara compatibilidad con Joomla %s (targetplatform: %s); "
                              "un sitio en %s no verá esta actualización" % (v, regex_tp, v))
        return avisos

    # ---- paquete instalable, LEEME e índice ----------------------------- #

    NOMBRE_PAQUETE = "Offline Updates"
    ELEMENTO_PAQUETE = "files_joomla_offline_updates"
    ZIP_PAQUETE = "joomla-offline-updates.zip"
    XML_PAQUETE = "joomla-offline-updates.xml"
    # Nombres de versiones anteriores del script (para borrar sus restos de la carpeta)
    ZIPS_ANTIGUOS = ("espejo-actualizaciones-local.zip",)

    def generar_paquete(self):
        """Genera un paquete de Joomla (tipo file) cuyo único fin es declarar las URLs del
        espejo como servidores de actualización. Al instalarlo, Joomla crea él mismo los
        sitios de actualización; al desinstalarlo, los borra. No hay que tocar la base de datos.

        Funciona porque al buscar actualizaciones Joomla consulta todos los sitios activos y
        asigna cada actualización a la extensión instalada por element/type/client/folder,
        no por el sitio de actualización al que esté enlazada."""
        servidores, vistos = [], set()
        for nombre, url_e, tipo_sitio, _ in self.sitios_espejo:
            if url_e not in vistos:
                vistos.add(url_e)
                servidores.append((nombre, url_e, tipo_sitio))
        ruta_zip = os.path.join(self.salida, self.ZIP_PAQUETE)
        if not servidores:
            if os.path.exists(ruta_zip):
                os.remove(ruta_zip)
            return
        # El propio paquete se actualiza desde el espejo: cuando cambia (se añaden o quitan
        # fuentes), Joomla lo ofrece en Sistema > Actualizar como cualquier otra extensión.
        url_xml_propio = self.url(self.XML_PAQUETE)
        servidores.append((self.NOMBRE_PAQUETE, url_xml_propio, "extension"))
        # La versión solo cambia cuando cambia la lista de servidores: así no hay que
        # reinstalar el paquete en cada ejecución, solo cuando se añade o quita algo.
        firma = hashlib.sha256(json.dumps([self.ELEMENTO_PAQUETE, self.NOMBRE_PAQUETE, servidores],
                                          sort_keys=True).encode()).hexdigest()
        previo = self.estado_anterior.get("paquete", {})
        if previo.get("firma") == firma and previo.get("version") and os.path.exists(ruta_zip):
            self.estado["paquete"] = previo
            self.paquete_cambiado = False
            self._xml_paquete(previo["version"], ruta_zip)
            self._registrar_paquete(previo["version"])
            return
        version = _dt.datetime.now().strftime("%Y.%m.%d.%H%M%S")
        self.estado["paquete"] = {"firma": firma, "version": version}
        self.paquete_cambiado = True
        el = self.ELEMENTO_PAQUETE
        lineas = [
            '<?xml version="1.0" encoding="utf-8"?>',
            '<extension type="file" method="upgrade">',
            "  <name>%s</name>" % escape(self.NOMBRE_PAQUETE),
            "  <author>joomla-offline</author>",
            "  <creationDate>%s</creationDate>" % _dt.date.today().isoformat(),
            "  <version>%s</version>" % version,
            "  <license>GNU General Public License version 2 or later</license>",
            "  <description>%s</description>" % escape(
                "Declara el espejo local %s como servidor de actualizaciones de Joomla. "
                "Desinstálalo para dejar de usar el espejo." % self.url_base),
            "  <fileset>",
            '    <files folder="files" target="media/%s">' % el,
            "      <filename>LEEME.txt</filename>",
            "    </files>",
            "  </fileset>",
            "  <scriptfile>script.php</scriptfile>",
            "  <updateservers>",
        ]
        for nombre, url_e, tipo_sitio in servidores:
            lineas.append('    <server type="%s" priority="1" name=%s>%s</server>'
                          % (tipo_sitio, quoteattr(nombre + " (espejo local)"), escape(url_e)))
        lineas += ["  </updateservers>", "</extension>"]
        leeme = ("Paquete generado por joomla-offline el %s.\r\n"
                 "Solo sirve para registrar en Joomla los servidores de actualizacion del espejo local:\r\n%s\r\n"
                 % (_dt.date.today().isoformat(), "\r\n".join("  - " + u for _, u, _ in servidores)))
        tmp = ruta_zip + ".part"
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(el + ".xml", "\n".join(lineas) + "\n")
            z.writestr("files/LEEME.txt", leeme)
            z.writestr("script.php", SCRIPT_PAQUETE_PHP % {"element": el})
        os.replace(tmp, ruta_zip)
        self._xml_paquete(version, ruta_zip)
        self._registrar_paquete(version)
        for viejo in self.ZIPS_ANTIGUOS:
            ruta = os.path.join(self.salida, viejo)
            if os.path.exists(ruta):
                os.remove(ruta)
        # Restos de versiones anteriores del script (modo SQL)
        for viejo in ("sql/activar_espejo.sql", "sql/quitar_espejo.sql",
                      "sql/reapuntar_al_espejo.sql", "sql/revertir_a_internet.sql"):
            ruta = os.path.join(self.salida, viejo)
            if os.path.exists(ruta):
                os.remove(ruta)
        carpeta_sql = os.path.join(self.salida, "sql")
        if os.path.isdir(carpeta_sql) and not os.listdir(carpeta_sql):
            os.rmdir(carpeta_sql)

    def _registrar_paquete(self, version):
        """Línea del propio paquete en el resumen, LEEME e index (en la clave de estado
        "paquete-espejo"; la versión real se guarda en estado["paquete"])."""
        self._registrar("paquete-espejo", self.NOMBRE_PAQUETE + " (espejo)", version,
                        [self.ZIP_PAQUETE, self.XML_PAQUETE])

    def _xml_paquete(self, version, ruta_zip):
        """XML de actualización del propio paquete del espejo."""
        hashes = hashes_de_fichero(ruta_zip)
        tp = "|".join(re.escape(r) + r"\." for r in self.ramas)
        tp = "(%s)" % tp if len(self.ramas) > 1 else tp
        partes = [
            '<?xml version="1.0" encoding="utf-8"?>',
            "<updates>",
            "  <update>",
            "    <name>%s</name>" % escape(self.NOMBRE_PAQUETE),
            "    <description>%s</description>" % escape("Fuentes del espejo local " + self.url_base),
            "    <element>%s</element>" % self.ELEMENTO_PAQUETE,
            "    <type>file</type>",
            "    <client>site</client>",
            "    <version>%s</version>" % escape(version),
            "    <downloads>",
            '      <downloadurl type="full" format="zip">%s</downloadurl>' % escape(self.url(self.ZIP_PAQUETE)),
            "    </downloads>",
            "    <tags>",
            "      <tag>stable</tag>",
            "    </tags>",
            '    <targetplatform name="joomla" version=%s/>' % quoteattr(tp),
        ]
        for alg in ("sha256", "sha384", "sha512"):
            partes.append("    <%s>%s</%s>" % (alg, hashes[alg], alg))
        partes += ["  </update>", "</updates>"]
        self.escribir(self.XML_PAQUETE, "\n".join(partes) + "\n")

    # ---- paquetes descargados a mano ------------------------------------- #

    def manual(self):
        """Copia tal cual al espejo (subcarpeta manual/) lo que haya en la carpeta 'manual' junto
        al config.json: paquetes sin servidor de actualizaciones (p. ej. plantillas de JoomLead
        descargadas de su área de cliente). Se mantiene sincronizada: lo que borres del origen
        desaparece del espejo."""
        destino = os.path.join(self.salida, "manual")
        if not os.path.isdir(self.origen_manual):
            os.makedirs(self.origen_manual, exist_ok=True)
            log("== Carpeta manual creada: %s (deja ahí los ZIP descargados a mano)" % self.origen_manual)
        if os.path.normcase(os.path.abspath(self.origen_manual)).startswith(
                os.path.normcase(os.path.abspath(self.salida)) + os.sep):
            self.errores.append("carpeta_manual no puede estar dentro de carpeta_salida")
            return
        # Se crea siempre, aunque esté vacía, para que se vea dónde irán los paquetes manuales
        os.makedirs(destino, exist_ok=True)
        previos = set(self.estado_anterior.get("manual", []))
        vistos = []
        for raiz, dirs, ficheros in os.walk(self.origen_manual):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for f in sorted(ficheros):
                if f.startswith(".") or f.lower() in ("thumbs.db", "desktop.ini"):
                    continue
                src = os.path.join(raiz, f)
                rel = os.path.relpath(src, self.origen_manual).replace(os.sep, "/")
                dst = os.path.join(destino, *rel.split("/"))
                st = os.stat(src)
                if not (os.path.exists(dst) and os.path.getsize(dst) == st.st_size
                        and int(os.path.getmtime(dst)) == int(st.st_mtime)):
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(src, dst)
                estado = "sin cambios" if rel in previos else "NUEVO"
                fecha = _dt.datetime.fromtimestamp(st.st_mtime).strftime("%d/%m/%Y")
                self.manuales.append(("manual/" + rel, st.st_size, fecha, estado))
                vistos.append(rel)
        # Quitar del espejo lo que ya no está en el origen
        if os.path.isdir(destino):
            for raiz, dirs, ficheros in os.walk(destino, topdown=False):
                for f in ficheros:
                    rel = os.path.relpath(os.path.join(raiz, f), destino).replace(os.sep, "/")
                    if rel not in vistos:
                        os.remove(os.path.join(raiz, f))
                if raiz != destino and not os.listdir(raiz):
                    os.rmdir(raiz)
        self.estado["manual"] = vistos
        if self.manuales:
            log("== Paquetes manuales: %d" % len(self.manuales))
        if self.cfg.get("manual_en_actualizaciones", True):
            self._actualizaciones_manuales()

    def _actualizaciones_manuales(self):
        """Hace que los ZIP de manual/ aparezcan en Sistema > Actualizar como cualquier otra
        extensión: se lee el manifiesto de cada ZIP (element, tipo, versión) y se publica una
        lista 'collection' (manual_xml/list.xml) con un XML de detalle por extensión, quedándose
        con la versión más alta si hay varias de la misma extensión.
        El sitio de actualización es siempre la misma lista, así que añadir o quitar ZIP en
        manual/ no obliga a reinstalar el paquete del espejo."""
        carpeta = "manual_xml"
        ruta_carpeta = os.path.join(self.salida, carpeta)
        mejores = {}       # (type, element, client, folder) -> (version, rel, info)
        self.manual_info = {}
        for rel, _, _, _ in self.manuales:
            if not rel.lower().endswith(".zip"):
                continue
            try:
                info = leer_manifiesto_zip(os.path.join(self.salida, *rel.split("/")))
            except (ErrorEspejo, zipfile.BadZipFile, OSError) as e:
                self.manual_info[rel] = "solo instalación manual (%s)" % e
                continue
            if not info["element"] or not info["version"] or not info["type"]:
                self.manual_info[rel] = "solo instalación manual (manifiesto sin nombre, tipo o versión)"
                continue
            clave = (info["type"], info["element"], info["client"], info["folder"])
            self.manual_info[rel] = "%s %s %s" % (info["type"], info["element"], info["version"])
            if clave not in mejores or clave_version(info["version"]) > clave_version(mejores[clave][0]):
                mejores[clave] = (info["version"], rel, info)
        # Rehacer la carpeta de XML desde cero en cada ejecución
        if os.path.isdir(ruta_carpeta):
            shutil.rmtree(ruta_carpeta)
        tp = "|".join(re.escape(r) + r"\." for r in self.ramas)
        tp = "(%s)" % tp if len(self.ramas) > 1 else tp
        entradas = []
        for (tipo, element, cliente, folder), (version, rel, info) in sorted(mejores.items()):
            hashes = hashes_de_fichero(os.path.join(self.salida, *rel.split("/")))
            nombre_det = slug("%s-%s-%s" % (tipo, folder, element)) + ".xml"
            partes = [
                '<?xml version="1.0" encoding="utf-8"?>',
                "<updates>",
                "  <update>",
                "    <name>%s</name>" % escape(info["nombre"] or element),
                "    <description>%s</description>" % escape("Paquete manual: " + rel),
                "    <element>%s</element>" % escape(element),
                "    <type>%s</type>" % escape(tipo),
                "    <client>%s</client>" % escape(cliente),
            ]
            if folder:
                partes.append("    <folder>%s</folder>" % escape(folder))
            partes += [
                "    <version>%s</version>" % escape(version),
                "    <downloads>",
                '      <downloadurl type="full" format="zip">%s</downloadurl>' % escape(self.url(rel)),
                "    </downloads>",
                "    <tags>",
                "      <tag>stable</tag>",
                "    </tags>",
                '    <targetplatform name="joomla" version=%s/>' % quoteattr(tp),
            ]
            for alg in ("sha256", "sha384", "sha512"):
                partes.append("    <%s>%s</%s>" % (alg, hashes[alg], alg))
            partes += ["  </update>", "</updates>"]
            self.escribir("%s/%s" % (carpeta, nombre_det), "\n".join(partes) + "\n")
            attrs = {"name": info["nombre"] or element, "element": element, "type": tipo,
                     "client": cliente, "version": version, "targetplatformversion": tp,
                     "detailsurl": self.url("%s/%s" % (carpeta, nombre_det))}
            if folder:
                attrs["folder"] = folder
            entradas.append(" ".join("%s=%s" % (k, quoteattr(v)) for k, v in attrs.items()))
            self.manual_info[rel] = "en Actualizar: " + self.manual_info[rel]
        # ZIP que no son la versión más alta de su extensión
        elegidos = {v[1] for v in mejores.values()}
        for rel in list(self.manual_info):
            if rel not in elegidos and not self.manual_info[rel].startswith("solo"):
                self.manual_info[rel] = "versión antigua, no se ofrece (" + self.manual_info[rel] + ")"
        lineas = ['<?xml version="1.0" encoding="utf-8"?>',
                  '<extensionset name="Paquetes manuales (espejo local)" description="ZIP de la carpeta manual del espejo">']
        lineas += ["  <extension %s/>" % e for e in entradas]
        lineas.append("</extensionset>")
        self.escribir(carpeta + "/list.xml", "\n".join(lineas) + "\n")
        self.sitios_espejo.append(("Paquetes manuales", self.url(carpeta + "/list.xml"), "collection", []))

    def generar_leeme(self):
        ahora = _dt.datetime.now().strftime("%d/%m/%Y %H:%M")
        l = ["ESPEJO DE ACTUALIZACIONES DE JOOMLA",
             "Generado: %s" % ahora,
             "URL base del espejo: %s" % self.url_base,
             "", "CONTENIDO", "========="]
        for r in self.resultados:
            l.append("")
            l.append("* %s — %s   [%s]" % (r["nombre"], r["version"] or "?", r["cambio"]))
            for a in r["archivos"]:
                l.append("    %s" % a)
            for a in r["avisos"]:
                l.append("    AVISO: %s" % a)
            if r["detalle"]:
                l.append("    ERROR: %s" % r["detalle"])
        if self.manuales:
            l += ["", "PAQUETES DESCARGADOS A MANO (carpeta manual/)",
                  "==============================================",
                  ("Joomla los ofrece en Sistema > Actualizar como cualquier otra extensión (si ya están instalados);"
                   " también se pueden subir a mano con Sistema > Instalar > Extensiones > 'Subir paquete'."
                   if self.cfg.get("manual_en_actualizaciones", True) else
                   "Se instalan con Sistema > Instalar > Extensiones > 'Subir paquete'.")]
            for rel, tam, fecha, estado in self.manuales:
                l.append("* %s   (%s, %.1f MB)   [%s]" % (rel, fecha, tam / 1048576.0, estado))
                if getattr(self, "manual_info", {}).get(rel):
                    l.append("    " + self.manual_info[rel])
        l += ["", "CÓMO USARLO", "===========",
              "A) Subir a mano (no requiere configurar nada):",
              "   - Core: Sistema > Joomla Update > pestaña 'Subir y actualizar' > ZIP de core/",
              "   - Idiomas y extensiones: Sistema > Instalar > Extensiones > 'Subir paquete'",
              "",
              "B) Que cada Joomla detecte las actualizaciones solo (sin tocar la base de datos):",
              "   1. Copia esta carpeta a un servidor web de la red aislada para que responda en",
              "      %s" % self.url_base,
              "   2. Core: Sistema > Joomla Update > Opciones > Canal de actualización = 'URL personalizada'",
              "      URL personalizada = %s" % self.url("core/list.xml"),
              "   3. Idiomas y extensiones: Sistema > Instalar > Extensiones > 'Subir paquete'",
              "      con %s (versión %s%s)." % (self.ZIP_PAQUETE, self.estado.get("paquete", {}).get("version", "?"),
                                              ", NUEVA: Joomla la ofrecerá en Actualizar" if getattr(self, "paquete_cambiado", False)
                                              and self.estado_anterior.get("paquete") else ""),
              "      Joomla crea solo los sitios de actualización del espejo.",
              "   4. Recomendado: en Sistema > Sitios de actualización, DESACTIVA (no borres) estos sitios",
              "      de internet para evitar avisos de 'no se pudo conectar':",
              ] + ["        - %s" % u for u in sorted({o.strip() for _, _, _, origs in self.sitios_espejo for o in origs}
                                                 | {URL_SITIO_COM_JOOMLAUPDATE})] + [
              "   5. Sistema > Actualizar > Extensiones > 'Buscar actualizaciones'",
              "",
              "   El paso 3 se hace una sola vez. El paquete se actualiza solo: cuando añades o quitas",
              "   fuentes en config.json, Joomla ofrece 'Joomla Offline Updates' en Sistema > Actualizar",
              "   (y al actualizarlo borra los sitios del espejo que ya no se usan). Solo hay que",
              "   reinstalarlo a mano si cambias url_base.",
              "   Para volver a internet: desinstala '%s', reactiva los" % self.NOMBRE_PAQUETE,
              "   sitios del paso 4 y pon el canal de Joomla Update en 'Por defecto'.",
              ""]
        if self.errores:
            l += ["ERRORES DE ESTA EJECUCIÓN", "========================="] + ["- " + e for e in self.errores] + [""]
        self.escribir("LEEME.txt", "\r\n".join(l) + "\r\n")

    def generar_indice(self):
        filas = []
        for r in self.resultados:
            enlaces = " ".join('<a href="%s">%s</a>' % (html.escape(a), html.escape(os.path.basename(a)))
                               for a in r["archivos"])
            avisos = "".join('<div class="aviso">%s</div>' % html.escape(a) for a in r["avisos"])
            if r["detalle"]:
                avisos += '<div class="error">%s</div>' % html.escape(r["detalle"])
            filas.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s%s</td></tr>" % (
                html.escape(r["nombre"]), html.escape(str(r["version"] or "?")), html.escape(r["cambio"]), enlaces, avisos))
        bloque_manual = ""
        if self.manuales:
            filas_m = "".join('<tr><td><a href="%s">%s</a></td><td>%s</td><td>%.1f MB</td><td>%s</td></tr>' % (
                html.escape(rel), html.escape(rel[len("manual/"):]), fecha, tam / 1048576.0, html.escape(estado))
                for rel, tam, fecha, estado in self.manuales)
            bloque_manual = ("<h2>Descargados a mano</h2><p>Joomla los ofrece en <em>Sistema → Actualizar</em> si ya "
                             "están instalados; también se pueden subir con "
                             "<em>Sistema → Instalar → Extensiones → Subir paquete</em>.</p>"
                             "<table><tr><th>Fichero</th><th>Fecha</th><th>Tamaño</th><th>Cambio</th></tr>%s</table>"
                             % filas_m)
        pagina = """<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Espejo Joomla</title>
<style>body{font-family:system-ui,sans-serif;margin:2rem;max-width:960px}table{border-collapse:collapse;width:100%%}
td,th{border-bottom:1px solid #ddd;padding:.5rem;text-align:left;vertical-align:top}.aviso{color:#9a6700;font-size:.9em}
.error{color:#b00020;font-size:.9em}code{background:#f3f3f3;padding:0 .3em}</style></head><body>
<h1>Espejo de actualizaciones de Joomla</h1><p>Generado el %s. URL del core para Joomla Update: <code>%s</code></p>
<table><tr><th>Paquete</th><th>Versión</th><th>Cambio</th><th>Descarga</th></tr>%s</table>
%s
<p>Instrucciones completas en <a href="LEEME.txt">LEEME.txt</a>.</p></body></html>
""" % (_dt.datetime.now().strftime("%d/%m/%Y %H:%M"), html.escape(self.url("core/list.xml")), "\n".join(filas), bloque_manual)
        self.escribir("index.html", pagina)

    def guardar_estado(self):
        self.estado["generado"] = _dt.datetime.now().isoformat(timespec="seconds")
        self.escribir("estado.json", json.dumps(self.estado, indent=2, ensure_ascii=False) + "\n")

    def ejecutar(self):
        os.makedirs(self.salida, exist_ok=True)
        self.core()
        self.idiomas_joomla()
        self.extensiones()
        self.manual()
        self.generar_paquete()
        self.generar_leeme()
        self.generar_indice()
        self.guardar_estado()
        log("")
        log("Carpeta lista: %s" % self.salida)
        for r in self.resultados:
            log("  %-40s %-12s %s" % (r["nombre"], r["version"] or "?", r["cambio"]))
        log("")
        if self.manuales:
            log("Paquetes manuales (%s):" % self.origen_manual)
            for rel, tam, fecha, estado in self.manuales:
                log("  %-53s %-12s %s" % (rel[len("manual/"):], estado,
                                          getattr(self, "manual_info", {}).get(rel, "")))
        else:
            log("Paquetes manuales: ninguno (carpeta %s vacía)" % self.origen_manual)
        if self.errores:
            log("")
            log("Hubo %d error(es); detalles en LEEME.txt" % len(self.errores))
        return 1 if self.errores else 0


def main():
    ap = argparse.ArgumentParser(description="Genera un espejo offline de actualizaciones de Joomla.")
    ap.add_argument("-c", "--config", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json"),
                    help="fichero de configuración (por defecto config.json junto al script)")
    args = ap.parse_args()
    try:
        with open(args.config, encoding="utf-8") as f:
            config = json.load(f)
    except (OSError, ValueError) as e:
        print("No se pudo leer la configuración %s: %s" % (args.config, e), file=sys.stderr)
        return 2
    if "url_base" not in config:
        print("Falta 'url_base' en la configuración", file=sys.stderr)
        return 2
    return Espejo(config, args.config).ejecutar()


if __name__ == "__main__":
    sys.exit(main())
