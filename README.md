# GMD_COProcessing
QGIS plugin for EA Post-Processing of GMD.

## Requirements
- QGIS 3.22 or later

## Installation (development)
This repository is already cloned into the QGIS plugins folder:

```
%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\GMD_COProcessing
```

1. Restart QGIS (or use **Plugin Reloader**).
2. Enable **GMD CO Processing** in *Plugins > Manage and Install Plugins > Installed*.
   Because the plugin is marked experimental, tick *Show also experimental plugins* in *Settings* if it does not appear.
3. A **GMD CO Processing** menu appears in the QGIS menu bar (before *Help*):
   - *GMD CO Processing Panel*: show/hide the plugin panel (also the toolbar button)
   - *Run Script*: run any script in `gmd_processing_tools/`
   - *New Script…*, *Open Scripts Folder*

## Structure
```
__init__.py                      # classFactory entry point
metadata.txt                     # plugin metadata
plugin.py                        # menu bar menu, toolbar button and dock panel
icon.svg
gmd_processing_tools/            # YOUR SCRIPTS GO HERE (listed and run from the panel)
    _template.py                 # template used by "New..."
    example_ea_summary.py
ui/
    dock_widget.ui               # panel layout (Qt Designer)
    dock_widget.py               # panel logic
```

## Scripts (`gmd_processing_tools/`)
Drop `.py` files into [gmd_processing_tools/](gmd_processing_tools/); they appear in the panel
automatically. See [gmd_processing_tools/README.md](gmd_processing_tools/README.md) for the
variables available to scripts (`iface`, `ea_layer`).

## UI (dock panel)
*GMD CO Processing > GMD CO Processing Panel* (or the toolbar button) toggles the panel:

- **EA layer**: available in scripts as `ea_layer`.
- **Scripts**: every script in `gmd_processing_tools/`, with its docstring as description.
- **Run** (or double-click): runs the script; `print()` output and errors go to the log.
- **Open**: opens the script in the QGIS Python console editor.
- **New...**: creates a script from `_template.py` and opens it.
- **Folder**: opens `gmd_processing_tools/` in Explorer.

Edit the layout in `ui/dock_widget.ui` with Qt Designer (`qgis-ltr-designer.bat`
in the QGIS `bin` folder, which includes the QGIS widgets).

## Releasing
Publishing a GitHub release runs [.github/workflows/release.yml](.github/workflows/release.yml), which
builds `GMD_COProcessing-<version>.zip` and attaches it to the release.

- The tag must be a version number and sets the plugin version: tag `v0.2.0` → `version=0.2.0`.
- The zip and `plugins.xml` are also published to the `plugin-repo` branch. QGIS repository URL
  (*Plugins > Manage and Install Plugins > Settings > Add…*):
  `https://raw.githubusercontent.com/GMD-Repository/GMD_COProcessing/plugin-repo/plugins.xml`
- A release marked *pre-release* is packaged with `experimental=True`, otherwise `False`.
- Files listed as `export-ignore` in `.gitattributes` are left out of the zip.

## Packaging (manual)
Zip the `GMD_COProcessing` folder (excluding `.git` and `__pycache__`) and install via
*Plugins > Manage and Install Plugins > Install from ZIP*:

```
git archive --format=zip --prefix=GMD_COProcessing/ -o GMD_COProcessing.zip HEAD
```
