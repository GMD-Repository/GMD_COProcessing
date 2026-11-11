r"""MBI Barangay Coverage — counter-check the remaining MBIs, then choose the covering barangay.

The remaining MBIs (the gaps / overlaps the PSOs declared pending, e.g. the 'remaining_cases'
layer of GEMMA's MBI Validator GeoPackage, Disputed cases included) are counter-checked with a
fresh run of the built-in MBI Checker (a copy of GEMMA's MBI Checker v13 — GEMMA does not need
to be installed) on the barangay layers:

  • Still detected     — the check still finds the remaining MBI (shares area with it).
  • Newly detected     — the check finds an MBI that is not in the remaining layer
                         (light green row in the preview).
  • No longer detected — a remaining MBI the check does not find any more (nothing to cover).

Every detected MBI involves two or more barangays, possibly from different barangay layers.
The preview lists each MBI with all of its involved barangays, so you can tick the barangay
that covers it:

  • Overlap — the ticked barangay keeps the overlap; the other barangays release (clip) it.
  • Gap     — the ticked barangay absorbs the gap; the other barangays are not extended.

Barangays are matched on the barangay geocode ('geocode' / 'involved_areas' in the MBI layer,
'geocode' / 'bgy_geocode' in the barangay layers). Several EAs of one barangay in an MBI case
count as one barangay.

Workflow:
  Step 1  Pick the Remaining MBI layer (or Browse GPKG… to load 'remaining_cases' from a
          ref_mbi_reviewed GeoPackage).
  Step 2  Select every barangay layer the involved barangays come from (one or more).
  Step 3  Pick the building point layer (and its geocode field): the MBI Checker counts the
          buildings inside each MBI, and per involved barangay the ones tagged to it are
          counted (the field may hold the barangay geocode or an EA geocode that starts with it).
  Step 4  Click Run Check & Counter-Check — runs the built-in MBI Checker on the selected barangay
          layers, compares each detection with the remaining MBIs, adds one "ppp_mbi"
          layer ('detection', 'ref_case_uuid', 'ref_status', 'ref_remarks') and lists its cases.
          Or pick an existing MBI layer (needs 'case_uuid') and click Load MBI Cases.
          'Skip within-barangay cases' leaves out cases that involve a single barangay.
  Step 5  Tick the Role / Status box of the covering barangay in each case.
          Shared overlaps: when an overlap runs across several barangays (and their layers
          are selected), more than one barangay can be ticked — each keeps only its own part
          of the overlap (the MBI clipped to that barangay, minus what a barangay ticked
          before it keeps). The case is Partly Covered until those parts fill the MBI.
          Auto-Assign Pending: single-barangay cases go to that barangay, multi-barangay
          cases go to the barangay owning most of the buildings inside the MBI (needs
          Step 3); shared overlaps go to every barangay with tagged buildings, most first.
          No-longer-detected MBIs have nothing to cover and cannot be ticked.
  Step 6  Apply Coverage writes 'assigned_bgy' (covering barangay geocodes, comma-separated)
          into the MBI layer. No coverage status is written here — the MBI EA Selector writes
          it. Unsaved, undoable edits; no-longer-detected MBIs are untouched.
          An MBI between TWO barangays goes to one of them (ticking one unticks the other);
          only an overlap across THREE OR MORE barangays can be shared out (several ticks).
  Step 7  Join MBI to Barangays makes a 'Barangays + MBI' layer: the ticked barangay layers with
          every kept MBI part added as its own feature carrying the covering barangay's attributes,
          flagged 'bgy_part' = 'MBI' / 'is_mbi' = 1 and drawn hatched magenta (kept parts are cut
          out of the barangay features, so released overlaps leave the other barangays).
  Step 8  Export GPKG + CSV writes the processed MBI layer, the coverage parts, the joined
          barangays and a decision log CSV into the Export folder (applying pending choices first).
  Step 9  Choose EAs in MBI EA Selector… applies pending choices and opens the MBI EA Selector
          with this layer in barangay mode: each MBI is listed under its covering barangay
          ('assigned_bgy') and only that barangay's EAs can be chosen to cover it.
"""
# Menu / panel entry (read by the GMD CO Processing plugin)
TOOL_NAME = "MBI Barangay Selector"
TOOL_GROUP = "EA Tools"
TOOL_ICON = "icons/mbi_bgy_selector.svg"

import csv
import os
import sys
import traceback
from datetime import datetime

# ===== MBI layer fields we read (written by EA_topochecker.py) =====
MBI_CASE_UUID = "case_uuid"
MBI_GEOCODE = "geocode"          # barangay geocode of the row's area
MBI_EA_NAME = "ea_name"          # EA name (only when the MBI layer was made from EA layers)
MBI_LEVEL = "mbi_level"
MBI_TYPE = "mbi_type"
MBI_NUM_BLDG = "num_bldg_pts"
MBI_BARANGAY = "barangay"
MBI_CITY_MUN = "city_mun"
MBI_PROVINCE = "province"
MBI_INVOLVED_AREAS = "involved_areas"   # comma-separated geocodes of every involved area
MBI_INVOLVED_BGYS = "involved_bgys"     # 'barangay, city;barangay, city' in the same order

# ===== Counter-check (built-in MBI Checker run vs the remaining MBI layer) =====
RUN_MODES = ["Gaps, Overlaps and Disputed", "Overlaps Only", "Gaps Only", "Disputed Only"]
DETECT_FIELD = "detection"
DETECT_STILL = "Still detected"        # the check still finds this remaining MBI
DETECT_NEW = "Newly detected"          # found by the check, not in the remaining layer
DETECT_GONE = "No longer detected"     # remaining MBI the check no longer finds
REF_UUID_FIELD = "ref_case_uuid"       # matched remaining MBI case_uuid(s)
REF_KIND_FIELD = "ref_mbi_type"        # their mbi_type (a Disputed case can match a gap / overlap)
REF_STATUS_FIELD = "ref_status"
REF_REMARKS_FIELD = "ref_remarks"
# The remaining layer is GEMMA's ref_mbi_cases or an MBI Validator output layer
# (remaining_cases / pending_cases); values are read from the first field present.
REF_TYPE_FIELDS = ["mbi_type", "case_type", "type"]
REF_STATUS_FIELDS = ["mbi_status", "ref_status", "status"]
REF_REMARKS_FIELDS = ["pso_remarks", "ref_remarks", "mbi_remarks", "remarks"]
REF_SOURCES = {"case_uuid": ["case_uuid", "uuid", "case_id"],
               "involved_bgys": ["involved_bgys", "ref_involved_bgys"],
               "num_bldg_pts": ["num_bldg_pts", "ref_num_bldg_pts"]}
GPKG_REMAINING_LAYERS = ["remaining_cases", "pending_cases", "ref_mbi_cases"]   # Browse GPKG picks
# A detection matches a remaining MBI when they share more than this area (m²) — touching
# along an edge only is not a match (as in GEMMA's MBI Validator).
MATCH_MIN_M2 = 0.01
# Output schema of the MBI Checker (Gaps / Overlaps / Disputed), kept in the counter-check layer
CHECKER_SCHEMA = [("case_uuid", "str"), ("geocode", "str"), ("region", "str"),
                  ("province", "str"), ("city_mun", "str"), ("barangay", "str"),
                  ("source", "str"), ("mbi_level", "str"), ("involved_areas", "str"),
                  ("involved_bgys", "str"), ("count_involved_areas", "int"),
                  ("mbi_type", "str"), ("num_bldg_pts", "int")]

# Fields we write
COVERAGE_FIELD = "assigned_bgy"   # default; editable in the panel
STATUS_FIELD = "coverage_status"  # default; editable in the panel
STATUS_COVERED = "1_Covered"
STATUS_NOT_COVERED = "2_Not Covered"
STATUS_PARTIAL = "3_Partially Covered"   # split overlap: part of it has no covering barangay yet

# Split overlaps: each covering barangay keeps only its own part of the MBI (MBI clipped to it)
# Join MBI to Barangays: barangay layers + each kept MBI part as its own, flagged feature
JOIN_LAYER_NAME = "Barangays + MBI"
JOIN_EXPORT_NAME = "mbi_bgy_joined"
JOIN_KIND_FIELD = "bgy_part"       # 'Barangay' (original area) / 'MBI' (joined MBI part)
JOIN_FLAG_FIELD = "is_mbi"         # 0 / 1, for filtering ("is_mbi" = 1)
JOIN_KIND_BGY = "Barangay"
JOIN_KIND_MBI = "MBI"
# MBI area left over after the chosen barangays' parts that still counts as covered (geometry
# rounding along barangay boundaries, not a real leftover): m², or this share of the MBI.
SLIVER_M2 = 0.5
SLIVER_SHARE = 0.001

# ===== Barangay layer fields =====
BGY_GEOCODE_FIELDS = ["geocode", "bgy_geocode"]
BGY_NAME_FIELDS = ["barangay", "bgy_name", "name"]
BGY_CITY_FIELDS = ["city_mun", "City/Municipality", "citymunicipality"]

# ===== Building point fields =====
BLDG_GEOCODE_FIELDS = ["bgy_geocode", "geocode", "ea_geocode"]   # auto-picked in this order

# Status colours (table cells; text is forced dark so it stays readable in dark themes)
COLOUR_PENDING = "#ffcc80"     # orange
COLOUR_COVERED = "#a5d6a7"     # green
COLOUR_RELEASED = "#eeeeee"    # light grey
COLOUR_PARTIAL = "#fff59d"     # yellow
COLOUR_NEW = "#dcedc8"         # light green: newly detected MBI (case row)
COLOUR_NEW_CHILD = "#f1f8e9"   # paler green: its barangay rows
COLOUR_GONE = "#e0e0e0"        # grey: remaining MBI no longer detected
COLOUR_JOINED = "#c51162"            # magenta hatch + dashed outline: MBI joined to a barangay
COLOUR_JOINED_FILL = "236,64,122,90"
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
    """Normalise a geocode for matching: '0200301300', 200301300 and 200301300.0 all give
    the same key."""
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


def _split_codes(text):
    """'a, b,c' -> ['a', 'b', 'c'] (the coverage field holds every covering barangay)."""
    return [c.strip() for c in str(text or "").split(",") if c.strip()]


def _feat_text(feature, candidates):
    """First non-empty value among the candidate fields (case-insensitive), else ''."""
    names = {f.name().lower(): f.name() for f in feature.fields()}
    for c in candidates:
        real = names.get(c.lower())
        if real:
            value = _text(feature, real)
            if value:
                return value
    return ""


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


class BgyCandidate:
    """One barangay involved in an MBI case."""

    __slots__ = ("code", "name", "city_mun", "province", "eas", "fids", "bldg",
                 "part", "part_area", "kept", "kept_area", "kept_bldg", "released")

    def __init__(self, code):
        self.code = code        # barangay geocode
        self.name = ""          # barangay name
        self.city_mun = ""
        self.province = ""
        self.eas = []           # EA names of this barangay in the case (EA MBI layers only)
        self.fids = []      # MBI rows where this barangay is "this area"
        self.bldg = None    # buildings inside the MBI tagged (by geocode) to this barangay
        self.part = None        # MBI ∩ barangay (MBI layer CRS): what it could keep
        self.part_area = 0.0    # m²
        self.kept = None        # what this barangay keeps with the current choices
        self.kept_area = 0.0
        self.kept_bldg = None   # building points inside 'kept'
        self.released = 0.0     # share of 'part' kept by other (covering) barangays

    @property
    def label(self):
        return f"{self.code} ({self.name})" if self.name else self.code

    @property
    def place(self):
        return ", ".join(p for p in (self.city_mun, self.province) if p)


class MBICase:
    """One MBI case_uuid: the discrepancy and all of its involved barangays."""

    def __init__(self, case_uuid, mbi_type=""):
        self.case_uuid = case_uuid
        self.mbi_type = mbi_type
        self.levels = set()
        self.num_bldg_pts = 0      # stored in the MBI layer by the topology checker
        self.bldg_live = None      # recounted from the Building Points layer (if set)
        self.bldg_untagged = 0     # of those, points whose geocode matches no involved barangay
        self.feature_ids = []
        self.candidates = {}     # code -> BgyCandidate (insertion ordered)
        self.assigned = []       # covering barangay code(s) in the order chosen, [] = pending
        self.saved = []          # value already stored in the layer
        self._saved_raw = ""
        self.search_text = ""
        self.geom = None         # MBI geometry (MBI layer CRS)
        self.area = 0.0          # m²
        self.split = False       # overlap shared out: each covering barangay keeps only its part
        self.bldg_geoms = None   # building points inside the MBI (MBI CRS), when counted
        self.covered = 0.0       # share of the MBI kept by the covering barangay(s)
        self.unassigned = None   # part of the MBI no covering barangay keeps yet
        self.detection = ""      # counter-check: Still / Newly / No longer detected ('' = none)
        self.ref_uuids = ""      # matched remaining MBI case_uuid(s)
        self.ref_kind = ""      # their mbi_type
        self.ref_status = ""
        self.ref_remarks = ""

    @property
    def gone(self):
        """A remaining MBI the check no longer finds: nothing to cover."""
        return self.detection == DETECT_GONE

    def add_feature(self, feat, coverage_field=None):
        self.feature_ids.append(feat.id())
        level = _text(feat, MBI_LEVEL)
        if level:
            self.levels.add(level)
        mbi_type = _norm_type(_text(feat, MBI_TYPE))
        if mbi_type:
            self.mbi_type = mbi_type
        self.detection = self.detection or _text(feat, DETECT_FIELD)
        self.ref_uuids = self.ref_uuids or _text(feat, REF_UUID_FIELD)
        self.ref_kind = self.ref_kind or _text(feat, REF_KIND_FIELD)
        self.ref_status = self.ref_status or _text(feat, REF_STATUS_FIELD)
        self.ref_remarks = self.ref_remarks or _text(feat, REF_REMARKS_FIELD)
        bldg = _field_value(feat, MBI_NUM_BLDG)
        try:
            self.num_bldg_pts = max(self.num_bldg_pts, int(float(bldg)))
        except (TypeError, ValueError):
            pass

        # The row's own barangay ("this area" of the pair)
        code = _text(feat, MBI_GEOCODE)
        if code:
            cand = self.candidates.get(code)
            if cand is None:
                cand = self.candidates[code] = BgyCandidate(code)
            cand.fids.append(feat.id())
            cand.name = cand.name or _text(feat, MBI_BARANGAY)
            cand.city_mun = cand.city_mun or _text(feat, MBI_CITY_MUN)
            cand.province = cand.province or _text(feat, MBI_PROVINCE)
            ea = _text(feat, MBI_EA_NAME)
            if ea and ea not in cand.eas:
                cand.eas.append(ea)

        # Every involved area (GEMMA's MBI Checker writes ONE row per MBI, listing the
        # involved geocodes in 'involved_areas' and 'barangay, city' labels in 'involved_bgys').
        labels = [s.strip() for s in _text(feat, MBI_INVOLVED_BGYS).split(";")]
        for i, inv in enumerate(_split_codes(_text(feat, MBI_INVOLVED_AREAS))):
            cand = self.candidates.get(inv)
            if cand is None:
                cand = self.candidates[inv] = BgyCandidate(inv)
            label = labels[i] if i < len(labels) else ""
            if label and label != "Unknown":
                name, city = label.rsplit(", ", 1) if ", " in label else (label, "")
                cand.name = cand.name or name.strip()
                cand.city_mun = cand.city_mun or city.strip()

        if coverage_field and not self._saved_raw:
            self._saved_raw = _text(feat, coverage_field)

    @property
    def status(self):
        """'pending' (no covering barangay), 'partial' (split overlap with an uncovered part),
        'covered', or 'gone' (remaining MBI no longer detected: nothing to cover)."""
        if self.gone:
            return "gone"
        if not self.assigned:
            return "pending"
        return "covered" if self.covered >= 1.0 else "partial"

    def resolve(self, da=None):
        """Work out what each barangay keeps / releases with the current choices.

        Split overlap: each covering barangay keeps its own part of the MBI (MBI ∩ barangay),
        minus what a barangay chosen before it already keeps; the case is covered once those
        parts fill the whole MBI. Otherwise (gaps, single-barangay cases, no barangay
        geometry) the covering barangay takes the whole MBI."""
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
            # Each building goes to the first covering barangay whose kept part holds it.
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

    def _join(self, attr):
        seen = []
        for c in self.candidates.values():
            v = getattr(c, attr)
            if v and v not in seen:
                seen.append(v)
        return ", ".join(seen)

    @property
    def barangays(self):
        return self._join("name")

    @property
    def places(self):
        return self._join("city_mun")

    def finish(self):
        parts = [self.case_uuid, self.mbi_type, self.level, self.detection, self.ref_uuids]
        for c in self.candidates.values():
            parts += [c.code, c.name, c.city_mun, c.province] + c.eas
        self.search_text = " ".join(parts).lower()


def load_mbi_cases(mbi_layer, default_type="", only_fids=None, coverage_field=None, tick=None,
                   skip_within=False):
    """Group the MBI layer rows by case_uuid -> ({uuid: MBICase}, skipped). skip_within
    leaves out the cases that involve a single barangay (gaps / overlaps between EAs of one
    barangay)."""
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
    skipped = 0
    for uuid in list(cases):
        case = cases[uuid]
        # No-longer-detected MBIs stay listed: the remaining layer may only name one barangay.
        if skip_within and len(case.candidates) <= 1 and not case.gone:
            del cases[uuid]
            skipped += 1
            continue
        if len(case.feature_ids) == 1 and len(case.candidates) > 1:
            # One row for the whole MBI (GEMMA format): no barangay owns the row, so the
            # row gets the case status and selecting a barangay selects the MBI row.
            for cand in case.candidates.values():
                cand.fids = []
        case.saved = _split_codes(case._saved_raw)
        case.assigned = [] if case.gone else [c for c in case.saved if c in case.candidates]
        case.finish()
    return cases, skipped


def read_bgy_lookup(bgy_layers):
    """barangay geocode -> {'name', 'city_mun', 'fids', 'layer_id'} across several barangay
    layers. A barangay stored as several features (e.g. EA polygons sharing the barangay
    geocode) keeps all of them. Returns (lookup, duplicate_count); the first layer that has a
    code wins, other layers' copies are counted as duplicates."""
    from qgis.core import QgsFeatureRequest

    lookup, duplicates = {}, 0
    for layer in bgy_layers:
        gc_field = _pick_field(layer, BGY_GEOCODE_FIELDS)
        name_field = _pick_field(layer, BGY_NAME_FIELDS)
        city_field = _pick_field(layer, BGY_CITY_FIELDS)
        if not gc_field:
            continue
        request = QgsFeatureRequest().setFlags(QgsFeatureRequest.NoGeometry)
        request.setSubsetOfAttributes([f for f in (gc_field, name_field, city_field) if f],
                                      layer.fields())
        for feat in layer.getFeatures(request):
            gc = _text(feat, gc_field)
            if not gc:
                continue
            info = lookup.get(gc)
            if info is None:
                lookup[gc] = {"name": _text(feat, name_field), "city_mun": _text(feat, city_field),
                              "fids": [feat.id()], "layer_id": layer.id()}
            elif info["layer_id"] == layer.id():
                info["fids"].append(feat.id())
            else:
                duplicates += 1
    return lookup, duplicates


