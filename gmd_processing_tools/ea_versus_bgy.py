# =====================================================================================
# EA vs Barangay Check (GMD CO Processing > EA Tools)
# Timestamp : 2026-11-24 20:00 (Asia/Manila, UTC+08:00)
# Version   : 1.7.1
#
# Changelog (oldest to newest)
#   1.0.0  Baseline as supplied: EA vs BGY / BGY vs BGY / EA vs EA checks, snapping of selected
#          discrepancies, Undo Last Snap, Duplicate Geometry Fixer tab, Help tab.
#   1.1.0  2026-11-23  Fix: snapping one EA no longer creates new overlaps / gaps with its neighbours.
#          - snapped_geometry(): cut / added pieces are snapped onto the EA before the overlay, and the
#            0.1 mm / 1 mm / 1 cm grid fallbacks no longer move the EA's untouched vertices
#            (new restore_vertices()). Before, a grid fallback rounded every vertex of the EA, including
#            the edges shared with its neighbours, so an EA vs EA scan after a snap found new
#            overlaps / gaps along edges that were never edited.
#          - to_layer_geometry(): the feature's own vertices keep their exact layer-CRS coordinates
#            (new layer_xy()); only the new vertices at the cut are converted lat/lon -> layer CRS, so a
#            datum round trip (e.g. PRS92 <-> WGS84) cannot nudge shared edges either.
#          - to_layer_geometry(): the repair no longer snaps the whole feature to a grid; if the new
#            shape cannot be made valid the row is marked Failed and the feature is left untouched.
#   1.2.0  2026-11-24  Fix: snapped discrepancies no longer come back on the next scan.
#          - snapped_geometry(): a result is accepted only if it really removes the cut pieces and covers the
#            added ones (new fixes_pieces(): leftovers together under a quarter of the minimum area). Before,
#            when several gaps went to one EA, the grid fallback's restored vertices fell back onto the old
#            outline and undid the fix, yet the row was still marked Snapped.
#          - EA vs EA / BGY vs BGY without a reference (new snap_inputs()): an overlap is cut with the kept
#            feature's own outline (also taking the hairline overlaps beside it), and added gaps are clipped
#            by the bordering features, so a snap cannot leave or create a discrepancy next to the one fixed.
#            Only the fixed edge changes: the feature's other vertices stay exactly where they were.
#          - Zoom highlight: the circles mark the Fix feature's out-of-place vertices (new
#            unsnapped_vertices()) instead of one point somewhere inside the piece.
#          - Detection with a reference (EA vs BGY, BGY vs BGY / EA vs EA with a reference): pieces found on
#            the 0.1 mm grid are re-checked on the exact geometry (new exact_rest()). The grid could report
#            land that is covered, or even make up a region on a long thin feature, which no snap could fix.
#          - snapped_geometry(): also tries the pieces without the 0.2 mm nudge (it flattened very thin
#            overlaps, so they failed with "unchanged"), and drops lines a grid collapses a hairline into
#            (they failed with "Overlay input is mixed-dimension"). Added gaps are trimmed by the neighbours,
#            not the whole feature, so an overlap the neighbour cuts in the same snap is not lost.
#   1.3.0  2026-11-24  EA vs EA -> EA vs BGY -> EA vs EA now ends clean, and snapped edges share vertices.
#          - Snap to a reference (new in snap_inputs()): around each piece the feature is cut to its own
#            reference outline and given all reference land no other feature rightly holds, so the hairline
#            slivers under min_area beside it are fixed too (before, they merged into new discrepancies,
#            e.g. needle overlaps in EA vs EA after an EA vs BGY snap).
#          - Detection with a reference uses the exact geometry (new exact_difference()): the 0.1 mm grid
#            split thin slivers into fragments under min_area, so EA vs BGY missed what EA vs EA then found.
#          - explode_pieces(): pieces of one row that touch (within ~0.1 mm) are one discrepancy.
#          - New only_pieces_changed(): a snap may change land only inside the selected pieces.
#          - New share_vertices(): after a snap, a vertex of the snapped feature lying on a neighbour's edge
#            (within 1 micron) is put into that neighbour too, and the other way round, so both have the same
#            vertices along the shared edge. Shapes do not change; Undo Last Snap takes it back with the snap.
#   1.4.0  2026-11-24  Snapped edges lie exactly on the barangay / neighbour line as the map draws them.
#          - Checks and overlays run in a projected working CRS in metres (new choose_work_crs()): the
#            layers' own projected CRS, else the map's, else the WGS 84 / UTM zone of the data; it must
#            convert to and from the layers' CRS exactly (a PRS92 <-> WGS 84 datum shift comes back ~1 mm
#            off). Before, they ran in lat/lon: a vertex put on a km-long edge was exact in lat/lon but the
#            map draws edges straight in its projected CRS, so the snapped edge showed up to ~10 cm off the
#            barangay line (e.g. 347 sqm along a 5 km edge) while every scan reported it clean.
#          - Tolerances are in metres (GRID_M, SNAP_TOL_M, KEEP_VERTEX_TOL_M, NEIGHBOUR_PAD_M, MIN_ZOOM_M).
#          - share_vertices() tests "on the edge" in the working CRS and writes each shared vertex with its
#            owner's exact layer coordinate.
#   1.5.0  2026-11-24  Vertices along shared edges are paired (no vertex without its partner on the neighbour).
#          - share_vertices() pairs within 1 cm (SHARE_TOL_M), not 1 micron: the data had ~980 vertices a
#            hair (0.01-10 mm) off the neighbour's edge, drawn as one line but each with its own vertices.
#            Such a vertex is moved onto the neighbour's edge (onto its vertex if one is that close) and put
#            into the neighbour; a vertex shared with a third feature moves there too. Checked against a
#            reference, a vertex on the feature's own reference line is never moved.
#          - New Match Vertices button (match_vertices()): does this for the whole checked layer; Undo Last
#            Snap takes it back. The snap buttons stay enabled after a clean scan.
#   1.5.1  2026-11-24  No duplicate vertices: Match Vertices / snaps left vertices a hair apart in a row,
#            flagged by QGIS as duplicate nodes. New weld (in share_vertices): vertices within 2 mm
#            (DUPLICATE_M) in any of the features involved become one point in every feature that has them
#            (one on a reference line wins), then exact duplicates and A-B-A spikes are dropped
#            (clean_vertices). Runs before and after pairing, so neighbour pairs stay paired.
#   1.6.0  2026-11-24  Vertex check in every scan: new issue "Unmatched vertices" (find_vertex_issues()), one
#            row per feature with a vertex within 1 cm of a neighbour's edge but no partner vertex there, or
#            two vertices within 2 mm. Shown as small squares; the zoom circles sit on the vertices. Snap
#            Selected on these rows matches them (share_vertices) in the same edit command as the other
#            snaps, then re-checks the feature: Snapped only if nothing is left, else Failed with the reason.
#   1.7.0  2026-11-24  EA vs BGY no longer says "all snapped" while an EA edge sits mm off the barangay line.
#          - Vertex check against the reference (ref_off_vertices / ref_missing_vertices): an EA vertex up to
#            5 cm (REF_TOL_M) off its own barangay line, or a barangay vertex missing from an EA edge running
#            along that line. Such slivers are under 0.1 sqm, so the area check never listed them.
#          - Snapping puts those vertices onto the line (onto the barangay vertex if one is within 2 mm) and
#            adds the missing barangay vertices (insert_ref_vertices), for the snapped EA and its neighbours.
#          - The weld keeps a barangay vertex where it is; a vertex on its own barangay line may slide along it
#            onto the neighbour's vertex (never a barangay corner).
#          - KEEP_VERTEX_TOL_M 1e-5 -> 1e-7: a move of a few microns was undone when written back.
#          - Where barangays do not meet at one point (corners mm apart), the EA vertex on another barangay's
#            line is left as is: no position fits all of them.
#   1.7.1  2026-11-24  Repeated Snap Selected no longer piles vertices at intersections or makes bow ties / lines.
#          - snap_inputs(): the hairline slivers beside a piece are taken whole, not clipped to a 1 cm pad
#            around it: the pad's outline put new vertices mm-cm from each real crossing, and every snap added
#            more (199 vertex pairs under 5 mm after a few rounds; now 0).
#          - share_vertices(): a vertex on its own reference line within 5 cm (SLIDE_M) of a reference vertex
#            slides onto it, so an intersection is one vertex.
#          - to_layer_geometry(): a QGIS repair (makeValid) that splits the feature into parts touching at a
#            point (a bow tie) or leaves specks is never used. If the shape crosses itself only in a lat/lon
#            layer (km-long edges bend ~10 cm between the CRSs), its long edges get a vertex every 100 m
#            (DENSIFY_M) instead, and the scan copy keeps exactly what was written.
#          - clean_vertices(): needles under 1 degree enclosing under 0.01 sqm (an overlay's leftover) are removed.
# =====================================================================================
r"""
EA vs Barangay boundary check (QGIS), with three check modes chosen in the window:

  EA vs BGY    each EA is compared to its barangay; the barangay is fixed, the EA is snapped to it.
  BGY vs BGY   with a reference barangay layer: each barangay is snapped to the reference barangay with
               the same geocode. Without one: the barangays are checked against each other.
  EA vs EA     with a reference EA layer: each EA is snapped to the reference EA with the same ea_geocode.
               Without one: the EAs are checked against each other.

The output is only the discrepancies: each feature is just the mismatched area, carrying the attributes of
the feature that must be fixed.

  Outside ...      the part of a feature outside its reference (cut off by a snap)
  Short of ...     reference land the feature does not reach (added by a snap; all bordering features listed)
  No matching ...  the feature's geocode is not in the reference layer (the whole feature; fix by hand)
  Overlap          self check: land in two features (cut from the larger one; Switch Fix: the other)
  Gap              self check: land enclosed by the features but in none (added to the feature with the
                   longest border along it; Switch Fix: the next bordering feature)

The window's list shows them, largest first. No layer is added to the project: the discrepancies are kept in
memory by the window, for zooming and snapping. Reference features with no match are named in the log.

Snapping is done per discrepancy: select rows in the list and press "Snap Selected". Only the feature named
in the "Fix" column is changed, and only in the checked layers (the reference is never edited):

  - Outside / Overlap pieces are cut off the feature.
  - Short / Gap pieces are added to the feature sharing the longest border with them.
  - Holes and parts smaller than the sliver setting that the edit leaves behind are removed.

Before a feature is changed, the new geometry must be valid, not empty, have the expected area, still be valid
in the layer's CRS, fit the layer (single-part layers cannot take several parts), and the feature must not
have been edited since the scan. Otherwise the row is marked Failed with the reason and it is untouched.

Several layers can be ticked on each side (e.g. every city / municipality of a province). The ticked layers are
scanned together, so discrepancies between two layers are found too, and each snap edits the feature's own layer.

The changed layers are left in edit mode and nothing is written to their files until you save:

  Keep    Save Layer Edits, or Layer > Current Edits > Save for All Layers
  Revert  "Undo Last Snap" (press again for the snap before), Edit > Undo, or Toggle Editing > Discard.
          Undo Last Snap is refused if the layers were edited or Edit > Undo was used after the snap, and
          is no longer possible once their edits are saved or discarded.

Clicking a row flashes it on the map; double-click (or Zoom to Selected) zooms to it and, with the "Blinking dotted
outline and snap preview on zoom" box ticked, blinks a dotted outline on the discrepancy, with circles on the Fix
feature's vertices that are out of place (orange cut, red add, grey fix by hand) and a dotted green outline of the Fix feature as it will be after Snap Selected.
Unticked, only the plain flash is shown.

Only the geometry is changed: attributes such as household counts stay as they are.
Each scan also removes any "Discrepancies ..." scratch layers left in the project by older versions.

Usage:
  1. Load the layers into the project. To check only part of a layer, set a filter on it (right-click > Filter).
  2. GMD CO Processing > EA Tools > EA vs Barangay Check. This opens the "EA vs Barangay Check" window
     with tabs:
     - EA vs Barangay Check: boundary comparisons and snapping (slivers < 0.1 sqm automatically ignored).
     - Duplicate Geometry Fixer: scan and fix duplicate geometry parts inside features or duplicate features.
     - Help and Information: quick reference and documentation.
  3. Choose the Check Mode, tick the layers to check and (optionally, except for EA vs BGY) the reference
     layers, or press Select All, then press Scan. Progress, warnings and the summary appear in the Log.
  4. The Discrepancies list shows every issue: click a row to flash it on the map, double-click to zoom to it.
     Select the rows to fix (Switch Fix to change which feature takes them) and press Snap Selected.
"""
# Menu / panel entry (read by the GMD CO Processing plugin)
TOOL_NAME = "EA vs Barangay Check"
TOOL_GROUP = "EA Tools"
TOOL_ICON = "icons/ea_versus_bgy.svg"

import os
import re
import shutil
import sys
import tempfile
import traceback
import warnings
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
import shapely
from shapely.geometry import MultiPolygon

warnings.filterwarnings("ignore")

# ================= CONFIG =================
MIN_AREA_SQM = 0.1  # fixed: differences smaller than this are slivers and ignored (0.1 sqm)
TOUCH_TOL_M = 1.0   # a gap within this distance of an EA counts as bordering that EA
MIN_OWNER_SHARE = 0.3  # a gap is added to one EA only if that EA has at least this share of its EA border
GRID_M = 1e-4      # fallback precision grid (0.1 mm) when an exact snap gives an invalid geometry
SNAP_TOL_M = 2e-4  # cut / added pieces are snapped onto the EA within this distance (0.2 mm) before the overlay
KEEP_VERTEX_TOL_M = 1e-7  # a vertex this close (0.1 micron) to one of the feature's own keeps that exact position
SHARE_TOL_M = 1e-2  # a vertex this close (1 cm) to a neighbour's edge is put into that edge too (shared)
SAME_VERTEX_M = 1e-6  # two vertices this close (1 micron) are the same vertex

# Layer-name patterns used, after the fields, to autodetect the layers in the panel.
BGY_LAYER_HINT = r"bgy|barangay"
EA_LAYER_HINT = r"(^|_)ea"

# Checks and overlays run in a projected CRS in metres (the working CRS, picked by each scan): the map
# canvas's CRS when it is projected, else the UTM zone of the data. An edge is straight in that CRS just
# as the map draws it. In lat/lon a long edge (km) is a curve on the map: a vertex put on it in lat/lon
# shows up to ~10 cm off the barangay line it should follow. The features' own vertices are written back
# untouched (layer_xy). Areas use an equal-area projection in metres centred on the Philippines.
DATA_CRS = "EPSG:32651"  # replaced by choose_work_crs() at each scan
AREA_CRS = "+proj=laea +lat_0=12 +lon_0=122 +datum=WGS84 +units=m +no_defs"


