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
pick the EA layer and run the scripts in [gmd_processing_tools/](gmd_processing_tools/).
See [gmd_processing_tools/README.md](gmd_processing_tools/README.md) for writing scripts.

## Release a new version
Releases are automatic: every push to `main` that changes the plugin (anything except this README)
bumps the patch version (e.g. `v0.3.1` -> `v0.3.2`), creates a GitHub release and updates the link
above. QGIS users then see an **Upgrade**.

For a bigger version jump (e.g. `v0.4.0`), publish a release by hand with that tag; the next automatic
release continues from it (`v0.4.1`). Mark a manual release *pre-release* to publish it as experimental.

## Folder structure
The repo root is the plugin folder, so it can be cloned straight into the QGIS plugins folder for
development (`git clone ... GMD_COProcessing` inside `.../profiles/default/python/plugins/`), then
`git pull` and reload the plugin to update.
```
__init__.py  plugin.py  metadata.txt  icon.svg  LICENSE
gmd_processing_tools/        # scripts, icons, styles
ui/                          # panel
.github/                     # release workflow (left out of the zip)
```
