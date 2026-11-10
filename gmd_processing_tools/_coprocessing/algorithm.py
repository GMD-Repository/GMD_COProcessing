# -*- coding: utf-8 -*-
"""Root compatibility proxy forwarding to core.algorithm."""

try:
    from .core.algorithm import *
    from .core.algorithm import (
        CoProcessingEarfMatch,
        build_geocode,
        read_earf_table,
        read_popcen_table,
        exclude_non_ea_levels,
        is_version_dir,
        POPCEN_FIELDS,
        GPKG_EARF_FIELDS,
        CODE_WIDTHS,
        to_whole_number_str,
    )
except (ImportError, ValueError):
    from core.algorithm import *
    from core.algorithm import (
        CoProcessingEarfMatch,
        build_geocode,
        read_earf_table,
        read_popcen_table,
        exclude_non_ea_levels,
        is_version_dir,
        POPCEN_FIELDS,
        GPKG_EARF_FIELDS,
        CODE_WIDTHS,
        to_whole_number_str,
    )
