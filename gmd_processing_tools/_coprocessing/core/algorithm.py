# -*- coding: utf-8 -*-
"""
CO Processing - EARF Geocode Matching
======================================
QGIS 3.28+ / 3.40+ Processing Algorithm.

Purpose
-------
Matches three PSO preliminary EARF-related sources on a common GEOCODE key:

  1. 2024 POPCEN Estimated Number of Households (Excel)
       geocode = province_code + city_mun_code + barangay_code + ean
  2. Processed EA delineation/merging output (Multiple GeoPackage layers/files with geometry)
       geocode = Prov + Mun + Brgy + EA
  3. Preliminary EARF (Multiple Excel files)
       geocode = Prov + Mun + Brgy + EA

All sources are outer-joined on GEOCODE: every geocode appearing in ANY of
the sources is retained in the output, and a MATCH_STATUS field records
which sources contributed to each row:
  - MATCHED_ALL: Present in POPCEN, GeoPackage, and EARF
  - UNMATCHED_POPCEN_GPKG: Present in POPCEN and GeoPackage only
  - UNMATCHED_POPCEN_EARF: Present in POPCEN and EARF only
  - UNMATCHED_GPKG_EARF: Present in GeoPackage and EARF only
  - UNMATCHED_POPCEN: Present in POPCEN only
  - UNMATCHED_GPKG: Present in GeoPackage only
  - UNMATCHED_EARF: Present in EARF only

Output is a single GeoPackage layer (geometries inherited and combined from the
EA GeoPackage layers where available) plus an optional plain CSV mirror of the
same table for easy sharing or import into a web viewer.
"""

import os
import re
from qgis.PyQt.QtCore import QCoreApplication, QVariant

try:
    from qgis.PyQt.QtCore import QMetaType
    FIELD_STRING_TYPE = QMetaType.Type.QString
except (ImportError, AttributeError):
    FIELD_STRING_TYPE = QVariant.String

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsMapLayer,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFile,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterMultipleLayers,
    QgsProcessingParameterString,
    QgsVectorLayer,
    QgsWkbTypes,
)


try:
    from .excel_utils import patch_openpyxl
except (ImportError, ValueError):
    try:
        from excel_utils import patch_openpyxl
    except ImportError:
        def patch_openpyxl():
            pass

patch_openpyxl()

# --------------------------------------------------------------------------
# Field-name configuration
# Adjust these if column headers in source files differ.
# --------------------------------------------------------------------------
POPCEN_FIELDS = {
    "province": "province_code",
    "city_mun": "city_mun_code",
    "barangay": "barangay_code",
    "ea": "ean",
}

GPKG_EARF_FIELDS = {
    "province": "Prov",
    "city_mun": "Mun",
    "barangay": "Brgy",
    "ea": "EA",
}

# Zero-padding widths applied to each code segment before concatenation (14-digit standard).
CODE_WIDTHS = {
    "province": 3,
    "city_mun": 2,
    "barangay": 3,
    "ea": 6,
}


def _get_popcen_db_path() -> str:
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) if os.path.basename(os.path.dirname(os.path.abspath(__file__))) in ("core", "db", "ui") else os.path.dirname(os.path.abspath(__file__))
    for candidate in [
        os.path.join(base_dir, "secured", "popcen_database.db"),
        os.path.join(base_dir, "data", "popcen_database.db"),
        os.path.join(base_dir, "popcen_database.db"),
    ]:
        if os.path.exists(candidate):
            return candidate
    return os.path.join(base_dir, "secured", "popcen_database.db")


def _get_earf_db_path() -> str:
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) if os.path.basename(os.path.dirname(os.path.abspath(__file__))) in ("core", "db", "ui") else os.path.dirname(os.path.abspath(__file__))
    for candidate in [
        os.path.join(base_dir, "secured", "earf_database.db"),
        os.path.join(base_dir, "data", "earf_database.db"),
        os.path.join(base_dir, "earf_database.db"),
        os.path.join(base_dir, "secured", "popcen_database.db"),
        os.path.join(base_dir, "data", "popcen_database.db"),
        os.path.join(base_dir, "popcen_database.db"),
    ]:
        if os.path.exists(candidate):
            return candidate
    return os.path.join(base_dir, "secured", "earf_database.db")


def _clean_field_val(val):
    """Clean up raw string / numeric representation from Excel or layer attributes."""
    if val is None:
        return ""
    s = str(val).strip()
    if s.lower() in ("nan", "none", "null"):
        return ""
    # Strip trailing ".0" added to float-formatted integers
    if s.endswith(".0"):
        s = s[:-2].strip()
    return s


