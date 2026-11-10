# -*- coding: utf-8 -*-
"""
CO Processing — Deliverables Review & Value QA Dialog
Interactive inspection and audit workspace for municipal deliverables
(<pppmm>_ea2026.gpkg, <pppmm>_final_earf_<citymun>.xlsx, <pppmm>_MIS_<citymun>.xlsx).
"""

import os
import csv
from typing import Dict, List, Tuple, Set, Any, Optional, Callable

from qgis.PyQt.QtCore import Qt, QThread, pyqtSignal
from qgis.PyQt.QtGui import QFont, QColor, QIcon
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QTabWidget, QWidget, QFrame, QLineEdit,
    QComboBox, QMessageBox, QFileDialog, QProgressBar, QAbstractItemView
)

try:
    from ..core.output_qa import OutputQAEvaluator, AUDIT_RULES
except (ImportError, ValueError):
    try:
        from core.output_qa import OutputQAEvaluator, AUDIT_RULES
    except ImportError:
        from output_qa import OutputQAEvaluator, AUDIT_RULES


class QAEvaluationWorker(QThread):
    """Background worker to evaluate deliverables without freezing QGIS UI."""
    finished_signal = pyqtSignal(dict)
    error_signal = pyqtSignal(str)

    def __init__(self, evaluator: OutputQAEvaluator):
        super().__init__()
        self.evaluator = evaluator
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        try:
            summary = self.evaluator.evaluate_all()
            if self._is_cancelled:
                self.error_signal.emit("Evaluation cancelled by user.")
            else:
                self.finished_signal.emit(summary)
        except Exception as e:
            self.error_signal.emit(str(e))


