"""GMD CO Processing - QGIS plugin entry point."""


def classFactory(iface):  # noqa: N802 (QGIS API name)
    """Load the plugin class. Called by QGIS when the plugin is loaded."""
    from .plugin import GMDCOProcessingPlugin

    return GMDCOProcessingPlugin(iface)
