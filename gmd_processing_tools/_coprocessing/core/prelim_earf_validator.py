# -*- coding: utf-8 -*-
"""
Prelim EARF Completeness & Consistency Validation Engine
Evaluates 13 completeness and consistency validation rules for Preliminary EARF
records and exports multi-sheet Excel reports adhering strictly to official specifications.
"""

import os
import re
from typing import Dict, List, Any, Optional, Set, Tuple

try:
    import openpyxl
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    from openpyxl.utils import get_column_letter
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False

# 13 Official Validation Rules
VALIDATION_SPECS = [
    {
        "id": "dup_ea",
        "tabs": "prelim EARF",
        "title": "Duplicate EA Records",
        "description": "EAs with duplicate attribute",
        "remarks": "create new sheet named values under validation_id",
        "headers": ["Geocode", "Prov", "Mun", "Brgy", "EA", "Remarks"],
        "severity": "ERROR",
    },
    {
        "id": "dup_new_ean",
        "tabs": "prelim EARF",
        "title": "Duplicate New EA Code",
        "description": "Multiple records share the same New EA Code where uniqueness is expected",
        "remarks": "create new sheet named values under validation_id",
        "headers": ["Geocode", "Prov", "Mun", "Brgy", "EA", "New EA Code", "Remarks"],
        "severity": "ERROR",
    },
    {
        "id": "earf_missing_geo",
        "tabs": "2024 POPCEN and Prelim EARF",
        "title": "Missing Geocode Match",
        "description": "Geocodes present in the 2024 POPCEN Estimated Number of Households that are not present in the Prelim EARF",
        "remarks": "create new sheet named values under validation_id",
        "headers": ["Geocode", "Prov", "Mun", "Brgy", "EA", "Remarks"],
        "severity": "WARNING",
    },
    {
        "id": "inv_ea_type",
        "tabs": "prelim EARF",
        "title": "Invalid EA Type",
        "description": "Entries with values other than Retained, Delineated, and Merged",
        "remarks": "create new sheet named values under validation_id",
        "headers": ["Geocode", "Prov", "Mun", "Brgy", "EA", "EA Type", "Remarks"],
        "severity": "ERROR",
    },
    {
        "id": "inv_hhcount",
        "tabs": "prelim EARF",
        "title": "Invalid Household Count",
        "description": "Household count is equal to building count",
        "remarks": "create new sheet named values under validation_id",
        "headers": ["Geocode", "Prov", "Mun", "Brgy", "EA", "Household Count", "Building Count", "Remarks"],
        "severity": "WARNING",
    },
    {
        "id": "inv_new_ean",
        "tabs": "prelim EARF",
        "title": "Invalid New EA Code",
        "description": "Entries with values other than the required 6-digit EA code",
        "remarks": "create new sheet named values under validation_id",
        "headers": ["Geocode", "Prov", "Mun", "Brgy", "EA", "New EA Code", "Remarks"],
        "severity": "ERROR",
    },
    {
        "id": "inv_sy",
        "tabs": "prelim EARF",
        "title": "Inconsistent EA Type and Source Year",
        "description": "Mismatch between EA Type and SY (Delineated/Merged = 2026, Retained = 2024)",
        "remarks": "create new sheet named values under validation_id",
        "headers": ["Geocode", "Prov", "Mun", "Brgy", "EA", "EA Type", "Source Year", "Remarks"],
        "severity": "WARNING",
    },
    {
        "id": "null_ea_count",
        "tabs": "prelim EARF",
        "title": "Missing EA Count",
        "description": "EA count should not be Null",
        "remarks": "create new sheet named values under validation_id",
        "headers": ["Geocode", "Prov", "Mun", "Brgy", "EA", "Number of EAs", "Remarks"],
        "severity": "WARNING",
    },
    {
        "id": "null_ea_type",
        "tabs": "prelim EARF",
        "title": "Missing EA Type",
        "description": "EA Type is blank or null",
        "remarks": "create new sheet named values under validation_id",
        "headers": ["Geocode", "Prov", "Mun", "Brgy", "EA", "EA Type", "Remarks"],
        "severity": "ERROR",
    },
    {
        "id": "null_hhcount",
        "tabs": "prelim EARF",
        "title": "Missing Household Count",
        "description": "Household count is blank or null",
        "remarks": "create new sheet named values under validation_id",
        "headers": ["Geocode", "Prov", "Mun", "Brgy", "EA", "Household Count", "Remarks"],
        "severity": "WARNING",
    },
    {
        "id": "null_new_ean",
        "tabs": "prelim EARF",
        "title": "Missing New EA Code",
        "description": "Delineated or Merged EAs with missing New EA Code values",
        "remarks": "create new sheet named values under validation_id",
        "headers": ["Geocode", "Prov", "Mun", "Brgy", "EA", "New EA Code", "Remarks"],
        "severity": "WARNING",
    },
    {
        "id": "null_remarks",
        "tabs": "prelim EARF",
        "title": "Missing Remarks",
        "description": "EAs subject to Delineation/Merging with a Retained EA Type but without corresponding remarks",
        "remarks": "create new sheet named values under validation_id",
        "headers": ["Geocode", "Prov", "Mun", "Brgy", "EA", "Remarks"],
        "severity": "WARNING",
    },
    {
        "id": "null_sy",
        "tabs": "prelim EARF",
        "title": "Missing Source Year",
        "description": "Source Year is blank or null",
        "remarks": "create new sheet named values under validation_id",
        "headers": ["Geocode", "Prov", "Mun", "Brgy", "EA", "Source Year", "Remarks"],
        "severity": "WARNING",
    },
]

