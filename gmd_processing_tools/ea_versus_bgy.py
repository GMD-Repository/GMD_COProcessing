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
  Revert  Toggle Editing > Discard, or Edit > Undo (one undo step per layer per Snap Selected)

Only the geometry is changed: attributes such as household counts stay as they are.
Each scan also removes any "Discrepancies ..." scratch layers left in the project by older versions.

Usage:
  1. Load the layers into the project. To check only part of a layer, set a filter on it (right-click > Filter).
  2. GMD CO Processing > EA Tools > EA vs Barangay Check. This opens the "EA vs Barangay Check" window
     (same layout as the MBI EA Selector, plus a Help and Information tab).
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
import pandas as pd
import pyogrio
import shapely
from shapely.geometry import MultiPolygon

warnings.filterwarnings("ignore")

# ================= CONFIG =================
MIN_AREA_SQM = 5.0  # default for the dialog: differences smaller than this are slivers and ignored
TOUCH_TOL_M = 1.0   # a gap within this distance of an EA counts as bordering that EA
MIN_OWNER_SHARE = 0.3  # a gap is added to one EA only if that EA has at least this share of its EA border
GRID_DEG = 1e-9     # fallback precision grid (~0.1 mm) when an exact snap gives an invalid geometry

# Layer-name patterns used, after the fields, to autodetect the layers in the panel.
BGY_LAYER_HINT = r"bgy|barangay"
EA_LAYER_HINT = r"(^|_)ea"

# Overlays run in the layers' own lat/lon so shared vertices and edges stay exact (reprojecting
# first bends straight edges and creates false slivers). Areas use an equal-area projection in
# metres centred on the Philippines.
DATA_CRS = "EPSG:4326"
AREA_CRS = "+proj=laea +lat_0=12 +lon_0=122 +datum=WGS84 +units=m +no_defs"

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
                "overlap": "142,68,173,210"}


def mode_issues(mode, self_check):
    """[(issue label, rgba)] for a mode: the legend, filter and map style of its scan."""
    if self_check:
        return [(SELF_ISSUES[0], KIND_COLOURS["overlap"]), (SELF_ISSUES[1], KIND_COLOURS["add"])]
    out, short, orphan = MODES[mode]["issues"]
    return [(out, KIND_COLOURS["cut"]), (short, KIND_COLOURS["add"]), (orphan, KIND_COLOURS["none"])]


def geometry_hash(geom):
    """Fingerprint of a QGIS geometry, to tell whether a feature changed since the scan."""
    return hash(bytes(geom.asWkb())) if geom is not None and not geom.isNull() else 0