BGY_REGION_FIELDS = ["region"]
BGY_PROVINCE_FIELDS = ["province"]


def read_bgy_info(bgy_layers):
    """geocode -> {'region', 'province', 'city_mun', 'barangay'} over the barangay layers (the
    first layer holding a code wins), plus the sorted province codes (first 3 digits of the
    geocodes) found in them."""
    from qgis.core import QgsFeatureRequest

    info, provinces = {}, set()
    for layer in bgy_layers:
        gc_field = _pick_field(layer, BGY_GEOCODE_FIELDS)
        if not gc_field:
            continue
        picks = {"region": _pick_field(layer, BGY_REGION_FIELDS),
                 "province": _pick_field(layer, BGY_PROVINCE_FIELDS),
                 "city_mun": _pick_field(layer, BGY_CITY_FIELDS),
                 "barangay": _pick_field(layer, BGY_NAME_FIELDS)}
        request = QgsFeatureRequest().setFlags(QgsFeatureRequest.NoGeometry)
        request.setSubsetOfAttributes([gc_field] + [f for f in picks.values() if f], layer.fields())
        for feat in layer.getFeatures(request):
            code = _text(feat, gc_field)
            if not code:
                continue
            if len(code) >= 3:
                provinces.add(code[:3])
            if code not in info:
                info[code] = {k: (_text(feat, f) or None) if f else None for k, f in picks.items()}
    return info, sorted(provinces)


def mbi_output_name(provinces):
    """'ppp_mbi' — ppp = province code (first 3 digits of the barangay geocodes); several
    provinces are joined with '_' ('012_015_mbi'); 'mbi' when none is known."""
    return "_".join(list(provinces) + ["mbi"])


def match_bldg_code(value, by_key):
    """Candidate for a building's geocode: an exact barangay geocode match, else the longest
    barangay geocode the value starts with (an EA geocode begins with its barangay's)."""
    key = _code_key(value)
    if not key:
        return None
    cand = by_key.get(key)
    if cand is not None:
        return cand
    best = None
    for k, c in by_key.items():
        if k and key.startswith(k) and (best is None or len(k) > len(best[0])):
            best = (k, c)
    return best[1] if best else None


# Fields the MBI Checker reads from each barangay layer ('geocode' is required: areas
# without it cannot be matched to a barangay; 'boundary' = Contested marks disputed areas).
CHECKER_REQUIRED_FIELDS = ["geocode"]
# Child-algorithm chatter the checker's feedback receives; kept out of the log.
_NOISY_LOG = ("Results:", "Packaging layer", "Using specified destination CRS",
              "Setting output type", "No spatial index", "Scratch layer queued",
              "Done setting layer style", "Exporting Overlap Layer")


def mode_kinds(mode):
    """MBI types a RUN_MODES index looks for."""
    return {0: {"Gap", "Overlap", "Disputed"}, 1: {"Overlap"}, 2: {"Gap"}, 3: {"Disputed"}}[mode]


# ─────────────── Built-in MBI Checker (copy of GEMMA's MBI Checker v13) ───────────────
# Copied from GEMMA plugin > gmd_scripts/gaps_overlaps_checker.py (GapsOverlaps, v13) so this
# tool works without GEMMA installed. Detection (merge > reproject to EPSG:3857 > fix >
# singleparts; overlap pairs > 0.10 m², one entry per polygon; gaps = holes of the dissolved
# barangays > 0.10 m², involved = polygons within 0.50 m; mbi_level from region / province /
# city / barangay; one row per finding, output in EPSG:4326) is unchanged. Adjusted for the
# counter-check:
#   • Disputed Areas are reported again (mbi_type 3_Disputed): polygons whose 'boundary' is
#     'Contested'. Their involved barangays are the contested polygon plus every polygon
#     within 0.50 m, so a covering barangay can be chosen. As in GEMMA, their footprint is
#     subtracted from the gaps.
#   • Reference MBI cases are NOT subtracted from the gaps (GEMMA auto-detects any loaded
#     layer with 'mbi_type' and drops its Disputed footprints) — that would hide the very
#     remaining Disputed MBIs being counter-checked.
#   • Building points are optional (counts are 0 without them).
#   • The Gaps / Overlaps / Disputed layers are returned instead of loaded into the project.
CHECKER_VERSION = "v13 (GEMMA) + counter-check adjustments"
CHECKER_MIN_M2 = 0.10        # findings this small (m², EPSG:3857) are slivers — not reported
CHECKER_GAP_BUFFER = 0.50    # m: polygons this close to a gap / disputed area are involved


def run_mbi_checker(polygon_layers, building_layers, mode, feedback=None):
    """Detect gaps, overlaps and disputed areas of the polygon layers.

    Returns {'Gap': layer, 'Overlap': layer, 'Disputed': layer} for the types the mode
    (RUN_MODES index) looks for — always emitted, empty when nothing is found."""
    import uuid as _uuid
    import processing
    from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsFeature,
                           QgsFeatureRequest, QgsField, QgsFields, QgsGeometry,
                           QgsProcessingContext, QgsProcessingFeedback, QgsProject,
                           QgsSpatialIndex, QgsVectorLayer, QgsWkbTypes)
    from qgis.PyQt.QtCore import QVariant

    if not polygon_layers:
        raise ValueError("Select at least one barangay layer.")
    feedback = feedback or QgsProcessingFeedback()
    context = QgsProcessingContext()
    context.setProject(QgsProject.instance())
    context.setFeedback(feedback)
    context.setInvalidGeometryCheck(QgsFeatureRequest.GeometryNoCheck)   # invalid input expected
    kinds = mode_kinds(mode)
    keep = []   # memory layers must outlive processing.run()

    target_crs = QgsCoordinateReferenceSystem("EPSG:3857")
    output_crs = QgsCoordinateReferenceSystem("EPSG:4326")
    to_out = QgsCoordinateTransform(target_crs, output_crs, QgsProject.instance())

    def run(alg, params):
        if feedback.isCanceled():
            raise _Cancelled()
        params["OUTPUT"] = "memory:"
        out = processing.run(alg, params, context=context, feedback=feedback)["OUTPUT"]
        if isinstance(out, str):
            out = context.getMapLayer(out)
        keep.append(out)
        return out

    def get_attr(feat, names):
        lower = {f.name().lower(): f.name() for f in feat.fields()}
        for n in names:
            real = lower.get(n.lower())
            if real:
                v = feat[real]
                if v is not None:
                    return v
        return None

    def txt(v):
        s = "" if v is None else str(v).strip()
        return "" if s.upper() == "NULL" else s

    def safe_area(geom):
        if geom is None or geom.isEmpty():
            return 0.0
        try:
            return float(geom.area())
        except Exception:
            return 0.0

    def label_of(info):
        brgy, city = txt(info.get("barangay")), txt(info.get("city_mun"))
        return f"{brgy}, {city}" if brgy and city else (brgy or city or "Unknown")

    def merge(layers):
        refs = []
        for layer in layers:
            mapping = [{"alias": "", "comment": "", "expression": f'"{f.name()}"',
                        "length": f.length(), "name": f.name(), "precision": f.precision(),
                        "sub_type": 0, "type": f.type(), "type_name": f.typeName()}
                       for f in layer.fields()]
            refs.append(run("native:refactorfields", {"INPUT": layer, "FIELDS_MAPPING": mapping}))
        return run("native:mergevectorlayers", {"LAYERS": refs, "CRS": layers[0].crs()})

    def reproject_fix(layer):
        reproj = run("native:reprojectlayer", {"INPUT": layer, "TARGET_CRS": target_crs,
                                               "CONVERT_CURVED_GEOMETRIES": False})
        return run("native:fixgeometries", {"INPUT": reproj, "METHOD": 1})

    def singleparts(layer):
        single = run("native:multiparttosingleparts", {"INPUT": layer})
        return run("native:fixgeometries", {"INPUT": single, "METHOD": 1})

    def output_layer(name):
        fields = QgsFields()
        for n, t in CHECKER_SCHEMA:
            fields.append(QgsField(n, QVariant.Int if t == "int" else QVariant.String))
        layer = QgsVectorLayer("Polygon?crs=EPSG:4326", name, "memory")
        layer.dataProvider().addAttributes(fields)
        layer.updateFields()
        return layer

    # ---- Prepare inputs ----
    feedback.pushInfo("Merging and fixing barangay layers…")
    feedback.setProgress(2)
    poly_layer = singleparts(reproject_fix(merge(polygon_layers)))
    infos, poly_index = {}, QgsSpatialIndex()
    for feat in poly_layer.getFeatures():
        geom = feat.geometry()
        if geom is None or geom.isEmpty():
            continue
        infos[feat.id()] = {
            "geometry": QgsGeometry(geom),
            "geocode": get_attr(feat, ["geocode"]),
            "region": get_attr(feat, ["region"]),
            "province": get_attr(feat, ["province"]),
            "city_mun": get_attr(feat, ["city_mun", "city/mun"]),
            "barangay": get_attr(feat, ["barangay", "brgy"]),
            "source": get_attr(feat, ["source"]),
            "boundary": get_attr(feat, ["boundary"]),
        }
        poly_index.addFeature(feat)
    if not infos:
        raise ValueError("No valid polygon features found in the barangay layers.")

    bldg_geoms, bldg_index = {}, QgsSpatialIndex()
    building_layers = [l for l in (building_layers or []) if l is not None]
    if building_layers:
        feedback.pushInfo("Merging and fixing building point layers…")
        feedback.setProgress(8)
        for feat in reproject_fix(merge(building_layers)).getFeatures():
            if feat.hasGeometry() and not feat.geometry().isEmpty():
                bldg_geoms[feat.id()] = QgsGeometry(feat.geometry())
                bldg_index.addFeature(feat)

    disputed_fids = {fid for fid, i in infos.items() if txt(i["boundary"]).lower() == "contested"}
    feedback.pushInfo(f"  {len(disputed_fids):,} Disputed (boundary = Contested) polygon(s); "
                      f"{len(infos) - len(disputed_fids):,} other polygon(s).")

    def count_points(geom):
        n = 0
        for fid in bldg_index.intersects(geom.boundingBox()):
            pt = bldg_geoms.get(fid)
            try:
                if pt is not None and geom.intersects(pt):
                    n += 1
            except Exception:
                pass
        return n

    def get_involved(finding, near=False, by_feature=False):
        """Polygons taking part in a finding. near=True (gaps, disputed): every polygon within
        CHECKER_GAP_BUFFER, grouped by barangay label; otherwise polygons sharing > 0.01 m²
        (by_feature keeps each polygon separate — overlaps inside one barangay)."""
        search = finding.buffer(CHECKER_GAP_BUFFER, 5) if near else finding
        grouped = {}
        for fid in poly_index.intersects(search.boundingBox()):
            info = infos.get(fid)
            if not info:
                continue
            try:
                if not search.intersects(info["geometry"]):
                    continue
                covered = 0.0 if near else safe_area(finding.intersection(info["geometry"]))
                if not near and covered <= 0.01:
                    continue
            except Exception:
                continue
            label = label_of(info)
            key = fid if by_feature else label
            entry = grouped.setdefault(key, {"info": info, "label": label, "covered": 0.0})
            entry["covered"] += covered
        if near and grouped:
            share = safe_area(finding) / len(grouped)
            for entry in grouped.values():
                entry["covered"] = share
        return sorted(grouped.values(), key=lambda x: x["label"])

    def mbi_level(involved):
        for key, level in (("region", "1_Inter-Region"), ("province", "2_Inter-Province"),
                           ("city_mun", "3_Inter-City/Municipality"), ("barangay", "4_Inter-Barangay")):
            if len({txt(x["info"].get(key)) for x in involved}) > 1:
                return level
        return "5_Within-Barangay"

    def overlap_reference(info1, info2):
        s1, s2 = txt(info1.get("source")).upper(), txt(info2.get("source")).upper()
        if "LGU" in s1 and "PSA" in s2:
            return info1
        if "LGU" in s2 and "PSA" in s1:
            return info2
        if s1 and not s2:
            return info1
        return info2

    def build_feature(geom_3857, involved, mbi_type, fields, ref=None):
        if not involved:
            return None
        g = QgsGeometry(geom_3857)
        try:
            g.transform(to_out)
        except Exception:
            pass
        ref = ref if ref is not None else max(involved, key=lambda x: x["covered"])["info"]
        f = QgsFeature(fields)
        f.setGeometry(g)
        f.setAttributes([str(_uuid.uuid4()), txt(ref.get("geocode")), txt(ref.get("region")),
                         txt(ref.get("province")), txt(ref.get("city_mun")), txt(ref.get("barangay")),
                         txt(ref.get("source")) or None, mbi_level(involved),
                         ",".join(txt(x["info"].get("geocode")) for x in involved),
                         ";".join(x["label"] for x in involved), len(involved), mbi_type,
                         count_points(geom_3857)])
        return f

    def polygon_parts(geom):
        if geom is None or geom.isEmpty():
            return []
        if QgsWkbTypes.geometryType(geom.wkbType()) == QgsWkbTypes.PolygonGeometry:
            return geom.asGeometryCollection() if geom.isMultipart() else [geom]
        if geom.isMultipart():
            return [p for p in geom.asGeometryCollection()
                    if QgsWkbTypes.geometryType(p.wkbType()) == QgsWkbTypes.PolygonGeometry]
        return []

    def finish(layer, feats, kind):
        if feats:
            layer.dataProvider().addFeatures(feats)
            layer.updateExtents()
        feedback.pushInfo(f"  {len(feats):,} {kind.lower()} finding(s).")
        results[kind] = layer

    results = {}

    # ---- Disputed areas (boundary = Contested) ----
    if "Disputed" in kinds:
        feedback.pushInfo("Extracting disputed areas…")
        feedback.setProgress(15)
        out = output_layer("Disputed Areas")
        feats = []
        for fid in disputed_fids:
            info = infos[fid]
            involved = get_involved(info["geometry"], near=True)
            if not any(x["info"] is info for x in involved):
                involved.append({"info": info, "label": label_of(info), "covered": 0.0})
            f = build_feature(info["geometry"], involved, "3_Disputed", out.fields(), ref=info)
            if f:
                feats.append(f)
        finish(out, feats, "Disputed")

    # ---- Overlaps (all polygons, Disputed included) ----
    if "Overlap" in kinds:
        feedback.pushInfo("Detecting overlaps…")
        feedback.setProgress(20)
        out = output_layer("Overlaps")
        feats, checked = [], set()
        ids = list(infos)
        for i, fid1 in enumerate(ids):
            if feedback.isCanceled():
                raise _Cancelled()
            info1 = infos[fid1]
            geom1 = info1["geometry"]
            for fid2 in poly_index.intersects(geom1.boundingBox()):
                pair = (min(fid1, fid2), max(fid1, fid2))
                if fid1 == fid2 or pair in checked:
                    continue
                checked.add(pair)
                info2 = infos.get(fid2)
                if not info2:
                    continue
                try:
                    if not geom1.intersects(info2["geometry"]):
                        continue
                    for part in polygon_parts(geom1.intersection(info2["geometry"])):
                        if safe_area(part) <= CHECKER_MIN_M2:
                            continue
                        involved = get_involved(part, by_feature=True)
                        if len(involved) < 2:
                            continue
                        f = build_feature(part, involved, "2_Overlap", out.fields(),
                                          ref=overlap_reference(info1, info2))
                        if f:
                            feats.append(f)
                except Exception:
                    continue
            if ids:
                feedback.setProgress(20 + int(i / len(ids) * 35))
        finish(out, feats, "Overlap")

    # ---- Gaps (Disputed polygons left out of the dissolve, then subtracted) ----
    if "Gap" in kinds:
        feedback.pushInfo("Detecting gaps…")
        feedback.setProgress(60)
        others = QgsVectorLayer(f"Polygon?crs={target_crs.authid()}", "non_disputed", "memory")
        others.dataProvider().addAttributes(poly_layer.fields().toList())
        others.updateFields()
        others.dataProvider().addFeatures([f for f in poly_layer.getFeatures()
                                           if f.id() in infos and f.id() not in disputed_fids])
        keep.append(others)
        dissolved = run("native:dissolve", {"INPUT": others, "FIELD": [], "SEPARATE_DISJOINT": False})
        no_holes = run("native:deleteholes", {"INPUT": dissolved, "MIN_AREA": 0})
        cleaned = run("native:dissolve", {"INPUT": no_holes, "FIELD": [], "SEPARATE_DISJOINT": False})
        diff = run("native:difference", {"INPUT": cleaned, "OVERLAY": dissolved, "GRID_SIZE": None})
        gaps = singleparts(diff)
        disputed_union = None
        if disputed_fids:
            disputed_union = QgsGeometry.unaryUnion([infos[fid]["geometry"] for fid in disputed_fids])
        out = output_layer("Gaps")
        feats = []
        for feat in gaps.getFeatures():
            geom = feat.geometry()
            if geom is None or geom.isEmpty() or safe_area(geom) <= CHECKER_MIN_M2:
                continue
            if disputed_union is not None and not disputed_union.isEmpty():
                try:
                    geom = geom.difference(disputed_union)
                except Exception:
                    pass
            for part in polygon_parts(geom):
                if safe_area(part) <= CHECKER_MIN_M2:
                    continue
                involved = get_involved(part, near=True)
                if involved:
                    f = build_feature(part, involved, "1_Gap", out.fields())
                    if f:
                        feats.append(f)
        finish(out, feats, "Gap")

    feedback.setProgress(100)
    return results


def make_distance_area(crs):
    """Ellipsoidal areas in m², whatever the CRS (degrees for EPSG:4326)."""
    from qgis.core import QgsDistanceArea, QgsProject
    prj = QgsProject.instance()
    da = QgsDistanceArea()
    da.setSourceCrs(crs, prj.transformContext())
    ellipsoid = prj.ellipsoid()
    da.setEllipsoid(ellipsoid if ellipsoid and ellipsoid.upper() != "NONE" else "EPSG:7030")
    return da


