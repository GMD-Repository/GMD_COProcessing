"""Dock panel: list, run and open the scripts in gmd_processing_tools/."""

import os
import re
import runpy
import sys
import traceback
from contextlib import redirect_stderr, redirect_stdout

from qgis.core import QgsApplication, QgsMapLayerProxyModel
from qgis.PyQt import uic
from qgis.PyQt.QtCore import QFileSystemWatcher, QSize, Qt, QUrl
from qgis.PyQt.QtGui import QDesktopServices, QFont
from qgis.PyQt.QtWidgets import (
    QApplication,
    QDockWidget,
    QInputDialog,
    QListWidgetItem,
    QMessageBox,
)

from ..script_registry import SCRIPTS_DIR, group_icon, grouped_scripts, theme_icon

TEMPLATE = os.path.join(SCRIPTS_DIR, "_template.py")
PATH_ROLE = Qt.UserRole
DESCRIPTION_ROLE = Qt.UserRole + 1

FORM_CLASS, _ = uic.loadUiType(
    os.path.join(os.path.dirname(__file__), "dock_widget.ui")
)


class _LogWriter:
    """File-like object that sends print() output to the panel log."""

    def __init__(self, log):
        self._log = log
        self._buffer = ""

    def write(self, text):
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._log(line)
            QgsApplication.processEvents()  # keep the log updating during long scripts

    def flush(self):
        if self._buffer:
            self._log(self._buffer)
            self._buffer = ""


class GMDCOProcessingDock(QDockWidget, FORM_CLASS):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.setupUi(self)
        self.iface = iface
        self._running = False

        os.makedirs(SCRIPTS_DIR, exist_ok=True)

        self.layerCombo.setFilters(QgsMapLayerProxyModel.VectorLayer)
        self.layerCombo.setAllowEmptyLayer(True)

        self.scriptList.setIconSize(QSize(20, 20))
        self.runButton.setIcon(theme_icon("mActionStart.svg"))
        self.openButton.setIcon(theme_icon("mActionFileOpen.svg"))
        self.newButton.setIcon(theme_icon("mActionFileNew.svg"))
        self.folderButton.setIcon(theme_icon("mIconFolderOpen.svg"))

        self.scriptList.currentItemChanged.connect(self._update_help)
        self.scriptList.itemDoubleClicked.connect(lambda *_: self.run_selected())
        self.runButton.clicked.connect(self.run_selected)
        self.openButton.clicked.connect(self.open_selected)
        self.newButton.clicked.connect(self.new_script)
        self.folderButton.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(SCRIPTS_DIR))
        )

        # Refresh the list automatically when files are added/removed/renamed.
        self._watcher = QFileSystemWatcher([SCRIPTS_DIR], self)
        self._watcher.directoryChanged.connect(lambda *_: self.populate_scripts())

        self.populate_scripts()

    # --------------------------------------------------------------- listing
    def populate_scripts(self, select=None):
        """Group headings (not selectable) followed by their scripts."""
        current = select or self._selected_path()
        self.scriptList.clear()
        bold = QFont()
        bold.setBold(True)
        first_script = None
        for group, scripts in grouped_scripts():
            heading = QListWidgetItem(group_icon(group), group)
            heading.setFont(bold)
            heading.setFlags(Qt.ItemIsEnabled)  # a heading, not a script
            self.scriptList.addItem(heading)
            for script in scripts:
                item = QListWidgetItem(script.icon, "    " + script.name)
                item.setData(PATH_ROLE, script.path)
                item.setData(DESCRIPTION_ROLE, script.description)
                item.setToolTip(os.path.basename(script.path))
                self.scriptList.addItem(item)
                first_script = first_script or item
                if script.path == current:
                    self.scriptList.setCurrentItem(item)
        if self.scriptList.currentItem() is None and first_script is not None:
            self.scriptList.setCurrentItem(first_script)
        self._update_help()

    def _selected_path(self):
        item = self.scriptList.currentItem()
        return item.data(PATH_ROLE) if item else None

    def _update_help(self, *_):
        item = self.scriptList.currentItem()
        self.helpText.setPlainText((item.data(DESCRIPTION_ROLE) or "") if item else "")
        self._update_buttons()

    def _update_buttons(self):
        has_script = self._selected_path() is not None
        for button in (self.runButton, self.openButton):
            button.setEnabled(has_script and not self._running)
        self.newButton.setEnabled(not self._running)
        self.scriptList.setEnabled(not self._running)

    def log(self, text):
        self.logView.appendPlainText(text)

    # ------------------------------------------------------------------- run
    def run_selected(self):
        path = self._selected_path()
        if path:
            self.run_script(path)

    def run_script(self, path):
        """Run a script; returns False if it raised an error (details in the log)."""
        if self._running:
            return False

        name = os.path.basename(path)
        script_globals = {
            "iface": self.iface,
            "ea_layer": self.layerCombo.currentLayer(),
        }

        self._running = True
        self._update_buttons()
        self.log(f"▶ {name}")
        writer = _LogWriter(self.log)
        sys.path.insert(0, SCRIPTS_DIR)  # allow `import _helpers` from scripts
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            with redirect_stdout(writer), redirect_stderr(writer):
                runpy.run_path(path, init_globals=script_globals, run_name="__main__")
            writer.flush()
            self.log(f"✔ {name} finished")
            return True
        except SystemExit:
            writer.flush()
            self.log(f"✔ {name} exited")
            return True
        except Exception:  # noqa: BLE001 - report any script error in the log
            writer.flush()
            self.log(traceback.format_exc().rstrip())
            self.log(f"✖ {name} failed")
            return False
        finally:
            QApplication.restoreOverrideCursor()
            if SCRIPTS_DIR in sys.path:
                sys.path.remove(SCRIPTS_DIR)
            self._running = False
            self._update_buttons()

    # ------------------------------------------------------------ open / new
    def open_selected(self):
        path = self._selected_path()
        if path:
            self.open_script(path)

    def open_script(self, path):
        """Open in the QGIS Python console editor; fall back to the OS editor."""
        try:
            from console import console

            if console._console is None or not console._console.isVisible():
                console.show_console()
            console._console.console.tabEditorWidget.newTabEditor(filename=path)
            console._console.console.splitterObj.show()  # make sure the editor pane is visible
        except Exception:  # noqa: BLE001 - console internals differ between versions
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def new_script(self):
        title, ok = QInputDialog.getText(self, "New script", "Tool name (shown in the menu):")
        if not ok or not title.strip():
            return
        title = title.strip()
        name = re.sub(r"\W+", "_", title).strip("_").lower()
        if not name:
            return
        path = os.path.join(SCRIPTS_DIR, f"{name}.py")
        if os.path.exists(path):
            QMessageBox.warning(self, "New script", f"{name}.py already exists.")
            return

        try:
            with open(TEMPLATE, encoding="utf-8") as f:
                content = f.read()
        except OSError:
            content = '"""Describe what this script does."""\n\nTOOL_NAME = "My New Tool"\n'
        content = content.replace('TOOL_NAME = "My New Tool"', f"TOOL_NAME = {title!r}", 1)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

        self.populate_scripts(select=path)
        self.open_script(path)