def choose_work_crs(layers):
    """The working CRS (authid) for a scan of `layers`, the first that suits of: the layers' own CRS (when
    projected in metres), the map's CRS, the WGS 84 / UTM zone at the centre of the layers.

    It must be projected in metres and convert to and from every layer's CRS exactly (a datum shift, e.g.
    WGS 84 <-> PRS92, comes back ~1 mm off, which would move the new vertices off the edges they meet)."""
    from qgis.core import (Qgis, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsPointXY,
                           QgsProject)

    project = QgsProject.instance()
    wgs = QgsCoordinateReferenceSystem("EPSG:4326")
    srcs = [l.crs() if l.crs().isValid() else wgs for l in layers]

    def metres(crs):
        try:
            return crs.mapUnits() == Qgis.DistanceUnit.Meters
        except AttributeError:  # older QGIS
            from qgis.core import QgsUnitTypes
            return crs.mapUnits() == QgsUnitTypes.DistanceMeters

    def exact(crs):
        for layer, src in zip(layers, srcs):
            if src == crs:
                continue
            there = QgsCoordinateTransform(src, crs, project)
            back = QgsCoordinateTransform(crs, src, project)
            box = layer.extent()
            for fx, fy in ((0.5, 0.5), (0.1, 0.1), (0.9, 0.9), (0.1, 0.9), (0.9, 0.1)):
                try:
                    p = QgsPointXY(box.xMinimum() + fx * box.width(), box.yMinimum() + fy * box.height())
                    q = there.transform(p)
                    q2 = there.transform(back.transform(q))
                except Exception:
                    return False
                if abs(q2.x() - q.x()) > 1e-6 or abs(q2.y() - q.y()) > 1e-6:
                    return False
        return True

    for crs in [*srcs, project.crs()]:
        if (crs.isValid() and not crs.isGeographic() and metres(crs) and crs.authid().upper().startswith("EPSG:")
                and exact(crs)):
            return crs.authid()
    extent = None
    for layer, src in zip(layers, srcs):
        box = layer.extent()
        if src != wgs:
            box = QgsCoordinateTransform(src, wgs, project).transformBoundingBox(box)
        if extent is None:
            extent = box
        else:
            extent.combineExtentWith(box)
    lon, lat = (extent.center().x(), extent.center().y()) if extent is not None else (122.0, 12.0)
    zone = min(60, max(1, int((lon + 180) // 6) + 1))
    return f"EPSG:{(32600 if lat >= 0 else 32700) + zone}"


def use_crs(authid):
    """Make `authid` the working CRS (DATA_CRS)."""
    global DATA_CRS
    DATA_CRS = authid

# Column names to look for, in order of preference (case-insensitive).
BGY_FIELDS = {
    "geocode": ["geocode", "bgy_geocode"],
    "region": ["region"],
    "province": ["province"],
    "city_mun": ["city_mun", "City/Municipality", "citymunicipality"],
    "barangay": ["barangay"],
}
EA_FIELDS = {
    "bgy_geocode": ["bgy_geocode", "BGY_GEO"],
    "ea_geocode": ["ea_geocode", "geocode"],
    "name": ["name", "ean"],
    "region": ["region"],
    "province": ["province"],
    "city_mun": ["city_mun", "City/Municipality", "citymunicipality"],
    "barangay": ["barangay"],
}
ADMIN = ["Region", "Province", "City/Municipality", "Barangay"]

WARNINGS = []


def warn(message):
    if message not in WARNINGS:
        WARNINGS.append(message)
        print(f"   [WARNING] {message}")


# ================= HELPERS =================
def bgy_key(value):
    """Barangay key: first 8 digits of the 14-digit geocode (province 3 + city/mun 2 + barangay 3)."""
    if value is None or pd.isna(value):
        return None
    s = re.sub(r"\D", "", re.sub(r"\.0$", "", str(value).strip()))
    if not s:
        return None
    s = s.zfill(8) if len(s) <= 8 else s.zfill(14)
    return s[:8]


def full_geocode(value):
    if value is None or pd.isna(value):
        return ""
    s = re.sub(r"\D", "", re.sub(r"\.0$", "", str(value).strip()))
    if not s:
        return ""
    return s.zfill(8).ljust(14, "0") if len(s) <= 8 else s.zfill(14)  # 8-digit barangay code -> 14 digits


def pick(df, candidates):
    """First matching column (case-insensitive) as a Series; all-None if none match."""
    cols = {c.lower(): c for c in df.columns}
    for c in candidates:
        if c.lower() in cols:
            return df[cols[c.lower()]]
    return pd.Series([None] * len(df), index=df.index, dtype=object)


def area_sqm(gdf):
    return gdf.geometry.to_crs(AREA_CRS).area


def read_layer(path, layer):
    try:
        return gpd.read_file(path, layer=layer)
    except Exception as e:
        warn(f"{layer}: {e} - retrying feature by feature")

    # Slow path: read attributes and raw WKB separately; geometries that can't be built become None.
    attrs = pyogrio.read_dataframe(path, layer=layer, read_geometry=False)
    meta, _, wkb, _ = pyogrio.raw.read(path, layer=layer, columns=[])
    geoms = [None] * len(wkb)
    bad = 0
    for i, w in enumerate(wkb):
        try:
            geoms[i] = shapely.from_wkb(w) if w is not None else None
        except Exception:
            bad += 1
    if bad:
        warn(f"{layer}: skipped {bad} unreadable geometr(ies)")
    return gpd.GeoDataFrame(attrs, geometry=geoms, crs=meta["crs"])


def polygons_only(geom):
    """Keep only polygon parts (make_valid can leave lines/points behind)."""
    if geom is None or geom.is_empty:
        return None
    if geom.geom_type in ("Polygon", "MultiPolygon"):
        return geom
    polys = []
    for part in shapely.get_parts(geom):
        if part.geom_type == "Polygon":
            polys.append(part)
        elif part.geom_type == "MultiPolygon":
            polys.extend(part.geoms)
    return MultiPolygon(polys) if polys else None


def clean_geometries(gdf, source):
    gdf = gdf.to_crs(DATA_CRS)
    geom = gpd.GeoSeries(shapely.force_2d(gdf.geometry.values), index=gdf.index, crs=gdf.crs)
    invalid = geom.notna() & ~geom.is_valid
    if invalid.any():
        try:
            geom[invalid] = geom[invalid].make_valid(method="structure", keep_collapsed=False)
        except TypeError:  # older geopandas (e.g. QGIS 3.40's 1.0) has no method option
            geom[invalid] = geom[invalid].make_valid()
    geom = geom.apply(polygons_only)

    dropped = geom.isna()
    if dropped.any():
        warn(f"{source}: skipped {int(dropped.sum())} feature(s) with empty or non-polygon geometry")
    gdf = gdf.set_geometry(geom)
    return gdf[~dropped]


def ea_key(value):
    """EA key: the full 14-digit EA geocode (None if missing)."""
    return full_geocode(value) or None


# Reference layers (fixed): key, Geocode, Label (name used in notes) and the admin names.
def prepare_bgy(gdf):
    f = lambda k: pick(gdf, BGY_FIELDS[k])
    return gpd.GeoDataFrame({
        "key": f("geocode").map(bgy_key),
        "Geocode": f("geocode").map(full_geocode),
        "Label": f("barangay"),
        "Region": f("region"),
        "Province": f("province"),
        "City/Municipality": f("city_mun"),
        "Barangay": f("barangay"),
    }, geometry=gdf.geometry, crs=gdf.crs)


def prepare_ea_ref(gdf):
    f = lambda k: pick(gdf, EA_FIELDS[k])
    code = f("ea_geocode")
    return gpd.GeoDataFrame({
        "key": code.map(ea_key),
        "Geocode": code.map(full_geocode),
        "Label": f("name").where(f("name").notna(), code.map(full_geocode)),
        "Region": f("region"),
        "Province": f("province"),
        "City/Municipality": f("city_mun"),
        "Barangay": f("barangay"),
    }, geometry=gdf.geometry, crs=gdf.crs)


# Target layers (snapped): key (what they are matched on), source ids, code / name and admin names.
def prepare_ea(gdf, key="bgy"):
    """key='bgy': matched to barangays (EA vs BGY); key='ea': matched by EA geocode (EA vs EA)."""
    f = lambda k: pick(gdf, EA_FIELDS[k])
    ea_code = f("ea_geocode")
    keys = (f("bgy_geocode").map(bgy_key).fillna(ea_code.map(bgy_key)) if key == "bgy"
            else ea_code.map(ea_key))
    return gpd.GeoDataFrame({
        "key": keys,
        "src_fid": gdf["src_fid"],
        "src_hash": gdf["src_hash"],
        "EA Geocode": ea_code.map(full_geocode),
        "EA": f("name"),
        "ea_Region": f("region"),
        "ea_Province": f("province"),
        "ea_City/Municipality": f("city_mun"),
        "ea_Barangay": f("barangay"),
    }, geometry=gdf.geometry, crs=gdf.crs)


def prepare_bgy_target(gdf):
    """A barangay layer to be snapped, in the same columns as an EA target."""
    f = lambda k: pick(gdf, BGY_FIELDS[k])
    return gpd.GeoDataFrame({
        "key": f("geocode").map(bgy_key),
        "src_fid": gdf["src_fid"],
        "src_hash": gdf["src_hash"],
        "EA Geocode": f("geocode").map(full_geocode),
        "EA": f("barangay"),
        "ea_Region": f("region"),
        "ea_Province": f("province"),
        "ea_City/Municipality": f("city_mun"),
        "ea_Barangay": f("barangay"),
    }, geometry=gdf.geometry, crs=gdf.crs)


# ================= CHECK MODES =================
# kind: which fields the layer must have ('ea' or 'bgy'). The reference is fixed; the target is snapped.
MODES = {
    "ea_bgy": {"label": "EA vs BGY", "target": "ea", "ref": "bgy", "ref_required": True,
               "unit": "EA", "ref_unit": "barangay",
               "issues": ("Outside BGY", "Short of BGY", "No matching barangay")},
    "bgy_bgy": {"label": "BGY vs BGY", "target": "bgy", "ref": "bgy", "ref_required": False,
                "unit": "barangay", "ref_unit": "reference barangay",
                "issues": ("Outside ref BGY", "Short of ref BGY", "No matching ref BGY")},
    "ea_ea": {"label": "EA vs EA", "target": "ea", "ref": "ea", "ref_required": False,
              "unit": "EA", "ref_unit": "reference EA",
              "issues": ("Outside ref EA", "Short of ref EA", "No matching ref EA")},
}
SELF_ISSUES = ("Overlap", "Gap")
# Map colours by what a snap does: cut (orange), add (red), nothing (grey); overlaps purple.
KIND_COLOURS = {"cut": "255,127,0,210", "add": "227,26,28,210", "none": "158,158,158,210",
                "overlap": "142,68,173,210", "vertex": "0,172,193,230"}
VERTEX_ISSUE = "Unmatched vertices"  # every mode: vertices along shared edges without their partner


def mode_issues(mode, self_check):
    """[(issue label, rgba)] for a mode: the legend, filter and map style of its scan."""
    vertex = (VERTEX_ISSUE, KIND_COLOURS["vertex"])
    if self_check:
        return [(SELF_ISSUES[0], KIND_COLOURS["overlap"]), (SELF_ISSUES[1], KIND_COLOURS["add"]), vertex]
    out, short, orphan = MODES[mode]["issues"]
    return [(out, KIND_COLOURS["cut"]), (short, KIND_COLOURS["add"]), (orphan, KIND_COLOURS["none"]), vertex]


def geometry_hash(geom):
    """Fingerprint of a QGIS geometry, to tell whether a feature changed since the scan."""
    return hash(bytes(geom.asWkb())) if geom is not None and not geom.isNull() else 0


def layer_to_gdf(layer):
    """Copy a QGIS layer (any provider, converted to the working CRS) to a temp GeoPackage and read it.

    Column src_fid holds each row's feature id in the QGIS layer, src_hash a fingerprint of its geometry.
    """
    from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsFeatureRequest, QgsProject,
                           QgsVectorFileWriter)

    tmp_dir = tempfile.mkdtemp(prefix="ea_qa_")
    tmp = os.path.join(tmp_dir, "layer.gpkg")
    opts = QgsVectorFileWriter.SaveVectorOptions()
    opts.driverName = "GPKG"
    opts.layerName = "data"
    # A source "fid" column clashes with the GeoPackage key (e.g. merged layers), so leave it out.
    opts.attributes = [i for i, fld in enumerate(layer.fields()) if fld.name().lower() != "fid"]
    src = layer.crs()
    if not src.isValid():
        warn(f"{layer.name()}: layer has no CRS, assuming EPSG:4326")
        src = QgsCoordinateReferenceSystem("EPSG:4326")
    opts.ct = QgsCoordinateTransform(src, QgsCoordinateReferenceSystem(DATA_CRS), QgsProject.instance())
    err = QgsVectorFileWriter.writeAsVectorFormatV3(layer, tmp, QgsProject.instance().transformContext(), opts)
    try:
        if err[0] != QgsVectorFileWriter.NoError:
            raise RuntimeError(f"Could not read layer '{layer.name()}': {err[1]}")
        gdf = read_layer(tmp, "data")
        feats = [(f.id(), geometry_hash(f.geometry()))
                 for f in layer.getFeatures(QgsFeatureRequest().setNoAttributes())]
        if len(feats) != len(gdf):
            raise RuntimeError(f"Layer '{layer.name()}': read {len(gdf):,} of {len(feats):,} features")
        gdf["src_fid"] = [fid for fid, _ in feats]
        gdf["src_hash"] = [h for _, h in feats]  # to detect edits made after the scan
        return gdf.set_crs(DATA_CRS, allow_override=True)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def load_layer(layer, prepare, geocode_fields):
    name = layer.name()
    gdf = layer_to_gdf(layer)
    if gdf.empty:
        raise RuntimeError(f"Layer '{name}' has no features")
    out = prepare(clean_geometries(gdf, name))

    missing = out["key"].isna()
    if missing.all():
        raise RuntimeError(f"No usable geocodes in '{name}'. Expected a field named one of: {', '.join(geocode_fields)}")
    if missing.any():
        warn(f"{name}: skipped {int(missing.sum())} feature(s) without a usable geocode")
    return gpd.GeoDataFrame(out[~missing].reset_index(drop=True), geometry="geometry", crs=DATA_CRS)


def merge_duplicate_barangays(bgy, what="barangay"):
    """One feature per reference unit: features sharing a geocode are merged."""
    multi = bgy["key"].duplicated(keep=False)
    if multi.any():
        warn(f"{bgy.loc[multi, 'key'].nunique():,} {what} geocode(s) appear more than once and were merged")
        merged = bgy[multi].dissolve(by="key", aggfunc="first").reset_index()
        bgy = pd.concat([bgy[~multi], merged], ignore_index=True)
    return gpd.GeoDataFrame(bgy, geometry="geometry", crs=DATA_CRS).reset_index(drop=True)


def touching_groups(polys):
    """Group polygons that touch (even at a single point, or within ~0.1 mm: the crossing point of two
    edges is computed, so neighbouring lenses can miss each other by a hair): a list of index lists."""
    parent = list(range(len(polys)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    if len(polys) > 1:
        a, b = shapely.STRtree(polys).query(polys, predicate="dwithin", distance=GRID_M)
        for i, j in zip(a, b):
            if i < j:
                parent[root(i)] = root(j)
    groups = {}
    for i in range(len(polys)):
        groups.setdefault(root(i), []).append(i)
    return list(groups.values())


def explode_pieces(gdf, min_area):
    """Split into pieces and drop slivers below min_area.

    Polygons of one row that touch, even at a single point, stay together as one (multi-polygon) piece: a
    hairline strip where two edges cross back and forth is a chain of tiny lenses meeting at points. Split
    up, each lens is under min_area and ignored, yet once a neighbouring piece is snapped they join into
    one strip that the next scan does find. Kept together, the strip is found, and fixed, at once."""
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]
    gdf = gdf.explode(index_parts=False)
    gdf = gdf[gdf.geom_type == "Polygon"]
    if not len(gdf):
        return gdf.assign(piece_area=[]).reset_index(drop=True)
    rows, geoms = [], []
    for _, grp in gdf.groupby(level=0, sort=False):
        polys = list(grp.geometry.values)
        for members in touching_groups(polys):
            rows.append(grp.iloc[members[0]])
            geoms.append(polys[members[0]] if len(members) == 1 else MultiPolygon([polys[i] for i in members]))
    out = gpd.GeoDataFrame(pd.DataFrame(rows).drop(columns=gdf.geometry.name).reset_index(drop=True),
                           geometry=geoms, crs=gdf.crs)
    out["piece_area"] = area_sqm(out)
    return out[out["piece_area"] >= min_area].reset_index(drop=True)


# ================= COMPARE =================
def exact_difference(a, b):
    """a - b on the exact geometry (the EA vs EA check measures the same way, so both checks agree).

    A precision grid is not used: on a long thin sliver it splits the piece into fragments under the
    minimum area, so the sliver is missed here but found by EA vs EA, and on a thin feature it can even
    make up a region that no snap could remove. Only if the exact overlay fails is the grid used."""
    try:
        return shapely.difference(a, b)
    except shapely.errors.GEOSException:
        return shapely.difference(a, b, grid_size=GRID_M)


def uncovered_land(inside, bgy, min_area):
    """The parts of each barangay that are not covered by its EAs."""
    scope = bgy[bgy["key"].isin(inside["key"])]
    cover = inside[["key", "geometry"]].dissolve(by="key").geometry
    cover = gpd.GeoSeries(cover.reindex(scope["key"]).values, index=scope.index, crs=DATA_CRS)
    diffs = [exact_difference(b, c) if c is not None else b
             for b, c in zip(scope.geometry.values, cover.values)]
    return explode_pieces(gpd.GeoDataFrame(
        {"key": scope["key"].values}, geometry=diffs, crs=DATA_CRS), min_area)


def link_gaps_to_eas(gap, inside):
    """One row per (gap, EA): the EAs bordering each gap, else the nearest EA of its barangay."""
    gap_m = gpd.GeoDataFrame({"gap_key": gap["key"]}, geometry=gap.geometry.to_crs(AREA_CRS).values, crs=AREA_CRS)
    ea_m = inside[["key", "geometry"]].to_crs(AREA_CRS)
    near = gpd.sjoin(gap_m.set_geometry(gap_m.buffer(TOUCH_TOL_M)), ea_m, predicate="intersects")
    near = near[near["gap_key"] == near["key"]]

    links = [(i, k, True) for i, k in zip(near.index, near["index_right"])]
    for i in gap_m.index.difference(near.index):
        own = ea_m[ea_m["key"] == gap_m.at[i, "gap_key"]]
        links.append((i, own.distance(gap_m.geometry[i]).idxmin(), False))
    return pd.DataFrame(links, columns=["gap", "ea_idx", "borders"]).astype({"gap": int, "ea_idx": int, "borders": bool})


def detached_candidates(gap, inside, rows):
    """For gaps no EA touches (e.g. an island of the barangay): every EA of the same barangay, nearest
    first, so Switch Fix can pick the one the piece is copied into (the EA becomes multipart)."""
    gap_m = gap.geometry.to_crs(AREA_CRS)
    ea_m = inside[["key", "geometry"]].to_crs(AREA_CRS)
    out = {}
    for g in rows:
        own = ea_m[ea_m["key"] == gap.at[g, "key"]]
        out[int(g)] = [int(i) for i in own.distance(gap_m[g]).sort_values().index]
    return out


STATUS_PENDING = "Not snapped"
STATUS_DONE = "Snapped"
STATUS_FAILED = "Failed"
STATUS_MANUAL = "Fix manually"


def find_discrepancies(ea, bgy, min_area, mode="ea_bgy"):
    """The reference is fixed, so every discrepancy is reported on the target feature that must be snapped to it.

    EA vs BGY: EAs matched to their barangay. BGY vs BGY / EA vs EA: each target feature matched to the
    reference feature with the same geocode. Returns one row per discrepancy, largest first: only the
    mismatched area, with the target's attributes. Hidden columns (starting with "_") say which feature a
    snap changes, what it does (_kind: cut / add / none) and which features it could change instead
    (_candidates, for Switch Fix). Also returns the reference features with no target.
    """
    m = MODES[mode]
    unit, ref = m["unit"], m["ref_unit"]
    issue_out, issue_short, issue_orphan = m["issues"]
    ea = ea.reset_index(drop=True)
    ea["ea_area"] = area_sqm(ea)
    bgy_by_key = bgy.set_index("key")
    for c in ADMIN:  # the target's own names; the reference only fills in what the target lacks
        ea[c] = ea[f"ea_{c}"].where(ea[f"ea_{c}"].notna(), ea["key"].map(bgy_by_key[c]))
    has_bgy = ea["key"].isin(bgy_by_key.index)
    inside = ea[has_bgy]

    # ===== OUTSIDE: parts of each target feature beyond its reference =====
    own_bgy = gpd.GeoSeries(bgy_by_key.geometry.reindex(inside["key"]).values, index=inside.index, crs=DATA_CRS)
    outside_geoms = [exact_difference(a, b) if b is not None else a
                     for a, b in zip(inside.geometry.values, own_bgy.values)]
    outside = explode_pieces(gpd.GeoDataFrame(
        {"ea_idx": inside.index}, geometry=outside_geoms, crs=DATA_CRS), min_area)

    into = pd.Series("", index=outside.index, dtype=object)
    if len(outside):
        pts = gpd.GeoDataFrame(geometry=outside.representative_point(), crs=DATA_CRS)
        hit = gpd.sjoin(pts, bgy[["Geocode", "Label", "geometry"]], predicate="within", how="left")
        hit = hit[~hit.index.duplicated()]
        into = hit["Label"].astype(str) + " (" + hit["Geocode"].astype(str) + ")"
        into = into.where(hit["Geocode"].notna(), f"an area outside the {ref} layer")

    src = ea.loc[outside["ea_idx"]].reset_index(drop=True)
    outside_out = gpd.GeoDataFrame({
        "Issue": issue_out,
        "EA Geocode": src["EA Geocode"],
        **{c: src[c] for c in ADMIN},
        "EA": src["EA"],
        "Area (sqm)": outside["piece_area"],
        "Fix EA": src["EA Geocode"],
        "Status": STATUS_PENDING,
        "Note": f"{unit[0].upper() + unit[1:]} extends into " + into,
        "_ea_idx": outside["ea_idx"].values,
        "_snappable": True,
        "_kind": "cut",
        "_candidates": [[int(i)] for i in outside["ea_idx"].values],
    }, geometry=outside.geometry.values, crs=DATA_CRS)

    # ===== SHORT: reference land its targets do not reach, named after the target(s) that must reach it =====
    gap = uncovered_land(inside, bgy, min_area)
    links = link_gaps_to_eas(gap, inside)

    def joined(col):
        values = links.assign(value=ea[col].reindex(links["ea_idx"]).values).groupby("gap")["value"]
        return values.agg(lambda s: ", ".join(sorted(set(s.dropna().astype(str))))).reindex(gap.index)

    src = ea.loc[links.groupby("gap")["ea_idx"].first().reindex(gap.index).astype(int)].reset_index(drop=True)
    borders = links.groupby("gap")["borders"].first().reindex(gap.index).astype(bool)
    owner, share, ranked = gap_owners(gap, links, inside)
    owner = owner.reindex(gap.index).astype(int)  # the feature a snap extends
    share = share.reindex(gap.index).fillna(1.0)
    n_eas = links.groupby("gap").size().reindex(gap.index).fillna(0).astype(int)
    # A gap along several features, none of which has most of it (e.g. a long hairline strip), would wrap
    # around the neighbours if it were added to one: that must be split by hand (or forced with Switch Fix).
    one_owner = share >= MIN_OWNER_SHARE
    snappable = borders & one_owner
    note = pd.Series(f"{unit[0].upper() + unit[1:]} does not reach its {ref} boundary here", index=gap.index,
                     dtype=object)
    note = note.where(borders, f"No {unit} reaches this detached part of the {ref} (e.g. an island): "
                               f"pick the {unit} to copy it into with Switch Fix (nearest first)")
    detached = detached_candidates(gap, inside, gap.index[~borders])
    note = note.where(~borders | one_owner,
                      "Borders " + n_eas.astype(str) + f" {unit}s and none has most of it: split it between "
                      "them by hand, or pick one with Switch Fix")
    short_out = gpd.GeoDataFrame({
        "Issue": issue_short,
        "EA Geocode": joined("EA Geocode"),
        **{c: src[c] for c in ADMIN},
        "EA": joined("EA"),
        "Area (sqm)": gap["piece_area"],
        "Fix EA": pd.Series(ea["EA Geocode"].reindex(owner.values).values, index=gap.index).where(snappable, ""),
        "Status": pd.Series(STATUS_PENDING, index=gap.index, dtype=object).where(snappable, STATUS_MANUAL),
        "Note": note,
        "_ea_idx": owner.values,
        "_snappable": snappable.values,
        "_kind": "add",
        "_candidates": [ranked.get(g, []) if b else detached.get(int(g), []) for g, b in zip(gap.index, borders)],
    }, geometry=gap.geometry.values, crs=DATA_CRS)

    # ===== TARGETS WITH NO REFERENCE: the whole feature is the discrepancy =====
    orphan = ea[~has_bgy].reset_index(drop=True)
    orphan_out = gpd.GeoDataFrame({
        "Issue": issue_orphan,
        "EA Geocode": orphan["EA Geocode"],
        **{c: orphan[c] for c in ADMIN},
        "EA": orphan["EA"],
        "Area (sqm)": orphan["ea_area"],
        "Fix EA": "",
        "Status": STATUS_MANUAL,
        "Note": f"{ref[0].upper() + ref[1:]} geocode " + orphan["key"].map(full_geocode)
                + f" is not in the {ref} layer",
        "_ea_idx": -1,
        "_snappable": False,
        "_kind": "none",
        "_candidates": [[] for _ in range(len(orphan))],
    }, geometry=orphan.geometry.values, crs=DATA_CRS)

    found = [f for f in (outside_out, short_out, orphan_out) if len(f)]
    out = pd.concat(found, ignore_index=True) if found else short_out
    out = out.sort_values("Area (sqm)", ascending=False, kind="stable").reset_index(drop=True)

    no_ea = bgy[bgy["key"].str[:5].isin(set(ea["key"].str[:5])) & ~bgy["key"].isin(inside["key"])]
    return gpd.GeoDataFrame(out, geometry="geometry", crs=DATA_CRS), no_ea


def gap_owners(gap, links, inside):
    """The feature that receives each gap (the one sharing the longest border with it), that feature's share
    of the gap's border with all bordering features (1.0 when only one borders it), and per gap every
    bordering feature, longest shared border first (the Switch Fix order)."""
    gap_m, ea_m = gap.geometry.to_crs(AREA_CRS), inside.geometry.to_crs(AREA_CRS)
    links = links.assign(shared=0.0)
    tied = links["gap"].duplicated(keep=False)
    for row in links[tied].itertuples():
        links.at[row.Index, "shared"] = gap_m[row.gap].boundary.intersection(
            ea_m[row.ea_idx].buffer(TOUCH_TOL_M)).length
    ordered = links.sort_values("shared", ascending=False, kind="stable")
    best = ordered.drop_duplicates("gap").set_index("gap")
    total = links.groupby("gap")["shared"].sum().reindex(best.index)
    share = (best["shared"] / total).where(total > 0, 1.0)
    ranked = {int(g): [int(i) for i in grp["ea_idx"]] for g, grp in ordered.groupby("gap", sort=False)}
    return best["ea_idx"], share, ranked


def find_self_discrepancies(ea, min_area, mode="ea_ea"):
    """One layer checked against itself: overlaps between its features and gaps enclosed by them.

    Overlap: the shared area of two features; a snap cuts it from the larger one (Switch Fix: the other).
    Gap: a hole in the union of the features; a snap adds it to the feature with the longest border along it.
    """
    unit = MODES[mode]["unit"]
    ea = ea.reset_index(drop=True)
    ea["ea_area"] = area_sqm(ea)
    for c in ADMIN:
        ea[c] = ea[f"ea_{c}"]
    found = []

    # ===== OVERLAPS (pairwise) =====
    shapes = gpd.GeoDataFrame(geometry=ea.geometry, crs=DATA_CRS)
    pairs = gpd.sjoin(shapes, shapes, predicate="intersects")
    pairs = pairs[pairs.index < pairs["index_right"]]
    if len(pairs):
        a_idx, b_idx = pairs.index.values, pairs["index_right"].values
        shared = shapely.intersection(ea.geometry.to_numpy()[a_idx], ea.geometry.to_numpy()[b_idx])
        pieces = explode_pieces(gpd.GeoDataFrame({"a": a_idx, "b": b_idx}, geometry=shared, crs=DATA_CRS),
                                min_area)
        if len(pieces):
            a, b = pieces["a"].astype(int).values, pieces["b"].astype(int).values
            area = ea["ea_area"].values
            fix = [x if area[x] >= area[y] else y for x, y in zip(a, b)]  # cut from the larger feature
            keep = [y if f == x else x for x, y, f in zip(a, b, fix)]
            src = ea.loc[fix].reset_index(drop=True)
            codes = ea["EA Geocode"].astype(str).values
            names = ea["EA"].where(ea["EA"].notna(), "").astype(str).values
            found.append(gpd.GeoDataFrame({
                "Issue": SELF_ISSUES[0],
                "EA Geocode": [f"{codes[x]}, {codes[y]}" for x, y in zip(a, b)],
                **{c: src[c] for c in ADMIN},
                "EA": [", ".join(n for n in (names[x], names[y]) if n) for x, y in zip(a, b)],
                "Area (sqm)": pieces["piece_area"].values,
                "Fix EA": codes[fix],
                "Status": STATUS_PENDING,
                "Note": [f"{unit[0].upper() + unit[1:]}s {codes[x]} and {codes[y]} overlap: cut from {codes[f]} "
                         f"(keep in {codes[k]})" for x, y, f, k in zip(a, b, fix, keep)],
                "_ea_idx": fix,
                "_snappable": True,
                "_kind": "cut",
                "_candidates": [[f, k] for f, k in zip(fix, keep)],
            }, geometry=pieces.geometry.values, crs=DATA_CRS))

    # ===== GAPS: holes enclosed by the features =====
    union = shapely.union_all(ea.geometry.to_numpy())
    holes = [shapely.Polygon(ring) for poly in shapely.get_parts(union) if poly.geom_type == "Polygon"
             for ring in poly.interiors]
    gap = explode_pieces(gpd.GeoDataFrame(geometry=holes, crs=DATA_CRS), min_area) if holes else \
        gpd.GeoDataFrame({"piece_area": []}, geometry=[], crs=DATA_CRS)
    if len(gap):
        gap_m = gpd.GeoDataFrame(geometry=gap.geometry.to_crs(AREA_CRS).buffer(TOUCH_TOL_M).values, crs=AREA_CRS)
        near = gpd.sjoin(gap_m, gpd.GeoDataFrame(geometry=ea.geometry.to_crs(AREA_CRS), crs=AREA_CRS),
                         predicate="intersects")
        links = pd.DataFrame({"gap": near.index.astype(int), "ea_idx": near["index_right"].astype(int).values,
                              "borders": True})
        owner, share, ranked = gap_owners(gap, links, ea)
        owner = owner.reindex(gap.index).fillna(-1).astype(int)
        share = share.reindex(gap.index).fillna(0.0)
        n_eas = links.groupby("gap").size().reindex(gap.index).fillna(0).astype(int)
        snappable = (owner >= 0) & (share >= MIN_OWNER_SHARE)
        codes = ea["EA Geocode"].astype(str)
        joined = links.assign(code=codes.reindex(links["ea_idx"]).values).groupby("gap")["code"].agg(
            lambda s: ", ".join(sorted(set(s)))).reindex(gap.index).fillna("")
        names = links.assign(n=ea["EA"].reindex(links["ea_idx"]).values).groupby("gap")["n"].agg(
            lambda s: ", ".join(sorted(set(s.dropna().astype(str))))).reindex(gap.index).fillna("")
        src = ea.loc[owner.clip(lower=0)].reset_index(drop=True)
        note = pd.Series(f"Gap between {unit}s: added to the one with the longest border along it",
                         index=gap.index, dtype=object)
        note = note.where(snappable, "Borders " + n_eas.astype(str) + f" {unit}s and none has most of it: split it "
                          "between them by hand, or pick one with Switch Fix")
        found.append(gpd.GeoDataFrame({
            "Issue": SELF_ISSUES[1],
            "EA Geocode": joined.values,
            **{c: src[c].values for c in ADMIN},
            "EA": names.values,
            "Area (sqm)": gap["piece_area"].values,
            "Fix EA": pd.Series(codes.reindex(owner.values).values, index=gap.index).where(snappable, "").values,
            "Status": pd.Series(STATUS_PENDING, index=gap.index, dtype=object).where(snappable, STATUS_MANUAL).values,
            "Note": note.values,
            "_ea_idx": owner.values,
            "_snappable": snappable.values,
            "_kind": "add",
            "_candidates": [ranked.get(int(g), []) for g in gap.index],
        }, geometry=gap.geometry.values, crs=DATA_CRS))

    if not found:
        return gpd.GeoDataFrame({c: [] for c in ["Issue", *TABLE_KEYS, "_ea_idx", "_snappable", "_kind",
                                                  "_candidates"]}, geometry=[], crs=DATA_CRS)
    out = pd.concat(found, ignore_index=True).sort_values("Area (sqm)", ascending=False, kind="stable")
    return gpd.GeoDataFrame(out.reset_index(drop=True), geometry="geometry", crs=DATA_CRS)


TABLE_KEYS = ["Status", "EA Geocode", "Fix EA", "EA", *ADMIN, "Area (sqm)", "Note"]


def warn_bgy_overlaps(bgy, min_area, what="barangays"):
    """The reference is taken as fixed, so where its features overlap, targets snapped to them overlap as well."""
    named = gpd.GeoDataFrame({"Barangay": bgy["Label"].where(bgy["Label"].notna(), bgy["Geocode"])},
                             geometry=bgy.geometry, crs=DATA_CRS)
    pairs = gpd.sjoin(named, named, predicate="intersects")
    pairs = pairs[pairs.index < pairs["index_right"]]
    if not len(pairs):
        return
    shared = gpd.GeoSeries(bgy.geometry.loc[pairs.index].values, crs=DATA_CRS).intersection(
        gpd.GeoSeries(bgy.geometry.loc[pairs["index_right"]].values, crs=DATA_CRS))
    pairs = pairs.assign(area=area_sqm(gpd.GeoDataFrame(geometry=shared, crs=DATA_CRS)).values)
    pairs = pairs[pairs["area"] >= min_area].sort_values("area", ascending=False)
    if len(pairs):
        names = [f"{r.Barangay_left} / {r.Barangay_right} ({r.area:,.0f} sqm)" for r in pairs.head(10).itertuples()]
        more = f" ... and {len(pairs) - 10:,} more" if len(pairs) > 10 else ""
        warn(f"{len(pairs):,} pair(s) of {what} overlap in the reference layer, so features snapped to them "
             f"overlap there too: {', '.join(names)}{more}")


# ================= SNAP (selected discrepancies only) =================
def make_valid(geom):
    try:
        return shapely.make_valid(geom, method="structure", keep_collapsed=False)
    except TypeError:  # shapely < 2.1 (QGIS 3.40 ships 2.0) has no method option
        return shapely.make_valid(geom)


def areas_sqm(geoms):
    return gpd.GeoSeries(list(geoms), crs=DATA_CRS).to_crs(AREA_CRS).area.values


def drop_specks(geom, min_area):
    """Remove holes, and parts beside a bigger one, that are smaller than min_area: the edit's leftovers."""
    polys = []
    for poly in shapely.get_parts(geom):
        holes = list(poly.interiors)
        if holes:
            keep = areas_sqm(shapely.Polygon(h) for h in holes) >= min_area
            poly = shapely.Polygon(poly.exterior, [h for h, k in zip(holes, keep) if k])
        polys.append(poly)
    if len(polys) > 1:
        sizes = areas_sqm(polys)
        big = [p for p, a in zip(polys, sizes) if a >= min_area]
        polys = big or [polys[int(sizes.argmax())]]
    return polys[0] if len(polys) == 1 else MultiPolygon(polys)


def restore_vertices(new, original, tol):
    """Put every vertex of `new` that lies within `tol` of a vertex of `original` back on that vertex exactly.

    A grid overlay rounds every coordinate of the EA, including the vertices along edges it shares with its
    neighbours. Those neighbours are not edited, so a moved shared vertex leaves a hairline overlap / gap
    between the two features. This undoes that: only genuinely new vertices (where the cut or the added land
    meets the old outline) keep their grid position.
    """
    old_xy, new_xy = shapely.get_coordinates(original), shapely.get_coordinates(new)
    if not len(old_xy) or not len(new_xy):
        return new
    tree = shapely.STRtree(shapely.points(old_xy))
    hit = tree.query_nearest(shapely.points(new_xy), max_distance=tol, return_distance=False, all_matches=False)
    if not hit.shape[1]:
        return new
    new_xy = new_xy.copy()
    new_xy[hit[0]] = old_xy[hit[1]]
    return shapely.set_coordinates(new, new_xy)


def only_pieces_changed(old, new, cuts, adds, min_area):
    """True if `new` differs from `old` only inside the cut / added pieces (each clump of change outside
    them under half of min_area). Guards against an overlay or a repair (make_valid) that fills in or drops
    land somewhere else; the area check alone would let that through on a large feature."""
    pieces = shapely.union_all([*cuts, *adds]) if (cuts or adds) else shapely.Polygon()
    try:
        change = shapely.difference(shapely.symmetric_difference(old, new), pieces)
    except shapely.errors.GEOSException:
        return False
    change = polygons_only(change)
    if change is None or change.is_empty:
        return True
    clumps = [p for p in shapely.get_parts(change) if p.area > 0]
    return not clumps or bool((areas_sqm(clumps) < min_area / 2).all())


def fixes_pieces(geom, cuts, adds, min_area):
    """True if what `geom` still keeps of the cut pieces and still misses of the added pieces is only specks:
    the leftovers are merged first, so ones side by side count as the one piece a new scan would find, and
    each merged clump must stay under half of min_area."""
    left = [shapely.intersection(geom, c) for c in cuts] + [shapely.difference(a, geom) for a in adds]
    left = [p for g in left if g is not None and not g.is_empty
            for p in shapely.get_parts(polygons_only(g) or shapely.Polygon()) if p.area > 0]
    if not left:
        return True
    clumps = [p for p in shapely.get_parts(shapely.union_all(left)) if p.area > 0]
    return not clumps or bool((areas_sqm(clumps) < min_area / 2).all())


def unsnapped_vertices(scan, row, tol=1e-4, limit=40):
    """Where the circles go when zooming to a discrepancy: the vertices of the Fix feature that are out of
    place (they lie on the overlap / gap and are not shared with the other feature(s) bordering it).

    Falls back to the piece's own vertices away from the pinch points where the two outlines meet, then to
    a point inside the piece. Returns a shapely MultiPoint (working CRS).
    """
    d, ea = scan.discrepancies, scan.ea
    piece = d.geometry[row]
    if d.at[row, "_kind"] == "vertex":  # one square per vertex to match: its centre is the vertex
        return shapely.MultiPoint(shapely.centroid(shapely.get_parts(piece))[:limit])
    pts = shapely.get_coordinates(piece)
    if len(pts) > 1:
        pts = np.unique(pts, axis=0)
    idx = int(d.at[row, "_ea_idx"])
    others = [int(c) for c in (d.at[row, "_candidates"] or []) if int(c) != idx and int(c) in ea.index]
    marked = np.empty((0, 2))
    if len(pts) and idx >= 0 and idx in ea.index:
        fix_xy = shapely.get_coordinates(ea.geometry[idx])
        p = shapely.points(pts)
        on_fix = shapely.STRtree(shapely.points(fix_xy)).query_nearest(
            p, max_distance=tol, return_distance=False, all_matches=False)[0] if len(fix_xy) else np.array([], int)
        keep = np.zeros(len(pts), dtype=bool)
        keep[on_fix] = True
        other_line = shapely.union_all([ea.geometry[o].boundary for o in others]) if others else None
        if other_line is not None and not other_line.is_empty:
            keep &= shapely.distance(p, other_line) > tol  # shared with the neighbour: already in place
        marked = pts[keep]
        if not len(marked) and other_line is not None and not other_line.is_empty:
            fix_line = ea.geometry[idx].boundary
            pinch = (shapely.distance(p, fix_line) <= tol) & (shapely.distance(p, other_line) <= tol)
            marked = pts[~pinch]
    if not len(marked):
        return shapely.MultiPoint([piece.representative_point()])
    if len(marked) > limit:
        marked = marked[np.linspace(0, len(marked) - 1, limit).astype(int)]
    return shapely.MultiPoint(marked)


NEIGHBOUR_PAD_M = 1e-2  # 1 cm: how far around a self-check piece the neighbour's own outline is used


def snap_inputs(scan, idx, rows):
    """(cuts, adds, clip) for snapping feature `idx` with the discrepancy rows `rows`.

    Checked against a reference, these are the discrepancy pieces themselves. In a self check (features
    against each other) the neighbour's own outline is used instead, so the fixed edge lands exactly on it:
    an overlap is cut with the kept feature's shape around the piece (which also takes the hairline
    overlaps under min_area along the same edge), and added gaps are clipped by the bordering features
    so they cannot push into them.
    """
    d, ea = scan.discrepancies, scan.ea
    cuts = [d.geometry[r] for r in rows if d.at[r, "_kind"] == "cut"]
    adds = [d.geometry[r] for r in rows if d.at[r, "_kind"] == "add"]
    if not scan.self_check:
        own = getattr(scan, "ref", {}).get(ea.at[idx, "key"]) if "key" in ea.columns else None
        if own is None or own.is_empty:
            return cuts, adds, None
        # Snapped to its reference: the hairline slivers under min_area touching each piece (where the two
        # edges cross back and forth) go with it, so they cannot merge into a new discrepancy once the piece is
        # gone. Each sliver is taken WHOLE: clipping it to a pad around the piece would put new vertices where
        # the pad's outline crosses the edges (a few mm to cm from the real crossing), and repeated snaps
        # would pile them up at the intersections.
        geom = ea.geometry[idx]

        def slivers(area):
            parts = [q for q in shapely.get_parts(polygons_only(area) or shapely.Polygon()) if q.area > 0]
            return [q for q, a in zip(parts, areas_sqm(parts) if parts else []) if a < scan.min_area]

        outside = slivers(exact_difference(geom, own))
        inside = slivers(exact_difference(own, geom))

        def with_slivers(r, pool):
            pad = shapely.buffer(d.geometry[r], NEIGHBOUR_PAD_M, join_style="mitre")
            touching = [q for q in pool if shapely.intersects(q, pad)]
            return polygons_only(shapely.union_all([d.geometry[r], *touching])) or d.geometry[r]

        cuts = [with_slivers(r, outside) for r in rows if d.at[r, "_kind"] == "cut"]
        adds = [with_slivers(r, inside) for r in rows if d.at[r, "_kind"] == "add"]
        if not adds:
            return cuts, adds, None
        # around the added land (the pad's outline stays outside it, so it adds no vertex)
        reach = shapely.buffer(shapely.union_all(adds), NEIGHBOUR_PAD_M, join_style="mitre")
        # Only land another feature rightly holds (inside its own reference) is left to it: a feature reaching
        # in from another reference is itself cut back to its own by the same snap.
        def rightful(j, g):
            theirs = scan.ref.get(ea.at[j, "key"])
            return shapely.intersection(g, theirs) if theirs is not None else g
        near = [polygons_only(shapely.intersection(rightful(j, g), reach)) for j, g in zip(ea.index, ea.geometry.values)
                if j != idx and g is not None and shapely.intersects(g, reach)]
        near = [g for g in near if g is not None and not g.is_empty]
        return cuts, adds, (shapely.union_all(near) if near else None)

    def others_of(r):
        return [int(c) for c in (d.at[r, "_candidates"] or []) if int(c) != idx and int(c) in ea.index]

    def around(r):
        """The other candidate features' shapes around the piece (its pad's outline stays outside the piece,
        so clipping the added land with them adds no vertex)."""
        pad = shapely.buffer(d.geometry[r], NEIGHBOUR_PAD_M, join_style="mitre")
        parts = [shapely.intersection(ea.geometry[o], pad) for o in others_of(r)]
        return [polygons_only(p) for p in parts if p is not None and not p.is_empty and polygons_only(p) is not None]

    def overlap_slivers(r):
        """The hairline overlaps (under min_area) with the kept feature touching the piece, each WHOLE (not
        clipped to a pad: see above)."""
        pad = shapely.buffer(d.geometry[r], NEIGHBOUR_PAD_M, join_style="mitre")
        out = []
        for o in others_of(r):
            shared = polygons_only(shapely.intersection(ea.geometry[idx], ea.geometry[o]))
            parts = [q for q in shapely.get_parts(shared or shapely.Polygon()) if q.area > 0]
            out += [q for q, a in zip(parts, areas_sqm(parts) if parts else [])
                    if a < scan.min_area and shapely.intersects(q, pad)]
        return out

    cuts = []
    for r in rows:
        if d.at[r, "_kind"] == "cut":
            cuts.append(polygons_only(shapely.union_all([d.geometry[r], *overlap_slivers(r)])) or d.geometry[r])
    near = [p for r in rows if d.at[r, "_kind"] == "add" for p in around(r)]
    return cuts, adds, (shapely.union_all(near) if near else None)

    def around(r):
        """The other candidate features' shapes within NEIGHBOUR_PAD_M of the piece."""
        pad = shapely.buffer(d.geometry[r], NEIGHBOUR_PAD_M, join_style="mitre")
        others = [int(c) for c in (d.at[r, "_candidates"] or []) if int(c) != idx and int(c) in ea.index]
        parts = [shapely.intersection(ea.geometry[o], pad) for o in others]
        return [polygons_only(p) for p in parts if p is not None and not p.is_empty and polygons_only(p) is not None]

    cuts = []
    for r in rows:
        if d.at[r, "_kind"] == "cut":
            cuts.append(shapely.union_all([d.geometry[r], *around(r)]))
    near = [p for r in rows if d.at[r, "_kind"] == "add" for p in around(r)]
    return cuts, adds, (shapely.union_all(near) if near else None)


def snapped_geometry(geom, cuts, adds, min_area, clip=None):
    """The EA after cutting off `cuts` and adding `adds`: (geometry, None), or (None, why it failed).

    Only the edge being fixed may change: every other vertex of the EA is kept bit-for-bit, because the
    neighbouring EAs share those vertices and are not edited. The cut / added pieces are first snapped onto
    the EA (they were found on a 0.1 mm grid, so their edges can miss the EA's own edge by a hair, which
    would leave near-zero-width spikes). The exact overlay keeps the EA's vertices; if its result is invalid
    or has such spikes (a vertex almost on another edge), the overlay is redone on a 0.1 mm, 1 mm and 1 cm
    grid, which merges them, and the EA's own vertices are put back afterwards (restore_vertices). The first
    sturdy result (or else the sturdiest) is used.

    Every candidate must also really fix what was selected (fixes_pieces): each added piece covered and each
    cut piece removed, to within min_area. Without that check a grid result whose restored vertices fall
    back onto the old outline could undo the fix and still be reported as snapped.

    `clip` (features checked against each other): the neighbours around the added pieces, taken off the
    added land only, so a snap can never create a new overlap.
    """
    # Clearance = how far a vertex can move before the polygon becomes invalid. The result should not be
    # more fragile than the EA already was, nor below 1 mm.
    needed = min(10 * GRID_M, shapely.minimum_clearance(geom) * 0.999)
    reason = "invalid"
    best, best_clearance = None, -1.0
    raw_cuts, raw_adds = list(cuts), list(adds)
    if clip is not None and not clip.is_empty:  # what must be covered: the added land the neighbours do not hold
        raw_adds = [polygons_only(shapely.difference(a, clip)) or shapely.Polygon() for a in raw_adds]
    nudged = ([shapely.snap(c, geom, SNAP_TOL_M) for c in cuts], [shapely.snap(a, geom, SNAP_TOL_M) for a in adds])

    def merged(pieces, grid):
        """The pieces as one polygon geometry: rounding a hairline to a grid can collapse it into a line,
        and an overlay refuses a mix of polygons and lines (\"mixed-dimension\")."""
        return polygons_only(shapely.union_all(pieces, grid_size=grid)) or shapely.Polygon()

    # The pieces nudged onto the EA first (avoids near-zero-width spikes), then as found: on a very thin
    # piece the nudge can flatten part of it, so that part would stay behind.
    attempts = [(c, a, grid) for c, a in (nudged, (raw_cuts, raw_adds))
                for grid in (None, GRID_M, 10 * GRID_M, 100 * GRID_M)]
    for cuts, adds, grid in attempts:
        try:
            g = geom
            if cuts:
                g = polygons_only(shapely.difference(g, merged(cuts, grid), grid_size=grid))
            if adds and g is not None:
                added = merged(adds, grid)
                if clip is not None and not clip.is_empty:
                    # only the added land is trimmed: the EA's own land (e.g. an overlap the neighbour is
                    # cutting in the same snap) stays, or it would end up in neither feature
                    added = polygons_only(shapely.difference(added, clip, grid_size=grid)) or shapely.Polygon()
                g = polygons_only(shapely.union(g, added, grid_size=grid))
        except shapely.errors.GEOSException as e:
            reason = f"invalid ({e})"
            continue
        if g is None or g.is_empty:
            reason = "empty"
            continue
        if not g.is_valid:
            g = make_valid(g)
        g = polygons_only(g)
        if g is None:
            reason = "empty"
            continue
        g = drop_specks(g, min_area)
        if grid is not None and not g.is_empty:
            g = restore_vertices(g, geom, 1.5 * grid)  # the grid must not move the EA's untouched vertices
        if g.is_valid and not g.is_empty:
            if not fixes_pieces(g, raw_cuts, raw_adds, min_area):
                reason = "unchanged (the overlay could not move the edge)"
                continue
            if not only_pieces_changed(geom, g, raw_cuts, raw_adds, min_area):
                reason = "changed outside the selected pieces"
                continue
            clearance = shapely.minimum_clearance(g)
            if clearance >= needed:
                return g, None
            if clearance > best_clearance:  # valid but fragile: keep the sturdiest in case none is good
                best, best_clearance = g, clearance
            continue
        reason = "invalid"
    if best is not None:
        return best, None  # still checked again in the layer's CRS before it is written
    return None, reason


def layer_xy(geom, original_latlon, original_layer, convert):
    """x / y of every vertex of the geometry `geom` (working CRS) in the layer's CRS, as an (n, 2) array.

    A vertex that is one of the feature's own (the working-CRS copy of an original vertex, matched within
    KEEP_VERTEX_TOL_M) gets that original layer coordinate back untouched, so converting working CRS -> layer CRS
    cannot nudge the edges shared with neighbouring features (the round trip is not exact for datum
    shifts, e.g. PRS92 <-> WGS84). Only new vertices (where the cut / added land meets the outline) are
    converted, with convert((x, y)) -> layer (x, y).
    """
    xy = shapely.get_coordinates(geom)
    out = np.empty_like(xy)
    done = np.zeros(len(xy), dtype=bool)
    if len(original_latlon) and len(xy):
        tree = shapely.STRtree(shapely.points(original_latlon))
        hit = tree.query_nearest(shapely.points(xy), max_distance=KEEP_VERTEX_TOL_M, return_distance=False,
                                 all_matches=False)
        out[hit[0]] = original_layer[hit[1]]
        done[hit[0]] = True
    for i in np.flatnonzero(~done):
        out[i] = convert(xy[i])
    return out


DENSIFY_M = 100.0  # fallback: edges longer than this get vertices every 100 m (see to_layer_geometry)


def to_layer_geometry(geom, ea_layer, original=None, used=None):
    """Shapely geometry (working CRS) -> valid QgsGeometry in the EA layer's CRS and geometry type, or (None, why).

    A shape valid in the working CRS can cross itself in a lat/lon layer: an edge kilometres long bends up
    to ~10 cm between the two (a straight line in one is a curve in the other), so two long edges close
    together can cross once converted. Then the long edges are given a vertex every DENSIFY_M along the
    working-CRS line (the bend is then under 0.1 mm) and it is tried again; a repair that would split the
    feature (a "bow tie") is never used. `used` (a list), if given, receives the working-CRS geometry that
    was written (the densified one if that was needed).
    """
    attempts = [(geom, False)]
    dense = shapely.segmentize(geom, DENSIFY_M)
    if shapely.get_num_coordinates(dense) != shapely.get_num_coordinates(geom):
        attempts.append((dense, False))
    attempts.append((geom, True))
    why = None
    for g, repair in attempts:
        out, why = _to_layer_geometry_once(g, ea_layer, original, repair)
        if out is not None:
            if used is not None:
                used.append(g)
            return out, None
    return None, why


def _to_layer_geometry_once(geom, ea_layer, original=None, repair=True):
    """One try of to_layer_geometry (see there); `repair`: also try QGIS repairs of an invalid result."""
    from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsGeometry, QgsPointXY,
                           QgsProject, QgsWkbTypes)

    crs = ea_layer.crs()
    geom = shapely.force_2d(geom)
    if crs.isValid() and crs.authid() != DATA_CRS:
        wgs = QgsCoordinateReferenceSystem(DATA_CRS)
        forward = QgsCoordinateTransform(wgs, crs, QgsProject.instance())

        def convert(p):
            q = forward.transform(QgsPointXY(float(p[0]), float(p[1])))
            return q.x(), q.y()

        if original is not None and not original.isNull():
            # Same transform that layer_to_gdf used to read the layer, so the copies match exactly.
            backward = QgsCoordinateTransform(crs, wgs, QgsProject.instance())
            own = np.array([(p.x(), p.y()) for p in original.vertices()], dtype=float).reshape(-1, 2)
            own_latlon = np.array([(q.x(), q.y()) for q in
                                   (backward.transform(QgsPointXY(float(x), float(y))) for x, y in own)],
                                  dtype=float).reshape(-1, 2)
            geom = shapely.set_coordinates(geom, layer_xy(geom, own_latlon, own, convert))
        else:
            geom = shapely.set_coordinates(geom, np.array([convert(p) for p in shapely.get_coordinates(geom)],
                                                          dtype=float).reshape(-1, 2))
    g = QgsGeometry()
    g.fromWkb(shapely.to_wkb(geom))
    if g.isNull() or g.isEmpty():
        return None, "it is empty in the EA layer's CRS"

    if not g.isGeosValid():
        # Converting the new vertices can leave near-duplicate vertices or hairline self-touches: try to repair
        # them, and accept a repair only if it is valid polygon geometry with the same area. The repairs only
        # touch the broken spot: snapping the whole feature to a grid is not used, because it would move the
        # edges shared with the neighbouring features and leave hairline overlaps / gaps between them.
        if not repair:
            return None, "it becomes invalid when converted to the EA layer's CRS"
        deduped = QgsGeometry(g)
        deduped.removeDuplicateNodes(1e-12)
        candidates = [deduped, g.makeValid()]
        fixed = None
        parts_before = int(shapely.get_num_geometries(geom))
        for c in candidates:
            if c is None or c.isNull() or c.isEmpty():
                continue
            if c.type() != QgsWkbTypes.PolygonGeometry:
                c = c.convertToType(QgsWkbTypes.PolygonGeometry, True)
                if c is None or c.isNull():
                    continue
            # a repair must not split the feature: makeValid turns a pinched outline into two parts touching
            # at a point (a "bow tie") plus specks. Specks are dropped; the rest must keep its part count and
            # area, else the feature is left untouched.
            cs = shapely.from_wkb(bytes(c.asWkb()))
            cparts = [q for q in shapely.get_parts(cs) if q.area > 1e-6 * max(cs.area, 1e-30)]
            if not cparts or len(cparts) != parts_before:
                continue
            if len(cparts) != shapely.get_num_geometries(cs):
                cs = cparts[0] if len(cparts) == 1 else MultiPolygon(cparts)
                c = QgsGeometry()
                c.fromWkb(shapely.to_wkb(cs))
            if c.isGeosValid() and abs(c.area() - g.area()) <= 1e-6 * abs(g.area()):
                fixed = c
                break
        if fixed is None:
            return None, ("it becomes invalid when converted to the EA layer's CRS (left untouched rather than "
                          "moving the edges it shares with its neighbours). Fix it by hand")
        g = fixed

    if QgsWkbTypes.isMultiType(ea_layer.wkbType()):
        g.convertToMultiType()
    elif g.isMultipart():
        if g.constGet().numGeometries() > 1:
            return None, "it needs several parts, but the EA layer is single-part"
        g.convertToSingleType()
    return g, None


class Scan:
    """Everything a scan found, kept so that selected discrepancies can be snapped afterwards."""

    def __init__(self, ea, discrepancies, layer, fids, min_area, mode="ea_bgy", self_check=False):
        self.ea = ea                        # target features as scanned (working CRS; src_layer = the id of the
                                            # layer each came from); updated after each snap
        self.discrepancies = discrepancies  # one row per discrepancy; Status updated after each snap
        self.layer = layer                  # in-memory Discrepancies layer, not in the project (None if none)
        self.fids = fids                    # its feature id for each discrepancy row
        self.min_area = min_area
        self.mode = mode
        self.self_check = self_check        # True: one layer checked against itself (overlaps / gaps)
        self.ref = {}                       # reference key -> its geometry (working CRS), when checked against one
        self.crs = DATA_CRS                 # the working CRS its geometries are in
        self.issues = mode_issues(mode, self_check)
        self.unit = MODES[mode]["unit"]
        self.messages = {}                  # row -> why it failed / what changed
        self.history = []                   # one entry per Snap Selected that changed something (for undo)
        self.needs_multi = {}               # layer id -> rows the last snap refused: single-part layer


def _set_layer_values(scan, rows, columns):
    """Copy the given columns of the discrepancy rows into the scratch Discrepancies layer."""
    if scan.layer is None:
        return
    idx = {c: scan.layer.fields().indexOf(c) for c in columns}
    changes = {scan.fids[r]: {i: scan.discrepancies.at[r, c] for c, i in idx.items() if i >= 0} for r in rows}
    if changes:
        scan.layer.dataProvider().changeAttributeValues(changes)
        scan.layer.triggerRepaint()


def switch_fix(scan, row):
    """Give a discrepancy to the next feature that could take it (overlap: cut it from the other feature;
    gap: add it to the next bordering feature). Returns the new Fix code, or None if there is no other."""
    d = scan.discrepancies
    cands = list(d.at[row, "_candidates"] or [])
    if d.at[row, "Status"] == STATUS_DONE or not cands or (len(cands) < 2 and bool(d.at[row, "_snappable"])):
        return None
    cur = int(d.at[row, "_ea_idx"])
    nxt = cands[(cands.index(cur) + 1) % len(cands)] if cur in cands and bool(d.at[row, "_snappable"]) else cands[0]
    code = scan.ea.at[nxt, "EA Geocode"]
    d.at[row, "_ea_idx"] = nxt
    d.at[row, "_snappable"] = True
    d.at[row, "Fix EA"] = code
    d.at[row, "Status"] = STATUS_PENDING
    if d.at[row, "_kind"] == "cut" and len(cands) == 2:
        other = scan.ea.at[cands[1 - cands.index(nxt)], "EA Geocode"]
        d.at[row, "Note"] = f"Overlap: cut from {code} (keep in {other}). Chosen with Switch Fix"
    else:
        d.at[row, "Note"] = f"Add to {code}. Chosen with Switch Fix"
    scan.messages.pop(row, None)
    _set_layer_values(scan, [row], ["Fix EA", "Status", "Note"])
    return code


def _ring_with_points(ring_xy, pts, tol, same=SAME_VERTEX_M):
    """Ring coordinates (closed, (n, 2)) with each point of pts that lies on one of its segments (within
    tol) inserted into that segment, in order along it. Points already at a vertex (within `same`) are
    skipped."""
    a, b = ring_xy[:-1], ring_xy[1:]
    ab = b - a
    seg_len2 = (ab ** 2).sum(axis=1)
    inserts = {}
    for p in pts:
        ap = p - a
        t = np.clip(np.where(seg_len2 > 0, (ap * ab).sum(axis=1) / np.where(seg_len2 > 0, seg_len2, 1), 0), 0, 1)
        dist2 = ((a + t[:, None] * ab - p) ** 2).sum(axis=1)
        k = int(dist2.argmin())
        if dist2[k] > tol * tol or (((ring_xy - p) ** 2).sum(axis=1) <= same * same).any():
            continue
        if 0 < t[k] < 1:
            inserts.setdefault(k, []).append((float(t[k]), p))
    if not inserts:
        return ring_xy, 0
    out, n = [], 0
    for k in range(len(a)):
        out.append(a[k])
        for _t, p in sorted(inserts.get(k, []), key=lambda e: e[0]):
            out.append(p)
            n += 1
    out.append(ring_xy[-1])
    return np.array(out), n


def _vertices_onto(target, source_xy, tol):
    """`target` (shapely, layer CRS) with every point of source_xy that lies on one of its edges (within
    tol) but is not one of its vertices inserted there, at exactly that point. Returns (geometry, count).

    Only the outline gains vertices: an edge moves by at most tol to pass through the new point. The result
    is refused (count 0) if it is not valid or its area moves by more than that allows."""
    if not len(source_xy):
        return target, 0
    pts = shapely.points(source_xy)
    near = shapely.distance(pts, target.boundary) <= tol
    if not near.any():
        return target, 0
    cand = source_xy[near]
    polys, total = [], 0
    for poly in shapely.get_parts(target):
        rings = []
        for ring in [poly.exterior, *poly.interiors]:
            xy = shapely.get_coordinates(ring)
            close = cand[shapely.distance(shapely.points(cand), ring) <= tol]
            if len(close):
                xy, n = _ring_with_points(xy, close, tol)
                total += n
            rings.append(xy)
        polys.append(shapely.Polygon(rings[0], rings[1:]))
    if not total:
        return target, 0
    new = polys[0] if len(polys) == 1 and target.geom_type == "Polygon" else MultiPolygon(polys)
    if not new.is_valid or abs(new.area - target.area) > tol * target.length:
        return target, 0
    return new, total


SLIDE_M = 0.05  # a vertex on its own reference line this close (5 cm) to a reference vertex slides onto it
DUPLICATE_M = 2e-3  # vertices closer than this (2 mm) are one vertex (QGIS flags 1e-8 deg, ~1.1 mm)


SPIKE_DEG = 1.0  # a vertex whose two edges meet at less than this angle ...
SPIKE_AREA_SQM = 0.01  # ... and enclose less than this is a needle left by an overlay: removed


def _clean_ring(xy, tol=DUPLICATE_M):
    """Closed ring coordinates without consecutive duplicate vertices (within tol) and without spikes (a
    vertex that goes out and straight back, A-B-A). Returns None if fewer than 3 distinct vertices remain."""
    pts = [p for p in xy[:-1]]
    changed = True
    while changed and len(pts) >= 3:
        changed = False
        out = []
        for p in pts:
            if out and ((out[-1] - p) ** 2).sum() <= tol * tol:
                changed = True
                continue
            out.append(p)
        if len(out) > 1 and ((out[0] - out[-1]) ** 2).sum() <= tol * tol:
            out.pop()
            changed = True
        n = len(out)
        if n >= 4:
            for i in range(n):  # a needle (under SPIKE_DEG) enclosing a speck: drop its tip
                a, b, c = out[i - 1], out[i], out[(i + 1) % n]
                v1, v2 = a - b, c - b
                l1, l2 = float(np.hypot(*v1)), float(np.hypot(*v2))
                if l1 <= 0 or l2 <= 0:
                    continue
                cos = float((v1 * v2).sum()) / (l1 * l2)
                if cos > np.cos(np.radians(SPIKE_DEG)) and abs(v1[0] * v2[1] - v1[1] * v2[0]) / 2 < SPIKE_AREA_SQM:
                    out = out[:i] + out[i + 1:]
                    changed = True
                    break
            n = len(out)
        if n >= 3:
            for i in range(n):  # A-B-A: drop B and the repeated A
                a, c = out[i - 1], out[(i + 1) % n]
                if ((a - c) ** 2).sum() <= tol * tol:
                    keep = [k for k in range(n) if k not in (i, (i + 1) % n)]
                    out = [out[k] for k in keep]
                    changed = True
                    break
        pts = out
    if len(pts) < 3:
        return None
    return np.vstack([pts, pts[:1]])


def clean_vertices(geom, tol=DUPLICATE_M):
    """`geom` with duplicate vertices and spikes removed from every ring (see _clean_ring). Returns the same
    object if nothing changed, or if the cleaned shape would be invalid / not the same area."""
    polys, changed = [], False
    for poly in shapely.get_parts(geom):
        rings = []
        for k, ring in enumerate([poly.exterior, *poly.interiors]):
            xy = shapely.get_coordinates(ring)
            new = _clean_ring(xy, tol)
            if new is None or len(new) != len(xy):
                changed = True
            if new is None:
                if k == 0:
                    break  # the whole part is a speck
                continue
            rings.append(new)
        if rings:
            polys.append(shapely.Polygon(rings[0], rings[1:]))
    if not changed or not polys:
        return geom
    new = polys[0] if len(polys) == 1 and geom.geom_type == "Polygon" else MultiPolygon(polys)
    if not new.is_valid or abs(new.area - geom.area) > tol * geom.length:
        return geom
    return new


REF_TOL_M = 0.05  # a vertex this close (5 cm) to its own reference line belongs on it (checked against one)


_REF_LINES = {}


def all_ref_lines(ref):
    """The outlines of all reference features (dict key -> geometry) as one geometry, cached per dict."""
    key = id(ref)
    if key not in _REF_LINES:
        _REF_LINES.clear()
        lines = [g.boundary for g in ref.values() if g is not None and not g.is_empty]
        _REF_LINES[key] = shapely.union_all(lines) if lines else None
    return _REF_LINES[key]


def ref_off_vertices(geom, ref, others=None):
    """Vertices of `geom` lying near its reference outline (within REF_TOL_M) but not on it, and where each
    belongs: the reference vertex nearby (within DUPLICATE_M), else the nearest point of the reference line.
    A vertex lying exactly on another reference outline (`others`: all of them) is left out: there the
    reference features themselves do not meet (e.g. three barangay corners mm apart), so no position fits
    all of them and moving it would pull the neighbours off their own lines.
    Returns (points (n, 2), targets (n, 2))."""
    empty = (np.empty((0, 2)), np.empty((0, 2)))
    if ref is None or ref.is_empty or geom is None or geom.is_empty:
        return empty
    line = ref.boundary
    xy = np.unique(shapely.get_coordinates(geom), axis=0)
    pts = shapely.points(xy)
    d = shapely.distance(pts, line)
    off = (d > SAME_VERTEX_M) & (d <= REF_TOL_M)
    if others is not None and off.any():
        off &= shapely.distance(pts, others) > SAME_VERTEX_M
    if not off.any():
        return empty
    rv = np.unique(shapely.get_coordinates(ref), axis=0)
    tree = shapely.STRtree(shapely.points(rv))
    targets = []
    for pt in pts[off]:
        hit = tree.query_nearest(pt, max_distance=DUPLICATE_M, return_distance=False, all_matches=False)
        if len(hit):
            targets.append(rv[int(hit[0])])
        else:
            targets.append(shapely.get_coordinates(shapely.shortest_line(pt, line))[1])
    return xy[off], np.array(targets).reshape(-1, 2)


def _ref_inserts(ring_xy, line, rv, rv_tree):
    """{segment index: [(t, point)]}: reference vertices to put into the ring's segments that run along the
    reference line (both ends on it) and pass within REF_TOL_M of them (inside the segment, not at an end)."""
    on = shapely.distance(shapely.points(ring_xy), line) <= SAME_VERTEX_M
    out = {}
    for k in range(len(ring_xy) - 1):
        if not (on[k] and on[k + 1]):
            continue
        a, b = ring_xy[k], ring_xy[k + 1]
        ab = b - a
        L2 = float((ab ** 2).sum())
        if L2 <= 0:
            continue
        seg = shapely.LineString([a, b])
        for h in rv_tree.query(seg, predicate="dwithin", distance=REF_TOL_M):
            q = rv[int(h)]
            t = float(((q - a) * ab).sum() / L2)
            if t <= 0 or t >= 1 or ((q - a) ** 2).sum() <= SAME_VERTEX_M ** 2 \
                    or ((q - b) ** 2).sum() <= SAME_VERTEX_M ** 2:
                continue
            out.setdefault(k, []).append((t, q))
    return out


def ref_missing_vertices(geom, ref):
    """Reference vertices missing from `geom`'s edges that run along the reference line (see _ref_inserts),
    as an (n, 2) array."""
    if ref is None or ref.is_empty or geom is None or geom.is_empty:
        return np.empty((0, 2))
    line, rv = ref.boundary, np.unique(shapely.get_coordinates(ref), axis=0)
    tree = shapely.STRtree(shapely.points(rv))
    pts = []
    for poly in shapely.get_parts(geom):
        for ring in [poly.exterior, *poly.interiors]:
            for lst in _ref_inserts(shapely.get_coordinates(ring), line, rv, tree).values():
                pts.extend(q for _t, q in lst)
    return np.unique(np.array(pts), axis=0) if pts else np.empty((0, 2))


def insert_ref_vertices(geom, ref):
    """`geom` with the reference vertices missing from its edges along the reference line put in (each such
    edge then follows the reference line vertex for vertex). Returns (geometry, count)."""
    if ref is None or ref.is_empty or geom is None or geom.is_empty:
        return geom, 0
    line, rv = ref.boundary, np.unique(shapely.get_coordinates(ref), axis=0)
    tree = shapely.STRtree(shapely.points(rv))
    polys, total = [], 0
    for poly in shapely.get_parts(geom):
        rings = []
        for ring in [poly.exterior, *poly.interiors]:
            xy = shapely.get_coordinates(ring)
            ins = _ref_inserts(xy, line, rv, tree)
            if ins:
                out = []
                for k in range(len(xy) - 1):
                    out.append(xy[k])
                    for _t, q in sorted(ins.get(k, []), key=lambda e: e[0]):
                        out.append(q)
                        total += 1
                out.append(xy[-1])
                xy = np.array(out)
            rings.append(xy)
        polys.append(shapely.Polygon(rings[0], rings[1:]))
    if not total:
        return geom, 0
    new = polys[0] if len(polys) == 1 and geom.geom_type == "Polygon" else MultiPolygon(polys)
    return new, total


def share_vertices(scan, layer, snapped, undo_geoms):
    """Give the snapped features and their neighbours (same layer) the same vertices along shared edges.

    The overlay puts new vertices on the snapped feature where its new edge meets the neighbour's (and the
    neighbour's corners along it): the shapes match, but the neighbour lacks those vertices, which shows as
    an unmatched vertex in the editor and can leave a hairline after later edits. Each such vertex is put
    into the other feature too, exactly on its edge, so neither shape changes. Runs inside the snap's edit
    command, so Undo Last Snap / Edit > Undo takes it back with the snap.

    "On the edge" is tested in the working CRS (straight edges as the map draws them). The vertices are
    written in the layer's CRS: a shared vertex gets the other feature's own layer coordinate, so both
    features hold exactly the same point."""
    from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsFeatureRequest,
                           QgsGeometry, QgsPointXY, QgsProject, QgsWkbTypes)

    ea = scan.ea
    layer_id = layer.id()
    tol = SHARE_TOL_M
    by_fid = {int(f): i for i, f, l in zip(ea.index, ea["src_fid"], ea["src_layer"]) if l == layer_id}
    to_work = to_layer = None
    if layer.crs().isValid() and layer.crs().authid() != DATA_CRS:
        work = QgsCoordinateReferenceSystem(DATA_CRS)
        to_work = QgsCoordinateTransform(layer.crs(), work, QgsProject.instance())
        to_layer = QgsCoordinateTransform(work, layer.crs(), QgsProject.instance())
    geoms, own = {}, {}  # fid -> shapely geometry in the working CRS; fid -> (working xy, layer xy) of its vertices

    def as_work(q):
        w = QgsGeometry(q)
        if to_work is not None:
            w.transform(to_work)
        return shapely.from_wkb(bytes(w.asWkb()))

    def current(fid):
        if fid not in geoms:
            q = layer.getFeature(fid).geometry()
            geoms[fid] = as_work(q)
            lxy = np.array([(p.x(), p.y()) for p in q.vertices()], dtype=float).reshape(-1, 2)
            own[fid] = (shapely.get_coordinates(geoms[fid]), lxy)
        return geoms[fid]

    touched = set()
    pairs = []
    for idx in snapped:
        fid = int(ea.at[idx, "src_fid"])
        current(fid)
        box = layer.getFeature(fid).geometry().boundingBox()
        box.grow(max(box.width(), box.height()) * 1e-5)
        for other in layer.getFeatures(QgsFeatureRequest().setFilterRect(box).setNoAttributes()):
            ofid = other.id()
            if ofid != fid and ofid in by_fid and (ofid, fid) not in pairs:
                pairs.append((fid, ofid))
    keys = {int(f): k for f, k, l in zip(ea["src_fid"], ea["key"], ea["src_layer"]) if l == layer_id} \
        if "key" in ea.columns else {}
    ref_lines = {}

    def on_own_ref(fid, p):
        """True if the point lies on the feature's own reference outline (checked against one): kept put."""
        if not scan.ref or fid not in keys:
            return False
        if fid not in ref_lines:
            r = scan.ref.get(keys[fid])
            ref_lines[fid] = r.boundary if r is not None else None
        return ref_lines[fid] is not None and shapely.distance(p, ref_lines[fid]) <= SAME_VERTEX_M

    def move_vertex(p, q, near):
        """Move vertex p to q in every loaded feature that has it (a vertex shared with a third feature moves
        there too, so that pair stays matched). All or nothing: refused if any result is invalid."""
        new = {}
        for f in near:
            g = geoms[f]
            xy = shapely.get_coordinates(g)
            hit = ((xy - p) ** 2).sum(axis=1) <= SAME_VERTEX_M ** 2
            if hit.any():
                xy[hit] = q
                g2 = shapely.set_coordinates(shapely.from_wkb(shapely.to_wkb(g)), xy)
                if not g2.is_valid or abs(g2.area - g.area) > REF_TOL_M * g.length:
                    return False
                new[f] = g2
        geoms.update(new)
        touched.update(new)
        return bool(new)

    def pair(fid, ofid):
        """Vertices of fid lying within tol of ofid's edge, put onto it. Returns how many."""
        a, b = current(fid), current(ofid)
        if shapely.distance(a, b) > tol:
            return 0
        done = 0
        line, bxy = b.boundary, np.unique(shapely.get_coordinates(b), axis=0)
        bpts = shapely.points(bxy)
        xy = np.unique(shapely.get_coordinates(a), axis=0)
        pts = shapely.points(xy)
        d = shapely.distance(pts, line)
        for p, pt, dist in zip(xy, pts, d):
            if dist > tol or shapely.distance(pt, bpts).min() <= SAME_VERTEX_M:
                continue  # not near the edge, or already one of its vertices
            slide = None
            if dist > SAME_VERTEX_M and on_own_ref(fid, pt):
                # on its own reference line: it may only slide along that line onto the neighbour's vertex,
                # and never if it is a reference vertex itself (a barangay corner stays put)
                vd = shapely.distance(pt, bpts)
                rt = ref_vertices()
                if vd.min() <= tol and on_own_ref(fid, bpts[int(vd.argmin())]) and not (
                        rt is not None and len(rt.query(pt, predicate="dwithin", distance=SAME_VERTEX_M))):
                    slide = bxy[int(vd.argmin())]
            if dist > SAME_VERTEX_M and (slide is not None or not on_own_ref(fid, pt)):
                # off the edge: move the vertex onto the neighbour's vertex nearby, else onto its edge
                vd = shapely.distance(pt, bpts)
                q = slide if slide is not None else bxy[int(vd.argmin())] if vd.min() <= tol else \
                    np.array(shapely.get_coordinates(shapely.shortest_line(pt, line))[1])
                if not move_vertex(p, q, [f for f in geoms if shapely.distance(geoms[f], pt) <= tol]):
                    continue
                p, pt = q, shapely.points(q)
                if shapely.distance(pt, bpts).min() <= SAME_VERTEX_M:
                    done += 1
                    continue
            # on the edge (or kept on its own reference line): put it into the neighbour's edge
            new_b, nb = _vertices_onto(geoms[ofid], np.array([p]), tol)
            if nb:
                geoms[ofid] = new_b
                touched.add(ofid)
                done += 1
            elif on_own_ref(fid, pt):
                # it stays put (on its reference line) but cannot go into the neighbour's edge (that edge would
                # fold back): slide the neighbour's nearby vertex along its own line onto it instead
                vd = shapely.distance(pt, bpts)
                for k in np.argsort(vd):
                    if vd[k] > tol:
                        break
                    v = bxy[int(k)]
                    if on_own_ref(ofid, bpts[int(k)]) and move_vertex(
                            v, p, [f for f in geoms if shapely.distance(geoms[f], bpts[int(k)]) <= tol]):
                        done += 1
                        break
        return done

    def ref_vertices():
        """STRtree of the reference vertices of the features loaded so far (a weld keeps them in place)."""
        if not scan.ref:
            return None
        rxy = [shapely.get_coordinates(r) for r in (scan.ref.get(keys.get(f)) for f in geoms) if r is not None]
        return shapely.STRtree(shapely.points(np.unique(np.concatenate(rxy), axis=0))) if rxy else None

    def weld():
        """Vertices within DUPLICATE_M of each other, in any loaded feature, become one identical point in
        every feature that has them (one on a feature's own reference line wins, else the most shared one),
        then exact duplicates and spikes are dropped. Done for all features at once, so a vertex pair
        between neighbours stays a pair."""
        fids = list(geoms)
        ref_vertex_tree = ref_vertices()
        arrays = [shapely.get_coordinates(geoms[f]) for f in fids]
        if not arrays:
            return
        xy = np.concatenate(arrays)
        owner = np.concatenate([np.full(len(a), k) for k, a in enumerate(arrays)])
        pts = shapely.points(xy)
        i, j = shapely.STRtree(pts).query(pts, predicate="dwithin", distance=DUPLICATE_M)
        keep = (i < j) & (((xy[i] - xy[j]) ** 2).sum(axis=1) > SAME_VERTEX_M ** 2)
        if not keep.any():
            new_xy = xy
        else:
            parent = list(range(len(xy)))

            def root(k):
                while parent[k] != k:
                    parent[k] = parent[parent[k]]
                    k = parent[k]
                return k
            for a, b in zip(i[keep], j[keep]):
                parent[root(a)] = root(b)
            groups = {}
            for k in np.unique(np.concatenate([i[keep], j[keep]])):
                groups.setdefault(root(k), []).append(k)
            # every vertex at (or within a micron of) a group member belongs to it too
            new_xy = xy.copy()
            for members in groups.values():
                cand = np.array(members)
                score = []
                for k in cand:
                    same = ((xy - xy[k]) ** 2).sum(axis=1) <= SAME_VERTEX_M ** 2
                    on_ref = any(on_own_ref(fids[o], pts[k]) for o in np.unique(owner[same]))
                    is_ref_vertex = ref_vertex_tree is not None and len(ref_vertex_tree.query(
                        pts[k], predicate="dwithin", distance=SAME_VERTEX_M)) > 0
                    score.append((is_ref_vertex, on_ref, int(same.sum())))  # a reference vertex wins
                rep = xy[cand[max(range(len(cand)), key=lambda n: score[n])]]
                for k in cand:
                    same = ((xy - xy[k]) ** 2).sum(axis=1) <= SAME_VERTEX_M ** 2
                    new_xy[same] = rep
        start = 0
        for f, a in zip(fids, arrays):
            g = geoms[f]
            part = new_xy[start:start + len(a)]
            start += len(a)
            g2 = g if np.array_equal(part, a) else shapely.set_coordinates(shapely.from_wkb(shapely.to_wkb(g)), part)
            g2 = clean_vertices(g2, SAME_VERTEX_M)
            if g2 is g:
                continue
            if g2.is_valid and abs(g2.area - g.area) <= tol * g.length:
                geoms[f] = g2
                touched.add(f)

    for fid, ofid in pairs:  # load them and their neighbours, so a vertex moved is found in every feature
        for f in (fid, ofid):  # that has it
            current(f)
            box = layer.getFeature(f).geometry().boundingBox()
            box.grow(max(box.width(), box.height()) * 1e-5)
            for other in layer.getFeatures(QgsFeatureRequest().setFilterRect(box).setNoAttributes()):
                if other.id() in by_fid:
                    current(other.id())
    weld()  # first, no near-duplicate vertices / spikes (e.g. left by the snap's overlay)
    if scan.ref:  # checked against a reference: each feature's edges along its own line follow it exactly
        for fid in list(geoms):  # (the neighbours too: they share those edges)
            ref = scan.ref.get(keys.get(fid))
            if ref is None:
                continue
            for p, q in zip(*ref_off_vertices(geoms[fid], ref, all_ref_lines(scan.ref))):  # onto the line (its vertex if one is near)
                pt = shapely.points(p)
                move_vertex(p, q, [f for f in geoms if shapely.distance(geoms[f], pt) <= REF_TOL_M])
            g = geoms[fid]
            g2, n = insert_ref_vertices(g, ref)  # the line's own vertices along those edges
            if n and g2.is_valid and abs(g2.area - g.area) <= REF_TOL_M * g.length:
                geoms[fid] = g2
                touched.add(fid)
    for _ in range(3):  # a vertex put in can bring another within reach: repeat until nothing changes
        added = 0
        for fid, ofid in pairs:
            added += pair(fid, ofid) + pair(ofid, fid)
        if not added:
            break
    if scan.ref:  # a vertex on its own reference line, a few mm-cm beside a reference vertex: slide onto it,
        # so an intersection is one vertex, not a cluster (it stays on the line: only the edge leaving it moves)
        rt = ref_vertices()
        if rt is not None:
            rverts = shapely.get_coordinates(rt.geometries)
            for fid in list(geoms):
                xy = np.unique(shapely.get_coordinates(geoms[fid]), axis=0)
                pts = shapely.points(xy)
                for p, pt in zip(xy, pts):
                    if not on_own_ref(fid, pt):
                        continue
                    hit = rt.query_nearest(pt, max_distance=SLIDE_M, return_distance=False, all_matches=False)
                    if not len(hit):
                        continue
                    q = rverts[int(hit[0])]
                    if ((q - p) ** 2).sum() <= SAME_VERTEX_M ** 2 or not on_own_ref(fid, shapely.points(q)):
                        continue
                    move_vertex(p, q, [f for f in geoms if shapely.distance(geoms[f], pt) <= SLIDE_M])
    weld()  # a vertex put in or moved can land next to another: one point again, everywhere

    if not touched:
        return 0
    # every known vertex: working xy -> its exact layer xy (a shared vertex takes its owner's coordinate)
    pool_work = np.concatenate([w for w, _ in own.values()])
    pool_layer = np.concatenate([l for _, l in own.values()])

    def convert(p):
        q = to_layer.transform(QgsPointXY(float(p[0]), float(p[1])))
        return q.x(), q.y()

    for fid in touched:
        idx = by_fid[fid]
        geom = geoms[fid]
        if to_layer is not None:
            geom = shapely.set_coordinates(geom, layer_xy(geom, pool_work, pool_layer, convert))
        g = QgsGeometry()
        g.fromWkb(shapely.to_wkb(geom))
        if QgsWkbTypes.isMultiType(layer.wkbType()):
            g.convertToMultiType()
        if g.isNull() or not g.isGeosValid() or not layer.changeGeometry(fid, g):
            continue
        if idx not in undo_geoms:
            undo_geoms[idx] = ea.at[idx, "geometry"]
        ea.at[idx, "geometry"] = as_work(layer.getFeature(fid).geometry())
        ea.at[idx, "src_hash"] = geometry_hash(layer.getFeature(fid).geometry())
    return len(touched)


VERTEX_MARK_M = 0.05  # each unmatched vertex is shown in the list's layer as a 10 cm square around it


def neighbour_lists(geoms):
    """For each geometry, the indexes of the others within SHARE_TOL_M of it."""
    geoms = list(geoms)
    out = [[] for _ in geoms]
    if len(geoms) < 2:
        return out
    i, j = shapely.STRtree(geoms).query(geoms, predicate="dwithin", distance=SHARE_TOL_M)
    for a, b in zip(i, j):
        if a != b:
            out[int(a)].append(int(b))
    return out


def vertex_problems(geoms, i, nbrs, ref=None, others=None):
    """Vertices of geoms[i] (working CRS) that need matching, as an (n, 2) array: within SHARE_TOL_M of a
    neighbour's edge without the neighbour having a vertex there (within SAME_VERTEX_M), closer than
    DUPLICATE_M to the next vertex of the same ring (a duplicate), and, with its reference `ref`: off the
    reference line by up to REF_TOL_M, or a reference vertex missing from an edge along that line."""
    g = geoms[i]
    if g is None or g.is_empty:
        return np.empty((0, 2))
    xy = np.unique(shapely.get_coordinates(g), axis=0)
    pts = shapely.points(xy)
    bad = np.zeros(len(xy), dtype=bool)
    for j in nbrs:
        b = geoms[j]
        if b is None or b.is_empty or shapely.distance(g, b) > SHARE_TOL_M:
            continue
        near = np.flatnonzero(shapely.distance(pts, b.boundary) <= SHARE_TOL_M)
        if not len(near):
            continue
        bv = shapely.points(np.unique(shapely.get_coordinates(b), axis=0))
        hit = shapely.STRtree(bv).query_nearest(pts[near], max_distance=SAME_VERTEX_M, return_distance=False,
                                                all_matches=False)[0]
        miss = np.ones(len(near), dtype=bool)
        miss[hit] = False
        bad[near[miss]] = True
    dups = []
    for poly in shapely.get_parts(g):
        for ring in [poly.exterior, *poly.interiors]:
            c = shapely.get_coordinates(ring)[:-1]
            if len(c) < 2:
                continue
            step = np.hypot(*(np.roll(c, -1, axis=0) - c).T)
            dups.extend(c[step <= DUPLICATE_M])
    out = [xy[bad]]
    if dups:
        out.append(np.array(dups))
    if ref is not None:
        out += [ref_off_vertices(g, ref, others)[0], ref_missing_vertices(g, ref)]
    out = np.vstack([o.reshape(-1, 2) for o in out])
    return np.unique(out, axis=0) if len(out) else out


def find_vertex_issues(ea, unit="EA", ref=None):
    """One 'Unmatched vertices' row per feature with vertices to match (see vertex_problems): Snap Selected
    matches them (share_vertices). The row's shape is a small square around each such vertex."""
    cols = ["Issue", *TABLE_KEYS, "_ea_idx", "_snappable", "_kind", "_candidates"]
    geoms = list(ea.geometry.values)
    nbrs = neighbour_lists(geoms)
    rows = []
    codes = ea["EA Geocode"].astype(str).values
    for k, idx in enumerate(ea.index):
        own = ref.get(ea.at[idx, "key"]) if ref and "key" in ea.columns else None
        pts = vertex_problems(geoms, k, nbrs[k], own, all_ref_lines(ref) if ref else None)
        if not len(pts):
            continue
        marks = shapely.union_all(shapely.buffer(shapely.points(pts), VERTEX_MARK_M, cap_style="square"))
        others = sorted({codes[j] for j in nbrs[k]})
        admin = {c: (ea.at[idx, f"ea_{c}"] if f"ea_{c}" in ea.columns else ea.at[idx, c] if c in ea.columns else "")
                 for c in ADMIN}
        rows.append({
            "Issue": VERTEX_ISSUE,
            "EA Geocode": codes[k],
            **admin,
            "EA": "" if pd.isna(ea.at[idx, "EA"]) else str(ea.at[idx, "EA"]) if "EA" in ea.columns else "",
            "Area (sqm)": 0.0,
            "Fix EA": codes[k],
            "Status": STATUS_PENDING,
            "Note": f"{len(pts)} vertex(es) without their partner on a neighbouring {unit} "
                    f"({', '.join(others[:5])}{'...' if len(others) > 5 else ''})"
                    + (", off its reference line (up to 5 cm) or missing a vertex of that line" if own is not None
                       else "") + ", or duplicated: Snap puts them in place (a vertex on its reference line "
                    "stays put)",
            "_ea_idx": int(idx),
            "_snappable": True,
            "_kind": "vertex",
            "_candidates": [int(idx)],
            "geometry": marks,
        })
    if not rows:
        return gpd.GeoDataFrame({c: [] for c in cols}, geometry=[], crs=DATA_CRS)
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=DATA_CRS)