def build_counter_check(detected, remaining, checked_kinds, name, bgy_info=None):
    """Compare MBI Checker detections with the remaining MBI layer.

    detected: [(layer, 'Gap' | 'Overlap' | 'Disputed')] (the checker's output layers);
    remaining: the remaining MBI layer, or None (plain check, no 'detection' fields);
    checked_kinds: MBI types the run looked for — a remaining MBI of another type is not
    reported as no longer detected.

    A detection matches a remaining MBI when they share area (not just an edge) and:
      • the remaining MBI is a Gap / Overlap of the same type, or
      • the remaining MBI is Disputed (or untyped): any detection matches — an open dispute
        shows up as a contested polygon, a gap or an overlap. Such cases are only checked
        when the run looks for all three types.
    Each detected MBI is written as ONE ROW PER INVOLVED BARANGAY (same case_uuid, the MBI
    geometry on every row; 'geocode' / region / province / city_mun / barangay are that row's
    barangay, from bgy_info {geocode: {...}} or the 'involved_bgys' labels), so each row gets
    its own coverage status. No-longer-detected MBIs keep the remaining layer's row.
    Returns (MultiPolygon memory layer or None when empty, stats dict)."""
    from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsFeature,
                           QgsField, QgsGeometry, QgsProject, QgsSpatialIndex, QgsVectorLayer)
    from qgis.PyQt.QtCore import QVariant

    prj = QgsProject.instance()
    if detected:
        crs = detected[0][0].crs()
    elif remaining is not None:
        crs = remaining.crs()
    else:
        crs = QgsCoordinateReferenceSystem("EPSG:4326")
    da = make_distance_area(crs)
    out = QgsVectorLayer("MultiPolygon" + (f"?crs={crs.authid()}" if crs.authid() else ""),
                         name, "memory")
    out.setCrs(crs)
    qtype = {"str": QVariant.String, "int": QVariant.Int}
    fields = [QgsField(n, qtype[t]) for n, t in CHECKER_SCHEMA]
    if remaining is not None:
        fields += [QgsField(n, QVariant.String)
                   for n in (DETECT_FIELD, REF_UUID_FIELD, REF_KIND_FIELD, REF_STATUS_FIELD,
                             REF_REMARKS_FIELD)]
    out.dataProvider().addAttributes(fields)
    out.updateFields()
    names = out.fields().names()
    int_fields = {n for n, t in CHECKER_SCHEMA if t == "int"}
    stats = {"still": 0, "new": 0, "gone": 0, "unchecked": 0, "disputed": 0, "remaining": 0,
             "disputed_still": 0, "rows": 0,
             "matched": 0, "multi": 0, "split": 0, "detected": 0}
    feats = []

    def geom_of(feat, xform):
        if not feat.hasGeometry():
            return None
        g = QgsGeometry(feat.geometry())
        if xform is not None:
            g.transform(xform)
        if not g.isGeosValid():
            g = g.makeValid()
        return _polygonal(g)

    def add(geom, attrs):
        g = QgsGeometry(geom)
        g.convertToMultiType()
        nf = QgsFeature(out.fields())
        nf.setGeometry(g)
        nf.setAttributes([attrs.get(n) for n in names])
        feats.append(nf)

    def add_per_barangay(geom, attrs):
        """One row per involved barangay (several polygons of one barangay = one row)."""
        labels = [s.strip() for s in str(attrs.get("involved_bgys") or "").split(";")]
        seen = set()
        for i, code in enumerate(_split_codes(attrs.get("involved_areas"))):
            if code in seen:
                continue
            seen.add(code)
            row = dict(attrs)
            row["geocode"] = code
            info = (bgy_info or {}).get(code)
            if info:
                row.update({k: v for k, v in info.items() if v})
            else:
                label = labels[i] if i < len(labels) else ""
                if label and label != "Unknown":
                    bname, city = label.rsplit(", ", 1) if ", " in label else (label, "")
                    row.update({"barangay": bname.strip(), "city_mun": city.strip() or None})
            add(geom, row)
        if not seen:
            add(geom, attrs)
        stats["rows"] += max(len(seen), 1)

    def join(values, sep):
        seen = []
        for v in values:
            if v and v not in seen:
                seen.append(v)
        return sep.join(seen) or None

    # Remaining MBIs (Disputed ones included)
    refs, index = {}, QgsSpatialIndex()
    if remaining is not None:
        xform = None if remaining.crs() == crs else QgsCoordinateTransform(remaining.crs(), crs, prj)
        for f in remaining.getFeatures():
            kind = _norm_type(_feat_text(f, REF_TYPE_FIELDS))
            stats["disputed"] += kind == "Disputed"
            g = geom_of(f, xform)
            if g is None:
                continue
            refs[f.id()] = {"feat": f, "geom": g, "kind": kind,
                            "uuid": _feat_text(f, REF_SOURCES["case_uuid"]) or f"ref_{f.id()}",
                            "status": _feat_text(f, REF_STATUS_FIELDS),
                            "remarks": _feat_text(f, REF_REMARKS_FIELDS)}
            idx_feat = QgsFeature(f.id())
            idx_feat.setGeometry(g)
            index.addFeature(idx_feat)
        stats["remaining"] = len(refs)

    # Fresh detections
    hit_count = {}   # remaining fid -> detections matching it
    for layer, kind in detected:
        xform = None if layer.crs() == crs else QgsCoordinateTransform(layer.crs(), crs, prj)
        lnames = set(layer.fields().names())
        for f in layer.getFeatures():
            g = geom_of(f, xform)
            if g is None:
                continue
            stats["detected"] += 1
            attrs = {n: f[n] for n, _ in CHECKER_SCHEMA if n in lnames}
            if not _text(f, MBI_TYPE):
                attrs[MBI_TYPE] = kind
            if remaining is not None:
                engine = QgsGeometry.createGeometryEngine(g.constGet())
                engine.prepareGeometry()
                hits = []
                for rid in index.intersects(g.boundingBox()):
                    r = refs.get(rid)
                    if r is None or r["kind"] not in ("", "Disputed", kind) \
                            or not engine.intersects(r["geom"].constGet()):
                        continue
                    if _area(_polygonal(g.intersection(r["geom"])), da) > MATCH_MIN_M2:
                        hits.append((rid, r))
                if hits:
                    stats["still"] += 1
                    stats["multi"] += len(hits) > 1
                    for rid, r in hits:
                        stats["disputed_still"] += rid not in hit_count and r["kind"] == "Disputed"
                        hit_count[rid] = hit_count.get(rid, 0) + 1
                    attrs.update({DETECT_FIELD: DETECT_STILL,
                                  REF_UUID_FIELD: join((r["uuid"] for _i, r in hits), ","),
                                  REF_KIND_FIELD: join((r["kind"] for _i, r in hits), ", "),
                                  REF_STATUS_FIELD: join((r["status"] for _i, r in hits), ", "),
                                  REF_REMARKS_FIELD: join((r["remarks"] for _i, r in hits), " | ")})
                else:
                    stats["new"] += 1
                    attrs[DETECT_FIELD] = DETECT_NEW
            add_per_barangay(g, attrs)
    stats["matched"] = len(hit_count)
    stats["split"] = sum(1 for n in hit_count.values() if n > 1)

    # Remaining MBIs the check no longer finds
    for rid, r in refs.items():
        if rid in hit_count:
            continue
        needs = {"Gap", "Overlap", "Disputed"} if r["kind"] in ("", "Disputed") else {r["kind"]}
        if not needs <= set(checked_kinds):
            stats["unchecked"] += 1   # this run did not look for its type
            continue
        attrs = {}
        for n, _t in CHECKER_SCHEMA:
            value = _feat_text(r["feat"], REF_SOURCES.get(n, [n])) or None
            if value is not None and n in int_fields:
                try:
                    value = int(float(value))
                except ValueError:
                    value = None
            attrs[n] = value
        attrs.update({"case_uuid": r["uuid"], MBI_TYPE: attrs.get(MBI_TYPE) or r["kind"] or None,
                      DETECT_FIELD: DETECT_GONE, REF_UUID_FIELD: r["uuid"],
                      REF_KIND_FIELD: r["kind"] or None,
                      REF_STATUS_FIELD: r["status"] or None, REF_REMARKS_FIELD: r["remarks"] or None})
        stats["gone"] += 1
        add(r["geom"], attrs)

    if not feats:
        return None, stats
    out.dataProvider().addFeatures(feats)
    out.updateExtents()
    return out, stats


def infer_mbi_type(layer_name):
    name = layer_name.lower()
    if "overlap" in name and "gap" not in name:
        return "Overlap"
    if "gap" in name and "overlap" not in name:
        return "Gap"
    return ""


# ─────────────────── UI (styled after GEMMA's Geometry Check & Repair) ───────────────────

DIALOG_NAME = "MBIBgyCoverageDialog"
DIALOG_TITLE = "MBI Barangay Coverage"
DIALOG_SUBTITLE = ("Counter-check the remaining MBIs with the built-in MBI Checker, review every "
                   "detected gap / overlap with its involved barangays, then choose the "
                   "barangay that covers it.")

BTN_RUN = ""
BTN_CANCEL = ""
BTN_EXPORT = ""
BTN_CLEAR = ""
GRP_STYLE = ("QGroupBox { font-weight:600; margin-top:6px; } "
             "QGroupBox::title { subcontrol-origin:margin; left:8px; }")
LEFT_WIDTH = 315

NO_CASES = ("No MBI cases loaded. Pick the Remaining MBI layer and tick the barangay layers on the "
            "left, then click Run Check & Counter-Check — or pick an existing MBI layer and click "
            "Load MBI Cases.")

TREE_COLS = ["MBI Case / Bgy Geocode", "Detection", "MBI Level", "Barangay", "City / Municipality",
             "Bldg Pts", "Role / Status"]
