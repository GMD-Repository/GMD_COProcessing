# gmd_processing_tools

Put your PyQGIS scripts here. Every `*.py` file in this folder appears in the
**GMD CO Processing** panel, where it can be run or opened for editing.

- Each script sets its menu entry at the top of the file (after the docstring):

  ```python
  TOOL_NAME = "EA vs Barangay Check"     # label in the menu and panel
  TOOL_GROUP = "EA Tools"                # submenu: GMD CO Processing > EA Tools
  TOOL_ICON = "icons/ea_versus_bgy.svg"  # relative to this folder
  ```

  Without them the file name is used, the group is "Other Tools" and the icon is the Python icon.
  A group's icon is `icons/<group_in_snake_case>.svg` (e.g. `icons/ea_tools.svg`).
- Files starting with `_` (e.g. `_template.py`, `_helpers.py`) are hidden from the
  list; use them for shared helper code (`import _helpers` works from a script).
- The first line of a script's docstring is shown as its description.
- Scripts are re-read every time they run, so edits take effect immediately.

Variables available inside a script (same as the QGIS Python console):

| Name       | Value                                         |
|------------|-----------------------------------------------|
| `iface`    | the QGIS interface                            |
| `ea_layer` | the layer chosen in the panel's *EA layer* box (or `None`) |

`print()` output and errors are shown in the panel's log.
