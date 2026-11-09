"""Finds the scripts in gmd_processing_tools/ and reads their menu metadata.

A script describes itself with optional string constants at the top of the file
(read without running the script):

    TOOL_NAME = "EA vs Barangay Check"     # label in the menu and panel (default: file name)
    TOOL_GROUP = "EA Tools"                # submenu / heading (default: OTHER_GROUP)
    TOOL_ICON = "icons/ea_versus_bgy.svg"  # relative to gmd_processing_tools/ (default: Python icon)

A group's icon is icons/<group as snake_case>.svg, e.g. icons/ea_tools.svg for "EA Tools".
"""

import ast
import os
import re
from dataclasses import dataclass

from qgis.core import QgsApplication
from qgis.PyQt.QtGui import QIcon

PLUGIN_DIR = os.path.dirname(__file__)
SCRIPTS_DIR = os.path.join(PLUGIN_DIR, "gmd_processing_tools")
ICONS_DIR = os.path.join(SCRIPTS_DIR, "icons")
OTHER_GROUP = "Other Tools"


@dataclass
class ScriptInfo:
    path: str
    name: str
    group: str
    icon_path: str
    description: str

    @property
    def icon(self):
        if self.icon_path and os.path.exists(self.icon_path):
            return QIcon(self.icon_path)
        return QgsApplication.getThemeIcon("/mIconPythonFile.svg")


def plugin_icon():
    return QIcon(os.path.join(PLUGIN_DIR, "icon.svg"))


def theme_icon(name):
    return QgsApplication.getThemeIcon(f"/{name}")


def group_icon(group):
    slug = re.sub(r"\W+", "_", group).strip("_").lower()
    path = os.path.join(ICONS_DIR, f"{slug}.svg")
    return QIcon(path) if os.path.exists(path) else theme_icon("mIconFolder.svg")


def _read_metadata(path):
    """String constants and docstring of a script, without running it."""
    try:
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
    except (OSError, SyntaxError, UnicodeDecodeError) as e:
        return {}, f"(could not read script: {e})"
    meta = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id.startswith("TOOL_")
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            meta[node.targets[0].id] = node.value.value
    return meta, ast.get_docstring(tree) or ""


def discover_scripts():
    """All scripts (files not starting with '_'), sorted by group then name."""
    os.makedirs(SCRIPTS_DIR, exist_ok=True)
    scripts = []
    for file_name in os.listdir(SCRIPTS_DIR):
        if not file_name.endswith(".py") or file_name.startswith("_"):
            continue
        path = os.path.join(SCRIPTS_DIR, file_name)
        meta, doc = _read_metadata(path)
        icon = meta.get("TOOL_ICON", "")
        scripts.append(ScriptInfo(
            path=path,
            name=meta.get("TOOL_NAME") or os.path.splitext(file_name)[0],
            group=meta.get("TOOL_GROUP") or OTHER_GROUP,
            icon_path=os.path.join(SCRIPTS_DIR, icon) if icon else "",
            description=doc,
        ))
    return sorted(scripts, key=lambda s: (s.group == OTHER_GROUP, s.group.lower(), s.name.lower()))


def grouped_scripts():
    """[(group, [ScriptInfo, ...]), ...] in display order."""
    groups = {}
    for script in discover_scripts():
        groups.setdefault(script.group, []).append(script)
    return list(groups.items())
