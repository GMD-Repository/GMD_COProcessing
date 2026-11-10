r"""MBI EA Coverage — choose which involved EA covers each MBI gap / overlap.

When the MBI Checker (EA Topology Checker) finds gaps or overlaps, every discrepancy
(case_uuid) involves two or more EAs — possibly from different EA layers. This window lists
each MBI case with all of its involved EAs, so you can tick the EA that covers the MBI area:

  • Overlap — the ticked EA keeps the overlap; the other EAs release (clip) it.
  • Gap     — the ticked EA absorbs the gap; the other EAs are not extended.

Workflow:
  Step 1  Select every EA layer the involved areas come from (one or more). EAs are matched
          by 'ea_geocode' across all selected layers.
  Step 2  Optionally pick a building point layer (and its geocode field) to count the
          buildings inside each MBI, and per involved EA the ones tagged to it.
  Step 3  Either click Run Check & Load Cases — runs the EA Topology Checker
          (EA_topochecker.py) on the selected EA layers, adds one "MBI Results" layer with
          every gap / overlap and lists its cases — or pick an existing MBI results layer
          (needs 'case_uuid') and click Load MBI Cases.
  Step 4  Tick the Role / Status box of the covering EA in each case.
          Auto-Assign Pending: single-EA cases go to that EA, multi-EA cases go to the EA
          owning most of the buildings inside the MBI (needs Step 2).
  Step 5  Apply Coverage writes 'assigned_ea' (covering EA), 'ea_role' (Covers / Releases,
          per row) and 'coverage_status' (1_Covered / 2_Not Covered, per case) into the MBI
          layer as unsaved, undoable edits.
  Step 6  Export GPKG + CSV writes the processed MBI layer and a decision log CSV into the
          Export folder (applying any pending choices first).
"""
# Menu / panel entry (read by the GMD CO Processing plugin)
TOOL_NAME = "MBI EA Selector"
TOOL_GROUP = "EA Tools"
TOOL_ICON = "icons/mbi_ea_selector.svg"

import csv
import os
import sys
import traceback
from datetime import datetime

# ===== MBI layer fields we read (written by EA_topochecker.py) =====
MBI_CASE_UUID = "case_uuid"
MBI_EA_GEOCODE = "ea_geocode"
MBI_EA_NAME = "ea_name"
MBI_GEOCODE = "geocode"
MBI_LEVEL = "mbi_level"
MBI_TYPE = "mbi_type"
MBI_NUM_BLDG = "num_bldg_pts"
MBI_BARANGAY = "barangay"
MBI_CITY_MUN = "city_mun"

# Fields we write
COVERAGE_FIELD = "assigned_ea"   # default; editable in the panel
STATUS_FIELD = "coverage_status"  # default; editable in the panel
ROLE_FIELD = "ea_role"
STATUS_COVERED = "1_Covered"
STATUS_NOT_COVERED = "2_Not Covered"
EXPORT_LAYER_NAME = "mbi_ea_coverage"
ROLE_COVERS = "Covers"
ROLE_RELEASES = "Releases"

# ===== EA layer fields =====
EA_GEOCODE_FIELDS = ["ea_geocode", "geocode"]
EA_NAME_FIELDS = ["name", "ean", "ea_name"]

# ===== Building point fields =====
BLDG_GEOCODE_FIELDS = ["ea_geocode", "geocode"]   # auto-picked in this order

# Status colours (table cells; text is forced dark so it stays readable in dark themes)
COLOUR_PENDING = "#ffcc80"     # orange
COLOUR_COVERED = "#a5d6a7"     # green
COLOUR_RELEASED = "#eeeeee"    # light grey
LEVEL_COLOURS = {
    "1_": "#ef9a9a", "2_": "#ef9a9a",   # inter-region / inter-province
    "3_": "#ffcc80",                    # inter-city/municipality
    "4_": "#fff59d",                    # inter-barangay
}


# ─────────────────── data helpers ───────────────────

def _field_value(feature, field_name):
    """Safely read a field from a QgsFeature, returning None for NULL / missing."""
    if not field_name or feature.fields().indexOf(field_name) < 0:
        return None
    v = feature[field_name]
    if v is None or (hasattr(v, "isNull") and v.isNull()):
        return None
    return v


def _text(feature, field_name):
    v = _field_value(feature, field_name)
    return "" if v is None else str(v).strip()


def _code_key(value):
    """Normalise a geocode for matching: '02003013000000', 2003013000000 and
    2003013000000.0 all give the same key."""
    if value is None or (hasattr(value, "isNull") and value.isNull()):
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    s = str(value).strip()
    return (s.lstrip("0") or "0") if s.isdigit() else s


def _pick_field(layer, candidates):
    """Return the first matching field name (case-insensitive) or None."""
    names = {f.name().lower(): f.name() for f in layer.fields()}
    for c in candidates:
        if c.lower() in names:
            return names[c.lower()]
    return None


class EACandidate:
    """One EA involved in an MBI case."""

    __slots__ = ("code", "geocode", "name", "barangay", "city_mun", "fids", "bldg")

    def __init__(self, code):
        self.code = code
        self.geocode = ""
        self.name = ""
        self.barangay = ""
        self.city_mun = ""
        self.fids = []      # MBI rows where this EA is "this area"
        self.bldg = None    # buildings inside the MBI tagged (by geocode) to this EA

    @property
    def label(self):
        return f"{self.code} ({self.name})" if self.name else self.code


class MBICase:
    """One MBI case_uuid: the discrepancy and all of its involved EAs."""

    def __init__(self, case_uuid, mbi_type=""):
        self.case_uuid = case_uuid
        self.mbi_type = mbi_type
        self.levels = set()
        self.num_bldg_pts = 0      # stored in the MBI layer by the topology checker
        self.bldg_live = None      # recounted from the Building Points layer (if set)
        self.bldg_untagged = 0     # of those, points whose geocode matches no involved EA
        self.feature_ids = []
        self.candidates = {}     # code -> EACandidate (insertion ordered)
        self.assigned_ea = ""    # code of the covering EA, "" = pending
        self.saved_ea = ""       # value already stored in the layer
        self.search_text = ""

    def add_feature(self, feat, coverage_field=None):
        self.feature_ids.append(feat.id())
        level = _text(feat, MBI_LEVEL)
        if level:
            self.levels.add(level)
        mbi_type = _text(feat, MBI_TYPE)
        if mbi_type:
            self.mbi_type = mbi_type
        bldg = _field_value(feat, MBI_NUM_BLDG)
        try:
            self.num_bldg_pts = max(self.num_bldg_pts, int(float(bldg)))
        except (TypeError, ValueError):
            pass

        # The row's own EA ("this area" of the pair)
        code = _text(feat, MBI_EA_GEOCODE) or _text(feat, MBI_GEOCODE)
        if code:
            cand = self.candidates.get(code)
            if cand is None:
                cand = self.candidates[code] = EACandidate(code)
            cand.fids.append(feat.id())
            cand.geocode = cand.geocode or _text(feat, MBI_GEOCODE)
            cand.name = cand.name or _text(feat, MBI_EA_NAME)
            cand.barangay = cand.barangay or _text(feat, MBI_BARANGAY)
            cand.city_mun = cand.city_mun or _text(feat, MBI_CITY_MUN)

        if coverage_field and not self.saved_ea:
            self.saved_ea = _text(feat, coverage_field)

    @property
    def bldg_pts(self):
        """Buildings inside the MBI: live count when a Building layer is set, else num_bldg_pts."""
        return self.num_bldg_pts if self.bldg_live is None else self.bldg_live

    @property
    def level(self):
        """Most severe MBI level of the case ('1_…' is the most severe)."""
        return min(self.levels) if self.levels else ""

    @property
    def level_label(self):
        lvl = self.level
        return lvl.split("_", 1)[1] if "_" in lvl else lvl

    @property
    def short_id(self):
        return self.case_uuid.strip("{}")[:8]

    @property
    def barangays(self):
        seen = []
        for c in self.candidates.values():
            if c.barangay and c.barangay not in seen:
                seen.append(c.barangay)
        return ", ".join(seen)

    def finish(self):
        parts = [self.case_uuid, self.mbi_type, self.level]
        for c in self.candidates.values():
            parts += [c.code, c.geocode, c.name, c.barangay, c.city_mun]
        self.search_text = " ".join(parts).lower()


def load_mbi_cases(mbi_layer, default_type="", only_fids=None, coverage_field=None, tick=None):
    """Group the MBI layer rows by case_uuid -> {uuid: MBICase}."""
    from qgis.core import QgsFeatureRequest

    request = QgsFeatureRequest().setFlags(QgsFeatureRequest.NoGeometry)
    if only_fids is not None:
        request.setFilterFids(list(only_fids))
    total = len(only_fids) if only_fids is not None else max(mbi_layer.featureCount(), 1)

    cases = {}
    for i, feat in enumerate(mbi_layer.getFeatures(request)):
        uuid = _text(feat, MBI_CASE_UUID)
        if uuid:
            case = cases.get(uuid)
            if case is None:
                case = cases[uuid] = MBICase(uuid, default_type)
            case.add_feature(feat, coverage_field)
        if tick and i % 200 == 0:
            tick(i, total)
    for case in cases.values():
        if case.saved_ea in case.candidates:
            case.assigned_ea = case.saved_ea
        case.finish()
    return cases


def read_ea_lookup(ea_layers):
    """ea_geocode -> {'name', 'fid', 'layer_id'} across several EA layers.
    Returns (lookup, duplicate_count); the first layer that has a code wins."""
    from qgis.core import QgsFeatureRequest

    lookup, duplicates = {}, 0
    for layer in ea_layers:
        gc_field = _pick_field(layer, EA_GEOCODE_FIELDS)
        name_field = _pick_field(layer, EA_NAME_FIELDS)
        if not gc_field:
            continue
        request = QgsFeatureRequest().setFlags(QgsFeatureRequest.NoGeometry)
        request.setSubsetOfAttributes([f for f in (gc_field, name_field) if f], layer.fields())
        for feat in layer.getFeatures(request):
            gc = _text(feat, gc_field)
            if not gc:
                continue
            if gc in lookup:
                duplicates += 1
                continue
            lookup[gc] = {"name": _text(feat, name_field), "fid": feat.id(), "layer_id": layer.id()}
    return lookup, duplicates


TOPOCHECKER_FILE = "EA_topochecker.py"
RUN_MODES = ["Overlaps and Gaps", "Overlaps Only", "Gaps Only"]
# Fields the topology checker reads from each EA layer ('geocode' is required: areas
# without it are left out of the MBI cases).
CHECKER_REQUIRED_FIELDS = ["geocode"]
# Child-algorithm chatter the checker's feedback receives; kept out of the log.
_NOISY_LOG = ("Results:", "Packaging layer", "Using specified destination CRS",
              "Setting output type", "No spatial index", "Scratch layer queued",
              "Done setting layer style", "Exporting Overlap Layer")


def _scripts_dir():
    if "__file__" in globals():
        return os.path.dirname(os.path.abspath(__file__))
    from qgis.core import QgsApplication
    return os.path.join(QgsApplication.qgisSettingsDirPath(), "python", "plugins",
                        "GMD_COProcessing", "gmd_processing_tools")


