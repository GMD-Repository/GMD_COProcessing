r"""
[TEST] EARF Inconsistency Checks — checks the 2026 EA GeoPackage and the Preliminary EARF spreadsheet,
cross-checked against the 2024 POPCEN Estimated Number of Households worksheet, and writes the two
"EA List of Inconsistencies" workbooks (GPKG and EARF).

Inputs (one dropdown each, picked from the layers loaded in the Layers panel; all three required):
  EA GeoPackage            <citymun>_ea2026.gpkg layers (attributes only, unsaved edits included)
  Preliminary EARF         spreadsheet layers (*.xlsx sheet, e.g. "EA2026")
  Est. No. of Households   spreadsheet layer of the 2024 POPCEN worksheet

Spreadsheets are re-read from their file with their real header row (QGIS's own spreadsheet reader
does not understand the multi-row EARF header).

Checks (same validation_id in both workbooks unless noted):
  dup_ea          two or more records with every attribute identical (feature id / Excel row ignored)
  dup_new_ean     the same New EA Code is used twice in one barangay (Merged EAs excepted)
  missing_geo     GPKG workbook: EARF geocodes (final, with the New EA Code) not in the GeoPackage
  earf_missing_geo EARF workbook: household-worksheet geocodes not in the EARF (EARF's city/muns only)
  inv_ea_type     EA Type other than Retained, Delineated, Merged
  inv_hhcount     Household count equal to building count
  inv_new_ean     New EA Code that is not 6 digits
  inv_sy          Retained must be 2024; Delineated / Merged must be 2026
  null_ea_count   EA count blank (EARF: only the first row of each old EA; split rows leave it blank)
  null_ea_type / null_hhcount / null_sy   blank values
  null_new_ean    Delineated or Merged EA without a New EA Code
  null_remarks    EA flagged for delineation/merging in the household worksheet, kept Retained, no remarks

The engine (everything above the "window" section) has no Qt code and can be run headless.
"""
# Menu / panel entry (read by the GMD CO Processing plugin)
TOOL_NAME = "[TEST] EARF Inconsistency Checks"
TOOL_GROUP = "GMD CO Processing"  # shown directly under the menu (see TOP_LEVEL_SCRIPTS in plugin.py)
TOOL_ICON = "icons/earf_post_processing.svg"

import os
import re
import sys
import time
import traceback
from datetime import datetime

import pandas as pd

# ═════════════════════════════ configuration ═════════════════════════════
VALID_TYPES = ("RETAINED", "DELINEATED", "MERGED")
EXPECTED_SY = {"RETAINED": "2024", "DELINEATED": "2026", "MERGED": "2026"}
TRUTHY = {"1", "Y", "YES", "TRUE", "X", "✓", "✔"}
BLANKS = {"", "nan", "none", "null", "<na>", "nat"}
SPREADSHEET_EXT = (".xlsx", ".xlsm", ".xls", ".ods", ".csv")

# (validation_id, title, description) — order and wording follow the templates
CHECKS = [
    ("dup_ea", "Duplicate EA Records", "EAs with duplicate attribute"),
    ("dup_new_ean", "Duplicate New EA Code",
     "Multiple records share the same New EA Code where uniqueness is expected"),
    ("missing_geo", "Missing Geocode Match",
     "Geocodes present in the Prelim EARF that are not present in the GeoPackage"),
    ("earf_missing_geo", "Missing Geocode Match",
     "Geocodes present in the 2024 POPCEN Estimated Number of Households that are not present in the Prelim EARF"),
    ("inv_ea_type", "Invalid EA Type", "Entries with values other than Retained, Delineated, and Merged"),
    ("inv_hhcount", "Invalid Household Count", "Household count is equal to building count"),
    ("inv_new_ean", "Invalid New EA Code", "Entries with values other than the required 6-digit EA code"),
    ("inv_sy", "Inconsistent EA Type and Source Year",
     "Mismatch between EA Type and SY (Delineated/Merged = 2026, \nRetained = 2024)"),
    ("null_ea_count", "Missing EA Count", "EA count should not be Null"),
    ("null_ea_type", "Missing EA Type", "EA Type is blank or null"),
    ("null_hhcount", "Missing Household Count", "Household count is blank or null"),
    ("null_new_ean", "Missing New EA Code", "Delineated or Merged EAs with missing New EA Code values"),
    ("null_remarks", "Missing Remarks",
     "EAs subject to Delineation/Merging with a Retained EA Type but without corresponding remarks"),
    ("null_sy", "Missing Source Year", "Source Year is blank or null"),
]
CHECK_INFO = {c[0]: c for c in CHECKS}
GPKG_IDS = [c[0] for c in CHECKS if c[0] != "earf_missing_geo"]
EARF_IDS = [c[0] for c in CHECKS if c[0] != "missing_geo"]
TABS = {"gpkg": "GeoPackage", "earf": "prelim EARF"}
CROSS_TABS = {("gpkg", "missing_geo"): "Prelim and GeoPackage",
              ("earf", "earf_missing_geo"): "2024 POPCEN and Prelim EARF"}

# Value column(s) of each sheet: (internal column, GPKG header, EARF header)
VALUE_COLS = {
    "dup_ea": [],
    "dup_new_ean": [("new_ean", "new_ean", "New EA Code")],
    "missing_geo": [],
    "earf_missing_geo": [],
    "inv_ea_type": [("ea_type", "ea_type", "EA Type")],
    "inv_hhcount": [("hh_count", "hh_count", "Household\nCount"), ("bldg_count", "bldg_count", "Building\nCount")],
    "inv_new_ean": [("new_ean", "new_ean", "New EA Code")],
    "inv_sy": [("ea_type", "ea_type", "EA Type"), ("sy", "sy", "Source Year")],
    "null_ea_count": [("ea_count", "eacount", "Number of EAs")],
    "null_ea_type": [("ea_type", "ea_type", "EA Type")],
    "null_hhcount": [("hh_count", "hh_count", "Household Count")],
    "null_new_ean": [("new_ean", "new_ean", "New EA Code")],
    "null_remarks": [],
    "null_sy": [("sy", "sy", "Source Year")],
}
GPKG_ID_COLS = [("geocode", "geocode"), ("region", "region"), ("province", "province"),
                ("city_mun", "city_mun"), ("barangay", "barangay")]
EARF_ID_COLS = [("geocode", "Geocode"), ("prov", "Prov"), ("mun", "Mun"), ("brgy", "Brgy"), ("ea", "EA")]


# ═════════════════════════════ small helpers ═════════════════════════════
def norm(name):
    """Header -> key: 'New Enumeration\\nArea Code' -> 'new_enumeration_area_code'."""
    return re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")


def clean(series):
    """Text values, trimmed, blanks as '', '2026.0' -> '2026'."""
    s = series.astype(object).where(series.notna(), "").astype(str).str.strip()
    s = s.where(~s.str.lower().isin(BLANKS), "")
    return s.str.replace(r"^(-?\d+)\.0+$", r"\1", regex=True)


def code(series, width):
    """Zero-padded code segment; non-numeric values are kept as typed."""
    s = clean(series)
    return s.where(~s.str.isdigit(), s.str.zfill(width))


def pick(columns, *candidates, contains=None):
    """First column whose normalised name is one of candidates (or contains a fragment)."""
    keys = {norm(c): c for c in columns}
    for cand in candidates:
        if cand in keys:
            return keys[cand]
    for frag in (contains or ()):
        for k, c in keys.items():
            if frag in k:
                return c
    return None


