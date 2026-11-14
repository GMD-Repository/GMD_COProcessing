# GMD CO Processing
QGIS plugin for EA post-processing of GMD. Requires QGIS 3.22 or later.

## Install (with automatic updates)
The plugin has two release channels, each with its own plugin repository URL. Add **one** of them.

| Channel | Who it is for | Version looks like | Plugin repository URL |
|---|---|---|---|
| **Stable** | Everyone doing production work | `0.1.0` | **https://raw.githubusercontent.com/GMD-Repository/GMD_COProcessing/plugin-repo/plugins.xml** |
| **Beta** (pre-release) | Testers and developers trying the next version | `0.1.1-beta.1` | **https://raw.githubusercontent.com/GMD-Repository/GMD_COProcessing/plugin-repo-beta/plugins.xml** |

1. In QGIS open **Plugins > Manage and Install Plugins > Settings**.
2. For **Beta** only: tick **Show also experimental plugins**.
3. Click **Add…** and enter:
   - Name: `GMD CO Processing` (Stable) or `GMD CO Processing Beta` (Beta)
   - URL: the URL of your channel from the table above
4. Go to **All**, search for **GMD CO Processing** and click **Install**.

QGIS checks the URL and offers an **Upgrade** when a new version of that channel is released.
In the Plugin Manager a Beta build has the description prefix **[BETA]**.

The Beta URL works once the first Beta (`0.1.0-beta.1`) is published.

To switch channels, remove the other repository in **Settings** (or untick **Enabled**), then
**Reload all repositories** and install from the channel you kept. Do not keep both enabled: QGIS
would always offer the highest version of the two.

## Use
A **GMD CO Processing** menu appears in the menu bar. The toolbar button opens the panel, where you
pick the EA layer and run the scripts in [gmd_processing_tools/](gmd_processing_tools/).
See [gmd_processing_tools/README.md](gmd_processing_tools/README.md) for writing scripts.

## Documentation
How to use each tool: **[docs/README.md](docs/README.md)** — one page per script in
[docs/tools/](docs/tools/) (EA MBI Checker, National-Level MBI Checker, EA vs Barangay Check, MBI Barangay Selector, MBI EA Selector,
EARF Post-Processing).

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
