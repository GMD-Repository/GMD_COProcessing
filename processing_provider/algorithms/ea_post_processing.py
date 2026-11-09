"""Template algorithm for EA post-processing.

Currently copies the input EA layer to the output unchanged. Replace the
body of processAlgorithm() with the actual post-processing logic.
"""

from qgis.core import (
    QgsFeatureSink,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
)
from qgis.PyQt.QtCore import QCoreApplication


class EAPostProcessingAlgorithm(QgsProcessingAlgorithm):
    INPUT = "INPUT"
    OUTPUT = "OUTPUT"

    def tr(self, text):
        return QCoreApplication.translate("EAPostProcessingAlgorithm", text)

    def createInstance(self):  # noqa: N802 (QGIS API name)
        return EAPostProcessingAlgorithm()

    def name(self):
        return "ea_post_processing"

    def displayName(self):  # noqa: N802 (QGIS API name)
        return self.tr("EA post-processing")

    def group(self):
        return self.tr("EA tools")

    def groupId(self):  # noqa: N802 (QGIS API name)
        return "ea_tools"

    def shortHelpString(self):  # noqa: N802 (QGIS API name)
        return self.tr("Post-processes an Enumeration Area (EA) layer of the GMD.")

    def initAlgorithm(self, config=None):  # noqa: N802 (QGIS API name)
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.INPUT,
                self.tr("EA layer"),
                [QgsProcessing.TypeVectorAnyGeometry],
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT,
                self.tr("Processed EA layer"),
            )
        )

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802
        source = self.parameterAsSource(parameters, self.INPUT, context)
        if source is None:
            raise QgsProcessingException(
                self.invalidSourceError(parameters, self.INPUT)
            )

        sink, dest_id = self.parameterAsSink(
            parameters,
            self.OUTPUT,
            context,
            source.fields(),
            source.wkbType(),
            source.sourceCrs(),
        )
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT))

        total = 100.0 / source.featureCount() if source.featureCount() else 0
        for current, feature in enumerate(source.getFeatures()):
            if feedback.isCanceled():
                break
            # TODO: EA post-processing logic goes here.
            sink.addFeature(feature, QgsFeatureSink.FastInsert)
            feedback.setProgress(int(current * total))

        return {self.OUTPUT: dest_id}