ROWKEY_SKIP = {"fid", "_fid", "_layer_id", "_row", "geom", "geometry"}


def row_key(df):
    """All attribute values of a record joined into one key (feature id / Excel row number left out)."""
    keep = [c for c in df.columns if norm(c) not in ROWKEY_SKIP and not str(c).startswith("_")]
    if not keep:
        return pd.Series("", index=df.index)
    parts = [clean(df[c]) for c in keep]
    key = parts[0]
    for p in parts[1:]:
        key = key + "\x1f" + p
    return key


def col(df, name):
    return clean(df[name]) if name and name in df.columns else pd.Series("", index=df.index)


def split_source(source):
    """'C:/x.xlsx|layername=EA2026' -> ('C:/x.xlsx', 'EA2026')."""
    parts = str(source).split("|")
    path, sheet = parts[0], None
    for p in parts[1:]:
        if p.lower().startswith("layername="):
            sheet = p.split("=", 1)[1]
    return path, sheet


def resolve_vrt(path, sheet=None):
    """A .vrt (written by the Spreadsheet Layers plugin) -> the spreadsheet and sheet it points at.

    Any other path is returned unchanged.
    """
    if not str(path).lower().endswith(".vrt") or not os.path.isfile(path):
        return path, sheet
    try:
        import xml.etree.ElementTree as ET
        root = ET.parse(path).getroot()
        layer = root.find("OGRVRTLayer") if root.tag != "OGRVRTLayer" else root
        if sheet:   # a .vrt can hold several layers: take the one named like the QGIS layer
            layer = next((l for l in root.iter("OGRVRTLayer") if l.get("name") == sheet), layer)
        src = layer.find("SrcDataSource")
        src_path = (src.text or "").strip()
        if src.get("relativeToVRT") == "1" or not os.path.isabs(src_path):
            src_path = os.path.join(os.path.dirname(path), src_path)
        src_layer = layer.findtext("SrcLayer") or None
        if src_layer is None:   # SrcSQL: SELECT ... FROM 'Sheet1' / "Sheet1" / Sheet1
            m = re.search(r"\bFROM\s+(?:'([^']+)'|\"([^\"]+)\"|\[([^\]]+)\]|(\S+))",
                          layer.findtext("SrcSQL") or "", re.I)
            src_layer = next((g for g in m.groups() if g), None) if m else None
        return os.path.normpath(src_path), src_layer
    except Exception:
        return path, sheet


def read_sheet_raw(path, sheet=None, nrows=None):
    if path.lower().endswith(".csv"):
        return pd.read_csv(path, header=None, dtype=str, nrows=nrows, keep_default_na=False)
    return pd.read_excel(path, sheet_name=sheet if sheet else 0, header=None, dtype=object, nrows=nrows)


def find_header(raw, required, scan=40):
    """Index of the first row whose normalised cells contain every required key."""
    for i in range(min(scan, len(raw))):
        keys = {norm(v) for v in raw.iloc[i].tolist() if pd.notna(v)}
        if all(any(k == r or k.startswith(r) for k in keys) for r in required):
            return i
    return None


def frame_from(raw, header_idx, fallback_idx=None):
    names = []
    for j, v in enumerate(raw.iloc[header_idx].tolist()):
        name = str(v).strip() if pd.notna(v) and str(v).strip() else ""
        if not name and fallback_idx is not None:
            alt = raw.iloc[fallback_idx, j]
            name = str(alt).strip() if pd.notna(alt) else ""
        base = name or f"col_{j + 1}"
        while base in names:          # keep repeated headers apart
            base += "_"
        names.append(base)
    body = raw.iloc[header_idx + 1:].copy()
    body.columns = names
    body["_row"] = body.index + 1          # Excel row number, for the user
    return body


# ═════════════════════════════ readers ═════════════════════════════
def read_earf(path, sheet=None, label=""):
    """Preliminary EARF -> one row per EA record (barangay / city / province title rows dropped)."""
    raw = read_sheet_raw(path, sheet)
    h = find_header(raw, ["prov", "mun", "brgy", "ea"])
    if h is None:
        raise ValueError(f"{label or path}: no 'Prov / Mun / Brgy / EA' header row found")
    df = frame_from(raw, h)
    # drop the '(2) (3) (4) ...' column-number row
    first = clean(df.iloc[:, 0])
    df = df[~first.str.fullmatch(r"\(\d+\)")]
    cols = df.columns
    c = {
        "prov": pick(cols, "prov", "province_code"),
        "mun": pick(cols, "mun", "city_mun_code"),
        "brgy": pick(cols, "brgy", "barangay_code"),
        "ea": pick(cols, "ea", "ean"),
        "ea_count": pick(cols, "number_of_eas", "no_of_eas", "eacount", contains=["number_of_ea"]),
        "name": pick(cols, contains=["province_city", "barangay_and_ea", "name"]),
        "new_ean": pick(cols, "new_enumeration_area_code", "new_ea_code", "new_ean",
                        contains=["new_enumeration", "new_ea"]),
        "hh_count": pick(cols, "household_count", "hh_count"),
        "bldg_count": pick(cols, "building_count", "bldg_count"),
        "ea_type": pick(cols, "ea_type", "type"),
        "sy": pick(cols, "source_year", "sy", contains=["source_y"]),
        "remarks": pick(cols, "remarks", contains=["remark"]),
    }
    out = pd.DataFrame({
        "prov": code(df[c["prov"]], 3), "mun": code(df[c["mun"]], 2),
        "brgy": code(df[c["brgy"]], 3), "ea": code(df[c["ea"]], 6),
    }, index=df.index)
    out["_rowkey"] = row_key(df)
    for k in ("ea_count", "new_ean", "hh_count", "bldg_count", "ea_type", "sy", "remarks"):
        out[k] = col(df, c[k])
    out["new_ean"] = out["new_ean"].where(~out["new_ean"].str.isdigit(), out["new_ean"].str.zfill(6))
    out["ea_type"] = out["ea_type"].str.upper()
    name = col(df, c["name"])
    out = out[(out["prov"] != "") | (name != "")]

    name = name.loc[out.index]
    if c["name"]:
        is_ea = name.str.match(r"(?i)^EA\b")
    else:   # no name column: an EA row has some 2026 value
        is_ea = (out["brgy"] != "000") & (out[["new_ean", "ea_type", "hh_count", "sy"]] != "").any(axis=1)

    # names from the title rows: province (mun 00), city/mun (brgy 000), barangay
    titles = out[~is_ea].assign(name=name[~is_ea])
    prov_names = titles[titles["mun"] == "00"].drop_duplicates("prov").set_index("prov")["name"]
    mun_t = titles[(titles["mun"] != "00") & (titles["brgy"] == "000")]
    mun_names = mun_t.assign(k=mun_t["prov"] + mun_t["mun"]).drop_duplicates("k").set_index("k")["name"]
    bgy_t = titles[titles["brgy"] != "000"]
    bgy_names = bgy_t.assign(k=bgy_t["prov"] + bgy_t["mun"] + bgy_t["brgy"]).drop_duplicates("k") \
        .set_index("k")["name"]

    out = out[is_ea].copy()
    out["mun5"] = out["prov"] + out["mun"]
    out["bgy8"] = out["mun5"] + out["brgy"]
    out["geocode"] = out["bgy8"] + out["ea"]                                   # 2024 (old) EA
    out["final_geocode"] = out["bgy8"] + out["new_ean"].where(out["new_ean"] != "", out["ea"])
    out["region"] = ""
    out["province"] = out["prov"].map(prov_names).fillna("")
    out["city_mun"] = out["mun5"].map(mun_names).fillna("")
    out["barangay"] = out["bgy8"].map(bgy_names).fillna("")
    out["source"] = label or os.path.basename(path)
    out["ref"] = "row " + df.loc[out.index, "_row"].astype(str)
    return out.reset_index(drop=True)


