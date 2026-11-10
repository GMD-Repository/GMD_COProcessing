# GMD CO Processing
QGIS plugin for EA post-processing of GMD. Requires QGIS 3.22 or later.

## Install (with automatic updates)
1. In QGIS open **Plugins > Manage and Install Plugins > Settings**.
2. Tick **Show also experimental plugins**.
3. Click **Add…** and enter:
   - Name: `GMD CO Processing`
   - URL: **https://raw.githubusercontent.com/GMD-Repository/GMD_COProcessing/plugin-repo/plugins.xml**
4. Go to **All**, search for **GMD CO Processing** and click **Install**.

QGIS checks this link and offers an **Upgrade** when a new version is released.

## Use
A **GMD CO Processing** menu appears in the menu bar. The toolbar button opens the panel, where you
pick the EA layer and run the scripts in [gmd_processing_tools/](GMD_COProcessing/gmd_processing_tools/).
See [gmd_processing_tools/README.md](GMD_COProcessing/gmd_processing_tools/README.md) for writing scripts.

## Release a new version
Publish a GitHub release with a version tag, e.g. `v0.3.2`. The
[release workflow](.github/workflows/release.yml) builds the plugin zip and updates the link above.
Mark it *pre-release* to publish it as experimental.

## Folder structure
```
GMD_COProcessing/            # the plugin (what QGIS installs)
    __init__.py  plugin.py  metadata.txt  icon.svg  LICENSE
    gmd_processing_tools/    # scripts, icons, styles
    ui/                      # panel
.github/                     # release workflow
```
