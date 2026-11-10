# -*- coding: utf-8 -*-
"""Root compatibility proxy forwarding to db.database."""

try:
    from .db.database import *
    from .db.database import (
        POPCENDatabase,
        find_earf_files_in_folder,
        find_gpkg_files_in_folder,
        get_gpkg_polygon_layer_info,
        is_version_dir,
        get_default_db_path,
        get_default_earf_db_path,
        get_default_gpkg_db_path,
        POPCEN_DB_FILENAME,
        EARF_DB_FILENAME,
        GPKG_DB_FILENAME,
        to_whole_number_str,
        compute_recoded_enumeration_areas,
        audit_matched_flags,
        export_output_summary_to_excel,
        DEFAULT_FINAL_EXPORT_ROOT,
        DEFAULT_GPKG_ROOT,
        DEFAULT_EARF_ROOT,
    )
except (ImportError, ValueError):
    from db.database import *
    from db.database import (
        POPCENDatabase,
        find_earf_files_in_folder,
        find_gpkg_files_in_folder,
        get_gpkg_polygon_layer_info,
        is_version_dir,
        get_default_db_path,
        get_default_earf_db_path,
        get_default_gpkg_db_path,
        POPCEN_DB_FILENAME,
        EARF_DB_FILENAME,
        GPKG_DB_FILENAME,
        to_whole_number_str,
        compute_recoded_enumeration_areas,
        audit_matched_flags,
        export_output_summary_to_excel,
        DEFAULT_FINAL_EXPORT_ROOT,
        DEFAULT_GPKG_ROOT,
        DEFAULT_EARF_ROOT,
    )
