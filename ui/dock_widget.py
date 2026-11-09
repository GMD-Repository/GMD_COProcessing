"""Dock panel: pick an EA layer and a tool, run it in the background."""

import os

from qgis.core import (
    QgsApplication,
    QgsMapLayerProxyModel,
    QgsProcessingAlgRunnerTask,
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsProcessingUtils,
    QgsProject,
)
from qgis.PyQt import uic
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtWidgets import QDockWidget, QListWidgetItem

FORM_CLASS, _ = uic.loadUiType(
    os.path.join(os.path.dirname(__file__), "dock_widget.ui")
)

# Parameter types that can receive the selected EA layer.
LAYER_PARAM_TYPES = {"source", "vector", "layer"}


class _Feedback(QgsProcessingFeedback):
    """Forwards algorithm messages as a signal (safe across threads)."""

    message = pyqtSignal(str)

    def pushInfo(self, info):  # noqa: N802
        super().pushInfo(info)
        self.message.emit(info)

    def pushWarning(self, warning):  # noqa: N802
        super().pushWarning(warning)
        self.message.emit(f"WARNING: {warning}")

    def reportError(self, error, fatalError=False):  # noqa: N802, N803
        super().reportError(error, fatalError)
        self.message.emit(f"ERROR: {error}")


class GMDCOProcessingDock(QDockWidget, FORM_CLASS):
    def __init__(self, provider, parent=None):
        super().__init__(parent)
        self.setupUi(self)
        self.provider = provider

        # Keep references alive while a task runs.
        self._task = None
        self._context = None
        self._feedback = None

        self.layerCombo.setFilters(QgsMapLayerProxyModel.VectorLayer)

        self.toolList.currentItemChanged.connect(self._update_help)
        self.runButton.clicked.connect(self.run_selected)
        self.cancelButton.clicked.connect(self.cancel)
        self.advancedButton.clicked.connect(self.open_advanced)
        self.provider.algorithmsLoaded.connect(self.populate_tools)

        self.populate_tools()

    # ----------------------------------------------------------------- tools
    def populate_tools(self):
        self.toolList.clear()
        algs = sorted(self.provider.algorithms(), key=lambda a: a.displayName())
        for alg in algs:
            item = QListWidgetItem(alg.icon(), alg.displayName())
            item.setData(Qt.UserRole, alg.id())
            item.setToolTip(alg.group())
            self.toolList.addItem(item)
        if algs:
            self.toolList.setCurrentRow(0)
        self._update_buttons()

    def _selected_alg_id(self):
        item = self.toolList.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _update_help(self, *_):
        alg_id = self._selected_alg_id()
        alg = QgsApplication.processingRegistry().algorithmById(alg_id) if alg_id else None
        self.helpText.setHtml((alg.shortHelpString() or "") if alg else "")
        self._update_buttons()

    def _update_buttons(self):
        running = self._task is not None
        has_tool = self._selected_alg_id() is not None
        self.runButton.setEnabled(has_tool and not running)
        self.advancedButton.setEnabled(has_tool and not running)
        self.cancelButton.setEnabled(running)
        self.toolList.setEnabled(not running)
        self.layerCombo.setEnabled(not running)

    # ------------------------------------------------------------ parameters
    def _build_params(self, alg):
        """Selected layer -> first layer input; temporary outputs for destinations."""
        params = {}
        layer = self.layerCombo.currentLayer()
        layer_assigned = False
        for p in alg.parameterDefinitions():
            if p.isDestination():
                params[p.name()] = "TEMPORARY_OUTPUT"
            elif not layer_assigned and layer and p.type() in LAYER_PARAM_TYPES:
                params[p.name()] = layer.id()
                layer_assigned = True
        return params

    # ------------------------------------------------------------------- run
    def log(self, text):
        self.logView.appendPlainText(text)

    def run_selected(self):
        alg_id = self._selected_alg_id()
        if not alg_id:
            return
        alg = QgsApplication.processingRegistry().createAlgorithmById(alg_id)

        context = QgsProcessingContext()
        context.setProject(QgsProject.instance())
        feedback = _Feedback()
        feedback.message.connect(self.log)
        feedback.progressChanged.connect(lambda v: self.progressBar.setValue(int(v)))

        params = self._build_params(alg)
        ok, msg = alg.checkParameterValues(params, context)
        if not ok:
            self.log(f"Cannot run '{alg.displayName()}': {msg}")
            self.log("Use 'Advanced…' to set the remaining parameters.")
            return

        self.progressBar.setValue(0)
        self.log(f"▶ {alg.displayName()} started")

        if alg.flags() & QgsProcessingAlgorithm.FlagNoThreading:
            ok, results = alg.run(params, context, feedback)
            self._finished(alg, context, ok, results)
            return

        task = QgsProcessingAlgRunnerTask(alg, params, context, feedback)
        task.executed.connect(
            lambda success, results: self._finished(alg, context, success, results)
        )
        self._task, self._context, self._feedback = task, context, feedback
        self._update_buttons()
        QgsApplication.taskManager().addTask(task)

    def cancel(self):
        if self._feedback is not None:
            self._feedback.cancel()
            self.log("Cancelling…")

    def _finished(self, alg, context, success, results):
        if success:
            self._load_outputs(alg, context, results)
            self.progressBar.setValue(100)
            self.log(f"✔ {alg.displayName()} finished")
        else:
            self.log(f"✖ {alg.displayName()} failed or was cancelled")
        self._task = self._context = self._feedback = None
        self._update_buttons()

    def _load_outputs(self, alg, context, results):
        for out in alg.outputDefinitions():
            value = results.get(out.name())
            if not isinstance(value, str):
                continue
            layer = QgsProcessingUtils.mapLayerFromString(value, context, True)
            if layer is None:
                if value:
                    self.log(f"  {out.description()}: {value}")
                continue
            layer = context.takeResultLayer(layer.id()) or layer
            layer.setName(out.description())
            QgsProject.instance().addMapLayer(layer)
            self.log(f"  Added layer '{layer.name()}'")

    def open_advanced(self):
        alg_id = self._selected_alg_id()
        if not alg_id:
            return
        import processing

        alg = QgsApplication.processingRegistry().algorithmById(alg_id)
        layer = self.layerCombo.currentLayer()
        initial = {}
        if layer:
            for p in alg.parameterDefinitions():
                if p.type() in LAYER_PARAM_TYPES:
                    initial[p.name()] = layer.id()
                    break
        processing.execAlgorithmDialog(alg_id, initial)