COL_ID, COL_DETECT, COL_LEVEL, COL_BGY, COL_CITY, COL_BLDG, COL_ROLE = range(7)

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
# Run Check & Counter-Check box (amber) and Load MBI Cases button (green): set apart so users who
# already have a counter-check result can reopen it without running the check again.
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
    <h2 style="margin:0 0 4px 0; color:#111827; font-size:17px;">MBI Barangay Coverage</h2>
    <div style="color:#4b5563;">Counter-check the remaining MBIs (the gaps, overlaps and disputed areas declared pending)
    with a fresh run of the <b>built-in MBI Checker</b> — a copy of GEMMA's MBI Checker, so GEMMA does not need to be
    installed — see which are still detected and which are new, then decide which involved barangay covers each one
    and export the result.</div>
  </div>

  <h3 style="color:#111827; font-size:14px; border-bottom:1px solid #e5e7eb; padding-bottom:4px;">Quick Start</h3>
  <ol style="margin-top:4px;">
    <li><b>Pick the remaining MBI layer</b> (left, <i>Remaining MBI Layer</i>). Not loaded yet? Click the folder
        button (<b>Browse GPKG…</b>) and open the MBI Validator's <i>ref_mbi_reviewed-….gpkg</i> — its
        <i>remaining_cases</i> layer is added and picked for you.</li>
    <li><b>Tick your barangay layers</b> (<i>Barangay Layers</i>) — every layer the involved barangays come from.</li>
    <li>Pick the <i>Building Points</i> layer so the check counts the buildings inside each MBI.</li>
    <li>Click <b>Run Check &amp; Counter-Check</b> (the <b style="color:#E65100;">orange</b> box). The MBI Checker
        runs on the barangay layers and every MBI it finds is compared with the remaining MBIs, Disputed ones
        included.</li>
    <li>Read the <b>Detection</b> column: <b>Still detected</b>, <b>Newly detected</b> (the whole row is
        <b style="color:#558b2f;">light green</b>) or <b>No longer detected</b> (grey).</li>
    <li>In the list, <b>tick the Role / Status box of the barangay that covers</b> each detected MBI — or click
        <b>Auto-Assign Pending</b> to let the tool decide the easy ones, then review. An overlap that
        runs across several barangays can be <b>shared out</b>: tick more than one barangay and each keeps only
        its own part (see <i>Shared Overlaps</i> below).</li>
    <li>Click <b>Apply Coverage</b> to write your choices into the <i>ppp_mbi</i> layer: the chosen barangay's row
        gets the covering barangay geocode in <i>assigned_bgy</i>. No coverage status is written here — the MBI EA
        Selector writes it.</li>
    <li>Click <b>Join MBI to Barangays</b> for a copy of the barangay layers with each MBI joined to the barangay
        it is assigned to — the joined MBI parts are drawn <b style="color:#c51162;">hatched magenta</b> and flagged
        <i>bgy_part</i> = <b>MBI</b> / <i>is_mbi</i> = <b>1</b>.</li>
    <li>Click <b>Export GPKG + CSV</b> to save the processed layer and a decision log.</li>
    <li><b>Next step — choose the EA:</b> click <b>Choose EAs in MBI EA Selector…</b>. It applies any pending
        choices, then opens the MBI EA Selector with this layer: every MBI is listed under the barangay you chose
        here and <b>only that barangay's EAs</b> are offered. Tick the EA layers there and click <b>Load MBI
        Cases</b>.</li>
  </ol>

  <div style="background:#f9fafb; border:1px solid #e5e7eb; border-left:4px solid #2e7d32;
              padding:10px 14px; margin:12px 0 16px 0;">
    <b style="color:#111827;">After the barangay, the EA:</b>
    The MBI EA Selector reads <i>assigned_bgy</i> from this layer (keep the Coverage field named
    <i>assigned_bgy</i>). A shared overlap is listed there once per covering barangay, each with only its part.
    MBIs still pending here are skipped there, so assign them first. It writes <i>assigned_ea</i> and
    <i>coverage_status</i> on the covering barangay's rows and never changes <i>assigned_bgy</i>.
  </div>

  <div style="background:#f9fafb; border:1px solid #e5e7eb; border-left:4px solid #4b5563;
              padding:10px 14px; margin:12px 0 16px 0;">
    <b style="color:#111827;">Nothing Reaches Disk Without Your Say-So:</b>
    Ticking barangays only changes the viewer. <i>Apply Coverage</i> writes into the MBI layer inside an
    ordinary QGIS edit session — keep it with Toggle Editing's <b>Save Edits</b>, or undo with
    <b>Edit &gt; Undo</b> / <b>Discard Edits</b>. The remaining MBI layer and the barangay layers are never
    edited. Files are only created by <i>Export GPKG + CSV</i>.
  </div>

  <h3 style="color:#111827; font-size:14px; border-bottom:1px solid #e5e7eb; padding-bottom:4px;">The Counter-Check</h3>
  <table width="100%" cellpadding="6" cellspacing="0" style="border:1px solid #e5e7eb; font-size:11px;">
    <tr><td width="110" style="font-weight:bold; vertical-align:top;">Still detected</td>
        <td>The check found an MBI that <b>shares area</b> with a remaining MBI of the same type (gap with gap, overlap
        with overlap, disputed with disputed) — the discrepancy the PSO declared pending is still there. A remaining
        <b>Disputed</b> MBI is still detected by <b>any</b> finding sharing its area: an open dispute shows up as a
        contested polygon, a gap or an overlap (the tooltip then names the remaining MBI's type). Hover the Detection cell for the
        remaining MBI's <i>case_uuid</i>, its declared status and remarks.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">Newly detected<br><span style="background:#dcedc8;">&nbsp;light green&nbsp;</span></td>
        <td>The check found an MBI that matches <b>no</b> remaining MBI — it is not in the remaining layer. The case row
        and its barangay rows are coloured light green (the Role / Status cell keeps its status colour). Newly detected
        MBIs are listed first.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">No longer detected<br><span style="background:#e0e0e0;">&nbsp;grey&nbsp;</span></td>
        <td>A remaining MBI the check did not find any more. It is listed for your information only — there is nothing
        to cover, so it cannot be ticked, Auto-Assign skips it and Apply Coverage leaves its fields untouched.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">Matching rule</td>
        <td>Same as GEMMA's MBI Validator: a detection and a remaining MBI only match when they overlap in <b>area</b>;
        merely touching along an edge does not count, so a genuinely new MBI next to an old one is reported as new.
        Remaining MBIs of a type the run did not look for are not called "no longer detected" (e.g. gaps with
        <i>Overlaps Only</i>); remaining <i>Disputed</i> MBIs are only checked by <i>Gaps, Overlaps and Disputed</i>,
        since a dispute can show up as any of the three.
        One detection overlapping several remaining MBIs lists them all in <i>ref_case_uuid</i>; a remaining MBI now
        found as several pieces gives one case per piece.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">Built-in MBI Checker</td>
        <td>A copy of GEMMA's MBI Checker (v13) inside this tool: barangay layers are merged, reprojected to
        EPSG:3857 and fixed; <b>overlaps</b> are intersecting polygon pairs over 0.10 m² (<i>2_Overlap</i>);
        <b>gaps</b> are holes between the dissolved barangays over 0.10 m², involving every barangay within 0.50 m
        (<i>1_Gap</i>); the MBI level comes from region / province / city / barangay. Adjusted for the counter-check:
        <b>disputed areas</b> are reported again (<i>3_Disputed</i>: polygons whose <i>boundary</i> is
        <i>Contested</i>, involving the contested polygon and every barangay within 0.50 m, so a covering barangay can
        be chosen; their footprint is left out of the gaps, as in GEMMA); the remaining MBIs are <b>not</b> subtracted
        from the gaps (GEMMA drops loaded Disputed cases from its gaps, which would hide the cases being checked); and
        building points are optional.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">ppp_mbi layer</td>
        <td>Run Check adds one temporary layer named <b>ppp_mbi</b>, where <i>ppp</i> is the province code — the first
        3 digits of the ticked barangay layers' geocodes (several provinces: <i>012_015_mbi</i>). Each detected MBI is
        written as <b>one row per involved barangay</b>, all sharing the MBI's <i>case_uuid</i> and geometry, as in the
        MBI EA Selector: <i>geocode, region, province, city_mun, barangay</i> are that row's barangay, so every row
        gets its own coverage status. Fields: the MBI Checker's (<i>case_uuid, geocode … involved_areas,
        involved_bgys, mbi_type, num_bldg_pts</i>) plus <b>detection</b>, <b>ref_case_uuid</b>, <b>ref_mbi_type</b>,
        <b>ref_status</b> and <b>ref_remarks</b>. No-longer-detected remaining MBIs keep the remaining layer's row.
        On the map it is coloured by detection (green = new, orange = still, grey dashed = no longer). Right-click
        &gt; <b>Make Permanent</b> to keep it. Without a remaining layer there are no detection fields.</td></tr>
  </table>

  <h3 style="color:#111827; font-size:14px; border-bottom:1px solid #e5e7eb; padding-bottom:4px;">How Barangays Are Matched</h3>
  <table width="100%" cellpadding="6" cellspacing="0" style="border:1px solid #e5e7eb; font-size:11px;">
    <tr><td width="110" style="font-weight:bold; vertical-align:top;">Barangay geocode</td>
        <td>The MBI Checker writes <b>one row per MBI</b>: the involved barangays are the geocodes in
        <i>involved_areas</i> (names from <i>involved_bgys</i>), plus the row's own <i>geocode</i>. In the barangay
        layers the tool looks for <i>geocode</i>, then <i>bgy_geocode</i>; the barangay name comes from
        <i>barangay</i> (or <i>bgy_name</i> / <i>name</i>).</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">EA MBI layers</td>
        <td>An older EA MBI layer (one row per involved EA) still loads with <i>Load MBI Cases</i>: all the EAs of one
        barangay in a case count as <b>one barangay</b> (hover the barangay name to see its EAs). Cases where every
        area belongs to the same barangay have nothing to decide — <i>Skip within-barangay cases</i> leaves them out.</td></tr>
  </table>

  <h3 style="color:#111827; font-size:14px; border-bottom:1px solid #e5e7eb; padding-bottom:4px;">Inputs (left panel)</h3>
  <table width="100%" cellpadding="6" cellspacing="0" style="border:1px solid #e5e7eb; font-size:11px;">
    <tr><td width="110" style="font-weight:bold; vertical-align:top;">Remaining MBI Layer</td>
        <td>The MBIs declared pending: the <i>remaining_cases</i> (or <i>pending_cases</i>) layer of the MBI
        Validator's GeoPackage, or a <i>ref_mbi_cases</i> layer. A loaded layer with one of those names is picked
        automatically. The folder button (<b>Browse GPKG…</b>) adds the layer from a GeoPackage — <i>remaining_cases</i>
        first, else <i>pending_cases</i> / <i>ref_mbi_cases</i>, else you choose. Its <i>case_uuid</i>, <i>mbi_type</i>,
        status (<i>mbi_status</i> / <i>ref_status</i>) and remarks (<i>pso_remarks</i> / <i>ref_remarks</i>) are read.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">Barangay Layers</td>
        <td>Click every barangay layer the involved barangays come from — an inter-municipal or inter-provincial
        MBI can involve barangays stored in different layers. These are the layers the MBI Checker checks. The log
        reports involved barangays that none of the selected layers contain (shown in red in the list). The barangay
        shapes are also what <b>shared overlaps</b> are clipped to. <i>Skip within-barangay cases</i> (on by default)
        leaves out cases that involve a single barangay.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">Building Points</td>
        <td>Passed to the MBI Checker (its <i>num_bldg_pts</i>). <b>Case row:</b> building points inside the MBI.
        <b>Barangay rows:</b> of those, the points whose <i>Geocode field</i> (auto: bgy_geocode, geocode, then
        ea_geocode) is the barangay's geocode <b>or starts with it</b>. Used by Auto-Assign. Without it the check
        still runs, with building counts of 0.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">Run Check &amp; Counter-Check<br>(orange box)</td>
        <td>Choose <i>Gaps, Overlaps and Disputed</i> (checks every remaining MBI), <i>Overlaps Only</i>,
        <i>Gaps Only</i> or <i>Disputed Only</i>, then click the button. It runs the <b>built-in MBI Checker</b> on the
        ticked barangay layers, compares every MBI it finds with the remaining MBI layer, adds the <i>ppp_mbi</i>
        layer and loads its cases. GEMMA is not needed. Barangay layers need a <i>geocode</i> field
        (plus <i>region</i>, <i>province</i>, <i>city_mun</i> and <i>barangay</i> for the MBI level, and
        <i>boundary</i> = <i>Contested</i> on disputed polygons).</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">Or Load an Existing MBI Layer<br>(green button)</td>
        <td>Reopens a result without running the check again: pick an earlier <i>ppp_mbi</i> layer (e.g. one
        you made permanent) and click <b>Load MBI Cases</b> — its Detection column and colours come back. Tick <i>Only
        selected MBI features</i> to load just part of it and <i>Resume saved coverage choices</i> to pre-tick
        barangays already applied to it.</td></tr>
  </table>

  <h3 style="color:#111827; font-size:14px; border-bottom:1px solid #e5e7eb; padding-bottom:4px;">Reading the Case List</h3>
  <table width="100%" cellpadding="6" cellspacing="0" style="border:1px solid #e5e7eb; font-size:11px;">
    <tr><td width="110" style="font-weight:bold; vertical-align:top;">Case row (bold)</td>
        <td>One gap / overlap. Shows its <b>Detection</b> (see <i>The Counter-Check</i>), MBI level, the barangays and
        cities / municipalities involved and the <b>Bldg Pts</b> inside it. Status is <b>Pending</b> (orange) until a
        barangay is chosen, then <b>Covered by &lt;barangay&gt;</b> (green) — or <b>Partly covered (x%)</b> (yellow)
        when the barangays ticked in a shared overlap do not fill the whole MBI yet. No-longer-detected MBIs show
        <b>No longer detected</b> (grey).</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">Barangay rows (below)</td>
        <td>Each involved barangay (geocode, name, city / municipality) with its own <b>Bldg Pts</b> (buildings
        inside this MBI tagged to that barangay). <b>Tick the Role / Status box of the covering barangay.</b> In a
        gap, an MBI between <b>two</b> barangays, or an overlap that cannot be shared out, only one barangay covers
        the case — ticking one unticks the other. Only an overlap across <b>three or more</b> barangays lets you
        tick several. Unticking every barangay makes the case Pending again.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">Moving around</td>
        <td>Click a row to select it on the map, double-click to zoom, right-click for more (set / add / remove
        a covering barangay, mark pending, copy case ID or the remaining MBI's case ID). Use the search box
        (barangay, city, geocode, EA name, case ID or detection) and the <i>Show</i> filter (<i>All / Pending /
        Partly covered / Covered / Newly detected / Still detected / No longer detected / Overlaps / Gaps / level</i>)
        to find cases. Click a column header to sort.</td></tr>
  </table>

  <h3 style="color:#111827; font-size:14px; border-bottom:1px solid #e5e7eb; padding-bottom:4px;">Shared Overlaps (one MBI across several barangays)</h3>
  <table width="100%" cellpadding="6" cellspacing="0" style="border:1px solid #e5e7eb; font-size:11px;">
    <tr><td width="110" style="font-weight:bold; vertical-align:top;">What it is</td>
        <td><b>Needs three or more candidate barangays</b> — an MBI between two barangays always goes to one of
        them. An overlap that runs across several barangays (e.g. one barangay overlapping a strip of three
        barangays of the next municipality) does not have to go to a single barangay. Tick several: <b>each
        ticked barangay keeps only its own part of the overlap</b> — the MBI clipped to that barangay. The other
        barangays release (clip) the parts the ticked ones keep.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">Order matters</td>
        <td>Where two ticked barangays both contain the same piece of the overlap, the barangay <b>ticked
        first</b> keeps it; the later one keeps the rest of its part. To change who keeps a shared piece, untick
        and re-tick in the order you want.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">Reading the rows</td>
        <td>Ticked barangay: <b>✔ Keeps its part (x%)</b> — its share of the MBI, plus the buildings in it.
        Unticked barangay: <b>Releases (clip)</b> when all of its part is kept by ticked barangays, <b>Releases x%
        · candidate</b> when only some is — tick it to keep the rest. Hover a row for the areas.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">On the map</td>
        <td>Select a barangay row to see its part: <b style="color:#2e7d32;">green</b> = what it keeps,
        <b style="color:#1565c0;">dashed blue</b> = what it would keep if ticked. Select the case row to see
        every kept part and, in <b style="color:#e65100;">orange</b>, what no ticked barangay keeps yet.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">When it applies</td>
        <td>Overlaps with two or more barangays whose shapes are all in the selected barangay layers. Gaps (no
        barangay contains a gap) and overlaps with a barangay missing from the selected layers keep the
        one-barangay-covers-all rule.</td></tr>
  </table>

  <h3 style="color:#111827; font-size:14px; border-bottom:1px solid #e5e7eb; padding-bottom:4px;">Choices in the Viewer</h3>
  <table width="100%" cellpadding="6" cellspacing="0" style="border:1px solid #e5e7eb; font-size:11px;">
    <tr><td width="110" style="font-weight:bold;">Overlap</td>
        <td>Ticked barangay: <b>Keeps overlap</b>. Others: <b>Releases (clip)</b>. With two barangays, ticking one
        unticks the other.</td></tr>
    <tr><td style="font-weight:bold;">Shared overlap</td>
        <td>Three or more barangays: each ticked barangay <b>Keeps its part</b>. Others: <b>Releases (clip)</b>.</td></tr>
    <tr><td style="font-weight:bold;">Gap</td>
        <td>Ticked barangay: <b>Absorbs gap</b>. Others: <b>Not extended</b>.</td></tr>
    <tr><td style="font-weight:bold;">Auto-Assign</td>
        <td>Single-barangay cases go to that barangay. Multi-barangay cases go to the barangay that owns most of the
        buildings inside the MBI (needs Building Points); shared overlaps go to <b>every</b> barangay with tagged
        buildings, most buildings first, each keeping its part. No tagged buildings or a tie stays Pending —
        decide those by hand. Auto-assigned shared overlaps can end up Partly covered: check them with
        <i>Show: Partly covered</i>.</td></tr>
  </table>

  <h3 style="color:#111827; font-size:14px; border-bottom:1px solid #e5e7eb; padding-bottom:4px;">Join MBI to Barangays</h3>
  <table width="100%" cellpadding="6" cellspacing="0" style="border:1px solid #e5e7eb; font-size:11px;">
    <tr><td width="110" style="font-weight:bold; vertical-align:top;">What it makes</td>
        <td>A new temporary layer <i>Barangays + MBI (&lt;date&gt;_&lt;time&gt;)</i>: every feature of the ticked
        barangay layers, plus <b>one feature per MBI part a barangay keeps</b> (your ticks in the viewer — Apply
        Coverage is not needed first). Each MBI part carries the <b>covering barangay's attributes</b> (geocode,
        name, city …), so it belongs to that barangay. The barangay layers themselves are never edited.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">How to tell it is an MBI</td>
        <td><b>bgy_part</b> = <i>MBI</i> (original areas: <i>Barangay</i>) and <b>is_mbi</b> = <i>1</i> (else 0) —
        filter with <i>"is_mbi" = 1</i>. On the map the MBI parts are <b style="color:#c51162;">hatched magenta with a
        dashed outline</b>. They also hold <i>mbi_case_uuid, mbi_type, mbi_level, mbi_detection, mbi_area_m2,
        mbi_bldg_pts</i> and <i>mbi_released_by</i> (the barangays that gave the overlap up).</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">Barangay features</td>
        <td>Kept MBI parts are <b>cut out</b> of every barangay feature they lie in, so nothing is counted twice: an
        overlap is removed from the barangays that released it (and stored once, as the MBI part, for the one that
        keeps it); a disputed polygon wholly absorbed by its covering barangay is left out. Gaps simply add a new
        part. To merge each MBI into its barangay, run <i>Vector &gt; Geoprocessing &gt; Dissolve</i> by
        <i>geocode</i>.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">Not joined</td>
        <td>Pending cases, the uncovered part of partly covered overlaps, and no-longer-detected MBIs. An MBI assigned
        to a barangay that is not in the ticked layers still gets its part, with only geocode / name / city filled
        (the log counts them).</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">Export</td>
        <td><i>Export GPKG + CSV</i> also writes this layer into the GeoPackage as <i>mbi_bgy_joined</i>.</td></tr>
  </table>

  <h3 style="color:#111827; font-size:14px; border-bottom:1px solid #e5e7eb; padding-bottom:4px;">Apply Coverage — Fields Written</h3>
  <table width="100%" cellpadding="6" cellspacing="0" style="border:1px solid #e5e7eb; font-size:11px;">
    <tr><td width="110" style="font-weight:bold; vertical-align:top;">Coverage field<br>(default <i>assigned_bgy</i>)</td>
        <td>Geocode of the covering barangay, on every row of the case — several geocodes, comma-separated, for a
        shared overlap (in the order ticked). Empty while the case is Pending.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">No coverage status</td>
        <td>This tool writes <b>no</b> <i>coverage_status</i>: the coverage status is decided per EA and written by
        the <b>MBI EA Selector</b>. Skipped within-barangay cases are not loaded and no-longer-detected MBIs have
        nothing to cover, so both are left untouched.</td></tr>
  </table>
  <div style="color:#4b5563; margin-top:4px;">The field name can be changed in the panel (letters, digits and
  underscores only), but the MBI EA Selector reads <i>assigned_bgy</i> — keep that name. A missing field is
  added to the MBI layer automatically.</div>

  <h3 style="color:#111827; font-size:14px; border-bottom:1px solid #e5e7eb; padding-bottom:4px;">Export GPKG + CSV</h3>
  <table width="100%" cellpadding="6" cellspacing="0" style="border:1px solid #e5e7eb; font-size:11px;">
    <tr><td width="110" style="font-weight:bold; vertical-align:top;">Export folder</td>
        <td>Where the files go. Leave it empty and you are asked for a folder when you click Export.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">GeoPackage</td>
        <td><i>ppp_mbi_&lt;date&gt;_&lt;time&gt;.gpkg</i> with: <i>ppp_mbi</i> — the whole MBI layer including the
        coverage fields, even if you have not saved the layer's edits yet — and <i>mbi_bgy_joined</i> — the barangays with their MBIs joined (as <i>Join MBI to Barangays</i>; only when
        barangay layers are ticked and some MBI is covered).</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">CSV</td>
        <td><i>ppp_mbi_&lt;date&gt;_&lt;time&gt;.csv</i> — one row per case / barangay: case ID, detection,
        remaining MBI case ID and declared status, type, level, MBI
        area, building counts, barangay geocode / name / city / province, its EAs, the barangay's part and kept
        area (m²) and kept buildings, <i>chosen</i> (Yes for the covering barangay) and the covering barangay(s). Opens in Excel.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">Not applied yet?</td>
        <td>If your choices are not in the MBI layer yet, Export offers to <b>Apply Coverage first</b>, so the
        files always match what you see. Pending cases are exported with an empty <i>assigned_bgy</i>.</td></tr>
  </table>

  <h3 style="color:#111827; font-size:14px; border-bottom:1px solid #e5e7eb; padding-bottom:4px;">If Something Looks Wrong</h3>
  <table width="100%" cellpadding="6" cellspacing="0" style="border:1px solid #e5e7eb; font-size:11px;">
    <tr><td width="110" style="font-weight:bold; vertical-align:top;">Run Check is greyed out</td>
        <td>Tick at least one barangay layer, and make sure each has a <i>geocode</i> field.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">No Detection column</td>
        <td>No remaining MBI layer was picked, so nothing was counter-checked. Pick it (or Browse GPKG…) and run the
        check again.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">Everything is Newly detected</td>
        <td>No detection shares area with a remaining MBI: check you picked the right remaining layer (the same
        province / municipality) and that its <i>mbi_type</i> values say Gap / Overlap / Disputed.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">A remaining MBI is missing from the list</td>
        <td>It is of a type this run did not look for (e.g. a gap with <i>Overlaps Only</i>, or a Disputed case with
        anything but <i>Gaps, Overlaps and Disputed</i>) — the log counts them. Run <i>Gaps, Overlaps and Disputed</i>
        to check everything.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">No disputed areas found</td>
        <td>The checker reports a disputed area only for a barangay polygon whose <i>boundary</i> field is
        <i>Contested</i>. Without that field, a remaining Disputed MBI can still be found as a gap or overlap
        sharing its area.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">Load MBI Cases is greyed out</td>
        <td>The picked MBI layer has no <i>case_uuid</i> field — use Run Check to create a proper one.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">No cases after loading</td>
        <td>Every case involves one barangay only (e.g. an EA MBI layer with only within-barangay
        discrepancies). Untick <i>Skip within-barangay cases</i> to list them anyway.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">Barangay in red / "not found"</td>
        <td>The barangay's layer is not ticked, or its geocode differs from the MBI layer's <i>geocode</i>. Tick
        the layer, then Load again.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">Can only tick one barangay in an overlap</td>
        <td>An MBI between two barangays (and every gap) takes one barangay. An overlap across three or more can
        be shared out only when the layers of <b>all</b> its barangays are ticked under <i>Barangay Layers</i> (none
        may be red) — tick them, then Load again.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">Stays Partly covered</td>
        <td>The ticked barangays' parts leave some of the MBI uncovered. Select the case row to see the orange
        leftover on the map, then tick the barangay that contains it.</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">Barangay rows show 0 buildings</td>
        <td>No building inside this MBI is tagged to that barangay. Check the <i>Geocode field</i> under Building
        Points holds the barangay geocode (or an EA geocode starting with it).</td></tr>
    <tr><td style="font-weight:bold; vertical-align:top;">Field name turns red</td>
        <td>Use letters, digits and underscores only, starting with a letter or underscore; the Coverage field,
        Apply and Export stay disabled until fixed.</td></tr>
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
                           QgsFeature, QgsFeatureRequest, QgsField, QgsFillSymbol,
                           QgsGeometry, QgsProcessingContext, QgsProcessingFeedback, QgsProject,
                           QgsRendererCategory, QgsSimpleFillSymbolLayer, QgsVectorFileWriter,
                           QgsVectorLayer, QgsWkbTypes)
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
    ROLE_BGY = Qt.UserRole + 1
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
        """Sorts cases by the clicked column, but keeps each case's barangays in a fixed order
        (otherwise ticking a barangay re-sorts the rows under the cursor)."""

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
            self.setWindowTitle(f"{DIALOG_TITLE} — MBI Barangay Selector")
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
            self.bgy_lookup = {}      # barangay geocode -> {name, city_mun, fids, layer_id}
            self.mbi_layer_id = None
            self.da = None               # QgsDistanceArea for the MBI layer (m²)
            self._bands = {}             # map highlights of the selected barangay's MBI part
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
            self.tabs.addTab(self._build_main_tab(), "MBI Barangay Coverage")
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
            prj.layersAdded.connect(self.refresh_bgy_list)
            prj.layersRemoved.connect(self.refresh_bgy_list)
            for combo in (self.mbi_combo, self.remaining_combo):
                combo.layerChanged.connect(self.refresh_bgy_list)
            for combo in (self.mbi_combo, self.bldg_combo):
                combo.layerChanged.connect(self.validate_inputs)
            self._pick_remaining_default()
            self.refresh_bgy_list()
            self.apply_theme()
            self.update_summary()

        # ──────────────── construction ────────────────
        def _build_main_tab(self):
            tab = QtWidgets.QWidget()
            root = QtWidgets.QHBoxLayout(tab)
            root.setContentsMargins(6, 6, 6, 6)
            root.setSpacing(8)

            # ── LEFT: inputs and load controls (scrolls when the window is short) ──
            left = QtWidgets.QWidget()
            ll = QtWidgets.QVBoxLayout(left)
            ll.setContentsMargins(0, 0, 4, 0)
            ll.setSpacing(6)

            box, lay = group("Remaining MBI Layer (declared pending)")
            rrow = QtWidgets.QHBoxLayout()
            self.remaining_combo = layer_combo(polygon_filter)
            self.remaining_combo.setToolTip(
                "The MBIs the PSOs declared pending — e.g. 'remaining_cases' of the MBI Validator's\n"
                "ref_mbi_reviewed GeoPackage, or a ref_mbi_cases layer. Run Check compares every\n"
                "MBI the check finds with these.")
            rrow.addWidget(self.remaining_combo, 1)
            self.browse_btn = QtWidgets.QToolButton()
            self.browse_btn.setIcon(QgsApplication.getThemeIcon("/mActionFileOpen.svg"))
            self.browse_btn.setToolTip("Browse GPKG… — add the remaining MBI layer of a GeoPackage\n"
                                       f"({', '.join(GPKG_REMAINING_LAYERS)}) to the project.")
            self.browse_btn.clicked.connect(self.browse_remaining_gpkg)
            rrow.addWidget(self.browse_btn)
            lay.addLayout(rrow)
            self.remaining_hint = self._hint()
            lay.addWidget(self.remaining_hint)
            ll.addWidget(box)

            box, lay = group("Barangay Layers")
            sel_row = QtWidgets.QHBoxLayout()
            self.btn_all = button("Select All", 24)
            self.btn_all.clicked.connect(lambda: self._check_all(True))
            self.btn_none = button("Clear", 24)
            self.btn_none.clicked.connect(lambda: self._check_all(False))
            sel_row.addWidget(self.btn_all)
            sel_row.addWidget(self.btn_none)
            lay.addLayout(sel_row)
            self.bgy_list = QtWidgets.QListWidget()
            self.bgy_list.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
            self.bgy_list.setAlternatingRowColors(True)
            self.bgy_list.setToolTip("Click a layer to check/uncheck it. Select every barangay layer\n"
                                     "the MBI's involved barangays come from.")
            self.bgy_list.setItemDelegate(LayerCheckDelegate(self.bgy_list))
            self.bgy_list.itemChanged.connect(self.validate_inputs)
            lay.addWidget(self.bgy_list, 1)
            self.layer_count_label = self._hint("Selected layers: 0 / 0")
            lay.addWidget(self.layer_count_label)
            self.bgy_hint = self._hint()
            lay.addWidget(self.bgy_hint)
            self.skip_within = QtWidgets.QCheckBox("Skip within-barangay cases")
            self.skip_within.setChecked(True)
            self.skip_within.setToolTip(
                "Leave out cases that involve a single barangay (e.g. gaps / overlaps between\n"
                "EAs of the same barangay in an EA MBI layer) — there is no barangay to choose.")
            lay.addWidget(self.skip_within)
            ll.addWidget(box, 1)

            box, lay = group("Building Points")
            self.bldg_combo = layer_combo(point_filter)
            lay.addWidget(self.bldg_combo)
            frow = QtWidgets.QHBoxLayout()
            frow.addWidget(QtWidgets.QLabel("Geocode field:"))
            self.bldg_field_combo = QgsFieldComboBox()
            self.bldg_field_combo.setAllowEmptyFieldName(True)
            self.bldg_field_combo.setToolTip(
                "Building point field holding the barangay geocode (or an EA geocode, which starts\n"
                "with it). Each building inside an MBI is credited to the involved barangay whose\n"
                "geocode equals — or begins — the value.")
            frow.addWidget(self.bldg_field_combo, 1)
            lay.addLayout(frow)
            self.bldg_combo.layerChanged.connect(self._on_bldg_layer)
            self.bldg_field_combo.fieldChanged.connect(self.validate_inputs)
            self.bldg_hint = self._hint()
            lay.addWidget(self.bldg_hint)
            ll.addWidget(box)

            box, lay = group("Run MBI Check && Counter-Check")
            self.run_box = box
            box.setObjectName("runBox")
            self.run_note = QtWidgets.QLabel(
                "Runs the built-in MBI Checker (a copy of GEMMA's — GEMMA is not needed) on the "
                "ticked barangay layers, then compares every MBI it finds with the Remaining MBI "
                "layer, Disputed cases included.")
            self.run_note.setWordWrap(True)
            lay.addWidget(self.run_note)
            self.mode_combo = QtWidgets.QComboBox()
            self.mode_combo.addItems(RUN_MODES)
            self.mode_combo.setToolTip("Which discrepancies the MBI Checker looks for. Disputed areas are\n"
                                       "polygons whose 'boundary' is Contested. Remaining MBIs of a type that\n"
                                       "is not checked are not reported as no longer detected; remaining\n"
                                       "Disputed MBIs are only checked with all three types.")
            lay.addWidget(self.mode_combo)
            self.run_btn = button("Run Check && Counter-Check")
            self.run_btn.setToolTip(
                "Run the built-in MBI Checker on the ticked barangay layers (and Building Points),\n"
                "compare each MBI found with the Remaining MBI layer (Still / Newly / No longer\n"
                "detected), add the 'ppp_mbi' layer (one row per involved barangay) and list its\n"
                "cases in the viewer (ppp = province code, first 3 digits of the barangay geocodes).")
            self.run_btn.setObjectName("runBtn")
            self.run_btn.clicked.connect(self.run_check)
            lay.addWidget(self.run_btn)
            self.run_hint = self._hint()
            lay.addWidget(self.run_hint)
            ll.addWidget(box)

            box, lay = group("Or Load an Existing MBI Layer")
            self.mbi_combo = layer_combo(polygon_filter)
            self.mbi_combo.setToolTip("A ppp_mbi layer from an earlier run (e.g. made permanent),\n"
                                      "or any Gaps / Overlaps layer with 'case_uuid'. Run Check fills this in.")
            lay.addWidget(self.mbi_combo)
            self.mbi_hint = self._hint()
            lay.addWidget(self.mbi_hint)
            self.selected_only = QtWidgets.QCheckBox("Only selected MBI features")
            self.selected_only.setToolTip("Load only the cases of the features selected on the map.")
            lay.addWidget(self.selected_only)
            self.preload_check = QtWidgets.QCheckBox("Resume saved coverage choices")
            self.preload_check.setChecked(True)
            self.preload_check.setToolTip("Pre-tick barangays from the coverage field already in the MBI layer.")
            lay.addWidget(self.preload_check)
            self.load_btn = button("Load MBI Cases")
            self.load_btn.setToolTip("Read the MBI layer picked above (no new check) and list each\n"
                                     "case with all of its involved barangays.")
            self.load_btn.setObjectName("loadBtn")
            self.load_btn.clicked.connect(lambda: self.load_cases())
            lay.addWidget(self.load_btn)
            ll.addWidget(box)

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
            left_scroll = QtWidgets.QScrollArea()
            left_scroll.setWidget(left)
            left_scroll.setWidgetResizable(True)
            left_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
            left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            left_scroll.setFixedWidth(LEFT_WIDTH + 14)
            left.setMinimumHeight(640)

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
                "Filter by Barangay, City / Municipality, Geocode, EA name, Case ID or Detection…")
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
            self.zoom_btn.setToolTip("Zoom to the selected cases / barangays")
            self.zoom_btn.clicked.connect(self.zoom_to_selection)
            filters.addWidget(self.zoom_btn)
            rl.addLayout(filters)
            self._fill_show_combo()

            self.tree = QtWidgets.QTreeWidget()
            self.tree.setColumnCount(len(TREE_COLS))
            self.tree.setHeaderLabels(TREE_COLS)
            head = self.tree.headerItem()
            head.setToolTip(COL_DETECT,
                            "Counter-check against the Remaining MBI layer:\n"
                            f"{DETECT_STILL} — the check still finds this remaining MBI.\n"
                            f"{DETECT_NEW} — found by the check, not in the remaining layer (light green).\n"
                            f"{DETECT_GONE} — a remaining MBI the check no longer finds (nothing to cover).")
            head.setToolTip(COL_BLDG,
                            "Case row: buildings inside the gap / overlap (num_bldg_pts, or a live\n"
                            "count when Building Points is set).\n"
                            "Barangay row: of those, the buildings whose geocode belongs to that barangay.")
            head.setToolTip(COL_ROLE,
                            "Tick the barangay that covers the MBI.\n"
                            "Overlap across several barangays: tick more than one — each ticked barangay keeps\n"
                            "only its own part of the overlap (the MBI clipped to that barangay).")
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
                swatch.format(COLOUR_NEW, "Newly detected (row)"),
                swatch.format(COLOUR_GONE, "No longer detected"),
                swatch.format(COLOUR_PENDING, "Pending"),
                swatch.format(COLOUR_PARTIAL, "Partly covered"),
                swatch.format(COLOUR_COVERED, "Covers MBI / its part"),
                swatch.format(COLOUR_RELEASED, "Releases / not extended"),
            ]) + " &nbsp;&nbsp;·&nbsp; Double-click: zoom · Right-click: more")
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
            self.field_edit.setToolTip("Field that receives the geocode(s) of the covering barangay(s),\n"
                                       "comma-separated when an overlap is shared out.")
            orow.addWidget(self.field_edit)
            # No coverage status here: the status is written by the MBI EA Selector only.
            # (The widget is kept hidden so field validation stays unchanged.)
            self.status_edit = QtWidgets.QLineEdit(STATUS_FIELD)
            self.status_edit.setVisible(False)
            self.status_edit.setMaximumWidth(140)
            self.status_edit.setToolTip(f"Field that receives the case status: '{STATUS_COVERED}' "
                                        f"(fully covered), '{STATUS_PARTIAL}' (part of a shared\n"
                                        f"overlap has no covering barangay yet) or '{STATUS_NOT_COVERED}' "
                                        "(still pending).")
            self.status_edit.textChanged.connect(self.validate_inputs)
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
                "Single-barangay cases go to that barangay. Multi-barangay cases go to the\n"
                "barangay that owns most of the buildings inside the MBI (needs Building Points +\n"
                "geocode field); shared overlaps go to every barangay with tagged buildings, most\n"
                "first, each keeping its own part. No tagged buildings or a tie stays pending.")
            self.auto_btn.clicked.connect(self.auto_assign)
            self.reset_btn = button("Reset Choices", 32, BTN_CANCEL)
            self.reset_btn.setToolTip("Clear every barangay tick in the viewer (nothing is written).")
            self.reset_btn.clicked.connect(self.reset_choices)
            self.apply_btn = button("Apply Coverage", 32, BTN_EXPORT)
            self.apply_btn.setToolTip(
                f"Writes the covering barangay geocode(s) ('{COVERAGE_FIELD}') into the MBI layer.\n"
                "No coverage status is written here — the MBI EA Selector writes it.\n"
                "Changes are left UNSAVED (the layer enters edit mode) — use QGIS's\n"
                "Save Edits to keep them, or Cancel Edits to discard and revert.")
            self.apply_btn.clicked.connect(self.apply_coverage)
            self.join_btn = button("Join MBI to Barangays", 32)
            self.join_btn.setToolTip(
                f"Make a new layer '{JOIN_LAYER_NAME}': a copy of the ticked barangay layers with every\n"
                "MBI joined to the barangay it is assigned to. Each joined MBI part is its own feature\n"
                f"with the barangay's attributes, '{JOIN_KIND_FIELD}' = '{JOIN_KIND_MBI}' and "
                f"'{JOIN_FLAG_FIELD}' = 1, drawn\n"
                "hatched magenta; released overlaps are cut out of the other barangays. Dissolve by\n"
                "geocode to merge them. Uses the ticks in the viewer; nothing is edited.")
            self.join_btn.clicked.connect(self.join_mbi)
            self.export_btn = button("Export GPKG + CSV", 32, BTN_EXPORT)
            self.export_btn.setToolTip(
                "Write the processed MBI layer (with the coverage fields) and the coverage parts\n"
                "to a GeoPackage, plus a CSV of every case / barangay decision, into the Export folder.\n"
                "Choices not yet applied are applied first.")
            self.export_btn.clicked.connect(self.export_results)
            arow.addWidget(self.auto_btn, 1)
            arow.addWidget(self.reset_btn, 1)
            arow.addWidget(self.apply_btn, 2)
            arow.addWidget(self.join_btn, 2)
            arow.addWidget(self.export_btn, 2)
            gf.addLayout(arow)
            # Next step: pick the covering EA inside each covering barangay (MBI EA Selector)
            nrow = QtWidgets.QHBoxLayout()
            self.next_label = QtWidgets.QLabel(
                "<b>Next:</b> choose which EA of the covering barangay covers each MBI.")
            self.next_label.setWordWrap(True)
            nrow.addWidget(self.next_label, 1)
            self.ea_sel_btn = button("Choose EAs in MBI EA Selector…", 32)
            self.ea_sel_btn.setToolTip(
                "Open the MBI EA Selector with this MBI layer: every MBI is listed under the covering\n"
                "barangay chosen here, and only that barangay's EAs are offered. Choices not yet\n"
                f"applied are applied first (the EA Selector reads '{COVERAGE_FIELD}').")
            self.ea_sel_btn.clicked.connect(self.open_ea_selector)
            nrow.addWidget(self.ea_sel_btn)
            gf.addLayout(nrow)
            self.apply_group.setEnabled(False)
            rl.addWidget(self.apply_group)

            rl.addWidget(QtWidgets.QLabel("Log"))
            self.log_box = QtWidgets.QPlainTextEdit()
            self.log_box.setReadOnly(True)
            self.log_box.setFixedHeight(150)
            rl.addWidget(self.log_box)

            root.addWidget(left_scroll)
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
            for label, data in [("All cases", None), ("Pending", "pending"),
                                ("Partly covered", "partial"), ("Covered", "covered"),
                                (DETECT_NEW, f"det:{DETECT_NEW}"), (DETECT_STILL, f"det:{DETECT_STILL}"),
                                (DETECT_GONE, f"det:{DETECT_GONE}"),
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
            self.bgy_list.setStyleSheet(t["list"])
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
            for lab in (self.mbi_hint, self.bgy_hint, self.bldg_hint, self.folder_hint,
                        self.layer_count_label, self.run_hint, self.remaining_hint):
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
                    sig.disconnect(self.refresh_bgy_list)
                except (TypeError, RuntimeError):
                    pass

        def done(self, result):
            self._teardown()
            super().done(result)

        def closeEvent(self, event):
            self._teardown()
            super().closeEvent(event)

        # ──────────────── barangay layer list (multi-select) ────────────────
        def _bgy_items(self):
            return [self.bgy_list.item(i) for i in range(self.bgy_list.count())
                    if self.bgy_list.item(i).data(Qt.UserRole)]

        def bgy_layers(self):
            prj = QgsProject.instance()
            return [lyr for lyr in (prj.mapLayer(i.data(Qt.UserRole)) for i in self._bgy_items()
                                    if i.checkState() == Qt.Checked) if lyr is not None]

        def refresh_bgy_list(self, *_args):
            """Polygon layers other than the MBI layer, keeping existing checks."""
            checked = {i.data(Qt.UserRole) for i in self._bgy_items() if i.checkState() == Qt.Checked}
            skip = {l.id() for l in (self.mbi_combo.currentLayer(), self.remaining_combo.currentLayer())
                    if l is not None}
            layers = [l for l in QgsProject.instance().mapLayers().values()
                      if isinstance(l, QgsVectorLayer)
                      and l.geometryType() == QgsWkbTypes.PolygonGeometry
                      and l.id() not in skip
                      and "case_uuid" not in field_names(l)]
            self.bgy_list.blockSignals(True)
            self.bgy_list.clear()
            for layer in sorted(layers, key=lambda l: l.name().lower()):
                item = QtWidgets.QListWidgetItem(layer.name())
                item.setData(Qt.UserRole, layer.id())
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if layer.id() in checked else Qt.Unchecked)
                if not _pick_field(layer, BGY_GEOCODE_FIELDS):
                    item.setToolTip("No 'geocode' / 'bgy_geocode' field — barangays cannot be matched.")
                    item.setForeground(QBrush(QColor("#9e9e9e")))
                self.bgy_list.addItem(item)
            self.bgy_list.blockSignals(False)
            self.validate_inputs()

        # ──────────────── remaining MBI layer ────────────────
        def _pick_remaining_default(self):
            """Pre-select a loaded layer named like a remaining MBI layer (remaining_cases…)."""
            layers = [l for l in QgsProject.instance().mapLayers().values()
                      if isinstance(l, QgsVectorLayer) and l.geometryType() == QgsWkbTypes.PolygonGeometry]
            for wanted in GPKG_REMAINING_LAYERS:
                for layer in layers:
                    if wanted in layer.name().lower():
                        self.remaining_combo.setLayer(layer)
                        return

        def browse_remaining_gpkg(self):
            """Add the remaining MBI layer of a GeoPackage (e.g. ref_mbi_reviewed-….gpkg) to the
            project and pick it: 'remaining_cases' first, else 'pending_cases' / 'ref_mbi_cases',
            else ask which polygon layer."""
            path, _f = QtWidgets.QFileDialog.getOpenFileName(
                self, "Open the remaining MBI GeoPackage", "", "GeoPackage (*.gpkg);;All files (*)")
            if not path:
                return
            try:
                from osgeo import ogr
                ds = ogr.Open(path)
                names = [ds.GetLayer(i).GetName() for i in range(ds.GetLayerCount())] if ds else []
                ds = None
            except Exception as e:
                self.log(f"Cannot read '{path}': {e}")
                names = []
            layers = []
            for name in names:
                layer = QgsVectorLayer(f"{path}|layername={name}", name, "ogr")
                if layer.isValid() and layer.geometryType() == QgsWkbTypes.PolygonGeometry:
                    layers.append(layer)
            if not layers:
                QtWidgets.QMessageBox.warning(self, DIALOG_TITLE,
                                              f"No polygon layer found in:\n{path}")
                return
            pick = next((l for wanted in GPKG_REMAINING_LAYERS for l in layers
                         if l.name().lower() == wanted), None)
            if pick is None and len(layers) == 1:
                pick = layers[0]
            if pick is None:
                choice, ok = QtWidgets.QInputDialog.getItem(
                    self, DIALOG_TITLE, "Which layer holds the remaining MBIs?",
                    [l.name() for l in layers], 0, False)
                if not ok:
                    return
                pick = next(l for l in layers if l.name() == choice)
            pick.setName(f"{pick.name()} ({os.path.splitext(os.path.basename(path))[0]})")
            QgsProject.instance().addMapLayer(pick)
            self.remaining_combo.setLayer(pick)
            self.log(f"Added remaining MBI layer '{pick.name()}' ({pick.featureCount():,} MBI(s)) "
                     f"from {path}")

        def _check_all(self, on):
            self.bgy_list.blockSignals(True)
            for item in self._bgy_items():
                item.setCheckState(Qt.Checked if on else Qt.Unchecked)
            self.bgy_list.blockSignals(False)
            self.validate_inputs()

        # ──────────────── validation ────────────────
        def validate_inputs(self, *_args):
            if not hasattr(self, "log_box"):
                return False
            ok = True
            mbi = self.mbi_combo.currentLayer()
            if mbi is None:
                self.set_hint(self.mbi_hint, "Optional: Run Check fills this in. Pick a layer here only "
                                             "to reopen an earlier result.")
                ok = False
            elif "case_uuid" not in field_names(mbi):
                self.set_hint(self.mbi_hint, "Missing 'case_uuid' field — use Run Check & "
                                             "Counter-Check to produce an MBI layer.", "error")
                ok = False
            else:
                kind = infer_mbi_type(mbi.name())
                self.set_hint(self.mbi_hint, f"{mbi.featureCount():,} MBI row(s)"
                              + (f" · {kind}s" if kind else "")
                              + (" · counter-checked" if DETECT_FIELD in field_names(mbi) else ""), "ok")

            remaining = self.remaining_combo.currentLayer()
            if remaining is None:
                self.set_hint(self.remaining_hint, "Pick the remaining MBI layer (or Browse GPKG…). "
                                                   "Without it Run Check only lists what it finds — "
                                                   "nothing is counter-checked.")
            elif not _pick_field(remaining, REF_SOURCES["case_uuid"]):
                self.set_hint(self.remaining_hint, "No 'case_uuid' field — is this a remaining MBI "
                                                   "layer? Cases are still matched by location.", "error")
            else:
                has_type = _pick_field(remaining, REF_TYPE_FIELDS)
                self.set_hint(self.remaining_hint, f"{remaining.featureCount():,} remaining MBI(s)"
                              + ("" if has_type else " · no 'mbi_type' field: matched to gaps and "
                                 "overlaps alike"), "ok")

            bgys = self.bgy_layers()
            self.set_hint(self.layer_count_label,
                          f"Selected layers: {len(bgys)} / {len(self._bgy_items())}")
            if not bgys:
                self.set_hint(self.bgy_hint, "Optional: without barangay layers, names come from the "
                                             "MBI layer; zoom to barangay and shared overlaps are off.")
            else:
                bad = [l.name() for l in bgys if not _pick_field(l, BGY_GEOCODE_FIELDS)]
                if bad:
                    self.set_hint(self.bgy_hint, "No 'geocode' / 'bgy_geocode' field in: "
                                  + ", ".join(bad), "error")
                else:
                    total = sum(l.featureCount() for l in bgys)
                    self.set_hint(self.bgy_hint, f"{total:,} polygon(s) in the selected layers.", "ok")

            bldg = self.bldg_combo.currentLayer()
            if bldg is None:
                self.set_hint(self.bldg_hint, "Optional: building counts per case / barangay will be skipped.")
            elif not self.bldg_field():
                self.set_hint(self.bldg_hint, f"{bldg.featureCount():,} building point(s). Pick the "
                                              "geocode field to count buildings per barangay (case totals "
                                              "only otherwise).")
            else:
                self.set_hint(self.bldg_hint, f"{bldg.featureCount():,} building point(s) · barangay counts "
                                              f"match '{self.bldg_field()}'", "ok")

            names = (self.coverage_field(), self.status_field())
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

            # Run Check: needs barangay layers carrying the checker's fields.
            if not bgys:
                self._run_ok = False
                self.set_hint(self.run_hint, "Tick the barangay layer(s) to check.")
            else:
                missing = [l.name() for l in bgys
                           if any(not _pick_field(l, [f]) for f in CHECKER_REQUIRED_FIELDS)]
                if missing:
                    self._run_ok = False
                    self.set_hint(self.run_hint, "Needs a 'geocode' field — missing in: "
                                  + ", ".join(missing), "error")
                elif not bgys[0].crs().isGeographic():
                    self._run_ok = True
                    self.set_hint(self.run_hint, f"'{bgys[0].name()}' is not in a geographic CRS "
                                                 "(EPSG:4326 expected) — results may be off.", "error")
                else:
                    self._run_ok = True
                    self.set_hint(self.run_hint, f"Checks {len(bgys)} barangay layer(s)"
                                  + ("" if bldg is not None else ", without building counts")
                                  + (f" against '{remaining.name()}'" if remaining is not None
                                     else " (no counter-check: no remaining MBI layer)") + ".")

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
            self.cases, self.case_items, self.bgy_lookup = {}, {}, {}
            self.mbi_layer_id = None
            self.log_box.clear()
            self.progress.setValue(0)
            self._fill_show_combo()
            self.validate_inputs()
            self.update_summary()

        def set_busy(self, busy):
            self.running = busy
            self.cancel_requested = False
            for w in (self.mbi_combo, self.bgy_list, self.btn_all, self.btn_none, self.bldg_combo,
                      self.remaining_combo, self.browse_btn,
                      self.selected_only, self.preload_check, self.skip_within, self.tree,
                      self.search_edit,
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
            self.join_btn.setEnabled(bool(self.bgy_layers()))
            self.zoom_btn.setEnabled(idle and bool(self.tree.selectedItems()))

        def update_summary(self):
            total = len(self.cases)
            n = self._status_counts()
            covered, partial, gone = n["covered"], n["partial"], n["gone"]
            shown = sum(1 for t in self.case_items.values() if not t.isHidden())
            if not total:
                text = NO_CASES
            else:
                det = {}
                for c in self.cases.values():
                    if c.detection:
                        det[c.detection] = det.get(c.detection, 0) + 1
                text = f"{total:,} MBI case(s)"
                if det:
                    text += " (" + ", ".join(f"{det[d]:,} {d.lower()}" for d in
                                             (DETECT_NEW, DETECT_STILL, DETECT_GONE) if det.get(d)) + ")"
                text += (f" - {n['pending']:,} pending, "
                         + (f"{partial:,} partly covered, " if partial else "")
                         + f"{covered:,} covered.")
                if shown != total:
                    text += f" Showing {shown:,}."
                unapplied = any(c.assigned != c.saved for c in self.cases.values())
                if covered < total - gone:
                    text += " Tick the covering barangay(s) of each case in the viewer."
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
            """Run the built-in MBI Checker on the ticked barangay layers, counter-check every MBI it
            finds against the remaining MBI layer, add the ppp_mbi layer to the project
            and load its cases into the viewer."""
            if self.running:
                return
            self.validate_inputs()
            if not self._run_ok or not self._unapplied_ok("Run a new check"):
                return
            bgys = self.bgy_layers()
            bldg = self.bldg_combo.currentLayer()
            remaining = self.remaining_combo.currentLayer()
            mode = self.mode_combo.currentIndex()
            checked_kinds = mode_kinds(mode)

            self.set_busy(True)
            self.log("=" * 50)
            self.log(f"Running MBI Checker {CHECKER_VERSION} ({RUN_MODES[mode]}) on {len(bgys)} "
                     "barangay layer(s): "
                     + ", ".join(l.name() for l in bgys))
            if bldg is None:
                self.log("  No Building Points layer — building counts will be 0.")
            self.summary.setText("Running the MBI Checker…")
            result = None
            start = datetime.now()
            try:
                feedback = self._run_feedback = CheckFeedback(self)
                found = run_mbi_checker(bgys, [bldg] if bldg is not None else [], mode, feedback)
                if feedback.isCanceled():
                    raise _Cancelled()
                detected = []
                for kind in ("Disputed", "Overlap", "Gap"):
                    layer = found.get(kind)
                    if layer is not None:
                        detected.append((layer, kind))
                        self.log(f"  {'Disputed areas' if kind == 'Disputed' else kind + 's'} found: "
                                 f"{layer.featureCount():,}")

                if remaining is not None:
                    self.summary.setText(f"Counter-checking against '{remaining.name()}'…")
                    QtWidgets.QApplication.processEvents()
                bgy_info, provinces = read_bgy_info(bgys)
                name = mbi_output_name(provinces)
                result, stats = build_counter_check(detected, remaining, checked_kinds, name, bgy_info)
                if stats["rows"]:
                    self.log(f"  {stats['detected']:,} detected MBI(s) written as {stats['rows']:,} row(s) — "
                             "one per involved barangay, each with its own coverage status")
                if remaining is not None:
                    self._log_counter_check(remaining, stats, checked_kinds)
                elapsed = str(datetime.now() - start).split(".")[0]
                if result is None:
                    self.log(f"No gaps, overlaps or disputed areas found and no remaining MBI to list "
                             f"({elapsed}).")
                else:
                    self._style_results(result)
                    QgsProject.instance().addMapLayer(result)
                    self.log(f"Added layer '{result.name()}' ({result.featureCount():,} row(s), {elapsed}). "
                             "It is a temporary layer — right-click > Make Permanent to keep it.")
            except _Cancelled:
                self.log("MBI check cancelled.")
                result = None
            except Exception as e:
                self.log(traceback.format_exc().rstrip())
                self.log(f"MBI check FAILED: {e}")
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

        def _log_counter_check(self, remaining, s, checked_kinds):
            self.log(f"Counter-check against '{remaining.name()}' ({s['remaining']:,} remaining MBI(s)"
                     + (f", {s['disputed']:,} of them Disputed" if s["disputed"] else "") + "):")
            self.log(f"  {DETECT_STILL}: {s['still']:,} MBI(s) found by the check, matching "
                     f"{s['matched']:,} remaining MBI(s)")
            self.log(f"  {DETECT_NEW}: {s['new']:,} MBI(s) not in the remaining layer "
                     "(light green in the list)")
            self.log(f"  {DETECT_GONE}: {s['gone']:,} remaining MBI(s) the check did not find")
            if s["disputed"]:
                self.log(f"  Disputed: {s['disputed_still']:,} of {s['disputed']:,} remaining Disputed MBI(s) "
                         "still detected (as a contested polygon, gap or overlap sharing their area)")
            if s["unchecked"]:
                self.log(f"  Not checked: {s['unchecked']:,} remaining MBI(s) of a type this run did not "
                         f"look for (only {', '.join(sorted(checked_kinds)).lower()}; Disputed needs all "
                         "three) — left out")
            if s["multi"]:
                self.log(f"  {s['multi']:,} detection(s) overlap more than one remaining MBI "
                         f"('{REF_UUID_FIELD}' lists them all)")
            if s["split"]:
                self.log(f"  {s['split']:,} remaining MBI(s) are now found as several pieces "
                         "(each piece is its own case)")

        def _style_results(self, layer):
            """Colour the MBI layer on the map by detection (or by MBI type without a counter-check)."""
            field = DETECT_FIELD if layer.fields().indexOf(DETECT_FIELD) >= 0 else MBI_TYPE
            styles = {DETECT_NEW: ("139,195,74,140", "51,105,30,255", "solid"),
                      DETECT_STILL: ("251,140,0,110", "230,81,0,255", "solid"),
                      DETECT_GONE: ("189,189,189,60", "97,97,97,255", "dash")}
            values = sorted({f[field] for f in layer.getFeatures() if f[field]})
            cats = []
            for value in values:
                fill, stroke, line = styles.get(value) or (
                    ("30,136,229,110", "13,71,161,255", "solid") if "gap" in str(value).lower()
                    else ("229,57,53,110", "183,28,28,255", "solid"))
                sym = QgsFillSymbol.createSimple({"color": fill, "outline_color": stroke,
                                                  "outline_width": "0.6", "outline_style": line})
                cats.append(QgsRendererCategory(value, sym, str(value)))
            if cats:
                layer.setRenderer(QgsCategorizedSymbolRenderer(field, cats))

        def load_cases(self, use_selection=True):
            if self.running or not self.validate_inputs():
                return
            mbi = self.mbi_combo.currentLayer()
            bgys = [l for l in self.bgy_layers() if _pick_field(l, BGY_GEOCODE_FIELDS)]
            bldg = self.bldg_combo.currentLayer()
            bldg_field = self.bldg_field()

            only_fids = None
            if use_selection and self.selected_only.isChecked():
                only_fids = mbi.selectedFeatureIds()
                if not only_fids:
                    QtWidgets.QMessageBox.warning(
                        self, DIALOG_TITLE, f"No features are selected in '{mbi.name()}'.")
                    return

            previous = ({u: list(c.assigned) for u, c in self.cases.items() if c.assigned}
                        if mbi.id() == self.mbi_layer_id else {})
            field = self.coverage_field()
            preload = field if (self.preload_check.isChecked()
                                and mbi.fields().indexOf(field) >= 0) else None

            self.set_busy(True)
            self.log("=" * 50)
            self.log(f"Loading MBI cases from '{mbi.name()}'"
                     + (f" ({len(only_fids):,} selected feature(s))" if only_fids else "") + "…")
            try:
                if mbi.fields().indexOf(MBI_GEOCODE) < 0 and mbi.fields().indexOf(MBI_INVOLVED_AREAS) < 0:
                    raise RuntimeError(f"'{mbi.name()}' has no '{MBI_GEOCODE}' / '{MBI_INVOLVED_AREAS}' "
                                       "field (the barangay geocodes) — use Run Check & Counter-Check "
                                       "to create one.")
                cases, skipped = load_mbi_cases(
                    mbi, infer_mbi_type(mbi.name()), only_fids, preload,
                    tick=lambda d, t: self.tick(d, t * 2 if bldg else t, "Reading MBI cases…"),
                    skip_within=self.skip_within.isChecked())
                involved = {c for case in cases.values() if not case.gone for c in case.candidates}
                self.log(f"  {len(cases):,} case(s) involving {len(involved):,} barangay(s)")
                det = {}
                for case in cases.values():
                    if case.detection:
                        det[case.detection] = det.get(case.detection, 0) + 1
                if det:
                    self.log("  " + " · ".join(f"{d}: {det[d]:,}" for d in
                                               (DETECT_NEW, DETECT_STILL, DETECT_GONE) if d in det))
                if skipped:
                    self.log(f"  Skipped {skipped:,} within-barangay case(s) (one barangay only — "
                             "untick 'Skip within-barangay cases' to list them)")
                if preload:
                    resumed = sum(1 for c in cases.values() if c.assigned)
                    self.log(f"  Resumed {resumed:,} saved choice(s) from '{preload}'")
                for uuid, codes in previous.items():
                    if uuid in cases:
                        cases[uuid].assigned = [c for c in codes if c in cases[uuid].candidates]
                self.da = self._distance_area(mbi)
                self.load_geometry(cases, mbi)

                lookup = {}
                if bgys:
                    self.log(f"Reading {len(bgys)} barangay layer(s): " + ", ".join(l.name() for l in bgys))
                    lookup, dupes = read_bgy_lookup(bgys)
                    for case in cases.values():
                        for cand in case.candidates.values():
                            info = lookup.get(cand.code)
                            if info:
                                cand.name = cand.name or info["name"]
                                cand.city_mun = cand.city_mun or info["city_mun"]
                        case.finish()
                    per_layer = {}
                    for code in involved:
                        if code in lookup:
                            lid = lookup[code]["layer_id"]
                            per_layer[lid] = per_layer.get(lid, 0) + 1
                    for layer in bgys:
                        self.log(f"  {layer.name()}: {per_layer.get(layer.id(), 0):,} involved barangay(s)")
                    missing = sorted(involved - set(lookup))
                    if missing:
                        self.log(f"  {len(missing):,} involved barangay(s) not found in the selected layers: "
                                 + ", ".join(missing[:10]) + (" …" if len(missing) > 10 else ""))
                        self.set_hint(self.bgy_hint, f"{len(missing):,} involved barangay(s) are not in the "
                                                    "selected layers — select the layer(s) they come "
                                                    "from.", "error")
                    else:
                        self.set_hint(self.bgy_hint, f"All {len(involved):,} involved barangay(s) found in "
                                                    f"the {len(bgys)} selected layer(s).", "ok")
                    if dupes:
                        self.log(f"  {dupes:,} barangay geocode(s) found in more than one layer — "
                                 "the first selected layer's barangay is used.")
                    self.compute_parts(cases, mbi, lookup)
                if bldg is not None:
                    self.count_buildings(cases, mbi, bldg, bldg_field)
                for case in cases.values():
                    if not case.split:
                        case.assigned = case.assigned[:1]
                    case.resolve(self.da)

                self.cases = cases
                self.bgy_lookup = lookup
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

        def _distance_area(self, layer):
            """Ellipsoidal areas in m², whatever the layer CRS (degrees for EPSG:4326)."""
            return make_distance_area(layer.crs())

        def load_geometry(self, cases, mbi_layer):
            """MBI geometry of each case. Every row of a case carries the same geometry, so
            one row per case is read."""
            first_fid = {case.feature_ids[0]: case for case in cases.values() if case.feature_ids}
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
                case = first_fid[feat.id()]
                case.geom = _polygonal(geom)
                case.area = _area(case.geom, self.da)

        def compute_parts(self, cases, mbi_layer, lookup):
            """Clip each case's MBI to every involved barangay (MBI ∩ barangay). An overlap whose
            involved barangays all have a part is 'split': several barangays can cover it, each
            keeping only its own part. A barangay stored as several polygons is clipped to their
            union."""
            prj = QgsProject.instance()
            wanted = {}   # barangay code -> [(case, cand)]
            for case in cases.values():
                if case.geom is None:
                    continue
                for cand in case.candidates.values():
                    if cand.code in lookup:
                        wanted.setdefault(cand.code, []).append((case, cand))
            by_layer = {}   # layer_id -> {fid: code}
            for code in wanted:
                info = lookup[code]
                for fid in info["fids"]:
                    by_layer.setdefault(info["layer_id"], {})[fid] = code
            total = max(sum(len(v) for v in by_layer.values()), 1)
            done = 0
            pieces = {}   # code -> [geometry in the MBI CRS]
            for layer_id, by_fid in by_layer.items():
                layer = prj.mapLayer(layer_id)
                if layer is None:
                    continue
                xform = QgsCoordinateTransform(layer.crs(), mbi_layer.crs(), prj)
                request = QgsFeatureRequest().setFilterFids(list(by_fid)).setNoAttributes()
                for feat in layer.getFeatures(request):
                    if done % 50 == 0:
                        self.tick(done, total, f"Reading barangay shapes ({done:,}/{total:,})…")
                    done += 1
                    if not feat.hasGeometry():
                        continue
                    g = QgsGeometry(feat.geometry())
                    if layer.crs() != mbi_layer.crs():
                        g.transform(xform)
                    if not g.isGeosValid():
                        g = g.makeValid()
                    pieces.setdefault(by_fid[feat.id()], []).append(g)
            total = max(len(pieces), 1)
            for i, (code, geoms) in enumerate(pieces.items()):
                if i % 25 == 0:
                    self.tick(i, total, f"Clipping MBIs to their barangays ({i:,}/{total:,})…")
                bgy_geom = geoms[0] if len(geoms) == 1 else QgsGeometry.unaryUnion(geoms)
                if bgy_geom is None or bgy_geom.isNull():
                    continue
                engine = QgsGeometry.createGeometryEngine(bgy_geom.constGet())
                engine.prepareGeometry()
                for case, cand in wanted[code]:
                    if not engine.intersects(case.geom.constGet()):
                        continue
                    cand.part = _polygonal(case.geom.intersection(bgy_geom))
                    cand.part_area = _area(cand.part, self.da)

            split = unsplit = pairs = 0
            for case in cases.values():
                cands = list(case.candidates.values())
                is_overlap = case.mbi_type.lower() != "gap" and not case.gone
                # Two candidates: one barangay or the other (ticking one unticks the other).
                # Only an MBI with 3+ candidate barangays can be shared out (several ticks).
                case.split = (is_overlap and case.geom is not None and len(cands) > 2
                              and all(c.part is not None for c in cands)
                              and sum(c.part_area for c in cands) > SLIVER_M2)
                if case.split:
                    split += 1
                elif is_overlap and len(cands) == 2:
                    pairs += 1
                elif is_overlap and len(cands) > 2:
                    unsplit += 1
            if pairs:
                self.log(f"  {pairs:,} overlap / disputed case(s) between two barangays — one barangay "
                         "covers the whole MBI (ticking one unticks the other)")
            self.log(f"  {split:,} overlap / disputed case(s) can be shared out between their barangays "
                     "(each covering barangay keeps only its part)")
            if unsplit:
                self.log(f"  {unsplit:,} overlap / disputed case(s) have an involved barangay missing from the "
                         "selected layers — one barangay covers the whole MBI there")

        def count_buildings(self, cases, mbi_layer, bldg_layer, gc_field=None):
            """Case: building points inside the MBI area. Barangay: those of them whose geocode
            (gc_field) is the barangay's geocode or starts with it (EA geocodes begin with their
            barangay's). Points matching no involved barangay are counted as untagged."""
            prj = QgsProject.instance()
            xform = QgsCoordinateTransform(mbi_layer.crs(), bldg_layer.crs(), prj)
            with_geom = [case for case in cases.values() if case.geom is not None]
            total = max(len(with_geom), 1)
            self.log(f"Counting buildings inside {len(with_geom):,} MBI case(s) from "
                     f"'{bldg_layer.name()}'"
                     + (f", per barangay by '{gc_field}'…" if gc_field else " (no geocode field: case totals only)…"))
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
                    # kept in the MBI CRS to count the buildings in each barangay's kept part
                    case.bldg_geoms.append(QgsGeometry.fromPointXY(xform.transform(
                        p.geometry().centroid().asPoint(), QgsCoordinateTransform.ReverseTransform)))
                    cand = match_bldg_code(p[gc_field], by_key) if gc_field else None
                    if cand is not None:
                        cand.bldg += 1
                        n_tagged += 1
                    else:
                        case.bldg_untagged += 1
                n_in += case.bldg_live
            self.log(f"  {n_in:,} building point(s) inside MBI cases"
                     + (f", {n_tagged:,} matched to an involved barangay, {n_in - n_tagged:,} untagged"
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
                n = len(case.candidates)
                top.setText(COL_BGY, f"{n} barangay{'s' if n != 1 else ''}: {case.barangays}"
                            if case.barangays else f"{n} barangay{'s' if n != 1 else ''} involved")
                top.setToolTip(COL_BGY, case.barangays)
                top.setText(COL_CITY, case.places)
                top.setToolTip(COL_CITY, case.places)
                top.setData(COL_BLDG, Qt.DisplayRole, case.bldg_pts)
                top.setTextAlignment(COL_BLDG, Qt.AlignRight | Qt.AlignVCenter)
                tip = f"{case.bldg_pts:,} building point(s) inside this {case.mbi_type.lower() or 'MBI'}"
                if case.bldg_live is not None:
                    if case.bldg_untagged:
                        tip += f"\n{case.bldg_untagged:,} not tagged to any involved barangay"
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
                top.setText(COL_DETECT, case.detection)
                top.setToolTip(COL_DETECT, self._detection_tip(case))
                if case.detection == DETECT_NEW:
                    # Newly detected: the whole row (except the status colour) in light green.
                    for col in range(len(TREE_COLS)):
                        if col != COL_ROLE:
                            paint(top, col, COLOUR_NEW)
                    font = top.font(COL_DETECT)
                    font.setBold(True)
                    top.setFont(COL_DETECT, font)
                elif case.gone:
                    for col in range(len(TREE_COLS)):
                        if col != COL_ROLE:
                            top.setForeground(col, QBrush(QColor("#9e9e9e")))

                for c_order, cand in enumerate(case.candidates.values()):
                    child = TreeItem(top)
                    child.setData(COL_ID, ROLE_ORDER, c_order)
                    child.setText(COL_ID, cand.code)
                    child.setData(COL_ID, ROLE_UUID, case.case_uuid)
                    child.setData(COL_ID, ROLE_BGY, cand.code)
                    child.setText(COL_BGY, cand.name)
                    if cand.eas:
                        child.setToolTip(COL_BGY, f"{len(cand.eas)} EA(s) of this barangay in the MBI:\n"
                                         + ", ".join(cand.eas))
                    child.setText(COL_CITY, cand.place)
                    if case.detection == DETECT_NEW:
                        for col in range(len(TREE_COLS)):
                            if col != COL_ROLE:
                                paint(child, col, COLOUR_NEW_CHILD)
                    elif case.gone:
                        for col in range(len(TREE_COLS)):
                            child.setForeground(col, QBrush(QColor("#9e9e9e")))
                    if self.bgy_lookup and cand.code not in self.bgy_lookup and not case.gone:
                        child.setToolTip(COL_ID, "Not found in the selected barangay layers")
                        child.setForeground(COL_ID, QBrush(QColor("#c62828")))
                    if cand.bldg is not None:
                        child.setData(COL_BLDG, Qt.DisplayRole, cand.bldg)
                        child.setToolTip(COL_BLDG, f"{cand.bldg:,} building point(s) inside this MBI "
                                                   f"tagged to {cand.code}")
                    child.setTextAlignment(COL_BLDG, Qt.AlignRight | Qt.AlignVCenter)
                    if case.gone:
                        child.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                    else:
                        child.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
                        child.setCheckState(COL_ROLE, Qt.Unchecked)
                self.tree.addTopLevelItem(top)
                self.case_items[case.case_uuid] = top
                self.paint_case(case)

            self.tree.setSortingEnabled(True)
            # Counter-checked: newly detected first ('Newly…' < 'No longer…' < 'Still…'),
            # each group by level, then by buildings.
            counter_checked = any(c.detection for c in self.cases.values())
            self.tree.setColumnHidden(COL_DETECT, not counter_checked)
            self.tree.sortByColumn(COL_DETECT if counter_checked else COL_LEVEL, Qt.AscendingOrder)
            self.tree.expandAll()
            for col in range(len(TREE_COLS)):
                self.tree.resizeColumnToContents(col)
            self.tree.setColumnWidth(COL_BGY, min(self.tree.columnWidth(COL_BGY), 220))
            self._painting = False
            self._fill_show_combo({c.level for c in self.cases.values() if c.level})
            self.apply_filter()

        def _detection_tip(self, case):
            if not case.detection:
                return ""
            tip = {DETECT_NEW: "Found by the MBI Checker but not in the remaining MBI layer.",
                   DETECT_STILL: "The check still finds this remaining MBI.",
                   DETECT_GONE: "This remaining MBI was not found by the check — nothing to cover."
                   }.get(case.detection, case.detection)
            if case.ref_uuids and case.detection != DETECT_NEW:
                tip += f"\nRemaining MBI case_uuid: {case.ref_uuids.replace(',', ', ')}"
            if case.ref_kind and case.detection != DETECT_NEW and case.ref_kind != case.mbi_type:
                tip += f"\nRemaining MBI type: {case.ref_kind}"
            if case.ref_status:
                tip += f"\nDeclared status: {case.ref_status}"
            if case.ref_remarks:
                tip += f"\nRemarks: {case.ref_remarks}"
            return tip

        def _area_text(self, m2):
            return f"{m2 / 10000:,.2f} ha" if m2 >= 10000 else f"{m2:,.1f} m²"

        def _covered_by(self, case, long=False):
            # Barangay names repeat across municipalities ("Poblacion"), so lists use the geocode.
            cands = [case.candidates[c] for c in case.assigned]
            if len(cands) == 1 and not long:
                return cands[0].label
            if len(cands) > 2 and not long:
                return f"{len(cands)} barangays"
            return ", ".join(c.label if long else c.code for c in cands)

        def paint_case(self, case):
            top = self.case_items.get(case.case_uuid)
            if top is None:
                return
            was_painting, self._painting = self._painting, True
            if case.gone:
                for i in range(top.childCount()):
                    top.child(i).setText(COL_ROLE, "—")
                    top.child(i).setToolTip(COL_ROLE, "Nothing to cover: the check no longer finds this MBI.")
                top.setText(COL_ROLE, DETECT_GONE)
                top.setToolTip(COL_ROLE, "The check no longer finds this remaining MBI, so no barangay "
                                         "needs to cover it.\nIts fields are left untouched by Apply Coverage.")
                paint(top, COL_ROLE, COLOUR_GONE)
                self._painting = was_painting
                return
            gap = case.mbi_type.lower() == "gap"
            on_text = "✔ Absorbs gap" if gap else "✔ Keeps overlap" if case.mbi_type else "✔ Covers MBI"
            off_text = "Not extended" if gap else "Releases (clip)" if case.mbi_type else "Releases"
            for i in range(top.childCount()):
                child = top.child(i)
                code = child.data(COL_ID, ROLE_BGY)
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
                        text = f"✔ Keeps its part ({_pct(share)})" if cand.kept_area > SLIVER_M2 \
                            else "✔ Keeps nothing (already kept)"
                        if cand.kept_bldg:
                            text += f" · {cand.kept_bldg:,} bldg"
                        tip += (f"\nKeeps {self._area_text(cand.kept_area)}"
                                + (" (the rest of its part is kept by a barangay ticked before it)"
                                   if cand.kept_area < cand.part_area - SLIVER_M2 else ""))
                    else:
                        text = on_text
                    child.setText(COL_ROLE, text)
                    paint(child, COL_ROLE, COLOUR_COVERED)
                elif case.assigned and cand.released >= 0.995:
                    child.setCheckState(COL_ROLE, Qt.Unchecked)
                    child.setText(COL_ROLE, off_text)
                    paint(child, COL_ROLE, COLOUR_RELEASED)
                elif case.assigned and cand.released > 0.005:
                    child.setCheckState(COL_ROLE, Qt.Unchecked)
                    child.setText(COL_ROLE, f"Releases {_pct(cand.released)} · candidate")
                    tip += (f"\n{_pct(cand.released)} of its part is kept by the ticked barangay(s); "
                            "tick it to keep the rest.")
                    paint(child, COL_ROLE, None)
                else:
                    child.setCheckState(COL_ROLE, Qt.Unchecked)
                    child.setText(COL_ROLE, "Candidate")
                    paint(child, COL_ROLE, None)
                child.setToolTip(COL_ROLE, tip.strip())
            status = case.status
            if status == "covered":
                top.setText(COL_ROLE, f"Covered by {self._covered_by(case)}")
                top.setToolTip(COL_ROLE, f"Covered by {self._covered_by(case, True)}")
                paint(top, COL_ROLE, COLOUR_COVERED)
            elif status == "partial":
                top.setText(COL_ROLE, f"Partly covered ({_pct(case.covered)}) by {self._covered_by(case)}")
                top.setToolTip(COL_ROLE, f"{_pct(case.covered)} of the MBI is kept by "
                                         f"{self._covered_by(case, True)}.\n"
                                         f"{self._area_text(case.area * (1 - case.covered))} has no "
                                         "covering barangay yet — tick another barangay to cover it.")
                paint(top, COL_ROLE, COLOUR_PARTIAL)
            else:
                top.setText(COL_ROLE, "Pending")
                top.setToolTip(COL_ROLE, "Tick the covering barangay(s) below."
                               + ("\nShared overlap: each ticked barangay keeps only its own part."
                                  if case.split else ""))
                paint(top, COL_ROLE, COLOUR_PENDING)
            self._painting = was_painting

        def on_item_changed(self, item, column):
            if self._painting or column != COL_ROLE or item.parent() is None:
                return
            case = self.cases.get(item.data(COL_ID, ROLE_UUID))
            if case is None or case.gone:
                return
            self.toggle_bgy(case, item.data(COL_ID, ROLE_BGY), item.checkState(COL_ROLE) == Qt.Checked)

        def toggle_bgy(self, case, code, on):
            """Tick / untick a covering barangay. Shared overlaps (3+ candidate barangays) keep every
            ticked barangay (each keeps its own part); otherwise — gaps, and MBIs between two
            barangays — ticking a barangay moves the coverage to it (the other is unticked)."""
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
                    self, DIALOG_TITLE, "Clear every barangay tick in the preview?\n"
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
            if mode and mode.startswith("det:") and case.detection != mode[4:]:
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
            """-> (MBI fids, {bgy_layer_id: fids}) for the given (or selected) tree items."""
            mbi_fids, bgy_fids = set(), {}
            for item in items if items is not None else self.tree.selectedItems():
                case = self.cases.get(item.data(COL_ID, ROLE_UUID))
                if case is None:
                    continue
                code = item.data(COL_ID, ROLE_BGY)
                if code:
                    cand = case.candidates.get(code)
                    # one-row-per-MBI layers: the barangay owns no row, select the MBI row
                    mbi_fids.update(cand.fids if cand and cand.fids else case.feature_ids)
                    info = self.bgy_lookup.get(code)
                    if info:
                        bgy_fids.setdefault(info["layer_id"], set()).update(info["fids"])
                else:
                    mbi_fids.update(case.feature_ids)
            return mbi_fids, bgy_fids

        def on_selection_changed(self):
            self.update_buttons()
            if self.running:
                return
            mbi_fids, bgy_fids = self._selection()
            if self.mbi_layer is not None:
                self.mbi_layer.selectByIds(list(mbi_fids))
            prj = QgsProject.instance()
            for layer_id in {i["layer_id"] for i in self.bgy_lookup.values()}:
                layer = prj.mapLayer(layer_id)
                if layer is not None:
                    layer.selectByIds(list(bgy_fids.get(layer_id, ())))
            self.show_bands()

        # Map highlights: green = part kept by a covering barangay, dashed blue = the part an
        # unticked barangay would keep, orange = part of the MBI no covering barangay keeps yet.
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
            """Highlight on the map the part of the MBI each selected barangay keeps (or would keep)."""
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
                if case is None or not case.split:
                    continue
                code = item.data(COL_ID, ROLE_BGY)
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
            mbi_fids, bgy_fids = self._selection(items)
            prj = QgsProject.instance()
            targets = [(self.mbi_layer, mbi_fids)]
            targets += [(prj.mapLayer(lid), fids) for lid, fids in bgy_fids.items()]
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
                code = item.data(COL_ID, ROLE_BGY)
                if case is not None and case.gone:
                    act = menu.addAction(f"{DETECT_GONE} — nothing to cover")
                    act.setEnabled(False)
                elif case is not None and code and case.split:
                    if code in case.assigned:
                        act = menu.addAction(f"Remove {code} from the covering barangays")
                        act.triggered.connect(lambda: self.toggle_bgy(case, code, False))
                    else:
                        act = menu.addAction(f"Add {code} as covering barangay (keeps its part)")
                        act.triggered.connect(lambda: self.toggle_bgy(case, code, True))
                    act = menu.addAction(f"Make {code} the only covering barangay")
                    act.setEnabled(case.assigned != [code])
                    act.triggered.connect(lambda: self.set_assignment(case, [code]))
                elif case is not None and code:
                    act = menu.addAction(f"Set {code} as covering barangay")
                    act.setEnabled(case.assigned != [code])
                    act.triggered.connect(lambda: self.set_assignment(case, [code]))
                if case is not None:
                    if not case.gone:
                        act = menu.addAction("Mark case as pending")
                        act.setEnabled(bool(case.assigned))
                        act.triggered.connect(lambda: self.set_assignment(case, []))
                    menu.addSeparator()
                    menu.addAction(QgsApplication.getThemeIcon("/mActionZoomToSelected.svg"),
                                   "Zoom to this", lambda: self.zoom_to_items([item]))
                    menu.addAction("Copy case ID",
                                   lambda: QtWidgets.QApplication.clipboard().setText(case.case_uuid))
                    if case.ref_uuids and case.detection != DETECT_NEW:
                        menu.addAction("Copy remaining MBI case ID(s)",
                                       lambda: QtWidgets.QApplication.clipboard().setText(case.ref_uuids))
                menu.addSeparator()
            menu.addAction("Expand all", self.tree.expandAll)
            menu.addAction("Collapse all", self.tree.collapseAll)
            menu.exec_(self.tree.viewport().mapToGlobal(pos))

        # ──────────────── auto-assign ────────────────
        def auto_assign(self):
            single = by_bldg = shared = left = 0
            for case in self.cases.values():
                if case.assigned or not case.candidates or case.gone:
                    continue
                cands = list(case.candidates.values())
                if len(cands) == 1:
                    self.set_assignment(case, [cands[0].code], quiet=True)
                    single += 1
                elif not all(c.bldg is not None for c in cands):
                    left += 1
                elif case.split:
                    # Every barangay with tagged buildings keeps its part, most buildings first.
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
            self.log(f"Auto-assign: {single:,} single-barangay case(s), {by_bldg:,} to the barangay owning most "
                     "of the buildings inside the MBI"
                     + (f", {shared:,} shared overlap(s) to every barangay with tagged buildings "
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

        def _row_status(self, case, cand):
            """Status of one barangay's row: the chosen barangay(s) are 1_Covered (3_Partially Covered
            while part of a shared overlap still has no covering barangay); every barangay the user
            did not choose is 2_Not Covered."""
            if cand.code in case.assigned:
                return STATUS_PARTIAL if case.status == "partial" else STATUS_COVERED
            return STATUS_NOT_COVERED

        def _status_counts(self):
            counts = {"pending": 0, "partial": 0, "covered": 0, "gone": 0}
            for case in self.cases.values():
                counts[case.status] += 1
            return counts

        def _needs_apply(self, layer):
            """True when the viewer holds choices the layer does not have yet (or the layer
            is missing one of the coverage fields)."""
            if any(c.assigned != c.saved for c in self.cases.values()):
                return True
            return layer.fields().indexOf(self.coverage_field()) < 0

        def _kept_parts(self, case):
            """(cand, released_by codes, building count) for every covering barangay of the case
            that keeps a part of the MBI with the current choices."""
            if case.geom is None or case.gone:
                return []
            out = []
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
                out.append((cand, released_by, bldg))
            return out

        # ---- Join MBI to Barangays ----
        def build_joined_layer(self, mbi_crs):
            """Copy of the ticked barangay layers with every kept MBI part joined to the barangay
            that covers it. The MBI parts are their own features carrying the covering barangay's
            attributes plus JOIN_KIND_FIELD = 'MBI' / JOIN_FLAG_FIELD = 1, so they stand out; the
            barangay features have the kept MBI parts cut out (an overlap a barangay released is
            removed from it, a disputed polygon absorbed by its covering barangay disappears).
            Dissolving by geocode gives the corrected barangays. Returns (layer, stats)."""
            from qgis.core import QgsSpatialIndex
            prj = QgsProject.instance()
            bgys = self.bgy_layers()
            if not bgys:
                raise RuntimeError("Tick the barangay layer(s) to join the MBIs into.")
            crs = bgys[0].crs()
            # Output schema: every barangay layer's fields (first one wins), then ours
            ours = [(JOIN_KIND_FIELD, QVariant.String), (JOIN_FLAG_FIELD, QVariant.Int),
                    ("mbi_case_uuid", QVariant.String), ("mbi_type", QVariant.String),
                    ("mbi_level", QVariant.String), ("mbi_detection", QVariant.String),
                    ("mbi_area_m2", QVariant.Double), ("mbi_bldg_pts", QVariant.Int),
                    ("mbi_released_by", QVariant.String), ("src_layer", QVariant.String)]
            reserved = {n.lower() for n, _t in ours} | {"fid"}
            fields, seen = [], set()
            for layer in bgys:
                for f in layer.fields():
                    if f.name().lower() not in reserved and f.name().lower() not in seen:
                        seen.add(f.name().lower())
                        fields.append(QgsField(f))
            fields += [QgsField(n, t) for n, t in ours]
            uri = "MultiPolygon" + (f"?crs={crs.authid()}" if crs.authid() else "")
            out = QgsVectorLayer(uri, f"{JOIN_LAYER_NAME} ({datetime.now():%Y%m%d_%H%M%S})", "memory")
            out.setCrs(crs)
            out.dataProvider().addAttributes(fields)
            out.updateFields()
            names = out.fields().names()
            stats = {"bgy": 0, "mbi": 0, "trimmed": 0, "absorbed": 0, "missing": 0, "cases": 0}

            # Kept MBI parts, in the output CRS
            pieces = []   # (case, cand, released_by, bldg, geometry)
            to_out = QgsCoordinateTransform(mbi_crs, crs, prj)
            for case in self.cases.values():
                kept = self._kept_parts(case)
                stats["cases"] += bool(kept)
                for cand, released_by, bldg in kept:
                    g = QgsGeometry(cand.kept)
                    if mbi_crs != crs:
                        g.transform(to_out)
                    pieces.append((case, cand, released_by, bldg, g))
            if not pieces:
                raise RuntimeError("No MBI has a covering barangay yet — tick the covering barangays "
                                   "(or Auto-Assign) first.")
            index = QgsSpatialIndex()
            for i, (_c, _d, _r, _b, g) in enumerate(pieces):
                f = QgsFeature(i)
                f.setGeometry(g)
                index.addFeature(f)

            def make(geom, values):
                g = QgsGeometry(geom)
                g.convertToMultiType()
                nf = QgsFeature(out.fields())
                nf.setGeometry(g)
                nf.setAttributes([values.get(n) for n in names])
                return nf

            feats = []
            first_attrs = {}   # barangay geocode -> attributes of its first feature
            wanted_codes = {cand.code for _c, cand, _r, _b, _g in pieces}
            total = max(sum(l.featureCount() for l in bgys), 1)
            done = 0
            for layer in bgys:
                xform = QgsCoordinateTransform(layer.crs(), crs, prj)
                gc_field = _pick_field(layer, BGY_GEOCODE_FIELDS)
                lnames = [n for n in layer.fields().names() if n.lower() not in reserved]
                for feat in layer.getFeatures():
                    if done % 200 == 0:
                        self.tick(done, total, f"Joining MBIs to barangays ({done:,}/{total:,})…")
                    done += 1
                    values = {n: feat[n] for n in lnames}
                    values.update({JOIN_KIND_FIELD: JOIN_KIND_BGY, JOIN_FLAG_FIELD: 0,
                                   "src_layer": layer.name()})
                    code = _text(feat, gc_field) if gc_field else ""
                    if code in wanted_codes and code not in first_attrs:
                        first_attrs[code] = dict(values)
                    if not feat.hasGeometry():
                        continue
                    g = QgsGeometry(feat.geometry())
                    if layer.crs() != crs:
                        g.transform(xform)
                    if not g.isGeosValid():
                        g = g.makeValid()
                    hits = [pieces[i][4] for i in index.intersects(g.boundingBox())]
                    hits = [h for h in hits if g.intersects(h)]
                    if hits:
                        cut = _polygonal(g.difference(hits[0] if len(hits) == 1
                                                      else QgsGeometry.unaryUnion(hits)))
                        if cut is None or _area(cut, self.da) <= SLIVER_M2:
                            stats["absorbed"] += 1   # wholly taken by an MBI (e.g. a disputed polygon)
                            continue
                        if abs(_area(cut, self.da) - _area(g, self.da)) > SLIVER_M2:
                            stats["trimmed"] += 1
                        g = cut
                    feats.append(make(g, values))
                    stats["bgy"] += 1

            for case, cand, released_by, bldg, g in pieces:
                values = dict(first_attrs.get(cand.code) or {})
                if not values:
                    stats["missing"] += 1   # covering barangay not in the ticked layers
                    gc_name = _pick_field(out, BGY_GEOCODE_FIELDS)
                    for key, value in ((gc_name, cand.code),
                                       (_pick_field(out, BGY_NAME_FIELDS), cand.name),
                                       (_pick_field(out, BGY_CITY_FIELDS), cand.city_mun)):
                        if key:
                            values[key] = value or None
                values.update({JOIN_KIND_FIELD: JOIN_KIND_MBI, JOIN_FLAG_FIELD: 1,
                               "mbi_case_uuid": case.case_uuid, "mbi_type": case.mbi_type or None,
                               "mbi_level": case.level or None, "mbi_detection": case.detection or None,
                               "mbi_area_m2": round(cand.kept_area, 2), "mbi_bldg_pts": bldg,
                               "mbi_released_by": ",".join(released_by) or None})
                values.setdefault("src_layer", None)
                feats.append(make(g, values))
                stats["mbi"] += 1
            out.dataProvider().addFeatures(feats)
            out.updateExtents()
            return out, stats

        def _style_joined(self, layer):
            """Barangays plain; joined MBI parts in a unique hatched magenta."""
            bgy_sym = QgsFillSymbol.createSimple({"color": "255,255,255,40", "outline_color": "97,97,97,255",
                                                  "outline_width": "0.4"})
            mbi_sym = QgsFillSymbol.createSimple({"color": COLOUR_JOINED_FILL, "outline_color": COLOUR_JOINED,
                                                  "outline_width": "0.8", "outline_style": "dash"})
            mbi_sym.appendSymbolLayer(QgsSimpleFillSymbolLayer.create(
                {"color": COLOUR_JOINED, "style": "b_diagonal", "outline_style": "no"}))
            layer.setRenderer(QgsCategorizedSymbolRenderer(JOIN_KIND_FIELD, [
                QgsRendererCategory(JOIN_KIND_BGY, bgy_sym, "Barangay"),
                QgsRendererCategory(JOIN_KIND_MBI, mbi_sym, "MBI joined to the barangay")]))

        def join_mbi(self):
            if self.running or not self.cases:
                return
            mbi = self.mbi_layer
            if mbi is None:
                QtWidgets.QMessageBox.warning(
                    self, DIALOG_TITLE, "The MBI layer is no longer in the project — load again.")
                return
            n = self._status_counts()
            msg = (f"Join every MBI to the barangay it is assigned to, in a new layer "
                   f"'{JOIN_LAYER_NAME}'?\n\n"
                   f"The ticked barangay layers are copied; each kept MBI part becomes its own feature "
                   f"with the covering barangay's attributes and {JOIN_KIND_FIELD} = '{JOIN_KIND_MBI}' "
                   f"({JOIN_FLAG_FIELD} = 1), shown hatched magenta. The barangay layers are not edited.")
            if n["pending"] or n["partial"]:
                msg += (f"\n\n{n['pending']:,} pending and {n['partial']:,} partly covered case(s): "
                        "their uncovered part is not joined.")
            if QtWidgets.QMessageBox.question(
                    self, DIALOG_TITLE, msg,
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel) != QtWidgets.QMessageBox.Yes:
                return
            self._join_mbi(mbi)

        def _join_mbi(self, mbi):
            self.set_busy(True)
            self.log("-" * 50)
            self.log("Joining MBIs to their covering barangays…")
            try:
                layer, s = self.build_joined_layer(mbi.crs())
                self._style_joined(layer)
                QgsProject.instance().addMapLayer(layer)
                self.log(f"  {s['mbi']:,} MBI part(s) from {s['cases']:,} case(s) joined "
                         f"({JOIN_KIND_FIELD} = '{JOIN_KIND_MBI}', {JOIN_FLAG_FIELD} = 1, hatched magenta)")
                self.log(f"  {s['bgy']:,} barangay feature(s) copied; {s['trimmed']:,} had a kept MBI part "
                         "cut out (released overlaps)")
                if s["absorbed"]:
                    self.log(f"  {s['absorbed']:,} barangay feature(s) wholly taken by an MBI part "
                             "(e.g. a disputed polygon) — left out")
                if s["missing"]:
                    self.log(f"  {s['missing']:,} MBI part(s) go to a barangay missing from the ticked "
                             "layers — only its geocode / name are filled")
                self.log(f"Added layer '{layer.name()}' ({layer.featureCount():,} feature(s)). Dissolve by "
                         "geocode to merge each MBI into its barangay; temporary layer — right-click > "
                         "Make Permanent to keep it.")
                self.progress.setValue(100)
                return layer
            except _Cancelled:
                self.log("Join cancelled.")
            except Exception as e:
                self.log(traceback.format_exc().rstrip())
                self.log(f"Join FAILED: {e}")
                QtWidgets.QMessageBox.warning(self, DIALOG_TITLE, f"Join failed: {e}")
            finally:
                self.set_busy(False)
                self.update_summary()
            return None

        def open_ea_selector(self):
            """Hand the MBI layer over to the MBI EA Selector (barangay mode): it lists every MBI
            under the covering barangay written here and offers only that barangay's EAs."""
            if self.running:
                return
            mbi = self.mbi_layer
            if mbi is None or not self.cases:
                QtWidgets.QMessageBox.warning(self, DIALOG_TITLE, "Load the MBI cases first.")
                return
            if self.coverage_field() != COVERAGE_FIELD:
                QtWidgets.QMessageBox.warning(
                    self, DIALOG_TITLE,
                    f"The MBI EA Selector reads the covering barangays from '{COVERAGE_FIELD}', but the "
                    f"Coverage field here is '{self.coverage_field()}'. Set it back to '{COVERAGE_FIELD}' "
                    "and Apply Coverage first.")
                return
            if self._needs_apply(mbi):
                if QtWidgets.QMessageBox.question(
                        self, DIALOG_TITLE,
                        "The choices in the viewer are not in the MBI layer yet.\n"
                        "Apply Coverage first, then open the MBI EA Selector?",
                        QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel) != QtWidgets.QMessageBox.Yes:
                    return
                if not self._write_coverage(mbi):
                    return
            n = self._status_counts()
            if not n["covered"] + n["partial"]:
                QtWidgets.QMessageBox.warning(
                    self, DIALOG_TITLE, "No MBI has a covering barangay yet — tick the covering "
                                        "barangays (or Auto-Assign) and Apply Coverage first.")
                return
            import runpy
            folder = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else \
                os.path.join(QgsApplication.qgisSettingsDirPath(), "python", "plugins",
                             "GMD_COProcessing", "gmd_processing_tools")
            path = os.path.join(folder, "mbi_ea_selector.py")
            if not os.path.exists(path):
                QtWidgets.QMessageBox.warning(self, DIALOG_TITLE, f"MBI EA Selector not found:\n{path}")
                return
            try:
                scope = runpy.run_path(path, init_globals={"iface": self.iface}, run_name="__main__")
                dialog = scope.get("_DIALOG") or scope["show_dialog"]()
                dialog.mbi_combo.setLayer(mbi)
                # Barangay layers that are really EA layers (they carry ea_geocode) are pre-ticked.
                ea_ids = {l.id() for l in self.bgy_layers() if _pick_field(l, ["ea_geocode"])}
                for item in dialog._ea_items():
                    if item.data(Qt.UserRole) in ea_ids:
                        item.setCheckState(Qt.Checked)
                if self.bldg_combo.currentLayer() is not None:
                    dialog.bldg_combo.setLayer(self.bldg_combo.currentLayer())
                dialog.validate_inputs()
                self.log(f"Opened the MBI EA Selector with '{mbi.name()}' — tick the EA layers of the "
                         "covering barangays there, then Load MBI Cases.")
            except Exception as e:
                self.log(traceback.format_exc().rstrip())
                QtWidgets.QMessageBox.warning(self, DIALOG_TITLE, f"Could not open the MBI EA Selector: {e}")

        def apply_coverage(self):
            layer = self.mbi_layer
            if self.running:
                return
            if layer is None:
                QtWidgets.QMessageBox.warning(
                    self, DIALOG_TITLE, "The MBI layer is no longer in the project — load again.")
                return
            n = self._status_counts()
            msg = (f"Write '{self.coverage_field()}' (covering barangay geocodes) "
                   f"for {len(self.cases) - n['gone']:,} case(s) into '{layer.name()}'?\n\n"
                   f"{n['covered'] + n['partial']:,} case(s) have a covering barangay, "
                   f"{n['pending']:,} are still pending (left empty).\n"
                   "No coverage status is written here — the MBI EA Selector writes it."
                   + (f"\n{n['gone']:,} no-longer-detected MBI(s) are left untouched." if n["gone"] else ""))
            if QtWidgets.QMessageBox.question(
                    self, DIALOG_TITLE, msg,
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel) != QtWidgets.QMessageBox.Yes:
                return
            self._write_coverage(layer)

        def _write_coverage(self, layer):
            """Write the coverage field (covering barangay geocodes, on every row of the case) for
            every loaded case (unsaved, undoable edits). No coverage status: the MBI EA Selector
            writes it. Returns True on success."""
            field = self.coverage_field()
            self.set_busy(True)
            self.log("-" * 50)
            self.log(f"Applying coverage to '{layer.name()}'…")
            try:
                if not layer.isEditable() and not layer.startEditing():
                    raise RuntimeError(f"Cannot edit '{layer.name()}'")
                if layer.fields().indexOf(field) < 0:
                    layer.addAttribute(QgsField(field, QVariant.String))
                    self.log(f"  Added field '{field}'")
                layer.updateFields()
                f_idx = layer.fields().indexOf(field)

                cases = [c for c in self.cases.values() if not c.gone]   # nothing to cover there
                layer.beginEditCommand(f"MBI barangay coverage for {len(cases)} case(s)")
                rows = 0
                for i, case in enumerate(cases):
                    for fid in case.feature_ids:
                        layer.changeAttributeValues(fid, {f_idx: ",".join(case.assigned) or None})
                        rows += 1
                    if i % 200 == 0:
                        self.progress.setValue(int(100 * i / max(len(cases), 1)))
                        QtWidgets.QApplication.processEvents()
                layer.endEditCommand()
                for case in cases:
                    case.saved = list(case.assigned)
                layer.triggerRepaint()
                n = self._status_counts()
                self.log(f"  Updated '{field}' on {rows:,} row(s) across {len(cases):,} case(s): "
                         f"{n['covered'] + n['partial']:,} with a covering barangay, "
                         f"{n['pending']:,} still pending")
                self.log(f"  Changes are UNSAVED edits in '{layer.name()}'. "
                         "Save: Toggle Editing > Save Edits. Undo: Edit > Undo.")
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
                              "with no covering barangay.")
            if n["partial"]:
                open_note += (f"\n{n['partial']:,} shared case(s) are only partly covered by their "
                              "ticked barangays.")
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
            base = self.output_name()
            gpkg = os.path.join(folder, f"{base}_{stamp}.gpkg")
            csv_path = os.path.join(folder, f"{base}_{stamp}.csv")
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

        def output_name(self):
            """'ppp_mbi' from the province codes of the ticked barangay layers (else of the loaded
            cases' barangays)."""
            bgys = self.bgy_layers()
            provinces = read_bgy_info(bgys)[1] if bgys else []
            if not provinces:
                provinces = sorted({code[:3] for case in self.cases.values()
                                    for code in case.candidates if len(code) >= 3})
            return mbi_output_name(provinces)

        def export_gpkg(self, layer, path):
            """The whole MBI layer, including unsaved edits and the coverage fields."""
            options = QgsVectorFileWriter.SaveVectorOptions()
            options.driverName = "GPKG"
            options.layerName = self.output_name()
            options.fileEncoding = "UTF-8"
            context = QgsProject.instance().transformContext()
            res = QgsVectorFileWriter.writeAsVectorFormatV3(layer, path, context, options)
            if res[0] != QgsVectorFileWriter.NoError:
                raise RuntimeError(f"GPKG export failed: {res[1]}")
            self.log(f"  GPKG written: {path} ({layer.featureCount():,} row(s))")

            if not self.bgy_layers() or not any(self._kept_parts(c) for c in self.cases.values()):
                return
            joined, _s = self.build_joined_layer(layer.crs())
            options = QgsVectorFileWriter.SaveVectorOptions()
            options.driverName = "GPKG"
            options.layerName = JOIN_EXPORT_NAME
            options.fileEncoding = "UTF-8"
            options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteLayer
            res = QgsVectorFileWriter.writeAsVectorFormatV3(joined, path, context, options)
            if res[0] != QgsVectorFileWriter.NoError:
                raise RuntimeError(f"GPKG export of the joined barangays failed: {res[1]}")
            self.log(f"    + layer '{JOIN_EXPORT_NAME}' ({_s['mbi']:,} joined MBI part(s), "
                     f"{_s['bgy']:,} barangay feature(s))")

        def export_csv(self, path):
            field = self.coverage_field()

            def m2(value):
                return "" if value is None else round(value, 2)

            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["case_uuid", DETECT_FIELD, REF_UUID_FIELD, REF_STATUS_FIELD,
                            "mbi_type", "mbi_level", "mbi_area_m2", "num_bldg_pts",
                            "bgy_geocode", "barangay", "city_mun", "province", "ea_names",
                            "bgy_bldg_in_mbi", "bgy_part_m2", "bgy_kept_m2", "bgy_kept_bldg",
                            "chosen", field])
                for case in sorted(self.cases.values(), key=lambda c: (c.level, c.case_uuid)):
                    for cand in case.candidates.values():
                        chosen = cand.code in case.assigned
                        w.writerow([case.case_uuid, case.detection, case.ref_uuids, case.ref_status,
                                    case.mbi_type, case.level, m2(case.area),
                                    case.bldg_pts, cand.code, cand.name, cand.city_mun,
                                    cand.province, ", ".join(cand.eas),
                                    "" if cand.bldg is None else cand.bldg,
                                    m2(cand.part_area) if case.split else "",
                                    m2(cand.kept_area) if chosen else "",
                                    "" if not chosen or cand.kept_bldg is None else cand.kept_bldg,
                                    "Yes" if chosen else "", ",".join(case.assigned)])
            self.log(f"  CSV written: {path}")

    return CoverageDialog


# ─────────────────── entry point ───────────────────

def show_dialog():
    """Open the MBI Barangay Coverage window (non-modal, so the map stays usable)."""
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


if "qgis" in sys.modules and not os.environ.get("MBI_BGY_SELECTOR_NO_DIALOG"):
    _DIALOG = show_dialog()
elif "qgis" not in sys.modules:
    print("Run this script inside QGIS: GMD CO Processing > EA Tools > MBI Barangay Selector.")
