# -*- coding: utf-8 -*-
"""
Helper utilities for Excel loading in QGIS.
Fixes known Windows fatal exception (Access Violation 0xC0000005) caused by
openpyxl delegating to lxml's C libxml2 (which clashes with the libxml2 QGIS/GDAL
load) inside QGIS.

If openpyxl was imported *before* this module (by another plugin or earlier code),
it has already bound lxml's Element/SubElement/... into many of its submodules via
``from openpyxl.xml.functions import Element``. Reloading one module is not enough,
so we rebind those names in every loaded ``openpyxl.*`` module to the pure-Python
xml.etree equivalents.
"""

import os
import sys
import threading

# Ensure environment variable is set as early as possible
os.environ["OPENPYXL_LXML"] = "False"

# Block lxml from being imported by openpyxl to prevent loading lxml.etree C library into memory
if "lxml" not in sys.modules:
    sys.modules["lxml"] = None
if "lxml.etree" not in sys.modules:
    sys.modules["lxml.etree"] = None

_XML_NAMES = ("Element", "SubElement", "fromstring", "tostring", "iterparse",
              "xmlfile", "QName", "ElementTree", "register_namespace", "LXML")

_lock = threading.Lock()
_patched = False


def _openpyxl_uses_lxml():
    xml_pkg = sys.modules.get("openpyxl.xml")
    if xml_pkg is not None and getattr(xml_pkg, "LXML", False):
        return True
    funcs = sys.modules.get("openpyxl.xml.functions")
    el = getattr(funcs, "Element", None) if funcs is not None else None
    return bool(el is not None and "lxml" in (getattr(el, "__module__", "") or ""))


def patch_openpyxl():
    """
    Ensure openpyxl strictly uses Python's standard library xml.etree instead of
    lxml. Safe to call repeatedly; the real work runs at most once.
    """
    global _patched
    os.environ["OPENPYXL_LXML"] = "False"

    if _patched or "openpyxl" not in sys.modules:
        return  # not imported yet -> env var makes it import without lxml

    with _lock:
        if _patched:
            return
        try:
            if not _openpyxl_uses_lxml():
                _patched = True
                return

            import importlib
            import openpyxl.xml as oxml
            import openpyxl.xml.functions as funcs

            import openpyxl

            old = {n: getattr(funcs, n) for n in _XML_NAMES if hasattr(funcs, n)}
            # functions.py reads ``from openpyxl import LXML`` -> flip it BEFORE reload
            oxml.LXML = False
            openpyxl.LXML = False
            funcs = importlib.reload(funcs)
            new = {n: getattr(funcs, n) for n in _XML_NAMES if hasattr(funcs, n)}
            new["LXML"] = False

            # Rebind every stale lxml reference held by other openpyxl modules
            for name, mod in list(sys.modules.items()):
                if mod is None or not (name == "openpyxl" or name.startswith("openpyxl.")):
                    continue
                d = getattr(mod, "__dict__", None)
                if not d:
                    continue
                for attr, old_obj in old.items():
                    if attr in d and attr in new and (d[attr] is old_obj or attr == "LXML"):
                        d[attr] = new[attr]
            _patched = True
        except Exception:
            pass


# Automatically execute on import
patch_openpyxl()