def read_household(path, sheet=None, label=""):
    """2024 POPCEN Estimated Number of Households -> one row per EA."""
    raw = read_sheet_raw(path, sheet)
    h = find_header(raw, ["province_code", "barangay_code"])
    if h is None:
        h = find_header(raw, ["prov", "brgy", "ea"])
    if h is None:
        raise ValueError(f"{label or path}: no 'province_code / barangay_code' header row found")
    df = frame_from(raw, h, fallback_idx=h - 1 if h > 0 else None)
    cols = df.columns
    c = {
        "prov": pick(cols, "province_code", "prov_code", "prov"),
        "mun": pick(cols, "city_mun_code", "mun_code", "mun"),
        "brgy": pick(cols, "barangay_code", "brgy_code", "brgy"),
        "ea": pick(cols, "ean", "ea", "ea_code"),
        "level": pick(cols, contains=["level"]),
        "delin": pick(cols, contains=["delineation"]),
        "merg": pick(cols, contains=["merging", "merge"]),
    }
    missing = [k for k in ("prov", "mun", "brgy", "ea") if not c[k]]
    if missing:
        raise ValueError(f"{label or path}: missing column(s) {', '.join(missing)}")
    out = pd.DataFrame({
        "prov": code(df[c["prov"]], 3), "mun": code(df[c["mun"]], 2),
        "brgy": code(df[c["brgy"]], 3), "ea": code(df[c["ea"]], 6),
    }, index=df.index)
    keep = (out["ea"] != "") & out["prov"].str.isdigit()
    if c["level"]:
        lvl = col(df, c["level"])
        keep &= ~lvl.str.contains(r"^\s*[0-4]\s*[-\u2013]|philippines|region|province|huc|city|"
                                  r"municipality|barangay", case=False, regex=True)
    out["subject"] = ""
    for key, word in (("delin", "delineation"), ("merg", "merging")):
        flag = col(df, c[key]).str.upper().isin(TRUTHY)
        out.loc[flag & (out["subject"] == ""), "subject"] = word
    out = out[keep].copy()
    out["mun5"] = out["prov"] + out["mun"]
    out["bgy8"] = out["mun5"] + out["brgy"]
    out["geocode"] = out["bgy8"] + out["ea"]
    out["source"] = label or os.path.basename(path)
    out["ref"] = "row " + (df.loc[out.index, "_row"]).astype(str)
    return out.drop_duplicates("geocode").reset_index(drop=True)


def gpkg_frame(records, label=""):
    """Rows of GeoPackage attributes (list of dicts or DataFrame) -> checked frame."""
    df = records if isinstance(records, pd.DataFrame) else pd.DataFrame(records)
    cols = df.columns
    c = {
        "geocode": pick(cols, "geocode", "ea_geocode", "psgc_ea"),
        "region": pick(cols, "region"), "province": pick(cols, "province"),
        "city_mun": pick(cols, "city_mun", "municipality", "citymun"),
        "barangay": pick(cols, "barangay"),
        "ean": pick(cols, "ean", "ea"),
        "new_ean": pick(cols, "new_ean", "new_ea_code"),
        "hh_count": pick(cols, "hh_count"), "bldg_count": pick(cols, "bldg_count"),
        "ea_type": pick(cols, "ea_type"), "sy": pick(cols, "sy", "source_year"),
        "ea_count": pick(cols, "eacount", "ea_count", "number_of_eas"),
        "remarks": pick(cols, "remarks", "remark"),
    }
    if not c["geocode"]:
        raise ValueError(f"{label}: the layer has no 'geocode' field")
    out = pd.DataFrame(index=df.index)
    out["_rowkey"] = row_key(df)
    for k, name in c.items():
        out[k] = col(df, name)
    out["geocode"] = out["geocode"].str.replace(r"\D", "", regex=True)
    out["ean"] = out["ean"].where(~out["ean"].str.isdigit(), out["ean"].str.zfill(6))
    out["new_ean"] = out["new_ean"].where(~out["new_ean"].str.isdigit(), out["new_ean"].str.zfill(6))
    out["ea_type"] = out["ea_type"].str.upper()
    out["mun5"] = out["geocode"].str[:5]
    out["bgy8"] = out["geocode"].str[:8]
    out["final_geocode"] = out["geocode"]
    out["old_geocode"] = out["bgy8"] + out["ean"]
    out["source"] = label
    out["ref"] = ("fid " + df["_fid"].astype(str)) if "_fid" in df.columns else ""
    for k in ("_fid", "_layer_id"):
        out[k] = df[k] if k in df.columns else None
    return out.reset_index(drop=True)


# ═════════════════════════════ checks ═════════════════════════════
def common_checks(df, subject_by_geocode, old_key, first_only_count=False):
    """The 12 checks shared by the GeoPackage and the EARF. Returns {id: (rows, note Series)}."""
    t, sy, nean = df["ea_type"], df["sy"], df["new_ean"]
    res = {}

    def add(cid, mask, note):
        note = note if isinstance(note, pd.Series) else pd.Series(note, index=df.index)
        res[cid] = (df[mask], note[mask])

    rk = df["_rowkey"]
    dup = rk.duplicated(keep=False)
    group = rk.map({k: i + 1 for i, k in enumerate(rk[dup].unique())})
    add("dup_ea", dup, "identical attributes on " + rk.map(rk.value_counts()).astype(str)
        + " records (group " + group.fillna(0).astype(int).astype(str) + ")")

    key = df["bgy8"] + "|" + nean
    cand = (nean != "") & (t != "MERGED")
    dupn = cand & key.where(cand).duplicated(keep=False) & key.where(cand).notna()
    add("dup_new_ean", dupn, "New EA Code " + nean + " repeated in barangay " + df["bgy8"])

    add("inv_ea_type", (t != "") & ~t.isin(VALID_TYPES), "EA Type '" + t + "' is not Retained/Delineated/Merged")

    hh = pd.to_numeric(df["hh_count"], errors="coerce")
    bl = pd.to_numeric(df["bldg_count"], errors="coerce")
    add("inv_hhcount", hh.notna() & bl.notna() & (hh == bl),
        "household count = building count = " + df["hh_count"] + " (recount)")

    add("inv_new_ean", (nean != "") & ~nean.str.fullmatch(r"\d{6}"), "New EA Code '" + nean + "' is not 6 digits")

    exp = t.map(EXPECTED_SY)
    add("inv_sy", exp.notna() & (sy != "") & (sy != exp.fillna("")),
        t.str.title() + " EA should have SY " + exp.fillna("") + ", found " + sy)

    cnt_scope = ~df[old_key].duplicated(keep="first") if first_only_count else pd.Series(True, index=df.index)
    add("null_ea_count", cnt_scope & (df["ea_count"] == ""), "EA count is blank")
    add("null_ea_type", t == "", "EA Type is blank")
    add("null_hhcount", df["hh_count"] == "", "household count is blank")
    add("null_new_ean", t.isin(("DELINEATED", "MERGED")) & (nean == ""), t.str.title() + " EA without New EA Code")

    if subject_by_geocode is not None:
        subj = df[old_key].map(subject_by_geocode).fillna("")
        add("null_remarks", (subj != "") & (t == "RETAINED") & (df["remarks"] == ""),
            "subject for " + subj + " in the household worksheet, kept Retained, no remarks")

    add("null_sy", sy == "", "Source Year is blank")
    return res


