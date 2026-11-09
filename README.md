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
3. The algorithms appear in the Processing Toolbox under **GMD CO Processing**, and
   the toolbar button / *Plugins > GMD CO Processing* menu opens the EA post-processing tool.

## Structure
```
__init__.py                      # classFactory entry point
metadata.txt                     # plugin metadata
plugin.py                        # registers provider, menu and toolbar action
icon.svg
processing_provider/
    provider.py                  # QgsProcessingProvider; register algorithms in loadAlgorithms()
    algorithms/
        ea_post_processing.py    # template algorithm (copies input to output)
```

## Adding an algorithm
1. Create a new `QgsProcessingAlgorithm` subclass in `processing_provider/algorithms/`.
2. Add it in `GMDCOProcessingProvider.loadAlgorithms()`.

It then appears automatically in the dock panel's tool list.

## UI (dock panel)
The toolbar button / *Plugins > GMD CO Processing* toggles a dock panel (`ui/`):

- **EA layer**: the vector layer passed to the tool's first layer input.
- **Tools**: every algorithm in the provider, with its help text.
- **Run**: runs in the background; outputs are temporary layers added to the project.
- **Advanced…**: opens the standard Processing dialog to set all parameters.
- Progress bar and log.

Edit the layout in `ui/dock_widget.ui` with Qt Designer (`qgis-ltr-designer.bat`
in the QGIS `bin` folder, which includes the QGIS widgets); logic is in `ui/dock_widget.py`.

## Releasing
Publishing a GitHub release runs [.github/workflows/release.yml](.github/workflows/release.yml), which
builds `GMD_COProcessing-<version>.zip` and attaches it to the release.

- The tag sets the plugin version: tag `v0.2.0` → `version=0.2.0` in the zipped `metadata.txt`.
- A release marked *pre-release* is packaged with `experimental=True`, otherwise `False`.
- Files listed as `export-ignore` in `.gitattributes` are left out of the zip.

## Packaging (manual)
Zip the `GMD_COProcessing` folder (excluding `.git` and `__pycache__`) and install via
*Plugins > Manage and Install Plugins > Install from ZIP*:

```
git archive --format=zip --prefix=GMD_COProcessing/ -o GMD_COProcessing.zip HEAD
```
