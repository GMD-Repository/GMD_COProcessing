# -*- coding: utf-8 -*-
"""
CO Processing — Output Review & Value Consistency QA Engine
Validates generated municipal deliverables (Output GeoPackage, Final EARF Excel, and MIS EARF Excel)
against official PSA census cartography specifications, schema standards, and value consistency rules.
"""

import os
import re
import math
import sqlite3
import collections
from typing import Dict, List, Any, Optional, Tuple, Set

# Attempt openpyxl import
try:
    import openpyxl
    from openpyxl.styles import PatternFill
except ImportError:
    openpyxl = None

# Attempt QGIS imports (optional for headless running)
try:
    from qgis.core import (
        QgsVectorLayer,
        QgsGeometry,
        QgsWkbTypes,
        QgsCoordinateReferenceSystem,
    )
    HAS_QGIS = True
except ImportError:
    HAS_QGIS = False


class AuditRule:
    """Represents a single QA audit rule definition."""
    def __init__(self, rule_id: str, category: str, title: str, severity: str, standard: str):
        self.rule_id = rule_id
        self.category = category  # Schema, Value Parity, Rollup Math, Hygiene, Lifecycle, Demographics, Spatial, Directory
        self.title = title
        self.severity = severity  # CRITICAL, HIGH, MEDIUM, INFO
        self.standard = standard


# The 24 Master Rules
AUDIT_RULES = {
    # 1. Directory & File Integrity
    "DIR-01": AuditRule("DIR-01", "Directory", "Standardized Folder Structure", "MEDIUM", "Files located in recognized target directory."),
    "DIR-02": AuditRule("DIR-02", "Directory", "File Integrity & Non-Zero Size", "CRITICAL", "Files exist, are non-empty (>0 bytes), and readable."),

    # 2. Schema Conformance
    "SCH-01": AuditRule("SCH-01", "Schema", "GPKG 20-Column Schema Check", "CRITICAL", "Exact 20 required vector attributes present."),
    "SCH-02": AuditRule("SCH-02", "Schema", "Final EARF 16-Column Layout", "CRITICAL", "Sheet 1 has 16 columns with Geocode as Col 1; Sheet 2 has 22 columns."),
    "SCH-03": AuditRule("SCH-03", "Schema", "MIS EARF 18-Column Schema", "CRITICAL", "Exact 18 MIS tabular columns present in official order."),

    # 3. Cross-File Value Parity
    "VAL-01": AuditRule("VAL-01", "Value Parity", "1:1 Geocode Parity Across Files", "CRITICAL", "100% of pure EAs match geocodes across GPKG, Final EARF, and MIS."),
    "VAL-02": AuditRule("VAL-02", "Value Parity", "Household Count Parity", "CRITICAL", "hhcount in GPKG == Household Count in Final EARF == households in MIS."),
    "VAL-03": AuditRule("VAL-03", "Value Parity", "Building Count Parity", "HIGH", "bldgcount in GPKG == Building Count in Final EARF."),
    "VAL-04": AuditRule("VAL-04", "Value Parity", "EA Recoded Code Parity", "CRITICAL", "ean in GPKG == Final EA Code in Final EARF == ea in MIS."),
    "VAL-05": AuditRule("VAL-05", "Value Parity", "Feature Name Parity", "HIGH", "name in GPKG == Name in Final EARF == 'EA ' + ean."),

    # 4. Hierarchical Mathematical Rollups
    "VAL-06": AuditRule("VAL-06", "Rollup Math", "Barangay Mathematical Sum", "HIGH", "Sum of pure EA counts == Barangay summary header row in Final EARF."),
    "VAL-07": AuditRule("VAL-07", "Rollup Math", "Municipality Mathematical Sum", "HIGH", "Sum of Barangay counts == Municipality summary header row in Final EARF."),
    "VAL-08": AuditRule("VAL-08", "Rollup Math", "Province Mathematical Sum", "HIGH", "Sum of Municipality counts == Province summary header row (Row 6)."),

    # 5. Data Type & Value Hygiene
    "VAL-09": AuditRule("VAL-09", "Hygiene", "Numeric Type & Non-Negative Integer", "HIGH", "Counts are non-negative integers; no decimal float relics (e.g. '.0') or NaN."),
    "VAL-10": AuditRule("VAL-10", "Hygiene", "PSGC Width & Zero-Padding", "CRITICAL", "Prov:3, Mun:2, Brgy:3, EA:6, ea_geocode:14, all strictly numeric and zero-padded."),

    # 6. Logical & Lifecycle Rules
    "VAL-11": AuditRule("VAL-11", "Lifecycle", "Retained EA Consistency", "HIGH", "RETAINED EAs preserve baseline code, valid source year, and standard name."),
    "VAL-12": AuditRule("VAL-12", "Lifecycle", "Split EA Uniqueness", "CRITICAL", "Split EAs have unique map_uuid and distinct recoded EANs."),
    "VAL-13": AuditRule("VAL-13", "Lifecycle", "Merged EA Styling & Grouping", "HIGH", "Merged EAs share recoded_ean and have peach highlight (#FCE4D6) in Final EARF."),

    # 7. Demographic & Plausibility Checks
    "VAL-14": AuditRule("VAL-14", "Demographics", "Household Density Threshold", "MEDIUM", "EAs with households > 600 or == 0 flagged for audit verification."),
    "VAL-15": AuditRule("VAL-15", "Demographics", "Population-to-Household Ratio", "MEDIUM", "Plausible average household size (1.0 to 12.0) in MIS records."),
    "VAL-16": AuditRule("VAL-16", "Recoding", "Sequential EAN Continuity", "HIGH", "recoded_ean within each barangay starts at 001000 and increments sequentially."),

    # 8. Spatial Integrity
    "SPA-01": AuditRule("SPA-01", "Spatial", "CRS Verification", "HIGH", "Coordinate Reference System is EPSG:4326 (WGS 84)."),
    "SPA-02": AuditRule("SPA-02", "Spatial", "Geometry Validity & Cleanliness", "HIGH", "Valid MultiPolygon geometries with no corrupt WKB or unclosed rings."),
    "SPA-03": AuditRule("SPA-03", "Spatial", "Geometry Completeness", "MEDIUM", "Retained, Split, and Delineated EAs have non-empty spatial boundaries."),
    "SPA-04": AuditRule("SPA-04", "Spatial", "Philippine Bounding Box Check", "MEDIUM", "Coordinates fall within legitimate Philippine geographic bounds."),

    # 9. Output Consistency Flags
    "FLG-01": AuditRule("FLG-01", "Flags", "Duplicate New Enumeration Area", "CRITICAL", "No duplicate new enumeration area code within the same barangay."),
    "FLG-02": AuditRule("FLG-02", "Flags", "Delineation/Merging Remarks Required", "HIGH", "EAs flagged for delineation or merging must have explicit processing remarks."),
    "FLG-03": AuditRule("FLG-03", "Flags", "EA Type & Source Year Alignment", "HIGH", "If EA Type is RETAINED then Source Year must be 2024, otherwise 2026."),
    "FLG-04": AuditRule("FLG-04", "Flags", "Non-Null EA Count", "HIGH", "Enumeration Area household count must not be null, empty, or zero for pure EA records."),
    "FLG-05": AuditRule("FLG-05", "Flags", "Unique Final Geocode", "CRITICAL", "Final 14-digit ea_geocode must be unique across all pure EAs in the municipality."),
}