def layer_to_gdf(layer):
    """Copy a QGIS layer (any provider, already in lat/lon) to a temp GeoPackage and read it.

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
    if layer.crs().isValid():
        opts.ct = QgsCoordinateTransform(layer.crs(), QgsCoordinateReferenceSystem(DATA_CRS), QgsProject.instance())
    else:
        warn(f"{layer.name()}: layer has no CRS, assuming EPSG:4326")
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


def explode_pieces(gdf, min_area):
    """Split into single polygons and drop slivers below min_area."""
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]
    gdf = gdf.explode(index_parts=False)
    gdf = gdf[gdf.geom_type == "Polygon"].copy()
    gdf["piece_area"] = area_sqm(gdf)
    return gdf[gdf["piece_area"] >= min_area].reset_index(drop=True)


# ================= COMPARE =================
def uncovered_land(inside, bgy, min_area):
    """The parts of each barangay that are not covered by its EAs."""
    scope = bgy[bgy["key"].isin(inside["key"])]
    cover = inside[["key", "geometry"]].dissolve(by="key").geometry
    cover = gpd.GeoSeries(cover.reindex(scope["key"]).values, index=scope.index, crs=DATA_CRS)
    return explode_pieces(gpd.GeoDataFrame(
        {"key": scope["key"].values}, geometry=scope.geometry.difference(cover).values, crs=DATA_CRS), min_area)


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
    outside = explode_pieces(gpd.GeoDataFrame(
        {"ea_idx": inside.index}, geometry=inside.geometry.difference(own_bgy).values, crs=DATA_CRS), min_area)

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
    note = note.where(borders, f"No {unit} reaches this part of the {ref}; nearest {unit} listed")
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
        "_candidates": [ranked.get(g, []) if b else [] for g, b in zip(gap.index, borders)],
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


def snapped_geometry(geom, cuts, adds, min_area):
    """The EA after cutting off `cuts` and adding `adds`: (geometry, None), or (None, why it failed).

    The exact overlay keeps the EA's own vertices. Its result is rejected if it is invalid or has
    near-zero-width spikes (a vertex almost on another edge): those are valid in lat/lon by a hair but
    break when converted to the layer's CRS or edited. The overlay is then redone on a 0.1 mm, 1 mm and
    1 cm grid, which merges such vertices; the first sturdy result (or else the sturdiest) is used.
    """
    # Clearance = how far a vertex can move before the polygon becomes invalid. The result should not be
    # more fragile than the EA already was, nor below 1 mm.
    needed = min(10 * GRID_DEG, shapely.minimum_clearance(geom) * 0.999)
    reason = "invalid"
    best, best_clearance = None, -1.0
    for grid in (None, GRID_DEG, 10 * GRID_DEG, 100 * GRID_DEG):
        try:
            g = geom
            if cuts:
                g = shapely.difference(g, shapely.union_all(cuts, grid_size=grid), grid_size=grid)
            if adds:
                g = shapely.union(g, shapely.union_all(adds, grid_size=grid), grid_size=grid)
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
        if g.is_valid and not g.is_empty:
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


def to_layer_geometry(geom, ea_layer):
    """Shapely geometry (lat/lon) -> valid QgsGeometry in the EA layer's CRS and geometry type, or (None, why)."""
    from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsGeometry, QgsProject,
                           QgsWkbTypes)

    g = QgsGeometry()
    g.fromWkb(shapely.to_wkb(shapely.force_2d(geom)))
    crs = ea_layer.crs()
    if crs.isValid() and crs.authid() != DATA_CRS:
        g.transform(QgsCoordinateTransform(QgsCoordinateReferenceSystem(DATA_CRS), crs, QgsProject.instance()))
    if g.isNull() or g.isEmpty():
        return None, "it is empty in the EA layer's CRS"

    if not g.isGeosValid():
        # Converting coordinates can leave near-duplicate vertices or hairline self-touches: try to repair
        # them, and accept a repair only if it is valid polygon geometry with the same area.
        grid = 1e-9 if crs.isGeographic() else 1e-4
        candidates = [g.snappedToGrid(grid, grid), g.makeValid()]
        fixed = None
        for c in candidates:
            if c is None or c.isNull() or c.isEmpty():
                continue
            if c.type() != QgsWkbTypes.PolygonGeometry:
                c = c.convertToType(QgsWkbTypes.PolygonGeometry, True)
                if c is None or c.isNull():
                    continue
            if c.isGeosValid() and abs(c.area() - g.area()) <= 1e-3 * abs(g.area()):
                fixed = c
                break
        if fixed is None:
            return None, "it becomes invalid when converted to the EA layer's CRS"
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
        self.ea = ea                        # target features as scanned (lat/lon; src_layer = the id of the
                                            # layer each came from); updated after each snap
        self.discrepancies = discrepancies  # one row per discrepancy; Status updated after each snap
        self.layer = layer                  # in-memory Discrepancies layer, not in the project (None if none)
        self.fids = fids                    # its feature id for each discrepancy row
        self.min_area = min_area
        self.mode = mode
        self.self_check = self_check        # True: one layer checked against itself (overlaps / gaps)
        self.issues = mode_issues(mode, self_check)
        self.unit = MODES[mode]["unit"]
        self.messages = {}                  # row -> why it failed / what changed


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


