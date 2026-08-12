from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem, QDialog,
    QFormLayout, QSpinBox, QMessageBox, QHeaderView, QComboBox
)
from PyQt6.QtCore import Qt, QTimer
import database as db
from ui.async_loader import AsyncDataLoader, make_progress_bar
from ui.i18n import set_language, t


class StockWidget(QWidget):
    def __init__(self):
        super().__init__()
        self._async_loader = None
        self._search_timer = None
        self._render_timer = None
        self._render_products = []
        self._render_index = 0
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        self.progress_bar = make_progress_bar()
        layout.addWidget(self.progress_bar)
        self._async_loader = AsyncDataLoader(self, self.progress_bar)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self.load_data)
        self._render_timer = QTimer(self)
        self._render_timer.timeout.connect(self._render_stock_chunk)

        toolbar = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Mahsulot qidirish...")
        self.search_edit.setFixedHeight(38)
        self.search_edit.setStyleSheet(
            "border:1px solid #d1d5db;border-radius:6px;padding:0 12px;font-size:13px;background:white;"
        )
        self.search_edit.textChanged.connect(self._queue_search)
        toolbar.addWidget(self.search_edit)

        self.template_filter = QComboBox()
        self.template_filter.setFixedHeight(38)
        self.template_filter.setMinimumWidth(180)
        self.template_filter.setStyleSheet(
            "border:1px solid #d1d5db;border-radius:6px;padding:0 10px;font-size:13px;background:white;"
        )
        self.template_filter.currentIndexChanged.connect(lambda _: self.load_data())
        toolbar.addWidget(self.template_filter)
        toolbar.addStretch()

        incoming_btn = QPushButton("+ Kirim qo'shish")
        incoming_btn.setFixedHeight(38)
        incoming_btn.setStyleSheet(
            "background:#059669;color:white;border:none;border-radius:6px;padding:0 16px;font-weight:bold;font-size:13px;"
        )
        incoming_btn.clicked.connect(self._add_stock)
        toolbar.addWidget(incoming_btn)
        layout.addLayout(toolbar)

        self.stats_lbl = QLabel("")
        self.stats_lbl.setStyleSheet("color:#475569;font-size:13px;font-weight:bold;")
        layout.addWidget(self.stats_lbl)

        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Mahsulot", "Qoldiq", "Amal"])
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(1, 140)
        self.table.setColumnWidth(2, 150)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet(
            "QTableWidget{background:white;border:1px solid #e2e8f0;border-radius:8px;font-size:13px;}"
            " QTableWidget::item{padding:7px 10px;}"
            " QHeaderView::section{background:#f8fafc;border:none;border-bottom:1px solid #e2e8f0;padding:8px;font-weight:bold;color:#64748b;}"
            " QTableWidget::item:alternate{background:#f8fafc;}"
        )
        self.table.verticalHeader().setDefaultSectionSize(56)
        layout.addWidget(self.table)
        self._load_template_filter()

    def load_data(self):
        query = self.search_edit.text() if hasattr(self, "search_edit") else ""
        if self.isVisible():
            self._async_loader.start(
                lambda: (db.search_products(query) if query else db.get_all_products(), db.get_templates()),
                self._apply_loaded_data,
            )
            return
        self._apply_loaded_data((db.search_products(query) if query else db.get_all_products(), db.get_templates()))

    def _apply_loaded_data(self, data):
        products, templates = data
        self._load_template_filter(templates)
        template_filter = self.template_filter.currentData() if hasattr(self, "template_filter") else None
        if template_filter == "none":
            products = [product for product in products if not product["template_id"]]
        elif template_filter:
            products = [product for product in products if product["template_id"] == template_filter]
        self._stats_counts = (len(products), sum(product["stock"] or 0 for product in products))
        self._update_stats_label()
        self._render_products = list(products)
        self._render_index = 0
        self.table.setRowCount(0)
        self.table.setUpdatesEnabled(False)
        if self.progress_bar:
            self.progress_bar.setVisible(True)
        self._render_timer.start(0)

    def _render_stock_chunk(self):
        if not self._render_products and self._render_index == 0:
            self._render_timer.stop()
            self.table.setUpdatesEnabled(True)
            set_language(self, self.property("app_language") or "uz")
            if self.progress_bar:
                QTimer.singleShot(150, lambda: self.progress_bar.setVisible(False))
            return
        batch_size = 30
        end = min(self._render_index + batch_size, len(self._render_products))
        for row in range(self._render_index, end):
            product = self._render_products[row]
            self.table.insertRow(row)
            name_item = QTableWidgetItem(product["name"])
            name_item.setData(Qt.ItemDataRole.UserRole, dict(product))
            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, QTableWidgetItem(f"{product['stock']}"))

            btn = QPushButton("+ Kirim")
            btn.setMinimumWidth(94)
            btn.setFixedHeight(30)
            btn.setStyleSheet(
                "background:#ecfdf5;color:#065f46;border:1px solid #6ee7b7;"
                "border-radius:6px;padding:4px 10px;font-size:12px;font-weight:bold;"
            )
            btn.clicked.connect(lambda _, r=row: self._quick_add(r))

            actions_widget = QWidget()
            actions_layout = QHBoxLayout(actions_widget)
            actions_layout.setContentsMargins(10, 4, 10, 4)
            actions_layout.addStretch()
            actions_layout.addWidget(btn)
            actions_layout.addStretch()
            self.table.setCellWidget(row, 2, actions_widget)
            self.table.setRowHeight(row, 56)
        self._render_index = end
        if self._render_index >= len(self._render_products):
            self._render_timer.stop()
            self.table.setUpdatesEnabled(True)
            set_language(self, self.property("app_language") or "uz")
            if self.progress_bar:
                QTimer.singleShot(150, lambda: self.progress_bar.setVisible(False))

    def _queue_search(self, *_args):
        self._search_timer.start()

    def _load_template_filter(self, templates=None):
        current = self.template_filter.currentData() if hasattr(self, "template_filter") else None
        self.template_filter.blockSignals(True)
        self.template_filter.clear()
        self.template_filter.addItem("Barcha templatelar", None)
        self.template_filter.addItem("Template tanlanmagan", "none")
        for template in templates if templates is not None else db.get_templates():
            self.template_filter.addItem(template["name"], template["id"])
        if current is not None:
            index = self.template_filter.findData(current)
            if index >= 0:
                self.template_filter.setCurrentIndex(index)
        self.template_filter.blockSignals(False)
        set_language(self.template_filter, self.property("app_language") or "uz")

    def _update_stats_label(self):
        type_count, total_stock = getattr(self, "_stats_counts", (0, 0))
        language = self.property("app_language") or "uz"
        unit = t("ta", language)
        self.stats_lbl.setText(
            f"{t('Turlar', language)}: {type_count} {unit}  |  "
            f"{t('Jami qoldiq', language)}: {total_stock} {unit}"
        )

    def _language_changed(self, _language):
        if hasattr(self, "stats_lbl"):
            self._update_stats_label()

    def _add_stock(self):
        dlg = StockInDialog(self)
        if dlg.exec():
            self.load_data()

    def _quick_add(self, row):
        item = self.table.item(row, 0)
        if not item:
            return
        product = item.data(Qt.ItemDataRole.UserRole)
        dlg = StockInDialog(self, product)
        if dlg.exec():
            self.load_data()


