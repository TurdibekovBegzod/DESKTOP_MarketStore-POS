from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QFrame, QSpinBox,
    QComboBox
)
from PyQt6.QtCore import Qt
import database as db
from ui.async_loader import AsyncDataLoader, make_progress_bar
from ui.i18n import set_language, t


class CheckingWidget(QWidget):
    def __init__(self, user=None):
        super().__init__()
        self.user = user
        self.session = None
        self._async_loader = None
        self._sections = []
        self._build_ui()
        self.load_data()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        self.progress_bar = make_progress_bar()
        layout.addWidget(self.progress_bar)
        self._async_loader = AsyncDataLoader(self, self.progress_bar)

        toolbar = QHBoxLayout()
        toolbar.addStretch()

        self.check_toggle_btn = QPushButton()
        self.check_toggle_btn.setProperty("i18n_skip", True)
        self.check_toggle_btn.setFixedHeight(38)
        self.check_toggle_btn.clicked.connect(self._toggle_checking)
        toolbar.addWidget(self.check_toggle_btn)
        layout.addLayout(toolbar)

        scan_frame = QFrame()
        scan_frame.setStyleSheet("background:white;border:1px solid #e2e8f0;border-radius:8px;")
        scan_layout = QHBoxLayout(scan_frame)
        scan_layout.setContentsMargins(12, 10, 12, 10)
        scan_layout.setSpacing(10)
        self.section_combo = QComboBox()
        self.section_combo.setMinimumWidth(170)
        self.section_combo.setFixedHeight(38)
        self.section_combo.setStyleSheet(self._input_style())
        self.section_combo.currentIndexChanged.connect(self._section_filter_changed)
        scan_layout.addWidget(self.section_combo)
        self.template_combo = QComboBox()
        self.template_combo.setMinimumWidth(170)
        self.template_combo.setFixedHeight(38)
        self.template_combo.setStyleSheet(self._input_style())
        self.template_combo.currentIndexChanged.connect(self.load_data)
        scan_layout.addWidget(self.template_combo)
        scan_layout.addWidget(QLabel("Shtrix-kod:"))
        self.barcode_edit = QLineEdit()
        self.barcode_edit.setPlaceholderText("Skaner bilan o'qing yoki barcode kiriting...")
        self.barcode_edit.setFixedHeight(38)
        self.barcode_edit.setStyleSheet(self._input_style())
        self.barcode_edit.returnPressed.connect(self._scan_barcode)
        scan_layout.addWidget(self.barcode_edit)
        scan_layout.addWidget(QLabel("Miqdor:"))
        self.qty_spin = QSpinBox()
        self.qty_spin.setRange(1, 999999)
        self.qty_spin.setValue(1)
        self.qty_spin.setFixedHeight(38)
        self.qty_spin.setFixedWidth(110)
        self.qty_spin.setStyleSheet(self._input_style())
        scan_layout.addWidget(self.qty_spin)
        self.scan_btn = QPushButton("Tekshirish")
        self.scan_btn.setFixedHeight(38)
        self.scan_btn.setStyleSheet(self._button_style("#eff6ff", "#1d4ed8", "#93c5fd", "#3b82f6", hover_fg="white"))
        self.scan_btn.clicked.connect(self._scan_barcode)
        scan_layout.addWidget(self.scan_btn)
        layout.addWidget(scan_frame)

        tables_row = QHBoxLayout()
        tables_row.setSpacing(12)

        checked_panel = self._panel("Tekshiruvdan o'tganlar")
        checked_layout = checked_panel.layout()
        self.checked_table = self._create_table(include_time=True)
        checked_layout.addWidget(self.checked_table)
        tables_row.addWidget(checked_panel, 1)

        unchecked_panel = self._panel("Tekshiruvdan o'tmaganlar")
        unchecked_layout = unchecked_panel.layout()
        self.unchecked_table = self._create_table(include_time=False)
        unchecked_layout.addWidget(self.unchecked_table)
        tables_row.addWidget(unchecked_panel, 1)

        layout.addLayout(tables_row, 1)

    def _panel(self, title):
        frame = QFrame()
        frame.setStyleSheet("QFrame{background:white;border:1px solid #e2e8f0;border-radius:8px;}")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)
        label = QLabel(title)
        label.setStyleSheet("font-size:13px;font-weight:bold;color:#1e293b;border:none;background:transparent;")
        layout.addWidget(label)
        return frame

    def _create_table(self, include_time=False):
        table = QTableWidget()
        headers = ["Mahsulot", "Shtrix-kod", "Qoldiq"]
        if include_time:
            headers.extend(["Sanaldi", "Qoldi", "Vaqt"])
        else:
            headers.extend(["Sanaldi", "Qoldi"])
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, len(headers)):
            table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
        table.setColumnWidth(1, 130)
        table.setColumnWidth(2, 80)
        if include_time:
            table.setColumnWidth(3, 80)
            table.setColumnWidth(4, 80)
            table.setColumnWidth(5, 145)
        else:
            table.setColumnWidth(3, 80)
            table.setColumnWidth(4, 80)
        table.verticalHeader().setDefaultSectionSize(42)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.setStyleSheet(self._table_style())
        return table

    def load_data(self):
        section_id = self._selected_section_id()
        template_id = self._selected_template_id()
        if self.isVisible():
            self._async_loader.start(lambda: self._fetch_checking_data(section_id, template_id), self._apply_loaded_data)
            return
        self._apply_loaded_data(self._fetch_checking_data(section_id, template_id))

    def _fetch_checking_data(self, section_id=None, template_id=None):
        session = db.get_active_inventory_check()
        sections = [dict(section) for section in db.get_product_sections()]
        templates = [dict(template) for template in db.get_templates(section_id)]
        if not session:
            preview_rows = self._filtered_product_preview_rows(section_id, template_id)
            return {"session": None, "counts": None, "checked": [], "unchecked": preview_rows, "sections": sections, "templates": templates}
        session_id = session["id"]
        return {
            "session": session,
            "counts": db.get_inventory_check_counts(session_id, section_id, template_id),
            "checked": db.get_inventory_check_items(session_id, True, section_id, template_id),
            "unchecked": db.get_inventory_check_items(session_id, False, section_id, template_id),
            "sections": sections,
            "templates": templates,
        }

    def _apply_loaded_data(self, data):
        self.session = data["session"]
        active = bool(self.session)
        self._load_filter_combos(data.get("sections", []), data.get("templates", []))
        self._update_toggle_button(active)
        self.barcode_edit.setEnabled(active)
        self.qty_spin.setEnabled(active)
        self.scan_btn.setEnabled(active)
        if active:
            self._fill_table(self.checked_table, data["checked"], True)
            self._fill_table(self.unchecked_table, data["unchecked"], False)
            self.barcode_edit.setFocus()
        else:
            self.checked_table.setRowCount(0)
            self._fill_table(self.unchecked_table, data["unchecked"], False)
        set_language(self, self.property("app_language") or "uz")
        self._update_toggle_button(active)

    def _filtered_product_preview_rows(self, section_id=None, template_id=None):
        products = db.get_all_products(section_id=section_id)
        rows = []
        for product in products:
            if template_id is not None and product["template_id"] != template_id:
                continue
            stock = product["stock"] or 0
            if stock <= 0:
                continue
            rows.append({
                "product_id": product["id"],
                "product_name": product["name"],
                "barcode": product["barcode"],
                "expected_stock": stock,
                "checked_quantity": 0,
                "checked_at": None,
            })
        return rows

    def _load_filter_combos(self, sections, templates):
        language = self.property("app_language") or "uz"
        current_section = self._selected_section_id()
        current_template = self._selected_template_id()
        self.section_combo.blockSignals(True)
        self.section_combo.clear()
        self.section_combo.addItem(t("Barcha bo'limlar", language), None)
        for section in sections:
            self.section_combo.addItem(section["name"], section["id"])
        if current_section is not None:
            index = self.section_combo.findData(current_section)
            if index >= 0:
                self.section_combo.setCurrentIndex(index)
        self.section_combo.blockSignals(False)

        self.template_combo.blockSignals(True)
        self.template_combo.clear()
        self.template_combo.addItem(t("Barcha templatelar", language), None)
        for template in templates:
            self.template_combo.addItem(template["name"], template["id"])
        if current_template is not None:
            index = self.template_combo.findData(current_template)
            if index >= 0:
                self.template_combo.setCurrentIndex(index)
        self.template_combo.blockSignals(False)

    def _selected_section_id(self):
        return self.section_combo.currentData() if hasattr(self, "section_combo") else None

    def _selected_template_id(self):
        return self.template_combo.currentData() if hasattr(self, "template_combo") else None

    def _section_filter_changed(self):
        self._load_template_combo_for_section()
        self.load_data()

    def _load_template_combo_for_section(self):
        language = self.property("app_language") or "uz"
        self.template_combo.blockSignals(True)
        self.template_combo.clear()
        self.template_combo.addItem(t("Barcha templatelar", language), None)
        for template in db.get_templates(self._selected_section_id()):
            self.template_combo.addItem(template["name"], template["id"])
        self.template_combo.blockSignals(False)

    def _update_toggle_button(self, active):
        language = self.property("app_language") or "uz"
        if active:
            self.check_toggle_btn.setText(t("Tekshiruvni to'xtatish", language))
            self.check_toggle_btn.setStyleSheet(self._button_style("#059669", "white", "#059669", "#047857"))
        else:
            self.check_toggle_btn.setText(t("Tekshirishni boshlash", language))
            self.check_toggle_btn.setStyleSheet(self._button_style("#3b82f6", "white", "#3b82f6", "#2563eb"))

    def _language_changed(self, _language):
        self._update_toggle_button(bool(self.session))

    def _fill_table(self, table, rows, include_time):
        table.setRowCount(0)
        for row_index, item in enumerate(rows):
            table.insertRow(row_index)
            values = [
                item["product_name"] or "",
                item["barcode"] or "",
                f"{item['expected_stock'] or 0}",
            ]
            if include_time:
                expected = item["expected_stock"] or 0
                checked = item["checked_quantity"] or 0
                values.extend([
                    f"{checked}",
                    f"{max(0, expected - checked)}",
                    item["checked_at"] or "",
                ])
            else:
                expected = item["expected_stock"] or 0
                checked = item["checked_quantity"] or 0
                values.extend([
                    f"{checked}",
                    f"{max(0, expected - checked)}",
                ])
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                if column in (2, 3, 4):
                    cell.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                table.setItem(row_index, column, cell)
            table.setRowHeight(row_index, 42)

    def _start_checking(self):
        language = self.property("app_language") or "uz"
        try:
            db.start_inventory_check(self.user["id"] if self.user else None)
            self.load_data()
        except db.AppError as exc:
            QMessageBox.warning(self, t("Checking boshlanmadi", language), str(exc))

    def _toggle_checking(self):
        if self.session:
            self._finish_checking()
        else:
            self._start_checking()

    def _finish_checking(self):
        if not self.session:
            return
        language = self.property("app_language") or "uz"
        counts = db.get_inventory_check_counts(self.session["id"])
        unchecked = counts["unchecked_count"] or 0
        unchecked_quantity = counts["unchecked_quantity"] or 0
        message = t("Checking jarayoni tugatilsinmi?", language)
        if unchecked:
            unchecked_label = t("Tekshiruvdan o'tmagan mahsulotlar", language)
            message += (
                f"\n\n{unchecked_label}: "
                f"{unchecked} {t('xil turdagi', language)} {unchecked_quantity} {t('ta', language)}"
            )
        reply = QMessageBox.question(
            self,
            t("Jarayonni tugatish", language),
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            result = db.finish_inventory_check(self.session["id"])
            QMessageBox.information(
                self,
                t("Checking tugadi", language),
                f"{t('Jami', language)}: {result['total'] or 0} {t('tur', language)}, "
                f"{result['total_quantity'] or 0} {t('ta', language)}\n"
                f"{t('Tekshirildi', language)}: {result['checked_count'] or 0} {t('tur', language)}, "
                f"{result['checked_quantity'] or 0} {t('ta', language)}\n"
                f"{t('Tekshirilmagan', language)}: {result['unchecked_count'] or 0} "
                f"{t('xil turdagi', language)} {result['unchecked_quantity'] or 0} {t('ta', language)}",
            )
            self.load_data()
        except db.AppError as exc:
            QMessageBox.warning(self, t("Tugallanmadi", language), str(exc))

    def _scan_barcode(self):
        language = self.property("app_language") or "uz"
        if not self.session:
            QMessageBox.warning(self, t("Checking yo'q", language), t("Avval tekshirishni boshlang.", language))
            return
        barcode = self.barcode_edit.text().strip()
        try:
            item = db.mark_inventory_product_checked(
                self.session["id"],
                barcode,
                self.qty_spin.value(),
                self._selected_section_id(),
                self._selected_template_id(),
            )
            self.barcode_edit.clear()
            self.qty_spin.setValue(1)
            self.load_data()
        except db.AppError as exc:
            QMessageBox.warning(self, t("Tekshirilmadi", language), str(exc))
            self.barcode_edit.selectAll()
            self.barcode_edit.setFocus()

    def _input_style(self):
        return """
            QLineEdit, QSpinBox, QComboBox { border:1px solid #d1d5db;border-radius:6px;
                                  padding:0 12px;font-size:13px;background:white; }
            QLineEdit:focus, QSpinBox:focus, QComboBox:focus { border-color:#3b82f6; }
            QComboBox::drop-down { border:none;width:26px; }
        """

    def _button_style(self, bg, fg, border, hover, hover_fg="white"):
        return f"""
            QPushButton {{ background:{bg};color:{fg};border:1px solid {border};
                          border-radius:6px;padding:0 14px;font-size:13px;font-weight:bold; }}
            QPushButton:hover {{ background:{hover};color:{hover_fg};border-color:{hover}; }}
            QPushButton:pressed {{ background:#1e293b;color:white;padding-top:2px; }}
            QPushButton:disabled {{ background:#f1f5f9;color:#94a3b8;border-color:#e2e8f0; }}
        """

    def _table_style(self):
        return """
            QTableWidget{background:white;border:1px solid #e2e8f0;border-radius:8px;font-size:13px;}
            QTableWidget::item{padding:7px 10px;}
            QTableWidget::item:selected{background:#dbeafe;color:#1e40af;}
            QHeaderView::section{background:#f8fafc;border:none;border-bottom:1px solid #e2e8f0;padding:8px;font-weight:bold;color:#64748b;}
            QTableWidget::item:alternate{background:#f8fafc;}
        """
