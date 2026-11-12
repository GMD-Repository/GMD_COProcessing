"""GMD CO Processing - QGIS plugin entry point."""

import sys


def classFactory(iface):  # noqa: N802 (QGIS API name)
    """Load the plugin class. Called by QGIS when the plugin is loaded."""
    # Ensure any previously cached modules under GMD_COProcessing, _coprocessing,
    # or gmd_processing_tools are unloaded from sys.modules when Plugin Reloader runs
    for mod_name in list(sys.modules.keys()):
        if (
            mod_name.startswith("GMD_COProcessing")
            or mod_name.startswith("_coprocessing")
            or mod_name.startswith("gmd_processing_tools")
            or mod_name in ("dialog", "database", "plugin", "script_registry")
        ):
            del sys.modules[mod_name]

    from .plugin import GMDCOProcessingPlugin

    return GMDCOProcessingPlugin(iface)