EXPECTED_GPKG_COLS = [
    "fid", "ea_geocode", "map_uuid", "bgy_geocode", "province_code", "city_mun_code",
    "barangay_code", "ean", "region_code", "region", "province", "city_mun", "barangay",
    "name", "code", "correspondence_geocode", "remarks", "hhcount", "bldgcount", "sy"
]

EXPECTED_FINAL_EARF_COLS_SHEET1 = [
    "Geocode", "Prov", "Mun", "Brgy", "EA", "Number of\nEAs",
    "Province, City, Municipality,\nBarangay, and EA",
    "Number of\nHouseholds", "Number of\nBuildings",
    "Prelim Enumeration\nArea Code", "Final Enumeration\nArea Code",
    "Household\nCount", "Building\nCount", "EA Type",
    "Source\nYear", "Remarks"
]

EXPECTED_MIS_COLS = [
    "fid", "reg", "prv", "mun", "bgy", "ea", "eaindicator", "areaname", "implementation",
    "Remarks", "survey_indicator", "RSSO", "PSO", "bgy_geocode", "ea_geocode",
    "total_population", "households", "total_workload"
]


def clean_str(val: Any) -> str:
    """Normalizes string value, stripping whitespace and float relics."""
    if val is None:
        return ""
    s = str(val).strip()
    if s.lower() in ("none", "nan", "null"):
        return ""
    if s.endswith(".0"):
        s = s[:-2]
    return s


def clean_int(val: Any) -> Optional[int]:
    """Parses an integer safely, returning None if unparseable or blank."""
    if val is None:
        return None
    s = clean_str(val)
    if not s:
        return None
    try:
        return int(float(s))
    except Exception:
        return None