def run_checks(gpkg=None, earf=None, household=None, step=print):
    """
    gpkg, earf, household: frames from gpkg_frame / read_earf / read_household (or None).
    Returns {"gpkg": {id: DataFrame}, "earf": {id: DataFrame}, "skipped": {(wb, id): reason}}.
    Each result DataFrame has the template columns + '_note', 'source', 'ref' (+ '_fid', '_layer_id').
    """
    t0 = time.time()
    subject = None
    if household is not None and not household.empty:
        s = household[household["subject"] != ""]
        subject = s.set_index("geocode")["subject"]
    out = {"gpkg": {}, "earf": {}, "skipped": {}}

    if gpkg is not None and not gpkg.empty:
        res = common_checks(gpkg, subject, "old_geocode")
        if earf is not None and not earf.empty:
            have = set(gpkg["geocode"])
            miss = earf[~earf["final_geocode"].isin(have)].copy()
            miss = miss.drop_duplicates("final_geocode")
            # names from the GeoPackage where its barangay / city is there
            for colname, key in (("region", "mun5"), ("province", "mun5"), ("city_mun", "mun5"),
                                 ("barangay", "bgy8")):
                lut = gpkg[gpkg[colname] != ""].drop_duplicates(key).set_index(key)[colname]
                miss[colname] = miss[key].map(lut).fillna(miss[colname])
            miss["geocode"] = miss["final_geocode"]
            miss["_fid"], miss["_layer_id"] = None, None
            res["missing_geo"] = (miss, pd.Series("in the EARF (" + miss["ref"] + ") but not in the GeoPackage",
                                                  index=miss.index))
        else:
            out["skipped"][("gpkg", "missing_geo")] = "no Preliminary EARF ticked"
        if subject is None:
            out["skipped"][("gpkg", "null_remarks")] = "no household worksheet ticked"
        out["gpkg"] = {cid: _shape(rows, note, cid, "gpkg") for cid, (rows, note) in res.items()}
        step(f"GeoPackage: {len(gpkg)} EAs checked")

    if earf is not None and not earf.empty:
        res = common_checks(earf, subject, "geocode", first_only_count=True)
        if household is not None and not household.empty:
            in_earf = set(earf["geocode"])
            scope = household[household["mun5"].isin(set(earf["mun5"]))]
            miss = scope[~scope["geocode"].isin(in_earf)].copy()
            res["earf_missing_geo"] = (miss, pd.Series("in the household worksheet (" + miss["ref"]
                                                       + ") but not in the EARF", index=miss.index))
        else:
            out["skipped"][("earf", "earf_missing_geo")] = "no household worksheet ticked"
            out["skipped"][("earf", "null_remarks")] = "no household worksheet ticked"
        out["earf"] = {cid: _shape(rows, note, cid, "earf") for cid, (rows, note) in res.items()}
        step(f"Preliminary EARF: {len(earf)} EA rows checked")

    step(f"Checks done in {time.time() - t0:.2f}s")
    return out


def _shape(rows, note, cid, wb):
    ids = GPKG_ID_COLS if wb == "gpkg" else EARF_ID_COLS
    if cid in ("dup_ea",) and wb == "gpkg":
        ids = ids + [("ean", "ean")]
    vals = [(c, g if wb == "gpkg" else e) for c, g, e in VALUE_COLS[cid]]
    data = {}
    for c, header in ids + vals:
        data[header] = rows[c] if c in rows.columns else ""
    data["remarks" if wb == "gpkg" else "Remarks"] = note
    for extra in ("source", "ref", "_fid", "_layer_id"):
        data[extra] = rows[extra] if extra in rows.columns else None
    first = (ids[0][1])
    return pd.DataFrame(data).sort_values(first, kind="mergesort").reset_index(drop=True)


def counts(results):
    return {(wb, cid): len(df) for wb in ("gpkg", "earf") for cid, df in results[wb].items()}