def snap_rows(scan, rows):
    """Snap the selected discrepancy rows. Returns {row: (status, message)} for the rows it handled.

    Rows are grouped by the feature they change, so each feature is edited once, in the layer it came
    from. The changes go to each layer's edit buffer as one undo step per layer; nothing is saved.
    """
    from qgis.core import QgsProject, QgsWkbTypes

    d, ea, unit = scan.discrepancies, scan.ea, scan.unit
    Unit = unit[0].upper() + unit[1:]
    outcome, todo = {}, {}
    for r in rows:
        status = d.at[r, "Status"]
        if status == STATUS_DONE:
            continue
        if not bool(d.at[r, "_snappable"]):
            outcome[r] = (STATUS_MANUAL, f"Not snapped: {d.at[r, 'Note']}")
        else:
            todo.setdefault(int(d.at[r, "_ea_idx"]), []).append(r)

    edits = {}  # layer id -> the edits to make in that layer
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

        cut_rows = [r for r in ea_rows if d.at[r, "_kind"] == "cut"]
        add_rows = [r for r in ea_rows if d.at[r, "_kind"] == "add"]
        old = ea.geometry[idx]
        new, why = snapped_geometry(old, [d.geometry[r] for r in cut_rows], [d.geometry[r] for r in add_rows],
                                    scan.min_area)
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
                 f"To allow it: Export > Save Features As, tick 'Force to multi-type', and scan that layer")
            continue

        geom, why = to_layer_geometry(new, ea_layer)
        if geom is None:
            fail(why)
            continue
        edits.setdefault(layer_id, []).append((idx, ea_rows, fid, geom, new, old_area, new_area))

    what = "overlaps / gaps" if scan.self_check else f"{MODES[scan.mode]['ref_unit']} boundaries"
    for layer_id, layer_edits in edits.items():
        ea_layer = QgsProject.instance().mapLayer(layer_id)
        if not ea_layer.isEditable() and not ea_layer.startEditing():
            for _, ea_rows, *_ in layer_edits:
                outcome.update({r: (STATUS_FAILED, f"'{ea_layer.name()}' cannot be edited") for r in ea_rows})
            continue
        ea_layer.beginEditCommand(f"Snap {len(layer_edits)} {unit}(s) to {what}")
        for idx, ea_rows, fid, geom, new, old_area, new_area in layer_edits:
            code = ea.at[idx, "EA Geocode"]
            if ea_layer.changeGeometry(fid, geom):
                ea.at[idx, "geometry"] = new
                ea.at[idx, "src_hash"] = geometry_hash(ea_layer.getFeature(fid).geometry())
                msg = f"{Unit} {code} snapped: {old_area:,.0f} -> {new_area:,.0f} sqm"
                outcome.update({r: (STATUS_DONE, msg) for r in ea_rows})
            else:
                outcome.update({r: (STATUS_FAILED, f"{Unit} {code} not changed: the layer refused the edit")
                                for r in ea_rows})
        ea_layer.endEditCommand()
        ea_layer.triggerRepaint()

    # Record the result in the scan, and in the Discrepancies layer's Status field.
    for r, (status, message) in outcome.items():
        d.at[r, "Status"] = status
        scan.messages[r] = message
    _set_layer_values(scan, list(outcome), ["Status"])
    return outcome


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

    layer, fids = None, []
    if len(discrepancies):
        step("Creating the output layer...")
        layer, fids = to_memory_layer(discrepancies, f"Discrepancies {datetime.now():%Y-%m-%d %H:%M:%S}",
                                      mode_issues(mode, self_check))
    return Scan(ea, discrepancies, layer, fids, min_area, mode, self_check)


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
HIGHLIGHT_COLOURS = {"cut": "#ff6d00", "add": "#d50000", "none": "#616161", "after": "#00c853"}
BLINKS = 12          # on/off steps of 300 ms, then the outlines stay drawn
MAX_PREVIEWS = 50    # most "after snap" outlines drawn at once
MIN_ZOOM_DEG = 0.0005  # ~55 m: zooming to a tiny piece (e.g. 1 sqm) still shows its surroundings

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
        blinks a <b>dotted outline with a circle</b> on the discrepancy (orange = cut off, red = added) and a
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
  <p style="margin:6px 0 0 0;"><b>Ignore slivers below</b>: differences smaller than this area (sqm) are treated
     as slivers and not listed.</p>

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
                            "the longest border with it (all bordering features are named)."),
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
                     "because no feature had most of its border."),
      ("Snap Selected", "Changes the features named in Fix for the selected rows. Unsaved, undoable edits."),
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
        and the feature must not have been edited since the scan. Otherwise the row is marked Failed.</li>
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
            # Set when run from the GMD CO Processing panel. That combo often just holds the first
            # layer, so it is used only if it looks like an EA layer.
            panel_layer = globals().get("ea_layer")
            if panel_layer is not None and panel_layer.id() in self.target.likely_ids():
                self.target.set_ticks([panel_layer.id()])
                self.manual["target"] = True
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

            box, lay = group("Settings")
            srow = QtWidgets.QHBoxLayout()
            srow.addWidget(QtWidgets.QLabel("Ignore slivers below:"))
            self.min_area = QtWidgets.QDoubleSpinBox()
            self.min_area.setRange(0, 1e7)
            self.min_area.setDecimals(2)
            self.min_area.setValue(MIN_AREA_SQM)
            self.min_area.setSuffix(" sqm")
            self.min_area.setToolTip("Differences smaller than this area are treated as slivers and not listed.")
            srow.addWidget(self.min_area, 1)
            lay.addLayout(srow)
            ll.addWidget(box)

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
            arow.addWidget(self.select_btn, 1)
            arow.addWidget(self.zoom_button, 1)
            arow.addWidget(self.switch_btn, 1)
            arow.addWidget(self.snap_button, 2)
            gf.addLayout(arow)
            self.highlight_chk = QtWidgets.QCheckBox("Blinking dotted outline and snap preview on zoom")
            self.highlight_chk.setChecked(True)
            self.highlight_chk.setToolTip(
                "On: zooming (double-click / Zoom to Selected) blinks a dotted outline and circle on the\n"
                "discrepancy and a dotted green outline of the Fix feature after Snap Selected.\n"
                "Off: only the plain red flash.")
            self.highlight_chk.toggled.connect(lambda on: None if on else self.clear_highlight())
            gf.addWidget(self.highlight_chk)
            self.snap_hint =self._hint("Snapping leaves the checked layer in edit mode: Save Layer Edits to keep "
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
            for w in [self.target, self.ref, self.min_area, self.table, self.filter, self.search_edit,
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
            self.snap_group.setEnabled(has_rows and idle)
            self.zoom_btn.setEnabled(bool(rows) and idle)
            self.zoom_button.setEnabled(bool(rows) and idle)
            self.snap_button.setEnabled(bool(statuses & snappable) and idle)
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
                     f"slivers < {self.min_area.value():g} sqm")
            self.log("  Checked: " + ", ".join(lyr.name() for lyr in targets))
            if refs:
                self.log("  Reference: " + ", ".join(lyr.name() for lyr in refs))
            self.set_busy(True)
            stream = _LogStream(self.log)
            try:
                with redirect_stdout(stream), redirect_stderr(stream):
                    scan = run_check(refs, targets, self.min_area.value(), mode=mode)
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
            if max(extent.width(), extent.height()) < MIN_ZOOM_DEG:  # e.g. a 1 sqm piece: keep some context
                c, h = extent.center(), MIN_ZOOM_DEG / 2
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
                self._blink = None
            for b in self.bands:
                try:
                    b.scene().removeItem(b) if b.scene() is not None else None
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
            to_canvas = QgsCoordinateTransform(QgsCoordinateReferenceSystem(DATA_CRS),
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
                    b.setIconSize(22)
                self.bands.append(b)

            d, ea = self.scan_result.discrepancies, self.scan_result.ea
            df_rows = [self.df_row(r) for r in rows]
            todo = {}
            for r in df_rows:
                colour = HIGHLIGHT_COLOURS.get(d.at[r, "_kind"], HIGHLIGHT_COLOURS["none"])
                band(d.geometry[r], colour, Qt.DashLine, 3)
                band(d.geometry[r].representative_point(), colour, Qt.SolidLine, 3, QgsWkbTypes.PointGeometry, 0)
                if d.at[r, "Status"] != STATUS_DONE and bool(d.at[r, "_snappable"]):
                    todo.setdefault(int(d.at[r, "_ea_idx"]), []).append(r)
            previews = 0
            for idx, rs in list(todo.items())[:MAX_PREVIEWS]:
                new, _why = snapped_geometry(ea.geometry[idx],
                                             [d.geometry[r] for r in rs if d.at[r, "_kind"] == "cut"],
                                             [d.geometry[r] for r in rs if d.at[r, "_kind"] == "add"],
                                             self.scan_result.min_area)
                if new is not None:
                    band(new, HIGHLIGHT_COLOURS["after"], Qt.DotLine, 3, alpha=35)
                    previews += 1

            state = {"ticks": 0}

            def blink():
                state["ticks"] += 1
                show = state["ticks"] % 2 == 0 or state["ticks"] >= BLINKS
                for b in self.bands:
                    b.setVisible(show)
                if state["ticks"] >= BLINKS and self._blink is not None:
                    self._blink.stop()

            self._blink = QTimer(self)
            self._blink.timeout.connect(blink)
            self._blink.start(300)
            what = f"; dotted green = {previews} feature(s) after Snap Selected" if previews else ""
            self.log(f"Map: dotted outline + circle = the discrepancy (orange cut, red add){what}.")

        # ----------------------------------------------------- switch fix
        def switch_selected(self):
            if self.running or self.scan_result is None:
                return
            d = self.scan_result.discrepancies
            changed = 0
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
                self.log(f"Snap FAILED: {e}. The layer was not changed by this step.")
                return
            finally:
                self.set_busy(False)

            # one log line per feature (rows of the same feature share their message)
            for message in dict.fromkeys(m for _, m in outcome.values()):
                self.log(f"  {message}")
            for df_row, r in by_df_row.items():
                if df_row in outcome:
                    self.paint_status(r)
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
