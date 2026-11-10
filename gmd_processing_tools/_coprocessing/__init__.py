# -*- coding: utf-8 -*-
"""
CO Processing — QGIS Plugin Entry Point
---------------------------------------
QGIS calls classFactory(iface) when the plugin is loaded.
"""

try:
    from .core import excel_utils  # noqa: F401 - Enforces openpyxl safety flags
except (ImportError, ValueError):
    import excel_utils  # noqa: F401


def classFactory(iface):
    """
    Load COProcessingPlugin.

    :param iface: A QGIS interface instance.
    :type iface: QgsInterface
    :rtype: COProcessingPlugin
    """
    try:
        from .plugin import COProcessingPlugin
    except (ImportError, ValueError):
        from plugin import COProcessingPlugin
    return COProcessingPlugin(iface)

