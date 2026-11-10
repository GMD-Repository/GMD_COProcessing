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
]