def match_vertices(scan):
    """Give every pair of neighbouring features of the scanned layer(s) the same vertices along their shared
    edges (share_vertices on all of them): a vertex of one within SHARE_TOL_M of the other's edge is put
    into that edge too. One edit command per layer, recorded like a snap so Undo Last Snap takes it back.
    Returns the number of features changed."""
    from qgis.core import QgsProject

    use_crs(scan.crs)
    ea = scan.ea
    total, undo_layers, undo_geoms = 0, [], {}
    for layer_id in dict.fromkeys(ea["src_layer"]):
        layer = QgsProject.instance().mapLayer(layer_id)
        if layer is None or (not layer.isEditable() and not layer.startEditing()):
            continue
        command = "Match vertices along shared edges"
        layer.beginEditCommand(command)
        try:
            n = share_vertices(scan, layer, list(ea.index[ea["src_layer"] == layer_id]), undo_geoms)
        finally:
            layer.endEditCommand()
            layer.triggerRepaint()
        if n:
            total += n
            undo_layers.append((layer_id, command, layer.undoStack().index()))
    if undo_layers:
        scan.history.append({"layers": undo_layers, "rows": {}, "geoms": undo_geoms})
    return total


def snap_rows(scan, rows):
    """Snap the selected discrepancy rows. Returns {row: (status, message)} for the rows it handled.

    Rows are grouped by the feature they change, so each feature is edited once, in the layer it came
    from. The changes go to each layer's edit buffer as one undo step per layer; nothing is saved.
    """
    from qgis.core import QgsProject, QgsWkbTypes

    use_crs(scan.crs)
    d, ea, unit = scan.discrepancies, scan.ea, scan.unit
    Unit = unit[0].upper() + unit[1:]
    outcome, todo = {}, {}
    scan.needs_multi = {}
    for r in rows:
        status = d.at[r, "Status"]
        if status == STATUS_DONE:
            continue
        if not bool(d.at[r, "_snappable"]):
            outcome[r] = (STATUS_MANUAL, f"Not snapped: {d.at[r, 'Note']}")
        else:
            todo.setdefault(int(d.at[r, "_ea_idx"]), []).append(r)

    edits = {}  # layer id -> the edits to make in that layer
    vertex_todo = {}  # layer id -> [(feature, its Unmatched vertices rows)]
    undo_layers, undo_geoms = [], {}  # for undo_last_snap: (layer id, edit command), feature -> old geometry
    for idx, ea_rows in todo.items():
        code = ea.at[idx, "EA Geocode"]

        def fail(message):
            outcome.update({r: (STATUS_FAILED, f"{Unit} {code} not changed: {message}") for r in ea_rows})

        layer_id = ea.at[idx, "src_layer"]
        ea_layer = QgsProject.instance().mapLayer(layer_id)
        if ea_layer is None:
            fail("its layer was removed from the project. Scan again")
            continue
        fid = int(ea.at[idx, "src_fid"])
        feature = ea_layer.getFeature(fid)
        if not feature.isValid():
            fail("it is no longer in the layer")
            continue
        if geometry_hash(feature.geometry()) != ea.at[idx, "src_hash"]:
            fail("it was edited after the scan. Scan again")
            continue

        vtx_rows = [r for r in ea_rows if d.at[r, "_kind"] == "vertex"]
        if vtx_rows:  # Unmatched vertices: matched in the layer's edit command below (share_vertices)
            vertex_todo.setdefault(layer_id, []).append((idx, vtx_rows))
            ea_rows = [r for r in ea_rows if d.at[r, "_kind"] != "vertex"]
            if not ea_rows:
                continue
        cut_rows = [r for r in ea_rows if d.at[r, "_kind"] == "cut"]
        add_rows = [r for r in ea_rows if d.at[r, "_kind"] == "add"]
        old = ea.geometry[idx]
        cuts, adds, clip = snap_inputs(scan, idx, ea_rows)
        new, why = snapped_geometry(old, cuts, adds, scan.min_area, clip=clip)
        if new is None:
            fail(f"the result would be {why}")
            continue

        old_area, new_area = areas_sqm([old, new])
        expected = old_area - d.loc[cut_rows, "Area (sqm)"].sum() + d.loc[add_rows, "Area (sqm)"].sum()
        tolerance = max(2 * scan.min_area * len(ea_rows), 0.005 * old_area, 1.0)
        if abs(new_area - expected) > tolerance:
            fail(f"it would become {new_area:,.0f} sqm instead of about {expected:,.0f} sqm")
            continue

        parts = shapely.get_num_geometries(new)
        if parts > 1 and not QgsWkbTypes.isMultiType(ea_layer.wkbType()):
            fail(f"it would have {parts} parts, but '{ea_layer.name()}' is a single-part Polygon layer. "
                 f"Snap again and answer Yes to make a multipart copy of the layer")
            scan.needs_multi.setdefault(layer_id, []).extend(ea_rows)
            continue

        written = []
        geom, why = to_layer_geometry(new, ea_layer, feature.geometry(), written)
        if written:
            new = written[0]  # what the layer holds (with the extra vertices if long edges needed them)
        if geom is None:
            fail(why)
            continue
        edits.setdefault(layer_id, []).append((idx, ea_rows, fid, geom, new, old_area, new_area))

    what = "overlaps / gaps" if scan.self_check else f"{MODES[scan.mode]['ref_unit']} boundaries"
    try:
        for layer_id in dict.fromkeys([*edits, *vertex_todo]):
            layer_edits, vtx = edits.get(layer_id, []), vertex_todo.get(layer_id, [])
            ea_layer = QgsProject.instance().mapLayer(layer_id)
            if not ea_layer.isEditable() and not ea_layer.startEditing():
                for _, ea_rows, *_ in [*layer_edits, *vtx]:
                    outcome.update({r: (STATUS_FAILED, f"'{ea_layer.name()}' cannot be edited") for r in ea_rows})
                continue
            command = f"Snap {len(layer_edits)} {unit}(s) to {what}" if layer_edits else                 f"Match the vertices of {len(vtx)} {unit}(s)"
            ea_layer.beginEditCommand(command)
            changed = False
            try:
                for idx, ea_rows, fid, geom, new, old_area, new_area in layer_edits:
                    code = ea.at[idx, "EA Geocode"]
                    old_geom = ea.at[idx, "geometry"]
                    if ea_layer.changeGeometry(fid, geom):
                        ea.at[idx, "geometry"] = new
                        ea.at[idx, "src_hash"] = geometry_hash(ea_layer.getFeature(fid).geometry())
                        msg = f"{Unit} {code} snapped: {old_area:,.0f} -> {new_area:,.0f} sqm"
                        outcome.update({r: (STATUS_DONE, msg) for r in ea_rows})
                        undo_geoms[idx] = old_geom
                        changed = True
                    else:
                        outcome.update({r: (STATUS_FAILED, f"{Unit} {code} not changed: the layer refused the edit")
                                        for r in ea_rows})
                snapped = [idx for idx, *_ in layer_edits if idx in undo_geoms] + [idx for idx, _ in vtx]
                if snapped and share_vertices(scan, ea_layer, snapped, undo_geoms):
                    changed = True
                if vtx:  # each Unmatched vertices row: done only if nothing is left to match on its feature
                    geoms = list(ea.geometry.values)
                    nbrs = neighbour_lists(geoms)
                    pos = {i: k for k, i in enumerate(ea.index)}
                    for idx, rs in vtx:
                        own = scan.ref.get(ea.at[idx, "key"]) if scan.ref and "key" in ea.columns else None
                        left = len(vertex_problems(geoms, pos[idx], nbrs[pos[idx]], own,
                                                   all_ref_lines(scan.ref) if scan.ref else None))
                        code = ea.at[idx, "EA Geocode"]
                        outcome.update({r: (STATUS_DONE, f"{Unit} {code}: vertices matched") if not left else
                                        (STATUS_FAILED, f"{Unit} {code}: {left} vertex(es) could not be matched: "
                                         "more than 1 cm off, the edit would make it invalid, or (checked against "
                                         "barangays) both vertices lie on their own barangay lines, which differ "
                                         "there by a hair. Match them in EA vs EA (no barangay line is kept "
                                         "there), or by hand") for r in rs})
            finally:
                ea_layer.endEditCommand()  # never leave the layer inside an open edit command
                ea_layer.triggerRepaint()
            if changed:
                # the stack position after the snap: Undo Last Snap needs the layer to still be exactly here
                undo_layers.append((layer_id, command, ea_layer.undoStack().index()))
    finally:
        # Record what was done even if a later layer failed, so the finished layers can still be undone.
        done_rows = {r: (d.at[r, "Status"], scan.messages.get(r))
                     for r, (s, _) in outcome.items() if s == STATUS_DONE}
        for r, (status, message) in outcome.items():
            d.at[r, "Status"] = status
            scan.messages[r] = message
        _set_layer_values(scan, list(outcome), ["Status"])
        if undo_layers:
            scan.history.append({"layers": undo_layers, "rows": done_rows, "geoms": undo_geoms})
    return outcome


