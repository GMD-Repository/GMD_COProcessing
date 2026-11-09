r"""
EA vs Barangay boundary check (QGIS).

The barangay boundary is fixed; only the EA is snapped to it. The output is only the discrepancies:
each feature is just the mismatched area, carrying the attributes of the EA that must be fixed.

  Outside BGY           the part of the EA that falls outside its barangay
  Short of BGY          the barangay land its EA does not reach (all EAs bordering it are listed)
  No matching barangay  the EA's barangay geocode is not in the barangay layer (the whole EA)

The panel's list shows them, largest first. No layer is added to the project: the discrepancies
are kept in memory by the panel, for zooming and snapping. Barangays with no EA have nothing to list, so they are named in the log instead.

Snapping is done per discrepancy: select rows in the list and press "Snap Selected". Only the EA named in the "Fix EA" column is changed:

  - Outside BGY pieces are cut off the EA.
  - Short of BGY pieces are added to the EA sharing the longest border with them.
  - Holes and parts smaller than the sliver setting that the edit leaves behind are removed.

Before an EA is changed, the new geometry must be valid, not empty, have the expected area, still be valid
in the EA layer's CRS, fit the layer (single-part layers cannot take several parts), and the EA must not
have been edited since the scan. Otherwise the row is marked Failed with the reason and the EA is untouched.

The EA layer is left in edit mode and nothing is written to its file until you save:

  Keep    Save Layer Edits
  Revert  Toggle Editing > Discard, or Edit > Undo (one undo step per Snap Selected)

Only the geometry is changed: attributes such as household counts stay as they are.
Each scan also removes any "Discrepancies ..." scratch layers left in the project by older versions.

Usage:
  1. Load the barangay layer and the EA layer into the project.
     To check only part of a layer, set a filter on it (right-click > Filter).
  2. GMD CO Processing > EA Tools > EA vs Barangay Check.
     This opens the "EA vs Barangay Check" panel beside the map.
  3. Pick the two layers and press Scan. Progress, warnings and the summary appear in the Log.
  4. The Discrepancies list shows every issue: click a row to flash it on the map,
     double-click to zoom to it. Select the rows to fix and press Snap Selected.
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

# Layer-name patterns used to preselect layers in the dialog.
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


def prepare_bgy(gdf):
    f = lambda k: pick(gdf, BGY_FIELDS[k])
    return gpd.GeoDataFrame({
        "key": f("geocode").map(bgy_key),
        "Geocode": f("geocode").map(full_geocode),
        "Region": f("region"),
        "Province": f("province"),
        "City/Municipality": f("city_mun"),
        "Barangay": f("barangay"),
    }, geometry=gdf.geometry, crs=gdf.crs)


def prepare_ea(gdf):
    f = lambda k: pick(gdf, EA_FIELDS[k])
    ea_code = f("ea_geocode")
    return gpd.GeoDataFrame({
        "key": f("bgy_geocode").map(bgy_key).fillna(ea_code.map(bgy_key)),
        "src_fid": gdf["src_fid"],
        "src_hash": gdf["src_hash"],
        "EA Geocode": ea_code.map(full_geocode),
        "EA": f("name"),
        "ea_Region": f("region"),
        "ea_Province": f("province"),
        "ea_City/Municipality": f("city_mun"),
        "ea_Barangay": f("barangay"),
    }, geometry=gdf.geometry, crs=gdf.crs)


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
        warn(f"{name}: skipped {int(missing.sum())} feature(s) without a usable barangay geocode")
    return gpd.GeoDataFrame(out[~missing].reset_index(drop=True), geometry="geometry", crs=DATA_CRS)


def merge_duplicate_barangays(bgy):
    """One feature per barangay: features sharing a geocode are merged."""
    multi = bgy["key"].duplicated(keep=False)
    if multi.any():
        warn(f"{bgy.loc[multi, 'key'].nunique():,} barangay geocode(s) appear more than once and were merged")
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


def find_discrepancies(ea, bgy, min_area):
    """The barangay boundary is fixed, so every discrepancy is reported on the EA that must be snapped to it.

    Returns one row per discrepancy, largest first: only the mismatched area, with the EA's attributes.
    Hidden columns (starting with "_") say which EA a snap changes. Also returns the barangays with no EA.
    """
    ea = ea.reset_index(drop=True)
    ea["ea_area"] = area_sqm(ea)
    bgy_by_key = bgy.set_index("key")
    for c in ADMIN:  # the EA's own names; the barangay layer only fills in what the EA layer lacks
        ea[c] = ea[f"ea_{c}"].where(ea[f"ea_{c}"].notna(), ea["key"].map(bgy_by_key[c]))
    has_bgy = ea["key"].isin(bgy_by_key.index)
    inside = ea[has_bgy]

    # ===== OUTSIDE: parts of each EA beyond its own barangay =====
    own_bgy = gpd.GeoSeries(bgy_by_key.geometry.reindex(inside["key"]).values, index=inside.index, crs=DATA_CRS)
    outside = explode_pieces(gpd.GeoDataFrame(
        {"ea_idx": inside.index}, geometry=inside.geometry.difference(own_bgy).values, crs=DATA_CRS), min_area)

    into = pd.Series("", index=outside.index, dtype=object)
    if len(outside):
        pts = gpd.GeoDataFrame(geometry=outside.representative_point(), crs=DATA_CRS)
        hit = gpd.sjoin(pts, bgy[["Geocode", "Barangay", "geometry"]], predicate="within", how="left")
        hit = hit[~hit.index.duplicated()]
        into = hit["Barangay"].astype(str) + " (" + hit["Geocode"].astype(str) + ")"
        into = into.where(hit["Geocode"].notna(), "an area outside the barangay layer")

    src = ea.loc[outside["ea_idx"]].reset_index(drop=True)
    outside_out = gpd.GeoDataFrame({
        "Issue": "Outside BGY",
        "EA Geocode": src["EA Geocode"],
        **{c: src[c] for c in ADMIN},
        "EA": src["EA"],
        "Area (sqm)": outside["piece_area"],
        "Fix EA": src["EA Geocode"],
        "Status": STATUS_PENDING,
        "Note": "EA extends into " + into,
        "_ea_idx": outside["ea_idx"].values,
        "_snappable": True,
    }, geometry=outside.geometry.values, crs=DATA_CRS)

    # ===== SHORT: barangay land its EAs do not reach, named after the EA(s) that must reach it =====
    gap = uncovered_land(inside, bgy, min_area)
    links = link_gaps_to_eas(gap, inside)

    def joined(col):
        values = links.assign(value=ea[col].reindex(links["ea_idx"]).values).groupby("gap")["value"]
        return values.agg(lambda s: ", ".join(sorted(set(s.dropna().astype(str))))).reindex(gap.index)

    src = ea.loc[links.groupby("gap")["ea_idx"].first().reindex(gap.index).astype(int)].reset_index(drop=True)
    borders = links.groupby("gap")["borders"].first().reindex(gap.index).astype(bool)
    owner, share = gap_owners(gap, links, inside)
    owner = owner.reindex(gap.index).astype(int)  # the EA a snap extends
    share = share.reindex(gap.index).fillna(1.0)
    n_eas = links.groupby("gap").size().reindex(gap.index).fillna(0).astype(int)
    # A gap along several EAs, none of which has most of it (e.g. a long hairline strip), would wrap around
    # the neighbours if it were added to one EA: that must be split by hand.
    one_owner = share >= MIN_OWNER_SHARE
    snappable = borders & one_owner
    note = pd.Series("EA does not reach its barangay boundary here", index=gap.index, dtype=object)
    note = note.where(borders, "No EA reaches this part of the barangay; nearest EA listed")
    note = note.where(~borders | one_owner,
                      "Borders " + n_eas.astype(str) + " EAs and none has most of it: split it between them by hand")
    short_out = gpd.GeoDataFrame({
        "Issue": "Short of BGY",
        "EA Geocode": joined("EA Geocode"),
        **{c: src[c] for c in ADMIN},
        "EA": joined("EA"),
        "Area (sqm)": gap["piece_area"],
        "Fix EA": pd.Series(ea["EA Geocode"].reindex(owner.values).values, index=gap.index).where(snappable, ""),
        "Status": pd.Series(STATUS_PENDING, index=gap.index, dtype=object).where(snappable, STATUS_MANUAL),
        "Note": note,
        "_ea_idx": owner.values,
        "_snappable": snappable.values,
    }, geometry=gap.geometry.values, crs=DATA_CRS)

    # ===== EAs WITH NO BARANGAY: the whole EA is the discrepancy =====
    orphan = ea[~has_bgy].reset_index(drop=True)
    orphan_out = gpd.GeoDataFrame({
        "Issue": "No matching barangay",
        "EA Geocode": orphan["EA Geocode"],
        **{c: orphan[c] for c in ADMIN},
        "EA": orphan["EA"],
        "Area (sqm)": orphan["ea_area"],
        "Fix EA": "",
        "Status": STATUS_MANUAL,
        "Note": "Barangay geocode " + orphan["key"] + "000000 is not in the barangay layer",
        "_ea_idx": -1,
        "_snappable": False,
    }, geometry=orphan.geometry.values, crs=DATA_CRS)

    found = [f for f in (outside_out, short_out, orphan_out) if len(f)]
    out = pd.concat(found, ignore_index=True) if found else short_out
    out = out.sort_values("Area (sqm)", ascending=False, kind="stable").reset_index(drop=True)

    no_ea = bgy[bgy["key"].str[:5].isin(set(ea["key"].str[:5])) & ~bgy["key"].isin(inside["key"])]
    return gpd.GeoDataFrame(out, geometry="geometry", crs=DATA_CRS), no_ea


def gap_owners(gap, links, inside):
    """The EA that receives each gap (the one sharing the longest border with it), and that EA's share
    of the gap's border with all EAs (1.0 when a single EA borders it)."""
    gap_m, ea_m = gap.geometry.to_crs(AREA_CRS), inside.geometry.to_crs(AREA_CRS)
    links = links.assign(shared=0.0)
    tied = links["gap"].duplicated(keep=False)
    for row in links[tied].itertuples():
        links.at[row.Index, "shared"] = gap_m[row.gap].boundary.intersection(
            ea_m[row.ea_idx].buffer(TOUCH_TOL_M)).length
    best = links.sort_values("shared", ascending=False, kind="stable").drop_duplicates("gap").set_index("gap")
    total = links.groupby("gap")["shared"].sum().reindex(best.index)
    share = (best["shared"] / total).where(total > 0, 1.0)
    return best["ea_idx"], share


