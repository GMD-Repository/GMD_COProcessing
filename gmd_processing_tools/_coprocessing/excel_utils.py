# -*- coding: utf-8 -*-
"""Root compatibility proxy forwarding to core.excel_utils."""

try:
    from .core.excel_utils import *
    from .core.excel_utils import patch_openpyxl
except (ImportError, ValueError):
    from core.excel_utils import *
    from core.excel_utils import patch_openpyxl