def to_whole_number_str(val) -> str:
    """
    Formats numeric / float strings or numbers as whole numbers without trailing decimals
    (e.g., 2024.0 -> '2024', '1004.0' -> '1004', '72.0' -> '72').
    Preserves non-numeric strings, empty values as '', and retains intact integer strings with leading zeros.
    """
    if val is None:
        return ""
    if isinstance(val, float) and (val != val or str(val).lower() == "nan"):
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


def build_geocode(row_dict, field_map=None):
    """
    Concatenate zero-padded code segments into one 14-digit geocode string,
    or retrieve pre-existing geocode attribute.
    Returns empty string if all segments are blank.
    """
    if field_map is None:
        field_map = GPKG_EARF_FIELDS

    # Lowercase lookup dictionary to support case-insensitive headers
    lower_dict = {str(k).strip().lower(): v for k, v in row_dict.items() if k is not None}

    # 1. Direct geocode check: If a valid numeric geocode attribute is already present in the row
    # (e.g. from uploaded GeoPackage or POPCEN/EARF), preserve it directly.
    for direct_key in ("geocode", "psgc_ea", "ea_geocode", "ean_code"):
        if direct_key in lower_dict:
            cleaned_direct = _clean_field_val(lower_dict[direct_key])
            if cleaned_direct and len(cleaned_direct) >= 9 and cleaned_direct.replace(".0", "").isdigit():
                return cleaned_direct.replace(".0", "")

    fallback_keys = {
        "province": ["province_code", "prov_code", "prov", "p_code", "province"],
        "city_mun": ["city_mun_code", "mun_code", "mun", "m_code", "city_mun", "city"],
        "barangay": ["barangay_code", "brgy_code", "brgy", "bgy", "b_code", "barangay"],
        "ea": ["ean", "ea", "ea_code", "ean_code"],
    }

    parts = []
    has_any = False
    has_all_numeric_segments = True
    for seg in ("province", "city_mun", "barangay", "ea"):
        target_name = field_map.get(seg, "")
        raw = row_dict.get(target_name)
        if raw is None and target_name:
            raw = lower_dict.get(target_name.strip().lower())

        if raw is None or _clean_field_val(raw) == "":
            for fb_key in fallback_keys.get(seg, []):
                if fb_key in lower_dict:
                    val_candidate = lower_dict[fb_key]
                    if _clean_field_val(val_candidate):
                        raw = val_candidate
                        break

        cleaned = _clean_field_val(raw)
        if cleaned:
            has_any = True
            # Segment must be numeric to be valid code segment
            cleaned_digits = cleaned.replace(".0", "")
            if not cleaned_digits.isdigit():
                has_all_numeric_segments = False
        else:
            has_all_numeric_segments = False
        parts.append(cleaned.zfill(CODE_WIDTHS[seg]))

    # When all 4 standard PSGC hierarchy segments (Prov, Mun, Brgy, and base EAN/EA) are explicitly
    # available as numeric codes (e.g. Prov=806, Mun=01, Brgy=051, EAN=002000), construct geocode.
    if has_all_numeric_segments:
        return "".join(parts)

    for direct_key in ("geocode", "psgc_ea", "ea_geocode", "ean_code"):
        if direct_key in lower_dict:
            cleaned_direct = _clean_field_val(lower_dict[direct_key])
            if cleaned_direct and len(cleaned_direct) >= 9:
                return cleaned_direct

    if not has_any:
        return ""

    return "".join(parts)