# ═════════════════════════════ Excel output ═════════════════════════════
def write_workbook(results, wb_key, path):
    """Write one 'EA List of Inconsistencies' workbook in the template layout."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    ids = GPKG_IDS if wb_key == "gpkg" else EARF_IDS
    found = results[wb_key]
    skipped = results.get("skipped", {})
    head_font = Font(bold=True, color="FFFFFF")
    head_fill = PatternFill("solid", fgColor="1F4E78")
    zero_fill = PatternFill("solid", fgColor="E2EFDA")
    hit_fill = PatternFill("solid", fgColor="FCE4D6")
    thin = Side(style="thin", color="BFBFBF")
    box = Border(left=thin, right=thin, top=thin, bottom=thin)
    wrap = Alignment(wrap_text=True, vertical="top")

    def header(ws, names, widths):
        for j, (name, w) in enumerate(zip(names, widths), start=2):
            cell = ws.cell(row=3, column=j, value=name)
            cell.font, cell.fill, cell.border = head_font, head_fill, box
            cell.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
            ws.column_dimensions[get_column_letter(j)].width = w
        ws.column_dimensions["A"].width = 3
        ws.freeze_panes = "B4"

    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    ws["A1"] = "List of Inconsistencies"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = f"Generated {datetime.now():%Y-%m-%d %H:%M}"
    ws["A2"].font = Font(italic=True, color="7F7F7F")
    header(ws, ["validation_id", "tabs", "title", "description", "frequency", "remarks"],
           [18, 26, 34, 60, 11, 34])
    for i, cid in enumerate(ids, start=4):
        _, title, desc = CHECK_INFO[cid]
        tab = CROSS_TABS.get((wb_key, cid), TABS[wb_key])
        reason = skipped.get((wb_key, cid))
        n = None if reason else len(found.get(cid, ()))
        row = [cid, tab, title, desc, n if n is not None else "n/a", f"Not checked: {reason}" if reason else ""]
        for j, v in enumerate(row, start=2):
            cell = ws.cell(row=i, column=j, value=v)
            cell.border, cell.alignment = box, wrap
        link = ws.cell(row=i, column=2)
        link.hyperlink = f"#'{cid}'!A1"
        link.font = Font(color="0563C1", underline="single")
        if n is not None:
            ws.cell(row=i, column=6).fill = zero_fill if n == 0 else hit_fill

    for cid in ids:
        sh = wb.create_sheet(cid)
        sh["A1"] = CHECK_INFO[cid][1]
        sh["A1"].font = Font(bold=True, size=13)
        sh["B2"] = '=HYPERLINK("#\'Summary\'!A1","< Summary")'
        sh["B2"].font = Font(color="0563C1", underline="single")
        df = found.get(cid)
        if df is None:
            df = _shape(pd.DataFrame(), pd.Series(dtype=str), cid, wb_key)
            if (wb_key, cid) in skipped:
                sh["D2"] = f"Not checked: {skipped[(wb_key, cid)]}"
        cols = [c for c in df.columns if c not in ("source", "ref", "_fid", "_layer_id")]
        widths = [18 if i == 0 else (40 if c.lower() == "remarks" else 16) for i, c in enumerate(cols)]
        header(sh, cols, widths)
        for r, values in enumerate(df[cols].itertuples(index=False, name=None), start=4):
            for j, v in enumerate(values, start=2):
                cell = sh.cell(row=r, column=j, value=None if v is None or v == "" else str(v))
                cell.border = box
                cell.number_format = "@"
        if len(df):
            sh.auto_filter.ref = f"B3:{get_column_letter(len(cols) + 1)}{len(df) + 3}"
    wb.save(path)
    return path


def output_prefix(frames):
    """City/mun code(s) of the data: '02011', or '020' for a province, or 'combined'."""
    mun = set()
    for f in frames:
        if f is not None and not f.empty:
            mun |= set(f["mun5"][f["mun5"].str.len() == 5])
    if len(mun) == 1:
        return mun.pop()
    provs = {m[:3] for m in mun}
    return provs.pop() if len(provs) == 1 else "combined"


# ═════════════════════════════ window ═════════════════════════════
DIALOG_NAME = "EAListOfInconsistenciesTestDialog"
DIALOG_TITLE = "[TEST] EARF Inconsistency Checks"
DIALOG_SUBTITLE = ("Checks the 2026 EA GeoPackage and the Preliminary EARF against the 2024 POPCEN Estimated "
                   "Number of Households and writes the two List of Inconsistencies workbooks.")

THEMES = {
    False: {"title": "#2C3E50", "sub": "#7F8C8D", "hint": "#455a64", "summary": "#37474f",
            "hit": "#fce4d6", "ok": "#e2efda", "skip": "#eceff1",
            "log": "background:#FFFFFF; color:#1F2937; font-family:Consolas,monospace; font-size:11px;"
                   " border:1px solid #BDC3C7;",
            "help": "QTextBrowser { background:#ffffff; color:#1f2937; font-size:12px;"
                    " border:1px solid #d1d5db; border-radius:4px; padding:8px; }"},
    True: {"title": "#ECEFF1", "sub": "#B0BEC5", "hint": "#B0BEC5", "summary": "#ECEFF1",
           "hit": "#5d4037", "ok": "#2e4d34", "skip": "#37474f",
           "log": "background:#1E2327; color:#ECEFF1; font-family:Consolas,monospace; font-size:11px;"
                  " border:1px solid #37474F;",
           "help": "QTextBrowser { background:#263238; color:#ECEFF1; font-size:12px;"
                   " border:1px solid #455A64; border-radius:4px; padding:8px; }"},
}
TAB_STYLE = """
    QTabBar::tab { font-weight:bold; font-size:12px; min-width:180px; padding:10px 24px; margin-right:2px;
                   border-top-left-radius:4px; border-top-right-radius:4px; }
    QTabBar::tab:selected { border-bottom:3px solid #3498DB; }
"""

_CHECK_ROWS = "".join(
    f'<tr><td style="border:1px solid #e5e7eb;"><b>{cid}</b></td>'
    f'<td style="border:1px solid #e5e7eb;">{title}</td>'
    f'<td style="border:1px solid #e5e7eb;">{desc}</td></tr>'
    for cid, title, desc in CHECKS)

HELP_HTML = f"""
<h2>{DIALOG_TITLE}</h2>
<p>This is a <b>test</b> tool. It finds the inconsistencies listed below in the 2026 EA GeoPackage and the
Preliminary EARF, and writes two workbooks in the <i>EA List of Inconsistencies</i> template:
<code>&lt;code&gt;_EA List of Incons (GPKG).xlsx</code> and <code>&lt;code&gt;_EA List of Incons (EARF).xlsx</code>.</p>

<h3>Quick start</h3>
<ol>
<li>Add the inputs to the QGIS project (drag the <code>_ea2026.gpkg</code> and the spreadsheets into QGIS;
for a spreadsheet pick the sheet, e.g. <code>EA2026</code>).</li>
<li>Choose the <b>EA GeoPackage</b> layer from the dropdown (press <b>Refresh Layers</b> if a layer you
just added is missing).</li>
<li>For <b>Preliminary EARF</b> and <b>Est. No. of Households</b>, the tool searches the GeoPackage's folder
and fills in the spreadsheet whose name matches (<i>earf</i>; <i>household</i>, <i>hh</i>, <i>popcen</i>
or <i>estimated</i>). Otherwise press <b>Browse…</b> (it opens in that folder) or type the path. Then pick
the <b>Sheet</b>. The spreadsheets do not need to be loaded in QGIS. All three inputs are required.</li>
<li>Press <b>Run Check</b>. The table on the right shows each check and how many records it found.</li>
<li>Click a check to list its records below. Double-click a GeoPackage record to zoom to the EA.</li>
<li>Choose the <b>Output folder</b> and press <b>Export Excel</b>.</li>
</ol>

<h3>Inputs</h3>
<ul>
<li><b>EA GeoPackage</b>: needs <code>geocode, region, province, city_mun, barangay, ean, new_ean, ea_type,
hh_count, bldg_count, sy, eacount, remarks</code>. Unsaved edits are included.</li>
<li><b>Preliminary EARF</b>: the sheet with the <i>Prov / Mun / Brgy / EA</i> header. The file is re-read
directly so its two-row header is understood. Barangay title rows are skipped; only rows named
<i>EA ...</i> are checked.</li>
<li><b>Est. No. of Households</b>: the 2024 POPCEN worksheet with <code>province_code,
city_mun_code, barangay_code, ean</code> and the <i>EA Subject for Delineation / Merging</i> columns.
It is required: it gives <code>earf_missing_geo</code> and the subject flags used by <code>null_remarks</code>.</li>
</ul>

<h3>Checks</h3>
<table cellspacing="0" cellpadding="4" style="border-collapse:collapse; width:100%;">{_CHECK_ROWS}</table>

<h3>Rules used</h3>
<ul>
<li><b>Geocode</b>: GeoPackage <code>geocode</code> is the final code (barangay + New EA Code). For the
EARF the final code is Prov+Mun+Brgy+New EA Code (or EA when blank); the sheets list the 2024 code
Prov+Mun+Brgy+EA.</li>
<li><b>dup_ea</b>: two or more records whose attributes are all the same (every field of the attribute
table, or every column of the EARF row). Only the feature id (<code>fid</code>) and the Excel row number
are ignored. The remarks column gives a group number so the copies can be paired.</li>
<li><b>dup_new_ean</b>: the same New EA Code value on two or more records in one barangay. Merged EAs are not counted, since
merged parts can share the code they merge into.</li>
<li><b>missing_geo</b> (GPKG workbook): EARF final geocodes not found in the GeoPackage.</li>
<li><b>earf_missing_geo</b> (EARF workbook): household-worksheet geocodes not in the EARF, only for the
city/municipalities present in the EARF.</li>
<li><b>inv_sy</b>: Retained must be 2024; Delineated and Merged must be 2026.</li>
<li><b>null_ea_count</b> (EARF): only the first row of each 2024 EA is checked, because split rows leave
<i>Number of EAs</i> blank.</li>
<li><b>null_remarks</b>: the household worksheet marks the EA for delineation or merging, but the EA is
still Retained and has no remarks.</li>
</ul>

<h3>Output</h3>
<p>Each workbook has a <b>Summary</b> sheet (frequency of each check, with links) and one sheet per
<code>validation_id</code>. The <b>remarks</b> column explains the problem. The file name starts with the
city/municipality code (or the province code, or <i>combined</i>).</p>

<h3>Troubleshooting</h3>
<ul>
<li><i>no 'Prov / Mun / Brgy / EA' header row found</i>: the wrong sheet is loaded; add the EARF sheet.</li>
<li><i>the layer has no 'geocode' field</i>: the chosen GeoPackage layer is not an ea2026 layer.</li>
<li>The table counts are per check; one EA can appear in several checks.</li>
</ul>
"""


def make_dialog_class():
    from qgis.core import QgsFeatureRequest, QgsMapLayer, QgsProject, QgsWkbTypes
    from qgis.PyQt import QtWidgets
    from qgis.PyQt.QtCore import Qt
    from qgis.PyQt.QtGui import QBrush, QColor

    def layer_path(layer):
        try:
            from qgis.core import QgsProviderRegistry
            parts = QgsProviderRegistry.instance().decodeUri(layer.providerType(), layer.source())
            if parts.get("path"):
                return resolve_vrt(parts["path"], parts.get("layerName") or None)
        except Exception:
            pass
        return resolve_vrt(*split_source(layer.source()))

    class LayerPicker(QtWidgets.QGroupBox):
        """One dropdown of the matching layers in the Layers panel."""

        def __init__(self, title, accept, hint, parent=None):
            super().__init__(title, parent)
            self.accept, self.hint = accept, re.compile(hint, re.I)
            lay = QtWidgets.QVBoxLayout(self)
            self.combo = QtWidgets.QComboBox()
            self.combo.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
            self.combo.setMinimumContentsLength(20)
            lay.addWidget(self.combo)

        def refresh(self):
            current = self.combo.currentData()
            self.combo.blockSignals(True)
            self.combo.clear()
            layers = sorted((l for l in QgsProject.instance().mapLayers().values() if self.accept(l)),
                            key=lambda l: l.name().lower())
            for layer in layers:
                self.combo.addItem(layer.name(), layer.id())
                self.combo.setItemData(self.combo.count() - 1, layer.source(), Qt.ToolTipRole)
            if not layers:
                self.combo.addItem("(no matching layer in the Layers panel)", None)
            idx = self.combo.findData(current) if current else -1
            if idx < 0:   # first time / layer removed: pick the likeliest layer by name
                idx = next((i for i, l in enumerate(layers)
                            if self.hint.search(l.name() + " " + l.source())), 0)
            self.combo.setCurrentIndex(idx)
            self.combo.blockSignals(False)

        def layer(self):
            lid = self.combo.currentData()
            return QgsProject.instance().mapLayer(lid) if lid else None

        def ticked_layers(self):
            lyr = self.layer()
            return [lyr] if lyr is not None else []

    class FilePicker(QtWidgets.QGroupBox):
        """A spreadsheet path (typed, browsed, or found in the GeoPackage's folder) and its sheet."""

        FILTER = "Spreadsheets (*.xlsx *.xlsm *.xls *.ods *.csv);;All files (*)"

        def __init__(self, title, hint, folder_fn, parent=None):
            super().__init__(title, parent)
            self.hint, self.folder_fn = re.compile(hint, re.I), folder_fn
            lay = QtWidgets.QVBoxLayout(self)
            row = QtWidgets.QHBoxLayout()
            self.path = QtWidgets.QLineEdit()
            self.path.setPlaceholderText("Spreadsheet file (.xlsx, .xls, .ods, .csv)")
            self.path.editingFinished.connect(self._load_sheets)
            browse = QtWidgets.QPushButton("Browse…")
            browse.clicked.connect(self.browse)
            row.addWidget(self.path, 1)
            row.addWidget(browse)
            lay.addLayout(row)
            srow = QtWidgets.QHBoxLayout()
            srow.addWidget(QtWidgets.QLabel("Sheet:"))
            self.sheet = QtWidgets.QComboBox()
            srow.addWidget(self.sheet, 1)
            lay.addLayout(srow)
            self._loaded = None

        def folder(self):
            p = self.path.text().strip()
            if p and os.path.isdir(os.path.dirname(p)):
                return os.path.dirname(p)
            return self.folder_fn() or ""

        def browse(self):
            fn, _ = QtWidgets.QFileDialog.getOpenFileName(self, self.title(), self.folder(), self.FILTER)
            if fn:
                self.path.setText(os.path.normpath(fn))
                self._load_sheets()

        def search_folder(self):
            """Fill in the likeliest spreadsheet in the GeoPackage's folder, if none is set yet."""
            if self.path.text().strip():
                return
            folder = self.folder_fn()
            if not folder or not os.path.isdir(folder):
                return
            try:
                files = sorted(f for f in os.listdir(folder)
                               if f.lower().endswith(SPREADSHEET_EXT) and not f.startswith("~$"))
            except OSError:
                return
            match = next((f for f in files if self.hint.search(f)), None)
            if match:
                self.path.setText(os.path.normpath(os.path.join(folder, match)))
                self._load_sheets()

        def _load_sheets(self):
            p = self.path.text().strip().strip('"')
            if p == self._loaded:
                return
            self._loaded = p
            self.sheet.clear()
            if not os.path.isfile(p) or p.lower().endswith(".csv"):
                return
            try:
                with pd.ExcelFile(p) as book:
                    names = list(book.sheet_names)
            except Exception as exc:
                self.sheet.addItem(f"(cannot open: {exc})", None)
                return
            for n in names:
                self.sheet.addItem(n, n)
            idx = next((i for i, n in enumerate(names) if self.hint.search(n)), 0)
            self.sheet.setCurrentIndex(idx)

        def refresh(self):
            self.search_folder()

        def ticked_layers(self):
            """[(path, sheet, label)] or [] — same role as LayerPicker.ticked_layers."""
            self._load_sheets()
            p = self.path.text().strip().strip('"')
            if not p or not os.path.isfile(p):
                return []
            sheet = self.sheet.currentData()
            label = os.path.basename(p) + (f" [{sheet}]" if sheet else "")
            return [(p, sheet, label)]

    def is_gpkg_layer(layer):
        return (layer.type() == QgsMapLayer.VectorLayer
                and layer.geometryType() == QgsWkbTypes.PolygonGeometry
                and layer.fields().indexOf("geocode") >= 0)

    class Dialog(QtWidgets.QDialog):
        def __init__(self, iface, parent=None):
            super().__init__(parent)
            self.setObjectName(DIALOG_NAME)
            self.setWindowTitle(f"{DIALOG_TITLE} — EA Tools")
            icon = os.path.join(os.path.dirname(os.path.abspath(__file__)), TOOL_ICON) \
                if "__file__" in globals() else ""
            if icon and os.path.exists(icon):
                from qgis.PyQt.QtGui import QIcon
                self.setWindowIcon(QIcon(icon))
            self.setWindowFlags(self.windowFlags() | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint)
            self.setAttribute(Qt.WA_DeleteOnClose, True)
            self.setMinimumSize(900, 620)
            self.resize(1180, 780)
            self.iface = iface
            self.results = None
            self.frames = (None, None, None)
            self.dark = self.palette().color(self.palette().Window).lightness() < 128
            th = THEMES[self.dark]

            root = QtWidgets.QVBoxLayout(self)
            title = QtWidgets.QLabel(DIALOG_TITLE)
            title.setStyleSheet(f"font-size:18px; font-weight:bold; color:{th['title']};")
            sub = QtWidgets.QLabel(DIALOG_SUBTITLE)
            sub.setWordWrap(True)
            sub.setStyleSheet(f"color:{th['sub']};")
            root.addWidget(title)
            root.addWidget(sub)
            tabs = QtWidgets.QTabWidget()
            tabs.setStyleSheet(TAB_STYLE)
            tabs.addTab(self._main_tab(th), "Inconsistency Check")
            help_view = QtWidgets.QTextBrowser()
            help_view.setOpenExternalLinks(True)
            help_view.setStyleSheet(th["help"])
            html = HELP_HTML
            if self.dark:
                for a, b in (("#e5e7eb", "#455A64"),):
                    html = html.replace(a, b)
            help_view.setHtml(html)
            tabs.addTab(help_view, "Help and Information")
            root.addWidget(tabs, 1)
            close_row = QtWidgets.QHBoxLayout()
            close_row.addStretch()
            close = QtWidgets.QPushButton("Close")
            close.setMinimumWidth(100)
            close.clicked.connect(self.close)
            close_row.addWidget(close)
            root.addLayout(close_row)

            self.refresh_layers()
            QgsProject.instance().layersAdded.connect(self.refresh_layers)
            QgsProject.instance().layersRemoved.connect(self.refresh_layers)

        def closeEvent(self, event):
            for sig in (QgsProject.instance().layersAdded, QgsProject.instance().layersRemoved):
                try:
                    sig.disconnect(self.refresh_layers)
                except Exception:
                    pass
            super().closeEvent(event)

        # ───────────── layout ─────────────
        def _main_tab(self, th):
            page = QtWidgets.QWidget()
            lay = QtWidgets.QHBoxLayout(page)

            left = QtWidgets.QWidget()
            left.setFixedWidth(330)
            lv = QtWidgets.QVBoxLayout(left)
            lv.setContentsMargins(0, 0, 0, 0)
            hint = QtWidgets.QLabel("Choose the GeoPackage layer from the Layers panel, then the two "
                                    "spreadsheet files (Browse…, or found in the GeoPackage's folder) "
                                    "and their sheets. All three are required.")
            hint.setWordWrap(True)
            hint.setStyleSheet(f"color:{th['hint']}; font-size:11px;")
            lv.addWidget(hint)
            self.gpkg_list = LayerPicker("1. EA GeoPackage (ea2026)", is_gpkg_layer, r"ea2026")
            self.earf_list = FilePicker("2. Preliminary EARF", r"earf", self._gpkg_folder)
            self.hh_list = FilePicker("3. Est. No. of Households",
                                      r"household|hh|popcen|estimated", self._gpkg_folder)
            self.gpkg_list.combo.currentIndexChanged.connect(self.refresh_layers)
            for w in (self.gpkg_list, self.earf_list, self.hh_list):
                lv.addWidget(w)
            refresh = QtWidgets.QPushButton("Refresh Layers")
            refresh.clicked.connect(self.refresh_layers)
            lv.addWidget(refresh)

            out_box = QtWidgets.QGroupBox("4. Output folder")
            ob = QtWidgets.QHBoxLayout(out_box)
            self.out_edit = QtWidgets.QLineEdit(os.path.join(os.path.expanduser("~"), "Downloads"))
            browse = QtWidgets.QPushButton("…")
            browse.setFixedWidth(30)
            browse.clicked.connect(self.pick_folder)
            ob.addWidget(self.out_edit)
            ob.addWidget(browse)
            lv.addWidget(out_box)

            self.run_btn = QtWidgets.QPushButton("Run Check")
            self.run_btn.setMinimumHeight(34)
            self.run_btn.setStyleSheet("QPushButton { background:#1565c0; color:white; font-weight:bold;"
                                       " border-radius:4px; } QPushButton:disabled { background:#90a4ae; }")
            self.run_btn.clicked.connect(self.run)
            lv.addWidget(self.run_btn)
            self.progress = QtWidgets.QProgressBar()
            self.progress.setRange(0, 1)
            self.progress.setValue(0)
            self.progress.setTextVisible(False)
            self.progress.setMaximumHeight(8)
            lv.addWidget(self.progress)
            lv.addStretch()
            lay.addWidget(left)

            right = QtWidgets.QVBoxLayout()
            self.summary = QtWidgets.QLabel("Choose the three inputs and press Run Check.")
            self.summary.setStyleSheet(f"font-weight:bold; color:{th['summary']};")
            self.summary.setWordWrap(True)
            right.addWidget(self.summary)

            split = QtWidgets.QSplitter(Qt.Vertical)
            self.check_table = QtWidgets.QTableWidget(0, 5)
            self.check_table.setHorizontalHeaderLabels(["Workbook", "validation_id", "Title", "Frequency", "Note"])
            self.check_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
            self.check_table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
            self.check_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
            self.check_table.verticalHeader().setVisible(False)
            self.check_table.horizontalHeader().setStretchLastSection(True)
            self.check_table.itemSelectionChanged.connect(self.show_rows)
            split.addWidget(self.check_table)

            lower = QtWidgets.QWidget()
            lw = QtWidgets.QVBoxLayout(lower)
            lw.setContentsMargins(0, 0, 0, 0)
            frow = QtWidgets.QHBoxLayout()
            frow.addWidget(QtWidgets.QLabel("Search:"))
            self.search = QtWidgets.QLineEdit()
            self.search.setPlaceholderText("geocode, barangay, remarks…")
            self.search.textChanged.connect(self.filter_rows)
            frow.addWidget(self.search)
            lw.addLayout(frow)
            self.row_table = QtWidgets.QTableWidget(0, 0)
            self.row_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
            self.row_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
            self.row_table.setAlternatingRowColors(True)
            self.row_table.verticalHeader().setVisible(False)
            self.row_table.horizontalHeader().setStretchLastSection(True)
            self.row_table.cellDoubleClicked.connect(self.zoom_to_row)
            lw.addWidget(self.row_table)
            split.addWidget(lower)
            split.setSizes([260, 380])
            right.addWidget(split, 1)

            act = QtWidgets.QHBoxLayout()
            self.export_btn = QtWidgets.QPushButton("Export Excel")
            self.export_btn.setEnabled(False)
            self.export_btn.clicked.connect(self.export)
            self.open_btn = QtWidgets.QPushButton("Open Output Folder")
            self.open_btn.clicked.connect(self.open_folder)
            act.addWidget(self.export_btn)
            act.addWidget(self.open_btn)
            act.addStretch()
            right.addLayout(act)

            self.log = QtWidgets.QPlainTextEdit()
            self.log.setReadOnly(True)
            self.log.setMaximumHeight(120)
            self.log.setStyleSheet(th["log"])
            right.addWidget(self.log)
            lay.addLayout(right, 1)
            return page

        # ───────────── actions ─────────────
        def say(self, text):
            self.log.appendPlainText(f"[{datetime.now():%H:%M:%S}] {text}")
            QtWidgets.QApplication.processEvents()

        def refresh_layers(self, *_):
            for w in (self.gpkg_list, self.earf_list, self.hh_list):
                w.refresh()

        def pick_folder(self):
            d = QtWidgets.QFileDialog.getExistingDirectory(self, "Output folder", self.out_edit.text())
            if d:
                self.out_edit.setText(d)

        def open_folder(self):
            d = self.out_edit.text()
            if os.path.isdir(d):
                os.startfile(d) if sys.platform == "win32" else None

        def _read_gpkg(self, layers):
            frames = []
            req = QgsFeatureRequest().setFlags(QgsFeatureRequest.NoGeometry)
            for layer in layers:
                names = layer.fields().names()
                rows = []
                for f in layer.getFeatures(req):
                    d = dict(zip(names, f.attributes()))
                    d["_fid"] = f.id()
                    rows.append(d)
                df = pd.DataFrame(rows, columns=names + ["_fid"])
                df["_layer_id"] = layer.id()
                frames.append(gpkg_frame(df, layer.name()))
                self.say(f"  GeoPackage '{layer.name()}': {len(df)} EAs")
            return pd.concat(frames, ignore_index=True) if frames else None

        def _gpkg_folder(self):
            lyr = self.gpkg_list.layer() if hasattr(self, "gpkg_list") else None
            if lyr is None:
                return ""
            path, _ = layer_path(lyr)
            return os.path.dirname(str(path))

        def _read_sheets(self, files, reader, what):
            frames = []
            for path, sheet, label in files:
                frames.append(reader(path, sheet, label))
                self.say(f"  {what} '{label}': {len(frames[-1])} EA rows")
            return pd.concat(frames, ignore_index=True) if frames else None

        def run(self):
            g, e, h = (self.gpkg_list.ticked_layers(), self.earf_list.ticked_layers(),
                       self.hh_list.ticked_layers())
            missing = [name for name, lyr in (("EA GeoPackage", g), ("Preliminary EARF", e),
                                              ("Est. No. of Households", h)) if not lyr]
            if missing:
                QtWidgets.QMessageBox.warning(self, DIALOG_TITLE, "Choose an input for: " + ", ".join(missing))
                return
            if (os.path.normcase(e[0][0]), e[0][1]) == (os.path.normcase(h[0][0]), h[0][1]):
                QtWidgets.QMessageBox.warning(self, DIALOG_TITLE,
                                              "The Preliminary EARF and the household worksheet are the same sheet.")
                return
            self.run_btn.setEnabled(False)
            self.progress.setRange(0, 0)
            QtWidgets.QApplication.setOverrideCursor(Qt.WaitCursor)
            t0 = time.time()
            try:
                self.say("Reading inputs…")
                gpkg = self._read_gpkg(g)
                earf = self._read_sheets(e, read_earf, "EARF")
                hh = self._read_sheets(h, read_household, "Households")
                self.frames = (gpkg, earf, hh)
                self.results = run_checks(gpkg, earf, hh, step=self.say)
                self.fill_check_table()
                total = sum(counts(self.results).values())
                self.summary.setText(f"{total} inconsistent record(s) found in "
                                     f"{sum(1 for v in counts(self.results).values() if v)} check(s) "
                                     f"— {time.time() - t0:.1f}s. Click a check to list its records.")
                self.export_btn.setEnabled(True)
            except Exception as exc:
                self.say(f"ERROR: {exc}")
                self.say(traceback.format_exc())
                QtWidgets.QMessageBox.critical(self, DIALOG_TITLE, str(exc))
            finally:
                QtWidgets.QApplication.restoreOverrideCursor()
                self.progress.setRange(0, 1)
                self.progress.setValue(1)
                self.run_btn.setEnabled(True)

        def fill_check_table(self):
            th = THEMES[self.dark]
            t = self.check_table
            t.setRowCount(0)
            for wb, ids in (("gpkg", GPKG_IDS), ("earf", EARF_IDS)):
                if not self.results[wb]:
                    continue
                for cid in ids:
                    reason = self.results["skipped"].get((wb, cid))
                    n = None if reason else len(self.results[wb].get(cid, ()))
                    r = t.rowCount()
                    t.insertRow(r)
                    vals = ["GPKG" if wb == "gpkg" else "EARF", cid, CHECK_INFO[cid][1],
                            "n/a" if n is None else str(n), f"Not checked: {reason}" if reason else ""]
                    colour = th["skip"] if n is None else (th["ok"] if n == 0 else th["hit"])
                    for c, v in enumerate(vals):
                        it = QtWidgets.QTableWidgetItem(v)
                        it.setData(Qt.UserRole, (wb, cid))
                        it.setBackground(QBrush(QColor(colour)))
                        t.setItem(r, c, it)
            t.resizeColumnsToContents()

        def current_df(self):
            items = self.check_table.selectedItems()
            if not items or self.results is None:
                return None, None
            wb, cid = items[0].data(Qt.UserRole)
            return wb, self.results[wb].get(cid)

        def show_rows(self):
            wb, df = self.current_df()
            t = self.row_table
            t.setRowCount(0)
            if df is None:
                t.setColumnCount(0)
                return
            cols = [c for c in df.columns if c not in ("_fid", "_layer_id")]
            t.setColumnCount(len(cols))
            t.setHorizontalHeaderLabels([c.replace("\n", " ") for c in cols])
            t.setRowCount(len(df))
            for r, values in enumerate(df[cols].itertuples(index=False, name=None)):
                for c, v in enumerate(values):
                    it = QtWidgets.QTableWidgetItem("" if v is None else str(v))
                    it.setData(Qt.UserRole, r)
                    t.setItem(r, c, it)
            t.resizeColumnsToContents()
            self.filter_rows()

        def filter_rows(self):
            text = self.search.text().strip().lower()
            t = self.row_table
            for r in range(t.rowCount()):
                hit = not text or any(text in (t.item(r, c).text().lower() if t.item(r, c) else "")
                                      for c in range(t.columnCount()))
                t.setRowHidden(r, not hit)

        def zoom_to_row(self, row, _col):
            wb, df = self.current_df()
            if df is None or self.iface is None:
                return
            rec = df.iloc[self.row_table.item(row, 0).data(Qt.UserRole)]
            lid, fid = rec.get("_layer_id"), rec.get("_fid")
            layer = QgsProject.instance().mapLayer(lid) if isinstance(lid, str) else None
            if layer is None or fid is None or pd.isna(fid):
                self.say("This record has no GeoPackage feature to zoom to.")
                return
            layer.selectByIds([int(fid)])
            self.iface.setActiveLayer(layer)
            self.iface.mapCanvas().zoomToSelected(layer)
            self.iface.mapCanvas().flashFeatureIds(layer, [int(fid)])

        def export(self):
            if self.results is None:
                return
            folder = self.out_edit.text().strip()
            if not folder:
                return
            os.makedirs(folder, exist_ok=True)
            prefix = output_prefix(self.frames)
            written = []
            try:
                for wb, label in (("gpkg", "GPKG"), ("earf", "EARF")):
                    if self.results[wb]:
                        path = os.path.join(folder, f"{prefix}_EA List of Incons ({label}).xlsx")
                        write_workbook(self.results, wb, path)
                        written.append(path)
                        self.say(f"Saved {path}")
            except PermissionError as exc:
                QtWidgets.QMessageBox.critical(self, DIALOG_TITLE,
                                               f"Cannot write the file (is it open in Excel?)\n{exc}")
                return
            if written:
                QtWidgets.QMessageBox.information(self, DIALOG_TITLE, "Saved:\n" + "\n".join(written))

    return Dialog


def show_dialog():
    from qgis.PyQt.QtWidgets import QApplication, QDialog
    try:
        while QApplication.overrideCursor() is not None:
            QApplication.restoreOverrideCursor()
    except Exception:
        pass
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


if "qgis" in sys.modules and not os.environ.get("EA_INCONS_NO_DIALOG"):
    _DIALOG = show_dialog()
elif "qgis" not in sys.modules:
    print(f"Run this script inside QGIS: GMD CO Processing > {TOOL_NAME}.")