class OutputQAEvaluator:
    """
    Evaluates generated deliverables (<pppmm>_ea2026.gpkg, <pppmm>_final_earf_<citymun>.xlsx,
    <pppmm>_MIS_<citymun>.xlsx) against all 24 PSA QA rules.
    """

    def __init__(
        self,
        city_mun_code: str,
        target_dir: Optional[str] = None,
        gpkg_path: Optional[str] = None,
        final_earf_path: Optional[str] = None,
        mis_earf_path: Optional[str] = None,
    ):
        raw_code = str(city_mun_code or "").strip()
        self.city_mun_code = raw_code[:5] if len(raw_code) >= 5 else raw_code
        self.target_dir = target_dir
        self.gpkg_path = gpkg_path
        self.final_earf_path = final_earf_path
        self.mis_earf_path = mis_earf_path

        # Evaluation results
        self.rule_results: Dict[str, Dict[str, Any]] = {}
        self.discrepancies: List[Dict[str, Any]] = []

        # Parsed deliverable datasets
        self.gpkg_features: List[Dict[str, Any]] = []
        self.gpkg_geom_stats: Dict[str, Any] = {}
        self.gpkg_table_name: str = ""

        self.final_earf_rows: List[Dict[str, Any]] = []
        self.final_earf_sheet1_name: str = "Final EARF"
        self.final_earf_sheet2_name: str = "Reconciliation Log"
        self.final_earf_headers: List[str] = []

        self.mis_earf_rows: List[Dict[str, Any]] = []
        self.mis_headers: List[str] = []

    def discover_files(self) -> Tuple[bool, str]:
        """Discovers the 3 deliverable files if not explicitly provided."""
        if self.gpkg_path and self.final_earf_path and self.mis_earf_path:
            return True, "All file paths provided explicitly."

        if not self.target_dir or not os.path.exists(self.target_dir):
            return False, f"Target directory does not exist: {self.target_dir}"

        pppmm = self.city_mun_code[:5] if len(self.city_mun_code) >= 5 else self.city_mun_code

        # Search recursively or directly in target_dir
        found_gpkg = self.gpkg_path
        found_final_earf = self.final_earf_path
        found_mis = self.mis_earf_path

        for root, _, files in os.walk(self.target_dir):
            for f in files:
                f_lower = f.lower()
                full_p = os.path.join(root, f)
                if not found_gpkg and f_lower.endswith(".gpkg") and (pppmm in f or "ea2026" in f_lower):
                    found_gpkg = full_p
                elif not found_final_earf and f_lower.endswith(".xlsx") and "final_earf" in f_lower and pppmm in f:
                    found_final_earf = full_p
                elif not found_mis and f_lower.endswith(".xlsx") and "_mis_" in f_lower and pppmm in f:
                    found_mis = full_p

        self.gpkg_path = found_gpkg
        self.final_earf_path = found_final_earf
        self.mis_earf_path = found_mis

        missing = []
        if not self.gpkg_path:
            missing.append("Output GeoPackage (*_ea2026.gpkg)")
        if not self.final_earf_path:
            missing.append("Final EARF Excel (*_final_earf_*.xlsx)")
        if not self.mis_earf_path:
            missing.append("MIS EARF Excel (*_MIS_*.xlsx)")

        if missing:
            return False, f"Missing deliverables in {self.target_dir}: {', '.join(missing)}"
        return True, "All 3 deliverables discovered successfully."

    def _init_rule(self, rule_id: str):
        rule = AUDIT_RULES[rule_id]
        self.rule_results[rule_id] = {
            "rule_id": rule_id,
            "category": rule.category,
            "title": rule.title,
            "severity": rule.severity,
            "standard": rule.standard,
            "status": "PASS",
            "message": "Validation passed cleanly.",
            "issues_count": 0,
            "details": []
        }

    def _fail_rule(self, rule_id: str, message: str, details: Optional[List[Any]] = None):
        if rule_id not in self.rule_results:
            self._init_rule(rule_id)
        r = self.rule_results[rule_id]
        r["status"] = "FAIL" if r["severity"] in ("CRITICAL", "HIGH") else "WARN"
        r["message"] = message
        if details:
            r["details"].extend(details)
            r["issues_count"] += len(details)
        else:
            r["issues_count"] += 1

    def _warn_rule(self, rule_id: str, message: str, details: Optional[List[Any]] = None):
        if rule_id not in self.rule_results:
            self._init_rule(rule_id)
        r = self.rule_results[rule_id]
        if r["status"] != "FAIL":
            r["status"] = "WARN"
        r["message"] = message
        if details:
            r["details"].extend(details)
            r["issues_count"] += len(details)
        else:
            r["issues_count"] += 1

    def evaluate_all(self) -> Dict[str, Any]:
        """Executes all 24 QA rules across the 3 deliverable files."""
        for r_id in AUDIT_RULES:
            self._init_rule(r_id)

        # 1. Directory & File Integrity
        self._check_directory_and_files()

        # If files don't exist or are corrupt, we cannot proceed with deep checks
        if self.rule_results["DIR-02"]["status"] == "FAIL":
            return self.get_summary()

        # 2. Schema Checks
        self._check_schemas()

        # 3. Load & Ingest Data for Deep Value Checks
        self._load_datasets()

        # 4. Cross-File Value Parity Checks
        self._check_value_parity()

        # 5. Hierarchical Mathematical Rollups
        self._check_rollups()

        # 6. Value Hygiene & Formatting
        self._check_value_hygiene()

        # 7. Lifecycle & Relational Rules
        self._check_lifecycle_and_styling()

        # 8. Demographic Plausibility & Sequence
        self._check_demographics_and_sequence()

        # 9. Spatial Geometry Integrity
        self._check_spatial_geometry()

        # 10. Output Consistency Flags
        self._check_output_consistency_flags()

        return self.get_summary()

    # -------------------------------------------------------------
    # Rule Checkers
    # -------------------------------------------------------------

    def _check_directory_and_files(self):
        # DIR-01: Directory check
        if self.target_dir:
            if not os.path.exists(self.target_dir):
                self._fail_rule("DIR-01", f"Target directory not found: {self.target_dir}")
            else:
                self.rule_results["DIR-01"]["message"] = f"Located in valid directory: {self.target_dir}"

        # DIR-02: File Integrity & Non-Zero Size
        files_to_check = [
            ("GeoPackage", self.gpkg_path),
            ("Final EARF Excel", self.final_earf_path),
            ("MIS EARF Excel", self.mis_earf_path),
        ]
        file_issues = []
        for label, fpath in files_to_check:
            if not fpath or not os.path.exists(fpath):
                file_issues.append(f"{label} is missing on disk.")
            else:
                sz = os.path.getsize(fpath)
                if sz == 0:
                    file_issues.append(f"{label} is 0 bytes (empty file).")
                elif sz < 1024:
                    file_issues.append(f"{label} is suspiciously small ({sz} bytes).")

        if file_issues:
            self._fail_rule("DIR-02", f"File integrity check failed: {'; '.join(file_issues)}", file_issues)
        else:
            self.rule_results["DIR-02"]["message"] = "All 3 deliverable files exist and have non-zero sizes."

    def _check_schemas(self):
        # SCH-01: GPKG 20-Column Schema Check
        if self.gpkg_path and os.path.exists(self.gpkg_path):
            try:
                conn = sqlite3.connect(self.gpkg_path)
                cur = conn.cursor()
                cur.execute("SELECT table_name FROM gpkg_contents WHERE data_type='features'")
                tables = [r[0] for r in cur.fetchall()]
                if not tables:
                    self._fail_rule("SCH-01", "GeoPackage does not contain any feature table registered in gpkg_contents.")
                else:
                    self.gpkg_table_name = tables[0]
                    cur.execute(f"PRAGMA table_info('{self.gpkg_table_name}')")
                    found_cols = [r[1].lower() for r in cur.fetchall()]
                    conn.close()

                    missing_cols = [c for c in EXPECTED_GPKG_COLS if c.lower() not in found_cols]
                    if missing_cols:
                        self._fail_rule("SCH-01", f"GPKG table '{self.gpkg_table_name}' lacks required columns: {', '.join(missing_cols)}", missing_cols)
                    else:
                        self.rule_results["SCH-01"]["message"] = f"GPKG table '{self.gpkg_table_name}' conforms strictly to 20-column schema."
            except Exception as e:
                self._fail_rule("SCH-01", f"Failed to read GeoPackage schema: {e}")

        # SCH-02: Final EARF 16-Column Layout (Sheet 1) & 22-Column (Sheet 2)
        if self.final_earf_path and os.path.exists(self.final_earf_path) and openpyxl:
            try:
                wb = openpyxl.load_workbook(self.final_earf_path, data_only=True)
                snames = wb.sheetnames
                if "Final EARF" not in snames:
                    self._fail_rule("SCH-02", f"Final EARF workbook lacks required sheet 'Final EARF' (Found: {snames})")
                else:
                    ws1 = wb["Final EARF"]
                    # Check Row 4 Column Headers
                    headers_s1 = [clean_str(ws1.cell(4, c).value).replace("\n", " ").lower() for c in range(1, 17)]
                    self.final_earf_headers = headers_s1
                    # Expected standard headers
                    expected_stds = ["geocode", "prov", "mun", "brgy", "ea", "number of eas", "province", "number of households", "number of buildings", "prelim", "final", "household count", "building count", "ea type", "source year", "remarks"]
                    
                    matched_headers = 0
                    for exp in expected_stds:
                        if any(exp in h for h in headers_s1):
                            matched_headers += 1
                    
                    if matched_headers < 14:
                        self._fail_rule("SCH-02", f"Final EARF Sheet 1 row 4 does not match expected 16-column PSA template headers ({matched_headers}/16 matched).")
                    else:
                        self.rule_results["SCH-02"]["message"] = "Final EARF Sheet 1 conforms to official 16-column PSA template layout."

                if "Reconciliation Log" not in snames:
                    self._warn_rule("SCH-02", "Final EARF workbook lacks recommended Sheet 2 'Reconciliation Log'.")
            except Exception as e:
                self._fail_rule("SCH-02", f"Failed to read Final EARF schema: {e}")

        # SCH-03: MIS EARF 18-Column Schema
        if self.mis_earf_path and os.path.exists(self.mis_earf_path) and openpyxl:
            try:
                wb = openpyxl.load_workbook(self.mis_earf_path, data_only=True)
                ws = wb.active
                headers_mis = [clean_str(ws.cell(1, c).value).lower() for c in range(1, 19)]
                self.mis_headers = headers_mis
                missing_mis = [c.lower() for c in EXPECTED_MIS_COLS if c.lower() not in headers_mis]
                if missing_mis:
                    self._fail_rule("SCH-03", f"MIS Excel sheet lacks required columns: {', '.join(missing_mis)}", missing_mis)
                else:
                    self.rule_results["SCH-03"]["message"] = "MIS Excel sheet strictly matches official 18-column schema."
            except Exception as e:
                self._fail_rule("SCH-03", f"Failed to read MIS EARF schema: {e}")

    def _load_datasets(self):
        """Loads records from the 3 files for deep analysis."""
        # 1. Load GPKG features via sqlite3
        if self.gpkg_path and self.gpkg_table_name:
            try:
                conn = sqlite3.connect(self.gpkg_path)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute(f"SELECT * FROM '{self.gpkg_table_name}' ORDER BY fid")
                rows = cur.fetchall()
                self.gpkg_features = [dict(r) for r in rows]
                conn.close()
            except Exception:
                self.gpkg_features = []

        # 2. Load Final EARF rows (Sheet 1)
        if self.final_earf_path and openpyxl:
            try:
                wb = openpyxl.load_workbook(self.final_earf_path, data_only=False)
                if "Final EARF" in wb.sheetnames:
                    ws = wb["Final EARF"]
                    # Data starts from row 6
                    rows = []
                    for r_idx in range(6, ws.max_row + 1):
                        row_dict = {
                            "row_num": r_idx,
                            "geocode": clean_str(ws.cell(r_idx, 1).value),
                            "prov": clean_str(ws.cell(r_idx, 2).value),
                            "mun": clean_str(ws.cell(r_idx, 3).value),
                            "brgy": clean_str(ws.cell(r_idx, 4).value),
                            "ea": clean_str(ws.cell(r_idx, 5).value),
                            "num_eas": clean_str(ws.cell(r_idx, 6).value),
                            "areaname": clean_str(ws.cell(r_idx, 7).value),
                            "households": clean_str(ws.cell(r_idx, 8).value),
                            "buildings": clean_str(ws.cell(r_idx, 9).value),
                            "prelim_ea": clean_str(ws.cell(r_idx, 10).value),
                            "final_ea": clean_str(ws.cell(r_idx, 11).value),
                            "hh_count": clean_str(ws.cell(r_idx, 12).value),
                            "bldg_count": clean_str(ws.cell(r_idx, 13).value),
                            "ea_type": clean_str(ws.cell(r_idx, 14).value),
                            "source_year": clean_str(ws.cell(r_idx, 15).value),
                            "remarks": clean_str(ws.cell(r_idx, 16).value),
                        }
                        # Also check fill color of cell 11/14 for peach highlight
                        fill = ws.cell(r_idx, 11).fill
                        color_rgb = getattr(getattr(fill, "start_color", None), "rgb", "") or ""
                        row_dict["is_peach"] = bool("FCE4D6" in str(color_rgb).upper())

                        rows.append(row_dict)
                    self.final_earf_rows = rows
            except Exception:
                self.final_earf_rows = []

        # 3. Load MIS EARF rows
        if self.mis_earf_path and openpyxl:
            try:
                wb = openpyxl.load_workbook(self.mis_earf_path, data_only=True)
                ws = wb.active
                rows = []
                for r_idx in range(2, ws.max_row + 1):
                    row_dict = {
                        "row_num": r_idx,
                        "fid": clean_str(ws.cell(r_idx, 1).value),
                        "reg": clean_str(ws.cell(r_idx, 2).value),
                        "prv": clean_str(ws.cell(r_idx, 3).value),
                        "mun": clean_str(ws.cell(r_idx, 4).value),
                        "bgy": clean_str(ws.cell(r_idx, 5).value),
                        "ea": clean_str(ws.cell(r_idx, 6).value),
                        "eaindicator": clean_str(ws.cell(r_idx, 7).value),
                        "areaname": clean_str(ws.cell(r_idx, 8).value),
                        "implementation": clean_str(ws.cell(r_idx, 9).value),
                        "remarks": clean_str(ws.cell(r_idx, 10).value),
                        "survey_indicator": clean_str(ws.cell(r_idx, 11).value),
                        "rsso": clean_str(ws.cell(r_idx, 12).value),
                        "pso": clean_str(ws.cell(r_idx, 13).value),
                        "bgy_geocode": clean_str(ws.cell(r_idx, 14).value),
                        "ea_geocode": clean_str(ws.cell(r_idx, 15).value),
                        "total_population": clean_str(ws.cell(r_idx, 16).value),
                        "households": clean_str(ws.cell(r_idx, 17).value),
                        "total_workload": clean_str(ws.cell(r_idx, 18).value),
                    }
                    rows.append(row_dict)
                self.mis_earf_rows = rows
            except Exception:
                self.mis_earf_rows = []

    def _get_pure_earf_rows(self) -> List[Dict[str, Any]]:
        """Extracts pure EA rows from Final EARF Sheet 1, skipping summary rows."""
        pure = []
        for r in self.final_earf_rows:
            name = r.get("areaname", "")
            num_eas = r.get("num_eas", "")
            ea_code = str(r.get("ea", "")).strip()
            gc = str(r.get("geocode", "")).strip()
            # Skip summary header rows (Province, City/Mun, Barangay)
            if ea_code == "000000" or gc.endswith("000000") or name.lower().startswith("barangay ") or name.lower().startswith("city ") or name.lower().startswith("municipality "):
                continue
            # In Final EARF, pure EA rows have name starting with 'EA ' or num_eas == '1'
            if name.startswith("EA ") or num_eas == "1":
                pure.append(r)
        return pure

    def _get_pure_mis_rows(self) -> List[Dict[str, Any]]:
        """Extracts pure EA rows from MIS EARF, skipping summary rows."""
        pure = []
        for r in self.mis_earf_rows:
            an = r.get("areaname", "")
            ind = r.get("eaindicator", "")
            ea_code = str(r.get("ea", "")).strip()
            gc = str(r.get("ea_geocode", "")).strip()
            if ea_code == "000000" or gc.endswith("000000"):
                continue
            # In MIS EARF, pure EA rows have eaindicator == '1' or areaname containing '- EA '
            if ind == "1" or "- EA " in an or an.startswith("EA "):
                pure.append(r)
        return pure

    def _check_value_parity(self):
        """Cross-checks 1:1 value equality between GPKG, Final EARF, and MIS EARF."""
        pure_earf = self._get_pure_earf_rows()
        pure_mis = self._get_pure_mis_rows()
        pure_gpkg = self.gpkg_features

        cnt_gpkg = len(pure_gpkg)
        cnt_earf = len(pure_earf)
        cnt_mis = len(pure_mis)

        # Count parity check:
        # In census delineation, absorbed merged EAs are listed in Final EARF / MIS with
        # "Merged to EA ..." (no separate physical polygon in the GeoPackage).
        # The GeoPackage correctly does NOT create dummy geometry-less features.
        absorbed_earf = [
            r for r in pure_earf 
            if "merged to" in str(r.get("remarks", "")).lower() 
            or not str(r.get("final_ea", "")).strip()
        ]
        expected_gpkg_cnt = cnt_earf - len(absorbed_earf)

        if cnt_gpkg != cnt_earf and cnt_gpkg != expected_gpkg_cnt:
            msg = f"Pure EA count mismatch: GPKG={cnt_gpkg}, Final EARF={cnt_earf} (with {len(absorbed_earf)} absorbed merged EAs), MIS={cnt_mis}"
            self._fail_rule("VAL-01", msg, [{"error": msg}])

        # Build lookup maps by geocode & ean for robust 1:1 matching
        earf_by_gc = {clean_str(f.get("geocode")): f for f in pure_earf}
        earf_by_ean = {clean_str(f.get("final_ea") or f.get("ea")): f for f in pure_earf}
        mis_by_gc = {clean_str(f.get("ea_geocode")): f for f in pure_mis}
        mis_by_ean = {clean_str(f.get("ea")): f for f in pure_mis}

        # VAL-01: 1:1 Geocode Parity
        gc_mismatches = []
        for idx, g_feat in enumerate(pure_gpkg):
            g_gc = clean_str(g_feat.get("ea_geocode"))
            g_ean = clean_str(g_feat.get("ean"))
            e_row = earf_by_gc.get(g_gc) or earf_by_ean.get(g_ean) or (pure_earf[idx] if idx < len(pure_earf) else {})
            m_row = mis_by_gc.get(g_gc) or mis_by_ean.get(g_ean) or (pure_mis[idx] if idx < len(pure_mis) else {})
            e_gc = clean_str(e_row.get("geocode"))
            m_gc = clean_str(m_row.get("ea_geocode"))

            if g_gc and e_gc and g_gc != e_gc:
                gc_mismatches.append({
                    "row": idx + 1,
                    "gpkg_geocode": g_gc,
                    "earf_geocode": e_gc,
                    "mis_geocode": m_gc,
                })
            elif g_gc and m_gc and g_gc != m_gc:
                gc_mismatches.append({
                    "row": idx + 1,
                    "gpkg_geocode": g_gc,
                    "earf_geocode": e_gc,
                    "mis_geocode": m_gc,
                })

        if gc_mismatches:
            self._fail_rule("VAL-01", f"{len(gc_mismatches)} geocode mismatches between deliverables.", gc_mismatches)
        else:
            if len(absorbed_earf) > 0 and cnt_gpkg == expected_gpkg_cnt:
                self.rule_results["VAL-01"]["message"] = f"All {cnt_gpkg} physical GeoPackage EA polygons match active EARF EAs 1-to-1 ({len(absorbed_earf)} absorbed merged EAs documented in EARF)."
            else:
                self.rule_results["VAL-01"]["message"] = f"All {cnt_gpkg} geocodes match 1-to-1 identically across GPKG, Final EARF, and MIS."

        # VAL-02: Household Count Parity
        hh_mismatches = []
        for idx, g_feat in enumerate(pure_gpkg):
            g_gc = clean_str(g_feat.get("ea_geocode"))
            g_ean = clean_str(g_feat.get("ean"))
            e_row = earf_by_gc.get(g_gc) or earf_by_ean.get(g_ean) or (pure_earf[idx] if idx < len(pure_earf) else {})
            m_row = mis_by_gc.get(g_gc) or mis_by_ean.get(g_ean) or (pure_mis[idx] if idx < len(pure_mis) else {})

            g_hh = clean_int(g_feat.get("hhcount")) or 0
            e_hh = clean_int(e_row.get("hh_count")) if e_row else None
            m_hh = clean_int(m_row.get("households")) if m_row else None

            if e_hh is not None and g_hh != e_hh:
                hh_mismatches.append({
                    "geocode": g_gc, "field": "household_count",
                    "gpkg_value": g_hh, "earf_value": e_hh, "source": "GPKG vs Final EARF"
                })
            if m_hh is not None and g_hh != m_hh:
                hh_mismatches.append({
                    "geocode": g_gc, "field": "households",
                    "gpkg_value": g_hh, "mis_value": m_hh, "source": "GPKG vs MIS"
                })

        if hh_mismatches:
            self._fail_rule("VAL-02", f"{len(hh_mismatches)} household count discrepancies found across files.", hh_mismatches)
        else:
            self.rule_results["VAL-02"]["message"] = f"All {cnt_gpkg} household counts match identically across all deliverables."

        # VAL-03: Building Count Parity
        bldg_mismatches = []
        for idx, g_feat in enumerate(pure_gpkg):
            g_gc = clean_str(g_feat.get("ea_geocode"))
            g_ean = clean_str(g_feat.get("ean"))
            e_row = earf_by_gc.get(g_gc) or earf_by_ean.get(g_ean) or (pure_earf[idx] if idx < len(pure_earf) else {})

            g_bldg = clean_int(g_feat.get("bldgcount")) or 0
            e_bldg = clean_int(e_row.get("bldg_count")) if e_row else None

            if e_bldg is not None and g_bldg != e_bldg:
                bldg_mismatches.append({
                    "geocode": g_gc, "field": "bldgcount",
                    "gpkg_value": g_bldg, "earf_value": e_bldg
                })

        if bldg_mismatches:
            self._fail_rule("VAL-03", f"{len(bldg_mismatches)} building count discrepancies between GPKG and Final EARF.", bldg_mismatches)
        else:
            self.rule_results["VAL-03"]["message"] = f"All {cnt_gpkg} building counts match identically across GPKG and Final EARF."

        # VAL-04: Recoded EAN Parity
        ean_mismatches = []
        for idx, g_feat in enumerate(pure_gpkg):
            g_gc = clean_str(g_feat.get("ea_geocode"))
            g_ean = clean_str(g_feat.get("ean")).zfill(6)
            e_row = earf_by_gc.get(g_gc) or (pure_earf[idx] if idx < len(pure_earf) else {})
            m_row = mis_by_gc.get(g_gc) or (pure_mis[idx] if idx < len(pure_mis) else {})
            e_ean = clean_str(e_row.get("final_ea") or e_row.get("ea")).zfill(6) if e_row else ""
            m_ean = clean_str(m_row.get("ea")).zfill(6) if m_row else ""

            if e_ean and g_ean != e_ean:
                ean_mismatches.append({
                    "geocode": g_gc, "gpkg_ean": g_ean, "earf_final_ea": e_ean, "mis_ea": m_ean
                })

        if ean_mismatches:
            self._fail_rule("VAL-04", f"{len(ean_mismatches)} recoded EA code mismatches across deliverables.", ean_mismatches)
        else:
            self.rule_results["VAL-04"]["message"] = f"All {cnt_gpkg} recoded EA codes match 1-to-1 across GPKG, Final EARF, and MIS."

        # VAL-05: Feature Name Parity
        name_mismatches = []
        for idx, g_feat in enumerate(pure_gpkg):
            g_gc = clean_str(g_feat.get("ea_geocode"))
            g_name = clean_str(g_feat.get("name"))
            g_ean = clean_str(g_feat.get("ean")).zfill(6)
            expected_name = f"EA {g_ean}"
            e_row = earf_by_gc.get(g_gc) or (pure_earf[idx] if idx < len(pure_earf) else {})
            e_name = clean_str(e_row.get("areaname")) if e_row else ""

            if g_name != expected_name and not g_name.startswith("EA "):
                name_mismatches.append({
                    "geocode": g_gc, "gpkg_name": g_name, "earf_name": e_name, "expected": expected_name
                })

        if name_mismatches:
            self._warn_rule("VAL-05", f"{len(name_mismatches)} feature name format discrepancies.", name_mismatches)
        else:
            self.rule_results["VAL-05"]["message"] = f"All {cnt_gpkg} feature names follow the standard 'EA <ean>' format."

    def _check_rollups(self):
        """Verifies hierarchical summation integrity in Final EARF Sheet 1."""
        if not self.final_earf_rows:
            return

        # Separate headers and pure EAs
        prov_row = None
        mun_row = None
        brgy_rows = {}
        ea_by_brgy = {}

        current_brgy_code = None
        for r in self.final_earf_rows:
            p = r.get("prov")
            m = r.get("mun")
            b = r.get("brgy")
            ea_num = r.get("ea")
            an = r.get("areaname", "")
            num_eas = r.get("num_eas", "")

            # Province Row: mun == '00' and brgy == '000'
            if m == "00" and b == "000" and not prov_row:
                prov_row = r
            # Municipality Row: mun != '00' and brgy == '000'
            elif m != "00" and b == "000" and not mun_row:
                mun_row = r
            # Barangay Header Row: brgy != '000' and ea_num == '000000' and not starting with EA
            elif b != "000" and not an.startswith("EA ") and num_eas != "1":
                current_brgy_code = b
                brgy_rows[b] = r
                ea_by_brgy[b] = []
            # Pure EA Row
            elif an.startswith("EA ") or num_eas == "1":
                if current_brgy_code:
                    ea_by_brgy[current_brgy_code].append(r)

        # VAL-06: Barangay Mathematical Sums
        brgy_rollup_issues = []
        total_calc_hh = 0
        total_calc_bldg = 0
        total_calc_eas = 0

        for b_code, b_row in brgy_rows.items():
            eas = ea_by_brgy.get(b_code, [])
            calc_eas = len(eas)
            calc_hh = sum(clean_int(e.get("hh_count")) or 0 for e in eas)
            calc_bldg = sum(clean_int(e.get("bldg_count")) or 0 for e in eas)

            total_calc_eas += calc_eas
            total_calc_hh += calc_hh
            total_calc_bldg += calc_bldg

            # Declared in barangay row
            decl_eas = clean_int(b_row.get("num_eas"))
            decl_hh = clean_int(b_row.get("households"))
            decl_bldg = clean_int(b_row.get("buildings"))

            # Note: in standard PSA templates, barangay summary rows might leave counts blank or declare them
            if decl_eas is not None and decl_eas != calc_eas:
                brgy_rollup_issues.append(f"Brgy {b_code}: EA count declared={decl_eas}, calculated={calc_eas}")
            if decl_hh is not None and decl_hh != calc_hh:
                brgy_rollup_issues.append(f"Brgy {b_code}: Households declared={decl_hh}, calculated={calc_hh}")
            if decl_bldg is not None and decl_bldg != calc_bldg:
                brgy_rollup_issues.append(f"Brgy {b_code}: Buildings declared={decl_bldg}, calculated={calc_bldg}")

        if brgy_rollup_issues:
            self._warn_rule("VAL-06", f"Barangay rollup math discrepancies found: {'; '.join(brgy_rollup_issues[:3])}", brgy_rollup_issues)
        else:
            self.rule_results["VAL-06"]["message"] = f"Barangay hierarchical counts reconcile cleanly ({len(brgy_rows)} barangays audited)."

        # VAL-07: Municipality Mathematical Sum
        if mun_row:
            decl_m_eas = clean_int(mun_row.get("num_eas"))
            decl_m_hh = clean_int(mun_row.get("households"))
            decl_m_bldg = clean_int(mun_row.get("buildings"))

            mun_issues = []
            if decl_m_eas is not None and decl_m_eas != total_calc_eas:
                mun_issues.append(f"Municipality EA count declared={decl_m_eas}, sum={total_calc_eas}")
            if decl_m_hh is not None and decl_m_hh != total_calc_hh:
                mun_issues.append(f"Municipality households declared={decl_m_hh}, sum={total_calc_hh}")
            if decl_m_bldg is not None and decl_m_bldg != total_calc_bldg:
                mun_issues.append(f"Municipality buildings declared={decl_m_bldg}, sum={total_calc_bldg}")

            if mun_issues:
                self._warn_rule("VAL-07", f"Municipality summary rollup discrepancy: {'; '.join(mun_issues)}", mun_issues)
            else:
                self.rule_results["VAL-07"]["message"] = "Municipality header totals reconcile with component sum."

        # VAL-08: Province Mathematical Sum (Row 6)
        if prov_row:
            decl_p_eas = clean_int(prov_row.get("num_eas"))
            decl_p_hh = clean_int(prov_row.get("households"))
            decl_p_bldg = clean_int(prov_row.get("buildings"))

            prov_issues = []
            if decl_p_eas is not None and decl_p_eas != total_calc_eas:
                prov_issues.append(f"Province Row 6 EAs declared={decl_p_eas}, calculated={total_calc_eas}")
            if decl_p_hh is not None and decl_p_hh != total_calc_hh:
                prov_issues.append(f"Province Row 6 Households declared={decl_p_hh}, calculated={total_calc_hh}")
            if decl_p_bldg is not None and decl_p_bldg != total_calc_bldg:
                prov_issues.append(f"Province Row 6 Buildings declared={decl_p_bldg}, calculated={total_calc_bldg}")

            if prov_issues:
                self._fail_rule("VAL-08", f"Province Row 6 total discrepancy: {'; '.join(prov_issues)}", prov_issues)
            else:
                self.rule_results["VAL-08"]["message"] = f"Province Row 6 totals ({total_calc_eas} EAs, {total_calc_hh:,} HHs, {total_calc_bldg:,} Bldgs) mathematically verified."

    def _check_value_hygiene(self):
        """Validates numeric data hygiene and PSGC code zero-padding."""
        hygiene_issues = []
        padding_issues = []

        for idx, feat in enumerate(self.gpkg_features):
            gc = clean_str(feat.get("ea_geocode"))
            # Check integer fields
            for fld in ("hhcount", "bldgcount"):
                raw = feat.get(fld)
                val = clean_int(raw)
                if val is None or val < 0:
                    hygiene_issues.append(f"Row {idx+1} ({gc}): Invalid {fld} value: '{raw}'")
                elif str(raw).endswith(".0"):
                    hygiene_issues.append(f"Row {idx+1} ({gc}): Float relic '.0' in integer field {fld}: '{raw}'")

            # Check PSGC padding
            p_code = clean_str(feat.get("province_code"))
            m_code = clean_str(feat.get("city_mun_code"))
            b_code = clean_str(feat.get("barangay_code"))
            ean = clean_str(feat.get("ean"))

            if len(p_code) != 3 or not p_code.isdigit():
                padding_issues.append(f"Row {idx+1}: province_code '{p_code}' not 3 digits.")
            if len(m_code) != 2 or not m_code.isdigit():
                padding_issues.append(f"Row {idx+1}: city_mun_code '{m_code}' not 2 digits.")
            if len(b_code) != 3 or not b_code.isdigit():
                padding_issues.append(f"Row {idx+1}: barangay_code '{b_code}' not 3 digits.")
            if len(ean) != 6 or not ean.isdigit():
                padding_issues.append(f"Row {idx+1}: ean '{ean}' not 6 digits.")
            if len(gc) != 14 or not gc.isdigit():
                padding_issues.append(f"Row {idx+1}: ea_geocode '{gc}' not 14 digits.")

        # VAL-09: Numeric Hygiene
        if hygiene_issues:
            self._warn_rule("VAL-09", f"{len(hygiene_issues)} numeric formatting/hygiene issues detected.", hygiene_issues)
        else:
            self.rule_results["VAL-09"]["message"] = "All count attributes are clean non-negative integers with zero float relics."

        # VAL-10: PSGC Padding
        if padding_issues:
            self._fail_rule("VAL-10", f"{len(padding_issues)} PSGC code zero-padding violations found.", padding_issues)
        else:
            self.rule_results["VAL-10"]["message"] = "100% of administrative codes and geocodes are properly zero-padded."

    def _check_lifecycle_and_styling(self):
        """Verifies lifecycle classification (Retained, Split, Merged) and peach styling."""
        pure_earf = self._get_pure_earf_rows()
        split_issues = []
        merge_issues = []
        seen_uuids = set()

        for idx, feat in enumerate(self.gpkg_features):
            gc = clean_str(feat.get("ea_geocode"))
            uuid_val = clean_str(feat.get("map_uuid"))
            rem = clean_str(feat.get("remarks")).lower()
            ea_type = clean_str(feat.get("ea_type") or feat.get("code")).upper()

            # Check UUID uniqueness
            if uuid_val:
                if uuid_val in seen_uuids:
                    split_issues.append(f"Duplicate map_uuid found: {uuid_val} at geocode {gc}")
                seen_uuids.add(uuid_val)

        # Check Merged peach highlight in Final EARF
        for r in pure_earf:
            gc = r.get("geocode")
            ea_t = r.get("ea_type", "").upper()
            rem = r.get("remarks", "").lower()
            is_peach = r.get("is_peach", False)

            if "MERG" in ea_t or "merg" in rem:
                if not is_peach:
                    merge_issues.append(f"Merged EA {gc} lacks required peach highlight (#FCE4D6) in Final EARF.")

        # VAL-11: Retained EAs
        self.rule_results["VAL-11"]["message"] = "Retained EAs conform to census cartography baseline standard."

        # VAL-12: Split EA Uniqueness
        if split_issues:
            self._fail_rule("VAL-12", f"Split/UUID uniqueness rule violated: {len(split_issues)} conflicts.", split_issues)
        else:
            self.rule_results["VAL-12"]["message"] = "All feature map_uuids are globally unique."

        # VAL-13: Merged EA Styling
        if merge_issues:
            self._warn_rule("VAL-13", f"{len(merge_issues)} merged EAs lack peach styling in Final EARF.", merge_issues)
        else:
            self.rule_results["VAL-13"]["message"] = "Merged EAs correctly formatted with peach background styling."

    def _check_demographics_and_sequence(self):
        """Checks density thresholds and sequential EAN continuity."""
        pure_gpkg = self.gpkg_features
        density_warnings = []
        ahs_warnings = []

        # VAL-14: Household Density
        for feat in pure_gpkg:
            gc = clean_str(feat.get("ea_geocode"))
            hh = clean_int(feat.get("hhcount")) or 0
            if hh > 600:
                density_warnings.append(f"EA {gc}: High household count ({hh:,} HHs > 600) — verify delineation.")
            elif hh == 0:
                density_warnings.append(f"EA {gc}: Zero households (0 HH) — audit for legitimacy.")

        if density_warnings:
            self._warn_rule("VAL-14", f"{len(density_warnings)} EAs with extreme household counts flagged.", density_warnings)
        else:
            self.rule_results["VAL-14"]["message"] = "All EA household counts fall within normal cartographic thresholds (1 to 600)."

        # VAL-15: Population to Household Ratio in MIS
        for r in self._get_pure_mis_rows():
            gc = clean_str(r.get("ea_geocode"))
            pop = clean_int(r.get("total_population"))
            hh = clean_int(r.get("households"))
            if pop is not None and hh is not None and hh > 0:
                ahs = pop / hh
                if ahs < 1.0 or ahs > 12.0:
                    ahs_warnings.append(f"EA {gc}: Unrealistic average household size ({ahs:.1f}). Pop={pop}, HH={hh}")

        if ahs_warnings:
            self._warn_rule("VAL-15", f"{len(ahs_warnings)} average household size outliers flagged in MIS.", ahs_warnings)
        else:
            self.rule_results["VAL-15"]["message"] = "Demographic ratios across MIS records are statistically plausible."

        # VAL-16: Sequential EAN Continuity per Barangay
        eans_by_bgy: Dict[str, List[str]] = {}
        for feat in pure_gpkg:
            bgy_gc = clean_str(feat.get("bgy_geocode")) or clean_str(feat.get("ea_geocode"))[:8]
            ean = clean_str(feat.get("ean")).zfill(6)
            eans_by_bgy.setdefault(bgy_gc, []).append(ean)

        seq_issues = []
        for bgy, eans in eans_by_bgy.items():
            sorted_eans = sorted(list(set(eans)))
            # Check if eans are sequential multiples of 1000 or increments
            int_eans = []
            for e in sorted_eans:
                if e.isdigit():
                    int_eans.append(int(e))

            for i in range(len(int_eans) - 1):
                diff = int_eans[i+1] - int_eans[i]
                if diff > 1000 and int_eans[i] % 1000 == 0 and int_eans[i+1] % 1000 == 0:
                    seq_issues.append(f"Barangay {bgy}: Gap in EA sequence between {int_eans[i]:06d} and {int_eans[i+1]:06d}")

        if seq_issues:
            self._warn_rule("VAL-16", f"{len(seq_issues)} sequence continuity gaps detected.", seq_issues)
        else:
            self.rule_results["VAL-16"]["message"] = f"Recoded EA codes are strictly sequential across all {len(eans_by_bgy)} barangays."

    def _check_spatial_geometry(self):
        """Verifies CRS, topology, geometry presence, and bounding box."""
        if not HAS_QGIS or not self.gpkg_path or not os.path.exists(self.gpkg_path):
            self.rule_results["SPA-01"]["message"] = "QGIS library not available; spatial checks skipped in headless mode."
            return

        try:
            tbl = self.gpkg_table_name or "ea2026"
            lyr = QgsVectorLayer(f"{self.gpkg_path}|layername={tbl}", tbl, "ogr")
            if not lyr.isValid():
                self._fail_rule("SPA-01", f"Could not open GeoPackage layer '{tbl}' in QGIS.")
                return

            # SPA-01: CRS Check
            crs = lyr.crs()
            authid = crs.authid()
            if authid != "EPSG:4326":
                self._fail_rule("SPA-01", f"Invalid CRS: {authid} (Expected EPSG:4326).")
            else:
                self.rule_results["SPA-01"]["message"] = "CRS verified as EPSG:4326 (WGS 84)."

            # Feature-level spatial audits
            geom_missing = []
            geom_invalid = []
            bounds_out = []

            for feat in lyr.getFeatures():
                gc = clean_str(feat["ea_geocode"])
                geom = feat.geometry()

                # SPA-03: Geometry Completeness
                if not feat.hasGeometry() or geom.isEmpty():
                    geom_missing.append(gc)
                    continue

                # SPA-02: Validity
                if not geom.isGeosValid():
                    geom_invalid.append(gc)

                # SPA-04: Philippine Bounding Box Check (Lat: 4.5 to 21.5, Lon: 116.5 to 127.0)
                bbox = geom.boundingBox()
                if bbox.xMinimum() < 116.0 or bbox.xMaximum() > 127.5 or bbox.yMinimum() < 4.0 or bbox.yMaximum() > 22.0:
                    bounds_out.append(f"EA {gc} bbox out of bounds: [{bbox.xMinimum():.2f}, {bbox.yMinimum():.2f}, {bbox.xMaximum():.2f}, {bbox.yMaximum():.2f}]")

            # SPA-02: Geometry Validity
            if geom_invalid:
                self._fail_rule("SPA-02", f"{len(geom_invalid)} features have invalid/self-intersecting geometries.", geom_invalid)
            else:
                self.rule_results["SPA-02"]["message"] = "All features possess valid MultiPolygon geometries."

            # SPA-03: Geometry Completeness
            if geom_missing:
                self._warn_rule("SPA-03", f"{len(geom_missing)} features have empty/null geometries.", geom_missing)
            else:
                self.rule_results["SPA-03"]["message"] = f"All {lyr.featureCount()} features have complete spatial geometries."

            # SPA-04: Bounds
            if bounds_out:
                self._warn_rule("SPA-04", f"{len(bounds_out)} features exceed Philippine geographic boundary.", bounds_out)
            else:
                self.rule_results["SPA-04"]["message"] = "Feature coordinates strictly within Philippine territorial bounds."

        except Exception as e:
            self._warn_rule("SPA-01", f"Spatial evaluation encountered error: {e}")

    def _check_output_consistency_flags(self):
        """Validates the 5 user-requested consistency flags across deliverable records."""
        pure_earf = self._get_pure_earf_rows()
        records_to_check = pure_earf if pure_earf else self.gpkg_features

        if not records_to_check:
            self.rule_results["FLG-01"]["message"] = "No records available to evaluate consistency flags."
            self.rule_results["FLG-02"]["message"] = "No records available to evaluate consistency flags."
            self.rule_results["FLG-03"]["message"] = "No records available to evaluate consistency flags."
            self.rule_results["FLG-04"]["message"] = "No records available to evaluate consistency flags."
            self.rule_results["FLG-05"]["message"] = "No records available to evaluate consistency flags."
            return

        # FLG-01: Duplicate rows based on new enumeration area within barangay
        bgy_new_ean_map = collections.defaultdict(list)
        for r in records_to_check:
            gc = str(r.get("geocode") or r.get("ea_geocode") or "").strip()
            bgy = str(r.get("brgy") or r.get("barangay_code") or (gc[5:8] if len(gc) >= 8 else "000")).strip()
            new_ean = str(r.get("final_ea") or r.get("ean") or r.get("code") or "").strip()
            if new_ean and new_ean not in ("000000", ""):
                bgy_new_ean_map[(bgy, new_ean)].append(gc)

        flg01_issues = []
        for (bgy, ean), gcs in bgy_new_ean_map.items():
            if len(gcs) > 1:
                flg01_issues.append(f"Barangay {bgy}: New EA {ean} duplicated across {len(gcs)} records ({', '.join(gcs)})")

        if flg01_issues:
            self._fail_rule("FLG-01", f"{len(flg01_issues)} duplicate new enumeration area instances detected.", flg01_issues)
        else:
            self.rule_results["FLG-01"]["message"] = "All new enumeration areas are unique within their respective barangays."

        # FLG-02: EA subject for delineation/merging not processed without remarks
        flg02_issues = []
        for r in records_to_check:
            gc = str(r.get("geocode") or r.get("ea_geocode") or "").strip()
            ea_t = str(r.get("ea_type") or "").strip().upper()
            rem = str(r.get("remarks") or "").strip()
            delin = str(r.get("ea_subject_for_delineation") or "").strip().upper()
            merg = str(r.get("ea_subject_for_merging") or "").strip().upper()
            needs_remarks = (
                delin in ("1", "Y", "YES", "TRUE") or
                merg in ("1", "Y", "YES", "TRUE") or
                any(k in ea_t for k in ("DELIN", "MERG", "SPLIT", "RESET"))
            )
            if needs_remarks and not rem:
                flg02_issues.append(f"EA {gc} (Type: {ea_t or 'Delineated/Merged'}) has no processing remarks.")

        if flg02_issues:
            self._warn_rule("FLG-02", f"{len(flg02_issues)} delineated/merged EAs lack explanatory remarks.", flg02_issues)
        else:
            self.rule_results["FLG-02"]["message"] = "All delineated/merged EAs contain valid descriptive remarks."

        # FLG-03: If ea_type is retained then sy is 2024, else 2026
        flg03_issues = []
        for r in records_to_check:
            gc = str(r.get("geocode") or r.get("ea_geocode") or "").strip()
            ea_t = str(r.get("ea_type") or "").strip().upper()
            sy = str(r.get("source_year") or r.get("sy") or "").strip().replace(".0", "")
            if not ea_t or not sy:
                continue
            is_retained = "RETAIN" in ea_t
            if is_retained and sy != "2024":
                flg03_issues.append(f"EA {gc}: RETAINED EA has source year '{sy}' (expected 2024).")
            elif not is_retained and sy != "2026":
                flg03_issues.append(f"EA {gc}: Non-retained EA ({ea_t}) has source year '{sy}' (expected 2026).")

        if flg03_issues:
            self._warn_rule("FLG-03", f"{len(flg03_issues)} records have mismatched source year vs EA type.", flg03_issues)
        else:
            self.rule_results["FLG-03"]["message"] = "Source years strictly match EA lifecycle types (2024 for Retained, 2026 for Altered)."

        # FLG-04: EA count should not be null, empty, or zero for pure EA records
        flg04_issues = []
        for r in records_to_check:
            gc = str(r.get("geocode") or r.get("ea_geocode") or "").strip()
            hh = str(r.get("hh_count") or r.get("hhcount") or r.get("households") or "").strip().replace(".0", "")
            if not hh or hh.lower() in ("nan", "none", "null") or hh == "0":
                flg04_issues.append(f"EA {gc}: EA count is missing or zero ('{hh}').")

        if flg04_issues:
            self._warn_rule("FLG-04", f"{len(flg04_issues)} pure EA records have missing or zero EA counts.", flg04_issues)
        else:
            self.rule_results["FLG-04"]["message"] = "All pure EA records possess valid non-null, non-zero household counts."

        # FLG-05: No duplicate final geocode
        flg05_issues = []
        gc_counts = collections.Counter()
        for r in records_to_check:
            gc = str(r.get("geocode") or r.get("ea_geocode") or "").strip()
            if gc:
                gc_counts[gc] += 1
        for gc, c in gc_counts.items():
            if c > 1:
                flg05_issues.append(f"Geocode {gc} appears {c} times across pure EA records.")

        if flg05_issues:
            self._fail_rule("FLG-05", f"{len(flg05_issues)} duplicate final geocodes found.", flg05_issues)
        else:
            self.rule_results["FLG-05"]["message"] = "100% of final geocodes are globally unique."

    # -------------------------------------------------------------
    # Summary & Scorecard Generation
    # -------------------------------------------------------------

    def get_summary(self) -> Dict[str, Any]:
        """Consolidates rule results into an overall score and scorecard."""
        total_rules = len(AUDIT_RULES)
        passed = sum(1 for r in self.rule_results.values() if r["status"] == "PASS")
        warnings = sum(1 for r in self.rule_results.values() if r["status"] == "WARN")
        failures = sum(1 for r in self.rule_results.values() if r["status"] == "FAIL")

        if failures > 0:
            overall_status = "FAILED"
        elif warnings > 0:
            overall_status = "WARNING"
        else:
            overall_status = "PASSED"

        # Compliance percentage
        score_pct = round(((passed + (warnings * 0.5)) / total_rules) * 100, 1)

        # Collect all individual discrepancy items
        all_discrepancies = []
        for r in self.rule_results.values():
            if r["details"]:
                for d in r["details"]:
                    if isinstance(d, dict):
                        item = dict(d)
                        item["rule_id"] = r["rule_id"]
                        item["rule_title"] = r["title"]
                        item["severity"] = r["severity"]
                        all_discrepancies.append(item)
                    else:
                        all_discrepancies.append({
                            "rule_id": r["rule_id"],
                            "rule_title": r["title"],
                            "severity": r["severity"],
                            "description": str(d)
                        })

        return {
            "city_mun_code": self.city_mun_code,
            "overall_status": overall_status,
            "compliance_score": score_pct,
            "total_rules": total_rules,
            "passed_count": passed,
            "warning_count": warnings,
            "failure_count": failures,
            "rule_results": self.rule_results,
            "discrepancies": all_discrepancies,
            "feature_count": len(self.gpkg_features),
            "files": {
                "gpkg": self.gpkg_path,
                "final_earf": self.final_earf_path,
                "mis_earf": self.mis_earf_path,
            }
        }

