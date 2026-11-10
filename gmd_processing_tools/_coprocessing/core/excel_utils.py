# -*- coding: utf-8 -*-
"""
Helper utilities for Excel loading in QGIS.
Fixes known Windows fatal exception (Access Violation 0xC0000005) caused by
openpyxl delegating to lxml's C libxml2 parser inside QGIS threads.
"""

import os
import sys

# Ensure environment variable is set as early as possible
os.environ["OPENPYXL_LXML"] = "False"

# Block lxml from being imported by openpyxl to prevent loading lxml.etree C library into memory
if "lxml" not in sys.modules:
    sys.modules["lxml"] = None
if "lxml.etree" not in sys.modules:
    sys.modules["lxml.etree"] = None


def patch_openpyxl():
    """
    Ensure openpyxl strictly uses Python's built-in standard library xml.etree
    instead of lxml to prevent native access violation crashes in QGIS.
    """
    os.environ["OPENPYXL_LXML"] = "False"

    if "openpyxl" in sys.modules:
        try:
            import openpyxl
            openpyxl.LXML = False
            if hasattr(openpyxl, "xml"):
                openpyxl.xml.LXML = False
                import importlib
                if "openpyxl.xml.functions" in sys.modules:
                    importlib.reload(openpyxl.xml.functions)
                if "openpyxl.reader.excel" in sys.modules:
                    importlib.reload(openpyxl.reader.excel)
        except Exception:
            pass


# Automatically execute on import
patch_openpyxl()