VALIDATION_MAP = {rule["id"]: rule for rule in VALIDATION_SPECS}

VALID_EA_TYPES = {"RETAINED", "DELINEATED", "MERGED"}


def _clean_str(val: Any) -> str:
    """Helper to convert values to cleaned stripped string."""
    if val is None:
        return ""
    s = str(val).strip()
    if s.lower() in ("nan", "none", "null", "-"):
        return ""
    if s.endswith(".0"):
        s = s[:-2]
    return s


def _to_int_or_none(val: Any) -> Optional[int]:
    """Helper to convert string or numeric to int."""
    s = _clean_str(val)
    if not s:
        return None
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return None


def run_prelim_earf_validations(
    earf_records: List[Dict[str, Any]],
    popcen_records: Optional[List[Dict[str, Any]]] = None,
    progress_callback=None,
) -> Dict[str, Any]:
    """
    Executes all 13 completeness and consistency checks across Prelim EARF records.

    Returns a dict with:
      - 'summary': list of dicts with rule metadata and frequency
      - 'issues_by_rule': dict mapping validation_id -> list of violating row dicts
      - 'row_issues': dict mapping record_id -> list of triggered validation_ids
      - 'row_notes': dict mapping record_id -> combined notes string
      - 'row_status': dict mapping record_id -> 'PASS' | 'WARNING' | 'ERROR'
      - 'stats': overall counts of total, passed, warnings, errors
    """
    total = len(earf_records)
    issues_by_rule: Dict[str, List[Dict[str, Any]]] = {rule["id"]: [] for rule in VALIDATION_SPECS}
    row_issues: Dict[Any, List[str]] = {}
    row_notes: Dict[Any, List[str]] = {}
    row_status: Dict[Any, str] = {}

    popcen_geo_set: Set[str] = set()
    popcen_geo_map: Dict[str, Dict[str, Any]] = {}
    popcen_subject_delin_merg: Set[str] = set()

    if popcen_records:
        for p in popcen_records:
            gc = _clean_str(p.get("geocode"))
            ean = _clean_str(p.get("ean"))
            if gc and ean != "000000":
                popcen_geo_set.add(gc)
                popcen_geo_map[gc] = p

                is_delin = _clean_str(p.get("ea_subject_for_delineation")) in ("1", "yes", "true", "y")
                is_merg = _clean_str(p.get("ea_subject_for_merging")) in ("1", "yes", "true", "y")
                if is_delin or is_merg:
                    popcen_subject_delin_merg.add(gc)

    # 1. Uniqueness pre-scans
    # dup_ea: geocode occurrences across all pure EA rows
    geocode_counts: Dict[str, int] = {}
    # dup_new_ean: (prov, mun, brgy, new_ean) occurrences
    brgy_new_ean_counts: Dict[Tuple[str, str, str, str], int] = {}

    for r in earf_records:
        rec_id = r.get("id")
        row_issues[rec_id] = []
        row_notes[rec_id] = []
        row_status[rec_id] = "PASS"

        ea = _clean_str(r.get("ea"))
        gc = _clean_str(r.get("geocode"))
        prov = _clean_str(r.get("prov"))
        mun = _clean_str(r.get("mun"))
        brgy = _clean_str(r.get("brgy"))
        new_ean = _clean_str(r.get("new_enumeration_area_code"))

        # Skip barangay header rows from EA count maps
        if ea and ea != "000000":
            if gc:
                geocode_counts[gc] = geocode_counts.get(gc, 0) + 1
            if new_ean and prov and mun and brgy:
                key = (prov, mun, brgy, new_ean)
                brgy_new_ean_counts[key] = brgy_new_ean_counts.get(key, 0) + 1

    # 2. Iterate each EARF record and evaluate rules
    earf_pure_geocodes: Set[str] = set()

    for idx, r in enumerate(earf_records):
        rec_id = r.get("id")
        gc = _clean_str(r.get("geocode"))
        prov = _clean_str(r.get("prov"))
        mun = _clean_str(r.get("mun"))
        brgy = _clean_str(r.get("brgy"))
        ea = _clean_str(r.get("ea"))
        ea_type_raw = _clean_str(r.get("ea_type"))
        ea_type_norm = ea_type_raw.upper()
        new_ean = _clean_str(r.get("new_enumeration_area_code"))
        source_year = _clean_str(r.get("source_year"))
        num_eas = _clean_str(r.get("number_of_eas"))
        remarks = _clean_str(r.get("remarks"))

        hh_count = _to_int_or_none(r.get("household_count"))
        if hh_count is None:
            hh_count = _to_int_or_none(r.get("number_of_households"))

        bldg_count = _to_int_or_none(r.get("building_count"))
        if bldg_count is None:
            bldg_count = _to_int_or_none(r.get("number_of_buildings"))

        is_brgy_header = (ea == "000000") or (not ea and _clean_str(r.get("areaname")).upper().startswith("BARANGAY"))

        if not is_brgy_header and gc:
            earf_pure_geocodes.add(gc)

        def _flag(rule_id: str, remark_msg: str):
            rule_spec = VALIDATION_MAP[rule_id]
            sev = rule_spec["severity"]
            row_issues[rec_id].append(rule_id)
            row_notes[rec_id].append(f"[{rule_id}] {remark_msg}")
            if sev == "ERROR":
                row_status[rec_id] = "ERROR"
            elif sev == "WARNING" and row_status[rec_id] != "ERROR":
                row_status[rec_id] = "WARNING"

            # Create entry for rule worksheet
            row_copy = dict(r)
            row_copy["issue_remarks"] = remark_msg
            issues_by_rule[rule_id].append(row_copy)

        # Rule evaluation for EA-level rows
        if not is_brgy_header:
            # Rule 1: dup_ea (Duplicate EA Records)
            if gc and geocode_counts.get(gc, 0) > 1:
                _flag("dup_ea", f"Geocode '{gc}' appears {geocode_counts[gc]} times in EARF")

            # Rule 2: dup_new_ean (Duplicate New EA Code within same Barangay)
            # Allow legitimate duplicates only if both are MERGED
            if new_ean and prov and mun and brgy:
                key = (prov, mun, brgy, new_ean)
                if brgy_new_ean_counts.get(key, 0) > 1:
                    # In merged cases, multiple EAs can point to the same target New EA code.
                    # Flag if any of the sharing records is NOT Merged, or flag for audit review.
                    if ea_type_norm != "MERGED":
                        _flag("dup_new_ean", f"New EA Code '{new_ean}' shared by multiple records in Brgy {brgy}")

            # Rule 4: inv_ea_type (Invalid EA Type)
            if ea_type_raw and ea_type_norm not in VALID_EA_TYPES:
                _flag("inv_ea_type", f"Invalid EA Type '{ea_type_raw}' (expected Retained, Delineated, or Merged)")

            # Rule 5: inv_hhcount (Invalid Household Count: HH == Bldg)
            if hh_count is not None and bldg_count is not None and hh_count > 0 and hh_count == bldg_count:
                _flag("inv_hhcount", f"Household count ({hh_count}) equals building count ({bldg_count})")

            # Rule 6: inv_new_ean (Invalid New EA Code format: must be 6 digits if present)
            if new_ean and (not new_ean.isdigit() or len(new_ean) != 6):
                _flag("inv_new_ean", f"Invalid New EA Code '{new_ean}' (expected 6-digit numeric string)")

            # Rule 7: inv_sy (Inconsistent EA Type and Source Year)
            if ea_type_norm in ("DELINEATED", "MERGED") and source_year and source_year != "2026":
                _flag("inv_sy", f"EA Type is '{ea_type_raw}' but Source Year is '{source_year}' (expected 2026)")
            elif ea_type_norm == "RETAINED" and source_year and source_year != "2024":
                _flag("inv_sy", f"EA Type is 'RETAINED' but Source Year is '{source_year}' (expected 2024)")

            # Rule 8: null_ea_count (Missing EA Count)
            if not num_eas:
                _flag("null_ea_count", "Missing EA count (Number of EAs is blank or null)")

            # Rule 9: null_ea_type (Missing EA Type)
            if not ea_type_raw:
                _flag("null_ea_type", "Missing EA Type (EA Type is blank or null)")

            # Rule 10: null_hhcount (Missing Household Count)
            if hh_count is None:
                _flag("null_hhcount", "Missing Household Count")

            # Rule 11: null_new_ean (Missing New EA Code for Delineated EAs)
            if ea_type_norm == "DELINEATED" and not new_ean:
                _flag("null_new_ean", "Delineated EA is missing New EA Code (2026)")

            # Rule 12: null_remarks (Missing Remarks for EAs subject to delineation/merging but Retained)
            if ea_type_norm == "RETAINED" and gc in popcen_subject_delin_merg and not remarks:
                _flag("null_remarks", "EA marked in POPCEN for delineation/merging retained without remarks")

            # Rule 13: null_sy (Missing Source Year)
            if not source_year:
                _flag("null_sy", "Missing Source Year (Source Year is blank or null)")

        if progress_callback and idx % 1000 == 0:
            progress_callback(int((idx / total) * 90), f"Evaluating validation rules ({idx:,} / {total:,})...")

    # Rule 3: earf_missing_geo (POPCEN geocodes missing in EARF)
    if popcen_records:
        for p in popcen_records:
            gc = _clean_str(p.get("geocode"))
            ean = _clean_str(p.get("ean"))
            if gc and ean != "000000" and gc not in earf_pure_geocodes:
                missing_row = {
                    "geocode": gc,
                    "prov": _clean_str(p.get("province_code") or p.get("prov")),
                    "mun": _clean_str(p.get("city_mun_code") or p.get("mun")),
                    "brgy": _clean_str(p.get("barangay_code") or p.get("brgy")),
                    "ea": ean,
                    "issue_remarks": "Geocode present in 2024 POPCEN but absent in Preliminary EARF",
                }
                issues_by_rule["earf_missing_geo"].append(missing_row)

    # 3. Construct Summary Table
    summary_list = []
    total_warnings = 0
    total_errors = 0
    total_passed = 0

    for rule in VALIDATION_SPECS:
        r_id = rule["id"]
        freq = len(issues_by_rule[r_id])
        summary_list.append({
            "validation_id": r_id,
            "tabs": rule["tabs"],
            "title": rule["title"],
            "description": rule["description"],
            "frequency": freq,
            "remarks": rule["remarks"],
        })

    for rec_id, st in row_status.items():
        if st == "ERROR":
            total_errors += 1
        elif st == "WARNING":
            total_warnings += 1
        else:
            total_passed += 1

    return {
        "summary": summary_list,
        "issues_by_rule": issues_by_rule,
        "row_issues": row_issues,
        "row_notes": {k: "; ".join(v) for k, v in row_notes.items()},
        "row_status": row_status,
        "stats": {
            "total_checked": total,
            "passed": total_passed,
            "warnings": total_warnings,
            "errors": total_errors,
            "pass_rate": round((total_passed / total) * 100, 1) if total > 0 else 0.0,
        },
    }


