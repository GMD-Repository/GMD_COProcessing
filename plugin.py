"""Main plugin class: own menu in the QGIS menu bar, a toolbar button and the dock panel.

Menu layout:

    GMD CO Processing
    ├── [icon] EA Tools ▸ [icon] EA vs Barangay Check     (one submenu per TOOL_GROUP)
    ├── ───────────
    ├── [icon] GMD CO Processing Panel
    ├── [icon] New Script…
    └── [icon] Open Scripts Folder
"""

from qgis.PyQt.QtCore import Qt, QUrl
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import QMenu

from .script_registry import SCRIPTS_DIR, group_icon, grouped_scripts, plugin_icon, theme_icon

MENU_TITLE = "&GMD CO Processing"


class GMDCOProcessingPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.dock = None
        self.menu = None
        self.panel_action = None
        self._tools_end = None       # separator after the tool groups
        self._tool_actions = []      # group submenus, rebuilt every time the menu opens

    def initGui(self):  # noqa: N802 (QGIS API name)
        from .ui.dock_widget import GMDCOProcessingDock

        main_window = self.iface.mainWindow()

        # ---- dock panel (the plugin's UI)
        self.dock = GMDCOProcessingDock(self.iface, main_window)
        self.dock.setObjectName("GMDCOProcessingDock")
        self.iface.addDockWidget(Qt.RightDockWidgetArea, self.dock)
        self.dock.hide()

        # toggleViewAction() keeps the menu check mark / toolbar button in sync with the dock.
        self.panel_action = self.dock.toggleViewAction()
        self.panel_action.setIcon(plugin_icon())
        self.panel_action.setText("GMD CO Processing Panel")

        # ---- own top-level menu, placed before "Help"
        self.menu = QMenu(MENU_TITLE, main_window)
        self.menu.setObjectName("GMDCOProcessingMenu")
        self.menu.setToolTipsVisible(True)
        self.menu.aboutToShow.connect(self._rebuild_tool_menus)

        self._tools_end = self.menu.addSeparator()
        self.menu.addAction(self.panel_action)
        self.menu.addAction(theme_icon("mActionFileNew.svg"), "New Script…", self._new_script)
        self.menu.addAction(
            theme_icon("mIconFolderOpen.svg"),
            "Open Scripts Folder",
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(SCRIPTS_DIR)),
        )
        self._rebuild_tool_menus()

        menu_bar = main_window.menuBar()
        help_menu = self.iface.helpMenu()
        if help_menu is not None:
            menu_bar.insertMenu(help_menu.menuAction(), self.menu)
        else:
            menu_bar.addMenu(self.menu)

        # ---- toolbar button toggles the panel
        self.iface.addToolBarIcon(self.panel_action)

    # ------------------------------------------------------------ tool menus
    def _rebuild_tool_menus(self):
        """One submenu per group, rebuilt on open so new or edited scripts show up immediately."""
        for action in self._tool_actions:
            self.menu.removeAction(action)
            if action.menu() is not None:
                action.menu().deleteLater()
        self._tool_actions = []

        groups = grouped_scripts()
        if not groups:
            placeholder = self.menu.addAction("(no scripts in gmd_processing_tools)")
            placeholder.setEnabled(False)
            self.menu.insertAction(self._tools_end, placeholder)
            self._tool_actions.append(placeholder)
            return

        for group, scripts in groups:
            submenu = QMenu(group, self.menu)
            submenu.setIcon(group_icon(group))
            submenu.setToolTipsVisible(True)
            for script in scripts:
                action = submenu.addAction(script.icon, script.name)
                first_line = script.description.strip().splitlines()[0] if script.description.strip() else ""
                action.setToolTip(first_line)
                action.triggered.connect(lambda _=False, p=script.path: self._run_script(p))
            self._tool_actions.append(self.menu.insertMenu(self._tools_end, submenu))

    def _show_panel(self):
        self.dock.show()
        self.dock.raise_()

    def _run_script(self, path):
        if not self.dock.run_script(path):
            self._show_panel()  # show the error in the panel log

    def _new_script(self):
        self._show_panel()
        self.dock.new_script()

    def unload(self):
        if self.panel_action is not None:
            self.iface.removeToolBarIcon(self.panel_action)
            self.panel_action = None

        if self.menu is not None:
            self.iface.mainWindow().menuBar().removeAction(self.menu.menuAction())
            self.menu.deleteLater()
            self.menu = None
            self._tool_actions = []

        if self.dock is not None:
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None