class StockInDialog(QDialog):
    def __init__(self, parent=None, product=None):
        super().__init__(parent)
        self.language = (parent.property("app_language") if parent else None) or "uz"
        self.setWindowTitle(t("Kirim qo'shish", self.language))
        self.setFixedWidth(380)
        self.setStyleSheet("QDialog{background:white;} QLabel{color:#374151;font-size:13px;}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)

        form = QFormLayout()
        form.setSpacing(10)
        self.product_combo = QComboBox()
        self.product_combo.setStyleSheet("border:1px solid #d1d5db;border-radius:6px;padding:6px 10px;font-size:13px;")
        for product_row in db.get_all_products():
            self.product_combo.addItem(product_row["name"], product_row["id"])
        if product:
            idx = self.product_combo.findData(product["id"])
            if idx >= 0:
                self.product_combo.setCurrentIndex(idx)
        form.addRow("Mahsulot:", self.product_combo)

        self.qty_spin = QSpinBox()
        self.qty_spin.setRange(1, 999999)
        self.qty_spin.setValue(1)
        self.qty_spin.setStyleSheet("border:1px solid #d1d5db;border-radius:6px;padding:6px 10px;font-size:13px;")
        form.addRow("Miqdor:", self.qty_spin)

        self.note_edit = QLineEdit()
        self.note_edit.setPlaceholderText("Ixtiyoriy izoh")
        self.note_edit.setStyleSheet("border:1px solid #d1d5db;border-radius:6px;padding:6px 10px;font-size:13px;")
        form.addRow("Izoh:", self.note_edit)
        layout.addLayout(form)

        btn_row = QHBoxLayout()
        cancel_btn = QPushButton("Bekor")
        cancel_btn.setStyleSheet("border:1px solid #d1d5db;border-radius:6px;padding:8px 16px;color:#6b7280;background:white;")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("Kirim qo'shish")
        save_btn.setStyleSheet("background:#059669;color:white;border:none;border-radius:6px;padding:8px 16px;font-weight:bold;")
        save_btn.clicked.connect(self._save)
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)
        set_language(self, self.language)

    def _save(self):
        product_id = self.product_combo.currentData()
        quantity = self.qty_spin.value()
        note = self.note_edit.text()
        try:
            db.add_stock(product_id, quantity, note)
        except db.AppError as exc:
            QMessageBox.warning(self, t("Saqlanmadi", self.language), str(exc))
            return
        success_message = str(quantity) + " " + t("ta mahsulot qo'shildi!", self.language)
        QMessageBox.information(
            self,
            t("Muvaffaqiyat", self.language),
            success_message,
        )
        self.accept()