def make_multipart_copy(scan, layer_id):
    """A single-part polygon layer cannot hold an EA with an island. Save a MultiPolygon copy of it next to
    the original (<name>_multi.gpkg; in memory if the layer has no file), add it to the project above the
    original with the same style, hide the original (its file is not changed; its unsaved edits move to the
    copy) and point the scan's features at the copy. Returns (new layer, None) or (None, why)."""
    from qgis.core import (QgsFeatureRequest, QgsMapLayerStyle, QgsProject, QgsProviderRegistry,
                           QgsVectorFileWriter, QgsVectorLayer)

    prj = QgsProject.instance()
    old = prj.mapLayer(layer_id)
    if old is None:
        return None, "the layer was removed from the project"

    name = f"{old.name()} (multipart)"
    path = QgsProviderRegistry.instance().decodeUri(old.providerType(), old.source()).get("path") or ""
    if path and os.path.isfile(path):
        stem = os.path.splitext(os.path.basename(path))[0]
        out, n = os.path.join(os.path.dirname(path), f"{stem}_multi.gpkg"), 2
        while os.path.exists(out):
            out, n = os.path.join(os.path.dirname(path), f"{stem}_multi_{n}.gpkg"), n + 1
        opts = QgsVectorFileWriter.SaveVectorOptions()
        opts.driverName = "GPKG"
        opts.layerName = re.sub(r"\W+", "_", stem).strip("_") or "layer"
        opts.forceMulti = True
        res = QgsVectorFileWriter.writeAsVectorFormatV3(old, out, prj.transformContext(), opts)
        if res[0] != QgsVectorFileWriter.NoError:
            return None, f"could not write {out}: {res[1]}"
        new = QgsVectorLayer(f"{out}|layername={opts.layerName}", name, "ogr")
    else:
        new = QgsVectorLayer(f"MultiPolygon?crs={old.crs().authid()}", name, "memory")
        new.dataProvider().addAttributes(old.fields().toList())
        new.updateFields()
        feats = []
        for f in old.getFeatures(QgsFeatureRequest().addOrderBy("$id")):
            g = f.geometry()
            g.convertToMultiType()
            f.setGeometry(g)
            feats.append(f)
        new.dataProvider().addFeatures(feats)
    if not new.isValid():
        return None, "the multipart copy could not be opened"

    # Same order in both (features are written in id order): pair them, checking each pair's area.
    old_feats = sorted(old.getFeatures(), key=lambda f: f.id())
    new_feats = sorted(new.getFeatures(), key=lambda f: f.id())
    if len(old_feats) != len(new_feats) or any(
            abs(a.geometry().area() - b.geometry().area()) > 1e-6 * max(a.geometry().area(), 1e-12)
            for a, b in zip(old_feats, new_feats)):
        return None, "the multipart copy does not match the original feature for feature"
    fid_map = {a.id(): b for a, b in zip(old_feats, new_feats)}

    # The copy already holds the original's unsaved edits (e.g. earlier snaps): drop them from the original
    # so it stays as saved, and forget the snaps made there (they can no longer be undone from here).
    if old.isEditable():
        old.rollBack()
    if any(lid == layer_id for e in scan.history for lid, *_ in e["layers"]):
        scan.history.clear()

    style = QgsMapLayerStyle()
    style.readFromLayer(old)
    style.writeToLayer(new)
    prj.addMapLayer(new, False)
    root = prj.layerTreeRoot()
    node = root.findLayer(old.id())
    parent = node.parent() if node is not None else root
    parent.insertLayer(parent.children().index(node) if node is not None else 0, new)
    if node is not None:
        node.setItemVisibilityChecked(False)

    ea = scan.ea
    for idx in ea.index[ea["src_layer"] == layer_id]:
        f = fid_map[int(ea.at[idx, "src_fid"])]
        ea.at[idx, "src_layer"] = new.id()
        ea.at[idx, "src_fid"] = int(f.id())
        ea.at[idx, "src_hash"] = geometry_hash(f.geometry())
    return new, None


