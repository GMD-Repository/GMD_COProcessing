# -*- coding: utf-8 -*-
"""
CO Processing — Core Processing Algorithms & Normalization
-----------------------------------------------------------
Contains geocode construction, multi-source outer-join algorithm,
and Excel data normalization utilities.
"""

from .algorithm import (
    CoProcessingEarfMatch,
    build_geocode,
    read_earf_table,
    read_popcen_table,
    exclude_non_ea_levels,
    is_version_dir,
    POPCEN_FIELDS,
    GPKG_EARF_FIELDS,
    CODE_WIDTHS,
)
from .excel_utils import patch_openpyxl
from .prelim_earf_validator import (
    VALIDATION_SPECS,
    VALIDATION_MAP,
    run_prelim_earf_validations,
    generate_validation_excel_report,
)
from .gpkg_validator import (
    GPKG_VALIDATION_SPECS,
    GPKG_VALIDATION_MAP,
    run_gpkg_validations,
    generate_gpkg_validation_excel_report,
)

__all__ = [
    "CoProcessingEarfMatch",
    "build_geocode",
    "read_earf_table",
    "read_popcen_table",
    "exclude_non_ea_levels",
    "is_version_dir",
    "POPCEN_FIELDS",
    "GPKG_EARF_FIELDS",
    "CODE_WIDTHS",
    "patch_openpyxl",
    "VALIDATION_SPECS",
    "VALIDATION_MAP",
    "run_prelim_earf_validations",
    "generate_validation_excel_report",
    "GPKG_VALIDATION_SPECS",
    "GPKG_VALIDATION_MAP",
    "run_gpkg_validations",
    "generate_gpkg_validation_excel_report",
]

