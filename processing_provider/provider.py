"""Processing provider that groups all GMD CO Processing algorithms."""

import os

from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

from .algorithms.ea_post_processing import EAPostProcessingAlgorithm

PLUGIN_DIR = os.path.dirname(os.path.dirname(__file__))


class GMDCOProcessingProvider(QgsProcessingProvider):
    ID = "gmdcoprocessing"
    NAME = "GMD CO Processing"

    def loadAlgorithms(self):  # noqa: N802 (QGIS API name)
        # Register new algorithms here.
        self.addAlgorithm(EAPostProcessingAlgorithm())

    def id(self):
        return self.ID

    def name(self):
        return self.NAME

    def longName(self):  # noqa: N802 (QGIS API name)
        return self.NAME

    def icon(self):
        return QIcon(os.path.join(PLUGIN_DIR, "icon.svg"))