def warn_bgy_overlaps(bgy, min_area):
    """The barangay layer is taken as fixed, so where barangays overlap, EAs snapped to them overlap as well."""
    named = gpd.GeoDataFrame({"Barangay": bgy["Barangay"].where(bgy["Barangay"].notna(), bgy["Geocode"])},
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
        warn(f"{len(pairs):,} pair(s) of barangays overlap in the barangay layer, so EAs snapped to them "
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

    def __init__(self, ea_layer, ea, discrepancies, layer, fids, min_area):
        self.ea_layer = ea_layer
        self.ea = ea                        # EAs as scanned (lat/lon); updated after each snap
        self.discrepancies = discrepancies  # one row per discrepancy; Status updated after each snap
        self.layer = layer                  # in-memory Discrepancies layer, not in the project (None if none)
        self.fids = fids                    # its feature id for each discrepancy row
        self.min_area = min_area
        self.messages = {}                  # row -> why it failed / what changed


def snap_rows(scan, rows):
    """Snap the selected discrepancy rows. Returns {row: (status, message)} for the rows it handled.

    Rows are grouped by the EA they change, so each EA is edited once. All changes go to the EA layer's
    edit buffer as one undo step; nothing is saved.
    """
    from qgis.core import QgsWkbTypes

    d, ea, ea_layer = scan.discrepancies, scan.ea, scan.ea_layer
    outcome, todo = {}, {}
    for r in rows:
        status = d.at[r, "Status"]
        if status == STATUS_DONE:
            continue
        if not bool(d.at[r, "_snappable"]):
            outcome[r] = (STATUS_MANUAL, f"Not snapped: {d.at[r, 'Note']}")
        else:
            todo.setdefault(int(d.at[r, "_ea_idx"]), []).append(r)

    multi_layer = QgsWkbTypes.isMultiType(ea_layer.wkbType())
    edits = []
    for idx, ea_rows in todo.items():
        code = ea.at[idx, "EA Geocode"]

        def fail(message):
            outcome.update({r: (STATUS_FAILED, f"EA {code} not changed: {message}") for r in ea_rows})

        fid = int(ea.at[idx, "src_fid"])
        feature = ea_layer.getFeature(fid)
        if not feature.isValid():
            fail("it is no longer in the layer")
            continue
        if geometry_hash(feature.geometry()) != ea.at[idx, "src_hash"]:
            fail("it was edited after the scan. Scan again")
            continue

        cut_rows = [r for r in ea_rows if d.at[r, "Issue"] == "Outside BGY"]
        add_rows = [r for r in ea_rows if d.at[r, "Issue"] == "Short of BGY"]
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
        if parts > 1 and not multi_layer:
            fail(f"it would have {parts} parts, but '{ea_layer.name()}' is a single-part Polygon layer. "
                 f"To allow it: Export > Save Features As, tick 'Force to multi-type', and scan that layer")
            continue

        geom, why = to_layer_geometry(new, ea_layer)
        if geom is None:
            fail(why)
            continue
        edits.append((idx, ea_rows, fid, geom, new, old_area, new_area))

    if edits:
        if not ea_layer.isEditable() and not ea_layer.startEditing():
            for _, ea_rows, *_ in edits:
                outcome.update({r: (STATUS_FAILED, f"'{ea_layer.name()}' cannot be edited") for r in ea_rows})
            edits = []
    if edits:
        ea_layer.beginEditCommand(f"Snap {len(edits)} EA(s) to barangay boundaries")
        for idx, ea_rows, fid, geom, new, old_area, new_area in edits:
            code = ea.at[idx, "EA Geocode"]
            if ea_layer.changeGeometry(fid, geom):
                ea.at[idx, "geometry"] = new
                ea.at[idx, "src_hash"] = geometry_hash(ea_layer.getFeature(fid).geometry())
                msg = f"EA {code} snapped: {old_area:,.0f} -> {new_area:,.0f} sqm"
                outcome.update({r: (STATUS_DONE, msg) for r in ea_rows})
            else:
                outcome.update({r: (STATUS_FAILED, f"EA {code} not changed: the layer refused the edit")
                                for r in ea_rows})
        ea_layer.endEditCommand()
        ea_layer.triggerRepaint()

    # Record the result in the scan, and in the Discrepancies layer's Status field.
    changes = {}
    status_field = scan.layer.fields().indexOf("Status") if scan.layer is not None else -1
    for r, (status, message) in outcome.items():
        d.at[r, "Status"] = status
        scan.messages[r] = message
        if status_field >= 0:
            changes[scan.fids[r]] = {status_field: status}
    if changes:
        scan.layer.dataProvider().changeAttributeValues(changes)
        scan.layer.triggerRepaint()
    return outcome


# ================= QGIS OUTPUT =================
ISSUE_COLOURS = [("Outside BGY", "255,127,0,210"), ("Short of BGY", "227,26,28,210"),
                 ("No matching barangay", "158,158,158,210")]


def categorize(lyr, field, colours):
    from qgis.core import QgsCategorizedSymbolRenderer, QgsFillSymbol, QgsRendererCategory

    def fill(color):
        return QgsFillSymbol.createSimple({"color": color, "outline_color": "35,35,35,255", "outline_width": "0.2"})

    lyr.setRenderer(QgsCategorizedSymbolRenderer(
        field, [QgsRendererCategory(value, fill(color), value) for value, color in colours]))


def to_memory_layer(df, name):
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
    categorize(lyr, "Issue", ISSUE_COLOURS)
    return lyr, [f.id() for f in added]  # feature ids in the same order as the rows of df


RESULT_LAYER_TAG = "gmd_ea_vs_bgy/discrepancies"
RESULT_LAYER_NAME = re.compile(r"^Discrepancies \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


# ================= RUN =================
def run_check(bgy_layer, ea_layer, min_area, step=print):
    """Finds the discrepancies and returns a Scan (its layer is kept in memory, not added to the project).

    Nothing in the EA layer is changed: use snap_rows() on the discrepancies to fix.
    """
    WARNINGS.clear()
    step("Loading barangay layer...")
    bgy = merge_duplicate_barangays(load_layer(bgy_layer, prepare_bgy, BGY_FIELDS["geocode"]))
    step("Loading EA layer...")
    ea = load_layer(ea_layer, prepare_ea, EA_FIELDS["bgy_geocode"] + EA_FIELDS["ea_geocode"])
    step("Comparing EAs to barangays...")
    discrepancies, no_ea = find_discrepancies(ea, bgy, min_area)
    warn_bgy_overlaps(bgy, min_area)

    if len(no_ea):
        names = [f"{row['Barangay']} ({row['Geocode']})" for _, row in no_ea.head(10).iterrows()]
        more = f" ... and {len(no_ea) - 10:,} more" if len(no_ea) > 10 else ""
        warn(f"{len(no_ea):,} barangay(s) have no EA, so they are not in the output: {', '.join(names)}{more}")

    layer, fids = None, []
    if len(discrepancies):
        step("Creating the output layer...")
        layer, fids = to_memory_layer(discrepancies, f"Discrepancies {datetime.now():%Y-%m-%d %H:%M:%S}")
    return Scan(ea_layer, ea, discrepancies, layer, fids, min_area)


# ================= RESULT TEXT =================
def result_message(discrepancies, include_warnings=True):
    if len(discrepancies):
        counts = discrepancies["Issue"].value_counts()
        msg = f"Discrepancies found: {len(discrepancies):,}\n"
        msg += "\n".join(f"  {k}: {v:,}" for k, v in counts.items())
        manual = int((discrepancies["Status"] == STATUS_MANUAL).sum())
        if manual:
            msg += f"\n  ({manual:,} must be fixed by hand)"
        msg += "\n\nTo fix: select rows in the list and press Snap Selected."
    else:
        msg = "All EAs are snapped to their barangay boundaries."
    if include_warnings and WARNINGS:
        msg += "\n\nWarnings:\n" + "\n".join(WARNINGS)
    return msg


# ================= PANEL (UI) =================
PANEL_NAME = "EAvsBarangayCheckDock"
PANEL_TITLE = "EA vs Barangay Check"
TABLE_COLUMNS = ["Issue", "Status", "EA Geocode", "Fix EA", "EA", "Barangay", "City/Municipality", "Area (sqm)", "Note"]
STATUS_COLOURS = {STATUS_DONE: "#2e7d32", STATUS_FAILED: "#c62828", STATUS_MANUAL: "#757575"}


class _LogStream:
    """print() target during a scan: sends each line to the panel log."""

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


def make_panel_class():
    from qgis.core import QgsApplication, QgsCoordinateTransform, QgsFeatureRequest, QgsProject
    from qgis.gui import QgsMapLayerComboBox
    from qgis.PyQt import QtWidgets
    from qgis.PyQt.QtCore import Qt
    from qgis.PyQt.QtGui import QBrush, QColor, QFont, QFontDatabase

    try:
        from qgis.core import Qgis
        polygon_filter = Qgis.LayerFilter.PolygonLayer
    except AttributeError:  # QGIS < 3.34
        from qgis.core import QgsMapLayerProxyModel
        polygon_filter = QgsMapLayerProxyModel.PolygonLayer

    FID_ROLE = Qt.UserRole          # on the Issue cell: feature id in the Discrepancies layer
    ROW_ROLE = Qt.UserRole + 1      # on the Issue cell: row in scan.discrepancies
    ISSUE_COL, STATUS_COL = TABLE_COLUMNS.index("Issue"), TABLE_COLUMNS.index("Status")
    issue_colours = {issue: QColor(*[int(v) for v in rgba.split(",")[:3]], 70) for issue, rgba in ISSUE_COLOURS}
    snappable = {STATUS_PENDING, STATUS_FAILED}

    def layer_combo(hint):
        combo = QgsMapLayerComboBox()
        combo.setFilters(polygon_filter)
        for i in range(combo.count()):
            if combo.layer(i) is not None and re.search(hint, combo.layer(i).name(), re.I):
                combo.setLayer(combo.layer(i))
                break
        return combo

    class CheckPanel(QtWidgets.QDockWidget):
        def __init__(self, iface, parent=None):
            super().__init__(PANEL_TITLE, parent)
            self.setObjectName(PANEL_NAME)
            self.iface = iface
            self.scan_result = None
            self.running = False

            body = QtWidgets.QWidget()
            self.setWidget(body)
            layout = QtWidgets.QVBoxLayout(body)

            # ---- inputs
            inputs = QtWidgets.QGroupBox("Input")
            form = QtWidgets.QFormLayout(inputs)
            self.bgy = layer_combo(BGY_LAYER_HINT)
            form.addRow("Barangay layer", self.bgy)
            self.ea = layer_combo(EA_LAYER_HINT)
            panel_layer = globals().get("ea_layer")  # set when run from the GMD CO Processing panel
            if panel_layer is not None and self.ea.findText(panel_layer.name()) >= 0:
                self.ea.setLayer(panel_layer)
            form.addRow("EA layer", self.ea)
            self.min_area = QtWidgets.QDoubleSpinBox()
            self.min_area.setRange(0, 1e7)
            self.min_area.setDecimals(2)
            self.min_area.setValue(MIN_AREA_SQM)
            self.min_area.setSuffix(" sqm")
            form.addRow("Ignore slivers below", self.min_area)
            layout.addWidget(inputs)

            # ---- scan button + busy bar
            row = QtWidgets.QHBoxLayout()
            self.scan_button = QtWidgets.QPushButton(QgsApplication.getThemeIcon("/mActionStart.svg"), "Scan")
            self.scan_button.setDefault(True)
            self.scan_button.clicked.connect(self.scan)
            self.progress = QtWidgets.QProgressBar()
            self.progress.setTextVisible(False)
            self.progress.setRange(0, 1)
            self.progress.setValue(0)
            row.addWidget(self.scan_button)
            row.addWidget(self.progress, 1)
            layout.addLayout(row)

            splitter = QtWidgets.QSplitter(Qt.Vertical)

            # ---- results list
            results = QtWidgets.QGroupBox("Discrepancies")
            rl = QtWidgets.QVBoxLayout(results)
            top = QtWidgets.QHBoxLayout()
            top.addWidget(QtWidgets.QLabel("Show"))
            self.filter = QtWidgets.QComboBox()
            self.filter.addItem("All", None)
            for issue, _ in ISSUE_COLOURS:
                self.filter.addItem(issue, ("issue", issue))
            for status in (STATUS_PENDING, STATUS_DONE, STATUS_FAILED, STATUS_MANUAL):
                self.filter.addItem(status, ("status", status))
            self.filter.currentIndexChanged.connect(self.apply_filter)
            top.addWidget(self.filter)
            top.addStretch()
            self.count_label = QtWidgets.QLabel("Not scanned yet")
            top.addWidget(self.count_label)
            rl.addLayout(top)

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
            rl.addWidget(self.table)

            hint = QtWidgets.QLabel("Click: select on map · Double-click: zoom · Ctrl/Shift+click: select several")
            hint.setEnabled(False)
            rl.addWidget(hint)
            buttons = QtWidgets.QHBoxLayout()
            self.zoom_button = QtWidgets.QPushButton(QgsApplication.getThemeIcon("/mActionZoomToSelected.svg"),
                                                     "Zoom to Selected")
            self.zoom_button.clicked.connect(lambda: self.zoom_to_rows(self.selected_rows()))
            buttons.addWidget(self.zoom_button)
            buttons.addStretch()
            self.snap_button = QtWidgets.QPushButton(QgsApplication.getThemeIcon("/mIconSnapping.svg"), "Snap Selected")
            self.snap_button.setToolTip("Snap the EAs of the selected discrepancies to their barangay boundary.\n"
                                        "Changes are unsaved edits in the EA layer (Edit > Undo to take them back).")
            self.snap_button.clicked.connect(self.snap_selected)
            buttons.addWidget(self.snap_button)
            rl.addLayout(buttons)
            splitter.addWidget(results)

            # ---- log
            log_box = QtWidgets.QGroupBox("Log")
            ll = QtWidgets.QVBoxLayout(log_box)
            self.log_view = QtWidgets.QPlainTextEdit()
            self.log_view.setReadOnly(True)
            self.log_view.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
            ll.addWidget(self.log_view)
            clear = QtWidgets.QPushButton("Clear Log")
            clear.clicked.connect(self.log_view.clear)
            ll.addWidget(clear, 0, Qt.AlignRight)
            splitter.addWidget(log_box)

            splitter.setStretchFactor(0, 3)
            splitter.setStretchFactor(1, 2)
            layout.addWidget(splitter, 1)
            self.update_buttons()

        # ------------------------------------------------------------ log
        def log(self, text):
            stamp = datetime.now().strftime("%H:%M:%S")
            for line in str(text).splitlines() or [""]:
                self.log_view.appendPlainText(f"[{stamp}] {line}")
            QtWidgets.QApplication.processEvents()  # show progress while the scan runs

        # ----------------------------------------------------------- busy
        def set_busy(self, busy):
            self.running = busy
            for w in (self.scan_button, self.bgy, self.ea, self.min_area, self.table, self.filter):
                w.setEnabled(not busy)
            self.progress.setRange(0, 0 if busy else 1)
            self.progress.setValue(0 if busy else 1)
            if busy:
                QtWidgets.QApplication.setOverrideCursor(Qt.WaitCursor)
            else:
                QtWidgets.QApplication.restoreOverrideCursor()
            self.update_buttons()

        def update_buttons(self):
            rows = self.selected_rows()
            self.zoom_button.setEnabled(bool(rows) and not self.running)
            statuses = {self.table.item(r, STATUS_COL).text() for r in rows}
            self.snap_button.setEnabled(bool(statuses & snappable) and not self.running)

        # ----------------------------------------------------------- scan
        def scan(self):
            if self.running:
                return
            bgy_layer, ea_layer = self.bgy.currentLayer(), self.ea.currentLayer()
            if bgy_layer is None or ea_layer is None:
                QtWidgets.QMessageBox.warning(self, PANEL_TITLE, "Choose the barangay layer and the EA layer.")
                return
            if bgy_layer == ea_layer:
                QtWidgets.QMessageBox.warning(self, PANEL_TITLE, "The barangay layer and the EA layer must be different.")
                return

            self.remove_old_layers(keep=(bgy_layer, ea_layer))
            self.scan_result = None
            self.table.setRowCount(0)
            self.count_label.setText("Scanning…")
            self.log("=" * 50)
            self.log(f"Scan: barangays '{bgy_layer.name()}', EAs '{ea_layer.name()}', "
                     f"slivers < {self.min_area.value():g} sqm")
            self.set_busy(True)
            stream = _LogStream(self.log)
            try:
                with redirect_stdout(stream), redirect_stderr(stream):
                    scan = run_check(bgy_layer, ea_layer, self.min_area.value())
                stream.flush()
            except Exception as e:  # noqa: BLE001 - report every failure in the log
                stream.flush()
                self.log(traceback.format_exc().rstrip())
                self.log(f"Scan FAILED: {e}")
                self.count_label.setText("Scan failed")
                return
            finally:
                self.set_busy(False)

            self.log(result_message(scan.discrepancies, include_warnings=False))
            if WARNINGS:
                self.log(f"{len(WARNINGS)} warning(s), see [WARNING] lines above.")
            self.scan_result = scan
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
                        if value in issue_colours:
                            item.setBackground(issue_colours[value])
                    if col == "Note":
                        item.setToolTip(item.text())
                    self.table.setItem(r, c, item)
                self.paint_status(r)
            self.table.setSortingEnabled(True)
            self.table.sortItems(TABLE_COLUMNS.index("Area (sqm)"), Qt.DescendingOrder)
            self.table.resizeColumnsToContents()
            self.apply_filter()

        def paint_status(self, table_row):
            """Show a discrepancy's status (and why it failed) in its Status cell."""
            item = self.table.item(table_row, STATUS_COL)
            df_row = self.table.item(table_row, ISSUE_COL).data(ROW_ROLE)
            status = self.scan_result.discrepancies.at[df_row, "Status"]
            item.setText(status)
            item.setToolTip(self.scan_result.messages.get(df_row, ""))
            font = QFont()
            font.setBold(status in (STATUS_DONE, STATUS_FAILED))
            item.setFont(font)
            item.setForeground(QBrush(QColor(STATUS_COLOURS[status])) if status in STATUS_COLOURS else QBrush())

        def apply_filter(self):
            wanted = self.filter.currentData()
            col = STATUS_COL if wanted and wanted[0] == "status" else ISSUE_COL
            shown = 0
            for r in range(self.table.rowCount()):
                hide = wanted is not None and self.table.item(r, col).text() != wanted[1]
                self.table.setRowHidden(r, hide)
                shown += not hide
            if self.scan_result is None:
                return
            total = self.table.rowCount()
            done = int((self.scan_result.discrepancies["Status"] == STATUS_DONE).sum()) if total else 0
            text = "No discrepancies" if total == 0 else (
                f"{shown:,} of {total:,}" if shown != total else f"{total:,} found")
            self.count_label.setText(text + (f" · {done:,} snapped" if done else ""))
            self.update_buttons()

        def selected_rows(self):
            return sorted({i.row() for i in self.table.selectedItems() if not self.table.isRowHidden(i.row())})

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
            canvas = self.iface.mapCanvas()
            to_canvas = QgsCoordinateTransform(layer.crs(), canvas.mapSettings().destinationCrs(), QgsProject.instance())
            extent = to_canvas.transformBoundingBox(extent)
            extent.scale(1.5)  # a little context around the discrepancy
            canvas.setExtent(extent)
            canvas.refresh()
            canvas.flashFeatureIds(layer, fids)

        # ----------------------------------------------------------- snap
        def snap_selected(self):
            if self.running or self.scan_result is None:
                return
            table_rows = self.selected_rows()
            by_df_row = {self.table.item(r, ISSUE_COL).data(ROW_ROLE): r for r in table_rows}
            if QgsProject.instance().mapLayer(self.scan_result.ea_layer.id()) is None:
                self.log("The EA layer was removed from the project. Scan again.")
                return

            self.log("-" * 50)
            self.log(f"Snapping {len(by_df_row):,} selected discrepanc{'y' if len(by_df_row) == 1 else 'ies'}...")
            self.set_busy(True)
            try:
                outcome = snap_rows(self.scan_result, sorted(by_df_row))
            except Exception as e:  # noqa: BLE001 - report every failure in the log
                self.log(traceback.format_exc().rstrip())
                self.log(f"Snap FAILED: {e}. The EA layer was not changed by this step.")
                return
            finally:
                self.set_busy(False)

            # one log line per EA (rows of the same EA share their message)
            for message in dict.fromkeys(m for _, m in outcome.values()):
                self.log(f"  {message}")
            for df_row, r in by_df_row.items():
                if df_row in outcome:
                    self.paint_status(r)
            done = sum(1 for s, _ in outcome.values() if s == STATUS_DONE)
            failed = sum(1 for s, _ in outcome.values() if s != STATUS_DONE)
            self.log(f"Snapped {done:,}, not snapped {failed:,} (hover a Status cell to see why).")
            if done:
                self.log(f"The changes are UNSAVED edits in '{self.scan_result.ea_layer.name()}'. "
                         f"Keep: Save Layer Edits. Undo: Edit > Undo.")
            self.apply_filter()

    return CheckPanel


def show_panel():
    """Open the EA vs Barangay panel docked beside the map. Running the script again replaces it."""
    from qgis.PyQt.QtCore import Qt
    from qgis.PyQt.QtWidgets import QDockWidget

    qgis_iface = globals().get("iface")
    if qgis_iface is None:
        try:
            from qgis.utils import iface as qgis_iface
        except ImportError:
            qgis_iface = None
    main = qgis_iface.mainWindow() if qgis_iface is not None else None

    if main is not None:
        for old in main.findChildren(QDockWidget, PANEL_NAME):
            qgis_iface.removeDockWidget(old)
            old.deleteLater()

    panel = make_panel_class()(qgis_iface, main)
    if main is not None:
        qgis_iface.addDockWidget(Qt.RightDockWidgetArea, panel)
        gmd = main.findChild(QDockWidget, "GMDCOProcessingDock")
        if gmd is not None and gmd.isVisible():
            main.tabifyDockWidget(gmd, panel)  # share the space with the GMD CO Processing panel
    panel.show()
    panel.raise_()
    return panel


if "qgis" in sys.modules and not os.environ.get("EA_QA_NO_DIALOG"):
    _PANEL = show_panel()
elif "qgis" not in sys.modules:
    print("Run this script inside QGIS: GMD CO Processing > EA Tools > EA vs Barangay Check.")
