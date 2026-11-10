# -*- coding: utf-8 -*-
"""Root compatibility proxy forwarding to db.gpkg_manager."""

try:
    from .db.gpkg_manager import *
    from .db.gpkg_manager import (
        EAGeoPackageDatabase,
        get_default_gpkg_path,
        GPKG_FILENAME,
        MASTER_LAYER_NAME,
    )
except (ImportError, ValueError):
    from db.gpkg_manager import *
    from db.gpkg_manager import (
        EAGeoPackageDatabase,
        get_default_gpkg_path,
        GPKG_FILENAME,
        MASTER_LAYER_NAME,
    )
