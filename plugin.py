"""Main plugin class: registers the Processing provider and the dock panel."""

import os

from qgis.core import QgsApplication
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon

from .processing_provider.provider import GMDCOProcessingProvider

PLUGIN_DIR = os.path.dirname(__file__)
MENU_NAME = "&GMD CO Processing"


class GMDCOProcessingPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.provider = None
        self.dock = None
        self.action = None

    def initProcessing(self):  # noqa: N802 (QGIS API name)
        """Called by QGIS (also in headless/qgis_process) to register the provider."""
        self.provider = GMDCOProcessingProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def initGui(self):  # noqa: N802 (QGIS API name)
        self.initProcessing()

        from .ui.dock_widget import GMDCOProcessingDock

        self.dock = GMDCOProcessingDock(self.provider, self.iface.mainWindow())
        self.dock.setObjectName("GMDCOProcessingDock")
        self.iface.addDockWidget(Qt.RightDockWidgetArea, self.dock)
        self.dock.hide()

        # toggleViewAction() keeps the button state in sync with the dock.
        self.action = self.dock.toggleViewAction()
        self.action.setIcon(QIcon(os.path.join(PLUGIN_DIR, "icon.svg")))
        self.action.setText("GMD CO Processing panel")
        self.iface.addPluginToMenu(MENU_NAME, self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        if self.action is not None:
            self.iface.removePluginMenu(MENU_NAME, self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action = None

        if self.dock is not None:
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None

        if self.provider is not None:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None