def undo_last_snap(scan):
    """Take back the last Snap Selected: undoes its edit command in each layer, puts the features'
    geometries and the rows' statuses back to what they were before it. Returns (rows restored, None) or
    (None, why it cannot be undone).

    Refused, with nothing changed, if a layer's undo stack is not exactly where the snap left it (another
    edit, or an Edit > Undo, was made afterwards). If a layer was removed or its edits were saved /
    discarded, the snap can never be undone from here, so the history is dropped."""
    from qgis.core import QgsProject

    if not scan.history:
        return None, "There is no snap to undo."
    entry = scan.history[-1]
    layers = []
    for layer_id, command, position in entry["layers"]:
        layer = QgsProject.instance().mapLayer(layer_id)
        if layer is None or not layer.isEditable():
            scan.history.clear()
            why = "was removed from the project" if layer is None else "had its edits saved or discarded"
            return None, ("A layer that was snapped " + why + ", so the snaps can no longer be undone here. "
                          "The rows keep their status.")
        stack = layer.undoStack()
        if stack.index() != position or stack.undoText() != command:
            return None, (f"'{layer.name()}' was edited, or Edit > Undo was used, after the snap. "
                          "Put it back as the snap left it first, or use Edit > Undo for that layer.")
        layers.append(layer)
    for layer in layers:
        layer.undoStack().undo()
        layer.triggerRepaint()

    d, ea = scan.discrepancies, scan.ea
    for idx, geom in entry["geoms"].items():
        layer = QgsProject.instance().mapLayer(ea.at[idx, "src_layer"])
        ea.at[idx, "geometry"] = geom
        ea.at[idx, "src_hash"] = geometry_hash(layer.getFeature(int(ea.at[idx, "src_fid"])).geometry())
    for r, (status, message) in entry["rows"].items():
        d.at[r, "Status"] = status
        if message is None:
            scan.messages.pop(r, None)
        else:
            scan.messages[r] = message
    _set_layer_values(scan, list(entry["rows"]), ["Status"])
    scan.history.pop()
    return list(entry["rows"]), None


# ================= QGIS OUTPUT =================
ISSUE_COLOURS = mode_issues("ea_bgy", False)  # EA vs BGY (kept for older callers)


def categorize(lyr, field, colours):
    from qgis.core import QgsCategorizedSymbolRenderer, QgsFillSymbol, QgsRendererCategory

    def fill(color):
        return QgsFillSymbol.createSimple({"color": color, "outline_color": "35,35,35,255", "outline_width": "0.2"})

    lyr.setRenderer(QgsCategorizedSymbolRenderer(
        field, [QgsRendererCategory(value, fill(color), value) for value, color in colours]))


def to_memory_layer(df, name, colours=None):
    """GeoDataFrame -> styled MultiPolygon scratch layer (columns starting with "_" are left out)."""
    from qgis.PyQt.QtCore import QVariant
    from qgis.core import QgsFeature, QgsField, QgsGeometry, QgsVectorLayer

    lyr = QgsVectorLayer(f"MultiPolygon?crs={DATA_CRS}", name, "memory")
    attrs = df.drop(columns=["geometry"] + [c for c in df.columns if str(c).startswith("_")]).copy()
    numeric = [c for c in attrs.columns if "(sqm)" in c]
    attrs[numeric] = attrs[numeric].astype(float).round(2)

    lyr.dataProvider().addAttributes([
        QgsField(col, QVariant.Double if col in numeric else QVariant.String) for col in attrs.columns])
    lyr.updateFields()

    values = attrs.astype(object).where(attrs.notna(), None).values.tolist()
    wkbs = shapely.to_wkb(shapely.force_2d(df.geometry.values)).tolist()
    feats = []
    for row, wkb in zip(values, wkbs):
        f = QgsFeature(lyr.fields())
        f.setAttributes([v.item() if hasattr(v, "item") else v for v in row])  # numpy -> python
        g = QgsGeometry()
        g.fromWkb(wkb)
        g.convertToMultiType()
        f.setGeometry(g)
        feats.append(f)
    ok, added = lyr.dataProvider().addFeatures(feats)
    if not ok:
        raise RuntimeError(f"Could not write the features of '{name}'")
    lyr.updateExtents()
    categorize(lyr, "Issue", colours or ISSUE_COLOURS)
    return lyr, [f.id() for f in added]  # feature ids in the same order as the rows of df


RESULT_LAYER_TAG = "gmd_ea_vs_bgy/discrepancies"
RESULT_LAYER_NAME = re.compile(r"^Discrepancies \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


# ================= RUN =================
def _load_target(layer, mode):
    kind = MODES[mode]["target"]
    if kind == "bgy":
        return load_layer(layer, prepare_bgy_target, BGY_FIELDS["geocode"])
    if mode == "ea_bgy":
        return load_layer(layer, prepare_ea, EA_FIELDS["bgy_geocode"] + EA_FIELDS["ea_geocode"])
    return load_layer(layer, lambda g: prepare_ea(g, key="ea"), EA_FIELDS["ea_geocode"])


def _load_reference(layer, mode):
    if MODES[mode]["ref"] == "bgy":
        return merge_duplicate_barangays(load_layer(layer, prepare_bgy, BGY_FIELDS["geocode"]))
    return merge_duplicate_barangays(load_layer(layer, prepare_ea_ref, EA_FIELDS["ea_geocode"]), "EA")


def _load_targets(layers, mode, step):
    """All target layers as one frame; src_layer is the id of the layer each feature came from."""
    parts = []
    for n, layer in enumerate(layers, 1):
        step(f"Loading {MODES[mode]['unit']} layer {n} of {len(layers)}: {layer.name()}...")
        parts.append(_load_target(layer, mode).assign(src_layer=layer.id()))
    return gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), geometry="geometry", crs=DATA_CRS)


def _load_references(layers, mode, step):
    """All reference layers as one frame, one feature per geocode (also across layers)."""
    parts = []
    for n, layer in enumerate(layers, 1):
        step(f"Loading {MODES[mode]['ref_unit']} layer {n} of {len(layers)}: {layer.name()}...")
        parts.append(_load_reference(layer, mode))
    merged = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), geometry="geometry", crs=DATA_CRS)
    return merge_duplicate_barangays(merged, "barangay" if MODES[mode]["ref"] == "bgy" else "EA")


def run_check(bgy_layers, ea_layers, min_area, step=print, mode="ea_bgy"):
    """Finds the discrepancies and returns a Scan (its layer is kept in memory, not added to the project).

    bgy_layers are the fixed reference, ea_layers the targets that get snapped (a single layer is also
    accepted for either). Each side's layers are scanned together, so discrepancies between two layers
    are found too. In BGY vs BGY and EA vs EA the reference is optional: without it (empty / None) the
    targets are checked against each other for overlaps and gaps. Nothing is changed: use snap_rows() on
    the discrepancies to fix.
    """
    as_list = lambda x: [] if x is None else list(x) if isinstance(x, (list, tuple)) else [x]
    bgy_layers, ea_layers = as_list(bgy_layers), as_list(ea_layers)
    m = MODES[mode]
    unit = m["unit"]
    WARNINGS.clear()
    self_check = not bgy_layers
    if self_check and m["ref_required"]:
        raise ValueError(f"{m['label']} needs a {m['ref_unit']} layer")
    if not ea_layers:
        raise ValueError(f"No {unit} layer to check")
    use_crs(choose_work_crs(ea_layers + bgy_layers))
    step(f"Working CRS: {DATA_CRS} (edges straight as the map draws them)")
    ea = _load_targets(ea_layers, mode, step)
    if self_check:
        step(f"Checking the {unit}s against each other (overlaps and gaps)...")
        discrepancies = find_self_discrepancies(ea, min_area, mode)
    else:
        bgy = _load_references(bgy_layers, mode, step)
        step(f"Comparing {unit}s to the {m['ref_unit']}s...")
        discrepancies, no_ea = find_discrepancies(ea, bgy, min_area, mode)
        warn_bgy_overlaps(bgy, min_area, f"{m['ref_unit']}s")
        if len(no_ea):
            names = [f"{row['Label']} ({row['Geocode']})" for _, row in no_ea.head(10).iterrows()]
            more = f" ... and {len(no_ea) - 10:,} more" if len(no_ea) > 10 else ""
            warn(f"{len(no_ea):,} {m['ref_unit']}(s) have no {unit}, so they are not in the output: "
                 f"{', '.join(names)}{more}")

    step(f"Checking the vertices along shared edges of the {unit}s...")
    ref = {} if self_check else {k: g for k, g in zip(bgy["key"], bgy.geometry.values) if g is not None}
    vertex_rows = find_vertex_issues(ea, unit, ref)
    if len(vertex_rows):
        discrepancies = gpd.GeoDataFrame(pd.concat([discrepancies, vertex_rows], ignore_index=True),
                                         geometry="geometry", crs=DATA_CRS) if len(discrepancies) else \
            vertex_rows.reset_index(drop=True)

    layer, fids = None, []
    if len(discrepancies):
        step("Creating the output layer...")
        layer, fids = to_memory_layer(discrepancies, f"Discrepancies {datetime.now():%Y-%m-%d %H:%M:%S}",
                                      mode_issues(mode, self_check))
    scan = Scan(ea, discrepancies, layer, fids, min_area, mode, self_check)
    scan.crs = DATA_CRS
    if not self_check:
        scan.ref = {k: g for k, g in zip(bgy["key"], bgy.geometry.values) if g is not None}
    return scan


# ================= RESULT TEXT =================
def result_message(discrepancies, include_warnings=True, scan=None):
    if len(discrepancies):
        counts = discrepancies["Issue"].value_counts()
        msg = f"Discrepancies found: {len(discrepancies):,}\n"
        msg += "\n".join(f"  {k}: {v:,}" for k, v in counts.items())
        manual = int((discrepancies["Status"] == STATUS_MANUAL).sum())
        if manual:
            msg += f"\n  ({manual:,} must be fixed by hand)"
        msg += "\n\nTo fix: select rows in the list and press Snap Selected."
    elif scan is not None and scan.self_check:
        msg = f"No overlaps or gaps between the {scan.unit}s."
    elif scan is not None:
        msg = f"All {scan.unit}s are snapped to their {MODES[scan.mode]['ref_unit']} boundaries."
    else:
        msg = "All EAs are snapped to their barangay boundaries."
    if include_warnings and WARNINGS:
        msg += "\n\nWarnings:\n" + "\n".join(WARNINGS)
    return msg


# ================= WINDOW (UI) =================
# Same look as the MBI EA Selector: title, tabs (tool + Help and Information), inputs on the left,
# results / actions / log on the right, light and dark themes.
DIALOG_NAME = "EAvsBarangayCheckDialog"
DIALOG_TITLE = "EA vs Barangay Check"
DIALOG_SUBTITLE = ("Check EA and barangay boundaries (EA vs BGY, BGY vs BGY or EA vs EA), "
                   "then snap the features that do not match.")
TABLE_COLUMNS = ["Issue", "Status", "EA Geocode", "Fix EA", "EA", "Barangay", "City/Municipality", "Area (sqm)", "Note"]
SEARCH_COLUMNS = ["EA Geocode", "Fix EA", "EA", "Barangay", "City/Municipality", "Note"]
# Column headers shown per kind of target layer (the data columns keep their names).
HEADER_LABELS = {"ea": {}, "bgy": {"EA Geocode": "BGY Geocode", "Fix EA": "Fix BGY", "EA": "Name"}}
STATUS_COLOURS = {STATUS_DONE: "#2e7d32", STATUS_FAILED: "#c62828", STATUS_MANUAL: "#757575"}
GRP_STYLE = ("QGroupBox { font-weight:600; margin-top:6px; } "
             "QGroupBox::title { subcontrol-origin:margin; left:8px; }")
LEFT_WIDTH = 315
# Map highlight on double-click / Zoom to Selected: dotted outline + circle on the discrepancy (by what a
# snap does) and a dotted green outline of the Fix feature as it will be after Snap Selected.
HIGHLIGHT_COLOURS = {"cut": "#ff6d00", "add": "#d50000", "none": "#616161", "after": "#00c853", "vertex": "#00acc1"}
BLINKS = 6           # on/off steps of 300 ms, then the outlines stay drawn
MAX_PREVIEWS = 50    # most "after snap" outlines drawn at once
MIN_ZOOM_M = 55.0  # 55 m: zooming to a tiny piece (e.g. 1 sqm) still shows its surroundings

NO_RESULTS = "No scan yet. Choose a check mode and tick the layers on the left, then click Scan."

# Per mode: titles of the two layer boxes, and what the mode does (shown under the radio buttons).
MODE_TEXT = {
    "ea_bgy": {"target": "EA Layers (snapped)", "ref": "Barangay Layers (fixed reference)",
               "hint": "Each EA is compared to its barangay; the EA is snapped to the barangay boundary."},
    "bgy_bgy": {"target": "Barangay Layers (checked and snapped)", "ref": "Reference Barangay Layers (optional)",
                "hint": "With a reference: each barangay is snapped to the reference barangay with the same "
                        "geocode. Without: the barangays are checked against each other for overlaps and gaps."},
    "ea_ea": {"target": "EA Layers (checked and snapped)", "ref": "Reference EA Layers (optional)",
              "hint": "With a reference: each EA is snapped to the reference EA with the same ea_geocode. "
                      "Without: the EAs are checked against each other for overlaps and gaps."},
}