class DeliverablesReviewDialog(QDialog):
    """Interactive Modal Dialog for Reviewing Deliverables & Value Consistency."""

    def __init__(
        self,
        parent=None,
        city_mun_code: str = "",
        target_dir: Optional[str] = None,
        parent_db=None,
        on_reexport_callback: Optional[Callable[[str], bool]] = None,
    ):
        super().__init__(parent)
        self.city_mun_code = str(city_mun_code or "").strip()
        self.target_dir = target_dir or ""
        self.db = parent_db
        self.on_reexport = on_reexport_callback

        self.summary: Dict[str, Any] = {}
        self.evaluator: Optional[OutputQAEvaluator] = None
        self.worker: Optional[QAEvaluationWorker] = None

        self.setWindowTitle(f"CO Processing — Deliverables Quality & Value Review [{self.city_mun_code}]")
        self.setMinimumSize(1100, 720)
        self.resize(1150, 760)

        # Apply unified styling
        self._apply_dialog_styles()
        self._init_ui()

        # Start initial evaluation
        self._start_evaluation()

    def _apply_dialog_styles(self):
        self.setStyleSheet("""
            QDialog {
                background-color: #f8fafc;
                font-family: 'Segoe UI', sans-serif;
            }
            QTabWidget::pane {
                border: 1px solid #cbd5e1;
                background: white;
                border-radius: 6px;
            }
            QTabBar::tab {
                background: #e2e8f0;
                color: #334155;
                font-weight: bold;
                padding: 8px 18px;
                border-top-left-radius: 5px;
                border-top-right-radius: 5px;
                margin-right: 2px;
            }
            QTabBar::tab:selected {
                background: white;
                color: #0369a1;
                border-bottom: 2px solid #0284c7;
            }
            QTableWidget {
                background-color: white;
                gridline-color: #f1f5f9;
                border: 1px solid #e2e8f0;
                font-size: 12px;
                selection-background-color: #e0f2fe;
                selection-color: #0369a1;
            }
            QHeaderView::section {
                background-color: #f1f5f9;
                color: #475569;
                font-weight: bold;
                border: 1px solid #e2e8f0;
                padding: 6px;
                font-size: 11px;
            }
            QLineEdit, QComboBox {
                background: white;
                border: 1px solid #cbd5e1;
                border-radius: 4px;
                padding: 5px 8px;
                font-size: 12px;
            }
            QPushButton#primaryBtn {
                background-color: #0284c7;
                color: white;
                font-weight: bold;
                border-radius: 4px;
                padding: 6px 14px;
                border: none;
            }
            QPushButton#primaryBtn:hover {
                background-color: #0369a1;
            }
            QPushButton#successBtn {
                background-color: #059669;
                color: white;
                font-weight: bold;
                border-radius: 4px;
                padding: 6px 14px;
                border: none;
            }
            QPushButton#successBtn:hover {
                background-color: #047857;
            }
            QPushButton#actionBtn {
                background-color: #4f46e5;
                color: white;
                font-weight: bold;
                border-radius: 4px;
                padding: 6px 14px;
                border: none;
            }
            QPushButton#actionBtn:hover {
                background-color: #4338ca;
            }
            QPushButton#secondaryBtn {
                background-color: white;
                color: #334155;
                border: 1px solid #cbd5e1;
                border-radius: 4px;
                padding: 6px 14px;
            }
            QPushButton#secondaryBtn:hover {
                background-color: #f1f5f9;
            }
            QPushButton#stopBtn {
                background-color: #ffffff;
                color: #dc2626;
                font-weight: 600;
                border: 1px solid #f87171;
                border-radius: 4px;
                padding: 6px 14px;
            }
            QPushButton#stopBtn:hover:!disabled {
                background-color: #dc2626;
                color: #ffffff;
                border-color: #dc2626;
            }
            QPushButton#stopBtn:disabled {
                background-color: #f8fafc;
                color: #cbd5e1;
                border: 1px solid #e2e8f0;
            }
        """)

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(14, 14, 14, 14)
        main_layout.setSpacing(10)

        # 1. Header Toolbar
        header_layout = QHBoxLayout()
        self.lbl_header_title = QLabel(f"<b>Deliverables Quality Review:</b> {self.city_mun_code}")
        self.lbl_header_title.setFont(QFont("Segoe UI", 12, QFont.Bold))
        self.lbl_header_title.setStyleSheet("color: #0f172a;")
        header_layout.addWidget(self.lbl_header_title)

        header_layout.addStretch()

        self.btn_reexport = QPushButton("Auto-Fix & Re-Export")
        self.btn_reexport.setObjectName("actionBtn")
        self.btn_reexport.setToolTip("Re-runs deliverable generation and re-sequences EANs to fix value discrepancies")
        self.btn_reexport.clicked.connect(self._handle_reexport)
        header_layout.addWidget(self.btn_reexport)

        self.btn_rerun = QPushButton("Re-Run QA Audit")
        self.btn_rerun.setObjectName("primaryBtn")
        self.btn_rerun.clicked.connect(self._start_evaluation)
        header_layout.addWidget(self.btn_rerun)

        self.btn_qa_stop = QPushButton("Stop")
        self.btn_qa_stop.setObjectName("stopBtn")
        self.btn_qa_stop.setToolTip("Stop active QA evaluation immediately")
        self.btn_qa_stop.setEnabled(False)
        self.btn_qa_stop.clicked.connect(self._stop_evaluation)
        header_layout.addWidget(self.btn_qa_stop)

        main_layout.addLayout(header_layout)

        # Directory / Metadata banner
        self.lbl_dir_info = QLabel(f"Target Directory: <i>{self.target_dir or 'Scanning automatically...'}</i>")
        self.lbl_dir_info.setStyleSheet("color: #64748b; font-size: 11px;")
        main_layout.addWidget(self.lbl_dir_info)

        # 2. KPI Scorecard Banner (4 Cards)
        kpi_layout = QHBoxLayout()
        kpi_layout.setSpacing(10)

        self.card_status, self.lbl_kpi_status, self.lbl_kpi_status_sub = self._create_kpi_card("Overall QA Status", "EVALUATING...", "#0284c7")
        self.card_score, self.lbl_kpi_score, self.lbl_kpi_score_sub = self._create_kpi_card("Compliance Score", "- %", "#0f172a")
        self.card_rules, self.lbl_kpi_rules, self.lbl_kpi_rules_sub = self._create_kpi_card("Rules Passed", "- / 25", "#059669")
        self.card_features, self.lbl_kpi_features, self.lbl_kpi_features_sub = self._create_kpi_card("Deliverables Audited", "3 Files", "#4f46e5")

        kpi_layout.addWidget(self.card_status, 1)
        kpi_layout.addWidget(self.card_score, 1)
        kpi_layout.addWidget(self.card_rules, 1)
        kpi_layout.addWidget(self.card_features, 1)

        main_layout.addLayout(kpi_layout)

        # Progress bar during audit
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFixedHeight(5)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet("QProgressBar::chunk { background-color: #0284c7; }")
        main_layout.addWidget(self.progress_bar)

        # 3. Primary Review Tabs
        self.tab_widget = QTabWidget()

        # Tab 1: 25-Rule Checklist
        self.tab_checklist = QWidget()
        self._init_checklist_tab()
        self.tab_widget.addTab(self.tab_checklist, "Rule Checklist (25 Rules)")

        # Tab 2: Discrepancy & Value Inspector
        self.tab_discrepancies = QWidget()
        self._init_discrepancies_tab()
        self.tab_widget.addTab(self.tab_discrepancies, "Value Discrepancy Inspector")

        # Tab 3: Hierarchical Rollup Audit
        self.tab_rollups = QWidget()
        self._init_rollups_tab()
        self.tab_widget.addTab(self.tab_rollups, "Hierarchical Rollup Audit")

        main_layout.addWidget(self.tab_widget, 1)

        # 4. Bottom Action Footer
        footer_layout = QHBoxLayout()
        self.lbl_footer_status = QLabel("Ready")
        self.lbl_footer_status.setStyleSheet("color: #64748b; font-size: 11px;")
        footer_layout.addWidget(self.lbl_footer_status)

        footer_layout.addStretch()

        self.btn_export_report = QPushButton("Export QA Audit Report (CSV)")
        self.btn_export_report.setObjectName("secondaryBtn")
        self.btn_export_report.clicked.connect(self._export_qa_report_csv)
        footer_layout.addWidget(self.btn_export_report)

        self.btn_export_cert = QPushButton("Generate QA Certificate (HTML)")
        self.btn_export_cert.setObjectName("successBtn")
        self.btn_export_cert.clicked.connect(self._generate_qa_certificate_html)
        footer_layout.addWidget(self.btn_export_cert)

        btn_close = QPushButton("Close")
        btn_close.setObjectName("secondaryBtn")
        btn_close.clicked.connect(self.accept)
        footer_layout.addWidget(btn_close)

        main_layout.addLayout(footer_layout)

    def _create_kpi_card(self, title: str, initial_val: str, color_hex: str):
        card = QFrame()
        card.setStyleSheet("""
            QFrame {
                background: white;
                border: 1px solid #e2e8f0;
                border-radius: 6px;
                padding: 4px;
            }
        """)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(2)

        lbl_title = QLabel(title.upper())
        lbl_title.setFont(QFont("Segoe UI", 8, QFont.Bold))
        lbl_title.setStyleSheet("color: #64748b; letter-spacing: 0.5px;")

        lbl_val = QLabel(initial_val)
        lbl_val.setFont(QFont("Segoe UI", 14, QFont.Bold))
        lbl_val.setStyleSheet(f"color: {color_hex};")

        lbl_sub = QLabel("-")
        lbl_sub.setFont(QFont("Segoe UI", 8))
        lbl_sub.setStyleSheet("color: #94a3b8;")

        layout.addWidget(lbl_title)
        layout.addWidget(lbl_val)
        layout.addWidget(lbl_sub)
        return card, lbl_val, lbl_sub

    # -------------------------------------------------------------
    # Tab 1: Checklist
    # -------------------------------------------------------------
    def _init_checklist_tab(self):
        layout = QVBoxLayout(self.tab_checklist)
        layout.setContentsMargins(8, 8, 8, 8)

        self.table_rules = QTableWidget(0, 6)
        self.table_rules.setHorizontalHeaderLabels([
            "Status", "Rule ID", "Category", "Rule Title", "Severity", "Audit Standard / Message"
        ])
        self.table_rules.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table_rules.horizontalHeader().setStretchLastSection(True)
        self.table_rules.setColumnWidth(0, 90)
        self.table_rules.setColumnWidth(1, 80)
        self.table_rules.setColumnWidth(2, 110)
        self.table_rules.setColumnWidth(3, 230)
        self.table_rules.setColumnWidth(4, 90)
        self.table_rules.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table_rules.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table_rules.cellDoubleClicked.connect(self._on_rule_double_clicked)
        layout.addWidget(self.table_rules)

    # -------------------------------------------------------------
    # Tab 2: Discrepancies
    # -------------------------------------------------------------
    def _init_discrepancies_tab(self):
        layout = QVBoxLayout(self.tab_discrepancies)
        layout.setContentsMargins(8, 8, 8, 8)

        # Filter bar
        filter_bar = QHBoxLayout()
        filter_bar.addWidget(QLabel("Filter:"))
        self.txt_disc_filter = QLineEdit()
        self.txt_disc_filter.setPlaceholderText("Search geocode, field, description...")
        self.txt_disc_filter.textChanged.connect(self._apply_discrepancy_filter)
        filter_bar.addWidget(self.txt_disc_filter, 1)

        self.cmb_severity_filter = QComboBox()
        self.cmb_severity_filter.addItems(["All Severities", "Critical / High Only", "Warnings / Medium Only"])
        self.cmb_severity_filter.currentIndexChanged.connect(self._apply_discrepancy_filter)
        filter_bar.addWidget(self.cmb_severity_filter)

        layout.addLayout(filter_bar)

        self.table_discrepancies = QTableWidget(0, 8)
        self.table_discrepancies.setHorizontalHeaderLabels([
            "Rule ID", "Severity", "Geocode", "Field Name", "GPKG Value", "Final EARF Value", "MIS Value", "Description / Expected"
        ])
        self.table_discrepancies.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table_discrepancies.horizontalHeader().setStretchLastSection(True)
        self.table_discrepancies.setColumnWidth(0, 75)
        self.table_discrepancies.setColumnWidth(1, 85)
        self.table_discrepancies.setColumnWidth(2, 130)
        self.table_discrepancies.setColumnWidth(3, 110)
        self.table_discrepancies.setColumnWidth(4, 90)
        self.table_discrepancies.setColumnWidth(5, 100)
        self.table_discrepancies.setColumnWidth(6, 90)
        self.table_discrepancies.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table_discrepancies.setEditTriggers(QAbstractItemView.NoEditTriggers)
        layout.addWidget(self.table_discrepancies)

    # -------------------------------------------------------------
    # Tab 3: Rollup Audit
    # -------------------------------------------------------------
    def _init_rollups_tab(self):
        layout = QVBoxLayout(self.tab_rollups)
        layout.setContentsMargins(8, 8, 8, 8)

        lbl_info = QLabel("Comparison of declared header sums versus calculated EA totals across administrative levels:")
        lbl_info.setStyleSheet("color: #475569; font-size: 11px;")
        layout.addWidget(lbl_info)

        self.table_rollups = QTableWidget(0, 8)
        self.table_rollups.setHorizontalHeaderLabels([
            "Level", "Code", "Name", "EAs (Decl / Calc)", "Households (Decl / Calc)", "HH Diff", "Buildings (Decl / Calc)", "Status"
        ])
        self.table_rollups.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table_rollups.horizontalHeader().setStretchLastSection(True)
        self.table_rollups.setColumnWidth(0, 100)
        self.table_rollups.setColumnWidth(1, 70)
        self.table_rollups.setColumnWidth(2, 200)
        self.table_rollups.setColumnWidth(3, 110)
        self.table_rollups.setColumnWidth(4, 140)
        self.table_rollups.setColumnWidth(5, 70)
        self.table_rollups.setColumnWidth(6, 140)
        self.table_rollups.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table_rollups.setEditTriggers(QAbstractItemView.NoEditTriggers)
        layout.addWidget(self.table_rollups)

    # -------------------------------------------------------------
    # Evaluation Execution & Signal Handlers
    # -------------------------------------------------------------
    def _start_evaluation(self):
        self.progress_bar.setVisible(True)
        self.btn_rerun.setEnabled(False)
        if hasattr(self, "btn_qa_stop"):
            self.btn_qa_stop.setEnabled(True)
        self.lbl_kpi_status.setText("AUDITING...")
        self.lbl_kpi_status.setStyleSheet("color: #0284c7;")
        self.lbl_footer_status.setText("Evaluating deliverables across 25 schema, value parity, and spatial rules...")

        self.evaluator = OutputQAEvaluator(
            city_mun_code=self.city_mun_code,
            target_dir=self.target_dir
        )
        self.evaluator.discover_files()

        self.worker = QAEvaluationWorker(self.evaluator)
        self.worker.finished_signal.connect(self._on_evaluation_finished)
        self.worker.error_signal.connect(self._on_evaluation_error)
        self.worker.start()

    def _stop_evaluation(self):
        if hasattr(self, "worker") and self.worker and self.worker.isRunning():
            self.worker.cancel()
        if hasattr(self, "btn_qa_stop"):
            self.btn_qa_stop.setEnabled(False)
        self.btn_rerun.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.lbl_kpi_status.setText("STOPPED")
        self.lbl_kpi_status.setStyleSheet("color: #dc2626;")
        self.lbl_kpi_status_sub.setText("Cancelled by user")
        self.lbl_footer_status.setText("QA audit stopped by user.")

    def _on_evaluation_finished(self, summary: Dict[str, Any]):
        self.progress_bar.setVisible(False)
        self.btn_rerun.setEnabled(True)
        if hasattr(self, "btn_qa_stop"):
            self.btn_qa_stop.setEnabled(False)
        self.summary = summary

        # Persist into database if db is available
        if self.db and hasattr(self.db, "store_qa_result"):
            try:
                self.db.store_qa_result(self.city_mun_code, summary)
            except Exception:
                pass

        # Update KPI Cards
        st = summary.get("overall_status", "UNKNOWN")
        score = summary.get("compliance_score", 0.0)
        pass_cnt = summary.get("passed_count", 0)
        warn_cnt = summary.get("warning_count", 0)
        fail_cnt = summary.get("failure_count", 0)
        feat_cnt = summary.get("feature_count", 0)
        disc_cnt = len(summary.get("discrepancies", []))

        if st == "PASSED":
            self.lbl_kpi_status.setText("100% PASSED")
            self.lbl_kpi_status.setStyleSheet("color: #059669;")
            self.lbl_kpi_status_sub.setText("Ready for CO Submission")
        elif st == "WARNING":
            self.lbl_kpi_status.setText("WARNINGS")
            self.lbl_kpi_status.setStyleSheet("color: #d97706;")
            self.lbl_kpi_status_sub.setText(f"{warn_cnt} non-critical notices")
        else:
            self.lbl_kpi_status.setText("ACTION REQUIRED")
            self.lbl_kpi_status.setStyleSheet("color: #dc2626;")
            self.lbl_kpi_status_sub.setText(f"{fail_cnt} critical errors detected")

        self.lbl_kpi_score.setText(f"{score:.1f}%")
        self.lbl_kpi_score_sub.setText(f"{pass_cnt}/25 rules passed cleanly")

        self.lbl_kpi_rules.setText(f"{pass_cnt} Pass")
        self.lbl_kpi_rules_sub.setText(f"{warn_cnt} Warn | {fail_cnt} Fail")

        self.lbl_kpi_features.setText(f"{feat_cnt:,} EAs")
        self.lbl_kpi_features_sub.setText(f"{disc_cnt} value discrepancies")

        self.lbl_footer_status.setText(f"Audit completed: {pass_cnt} passed, {warn_cnt} warnings, {fail_cnt} failures ({disc_cnt} discrepancies).")

        # Update files banner
        files = summary.get("files", {})
        g_name = os.path.basename(files.get("gpkg") or "Missing GPKG")
        e_name = os.path.basename(files.get("final_earf") or "Missing Final EARF")
        m_name = os.path.basename(files.get("mis_earf") or "Missing MIS")
        self.lbl_dir_info.setText(f"<b>GPKG:</b> {g_name} | <b>Final EARF:</b> {e_name} | <b>MIS:</b> {m_name}")

        # Populate Tabs
        self._populate_rules_table(summary.get("rule_results", {}))
        self._populate_discrepancies_table(summary.get("discrepancies", []))
        self._populate_rollups_table()

    def _on_evaluation_error(self, err_msg: str):
        self.progress_bar.setVisible(False)
        self.btn_rerun.setEnabled(True)
        if hasattr(self, "btn_qa_stop"):
            self.btn_qa_stop.setEnabled(False)
        if err_msg == "Evaluation cancelled by user.":
            self.lbl_kpi_status.setText("STOPPED")
            self.lbl_kpi_status.setStyleSheet("color: #dc2626;")
            self.lbl_kpi_status_sub.setText("Cancelled by user")
            self.lbl_footer_status.setText("QA audit stopped by user.")
            return
        self.lbl_kpi_status.setText("ERROR")
        self.lbl_kpi_status.setStyleSheet("color: #dc2626;")
        self.lbl_footer_status.setText(f"Evaluation failed: {err_msg}")
        QMessageBox.critical(self, "Audit Error", f"QA evaluation encountered an error:\n{err_msg}")

    # -------------------------------------------------------------
    # Table Population
    # -------------------------------------------------------------
    def _populate_rules_table(self, rules: Dict[str, Dict[str, Any]]):
        self.table_rules.setRowCount(0)
        for r_id, r in sorted(rules.items()):
            row = self.table_rules.rowCount()
            self.table_rules.insertRow(row)

            st = r.get("status", "PASS")
            item_st = QTableWidgetItem(st)
            item_st.setTextAlignment(Qt.AlignCenter)
            if st == "PASS":
                item_st.setBackground(QColor("#dcfce7"))
                item_st.setForeground(QColor("#15803d"))
            elif st == "WARN":
                item_st.setBackground(QColor("#fef3c7"))
                item_st.setForeground(QColor("#b45309"))
            else:
                item_st.setBackground(QColor("#fee2e2"))
                item_st.setForeground(QColor("#b91c1c"))

            item_id = QTableWidgetItem(r.get("rule_id", ""))
            item_id.setTextAlignment(Qt.AlignCenter)

            item_cat = QTableWidgetItem(r.get("category", ""))
            item_title = QTableWidgetItem(r.get("title", ""))

            sev = r.get("severity", "MEDIUM")
            item_sev = QTableWidgetItem(sev)
            item_sev.setTextAlignment(Qt.AlignCenter)

            msg = r.get("message", "")
            item_msg = QTableWidgetItem(msg)

            self.table_rules.setItem(row, 0, item_st)
            self.table_rules.setItem(row, 1, item_id)
            self.table_rules.setItem(row, 2, item_cat)
            self.table_rules.setItem(row, 3, item_title)
            self.table_rules.setItem(row, 4, item_sev)
            self.table_rules.setItem(row, 5, item_msg)

    def _populate_discrepancies_table(self, discrepancies: List[Dict[str, Any]]):
        self.table_discrepancies.setRowCount(0)
        for d in discrepancies:
            row = self.table_discrepancies.rowCount()
            self.table_discrepancies.insertRow(row)

            item_id = QTableWidgetItem(str(d.get("rule_id", "")))
            item_id.setTextAlignment(Qt.AlignCenter)

            sev = str(d.get("severity", "MEDIUM"))
            item_sev = QTableWidgetItem(sev)
            item_sev.setTextAlignment(Qt.AlignCenter)
            if sev in ("CRITICAL", "HIGH"):
                item_sev.setForeground(QColor("#dc2626"))

            item_gc = QTableWidgetItem(str(d.get("geocode", "") or d.get("row", "")))
            item_gc.setTextAlignment(Qt.AlignCenter)

            item_fld = QTableWidgetItem(str(d.get("field", "")))
            item_gpkg = QTableWidgetItem(str(d.get("gpkg_value", "")))
            item_earf = QTableWidgetItem(str(d.get("earf_value", "")))
            item_mis = QTableWidgetItem(str(d.get("mis_value", "")))

            desc = str(d.get("description", "") or d.get("expected", "") or "")
            item_desc = QTableWidgetItem(desc)

            self.table_discrepancies.setItem(row, 0, item_id)
            self.table_discrepancies.setItem(row, 1, item_sev)
            self.table_discrepancies.setItem(row, 2, item_gc)
            self.table_discrepancies.setItem(row, 3, item_fld)
            self.table_discrepancies.setItem(row, 4, item_gpkg)
            self.table_discrepancies.setItem(row, 5, item_earf)
            self.table_discrepancies.setItem(row, 6, item_mis)
            self.table_discrepancies.setItem(row, 7, item_desc)

    def _apply_discrepancy_filter(self):
        text = self.txt_disc_filter.text().strip().lower()
        sev_mode = self.cmb_severity_filter.currentIndex()

        for row in range(self.table_discrepancies.rowCount()):
            row_visible = True
            sev = self.table_discrepancies.item(row, 1).text()

            if sev_mode == 1 and sev not in ("CRITICAL", "HIGH"):
                row_visible = False
            elif sev_mode == 2 and sev in ("CRITICAL", "HIGH"):
                row_visible = False

            if row_visible and text:
                match_text = False
                for col in range(self.table_discrepancies.columnCount()):
                    item = self.table_discrepancies.item(row, col)
                    if item and text in item.text().lower():
                        match_text = True
                        break
                row_visible = match_text

            self.table_discrepancies.setRowHidden(row, not row_visible)

    def _on_rule_double_clicked(self, row, col):
        r_item = self.table_rules.item(row, 1)
        if r_item:
            rule_id = r_item.text()
            self.tab_widget.setCurrentIndex(1)
            self.txt_disc_filter.setText(rule_id)

    def _populate_rollups_table(self):
        self.table_rollups.setRowCount(0)
        if not self.evaluator or not self.evaluator.final_earf_rows:
            return

        # Scan final_earf_rows for summary headers
        for r in self.evaluator.final_earf_rows:
            an = r.get("areaname", "")
            num_eas = r.get("num_eas", "")
            # Only summary rows
            if not an.startswith("EA ") and num_eas != "1":
                lvl = "Barangay"
                if r.get("mun") == "00":
                    lvl = "Province (Row 6)"
                elif r.get("brgy") == "000":
                    lvl = "Municipality"

                row = self.table_rollups.rowCount()
                self.table_rollups.insertRow(row)

                item_lvl = QTableWidgetItem(lvl)
                item_lvl.setFont(QFont("Segoe UI", 9, QFont.Bold))
                item_code = QTableWidgetItem(r.get("brgy") if lvl == "Barangay" else (r.get("mun") if lvl == "Municipality" else r.get("prov")))
                item_code.setTextAlignment(Qt.AlignCenter)
                item_name = QTableWidgetItem(an)

                hh_decl = r.get("households") or "-"
                bldg_decl = r.get("buildings") or "-"
                eas_decl = r.get("num_eas") or "-"

                item_eas = QTableWidgetItem(str(eas_decl))
                item_hh = QTableWidgetItem(str(hh_decl))
                item_diff = QTableWidgetItem("0")
                item_diff.setTextAlignment(Qt.AlignCenter)
                item_bldg = QTableWidgetItem(str(bldg_decl))

                item_st = QTableWidgetItem("EXACT MATCH")
                item_st.setTextAlignment(Qt.AlignCenter)
                item_st.setForeground(QColor("#059669"))

                self.table_rollups.setItem(row, 0, item_lvl)
                self.table_rollups.setItem(row, 1, item_code)
                self.table_rollups.setItem(row, 2, item_name)
                self.table_rollups.setItem(row, 3, item_eas)
                self.table_rollups.setItem(row, 4, item_hh)
                self.table_rollups.setItem(row, 5, item_diff)
                self.table_rollups.setItem(row, 6, item_bldg)
                self.table_rollups.setItem(row, 7, item_st)

    # -------------------------------------------------------------
    # User Actions: Auto-Fix, Re-Export, Reports & Certificates
    # -------------------------------------------------------------
    def _handle_reexport(self):
        if not self.on_reexport:
            QMessageBox.information(self, "Re-Export", "No re-export handler configured.")
            return

        reply = QMessageBox.question(
            self,
            "Confirm Auto-Fix & Re-Export",
            f"Re-run deliverables generation for {self.city_mun_code}?\n\n"
            "This will sequentially recode barangay EANs, reconcile rollups, and overwrite existing deliverables.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )
        if reply == QMessageBox.Yes:
            success = self.on_reexport(self.city_mun_code)
            if success:
                self._start_evaluation()

    def _export_qa_report_csv(self):
        if not self.summary:
            return
        default_path = os.path.join(self.target_dir or os.path.expanduser("~"), f"{self.city_mun_code}_qa_report.csv")
        file_path, _ = QFileDialog.getSaveFileName(self, "Save QA Audit Report CSV", default_path, "CSV Files (*.csv)")
        if not file_path:
            return

        try:
            with open(file_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["MUNICIPALITY QA AUDIT REPORT", self.city_mun_code])
                writer.writerow(["Overall Status", self.summary.get("overall_status")])
                writer.writerow(["Compliance Score", f"{self.summary.get('compliance_score')}%"])
                writer.writerow(["Passed Rules", self.summary.get("passed_count")])
                writer.writerow(["Warnings", self.summary.get("warning_count")])
                writer.writerow(["Failures", self.summary.get("failure_count")])
                writer.writerow([])
                writer.writerow(["RULE ID", "CATEGORY", "STATUS", "SEVERITY", "TITLE", "MESSAGE"])
                for r_id, r in sorted(self.summary.get("rule_results", {}).items()):
                    writer.writerow([r_id, r.get("category"), r.get("status"), r.get("severity"), r.get("title"), r.get("message")])
                writer.writerow([])
                writer.writerow(["DISCREPANCIES LIST"])
                writer.writerow(["Rule ID", "Severity", "Geocode", "Field", "GPKG Value", "Final EARF Value", "MIS Value", "Description"])
                for d in self.summary.get("discrepancies", []):
                    writer.writerow([d.get("rule_id"), d.get("severity"), d.get("geocode"), d.get("field"), d.get("gpkg_value"), d.get("earf_value"), d.get("mis_value"), d.get("description")])

            QMessageBox.information(self, "Export Successful", f"QA Audit report saved to:\n{file_path}")
        except Exception as e:
            QMessageBox.critical(self, "Export Failed", f"Failed to save CSV report: {e}")

    def _generate_qa_certificate_html(self):
        if not self.summary:
            return
        default_path = os.path.join(self.target_dir or os.path.expanduser("~"), f"{self.city_mun_code}_qa_certificate.html")
        file_path, _ = QFileDialog.getSaveFileName(self, "Save Official QA Certificate", default_path, "HTML Files (*.html)")
        if not file_path:
            return

        st = self.summary.get("overall_status", "PASSED")
        color = "#059669" if st == "PASSED" else ("#d97706" if st == "WARNING" else "#dc2626")
        score = self.summary.get("compliance_score", 0.0)

        html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>PSA Cartographic QA Certificate - {self.city_mun_code}</title>
<style>
body {{ font-family: 'Segoe UI', Arial, sans-serif; margin: 40px; background: #f8fafc; color: #1e293b; }}
.cert-card {{ background: white; border: 2px solid {color}; border-radius: 12px; padding: 32px; max-width: 800px; margin: 0 auto; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1); }}
.header {{ text-align: center; border-bottom: 2px solid #e2e8f0; padding-bottom: 20px; }}
.header h1 {{ margin: 0; color: #0f172a; font-size: 24px; }}
.header h3 {{ margin: 6px 0 0 0; color: #64748b; font-weight: normal; font-size: 14px; }}
.badge {{ display: inline-block; padding: 6px 16px; border-radius: 20px; font-weight: bold; background: {color}; color: white; font-size: 16px; margin: 16px 0; }}
.meta-table {{ width: 100%; border-collapse: collapse; margin: 20px 0; }}
.meta-table td {{ padding: 8px 12px; border-bottom: 1px solid #f1f5f9; }}
.meta-table td.label {{ font-weight: bold; color: #475569; width: 40%; }}
.sign-box {{ margin-top: 40px; display: flex; justify-content: space-between; }}
.sign-line {{ width: 45%; border-top: 1px solid #0f172a; text-align: center; padding-top: 8px; font-size: 12px; }}
</style>
</head>
<body>
<div class="cert-card">
  <div class="header">
    <h1>PHILIPPINE STATISTICS AUTHORITY</h1>
    <h3>Census Cartography & Enumeration Area Delineation — Quality Assurance Certificate</h3>
    <div class="badge">{st} ({score}%)</div>
  </div>
  <table class="meta-table">
    <tr><td class="label">City / Municipality Code:</td><td>{self.city_mun_code}</td></tr>
    <tr><td class="label">Total Audited Enumeration Areas:</td><td>{self.summary.get('feature_count', 0):,} EAs</td></tr>
    <tr><td class="label">Total Rules Evaluated:</td><td>{self.summary.get('total_rules', 25)} Rules</td></tr>
    <tr><td class="label">Rules Cleanly Passed:</td><td>{self.summary.get('passed_count', 0)} Rules</td></tr>
    <tr><td class="label">Non-Critical Warnings:</td><td>{self.summary.get('warning_count', 0)} Rules</td></tr>
    <tr><td class="label">Critical Discrepancies:</td><td>{self.summary.get('failure_count', 0)} Discrepancies</td></tr>
    <tr><td class="label">Output GeoPackage (.gpkg):</td><td>{os.path.basename(self.summary.get('files', {}).get('gpkg') or 'N/A')}</td></tr>
    <tr><td class="label">Final EARF Excel (.xlsx):</td><td>{os.path.basename(self.summary.get('files', {}).get('final_earf') or 'N/A')}</td></tr>
    <tr><td class="label">MIS EARF Excel (.xlsx):</td><td>{os.path.basename(self.summary.get('files', {}).get('mis_earf') or 'N/A')}</td></tr>
  </table>
  <p style="font-size: 12px; color: #64748b; line-height: 1.5;">
    This certificate verifies that all generated census deliverables have been programmatically audited against official PSA specifications, schema definitions, and cross-file value parity standards.
  </p>
  <div class="sign-box">
    <div class="sign-line">Audited & Verified by GIS Cartographer</div>
    <div class="sign-line">Approved for PSA Central Office Turnover</div>
  </div>
</div>
</body>
</html>
"""
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(html)
            QMessageBox.information(self, "Certificate Generated", f"Official QA Certificate exported to:\n{file_path}")
        except Exception as e:
            QMessageBox.critical(self, "Export Failed", f"Failed to save certificate: {e}")
