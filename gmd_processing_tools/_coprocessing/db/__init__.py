# -*- coding: utf-8 -*-
"""
CO Processing — Database & Persistent Storage Layer
---------------------------------------------------
Provides SQLite and GeoPackage database management for 2024 POPCEN,
Preliminary EARF, and EA GeoPackage layers.
"""

from .database import (
    POPCENDatabase,
    find_earf_files_in_folder,
    find_gpkg_files_in_folder,
    get_gpkg_polygon_layer_info,
    get_default_db_path,
    get_default_earf_db_path,
    get_default_gpkg_db_path,
    REQUIRED_GPKG_COLUMNS,
    get_gpkg_layer_columns,
    validate_gpkg_columns,
    export_matched_to_excel,
    export_matched_to_mis_excel,
)
from .gpkg_manager import EAGeoPackageDatabase, get_default_gpkg_path

__all__ = [
    "POPCENDatabase",
    "find_earf_files_in_folder",
    "find_gpkg_files_in_folder",
    "get_gpkg_polygon_layer_info",
    "REQUIRED_GPKG_COLUMNS",
    "get_gpkg_layer_columns",
    "validate_gpkg_columns",
    "get_default_db_path",
    "get_default_earf_db_path",
    "get_default_gpkg_db_path",
    "export_matched_to_excel",
    "export_matched_to_mis_excel",
    "EAGeoPackageDatabase",
    "get_default_gpkg_path",
]