def load_topochecker():
    """Import EA_topochecker.py (fresh each time, so edits to it are picked up)."""
    import importlib.util
    path = os.path.join(_scripts_dir(), TOPOCHECKER_FILE)
    if not os.path.exists(path):
        raise FileNotFoundError(f"EA Topology Checker not found: {path}")
    spec = importlib.util.spec_from_file_location("gmd_ea_topochecker", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def infer_mbi_type(layer_name):
    name = layer_name.lower()
    if "overlap" in name and "gap" not in name:
        return "Overlap"
    if "gap" in name and "overlap" not in name:
        return "Gap"
    return ""


# ─────────────────── UI (styled after GEMMA's Geometry Check & Repair) ───────────────────

DIALOG_NAME = "MBIEACoverageDialog"
DIALOG_TITLE = "MBI EA Coverage"
DIALOG_SUBTITLE = ("Review every MBI gap / overlap with all of its involved EAs, "
                   "then choose the EA that covers it.")

BTN_RUN = ""
BTN_CANCEL = ""
BTN_EXPORT = ""
BTN_CLEAR = ""
GRP_STYLE = ("QGroupBox { font-weight:600; margin-top:6px; } "
             "QGroupBox::title { subcontrol-origin:margin; left:8px; }")
LEFT_WIDTH = 315

NO_CASES = ("No MBI cases loaded. Tick the EA layers on the left, then click Run Check & Load "
            "Cases — or pick an existing MBI Results layer and click Load MBI Cases.")

TREE_COLS = ["MBI Case / EA Geocode", "MBI Level", "Barangay", "EA Name", "Bldg Pts", "Role / Status"]
COL_ID, COL_LEVEL, COL_BGY, COL_NAME, COL_BLDG, COL_ROLE = range(6)

# Light / dark palettes (same values as GEMMA's Check and Update dialog)
THEMES = {
    False: {
        "title": "#2C3E50", "sub": "#7F8C8D", "line": "#BDC3C7", "hint": "#455a64",
        "summary": "#37474f",
        "log": ("background: #FFFFFF; color: #1F2937; font-family: Consolas, monospace; "
                "font-size: 11px; border: 1px solid #BDC3C7;"),
        "list": """
            QListWidget { border: 1px solid #b0bec5; outline: 0; }
            QListWidget::item { padding: 3px 4px; }
            QListWidget::indicator:checked { background-color: #1565c0; border: 1px solid #1565c0; }
        """,
        "tree": "",
        "tabs": """
            QTabBar::tab {
                font-weight: bold; font-size: 12px; min-width: 180px; padding: 10px 24px;
                margin-right: 2px; background-color: #EAEDED; color: #2C3E50;
                border-top-left-radius: 4px; border-top-right-radius: 4px;
            }
            QTabBar::tab:selected { background-color: #FFFFFF; color: #2980B9;
                                    border-bottom: 3px solid #3498DB; }
            QTabBar::tab:hover:!selected { background-color: #D5D8DC; }
            QTabWidget::pane { border: 1px solid #BDC3C7; }
        """,
        "help": ("QTextBrowser { background-color: #ffffff; color: #1f2937; font-size: 12px;"
                 " border: 1px solid #d1d5db; border-radius: 4px; padding: 8px; }"),
    },
    True: {
        "title": "#ECEFF1", "sub": "#B0BEC5", "line": "#455A64", "hint": "#B0BEC5",
        "summary": "#ECEFF1",
        "log": ("background: #1E2327; color: #ECEFF1; font-family: Consolas, monospace; "
                "font-size: 11px; border: 1px solid #37474F;"),
        "list": """
            QListWidget { border: 1px solid #455A64; border-radius: 4px;
                          background-color: #263238; color: #ECEFF1; }
            QListWidget::item { padding: 3px 4px; color: #ECEFF1; }
            QListWidget::item:hover { background-color: #37474F; }
            QListWidget::indicator:checked { background-color: #1976D2; border: 1px solid #1976D2; }
        """,
        "tree": """
            QTreeWidget { background-color: #263238; alternate-background-color: #1E2327;
                          color: #ECEFF1; selection-background-color: #1976D2;
                          selection-color: white; }
            QHeaderView::section { background-color: #37474F; color: #ECEFF1;
                                   border: 1px solid #455A64; padding: 4px; }
        """,
        "tabs": """
            QTabBar::tab {
                font-weight: bold; font-size: 12px; min-width: 180px; padding: 10px 24px;
                margin-right: 2px; background-color: #263238; color: #B0BEC5;
                border-top-left-radius: 4px; border-top-right-radius: 4px;
            }
            QTabBar::tab:selected { background-color: #37474F; color: #4FC3F7;
                                    border-bottom: 3px solid #29B6F6; }
            QTabBar::tab:hover:!selected { background-color: #324148; color: #ECEFF1; }
            QTabWidget::pane { border: 1px solid #455A64; }
        """,
        "help": ("QTextBrowser { background-color: #263238; color: #ECEFF1; font-size: 12px;"
                 " border: 1px solid #455A64; border-radius: 4px; padding: 8px; }"),
    },
}
# Help HTML colour swaps for dark mode
HELP_DARK_SWAPS = [("#111827", "#ECEFF1"), ("#1f2937", "#ECEFF1"), ("#374151", "#B0BEC5"),
                   ("#4b5563", "#B0BEC5"), ("#f9fafb", "#1E2327"), ("#e5e7eb", "#455A64")]

HELP_HTML = """
<div style="font-family:'Segoe UI',Arial,sans-serif; color:#1f2937; font-size:12px; line-height:1.5;">
  <div style="border-bottom:2px solid #374151; padding-bottom:8px; margin-bottom:14px;">
    <h2 style="margin:0 0 4px 0; color:#111827; font-size:17px;">MBI EA Coverage</h2>
    <div style="color:#4b5563;">Decide which involved EA covers each MBI gap or overlap, then export the result</div>
  </div>

  <h3 style="color:#111827; font-size:14px; border-bottom:1px solid #e5e7eb; padding-bottom:4px;">Quick Start</h3>
  <ol style="margin-top:4px;">
    <li><b>Tick your EA layers</b> (left, <i>EA Layers</i>) — every layer the involved areas come from.</li>
    <li><b>Optional:</b> pick a <i>Building Points</i> layer so each case and EA shows its building count.</li>
    <li>Click <b>Run Check &amp; Load Cases</b> to find the gaps / overlaps and list them.
        <i>Already have an MBI layer?</i> Pick it under <i>MBI Results Layer</i> and click
        <b>Load MBI Cases</b> instead.</li>
    <li>In the list, <b>tick the Role / Status box of the EA that covers</b> each case — or click
        <b>Auto-Assign Pending</b> to let the tool decide the easy ones, then review.</li>
    <li>Click <b>Apply Coverage</b> to write your choices into the MBI layer.</li>
    <li>Click <b>Export GPKG + CSV</b> to save the processed layer and a decision log.</li>
  </ol>

  <div style="background:#f9fafb; border:1px solid #e5e7eb; border-left:4px solid #4b5563;
              padding:10px 14px; margin:12px 0 16px 0;">
    <b style="color:#111827;">Nothing Reaches Disk Without Your Say-So:</b>
    Ticking EAs only changes the viewer. <i>Apply Coverage</i> writes into the MBI layer inside an
    ordinary QGIS edit session — keep it with Toggle Editing's <b>Save Edits</b>, or undo with
    <b>Edit &gt; Undo</b> / <b>Discard Edits</b>. EA layers are never edited. Files are only created by
    <i>Export GPKG + CSV</i>.
  </div>

  <h3 style="color:#111827; font-size:14px; border-bottom:1px solid #e5e7eb; padding-bottom:4px;">Inputs (left panel)</h3>
  <table width="100%" cellpadding="6" cellspacing="0" style="border:1px solid #e5e7eb; font-size:11px;">
    <tr><td width="110" style="font-weight:bold; vertical-align:top;">MBI Results Layer</td>
        <td>The Gaps / Overlaps layer (must have <i>case_uuid</i>). Created for you by <i>Run Check</i>;
        pick one yourself only to continue earlier work. Tick <i>Only selected MBI features</i> to load
        just part of it, and <i>Resume saved coverage choices</i> to pre-tick EAs already stored in it.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">EA Layers</td>
        <td>Click every EA layer the involved areas come from — an inter-barangay or inter-municipal MBI
        can involve EAs stored in different layers. EAs are matched on <i>ea_geocode</i>; the log reports
        involved EAs that none of the selected layers contain (shown in red in the list).</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">Building Points</td>
        <td>Optional. <b>Case row:</b> building points inside the gap / overlap. <b>EA rows:</b> of those,
        the points whose <i>Geocode field</i> (auto: ea_geocode, then geocode) matches the EA. Used by
        Auto-Assign.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">Run Topology Check</td>
        <td>Choose <i>Overlaps and Gaps</i>, <i>Overlaps Only</i> or <i>Gaps Only</i>, then
        <b>Run Check &amp; Load Cases</b>. It runs the EA Topology Checker on the ticked EA layers, adds one
        <i>MBI Results</i> layer (a temporary layer — right-click &gt; <b>Make Permanent</b> to keep it) and
        loads its cases. A gap / overlap that runs across more than two EAs is <b>one case</b> listing all of
        them. EA layers need <i>geocode</i> and <i>ea_geocode</i> fields and should be in EPSG:4326.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">Load MBI Cases</td>
        <td>Lists the cases of the picked MBI layer without running the check.</td></tr>
  </table>

  <h3 style="color:#111827; font-size:14px; border-bottom:1px solid #e5e7eb; padding-bottom:4px;">Reading the Case List</h3>
  <table width="100%" cellpadding="6" cellspacing="0" style="border:1px solid #e5e7eb; font-size:11px;">
    <tr><td width="110" style="font-weight:bold; vertical-align:top;">Case row (bold)</td>
        <td>One gap / overlap. Shows its MBI level, the barangays involved, how many EAs are involved and
        the <b>Bldg Pts</b> inside it. Status is <b>Pending</b> (orange) until an EA is chosen, then
        <b>Covered by &lt;EA&gt;</b> (green).</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">EA rows (below)</td>
        <td>Each involved EA with its barangay and its own <b>Bldg Pts</b> (buildings inside this MBI tagged to
        that EA). <b>Tick the Role / Status box of the covering EA.</b> Only one EA can cover a case —
        ticking another moves the coverage, unticking makes the case Pending again.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">Moving around</td>
        <td>Click a row to select it on the map, double-click to zoom, right-click for more (set covering EA,
        mark pending, copy case ID). Use the search box and the <i>All / Pending / Covered / Overlaps /
        Gaps / level</i> filter to find cases. Click a column header to sort.</td></tr>
  </table>

  <h3 style="color:#111827; font-size:14px; border-bottom:1px solid #e5e7eb; padding-bottom:4px;">Roles</h3>
  <table width="100%" cellpadding="6" cellspacing="0" style="border:1px solid #e5e7eb; font-size:11px;">
    <tr><td width="110" style="font-weight:bold;">Overlap</td>
        <td>Ticked EA: <b>Keeps overlap</b> (role <i>Covers</i>). Others: <b>Releases (clip)</b> (role <i>Releases</i>).</td></tr>
    <tr><td style="font-weight:bold;">Gap</td>
        <td>Ticked EA: <b>Absorbs gap</b> (role <i>Covers</i>). Others: <b>Not extended</b> (role <i>Releases</i>).</td></tr>
    <tr><td style="font-weight:bold;">Auto-Assign</td>
        <td>Single-EA cases go to that EA. Multi-EA cases go to the EA that owns most of the buildings inside
        the MBI (needs Building Points); no tagged buildings or a tie stays Pending — decide those by hand.</td></tr>
  </table>

  <h3 style="color:#111827; font-size:14px; border-bottom:1px solid #e5e7eb; padding-bottom:4px;">Apply Coverage — Fields Written</h3>
  <table width="100%" cellpadding="6" cellspacing="0" style="border:1px solid #e5e7eb; font-size:11px;">
    <tr><td width="110" style="font-weight:bold; vertical-align:top;">Coverage field<br>(default <i>assigned_ea</i>)</td>
        <td>Code of the covering EA, on every row of the case. Empty while the case is Pending.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">ea_role</td>
        <td><b>Covers</b> on the covering EA's rows, <b>Releases</b> on the others. Empty while Pending.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">Status field<br>(default <i>coverage_status</i>)</td>
        <td><b>1_Covered</b> — an EA covers the case. <b>2_Not Covered</b> — the case is still Pending.
        Written for every loaded case, so you can filter or symbolise on it.</td></tr>
  </table>
  <div style="color:#4b5563; margin-top:4px;">The two field names can be changed in the panel (letters, digits and
  underscores only, all different). Missing fields are added to the MBI layer automatically.</div>

  <h3 style="color:#111827; font-size:14px; border-bottom:1px solid #e5e7eb; padding-bottom:4px;">Export GPKG + CSV</h3>
  <table width="100%" cellpadding="6" cellspacing="0" style="border:1px solid #e5e7eb; font-size:11px;">
    <tr><td width="110" style="font-weight:bold; vertical-align:top;">Export folder</td>
        <td>Where the files go. Leave it empty and you are asked for a folder when you click Export.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">GeoPackage</td>
        <td><i>mbi_ea_coverage_&lt;date&gt;_&lt;time&gt;.gpkg</i> — the whole MBI layer including the coverage fields,
        even if you have not saved the layer's edits yet.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">CSV</td>
        <td><i>mbi_ea_coverage_&lt;date&gt;_&lt;time&gt;.csv</i> — one row per case / EA: case ID, type, level, building
        counts, EA code / name / barangay, role, covering EA and status. Opens in Excel.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">Not applied yet?</td>
        <td>If your choices are not in the MBI layer yet, Export offers to <b>Apply Coverage first</b>, so the
        files always match what you see. Pending cases are exported as <b>2_Not Covered</b>.</td></tr>
  </table>

  <h3 style="color:#111827; font-size:14px; border-bottom:1px solid #e5e7eb; padding-bottom:4px;">If Something Looks Wrong</h3>
  <table width="100%" cellpadding="6" cellspacing="0" style="border:1px solid #e5e7eb; font-size:11px;">
    <tr><td width="110" style="font-weight:bold; vertical-align:top;">Run Check is greyed out</td>
        <td>Tick at least one EA layer, and make sure each has <i>geocode</i> and <i>ea_geocode</i> fields.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">Load MBI Cases is greyed out</td>
        <td>The picked MBI layer has no <i>case_uuid</i> field — use Run Check to create a proper one.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">EA in red / "not found"</td>
        <td>The EA's layer is not ticked. Tick it, then Load again.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">EA rows show 0 buildings</td>
        <td>No building inside this MBI is tagged to that EA. Check the <i>Geocode field</i> under Building Points
        is the one holding the EA geocode.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">Field name turns red</td>
        <td>Use letters, digits and underscores only, starting with a letter or underscore; the Coverage field,
        Status field and <i>ea_role</i> must all differ. Apply and Export stay disabled until fixed.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">Made a mistake</td>
        <td>Before Apply: <b>Reset Choices</b> or untick. After Apply: <b>Edit &gt; Undo</b> or
        <b>Discard Edits</b> on the MBI layer (before saving).</td></tr>
  </table>
</div>
"""


class _Cancelled(Exception):
    pass


def make_dialog_class():
    import re
    from qgis.core import (QgsApplication, QgsCoordinateTransform, QgsFeature, QgsFeatureRequest,
                           QgsField, QgsGeometry, QgsProcessingContext, QgsProcessingFeedback,
                           QgsProject, QgsVectorFileWriter, QgsVectorLayer, QgsWkbTypes)
    from qgis.gui import QgsFieldComboBox, QgsFileWidget, QgsMapLayerComboBox
    from qgis.PyQt import QtWidgets
    from qgis.PyQt.QtCore import QEvent, Qt, QVariant
    from qgis.PyQt.QtGui import QBrush, QColor

    try:
        from qgis.core import Qgis
        polygon_filter = Qgis.LayerFilter.PolygonLayer
        point_filter = Qgis.LayerFilter.PointLayer
    except AttributeError:
        from qgis.core import QgsMapLayerProxyModel
        polygon_filter = QgsMapLayerProxyModel.PolygonLayer
        point_filter = QgsMapLayerProxyModel.PointLayer

    ROLE_UUID = Qt.UserRole
    ROLE_EA = Qt.UserRole + 1
    ROLE_ORDER = Qt.UserRole + 2
    DARK_TEXT = QBrush(QColor("#212121"))

    def field_names(layer):
        return {f.name().lower() for f in layer.fields()}

    def group(title=""):
        box = QtWidgets.QGroupBox(title)
        box.setStyleSheet(GRP_STYLE)
        return box, QtWidgets.QVBoxLayout(box)

    def button(text, height=32, style=BTN_RUN):
        b = QtWidgets.QPushButton(text)
        b.setFixedHeight(height)
        b.setStyleSheet(style)
        return b

    def layer_combo(filters):
        combo = QgsMapLayerComboBox()
        combo.setFilters(filters)
        combo.setAllowEmptyLayer(True)
        combo.setLayer(None)
        combo.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(12)
        return combo

    def paint(item, column, colour):
        if colour:
            item.setBackground(column, QBrush(QColor(colour)))
            item.setForeground(column, DARK_TEXT)
        else:
            item.setData(column, Qt.BackgroundRole, None)
            item.setData(column, Qt.ForegroundRole, None)

    class LayerCheckDelegate(QtWidgets.QStyledItemDelegate):
        """Clicking anywhere on a layer row toggles its checkbox (GEMMA's input-list behaviour)."""

        def editorEvent(self, event, model, option, index):
            if not (index.flags() & Qt.ItemIsUserCheckable) or not (index.flags() & Qt.ItemIsEnabled):
                return False
            if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
                cur = index.data(Qt.CheckStateRole)
                model.setData(index, Qt.Unchecked if cur == Qt.Checked else Qt.Checked,
                              Qt.CheckStateRole)
                return True
            return False

    class CheckFeedback(QgsProcessingFeedback):
        """Shows the topology checker's progress / messages in the dialog and keeps the UI
        responsive (so Cancel works) while it runs on the main thread."""

        def __init__(self, dialog):
            super().__init__()
            self.dialog = dialog
            self.progressChanged.connect(self._on_progress)

        def _on_progress(self, value):
            self.dialog.progress.setValue(int(value))
            QtWidgets.QApplication.processEvents()

        def _log(self, text, prefix=""):
            text = str(text).strip()
            if text and not any(text.startswith(n) for n in _NOISY_LOG) \
                    and "feature(s) from input layer were successfully matched" not in text:
                self.dialog.log(f"  {prefix}{text}")
            QtWidgets.QApplication.processEvents()

        def pushInfo(self, info):
            self._log(info)

        def pushWarning(self, warning):
            self._log(warning, "WARNING: ")

        def reportError(self, error, fatalError=False):
            self._log(error, "ERROR: ")

        def pushCommandInfo(self, info):
            pass

        def pushDebugInfo(self, info):
            pass

        def pushConsoleInfo(self, info):
            pass

    class TreeItem(QtWidgets.QTreeWidgetItem):
        """Sorts cases by the clicked column, but keeps each case's EAs in a fixed order
        (otherwise ticking an EA re-sorts the rows under the cursor)."""

        def __lt__(self, other):
            tree = self.treeWidget()
            desc = tree is not None and tree.header().sortIndicatorOrder() == Qt.DescendingOrder
            a_ord, b_ord = self.data(COL_ID, ROLE_ORDER), other.data(COL_ID, ROLE_ORDER)
            by_order = (a_ord > b_ord) if desc else (a_ord < b_ord)
            if self.parent() is not None:
                return by_order
            col = tree.sortColumn() if tree is not None else COL_ID
            a, b = self.data(col, Qt.DisplayRole), other.data(col, Qt.DisplayRole)
            a = "" if a is None else a
            b = "" if b is None else b
            if a == b:
                return by_order
            try:
                return a < b
            except TypeError:
                return str(a) < str(b)

    class CoverageDialog(QtWidgets.QDialog):
        def __init__(self, iface, parent=None):
            super().__init__(parent)
            self.setObjectName(DIALOG_NAME)
            self.setWindowTitle(f"{DIALOG_TITLE} — MBI EA Selector")
            icon = os.path.join(os.path.dirname(os.path.abspath(__file__)), TOOL_ICON) \
                if "__file__" in globals() else ""
            if icon and os.path.exists(icon):
                from qgis.PyQt.QtGui import QIcon
                self.setWindowIcon(QIcon(icon))
            self.setWindowFlags(self.windowFlags()
                                | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint)
            self.setAttribute(Qt.WA_DeleteOnClose, True)
            self.setMinimumSize(860, 560)
            self.resize(1100, 740)

            self.iface = iface
            self.cases = {}           # case_uuid -> MBICase
            self.case_items = {}      # case_uuid -> top-level tree item
            self.ea_lookup = {}       # ea_geocode -> {name, fid, layer_id}
            self.mbi_layer_id = None
            self.running = False
            self.cancel_requested = False
            self._painting = False
            self._inputs_ok = False
            self._run_ok = False
            self._run_feedback = None
            self._field_ok = True
            self.is_dark = self.palette().color(self.palette().Window).lightness() < 128

            root = QtWidgets.QVBoxLayout(self)
            root.setContentsMargins(12, 12, 12, 12)
            root.setSpacing(8)
            self.title_label = QtWidgets.QLabel(DIALOG_TITLE)
            self.title_label.setWordWrap(True)
            root.addWidget(self.title_label)
            self.sub_label = QtWidgets.QLabel(DIALOG_SUBTITLE)
            self.sub_label.setWordWrap(True)
            root.addWidget(self.sub_label)
            self.sep_line = QtWidgets.QFrame()
            self.sep_line.setFrameShape(QtWidgets.QFrame.HLine)
            root.addWidget(self.sep_line)

            self.tabs = QtWidgets.QTabWidget()
            self.tabs.addTab(self._build_main_tab(), "MBI EA Coverage")
            self.tabs.addTab(self._build_help_tab(), "Help and Information")
            root.addWidget(self.tabs, 1)

            close_row = QtWidgets.QHBoxLayout()
            close_row.addStretch()
            close_btn = QtWidgets.QPushButton("Close")
            close_btn.setMinimumWidth(100)
            close_btn.clicked.connect(self.close)
            close_row.addWidget(close_btn)
            root.addLayout(close_row)

            prj = QgsProject.instance()
            prj.layersAdded.connect(self.refresh_ea_list)
            prj.layersRemoved.connect(self.refresh_ea_list)
            self.mbi_combo.layerChanged.connect(self.refresh_ea_list)
            for combo in (self.mbi_combo, self.bldg_combo):
                combo.layerChanged.connect(self.validate_inputs)
            self.refresh_ea_list()
            self.apply_theme()
            self.update_summary()

        # ──────────────── construction ────────────────
        def _build_main_tab(self):
            tab = QtWidgets.QWidget()
            root = QtWidgets.QHBoxLayout(tab)
            root.setContentsMargins(6, 6, 6, 6)
            root.setSpacing(8)

            # ── LEFT: inputs and load controls ──
            left = QtWidgets.QWidget()
            left.setFixedWidth(LEFT_WIDTH)
            ll = QtWidgets.QVBoxLayout(left)
            ll.setContentsMargins(0, 0, 0, 0)
            ll.setSpacing(6)

            box, lay = group("MBI Results Layer")
            self.mbi_combo = layer_combo(polygon_filter)
            self.mbi_combo.setToolTip("Gaps / Overlaps output of the EA Topology Checker.")
            lay.addWidget(self.mbi_combo)
            self.mbi_hint = self._hint()
            lay.addWidget(self.mbi_hint)
            self.selected_only = QtWidgets.QCheckBox("Only selected MBI features")
            self.selected_only.setToolTip("Load only the cases of the features selected on the map.")
            lay.addWidget(self.selected_only)
            self.preload_check = QtWidgets.QCheckBox("Resume saved coverage choices")
            self.preload_check.setChecked(True)
            self.preload_check.setToolTip("Pre-tick EAs from the coverage field already in the MBI layer.")
            lay.addWidget(self.preload_check)
            ll.addWidget(box)

            box, lay = group("EA Layers")
            sel_row = QtWidgets.QHBoxLayout()
            self.btn_all = button("Select All", 24)
            self.btn_all.clicked.connect(lambda: self._check_all(True))
            self.btn_none = button("Clear", 24)
            self.btn_none.clicked.connect(lambda: self._check_all(False))
            sel_row.addWidget(self.btn_all)
            sel_row.addWidget(self.btn_none)
            lay.addLayout(sel_row)
            self.ea_list = QtWidgets.QListWidget()
            self.ea_list.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
            self.ea_list.setAlternatingRowColors(True)
            self.ea_list.setToolTip("Click a layer to check/uncheck it. Select every EA layer\n"
                                    "the MBI's involved areas come from.")
            self.ea_list.setItemDelegate(LayerCheckDelegate(self.ea_list))
            self.ea_list.itemChanged.connect(self.validate_inputs)
            lay.addWidget(self.ea_list, 1)
            self.layer_count_label = self._hint("Selected layers: 0 / 0")
            lay.addWidget(self.layer_count_label)
            self.ea_hint = self._hint()
            lay.addWidget(self.ea_hint)
            ll.addWidget(box, 1)

            box, lay = group("Building Points (Optional)")
            self.bldg_combo = layer_combo(point_filter)
            lay.addWidget(self.bldg_combo)
            frow = QtWidgets.QHBoxLayout()
            frow.addWidget(QtWidgets.QLabel("Geocode field:"))
            self.bldg_field_combo = QgsFieldComboBox()
            self.bldg_field_combo.setAllowEmptyFieldName(True)
            self.bldg_field_combo.setToolTip(
                "Building point field holding the EA geocode. Each building inside an MBI is\n"
                "credited to the involved EA whose ea_geocode (or geocode) matches it.")
            frow.addWidget(self.bldg_field_combo, 1)
            lay.addLayout(frow)
            self.bldg_combo.layerChanged.connect(self._on_bldg_layer)
            self.bldg_field_combo.fieldChanged.connect(self.validate_inputs)
            self.bldg_hint = self._hint()
            lay.addWidget(self.bldg_hint)
            ll.addWidget(box)

            box, lay = group("Run Topology Check")
            self.mode_combo = QtWidgets.QComboBox()
            self.mode_combo.addItems(RUN_MODES)
            self.mode_combo.setToolTip("Which discrepancies the EA Topology Checker looks for.")
            lay.addWidget(self.mode_combo)
            self.run_btn = button("Run Check && Load Cases")
            self.run_btn.setToolTip(
                "Run the EA Topology Checker on the ticked EA layers (and Building Points),\n"
                "add the resulting MBI Results layer and list its cases in the viewer.")
            self.run_btn.clicked.connect(self.run_check)
            lay.addWidget(self.run_btn)
            self.run_hint = self._hint()
            lay.addWidget(self.run_hint)
            ll.addWidget(box)

            self.load_btn = button("Load MBI Cases")
            self.load_btn.setToolTip("Read the existing MBI Results layer (picked above) and list each\n"
                                     "case with all of its involved EAs.")
            self.load_btn.clicked.connect(lambda: self.load_cases())
            ll.addWidget(self.load_btn)
            r1 = QtWidgets.QHBoxLayout()
            self.cancel_btn = button("Cancel", 32, BTN_CANCEL)
            self.cancel_btn.clicked.connect(self.request_cancel)
            self.clear_btn = button("Clear", 32, BTN_CLEAR)
            self.clear_btn.setToolTip("Clear the case viewer and the log.")
            self.clear_btn.clicked.connect(self.clear_all)
            r1.addWidget(self.cancel_btn)
            r1.addWidget(self.clear_btn)
            ll.addLayout(r1)
            self.progress = QtWidgets.QProgressBar()
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
            self.progress.setFixedHeight(18)
            ll.addWidget(self.progress)

            # ── RIGHT: case viewer, coverage panel, log ──
            right = QtWidgets.QWidget()
            rl = QtWidgets.QVBoxLayout(right)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(6)

            self.summary = QtWidgets.QLabel(NO_CASES)
            self.summary.setWordWrap(True)
            rl.addWidget(self.summary)

            filters = QtWidgets.QHBoxLayout()
            self.search_edit = QtWidgets.QLineEdit()
            self.search_edit.setPlaceholderText(
                "Filter by Barangay name, Geocode, EA name or Case ID…")
            self.search_edit.setClearButtonEnabled(True)
            self.search_edit.textChanged.connect(self.apply_filter)
            filters.addWidget(self.search_edit, 1)
            self.show_combo = QtWidgets.QComboBox()
            self.show_combo.currentIndexChanged.connect(self.apply_filter)
            filters.addWidget(self.show_combo)
            for icon, tip, slot in (("/mActionExpandTree.svg", "Expand all cases", self._expand),
                                    ("/mActionCollapseTree.svg", "Collapse all cases", self._collapse)):
                tb = QtWidgets.QToolButton()
                tb.setIcon(QgsApplication.getThemeIcon(icon))
                tb.setToolTip(tip)
                tb.clicked.connect(slot)
                filters.addWidget(tb)
            self.zoom_btn = QtWidgets.QToolButton()
            self.zoom_btn.setIcon(QgsApplication.getThemeIcon("/mActionZoomToSelected.svg"))
            self.zoom_btn.setToolTip("Zoom to the selected cases / EAs")
            self.zoom_btn.clicked.connect(self.zoom_to_selection)
            filters.addWidget(self.zoom_btn)
            rl.addLayout(filters)
            self._fill_show_combo()

            self.tree = QtWidgets.QTreeWidget()
            self.tree.setColumnCount(len(TREE_COLS))
            self.tree.setHeaderLabels(TREE_COLS)
            head = self.tree.headerItem()
            head.setToolTip(COL_BLDG,
                            "Case row: buildings inside the gap / overlap (num_bldg_pts, or a live\n"
                            "count when Building Points is set).\n"
                            "EA row: of those, the buildings whose geocode belongs to that EA.")
            head.setToolTip(COL_ROLE, "Tick the EA that covers the MBI (one per case).")
            self.tree.setAlternatingRowColors(True)
            self.tree.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
            self.tree.setUniformRowHeights(True)
            self.tree.setSortingEnabled(True)
            self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
            self.tree.customContextMenuRequested.connect(self.show_context_menu)
            self.tree.itemChanged.connect(self.on_item_changed)
            self.tree.itemDoubleClicked.connect(self.on_double_click)
            self.tree.itemSelectionChanged.connect(self.on_selection_changed)
            rl.addWidget(self.tree, 1)

            swatch = '<span style="background-color:{}; border:1px solid #999;">&nbsp;&nbsp;&nbsp;</span> {}'
            self.legend = QtWidgets.QLabel(" &nbsp;&nbsp;".join([
                swatch.format(COLOUR_PENDING, "Pending"),
                swatch.format(COLOUR_COVERED, "Covers MBI"),
                swatch.format(COLOUR_RELEASED, "Releases / not extended"),
            ]) + " &nbsp;&nbsp;·&nbsp; Double-click: zoom · Right-click: more")
            rl.addWidget(self.legend)

            # Coverage panel sits below the viewer: Load -> Review & tick -> Apply.
            self.apply_group, gf = group("")
            gf.setContentsMargins(10, 24, 10, 10)
            gf.setSpacing(7)
            orow = QtWidgets.QHBoxLayout()
            orow.addWidget(QtWidgets.QLabel("Coverage field:"))
            self.field_edit = QtWidgets.QLineEdit(COVERAGE_FIELD)
            self.field_edit.setMaximumWidth(140)
            self.field_edit.textChanged.connect(self.validate_inputs)
            self.field_edit.setToolTip("Field that receives the code of the covering EA.")
            orow.addWidget(self.field_edit)
            orow.addSpacing(10)
            orow.addWidget(QtWidgets.QLabel("Status field:"))
            self.status_edit = QtWidgets.QLineEdit(STATUS_FIELD)
            self.status_edit.setMaximumWidth(140)
            self.status_edit.setToolTip(f"Field that receives the case status: '{STATUS_COVERED}' "
                                        f"(an EA covers it) or '{STATUS_NOT_COVERED}' (still pending).")
            self.status_edit.textChanged.connect(self.validate_inputs)
            orow.addWidget(self.status_edit)
            orow.addStretch(1)
            gf.addLayout(orow)
            erow = QtWidgets.QHBoxLayout()
            erow.addWidget(QtWidgets.QLabel("Export folder:"))
            self.folder_widget = QgsFileWidget()
            self.folder_widget.setStorageMode(QgsFileWidget.GetDirectory)
            self.folder_widget.setToolTip("Where Export GPKG + CSV writes its files.")
            self.folder_widget.fileChanged.connect(self.validate_inputs)
            erow.addWidget(self.folder_widget, 1)
            gf.addLayout(erow)
            self.folder_hint = self._hint()
            gf.addWidget(self.folder_hint)
            arow = QtWidgets.QHBoxLayout()
            self.auto_btn = button("Auto-Assign Pending", 32)
            self.auto_btn.setToolTip(
                "Single-EA cases go to that EA. Multi-EA cases go to the EA that owns most of\n"
                "the buildings inside the MBI (needs Building Points + geocode field);\n"
                "no tagged buildings or a tie stays pending.")
            self.auto_btn.clicked.connect(self.auto_assign)
            self.reset_btn = button("Reset Choices", 32, BTN_CANCEL)
            self.reset_btn.setToolTip("Clear every EA tick in the viewer (nothing is written).")
            self.reset_btn.clicked.connect(self.reset_choices)
            self.apply_btn = button("Apply Coverage", 32, BTN_EXPORT)
            self.apply_btn.setToolTip(
                f"Writes the covering EA ('{COVERAGE_FIELD}'), each row's role ('{ROLE_FIELD}') and\n"
                f"the case status ('{STATUS_FIELD}': {STATUS_COVERED} / {STATUS_NOT_COVERED}) into the\n"
                "MBI layer. Changes are left UNSAVED (the layer enters edit mode) — use QGIS's\n"
                "Save Edits to keep them, or Cancel Edits to discard and revert.")
            self.apply_btn.clicked.connect(self.apply_coverage)
            self.export_btn = button("Export GPKG + CSV", 32, BTN_EXPORT)
            self.export_btn.setToolTip(
                "Write the processed MBI layer (with the coverage fields) to a GeoPackage, plus a\n"
                "CSV of every case / EA decision, into the Export folder. Choices not yet applied\n"
                "are applied first.")
            self.export_btn.clicked.connect(self.export_results)
            arow.addWidget(self.auto_btn, 1)
            arow.addWidget(self.reset_btn, 1)
            arow.addWidget(self.apply_btn, 2)
            arow.addWidget(self.export_btn, 2)
            gf.addLayout(arow)
            self.apply_group.setEnabled(False)
            rl.addWidget(self.apply_group)

            rl.addWidget(QtWidgets.QLabel("Log"))
            self.log_box = QtWidgets.QPlainTextEdit()
            self.log_box.setReadOnly(True)
            self.log_box.setFixedHeight(150)
            rl.addWidget(self.log_box)

            root.addWidget(left)
            root.addWidget(right, 1)
            return tab

        def _build_help_tab(self):
            tab = QtWidgets.QWidget()
            lay = QtWidgets.QVBoxLayout(tab)
            lay.setContentsMargins(8, 8, 8, 8)
            self.help_text = QtWidgets.QTextBrowser()
            self.help_text.setOpenExternalLinks(True)
            lay.addWidget(self.help_text)
            return tab

        def _on_bldg_layer(self, layer):
            """Point the field picker at the new layer and pre-select its geocode field."""
            self.bldg_field_combo.blockSignals(True)
            self.bldg_field_combo.setLayer(layer)
            self.bldg_field_combo.setField(
                (_pick_field(layer, BLDG_GEOCODE_FIELDS) or "") if layer is not None else "")
            self.bldg_field_combo.blockSignals(False)
            self.validate_inputs()

        def bldg_field(self):
            layer = self.bldg_combo.currentLayer()
            name = self.bldg_field_combo.currentField()
            return name if layer is not None and name and layer.fields().indexOf(name) >= 0 else None

        def _hint(self, text=""):
            lab = QtWidgets.QLabel(text)
            lab.setWordWrap(True)
            lab.setProperty("hintKind", "info")
            return lab

        def set_hint(self, label, text, kind="info"):
            """kind: 'error' (red), 'ok' (green) or 'info' (theme hint grey)."""
            label.setText(text)
            label.setProperty("hintKind", kind)
            colour = {"error": "#e57373" if self.is_dark else "#c62828",
                      "ok": "#81c784" if self.is_dark else "#2e7d32"}.get(
                kind, THEMES[self.is_dark]["hint"])
            label.setStyleSheet(f"color:{colour}; font-size:11px;")

        def _fill_show_combo(self, levels=()):
            current = self.show_combo.currentData()
            self.show_combo.blockSignals(True)
            self.show_combo.clear()
            for label, data in [("All cases", None), ("Pending", "pending"), ("Covered", "covered"),
                                ("Overlaps", "type:Overlap"), ("Gaps", "type:Gap")]:
                self.show_combo.addItem(label, data)
            for lvl in sorted(levels):
                self.show_combo.addItem(lvl, f"level:{lvl}")
            idx = self.show_combo.findData(current)
            self.show_combo.setCurrentIndex(max(idx, 0))
            self.show_combo.blockSignals(False)

        def _expand(self):
            self.tree.expandAll()

        def _collapse(self):
            self.tree.collapseAll()

        # ──────────────── theme ────────────────
        def apply_theme(self):
            t = THEMES[self.is_dark]
            self.title_label.setStyleSheet(
                f"font-size: 15px; font-weight: bold; color: {t['title']}; padding: 2px 0;")
            self.sub_label.setStyleSheet(f"color: {t['sub']}; font-size: 11px; margin-bottom: 4px;")
            self.sep_line.setStyleSheet(f"color: {t['line']};")
            self.tabs.setStyleSheet(t["tabs"])
            self.summary.setStyleSheet(f"font-size:13px; font-weight:600; color:{t['summary']};")
            self.log_box.setStyleSheet(t["log"])
            self.ea_list.setStyleSheet(t["list"])
            self.tree.setStyleSheet(t["tree"])
            self.legend.setStyleSheet(f"font-size:11px; color:{t['hint']}; padding:2px 0;")
            html = HELP_HTML
            if self.is_dark:
                for a, b in HELP_DARK_SWAPS:
                    html = html.replace(a, b)
            self.help_text.setStyleSheet(t["help"])
            self.help_text.setHtml(html)
            for lab in (self.mbi_hint, self.ea_hint, self.bldg_hint, self.folder_hint,
                        self.layer_count_label, self.run_hint):
                self.set_hint(lab, lab.text(), lab.property("hintKind") or "info")

        def showEvent(self, event):
            super().showEvent(event)
            dark = self.palette().color(self.palette().Window).lightness() < 128
            if dark != self.is_dark:
                self.is_dark = dark
                self.apply_theme()

        # ──────────────── teardown ────────────────
        def _teardown(self):
            prj = QgsProject.instance()
            for sig in (prj.layersAdded, prj.layersRemoved):
                try:
                    sig.disconnect(self.refresh_ea_list)
                except (TypeError, RuntimeError):
                    pass

        def done(self, result):
            self._teardown()
            super().done(result)

        def closeEvent(self, event):
            self._teardown()
            super().closeEvent(event)

        # ──────────────── EA layer list (multi-select) ────────────────
        def _ea_items(self):
            return [self.ea_list.item(i) for i in range(self.ea_list.count())
                    if self.ea_list.item(i).data(Qt.UserRole)]

        def ea_layers(self):
            prj = QgsProject.instance()
            return [lyr for lyr in (prj.mapLayer(i.data(Qt.UserRole)) for i in self._ea_items()
                                    if i.checkState() == Qt.Checked) if lyr is not None]

        def refresh_ea_list(self, *_args):
            """Polygon layers other than the MBI layer, keeping existing checks."""
            checked = {i.data(Qt.UserRole) for i in self._ea_items() if i.checkState() == Qt.Checked}
            mbi = self.mbi_combo.currentLayer()
            layers = [l for l in QgsProject.instance().mapLayers().values()
                      if isinstance(l, QgsVectorLayer)
                      and l.geometryType() == QgsWkbTypes.PolygonGeometry
                      and (mbi is None or l.id() != mbi.id())
                      and "case_uuid" not in field_names(l)]
            self.ea_list.blockSignals(True)
            self.ea_list.clear()
            for layer in sorted(layers, key=lambda l: l.name().lower()):
                item = QtWidgets.QListWidgetItem(layer.name())
                item.setData(Qt.UserRole, layer.id())
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if layer.id() in checked else Qt.Unchecked)
                if not _pick_field(layer, EA_GEOCODE_FIELDS):
                    item.setToolTip("No 'ea_geocode' / 'geocode' field — EAs cannot be matched.")
                    item.setForeground(QBrush(QColor("#9e9e9e")))
                self.ea_list.addItem(item)
            self.ea_list.blockSignals(False)
            self.validate_inputs()

        def _check_all(self, on):
            self.ea_list.blockSignals(True)
            for item in self._ea_items():
                item.setCheckState(Qt.Checked if on else Qt.Unchecked)
            self.ea_list.blockSignals(False)
            self.validate_inputs()

        # ──────────────── validation ────────────────
        def validate_inputs(self, *_args):
            if not hasattr(self, "log_box"):
                return False
            ok = True
            mbi = self.mbi_combo.currentLayer()
            if mbi is None:
                self.set_hint(self.mbi_hint, "Pick an existing MBI Results layer, or create one with "
                                             "Run Check & Load Cases below.")
                ok = False
            elif "case_uuid" not in field_names(mbi):
                self.set_hint(self.mbi_hint, "Missing 'case_uuid' field — run the EA Topology "
                                             "Checker to produce an MBI layer.", "error")
                ok = False
            else:
                kind = infer_mbi_type(mbi.name())
                self.set_hint(self.mbi_hint, f"{mbi.featureCount():,} MBI row(s)"
                              + (f" · {kind}s" if kind else ""), "ok")

            eas = self.ea_layers()
            self.set_hint(self.layer_count_label,
                          f"Selected layers: {len(eas)} / {len(self._ea_items())}")
            if not eas:
                self.set_hint(self.ea_hint, "Optional: without EA layers, EA names come from the MBI "
                                            "layer; zoom to EA and building counts are off.")
            else:
                bad = [l.name() for l in eas if not _pick_field(l, EA_GEOCODE_FIELDS)]
                if bad:
                    self.set_hint(self.ea_hint, "No 'ea_geocode' field in: " + ", ".join(bad), "error")
                else:
                    total = sum(l.featureCount() for l in eas)
                    self.set_hint(self.ea_hint, f"{total:,} EA(s) in the selected layers.", "ok")

            bldg = self.bldg_combo.currentLayer()
            if bldg is None:
                self.set_hint(self.bldg_hint, "Optional: building counts per case / EA will be skipped.")
            elif not self.bldg_field():
                self.set_hint(self.bldg_hint, f"{bldg.featureCount():,} building point(s). Pick the "
                                              "geocode field to count buildings per EA (case totals "
                                              "only otherwise).")
            else:
                self.set_hint(self.bldg_hint, f"{bldg.featureCount():,} building point(s) · EA counts "
                                              f"match '{self.bldg_field()}'", "ok")

            names = (self.coverage_field(), self.status_field(), ROLE_FIELD)
            valid = [bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", n)) for n in names]
            clash = len({n.lower() for n in names}) < len(names)
            bad = "border: 1px solid #c62828;"
            self._field_ok = all(valid) and not clash
            self.field_edit.setStyleSheet("" if valid[0] and not clash else bad)
            self.status_edit.setStyleSheet("" if valid[1] and not clash else bad)

            folder = self.folder_widget.filePath().strip()
            if not folder:
                self.set_hint(self.folder_hint, "Export writes a GPKG and a CSV here "
                                                "(you are asked for a folder if empty).")
            elif os.path.isdir(folder):
                self.set_hint(self.folder_hint, "Export GPKG + CSV writes its files here.", "ok")
            else:
                self.set_hint(self.folder_hint, "Folder does not exist.", "error")

            # Run Topology Check: needs EA layers carrying the checker's fields.
            if not eas:
                self._run_ok = False
                self.set_hint(self.run_hint, "Tick the EA layer(s) to check.")
            else:
                missing = [l.name() for l in eas
                           if any(not _pick_field(l, [f]) for f in CHECKER_REQUIRED_FIELDS)
                           or not _pick_field(l, EA_GEOCODE_FIELDS)]
                if missing:
                    self._run_ok = False
                    self.set_hint(self.run_hint, "Needs 'geocode' and 'ea_geocode' fields — missing in: "
                                  + ", ".join(missing), "error")
                elif not eas[0].crs().isGeographic():
                    self._run_ok = True
                    self.set_hint(self.run_hint, f"'{eas[0].name()}' is not in a geographic CRS "
                                                 "(EPSG:4326 expected) — results may be off.", "error")
                else:
                    self._run_ok = True
                    self.set_hint(self.run_hint, f"Checks {len(eas)} EA layer(s)"
                                  + ("" if bldg is not None else ", without building counts") + ".")

            self._inputs_ok = ok
            self.update_buttons()
            return ok

        def coverage_field(self):
            return self.field_edit.text().strip()

        def status_field(self):
            return self.status_edit.text().strip()

        # ──────────────── layers / logging / state ────────────────
        @property
        def mbi_layer(self):
            return QgsProject.instance().mapLayer(self.mbi_layer_id) if self.mbi_layer_id else None

        def log(self, text):
            stamp = datetime.now().strftime("%H:%M:%S")
            for line in str(text).splitlines() or [""]:
                self.log_box.appendPlainText(f"[{stamp}] {line}")

        def tick(self, done, total, text=None):
            self.progress.setValue(int(100 * min(done, total) / max(total, 1)))
            if text:
                self.summary.setText(text)
            QtWidgets.QApplication.processEvents()
            if self.cancel_requested:
                raise _Cancelled()

        def request_cancel(self):
            if self.running:
                self.cancel_requested = True
                if self._run_feedback is not None:
                    self._run_feedback.cancel()
                self.summary.setText("Cancelling…")

        def clear_all(self):
            unsaved = sum(1 for c in self.cases.values() if c.assigned_ea != c.saved_ea)
            if unsaved and QtWidgets.QMessageBox.question(
                    self, DIALOG_TITLE,
                    f"{unsaved:,} choice(s) have not been applied yet. Clear the viewer anyway?",
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel) != QtWidgets.QMessageBox.Yes:
                return
            self._painting = True
            self.tree.clear()
            self._painting = False
            self.cases, self.case_items, self.ea_lookup = {}, {}, {}
            self.mbi_layer_id = None
            self.log_box.clear()
            self.progress.setValue(0)
            self._fill_show_combo()
            self.validate_inputs()
            self.update_summary()

        def set_busy(self, busy):
            self.running = busy
            self.cancel_requested = False
            for w in (self.mbi_combo, self.ea_list, self.btn_all, self.btn_none, self.bldg_combo,
                      self.selected_only, self.preload_check, self.tree, self.search_edit,
                      self.show_combo, self.clear_btn, self.mode_combo, self.bldg_field_combo):
                w.setEnabled(not busy)
            if busy:
                self.progress.setValue(0)
                QtWidgets.QApplication.setOverrideCursor(Qt.WaitCursor)
            else:
                QtWidgets.QApplication.restoreOverrideCursor()
            self.update_buttons()

        def update_buttons(self):
            if not hasattr(self, "apply_group"):
                return
            idle = not self.running
            has_cases = bool(self.cases) and self.mbi_layer is not None
            self.cancel_btn.setEnabled(self.running)
            self.load_btn.setEnabled(idle and self._inputs_ok)
            self.run_btn.setEnabled(idle and self._run_ok)
            self.apply_group.setEnabled(idle and has_cases)
            self.apply_btn.setEnabled(self._field_ok)
            self.export_btn.setEnabled(self._field_ok)
            self.zoom_btn.setEnabled(idle and bool(self.tree.selectedItems()))

        def update_summary(self):
            total = len(self.cases)
            covered = sum(1 for c in self.cases.values() if c.assigned_ea)
            shown = sum(1 for t in self.case_items.values() if not t.isHidden())
            if not total:
                text = NO_CASES
            else:
                text = f"{total:,} MBI case(s) - {total - covered:,} pending, {covered:,} covered."
                if shown != total:
                    text += f" Showing {shown:,}."
                unapplied = any(c.assigned_ea != c.saved_ea for c in self.cases.values())
                if covered < total:
                    text += " Tick the covering EA of each case in the viewer."
                elif unapplied:
                    text += " Click Apply Coverage to write the choices."
                else:
                    text += (" Coverage applied - Save Edits on the MBI layer to keep it, "
                             "or click Export GPKG + CSV.")
            if not self.running:
                self.summary.setText(text)
            self.update_buttons()

        # ──────────────── load ────────────────
        # ──────────────── run topology check ────────────────
        def _unapplied_ok(self, action):
            unsaved = sum(1 for c in self.cases.values() if c.assigned_ea != c.saved_ea)
            return not unsaved or QtWidgets.QMessageBox.question(
                self, DIALOG_TITLE,
                f"{unsaved:,} choice(s) have not been applied yet. {action} anyway?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel) == QtWidgets.QMessageBox.Yes

        def run_check(self, *_args):
            """Run EA_topochecker.py on the ticked EA layers, add the combined MBI Results
            layer to the project and load its cases into the viewer."""
            if self.running:
                return
            self.validate_inputs()
            if not self._run_ok or not self._unapplied_ok("Run a new check"):
                return
            eas = self.ea_layers()
            bldg = self.bldg_combo.currentLayer()
            mode = self.mode_combo.currentIndex()

            self.set_busy(True)
            self.log("=" * 50)
            self.log(f"Running EA Topology Check ({RUN_MODES[mode]}) on {len(eas)} EA layer(s): "
                     + ", ".join(l.name() for l in eas))
            if bldg is None:
                self.log("  No Building Points layer — building counts will be 0.")
            self.summary.setText("Running the EA Topology Check…")
            result = None
            start = datetime.now()
            try:
                topo = load_topochecker()
                bldg_inputs = [bldg] if bldg is not None else [
                    QgsVectorLayer(f"Point?crs={eas[0].crs().authid() or 'EPSG:4326'}",
                                   "no_buildings", "memory")]
                feedback = self._run_feedback = CheckFeedback(self)
                context = QgsProcessingContext()
                context.setProject(QgsProject.instance())
                context.setFeedback(feedback)
                alg = topo.GapsOverlaps()
                alg.initAlgorithm()
                alg.processAlgorithm({'INPUT1': eas, 'INPUT2': bldg_inputs,
                                      'RUN_MODE': mode, 'EXPORT_MBI': False}, context, feedback)
                if feedback.isCanceled():
                    raise _Cancelled()
                store = context.temporaryLayerStore()
                parts = []
                for ref, details in context.layersToLoadOnCompletion().items():
                    layer = store.mapLayer(ref)
                    kind = {"Overlaps": "Overlap", "Gaps": "Gap"}.get(details.name)
                    if layer is not None and kind:
                        parts.append((layer, kind))
                result = self._combine_results(parts, topo)
                elapsed = str(datetime.now() - start).split(".")[0]
                if result is None:
                    self.log(f"No gaps or overlaps found ({elapsed}).")
                else:
                    QgsProject.instance().addMapLayer(result)
                    self.log(f"Added layer '{result.name()}' ({result.featureCount():,} row(s), {elapsed}). "
                             "It is a temporary layer — right-click > Make Permanent to keep it.")
            except _Cancelled:
                self.log("Topology check cancelled.")
                result = None
            except Exception as e:
                self.log(traceback.format_exc().rstrip())
                self.log(f"Topology check FAILED: {e}")
                result = None
            finally:
                self._run_feedback = None
                self.set_busy(False)
                self.update_summary()

            if result is None:
                if not self.cases:
                    self.progress.setValue(0)
                return
            self.mbi_combo.setLayer(result)
            self.load_cases(use_selection=False)

        def _combine_results(self, parts, topo):
            """Overlaps + Gaps layers -> one MBI Results memory layer with 'mbi_type'."""
            parts = [(layer, kind) for layer, kind in parts if layer.featureCount()]
            if not parts:
                return None
            src = parts[0][0]
            out = QgsVectorLayer(f"Polygon?crs={src.crs().authid()}",
                                 f"MBI Results ({datetime.now():%Y%m%d_%H%M%S})", "memory")
            out.dataProvider().addAttributes(src.fields().toList()
                                             + [QgsField(MBI_TYPE, QVariant.String)])
            out.updateFields()
            names = [f.name() for f in out.fields()]
            feats = []
            for layer, kind in parts:
                lnames = layer.fields().names()
                n_cases = set()
                for f in layer.getFeatures():
                    nf = QgsFeature(out.fields())
                    nf.setGeometry(f.geometry())
                    nf.setAttributes([kind if n == MBI_TYPE else (f[n] if n in lnames else None)
                                      for n in names])
                    feats.append(nf)
                    n_cases.add(f[MBI_CASE_UUID])
                self.log(f"  {kind}s: {len(n_cases):,} case(s), {layer.featureCount():,} row(s)")
            out.dataProvider().addFeatures(feats)
            out.updateExtents()
            style = getattr(topo, "STYLE_PATH", "")
            if style and os.path.exists(style):
                out.loadNamedStyle(style)
            return out

        def load_cases(self, use_selection=True):
            if self.running or not self.validate_inputs():
                return
            mbi = self.mbi_combo.currentLayer()
            eas = [l for l in self.ea_layers() if _pick_field(l, EA_GEOCODE_FIELDS)]
            bldg = self.bldg_combo.currentLayer()
            bldg_field = self.bldg_field()

            only_fids = None
            if use_selection and self.selected_only.isChecked():
                only_fids = mbi.selectedFeatureIds()
                if not only_fids:
                    QtWidgets.QMessageBox.warning(
                        self, DIALOG_TITLE, f"No features are selected in '{mbi.name()}'.")
                    return

            previous = ({u: c.assigned_ea for u, c in self.cases.items() if c.assigned_ea}
                        if mbi.id() == self.mbi_layer_id else {})
            field = self.coverage_field()
            preload = field if (self.preload_check.isChecked()
                                and mbi.fields().indexOf(field) >= 0) else None

            self.set_busy(True)
            self.log("=" * 50)
            self.log(f"Loading MBI cases from '{mbi.name()}'"
                     + (f" ({len(only_fids):,} selected feature(s))" if only_fids else "") + "…")
            try:
                cases = load_mbi_cases(
                    mbi, infer_mbi_type(mbi.name()), only_fids, preload,
                    tick=lambda d, t: self.tick(d, t * 2 if bldg else t, "Reading MBI cases…"))
                involved = {c for case in cases.values() for c in case.candidates}
                self.log(f"  {len(cases):,} case(s) involving {len(involved):,} EA(s)")
                if preload:
                    resumed = sum(1 for c in cases.values() if c.assigned_ea)
                    self.log(f"  Resumed {resumed:,} saved choice(s) from '{preload}'")
                for uuid, code in previous.items():
                    if uuid in cases and code in cases[uuid].candidates:
                        cases[uuid].assigned_ea = code

                lookup = {}
                if eas:
                    self.log(f"Reading {len(eas)} EA layer(s): " + ", ".join(l.name() for l in eas))
                    lookup, dupes = read_ea_lookup(eas)
                    for case in cases.values():
                        for cand in case.candidates.values():
                            info = lookup.get(cand.code)
                            if info and not cand.name:
                                cand.name = info["name"]
                        case.finish()
                    per_layer = {}
                    for code in involved:
                        if code in lookup:
                            lid = lookup[code]["layer_id"]
                            per_layer[lid] = per_layer.get(lid, 0) + 1
                    for layer in eas:
                        self.log(f"  {layer.name()}: {per_layer.get(layer.id(), 0):,} involved EA(s)")
                    missing = sorted(involved - set(lookup))
                    if missing:
                        self.log(f"  {len(missing):,} involved EA(s) not found in the selected layers: "
                                 + ", ".join(missing[:10]) + (" …" if len(missing) > 10 else ""))
                        self.set_hint(self.ea_hint, f"{len(missing):,} involved EA(s) are not in the "
                                                    "selected layers — select the layer(s) they come "
                                                    "from.", "error")
                    else:
                        self.set_hint(self.ea_hint, f"All {len(involved):,} involved EA(s) found in "
                                                    f"the {len(eas)} selected layer(s).", "ok")
                    if dupes:
                        self.log(f"  {dupes:,} duplicate ea_geocode(s) across layers — the first "
                                 "selected layer's EA is used.")
                if bldg is not None:
                    self.count_buildings(cases, mbi, bldg, bldg_field)

                self.cases = cases
                self.ea_lookup = lookup
                self.mbi_layer_id = mbi.id()
                self.populate_tree()
                self.progress.setValue(100)
                self.log("Load finished.")
            except _Cancelled:
                self.log("Cancelled — the previous viewer contents were kept.")
            except Exception as e:
                self.log(traceback.format_exc().rstrip())
                self.log(f"Load FAILED: {e}")
                self.progress.setValue(0)
            finally:
                self.set_busy(False)
                self.update_summary()

        def count_buildings(self, cases, mbi_layer, bldg_layer, gc_field=None):
            """Case: building points inside the MBI area. EA: those of them whose geocode
            (gc_field) matches the EA's ea_geocode, or its geocode when only one involved
            EA has it. Points matching no involved EA are counted as untagged."""
            prj = QgsProject.instance()
            xform = QgsCoordinateTransform(mbi_layer.crs(), bldg_layer.crs(), prj)
            # Every row of a case carries the same MBI geometry, so read one per case.
            first_fid = {case.feature_ids[0]: case for case in cases.values() if case.feature_ids}
            total = max(len(first_fid), 1)
            self.log(f"Counting buildings inside {len(first_fid):,} MBI case(s) from "
                     f"'{bldg_layer.name()}'"
                     + (f", per EA by '{gc_field}'…" if gc_field else " (no geocode field: case totals only)…"))
            request = QgsFeatureRequest().setFilterFids(list(first_fid)).setNoAttributes()
            n_in = n_tagged = 0
            for i, feat in enumerate(mbi_layer.getFeatures(request)):
                if i % 25 == 0:
                    self.tick(total + i, total * 2, f"Counting buildings ({i:,}/{total:,})…")
                case = first_fid[feat.id()]
                case.bldg_live, case.bldg_untagged = 0, 0
                by_key = {}
                if gc_field:
                    for cand in case.candidates.values():
                        cand.bldg = 0
                        by_key.setdefault(_code_key(cand.code), cand)
                    shared = {}
                    for cand in case.candidates.values():
                        k = _code_key(cand.geocode)
                        if k:
                            shared[k] = shared.get(k, 0) + 1
                    for cand in case.candidates.values():
                        k = _code_key(cand.geocode)
                        if k and shared[k] == 1:
                            by_key.setdefault(k, cand)
                if not feat.hasGeometry():
                    continue
                geom = QgsGeometry(feat.geometry())
                geom.transform(xform)
                engine = QgsGeometry.createGeometryEngine(geom.constGet())
                engine.prepareGeometry()
                req = QgsFeatureRequest().setFilterRect(geom.boundingBox())
                if gc_field:
                    req.setSubsetOfAttributes([gc_field], bldg_layer.fields())
                else:
                    req.setNoAttributes()
                for p in bldg_layer.getFeatures(req):
                    if not p.hasGeometry() or not engine.intersects(p.geometry().constGet()):
                        continue
                    case.bldg_live += 1
                    cand = by_key.get(_code_key(p[gc_field])) if gc_field else None
                    if cand is not None:
                        cand.bldg += 1
                        n_tagged += 1
                    else:
                        case.bldg_untagged += 1
                n_in += case.bldg_live
            self.log(f"  {n_in:,} building point(s) inside MBI cases"
                     + (f", {n_tagged:,} matched to an involved EA, {n_in - n_tagged:,} untagged"
                        if gc_field else ""))

        # ──────────────── tree ────────────────
        def populate_tree(self):
            self._painting = True
            self.tree.setSortingEnabled(False)
            self.tree.clear()
            self.case_items = {}
            ordered = sorted(self.cases.values(), key=lambda c: (c.level, -c.bldg_pts))
            for order, case in enumerate(ordered):
                top = TreeItem()
                top.setData(COL_ID, ROLE_ORDER, order)
                top.setText(COL_ID, f"{case.mbi_type or 'MBI'} · {case.short_id}")
                top.setToolTip(COL_ID, case.case_uuid)
                top.setData(COL_ID, ROLE_UUID, case.case_uuid)
                top.setText(COL_LEVEL, case.level_label)
                top.setToolTip(COL_LEVEL, case.level)
                paint(top, COL_LEVEL, LEVEL_COLOURS.get(case.level[:2]))
                top.setText(COL_BGY, case.barangays)
                top.setToolTip(COL_BGY, case.barangays)
                n = len(case.candidates)
                top.setText(COL_NAME, f"{n} EA{'s' if n != 1 else ''} involved")
                top.setData(COL_BLDG, Qt.DisplayRole, case.bldg_pts)
                top.setTextAlignment(COL_BLDG, Qt.AlignRight | Qt.AlignVCenter)
                tip = f"{case.bldg_pts:,} building point(s) inside this {case.mbi_type.lower() or 'MBI'}"
                if case.bldg_live is not None:
                    if case.bldg_untagged:
                        tip += f"\n{case.bldg_untagged:,} not tagged to any involved EA"
                    if case.bldg_live != case.num_bldg_pts:
                        tip += f"\n(num_bldg_pts in the MBI layer: {case.num_bldg_pts:,})"
                top.setToolTip(COL_BLDG, tip)
                if case.bldg_pts:
                    font = top.font(COL_BLDG)
                    font.setBold(True)
                    top.setFont(COL_BLDG, font)
                font = top.font(COL_ID)
                font.setBold(True)
                top.setFont(COL_ID, font)
                top.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)

                for c_order, cand in enumerate(case.candidates.values()):
                    child = TreeItem(top)
                    child.setData(COL_ID, ROLE_ORDER, c_order)
                    child.setText(COL_ID, cand.code)
                    child.setData(COL_ID, ROLE_UUID, case.case_uuid)
                    child.setData(COL_ID, ROLE_EA, cand.code)
                    child.setText(COL_BGY, ", ".join(p for p in (cand.barangay, cand.city_mun) if p))
                    child.setText(COL_NAME, cand.name)
                    if self.ea_lookup and cand.code not in self.ea_lookup:
                        child.setToolTip(COL_ID, "Not found in the selected EA layers")
                        child.setForeground(COL_ID, QBrush(QColor("#c62828")))
                    if cand.bldg is not None:
                        child.setData(COL_BLDG, Qt.DisplayRole, cand.bldg)
                        child.setToolTip(COL_BLDG, f"{cand.bldg:,} building point(s) inside this MBI "
                                                   f"tagged to {cand.code}")
                    child.setTextAlignment(COL_BLDG, Qt.AlignRight | Qt.AlignVCenter)
                    child.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
                    child.setCheckState(COL_ROLE, Qt.Unchecked)
                self.tree.addTopLevelItem(top)
                self.case_items[case.case_uuid] = top
                self.paint_case(case)

            self.tree.setSortingEnabled(True)
            self.tree.sortByColumn(COL_LEVEL, Qt.AscendingOrder)
            self.tree.expandAll()
            for col in range(len(TREE_COLS)):
                self.tree.resizeColumnToContents(col)
            self.tree.setColumnWidth(COL_BGY, min(self.tree.columnWidth(COL_BGY), 220))
            self._painting = False
            self._fill_show_combo({c.level for c in self.cases.values() if c.level})
            self.apply_filter()

        def paint_case(self, case):
            top = self.case_items.get(case.case_uuid)
            if top is None:
                return
            was_painting, self._painting = self._painting, True
            gap = case.mbi_type.lower() == "gap"
            on_text = "✔ Absorbs gap" if gap else "✔ Keeps overlap" if case.mbi_type else "✔ Covers MBI"
            off_text = "Not extended" if gap else "Releases (clip)" if case.mbi_type else "Releases"
            for i in range(top.childCount()):
                child = top.child(i)
                code = child.data(COL_ID, ROLE_EA)
                if case.assigned_ea == code:
                    child.setCheckState(COL_ROLE, Qt.Checked)
                    child.setText(COL_ROLE, on_text)
                    paint(child, COL_ROLE, COLOUR_COVERED)
                elif case.assigned_ea:
                    child.setCheckState(COL_ROLE, Qt.Unchecked)
                    child.setText(COL_ROLE, off_text)
                    paint(child, COL_ROLE, COLOUR_RELEASED)
                else:
                    child.setCheckState(COL_ROLE, Qt.Unchecked)
                    child.setText(COL_ROLE, "Candidate")
                    paint(child, COL_ROLE, None)
            if case.assigned_ea:
                cand = case.candidates.get(case.assigned_ea)
                top.setText(COL_ROLE, f"Covered by {cand.label if cand else case.assigned_ea}")
                paint(top, COL_ROLE, COLOUR_COVERED)
            else:
                top.setText(COL_ROLE, "Pending")
                paint(top, COL_ROLE, COLOUR_PENDING)
            self._painting = was_painting

        def on_item_changed(self, item, column):
            if self._painting or column != COL_ROLE or item.parent() is None:
                return
            case = self.cases.get(item.data(COL_ID, ROLE_UUID))
            if case is None:
                return
            code = item.data(COL_ID, ROLE_EA)
            if item.checkState(COL_ROLE) == Qt.Checked:
                self.set_assignment(case, code)
            elif case.assigned_ea == code:
                self.set_assignment(case, "")

        def set_assignment(self, case, code, quiet=False):
            if case.assigned_ea == code:
                return
            case.assigned_ea = code
            self.paint_case(case)
            if not quiet:
                self.log(f"{case.mbi_type or 'MBI'} {case.short_id} → "
                         + (f"covered by {case.candidates[code].label}" if code else "pending"))
                self.filter_item(case)
                self.update_summary()

        def reset_choices(self):
            if not any(c.assigned_ea for c in self.cases.values()):
                return
            if QtWidgets.QMessageBox.question(
                    self, DIALOG_TITLE, "Clear every EA tick in the preview?\n"
                    "Nothing in the layer changes until you Apply Coverage.",
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel) != QtWidgets.QMessageBox.Yes:
                return
            for case in self.cases.values():
                self.set_assignment(case, "", quiet=True)
            self.log("All choices cleared.")
            self.apply_filter()

        # ──────────────── filter ────────────────
        def _case_visible(self, case):
            mode = self.show_combo.currentData()
            if mode == "pending" and case.assigned_ea:
                return False
            if mode == "covered" and not case.assigned_ea:
                return False
            if mode and mode.startswith("type:") and case.mbi_type != mode[5:]:
                return False
            if mode and mode.startswith("level:") and case.level != mode[6:]:
                return False
            text = self.search_edit.text().strip().lower()
            return not text or text in case.search_text

        def filter_item(self, case):
            top = self.case_items.get(case.case_uuid)
            if top is not None:
                top.setHidden(not self._case_visible(case))

        def apply_filter(self, *_args):
            for case in self.cases.values():
                self.filter_item(case)
            self.update_summary()

        # ──────────────── selection / zoom / context menu ────────────────
        def _selection(self, items=None):
            """-> (MBI fids, {ea_layer_id: fids}) for the given (or selected) tree items."""
            mbi_fids, ea_fids = set(), {}
            for item in items if items is not None else self.tree.selectedItems():
                case = self.cases.get(item.data(COL_ID, ROLE_UUID))
                if case is None:
                    continue
                code = item.data(COL_ID, ROLE_EA)
                if code:
                    cand = case.candidates.get(code)
                    mbi_fids.update(cand.fids if cand else case.feature_ids)
                    info = self.ea_lookup.get(code)
                    if info:
                        ea_fids.setdefault(info["layer_id"], set()).add(info["fid"])
                else:
                    mbi_fids.update(case.feature_ids)
            return mbi_fids, ea_fids

        def on_selection_changed(self):
            self.update_buttons()
            if self.running:
                return
            mbi_fids, ea_fids = self._selection()
            if self.mbi_layer is not None:
                self.mbi_layer.selectByIds(list(mbi_fids))
            prj = QgsProject.instance()
            for layer_id in {i["layer_id"] for i in self.ea_lookup.values()}:
                layer = prj.mapLayer(layer_id)
                if layer is not None:
                    layer.selectByIds(list(ea_fids.get(layer_id, ())))

        def on_double_click(self, item, column):
            if column == COL_ROLE and item.parent() is not None:
                return
            self.zoom_to_items([item])

        def zoom_to_selection(self):
            self.zoom_to_items(self.tree.selectedItems())

        def zoom_to_items(self, items):
            if self.iface is None:
                return
            mbi_fids, ea_fids = self._selection(items)
            prj = QgsProject.instance()
            targets = [(self.mbi_layer, mbi_fids)]
            targets += [(prj.mapLayer(lid), fids) for lid, fids in ea_fids.items()]
            canvas = self.iface.mapCanvas()
            dest = canvas.mapSettings().destinationCrs()
            extent, flashes = None, []
            for layer, fids in targets:
                if layer is None or not fids:
                    continue
                xform = QgsCoordinateTransform(layer.crs(), dest, prj)
                request = QgsFeatureRequest().setFilterFids(list(fids)).setNoAttributes()
                for f in layer.getFeatures(request):
                    if not f.hasGeometry():
                        continue
                    box = xform.transformBoundingBox(f.geometry().boundingBox())
                    if extent is None:
                        extent = box
                    else:
                        extent.combineExtentWith(box)
                flashes.append((layer, list(fids)))
            if extent is None:
                return
            extent.scale(1.3)
            canvas.setExtent(extent)
            canvas.refresh()
            for layer, fids in flashes:
                canvas.flashFeatureIds(layer, fids)

        def show_context_menu(self, pos):
            item = self.tree.itemAt(pos)
            menu = QtWidgets.QMenu(self)
            if item is not None:
                case = self.cases.get(item.data(COL_ID, ROLE_UUID))
                code = item.data(COL_ID, ROLE_EA)
                if case is not None and code:
                    act = menu.addAction(f"Set {code} as covering EA")
                    act.setEnabled(case.assigned_ea != code)
                    act.triggered.connect(lambda: self.set_assignment(case, code))
                if case is not None:
                    act = menu.addAction("Mark case as pending")
                    act.setEnabled(bool(case.assigned_ea))
                    act.triggered.connect(lambda: self.set_assignment(case, ""))
                    menu.addSeparator()
                    menu.addAction(QgsApplication.getThemeIcon("/mActionZoomToSelected.svg"),
                                   "Zoom to this", lambda: self.zoom_to_items([item]))
                    menu.addAction("Copy case ID",
                                   lambda: QtWidgets.QApplication.clipboard().setText(case.case_uuid))
                menu.addSeparator()
            menu.addAction("Expand all", self.tree.expandAll)
            menu.addAction("Collapse all", self.tree.collapseAll)
            menu.exec_(self.tree.viewport().mapToGlobal(pos))

        # ──────────────── auto-assign ────────────────
        def auto_assign(self):
            single = by_bldg = left = 0
            for case in self.cases.values():
                if case.assigned_ea or not case.candidates:
                    continue
                cands = list(case.candidates.values())
                if len(cands) == 1:
                    self.set_assignment(case, cands[0].code, quiet=True)
                    single += 1
                elif all(c.bldg is not None for c in cands):
                    most = max(c.bldg for c in cands)
                    leaders = [c for c in cands if c.bldg == most]
                    if most > 0 and len(leaders) == 1:
                        self.set_assignment(case, leaders[0].code, quiet=True)
                        by_bldg += 1
                    else:
                        left += 1
                else:
                    left += 1
            self.log(f"Auto-assign: {single:,} single-EA case(s), {by_bldg:,} to the EA owning most "
                     "of the buildings inside the MBI"
                     + (f", {left:,} left pending (no tagged buildings, a tie, or no building "
                        "counts)" if left else ""))
            self.apply_filter()

        # ──────────────── apply ────────────────
        def _case_status(self, case):
            return STATUS_COVERED if case.assigned_ea else STATUS_NOT_COVERED

        def _needs_apply(self, layer):
            """True when the viewer holds choices the layer does not have yet (or the layer
            is missing one of the coverage fields)."""
            if any(c.assigned_ea != c.saved_ea for c in self.cases.values()):
                return True
            return any(layer.fields().indexOf(n) < 0
                       for n in (self.coverage_field(), ROLE_FIELD, self.status_field()))

        def apply_coverage(self):
            layer = self.mbi_layer
            if self.running:
                return
            if layer is None:
                QtWidgets.QMessageBox.warning(
                    self, DIALOG_TITLE, "The MBI layer is no longer in the project — load again.")
                return
            covered = sum(1 for c in self.cases.values() if c.assigned_ea)
            pending = len(self.cases) - covered
            msg = (f"Write '{self.coverage_field()}', '{ROLE_FIELD}' and '{self.status_field()}' "
                   f"for all {len(self.cases):,} case(s) into '{layer.name()}'?\n\n"
                   f"{covered:,} case(s) → {STATUS_COVERED}\n{pending:,} case(s) → {STATUS_NOT_COVERED}")
            if QtWidgets.QMessageBox.question(
                    self, DIALOG_TITLE, msg,
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel) != QtWidgets.QMessageBox.Yes:
                return
            self._write_coverage(layer)

        def _write_coverage(self, layer):
            """Write coverage field, role and status for every loaded case (unsaved, undoable
            edits). Returns True on success."""
            field, status_field = self.coverage_field(), self.status_field()
            self.set_busy(True)
            self.log("-" * 50)
            self.log(f"Applying coverage to '{layer.name()}'…")
            try:
                if not layer.isEditable() and not layer.startEditing():
                    raise RuntimeError(f"Cannot edit '{layer.name()}'")
                for name in (field, ROLE_FIELD, status_field):
                    if layer.fields().indexOf(name) < 0:
                        layer.addAttribute(QgsField(name, QVariant.String))
                        self.log(f"  Added field '{name}'")
                layer.updateFields()
                f_idx = layer.fields().indexOf(field)
                r_idx = layer.fields().indexOf(ROLE_FIELD)
                s_idx = layer.fields().indexOf(status_field)

                layer.beginEditCommand(f"MBI EA coverage for {len(self.cases)} case(s)")
                rows = 0
                cases = list(self.cases.values())
                for i, case in enumerate(cases):
                    role_by_fid = {}
                    for cand in case.candidates.values():
                        role = (None if not case.assigned_ea else
                                ROLE_COVERS if cand.code == case.assigned_ea else ROLE_RELEASES)
                        for fid in cand.fids:
                            role_by_fid[fid] = role
                    for fid in case.feature_ids:
                        layer.changeAttributeValues(fid, {
                            f_idx: case.assigned_ea or None,
                            r_idx: role_by_fid.get(fid),
                            s_idx: self._case_status(case)})
                        rows += 1
                    if i % 200 == 0:
                        self.progress.setValue(int(100 * i / max(len(cases), 1)))
                        QtWidgets.QApplication.processEvents()
                layer.endEditCommand()
                for case in cases:
                    case.saved_ea = case.assigned_ea
                layer.triggerRepaint()
                self.progress.setValue(100)
                covered = sum(1 for c in cases if c.assigned_ea)
                self.log(f"  Updated {rows:,} row(s) across {len(cases):,} case(s): "
                         f"{covered:,} {STATUS_COVERED}, {len(cases) - covered:,} {STATUS_NOT_COVERED}")
                self.log(f"  Changes are UNSAVED edits in '{layer.name()}'. "
                         "Save: Toggle Editing > Save Edits. Undo: Edit > Undo.")
                return True
            except Exception as e:
                if layer.isEditable():
                    layer.destroyEditCommand()
                self.log(traceback.format_exc().rstrip())
                self.log(f"Apply FAILED: {e}")
                return False
            finally:
                self.set_busy(False)
                self.update_summary()

        # ──────────────── export ────────────────
        def export_results(self):
            """Write the processed MBI layer to a GPKG and the decisions to a CSV."""
            if self.running:
                return
            layer = self.mbi_layer
            if layer is None or not self.cases:
                QtWidgets.QMessageBox.warning(
                    self, DIALOG_TITLE, "Load the MBI cases first — nothing to export.")
                return
            folder = self.folder_widget.filePath().strip()
            if not folder or not os.path.isdir(folder):
                folder = QtWidgets.QFileDialog.getExistingDirectory(
                    self, "Choose the export folder", folder if os.path.isdir(folder) else "")
                if not folder:
                    return
                self.folder_widget.setFilePath(folder)

            pending = sum(1 for c in self.cases.values() if not c.assigned_ea)
            if self._needs_apply(layer):
                msg = ("The choices in the viewer are not in the MBI layer yet.\n"
                       "Apply Coverage first, then export?")
                if pending:
                    msg += (f"\n\n{pending:,} case(s) are still pending and will be exported as "
                            f"{STATUS_NOT_COVERED}.")
                if QtWidgets.QMessageBox.question(
                        self, DIALOG_TITLE, msg,
                        QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel) != QtWidgets.QMessageBox.Yes:
                    return
                if not self._write_coverage(layer):
                    return
            elif pending and QtWidgets.QMessageBox.question(
                    self, DIALOG_TITLE,
                    f"{pending:,} case(s) are still pending and will be exported as "
                    f"{STATUS_NOT_COVERED}. Export anyway?",
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel) != QtWidgets.QMessageBox.Yes:
                return

            stamp = f"{datetime.now():%Y%m%d_%H%M%S}"
            gpkg = os.path.join(folder, f"{EXPORT_LAYER_NAME}_{stamp}.gpkg")
            csv_path = os.path.join(folder, f"{EXPORT_LAYER_NAME}_{stamp}.csv")
            self.set_busy(True)
            self.log("-" * 50)
            self.log(f"Exporting to {folder}…")
            try:
                self.export_gpkg(layer, gpkg)
                self.progress.setValue(80)
                self.export_csv(csv_path)
                self.progress.setValue(100)
                self.log("Export finished.")
            except Exception as e:
                self.log(traceback.format_exc().rstrip())
                self.log(f"Export FAILED: {e}")
            finally:
                self.set_busy(False)
                self.update_summary()

        def export_gpkg(self, layer, path):
            """The whole MBI layer, including unsaved edits and the coverage fields."""
            options = QgsVectorFileWriter.SaveVectorOptions()
            options.driverName = "GPKG"
            options.layerName = EXPORT_LAYER_NAME
            options.fileEncoding = "UTF-8"
            res = QgsVectorFileWriter.writeAsVectorFormatV3(
                layer, path, QgsProject.instance().transformContext(), options)
            if res[0] != QgsVectorFileWriter.NoError:
                raise RuntimeError(f"GPKG export failed: {res[1]}")
            self.log(f"  GPKG written: {path} ({layer.featureCount():,} row(s))")

        def export_csv(self, path):
            field, status_field = self.coverage_field(), self.status_field()
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["case_uuid", "mbi_type", "mbi_level", "num_bldg_pts", "ea_code",
                            "ea_name", "barangay", "city_mun", "ea_bldg_in_mbi", ROLE_FIELD,
                            field, status_field])
                for case in sorted(self.cases.values(), key=lambda c: (c.level, c.case_uuid)):
                    for cand in case.candidates.values():
                        role = ("" if not case.assigned_ea else
                                ROLE_COVERS if cand.code == case.assigned_ea else ROLE_RELEASES)
                        w.writerow([case.case_uuid, case.mbi_type, case.level, case.bldg_pts,
                                    cand.code, cand.name, cand.barangay, cand.city_mun,
                                    "" if cand.bldg is None else cand.bldg, role,
                                    case.assigned_ea, self._case_status(case)])
            self.log(f"  CSV written: {path}")

    return CoverageDialog


# ─────────────────── entry point ───────────────────

def show_dialog():
    """Open the MBI EA Coverage window (non-modal, so the map stays usable)."""
    from qgis.PyQt.QtWidgets import QDialog

    qgis_iface = globals().get("iface")
    if qgis_iface is None:
        try:
            from qgis.utils import iface as qgis_iface
        except ImportError:
            qgis_iface = None
    main = qgis_iface.mainWindow() if qgis_iface is not None else None

    if main is not None:
        for old in main.findChildren(QDialog, DIALOG_NAME):
            old.close()

    dialog = make_dialog_class()(qgis_iface, main)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog


if "qgis" in sys.modules and not os.environ.get("MBI_EA_SELECTOR_NO_DIALOG"):
    _DIALOG = show_dialog()
elif "qgis" not in sys.modules:
    print("Run this script inside QGIS: GMD CO Processing > EA Tools > MBI EA Selector.")