def read_earf_table(file_path, sheet_name=0):
    """
    Reads a Preliminary EARF Excel file and auto-detects multi-tier header rows.
    Supports row headers like 'Prov', 'Mun', 'Brgy', 'EA', skips metadata banner rows
    and numeric index rows (e.g. '(2)', '(3)'...).
    """
    import pandas as pd
    if not file_path or not os.path.exists(file_path):
        return pd.DataFrame()

    sheet_arg = sheet_name.strip() if isinstance(sheet_name, str) and sheet_name.strip() else 0
    patch_openpyxl()
    df_raw = pd.read_excel(file_path, sheet_name=sheet_arg, header=None, nrows=20, dtype=str, engine="openpyxl")

    header_idx = None
    for idx, row in df_raw.iterrows():
        s = " ".join([str(v).lower() for v in row if pd.notna(v)])
        if ("prov" in s or "province" in s) and ("brgy" in s or "barangay" in s) and "ea" in s:
            header_idx = idx
            break

    if header_idx is None:
        header_idx = 0

    main_row = df_raw.iloc[header_idx]
    cols = []
    for c_i in range(len(main_row)):
        m_val = str(main_row[c_i]).strip().replace("\n", " ") if pd.notna(main_row[c_i]) else ""
        if m_val and m_val.lower() != "nan":
            cols.append(m_val)
        else:
            cols.append(f"Col_{c_i + 1}")

    skip_rows = header_idx + 1
    if skip_rows < len(df_raw):
        next_row = df_raw.iloc[skip_rows]
        if any(str(v).strip().startswith("(") and str(v).strip().endswith(")") for v in next_row if pd.notna(v)):
            skip_rows += 1

    patch_openpyxl()
    df = pd.read_excel(file_path, sheet_name=sheet_arg, skiprows=skip_rows, header=None, dtype=str, engine="openpyxl")
    num_cols = min(len(cols), len(df.columns))
    df = df.iloc[:, :num_cols]
    df.columns = cols[:num_cols]

    # Calculate geocode
    df["geocode"] = df.apply(lambda r: build_geocode(r.to_dict(), GPKG_EARF_FIELDS), axis=1)

    # Map common alias headers for 2026 counts to canonical columns
    for col in list(df.columns):
        c_low = str(col).strip().lower().replace("_", " ")
        if ("household" in c_low and "count" in c_low) or c_low in ("hh count", "hhcount"):
            if "household_count" not in df.columns:
                df["household_count"] = df[col]
        elif ("building" in c_low and "count" in c_low) or c_low in ("bldg count", "bldgcount"):
            if "building_count" not in df.columns:
                df["building_count"] = df[col]
        elif "ea type" in c_low or c_low == "type":
            if "ea_type" not in df.columns:
                df["ea_type"] = df[col]
        elif "source year" in c_low or c_low in ("source_year", "sy", "source yr", "year"):
            if "source_year" not in df.columns:
                df["source_year"] = df[col]

    # If EA Type is Retained, set source_year to 2024, else 2026
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

    return df


def read_popcen_table(file_path, sheet_name=0):
    """
    Reads a POPCEN Excel or CSV file and auto-detects multi-tier header rows
    (e.g., category groups on Row 6 + column names on Row 7).
    """
    import pandas as pd
    sheet_arg = sheet_name.strip() if isinstance(sheet_name, str) and sheet_name.strip() else 0
    if file_path.lower().endswith(".csv"):
        df_raw = pd.read_csv(file_path, header=None, nrows=15, dtype=str)
    else:
        patch_openpyxl()
        df_raw = pd.read_excel(file_path, sheet_name=sheet_arg, header=None, nrows=15, dtype=str, engine="openpyxl")

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
        patch_openpyxl()
        df = pd.read_excel(file_path, sheet_name=sheet_arg, skiprows=header_idx + 1, header=None, dtype=str, engine="openpyxl")

    num_cols = min(len(cols), len(df.columns))
    df = df.iloc[:, :num_cols]
    df.columns = cols[:num_cols]
    return df


def exclude_non_ea_levels(df):
    """
    Removes rows whose Geographic Level is Philippines, Region, Province,
    City/Municipality, or Barangay so they are excluded from matching.
    Returns (filtered_df, excluded_count). No-op if no level column exists.
    """
    lvl_col = next((c for c in df.columns if "level" in str(c).lower()), None)
    if lvl_col is None:
        return df, 0
    pattern = r"^\s*[0-4]\s*[-\u2013]|philippines|region|province|huc|city|municipality|barangay"
    mask = df[lvl_col].astype(str).str.contains(pattern, case=False, regex=True, na=False)
    return df[~mask].copy(), int(mask.sum())


def is_version_dir(dir_name: str) -> bool:
    """
    Returns True if a directory name matches version/archive folder naming conventions.
    Matches folder names like 'version', 'versions', 'version_1', 'version 1', 'v1', 'v2', etc.
    """
    name = dir_name.strip().lower()
    if not name:
        return False
    if "version" in name:
        return True
    if re.match(r"^(?:v|ver)[-_ ]?\d+(?:\.\d+)*$", name):
        return True
    return False


