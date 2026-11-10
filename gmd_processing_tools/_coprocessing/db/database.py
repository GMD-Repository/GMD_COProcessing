# -*- coding: utf-8 -*-
"""
CO Processing — Local Database Manager (SQLite)
-----------------------------------------------
Provides fast, indexed local database storage for:
  1. 2024 POPCEN Estimated Number of Households dataset (`popcen_records`)
  2. Consolidated Preliminary EARF datasets (`earf_records`)
  3. Consistency validation flags and outer-join match statuses.

Enables instant querying, searching, filtering, and paging across 200,000+
records without loading massive Excel files repeatedly into RAM.
"""

import os
import glob
import re
import sqlite3
import collections
from datetime import datetime
import pandas as pd
from typing import Dict, List, Optional, Tuple, Any, Union, Callable

try:
    from ..core.algorithm import build_geocode, read_earf_table, POPCEN_FIELDS, GPKG_EARF_FIELDS, to_whole_number_str
except (ImportError, ValueError):
    try:
        from core.algorithm import build_geocode, read_earf_table, POPCEN_FIELDS, GPKG_EARF_FIELDS, to_whole_number_str
    except (ImportError, ValueError):
        try:
            from algorithm import build_geocode, read_earf_table, POPCEN_FIELDS, GPKG_EARF_FIELDS, to_whole_number_str
        except (ImportError, ValueError):
            def to_whole_number_str(val) -> str:
                if val is None:
                    return ""
                if isinstance(val, float) and (pd.isna(val) or val != val):
                    return ""
                s = str(val).strip()
                if not s or s.lower() in ("nan", "none", "null", "<na>"):
                    return ""
                if s.endswith(".0"):
                    s = s[:-2].strip()
                elif s.endswith(".00"):
                    s = s[:-3].strip()
                try:
                    f = float(s)
                    if f.is_integer():
                        if "." not in s:
                            return s
                        return str(int(round(f)))
                except (ValueError, TypeError):
                    pass
                return s


POPCEN_DB_FILENAME = "popcen_database.db"
EARF_DB_FILENAME = "earf_database.db"
GPKG_DB_FILENAME = "gpkg_database.db"

# Legacy alias
DB_FILENAME = POPCEN_DB_FILENAME


def _resolve_db_file(filename: str) -> str:
    """Resolves database file path by checking secured/ directory, then data/, then root directory, and existing COProcessing plugin path."""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) if os.path.basename(os.path.dirname(os.path.abspath(__file__))) in ("db", "core", "ui") else os.path.dirname(os.path.abspath(__file__))
    secured_path = os.path.join(base_dir, "secured", filename)
    if os.path.exists(secured_path):
        return secured_path
    data_path = os.path.join(base_dir, "data", filename)
    if os.path.exists(data_path):
        return data_path
    root_path = os.path.join(base_dir, filename)
    if os.path.exists(root_path):
        return root_path

    # Check GMD_COProcessing plugin secured folder
    plugin_dir = os.path.dirname(os.path.dirname(base_dir))
    gmd_secured = os.path.join(plugin_dir, "secured", filename)
    if os.path.exists(gmd_secured):
        return gmd_secured

    # Check original COProcessing plugin secured folder (where user databases already reside)
    plugins_dir = os.path.dirname(plugin_dir)
    coproc_secured = os.path.join(plugins_dir, "COProcessing", "secured", filename)
    if os.path.exists(coproc_secured):
        return coproc_secured

    # Fallback to standard AppData COProcessing path
    appdata = os.environ.get("APPDATA", "")
    if appdata:
        std_coproc = os.path.join(appdata, "QGIS", "QGIS3", "profiles", "default", "python", "plugins", "COProcessing", "secured", filename)
        if os.path.exists(std_coproc):
            return std_coproc

    os.makedirs(os.path.join(base_dir, "secured"), exist_ok=True)
    return secured_path


def get_default_db_path() -> str:
    """Returns the default SQLite database path for 2024 POPCEN records & master match results."""
    return _resolve_db_file(POPCEN_DB_FILENAME)


def get_default_earf_db_path() -> str:
    """Returns the default SQLite database path for Preliminary EARF records."""
    return _resolve_db_file(EARF_DB_FILENAME)


def get_default_gpkg_db_path() -> str:
    """Returns the default SQLite database path for EA GeoPackage records."""
    return _resolve_db_file(GPKG_DB_FILENAME)


DEFAULT_FINAL_EXPORT_ROOT = r"Y:\2026\1Map\Final EARF and geopackages"
DEFAULT_GPKG_ROOT = r"Y:\geotags-uploads\ea-delineation"
DEFAULT_EARF_ROOT = r"D:\PSA-GIS\Camarines Sur\Project 1MAP\3_EA Delineation and Merging\1_Downloaded Files\Output\2_output_files"



def _clean_hier_name(s: str) -> str:
    return re.sub(r'[^a-zA-Z0-9]', '', str(s or '').lower())


def _match_hier_names(d_name: str, target_name: str) -> bool:
    cd = _clean_hier_name(d_name)
    ct = _clean_hier_name(target_name)
    if not cd or not ct:
        return False
    if cd == ct or cd in ct or ct in cd:
        return True
    cd_strip = cd.replace("cityof", "").replace("city", "").replace("municipalityof", "").replace("provinceof", "")
    ct_strip = ct.replace("cityof", "").replace("city", "").replace("municipalityof", "").replace("provinceof", "")
    if cd_strip and ct_strip and (cd_strip == ct_strip or cd_strip in ct_strip or ct_strip in cd_strip):
        return True
    return False


def is_version_dir(dir_name: str) -> bool:
    """
    Returns True if a directory name matches version/archive folder naming conventions.
    Matches folder names like 'versions', 'version', 'version_1', 'version 1', 'v1', 'v2', etc.
    """
    name = str(dir_name).strip().lower()
    if not name:
        return False
    if "version" in name:
        return True
    if re.match(r"^(?:v|ver)[-_ ]?\d+(?:\.\d+)*$", name):
        return True
    return False


def find_gpkg_files_in_folder(folder_path: str, recursive: bool = False, pattern: str = "_latest") -> List[str]:
    """
    Scans folder_path for all GeoPackage files (.gpkg / .sqlite)
    matching pattern (default: '_latest', e.g. '*_latest.gpkg'),
    excluding temporary files ('~$') and 'versions' directories.
    If pattern is provided but yields 0 matching files, it falls back
    to returning all valid GeoPackage files found.
    """
    if not folder_path or not os.path.exists(folder_path):
        return []

    target_pattern = pattern.lower().strip() if pattern else ""
    candidate_files = []

    if recursive:
        for root, dirs, files in os.walk(folder_path):
            dirs[:] = [d for d in dirs if not is_version_dir(d)]
            rel_path = os.path.relpath(root, folder_path)
            if rel_path != "." and any(is_version_dir(part) for part in rel_path.replace(os.sep, "/").split("/")):
                continue
            for f in sorted(files):
                if f.lower().endswith((".gpkg", ".sqlite")) and not f.startswith("~$"):
                    candidate_files.append(os.path.normpath(os.path.join(root, f)))
    else:
        for f in sorted(os.listdir(folder_path)):
            fpath = os.path.join(folder_path, f)
            if os.path.isfile(fpath) and f.lower().endswith((".gpkg", ".sqlite")) and not f.startswith("~$"):
                candidate_files.append(os.path.normpath(fpath))

    if target_pattern:
        pattern_matches = [f for f in candidate_files if target_pattern in os.path.basename(f).lower()]
        if pattern_matches:
            return pattern_matches

    return candidate_files


REQUIRED_GPKG_COLUMNS = [
    "map_uuid",
    "geocode",
    "region",
    "province",
    "city_mun",
    "barangay",
    "ean",
    "name",
    "code",
    "hhcount",
    "bldgcount",
    "sy",
    "new_ean",
    "hh_count",
    "bldg_count",
    "ea_type",
    "remarks",
]


def get_gpkg_layer_columns(source: Union[str, Any], table_name: Optional[str] = None) -> List[str]:
    """Extracts column/attribute names from a GeoPackage file or QgsVectorLayer."""
    if hasattr(source, "fields"):
        try:
            return [f.name() for f in source.fields()]
        except Exception:
            pass

    fpath = str(source).strip()
    if not fpath or not os.path.exists(fpath):
        return []

    try:
        conn = sqlite3.connect(fpath)
        cur = conn.cursor()
        target_table = table_name
        if not target_table:
            cur.execute("""
                SELECT g.table_name 
                FROM gpkg_geometry_columns g
                JOIN gpkg_contents c ON g.table_name = c.table_name
                WHERE UPPER(g.geometry_type_name) IN ('POLYGON', 'MULTIPOLYGON')
            """)
            poly_rows = cur.fetchall()
            if poly_rows:
                target_table = poly_rows[0][0]
            else:
                cur.execute("SELECT table_name FROM gpkg_contents LIMIT 1")
                row = cur.fetchone()
                if row:
                    target_table = row[0]

        if target_table:
            cur.execute(f'PRAGMA table_info("{target_table}")')
            cols = [r[1] for r in cur.fetchall()]
            conn.close()
            return cols
        conn.close()
    except Exception:
        pass
    return []


def validate_gpkg_columns(source: Union[str, Any], table_name: Optional[str] = None) -> Tuple[bool, List[str], List[str]]:
    """
    Validates that a GeoPackage layer or file has all required column headers before upload.
    Returns (is_valid, missing_columns, existing_columns).
    Matching is case-insensitive.
    """
    cols = get_gpkg_layer_columns(source, table_name=table_name)
    existing_lower = {str(c).strip().lower(): str(c) for c in cols if c}
    
    missing = []
    for req in REQUIRED_GPKG_COLUMNS:
        if req.lower() not in existing_lower:
            missing.append(req)

    return (len(missing) == 0), missing, cols


def get_gpkg_polygon_layer_info(fpath: str) -> Tuple[bool, List[str], str]:
    """
    Inspects a GeoPackage (.gpkg or .sqlite) file and returns:
    (has_polygon, polygon_table_names, summary_str)
    """
    if not fpath or not os.path.exists(fpath):
        return False, [], "File not found"
    try:
        conn = sqlite3.connect(fpath)
        cur = conn.cursor()
        cur.execute("""
            SELECT g.table_name, UPPER(g.geometry_type_name) 
            FROM gpkg_geometry_columns g
            JOIN gpkg_contents c ON g.table_name = c.table_name
        """)
        rows = cur.fetchall()
        conn.close()

        if not rows:
            return False, [], "Non-Spatial Table"

        poly_tables = [r[0] for r in rows if "POLYGON" in r[1]]
        all_types = sorted(list(set(r[1] for r in rows)))
        type_str = ", ".join(all_types)

        if poly_tables:
            return True, poly_tables, f"Polygon ({', '.join(poly_tables)})"
        else:
            return False, [], f"Non-Polygon ({type_str})"
    except Exception:
        return True, [], "GeoPackage"


def find_earf_files_in_folder(folder_path: str, recursive: bool = False, pattern: str = "_earf") -> List[str]:
    """
    Scans folder_path for all Preliminary EARF Excel files (.xlsx / .xls)
    that contain '_earf' in their filename (case-insensitive),
    excluding temporary files ('~$') and 'versions' directories.
    """
    if not folder_path or not os.path.exists(folder_path):
        return []

    earf_files = []
    target_pattern = pattern.lower().strip() if pattern else "_earf"

    if recursive:
        for root, dirs, files in os.walk(folder_path):
            dirs[:] = [d for d in dirs if not is_version_dir(d)]
            rel_path = os.path.relpath(root, folder_path)
            if rel_path != "." and any(is_version_dir(part) for part in rel_path.replace(os.sep, "/").split("/")):
                continue
            for f in sorted(files):
                f_low = f.lower()
                if f_low.endswith((".xlsx", ".xls")) and not f.startswith("~$"):
                    if target_pattern in f_low:
                        earf_files.append(os.path.normpath(os.path.join(root, f)))
    else:
        for f in sorted(os.listdir(folder_path)):
            fpath = os.path.join(folder_path, f)
            f_low = f.lower()
            if os.path.isfile(fpath) and f_low.endswith((".xlsx", ".xls")) and not f.startswith("~$"):
                if target_pattern in f_low:
                    earf_files.append(os.path.normpath(fpath))

    return earf_files




def is_summary_header_record(r: Dict[str, Any]) -> bool:
    """
    Option A: Checks whether a record is an administrative summary header row
    (Province summary, Municipality summary, or empty Barangay header with no counts/geometry).
    Genuine single-EA barangays (with POPCEN 5-EA, geometry, or counts) return False (kept).
    """
    gc = str(r.get("geocode", "") or "").strip()
    if not gc:
        return True
    if gc.endswith("00000000"):
        return True
    if gc.endswith("000000"):
        in_p = r.get("in_popcen") in (1, "1", True)
        in_g = r.get("in_gpkg") in (1, "1", True) or bool(r.get("has_geometry"))
        ne = str(r.get("new_enumeration_area_code") or r.get("new_ean") or "").strip()
        hh = str(r.get("household_count") or r.get("earf_household_count") or r.get("no_of_households") or "").strip()
        bldg = str(r.get("building_count") or r.get("earf_building_count") or "").strip()
        if not in_p and not in_g and (not ne or ne == "000000") and not hh and not bldg:
            return True
    return False