# Light / dark palettes (same values as the MBI EA Selector)
THEMES = {
    False: {
        "title": "#2C3E50", "sub": "#7F8C8D", "line": "#BDC3C7", "hint": "#455a64",
        "summary": "#37474f",
        "log": ("background: #FFFFFF; color: #1F2937; font-family: Consolas, monospace; "
                "font-size: 11px; border: 1px solid #BDC3C7;"),
        "table": "",
        "list": """
            QListWidget { border: 1px solid #b0bec5; outline: 0; }
            QListWidget::item { padding: 3px 4px; }
            QListWidget::indicator:checked { background-color: #1565c0; border: 1px solid #1565c0; }
        """,
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
        "table": """
            QTableWidget { background-color: #263238; alternate-background-color: #1E2327;
                           color: #ECEFF1; selection-background-color: #1976D2;
                           selection-color: white; gridline-color: #37474F; }
            QHeaderView::section { background-color: #37474F; color: #ECEFF1;
                                   border: 1px solid #455A64; padding: 4px; }
            QTableCornerButton::section { background-color: #37474F; }
        """,
        "list": """
            QListWidget { border: 1px solid #455A64; border-radius: 4px; outline: 0;
                          background-color: #263238; alternate-background-color: #1E2327; color: #ECEFF1; }
            QListWidget::item { padding: 3px 4px; }
            QListWidget::item:hover { background-color: #37474F; }
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

_ROW = '<tr{bg}><td style="border:1px solid #e5e7eb;"><b>{a}</b></td><td style="border:1px solid #e5e7eb;">{b}</td></tr>'


def _help_table(rows):
    body = "".join(_ROW.format(bg=' style="background:#f9fafb;"' if i % 2 == 0 else "", a=a, b=b)
                   for i, (a, b) in enumerate(rows))
    return f'<table cellspacing="0" cellpadding="4" style="border-collapse:collapse; width:100%;">{body}</table>'


DUPGEOM_HELP_HTML = """
<div style="font-family:'Segoe UI',Arial,sans-serif; color:#1f2937; font-size:12px; line-height:1.5;">
  <div style="border-bottom:2px solid #374151; padding-bottom:8px; margin-bottom:14px;">
    <h2 style="margin:0 0 4px 0; color:#111827; font-size:17px;">Duplicate Geometry Fixer</h2>
    <div style="color:#4b5563;">Scan one or more polygon layers for duplicate geometry parts and remove them</div>
  </div>

  <h3 style="color:#111827; margin:12px 0 4px 0;">What it fixes</h3>
  <p style="margin:0 0 8px 0;">
    A <b>MultiPolygon</b> feature can carry two or more identical polygon rings — the same outline
    drawn twice and merged into one feature. QGIS Geometry Checker reports these as
    <i>"duplicate geometry"</i> errors. This tool removes the extra copies so each feature
    keeps only one instance of each distinct ring.
  </p>

  <h3 style="color:#111827; margin:12px 0 4px 0;">Quick Start</h3>
  <ol style="margin-top:0;">
    <li>Tick one or more polygon layers in the list on the left.</li>
    <li>Click <b>Scan</b> to find features with duplicate parts. The table lists every affected feature
        with a count of parts before and after deduplication.</li>
    <li>Review the list. Click a row to flash the feature on the map; double-click to zoom to it.</li>
    <li>Click <b>Fix All</b> to remove the duplicate parts from every listed feature, or select rows
        and click <b>Fix Selected</b> to fix only those.</li>
    <li>The layers are left in <b>edit mode</b>. Save with <b>Save Layer Edits</b> or
        <b>Layer &gt; Current Edits &gt; Save for All Layers</b>.</li>
  </ol>

  <h3 style="color:#111827; margin:12px 0 4px 0;">Notes</h3>
  <ul style="margin-top:0;">
    <li>Only <b>exact duplicates</b> (identical WKT) within the same feature are removed. Near-duplicate
        or overlapping-but-different parts are not touched.</li>
    <li>Single-part Polygon features are always skipped (they cannot have duplicate parts).</li>
    <li>The fix writes only geometry, never attributes.</li>
    <li>Each fix is one undoable edit step per layer (<b>Edit &gt; Undo</b>).</li>
  </ul>
</div>
"""

HELP_HTML = f"""
<div style="font-family:'Segoe UI',Arial,sans-serif; color:#1f2937; font-size:12px; line-height:1.5;">
  <div style="border-bottom:2px solid #374151; padding-bottom:8px; margin-bottom:14px;">
    <h2 style="margin:0 0 4px 0; color:#111827; font-size:17px;">EA vs Barangay Check</h2>
    <div style="color:#4b5563;">Find where EA or barangay boundaries do not match, then snap them</div>
  </div>

  <h3 style="color:#111827; margin:12px 0 4px 0;">Quick Start</h3>
  <ol style="margin-top:0;">
    <li>Load the layers into the project. To check only part of a layer, set a filter on it first
        (right-click the layer &gt; Filter).</li>
    <li>Under <b>Check Mode</b>, choose <b>EA vs BGY</b>, <b>BGY vs BGY</b> or <b>EA vs EA</b>.</li>
    <li>Tick the layers to check (they are the ones that get snapped) and, if the mode uses one, the reference
        layers (fixed, never changed). For a whole province, load its cities / municipalities and press
        <b>Select All</b> in each list. All ticked layers are scanned together.</li>
    <li>Click <b>Scan</b>. Progress and warnings appear in the Log.</li>
    <li>Review the <b>Discrepancies</b> list: click a row to flash it on the map, double-click to zoom. The zoom
        blinks a <b>dotted outline</b> on the discrepancy, with a <b>circle on each vertex of the Fix feature that is out of
        place</b> (not yet on its neighbour's edge), in orange = cut off, red = added, grey = fix by hand, and a
        <b>dotted green outline</b> of the Fix feature as it will be after <b>Snap Selected</b>, so even a
        1 sqm piece is easy to see and check before snapping. <b>Zoom to Selected</b> does the same for
        several rows. Untick <b>Blinking dotted outline and snap preview on zoom</b> (Snap panel) for the plain
        red flash only.</li>
    <li>Select the rows to fix (Ctrl/Shift+click for several, or <b>Select Not Snapped</b>) and click
        <b>Snap Selected</b>.</li>
    <li>Check the result on the map, then keep it with <b>Save Layer Edits</b>, or for several layers
        <b>Layer &gt; Current Edits &gt; Save for All Layers</b>.</li>
  </ol>

  <h3 style="color:#111827; margin:12px 0 4px 0;">Check modes</h3>
  {_help_table([
      ("EA vs BGY", "Each EA is compared to its barangay (matched by <code>bgy_geocode</code>, else the first 8 "
                    "digits of <code>ea_geocode</code>). The barangay layer is the fixed reference; the EAs are "
                    "snapped to it. A barangay layer is required."),
      ("BGY vs BGY", "<b>With a reference barangay layer:</b> each barangay is snapped to the reference barangay "
                     "with the same <code>geocode</code> (e.g. your barangays vs the official boundary).<br>"
                     "<b>Without a reference</b> (leave it empty): the barangays are checked against each other "
                     "for overlaps and gaps."),
      ("EA vs EA", "<b>With a reference EA layer:</b> each EA is snapped to the reference EA with the same "
                   "<code>ea_geocode</code> (e.g. an updated EA layer vs the previous one).<br>"
                   "<b>Without a reference</b> (leave it empty): the EAs are checked against each other for "
                   "overlaps and gaps."),
  ])}
  <p style="margin:6px 0 0 0;">Differences smaller than 0.1 sqm are treated as slivers and ignored.</p>

  <h3 style="color:#111827; margin:12px 0 4px 0;">Ticking layers</h3>
  <ul style="margin-top:0;">
    <li>Click a row to tick or untick it. <b>Select All</b> ticks every layer of the list's kind; <b>Clear</b>
        unticks the list.</li>
    <li>The ticked layers are scanned as one, so a self check also finds overlaps and gaps
        <i>between</i> two cities / municipalities.</li>
    <li>Grey rows: a layer without the needed geocode field cannot be ticked. One that looks like another kind
        (or an MBI result layer) can be ticked by hand, but <b>Select All</b> leaves it out.</li>
    <li>A layer ticked in one list is greyed out in the other. If only one layer fits a list, it is ticked
        for you.</li>
  </ul>

  <h3 style="color:#111827; margin:12px 0 4px 0;">Issues in the list</h3>
  {_help_table([
      ("Outside &hellip;", "The part of a feature outside its reference (<i>Outside BGY</i>, <i>Outside ref BGY</i>, "
                           "<i>Outside ref EA</i>). Snapping cuts it off."),
      ("Short of &hellip;", "Reference land the feature does not reach. Snapping adds it to the feature that shares "
                            "the longest border with it (all bordering features are named). A detached piece "
                            "no feature touches (e.g. an island) is <i>Fix manually</i>: pick the feature with "
                            "<b>Switch Fix</b> (nearest first) and snap to copy the piece into it (it becomes "
                            "multipart). If its layer is single-part, Snap offers to make a multipart copy "
                            "(<i>&lt;name&gt;_multi.gpkg</i> next to the original, which is hidden but not "
                            "changed) and snaps into that."),
      ("No matching &hellip;", "The feature's geocode is not in the reference layer. Fix it by hand."),
      ("Overlap", "<i>Self check.</i> Two features cover the same land. Snapping cuts it from the larger one; "
                  "use <b>Switch Fix</b> to cut it from the other one instead."),
      ("Gap", "<i>Self check.</i> Land enclosed by the features but in none of them. Snapping adds it to the "
              "feature with the longest border along it; <b>Switch Fix</b> picks the next bordering feature."),
  ])}
  <p style="margin:6px 0 0 0;">Each row names the feature to change in <b>Fix EA</b> / <b>Fix BGY</b>. Use the
     <b>Show</b> list to filter by issue or status, and the search box to find a name, geocode or barangay.
     A self check only finds gaps <i>enclosed</i> by the features; land beyond the layer's outer edge is not
     a gap.</p>

  <h3 style="color:#111827; margin:12px 0 4px 0;">Buttons</h3>
  {_help_table([
      ("Select Not Snapped", "Selects every listed row that can still be snapped."),
      ("Zoom to Selected", "Zooms the map to the selected discrepancies and blinks their dotted outlines, plus "
                           "the dotted green preview of each Fix feature after Snap Selected."),
      ("Switch Fix", "For overlaps and gaps (and shortfalls bordering several features): gives the selected row "
                     "to the next feature that could take it. Also lets you snap a row marked <i>Fix manually</i> "
                     "because no feature had most of its border, or a detached piece such as an island "
                     "(the features of its reference area, nearest first)."),
      ("Snap Selected", "Changes the features named in Fix for the selected rows. Unsaved, undoable edits."),
      ("Match Vertices", "Pairs the vertices along every shared edge of the checked layer: a vertex within 1 cm "
                         "of a neighbour's edge is moved onto it and added to the neighbour, so both hold the same "
                         "point. In EA vs BGY a vertex on its barangay line is never moved. <b>Undo Last Snap</b> "
                         "takes it back."),
      ("Undo Last Snap", "Takes back the last <b>Snap Selected</b>: the features return to their shape before it and "
                         "its rows to the status they had before it (normally <i>Not snapped</i>). Press again to "
                         "undo the snap before that. It is refused (nothing changes) if you edited those layers or "
                         "used Edit &gt; Undo afterwards; if their edits were saved or discarded, or a layer was "
                         "removed, the snaps can no longer be undone here."),
      ("Clear", "Clears the list and the log."),
  ])}

  <h3 style="color:#111827; margin:12px 0 4px 0;">Status column</h3>
  <ul style="margin-top:0;">
    <li><b>Not snapped</b> &ndash; ready to snap.</li>
    <li><b style="color:#2e7d32;">Snapped</b> &ndash; the feature was changed (unsaved edit).</li>
    <li><b style="color:#c62828;">Failed</b> &ndash; the feature was left untouched. Hover the cell for the reason.</li>
    <li><b style="color:#757575;">Fix manually</b> &ndash; cannot be snapped automatically.</li>
  </ul>

  <h3 style="color:#111827; margin:12px 0 4px 0;">Saving and undoing</h3>
  <ul style="margin-top:0;">
    <li>Each snap edits the feature's own layer. The changed layers are left in <b>edit mode</b>; nothing is
        written to their files until you save. The reference layers are never changed.</li>
    <li><b>Keep:</b> Save Layer Edits, or Layer &gt; Current Edits &gt; Save for All Layers.</li>
    <li><b>Revert:</b> Toggle Editing &gt; Discard, or Edit &gt; Undo (one undo step per layer per Snap
        Selected).</li>
    <li>Only the geometry changes; attributes such as household counts stay as they are.</li>
    <li>Before a feature is changed, the new shape is checked (valid, not empty, expected area, fits the layer)
        and the feature must not have been edited since the scan. It must also really fix what was selected
        (cut pieces gone, gaps covered), so a row marked Snapped does not come back on the next scan.
        Otherwise the row is marked Failed.</li>
    <li>After a snap, the snapped feature and its neighbours get the <b>same vertices</b> along their shared
        edge: a vertex of one within 1 cm of the other's edge is moved onto it (onto the neighbour's vertex if
        one is that close) and added to the neighbour too. Checked against a barangay layer, a vertex on its
        barangay line is never moved.</li>
    <li>Every scan also lists <b>Unmatched vertices</b> (teal): one row per feature that has a vertex within
        1 cm of a neighbour's edge with no partner vertex there, or two vertices within 2 mm (a duplicate).
        In EA vs BGY also: a vertex up to 5 cm off its own barangay line, or a barangay vertex missing from an
        EA edge along that line. These slivers are under 0.1 sqm, so they are not Outside / Short of BGY rows,
        but they show on the map when zoomed in; snapping puts the EA edge exactly on the barangay line.</li>
    <li>Snapping never leaves several vertices bunched at one intersection, a vertex out and straight back (a
        line), or a feature split into parts touching at a point (a bow tie): leftover slivers are taken whole,
        a vertex a few cm beside a barangay vertex on the same line joins it, and a repair that would split
        the feature is refused (the row is then Failed and the feature left as it was).
        Zoom to see circles on those vertices; <b>Snap Selected</b> matches them and marks the row Snapped
        once nothing is left. A vertex lying on its own barangay line is never moved in EA vs BGY: if both
        vertices of a pair lie on their own (slightly different) barangay lines, the row is Failed: match it in
        EA vs EA instead.</li>
    <li><b>Match Vertices</b> does the same for every shared edge of the checked layer, also where nothing was
        snapped (e.g. vertices already a hair off in the data). Vertices within 2 mm of each other become one
        point (no duplicate vertices or spikes are left). Undo Last Snap takes it back. Best run last,
        from an EA vs BGY scan, then Scan again to confirm.</li>
    <li>Checks and snaps run in a projected CRS in metres, so an edge is straight exactly as the map draws
        it: the layers' own CRS when projected, else the map's CRS, else the UTM zone of the data (the Log
        names it). A snapped edge then lies on the barangay line at any zoom, also along km-long edges.</li>
    <li>Recommended order: <b>EA vs EA</b> first, then <b>EA vs BGY</b>, then EA vs EA again to confirm. A long
        hairline strip may need a second Scan and Snap in EA vs BGY. A gap EA vs EA still finds after that
        is land in no barangay: check the barangay layer there.</li>
    <li>Features checked against each other: an overlap is cut along the kept feature's own outline and an
        added gap is clipped by the features around it, so the fixed edge lands exactly on the neighbour's
        edge. Every other vertex of the feature stays where it was.</li>
  </ul>

  <h3 style="color:#111827; margin:12px 0 4px 0;">Troubleshooting</h3>
  <ul style="margin-top:0;">
    <li><b>Scan is disabled:</b> tick at least one layer to check (and, for EA vs BGY, at least one barangay
        layer). The hint under each list says what is missing.</li>
    <li><b>Scan stops at one layer:</b> a ticked layer that is empty or has only blank geocodes stops the scan,
        so a result never silently misses a city / municipality. The Log names it: untick it or fix it.</li>
    <li><b>"No usable geocodes":</b> the layer has no geocode field. Barangays need <code>geocode</code> (or
        <code>bgy_geocode</code>); EAs need <code>ea_geocode</code> (EA vs BGY also accepts
        <code>bgy_geocode</code>).</li>
    <li><b>Nothing listed:</b> every difference is below the sliver size, or the boundaries already match.</li>
    <li><b>Reference features with no match</b> have nothing to list; they are named in the Log instead.</li>
    <li><b>A big Gap in a self check</b> may be land that is really outside the layer (e.g. a lake or a
        neighbouring town that is not loaded). Leave it, or filter it out with the search box.</li>
    <li><b>Edited the layer after scanning:</b> scan again before snapping, or the row is marked Failed.</li>
  </ul>
</div>
"""


class _LogStream:
    """print() target during a scan: sends each line to the window log."""

    def __init__(self, write_line):
        self._write_line = write_line
        self._buffer = ""

    def write(self, text):
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._write_line(line)

    def flush(self):
        if self._buffer:
            self._write_line(self._buffer)
            self._buffer = ""


def make_dialog_class():
    from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsFeatureRequest,
                           QgsGeometry, QgsProject, QgsRectangle, QgsVectorLayer, QgsWkbTypes)
    from qgis.PyQt import QtWidgets
    from qgis.PyQt.QtCore import QEvent, QSize, Qt, QTimer, pyqtSignal
    from qgis.PyQt.QtGui import QBrush, QColor, QFont

    FID_ROLE = Qt.UserRole          # on the Issue cell: feature id in the Discrepancies layer
    ROW_ROLE = Qt.UserRole + 1      # on the Issue cell: row in scan.discrepancies
    COL = {c: i for i, c in enumerate(TABLE_COLUMNS)}
    ISSUE_COL, STATUS_COL = COL["Issue"], COL["Status"]
    search_cols = [COL[c] for c in SEARCH_COLUMNS]
    snappable = {STATUS_PENDING, STATUS_FAILED}
    DARK_TEXT = QBrush(QColor("#212121"))
    LAYER_ID_ROLE = Qt.UserRole     # on a layer row: the layer's id
    LIKELY_ROLE = Qt.UserRole + 1   # on a layer row: True if Select All may tick it
    USABLE_ROLE = Qt.UserRole + 2   # on a layer row: True if it has the geocode field the role needs

    def qcolour(rgba, alpha=70):
        return QColor(*[int(v) for v in rgba.split(",")[:3]], alpha)

    def group(title=""):
        box = QtWidgets.QGroupBox(title)
        box.setStyleSheet(GRP_STYLE)
        return box, QtWidgets.QVBoxLayout(box)

    def button(text, height=32, icon=None):
        b = QtWidgets.QPushButton(text)
        if icon:
            b.setIcon(QgsApplication.getThemeIcon(icon))
        b.setFixedHeight(height)
        return b

    # Fields only an EA layer has; a barangay layer has a geocode but none of these.
    ea_only = {"ea_geocode", "ean", "bgy_geo"}
    # Fields of MBI / joined result layers: they have a geocode, but are not boundaries to check.
    result_only = {"case_uuid", "bgy_part"}
    bgy_geocode = {c.lower() for c in BGY_FIELDS["geocode"]}

    def needed_fields(mode, role):
        """Field names (any one of them) a layer needs in this role ('target' / 'ref') of the mode."""
        kind = MODES[mode][role]
        if kind == "bgy":
            return BGY_FIELDS["geocode"]
        if mode == "ea_bgy":
            return EA_FIELDS["bgy_geocode"] + EA_FIELDS["ea_geocode"]
        return EA_FIELDS["ea_geocode"]

    def layer_fields(layer):
        try:
            return {f.name().lower() for f in layer.fields()}
        except Exception:  # noqa: BLE001 - not a vector layer
            return set()

    def layer_scores(layer):
        """(EA score, barangay score) from the layer's fields, with the layer name as a tie-breaker."""
        fields = layer_fields(layer)
        if not fields:
            return 0, 0
        is_ea = bool(fields & ea_only) or {"bgy_geocode", "geocode"} <= fields
        name = layer.name()
        ea = (4 if is_ea else 0) + (1 if re.search(EA_LAYER_HINT, name, re.I) else 0)
        bgy = ((4 if fields & bgy_geocode and not is_ea else 0)
               + (1 if re.search(BGY_LAYER_HINT, name, re.I) else 0)
               + (1 if "barangay" in fields and not is_ea else 0))
        return ea, bgy

    def looks_like(layer, kind):
        """True if the layer's fields and name say it is a boundary layer of this kind ('ea' / 'bgy')."""
        if layer_fields(layer) & result_only:
            return False
        ea, bgy = layer_scores(layer)
        return ea > bgy if kind == "ea" else bgy > ea

    class CompactList(QtWidgets.QListWidget):
        """A list that asks for little height, so the left column fits without scrolling; it still
        stretches into any free room."""

        def sizeHint(self):
            return QSize(super().sizeHint().width(), 110)

    class LayerCheckDelegate(QtWidgets.QStyledItemDelegate):
        """Clicking anywhere on a layer row toggles its checkbox."""

        def editorEvent(self, event, model, option, index):
            if not (index.flags() & Qt.ItemIsUserCheckable) or not (index.flags() & Qt.ItemIsEnabled):
                return False
            if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
                current = index.data(Qt.CheckStateRole)
                model.setData(index, Qt.Unchecked if current == Qt.Checked else Qt.Checked, Qt.CheckStateRole)
                return True
            return False

    class LayerChecklist(QtWidgets.QWidget):
        """The project's polygon layers, one checkbox each, with Select All / Clear buttons.

        Layers without the geocode field the role needs are greyed out and cannot be ticked. Layers that
        look like another kind (e.g. a barangay layer in an EA list, or an MBI layer) can be ticked by hand,
        but Select All leaves them out. A layer ticked in the other list is greyed out: it cannot be in both.
        """
        changed = pyqtSignal()  # the user ticked or unticked something

        def __init__(self, tooltip):
            super().__init__()
            lay = QtWidgets.QVBoxLayout(self)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(4)
            row = QtWidgets.QHBoxLayout()
            self.all_btn = button("Select All", 24)
            self.all_btn.setToolTip("Tick every layer of this kind (e.g. all the cities / municipalities).")
            self.all_btn.clicked.connect(lambda: self._tick_all(True))
            self.none_btn = button("Clear", 24)
            self.none_btn.setToolTip("Untick every layer in this list.")
            self.none_btn.clicked.connect(lambda: self._tick_all(False))
            row.addWidget(self.all_btn)
            row.addWidget(self.none_btn)
            lay.addLayout(row)
            self.list = CompactList()
            self.list.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
            self.list.setAlternatingRowColors(True)
            self.list.setToolTip(tooltip)
            self.list.setItemDelegate(LayerCheckDelegate(self.list))
            self.list.setMinimumHeight(110)
            self.list.itemChanged.connect(lambda _item: self.changed.emit())
            lay.addWidget(self.list, 1)

        def items(self):
            return [self.list.item(i) for i in range(self.list.count())]

        def checked_ids(self):
            return {i.data(LAYER_ID_ROLE) for i in self.items() if i.checkState() == Qt.Checked}

        def layers(self):
            """The ticked layers, in list order."""
            prj = QgsProject.instance()
            found = (prj.mapLayer(i.data(LAYER_ID_ROLE)) for i in self.items() if i.checkState() == Qt.Checked)
            return [lyr for lyr in found if lyr is not None]

        def likely_ids(self):
            return [i.data(LAYER_ID_ROLE) for i in self.items() if i.data(LIKELY_ROLE)]

        def fill(self, mode, role):
            """List the project's polygon layers for this role of the mode, keeping the ticks still valid."""
            checked = self.checked_ids()
            needed = needed_fields(mode, role)
            kind = MODES[mode][role]
            layers = [lyr for lyr in QgsProject.instance().mapLayers().values()
                      if isinstance(lyr, QgsVectorLayer) and lyr.geometryType() == QgsWkbTypes.PolygonGeometry]
            rows = [(lyr, bool(layer_fields(lyr) & {f.lower() for f in needed})) for lyr in layers]
            rows = [(lyr, usable, usable and looks_like(lyr, kind)) for lyr, usable in rows]
            rows.sort(key=lambda r: (not r[2], not r[1], r[0].name().lower()))  # likely layers first
            self.list.blockSignals(True)
            self.list.clear()
            for lyr, usable, likely in rows:
                item = QtWidgets.QListWidgetItem(lyr.name())
                item.setData(LAYER_ID_ROLE, lyr.id())
                item.setData(LIKELY_ROLE, likely)
                item.setData(USABLE_ROLE, usable)
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable if usable else Qt.NoItemFlags)
                item.setCheckState(Qt.Checked if usable and lyr.id() in checked else Qt.Unchecked)
                if not usable:
                    item.setToolTip(f"No geocode field. Expected one of: {', '.join(needed)}.")
                elif not likely:
                    item.setToolTip(f"Does not look like {'a barangay' if kind == 'bgy' else 'an EA'} boundary "
                                    "layer: Select All leaves it out, but you can tick it by hand.")
                if not likely:
                    item.setForeground(QBrush(QColor("#9e9e9e")))
                self.list.addItem(item)
            self.list.blockSignals(False)

        def set_ticks(self, ids):
            """Tick exactly these layers (those that can be ticked)."""
            self.list.blockSignals(True)
            for item in self.items():
                can_tick = bool(item.flags() & Qt.ItemIsEnabled)
                item.setCheckState(Qt.Checked if can_tick and item.data(LAYER_ID_ROLE) in ids else Qt.Unchecked)
            self.list.blockSignals(False)

        def block_layers(self, ids):
            """Grey out the rows of these layers (ticked in the other list); release the others."""
            self.list.blockSignals(True)
            for item in self.items():
                if item.data(USABLE_ROLE):
                    blocked = item.data(LAYER_ID_ROLE) in ids
                    if blocked:
                        item.setCheckState(Qt.Unchecked)
                    item.setFlags(Qt.NoItemFlags if blocked else Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            self.list.blockSignals(False)

        def _tick_all(self, on):
            self.set_ticks(self.likely_ids() if on else [])
            self.changed.emit()

    class CheckDialog(QtWidgets.QDialog):
        def __init__(self, iface, parent=None):
            super().__init__(parent)
            self.setObjectName(DIALOG_NAME)
            self.setWindowTitle(f"{DIALOG_TITLE} — EA Tools")
            icon = os.path.join(os.path.dirname(os.path.abspath(__file__)), TOOL_ICON) \
                if "__file__" in globals() else ""
            if icon and os.path.exists(icon):
                from qgis.PyQt.QtGui import QIcon
                self.setWindowIcon(QIcon(icon))
            self.setWindowFlags(self.windowFlags()
                                | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint)
            self.setAttribute(Qt.WA_DeleteOnClose, True)
            self.setMinimumSize(860, 600)
            self.resize(1100, 760)

            self.iface = iface
            self.scan_result = None
            self.bands = []     # map highlight rubber bands
            self._blink = None  # their blink timer
            self.running = False
            self._inputs_ok = False
            self.is_dark = self.palette().color(self.palette().Window).lightness() < 128
            # A list the user has ticked by hand is left alone by autodetection.
            self.manual = {"ref": False, "target": False}

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
            self.tabs.addTab(self._build_main_tab(), "EA vs Barangay Check")
            self.tabs.addTab(self._build_dupgeom_tab(), "Duplicate Geometry Fixer")
            self.tabs.addTab(self._build_help_tab(), "Help and Information")
            root.addWidget(self.tabs, 1)

            close_row = QtWidgets.QHBoxLayout()
            close_row.addStretch()
            close_btn = QtWidgets.QPushButton("Close")
            close_btn.setMinimumWidth(100)
            close_btn.clicked.connect(self.close)
            close_row.addWidget(close_btn)
            root.addLayout(close_row)

            self.apply_mode(reset=False)
            self.refresh_lists()
            self.autodetect_layers()
            self.ref.changed.connect(lambda: self.on_layer_picked("ref"))
            self.target.changed.connect(lambda: self.on_layer_picked("target"))
            QgsProject.instance().layersAdded.connect(self.on_layers_added)
            QgsProject.instance().layersRemoved.connect(self.on_layers_added)
            self.apply_theme()
            self.validate_inputs()
            self.update_summary()

        # ──────────────── construction ────────────────
        def _build_main_tab(self):
            tab = QtWidgets.QWidget()
            root = QtWidgets.QHBoxLayout(tab)
            root.setContentsMargins(6, 6, 6, 6)
            root.setSpacing(8)

            # ── LEFT: mode, inputs and run controls ──
            left = QtWidgets.QWidget()
            left.setFixedWidth(LEFT_WIDTH)
            ll = QtWidgets.QVBoxLayout(left)
            ll.setContentsMargins(0, 0, 0, 0)
            ll.setSpacing(6)

            box, lay = group("Check Mode")
            self.mode_group = QtWidgets.QButtonGroup(self)
            self.mode_buttons = {}
            mrow = QtWidgets.QHBoxLayout()
            for key, m in MODES.items():
                rb = QtWidgets.QRadioButton(m["label"])
                rb.setToolTip(MODE_TEXT[key]["hint"])
                self.mode_group.addButton(rb)
                self.mode_buttons[key] = rb
                mrow.addWidget(rb)
            self.mode_buttons["ea_bgy"].setChecked(True)
            self.mode_group.buttonToggled.connect(self.on_mode_toggled)
            lay.addLayout(mrow)
            self.mode_hint = self._hint()
            lay.addWidget(self.mode_hint)
            ll.addWidget(box)

            self.target_box, lay = group()
            self.target = LayerChecklist("Click a layer to tick / untick it. The ticked layers are scanned\n"
                                         "together, and only they are edited by Snap Selected.")
            lay.addWidget(self.target, 1)
            self.target_hint = self._hint()
            lay.addWidget(self.target_hint)
            ll.addWidget(self.target_box, 1)

            self.ref_box, lay = group()
            self.ref = LayerChecklist("Click a layer to tick / untick it. The ticked layers are the fixed\n"
                                      "reference boundary. They are never changed.")
            lay.addWidget(self.ref, 1)
            self.ref_hint = self._hint()
            lay.addWidget(self.ref_hint)
            ll.addWidget(self.ref_box, 1)

            box, lay = group("Run Check")
            self.scan_button = button("Scan", 32, "/mActionStart.svg")
            self.scan_button.setDefault(True)
            self.scan_button.setToolTip("Find the discrepancies of the chosen mode and list them.\n"
                                        "Nothing is changed until you press Snap Selected.")
            self.scan_button.clicked.connect(self.scan)
            lay.addWidget(self.scan_button)
            self.run_hint = self._hint()
            lay.addWidget(self.run_hint)
            ll.addWidget(box)

            self.clear_btn = button("Clear", 32)
            self.clear_btn.setToolTip("Clear the discrepancy list and the log.")
            self.clear_btn.clicked.connect(self.clear_all)
            ll.addWidget(self.clear_btn)
            self.progress = QtWidgets.QProgressBar()
            self.progress.setTextVisible(False)
            self.progress.setRange(0, 1)
            self.progress.setValue(0)
            self.progress.setFixedHeight(18)
            ll.addWidget(self.progress)
            left.setMinimumHeight(560)
            left_scroll = QtWidgets.QScrollArea()  # the two layer lists need room on small screens
            left_scroll.setWidget(left)
            left_scroll.setWidgetResizable(True)
            left_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
            left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            left_scroll.setFixedWidth(LEFT_WIDTH + 18)

            # ── RIGHT: discrepancy list, snap panel, log ──
            right = QtWidgets.QWidget()
            rl = QtWidgets.QVBoxLayout(right)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(6)

            self.summary = QtWidgets.QLabel(NO_RESULTS)
            self.summary.setWordWrap(True)
            rl.addWidget(self.summary)

            filters = QtWidgets.QHBoxLayout()
            self.search_edit = QtWidgets.QLineEdit()
            self.search_edit.setPlaceholderText("Filter by name, Geocode, Barangay or City/Municipality…")
            self.search_edit.setClearButtonEnabled(True)
            self.search_edit.textChanged.connect(self.apply_filter)
            filters.addWidget(self.search_edit, 1)
            self.filter = QtWidgets.QComboBox()
            self.filter.currentIndexChanged.connect(self.apply_filter)
            filters.addWidget(self.filter)
            self.zoom_btn = QtWidgets.QToolButton()
            self.zoom_btn.setIcon(QgsApplication.getThemeIcon("/mActionZoomToSelected.svg"))
            self.zoom_btn.setToolTip("Zoom to the selected discrepancies")
            self.zoom_btn.clicked.connect(lambda: self.zoom_to_rows(self.selected_rows()))
            filters.addWidget(self.zoom_btn)
            rl.addLayout(filters)

            self.table = QtWidgets.QTableWidget(0, len(TABLE_COLUMNS))
            self.table.setHorizontalHeaderLabels(TABLE_COLUMNS)
            self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
            self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
            self.table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
            self.table.setAlternatingRowColors(True)
            self.table.setSortingEnabled(True)
            self.table.verticalHeader().setVisible(False)
            self.table.horizontalHeader().setStretchLastSection(True)
            self.table.itemSelectionChanged.connect(self.on_table_selection)
            self.table.cellDoubleClicked.connect(lambda r, _c: self.zoom_to_rows([r]))
            rl.addWidget(self.table, 1)

            self.legend = QtWidgets.QLabel()
            rl.addWidget(self.legend)

            # Snap panel sits below the list: Scan -> Review & select -> Snap.
            self.snap_group, gf = group("")
            gf.setContentsMargins(10, 24, 10, 10)
            gf.setSpacing(7)
            arow = QtWidgets.QHBoxLayout()
            self.select_btn = button("Select Not Snapped", 32)
            self.select_btn.setToolTip("Select every listed row that has not been snapped yet.")
            self.select_btn.clicked.connect(self.select_pending)
            self.zoom_button = button("Zoom to Selected", 32, "/mActionZoomToSelected.svg")
            self.zoom_button.clicked.connect(lambda: self.zoom_to_rows(self.selected_rows()))
            self.switch_btn = button("Switch Fix", 32, "/mActionRefresh.svg")
            self.switch_btn.setToolTip(
                "Give the selected overlaps / gaps to the next feature that could take them\n"
                "(overlap: cut it from the other feature; gap: add it to the next bordering one).\n"
                "Also makes a 'Fix manually' gap snappable by choosing a feature for it.")
            self.switch_btn.clicked.connect(self.switch_selected)
            self.snap_button = button("Snap Selected", 32, "/mIconSnapping.svg")
            self.snap_button.setToolTip("Snap the features named in Fix for the selected discrepancies.\n"
                                        "Changes are unsaved edits in the checked layer (Edit > Undo to take "
                                        "them back).")
            self.snap_button.clicked.connect(self.snap_selected)
            self.undo_btn = button("Undo Last Snap", 32, "/mActionUndo.svg")
            self.undo_btn.setToolTip(
                "Take back the last Snap Selected: the features go back to their shape before it and\n"
"their rows to their earlier status (normally 'Not snapped'). Press again for the snap\n"
                "before that. Refused if you edited those layers or used Edit > Undo afterwards; not\n"
                "possible once their edits were saved or discarded.")
            self.undo_btn.clicked.connect(self.undo_snap)
            self.match_btn = button("Match Vertices", 32, "/mActionVertexTool.svg")
            self.match_btn.setToolTip(
                "Pair the vertices along every shared edge of the checked layer: a vertex of one feature\n"
                "within 1 cm of a neighbour's edge is moved onto it (onto the neighbour's vertex if one is that\n"
                "close) and put into the neighbour too, so both hold the same point. Checked against a\n"
                "barangay layer, a vertex on its barangay line is never moved. Undo Last Snap takes it back.")
            self.match_btn.clicked.connect(self.match_all)
            arow.addWidget(self.select_btn, 1)
            arow.addWidget(self.zoom_button, 1)
            arow.addWidget(self.switch_btn, 1)
            arow.addWidget(self.snap_button, 2)
            arow.addWidget(self.match_btn, 1)
            arow.addWidget(self.undo_btn, 1)
            gf.addLayout(arow)
            self.highlight_chk = QtWidgets.QCheckBox("Blinking dotted outline and snap preview on zoom")
            self.highlight_chk.setChecked(True)
            self.highlight_chk.setToolTip(
                "On: zooming (double-click / Zoom to Selected) blinks a dotted outline and circle on the\n"
                "discrepancy and a dotted green outline of the Fix feature after Snap Selected.\n"
                "Off: only the plain red flash.")
            self.highlight_chk.toggled.connect(lambda on: None if on else self.clear_highlight())
            gf.addWidget(self.highlight_chk)
            self.snap_hint = self._hint("Snapping leaves the checked layer in edit mode: Save Layer Edits to keep "
                                        "the changes, Edit > Undo to take them back.")
            gf.addWidget(self.snap_hint)
            self.snap_group.setEnabled(False)
            rl.addWidget(self.snap_group)

            rl.addWidget(QtWidgets.QLabel("Log"))
            self.log_view = QtWidgets.QPlainTextEdit()
            self.log_view.setReadOnly(True)
            self.log_view.setFixedHeight(150)
            rl.addWidget(self.log_view)

            root.addWidget(left_scroll)
            root.addWidget(right, 1)
            return tab

        def _build_dupgeom_tab(self):
            """Build the Duplicate Geometry Fixer tab."""
            tab = QtWidgets.QWidget()
            root = QtWidgets.QHBoxLayout(tab)
            root.setContentsMargins(6, 6, 6, 6)
            root.setSpacing(8)

            # ── LEFT column: layer list + controls ──────────────────────────────
            left = QtWidgets.QWidget()
            left.setFixedWidth(LEFT_WIDTH)
            ll = QtWidgets.QVBoxLayout(left)
            ll.setContentsMargins(0, 0, 0, 0)
            ll.setSpacing(6)

            # Layer list ----------------------------------------------------------
            lbox, llay = group("Polygon Layers to Scan")
            self.dupgeom_layer_list = CompactList()
            self.dupgeom_layer_list.setSelectionMode(
                QtWidgets.QAbstractItemView.NoSelection)
            self.dupgeom_layer_list.setAlternatingRowColors(True)
            self.dupgeom_layer_list.setItemDelegate(
                LayerCheckDelegate(self.dupgeom_layer_list))
            self.dupgeom_layer_list.setMinimumHeight(200)
            self.dupgeom_layer_list.setToolTip(
                "Tick the layers to scan for duplicate geometries or duplicate parts.")
            self.dupgeom_layer_list.itemChanged.connect(
                lambda _item: self._dupgeom_update_buttons())
            self.dupgeom_layer_list.itemClicked.connect(
                lambda _item: self._dupgeom_update_buttons())
            lbtn_row = QtWidgets.QHBoxLayout()
            self.dupgeom_all_btn = button("Select All", 24)
            self.dupgeom_all_btn.clicked.connect(
                lambda: self._dupgeom_tick_all(True))
            self.dupgeom_none_btn = button("Clear", 24)
            self.dupgeom_none_btn.clicked.connect(
                lambda: self._dupgeom_tick_all(False))
            lbtn_row.addWidget(self.dupgeom_all_btn)
            lbtn_row.addWidget(self.dupgeom_none_btn)
            llay.addLayout(lbtn_row)
            llay.addWidget(self.dupgeom_layer_list, 1)
            ll.addWidget(lbox, 1)

            # Run controls --------------------------------------------------------
            rbox, rlay = group("Run")
            self.dupgeom_scan_btn = button("Scan", 32, "/mActionStart.svg")
            self.dupgeom_scan_btn.setToolTip(
                "Scan the ticked layers for features with duplicate geometry parts or duplicate features.\n"
                "Nothing is changed until you press Fix.")
            self.dupgeom_scan_btn.clicked.connect(self._dupgeom_scan)
            rlay.addWidget(self.dupgeom_scan_btn)

            fix_row = QtWidgets.QHBoxLayout()
            self.dupgeom_fix_sel_btn = button("Fix Selected", 32,
                                               "/mIconSnapping.svg")
            self.dupgeom_fix_sel_btn.setToolTip(
                "Fix duplicate geometries for the selected rows only.")
            self.dupgeom_fix_sel_btn.clicked.connect(
                lambda: self._dupgeom_fix(selected_only=True))
            self.dupgeom_fix_all_btn = button("Fix All", 32,
                                               "/mIconSnapping.svg")
            self.dupgeom_fix_all_btn.setToolTip(
                "Fix duplicate geometries for every listed row.")
            self.dupgeom_fix_all_btn.clicked.connect(
                lambda: self._dupgeom_fix(selected_only=False))
            fix_row.addWidget(self.dupgeom_fix_sel_btn, 1)
            fix_row.addWidget(self.dupgeom_fix_all_btn, 1)
            rlay.addLayout(fix_row)

            self.dupgeom_run_hint = self._hint(
                "Tick at least one layer, then click Scan.")
            rlay.addWidget(self.dupgeom_run_hint)
            self.dupgeom_progress = QtWidgets.QProgressBar()
            self.dupgeom_progress.setTextVisible(False)
            self.dupgeom_progress.setRange(0, 1)
            self.dupgeom_progress.setValue(0)
            self.dupgeom_progress.setFixedHeight(18)
            rlay.addWidget(self.dupgeom_progress)
            ll.addWidget(rbox)

            left_scroll = QtWidgets.QScrollArea()
            left_scroll.setWidget(left)
            left_scroll.setWidgetResizable(True)
            left_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
            left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            left_scroll.setFixedWidth(LEFT_WIDTH + 18)

            # ── RIGHT column: results table + log ───────────────────────────────
            right = QtWidgets.QWidget()
            rl = QtWidgets.QVBoxLayout(right)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(6)

            self.dupgeom_summary = QtWidgets.QLabel(
                "No scan yet. Tick layers on the left and click Scan.")
            self.dupgeom_summary.setWordWrap(True)
            rl.addWidget(self.dupgeom_summary)

            # Results table -------------------------------------------------------
            DG_COLS = ["Layer", "Feature ID", "Issue", "Details", "Status"]
            self.dupgeom_table = QtWidgets.QTableWidget(0, len(DG_COLS))
            self.dupgeom_table.setHorizontalHeaderLabels(DG_COLS)
            self.dupgeom_table.setEditTriggers(
                QtWidgets.QAbstractItemView.NoEditTriggers)
            self.dupgeom_table.setSelectionBehavior(
                QtWidgets.QAbstractItemView.SelectRows)
            self.dupgeom_table.setSelectionMode(
                QtWidgets.QAbstractItemView.ExtendedSelection)
            self.dupgeom_table.setAlternatingRowColors(True)
            self.dupgeom_table.setSortingEnabled(True)
            self.dupgeom_table.verticalHeader().setVisible(False)
            self.dupgeom_table.horizontalHeader().setStretchLastSection(True)
            self.dupgeom_table.itemSelectionChanged.connect(
                self._dupgeom_on_select)
            self.dupgeom_table.cellDoubleClicked.connect(
                self._dupgeom_zoom_row)
            rl.addWidget(self.dupgeom_table, 1)

            # Log -----------------------------------------------------------------
            rl.addWidget(QtWidgets.QLabel("Log"))
            self.dupgeom_log = QtWidgets.QPlainTextEdit()
            self.dupgeom_log.setReadOnly(True)
            self.dupgeom_log.setFixedHeight(150)
            rl.addWidget(self.dupgeom_log)

            root.addWidget(left_scroll)
            root.addWidget(right, 1)

            # Internal state: list of scan rows as dicts
            self._dupgeom_rows = []          # {layer_id, fid, layer_name, before, after, geom_new}
            self._dupgeom_fix_applied = set()  # row indices already fixed

            # Wire layer list refresh to project changes
            QgsProject.instance().layersAdded.connect(
                self._dupgeom_refresh_layers)
            QgsProject.instance().layersRemoved.connect(
                self._dupgeom_refresh_layers)
            self._dupgeom_refresh_layers()
            self._dupgeom_update_buttons()

            return tab

        # ──────────────── Duplicate Geometry Fixer helpers ────────────────────

        _DG_LAYER_ID = Qt.UserRole       # on a list row: layer id
        _DG_LAYER_ROW = Qt.UserRole      # on a table cell: row index in _dupgeom_rows
        _DG_STATUS_PENDING = "Pending"
        _DG_STATUS_FIXED   = "Fixed"
        _DG_STATUS_FAILED  = "Failed"

        def _dupgeom_refresh_layers(self, *_):
            """Rebuild the layer checklist from the current project."""
            from qgis.core import QgsVectorLayer, QgsWkbTypes
            prj = QgsProject.instance()
            checked = {
                self.dupgeom_layer_list.item(i).data(self._DG_LAYER_ID)
                for i in range(self.dupgeom_layer_list.count())
                if self.dupgeom_layer_list.item(i).checkState() == Qt.Checked
            }
            self.dupgeom_layer_list.blockSignals(True)
            self.dupgeom_layer_list.clear()
            polygon_layers = [
                lyr for lyr in prj.mapLayers().values()
                if isinstance(lyr, QgsVectorLayer) and lyr.geometryType() == QgsWkbTypes.PolygonGeometry
            ]
            for lyr in polygon_layers:
                item = QtWidgets.QListWidgetItem(lyr.name())
                item.setData(self._DG_LAYER_ID, lyr.id())
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
                # Auto-check if previously checked, or if there's only 1 layer
                is_checked = (lyr.id() in checked) or (len(polygon_layers) == 1 and not checked)
                item.setCheckState(Qt.Checked if is_checked else Qt.Unchecked)
                self.dupgeom_layer_list.addItem(item)
            self.dupgeom_layer_list.blockSignals(False)
            self._dupgeom_update_buttons()

        def _dupgeom_tick_all(self, on):
            self.dupgeom_layer_list.blockSignals(True)
            for i in range(self.dupgeom_layer_list.count()):
                item = self.dupgeom_layer_list.item(i)
                item.setCheckState(Qt.Checked if on else Qt.Unchecked)
            self.dupgeom_layer_list.blockSignals(False)
            self._dupgeom_update_buttons()

        def _dupgeom_checked_layers(self):
            """Return the ticked QgsVectorLayer objects."""
            prj = QgsProject.instance()
            ids = [
                self.dupgeom_layer_list.item(i).data(self._DG_LAYER_ID)
                for i in range(self.dupgeom_layer_list.count())
                if self.dupgeom_layer_list.item(i).checkState() == Qt.Checked
            ]
            return [lyr for lid in ids for lyr in [prj.mapLayer(lid)] if lyr]

        def _dupgeom_update_buttons(self):
            checked_count = sum(
                1 for i in range(self.dupgeom_layer_list.count())
                if self.dupgeom_layer_list.item(i).checkState() == Qt.Checked
            )
            has_layers = checked_count > 0
            has_rows = self.dupgeom_table.rowCount() > 0
            sel_rows = bool(self.dupgeom_table.selectedItems())
            self.dupgeom_scan_btn.setEnabled(has_layers)
            self.dupgeom_fix_all_btn.setEnabled(has_rows)
            self.dupgeom_fix_sel_btn.setEnabled(sel_rows)
            if not has_layers:
                self.set_hint(self.dupgeom_run_hint,
                              "Tick at least one layer, then click Scan.",
                              "error")
            else:
                self.set_hint(self.dupgeom_run_hint,
                              f"{checked_count} layer(s) ticked. Ready to scan.",
                              "ok")

        def _dupgeom_log(self, text):
            from datetime import datetime
            stamp = datetime.now().strftime("%H:%M:%S")
            self.dupgeom_log.appendPlainText(f"[{stamp}] {text}")
            QgsApplication.processEvents()

        def _dupgeom_scan(self):
            """Scan ticked layers; populate the results table with duplicate parts and duplicate features."""
            from qgis.core import QgsApplication, QgsGeometry
            layers = self._dupgeom_checked_layers()
            if not layers:
                self._dupgeom_log("No layers ticked.")
                return

            self.dupgeom_table.setSortingEnabled(False)
            self.dupgeom_table.setRowCount(0)
            self._dupgeom_rows = []
            self._dupgeom_fix_applied = set()

            self.dupgeom_progress.setRange(0, 0)
            self.dupgeom_scan_btn.setEnabled(False)
            self.dupgeom_fix_all_btn.setEnabled(False)
            self.dupgeom_fix_sel_btn.setEnabled(False)
            self._dupgeom_log("Scan started…")

            total_found = 0
            for lyr in layers:
                lname = lyr.name()
                self._dupgeom_log(f"  Scanning '{lname}'…")
                count = lyr.featureCount()
                self.dupgeom_progress.setRange(0, max(count, 1))
                self.dupgeom_progress.setValue(0)
                found = 0
                seen_geoms = {}  # wkb_2d_bytes -> first_fid

                for i, feat in enumerate(lyr.getFeatures()):
                    self.dupgeom_progress.setValue(i + 1)
                    if i % 200 == 0:
                        QgsApplication.processEvents()

                    geom = feat.geometry()
                    if not geom or geom.isNull() or geom.isEmpty():
                        continue

                    # 1. Check duplicate parts inside multi-part feature
                    has_dup_parts = False
                    new_geom = None
                    n_parts_before = 0
                    n_parts_after = 0

                    if geom.isMultipart():
                        try:
                            sg = shapely.from_wkb(bytes(geom.asWkb()))
                            if hasattr(sg, "geoms") and len(sg.geoms) > 1:
                                n_parts_before = len(sg.geoms)
                                seen_pwkb = set()
                                unique_geoms = []
                                for p in sg.geoms:
                                    pwkb = shapely.to_wkb(shapely.force_2d(p))
                                    if pwkb not in seen_pwkb:
                                        seen_pwkb.add(pwkb)
                                        unique_geoms.append(p)
                                n_parts_after = len(unique_geoms)
                                if n_parts_after < n_parts_before:
                                    has_dup_parts = True
                                    clean_sg = unique_geoms[0] if len(unique_geoms) == 1 else shapely.MultiPolygon(unique_geoms)
                                    new_geom = QgsGeometry()
                                    new_geom.fromWkb(shapely.to_wkb(shapely.force_2d(clean_sg)))
                        except Exception:
                            pass

                    if has_dup_parts and new_geom is not None:
                        row_idx = len(self._dupgeom_rows)
                        self._dupgeom_rows.append({
                            "type":       "part",
                            "layer_id":   lyr.id(),
                            "layer_name": lname,
                            "fid":        feat.id(),
                            "geom_new":   new_geom,
                            "extent":     geom.boundingBox(),
                        })
                        r = self.dupgeom_table.rowCount()
                        self.dupgeom_table.insertRow(r)
                        cells = [
                            lname,
                            str(feat.id()),
                            f"Duplicate part(s) ({n_parts_before} → {n_parts_after})",
                            f"{n_parts_before - n_parts_after} identical part(s) removed",
                            self._DG_STATUS_PENDING,
                        ]
                        for c, val in enumerate(cells):
                            item = QtWidgets.QTableWidgetItem(val)
                            item.setData(self._DG_LAYER_ROW, row_idx)
                            self.dupgeom_table.setItem(r, c, item)
                        self._dupgeom_paint_status(r, self._DG_STATUS_PENDING)
                        found += 1

                    # 2. Check if this feature is a duplicate of another feature
                    effective_geom = new_geom if (has_dup_parts and new_geom) else geom
                    try:
                        feat_wkb = bytes(effective_geom.asWkb())
                        if feat_wkb in seen_geoms:
                            first_fid = seen_geoms[feat_wkb]
                            row_idx = len(self._dupgeom_rows)
                            self._dupgeom_rows.append({
                                "type":       "feature",
                                "layer_id":   lyr.id(),
                                "layer_name": lname,
                                "fid":        feat.id(),
                                "geom_new":   None,
                                "extent":     geom.boundingBox(),
                            })
                            r = self.dupgeom_table.rowCount()
                            self.dupgeom_table.insertRow(r)
                            cells = [
                                lname,
                                str(feat.id()),
                                "Duplicate feature",
                                f"Identical geometry to FID {first_fid}",
                                self._DG_STATUS_PENDING,
                            ]
                            for c, val in enumerate(cells):
                                item = QtWidgets.QTableWidgetItem(val)
                                item.setData(self._DG_LAYER_ROW, row_idx)
                                self.dupgeom_table.setItem(r, c, item)
                            self._dupgeom_paint_status(r, self._DG_STATUS_PENDING)
                            found += 1
                        else:
                            seen_geoms[feat_wkb] = feat.id()
                    except Exception:
                        pass

                total_found += found
                self._dupgeom_log(
                    f"  '{lname}': {found} duplicate issue(s) found.")

            self.dupgeom_progress.setRange(0, 1)
            self.dupgeom_progress.setValue(1)
            self.dupgeom_table.setSortingEnabled(True)
            self.dupgeom_table.resizeColumnsToContents()

            if total_found:
                summary = (f"{total_found} duplicate issue(s) found "
                           f"across {len(layers)} layer(s). Select rows and click Fix Selected, "
                           "or click Fix All.")
                self.set_hint(self.dupgeom_run_hint,
                              f"Found {total_found} issue(s). Click Fix All or Fix Selected.",
                              "error")
            else:
                summary = "No duplicate geometry parts or duplicate features found."
                self.set_hint(self.dupgeom_run_hint,
                              "No duplicates found.", "ok")
            self.dupgeom_summary.setText(summary)
            self._dupgeom_log(f"Scan complete: {summary}")
            self._dupgeom_update_buttons()
            self.dupgeom_scan_btn.setEnabled(True)

        def _dupgeom_paint_status(self, row, status):
            STATUS_COL = 4
            item = self.dupgeom_table.item(row, STATUS_COL)
            if item is None:
                return
            colours = {
                self._DG_STATUS_PENDING: None,
                self._DG_STATUS_FIXED:   "#2e7d32",
                self._DG_STATUS_FAILED:  "#c62828",
            }
            col = colours.get(status)
            if col:
                item.setForeground(QBrush(QColor(col)))
            item.setText(status)

        def _dupgeom_row_index(self, table_row):
            """Return the _dupgeom_rows index stored in the first cell of a table row."""
            item = self.dupgeom_table.item(table_row, 0)
            return item.data(self._DG_LAYER_ROW) if item else None

        def _dupgeom_on_select(self):
            self.dupgeom_fix_sel_btn.setEnabled(
                bool(self.dupgeom_table.selectedItems()))

        def _dupgeom_zoom_row(self, table_row, _col=None):
            """Flash + zoom the map to the feature in a double-clicked row."""
            if self.iface is None:
                return
            row_idx = self._dupgeom_row_index(table_row)
            if row_idx is None:
                return
            info = self._dupgeom_rows[row_idx]
            lyr = QgsProject.instance().mapLayer(info["layer_id"])
            if lyr is None:
                return
            canvas = self.iface.mapCanvas()
            extent = info["extent"]
            if extent is not None and not extent.isEmpty():
                try:
                    from qgis.core import QgsCoordinateTransform
                    to_canvas = QgsCoordinateTransform(
                        lyr.crs(), canvas.mapSettings().destinationCrs(), QgsProject.instance())
                    extent = to_canvas.transformBoundingBox(extent)
                    extent.scale(1.5)
                    canvas.setExtent(extent)
                    canvas.refresh()
                except Exception:
                    canvas.setExtent(info["extent"])
                    canvas.refresh()
            try:
                canvas.flashFeatureIds(lyr, [info["fid"]])
            except Exception:
                pass

        def _dupgeom_fix(self, selected_only=False):
            """Apply fixes to listed (or selected) rows."""
            from qgis.core import QgsApplication

            if selected_only:
                table_rows = sorted({
                    i.row() for i in self.dupgeom_table.selectedItems()
                })
            else:
                table_rows = list(range(self.dupgeom_table.rowCount()))

            by_layer = {}
            for tr in table_rows:
                ri = self._dupgeom_row_index(tr)
                if ri is None or ri in self._dupgeom_fix_applied:
                    continue
                info = self._dupgeom_rows[ri]
                by_layer.setdefault(info["layer_id"], []).append((tr, ri))

            if not by_layer:
                self._dupgeom_log("Nothing to fix (all selected rows already fixed or none selected).")
                return

            self.dupgeom_progress.setRange(0, len(by_layer))
            self.dupgeom_progress.setValue(0)
            fixed_total = 0

            for li, (layer_id, pairs) in enumerate(by_layer.items()):
                lyr = QgsProject.instance().mapLayer(layer_id)
                if lyr is None:
                    for tr, ri in pairs:
                        self._dupgeom_paint_status(tr, self._DG_STATUS_FAILED)
                    self._dupgeom_log(
                        f"Layer '{layer_id}' no longer in project — skipped.")
                    continue

                if not lyr.isEditable() and not lyr.startEditing():
                    for tr, ri in pairs:
                        self._dupgeom_paint_status(tr, self._DG_STATUS_FAILED)
                    self._dupgeom_log(
                        f"'{lyr.name()}' cannot be put into edit mode — skipped.")
                    continue

                lyr.beginEditCommand(
                    f"Fix duplicate geometries ({len(pairs)} issue(s))")
                ok, fail = 0, 0
                for tr, ri in pairs:
                    info = self._dupgeom_rows[ri]
                    success = False
                    if info["type"] == "part":
                        success = lyr.changeGeometry(info["fid"], info["geom_new"])
                    elif info["type"] == "feature":
                        success = lyr.deleteFeature(info["fid"])

                    if success:
                        self._dupgeom_fix_applied.add(ri)
                        self._dupgeom_paint_status(tr, self._DG_STATUS_FIXED)
                        ok += 1
                    else:
                        self._dupgeom_paint_status(tr, self._DG_STATUS_FAILED)
                        fail += 1
                lyr.endEditCommand()
                self._dupgeom_log(
                    f"'{lyr.name()}': {ok} fixed, {fail} failed.")
                fixed_total += ok
                self.dupgeom_progress.setValue(li + 1)
                QgsApplication.processEvents()

            self.dupgeom_progress.setRange(0, 1)
            self.dupgeom_progress.setValue(1)
            self._dupgeom_log(
                f"Done. {fixed_total} issue(s) fixed (layers left in edit mode; save to keep).")
            if fixed_total:
                self.set_hint(self.dupgeom_run_hint,
                              f"{fixed_total} issue(s) fixed. Save layer edits to keep.",
                              "ok")
            self._dupgeom_update_buttons()

        def _build_help_tab(self):
            tab = QtWidgets.QWidget()
            lay = QtWidgets.QVBoxLayout(tab)
            lay.setContentsMargins(8, 8, 8, 8)
            self.help_text = QtWidgets.QTextBrowser()
            self.help_text.setOpenExternalLinks(True)
            lay.addWidget(self.help_text)
            return tab

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

        # ──────────────── mode ────────────────
        def mode(self):
            for key, rb in self.mode_buttons.items():
                if rb.isChecked():
                    return key
            return "ea_bgy"

        def self_check(self):
            """True when the chosen mode runs on the checked layers alone (no reference ticked)."""
            return not MODES[self.mode()]["ref_required"] and not self.ref.checked_ids()

        def current_issues(self):
            if self.scan_result is not None:
                return self.scan_result.issues
            return mode_issues(self.mode(), self.self_check())

        def on_mode_toggled(self, _button, checked):
            if checked and not self.running:
                self.apply_mode(reset=True)

        def apply_mode(self, reset=True):
            """Relabel the layer boxes and table for the chosen mode; old results do not apply to it."""
            mode = self.mode()
            text = MODE_TEXT[mode]
            self.target_box.setTitle(text["target"])
            self.ref_box.setTitle(text["ref"])
            self.set_hint(self.mode_hint, text["hint"])
            labels = HEADER_LABELS[MODES[mode]["target"]]
            self.table.setHorizontalHeaderLabels([labels.get(c, c) for c in TABLE_COLUMNS])
            fix_col = COL["Fix EA"]
            self.table.horizontalHeaderItem(fix_col).setToolTip("The feature that Snap Selected changes for this row.")
            if reset:
                self.clear_highlight()
                if self.scan_result is not None or self.table.rowCount():
                    self.log(f"Check mode changed to {MODES[mode]['label']}: previous results cleared.")
                self.scan_result = None
                self.table.setRowCount(0)
                self.manual = {"ref": False, "target": False}
                self.refresh_lists()
                self.autodetect_layers()
                self.validate_inputs()
                self.update_summary()
            self.refresh_issue_widgets()

        def refresh_issue_widgets(self):
            """Rebuild the Show filter and the colour legend for the current issues."""
            issues = self.current_issues()
            current = self.filter.currentData()
            self.filter.blockSignals(True)
            self.filter.clear()
            self.filter.addItem("All issues", None)
            for issue, _ in issues:
                self.filter.addItem(issue, ("issue", issue))
            for status in (STATUS_PENDING, STATUS_DONE, STATUS_FAILED, STATUS_MANUAL):
                self.filter.addItem(status, ("status", status))
            self.filter.setCurrentIndex(max(self.filter.findData(current), 0))
            self.filter.blockSignals(False)
            swatch = '<span style="background-color:rgb({}); border:1px solid #999;">&nbsp;&nbsp;&nbsp;</span> {}'
            parts = [swatch.format(",".join(rgba.split(",")[:3]), issue) for issue, rgba in issues]
            self.legend.setText(" &nbsp;&nbsp;".join(parts)
                                + " &nbsp;&nbsp;·&nbsp; Click: select on map · Double-click: zoom")

        # ──────────────── theme ────────────────
        def apply_theme(self):
            t = THEMES[self.is_dark]
            self.title_label.setStyleSheet(
                f"font-size: 15px; font-weight: bold; color: {t['title']}; padding: 2px 0;")
            self.sub_label.setStyleSheet(f"color: {t['sub']}; font-size: 11px; margin-bottom: 4px;")
            self.sep_line.setStyleSheet(f"color: {t['line']};")
            self.tabs.setStyleSheet(t["tabs"])
            self.summary.setStyleSheet(f"font-size:13px; font-weight:600; color:{t['summary']};")
            self.log_view.setStyleSheet(t["log"])
            self.table.setStyleSheet(t["table"])
            self.target.list.setStyleSheet(t["list"])
            self.ref.list.setStyleSheet(t["list"])
            self.legend.setStyleSheet(f"font-size:11px; color:{t['hint']}; padding:2px 0;")
            # Duplicate Geometry Fixer tab
            self.dupgeom_log.setStyleSheet(t["log"])
            self.dupgeom_table.setStyleSheet(t["table"])
            self.dupgeom_layer_list.setStyleSheet(t["list"])
            self.dupgeom_summary.setStyleSheet(
                f"font-size:13px; font-weight:600; color:{t['summary']};")
            html = HELP_HTML
            if self.is_dark:
                for a, b in HELP_DARK_SWAPS:
                    html = html.replace(a, b)
            self.help_text.setStyleSheet(t["help"])
            self.help_text.setHtml(html)
            for lab in (self.mode_hint, self.target_hint, self.ref_hint, self.run_hint, self.snap_hint):
                self.set_hint(lab, lab.text(), lab.property("hintKind") or "info")

        def showEvent(self, event):
            super().showEvent(event)
            dark = self.palette().color(self.palette().Window).lightness() < 128
            if dark != self.is_dark:
                self.is_dark = dark
                self.apply_theme()

        # ──────────────── teardown ────────────────
        def _teardown(self):
            self.clear_highlight()
            prj = QgsProject.instance()
            for sig in (prj.layersAdded, prj.layersRemoved):
                try:
                    sig.disconnect(self.on_layers_added)
                except (TypeError, RuntimeError):
                    pass
                try:
                    sig.disconnect(self._dupgeom_refresh_layers)
                except (TypeError, RuntimeError):
                    pass

        def done(self, result):
            self._teardown()
            super().done(result)

        def closeEvent(self, event):
            self._teardown()
            super().closeEvent(event)

        # ──────────────── layers ────────────────
        def refresh_lists(self):
            """Rebuild both layer lists for the current mode and project, keeping valid ticks."""
            mode = self.mode()
            self.target.fill(mode, "target")
            self.ref.fill(mode, "ref")
            self.sync_lists()

        def sync_lists(self):
            """A layer ticked in one list cannot be ticked in the other."""
            self.ref.block_layers(self.target.checked_ids())
            self.target.block_layers(self.ref.checked_ids())

        def autodetect_layers(self):
            """A list the user has not touched gets a tick only when exactly one layer fits it, so a single
            pair of layers is ready to scan. With several candidates the user ticks them (or Select All).
            An optional reference is left empty (self check)."""
            m = MODES[self.mode()]
            if not self.manual["target"]:
                others = self.ref.checked_ids() if self.manual["ref"] else set()
                likely = [i for i in self.target.likely_ids() if i not in others]
                self.target.set_ticks(likely if len(likely) == 1 else [])
            if not self.manual["ref"]:
                likely = [i for i in self.ref.likely_ids() if i not in self.target.checked_ids()]
                self.ref.set_ticks(likely if m["ref_required"] and len(likely) == 1 else [])
            self.sync_lists()

        def on_layer_picked(self, which):
            box = self.target if which == "target" else self.ref
            self.manual[which] = bool(box.checked_ids())
            self.sync_lists()
            self.autodetect_layers()  # re-pick the other list if it was autodetected
            self.validate_inputs()
            if self.scan_result is None:
                self.refresh_issue_widgets()  # self check <-> reference changes the issue names

        def on_layers_added(self, *_args):
            if not self.running:
                self.refresh_lists()
                self.autodetect_layers()
                self.validate_inputs()

        def validate_inputs(self):
            """Hints under the layer lists; Scan is enabled only when the needed layers are ticked."""
            m = MODES[self.mode()]
            targets, refs = self.target.layers(), self.ref.layers()

            def ticked(layers, box):
                count = sum(max(lyr.featureCount(), 0) for lyr in layers)
                return f"{len(layers)} of {box.list.count()} layer(s) ticked, {count:,} feature(s)."

            if targets:
                self.set_hint(self.target_hint, ticked(targets, self.target), "ok")
            else:
                self.set_hint(self.target_hint, f"Tick the {m['unit']} layer(s) to check.")
            if refs:
                self.set_hint(self.ref_hint, ticked(refs, self.ref), "ok")
            elif m["ref_required"]:
                self.set_hint(self.ref_hint, f"Tick the {m['ref_unit']} layer(s).")
            else:
                self.set_hint(self.ref_hint, f"None: the {m['unit']}s are checked against each other "
                                             "(overlaps and gaps). Tick layers to compare with them instead.")

            ok = bool(targets) and (bool(refs) or not m["ref_required"])
            if ok:
                what = (f"overlaps and gaps between the {m['unit']}s of {len(targets)} layer(s)" if not refs
                        else f"{len(targets)} layer(s) vs {len(refs)} reference layer(s)")
                self.set_hint(self.run_hint, f"Ready: {m['label']}, {what}. Click Scan.", "ok")
            else:
                self.set_hint(self.run_hint, "Tick the layers to enable Scan.")
            self._inputs_ok = ok
            self.update_buttons()

        # ------------------------------------------------------------ log
        def log(self, text):
            stamp = datetime.now().strftime("%H:%M:%S")
            for line in str(text).splitlines() or [""]:
                self.log_view.appendPlainText(f"[{stamp}] {line}")
            QtWidgets.QApplication.processEvents()  # show progress while the scan runs

        def clear_all(self):
            if self.running:
                return
            self.clear_highlight()
            self.scan_result = None
            self.table.setRowCount(0)
            self.log_view.clear()
            self.refresh_issue_widgets()
            self.update_summary()
            self.update_buttons()

        # ----------------------------------------------------------- busy
        def set_busy(self, busy):
            self.running = busy
            for w in [self.target, self.ref, self.table, self.filter, self.search_edit,
                      self.clear_btn, *self.mode_buttons.values()]:
                w.setEnabled(not busy)
            self.progress.setRange(0, 0 if busy else 1)
            self.progress.setValue(0 if busy else 1)
            if busy:
                QtWidgets.QApplication.setOverrideCursor(Qt.WaitCursor)
            else:
                QtWidgets.QApplication.restoreOverrideCursor()
            self.update_buttons()

        def df_row(self, table_row):
            return self.table.item(table_row, ISSUE_COL).data(ROW_ROLE)

        def can_switch(self, table_row):
            d = self.scan_result.discrepancies
            r = self.df_row(table_row)
            cands = d.at[r, "_candidates"] or []
            return d.at[r, "Status"] != STATUS_DONE and (len(cands) > 1 or (cands and not d.at[r, "_snappable"]))

        def update_buttons(self):
            rows = self.selected_rows()
            statuses = {self.table.item(r, STATUS_COL).text() for r in rows}
            has_rows = self.table.rowCount() > 0
            idle = not self.running
            self.scan_button.setEnabled(self._inputs_ok and idle)
            self.snap_group.setEnabled((has_rows or self.scan_result is not None) and idle)
            self.zoom_btn.setEnabled(bool(rows) and idle)
            self.zoom_button.setEnabled(bool(rows) and idle)
            self.snap_button.setEnabled(bool(statuses & snappable) and idle)
            self.undo_btn.setEnabled(idle and self.scan_result is not None and bool(self.scan_result.history))
            self.match_btn.setEnabled(idle and self.scan_result is not None)
            self.select_btn.setEnabled(has_rows and idle)
            self.switch_btn.setEnabled(idle and self.scan_result is not None
                                       and any(self.can_switch(r) for r in rows))

        def update_summary(self):
            """One-line state above the list."""
            if self.scan_result is None:
                self.summary.setText(NO_RESULTS)
                return
            total = self.table.rowCount()
            if total == 0:
                self.summary.setText(result_message(self.scan_result.discrepancies, False, self.scan_result))
                return
            shown = sum(not self.table.isRowHidden(r) for r in range(total))
            done = int((self.scan_result.discrepancies["Status"] == STATUS_DONE).sum())
            noun = "discrepancy" if total == 1 else "discrepancies"
            text = f"{shown:,} of {total:,} {noun} shown" if shown != total else f"{total:,} {noun} found"
            self.summary.setText(text + (f" · {done:,} snapped" if done else ""))

        # ----------------------------------------------------------- scan
        def scan(self):
            if self.running or not self._inputs_ok:
                return
            mode = self.mode()
            m = MODES[mode]
            targets, refs = self.target.layers(), self.ref.layers()

            self.remove_old_layers(keep=targets + refs)
            self.clear_highlight()
            self.scan_result = None
            self.table.setRowCount(0)
            self.summary.setText("Scanning…")
            self.log("=" * 50)
            against = f"{len(refs)} reference layer(s)" if refs else "each other (overlaps and gaps)"
            self.log(f"Scan {m['label']}: {len(targets)} layer(s) vs {against}, "
                     f"slivers < {MIN_AREA_SQM:g} sqm")
            self.log("  Checked: " + ", ".join(lyr.name() for lyr in targets))
            if refs:
                self.log("  Reference: " + ", ".join(lyr.name() for lyr in refs))
            edited = [lyr for lyr in targets + refs if lyr.isEditable()]
            if edited:
                self.log("  Note: " + ", ".join(lyr.name() for lyr in edited) +
                         " ha" + ("ve" if len(edited) > 1 else "s") +
                         " unsaved edits — the scan reads those edits, so any discrepancy"
                         " found here is genuine (not a re-read of the pre-snap geometry).")
            self.set_busy(True)
            stream = _LogStream(self.log)
            try:
                with redirect_stdout(stream), redirect_stderr(stream):
                    scan = run_check(refs, targets, MIN_AREA_SQM, mode=mode)
                stream.flush()
            except Exception as e:  # noqa: BLE001 - report every failure in the log
                stream.flush()
                self.log(traceback.format_exc().rstrip())
                self.log(f"Scan FAILED: {e}")
                self.summary.setText("Scan failed. See the Log.")
                return
            finally:
                self.set_busy(False)

            self.log(result_message(scan.discrepancies, include_warnings=False, scan=scan))
            if WARNINGS:
                self.log(f"{len(WARNINGS)} warning(s), see [WARNING] lines above.")
            self.scan_result = scan
            self.refresh_issue_widgets()
            self.show_results()
            self.log("Scan finished.")

        def remove_old_layers(self, keep=()):
            """Remove "Discrepancies ..." scratch layers that older versions added to the project,
            unless one was made permanent or is one of the input layers."""
            keep_ids = {k.id() for k in keep}
            old = [lyr for lyr in QgsProject.instance().mapLayers().values()
                   if lyr.id() not in keep_ids and lyr.providerType() == "memory"
                   and (lyr.customProperty(RESULT_LAYER_TAG) or RESULT_LAYER_NAME.match(lyr.name()))]
            if not old:
                return
            names = [lyr.name() for lyr in old]
            QgsProject.instance().removeMapLayers([lyr.id() for lyr in old])
            self.log(f"Removed {len(names)} previous scratch layer(s): {', '.join(names)}")

        # -------------------------------------------------------- results
        def show_results(self):
            df = self.scan_result.discrepancies
            colours = {issue: qcolour(rgba) for issue, rgba in self.scan_result.issues}
            self.table.setSortingEnabled(False)  # fill first, sort after
            records = df.drop(columns="geometry").to_dict("records")
            self.table.setRowCount(len(records))
            for r, (fid, rec) in enumerate(zip(self.scan_result.fids, records)):
                for c, col in enumerate(TABLE_COLUMNS):
                    value = rec.get(col)
                    item = QtWidgets.QTableWidgetItem()
                    if col == "Area (sqm)":
                        item.setData(Qt.DisplayRole, round(float(value), 2))  # numeric sort
                        item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    else:
                        item.setText("" if value is None or pd.isna(value) else str(value))
                    if col == "Issue":
                        item.setData(FID_ROLE, fid)
                        item.setData(ROW_ROLE, r)
                        if value in colours:
                            item.setBackground(colours[value])
                            item.setForeground(DARK_TEXT if not self.is_dark else QBrush(QColor("#ECEFF1")))
                    if col == "Note":
                        item.setToolTip(item.text())
                    self.table.setItem(r, c, item)
                self.paint_status(r)
            self.table.setSortingEnabled(True)
            self.table.sortItems(COL["Area (sqm)"], Qt.DescendingOrder)
            self.table.resizeColumnsToContents()
            self.apply_filter()

        def paint_status(self, table_row):
            """Show a discrepancy's status (and why it failed) in its Status cell."""
            item = self.table.item(table_row, STATUS_COL)
            df_row = self.df_row(table_row)
            status = self.scan_result.discrepancies.at[df_row, "Status"]
            item.setText(status)
            item.setToolTip(self.scan_result.messages.get(df_row, ""))
            font = QFont()
            font.setBold(status in (STATUS_DONE, STATUS_FAILED))
            item.setFont(font)
            item.setForeground(QBrush(QColor(STATUS_COLOURS[status])) if status in STATUS_COLOURS else QBrush())

        def apply_filter(self):
            """Show only the rows matching the Show list and the search text."""
            wanted = self.filter.currentData()
            col = STATUS_COL if wanted and wanted[0] == "status" else ISSUE_COL
            needle = self.search_edit.text().strip().lower()
            for r in range(self.table.rowCount()):
                hide = wanted is not None and self.table.item(r, col).text() != wanted[1]
                if not hide and needle:
                    hide = not any(needle in self.table.item(r, c).text().lower() for c in search_cols)
                self.table.setRowHidden(r, hide)
            self.update_summary()
            self.update_buttons()

        def selected_rows(self):
            return sorted({i.row() for i in self.table.selectedItems() if not self.table.isRowHidden(i.row())})

        def select_pending(self):
            """Select every visible row that can still be snapped."""
            self.table.clearSelection()
            mode = self.table.selectionMode()
            self.table.setSelectionMode(QtWidgets.QAbstractItemView.MultiSelection)
            try:
                for r in range(self.table.rowCount()):
                    if not self.table.isRowHidden(r) and self.table.item(r, STATUS_COL).text() in snappable:
                        self.table.selectRow(r)
            finally:
                self.table.setSelectionMode(mode)

        def result_layer(self):
            return self.scan_result.layer if self.scan_result is not None else None

        def fids_for_rows(self, rows):
            return [self.table.item(r, ISSUE_COL).data(FID_ROLE) for r in rows]

        # ------------------------------------------------- list -> map
        def on_table_selection(self):
            self.update_buttons()
            layer, rows = self.result_layer(), self.selected_rows()
            if layer is not None and rows and self.iface is not None:
                self.iface.mapCanvas().flashFeatureIds(layer, self.fids_for_rows(rows))

        # ----------------------------------------------------------- zoom
        def zoom_to_rows(self, rows):
            layer = self.result_layer()
            if layer is None or not rows or self.iface is None:
                return
            fids = self.fids_for_rows(rows)
            extent = None
            for f in layer.getFeatures(QgsFeatureRequest().setFilterFids(fids)):
                box = f.geometry().boundingBox()
                if extent is None:
                    extent = box
                else:
                    extent.combineExtentWith(box)
            if extent is None:
                return
            if max(extent.width(), extent.height()) < MIN_ZOOM_M:  # e.g. a 1 sqm piece: keep some context
                c, h = extent.center(), MIN_ZOOM_M / 2
                extent = QgsRectangle(c.x() - h, c.y() - h, c.x() + h, c.y() + h)
            canvas = self.iface.mapCanvas()
            to_canvas = QgsCoordinateTransform(layer.crs(), canvas.mapSettings().destinationCrs(), QgsProject.instance())
            extent = to_canvas.transformBoundingBox(extent)
            extent.scale(1.5)  # a little context around the discrepancy
            canvas.setExtent(extent)
            canvas.refresh()
            if self.highlight_chk.isChecked():
                canvas.flashFeatureIds(layer, fids, QColor(255, 0, 0, 255), QColor(255, 0, 0, 0), 6, 400)
                self.highlight_rows(rows)
            else:
                self.clear_highlight()
                canvas.flashFeatureIds(layer, fids)  # plain flash

        # ------------------------------------------------- map highlight
        def clear_highlight(self):
            """Remove the dotted outlines / markers and stop their blinking."""
            if self._blink is not None:
                self._blink.stop()
                self._blink.deleteLater()
                self._blink = None
            for b in self.bands:
                try:
                    b.reset()
                    if b.scene() is not None:
                        b.scene().removeItem(b)
                except RuntimeError:  # canvas already gone
                    pass
            self.bands = []

        def highlight_rows(self, rows):
            """Blink a dotted outline and a circle marker on each discrepancy, and a dotted green outline
            of how its Fix feature will look after Snap Selected. They stay drawn after blinking until the
            next zoom, scan, snap or Clear."""
            self.clear_highlight()
            if self.iface is None or self.scan_result is None or not rows:
                return
            from qgis.gui import QgsRubberBand

            canvas = self.iface.mapCanvas()
            to_canvas = QgsCoordinateTransform(QgsCoordinateReferenceSystem(self.scan_result.crs),
                                               canvas.mapSettings().destinationCrs(), QgsProject.instance())

            def band(geom, colour, style, width, kind=QgsWkbTypes.PolygonGeometry, alpha=70):
                g = QgsGeometry()
                g.fromWkb(shapely.to_wkb(shapely.force_2d(geom)))
                g.transform(to_canvas)
                b = QgsRubberBand(canvas, kind)
                b.setToGeometry(g, None)
                fill = QColor(colour)
                fill.setAlpha(alpha)
                b.setStrokeColor(QColor(colour))
                b.setFillColor(fill)
                b.setWidth(width)
                b.setLineStyle(style)
                if kind == QgsWkbTypes.PointGeometry:
                    b.setIcon(QgsRubberBand.ICON_CIRCLE)
                    b.setIconSize(16)
                self.bands.append(b)

            d, ea = self.scan_result.discrepancies, self.scan_result.ea
            df_rows = [self.df_row(r) for r in rows]
            todo = {}
            for r in df_rows:
                colour = HIGHLIGHT_COLOURS.get(d.at[r, "_kind"], HIGHLIGHT_COLOURS["none"])
                band(d.geometry[r], colour, Qt.DashLine, 3)
                # circles on the Fix feature's vertices that are out of place (not at a random inside point)
                band(unsnapped_vertices(self.scan_result, r), colour, Qt.SolidLine, 2, QgsWkbTypes.PointGeometry, 0)
                if d.at[r, "Status"] != STATUS_DONE and bool(d.at[r, "_snappable"]) and d.at[r, "_kind"] != "vertex":
                    todo.setdefault(int(d.at[r, "_ea_idx"]), []).append(r)
            previews = 0
            for idx, rs in list(todo.items())[:MAX_PREVIEWS]:
                cuts, adds, clip = snap_inputs(self.scan_result, idx, rs)
                new, _why = snapped_geometry(ea.geometry[idx], cuts, adds, self.scan_result.min_area, clip=clip)
                if new is not None:
                    band(new, HIGHLIGHT_COLOURS["after"], Qt.DotLine, 3, alpha=35)
                    previews += 1

            state = {"ticks": 0}

            def blink():
                state["ticks"] += 1
                show = state["ticks"] % 2 == 0 or state["ticks"] >= BLINKS
                try:
                    for b in self.bands:
                        b.setVisible(show)
                except RuntimeError:  # canvas items already deleted (project closed)
                    self.clear_highlight()
                    return
                if state["ticks"] >= BLINKS and self._blink is not None:
                    self._blink.stop()

            self._blink = QTimer(self)
            self._blink.timeout.connect(blink)
            self._blink.start(300)
            what = f"; dotted green = {previews} feature(s) after Snap Selected" if previews else ""
            self.log(f"Map: dotted outline + circle = the discrepancy (orange cut, red add, grey fix by hand){what}.")

        # ----------------------------------------------------- switch fix
        def switch_selected(self):
            if self.running or self.scan_result is None:
                return
            d = self.scan_result.discrepancies
            changed = 0
            self.clear_highlight()  # the "after snap" preview was drawn for the old Fix feature
            self.table.setSortingEnabled(False)  # repainting a Status cell must not re-sort the rows under us
            for r in self.selected_rows():
                if not self.can_switch(r):
                    continue
                row = self.df_row(r)
                code = switch_fix(self.scan_result, row)
                if code is None:
                    continue
                changed += 1
                self.table.item(r, COL["Fix EA"]).setText(str(code))
                note = self.table.item(r, COL["Note"])
                note.setText(str(d.at[row, "Note"]))
                note.setToolTip(note.text())
                self.paint_status(r)
                self.log(f"  {d.at[row, 'Issue']} ({d.at[row, 'Area (sqm)']:,.0f} sqm): Fix is now {code}")
            self.table.setSortingEnabled(True)
            if changed:
                self.log(f"Switched the Fix of {changed:,} row(s). Press Snap Selected to apply.")
            self.apply_filter()

        # ----------------------------------------------------------- snap
        def snap_selected(self):
            if self.running or self.scan_result is None:
                return
            table_rows = self.selected_rows()
            by_df_row = {self.df_row(r): r for r in table_rows}
            self.clear_highlight()  # the "after snap" preview no longer applies

            self.log("-" * 50)
            self.log(f"Snapping {len(by_df_row):,} selected discrepanc{'y' if len(by_df_row) == 1 else 'ies'}...")
            self.set_busy(True)
            try:
                outcome = snap_rows(self.scan_result, sorted(by_df_row))
            except Exception as e:  # noqa: BLE001 - report every failure in the log
                self.log(traceback.format_exc().rstrip())
                self.log(f"Snap FAILED: {e}. Some layers may already have been changed: check them, and "
                         "use Undo Last Snap or Edit > Undo if needed.")
                self.update_buttons()
                return
            finally:
                self.set_busy(False)
            outcome.update(self.snap_into_multipart_copies())

            # one log line per feature (rows of the same feature share their message)
            for message in dict.fromkeys(m for _, m in outcome.values()):
                self.log(f"  {message}")
            self.table.setSortingEnabled(False)  # repainting a Status cell must not re-sort the rows
            for df_row, r in by_df_row.items():
                if df_row in outcome:
                    self.paint_status(r)
            self.table.setSortingEnabled(True)
            done = sum(1 for s, _ in outcome.values() if s == STATUS_DONE)
            failed = sum(1 for s, _ in outcome.values() if s != STATUS_DONE)
            self.log(f"Snapped {done:,}, not snapped {failed:,} (hover a Status cell to see why).")
            if done:
                d, ea = self.scan_result.discrepancies, self.scan_result.ea
                ids = {ea.at[int(d.at[r, "_ea_idx"]), "src_layer"] for r, (s, _) in outcome.items()
                       if s == STATUS_DONE}
                names = sorted(lyr.name() for lyr in map(QgsProject.instance().mapLayer, ids) if lyr is not None)
                self.log(f"The changes are UNSAVED edits in {len(names)} layer(s): {', '.join(names)}. "
                         "Keep: Layer > Current Edits > Save for All Layers. Undo: Edit > Undo (per layer).")
            self.apply_filter()

        def snap_into_multipart_copies(self):
            """Rows refused because their feature would get a second part (e.g. an island) in a single-part
            layer: offer to make a multipart copy of that layer, switch the scan to it and snap them again.
            Returns the new outcome of those rows ({} if declined or not needed)."""
            scan = self.scan_result
            pending = dict(scan.needs_multi)
            if not pending:
                return {}
            names = [QgsProject.instance().mapLayer(i).name() for i in pending
                     if QgsProject.instance().mapLayer(i) is not None]
            answer = QtWidgets.QMessageBox.question(
                self, "Single-part layer",
                f"{sum(map(len, pending.values())):,} row(s) would give a {scan.unit} a second part (e.g. an "
                f"island), but these layers are single-part Polygon:\n\n  " + "\n  ".join(names) +
                "\n\nMake a multipart copy of each? It is saved next to the original as <name>_multi.gpkg, "
                "added above it with the same style and used from now on. The original is hidden, not changed.")
            if answer != QtWidgets.QMessageBox.Yes:
                return {}
            outcome, retry = {}, []
            for layer_id, rows in pending.items():
                new, why = make_multipart_copy(scan, layer_id)
                if new is None:
                    self.log(f"  Multipart copy not made: {why}")
                    continue
                self.log(f"  Made multipart copy '{new.name()}' ({new.source().split('|')[0]}); "
                         "it replaces the original in this scan")
                ids = (self.target.checked_ids() - {layer_id}) | {new.id()}
                self.refresh_lists()
                self.target.set_ticks(ids)
                self.manual["target"] = True
                self.sync_lists()
                retry.extend(rows)
            if retry:
                self.set_busy(True)
                try:
                    outcome = snap_rows(scan, sorted(retry))
                finally:
                    self.set_busy(False)
            return outcome

        def match_all(self):
            """Match Vertices: pair the vertices along all shared edges of the scanned layer(s)."""
            if self.running or self.scan_result is None:
                return
            self.clear_highlight()
            try:
                n = match_vertices(self.scan_result)
            except Exception as e:
                self.log(f"Match Vertices failed: {e}")
                self.update_buttons()
                return
            if n:
                self.log(f"Matched the vertices along shared edges: {n:,} feature(s) changed (unsaved edits; "
                         "Undo Last Snap takes it back). Scan again to confirm.")
            else:
                self.log("Match Vertices: every vertex along a shared edge already has its pair.")
            self.update_buttons()

        def undo_snap(self):
            """Undo Last Snap: takes back the last Snap Selected and gives its rows their earlier status."""
            if self.running or self.scan_result is None:
                return
            self.clear_highlight()
            rows, why = undo_last_snap(self.scan_result)
            if rows is None:
                self.log(f"Undo not done: {why}")
                self.update_buttons()
                return
            self.table.setSortingEnabled(False)  # repainting a Status cell must not re-sort the rows
            table_of = {self.df_row(r): r for r in range(self.table.rowCount())}
            for df_row in rows:
                if df_row in table_of:
                    self.paint_status(table_of[df_row])
            self.table.setSortingEnabled(True)
            left = len(self.scan_result.history)
            self.log(f"Undid the last snap: {len(rows):,} row(s) are back to their status before it "
                     f"(normally Not snapped); {left} earlier snap(s) can still be undone.")
            self.apply_filter()

    return CheckDialog


# ================= ENTRY POINT =================
def show_dialog():
    """Open the EA vs Barangay Check window (non-modal, so the map stays usable)."""
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
        # remove the docked panel left by older versions
        from qgis.PyQt.QtWidgets import QDockWidget
        for old in main.findChildren(QDockWidget, "EAvsBarangayCheckDock"):
            qgis_iface.removeDockWidget(old)
            old.deleteLater()

    dialog = make_dialog_class()(qgis_iface, main)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog


if "qgis" in sys.modules and not os.environ.get("EA_QA_NO_DIALOG"):
    _DIALOG = show_dialog()
elif "qgis" not in sys.modules:
    print("Run this script inside QGIS: GMD CO Processing > EA Tools > EA vs Barangay Check.")