class CoProcessingEarfMatch(QgsProcessingAlgorithm):
    """
    QGIS Processing Algorithm that matches POPCEN, multiple EA Geopackages,
    and multiple preliminary EARF sources on common GEOCODE.
    """

    INPUT_POPCEN = "INPUT_POPCEN"
    POPCEN_SHEET = "POPCEN_SHEET"
    INPUT_GPKG = "INPUT_GPKG"
    INPUT_EARF = "INPUT_EARF"
    EARF_SHEET = "EARF_SHEET"
    OUTPUT = "OUTPUT"
    OUTPUT_CSV = "OUTPUT_CSV"

    def tr(self, string):
        return QCoreApplication.translate("CoProcessingEarfMatch", string)

    def createInstance(self):
        return CoProcessingEarfMatch()

    def name(self):
        return "co_processing_earf_match"

    def displayName(self):
        return self.tr("CO Processing - Match POPCEN / EA / EARF on geocode")

    def group(self):
        return self.tr("EA Delineation Tools")

    def groupId(self):
        return "ea_delineation_tools"

    def shortHelpString(self):
        return self.tr(
            "Builds a GEOCODE key on each of the inputs and outer-joins them, "
            "keeping unmatched geocodes with a MATCH_STATUS flag instead of dropping them.\n\n"
            "Inputs:\n"
            "  1. 2024 POPCEN Estimated Number of Households (Excel)\n"
            "  2. Processed EA GeoPackage layer(s) (Supports multiple layers or GeoPackage files with geometry)\n"
            "  3. Preliminary EARF Excel file(s) (Supports multiple .xlsx files)\n\n"
            "Geocodes are built by concatenating zero-padded province (2), municipality (2), "
            "barangay (3), and EA (4) codes into an 11-digit PSGC key.\n\n"
            "Output is a GeoPackage layer (geometries inherited and combined from the EA layers) "
            "and an optional CSV mirror table."
        )

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterFile(
                self.INPUT_POPCEN,
                self.tr("2024 POPCEN Estimated Number of Households (xlsx, blank = use Local Database)"),
                behavior=QgsProcessingParameterFile.File,
                extension="xlsx",
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterString(
                self.POPCEN_SHEET,
                self.tr("POPCEN sheet name (blank = first sheet)"),
                optional=True,
                defaultValue="",
            )
        )
        self.addParameter(
            QgsProcessingParameterMultipleLayers(
                self.INPUT_GPKG,
                self.tr("Processed EA GeoPackage layer(s)"),
                layerType=QgsProcessing.TypeVectorPolygon,
            )
        )
        self.addParameter(
            QgsProcessingParameterMultipleLayers(
                self.INPUT_EARF,
                self.tr("Preliminary EARF Excel file(s) (xlsx)"),
                layerType=QgsProcessing.TypeFile,
            )
        )
        self.addParameter(
            QgsProcessingParameterString(
                self.EARF_SHEET,
                self.tr("Preliminary EARF sheet name (blank = first sheet)"),
                optional=True,
                defaultValue="",
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT,
                self.tr("Matched output layer (GeoPackage)"),
            )
        )
        self.addParameter(
            QgsProcessingParameterFileDestination(
                self.OUTPUT_CSV,
                self.tr("Matched output table (CSV, for web viewer)"),
                fileFilter="CSV files (*.csv)",
                optional=True,
            )
        )

    def _resolve_files(self, parameters, param_name, context):
        """Extract a list of file paths from parameter, handling single or multiple inputs."""
        try:
            files = self.parameterAsFileList(parameters, param_name, context)
        except Exception:
            files = []
        if not files:
            raw = parameters.get(param_name)
            if isinstance(raw, (list, tuple)):
                files = list(raw)
            elif isinstance(raw, str) and raw.strip():
                files = [p.strip().strip('"').strip("'") for p in raw.replace("\r", "\n").replace(";", "\n").split("\n") if p.strip()]
        resolved = []
        for f in files:
            if isinstance(f, str):
                for part in f.replace("\r", "\n").replace(";", "\n").split("\n"):
                    clean = part.strip().strip('"').strip("'")
                    if clean:
                        if os.path.isdir(clean):
                            for root, dirs, fnames in os.walk(clean):
                                dirs[:] = [d for d in dirs if not is_version_dir(d)]
                                for fn in sorted(fnames):
                                    if fn.startswith("~$"):
                                        continue
                                    ext = os.path.splitext(fn)[1].lower()
                                    if ext in (".xlsx", ".xls") and "_earf" in fn.lower():
                                        full_p = os.path.normpath(os.path.join(root, fn))
                                        if full_p not in resolved:
                                            resolved.append(full_p)
                        elif os.path.isfile(clean) and clean not in resolved:
                            resolved.append(clean)
        return resolved

    def _resolve_layers(self, parameters, param_name, context):
        """Extract a list of valid polygon QgsVectorLayer objects from parameter."""
        try:
            layers = self.parameterAsLayerList(parameters, param_name, context)
        except Exception:
            layers = []
        valid_layers = [lyr for lyr in layers if lyr is not None and lyr.isValid() and is_polygon_layer(lyr)]
        if valid_layers:
            return valid_layers

        raw = parameters.get(param_name)
        candidates = []
        if isinstance(raw, (list, tuple)):
            candidates = list(raw)
        elif isinstance(raw, str) and raw.strip():
            candidates = [p.strip().strip('"').strip("'") for p in raw.replace("\r", "\n").replace(";", "\n").split("\n") if p.strip()]
        elif isinstance(raw, QgsMapLayer):
            candidates = [raw]

        resolved = []
        for item in candidates:
            if isinstance(item, QgsMapLayer):
                if item.isValid() and is_polygon_layer(item) and item not in resolved:
                    resolved.append(item)
            elif isinstance(item, str):
                p = item.strip().strip('"').strip("'")
                if os.path.isdir(p):
                    for root, dirs, files in os.walk(p):
                        dirs[:] = [d for d in dirs if not is_version_dir(d)]
                        rel_path = os.path.relpath(root, p)
                        if rel_path != "." and any(is_version_dir(part) for part in rel_path.replace("\\", "/").split("/")):
                            continue
                        for f in sorted(files):
                            if f.lower().endswith(('.gpkg', '.sqlite')):
                                fp = os.path.normpath(os.path.join(root, f))
                                base_name = os.path.splitext(f)[0]
                                try:
                                    from ..db.database import get_gpkg_polygon_layer_info
                                    has_poly, poly_tables, _ = get_gpkg_polygon_layer_info(fp)
                                except Exception:
                                    try:
                                        from COProcessing.db.database import get_gpkg_polygon_layer_info
                                        has_poly, poly_tables, _ = get_gpkg_polygon_layer_info(fp)
                                    except Exception:
                                        try:
                                            from database import get_gpkg_polygon_layer_info
                                            has_poly, poly_tables, _ = get_gpkg_polygon_layer_info(fp)
                                        except Exception:
                                            has_poly, poly_tables = True, []
                                if poly_tables:
                                    for tbl in poly_tables:
                                        vl = QgsVectorLayer(f"{fp}|layername={tbl}", f"{base_name} - {tbl}", "ogr")
                                        if vl.isValid() and is_polygon_layer(vl) and vl not in resolved:
                                            resolved.append(vl)
                                elif has_poly:
                                    vl = QgsVectorLayer(fp, base_name, "ogr")
                                    if vl.isValid() and is_polygon_layer(vl) and vl not in resolved:
                                        resolved.append(vl)
                elif os.path.exists(p):
                    base_name = os.path.splitext(os.path.basename(p))[0]
                    try:
                        from ..db.database import get_gpkg_polygon_layer_info
                        has_poly, poly_tables, _ = get_gpkg_polygon_layer_info(p)
                    except Exception:
                        try:
                            from COProcessing.db.database import get_gpkg_polygon_layer_info
                            has_poly, poly_tables, _ = get_gpkg_polygon_layer_info(p)
                        except Exception:
                            try:
                                from database import get_gpkg_polygon_layer_info
                                doubts = get_gpkg_polygon_layer_info(p)
                                has_poly, poly_tables, _ = doubts
                            except Exception:
                                has_poly, poly_tables = True, []
                    if poly_tables:
                        for tbl in poly_tables:
                            vl = QgsVectorLayer(f"{p}|layername={tbl}", f"{base_name} - {tbl}", "ogr")
                            if vl.isValid() and is_polygon_layer(vl) and vl not in resolved:
                                resolved.append(vl)
                    elif has_poly:
                        vl = QgsVectorLayer(p, base_name, "ogr")
                        if vl.isValid() and is_polygon_layer(vl) and vl not in resolved:
                            resolved.append(vl)
        return resolved

    def processAlgorithm(self, parameters, context, feedback):
        try:
            import pandas as pd
            import openpyxl  # noqa: F401
        except ImportError:
            raise QgsProcessingException(
                "This tool requires the 'pandas' and 'openpyxl' packages in "
                "the QGIS Python environment. Install via the OSGeo4W shell: "
                "python -m pip install pandas openpyxl"
            )

        popcen_path = self.parameterAsFile(parameters, self.INPUT_POPCEN, context)
        popcen_sheet = self.parameterAsString(parameters, self.POPCEN_SHEET, context)
        earf_sheet = self.parameterAsString(parameters, self.EARF_SHEET, context)

        gpkg_layers = self._resolve_layers(parameters, self.INPUT_GPKG, context)
        earf_files = self._resolve_files(parameters, self.INPUT_EARF, context)

        if not gpkg_layers:
            raise QgsProcessingException("Please provide at least one valid polygon EA GeoPackage layer (EA boundaries).")

        earf_use_db = False
        if not earf_files:
            db_path = _get_popcen_db_path()
            if os.path.exists(db_path):
                import sqlite3
                try:
                    conn = sqlite3.connect(db_path)
                    cur = conn.cursor()
                    cur.execute("SELECT COUNT(*) FROM earf_records")
                    if cur.fetchone()[0] > 0:
                        earf_use_db = True
                    conn.close()
                except Exception:
                    earf_use_db = False

            if not earf_use_db:
                raise QgsProcessingException("Please provide at least one valid Preliminary EARF Excel file or consolidate into Local Database.")

        # ---- 1. POPCEN Excel or Local Database -> geocode -----------------
        if popcen_path and os.path.exists(popcen_path):
            feedback.pushInfo(f"Reading POPCEN Excel: {os.path.basename(popcen_path)}...")
            try:
                popcen_df = read_popcen_table(popcen_path, sheet_name=popcen_sheet)
            except Exception as e:
                raise QgsProcessingException(f"Failed to read POPCEN Excel file: {e}")
            popcen_df["geocode"] = popcen_df.apply(
                lambda r: build_geocode(r.to_dict(), POPCEN_FIELDS), axis=1
            )
        else:
            db_path = _get_popcen_db_path()
            if os.path.exists(db_path):
                import sqlite3
                feedback.pushInfo("Reading POPCEN records from Local SQLite Database (popcen_database.db)...")
                conn = sqlite3.connect(db_path)
                popcen_df = pd.read_sql_query("SELECT * FROM popcen_records", conn)
                conn.close()
                if popcen_df.empty:
                    raise QgsProcessingException("Local database is empty. Please provide a POPCEN Excel file.")
            else:
                raise QgsProcessingException(
                    "POPCEN file not found and Local Database does not exist. "
                    "Please provide a 2024 POPCEN Excel file."
                )
        _all_popcen_geocodes = set(popcen_df["geocode"].astype(str).str.strip())
        popcen_df, n_excluded = exclude_non_ea_levels(popcen_df)
        non_ea_geocodes = _all_popcen_geocodes - set(popcen_df["geocode"].astype(str).str.strip())
        non_ea_geocodes.discard("")
        if n_excluded > 0:
            feedback.pushInfo(
                f"POPCEN: excluded {n_excluded} rows at Philippines/Region/Province/"
                f"City-Municipality/Barangay level from matching."
            )
        empty_popcen = (popcen_df["geocode"] == "").sum()
        if empty_popcen > 0:
            feedback.pushWarning(f"POPCEN: {empty_popcen} rows had blank or invalid geocodes and were skipped.")
            popcen_df = popcen_df[popcen_df["geocode"] != ""].copy()

        popcen_df = popcen_df.add_prefix("popcen_")
        popcen_df = popcen_df.rename(columns={"popcen_geocode": "geocode"})
        popcen_unique = popcen_df["geocode"].nunique()
        feedback.pushInfo(f"  POPCEN: {len(popcen_df)} rows, {popcen_unique} unique geocodes")
        if len(popcen_df) != popcen_unique:
            feedback.pushWarning(f"  POPCEN has {len(popcen_df) - popcen_unique} duplicate geocodes (consolidating).")
            popcen_df = popcen_df.drop_duplicates(subset=["geocode"], keep="first")

        if feedback.isCanceled():
            return {}

        # ---- 2. EA GeoPackage layers -> geocode + geometry -----------------
        feedback.pushInfo(f"Reading {len(gpkg_layers)} EA GeoPackage layer(s)...")
        gpkg_rows = []
        geom_by_geocode = {}
        layers_by_geocode = {}
        target_crs = None
        base_wkb = QgsWkbTypes.NoGeometry

        for lyr_idx, layer in enumerate(gpkg_layers):
            if feedback.isCanceled():
                return {}
            lyr_name = layer.name() or f"Layer_{lyr_idx + 1}"
            feedback.pushInfo(f"  Processing layer ({lyr_idx + 1}/{len(gpkg_layers)}): {lyr_name}...")

            if target_crs is None and layer.crs().isValid():
                target_crs = layer.crs()
                base_wkb = layer.wkbType()

            reproject = False
            transform = None
            if target_crs and layer.crs().isValid() and layer.crs() != target_crs:
                reproject = True
                transform = QgsCoordinateTransform(layer.crs(), target_crs, context.transformContext())

            field_names = [f.name() for f in layer.fields()]
            for feat in layer.getFeatures():
                if feedback.isCanceled():
                    return {}
                attrs = dict(zip(field_names, feat.attributes()))
                gc = build_geocode(attrs, GPKG_EARF_FIELDS)
                if not gc:
                    continue

                if gc not in layers_by_geocode:
                    layers_by_geocode[gc] = set()
                layers_by_geocode[gc].add(lyr_name)

                row = {f"gpkg_{k}": attrs[k] for k in field_names}
                row["geocode"] = gc
                gpkg_rows.append(row)

                if feat.hasGeometry() and not feat.geometry().isEmpty():
                    if feat.geometry().type() != QgsWkbTypes.PolygonGeometry:
                        continue
                    geom = QgsGeometry(feat.geometry())
                    if reproject and transform:
                        geom.transform(transform)
                    if gc in geom_by_geocode:
                        # Combine multipart polygons belonging to the same EA geocode
                        combined = geom_by_geocode[gc].combine(geom)
                        if combined and not combined.isEmpty():
                            geom_by_geocode[gc] = combined
                    else:
                        geom_by_geocode[gc] = geom

        if gpkg_rows:
            gpkg_df = pd.DataFrame(gpkg_rows)
        else:
            gpkg_df = pd.DataFrame(columns=["geocode"])

        gpkg_unique = gpkg_df["geocode"].nunique() if not gpkg_df.empty else 0
        total_feats = len(gpkg_df)
        feedback.pushInfo(f"  GeoPackage: {total_feats} total features across {len(gpkg_layers)} layer(s), {gpkg_unique} unique geocodes")

        # Consistency and overlap check across layers
        cross_file_overlaps = [gc for gc, lyrs in layers_by_geocode.items() if len(lyrs) > 1]
        if cross_file_overlaps:
            feedback.pushWarning(
                f"  [CONSISTENCY CHECK] Detected {len(cross_file_overlaps)} cross-file overlapping geocode(s) "
                f"present in multiple GeoPackage layers (e.g., {', '.join(cross_file_overlaps[:5])}{'...' if len(cross_file_overlaps) > 5 else ''})."
            )
            feedback.pushInfo(
                f"  [DEDUPLICATION] Consolidating overlapping features: combining multipart geometries into unified MultiPolygons "
                f"and retaining primary attributes to prevent duplicate feature creation in output."
            )

        if total_feats != gpkg_unique:
            feedback.pushInfo(f"  GeoPackage: consolidating duplicate geocode features into single entries.")
            gpkg_df = gpkg_df.drop_duplicates(subset=["geocode"], keep="first")

        if feedback.isCanceled():
            return {}

        # ---- 3. Preliminary EARF Excel files or Local Database -> geocode ----
        if earf_use_db:
            db_path = _get_popcen_db_path()
            feedback.pushInfo("Reading consolidated Preliminary EARF records from Local SQLite Database (earf_records)...")
            import sqlite3
            conn = sqlite3.connect(db_path)
            earf_df = pd.read_sql_query("SELECT * FROM earf_records", conn)
            conn.close()
        else:
            feedback.pushInfo(f"Reading {len(earf_files)} preliminary EARF Excel file(s)...")
            earf_dfs = []
            for file_idx, fpath in enumerate(earf_files):
                if feedback.isCanceled():
                    return {}
                if not fpath or not os.path.exists(fpath):
                    feedback.pushWarning(f"  EARF file not found: {fpath}")
                    continue
                fname = os.path.basename(fpath)
                feedback.pushInfo(f"  Reading EARF file ({file_idx + 1}/{len(earf_files)}): {fname}...")
                sheet_arg = earf_sheet.strip() if earf_sheet and earf_sheet.strip() else 0
                patch_openpyxl()
                try:
                    df_i = read_earf_table(fpath, sheet_name=sheet_arg)
                except Exception as e:
                    feedback.reportError(f"  Failed to read EARF file {fname}: {e}")
                    continue

                empty_cnt = (df_i["geocode"] == "").sum()
                if empty_cnt > 0:
                    feedback.pushWarning(f"    {fname}: {empty_cnt} rows had blank or invalid geocodes and were skipped.")
                    df_i = df_i[df_i["geocode"] != ""].copy()
                earf_dfs.append(df_i)

            if earf_dfs:
                earf_df = pd.concat(earf_dfs, ignore_index=True)
            else:
                earf_df = pd.DataFrame(columns=["geocode"])

        earf_df = earf_df.add_prefix("earf_")
        earf_df = earf_df.rename(columns={"earf_geocode": "geocode"})
        earf_unique = earf_df["geocode"].nunique() if not earf_df.empty else 0
        feedback.pushInfo(f"  Combined EARF: {len(earf_df)} rows, {earf_unique} unique geocodes (preserving split duplicate EAs).")

        if feedback.isCanceled():
            return {}

        # ---- 4. Outer-join all sources on geocode --------------------------
        feedback.pushInfo("Matching on geocode (outer join, unmatched rows kept)...")
        # GeoPackage layers and Preliminary EARFs are strictly Enumeration Area (EA) records
        # and must never be purged by non_ea_geocodes. POPCEN non-EA levels were already excluded.

        # Restrict POPCEN to the uploaded citymuns present in EARF and GPKG
        uploaded_cm_set = set()
        if not gpkg_df.empty and "geocode" in gpkg_df.columns:
            uploaded_cm_set |= {str(g)[:5] for g in gpkg_df["geocode"].dropna() if len(str(g)) >= 5 and not str(g)[:5].endswith("00")}
        if not earf_df.empty and "geocode" in earf_df.columns:
            uploaded_cm_set |= {str(g)[:5] for g in earf_df["geocode"].dropna() if len(str(g)) >= 5 and not str(g)[:5].endswith("00")}
        if uploaded_cm_set:
            popcen_df = popcen_df[popcen_df["geocode"].astype(str).str[:5].isin(uploaded_cm_set)].copy()
            feedback.pushInfo(f"POPCEN restricted to {len(uploaded_cm_set)} uploaded city/municipalities ({len(popcen_df)} EAs).")

        popcen_geocodes = set(popcen_df["geocode"].dropna())
        gpkg_geocodes = set(gpkg_df["geocode"].dropna()) if not gpkg_df.empty else set()
        earf_geocodes = set(earf_df["geocode"].dropna())

        merged = popcen_df.merge(
            gpkg_df, on="geocode", how="outer"
        ).merge(
            earf_df, on="geocode", how="outer"
        )

        def compute_match_status(gc):
            in_popcen = gc in popcen_geocodes
            in_gpkg = gc in gpkg_geocodes
            in_earf = gc in earf_geocodes
            if in_popcen and in_gpkg and in_earf:
                return "MATCHED_ALL"
            tags = []
            if in_popcen:
                tags.append("POPCEN")
            if in_gpkg:
                tags.append("GPKG")
            if in_earf:
                tags.append("EARF")
            return "UNMATCHED_" + "_".join(tags) if tags else "UNMATCHED"

        merged["match_status"] = merged["geocode"].apply(compute_match_status)

        # Stably sort merged by geocode so that duplicate geocodes appear immediately adjacent to each other
        merged = merged.sort_values(by=["geocode"], kind="mergesort").reset_index(drop=True)

        # For duplicate geocodes, blank out baseline POPCEN households on subsequent rows to prevent double counting
        if "popcen_no_of_households" in merged.columns:
            dup_mask = merged.duplicated(subset=["geocode"], keep="first")
            merged.loc[dup_mask, "popcen_no_of_households"] = ""

        # Place geocode and match_status first for best usability
        first_cols = ["geocode", "match_status"]
        other_cols = [c for c in merged.columns if c not in first_cols]
        merged = merged[first_cols + other_cols]

        n_all = (merged["match_status"] == "MATCHED_ALL").sum()
        n_partial = len(merged) - n_all
        feedback.pushInfo(
            f"Result: {len(merged)} geocodes total — {n_all} matched in all "
            f"three sources, {n_partial} partially matched / unmatched"
        )

        if feedback.isCanceled():
            return {}

        # ---- 5. Build output sink ------------------------------------------
        out_fields = QgsFields()
        for col in merged.columns:
            out_fields.append(QgsField(col, FIELD_STRING_TYPE))

        crs = target_crs if target_crs and target_crs.isValid() else QgsCoordinateReferenceSystem("EPSG:4326")
        if geom_by_geocode:
            if base_wkb not in (QgsWkbTypes.NoGeometry, QgsWkbTypes.Unknown):
                wkb_type = QgsWkbTypes.multiType(base_wkb)
            else:
                first_geom = next(iter(geom_by_geocode.values()))
                wkb_type = QgsWkbTypes.multiType(first_geom.wkbType()) if first_geom else QgsWkbTypes.MultiPolygon
        else:
            wkb_type = QgsWkbTypes.NoGeometry

        (sink, dest_id) = self.parameterAsSink(
            parameters, self.OUTPUT, context, out_fields, wkb_type, crs
        )
        if sink is None:
            raise QgsProcessingException("Could not create output feature sink.")

        total_features = len(merged)
        step = max(1, total_features // 100) if total_features else 1

        for idx, (_, row) in enumerate(merged.iterrows()):
            if feedback.isCanceled():
                break
            f = QgsFeature(out_fields)
            f.setAttributes([("" if pd.isna(v) else str(v)) for v in row])
            gc = row["geocode"]
            if gc in geom_by_geocode:
                f.setGeometry(geom_by_geocode[gc])
            sink.addFeature(f)

            if idx % step == 0:
                feedback.setProgress(int((idx / total_features) * 100))

        # ---- 6. Optional CSV mirror for the web viewer ---------------------
        csv_path = self.parameterAsFileOutput(parameters, self.OUTPUT_CSV, context)
        if csv_path:
            merged.to_csv(csv_path, index=False, encoding="utf-8")
            feedback.pushInfo(f"CSV mirror written to {csv_path}")

        return {self.OUTPUT: dest_id, self.OUTPUT_CSV: csv_path}
