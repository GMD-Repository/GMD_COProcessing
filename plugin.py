"""Main plugin class: own menu in the QGIS menu bar, a toolbar button and the dock panel.

Menu layout:

    GMD CO Processing
    ├── [icon] EA Tools ▸ [icon] EA vs Barangay Check     (one submenu per TOOL_GROUP)
    ├── ───────────
    ├── [icon] GMD CO Processing Panel
    ├── [icon] New Script…
    └── [icon] Open Scripts Folder
"""

import os

from qgis.PyQt.QtCore import Qt, QUrl
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import QAction, QMenu

from .script_registry import SCRIPTS_DIR, earf_icon, group_icon, grouped_scripts, plugin_icon, theme_icon

MENU_TITLE = "&GMD CO Processing"
HELP_URL = "https://github.com/GMD-Repository/GMD_COProcessing#readme"


class GMDCOProcessingPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.dock = None
        self.menu = None
        self.panel_action = None
        self.earf_action = None
        self.help_action = None
        self._tools_end = None       # separator after the tool groups
        self._tool_actions = []      # group submenus, rebuilt every time the menu opens

    def initGui(self):  # noqa: N802 (QGIS API name)
        from .ui.dock_widget import GMDCOProcessingDock

        main_window = self.iface.mainWindow()

        # Clean up any leftover duplicate dock widgets from prior reloads or crashes
        for old_dock in main_window.findChildren(GMDCOProcessingDock):
            try:
                self.iface.removeDockWidget(old_dock)
                old_dock.deleteLater()
            except Exception:
                pass

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

        # Dedicated action for EARF Post-Processing (with icon)
        self.earf_action = QAction(earf_icon(), "EARF Post-Processing", main_window)
        self.earf_action.setObjectName("GMDEARFPostProcessingAction")
        self.earf_action.setToolTip("Open EARF Post-Processing & Geocode Matching workspace")
        self.earf_action.triggered.connect(self._open_earf_post_processing)
        self.menu.addAction(self.earf_action)

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

        # ---- toolbar buttons
        self.iface.addToolBarIcon(self.earf_action)
        self.iface.addToolBarIcon(self.panel_action)

        # ---- help entry under Help > Plugins (PyQGIS cookbook convention)
        self.help_action = QAction(plugin_icon(), "GMD CO Processing Help", main_window)
        self.help_action.setObjectName("GMDCOProcessingHelp")
        self.help_action.triggered.connect(lambda: QDesktopServices.openUrl(QUrl(HELP_URL)))
        self.iface.pluginHelpMenu().addAction(self.help_action)

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

    def _open_earf_post_processing(self):
        earf_script = os.path.join(SCRIPTS_DIR, "earf_post_processing.py")
        if os.path.exists(earf_script):
            self._run_script(earf_script)

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
        if self.help_action is not None:
            self.iface.pluginHelpMenu().removeAction(self.help_action)
            self.help_action.deleteLater()
            self.help_action = None

        if self.earf_action is not None:
            self.iface.removeToolBarIcon(self.earf_action)
            self.earf_action.deleteLater()
            self.earf_action = None

        if self.panel_action is not None:
            self.iface.removeToolBarIcon(self.panel_action)
            self.panel_action = None

        if self.menu is not None:
            self.iface.mainWindow().menuBar().removeAction(self.menu.menuAction())
            self.menu.deleteLater()
            self.menu = None
            self._tool_actions = []

        if self.dock is not None:
            try:
                self.iface.removeDockWidget(self.dock)
                self.dock.close()
                self.dock.setParent(None)
                self.dock.deleteLater()
            except Exception:
                pass
            self.dock = None

        # Close any open EARF Post-Processing dialogs so that old UI instances
        # don't linger across plugin reloads
        if main_win:
            try:
                from qgis.PyQt.QtWidgets import QDialog
                for dlg in main_win.findChildren(QDialog):
                    if dlg.objectName() == "COProcessingDialog":
                        try:
                            dlg.close()
                            dlg.setParent(None)
                            dlg.deleteLater()
                        except Exception:
                            pass
            except Exception:
                pass

        # Clear cached modules from sys.modules to guarantee clean re-import
        import sys
        for mod_name in list(sys.modules.keys()):
            if (
                mod_name.startswith("GMD_COProcessing")
                or mod_name.startswith("_coprocessing")
                or mod_name.startswith("gmd_processing_tools")
                or mod_name in ("dialog", "database", "plugin", "script_registry")
            ):
                del sys.modules[mod_name]
