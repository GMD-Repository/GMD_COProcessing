# -*- coding: utf-8 -*-
r"""
EARF Post-Processing & Geocode Matching.

Multi-source preview (POPCEN / Preliminary EARF / EA GeoPackages) & geocode outer-join engine.
Features:
  - 3 dedicated source workspaces (POPCEN Database, Preliminary EARF files, and EA GeoPackages)
  - Full-featured Geocode Matching engine with outer-join cross-referencing
  - Real-time KPIs, multi-level filters, custom grouped headers, and pagination
  - One-click export to final GeoPackage deliverables and Excel summary reports

Usage:
  - From the GMD CO Processing panel: select "EARF Post-Processing" and click "Run".
  - From the QGIS menu: GMD CO Processing > EA Tools > EARF Post-Processing.
"""

# Menu / panel entry (read by the GMD CO Processing plugin)
TOOL_NAME = "EARF Post-Processing"
TOOL_GROUP = "EA Tools"
TOOL_ICON = "icons/earf_post_processing.svg"

import os
import sys

# Ensure local script directory and bundled packages are in search path
_CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if _CURRENT_DIR not in sys.path:
    sys.path.insert(0, _CURRENT_DIR)

_COPROC_DIR = os.path.join(_CURRENT_DIR, "_coprocessing")
if os.path.exists(_COPROC_DIR) and _COPROC_DIR not in sys.path:
    sys.path.insert(0, _COPROC_DIR)

# Import dialog class from local bundled package or fallback to COProcessing plugin
try:
    from _coprocessing.ui.dialog import COProcessingDialog
except (ImportError, ModuleNotFoundError):
    try:
        from ui.dialog import COProcessingDialog
    except (ImportError, ModuleNotFoundError):
        try:
            from COProcessing.ui.dialog import COProcessingDialog
        except (ImportError, ModuleNotFoundError) as err:
            raise ImportError(
                f"Could not import COProcessingDialog from _coprocessing or COProcessing: {err}"
            ) from err


DIALOG_OBJECT_NAME = "COProcessingDialog"


def show_dialog():
    """Open the EARF Post-Processing window (non-modal, so QGIS remains interactive)."""
    from qgis.PyQt.QtWidgets import QApplication, QDialog

    # Restore wait cursor if script was triggered from GMD CO Processing panel
    try:
        while QApplication.overrideCursor() is not None:
            QApplication.restoreOverrideCursor()
    except Exception:
        pass

    # Resolve QGIS interface
    qgis_iface = globals().get("iface")
    if qgis_iface is None:
        try:
            from qgis.utils import iface as qgis_iface
        except ImportError:
            qgis_iface = None

    main_win = qgis_iface.mainWindow() if qgis_iface is not None else None

    # Focus existing dialog if already opened
    if main_win is not None:
        for old in main_win.findChildren(QDialog):
            if old.objectName() == DIALOG_OBJECT_NAME or isinstance(old, COProcessingDialog):
                if old.isMinimized():
                    old.showNormal()
                old.show()
                old.raise_()
                old.activateWindow()
                return old

    # Instantiate dialog
    dialog = COProcessingDialog(qgis_iface, parent=main_win)
    dialog.setObjectName(DIALOG_OBJECT_NAME)

    # Optional: Pre-fill layer if an EA layer was selected in the GMD panel
    ea_layer = globals().get("ea_layer")
    if ea_layer is not None and hasattr(ea_layer, "source"):
        src = ea_layer.source()
        if src and (".gpkg" in src.lower() or os.path.exists(src)):
            # If layer has GPKG file path, populate preview line edit
            clean_path = src.split("|")[0].strip('"')
            if os.path.exists(clean_path):
                if hasattr(dialog, "txt_gpkg_preview_layers"):
                    dialog.txt_gpkg_preview_layers.setText(clean_path)
                if hasattr(dialog, "txt_gpkg_layers"):
                    dialog.txt_gpkg_layers.setText(clean_path)

    if dialog.isMinimized():
        dialog.showNormal()
    else:
        dialog.show()

    dialog.raise_()
    dialog.activateWindow()
    return dialog


# Execute entry point when run inside QGIS
if "qgis" in sys.modules and not os.environ.get("EARF_POST_PROCESSING_NO_DIALOG"):
    _DIALOG = show_dialog()
elif "qgis" not in sys.modules:
    print("Run this script inside QGIS: GMD CO Processing > EA Tools > EARF Post-Processing.")
