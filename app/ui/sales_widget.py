from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QComboBox,
    QFrame, QMessageBox, QHeaderView, QSpinBox, QDoubleSpinBox,
    QDialog, QFormLayout, QCheckBox
)
from PyQt6.QtCore import Qt, pyqtSignal, QRegularExpression, QTimer
from PyQt6.QtGui import QFont, QColor, QPixmap, QRegularExpressionValidator
from pathlib import Path
import sys
import database as db
from ui.async_loader import AsyncDataLoader, make_progress_bar
from ui.i18n import set_language, t


def resource_path(relative_path):
    base_path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return str(base_path / relative_path)


SHOPPING_CART_ICON_PATH = resource_path("images/shopping-cart.png")


class CurrencyDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.language = (parent.property("app_language") if parent else None) or "uz"
        self.setWindowTitle("Valyuta kurslari")
        self.setFixedWidth(520)
        self._build_ui()
        self._apply_language()
        self.load_data()

    def _build_ui(self):
        self.setStyleSheet("""
            QDialog { background: white; }
            QLabel { color: #374151; font-size: 13px; }
            QLineEdit, QDoubleSpinBox {
                border: 1px solid #d1d5db; border-radius: 6px;
                padding: 7px 10px; font-size: 13px; background: white;
            }
            QTableWidget { background: white; border: 1px solid #e2e8f0; border-radius: 8px; }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)

        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Kod", "Nomi", "1 birlik = so'm"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.table)

        form = QFormLayout()
        self.code_edit = QLineEdit()
        self.code_edit.setPlaceholderText("USD")
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("AQSh dollari")
        self.rate_spin = QDoubleSpinBox()
        self.rate_spin.setRange(0.0001, 999999999)
        self.rate_spin.setDecimals(4)
        self.rate_spin.setSuffix(" so'm")
        form.addRow("Kod:", self.code_edit)
        form.addRow("Nomi:", self.name_edit)
        form.addRow("Kurs:", self.rate_spin)
        layout.addLayout(form)

        btn_row = QHBoxLayout()
        save_btn = QPushButton("Saqlash")
        save_btn.setStyleSheet("background:#3b82f6;color:white;border:none;border-radius:6px;padding:8px 16px;font-weight:bold;")
        save_btn.clicked.connect(self._save_currency)
        delete_btn = QPushButton("O'chirish")
        delete_btn.clicked.connect(self._delete_currency)
        close_btn = QPushButton("Yopish")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(save_btn)
        btn_row.addWidget(delete_btn)
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        self.table.itemSelectionChanged.connect(self._fill_selected)

    def _apply_language(self):
        self.setWindowTitle(t("Valyuta kurslari", self.language))
        self.rate_spin.setSuffix(" " + t("so'm", self.language))
        set_language(self, self.language)

    def load_data(self):
        self.table.setRowCount(0)
        for row, currency in enumerate(db.get_currencies()):
            self.table.insertRow(row)
            code_item = QTableWidgetItem(currency["code"])
            code_item.setData(Qt.ItemDataRole.UserRole, dict(currency))
            self.table.setItem(row, 0, code_item)
            self.table.setItem(row, 1, QTableWidgetItem(currency["name"]))
            rate_item = QTableWidgetItem(f"{currency['rate_to_uzs']:,.4f}")
            rate_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row, 2, rate_item)

    def _selected_currency(self):
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _fill_selected(self):
        currency = self._selected_currency()
        if not currency:
            return
        self.code_edit.setText(currency["code"])
        self.name_edit.setText(currency["name"])
        self.rate_spin.setValue(currency["rate_to_uzs"])
        self.code_edit.setEnabled(currency["code"] != "UZS")

    def _save_currency(self):
        try:
            db.save_currency(self.code_edit.text(), self.name_edit.text(), self.rate_spin.value())
            self.code_edit.setEnabled(True)
            self.load_data()
        except db.AppError as exc:
            QMessageBox.warning(self, "Saqlanmadi", str(exc))

    def _delete_currency(self):
        currency = self._selected_currency()
        if not currency:
            return
        try:
            db.delete_currency(currency["code"])
            self.code_edit.clear()
            self.name_edit.clear()
            self.code_edit.setEnabled(True)
            self.load_data()
        except db.AppError as exc:
            QMessageBox.warning(self, "O'chirilmadi", str(exc))


class SaleCustomerDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.language = (parent.property("app_language") if parent else None) or "uz"
        self.setWindowTitle("Mijoz ma'lumoti")
        self.setFixedWidth(380)
        self._build_ui()
        self._apply_language()

    def _build_ui(self):
        self.setStyleSheet("""
            QDialog { background: white; }
            QLabel { color: #374151; font-size: 13px; }
            QCheckBox { color: #1e293b; font-size: 13px; font-weight: 600; }
            QLineEdit {
                border: 1px solid #d1d5db; border-radius: 6px;
                padding: 8px 10px; font-size: 13px; background: white;
            }
            QLineEdit:disabled {
                background: #f1f5f9; color: #94a3b8;
            }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        info = QLabel("Mijoz ma'lumotini sotuv arxivida saqlash ixtiyoriy.")
        info.setWordWrap(True)
        layout.addWidget(info)

        self.customer_check = QCheckBox("Mijoz nomi va telefonini kiritish")
        self.customer_check.toggled.connect(self._toggle_customer_fields)
        layout.addWidget(self.customer_check)

        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Ism-familya")
        self.phone_edit = QLineEdit()
        self.phone_edit.setPlaceholderText("+998 XX XXX XX XX")
        form.addRow("Mijoz:", self.name_edit)
        form.addRow("Telefon:", self.phone_edit)
        layout.addLayout(form)

        btns = QHBoxLayout()
        cancel_btn = QPushButton("Bekor")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("OK")
        save_btn.setStyleSheet("background:#3b82f6;color:white;border:none;border-radius:6px;padding:8px 16px;font-weight:bold;")
        save_btn.clicked.connect(self.accept)
        btns.addStretch()
        btns.addWidget(cancel_btn)
        btns.addWidget(save_btn)
        layout.addLayout(btns)
        self._toggle_customer_fields(False)

    def _toggle_customer_fields(self, enabled):
        self.name_edit.setEnabled(enabled)
        self.phone_edit.setEnabled(enabled)
        if enabled:
            self.name_edit.setFocus()

    def get_data(self):
        if not self.customer_check.isChecked():
            return {"name": None, "phone": None}
        return {
            "name": self.name_edit.text().strip() or None,
            "phone": self.phone_edit.text().strip() or None,
        }

    def _apply_language(self):
        self.setWindowTitle(t("Mijoz ma'lumoti", self.language))
        set_language(self, self.language)


class ProductInfoDialog(QDialog):
    def __init__(self, parent=None, product=None, show_cost=True):
        super().__init__(parent)
        self.product = product or {}
        self.show_cost = show_cost
        self.language = (parent.property("app_language") if parent else None) or "uz"
        self.setWindowTitle(t("Mahsulot ma'lumotlari", self.language))
        self.setMinimumWidth(520)
        self._build_ui()
        set_language(self, self.language)

    def _build_ui(self):
        self.setStyleSheet("""
            QDialog { background: white; }
            QLabel { color: #374151; font-size: 13px; }
            QTableWidget { background: white; border: 1px solid #e2e8f0; border-radius: 8px; font-size: 13px; }
            QHeaderView::section { background: #f8fafc; border: none; border-bottom: 1px solid #e2e8f0;
                                   padding: 7px; font-weight: bold; color: #64748b; }
            QPushButton { background: #3b82f6; color: white; border: none; border-radius: 6px;
                          padding: 8px 18px; font-weight: bold; }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(12)

        title = QLabel(self.product.get("name") or "")
        title.setStyleSheet("font-size:16px;font-weight:bold;color:#1e293b;")
        title.setWordWrap(True)
        layout.addWidget(title)

        form = QFormLayout()
        form.setSpacing(8)
        for label, value in self._info_rows():
            value_lbl = QLabel(str(value or "-"))
            value_lbl.setWordWrap(True)
            form.addRow(t(label, self.language), value_lbl)
        layout.addLayout(form)

        attrs = db.get_product_attribute_details(self.product.get("id")) if self.product.get("id") else []
        attrs_title = QLabel(t("Atributlar", self.language))
        attrs_title.setStyleSheet("font-size:14px;font-weight:bold;color:#1e293b;margin-top:4px;")
        layout.addWidget(attrs_title)

        table = QTableWidget()
        table.setColumnCount(2)
        table.setHorizontalHeaderLabels([t("Nomi", self.language), t("Qiymat", self.language)])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setRowCount(max(1, len(attrs)))
        if attrs:
            for row, attr in enumerate(attrs):
                table.setItem(row, 0, QTableWidgetItem(attr["name"]))
                table.setItem(row, 1, QTableWidgetItem(attr["value"] or ""))
        else:
            table.setItem(0, 0, QTableWidgetItem(t("Ma'lumot yo'q", self.language)))
            table.setItem(0, 1, QTableWidgetItem(""))
        table.setMinimumHeight(140)
        layout.addWidget(table)

        close_row = QHBoxLayout()
        close_row.addStretch()
        close_btn = QPushButton("Yopish")
        close_btn.clicked.connect(self.accept)
        close_row.addWidget(close_btn)
        layout.addLayout(close_row)

    def _info_rows(self):
        product = self.product
        money_unit = t("so'm", self.language)
        price_currency = product.get("price_currency") or "UZS"
        cost_currency = product.get("cost_currency") or "UZS"
        price_original = product.get("price_original") or product.get("price") or 0
        cost_original = product.get("cost_original") or product.get("cost") or 0
        rows = [
            ("Shtrix-kod:", product.get("barcode") or "-"),
            ("Template:", product.get("template_name") or "-"),
            ("Ta'minotchi:", product.get("supplier_name") or "-"),
            ("Narx:", f"{product.get('price') or 0:,.0f} {money_unit} ({price_original:,.4f} {price_currency})"),
            ("Qoldiq:", product.get("stock") or 0),
            ("Jarayonda:", product.get("process_quantity") or 0),
        ]
        if self.show_cost:
            rows.insert(4, (
                "Xarid narxi:",
                f"{product.get('cost') or 0:,.0f} {money_unit} ({cost_original:,.4f} {cost_currency})",
            ))
        return rows


class SalesWidget(QWidget):
    def __init__(self, user):
        super().__init__()
        self.user = user
        self.cart = []
        self._async_loader = None
        self._search_timer = None
        self._render_timer = None
        self._render_products = []
        self._render_index = 0
        self._render_generation = 0
        self._build_ui()
        self.load_data()

    def _build_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(16, 16, 16, 16)
        root_layout.setSpacing(12)

        self.progress_bar = make_progress_bar()
        root_layout.addWidget(self.progress_bar)
        self._async_loader = AsyncDataLoader(self, self.progress_bar)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self._run_product_search)
        self._render_timer = QTimer(self)
        self._render_timer.timeout.connect(self._render_product_chunk)

        layout = QHBoxLayout()
        layout.setSpacing(16)
        root_layout.addLayout(layout, 1)

        # ── Left: Search + Products ───────────────────────
        left = QVBoxLayout()
        search_row = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Mahsulot nomi yoki shtrix-kod... Skaner Enter yuborsa savatga qo'shiladi")
        self.search_edit.setFixedHeight(40)
        self.search_edit.setStyleSheet(self._input_style())
        self.search_edit.textChanged.connect(self._search_products)
        self.search_edit.returnPressed.connect(self._scan_barcode)
        search_row.addWidget(self.search_edit)
        left.addLayout(search_row)

        self.products_table = QTableWidget()
        self.products_table.setColumnCount(5)
        self.products_table.setHorizontalHeaderLabels(["Nomi", "Shtrix-kod", "Narx", "Qoldiq", ""])
        self.products_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.products_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.products_table.setColumnWidth(4, 58)
        self.products_table.verticalHeader().setDefaultSectionSize(44)
        self.products_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.products_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.products_table.setAlternatingRowColors(True)
        self.products_table.setStyleSheet(self._table_style())
        self.products_table.doubleClicked.connect(self._show_product_from_table)
        left.addWidget(self.products_table)
        layout.addLayout(left, 3)

        # ── Right: Cart + Payment ─────────────────────────
        right = QVBoxLayout()
        right.setSpacing(10)

        cart_title_row = QHBoxLayout()
        cart_title_row.setSpacing(6)
        cart_icon = QLabel()
        cart_icon.setObjectName("salesCartIcon")
        cart_icon.setFixedSize(18, 18)
        cart_pixmap = QPixmap(SHOPPING_CART_ICON_PATH)
        if not cart_pixmap.isNull():
            cart_icon.setPixmap(cart_pixmap.scaled(
                18,
                18,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
        cart_lbl = QLabel("Savat")
        cart_lbl.setObjectName("salesCartTitle")
        cart_lbl.setStyleSheet("font-size: 15px; font-weight: bold; color: #1e293b;")
        cart_title_row.addWidget(cart_icon, 0, Qt.AlignmentFlag.AlignVCenter)
        cart_title_row.addWidget(cart_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
        cart_title_row.addStretch()
        right.addLayout(cart_title_row)

        self.cart_table = QTableWidget()
        self.cart_table.setColumnCount(5)
        self.cart_table.setHorizontalHeaderLabels(["Nomi", "Narx", "Miqdor", "Jami", ""])
        self.cart_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column, width in [(1, 92), (2, 104), (3, 110), (4, 54)]:
            self.cart_table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self.cart_table.setColumnWidth(column, width)
        self.cart_table.verticalHeader().setDefaultSectionSize(46)
        self.cart_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.cart_table.setStyleSheet(self._table_style())
        self.cart_table.doubleClicked.connect(self._show_product_from_cart)
        right.addWidget(self.cart_table)

        # Discount
        disc_row = QHBoxLayout()
        disc_lbl = QLabel("Chegirma:")
        disc_lbl.setFixedWidth(70)
        self.discount_edit = QLineEdit()
        self.discount_edit.setPlaceholderText("0.00")
        self.discount_edit.setValidator(QRegularExpressionValidator(QRegularExpression(r"^\d*([.,]\d{0,2})?$"), self))
        self.discount_edit.setMinimumHeight(40)
        self.discount_edit.setMinimumWidth(120)
        self.discount_edit.setStyleSheet(self._input_style())
        self.discount_edit.textChanged.connect(self._update_totals)
        self.discount_currency_combo = QComboBox()
        self.discount_currency_combo.setMinimumHeight(40)
        self.discount_currency_combo.setMinimumWidth(86)
        self.discount_currency_combo.setStyleSheet(self._input_style())
        self.discount_currency_combo.currentIndexChanged.connect(self._update_totals)
        disc_row.addWidget(disc_lbl)
        disc_row.addWidget(self.discount_edit)
        disc_row.addWidget(self.discount_currency_combo)
        right.addLayout(disc_row)

        # Totals card
        totals_frame = QFrame()
        totals_frame.setStyleSheet("""
            QFrame { background: white; border: 1px solid #e2e8f0;
                     border-radius: 10px; padding: 4px; }
        """)
        totals_layout = QVBoxLayout(totals_frame)
        totals_layout.setSpacing(6)

        self.subtotal_lbl = QLabel("")
        self.subtotal_lbl.setStyleSheet("color: #1e293b; font-size: 20px; font-weight: bold;")

        totals_layout.addWidget(self.subtotal_lbl)
        right.addWidget(totals_frame)

        # Payment method and cashier selector
        pay_row = QHBoxLayout()
        pay_row.setSpacing(10)
        pay_lbl = QLabel("To'lov:")
        pay_lbl.setFixedWidth(55)
        self.payment_combo = QComboBox()
        self.payment_combo.addItems(["Naqd", "Plastik karta"])
        self.payment_combo.setStyleSheet(self._input_style())
        self.payment_combo.currentIndexChanged.connect(self._on_payment_changed)
        pay_row.addWidget(pay_lbl)
        pay_row.addWidget(self.payment_combo, 1)

        cashier_lbl = QLabel("Kassir:")
        cashier_lbl.setFixedWidth(55)
        self.cashier_combo = QComboBox()
        self.cashier_combo.setStyleSheet(self._input_style())
        pay_row.addWidget(cashier_lbl)
        pay_row.addWidget(self.cashier_combo, 1)
        right.addLayout(pay_row)

        currency_row = QHBoxLayout()
        currency_lbl = QLabel("Valyuta:")
        currency_lbl.setFixedWidth(55)
        self.currency_combo = QComboBox()
        self.currency_combo.setStyleSheet(self._input_style())
        self.currency_combo.currentIndexChanged.connect(self._on_currency_changed)
        currency_btn = QPushButton("Kurslar")
        currency_btn.setFixedHeight(34)
        currency_btn.setStyleSheet("""
            QPushButton { background: white; color: #1e293b; border: 1px solid #d1d5db;
                          border-radius: 6px; padding: 0 10px; font-size: 12px; }
            QPushButton:hover { background: #f8fafc; }
        """)
        currency_btn.clicked.connect(self._manage_currencies)
        currency_row.addWidget(currency_lbl)
        currency_row.addWidget(self.currency_combo)
        currency_row.addWidget(currency_btn)
        right.addLayout(currency_row)

        self.currency_total_lbl = QLabel("")
        self.currency_total_lbl.setStyleSheet("color: #64748b; font-size: 12px;")
        right.addWidget(self.currency_total_lbl)

        # Action buttons
        sell_btn = QPushButton("Sotishni yakunlash")
        sell_btn.setObjectName("complete_sale_btn")
        sell_btn.setFixedHeight(48)
        sell_btn.setStyleSheet("""
            QPushButton { background: #059669; color: white; font-size: 15px;
                          font-weight: bold; border: none; border-radius: 8px; }
            QPushButton:hover { background: #047857; }
            QPushButton:pressed { background: #065f46; padding-top: 3px; }
        """)
        sell_btn.clicked.connect(self._complete_sale)
        right.addWidget(sell_btn)

        clear_btn = QPushButton("Savatni tozalash")
        clear_btn.setObjectName("clear_cart_btn")
        clear_btn.setFixedHeight(36)
        clear_btn.setStyleSheet("""
            QPushButton { background: transparent; color: #ef4444; font-size: 13px;
                          border: 1px solid #ef4444; border-radius: 6px; }
            QPushButton:hover { background: #ef4444; color: white; }
            QPushButton:pressed { background: #991b1b; color: white; padding-top: 3px; }
        """)
        clear_btn.clicked.connect(self._clear_cart)
        right.addWidget(clear_btn)

        layout.addLayout(right, 2)

    def load_data(self):
        query = self.search_edit.text() if hasattr(self, "search_edit") else ""
        if self.isVisible():
            self._async_loader.start(
                lambda: (
                    db.search_products(query) if query else db.get_all_products(),
                    [dict(currency) for currency in db.get_currencies()],
                    [dict(user) for user in db.get_users()],
                ),
                self._apply_loaded_data,
            )
            return
        self._apply_loaded_data((
            db.search_products(query) if query else db.get_all_products(),
            [dict(currency) for currency in db.get_currencies()],
            [dict(user) for user in db.get_users()],
        ))

    def refresh_from_sync(self, changed_tables=()):
        relevant = {
            "users", "currencies", "products", "product_attributes",
            "product_sections", "sales", "sale_items", "sale_returns",
            "stock_movements",
        }
        changed = set(changed_tables or ())
        if changed and changed.isdisjoint(relevant):
            return
        self.load_data()

    def _apply_loaded_data(self, data):
        products, currencies, users = data
        self._load_products(products=products)
        self._load_currencies(currencies)
        self._load_cashiers(users)
        set_language(self, self.property("app_language") or "uz")

    def _load_products(self, query="", products=None):
        if products is None:
            products = db.search_products(query) if query else db.get_all_products()

        self._render_products = [
            product for product in products
            if self._available_stock(product) > 0
        ]
        self._render_index = 0
        self._render_generation += 1
        self.products_table.setRowCount(0)
        self.products_table.setUpdatesEnabled(False)
        if self.progress_bar:
            self.progress_bar.setVisible(True)
        self._render_timer.start(0)

    def _render_product_chunk(self):
        if not self._render_products and self._render_index == 0:
            self._render_timer.stop()
            self.products_table.setUpdatesEnabled(True)
            set_language(self, self.property("app_language") or "uz")
            if self.progress_bar:
                QTimer.singleShot(150, lambda: self.progress_bar.setVisible(False))
            return
        batch_size = 25
        end = min(self._render_index + batch_size, len(self._render_products))
        for row in range(self._render_index, end):
            p = self._render_products[row]
            available_stock = self._available_stock(p)
            self.products_table.insertRow(row)
            self.products_table.setItem(row, 0, QTableWidgetItem(p["name"]))
            self.products_table.setItem(row, 1, QTableWidgetItem(p["barcode"] or ""))
            price_item = QTableWidgetItem(f"{p['price']:,.0f} so'm")
            price_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.products_table.setItem(row, 2, price_item)
            stock_item = QTableWidgetItem(f"{available_stock}")
            self.products_table.setItem(row, 3, stock_item)

            add_btn = QPushButton("+")
            add_btn.setToolTip("Savatga qo'shish")
            add_btn.setEnabled(available_stock > 0)
            add_btn.setFixedSize(25, 25)
            add_btn.setStyleSheet(self._add_button_style())
            add_btn.clicked.connect(lambda _, r=row: self._add_to_cart(r))
            add_wrap = QWidget()
            add_layout = QHBoxLayout(add_wrap)
            add_layout.setContentsMargins(0, 0, 0, 0)
            add_layout.setSpacing(0)
            add_layout.addWidget(add_btn, 0, Qt.AlignmentFlag.AlignCenter)
            self.products_table.setCellWidget(row, 4, add_wrap)
            self.products_table.setRowHeight(row, 44)
            self.products_table.item(row, 0).setData(Qt.ItemDataRole.UserRole, dict(p))
        self._render_index = end
        if self._render_index >= len(self._render_products):
            self._render_timer.stop()
            self.products_table.setUpdatesEnabled(True)
            set_language(self, self.property("app_language") or "uz")
            if self.progress_bar:
                QTimer.singleShot(150, lambda: self.progress_bar.setVisible(False))

    def _load_currencies(self, currencies=None):
        current = self.currency_combo.currentData() if hasattr(self, "currency_combo") else None
        discount_current = self.discount_currency_combo.currentData() if hasattr(self, "discount_currency_combo") else None
        preferred_code = db.get_app_settings(self.user.get("id")).get("currency", "UZS")
        currencies = currencies if currencies is not None else [dict(currency) for currency in db.get_currencies()]
        self.currency_combo.blockSignals(True)
        self.currency_combo.clear()
        for currency in currencies:
            self.currency_combo.addItem(f"{currency['code']} - {currency['name']}", dict(currency))
        selected_code = current["code"] if current else preferred_code
        idx = self.currency_combo.findText(selected_code, Qt.MatchFlag.MatchStartsWith)
        if idx >= 0:
            self.currency_combo.setCurrentIndex(idx)
        self.currency_combo.blockSignals(False)

        self.discount_currency_combo.blockSignals(True)
        self.discount_currency_combo.clear()
        for currency in currencies:
            self.discount_currency_combo.addItem(currency["code"], dict(currency))
        discount_code = discount_current["code"] if discount_current else preferred_code
        idx = self.discount_currency_combo.findText(discount_code, Qt.MatchFlag.MatchStartsWith)
        if idx >= 0:
            self.discount_currency_combo.setCurrentIndex(idx)
        self.discount_currency_combo.blockSignals(False)
        self._on_currency_changed()

    def _load_cashiers(self, users=None):
        current_id = self.cashier_combo.currentData() if hasattr(self, "cashier_combo") else None
        users = users if users is not None else [dict(user) for user in db.get_users()]
        current_user = dict(self.user)
        current_user_id = current_user.get("id")
        db_current_user = next((user for user in users if user.get("id") == current_user_id), None)
        if db_current_user:
            current_user.update(db_current_user)
        visible_users = []
        seen_ids = set()
        if current_user_id:
            visible_users.append(current_user)
            seen_ids.add(current_user_id)
        for user in users:
            user_id = user.get("id")
            username = (user.get("username") or "").strip().lower()
            email = (user.get("email") or "").strip().lower()
            u_role = user.get("role")
            if user_id in seen_ids:
                continue
            if u_role == "admin":
                continue
            if username == "admin" and email in ("", "admin@gmail.com"):
                continue
            visible_users.append(user)
            if user_id:
                seen_ids.add(user_id)
        users = sorted(
            visible_users,
            key=lambda user: (
                0 if user.get("id") == current_user_id else 1,
                0 if user.get("role") == "admin" else 1,
                self._cashier_display_name(user).lower(),
            ),
        )
        self.cashier_combo.blockSignals(True)
        self.cashier_combo.clear()
        for user in users:
            self.cashier_combo.addItem(self._cashier_display_name(user), user.get("id"))
        idx = self.cashier_combo.findData(current_id) if current_id is not None else -1
        if idx < 0 and self.cashier_combo.count():
            idx = 0
        if idx >= 0:
            self.cashier_combo.setCurrentIndex(idx)
        self.cashier_combo.blockSignals(False)

    def _cashier_display_name(self, user):
        username = (user.get("username") or "").strip()
        if username and "@" not in username:
            return username
        name = (username or user.get("email") or "User").strip()
        if "@" in name:
            name = name.split("@", 1)[0]
        name = name.replace(".", " ").replace("_", " ")
        return " ".join(part.capitalize() for part in name.split()) or "User"

    def _selected_cashier_id(self):
        cashier_id = self.cashier_combo.currentData() if hasattr(self, "cashier_combo") else None
        return cashier_id or self.user["id"]

    def _selected_currency(self):
        return self.currency_combo.currentData() or {"code": "UZS", "rate_to_uzs": 1}

    def _selected_discount_currency(self):
        return self.discount_currency_combo.currentData() or {"code": "UZS", "rate_to_uzs": 1}

    def _amount_from_line_edit(self, edit):
        text = edit.text().strip().replace(" ", "").replace(",", ".")
        try:
            return max(0, float(text)) if text else 0
        except ValueError:
            return 0

    def _discount_value_uzs(self):
        currency = self._selected_discount_currency()
        return self._amount_from_line_edit(self.discount_edit) * (currency["rate_to_uzs"] or 1)

    def _format_amount(self, value):
        return f"{value:.2f}".rstrip("0").rstrip(".")

    def _on_currency_changed(self):
        self._update_totals()

    def _on_payment_changed(self):
        self._update_totals()

    def _language(self):
        return self.property("app_language") or "uz"

    def _money_unit(self):
        return t("so'm", self._language())

    def _language_changed(self, language):
        self.setProperty("app_language", language)
        self._update_totals()

    def _manage_currencies(self):
        dlg = CurrencyDialog(self)
        dlg.exec()
        self.load_data()

    def _search_products(self, text):
        self._pending_search_text = text
        self._search_timer.start()

    def _run_product_search(self):
        text = getattr(self, "_pending_search_text", self.search_edit.text())
        if self.isVisible():
            self._async_loader.start(
                lambda: db.search_products(text) if text else db.get_all_products(),
                lambda products: self._load_products(products=products),
            )
            return
        self._load_products(text)

    def _scan_barcode(self):
        barcode = self.search_edit.text().strip()
        if not barcode:
            return
        product = db.get_product_by_barcode(barcode)
        if not product:
            QMessageBox.warning(self, "Topilmadi", f"'{barcode}' shtrix-kodli mahsulot topilmadi.")
            return
        self._add_product_to_cart(dict(product))
        self.search_edit.clear()
        self.search_edit.setFocus()

    def _add_to_cart_from_table(self, index):
        self._add_to_cart(index.row())

    def _show_product_from_table(self, index):
        item = self.products_table.item(index.row(), 0)
        product = item.data(Qt.ItemDataRole.UserRole) if item else None
        if product:
            self._show_product_info(product)

    def _show_product_from_cart(self, index):
        if 0 <= index.row() < len(self.cart):
            product = db.get_product_by_id(self.cart[index.row()]["product_id"])
            if product:
                self._show_product_info(dict(product))

    def _show_product_info(self, product):
        dlg = ProductInfoDialog(self, product, show_cost=False)
        dlg.exec()

    def _add_to_cart(self, row):
        item = self.products_table.item(row, 0)
        if not item:
            return
        product = item.data(Qt.ItemDataRole.UserRole)
        if not product:
            return
        self._add_product_to_cart(product)

    def _add_product_to_cart(self, product):
        available_stock = self._available_stock(product)
        if available_stock <= 0:
            QMessageBox.warning(self, "Qoldiq yo'q", "Bu mahsulot omborda qolmagan.")
            return

        # Check if already in cart
        for cart_item in self.cart:
            if cart_item["product_id"] == product["id"]:
                if cart_item["quantity"] >= cart_item["stock"]:
                    QMessageBox.warning(
                        self, "Qoldiq yetarli emas",
                        f"{product['name']} uchun omborda {cart_item['stock']} ta bor."
                    )
                    return
                cart_item["quantity"] += 1
                cart_item["subtotal"] = cart_item["quantity"] * cart_item["price"]
                self._refresh_cart()
                return

        self.cart.append({
            "product_id": product["id"],
            "name": product["name"],
            "price": product["price"],
            "quantity": 1,
            "subtotal": product["price"],
            "stock": available_stock,
        })
        self._refresh_cart()

    @staticmethod
    def _available_stock(product):
        stock = product.get("stock", 0) or 0
        process_quantity = product.get("process_quantity", 0) or 0
        return max(0, stock - process_quantity)

    def _refresh_cart(self):
        self.cart_table.setRowCount(0)
        for row, item in enumerate(self.cart):
            self.cart_table.insertRow(row)
            name_item = QTableWidgetItem(item["name"])
            name_item.setData(Qt.ItemDataRole.UserRole, item["product_id"])
            self.cart_table.setItem(row, 0, name_item)
            self.cart_table.setItem(row, 1, QTableWidgetItem(f"{item['price']:,.0f}"))

            qty_spin = QSpinBox()
            qty_spin.setRange(1, item["stock"])
            qty_spin.setValue(item["quantity"])
            qty_spin.setMinimumHeight(34)
            qty_spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
            qty_spin.setStyleSheet(self._qty_style())
            qty_spin.valueChanged.connect(lambda val, r=row: self._update_qty(r, val))
            self.cart_table.setCellWidget(row, 2, qty_spin)

            subtotal_item = QTableWidgetItem(f"{item['subtotal']:,.0f}")
            subtotal_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.cart_table.setItem(row, 3, subtotal_item)

            del_btn = QPushButton("✕")
            del_btn.setFixedSize(30, 30)
            del_btn.setStyleSheet(self._delete_button_style())
            del_btn.clicked.connect(lambda _, r=row: self._remove_from_cart(r))
            self.cart_table.setCellWidget(row, 4, del_btn)
            self.cart_table.setRowHeight(row, 46)

        self._update_totals()

    def _update_qty(self, row, val):
        if row < len(self.cart):
            self.cart[row]["quantity"] = val
            self.cart[row]["subtotal"] = val * self.cart[row]["price"]
            subtotal_item = self.cart_table.item(row, 3)
            if subtotal_item:
                subtotal_item.setText(f"{self.cart[row]['subtotal']:,.0f}")
            self._update_totals()

    def _remove_from_cart(self, row):
        if row < len(self.cart):
            self.cart.pop(row)
            self._refresh_cart()

    def _update_totals(self):
        subtotal = sum(i["subtotal"] for i in self.cart)
        discount = self._discount_value_uzs()
        if discount > subtotal:
            discount_currency = self._selected_discount_currency()
            rate = discount_currency["rate_to_uzs"] or 1
            self.discount_edit.blockSignals(True)
            self.discount_edit.setText(self._format_amount(subtotal / rate))
            self.discount_edit.blockSignals(False)
            discount = subtotal
        total = max(0, subtotal - discount)
        language = self._language()
        money_unit = self._money_unit()
        self.subtotal_lbl.setText(f"{t('Jami', language)}: {total:,.0f} {money_unit}")
        currency = self._selected_currency()
        rate = currency["rate_to_uzs"] or 1
        currency_by_label = t("bo'yicha:", language)
        self.currency_total_lbl.setText(
            f"{currency['code']} {currency_by_label} {total / rate:,.2f} {currency['code']} "
            f"({t('kurs:', language)} {rate:,.2f} {money_unit})"
        )

    def _complete_sale(self):
        if not self.cart:
            QMessageBox.warning(self, t("Xatolik", self._language()), t("Savat bo'sh!", self._language()))
            return

        subtotal = sum(i["subtotal"] for i in self.cart)
        discount = min(self._discount_value_uzs(), subtotal)
        total = max(0, subtotal - discount)
        currency = self._selected_currency()
        rate = currency["rate_to_uzs"] or 1
        payment = self.payment_combo.currentText().lower()

        customer_id, customer_name, customer_phone = None, None, None
        customer_dlg = SaleCustomerDialog(self)
        if customer_dlg.exec() != QDialog.DialogCode.Accepted:
            return
        customer_data = customer_dlg.get_data()
        customer_name = customer_data["name"]
        customer_phone = customer_data["phone"]

        effective_paid = total
        effective_paid_original = total / rate

        try:
            sale_id = db.create_sale(
                customer_id=customer_id,
                cashier_id=self._selected_cashier_id(),
                items=self.cart,
                total=subtotal,
                discount=discount,
                paid=effective_paid,
                payment_method=payment,
                currency_code=currency["code"],
                exchange_rate=rate,
                paid_original=effective_paid_original,
                customer_name=customer_name,
                customer_phone=customer_phone,
            )
        except db.AppError as exc:
            QMessageBox.warning(self, t("Sotuv yakunlanmadi", self._language()), str(exc))
            self._load_products(self.search_edit.text())
            return

        language = self._language()
        money_unit = self._money_unit()
        pay_label = t("To'lash:", language)
        payment_label = t("To'lov turi:", language)
        msg = (
            f"{t('Sotuv', language)} #{db.get_sale_display_no(sale_id)} {t('muvaffaqiyatli!', language)}\n\n"
            f"{t('Jami', language)}: {subtotal:,.0f} {money_unit}\n"
            f"{t('Chegirma:', language)} {discount:,.0f} {money_unit}\n"
            f"{pay_label} {total:,.0f} {money_unit}\n"
            f"{payment_label} {self.payment_combo.currentText()}\n"
            f"{t('Valyuta:', language)} {effective_paid_original:,.2f} {currency['code']}"
        )
        QMessageBox.information(self, t("Sotuv yakunlandi", language), msg)
        self._clear_cart()
        self._load_products()

    def _clear_cart(self):
        self.cart.clear()
        self.cart_table.setRowCount(0)
        self.discount_edit.clear()
        self._update_totals()

    def _input_style(self):
        return """
            QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox {
                background: white; border: 1px solid #d1d5db;
                border-radius: 6px; padding: 6px 10px;
                font-size: 14px; color: #1e293b; min-height: 28px;
            }
            QLineEdit:focus, QComboBox:focus { border-color: #3b82f6; }
        """

    def _add_button_style(self):
        return """
            QPushButton {
                background: #3b82f6; color: white; border: none;
                border-radius: 6px; padding: 0;
                font-size: 15px; font-weight: bold;
            }
            QPushButton:hover { background: #2563eb; }
            QPushButton:pressed {
                background: #1d4ed8;
                padding-top: 2px;
            }
            QPushButton:disabled { background: #cbd5e1; color: #64748b; }
        """

    def _qty_style(self):
        return """
            QSpinBox {
                background: white; color: #1e293b;
                border: 1px solid #cbd5e1; border-radius: 6px;
                padding: 3px 8px; font-size: 12px; font-weight: 600;
            }
            QSpinBox:focus { border-color: #3b82f6; }
            QSpinBox::up-button, QSpinBox::down-button { width: 20px; }
        """

    def _delete_button_style(self):
        return """
            QPushButton {
                background: #fee2e2; color: #ef4444; border: none;
                border-radius: 6px; font-weight: bold; font-size: 13px;
            }
            QPushButton:hover { background: #ef4444; color: white; }
            QPushButton:pressed {
                background: #991b1b;
                color: white;
                padding-top: 3px;
            }
        """

    def _table_style(self):
        return """
            QTableWidget { background: white; border: 1px solid #e2e8f0;
                           border-radius: 8px; gridline-color: #f1f5f9;
                           font-size: 13px; }
            QTableWidget::item { padding: 6px 8px; }
            QTableWidget::item:selected { background: #dbeafe; color: #1e40af; }
            QHeaderView::section { background: #f8fafc; border: none;
                                   border-bottom: 1px solid #e2e8f0;
                                   padding: 8px; font-weight: bold; color: #64748b; }
            QTableWidget::item:alternate { background: #f8fafc; }
        """
