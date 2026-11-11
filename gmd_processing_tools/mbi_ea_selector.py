r"""MBI EA Coverage — choose which involved EA covers each MBI gap / overlap.

When the EA MBI Checker finds gaps or overlaps, every discrepancy
(case_uuid) involves two or more EAs — possibly from different EA layers. This window lists
each MBI case with all of its involved EAs, so you can tick the EA that covers the MBI area:

  • Overlap — the ticked EA keeps the overlap; the other EAs release (clip) it.
  • Gap     — the ticked EA absorbs the gap; the other EAs are not extended.

Workflow:
  Step 1  Select every EA layer the involved areas come from (one or more). EAs are matched
          by 'ea_geocode' across all selected layers.
  Step 2  Optionally pick a building point layer (and its geocode field) to count the
          buildings inside each MBI, and per involved EA the ones tagged to it.
  Step 3  Pick the MBI Results layer — the 'ppp_mbi' layer of the MBI Barangay Selector
          (needs 'case_uuid') — and click Load MBI Cases. No MBI check is run here (the
          Run Check box is hidden): the MBIs were detected in the Barangay Selector.
          A row without a barangay name gets it by its geocode / bgy_geocode from the other
          MBI rows, else from the ticked EA layers.
  Step 4  Tick the Role / Status box of the covering EA in each case.
          Shared overlaps: when an overlap runs across several EAs (and their layers are
          selected), more than one EA can be ticked — each ticked EA keeps only its own part
          of the overlap (the MBI clipped to that EA, minus what an EA ticked before it
          keeps). The case is Partly Covered until the ticked EAs' parts fill the MBI.
          Auto-Assign Pending: single-EA cases go to that EA, multi-EA cases go to the EA
          owning most of the buildings inside the MBI (needs Step 2); shared overlaps go to
          every EA with tagged buildings, most first.
  Step 5  Apply Coverage writes 'assigned_ea' (covering EA codes, comma-separated),
          'ea_role' (Covers / Releases, per row) and 'coverage_status' (1_Covered /
          2_Not Covered / 3_Partially Covered, per case) into the MBI layer as unsaved,
          undoable edits, and adds the temporary layer 'ppp_mbi_ea_clipped' (same fields as
          ppp_mbi): the covering barangay's row becomes one row per ticked EA, clipped to that
          EA (assigned_ea = that EA, 1_Covered); not-covered rows and leftovers are kept with
          2_Not Covered. An old 'MBI Coverage Parts' layer is
          removed. This tool is the only one that writes the coverage status.
  Step 6  Export GPKG + CSV writes the processed MBI layer, the coverage parts and a decision
          log CSV into the Export folder (applying any pending choices first).

MBIs from the MBI Barangay Selector ('ppp_mbi', has 'assigned_bgy'):
  Picking such a layer ticks "Choose EAs inside the covering barangay". The covering barangay
  chosen there decides which EAs can cover the MBI:
    • Each MBI is listed ONCE PER COVERING BARANGAY (a shared overlap is first clipped to each
      barangay — the MBI ∩ that barangay's EAs, minus what a barangay chosen before it keeps).
    • The EAs offered are only that barangay's EAs (from the ticked EA layers, matched on the
      EA layer's 'geocode', else the start of 'ea_geocode'): for an overlap / disputed area the
      EAs holding part of it (several can share it out), for a gap the EAs within the search
      distance (default 5 m); none that close -> the 3 nearest EAs of the barangay.
    • MBIs with no covering barangay yet, and no-longer-detected MBIs, are skipped.
  Apply writes 'assigned_ea' and 'coverage_status' on the covering barangay's row(s) only;
  rows of barangays not chosen get no EA and 2_Not Covered. The Barangay Selector's
  'assigned_bgy' is never changed (no 'ea_role' in this mode).
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
STATUS_PARTIAL = "3_Partially Covered"   # split overlap: part of it has no covering EA yet
EXPORT_LAYER_NAME = "mbi_ea_coverage"
ROLE_COVERS = "Covers"
ROLE_RELEASES = "Releases"

# Split overlaps: each covering EA keeps only its own part of the MBI (MBI clipped to the EA)
PARTS_LAYER_NAME = "MBI Coverage Parts"
PARTS_EXPORT_NAME = "mbi_ea_coverage_parts"
CLIPPED_SUFFIX = "ea_clipped"   # Apply adds '<ppp>_mbi_ea_clipped': the MBIs clipped to each ticked EA
PART_KEPT = "Kept"
PART_UNASSIGNED = "Unassigned"
# MBI area left over after the chosen EAs' parts that still counts as covered (geometry
# rounding along EA boundaries, not a real leftover): m², or this share of the MBI.
SLIVER_M2 = 0.5
SLIVER_SHARE = 0.001

# ===== MBI layers from the MBI Barangay Selector ('ppp_mbi', one row per involved barangay) =====
# The covering barangay(s) chosen there are read from BGY_COVERAGE_FIELD; the EAs offered for a
# case are only the EAs of that barangay that cover / border the MBI.
BGY_COVERAGE_FIELD = "assigned_bgy"        # covering barangay geocode(s), comma-separated
# The coverage status is written by this tool only (the Barangay Selector writes none), so
# barangay mode uses the same default status field.
BGY_MODE_STATUS_FIELD = STATUS_FIELD
MBI_DETECTION = "detection"
DETECT_GONE = "No longer detected"
GAP_SEARCH_M = 5.0      # default: EAs of the covering barangay within this distance of a gap
NEAREST_FALLBACK = 3    # no EA that close: list this many of the barangay's nearest EAs

# ===== EA layer fields =====
EA_GEOCODE_FIELDS = ["ea_geocode", "geocode"]
EA_BGY_FIELDS = ["geocode", "bgy_geocode"]   # barangay geocode of an EA (else: ea_geocode prefix)
# Barangay name lookup. A row without a barangay name falls back to the name found, by its
# geocode / bgy_geocode, on other rows of the MBI layer, then in the EA layers.
BGY_CODE_FIELDS = ["geocode", "bgy_geocode", "bgy_geo"]
BGY_NAME_FIELDS = ["barangay", "bgy_name", "brgy_name", "barangay_name"]
BGY_CITY_FIELDS = ["city_mun", "city_municipality", "citymun", "mun_name"]


def _first_text(feature, candidates):
    """First non-empty value among the candidate fields (case-insensitive), else ''."""
    names = {f.name().lower(): f.name() for f in feature.fields()}
    for c in candidates:
        real = names.get(c.lower())
        if real:
            v = _text(feature, real)
            if v and v.upper() not in ("NULL", "UNKNOWN"):
                return v
    return ""
EA_NAME_FIELDS = ["name", "ean", "ea_name"]

# ===== Building point fields =====
BLDG_GEOCODE_FIELDS = ["ea_geocode", "geocode"]   # auto-picked in this order

# Status colours (table cells; text is forced dark so it stays readable in dark themes)
# Two families that never look alike: WARM / green = decision status, COOL = MBI level.
# All soft pastels (easy on the eyes; text stays dark on them).
COLOUR_PENDING = "#ffe0b2"     # soft peach: still to decide
COLOUR_COVERED = "#c8e6c9"     # soft green: chosen / covered
COLOUR_RELEASED = "#eceff1"    # pale grey: gives it up / not extended
COLOUR_PARTIAL = "#fff3c4"     # soft cream-yellow: partly covered
# MBI level: cool lavender -> blue -> aqua -> slate (darker = more severe), never a status colour.
LEVEL_COLOURS = {
    "1_": "#d9c8ee",   # Inter-Region            lavender
    "2_": "#d3d6f3",   # Inter-Province          periwinkle
    "3_": "#cfe3f6",   # Inter-City/Municipality sky blue
    "4_": "#d3edf0",   # Inter-Barangay          pale aqua
    "5_": "#e4e8eb",   # Within-Barangay         slate
}
LEVEL_NAMES = {"1_": "Inter-Region", "2_": "Inter-Province", "3_": "Inter-City/Mun",
               "4_": "Inter-Barangay", "5_": "Within-Barangay"}

# Role / Status column: plain words saying what a tick does.
TEXT_CHOOSE = "☐ Tick if this {unit} covers it"


def role_texts(mbi_type, unit):
    """(ticked text, not-chosen text, ticked tooltip, not-chosen tooltip) for one MBI type."""
    kind = (mbi_type or "").lower()
    if kind == "gap":
        return ("✔ Covers — its boundary is extended over the gap",
                "Not extended — stays as it is",
                f"\nThis {unit} absorbs the whole gap: its boundary is extended to fill it.",
                f"\nAnother {unit} absorbs the gap; this one is not changed.")
    if kind == "overlap":
        return ("✔ Covers — keeps the overlap",
                "✖ Gives up the overlap — cut out of it",
                f"\nThis {unit} keeps the overlapping area.",
                f"\nAnother {unit} keeps the overlap, so it is cut out of this {unit}.")
    return ("✔ Covers — takes this area",
            "✖ Gives up this area",
            f"\nThis {unit} takes the whole MBI area.",
            f"\nAnother {unit} takes the MBI area, so this {unit} gives it up.")


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


BGY_DIGITS = 8   # 14-digit PSA geocode = 8-digit barangay + 6-digit EA number ('000000' = barangay)


def _bgy_key(value):
    """Barangay part of a geocode, for matching a barangay with its EAs.

    14-digit codes (13 when the leading zero was lost): the first 8 digits — so barangay
    '02709007000000' and its EAs '02709007001000', '02709007002000' share the key '02709007'.
    Other codes: _code_key (the EA code then has to start with the barangay code)."""
    if value is None or (hasattr(value, "isNull") and value.isNull()):
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    s = str(value).strip()
    if s.isdigit() and len(s) in (13, 14):
        return s.zfill(14)[:BGY_DIGITS]
    return _code_key(s)


def _pick_field(layer, candidates):
    """Return the first matching field name (case-insensitive) or None."""
    names = {f.name().lower(): f.name() for f in layer.fields()}
    for c in candidates:
        if c.lower() in names:
            return names[c.lower()]
    return None


def _split_codes(text):
    """'a, b,c' -> ['a', 'b', 'c'] (the coverage field holds every covering EA)."""
    return [c.strip() for c in str(text or "").split(",") if c.strip()]


def _norm_type(value):
    """'1_Gap' / 'gap' -> 'Gap', '2_Overlap' -> 'Overlap', '3_Disputed' -> 'Disputed'."""
    v = str(value or "").strip()
    low = v.lower()
    if "disput" in low:
        return "Disputed"
    if "overlap" in low:
        return "Overlap"
    if "gap" in low:
        return "Gap"
    return v


def is_bgy_mbi_layer(layer):
    """True for an MBI layer the MBI Barangay Selector assigned covering barangays to."""
    return layer is not None and layer.fields().indexOf(BGY_COVERAGE_FIELD) >= 0 \
        and layer.fields().indexOf(MBI_CASE_UUID) >= 0


def _polygonal(geom):
    """Polygon part of a geometry (an intersection can add lines / points), or None."""
    from qgis.core import QgsGeometry, QgsWkbTypes
    if geom is None or geom.isNull() or geom.isEmpty():
        return None
    if geom.type() == QgsWkbTypes.PolygonGeometry:
        return geom
    polys = [QgsGeometry(p) for p in geom.asGeometryCollection()
             if p.type() == QgsWkbTypes.PolygonGeometry and not p.isEmpty()]
    if not polys:
        return None
    return QgsGeometry.unaryUnion(polys) if len(polys) > 1 else polys[0]


def _area(geom, da=None):
    """Area in m² (QgsDistanceArea with an ellipsoid), 0 for no geometry."""
    if geom is None or geom.isNull() or geom.isEmpty():
        return 0.0
    return da.measureArea(geom) if da is not None else geom.area()


def _pct(share):
    """0.4 -> '40%', never rounding a part to a misleading 0% / 100%."""
    if 0 < share < 0.01:
        return "<1%"
    if 0.99 < share < 1:
        return ">99%"
    return f"{100 * share:.0f}%"


class EACandidate:
    """One EA involved in an MBI case."""

    __slots__ = ("code", "geocode", "name", "barangay", "city_mun", "fids", "bldg",
                 "part", "part_area", "kept", "kept_area", "kept_bldg", "released", "dist")

    def __init__(self, code):
        self.code = code
        self.geocode = ""
        self.name = ""
        self.barangay = ""
        self.city_mun = ""
        self.dist = None    # barangay mode: distance from the MBI in m (0 = touches / covers it)
        self.fids = []      # MBI rows where this EA is "this area"
        self.bldg = None    # buildings inside the MBI tagged (by geocode) to this EA
        self.part = None        # MBI ∩ EA (MBI layer CRS): what this EA could keep
        self.part_area = 0.0    # m²
        self.kept = None        # what this EA keeps with the current choices
        self.kept_area = 0.0
        self.kept_bldg = None   # building points inside 'kept'
        self.released = 0.0     # share of 'part' kept by other (covering) EAs

    @property
    def label(self):
        return f"{self.code} ({self.name})" if self.name else self.code


class MBICase:
    """One MBI case_uuid: the discrepancy and all of its involved EAs."""

    def __init__(self, case_uuid, mbi_type=""):
        self.case_uuid = case_uuid
        self.key = case_uuid       # viewer key; barangay mode: '<case_uuid>|<barangay geocode>'
        self.mbi_type = mbi_type
        # Barangay mode (MBI layer from the MBI Barangay Selector): this case is the part of the
        # MBI one covering barangay keeps, and only that barangay's EAs are candidates.
        self.bgy_code = ""         # covering barangay geocode
        self.bgy_name = ""
        self.bgy_city = ""
        self.bgy_codes = []        # every covering barangay of the MBI, in the order chosen
        self.all_fids = []         # every row of the MBI (rows of barangays not chosen included)
        self.mbi_area = 0.0        # m² of the whole MBI (self.area = this barangay's part)
        self.levels = set()
        self.num_bldg_pts = 0      # stored in the MBI layer by the topology checker
        self.bldg_live = None      # recounted from the Building Points layer (if set)
        self.bldg_untagged = 0     # of those, points whose geocode matches no involved EA
        self.feature_ids = []
        self.candidates = {}     # code -> EACandidate (insertion ordered)
        self.assigned = []       # covering EA code(s) in the order chosen, [] = pending
        self.saved = []          # value already stored in the layer
        self._saved_raw = ""
        self.search_text = ""
        self.geom = None         # MBI geometry (MBI layer CRS)
        self.area = 0.0          # m²
        self.split = False       # overlap shared out: each covering EA keeps only its part
        self.no_split_reason = ""   # why several EAs cannot be ticked (logged on Load)
        self.bldg_geoms = None   # building points inside the MBI (MBI CRS), when counted
        self.covered = 0.0       # share of the MBI kept by the covering EA(s)
        self.unassigned = None   # part of the MBI no covering EA keeps yet

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

        if coverage_field and not self._saved_raw:
            self._saved_raw = _text(feat, coverage_field)

    def add_bgy_row(self, feat, coverage_field=None):
        """A row of the covering barangay in a Barangay Selector layer (no EA of its own:
        the candidate EAs are found in the EA layers)."""
        self.feature_ids.append(feat.id())
        level = _text(feat, MBI_LEVEL)
        if level:
            self.levels.add(level)
        mbi_type = _norm_type(_text(feat, MBI_TYPE))
        if mbi_type:
            self.mbi_type = mbi_type
        bldg = _field_value(feat, MBI_NUM_BLDG)
        try:
            self.num_bldg_pts = max(self.num_bldg_pts, int(float(bldg)))
        except (TypeError, ValueError):
            pass
        row_code = _first_text(feat, BGY_CODE_FIELDS)
        if _code_key(row_code) == _code_key(self.bgy_code):
            self.bgy_name = self.bgy_name or _first_text(feat, BGY_NAME_FIELDS)
            self.bgy_city = self.bgy_city or _first_text(feat, BGY_CITY_FIELDS)
        if coverage_field and not self._saved_raw:
            self._saved_raw = _text(feat, coverage_field)

    @property
    def bgy_label(self):
        return f"{self.bgy_name} ({self.bgy_code})" if self.bgy_name else self.bgy_code

    @property
    def bgy_shared(self):
        return len(self.bgy_codes) > 1

    @property
    def status(self):
        """'pending' (no covering EA), 'partial' (split overlap with an uncovered part)
        or 'covered'."""
        if not self.assigned:
            return "pending"
        return "covered" if self.covered >= 1.0 else "partial"

    def resolve(self, da=None):
        """Work out what each EA keeps / releases with the current choices.

        Split overlap: each covering EA keeps its own part of the MBI (MBI ∩ EA), minus
        what an EA chosen before it already keeps; the case is covered once those parts
        fill the whole MBI. Otherwise (gaps, single-EA cases, no EA geometry) the covering
        EA takes the whole MBI."""
        from qgis.core import QgsGeometry
        for c in self.candidates.values():
            c.kept, c.kept_area, c.kept_bldg, c.released = None, 0.0, None, 0.0
        self.covered, self.unassigned = 0.0, self.geom
        if not self.assigned:
            return
        chosen = [self.candidates[code] for code in self.assigned]
        if not self.split:
            cand = chosen[0]
            cand.kept, cand.kept_area = self.geom, self.area
            for c in self.candidates.values():
                if c is not cand:
                    c.released = 1.0
            self.covered, self.unassigned = 1.0, None
        else:
            claimed = None
            for cand in chosen:
                piece = cand.part if claimed is None or cand.part is None \
                    else _polygonal(cand.part.difference(claimed))
                cand.kept, cand.kept_area = piece, _area(piece, da)
                if piece is not None:
                    claimed = piece if claimed is None else QgsGeometry.unaryUnion([claimed, piece])
            left = self.geom if claimed is None else _polygonal(self.geom.difference(claimed))
            left_area = _area(left, da)
            if left_area <= max(SLIVER_M2, SLIVER_SHARE * self.area):
                self.covered, self.unassigned = 1.0, None
            else:
                self.covered = min(max(1.0 - left_area / self.area, 0.0), 0.999) if self.area else 0.0
                self.unassigned = left
            for c in self.candidates.values():
                if c in chosen:
                    continue
                if c.part_area <= SLIVER_M2:
                    c.released = 1.0
                elif claimed is not None:
                    shared = _area(_polygonal(c.part.intersection(claimed)), da)
                    c.released = min(shared / c.part_area, 1.0)

        if self.bldg_geoms is not None:
            # Each building goes to the first covering EA whose kept part holds it.
            engines = []
            for cand in chosen:
                cand.kept_bldg = 0
                if cand.kept is not None:
                    eng = QgsGeometry.createGeometryEngine(cand.kept.constGet())
                    eng.prepareGeometry()
                    engines.append((cand, eng))
            for pt in self.bldg_geoms:
                for cand, eng in engines:
                    if eng.intersects(pt.constGet()):
                        cand.kept_bldg += 1
                        break

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
        parts = [self.case_uuid, self.mbi_type, self.level, self.bgy_code, self.bgy_name, self.bgy_city]
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
                case.all_fids = case.feature_ids
            case.add_feature(feat, coverage_field)
        if tick and i % 200 == 0:
            tick(i, total)
    for case in cases.values():
        case.saved = _split_codes(case._saved_raw)
        case.assigned = [c for c in case.saved if c in case.candidates]
        case.finish()
    return cases


def load_bgy_assigned_cases(mbi_layer, only_fids=None, coverage_field=None, tick=None,
                            bgy_field=BGY_COVERAGE_FIELD):
    """MBI layer of the MBI Barangay Selector -> ({key: MBICase}, stats).

    Each MBI gets ONE CASE PER COVERING BARANGAY (bgy_field, in the order chosen there): the
    case holds that barangay's row(s) and its EA candidates are found later in the EA layers.
    MBIs without a covering barangay (still pending, or no longer detected) are skipped."""
    from qgis.core import QgsFeatureRequest

    request = QgsFeatureRequest().setFlags(QgsFeatureRequest.NoGeometry)
    if only_fids is not None:
        request.setFilterFids(list(only_fids))
    total = len(only_fids) if only_fids is not None else max(mbi_layer.featureCount(), 1)

    rows = {}   # case_uuid -> [feature]
    names = {}  # barangay geocode key -> (name, city) seen on any row (name fallback)
    for i, feat in enumerate(mbi_layer.getFeatures(request)):
        uuid = _text(feat, MBI_CASE_UUID)
        if uuid:
            rows.setdefault(uuid, []).append(feat)
        k = _code_key(_first_text(feat, BGY_CODE_FIELDS))
        name = _first_text(feat, BGY_NAME_FIELDS)
        if k and name and k not in names:
            names[k] = (name, _first_text(feat, BGY_CITY_FIELDS))
        if tick and i % 200 == 0:
            tick(i, total)

    cases = {}
    stats = {"mbis": len(rows), "no_bgy": 0, "gone": 0, "used": 0}
    for uuid, feats in rows.items():
        if any(_text(f, MBI_DETECTION) == DETECT_GONE for f in feats):
            stats["gone"] += 1
            continue
        codes = next((_split_codes(_text(f, bgy_field)) for f in feats if _text(f, bgy_field)), [])
        if not codes:
            stats["no_bgy"] += 1
            continue
        stats["used"] += 1
        all_fids = [f.id() for f in feats]
        for code in codes:
            own = [f for f in feats if _code_key(_first_text(f, BGY_CODE_FIELDS)) == _code_key(code)]
            case = MBICase(uuid)
            case.key = f"{uuid}|{code}"
            case.bgy_code, case.bgy_codes, case.all_fids = code, codes, all_fids
            # One row for the whole MBI (older layers): every covering barangay shares it.
            for f in own or feats:
                case.add_bgy_row(f, coverage_field)
            if not case.bgy_name:   # fallback: the name another row gives this geocode
                case.bgy_name, city = names.get(_code_key(code), ("", ""))
                case.bgy_city = case.bgy_city or city
            cases[case.key] = case
    for case in cases.values():
        case.saved = _split_codes(case._saved_raw)
        case.finish()
    return cases, stats


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
        raise FileNotFoundError(f"EA MBI Checker not found: {path}")
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

NO_CASES = ("No MBI cases loaded. Pick the MBI Results layer from the MBI Barangay Selector, tick "
            "the EA layers on the left, then click Load MBI Cases.")

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
# Run EA MBI Check box (amber, optional) and Load MBI Cases button (green): set apart so users who
# already have an MBI Results layer skip the run step.
RUN_COLOURS = {
    False: {"bg": "#FFF3E0", "border": "#FB8C00", "title": "#E65100", "btn": "#FFB74D", "btn_text": "#3E2723",
            "btn_hover": "#FFA726", "btn_off": "#FFE0B2", "load_off": "#DCEDC8", "off_text": "#8D6E63",
            "load": "#A5D6A7", "load_text": "#1B5E20", "load_border": "#43A047", "load_hover": "#81C784"},
    True: {"bg": "#3E2F1C", "border": "#FB8C00", "title": "#FFB74D", "btn": "#EF6C00", "btn_text": "#FFFFFF",
           "btn_hover": "#F57C00", "btn_off": "#5D4A2E", "load_off": "#2F4A31", "off_text": "#A1887F",
           "load": "#2E7D32", "load_text": "#FFFFFF", "load_border": "#66BB6A", "load_hover": "#388E3C"},
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
    <li><b>Pick the MBI Results Layer</b> — the <i>ppp_mbi</i> layer from the <b>MBI Barangay Selector</b>, after
        you assigned the covering barangays there. No check is run here: the cases are only loaded.</li>
    <li><b>Tick your EA layers</b> (left, <i>EA Layers</i>) — the layers of the covering barangays' EAs.</li>
    <li><b>Optional:</b> pick a <i>Building Points</i> layer so each case and EA shows its building count.</li>
    <li>Click <b style="color:#2e7d32;">Load MBI Cases</b> (green).</li>
    <li>In the list, <b>tick the Role / Status box of the EA that covers</b> each case — or click
        <b>Auto-Assign Pending</b> to let the tool decide the easy ones, then review. An overlap that
        runs across several EAs can be <b>shared out</b>: tick more than one EA and each keeps only its
        own part (see <i>Shared Overlaps</i> below).</li>
    <li>Click <b>Apply Coverage</b> to write your choices into the MBI layer.</li>
    <li>Click <b>Export GPKG + CSV</b> to save the processed layer and a decision log.</li>
  </ol>

  <div style="background:#f9fafb; border:1px solid #e5e7eb; border-left:4px solid #2e7d32;
              padding:10px 14px; margin:12px 0 16px 0;">
    <b style="color:#111827;">Coming from the MBI Barangay Selector?</b>
    Pick its <i>ppp_mbi</i> layer under <i>MBI Results Layer</i> (or click <b>Choose EAs…</b> in the Barangay
    Selector, which opens this window with it). <b>Choose EAs inside the covering barangay</b> is ticked for you:
    every MBI is listed under the barangay you chose there, and you only pick <b>which EA of that barangay</b>
    covers it. Tick the EA layers of those barangays, then <b>Load MBI Cases</b>. See <i>MBIs From the Barangay
    Selector</i> below.
  </div>

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
        <td>The <i>ppp_mbi</i> layer of the MBI Barangay Selector (must have <i>case_uuid</i>). Tick <i>Only selected MBI features</i> to load
        just part of it, and <i>Resume saved coverage choices</i> to pre-tick EAs already stored in it.
        A <i>ppp_mbi</i> layer of the MBI Barangay Selector works too (see below).</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">Choose EAs inside the covering barangay</td>
        <td>Only for a layer with <i>assigned_bgy</i> (MBI Barangay Selector) — ticked automatically when you pick
        one. Lists each MBI under its covering barangay and offers only that barangay's EAs. <b>EAs within … m of a
        gap</b>: how close an EA must be to a gap to be offered (default 5 m). Untick it to treat the layer like an
        ordinary MBI Results layer.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">EA Layers</td>
        <td>Click every EA layer the involved areas come from — an inter-barangay or inter-municipal MBI
        can involve EAs stored in different layers. EAs are matched on <i>ea_geocode</i>; the log reports
        involved EAs that none of the selected layers contain (shown in red in the list). The EA shapes are
        also what <b>shared overlaps</b> are clipped to — an overlap can only be shared out when all of its
        EAs are found in the selected layers.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">Building Points</td>
        <td>Optional. <b>Case row:</b> building points inside the gap / overlap. <b>EA rows:</b> of those,
        the points whose <i>Geocode field</i> (auto: ea_geocode, then geocode) matches the EA. Used by
        Auto-Assign.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">Load MBI Cases<br>(green button)</td>
        <td>Lists the cases of the MBI Results layer picked at the top. No MBI check is run in this tool — the
        MBIs were already detected and assigned a barangay in the MBI Barangay Selector.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">Barangay name fallback</td>
        <td>If a row of the MBI layer has no barangay name, the name is looked up by the barangay geocode
        (<i>geocode</i> / <i>bgy_geocode</i> / <i>bgy_geo</i>): first on the other rows of the MBI layer, then in
        the ticked EA layers (their <i>barangay</i> / <i>bgy_name</i> field). The log says how many were filled
        and lists geocodes with no name found (shown by geocode only).</td></tr>
  </table>

  <h3 style="color:#111827; font-size:14px; border-bottom:1px solid #e5e7eb; padding-bottom:4px;">Reading the Case List</h3>
  <table width="100%" cellpadding="6" cellspacing="0" style="border:1px solid #e5e7eb; font-size:11px;">
    <tr><td width="110" style="font-weight:bold; vertical-align:top;">Case row (bold)</td>
        <td>One gap / overlap. Shows its MBI level, the barangays involved, how many EAs are involved and
        the <b>Bldg Pts</b> inside it. Status is <b>Pending — tick the covering EA below</b> (peach) until an EA is chosen, then
        <b>✔ Done — covered by &lt;EA&gt;</b> (green) — or <b>Partly covered (x%) — tick another EA</b> (cream) when the EAs ticked in a
        shared overlap do not fill the whole MBI yet.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">EA rows (below)</td>
        <td>Each involved EA with its barangay and its own <b>Bldg Pts</b> (buildings inside this MBI tagged to
        that EA). <b>Tick the Role / Status box of the covering EA.</b> In a gap, or an overlap that cannot
        be shared out, only one EA covers the case — ticking another moves the coverage. Unticking every EA
        makes the case Pending again.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">Moving around</td>
        <td>Click a row to select it on the map, double-click to zoom, right-click for more (set / add / remove
        a covering EA, mark pending, copy case ID). Use the search box and the <i>All / Pending / Partly
        covered / Covered / Overlaps / Gaps / level</i> filter to find cases. Click a column header to sort.</td></tr>
  </table>

  <h3 style="color:#111827; font-size:14px; border-bottom:1px solid #e5e7eb; padding-bottom:4px;">Shared Overlaps (one MBI across several EAs)</h3>
  <table width="100%" cellpadding="6" cellspacing="0" style="border:1px solid #e5e7eb; font-size:11px;">
    <tr><td width="110" style="font-weight:bold; vertical-align:top;">What it is</td>
        <td>An overlap that runs across several EAs (e.g. one EA of a municipality overlapping a strip of four
        EAs of the next one) does not have to go to a single EA. Tick several EAs: <b>each ticked EA keeps only
        its own part of the overlap</b> — the MBI clipped to that EA. The other EAs release (clip) the parts the
        ticked EAs keep.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">Order matters</td>
        <td>Where two ticked EAs both contain the same piece of the overlap, the EA <b>ticked first</b> keeps it;
        the later one keeps the rest of its part. To change who keeps a shared piece, untick and re-tick in the
        order you want.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">Reading the rows</td>
        <td>Ticked EA: <b>✔ Covers its part (x% of the MBI)</b> — its share of the MBI, plus the buildings in it.
        Unticked EA: <b>✖ Gives up the overlap — cut out of it</b> when all of its part is kept by ticked EAs, <b>Gives up x% ·
        tick to keep the rest</b> when only some is — tick it to keep the rest. Hover a row for the areas.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">On the map</td>
        <td>Select an EA row to see its part: <b style="color:#2e7d32;">green</b> = what it keeps,
        <b style="color:#1565c0;">dashed blue</b> = what it would keep if ticked. Select the case row to see
        every kept part and, in <b style="color:#e65100;">orange</b>, what no ticked EA keeps yet.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">When it applies</td>
        <td>Overlaps with two or more EAs whose shapes are all in the selected EA layers. Gaps (no EA contains a
        gap) and overlaps with an EA missing from the selected layers keep the one-EA-covers-all rule.</td></tr>
  </table>

  <h3 style="color:#111827; font-size:14px; border-bottom:1px solid #e5e7eb; padding-bottom:4px;">MBIs From the Barangay Selector (choose the EA inside the covering barangay)</h3>
  <table width="100%" cellpadding="6" cellspacing="0" style="border:1px solid #e5e7eb; font-size:11px;">
    <tr><td width="110" style="font-weight:bold; vertical-align:top;">Before you start</td>
        <td>In the <b>MBI Barangay Selector</b>, tick the covering barangay of each MBI and click <b>Apply
        Coverage</b> — it writes <i>assigned_bgy</i>. MBIs without a covering barangay (still pending) and
        <i>No longer detected</i> MBIs are skipped here; the log says how many.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">One case per barangay</td>
        <td>Each MBI is listed under its covering barangay (the <i>Barangay</i> column). An overlap shared out
        between barangays there is listed <b>once per barangay</b>, each holding only <b>its part</b> of the MBI
        (the MBI clipped to that barangay's EAs) — the column shows the part's share, e.g. <i>its part (60%)</i>.
        Select the case row to see that part on the map (dashed blue).</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">Which EAs are offered</td>
        <td>Only the EAs of the covering barangay, from the ticked EA layers. With 14-digit geocodes the
        <b>first 8 digits</b> are the barangay and the last 6 the EA: the barangay in <i>assigned_bgy</i>
        (<i>02709007000000</i>) gets the EAs <i>02709007001000</i>, <i>02709007002000</i>, … Other codes: the EA code must
        start with the barangay code.<br>
        <b>Overlap / disputed area</b> — the barangay's EAs holding part of it. Several EAs can share it out,
        each keeping its own part (as in <i>Shared Overlaps</i>).<br>
        <b>Gap</b> — the barangay's EAs bordering the gap or within the <i>EAs within … m of a gap</i> distance;
        one EA absorbs the whole gap.<br>
        <b>None that close</b> — the 3 nearest EAs of the barangay, with their distance (<i>12.0 m away</i>) in the
        Barangay column: check those on the map.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">Fields written</td>
        <td>On the <b>covering barangay's row(s)</b> of the MBI layer: <i>assigned_ea</i> (chosen EA codes) and
        <i>coverage_status</i> (1_Covered / 2_Not Covered / 3_Partially Covered — only this tool writes it; the
        Barangay Selector writes none). Rows of barangays not chosen get no EA and <b>2_Not Covered</b>. The
        Barangay Selector's <i>assigned_bgy</i> is <b>never changed</b> (a field name turns red if you type it).
        There is no <i>ea_role</i> in this mode. The exported coverage parts and the CSV also carry the covering
        barangay (<i>bgy_geocode</i> / <i>covering_bgy</i>).</td></tr>
  </table>

  <h3 style="color:#111827; font-size:14px; border-bottom:1px solid #e5e7eb; padding-bottom:4px;">Roles</h3>
  <table width="100%" cellpadding="6" cellspacing="0" style="border:1px solid #e5e7eb; font-size:11px;">
    <tr><td width="110" style="font-weight:bold;">Overlap</td>
        <td>Ticked EA: <b>✔ Covers — keeps the overlap</b> (role <i>Covers</i>). Others: <b>✖ Gives up the overlap — cut out of it</b> (role <i>Releases</i>). Before you tick, each row reads <b>☐ Tick if this EA covers it</b>.</td></tr>
    <tr><td style="font-weight:bold;">Shared overlap</td>
        <td>Each ticked EA: <b>✔ Covers its part</b> (role <i>Covers</i>). Others: <b>✖ Gives up</b> (role
        <i>Releases</i>) once all of their part is kept by ticked EAs; no role while part of it is undecided.</td></tr>
    <tr><td style="font-weight:bold;">Gap</td>
        <td>Ticked EA: <b>✔ Covers — its boundary is extended over the gap</b> (role <i>Covers</i>). Others: <b>Not extended — stays as it is</b> (role <i>Releases</i>).</td></tr>
    <tr><td style="font-weight:bold;">Auto-Assign</td>
        <td>Single-EA cases go to that EA. Multi-EA cases go to the EA that owns most of the buildings inside
        the MBI (needs Building Points); shared overlaps go to <b>every</b> EA with tagged buildings, most
        buildings first, each keeping its part. No tagged buildings or a tie stays Pending — decide those by
        hand. Auto-assigned shared overlaps can end up Partly covered: check them with <i>Show: Partly
        covered</i>.</td></tr>
  </table>

  <h3 style="color:#111827; font-size:14px; border-bottom:1px solid #e5e7eb; padding-bottom:4px;">Apply Coverage — Fields Written</h3>
  <table width="100%" cellpadding="6" cellspacing="0" style="border:1px solid #e5e7eb; font-size:11px;">
    <tr><td width="110" style="font-weight:bold; vertical-align:top;">Coverage field<br>(default <i>assigned_ea</i>)</td>
        <td>Code of the covering EA, on every row of the case — several codes, comma-separated, for a shared
        overlap (in the order ticked). Empty while the case is Pending.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">ea_role</td>
        <td><b>Covers</b> on the covering EAs' rows, <b>Releases</b> on the others. Empty while Pending (and,
        in a shared overlap, for an EA whose part is not fully kept by the ticked EAs).</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">Status field<br>(default <i>coverage_status</i>)</td>
        <td><b>1_Covered</b> — the case is fully covered. <b>2_Not Covered</b> — the case is still Pending.
        <b>3_Partially Covered</b> — a shared overlap where part of the MBI has no covering EA yet.
        Written for every loaded case, so you can filter or symbolise on it.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">ppp_mbi_ea_clipped</td>
        <td>Apply also adds one temporary layer, <b>ppp_mbi_ea_clipped</b> (<i>ppp</i> = province code of the
        covering barangays, e.g. <i>027_mbi_ea_clipped</i>) with <b>the same fields as the ppp_mbi layer</b>. Instead
        of one row listing all EAs in <i>assigned_ea</i>, the covering barangay's row is split into <b>one row per
        ticked EA</b>: the MBI clipped to that EA's boundary, <i>assigned_ea</i> = that EA only, <i>1_Covered</i>.
        The not-covered rows are kept too: the barangays not chosen and pending cases (original shape,
        <i>2_Not Covered</i>), and the part no ticked EA keeps yet (<i>2_Not Covered</i>, no EA). Each Apply
        replaces it. Right-click › <i>Make Permanent</i> to keep it.
        (A leftover <i>MBI Coverage Parts</i> layer from an earlier version is removed.) <i>Export GPKG + CSV</i>
        also writes the parts, uncovered leftovers included, as <i>mbi_ea_coverage_parts</i>.</td></tr>
  </table>
  <div style="color:#4b5563; margin-top:4px;">The two field names can be changed in the panel (letters, digits and
  underscores only, all different). Missing fields are added to the MBI layer automatically.</div>

  <h3 style="color:#111827; font-size:14px; border-bottom:1px solid #e5e7eb; padding-bottom:4px;">Export GPKG + CSV</h3>
  <table width="100%" cellpadding="6" cellspacing="0" style="border:1px solid #e5e7eb; font-size:11px;">
    <tr><td width="110" style="font-weight:bold; vertical-align:top;">Export folder</td>
        <td>Where the files go. Leave it empty and you are asked for a folder when you click Export.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">GeoPackage</td>
        <td><i>mbi_ea_coverage_&lt;date&gt;_&lt;time&gt;.gpkg</i> with two layers: <i>mbi_ea_coverage</i> — the whole MBI
        layer including the coverage fields, even if you have not saved the layer's edits yet — and
        <i>mbi_ea_coverage_parts</i> — the part of each MBI every covering EA keeps (<i>part_role</i> <b>Kept</b>,
        with the EA's <i>ea_geocode</i>, name, barangay, <i>area_m2</i>, <i>pct_of_mbi</i>, <i>num_bldg_pts</i>,
        <i>released_by</i>), plus <b>Unassigned</b> for what no covering EA keeps yet.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">CSV</td>
        <td><i>mbi_ea_coverage_&lt;date&gt;_&lt;time&gt;.csv</i> — one row per case / EA: case ID, type, level, MBI area,
        building counts, EA code / name / barangay, the EA's part and kept area (m²) and kept buildings, role,
        covering EA(s) and status. Opens in Excel.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">Not applied yet?</td>
        <td>If your choices are not in the MBI layer yet, Export offers to <b>Apply Coverage first</b>, so the
        files always match what you see. Pending cases are exported as <b>2_Not Covered</b>, partly covered
        shared overlaps as <b>3_Partially Covered</b>.</td></tr>
  </table>

  <h3 style="color:#111827; font-size:14px; border-bottom:1px solid #e5e7eb; padding-bottom:4px;">If Something Looks Wrong</h3>
  <table width="100%" cellpadding="6" cellspacing="0" style="border:1px solid #e5e7eb; font-size:11px;">
    <tr><td width="110" style="font-weight:bold; vertical-align:top;">Can tick only one EA of an overlap</td>
        <td>After <i>Load MBI Cases</i>, the log lists every such case under <i>overlap case(s) where only ONE EA
        can be ticked</i>, with the reason. Usually no EA of the barangay holds more than 0.5 m² of the MBI — the
        EAs only touch it or lie within the search distance — so one EA takes the whole MBI. Check that the EA
        layer lines up with the MBI on the map. Gaps always take one EA.</td></tr>
    <tr><td width="110" style="font-weight:bold; vertical-align:top;">Load MBI Cases is greyed out</td>
        <td>Pick the <i>ppp_mbi</i> layer of the MBI Barangay Selector (it needs <i>case_uuid</i>) and tick at least
        one EA layer with an <i>ea_geocode</i> field.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">Barangay column shows only a geocode</td>
        <td>No barangay name was found for that geocode in the MBI layer or the ticked EA layers. Tick an EA layer
        that has a <i>barangay</i> / <i>bgy_name</i> field, then Load again.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">EA in red / "not found"</td>
        <td>The EA's layer is not ticked. Tick it, then Load again.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">Can only tick one EA in an overlap</td>
        <td>The overlap cannot be shared out: tick the layers of <b>all</b> its EAs under <i>EA Layers</i> (none
        may be red), then Load again. Gaps always take one EA.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">Stays Partly covered</td>
        <td>The ticked EAs' parts leave some of the MBI uncovered. Select the case row to see the orange
        leftover on the map, then tick the EA that contains it.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">EA rows show 0 buildings</td>
        <td>No building inside this MBI is tagged to that EA. Check the <i>Geocode field</i> under Building Points
        is the one holding the EA geocode.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">Field name turns red</td>
        <td>Use letters, digits and underscores only, starting with a letter or underscore; the Coverage field,
        Status field and <i>ea_role</i> must all differ. Apply and Export stay disabled until fixed.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">"No EA found" / "no EA to choose"</td>
        <td>Barangay mode: no EA of the covering barangay is in the ticked EA layers. Tick the EA layer that
        barangay comes from, then Load again.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">Barangay mode: fewer cases than MBIs</td>
        <td>MBIs still pending in the Barangay Selector (no <i>assigned_bgy</i>) are skipped — assign them there
        and Apply, then Load again. A shared overlap gives one case per covering barangay, so there can also be
        more cases than MBIs.</td></tr>
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
    from qgis.core import (QgsApplication, QgsCategorizedSymbolRenderer, QgsCoordinateTransform,
                           QgsDistanceArea, QgsFeature, QgsFeatureRequest, QgsField, QgsFillSymbol,
                           QgsGeometry, QgsProcessingContext, QgsProcessingFeedback, QgsProject,
                           QgsRendererCategory, QgsVectorFileWriter, QgsVectorLayer, QgsWkbTypes)
    from qgis.gui import QgsFieldComboBox, QgsFileWidget, QgsMapLayerComboBox, QgsRubberBand
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
            self.case_items = {}      # case key -> top-level tree item
            self.ea_lookup = {}       # ea_geocode -> {name, fid, layer_id}
            self.mbi_layer_id = None
            self.parts_layer_id = None   # old 'MBI Coverage Parts' layer (removed on Apply)
            self.da = None               # QgsDistanceArea for the MBI layer (m²)
            self.bgy_mode = False        # loaded cases come from an MBI Barangay Selector layer
            self._bands = {}             # map highlights of the selected EA's MBI part
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
            self.mbi_combo.layerChanged.connect(self._on_mbi_layer)
            for combo in (self.mbi_combo, self.bldg_combo):
                combo.layerChanged.connect(self.validate_inputs)
            self._on_mbi_layer(self.mbi_combo.currentLayer())
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
            self.mbi_combo.setToolTip("Gaps / Overlaps output of the EA MBI Checker.")
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
            # Barangay mode: MBI layer from the MBI Barangay Selector (covering barangay chosen)
            self.bgy_mode_check = QtWidgets.QCheckBox("Choose EAs inside the covering barangay")
            self.bgy_mode_check.setToolTip(
                f"For an MBI layer of the MBI Barangay Selector (has '{BGY_COVERAGE_FIELD}').\n"
                "Each MBI is listed once per covering barangay chosen there, and only the EAs of\n"
                "that barangay (from the ticked EA layers) that cover or border the MBI are offered.")
            self.bgy_mode_check.toggled.connect(self._on_bgy_mode)
            lay.addWidget(self.bgy_mode_check)
            self.bgy_opts = QtWidgets.QWidget()
            brow = QtWidgets.QHBoxLayout(self.bgy_opts)
            brow.setContentsMargins(18, 0, 0, 0)
            brow.addWidget(QtWidgets.QLabel("EAs within"))
            self.gap_dist_spin = QtWidgets.QDoubleSpinBox()
            self.gap_dist_spin.setRange(0.0, 1000.0)
            self.gap_dist_spin.setDecimals(1)
            self.gap_dist_spin.setSingleStep(1.0)
            self.gap_dist_spin.setValue(GAP_SEARCH_M)
            self.gap_dist_spin.setSuffix(" m")
            self.gap_dist_spin.setToolTip(
                "Gaps lie outside every EA: the barangay's EAs this close to the gap are offered.\n"
                "Overlaps: the barangay's EAs that contain part of the overlap are offered (this\n"
                f"distance is used only if none does). Nothing that close: the {NEAREST_FALLBACK} nearest "
                "EAs of the barangay are listed.")
            brow.addWidget(self.gap_dist_spin)
            brow.addWidget(QtWidgets.QLabel("of a gap"), 1)
            lay.addWidget(self.bgy_opts)
            self.bgy_mode_hint = self._hint()
            lay.addWidget(self.bgy_mode_hint)
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

            box, lay = group("Run EA MBI Check (only if you have no MBI layer)")
            self.run_box = box
            box.setObjectName("runBox")
            self.run_note = QtWidgets.QLabel(
                "Already have an MBI Results layer? Skip this box: pick it at the top, "
                "then click Load MBI Cases.")
            self.run_note.setWordWrap(True)
            lay.addWidget(self.run_note)
            self.mode_combo = QtWidgets.QComboBox()
            self.mode_combo.addItems(RUN_MODES)
            self.mode_combo.setToolTip("Which discrepancies the EA MBI Checker looks for.")
            lay.addWidget(self.mode_combo)
            self.run_btn = button("Run Check && Load Cases")
            self.run_btn.setToolTip(
                "Run the EA MBI Checker on the ticked EA layers (and Building Points),\n"
                "add the resulting MBI Results layer and list its cases in the viewer.")
            self.run_btn.setObjectName("runBtn")
            self.run_btn.clicked.connect(self.run_check)
            lay.addWidget(self.run_btn)
            self.run_hint = self._hint()
            lay.addWidget(self.run_hint)
            ll.addWidget(box)
            # The MBI Results layer comes from the MBI Barangay Selector: no check is run here,
            # the cases are only loaded. (Kept hidden so the old run path stays intact.)
            box.setVisible(False)

            self.load_btn = button("Load MBI Cases")
            self.load_btn.setToolTip("Read the MBI Results layer picked above (the ppp_mbi layer of the\n"
                                     "MBI Barangay Selector) and list each case with its candidate EAs.")
            self.load_btn.setObjectName("loadBtn")
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
            head.setToolTip(COL_ROLE,
                            "Tick the EA that covers the MBI.\n"
                            "Overlap across several EAs: tick more than one — each ticked EA keeps\n"
                            "only its own part of the overlap (the MBI clipped to that EA).")
            self.tree.setAlternatingRowColors(True)
            self.tree.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
            self.tree.setUniformRowHeights(True)
            # Full screen: the Barangay column takes the spare width, so Role / Status keeps
            # its size instead of stretching across the window.
            head_view = self.tree.header()
            head_view.setStretchLastSection(False)
            head_view.setSectionResizeMode(COL_BGY, QtWidgets.QHeaderView.Stretch)
            head_view.setMinimumSectionSize(40)
            self.tree.setSortingEnabled(True)
            self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
            self.tree.customContextMenuRequested.connect(self.show_context_menu)
            self.tree.itemChanged.connect(self.on_item_changed)
            self.tree.itemDoubleClicked.connect(self.on_double_click)
            self.tree.itemSelectionChanged.connect(self.on_selection_changed)
            rl.addWidget(self.tree, 1)

            swatch = '<span style="background-color:{}; border:1px solid #999;">&nbsp;&nbsp;&nbsp;</span> {}'
            level_line = '<b>MBI level:</b> ' + ' &nbsp;'.join(
                swatch.format(LEVEL_COLOURS[k], LEVEL_NAMES[k]) for k in sorted(LEVEL_COLOURS))
            self.legend = QtWidgets.QLabel(" &nbsp;&nbsp;".join([
                swatch.format(COLOUR_PENDING, "Pending (to decide)"),
                swatch.format(COLOUR_PARTIAL, "Partly covered"),
                swatch.format(COLOUR_COVERED, "✔ Covers"),
                swatch.format(COLOUR_RELEASED, "Gives up / not extended"),
            ]) + " &nbsp;&nbsp;·&nbsp; Double-click: zoom · Right-click: more<br>" + level_line)
            self.legend.setWordWrap(True)
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
            self.field_edit.setToolTip("Field that receives the code(s) of the covering EA(s),\n"
                                       "comma-separated when an overlap is shared out.")
            orow.addWidget(self.field_edit)
            orow.addSpacing(10)
            orow.addWidget(QtWidgets.QLabel("Status field:"))
            self.status_edit = QtWidgets.QLineEdit(STATUS_FIELD)
            self.status_edit.setMaximumWidth(140)
            self.status_edit.setToolTip(f"Field that receives the case status: '{STATUS_COVERED}' "
                                        f"(fully covered), '{STATUS_PARTIAL}' (part of a shared\n"
                                        f"overlap has no covering EA yet) or '{STATUS_NOT_COVERED}' "
                                        "(still pending).")
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
                "shared overlaps go to every EA with tagged buildings, most first, each keeping\n"
                "its own part. No tagged buildings or a tie stays pending.")
            self.auto_btn.clicked.connect(self.auto_assign)
            self.reset_btn = button("Reset Choices", 32, BTN_CANCEL)
            self.reset_btn.setToolTip("Clear every EA tick in the viewer (nothing is written).")
            self.reset_btn.clicked.connect(self.reset_choices)
            self.apply_btn = button("Apply Coverage", 32, BTN_EXPORT)
            self.apply_btn.setToolTip(
                f"Writes the covering EA(s) ('{COVERAGE_FIELD}'), each row's role ('{ROLE_FIELD}') and\n"
                f"the case status ('{STATUS_FIELD}') into the MBI layer, and adds 'ppp_mbi_ea_clipped'\n"
                "(each MBI clipped to the boundary of every ticked EA).\n"
                "Changes are left UNSAVED (the layer enters edit mode) — use QGIS's\n"
                "Save Edits to keep them, or Cancel Edits to discard and revert.")
            self.apply_btn.clicked.connect(self.apply_coverage)
            self.export_btn = button("Export GPKG + CSV", 32, BTN_EXPORT)
            self.export_btn.setToolTip(
                "Write the processed MBI layer (with the coverage fields) and the coverage parts\n"
                "to a GeoPackage, plus a CSV of every case / EA decision, into the Export folder.\n"
                "Choices not yet applied are applied first.")
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

        def _on_mbi_layer(self, layer):
            """A Barangay Selector layer switches barangay mode on (and off for other layers)."""
            bgy = is_bgy_mbi_layer(layer)
            self.bgy_mode_check.setEnabled(bgy)
            self.bgy_mode_check.setChecked(bgy)
            self._on_bgy_mode(bgy)

        def _on_bgy_mode(self, on):
            """Show the barangay-mode options and swap the default status field, so the
            Barangay Selector's own 'coverage_status' is never overwritten."""
            self.bgy_opts.setVisible(on)
            self.bgy_mode_hint.setVisible(on)
            status = self.status_edit.text().strip()
            if on and status == STATUS_FIELD:
                self.status_edit.setText(BGY_MODE_STATUS_FIELD)
            elif not on and status == BGY_MODE_STATUS_FIELD:
                self.status_edit.setText(STATUS_FIELD)
            self.validate_inputs()

        def bgy_mode_wanted(self):
            return self.bgy_mode_check.isChecked() and is_bgy_mbi_layer(self.mbi_combo.currentLayer())

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

        def _fill_show_combo(self, levels=(), types=()):
            current = self.show_combo.currentData()
            self.show_combo.blockSignals(True)
            self.show_combo.clear()
            for label, data in [("All cases", None), ("Pending", "pending"),
                                ("Partly covered", "partial"), ("Covered", "covered"),
                                ("Overlaps", "type:Overlap"), ("Gaps", "type:Gap")]:
                self.show_combo.addItem(label, data)
            for kind in sorted(set(types) - {"Overlap", "Gap"}):
                self.show_combo.addItem(f"{kind} areas" if kind == "Disputed" else kind, f"type:{kind}")
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
            rc = RUN_COLOURS[self.is_dark]
            self.run_box.setStyleSheet(
                GRP_STYLE + f" QGroupBox#runBox {{ background-color:{rc['bg']}; border:1px solid {rc['border']};"
                f" border-radius:4px; margin-top:8px; padding-top:6px; }}"
                f" QGroupBox#runBox::title {{ color:{rc['title']}; }}")
            self.run_note.setStyleSheet(f"color:{rc['title']}; font-size:11px; font-style:italic;")
            self.run_btn.setStyleSheet(
                f"QPushButton#runBtn {{ background-color:{rc['btn']}; color:{rc['btn_text']}; font-weight:600;"
                f" border:1px solid {rc['border']}; border-radius:3px; }}"
                f" QPushButton#runBtn:hover {{ background-color:{rc['btn_hover']}; }}"
                f" QPushButton#runBtn:disabled {{ background-color:{rc['btn_off']}; color:{rc['off_text']}; }}")
            self.load_btn.setStyleSheet(
                f"QPushButton#loadBtn {{ background-color:{rc['load']}; color:{rc['load_text']}; font-weight:600;"
                f" border:1px solid {rc['load_border']}; border-radius:3px; }}"
                f" QPushButton#loadBtn:hover {{ background-color:{rc['load_hover']}; }}"
                f" QPushButton#loadBtn:disabled {{ background-color:{rc['load_off']}; color:{rc['off_text']}; }}")
            html = HELP_HTML
            if self.is_dark:
                for a, b in HELP_DARK_SWAPS:
                    html = html.replace(a, b)
            self.help_text.setStyleSheet(t["help"])
            self.help_text.setHtml(html)
            for lab in (self.mbi_hint, self.ea_hint, self.bldg_hint, self.folder_hint,
                        self.layer_count_label, self.run_hint, self.bgy_mode_hint):
                self.set_hint(lab, lab.text(), lab.property("hintKind") or "info")

        def showEvent(self, event):
            super().showEvent(event)
            dark = self.palette().color(self.palette().Window).lightness() < 128
            if dark != self.is_dark:
                self.is_dark = dark
                self.apply_theme()

        # ──────────────── teardown ────────────────
        def _teardown(self):
            self.clear_bands()
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
                self.set_hint(self.mbi_hint, "Pick the MBI Results layer (ppp_mbi) from the MBI "
                                             "Barangay Selector.")
                ok = False
            elif "case_uuid" not in field_names(mbi):
                self.set_hint(self.mbi_hint, "Missing 'case_uuid' field — pick the ppp_mbi layer of "
                                             "the MBI Barangay Selector.", "error")
                ok = False
            elif is_bgy_mbi_layer(mbi):
                self.set_hint(self.mbi_hint, f"{mbi.featureCount():,} MBI row(s) · covering barangays "
                                             f"from the MBI Barangay Selector ('{BGY_COVERAGE_FIELD}')", "ok")
            else:
                kind = infer_mbi_type(mbi.name())
                self.set_hint(self.mbi_hint, f"{mbi.featureCount():,} MBI row(s)"
                              + (f" · {kind}s" if kind else ""), "ok")

            bgy_mode = self.bgy_mode_wanted()
            if bgy_mode:
                self.set_hint(self.bgy_mode_hint, "Each MBI is listed once per covering barangay; tick the "
                                                  "EA of that barangay that covers it. Needs the EA layers "
                                                  "of those barangays (below).")
            eas = self.ea_layers()
            self.set_hint(self.layer_count_label,
                          f"Selected layers: {len(eas)} / {len(self._ea_items())}")
            if not eas and bgy_mode:
                self.set_hint(self.ea_hint, "Tick the EA layer(s) of the covering barangays — the EAs "
                                            "to choose from are read from them.", "error")
                ok = False
            elif not eas:
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
            # Barangay mode: never overwrite the Barangay Selector's covering barangays.
            taken = {BGY_COVERAGE_FIELD.lower()} if bgy_mode else set()
            bad_cov, bad_status = names[0].lower() in taken, names[1].lower() in taken
            bad = "border: 1px solid #c62828;"
            self._field_ok = all(valid) and not clash and not bad_cov and not bad_status
            self.field_edit.setStyleSheet("" if valid[0] and not clash and not bad_cov else bad)
            self.status_edit.setStyleSheet("" if valid[1] and not clash and not bad_status else bad)
            self.status_edit.setToolTip(
                (f"'{names[1]}' holds the Barangay Selector's covering barangays — use another "
                 f"name (default '{STATUS_FIELD}').\n\n" if bad_status else "")
                + f"Field that receives the case status: '{STATUS_COVERED}' (fully covered), "
                f"'{STATUS_PARTIAL}' (part of a shared\noverlap has no covering EA yet) or "
                f"'{STATUS_NOT_COVERED}' (still pending).")

            folder = self.folder_widget.filePath().strip()
            if not folder:
                self.set_hint(self.folder_hint, "Export writes a GPKG and a CSV here "
                                                "(you are asked for a folder if empty).")
            elif os.path.isdir(folder):
                self.set_hint(self.folder_hint, "Export GPKG + CSV writes its files here.", "ok")
            else:
                self.set_hint(self.folder_hint, "Folder does not exist.", "error")

            # Run EA MBI Check: needs EA layers carrying the checker's fields.
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
            unsaved = sum(1 for c in self.cases.values() if c.assigned != c.saved)
            if unsaved and QtWidgets.QMessageBox.question(
                    self, DIALOG_TITLE,
                    f"{unsaved:,} choice(s) have not been applied yet. Clear the viewer anyway?",
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel) != QtWidgets.QMessageBox.Yes:
                return
            self.clear_bands()
            self._painting = True
            self.tree.clear()
            self._painting = False
            self.cases, self.case_items, self.ea_lookup = {}, {}, {}
            self.mbi_layer_id = None
            self.bgy_mode = False
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
                      self.show_combo, self.clear_btn, self.mode_combo, self.bldg_field_combo,
                      self.gap_dist_spin):
                w.setEnabled(not busy)
            self.bgy_mode_check.setEnabled(not busy and is_bgy_mbi_layer(self.mbi_combo.currentLayer()))
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
            covered = sum(1 for c in self.cases.values() if c.status == "covered")
            partial = sum(1 for c in self.cases.values() if c.status == "partial")
            shown = sum(1 for t in self.case_items.values() if not t.isHidden())
            if not total:
                text = NO_CASES
            else:
                what = (f"{total:,} covering-barangay case(s) from "
                        f"{len({c.case_uuid for c in self.cases.values()}):,} MBI(s)"
                        if self.bgy_mode else f"{total:,} MBI case(s)")
                text = (f"{what} - {total - covered - partial:,} pending, "
                        + (f"{partial:,} partly covered, " if partial else "")
                        + f"{covered:,} covered.")
                if shown != total:
                    text += f" Showing {shown:,}."
                unapplied = any(c.assigned != c.saved for c in self.cases.values())
                if covered < total:
                    text += " Tick the covering EA(s) of each case in the viewer."
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
            unsaved = sum(1 for c in self.cases.values() if c.assigned != c.saved)
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
            self.log(f"Running EA MBI Check ({RUN_MODES[mode]}) on {len(eas)} EA layer(s): "
                     + ", ".join(l.name() for l in eas))
            if bldg is None:
                self.log("  No Building Points layer — building counts will be 0.")
            self.summary.setText("Running the EA MBI Check…")
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
                alg = topo.EAMBIChecker()
                alg.initAlgorithm()
                alg.processAlgorithm({'EA_LAYERS': eas, 'BLDG_LAYERS': bldg_inputs,
                                      'RUN_MODE': mode}, context, feedback)
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
                self.log("EA MBI check cancelled.")
                result = None
            except Exception as e:
                self.log(traceback.format_exc().rstrip())
                self.log(f"EA MBI check FAILED: {e}")
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

            bgy_mode = self.bgy_mode_wanted()
            previous = ({k: list(c.assigned) for k, c in self.cases.items() if c.assigned}
                        if mbi.id() == self.mbi_layer_id and bgy_mode == self.bgy_mode else {})
            field = self.coverage_field()
            preload = field if (self.preload_check.isChecked()
                                and mbi.fields().indexOf(field) >= 0) else None

            self.set_busy(True)
            self.log("=" * 50)
            self.log(f"Loading MBI cases from '{mbi.name()}'"
                     + (f" ({len(only_fids):,} selected feature(s))" if only_fids else "")
                     + (" — EAs chosen inside the covering barangay" if bgy_mode else "") + "…")
            if bgy_mode:
                try:
                    self._load_bgy_cases(mbi, eas, bldg, bldg_field, only_fids, preload, previous)
                except _Cancelled:
                    self.log("Cancelled — the previous viewer contents were kept.")
                except Exception as e:
                    self.log(traceback.format_exc().rstrip())
                    self.log(f"Load FAILED: {e}")
                    self.progress.setValue(0)
                finally:
                    self.set_busy(False)
                    self.update_summary()
                return
            try:
                cases = load_mbi_cases(
                    mbi, infer_mbi_type(mbi.name()), only_fids, preload,
                    tick=lambda d, t: self.tick(d, t * 2 if bldg else t, "Reading MBI cases…"))
                involved = {c for case in cases.values() for c in case.candidates}
                self.log(f"  {len(cases):,} case(s) involving {len(involved):,} EA(s)")
                if preload:
                    resumed = sum(1 for c in cases.values() if c.assigned)
                    self.log(f"  Resumed {resumed:,} saved choice(s) from '{preload}'")
                for uuid, codes in previous.items():
                    if uuid in cases:
                        cases[uuid].assigned = [c for c in codes if c in cases[uuid].candidates]
                self.da = self._distance_area(mbi)
                self.load_geometry(cases, mbi)

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
                    self.compute_parts(cases, mbi, lookup)
                if bldg is not None:
                    self.count_buildings(cases, mbi, bldg, bldg_field)
                for case in cases.values():
                    if not case.split:
                        case.assigned = case.assigned[:1]
                    case.resolve(self.da)

                self.cases = cases
                self.ea_lookup = lookup
                self.mbi_layer_id = mbi.id()
                self.bgy_mode = False
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

        def _distance_area(self, layer):
            """Ellipsoidal areas in m², whatever the layer CRS (degrees for EPSG:4326)."""
            prj = QgsProject.instance()
            da = QgsDistanceArea()
            da.setSourceCrs(layer.crs(), prj.transformContext())
            ellipsoid = prj.ellipsoid()
            da.setEllipsoid(ellipsoid if ellipsoid and ellipsoid.upper() != "NONE" else "EPSG:7030")
            return da

        def load_geometry(self, cases, mbi_layer):
            """MBI geometry of each case. Every row of a case carries the same geometry, so
            one row per case is read."""
            first_fid = {}   # fid -> cases (barangay mode: covering barangays can share a row)
            for case in cases.values():
                if case.feature_ids:
                    first_fid.setdefault(case.feature_ids[0], []).append(case)
            request = QgsFeatureRequest().setFilterFids(list(first_fid)).setNoAttributes()
            total = max(len(first_fid), 1)
            for i, feat in enumerate(mbi_layer.getFeatures(request)):
                if i % 200 == 0:
                    self.tick(i, total, "Reading MBI geometry…")
                if not feat.hasGeometry():
                    continue
                geom = QgsGeometry(feat.geometry())
                if not geom.isGeosValid():
                    geom = geom.makeValid()
                geom = _polygonal(geom)
                for case in first_fid[feat.id()]:
                    case.geom = geom
                    case.area = case.mbi_area = _area(geom, self.da)

        # ──────────────── barangay mode (MBI layer from the MBI Barangay Selector) ────────────────
        def _load_bgy_cases(self, mbi, eas, bldg, bldg_field, only_fids, preload, previous):
            """One case per (MBI, covering barangay); the EA candidates are the barangay's EAs
            in the ticked EA layers that cover (overlap) or border (gap) the MBI."""
            cases, st = load_bgy_assigned_cases(
                mbi, only_fids, preload,
                tick=lambda d, t: self.tick(d, t * 2 if bldg else t, "Reading MBI cases…"))
            shared = sum(1 for c in cases.values() if c.bgy_shared and c.bgy_code == c.bgy_codes[0])
            self.log(f"  {st['mbis']:,} MBI(s), {st['used']:,} with a covering barangay → {len(cases):,} "
                     "case(s), one per covering barangay"
                     + (f" ({shared:,} MBI(s) shared out between barangays)" if shared else ""))
            if st["no_bgy"]:
                self.log(f"  {st['no_bgy']:,} MBI(s) have no covering barangay in '{BGY_COVERAGE_FIELD}' "
                         "yet — skipped (choose it in the MBI Barangay Selector and Apply there first)")
            if st["gone"]:
                self.log(f"  {st['gone']:,} no-longer-detected MBI(s) skipped (nothing to cover)")
            if not cases:
                self.log("Nothing to load.")
                QtWidgets.QMessageBox.warning(
                    self, DIALOG_TITLE,
                    f"No MBI in '{mbi.name()}' has a covering barangay yet.\n\nIn the MBI Barangay "
                    f"Selector, tick the covering barangays and click Apply Coverage (it writes "
                    f"'{BGY_COVERAGE_FIELD}'), then load again.")
                return
            self.da = self._distance_area(mbi)
            self.load_geometry(cases, mbi)
            lookup = self.find_bgy_eas(cases, mbi, eas)
            for case in cases.values():
                case.assigned = [c for c in case.saved if c in case.candidates]
                case.finish()
            if preload:
                resumed = sum(1 for c in cases.values() if c.assigned)
                self.log(f"  Resumed {resumed:,} saved choice(s) from '{preload}'")
            for key, codes in previous.items():
                if key in cases:
                    cases[key].assigned = [c for c in codes if c in cases[key].candidates]
            if bldg is not None:
                self.count_buildings(cases, mbi, bldg, bldg_field)
            for case in cases.values():
                if not case.split:
                    case.assigned = case.assigned[:1]
                case.resolve(self.da)

            self.cases = cases
            self.ea_lookup = lookup
            self.mbi_layer_id = mbi.id()
            self.bgy_mode = True
            self.populate_tree()
            self.progress.setValue(100)
            self.log("Load finished.")

        def find_bgy_eas(self, cases, mbi_layer, ea_layers):
            """Fill each case's EA candidates from the covering barangay's EAs.

            Shared MBIs (several covering barangays) are first clipped to each barangay (MBI ∩
            its EAs, minus what a barangay chosen before it keeps), as in the Barangay Selector.
            Overlap / disputed: the EAs holding part of the barangay's MBI; gap (or none of them
            does): the EAs within the search distance; still none: the nearest few.
            Returns the ea_geocode lookup {code: {name, fid, layer_id}}."""
            import math
            from qgis.core import QgsCoordinateReferenceSystem
            prj = QgsProject.instance()
            keys = {_bgy_key(c.bgy_code) for c in cases.values()}
            key_lens = sorted({len(k) for k in keys}, reverse=True)

            # 1. EAs of the covering barangays (MBI layer CRS)
            eas_by_bgy, lookup, dupes = {}, {}, 0
            bgy_names = {}   # barangay key -> (name, city) from the EA layers (name fallback)
            total = max(sum(l.featureCount() for l in ea_layers), 1)
            done = 0
            for layer in ea_layers:
                ea_field = _pick_field(layer, EA_GEOCODE_FIELDS)
                bgy_field = _pick_field(layer, BGY_CODE_FIELDS)
                if bgy_field == ea_field:
                    bgy_field = None
                name_field = _pick_field(layer, EA_NAME_FIELDS)
                bname_field = _pick_field(layer, BGY_NAME_FIELDS)
                bcity_field = _pick_field(layer, BGY_CITY_FIELDS)
                xform = QgsCoordinateTransform(layer.crs(), mbi_layer.crs(), prj)
                request = QgsFeatureRequest().setSubsetOfAttributes(
                    [f for f in (ea_field, bgy_field, name_field, bname_field, bcity_field) if f],
                    layer.fields())
                for feat in layer.getFeatures(request):
                    if done % 500 == 0:
                        self.tick(done, total, f"Reading the barangays' EAs ({done:,}/{total:,})…")
                    done += 1
                    code = _text(feat, ea_field)
                    if not code or not feat.hasGeometry():
                        continue
                    bkey = _bgy_key(_text(feat, bgy_field)) if bgy_field else ""
                    if bkey not in keys:
                        # 14-digit PSA codes: the first 8 digits are the barangay
                        bkey = _bgy_key(code)
                    if bkey not in keys:
                        # otherwise an EA geocode starts with its barangay's geocode
                        ek = _code_key(code)
                        bkey = next((ek[:n] for n in key_lens if n < len(ek) and ek[:n] in keys), "")
                    if not bkey:
                        continue
                    if bkey not in bgy_names and bname_field and _text(feat, bname_field):
                        bgy_names[bkey] = (_text(feat, bname_field),
                                           _text(feat, bcity_field) if bcity_field else "")
                    if code in lookup:
                        dupes += 1
                        continue
                    g = QgsGeometry(feat.geometry())
                    if layer.crs() != mbi_layer.crs():
                        g.transform(xform)
                    if not g.isGeosValid():
                        g = g.makeValid()
                    lookup[code] = {"name": _text(feat, name_field), "fid": feat.id(),
                                    "layer_id": layer.id()}
                    eas_by_bgy.setdefault(bkey, []).append((code, lookup[code]["name"], g))
            self.log(f"  {len(lookup):,} EA(s) of the {len(keys):,} covering barangay(s) found in "
                     f"{len(ea_layers)} EA layer(s)")
            # Barangay name fallback: the MBI layer had none for this geocode -> EA layers
            named = 0
            for case in cases.values():
                found = bgy_names.get(_bgy_key(case.bgy_code))
                if found and not case.bgy_name:
                    case.bgy_name = found[0]
                    named += 1
                if found and not case.bgy_city:
                    case.bgy_city = found[1]
            if named:
                self.log(f"  {named:,} case(s) had no barangay name in the MBI layer — named from the "
                         "EA layers by geocode")
            unnamed = sorted({c.bgy_code for c in cases.values() if not c.bgy_name})
            if unnamed:
                self.log(f"  No barangay name found for {len(unnamed):,} geocode(s): "
                         + ", ".join(unnamed[:10]) + (" …" if len(unnamed) > 10 else ""))
            if dupes:
                self.log(f"  {dupes:,} duplicate ea_geocode(s) across layers — the first selected "
                         "layer's EA is used.")
            missing = sorted({c.bgy_label for c in cases.values()
                              if _bgy_key(c.bgy_code) not in eas_by_bgy})
            if missing:
                self.log(f"  {len(missing):,} covering barangay(s) have no EA in the selected layers: "
                         + ", ".join(missing[:10]) + (" …" if len(missing) > 10 else ""))
                self.set_hint(self.ea_hint, f"{len(missing):,} covering barangay(s) have no EA in the "
                                            "selected layers — tick the layer(s) they come from.", "error")
            else:
                self.set_hint(self.ea_hint, f"EAs found for all {len(keys):,} covering barangay(s).", "ok")

            # 2. Shared MBIs: each covering barangay's own part
            by_uuid = {}
            for case in cases.values():
                by_uuid.setdefault(case.case_uuid, []).append(case)
            unclipped = 0
            for subs in by_uuid.values():
                whole = subs[0].geom
                if len(subs) < 2 or whole is None:
                    continue
                claimed = None
                for case in subs:   # in the order the barangays were chosen
                    geoms = [g for _c, _n, g in eas_by_bgy.get(_bgy_key(case.bgy_code), [])
                             if g.intersects(whole)]
                    part = _polygonal(whole.intersection(
                        geoms[0] if len(geoms) == 1 else QgsGeometry.unaryUnion(geoms))) if geoms else None
                    if part is not None and claimed is not None:
                        part = _polygonal(part.difference(claimed))
                    if part is None or _area(part, self.da) <= SLIVER_M2:
                        unclipped += 1   # no EA of the barangay in the MBI: keep the whole MBI
                        continue
                    case.geom, case.area = part, _area(part, self.da)
                    claimed = part if claimed is None else QgsGeometry.unaryUnion([claimed, part])
            if unclipped:
                self.log(f"  {unclipped:,} shared MBI part(s) could not be clipped to their barangay's EAs "
                         "— the whole MBI is offered there")

            # 3. Candidates
            max_m = self.gap_dist_spin.value()
            to_m = QgsCoordinateTransform(mbi_layer.crs(), QgsCoordinateReferenceSystem("EPSG:3857"), prj) \
                if mbi_layer.crs().isGeographic() else None

            def metric(g):
                g = QgsGeometry(g)
                if to_m is not None:
                    g.transform(to_m)
                return g

            n = {"inside": 0, "near": 0, "nearest": 0, "none": 0}
            for i, case in enumerate(cases.values()):
                if i % 25 == 0:
                    self.tick(i, len(cases), f"Finding the EAs of each covering barangay ({i:,}/{len(cases):,})…")
                infos = eas_by_bgy.get(_bgy_key(case.bgy_code), [])
                if case.geom is None or not infos:
                    n["none"] += 1
                    continue
                is_gap = case.mbi_type.lower() == "gap"
                engine = QgsGeometry.createGeometryEngine(case.geom.constGet())
                engine.prepareGeometry()
                scored = []   # [code, name, part, part_area, dist_m, geom]
                best_raw = 0.0   # largest area of the MBI inside one EA (even below SLIVER_M2)
                for code, name, g in infos:
                    touches = engine.intersects(g.constGet())
                    part, part_area = None, 0.0
                    if touches and not is_gap:
                        part = _polygonal(case.geom.intersection(g))
                        part_area = _area(part, self.da)
                        best_raw = max(best_raw, part_area)
                        if part_area <= SLIVER_M2:
                            part, part_area = None, 0.0
                    scored.append([code, name, part, part_area, 0.0 if touches else None, g])
                chosen = sorted((s for s in scored if s[2] is not None), key=lambda s: -s[3])
                path = "inside"
                if chosen:
                    n["inside"] += 1
                else:
                    m_case = metric(case.geom)
                    scale = math.cos(math.radians(case.geom.centroid().asPoint().y())) if to_m else 1.0
                    for s in scored:
                        if s[4] is None:
                            s[4] = m_case.distance(metric(s[5])) * scale
                    chosen = sorted((s for s in scored if s[4] <= max_m), key=lambda s: s[4])
                    if chosen:
                        n["near"] += 1
                        path = "near"
                    else:
                        chosen = sorted(scored, key=lambda s: s[4])[:NEAREST_FALLBACK]
                        n["nearest"] += 1
                        path = "nearest"
                for code, name, part, part_area, dist, _g in chosen:
                    cand = case.candidates[code] = EACandidate(code)
                    cand.name, cand.geocode = name, case.bgy_code
                    cand.barangay, cand.city_mun = case.bgy_name, case.bgy_city
                    cand.part, cand.part_area, cand.dist = part, part_area, dist
                case.split = (not is_gap and len(case.candidates) > 1
                              and all(c.part is not None for c in case.candidates.values())
                              and sum(c.part_area for c in case.candidates.values()) > SLIVER_M2)
                case.no_split_reason = ""
                if not case.split and len(case.candidates) > 1:
                    k = len(case.candidates)
                    if is_gap:
                        case.no_split_reason = "a gap is absorbed whole by one EA"
                    elif path == "near":
                        case.no_split_reason = (
                            f"no EA of {case.bgy_label} holds more than {SLIVER_M2:g} m² of the MBI "
                            f"(largest: {best_raw:,.2f} m²) — the {k} EA(s) offered only touch it or lie "
                            f"within {max_m:g} m, so one EA takes the whole MBI. Check that the EA layer "
                            "lines up with the MBI on the map.")
                    elif path == "nearest":
                        case.no_split_reason = (
                            f"no EA of {case.bgy_label} holds or touches the MBI (none within "
                            f"{max_m:g} m) — only the {k} nearest EA(s) are offered, so one EA takes "
                            "the whole MBI.")
                    else:
                        case.no_split_reason = (
                            f"the EAs' parts of the MBI add up to {SLIVER_M2:g} m² or less, "
                            "so one EA takes the whole MBI.")
            self.log_no_split(cases)
            self.log(f"  EA candidates: {n['inside']:,} case(s) with EAs holding the MBI, "
                     f"{n['near']:,} with EAs within {max_m:g} m"
                     + (f", {n['nearest']:,} with only the {NEAREST_FALLBACK} nearest EAs (none within "
                        f"{max_m:g} m — check them)" if n["nearest"] else "")
                     + (f", {n['none']:,} with no EA found" if n["none"] else ""))
            split = sum(1 for c in cases.values() if c.split)
            if split:
                self.log(f"  {split:,} case(s) can be shared out between EAs of the barangay "
                         "(each covering EA keeps only its part)")
            return lookup

        def log_no_split(self, cases, limit=20):
            """Log why several EAs cannot be ticked, for every case with 2+ EAs where only one
            can cover the MBI. Gaps are summarised in one line (one EA always absorbs a gap)."""
            gaps = [c for c in cases.values() if c.no_split_reason and c.mbi_type.lower() == "gap"]
            others = [c for c in cases.values() if c.no_split_reason and c.mbi_type.lower() != "gap"]
            if gaps:
                self.log(f"  {len(gaps):,} gap case(s) with several EAs: tick ONE EA — it absorbs the "
                         "whole gap.")
            if not others:
                return
            self.log(f"  {len(others):,} overlap case(s) where only ONE EA can be ticked:")
            for case in others[:limit]:
                where = f" ({case.bgy_label})" if case.bgy_code else ""
                self.log(f"    • {case.mbi_type or 'MBI'} {case.case_uuid[:8]}{where}: "
                         f"{case.no_split_reason}")
            if len(others) > limit:
                self.log(f"    … and {len(others) - limit:,} more")

        def compute_parts(self, cases, mbi_layer, lookup):
            """Clip each case's MBI to every involved EA (MBI ∩ EA). An overlap whose
            involved EAs all have a part is 'split': several EAs can cover it, each keeping
            only its own part."""
            prj = QgsProject.instance()
            wanted = {}   # layer_id -> {fid: [(case, cand)]}
            for case in cases.values():
                if case.geom is None:
                    continue
                for cand in case.candidates.values():
                    info = lookup.get(cand.code)
                    if info:
                        wanted.setdefault(info["layer_id"], {}).setdefault(
                            info["fid"], []).append((case, cand))
            total = max(sum(len(v) for v in wanted.values()), 1)
            done = 0
            for layer_id, by_fid in wanted.items():
                layer = prj.mapLayer(layer_id)
                if layer is None:
                    continue
                xform = QgsCoordinateTransform(layer.crs(), mbi_layer.crs(), prj)
                request = QgsFeatureRequest().setFilterFids(list(by_fid)).setNoAttributes()
                for feat in layer.getFeatures(request):
                    if done % 50 == 0:
                        self.tick(done, total, f"Clipping MBIs to their EAs ({done:,}/{total:,})…")
                    done += 1
                    if not feat.hasGeometry():
                        continue
                    ea_geom = QgsGeometry(feat.geometry())
                    if layer.crs() != mbi_layer.crs():
                        ea_geom.transform(xform)
                    if not ea_geom.isGeosValid():
                        ea_geom = ea_geom.makeValid()
                    for case, cand in by_fid[feat.id()]:
                        cand.part = _polygonal(case.geom.intersection(ea_geom))
                        cand.part_area = _area(cand.part, self.da)

            split = unsplit = 0
            for case in cases.values():
                cands = list(case.candidates.values())
                is_overlap = case.mbi_type.lower() != "gap"
                case.split = (is_overlap and case.geom is not None and len(cands) > 1
                              and all(c.part is not None for c in cands)
                              and sum(c.part_area for c in cands) > SLIVER_M2)
                case.no_split_reason = ""
                if case.split:
                    split += 1
                elif len(cands) > 1:
                    if not is_overlap:
                        case.no_split_reason = "a gap is absorbed whole by one EA"
                    else:
                        unsplit += 1
                        missing = [c.code for c in cands if c.part is None]
                        if case.geom is None:
                            case.no_split_reason = "the MBI has no geometry, so one EA takes it whole."
                        elif missing:
                            case.no_split_reason = (
                                f"EA(s) {', '.join(missing)} hold no part of the MBI (not in the selected "
                                f"EA layers, or {SLIVER_M2:g} m² or less inside them), so one EA takes "
                                "the whole MBI.")
                        else:
                            case.no_split_reason = (f"the EAs' parts add up to {SLIVER_M2:g} m² or less, "
                                                    "so one EA takes the whole MBI.")
            self.log(f"  {split:,} overlap case(s) can be shared out between their EAs "
                     "(each covering EA keeps only its part)")
            self.log_no_split(cases)

        def count_buildings(self, cases, mbi_layer, bldg_layer, gc_field=None):
            """Case: building points inside the MBI area. EA: those of them whose geocode
            (gc_field) matches the EA's ea_geocode, or its geocode when only one involved
            EA has it. Points matching no involved EA are counted as untagged."""
            prj = QgsProject.instance()
            xform = QgsCoordinateTransform(mbi_layer.crs(), bldg_layer.crs(), prj)
            with_geom = [case for case in cases.values() if case.geom is not None]
            total = max(len(with_geom), 1)
            self.log(f"Counting buildings inside {len(with_geom):,} MBI case(s) from "
                     f"'{bldg_layer.name()}'"
                     + (f", per EA by '{gc_field}'…" if gc_field else " (no geocode field: case totals only)…"))
            n_in = n_tagged = 0
            for i, case in enumerate(with_geom):
                if i % 25 == 0:
                    self.tick(total + i, total * 2, f"Counting buildings ({i:,}/{total:,})…")
                case.bldg_live, case.bldg_untagged = 0, 0
                case.bldg_geoms = []
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
                geom = QgsGeometry(case.geom)
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
                    # kept in the MBI CRS to count the buildings in each EA's kept part
                    case.bldg_geoms.append(QgsGeometry.fromPointXY(xform.transform(
                        p.geometry().centroid().asPoint(), QgsCoordinateTransform.ReverseTransform)))
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
            ordered = sorted(self.cases.values(),
                             key=lambda c: (c.level, -c.bldg_pts, c.case_uuid, c.key))
            for order, case in enumerate(ordered):
                top = TreeItem()
                top.setData(COL_ID, ROLE_ORDER, order)
                top.setText(COL_ID, f"{case.mbi_type or 'MBI'} · {case.short_id}")
                top.setToolTip(COL_ID, case.case_uuid)
                top.setData(COL_ID, ROLE_UUID, case.key)
                top.setText(COL_LEVEL, case.level_label)
                top.setToolTip(COL_LEVEL, case.level)
                paint(top, COL_LEVEL, LEVEL_COLOURS.get(case.level[:2]))
                n = len(case.candidates)
                if self.bgy_mode:
                    bgy = ", ".join(p for p in (case.bgy_name or case.bgy_code, case.bgy_city) if p)
                    tip = f"Covering barangay (MBI Barangay Selector): {case.bgy_label}"
                    if case.bgy_shared:
                        share = case.area / case.mbi_area if case.mbi_area else 0
                        bgy += f" · its part ({_pct(share)})"
                        tip += (f"\nThe MBI is shared out between {len(case.bgy_codes)} barangays "
                                f"({', '.join(case.bgy_codes)}); this case is the part "
                                f"{case.bgy_code} keeps: {self._area_text(case.area)}.")
                    top.setText(COL_BGY, bgy)
                    top.setToolTip(COL_BGY, tip)
                    top.setText(COL_NAME, f"{n} EA{'s' if n != 1 else ''} of the barangay" if n
                                else "No EA found in the selected EA layers")
                    if not n:
                        top.setToolTip(COL_NAME, f"No EA of {case.bgy_label} is in the ticked EA layers "
                                                 "near this MBI — tick its EA layer, then Load again.")
                        top.setForeground(COL_NAME, QBrush(QColor("#c62828")))
                else:
                    top.setText(COL_BGY, case.barangays)
                    top.setToolTip(COL_BGY, case.barangays)
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
                    child.setData(COL_ID, ROLE_UUID, case.key)
                    child.setData(COL_ID, ROLE_EA, cand.code)
                    place = ", ".join(p for p in (cand.barangay, cand.city_mun) if p)
                    if cand.dist is not None and cand.dist > 0.05:
                        place = f"{cand.dist:,.1f} m away" + (f" · {place}" if place else "")
                        child.setToolTip(COL_BGY, f"{cand.code} does not touch this MBI: it is "
                                                  f"{cand.dist:,.1f} m from it.")
                    elif cand.dist is not None and cand.part is None:
                        child.setToolTip(COL_BGY, f"{cand.code} borders this MBI.")
                    child.setText(COL_BGY, place)
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
                self.case_items[case.key] = top
                self.paint_case(case)

            self.tree.setSortingEnabled(True)
            self.tree.sortByColumn(COL_LEVEL, Qt.AscendingOrder)
            self.tree.expandAll()
            for col in range(len(TREE_COLS)):
                self.tree.resizeColumnToContents(col)
            self.tree.setColumnWidth(COL_BGY, min(self.tree.columnWidth(COL_BGY), 220))
            self._painting = False
            self._fill_show_combo({c.level for c in self.cases.values() if c.level},
                                  {c.mbi_type for c in self.cases.values() if c.mbi_type})
            self.apply_filter()

        def _area_text(self, m2):
            return f"{m2 / 10000:,.2f} ha" if m2 >= 10000 else f"{m2:,.1f} m²"

        def _covered_by(self, case, long=False):
            # EA names repeat across barangays ("EA 000000"), so short labels use the code.
            cands = [case.candidates[c] for c in case.assigned]
            if len(cands) == 1 and not long:
                return cands[0].label
            if len(cands) > 2 and not long:
                return f"{len(cands)} EAs"
            return ", ".join(c.label if long else c.code for c in cands)

        def paint_case(self, case):
            top = self.case_items.get(case.key)
            if top is None:
                return
            was_painting, self._painting = self._painting, True
            on_text, off_text, on_tip, off_tip = role_texts(case.mbi_type, "EA")
            for i in range(top.childCount()):
                child = top.child(i)
                code = child.data(COL_ID, ROLE_EA)
                cand = case.candidates.get(code)
                if cand is None:
                    continue
                tip = ""
                if case.split:
                    tip = (f"Part of this MBI inside {cand.code}: {self._area_text(cand.part_area)} "
                           f"({_pct(cand.part_area / case.area if case.area else 0)} of the MBI)")
                if code in case.assigned:
                    child.setCheckState(COL_ROLE, Qt.Checked)
                    if case.split:
                        share = cand.kept_area / case.area if case.area else 0
                        text = (f"✔ Covers its part ({_pct(share)} of the MBI)" if cand.kept_area > SLIVER_M2
                                else "✔ Covers nothing — an EA ticked earlier keeps its part")
                        if cand.kept_bldg:
                            text += f" · {cand.kept_bldg:,} bldg"
                        tip += (f"\nThis EA keeps {self._area_text(cand.kept_area)} of the MBI"
                                + (" (the rest of its part is kept by an EA ticked before it)"
                                   if cand.kept_area < cand.part_area - SLIVER_M2 else ""))
                    else:
                        text = on_text
                        tip += on_tip
                    child.setText(COL_ROLE, text)
                    paint(child, COL_ROLE, COLOUR_COVERED)
                elif case.assigned and cand.released >= 0.995:
                    child.setCheckState(COL_ROLE, Qt.Unchecked)
                    child.setText(COL_ROLE, off_text)
                    tip += off_tip
                    paint(child, COL_ROLE, COLOUR_RELEASED)
                elif case.assigned and cand.released > 0.005:
                    child.setCheckState(COL_ROLE, Qt.Unchecked)
                    child.setText(COL_ROLE, f"Gives up {_pct(cand.released)} · tick to keep the rest")
                    tip += (f"\n{_pct(cand.released)} of its part is kept by the ticked EA(s); "
                            "tick it to keep the rest.")
                    paint(child, COL_ROLE, None)
                else:
                    child.setCheckState(COL_ROLE, Qt.Unchecked)
                    child.setText(COL_ROLE, TEXT_CHOOSE.format(unit="EA"))
                    tip += f"\nTick this box if this EA should cover the {(case.mbi_type or 'MBI').lower()}."
                    paint(child, COL_ROLE, None)
                child.setToolTip(COL_ROLE, tip.strip())
            status = case.status
            if status == "covered":
                top.setText(COL_ROLE, f"✔ Done — covered by {self._covered_by(case)}")
                top.setToolTip(COL_ROLE, f"Covered by {self._covered_by(case, True)}.\n"
                                         "Untick it below to undo, or tick another EA to change it.")
                paint(top, COL_ROLE, COLOUR_COVERED)
            elif status == "partial":
                top.setText(COL_ROLE, f"Partly covered ({_pct(case.covered)}) — tick another EA")
                top.setToolTip(COL_ROLE, f"{_pct(case.covered)} of the MBI is kept by "
                                         f"{self._covered_by(case, True)}.\n"
                                         f"{self._area_text(case.area * (1 - case.covered))} has no "
                                         "covering EA yet — tick another EA to cover it.")
                paint(top, COL_ROLE, COLOUR_PARTIAL)
            elif not case.candidates:
                top.setText(COL_ROLE, "Pending — no EA found to choose")
                top.setToolTip(COL_ROLE, "No EA of the covering barangay was found in the ticked EA "
                                         "layers.\nTick its EA layer, then Load again.")
                paint(top, COL_ROLE, COLOUR_PENDING)
            else:
                top.setText(COL_ROLE, "Pending — tick the covering EA below")
                top.setToolTip(COL_ROLE, "Tick the covering EA(s) below."
                               + ("\nShared overlap: each ticked EA keeps only its own part."
                                  if case.split else "")
                               + (f"\nOnly EAs of {case.bgy_label} (the covering barangay) are listed."
                                  if self.bgy_mode else ""))
                paint(top, COL_ROLE, COLOUR_PENDING)
            self._painting = was_painting

        def on_item_changed(self, item, column):
            if self._painting or column != COL_ROLE or item.parent() is None:
                return
            case = self.cases.get(item.data(COL_ID, ROLE_UUID))
            if case is None:
                return
            self.toggle_ea(case, item.data(COL_ID, ROLE_EA), item.checkState(COL_ROLE) == Qt.Checked)

        def toggle_ea(self, case, code, on):
            """Tick / untick a covering EA. Shared overlaps keep every ticked EA (each keeps
            its own part); otherwise ticking an EA moves the coverage to it."""
            if on:
                if code in case.assigned:
                    return
                codes = case.assigned + [code] if case.split else [code]
            else:
                codes = [c for c in case.assigned if c != code]
            self.set_assignment(case, codes)

        def set_assignment(self, case, codes, quiet=False):
            codes = list(codes)
            if case.assigned == codes:
                return
            case.assigned = codes
            case.resolve(self.da)
            self.paint_case(case)
            if not quiet:
                status = case.status
                self.log(f"{case.mbi_type or 'MBI'} {case.short_id} → "
                         + ("pending" if status == "pending" else
                            f"covered by {self._covered_by(case, True)}" if status == "covered" else
                            f"partly covered ({_pct(case.covered)}) by {self._covered_by(case, True)}"))
                self.filter_item(case)
                self.update_summary()
                self.on_selection_changed()

        def reset_choices(self):
            if not any(c.assigned for c in self.cases.values()):
                return
            if QtWidgets.QMessageBox.question(
                    self, DIALOG_TITLE, "Clear every EA tick in the preview?\n"
                    "Nothing in the layer changes until you Apply Coverage.",
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel) != QtWidgets.QMessageBox.Yes:
                return
            for case in self.cases.values():
                self.set_assignment(case, [], quiet=True)
            self.log("All choices cleared.")
            self.apply_filter()

        # ──────────────── filter ────────────────
        def _case_visible(self, case):
            mode = self.show_combo.currentData()
            if mode in ("pending", "partial", "covered") and case.status != mode:
                return False
            if mode and mode.startswith("type:") and case.mbi_type != mode[5:]:
                return False
            if mode and mode.startswith("level:") and case.level != mode[6:]:
                return False
            text = self.search_edit.text().strip().lower()
            return not text or text in case.search_text

        def filter_item(self, case):
            top = self.case_items.get(case.key)
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
                    mbi_fids.update((cand.fids if cand else None) or case.feature_ids)
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
            self.show_bands()

        # Map highlights: green = part kept by a covering EA, dashed blue = the part an
        # unticked EA would keep, orange = part of the MBI no covering EA keeps yet.
        BAND_STYLES = {"kept": ((67, 160, 71, 110), "#1b5e20", Qt.SolidLine),
                       "part": ((30, 136, 229, 70), "#0d47a1", Qt.DashLine),
                       "left": ((251, 140, 0, 110), "#e65100", Qt.SolidLine)}

        def clear_bands(self):
            for band in self._bands.values():
                try:
                    band.reset(QgsWkbTypes.PolygonGeometry)
                    if self.iface is not None:
                        self.iface.mapCanvas().scene().removeItem(band)
                except RuntimeError:
                    pass
            self._bands = {}

        def _band(self, kind):
            band = self._bands.get(kind)
            if band is None:
                fill, stroke, style = self.BAND_STYLES[kind]
                band = QgsRubberBand(self.iface.mapCanvas(), QgsWkbTypes.PolygonGeometry)
                band.setFillColor(QColor(*fill))
                band.setStrokeColor(QColor(stroke))
                band.setWidth(2)
                band.setLineStyle(style)
                self._bands[kind] = band
            return band

        def show_bands(self):
            """Highlight on the map the part of the MBI each selected EA keeps (or would keep)."""
            if self.iface is None:
                return
            for band in self._bands.values():
                band.reset(QgsWkbTypes.PolygonGeometry)
            mbi = self.mbi_layer
            if mbi is None:
                return
            geoms = {"kept": [], "part": [], "left": []}
            for item in self.tree.selectedItems():
                case = self.cases.get(item.data(COL_ID, ROLE_UUID))
                if case is None:
                    continue
                code = item.data(COL_ID, ROLE_EA)
                if self.bgy_mode and case.bgy_shared and not code and case.geom is not None \
                        and not case.assigned:
                    geoms["part"].append(case.geom)   # the part of the MBI this barangay keeps
                if not case.split:
                    continue
                cands = [case.candidates.get(code)] if code else list(case.candidates.values())
                for cand in cands:
                    if cand is None:
                        continue
                    if cand.kept is not None:
                        geoms["kept"].append(cand.kept)
                    elif code and cand.part is not None:
                        geoms["part"].append(cand.part)
                if not code and case.status == "partial" and case.unassigned is not None:
                    geoms["left"].append(case.unassigned)
            for kind, gs in geoms.items():
                if gs:
                    band = self._band(kind)
                    for g in gs:
                        band.addGeometry(g, mbi.crs())

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
                if case is not None and code and case.split:
                    if code in case.assigned:
                        act = menu.addAction(f"Remove {code} from the covering EAs")
                        act.triggered.connect(lambda: self.toggle_ea(case, code, False))
                    else:
                        act = menu.addAction(f"Add {code} as covering EA (keeps its part)")
                        act.triggered.connect(lambda: self.toggle_ea(case, code, True))
                    act = menu.addAction(f"Make {code} the only covering EA")
                    act.setEnabled(case.assigned != [code])
                    act.triggered.connect(lambda: self.set_assignment(case, [code]))
                elif case is not None and code:
                    act = menu.addAction(f"Set {code} as covering EA")
                    act.setEnabled(case.assigned != [code])
                    act.triggered.connect(lambda: self.set_assignment(case, [code]))
                if case is not None:
                    act = menu.addAction("Mark case as pending")
                    act.setEnabled(bool(case.assigned))
                    act.triggered.connect(lambda: self.set_assignment(case, []))
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
            single = by_bldg = shared = left = 0
            for case in self.cases.values():
                if case.assigned or not case.candidates:
                    continue
                cands = list(case.candidates.values())
                if len(cands) == 1:
                    self.set_assignment(case, [cands[0].code], quiet=True)
                    single += 1
                elif not all(c.bldg is not None for c in cands):
                    left += 1
                elif case.split:
                    # Every EA with tagged buildings keeps its part, most buildings first.
                    owners = sorted((c for c in cands if c.bldg), key=lambda c: -c.bldg)
                    if owners:
                        self.set_assignment(case, [c.code for c in owners], quiet=True)
                        shared += 1
                    else:
                        left += 1
                else:
                    most = max(c.bldg for c in cands)
                    leaders = [c for c in cands if c.bldg == most]
                    if most > 0 and len(leaders) == 1:
                        self.set_assignment(case, [leaders[0].code], quiet=True)
                        by_bldg += 1
                    else:
                        left += 1
            partial = sum(1 for c in self.cases.values() if c.status == "partial")
            self.log(f"Auto-assign: {single:,} single-EA case(s), {by_bldg:,} to the EA owning most "
                     "of the buildings inside the MBI"
                     + (f", {shared:,} shared overlap(s) to every EA with tagged buildings "
                        "(each keeps its part)" if shared else "")
                     + (f", {left:,} left pending (no tagged buildings, a tie, or no building "
                        "counts)" if left else "")
                     + (f". {partial:,} case(s) are only partly covered — review them "
                        "(Show: Partly covered)." if partial else ""))
            self.apply_filter()
            self.on_selection_changed()

        # ──────────────── apply ────────────────
        def _case_status(self, case):
            return {"covered": STATUS_COVERED, "partial": STATUS_PARTIAL}.get(
                case.status, STATUS_NOT_COVERED)

        def _row_role(self, case, cand):
            """Covers: a covering EA. Releases: everything this EA could keep is kept by
            the covering EA(s). None: pending, or part of it is still undecided."""
            if cand.code in case.assigned:
                return ROLE_COVERS
            if case.assigned and cand.released >= 0.995:
                return ROLE_RELEASES
            return None

        def _status_counts(self):
            counts = {"pending": 0, "partial": 0, "covered": 0}
            for case in self.cases.values():
                counts[case.status] += 1
            return counts

        def _needs_apply(self, layer):
            """True when the viewer holds choices the layer does not have yet (or the layer
            is missing one of the coverage fields)."""
            if any(c.assigned != c.saved for c in self.cases.values()):
                return True
            return any(layer.fields().indexOf(n) < 0 for n in self._written_fields())

        def _written_fields(self):
            """Barangay mode has no EA rows, so no 'ea_role'."""
            if self.bgy_mode:
                return (self.coverage_field(), self.status_field())
            return (self.coverage_field(), ROLE_FIELD, self.status_field())

        # ──────────────── coverage parts (MBI clipped to each covering EA) ────────────────
        PART_FIELDS = [("case_uuid", QVariant.String), ("mbi_type", QVariant.String),
                       ("mbi_level", QVariant.String), ("part_role", QVariant.String),
                       ("ea_geocode", QVariant.String), ("ea_name", QVariant.String),
                       ("bgy_geocode", QVariant.String),
                       ("barangay", QVariant.String), ("city_mun", QVariant.String),
                       ("released_by", QVariant.String), ("area_m2", QVariant.Double),
                       ("pct_of_mbi", QVariant.Double), ("num_bldg_pts", QVariant.Int)]

        def build_parts_layer(self, crs, name=PARTS_LAYER_NAME, kept_only=False):
            """Memory layer, one feature per covering EA holding the part of the MBI it keeps
            ('Kept'), plus one 'Unassigned' feature per case for the part no covering EA
            keeps yet. Together they cover every loaded MBI exactly once.
            kept_only: only the 'Kept' parts (the MBI clipped to each ticked EA)."""
            uri = "MultiPolygon" + (f"?crs={crs.authid()}" if crs.authid() else "")
            out = QgsVectorLayer(uri, name, "memory")
            out.setCrs(crs)
            out.dataProvider().addAttributes([QgsField(n, t) for n, t in self.PART_FIELDS])
            out.updateFields()
            feats = []

            def add(case, geom, role, cand, released_by, area, bldg):
                g = QgsGeometry(geom)
                g.convertToMultiType()
                f = QgsFeature(out.fields())
                f.setGeometry(g)
                f.setAttributes([
                    case.case_uuid, case.mbi_type or None, case.level or None, role,
                    cand.code if cand else None, (cand.name or None) if cand else None,
                    ((cand.geocode if cand else "") or case.bgy_code) or None,
                    (cand.barangay if cand else case.bgy_name) or None,
                    (cand.city_mun if cand else case.bgy_city) or None,
                    ",".join(released_by) or None, round(area, 2),
                    round(100 * area / case.area, 2) if case.area else None, bldg])
                feats.append(f)

            for case in sorted(self.cases.values(), key=lambda c: (c.level, c.case_uuid)):
                if case.geom is None:
                    continue
                kept_bldg = 0
                for code in case.assigned:
                    cand = case.candidates[code]
                    if cand.kept is None or (case.split and cand.kept_area <= SLIVER_M2):
                        continue
                    if case.split:
                        released_by = [c.code for c in case.candidates.values()
                                       if c is not cand and c.part is not None
                                       and _area(_polygonal(c.part.intersection(cand.kept)),
                                                 self.da) > SLIVER_M2]
                        bldg = cand.kept_bldg
                    else:
                        released_by = [c for c in case.candidates if c != code]
                        bldg = case.bldg_pts if cand.kept_bldg is None else cand.kept_bldg
                    kept_bldg += bldg or 0
                    add(case, cand.kept, PART_KEPT, cand, released_by, cand.kept_area, bldg)
                if not kept_only and case.status != "covered" and case.unassigned is not None:
                    if case.bldg_geoms is not None:
                        bldg = len(case.bldg_geoms) - kept_bldg
                    else:
                        bldg = case.bldg_pts if not case.assigned else None
                    add(case, case.unassigned, PART_UNASSIGNED, None, [],
                        _area(case.unassigned, self.da), bldg)
            out.dataProvider().addFeatures(feats)
            out.updateExtents()
            return out

        def _style_parts(self, layer):
            cats = []
            for value, fill, stroke in ((PART_KEPT, "67,160,71,120", "27,94,32,255"),
                                        (PART_UNASSIGNED, "251,140,0,120", "230,81,0,255")):
                sym = QgsFillSymbol.createSimple({"color": fill, "outline_color": stroke,
                                                  "outline_width": "0.6"})
                cats.append(QgsRendererCategory(value, sym, value))
            layer.setRenderer(QgsCategorizedSymbolRenderer("part_role", cats))

        def clipped_layer_name(self):
            """'ppp_mbi_ea_clipped' — ppp = province code(s) of the covering barangays (first 3
            geocode digits; several joined with '_'), else taken from the MBI layer's name."""
            def prov(code):
                s = _code_key(code)
                if not s.isdigit():
                    return ""
                s = s.zfill(14) if len(s) > 9 else s   # leading zero lost: 2709007000000
                return s[:3]
            provs = sorted({p for p in (prov(c.bgy_code) for c in self.cases.values()) if p})
            if provs:
                return "_".join(provs + ["mbi", CLIPPED_SUFFIX])
            base = self.mbi_layer.name() if self.mbi_layer is not None else "mbi"
            return f"{base}_{CLIPPED_SUFFIX}"

        def update_clipped_layer(self, mbi_layer):
            """Add (or replace) the 'ppp_mbi_ea_clipped' scratch layer: every MBI clipped to the
            boundary of each ticked EA — one feature per covering EA (a shared overlap ticked to
            two EAs becomes two features). Pending cases and uncovered leftovers are left out."""
            name = self.clipped_layer_name()
            new = (self.build_clipped_rows(mbi_layer, name) if self.bgy_mode
                   else self.build_parts_layer(mbi_layer.crs(), name=name, kept_only=True))
            prj = QgsProject.instance()
            old = [l.id() for l in prj.mapLayersByName(name) if l.dataProvider().name() == "memory"]
            if old:
                prj.removeMapLayers(old)
            self._style_clipped(new)
            prj.addMapLayer(new)
            cases = len({f["case_uuid"] for f in new.getFeatures()})
            s_name = self.status_field()
            covered = (sum(1 for f in new.getFeatures() if f[s_name] == STATUS_COVERED)
                       if s_name in new.fields().names() else new.featureCount())
            self.log(f"  '{name}': {new.featureCount():,} row(s) of {cases:,} MBI(s) — "
                     f"{covered:,} clipped to a ticked EA ({STATUS_COVERED}), "
                     f"{new.featureCount() - covered:,} {STATUS_NOT_COVERED} "
                     "(temporary layer — right-click > Make Permanent to keep it)")

        def build_clipped_rows(self, mbi_layer, name):
            """Same fields as the ppp_mbi layer. Every row of the loaded MBIs is copied, except
            that a covering barangay's row is replaced by ONE ROW PER TICKED EA: the MBI clipped
            to that EA, with only that EA in the coverage field and 1_Covered. The part no ticked
            EA keeps (Partly covered) is one more row with no EA and 2_Not Covered; rows of
            barangays not chosen and pending cases keep their geometry with 2_Not Covered."""
            from qgis.core import QgsFeatureRequest
            crs = mbi_layer.crs()
            uri = "MultiPolygon" + (f"?crs={crs.authid()}" if crs.authid() else "")
            out = QgsVectorLayer(uri, name, "memory")
            out.setCrs(crs)
            out.dataProvider().addAttributes(mbi_layer.fields().toList())
            out.updateFields()
            names = out.fields().names()
            f_name, s_name = self.coverage_field(), self.status_field()

            by_fid, every = {}, set()
            for case in self.cases.values():
                for fid in case.feature_ids:
                    by_fid.setdefault(fid, []).append(case)
                every.update(case.all_fids or case.feature_ids)

            feats = []

            def add(src, geom, ea_code, status):
                if geom is None or geom.isEmpty():
                    return
                g = QgsGeometry(geom)
                g.convertToMultiType()
                f = QgsFeature(out.fields())
                f.setGeometry(g)
                attrs = list(src.attributes())
                if f_name in names:
                    attrs[names.index(f_name)] = ea_code
                if s_name in names:
                    attrs[names.index(s_name)] = status
                f.setAttributes(attrs)
                feats.append(f)

            for src in mbi_layer.getFeatures(QgsFeatureRequest().setFilterFids(list(every))):
                subs = by_fid.get(src.id(), [])
                if not any(s.assigned for s in subs):
                    add(src, src.geometry(), None, STATUS_NOT_COVERED)
                    continue
                for case in subs:
                    for code in case.assigned:
                        cand = case.candidates.get(code)
                        if cand is None or cand.kept is None or \
                                (case.split and cand.kept_area <= SLIVER_M2):
                            continue
                        add(src, cand.kept, code, STATUS_COVERED)
                    if not case.assigned:
                        add(src, case.geom, None, STATUS_NOT_COVERED)
                    elif case.status != "covered" and case.unassigned is not None:
                        add(src, case.unassigned, None, STATUS_NOT_COVERED)
            out.dataProvider().addFeatures(feats)
            out.updateExtents()
            return out

        def _style_clipped(self, layer):
            """Two legend entries whatever the number of EAs: covered (green) / not covered (grey,
            dashed). Each EA's part is its own feature, so the dark outlines show the EA split."""
            field = self.status_field()
            if field not in layer.fields().names():     # EA mode layer (part_role)
                field, covered, not_covered = "part_role", PART_KEPT, PART_UNASSIGNED
            else:
                covered, not_covered = STATUS_COVERED, STATUS_NOT_COVERED
            cats = [
                QgsRendererCategory(covered, QgsFillSymbol.createSimple(
                    {"color": "102,187,106,150", "outline_color": "27,94,32,255",
                     "outline_width": "0.5"}), f"{covered} (clipped to its EA)"),
                QgsRendererCategory(not_covered, QgsFillSymbol.createSimple(
                    {"color": "158,158,158,90", "outline_color": "97,97,97,255",
                     "outline_width": "0.4", "outline_style": "dash"}), not_covered),
            ]
            layer.setRenderer(QgsCategorizedSymbolRenderer(field, cats))

        def remove_parts_layer(self):
            """Remove the temporary 'MBI Coverage Parts' layer (made by earlier versions of this
            tool) from the project. The parts are only written to the export GeoPackage."""
            prj = QgsProject.instance()
            ids = [l.id() for l in prj.mapLayersByName(PARTS_LAYER_NAME)
                   if l.dataProvider().name() == "memory"]
            if self.parts_layer_id and prj.mapLayer(self.parts_layer_id) is not None:
                ids.append(self.parts_layer_id)
            if ids:
                prj.removeMapLayers(list(set(ids)))
                self.log(f"  Removed the temporary '{PARTS_LAYER_NAME}' layer")
            self.parts_layer_id = None

        def update_parts_layer(self, mbi_layer):
            """Add (or refresh) the MBI Coverage Parts layer in the project."""
            new = self.build_parts_layer(mbi_layer.crs())
            prj = QgsProject.instance()
            old = prj.mapLayer(self.parts_layer_id) if self.parts_layer_id else None
            if old is not None and old.dataProvider().name() == "memory":
                old.dataProvider().truncate()
                old.dataProvider().addFeatures(list(new.getFeatures()))
                old.updateExtents()
                old.triggerRepaint()
                layer = old
            else:
                self._style_parts(new)
                prj.addMapLayer(new)
                self.parts_layer_id = new.id()
                layer = new
            kept = sum(1 for f in layer.getFeatures() if f["part_role"] == PART_KEPT)
            self.log(f"  '{layer.name()}': {kept:,} kept part(s), "
                     f"{layer.featureCount() - kept:,} unassigned part(s) (temporary layer)")

        def apply_coverage(self):
            layer = self.mbi_layer
            if self.running:
                return
            if layer is None:
                QtWidgets.QMessageBox.warning(
                    self, DIALOG_TITLE, "The MBI layer is no longer in the project — load again.")
                return
            n = self._status_counts()
            fields = "', '".join(self._written_fields())
            msg = (f"Write '{fields}' for all {len(self.cases):,} case(s) into '{layer.name()}'?\n\n"
                   + (f"Written on the covering barangay's row(s); the rows of barangays not chosen "
                      f"get {STATUS_NOT_COVERED} and no EA. '{BGY_COVERAGE_FIELD}' is not "
                      "changed.\n\n" if self.bgy_mode else "")
                   + f"{n['covered']:,} case(s) → {STATUS_COVERED}\n"
                   + (f"{n['partial']:,} case(s) → {STATUS_PARTIAL}\n" if n["partial"] else "")
                   + f"{n['pending']:,} case(s) → {STATUS_NOT_COVERED}")
            if QtWidgets.QMessageBox.question(
                    self, DIALOG_TITLE, msg,
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel) != QtWidgets.QMessageBox.Yes:
                return
            self._write_coverage(layer)

        def _write_bgy_rows(self, layer, cases, f_idx, s_idx):
            """Barangay mode: each covering barangay's row(s) get the EA(s) chosen for its case
            and that case's status; rows of barangays not chosen get no EA, 2_Not Covered.
            A row shared by several covering barangays (one row per MBI) gets all their EAs."""
            by_fid, every = {}, set()
            for case in cases:
                for fid in case.feature_ids:
                    by_fid.setdefault(fid, []).append(case)
                every.update(case.all_fids)
            rows = 0
            for i, (fid, subs) in enumerate(by_fid.items()):
                codes = [c for s in subs for c in s.assigned]
                statuses = {self._case_status(s) for s in subs}
                status = (statuses.pop() if len(statuses) == 1
                          else STATUS_PARTIAL if codes else STATUS_NOT_COVERED)
                layer.changeAttributeValues(fid, {f_idx: ",".join(codes) or None, s_idx: status})
                rows += 1
                if i % 200 == 0:
                    self.progress.setValue(int(100 * i / max(len(by_fid), 1)))
                    QtWidgets.QApplication.processEvents()
            for fid in every - set(by_fid):
                layer.changeAttributeValues(fid, {f_idx: None, s_idx: STATUS_NOT_COVERED})
                rows += 1
            return rows

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
                for name in self._written_fields():
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
                if self.bgy_mode:
                    rows = self._write_bgy_rows(layer, cases, f_idx, s_idx)
                for i, case in enumerate(cases if not self.bgy_mode else []):
                    role_by_fid = {}
                    for cand in case.candidates.values():
                        role = self._row_role(case, cand)
                        for fid in cand.fids:
                            role_by_fid[fid] = role
                    for fid in case.feature_ids:
                        layer.changeAttributeValues(fid, {
                            f_idx: ",".join(case.assigned) or None,
                            r_idx: role_by_fid.get(fid),
                            s_idx: self._case_status(case)})
                        rows += 1
                    if i % 200 == 0:
                        self.progress.setValue(int(100 * i / max(len(cases), 1)))
                        QtWidgets.QApplication.processEvents()
                layer.endEditCommand()
                for case in cases:
                    case.saved = list(case.assigned)
                layer.triggerRepaint()
                n = self._status_counts()
                self.log(f"  Updated {rows:,} row(s) across {len(cases):,} case(s): "
                         f"{n['covered']:,} {STATUS_COVERED}, "
                         + (f"{n['partial']:,} {STATUS_PARTIAL}, " if n["partial"] else "")
                         + f"{n['pending']:,} {STATUS_NOT_COVERED}")
                self.log(f"  Changes are UNSAVED edits in '{layer.name()}'. "
                         "Save: Toggle Editing > Save Edits. Undo: Edit > Undo.")
                self.remove_parts_layer()   # old 'MBI Coverage Parts' layer of earlier versions
                try:
                    self.update_clipped_layer(layer)
                except Exception as e:
                    self.log(f"  Could not build the clipped EA MBI layer: {e}")
                self.progress.setValue(100)
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

            n = self._status_counts()
            open_note = ""
            if n["pending"]:
                open_note += (f"\n{n['pending']:,} case(s) are still pending and will be exported "
                              f"as {STATUS_NOT_COVERED}.")
            if n["partial"]:
                open_note += (f"\n{n['partial']:,} case(s) are only partly covered and will be "
                              f"exported as {STATUS_PARTIAL}.")
            if self._needs_apply(layer):
                msg = ("The choices in the viewer are not in the MBI layer yet.\n"
                       "Apply Coverage first, then export?")
                if open_note:
                    msg += "\n" + open_note
                if QtWidgets.QMessageBox.question(
                        self, DIALOG_TITLE, msg,
                        QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel) != QtWidgets.QMessageBox.Yes:
                    return
                if not self._write_coverage(layer):
                    return
            elif open_note and QtWidgets.QMessageBox.question(
                    self, DIALOG_TITLE, open_note.strip() + "\n\nExport anyway?",
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
            context = QgsProject.instance().transformContext()
            res = QgsVectorFileWriter.writeAsVectorFormatV3(layer, path, context, options)
            if res[0] != QgsVectorFileWriter.NoError:
                raise RuntimeError(f"GPKG export failed: {res[1]}")
            self.log(f"  GPKG written: {path} ({layer.featureCount():,} row(s))")

            parts = self.build_parts_layer(layer.crs())
            options = QgsVectorFileWriter.SaveVectorOptions()
            options.driverName = "GPKG"
            options.layerName = PARTS_EXPORT_NAME
            options.fileEncoding = "UTF-8"
            options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteLayer
            res = QgsVectorFileWriter.writeAsVectorFormatV3(parts, path, context, options)
            if res[0] != QgsVectorFileWriter.NoError:
                raise RuntimeError(f"GPKG export of the coverage parts failed: {res[1]}")
            self.log(f"    + layer '{PARTS_EXPORT_NAME}' ({parts.featureCount():,} part(s))")

        def export_csv(self, path):
            field, status_field = self.coverage_field(), self.status_field()

            def m2(value):
                return "" if value is None else round(value, 2)

            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["case_uuid", "mbi_type", "mbi_level", "mbi_area_m2", "num_bldg_pts",
                            "covering_bgy", "bgy_part_m2",
                            "ea_code", "ea_name", "barangay", "city_mun", "ea_dist_m", "ea_bldg_in_mbi",
                            "ea_part_m2", "ea_kept_m2", "ea_kept_bldg", ROLE_FIELD,
                            field, status_field])
                for case in sorted(self.cases.values(), key=lambda c: (c.level, c.case_uuid, c.key)):
                    head = [case.case_uuid, case.mbi_type, case.level, m2(case.mbi_area or case.area),
                            case.bldg_pts, case.bgy_code,
                            m2(case.area) if self.bgy_mode and case.bgy_shared else ""]
                    if not case.candidates:
                        w.writerow(head + [""] * 10 + [",".join(case.assigned), self._case_status(case)])
                    for cand in case.candidates.values():
                        chosen = cand.code in case.assigned
                        w.writerow(head + [cand.code, cand.name, cand.barangay, cand.city_mun,
                                    "" if cand.dist is None else round(cand.dist, 1),
                                    "" if cand.bldg is None else cand.bldg,
                                    m2(cand.part_area) if case.split else "",
                                    m2(cand.kept_area) if chosen else "",
                                    "" if not chosen or cand.kept_bldg is None else cand.kept_bldg,
                                    self._row_role(case, cand) or "",
                                    ",".join(case.assigned), self._case_status(case)])
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
