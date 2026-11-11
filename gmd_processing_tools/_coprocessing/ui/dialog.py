# -*- coding: utf-8 -*-
"""
CO Processing — Main GUI Dialog with EARF Post-Processing & Multi-Source Previews
---------------------------------------------------------------------------------
Features:
  - Unified "EARF Post-Processing" workspace with 3 dedicated sub-tabs:
      1. POPCEN Table (Local SQLite Database `popcen_database.db` with 201k+ records
         + custom Excel/CSV preview with KPI cards, multi-level filters, and pagination)
      2. Preliminary EARF Files (Multi-file folder scanner & batch consolidator into SQLite,
         multi-rule Consistency Check Engine, official 2026 EARF multi-tier headers,
         issue filtering, and report export)
      3. EA GeoPackages (Attribute table inspector matching 01915_ea2026.gpkg schema,
         geometry status inspection, and layer feature preview)
  - Bi-directional synchronization with Processing Inputs & Execution tab.
  - Multi-threaded processing execution with real-time progress bar and activity log.
  - Independent CSV export for each preview source and consistency reports.
"""

from __future__ import annotations

import os
import re
import sys
import time
import sqlite3
import collections
from typing import Tuple, List, Dict, Any, Optional
import pandas as pd

from qgis.core import (
    QgsApplication,
    QgsProject,
    QgsVectorLayer,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import Qt, QThread, pyqtSignal, QRect, QSize
from qgis.PyQt.QtGui import QColor, QFont, QIcon, QPainter, QPen
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QAction,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QCompleter,
    QFileSystemModel,
    QListWidget,
    QListWidgetItem,
)

try:
    from ..core.algorithm import (
        CoProcessingEarfMatch,
        build_geocode,
        read_earf_table,
        POPCEN_FIELDS,
        GPKG_EARF_FIELDS,
        exclude_non_ea_levels,
        to_whole_number_str,
    )
    from ..db.database import (
        POPCENDatabase, find_earf_files_in_folder, find_gpkg_files_in_folder,
        get_gpkg_polygon_layer_info, validate_gpkg_columns, REQUIRED_GPKG_COLUMNS, compute_recoded_enumeration_areas, audit_matched_flags, export_output_summary_to_excel, is_summary_header_record, DEFAULT_FINAL_EXPORT_ROOT, DEFAULT_GPKG_ROOT, DEFAULT_EARF_ROOT
    )
except (ImportError, ValueError):
    from algorithm import (
        CoProcessingEarfMatch,
        build_geocode,
        read_earf_table,
        POPCEN_FIELDS,
        GPKG_EARF_FIELDS,
        exclude_non_ea_levels,
        to_whole_number_str,
    )
    from database import (
        POPCENDatabase, find_earf_files_in_folder, find_gpkg_files_in_folder,
        get_gpkg_polygon_layer_info, validate_gpkg_columns, REQUIRED_GPKG_COLUMNS, compute_recoded_enumeration_areas, audit_matched_flags, export_output_summary_to_excel, is_summary_header_record, DEFAULT_FINAL_EXPORT_ROOT, DEFAULT_GPKG_ROOT, DEFAULT_EARF_ROOT
    )

if "DEFAULT_GPKG_ROOT" not in globals():
    DEFAULT_GPKG_ROOT = r"Y:\geotags-uploads\ea-delineation"

if "DEFAULT_EARF_ROOT" not in globals():
    DEFAULT_EARF_ROOT = r"D:\PSA-GIS\Camarines Sur\Project 1MAP\3_EA Delineation and Merging\1_Downloaded Files\Output\2_output_files"


try:
    from .qa_dialog import DeliverablesReviewDialog
except (ImportError, ValueError):
    try:
        from qa_dialog import DeliverablesReviewDialog
    except Exception:
        DeliverablesReviewDialog = None

try:
    to_whole_number_str
except NameError:
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


# --------------------------------------------------------------------------
# Smart POPCEN Excel Loader
# --------------------------------------------------------------------------
def load_popcen_data(file_path, sheet_name=0):
    """
    Reads a POPCEN Excel/CSV file and auto-detects multi-tier header rows
    (e.g., Row 6 category groups + Row 7 column names).
    """
    if not file_path or not os.path.exists(file_path):
        return pd.DataFrame()

    sheet_arg = sheet_name.strip() if isinstance(sheet_name, str) and sheet_name.strip() else 0
    if file_path.lower().endswith(".csv"):
        df_raw = pd.read_csv(file_path, header=None, nrows=15, dtype=str)
    else:
        df_raw = pd.read_excel(file_path, sheet_name=sheet_arg, header=None, nrows=15, dtype=str)

    header_idx = None
    for idx, row in df_raw.iterrows():
        s = " ".join([str(v).lower() for v in row if pd.notna(v)])
        if "province_code" in s and "barangay_code" in s:
            header_idx = idx
            break

    if header_idx is None:
        header_idx = 0

    cat_row = df_raw.iloc[header_idx - 1] if header_idx > 0 else None
    main_row = df_raw.iloc[header_idx]

    cols = []
    for c_i in range(len(main_row)):
        m_val = str(main_row[c_i]).strip() if pd.notna(main_row[c_i]) else ""
        c_val = str(cat_row[c_i]).strip() if cat_row is not None and pd.notna(cat_row[c_i]) else ""
        if m_val and m_val.lower() != "nan":
            cols.append(m_val)
        elif c_val and c_val.lower() != "nan":
            cols.append(c_val)
        else:
            cols.append(f"Col_{c_i + 1}")

    if file_path.lower().endswith(".csv"):
        df = pd.read_csv(file_path, skiprows=header_idx + 1, header=None, dtype=str)
    else:
        df = pd.read_excel(file_path, sheet_name=sheet_arg, skiprows=header_idx + 1, header=None, dtype=str)

    num_cols = min(len(cols), len(df.columns))
    df = df.iloc[:, :num_cols]
    df.columns = cols[:num_cols]

    # Calculate geocode
    df["geocode"] = df.apply(lambda r: build_geocode(r.to_dict(), POPCEN_FIELDS), axis=1)
    return df


def has_new_ea_value(val) -> bool:
    """Checks if a cell value represents a non-empty New EA Code (2026)."""
    if pd.isna(val):
        return False
    s = str(val).strip()
    return bool(s and s.lower() not in ("nan", "none", "null", "-", ""))


def find_new_ea_code_col(df: pd.DataFrame):
    """Finds the column representing 'New EA Code (2026)' across various header naming conventions."""
    target_candidates = [
        "new_enumeration_area_code",
        "new enumeration area code (2026)",
        "new enumeration area code",
        "new ea code (2026)",
        "new ea code",
        "new_ean",
        "new_ea_code",
        "new_ea",
        "ean_2026",
    ]
    for cand in target_candidates:
        cand_clean = cand.replace("_", " ")
        for col in df.columns:
            c_clean = str(col).strip().lower().replace("\n", " ").replace("_", " ")
            if c_clean == cand_clean:
                return col
    for col in df.columns:
        c_clean = str(col).strip().lower()
        if "new" in c_clean and ("ea" in c_clean or "ean" in c_clean or "code" in c_clean):
            return col
    return None


def find_geographic_level_col(df: pd.DataFrame):
    """Finds the column representing Geographic Level across various naming conventions."""
    for col in df.columns:
        c_clean = str(col).strip().lower().replace("_", " ").replace("\n", " ")
        if "geographic" in c_clean and "level" in c_clean:
            return col
    for col in df.columns:
        c_clean = str(col).strip().lower().replace("_", " ").replace("\n", " ")
        if "geo" in c_clean and "level" in c_clean:
            return col
    for col in df.columns:
        c_clean = str(col).strip().lower()
        if "level" in c_clean:
            return col
    return None


def is_ea_level_value(val) -> bool:
    """Checks if a cell represents a 5 - EA geographic level."""
    if pd.isna(val):
        return False
    s = str(val).strip().upper()
    return "5 - EA" in s or "5-EA" in s or (s.startswith("5") and "EA" in s)



# --------------------------------------------------------------------------
# GeoPackage Feature Loader & Multi-File Consistency Check Engine
# --------------------------------------------------------------------------
def is_version_dir(dir_name: str) -> bool:
    """
    Returns True if a directory name matches version/archive folder naming conventions.
    Matches folder names like 'versions', 'version', 'version_1', 'version 1', 'v1', 'v2', etc.
    """
    name = dir_name.strip().lower()
    if not name:
        return False
    if "version" in name:
        return True
    if re.match(r"^(?:v|ver)[-_ ]?\d+(?:\.\d+)*$", name):
        return True
    return False


def is_polygon_layer(lyr) -> bool:
    """
    Checks if a QGIS layer is a valid vector layer with polygon geometry.
    Returns True for Polygon and MultiPolygon geometry types.
    """
    if not lyr or not hasattr(lyr, "isValid") or not lyr.isValid():
        return False
    if hasattr(lyr, "geometryType"):
        try:
            if lyr.geometryType() == QgsWkbTypes.PolygonGeometry:
                return True
        except Exception:
            pass
    if hasattr(lyr, "wkbType"):
        try:
            wkb = lyr.wkbType()
            if QgsWkbTypes.geometryType(wkb) == QgsWkbTypes.PolygonGeometry:
                return True
            if "polygon" in QgsWkbTypes.displayString(wkb).lower():
                return True
        except Exception:
            pass
    return False


def resolve_gpkg_sources(sources_str):
    """
    Expands string containing file paths, directory paths, or QGIS layer names
    into a deduplicated list of layer names and .gpkg / .sqlite file paths.
    """
    if not sources_str or not sources_str.strip():
        return []
    raw_items = [s.strip().strip('"').strip("'") for s in sources_str.replace("\r", "\n").replace(";", "\n").split("\n") if s.strip()]
    resolved = []
    seen = set()

    for item in raw_items:
        if os.path.isdir(item):
            for root, dirs, files in os.walk(item):
                dirs[:] = [d for d in dirs if not is_version_dir(d)]
                rel_path = os.path.relpath(root, item)
                if rel_path != "." and any(is_version_dir(part) for part in rel_path.replace("\\", "/").split("/")):
                    continue
                for f in sorted(files):
                    if f.lower().endswith(('.gpkg', '.sqlite')):
                        full_p = os.path.normpath(os.path.join(root, f))
                        if full_p.lower() not in seen:
                            seen.add(full_p.lower())
                            resolved.append(full_p)
        elif os.path.isfile(item):
            # Exclude files located inside a version folder
            parts = os.path.normpath(item).replace("\\", "/").split("/")
            if any(is_version_dir(p) for p in parts[:-1]):
                continue
            norm_p = os.path.normpath(item)
            if norm_p.lower() not in seen:
                seen.add(norm_p.lower())
                resolved.append(norm_p)
        else:
            if item.lower() not in seen:
                seen.add(item.lower())
                resolved.append(item)
    return resolved


def analyze_gpkg_consistency(df: pd.DataFrame):
    """
    Performs comprehensive multi-file consistency and duplication analysis on GPKG records.
    Detects cross-file overlaps, intra-file multipart EA fragments, attribute conflicts,
    missing geometries, and invalid geocodes.
    """
    if df.empty:
        summary = {
            "total_features": 0,
            "unique_geocodes": 0,
            "total_duplicates": 0,
            "cross_file_overlaps": 0,
            "multipart_geocodes": 0,
            "attribute_conflicts": 0,
            "missing_geometry": 0,
            "invalid_geocodes": 0,
            "issues": [],
            "status_level": "CLEAN",
        }
        return df, summary

    df = df.copy()
    if "geocode" not in df.columns:
        df["geocode"] = ""

    df["geocode"] = df["geocode"].astype(str).str.strip().replace("nan", "").replace("None", "")

    # Group counts
    gc_counts = df["geocode"].value_counts().to_dict()
    df["dup_count"] = df["geocode"].map(gc_counts).fillna(1).astype(int)

    file_col = "layer_source" if "layer_source" in df.columns else None
    if file_col:
        file_counts = df.groupby("geocode")[file_col].nunique().to_dict()
        file_lists = df.groupby("geocode")[file_col].unique().apply(lambda arr: ", ".join([str(x) for x in arr])).to_dict()
        df["file_count"] = df["geocode"].map(file_counts).fillna(1).astype(int)
        df["source_files"] = df["geocode"].map(file_lists).fillna("")
    else:
        df["file_count"] = 1
        df["source_files"] = ""

    # Attribute conflict detection across duplicate geocodes
    compare_keys = []
    for k in ["ea_type", "type", "region", "province", "city_mun", "barangay", "ean", "hh_count", "bldg_count", "eacount", "hhcount"]:
        found = next((c for c in df.columns if c.strip().lower() == k), None)
        if found and found not in compare_keys:
            compare_keys.append(found)

    conflict_geocodes = set()
    conflict_notes = {}
    if compare_keys:
        for gc, group in df[df["dup_count"] > 1].groupby("geocode"):
            if not gc:
                continue
            mismatches = []
            for col in compare_keys:
                vals = set(group[col].dropna().astype(str).str.strip().unique()) - {"", "nan", "None"}
                if len(vals) > 1:
                    mismatches.append(f"{col}: {list(vals)}")
            if mismatches:
                conflict_geocodes.add(gc)
                conflict_notes[gc] = "; ".join(mismatches)

    statuses = []
    details = []

    for _, row in df.iterrows():
        gc = row["geocode"]
        geom = str(row.get("geom_status", ""))
        cnt = row["dup_count"]
        f_cnt = row["file_count"]
        is_missing_geom = (not geom or "No Geometry" in geom or "none" in geom.lower())
        is_invalid_gc = (not gc or len(gc) < 9 or not gc.isalnum())

        if is_invalid_gc:
            statuses.append("INVALID_GEOCODE")
            details.append("Geocode is empty or malformed (<9 chars)")
        elif is_missing_geom:
            statuses.append("NO_GEOMETRY")
            details.append("Feature lacks valid geometry or has empty polygon")
        elif gc in conflict_geocodes:
            statuses.append("CONFLICTING_DATA")
            details.append(f"Attribute mismatch across duplicates ({conflict_notes.get(gc, '')})")
        elif f_cnt > 1:
            statuses.append("CROSS_FILE_DUP")
            details.append(f"Geocode appears in {f_cnt} separate files: {row.get('source_files', '')}")
        elif cnt > 1:
            statuses.append("MULTIPART_PARTS")
            details.append(f"Appears {cnt} times in same file (multipart EA polygon parts)")
        else:
            statuses.append("UNIQUE")
            details.append("Unique single-part EA record")

    df["consistency_status"] = statuses
    df["consistency_detail"] = details

    total_features = len(df)
    unique_geocodes = df[df["geocode"] != ""]["geocode"].nunique()
    cross_file_overlaps = df[df["consistency_status"].isin(["CROSS_FILE_DUP", "CONFLICTING_DATA"]) & (df["file_count"] > 1)]["geocode"].nunique()
    multipart_geocodes = df[df["consistency_status"] == "MULTIPART_PARTS"]["geocode"].nunique()
    attribute_conflicts = len(conflict_geocodes)
    missing_geom_cnt = int((df["consistency_status"] == "NO_GEOMETRY").sum())
    invalid_gc_cnt = int((df["consistency_status"] == "INVALID_GEOCODE").sum())
    total_dup_rows = int((df["dup_count"] > 1).sum())

    status_level = "CLEAN"
    if cross_file_overlaps > 0 or attribute_conflicts > 0:
        status_level = "OVERLAPS_FOUND"
    elif multipart_geocodes > 0 or missing_geom_cnt > 0 or invalid_gc_cnt > 0:
        status_level = "WARNING"

    issue_rows = df[df["consistency_status"] != "UNIQUE"]
    issue_list = []
    seen_gc_issues = set()
    for _, r in issue_rows.iterrows():
        gc = r["geocode"]
        if gc in seen_gc_issues and gc != "":
            continue
        seen_gc_issues.add(gc)
        issue_list.append({
            "geocode": gc or "(BLANK)",
            "status": r["consistency_status"],
            "dup_count": int(r["dup_count"]),
            "file_count": int(r["file_count"]),
            "source_files": r["source_files"],
            "details": r["consistency_detail"],
        })

    summary = {
        "total_features": total_features,
        "unique_geocodes": unique_geocodes,
        "total_duplicates": total_dup_rows,
        "cross_file_overlaps": cross_file_overlaps,
        "multipart_geocodes": multipart_geocodes,
        "attribute_conflicts": attribute_conflicts,
        "missing_geometry": missing_geom_cnt,
        "invalid_geocodes": invalid_gc_cnt,
        "issues": issue_list,
        "status_level": status_level,
    }
    return df, summary


def load_gpkg_data(sources_str):
    """
    Reads polygon vector layers or GeoPackage files into a unified pandas DataFrame,
    capturing attribute columns, geometry status, and consistency metrics for preview.
    Ensures that only polygon / multipolygon vector layers are loaded.
    """
    if not sources_str or not sources_str.strip():
        return pd.DataFrame()

    items = resolve_gpkg_sources(sources_str)
    all_rows = []

    for item in items:
        # 1. Check if item matches an active QGIS vector layer
        lyr = None
        try:
            for project_layer in QgsProject.instance().mapLayers().values():
                if (project_layer.name() == item or project_layer.id() == item) and isinstance(project_layer, QgsVectorLayer):
                    if is_polygon_layer(project_layer):
                        lyr = project_layer
                        break
        except Exception:
            lyr = None

        if lyr is not None and lyr.isValid():
            is_valid_cols, _, _ = validate_gpkg_columns(lyr)
            if not is_valid_cols:
                continue
            field_names = [f.name() for f in lyr.fields()]
            src_name = lyr.name() or item

            for feat in lyr.getFeatures():
                if not feat.hasGeometry() or feat.geometry().isEmpty():
                    continue
                # Ensure feature geometry is Polygon or MultiPolygon
                if feat.geometry().type() != QgsWkbTypes.PolygonGeometry:
                    continue

                attrs = dict(zip(field_names, feat.attributes()))
                gc = build_geocode(attrs, GPKG_EARF_FIELDS)
                try:
                    geom_str = QgsWkbTypes.displayString(feat.geometry().wkbType())
                except Exception:
                    geom_str = "Polygon"

                row = {k: attrs[k] for k in field_names}
                row["geocode"] = gc
                row["geom_status"] = geom_str
                row["layer_source"] = src_name
                row["source_file"] = src_name
                all_rows.append(row)
            continue

        # 2. Check if item is a GeoPackage file on disk
        if os.path.exists(item) and os.path.isfile(item):
            base_name = os.path.splitext(os.path.basename(item))[0]
            has_poly, poly_tables, desc = get_gpkg_polygon_layer_info(item)

            # Skip files that are confirmed non-polygon (e.g. points, lines only)
            if not has_poly and poly_tables is not None and len(poly_tables) == 0 and "non-polygon" in desc.lower():
                continue

            loaded_any = False

            # If specific polygon sublayers were detected in the GeoPackage
            if poly_tables:
                for tbl_name in poly_tables:
                    sub_lyr = None
                    try:
                        sub_lyr = QgsVectorLayer(f"{item}|layername={tbl_name}", f"{base_name} - {tbl_name}", "ogr")
                    except Exception:
                        sub_lyr = None

                    if sub_lyr is not None and sub_lyr.isValid() and is_polygon_layer(sub_lyr):
                        is_valid_cols, _, _ = validate_gpkg_columns(sub_lyr)
                        if not is_valid_cols:
                            continue
                        field_names = [f.name() for f in sub_lyr.fields()]
                        for feat in sub_lyr.getFeatures():
                            if not feat.hasGeometry() or feat.geometry().isEmpty():
                                continue
                            if feat.geometry().type() != QgsWkbTypes.PolygonGeometry:
                                continue

                            attrs = dict(zip(field_names, feat.attributes()))
                            gc = build_geocode(attrs, GPKG_EARF_FIELDS)
                            try:
                                geom_str = QgsWkbTypes.displayString(feat.geometry().wkbType())
                            except Exception:
                                geom_str = "Polygon"

                            row = {k: attrs[k] for k in field_names}
                            row["geocode"] = gc
                            row["geom_status"] = geom_str
                            row["layer_source"] = f"{base_name} ({tbl_name})"
                            row["source_file"] = os.path.basename(item)
                            all_rows.append(row)
                            loaded_any = True
                        del sub_lyr

            # Fallback to direct layer opening if sublayers were not enumerated
            if not loaded_any:
                try:
                    direct_lyr = QgsVectorLayer(item, base_name, "ogr")
                    if direct_lyr.isValid() and is_polygon_layer(direct_lyr):
                        is_valid_cols, _, _ = validate_gpkg_columns(direct_lyr)
                        if not is_valid_cols:
                            continue
                        field_names = [f.name() for f in direct_lyr.fields()]
                        for feat in direct_lyr.getFeatures():
                            if not feat.hasGeometry() or feat.geometry().isEmpty():
                                continue
                            if feat.geometry().type() != QgsWkbTypes.PolygonGeometry:
                                continue

                            attrs = dict(zip(field_names, feat.attributes()))
                            gc = build_geocode(attrs, GPKG_EARF_FIELDS)
                            try:
                                geom_str = QgsWkbTypes.displayString(feat.geometry().wkbType())
                            except Exception:
                                geom_str = "Polygon"

                            row = {k: attrs[k] for k in field_names}
                            row["geocode"] = gc
                            row["geom_status"] = geom_str
                            row["layer_source"] = base_name
                            row["source_file"] = os.path.basename(item)
                            all_rows.append(row)
                            loaded_any = True
                    del direct_lyr
                except Exception:
                    pass

            # SQLite direct fallback: ONLY select tables with Polygon or MultiPolygon geometry
            if not loaded_any:
                try:
                    conn = sqlite3.connect(item)
                    cur = conn.cursor()
                    cur.execute("""
                        SELECT g.table_name 
                        FROM gpkg_geometry_columns g
                        JOIN gpkg_contents c ON g.table_name = c.table_name
                        WHERE UPPER(g.geometry_type_name) IN ('POLYGON', 'MULTIPOLYGON')
                    """)
                    poly_tables_sql = [r[0] for r in cur.fetchall()]
                    for t in poly_tables_sql:
                        cur.execute(f'PRAGMA table_info("{t}")')
                        cols = [r[1] for r in cur.fetchall()]
                        cur.execute(f'SELECT * FROM "{t}"')
                        for r in cur.fetchall():
                            d = dict(zip(cols, r))
                            d["geom_status"] = "Polygon" if d.get("geom") else "No Geometry"
                            if "geom" in d:
                                del d["geom"]
                            d["layer_source"] = f"{os.path.basename(item)} ({t})"
                            d["source_file"] = os.path.basename(item)
                            d["geocode"] = build_geocode(d, GPKG_EARF_FIELDS)
                            all_rows.append(d)
                    conn.close()
                except Exception:
                    pass

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows)
    if "eacount" not in df.columns:
        df["eacount"] = "1"
    else:
        df["eacount"] = df["eacount"].fillna("1")
    for c in df.columns:
        if c.strip().lower() in ("code", "hhcount", "sy", "hh_count", "bldgcount", "bldg_count", "eacount", "hh count", "bldg count", "ea count"):
            df[c] = df[c].apply(to_whole_number_str)
    df, summary = analyze_gpkg_consistency(df)
    df._consistency_summary = summary
    return df


class GPKGConsistencyDialog(QDialog):
    """
    Detailed modal report displaying multi-file consistency, overlap, and duplication
    findings across all loaded GeoPackages.
    """
    def __init__(self, parent, summary: dict, df: pd.DataFrame):
        super().__init__(parent)
        self.summary = summary
        self.df = df
        self.setWindowTitle("EA GeoPackage Consistency & Duplication Report")
        self.resize(780, 540)
        self.setModal(True)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Header banner
        header = QFrame()
        header.setStyleSheet("background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 10px;")
        h_layout = QVBoxLayout(header)
        h_layout.setContentsMargins(0, 0, 0, 0)
        h_layout.setSpacing(4)

        status_level = self.summary.get("status_level", "CLEAN")
        if status_level == "OVERLAPS_FOUND":
            badge_html = "<span style='background:#fee2e2; color:#b91c1c; font-weight:bold; padding:3px 8px; border-radius:4px;'>WARNING: CROSS-FILE OVERLAPS DETECTED</span>"
            desc_html = f"Detected <b>{self.summary.get('cross_file_overlaps', 0)}</b> geocodes shared across multiple GeoPackage files. Attribute conflicts: <b>{self.summary.get('attribute_conflicts', 0)}</b>."
        elif status_level == "WARNING":
            badge_html = "<span style='background:#fef3c7; color:#b45309; font-weight:bold; padding:3px 8px; border-radius:4px;'>NOTICE: MULTIPART OR GEOMETRY ISSUES</span>"
            desc_html = f"Found <b>{self.summary.get('multipart_geocodes', 0)}</b> multipart EA geocodes. Missing geometries: <b>{self.summary.get('missing_geometry', 0)}</b>."
        else:
            badge_html = "<span style='background:#dcfce7; color:#15803d; font-weight:bold; padding:3px 8px; border-radius:4px;'>PASSED: 100% CONSISTENT</span>"
            desc_html = "All loaded GeoPackage features have unique geocodes with zero cross-file duplicates or geometry errors."

        title_lbl = QLabel(f"<h3 style='margin:0; padding:0;'>EA GeoPackage Duplication Analysis & Consistency Report &nbsp; {badge_html}</h3>")
        desc_lbl = QLabel(f"{desc_html}<br><span style='color:#64748b; font-size:11px;'>Total Features: <b>{self.summary.get('total_features', 0):,}</b> | Unique Geocodes: <b>{self.summary.get('unique_geocodes', 0):,}</b> | Duplicate Records: <b>{self.summary.get('total_duplicates', 0):,}</b></span>")
        desc_lbl.setWordWrap(True)
        h_layout.addWidget(title_lbl)
        h_layout.addWidget(desc_lbl)
        layout.addWidget(header)

        # Notice on deduplication prevention
        notice_frame = QFrame()
        notice_frame.setStyleSheet("background: #eff6ff; border-left: 4px solid #3b82f6; border-radius: 4px; padding: 8px;")
        n_layout = QVBoxLayout(notice_frame)
        n_layout.setContentsMargins(6, 4, 6, 4)
        notice_text = QLabel(
            "<b>Deduplication & Geometry Consolidation:</b><br>"
            "The matching engine automatically consolidates duplicate geocode records into single entries. "
            "Multipart polygons sharing the same 14-digit geocode are unified via spatial union (MultiPolygon) "
            "with primary attributes retained, guaranteeing zero duplicate features in the output GeoPackage."
        )
        notice_text.setWordWrap(True)
        notice_text.setStyleSheet("color: #1e40af; font-size: 11px;")
        n_layout.addWidget(notice_text)
        layout.addWidget(notice_frame)

        # Issues Table
        issues = self.summary.get("issues", [])
        layout.addWidget(QLabel(f"<b>Detected Consistency Findings ({len(issues)} items):</b>"))

        table = QTableWidget()
        table.setAlternatingRowColors(True)
        table.setColumnCount(5)
        table.setHorizontalHeaderLabels(["#", "Geocode", "Status", "Files / Occurrences", "Issue Details"])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)

        if not issues:
            table.setRowCount(1)
            item = QTableWidgetItem("Zero duplicate or consistency issues found across all loaded layers.")
            item.setForeground(QColor("#15803d"))
            table.setItem(0, 4, item)
        else:
            table.setRowCount(len(issues))
            for i, issue in enumerate(issues):
                num_item = QTableWidgetItem(str(i + 1))
                num_item.setTextAlignment(Qt.AlignCenter)
                table.setItem(i, 0, num_item)

                gc_item = QTableWidgetItem(issue.get("geocode", ""))
                font = gc_item.font()
                font.setBold(True)
                gc_item.setFont(font)
                gc_item.setForeground(QColor("#0d9488"))
                table.setItem(i, 1, gc_item)

                st = issue.get("status", "")
                st_item = QTableWidgetItem(st)
                if "CROSS_FILE" in st:
                    st_item.setBackground(QColor("#fee2e2"))
                    st_item.setForeground(QColor("#b91c1c"))
                elif "CONFLICT" in st:
                    st_item.setBackground(QColor("#ffedd5"))
                    st_item.setForeground(QColor("#c2410c"))
                elif "MULTIPART" in st:
                    st_item.setBackground(QColor("#e0e7ff"))
                    st_item.setForeground(QColor("#3730a3"))
                elif "GEOMETRY" in st:
                    st_item.setBackground(QColor("#fef2f2"))
                    st_item.setForeground(QColor("#991b1b"))
                table.setItem(i, 2, st_item)

                occ_str = f"{issue.get('file_count', 1)} files ({issue.get('dup_count', 1)} parts)"
                occ_item = QTableWidgetItem(occ_str)
                table.setItem(i, 3, occ_item)

                det_str = f"{issue.get('details', '')} [Files: {issue.get('source_files', '')}]"
                table.setItem(i, 4, QTableWidgetItem(det_str))

        layout.addWidget(table, 1)

        # Action Buttons
        btn_box = QHBoxLayout()
        btn_filter = QPushButton("Filter Preview Table to Issues")
        btn_filter.setStyleSheet("font-weight: 600; color: #4338ca; background-color: #e0e7ff; padding: 6px 14px; border-radius: 4px;")
        btn_filter.clicked.connect(self._filter_main_table)
        btn_box.addWidget(btn_filter)

        btn_export = QPushButton("Export Duplicates Report (CSV)...")
        btn_export.clicked.connect(self._export_report_csv)
        btn_box.addWidget(btn_export)

        btn_box.addStretch()

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        btn_box.addWidget(btn_close)

        layout.addLayout(btn_box)

    def _filter_main_table(self):
        parent = self.parent()
        if hasattr(parent, "cmb_gpkg_issue_filter"):
            idx = parent.cmb_gpkg_issue_filter.findText("Any Issue / Duplicate")
            if idx >= 0:
                parent.cmb_gpkg_issue_filter.setCurrentIndex(idx)
        self.accept()

    def _export_report_csv(self):
        parent = self.parent()
        if hasattr(parent, "_export_gpkg_duplicate_csv"):
            parent._export_gpkg_duplicate_csv()


# --------------------------------------------------------------------------
# Background Worker for Match Execution
# --------------------------------------------------------------------------
class ExecutionWorker(QThread):
    progress_signal = pyqtSignal(int)
    log_signal = pyqtSignal(str, str)
    finished_signal = pyqtSignal(dict)

    def __init__(self, params):
        super().__init__()
        self.params = params
        self.alg = CoProcessingEarfMatch()
        self.is_cancelled = False

    def cancel(self):
        self.is_cancelled = True

    def run(self):
        class WorkerFeedback(QgsProcessingFeedback):
            def __init__(w_self, qthread_ref):
                super().__init__()
                w_self.qthread = qthread_ref

            def setProgress(w_self, progress):
                w_self.qthread.progress_signal.emit(int(progress))

            def pushInfo(w_self, info):
                w_self.qthread.log_signal.emit("info", str(info))

            def pushWarning(w_self, warning):
                w_self.qthread.log_signal.emit("warning", str(warning))

            def reportError(w_self, error, fatalError=False):
                w_self.qthread.log_signal.emit("error", str(error))

            def isCanceled(w_self):
                return w_self.qthread.is_cancelled

        feedback = WorkerFeedback(self)
        context = QgsProcessingContext()
        try:
            self.log_signal.emit("info", "Starting CO Processing execution...")
            results = self.alg.processAlgorithm(self.params, context, feedback)
            self.finished_signal.emit(results)
        except Exception as e:
            self.log_signal.emit("error", f"Processing error: {str(e)}")
            self.finished_signal.emit({})


# --------------------------------------------------------------------------
# Background Worker for Database Import (POPCEN)
# --------------------------------------------------------------------------
class DbImportWorker(QThread):
    progress_signal = pyqtSignal(int, str)
    finished_signal = pyqtSignal(int, str)

    def __init__(self, db, file_path, sheet_name=0):
        super().__init__()
        self.db = db
        self.file_path = file_path
        self.sheet_name = sheet_name
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        def _cb(pct, msg):
            if self._is_cancelled:
                return False
            self.progress_signal.emit(pct, msg)
            return True

        try:
            count = self.db.import_from_file(
                self.file_path,
                sheet_name=self.sheet_name,
                replace=True,
                progress_callback=_cb,
            )
            if self._is_cancelled:
                self.finished_signal.emit(0, "Cancelled")
            else:
                self.finished_signal.emit(count, "")
        except Exception as e:
            self.finished_signal.emit(0, str(e))


# --------------------------------------------------------------------------
# Background Worker for Preliminary EARF Folder Consolidation
# --------------------------------------------------------------------------
class EarfConsolidationWorker(QThread):
    progress_signal = pyqtSignal(int, str)
    finished_signal = pyqtSignal(int, int, str)

    def __init__(self, db, folder_path=None, recursive=False, sheet_name=0, file_list=None, replace=True):
        super().__init__()
        self.db = db
        self.folder_path = folder_path
        self.recursive = recursive
        self.sheet_name = sheet_name
        self.file_list = file_list
        self.replace = replace
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        def _cb(pct, msg):
            if self._is_cancelled:
                return False
            self.progress_signal.emit(pct, msg)
            return True

        try:
            if self.file_list is not None:
                recs, files = self.db.import_earf_from_files(
                    self.file_list,
                    sheet_name=self.sheet_name,
                    replace=self.replace,
                    progress_callback=_cb,
                )
            else:
                recs, files = self.db.import_earf_from_folder(
                    self.folder_path,
                    recursive=self.recursive,
                    sheet_name=self.sheet_name,
                    replace=self.replace,
                    progress_callback=_cb,
                )
            if self._is_cancelled:
                self.finished_signal.emit(0, 0, "Cancelled")
            else:
                self.finished_signal.emit(recs, files, "")
        except Exception as e:
            self.finished_signal.emit(0, 0, str(e))


# --------------------------------------------------------------------------
# Background Worker for Preliminary EARF Consistency Check
# --------------------------------------------------------------------------
class EarfConsistencyWorker(QThread):
    progress_signal = pyqtSignal(int, str)
    finished_signal = pyqtSignal(dict, str)

    def __init__(self, db, check_popcen=True):
        super().__init__()
        self.db = db
        self.check_popcen = check_popcen
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        def _cb(pct, msg):
            if self._is_cancelled:
                return False
            self.progress_signal.emit(pct, msg)
            return True

        try:
            results = self.db.run_earf_consistency_checks(
                check_popcen=self.check_popcen,
                progress_callback=_cb,
            )
            if self._is_cancelled:
                self.finished_signal.emit({}, "Cancelled")
            else:
                self.finished_signal.emit(results, "")
        except Exception as e:
            self.finished_signal.emit({}, str(e))



class GpkgFileSelectionDialog(QDialog):
    """
    Modal dialog allowing users to view, search, select with checkboxes,
    and consolidate discovered GeoPackage files into the local database (gpkg_records).
    """
    def __init__(self, parent, folder_path: str, file_paths: list = None, recursive: bool = False):
        super().__init__(parent)
        self.folder_path = folder_path
        self.recursive = recursive
        self.selected_files = []
        self._is_updating_checks = False

        if file_paths is not None:
            self.file_paths = file_paths
        else:
            self.file_paths = find_gpkg_files_in_folder(folder_path, recursive=recursive, pattern="_latest")

        self.setWindowTitle("Select GeoPackage Files to Consolidate into Database")
        self.resize(840, 560)
        self.setModal(True)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # 1. Header Banner
        header = QFrame()
        header.setStyleSheet("background: #f8fafc; border: 1px solid #cbd5e1; border-radius: 6px; padding: 10px;")
        h_layout = QVBoxLayout(header)
        h_layout.setContentsMargins(0, 0, 0, 0)
        h_layout.setSpacing(4)

        title_lbl = QLabel("<h3 style='margin:0; padding:0; color:#0f172a;'>Select GeoPackage Files to Upload</h3>")
        
        folder_display = self.folder_path
        if len(folder_display) > 85:
            folder_display = folder_display[:40] + "..." + folder_display[-42:]
        desc_lbl = QLabel(
            f"<b>Source Folder:</b> <span style='font-family:monospace; color:#0284c7;'>{folder_display}</span><br>"
            f"<span style='color:#64748b; font-size:11px;'>Discovered <b>{len(self.file_paths):,}</b> GeoPackage file(s). "
            f"Check the polygon files you want to consolidate into <b>gpkg_database.db</b> (table: <i>gpkg_records</i>).<br>"
            f"<b>Requirement:</b> Only polygon vector layers containing all required column headers "
            f"(<i>map_uuid, geocode, region, province, city_mun, barangay, ean, name, code, hhcount, bldgcount, sy, new_ean, hh_count, bldg_count, ea_type, eacount, remarks</i>) "
            f"are accepted.</span>"
        )
        desc_lbl.setWordWrap(True)
        h_layout.addWidget(title_lbl)
        h_layout.addWidget(desc_lbl)
        layout.addWidget(header)

        # 2. Filter & Selection Toolbar
        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)

        self.chk_all = QCheckBox("Select All")
        self.chk_all.setStyleSheet("font-weight: 600; color: #1e293b;")
        self.chk_all.setCheckState(Qt.Checked)
        self.chk_all.stateChanged.connect(self._on_select_all_toggled)
        toolbar.addWidget(self.chk_all)

        btn_invert = QPushButton("Invert")
        btn_invert.setToolTip("Invert checked status of all visible files")
        btn_invert.setStyleSheet("padding: 3px 8px; font-size: 11px;")
        btn_invert.clicked.connect(self._invert_selection)
        toolbar.addWidget(btn_invert)

        self.chk_subfolders = QCheckBox("Include Subfolders")
        self.chk_subfolders.setChecked(self.recursive)
        self.chk_subfolders.setToolTip("Recursively scan subfolders for GeoPackages (excluding 'versions')")
        self.chk_subfolders.stateChanged.connect(self._on_subfolders_toggled)
        toolbar.addWidget(self.chk_subfolders)

        toolbar.addSpacing(10)

        toolbar.addWidget(QLabel("Filter:"))
        self.txt_filter = QLineEdit()
        self.txt_filter.setPlaceholderText("Filter by file name or subfolder...")
        self.txt_filter.setClearButtonEnabled(True)
        self.txt_filter.textChanged.connect(self._apply_filter)
        toolbar.addWidget(self.txt_filter, 1)

        self.lbl_summary = QLabel()
        self.lbl_summary.setStyleSheet("font-weight: 600; color: #0284c7; font-size: 11px;")
        toolbar.addWidget(self.lbl_summary)

        layout.addLayout(toolbar)

        # 3. Files Table
        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(["[✓]", "#", "GeoPackage File", "Subfolder / Relative Path", "Geometry", "Size", "Date Modified"])
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.verticalHeader().setVisible(False)

        self.table.setColumnWidth(0, 48)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self.table.setColumnWidth(1, 40)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setColumnWidth(4, 130)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Fixed)
        self.table.setColumnWidth(5, 85)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Fixed)
        self.table.setColumnWidth(6, 140)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.Fixed)

        self._populate_table()
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        layout.addWidget(self.table, 1)

        # 4. Bottom Options & Action Bar
        bot_bar = QHBoxLayout()
        bot_bar.setSpacing(10)

        self.chk_replace = QCheckBox("Replace existing database records (Recommended)")
        self.chk_replace.setChecked(True)
        self.chk_replace.setToolTip("If checked, replaces existing records in gpkg_records.\nIf unchecked, appends to existing records.")
        bot_bar.addWidget(self.chk_replace)

        bot_bar.addStretch()

        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        bot_bar.addWidget(btn_cancel)

        btn_path_only = QPushButton("Set Folder Only")
        btn_path_only.setToolTip("Keep this folder path without uploading files right now")
        btn_path_only.clicked.connect(self._on_set_folder_only)
        bot_bar.addWidget(btn_path_only)

        self.btn_upload = QPushButton("Upload Selected to Database")
        self.btn_upload.setStyleSheet(
            "QPushButton { background-color: #059669; color: white; font-weight: bold; border-radius: 4px; padding: 6px 16px; }"
            "QPushButton:hover { background-color: #047857; }"
            "QPushButton:disabled { background-color: #94a3b8; }"
        )
        self.btn_upload.clicked.connect(self._on_upload_clicked)
        bot_bar.addWidget(self.btn_upload)

        layout.addLayout(bot_bar)

        self._update_selection_summary()

    def _populate_table(self):
        self._is_updating_checks = True
        self.table.setRowCount(len(self.file_paths))
        for row, fpath in enumerate(self.file_paths):
            fname = os.path.basename(fpath)
            try:
                rel_dir = os.path.relpath(os.path.dirname(fpath), self.folder_path)
                if rel_dir == ".":
                    rel_dir = "./"
            except Exception:
                rel_dir = "./"

            try:
                stat = os.stat(fpath)
                size_bytes = stat.st_size
                mtime_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime))
            except Exception:
                size_bytes = 0
                mtime_str = "Unknown"

            # Inspect polygon layer info & required columns
            has_poly, poly_tables, desc = get_gpkg_polygon_layer_info(fpath)
            is_valid_cols, missing_cols, existing_cols = validate_gpkg_columns(fpath)
            can_upload = has_poly and is_valid_cols

            # Col 0: Checkbox
            chk_item = QTableWidgetItem()
            chk_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
            chk_item.setCheckState(Qt.Checked if can_upload else Qt.Unchecked)
            chk_item.setTextAlignment(Qt.AlignCenter)
            chk_item.setData(Qt.UserRole, fpath)
            chk_item.setData(Qt.UserRole + 1, size_bytes)
            chk_item.setData(Qt.UserRole + 2, has_poly)
            chk_item.setData(Qt.UserRole + 3, desc)
            chk_item.setData(Qt.UserRole + 4, is_valid_cols)
            chk_item.setData(Qt.UserRole + 5, missing_cols)
            if not has_poly:
                chk_item.setToolTip(f"Cannot select: {desc}. Only polygon layers are accepted.")
            elif not is_valid_cols:
                missing_preview = ", ".join(missing_cols[:5]) + ("..." if len(missing_cols) > 5 else "")
                chk_item.setToolTip(f"Cannot select: Missing required columns: {missing_preview}")
            self.table.setItem(row, 0, chk_item)

            # Col 1: #
            idx_item = QTableWidgetItem(str(row + 1))
            idx_item.setTextAlignment(Qt.AlignCenter)
            idx_item.setForeground(QColor("#64748b"))
            idx_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.table.setItem(row, 1, idx_item)

            # Col 2: File Name
            name_item = QTableWidgetItem(fname)
            font = name_item.font()
            font.setBold(True)
            name_item.setFont(font)
            name_item.setForeground(QColor("#0f172a"))
            name_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            name_item.setToolTip(fpath)
            self.table.setItem(row, 2, name_item)

            # Col 3: Subfolder
            dir_item = QTableWidgetItem(rel_dir)
            dir_item.setForeground(QColor("#475569"))
            dir_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            dir_item.setToolTip(os.path.dirname(fpath))
            self.table.setItem(row, 3, dir_item)

            # Col 4: Geometry & Column Header Validation Status
            if not has_poly:
                geom_label = desc or "Non-Polygon"
                geom_item = QTableWidgetItem(geom_label)
                geom_item.setForeground(QColor("#dc2626"))  # Red
                geom_item.setToolTip(f"Excluded: {desc}. EA GeoPackage must contain polygon/multipolygon boundaries.")
            elif not is_valid_cols:
                geom_label = f"Missing {len(missing_cols)} Cols"
                geom_item = QTableWidgetItem(geom_label)
                geom_item.setForeground(QColor("#ea580c"))  # Amber/Orange
                missing_preview = ", ".join(missing_cols[:6]) + ("..." if len(missing_cols) > 6 else "")
                geom_item.setToolTip(f"Excluded: Missing required columns: {missing_preview}")
            else:
                count_str = f" ({len(poly_tables)})" if len(poly_tables) > 1 else ""
                geom_label = f"Valid Polygon{count_str}"
                geom_item = QTableWidgetItem(geom_label)
                geom_item.setForeground(QColor("#059669"))  # Emerald green
                tip_layers = ", ".join(poly_tables) if poly_tables else fname
                geom_item.setToolTip(f"Valid: Polygon layer with all required column headers ({tip_layers}).")
            geom_item.setTextAlignment(Qt.AlignCenter)
            geom_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.table.setItem(row, 4, geom_item)

            # Col 5: Size
            size_str = self._format_size(size_bytes)
            size_item = QTableWidgetItem(size_str)
            size_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            size_item.setForeground(QColor("#0d9488"))
            size_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.table.setItem(row, 5, size_item)

            # Col 6: Last Modified
            mod_item = QTableWidgetItem(mtime_str)
            mod_item.setTextAlignment(Qt.AlignCenter)
            mod_item.setForeground(QColor("#64748b"))
            mod_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.table.setItem(row, 6, mod_item)

        self._is_updating_checks = False

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"

    def _on_item_changed(self, item):
        if self._is_updating_checks or item.column() != 0:
            return
        if item.checkState() == Qt.Checked:
            has_poly = item.data(Qt.UserRole + 2)
            is_valid_cols = item.data(Qt.UserRole + 4)
            missing_cols = item.data(Qt.UserRole + 5) or []

            if not has_poly:
                self._is_updating_checks = True
                item.setCheckState(Qt.Unchecked)
                self._is_updating_checks = False
                desc = item.data(Qt.UserRole + 3) or "Non-polygon layer"
                QMessageBox.warning(
                    self,
                    "Non-Polygon Layer Excluded",
                    f"This GeoPackage cannot be selected because it does not contain a polygon vector layer.\n\n"
                    f"Detected geometry: {desc}\n\n"
                    f"Only polygon/multipolygon vector layers (EA boundaries) can be uploaded into the GeoPackage database."
                )
                return

            if not is_valid_cols:
                self._is_updating_checks = True
                item.setCheckState(Qt.Unchecked)
                self._is_updating_checks = False
                missing_str = "\n".join(f"• {c}" for c in missing_cols)
                QMessageBox.warning(
                    self,
                    "Missing Required Columns",
                    f"This GeoPackage cannot be selected because the layer is missing required column headers:\n\n"
                    f"{missing_str}\n\n"
                    f"All required column headers must be present before adding to the database."
                )
                return
        self._update_selection_summary()

    def _on_cell_double_clicked(self, row, col):
        item = self.table.item(row, 0)
        if item:
            has_poly = item.data(Qt.UserRole + 2)
            is_valid_cols = item.data(Qt.UserRole + 4)
            missing_cols = item.data(Qt.UserRole + 5) or []

            if not has_poly:
                desc = item.data(Qt.UserRole + 3) or "Non-polygon layer"
                QMessageBox.warning(
                    self,
                    "Non-Polygon Layer Excluded",
                    f"This GeoPackage cannot be selected because it does not contain a polygon vector layer.\n\n"
                    f"Detected geometry: {desc}\n\n"
                    f"Only polygon/multipolygon vector layers (EA boundaries) can be uploaded into the GeoPackage database."
                )
                return

            if not is_valid_cols:
                missing_str = "\n".join(f"• {c}" for c in missing_cols)
                QMessageBox.warning(
                    self,
                    "Missing Required Columns",
                    f"This GeoPackage cannot be selected because the layer is missing required column headers:\n\n"
                    f"{missing_str}\n\n"
                    f"All required column headers must be present before adding to the database."
                )
                return

            new_state = Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked
            item.setCheckState(new_state)

    def _on_select_all_toggled(self, state):
        if self._is_updating_checks:
            return
        target = Qt.Checked if state == Qt.Checked else Qt.Unchecked
        self._is_updating_checks = True
        for row in range(self.table.rowCount()):
            if not self.table.isRowHidden(row):
                item = self.table.item(row, 0)
                if item:
                    if target == Qt.Checked:
                        has_poly = item.data(Qt.UserRole + 2)
                        is_valid_cols = item.data(Qt.UserRole + 4)
                        item.setCheckState(Qt.Checked if (has_poly and is_valid_cols) else Qt.Unchecked)
                    else:
                        item.setCheckState(Qt.Unchecked)
        self._is_updating_checks = False
        self._update_selection_summary()

    def _invert_selection(self):
        self._is_updating_checks = True
        for row in range(self.table.rowCount()):
            if not self.table.isRowHidden(row):
                item = self.table.item(row, 0)
                if item:
                    cur = item.checkState()
                    if cur == Qt.Checked:
                        item.setCheckState(Qt.Unchecked)
                    else:
                        has_poly = item.data(Qt.UserRole + 2)
                        is_valid_cols = item.data(Qt.UserRole + 4)
                        item.setCheckState(Qt.Checked if (has_poly and is_valid_cols) else Qt.Unchecked)
        self._is_updating_checks = False
        self._update_selection_summary()

    def _on_subfolders_toggled(self, state):
        self.recursive = (state == Qt.Checked)
        self.file_paths = find_gpkg_files_in_folder(self.folder_path, recursive=self.recursive, pattern="_latest")
        self._populate_table()
        self._apply_filter(self.txt_filter.text())
        self._update_selection_summary()

    def _apply_filter(self, text):
        query = text.lower().strip()
        for row in range(self.table.rowCount()):
            name_item = self.table.item(row, 2)
            dir_item = self.table.item(row, 3)
            geom_item = self.table.item(row, 4)
            name_text = name_item.text().lower() if name_item else ""
            dir_text = dir_item.text().lower() if dir_item else ""
            geom_text = geom_item.text().lower() if geom_item else ""
            matches = (not query) or (query in name_text) or (query in dir_text) or (query in geom_text)
            self.table.setRowHidden(row, not matches)
        self._update_selection_summary()

    def _update_selection_summary(self):
        total_rows = self.table.rowCount()
        poly_count = 0
        checked_count = 0
        visible_count = 0
        total_bytes = 0

        for row in range(total_rows):
            is_vis = not self.table.isRowHidden(row)
            if is_vis:
                visible_count += 1
            item = self.table.item(row, 0)
            if item:
                has_poly = item.data(Qt.UserRole + 2)
                if has_poly:
                    poly_count += 1
                if item.checkState() == Qt.Checked:
                    checked_count += 1
                    size = item.data(Qt.UserRole + 1) or 0
                    total_bytes += size

        self._is_updating_checks = True
        if checked_count == 0:
            self.chk_all.setCheckState(Qt.Unchecked)
        elif checked_count == poly_count and poly_count > 0:
            self.chk_all.setCheckState(Qt.Checked)
        else:
            self.chk_all.setCheckState(Qt.PartiallyChecked)
        self._is_updating_checks = False

        size_str = self._format_size(total_bytes)
        non_poly_count = total_rows - poly_count
        extra_info = f" ({non_poly_count} non-polygon excluded)" if non_poly_count > 0 else ""
        if visible_count < total_rows:
            self.lbl_summary.setText(f"Showing {visible_count}/{total_rows} | Selected: {checked_count}/{poly_count} polygon{extra_info} ({size_str})")
        else:
            self.lbl_summary.setText(f"Selected: {checked_count}/{poly_count} polygon files{extra_info} ({size_str})")

        self.btn_upload.setEnabled(checked_count > 0)
        self.btn_upload.setText(f"Upload Selected to Database ({checked_count} files)")

    def _on_upload_clicked(self):
        selected = []
        invalid_files = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.checkState() == Qt.Checked:
                fpath = item.data(Qt.UserRole)
                has_poly = item.data(Qt.UserRole + 2)
                is_valid_cols = item.data(Qt.UserRole + 4)
                if fpath:
                    if has_poly and is_valid_cols:
                        selected.append(fpath)
                    else:
                        invalid_files.append(fpath)

        if not selected:
            QMessageBox.warning(
                self,
                "No Valid Layers Selected",
                "Please select at least one valid GeoPackage file to upload.\n\n"
                "Layers must be polygon vector layers and must contain all required column headers:\n"
                "map_uuid, geocode, region, province, city_mun, barangay, ean, name, code, "
                "hhcount, bldgcount, sy, new_ean, hh_count, bldg_count, ea_type, eacount, remarks."
            )
            return

        if invalid_files:
            QMessageBox.warning(
                self,
                "Invalid Files Excluded",
                f"{len(invalid_files)} file(s) were excluded because they are non-polygon or missing required column headers.\n\n"
                f"{len(selected)} valid file(s) will be uploaded into the database."
            )

        self.selected_files = selected
        self.accept()

    def _on_set_folder_only(self):
        self.selected_files = []
        self.reject()

    def get_selected_files(self) -> list:
        return self.selected_files

    def is_replace_mode(self) -> bool:
        return self.chk_replace.isChecked()


# --------------------------------------------------------------------------
# Background Worker for EA GeoPackage Folder Consolidation
# --------------------------------------------------------------------------
class GpkgConsolidationWorker(QThread):
    progress_signal = pyqtSignal(int, str)
    finished_signal = pyqtSignal(int, int, str)

    def __init__(self, db, folder_path=None, recursive=False, file_list=None, replace=True):
        super().__init__()
        self.db = db
        self.folder_path = folder_path
        self.recursive = recursive
        self.file_list = file_list
        self.replace = replace
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        def _cb(pct, msg):
            if self._is_cancelled:
                return False
            self.progress_signal.emit(pct, msg)
            return True

        try:
            if self.file_list is not None:
                recs, files = self.db.import_gpkg_from_files(
                    self.file_list,
                    replace=self.replace,
                    progress_callback=_cb,
                )
            else:
                recs, files = self.db.import_gpkg_from_folder(
                    self.folder_path,
                    recursive=self.recursive,
                    replace=self.replace,
                    progress_callback=_cb,
                )
            if self._is_cancelled:
                self.finished_signal.emit(0, 0, "Cancelled")
            else:
                self.finished_signal.emit(recs, files, "")
        except Exception as e:
            self.finished_signal.emit(0, 0, str(e))


# --------------------------------------------------------------------------
# Background Worker for EA GeoPackage Consistency & Duplication Check
# --------------------------------------------------------------------------
class GpkgConsistencyWorker(QThread):
    progress_signal = pyqtSignal(int, str)
    finished_signal = pyqtSignal(dict, str)

    def __init__(self, db, check_popcen=True):
        super().__init__()
        self.db = db
        self.check_popcen = check_popcen
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        def _cb(pct, msg):
            if self._is_cancelled:
                return False
            self.progress_signal.emit(pct, msg)
            return True

        try:
            results = self.db.run_gpkg_consistency_checks(
                check_popcen=self.check_popcen,
                progress_callback=_cb,
            )
            if self._is_cancelled:
                self.finished_signal.emit({}, "Cancelled")
            else:
                self.finished_signal.emit(results, "")
        except Exception as e:
            self.finished_signal.emit({}, str(e))




# --------------------------------------------------------------------------
# Background Worker for Deliverables Monitoring Directory Scan
# --------------------------------------------------------------------------
class MonitoringScanWorker(QThread):
    progress_signal = pyqtSignal(int, str)
    finished_signal = pyqtSignal(dict, str)

    def __init__(self, db, root_dir: str, scope: str = "subfolders"):
        super().__init__()
        self.db = db
        self.root_dir = root_dir
        self.scope = scope
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        def _cb(pct, msg):
            if self._is_cancelled:
                return False
            self.progress_signal.emit(pct, msg)
            return True

        try:
            summary = self.db.scan_output_deliverables(
                root_dir=self.root_dir,
                scope=self.scope,
                progress_callback=_cb,
            )
            if self._is_cancelled:
                self.finished_signal.emit({}, "Cancelled")
            else:
                self.finished_signal.emit(summary, "")
        except Exception as e:
            self.finished_signal.emit({}, str(e))


# --------------------------------------------------------------------------
# Background Worker for Final Deliverables Export (GPKG, Final EARF, MIS EARF)
# --------------------------------------------------------------------------
class DeliverablesExportWorker(QThread):
    """
    Background worker thread to export final deliverables (GeoPackage, Final EARF Excel,
    and MIS EARF Excel) without blocking the Qt GUI thread.
    """
    progress_signal = pyqtSignal(int, str)
    task_completed_signal = pyqtSignal(str, dict)
    finished_signal = pyqtSignal(dict, str)

    def __init__(self, db, tasks):
        super().__init__()
        self.db = db
        self.tasks = tasks
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        total_tasks = len(self.tasks)
        if total_tasks == 0:
            self.finished_signal.emit({"total_exported": 0, "total_features": 0, "results": []}, "")
            return

        results = []
        total_exported = 0
        total_features = 0

        for t_idx, task in enumerate(self.tasks):
            if self._is_cancelled:
                break

            c_code = task.get("city_mun", "")
            m_name = task.get("mun_name", c_code)
            out_gpkg = task.get("out_gpkg_path", "")
            out_excel = task.get("out_excel_path", "")
            out_mis = task.get("out_mis_path", "")
            out_pcd = task.get("out_pcd_path", "")
            search = task.get("search", "")
            status = task.get("status", "All")
            ea_lifecycle = task.get("ea_lifecycle", "All")
            geometry_sources = task.get("geometry_sources", [])
            layer_name = task.get("layer_name", "")

            base_pct = int((t_idx / total_tasks) * 100)
            task_slice = 100.0 / total_tasks

            def _gpkg_cb(sub_pct, msg):
                if self._is_cancelled:
                    return False
                overall = int(base_pct + (sub_pct / 100.0) * (task_slice * 0.40))
                self.progress_signal.emit(min(overall, 99), f"[{t_idx+1}/{total_tasks}] {m_name}: {msg}")
                return True

            task_res = {
                "city_mun": c_code,
                "mun_name": m_name,
                "clean_cm": layer_name,
                "count": 0,
                "geom_count": 0,
                "gpkg_path": out_gpkg,
                "excel_path": out_excel,
                "mis_path": out_mis,
                "pcd_path": out_pcd,
                "success": False,
                "error": None
            }

            try:
                # 1. GeoPackage Export (40% of slice)
                self.progress_signal.emit(
                    max(base_pct, 1),
                    f"[{t_idx+1}/{total_tasks}] Exporting {m_name} ({c_code}): Generating GeoPackage..."
                )
                cnt, geom_cnt = self.db.export_matched_to_gpkg(
                    out_gpkg_path=out_gpkg,
                    search=search,
                    status=status,
                    ea_lifecycle=ea_lifecycle,
                    city_mun=c_code,
                    geometry_sources=geometry_sources,
                    layer_name=layer_name,
                    progress_callback=_gpkg_cb,
                )

                if self._is_cancelled:
                    break

                task_res["count"] = cnt
                task_res["geom_count"] = geom_cnt

                if cnt > 0:
                    # 2. Final EARF Excel Export (25% of slice)
                    earf_pct = int(base_pct + task_slice * 0.40)
                    self.progress_signal.emit(
                        min(earf_pct, 99),
                        f"[{t_idx+1}/{total_tasks}] Exporting {m_name} ({c_code}): Writing Final EARF Excel..."
                    )
                    self.db.export_filtered_matched_to_csv(
                        out_csv=out_excel,
                        search=search,
                        status=status,
                        ea_lifecycle=ea_lifecycle,
                        city_mun=c_code,
                    )

                    if self._is_cancelled:
                        break

                    # 3. Final MIS Excel Export (20% of slice)
                    mis_pct = int(base_pct + task_slice * 0.65)
                    self.progress_signal.emit(
                        min(mis_pct, 99),
                        f"[{t_idx+1}/{total_tasks}] Exporting {m_name} ({c_code}): Writing Final MIS Excel..."
                    )
                    self.db.export_filtered_matched_to_mis_excel(
                        out_excel=out_mis,
                        search=search,
                        status=status,
                        ea_lifecycle=ea_lifecycle,
                        city_mun=c_code,
                    )

                    if self._is_cancelled:
                        break

                    # 4. PCD EARF Excel Export (15% of slice)
                    if out_pcd:
                        pcd_pct = int(base_pct + task_slice * 0.85)
                        self.progress_signal.emit(
                            min(pcd_pct, 99),
                            f"[{t_idx+1}/{total_tasks}] Exporting {m_name} ({c_code}): Writing PCD EARF Excel..."
                        )
                        self.db.export_filtered_matched_to_pcd_excel(
                            out_excel=out_pcd,
                            search=search,
                            status=status,
                            ea_lifecycle=ea_lifecycle,
                            city_mun=c_code,
                        )

                    task_res["success"] = True
                    total_exported += 1
                    total_features += cnt
                else:
                    task_res["error"] = "No matched records found"

            except Exception as e:
                task_res["error"] = str(e)

            results.append(task_res)
            self.task_completed_signal.emit(c_code, task_res)

        summary = {
            "total_exported": total_exported,
            "total_features": total_features,
            "results": results,
            "cancelled": self._is_cancelled
        }

        if self._is_cancelled:
            self.progress_signal.emit(100, "Export cancelled by user.")
            self.finished_signal.emit(summary, "Cancelled")
        else:
            self.progress_signal.emit(100, "Export completed successfully!")
            self.finished_signal.emit(summary, "")


class GroupedHeaderView(QHeaderView):
    """
    Two-tier hierarchical horizontal header view for the reconciliation table.
    Displays grouped worksheet banners (2024 POPCEN-CBMS, 2026 EARF, 2026 Final EA)
    in the top tier, while individual column names occupy the bottom tier.
    Automatically handles horizontal scrolling, column resizing, and visibility toggling.
    """
    def __init__(self, orientation=Qt.Horizontal, parent=None):
        super().__init__(orientation, parent)
        self.groups = [
            (1, 15, "2024 POPCEN-CBMS Worksheet  (2024 EARF / Estimated)", "#fee2e2", "#991b1b", "#fca5a5"),
            (16, 21, "2026 EARF Worksheet  (2026 Preliminary EA)", "#ecfdf5", "#065f46", "#a7f3d0"),
            (22, 22, "2026 Final EA  (Recoded EA)", "#eff6ff", "#1e40af", "#bfdbfe"),
        ]
        self.group_height = 28
        self.sectionResized.connect(lambda *_: self.viewport().update())
        self.geometriesChanged.connect(lambda: self.viewport().update())

    def set_groups(self, groups):
        self.groups = groups
        self.viewport().update()

    def sizeHint(self):
        s = super().sizeHint()
        if self.count() < 22 or not self.groups:
            return s
        return QSize(s.width(), 60)

    def paintSection(self, painter, rect, logicalIndex):
        if self.count() < 22 or not self.groups:
            super().paintSection(painter, rect, logicalIndex)
            return

        # Column 0 is the row index '#' spanning the full header height
        if logicalIndex == 0:
            painter.fillRect(rect, QColor("#f1f5f9"))
            pen = QPen(QColor("#cbd5e1"))
            pen.setWidth(1)
            painter.setPen(pen)
            painter.drawRect(rect.adjusted(0, 0, -1, -1))
            
            font = self.font()
            font.setBold(True)
            font.setPointSize(9)
            painter.setFont(font)
            painter.setPen(QColor("#64748b"))
            painter.drawText(rect, Qt.AlignCenter, "#")
            return

        # Bottom rectangle for individual column header
        bottom_rect = QRect(rect.left(), rect.top() + self.group_height, rect.width(), rect.height() - self.group_height)
        
        painter.fillRect(bottom_rect, QColor("#f8fafc"))
        pen = QPen(QColor("#cbd5e1"))
        pen.setWidth(1)
        painter.setPen(pen)
        painter.drawRect(bottom_rect.adjusted(0, 0, -1, -1))
        
        val = self.model().headerData(logicalIndex, self.orientation(), Qt.DisplayRole) if self.model() else ""
        text = str(val) if val is not None else ""
        
        font = self.font()
        font.setBold(True)
        font.setPointSize(8)
        painter.setFont(font)
        painter.setPen(QColor("#334155"))
        
        text_rect = bottom_rect.adjusted(4, 2, -4, -2)
        painter.drawText(text_rect, Qt.AlignCenter | Qt.TextWordWrap, text)

    def paintEvent(self, event):
        super().paintEvent(event)
        
        if self.count() < 22 or not self.groups:
            return
            
        painter = QPainter(self.viewport())
        font = self.font()
        font.setBold(True)
        font.setPointSize(9)
        painter.setFont(font)
        
        vp_rect = self.viewport().rect()
        
        for start_col, end_col, title, bg_hex, text_hex, border_hex in self.groups:
            vis_cols = [c for c in range(start_col, end_col + 1) if c < self.count() and not self.isSectionHidden(c)]
            if not vis_cols:
                continue
                
            first_c = vis_cols[0]
            last_c = vis_cols[-1]
            
            x_start = self.sectionViewportPosition(first_c)
            x_end = self.sectionViewportPosition(last_c) + self.sectionSize(last_c)
            w = x_end - x_start
            if w <= 0:
                continue
                
            group_rect = QRect(x_start, 0, w, self.group_height)
            
            # Fill background and draw border
            painter.fillRect(group_rect, QColor(bg_hex))
            pen = QPen(QColor(border_hex))
            pen.setWidth(1)
            painter.setPen(pen)
            painter.drawRect(group_rect.adjusted(0, 0, -1, -1))
            
            # Draw text centered in the visible part of the group
            vis_group_rect = group_rect.intersected(vp_rect)
            target_rect = vis_group_rect if vis_group_rect.width() > 60 else group_rect
            painter.setPen(QColor(text_hex))
            painter.drawText(target_rect, Qt.AlignCenter | Qt.TextSingleLine, title)


# --------------------------------------------------------------------------
# Main Dialog Class
# --------------------------------------------------------------------------
class COProcessingDialog(QDialog):
    """Interactive GUI dialog with EARF Post-Processing workspace and local database."""

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.worker = None
        self.import_worker = None
        self.earf_consolidation_worker = None
        self.earf_consistency_worker = None
        self.gpkg_consolidation_worker = None
        self.gpkg_consistency_worker = None
        self.db = POPCENDatabase()

        # POPCEN state
        self.current_page = 0
        self.page_size = 1000
        self.total_filtered_records = 0
        self.popcen_df = pd.DataFrame()
        self.popcen_filtered_df = pd.DataFrame()
        self.popcen_select_all_global = False
        self.popcen_selected_ids = set()
        self.popcen_deselected_ids = set()
        self._is_updating_popcen_checks = False

        # Preliminary EARF state
        self.earf_page = 0
        self.earf_page_size = 1000
        self.earf_total_filtered = 0
        self.earf_df = pd.DataFrame()
        self.earf_filtered_df = pd.DataFrame()
        self.earf_select_all_global = False
        self.earf_selected_ids = set()
        self.earf_deselected_ids = set()
        self._is_updating_earf_checks = False

        # EA GeoPackages state
        self.gpkg_page = 0
        self.gpkg_page_size = 1000
        self.gpkg_total_filtered = 0
        self.gpkg_df = pd.DataFrame()
        self.gpkg_filtered_df = pd.DataFrame()
        self.gpkg_select_all_global = False
        self.gpkg_selected_ids = set()
        self.gpkg_deselected_ids = set()
        self._is_updating_gpkg_checks = False

        # Geocode Matching state
        self.matching_page = 0
        self.matching_page_size = 1000
        self.matching_total_filtered = 0
        self.matching_df = pd.DataFrame()
        self.matching_filtered_df = pd.DataFrame()

        # Deliverables Monitoring state (Output GPKG, Final EARF, MIS EARF)
        self.monitoring_scan_worker = None
        self.mon_records = []
        self.mon_filtered_records = []
        self.mon_page = 0
        self.mon_page_size = 0
        self.mon_selected_record = None
        self._is_scanning_monitoring = False

        self.progress_bar = None
        self.btn_run = None

        self.setWindowTitle("CO Processing — EARF Post-Processing & Geocode Matching")
        self.resize(1280, 830)
        self.setMinimumSize(1040, 680)

        # Enable Minimize, Maximize / Restore, and Close buttons on dialog window
        self.setWindowFlags(
            Qt.Window
            | Qt.WindowMinMaxButtonsHint
            | Qt.WindowCloseButtonHint
        )
        self.setSizeGripEnabled(True)

        self._setup_ui()
        self._apply_styles()

        # Check default paths if available
        self._init_default_paths()

        # Auto-load initial state from local database if available
        self._check_and_load_initial_data()
        self._initial_load_done = True

    def showEvent(self, event):
        """Refresh data and reset progress each time the dialog is (re)opened."""
        super().showEvent(event)
        if getattr(self, "_initial_load_done", False) and not event.spontaneous():
            if self.progress_bar:
                self.progress_bar.setValue(0)
            try:
                self._check_and_load_initial_data()
            except Exception:
                pass

    def keyPressEvent(self, event):
        """Allows toggling Maximize/Restore with F11."""
        if event.key() == Qt.Key_F11:
            if self.isMaximized():
                self.showNormal()
            else:
                self.showMaximized()
            event.accept()
            return
        super().keyPressEvent(event)

    def _init_default_paths(self):
        """Keep inputs clean and unpopulated on startup."""
        pass

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(10)

        # Header Banner
        header_frame = QFrame()
        header_frame.setObjectName("headerFrame")
        header_layout = QHBoxLayout(header_frame)
        header_layout.setContentsMargins(14, 10, 14, 10)

        title_layout = QVBoxLayout()
        title_label = QLabel("CO Processing — EARF Post-Processing")
        title_label.setObjectName("dialogTitle")
        title_font = QFont("Segoe UI", 13, QFont.Bold)
        title_label.setFont(title_font)

        sub_label = QLabel("Multi-source preview (POPCEN / Preliminary EARF / EA GeoPackages) & geocode outer-join engine")
        sub_label.setObjectName("dialogSubtitle")
        sub_font = QFont("Segoe UI", 9)
        sub_label.setFont(sub_font)

        title_layout.addWidget(title_label)
        title_layout.addWidget(sub_label)
        header_layout.addLayout(title_layout)
        header_layout.addStretch()


        main_layout.addWidget(header_frame)

        # Main Tabs
        self.tabs = QTabWidget()
        self.tabs.setObjectName("mainTabs")
        self.tabs.tabBar().setElideMode(Qt.ElideNone)

        # Tab 1: Dashboard (Deliverables Monitoring: Output GPKG, Final EARF, MIS EARF) on the left side
        self.monitoring_tab = QWidget()
        self._setup_monitoring_tab()
        self.tabs.addTab(self.monitoring_tab, "Dashboard")

        # Tab 2: Geocode Matching
        self.matching_tab = QWidget()
        self._setup_matching_tab()
        self.tabs.addTab(self.matching_tab, "Geocode Matching")

        # Tab 3: EARF Post-Processing (with 3 nested sub-tabs)
        self.post_processing_tab = QWidget()
        self._setup_post_processing_tab()
        self.tabs.addTab(self.post_processing_tab, "EARF Post-Processing")

        self.tabs.currentChanged.connect(self._on_primary_tab_changed)
        main_layout.addWidget(self.tabs)

        # Compatibility aliases pointing to active EARF Post-Processing & Matching widgets
        self.txt_popcen_file = getattr(self, "txt_popcen_quick", None)
        self.txt_popcen_sheet = getattr(self, "txt_sheet_quick", None)
        self.txt_gpkg_layers = getattr(self, "txt_gpkg_preview_layers", None)
        self.txt_earf_files = getattr(self, "txt_earf_preview_files", None)
        self.txt_earf_sheet = getattr(self, "txt_earf_preview_sheet", None)
        self.txt_out_gpkg = getattr(self, "txt_match_export_dir", None)
        self.config_tab = None

        class _ConsoleLogStub:
            def append(self, text):
                pass
            def clear(self):
                pass
        self.console_log = _ConsoleLogStub()

    def _on_primary_tab_changed(self, index):
        """Handles switching between primary tabs (Dashboard, Matching, Post-Processing)."""
        if hasattr(self, "monitoring_tab") and index == self.tabs.indexOf(self.monitoring_tab):  # Dashboard tab
            if getattr(self, "_dashboard_needs_update", False):
                self._dashboard_needs_update = False
                pending_codes = getattr(self, "_dashboard_pending_update_codes", [])
                if pending_codes:
                    self._update_monitoring_for_citymuns(pending_codes)
                    self._dashboard_pending_update_codes = []
                else:
                    self._start_monitoring_scan()
            elif not self.mon_records:
                saved = self.db.get_saved_monitoring_records()
                if saved:
                    self.mon_records = saved
                    self._apply_monitoring_filters()
                else:
                    self._start_monitoring_scan()

    # ----------------------------------------------------------------------
    # Tab 1: Geocode Matching (Outer-Join of 3 EARF Sources)
    # ----------------------------------------------------------------------
    def _setup_matching_tab(self):
        layout = QVBoxLayout(self.matching_tab)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # 1. Source Readiness & Top Action Bar
        top_bar = QHBoxLayout()
        top_bar.addWidget(QLabel("<b>Source Readiness:</b>"))

        self.lbl_match_pop_badge = QLabel("POPCEN: Checking...")
        self.lbl_match_pop_badge.setObjectName("dbBadge")
        top_bar.addWidget(self.lbl_match_pop_badge)

        self.lbl_match_earf_badge = QLabel("EARF: Checking...")
        self.lbl_match_earf_badge.setObjectName("dbBadge")
        top_bar.addWidget(self.lbl_match_earf_badge)

        self.lbl_match_gpkg_badge = QLabel("GPKG: Checking...")
        self.lbl_match_gpkg_badge.setObjectName("dbBadge")
        top_bar.addWidget(self.lbl_match_gpkg_badge)

        top_bar.addStretch()

        self.btn_run_live_match = QPushButton("Match Sources Now")
        self.btn_run_live_match.setObjectName("primaryBtn")
        self.btn_run_live_match.setToolTip("Run outer-join matching across current POPCEN, EARF, and GPKG sources on 14-digit geocode")
        self.btn_run_live_match.clicked.connect(self._run_live_matching)
        top_bar.addWidget(self.btn_run_live_match)

        self.btn_match_stop = QPushButton("Stop")
        self.btn_match_stop.setObjectName("stopBtn")
        self.btn_match_stop.setToolTip("Stop active geocode matching process immediately")
        self.btn_match_stop.setEnabled(False)
        self.btn_match_stop.clicked.connect(self._stop_matching)
        top_bar.addWidget(self.btn_match_stop)

        self.btn_match_refresh = QPushButton("Refresh")
        self.btn_match_refresh.setObjectName("actionBtn")
        self.btn_match_refresh.clicked.connect(self._check_and_load_matching_data)
        top_bar.addWidget(self.btn_match_refresh)

        self.btn_export_output_summary = QPushButton("Export Output Summary (Excel)...")
        self.btn_export_output_summary.setObjectName("primaryBtn")
        self.btn_export_output_summary.setToolTip("Export formal Output Summary table & Validation Flags in Excel (.xlsx)")
        self.btn_export_output_summary.clicked.connect(self._export_output_summary_excel)
        top_bar.addWidget(self.btn_export_output_summary)
        self.btn_export_match_csv = self.btn_export_output_summary

        self.btn_add_to_map = QPushButton("Add Layer to Map")
        self.btn_add_to_map.setObjectName("secondaryBtn")
        self.btn_add_to_map.clicked.connect(self._add_matching_layer_to_map)
        top_bar.addWidget(self.btn_add_to_map)

        layout.addLayout(top_bar)

        # 2. KPI Summary Cards Frame (8 Cards)
        kpi_frame = QFrame()
        kpi_frame.setObjectName("kpiFrame")
        kpi_layout = QHBoxLayout(kpi_frame)
        kpi_layout.setContentsMargins(8, 6, 8, 6)
        kpi_layout.setSpacing(8)

        self.kpi_match_total = self._create_kpi_card("Total Geocodes", "0", "#0284c7")
        self.kpi_match_all = self._create_kpi_card("Fully Matched", "0", "#16a34a")
        self.kpi_match_pop_gpkg = self._create_kpi_card("POPCEN + GPKG", "0", "#0284c7")
        self.kpi_match_pop_earf = self._create_kpi_card("POPCEN + EARF", "0", "#7c3aed")
        self.kpi_match_gpkg_earf = self._create_kpi_card("GPKG + EARF", "0", "#ea580c")
        self.kpi_match_single = self._create_kpi_card("Single Source Only", "0", "#dc2626")
        self.kpi_match_reset = self._create_kpi_card("Reset / Delineated EAs", "0", "#d97706")
        self.kpi_match_hh_diff = self._create_kpi_card("Household Diffs", "0", "#e11d48")

        for k in (
            self.kpi_match_total,
            self.kpi_match_all,
            self.kpi_match_pop_gpkg,
            self.kpi_match_pop_earf,
            self.kpi_match_gpkg_earf,
            self.kpi_match_single,
            self.kpi_match_reset,
            self.kpi_match_hh_diff,
        ):
            kpi_layout.addWidget(k)

        layout.addWidget(kpi_frame)

        # 3. Filter & Search Controls Bar
        filter_bar = QHBoxLayout()
        filter_bar.addWidget(QLabel("Search:"))
        self.search_match = QLineEdit()
        self.search_match.setPlaceholderText("Search Geocode, Area Name, EAN, New EAN, Remarks...")
        self.search_match.textChanged.connect(self._on_matching_search_changed)
        filter_bar.addWidget(self.search_match, 1)

        filter_bar.addWidget(QLabel("Match Status:"))
        self.cmb_match_status_filter = QComboBox()
        self.cmb_match_status_filter.addItems([
            "All Records",
            "MATCHED_ALL",
            "Discrepancies Only",
            "UNMATCHED_POPCEN_GPKG",
            "UNMATCHED_POPCEN_EARF",
            "UNMATCHED_GPKG_EARF",
            "UNMATCHED_POPCEN",
            "UNMATCHED_GPKG",
            "UNMATCHED_EARF",
        ])
        self.cmb_match_status_filter.currentIndexChanged.connect(self._on_matching_filter_changed)
        filter_bar.addWidget(self.cmb_match_status_filter)

        filter_bar.addWidget(QLabel("EA Lifecycle:"))
        self.cmb_match_lifecycle_filter = QComboBox()
        self.cmb_match_lifecycle_filter.addItems([
            "All EA Types",
            "Retained Only",
            "Reset / Delineated Only",
            "Split EAs Only",
            "Merged EAs Only",
        ])
        self.cmb_match_lifecycle_filter.currentIndexChanged.connect(self._on_matching_filter_changed)
        filter_bar.addWidget(self.cmb_match_lifecycle_filter)

        filter_bar.addWidget(QLabel("Flags:"))
        self.cmb_match_flag_filter = QComboBox()
        self.cmb_match_flag_filter.addItems([
            "All Records",
            "Any Flag / Issue",
            "FLAG-01: Duplicate New EA",
            "FLAG-02: Delin/Merg without Remarks",
            "FLAG-03: Source Year Mismatch",
            "FLAG-04: Null EA Count",
            "FLAG-05: Duplicate Geocode",
        ])
        self.cmb_match_flag_filter.currentIndexChanged.connect(self._on_matching_filter_changed)
        filter_bar.addWidget(self.cmb_match_flag_filter)

        filter_bar.addWidget(QLabel("City/Mun:"))
        self.cmb_match_citymun_filter = QComboBox()
        self.cmb_match_citymun_filter.addItem("All Uploaded City/Mun", "")
        self.cmb_match_citymun_filter.currentIndexChanged.connect(self._on_matching_citymun_changed)
        filter_bar.addWidget(self.cmb_match_citymun_filter)

        filter_bar.addWidget(QLabel("Page Size:"))
        self.cmb_match_page_size = QComboBox()
        self.cmb_match_page_size.addItems(["100", "250", "500", "1000", "2000", "5000"])
        self.cmb_match_page_size.setCurrentText("1000")
        self.cmb_match_page_size.currentIndexChanged.connect(self._on_matching_page_size_changed)
        filter_bar.addWidget(self.cmb_match_page_size)

        self.btn_match_prev = QPushButton("Prev")
        self.btn_match_prev.clicked.connect(self._matching_prev_page)
        filter_bar.addWidget(self.btn_match_prev)

        self.lbl_match_page_info = QLabel("Page 1 of 1 (0 records)")
        self.lbl_match_page_info.setObjectName("pageInfo")
        filter_bar.addWidget(self.lbl_match_page_info)

        self.btn_match_next = QPushButton("Next")
        self.btn_match_next.clicked.connect(self._matching_next_page)
        filter_bar.addWidget(self.btn_match_next)

        layout.addLayout(filter_bar)

        # 3.5. Export Controls Bar (Folder Directory Selector, Search & Export Button)
        export_bar = QHBoxLayout()
        export_bar.addWidget(QLabel("<b>Export Folder:</b>"))
        self.txt_match_export_dir = QLineEdit()
        self.txt_match_export_dir.setPlaceholderText("Select folder directory for exported deliverables...")
        root_default = DEFAULT_FINAL_EXPORT_ROOT if os.path.exists(DEFAULT_FINAL_EXPORT_ROOT) else os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
        if os.path.exists(root_default):
            self.txt_match_export_dir.setText(root_default)
        self._final_export_root_dir = root_default

        try:
            completer = QCompleter(self)
            fs_model = QFileSystemModel(completer)
            fs_model.setRootPath("")
            completer.setModel(fs_model)
            self.txt_match_export_dir.setCompleter(completer)
        except Exception:
            pass

        export_bar.addWidget(self.txt_match_export_dir, 1)

        self.btn_match_search_folder = QPushButton("Search Folder...")
        self.btn_match_search_folder.setObjectName("secondaryBtn")
        self.btn_match_search_folder.setToolTip("Search and pick subfolder by municipality / province name")
        self.btn_match_search_folder.clicked.connect(self._search_export_folder_directory)
        export_bar.addWidget(self.btn_match_search_folder)

        self.btn_match_browse_export = QPushButton("Browse...")
        self.btn_match_browse_export.setObjectName("secondaryBtn")
        self.btn_match_browse_export.setToolTip("Browse export directory folder")
        self.btn_match_browse_export.clicked.connect(self._browse_match_export_dir)
        export_bar.addWidget(self.btn_match_browse_export)

        self.btn_export_match_gpkg = QPushButton("Export final data")
        self.btn_export_match_gpkg.setObjectName("primaryBtn")
        self.btn_export_match_gpkg.setToolTip("Export final deliverables (GeoPackage and Excel) using the standard attribute schema")
        self.btn_export_match_gpkg.clicked.connect(self._export_matching_gpkg)
        export_bar.addWidget(self.btn_export_match_gpkg)

        layout.addLayout(export_bar)

        # 4. Reconciled Data Grid Table with Multi-Tier Grouped Headers
        self.table_matching = QTableWidget()
        self.table_matching.setObjectName("matchingTable")
        self.table_matching.setAlternatingRowColors(True)
        self.table_matching.setSelectionBehavior(QTableWidget.SelectRows)
        self.match_grouped_header = GroupedHeaderView(Qt.Horizontal, self.table_matching)
        self.table_matching.setHorizontalHeader(self.match_grouped_header)
        self.table_matching.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table_matching.horizontalHeader().setStretchLastSection(False)
        layout.addWidget(self.table_matching)
        self._enable_column_toggle(self.table_matching)

    def _enable_column_toggle(self, table):
        """Right-click a column header to hide/unhide columns of the table."""
        header = table.horizontalHeader()
        header.setContextMenuPolicy(Qt.CustomContextMenu)
        header.customContextMenuRequested.connect(
            lambda pos, t=table: self._show_column_menu(t, pos)
        )
        header.setToolTip("Right-click to hide/unhide columns")

    def _show_column_menu(self, table, pos):
        header = table.horizontalHeader()
        menu = QMenu(self)
        col_actions = {}
        for col in range(table.columnCount()):
            if col == 0 and hasattr(self, "table_earf") and table in (self.table_earf, self.table_gpkg):
                continue
            item = table.horizontalHeaderItem(col)
            label = item.text() if item is not None else f"Column {col + 1}"
            act = menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(not table.isColumnHidden(col))
            col_actions[act] = col
        menu.addSeparator()
        show_all = menu.addAction("Show All Columns")

        chosen = menu.exec_(header.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen is show_all:
            for c in range(table.columnCount()):
                table.setColumnHidden(c, False)
        elif chosen in col_actions:
            c = col_actions[chosen]
            table.setColumnHidden(c, not table.isColumnHidden(c))
        menu.deleteLater()

    # ----------------------------------------------------------------------
    # Geocode Matching Queries, KPIs, and Synchronization Logic
    # ----------------------------------------------------------------------
    def _check_and_load_matching_data(self):
        """Refreshes source status badges and loads matched records from local database if available."""
        # 1. POPCEN status
        pop_count = self.db.record_count()
        if pop_count > 0:
            self.lbl_match_pop_badge.setText(f"POPCEN: DB ({pop_count:,})")
            self.lbl_match_pop_badge.setStyleSheet("color: #15803d; font-weight: 600; background-color: #dcfce7; padding: 2px 6px; border-radius: 4px;")
        elif not self.popcen_df.empty:
            self.lbl_match_pop_badge.setText(f"POPCEN: Custom ({len(self.popcen_df):,})")
            self.lbl_match_pop_badge.setStyleSheet("color: #0369a1; font-weight: 600; background-color: #e0f2fe; padding: 2px 6px; border-radius: 4px;")
        else:
            self.lbl_match_pop_badge.setText("POPCEN: Empty")
            self.lbl_match_pop_badge.setStyleSheet("color: #64748b; background-color: #f1f5f9; padding: 2px 6px; border-radius: 4px;")

        # 2. EARF status
        earf_count = self.db.earf_record_count()
        if earf_count > 0:
            self.lbl_match_earf_badge.setText(f"EARF: DB ({earf_count:,})")
            self.lbl_match_earf_badge.setStyleSheet("color: #15803d; font-weight: 600; background-color: #dcfce7; padding: 2px 6px; border-radius: 4px;")
        elif not self.earf_df.empty:
            self.lbl_match_earf_badge.setText(f"EARF: File ({len(self.earf_df):,})")
            self.lbl_match_earf_badge.setStyleSheet("color: #0369a1; font-weight: 600; background-color: #e0f2fe; padding: 2px 6px; border-radius: 4px;")
        else:
            self.lbl_match_earf_badge.setText("EARF: Empty")
            self.lbl_match_earf_badge.setStyleSheet("color: #64748b; background-color: #f1f5f9; padding: 2px 6px; border-radius: 4px;")

        # 3. GPKG status
        gpkg_count = self.db.gpkg_record_count()
        if gpkg_count > 0:
            self.lbl_match_gpkg_badge.setText(f"GPKG: DB ({gpkg_count:,})")
            self.lbl_match_gpkg_badge.setStyleSheet("color: #15803d; font-weight: 600; background-color: #dcfce7; padding: 2px 6px; border-radius: 4px;")
        elif not self.gpkg_df.empty:
            self.lbl_match_gpkg_badge.setText(f"GPKG: Custom ({len(self.gpkg_df):,})")
            self.lbl_match_gpkg_badge.setStyleSheet("color: #0369a1; font-weight: 600; background-color: #e0f2fe; padding: 2px 6px; border-radius: 4px;")
        elif self.txt_gpkg_preview_layers.text().strip():
            self.lbl_match_gpkg_badge.setText("GPKG: Path Set")
            self.lbl_match_gpkg_badge.setStyleSheet("color: #b45309; background-color: #fef3c7; padding: 2px 6px; border-radius: 4px;")
        else:
            self.lbl_match_gpkg_badge.setText("GPKG: Empty")
            self.lbl_match_gpkg_badge.setStyleSheet("color: #64748b; background-color: #f1f5f9; padding: 2px 6px; border-radius: 4px;")

        # 4. Populate City/Mun dropdown with uploaded citymuns
        if hasattr(self, "cmb_match_citymun_filter"):
            current_cm = self.cmb_match_citymun_filter.currentData()
            self.cmb_match_citymun_filter.blockSignals(True)
            self.cmb_match_citymun_filter.clear()
            citymuns = self.db.get_uploaded_citymuns()
            self.cmb_match_citymun_filter.addItem(f"All Uploaded City/Mun ({len(citymuns)})", "")
            restore_idx = 0
            for idx, cm in enumerate(citymuns, start=1):
                self.cmb_match_citymun_filter.addItem(cm["display"], cm["code"])
                if current_cm and cm["code"] == current_cm:
                    restore_idx = idx
            self.cmb_match_citymun_filter.setCurrentIndex(restore_idx)
            self.cmb_match_citymun_filter.blockSignals(False)

        # 5. Load database KPIs and table
        self._load_matching_kpis()
        self._fetch_and_render_matching_page()

    def _on_matching_citymun_changed(self):
        self.matching_page = 0
        self._load_matching_kpis()
        self._fetch_and_render_matching_page()
        self._update_export_dir_for_current_citymun()

    def _update_export_dir_for_current_citymun(self):
        cm = getattr(self, "cmb_match_citymun_filter", None)
        selected_cm = cm.currentData() if cm else None
        root_dir = getattr(self, "_final_export_root_dir", None) or DEFAULT_FINAL_EXPORT_ROOT
        if not os.path.exists(root_dir):
            root_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

        if selected_cm:
            subfolder, hier = self.db.resolve_final_export_subfolder(root_dir, selected_cm)
            if subfolder:
                self.txt_match_export_dir.setText(subfolder)
                self.txt_match_export_dir.setToolTip(f"Export Folder: {hier.get('city_mun')}, {hier.get('province')} ({hier.get('region')})")
        else:
            if os.path.exists(root_dir):
                self.txt_match_export_dir.setText(root_dir)
                self.txt_match_export_dir.setToolTip("Export Root Folder")

    def _load_matching_kpis(self):
        cm = getattr(self, "cmb_match_citymun_filter", None)
        selected_cm = cm.currentData() if cm else None
        kpis = self.db.get_matched_kpis(city_mun=selected_cm)
        self.kpi_match_total.val_label.setText(f"{kpis['total_geocodes']:,}")
        self.kpi_match_all.val_label.setText(f"{kpis['matched_all']:,}")
        self.kpi_match_pop_gpkg.val_label.setText(f"{kpis['popcen_gpkg']:,}")
        self.kpi_match_pop_earf.val_label.setText(f"{kpis['popcen_earf']:,}")
        self.kpi_match_gpkg_earf.val_label.setText(f"{kpis['gpkg_earf']:,}")
        self.kpi_match_single.val_label.setText(f"{kpis['single_source']:,}")
        self.kpi_match_reset.val_label.setText(f"{kpis['reset_eas']:,}")
        self.kpi_match_hh_diff.val_label.setText(f"{kpis['hh_diffs']:,}")

    def _fetch_and_render_matching_page(self):
        search = self.search_match.text().strip()
        status = self.cmb_match_status_filter.currentText()
        lifecycle = self.cmb_match_lifecycle_filter.currentText()
        flag_val = getattr(self, "cmb_match_flag_filter", None)
        flag_filter = flag_val.currentText() if flag_val else None
        cm = getattr(self, "cmb_match_citymun_filter", None)
        selected_cm = cm.currentData() if cm else None

        offset = self.matching_page * self.matching_page_size
        rows, total = self.db.query_matched_records(
            search=search,
            status=status,
            ea_lifecycle=lifecycle,
            flag_filter=flag_filter,
            city_mun=selected_cm,
            limit=self.matching_page_size,
            offset=offset,
        )
        self.matching_total_filtered = total
        self._populate_matching_table(rows, total, offset)

    def _populate_matching_table(self, rows: list, total_filtered: int, offset: int = 0):
        self.table_matching.setUpdatesEnabled(False)
        self.table_matching.clear()

        total_pages = max(1, (total_filtered + self.matching_page_size - 1) // self.matching_page_size)
        cur_display = self.matching_page + 1 if total_filtered > 0 else 0
        self.lbl_match_page_info.setText(f"Page {cur_display} of {total_pages} ({total_filtered:,} records)")
        self.btn_match_prev.setEnabled(self.matching_page > 0)
        self.btn_match_next.setEnabled(self.matching_page < total_pages - 1)

        headers = [
            "#",
            "Geocode",
            "Geographic Name",
            "Geographic Level",
            "region_code",
            "province_code",
            "city_mun_code",
            "barangay_code",
            "ean",
            "No. of EAs",
            "Areaname",
            "Household Population",
            "No. of Households",
            "EA Subject for Delineation",
            "EA Subject for Merging",
            "Remarks (POPCEN)",
            "New Enumeration Area Code",
            "Household Count",
            "Building Count",
            "EA Type",
            "Source Year",
            "Remarks (EARF)",
            "Recoded Enumeration Area",
        ]

        if not rows:
            self.table_matching.setColumnCount(1)
            self.table_matching.setRowCount(1)
            self.table_matching.setHorizontalHeaderLabels(["Status"])
            item = QTableWidgetItem("No matched records found. Click 'Match Sources Now' to run cross-matching.")
            item.setForeground(QColor("#64748b"))
            self.table_matching.setItem(0, 0, item)
            self.table_matching.setUpdatesEnabled(True)
            return

        self.table_matching.setColumnCount(len(headers))
        self.table_matching.setRowCount(len(rows))
        self.table_matching.setHorizontalHeaderLabels(headers)

        # Column widths for all 23 columns matching the 2-worksheet specification
        col_widths = [
            50,   # 0: #
            130,  # 1: Geocode
            140,  # 2: Geographic Name
            110,  # 3: Geographic Level
            85,   # 4: region_code
            85,   # 5: province_code
            85,   # 6: city_mun_code
            85,   # 7: barangay_code
            75,   # 8: ean
            80,   # 9: No. of EAs
            140,  # 10: Areaname
            120,  # 11: Household Population
            110,  # 12: No. of Households
            130,  # 13: EA Subject for Delineation
            130,  # 14: EA Subject for Merging
            130,  # 15: Remarks (POPCEN)
            140,  # 16: New Enumeration Area Code
            100,  # 17: Household Count
            100,  # 18: Building Count
            100,  # 19: EA Type
            85,   # 20: Source Year
            130,  # 21: Remarks (EARF)
            160,  # 22: Recoded Enumeration Area
        ]
        for col_idx, width in enumerate(col_widths):
            self.table_matching.setColumnWidth(col_idx, width)

        mono_font = QFont("Consolas, Courier New, monospace", 9)
        mono_font.setStyleHint(QFont.Monospace)

        for row_idx, r in enumerate(rows):
            # 0: Row Index (with validation flag indicator)
            flags_str = str(r.get("validation_flags") or "").strip()
            notes_str = str(r.get("flag_notes") or "").strip()
            idx_item = QTableWidgetItem(f"{offset + row_idx + 1:,}")
            idx_item.setTextAlignment(Qt.AlignCenter)
            if flags_str:
                idx_item.setForeground(QColor("#b91c1c"))
                idx_item.setBackground(QColor("#fee2e2"))
                idx_item.setToolTip(f"Validation Flags: {flags_str}\n{notes_str}")
            else:
                idx_item.setForeground(QColor("#94a3b8"))
            self.table_matching.setItem(row_idx, 0, idx_item)

            # 1: Geocode
            gc_str = str(r.get("geocode", "") or "")
            gc_item = QTableWidgetItem(gc_str)
            gc_item.setFont(mono_font)
            gc_item.setTextAlignment(Qt.AlignCenter)
            self.table_matching.setItem(row_idx, 1, gc_item)

            # Check if this row belongs to 2024 POPCEN
            is_in_pop = bool(r.get("in_popcen") in (1, "1", True) and r.get("geographic_name"))

            # 2: Geographic Name
            gn_str = str(r.get("geographic_name", "") or "") if is_in_pop else ""
            self.table_matching.setItem(row_idx, 2, QTableWidgetItem(gn_str))

            # 3: Geographic Level
            gl_str = str(r.get("geographic_level", "") or "") if is_in_pop else ""
            gl_item = QTableWidgetItem(gl_str)
            gl_item.setTextAlignment(Qt.AlignCenter)
            self.table_matching.setItem(row_idx, 3, gl_item)

            # 4: region_code
            rc = str(r.get("region_code", "") or "") if is_in_pop else ""
            rc_item = QTableWidgetItem(rc)
            rc_item.setFont(mono_font)
            rc_item.setTextAlignment(Qt.AlignCenter)
            self.table_matching.setItem(row_idx, 4, rc_item)

            # 5: province_code
            pc = str(r.get("province_code", "") or "") if is_in_pop else ""
            pc_item = QTableWidgetItem(pc)
            pc_item.setFont(mono_font)
            pc_item.setTextAlignment(Qt.AlignCenter)
            self.table_matching.setItem(row_idx, 5, pc_item)

            # 6: city_mun_code
            mc = str(r.get("city_mun_code", "") or "") if is_in_pop else ""
            mc_item = QTableWidgetItem(mc)
            mc_item.setFont(mono_font)
            mc_item.setTextAlignment(Qt.AlignCenter)
            self.table_matching.setItem(row_idx, 6, mc_item)

            # 7: barangay_code
            bc = str(r.get("barangay_code", "") or "") if is_in_pop else ""
            bc_item = QTableWidgetItem(bc)
            bc_item.setFont(mono_font)
            bc_item.setTextAlignment(Qt.AlignCenter)
            self.table_matching.setItem(row_idx, 7, bc_item)

            # 8: ean
            ean_val = str(r.get("ean", "") or "").replace(".0", "") if is_in_pop else ""
            ean_item = QTableWidgetItem(ean_val)
            ean_item.setFont(mono_font)
            ean_item.setTextAlignment(Qt.AlignCenter)
            self.table_matching.setItem(row_idx, 8, ean_item)

            # 9: No. of EAs
            neas_val = str(r.get("no_of_eas", "") or "").replace(".0", "") if is_in_pop else ""
            neas_item = QTableWidgetItem(neas_val)
            neas_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table_matching.setItem(row_idx, 9, neas_item)

            # 10: Areaname
            an_str = str(r.get("areaname", "") or "") if is_in_pop else ""
            self.table_matching.setItem(row_idx, 10, QTableWidgetItem(an_str))

            # 11: Household Population
            hpop = str(r.get("household_population", "") or "").replace(".0", "") if is_in_pop else ""
            hpop_item = QTableWidgetItem(hpop)
            hpop_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table_matching.setItem(row_idx, 11, hpop_item)

            # 12: No. of Households
            nhh = str(r.get("no_of_households", "") or r.get("popcen_households", "") or "").replace(".0", "") if is_in_pop else ""
            nhh_item = QTableWidgetItem(nhh)
            nhh_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table_matching.setItem(row_idx, 12, nhh_item)

            # 13: EA Subject for Delineation
            delin = str(r.get("ea_subject_for_delineation", "") or r.get("popcen_delineation", "") or "").strip() if is_in_pop else ""
            delin_item = QTableWidgetItem(delin)
            delin_item.setTextAlignment(Qt.AlignCenter)
            if delin not in ("", "-", "0"):
                delin_item.setBackground(QColor("#fef3c7"))
                delin_item.setForeground(QColor("#b45309"))
            self.table_matching.setItem(row_idx, 13, delin_item)

            # 14: EA Subject for Merging
            merg = str(r.get("ea_subject_for_merging", "") or r.get("popcen_merging", "") or "").strip() if is_in_pop else ""
            merg_item = QTableWidgetItem(merg)
            merg_item.setTextAlignment(Qt.AlignCenter)
            if merg not in ("", "-", "0"):
                merg_item.setBackground(QColor("#ffedd5"))
                merg_item.setForeground(QColor("#c2410c"))
            self.table_matching.setItem(row_idx, 14, merg_item)

            # 15: Remarks (POPCEN)
            prem = str(r.get("popcen_remarks", "") or "") if is_in_pop else ""
            self.table_matching.setItem(row_idx, 15, QTableWidgetItem(prem))

            # 16: New Enumeration Area Code
            nean = str(r.get("new_enumeration_area_code") or r.get("new_ean") or "").replace(".0", "")
            nean_item = QTableWidgetItem(nean)
            nean_item.setFont(mono_font)
            nean_item.setTextAlignment(Qt.AlignCenter)
            if nean and nean != ean_val:
                nean_item.setBackground(QColor("#fef3c7"))
                nean_item.setForeground(QColor("#b45309"))
                n_font = nean_item.font()
                n_font.setBold(True)
                nean_item.setFont(n_font)
            self.table_matching.setItem(row_idx, 16, nean_item)

            # 17: Household Count (2026)
            hh_cnt = str(r.get("household_count") or r.get("earf_household_count") or r.get("earf_households") or "").replace(".0", "")
            if hh_cnt.lower() in ("nan", "none", "null"):
                hh_cnt = ""
            hh_cnt_item = QTableWidgetItem(hh_cnt)
            hh_cnt_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table_matching.setItem(row_idx, 17, hh_cnt_item)

            # 18: Building Count (2026)
            bldg_cnt = str(r.get("building_count") or r.get("earf_building_count") or "").replace(".0", "")
            if bldg_cnt.lower() in ("nan", "none", "null"):
                bldg_cnt = ""
            bldg_cnt_item = QTableWidgetItem(bldg_cnt)
            bldg_cnt_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table_matching.setItem(row_idx, 18, bldg_cnt_item)

            # 19: EA Type (Displayed based on GPKG / EARF)
            ea_t_val = str(r.get("ea_type") or r.get("ea_lifecycle") or "").strip().upper()
            ea_t_item = QTableWidgetItem(ea_t_val)
            ea_t_item.setTextAlignment(Qt.AlignCenter)
            if "RETAIN" in ea_t_val:
                ea_t_item.setForeground(QColor("#15803d"))
            elif "SPLIT" in ea_t_val:
                ea_t_item.setForeground(QColor("#1d4ed8"))
            elif "MERG" in ea_t_val:
                ea_t_item.setForeground(QColor("#b45309"))
            elif "DELIN" in ea_t_val or "RESET" in ea_t_val:
                ea_t_item.setForeground(QColor("#b91c1c"))
            self.table_matching.setItem(row_idx, 19, ea_t_item)

            # 20: Source Year
            sy = str(r.get("source_year", "") or "").replace(".0", "")
            if not sy:
                ea_life = str(r.get("ea_lifecycle") or "").upper()
                sy = "2024" if "RETAIN" in ea_life else "2026"
            sy_item = QTableWidgetItem(sy)
            sy_item.setFont(mono_font)
            sy_item.setTextAlignment(Qt.AlignCenter)
            self.table_matching.setItem(row_idx, 20, sy_item)

            # 21: Remarks (EARF)
            erem = str(r.get("earf_remarks") or r.get("remarks") or "")
            self.table_matching.setItem(row_idx, 21, QTableWidgetItem(erem))

            # 22: Recoded Enumeration Area (Option B: displayed exclusively in Column 22)
            rec_val = str(r.get("recoded_ean") or "").strip()
            recoded_item = QTableWidgetItem(rec_val)
            recoded_item.setFont(mono_font)
            recoded_item.setTextAlignment(Qt.AlignCenter)
            if rec_val:
                recoded_item.setForeground(QColor("#1e40af"))
                r_font = recoded_item.font()
                r_font.setBold(True)
                recoded_item.setFont(r_font)
            self.table_matching.setItem(row_idx, 22, recoded_item)

        self.table_matching.setUpdatesEnabled(True)

    def _on_matching_search_changed(self):
        self.matching_page = 0
        self._fetch_and_render_matching_page()

    def _on_matching_filter_changed(self):
        self.matching_page = 0
        self._fetch_and_render_matching_page()

    def _on_matching_page_size_changed(self):
        self.matching_page_size = int(self.cmb_match_page_size.currentText())
        self.matching_page = 0
        self._fetch_and_render_matching_page()

    def _matching_prev_page(self):
        if self.matching_page > 0:
            self.matching_page -= 1
            self._fetch_and_render_matching_page()

    def _matching_next_page(self):
        total_pages = max(1, (self.matching_total_filtered + self.matching_page_size - 1) // self.matching_page_size)
        if self.matching_page < total_pages - 1:
            self.matching_page += 1
            self._fetch_and_render_matching_page()

    def _run_live_matching(self):
        """
        Executes immediate outer-join cross-matching across the 3 EARF Post-Processing sub-tabs.
        """
        self._is_matching_cancelled = False
        self.btn_run_live_match.setEnabled(False)
        self.btn_run_live_match.setText("Matching...")
        if hasattr(self, "btn_match_stop"):
            self.btn_match_stop.setEnabled(True)
        QApplication.processEvents()
        try:
            # 1. Fetch Preliminary EARF (Excluding non-EA summary headers - Option A)
            earf_list_dict = collections.defaultdict(list)
            if self.db.is_earf_populated():
                e_rows, _ = self.db.query_earf_records(only_with_new_ea=False, limit=500000)
                for r in e_rows:
                    gc = str(r.get("geocode", "")).strip()
                    if not gc or gc.endswith("00000000"):
                        continue
                    new_e = str(r.get("new_enumeration_area_code", "") or r.get("new_ean", "") or "").strip()
                    hh_c = str(r.get("household_count", "") or r.get("number_of_households", "") or "").strip()
                    bldg_c = str(r.get("building_count", "") or r.get("number_of_buildings", "") or "").strip()
                    if gc.endswith("000000") and (not new_e or new_e == "000000") and not hh_c and not bldg_c:
                        continue
                    earf_list_dict[gc].append(r)
            elif not self.earf_df.empty:
                for _, r in self.earf_df.iterrows():
                    d = r.to_dict()
                    gc = str(d.get("geocode", "")).strip()
                    if not gc or gc.endswith("00000000"):
                        continue
                    new_e = str(d.get("new_enumeration_area_code", "") or d.get("new_ean", "") or "").strip()
                    hh_c = str(d.get("household_count", "") or d.get("number_of_households", "") or "").strip()
                    bldg_c = str(d.get("building_count", "") or d.get("number_of_buildings", "") or "").strip()
                    if gc.endswith("000000") and (not new_e or new_e == "000000") and not hh_c and not bldg_c:
                        continue
                    earf_list_dict[gc].append(d)

            # 2. Fetch EA GeoPackages
            gpkg_list_dict = collections.defaultdict(list)
            is_gpkg_db = getattr(self, "cmb_gpkg_source_mode", None) is not None and self.cmb_gpkg_source_mode.currentIndex() == 0

            def _add_gpkg_row(r_dict):
                gc = str(r_dict.get("geocode", "") or "").strip()
                if not gc:
                    bgy_gc = str(r_dict.get("bgy_geocode", "") or "").strip()
                    ean_val = str(r_dict.get("ean", "") or r_dict.get("new_ean", "") or "").strip()
                    if bgy_gc and ean_val:
                        gc = f"{bgy_gc}{ean_val.zfill(6)}"
                    elif r_dict.get("map_uuid"):
                        gc = f"GPKG_UNMATCHED_{r_dict.get('map_uuid')}"
                    elif r_dict.get("fid"):
                        gc = f"GPKG_UNMATCHED_{r_dict.get('fid')}"
                    elif r_dict.get("id"):
                        gc = f"GPKG_UNMATCHED_{r_dict.get('id')}"
                if gc and not gc.endswith("00000000"):
                    gpkg_list_dict[gc].append(r_dict)

            if is_gpkg_db and self.db.is_gpkg_populated():
                with self.db._get_gpkg_connection() as conn:
                    cur = conn.cursor()
                    cur.execute("SELECT * FROM gpkg_records")
                    for row in cur.fetchall():
                        _add_gpkg_row(dict(row))
            elif hasattr(self, "gpkg_df") and not self.gpkg_df.empty:
                for _, r in self.gpkg_df.iterrows():
                    _add_gpkg_row(r.to_dict())
            elif getattr(self, "txt_gpkg_preview_layers", None) and self.txt_gpkg_preview_layers.text().strip():
                try:
                    df = load_gpkg_data(self.txt_gpkg_preview_layers.text().strip())
                    if not df.empty:
                        self.gpkg_df = df
                        for _, r in df.iterrows():
                            _add_gpkg_row(r.to_dict())
                except Exception:
                    pass

            # Robust fallback: if gpkg_list_dict is still empty but database has records, load from database!
            if not gpkg_list_dict and self.db.is_gpkg_populated():
                with self.db._get_gpkg_connection() as conn:
                    cur = conn.cursor()
                    cur.execute("SELECT * FROM gpkg_records")
                    for row in cur.fetchall():
                        _add_gpkg_row(dict(row))
            elif not gpkg_list_dict and hasattr(self, "gpkg_df") and not self.gpkg_df.empty:
                for _, r in self.gpkg_df.iterrows():
                    _add_gpkg_row(r.to_dict())

            # Determine uploaded citymuns: only the citymun that has already uploaded in the db should appear and process
            uploaded_citymun_codes = set()
            for gc in earf_list_dict.keys():
                if len(gc) >= 5 and not gc[:5].endswith("00"):
                    uploaded_citymun_codes.add(gc[:5])
            for gc in gpkg_list_dict.keys():
                if len(gc) >= 5 and not gc[:5].endswith("00"):
                    uploaded_citymun_codes.add(gc[:5])
            if not self.popcen_df.empty:
                for gc in self.popcen_df["geocode"].astype(str):
                    if len(gc) >= 5 and not gc[:5].endswith("00"):
                        uploaded_citymun_codes.add(gc[:5])

            # 3. Fetch POPCEN only for the uploaded citymuns
            popcen_list_dict = collections.defaultdict(list)
            if self.cmb_source_mode.currentIndex() == 0 and self.db.is_populated():
                if uploaded_citymun_codes:
                    with self.db._get_connection() as conn:
                        cur = conn.cursor()
                        placeholders = ','.join('?' for _ in uploaded_citymun_codes)
                        cur.execute(f"""
                            SELECT * FROM popcen_records 
                            WHERE geographic_level LIKE '%5 - EA%' 
                              AND SUBSTR(geocode, 1, 5) IN ({placeholders})
                        """, list(uploaded_citymun_codes))
                        cols = [d[0] for d in cur.description]
                        for row in cur.fetchall():
                            r = dict(zip(cols, row))
                            gc = str(r.get("geocode", "")).strip()
                            if gc:
                                popcen_list_dict[gc].append(r)
                else:
                    rows, _ = self.db.query_records(level="5 - EA", limit=500000)
                    for r in rows:
                        gc = str(r.get("geocode", "")).strip()
                        if gc:
                            popcen_list_dict[gc].append(r)
            elif not self.popcen_df.empty:
                filtered_popcen, _ = exclude_non_ea_levels(self.popcen_df)
                for _, r in filtered_popcen.iterrows():
                    d = r.to_dict()
                    gc = str(d.get("geocode", "")).strip()
                    if gc:
                        popcen_list_dict[gc].append(d)

            if not popcen_list_dict and not earf_list_dict and not gpkg_list_dict:
                QMessageBox.warning(self, "No Sources", "Please ensure at least one data source is loaded or configured in the EARF Post-Processing workspace.")
                self.btn_run_live_match.setEnabled(True)
                self.btn_run_live_match.setText("Match Sources Now")
                return

            # Note: GeoPackage and Preliminary EARF are strictly Enumeration Area (EA) records
            # and must never be purged. POPCEN records are already filtered above
            # using WHERE geographic_level LIKE '%5 - EA%' or exclude_non_ea_levels().

            all_geocodes = sorted(list(set(popcen_list_dict.keys()) | set(earf_list_dict.keys()) | set(gpkg_list_dict.keys())))
            matched_records = []

            for idx, gc in enumerate(all_geocodes):
                if idx % 1000 == 0:
                    QApplication.processEvents()
                    if getattr(self, "_is_matching_cancelled", False):
                        break
                p_rows = popcen_list_dict.get(gc, [])
                e_rows = earf_list_dict.get(gc, [])
                g_rows = gpkg_list_dict.get(gc, [])

                num_entries = max(len(p_rows), len(e_rows), len(g_rows), 1)
                is_split_ea = num_entries > 1

                in_p = 1 if p_rows else 0
                in_e = 1 if e_rows else 0
                in_g = 1 if g_rows else 0

                # 7-State Match Status
                if in_p and in_e and in_g:
                    match_status = "MATCHED_ALL"
                elif in_p and in_g and not in_e:
                    match_status = "UNMATCHED_POPCEN_GPKG"
                elif in_p and in_e and not in_g:
                    match_status = "UNMATCHED_POPCEN_EARF"
                elif in_g and in_e and not in_p:
                    match_status = "UNMATCHED_GPKG_EARF"
                elif in_p and not in_e and not in_g:
                    match_status = "UNMATCHED_POPCEN"
                elif in_g and not in_p and not in_e:
                    match_status = "UNMATCHED_GPKG"
                else:
                    match_status = "UNMATCHED_EARF"

                base_p = p_rows[0] if p_rows else {}
                base_e = e_rows[0] if e_rows else {}
                base_g = g_rows[0] if g_rows else {}
                base_an = base_p.get("areaname") or base_e.get("areaname") or base_g.get("name") or base_p.get("geographic_name") or ""
                base_old_ean = base_p.get("ean") or base_e.get("ea") or base_g.get("ean") or ""
                base_old_ean = str(base_old_ean).strip().replace(".0", "")

                for idx in range(num_entries):
                    p_row = p_rows[idx] if idx < len(p_rows) else {}
                    e_row = e_rows[idx] if idx < len(e_rows) else {}
                    g_row = g_rows[idx] if idx < len(g_rows) else {}

                    in_p_row = 1 if p_row else 0
                    in_e_row = 1 if e_row else 0
                    in_g_row = 1 if g_row else 0

                    if in_p_row and in_e_row and in_g_row:
                        match_status = "MATCHED_ALL"
                    elif in_p_row and in_g_row and not in_e_row:
                        match_status = "UNMATCHED_POPCEN_GPKG"
                    elif in_p_row and in_e_row and not in_g_row:
                        match_status = "UNMATCHED_POPCEN_EARF"
                    elif in_g_row and in_e_row and not in_p_row:
                        match_status = "UNMATCHED_GPKG_EARF"
                    elif in_p_row and not in_e_row and not in_g_row:
                        match_status = "UNMATCHED_POPCEN"
                    elif in_g_row and not in_p_row and not in_e_row:
                        match_status = "UNMATCHED_GPKG"
                    else:
                        match_status = "UNMATCHED_EARF"

                    # 2024 POPCEN fields (Columns 1 to 15):
                    # Only populated if this row genuinely belongs to 2024 POPCEN table!
                    # If this is an additional row based on Prelim EARF inputs or GPKG, keep completely blank.
                    if in_p_row:
                        geo_name = str(p_row.get("geographic_name", "") or "").strip()
                        geo_level = str(p_row.get("geographic_level", "") or "5 - EA").strip()
                        reg_code = str(p_row.get("region_code", "") or "").strip()
                        prov_code = str(p_row.get("province_code", "") or "").strip()
                        mun_code = str(p_row.get("city_mun_code", "") or "").strip()
                        brgy_code = str(p_row.get("barangay_code", "") or "").strip()
                        old_ean = to_whole_number_str(p_row.get("ean", "") or "")
                        if old_ean.isdigit() and len(old_ean) < 6:
                            old_ean = f"{int(old_ean):03d}000" if len(old_ean) <= 3 else f"{int(old_ean):06d}"
                        no_eas = str(p_row.get("no_of_eas", "") or "").strip()
                        an = str(p_row.get("areaname", "") or "").strip()
                        hh_pop = str(p_row.get("household_population", "") or "").strip()
                        phh = str(p_row.get("no_of_households", "") or "").strip()
                        delin_flag = str(p_row.get("ea_subject_for_delineation", "") or "").strip()
                        merge_flag = str(p_row.get("ea_subject_for_merging", "") or "").strip()
                        gap_flag = str(p_row.get("ea_with_gap_overlap", "") or "").strip()
                        p_remarks = str(p_row.get("remarks", "") or "").strip()
                    else:
                        geo_name = ""
                        geo_level = ""
                        reg_code = ""
                        prov_code = ""
                        mun_code = ""
                        brgy_code = ""
                        # For internal grouping / export support, extract PSGC codes if available
                        if len(gc) == 14 and gc.isdigit():
                            prov_code = gc[:3]
                            mun_code = gc[3:5]
                            brgy_code = gc[5:8]
                        elif len(gc) == 16 and gc.isdigit():
                            reg_code = gc[:2]
                            prov_code = gc[2:5]
                            mun_code = gc[5:7]
                            brgy_code = gc[7:10]
                        old_ean = ""
                        no_eas = ""
                        an = ""
                        hh_pop = ""
                        phh = ""
                        delin_flag = ""
                        merge_flag = ""
                        gap_flag = ""
                        p_remarks = ""

                    ehh = str(e_row.get("household_count") or e_row.get("number_of_households") or g_row.get("hhcount") or g_row.get("hh_count") or "").strip()

                    earf_hh_2026 = str(e_row.get("household_count") or e_row.get("earf_household_count") or "").strip()
                    if not earf_hh_2026:
                        for k in ("household count (2026)", "household count", "household_count_2026", "hh_count", "hhcount"):
                            for rk, rv in e_row.items():
                                if str(rk).strip().lower() == k and pd.notna(rv):
                                    earf_hh_2026 = str(rv).strip()
                                    break
                            if earf_hh_2026:
                                break
                    if earf_hh_2026.endswith(".0"):
                        earf_hh_2026 = earf_hh_2026[:-2]
                    if earf_hh_2026.lower() in ("nan", "none", "null"):
                        earf_hh_2026 = ""

                    earf_bldg_2026 = str(e_row.get("building_count") or e_row.get("earf_building_count") or "").strip()
                    if not earf_bldg_2026:
                        for k in ("building count (2026)", "building count", "building_count_2026", "bldg_count", "bldgcount"):
                            for rk, rv in e_row.items():
                                if str(rk).strip().lower() == k and pd.notna(rv):
                                    earf_bldg_2026 = str(rv).strip()
                                    break
                            if earf_bldg_2026:
                                break
                    if earf_bldg_2026.endswith(".0"):
                        earf_bldg_2026 = earf_bldg_2026[:-2]
                    if earf_bldg_2026.lower() in ("nan", "none", "null"):
                        earf_bldg_2026 = ""

                    diff_str = ""
                    try:
                        p_val = float(phh) if phh and phh not in ("-", "nan", "None") else None
                        e_val = float(ehh) if ehh and ehh not in ("-", "nan", "None") else None
                        if p_val is not None and e_val is not None:
                            diff = int(round(e_val - p_val))
                            diff_str = f"{diff:+d}" if diff != 0 else "0"
                    except Exception:
                        diff_str = ""

                    # Explicit new EA code from Preliminary EARF or GPKG
                    new_ean_raw = e_row.get("new_enumeration_area_code") or e_row.get("new_ean") or g_row.get("new_ean") or ""
                    if not new_ean_raw and g_row.get("code"):
                        g_code = str(g_row.get("code", "")).strip().replace(".0", "")
                        if len(g_code) == 6 and g_code.isdigit():
                            new_ean_raw = g_code
                    if not new_ean_raw:
                        cand = str(e_row.get("ea") or g_row.get("ean") or g_row.get("new_ean") or "").strip().replace(".0", "")
                        cand = to_whole_number_str(cand)
                        if cand and cand != "1004":
                            new_ean_raw = cand
                        elif len(gc) >= 6 and gc[-6:].isdigit():
                            new_ean_raw = gc[-6:]

                    new_ean = to_whole_number_str(new_ean_raw)
                    if new_ean.isdigit() and len(new_ean) < 6:
                        new_ean = f"{int(new_ean):03d}000" if len(new_ean) <= 3 else f"{int(new_ean):06d}"

                    is_reset = 1 if (new_ean and old_ean and new_ean != old_ean) else 0

                    new_ea_code = new_ean if new_ean else "" 

                    ea_t = str(e_row.get("ea_type") or g_row.get("ea_type") or "").strip()
                    rem = str(e_row.get("remarks") or g_row.get("remarks") or (p_row.get("remarks") if in_p_row else "") or "").strip()

                    if is_split_ea or "split" in ea_t.lower() or "split" in rem.lower():
                        lifecycle = "SPLIT"
                    elif "merg" in ea_t.lower() or "merg" in rem.lower():
                        lifecycle = "MERGED"
                    elif is_reset == 1 or "delineat" in ea_t.lower() or "delineat" in rem.lower():
                        lifecycle = "RESET"
                    elif "retain" in ea_t.lower():
                        lifecycle = "RETAINED"
                    elif in_p_row and not in_e_row and not in_g_row:
                        lifecycle = "BASELINE"
                    else:
                        lifecycle = "RETAINED" if not is_reset else "RESET"

                    has_geom = 1 if (g_row and str(g_row.get("geom_status", "")).lower() != "no geometry") else 0

                    # Household and Building counts:
                    # GeoPackage hh_count and bldg_count are the primary authoritative values
                    g_hh = str(g_row.get("hh_count") or g_row.get("hhcount") or "").strip()
                    g_bldg = str(g_row.get("bldg_count") or g_row.get("bldgcount") or "").strip()
                    if g_hh.endswith(".0"):
                        g_hh = g_hh[:-2]
                    if g_bldg.endswith(".0"):
                        g_bldg = g_bldg[:-2]
                    if g_hh.lower() in ("nan", "none", "null"):
                        g_hh = ""
                    if g_bldg.lower() in ("nan", "none", "null"):
                        g_bldg = ""

                    # 2026 EARF fields (16 to 21)
                    hh_cnt = g_hh or earf_hh_2026 or ehh
                    bldg_cnt = g_bldg or earf_bldg_2026 or str(e_row.get("building_count") or "").strip()
                    if bldg_cnt.endswith(".0"):
                        bldg_cnt = bldg_cnt[:-2]
                    if bldg_cnt.lower() in ("nan", "none", "null"):
                        bldg_cnt = ""
                    ea_type_val = ea_t or lifecycle
                    gpkg_ea_type = str(g_row.get("ea_type") or g_row.get("type") or g_row.get("code") or e_row.get("ea_type") or ea_type_val or "").strip().upper()
                    src_year = to_whole_number_str(e_row.get("source_year") or g_row.get("sy") or "")
                    if not src_year:
                        src_year = "2024" if "RETAIN" in gpkg_ea_type.upper() else "2026"
                    e_remarks = str(e_row.get("remarks") or g_row.get("remarks") or "").strip()

                    rec = {
                        "geocode": gc,
                        "geographic_name": geo_name,
                        "geographic_level": geo_level,
                        "region_code": reg_code,
                        "province_code": prov_code,
                        "city_mun_code": mun_code,
                        "barangay_code": brgy_code,
                        "ean": old_ean,
                        "no_of_eas": no_eas,
                        "areaname": an,
                        "household_population": hh_pop,
                        "no_of_households": phh,
                        "ea_subject_for_delineation": delin_flag,
                        "ea_subject_for_merging": merge_flag,
                        "popcen_remarks": p_remarks,
                        "new_enumeration_area_code": new_ea_code,
                        "household_count": hh_cnt,
                        "building_count": bldg_cnt,
                        "ea_type": gpkg_ea_type,
                        "source_year": src_year,
                        "earf_remarks": e_remarks,
                        "recoded_ean": "",
                        "match_status": match_status,
                        "in_popcen": in_p_row,
                        "in_earf": in_e_row,
                        "in_gpkg": in_g_row,
                        "popcen_households": phh,
                        "earf_households": ehh,
                        "household_diff": diff_str,
                        "earf_household_count": hh_cnt,
                        "earf_building_count": bldg_cnt,
                        "popcen_delineation": delin_flag,
                        "popcen_merging": merge_flag,
                        "popcen_gap_overlap": gap_flag,
                        "new_ean": new_ea_code,
                        "is_reset": is_reset,
                        "ea_lifecycle": lifecycle,
                        "remarks": e_remarks or p_remarks or rem,
                        "gpkg_layers": str(g_row.get("layer_source") or g_row.get("source_file") or ""),
                        "has_geometry": has_geom,
                        "geom_type": str(g_row.get("geom_status") or ("Polygon" if has_geom else "No Geometry")),
                        "gpkg_hh_count": g_hh,
                        "gpkg_bldg_count": g_bldg,
                    }
                    if not is_summary_header_record(rec):
                        matched_records.append(rec)

            # Compute Recoded Enumeration Area (Column 22) per Barangay
            if hasattr(self.db, "compute_recoded_enumeration_areas"):
                self.db.compute_recoded_enumeration_areas(matched_records)
            elif "compute_recoded_enumeration_areas" in globals():
                compute_recoded_enumeration_areas(matched_records)

            if getattr(self, "_is_matching_cancelled", False):
                if hasattr(self, "lbl_match_page_info"):
                    self.lbl_match_page_info.setText("Matching stopped by user.")
                return

            # Store in database
            self.db.store_matched_records(matched_records, replace=True)

            self.matching_page = 0
            self._check_and_load_matching_data()

            QMessageBox.information(
                self,
                "Matching Complete",
                f"Successfully reconciled {len(matched_records):,} EAs across all 3 data sources!\n\n"
                f"Fully Matched: {self.kpi_match_all.val_label.text()}\n"
                f"Reset / Delineated EAs: {self.kpi_match_reset.val_label.text()}"
            )
        except Exception as e:
            QMessageBox.critical(self, "Matching Error", f"Failed to perform live matching: {e}")
        finally:
            self.btn_run_live_match.setEnabled(True)
            self.btn_run_live_match.setText("Match Sources Now")
            if hasattr(self, "btn_match_stop"):
                self.btn_match_stop.setEnabled(False)

    def _stop_matching(self):
        """Immediately stops the running geocode matching process."""
        self._is_matching_cancelled = True
        if hasattr(self, "worker") and self.worker and self.worker.isRunning():
            self.worker.cancel()
        if hasattr(self, "btn_match_stop"):
            self.btn_match_stop.setEnabled(False)
        self.btn_run_live_match.setEnabled(True)
        self.btn_run_live_match.setText("Match Sources Now")
        if hasattr(self, "lbl_match_page_info"):
            self.lbl_match_page_info.setText("Matching stopped by user.")

    def _browse_match_export_dir(self):
        curr_dir = self.txt_match_export_dir.text().strip() or getattr(self, "_final_export_root_dir", None) or DEFAULT_FINAL_EXPORT_ROOT
        if not curr_dir or not os.path.exists(curr_dir):
            curr_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Root Folder Directory for Final Export",
            curr_dir,
            QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks
        )
        if folder:
            self._final_export_root_dir = folder
            self.txt_match_export_dir.setText(folder)
            self._update_export_dir_for_current_citymun()

    def _search_export_folder_directory(self):
        """Interactive folder directory search dialog that scans and filters directories in real time."""
        root_dir = getattr(self, "_final_export_root_dir", None) or DEFAULT_FINAL_EXPORT_ROOT
        if not os.path.exists(root_dir):
            root_dir = self.txt_match_export_dir.text().strip()
        if not os.path.exists(root_dir):
            root_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

        dlg = QDialog(self)
        dlg.setWindowTitle("Folder Directory Search")
        dlg.resize(650, 480)
        dlg_layout = QVBoxLayout(dlg)

        top_info = QLabel(f"<b>Root Directory:</b> {root_dir}")
        top_info.setWordWrap(True)
        dlg_layout.addWidget(top_info)

        search_edit = QLineEdit()
        search_edit.setPlaceholderText("Type municipality, province, or folder name to search...")
        dlg_layout.addWidget(search_edit)

        list_widget = QListWidget()
        dlg_layout.addWidget(list_widget)

        # Collect subdirectories (up to 3 levels deep)
        all_dirs = []
        try:
            for r_path, dirs, _ in os.walk(root_dir):
                depth = r_path[len(root_dir):].count(os.sep)
                if depth > 2:
                    dirs.clear()
                    continue
                for d in dirs:
                    if not is_version_dir(d):
                        full_p = os.path.join(r_path, d)
                        rel_p = os.path.relpath(full_p, root_dir)
                        all_dirs.append((rel_p, full_p))
        except Exception:
            pass

        def _update_filter(text: str):
            list_widget.clear()
            query = text.strip().lower()
            for rel_p, full_p in all_dirs:
                if not query or query in rel_p.lower():
                    item = QListWidgetItem(f"📁 {rel_p}")
                    item.setData(Qt.UserRole, full_p)
                    list_widget.addItem(item)

        _update_filter("")
        search_edit.textChanged.connect(_update_filter)

        btn_box = QHBoxLayout()
        btn_box.addStretch()
        btn_select = QPushButton("Select Folder")
        btn_select.setObjectName("primaryBtn")
        btn_cancel = QPushButton("Cancel")
        btn_box.addWidget(btn_cancel)
        btn_box.addWidget(btn_select)
        dlg_layout.addLayout(btn_box)

        selected_folder = []

        def _on_accept():
            cur = list_widget.currentItem()
            if cur:
                selected_folder.append(cur.data(Qt.UserRole))
                dlg.accept()

        btn_select.clicked.connect(_on_accept)
        list_widget.itemDoubleClicked.connect(lambda _: _on_accept())
        btn_cancel.clicked.connect(dlg.reject)

        if dlg.exec_() == QDialog.Accepted and selected_folder:
            chosen = selected_folder[0]
            self.txt_match_export_dir.setText(chosen)
            self._final_export_root_dir = os.path.dirname(chosen)

    def _cancel_deliverables_export(self):
        """Cancels the active DeliverablesExportWorker."""
        if hasattr(self, "deliverables_worker") and self.deliverables_worker and self.deliverables_worker.isRunning():
            self.deliverables_worker.cancel()
        if hasattr(self, "export_progress_dlg") and self.export_progress_dlg:
            self.export_progress_dlg.setLabelText("Cancelling export... please wait.")

    def _on_deliverables_export_progress(self, pct: int, msg: str):
        """Handles live progress updates from DeliverablesExportWorker."""
        if hasattr(self, "export_progress_dlg") and self.export_progress_dlg:
            self.export_progress_dlg.setValue(pct)
            self.export_progress_dlg.setLabelText(msg)
        if hasattr(self, "btn_export_match_gpkg") and self.btn_export_match_gpkg:
            self.btn_export_match_gpkg.setText(f"Exporting {pct}%...")

    def _on_deliverables_task_completed(self, city_mun_code: str, task_res: Dict[str, Any]):
        """Incrementally updates monitoring dashboard in real-time as each municipality finishes."""
        if city_mun_code:
            try:
                self._update_monitoring_for_citymuns([city_mun_code])
            except Exception:
                pass

    def _on_deliverables_export_finished(
        self,
        summary: Dict[str, Any],
        err_msg: str,
        is_batch: bool = False,
        post_action_ask_canvas: bool = False
    ):
        """Cleans up the export dialog and presents results to the user."""
        if hasattr(self, "export_progress_dlg") and self.export_progress_dlg:
            self.export_progress_dlg.close()
            self.export_progress_dlg = None

        if hasattr(self, "btn_export_match_gpkg") and self.btn_export_match_gpkg:
            self.btn_export_match_gpkg.setEnabled(True)
            self.btn_export_match_gpkg.setText("Export final data")

        if hasattr(self, "btn_mon_export_now") and self.btn_mon_export_now:
            self.btn_mon_export_now.setEnabled(True)

        if err_msg == "Cancelled":
            QMessageBox.information(self, "Export Cancelled", "The deliverables export was cancelled.")
            return

        if err_msg:
            QMessageBox.critical(self, "Export Failed", f"Deliverables export failed: {err_msg}")
            return

        results = summary.get("results", [])
        total_exported = summary.get("total_exported", 0)
        total_features = summary.get("total_features", 0)

        # Batch Export feedback
        if is_batch:
            root_dir = getattr(self, "_final_export_root_dir", None) or DEFAULT_FINAL_EXPORT_ROOT
            QMessageBox.information(
                self,
                "Batch Export Complete",
                f"Successfully exported {total_exported:,} processed City/Municipalities ({total_features:,} total features) into their respective Region/Province/CityMun subfolders under:\n{root_dir}\n\n"
                f"Final deliverables generated per City/Municipality:\n"
                f"• Final GeoPackage (<pppmm>_ea2026.gpkg)\n"
                f"• Final EARF Excel (<pppmm>_final_earf_<citymun>.xlsx)\n"
                f"• Final MIS Excel (<pppmm>_MIS_<citymun>.xlsx)\n"
                f"• PCD EARF Excel (<pppmm>_earf2026_PCD.xlsx)"
            )
            return

        # Single Municipality feedback
        if results:
            res = results[0]
            if res.get("success"):
                mun_name = res.get("mun_name", res.get("city_mun", ""))
                c_code = res.get("city_mun", "")
                pppmm = c_code[:5] if len(c_code) >= 5 else c_code
                clean_cm = res.get("clean_cm", "ea2026")
                target_folder = os.path.dirname(res["gpkg_path"])
                out_filename = os.path.basename(res["gpkg_path"])
                out_excel_filename = os.path.basename(res["excel_path"])
                out_mis_filename = os.path.basename(res["mis_path"])
                out_pcd_filename = os.path.basename(res.get("pcd_path") or f"{pppmm}_earf2026_PCD.xlsx")
                geom_count = res.get("geom_count", 0)
                count = res.get("count", 0)

                self._last_exported_gpkg_path = res["gpkg_path"]
                self._last_exported_layer_display_name = f"{pppmm}_ea2026 - {clean_cm}"

                if post_action_ask_canvas:
                    msg = (
                        f"Successfully exported final deliverables for {mun_name}!\n\n"
                        f"Folder: {target_folder}\n"
                        f"• Final GeoPackage: {out_filename}\n"
                        f"• Final EARF Excel: {out_excel_filename}\n"
                        f"• Final MIS Excel: {out_mis_filename}\n"
                        f"• PCD EARF Excel: {out_pcd_filename}\n\n"
                        f"GeoPackage Features: {geom_count:,} (Retained as delineated in GeoPackage)\n\n"
                        f"Would you like to add the GeoPackage layer to the QGIS map canvas?"
                    )
                    reply = QMessageBox.question(
                        self, "Export Complete", msg, QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
                    )
                    if reply == QMessageBox.Yes:
                        layer_display_name = f"{pppmm}_ea2026 - {clean_cm}"
                        vlayer = QgsVectorLayer(res["gpkg_path"], layer_display_name, "ogr")
                        if vlayer.isValid():
                            QgsProject.instance().addMapLayer(vlayer)
                else:
                    QMessageBox.information(
                        self,
                        "Export Complete",
                        f"Successfully generated deliverables for {c_code} ({mun_name})!\n\n"
                        f"Folder: {target_folder}\n"
                        f"• Final GeoPackage: {out_filename}\n"
                        f"• Final EARF Excel: {out_excel_filename}\n"
                        f"• Final MIS Excel: {out_mis_filename}\n"
                        f"• PCD EARF Excel: {out_pcd_filename}\n\n"
                        f"Total Features: {count:,} (with Geometry: {geom_count:,})"
                    )
            elif res.get("error"):
                QMessageBox.warning(
                    self,
                    "Export Warning",
                    f"Could not export deliverables for {res.get('mun_name', res.get('city_mun'))}: {res['error']}"
                )

    def _start_deliverables_export(
        self,
        tasks: List[Dict[str, Any]],
        is_batch: bool = False,
        post_action_ask_canvas: bool = False
    ):
        """
        Launches DeliverablesExportWorker in a background QThread with a responsive
        QProgressDialog to prevent GUI freezing.
        """
        if not tasks:
            return

        if hasattr(self, "deliverables_worker") and self.deliverables_worker and self.deliverables_worker.isRunning():
            QMessageBox.warning(self, "Export in Progress", "A deliverables export is already running in the background.")
            return

        # Disable trigger buttons during export
        if hasattr(self, "btn_export_match_gpkg") and self.btn_export_match_gpkg:
            self.btn_export_match_gpkg.setEnabled(False)
            self.btn_export_match_gpkg.setText("Exporting...")

        if hasattr(self, "btn_mon_export_now") and self.btn_mon_export_now:
            self.btn_mon_export_now.setEnabled(False)

        # Create responsive progress dialog
        self.export_progress_dlg = QProgressDialog(
            "Initializing deliverables export...",
            "Cancel",
            0,
            100,
            self
        )
        self.export_progress_dlg.setWindowTitle("Exporting Final Deliverables")
        self.export_progress_dlg.setWindowModality(Qt.WindowModal)
        self.export_progress_dlg.setMinimumDuration(0)
        self.export_progress_dlg.setValue(0)
        self.export_progress_dlg.setAutoClose(False)
        self.export_progress_dlg.setAutoReset(False)
        self.export_progress_dlg.canceled.connect(self._cancel_deliverables_export)
        self.export_progress_dlg.show()
        QApplication.processEvents()

        # Instantiate background worker
        self.deliverables_worker = DeliverablesExportWorker(db=self.db, tasks=tasks)
        self.deliverables_worker.progress_signal.connect(self._on_deliverables_export_progress)
        self.deliverables_worker.task_completed_signal.connect(self._on_deliverables_task_completed)
        self.deliverables_worker.finished_signal.connect(
            lambda summary, err: self._on_deliverables_export_finished(
                summary, err, is_batch=is_batch, post_action_ask_canvas=post_action_ask_canvas
            )
        )
        self.deliverables_worker.start()

    def _export_matching_gpkg(self):
        export_dir = self.txt_match_export_dir.text().strip()
        root_dir = getattr(self, "_final_export_root_dir", None) or DEFAULT_FINAL_EXPORT_ROOT
        if not os.path.exists(root_dir):
            if export_dir and os.path.exists(export_dir):
                root_dir = export_dir
            else:
                root_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

        # Gather geometry sources from preview inputs and active layers (as string paths)
        geom_sources = []
        if hasattr(self, "txt_gpkg_preview_layers") and self.txt_gpkg_preview_layers.text().strip():
            geom_sources.extend(resolve_gpkg_sources(self.txt_gpkg_preview_layers.text().strip()))
        if hasattr(self, "txt_gpkg_layers") and self.txt_gpkg_layers.text().strip():
            geom_sources.extend(resolve_gpkg_sources(self.txt_gpkg_layers.text().strip()))
        if hasattr(self, "txt_gpkg_folder") and self.txt_gpkg_folder.text().strip():
            folder_srcs = find_gpkg_files_in_folder(self.txt_gpkg_folder.text().strip(), recursive=True)
            geom_sources.extend(folder_srcs)

        # Also check open project polygon layers
        try:
            for l in QgsProject.instance().mapLayers().values():
                if isinstance(l, QgsVectorLayer) and is_polygon_layer(l):
                    src = l.source()
                    if src:
                        geom_sources.append(src)
        except Exception:
            pass

        cm = getattr(self, "cmb_match_citymun_filter", None)
        selected_cm = cm.currentData() if cm else None

        # Case 1: A specific City/Mun is selected in the filter
        if selected_cm:
            target_folder, hier = self.db.resolve_final_export_subfolder(root_dir, selected_cm)
            if not target_folder:
                target_folder = export_dir if os.path.exists(export_dir) else root_dir
            os.makedirs(target_folder, exist_ok=True)
            self.txt_match_export_dir.setText(target_folder)

            mun_name = hier.get("city_mun") or cm.currentText()
            clean_cm = re.sub(r'\s*\([^)]*\)', '', mun_name)
            clean_cm = re.sub(r'[^a-zA-Z0-9]+', '_', clean_cm).strip('_').lower()
            if not clean_cm:
                clean_cm = "ea2026"
            pppmm = selected_cm[:5] if len(selected_cm) >= 5 else selected_cm
            out_filename = f"{pppmm}_ea2026.gpkg"
            out_excel_filename = f"{pppmm}_final_earf_{clean_cm}.xlsx"
            out_mis_filename = f"{pppmm}_MIS_{clean_cm}.xlsx"
            out_pcd_filename = f"{pppmm}_earf2026_PCD.xlsx"
            out_path = os.path.join(target_folder, out_filename)
            out_excel_path = os.path.join(target_folder, out_excel_filename)
            out_mis_path = os.path.join(target_folder, out_mis_filename)
            out_pcd_path = os.path.join(target_folder, out_pcd_filename)

            if os.path.exists(out_path) or os.path.exists(out_excel_path) or os.path.exists(out_mis_path) or os.path.exists(out_pcd_path):
                existing_files = [f for f in (out_filename, out_excel_filename, out_mis_filename, out_pcd_filename) if os.path.exists(os.path.join(target_folder, f))]
                reply = QMessageBox.question(
                    self,
                    "Confirm Overwrite",
                    f"The following file(s) already exist in:\n{target_folder}\n\n" + "\n".join(f"• {f}" for f in existing_files) + "\n\nDo you want to overwrite them?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No
                )
                if reply != QMessageBox.Yes:
                    return

            task = {
                "city_mun": selected_cm,
                "mun_name": mun_name,
                "clean_cm": clean_cm,
                "target_folder": target_folder,
                "out_gpkg_path": out_path,
                "out_excel_path": out_excel_path,
                "out_mis_path": out_mis_path,
                "out_pcd_path": out_pcd_path,
                "search": self.search_match.text().strip(),
                "status": self.cmb_match_status_filter.currentText(),
                "ea_lifecycle": self.cmb_match_lifecycle_filter.currentText(),
                "geometry_sources": geom_sources,
                "layer_name": clean_cm,
            }
            self._start_deliverables_export([task], is_batch=False, post_action_ask_canvas=True)

        # Case 2: No specific City/Mun selected -> batch export all processed citymuns
        else:
            processed_citymuns = self.db.get_processed_citymuns()
            if not processed_citymuns:
                QMessageBox.warning(
                    self,
                    "No Processed Records",
                    "No processed City/Municipality records found in the database to export.\n\n"
                    "Please ensure matching / reconciliation has been performed first."
                )
                return

            reply = QMessageBox.question(
                self,
                "Export All Processed City/Municipalities",
                f"No specific City/Municipality is selected.\n\n"
                f"Would you like to export all {len(processed_citymuns)} processed City/Municipalities into their respective subfolders under:\n"
                f"{root_dir}?\n\n"
                f"Each City/Municipality will generate:\n"
                f"  • Final GeoPackage: <pppmm>_ea2026.gpkg\n"
                f"  • Final EARF Excel: <pppmm>_final_earf_<citymun>.xlsx\n"
                f"  • Final MIS Excel: <pppmm>_MIS_<citymun>.xlsx\n"
                f"  • PCD EARF Excel: <pppmm>_earf2026_PCD.xlsx",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes
            )
            if reply != QMessageBox.Yes:
                return

            tasks = []
            for cm_item in processed_citymuns:
                c_code = cm_item["code"]
                target_folder, hier = self.db.resolve_final_export_subfolder(root_dir, c_code)
                if not target_folder:
                    target_folder = export_dir if os.path.exists(export_dir) else root_dir
                os.makedirs(target_folder, exist_ok=True)
                m_name = hier.get("city_mun") or cm_item["name"]
                clean_cm = re.sub(r'\s*\([^)]*\)', '', m_name)
                clean_cm = re.sub(r'[^a-zA-Z0-9]+', '_', clean_cm).strip('_').lower()
                if not clean_cm:
                    clean_cm = "ea2026"
                pppmm = c_code[:5] if len(c_code) >= 5 else c_code
                c_out_filename = f"{pppmm}_ea2026.gpkg"
                c_out_excel_filename = f"{pppmm}_final_earf_{clean_cm}.xlsx"
                c_out_mis_filename = f"{pppmm}_MIS_{clean_cm}.xlsx"
                c_out_pcd_filename = f"{pppmm}_earf2026_PCD.xlsx"
                tasks.append({
                    "city_mun": c_code,
                    "mun_name": m_name,
                    "clean_cm": clean_cm,
                    "target_folder": target_folder,
                    "out_gpkg_path": os.path.join(target_folder, c_out_filename),
                    "out_excel_path": os.path.join(target_folder, c_out_excel_filename),
                    "out_mis_path": os.path.join(target_folder, c_out_mis_filename),
                    "out_pcd_path": os.path.join(target_folder, c_out_pcd_filename),
                    "search": self.search_match.text().strip(),
                    "status": self.cmb_match_status_filter.currentText(),
                    "ea_lifecycle": self.cmb_match_lifecycle_filter.currentText(),
                    "geometry_sources": geom_sources,
                    "layer_name": clean_cm,
                })

            self._start_deliverables_export(tasks, is_batch=True, post_action_ask_canvas=False)

    def _export_output_summary_excel(self):
        """Exports formal Output Summary Excel report (.xlsx) with 2 sheets: Output Summary & Validation Flags."""
        cm = getattr(self, "cmb_match_citymun_filter", None)
        selected_cm = cm.currentData() if cm else None
        flag_val = getattr(self, "cmb_match_flag_filter", None)
        selected_flag = flag_val.currentText() if flag_val else None

        default_name = "Output_Summary_Report.xlsx"
        if selected_cm:
            pppmm = selected_cm[:5] if len(selected_cm) >= 5 else selected_cm
            mun_name = cm.currentText()
            clean_cm = re.sub(r'\s*\([^)]*\)', '', mun_name)
            clean_cm = re.sub(r'[^a-zA-Z0-9]+', '_', clean_cm).strip('_').lower()
            if clean_cm:
                default_name = f"{pppmm}_output_summary_{clean_cm}.xlsx"

        f, _ = QFileDialog.getSaveFileName(
            self,
            "Export Output Summary & Validation Flags",
            default_name,
            "Excel Workbook (*.xlsx)"
        )
        if not f:
            return
        if not f.lower().endswith(".xlsx"):
            f += ".xlsx"

        try:
            res = self.db.export_output_summary_to_excel(
                out_excel=f,
                search=self.search_match.text().strip(),
                status=self.cmb_match_status_filter.currentText(),
                ea_lifecycle=self.cmb_match_lifecycle_filter.currentText(),
                flag_filter=selected_flag,
                city_mun=selected_cm,
                metadata={
                    "municipality": cm.currentText() if cm else "All",
                    "province": getattr(self, "_active_province", ""),
                    "region": getattr(self, "_active_region", "")
                }
            )
            if not res or not isinstance(res, dict):
                res = {"total_rows": 0, "total_flagged": 0}
            flagged = res.get("total_flagged", 0)
            rows = res.get("total_rows", 0)
            msg = (
                f"Successfully exported Output Summary table to:\n{f}\n\n"
                f"Records Audited: {rows:,}\n"
                f"Validation Flags Recorded: {flagged:,}\n\n"
                f"Sheet 1: 'Output Summary' (Hierarchical rollups & Flag counts)\n"
                f"Sheet 2: 'Validation Flags & Exceptions' (Itemized flag details & corrective notes)"
            )
            QMessageBox.information(self, "Export Complete", msg)
        except Exception as e:
            QMessageBox.critical(self, "Export Failed", f"Failed to export Output Summary: {e}")

    def _export_matching_csv(self):
        """Compatibility wrapper forwarding to Output Summary Excel export."""
        self._export_output_summary_excel()

    def _add_matching_layer_to_map(self):
        # 1. Check last exported GeoPackage if exists
        last_gpkg = getattr(self, "_last_exported_gpkg_path", None)
        if last_gpkg and os.path.exists(last_gpkg):
            disp = getattr(self, "_last_exported_layer_display_name", None) or os.path.splitext(os.path.basename(last_gpkg))[0]
            vlayer = QgsVectorLayer(last_gpkg, disp, "ogr")
            if vlayer.isValid():
                QgsProject.instance().addMapLayer(vlayer)
                QMessageBox.information(self, "Layer Added", f"Added GeoPackage layer to map canvas:\n{last_gpkg}")
                return

        # 2. Check if a GeoPackage exists for the current filtered City/Mun in export folder
        cm = getattr(self, "cmb_match_citymun_filter", None)
        selected_cm = cm.currentData() if cm else None
        export_folder = self.txt_match_export_dir.text().strip() if hasattr(self, "txt_match_export_dir") else ""
        if export_folder and os.path.exists(export_folder):
            if selected_cm:
                mun_name = cm.currentText()
                clean_cm = re.sub(r'\s*\([^)]*\)', '', mun_name)
                clean_cm = re.sub(r'[^a-zA-Z0-9]+', '_', clean_cm).strip('_').lower()
                pppmm = selected_cm[:5] if len(selected_cm) >= 5 else selected_cm
                candidate = os.path.join(export_folder, f"{pppmm}_ea2026.gpkg")
                if not os.path.exists(candidate):
                    safe_cm = re.sub(r'[\/*?:"<>|]', "", mun_name).replace(" ", "_").strip()
                    candidate = os.path.join(export_folder, f"{selected_cm}_{safe_cm}_reconciled_ea2026.gpkg")
                if os.path.exists(candidate):
                    disp = f"{pppmm}_ea2026 - {clean_cm}" if clean_cm else f"{pppmm}_ea2026"
                    vlayer = QgsVectorLayer(candidate, disp, "ogr")
                    if vlayer.isValid():
                        QgsProject.instance().addMapLayer(vlayer)
                        QMessageBox.information(self, "Layer Added", f"Added GeoPackage layer to map canvas:\n{candidate}")
                        return

        # 3. Check input GPKG preview layers in Tab 2
        gpkg_path = self.txt_gpkg_preview_layers.text().strip() if hasattr(self, "txt_gpkg_preview_layers") else ""
        if gpkg_path and os.path.exists(gpkg_path):
            vlayer = QgsVectorLayer(gpkg_path, "EA GeoPackage (Delineated)", "ogr")
            if vlayer.isValid():
                QgsProject.instance().addMapLayer(vlayer)
                QMessageBox.information(self, "Layer Added", f"Added EA GeoPackage layer to map canvas:\n{gpkg_path}")
                return

        QMessageBox.warning(self, "No Layer File", "Please export final data via 'Export final data' or specify an EA GeoPackage layer first.")

    # ----------------------------------------------------------------------
    # Tab 2: EARF Post-Processing Workspace (Contains 3 Sub-Tabs)
    # ----------------------------------------------------------------------
    def _setup_post_processing_tab(self):
        layout = QVBoxLayout(self.post_processing_tab)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self.sub_tabs = QTabWidget()
        self.sub_tabs.setObjectName("subTabs")
        self.sub_tabs.tabBar().setElideMode(Qt.ElideNone)

        # Sub-tab 1: 2024 POPCEN
        self.tab_popcen = QWidget()
        self._setup_popcen_subtab()
        self.sub_tabs.addTab(self.tab_popcen, "2024 POPCEN")

        # Sub-tab 2: Prelim EARF
        self.tab_earf = QWidget()
        self._setup_earf_subtab()
        self.sub_tabs.addTab(self.tab_earf, "Prelim EARF")

        # Sub-tab 3: GeoPackage
        self.tab_gpkg = QWidget()
        self._setup_gpkg_subtab()
        self.sub_tabs.addTab(self.tab_gpkg, "GeoPackage")

        layout.addWidget(self.sub_tabs)

    # ----------------------------------------------------------------------
    # Sub-Tab 1: POPCEN Table
    # ----------------------------------------------------------------------
    def _setup_popcen_subtab(self):
        layout = QVBoxLayout(self.tab_popcen)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # 1. Source Mode & Database Status Bar
        top_bar = QHBoxLayout()
        top_bar.addWidget(QLabel("<b>Data Source:</b>"))
        self.cmb_source_mode = QComboBox()
        self.cmb_source_mode.addItems([
            "Local Database (SQLite: popcen_database.db)",
            "Custom Excel / CSV File"
        ])
        self.cmb_source_mode.currentIndexChanged.connect(self._on_source_mode_changed)
        top_bar.addWidget(self.cmb_source_mode)

        self.lbl_db_badge = QLabel("Checking database...")
        self.lbl_db_badge.setObjectName("dbBadge")
        top_bar.addWidget(self.lbl_db_badge)

        top_bar.addStretch()

        self.btn_import_db = QPushButton("Import Excel into Local DB...")
        self.btn_import_db.setToolTip("Load and persist a POPCEN Excel/CSV file into the local SQLite database for instant future access")
        self.btn_import_db.clicked.connect(self._import_into_database_prompt)
        top_bar.addWidget(self.btn_import_db)

        self.btn_popcen_stop = QPushButton("Stop")
        self.btn_popcen_stop.setObjectName("stopBtn")
        self.btn_popcen_stop.setToolTip("Stop active POPCEN database import immediately")
        self.btn_popcen_stop.setEnabled(False)
        self.btn_popcen_stop.clicked.connect(self._stop_popcen_import)
        top_bar.addWidget(self.btn_popcen_stop)

        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.setObjectName("actionBtn")
        self.btn_refresh.clicked.connect(self._refresh_view)
        top_bar.addWidget(self.btn_refresh)

        layout.addLayout(top_bar)

        # File selection bar (visible when Custom File mode is selected)
        self.file_bar = QWidget()
        file_layout = QHBoxLayout(self.file_bar)
        file_layout.setContentsMargins(0, 0, 0, 0)
        file_layout.addWidget(QLabel("POPCEN File:"))
        self.txt_popcen_quick = QLineEdit()
        self.txt_popcen_quick.setPlaceholderText("Select custom POPCEN Excel file (.xlsx)...")
        file_layout.addWidget(self.txt_popcen_quick, 1)

        btn_browse_quick = QPushButton("Browse...")
        btn_browse_quick.clicked.connect(self._browse_popcen_file)
        file_layout.addWidget(btn_browse_quick)

        file_layout.addWidget(QLabel("Sheet:"))
        self.txt_sheet_quick = QLineEdit()
        self.txt_sheet_quick.setPlaceholderText("blank = 1st")
        self.txt_sheet_quick.setMaximumWidth(80)
        file_layout.addWidget(self.txt_sheet_quick)

        btn_load_file = QPushButton("Load File Preview")
        btn_load_file.clicked.connect(self._load_file_preview)
        file_layout.addWidget(btn_load_file)

        self.file_bar.setVisible(False)
        layout.addWidget(self.file_bar)

        # 2. KPI Summary Cards Frame
        kpi_frame = QFrame()
        kpi_frame.setObjectName("kpiFrame")
        kpi_frame.setAttribute(Qt.WA_StyledBackground, True)
        kpi_frame.setStyleSheet(
            "QFrame#kpiFrame { background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 6px; }"
        )
        kpi_layout = QHBoxLayout(kpi_frame)
        kpi_layout.setContentsMargins(8, 6, 8, 6)
        kpi_layout.setSpacing(10)

        self.kpi_total = self._create_kpi_card("Total Records", "0", "#0284c7")
        self.kpi_ea = self._create_kpi_card("Enumeration Areas (EA)", "0", "#059669")
        self.kpi_hh = self._create_kpi_card("Total Households", "0", "#4f46e5")
        self.kpi_delin = self._create_kpi_card("Subject for Delineation", "0", "#dc2626")
        self.kpi_merge = self._create_kpi_card("Subject for Merging", "0", "#ea580c")
        self.kpi_gap = self._create_kpi_card("Gap / Overlap Issues", "0", "#b45309")

        for k in (self.kpi_total, self.kpi_ea, self.kpi_hh, self.kpi_delin, self.kpi_merge, self.kpi_gap):
            kpi_layout.addWidget(k)

        layout.addWidget(kpi_frame)

        # 3. Filter, Search & Record Action Bar
        filter_bar = QHBoxLayout()

        self.chk_popcen_select_all = QCheckBox("Select All")
        self.chk_popcen_select_all.setStyleSheet("font-weight: 600; color: #1e293b;")
        self.chk_popcen_select_all.setToolTip("Select or deselect all records matching current filters across all pages in the database")
        self.chk_popcen_select_all.stateChanged.connect(self._on_popcen_select_all_toggled)
        filter_bar.addWidget(self.chk_popcen_select_all)

        self.btn_popcen_remove = QPushButton("Remove Selected")
        self.btn_popcen_remove.setObjectName("dangerBtn")
        self.btn_popcen_remove.setToolTip("Permanently remove all checked POPCEN records (cascades deletion across EARF, GPKG, and matching workspaces)")
        self.btn_popcen_remove.setEnabled(False)
        self.btn_popcen_remove.clicked.connect(self._remove_selected_popcen_records)
        filter_bar.addWidget(self.btn_popcen_remove)

        self.lbl_popcen_selected_count = QLabel("0 selected")
        self.lbl_popcen_selected_count.setStyleSheet("color: #64748b; font-size: 11px;")
        filter_bar.addWidget(self.lbl_popcen_selected_count)

        v_sep_popcen = QFrame()
        v_sep_popcen.setFrameShape(QFrame.VLine)
        v_sep_popcen.setFrameShadow(QFrame.Sunken)
        v_sep_popcen.setStyleSheet("color: #cbd5e1; margin: 2px 4px;")
        filter_bar.addWidget(v_sep_popcen)

        filter_bar.addWidget(QLabel("Search:"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search Name, Areaname, Geocode, EAN, Prov...")
        self.search_input.textChanged.connect(self._on_search_changed)
        filter_bar.addWidget(self.search_input, 1)

        filter_bar.addWidget(QLabel("Level:"))
        self.cmb_level_filter = QComboBox()
        self.cmb_level_filter.addItems([
            "5 - EA",
            "All Levels",
            "4 - Barangay",
            "3 - City/Municipality",
            "2 - Province/HUC",
            "1 - Region",
        ])
        self.cmb_level_filter.setCurrentText("5 - EA")
        self.cmb_level_filter.currentIndexChanged.connect(self._reset_and_filter)
        filter_bar.addWidget(self.cmb_level_filter)

        self.chk_popcen_only_ea = QCheckBox("5 - EA Only")
        self.chk_popcen_only_ea.setChecked(True)
        self.chk_popcen_only_ea.setToolTip("Only view rows with '5 - EA' values in Geographic Level")
        self.chk_popcen_only_ea.setStyleSheet("font-weight: 600; color: #0284c7;")
        self.chk_popcen_only_ea.stateChanged.connect(self._on_popcen_ea_toggled)
        filter_bar.addWidget(self.chk_popcen_only_ea)

        filter_bar.addWidget(QLabel("Filter Issue:"))
        self.cmb_issue_filter = QComboBox()
        self.cmb_issue_filter.addItems([
            "All Rows",
            "EA Rows Only",
            "Subject for Delineation",
            "Subject for Merging",
            "Gap / Overlap Issues",
            "No Issues Only",
        ])
        self.cmb_issue_filter.currentIndexChanged.connect(self._reset_and_filter)
        filter_bar.addWidget(self.cmb_issue_filter)

        filter_bar.addWidget(QLabel("Page Size:"))
        self.cmb_page_size = QComboBox()
        self.cmb_page_size.addItems(["200", "500", "1000", "2000", "5000"])
        self.cmb_page_size.setCurrentText("1000")
        self.cmb_page_size.currentIndexChanged.connect(self._on_page_size_changed)
        filter_bar.addWidget(self.cmb_page_size)

        # Pagination Buttons
        self.btn_prev_page = QPushButton("Prev")
        self.btn_prev_page.setFixedWidth(65)
        self.btn_prev_page.clicked.connect(self._prev_page)
        filter_bar.addWidget(self.btn_prev_page)

        self.lbl_page_info = QLabel("Page 1 of 1")
        self.lbl_page_info.setObjectName("pageInfo")
        filter_bar.addWidget(self.lbl_page_info)

        self.btn_next_page = QPushButton("Next")
        self.btn_next_page.setFixedWidth(65)
        self.btn_next_page.clicked.connect(self._next_page)
        filter_bar.addWidget(self.btn_next_page)

        layout.addLayout(filter_bar)

        # 4. Interactive Table Widget
        self.table_widget = QTableWidget()
        self.table_widget.setObjectName("previewTable")
        self.table_widget.setAlternatingRowColors(True)
        self.table_widget.setSortingEnabled(False)
        self.table_widget.horizontalHeader().setStretchLastSection(False)
        self.table_widget.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table_widget.verticalHeader().setDefaultSectionSize(26)
        self.table_widget.itemChanged.connect(self._on_popcen_table_item_changed)
        self.table_widget.horizontalHeader().sectionClicked.connect(self._on_popcen_header_clicked)
        self.table_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table_widget.customContextMenuRequested.connect(self._show_popcen_table_context_menu)
        layout.addWidget(self.table_widget, 1)
        self._enable_column_toggle(self.table_widget)

        # 5. Status text below table
        self.lbl_table_info = QLabel("Ready.")
        self.lbl_table_info.setObjectName("infoLabel")
        layout.addWidget(self.lbl_table_info)

    # ----------------------------------------------------------------------
    # Sub-Tab 2: Preliminary EARF Files (Folder Consolidation + Consistency)
    # ----------------------------------------------------------------------
    def _setup_earf_subtab(self):
        layout = QVBoxLayout(self.tab_earf)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # 1. Mode Selector Bar
        mode_bar = QHBoxLayout()
        mode_bar.addWidget(QLabel("<b>Data Source:</b>"))
        self.cmb_earf_source_mode = QComboBox()
        self.cmb_earf_source_mode.addItems([
            "Consolidated Database (SQLite: earf_database.db)",
            "Manual File Selection / Preview"
        ])
        self.cmb_earf_source_mode.currentIndexChanged.connect(self._on_earf_source_mode_changed)
        mode_bar.addWidget(self.cmb_earf_source_mode)

        self.lbl_earf_db_badge = QLabel("Checking EARF database...")
        self.lbl_earf_db_badge.setObjectName("dbBadge")
        mode_bar.addWidget(self.lbl_earf_db_badge)

        mode_bar.addStretch()

        self.btn_earf_refresh = QPushButton("Refresh")
        self.btn_earf_refresh.setObjectName("actionBtn")
        self.btn_earf_refresh.clicked.connect(self._refresh_earf_view)
        mode_bar.addWidget(self.btn_earf_refresh)

        layout.addLayout(mode_bar)

        # 2. Folder Consolidation Bar (Visible in Database Mode)
        self.earf_folder_bar = QWidget()
        folder_layout = QHBoxLayout(self.earf_folder_bar)
        folder_layout.setContentsMargins(0, 0, 0, 0)
        folder_layout.addWidget(QLabel("<b>EARF Folder:</b>"))

        self.txt_earf_folder = QLineEdit()
        self.txt_earf_folder.setPlaceholderText("Select directory containing Preliminary EARF files (*_earf*.xlsx)...")
        self.txt_earf_folder.setText(DEFAULT_EARF_ROOT)
        folder_layout.addWidget(self.txt_earf_folder, 1)

        btn_browse_folder = QPushButton("Browse Folder...")
        btn_browse_folder.clicked.connect(self._browse_earf_folder)
        folder_layout.addWidget(btn_browse_folder)

        self.chk_earf_recursive = QCheckBox("Subfolders")
        self.chk_earf_recursive.setChecked(True)
        self.chk_earf_recursive.setToolTip("Recursively scan nested subdirectories for Excel files")
        folder_layout.addWidget(self.chk_earf_recursive)

        self.btn_consolidate_earf = QPushButton("Consolidate Folder to DB...")
        self.btn_consolidate_earf.setObjectName("actionBtn")
        self.btn_consolidate_earf.setToolTip("Scan folder, parse all EARF Excel files (*_earf*), and save into local SQLite database")
        self.btn_consolidate_earf.clicked.connect(self._consolidate_folder_prompt)
        folder_layout.addWidget(self.btn_consolidate_earf)

        self.btn_run_consistency = QPushButton("Run Consistency Check")
        self.btn_run_consistency.setObjectName("primaryBtn")
        self.btn_run_consistency.setToolTip("Run R1 (Geocode Format) & R6 (POPCEN Match) consistency checks across consolidated records")
        self.btn_run_consistency.clicked.connect(self._run_consistency_check_prompt)
        folder_layout.addWidget(self.btn_run_consistency)

        self.btn_earf_stop = QPushButton("Stop")
        self.btn_earf_stop.setObjectName("stopBtn")
        self.btn_earf_stop.setToolTip("Stop active EARF folder consolidation or consistency check immediately")
        self.btn_earf_stop.setEnabled(False)
        self.btn_earf_stop.clicked.connect(self._stop_earf_worker)
        folder_layout.addWidget(self.btn_earf_stop)

        layout.addWidget(self.earf_folder_bar)

        # 3. Manual File Bar (Visible in Custom File Mode)
        self.earf_file_bar = QWidget()
        file_layout = QHBoxLayout(self.earf_file_bar)
        file_layout.setContentsMargins(0, 0, 0, 0)
        file_layout.addWidget(QLabel("EARF Files:"))

        self.txt_earf_preview_files = QLineEdit()
        self.txt_earf_preview_files.setPlaceholderText("Select file(s) or folder containing Preliminary EARF files (*_earf*.xlsx)...")
        file_layout.addWidget(self.txt_earf_preview_files, 1)

        btn_browse_earf = QPushButton("Browse Files...")
        btn_browse_earf.clicked.connect(self._browse_earf_preview)
        file_layout.addWidget(btn_browse_earf)

        btn_browse_earf_folder_prev = QPushButton("Browse Folder...")
        btn_browse_earf_folder_prev.setToolTip("Select folder to scan and load all preliminary EARF files (*_earf*.xlsx/xls)")
        btn_browse_earf_folder_prev.clicked.connect(self._browse_earf_folder_preview)
        file_layout.addWidget(btn_browse_earf_folder_prev)

        file_layout.addWidget(QLabel("Sheet:"))
        self.txt_earf_preview_sheet = QLineEdit()
        self.txt_earf_preview_sheet.setPlaceholderText("blank = 1st")
        self.txt_earf_preview_sheet.setMaximumWidth(80)
        file_layout.addWidget(self.txt_earf_preview_sheet)

        btn_load_earf = QPushButton("Load EARF Preview")
        btn_load_earf.clicked.connect(self._load_earf_preview)
        file_layout.addWidget(btn_load_earf)

        self.earf_file_bar.setVisible(False)
        layout.addWidget(self.earf_file_bar)

        # 4. KPI Summary Cards Frame
        kpi_frame = QFrame()
        kpi_frame.setObjectName("kpiFrame")
        kpi_layout = QHBoxLayout(kpi_frame)
        kpi_layout.setContentsMargins(8, 6, 8, 6)
        kpi_layout.setSpacing(10)

        self.kpi_earf_files = self._create_kpi_card("Files Loaded", "0", "#0284c7")
        self.kpi_earf_total = self._create_kpi_card("Total EARF Rows", "0", "#4f46e5")
        self.kpi_earf_unique = self._create_kpi_card("Unique Geocodes", "0", "#059669")
        self.kpi_earf_consistency = self._create_kpi_card("Consistency", "Pending Check", "#64748b")
        self.kpi_earf_popcen_warn = self._create_kpi_card("Missing POPCEN", "0", "#b45309")
        self.kpi_earf_retained = self._create_kpi_card("Retained EAs", "0", "#16a34a")
        self.kpi_earf_split = self._create_kpi_card("Split EAs", "0", "#2563eb")
        self.kpi_earf_merged = self._create_kpi_card("Merged EAs", "0", "#ea580c")

        for k in (
            self.kpi_earf_files, self.kpi_earf_total, self.kpi_earf_unique,
            self.kpi_earf_consistency, self.kpi_earf_popcen_warn,
            self.kpi_earf_retained, self.kpi_earf_split, self.kpi_earf_merged
        ):
            kpi_layout.addWidget(k)

        layout.addWidget(kpi_frame)

        # 5. Filter, Search & Record Action Bar
        filter_bar = QHBoxLayout()

        self.chk_earf_select_all = QCheckBox("Select All")
        self.chk_earf_select_all.setStyleSheet("font-weight: 600; color: #1e293b;")
        self.chk_earf_select_all.setToolTip("Select or deselect all records matching current filters across all pages in the database")
        self.chk_earf_select_all.stateChanged.connect(self._on_earf_select_all_toggled)
        filter_bar.addWidget(self.chk_earf_select_all)

        self.btn_earf_remove = QPushButton("Remove Selected")
        self.btn_earf_remove.setObjectName("dangerBtn")
        self.btn_earf_remove.setToolTip("Permanently remove all checked Preliminary EARF records")
        self.btn_earf_remove.setEnabled(False)
        self.btn_earf_remove.clicked.connect(self._remove_selected_earf_records)
        filter_bar.addWidget(self.btn_earf_remove)

        self.lbl_earf_selected_count = QLabel("0 selected")
        self.lbl_earf_selected_count.setStyleSheet("color: #64748b; font-size: 11px; font-weight: 500;")
        filter_bar.addWidget(self.lbl_earf_selected_count)

        v_sep_earf = QFrame()
        v_sep_earf.setFrameShape(QFrame.VLine)
        v_sep_earf.setFrameShadow(QFrame.Sunken)
        v_sep_earf.setStyleSheet("color: #cbd5e1; margin: 2px 4px;")
        filter_bar.addWidget(v_sep_earf)

        filter_bar.addWidget(QLabel("Search:"))
        self.search_earf = QLineEdit()
        self.search_earf.setPlaceholderText("Search Geocode, Prov, Mun, Brgy, EA, Name, File...")
        self.search_earf.textChanged.connect(self._on_earf_search_changed)
        filter_bar.addWidget(self.search_earf, 1)

        filter_bar.addWidget(QLabel("Consistency:"))
        self.cmb_earf_consistency_filter = QComboBox()
        self.cmb_earf_consistency_filter.addItems([
            "All Records",
            "Passed (PASS)",
            "All Issues (Warnings & Errors)",
            "R6: Missing in POPCEN (Warning)",
            "R1: Invalid Geocode (Error)",
        ])
        self.cmb_earf_consistency_filter.currentIndexChanged.connect(self._on_earf_filter_changed)
        filter_bar.addWidget(self.cmb_earf_consistency_filter)

        filter_bar.addWidget(QLabel("EA Type:"))
        self.cmb_earf_type_filter = QComboBox()
        self.cmb_earf_type_filter.addItems(["All EA Types", "RETAINED", "SPLIT", "MERGED", "Other"])
        self.cmb_earf_type_filter.currentIndexChanged.connect(self._on_earf_filter_changed)
        filter_bar.addWidget(self.cmb_earf_type_filter)

        self.chk_earf_only_with_new_ea = QCheckBox("With New EA Code (2026)")
        self.chk_earf_only_with_new_ea.setChecked(True)
        self.chk_earf_only_with_new_ea.setToolTip("Only view rows with valid values in New EA Code (2026)")
        self.chk_earf_only_with_new_ea.setStyleSheet("font-weight: 600; color: #047857;")
        self.chk_earf_only_with_new_ea.stateChanged.connect(self._on_earf_filter_changed)
        filter_bar.addWidget(self.chk_earf_only_with_new_ea)

        filter_bar.addWidget(QLabel("Page Size:"))
        self.cmb_earf_page_size = QComboBox()
        self.cmb_earf_page_size.addItems(["200", "500", "1000", "2000", "5000"])
        self.cmb_earf_page_size.setCurrentText("1000")
        self.cmb_earf_page_size.currentIndexChanged.connect(self._on_earf_page_size_changed)
        filter_bar.addWidget(self.cmb_earf_page_size)

        # Pagination Buttons
        self.btn_earf_prev = QPushButton("Prev")
        self.btn_earf_prev.setFixedWidth(65)
        self.btn_earf_prev.clicked.connect(self._earf_prev_page)
        filter_bar.addWidget(self.btn_earf_prev)

        self.lbl_earf_page_info = QLabel("Page 1 of 1")
        self.lbl_earf_page_info.setObjectName("pageInfo")
        filter_bar.addWidget(self.lbl_earf_page_info)

        self.btn_earf_next = QPushButton("Next")
        self.btn_earf_next.setFixedWidth(65)
        self.btn_earf_next.clicked.connect(self._earf_next_page)
        filter_bar.addWidget(self.btn_earf_next)

        layout.addLayout(filter_bar)

        # 6. Interactive Table Widget
        self.table_earf = QTableWidget()
        self.table_earf.setObjectName("previewTable")
        self.table_earf.setAlternatingRowColors(True)
        self.table_earf.setSortingEnabled(False)
        self.table_earf.horizontalHeader().setStretchLastSection(False)
        self.table_earf.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table_earf.verticalHeader().setDefaultSectionSize(26)
        self.table_earf.itemChanged.connect(self._on_earf_table_item_changed)
        self.table_earf.horizontalHeader().sectionClicked.connect(self._on_earf_header_clicked)
        self.table_earf.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table_earf.customContextMenuRequested.connect(self._show_earf_table_context_menu)
        layout.addWidget(self.table_earf, 1)
        self._enable_column_toggle(self.table_earf)

        # 7. Status text
        self.lbl_earf_table_info = QLabel("Ready.")
        self.lbl_earf_table_info.setObjectName("infoLabel")
        layout.addWidget(self.lbl_earf_table_info)

    # ----------------------------------------------------------------------
    # Sub-Tab 3: EA GeoPackages (Inspected from 01915_ea2026.gpkg)
    # ----------------------------------------------------------------------
    def _setup_gpkg_subtab(self):
        layout = QVBoxLayout(self.tab_gpkg)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # 1. Mode Selector Bar
        mode_bar = QHBoxLayout()
        mode_bar.addWidget(QLabel("<b>Data Source:</b>"))
        self.cmb_gpkg_source_mode = QComboBox()
        self.cmb_gpkg_source_mode.addItems([
            "Consolidated Database (SQLite: gpkg_database.db)",
            "Manual File Selection / Project Layers"
        ])
        self.cmb_gpkg_source_mode.currentIndexChanged.connect(self._on_gpkg_source_mode_changed)
        mode_bar.addWidget(self.cmb_gpkg_source_mode)

        self.lbl_gpkg_db_badge = QLabel("Checking GPKG database...")
        self.lbl_gpkg_db_badge.setObjectName("dbBadge")
        mode_bar.addWidget(self.lbl_gpkg_db_badge)

        mode_bar.addStretch()

        self.btn_gpkg_refresh = QPushButton("Refresh")
        self.btn_gpkg_refresh.setObjectName("actionBtn")
        self.btn_gpkg_refresh.clicked.connect(self._refresh_gpkg_view)
        mode_bar.addWidget(self.btn_gpkg_refresh)

        layout.addLayout(mode_bar)

        # 2. Folder Consolidation Bar (Visible in Database Mode)
        self.gpkg_folder_bar = QWidget()
        folder_layout = QHBoxLayout(self.gpkg_folder_bar)
        folder_layout.setContentsMargins(0, 0, 0, 0)
        folder_layout.addWidget(QLabel("<b>GPKG Folder:</b>"))

        self.txt_gpkg_folder = QLineEdit()
        self.txt_gpkg_folder.setPlaceholderText("Select directory containing GeoPackage (.gpkg) files...")
        self.txt_gpkg_folder.setText(DEFAULT_GPKG_ROOT)
        folder_layout.addWidget(self.txt_gpkg_folder, 1)

        btn_browse_folder = QPushButton("Browse Folder...")
        btn_browse_folder.clicked.connect(self._browse_gpkg_folder)
        folder_layout.addWidget(btn_browse_folder)

        self.chk_gpkg_recursive = QCheckBox("Subfolders")
        self.chk_gpkg_recursive.setChecked(True)
        self.chk_gpkg_recursive.setToolTip("Recursively scan nested subdirectories for GeoPackages (excluding 'versions' folders)")
        folder_layout.addWidget(self.chk_gpkg_recursive)

        self.btn_consolidate_gpkg = QPushButton("Consolidate Folder to DB...")
        self.btn_consolidate_gpkg.setObjectName("actionBtn")
        self.btn_consolidate_gpkg.setToolTip("Scan folder, parse all GeoPackage files, and save into local SQLite database")
        self.btn_consolidate_gpkg.clicked.connect(self._consolidate_gpkg_folder_prompt)
        folder_layout.addWidget(self.btn_consolidate_gpkg)

        self.btn_run_gpkg_consistency = QPushButton("Run Consistency Check")
        self.btn_run_gpkg_consistency.setObjectName("primaryBtn")
        self.btn_run_gpkg_consistency.setToolTip("Run multi-file consistency, cross-file duplicate, and multipart checks across consolidated records")
        self.btn_run_gpkg_consistency.clicked.connect(self._run_gpkg_consistency_check_prompt)
        folder_layout.addWidget(self.btn_run_gpkg_consistency)

        self.btn_gpkg_stop = QPushButton("Stop")
        self.btn_gpkg_stop.setObjectName("stopBtn")
        self.btn_gpkg_stop.setToolTip("Stop active GeoPackage consolidation or consistency check immediately")
        self.btn_gpkg_stop.setEnabled(False)
        self.btn_gpkg_stop.clicked.connect(self._stop_gpkg_worker)
        folder_layout.addWidget(self.btn_gpkg_stop)

        layout.addWidget(self.gpkg_folder_bar)

        # 3. Manual File / Layer Bar (Visible in Manual Mode)
        self.gpkg_file_bar = QWidget()
        file_layout = QHBoxLayout(self.gpkg_file_bar)
        file_layout.setContentsMargins(0, 0, 0, 0)
        file_layout.addWidget(QLabel("<b>EA GeoPackage Layer(s):</b>"))

        self.txt_gpkg_preview_layers = QLineEdit()
        self.txt_gpkg_preview_layers.setPlaceholderText("Select GPKG files on disk, folder, or project layers...")
        file_layout.addWidget(self.txt_gpkg_preview_layers, 1)

        btn_browse_gpkg = QPushButton("Browse GPKG...")
        btn_browse_gpkg.setToolTip("Select one or more GeoPackage (.gpkg) files from disk")
        btn_browse_gpkg.clicked.connect(self._browse_gpkg_preview)
        file_layout.addWidget(btn_browse_gpkg)

        btn_project_layers = QPushButton("Use Project Layers")
        btn_project_layers.setToolTip("Auto-detect all polygon vector layers in current QGIS project")
        btn_project_layers.clicked.connect(self._detect_project_layers_preview)
        file_layout.addWidget(btn_project_layers)

        btn_load_gpkg = QPushButton("Load GPKG Preview")
        btn_load_gpkg.setObjectName("actionBtn")
        btn_load_gpkg.clicked.connect(self._load_gpkg_preview)
        file_layout.addWidget(btn_load_gpkg)

        layout.addWidget(self.gpkg_file_bar)

        # 2. KPI Summary Cards Frame
        kpi_frame = QFrame()
        kpi_frame.setObjectName("kpiFrame")
        kpi_layout = QHBoxLayout(kpi_frame)
        kpi_layout.setContentsMargins(8, 6, 8, 6)
        kpi_layout.setSpacing(10)

        self.kpi_gpkg_layers = self._create_kpi_card("Layers / Files", "0", "#0284c7")
        self.kpi_gpkg_features = self._create_kpi_card("Total Features", "0", "#4f46e5")
        self.kpi_gpkg_geoms = self._create_kpi_card("Polygons", "0", "#059669")
        self.kpi_gpkg_unique = self._create_kpi_card("Unique Geocodes", "0", "#0d9488")
        self.kpi_gpkg_duplicates = self._create_kpi_card("Duplicate Geocodes", "0", "#f59e0b")
        self.kpi_gpkg_overlaps = self._create_kpi_card("Cross-File Overlaps", "0", "#dc2626")
        self.kpi_gpkg_consistency = self._create_kpi_card("Consistency", "Pending Check", "#64748b")
        self.kpi_gpkg_retained = self._create_kpi_card("Retained EAs", "0", "#16a34a")
        self.kpi_gpkg_split = self._create_kpi_card("Split EAs", "0", "#0284c7")
        self.kpi_gpkg_merged = self._create_kpi_card("Merged EAs", "0", "#ea580c")

        for k in (
            self.kpi_gpkg_layers, self.kpi_gpkg_features, self.kpi_gpkg_geoms,
            self.kpi_gpkg_unique, self.kpi_gpkg_duplicates, self.kpi_gpkg_overlaps,
            self.kpi_gpkg_consistency, self.kpi_gpkg_retained, self.kpi_gpkg_split, self.kpi_gpkg_merged
        ):
            kpi_layout.addWidget(k)

        layout.addWidget(kpi_frame)

        # 3. Filter, Search & Record Action Bar
        filter_bar = QHBoxLayout()

        self.chk_gpkg_select_all = QCheckBox("Select All")
        self.chk_gpkg_select_all.setStyleSheet("font-weight: 600; color: #1e293b;")
        self.chk_gpkg_select_all.setToolTip("Select or deselect all features matching current filters across all pages in the database")
        self.chk_gpkg_select_all.stateChanged.connect(self._on_gpkg_select_all_toggled)
        filter_bar.addWidget(self.chk_gpkg_select_all)

        self.btn_gpkg_remove = QPushButton("Remove Selected")
        self.btn_gpkg_remove.setObjectName("dangerBtn")
        self.btn_gpkg_remove.setToolTip("Permanently remove all checked GeoPackage features from loaded dataset")
        self.btn_gpkg_remove.setEnabled(False)
        self.btn_gpkg_remove.clicked.connect(self._remove_selected_gpkg_records)
        filter_bar.addWidget(self.btn_gpkg_remove)

        self.lbl_gpkg_selected_count = QLabel("0 selected")
        self.lbl_gpkg_selected_count.setStyleSheet("color: #64748b; font-size: 11px; font-weight: 500;")
        filter_bar.addWidget(self.lbl_gpkg_selected_count)

        v_sep_gpkg = QFrame()
        v_sep_gpkg.setFrameShape(QFrame.VLine)
        v_sep_gpkg.setFrameShadow(QFrame.Sunken)
        v_sep_gpkg.setStyleSheet("color: #cbd5e1; margin: 2px 4px;")
        filter_bar.addWidget(v_sep_gpkg)

        filter_bar.addWidget(QLabel("Search:"))
        self.search_gpkg = QLineEdit()
        self.search_gpkg.setPlaceholderText("Search Geocode, Province, City/Mun, Barangay, EAN, Name...")
        self.search_gpkg.textChanged.connect(self._on_gpkg_search_changed)
        filter_bar.addWidget(self.search_gpkg, 1)

        filter_bar.addWidget(QLabel("Consistency:"))
        self.cmb_gpkg_issue_filter = QComboBox()
        self.cmb_gpkg_issue_filter.addItems([
            "All Records",
            "Any Issue / Duplicate",
            "Cross-File Overlaps Only",
            "Intra-File Multipart Only",
            "Conflicting Data Only",
            "Missing Geometry Only",
            "Unique & Clean Only",
        ])
        self.cmb_gpkg_issue_filter.currentIndexChanged.connect(self._on_gpkg_filter_changed)
        filter_bar.addWidget(self.cmb_gpkg_issue_filter)

        filter_bar.addWidget(QLabel("EA Type:"))
        self.cmb_gpkg_type_filter = QComboBox()
        self.cmb_gpkg_type_filter.addItems(["All EA Types", "RETAINED", "SPLIT", "MERGED", "Other"])
        self.cmb_gpkg_type_filter.currentIndexChanged.connect(self._on_gpkg_filter_changed)
        filter_bar.addWidget(self.cmb_gpkg_type_filter)

        btn_run_consistency = QPushButton("Check Consistency...")
        btn_run_consistency.setToolTip("Run comprehensive consistency & duplication check across all loaded GeoPackages")
        btn_run_consistency.setStyleSheet("font-weight: 600; color: #4338ca; background-color: #e0e7ff; border: 1px solid #c7d2fe; border-radius: 4px; padding: 4px 8px;")
        btn_run_consistency.clicked.connect(self._run_gpkg_consistency_dispatcher)
        filter_bar.addWidget(btn_run_consistency)

        filter_bar.addWidget(QLabel("Page Size:"))
        self.cmb_gpkg_page_size = QComboBox()
        self.cmb_gpkg_page_size.addItems(["200", "500", "1000", "2000", "5000"])
        self.cmb_gpkg_page_size.setCurrentText("1000")
        self.cmb_gpkg_page_size.currentIndexChanged.connect(self._on_gpkg_page_size_changed)
        filter_bar.addWidget(self.cmb_gpkg_page_size)

        # Pagination Buttons
        self.btn_gpkg_prev = QPushButton("Prev")
        self.btn_gpkg_prev.setFixedWidth(65)
        self.btn_gpkg_prev.clicked.connect(self._gpkg_prev_page)
        filter_bar.addWidget(self.btn_gpkg_prev)

        self.lbl_gpkg_page_info = QLabel("Page 1 of 1")
        self.lbl_gpkg_page_info.setObjectName("pageInfo")
        filter_bar.addWidget(self.lbl_gpkg_page_info)

        self.btn_gpkg_next = QPushButton("Next")
        self.btn_gpkg_next.setFixedWidth(65)
        self.btn_gpkg_next.clicked.connect(self._gpkg_next_page)
        filter_bar.addWidget(self.btn_gpkg_next)

        layout.addLayout(filter_bar)

        # 4. Interactive Table Widget
        self.table_gpkg = QTableWidget()
        self.table_gpkg.setObjectName("previewTable")
        self.table_gpkg.setAlternatingRowColors(True)
        self.table_gpkg.setSortingEnabled(False)
        self.table_gpkg.horizontalHeader().setStretchLastSection(False)
        self.table_gpkg.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table_gpkg.verticalHeader().setDefaultSectionSize(26)
        self.table_gpkg.itemChanged.connect(self._on_gpkg_table_item_changed)
        self.table_gpkg.horizontalHeader().sectionClicked.connect(self._on_gpkg_header_clicked)
        self.table_gpkg.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table_gpkg.customContextMenuRequested.connect(self._show_gpkg_table_context_menu)
        layout.addWidget(self.table_gpkg, 1)
        self._enable_column_toggle(self.table_gpkg)

        # 5. Status text
        self.lbl_gpkg_table_info = QLabel("No EA GeoPackage layers loaded.")
        self.lbl_gpkg_table_info.setObjectName("infoLabel")
        layout.addWidget(self.lbl_gpkg_table_info)

    # ----------------------------------------------------------------------
    # Tab 2: Processing Inputs & Execution Tab
    # ----------------------------------------------------------------------
    def _setup_config_tab(self):
        """Legacy configuration tab stub (tab removed to streamline UI)."""
        pass

    def _create_kpi_card(self, title, default_val, accent_color):
        card = QFrame()
        card.setObjectName("kpiCard")
        card.setAttribute(Qt.WA_StyledBackground, True)
        card.setStyleSheet(
            "QFrame#kpiCard { background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 4px; }"
        )
        l = QVBoxLayout(card)
        l.setContentsMargins(8, 6, 8, 6)
        l.setSpacing(2)

        t_lbl = QLabel(title)
        t_lbl.setObjectName("kpiTitle")
        t_lbl.setFont(QFont("Segoe UI", 8))
        t_lbl.setStyleSheet("color: #64748b; font-weight: 500;")

        v_lbl = QLabel(default_val)
        v_lbl.setObjectName("kpiValue")
        v_lbl.setFont(QFont("Segoe UI", 11, QFont.Bold))
        v_lbl.setStyleSheet(f"color: {accent_color};")

        l.addWidget(t_lbl)
        l.addWidget(v_lbl)
        card.title_label = t_lbl
        card.val_label = v_lbl
        return card

    # ----------------------------------------------------------------------
    # Initial Data & Database Check
    # ----------------------------------------------------------------------
    def _check_and_load_initial_data(self):
        # 1. Check POPCEN Database
        count = self.db.record_count()
        if count > 0:
            kpis = self.db.get_kpis()
            self.lbl_db_badge.setText(f"Local Database: {kpis['total_eas']:,} EA records ({count:,} total) loaded")
            self.lbl_db_badge.setStyleSheet("color: #15803d; font-weight: 600; background-color: #dcfce7; padding: 2px 8px; border-radius: 4px;")
            self.cmb_source_mode.setCurrentIndex(0)
            self._load_database_kpis()
            self._fetch_and_render_db_page()
        else:
            self.lbl_db_badge.setText("Local Database: Empty")
            self.lbl_db_badge.setStyleSheet("color: #b45309; font-weight: 600; background-color: #fef3c7; padding: 2px 8px; border-radius: 4px;")

        # 2. Check EARF Database
        self._check_and_load_earf_data()

        # 3. Check GPKG Database
        self._check_and_load_gpkg_data()

        # 4. Check Matched Records Database
        self._check_and_load_matching_data()

        # 5. Initialize Dashboard (Deliverables Monitoring)
        try:
            self._init_monitoring_regions_provinces()
            # Immediately restore saved root directory and cached records from SQLite so Dashboard keeps records across sessions
            saved_root = self.db.get_saved_monitoring_root_dir()
            if saved_root:
                self.txt_mon_root_dir.setText(saved_root)
            saved_records = self.db.get_saved_monitoring_records()
            if saved_records:
                self.mon_records = saved_records
                self._apply_monitoring_filters()
            # Start background scan to check for updates only if root directory is available on disk
            if saved_root and os.path.exists(saved_root):
                self._start_monitoring_scan()
        except Exception:
            pass

    def _load_database_kpis(self):
        kpis = self.db.get_kpis()
        self.kpi_total.val_label.setText(f"{kpis['total_records']:,}")
        self.kpi_ea.val_label.setText(f"{kpis['total_eas']:,}")
        self.kpi_hh.val_label.setText(f"{kpis['total_households']:,}")
        self.kpi_delin.val_label.setText(f"{kpis['delineation']:,}")
        self.kpi_merge.val_label.setText(f"{kpis['merging']:,}")
        self.kpi_gap.val_label.setText(f"{kpis['gap_overlap']:,}")

    def _on_source_mode_changed(self):
        self._reset_popcen_selection()
        mode = self.cmb_source_mode.currentIndex()
        if mode == 0:  # Local Database
            self.file_bar.setVisible(False)
            self._check_and_load_initial_data()
        else:  # Custom File
            self.file_bar.setVisible(True)
            if not self.popcen_df.empty:
                self._apply_file_filters()

    def _refresh_view(self):
        self._reset_popcen_selection()
        if self.cmb_source_mode.currentIndex() == 0:
            self._check_and_load_initial_data()
        else:
            self._load_file_preview()

    def _on_popcen_ea_toggled(self):
        is_ea = self.chk_popcen_only_ea.isChecked()
        self.cmb_level_filter.blockSignals(True)
        if is_ea:
            self.cmb_level_filter.setCurrentText("5 - EA")
        else:
            if self.cmb_level_filter.currentText() == "5 - EA":
                self.cmb_level_filter.setCurrentText("All Levels")
        self.cmb_level_filter.blockSignals(False)
        self._reset_and_filter()

    # ----------------------------------------------------------------------
    # POPCEN Database Query & Paging Logic
    # ----------------------------------------------------------------------
    def _fetch_and_render_db_page(self):
        search_txt = self.search_input.text().strip()
        lvl = self.cmb_level_filter.currentText()
        issue = self.cmb_issue_filter.currentText()
        only_ea = getattr(self, "chk_popcen_only_ea", None) is not None and self.chk_popcen_only_ea.isChecked()

        t0 = time.time()
        offset = self.current_page * self.page_size
        records, total_filtered = self.db.query_records(
            search=search_txt,
            level="5 - EA" if only_ea else lvl,
            issue=issue,
            only_ea_level=only_ea,
            limit=self.page_size,
            offset=offset,
        )
        query_ms = round((time.time() - t0) * 1000, 1)

        self.total_filtered_records = total_filtered
        total_pages = max(1, (total_filtered + self.page_size - 1) // self.page_size)

        self.btn_prev_page.setEnabled(self.current_page > 0)
        self.btn_next_page.setEnabled(self.current_page < total_pages - 1)
        self.lbl_page_info.setText(f"Page {self.current_page + 1:,} of {total_pages:,}")

        df_page = pd.DataFrame(records) if records else pd.DataFrame()
        self._populate_table(df_page, total_filtered, offset=offset)

        start_num = offset + 1 if total_filtered > 0 else 0
        end_num = min(offset + len(records), total_filtered)
        filter_note = " [Filtered: 5 - EA only]" if only_ea else ""
        self.lbl_table_info.setText(
            f"Displaying {start_num:,}–{end_num:,} of {total_filtered:,} filtered records "
            f"(Total database: {self.db.record_count():,}){filter_note} | Query time: {query_ms} ms"
        )

    def _reset_and_filter(self):
        self.current_page = 0
        self._reset_popcen_selection()
        if hasattr(self, "chk_popcen_only_ea") and self.chk_popcen_only_ea is not None:
            self.chk_popcen_only_ea.blockSignals(True)
            self.chk_popcen_only_ea.setChecked(self.cmb_level_filter.currentText() == "5 - EA")
            self.chk_popcen_only_ea.blockSignals(False)
        if self.cmb_source_mode.currentIndex() == 0:
            self._fetch_and_render_db_page()
        else:
            self._apply_file_filters()

    def _on_search_changed(self):
        self._reset_and_filter()

    def _on_page_size_changed(self):
        try:
            self.page_size = int(self.cmb_page_size.currentText())
        except ValueError:
            self.page_size = 1000
        self._reset_and_filter()

    def _prev_page(self):
        if self.current_page > 0:
            self.current_page -= 1
            if self.cmb_source_mode.currentIndex() == 0:
                self._fetch_and_render_db_page()
            else:
                self._apply_file_filters()

    def _next_page(self):
        total_count = self.total_filtered_records if self.cmb_source_mode.currentIndex() == 0 else (len(self.popcen_filtered_df) if hasattr(self, "popcen_filtered_df") else len(self.popcen_df))
        total_pages = max(1, (total_count + self.page_size - 1) // self.page_size)
        if self.current_page < total_pages - 1:
            self.current_page += 1
            if self.cmb_source_mode.currentIndex() == 0:
                self._fetch_and_render_db_page()
            else:
                self._apply_file_filters()

    # ----------------------------------------------------------------------
    # POPCEN Table Rendering
    # ----------------------------------------------------------------------
    def _populate_table(self, df: pd.DataFrame, total_filtered: int, offset: int = 0):
        self._is_updating_popcen_checks = True
        try:
            self.table_widget.setUpdatesEnabled(False)
            self.table_widget.blockSignals(True)
            self.table_widget.clear()

            if df.empty:
                self.table_widget.setColumnCount(1)
                self.table_widget.setRowCount(1)
                self.table_widget.setHorizontalHeaderLabels(["Info"])
                item = QTableWidgetItem("No records match the current filters.")
                item.setForeground(QColor("#64748b"))
                self.table_widget.setItem(0, 0, item)
                self.table_widget.blockSignals(False)
                self.table_widget.setUpdatesEnabled(True)
                self._update_popcen_selection_state()
                return

            cols_order = [
                "geographic_name",
                "geographic_level",
                "geocode",
                "match_status",
                "region_code",
                "province_code",
                "city_mun_code",
                "barangay_code",
                "ean",
                "no_of_eas",
                "areaname",
                "household_population",
                "no_of_households",
                "ea_subject_for_delineation",
                "ea_subject_for_merging",
                "ea_with_gap_overlap",
                "estimated_number_of_households",
                "remarks",
            ]

            disp_cols = [c for c in cols_order if c in df.columns]
            for c in df.columns:
                if c not in disp_cols and c != "id":
                    disp_cols.append(c)

            header_labels = ["☑ Select", "#"] + [
                c.replace("_", " ").title().replace("Ean", "EAN").replace("Eas", "EAs").replace("Ea ", "EA ")
                for c in disp_cols
            ]

            self.table_widget.setColumnCount(len(disp_cols) + 2)
            self.table_widget.setRowCount(len(df))
            self.table_widget.setHorizontalHeaderLabels(header_labels)
            self.table_widget.setColumnWidth(0, 68)
            self.table_widget.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
            self.table_widget.setColumnWidth(1, 55)

            for row_idx, (_, row) in enumerate(df.iterrows()):
                # Column 0: Checkbox for row selection
                chk_item = QTableWidgetItem()
                chk_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
                row_id = row.get("id") if "id" in row and pd.notna(row.get("id")) else row.name
                norm_id = int(row_id) if str(row_id).isdigit() else row_id
                chk_item.setData(Qt.UserRole, row_id)
                gc_val = str(row.get("geocode", "") or "").strip()
                chk_item.setData(Qt.UserRole + 1, gc_val)

                if self.popcen_select_all_global:
                    deselected = {int(x) if str(x).isdigit() else x for x in self.popcen_deselected_ids}
                    is_chk = (norm_id not in deselected)
                else:
                    selected = {int(x) if str(x).isdigit() else x for x in self.popcen_selected_ids}
                    is_chk = (norm_id in selected)

                chk_item.setCheckState(Qt.Checked if is_chk else Qt.Unchecked)
                chk_item.setTextAlignment(Qt.AlignCenter)
                self.table_widget.setItem(row_idx, 0, chk_item)

                # Column 1: Row index
                idx_item = QTableWidgetItem(f"{offset + row_idx + 1:,}")
                idx_item.setTextAlignment(Qt.AlignCenter)
                idx_item.setForeground(QColor("#64748b"))
                self.table_widget.setItem(row_idx, 1, idx_item)

                for col_idx, col_name in enumerate(disp_cols):
                    raw_val = row[col_name]
                    val = str(raw_val) if pd.notna(raw_val) else ""
                    if val.lower() == "nan":
                        val = ""

                    col_low = col_name.lower().strip()
                    if (
                        col_low in ("code", "hhcount", "sy", "hh_count", "bldgcount", "bldg_count", "eacount", "hh count", "bldg count", "ea count", "dup_count", "file_count")
                        or (val.endswith(".0") and any(k in col_low for k in ["code", "count", "sy", "year"]))
                    ):
                        val = to_whole_number_str(val)

                    item = QTableWidgetItem(val)

                    if any(k in col_low for k in ["code", "ean", "geocode", "#", "no_of_eas"]):
                        item.setTextAlignment(Qt.AlignCenter)
                    elif any(k in col_low for k in ["population", "households", "count"]):
                        item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    else:
                        item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)

                    if "match_status" in col_low:
                        if val == "MATCHED_ALL":
                            item.setBackground(QColor("#dcfce7"))
                            item.setForeground(QColor("#15803d"))
                        elif "UNMATCHED" in val:
                            item.setBackground(QColor("#fee2e2"))
                            item.setForeground(QColor("#b91c1c"))
                    elif "gap_overlap" in col_low or "gap/overlap" in col_low:
                        v_low = val.strip().lower()
                        if v_low == "gap & overlap":
                            item.setBackground(QColor("#fee2e2"))
                            item.setForeground(QColor("#991b1b"))
                        elif v_low in ("overlap", "gap"):
                            item.setBackground(QColor("#fef3c7"))
                            item.setForeground(QColor("#b45309"))
                        elif v_low == "no issue":
                            item.setForeground(QColor("#16a34a"))
                    elif any(k in col_low for k in ["delineation", "merging"]):
                        v_str = val.strip().replace("-", "")
                        if v_str and v_str != "0":
                            item.setBackground(QColor("#fff7ed"))
                            item.setForeground(QColor("#c2410c"))
                            font = item.font()
                            font.setBold(True)
                            item.setFont(font)
                    elif "level" in col_low:
                        if "5 - EA" in val or "5-EA" in val:
                            font = item.font()
                            font.setBold(True)
                            item.setFont(font)
                            item.setForeground(QColor("#0284c7"))
                            item.setBackground(QColor("#f0f9ff"))
                        elif "4 - Barangay" in val:
                            item.setForeground(QColor("#4338ca"))

                    self.table_widget.setItem(row_idx, col_idx + 2, item)

            self.table_widget.blockSignals(False)
            self.table_widget.setUpdatesEnabled(True)
        finally:
            self._is_updating_popcen_checks = False
        self._update_popcen_selection_state()

    # ----------------------------------------------------------------------
    # POPCEN Record Selection & Synchronized Removal Logic
    # ----------------------------------------------------------------------
    def _reset_popcen_selection(self):
        """Clears all POPCEN multi-page and local selections."""
        self.popcen_select_all_global = False
        self.popcen_selected_ids.clear()
        self.popcen_deselected_ids.clear()
        self._update_popcen_selection_state()

    def _update_popcen_selection_state(self):
        """Updates the remove button and select all checkbox based on checked rows/global selection state."""
        if not hasattr(self, "btn_popcen_remove") or not hasattr(self, "table_widget"):
            return

        total_matching = getattr(self, "total_filtered_records", 0)
        if self.table_widget.columnCount() <= 1 or total_matching == 0:
            self.lbl_popcen_selected_count.setText("0 selected")
            self.btn_popcen_remove.setEnabled(False)
            self.btn_popcen_remove.setText("Remove Selected")
            self.chk_popcen_select_all.blockSignals(True)
            self.chk_popcen_select_all.setCheckState(Qt.Unchecked)
            self.chk_popcen_select_all.setEnabled(False)
            self.chk_popcen_select_all.blockSignals(False)
            return

        self.chk_popcen_select_all.setEnabled(True)

        if self.popcen_select_all_global:
            total_selected = max(0, total_matching - len(self.popcen_deselected_ids))
        else:
            total_selected = len(self.popcen_selected_ids)

        if self.popcen_select_all_global and len(self.popcen_deselected_ids) == 0:
            self.lbl_popcen_selected_count.setText(f"{total_selected:,} selected (all pages)")
        elif total_selected > 0:
            self.lbl_popcen_selected_count.setText(f"{total_selected:,} selected")
        else:
            self.lbl_popcen_selected_count.setText("0 selected")

        self.btn_popcen_remove.setEnabled(total_selected > 0)
        self.btn_popcen_remove.setText(f"Remove Selected ({total_selected:,})" if total_selected > 0 else "Remove Selected")

        self.chk_popcen_select_all.blockSignals(True)
        if total_matching > 0 and total_selected == total_matching:
            self.chk_popcen_select_all.setCheckState(Qt.Checked)
        elif total_selected > 0:
            self.chk_popcen_select_all.setCheckState(Qt.PartiallyChecked)
        else:
            self.chk_popcen_select_all.setCheckState(Qt.Unchecked)
        self.chk_popcen_select_all.blockSignals(False)

    def _on_popcen_table_item_changed(self, item):
        if item.column() == 0:
            if getattr(self, "_is_updating_popcen_checks", False):
                return
            row_id = item.data(Qt.UserRole)
            if row_id is None:
                return
            norm_id = int(row_id) if str(row_id).isdigit() else row_id
            is_checked = (item.checkState() == Qt.Checked)

            if self.popcen_select_all_global:
                if not is_checked:
                    self.popcen_deselected_ids.add(norm_id)
                else:
                    self.popcen_deselected_ids.discard(norm_id)
            else:
                if is_checked:
                    self.popcen_selected_ids.add(norm_id)
                else:
                    self.popcen_selected_ids.discard(norm_id)

                total_matching = getattr(self, "total_filtered_records", 0)
                if total_matching > 0 and len(self.popcen_selected_ids) == total_matching:
                    self.popcen_select_all_global = True
                    self.popcen_selected_ids.clear()
                    self.popcen_deselected_ids.clear()

            self._update_popcen_selection_state()

    def _on_popcen_select_all_toggled(self, state):
        if getattr(self, "_is_updating_popcen_checks", False):
            return
        self._is_updating_popcen_checks = True
        try:
            is_checked = (state == Qt.Checked or (state != Qt.Unchecked and not self.popcen_select_all_global))
            self.popcen_select_all_global = is_checked
            self.popcen_selected_ids.clear()
            self.popcen_deselected_ids.clear()

            self.table_widget.blockSignals(True)
            target_state = Qt.Checked if is_checked else Qt.Unchecked
            for r in range(self.table_widget.rowCount()):
                item = self.table_widget.item(r, 0)
                if item and (item.flags() & Qt.ItemIsUserCheckable):
                    item.setCheckState(target_state)
            self.table_widget.blockSignals(False)
        finally:
            self._is_updating_popcen_checks = False

        self._update_popcen_selection_state()

    def _on_popcen_header_clicked(self, logical_index):
        if logical_index == 0:
            current = (self.chk_popcen_select_all.checkState() == Qt.Checked)
            self.chk_popcen_select_all.setCheckState(Qt.Unchecked if current else Qt.Checked)

    def _show_popcen_table_context_menu(self, pos):
        item = self.table_widget.itemAt(pos)
        if not item or self.table_widget.columnCount() <= 1:
            return
        menu = QMenu(self)
        checked_ids, _ = self._get_selected_popcen_records()
        checked_count = len(checked_ids)
        act_toggle = menu.addAction("Toggle Checkbox for This Row")
        menu.addSeparator()
        if checked_count > 0:
            act_remove = menu.addAction(f"🗑 Remove {checked_count:,} Checked Record(s)")
        else:
            act_remove = menu.addAction("🗑 Remove This Record")

        action = menu.exec_(self.table_widget.viewport().mapToGlobal(pos))
        if action == act_toggle:
            r = item.row()
            chk = self.table_widget.item(r, 0)
            if chk:
                new_state = Qt.Unchecked if chk.checkState() == Qt.Checked else Qt.Checked
                chk.setCheckState(new_state)
        elif action == act_remove:
            if checked_count > 0:
                self._remove_selected_popcen_records()
            else:
                r = item.row()
                chk = self.table_widget.item(r, 0)
                if chk:
                    chk.setCheckState(Qt.Checked)
                self._remove_selected_popcen_records()

    def _get_table_row_geocode(self, table: QTableWidget, row_idx: int) -> str:
        """Helper to find the geocode string for a given row in any table widget."""
        for c in range(table.columnCount()):
            header = table.horizontalHeaderItem(c)
            if header and "geocode" in header.text().lower():
                cell = table.item(row_idx, c)
                if cell and cell.text().strip():
                    return cell.text().strip()
        return ""

    def _get_selected_popcen_records(self) -> Tuple[List[Any], List[str]]:
        """Returns (ids, geocodes) for selected POPCEN records across all pages or visible rows."""
        is_db_mode = getattr(self, "cmb_source_mode", None) is not None and self.cmb_source_mode.currentIndex() == 0

        if getattr(self, "popcen_select_all_global", False):
            if is_db_mode:
                search_txt = self.search_input.text().strip()
                lvl = self.cmb_level_filter.currentText()
                issue = self.cmb_issue_filter.currentText()
                only_ea = getattr(self, "chk_popcen_only_ea", None) is not None and self.chk_popcen_only_ea.isChecked()

                all_items = self.db.query_all_popcen_ids_and_geocodes(
                    search=search_txt,
                    level="5 - EA" if only_ea else lvl,
                    issue=issue,
                    only_ea_level=only_ea,
                )
                deselected = {int(x) if str(x).isdigit() else x for x in self.popcen_deselected_ids}
                checked_ids = [row_id for (row_id, gc) in all_items if (int(row_id) if str(row_id).isdigit() else row_id) not in deselected]
                checked_geocodes = [gc for (row_id, gc) in all_items if (int(row_id) if str(row_id).isdigit() else row_id) not in deselected and gc]
                return checked_ids, list(dict.fromkeys(checked_geocodes))
            else:
                df = self.popcen_filtered_df if hasattr(self, "popcen_filtered_df") and not self.popcen_filtered_df.empty else self.popcen_df
                if df is not None and not df.empty:
                    deselected = {int(x) if str(x).isdigit() else x for x in self.popcen_deselected_ids}
                    checked_ids = []
                    checked_geocodes = []
                    for idx, row in df.iterrows():
                        row_id = row.get("id") if "id" in row and pd.notna(row.get("id")) else idx
                        norm_id = int(row_id) if str(row_id).isdigit() else row_id
                        if norm_id not in deselected:
                            checked_ids.append(row_id)
                            gc = str(row.get("geocode", "") or "").strip()
                            if gc and gc.lower() not in ("nan", "none", ""):
                                checked_geocodes.append(gc)
                    return checked_ids, list(dict.fromkeys(checked_geocodes))

        if getattr(self, "popcen_selected_ids", None):
            if is_db_mode:
                checked_ids = list(self.popcen_selected_ids)
                int_ids = [int(x) for x in checked_ids if str(x).isdigit()]
                checked_geocodes = self.db.get_popcen_geocodes_by_ids(int_ids)
                return checked_ids, list(dict.fromkeys(checked_geocodes))
            else:
                df = self.popcen_df
                checked_ids = []
                checked_geocodes = []
                sel_set = {int(x) if str(x).isdigit() else x for x in self.popcen_selected_ids}
                if df is not None and not df.empty:
                    for idx, row in df.iterrows():
                        row_id = row.get("id") if "id" in row and pd.notna(row.get("id")) else idx
                        norm_id = int(row_id) if str(row_id).isdigit() else row_id
                        if norm_id in sel_set:
                            checked_ids.append(row_id)
                            gc = str(row.get("geocode", "") or "").strip()
                            if gc and gc.lower() not in ("nan", "none", ""):
                                checked_geocodes.append(gc)
                return checked_ids, list(dict.fromkeys(checked_geocodes))

        # Fallback to visible page checkboxes
        checked_ids = []
        checked_geocodes = []
        for r in range(self.table_widget.rowCount()):
            item = self.table_widget.item(r, 0)
            if item and (item.flags() & Qt.ItemIsUserCheckable) and item.checkState() == Qt.Checked:
                row_id = item.data(Qt.UserRole)
                gc = item.data(Qt.UserRole + 1)
                if row_id is not None:
                    checked_ids.append(row_id)
                if gc:
                    checked_geocodes.append(str(gc).strip())
                else:
                    gc_txt = self._get_table_row_geocode(self.table_widget, r)
                    if gc_txt:
                        checked_geocodes.append(gc_txt)

        # Fallback to row selection
        if not checked_ids and not checked_geocodes:
            selected_rows = self.table_widget.selectionModel().selectedRows()
            if selected_rows:
                for idx in selected_rows:
                    r = idx.row()
                    item = self.table_widget.item(r, 0)
                    if item:
                        row_id = item.data(Qt.UserRole)
                        gc = item.data(Qt.UserRole + 1)
                        if row_id is not None:
                            checked_ids.append(row_id)
                        if gc:
                            checked_geocodes.append(str(gc).strip())
                        else:
                            gc_txt = self._get_table_row_geocode(self.table_widget, r)
                            if gc_txt:
                                checked_geocodes.append(gc_txt)

        return checked_ids, list(dict.fromkeys(checked_geocodes))

    def _get_selected_earf_records(self) -> Tuple[List[Any], List[str]]:
        """Returns (ids, geocodes) for selected Preliminary EARF records across all pages or visible rows."""
        is_db_mode = getattr(self, "cmb_earf_source_mode", None) is not None and self.cmb_earf_source_mode.currentIndex() == 0

        if self.earf_select_all_global:
            if is_db_mode:
                search_txt = self.search_earf.text().strip()
                ea_type = self.cmb_earf_type_filter.currentText()
                cons_filter = self.cmb_earf_consistency_filter.currentText()
                only_new = getattr(self, "chk_earf_only_with_new_ea", None) is not None and self.chk_earf_only_with_new_ea.isChecked()

                all_items = self.db.query_all_earf_ids_and_geocodes(
                    search=search_txt,
                    ea_type=ea_type,
                    consistency_filter=cons_filter,
                    only_with_new_ea=only_new,
                )
                deselected = {int(x) if str(x).isdigit() else x for x in self.earf_deselected_ids}
                checked_ids = [row_id for (row_id, gc) in all_items if (int(row_id) if str(row_id).isdigit() else row_id) not in deselected]
                checked_geocodes = [gc for (row_id, gc) in all_items if (int(row_id) if str(row_id).isdigit() else row_id) not in deselected and gc]
                return checked_ids, list(dict.fromkeys(checked_geocodes))
            else:
                df = self.earf_filtered_df if hasattr(self, "earf_filtered_df") and not self.earf_filtered_df.empty else self.earf_df
                if df is not None and not df.empty:
                    deselected = {int(x) if str(x).isdigit() else x for x in self.earf_deselected_ids}
                    checked_ids = []
                    checked_geocodes = []
                    for idx, row in df.iterrows():
                        row_id = row.get("id") if "id" in row and pd.notna(row.get("id")) else idx
                        norm_id = int(row_id) if str(row_id).isdigit() else row_id
                        if norm_id not in deselected:
                            checked_ids.append(row_id)
                            gc = str(row.get("geocode", "") or "").strip()
                            if gc and gc.lower() not in ("nan", "none", ""):
                                checked_geocodes.append(gc)
                    return checked_ids, list(dict.fromkeys(checked_geocodes))

        if self.earf_selected_ids:
            if is_db_mode:
                checked_ids = list(self.earf_selected_ids)
                int_ids = [int(x) for x in checked_ids if str(x).isdigit()]
                checked_geocodes = self.db.get_earf_geocodes_by_ids(int_ids)
                return checked_ids, list(dict.fromkeys(checked_geocodes))
            else:
                df = self.earf_df
                checked_ids = []
                checked_geocodes = []
                sel_set = {int(x) if str(x).isdigit() else x for x in self.earf_selected_ids}
                if df is not None and not df.empty:
                    for idx, row in df.iterrows():
                        row_id = row.get("id") if "id" in row and pd.notna(row.get("id")) else idx
                        norm_id = int(row_id) if str(row_id).isdigit() else row_id
                        if norm_id in sel_set:
                            checked_ids.append(row_id)
                            gc = str(row.get("geocode", "") or "").strip()
                            if gc and gc.lower() not in ("nan", "none", ""):
                                checked_geocodes.append(gc)
                return checked_ids, list(dict.fromkeys(checked_geocodes))

        # Fallback to visible page checkboxes
        checked_ids = []
        checked_geocodes = []
        for r in range(self.table_earf.rowCount()):
            item = self.table_earf.item(r, 0)
            if item and (item.flags() & Qt.ItemIsUserCheckable) and item.checkState() == Qt.Checked:
                row_id = item.data(Qt.UserRole)
                gc = item.data(Qt.UserRole + 1)
                if row_id is not None:
                    checked_ids.append(row_id)
                if gc:
                    checked_geocodes.append(str(gc).strip())
                else:
                    gc_txt = self._get_table_row_geocode(self.table_earf, r)
                    if gc_txt:
                        checked_geocodes.append(gc_txt)

        if not checked_ids and not checked_geocodes:
            selected_rows = self.table_earf.selectionModel().selectedRows()
            if selected_rows:
                for idx in selected_rows:
                    r = idx.row()
                    item = self.table_earf.item(r, 0)
                    if item:
                        row_id = item.data(Qt.UserRole)
                        gc = item.data(Qt.UserRole + 1)
                        if row_id is not None:
                            checked_ids.append(row_id)
                        if gc:
                            checked_geocodes.append(str(gc).strip())
                        else:
                            gc_txt = self._get_table_row_geocode(self.table_earf, r)
                            if gc_txt:
                                checked_geocodes.append(gc_txt)

        return checked_ids, list(dict.fromkeys(checked_geocodes))

    def _get_selected_gpkg_records(self) -> Tuple[List[Any], List[str]]:
        """Returns (ids, geocodes) for selected GeoPackage features across all pages or visible rows."""
        is_db_mode = getattr(self, "cmb_gpkg_source_mode", None) is not None and self.cmb_gpkg_source_mode.currentIndex() == 0

        if self.gpkg_select_all_global:
            if is_db_mode:
                search_txt = self.search_gpkg.text().strip()
                issue_filter = self.cmb_gpkg_issue_filter.currentText()
                ea_type = self.cmb_gpkg_type_filter.currentText()

                all_items = self.db.query_all_gpkg_ids_and_geocodes(
                    search=search_txt,
                    ea_type=ea_type,
                    issue_filter=issue_filter,
                )
                deselected = {int(x) if str(x).isdigit() else x for x in self.gpkg_deselected_ids}
                checked_ids = [row_id for (row_id, gc) in all_items if (int(row_id) if str(row_id).isdigit() else row_id) not in deselected]
                checked_geocodes = [gc for (row_id, gc) in all_items if (int(row_id) if str(row_id).isdigit() else row_id) not in deselected and gc]
                return checked_ids, list(dict.fromkeys(checked_geocodes))
            else:
                df = self.gpkg_filtered_df if hasattr(self, "gpkg_filtered_df") and not self.gpkg_filtered_df.empty else self.gpkg_df
                if df is not None and not df.empty:
                    deselected = {int(x) if str(x).isdigit() else x for x in self.gpkg_deselected_ids}
                    checked_ids = []
                    checked_geocodes = []
                    for idx, row in df.iterrows():
                        row_id = row.get("id") if "id" in row and pd.notna(row.get("id")) else idx
                        norm_id = int(row_id) if str(row_id).isdigit() else row_id
                        if norm_id not in deselected:
                            checked_ids.append(row_id)
                            gc = str(row.get("geocode", "") or "").strip()
                            if gc and gc.lower() not in ("nan", "none", ""):
                                checked_geocodes.append(gc)
                    return checked_ids, list(dict.fromkeys(checked_geocodes))

        if self.gpkg_selected_ids:
            if is_db_mode:
                checked_ids = list(self.gpkg_selected_ids)
                int_ids = [int(x) for x in checked_ids if str(x).isdigit()]
                checked_geocodes = self.db.get_gpkg_geocodes_by_ids(int_ids)
                return checked_ids, list(dict.fromkeys(checked_geocodes))
            else:
                df = self.gpkg_df
                checked_ids = []
                checked_geocodes = []
                sel_set = {int(x) if str(x).isdigit() else x for x in self.gpkg_selected_ids}
                if df is not None and not df.empty:
                    for idx, row in df.iterrows():
                        row_id = row.get("id") if "id" in row and pd.notna(row.get("id")) else idx
                        norm_id = int(row_id) if str(row_id).isdigit() else row_id
                        if norm_id in sel_set:
                            checked_ids.append(row_id)
                            gc = str(row.get("geocode", "") or "").strip()
                            if gc and gc.lower() not in ("nan", "none", ""):
                                checked_geocodes.append(gc)
                return checked_ids, list(dict.fromkeys(checked_geocodes))

        # Fallback to visible page checkboxes
        checked_ids = []
        checked_geocodes = []
        for r in range(self.table_gpkg.rowCount()):
            item = self.table_gpkg.item(r, 0)
            if item and (item.flags() & Qt.ItemIsUserCheckable) and item.checkState() == Qt.Checked:
                row_id = item.data(Qt.UserRole)
                gc = item.data(Qt.UserRole + 1)
                if row_id is not None:
                    checked_ids.append(row_id)
                if gc:
                    checked_geocodes.append(str(gc).strip())
                else:
                    gc_txt = self._get_table_row_geocode(self.table_gpkg, r)
                    if gc_txt:
                        checked_geocodes.append(gc_txt)

        if not checked_ids and not checked_geocodes:
            selected_rows = self.table_gpkg.selectionModel().selectedRows()
            if selected_rows:
                for idx in selected_rows:
                    r = idx.row()
                    item = self.table_gpkg.item(r, 0)
                    if item:
                        row_id = item.data(Qt.UserRole)
                        gc = item.data(Qt.UserRole + 1)
                        if row_id is not None:
                            checked_ids.append(row_id)
                        if gc:
                            checked_geocodes.append(str(gc).strip())
                        else:
                            gc_txt = self._get_table_row_geocode(self.table_gpkg, r)
                            if gc_txt:
                                checked_geocodes.append(gc_txt)

        return checked_ids, list(dict.fromkeys(checked_geocodes))

    def _sync_remove_geocodes(
        self,
        geocodes: List[str],
        popcen_ids: Optional[List[Any]] = None,
        earf_ids: Optional[List[Any]] = None,
        gpkg_ids: Optional[List[Any]] = None,
        trigger_source: str = "POPCEN"
    ) -> Dict[str, int]:
        """
        Synchronously purges records across ALL databases and in-memory datasets:
          1. popcen_records (popcen_database.db)
          2. earf_records (earf_database.db)
          3. gpkg_records (gpkg_database.db)
          4. matched_records (popcen_database.db)
          5. In-memory dataframes: self.popcen_df, self.earf_df, self.gpkg_df
        And refreshes KPIs, pagination, and views across Sub-Tabs 1, 2, 3, and Tab 1.
        """
        clean_geocodes = list({
            str(gc).strip() for gc in geocodes
            if gc and str(gc).strip() and str(gc).strip().lower() not in ("nan", "none", "")
        })

        # 1. Database level deletion
        db_counts = {"popcen": 0, "earf": 0, "gpkg": 0, "matched": 0}
        if clean_geocodes:
            db_counts = self.db.delete_records_by_geocodes(clean_geocodes)

        # In case some records without geocodes were selected by ID
        if popcen_ids:
            int_pop_ids = [int(x) for x in popcen_ids if str(x).isdigit()]
            if int_pop_ids:
                del_pop = self.db.delete_popcen_records_by_ids(int_pop_ids)
                db_counts["popcen"] = max(db_counts.get("popcen", 0), del_pop)

        if earf_ids:
            int_earf_ids = [int(x) for x in earf_ids if str(x).isdigit()]
            if int_earf_ids:
                del_earf = self.db.delete_earf_records_by_ids(int_earf_ids)
                db_counts["earf"] = max(db_counts.get("earf", 0), del_earf)

        if gpkg_ids:
            int_gpkg_ids = [int(x) for x in gpkg_ids if str(x).isdigit()]
            if int_gpkg_ids:
                del_gpkg = self.db.delete_gpkg_records_by_ids(int_gpkg_ids)
                db_counts["gpkg"] = max(db_counts.get("gpkg", 0), del_gpkg)

        # 2. In-memory POPCEN DataFrame
        if hasattr(self, "popcen_df") and not self.popcen_df.empty:
            if clean_geocodes and "geocode" in self.popcen_df.columns:
                self.popcen_df = self.popcen_df[~self.popcen_df["geocode"].astype(str).str.strip().isin(clean_geocodes)]
            if popcen_ids:
                if "id" in self.popcen_df.columns:
                    self.popcen_df = self.popcen_df[~self.popcen_df["id"].isin(popcen_ids)]
                drop_idx = [i for i in popcen_ids if i in self.popcen_df.index]
                if drop_idx:
                    self.popcen_df = self.popcen_df.drop(index=drop_idx, errors="ignore")

        # 3. In-memory EARF DataFrame
        if hasattr(self, "earf_df") and not self.earf_df.empty:
            if clean_geocodes and "geocode" in self.earf_df.columns:
                self.earf_df = self.earf_df[~self.earf_df["geocode"].astype(str).str.strip().isin(clean_geocodes)]
            if earf_ids:
                if "id" in self.earf_df.columns:
                    self.earf_df = self.earf_df[~self.earf_df["id"].isin(earf_ids)]
                drop_idx = [i for i in earf_ids if i in self.earf_df.index]
                if drop_idx:
                    self.earf_df = self.earf_df.drop(index=drop_idx, errors="ignore")

        # 4. In-memory GPKG DataFrame
        if hasattr(self, "gpkg_df") and not self.gpkg_df.empty:
            if clean_geocodes and "geocode" in self.gpkg_df.columns:
                self.gpkg_df = self.gpkg_df[~self.gpkg_df["geocode"].astype(str).str.strip().isin(clean_geocodes)]
            if gpkg_ids:
                if "id" in self.gpkg_df.columns:
                    self.gpkg_df = self.gpkg_df[~self.gpkg_df["id"].isin(gpkg_ids)]
                drop_idx = [i for i in gpkg_ids if i in self.gpkg_df.index]
                if drop_idx:
                    self.gpkg_df = self.gpkg_df.drop(index=drop_idx, errors="ignore")

            if hasattr(self.gpkg_df, "_consistency_summary") or ("consistency_status" in self.gpkg_df.columns):
                try:
                    self.gpkg_df, summary = analyze_gpkg_consistency(self.gpkg_df)
                    self.gpkg_df._consistency_summary = summary
                except Exception:
                    pass

        # 5. Refresh Sub-Tab 1 (POPCEN)
        try:
            self.popcen_select_all_global = False
            self.popcen_selected_ids.clear()
            self.popcen_deselected_ids.clear()
            if self.cmb_source_mode.currentIndex() == 0:
                self._load_database_kpis()
                total_db = self.db.record_count()
                max_page = max(0, (total_db - 1) // self.page_size) if total_db > 0 else 0
                if self.current_page > max_page:
                    self.current_page = max_page
                self._fetch_and_render_db_page()
            else:
                self._update_file_kpis()
                total_loaded = len(self.popcen_df) if hasattr(self, "popcen_df") else 0
                max_page = max(0, (total_loaded - 1) // self.page_size) if total_loaded > 0 else 0
                if self.current_page > max_page:
                    self.current_page = max_page
                self._apply_file_filters()
            self._update_popcen_selection_state()
        except Exception:
            pass

        # 6. Refresh Sub-Tab 2 (EARF)
        try:
            self.earf_select_all_global = False
            self.earf_selected_ids.clear()
            self.earf_deselected_ids.clear()
            if self.cmb_earf_source_mode.currentIndex() == 0:
                self._load_earf_db_kpis()
                total_db = self.db.earf_record_count()
                max_page = max(0, (total_db - 1) // self.earf_page_size) if total_db > 0 else 0
                if self.earf_page > max_page:
                    self.earf_page = max_page
                self._fetch_and_render_earf_db_page()
            else:
                self._update_earf_file_kpis()
                total_loaded = len(self.earf_df) if hasattr(self, "earf_df") else 0
                max_page = max(0, (total_loaded - 1) // self.earf_page_size) if total_loaded > 0 else 0
                if self.earf_page > max_page:
                    self.earf_page = max_page
                self._apply_earf_file_filters()
            self._update_earf_selection_state()
        except Exception:
            pass

        # 7. Refresh Sub-Tab 3 (GPKG)
        try:
            self.gpkg_select_all_global = False
            self.gpkg_selected_ids.clear()
            self.gpkg_deselected_ids.clear()
            is_gpkg_db = getattr(self, "cmb_gpkg_source_mode", None) is not None and self.cmb_gpkg_source_mode.currentIndex() == 0
            if is_gpkg_db:
                self._load_gpkg_db_kpis()
                total_db = self.db.gpkg_record_count()
                max_page = max(0, (total_db - 1) // self.gpkg_page_size) if total_db > 0 else 0
                if self.gpkg_page > max_page:
                    self.gpkg_page = max_page
                self._fetch_and_render_gpkg_db_page()
            else:
                self._update_gpkg_kpis()
                total_loaded = len(self.gpkg_df) if hasattr(self, "gpkg_df") else 0
                max_page = max(0, (total_loaded - 1) // self.gpkg_page_size) if total_loaded > 0 else 0
                if self.gpkg_page > max_page:
                    self.gpkg_page = max_page
                self._apply_gpkg_filters()
            self._update_gpkg_selection_state()
        except Exception:
            pass

        # 8. Refresh Tab 1 (Master Geocode Matching)
        try:
            self._check_and_load_matching_data()
        except Exception:
            pass

        return db_counts

    def _remove_selected_popcen_records(self):
        """Permanently removes all checked POPCEN records and synchronizes removal across EARF and GPKG workspaces."""
        checked_ids, checked_geocodes = self._get_selected_popcen_records()

        if not checked_ids and not checked_geocodes:
            QMessageBox.information(
                self,
                "No Selection",
                "Please check the checkbox at the left side of the POPCEN record(s) you wish to remove."
            )
            return

        count = max(len(checked_ids), len(checked_geocodes))
        is_db_mode = (self.cmb_source_mode.currentIndex() == 0)
        target_desc = "local SQLite database (`popcen_records`)" if is_db_mode else "loaded dataset in memory"

        ret = QMessageBox.question(
            self,
            "Confirm Synchronized Record Removal",
            f"Are you sure you want to permanently delete {count:,} selected POPCEN record(s) from the {target_desc}?\n\n"
            f"Cascading Synchronized Removal:\n"
            f" • Sub-Tab 1: 2024 POPCEN (popcen_database.db)\n"
            f" • Sub-Tab 2: Preliminary EARF (earf_database.db)\n"
            f" • Sub-Tab 3: EA GeoPackages (gpkg_database.db)\n"
            f" • Tab 1: Master Geocode Matching (matched_records)\n\n"
            f"Matching EA geocodes will be permanently deleted across all databases and will no longer be visible in Sub-Tab 2 and Sub-Tab 3.\n\n"
            f"This action cannot be undone. Proceed?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if ret != QMessageBox.Yes:
            return

        try:
            counts = self._sync_remove_geocodes(
                geocodes=checked_geocodes,
                popcen_ids=checked_ids,
                trigger_source="POPCEN"
            )
            self.lbl_table_info.setText(
                f"Successfully deleted {count:,} POPCEN record(s) with synchronized cascade across all databases."
            )
            QMessageBox.information(
                self,
                "Records Removed",
                f"Successfully removed {count:,} POPCEN record(s).\n\n"
                f"Synchronized database updates:\n"
                f" • POPCEN records deleted: {counts.get('popcen', 0):,}\n"
                f" • EARF records deleted: {counts.get('earf', 0):,}\n"
                f" • GPKG records deleted: {counts.get('gpkg', 0):,}\n"
                f" • Matched records purged: {counts.get('matched', 0):,}"
            )
        except Exception as e:
            QMessageBox.critical(
                self,
                "Removal Error",
                f"Failed to remove POPCEN records:\n{e}"
            )

    # ----------------------------------------------------------------------
    # POPCEN Import / File Actions
    # ----------------------------------------------------------------------
    def _import_into_database_prompt(self):
        f, _ = QFileDialog.getOpenFileName(
            self, "Select POPCEN File to Import into Local Database", "", "Excel Files (*.xlsx *.xls);;CSV Files (*.csv)"
        )
        if not f:
            return

        ret = QMessageBox.question(
            self,
            "Import Confirmation",
            f"Importing will load records from:\n{os.path.basename(f)}\n\ninto the local SQLite database. Proceed?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if ret != QMessageBox.Yes:
            return

        if self.progress_bar:
            self.progress_bar.setValue(0)
        self.lbl_table_info.setText("Importing records into local database...")
        self.btn_import_db.setEnabled(False)
        if hasattr(self, "btn_popcen_stop"):
            self.btn_popcen_stop.setEnabled(True)

        self.import_worker = DbImportWorker(self.db, f, sheet_name=0)
        self.import_worker.progress_signal.connect(lambda p, m: ((self.progress_bar.setValue(p) if self.progress_bar else None), self.lbl_table_info.setText(m)))
        self.import_worker.finished_signal.connect(self._import_finished)
        self.import_worker.start()

    def _stop_popcen_import(self):
        """Immediately cancels running POPCEN database import."""
        if hasattr(self, "import_worker") and self.import_worker and self.import_worker.isRunning():
            self.import_worker.cancel()
        if hasattr(self, "btn_popcen_stop"):
            self.btn_popcen_stop.setEnabled(False)
        self.btn_import_db.setEnabled(True)
        self.lbl_table_info.setText("POPCEN import stopped by user.")

    def _import_finished(self, count, err_msg):
        self.btn_import_db.setEnabled(True)
        if hasattr(self, "btn_popcen_stop"):
            self.btn_popcen_stop.setEnabled(False)
        if self.progress_bar:
            self.progress_bar.setValue(100)
        if err_msg and err_msg != "Cancelled":
            QMessageBox.critical(self, "Import Failed", f"Error during database import:\n{err_msg}")
            self.lbl_table_info.setText(f"Import failed: {err_msg}")
        elif err_msg == "Cancelled":
            self.lbl_table_info.setText("POPCEN import cancelled by user.")
        else:
            QMessageBox.information(
                self, "Import Successful", f"Successfully imported {count:,} records into the local database!"
            )
            self.cmb_source_mode.setCurrentIndex(0)
            self._check_and_load_initial_data()

    def _browse_popcen_file(self):
        f, _ = QFileDialog.getOpenFileName(
            self, "Select 2024 POPCEN Estimated Number of Households", "", "Excel Files (*.xlsx *.xls);;CSV Files (*.csv)"
        )
        if f:
            self.txt_popcen_file.setText(f)
            self.txt_popcen_quick.setText(f)
            self.cmb_source_mode.setCurrentIndex(1)
            self._load_file_preview()

    def _load_file_preview(self):
        file_path = self.txt_popcen_quick.text().strip() or self.txt_popcen_file.text().strip()
        if not file_path or not os.path.exists(file_path):
            QMessageBox.warning(self, "Select File", "Please select a valid POPCEN Excel or CSV file first.")
            return

        sheet_name = self.txt_sheet_quick.text().strip()
        self.lbl_table_info.setText("Loading and parsing POPCEN file...")
        QApplication.processEvents()

        try:
            self.popcen_df = load_popcen_data(file_path, sheet_name=sheet_name)
        except Exception as e:
            QMessageBox.critical(self, "Load Error", f"Failed to load POPCEN worksheet:\n{e}")
            self.lbl_table_info.setText(f"Error loading table: {e}")
            return

        if self.popcen_df.empty:
            self.lbl_table_info.setText("File loaded, but no data records were found.")
            return

        self._update_file_kpis()
        self._apply_file_filters()

    def _update_file_kpis(self):
        df = self.popcen_df
        if df.empty:
            return
        self.kpi_total.val_label.setText(f"{len(df):,}")
        lvl_col = next((c for c in df.columns if "level" in c.lower()), None)
        if lvl_col:
            ea_count = df[lvl_col].str.contains("5 - EA|EA", case=False, na=False).sum()
        else:
            ea_count = len(df)
        self.kpi_ea.val_label.setText(f"{ea_count:,}")

        hh_col = next((c for c in df.columns if "households" in c.lower()), None)
        if hh_col:
            hh_vals = pd.to_numeric(df[hh_col].astype(str).str.replace(",", "").str.strip(), errors="coerce").fillna(0)
            self.kpi_hh.val_label.setText(f"{int(hh_vals.sum()):,}")

    def _apply_file_filters(self):
        if self.popcen_df.empty:
            self._populate_table(pd.DataFrame(), 0)
            self.lbl_table_info.setText("No POPCEN records to display.")
            return
        df = self.popcen_df.copy()

        lvl_col = find_geographic_level_col(df)
        only_ea = getattr(self, "chk_popcen_only_ea", None) is not None and self.chk_popcen_only_ea.isChecked()

        if only_ea:
            if lvl_col:
                df = df[df[lvl_col].apply(is_ea_level_value)]
        else:
            lvl = self.cmb_level_filter.currentText()
            if lvl_col and lvl != "All Levels":
                df = df[df[lvl_col].astype(str).str.contains(lvl[:6], case=False, na=False)]

        issue = self.cmb_issue_filter.currentText()
        if issue != "All Rows":
            if issue == "EA Rows Only" and lvl_col:
                df = df[df[lvl_col].apply(is_ea_level_value)]
            elif issue == "Subject for Delineation":
                delin_col = next((c for c in df.columns if "delineation" in c.lower()), None)
                if delin_col:
                    df = df[df[delin_col].astype(str).str.strip().replace("-", "").replace("0", "") != ""]
            elif issue == "Subject for Merging":
                merg_col = next((c for c in df.columns if "merging" in c.lower()), None)
                if merg_col:
                    df = df[df[merg_col].astype(str).str.strip().replace("-", "").replace("0", "") != ""]
            elif issue == "Gap / Overlap Issues":
                gap_col = next((c for c in df.columns if "gap" in c.lower() or "overlap" in c.lower()), None)
                if gap_col:
                    df = df[df[gap_col].astype(str).str.lower().str.strip().isin(["gap & overlap", "overlap", "gap"])]
            elif issue == "No Issues Only":
                gap_col = next((c for c in df.columns if "gap" in c.lower() or "overlap" in c.lower()), None)
                if gap_col:
                    df = df[~df[gap_col].astype(str).str.lower().str.strip().isin(["gap & overlap", "overlap", "gap"])]

        query = self.search_input.text().strip().lower()
        if query:
            cols = [c for c in df.columns if any(k in c.lower() for k in ["name", "areaname", "geocode", "ean", "province", "city", "barangay"])]
            mask = False
            for col in cols:
                mask = mask | df[col].astype(str).str.lower().str.contains(query, na=False)
            df = df[mask]

        self.popcen_filtered_df = df
        total_filtered = len(df)
        self.total_filtered_records = total_filtered
        total_pages = max(1, (total_filtered + self.page_size - 1) // self.page_size)

        self.btn_prev_page.setEnabled(self.current_page > 0)
        self.btn_next_page.setEnabled(self.current_page < total_pages - 1)
        self.lbl_page_info.setText(f"Page {self.current_page + 1:,} of {total_pages:,}")

        offset = self.current_page * self.page_size
        df_page = df.iloc[offset : offset + self.page_size]
        self._populate_table(df_page, total_filtered, offset=offset)

        start_num = offset + 1 if total_filtered > 0 else 0
        end_num = min(offset + len(df_page), total_filtered)
        filter_note = " [Filtered: 5 - EA only]" if only_ea else ""
        self.lbl_table_info.setText(
            f"Displaying {start_num:,}–{end_num:,} of {total_filtered:,} filtered records "
            f"(Total loaded: {len(self.popcen_df):,}){filter_note}"
        )

    def _export_preview_csv(self):
        f, _ = QFileDialog.getSaveFileName(self, "Export Current POPCEN Table", "popcen_preview.csv", "CSV Files (*.csv)")
        if not f:
            return
        try:
            only_ea = getattr(self, "chk_popcen_only_ea", None) is not None and self.chk_popcen_only_ea.isChecked()
            if self.cmb_source_mode.currentIndex() == 0:
                self.db.export_filtered_to_csv(
                    f,
                    search=self.search_input.text().strip(),
                    level="5 - EA" if only_ea else self.cmb_level_filter.currentText(),
                    issue=self.cmb_issue_filter.currentText(),
                    only_ea_level=only_ea,
                )
            else:
                out_df = getattr(self, "popcen_filtered_df", self.popcen_df)
                out_df.to_csv(f, index=False, encoding="utf-8")
            QMessageBox.information(self, "Export Success", f"POPCEN table exported to:\n{f}")
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Export failed:\n{e}")

    # ----------------------------------------------------------------------
    # Preliminary EARF Consolidation & Preview Logic
    # ----------------------------------------------------------------------
    def _check_and_load_earf_data(self):
        try:
            self.db.update_earf_source_years()
        except Exception:
            pass
        count = self.db.earf_record_count()
        if count > 0:
            only_new = getattr(self, "chk_earf_only_with_new_ea", None) is not None and self.chk_earf_only_with_new_ea.isChecked()
            kpis = self.db.get_earf_kpis(only_with_new_ea=only_new)
            self.lbl_earf_db_badge.setText(f"EARF Database: {kpis['total_records']:,} records ({count:,} total) across {kpis['total_files']} files")
            self.lbl_earf_db_badge.setStyleSheet("color: #15803d; font-weight: 600; background-color: #dcfce7; padding: 2px 8px; border-radius: 4px;")
            self.cmb_earf_source_mode.setCurrentIndex(0)
            self._load_earf_db_kpis(kpis)
            self._fetch_and_render_earf_db_page()
        else:
            self.lbl_earf_db_badge.setText("EARF Database: Empty")
            self.lbl_earf_db_badge.setStyleSheet("color: #b45309; font-weight: 600; background-color: #fef3c7; padding: 2px 8px; border-radius: 4px;")
            # If files in input line edit, load manual preview
            if self.txt_earf_preview_files.text().strip():
                self._load_earf_preview()

    def _load_earf_db_kpis(self, kpis=None):
        if kpis is None:
            only_new = getattr(self, "chk_earf_only_with_new_ea", None) is not None and self.chk_earf_only_with_new_ea.isChecked()
            kpis = self.db.get_earf_kpis(only_with_new_ea=only_new)
        self.kpi_earf_files.val_label.setText(f"{kpis['total_files']:,}")
        self.kpi_earf_total.val_label.setText(f"{kpis['total_records']:,}")
        self.kpi_earf_unique.val_label.setText(f"{kpis['unique_geocodes']:,}")
        self.kpi_earf_popcen_warn.val_label.setText(f"{kpis['warnings']:,}")
        self.kpi_earf_retained.val_label.setText(f"{kpis['retained']:,}")
        self.kpi_earf_split.val_label.setText(f"{kpis['split']:,}")
        self.kpi_earf_merged.val_label.setText(f"{kpis['merged']:,}")

        if kpis["total_records"] > 0:
            if kpis["errors"] > 0:
                self.kpi_earf_consistency.val_label.setText(f"{kpis['pass_rate']}% Pass ({kpis['errors']} err)")
                self.kpi_earf_consistency.val_label.setStyleSheet("color: #dc2626;")
            elif kpis["warnings"] > 0:
                self.kpi_earf_consistency.val_label.setText(f"{kpis['pass_rate']}% Pass ({kpis['warnings']} warn)")
                self.kpi_earf_consistency.val_label.setStyleSheet("color: #b45309;")
            else:
                self.kpi_earf_consistency.val_label.setText(f"{kpis['pass_rate']}% Pass")
                self.kpi_earf_consistency.val_label.setStyleSheet("color: #15803d;")
        else:
            self.kpi_earf_consistency.val_label.setText("Pending Check")
            self.kpi_earf_consistency.val_label.setStyleSheet("color: #64748b;")

    def _on_earf_source_mode_changed(self):
        mode = self.cmb_earf_source_mode.currentIndex()
        if mode == 0:  # Consolidated Database
            self.earf_folder_bar.setVisible(True)
            self.earf_file_bar.setVisible(False)
            self._check_and_load_earf_data()
        else:  # Manual File Selection
            self.earf_folder_bar.setVisible(False)
            self.earf_file_bar.setVisible(True)
            if not self.earf_df.empty:
                self._apply_earf_file_filters()

    def _refresh_earf_view(self):
        if self.cmb_earf_source_mode.currentIndex() == 0:
            self._check_and_load_earf_data()
        else:
            self._load_earf_preview()

    def _get_initial_earf_dir(self):
        # 1. Preview / loaded layer inputs
        for field in (getattr(self, "txt_earf_preview_files", None), getattr(self, "txt_earf_files", None)):
            if field:
                cur_text = field.text().strip()
                if cur_text:
                    first_path = cur_text.split(";")[0].strip().strip('"').strip("'")
                    if os.path.isdir(first_path):
                        return first_path
                    elif os.path.isfile(first_path) or os.path.exists(first_path):
                        return os.path.dirname(first_path)

        # 2. EARF folder input
        folder_field = getattr(self, "txt_earf_folder", None)
        if folder_field:
            cur_folder = folder_field.text().strip().strip('"').strip("'")
            if os.path.isdir(cur_folder):
                return cur_folder
            elif os.path.isfile(cur_folder) or os.path.exists(cur_folder):
                return os.path.dirname(cur_folder)
            elif cur_folder:
                return cur_folder

        # 3. Fallback to default EARF root directory
        return DEFAULT_EARF_ROOT

    def _browse_earf_folder(self):
        start_dir = self.txt_earf_folder.text().strip() or self._get_initial_earf_dir() or DEFAULT_EARF_ROOT
        folder = QFileDialog.getExistingDirectory(self, "Select Folder Containing Preliminary EARF Files (*_earf*)", start_dir)
        if folder:
            self.txt_earf_folder.setText(folder)
            recursive = self.chk_earf_recursive.isChecked() if hasattr(self, "chk_earf_recursive") else False
            found = find_earf_files_in_folder(folder, recursive=recursive, pattern="_earf")

            # If non-recursive found no files, check if subfolders have files
            if not found and not recursive:
                sub_found = find_earf_files_in_folder(folder, recursive=True, pattern="_earf")
                if sub_found:
                    recursive = True
                    if hasattr(self, "chk_earf_recursive"):
                        self.chk_earf_recursive.setChecked(True)
                    found = sub_found

            if hasattr(self, "lbl_earf_table_info"):
                if found:
                    self.lbl_earf_table_info.setText(f"Found {len(found):,} Preliminary EARF file(s) (*_earf*.xlsx/xls). Ready to consolidate.")
                else:
                    self.lbl_earf_table_info.setText("Warning: No Excel files containing '_earf' in filename found in selected folder.")

    def _consolidate_folder_prompt(self):
        folder = self.txt_earf_folder.text().strip()
        if not folder or not os.path.exists(folder):
            QMessageBox.warning(self, "Select Folder", "Please select a valid folder containing Preliminary EARF Excel files first.")
            return

        recursive = self.chk_earf_recursive.isChecked()
        earf_files = find_earf_files_in_folder(folder, recursive=recursive, pattern="_earf")
        if not earf_files and not recursive:
            sub_files = find_earf_files_in_folder(folder, recursive=True, pattern="_earf")
            if sub_files:
                recursive = True
                if hasattr(self, "chk_earf_recursive"):
                    self.chk_earf_recursive.setChecked(True)
                earf_files = sub_files

        if not earf_files:
            QMessageBox.warning(
                self,
                "No EARF Files Found",
                f"No Excel files containing '_earf' in their filename were found in:\n{folder}\n\n"
                f"(Recursive subfolders: {'Yes' if recursive else 'No'})\n\n"
                "Please verify that the folder contains Excel files matching '*_earf*.xlsx' or '*_earf*.xls'.",
            )
            return

        ret = QMessageBox.question(
            self,
            "Consolidation Confirmation",
            f"Found {len(earf_files):,} Preliminary EARF Excel file(s) (*_earf*.xlsx/xls) in:\n{folder}\n\n"
            f"(Recursive subfolders: {'Yes' if recursive else 'No'})\n\n"
            "This will parse all matching files and save them into the local database (earf_records). Proceed?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if ret != QMessageBox.Yes:
            return

        self.btn_consolidate_earf.setEnabled(False)
        self.btn_run_consistency.setEnabled(False)
        if hasattr(self, "btn_earf_stop"):
            self.btn_earf_stop.setEnabled(True)
        if self.progress_bar:
            self.progress_bar.setValue(0)
        self.lbl_earf_table_info.setText(f"Scanning and consolidating {len(earf_files):,} Preliminary EARF files...")

        self.earf_consolidation_worker = EarfConsolidationWorker(self.db, folder, recursive=recursive, sheet_name=0, file_list=earf_files)
        self.earf_consolidation_worker.progress_signal.connect(
            lambda p, m: ((self.progress_bar.setValue(p) if self.progress_bar else None), self.lbl_earf_table_info.setText(m))
        )
        self.earf_consolidation_worker.finished_signal.connect(self._earf_consolidation_finished)
        self.earf_consolidation_worker.start()

    def _stop_earf_worker(self):
        """Immediately stops the running EARF consolidation or consistency check."""
        stopped = False
        if hasattr(self, "earf_consolidation_worker") and self.earf_consolidation_worker and self.earf_consolidation_worker.isRunning():
            self.earf_consolidation_worker.cancel()
            stopped = True
        if hasattr(self, "earf_consistency_worker") and self.earf_consistency_worker and self.earf_consistency_worker.isRunning():
            self.earf_consistency_worker.cancel()
            stopped = True
        if stopped:
            if hasattr(self, "btn_earf_stop"):
                self.btn_earf_stop.setEnabled(False)
            self.btn_consolidate_earf.setEnabled(True)
            self.btn_run_consistency.setEnabled(True)
            self.lbl_earf_table_info.setText("EARF process stopped by user.")

    def _earf_consolidation_finished(self, recs, files, err):
        self.btn_consolidate_earf.setEnabled(True)
        self.btn_run_consistency.setEnabled(True)
        if hasattr(self, "btn_earf_stop"):
            self.btn_earf_stop.setEnabled(False)
        if self.progress_bar:
            self.progress_bar.setValue(100)

        if err and err != "Cancelled":
            QMessageBox.critical(self, "Consolidation Error", f"Failed to consolidate EARF files:\n{err}")
            self.lbl_earf_table_info.setText(f"Error: {err}")
            return
        elif err == "Cancelled":
            self.lbl_earf_table_info.setText("EARF consolidation cancelled by user.")
            return

        QMessageBox.information(
            self,
            "Consolidation Successful",
            f"Successfully consolidated {recs:,} Preliminary EARF records across {files} Excel file(s) into the local database!",
        )

        # Refresh database view
        self.cmb_earf_source_mode.setCurrentIndex(0)
        self._check_and_load_earf_data()

        # Prompt consistency check
        chk_prompt = QMessageBox.question(
            self,
            "Run Consistency Check?",
            "Do you want to run the multi-rule Consistency Check on the consolidated records now?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if chk_prompt == QMessageBox.Yes:
            self._run_consistency_check_prompt()

    def _run_consistency_check_prompt(self):
        try:
            self.db.update_earf_source_years()
        except Exception:
            pass
        count = self.db.earf_record_count()
        if count == 0:
            QMessageBox.warning(self, "No Records", "Please consolidate EARF files into the database before running consistency checks.")
            return

        self.btn_run_consistency.setEnabled(False)
        if hasattr(self, "btn_earf_stop"):
            self.btn_earf_stop.setEnabled(True)
        if self.progress_bar:
            self.progress_bar.setValue(0)
        self.lbl_earf_table_info.setText("Running R1 & R6 consistency checks across EARF records...")

        self.earf_consistency_worker = EarfConsistencyWorker(self.db, check_popcen=True)
        self.earf_consistency_worker.progress_signal.connect(
            lambda p, m: ((self.progress_bar.setValue(p) if self.progress_bar else None), self.lbl_earf_table_info.setText(m))
        )
        self.earf_consistency_worker.finished_signal.connect(self._earf_consistency_finished)
        self.earf_consistency_worker.start()

    def _earf_consistency_finished(self, results, err):
        self.btn_run_consistency.setEnabled(True)
        if hasattr(self, "btn_earf_stop"):
            self.btn_earf_stop.setEnabled(False)
        if self.progress_bar:
            self.progress_bar.setValue(100)

        if err and err != "Cancelled":
            QMessageBox.critical(self, "Consistency Check Error", f"Error during consistency evaluation:\n{err}")
            self.lbl_earf_table_info.setText(f"Check error: {err}")
            return
        elif err == "Cancelled":
            self.lbl_earf_table_info.setText("EARF consistency check cancelled by user.")
            return
            self.lbl_earf_table_info.setText(f"Check error: {err}")
            return

        # Reload KPIs and refresh table
        self._check_and_load_earf_data()

        total = results.get("total_checked", 0)
        passed = results.get("passed", 0)
        warn = results.get("warnings", 0)
        err_cnt = results.get("errors", 0)
        rate = results.get("pass_rate", 0.0)

        msg = (
            f"Consistency Check Complete (R1 & R6)!\n\n"
            f"• Total Records Evaluated: {total:,}\n"
            f"• Passed (R1 & R6): {passed:,}\n"
            f"• R6 Warnings (Missing in POPCEN): {warn:,}\n"
            f"• R1 Errors (Invalid Geocode): {err_cnt:,}\n\n"
            f"Pass Rate: {rate}%\n\n"
            "Use the 'Consistency' filter dropdown to isolate records missing in POPCEN or with invalid geocodes."
        )
        QMessageBox.information(self, "Consistency Results", msg)

    def _fetch_and_render_earf_db_page(self):
        search_txt = self.search_earf.text().strip()
        ea_type = self.cmb_earf_type_filter.currentText()
        cons_filter = self.cmb_earf_consistency_filter.currentText()
        only_new = getattr(self, "chk_earf_only_with_new_ea", None) is not None and self.chk_earf_only_with_new_ea.isChecked()

        t0 = time.time()
        offset = self.earf_page * self.earf_page_size
        records, total_filtered = self.db.query_earf_records(
            search=search_txt,
            ea_type=ea_type,
            consistency_filter=cons_filter,
            only_with_new_ea=only_new,
            limit=self.earf_page_size,
            offset=offset,
        )
        query_ms = round((time.time() - t0) * 1000, 1)

        self.earf_total_filtered = total_filtered
        total_pages = max(1, (total_filtered + self.earf_page_size - 1) // self.earf_page_size)

        self.btn_earf_prev.setEnabled(self.earf_page > 0)
        self.btn_earf_next.setEnabled(self.earf_page < total_pages - 1)
        self.lbl_earf_page_info.setText(f"Page {self.earf_page + 1:,} of {total_pages:,}")

        df_page = pd.DataFrame(records) if records else pd.DataFrame()
        self._populate_earf_table(df_page, total_filtered, offset=offset)

        start_num = offset + 1 if total_filtered > 0 else 0
        end_num = min(offset + len(records), total_filtered)
        db_total = self.db.earf_record_count()
        filter_note = " [Filtered: New EA Code (2026) only]" if only_new else ""
        self.lbl_earf_table_info.setText(
            f"Displaying {start_num:,}–{end_num:,} of {total_filtered:,} filtered EARF records "
            f"(Total DB: {db_total:,}){filter_note} | Query time: {query_ms} ms"
        )

    def _browse_earf_preview(self):
        init_dir = self._get_initial_earf_dir() or DEFAULT_EARF_ROOT
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Preliminary EARF Excel File(s)",
            init_dir,
            "Preliminary EARF Excel (*_earf*.xlsx *_earf*.xls);;All Excel Files (*.xlsx *.xls);;All Files (*.*)",
        )
        if files:
            p_str = ";".join(files)
            self.txt_earf_preview_files.setText(p_str)
            if hasattr(self, "txt_earf_files"):
                self.txt_earf_files.setText(p_str)
            self._load_earf_preview()

    def _browse_earf_folder_preview(self):
        start_dir = self._get_initial_earf_dir() or DEFAULT_EARF_ROOT
        folder = QFileDialog.getExistingDirectory(self, "Select Folder Containing Preliminary EARF Files (*_earf*)", start_dir)
        if folder:
            earf_files = find_earf_files_in_folder(folder, recursive=True, pattern="_earf")
            if not earf_files:
                QMessageBox.warning(
                    self,
                    "No EARF Files Found",
                    f"No Excel files containing '_earf' in their filename were found in:\n{folder}\n\n"
                    "Please verify that the folder contains Excel files matching '*_earf*.xlsx' or '*_earf*.xls'.",
                )
                return
            p_str = ";".join(earf_files)
            self.txt_earf_preview_files.setText(p_str)
            if hasattr(self, "txt_earf_files"):
                self.txt_earf_files.setText(p_str)
            self._load_earf_preview()

    def _load_earf_preview(self):
        files_str = self.txt_earf_preview_files.text().strip()
        if not files_str and hasattr(self, "txt_earf_files"):
            files_str = self.txt_earf_files.text().strip()
        if not files_str:
            return

        raw_items = [s.strip().strip('"').strip("'") for s in files_str.replace("\r", "\n").replace(";", "\n").split("\n") if s.strip()]
        file_list = []
        for item in raw_items:
            if os.path.isdir(item):
                matched = find_earf_files_in_folder(item, recursive=True, pattern="_earf")
                file_list.extend(matched)
            elif os.path.isfile(item):
                file_list.append(item)

        # Deduplicate preserving order
        seen = set()
        deduped = []
        for f in file_list:
            norm_f = os.path.normpath(f)
            if norm_f.lower() not in seen:
                seen.add(norm_f.lower())
                deduped.append(norm_f)

        file_list = deduped
        if not file_list:
            self.lbl_earf_table_info.setText("No valid Preliminary EARF files (*_earf*.xlsx/xls) found.")
            return

        sheet_name = self.txt_earf_preview_sheet.text().strip()
        self.lbl_earf_table_info.setText(f"Loading {len(file_list)} Preliminary EARF file(s)...")
        QApplication.processEvents()

        earf_dfs = []
        for fpath in file_list:
            try:
                df_i = read_earf_table(fpath, sheet_name=sheet_name)
                if not df_i.empty:
                    df_i["source_file"] = os.path.basename(fpath)
                    df_i["consistency_status"] = "NOT_CHECKED"
                    df_i["consistency_notes"] = "Manual file mode"
                    earf_dfs.append(df_i)
            except Exception as e:
                self.lbl_earf_table_info.setText(f"Error loading {os.path.basename(fpath)}: {e}")

        if earf_dfs:
            self.earf_df = pd.concat(earf_dfs, ignore_index=True)
            type_col = next((c for c in self.earf_df.columns if any(k in str(c).strip().lower() for k in ["ea_type", "ea type", "type"])), None)
            year_col = next((c for c in self.earf_df.columns if any(k in str(c).strip().lower() for k in ["source_year", "source year", "source yr", "sy", "year"])), None)
            if not year_col:
                year_col = "source_year"
                self.earf_df[year_col] = "2026"
            if type_col:
                mask_retained = self.earf_df[type_col].astype(str).str.strip().str.upper().str.contains("RETAIN", na=False)
                self.earf_df.loc[mask_retained, year_col] = "2024"
                self.earf_df.loc[~mask_retained, year_col] = "2026"
            else:
                self.earf_df[year_col] = "2026"
        else:
            self.earf_df = pd.DataFrame()

        self._update_earf_file_kpis(len(file_list))
        self.earf_page = 0
        self._apply_earf_file_filters()

    def _update_earf_file_kpis(self, file_count=None):
        df = self.earf_df
        if file_count is None:
            if "source_file" in df.columns:
                file_count = df["source_file"].nunique()
            else:
                try:
                    file_count = int(self.kpi_earf_files.val_label.text().replace(",", ""))
                except Exception:
                    file_count = 1
        self.kpi_earf_files.val_label.setText(f"{file_count:,}")
        if df.empty:
            self.kpi_earf_total.val_label.setText("0")
            self.kpi_earf_unique.val_label.setText("0")
            self.kpi_earf_retained.val_label.setText("0")
            self.kpi_earf_split.val_label.setText("0")
            self.kpi_earf_merged.val_label.setText("0")
            self.kpi_earf_multi.val_label.setText("0")
            self.kpi_earf_consistency.val_label.setText("Manual Mode")
            return

        only_new = getattr(self, "chk_earf_only_with_new_ea", None) is not None and self.chk_earf_only_with_new_ea.isChecked()
        if only_new:
            new_ea_col = find_new_ea_code_col(df)
            if new_ea_col:
                df = df[df[new_ea_col].apply(has_new_ea_value)]

        self.kpi_earf_total.val_label.setText(f"{len(df):,}")
        unique_gc = df["geocode"].nunique() if "geocode" in df.columns else 0
        self.kpi_earf_unique.val_label.setText(f"{unique_gc:,}")

        type_col = next((c for c in df.columns if "type" in c.lower()), None)
        if type_col:
            types_upper = df[type_col].astype(str).str.upper()
            retained = types_upper.str.contains("RETAIN", na=False).sum()
            split = types_upper.str.contains("SPLIT", na=False).sum()
            merged = types_upper.str.contains("MERG", na=False).sum()
            self.kpi_earf_retained.val_label.setText(f"{retained:,}")
            self.kpi_earf_split.val_label.setText(f"{split:,}")
            self.kpi_earf_merged.val_label.setText(f"{merged:,}")
        else:
            self.kpi_earf_retained.val_label.setText("-")
            self.kpi_earf_split.val_label.setText("-")
            self.kpi_earf_merged.val_label.setText("-")

        self.kpi_earf_consistency.val_label.setText("Consolidate to Check")
        self.kpi_earf_consistency.val_label.setStyleSheet("color: #64748b;")

    def _apply_earf_file_filters(self):
        if self.earf_df.empty:
            self._populate_earf_table(pd.DataFrame(), 0)
            self.lbl_earf_table_info.setText("No EARF records to display.")
            return

        df = self.earf_df.copy()
        only_new = getattr(self, "chk_earf_only_with_new_ea", None) is not None and self.chk_earf_only_with_new_ea.isChecked()
        if only_new:
            new_ea_col = find_new_ea_code_col(df)
            if new_ea_col:
                df = df[df[new_ea_col].apply(has_new_ea_value)]

        ea_type_filter = self.cmb_earf_type_filter.currentText()
        type_col = next((c for c in df.columns if "type" in c.lower()), None)
        if type_col and ea_type_filter != "All EA Types":
            if ea_type_filter == "Other":
                df = df[~df[type_col].astype(str).str.upper().str.contains("RETAIN|SPLIT|MERG", na=False)]
            else:
                df = df[df[type_col].astype(str).str.upper().str.contains(ea_type_filter, na=False)]

        query = self.search_earf.text().strip().lower()
        if query:
            cols = [c for c in df.columns if any(k in c.lower() for k in ["geocode", "prov", "mun", "brgy", "ea", "name", "areaname", "code", "type"])]
            mask = False
            for c in cols:
                mask = mask | df[c].astype(str).str.lower().str.contains(query, na=False)
            df = df[mask]

        self.earf_filtered_df = df
        total_filtered = len(df)
        total_pages = max(1, (total_filtered + self.earf_page_size - 1) // self.earf_page_size)

        self.btn_earf_prev.setEnabled(self.earf_page > 0)
        self.btn_earf_next.setEnabled(self.earf_page < total_pages - 1)
        self.lbl_earf_page_info.setText(f"Page {self.earf_page + 1:,} of {total_pages:,}")

        offset = self.earf_page * self.earf_page_size
        df_page = df.iloc[offset:offset + self.earf_page_size]
        self._populate_earf_table(df_page, total_filtered, offset=offset)

        start_num = offset + 1 if total_filtered > 0 else 0
        end_num = min(offset + len(df_page), total_filtered)
        filter_note = " [Filtered: New EA Code (2026) only]" if only_new else ""
        self.lbl_earf_table_info.setText(
            f"Displaying {start_num:,}–{end_num:,} of {total_filtered:,} filtered EARF records "
            f"(Total loaded: {len(self.earf_df):,}){filter_note}"
        )

    def _on_earf_search_changed(self):
        self.earf_page = 0
        self._reset_earf_selection()
        if self.cmb_earf_source_mode.currentIndex() == 0:
            self._fetch_and_render_earf_db_page()
        else:
            self._apply_earf_file_filters()

    def _on_earf_filter_changed(self):
        self.earf_page = 0
        self._reset_earf_selection()
        if self.cmb_earf_source_mode.currentIndex() == 0:
            self._load_earf_db_kpis()
            self._fetch_and_render_earf_db_page()
        else:
            file_count = len([f for f in self.txt_earf_preview_files.text().split(";") if f.strip()]) if self.txt_earf_preview_files.text() else 1
            self._update_earf_file_kpis(file_count)
            self._apply_earf_file_filters()

    def _on_earf_page_size_changed(self):
        try:
            self.earf_page_size = int(self.cmb_earf_page_size.currentText())
        except ValueError:
            self.earf_page_size = 1000
        self.earf_page = 0
        if self.cmb_earf_source_mode.currentIndex() == 0:
            self._fetch_and_render_earf_db_page()
        else:
            self._apply_earf_file_filters()

    def _earf_prev_page(self):
        if self.earf_page > 0:
            self.earf_page -= 1
            if self.cmb_earf_source_mode.currentIndex() == 0:
                self._fetch_and_render_earf_db_page()
            else:
                self._apply_earf_file_filters()

    def _earf_next_page(self):
        total_count = self.earf_total_filtered if self.cmb_earf_source_mode.currentIndex() == 0 else len(self.earf_filtered_df)
        total_pages = max(1, (total_count + self.earf_page_size - 1) // self.earf_page_size)
        if self.earf_page < total_pages - 1:
            self.earf_page += 1
            if self.cmb_earf_source_mode.currentIndex() == 0:
                self._fetch_and_render_earf_db_page()
            else:
                self._apply_earf_file_filters()

    def _populate_earf_table(self, df: pd.DataFrame, total_filtered: int, offset: int = 0):
        self.table_earf.setUpdatesEnabled(False)
        self.table_earf.blockSignals(True)
        self.table_earf.clear()

        if df.empty:
            self.table_earf.setColumnCount(1)
            self.table_earf.setRowCount(1)
            self.table_earf.setHorizontalHeaderLabels(["Info"])
            item = QTableWidgetItem("No Preliminary EARF records match current filters.")
            item.setForeground(QColor("#64748b"))
            self.table_earf.setItem(0, 0, item)
            self.table_earf.blockSignals(False)
            self.table_earf.setUpdatesEnabled(True)
            self._update_earf_selection_state()
            return

        df = df.copy()
        type_col = next((c for c in df.columns if any(k in str(c).strip().lower() for k in ["ea_type", "ea type", "type"])), None)
        year_col = next((c for c in df.columns if any(k in str(c).strip().lower() for k in ["source_year", "source year", "source yr", "sy", "year"])), None)
        if not year_col:
            year_col = "source_year"
            df[year_col] = "2026"
        if type_col:
            mask_retained = df[type_col].astype(str).str.strip().str.upper().str.contains("RETAIN", na=False)
            df.loc[mask_retained, year_col] = "2024"
            df.loc[~mask_retained, year_col] = "2026"
        else:
            df[year_col] = "2026"

        preferred_order = [
            "geocode",
            "consistency_status",
            "prov",
            "mun",
            "brgy",
            "ea",
            "number_of_eas",
            "areaname",
            "number_of_households",
            "number_of_buildings",
            "new_enumeration_area_code",
            "household_count",
            "building_count",
            "ea_type",
            "source_year",
            "remarks",
            "source_file",
        ]

        disp_cols = []
        new_ea_identified = find_new_ea_code_col(df)
        for p in preferred_order:
            if p == "new_enumeration_area_code" and new_ea_identified and new_ea_identified in df.columns:
                if new_ea_identified not in disp_cols:
                    disp_cols.append(new_ea_identified)
                continue
            found = next((c for c in df.columns if c.strip().lower() == p.strip().lower()), None)
            if found and found not in disp_cols:
                disp_cols.append(found)

        for c in df.columns:
            if c not in disp_cols and c not in ("id", "created_at", "consistency_notes"):
                disp_cols.append(c)

        # Pretty header mapping
        name_map = {
            "geocode": "Geocode",
            "consistency_status": "Consistency",
            "prov": "Prov",
            "mun": "Mun",
            "brgy": "Brgy",
            "ea": "EA",
            "number_of_eas": "Number of EAs",
            "areaname": "Province, City, Municipality, Barangay, and EA",
            "number_of_households": "Number of Households (2024)",
            "number_of_buildings": "Number of Buildings (2024)",
            "new_enumeration_area_code": "New EA Code (2026)",
            "new enumeration area code": "New EA Code (2026)",
            "new enumeration area code (2026)": "New EA Code (2026)",
            "new ea code": "New EA Code (2026)",
            "new ea code (2026)": "New EA Code (2026)",
            "new_ea_code": "New EA Code (2026)",
            "new_ea": "New EA Code (2026)",
            "new_ean": "New EA Code (2026)",
            "household_count": "Household Count (2026)",
            "building_count": "Building Count (2026)",
            "ea_type": "EA Type",
            "source_year": "Source Year",
            "remarks": "Remarks",
            "source_file": "Source File",
            "match_status": "Match Status",
        }

        header_labels = ["☑ Select", "#"] + [name_map.get(c.lower().strip(), c.replace("_", " ").title()) for c in disp_cols]

        self.table_earf.setColumnCount(len(disp_cols) + 2)
        self.table_earf.setRowCount(len(df))
        self.table_earf.setHorizontalHeaderLabels(header_labels)
        self.table_earf.setColumnWidth(0, 68)
        self.table_earf.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self.table_earf.setColumnWidth(1, 55)

        for row_idx, (_, row) in enumerate(df.iterrows()):
            # Column 0: Checkbox for row selection
            chk_item = QTableWidgetItem()
            chk_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
            row_id = row.get("id") if "id" in row and pd.notna(row.get("id")) else row.name
            norm_id = int(row_id) if str(row_id).isdigit() else row_id
            chk_item.setData(Qt.UserRole, row_id)
            gc_val = str(row.get("geocode", "") or "").strip()
            chk_item.setData(Qt.UserRole + 1, gc_val)

            if self.earf_select_all_global:
                deselected = {int(x) if str(x).isdigit() else x for x in self.earf_deselected_ids}
                is_chk = (norm_id not in deselected)
            else:
                selected = {int(x) if str(x).isdigit() else x for x in self.earf_selected_ids}
                is_chk = (norm_id in selected)

            chk_item.setCheckState(Qt.Checked if is_chk else Qt.Unchecked)
            chk_item.setTextAlignment(Qt.AlignCenter)
            self.table_earf.setItem(row_idx, 0, chk_item)

            # Column 1: Row number index
            idx_item = QTableWidgetItem(f"{offset + row_idx + 1:,}")
            idx_item.setTextAlignment(Qt.AlignCenter)
            idx_item.setForeground(QColor("#64748b"))
            self.table_earf.setItem(row_idx, 1, idx_item)

            row_notes = str(row["consistency_notes"]) if "consistency_notes" in row and pd.notna(row["consistency_notes"]) else ""

            for col_idx, col_name in enumerate(disp_cols):
                raw_val = row[col_name]
                val = str(raw_val) if pd.notna(raw_val) else ""
                if val.lower() == "nan":
                    val = ""

                col_low = col_name.lower().strip()
                item = QTableWidgetItem(val)
                is_new_ea = (col_name == new_ea_identified) or ("new" in col_low and any(k in col_low for k in ["ea", "ean", "code", "enumeration"]))

                if is_new_ea or any(k in col_low for k in ["code", "prov", "mun", "brgy", "ea", "geocode", "#", "year", "consistency"]):
                    item.setTextAlignment(Qt.AlignCenter)
                elif any(k in col_low for k in ["count", "households", "buildings", "number of"]):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                else:
                    item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)

                # Badges & Styling
                if "geocode" in col_low:
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                    item.setForeground(QColor("#0369a1"))

                elif is_new_ea:
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                    item.setForeground(QColor("#047857"))  # emerald green
                    if val:
                        item.setBackground(QColor("#ecfdf5"))  # subtle soft-green highlight

                elif "consistency" in col_low:
                    v_up = val.upper()
                    if "PASS" in v_up:
                        item.setText("PASS")
                        item.setBackground(QColor("#dcfce7"))
                        item.setForeground(QColor("#15803d"))
                    elif "MULTI" in v_up:
                        item.setText("MULTI-RECORD")
                        item.setBackground(QColor("#dbeafe"))
                        item.setForeground(QColor("#1d4ed8"))
                    elif "WARN" in v_up:
                        item.setText("WARNING")
                        item.setBackground(QColor("#fef3c7"))
                        item.setForeground(QColor("#b45309"))
                    elif "ERR" in v_up:
                        item.setText("ERROR")
                        item.setBackground(QColor("#fee2e2"))
                        item.setForeground(QColor("#b91c1c"))
                    else:
                        item.setText("Pending")
                        item.setForeground(QColor("#64748b"))

                    if row_notes:
                        item.setToolTip(row_notes)

                elif "type" in col_low:
                    v_up = val.upper()
                    if "RETAIN" in v_up:
                        item.setBackground(QColor("#dcfce7"))
                        item.setForeground(QColor("#15803d"))
                    elif "SPLIT" in v_up:
                        item.setBackground(QColor("#dbeafe"))
                        item.setForeground(QColor("#1d4ed8"))
                    elif "MERG" in v_up:
                        item.setBackground(QColor("#ffedd5"))
                        item.setForeground(QColor("#c2410c"))

                self.table_earf.setItem(row_idx, col_idx + 2, item)

        self.table_earf.blockSignals(False)
        self.table_earf.setUpdatesEnabled(True)
        self._update_earf_selection_state()

    def _export_earf_csv(self):
        f, _ = QFileDialog.getSaveFileName(self, "Export Preliminary EARF Table", "preliminary_earf.csv", "CSV Files (*.csv)")
        if not f:
            return
        try:
            only_new = getattr(self, "chk_earf_only_with_new_ea", None) is not None and self.chk_earf_only_with_new_ea.isChecked()
            if self.cmb_earf_source_mode.currentIndex() == 0:
                self.db.export_filtered_earf_to_csv(
                    f,
                    search=self.search_earf.text().strip(),
                    ea_type=self.cmb_earf_type_filter.currentText(),
                    consistency_filter=self.cmb_earf_consistency_filter.currentText(),
                    only_with_new_ea=only_new,
                )
            else:
                self.earf_filtered_df.to_csv(f, index=False, encoding="utf-8")
            QMessageBox.information(self, "Export Success", f"Preliminary EARF table exported to:\n{f}")
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Export failed:\n{e}")

    def _export_earf_issues_csv(self):
        if self.cmb_earf_source_mode.currentIndex() != 0:
            QMessageBox.information(self, "Database Mode Required", "Please consolidate EARF files into the database and run consistency checks first.")
            return

        f, _ = QFileDialog.getSaveFileName(self, "Export Consistency Issues Report", "earf_consistency_issues.csv", "CSV Files (*.csv)")
        if not f:
            return
        try:
            only_new = getattr(self, "chk_earf_only_with_new_ea", None) is not None and self.chk_earf_only_with_new_ea.isChecked()
            self.db.export_filtered_earf_to_csv(
                f,
                search=self.search_earf.text().strip(),
                ea_type=self.cmb_earf_type_filter.currentText(),
                consistency_filter="All Issues",
                only_with_new_ea=only_new,
            )
            QMessageBox.information(self, "Export Success", f"Consistency issues report exported to:\n{f}")
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Export failed:\n{e}")

    # ----------------------------------------------------------------------
    # Preliminary EARF Record Selection & Removal Logic
    # ----------------------------------------------------------------------
    def _reset_earf_selection(self):
        """Clears all EARF multi-page and local selections."""
        self.earf_select_all_global = False
        self.earf_selected_ids.clear()
        self.earf_deselected_ids.clear()
        self._update_earf_selection_state()

    def _update_earf_selection_state(self):
        """Updates the remove button and select all checkbox based on checked rows/global selection state."""
        if not hasattr(self, "btn_earf_remove") or not hasattr(self, "table_earf"):
            return

        total_matching = getattr(self, "earf_total_filtered", 0)
        if self.table_earf.columnCount() <= 1 or total_matching == 0:
            self.lbl_earf_selected_count.setText("0 selected")
            self.btn_earf_remove.setEnabled(False)
            self.btn_earf_remove.setText("Remove Selected")
            self.chk_earf_select_all.blockSignals(True)
            self.chk_earf_select_all.setCheckState(Qt.Unchecked)
            self.chk_earf_select_all.setEnabled(False)
            self.chk_earf_select_all.blockSignals(False)
            return

        self.chk_earf_select_all.setEnabled(True)

        if self.earf_select_all_global:
            total_selected = max(0, total_matching - len(self.earf_deselected_ids))
        else:
            total_selected = len(self.earf_selected_ids)

        if self.earf_select_all_global and len(self.earf_deselected_ids) == 0:
            self.lbl_earf_selected_count.setText(f"{total_selected:,} selected (all pages)")
        elif total_selected > 0:
            self.lbl_earf_selected_count.setText(f"{total_selected:,} selected")
        else:
            self.lbl_earf_selected_count.setText("0 selected")

        self.btn_earf_remove.setEnabled(total_selected > 0)
        self.btn_earf_remove.setText(f"Remove Selected ({total_selected:,})" if total_selected > 0 else "Remove Selected")

        self.chk_earf_select_all.blockSignals(True)
        if total_matching > 0 and total_selected == total_matching:
            self.chk_earf_select_all.setCheckState(Qt.Checked)
        elif total_selected > 0:
            self.chk_earf_select_all.setCheckState(Qt.PartiallyChecked)
        else:
            self.chk_earf_select_all.setCheckState(Qt.Unchecked)
        self.chk_earf_select_all.blockSignals(False)

    def _on_earf_table_item_changed(self, item):
        if item.column() == 0:
            if getattr(self, "_is_updating_earf_checks", False):
                return
            row_id = item.data(Qt.UserRole)
            if row_id is None:
                return
            norm_id = int(row_id) if str(row_id).isdigit() else row_id
            is_checked = (item.checkState() == Qt.Checked)

            if self.earf_select_all_global:
                if not is_checked:
                    self.earf_deselected_ids.add(norm_id)
                else:
                    self.earf_deselected_ids.discard(norm_id)
            else:
                if is_checked:
                    self.earf_selected_ids.add(norm_id)
                else:
                    self.earf_selected_ids.discard(norm_id)

                total_matching = getattr(self, "earf_total_filtered", 0)
                if total_matching > 0 and len(self.earf_selected_ids) == total_matching:
                    self.earf_select_all_global = True
                    self.earf_selected_ids.clear()
                    self.earf_deselected_ids.clear()

            self._update_earf_selection_state()

    def _on_earf_select_all_toggled(self, state):
        if getattr(self, "_is_updating_earf_checks", False):
            return
        self._is_updating_earf_checks = True
        try:
            is_checked = (state == Qt.Checked or (state != Qt.Unchecked and not self.earf_select_all_global))
            self.earf_select_all_global = is_checked
            self.earf_selected_ids.clear()
            self.earf_deselected_ids.clear()

            self.table_earf.blockSignals(True)
            target_state = Qt.Checked if is_checked else Qt.Unchecked
            for r in range(self.table_earf.rowCount()):
                item = self.table_earf.item(r, 0)
                if item and (item.flags() & Qt.ItemIsUserCheckable):
                    item.setCheckState(target_state)
            self.table_earf.blockSignals(False)
        finally:
            self._is_updating_earf_checks = False

        self._update_earf_selection_state()

    def _on_earf_header_clicked(self, logical_index):
        if logical_index == 0:
            current = (self.chk_earf_select_all.checkState() == Qt.Checked)
            self.chk_earf_select_all.setCheckState(Qt.Unchecked if current else Qt.Checked)

    def _show_earf_table_context_menu(self, pos):
        item = self.table_earf.itemAt(pos)
        if not item or self.table_earf.columnCount() <= 1:
            return
        menu = QMenu(self)
        checked_count = sum(
            1 for r in range(self.table_earf.rowCount())
            if self.table_earf.item(r, 0) and self.table_earf.item(r, 0).checkState() == Qt.Checked
        )
        act_toggle = menu.addAction("Toggle Checkbox for This Row")
        menu.addSeparator()
        if checked_count > 0:
            act_remove = menu.addAction(f"🗑 Remove {checked_count:,} Checked Record(s)")
        else:
            act_remove = menu.addAction("🗑 Remove This Record")

        action = menu.exec_(self.table_earf.viewport().mapToGlobal(pos))
        if action == act_toggle:
            r = item.row()
            chk = self.table_earf.item(r, 0)
            if chk:
                new_state = Qt.Unchecked if chk.checkState() == Qt.Checked else Qt.Checked
                chk.setCheckState(new_state)
        elif action == act_remove:
            if checked_count > 0:
                self._remove_selected_earf_records()
            else:
                r = item.row()
                chk = self.table_earf.item(r, 0)
                if chk:
                    chk.setCheckState(Qt.Checked)
                self._remove_selected_earf_records()

    def _remove_selected_earf_records(self):
        """Permanently removes all checked Preliminary EARF records and cascades removal across POPCEN, GPKG, and matching workspaces."""
        checked_ids, checked_geocodes = self._get_selected_earf_records()

        if not checked_ids and not checked_geocodes:
            QMessageBox.information(
                self,
                "No Selection",
                "Please check the checkbox at the left side of the Preliminary EARF record(s) you wish to remove."
            )
            return

        count = max(len(checked_ids), len(checked_geocodes))
        is_db_mode = (self.cmb_earf_source_mode.currentIndex() == 0)
        target_desc = "local SQLite database (`earf_records`)" if is_db_mode else "loaded dataset in memory"

        ret = QMessageBox.question(
            self,
            "Confirm Synchronized Record Removal",
            f"Are you sure you want to permanently delete {count:,} selected Preliminary EARF record(s) from the {target_desc}?\n\n"
            f"Cascading Synchronized Removal:\n"
            f" • Sub-Tab 1: 2024 POPCEN (popcen_database.db)\n"
            f" • Sub-Tab 2: Preliminary EARF (earf_database.db)\n"
            f" • Sub-Tab 3: EA GeoPackages (gpkg_database.db)\n"
            f" • Tab 1: Master Geocode Matching (matched_records)\n\n"
            f"Matching EA geocodes will be permanently deleted across all databases and will no longer be visible in Sub-Tab 2 and Sub-Tab 3.\n\n"
            f"This action cannot be undone. Proceed?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if ret != QMessageBox.Yes:
            return

        try:
            counts = self._sync_remove_geocodes(
                geocodes=checked_geocodes,
                earf_ids=checked_ids,
                trigger_source="EARF"
            )
            self.lbl_earf_table_info.setText(
                f"Successfully deleted {count:,} Preliminary EARF record(s) with synchronized cascade across all databases."
            )
            QMessageBox.information(
                self,
                "Records Removed",
                f"Successfully removed {count:,} Preliminary EARF record(s).\n\n"
                f"Synchronized database updates:\n"
                f" • POPCEN records deleted: {counts.get('popcen', 0):,}\n"
                f" • EARF records deleted: {counts.get('earf', 0):,}\n"
                f" • GPKG records deleted: {counts.get('gpkg', 0):,}\n"
                f" • Matched records purged: {counts.get('matched', 0):,}"
            )
        except Exception as e:
            QMessageBox.critical(
                self,
                "Removal Error",
                f"Failed to remove Preliminary EARF records:\n{e}"
            )

    # ----------------------------------------------------------------------
    # EA GeoPackages Consolidation & Database Preview Logic
    # ----------------------------------------------------------------------
    def _check_and_load_gpkg_data(self):
        count = self.db.gpkg_record_count()
        if count > 0:
            kpis = self.db.get_gpkg_kpis()
            self.lbl_gpkg_db_badge.setText(f"GPKG Database: {kpis['total_features']:,} records across {kpis['total_files']} files")
            self.lbl_gpkg_db_badge.setStyleSheet("color: #15803d; font-weight: 600; background-color: #dcfce7; padding: 2px 8px; border-radius: 4px;")
            self.cmb_gpkg_source_mode.setCurrentIndex(0)
            self._load_gpkg_db_kpis(kpis)
            self._fetch_and_render_gpkg_db_page()
        else:
            self.lbl_gpkg_db_badge.setText("GPKG Database: Empty")
            self.lbl_gpkg_db_badge.setStyleSheet("color: #b45309; font-weight: 600; background-color: #fef3c7; padding: 2px 8px; border-radius: 4px;")
            if self.txt_gpkg_preview_layers.text().strip():
                self._load_gpkg_preview()

    def _load_gpkg_db_kpis(self, kpis=None):
        if kpis is None:
            kpis = self.db.get_gpkg_kpis()
        self.kpi_gpkg_layers.val_label.setText(f"{kpis['total_files']:,}")
        self.kpi_gpkg_features.val_label.setText(f"{kpis['total_features']:,}")
        self.kpi_gpkg_geoms.val_label.setText(f"{kpis['polygons']:,}")
        self.kpi_gpkg_unique.val_label.setText(f"{kpis['unique_geocodes']:,}")
        self.kpi_gpkg_duplicates.val_label.setText(f"{kpis['duplicate_geocodes']:,}")
        if kpis['duplicate_geocodes'] > 0:
            self.kpi_gpkg_duplicates.val_label.setStyleSheet("color: #d97706; font-weight: bold;")
        else:
            self.kpi_gpkg_duplicates.val_label.setStyleSheet("color: #15803d;")

        self.kpi_gpkg_overlaps.val_label.setText(f"{kpis['cross_file_overlaps']:,}")
        if kpis['cross_file_overlaps'] > 0:
            self.kpi_gpkg_overlaps.val_label.setStyleSheet("color: #dc2626; font-weight: bold;")
        else:
            self.kpi_gpkg_overlaps.val_label.setStyleSheet("color: #64748b;")

        self.kpi_gpkg_consistency.val_label.setText(kpis["consistency_label"])
        self.kpi_gpkg_consistency.val_label.setStyleSheet(f"color: {kpis['consistency_color']}; font-weight: bold;")
        self.kpi_gpkg_retained.val_label.setText(f"{kpis['retained']:,}")
        self.kpi_gpkg_split.val_label.setText(f"{kpis['split']:,}")
        self.kpi_gpkg_merged.val_label.setText(f"{kpis['merged']:,}")

    def _on_gpkg_source_mode_changed(self):
        mode = self.cmb_gpkg_source_mode.currentIndex()
        if mode == 0:  # Consolidated Database
            self.gpkg_folder_bar.setVisible(True)
            self.gpkg_file_bar.setVisible(False)
            self._check_and_load_gpkg_data()
        else:  # Manual File Selection / Layers
            self.gpkg_folder_bar.setVisible(False)
            self.gpkg_file_bar.setVisible(True)
            if not self.gpkg_df.empty:
                self._apply_gpkg_filters()

    def _refresh_gpkg_view(self):
        if self.cmb_gpkg_source_mode.currentIndex() == 0:
            self._check_and_load_gpkg_data()
        else:
            self._load_gpkg_preview()

    def _browse_gpkg_folder(self):
        start_dir = self.txt_gpkg_folder.text().strip() or self._get_initial_gpkg_dir() or DEFAULT_GPKG_ROOT
        folder = QFileDialog.getExistingDirectory(self, "Select Folder Containing GeoPackage Files (*_latest.gpkg)", start_dir)
        if not folder:
            return

        self.txt_gpkg_folder.setText(folder)
        recursive = self.chk_gpkg_recursive.isChecked()
        gpkg_files = find_gpkg_files_in_folder(folder, recursive=recursive, pattern="_latest")

        # If non-recursive found no files, check if subfolders have files
        if not gpkg_files and not recursive:
            sub_files = find_gpkg_files_in_folder(folder, recursive=True, pattern="_latest")
            if sub_files:
                recursive = True
                self.chk_gpkg_recursive.setChecked(True)
                gpkg_files = sub_files

        if hasattr(self, "lbl_gpkg_table_info"):
            if gpkg_files:
                self.lbl_gpkg_table_info.setText(f"Found {len(gpkg_files):,} GeoPackage file(s) (*_latest.gpkg). Ready to consolidate.")
            else:
                self.lbl_gpkg_table_info.setText("Warning: No .gpkg files found in selected folder.")

    def _consolidate_gpkg_folder_prompt(self):
        folder = self.txt_gpkg_folder.text().strip()
        if not folder or not os.path.exists(folder):
            QMessageBox.warning(self, "Select Folder", "Please select a valid folder containing GeoPackage (.gpkg) files first.")
            return

        recursive = self.chk_gpkg_recursive.isChecked()
        gpkg_files = find_gpkg_files_in_folder(folder, recursive=recursive, pattern="_latest")
        if not gpkg_files and not recursive:
            sub_files = find_gpkg_files_in_folder(folder, recursive=True, pattern="_latest")
            if sub_files:
                recursive = True
                self.chk_gpkg_recursive.setChecked(True)
                gpkg_files = sub_files

        if not gpkg_files:
            QMessageBox.warning(
                self,
                "No GeoPackages Found",
                f"No GeoPackage (.gpkg) files found in directory:\n{folder}\n\n"
                "(Note: subfolders named 'versions' are excluded)"
            )
            return

        dlg = GpkgFileSelectionDialog(self, folder, file_paths=gpkg_files, recursive=recursive)
        if dlg.exec_() == QDialog.Accepted:
            selected_files = dlg.get_selected_files()
            if selected_files:
                self.chk_gpkg_recursive.setChecked(dlg.recursive)
                self._start_gpkg_consolidation_files(selected_files, replace=dlg.is_replace_mode())

    def _start_gpkg_consolidation_files(self, selected_files: list, replace: bool = True):
        self.btn_consolidate_gpkg.setEnabled(False)
        self.btn_run_gpkg_consistency.setEnabled(False)
        if hasattr(self, "btn_gpkg_stop"):
            self.btn_gpkg_stop.setEnabled(True)
        if self.progress_bar:
            self.progress_bar.setValue(0)
        self.lbl_gpkg_table_info.setText(f"Consolidating {len(selected_files)} selected GeoPackage file(s) into database...")

        self.gpkg_consolidation_worker = GpkgConsolidationWorker(self.db, file_list=selected_files, replace=replace)
        self.gpkg_consolidation_worker.progress_signal.connect(
            lambda p, m: ((self.progress_bar.setValue(p) if self.progress_bar else None), self.lbl_gpkg_table_info.setText(m))
        )
        self.gpkg_consolidation_worker.finished_signal.connect(self._gpkg_consolidation_finished)
        self.gpkg_consolidation_worker.start()

    def _stop_gpkg_worker(self):
        """Immediately stops the running GeoPackage consolidation or consistency check."""
        stopped = False
        if hasattr(self, "gpkg_consolidation_worker") and self.gpkg_consolidation_worker and self.gpkg_consolidation_worker.isRunning():
            self.gpkg_consolidation_worker.cancel()
            stopped = True
        if hasattr(self, "gpkg_consistency_worker") and self.gpkg_consistency_worker and self.gpkg_consistency_worker.isRunning():
            self.gpkg_consistency_worker.cancel()
            stopped = True
        if stopped:
            if hasattr(self, "btn_gpkg_stop"):
                self.btn_gpkg_stop.setEnabled(False)
            self.btn_consolidate_gpkg.setEnabled(True)
            self.btn_run_gpkg_consistency.setEnabled(True)
            self.lbl_gpkg_table_info.setText("GPKG process stopped by user.")

    def _gpkg_consolidation_finished(self, recs, files, err):
        self.btn_consolidate_gpkg.setEnabled(True)
        self.btn_run_gpkg_consistency.setEnabled(True)
        if hasattr(self, "btn_gpkg_stop"):
            self.btn_gpkg_stop.setEnabled(False)
        if self.progress_bar:
            self.progress_bar.setValue(100)

        if err and err != "Cancelled":
            QMessageBox.critical(self, "Consolidation Error", f"Failed to consolidate GeoPackage files:\n{err}")
            self.lbl_gpkg_table_info.setText(f"Error: {err}")
            return
        elif err == "Cancelled":
            self.lbl_gpkg_table_info.setText("GPKG consolidation cancelled by user.")
            return

        QMessageBox.information(
            self,
            "Consolidation Successful",
            f"Successfully consolidated {recs:,} GeoPackage records across {files} file(s) into the local database (gpkg_records)!",
        )

        # Make sure Sub-tab 3 (GeoPackage) is active, reset page to 0, and refresh view
        if hasattr(self, "sub_tabs"):
            self.sub_tabs.setCurrentIndex(2)
        self.gpkg_page = 0
        self.cmb_gpkg_source_mode.setCurrentIndex(0)
        self._check_and_load_gpkg_data()
        self._check_and_load_matching_data()
        if hasattr(self, "table_gpkg"):
            self.table_gpkg.viewport().update()

        # Prompt consistency check
        chk_prompt = QMessageBox.question(
            self,
            "Run Consistency Check?",
            "Do you want to run the multi-file consistency and duplication check on the consolidated records now?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if chk_prompt == QMessageBox.Yes:
            self._run_gpkg_consistency_check_prompt()

    def _run_gpkg_consistency_check_prompt(self):
        count = self.db.gpkg_record_count()
        if count == 0:
            QMessageBox.warning(self, "No Records", "Please consolidate GeoPackage files into the database before running consistency checks.")
            return

        self.btn_run_gpkg_consistency.setEnabled(False)
        if hasattr(self, "btn_gpkg_stop"):
            self.btn_gpkg_stop.setEnabled(True)
        if self.progress_bar:
            self.progress_bar.setValue(0)
        self.lbl_gpkg_table_info.setText("Running consistency & duplication checks across GPKG database...")

        self.gpkg_consistency_worker = GpkgConsistencyWorker(self.db, check_popcen=True)
        self.gpkg_consistency_worker.progress_signal.connect(
            lambda p, m: ((self.progress_bar.setValue(p) if self.progress_bar else None), self.lbl_gpkg_table_info.setText(m))
        )
        self.gpkg_consistency_worker.finished_signal.connect(self._gpkg_consistency_finished)
        self.gpkg_consistency_worker.start()

    def _gpkg_consistency_finished(self, results, err):
        self.btn_run_gpkg_consistency.setEnabled(True)
        if hasattr(self, "btn_gpkg_stop"):
            self.btn_gpkg_stop.setEnabled(False)
        if self.progress_bar:
            self.progress_bar.setValue(100)

        if err and err != "Cancelled":
            QMessageBox.critical(self, "Consistency Check Error", f"Error during GPKG consistency evaluation:\n{err}")
            self.lbl_gpkg_table_info.setText(f"Check error: {err}")
            return
        elif err == "Cancelled":
            self.lbl_gpkg_table_info.setText("GPKG consistency check cancelled by user.")
            return
            self.lbl_gpkg_table_info.setText(f"Check error: {err}")
            return

        # Reload KPIs and refresh table
        self._check_and_load_gpkg_data()

        total = results.get("total_checked", 0)
        unique = results.get("unique_geocodes", 0)
        overlaps = results.get("cross_file_overlaps", 0)
        multiparts = results.get("multipart_geocodes", 0)
        conflicts = results.get("attribute_conflicts", 0)
        missing_geom = results.get("missing_geometry", 0)
        invalid_gc = results.get("invalid_geocodes", 0)

        msg = (
            f"EA GeoPackage Consistency Check Complete!\n\n"
            f"• Total Features Checked: {total:,}\n"
            f"• Unique Geocodes: {unique:,}\n"
            f"• Cross-File Overlaps: {overlaps:,}\n"
            f"• Multipart EA Geocodes: {multiparts:,}\n"
            f"• Attribute Conflicts: {conflicts:,}\n"
            f"• Missing Geometry: {missing_geom:,}\n"
            f"• Invalid Geocodes: {invalid_gc:,}\n\n"
            "Use the 'Consistency' dropdown filter to isolate overlapping or conflicted records."
        )
        QMessageBox.information(self, "GPKG Consistency Results", msg)

    def _run_gpkg_consistency_dispatcher(self):
        if getattr(self, "cmb_gpkg_source_mode", None) and self.cmb_gpkg_source_mode.currentIndex() == 0:
            self._run_gpkg_consistency_check_prompt()
        else:
            self._run_gpkg_consistency_check()

    def _fetch_and_render_gpkg_db_page(self):
        search_txt = self.search_gpkg.text().strip()
        issue_filter = self.cmb_gpkg_issue_filter.currentText()
        ea_type = self.cmb_gpkg_type_filter.currentText()

        offset = self.gpkg_page * self.gpkg_page_size
        records, total_filtered = self.db.query_gpkg_records(
            search=search_txt,
            ea_type=ea_type,
            issue_filter=issue_filter,
            limit=self.gpkg_page_size,
            offset=offset,
        )
        self.gpkg_total_filtered = total_filtered
        total_pages = max(1, (total_filtered + self.gpkg_page_size - 1) // self.gpkg_page_size)

        self.btn_gpkg_prev.setEnabled(self.gpkg_page > 0)
        self.btn_gpkg_next.setEnabled(self.gpkg_page < total_pages - 1)
        self.lbl_gpkg_page_info.setText(f"Page {self.gpkg_page + 1:,} of {total_pages:,}")

        # Render rows in table
        df_page = pd.DataFrame(records) if records else pd.DataFrame()
        self._populate_gpkg_table(df_page, total_filtered, offset=offset)

        start_num = offset + 1 if total_filtered > 0 else 0
        end_num = min(offset + len(records), total_filtered)
        count = self.db.gpkg_record_count()
        self.lbl_gpkg_table_info.setText(
            f"Displaying {start_num:,}–{end_num:,} of {total_filtered:,} filtered GPKG records "
            f"({count:,} total in database)"
        )
    def _get_initial_gpkg_dir(self):
        # 1. Preview / loaded layer inputs
        for field in (getattr(self, "txt_gpkg_preview_layers", None), getattr(self, "txt_gpkg_layers", None)):
            if field:
                cur_text = field.text().strip()
                if cur_text:
                    first_path = cur_text.split(";")[0].strip().strip('"').strip("'")
                    if os.path.isdir(first_path):
                        return first_path
                    elif os.path.isfile(first_path) or os.path.exists(first_path):
                        return os.path.dirname(first_path)

        # 2. GPKG folder input
        folder_field = getattr(self, "txt_gpkg_folder", None)
        if folder_field:
            cur_folder = folder_field.text().strip().strip('"').strip("'")
            if os.path.isdir(cur_folder):
                return cur_folder
            elif os.path.isfile(cur_folder) or os.path.exists(cur_folder):
                return os.path.dirname(cur_folder)
            elif cur_folder:
                return cur_folder

        # 3. Fallback to default GPKG root directory
        return DEFAULT_GPKG_ROOT

    def _browse_gpkg_preview(self):
        init_dir = self._get_initial_gpkg_dir() or DEFAULT_GPKG_ROOT
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select EA GeoPackage Layer(s)", init_dir, "GeoPackage Files (*.gpkg *.sqlite);;All Files (*.*)"
        )
        if files:
            valid_poly = []
            non_poly = []
            for f in files:
                has_poly, _, _ = get_gpkg_polygon_layer_info(f)
                if has_poly:
                    valid_poly.append(f)
                else:
                    non_poly.append(f)

            if non_poly and not valid_poly:
                QMessageBox.warning(
                    self,
                    "Invalid Geometry Type",
                    "The selected GeoPackage file(s) do not contain polygon vector layers (EA boundaries).\n\n"
                    "Only polygon vector layers can be loaded."
                )
                return
            elif non_poly:
                QMessageBox.warning(
                    self,
                    "Non-Polygon Layers Excluded",
                    f"{len(non_poly)} file(s) were excluded because they do not contain polygon vector layers.\n\n"
                    f"{len(valid_poly)} polygon file(s) will be loaded."
                )

            p_str = ";".join(valid_poly)
            self.txt_gpkg_preview_layers.setText(p_str)
            self.txt_gpkg_layers.setText(p_str)
            self._load_gpkg_preview()

    def _scan_folder_for_gpkg(self):
        init_dir = self._get_initial_gpkg_dir()
        folder = None
        if init_dir and os.path.isdir(init_dir):
            ret = QMessageBox.question(
                self,
                "Scan GPKG Folder",
                f"Scan all GeoPackages in the folder of the current GPKG?\n\n{init_dir}\n\n"
                "(Subfolders named 'versions' will be excluded)\n\n"
                "• Click 'Yes' to scan this folder directly.\n"
                "• Click 'No' to browse and choose a different folder.",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                QMessageBox.Yes
            )
            if ret == QMessageBox.Cancel:
                return []
            elif ret == QMessageBox.Yes:
                folder = init_dir

        if not folder:
            folder = QFileDialog.getExistingDirectory(
                self, "Select Folder Containing GeoPackage Files", init_dir or DEFAULT_GPKG_ROOT
            )
        if not folder:
            return []

        found = find_gpkg_files_in_folder(folder, recursive=True, pattern="_latest")
        if not found:
            QMessageBox.warning(
                self,
                "No GeoPackages Found",
                f"No GeoPackage (.gpkg) files matching '*_latest.gpkg*' found in directory:\n{folder}\n(subfolder 'versions' excluded)"
            )
            return []
        return found

    def _scan_gpkg_folder_preview(self):
        found = self._scan_folder_for_gpkg()
        if found:
            p_str = ";".join(found)
            self.txt_gpkg_preview_layers.setText(p_str)
            self.txt_gpkg_layers.setText(p_str)
            self.lbl_gpkg_table_info.setText(f"Discovered {len(found)} GeoPackage file(s) in folder (excluding version folders).")
            self._load_gpkg_preview()

    def _scan_gpkg_folder_config(self):
        found = self._scan_folder_for_gpkg()
        if found:
            p_str = ";".join(found)
            self.txt_gpkg_layers.setText(p_str)
            self.txt_gpkg_preview_layers.setText(p_str)
            self.console_log.append(f"<span style='color:#059669;'>Discovered and added {len(found)} GeoPackage file(s) from folder (version folders excluded).</span>")

    def _detect_project_layers_preview(self):
        layers = [lyr for lyr in QgsProject.instance().mapLayers().values() if isinstance(lyr, QgsVectorLayer) and lyr.isValid() and is_polygon_layer(lyr)]
        if layers:
            p_str = ";".join([lyr.name() for lyr in layers])
            self.txt_gpkg_preview_layers.setText(p_str)
            self.txt_gpkg_layers.setText(p_str)
            self._load_gpkg_preview()
        else:
            QMessageBox.information(
                self,
                "No Polygon Layers",
                "No valid polygon vector layers (EA boundaries) were found in the current QGIS project.\n\n"
                "Only polygon/multipolygon vector layers can be previewed or processed."
            )

    def _load_gpkg_preview(self):
        sources_str = self.txt_gpkg_preview_layers.text().strip() or self.txt_gpkg_layers.text().strip()
        if not sources_str:
            return

        # Auto-expand directory inputs if user pasted a folder path
        resolved = resolve_gpkg_sources(sources_str)
        if resolved:
            expanded_str = ";".join(resolved)
            if expanded_str != sources_str:
                self.txt_gpkg_preview_layers.setText(expanded_str)
                self.txt_gpkg_layers.setText(expanded_str)
                sources_str = expanded_str

        self.lbl_gpkg_table_info.setText("Loading EA GeoPackage features...")
        QApplication.processEvents()

        try:
            self.gpkg_df = load_gpkg_data(sources_str)
        except Exception as e:
            self.lbl_gpkg_table_info.setText(f"Error loading GeoPackage: {e}")
            self.gpkg_df = pd.DataFrame()

        self._update_gpkg_kpis()
        self.gpkg_page = 0
        self._apply_gpkg_filters()

    def _update_gpkg_kpis(self):
        df = self.gpkg_df
        if df.empty:
            self.kpi_gpkg_layers.val_label.setText("0")
            self.kpi_gpkg_features.val_label.setText("0")
            self.kpi_gpkg_geoms.val_label.setText("0")
            self.kpi_gpkg_unique.val_label.setText("0")
            self.kpi_gpkg_duplicates.val_label.setText("0")
            self.kpi_gpkg_duplicates.val_label.setStyleSheet("color: #64748b;")
            self.kpi_gpkg_overlaps.val_label.setText("0")
            self.kpi_gpkg_overlaps.val_label.setStyleSheet("color: #64748b;")
            self.kpi_gpkg_consistency.val_label.setText("Pending Check")
            self.kpi_gpkg_consistency.val_label.setStyleSheet("color: #64748b;")
            self.kpi_gpkg_retained.val_label.setText("0")
            self.kpi_gpkg_split.val_label.setText("0")
            self.kpi_gpkg_merged.val_label.setText("0")
            return

        layers_cnt = df["layer_source"].nunique() if "layer_source" in df.columns else 1
        self.kpi_gpkg_layers.val_label.setText(f"{layers_cnt:,}")
        self.kpi_gpkg_features.val_label.setText(f"{len(df):,}")

        if "geom_status" in df.columns:
            poly_cnt = df["geom_status"].astype(str).str.contains("Polygon", case=False, na=False).sum()
        else:
            poly_cnt = len(df)
        self.kpi_gpkg_geoms.val_label.setText(f"{poly_cnt:,}")

        summary = getattr(self.gpkg_df, "_consistency_summary", None)
        if not summary:
            _, summary = analyze_gpkg_consistency(self.gpkg_df)

        self.kpi_gpkg_unique.val_label.setText(f"{summary['unique_geocodes']:,}")
        self.kpi_gpkg_duplicates.val_label.setText(f"{summary['total_duplicates']:,}")
        if summary['total_duplicates'] > 0:
            self.kpi_gpkg_duplicates.val_label.setStyleSheet("color: #d97706; font-weight: bold;")
        else:
            self.kpi_gpkg_duplicates.val_label.setStyleSheet("color: #15803d;")

        self.kpi_gpkg_overlaps.val_label.setText(f"{summary['cross_file_overlaps']:,}")
        if summary['cross_file_overlaps'] > 0:
            self.kpi_gpkg_overlaps.val_label.setStyleSheet("color: #dc2626; font-weight: bold;")
            self.kpi_gpkg_consistency.val_label.setText(f"{summary['cross_file_overlaps']} Overlaps")
            self.kpi_gpkg_consistency.val_label.setStyleSheet("color: #dc2626; font-weight: bold;")
        elif summary['attribute_conflicts'] > 0:
            self.kpi_gpkg_consistency.val_label.setText(f"{summary['attribute_conflicts']} Conflicts")
            self.kpi_gpkg_consistency.val_label.setStyleSheet("color: #ea580c; font-weight: bold;")
        elif summary['multipart_geocodes'] > 0:
            self.kpi_gpkg_consistency.val_label.setText(f"{summary['multipart_geocodes']} Multiparts")
            self.kpi_gpkg_consistency.val_label.setStyleSheet("color: #0284c7; font-weight: bold;")
        elif summary['missing_geometry'] > 0 or summary['invalid_geocodes'] > 0:
            self.kpi_gpkg_consistency.val_label.setText("Geom/Code Err")
            self.kpi_gpkg_consistency.val_label.setStyleSheet("color: #dc2626; font-weight: bold;")
        else:
            self.kpi_gpkg_consistency.val_label.setText("100% Clean")
            self.kpi_gpkg_consistency.val_label.setStyleSheet("color: #15803d; font-weight: bold;")

        type_col = next((c for c in df.columns if "ea_type" in c.lower() or "type" in c.lower()), None)
        if type_col:
            types_upper = df[type_col].astype(str).str.upper()
            retained = types_upper.str.contains("RETAIN", na=False).sum()
            split = types_upper.str.contains("SPLIT", na=False).sum()
            merged = types_upper.str.contains("MERG", na=False).sum()
            self.kpi_gpkg_retained.val_label.setText(f"{retained:,}")
            self.kpi_gpkg_split.val_label.setText(f"{split:,}")
            self.kpi_gpkg_merged.val_label.setText(f"{merged:,}")
        else:
            self.kpi_gpkg_retained.val_label.setText("-")
            self.kpi_gpkg_split.val_label.setText("-")
            self.kpi_gpkg_merged.val_label.setText("-")

    def _apply_gpkg_filters(self):
        if self.gpkg_df.empty:
            self._populate_gpkg_table(pd.DataFrame(), 0)
            self.lbl_gpkg_table_info.setText("No EA GeoPackage records to display.")
            return

        df = self.gpkg_df.copy()

        # Consistency issue filter
        if hasattr(self, "cmb_gpkg_issue_filter"):
            issue_filter = self.cmb_gpkg_issue_filter.currentText()
            if "Any Issue" in issue_filter:
                df = df[df["consistency_status"] != "UNIQUE"]
            elif "Cross-File Overlaps" in issue_filter:
                df = df[df["consistency_status"].isin(["CROSS_FILE_DUP", "CONFLICTING_DATA"]) & (df["file_count"] > 1)]
            elif "Multipart" in issue_filter:
                df = df[df["consistency_status"] == "MULTIPART_PARTS"]
            elif "Conflicting" in issue_filter:
                df = df[df["consistency_status"] == "CONFLICTING_DATA"]
            elif "Missing Geometry" in issue_filter:
                df = df[df["consistency_status"] == "NO_GEOMETRY"]
            elif "Unique" in issue_filter:
                df = df[df["consistency_status"] == "UNIQUE"]

        # EA type filter
        ea_type_filter = self.cmb_gpkg_type_filter.currentText()
        type_col = next((c for c in df.columns if "ea_type" in c.lower() or "type" in c.lower()), None)
        if type_col and ea_type_filter != "All EA Types":
            if ea_type_filter == "Other":
                df = df[~df[type_col].astype(str).str.upper().str.contains("RETAIN|SPLIT|MERG", na=False)]
            else:
                df = df[df[type_col].astype(str).str.upper().str.contains(ea_type_filter, na=False)]

        query = self.search_gpkg.text().strip().lower()
        if query:
            cols = [c for c in df.columns if any(k in c.lower() for k in ["geocode", "province", "city", "mun", "barangay", "ean", "name", "layer", "source", "status", "detail"])]
            mask = False
            for c in cols:
                mask = mask | df[c].astype(str).str.lower().str.contains(query, na=False)
            df = df[mask]

        self.gpkg_filtered_df = df
        total_filtered = len(df)
        total_pages = max(1, (total_filtered + self.gpkg_page_size - 1) // self.gpkg_page_size)

        self.btn_gpkg_prev.setEnabled(self.gpkg_page > 0)
        self.btn_gpkg_next.setEnabled(self.gpkg_page < total_pages - 1)
        self.lbl_gpkg_page_info.setText(f"Page {self.gpkg_page + 1:,} of {total_pages:,}")

        offset = self.gpkg_page * self.gpkg_page_size
        df_page = df.iloc[offset:offset + self.gpkg_page_size]
        self._populate_gpkg_table(df_page, total_filtered, offset=offset)

        start_num = offset + 1 if total_filtered > 0 else 0
        end_num = min(offset + len(df_page), total_filtered)
        self.lbl_gpkg_table_info.setText(
            f"Displaying {start_num:,}–{end_num:,} of {total_filtered:,} filtered GPKG features "
            f"(Total loaded: {len(self.gpkg_df):,})"
        )

    def _on_gpkg_search_changed(self):
        self.gpkg_page = 0
        self._reset_gpkg_selection()
        if getattr(self, "cmb_gpkg_source_mode", None) and self.cmb_gpkg_source_mode.currentIndex() == 0:
            self._fetch_and_render_gpkg_db_page()
        else:
            self._apply_gpkg_filters()

    def _on_gpkg_filter_changed(self):
        self.gpkg_page = 0
        self._reset_gpkg_selection()
        if getattr(self, "cmb_gpkg_source_mode", None) and self.cmb_gpkg_source_mode.currentIndex() == 0:
            self._fetch_and_render_gpkg_db_page()
        else:
            self._apply_gpkg_filters()

    def _on_gpkg_page_size_changed(self):
        try:
            self.gpkg_page_size = int(self.cmb_gpkg_page_size.currentText())
        except ValueError:
            self.gpkg_page_size = 1000
        self.gpkg_page = 0
        if getattr(self, "cmb_gpkg_source_mode", None) and self.cmb_gpkg_source_mode.currentIndex() == 0:
            self._fetch_and_render_gpkg_db_page()
        else:
            self._apply_gpkg_filters()

    def _gpkg_prev_page(self):
        if self.gpkg_page > 0:
            self.gpkg_page -= 1
            if getattr(self, "cmb_gpkg_source_mode", None) and self.cmb_gpkg_source_mode.currentIndex() == 0:
                self._fetch_and_render_gpkg_db_page()
            else:
                self._apply_gpkg_filters()

    def _gpkg_next_page(self):
        if getattr(self, "cmb_gpkg_source_mode", None) and self.cmb_gpkg_source_mode.currentIndex() == 0:
            total_pages = max(1, (getattr(self, "gpkg_total_filtered", 0) + self.gpkg_page_size - 1) // self.gpkg_page_size)
            if self.gpkg_page < total_pages - 1:
                self.gpkg_page += 1
                self._fetch_and_render_gpkg_db_page()
        else:
            total_pages = max(1, (len(self.gpkg_filtered_df) + self.gpkg_page_size - 1) // self.gpkg_page_size)
            if self.gpkg_page < total_pages - 1:
                self.gpkg_page += 1
                self._apply_gpkg_filters()

    def _populate_gpkg_table(self, df: pd.DataFrame, total_filtered: int, offset: int = 0):
        self.table_gpkg.setUpdatesEnabled(False)
        self.table_gpkg.blockSignals(True)
        self.table_gpkg.clear()

        if df.empty:
            self.table_gpkg.setColumnCount(1)
            self.table_gpkg.setRowCount(1)
            self.table_gpkg.setHorizontalHeaderLabels(["Info"])
            item = QTableWidgetItem("No EA GeoPackage features match current filters.")
            item.setForeground(QColor("#64748b"))
            self.table_gpkg.setItem(0, 0, item)
            self.table_gpkg.blockSignals(False)
            self.table_gpkg.setUpdatesEnabled(True)
            self._update_gpkg_selection_state()
            return

        df = df.copy()
        if "source_files" in df.columns and "source_file" in df.columns:
            has_multi = df["source_files"].astype(str).str.contains(",", na=False)
            df.loc[has_multi, "source_file"] = df.loc[has_multi, "source_files"]
        elif "source_files" in df.columns and "source_file" not in df.columns:
            df["source_file"] = df["source_files"]

        preferred_order = [
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
            "eacount",
            "remarks",
            "consistency_status",
            "consistency_detail",
            "dup_count",
            "file_count",
            "layer_source",
            "geom_status",
            "source_file",
        ]

        disp_cols = []
        for p in preferred_order:
            found = next((c for c in df.columns if c.strip().lower() == p.strip().lower()), None)
            if found and found not in disp_cols:
                disp_cols.append(found)

        for c in df.columns:
            if c not in disp_cols and c not in ("geom", "id", "created_at", "fid", "source_files"):
                disp_cols.append(c)

        header_labels = ["☑ Select", "#"] + [
            c.replace("_", " ").title().replace("Ean", "EAN").replace("Gpkg", "GPKG").replace("Fid", "FID").replace("Geom Status", "Geometry").replace("Dup Count", "Dup Count").replace("File Count", "Files")
            for c in disp_cols
        ]

        self.table_gpkg.setColumnCount(len(disp_cols) + 2)
        self.table_gpkg.setRowCount(len(df))
        self.table_gpkg.setHorizontalHeaderLabels(header_labels)
        self.table_gpkg.setColumnWidth(0, 68)
        self.table_gpkg.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self.table_gpkg.setColumnWidth(1, 55)

        for row_idx, (_, row) in enumerate(df.iterrows()):
            # Column 0: Checkbox for feature selection
            chk_item = QTableWidgetItem()
            chk_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
            row_id = row["id"] if ("id" in row and pd.notna(row["id"])) else row.name
            norm_id = int(row_id) if str(row_id).isdigit() else row_id
            chk_item.setData(Qt.UserRole, row_id)
            gc_val = str(row.get("geocode", "") or "").strip()
            chk_item.setData(Qt.UserRole + 1, gc_val)

            if self.gpkg_select_all_global:
                deselected = {int(x) if str(x).isdigit() else x for x in self.gpkg_deselected_ids}
                is_chk = (norm_id not in deselected)
            else:
                selected = {int(x) if str(x).isdigit() else x for x in self.gpkg_selected_ids}
                is_chk = (norm_id in selected)

            chk_item.setCheckState(Qt.Checked if is_chk else Qt.Unchecked)
            chk_item.setTextAlignment(Qt.AlignCenter)
            self.table_gpkg.setItem(row_idx, 0, chk_item)

            # Column 1: Row number index
            idx_item = QTableWidgetItem(f"{offset + row_idx + 1:,}")
            idx_item.setTextAlignment(Qt.AlignCenter)
            idx_item.setForeground(QColor("#64748b"))
            self.table_gpkg.setItem(row_idx, 1, idx_item)

            for col_idx, col_name in enumerate(disp_cols):
                raw_val = row[col_name]
                val = str(raw_val) if pd.notna(raw_val) else ""
                if val.lower() == "nan":
                    val = ""

                item = QTableWidgetItem(val)
                col_low = col_name.lower().strip()

                if any(k in col_low for k in ["code", "ean", "geocode", "#", "sy", "uuid", "dup_count", "file_count"]):
                    item.setTextAlignment(Qt.AlignCenter)
                elif any(k in col_low for k in ["count", "hh_", "bldg_"]):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                else:
                    item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)

                if "geocode" in col_low:
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                    item.setForeground(QColor("#0d9488"))
                elif "consistency_status" in col_low or col_low == "status":
                    item.setTextAlignment(Qt.AlignCenter)
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                    v_up = val.upper()
                    if "UNIQUE" in v_up:
                        item.setText("UNIQUE")
                        item.setBackground(QColor("#dcfce7"))
                        item.setForeground(QColor("#15803d"))
                    elif "CROSS_FILE" in v_up or "OVERLAP" in v_up:
                        f_num = row.get("file_count", 2)
                        item.setText(f"OVERLAP ({f_num} files)")
                        item.setBackground(QColor("#fee2e2"))
                        item.setForeground(QColor("#b91c1c"))
                    elif "MULTIPART" in v_up:
                        p_num = row.get("dup_count", 2)
                        item.setText(f"MULTIPART ({p_num} parts)")
                        item.setBackground(QColor("#e0e7ff"))
                        item.setForeground(QColor("#3730a3"))
                    elif "CONFLICT" in v_up:
                        item.setText("DATA CONFLICT")
                        item.setBackground(QColor("#ffedd5"))
                        item.setForeground(QColor("#c2410c"))
                    elif "GEOMETRY" in v_up:
                        item.setText("NO GEOM")
                        item.setBackground(QColor("#fef2f2"))
                        item.setForeground(QColor("#991b1b"))
                    elif "INVALID" in v_up:
                        item.setText("INVALID CODE")
                        item.setBackground(QColor("#f1f5f9"))
                        item.setForeground(QColor("#475569"))
                elif "dup_count" in col_low:
                    try:
                        d_cnt = int(row.get("dup_count", 1))
                    except (ValueError, TypeError):
                        d_cnt = 1
                    if d_cnt > 1:
                        font = item.font()
                        font.setBold(True)
                        item.setFont(font)
                        item.setForeground(QColor("#dc2626"))
                elif "geom" in col_low:
                    item.setTextAlignment(Qt.AlignCenter)
                    if "Polygon" in val:
                        item.setBackground(QColor("#dcfce7"))
                        item.setForeground(QColor("#15803d"))
                    else:
                        item.setBackground(QColor("#fee2e2"))
                        item.setForeground(QColor("#b91c1c"))
                elif "type" in col_low:
                    v_up = val.upper()
                    if "RETAIN" in v_up:
                        item.setBackground(QColor("#dcfce7"))
                        item.setForeground(QColor("#15803d"))
                    elif "SPLIT" in v_up:
                        item.setBackground(QColor("#dbeafe"))
                        item.setForeground(QColor("#1d4ed8"))
                    elif "MERG" in v_up:
                        item.setBackground(QColor("#ffedd5"))
                        item.setForeground(QColor("#c2410c"))

                self.table_gpkg.setItem(row_idx, col_idx + 2, item)

        self.table_gpkg.blockSignals(False)
        self.table_gpkg.setUpdatesEnabled(True)
        self._update_gpkg_selection_state()

    def _run_gpkg_consistency_check(self):
        if self.gpkg_df.empty:
            sources_str = self.txt_gpkg_preview_layers.text().strip() or self.txt_gpkg_layers.text().strip()
            if sources_str:
                self._load_gpkg_preview()
            else:
                QMessageBox.warning(self, "No Source", "Please browse or scan for GeoPackage files first.")
                return

        if self.gpkg_df.empty:
            QMessageBox.warning(self, "No Data", "No GeoPackage features were loaded.")
            return

        summary = getattr(self.gpkg_df, "_consistency_summary", None)
        if not summary:
            _, summary = analyze_gpkg_consistency(self.gpkg_df)

        dlg = GPKGConsistencyDialog(self, summary, self.gpkg_df)
        dlg.exec_()

    def _export_gpkg_duplicate_csv(self):
        if self.gpkg_df.empty:
            QMessageBox.information(self, "No Data", "No EA GeoPackage records loaded.")
            return

        dup_df = self.gpkg_df[self.gpkg_df["consistency_status"] != "UNIQUE"]
        if dup_df.empty:
            QMessageBox.information(
                self,
                "Clean GeoPackages",
                f"All {len(self.gpkg_df):,} records are 100% clean and unique! Zero duplicates or consistency issues detected."
            )
            return

        f, _ = QFileDialog.getSaveFileName(
            self,
            "Export GeoPackage Duplicates & Issues",
            "gpkg_duplicates_report.csv",
            "CSV Files (*.csv)"
        )
        if f:
            try:
                export_dup = dup_df.copy()
                drop_cols = [c for c in ["id", "fid", "created_at", "geom"] if c in export_dup.columns]
                if drop_cols:
                    export_dup = export_dup.drop(columns=drop_cols)
                export_dup.to_csv(f, index=False, encoding="utf-8")
                QMessageBox.information(
                    self,
                    "Export Success",
                    f"Successfully exported {len(dup_df):,} duplicate / issue record(s) to:\n{f}"
                )
            except Exception as e:
                QMessageBox.critical(self, "Export Error", f"Export failed:\n{e}")

    def _export_gpkg_csv(self):
        if self.gpkg_filtered_df.empty:
            QMessageBox.information(self, "No Data", "No EA GeoPackage data to export.")
            return

        f, _ = QFileDialog.getSaveFileName(self, "Export EA GeoPackage Attributes", "ea_geopackages.csv", "CSV Files (*.csv)")
        if f:
            try:
                export_df = self.gpkg_filtered_df.copy()
                for c in ("code", "hhcount", "sy", "hh_count", "bldgcount", "bldg_count", "eacount"):
                    if c in export_df.columns:
                        export_df[c] = export_df[c].apply(to_whole_number_str)
                drop_cols = [c for c in ["id", "fid", "created_at", "geom", "source_files"] if c in export_df.columns]
                if drop_cols:
                    export_df = export_df.drop(columns=drop_cols)
                export_df.to_csv(f, index=False, encoding="utf-8")
                QMessageBox.information(self, "Export Success", f"EA GeoPackage attributes exported to:\n{f}")
            except Exception as e:
                QMessageBox.critical(self, "Export Error", f"Export failed:\n{e}")

    # ----------------------------------------------------------------------
    # EA GeoPackage Feature Selection & Removal Logic
    # ----------------------------------------------------------------------
    def _reset_gpkg_selection(self):
        """Clears all GPKG multi-page and local selections."""
        self.gpkg_select_all_global = False
        self.gpkg_selected_ids.clear()
        self.gpkg_deselected_ids.clear()
        self._update_gpkg_selection_state()

    def _update_gpkg_selection_state(self):
        """Updates the remove button and select all checkbox based on checked rows/global selection state."""
        if not hasattr(self, "btn_gpkg_remove") or not hasattr(self, "table_gpkg"):
            return

        total_matching = getattr(self, "gpkg_total_filtered", 0)
        if self.table_gpkg.columnCount() <= 1 or total_matching == 0:
            self.lbl_gpkg_selected_count.setText("0 selected")
            self.btn_gpkg_remove.setEnabled(False)
            self.btn_gpkg_remove.setText("Remove Selected")
            self.chk_gpkg_select_all.blockSignals(True)
            self.chk_gpkg_select_all.setCheckState(Qt.Unchecked)
            self.chk_gpkg_select_all.setEnabled(False)
            self.chk_gpkg_select_all.blockSignals(False)
            return

        self.chk_gpkg_select_all.setEnabled(True)

        if self.gpkg_select_all_global:
            total_selected = max(0, total_matching - len(self.gpkg_deselected_ids))
        else:
            total_selected = len(self.gpkg_selected_ids)

        if self.gpkg_select_all_global and len(self.gpkg_deselected_ids) == 0:
            self.lbl_gpkg_selected_count.setText(f"{total_selected:,} selected (all pages)")
        elif total_selected > 0:
            self.lbl_gpkg_selected_count.setText(f"{total_selected:,} selected")
        else:
            self.lbl_gpkg_selected_count.setText("0 selected")

        self.btn_gpkg_remove.setEnabled(total_selected > 0)
        self.btn_gpkg_remove.setText(f"Remove Selected ({total_selected:,})" if total_selected > 0 else "Remove Selected")

        self.chk_gpkg_select_all.blockSignals(True)
        if total_matching > 0 and total_selected == total_matching:
            self.chk_gpkg_select_all.setCheckState(Qt.Checked)
        elif total_selected > 0:
            self.chk_gpkg_select_all.setCheckState(Qt.PartiallyChecked)
        else:
            self.chk_gpkg_select_all.setCheckState(Qt.Unchecked)
        self.chk_gpkg_select_all.blockSignals(False)

    def _on_gpkg_table_item_changed(self, item):
        if item.column() == 0:
            if getattr(self, "_is_updating_gpkg_checks", False):
                return
            row_id = item.data(Qt.UserRole)
            if row_id is None:
                return
            norm_id = int(row_id) if str(row_id).isdigit() else row_id
            is_checked = (item.checkState() == Qt.Checked)

            if self.gpkg_select_all_global:
                if not is_checked:
                    self.gpkg_deselected_ids.add(norm_id)
                else:
                    self.gpkg_deselected_ids.discard(norm_id)
            else:
                if is_checked:
                    self.gpkg_selected_ids.add(norm_id)
                else:
                    self.gpkg_selected_ids.discard(norm_id)

                total_matching = getattr(self, "gpkg_total_filtered", 0)
                if total_matching > 0 and len(self.gpkg_selected_ids) == total_matching:
                    self.gpkg_select_all_global = True
                    self.gpkg_selected_ids.clear()
                    self.gpkg_deselected_ids.clear()

            self._update_gpkg_selection_state()

    def _on_gpkg_select_all_toggled(self, state):
        if getattr(self, "_is_updating_gpkg_checks", False):
            return
        self._is_updating_gpkg_checks = True
        try:
            is_checked = (state == Qt.Checked or (state != Qt.Unchecked and not self.gpkg_select_all_global))
            self.gpkg_select_all_global = is_checked
            self.gpkg_selected_ids.clear()
            self.gpkg_deselected_ids.clear()

            self.table_gpkg.blockSignals(True)
            target_state = Qt.Checked if is_checked else Qt.Unchecked
            for r in range(self.table_gpkg.rowCount()):
                item = self.table_gpkg.item(r, 0)
                if item and (item.flags() & Qt.ItemIsUserCheckable):
                    item.setCheckState(target_state)
            self.table_gpkg.blockSignals(False)
        finally:
            self._is_updating_gpkg_checks = False

        self._update_gpkg_selection_state()

    def _on_gpkg_header_clicked(self, logical_index):
        if logical_index == 0:
            current = (self.chk_gpkg_select_all.checkState() == Qt.Checked)
            self.chk_gpkg_select_all.setCheckState(Qt.Unchecked if current else Qt.Checked)

    def _show_gpkg_table_context_menu(self, pos):
        item = self.table_gpkg.itemAt(pos)
        if not item or self.table_gpkg.columnCount() <= 1:
            return
        menu = QMenu(self)
        checked_count = sum(
            1 for r in range(self.table_gpkg.rowCount())
            if self.table_gpkg.item(r, 0) and self.table_gpkg.item(r, 0).checkState() == Qt.Checked
        )
        act_toggle = menu.addAction("Toggle Checkbox for This Row")
        menu.addSeparator()
        if checked_count > 0:
            act_remove = menu.addAction(f"🗑 Remove {checked_count:,} Checked Feature(s)")
        else:
            act_remove = menu.addAction("🗑 Remove This Feature")

        action = menu.exec_(self.table_gpkg.viewport().mapToGlobal(pos))
        if action == act_toggle:
            r = item.row()
            chk = self.table_gpkg.item(r, 0)
            if chk:
                new_state = Qt.Unchecked if chk.checkState() == Qt.Checked else Qt.Checked
                chk.setCheckState(new_state)
        elif action == act_remove:
            if checked_count > 0:
                self._remove_selected_gpkg_records()
            else:
                r = item.row()
                chk = self.table_gpkg.item(r, 0)
                if chk:
                    chk.setCheckState(Qt.Checked)
                self._remove_selected_gpkg_records()

    def _remove_selected_gpkg_records(self):
        """Permanently removes all checked GeoPackage features and cascades removal across POPCEN, EARF, and matching workspaces."""
        checked_ids, checked_geocodes = self._get_selected_gpkg_records()

        if not checked_ids and not checked_geocodes:
            QMessageBox.information(
                self,
                "No Selection",
                "Please check the checkbox at the left side of the GeoPackage record(s) you wish to remove."
            )
            return

        count = max(len(checked_ids), len(checked_geocodes))
        is_db_mode = getattr(self, "cmb_gpkg_source_mode", None) is not None and self.cmb_gpkg_source_mode.currentIndex() == 0
        target_desc = "local SQLite database (`gpkg_records`)" if is_db_mode else "loaded dataset in memory"

        ret = QMessageBox.question(
            self,
            "Confirm Synchronized Record Removal",
            f"Are you sure you want to permanently delete {count:,} selected GeoPackage record(s) from the {target_desc}?\n\n"
            f"Cascading Synchronized Removal:\n"
            f" • Sub-Tab 1: 2024 POPCEN (popcen_database.db)\n"
            f" • Sub-Tab 2: Preliminary EARF (earf_database.db)\n"
            f" • Sub-Tab 3: EA GeoPackages (gpkg_database.db)\n"
            f" • Tab 1: Master Geocode Matching (matched_records)\n\n"
            f"Matching EA geocodes will be permanently deleted across all databases and will no longer be visible in Sub-Tab 2 and Sub-Tab 3.\n\n"
            f"This action cannot be undone. Proceed?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if ret != QMessageBox.Yes:
            return

        try:
            counts = self._sync_remove_geocodes(
                geocodes=checked_geocodes,
                gpkg_ids=checked_ids,
                trigger_source="GPKG"
            )
            self.lbl_gpkg_table_info.setText(
                f"Successfully deleted {count:,} GeoPackage record(s) with synchronized cascade across all databases."
            )
            QMessageBox.information(
                self,
                "Records Removed",
                f"Successfully removed {count:,} GeoPackage record(s).\n\n"
                f"Synchronized database updates:\n"
                f" • POPCEN records deleted: {counts.get('popcen', 0):,}\n"
                f" • EARF records deleted: {counts.get('earf', 0):,}\n"
                f" • GPKG records deleted: {counts.get('gpkg', 0):,}\n"
                f" • Matched records purged: {counts.get('matched', 0):,}"
            )
        except Exception as e:
            QMessageBox.critical(
                self,
                "Removal Error",
                f"Failed to remove GeoPackage records:\n{e}"
            )

    # ----------------------------------------------------------------------
    # Processing Configuration & Execution
    # ----------------------------------------------------------------------
    def _browse_gpkg_files(self):
        init_dir = self._get_initial_gpkg_dir() or DEFAULT_GPKG_ROOT
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select Processed EA GeoPackage Layer(s)", init_dir, "GeoPackage Files (*.gpkg *.sqlite);;All Files (*.*)"
        )
        if files:
            valid_poly = []
            non_poly = []
            for f in files:
                has_poly, _, _ = get_gpkg_polygon_layer_info(f)
                if has_poly:
                    valid_poly.append(f)
                else:
                    non_poly.append(f)

            if non_poly and not valid_poly:
                QMessageBox.warning(
                    self,
                    "Invalid Geometry Type",
                    "The selected GeoPackage file(s) do not contain polygon vector layers (EA boundaries).\n\n"
                    "Only polygon vector layers can be processed."
                )
                return
            elif non_poly:
                QMessageBox.warning(
                    self,
                    "Non-Polygon Layers Excluded",
                    f"{len(non_poly)} file(s) were excluded because they do not contain polygon vector layers.\n\n"
                    f"{len(valid_poly)} polygon file(s) will be loaded."
                )

            p_str = ";".join(valid_poly)
            self.txt_gpkg_layers.setText(p_str)
            self.txt_gpkg_preview_layers.setText(p_str)

    def _detect_project_layers(self):
        layers = [lyr for lyr in QgsProject.instance().mapLayers().values() if isinstance(lyr, QgsVectorLayer) and lyr.isValid() and is_polygon_layer(lyr)]
        if layers:
            p_str = ";".join([lyr.name() for lyr in layers])
            self.txt_gpkg_layers.setText(p_str)
            self.txt_gpkg_preview_layers.setText(p_str)
            self.console_log.append(f"<span style='color:#059669;'>Selected {len(layers)} active polygon vector layers from QGIS project.</span>")
        else:
            QMessageBox.information(
                self,
                "No Polygon Layers",
                "No valid polygon vector layers (EA boundaries) were found in the current QGIS project.\n\n"
                "Only polygon/multipolygon vector layers can be processed."
            )

    def _browse_earf_files(self):
        init_dir = self._get_initial_earf_dir() or DEFAULT_EARF_ROOT
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Preliminary EARF Excel File(s)",
            init_dir,
            "Preliminary EARF Excel (*_earf*.xlsx *_earf*.xls);;All Excel Files (*.xlsx *.xls);;All Files (*.*)",
        )
        if files:
            p_str = ";".join(files)
            self.txt_earf_files.setText(p_str)
            self.txt_earf_preview_files.setText(p_str)

    def _browse_out_gpkg(self):
        f, _ = QFileDialog.getSaveFileName(self, "Save Matched GeoPackage", "", "GeoPackage (*.gpkg)")
        if f:
            if not f.lower().endswith(".gpkg"):
                f += ".gpkg"
            self.txt_out_gpkg.setText(f)

    def _browse_out_csv(self):
        f, _ = QFileDialog.getSaveFileName(self, "Save Matched CSV Mirror", "", "CSV Files (*.csv)")
        if f:
            if not f.lower().endswith(".csv"):
                f += ".csv"
            self.txt_out_csv.setText(f)

    def _open_processing_dialog(self):
        import processing
        processing.execAlgorithmDialog(CoProcessingEarfMatch().name())

    def _start_matching(self):
        popcen_file = self.txt_popcen_file.text().strip() or self.txt_popcen_quick.text().strip()
        popcen_sheet = self.txt_popcen_sheet.text().strip()
        gpkg_layers_str = self.txt_gpkg_layers.text().strip() or self.txt_gpkg_preview_layers.text().strip()
        earf_files_str = self.txt_earf_files.text().strip() or self.txt_earf_preview_files.text().strip()
        earf_sheet = self.txt_earf_sheet.text().strip() or self.txt_earf_preview_sheet.text().strip()
        out_gpkg = self.txt_out_gpkg.text().strip() or "TEMPORARY_OUTPUT"
        out_csv = self.txt_out_csv.text().strip()

        # Check if local database can serve as POPCEN source if no file specified
        if not popcen_file:
            if self.db.is_populated():
                self.console_log.append("<span style='color:#059669;'>Using POPCEN records from Local Database...</span>")
            else:
                QMessageBox.warning(self, "Input Error", "Please provide a POPCEN Excel file or populate the local database first.")
                self.tabs.setCurrentIndex(self.tabs.indexOf(self.post_processing_tab))
                return

        if not gpkg_layers_str:
            QMessageBox.warning(self, "Input Error", "Please select at least one Processed EA GeoPackage layer.")
            self.tabs.setCurrentIndex(self.tabs.indexOf(self.post_processing_tab))
            return

        # Check if local database can serve as EARF source if no file specified
        if not earf_files_str:
            if self.db.is_earf_populated():
                self.console_log.append("<span style='color:#059669;'>Using consolidated Preliminary EARF records from Local Database...</span>")
            else:
                QMessageBox.warning(self, "Input Error", "Please select at least one Preliminary EARF Excel file or consolidate into Local Database.")
                self.tabs.setCurrentIndex(self.tabs.indexOf(self.post_processing_tab))
                return

        params = {
            CoProcessingEarfMatch.INPUT_POPCEN: popcen_file if popcen_file else "",
            CoProcessingEarfMatch.POPCEN_SHEET: popcen_sheet,
            CoProcessingEarfMatch.INPUT_GPKG: gpkg_layers_str,
            CoProcessingEarfMatch.INPUT_EARF: earf_files_str if earf_files_str else "",
            CoProcessingEarfMatch.EARF_SHEET: earf_sheet,
            CoProcessingEarfMatch.OUTPUT: out_gpkg,
            CoProcessingEarfMatch.OUTPUT_CSV: out_csv if out_csv else None,
        }

        if self.btn_run:
            self.btn_run.setEnabled(False)
            self.btn_run.setText("Matching Sources...")
        if self.progress_bar:
            self.progress_bar.setValue(0)
        self.console_log.clear()
        self.console_log.append("<span style='color:#0369a1;'><b>Starting CO Processing Geocode Matching...</b></span>")

        self.worker = ExecutionWorker(params)
        self.worker.progress_signal.connect(lambda p: (self.progress_bar.setValue(p) if self.progress_bar else None))
        self.worker.log_signal.connect(self._handle_log)
        self.worker.finished_signal.connect(self._matching_finished)
        self.worker.start()

    def _handle_log(self, level, msg):
        if level == "error":
            html = f"<span style='color:#dc2626;'>[ERROR] {msg}</span>"
        elif level == "warning":
            html = f"<span style='color:#d97706;'>[WARN] {msg}</span>"
        else:
            html = f"<span style='color:#059669;'>{msg}</span>"
        self.console_log.append(html)

    def _matching_finished(self, results):
        if self.btn_run:
            self.btn_run.setEnabled(True)
            self.btn_run.setText("Run Geocode Matching")
        if self.progress_bar:
            self.progress_bar.setValue(100)

        if not results:
            self.console_log.append("<span style='color:#dc2626;'><b>Matching failed or was canceled.</b></span>")
            return

        out_dest = results.get(CoProcessingEarfMatch.OUTPUT, "")
        self.console_log.append(f"<span style='color:#15803d;'><b>Matching complete! Output: {out_dest}</b></span>")

        # Update local database with match statuses and store into matched_records
        csv_dest = results.get(CoProcessingEarfMatch.OUTPUT_CSV, "")
        if csv_dest and os.path.exists(csv_dest):
            try:
                matched_df = pd.read_csv(csv_dest, dtype=str)
                if "geocode" in matched_df.columns and "match_status" in matched_df.columns:
                    status_dict = dict(zip(matched_df["geocode"], matched_df["match_status"]))
                    self.db.update_match_status(status_dict)
                    self.console_log.append("<span style='color:#059669;'>Updated match statuses in Local Database.</span>")

                # Store all matched records into SQLite matched_records table
                records_to_store = []
                seen_geocodes = set()
                geocode_counts = matched_df["geocode"].value_counts().to_dict() if "geocode" in matched_df.columns else {}

                for _, row in matched_df.iterrows():
                    d = row.to_dict()
                    gc = str(d.get("geocode", "")).strip()
                    is_duplicate = gc in seen_geocodes
                    seen_geocodes.add(gc)

                    st = str(d.get("match_status", "")).strip()

                    in_p = 1 if ("POPCEN" in st or st == "MATCHED_ALL") else 0
                    in_e = 1 if ("EARF" in st or st == "MATCHED_ALL") else 0
                    in_g = 1 if ("GPKG" in st or st == "MATCHED_ALL") else 0

                    an = d.get("popcen_areaname") or d.get("earf_areaname") or d.get("gpkg_name") or d.get("popcen_geographic_name") or ""
                    phh = "" if is_duplicate else str(d.get("popcen_no_of_households", "")).strip()

                    # GeoPackage hh_count and bldg_count are the primary values
                    gpkg_hh = str(d.get("gpkg_hh_count") or d.get("hh_count") or "").strip()
                    gpkg_bldg = str(d.get("gpkg_bldg_count") or d.get("bldg_count") or "").strip()
                    if gpkg_hh.endswith(".0"):
                        gpkg_hh = gpkg_hh[:-2]
                    if gpkg_bldg.endswith(".0"):
                        gpkg_bldg = gpkg_bldg[:-2]

                    ehh = gpkg_hh or str(d.get("earf_household_count") or d.get("earf_number_of_households") or "").strip()

                    earf_hh_2026 = gpkg_hh or str(d.get("earf_household_count") or d.get("household_count") or "").strip()
                    if not earf_hh_2026:
                        for k in ("earf_household count (2026)", "earf_household count", "household count (2026)", "household count", "hh_count", "hhcount"):
                            for rk, rv in d.items():
                                if str(rk).strip().lower() == k and pd.notna(rv):
                                    earf_hh_2026 = str(rv).strip()
                                    break
                            if earf_hh_2026:
                                break
                    if earf_hh_2026.endswith(".0"):
                        earf_hh_2026 = earf_hh_2026[:-2]
                    if earf_hh_2026.lower() in ("nan", "none", "null"):
                        earf_hh_2026 = ""

                    earf_bldg_2026 = gpkg_bldg or str(d.get("earf_building_count") or d.get("building_count") or "").strip()
                    if not earf_bldg_2026:
                        for k in ("earf_building count (2026)", "earf_building count", "building count (2026)", "building count", "bldg_count", "bldgcount"):
                            for rk, rv in d.items():
                                if str(rk).strip().lower() == k and pd.notna(rv):
                                    earf_bldg_2026 = str(rv).strip()
                                    break
                            if earf_bldg_2026:
                                break
                    if earf_bldg_2026.endswith(".0"):
                        earf_bldg_2026 = earf_bldg_2026[:-2]
                    if earf_bldg_2026.lower() in ("nan", "none", "null"):
                        earf_bldg_2026 = ""

                    diff_str = ""
                    try:
                        p_val = float(phh) if phh and phh not in ("-", "nan", "None") else None
                        e_val = float(ehh) if ehh and ehh not in ("-", "nan", "None") else None
                        if p_val is not None and e_val is not None:
                            diff = int(round(e_val - p_val))
                            diff_str = f"{diff:+d}" if diff != 0 else "0"
                    except Exception:
                        diff_str = ""

                    old_ean = d.get("popcen_ean") or d.get("earf_ea") or d.get("gpkg_ean") or ""
                    new_ean = d.get("earf_new_enumeration_area_code") or d.get("gpkg_new_ean") or d.get("gpkg_code") or ""
                    old_ean = str(old_ean).strip().replace(".0", "")
                    new_ean = str(new_ean).strip().replace(".0", "")
                    is_reset = 1 if (new_ean and old_ean and new_ean != old_ean) else 0

                    ea_t = str(d.get("earf_ea_type") or d.get("gpkg_ea_type") or "").strip()
                    rem = str(d.get("earf_remarks") or d.get("gpkg_remarks") or d.get("popcen_remarks") or "").strip()

                    has_split = geocode_counts.get(gc, 0) > 1 or is_duplicate or "split" in ea_t.lower() or "split" in rem.lower()
                    if has_split:
                        lifecycle = "SPLIT"
                    elif "merg" in ea_t.lower() or "merg" in rem.lower():
                        lifecycle = "MERGED"
                    elif is_reset == 1 or "delineat" in ea_t.lower():
                        lifecycle = "RESET"
                    elif "retain" in ea_t.lower():
                        lifecycle = "RETAINED"
                    elif in_p and not in_e and not in_g:
                        lifecycle = "BASELINE"
                    else:
                        lifecycle = "RETAINED" if not is_reset else "RESET"

                    has_geom = 1 if in_g else 0

                    rec = {
                        "geocode": gc,
                        "match_status": st,
                        "in_popcen": in_p,
                        "in_earf": in_e,
                        "in_gpkg": in_g,
                        "areaname": an,
                        "popcen_households": phh,
                        "earf_households": ehh,
                        "household_diff": diff_str,
                        "earf_household_count": earf_hh_2026,
                        "earf_building_count": earf_bldg_2026,
                        "popcen_delineation": "" if is_duplicate else str(d.get("popcen_ea_subject_for_delineation", "")),
                        "popcen_merging": "" if is_duplicate else str(d.get("popcen_ea_subject_for_merging", "")),
                        "popcen_gap_overlap": "" if is_duplicate else str(d.get("popcen_ea_with_gap_overlap", "")),
                        "ean": old_ean,
                        "new_ean": new_ean,
                        "new_enumeration_area_code": new_ean,
                        "is_reset": is_reset,
                        "ea_lifecycle": lifecycle,
                        "ea_type": "",
                        "remarks": rem,
                        "gpkg_layers": str(d.get("gpkg_layer_source", "")),
                        "has_geometry": has_geom,
                        "geom_type": "Polygon" if has_geom else "No Geometry",
                        "gpkg_hh_count": gpkg_hh,
                        "gpkg_bldg_count": gpkg_bldg,
                    }
                    records_to_store.append(rec)

                if records_to_store:
                    if hasattr(self.db, "compute_recoded_enumeration_areas"):
                        self.db.compute_recoded_enumeration_areas(records_to_store)
                    elif "compute_recoded_enumeration_areas" in globals():
                        compute_recoded_enumeration_areas(records_to_store)
                    self.db.store_matched_records(records_to_store, replace=True)

                self._check_and_load_initial_data()
                self._check_and_load_matching_data()
                self.tabs.setCurrentIndex(self.tabs.indexOf(self.matching_tab))
            except Exception as e:
                self.console_log.append(f"<span style='color:#ea580c;'>Status update note: {e}</span>")

        QMessageBox.information(self, "Matching Finished", f"EARF Geocode Matching finished successfully!\nOutput: {out_dest}")

    # ----------------------------------------------------------------------
    # Tab 3: Deliverables Monitoring (Output GPKG, Final EARF, MIS EARF)
    # ----------------------------------------------------------------------
    def _setup_monitoring_tab(self):
        """Sets up Tab 3: Output Deliverables Monitoring (Output GPKG, Final EARF, MIS EARF)."""
        layout = QVBoxLayout(self.monitoring_tab)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # 1. Top Controls Bar: Root Directory & Actions
        top_bar = QHBoxLayout()
        top_bar.addWidget(QLabel("<b>Root Directory:</b>"))

        self.txt_mon_root_dir = QLineEdit()
        self.txt_mon_root_dir.setPlaceholderText("Select root directory for final deliverables (Region/Province/CityMun)...")
        saved_root = self.db.get_saved_monitoring_root_dir()
        if saved_root:
            root_default = saved_root
        elif os.path.exists(DEFAULT_FINAL_EXPORT_ROOT):
            root_default = DEFAULT_FINAL_EXPORT_ROOT
        else:
            root_default = getattr(self, "_final_export_root_dir", None) or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
        self.txt_mon_root_dir.setText(root_default)
        top_bar.addWidget(self.txt_mon_root_dir, 1)

        self.btn_mon_browse = QPushButton("Browse...")
        self.btn_mon_browse.setObjectName("secondaryBtn")
        self.btn_mon_browse.setToolTip("Browse root folder containing final export deliverables")
        self.btn_mon_browse.clicked.connect(self._browse_monitoring_root_dir)
        top_bar.addWidget(self.btn_mon_browse)

        self.btn_mon_open_root = QPushButton("Open Folder")
        self.btn_mon_open_root.setObjectName("secondaryBtn")
        self.btn_mon_open_root.setToolTip("Open root export folder in Windows File Explorer")
        self.btn_mon_open_root.clicked.connect(self._open_monitoring_root_dir)
        top_bar.addWidget(self.btn_mon_open_root)

        self.btn_mon_scan = QPushButton("Scan / Refresh")
        self.btn_mon_scan.setObjectName("primaryBtn")
        self.btn_mon_scan.setToolTip("Scan target folder and inspect Output GPKGs, Final EARFs, and MIS EARFs")
        self.btn_mon_scan.clicked.connect(self._start_monitoring_scan)
        top_bar.addWidget(self.btn_mon_scan)

        self.btn_mon_stop = QPushButton("Stop")
        self.btn_mon_stop.setObjectName("stopBtn")
        self.btn_mon_stop.setToolTip("Stop active deliverable scan immediately")
        self.btn_mon_stop.setEnabled(False)
        self.btn_mon_stop.clicked.connect(self._stop_monitoring_scan)
        top_bar.addWidget(self.btn_mon_stop)

        self.btn_mon_export_report = QPushButton("Export Report...")
        self.btn_mon_export_report.setObjectName("secondaryBtn")
        self.btn_mon_export_report.setToolTip("Export complete monitoring verification audit report to CSV")
        self.btn_mon_export_report.clicked.connect(self._export_monitoring_report)
        top_bar.addWidget(self.btn_mon_export_report)

        layout.addLayout(top_bar)

        # 2. KPI Summary Cards Frame (6 Cards)
        kpi_frame = QFrame()
        kpi_frame.setObjectName("kpiFrame")
        kpi_layout = QHBoxLayout(kpi_frame)
        kpi_layout.setContentsMargins(8, 6, 8, 6)
        kpi_layout.setSpacing(8)

        self.kpi_mon_total = self._create_kpi_card("Total Cities/Municipalities", "1,655", "#0284c7")
        self.kpi_mon_total.setToolTip("Fixed Benchmark: 1,655 Official Philippine Cities & Municipalities (POPCEN)")
        self.kpi_mon_complete = self._create_kpi_card("Fully Complete (3/3)", "0", "#16a34a")
        self.kpi_mon_percent = self._create_kpi_card("Percent Completed", "0.0%", "#0d9488")
        self.kpi_mon_percent.setToolTip("Percent Completed: Total Fully Complete (3/3) / 1,655 Cities/Municipalities")
        self.kpi_mon_gpkg = self._create_kpi_card("Output GPKG Ready", "0", "#0ea5e9")
        self.kpi_mon_final_earf = self._create_kpi_card("Final EARF Ready", "0", "#059669")
        self.kpi_mon_mis_earf = self._create_kpi_card("MIS EARF Ready", "0", "#7c3aed")
        self.kpi_mon_incomplete = self._create_kpi_card("Incomplete / Pending", "1,655", "#e11d48")

        for k in (
            self.kpi_mon_total,
            self.kpi_mon_complete,
            self.kpi_mon_percent,
            self.kpi_mon_gpkg,
            self.kpi_mon_final_earf,
            self.kpi_mon_mis_earf,
            self.kpi_mon_incomplete,
        ):
            kpi_layout.addWidget(k)

        layout.addWidget(kpi_frame)

        # 3. Filter & Search Controls Bar
        filter_bar = QHBoxLayout()
        filter_bar.addWidget(QLabel("Search:"))
        self.search_mon = QLineEdit()
        self.search_mon.setPlaceholderText("Search Municipality, Code, Province, Region, Filename...")
        self.search_mon.textChanged.connect(self._on_mon_search_changed)
        filter_bar.addWidget(self.search_mon, 1)

        filter_bar.addWidget(QLabel("Scope:"))
        self.cmb_mon_scope = QComboBox()
        self.cmb_mon_scope.addItems([
            "Root Directory Subfolders (Default)",
            "Discovered on Disk",
            "Uploaded Municipalities (Batch)",
            "All in Database",
            "Processed in Matching",
        ])
        self.cmb_mon_scope.currentIndexChanged.connect(self._on_mon_scope_changed)
        filter_bar.addWidget(self.cmb_mon_scope)

        filter_bar.addWidget(QLabel("Region:"))
        self.cmb_mon_region = QComboBox()
        self.cmb_mon_region.addItem("All Regions", "")
        self.cmb_mon_region.currentIndexChanged.connect(self._on_mon_region_changed)
        filter_bar.addWidget(self.cmb_mon_region)

        filter_bar.addWidget(QLabel("Province:"))
        self.cmb_mon_province = QComboBox()
        self.cmb_mon_province.addItem("All Provinces", "")
        self.cmb_mon_province.currentIndexChanged.connect(self._on_mon_filter_changed)
        filter_bar.addWidget(self.cmb_mon_province)

        filter_bar.addWidget(QLabel("Status:"))
        self.cmb_mon_status = QComboBox()
        self.cmb_mon_status.addItems([
            "All Statuses",
            "Complete (3/3)",
            "Partial (1-2/3)",
            "Missing All (0/3)",
            "Missing GPKG",
            "Missing Final EARF",
            "Missing MIS EARF",
        ])
        self.cmb_mon_status.currentIndexChanged.connect(self._on_mon_filter_changed)
        filter_bar.addWidget(self.cmb_mon_status)

        filter_bar.addWidget(QLabel("Page Size:"))
        self.cmb_mon_page_size = QComboBox()
        self.cmb_mon_page_size.addItems(["50", "100", "250", "500", "1000", "All"])
        self.cmb_mon_page_size.setCurrentText("All")
        self.cmb_mon_page_size.currentIndexChanged.connect(self._on_mon_page_size_changed)
        filter_bar.addWidget(self.cmb_mon_page_size)

        self.btn_mon_prev = QPushButton("Prev")
        self.btn_mon_prev.clicked.connect(self._mon_prev_page)
        filter_bar.addWidget(self.btn_mon_prev)

        self.lbl_mon_page_info = QLabel("Page 1 of 1 (0 records)")
        self.lbl_mon_page_info.setObjectName("pageInfo")
        filter_bar.addWidget(self.lbl_mon_page_info)

        self.btn_mon_next = QPushButton("Next")
        self.btn_mon_next.clicked.connect(self._mon_next_page)
        filter_bar.addWidget(self.btn_mon_next)

        layout.addLayout(filter_bar)

        # 4. Monitoring Table
        self.table_monitoring = QTableWidget()
        self.table_monitoring.setObjectName("monitoringTable")
        self.table_monitoring.setAlternatingRowColors(True)
        self.table_monitoring.setSelectionBehavior(QTableWidget.SelectRows)
        self.table_monitoring.setSelectionMode(QTableWidget.SingleSelection)
        self.table_monitoring.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table_monitoring.verticalHeader().setVisible(False)
        self.table_monitoring.verticalHeader().setDefaultSectionSize(28)
        self.table_monitoring.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table_monitoring.customContextMenuRequested.connect(self._show_monitoring_context_menu)
        self.table_monitoring.itemSelectionChanged.connect(self._on_monitoring_row_selected)
        self.table_monitoring.cellDoubleClicked.connect(self._on_monitoring_row_double_clicked)

        headers = [
            "#",
            "Region",
            "Province",
            "City / Municipality",
            "Overall Status",
            "Output GPKG",
            "Final EARF",
            "MIS EARF",
            "Last Modified",
            "Subfolder Location",
        ]
        self.table_monitoring.setColumnCount(len(headers))
        self.table_monitoring.setHorizontalHeaderLabels(headers)
        h = self.table_monitoring.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.Interactive)
        h.setSectionResizeMode(2, QHeaderView.Interactive)
        h.setSectionResizeMode(3, QHeaderView.Interactive)
        h.setSectionResizeMode(4, QHeaderView.Interactive)
        h.setSectionResizeMode(5, QHeaderView.Interactive)
        h.setSectionResizeMode(6, QHeaderView.Interactive)
        h.setSectionResizeMode(7, QHeaderView.Interactive)
        h.setSectionResizeMode(8, QHeaderView.Interactive)
        h.setSectionResizeMode(9, QHeaderView.Stretch)
        self.table_monitoring.setColumnWidth(1, 140)
        self.table_monitoring.setColumnWidth(2, 110)
        self.table_monitoring.setColumnWidth(3, 170)
        self.table_monitoring.setColumnWidth(4, 125)
        self.table_monitoring.setColumnWidth(5, 145)
        self.table_monitoring.setColumnWidth(6, 145)
        self.table_monitoring.setColumnWidth(7, 145)
        self.table_monitoring.setColumnWidth(8, 125)

        layout.addWidget(self.table_monitoring, 1)

        # 5. Detail & Quick Action Panel (Bottom Frame)
        self.mon_detail_frame = QFrame()
        self.mon_detail_frame.setObjectName("kpiFrame")
        detail_layout = QVBoxLayout(self.mon_detail_frame)
        detail_layout.setContentsMargins(10, 8, 10, 8)
        detail_layout.setSpacing(6)

        # Title & Subfolder Path Header
        det_head = QHBoxLayout()
        self.lbl_mon_detail_title = QLabel("<b>Selected Municipality:</b> None (Select any row above to inspect deliverables)")
        self.lbl_mon_detail_title.setFont(QFont("Segoe UI", 10))
        det_head.addWidget(self.lbl_mon_detail_title)
        det_head.addStretch()

        self.btn_mon_copy_folder = QPushButton("Copy Folder Path")
        self.btn_mon_copy_folder.setObjectName("secondaryBtn")
        self.btn_mon_copy_folder.setEnabled(False)
        self.btn_mon_copy_folder.clicked.connect(self._copy_selected_folder_path)
        det_head.addWidget(self.btn_mon_copy_folder)

        self.btn_mon_open_folder = QPushButton("Open Folder in Explorer")
        self.btn_mon_open_folder.setObjectName("secondaryBtn")
        self.btn_mon_open_folder.setEnabled(False)
        self.btn_mon_open_folder.clicked.connect(self._open_selected_folder)
        det_head.addWidget(self.btn_mon_open_folder)

        self.btn_mon_export_now = QPushButton("Export Deliverables Now")
        self.btn_mon_export_now.setObjectName("primaryBtn")
        self.btn_mon_export_now.setEnabled(False)
        self.btn_mon_export_now.setToolTip("Export final deliverables for the selected municipality")
        self.btn_mon_export_now.clicked.connect(self._export_deliverables_for_selected_mun)
        det_head.addWidget(self.btn_mon_export_now)

        self.btn_mon_review_qa = QPushButton("Audit & Review Outputs (QA)")
        self.btn_mon_review_qa.setObjectName("primaryBtn")
        self.btn_mon_review_qa.setStyleSheet("background-color: #4f46e5; color: white; font-weight: bold;")
        self.btn_mon_review_qa.setEnabled(False)
        self.btn_mon_review_qa.setToolTip("Run comprehensive 25-rule QA review on deliverables for this municipality")
        self.btn_mon_review_qa.clicked.connect(self._open_qa_review_for_selected_mun)
        det_head.addWidget(self.btn_mon_review_qa)

        detail_layout.addLayout(det_head)

        # 3 Deliverable Status Detail Cards
        cards_layout = QHBoxLayout()
        cards_layout.setSpacing(8)

        # GPKG Block
        self.card_det_gpkg = QFrame()
        self.card_det_gpkg.setObjectName("kpiCard")
        cg_layout = QVBoxLayout(self.card_det_gpkg)
        cg_layout.setContentsMargins(8, 6, 8, 6)
        cg_layout.setSpacing(3)
        cg_title = QLabel("1. Output GeoPackage (.gpkg)")
        cg_title.setFont(QFont("Segoe UI", 8, QFont.Bold))
        cg_title.setStyleSheet("color: #0369a1;")
        self.lbl_det_gpkg_status = QLabel("Status: -")
        self.lbl_det_gpkg_info = QLabel("File: -")
        self.lbl_det_gpkg_info.setWordWrap(True)
        self.btn_mon_load_gpkg = QPushButton("Load GPKG to Map")
        self.btn_mon_load_gpkg.setObjectName("actionBtn")
        self.btn_mon_load_gpkg.setEnabled(False)
        self.btn_mon_load_gpkg.clicked.connect(self._load_selected_gpkg_to_map)
        cg_layout.addWidget(cg_title)
        cg_layout.addWidget(self.lbl_det_gpkg_status)
        cg_layout.addWidget(self.lbl_det_gpkg_info)
        cg_layout.addWidget(self.btn_mon_load_gpkg)
        cards_layout.addWidget(self.card_det_gpkg, 1)

        # Final EARF Block
        self.card_det_earf = QFrame()
        self.card_det_earf.setObjectName("kpiCard")
        ce_layout = QVBoxLayout(self.card_det_earf)
        ce_layout.setContentsMargins(8, 6, 8, 6)
        ce_layout.setSpacing(3)
        ce_title = QLabel("2. Final EARF Excel (.xlsx)")
        ce_title.setFont(QFont("Segoe UI", 8, QFont.Bold))
        ce_title.setStyleSheet("color: #047857;")
        self.lbl_det_earf_status = QLabel("Status: -")
        self.lbl_det_earf_info = QLabel("File: -")
        self.lbl_det_earf_info.setWordWrap(True)
        self.btn_mon_open_earf = QPushButton("Open Final EARF")
        self.btn_mon_open_earf.setObjectName("secondaryBtn")
        self.btn_mon_open_earf.setEnabled(False)
        self.btn_mon_open_earf.clicked.connect(self._open_selected_final_earf)
        ce_layout.addWidget(ce_title)
        ce_layout.addWidget(self.lbl_det_earf_status)
        ce_layout.addWidget(self.lbl_det_earf_info)
        ce_layout.addWidget(self.btn_mon_open_earf)
        cards_layout.addWidget(self.card_det_earf, 1)

        # MIS EARF Block
        self.card_det_mis = QFrame()
        self.card_det_mis.setObjectName("kpiCard")
        cm_layout = QVBoxLayout(self.card_det_mis)
        cm_layout.setContentsMargins(8, 6, 8, 6)
        cm_layout.setSpacing(3)
        cm_title = QLabel("3. MIS EARF Excel (.xlsx)")
        cm_title.setFont(QFont("Segoe UI", 8, QFont.Bold))
        cm_title.setStyleSheet("color: #6d28d9;")
        self.lbl_det_mis_status = QLabel("Status: -")
        self.lbl_det_mis_info = QLabel("File: -")
        self.lbl_det_mis_info.setWordWrap(True)
        self.btn_mon_open_mis = QPushButton("Open MIS EARF")
        self.btn_mon_open_mis.setObjectName("secondaryBtn")
        self.btn_mon_open_mis.setEnabled(False)
        self.btn_mon_open_mis.clicked.connect(self._open_selected_mis_earf)
        cm_layout.addWidget(cm_title)
        cm_layout.addWidget(self.lbl_det_mis_status)
        cm_layout.addWidget(self.lbl_det_mis_info)
        cm_layout.addWidget(self.btn_mon_open_mis)
        cards_layout.addWidget(self.card_det_mis, 1)

        detail_layout.addLayout(cards_layout)
        layout.addWidget(self.mon_detail_frame)

    def _init_monitoring_regions_provinces(self):
        """Populates region and province dropdowns for monitoring filter."""
        try:
            self.cmb_mon_region.blockSignals(True)
            self.cmb_mon_region.clear()
            self.cmb_mon_region.addItem("All Regions", "")
            regions = self.db.get_distinct_regions()
            for r in regions:
                self.cmb_mon_region.addItem(r["display"], r["code"])
            self.cmb_mon_region.blockSignals(False)

            self._update_monitoring_provinces()
        except Exception:
            pass

    def _update_monitoring_provinces(self):
        """Updates province dropdown based on currently selected region."""
        try:
            reg_code = self.cmb_mon_region.currentData()
            self.cmb_mon_province.blockSignals(True)
            self.cmb_mon_province.clear()
            self.cmb_mon_province.addItem("All Provinces", "")
            provinces = self.db.get_distinct_provinces(reg_code if reg_code else None)
            for p in provinces:
                self.cmb_mon_province.addItem(p["display"], p["code"])
            self.cmb_mon_province.blockSignals(False)
        except Exception:
            pass

    def _on_mon_region_changed(self):
        self._update_monitoring_provinces()
        self._apply_monitoring_filters()

    def _on_mon_filter_changed(self):
        self._apply_monitoring_filters()

    def _on_mon_scope_changed(self):
        self._start_monitoring_scan()

    def _on_mon_search_changed(self):
        self._apply_monitoring_filters()

    def _on_mon_page_size_changed(self):
        txt = self.cmb_mon_page_size.currentText()
        self.mon_page_size = 0 if txt == "All" else int(txt)
        self.mon_page = 0
        self._fetch_and_render_mon_page()

    def _mon_prev_page(self):
        if self.mon_page > 0:
            self.mon_page -= 1
            self._fetch_and_render_mon_page()

    def _mon_next_page(self):
        max_pages = (len(self.mon_filtered_records) - 1) // self.mon_page_size if self.mon_page_size > 0 else 0
        if self.mon_page < max_pages:
            self.mon_page += 1
            self._fetch_and_render_mon_page()

    def _browse_monitoring_root_dir(self):
        curr_dir = self.txt_mon_root_dir.text().strip()
        if not curr_dir or not os.path.exists(curr_dir):
            curr_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Root Directory for Final Deliverables Monitoring",
            curr_dir
        )
        if folder:
            self.txt_mon_root_dir.setText(folder)
            self._start_monitoring_scan()

    def _open_monitoring_root_dir(self):
        d = self.txt_mon_root_dir.text().strip()
        if d and os.path.exists(d):
            try:
                os.startfile(d)
            except Exception as e:
                QMessageBox.warning(self, "Open Folder Failed", f"Could not open directory: {e}")
        else:
            QMessageBox.warning(self, "Directory Missing", f"Directory does not exist on disk:\n{d}")

    def _start_monitoring_scan(self):
        """Starts asynchronous background scan of final deliverables under root directory."""
        root_dir = self.txt_mon_root_dir.text().strip()
        if not root_dir:
            saved_root = self.db.get_saved_monitoring_root_dir()
            if saved_root:
                root_dir = saved_root
                self.txt_mon_root_dir.setText(root_dir)
            else:
                root_default = DEFAULT_FINAL_EXPORT_ROOT if os.path.exists(DEFAULT_FINAL_EXPORT_ROOT) else os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
                root_dir = root_default
                self.txt_mon_root_dir.setText(root_dir)

        if not os.path.exists(root_dir):
            # Directory does not exist on disk (e.g. disconnected network drive).
            # Do not overwrite preserved cached records with a blank scan.
            return

        scope_text = self.cmb_mon_scope.currentText()
        scope = "subfolders"
        if "All in Database" in scope_text:
            scope = "all"
        elif "Discovered on Disk" in scope_text or "Root Directory Subfolders" in scope_text:
            scope = "subfolders"
        elif "Uploaded" in scope_text:
            scope = "uploaded"
        elif "Processed in Matching" in scope_text:
            scope = "processed"

        if self._is_scanning_monitoring:
            return

        self._is_scanning_monitoring = True
        self.btn_mon_scan.setEnabled(False)
        self.btn_mon_scan.setText("Scanning...")
        if hasattr(self, "btn_mon_stop"):
            self.btn_mon_stop.setEnabled(True)

        self.monitoring_scan_worker = MonitoringScanWorker(
            db=self.db,
            root_dir=root_dir,
            scope=scope,
        )
        self.monitoring_scan_worker.progress_signal.connect(self._on_monitoring_scan_progress)
        self.monitoring_scan_worker.finished_signal.connect(self._on_monitoring_scan_finished)
        self.monitoring_scan_worker.start()

    def _stop_monitoring_scan(self):
        """Immediately stops the running deliverables monitoring scan."""
        if hasattr(self, "monitoring_scan_worker") and self.monitoring_scan_worker and self.monitoring_scan_worker.isRunning():
            self.monitoring_scan_worker.cancel()
        self._is_scanning_monitoring = False
        if hasattr(self, "btn_mon_stop"):
            self.btn_mon_stop.setEnabled(False)
        self.btn_mon_scan.setEnabled(True)
        self.btn_mon_scan.setText("Scan / Refresh")

    def _on_monitoring_scan_progress(self, pct, msg):
        self.btn_mon_scan.setText(f"Scanning {pct}%...")

    def _on_monitoring_scan_finished(self, summary, err_msg):
        self._is_scanning_monitoring = False
        self.btn_mon_scan.setEnabled(True)
        self.btn_mon_scan.setText("Scan / Refresh")
        if hasattr(self, "btn_mon_stop"):
            self.btn_mon_stop.setEnabled(False)

        if err_msg and err_msg != "Cancelled":
            QMessageBox.warning(self, "Scan Error", f"Encountered an issue during monitoring scan: {err_msg}")
            return

        self.mon_records = summary.get("records", [])
        self._apply_monitoring_filters()

    def _apply_monitoring_filters(self):
        """Filters self.mon_records by region, province, status, and search query."""
        if not self.mon_records:
            self.mon_filtered_records = []
            self._update_monitoring_kpi_cards([])
            self._fetch_and_render_mon_page()
            return

        reg_code = self.cmb_mon_region.currentData()
        reg_text = self.cmb_mon_region.currentText()
        prov_code = self.cmb_mon_province.currentData()
        prov_text = self.cmb_mon_province.currentText()
        status_filter = self.cmb_mon_status.currentText()
        search_query = self.search_mon.text().strip().lower()

        filtered = []
        for r in self.mon_records:
            # Region filter
            if reg_text and reg_text != "All Regions":
                r_reg = (r.get("region") or "").lower()
                if reg_code and reg_code.lower() not in r_reg and reg_text.lower() not in r_reg:
                    continue
                elif not reg_code and reg_text.lower() not in r_reg:
                    continue

            # Province filter
            if prov_text and prov_text != "All Provinces":
                r_prov = (r.get("province") or "").lower()
                if prov_code and prov_code.lower() not in r_prov and prov_text.lower() not in r_prov:
                    continue
                elif not prov_code and prov_text.lower() not in r_prov:
                    continue

            # Status filter
            if status_filter and status_filter != "All Statuses":
                st = r.get("status")
                if status_filter == "Complete (3/3)" and st != "COMPLETE":
                    continue
                elif status_filter == "Partial (1-2/3)" and st != "PARTIAL":
                    continue
                elif status_filter == "Missing All (0/3)" and st != "MISSING":
                    continue
                elif status_filter == "Missing GPKG" and r["gpkg"]["exists"]:
                    continue
                elif status_filter == "Missing Final EARF" and r["final_earf"]["exists"]:
                    continue
                elif status_filter == "Missing MIS EARF" and r["mis_earf"]["exists"]:
                    continue

            # Search query
            if search_query:
                haystack = f"{r.get('code', '')} {r.get('pppmm', '')} {r.get('name', '')} {r.get('province', '')} {r.get('region', '')} {r['gpkg']['filename']} {r['final_earf']['filename']} {r['mis_earf']['filename']}".lower()
                if search_query not in haystack:
                    continue

            filtered.append(r)

        self.mon_filtered_records = filtered
        self._update_monitoring_kpi_cards(filtered)
        self.mon_page = 0
        self._fetch_and_render_mon_page()

    def _update_monitoring_kpi_cards(self, records):
        """Updates the 7 KPI cards on the monitoring tab with fixed 1,655 benchmark."""
        FIXED_TOTAL_CITIES_MUN = 1655
        tot = len(records)
        comp = sum(1 for r in records if r.get("status") == "COMPLETE")
        part = sum(1 for r in records if r.get("status") == "PARTIAL")
        miss = sum(1 for r in records if r.get("status") == "MISSING")
        gpkg_cnt = sum(1 for r in records if r.get("gpkg", {}).get("exists"))
        earf_cnt = sum(1 for r in records if r.get("final_earf", {}).get("exists"))
        mis_cnt = sum(1 for r in records if r.get("mis_earf", {}).get("exists"))

        # The total count of cities/municipalities is fixed at 1,655 nationwide
        self.kpi_mon_total.val_label.setText("1,655")

        # Percent Completed = Total Fully Complete (3/3) / 1,655 Cities/Municipalities * 100%
        pct = (comp / FIXED_TOTAL_CITIES_MUN * 100.0)
        self.kpi_mon_percent.val_label.setText(f"{pct:.1f}%")

        self.kpi_mon_complete.val_label.setText(f"{comp:,}")
        self.kpi_mon_gpkg.val_label.setText(f"{gpkg_cnt:,}")
        self.kpi_mon_final_earf.val_label.setText(f"{earf_cnt:,}")
        self.kpi_mon_mis_earf.val_label.setText(f"{mis_cnt:,}")

        # Incomplete / Pending
        incomp = FIXED_TOTAL_CITIES_MUN - comp if (not self.mon_records or tot >= len(self.mon_records)) else (tot - comp)
        self.kpi_mon_incomplete.val_label.setText(f"{incomp:,}")

        is_filtered = bool(self.mon_records and tot < len(self.mon_records))
        if is_filtered:
            filt_pct = (comp / tot * 100.0) if tot > 0 else 0.0
            self.kpi_mon_total.setToolTip(f"Fixed Benchmark: 1,655 Cities & Municipalities\nFiltered Scope: Showing {tot:,} records")
            self.kpi_mon_percent.setToolTip(f"Nationwide Completion: {comp:,} of 1,655 ({pct:.2f}%)\nFiltered Completion: {comp:,} of {tot:,} ({filt_pct:.1f}%)")
        else:
            self.kpi_mon_total.setToolTip("Fixed Benchmark: 1,655 Official Philippine Cities & Municipalities (POPCEN)")
            self.kpi_mon_percent.setToolTip(f"Nationwide Completion: {comp:,} of 1,655 Cities & Municipalities ({pct:.2f}%)")

    def _fetch_and_render_mon_page(self):
        """Renders the current page of filtered monitoring records into table_monitoring."""
        total = len(self.mon_filtered_records)
        if total == 0:
            self.table_monitoring.setRowCount(0)
            self.lbl_mon_page_info.setText("Page 1 of 1 (0 records)")
            self.btn_mon_prev.setEnabled(False)
            self.btn_mon_next.setEnabled(False)
            self._update_monitoring_detail_panel(None)
            return

        page_size = self.mon_page_size if self.mon_page_size > 0 else total
        max_page = (total - 1) // page_size if page_size > 0 else 0
        self.mon_page = max(0, min(self.mon_page, max_page))

        start_idx = self.mon_page * page_size
        end_idx = min(start_idx + page_size, total)
        slice_records = self.mon_filtered_records[start_idx:end_idx]

        FIXED_TOTAL_CITIES_MUN = 1655
        if total >= FIXED_TOTAL_CITIES_MUN or (self.mon_records and total >= len(self.mon_records)):
            self.lbl_mon_page_info.setText(f"Page {self.mon_page + 1} of {max_page + 1} (1,655 cities/municipalities)")
        else:
            self.lbl_mon_page_info.setText(f"Page {self.mon_page + 1} of {max_page + 1} ({total:,} filtered of 1,655 cities/municipalities)")

        self.btn_mon_prev.setEnabled(self.mon_page > 0)
        self.btn_mon_next.setEnabled(self.mon_page < max_page)

        self._populate_monitoring_table(slice_records, start_idx)

    def _populate_monitoring_table(self, records, offset):
        """Populates the QTableWidget with deliverable statuses and styled badges."""
        self.table_monitoring.setRowCount(len(records))

        for row_idx, r in enumerate(records):
            # Col 0: Index
            idx_item = QTableWidgetItem(f"{offset + row_idx + 1:,}")
            idx_item.setTextAlignment(Qt.AlignCenter)
            self.table_monitoring.setItem(row_idx, 0, idx_item)

            # Col 1: Region
            reg_item = QTableWidgetItem(r.get("region", "") or "-")
            self.table_monitoring.setItem(row_idx, 1, reg_item)

            # Col 2: Province
            prov_item = QTableWidgetItem(r.get("province", "") or "-")
            self.table_monitoring.setItem(row_idx, 2, prov_item)

            # Col 3: City / Municipality
            mun_str = f"{r.get('code', '')} - {r.get('name', '')}" if r.get('code') else r.get('name', '')
            mun_item = QTableWidgetItem(mun_str)
            mun_font = QFont()
            mun_font.setBold(True)
            mun_item.setFont(mun_font)
            self.table_monitoring.setItem(row_idx, 3, mun_item)

            # Col 4: Overall Status Badge
            st = r.get("status", "MISSING")
            ratio = r.get("completion_ratio", "0/3")
            status_item = QTableWidgetItem(f"{ratio} {st}")
            status_item.setTextAlignment(Qt.AlignCenter)
            status_font = QFont()
            status_font.setBold(True)
            status_item.setFont(status_font)
            if st == "COMPLETE":
                status_item.setForeground(QColor("#15803d"))
                status_item.setBackground(QColor("#dcfce7"))
            elif st == "PARTIAL":
                status_item.setForeground(QColor("#b45309"))
                status_item.setBackground(QColor("#fef3c7"))
            else:
                status_item.setForeground(QColor("#b91c1c"))
                status_item.setBackground(QColor("#fee2e2"))
            self.table_monitoring.setItem(row_idx, 4, status_item)

            # Col 5: Output GPKG
            gpkg = r.get("gpkg", {})
            if gpkg.get("exists"):
                gpkg_item = QTableWidgetItem(f"READY ({gpkg.get('size_str', '')})")
                gpkg_item.setTextAlignment(Qt.AlignCenter)
                gpkg_item.setForeground(QColor("#0369a1"))
                gpkg_item.setBackground(QColor("#e0f2fe"))
                fc = gpkg.get('feature_count')
                fc_fmt = f"{fc:,}" if isinstance(fc, (int, float)) else str(fc or 0)
                gpkg_item.setToolTip(f"File: {gpkg.get('filename')}\nPath: {gpkg.get('path')}\nSize: {gpkg.get('size_str')}\nFeatures: {fc_fmt}\nModified: {gpkg.get('mtime_str')}")
            else:
                gpkg_item = QTableWidgetItem("MISSING")
                gpkg_item.setTextAlignment(Qt.AlignCenter)
                gpkg_item.setForeground(QColor("#94a3b8"))
                gpkg_item.setBackground(QColor("#f8fafc"))
                gpkg_item.setToolTip(f"Expected: {r.get('pppmm')}_ea2026.gpkg")
            self.table_monitoring.setItem(row_idx, 5, gpkg_item)

            # Col 6: Final EARF Excel
            earf = r.get("final_earf", {})
            if earf.get("exists"):
                earf_item = QTableWidgetItem(f"READY ({earf.get('size_str', '')})")
                earf_item.setTextAlignment(Qt.AlignCenter)
                earf_item.setForeground(QColor("#047857"))
                earf_item.setBackground(QColor("#d1fae5"))
                earf_item.setToolTip(f"File: {earf.get('filename')}\nPath: {earf.get('path')}\nSize: {earf.get('size_str')}\nModified: {earf.get('mtime_str')}")
            else:
                earf_item = QTableWidgetItem("MISSING")
                earf_item.setTextAlignment(Qt.AlignCenter)
                earf_item.setForeground(QColor("#94a3b8"))
                earf_item.setBackground(QColor("#f8fafc"))
                earf_item.setToolTip(f"Expected: {r.get('pppmm')}_final_earf_{r.get('clean_name')}.xlsx")
            self.table_monitoring.setItem(row_idx, 6, earf_item)

            # Col 7: MIS EARF Excel
            mis = r.get("mis_earf", {})
            if mis.get("exists"):
                mis_item = QTableWidgetItem(f"READY ({mis.get('size_str', '')})")
                mis_item.setTextAlignment(Qt.AlignCenter)
                mis_item.setForeground(QColor("#6d28d9"))
                mis_item.setBackground(QColor("#ede9fe"))
                mis_item.setToolTip(f"File: {mis.get('filename')}\nPath: {mis.get('path')}\nSize: {mis.get('size_str')}\nModified: {mis.get('mtime_str')}")
            else:
                mis_item = QTableWidgetItem("MISSING")
                mis_item.setTextAlignment(Qt.AlignCenter)
                mis_item.setForeground(QColor("#94a3b8"))
                mis_item.setBackground(QColor("#f8fafc"))
                mis_item.setToolTip(f"Expected: {r.get('pppmm')}_MIS_{r.get('clean_name')}.xlsx")
            self.table_monitoring.setItem(row_idx, 7, mis_item)

            # Col 8: Last Modified
            mod_str = str(r.get("last_modified_str") or r.get("last_modified") or "-")
            mod_item = QTableWidgetItem(mod_str)
            mod_item.setTextAlignment(Qt.AlignCenter)
            self.table_monitoring.setItem(row_idx, 8, mod_item)

            # Col 9: Subfolder Location
            loc_item = QTableWidgetItem(r.get("folder_path", "") or "-")
            loc_item.setToolTip(r.get("folder_path", ""))
            self.table_monitoring.setItem(row_idx, 9, loc_item)

    def _on_monitoring_row_selected(self):
        """Called when a row is clicked/selected in table_monitoring."""
        selected_rows = self.table_monitoring.selectionModel().selectedRows()
        if not selected_rows:
            return
        row = selected_rows[0].row()
        page_size = self.mon_page_size if self.mon_page_size > 0 else len(self.mon_filtered_records)
        idx = (self.mon_page * page_size) + row
        if 0 <= idx < len(self.mon_filtered_records):
            rec = self.mon_filtered_records[idx]
            self.mon_selected_record = rec
            self._update_monitoring_detail_panel(rec)

    def _update_monitoring_detail_panel(self, rec):
        """Updates the bottom detail panel cards for the selected municipality."""
        if not rec:
            self.lbl_mon_detail_title.setText("<b>Selected Municipality:</b> None (Select any row above to inspect deliverables)")
            self.btn_mon_copy_folder.setEnabled(False)
            self.btn_mon_open_folder.setEnabled(False)
            self.btn_mon_export_now.setEnabled(False)
            self.btn_mon_review_qa.setEnabled(False)
            self.lbl_det_gpkg_status.setText("Status: -")
            self.lbl_det_gpkg_info.setText("File: -")
            self.btn_mon_load_gpkg.setEnabled(False)
            self.lbl_det_earf_status.setText("Status: -")
            self.lbl_det_earf_info.setText("File: -")
            self.btn_mon_open_earf.setEnabled(False)
            self.lbl_det_mis_status.setText("Status: -")
            self.lbl_det_mis_info.setText("File: -")
            self.btn_mon_open_mis.setEnabled(False)
            return

        m_title = f"<b>Selected:</b> {rec.get('code', '')} - {rec.get('name', '')} ({rec.get('province', '')}, {rec.get('region', '')})"
        self.lbl_mon_detail_title.setText(m_title)
        has_f = bool(rec.get("folder_path") and os.path.exists(rec.get("folder_path")))
        self.btn_mon_copy_folder.setEnabled(bool(rec.get("folder_path")))
        self.btn_mon_open_folder.setEnabled(has_f)
        self.btn_mon_export_now.setEnabled(bool(rec.get("has_matching_data", False)))
        has_any_deliv = bool(
            rec.get("has_matching_data") or
            rec.get("gpkg", {}).get("exists") or
            rec.get("final_earf", {}).get("exists") or
            rec.get("mis_earf", {}).get("exists")
        )
        self.btn_mon_review_qa.setEnabled(has_any_deliv)

        # GPKG Block
        g = rec.get("gpkg", {})
        if g.get("exists"):
            self.lbl_det_gpkg_status.setText(f"Status: <b style='color:#0369a1;'>READY</b> ({g.get('size_str')})")
            feat_text = f" | Features: {g.get('feature_count'):,}" if g.get("feature_count", 0) > 0 else ""
            self.lbl_det_gpkg_info.setText(f"File: {g.get('filename')}{feat_text}\nModified: {g.get('mtime_str')}")
            self.btn_mon_load_gpkg.setEnabled(True)
        else:
            self.lbl_det_gpkg_status.setText("Status: <b style='color:#dc2626;'>MISSING</b>")
            self.lbl_det_gpkg_info.setText(f"File: Expected {rec.get('pppmm')}_ea2026.gpkg")
            self.btn_mon_load_gpkg.setEnabled(False)

        # Final EARF Block
        e = rec.get("final_earf", {})
        if e.get("exists"):
            self.lbl_det_earf_status.setText(f"Status: <b style='color:#047857;'>READY</b> ({e.get('size_str')})")
            self.lbl_det_earf_info.setText(f"File: {e.get('filename')}\nModified: {e.get('mtime_str')}")
            self.btn_mon_open_earf.setEnabled(True)
        else:
            self.lbl_det_earf_status.setText("Status: <b style='color:#dc2626;'>MISSING</b>")
            self.lbl_det_earf_info.setText(f"File: Expected {rec.get('pppmm')}_final_earf_{rec.get('clean_name')}.xlsx")
            self.btn_mon_open_earf.setEnabled(False)

        # MIS EARF Block
        m = rec.get("mis_earf", {})
        if m.get("exists"):
            self.lbl_det_mis_status.setText(f"Status: <b style='color:#6d28d9;'>READY</b> ({m.get('size_str')})")
            self.lbl_det_mis_info.setText(f"File: {m.get('filename')}\nModified: {m.get('mtime_str')}")
            self.btn_mon_open_mis.setEnabled(True)
        else:
            self.lbl_det_mis_status.setText("Status: <b style='color:#dc2626;'>MISSING</b>")
            self.lbl_det_mis_info.setText(f"File: Expected {rec.get('pppmm')}_MIS_{rec.get('clean_name')}.xlsx")
            self.btn_mon_open_mis.setEnabled(False)

    def _on_monitoring_row_double_clicked(self, row, col):
        """Double clicking a row loads GPKG to map if available, or opens the subfolder."""
        page_size = self.mon_page_size if self.mon_page_size > 0 else len(self.mon_filtered_records)
        idx = (self.mon_page * page_size) + row
        if 0 <= idx < len(self.mon_filtered_records):
            rec = self.mon_filtered_records[idx]
            if rec.get("gpkg", {}).get("exists"):
                self._load_selected_gpkg_to_map()
            elif rec.get("folder_path") and os.path.exists(rec.get("folder_path")):
                self._open_selected_folder()

    def _show_monitoring_context_menu(self, pos):
        """Displays right-click action menu on the monitoring table."""
        selected_rows = self.table_monitoring.selectionModel().selectedRows()
        if not selected_rows:
            return
        row = selected_rows[0].row()
        page_size = self.mon_page_size if self.mon_page_size > 0 else len(self.mon_filtered_records)
        idx = (self.mon_page * page_size) + row
        if not (0 <= idx < len(self.mon_filtered_records)):
            return
        rec = self.mon_filtered_records[idx]

        menu = QMenu(self)
        action_map = menu.addAction("Load Output GeoPackage to Map Canvas")
        action_map.setEnabled(bool(rec.get("gpkg", {}).get("exists")))
        action_map.triggered.connect(self._load_selected_gpkg_to_map)

        action_earf = menu.addAction("Open Final EARF Spreadsheet")
        action_earf.setEnabled(bool(rec.get("final_earf", {}).get("exists")))
        action_earf.triggered.connect(self._open_selected_final_earf)

        action_mis = menu.addAction("Open MIS EARF Spreadsheet")
        action_mis.setEnabled(bool(rec.get("mis_earf", {}).get("exists")))
        action_mis.triggered.connect(self._open_selected_mis_earf)

        menu.addSeparator()

        action_folder = menu.addAction("Open Subfolder in Windows Explorer")
        action_folder.setEnabled(bool(rec.get("folder_path") and os.path.exists(rec.get("folder_path"))))
        action_folder.triggered.connect(self._open_selected_folder)

        action_copy = menu.addAction("Copy Subfolder Path")
        action_copy.setEnabled(bool(rec.get("folder_path")))
        action_copy.triggered.connect(self._copy_selected_folder_path)

        menu.addSeparator()

        action_qa = menu.addAction(f"Audit & Review Outputs (QA) for {rec.get('name', 'Selected Mun')}...")
        action_qa.setEnabled(bool(
            rec.get("has_matching_data") or
            rec.get("gpkg", {}).get("exists") or
            rec.get("final_earf", {}).get("exists") or
            rec.get("mis_earf", {}).get("exists")
        ))
        action_qa.triggered.connect(lambda: self._open_qa_review_for_record(rec))

        action_export = menu.addAction(f"Export Deliverables for {rec.get('name', 'Selected Mun')}")
        action_export.setEnabled(bool(rec.get("has_matching_data", False)))
        action_export.triggered.connect(self._export_deliverables_for_selected_mun)

        menu.addSeparator()

        action_refresh_mun = menu.addAction(f"Refresh Deliverables for {rec.get('name', 'This Municipality')}")
        action_refresh_mun.triggered.connect(lambda: self._refresh_single_municipality(rec))

        menu.exec_(self.table_monitoring.viewport().mapToGlobal(pos))

    def _load_selected_gpkg_to_map(self):
        rec = self.mon_selected_record
        if not rec or not rec.get("gpkg", {}).get("exists"):
            return
        gpkg_path = rec["gpkg"]["path"]
        mun_name = rec.get("name", "")
        pppmm = rec.get("pppmm", "")
        clean_cm = rec.get("clean_name", "ea2026")
        layer_name = f"{pppmm}_ea2026 - {clean_cm}"
        vlayer = QgsVectorLayer(gpkg_path, layer_name, "ogr")
        if vlayer.isValid():
            QgsProject.instance().addMapLayer(vlayer)
            QMessageBox.information(self, "Layer Added", f"Successfully loaded GeoPackage layer into map canvas:\n'{layer_name}'")
        else:
            QMessageBox.warning(self, "Invalid Layer", f"Could not load vector layer from GeoPackage:\n{gpkg_path}")

    def _open_selected_final_earf(self):
        rec = self.mon_selected_record
        if not rec or not rec.get("final_earf", {}).get("exists"):
            return
        p = rec["final_earf"]["path"]
        try:
            os.startfile(p)
        except Exception as e:
            QMessageBox.warning(self, "Open File Failed", f"Could not open Final EARF file: {e}")

    def _open_selected_mis_earf(self):
        rec = self.mon_selected_record
        if not rec or not rec.get("mis_earf", {}).get("exists"):
            return
        p = rec["mis_earf"]["path"]
        try:
            os.startfile(p)
        except Exception as e:
            QMessageBox.warning(self, "Open File Failed", f"Could not open MIS EARF file: {e}")

    def _open_selected_folder(self):
        rec = self.mon_selected_record
        if not rec or not rec.get("folder_path") or not os.path.exists(rec.get("folder_path")):
            QMessageBox.warning(self, "Folder Missing", "The subfolder for this municipality does not exist yet.")
            return
        try:
            os.startfile(rec["folder_path"])
        except Exception as e:
            QMessageBox.warning(self, "Open Folder Failed", f"Could not open subfolder: {e}")

    def _copy_selected_folder_path(self):
        rec = self.mon_selected_record
        if rec and rec.get("folder_path"):
            QApplication.clipboard().setText(rec["folder_path"])
            QMessageBox.information(self, "Copied", f"Copied path to clipboard:\n{rec['folder_path']}")

    def _export_monitoring_report(self):
        """Exports the current monitoring records to a CSV audit report."""
        if not self.mon_filtered_records:
            QMessageBox.warning(self, "No Records", "No monitoring records to export.")
            return

        default_path = os.path.join(
            self.txt_mon_root_dir.text().strip() or os.getcwd(),
            f"deliverables_monitoring_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        )
        out_csv, _ = QFileDialog.getSaveFileName(
            self,
            "Export Deliverables Monitoring Report (CSV)",
            default_path,
            "CSV Files (*.csv)"
        )
        if not out_csv:
            return

        try:
            cnt = self.db.export_monitoring_report_csv(out_csv, self.mon_filtered_records)
            QMessageBox.information(
                self,
                "Report Exported",
                f"Successfully exported deliverables monitoring report ({cnt:,} rows) to:\n{out_csv}"
            )
        except Exception as e:
            QMessageBox.critical(self, "Export Failed", f"Failed to export monitoring report: {e}")

    def _open_qa_review_for_selected_mun(self):
        """Opens the Deliverables Quality & Value Review dialog for the selected municipality."""
        rec = self.mon_selected_record
        if not rec:
            return
        self._open_qa_review_for_record(rec)

    def _open_qa_review_for_record(self, rec: Dict[str, Any]):
        """Opens the QA Review modal dialog for a specific municipality record."""
        c_code = rec.get("code")
        if not c_code:
            return

        if DeliverablesReviewDialog is None:
            QMessageBox.critical(self, "Module Missing", "The DeliverablesReviewDialog module could not be loaded.")
            return

        root_dir = self.txt_mon_root_dir.text().strip() or getattr(self, "_final_export_root_dir", None) or DEFAULT_FINAL_EXPORT_ROOT
        target_folder, _ = self.db.resolve_final_export_subfolder(root_dir, c_code)
        if not target_folder or not os.path.exists(target_folder):
            target_folder = rec.get("folder_path") or root_dir

        dlg = DeliverablesReviewDialog(
            parent=self,
            city_mun_code=c_code,
            target_dir=target_folder,
            parent_db=self.db,
            on_reexport_callback=self._export_deliverables_for_citymun,
        )
        dlg.exec_()

        # Update monitoring cache and UI table
        self._update_monitoring_for_citymuns([c_code])

    def _export_deliverables_for_selected_mun(self):
        """Directly exports the 3 final deliverables for the selected municipality in background."""
        rec = self.mon_selected_record
        if not rec:
            return
        c_code = rec.get("code")
        m_name = rec.get("name")
        reply = QMessageBox.question(
            self,
            "Confirm Deliverables Export",
            f"Export final deliverables for {c_code} ({m_name})?\n\n"
            f"Deliverables to generate:\n"
            f"  • Final GeoPackage (<pppmm>_ea2026.gpkg)\n"
            f"  • Final EARF Excel (<pppmm>_final_earf_<citymun>.xlsx)\n"
            f"  • Final MIS Excel (<pppmm>_MIS_<citymun>.xlsx)\n"
            f"  • PCD EARF Excel (<pppmm>_earf2026_PCD.xlsx)",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )
        if reply != QMessageBox.Yes:
            return

        self._export_deliverables_for_citymun(c_code)

    def _export_deliverables_for_citymun(self, city_mun_code: str) -> bool:
        """Exports the 3 final deliverables for a specific municipality in a background worker thread."""
        root_dir = self.txt_mon_root_dir.text().strip() or getattr(self, "_final_export_root_dir", None) or DEFAULT_FINAL_EXPORT_ROOT
        if not os.path.exists(root_dir):
            root_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

        target_folder, hier = self.db.resolve_final_export_subfolder(root_dir, city_mun_code)
        if not target_folder:
            target_folder = root_dir
        os.makedirs(target_folder, exist_ok=True)

        mun_name = hier.get("city_mun") or city_mun_code
        clean_cm = re.sub(r'\s*\([^)]*\)', '', mun_name)
        clean_cm = re.sub(r'[^a-zA-Z0-9]+', '_', clean_cm).strip('_').lower()
        if not clean_cm:
            clean_cm = "ea2026"
        pppmm = city_mun_code[:5] if len(city_mun_code) >= 5 else city_mun_code

        out_filename = f"{pppmm}_ea2026.gpkg"
        out_excel_filename = f"{pppmm}_final_earf_{clean_cm}.xlsx"
        out_mis_filename = f"{pppmm}_MIS_{clean_cm}.xlsx"
        out_pcd_filename = f"{pppmm}_earf2026_PCD.xlsx"
        out_path = os.path.join(target_folder, out_filename)
        out_excel_path = os.path.join(target_folder, out_excel_filename)
        out_mis_path = os.path.join(target_folder, out_mis_filename)
        out_pcd_path = os.path.join(target_folder, out_pcd_filename)

        geom_sources = []
        try:
            for l in QgsProject.instance().mapLayers().values():
                if isinstance(l, QgsVectorLayer) and is_polygon_layer(l):
                    src = l.source()
                    if src:
                        geom_sources.append(src)
        except Exception:
            pass

        task = {
            "city_mun": city_mun_code,
            "mun_name": mun_name,
            "clean_cm": clean_cm,
            "target_folder": target_folder,
            "out_gpkg_path": out_path,
            "out_excel_path": out_excel_path,
            "out_mis_path": out_mis_path,
            "out_pcd_path": out_pcd_path,
            "search": "",
            "status": "All",
            "ea_lifecycle": "All",
            "geometry_sources": geom_sources,
            "layer_name": clean_cm,
        }
        self._start_deliverables_export([task], is_batch=False, post_action_ask_canvas=False)
        return True

    def _update_monitoring_for_citymuns(self, citymun_codes: List[str]):
        """
        Incrementally checks deliverables on disk for specific municipalities,
        persists updates in SQLite, and refreshes the Dashboard UI without full re-scanning.
        """
        if not citymun_codes:
            return
        root_dir = self.txt_mon_root_dir.text().strip() or getattr(self, "_final_export_root_dir", None) or DEFAULT_FINAL_EXPORT_ROOT
        updated_records = []
        for c_code in citymun_codes:
            if not c_code:
                continue
            rec = self.db.update_single_municipality_deliverables(root_dir=root_dir, city_mun_code=c_code)
            if rec:
                updated_records.append(rec)

        if not updated_records:
            return

        # Update or insert into in-memory self.mon_records
        rec_map = {r.get("code"): r for r in self.mon_records}
        for u_rec in updated_records:
            rec_map[u_rec.get("code")] = u_rec
        self.mon_records = list(rec_map.values())
        self.mon_records.sort(key=lambda r: (r.get("region") or "", r.get("province") or "", r.get("name") or ""))

        # Refresh dashboard view and KPI cards
        self._apply_monitoring_filters()

    def _refresh_single_municipality(self, rec: Dict[str, Any]):
        """Re-scans disk for a single municipality, updates SQLite cache, and refreshes Dashboard."""
        if not rec:
            return
        c_code = rec.get("code")
        if not c_code:
            return
        self._update_monitoring_for_citymuns([c_code])
        if self.mon_selected_record and self.mon_selected_record.get("code") == c_code:
            for r in self.mon_records:
                if r.get("code") == c_code:
                    self._update_monitoring_detail_panel(r)
                    break
        QMessageBox.information(
            self,
            "Deliverables Refreshed",
            f"Updated deliverables status for {rec.get('name', c_code)}.\nSaved to local database cache."
        )

    def _apply_styles(self):
        self.setStyleSheet("""
            QDialog {
                background-color: #f8fafc;
                font-family: 'Segoe UI', Tahoma, sans-serif;
            }
            #headerFrame {
                background-color: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 6px;
            }
            #dialogTitle {
                color: #0f172a;
            }
            #dialogSubtitle {
                color: #64748b;
            }
            #kpiFrame {
                background-color: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 6px;
            }
            #kpiCard {
                background-color: #f8fafc;
                border: 1px solid #e2e8f0;
                border-radius: 4px;
            }
            #subTabs::pane {
                border: 1px solid #cbd5e1;
                background-color: #ffffff;
                border-radius: 4px;
            }
            #subTabs > QTabBar::tab {
                background: #f1f5f9;
                color: #475569;
                border: 1px solid #cbd5e1;
                border-bottom: none;
                padding: 6px 20px;
                min-width: 125px;
                margin-right: 3px;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
                font-weight: 600;
                font-size: 11px;
            }
            #subTabs > QTabBar::tab:selected {
                background: #ffffff;
                color: #0369a1;
                border-bottom: 2px solid #0284c7;
            }
            #subTabs > QTabBar::tab:hover:!selected {
                background: #e2e8f0;
            }
            #mainTabs > QTabBar::tab {
                background: #f1f5f9;
                color: #334155;
                border: 1px solid #cbd5e1;
                border-bottom: none;
                padding: 7px 24px;
                min-width: 160px;
                margin-right: 4px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                font-weight: 600;
                font-size: 12px;
            }
            #mainTabs > QTabBar::tab:selected {
                background: #ffffff;
                color: #0369a1;
                border-bottom: 2px solid #0284c7;
            }
            #previewTable, #matchingTable {
                background-color: #ffffff;
                alternate-background-color: #f8fafc;
                border: 1px solid #cbd5e1;
                gridline-color: #e2e8f0;
                font-size: 11px;
            }
            QHeaderView::section {
                background-color: #f1f5f9;
                color: #334155;
                padding: 4px 6px;
                font-weight: 600;
                border: 1px solid #cbd5e1;
            }
            #primaryBtn {
                background-color: #0284c7;
                color: #ffffff;
                font-weight: 600;
                border-radius: 4px;
                padding: 4px 14px;
                border: none;
            }
            #primaryBtn:hover {
                background-color: #0369a1;
            }
            #actionBtn {
                background-color: #059669;
                color: #ffffff;
                font-weight: 600;
                border-radius: 4px;
                padding: 4px 12px;
                border: none;
            }
            #actionBtn:hover {
                background-color: #047857;
            }
            #stopBtn, #dangerBtn {
                background-color: #ffffff;
                color: #dc2626;
                font-weight: 600;
                border: 1px solid #f87171;
                border-radius: 4px;
                padding: 4px 12px;
            }
            #stopBtn:hover:!disabled, #dangerBtn:hover:!disabled {
                background-color: #dc2626;
                color: #ffffff;
                border-color: #dc2626;
            }
            #stopBtn:disabled, #dangerBtn:disabled {
                background-color: #f8fafc;
                color: #cbd5e1;
                border: 1px solid #e2e8f0;
            }
            #secondaryBtn, #closeBtn {
                background-color: #ffffff;
                color: #334155;
                border: 1px solid #cbd5e1;
                border-radius: 4px;
                padding: 4px 12px;
            }
            #secondaryBtn:hover, #closeBtn:hover {
                background-color: #f1f5f9;
            }
            #consoleLog {
                background-color: #0f172a;
                color: #e2e8f0;
                font-family: Consolas, 'Courier New', monospace;
                font-size: 11px;
                border-radius: 4px;
            }
            #dbBadge {
                font-size: 11px;
            }
            #pageInfo {
                color: #475569;
                font-weight: 600;
                padding: 0 4px;
            }
        """)