def compute_recoded_enumeration_areas(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Computes 'recoded_ean' (Column 22: Recoded Enumeration Area) per Barangay adhering to PSA EARF rules:
      - Scope: Per Barangay (PSGC prefix: prov_code + city_mun_code + barangay_code).
      - Option B: Keeps Column 16 ('new_enumeration_area_code' / 'new_ean') as the raw preliminary input code (e.g. '004003').
      - Rule A (Single EA Barangay): If only 1 resulting EA in barangay (or merged to 1 EA), code is strictly '000000'.
      - Rule B (Multi-EA Barangay - Retained / Base EAs): Sequentially numbered ending in '000' (e.g. '001000', '002000').
      - Rule C (Merged EA): Merged parts share the recoded code of their target merged EA.
      - Rule D (Delineated / Split EA - Appended to the last): Appended incrementally after the last base EA
        (e.g. if base EAs are 001000, 002000, 003000, the new split '004003' becomes '004000').
    """
    if not records:
        return records

    def is_base_code(code: str) -> bool:
        c = str(code or "").replace(".0", "").strip()
        if c.isdigit() and len(c) == 6 and c.endswith("000") and c != "000000":
            return True
        return False

    by_brgy = collections.defaultdict(list)
    for r in records:
        if is_summary_header_record(r):
            r["recoded_ean"] = ""
            continue
        gc = str(r.get("geocode", "") or "").strip()
        brgy_code = str(r.get("barangay_code", "") or "").strip()
        prov = str(r.get("province_code", "") or "").strip()
        mun = str(r.get("city_mun_code", "") or "").strip()

        if prov and mun and brgy_code:
            brgy_key = f"{prov.zfill(3)}{mun.zfill(2)}{brgy_code.zfill(3)}"
        elif len(gc) >= 8:
            if len(gc) == 16:
                brgy_key = gc[2:10]
            else:
                brgy_key = gc[:8]
        else:
            brgy_key = str(r.get("areaname", "") or r.get("geographic_name") or "UNKNOWN")
        by_brgy[brgy_key].append(r)

    for brgy_key, b_records in by_brgy.items():
        record_entities = []
        seen_base_keys = collections.defaultdict(int)
        seen_split_keys = collections.defaultdict(int)

        for r in b_records:
            # Preserve raw preliminary new_ean/new_enumeration_area_code in Column 16 (Option B)
            orig_nean = str(r.get("new_enumeration_area_code") or r.get("new_ean") or "").strip().replace(".0", "")
            if orig_nean == "1004":
                orig_nean = ""

            # Normalize raw preliminary code format if numeric without changing its identity
            if orig_nean:
                if orig_nean.isdigit():
                    if len(orig_nean) <= 3:
                        norm_nean = f"{int(orig_nean):03d}000"
                    elif len(orig_nean) == 4:
                        norm_nean = f"{int(orig_nean[:3]):03d}00{orig_nean[3:]}"
                    else:
                        norm_nean = f"{int(orig_nean):06d}"
                else:
                    norm_nean = orig_nean
                r["new_enumeration_area_code"] = norm_nean
                r["new_ean"] = norm_nean
            else:
                r["new_enumeration_area_code"] = ""
                r["new_ean"] = ""

            old_ean = str(r.get("ean", "") or "").strip().replace(".0", "")
            if old_ean.isdigit() and len(old_ean) < 6:
                old_ean = f"{int(old_ean):03d}000" if len(old_ean) <= 3 else f"{int(old_ean):06d}"

            ea_t = str(r.get("ea_type") or r.get("ea_lifecycle") or "").upper().strip()
            rem = str(r.get("remarks") or r.get("earf_remarks") or r.get("popcen_remarks") or "").strip()

            is_merged = "MERG" in ea_t or "MERG" in rem.upper()
            target_merged = ""
            if is_merged:
                m = re.search(r"merge[d\s]+(?:to|with|into)\s+(?:EA\s*)?(\d+)", rem, re.IGNORECASE)
                if m:
                    target_merged = m.group(1).zfill(6)
                elif orig_nean and is_base_code(orig_nean):
                    target_merged = orig_nean.zfill(6)
                elif old_ean and is_base_code(old_ean):
                    target_merged = old_ean.zfill(6)

            has_sub_code = bool(norm_nean.isdigit() and len(norm_nean) == 6 and not norm_nean.endswith("000") and norm_nean != "000000")
            is_explicit_split = bool("SPLIT" in ea_t or "DELIN" in ea_t or "delineat" in rem.lower())

            cand_base = old_ean if is_base_code(old_ean) else (norm_nean if is_base_code(norm_nean) else "")

            if is_merged:
                entity_key = target_merged if target_merged else (cand_base or "MERGED_TARGET")
                record_entities.append((r, entity_key, "MERGED", norm_nean, old_ean))
            elif has_sub_code:
                # Sub-code e.g. 004003, 003002 -> Appended Delineated entity
                entity_key = norm_nean
                record_entities.append((r, entity_key, "DELINEATED", norm_nean, old_ean))
            elif cand_base:
                if seen_base_keys[cand_base] == 0:
                    seen_base_keys[cand_base] += 1
                    entity_key = cand_base
                    record_entities.append((r, entity_key, "BASE", norm_nean, old_ean))
                else:
                    # Duplicate row for same base code -> Appended split part
                    seen_split_keys[cand_base] += 1
                    entity_key = f"{cand_base}_split_{seen_split_keys[cand_base]}"
                    record_entities.append((r, entity_key, "DELINEATED", norm_nean, old_ean))
            elif is_explicit_split:
                entity_key = norm_nean or f"SPLIT_{id(r)}"
                record_entities.append((r, entity_key, "DELINEATED", norm_nean, old_ean))
            else:
                entity_key = norm_nean or old_ean or f"BASE_{id(r)}"
                record_entities.append((r, entity_key, "BASE", norm_nean, old_ean))

        unique_entities = {ent[1] for ent in record_entities}

        # Rule A: Single-EA Barangay (or all merged into 1 EA)
        if len(unique_entities) <= 1:
            for r, ent_key, kind, norm_nean, old_ean in record_entities:
                r["recoded_ean"] = "000000"
            continue

        # Rule B & D: Multi-EA Barangay
        base_merged_entities = []
        delineated_entities = []

        for r, ent_key, kind, norm_nean, old_ean in record_entities:
            if kind == "DELINEATED":
                if ent_key not in delineated_entities:
                    delineated_entities.append(ent_key)
            else:
                if ent_key not in base_merged_entities:
                    base_merged_entities.append(ent_key)

        def sort_key(k: str):
            digits = "".join(c for c in k if c.isdigit())
            return (int(digits) if digits else 999999, k)

        sorted_base = sorted(base_merged_entities, key=sort_key)
        sorted_delineated = sorted(delineated_entities, key=sort_key)

        ordered_all_entities = sorted_base + sorted_delineated

        # Incremental alignment (k = 1, 2, ...): k -> f"{k:03d}000"
        recoding_map = {}
        for k, ent_key in enumerate(ordered_all_entities, start=1):
            recoding_map[ent_key] = f"{k:03d}000"

        for r, ent_key, kind, norm_nean, old_ean in record_entities:
            r["recoded_ean"] = recoding_map.get(ent_key, f"{len(ordered_all_entities):03d}000")

    return records


def audit_matched_flags(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Evaluates 5 critical PSA cartography validation flags across reconciled records:
      - FLAG-01: Duplicate rows based on new enumeration area code within each Barangay.
      - FLAG-02: EA subject for delineation/merging not processed without remarks.
      - FLAG-03: Source year consistency against GPKG ea_type (Retained -> 2024, Others -> 2026).
      - FLAG-04: EA count should not be null, empty, or zero for pure EA records.
      - FLAG-05: No duplicate final 14-digit geocode across physical features.
    Populates 'validation_flags' and 'flag_notes' in each record dict.
    """
    if not records:
        return records

    brgy_groups = collections.defaultdict(list)
    geocode_counts = collections.defaultdict(int)

    for r in records:
        if is_summary_header_record(r):
            r["validation_flags"] = ""
            r["flag_notes"] = ""
            continue
        gc = str(r.get("geocode") or "").strip()
        final_gc = str(r.get("ea_geocode") or gc).strip()
        if final_gc:
            geocode_counts[final_gc] += 1

        b_code = str(r.get("barangay_code") or "").strip().zfill(3)
        p_code = str(r.get("province_code") or "").strip()
        m_code = str(r.get("city_mun_code") or "").strip()
        if not p_code and len(gc) >= 3:
            p_code = gc[:3]
        if not m_code and len(gc) >= 5:
            m_code = gc[3:5]
        if (not b_code or b_code == "000") and len(gc) >= 8:
            b_code = gc[5:8]
        brgy_key = f"{p_code.zfill(3)}{m_code.zfill(2)}{b_code.zfill(3)}"
        brgy_groups[brgy_key].append(r)

    for brgy_key, b_recs in brgy_groups.items():
        nean_counts = collections.defaultdict(int)
        for r in b_recs:
            nean = str(r.get("new_enumeration_area_code") or r.get("new_ean") or "").strip().replace(".0", "")
            if nean and nean not in ("0", "000000", "-", "NONE", "NAN", "NULL"):
                nean_counts[nean] += 1

        for r in b_recs:
            flags = []
            notes = []

            # FLAG-01: Duplicate rows based on new enumeration area in barangay
            nean = str(r.get("new_enumeration_area_code") or r.get("new_ean") or "").strip().replace(".0", "")
            if nean and nean not in ("0", "000000", "-", "NONE", "NAN", "NULL"):
                if nean_counts[nean] > 1:
                    flags.append("FLAG-01")
                    notes.append(f"FLAG-01: Duplicate new EA code '{nean}' in Barangay ({nean_counts[nean]} rows)")

            # FLAG-02: EA subject for delineation/merging not processed without remarks
            delin_val = str(r.get("ea_subject_for_delineation") or r.get("popcen_delineation") or "").strip().upper()
            merg_val = str(r.get("ea_subject_for_merging") or r.get("popcen_merging") or "").strip().upper()
            is_subject = bool(delin_val in ("1", "YES", "TRUE", "Y") or merg_val in ("1", "YES", "TRUE", "Y"))
            ea_life = str(r.get("ea_lifecycle") or "").strip().upper()
            ea_t = str(r.get("ea_type") or "").strip().upper()
            is_retained = ("RETAIN" in ea_life or "RETAIN" in ea_t or not (is_subject and any(k in ea_life for k in ("SPLIT", "MERG", "DELIN"))))
            rem = str(r.get("remarks") or r.get("earf_remarks") or r.get("popcen_remarks") or "").strip()

            if is_subject and is_retained and not rem:
                reason = "delineation" if delin_val in ("1", "YES", "TRUE", "Y") else "merging"
                flags.append("FLAG-02")
                notes.append(f"FLAG-02: EA subject for {reason} not processed without explanatory remarks")

            # FLAG-03: if ea_type is retained then sy is 2024, else 2026
            sy = str(r.get("source_year") or r.get("sy") or "").strip().replace(".0", "")
            check_ea_type = ea_t or ea_life
            if "RETAIN" in check_ea_type:
                if sy and sy != "2024":
                    flags.append("FLAG-03")
                    notes.append(f"FLAG-03: Inconsistent SY '{sy}' for Retained EA (expected 2024)")
            elif any(k in check_ea_type for k in ("SPLIT", "MERG", "DELIN", "RESET")):
                if sy and sy != "2026":
                    flags.append("FLAG-03")
                    notes.append(f"FLAG-03: Inconsistent SY '{sy}' for {check_ea_type} EA (expected 2026)")

            # FLAG-04: ea count should not be null
            eacnt = str(r.get("no_of_eas") or r.get("eacount") or r.get("number_of_eas") or "").strip().replace(".0", "")
            if not eacnt or eacnt.upper() in ("NONE", "NAN", "NULL"):
                eacnt = str(r.get("household_count") or r.get("earf_household_count") or r.get("no_of_households") or "").strip().replace(".0", "")
            if not eacnt or eacnt in ("0", "NONE", "NAN", "NULL"):
                flags.append("FLAG-04")
                notes.append("FLAG-04: EA count is null, blank, or zero")

            # FLAG-05: no duplicate geocode
            gc = str(r.get("geocode") or "").strip()
            final_gc = str(r.get("ea_geocode") or gc).strip()
            if final_gc and geocode_counts[final_gc] > 1:
                flags.append("FLAG-05")
                notes.append(f"FLAG-05: Duplicate geocode detected ({geocode_counts[final_gc]} occurrences)")

            r["validation_flags"] = "; ".join(flags)
            r["flag_notes"] = "; ".join(notes)

    return records


def export_matched_to_excel(
    records: List[Dict[str, Any]], 
    out_path: str,
    metadata: Optional[Dict[str, str]] = None
):
    """
    Exports matched records to the official PSA Final EARF Excel workbook (.xlsx)
    matching the exact 15-column template structure:
      - Sheet 1: '2026 Final EARF' (15-column PSA official hierarchical format)
        - Row 1: '2026 Final Enumeration Area Reference File'
        - Row 2: 'As of Oct 2026'
        - Row 3: Group Headers ('2024 EARF' [A3:H3] | '2026 Preliminary EA' [I3:O3])
        - Row 4: Column Headers (Prov, Mun, Brgy, EA, Number of EAs, Province/City/Barangay/EA name,
                 Number of Households, Number of Buildings, Prelim EA Code, Final EA Code,
                 Household Count, Building Count, EA Type, Source Year, Remarks)
        - Row 5: Numbered tags (2) to (14)
        - Row 6+: Hierarchical rows (Province summary, Municipality summary, Barangay summary,
                 and pure EA rows with recoded_ean in Final EA Code, peach highlight for MERGED EAs)
      - Sheet 2: 'Reconciliation Log' (22-column comprehensive side-by-side reconciliation)
    """
    import openpyxl
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    from openpyxl.utils import get_column_letter

    # Filter out administrative summary headers so only pure EAs are in records
    pure_records = []
    for r in records:
        if not is_summary_header_record(r):
            pure_records.append(r)
    records = pure_records

    # Ensure recoded_ean is populated across records
    if any(not r.get("recoded_ean") for r in records):
        records = compute_recoded_enumeration_areas(records)

    wb = openpyxl.Workbook()
    
    # ----------------------------------------------------
    # SHEET 1: Official 15-Column PSA Final EARF Template
    # ----------------------------------------------------
    ws1 = wb.active
    ws1.title = "Final EARF"

    # Styling Palette
    fill_green_grp = PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid")
    fill_blue_grp = PatternFill(start_color="D9E1F2", end_color="D9E1F2", fill_type="solid")
    fill_peach_merged = PatternFill(start_color="FCE4D6", end_color="FCE4D6", fill_type="solid")
    fill_neutral_gray = PatternFill(start_color="F2F2F2", end_color="F2F2F2", fill_type="solid")

    font_title = Font(name="Calibri", size=14, bold=True, color="000000")
    font_subtitle = Font(name="Calibri", size=10, bold=False, color="000000")
    font_group_header = Font(name="Calibri", size=11, bold=True, color="000000")
    font_col_header = Font(name="Calibri", size=10, bold=True, color="000000")
    font_col_num = Font(name="Calibri", size=9, bold=False, color="000000")
    font_bold = Font(name="Calibri", size=10, bold=True, color="000000")
    font_data = Font(name="Calibri", size=10, color="000000")
    font_final_ea = Font(name="Calibri", size=10, bold=True, color="000000")

    thin_border = Border(
        left=Side(style='thin', color='D9D9D9'),
        right=Side(style='thin', color='D9D9D9'),
        top=Side(style='thin', color='D9D9D9'),
        bottom=Side(style='thin', color='D9D9D9')
    )
    header_border = Border(
        left=Side(style='thin', color='D9D9D9'),
        right=Side(style='thin', color='D9D9D9'),
        top=Side(style='thin', color='D9D9D9'),
        bottom=Side(style='medium', color='000000')
    )

    center_align = Alignment(horizontal="center", vertical="center")
    wrap_center_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
    left_align = Alignment(horizontal="left", vertical="center")
    right_align = Alignment(horizontal="right", vertical="center")

    # Row 1: Title
    ws1["A1"] = "2026 Final Enumeration Area Reference File"
    ws1["A1"].font = font_title

    # Row 2: Subtitle (Reflects the month and year the file was created)
    as_of_val = ""
    if metadata and isinstance(metadata, dict):
        as_of_val = metadata.get("as_of") or metadata.get("created_at")
    if not as_of_val:
        as_of_val = datetime.now().strftime("%b %Y")
    as_of_text = str(as_of_val).strip()
    if not as_of_text.lower().startswith("as of"):
        as_of_text = f"As of {as_of_text}"
    ws1["A2"] = as_of_text
    ws1["A2"].font = font_subtitle

    # Row 3: Major Group Headers
    # Column A (Geocode) is standalone / unmerged from 2024 EARF with neutral fill
    cell_a3 = ws1.cell(row=3, column=1, value="")
    cell_a3.fill = fill_neutral_gray
    cell_a3.border = thin_border

    # 2024 EARF covers Prov to Number of Buildings (Cols B to I)
    ws1.merge_cells("B3:I3")
    ws1["B3"] = "2024 EARF"
    ws1["B3"].font = font_group_header
    ws1["B3"].alignment = center_align

    # 2026 Preliminary EA covers Prelim EA Code to Remarks (Cols J to P)
    ws1.merge_cells("J3:P3")
    ws1["J3"] = "2026 Preliminary EA"
    ws1["J3"].font = font_group_header
    ws1["J3"].alignment = center_align

    for c in range(2, 10):
        cell = ws1.cell(row=3, column=c)
        cell.fill = fill_green_grp
        cell.border = thin_border
    for c in range(10, 17):
        cell = ws1.cell(row=3, column=c)
        cell.fill = fill_blue_grp
        cell.border = thin_border

    # Row 4: Column Headers (16 Columns with Geocode as Column 1)
    col_names = [
        "Geocode",
        "Prov", "Mun", "Brgy", "EA", "Number of\nEAs",
        "Province, City, Municipality,\nBarangay, and EA",
        "Number of\nHouseholds", "Number of\nBuildings",
        "Prelim Enumeration\nArea Code", "Final Enumeration\nArea Code",
        "Household\nCount", "Building\nCount", "EA Type",
        "Source\nYear", "Remarks"
    ]

    for idx, name in enumerate(col_names, start=1):
        cell = ws1.cell(row=4, column=idx, value=name)
        cell.font = font_col_header
        cell.alignment = wrap_center_align
        if idx == 1:
            cell.fill = fill_neutral_gray
        elif idx <= 9:
            cell.fill = fill_green_grp
        else:
            cell.fill = fill_blue_grp
        cell.border = thin_border

    # Row 5: Column Numbers ((1) to (14))
    col_nums = [
        "(1)",
        "(2)", "(3)", "(4)", "(5)", "(6)",
        "(7)", "(8)", "(9)",
        "(10)", "",
        "(11)", "(12)", "(13)", "(14)", ""
    ]

    for idx, num in enumerate(col_nums, start=1):
        cell = ws1.cell(row=5, column=idx, value=num)
        cell.font = font_col_num
        cell.alignment = center_align
        if idx == 1:
            cell.fill = fill_neutral_gray
        elif idx <= 9:
            cell.fill = fill_green_grp
        else:
            cell.fill = fill_blue_grp
        cell.border = header_border

    ws1.row_dimensions[1].height = 26
    ws1.row_dimensions[2].height = 18
    ws1.row_dimensions[3].height = 22
    ws1.row_dimensions[4].height = 32
    ws1.row_dimensions[5].height = 18

    # Hierarchy Resolution
    resolved_prov_code = ""
    resolved_mun_code = ""
    resolved_prov_name = ""
    resolved_mun_name = ""

    if metadata:
        p_meta = str(metadata.get("province_code") or "").strip()
        m_meta = str(metadata.get("city_mun_code") or "").strip()
        if p_meta:
            resolved_prov_code = p_meta.zfill(3)
        if m_meta:
            resolved_mun_code = m_meta.zfill(2)
        resolved_prov_name = str(metadata.get("province") or "").strip().upper()
        resolved_mun_name = str(metadata.get("city_mun") or "").strip().upper()

    if records and (not resolved_prov_name or not resolved_mun_name or not resolved_prov_code or not resolved_mun_code):
        f_rec = records[0]
        f_gc = str(f_rec.get("geocode") or "")
        if not resolved_prov_code:
            p_val = str(f_rec.get("province_code") or (f_gc[:3] if len(f_gc) >= 3 else "")).strip()
            if p_val:
                resolved_prov_code = p_val.zfill(3)
        if not resolved_mun_code:
            m_val = str(f_rec.get("city_mun_code") or (f_gc[3:5] if len(f_gc) >= 5 else "")).strip()
            if m_val:
                resolved_mun_code = m_val.zfill(2)
        if not resolved_prov_name or not resolved_mun_name:
            try:
                db_inst = POPCENDatabase()
                h_info = db_inst.get_citymun_hierarchy(f_gc[:5])
                resolved_prov_name = resolved_prov_name or str(h_info.get("province") or "PROVINCE").strip().upper()
                resolved_mun_name = resolved_mun_name or str(h_info.get("city_mun") or "MUNICIPALITY").strip().upper()
            except Exception:
                pass

    if not resolved_prov_code:
        resolved_prov_code = "000"
    if not resolved_mun_code:
        resolved_mun_code = "00"
    if not resolved_prov_name:
        resolved_prov_name = "PROVINCE"
    if not resolved_mun_name:
        resolved_mun_name = "MUNICIPALITY"

    # Group records by (prov_code, mun_code)
    muns_dict = collections.defaultdict(lambda: collections.defaultdict(list))
    for r in records:
        p_c = str(r.get("province_code") or "").strip()
        if p_c:
            p_c = p_c.zfill(3)
        elif len(str(r.get("geocode") or "")) >= 3:
            p_c = str(r.get("geocode"))[:3]
        else:
            p_c = resolved_prov_code

        m_c = str(r.get("city_mun_code") or "").strip()
        if m_c:
            m_c = m_c.zfill(2)
        elif len(str(r.get("geocode") or "")) >= 5:
            m_c = str(r.get("geocode"))[3:5]
        else:
            m_c = resolved_mun_code
        b_c = str(r.get("barangay_code") or "").strip().zfill(3)
        if not b_c or b_c == "000":
            gc = str(r.get("geocode") or "")
            if len(gc) >= 8:
                b_c = gc[5:8]
        muns_dict[(p_c, m_c)][b_c].append(r)

    current_row = 6

    # Calculate overall totals for Province row
    tot_eas = len(records)
    tot_hh = 0
    tot_bldg = 0
    for r in records:
        try:
            p_hh = r.get("household_count") or r.get("earf_household_count") or r.get("no_of_households") or r.get("popcen_households") or 0
            tot_hh += int(p_hh)
        except Exception:
            pass
        try:
            p_bldg = r.get("building_count") or r.get("earf_building_count") or r.get("number_of_buildings") or 0
            tot_bldg += int(p_bldg)
        except Exception:
            pass

    # 1. Province Header Row (Row 6)
    prov_gc = f"{resolved_prov_code}00000000000"
    prov_row_vals = [
        prov_gc, resolved_prov_code, "00", "000", "000000", tot_eas,
        resolved_prov_name, tot_hh, tot_bldg,
        "", "", "", "", "", "", ""
    ]
    for c_idx, val in enumerate(prov_row_vals, start=1):
        cell = ws1.cell(row=current_row, column=c_idx, value=val)
        cell.font = font_bold
        cell.fill = fill_neutral_gray if c_idx == 1 else fill_green_grp
        cell.border = thin_border
        if c_idx in (1, 2, 3, 4, 5):
            cell.alignment = center_align
            cell.number_format = "@"
        elif c_idx == 6:
            cell.alignment = right_align
            if isinstance(val, int):
                cell.number_format = "#,##0"
        elif c_idx == 7:
            cell.alignment = left_align
        elif c_idx in (8, 9):
            cell.alignment = right_align
            if isinstance(val, int):
                cell.number_format = "#,##0"
    ws1.row_dimensions[current_row].height = 20
    current_row += 1

    # 2. Iterate Municipalities
    for (p_c, m_c), brgys_map in sorted(muns_dict.items()):
        # Municipality Header Row
        m_name_disp = resolved_mun_name
        mun_gc = f"{p_c}{m_c}000000000"
        mun_row_vals = [
            mun_gc, p_c, m_c, "000", "000000", "",
            m_name_disp, "", "",
            "", "", "", "", "", "", ""
        ]
        for c_idx, val in enumerate(mun_row_vals, start=1):
            cell = ws1.cell(row=current_row, column=c_idx, value=val)
            cell.font = font_bold
            cell.border = thin_border
            if c_idx in (1, 2, 3, 4, 5):
                cell.alignment = center_align
                cell.number_format = "@"
            elif c_idx == 7:
                cell.alignment = left_align
        ws1.row_dimensions[current_row].height = 20
        current_row += 1

        # Iterate Barangays
        for b_code in sorted(brgys_map.keys()):
            b_recs = brgys_map[b_code]
            b_name = str(b_recs[0].get("geographic_name") or b_recs[0].get("areaname") or "").strip().upper()
            if " - EA" in b_name:
                b_name = b_name.split(" - EA")[0].strip()

            # Barangay Header Row
            brgy_gc = f"{p_c}{m_c}{b_code}000000"
            brgy_row_vals = [
                brgy_gc, p_c, m_c, b_code, "000000", "",
                b_name, "", "",
                "", "", "", "", "", "", ""
            ]
            for c_idx, val in enumerate(brgy_row_vals, start=1):
                cell = ws1.cell(row=current_row, column=c_idx, value=val)
                cell.font = font_bold
                cell.border = thin_border
                if c_idx in (1, 2, 3, 4, 5):
                    cell.alignment = center_align
                    cell.number_format = "@"
                elif c_idx == 7:
                    cell.alignment = left_align
            ws1.row_dimensions[current_row].height = 20
            current_row += 1

            # EA Data Rows
            for r in b_recs:
                ean_raw = str(r.get("ean") or "000000").strip()
                if ean_raw.isdigit() and len(ean_raw) < 6:
                    ean_raw = f"{int(ean_raw):03d}000" if len(ean_raw) <= 3 else f"{int(ean_raw):06d}"

                prelim_ea = str(r.get("new_enumeration_area_code") or r.get("new_ean") or "").strip()
                if prelim_ea.isdigit() and len(prelim_ea) < 6:
                    prelim_ea = f"{int(prelim_ea):03d}000" if len(prelim_ea) <= 3 else f"{int(prelim_ea):06d}"

                final_ea = str(r.get("recoded_ean") or "").strip()
                if final_ea.isdigit() and len(final_ea) < 6:
                    final_ea = f"{int(final_ea):03d}000" if len(final_ea) <= 3 else f"{int(final_ea):06d}"
                if not final_ea:
                    final_ea = prelim_ea or ean_raw

                # Exact ea_geocode matching the exported GPKG (bgy_geocode + final_ea / gpkg_ean)
                bgy_gc = f"{p_c.zfill(3)}{m_c.zfill(2)}{b_code.zfill(3)}"
                gpkg_ean = final_ea or prelim_ea or ean_raw
                ea_gc = str(r.get("ea_geocode") or "").strip()
                if not ea_gc or len(ea_gc) != 14:
                    ea_gc = f"{bgy_gc}{gpkg_ean}"

                p_hh_val = r.get("no_of_households") or r.get("popcen_households") or ""
                p_bldg_val = r.get("building_count") or r.get("number_of_buildings") or ""

                e_hh_val = r.get("household_count") or r.get("earf_household_count") or p_hh_val or ""
                e_bldg_val = r.get("building_count") or r.get("earf_building_count") or p_bldg_val or ""

                ea_t = str(r.get("ea_type") or r.get("ea_lifecycle") or "RETAINED").strip().upper()
                if "MERG" in ea_t:
                    ea_t = "MERGED"
                elif "SPLIT" in ea_t:
                    ea_t = "SPLIT"
                elif "DELIN" in ea_t:
                    ea_t = "DELINEATED"
                elif "RETAIN" in ea_t:
                    ea_t = "RETAINED"

                src_y = str(r.get("source_year") or ("2024" if ea_t == "RETAINED" else "2026")).strip()
                rem_str = str(r.get("remarks") or r.get("earf_remarks") or "").strip()

                is_merged = (ea_t == "MERGED")

                effective_name_ean = final_ea or prelim_ea or ean_raw
                ea_row_vals = [
                    ea_gc, p_c, m_c, b_code, ean_raw, 1,
                    f"EA {effective_name_ean}",
                    int(p_hh_val) if str(p_hh_val).isdigit() else p_hh_val,
                    int(p_bldg_val) if str(p_bldg_val).isdigit() else p_bldg_val,
                    prelim_ea, final_ea,
                    int(e_hh_val) if str(e_hh_val).isdigit() else e_hh_val,
                    int(e_bldg_val) if str(e_bldg_val).isdigit() else e_bldg_val,
                    ea_t, src_y, rem_str
                ]

                for c_idx, val in enumerate(ea_row_vals, start=1):
                    cell = ws1.cell(row=current_row, column=c_idx, value=val)
                    cell.border = thin_border
                    if is_merged:
                        cell.fill = fill_peach_merged

                    if c_idx == 11:  # Final EA Code (from recoded_ean)
                        cell.font = font_final_ea
                        cell.alignment = center_align
                        cell.number_format = "@"
                    elif c_idx in (1, 2, 3, 4, 5, 10, 14, 15):
                        cell.font = font_data
                        cell.alignment = center_align
                        if c_idx in (1, 2, 3, 4, 5, 10):
                            cell.number_format = "@"
                    elif c_idx in (6, 8, 9, 12, 13):
                        cell.font = font_data
                        cell.alignment = right_align
                        if isinstance(val, int):
                            cell.number_format = "#,##0"
                    else:
                        cell.font = font_data
                        cell.alignment = left_align

                ws1.row_dimensions[current_row].height = 20
                current_row += 1

    # Format Column Widths for Sheet 1 (16 Columns: A to P)
    col_w_map = {
        "A": 18, "B": 8, "C": 8, "D": 8, "E": 10,
        "F": 14, "G": 36, "H": 14, "I": 14, "J": 16,
        "K": 16, "L": 14, "M": 14, "N": 14, "O": 12, "P": 30
    }
    for col_letter, width in col_w_map.items():
        ws1.column_dimensions[col_letter].width = width

    ws1.freeze_panes = "G6" 
    ws1.views.sheetView[0].showGridLines = True

    # ----------------------------------------------------
    # SHEET 2: Full 22-Column Reconciliation Log
    # ----------------------------------------------------
    ws2 = wb.create_sheet(title="Reconciliation Log")

    fill_popcen_s2 = PatternFill(start_color="FDE8E8", end_color="FDE8E8", fill_type="solid")
    fill_prelim_s2 = PatternFill(start_color="DEF7EC", end_color="DEF7EC", fill_type="solid")
    fill_final_s2 = PatternFill(start_color="E1EFFE", end_color="E1EFFE", fill_type="solid")
    fill_rec_cell_s2 = PatternFill(start_color="EFF6FF", end_color="EFF6FF", fill_type="solid")
    font_rec_s2 = Font(name="Calibri", size=10, bold=True, color="1E40AF")

    ws2.merge_cells("A1:O1")
    ws2["A1"] = "2024 POPCEN-CBMS Worksheet"
    ws2["A1"].font = font_group_header
    ws2["A1"].alignment = center_align

    ws2.merge_cells("P1:V1")
    ws2["P1"] = "2026 EARF Worksheet"
    ws2["P1"].font = font_group_header
    ws2["P1"].alignment = center_align

    for col in range(1, 16):
        c = ws2.cell(row=1, column=col)
        c.fill = fill_popcen_s2
        c.border = thin_border
    for col in range(16, 23):
        c = ws2.cell(row=1, column=col)
        c.fill = fill_prelim_s2 if col <= 21 else fill_final_s2
        c.border = thin_border

    headers_s2 = [
        "Geocode", "Geographic Name", "Geographic Level", "region_code", "province_code",
        "city_mun_code", "barangay_code", "ean", "No. of EAs", "Areaname",
        "Household Population", "No. of Households", "EA Subject for Delineation", "EA Subject for Merging", "Remarks",
        "New Enumeration Area Code", "Household Count", "Building Count", "EA Type", "Source Year", "Remarks",
        "Recoded Enumeration Area"
    ]

    for col_idx, h_text in enumerate(headers_s2, start=1):
        cell = ws2.cell(row=2, column=col_idx, value=h_text)
        cell.font = font_col_header
        cell.alignment = wrap_center_align
        cell.fill = fill_popcen_s2 if col_idx <= 15 else (fill_prelim_s2 if col_idx <= 21 else fill_final_s2)
        cell.border = thin_border

    field_keys_s2 = [
        "geocode", "geographic_name", "geographic_level", "region_code", "province_code",
        "city_mun_code", "barangay_code", "ean", "no_of_eas", "areaname",
        "household_population", "no_of_households", "ea_subject_for_delineation", "ea_subject_for_merging", "popcen_remarks",
        "new_enumeration_area_code", "household_count", "building_count", "ea_type", "source_year", "earf_remarks",
        "recoded_ean"
    ]

    for row_idx, r in enumerate(records, start=3):
        is_pop = r.get("in_popcen") in (1, "1", True) and bool(r.get("geographic_name"))
        for col_idx, k in enumerate(field_keys_s2, start=1):
            if not is_pop and k in (
                "geographic_name", "geographic_level", "region_code", "province_code",
                "city_mun_code", "barangay_code", "ean", "no_of_eas", "areaname",
                "household_population", "no_of_households", "ea_subject_for_delineation",
                "ea_subject_for_merging", "popcen_remarks"
            ):
                val = ""
            else:
                val = r.get(k, "")
                if (val is None or val == "") and k == "new_enumeration_area_code":
                    val = r.get("new_ean", "")
                elif (val is None or val == "") and k == "household_count":
                    val = r.get("earf_household_count") or r.get("earf_households", "")
                elif (val is None or val == "") and k == "building_count":
                    val = r.get("earf_building_count", "")
                elif (val is None or val == "") and k == "earf_remarks":
                    val = r.get("remarks", "")
                elif (val is None or val == "") and k == "popcen_remarks" and is_pop:
                    val = r.get("remarks", "")
                elif (val is None or val == "") and k == "no_of_households" and is_pop:
                    val = r.get("popcen_households", "")
                elif (val is None or val == "") and k == "ea_subject_for_delineation" and is_pop:
                    val = r.get("popcen_delineation", "")
                elif (val is None or val == "") and k == "ea_subject_for_merging" and is_pop:
                    val = r.get("popcen_merging", "")

            cell_val = str(val).strip() if val is not None else ""
            if cell_val.endswith(".0"):
                cell_val = cell_val[:-2]

            cell = ws2.cell(row=row_idx, column=col_idx)
            cell.border = thin_border

            if k == "recoded_ean":
                cell.value = cell_val
                cell.font = font_rec_s2
                cell.fill = fill_rec_cell_s2
                cell.alignment = center_align
            elif k in ("geocode", "region_code", "province_code", "city_mun_code", "barangay_code", "ean", "new_enumeration_area_code", "source_year"):
                cell.value = cell_val
                cell.font = font_data
                cell.alignment = center_align
            elif k in ("no_of_eas", "household_population", "no_of_households", "household_count", "building_count"):
                if cell_val.isdigit():
                    cell.value = int(cell_val)
                    cell.number_format = "#,##0"
                else:
                    cell.value = cell_val
                cell.font = font_data
                cell.alignment = right_align
            else:
                cell.value = cell_val
                cell.font = font_data
                cell.alignment = left_align

        ws2.row_dimensions[row_idx].height = 20

    for col in ws2.columns:
        col_letter = get_column_letter(col[0].column)
        lengths = [len(str(cell.value or '')) for cell in col[1:]]
        max_len = max(lengths) if lengths else 10
        ws2.column_dimensions[col_letter].width = max(max_len + 3, 12)

    ws2.row_dimensions[1].height = 24
    ws2.row_dimensions[2].height = 28
    ws2.freeze_panes = "B3"
    ws2.views.sheetView[0].showGridLines = True

    # Make Sheet 1 the default active sheet
    wb.active = ws1
    wb.save(out_path)


def export_matched_to_mis_excel(
    records: List[Dict[str, Any]], 
    out_path: str,
    metadata: Optional[Dict[str, str]] = None
):
    """
    Exports matched records to the official PSA MIS Excel workbook (.xlsx)
    with the exact 18-column schema:
      fid, reg, prv, mun, bgy, ea, eaindicator, areaname, implementation,
      Remarks, survey_indicator, RSSO, PSO, bgy_geocode, ea_geocode,
      total_population, households, total_workload
    """
    import openpyxl
    from openpyxl.styles import Font, Alignment, Border, Side

    pure_records = [r for r in records if not is_summary_header_record(r)]
    records = pure_records
    if not records:
        return

    if any(not r.get("recoded_ean") for r in records):
        records = compute_recoded_enumeration_areas(records)

    reg_lookup = {}
    prov_lookup = {}
    mun_lookup = {}
    brgy_lookup = {}

    pop_path = get_default_db_path()
    if pop_path and os.path.exists(pop_path):
        try:
            with sqlite3.connect(pop_path) as conn:
                cur = conn.cursor()
                cur.execute("SELECT region_code, areaname, geographic_name FROM popcen_records WHERE geographic_level LIKE '%1 - Region%'")
                for r in cur.fetchall():
                    rc = str(r[0] or "").strip().zfill(2)
                    if rc:
                        reg_lookup[rc] = (str(r[1] or "").strip(), str(r[2] or "").strip())

                cur.execute("SELECT province_code, areaname, geographic_name, region_code FROM popcen_records WHERE geographic_level LIKE '%2 - Province%'")
                for r in cur.fetchall():
                    pc = str(r[0] or "").strip().zfill(3)
                    if pc:
                        prov_lookup[pc] = (str(r[1] or "").strip(), str(r[2] or "").strip(), str(r[3] or "").strip().zfill(2))

                cur.execute("SELECT province_code, city_mun_code, areaname, geographic_name FROM popcen_records WHERE geographic_level LIKE '%3 - City%'")
                for r in cur.fetchall():
                    pc = str(r[0] or "").strip().zfill(3)
                    mc = str(r[1] or "").strip().zfill(2)
                    if pc and mc:
                        mun_lookup[(pc, mc)] = (str(r[2] or "").strip(), str(r[3] or "").strip())

                cur.execute("SELECT province_code, city_mun_code, barangay_code, areaname, geographic_name FROM popcen_records WHERE geographic_level LIKE '%4 - Barangay%'")
                for r in cur.fetchall():
                    pc = str(r[0] or "").strip().zfill(3)
                    mc = str(r[1] or "").strip().zfill(2)
                    bc = str(r[2] or "").strip().zfill(3)
                    if pc and mc and bc:
                        brgy_lookup[(pc, mc, bc)] = (str(r[3] or "").strip(), str(r[4] or "").strip())
        except Exception:
            pass

    def _parse_count(val: Any) -> int:
        if val is None or val == "":
            return 0
        s = str(val).replace(",", "").strip()
        try:
            return int(round(float(s)))
        except Exception:
            return 0

    hierarchy = collections.defaultdict(lambda: collections.defaultdict(lambda: collections.defaultdict(list)))
    prov_reg_map = {}

    for r in records:
        gc = str(r.get("geocode") or "").strip()
        p_c = str(r.get("province_code") or "").strip()
        if p_c:
            p_c = p_c.zfill(3)
        elif len(gc) >= 3:
            p_c = gc[:3].zfill(3)
        elif metadata and metadata.get("province_code"):
            p_c = str(metadata.get("province_code")).strip().zfill(3)
        else:
            p_c = "000"

        m_c = str(r.get("city_mun_code") or "").strip()
        if m_c:
            m_c = m_c.zfill(2)
        elif len(gc) >= 5:
            m_c = gc[3:5].zfill(2)
        elif metadata and metadata.get("city_mun_code"):
            m_c = str(metadata.get("city_mun_code")).strip().zfill(2)
        else:
            m_c = "00"

        b_c = str(r.get("barangay_code") or "").strip().zfill(3)
        if not b_c or b_c == "000":
            if len(gc) >= 8:
                b_c = gc[5:8].zfill(3)
            else:
                b_c = "001"

        r_c = str(r.get("region_code") or "").strip()
        if r_c:
            r_c = r_c.zfill(2)
        elif p_c in prov_lookup and prov_lookup[p_c][2]:
            r_c = prov_lookup[p_c][2]
        elif metadata and metadata.get("region_code"):
            r_c = str(metadata.get("region_code")).strip().zfill(2)
        else:
            r_c = "00"

        prov_reg_map[p_c] = r_c
        hierarchy[p_c][m_c][b_c].append(r)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "MIS"

    font_header = Font(name="Calibri", size=11, bold=True, color="000000")
    font_data = Font(name="Calibri", size=11, color="000000")

    thin_border = Border(
        left=Side(style='thin', color='D9D9D9'),
        right=Side(style='thin', color='D9D9D9'),
        top=Side(style='thin', color='D9D9D9'),
        bottom=Side(style='thin', color='D9D9D9')
    )

    left_align = Alignment(horizontal="left", vertical="center")
    right_align = Alignment(horizontal="right", vertical="center")

    mis_headers = [
        "fid", "reg", "prv", "mun", "bgy", "ea", "eaindicator", "areaname",
        "implementation", "Remarks", "survey_indicator", "RSSO", "PSO",
        "bgy_geocode", "ea_geocode", "total_population", "households", "total_workload"
    ]

    for col_idx, h_text in enumerate(mis_headers, start=1):
        c = ws.cell(row=1, column=col_idx, value=h_text)
        c.font = font_header
        c.border = thin_border
        c.alignment = left_align

    ws.row_dimensions[1].height = 24

    current_fid = 1
    current_row = 2
    default_impl = "PSA-funded and facilitate 1st to 6th income class cities and municipalities"

    for p_c in sorted(hierarchy.keys()):
        r_c = prov_reg_map.get(p_c, "00")
        p_info = prov_lookup.get(p_c, ("", "", ""))
        r_info = reg_lookup.get(r_c, ("", ""))

        rsso = ""
        if metadata and metadata.get("region"):
            rsso = str(metadata.get("region")).strip()
        if not rsso:
            rsso = r_info[1] or r_info[0] or f"Region {r_c}"

        pso = ""
        if metadata and metadata.get("province"):
            pso = str(metadata.get("province")).strip()
        if not pso:
            pso = p_info[1] or p_info[0] or f"Province {p_c}"

        prov_areaname = (p_info[0] or pso).upper()

        prov_eas_count = 0
        prov_pop_total = 0
        prov_hh_total = 0
        prov_wl_total = 0

        for m_c, brgys_map in hierarchy[p_c].items():
            for b_c, b_recs in brgys_map.items():
                prov_eas_count += len(b_recs)
                for r in b_recs:
                    ea_pop = _parse_count(r.get("household_population"))
                    ea_hh = _parse_count(r.get("no_of_households") or r.get("popcen_households") or r.get("household_count"))
                    ea_wl = _parse_count(r.get("household_count") or r.get("earf_household_count") or r.get("estimated_number_of_households") or ea_hh)
                    prov_pop_total += ea_pop
                    prov_hh_total += ea_hh
                    prov_wl_total += ea_wl

        prov_bgy_gc = f"{p_c.zfill(3)}00000"
        prov_ea_gc = f"{prov_bgy_gc}000000"
        p_row_data = [
            (current_fid, right_align, "0"),
            (r_c, left_align, "@"),
            (p_c, left_align, "@"),
            ("00", left_align, "@"),
            ("000", left_align, "@"),
            ("000000", left_align, "@"),
            (prov_eas_count, right_align, "0"),
            (prov_areaname, left_align, None),
            ("", left_align, None),
            ("", left_align, None),
            (2, right_align, "0"),
            (rsso, left_align, None),
            (pso, left_align, None),
            (prov_bgy_gc, left_align, "@"),
            (prov_ea_gc, left_align, "@"),
            ("", right_align, None),
            ("", right_align, None),
            ("", right_align, None),
        ]
        for c_idx, (val, align, num_fmt) in enumerate(p_row_data, start=1):
            cell = ws.cell(row=current_row, column=c_idx, value=val)
            cell.font = font_data
            cell.border = thin_border
            cell.alignment = align
            if num_fmt:
                cell.number_format = num_fmt

        ws.row_dimensions[current_row].height = 20
        current_fid += 1
        current_row += 1

        for m_c in sorted(hierarchy[p_c].keys()):
            brgys_map = hierarchy[p_c][m_c]
            m_info = mun_lookup.get((p_c, m_c), ("", ""))
            mun_name = ""
            if metadata and metadata.get("city_mun"):
                mun_name = str(metadata.get("city_mun")).strip()
            if not mun_name:
                mun_name = m_info[0] or m_info[1] or f"MUNICIPALITY {m_c}"
            mun_areaname = mun_name.upper()

            mun_bgy_gc = f"{p_c.zfill(3)}{m_c.zfill(2)}000"
            mun_ea_gc = f"{mun_bgy_gc}000000"
            m_row_data = [
                (current_fid, right_align, "0"),
                (r_c, left_align, "@"),
                (p_c, left_align, "@"),
                (m_c, left_align, "@"),
                ("000", left_align, "@"),
                ("000000", left_align, "@"),
                ("", right_align, None),
                (mun_areaname, left_align, None),
                ("", left_align, None),
                ("", left_align, None),
                (2, right_align, "0"),
                (rsso, left_align, None),
                (pso, left_align, None),
                (mun_bgy_gc, left_align, "@"),
                (mun_ea_gc, left_align, "@"),
                ("", right_align, None),
                ("", right_align, None),
                ("", right_align, None),
            ]
            for c_idx, (val, align, num_fmt) in enumerate(m_row_data, start=1):
                cell = ws.cell(row=current_row, column=c_idx, value=val)
                cell.font = font_data
                cell.border = thin_border
                cell.alignment = align
                if num_fmt:
                    cell.number_format = num_fmt

            ws.row_dimensions[current_row].height = 20
            current_fid += 1
            current_row += 1

            for b_code in sorted(brgys_map.keys()):
                b_recs = brgys_map[b_code]
                b_info = brgy_lookup.get((p_c, m_c, b_code), ("", ""))
                b_name_raw = b_info[0] or b_info[1] or b_recs[0].get("geographic_name") or b_recs[0].get("barangay") or b_recs[0].get("areaname") or f"BARANGAY {b_code}"
                if " - EA" in b_name_raw:
                    b_name_raw = b_name_raw.split(" - EA")[0].strip()
                brgy_areaname = b_name_raw.upper()

                brgy_bgy_gc = f"{p_c.zfill(3)}{m_c.zfill(2)}{b_code.zfill(3)}"
                brgy_ea_gc = f"{brgy_bgy_gc}000000"
                b_row_data = [
                    (current_fid, right_align, "0"),
                    (r_c, left_align, "@"),
                    (p_c, left_align, "@"),
                    (m_c, left_align, "@"),
                    (b_code.zfill(3), left_align, "@"),
                    ("000000", left_align, "@"),
                    ("", right_align, None),
                    (brgy_areaname, left_align, None),
                    ("", left_align, None),
                    ("", left_align, None),
                    (2, right_align, "0"),
                    (rsso, left_align, None),
                    (pso, left_align, None),
                    (brgy_bgy_gc, left_align, "@"),
                    (brgy_ea_gc, left_align, "@"),
                    ("", right_align, None),
                    ("", right_align, None),
                    ("", right_align, None),
                ]
                for c_idx, (val, align, num_fmt) in enumerate(b_row_data, start=1):
                    cell = ws.cell(row=current_row, column=c_idx, value=val)
                    cell.font = font_data
                    cell.border = thin_border
                    cell.alignment = align
                    if num_fmt:
                        cell.number_format = num_fmt

                ws.row_dimensions[current_row].height = 20
                current_fid += 1
                current_row += 1

                for r in b_recs:
                    # Sync exact ean, bgy_geocode, and ea_geocode with output GPKG
                    bgy_gc = str(r.get("bgy_geocode") or "").strip()
                    if not bgy_gc or len(bgy_gc) != 8:
                        bgy_gc = f"{p_c.zfill(3)}{m_c.zfill(2)}{b_code.zfill(3)}"

                    recoded_ean = to_whole_number_str(r.get("recoded_ean") or "").strip()
                    if recoded_ean.isdigit() and len(recoded_ean) < 6:
                        recoded_ean = f"{int(recoded_ean):03d}000" if len(recoded_ean) <= 3 else f"{int(recoded_ean):06d}"

                    fallback_ean = to_whole_number_str(r.get("new_enumeration_area_code") or r.get("new_ean") or r.get("ean") or "000000").strip()
                    if fallback_ean.isdigit() and len(fallback_ean) < 6:
                        fallback_ean = f"{int(fallback_ean):03d}000" if len(fallback_ean) <= 3 else f"{int(fallback_ean):06d}"

                    gpkg_ean = recoded_ean or fallback_ean

                    ea_gc = str(r.get("ea_geocode") or "").strip()
                    if not ea_gc or len(ea_gc) != 14:
                        ea_gc = f"{bgy_gc}{gpkg_ean}" if (bgy_gc and gpkg_ean) else str(r.get("geocode") or "")

                    ea_areaname = f"{brgy_areaname} - EA {gpkg_ean}"
                    ea_remarks = str(r.get("remarks") or r.get("earf_remarks") or r.get("popcen_remarks") or "").strip()

                    ea_row_data = [
                        (current_fid, right_align, "0"),
                        (r_c, left_align, "@"),
                        (p_c, left_align, "@"),
                        (m_c, left_align, "@"),
                        (b_code.zfill(3), left_align, "@"),
                        (gpkg_ean, left_align, "@"),
                        (1, right_align, "0"),
                        (ea_areaname, left_align, None),
                        ("", left_align, None),
                        ("", left_align, None),
                        (2, right_align, "0"),
                        (rsso, left_align, None),
                        (pso, left_align, None),
                        (bgy_gc, left_align, "@"),
                        (ea_gc, left_align, "@"),
                        ("", right_align, None),
                        ("", right_align, None),
                        ("", right_align, None),
                    ]
                    for c_idx, (val, align, num_fmt) in enumerate(ea_row_data, start=1):
                        cell = ws.cell(row=current_row, column=c_idx, value=val)
                        cell.font = font_data
                        cell.border = thin_border
                        cell.alignment = align
                        if num_fmt:
                            cell.number_format = num_fmt

                    ws.row_dimensions[current_row].height = 20
                    current_fid += 1
                    current_row += 1

    col_w_map = {
        "A": 8, "B": 8, "C": 8, "D": 8, "E": 8, "F": 10,
        "G": 14, "H": 36, "I": 55, "J": 28, "K": 18, "L": 45,
        "M": 20, "N": 16, "O": 20, "P": 18, "Q": 14, "R": 16
    }
    for col_letter, width in col_w_map.items():
        ws.column_dimensions[col_letter].width = width

    ws.freeze_panes = "A2"
    ws.views.sheetView[0].showGridLines = True
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    wb.save(out_path)


def export_output_summary_to_excel(
    records: List[Dict[str, Any]],
    out_path: str,
    metadata: Optional[Dict[str, Any]] = None,
):
    """
    Generates the official Output Summary Table workbook (.xlsx) with:
      - Sheet 1: 'Output Summary'
        Hierarchical rollups by Barangay and Municipality comparing 2024 Base EAs vs.
        2026 Final EAs, breakdowns by classification (Retained, Split, Merged, Delineated),
        Household / Building workloads, and total flagged validation discrepancies.
      - Sheet 2: 'Validation Flags & Exceptions'
        Complete itemized audit register of all flagged records across the 5 rules
        (FLAG-01 to FLAG-05) with details and recommended corrective actions.
    """
    import openpyxl
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    from openpyxl.utils import get_column_letter

    pure_records = [r for r in records if not is_summary_header_record(r)]
    if not pure_records:
        pure_records = records

    # Ensure recoded_ean and validation flags are computed
    if any(not r.get("recoded_ean") for r in pure_records):
        pure_records = compute_recoded_enumeration_areas(pure_records)
    if any(r.get("validation_flags") is None for r in pure_records):
        pure_records = audit_matched_flags(pure_records)

    wb = openpyxl.Workbook()

    # Styling Palette
    fill_title = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid")
    fill_admin_grp = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")
    fill_ea_grp = PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid")
    fill_workload_grp = PatternFill(start_color="D9E1F2", end_color="D9E1F2", fill_type="solid")
    fill_flags_grp = PatternFill(start_color="FEF3C7", end_color="FEF3C7", fill_type="solid")
    fill_mun_total = PatternFill(start_color="E2E8F0", end_color="E2E8F0", fill_type="solid")
    fill_flag_cell = PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid")

    font_title = Font(name="Calibri", size=13, bold=True, color="FFFFFF")
    font_sub = Font(name="Calibri", size=10, bold=False, color="475569")
    font_grp = Font(name="Calibri", size=10, bold=True, color="0F172A")
    font_hdr = Font(name="Calibri", size=9, bold=True, color="000000")
    font_bold = Font(name="Calibri", size=10, bold=True, color="000000")
    font_data = Font(name="Calibri", size=10, color="000000")
    font_flag_bold = Font(name="Calibri", size=10, bold=True, color="991B1B")

    thin_border = Border(
        left=Side(style='thin', color='D9D9D9'),
        right=Side(style='thin', color='D9D9D9'),
        top=Side(style='thin', color='D9D9D9'),
        bottom=Side(style='thin', color='D9D9D9')
    )
    total_border = Border(
        left=Side(style='thin', color='94A3B8'),
        right=Side(style='thin', color='94A3B8'),
        top=Side(style='thin', color='000000'),
        bottom=Side(style='double', color='000000')
    )

    center_align = Alignment(horizontal="center", vertical="center")
    wrap_center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    left_align = Alignment(horizontal="left", vertical="center")
    right_align = Alignment(horizontal="right", vertical="center")

    # ----------------------------------------------------
    # SHEET 1: Output Summary Table
    # ----------------------------------------------------
    ws1 = wb.active
    ws1.title = "Output Summary"

    # Hierarchy Metadata Resolution
    resolved_prov_code = ""
    resolved_mun_code = ""
    resolved_prov_name = ""
    resolved_mun_name = ""
    if metadata and isinstance(metadata, dict):
        resolved_prov_code = str(metadata.get("province_code") or "").strip().zfill(3)
        resolved_mun_code = str(metadata.get("city_mun_code") or "").strip().zfill(2)
        resolved_prov_name = str(metadata.get("province") or "").strip().upper()
        resolved_mun_name = str(metadata.get("city_mun") or "").strip().upper()

    if pure_records and (not resolved_prov_name or not resolved_mun_name):
        f_gc = str(pure_records[0].get("geocode") or "")
        if not resolved_prov_code and len(f_gc) >= 3:
            resolved_prov_code = f_gc[:3]
        if not resolved_mun_code and len(f_gc) >= 5:
            resolved_mun_code = f_gc[3:5]
        if not resolved_prov_name or not resolved_mun_name:
            try:
                db_inst = POPCENDatabase()
                h_info = db_inst.get_citymun_hierarchy(f_gc[:5])
                resolved_prov_name = resolved_prov_name or str(h_info.get("province") or "PROVINCE").strip().upper()
                resolved_mun_name = resolved_mun_name or str(h_info.get("city_mun") or "MUNICIPALITY").strip().upper()
            except Exception:
                pass

    if not resolved_prov_name:
        resolved_prov_name = "PROVINCE"
    if not resolved_mun_name:
        resolved_mun_name = "MUNICIPALITY"

    # Row 1: Title Banner
    ws1.merge_cells("A1:T1")
    ws1["A1"] = "2026 CENSUS CARTOGRAPHY & DELINEATION — MUNICIPAL OUTPUT SUMMARY TABLE"
    ws1["A1"].font = font_title
    ws1["A1"].fill = fill_title
    ws1["A1"].alignment = center_align
    ws1.row_dimensions[1].height = 26

    # Row 2: Subtitle Metadata
    ws1.merge_cells("A2:T2")
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    ws1["A2"] = f"Province: {resolved_prov_name} ({resolved_prov_code}) | Municipality: {resolved_mun_name} ({resolved_mun_code}) | Generated: {now_str}"
    ws1["A2"].font = font_sub
    ws1["A2"].alignment = left_align
    ws1.row_dimensions[2].height = 18

    # Row 3: Group Headers
    ws1.merge_cells("A3:C3")
    ws1["A3"] = "Administrative Hierarchy"
    ws1["A3"].font = font_grp
    ws1["A3"].alignment = center_align
    for c in range(1, 4):
        ws1.cell(3, c).fill = fill_admin_grp
        ws1.cell(3, c).border = thin_border

    ws1.merge_cells("D3:I3")
    ws1["D3"] = "Enumeration Area Counts & Classifications"
    ws1["D3"].font = font_grp
    ws1["D3"].alignment = center_align
    for c in range(4, 10):
        ws1.cell(3, c).fill = fill_ea_grp
        ws1.cell(3, c).border = thin_border

    ws1.merge_cells("J3:N3")
    ws1["J3"] = "Demographic & Structural Workload"
    ws1["J3"].font = font_grp
    ws1["J3"].alignment = center_align
    for c in range(10, 15):
        ws1.cell(3, c).fill = fill_workload_grp
        ws1.cell(3, c).border = thin_border

    ws1.merge_cells("O3:T3")
    ws1["O3"] = "Validation Flags & Quality Audit"
    ws1["O3"].font = font_grp
    ws1["O3"].alignment = center_align
    for c in range(15, 21):
        ws1.cell(3, c).fill = fill_flags_grp
        ws1.cell(3, c).border = thin_border
    ws1.row_dimensions[3].height = 22

    # Row 4: Column Headers
    headers_s1 = [
        ("PSGC Code", center_align, fill_admin_grp),
        ("Brgy Code", center_align, fill_admin_grp),
        ("Barangay Name", left_align, fill_admin_grp),
        ("2024 Base EAs", wrap_center, fill_ea_grp),
        ("Retained EAs", wrap_center, fill_ea_grp),
        ("Split EAs", wrap_center, fill_ea_grp),
        ("Merged EAs", wrap_center, fill_ea_grp),
        ("Delineated EAs", wrap_center, fill_ea_grp),
        ("2026 Final EAs", wrap_center, fill_ea_grp),
        ("2024 Households", wrap_center, fill_workload_grp),
        ("2026 Households", wrap_center, fill_workload_grp),
        ("Household Diff", wrap_center, fill_workload_grp),
        ("2024 Buildings", wrap_center, fill_workload_grp),
        ("2026 Buildings", wrap_center, fill_workload_grp),
        ("Duplicate New EA (FLAG-01)", wrap_center, fill_flags_grp),
        ("Unprocessed Delin/Merg (FLAG-02)", wrap_center, fill_flags_grp),
        ("SY Mismatch (FLAG-03)", wrap_center, fill_flags_grp),
        ("Null EA Count (FLAG-04)", wrap_center, fill_flags_grp),
        ("Duplicate Geocode (FLAG-05)", wrap_center, fill_flags_grp),
        ("Total Flags", wrap_center, fill_flags_grp),
    ]

    for col_idx, (name, align, fill_bg) in enumerate(headers_s1, start=1):
        cell = ws1.cell(row=4, column=col_idx, value=name)
        cell.font = font_hdr
        cell.alignment = align
        cell.fill = fill_bg
        cell.border = thin_border
    ws1.row_dimensions[4].height = 34

    # Group Records by Barangay
    brgys_map = collections.defaultdict(list)
    for r in pure_records:
        gc = str(r.get("geocode") or "")
        b_code = str(r.get("barangay_code") or "").strip().zfill(3)
        if not b_code or b_code == "000":
            if len(gc) >= 8:
                b_code = gc[5:8]
            else:
                b_code = "001"
        brgys_map[b_code].append(r)

    # Compute Statistics per Barangay
    brgy_stats = []
    tot_base_eas = 0
    tot_retained = 0
    tot_split = 0
    tot_merged = 0
    tot_delin = 0
    tot_final_eas = 0
    tot_hh_2024 = 0
    tot_hh_2026 = 0
    tot_bldg_2024 = 0
    tot_bldg_2026 = 0
    tot_f1 = 0
    tot_f2 = 0
    tot_f3 = 0
    tot_f4 = 0
    tot_f5 = 0
    tot_flags = 0

    flagged_records_for_s2 = []

    for b_code in sorted(brgys_map.keys()):
        b_recs = brgys_map[b_code]
        first_r = b_recs[0]
        b_name = str(first_r.get("geographic_name") or first_r.get("areaname") or f"Barangay {b_code}").strip().upper()
        if " - EA" in b_name:
            b_name = b_name.split(" - EA")[0].strip()

        psgc_brgy = f"{resolved_prov_code}{resolved_mun_code}{b_code}000000"

        b_base_eas = 0
        b_retained = 0
        b_split = 0
        b_merged = 0
        b_delin = 0
        b_final_eas = 0
        b_hh_2024 = 0
        b_hh_2026 = 0
        b_bldg_2024 = 0
        b_bldg_2026 = 0
        b_f1 = 0
        b_f2 = 0
        b_f3 = 0
        b_f4 = 0
        b_f5 = 0
        b_flags = 0

        for r in b_recs:
            ea_t = str(r.get("ea_type") or r.get("ea_lifecycle") or "").strip().upper()
            ea_life = str(r.get("ea_lifecycle") or "").strip().upper()
            in_pop = r.get("in_popcen") in (1, "1", True)
            if in_pop:
                b_base_eas += 1

            if "RETAIN" in ea_t or (not ea_t and "RETAIN" in ea_life):
                b_retained += 1
                b_final_eas += 1
            elif "SPLIT" in ea_t or "SPLIT" in ea_life:
                b_split += 1
                b_final_eas += 1
            elif "MERG" in ea_t or "MERG" in ea_life:
                b_merged += 1
            elif "DELIN" in ea_t or "RESET" in ea_t or "DELIN" in ea_life or "RESET" in ea_life:
                b_delin += 1
                b_final_eas += 1
            else:
                b_retained += 1
                b_final_eas += 1

            # Workload
            try:
                p_hh = r.get("no_of_households") or r.get("popcen_households") or 0
                b_hh_2024 += int(p_hh)
            except Exception:
                pass
            try:
                e_hh = r.get("household_count") or r.get("earf_household_count") or r.get("hhcount") or 0
                b_hh_2026 += int(e_hh)
            except Exception:
                pass
            try:
                p_bldg = r.get("number_of_buildings") or r.get("building_count") or 0
                b_bldg_2024 += int(p_bldg)
            except Exception:
                pass
            try:
                e_bldg = r.get("building_count") or r.get("earf_building_count") or r.get("bldgcount") or 0
                b_bldg_2026 += int(e_bldg)
            except Exception:
                pass

            # Flags
            v_flags = str(r.get("validation_flags") or "").strip()
            if v_flags:
                b_flags += 1
                flagged_records_for_s2.append((r, b_name, b_code))
                if "FLAG-01" in v_flags:
                    b_f1 += 1
                if "FLAG-02" in v_flags:
                    b_f2 += 1
                if "FLAG-03" in v_flags:
                    b_f3 += 1
                if "FLAG-04" in v_flags:
                    b_f4 += 1
                if "FLAG-05" in v_flags:
                    b_f5 += 1

        b_hh_diff = b_hh_2026 - b_hh_2024
        brgy_stats.append({
            "psgc": psgc_brgy,
            "b_code": b_code,
            "b_name": b_name,
            "base_eas": b_base_eas or len(b_recs),
            "retained": b_retained,
            "split": b_split,
            "merged": b_merged,
            "delin": b_delin,
            "final_eas": b_final_eas or len(b_recs),
            "hh_2024": b_hh_2024,
            "hh_2026": b_hh_2026,
            "hh_diff": b_hh_diff,
            "bldg_2024": b_bldg_2024,
            "bldg_2026": b_bldg_2026,
            "f1": b_f1,
            "f2": b_f2,
            "f3": b_f3,
            "f4": b_f4,
            "f5": b_f5,
            "flags": b_flags,
        })

        tot_base_eas += (b_base_eas or len(b_recs))
        tot_retained += b_retained
        tot_split += b_split
        tot_merged += b_merged
        tot_delin += b_delin
        tot_final_eas += (b_final_eas or len(b_recs))
        tot_hh_2024 += b_hh_2024
        tot_hh_2026 += b_hh_2026
        tot_bldg_2024 += b_bldg_2024
        tot_bldg_2026 += b_bldg_2026
        tot_f1 += b_f1
        tot_f2 += b_f2
        tot_f3 += b_f3
        tot_f4 += b_f4
        tot_f5 += b_f5
        tot_flags += b_flags

    # Row 5: Municipality Summary Total Row
    mun_psgc = f"{resolved_prov_code}{resolved_mun_code}000000000"
    tot_diff = tot_hh_2026 - tot_hh_2024
    row_5_vals = [
        (mun_psgc, center_align, "@"),
        ("000", center_align, "@"),
        (f"MUNICIPAL TOTAL: {resolved_mun_name}", left_align, None),
        (tot_base_eas, right_align, "#,##0"),
        (tot_retained, right_align, "#,##0"),
        (tot_split, right_align, "#,##0"),
        (tot_merged, right_align, "#,##0"),
        (tot_delin, right_align, "#,##0"),
        (tot_final_eas, right_align, "#,##0"),
        (tot_hh_2024, right_align, "#,##0"),
        (tot_hh_2026, right_align, "#,##0"),
        (tot_diff, right_align, "#,##0"),
        (tot_bldg_2024, right_align, "#,##0"),
        (tot_bldg_2026, right_align, "#,##0"),
        (tot_f1, right_align, "#,##0"),
        (tot_f2, right_align, "#,##0"),
        (tot_f3, right_align, "#,##0"),
        (tot_f4, right_align, "#,##0"),
        (tot_f5, right_align, "#,##0"),
        (tot_flags, right_align, "#,##0"),
    ]

    for col_idx, (val, align, num_fmt) in enumerate(row_5_vals, start=1):
        cell = ws1.cell(row=5, column=col_idx, value=val)
        cell.font = font_bold
        cell.fill = fill_mun_total
        cell.border = total_border
        cell.alignment = align
        if num_fmt:
            cell.number_format = num_fmt
    ws1.row_dimensions[5].height = 24

    # Rows 6+: Barangay Data Rows
    current_row = 6
    for st in brgy_stats:
        b_vals = [
            (st["psgc"], center_align, "@"),
            (st["b_code"], center_align, "@"),
            (st["b_name"], left_align, None),
            (st["base_eas"], right_align, "#,##0"),
            (st["retained"], right_align, "#,##0"),
            (st["split"], right_align, "#,##0"),
            (st["merged"], right_align, "#,##0"),
            (st["delin"], right_align, "#,##0"),
            (st["final_eas"], right_align, "#,##0"),
            (st["hh_2024"], right_align, "#,##0"),
            (st["hh_2026"], right_align, "#,##0"),
            (st["hh_diff"], right_align, "#,##0"),
            (st["bldg_2024"], right_align, "#,##0"),
            (st["bldg_2026"], right_align, "#,##0"),
            (st["f1"], right_align, "#,##0"),
            (st["f2"], right_align, "#,##0"),
            (st["f3"], right_align, "#,##0"),
            (st["f4"], right_align, "#,##0"),
            (st["f5"], right_align, "#,##0"),
            (st["flags"], right_align, "#,##0"),
        ]
        has_brgy_flags = st["flags"] > 0
        for col_idx, (val, align, num_fmt) in enumerate(b_vals, start=1):
            cell = ws1.cell(row=current_row, column=col_idx, value=val)
            cell.font = font_data
            cell.border = thin_border
            cell.alignment = align
            if num_fmt:
                cell.number_format = num_fmt
            if col_idx in (15, 16, 17, 18, 19, 20) and isinstance(val, int) and val > 0:
                cell.fill = fill_flag_cell
                cell.font = font_flag_bold
        ws1.row_dimensions[current_row].height = 20
        current_row += 1

    # Sheet 1 Column Widths
    col_w_s1 = {
        "A": 16, "B": 10, "C": 28, "D": 13, "E": 13, "F": 11,
        "G": 11, "H": 13, "I": 13, "J": 14, "K": 14, "L": 14,
        "M": 14, "N": 14, "O": 16, "P": 18, "Q": 16, "R": 14,
        "S": 16, "T": 14
    }
    for col_letter, width in col_w_s1.items():
        ws1.column_dimensions[col_letter].width = width
    ws1.freeze_panes = "D6"
    ws1.views.sheetView[0].showGridLines = True

    # ----------------------------------------------------
    # SHEET 2: Validation Flags & Exceptions Log
    # ----------------------------------------------------
    ws2 = wb.create_sheet(title="Validation Flags & Exceptions")

    # Title Banner
    ws2.merge_cells("A1:M1")
    ws2["A1"] = f"VALIDATION FLAGS & EXCEPTIONS AUDIT REGISTER — {resolved_mun_name} ({len(flagged_records_for_s2):,} Flags Detected)"
    ws2["A1"].font = font_title
    ws2["A1"].fill = fill_title
    ws2["A1"].alignment = center_align
    ws2.row_dimensions[1].height = 26

    # Column Headers
    headers_s2 = [
        ("#", center_align),
        ("Geocode", center_align),
        ("Brgy Code", center_align),
        ("Barangay Name", left_align),
        ("EA Name", left_align),
        ("GPKG EA Type", center_align),
        ("Source Year", center_align),
        ("New EA Code", center_align),
        ("EA Count", right_align),
        ("Flag Codes", center_align),
        ("Flag Details & Notes", left_align),
        ("Remarks", left_align),
        ("Recommended Action", left_align),
    ]

    for col_idx, (name, align) in enumerate(headers_s2, start=1):
        cell = ws2.cell(row=2, column=col_idx, value=name)
        cell.font = font_hdr
        cell.alignment = align
        cell.fill = fill_flags_grp
        cell.border = thin_border
    ws2.row_dimensions[2].height = 28

    if not flagged_records_for_s2:
        ws2.merge_cells("A3:M3")
        cell = ws2.cell(row=3, column=1, value="Clean Quality Audit: Zero validation flags or discrepancies detected. 100% of records conform to PSA standards.")
        cell.font = font_bold
        cell.alignment = center_align
        ws2.row_dimensions[3].height = 24
    else:
        s2_row = 3
        for idx, (r, b_name, b_code) in enumerate(flagged_records_for_s2, start=1):
            gc = str(r.get("geocode") or "")
            ean = str(r.get("ean") or "").strip()
            ea_name = str(r.get("name") or (f"EA {ean}" if ean else "")).strip()
            ea_t = str(r.get("ea_type") or r.get("ea_lifecycle") or "").strip().upper()
            sy = str(r.get("source_year") or r.get("sy") or "").strip().replace(".0", "")
            nean = str(r.get("new_enumeration_area_code") or r.get("new_ean") or "").strip().replace(".0", "")
            eacnt = str(r.get("no_of_eas") or r.get("eacount") or "").strip().replace(".0", "")
            flags_str = str(r.get("validation_flags") or "").strip()
            notes_str = str(r.get("flag_notes") or "").strip()
            rem = str(r.get("remarks") or r.get("earf_remarks") or r.get("popcen_remarks") or "").strip()

            actions = []
            if "FLAG-01" in flags_str:
                actions.append("Renumber conflicting new EA codes in Preliminary EARF to ensure uniqueness.")
            if "FLAG-02" in flags_str:
                actions.append("Provide formal justification in Remarks explaining why the candidate EA was retained.")
            if "FLAG-03" in flags_str:
                actions.append("Correct Source Year: Set SY to 2024 for Retained EAs, or 2026 for Split/Merged/Delineated EAs.")
            if "FLAG-04" in flags_str:
                actions.append("Populate EA Count (must be non-zero integer >= 1).")
            if "FLAG-05" in flags_str:
                actions.append("Deduplicate geocode features or adjust 6-digit EAN suffix.")
            rec_action = "; ".join(actions) if actions else "Review record attributes."

            row_data = [
                (idx, center_align, None),
                (gc, center_align, "@"),
                (b_code, center_align, "@"),
                (b_name, left_align, None),
                (ea_name, left_align, None),
                (ea_t, center_align, None),
                (sy, center_align, "@"),
                (nean, center_align, "@"),
                (int(eacnt) if eacnt.isdigit() else eacnt, right_align, "#,##0" if eacnt.isdigit() else None),
                (flags_str, center_align, None),
                (notes_str, left_align, None),
                (rem, left_align, None),
                (rec_action, left_align, None),
            ]

            for col_idx, (val, align, num_fmt) in enumerate(row_data, start=1):
                cell = ws2.cell(row=s2_row, column=col_idx, value=val)
                cell.font = font_data
                cell.border = thin_border
                cell.alignment = align
                if num_fmt:
                    cell.number_format = num_fmt
                if col_idx == 10:
                    cell.fill = fill_flag_cell
                    cell.font = font_flag_bold
            ws2.row_dimensions[s2_row].height = 20
            s2_row += 1

    col_w_s2 = {
        "A": 6, "B": 18, "C": 10, "D": 24, "E": 14, "F": 14,
        "G": 12, "H": 14, "I": 10, "J": 16, "K": 40, "L": 26, "M": 45
    }
    for col_letter, width in col_w_s2.items():
        ws2.column_dimensions[col_letter].width = width
    ws2.freeze_panes = "C3"
    ws2.views.sheetView[0].showGridLines = True

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    wb.save(out_path)

    return {
        "status": "success",
        "output_path": out_path,
        "total_rows": len(records),
        "total_flagged": len(flagged_records_for_s2),
        "total_barangays": len(brgys_map),
    }



class POPCENDatabase:
    """
    Manages dedicated SQLite databases for:
      - 2024 POPCEN records & master match results (popcen_database.db)
      - Preliminary EARF consolidated records (earf_database.db)
      - EA GeoPackage consolidated records (gpkg_database.db)
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        popcen_db_path: Optional[str] = None,
        earf_db_path: Optional[str] = None,
        gpkg_db_path: Optional[str] = None,
    ):
        base_popcen = db_path or popcen_db_path
        self.popcen_db_path = base_popcen or get_default_db_path()
        self.db_path = self.popcen_db_path  # legacy alias
        self.earf_db_path = earf_db_path or get_default_earf_db_path()
        self.gpkg_db_path = gpkg_db_path or get_default_gpkg_db_path()
        self.init_db()

    @staticmethod
    def _configure_fast_pragmas(conn: sqlite3.Connection):
        """Applies high-performance SQLite PRAGMAs (WAL mode, cache size, temp_store memory)."""
        try:
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode = WAL;")
            cur.execute("PRAGMA synchronous = NORMAL;")
            cur.execute("PRAGMA cache_size = -64000;")
            cur.execute("PRAGMA temp_store = MEMORY;")
            cur.execute("PRAGMA mmap_size = 268435456;")
        except Exception:
            pass

    def _get_connection(self) -> sqlite3.Connection:
        """Returns connection to 2024 POPCEN & master match database (popcen_database.db)."""
        conn = sqlite3.connect(self.popcen_db_path)
        conn.row_factory = sqlite3.Row
        self._configure_fast_pragmas(conn)
        return conn

    def _get_earf_connection(self) -> sqlite3.Connection:
        """Returns connection to Preliminary EARF database (earf_database.db)."""
        conn = sqlite3.connect(self.earf_db_path)
        conn.row_factory = sqlite3.Row
        self._configure_fast_pragmas(conn)
        return conn

    def _get_gpkg_connection(self) -> sqlite3.Connection:
        """Returns connection to EA GeoPackage database (gpkg_database.db)."""
        conn = sqlite3.connect(self.gpkg_db_path)
        conn.row_factory = sqlite3.Row
        self._configure_fast_pragmas(conn)
        return conn

    def init_db(self):
        """Initializes tables and indexes across all dedicated databases."""
        self.init_popcen_db()
        self.init_earf_db()
        self.init_gpkg_db()
        self._migrate_legacy_tables_if_needed()

    def init_popcen_db(self):
        """Initializes popcen_records and matched_records in popcen_database.db."""
        with self._get_connection() as conn:
            cur = conn.cursor()

            # 1. POPCEN Table
            cur.execute("""
            CREATE TABLE IF NOT EXISTS popcen_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                geocode TEXT,
                geographic_name TEXT,
                geographic_level TEXT,
                region_code TEXT,
                province_code TEXT,
                city_mun_code TEXT,
                barangay_code TEXT,
                ean TEXT,
                no_of_eas TEXT,
                areaname TEXT,
                household_population TEXT,
                no_of_households TEXT,
                ea_subject_for_delineation TEXT,
                ea_subject_for_merging TEXT,
                ea_with_gap_overlap TEXT,
                estimated_number_of_households TEXT,
                remarks TEXT,
                match_status TEXT DEFAULT 'Pending Match',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """)

            cur.execute("CREATE INDEX IF NOT EXISTS idx_popcen_gc ON popcen_records(geocode);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_popcen_lvl ON popcen_records(geographic_level);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_popcen_prov ON popcen_records(province_code);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_popcen_mun ON popcen_records(province_code, city_mun_code);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_popcen_brgy ON popcen_records(province_code, city_mun_code, barangay_code);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_popcen_delin ON popcen_records(ea_subject_for_delineation);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_popcen_merge ON popcen_records(ea_subject_for_merging);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_popcen_gap ON popcen_records(ea_with_gap_overlap);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_popcen_match ON popcen_records(match_status);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_popcen_name ON popcen_records(geographic_name);")

            # 2. Reconciled Matched Records Table (Cross-matching of 3 sources)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS matched_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                geocode TEXT,
                geographic_name TEXT DEFAULT '',
                geographic_level TEXT DEFAULT '',
                region_code TEXT DEFAULT '',
                province_code TEXT DEFAULT '',
                city_mun_code TEXT DEFAULT '',
                barangay_code TEXT DEFAULT '',
                ean TEXT DEFAULT '',
                no_of_eas TEXT DEFAULT '',
                areaname TEXT DEFAULT '',
                household_population TEXT DEFAULT '',
                no_of_households TEXT DEFAULT '',
                ea_subject_for_delineation TEXT DEFAULT '',
                ea_subject_for_merging TEXT DEFAULT '',
                popcen_remarks TEXT DEFAULT '',
                new_enumeration_area_code TEXT DEFAULT '',
                household_count TEXT DEFAULT '',
                building_count TEXT DEFAULT '',
                ea_type TEXT DEFAULT '',
                source_year TEXT DEFAULT '',
                earf_remarks TEXT DEFAULT '',
                recoded_ean TEXT DEFAULT '',
                match_status TEXT DEFAULT 'Pending Match',
                in_popcen INTEGER DEFAULT 0,
                in_earf INTEGER DEFAULT 0,
                in_gpkg INTEGER DEFAULT 0,
                popcen_households TEXT DEFAULT '',
                earf_households TEXT DEFAULT '',
                household_diff TEXT DEFAULT '',
                earf_household_count TEXT DEFAULT '',
                earf_building_count TEXT DEFAULT '',
                popcen_delineation TEXT DEFAULT '',
                popcen_merging TEXT DEFAULT '',
                popcen_gap_overlap TEXT DEFAULT '',
                new_ean TEXT DEFAULT '',
                is_reset INTEGER DEFAULT 0,
                ea_lifecycle TEXT DEFAULT 'RETAINED',
                remarks TEXT DEFAULT '',
                gpkg_layers TEXT DEFAULT '',
                has_geometry INTEGER DEFAULT 0,
                geom_type TEXT DEFAULT '',
                gpkg_hh_count TEXT DEFAULT '',
                gpkg_bldg_count TEXT DEFAULT '',
                validation_flags TEXT DEFAULT '',
                flag_notes TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """)

            # Check if existing matched_records table has a UNIQUE constraint on geocode and migrate it
            cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='matched_records'")
            tbl_sql_row = cur.fetchone()
            if tbl_sql_row and tbl_sql_row[0] and ("geocode text unique" in tbl_sql_row[0].lower() or "unique(geocode)" in tbl_sql_row[0].lower().replace(" ", "")):
                cur.execute("ALTER TABLE matched_records RENAME TO matched_records_legacy;")
                cur.execute("""
                CREATE TABLE matched_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    geocode TEXT,
                    geographic_name TEXT DEFAULT '',
                    geographic_level TEXT DEFAULT '',
                    region_code TEXT DEFAULT '',
                    province_code TEXT DEFAULT '',
                    city_mun_code TEXT DEFAULT '',
                    barangay_code TEXT DEFAULT '',
                    ean TEXT DEFAULT '',
                    no_of_eas TEXT DEFAULT '',
                    areaname TEXT DEFAULT '',
                    household_population TEXT DEFAULT '',
                    no_of_households TEXT DEFAULT '',
                    ea_subject_for_delineation TEXT DEFAULT '',
                    ea_subject_for_merging TEXT DEFAULT '',
                    popcen_remarks TEXT DEFAULT '',
                    new_enumeration_area_code TEXT DEFAULT '',
                    household_count TEXT DEFAULT '',
                    building_count TEXT DEFAULT '',
                    ea_type TEXT DEFAULT '',
                    source_year TEXT DEFAULT '',
                    earf_remarks TEXT DEFAULT '',
                    recoded_ean TEXT DEFAULT '',
                    match_status TEXT DEFAULT 'Pending Match',
                    in_popcen INTEGER DEFAULT 0,
                    in_earf INTEGER DEFAULT 0,
                    in_gpkg INTEGER DEFAULT 0,
                    popcen_households TEXT DEFAULT '',
                    earf_households TEXT DEFAULT '',
                    household_diff TEXT DEFAULT '',
                    earf_household_count TEXT DEFAULT '',
                    earf_building_count TEXT DEFAULT '',
                    popcen_delineation TEXT DEFAULT '',
                    popcen_merging TEXT DEFAULT '',
                    popcen_gap_overlap TEXT DEFAULT '',
                    new_ean TEXT DEFAULT '',
                    is_reset INTEGER DEFAULT 0,
                    ea_lifecycle TEXT DEFAULT 'RETAINED',
                    remarks TEXT DEFAULT '',
                    gpkg_layers TEXT DEFAULT '',
                    has_geometry INTEGER DEFAULT 0,
                    geom_type TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """)
                cur.execute("PRAGMA table_info(matched_records_legacy)")
                old_cols = [r[1] for r in cur.fetchall() if r[1] != 'id']
                cur.execute("PRAGMA table_info(matched_records)")
                new_table_cols = [r[1] for r in cur.fetchall() if r[1] != 'id']
                common_cols = [c for c in old_cols if c in new_table_cols]
                if common_cols:
                    cols_str = ", ".join(common_cols)
                    cur.execute(f"INSERT INTO matched_records ({cols_str}) SELECT {cols_str} FROM matched_records_legacy;")
                cur.execute("DROP TABLE matched_records_legacy;")
                conn.commit()

            cur.execute("PRAGMA table_info(matched_records)")
            existing_match_cols = {row[1] for row in cur.fetchall()}
            match_cols_to_add = [
                ("geographic_name", "TEXT DEFAULT ''"),
                ("geographic_level", "TEXT DEFAULT ''"),
                ("region_code", "TEXT DEFAULT ''"),
                ("province_code", "TEXT DEFAULT ''"),
                ("city_mun_code", "TEXT DEFAULT ''"),
                ("barangay_code", "TEXT DEFAULT ''"),
                ("no_of_eas", "TEXT DEFAULT ''"),
                ("household_population", "TEXT DEFAULT ''"),
                ("no_of_households", "TEXT DEFAULT ''"),
                ("ea_subject_for_delineation", "TEXT DEFAULT ''"),
                ("ea_subject_for_merging", "TEXT DEFAULT ''"),
                ("popcen_remarks", "TEXT DEFAULT ''"),
                ("new_enumeration_area_code", "TEXT DEFAULT ''"),
                ("household_count", "TEXT DEFAULT ''"),
                ("building_count", "TEXT DEFAULT ''"),
                ("source_year", "TEXT DEFAULT ''"),
                ("earf_remarks", "TEXT DEFAULT ''"),
                ("recoded_ean", "TEXT DEFAULT ''"),
                ("earf_household_count", "TEXT DEFAULT ''"),
                ("earf_building_count", "TEXT DEFAULT ''"),
                ("gpkg_hh_count", "TEXT DEFAULT ''"),
                ("gpkg_bldg_count", "TEXT DEFAULT ''"),
                ("validation_flags", "TEXT DEFAULT ''"),
                ("flag_notes", "TEXT DEFAULT ''"),
            ]
            for col_name, col_def in match_cols_to_add:
                if col_name not in existing_match_cols:
                    cur.execute(f"ALTER TABLE matched_records ADD COLUMN {col_name} {col_def}")

            cur.execute("CREATE INDEX IF NOT EXISTS idx_matched_gc ON matched_records(geocode);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_matched_status ON matched_records(match_status);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_matched_lifecycle ON matched_records(ea_lifecycle);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_matched_recoded ON matched_records(recoded_ean);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_matched_brgy ON matched_records(province_code, city_mun_code, barangay_code);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_matched_flags ON matched_records(validation_flags);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_matched_new_ean ON matched_records(new_enumeration_area_code);")

            # 3. Dashboard Monitoring Records Table (Persistent Deliverables Cache)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS dashboard_monitoring_records (
                city_mun_code TEXT PRIMARY KEY,
                region TEXT DEFAULT '',
                province TEXT DEFAULT '',
                city_mun TEXT DEFAULT '',
                pppmm TEXT DEFAULT '',
                status TEXT DEFAULT 'MISSING',
                gpkg_exists INTEGER DEFAULT 0,
                gpkg_filename TEXT DEFAULT '',
                gpkg_path TEXT DEFAULT '',
                gpkg_size INTEGER DEFAULT 0,
                gpkg_size_str TEXT DEFAULT '',
                gpkg_mtime REAL DEFAULT 0,
                gpkg_mtime_str TEXT DEFAULT '',
                gpkg_feature_count INTEGER DEFAULT 0,
                final_earf_exists INTEGER DEFAULT 0,
                final_earf_filename TEXT DEFAULT '',
                final_earf_path TEXT DEFAULT '',
                final_earf_size INTEGER DEFAULT 0,
                final_earf_size_str TEXT DEFAULT '',
                final_earf_mtime REAL DEFAULT 0,
                final_earf_mtime_str TEXT DEFAULT '',
                mis_earf_exists INTEGER DEFAULT 0,
                mis_earf_filename TEXT DEFAULT '',
                mis_earf_path TEXT DEFAULT '',
                mis_earf_size INTEGER DEFAULT 0,
                mis_earf_size_str TEXT DEFAULT '',
                mis_earf_mtime REAL DEFAULT 0,
                mis_earf_mtime_str TEXT DEFAULT '',
                subfolder_path TEXT DEFAULT '',
                subfolder_exists INTEGER DEFAULT 0,
                last_modified REAL DEFAULT 0,
                last_modified_str TEXT DEFAULT '',
                last_scanned TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_dash_reg ON dashboard_monitoring_records(region);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_dash_prv ON dashboard_monitoring_records(province);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_dash_stat ON dashboard_monitoring_records(status);")

            # 4. Dashboard Settings Table (Saved root directory, etc.)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS dashboard_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            """)

            # 5. Deliverables QA Results Table (Persistent QA Review Cache)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS deliverables_qa_results (
                city_mun_code TEXT PRIMARY KEY,
                overall_status TEXT,
                compliance_score REAL,
                total_rules INTEGER,
                passed_count INTEGER,
                warning_count INTEGER,
                failure_count INTEGER,
                feature_count INTEGER,
                discrepancies_count INTEGER,
                summary_json TEXT,
                evaluated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_qa_stat ON deliverables_qa_results(overall_status);")
            conn.commit()

    def init_earf_db(self):
        """Initializes earf_records in dedicated earf_database.db."""
        with self._get_earf_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
            CREATE TABLE IF NOT EXISTS earf_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                geocode TEXT,
                prov TEXT,
                mun TEXT,
                brgy TEXT,
                ea TEXT,
                number_of_eas TEXT,
                areaname TEXT,
                number_of_households TEXT,
                number_of_buildings TEXT,
                new_enumeration_area_code TEXT,
                household_count TEXT,
                building_count TEXT,
                ea_type TEXT,
                source_year TEXT,
                remarks TEXT,
                source_file TEXT,
                match_status TEXT DEFAULT 'Pending Match',
                consistency_status TEXT DEFAULT 'NOT_CHECKED',
                consistency_notes TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """)

            cur.execute("CREATE INDEX IF NOT EXISTS idx_earf_gc ON earf_records(geocode);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_earf_prov ON earf_records(prov);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_earf_mun ON earf_records(prov, mun);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_earf_brgy ON earf_records(prov, mun, brgy);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_earf_type ON earf_records(ea_type);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_earf_file ON earf_records(source_file);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_earf_name ON earf_records(areaname);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_earf_consistency ON earf_records(consistency_status);")
            cur.execute("""
            UPDATE earf_records 
            SET source_year = CASE 
                WHEN UPPER(TRIM(COALESCE(ea_type, ''))) LIKE '%RETAIN%' THEN '2024' 
                ELSE '2026' 
            END 
            WHERE source_year IS NULL 
               OR (UPPER(TRIM(COALESCE(ea_type, ''))) LIKE '%RETAIN%' AND source_year != '2024') 
               OR (UPPER(TRIM(COALESCE(ea_type, ''))) NOT LIKE '%RETAIN%' AND source_year != '2026');
            """)
            conn.commit()

    def update_earf_source_years(self) -> int:
        """Updates all earf_records: source_year = '2024' if Retained, else '2026'."""
        try:
            with self._get_earf_connection() as conn:
                cur = conn.cursor()
                cur.execute("""
                    UPDATE earf_records 
                    SET source_year = CASE 
                        WHEN UPPER(TRIM(COALESCE(ea_type, ''))) LIKE '%RETAIN%' THEN '2024' 
                        ELSE '2026' 
                    END 
                    WHERE source_year IS NULL 
                       OR (UPPER(TRIM(COALESCE(ea_type, ''))) LIKE '%RETAIN%' AND source_year != '2024') 
                       OR (UPPER(TRIM(COALESCE(ea_type, ''))) NOT LIKE '%RETAIN%' AND source_year != '2026')
                """)
                count = cur.rowcount
                conn.commit()
                return count
        except Exception:
            return 0

    def update_earf_retained_source_year(self) -> int:
        """Compatibility alias for update_earf_source_years."""
        return self.update_earf_source_years()

    def init_gpkg_db(self):
        """Initializes gpkg_records in dedicated gpkg_database.db."""
        with self._get_gpkg_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
            CREATE TABLE IF NOT EXISTS gpkg_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fid INTEGER,
                map_uuid TEXT,
                geocode TEXT,
                region TEXT,
                province TEXT,
                city_mun TEXT,
                barangay TEXT,
                ean TEXT,
                name TEXT,
                code TEXT,
                hhcount TEXT,
                bldgcount TEXT,
                sy TEXT,
                new_ean TEXT,
                hh_count TEXT,
                bldg_count TEXT,
                ea_type TEXT,
                eacount TEXT,
                remarks TEXT,
                layer_source TEXT,
                source_file TEXT,
                geom_status TEXT,
                match_status TEXT DEFAULT 'Pending Match',
                consistency_status TEXT DEFAULT 'UNIQUE',
                consistency_detail TEXT DEFAULT '',
                dup_count INTEGER DEFAULT 1,
                file_count INTEGER DEFAULT 1,
                source_files TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """)

            cur.execute("PRAGMA table_info(gpkg_records)")
            existing_gpkg_cols = {row[1] for row in cur.fetchall()}
            if "match_status" not in existing_gpkg_cols:
                cur.execute("ALTER TABLE gpkg_records ADD COLUMN match_status TEXT DEFAULT 'Pending Match'")
            if "fid" not in existing_gpkg_cols:
                cur.execute("ALTER TABLE gpkg_records ADD COLUMN fid INTEGER")
            if "map_uuid" not in existing_gpkg_cols:
                cur.execute("ALTER TABLE gpkg_records ADD COLUMN map_uuid TEXT")

            cur.execute("CREATE INDEX IF NOT EXISTS idx_gpkg_gc ON gpkg_records(geocode);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_gpkg_match_status ON gpkg_records(match_status);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_gpkg_prov ON gpkg_records(province);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_gpkg_mun ON gpkg_records(province, city_mun);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_gpkg_brgy ON gpkg_records(province, city_mun, barangay);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_gpkg_type ON gpkg_records(ea_type);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_gpkg_source ON gpkg_records(source_file);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_gpkg_layer ON gpkg_records(layer_source);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_gpkg_consistency ON gpkg_records(consistency_status);")

            # Sanitize float strings (e.g. '1004.0' -> '1004') for whole-number columns in existing database
            for col in ("code", "hhcount", "sy", "hh_count", "bldgcount", "bldg_count", "eacount"):
                if col in existing_gpkg_cols:
                    cur.execute(f"UPDATE gpkg_records SET {col} = SUBSTR({col}, 1, LENGTH({col}) - 2) WHERE {col} LIKE '%.0'")
                    cur.execute(f"UPDATE gpkg_records SET {col} = SUBSTR({col}, 1, LENGTH({col}) - 3) WHERE {col} LIKE '%.00'")
            conn.commit()

    def _migrate_legacy_tables_if_needed(self):
        """
        Migrates any legacy earf_records or gpkg_records tables from popcen_database.db
        into their dedicated databases (earf_database.db and gpkg_database.db).
        """
        if not os.path.exists(self.popcen_db_path):
            return

        try:
            with self._get_connection() as pop_conn:
                pop_cur = pop_conn.cursor()

                # 1. Migrate earf_records if present in popcen_database.db
                pop_cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='earf_records'")
                if pop_cur.fetchone():
                    pop_cur.execute("SELECT COUNT(*) FROM earf_records")
                    count = pop_cur.fetchone()[0]
                    if count > 0 and self.earf_record_count() == 0:
                        pop_cur.execute("SELECT * FROM earf_records")
                        rows = [dict(r) for r in pop_cur.fetchall()]
                        if rows:
                            cols = [k for k in rows[0].keys() if k != "id"]
                            placeholders = ", ".join(["?"] * len(cols))
                            col_str = ", ".join(cols)
                            with self._get_earf_connection() as earf_conn:
                                earf_cur = earf_conn.cursor()
                                for i in range(0, len(rows), 1000):
                                    chunk = [[r[c] for c in cols] for r in rows[i : i + 1000]]
                                    earf_cur.executemany(f"INSERT INTO earf_records ({col_str}) VALUES ({placeholders})", chunk)
                                earf_conn.commit()
                    pop_cur.execute("DROP TABLE IF EXISTS earf_records")
                    pop_conn.commit()

                # 2. Migrate gpkg_records if present in popcen_database.db
                pop_cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='gpkg_records'")
                if pop_cur.fetchone():
                    pop_cur.execute("SELECT COUNT(*) FROM gpkg_records")
                    count = pop_cur.fetchone()[0]
                    if count > 0 and self.gpkg_record_count() == 0:
                        pop_cur.execute("SELECT * FROM gpkg_records")
                        rows = [dict(r) for r in pop_cur.fetchall()]
                        if rows:
                            cols = [k for k in rows[0].keys() if k != "id"]
                            placeholders = ", ".join(["?"] * len(cols))
                            col_str = ", ".join(cols)
                            with self._get_gpkg_connection() as gpkg_conn:
                                gpkg_cur = gpkg_conn.cursor()
                                for i in range(0, len(rows), 1000):
                                    chunk = [[r[c] for c in cols] for r in rows[i : i + 1000]]
                                    gpkg_cur.executemany(f"INSERT INTO gpkg_records ({col_str}) VALUES ({placeholders})", chunk)
                                gpkg_conn.commit()
                    pop_cur.execute("DROP TABLE IF EXISTS gpkg_records")
                    pop_conn.commit()
        except Exception:
            pass
            cur.execute("CREATE INDEX IF NOT EXISTS idx_matched_reset ON matched_records(is_reset);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_matched_in_pop ON matched_records(in_popcen);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_matched_in_earf ON matched_records(in_earf);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_matched_in_gpkg ON matched_records(in_gpkg);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_matched_name ON matched_records(areaname);")

            conn.commit()

    # ----------------------------------------------------------------------
    # POPCEN Methods
    # ----------------------------------------------------------------------
    def record_count(self) -> int:
        """Returns the total number of records currently in popcen_records."""
        try:
            with self._get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM popcen_records")
                return cur.fetchone()[0]
        except Exception:
            return 0

    def is_populated(self) -> bool:
        """Returns True if popcen_records contains data."""
        return self.record_count() > 0

    def clear(self):
        """Clears all records from popcen_records."""
        conn = self._get_connection()
        try:
            conn.execute("DELETE FROM popcen_records")
            conn.commit()
            old_iso = conn.isolation_level
            conn.isolation_level = None
            conn.execute("VACUUM")
            conn.isolation_level = old_iso
        finally:
            conn.close()

    def delete_popcen_records_by_ids(self, ids: List[int]) -> int:
        """Deletes specific records from popcen_records by their primary key IDs."""
        if not ids:
            return 0
        total_deleted = 0
        with self._get_connection() as conn:
            cur = conn.cursor()
            for i in range(0, len(ids), 900):
                chunk = ids[i : i + 900]
                placeholders = ",".join(["?"] * len(chunk))
                cur.execute(f"DELETE FROM popcen_records WHERE id IN ({placeholders})", chunk)
                total_deleted += cur.rowcount
            conn.commit()
        return total_deleted

    def delete_records_by_geocodes(self, geocodes: List[str]) -> Dict[str, int]:
        """
        Synchronously removes records matching the given 14-digit geocodes across:
          - popcen_records (popcen_database.db)
          - earf_records (earf_database.db)
          - gpkg_records (gpkg_database.db)
          - matched_records (popcen_database.db)
        Ensures deleted records are permanently removed from all databases.
        """
        clean_geocodes = list({str(gc).strip() for gc in geocodes if gc and str(gc).strip()})
        if not clean_geocodes:
            return {"popcen": 0, "earf": 0, "gpkg": 0, "matched": 0}

        counts = {"popcen": 0, "earf": 0, "gpkg": 0, "matched": 0}

        # 1. Delete from popcen_records & matched_records (popcen_database.db)
        with self._get_connection() as conn:
            cur = conn.cursor()
            for i in range(0, len(clean_geocodes), 900):
                chunk = clean_geocodes[i : i + 900]
                placeholders = ",".join(["?"] * len(chunk))
                cur.execute(f"DELETE FROM popcen_records WHERE geocode IN ({placeholders})", chunk)
                counts["popcen"] += cur.rowcount
                cur.execute(f"DELETE FROM matched_records WHERE geocode IN ({placeholders})", chunk)
                counts["matched"] += cur.rowcount
            conn.commit()

        # 2. Delete from earf_records (earf_database.db)
        with self._get_earf_connection() as conn:
            cur = conn.cursor()
            for i in range(0, len(clean_geocodes), 900):
                chunk = clean_geocodes[i : i + 900]
                placeholders = ",".join(["?"] * len(chunk))
                cur.execute(f"DELETE FROM earf_records WHERE geocode IN ({placeholders})", chunk)
                counts["earf"] += cur.rowcount
            conn.commit()

        # 3. Delete from gpkg_records (gpkg_database.db)
        with self._get_gpkg_connection() as conn:
            cur = conn.cursor()
            for i in range(0, len(clean_geocodes), 900):
                chunk = clean_geocodes[i : i + 900]
                placeholders = ",".join(["?"] * len(chunk))
                cur.execute(f"DELETE FROM gpkg_records WHERE geocode IN ({placeholders})", chunk)
                counts["gpkg"] += cur.rowcount
            conn.commit()

        return counts

    def import_dataframe(self, df: pd.DataFrame, replace: bool = True, progress_callback=None) -> int:
        """Bulk inserts a POPCEN DataFrame into the local database with column mapping."""
        if df.empty:
            return 0

        field_map = {
            "geocode": "geocode",
            "geographic_name": "geographic_name",
            "geographic_level": "geographic_level",
            "region_code": "region_code",
            "province_code": "province_code",
            "city_mun_code": "city_mun_code",
            "barangay_code": "barangay_code",
            "ean": "ean",
            "no_of_eas": "no_of_eas",
            "areaname": "areaname",
            "household_population": "household_population",
            "no_of_households": "no_of_households",
            "ea_subject_for_delineation": "ea_subject_for_delineation",
            "ea_subject_for_merging": "ea_subject_for_merging",
            "ea_with_gap_overlap": "ea_with_gap_overlap",
            "estimated_number_of_households": "estimated_number_of_households",
            "remarks": "remarks",
        }

        col_lookup = {}
        for c in df.columns:
            clean = str(c).strip().lower().replace(" ", "_").replace("/", "_").replace(".", "")
            col_lookup[clean] = c

        records_to_insert = []
        for _, row in df.iterrows():
            rec = {}
            for target_col in field_map.keys():
                src_col = col_lookup.get(target_col)
                if src_col is not None:
                    v = row[src_col]
                    rec[target_col] = str(v).strip() if pd.notna(v) and str(v).lower() != "nan" else ""
                else:
                    rec[target_col] = ""

            if not rec.get("geocode") and rec.get("province_code") and rec.get("barangay_code"):
                rec["geocode"] = build_geocode(rec, POPCEN_FIELDS)

            records_to_insert.append(rec)

        cols = list(field_map.keys())
        placeholders = ", ".join(["?"] * len(cols))
        insert_sql = f"INSERT INTO popcen_records ({', '.join(cols)}) VALUES ({placeholders})"

        with self._get_connection() as conn:
            cur = conn.cursor()
            if replace:
                cur.execute("DELETE FROM popcen_records")
                conn.commit()

            batch_size = 5000
            total = len(records_to_insert)
            for i in range(0, total, batch_size):
                batch = records_to_insert[i : i + batch_size]
                rows_data = [[r[c] for c in cols] for r in batch]
                cur.executemany(insert_sql, rows_data)
                conn.commit()
                if progress_callback:
                    pct = int(((i + len(batch)) / total) * 100)
                    if progress_callback(pct, f"Importing POPCEN records ({i + len(batch):,} / {total:,})...") is False:
                        break

        return len(records_to_insert)

    def import_from_file(self, file_path: str, sheet_name=0, replace: bool = True, progress_callback=None) -> int:
        """Reads a POPCEN Excel/CSV file and imports it into the local database."""
        try:
            from ..ui.dialog import load_popcen_data
        except (ImportError, ValueError):
            try:
                from ui.dialog import load_popcen_data
            except (ImportError, ValueError):
                from dialog import load_popcen_data
        df = load_popcen_data(file_path, sheet_name=sheet_name)
        return self.import_dataframe(df, replace=replace, progress_callback=progress_callback)

    def _build_popcen_where_clause(
        self,
        search: Optional[str] = None,
        level: Optional[str] = "5 - EA",
        issue: Optional[str] = None,
        only_ea_level: bool = True,
    ) -> Tuple[str, List[Any]]:
        """Constructs WHERE clause and parameters for popcen_records queries."""
        conditions = []
        params = []

        if only_ea_level:
            conditions.append("geographic_level LIKE '%5 - EA%'")
        elif level and level != "All Levels":
            lvl_code = level[:6].strip()
            conditions.append("geographic_level LIKE ?")
            params.append(f"%{lvl_code}%")

        if search and search.strip():
            s = f"%{search.strip().lower()}%"
            conditions.append("""(
                LOWER(geographic_name) LIKE ? OR
                LOWER(areaname) LIKE ? OR
                geocode LIKE ? OR
                ean LIKE ? OR
                province_code LIKE ?
            )""")
            params.extend([s, s, s, s, s])

        if issue and issue != "All Rows":
            if issue == "EA Rows Only":
                if not only_ea_level:
                    conditions.append("geographic_level LIKE '%5 - EA%'")
            elif issue == "Subject for Delineation":
                conditions.append("TRIM(COALESCE(ea_subject_for_delineation, '')) NOT IN ('', '0', '-')")
            elif issue == "Subject for Merging":
                conditions.append("TRIM(COALESCE(ea_subject_for_merging, '')) NOT IN ('', '0', '-')")
            elif issue == "Gap / Overlap Issues":
                conditions.append("LOWER(TRIM(COALESCE(ea_with_gap_overlap, ''))) IN ('gap & overlap', 'overlap', 'gap')")
            elif issue == "No Issues Only":
                conditions.append("""
                    (TRIM(COALESCE(ea_subject_for_delineation, '')) IN ('', '0', '-')) AND
                    (TRIM(COALESCE(ea_subject_for_merging, '')) IN ('', '0', '-')) AND
                    (LOWER(TRIM(COALESCE(ea_with_gap_overlap, ''))) NOT IN ('gap & overlap', 'overlap', 'gap'))
                """)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        return where_clause, params

    def query_all_popcen_ids_and_geocodes(
        self,
        search: Optional[str] = None,
        level: Optional[str] = "5 - EA",
        issue: Optional[str] = None,
        only_ea_level: bool = True,
    ) -> List[Tuple[int, str]]:
        """Returns all (id, geocode) tuples matching current filters across the entire popcen_records table."""
        where_clause, params = self._build_popcen_where_clause(
            search=search,
            level=level,
            issue=issue,
            only_ea_level=only_ea_level,
        )
        sql = f"SELECT id, COALESCE(geocode, '') FROM popcen_records {where_clause} ORDER BY id ASC"
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            return [(r[0], str(r[1]).strip()) for r in cur.fetchall()]

    def get_popcen_geocodes_by_ids(self, ids: List[int]) -> List[str]:
        """Fetches distinct geocodes for specific popcen_record IDs."""
        if not ids:
            return []
        geocodes = set()
        with self._get_connection() as conn:
            cur = conn.cursor()
            for i in range(0, len(ids), 900):
                chunk = ids[i : i + 900]
                placeholders = ",".join(["?"] * len(chunk))
                cur.execute(f"SELECT DISTINCT geocode FROM popcen_records WHERE id IN ({placeholders}) AND geocode IS NOT NULL", chunk)
                for r in cur.fetchall():
                    if r[0] and str(r[0]).strip():
                        geocodes.add(str(r[0]).strip())
        return list(geocodes)

    def query_records(
        self,
        search: Optional[str] = None,
        level: Optional[str] = "5 - EA",
        issue: Optional[str] = None,
        only_ea_level: bool = True,
        limit: int = 1000,
        offset: int = 0,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Queries POPCEN records with search, filters, pagination, and total count."""
        where_clause, params = self._build_popcen_where_clause(
            search=search,
            level=level,
            issue=issue,
            only_ea_level=only_ea_level,
        )

        count_sql = f"SELECT COUNT(*) FROM popcen_records {where_clause}"
        select_sql = f"""
            SELECT * FROM popcen_records
            {where_clause}
            ORDER BY id ASC
            LIMIT ? OFFSET ?
        """

        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(count_sql, params)
            total_count = cur.fetchone()[0]

            cur.execute(select_sql, params + [limit, offset])
            rows = [dict(r) for r in cur.fetchall()]

        return rows, total_count

    def get_kpis(self) -> Dict[str, Any]:
        """Calculates KPI summary values from popcen_records."""
        kpis = {
            "total_records": 0,
            "total_eas": 0,
            "total_households": 0,
            "delineation": 0,
            "merging": 0,
            "gap_overlap": 0,
            "matched_all": 0,
            "unmatched": 0,
        }

        try:
            with self._get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM popcen_records")
                kpis["total_records"] = cur.fetchone()[0]

                cur.execute("SELECT COUNT(*) FROM popcen_records WHERE geographic_level LIKE '%5 - EA%'")
                kpis["total_eas"] = cur.fetchone()[0]

                cur.execute("""
                SELECT COUNT(*) FROM popcen_records
                WHERE TRIM(COALESCE(ea_subject_for_delineation, '')) NOT IN ('', '0', '-')
                """)
                kpis["delineation"] = cur.fetchone()[0]

                cur.execute("""
                SELECT COUNT(*) FROM popcen_records
                WHERE TRIM(COALESCE(ea_subject_for_merging, '')) NOT IN ('', '0', '-')
                """)
                kpis["merging"] = cur.fetchone()[0]

                cur.execute("""
                SELECT COUNT(*) FROM popcen_records
                WHERE LOWER(TRIM(ea_with_gap_overlap)) IN ('gap & overlap', 'overlap', 'gap')
                """)
                kpis["gap_overlap"] = cur.fetchone()[0]

                cur.execute("SELECT COUNT(*) FROM popcen_records WHERE match_status = 'MATCHED_ALL'")
                kpis["matched_all"] = cur.fetchone()[0]

                cur.execute("SELECT COUNT(*) FROM popcen_records WHERE match_status LIKE 'UNMATCHED%'")
                kpis["unmatched"] = cur.fetchone()[0]

                cur.execute("""
                SELECT no_of_households FROM popcen_records
                WHERE geographic_level LIKE '%5 - EA%' AND no_of_households IS NOT NULL
                """)
                hh_total = 0
                for r in cur.fetchall():
                    val = str(r[0]).replace(",", "").strip()
                    try:
                        hh_total += int(val)
                    except ValueError:
                        pass
                kpis["total_households"] = hh_total
        except Exception:
            pass

        return kpis

    def update_match_status(self, geocode_status_map: Dict[str, str], progress_callback=None):
        """Bulk updates the match_status column from a dictionary of {geocode: status} across all databases."""
        if not geocode_status_map:
            return

        items = list(geocode_status_map.items())
        batch_size = 5000
        total_batches = (len(items) + batch_size - 1) // batch_size

        # 1. Update popcen_records in popcen_database.db
        with self._get_connection() as conn:
            cur = conn.cursor()
            for b_idx in range(total_batches):
                chunk = [(status, gc) for gc, status in items[b_idx * batch_size : (b_idx + 1) * batch_size]]
                cur.executemany("UPDATE popcen_records SET match_status = ? WHERE geocode = ?", chunk)
            conn.commit()

        # 2. Update earf_records in earf_database.db
        if self.is_earf_populated():
            with self._get_earf_connection() as conn:
                cur = conn.cursor()
                for b_idx in range(total_batches):
                    chunk = [(status, gc) for gc, status in items[b_idx * batch_size : (b_idx + 1) * batch_size]]
                    cur.executemany("UPDATE earf_records SET match_status = ? WHERE geocode = ?", chunk)
                conn.commit()

        # 3. Update gpkg_records in gpkg_database.db
        if self.is_gpkg_populated():
            with self._get_gpkg_connection() as conn:
                cur = conn.cursor()
                for b_idx in range(total_batches):
                    chunk = [(status, gc) for gc, status in items[b_idx * batch_size : (b_idx + 1) * batch_size]]
                    cur.executemany("UPDATE gpkg_records SET match_status = ? WHERE geocode = ?", chunk)
                conn.commit()

        if progress_callback:
            progress_callback(100, f"Updated {len(items):,} match statuses across databases...")

    def export_filtered_to_csv(
        self,
        out_csv: str,
        search: Optional[str] = None,
        level: Optional[str] = "5 - EA",
        issue: Optional[str] = None,
        only_ea_level: bool = True,
    ):
        """Streams filtered query results to CSV."""
        rows, _ = self.query_records(
            search=search,
            level=level,
            issue=issue,
            only_ea_level=only_ea_level,
            limit=1000000,
            offset=0,
        )
        if rows:
            df = pd.DataFrame(rows)
            for c in ("code", "hhcount", "sy", "hh_count", "bldgcount", "bldg_count", "eacount"):
                if c in df.columns:
                    df[c] = df[c].apply(to_whole_number_str)
            drop_cols = [c for c in ["id", "fid", "created_at", "geom", "source_files"] if c in df.columns]
            if drop_cols:
                df = df.drop(columns=drop_cols)
            df.to_csv(out_csv, index=False, encoding="utf-8")




    # ----------------------------------------------------------------------
    # Preliminary EARF Consolidation & Database Management
    # ----------------------------------------------------------------------
    def earf_record_count(self) -> int:
        """Returns total records in earf_records."""
        try:
            with self._get_earf_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM earf_records")
                return cur.fetchone()[0]
        except Exception:
            return 0

    def is_earf_populated(self) -> bool:
        """Returns True if earf_records contains data."""
        return self.earf_record_count() > 0

    def clear_earf_records(self):
        """Clears all records from earf_records."""
        conn = self._get_earf_connection()
        try:
            conn.execute("DELETE FROM earf_records")
            conn.commit()
            old_iso = conn.isolation_level
            conn.isolation_level = None
            conn.execute("VACUUM")
            conn.isolation_level = old_iso
        finally:
            conn.close()

    def delete_earf_records_by_ids(self, ids: List[int]) -> int:
        """Deletes specific records from earf_records by their primary key IDs."""
        if not ids:
            return 0
        total_deleted = 0
        with self._get_earf_connection() as conn:
            cur = conn.cursor()
            for i in range(0, len(ids), 900):
                chunk = ids[i : i + 900]
                placeholders = ",".join(["?"] * len(chunk))
                cur.execute(f"DELETE FROM earf_records WHERE id IN ({placeholders})", chunk)
                total_deleted += cur.rowcount
            conn.commit()
        return total_deleted

    def import_earf_from_folder(
        self, folder_path: str, recursive: bool = False, sheet_name=0, replace: bool = True, progress_callback=None
    ) -> Tuple[int, int]:
        """
        Scans folder_path for all Preliminary EARF Excel files (.xlsx / .xls)
        and imports them into earf_records.
        """
        if not folder_path or not os.path.exists(folder_path):
            return 0, 0

        excel_files = find_earf_files_in_folder(folder_path, recursive=recursive, pattern="_earf")





        return self.import_earf_from_files(excel_files, sheet_name=sheet_name, replace=replace, progress_callback=progress_callback)

    def import_earf_from_files(
        self, file_list: List[str], sheet_name=0, replace: bool = True, progress_callback=None
    ) -> Tuple[int, int]:
        """
        Parses multiple Preliminary EARF Excel files and bulk-inserts them into earf_records.
        Returns (total_imported_records, files_count).
        """
        raw_valid = [f for f in file_list if f and os.path.exists(f)]
        if not raw_valid:
            return 0, 0

        # Filter to only include valid Excel files excluding temporary files
        valid_files = [
            f for f in raw_valid
            if f.lower().endswith(('.xlsx', '.xls')) and not os.path.basename(f).startswith('~$')
        ]

        if not valid_files:
            return 0, 0

        all_records = []
        total_files = len(valid_files)

        for f_idx, fpath in enumerate(valid_files):
            fname = os.path.basename(fpath)
            if progress_callback:
                pct = int((f_idx / total_files) * 60)
                if progress_callback(pct, f"Parsing file ({f_idx + 1}/{total_files}): {fname}...") is False:
                    break

            try:
                df = read_earf_table(fpath, sheet_name=sheet_name)
                if df.empty:
                    continue

                col_lookup = {str(c).strip().lower(): c for c in df.columns}

                for _, row in df.iterrows():
                    def _get_val(target_keys):
                        for k in target_keys:
                            found = col_lookup.get(k.lower())
                            if found is not None:
                                val = row[found]
                                if pd.notna(val) and str(val).strip().lower() not in ("nan", "none", "null"):
                                    return str(val).strip()
                        return ""

                    prov = _get_val(["prov", "province", "prov_code", "p_code"])
                    mun = _get_val(["mun", "city_mun", "mun_code", "m_code"])
                    brgy = _get_val(["brgy", "barangay", "brgy_code"])
                    ea = _get_val(["ea", "ean", "ea_code"])

                    gc = _get_val(["geocode", "psgc_ea"])
                    if not gc and (prov or mun or brgy or ea):
                        gc = build_geocode(row.to_dict(), GPKG_EARF_FIELDS)

                    rec = {
                        "geocode": gc,
                        "prov": prov,
                        "mun": mun,
                        "brgy": brgy,
                        "ea": ea,
                        "number_of_eas": _get_val(["number of eas", "number_of_eas", "eas"]),
                        "areaname": _get_val([
                            "province, city, municipality, barangay, and ea",
                            "areaname",
                            "geographic_name",
                            "name",
                        ]),
                        "number_of_households": _get_val(["number of households", "number_of_households", "2024 estimated"]),
                        "number_of_buildings": _get_val(["number of buildings", "number_of_buildings"]),
                        "new_enumeration_area_code": _get_val(["new enumeration area code (2026)", "new enumeration area code", "new ea code (2026)", "new ea code", "new_ean", "new_ea_code", "code"]),
                        "household_count": _get_val(["household count (2026)", "household count", "household_count", "hh_count", "hhcount"]),
                        "building_count": _get_val(["building count (2026)", "building count", "building_count", "bldg_count", "bldgcount"]),
                        "ea_type": _get_val(["ea type", "ea_type", "type"]),
                        "source_year": "2024" if "retain" in _get_val(["ea type", "ea_type", "type"]).lower() else "2026",
                        "remarks": _get_val(["remarks", "remark", "notes"]),
                        "source_file": fname,
                        "match_status": "Pending Match",
                        "consistency_status": "NOT_CHECKED",
                        "consistency_notes": "",
                    }
                    all_records.append(rec)

            except Exception as e:
                pass

        if not all_records:
            return 0, len(valid_files)

        cols = [
            "geocode", "prov", "mun", "brgy", "ea", "number_of_eas", "areaname",
            "number_of_households", "number_of_buildings", "new_enumeration_area_code",
            "household_count", "building_count", "ea_type", "source_year", "remarks",
            "source_file", "match_status", "consistency_status", "consistency_notes",
        ]
        placeholders = ", ".join(["?"] * len(cols))
        insert_sql = f"INSERT INTO earf_records ({', '.join(cols)}) VALUES ({placeholders})"

        with self._get_earf_connection() as conn:
            cur = conn.cursor()
            if replace:
                cur.execute("DELETE FROM earf_records")
                conn.commit()

            batch_size = 5000
            total_recs = len(all_records)
            for i in range(0, total_recs, batch_size):
                batch = all_records[i : i + batch_size]
                rows_data = [[r[c] for c in cols] for r in batch]
                cur.executemany(insert_sql, rows_data)
                conn.commit()
                if progress_callback:
                    pct = 60 + int(((i + len(batch)) / total_recs) * 40)
                    if progress_callback(pct, f"Saving consolidated EARF records ({i + len(batch):,} / {total_recs:,})...") is False:
                        break

        return len(all_records), len(valid_files)

    def import_earf_dataframe(self, df: pd.DataFrame, replace: bool = True, progress_callback=None) -> int:
        """Inserts records from a pandas DataFrame into earf_records."""
        if df.empty:
            return 0

        cols = [
            "geocode", "prov", "mun", "brgy", "ea", "number_of_eas", "areaname",
            "number_of_households", "number_of_buildings", "new_enumeration_area_code",
            "household_count", "building_count", "ea_type", "source_year", "remarks",
            "source_file", "match_status", "consistency_status", "consistency_notes",
        ]
        placeholders = ", ".join(["?"] * len(cols))
        insert_sql = f"INSERT INTO earf_records ({', '.join(cols)}) VALUES ({placeholders})"

        all_records = []
        for _, row in df.iterrows():
            r = row.to_dict()
            rec = {
                c: (str(r.get(c, "") or "").strip() if pd.notna(r.get(c)) else "")
                for c in cols
            }
            if not rec.get("match_status"):
                rec["match_status"] = "Pending Match"
            if not rec.get("consistency_status"):
                rec["consistency_status"] = "PASS"
            all_records.append(rec)

        with self._get_earf_connection() as conn:
            cur = conn.cursor()
            if replace:
                cur.execute("DELETE FROM earf_records")
                conn.commit()

            batch_size = 5000
            total_recs = len(all_records)
            for i in range(0, total_recs, batch_size):
                batch = all_records[i : i + batch_size]
                rows_data = [[r[c] for c in cols] for r in batch]
                cur.executemany(insert_sql, rows_data)
                conn.commit()

        return len(all_records)

    # ----------------------------------------------------------------------
    # Preliminary EARF Consistency Check Engine
    # ----------------------------------------------------------------------
    def run_earf_consistency_checks(self, check_popcen: bool = True, progress_callback=None) -> Dict[str, Any]:
        """
        Executes focused consistency validation across all records in earf_records:
          - R1: Geocode format & structure check (14-digit numeric validation & admin segments)
          - R6: Cross-validation against 2024 POPCEN database (geocode presence)
        """
        results = {
            "total_checked": 0,
            "passed": 0,
            "warnings": 0,
            "errors": 0,
            "missing_popcen": 0,
            "invalid_geocodes": 0,
            "pass_rate": 0.0,
        }

        # 1. Fetch POPCEN geocodes set if available from popcen_database.db
        popcen_geocodes = set()
        popcen_available = False
        if check_popcen and self.is_populated():
            with self._get_connection() as pop_conn:
                pop_cur = pop_conn.cursor()
                pop_cur.execute("SELECT geocode FROM popcen_records WHERE geocode IS NOT NULL AND geocode != ''")
                popcen_geocodes = {r[0] for r in pop_cur.fetchall()}
                popcen_available = len(popcen_geocodes) > 0

        with self._get_earf_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM earf_records")
            total = cur.fetchone()[0]
            if total == 0:
                return results

            results["total_checked"] = total

            # 2. Read all rows for R1 & R6 evaluation
            cur.execute("""
                SELECT id, geocode, prov, mun, brgy, ea, areaname
                FROM earf_records
            """)
            rows = cur.fetchall()

            updates = []
            for idx, r in enumerate(rows):
                rec_id = r["id"]
                gc = str(r["geocode"]).strip() if r["geocode"] else ""
                prov = str(r["prov"]).strip() if r["prov"] else ""
                mun = str(r["mun"]).strip() if r["mun"] else ""
                brgy = str(r["brgy"]).strip() if r["brgy"] else ""
                ea = str(r["ea"]).strip() if r["ea"] else ""

                status = "PASS"
                notes = []

                # ---- R1: Geocode format & structure (14-digit numeric string) ----
                is_r1_valid = True
                if not gc or len(gc) != 14 or not gc.isdigit():
                    is_r1_valid = False
                    status = "ERROR"
                    notes.append("R1: Invalid geocode format (expected 14-digit numeric string)")
                    results["invalid_geocodes"] += 1
                elif not (prov and mun and brgy and ea):
                    is_r1_valid = False
                    status = "ERROR"
                    notes.append("R1: Incomplete administrative codes (Prov, Mun, Brgy, or EA missing)")
                    results["invalid_geocodes"] += 1

                # ---- R6: Cross-validation with POPCEN Database ----
                if is_r1_valid:
                    if popcen_available:
                        if gc not in popcen_geocodes:
                            status = "WARNING"
                            notes.append("R6: Geocode not found in 2024 POPCEN database")
                            results["missing_popcen"] += 1
                        else:
                            notes.append("R1 & R6 passed (Valid format & matched in POPCEN)")
                    else:
                        notes.append("R1 passed (POPCEN database not loaded to check R6)")

                updates.append((status, "; ".join(notes), rec_id))

                if status == "PASS":
                    results["passed"] += 1
                elif status == "WARNING":
                    results["warnings"] += 1
                elif status == "ERROR":
                    results["errors"] += 1

                if progress_callback and idx % 1000 == 0:
                    pct = int((idx / total) * 100)
                    if progress_callback(pct, f"Evaluating R1 & R6 consistency ({idx:,} / {total:,})...") is False:
                        break

            # Bulk update consistency columns
            cur.executemany("UPDATE earf_records SET consistency_status = ?, consistency_notes = ? WHERE id = ?", updates)
            conn.commit()

        results["pass_rate"] = round((results["passed"] / results["total_checked"]) * 100, 1) if results["total_checked"] > 0 else 0.0

        return results

    def _build_earf_where_clause(
        self,
        search: Optional[str] = None,
        ea_type: Optional[str] = None,
        consistency_filter: Optional[str] = "All Records",
        only_with_new_ea: bool = True,
    ) -> Tuple[str, List[Any]]:
        """Constructs WHERE clause and parameters for earf_records queries."""
        conditions = []
        params = []

        if only_with_new_ea:
            conditions.append(
                "(new_enumeration_area_code IS NOT NULL AND TRIM(new_enumeration_area_code) != '' AND LOWER(TRIM(new_enumeration_area_code)) NOT IN ('nan', 'none', 'null', '-'))"
            )

        if search and search.strip():
            s = f"%{search.strip().lower()}%"
            conditions.append("""(
                LOWER(geocode) LIKE ? OR
                LOWER(areaname) LIKE ? OR
                LOWER(prov) LIKE ? OR
                LOWER(mun) LIKE ? OR
                LOWER(brgy) LIKE ? OR
                LOWER(ea) LIKE ? OR
                LOWER(new_enumeration_area_code) LIKE ? OR
                LOWER(remarks) LIKE ? OR
                LOWER(source_file) LIKE ?
            )""")
            params.extend([s, s, s, s, s, s, s, s, s])

        if ea_type and ea_type != "All EA Types":
            if ea_type == "Other":
                conditions.append("UPPER(COALESCE(ea_type, '')) NOT LIKE '%RETAIN%' AND UPPER(COALESCE(ea_type, '')) NOT LIKE '%SPLIT%' AND UPPER(COALESCE(ea_type, '')) NOT LIKE '%MERG%'")
            else:
                conditions.append("UPPER(ea_type) LIKE ?")
                params.append(f"%{ea_type.upper()}%")

        if consistency_filter and consistency_filter != "All Records":
            if "PASS" in consistency_filter:
                conditions.append("consistency_status = 'PASS'")
            elif "All Issues" in consistency_filter:
                conditions.append("consistency_status IN ('WARNING', 'ERROR')")
            elif "POPCEN" in consistency_filter or "Missing" in consistency_filter:
                conditions.append("(consistency_status = 'WARNING' OR consistency_notes LIKE '%POPCEN%')")
            elif "Invalid" in consistency_filter or "R1" in consistency_filter:
                conditions.append("(consistency_status = 'ERROR' OR consistency_notes LIKE '%Invalid geocode%' OR consistency_notes LIKE '%R1%')")
            elif consistency_filter == "Multi-Record / Split EAs (Info)":
                conditions.append("consistency_status = 'MULTI_RECORD'")

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        return where_clause, params

    def query_earf_records(
        self,
        search: Optional[str] = None,
        ea_type: Optional[str] = None,
        consistency_filter: Optional[str] = "All Records",
        only_with_new_ea: bool = True,
        limit: int = 1000,
        offset: int = 0,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Queries earf_records with search, EA type, consistency filter, New EA Code requirement, and pagination."""
        where_clause, params = self._build_earf_where_clause(
            search=search,
            ea_type=ea_type,
            consistency_filter=consistency_filter,
            only_with_new_ea=only_with_new_ea,
        )

        count_sql = f"SELECT COUNT(*) FROM earf_records {where_clause}"
        select_sql = f"""
            SELECT * FROM earf_records
            {where_clause}
            ORDER BY id ASC
            LIMIT ? OFFSET ?
        """

        with self._get_earf_connection() as conn:
            cur = conn.cursor()
            cur.execute(count_sql, params)
            total_count = cur.fetchone()[0]

            cur.execute(select_sql, params + [limit, offset])
            rows = [dict(r) for r in cur.fetchall()]

        return rows, total_count

    def query_all_earf_ids_and_geocodes(
        self,
        search: Optional[str] = None,
        ea_type: Optional[str] = None,
        consistency_filter: Optional[str] = "All Records",
        only_with_new_ea: bool = True,
    ) -> List[Tuple[int, str]]:
        """Returns all (id, geocode) tuples matching current filters across the entire earf_records table."""
        where_clause, params = self._build_earf_where_clause(
            search=search,
            ea_type=ea_type,
            consistency_filter=consistency_filter,
            only_with_new_ea=only_with_new_ea,
        )
        sql = f"SELECT id, COALESCE(geocode, '') FROM earf_records {where_clause} ORDER BY id ASC"
        with self._get_earf_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            return [(r[0], str(r[1]).strip()) for r in cur.fetchall()]

    def get_earf_geocodes_by_ids(self, ids: List[int]) -> List[str]:
        """Fetches distinct geocodes for specific earf_record IDs."""
        if not ids:
            return []
        geocodes = set()
        with self._get_earf_connection() as conn:
            cur = conn.cursor()
            for i in range(0, len(ids), 900):
                chunk = ids[i : i + 900]
                placeholders = ",".join(["?"] * len(chunk))
                cur.execute(f"SELECT DISTINCT geocode FROM earf_records WHERE id IN ({placeholders}) AND geocode IS NOT NULL", chunk)
                for r in cur.fetchall():
                    if r[0] and str(r[0]).strip():
                        geocodes.add(str(r[0]).strip())
        return list(geocodes)

    def get_earf_kpis(self, only_with_new_ea: bool = True) -> Dict[str, Any]:
        """Calculates KPI summary values from earf_records."""
        kpis = {
            "total_files": 0,
            "total_records": 0,
            "unique_geocodes": 0,
            "retained": 0,
            "split": 0,
            "merged": 0,
            "passed": 0,
            "warnings": 0,
            "errors": 0,
            "pass_rate": 0.0,
        }

        where_clause = "WHERE (new_enumeration_area_code IS NOT NULL AND TRIM(new_enumeration_area_code) != '' AND LOWER(TRIM(new_enumeration_area_code)) NOT IN ('nan', 'none', 'null', '-'))" if only_with_new_ea else ""
        and_clause = " AND (new_enumeration_area_code IS NOT NULL AND TRIM(new_enumeration_area_code) != '' AND LOWER(TRIM(new_enumeration_area_code)) NOT IN ('nan', 'none', 'null', '-'))" if only_with_new_ea else ""

        try:
            with self._get_earf_connection() as conn:
                cur = conn.cursor()
                cur.execute(f"SELECT COUNT(*) FROM earf_records {where_clause}")
                kpis["total_records"] = cur.fetchone()[0]
                if kpis["total_records"] == 0:
                    return kpis

                cur.execute(f"SELECT COUNT(DISTINCT source_file) FROM earf_records WHERE source_file IS NOT NULL{and_clause}")
                kpis["total_files"] = cur.fetchone()[0]

                cur.execute(f"SELECT COUNT(DISTINCT geocode) FROM earf_records WHERE geocode IS NOT NULL AND geocode != ''{and_clause}")
                kpis["unique_geocodes"] = cur.fetchone()[0]

                cur.execute(f"SELECT COUNT(*) FROM earf_records WHERE UPPER(ea_type) LIKE '%RETAIN%'{and_clause}")
                kpis["retained"] = cur.fetchone()[0]

                cur.execute(f"SELECT COUNT(*) FROM earf_records WHERE UPPER(ea_type) LIKE '%SPLIT%'{and_clause}")
                kpis["split"] = cur.fetchone()[0]

                cur.execute(f"SELECT COUNT(*) FROM earf_records WHERE UPPER(ea_type) LIKE '%MERG%'{and_clause}")
                kpis["merged"] = cur.fetchone()[0]

                cur.execute(f"SELECT COUNT(*) FROM earf_records WHERE consistency_status = 'PASS'{and_clause}")
                kpis["passed"] = cur.fetchone()[0]

                cur.execute(f"SELECT COUNT(*) FROM earf_records WHERE consistency_status = 'WARNING'{and_clause}")
                kpis["warnings"] = cur.fetchone()[0]

                cur.execute(f"SELECT COUNT(*) FROM earf_records WHERE consistency_status = 'ERROR'{and_clause}")
                kpis["errors"] = cur.fetchone()[0]

                total_checked = kpis["passed"] + kpis["warnings"] + kpis["errors"]
                if total_checked > 0:
                    kpis["pass_rate"] = round((kpis["passed"] / total_checked) * 100, 1)

        except Exception:
            pass

        return kpis

    def export_filtered_earf_to_csv(
        self,
        out_csv: str,
        search: Optional[str] = None,
        ea_type: Optional[str] = None,
        consistency_filter: Optional[str] = "All Records",
        only_with_new_ea: bool = True,
    ):
        """Streams filtered EARF records to CSV."""
        rows, _ = self.query_earf_records(
            search=search,
            ea_type=ea_type,
            consistency_filter=consistency_filter,
            only_with_new_ea=only_with_new_ea,
            limit=1000000,
            offset=0,
        )
        if rows:
            df = pd.DataFrame(rows)
            drop_cols = [c for c in ["id", "fid", "created_at", "geom", "source_files"] if c in df.columns]
            if drop_cols:
                df = df.drop(columns=drop_cols)
            df.to_csv(out_csv, index=False, encoding="utf-8")




    # ----------------------------------------------------------------------
    # EA GeoPackage Consolidation & Database Management
    # ----------------------------------------------------------------------
    def gpkg_record_count(self) -> int:
        """Returns total records in gpkg_records."""
        try:
            with self._get_gpkg_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM gpkg_records")
                return cur.fetchone()[0]
        except Exception:
            return 0

    def is_gpkg_populated(self) -> bool:
        """Returns True if gpkg_records contains data."""
        return self.gpkg_record_count() > 0

    def clear_gpkg_records(self):
        """Clears all records from gpkg_records."""
        conn = self._get_gpkg_connection()
        try:
            conn.execute("DELETE FROM gpkg_records")
            conn.commit()
            old_iso = conn.isolation_level
            conn.isolation_level = None
            conn.execute("VACUUM")
            conn.isolation_level = old_iso
        finally:
            conn.close()

    def delete_gpkg_records_by_ids(self, ids: List[int]) -> int:
        """Deletes specific records from gpkg_records by their primary key IDs."""
        if not ids:
            return 0
        total_deleted = 0
        with self._get_gpkg_connection() as conn:
            cur = conn.cursor()
            for i in range(0, len(ids), 900):
                chunk = ids[i : i + 900]
                placeholders = ",".join(["?"] * len(chunk))
                cur.execute(f"DELETE FROM gpkg_records WHERE id IN ({placeholders})", chunk)
                total_deleted += cur.rowcount
            conn.commit()
        return total_deleted

    def import_gpkg_from_folder(
        self, folder_path: str, recursive: bool = False, replace: bool = True, progress_callback=None
    ) -> Tuple[int, int]:
        """
        Scans folder_path for all GeoPackage files (.gpkg / .sqlite)
        excluding 'versions' directories, and imports them into gpkg_records.
        """
        gpkg_files = find_gpkg_files_in_folder(folder_path, recursive=recursive)
        return self.import_gpkg_from_files(gpkg_files, replace=replace, progress_callback=progress_callback)

    def import_gpkg_from_files(
        self, file_list: List[str], replace: bool = True, progress_callback=None
    ) -> Tuple[int, int]:
        """
        Loads multiple GeoPackage files, evaluates multi-file consistency, and bulk-inserts them into gpkg_records.
        Returns (total_imported_records, files_count).
        """
        valid_files = [f for f in file_list if f and os.path.exists(f)]
        if not valid_files:
            return 0, 0

        try:
            from ..ui.dialog import load_gpkg_data, analyze_gpkg_consistency
        except (ImportError, ValueError):
            try:
                from ui.dialog import load_gpkg_data, analyze_gpkg_consistency
            except (ImportError, ValueError):
                try:
                    from .dialog import load_gpkg_data, analyze_gpkg_consistency
                except (ImportError, ValueError):
                    from dialog import load_gpkg_data, analyze_gpkg_consistency

        all_dfs = []
        total_files = len(valid_files)

        for f_idx, fpath in enumerate(valid_files):
            fname = os.path.basename(fpath)
            if progress_callback:
                pct = int((f_idx / total_files) * 60)
                if progress_callback(pct, f"Reading GPKG ({f_idx + 1}/{total_files}): {fname}...") is False:
                    break
            try:
                df_single = load_gpkg_data(fpath)
                if not df_single.empty:
                    all_dfs.append(df_single)
            except Exception:
                pass

        if not all_dfs:
            return 0, len(valid_files)

        if progress_callback:
            progress_callback(65, "Analyzing multi-file consistency, duplicates, and overlaps...")

        combined_df = pd.concat(all_dfs, ignore_index=True)
        combined_df, _ = analyze_gpkg_consistency(combined_df)

        return self.import_gpkg_dataframe(combined_df, replace=replace, progress_callback=progress_callback), len(valid_files)

    def import_gpkg_dataframe(self, df: pd.DataFrame, replace: bool = True, progress_callback=None) -> int:
        """Bulk inserts a GeoPackage DataFrame into the local gpkg_records table."""
        if df.empty:
            return 0

        cols = [
            "fid", "map_uuid", "geocode", "region", "province", "city_mun", "barangay", "ean", "name",
            "code", "hhcount", "bldgcount", "sy", "new_ean", "hh_count", "bldg_count",
            "ea_type", "eacount", "remarks", "layer_source", "source_file",
            "geom_status", "consistency_status", "consistency_detail", "dup_count",
            "file_count", "source_files"
        ]

        # Generate a new system base fid to prevent duplicate key errors and conflicts
        next_fid = 1
        if not replace:
            try:
                with self._get_gpkg_connection() as conn:
                    cur = conn.cursor()
                    cur.execute("SELECT MAX(COALESCE(fid, id, 0)) FROM gpkg_records")
                    row = cur.fetchone()
                    if row and row[0] is not None:
                        next_fid = int(row[0]) + 1
            except Exception:
                next_fid = 1

        def _find_val(row_dict, candidates):
            for c in candidates:
                for k, v in row_dict.items():
                    if k.strip().lower() == c.strip().lower() and pd.notna(v):
                        val_str = str(v).strip()
                        if val_str.lower() not in ("nan", "none", "null"):
                            return val_str
            return ""

        all_records = []
        for _, row in df.iterrows():
            r_dict = row.to_dict()
            gc = str(r_dict.get("geocode", "")).strip()
            if not gc:
                gc = build_geocode(r_dict, GPKG_EARF_FIELDS)

            rec_fid = next_fid
            next_fid += 1

            m_uuid = _find_val(r_dict, ["map_uuid", "uuid"])
            if not m_uuid:
                import uuid
                m_uuid = str(uuid.uuid4())

            rec = {
                "fid": rec_fid,
                "map_uuid": m_uuid,
                "geocode": gc,
                "region": _find_val(r_dict, ["region", "reg", "region_code", "reg_code", "r_code"]),
                "province": _find_val(r_dict, ["province", "prov", "prov_code", "p_code", "province_code"]),
                "city_mun": _find_val(r_dict, ["city_mun", "mun", "city", "city_municipality", "mun_code", "m_code"]),
                "barangay": _find_val(r_dict, ["barangay", "brgy", "brgy_code", "b_code"]),
                "ean": _find_val(r_dict, ["ean", "ea", "ea_code"]),
                "name": _find_val(r_dict, ["name", "areaname", "geographic_name", "barangay_name"]),
                "new_ean": _find_val(r_dict, ["new_ean", "code", "new_ea_code", "new enumeration area code", "new_enumeration_area_code"]),
                "hh_count": to_whole_number_str(_find_val(r_dict, ["hh_count", "hhcount", "household_count", "household count", "number_of_households", "number of households"])),
                "bldg_count": to_whole_number_str(_find_val(r_dict, ["bldg_count", "bldgcount", "building_count", "building count", "number_of_buildings", "number of buildings"])),
                "ea_type": _find_val(r_dict, ["ea_type", "type", "ea type"]),
                "eacount": to_whole_number_str(_find_val(r_dict, ["eacount", "ea_count", "number_of_eas", "number of eas"])),
                "hhcount": to_whole_number_str(_find_val(r_dict, ["hhcount", "hh_count"])),
                "bldgcount": to_whole_number_str(_find_val(r_dict, ["bldgcount", "bldg_count"])),
                "sy": to_whole_number_str(_find_val(r_dict, ["sy", "source_year", "year"])),
                "code": to_whole_number_str(_find_val(r_dict, ["code", "new_ean"])),
                "remarks": _find_val(r_dict, ["remarks", "remark", "notes", "description"]),
                "layer_source": _find_val(r_dict, ["layer_source", "layer", "source"]),
                "source_file": _find_val(r_dict, ["source_file", "filename", "file"]),
                "geom_status": _find_val(r_dict, ["geom_status", "geometry_status", "geometry", "geom"]) or "Polygon",
                "consistency_status": str(r_dict.get("consistency_status") or "UNIQUE"),
                "consistency_detail": str(r_dict.get("consistency_detail") or ""),
                "dup_count": int(r_dict.get("dup_count", 1) or 1),
                "file_count": int(r_dict.get("file_count", 1) or 1),
                "source_files": str(r_dict.get("source_files") or ""),
            }
            all_records.append(rec)

        # Check map_uuid for duplicates: if there are duplicates, check ea_type for Delineated values
        # and update with a new UUID to prevent duplicate map_uuid
        seen_map_uuids = collections.defaultdict(list)
        for idx, rec in enumerate(all_records):
            u = rec.get("map_uuid", "")
            if u:
                seen_map_uuids[u].append(idx)

        import uuid
        for u, indices in seen_map_uuids.items():
            if len(indices) > 1:
                delin_indices = []
                other_indices = []
                for idx in indices:
                    rec = all_records[idx]
                    ea_t = str(rec.get("ea_type") or "").strip().upper()
                    rem = str(rec.get("remarks") or "").strip().lower()
                    if "DELIN" in ea_t or "delineat" in rem or "split" in ea_t or "split" in rem:
                        delin_indices.append(idx)
                    else:
                        other_indices.append(idx)

                # Delineated duplicates receive new UUIDs to prevent duplication
                target_indices = delin_indices if other_indices else delin_indices[1:]
                for idx in target_indices:
                    all_records[idx]["map_uuid"] = str(uuid.uuid4())

        placeholders = ", ".join(["?"] * len(cols))
        insert_sql = f"INSERT INTO gpkg_records ({', '.join(cols)}) VALUES ({placeholders})"

        with self._get_gpkg_connection() as conn:
            cur = conn.cursor()
            if replace:
                cur.execute("DELETE FROM gpkg_records")
                conn.commit()

            batch_size = 5000
            total_recs = len(all_records)
            for i in range(0, total_recs, batch_size):
                batch = all_records[i : i + batch_size]
                rows_data = [[r[c] for c in cols] for r in batch]
                cur.executemany(insert_sql, rows_data)
                conn.commit()
                if progress_callback:
                    pct = 70 + int(((i + len(batch)) / total_recs) * 30)
                    if progress_callback(pct, f"Saving consolidated GPKG records ({i + len(batch):,} / {total_recs:,})...") is False:
                        break

        return len(all_records)

    def deduplicate_gpkg_map_uuids(self) -> int:
        """
        Scans gpkg_records for duplicate map_uuid values.
        When duplicates are found, checks the ea_type for Delineated values
        and updates them with a new UUID to prevent duplicates.
        Returns the number of records updated.
        """
        import uuid
        with self._get_gpkg_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT map_uuid, COUNT(*) as cnt
                FROM gpkg_records
                WHERE map_uuid IS NOT NULL AND map_uuid != ''
                GROUP BY map_uuid
                HAVING cnt > 1
            """)
            dup_uuids = [r[0] for r in cur.fetchall()]
            if not dup_uuids:
                return 0

            updates = []
            for u in dup_uuids:
                cur.execute("SELECT id, geocode, ean, ea_type, remarks FROM gpkg_records WHERE map_uuid = ? ORDER BY id", (u,))
                rows = [dict(r) for r in cur.fetchall()]

                delin_rows = []
                other_rows = []
                for r in rows:
                    ea_t = str(r.get("ea_type") or "").strip().upper()
                    rem = str(r.get("remarks") or "").strip().lower()
                    if "DELIN" in ea_t or "delineat" in rem or "split" in ea_t or "split" in rem:
                        delin_rows.append(r)
                    else:
                        other_rows.append(r)

                target_rows = delin_rows if other_rows else delin_rows[1:]
                for r in target_rows:
                    updates.append((str(uuid.uuid4()), r["id"]))

            if updates:
                cur.executemany("UPDATE gpkg_records SET map_uuid = ? WHERE id = ?", updates)
                conn.commit()

            return len(updates)

    def run_gpkg_consistency_checks(self, check_popcen: bool = True, progress_callback=None) -> Dict[str, Any]:
        """
        Executes multi-file consistency, overlap, multipart, and attribute duplication analysis
        across all records currently stored in gpkg_records, updating their consistency statuses.
        """
        results = {
            "total_checked": 0,
            "unique_geocodes": 0,
            "cross_file_overlaps": 0,
            "multipart_geocodes": 0,
            "attribute_conflicts": 0,
            "missing_geometry": 0,
            "invalid_geocodes": 0,
            "status_level": "CLEAN",
        }

        with self._get_gpkg_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM gpkg_records")
            total = cur.fetchone()[0]
            if total == 0:
                return results

            results["total_checked"] = total
            if progress_callback:
                progress_callback(10, f"Evaluating consistency across {total:,} GPKG records...")

            # 1. Fetch all rows
            cur.execute("""
                SELECT id, geocode, layer_source, source_file, geom_status, ea_type,
                       region, province, city_mun, barangay, ean, hh_count, bldg_count
                FROM gpkg_records
            """)
            rows = [dict(r) for r in cur.fetchall()]

            # 2. Count geocodes and files
            import collections
            gc_map = collections.defaultdict(list)
            for r in rows:
                gc = str(r.get("geocode") or "").strip()
                gc_map[gc].append(r)

            results["unique_geocodes"] = len([gc for gc in gc_map if gc])

            # Attribute conflict detection
            conflict_geocodes = set()
            conflict_notes = {}
            for gc, r_list in gc_map.items():
                if len(r_list) > 1 and gc:
                    mismatches = []
                    for check_key in ["ea_type", "province", "city_mun", "barangay", "ean", "hh_count", "bldg_count"]:
                        vals = {str(r.get(check_key, "")).strip() for r in r_list if str(r.get(check_key, "")).strip() not in ("", "nan", "None")}
                        if len(vals) > 1:
                            mismatches.append(f"{check_key}: {list(vals)}")
                    if mismatches:
                        conflict_geocodes.add(gc)
                        conflict_notes[gc] = "; ".join(mismatches)

            results["attribute_conflicts"] = len(conflict_geocodes)

            # Compute updates
            updates = []
            cross_file_set = set()
            multipart_set = set()
            missing_geom_cnt = 0
            invalid_gc_cnt = 0

            for idx, r in enumerate(rows):
                if progress_callback and idx % 1000 == 0:
                    pct = 10 + int((idx / total) * 50)
                    if progress_callback(pct, f"Evaluating GPKG consistency ({idx:,} / {total:,})...") is False:
                        break
                rid = r["id"]
                gc = str(r.get("geocode") or "").strip()
                geom = str(r.get("geom_status") or "")
                matching_rows = gc_map.get(gc, [])
                dup_count = len(matching_rows)
                files = list({str(m.get("source_file") or m.get("layer_source") or "").strip() for m in matching_rows if m.get("source_file") or m.get("layer_source")})
                file_count = len(files)
                source_files_str = ", ".join(files)

                is_missing_geom = (not geom or "No Geometry" in geom or "none" in geom.lower())
                is_invalid_gc = (not gc or len(gc) < 9 or not gc.isalnum())

                if is_invalid_gc:
                    st = "INVALID_GEOCODE"
                    dt = "Geocode is empty or malformed (<9 chars)"
                    invalid_gc_cnt += 1
                elif is_missing_geom:
                    st = "NO_GEOMETRY"
                    dt = "Feature lacks valid geometry or has empty polygon"
                    missing_geom_cnt += 1
                elif gc in conflict_geocodes:
                    st = "CONFLICTING_DATA"
                    dt = f"Attribute mismatch across duplicates ({conflict_notes.get(gc, '')})"
                    if file_count > 1:
                        cross_file_set.add(gc)
                elif file_count > 1:
                    st = "CROSS_FILE_DUP"
                    dt = f"Geocode appears in {file_count} separate files: {source_files_str}"
                    cross_file_set.add(gc)
                elif dup_count > 1:
                    st = "MULTIPART_PARTS"
                    dt = f"Appears {dup_count} times in same file (multipart EA polygon parts)"
                    multipart_set.add(gc)
                else:
                    st = "UNIQUE"
                    dt = "Unique single-part EA record"

                updates.append((st, dt, dup_count, file_count, source_files_str, rid))

            results["cross_file_overlaps"] = len(cross_file_set)
            results["multipart_geocodes"] = len(multipart_set)
            results["missing_geometry"] = missing_geom_cnt
            results["invalid_geocodes"] = invalid_gc_cnt

            if results["cross_file_overlaps"] > 0 or results["attribute_conflicts"] > 0:
                results["status_level"] = "OVERLAPS_FOUND"
            elif results["multipart_geocodes"] > 0 or missing_geom_cnt > 0 or invalid_gc_cnt > 0:
                results["status_level"] = "WARNING"
            else:
                results["status_level"] = "CLEAN"

            if progress_callback:
                progress_callback(60, "Writing consistency updates to database...")

            batch_size = 5000
            for i in range(0, len(updates), batch_size):
                chunk = updates[i : i + batch_size]
                cur.executemany("""
                    UPDATE gpkg_records
                    SET consistency_status = ?,
                        consistency_detail = ?,
                        dup_count = ?,
                        file_count = ?,
                        source_files = ?
                    WHERE id = ?
                """, chunk)
                conn.commit()

        return results

    def _build_gpkg_where_clause(
        self,
        search: Optional[str] = None,
        ea_type: Optional[str] = None,
        issue_filter: Optional[str] = "All Records",
    ) -> Tuple[str, List[Any]]:
        """Constructs WHERE clause and parameters for gpkg_records queries."""
        conditions = []
        params = []

        if search and search.strip():
            s = f"%{search.strip().lower()}%"
            conditions.append("""(
                LOWER(geocode) LIKE ? OR
                LOWER(name) LIKE ? OR
                LOWER(province) LIKE ? OR
                LOWER(city_mun) LIKE ? OR
                LOWER(barangay) LIKE ? OR
                LOWER(ean) LIKE ? OR
                LOWER(new_ean) LIKE ? OR
                LOWER(remarks) LIKE ? OR
                LOWER(source_file) LIKE ? OR
                LOWER(layer_source) LIKE ?
            )""")
            params.extend([s, s, s, s, s, s, s, s, s, s])

        if ea_type and ea_type != "All EA Types":
            if ea_type == "Other":
                conditions.append("UPPER(COALESCE(ea_type, '')) NOT LIKE '%RETAIN%' AND UPPER(COALESCE(ea_type, '')) NOT LIKE '%SPLIT%' AND UPPER(COALESCE(ea_type, '')) NOT LIKE '%MERG%'")
            else:
                conditions.append("UPPER(ea_type) LIKE ?")
                params.append(f"%{ea_type.upper()}%")

        if issue_filter and issue_filter != "All Records":
            if issue_filter == "Any Issue / Duplicate":
                conditions.append("consistency_status != 'UNIQUE'")
            elif issue_filter == "Cross-File Overlaps Only":
                conditions.append("consistency_status IN ('CROSS_FILE_DUP', 'CONFLICTING_DATA') AND file_count > 1")
            elif issue_filter == "Intra-File Multipart Only":
                conditions.append("consistency_status = 'MULTIPART_PARTS'")
            elif issue_filter == "Conflicting Data Only":
                conditions.append("consistency_status = 'CONFLICTING_DATA'")
            elif issue_filter == "Missing Geometry Only":
                conditions.append("consistency_status = 'NO_GEOMETRY'")
            elif issue_filter == "Unique & Clean Only":
                conditions.append("consistency_status = 'UNIQUE'")

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        return where_clause, params

    def query_gpkg_records(
        self,
        search: Optional[str] = None,
        ea_type: Optional[str] = None,
        issue_filter: Optional[str] = "All Records",
        limit: int = 1000,
        offset: int = 0,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Queries gpkg_records with search, EA type, issue filter, and pagination."""
        where_clause, params = self._build_gpkg_where_clause(
            search=search,
            ea_type=ea_type,
            issue_filter=issue_filter,
        )

        count_sql = f"SELECT COUNT(*) FROM gpkg_records {where_clause}"
        select_sql = f"""
            SELECT * FROM gpkg_records
            {where_clause}
            ORDER BY id ASC
            LIMIT ? OFFSET ?
        """

        with self._get_gpkg_connection() as conn:
            cur = conn.cursor()
            cur.execute(count_sql, params)
            total_count = cur.fetchone()[0]

            cur.execute(select_sql, params + [limit, offset])
            rows = [dict(r) for r in cur.fetchall()]

        return rows, total_count

    def query_all_gpkg_ids_and_geocodes(
        self,
        search: Optional[str] = None,
        ea_type: Optional[str] = None,
        issue_filter: Optional[str] = "All Records",
    ) -> List[Tuple[int, str]]:
        """Returns all (id, geocode) tuples matching current filters across the entire gpkg_records table."""
        where_clause, params = self._build_gpkg_where_clause(
            search=search,
            ea_type=ea_type,
            issue_filter=issue_filter,
        )
        sql = f"SELECT id, COALESCE(geocode, '') FROM gpkg_records {where_clause} ORDER BY id ASC"
        with self._get_gpkg_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            return [(r[0], str(r[1]).strip()) for r in cur.fetchall()]

    def get_gpkg_geocodes_by_ids(self, ids: List[int]) -> List[str]:
        """Fetches distinct geocodes for specific gpkg_record IDs."""
        if not ids:
            return []
        geocodes = set()
        with self._get_gpkg_connection() as conn:
            cur = conn.cursor()
            for i in range(0, len(ids), 900):
                chunk = ids[i : i + 900]
                placeholders = ",".join(["?"] * len(chunk))
                cur.execute(f"SELECT DISTINCT geocode FROM gpkg_records WHERE id IN ({placeholders}) AND geocode IS NOT NULL", chunk)
                for r in cur.fetchall():
                    if r[0] and str(r[0]).strip():
                        geocodes.add(str(r[0]).strip())
        return list(geocodes)

    def get_gpkg_kpis(self) -> Dict[str, Any]:
        """Calculates KPI summary values from gpkg_records."""
        kpis = {
            "total_files": 0,
            "total_features": 0,
            "polygons": 0,
            "unique_geocodes": 0,
            "duplicate_geocodes": 0,
            "cross_file_overlaps": 0,
            "retained": 0,
            "split": 0,
            "merged": 0,
            "consistency_label": "Pending Check",
            "consistency_color": "#64748b",
            "attribute_conflicts": 0,
            "multipart_geocodes": 0,
            "missing_geometry": 0,
            "invalid_geocodes": 0,
        }

        try:
            with self._get_gpkg_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM gpkg_records")
                kpis["total_features"] = cur.fetchone()[0]
                if kpis["total_features"] == 0:
                    return kpis

                cur.execute("SELECT COUNT(DISTINCT source_file) FROM gpkg_records WHERE source_file IS NOT NULL AND source_file != ''")
                kpis["total_files"] = cur.fetchone()[0]
                if kpis["total_files"] == 0:
                    cur.execute("SELECT COUNT(DISTINCT layer_source) FROM gpkg_records WHERE layer_source IS NOT NULL AND layer_source != ''")
                    kpis["total_files"] = cur.fetchone()[0]

                cur.execute("SELECT COUNT(*) FROM gpkg_records WHERE geom_status LIKE '%Polygon%'")
                kpis["polygons"] = cur.fetchone()[0]

                cur.execute("SELECT COUNT(DISTINCT geocode) FROM gpkg_records WHERE geocode IS NOT NULL AND geocode != ''")
                kpis["unique_geocodes"] = cur.fetchone()[0]

                cur.execute("SELECT COUNT(DISTINCT geocode) FROM gpkg_records WHERE dup_count > 1 AND geocode IS NOT NULL AND geocode != ''")
                kpis["duplicate_geocodes"] = cur.fetchone()[0]

                cur.execute("SELECT COUNT(DISTINCT geocode) FROM gpkg_records WHERE consistency_status IN ('CROSS_FILE_DUP', 'CONFLICTING_DATA') AND file_count > 1")
                kpis["cross_file_overlaps"] = cur.fetchone()[0]

                cur.execute("SELECT COUNT(*) FROM gpkg_records WHERE UPPER(ea_type) LIKE '%RETAIN%'")
                kpis["retained"] = cur.fetchone()[0]

                cur.execute("SELECT COUNT(*) FROM gpkg_records WHERE UPPER(ea_type) LIKE '%SPLIT%'")
                kpis["split"] = cur.fetchone()[0]

                cur.execute("SELECT COUNT(*) FROM gpkg_records WHERE UPPER(ea_type) LIKE '%MERG%'")
                kpis["merged"] = cur.fetchone()[0]

                cur.execute("SELECT COUNT(DISTINCT geocode) FROM gpkg_records WHERE consistency_status = 'CONFLICTING_DATA'")
                kpis["attribute_conflicts"] = cur.fetchone()[0]

                cur.execute("SELECT COUNT(DISTINCT geocode) FROM gpkg_records WHERE consistency_status = 'MULTIPART_PARTS'")
                kpis["multipart_geocodes"] = cur.fetchone()[0]

                cur.execute("SELECT COUNT(*) FROM gpkg_records WHERE consistency_status = 'NO_GEOMETRY'")
                kpis["missing_geometry"] = cur.fetchone()[0]

                cur.execute("SELECT COUNT(*) FROM gpkg_records WHERE consistency_status = 'INVALID_GEOCODE'")
                kpis["invalid_geocodes"] = cur.fetchone()[0]

                if kpis["cross_file_overlaps"] > 0:
                    kpis["consistency_label"] = f"{kpis['cross_file_overlaps']} Overlaps"
                    kpis["consistency_color"] = "#dc2626"
                elif kpis["attribute_conflicts"] > 0:
                    kpis["consistency_label"] = f"{kpis['attribute_conflicts']} Conflicts"
                    kpis["consistency_color"] = "#ea580c"
                elif kpis["multipart_geocodes"] > 0:
                    kpis["consistency_label"] = f"{kpis['multipart_geocodes']} Multiparts"
                    kpis["consistency_color"] = "#0284c7"
                elif kpis["missing_geometry"] > 0 or kpis["invalid_geocodes"] > 0:
                    kpis["consistency_label"] = "Geom/Code Err"
                    kpis["consistency_color"] = "#dc2626"
                else:
                    kpis["consistency_label"] = "100% Clean"
                    kpis["consistency_color"] = "#15803d"

        except Exception:
            pass

        return kpis

    def export_filtered_gpkg_to_csv(
        self,
        out_csv: str,
        search: Optional[str] = None,
        ea_type: Optional[str] = None,
        issue_filter: Optional[str] = "All Records",
    ):
        """Streams filtered GPKG records to CSV."""
        rows, _ = self.query_gpkg_records(
            search=search,
            ea_type=ea_type,
            issue_filter=issue_filter,
            limit=1000000,
            offset=0,
        )
        if rows:
            df = pd.DataFrame(rows)
            drop_cols = [c for c in ["id", "fid", "created_at", "geom", "source_files"] if c in df.columns]
            if drop_cols:
                df = df.drop(columns=drop_cols)
            df.to_csv(out_csv, index=False, encoding="utf-8")




    # ----------------------------------------------------------------------
    # Reconciled Matched Records Methods
    # ----------------------------------------------------------------------
    def matched_record_count(self) -> int:
        """Returns the total number of records currently in matched_records."""
        try:
            with self._get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM matched_records")
                return cur.fetchone()[0]
        except Exception:
            return 0

    def is_matched_populated(self) -> bool:
        return self.matched_record_count() > 0

    def clear_matched_records(self):
        """Clears all records from matched_records."""
        conn = self._get_connection()
        try:
            conn.execute("DELETE FROM matched_records")
            conn.commit()
            old_iso = conn.isolation_level
            conn.isolation_level = None
            conn.execute("VACUUM")
            conn.isolation_level = old_iso
        finally:
            conn.close()



    def store_matched_records(self, df_or_records, replace: bool = True, progress_callback=None) -> int:
        """
        Stores matched outer-join reconciliation records into SQLite.
        Supports either a pandas DataFrame or list of dicts.
        Computes 'recoded_ean' per Barangay adhering to PSA EARF rules.
        """
        if isinstance(df_or_records, pd.DataFrame):
            records = df_or_records.to_dict(orient="records")
        elif isinstance(df_or_records, list):
            records = df_or_records
        else:
            return 0

        if not records:
            return 0

        # Option A: Exclude non-EA summary header rows
        records = [r for r in records if not is_summary_header_record(r)]
        if not records:
            return 0

        # Compute Recoded Enumeration Area (Column 22) per Barangay
        compute_recoded_enumeration_areas(records)

        # Audit consistency flags (FLAG-01 to FLAG-05)
        audit_matched_flags(records)

        cols = [
            "geocode", "geographic_name", "geographic_level", "region_code", "province_code",
            "city_mun_code", "barangay_code", "ean", "no_of_eas", "areaname",
            "household_population", "no_of_households", "ea_subject_for_delineation",
            "ea_subject_for_merging", "popcen_remarks",
            "new_enumeration_area_code", "household_count", "building_count",
            "ea_type", "source_year", "earf_remarks", "recoded_ean",
            "match_status", "in_popcen", "in_earf", "in_gpkg",
            "popcen_households", "earf_households", "household_diff",
            "earf_household_count", "earf_building_count",
            "popcen_delineation", "popcen_merging", "popcen_gap_overlap",
            "new_ean", "is_reset", "ea_lifecycle", "remarks",
            "gpkg_layers", "has_geometry", "geom_type",
            "gpkg_hh_count", "gpkg_bldg_count",
            "validation_flags", "flag_notes"
        ]

        insert_sql = f"""
        INSERT INTO matched_records ({', '.join(cols)})
        VALUES ({', '.join(['?'] * len(cols))})
        """

        with self._get_connection() as conn:
            cur = conn.cursor()
            if replace:
                cur.execute("DELETE FROM matched_records")
                conn.commit()

            batch_size = 5000
            total = len(records)
            for i in range(0, total, batch_size):
                batch = records[i : i + batch_size]
                rows_data = []
                for r in batch:
                    row_vals = []
                    for c in cols:
                        v = r.get(c, "")
                        if (v is None or v == "") and c == "new_enumeration_area_code":
                            v = r.get("new_ean", "")
                        elif (v is None or v == "") and c == "new_ean":
                            v = r.get("new_enumeration_area_code", "")
                        elif (v is None or v == "") and c == "household_count":
                            v = r.get("earf_household_count") or r.get("earf_households", "")
                        elif (v is None or v == "") and c == "earf_household_count":
                            v = r.get("household_count", "")
                        elif (v is None or v == "") and c == "building_count":
                            v = r.get("earf_building_count", "")
                        elif (v is None or v == "") and c == "earf_building_count":
                            v = r.get("building_count", "")
                        elif (v is None or v == "") and c == "popcen_remarks":
                            v = r.get("remarks", "")
                        elif (v is None or v == "") and c == "earf_remarks":
                            v = r.get("remarks", "")
                        elif (v is None or v == "") and c == "no_of_households":
                            v = r.get("popcen_households", "")
                        elif (v is None or v == "") and c == "ea_subject_for_delineation":
                            v = r.get("popcen_delineation", "")
                        elif (v is None or v == "") and c == "ea_subject_for_merging":
                            v = r.get("popcen_merging", "")

                        if v is None or (isinstance(v, float) and pd.isna(v)):
                            v = ""
                        elif isinstance(v, (int, float)):
                            v = str(v)
                            if v.endswith(".0"):
                                v = v[:-2]
                        row_vals.append(v)
                    rows_data.append(row_vals)

                cur.executemany(insert_sql, rows_data)
                conn.commit()
                if progress_callback:
                    pct = int(((i + len(batch)) / total) * 100)
                    progress_callback(pct, f"Saving matched records ({i + len(batch):,} / {total:,})...")

        return len(records)

    def get_uploaded_citymuns(self) -> List[Dict[str, str]]:
        """
        Returns a sorted list of unique city/municipalities uploaded in the database
        (from earf_records and gpkg_records).
        Each entry has 'code' (5-digit code), 'name' (e.g. 'Bugasong'), and 'display' ('00604 - Bugasong').
        """
        codes = set()
        if self.is_earf_populated():
            with self._get_earf_connection() as conn:
                cur = conn.cursor()
                cur.execute("""
                    SELECT DISTINCT SUBSTR(geocode, 1, 5) 
                    FROM earf_records 
                    WHERE geocode IS NOT NULL 
                      AND LENGTH(geocode) = 14 
                      AND SUBSTR(geocode, 4, 2) != '00'
                """)
                for r in cur.fetchall():
                    c = str(r[0]).strip()
                    if len(c) == 5 and not c.endswith("00"):
                        codes.add(c)

        if self.is_gpkg_populated():
            with self._get_gpkg_connection() as conn:
                cur = conn.cursor()
                cur.execute("""
                    SELECT DISTINCT SUBSTR(geocode, 1, 5) 
                    FROM gpkg_records 
                    WHERE geocode IS NOT NULL 
                      AND LENGTH(geocode) = 14 
                      AND SUBSTR(geocode, 4, 2) != '00'
                """)
                for r in cur.fetchall():
                    c = str(r[0]).strip()
                    if len(c) == 5 and not c.endswith("00"):
                        codes.add(c)

        if not codes:
            with self._get_connection() as conn:
                cur = conn.cursor()
                cur.execute("""
                    SELECT DISTINCT SUBSTR(geocode, 1, 5) 
                    FROM matched_records 
                    WHERE geocode IS NOT NULL 
                      AND LENGTH(geocode) >= 5 
                      AND SUBSTR(geocode, 4, 2) != '00'
                """)
                for r in cur.fetchall():
                    c = str(r[0]).strip()
                    if len(c) == 5 and not c.endswith("00"):
                        codes.add(c)

        if not codes:
            return []

        names_map = {}
        with self._get_connection() as conn:
            cur = conn.cursor()
            placeholders = ','.join('?' for _ in codes)
            cur.execute(f"""
                SELECT DISTINCT SUBSTR(geocode, 1, 5), areaname 
                FROM popcen_records 
                WHERE geographic_level LIKE '%3 - City%' 
                  AND SUBSTR(geocode, 1, 5) IN ({placeholders})
            """, list(codes))
            for c_code, c_name in cur.fetchall():
                names_map[str(c_code).strip()] = str(c_name).strip()

        missing_codes = [c for c in codes if c not in names_map]
        if missing_codes and self.is_gpkg_populated():
            with self._get_gpkg_connection() as conn:
                cur = conn.cursor()
                for c in missing_codes:
                    cur.execute("""
                        SELECT city_mun, province 
                        FROM gpkg_records 
                        WHERE SUBSTR(geocode, 1, 5) = ? 
                          AND city_mun IS NOT NULL AND city_mun != '' 
                        LIMIT 1
                    """, (c,))
                    row = cur.fetchone()
                    if row and row[0]:
                        names_map[c] = str(row[0]).strip()

        missing_codes = [c for c in codes if c not in names_map]
        if missing_codes and self.is_earf_populated():
            with self._get_earf_connection() as conn:
                cur = conn.cursor()
                for c in missing_codes:
                    cur.execute("""
                        SELECT city_mun, province 
                        FROM earf_records 
                        WHERE SUBSTR(geocode, 1, 5) = ? 
                          AND city_mun IS NOT NULL AND city_mun != '' 
                        LIMIT 1
                    """, (c,))
                    row = cur.fetchone()
                    if row and row[0]:
                        names_map[c] = str(row[0]).strip()

        result = []
        for c in sorted(list(codes)):
            c_name = names_map.get(c, f"City/Mun {c}")
            result.append({
                "code": c,
                "name": c_name,
                "display": f"{c} - {c_name}"
            })
        return result

    def get_processed_citymuns(self) -> List[Dict[str, str]]:
        """
        Returns a sorted list of unique city/municipalities that have already been
        processed/matched in matched_records.
        Each entry has 'code' (5-digit code), 'name' (e.g. 'Bugasong'), and 'display' ('00604 - Bugasong').
        """
        codes = set()
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT DISTINCT SUBSTR(geocode, 1, 5) 
                FROM matched_records 
                WHERE geocode IS NOT NULL 
                  AND LENGTH(geocode) >= 5 
                  AND SUBSTR(geocode, 4, 2) != '00'
            """)
            for r in cur.fetchall():
                c = str(r[0]).strip()
                if len(c) == 5 and not c.endswith("00"):
                    codes.add(c)

        if not codes:
            return []

        names_map = {}
        with self._get_connection() as conn:
            cur = conn.cursor()
            placeholders = ','.join('?' for _ in codes)
            cur.execute(f"""
                SELECT DISTINCT SUBSTR(geocode, 1, 5), areaname 
                FROM popcen_records 
                WHERE geographic_level LIKE '%3 - City%' 
                  AND SUBSTR(geocode, 1, 5) IN ({placeholders})
            """, list(codes))
            for c_code, c_name in cur.fetchall():
                names_map[str(c_code).strip()] = str(c_name).strip()

        missing_codes = [c for c in codes if c not in names_map]
        if missing_codes and self.is_gpkg_populated():
            with self._get_gpkg_connection() as conn:
                cur = conn.cursor()
                for c in missing_codes:
                    cur.execute("""
                        SELECT city_mun, province 
                        FROM gpkg_records 
                        WHERE SUBSTR(geocode, 1, 5) = ? 
                          AND city_mun IS NOT NULL AND city_mun != '' 
                        LIMIT 1
                    """, (c,))
                    row = cur.fetchone()
                    if row and row[0]:
                        names_map[c] = str(row[0]).strip()

        missing_codes = [c for c in codes if c not in names_map]
        if missing_codes and self.is_earf_populated():
            with self._get_earf_connection() as conn:
                cur = conn.cursor()
                for c in missing_codes:
                    cur.execute("""
                        SELECT city_mun, province 
                        FROM earf_records 
                        WHERE SUBSTR(geocode, 1, 5) = ? 
                          AND city_mun IS NOT NULL AND city_mun != '' 
                        LIMIT 1
                    """, (c,))
                    row = cur.fetchone()
                    if row and row[0]:
                        names_map[c] = str(row[0]).strip()

        still_missing = [c for c in codes if c not in names_map]
        if still_missing:
            with self._get_connection() as conn:
                cur = conn.cursor()
                for c in still_missing:
                    cur.execute("""
                        SELECT areaname, geographic_name 
                        FROM matched_records 
                        WHERE SUBSTR(geocode, 1, 5) = ? 
                          AND (areaname IS NOT NULL OR geographic_name IS NOT NULL) 
                        LIMIT 1
                    """, (c,))
                    row = cur.fetchone()
                    if row and (row[0] or row[1]):
                        raw_name = str(row[0] or row[1]).strip()
                        if " - EA" in raw_name:
                            raw_name = raw_name.split(" - EA")[0].strip()
                        names_map[c] = raw_name

        result = []
        for c in sorted(list(codes)):
            c_name = names_map.get(c, f"City/Mun {c}")
            result.append({
                "code": c,
                "name": c_name,
                "display": f"{c} - {c_name}"
            })
        return result

    def get_citymun_hierarchy(self, city_mun_code: str) -> Dict[str, str]:
        """
        Resolves the geographic hierarchy (Region, Province, City/Municipality names and codes)
        for a 5-digit city/municipality code across gpkg_records, popcen_records, and earf_records.
        """
        cm_code = str(city_mun_code or "").strip()
        cm_5 = cm_code[:5] if len(cm_code) >= 5 else cm_code
        reg_name, prov_name, mun_name = "", "", ""
        reg_code, prov_code = "", ""

        # 1. Check gpkg_records
        if self.is_gpkg_populated():
            try:
                with self._get_gpkg_connection() as conn:
                    cur = conn.cursor()
                    cur.execute("""
                        SELECT region, province, city_mun 
                        FROM gpkg_records 
                        WHERE (SUBSTR(geocode, 1, 5) = ? OR SUBSTR(geocode, 1, 6) = ?)
                          AND region IS NOT NULL AND region != '' 
                          AND province IS NOT NULL AND province != '' 
                          AND city_mun IS NOT NULL AND city_mun != '' 
                        LIMIT 1
                    """, (cm_5, cm_code))
                    row = cur.fetchone()
                    if row:
                        reg_name = str(row[0] or "").strip()
                        prov_name = str(row[1] or "").strip()
                        mun_name = str(row[2] or "").strip()
            except Exception:
                pass

        # 2. Check popcen_records
        if (not reg_name or not prov_name or not mun_name) and self.is_populated():
            try:
                with self._get_connection() as conn:
                    cur = conn.cursor()
                    cur.execute("""
                        SELECT region_code, province_code, city_mun_code, areaname, geographic_name 
                        FROM popcen_records 
                        WHERE (SUBSTR(geocode, 1, 5) = ? OR SUBSTR(geocode, 1, 6) = ?)
                        LIMIT 1
                    """, (cm_5, cm_code))
                    p_row = cur.fetchone()
                    if p_row:
                        if not mun_name:
                            mun_name = str(p_row[4] or p_row[3] or "").strip()
                        reg_code = str(p_row[0] or "").strip()
                        prov_code = str(p_row[1] or "").strip()
                        if reg_code and not reg_name:
                            cur.execute("SELECT areaname FROM popcen_records WHERE geographic_level LIKE '%1 - Region%' AND region_code = ? LIMIT 1", (reg_code,))
                            r_res = cur.fetchone()
                            if r_res:
                                reg_name = str(r_res[0] or "").strip()
                        if prov_code and not prov_name:
                            cur.execute("SELECT areaname FROM popcen_records WHERE geographic_level LIKE '%2 - Prov%' AND province_code = ? LIMIT 1", (prov_code,))
                            pr_res = cur.fetchone()
                            if pr_res:
                                prov_name = str(pr_res[0] or "").strip()
            except Exception:
                pass

        # 3. Check earf_records
        if (not reg_name or not prov_name or not mun_name) and self.is_earf_populated():
            try:
                with self._get_earf_connection() as conn:
                    cur = conn.cursor()
                    cur.execute("""
                        SELECT region, province, city_mun 
                        FROM earf_records 
                        WHERE (SUBSTR(geocode, 1, 5) = ? OR SUBSTR(geocode, 1, 6) = ?)
                          AND region IS NOT NULL AND region != '' 
                          AND province IS NOT NULL AND province != '' 
                          AND city_mun IS NOT NULL AND city_mun != '' 
                        LIMIT 1
                    """, (cm_5, cm_code))
                    e_row = cur.fetchone()
                    if e_row:
                        if not reg_name: reg_name = str(e_row[0] or "").strip()
                        if not prov_name: prov_name = str(e_row[1] or "").strip()
                        if not mun_name: mun_name = str(e_row[2] or "").strip()
            except Exception:
                pass

        return {
            "region": reg_name,
            "province": prov_name,
            "city_mun": mun_name,
            "region_code": reg_code,
            "province_code": prov_code,
            "city_mun_code": cm_5,
        }

    def resolve_final_export_subfolder(self, root_dir: str, city_mun_code: str) -> Tuple[Optional[str], Dict[str, str]]:
        """
        Locates or constructs the specific City/Municipality subfolder under root_dir
        following the official hierarchy: <root_dir>/<Region>/<Province>/<CityMun>.
        Checks the Region, Province, and CityMun folder names dynamically on disk.
        """
        hier = self.get_citymun_hierarchy(city_mun_code)
        reg_name = hier.get("region", "")
        prov_name = hier.get("province", "")
        mun_name = hier.get("city_mun", "")

        if not root_dir or not os.path.exists(root_dir):
            return None, hier

        matched_reg_dir = None
        try:
            for d in os.listdir(root_dir):
                full_d = os.path.join(root_dir, d)
                if os.path.isdir(full_d) and _match_hier_names(d, reg_name):
                    matched_reg_dir = full_d
                    break
        except Exception:
            pass

        if not matched_reg_dir:
            fallback = os.path.join(root_dir, reg_name or "Region", prov_name or "Province", mun_name or city_mun_code)
            return fallback, hier

        # 1. Try matching Province/District then CityMun
        try:
            for d in os.listdir(matched_reg_dir):
                full_d = os.path.join(matched_reg_dir, d)
                if os.path.isdir(full_d) and _match_hier_names(d, prov_name):
                    for sub_d in os.listdir(full_d):
                        full_sub = os.path.join(full_d, sub_d)
                        if os.path.isdir(full_sub) and _match_hier_names(sub_d, mun_name):
                            return full_sub, hier
        except Exception:
            pass

        # 2. Try matching CityMun anywhere under matched_reg_dir (handles NCR districts like NCR I - NCR V)
        try:
            for d in os.listdir(matched_reg_dir):
                full_d = os.path.join(matched_reg_dir, d)
                if os.path.isdir(full_d):
                    if _match_hier_names(d, mun_name):
                        return full_d, hier
                    for sub_d in os.listdir(full_d):
                        full_sub = os.path.join(full_d, sub_d)
                        if os.path.isdir(full_sub) and _match_hier_names(sub_d, mun_name):
                            return full_sub, hier
        except Exception:
            pass

        fallback = os.path.join(matched_reg_dir, prov_name or "Province", mun_name or city_mun_code)
        return fallback, hier

    def query_matched_records(
        self,
        search: Optional[str] = None,
        status: Optional[str] = None,
        ea_lifecycle: Optional[str] = None,
        flag_filter: Optional[str] = None,
        discrepancy_only: bool = False,
        city_mun: Optional[str] = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """
        Queries matched reconciliation records with search, filters, pagination, and total count.
        """
        conditions = []
        params = []

        if search and search.strip():
            s = f"%{search.strip().lower()}%"
            conditions.append("""(
                LOWER(areaname) LIKE ? OR
                LOWER(geographic_name) LIKE ? OR
                geocode LIKE ? OR
                ean LIKE ? OR
                new_ean LIKE ? OR
                new_enumeration_area_code LIKE ? OR
                recoded_ean LIKE ? OR
                LOWER(ea_type) LIKE ? OR
                LOWER(remarks) LIKE ? OR
                LOWER(popcen_remarks) LIKE ? OR
                LOWER(earf_remarks) LIKE ?
            )""")
            params.extend([s] * 11)

        if status and status != "All Records":
            if status == "Discrepancies Only":
                conditions.append("match_status != 'MATCHED_ALL'")
            else:
                conditions.append("match_status = ?")
                params.append(status)

        # Option A: Exclude non-EA summary header rows
        conditions.append("NOT (geocode LIKE '%00000000' OR (geocode LIKE '%000000' AND in_popcen = 0 AND has_geometry = 0 AND (new_enumeration_area_code = '' OR new_enumeration_area_code = '000000' OR new_enumeration_area_code IS NULL) AND (household_count = '' OR household_count IS NULL) AND (building_count = '' OR building_count IS NULL)))")

        if ea_lifecycle and ea_lifecycle != "All EA Types":
            if ea_lifecycle == "Retained Only":
                conditions.append("(UPPER(ea_lifecycle) = 'RETAINED' OR UPPER(ea_type) LIKE '%RETAIN%')")
            elif ea_lifecycle == "Reset / Delineated Only":
                conditions.append("(is_reset = 1 OR UPPER(ea_lifecycle) IN ('RESET', 'DELINEATED') OR UPPER(ea_type) LIKE '%DELIN%')")
            elif ea_lifecycle == "Split EAs Only":
                conditions.append("(UPPER(ea_lifecycle) LIKE '%SPLIT%' OR UPPER(ea_type) LIKE '%SPLIT%')")
            elif ea_lifecycle == "Merged EAs Only":
                conditions.append("(UPPER(ea_lifecycle) LIKE '%MERG%' OR UPPER(ea_type) LIKE '%MERG%')")
            else:
                conditions.append("(UPPER(ea_lifecycle) LIKE ? OR UPPER(ea_type) LIKE ?)")
                params.extend([f"%{ea_lifecycle.upper()}%", f"%{ea_lifecycle.upper()}%"])

        if discrepancy_only:
            conditions.append("""(
                match_status != 'MATCHED_ALL' OR
                is_reset = 1 OR
                (household_diff != '' AND household_diff != '0' AND household_diff IS NOT NULL)
            )""")

        if city_mun and city_mun not in ("All Records", "All City/Mun", "All Uploaded City/Mun", ""):
            cm_val = str(city_mun).strip()[:5]
            conditions.append("SUBSTR(geocode, 1, 5) = ?")
            params.append(cm_val)

        if flag_filter and flag_filter != "All Records":
            if flag_filter in ("Any Flag / Issue", "Any Flag", "Has Flag"):
                conditions.append("(validation_flags IS NOT NULL AND validation_flags != '')")
            elif "FLAG-01" in flag_filter:
                conditions.append("validation_flags LIKE '%FLAG-01%'")
            elif "FLAG-02" in flag_filter:
                conditions.append("validation_flags LIKE '%FLAG-02%'")
            elif "FLAG-03" in flag_filter:
                conditions.append("validation_flags LIKE '%FLAG-03%'")
            elif "FLAG-04" in flag_filter:
                conditions.append("validation_flags LIKE '%FLAG-04%'")
            elif "FLAG-05" in flag_filter:
                conditions.append("validation_flags LIKE '%FLAG-05%'")
            else:
                conditions.append("validation_flags LIKE ?")
                params.append(f"%{flag_filter.strip()}%")

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        count_sql = f"SELECT COUNT(*) FROM matched_records {where_clause}"
        query_sql = f"""
        SELECT * FROM matched_records
        {where_clause}
        ORDER BY geocode ASC, id ASC
        LIMIT ? OFFSET ?
        """

        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(count_sql, params)
            total_filtered = cur.fetchone()[0]

            query_params = list(params) + [limit, offset]
            cur.execute(query_sql, query_params)
            rows = [dict(r) for r in cur.fetchall()]

        return rows, total_filtered

    def get_matched_kpis(self, city_mun: Optional[str] = None) -> Dict[str, Any]:
        """Calculates KPI summary metrics for the Geocode Matching workspace."""
        kpis = {
            "total_geocodes": 0,
            "matched_all": 0,
            "popcen_gpkg": 0,
            "popcen_earf": 0,
            "gpkg_earf": 0,
            "single_source": 0,
            "reset_eas": 0,
            "hh_diffs": 0,
            "with_geom": 0,
        }

        summary_filter = "NOT (geocode LIKE '%00000000' OR (geocode LIKE '%000000' AND in_popcen = 0 AND has_geometry = 0 AND (new_enumeration_area_code = '' OR new_enumeration_area_code = '000000' OR new_enumeration_area_code IS NULL) AND (household_count = '' OR household_count IS NULL) AND (building_count = '' OR building_count IS NULL)))"
        where_cm = f"WHERE {summary_filter}"
        params_cm = []
        if city_mun and city_mun not in ("All Records", "All City/Mun", "All Uploaded City/Mun", ""):
            cm_val = str(city_mun).strip()[:5]
            where_cm = f"WHERE SUBSTR(geocode, 1, 5) = ? AND {summary_filter}"
            params_cm = [cm_val]

        def _and(expr):
            if where_cm:
                return f"{where_cm} AND ({expr})"
            return f"WHERE {expr}"

        try:
            with self._get_connection() as conn:
                cur = conn.cursor()
                cur.execute(f"SELECT COUNT(*) FROM matched_records {where_cm}", params_cm)
                kpis["total_geocodes"] = cur.fetchone()[0]

                cur.execute(f"SELECT COUNT(*) FROM matched_records {_and("match_status = 'MATCHED_ALL'")}", params_cm)
                kpis["matched_all"] = cur.fetchone()[0]

                cur.execute(f"SELECT COUNT(*) FROM matched_records {_and("match_status = 'UNMATCHED_POPCEN_GPKG'")}", params_cm)
                kpis["popcen_gpkg"] = cur.fetchone()[0]

                cur.execute(f"SELECT COUNT(*) FROM matched_records {_and("match_status = 'UNMATCHED_POPCEN_EARF'")}", params_cm)
                kpis["popcen_earf"] = cur.fetchone()[0]

                cur.execute(f"SELECT COUNT(*) FROM matched_records {_and("match_status = 'UNMATCHED_GPKG_EARF'")}", params_cm)
                kpis["gpkg_earf"] = cur.fetchone()[0]

                cur.execute(f"""
                SELECT COUNT(*) FROM matched_records
                {_and("match_status IN ('UNMATCHED_POPCEN', 'UNMATCHED_GPKG', 'UNMATCHED_EARF')")}
                """, params_cm)
                kpis["single_source"] = cur.fetchone()[0]

                cur.execute(f"""
                SELECT COUNT(*) FROM matched_records
                {_and("is_reset = 1 OR UPPER(ea_lifecycle) IN ('RESET', 'DELINEATED') OR (ean != new_ean AND new_ean != '')")}
                """, params_cm)
                kpis["reset_eas"] = cur.fetchone()[0]

                cur.execute(f"""
                SELECT COUNT(*) FROM matched_records
                {_and("household_diff != '' AND household_diff != '0' AND household_diff IS NOT NULL")}
                """, params_cm)
                kpis["hh_diffs"] = cur.fetchone()[0]

                cur.execute(f"SELECT COUNT(*) FROM matched_records {_and("has_geometry = 1")}", params_cm)
                kpis["with_geom"] = cur.fetchone()[0]
        except Exception:
            pass

        return kpis


    def export_filtered_matched_to_mis_excel(
        self,
        out_excel: str,
        search: Optional[str] = None,
        status: Optional[str] = None,
        ea_lifecycle: Optional[str] = None,
        discrepancy_only: bool = False,
        city_mun: Optional[str] = None,
    ):
        """Exports filtered matched records to official PSA MIS format Excel (.xlsx)."""
        rows, _ = self.query_matched_records(
            search=search,
            status=status,
            ea_lifecycle=ea_lifecycle,
            discrepancy_only=discrepancy_only,
            city_mun=city_mun,
            limit=1000000,
            offset=0,
        )
        if not rows:
            return

        meta = None
        if city_mun and city_mun not in ("All Records", "All City/Mun", "All Uploaded City/Mun", ""):
            meta = self.get_citymun_hierarchy(city_mun)
        export_matched_to_mis_excel(rows, out_excel, metadata=meta)

    def export_filtered_matched_to_csv(
        self,
        out_csv: str,
        search: Optional[str] = None,
        status: Optional[str] = None,
        ea_lifecycle: Optional[str] = None,
        discrepancy_only: bool = False,
        city_mun: Optional[str] = None,
    ):
        """Streams filtered matched records to CSV or formatted Excel (.xlsx)."""
        rows, _ = self.query_matched_records(
            search=search,
            status=status,
            ea_lifecycle=ea_lifecycle,
            discrepancy_only=discrepancy_only,
            city_mun=city_mun,
            limit=1000000,
            offset=0,
        )
        if not rows:
            return

        if str(out_csv).lower().endswith(".xlsx"):
            meta = None
            if city_mun and city_mun not in ("All Records", "All City/Mun", "All Uploaded City/Mun", ""):
                meta = self.get_citymun_hierarchy(city_mun)
            export_matched_to_excel(rows, out_csv, metadata=meta)
            return

        # CSV Export with exact 22 columns
        export_cols = [
            ("geocode", "Geocode"),
            ("geographic_name", "Geographic Name"),
            ("geographic_level", "Geographic Level"),
            ("region_code", "region_code"),
            ("province_code", "province_code"),
            ("city_mun_code", "city_mun_code"),
            ("barangay_code", "barangay_code"),
            ("ean", "ean"),
            ("no_of_eas", "No. of EAs"),
            ("areaname", "Areaname"),
            ("household_population", "Household Population"),
            ("no_of_households", "No. of Households"),
            ("ea_subject_for_delineation", "EA Subject for Delineation"),
            ("ea_subject_for_merging", "EA Subject for Merging"),
            ("popcen_remarks", "Remarks"),
            ("new_enumeration_area_code", "New Enumeration Area Code"),
            ("household_count", "Household Count"),
            ("building_count", "Building Count"),
            ("ea_type", "EA Type"),
            ("source_year", "Source Year"),
            ("earf_remarks", "Remarks"),
            ("recoded_ean", "Recoded Enumeration Area"),
        ]

        headers = [label for _, label in export_cols]
        data = []
        for r in rows:
            is_pop = r.get("in_popcen") in (1, "1", True) and bool(r.get("geographic_name"))
            row_vals = []
            for k, label in export_cols:
                if not is_pop and k in (
                    "geographic_name", "geographic_level", "region_code", "province_code",
                    "city_mun_code", "barangay_code", "ean", "no_of_eas", "areaname",
                    "household_population", "no_of_households", "ea_subject_for_delineation",
                    "ea_subject_for_merging", "popcen_remarks"
                ):
                    v = ""
                else:
                    v = r.get(k, "")
                    if (v is None or v == "") and k == "new_enumeration_area_code":
                        v = r.get("new_ean", "")
                    elif (v is None or v == "") and k == "household_count":
                        v = r.get("earf_household_count") or r.get("earf_households", "")
                    elif (v is None or v == "") and k == "building_count":
                        v = r.get("earf_building_count", "")
                    elif (v is None or v == "") and k == "popcen_remarks" and is_pop:
                        v = r.get("remarks", "")
                    elif (v is None or v == "") and k == "earf_remarks":
                        v = r.get("remarks", "")
                    elif (v is None or v == "") and k == "no_of_households" and is_pop:
                        v = r.get("popcen_households", "")
                    elif (v is None or v == "") and k == "ea_subject_for_delineation" and is_pop:
                        v = r.get("popcen_delineation", "")
                    elif (v is None or v == "") and k == "ea_subject_for_merging" and is_pop:
                        v = r.get("popcen_merging", "")
                if v is None:
                    v = ""
                row_vals.append(v)
            data.append(row_vals)

        df = pd.DataFrame(data, columns=headers)
        df.to_csv(out_csv, index=False, encoding="utf-8")

    @staticmethod
    def is_summary_header_record(r: Dict[str, Any]) -> bool:
        return is_summary_header_record(r)

    @staticmethod
    def compute_recoded_enumeration_areas(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return compute_recoded_enumeration_areas(records)

    @staticmethod
    def audit_matched_flags(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return audit_matched_flags(records)

    def export_output_summary_to_excel(
        self,
        out_excel: str,
        search: Optional[str] = None,
        status: Optional[str] = None,
        ea_lifecycle: Optional[str] = None,
        flag_filter: Optional[str] = None,
        discrepancy_only: bool = False,
        city_mun: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Exports formal 2-sheet Output Summary workbook (.xlsx):
          Sheet 1: Output Summary (Hierarchical breakdown & flag tallies)
          Sheet 2: Validation Flags & Exceptions (Itemized audit register)
        """
        rows, _ = self.query_matched_records(
            search=search,
            status=status,
            ea_lifecycle=ea_lifecycle,
            flag_filter=flag_filter,
            discrepancy_only=discrepancy_only,
            city_mun=city_mun,
            limit=2000000,
            offset=0,
        )
        meta = dict(metadata or {})
        if city_mun and not meta.get("city_mun"):
            meta["city_mun"] = city_mun
        return export_output_summary_to_excel(rows, out_excel, metadata=meta)

    def export_matched_to_gpkg(
        self,
        out_gpkg_path: str,
        search: Optional[str] = None,
        status: Optional[str] = None,
        ea_lifecycle: Optional[str] = None,
        discrepancy_only: bool = False,
        city_mun: Optional[str] = None,
        geometry_sources: Optional[List[Any]] = None,
        layer_name: Optional[str] = None,
        progress_callback=None,
    ) -> Tuple[int, int]:
        """
        Exports filtered matched_records to a GeoPackage vector layer matching
        the exact attribute schema specified in the 2026 EARF specifications:
          fid, ea_geocode, map_uuid, bgy_geocode, province_code, city_mun_code,
          barangay_code, ean, region_code, region, province, city_mun, barangay,
          name, code, correspondence_geocode, remarks, hhcount, bldgcount, sy
        Returns (total_exported_count, features_with_geometry_count).
        """
        rows, _ = self.query_matched_records(
            search=search,
            status=status,
            ea_lifecycle=ea_lifecycle,
            discrepancy_only=discrepancy_only,
            city_mun=city_mun,
            limit=1000000,
            offset=0,
        )
        if not rows:
            return 0, 0

        # Strict constraint: Do not create EAs in the GPKG that only exist in EARF or POPCEN.
        # Retain only physical records that actually belong to the GeoPackage.
        gpkg_rows = [r for r in rows if r.get("in_gpkg") or r.get("has_geometry")]
        if gpkg_rows:
            rows = gpkg_rows

        # Layer name matches filename without extension (e.g. "00604_ea2026"), never "reconciled"
        target_layer_name = layer_name
        if not target_layer_name or target_layer_name in ("reconciled_ea2026", "ea2026"):
            clean_cm = ""
            if city_mun:
                try:
                    _, hier = self.resolve_final_export_subfolder("", city_mun)
                    m_name = hier.get("city_mun")
                    if m_name:
                        c_clean = re.sub(r'\s*\([^)]*\)', '', m_name)
                        clean_cm = re.sub(r'[^a-zA-Z0-9]+', '_', c_clean).strip('_').lower()
                except Exception:
                    pass
            if clean_cm:
                target_layer_name = clean_cm
            else:
                base_f = os.path.splitext(os.path.basename(out_gpkg_path))[0]
                target_layer_name = base_f if base_f else "ea2026" 

        try:
            from .gpkg_manager import export_matched_records_to_gpkg
        except (ImportError, ValueError):
            try:
                from COProcessing.db.gpkg_manager import export_matched_records_to_gpkg
            except (ImportError, ValueError):
                try:
                    from gpkg_manager import export_matched_records_to_gpkg
                except (ImportError, ValueError):
                    from db.gpkg_manager import export_matched_records_to_gpkg

        return export_matched_records_to_gpkg(
            records=rows,
            out_gpkg_path=out_gpkg_path,
            layer_name=target_layer_name,
            crs_auth_id="EPSG:4326",
            geometry_sources=geometry_sources,
            popcen_db_path=self.db_path,
            gpkg_db_path=self.gpkg_db_path,
            progress_callback=progress_callback,
        )

    # ----------------------------------------------------------------------
    # Output Deliverables Monitoring & Verification Engine
    # ----------------------------------------------------------------------
    def get_distinct_regions(self) -> List[Dict[str, str]]:
        """Returns a sorted list of distinct administrative regions with code and name."""
        regions = []
        seen = set()
        if self.is_populated():
            try:
                with self._get_connection() as conn:
                    cur = conn.cursor()
                    cur.execute("""
                        SELECT DISTINCT region_code, areaname 
                        FROM popcen_records 
                        WHERE geographic_level LIKE '%1 - Region%' 
                          AND region_code IS NOT NULL AND region_code != ''
                        ORDER BY region_code
                    """)
                    for r in cur.fetchall():
                        c = str(r[0]).strip().zfill(2)
                        name = str(r[1] or "").strip()
                        if c and c not in seen:
                            seen.add(c)
                            regions.append({
                                "code": c,
                                "name": name,
                                "display": f"{c} - {name}" if name else c
                            })
            except Exception:
                pass
        return regions

    def get_distinct_provinces(self, region_code_or_name: Optional[str] = None) -> List[Dict[str, str]]:
        """Returns a sorted list of provinces, optionally filtered by region code or name."""
        provinces = []
        seen = set()
        if self.is_populated():
            try:
                with self._get_connection() as conn:
                    cur = conn.cursor()
                    query = """
                        SELECT DISTINCT province_code, areaname, region_code 
                        FROM popcen_records 
                        WHERE geographic_level LIKE '%2 - Province%' 
                          AND province_code IS NOT NULL AND province_code != ''
                    """
                    params = []
                    if region_code_or_name:
                        rc = str(region_code_or_name).strip()
                        if len(rc) <= 2 and rc.isdigit():
                            query += " AND region_code = ?"
                            params.append(rc.zfill(2))
                        else:
                            cur.execute("SELECT region_code FROM popcen_records WHERE geographic_level LIKE '%1 - Region%' AND areaname = ? LIMIT 1", (rc,))
                            r_row = cur.fetchone()
                            if r_row and r_row[0]:
                                query += " AND region_code = ?"
                                params.append(str(r_row[0]).strip().zfill(2))
                            else:
                                query += " AND areaname LIKE ?"
                                params.append(f"%{rc}%")

                    query += " ORDER BY areaname"
                    cur.execute(query, params)
                    for r in cur.fetchall():
                        c = str(r[0]).strip().zfill(3)
                        name = str(r[1] or "").strip()
                        reg_c = str(r[2] or "").strip().zfill(2)
                        if c and c not in seen:
                            seen.add(c)
                            provinces.append({
                                "code": c,
                                "name": name,
                                "region_code": reg_c,
                                "display": f"{c} - {name}" if name else c
                            })
            except Exception:
                pass
        return provinces

    def detect_deliverables_for_citymun(
        self,
        root_dir: str,
        city_mun_code: str,
        hier_info: Optional[Dict[str, str]] = None,
        target_folder: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Inspects the file system under root_dir for the three final deliverables of a given municipality:
          1. Output GeoPackage (.gpkg, <pppmm>_ea2026.gpkg)
          2. Final EARF Excel (.xlsx, <pppmm>_final_earf_<citymun>.xlsx)
          3. MIS EARF Excel (.xlsx, <pppmm>_MIS_<citymun>.xlsx)
        """
        import datetime
        if not hier_info:
            hier_info = self.get_citymun_hierarchy(city_mun_code)

        reg_name = hier_info.get("region", "")
        prov_name = hier_info.get("province", "")
        mun_name = hier_info.get("city_mun", "") or city_mun_code
        pppmm = city_mun_code[:5] if len(city_mun_code) >= 5 else city_mun_code
        clean_cm = re.sub(r'\s*\([^)]*\)', '', mun_name)
        clean_cm = re.sub(r'[^a-zA-Z0-9]+', '_', clean_cm).strip('_').lower()
        if not clean_cm:
            clean_cm = "ea2026"

        subfolder = target_folder
        if not subfolder and root_dir and os.path.exists(root_dir):
            subfolder, _ = self.resolve_final_export_subfolder(root_dir, city_mun_code)

        folder_exists = os.path.exists(subfolder) if (subfolder and os.path.isdir(subfolder)) else False

        search_dirs = []
        if folder_exists and subfolder:
            search_dirs.append(subfolder)
        if root_dir and os.path.exists(root_dir) and root_dir not in search_dirs:
            search_dirs.append(root_dir)

        def _fmt_size(b: int) -> str:
            if b <= 0: return "0 B"
            if b < 1024: return f"{b} B"
            if b < 1024 * 1024: return f"{b / 1024:.1f} KB"
            return f"{b / (1024 * 1024):.2f} MB"

        gpkg_data = {"exists": False, "filename": "", "path": "", "size": 0, "size_str": "-", "mtime": 0, "mtime_str": "-", "layer_name": "", "feature_count": 0}
        earf_data = {"exists": False, "filename": "", "path": "", "size": 0, "size_str": "-", "mtime": 0, "mtime_str": "-"}
        mis_data = {"exists": False, "filename": "", "path": "", "size": 0, "size_str": "-", "mtime": 0, "mtime_str": "-"}

        for sdir in search_dirs:
            try:
                entries = os.listdir(sdir)
            except Exception:
                continue

            is_specific = (sdir == subfolder)

            # 1. Output GeoPackage
            if not gpkg_data["exists"]:
                gpkg_cands = [f for f in entries if f.lower().endswith(".gpkg") and not f.startswith("._")]
                best_gpkg = None
                if gpkg_cands:
                    for f in gpkg_cands:
                        if f.startswith(pppmm) and "ea2026" in f.lower():
                            best_gpkg = f; break
                    if not best_gpkg:
                        for f in gpkg_cands:
                            if f.startswith(pppmm) or clean_cm in f.lower():
                                best_gpkg = f; break
                    if not best_gpkg and is_specific:
                        for f in gpkg_cands:
                            if "ea2026" in f.lower() or "reconciled" in f.lower():
                                best_gpkg = f; break
                        if not best_gpkg:
                            best_gpkg = gpkg_cands[0]

                if best_gpkg:
                    p = os.path.join(sdir, best_gpkg)
                    try:
                        st = os.stat(p)
                        dt_str = datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
                        l_name, feat_cnt = "", 0
                        try:
                            c_gpkg = sqlite3.connect(p)
                            cur_g = c_gpkg.cursor()
                            cur_g.execute("SELECT table_name FROM gpkg_contents WHERE data_type = 'features' LIMIT 1")
                            row_g = cur_g.fetchone()
                            if row_g and row_g[0]:
                                l_name = str(row_g[0])
                                cur_g.execute(f'SELECT COUNT(*) FROM "{l_name}"')
                                cnt_r = cur_g.fetchone()
                                if cnt_r: feat_cnt = int(cnt_r[0])
                            c_gpkg.close()
                        except Exception:
                            pass
                        gpkg_data = {
                            "exists": True,
                            "filename": best_gpkg,
                            "path": p,
                            "size": st.st_size,
                            "size_str": _fmt_size(st.st_size),
                            "mtime": st.st_mtime,
                            "mtime_str": dt_str,
                            "layer_name": l_name,
                            "feature_count": feat_cnt,
                        }
                    except Exception:
                        pass

            # 2. Final EARF Excel
            if not earf_data["exists"]:
                excel_cands = [f for f in entries if f.lower().endswith((".xlsx", ".xls")) and not f.startswith("~$") and not f.startswith("._")]
                earf_cands = [f for f in excel_cands if "mis" not in f.lower()]
                best_earf = None
                if earf_cands:
                    for f in earf_cands:
                        if f.startswith(pppmm) and "final_earf" in f.lower():
                            best_earf = f; break
                    if not best_earf:
                        for f in earf_cands:
                            if f.startswith(pppmm) and "ea2026" in f.lower():
                                best_earf = f; break
                    if not best_earf:
                        for f in earf_cands:
                            if "final_earf" in f.lower() and (clean_cm in f.lower() or is_specific):
                                best_earf = f; break
                    if not best_earf and is_specific:
                        for f in earf_cands:
                            if "final" in f.lower():
                                best_earf = f; break
                        if not best_earf:
                            best_earf = earf_cands[0]

                if best_earf:
                    p = os.path.join(sdir, best_earf)
                    try:
                        st = os.stat(p)
                        dt_str = datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
                        earf_data = {
                            "exists": True,
                            "filename": best_earf,
                            "path": p,
                            "size": st.st_size,
                            "size_str": _fmt_size(st.st_size),
                            "mtime": st.st_mtime,
                            "mtime_str": dt_str,
                        }
                    except Exception:
                        pass

            # 3. MIS EARF Excel
            if not mis_data["exists"]:
                excel_cands = [f for f in entries if f.lower().endswith((".xlsx", ".xls")) and not f.startswith("~$") and not f.startswith("._")]
                mis_cands = [f for f in excel_cands if "mis" in f.lower()]
                best_mis = None
                if mis_cands:
                    for f in mis_cands:
                        if f.startswith(pppmm) and "mis" in f.lower():
                            best_mis = f; break
                    if not best_mis:
                        for f in mis_cands:
                            if "mis" in f.lower() and (clean_cm in f.lower() or is_specific):
                                best_mis = f; break
                    if not best_mis and is_specific:
                        best_mis = mis_cands[0]

                if best_mis:
                    p = os.path.join(sdir, best_mis)
                    try:
                        st = os.stat(p)
                        dt_str = datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
                        mis_data = {
                            "exists": True,
                            "filename": best_mis,
                            "path": p,
                            "size": st.st_size,
                            "size_str": _fmt_size(st.st_size),
                            "mtime": st.st_mtime,
                            "mtime_str": dt_str,
                        }
                    except Exception:
                        pass

        files_present = sum([gpkg_data["exists"], earf_data["exists"], mis_data["exists"]])
        if files_present == 3:
            status = "COMPLETE"
        elif files_present > 0:
            status = "PARTIAL"
        else:
            status = "MISSING"

        latest_mtime = max([gpkg_data["mtime"], earf_data["mtime"], mis_data["mtime"]])
        latest_mtime_str = datetime.datetime.fromtimestamp(latest_mtime).strftime("%Y-%m-%d %H:%M") if latest_mtime > 0 else "-"

        # Check if matched records exist for this municipality
        has_matching = False
        try:
            with self._get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT 1 FROM matched_records WHERE SUBSTR(geocode, 1, 5) = ? LIMIT 1", (pppmm,))
                if cur.fetchone():
                    has_matching = True
        except Exception:
            pass

        return {
            "code": pppmm,
            "pppmm": pppmm,
            "name": mun_name,
            "clean_name": clean_cm,
            "province": prov_name,
            "region": reg_name,
            "folder_path": subfolder or "",
            "folder_exists": folder_exists,
            "status": status,
            "completion_count": files_present,
            "completion_ratio": f"{files_present}/3",
            "last_modified": latest_mtime_str,
            "last_modified_ts": latest_mtime,
            "has_matching_data": has_matching,
            "gpkg": gpkg_data,
            "final_earf": earf_data,
            "mis_earf": mis_data,
        }

    def find_last_subfolders_in_root(self, root_dir: str) -> List[Tuple[str, List[str]]]:
        """
        Finds all 'last subfolders' (leaf directories) in the root directory.
        Each last subfolder corresponds to one municipality folder.
        Returns a list of tuples: (subfolder_path, list_of_filenames).
        """
        if not root_dir or not os.path.exists(root_dir) or not os.path.isdir(root_dir):
            return []

        leaf_dirs = []
        for dirpath, dirnames, filenames in os.walk(root_dir):
            if os.path.abspath(dirpath) == os.path.abspath(root_dir):
                continue

            valid_subdirs = [d for d in dirnames if not d.startswith('.') and not d.startswith('_')]
            has_deliverables = any(
                f.lower().endswith((".gpkg", ".xlsx", ".xls")) and not f.startswith("~$") and not f.startswith("._")
                for f in filenames
            )

            # Last subfolder condition: no subdirectories or contains deliverables
            if not valid_subdirs or has_deliverables:
                leaf_dirs.append((dirpath, filenames))

        # Keep only the deepest leaf directories (no ancestor directories)
        clean_leafs = []
        abs_paths = [os.path.abspath(p[0]) for p in leaf_dirs]
        for idx, (path, files) in enumerate(leaf_dirs):
            p_abs = abs_paths[idx]
            is_ancestor = any(
                other_p != p_abs and other_p.startswith(p_abs + os.sep)
                for other_p in abs_paths
            )
            if not is_ancestor:
                clean_leafs.append((path, files))

        return clean_leafs

    def scan_output_deliverables(
        self,
        root_dir: str,
        scope: str = "subfolders",
        region_filter: Optional[str] = None,
        province_filter: Optional[str] = None,
        search: Optional[str] = None,
        status_filter: Optional[str] = None,
        progress_callback: Optional[Callable[[int, str], None]] = None
    ) -> Dict[str, Any]:
        """
        Scans all output deliverables (GPKG, Final EARF, MIS EARF) under root_dir.
        Checks the last subfolder in the root directory for the count of total municipalities.
        Supports scopes:
          - 'subfolders' / 'disk': counts and inspects the last subfolders (leaf municipal folders) in root_dir.
          - 'uploaded': batch uploaded municipalities in current session.
          - 'processed': matched municipalities.
          - 'all': nationwide POPCEN municipalities.
        """
        # If root_dir is unreachable (e.g. disconnected network drive), return cached records to preserve deliverables state
        if root_dir and not os.path.exists(root_dir):
            saved = self.get_saved_monitoring_records()
            if saved:
                comp = sum(1 for r in saved if r.get("status") == "COMPLETE")
                part = sum(1 for r in saved if r.get("status") == "PARTIAL")
                miss = sum(1 for r in saved if r.get("status") == "MISSING")
                gpkg_cnt = sum(1 for r in saved if r.get("gpkg", {}).get("exists"))
                earf_cnt = sum(1 for r in saved if r.get("final_earf", {}).get("exists"))
                mis_cnt = sum(1 for r in saved if r.get("mis_earf", {}).get("exists"))
                pct_comp = round((comp / 1655 * 100), 1)
                return {
                    "root_dir": root_dir,
                    "total": 1655,
                    "total_municipalities": 1655,
                    "complete": comp,
                    "complete_count": comp,
                    "partial": part,
                    "partial_count": part,
                    "missing": miss,
                    "missing_count": miss,
                    "gpkg_ready": gpkg_cnt,
                    "final_earf_ready": earf_cnt,
                    "mis_earf_ready": mis_cnt,
                    "completion_percentage": pct_comp,
                    "records": saved,
                }

        candidates = []  # list of (c_code, mun_name, target_folder)
        seen_folders = set()
        seen_codes = set()

        # Pre-fetch lookup from popcen_records for matching and hierarchy cache
        lookup_list = []
        hier_map = {}
        try:
            with self._get_connection() as conn:
                cur = conn.cursor()
                cur.execute("""
                    SELECT DISTINCT SUBSTR(geocode, 1, 5), areaname, province_code, region_code 
                    FROM popcen_records 
                    WHERE geographic_level LIKE '%3 - City%' AND geocode IS NOT NULL
                    ORDER BY areaname
                """)
                for r in cur.fetchall():
                    lookup_list.append((str(r[0]).strip(), str(r[1]).strip(), str(r[2]).strip(), str(r[3]).strip()))

                cur.execute("SELECT DISTINCT province_code, areaname FROM popcen_records WHERE geographic_level LIKE '%2 - Prov%'")
                prov_map = {str(r[0]).strip(): str(r[1]).strip() for r in cur.fetchall()}

                cur.execute("SELECT DISTINCT region_code, areaname FROM popcen_records WHERE geographic_level LIKE '%1 - Reg%'")
                reg_map = {str(r[0]).strip(): str(r[1]).strip() for r in cur.fetchall()}

            for code, name, pcode, rcode in lookup_list:
                hier_map[code] = {
                    "city_mun": name,
                    "province": prov_map.get(pcode, ""),
                    "region": reg_map.get(rcode, ""),
                    "pppmm": code[:5] if len(code) >= 5 else code,
                }
        except Exception:
            pass

        # Check last subfolders in root_dir
        leafs = self.find_last_subfolders_in_root(root_dir) if (root_dir and os.path.exists(root_dir)) else []

        if scope in ("subfolders", "disk", "root") or (scope == "uploaded" and leafs):
            for folder_path, files in leafs:
                norm_folder = os.path.normpath(folder_path)
                if norm_folder in seen_folders:
                    continue
                seen_folders.add(norm_folder)

                mun_folder = os.path.basename(folder_path)
                parent_dir = os.path.dirname(folder_path)
                prov_folder = os.path.basename(parent_dir) if parent_dir != root_dir else ""
                grandparent_dir = os.path.dirname(parent_dir) if parent_dir != root_dir else ""
                reg_folder = os.path.basename(grandparent_dir) if grandparent_dir != root_dir else ""

                c_code = ""
                pppmm = ""
                # 1. Check for 5-digit code in file names
                for f in files:
                    m = re.match(r'^(\d{5})_', f)
                    if m:
                        pppmm = m.group(1)
                        c_code = pppmm
                        break

                # 2. Match folder name + province against database
                if not c_code and lookup_list:
                    for code, aname, pcode, rcode in lookup_list:
                        if _match_hier_names(mun_folder, aname):
                            if prov_folder:
                                hier_chk = hier_map.get(code) or self.get_citymun_hierarchy(code)
                                if _match_hier_names(prov_folder, hier_chk.get("province", "")):
                                    c_code = code[:5]
                                    pppmm = c_code
                                    break
                            else:
                                c_code = code[:5]
                                pppmm = c_code
                                break
                    if not c_code:
                        for code, aname, pcode, rcode in lookup_list:
                            if _match_hier_names(mun_folder, aname):
                                c_code = code[:5]
                                pppmm = c_code
                                break

                if c_code:
                    seen_codes.add(c_code)

                candidates.append((c_code or mun_folder, mun_folder, folder_path))

            # Ensure the full nationwide universe of 1,655 cities/municipalities is included
            # so the Total Cities/Municipalities count is fixed at 1,655 as required.
            if lookup_list:
                for code, aname, pcode, rcode in lookup_list:
                    c5 = code[:5]
                    if c5 not in seen_codes:
                        seen_codes.add(c5)
                        candidates.append((c5, aname, None))

        elif scope == "processed":
            raw_list = self.get_processed_citymuns()
            for item in raw_list:
                c = item["code"][:5]
                if c not in seen_codes:
                    seen_codes.add(c)
                    candidates.append((c, item.get("name", ""), None))
        elif scope == "all":
            if lookup_list:
                for code, aname, pcode, rcode in lookup_list:
                    c5 = code[:5]
                    if c5 not in seen_codes:
                        seen_codes.add(c5)
                        candidates.append((c5, aname, None))
            else:
                try:
                    with self._get_connection() as conn:
                        cur = conn.cursor()
                        cur.execute("""
                            SELECT DISTINCT SUBSTR(geocode, 1, 5), areaname 
                            FROM popcen_records 
                            WHERE geographic_level LIKE '%3 - City%' AND geocode IS NOT NULL
                            ORDER BY areaname
                        """)
                        for r in cur.fetchall():
                            c = str(r[0]).strip()[:5]
                            if c and c not in seen_codes:
                                seen_codes.add(c)
                                candidates.append((c, str(r[1]).strip(), None))
                except Exception:
                    pass
        else: # uploaded and no subfolders found on disk
            raw_list = self.get_uploaded_citymuns()
            for item in raw_list:
                c = item["code"][:5]
                if c not in seen_codes:
                    seen_codes.add(c)
                    candidates.append((c, item.get("name", ""), None))

        total_candidates = len(candidates)
        all_records = []

        for idx, (c_code, mun_name, target_folder) in enumerate(candidates, start=1):
            if progress_callback and total_candidates > 0 and (idx % 25 == 0 or idx == total_candidates):
                pct = int((idx / total_candidates) * 100)
                if progress_callback(pct, f"Checking municipality {idx}/{total_candidates} ({mun_name or c_code})...") is False:
                    break

            hier = hier_map.get(c_code, {})
            if not hier:
                hier = self.get_citymun_hierarchy(c_code) if (c_code and len(c_code) in (5, 6) and c_code.isdigit()) else {}
            if mun_name and not hier.get("city_mun"):
                hier["city_mun"] = mun_name
            if target_folder:
                p_dir = os.path.dirname(target_folder)
                gp_dir = os.path.dirname(p_dir)
                if not hier.get("province") and p_dir != root_dir:
                    hier["province"] = os.path.basename(p_dir)
                if not hier.get("region") and gp_dir != root_dir:
                    hier["region"] = os.path.basename(gp_dir)

            if target_folder:
                rec = self.detect_deliverables_for_citymun(root_dir, c_code, hier, target_folder=target_folder)
                rec["folder_path"] = target_folder
                rec["folder_exists"] = True
            else:
                # Fast construction for municipalities not present on disk
                clean_cm = re.sub(r'\s*\([^)]*\)', '', hier.get("city_mun") or mun_name or c_code)
                clean_cm = re.sub(r'[^a-zA-Z0-9]+', '_', clean_cm).strip('_').lower() or "ea2026"
                pppmm = c_code[:5] if len(c_code) >= 5 else c_code
                rec = {
                    "code": pppmm,
                    "pppmm": pppmm,
                    "name": hier.get("city_mun") or mun_name or c_code,
                    "clean_name": clean_cm,
                    "province": hier.get("province", ""),
                    "region": hier.get("region", ""),
                    "folder_path": "",
                    "folder_exists": False,
                    "gpkg": {"exists": False, "filename": "", "path": "", "size": 0, "size_str": "-", "mtime": 0, "mtime_str": "-", "layer_name": "", "feature_count": 0},
                    "final_earf": {"exists": False, "filename": "", "path": "", "size": 0, "size_str": "-", "mtime": 0, "mtime_str": "-"},
                    "mis_earf": {"exists": False, "filename": "", "path": "", "size": 0, "size_str": "-", "mtime": 0, "mtime_str": "-"},
                    "status": "MISSING",
                    "completion_ratio": "0/3",
                    "last_modified": 0,
                    "last_modified_str": "-",
                    "has_matching_data": False,
                }

            # Apply region filter if specified
            if region_filter and region_filter != "All Regions":
                rf_clean = region_filter.strip().lower()
                r_code = rf_clean[:2] if len(rf_clean) >= 2 and rf_clean[:2].isdigit() else ""
                rec_r = (rec["region"] or "").lower()
                if r_code and r_code not in rec_r and rf_clean not in rec_r:
                    continue
                elif not r_code and rf_clean not in rec_r:
                    continue

            # Apply province filter if specified
            if province_filter and province_filter != "All Provinces":
                pf_clean = province_filter.strip().lower()
                p_code = pf_clean[:3] if len(pf_clean) >= 3 and pf_clean[:3].isdigit() else ""
                rec_p = (rec["province"] or "").lower()
                if p_code and p_code not in rec_p and pf_clean not in rec_p:
                    continue
                elif not p_code and pf_clean not in rec_p:
                    continue

            # Apply search filter if specified
            if search:
                s_lower = search.strip().lower()
                searchable = f"{rec['code']} {rec['pppmm']} {rec['name']} {rec['province']} {rec['region']} {rec['gpkg']['filename']} {rec['final_earf']['filename']} {rec['mis_earf']['filename']}".lower()
                if s_lower not in searchable:
                    continue

            # Apply status filter if specified
            if status_filter and status_filter != "All Statuses":
                sf = status_filter.strip()
                if sf == "Complete (3/3)" and rec["status"] != "COMPLETE":
                    continue
                elif sf == "Partial (1-2/3)" and rec["status"] != "PARTIAL":
                    continue
                elif sf == "Missing All (0/3)" and rec["status"] != "MISSING":
                    continue
                elif sf == "Missing GPKG" and rec["gpkg"]["exists"]:
                    continue
                elif sf == "Missing Final EARF" and rec["final_earf"]["exists"]:
                    continue
                elif sf == "Missing MIS EARF" and rec["mis_earf"]["exists"]:
                    continue

            all_records.append(rec)

        all_records.sort(key=lambda r: (r["region"] or "", r["province"] or "", r["name"] or ""))

        FIXED_TOTAL_CITIES_MUN = 1655
        tot = len(all_records)
        comp = sum(1 for r in all_records if r["status"] == "COMPLETE")
        part = sum(1 for r in all_records if r["status"] == "PARTIAL")
        miss = sum(1 for r in all_records if r["status"] == "MISSING")
        gpkg_cnt = sum(1 for r in all_records if r["gpkg"]["exists"])
        earf_cnt = sum(1 for r in all_records if r["final_earf"]["exists"])
        mis_cnt = sum(1 for r in all_records if r["mis_earf"]["exists"])
        pct_comp = round((comp / FIXED_TOTAL_CITIES_MUN * 100), 1)

        # Persist scan results in SQLite so the dashboard keeps the records across sessions
        try:
            self.save_monitoring_records(all_records, root_dir)
        except Exception as e_save:
            print(f"Warning: Failed to persist dashboard monitoring records: {e_save}")

        return {
            "root_dir": root_dir,
            "total": FIXED_TOTAL_CITIES_MUN,
            "total_municipalities": FIXED_TOTAL_CITIES_MUN,
            "complete": comp,
            "complete_count": comp,
            "partial": part,
            "partial_count": part,
            "missing": miss,
            "missing_count": miss,
            "gpkg_ready": gpkg_cnt,
            "final_earf_ready": earf_cnt,
            "mis_earf_ready": mis_cnt,
            "completion_percentage": pct_comp,
            "records": all_records,
        }

    def export_monitoring_report_csv(self, out_csv: str, records: List[Dict[str, Any]]) -> int:
        """Exports the monitoring status of all given records to a CSV report."""
        import csv
        os.makedirs(os.path.dirname(os.path.abspath(out_csv)), exist_ok=True)
        with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow([
                "Region", "Province", "City / Municipality", "PSGC Code", "5-Digit Code",
                "Deliverables Status", "Completion",
                "Output GPKG Status", "Output GPKG File", "Output GPKG Size",
                "Final EARF Status", "Final EARF File", "Final EARF Size",
                "MIS EARF Status", "MIS EARF File", "MIS EARF Size",
                "Subfolder Path", "Folder Exists", "Last Modified"
            ])
            for r in records:
                c5 = str(r.get("pppmm") or r.get("code") or "")[:5]
                writer.writerow([
                    r.get("region", ""),
                    r.get("province", ""),
                    r.get("name", ""),
                    c5,
                    c5,
                    r.get("status", ""),
                    r.get("completion_ratio", ""),
                    "READY" if r["gpkg"]["exists"] else "MISSING",
                    r["gpkg"]["filename"],
                    r["gpkg"]["size_str"],
                    "READY" if r["final_earf"]["exists"] else "MISSING",
                    r["final_earf"]["filename"],
                    r["final_earf"]["size_str"],
                    "READY" if r["mis_earf"]["exists"] else "MISSING",
                    r["mis_earf"]["filename"],
                    r["mis_earf"]["size_str"],
                    r.get("folder_path", ""),
                    "YES" if r.get("folder_exists") else "NO",
                    r.get("last_modified", ""),
                ])
        return len(records)

    def save_monitoring_records(self, records: List[Dict[str, Any]], root_dir: str = "") -> int:
        """Persists monitoring records into SQLite so the dashboard keeps the records across sessions."""
        if not records:
            return 0
        try:
            with self._get_connection() as conn:
                cur = conn.cursor()
                if root_dir:
                    cur.execute(
                        "INSERT OR REPLACE INTO dashboard_settings (key, value) VALUES ('monitoring_root_dir', ?)",
                        (root_dir,)
                    )
                sql = """
                INSERT OR REPLACE INTO dashboard_monitoring_records (
                    city_mun_code, region, province, city_mun, pppmm, status,
                    gpkg_exists, gpkg_filename, gpkg_path, gpkg_size, gpkg_size_str, gpkg_mtime, gpkg_mtime_str, gpkg_feature_count,
                    final_earf_exists, final_earf_filename, final_earf_path, final_earf_size, final_earf_size_str, final_earf_mtime, final_earf_mtime_str,
                    mis_earf_exists, mis_earf_filename, mis_earf_path, mis_earf_size, mis_earf_size_str, mis_earf_mtime, mis_earf_mtime_str,
                    subfolder_path, subfolder_exists, last_modified, last_modified_str, last_scanned
                ) VALUES (
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, CURRENT_TIMESTAMP
                );
                """
                params = []
                for r in records:
                    g = r.get("gpkg", {})
                    fe = r.get("final_earf", {})
                    me = r.get("mis_earf", {})
                    c_code = r.get("code") or r.get("city_mun_code") or ""
                    if not c_code:
                        continue
                    params.append((
                        c_code,
                        r.get("region") or "",
                        r.get("province") or "",
                        r.get("name") or r.get("city_mun") or "",
                        r.get("pppmm") or "",
                        r.get("status") or "MISSING",
                        1 if g.get("exists") else 0,
                        g.get("filename") or "",
                        g.get("path") or "",
                        g.get("size") or 0,
                        g.get("size_str") or "",
                        g.get("mtime") or 0.0,
                        g.get("mtime_str") or "",
                        g.get("feature_count") or 0,
                        1 if fe.get("exists") else 0,
                        fe.get("filename") or "",
                        fe.get("path") or "",
                        fe.get("size") or 0,
                        fe.get("size_str") or "",
                        fe.get("mtime") or 0.0,
                        fe.get("mtime_str") or "",
                        1 if me.get("exists") else 0,
                        me.get("filename") or "",
                        me.get("path") or "",
                        me.get("size") or 0,
                        me.get("size_str") or "",
                        me.get("mtime") or 0.0,
                        me.get("mtime_str") or "",
                        r.get("folder_path") or r.get("subfolder_path") or "",
                        1 if r.get("folder_exists") or r.get("subfolder_exists") else 0,
                        r.get("last_modified") or 0.0,
                        r.get("last_modified_str") or "",
                    ))
                if params:
                    cur.executemany(sql, params)
                    conn.commit()
                return len(params)
        except Exception as e:
            print(f"Error saving monitoring records: {e}")
            return 0

    def get_saved_monitoring_records(self) -> List[Dict[str, Any]]:
        """Loads cached monitoring records from SQLite so dashboard displays instantly without re-scanning."""
        try:
            with self._get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT * FROM dashboard_monitoring_records ORDER BY region, province, city_mun;")
                rows = cur.fetchall()
                records = []
                for row in rows:
                    r_dict = dict(row)
                    c_code = r_dict.get("city_mun_code", "")
                    m_name = r_dict.get("city_mun", "")
                    clean_cm = re.sub(r'\s*\([^)]*\)', '', m_name)
                    clean_cm = re.sub(r'[^a-zA-Z0-9]+', '_', clean_cm).strip('_').lower()
                    
                    g_exists = bool(r_dict.get("gpkg_exists"))
                    fe_exists = bool(r_dict.get("final_earf_exists"))
                    me_exists = bool(r_dict.get("mis_earf_exists"))
                    
                    ready_cnt = sum([1 if g_exists else 0, 1 if fe_exists else 0, 1 if me_exists else 0])
                    comp_ratio = f"{ready_cnt}/3"

                    records.append({
                        "code": c_code,
                        "name": m_name,
                        "clean_name": clean_cm,
                        "region": r_dict.get("region", ""),
                        "province": r_dict.get("province", ""),
                        "pppmm": r_dict.get("pppmm", ""),
                        "status": r_dict.get("status", "MISSING"),
                        "completion_ratio": comp_ratio,
                        "has_matching_data": True,
                        "gpkg": {
                            "exists": g_exists,
                            "filename": r_dict.get("gpkg_filename", ""),
                            "path": r_dict.get("gpkg_path", ""),
                            "size": r_dict.get("gpkg_size", 0),
                            "size_str": r_dict.get("gpkg_size_str", ""),
                            "mtime": r_dict.get("gpkg_mtime", 0.0),
                            "mtime_str": r_dict.get("gpkg_mtime_str", ""),
                            "feature_count": r_dict.get("gpkg_feature_count", 0),
                        },
                        "final_earf": {
                            "exists": fe_exists,
                            "filename": r_dict.get("final_earf_filename", ""),
                            "path": r_dict.get("final_earf_path", ""),
                            "size": r_dict.get("final_earf_size", 0),
                            "size_str": r_dict.get("final_earf_size_str", ""),
                            "mtime": r_dict.get("final_earf_mtime", 0.0),
                            "mtime_str": r_dict.get("final_earf_mtime_str", ""),
                        },
                        "mis_earf": {
                            "exists": me_exists,
                            "filename": r_dict.get("mis_earf_filename", ""),
                            "path": r_dict.get("mis_earf_path", ""),
                            "size": r_dict.get("mis_earf_size", 0),
                            "size_str": r_dict.get("mis_earf_size_str", ""),
                            "mtime": r_dict.get("mis_earf_mtime", 0.0),
                            "mtime_str": r_dict.get("mis_earf_mtime_str", ""),
                        },
                        "folder_path": r_dict.get("subfolder_path", ""),
                        "subfolder_path": r_dict.get("subfolder_path", ""),
                        "folder_exists": bool(r_dict.get("subfolder_exists")),
                        "subfolder_exists": bool(r_dict.get("subfolder_exists")),
                        "last_modified": r_dict.get("last_modified", 0.0),
                        "last_modified_str": r_dict.get("last_modified_str", ""),
                        "last_scanned": r_dict.get("last_scanned", ""),
                    })
                return records
        except Exception as e:
            print(f"Error loading saved monitoring records: {e}")
            return []

    def get_saved_monitoring_root_dir(self) -> str:
        """Retrieves the previously saved monitoring root directory."""
        try:
            with self._get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT value FROM dashboard_settings WHERE key = 'monitoring_root_dir';")
                row = cur.fetchone()
                return row[0] if row and row[0] else ""
        except Exception:
            return ""

    def update_single_municipality_deliverables(self, root_dir: str, city_mun_code: str) -> Optional[Dict[str, Any]]:
        """Re-checks deliverables for a single municipality on disk and updates the SQLite cache."""
        if not city_mun_code:
            return None
        hier = self.get_citymun_hierarchy(city_mun_code)
        rec = self.detect_deliverables_for_citymun(
            root_dir=root_dir,
            city_mun_code=city_mun_code,
            hier_info=hier
        )
        if rec:
            self.save_monitoring_records([rec], root_dir)
        return rec

    def store_qa_result(self, city_mun_code: str, summary: Dict[str, Any]):
        """Persists the QA audit summary for a municipality into SQLite."""
        if not city_mun_code or not summary:
            return
        import json
        raw_code = str(city_mun_code).strip()
        c_code = raw_code[:5] if len(raw_code) >= 5 else raw_code
        overall_status = str(summary.get("overall_status", "UNCHECKED"))
        score = float(summary.get("compliance_score", 0.0))
        tot_rules = int(summary.get("total_rules", 0))
        passed = int(summary.get("passed_count", 0))
        warnings = int(summary.get("warning_count", 0))
        failures = int(summary.get("failure_count", 0))
        feat_cnt = int(summary.get("feature_count", 0))
        disc_cnt = len(summary.get("discrepancies", []))
        summary_json = json.dumps(summary)

        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
            INSERT OR REPLACE INTO deliverables_qa_results (
                city_mun_code, overall_status, compliance_score, total_rules,
                passed_count, warning_count, failure_count, feature_count,
                discrepancies_count, summary_json, evaluated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (c_code, overall_status, score, tot_rules, passed, warnings, failures, feat_cnt, disc_cnt, summary_json))
            conn.commit()

    def get_qa_result(self, city_mun_code: str) -> Optional[Dict[str, Any]]:
        """Retrieves the cached QA audit summary for a municipality."""
        if not city_mun_code:
            return None
        import json
        raw_code = str(city_mun_code).strip()
        c_code = raw_code[:5] if len(raw_code) >= 5 else raw_code
        try:
            with self._get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT summary_json FROM deliverables_qa_results WHERE city_mun_code = ?", (c_code,))
                row = cur.fetchone()
                if row and row[0]:
                    return json.loads(row[0])
        except Exception:
            pass
        return None

    def get_all_qa_results(self) -> Dict[str, Dict[str, Any]]:
        """Retrieves high-level QA results for all municipalities mapped by city_mun_code."""
        results = {}
        try:
            with self._get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT city_mun_code, overall_status, compliance_score, passed_count, warning_count, failure_count, discrepancies_count, evaluated_at FROM deliverables_qa_results")
                for r in cur.fetchall():
                    results[str(r[0])] = {
                        "overall_status": r[1],
                        "compliance_score": r[2],
                        "passed_count": r[3],
                        "warning_count": r[4],
                        "failure_count": r[5],
                        "discrepancies_count": r[6],
                        "evaluated_at": r[7],
                    }
        except Exception:
            pass
        return results