def generate_validation_excel_report(output_file: str, validation_results: Dict[str, Any]) -> str:
    """
    Creates the official multi-sheet Excel validation report matching the exact structure
    of the Google Spreadsheet (Sheet 1 Summary + Sheets 2-14 per validation_id).
    """
    if not HAS_OPENPYXL:
        raise RuntimeError("openpyxl is required to generate the Excel validation report.")

    wb = openpyxl.Workbook()
    wb.remove(wb.active)  # Remove default blank sheet

    summary_data = validation_results.get("summary", [])
    issues_by_rule = validation_results.get("issues_by_rule", {})

    font_title = Font(name="Arial", size=14, bold=True)
    font_header = Font(name="Arial", size=10, bold=True)
    font_data = Font(name="Arial", size=10, bold=False)

    align_center = Alignment(horizontal="center", vertical="center")
    align_left = Alignment(horizontal="left", vertical="center")
    align_right = Alignment(horizontal="right", vertical="center")

    thin_border_side = Side(border_style="thin", color="CBD5E1")
    cell_border = Border(left=thin_border_side, right=thin_border_side, top=thin_border_side, bottom=thin_border_side)
    header_border = Border(left=thin_border_side, right=thin_border_side, top=thin_border_side, bottom=Side(border_style="medium", color="0F172A"))

    fill_header = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")
    fill_highlight = PatternFill(start_color="FEF3C7", end_color="FEF3C7", fill_type="solid")

    # =========================================================================
    # 1. Master Sheet: 'Summary'
    # =========================================================================
    ws_summary = wb.create_sheet(title="Summary")
    ws_summary.views.sheetView[0].showGridLines = True

    # Row 1: Title
    cell_title = ws_summary.cell(row=1, column=1, value="List of Inconsistencies")
    cell_title.font = font_title

    # Row 3: Headers starting at Column B (Col 2)
    summary_headers = ["validation_id", "tabs", "title", "description", "frequency", "remarks"]
    for c_idx, h_text in enumerate(summary_headers, start=2):
        c = ws_summary.cell(row=3, column=c_idx, value=h_text)
        c.font = font_header
        c.alignment = align_center if h_text in ("validation_id", "frequency") else align_left
        c.border = header_border
        c.fill = fill_header

    # Row 4+: Data
    for r_idx, row_dict in enumerate(summary_data, start=4):
        ws_summary.cell(row=r_idx, column=2, value=row_dict.get("validation_id", "")).alignment = align_center
        ws_summary.cell(row=r_idx, column=3, value=row_dict.get("tabs", "")).alignment = align_left
        ws_summary.cell(row=r_idx, column=4, value=row_dict.get("title", "")).alignment = align_left
        ws_summary.cell(row=r_idx, column=5, value=row_dict.get("description", "")).alignment = align_left

        freq_cell = ws_summary.cell(row=r_idx, column=6, value=row_dict.get("frequency", 0))
        freq_cell.alignment = align_center
        if row_dict.get("frequency", 0) > 0:
            freq_cell.fill = fill_highlight

        ws_summary.cell(row=r_idx, column=7, value=row_dict.get("remarks", "")).alignment = align_left

        for c_idx in range(2, 8):
            cell = ws_summary.cell(row=r_idx, column=c_idx)
            cell.font = font_data
            cell.border = cell_border

    # Set column widths
    ws_summary.column_dimensions["A"].width = 3.0
    ws_summary.column_dimensions["B"].width = 18.0
    ws_summary.column_dimensions["C"].width = 25.0
    ws_summary.column_dimensions["D"].width = 32.0
    ws_summary.column_dimensions["E"].width = 55.0
    ws_summary.column_dimensions["F"].width = 12.0
    ws_summary.column_dimensions["G"].width = 45.0

    # =========================================================================
    # 2. Sheets 2 through 14: Dedicated Sheet per validation_id
    # =========================================================================
    for rule in VALIDATION_SPECS:
        rule_id = rule["id"]
        title_text = rule["title"]
        headers = rule["headers"]
        rows_to_write = issues_by_rule.get(rule_id, [])

        ws = wb.create_sheet(title=rule_id)
        ws.views.sheetView[0].showGridLines = True

        # Row 1: Sheet Title
        cell_t = ws.cell(row=1, column=1, value=title_text)
        cell_t.font = font_title

        # Row 3: Column Headers starting at Column B
        for c_idx, h_name in enumerate(headers, start=2):
            c = ws.cell(row=3, column=c_idx, value=h_name)
            c.font = font_header
            c.alignment = align_center
            c.border = header_border
            c.fill = fill_header

        # Row 4+: Violating Records
        for r_idx, item in enumerate(rows_to_write, start=4):
            for c_idx, h_name in enumerate(headers, start=2):
                val = ""
                h_low = h_name.strip().lower()
                if h_low == "geocode":
                    val = item.get("geocode", "")
                elif h_low == "prov":
                    val = item.get("prov", "")
                elif h_low == "mun":
                    val = item.get("mun", "")
                elif h_low == "brgy":
                    val = item.get("brgy", "")
                elif h_low == "ea":
                    val = item.get("ea", "")
                elif "new ea" in h_low:
                    val = item.get("new_enumeration_area_code", "")
                elif "ea type" in h_low:
                    val = item.get("ea_type", "")
                elif "household" in h_low:
                    val = item.get("household_count") or item.get("number_of_households") or ""
                elif "building" in h_low:
                    val = item.get("building_count") or item.get("number_of_buildings") or ""
                elif "source year" in h_low:
                    val = item.get("source_year", "")
                elif "number of eas" in h_low:
                    val = item.get("number_of_eas", "")
                elif h_low == "remarks":
                    val = item.get("issue_remarks") or item.get("remarks", "")

                cell = ws.cell(row=r_idx, column=c_idx, value=val)
                cell.font = font_data
                cell.border = cell_border
                if any(k in h_low for k in ("count", "households", "buildings")):
                    cell.alignment = align_right
                elif any(k in h_low for k in ("remarks", "name")):
                    cell.alignment = align_left
                else:
                    cell.alignment = align_center

        # Format column dimensions
        ws.column_dimensions["A"].width = 3.0
        for c_idx, h_name in enumerate(headers, start=2):
            col_letter = get_column_letter(c_idx)
            if "geocode" in h_name.lower():
                ws.column_dimensions[col_letter].width = 18.0
            elif "remarks" in h_name.lower():
                ws.column_dimensions[col_letter].width = 45.0
            elif any(k in h_name.lower() for k in ("count", "year", "type", "code", "eas")):
                ws.column_dimensions[col_letter].width = 16.0
            else:
                ws.column_dimensions[col_letter].width = 10.0

    out_dir = os.path.dirname(os.path.abspath(output_file))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    wb.save(output_file)
    return output_file
