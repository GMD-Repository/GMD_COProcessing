# -*- coding: utf-8 -*-
"""Root compatibility proxy forwarding to ui.dialog."""

try:
    from .ui.dialog import *
    from .ui.dialog import (
        COProcessingDialog,
        GroupedHeaderView,
        GPKGConsistencyDialog,
        GpkgFileSelectionDialog,
        ExecutionWorker,
        DbImportWorker,
        EarfConsolidationWorker,
        EarfConsistencyWorker,
        GpkgConsolidationWorker,
        GpkgConsistencyWorker,
        MonitoringScanWorker,
        DeliverablesExportWorker,
    )
except (ImportError, ValueError):
    from ui.dialog import *
    from ui.dialog import (
        COProcessingDialog,
        GroupedHeaderView,
        GPKGConsistencyDialog,
        GpkgFileSelectionDialog,
        ExecutionWorker,
        DbImportWorker,
        EarfConsolidationWorker,
        EarfConsistencyWorker,
        GpkgConsolidationWorker,
        GpkgConsistencyWorker,
        MonitoringScanWorker,
        DeliverablesExportWorker,
    )
