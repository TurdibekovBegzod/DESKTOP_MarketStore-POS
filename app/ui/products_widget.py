from datetime import date, timedelta
from pathlib import Path
import sys

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QDialog,
    QFormLayout, QComboBox, QSpinBox, QDoubleSpinBox,
    QMessageBox, QHeaderView, QFrame, QCheckBox, QScrollArea, QTabWidget,
    QDateEdit, QCalendarWidget, QGridLayout, QMenu, QSizePolicy
)
from PyQt6.QtCore import Qt, QRectF, QSize, QSizeF, QMarginsF, QRegularExpression, QTimer, QDate
from PyQt6.QtGui import (
    QColor, QPainter, QPen, QFont, QPageSize, QPageLayout,
    QDoubleValidator, QIcon, QRegularExpressionValidator
)
from PyQt6.QtPrintSupport import QPrinter, QPrintDialog
import database as db
from ui.async_loader import AsyncDataLoader, make_progress_bar
from ui.i18n import set_language, t
from ui.sales_widget import ProductInfoDialog


def resource_path(relative_path):
    base_path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return str(base_path / relative_path)


COPY_ICON_PATH = resource_path("images/copy.png")
TRASH_ICON_PATH = resource_path("images/trash.png")
LEFT_ARROW_ICON_PATH = resource_path("images/left-arrow.png")


def _row_value(row, key, default=None):
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


CODE128_PATTERNS = [
    "212222", "222122", "222221", "121223", "121322", "131222", "122213", "122312", "132212", "221213",
    "221312", "231212", "112232", "122132", "122231", "113222", "123122", "123221", "223211", "221132",
    "221231", "213212", "223112", "312131", "311222", "321122", "321221", "312212", "322112", "322211",
    "212123", "212321", "232121", "111323", "131123", "131321", "112313", "132113", "132311", "211313",
    "231113", "231311", "112133", "112331", "132131", "113123", "113321", "133121", "313121", "211331",
    "231131", "213113", "213311", "213131", "311123", "311321", "331121", "312113", "312311", "332111",
    "314111", "221411", "431111", "111224", "111422", "121124", "121421", "141122", "141221", "112214",
    "112412", "122114", "122411", "142112", "142211", "241211", "221114", "413111", "241112", "134111",
    "111242", "121142", "121241", "114212", "124112", "124211", "411212", "421112", "421211", "212141",
    "214121", "412121", "111143", "111341", "131141", "114113", "114311", "411113", "411311", "113141",
    "114131", "311141", "411131", "211412", "211214", "211232", "2331112",
]


def _code128_values(text):
    values = [104]
    for char in text:
        code = ord(char)
        if code < 32 or code > 126:
            raise ValueError("Barcode faqat oddiy harf/raqamlardan iborat bo'lishi kerak.")
        values.append(code - 32)
    checksum = values[0]
    for index, value in enumerate(values[1:], start=1):
        checksum += index * value
    values.append(checksum % 103)
    values.append(106)
    return values


class ProductSectionDialog(QDialog):
    def __init__(self, parent=None, section=None):
        super().__init__(parent)
        self.section = section
        self.language = (parent.property("app_language") if parent else None) or "uz"
        self.setWindowTitle(t("Bo'lim qo'shish" if not section else "Bo'limni tahrirlash", self.language))
        self.setFixedWidth(360)
        self._build_ui()
        set_language(self, self.language)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        self.name_edit = QLineEdit(self.section["name"] if self.section else "")
        self.name_edit.setPlaceholderText(t("Bo'lim nomi", self.language))
        self.name_edit.setStyleSheet("border:1px solid #d1d5db;border-radius:6px;padding:8px 10px;background:white;")
        layout.addWidget(self.name_edit)

        btns = QHBoxLayout()
        cancel_btn = QPushButton(t("Bekor", self.language))
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton(t("Saqlash", self.language))
        save_btn.setStyleSheet("background:#3b82f6;color:white;border:none;border-radius:6px;padding:8px 16px;font-weight:bold;")
        save_btn.clicked.connect(self._save)
        btns.addStretch()
        btns.addWidget(cancel_btn)
        btns.addWidget(save_btn)
        layout.addLayout(btns)

    def _save(self):
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, t("Xatolik", self.language), t("Bo'lim nomini kiriting!", self.language))
            return
        self.accept()


class SectionTitleWidget(QWidget):
    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self._text = text
        self.setObjectName("productsSectionTitle")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAutoFillBackground(False)
        self.setMinimumHeight(28)

    def setText(self, text):
        self._text = text or ""
        self.updateGeometry()
        self.update()

    def text(self):
        return self._text

    def sizeHint(self):
        return QSize(220, 30)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        font = QFont("Segoe UI", 18)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#1e293b"))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._text)
        painter.end()


class TemplateDialog(QDialog):
    def __init__(self, parent=None, template=None):
        super().__init__(parent)
        self.template = template
        self.language = (parent.property("app_language") if parent else None) or "uz"
        self.setWindowTitle(t("Template qo'shish" if not template else "Template tahrirlash", self.language))
        self.setFixedWidth(520)
        self._build_ui()
        set_language(self, self.language)

    def _build_ui(self):
        self.setStyleSheet("""
            QDialog { background: white; }
            QLabel { color: #374151; font-size: 13px; }
            QLineEdit { border: 1px solid #d1d5db; border-radius: 6px; padding: 7px 10px; }
            QTableWidget { border: 1px solid #e2e8f0; border-radius: 8px; }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)

        self.name_edit = QLineEdit(self.template["name"] if self.template else "")
        self.name_edit.setPlaceholderText("Masalan: Telefon, Kiyim, Elektronika")
        layout.addWidget(QLabel("Template nomi"))
        layout.addWidget(self.name_edit)

        layout.addWidget(QLabel("Hususiyatlar"))
        self.fields_table = QTableWidget()
        self.fields_table.setColumnCount(2)
        self.fields_table.setHorizontalHeaderLabels(["Nomi", "Majburiy"])
        self.fields_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.fields_table.setMinimumHeight(180)
        layout.addWidget(self.fields_table)

        field_btns = QHBoxLayout()
        add_field_btn = QPushButton("+ Hususiyat")
        add_field_btn.clicked.connect(self._add_field_row)
        remove_field_btn = QPushButton("O'chirish")
        remove_field_btn.clicked.connect(self._remove_field_row)
        field_btns.addWidget(add_field_btn)
        field_btns.addWidget(remove_field_btn)
        field_btns.addStretch()
        layout.addLayout(field_btns)

        btn_row = QHBoxLayout()
        cancel_btn = QPushButton("Bekor")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("Saqlash")
        save_btn.setStyleSheet("background:#3b82f6;color:white;border:none;border-radius:6px;padding:8px 18px;font-weight:bold;")
        save_btn.clicked.connect(self._save)
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

        if self.template:
            for field in db.get_template_fields(self.template["id"]):
                self._add_field_row(field["name"], bool(field["required"]))
        else:
            self._add_field_row("Brend", False)
            self._add_field_row("Model", False)

    def _add_field_row(self, name="", required=False):
        row = self.fields_table.rowCount()
        self.fields_table.insertRow(row)
        self.fields_table.setItem(row, 0, QTableWidgetItem(name))
        check = QCheckBox()
        check.setChecked(required)
        self.fields_table.setCellWidget(row, 1, check)

    def _remove_field_row(self):
        row = self.fields_table.currentRow()
        if row >= 0:
            self.fields_table.removeRow(row)

    def _save(self):
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, t("Xatolik", self.language), t("Template nomini kiriting!", self.language))
            return
        if not self.get_fields():
            QMessageBox.warning(self, t("Xatolik", self.language), t("Kamida bitta hususiyat kiriting!", self.language))
            return
        self.accept()

    def get_fields(self):
        fields = []
        seen = set()
        for row in range(self.fields_table.rowCount()):
            item = self.fields_table.item(row, 0)
            name = item.text().strip() if item else ""
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())
            check = self.fields_table.cellWidget(row, 1)
            fields.append({"name": name, "field_type": "text", "required": check.isChecked() if check else False})
        return fields


class TemplateManagerDialog(QDialog):
    def __init__(self, parent=None, section_id=None):
        super().__init__(parent)
        self.section_id = section_id
        self.language = (parent.property("app_language") if parent else None) or "uz"
        self.setWindowTitle(t("Product templatelar", self.language))
        self.setFixedSize(560, 420)
        self._build_ui()
        set_language(self, self.language)
        self.load_data()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        top = QHBoxLayout()
        add_btn = QPushButton("+ Template")
        add_btn.clicked.connect(self._add_template)
        edit_btn = QPushButton("Tahrir")
        edit_btn.clicked.connect(self._edit_template)
        del_btn = QPushButton("O'chirish")
        del_btn.clicked.connect(self._delete_template)
        top.addWidget(add_btn)
        top.addWidget(edit_btn)
        top.addWidget(del_btn)
        top.addStretch()
        layout.addLayout(top)

        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Nomi", "Hususiyatlar", "ID"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnHidden(2, True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.table)

        close_btn = QPushButton("Yopish")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)

    def load_data(self):
        self.table.setRowCount(0)
        for row, template in enumerate(db.get_templates(self.section_id)):
            fields = db.get_template_fields(template["id"])
            self.table.insertRow(row)
            name_item = QTableWidgetItem(template["name"])
            name_item.setData(Qt.ItemDataRole.UserRole, dict(template))
            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, QTableWidgetItem(", ".join(f["name"] for f in fields)))
            self.table.setItem(row, 2, QTableWidgetItem(str(template["id"])))

    def _selected_template(self):
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _add_template(self):
        dlg = TemplateDialog(self)
        if dlg.exec():
            try:
                db.add_template(dlg.name_edit.text().strip(), dlg.get_fields(), self.section_id)
                self.load_data()
            except db.AppError as exc:
                QMessageBox.warning(self, t("Saqlanmadi", self.language), t(str(exc), self.language))

    def _edit_template(self):
        template = self._selected_template()
        if not template:
            return
        dlg = TemplateDialog(self, template)
        if dlg.exec():
            try:
                db.update_template(template["id"], dlg.name_edit.text().strip(), dlg.get_fields())
                self.load_data()
            except db.AppError as exc:
                QMessageBox.warning(self, t("Saqlanmadi", self.language), t(str(exc), self.language))

    def _delete_template(self):
        template = self._selected_template()
        if not template:
            return
        reply = QMessageBox.question(
            self, "O'chirish", f"'{template['name']}' template o'chirilsinmi?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                db.delete_template(template["id"])
                self.load_data()
            except db.AppError as exc:
                QMessageBox.warning(self, "O'chirilmadi", str(exc))


class ProductCategoryDialog(QDialog):
    def __init__(self, parent=None, category=None):
        super().__init__(parent)
        self.category = category
        self.setWindowTitle("Kategoriya qo'shish" if not category else "Kategoriya tahrirlash")
        self.setFixedWidth(320)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        self.name_edit = QLineEdit(self.category["name"] if self.category else "")
        self.name_edit.setPlaceholderText("Kategoriya nomi")
        self.name_edit.setStyleSheet("border:1px solid #d1d5db;border-radius:6px;padding:7px 10px;background:white;")
        layout.addWidget(self.name_edit)

        btns = QHBoxLayout()
        cancel_btn = QPushButton("Bekor")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("Saqlash")
        save_btn.setStyleSheet("background:#3b82f6;color:white;border:none;border-radius:6px;padding:8px 16px;font-weight:bold;")
        save_btn.clicked.connect(self._save)
        btns.addStretch()
        btns.addWidget(cancel_btn)
        btns.addWidget(save_btn)
        layout.addLayout(btns)

    def _save(self):
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "Xatolik", "Kategoriya nomini kiriting!")
            return
        self.accept()


class ProductCategoryManagerDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Mahsulot kategoriyalari")
        self.setMinimumSize(520, 420)
        self._build_ui()
        self.load_data()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        toolbar = QHBoxLayout()
        add_btn = QPushButton("+ Kategoriya")
        add_btn.clicked.connect(self._add_category)
        toolbar.addWidget(add_btn)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        self.table = QTableWidget()
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["Nomi", "Amallar"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(1, 210)
        self.table.verticalHeader().setDefaultSectionSize(54)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table)

    def load_data(self):
        self.table.setRowCount(0)
        for row, category in enumerate(db.get_categories()):
            self.table.insertRow(row)
            item = QTableWidgetItem(category["name"])
            item.setData(Qt.ItemDataRole.UserRole, dict(category))
            self.table.setItem(row, 0, item)
            self.table.setCellWidget(row, 1, self._actions_widget(row))
            self.table.setRowHeight(row, 54)

    def _actions_widget(self, row):
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(6)
        edit_btn = QPushButton("✏ Tahrir")
        delete_btn = QPushButton("🗑 O'chir")
        edit_btn.setFixedSize(92, 30)
        delete_btn.setFixedSize(96, 30)
        edit_btn.setStyleSheet(self._state_button("#fff7ed", "#9a3412", "#fdba74", "#fb923c"))
        delete_btn.setStyleSheet(self._state_button("#fef2f2", "#991b1b", "#fca5a5", "#dc2626"))
        edit_btn.clicked.connect(lambda _, r=row: self._edit_category(r))
        delete_btn.clicked.connect(lambda _, r=row: self._delete_category(r))
        layout.addWidget(edit_btn)
        layout.addWidget(delete_btn)
        return widget

    def _category_at_row(self, row):
        item = self.table.item(row, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _add_category(self):
        dlg = ProductCategoryDialog(self)
        if dlg.exec():
            try:
                db.add_category(dlg.name_edit.text().strip())
                self.load_data()
            except db.AppError as exc:
                QMessageBox.warning(self, "Saqlanmadi", str(exc))

    def _edit_category(self, row):
        category = self._category_at_row(row)
        if not category:
            return
        dlg = ProductCategoryDialog(self, category)
        if dlg.exec():
            try:
                db.update_category(category["id"], dlg.name_edit.text().strip())
                self.load_data()
            except db.AppError as exc:
                QMessageBox.warning(self, "Saqlanmadi", str(exc))

    def _delete_category(self, row):
        category = self._category_at_row(row)
        if not category:
            return
        reply = QMessageBox.question(
            self,
            "Kategoriya o'chirish",
            f"'{category['name']}' o'chirilsinmi?\nMahsulotlar o'chmaydi, kategoriyasi bo'shatiladi.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            db.delete_category(category["id"])
            self.load_data()

    def _state_button(self, bg, fg, border, hover):
        return f"""
            QPushButton{{background:{bg};color:{fg};border:1px solid {border};border-radius:6px;font-weight:bold;}}
            QPushButton:hover{{background:{hover};color:white;border-color:{hover};}}
            QPushButton:pressed{{background:#1e293b;color:white;border-color:#1e293b;padding-top:2px;}}
        """


class ReturnSaleItemDialog(QDialog):
    def __init__(self, parent=None, archive_row=None):
        super().__init__(parent)
        self.archive_row = archive_row
        self.available = archive_row["quantity"] - archive_row["returned_quantity"]
        self.language = (parent.property("app_language") if parent else None) or "uz"
        self.setWindowTitle(t("Mahsulotni qaytarish", self.language))
        self.setFixedWidth(380)
        self._build_ui()
        set_language(self, self.language)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        info = QLabel(
            f"{self.archive_row['product_name']}\n"
            f"{t('Sotuv', self.language)} #{self.archive_row['sale_id']} | "
            f"{t('Qaytarish mumkin', self.language)}: {self.available}"
        )
        info.setStyleSheet("color:#1e293b;font-size:13px;font-weight:bold;")
        layout.addWidget(info)

        form = QFormLayout()
        self.qty_spin = QSpinBox()
        self.qty_spin.setRange(1, max(1, self.available))
        self.qty_spin.setValue(max(1, self.available))
        self.qty_spin.setEnabled(False)
        self.qty_spin.setStyleSheet("border:1px solid #d1d5db;border-radius:6px;padding:7px 10px;background:white;")
        self.note_edit = QLineEdit()
        self.note_edit.setPlaceholderText("Ixtiyoriy izoh")
        self.note_edit.setStyleSheet("border:1px solid #d1d5db;border-radius:6px;padding:7px 10px;background:white;")
        form.addRow("Miqdor:", self.qty_spin)
        form.addRow("Izoh:", self.note_edit)
        layout.addLayout(form)

        btns = QHBoxLayout()
        cancel_btn = QPushButton("Bekor")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("Qaytarish")
        save_btn.setStyleSheet("background:#059669;color:white;border:none;border-radius:6px;padding:8px 16px;font-weight:bold;")
        save_btn.clicked.connect(self.accept)
        btns.addStretch()
        btns.addWidget(cancel_btn)
        btns.addWidget(save_btn)
        layout.addLayout(btns)

    def get_data(self):
        return {
            "quantity": self.qty_spin.value(),
            "note": self.note_edit.text().strip(),
        }


class ProcessSaleDialog(QDialog):
    def __init__(self, parent=None, product=None):
        super().__init__(parent)
        self.product = product
        self.language = (parent.property("app_language") if parent else None) or "uz"
        self.setWindowTitle(t("Sotuvni yakunlash", self.language))
        self.setFixedWidth(360)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        info = QLabel(f"{product['name']} | {t('Qoldiq', self.language)}: {product['stock']} {product['unit']}")
        info.setStyleSheet("color:#1e293b;font-size:13px;font-weight:bold;")
        layout.addWidget(info)

        form = QFormLayout()
        self.qty_spin = QSpinBox()
        self.qty_spin.setRange(1, max(1, product["stock"]))
        self.qty_spin.setValue(1)
        self.qty_spin.setStyleSheet("border:1px solid #d1d5db;border-radius:6px;padding:7px 10px;background:white;")
        form.addRow("Miqdor:", self.qty_spin)
        layout.addLayout(form)

        btns = QHBoxLayout()
        cancel_btn = QPushButton("Bekor")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("Sotuvni yakunlash")
        save_btn.setStyleSheet("background:#059669;color:white;border:none;border-radius:6px;padding:8px 16px;font-weight:bold;")
        save_btn.clicked.connect(self.accept)
        btns.addStretch()
        btns.addWidget(cancel_btn)
        btns.addWidget(save_btn)
        layout.addLayout(btns)
        set_language(self, self.language)

    def quantity(self):
        return self.qty_spin.value()


class MoveToProcessDialog(QDialog):
    def __init__(self, parent=None, product=None, available=0, initial=None, edit_mode=False):
        super().__init__(parent)
        self.product = product
        self.available = available
        self.initial = initial or {}
        self.edit_mode = edit_mode
        self.language = (parent.property("app_language") if parent else None) or "uz"
        self.currencies = [dict(c) for c in db.get_currencies()]
        self.setWindowTitle("Jarayonni tahrirlash" if self.edit_mode else "Jarayonga o'tkazish")
        self.setFixedWidth(390)
        self._build_ui()
        self._apply_language()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        self.info_lbl = QLabel()
        self.info_lbl.setStyleSheet("color:#1e293b;font-size:13px;font-weight:bold;")
        layout.addWidget(self.info_lbl)

        form = QFormLayout()
        self.qty_spin = QSpinBox()
        self.qty_spin.setRange(1, max(1, self.available))
        self.qty_spin.setValue(int(self.initial.get("quantity") or 1))
        self.qty_spin.setVisible(not self.edit_mode)
        self.deposit_edit = QLineEdit()
        self.deposit_edit.setPlaceholderText("0.00")
        self.deposit_edit.setValidator(QRegularExpressionValidator(QRegularExpression(r"^\d*([.,]\d{0,2})?$"), self))
        deposit_amount = float(self.initial.get("deposit_amount") or 0)
        if deposit_amount > 0:
            self.deposit_edit.setText(self._format_amount(deposit_amount))
        self.currency_combo = QComboBox()
        for currency in self.currencies:
            self.currency_combo.addItem(currency["code"], currency["code"])
        idx = self.currency_combo.findText(self.initial.get("deposit_currency") or "UZS")
        if idx >= 0:
            self.currency_combo.setCurrentIndex(idx)
        for widget in [self.qty_spin, self.deposit_edit, self.currency_combo]:
            widget.setStyleSheet("border:1px solid #d1d5db;border-radius:6px;padding:7px 10px;background:white;")
        deposit_row = QHBoxLayout()
        deposit_row.addWidget(self.deposit_edit)
        deposit_row.addWidget(self.currency_combo)
        if not self.edit_mode:
            form.addRow("Miqdor:", self.qty_spin)
        form.addRow("Zaklad:", deposit_row)
        layout.addLayout(form)

        self.customer_check = QCheckBox("Mijoz nomi va telefonini kiritish")
        self.customer_check.toggled.connect(self._toggle_customer_fields)
        layout.addWidget(self.customer_check)

        customer_form = QFormLayout()
        self.customer_name_edit = QLineEdit()
        self.customer_name_edit.setPlaceholderText("Ism-familya")
        self.customer_name_edit.setText(self.initial.get("customer_name") or "")
        self.customer_phone_edit = QLineEdit()
        self.customer_phone_edit.setPlaceholderText("+998 XX XXX XX XX")
        self.customer_phone_edit.setText(self.initial.get("customer_phone") or "")
        for widget in [self.customer_name_edit, self.customer_phone_edit]:
            widget.setStyleSheet("border:1px solid #d1d5db;border-radius:6px;padding:7px 10px;background:white;")
        customer_form.addRow("Mijoz:", self.customer_name_edit)
        customer_form.addRow("Telefon:", self.customer_phone_edit)
        layout.addLayout(customer_form)

        btns = QHBoxLayout()
        cancel_btn = QPushButton("Bekor")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("Saqlash" if self.edit_mode else "Jarayonga o'tkazish")
        save_btn.setStyleSheet("background:#3b82f6;color:white;border:none;border-radius:6px;padding:8px 16px;font-weight:bold;")
        save_btn.clicked.connect(self._accept)
        btns.addStretch()
        btns.addWidget(cancel_btn)
        btns.addWidget(save_btn)
        layout.addLayout(btns)
        has_customer = bool(self.initial.get("customer_name") or self.initial.get("customer_phone"))
        self.customer_check.setChecked(has_customer)
        self._toggle_customer_fields(has_customer)

    def _apply_language(self):
        title = "Jarayonni tahrirlash" if self.edit_mode else "Jarayonga o'tkazish"
        self.setWindowTitle(t(title, self.language))
        self.info_lbl.setText(f"{self.product['name']} | {t('Bor qoldiq', self.language)}: {self.available}")
        set_language(self, self.language)

    def _toggle_customer_fields(self, enabled):
        self.customer_name_edit.setEnabled(enabled)
        self.customer_phone_edit.setEnabled(enabled)
        if enabled:
            self.customer_name_edit.setFocus()

    def get_data(self):
        customer_name = customer_phone = None
        if self.customer_check.isChecked():
            customer_name = self.customer_name_edit.text().strip() or None
            customer_phone = self.customer_phone_edit.text().strip() or None
        return {
            "quantity": self.qty_spin.value(),
            "deposit_amount": self._deposit_value(),
            "deposit_currency": self.currency_combo.currentData() or "UZS",
            "customer_name": customer_name,
            "customer_phone": customer_phone,
        }

    def _accept(self):
        text = self.deposit_edit.text().strip()
        if not text or self._deposit_value() <= 0:
            QMessageBox.warning(
                self,
                t("Zaklad kerak", self.language),
                t("Zaklad faqat 0 dan katta musbat raqam bo'lishi kerak.", self.language),
            )
            return
        self.accept()

    def _deposit_value(self):
        text = self.deposit_edit.text().strip().replace(" ", "").replace(",", ".")
        try:
            return max(0, float(text)) if text else 0
        except ValueError:
            return 0

    def _format_amount(self, value):
        return f"{value:.2f}".rstrip("0").rstrip(".")


class ProductArchiveDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Mahsulotlar arxivi")
        self.setMinimumSize(1280, 720)
        self.resize(1320, 760)
        self._build_ui()
        self.load_data()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        toolbar = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Mahsulot, shtrix-kod, kassir yoki mijoz bo'yicha qidirish...")
        self.search_edit.setFixedHeight(38)
        self.search_edit.setStyleSheet("border:1px solid #d1d5db;border-radius:6px;padding:0 12px;font-size:13px;background:white;")
        self.search_edit.textChanged.connect(self.load_data)
        toolbar.addWidget(self.search_edit)
        layout.addLayout(toolbar)

        self.table = QTableWidget()
        self.table.setColumnCount(12)
        self.table.setHorizontalHeaderLabels([
            "Vaqt", "Sotuv", "Mahsulot", "Shtrix-kod", "Sotildi",
            "Qaytdi", "Narx", "To'lov", "Kassir", "Mijoz", "Telefon", "Amal"
        ])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for column, width in [
            (0, 150), (1, 70), (3, 110), (4, 80), (5, 80),
            (6, 120), (7, 110), (8, 110), (9, 130), (10, 130), (11, 150)
        ]:
            self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(column, width)
        self.table.verticalHeader().setDefaultSectionSize(54)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet("""
            QTableWidget{background:white;border:1px solid #e2e8f0;border-radius:8px;font-size:13px;}
            QTableWidget::item{padding:7px 10px;}
            QHeaderView::section{background:#f8fafc;border:none;border-bottom:1px solid #e2e8f0;padding:8px;font-weight:bold;color:#64748b;}
            QTableWidget::item:alternate{background:#f8fafc;}
        """)
        layout.addWidget(self.table)

    def load_data(self):
        query = self.search_edit.text() if hasattr(self, "search_edit") else ""
        rows = db.get_product_sales_archive(query)
        self.table.setRowCount(0)
        for row_index, archive_row in enumerate(rows):
            self.table.insertRow(row_index)
            data = dict(archive_row)
            values = [
                data["created_at"] or "",
                f"#{data['sale_id']}",
                data["product_name"] or "",
                data["barcode"] or "",
                str(data["quantity"]),
                str(data["returned_quantity"]),
                f"{data['price']:,.0f} so'm",
                self._payment_label(data["payment_method"]),
                data["cashier_name"] or "",
                data["customer_name"] or "",
                data["customer_phone"] or "",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, data)
                if column in (4, 5, 6):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(row_index, column, item)
            self.table.setCellWidget(row_index, 11, self._actions_widget(row_index))
            self.table.setRowHeight(row_index, 54)

    def _payment_label(self, value):
        labels = {
            "naqd": "Naqd",
            "plastik karta": "Plastik karta",
            "qarz": "Qarz",
        }
        return labels.get(value, value or "")

    def _actions_widget(self, row):
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(0)
        btn = QPushButton("Qaytarish")
        btn.setFixedSize(118, 30)
        archive_row = self._archive_at_row(row)
        available = (archive_row["quantity"] - archive_row["returned_quantity"]) if archive_row else 0
        btn.setEnabled(available > 0)
        btn.setStyleSheet("""
            QPushButton{background:#ecfdf5;color:#065f46;border:1px solid #6ee7b7;border-radius:6px;font-weight:bold;}
            QPushButton:hover{background:#10b981;color:white;border-color:#10b981;}
            QPushButton:disabled{background:#f1f5f9;color:#94a3b8;border-color:#e2e8f0;}
        """)
        btn.clicked.connect(lambda _, r=row: self._return_item(r))
        layout.addWidget(btn, alignment=Qt.AlignmentFlag.AlignCenter)
        return widget

    def _archive_at_row(self, row):
        item = self.table.item(row, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _return_item(self, row):
        language = self.property("app_language") or "uz"
        archive_row = self._archive_at_row(row)
        if not archive_row:
            return
        available = archive_row["quantity"] - archive_row["returned_quantity"]
        if available <= 0:
            QMessageBox.information(self, t("Qaytarib bo'lingan", language), t("Bu sotuvdagi mahsulot to'liq qaytarilgan.", language))
            return
        dlg = ReturnSaleItemDialog(self, archive_row)
        if dlg.exec():
            data = dlg.get_data()
            try:
                db.return_sale_item(archive_row["sale_item_id"], data["quantity"], data["note"])
                QMessageBox.information(self, t("Qaytarildi", language), t("Mahsulot omborga qaytarildi.", language))
                self.load_data()
                parent = self.parent()
                if parent and hasattr(parent, "load_data"):
                    parent.load_data()
            except db.AppError as exc:
                QMessageBox.warning(self, t("Qaytarilmadi", language), str(exc))


class BarcodeLabelWidget(QWidget):
    def __init__(self, product):
        super().__init__()
        self.product = product
        self.setFixedSize(400, 300)
        self.setAutoFillBackground(True)
        self.setStyleSheet("background:white;border:1px solid #d1d5db;")

    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            self.draw_label(painter, QRectF(0, 0, self.width(), self.height()))
        except Exception as exc:
            painter.fillRect(self.rect(), QColor("white"))
            painter.setPen(QColor("#b91c1c"))
            painter.setFont(QFont("Arial", 10))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, f"Barcode chizilmadi:\n{exc}")
        finally:
            painter.end()

    def draw_label(self, painter, rect):
        painter.save()
        painter.fillRect(rect, QColor("white"))
        margin_x = rect.width() * 0.08
        margin_y = rect.height() * 0.08
        content = rect.adjusted(margin_x, margin_y, -margin_x, -margin_y)

        name = self.product["name"] or ""
        barcode = self.product["barcode"] or ""

        painter.setPen(QColor("#111827"))
        title_font = QFont("Arial", 8)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.drawText(
            QRectF(content.left(), content.top(), content.width(), content.height() * 0.18),
            Qt.AlignmentFlag.AlignCenter,
            name[:34],
        )

        barcode_rect = QRectF(
            content.left(),
            content.top() + content.height() * 0.22,
            content.width(),
            content.height() * 0.42,
        )
        self._draw_code128(painter, barcode_rect, barcode)

        painter.setFont(QFont("Arial", 8))
        painter.drawText(
            QRectF(content.left(), barcode_rect.bottom() + 2, content.width(), content.height() * 0.22),
            Qt.AlignmentFlag.AlignCenter,
            barcode,
        )

        painter.setPen(QPen(QColor("#d1d5db"), 1))
        painter.drawRect(rect.adjusted(1, 1, -1, -1))
        painter.restore()

    def _draw_code128(self, painter, rect, text):
        painter.save()
        painter.fillRect(rect, QColor("white"))
        values = _code128_values(text)
        patterns = [CODE128_PATTERNS[value] for value in values]
        total_modules = sum(int(width) for pattern in patterns for width in pattern)
        module_width = rect.width() / total_modules
        x = rect.left()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("black"))
        for pattern in patterns:
            draw_bar = True
            for width_char in pattern:
                width = int(width_char) * module_width
                if draw_bar:
                    painter.drawRect(QRectF(x, rect.top(), width, rect.height()))
                x += width
                draw_bar = not draw_bar
        painter.restore()


class BarcodePrintDialog(QDialog):
    def __init__(self, parent=None, product=None):
        super().__init__(parent)
        self.product = product
        self.setWindowTitle("Barcode chiqarish")
        self.setFixedWidth(460)
        self._build_ui()

    def _build_ui(self):
        self.setStyleSheet("QDialog{background:white;} QLabel{color:#374151;font-size:13px;}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        info = QLabel("Label o'lchami: 40 x 30 mm")
        info.setStyleSheet("font-weight:bold;color:#1e293b;")
        layout.addWidget(info)

        self.preview = BarcodeLabelWidget(self.product)
        layout.addWidget(self.preview, alignment=Qt.AlignmentFlag.AlignCenter)

        btn_row = QHBoxLayout()
        cancel_btn = QPushButton("Yopish")
        cancel_btn.clicked.connect(self.reject)
        print_btn = QPushButton("Xprinterga chiqarish")
        print_btn.setStyleSheet("background:#059669;color:white;border:none;border-radius:6px;padding:9px 18px;font-weight:bold;")
        print_btn.clicked.connect(self._print_label)
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(print_btn)
        layout.addLayout(btn_row)

    def _print_label(self):
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setPageSize(QPageSize(QSizeF(40, 30), QPageSize.Unit.Millimeter, "40x30"))
        printer.setPageMargins(QMarginsF(0, 0, 0, 0), QPageLayout.Unit.Millimeter)
        printer.setFullPage(True)

        dialog = QPrintDialog(printer, self)
        dialog.setWindowTitle("Xprinter tanlash")
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        painter = QPainter(printer)
        page_rect = QRectF(printer.pageRect(QPrinter.Unit.DevicePixel))
        self.preview.draw_label(painter, page_rect)
        painter.end()
        QMessageBox.information(self, "Barcode", "Barcode printerga yuborildi.")


class ProductDialog(QDialog):
    def __init__(self, parent=None, product=None, duplicate=False, section_id=None):
        super().__init__(parent)
        self.product = product
        self.duplicate = duplicate
        self.section_id = section_id if section_id is not None else _row_value(product, "section_id")
        self.language = (parent.property("app_language") if parent else None) or "uz"
        self.attr_edits = {}
        self.attr_fields = {}
        self.currencies = [dict(c) for c in db.get_currencies()]
        self.product_attributes = db.get_product_attributes(product["id"]) if product else {}
        title = "Mahsulot qo'shish" if (not product or duplicate) else "Mahsulotni tahrirlash"
        self.setWindowTitle(t(title, self.language))
        self.setFixedWidth(460)
        self.setMaximumHeight(720)
        self.resize(460, 680)
        self._build_ui()
        set_language(self, self.language)

    def _build_ui(self):
        self.setStyleSheet("""
            QDialog { background: white; }
            QLabel { color: #374151; font-size: 13px; }
            QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
                border: 1px solid #d1d5db; border-radius: 6px;
                padding: 7px 10px; font-size: 13px; background: white;
            }
            QLineEdit:focus { border-color: #3b82f6; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll_content = QWidget()
        scroll.setWidget(scroll_content)

        content_layout = QVBoxLayout(scroll_content)
        content_layout.setContentsMargins(24, 20, 24, 16)
        content_layout.setSpacing(12)

        form = QFormLayout()
        form.setSpacing(10)

        barcode_value = "" if self.duplicate else (self.product["barcode"] if self.product and self.product["barcode"] else "")
        self.barcode_edit = QLineEdit(barcode_value)
        self.barcode_edit.setPlaceholderText("Ixtiyoriy")
        form.addRow("Shtrix-kod:", self.barcode_edit)

        self.name_edit = QLineEdit(self.product["name"] if self.product else "")
        self.name_edit.setPlaceholderText("Mahsulot nomi *")
        form.addRow("Nomi *:", self.name_edit)

        self.supplier_combo = QComboBox()
        self.supplier_combo.addItem("Kimdan olingan tanlanmagan", None)
        for supplier in db.get_all_suppliers():
            self.supplier_combo.addItem(supplier["name"], supplier["id"])
        supplier_id = _row_value(self.product, "supplier_id")
        if self.product and supplier_id:
            idx = self.supplier_combo.findData(supplier_id)
            if idx >= 0:
                self.supplier_combo.setCurrentIndex(idx)
        form.addRow("Kimdan olingan:", self.supplier_combo)

        self.template_combo = QComboBox()
        self.template_combo.addItem("Template tanlanmagan", None)
        for template in db.get_templates(self.section_id):
            self.template_combo.addItem(template["name"], template["id"])
        template_id = _row_value(self.product, "template_id")
        if self.product and template_id:
            idx = self.template_combo.findData(template_id)
            if idx >= 0:
                self.template_combo.setCurrentIndex(idx)
        self.template_combo.currentIndexChanged.connect(self._load_template_fields)
        form.addRow("Template:", self.template_combo)

        money_validator = QDoubleValidator(0, 999999999, 4, self)
        money_validator.setNotation(QDoubleValidator.Notation.StandardNotation)

        self.cost_spin = QLineEdit()
        self.cost_spin.setValidator(money_validator)
        self.cost_spin.setPlaceholderText("0.0000")
        cost_value = (
            self.product["cost_original"] if self.product and self.product["cost_original"] else
            (self.product["cost"] if self.product else 0)
        )
        if cost_value:
            self.cost_spin.setText(self._format_money_input(cost_value))
        self.cost_currency_combo = self._currency_combo(
            self.product["cost_currency"] if self.product and self.product["cost_currency"] else self._default_currency_code()
        )
        self.cost_currency_combo.currentIndexChanged.connect(self._update_price_labels)
        self.cost_spin.textChanged.connect(self._update_price_labels)
        cost_row = QHBoxLayout()
        cost_row.addWidget(self.cost_spin)
        cost_row.addWidget(self.cost_currency_combo)
        form.addRow("Xarid narxi:", cost_row)
        self.cost_uzs_lbl = QLabel("")
        self.cost_uzs_lbl.setStyleSheet("color:#64748b;font-size:12px;")
        form.addRow("", self.cost_uzs_lbl)

        self.price_spin = QLineEdit()
        self.price_spin.setValidator(money_validator)
        self.price_spin.setPlaceholderText("0.0000")
        price_value = (
            self.product["price_original"] if self.product and self.product["price_original"] else
            (self.product["price"] if self.product else 0)
        )
        if price_value:
            self.price_spin.setText(self._format_money_input(price_value))
        self.price_currency_combo = self._currency_combo(
            self.product["price_currency"] if self.product and self.product["price_currency"] else self._default_currency_code()
        )
        self.price_currency_combo.currentIndexChanged.connect(self._update_price_labels)
        self.price_spin.textChanged.connect(self._update_price_labels)
        price_row = QHBoxLayout()
        price_row.addWidget(self.price_spin)
        price_row.addWidget(self.price_currency_combo)
        form.addRow("Sotish narxi *:", price_row)
        self.price_uzs_lbl = QLabel("")
        self.price_uzs_lbl.setStyleSheet("color:#64748b;font-size:12px;")
        form.addRow("", self.price_uzs_lbl)

        self.stock_spin = QSpinBox()
        self.stock_spin.setRange(1, 9999999)
        self.stock_spin.setValue(max(1, self.product["stock"] if self.product else 1))
        form.addRow("Qoldiq:", self.stock_spin)

        content_layout.addLayout(form)

        attrs_title = QLabel("Template hususiyatlari")
        attrs_title.setStyleSheet("font-weight:bold;color:#1e293b;margin-top:6px;")
        content_layout.addWidget(attrs_title)
        self.attrs_form = QFormLayout()
        self.attrs_form.setSpacing(8)
        content_layout.addLayout(self.attrs_form)
        self._load_template_fields()
        self._update_price_labels()

        layout.addWidget(scroll, 1)

        self.print_barcode_check = QCheckBox("Saqlagandan keyin barcode chiqarish")
        self.print_barcode_check.setVisible(not self.product or self.duplicate)
        self.print_barcode_check.setChecked(not self.product or self.duplicate)
        self.print_barcode_check.setStyleSheet("margin:0 24px;color:#1e293b;font-size:13px;font-weight:600;")
        layout.addWidget(self.print_barcode_check)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(24, 12, 24, 20)
        cancel_btn = QPushButton("Bekor qilish")
        cancel_btn.setStyleSheet("""
            QPushButton { background: white; border: 1px solid #d1d5db;
                          border-radius: 6px; padding: 9px 20px; color: #6b7280; }
            QPushButton:hover { background: #f9fafb; }
        """)
        cancel_btn.clicked.connect(self.reject)

        save_btn = QPushButton("Saqlash")
        save_btn.setStyleSheet("""
            QPushButton { background: #3b82f6; color: white; border: none;
                          border-radius: 6px; padding: 9px 20px; font-weight: bold; }
            QPushButton:hover { background: #2563eb; }
        """)
        save_btn.clicked.connect(self._save)
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

    def _save(self):
        barcode = self.barcode_edit.text().strip()
        if not barcode:
            QMessageBox.warning(self, t("Xatolik", self.language), t("Shtrix-kodni kiriting!", self.language))
            return
        if barcode:
            existing = db.get_product_by_barcode(barcode)
            current_id = None if self.duplicate else (_row_value(self.product, "id") if self.product else None)
            if existing and existing["id"] != current_id:
                QMessageBox.warning(
                    self,
                    t("Saqlanmadi", self.language),
                    t("Bu shtrix-kod allaqachon mavjud.", self.language),
                )
                return
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, t("Xatolik", self.language), t("Mahsulot nomini kiriting!", self.language))
            return
        if self._money_value(self.price_spin) <= 0:
            QMessageBox.warning(self, t("Xatolik", self.language), t("Sotish narxini kiriting!", self.language))
            return
        if self._money_value(self.cost_spin) <= 0:
            QMessageBox.warning(self, t("Xatolik", self.language), t("Xarid narxini kiriting!", self.language))
            return
        for field_id, edit in self.attr_edits.items():
            field = self.attr_fields[field_id]
            if field["required"] and not edit.text().strip():
                QMessageBox.warning(
                    self,
                    t("Xatolik", self.language),
                    f"{field['name']} {t('hususiyatini kiriting!', self.language)}",
                )
                return
        self.accept()

    def get_data(self):
        price_currency = self._selected_currency(self.price_currency_combo)
        cost_currency = self._selected_currency(self.cost_currency_combo)
        price_original = self._money_value(self.price_spin)
        cost_original = self._money_value(self.cost_spin)
        price_rate = price_currency["rate_to_uzs"]
        cost_rate = cost_currency["rate_to_uzs"]
        return {
            "barcode": self.barcode_edit.text().strip() or None,
            "name": self.name_edit.text().strip(),
            "section_id": self.section_id,
            "template_id": self.template_combo.currentData(),
            "supplier_id": self.supplier_combo.currentData(),
            "category_id": None,
            "price": price_original * price_rate,
            "cost": cost_original * cost_rate,
            "price_currency": price_currency["code"],
            "price_exchange_rate": price_rate,
            "price_original": price_original,
            "cost_currency": cost_currency["code"],
            "cost_exchange_rate": cost_rate,
            "cost_original": cost_original,
            "stock": self.stock_spin.value(),
            "unit": _row_value(self.product, "unit", "dona") if self.product else "dona",
        }

    def _currency_combo(self, selected_code):
        combo = QComboBox()
        for currency in self._ordered_currencies():
            combo.addItem(currency["code"], currency)
        idx = combo.findText(selected_code)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        return combo

    def _ordered_currencies(self):
        priority = {"USD": 0, "EUR": 1, "UZS": 2}
        return sorted(
            self.currencies,
            key=lambda currency: (priority.get(currency["code"], 10), currency["code"]),
        )

    def _default_currency_code(self):
        codes = {currency["code"] for currency in self.currencies}
        return "USD" if "USD" in codes else "UZS"

    def _selected_currency(self, combo):
        return combo.currentData() or {"code": "UZS", "rate_to_uzs": 1}

    def _money_value(self, edit):
        text = edit.text().strip().replace(" ", "").replace(",", ".")
        try:
            return float(text) if text else 0
        except ValueError:
            return 0

    def _format_money_input(self, value):
        return f"{float(value):.4f}".rstrip("0").rstrip(".")

    def _update_price_labels(self):
        price_currency = self._selected_currency(self.price_currency_combo)
        cost_currency = self._selected_currency(self.cost_currency_combo)
        price_uzs = self._money_value(self.price_spin) * price_currency["rate_to_uzs"]
        cost_uzs = self._money_value(self.cost_spin) * cost_currency["rate_to_uzs"]
        uzs_label = t("So'mda", self.language)
        money_unit = t("so'm", self.language)
        rate_label = t("kurs:", self.language)
        self.price_uzs_lbl.setText(
            f"{uzs_label}: {price_uzs:,.0f} {money_unit} | {rate_label} {price_currency['rate_to_uzs']:,.2f}"
        )
        self.cost_uzs_lbl.setText(
            f"{uzs_label}: {cost_uzs:,.0f} {money_unit} | {rate_label} {cost_currency['rate_to_uzs']:,.2f}"
        )

    def get_attributes(self):
        return {field_id: edit.text().strip() for field_id, edit in self.attr_edits.items()}

    def _load_template_fields(self):
        while self.attrs_form.rowCount():
            self.attrs_form.removeRow(0)
        self.attr_edits = {}
        self.attr_fields = {}
        template_id = self.template_combo.currentData()
        fields = db.get_template_fields(template_id)
        if not fields:
            self.attrs_form.addRow(QLabel(t("Template tanlansa, hususiyat maydonlari shu yerda chiqadi.", self.language)))
            return
        for field_row in fields:
            field = dict(field_row)
            edit = QLineEdit(self.product_attributes.get(field["id"], ""))
            edit.setPlaceholderText(field["name"])
            label = f"{field['name']} *:" if field["required"] else f"{field['name']}:"
            self.attrs_form.addRow(label, edit)
            self.attr_edits[field["id"]] = edit
            self.attr_fields[field["id"]] = field


class ProductsWidget(QWidget):
    def __init__(self, user=None):
        super().__init__()
        self.user = user
        self._async_loader = None
        self._search_timer = None
        self._render_generation = 0
        self.current_section = None
        self._showing_trash = False
        self._empty_state_panel = None
        self._theme = {}
        self._build_ui()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_empty_state_panel()

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
        self._search_timer.timeout.connect(lambda: self.load_data(self.search_edit.text()))

        # Toolbar
        toolbar = QHBoxLayout()
        self.back_sections_btn = QPushButton()
        self.back_sections_btn.setIcon(QIcon(LEFT_ARROW_ICON_PATH))
        self.back_sections_btn.setIconSize(QSize(15, 15))
        self.back_sections_btn.setFixedSize(32, 32)
        self.back_sections_btn.setToolTip(t("Bo'limlar", self.property("app_language") or "uz"))
        self.back_sections_btn.setStyleSheet("""
            QPushButton { background:white;color:#1e293b;border:1px solid #d1d5db;border-radius:6px;font-weight:bold; }
            QPushButton:hover { background:#eff6ff;border-color:#93c5fd;color:#1d4ed8; }
        """)
        self.back_sections_btn.clicked.connect(self._show_sections)
        toolbar.addWidget(self.back_sections_btn)

        self.trash_btn = QPushButton(t("Trash", self.property("app_language") or "uz"))
        self.trash_btn.setIcon(QIcon(TRASH_ICON_PATH))
        self.trash_btn.setIconSize(QSizeF(18, 18).toSize())
        self.trash_btn.setFixedHeight(38)
        self.trash_btn.setMinimumWidth(96)
        self.trash_btn.setToolTip(t("Trash", self.property("app_language") or "uz"))
        self.trash_btn.setStyleSheet("""
            QPushButton { background:white;color:#334155;border:1px solid #cbd5e1;
                          border-radius:6px;padding:0 14px;font-size:13px;font-weight:bold;text-align:center; }
            QPushButton:hover { background:#f1f5f9;border-color:#94a3b8; }
        """)
        self.trash_btn.clicked.connect(self._show_trash)
        toolbar.addWidget(self.trash_btn)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Qidirish...")
        self.search_edit.setFixedHeight(38)
        self.search_edit.setStyleSheet("""
            QLineEdit { border: 1px solid #d1d5db; border-radius: 6px;
                        padding: 0 12px; font-size: 13px; background: white; }
            QLineEdit:focus { border-color: #3b82f6; }
        """)
        self.search_edit.textChanged.connect(self._queue_search)
        toolbar.addWidget(self.search_edit)

        self.supplier_filter = QComboBox()
        self.supplier_filter.setFixedHeight(38)
        self.supplier_filter.setMinimumWidth(190)
        self.supplier_filter.setStyleSheet("""
            QComboBox { border: 1px solid #d1d5db; border-radius: 6px;
                        padding: 0 10px; font-size: 13px; background: white; }
        """)
        self.supplier_filter.currentIndexChanged.connect(lambda _: self.load_data())
        toolbar.addWidget(self.supplier_filter)

        self.template_filter = QComboBox()
        self.template_filter.setFixedHeight(38)
        self.template_filter.setMinimumWidth(180)
        self.template_filter.setStyleSheet("""
            QComboBox { border: 1px solid #d1d5db; border-radius: 6px;
                        padding: 0 10px; font-size: 13px; background: white; }
        """)
        self.template_filter.currentIndexChanged.connect(lambda _: self.load_data())
        toolbar.addWidget(self.template_filter)

        self.display_currency_combo = QComboBox()
        self.display_currency_combo.setFixedHeight(38)
        self.display_currency_combo.setMinimumWidth(92)
        self.display_currency_combo.setStyleSheet("""
            QComboBox { border: 1px solid #d1d5db; border-radius: 6px;
                        padding: 0 10px; font-size: 13px; background: white; }
        """)
        self._load_display_currency_combo()
        self.display_currency_combo.currentIndexChanged.connect(lambda _: self.load_data())
        toolbar.addStretch()

        self.section_add_btn = QPushButton("+ Bo'lim qo'shish")
        self.section_add_btn.setFixedHeight(38)
        self.section_add_btn.setStyleSheet("""
            QPushButton { background: #3b82f6; color: white; border: none;
                          border-radius: 6px; padding: 0 16px; font-size: 13px; font-weight: bold; }
            QPushButton:hover { background: #2563eb; }
        """)
        self.section_add_btn.clicked.connect(self._add_section)
        toolbar.addWidget(self.section_add_btn)

        self.templates_btn = QPushButton("Templatelar")
        self.templates_btn.setFixedHeight(38)
        self.templates_btn.setStyleSheet("""
            QPushButton { background: white; color: #1e293b; border: 1px solid #d1d5db;
                          border-radius: 6px; padding: 0 14px; font-size: 13px; }
            QPushButton:hover { background: #f8fafc; }
        """)
        self.templates_btn.clicked.connect(self._manage_templates)
        toolbar.addWidget(self.templates_btn)

        self.add_btn = QPushButton("+ Mahsulot qo'shish")
        self.add_btn.setFixedHeight(38)
        self.add_btn.setStyleSheet("""
            QPushButton { background: #3b82f6; color: white; border: none;
                          border-radius: 6px; padding: 0 16px; font-size: 13px; font-weight: bold; }
            QPushButton:hover { background: #2563eb; }
        """)
        self.add_btn.clicked.connect(self._add_product)
        toolbar.addWidget(self.add_btn)

        self.clear_sold_btn = QPushButton("Sotilganlarni tozalash")
        self.clear_sold_btn.setObjectName("danger_clear_sold_btn")
        self.clear_sold_btn.setFixedHeight(38)
        self.clear_sold_btn.setStyleSheet("""
            QPushButton { background:#dc2626;color:white;border:none;border-radius:6px;padding:0 14px;font-size:13px;font-weight:bold; }
            QPushButton:hover { background:#b91c1c; }
            QPushButton:pressed { background:#991b1b;padding-top:2px; }
        """)
        self.clear_sold_btn.clicked.connect(self._clear_sold_history)
        toolbar.addWidget(self.clear_sold_btn)
        layout.addLayout(toolbar)

        self.section_title_lbl = SectionTitleWidget()
        self._apply_section_title_style()
        layout.addWidget(self.section_title_lbl)

        self.sections_scroll = QScrollArea()
        self.sections_scroll.setWidgetResizable(True)
        self.sections_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.sections_scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        self.sections_content = QWidget()
        self.sections_content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.sections_grid = QGridLayout(self.sections_content)
        self.sections_grid.setContentsMargins(0, 0, 0, 0)
        self.sections_grid.setSpacing(12)
        self.sections_grid.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.sections_scroll.setWidget(self.sections_content)
        layout.addWidget(self.sections_scroll, 1)

        # Stats bar
        self.stats_lbl = QLabel()
        self.stats_lbl.setStyleSheet("color: #64748b; font-size: 12px;")
        layout.addWidget(self.stats_lbl)

        self.tabs = QTabWidget()
        self.table = self._create_products_table()
        self.process_table = self._create_process_table()
        self.table.setColumnHidden(5, True)
        self.sold_table = self._create_sold_table()
        self.tabs.addTab(self.table, "Bor mahsulotlar")
        self.tabs.addTab(self.process_table, "Jarayonda")
        self.tabs.addTab(self.sold_table, "Sotilganlar")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self.tabs)

        self.date_lbl = QLabel("Sana:")
        self.date_edit = QDateEdit()
        self.date_edit.setDate(QDate.currentDate())
        self.date_edit.setDateRange(QDate(2000, 1, 1), QDate.currentDate().addYears(10))
        self.date_edit.setCalendarPopup(True)
        calendar = QCalendarWidget(self)
        calendar.setNavigationBarVisible(True)
        calendar.setGridVisible(True)
        calendar.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
        calendar.setMinimumSize(400, 300)
        self.date_edit.setCalendarWidget(calendar)
        self.date_edit.setDisplayFormat("dd.MM.yyyy")
        self.date_edit.setFixedSize(210, 42)
        self.date_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.date_edit.setStyleSheet("""
            QDateEdit {
                border:1px solid #d1d5db;
                border-radius:6px;
                padding:2px 34px 2px 10px;
                background:white;
                font-size:14px;
            }
            QDateEdit::drop-down { width:30px;border:none; }
        """)
        self.date_edit.dateChanged.connect(lambda _: self.load_data())

        self.period_combo = QComboBox()
        self.period_combo.addItem("Kunlik", "day")
        self.period_combo.addItem("Haftalik", "week")
        self.period_combo.addItem("Oylik", "month")
        self.period_combo.addItem("Yillik", "year")
        self.period_combo.setCurrentIndex(self.period_combo.findData("year"))
        self.period_combo.setFixedHeight(36)
        self.period_combo.setMinimumWidth(110)
        self.period_combo.setStyleSheet("border:1px solid #d1d5db;border-radius:6px;padding:6px 10px;background:white;")
        self.period_combo.currentIndexChanged.connect(lambda _: self.load_data())

        period_button_style = """
            QPushButton { background:white;color:#1e293b;border:1px solid #d1d5db;border-radius:6px;padding:0 12px;font-weight:bold; }
            QPushButton:hover { background:#eff6ff;border-color:#93c5fd;color:#1d4ed8; }
        """
        self.prev_period_btn = QPushButton("<")
        self.prev_period_btn.setFixedSize(36, 36)
        self.prev_period_btn.setStyleSheet(period_button_style)
        self.prev_period_btn.clicked.connect(lambda: self._shift_period(-1))
        self.next_period_btn = QPushButton(">")
        self.next_period_btn.setFixedSize(36, 36)
        self.next_period_btn.setStyleSheet(period_button_style)
        self.next_period_btn.clicked.connect(lambda: self._shift_period(1))
        self.today_btn = QPushButton("Bugun")
        self.today_btn.setFixedHeight(36)
        self.today_btn.setMinimumWidth(82)
        self.today_btn.setStyleSheet(period_button_style)
        self.today_btn.clicked.connect(lambda: self.date_edit.setDate(QDate.currentDate()))
        self.display_currency_combo.setFixedHeight(36)

        period_controls = QHBoxLayout()
        period_controls.setSpacing(8)
        period_controls.addStretch()
        period_controls.addWidget(self.prev_period_btn)
        period_controls.addWidget(self.date_lbl)
        period_controls.addWidget(self.date_edit)
        period_controls.addWidget(self.period_combo)
        period_controls.addWidget(self.next_period_btn)
        period_controls.addWidget(self.today_btn)
        period_controls.addWidget(self.display_currency_combo)
        period_controls.addStretch()
        layout.addLayout(period_controls)
        self.product_view_widgets = [self.stats_lbl, self.tabs]
        self.period_widgets = [
            self.prev_period_btn, self.date_lbl, self.date_edit,
            self.period_combo, self.next_period_btn, self.today_btn,
            self.display_currency_combo,
        ]
        self._load_supplier_filter()
        self._load_template_filter()
        self._load_display_currency_combo()
        self._update_clear_sold_button()
        self._show_sections()
        QTimer.singleShot(0, self._apply_section_title_style)

    def showEvent(self, event):
        super().showEvent(event)
        self._apply_section_title_style()

    def _apply_section_title_style(self):
        if not hasattr(self, "section_title_lbl"):
            return
        self.section_title_lbl.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.section_title_lbl.setStyleSheet("")
        self.section_title_lbl.update()

    def _set_products_mode(self, in_section, trash=False):
        self._showing_trash = bool(trash)
        section_widgets = [self.sections_scroll]
        product_widgets = getattr(self, "product_view_widgets", [])
        for widget in section_widgets:
            widget.setVisible(not in_section)
        for widget in product_widgets:
            widget.setVisible(in_section)
        for widget in [
            self.search_edit, self.supplier_filter, self.template_filter,
            self.templates_btn, self.add_btn, self.clear_sold_btn,
            self.back_sections_btn,
        ]:
            widget.setVisible(in_section)
        for widget in getattr(self, "period_widgets", []):
            widget.setVisible(in_section)
        self.section_add_btn.setVisible(not in_section and not trash)
        self.trash_btn.setVisible(not in_section and not trash)
        self.back_sections_btn.setVisible(in_section or trash)
        self.section_title_lbl.setVisible(not in_section or trash)
        if in_section:
            self._update_clear_sold_button()
        else:
            self.clear_sold_btn.setVisible(False)

    def apply_theme(self, _theme):
        self._theme = _theme or {}
        self._apply_section_title_style()
        if self.current_section:
            return
        if self._showing_trash:
            self._load_trash()
        else:
            self._load_sections()

    def _show_sections(self):
        self.current_section = None
        self._set_products_mode(False)
        self.section_title_lbl.setText(t("Bo'limlar", self.property("app_language") or "uz"))
        self._apply_section_title_style()
        self._load_sections()

    def _show_trash(self):
        self.current_section = None
        self._set_products_mode(False, trash=True)
        self.section_title_lbl.setText(t("Trash", self.property("app_language") or "uz"))
        self._apply_section_title_style()
        self._load_trash()

    def _open_section(self, section):
        self.current_section = dict(section)
        self._set_products_mode(True)
        self._apply_section_title_style()
        self._load_template_filter()
        self.load_data()

    def _load_sections(self):
        self._clear_sections_grid()
        sections = db.get_product_sections()
        if not sections:
            self.sections_grid.setAlignment(Qt.AlignmentFlag(0))
            empty = self._empty_sections_state(t("Bo'lim yo'q", self.property("app_language") or "uz"))
            self.sections_grid.addWidget(empty, 0, 0, 1, 4)
            for column in range(4):
                self.sections_grid.setColumnStretch(column, 1)
            self.sections_grid.setRowStretch(0, 1)
            QTimer.singleShot(0, self._fit_empty_state_panel)
            return
        self.sections_grid.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        for index, section in enumerate(sections):
            self.sections_grid.addWidget(self._section_card(section), index // 4, index % 4)
        for column in range(4):
            self.sections_grid.setColumnStretch(column, 0)
        self.sections_grid.setRowStretch((len(sections) + 3) // 4, 1)

    def _clear_sections_grid(self):
        self._empty_state_panel = None
        while self.sections_grid.count():
            item = self.sections_grid.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def _empty_sections_state(self, text):
        panel = QFrame()
        panel.setObjectName("sectionsEmptyState")
        panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        panel.setStyleSheet("""
            QFrame#sectionsEmptyState {
                background:#ffffff;
                border:1px solid #dbeafe;
                border-radius:8px;
            }
        """)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 16)
        label = QLabel(text)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet("color:#475569;font-size:14px;border:none;background:transparent;")
        layout.addWidget(label)
        self._empty_state_panel = panel
        self._fit_empty_state_panel(panel)
        return panel

    def _fit_empty_state_panel(self, panel=None):
        panel = panel or self._empty_state_panel
        if not panel or not hasattr(self, "sections_scroll"):
            return
        viewport = self.sections_scroll.viewport().size()
        min_width = max((260 * 4) + (12 * 3), viewport.width() - 2)
        min_height = max(300, viewport.height() - 2)
        panel.setMinimumSize(min_width, min_height)

    def _section_card_style(self):
        accent = self._theme.get("accent", "#3b82f6")
        border = QColor(accent).lighter(145).name()
        hover_border = QColor(accent).name()
        return f"""
            QFrame#sectionCard {{
                background:#ffffff;
                border:2px solid {border};
                border-radius:8px;
            }}
            QFrame#sectionCard:hover {{
                background:#ffffff;
                border-color:{hover_border};
            }}
            QFrame#sectionCard:focus {{
                outline:0;
                border:2px solid {border};
            }}
        """

    def _section_card(self, section):
        card = QFrame()
        card.setObjectName("sectionCard")
        card.setFixedSize(260, 205)
        card.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        card.setCursor(Qt.CursorShape.PointingHandCursor)
        card.setStyleSheet(self._section_card_style())
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 12, 14, 14)
        layout.setSpacing(10)

        top = QHBoxLayout()
        name_lbl = QLabel(section["name"])
        name_lbl.setWordWrap(True)
        name_lbl.setStyleSheet("color:#0f172a;font-size:16px;font-weight:bold;border:none;background:transparent;")
        menu_btn = QPushButton("⋮")
        menu_btn.setFixedSize(28, 28)
        menu_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        menu_btn.setStyleSheet("""
            QPushButton{background:transparent;border:none;color:#475569;font-size:18px;font-weight:bold;}
            QPushButton:hover{background:#e2e8f0;border-radius:6px;}
            QPushButton:focus{outline:0;background:transparent;border:none;}
        """)
        menu_btn.clicked.connect(lambda _, s=dict(section), b=menu_btn: self._show_section_menu(s, b))
        top.addWidget(name_lbl, 1)
        top.addWidget(menu_btn, alignment=Qt.AlignmentFlag.AlignTop)
        layout.addLayout(top)

        products_count = len(db.get_all_products(section_id=section["id"]))
        templates_count = len(db.get_templates(section["id"]))
        language = self.property("app_language") or "uz"
        metrics = QFrame()
        metrics.setObjectName("sectionMetrics")
        metrics.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        metrics.setStyleSheet("QFrame#sectionMetrics{background:#f8fbff;border:1px solid #dbeafe;border-radius:8px;}")
        metrics_layout = QVBoxLayout(metrics)
        metrics_layout.setContentsMargins(12, 10, 12, 10)
        metrics_layout.setSpacing(6)
        product_lbl = QLabel(f"{t('Jami mahsulot', language)}: {products_count}")
        template_lbl = QLabel(f"{t('Templatelar', language)}: {templates_count}")
        for label in (product_lbl, template_lbl):
            label.setStyleSheet("color:#475569;font-size:12px;border:none;background:transparent;")
            metrics_layout.addWidget(label)
        layout.addWidget(metrics)
        layout.addStretch()
        card.mousePressEvent = lambda event, s=dict(section): self._open_section(s)
        return card

    def _load_trash(self):
        self._clear_sections_grid()
        trash = db.get_product_trash()
        items = [("section", row) for row in trash["sections"]]
        items.extend(("product", row) for row in trash["products"])
        if not items:
            self.sections_grid.setAlignment(Qt.AlignmentFlag(0))
            empty = self._empty_sections_state(t("Trash bo'sh", self.property("app_language") or "uz"))
            self.sections_grid.addWidget(empty, 0, 0, 1, 4)
            for column in range(4):
                self.sections_grid.setColumnStretch(column, 1)
            self.sections_grid.setRowStretch(0, 1)
            QTimer.singleShot(0, self._fit_empty_state_panel)
            return
        self.sections_grid.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        for index, (kind, row) in enumerate(items):
            self.sections_grid.addWidget(self._trash_card(kind, row), index // 4, index % 4)
        for column in range(4):
            self.sections_grid.setColumnStretch(column, 0)
        self.sections_grid.setRowStretch((len(items) + 3) // 4, 1)

    def _trash_card(self, kind, row):
        language = self.property("app_language") or "uz"
        card = QFrame()
        card.setObjectName("trashCard")
        card.setFixedSize(260, 175)
        card.setStyleSheet("""
            QFrame#trashCard { background:#fff; border:1px solid #fecaca; border-radius:8px; }
        """)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)
        title = _row_value(row, "original_name") or _row_value(row, "name")
        if kind == "product":
            title = _row_value(row, "name")
        title_lbl = QLabel(title or "")
        title_lbl.setWordWrap(True)
        title_lbl.setStyleSheet("color:#111827;font-size:15px;font-weight:bold;border:none;background:transparent;")
        layout.addWidget(title_lbl)
        type_lbl = QLabel(t("Bo'lim", language) if kind == "section" else t("Mahsulot", language))
        deleted_label = t("O'chirilgan", language)
        deleted_lbl = QLabel(f"{deleted_label}: {_row_value(row, 'deleted_at', '') or ''}")
        purge_lbl = QLabel(f"{t('30 kun ichida', language)}: {_row_value(row, 'purge_after', '') or ''}")
        labels = [type_lbl]
        if kind == "product" and _row_value(row, "section_name"):
            section_label = t("Bo'lim", language)
            labels.append(QLabel(f"{section_label}: {_row_value(row, 'section_name')}"))
        labels.extend([deleted_lbl, purge_lbl])
        for label in labels:
            label.setStyleSheet("color:#64748b;font-size:12px;border:none;background:transparent;")
            layout.addWidget(label)
        layout.addStretch()
        restore_btn = QPushButton(t("Qayta tiklash", language))
        restore_btn.setFixedHeight(32)
        restore_btn.setStyleSheet("""
            QPushButton{background:#ecfdf5;color:#065f46;border:1px solid #86efac;border-radius:6px;font-weight:bold;}
            QPushButton:hover{background:#10b981;color:white;border-color:#10b981;}
        """)
        if kind == "section":
            restore_btn.clicked.connect(lambda _, sid=row["id"]: self._restore_section(sid))
        else:
            restore_btn.clicked.connect(lambda _, pid=row["id"]: self._restore_product(pid))
        layout.addWidget(restore_btn)
        return card

    def _restore_section(self, section_id):
        language = self.property("app_language") or "uz"
        try:
            db.restore_product_section(section_id)
            self._load_trash()
        except db.AppError as exc:
            QMessageBox.warning(self, t("Tiklanmadi", language), t(str(exc), language))

    def _restore_product(self, product_id):
        language = self.property("app_language") or "uz"
        try:
            db.restore_product(product_id)
            self._load_trash()
        except db.AppError as exc:
            QMessageBox.warning(self, t("Tiklanmadi", language), t(str(exc), language))

    def _show_section_menu(self, section, button):
        menu = QMenu(self)
        language = self.property("app_language") or "uz"
        edit_action = menu.addAction(t("Tahrir", language))
        delete_action = menu.addAction(t("O'chir", language))
        action = menu.exec(button.mapToGlobal(button.rect().bottomLeft()))
        if action == edit_action:
            self._edit_section(section)
        elif action == delete_action:
            self._delete_section(section)

    def _add_section(self):
        language = self.property("app_language") or "uz"
        dlg = ProductSectionDialog(self)
        if dlg.exec():
            try:
                db.add_product_section(dlg.name_edit.text().strip())
                self._load_sections()
            except db.AppError as exc:
                QMessageBox.warning(self, t("Saqlanmadi", language), t(str(exc), language))

    def _edit_section(self, section):
        language = self.property("app_language") or "uz"
        dlg = ProductSectionDialog(self, section)
        if dlg.exec():
            try:
                db.update_product_section(section["id"], dlg.name_edit.text().strip())
                self._load_sections()
            except db.AppError as exc:
                QMessageBox.warning(self, t("Saqlanmadi", language), t(str(exc), language))

    def _delete_section(self, section):
        language = self.property("app_language") or "uz"
        delete_text = t("bo'limi va ichidagi mahsulot/template ma'lumotlari o'chirilsinmi?", language)
        reply = QMessageBox.question(
            self,
            t("Bo'limni o'chirish", language),
            f"'{section['name']}' {delete_text}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            db.delete_product_section(section["id"])
            self._load_sections()
        except db.AppError as exc:
            QMessageBox.warning(self, t("O'chirilmadi", language), t(str(exc), language))

    def _on_tab_changed(self):
        self._update_clear_sold_button()
        self.load_data()

    def _queue_search(self, *_args):
        self._search_timer.start()

    def _update_clear_sold_button(self):
        if hasattr(self, "clear_sold_btn") and hasattr(self, "tabs"):
            self.clear_sold_btn.setVisible(self.tabs.currentWidget() is self.sold_table)

    def _create_products_table(self):
        table = QTableWidget()
        table.setColumnCount(8)
        table.setHorizontalHeaderLabels([
            "Nomi", "Template", "Shtrix-kod", "Narx", "Qoldiq", "Zaklad", "Amallar", "Copy"
        ])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeMode.Fixed)
        table.setColumnWidth(5, 130)
        table.setColumnWidth(6, 180)
        table.setColumnWidth(7, 70)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.setStyleSheet(self._table_style())
        table.verticalHeader().setDefaultSectionSize(112)
        table.doubleClicked.connect(lambda index, t=table: self._show_product_info_from_table(index, t))
        return table

    def _create_process_table(self):
        table = QTableWidget()
        table.setColumnCount(9)
        table.setHorizontalHeaderLabels([
            "Nomi", "Template", "Shtrix-kod", "Narx", "Qoldiq",
            "Zaklad", "Mijoz", "Telefon", "Amallar"
        ])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column, width in [(5, 120), (6, 130), (7, 130), (8, 180)]:
            table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            table.setColumnWidth(column, width)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.setStyleSheet(self._table_style())
        table.verticalHeader().setDefaultSectionSize(112)
        table.doubleClicked.connect(lambda index, t=table: self._show_product_info_from_table(index, t))
        return table

    def _create_sold_table(self):
        table = QTableWidget()
        table.setColumnCount(12)
        table.setHorizontalHeaderLabels([
            "Vaqt", "Sotuv", "Mahsulot", "Shtrix-kod", "Sotildi",
            "Narx", "Chegirma", "Chegirmadan keyin", "To'lov", "Mijoz", "Telefon", "Amal"
        ])
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for column, width in [
            (0, 150), (1, 70), (3, 110), (4, 80),
            (5, 120), (6, 110), (7, 140), (8, 110), (9, 130), (10, 130), (11, 130)
        ]:
            table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            table.setColumnWidth(column, width)
        table.verticalHeader().setDefaultSectionSize(54)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.setStyleSheet(self._table_style())
        table.doubleClicked.connect(self._show_product_info_from_sold)
        return table

    def _table_style(self):
        return """
            QTableWidget { background: white; border: 1px solid #e2e8f0;
                           border-radius: 8px; font-size: 13px; }
            QTableWidget::item { padding: 7px 10px; }
            QTableWidget::item:selected { background: #dbeafe; color: #1e40af; }
            QHeaderView::section { background: #f8fafc; border: none;
                                   border-bottom: 1px solid #e2e8f0;
                                   padding: 8px; font-weight: bold; color: #64748b; }
            QTableWidget::item:alternate { background: #f8fafc; }
        """

    def _load_supplier_filter(self, suppliers=None):
        current = self.supplier_filter.currentData() if hasattr(self, "supplier_filter") else None
        self.supplier_filter.blockSignals(True)
        self.supplier_filter.clear()
        self.supplier_filter.addItem("Barcha ta'minotchilar", None)
        self.supplier_filter.addItem("Ta'minotchi tanlanmagan", "none")
        for supplier in suppliers if suppliers is not None else db.get_all_suppliers():
            self.supplier_filter.addItem(supplier["name"], supplier["id"])
        if current is not None:
            idx = self.supplier_filter.findData(current)
            if idx >= 0:
                self.supplier_filter.setCurrentIndex(idx)
        self.supplier_filter.blockSignals(False)

    def _load_template_filter(self, templates=None):
        current = self.template_filter.currentData() if hasattr(self, "template_filter") else None
        self.template_filter.blockSignals(True)
        self.template_filter.clear()
        self.template_filter.addItem("Barcha templatelar", None)
        self.template_filter.addItem("Template tanlanmagan", "none")
        section_id = self.current_section["id"] if self.current_section else None
        for template in templates if templates is not None else db.get_templates(section_id):
            self.template_filter.addItem(template["name"], template["id"])
        if current is not None:
            idx = self.template_filter.findData(current)
            if idx >= 0:
                self.template_filter.setCurrentIndex(idx)
        self.template_filter.blockSignals(False)

    def _load_display_currency_combo(self, currencies=None):
        if not hasattr(self, "display_currency_combo"):
            return
        current = self.display_currency_combo.currentData()
        self.display_currency_combo.blockSignals(True)
        self.display_currency_combo.clear()
        currencies = [dict(currency) for currency in (currencies if currencies is not None else db.get_currencies())]
        priority = {"UZS": 0, "USD": 1, "EUR": 2}
        currencies.sort(key=lambda currency: (priority.get(currency["code"], 10), currency["code"]))
        for currency in currencies:
            self.display_currency_combo.addItem(currency["code"], currency)
        if not currencies:
            self.display_currency_combo.addItem("UZS", {"code": "UZS", "rate_to_uzs": 1})
        if current:
            idx = self.display_currency_combo.findText(current["code"], Qt.MatchFlag.MatchStartsWith)
            if idx >= 0:
                self.display_currency_combo.setCurrentIndex(idx)
        self.display_currency_combo.blockSignals(False)

    def _selected_display_currency(self):
        if hasattr(self, "display_currency_combo"):
            return self.display_currency_combo.currentData() or {"code": "UZS", "rate_to_uzs": 1}
        return {"code": "UZS", "rate_to_uzs": 1}

    def _money_display(self, amount_uzs):
        currency = self._selected_display_currency()
        code = currency.get("code") or "UZS"
        rate = currency.get("rate_to_uzs") or 1
        value = (amount_uzs or 0) / rate
        if code == "UZS":
            unit = t("so'm", self.property("app_language") or "uz")
            return f"{value:,.0f} {unit}"
        return f"{value:,.2f} {code}"

    def load_data(self, query=None):
        if not self.current_section:
            self._show_sections()
            return
        if query is not None and not isinstance(query, str):
            query = None
        if query is None:
            query = self.search_edit.text() if hasattr(self, 'search_edit') else ""
        start_date, end_date = self._date_range()
        section_id = self.current_section["id"]

        if self.isVisible():
            self._async_loader.start(
                lambda: {
                    "query": query,
                    "start_date": start_date,
                    "end_date": end_date,
                    "products": db.search_products(query, start_date, end_date, section_id) if query else db.get_all_products(start_date, end_date, section_id),
                    "sold_rows": [dict(row) for row in db.get_product_sales_archive(query, start_date, end_date)],
                    "suppliers": db.get_all_suppliers(),
                    "templates": db.get_templates(section_id),
                    "currencies": [dict(currency) for currency in db.get_currencies()],
                },
                self._apply_loaded_data,
            )
            return

        self._apply_loaded_data({
            "query": query,
            "start_date": start_date,
            "end_date": end_date,
            "products": db.search_products(query, start_date, end_date, section_id) if query else db.get_all_products(start_date, end_date, section_id),
            "sold_rows": [dict(row) for row in db.get_product_sales_archive(query, start_date, end_date)],
            "suppliers": db.get_all_suppliers(),
            "templates": db.get_templates(section_id),
            "currencies": [dict(currency) for currency in db.get_currencies()],
        })

    def _apply_loaded_data(self, data):
        self._load_supplier_filter(data["suppliers"])
        self._load_template_filter(data["templates"])
        self._load_display_currency_combo(data["currencies"])
        self._currency_rates = {currency["code"]: (currency.get("rate_to_uzs") or 1) for currency in data["currencies"]}
        products = data["products"]
        products = self._apply_product_filters(products)
        sold_rows = self._apply_product_filters(data["sold_rows"])
        sold_count = len(sold_rows)

        available = [p for p in products if self._available_quantity(p) > 0]
        processing = [p for p in products if (_row_value(p, "process_quantity", 0) or 0) > 0]
        self._stats_counts = (len(available), len(processing), sold_count)
        self._update_stats_label()

        current_table = self.tabs.currentWidget() if hasattr(self, "tabs") else self.table
        self.table.setRowCount(0)
        self.process_table.setRowCount(0)
        self.sold_table.setRowCount(0)
        if current_table is self.process_table:
            self._fill_products_table(self.process_table, processing, "process")
        elif current_table is self.sold_table:
            self._fill_sold_table(sold_rows)
        else:
            self._fill_products_table(self.table, available, "available")
        set_language(self, self.property("app_language") or "uz")

    def _date_range(self):
        if not hasattr(self, "date_edit"):
            today = date.today().isoformat()
            return today, today
        selected = self.date_edit.date().toPyDate()
        period = self.period_combo.currentData()
        if period == "day":
            start = end = selected
        elif period == "week":
            start = selected - timedelta(days=selected.weekday())
            end = start + timedelta(days=6)
        elif period == "month":
            start = selected.replace(day=1)
            next_month = selected.replace(year=selected.year + 1, month=1, day=1) if selected.month == 12 else selected.replace(month=selected.month + 1, day=1)
            end = next_month - timedelta(days=1)
        else:
            start = selected.replace(month=1, day=1)
            end = selected.replace(month=12, day=31)
        return start.isoformat(), end.isoformat()

    def _shift_period(self, direction):
        current = self.date_edit.date()
        period = self.period_combo.currentData()
        if period == "day":
            next_date = current.addDays(direction)
        elif period == "week":
            next_date = current.addDays(direction * 7)
        elif period == "month":
            next_date = current.addMonths(direction)
        else:
            next_date = current.addYears(direction)
        self.date_edit.setDate(next_date)

    def _update_stats_label(self):
        available_count, processing_count, sold_count = getattr(self, "_stats_counts", (0, 0, 0))
        language = self.property("app_language") or "uz"
        unit = t("ta", language)
        self.stats_lbl.setText(
            f"{t('Bor', language)}: {available_count} {unit}  |  "
            f"{t('Jarayonda', language)}: {processing_count} {unit}  |  "
            f"{t('Sotilganlar', language)}: {sold_count} {unit}"
        )

    def _language_changed(self, _language):
        language = self.property("app_language") or "uz"
        if hasattr(self, "back_sections_btn"):
            self.back_sections_btn.setToolTip(t("Bo'limlar", language))
        if hasattr(self, "trash_btn"):
            self.trash_btn.setText(t("Trash", language))
            self.trash_btn.setToolTip(t("Trash", language))
        if hasattr(self, "section_add_btn"):
            self.section_add_btn.setText(t("+ Bo'lim qo'shish", language))
        if hasattr(self, "section_title_lbl"):
            self._apply_section_title_style()
        if hasattr(self, "sections_scroll") and not self.current_section:
            self.section_title_lbl.setText(t("Bo'limlar", language))
            self._apply_section_title_style()
            self._load_sections()
        if hasattr(self, "stats_lbl"):
            self._update_stats_label()

    def _apply_product_filters(self, products):
        supplier_filter = self.supplier_filter.currentData() if hasattr(self, "supplier_filter") else None
        if self.current_section:
            products = [p for p in products if _row_value(p, "section_id") == self.current_section["id"]]
        if supplier_filter == "none":
            products = [p for p in products if not _row_value(p, "supplier_id")]
        elif supplier_filter:
            products = [p for p in products if _row_value(p, "supplier_id") == supplier_filter]

        template_filter = self.template_filter.currentData() if hasattr(self, "template_filter") else None
        if template_filter == "none":
            products = [p for p in products if not _row_value(p, "template_id")]
        elif template_filter:
            products = [p for p in products if _row_value(p, "template_id") == template_filter]
        return products

    def _filtered_sold_rows(self, query):
        rows = [dict(row) for row in db.get_product_sales_archive(query)]
        return self._apply_product_filters(rows)

    def _fill_products_table(self, table, products, mode):
        table.setRowCount(0)
        table.setUpdatesEnabled(False)
        rows = list(products)
        state = {"row": 0}
        self._render_generation += 1
        generation = self._render_generation
        if self.progress_bar:
            self.progress_bar.setVisible(True)

        def render_chunk():
            if generation != self._render_generation:
                return
            end = min(state["row"] + 8, len(rows))
            for row in range(state["row"], end):
                p = rows[row]
                self._fill_product_table_row(table, row, p, mode)
            state["row"] = end
            if state["row"] < len(rows):
                QTimer.singleShot(0, render_chunk)
                return
            table.setUpdatesEnabled(True)
            set_language(self, self.property("app_language") or "uz")
            if self.progress_bar:
                QTimer.singleShot(150, lambda: self.progress_bar.setVisible(False))

        QTimer.singleShot(0, render_chunk)

    def _fill_product_table_row(self, table, row, p, mode):
            table.insertRow(row)
            name_item = QTableWidgetItem(p["name"])
            name_item.setData(Qt.ItemDataRole.UserRole, dict(p))
            table.setItem(row, 0, name_item)
            table.setItem(row, 1, QTableWidgetItem(p["template_name"] or ""))
            table.setItem(row, 2, QTableWidgetItem(p["barcode"] or ""))

            price_item = QTableWidgetItem(self._money_display(p["price"]))
            price_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            table.setItem(row, 3, price_item)

            stock_item = QTableWidgetItem(f"{p['stock']}")
            if mode == "available":
                stock_item = QTableWidgetItem(f"{self._available_quantity(p)}")
            elif mode == "process":
                stock_item = QTableWidgetItem(f"{_row_value(p, 'process_quantity', 0) or 0}")
            if p["stock"] <= 0:
                stock_item.setForeground(QColor("#ef4444"))
                stock_item.setBackground(QColor("#fef2f2"))
            table.setItem(row, 4, stock_item)
            deposit = _row_value(p, "process_deposit", 0) or 0
            deposit_currency = _row_value(p, "process_deposit_currency", "UZS") or "UZS"
            deposit_rate = getattr(self, "_currency_rates", {}).get(deposit_currency, 1) or 1
            deposit_text = self._money_display(deposit * deposit_rate) if mode == "process" else ""
            table.setItem(row, 5, QTableWidgetItem(deposit_text))
            action_column = 6
            if mode == "process":
                table.setItem(row, 6, QTableWidgetItem(_row_value(p, "process_customer_name", "") or ""))
                table.setItem(row, 7, QTableWidgetItem(_row_value(p, "process_customer_phone", "") or ""))
                action_column = 8
            elif mode == "available":
                copy_wrap = QWidget()
                copy_layout = QHBoxLayout(copy_wrap)
                copy_layout.setContentsMargins(0, 0, 0, 0)
                duplicate_btn = QPushButton()
                duplicate_btn.setIcon(QIcon(COPY_ICON_PATH))
                duplicate_btn.setFixedSize(34, 28)
                duplicate_btn.setIconSize(QSizeF(18, 18).toSize())
                duplicate_btn.setToolTip(t("Nusxalash", self.property("app_language") or "uz"))
                duplicate_btn.setStyleSheet("""
                    QPushButton { background:#f8fafc;color:#334155;border:1px solid #cbd5e1;
                                  border-radius:6px;font-size:14px;font-weight:bold; }
                    QPushButton:hover { background:#e0f2fe;color:#0369a1;border-color:#7dd3fc; }
                    QPushButton:pressed { background:#0f172a;color:white;padding-top:2px; }
                """)
                duplicate_btn.clicked.connect(lambda _, r=row, t=table: self._duplicate_product(r, t))
                copy_layout.addWidget(duplicate_btn, alignment=Qt.AlignmentFlag.AlignCenter)
                table.setCellWidget(row, 7, copy_wrap)

            actions_widget = QWidget()
            actions_widget.setStyleSheet("background: transparent;")
            actions_layout = QVBoxLayout(actions_widget)
            actions_layout.setContentsMargins(8, 6, 8, 6)
            actions_layout.setSpacing(5)

            if mode == "available":
                process_btn = self._action_button("Jarayonga", "#eff6ff", "#1d4ed8", "#93c5fd", "#3b82f6")
                process_btn.clicked.connect(lambda _, r=row, t=table: self._move_to_process(r, t))
                actions_layout.addWidget(process_btn)

                edit_btn = self._action_button("Tahrir", "#fff7ed", "#9a3412", "#fdba74", "#fb923c")
                edit_btn.clicked.connect(lambda _, r=row, t=table: self._edit_product(r, t))
                actions_layout.addWidget(edit_btn)

                barcode_btn = self._action_button("Barcode", "#eff6ff", "#1d4ed8", "#93c5fd", "#3b82f6")
                barcode_btn.clicked.connect(lambda _, r=row, t=table: self._print_barcode(r, t))
                actions_layout.addWidget(barcode_btn)

                del_btn = self._action_button("O'chir", "#fef2f2", "#b91c1c", "#fca5a5", "#ef4444")
                del_btn.clicked.connect(lambda _, r=row, t=table: self._delete_product(r, t))
                actions_layout.addWidget(del_btn)
            else:
                actions_widget.setStyleSheet("background: transparent; border: none;")
                actions_layout.setContentsMargins(0, 8, 0, 8)
                actions_layout.setSpacing(5)
                edit_btn = self._action_button("Tahrir", "#ffffff", "#9a3412", "#fdba74", "#fff7ed", hover_fg="#9a3412")
                edit_btn.setFixedSize(156, 24)
                edit_btn.clicked.connect(lambda _, r=row, t=table: self._edit_process(r, t))
                actions_layout.addWidget(edit_btn, alignment=Qt.AlignmentFlag.AlignCenter)

                back_btn = self._action_button("Bor mahsulotlarga", "#ffffff", "#2563eb", "#93c5fd", "#eff6ff", hover_fg="#1d4ed8")
                back_btn.setFixedSize(156, 24)
                back_btn.clicked.connect(lambda _, r=row, t=table: self._move_to_available(r, t))
                actions_layout.addWidget(back_btn, alignment=Qt.AlignmentFlag.AlignCenter)

                sell_btn = self._action_button("Sotuvni yakunlash", "#ffffff", "#047857", "#86efac", "#ecfdf5", hover_fg="#065f46")
                sell_btn.setFixedSize(156, 24)
                sell_btn.clicked.connect(lambda _, r=row, t=table: self._complete_process_sale(r, t))
                actions_layout.addWidget(sell_btn, alignment=Qt.AlignmentFlag.AlignCenter)
            table.setCellWidget(row, action_column, actions_widget)
            table.setRowHeight(row, 140 if mode == "available" else 112)

    def _available_quantity(self, product):
        return max(0, (product["stock"] or 0) - (_row_value(product, "process_quantity", 0) or 0))

    def _action_button(self, text, bg, fg, border, hover, hover_fg="white"):
        btn = QPushButton(text)
        btn.setFixedSize(86, 26)
        btn.setStyleSheet(f"""
            QPushButton {{ background: {bg}; color: {fg}; border: 1px solid {border};
                          border-radius: 6px; padding: 0 8px; font-size: 12px; font-weight: bold; }}
            QPushButton:hover {{ background: {hover}; color: {hover_fg}; border-color: {border}; }}
            QPushButton:pressed {{ background: #1e293b; color: white; padding-top: 2px; }}
        """)
        return btn

    def _fill_sold_table(self, rows):
        self.sold_table.setRowCount(0)
        self.sold_table.setUpdatesEnabled(False)
        rows = list(rows)
        state = {"row": 0}
        self._render_generation += 1
        generation = self._render_generation
        if self.progress_bar:
            self.progress_bar.setVisible(True)

        def render_chunk():
            if generation != self._render_generation:
                return
            end = min(state["row"] + 20, len(rows))
            for row_index in range(state["row"], end):
                self._fill_sold_table_row(row_index, rows[row_index])
            state["row"] = end
            if state["row"] < len(rows):
                QTimer.singleShot(0, render_chunk)
                return
            self.sold_table.setUpdatesEnabled(True)
            set_language(self, self.property("app_language") or "uz")
            if self.progress_bar:
                QTimer.singleShot(150, lambda: self.progress_bar.setVisible(False))

        QTimer.singleShot(0, render_chunk)

    def _fill_sold_table_row(self, row_index, archive_row):
            self.sold_table.insertRow(row_index)
            data = dict(archive_row)
            values = [
                data["created_at"] or "",
                f"#{data['sale_id']}",
                data["product_name"] or "",
                data["barcode"] or "",
                str(data["quantity"]),
                self._money_display(data["price"]),
                self._money_display(data.get("item_discount", data.get("discount", 0)) or 0),
                self._money_display(data.get("item_total_after_discount", data.get("active_subtotal", data.get("subtotal", 0))) or 0),
                self._payment_label(data["payment_method"]),
                data["customer_name"] or "",
                data["customer_phone"] or "",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, data)
                if column in (4, 5, 6, 7):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.sold_table.setItem(row_index, column, item)
            self.sold_table.setCellWidget(row_index, 11, self._sold_actions_widget(row_index))
            self.sold_table.setRowHeight(row_index, 54)

    def _sold_actions_widget(self, row):
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(6, 4, 6, 4)
        btn = QPushButton("Qaytarish")
        btn.setFixedSize(100, 30)
        archive_row = self._sold_at_row(row)
        available = (archive_row["quantity"] - archive_row["returned_quantity"]) if archive_row else 0
        btn.setEnabled(available > 0)
        btn.setStyleSheet("""
            QPushButton{background:#ecfdf5;color:#065f46;border:1px solid #6ee7b7;border-radius:6px;font-weight:bold;}
            QPushButton:hover{background:#10b981;color:white;border-color:#10b981;}
            QPushButton:pressed{background:#047857;color:white;padding-top:2px;}
            QPushButton:disabled{background:#f1f5f9;color:#94a3b8;border-color:#e2e8f0;}
        """)
        btn.clicked.connect(lambda _, r=row: self._return_sold_item(r))
        layout.addWidget(btn, alignment=Qt.AlignmentFlag.AlignCenter)
        return widget

    def _sold_at_row(self, row):
        item = self.sold_table.item(row, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _payment_label(self, value):
        labels = {
            "naqd": "Naqd",
            "plastik karta": "Plastik karta",
            "qarz": "Qarz",
        }
        return labels.get(value, value or "")

    def _return_sold_item(self, row):
        language = self.property("app_language") or "uz"
        archive_row = self._sold_at_row(row)
        if not archive_row:
            return
        available = archive_row["quantity"] - archive_row["returned_quantity"]
        if available <= 0:
            QMessageBox.information(self, t("Qaytarib bo'lingan", language), t("Bu sotuvdagi mahsulot to'liq qaytarilgan.", language))
            return
        dlg = ReturnSaleItemDialog(self, archive_row)
        if dlg.exec():
            data = dlg.get_data()
            try:
                db.return_sale_item(archive_row["sale_item_id"], data["quantity"], data["note"])
                QMessageBox.information(self, t("Qaytarildi", language), t("Mahsulot omborga qaytarildi.", language))
                self.load_data()
            except db.AppError as exc:
                QMessageBox.warning(self, t("Qaytarilmadi", language), str(exc))

    def _clear_sold_history(self):
        language = self.property("app_language") or "uz"
        reply = QMessageBox.question(
            self,
            t("Sotilganlarni tozalash", language),
            t("Barcha sotuv tarixi va sotilganlar ro'yxati tozalansinmi?", language),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            db.clear_sales_history()
            self.load_data()
            QMessageBox.information(self, t("Tozalandi", language), t("Sotilganlar ro'yxati tozalandi.", language))
        except db.AppError as exc:
            QMessageBox.warning(self, t("Tozalanmadi", language), str(exc))

    def _manage_templates(self):
        if not self.current_section:
            return
        dlg = TemplateManagerDialog(self, self.current_section["id"])
        dlg.exec()
        self.load_data()

    def _show_archive(self):
        dlg = ProductArchiveDialog(self)
        dlg.exec()
        self.load_data()

    def _add_product(self):
        language = self.property("app_language") or "uz"
        try:
            dlg = ProductDialog(self, section_id=self.current_section["id"] if self.current_section else None)
        except db.AppError as exc:
            QMessageBox.warning(self, t("Xatolik", language), f"{t('Mahsulot oynasi ochilmadi:', language)}\n{exc}")
            return
        if dlg.exec():
            try:
                product_id = db.add_product(dlg.get_data())
                db.save_product_attributes(product_id, dlg.get_attributes())
                self.load_data()
                if dlg.print_barcode_check.isChecked():
                    self._print_product_barcode_by_id(product_id)
            except db.AppError as exc:
                QMessageBox.warning(self, t("Saqlanmadi", language), t(str(exc), language))

    def _duplicate_product(self, row, table=None):
        language = self.property("app_language") or "uz"
        item = self._product_item(row, table)
        if not item:
            return
        product = item.data(Qt.ItemDataRole.UserRole)
        if not product:
            return
        try:
            dlg = ProductDialog(self, product, duplicate=True, section_id=self.current_section["id"] if self.current_section else None)
        except db.AppError as exc:
            QMessageBox.warning(self, t("Xatolik", language), f"{t('Mahsulot oynasi ochilmadi:', language)}\n{exc}")
            return
        if dlg.exec():
            try:
                product_id = db.add_product(dlg.get_data())
                db.save_product_attributes(product_id, dlg.get_attributes())
                self.load_data()
                if dlg.print_barcode_check.isChecked():
                    self._print_product_barcode_by_id(product_id)
            except db.AppError as exc:
                QMessageBox.warning(self, t("Saqlanmadi", language), t(str(exc), language))

    def _product_item(self, row, table=None):
        table = table or self.table
        return table.item(row, 0)

    def _show_product_info_from_table(self, index, table):
        item = self._product_item(index.row(), table)
        product = item.data(Qt.ItemDataRole.UserRole) if item else None
        if product:
            self._show_product_info(dict(product))

    def _show_product_info_from_sold(self, index):
        archive_row = self._sold_at_row(index.row())
        if not archive_row:
            return
        product = db.get_product_by_id(archive_row["product_id"])
        if product:
            self._show_product_info(dict(product))

    def _show_product_info(self, product):
        dlg = ProductInfoDialog(self, product)
        dlg.exec()

    def _move_to_process(self, row, table=None):
        language = self.property("app_language") or "uz"
        item = self._product_item(row, table)
        if not item:
            return
        product = item.data(Qt.ItemDataRole.UserRole)
        available = self._available_quantity(product)
        if available <= 0:
            QMessageBox.warning(
                self,
                t("Qoldiq yetarli emas", language),
                t("Jarayonga o'tkazish uchun bor qoldiq yo'q.", language),
            )
            return
        dlg = MoveToProcessDialog(self, product, available)
        if dlg.exec():
            data = dlg.get_data()
            try:
                db.put_product_in_process(
                    product["id"],
                    data["quantity"],
                    data["deposit_amount"],
                    data["deposit_currency"],
                    data["customer_name"],
                    data["customer_phone"],
                )
                moved_text = t("jarayonga o'tkazildi.", language)
                deposit_label = t("Zaklad", language)
                QMessageBox.information(
                    self,
                    t("Jarayonga o'tkazildi", language),
                    f"{data['quantity']} {product['unit']} {moved_text}\n"
                    f"{deposit_label}: {data['deposit_amount']:,.2f} {data['deposit_currency']}",
                )
                self.load_data()
                self.tabs.setCurrentWidget(self.process_table)
            except db.AppError as exc:
                QMessageBox.warning(self, t("Jarayonga o'tkazilmadi", language), str(exc))

    def _move_to_available(self, row, table=None):
        language = self.property("app_language") or "uz"
        item = self._product_item(row, table)
        if not item:
            return
        product = item.data(Qt.ItemDataRole.UserRole)
        reply = QMessageBox.question(
            self,
            t("Tasdiqlash", language),
            t("Mahsulot bor mahsulotlarga qaytarilsinmi?", language),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        db.clear_product_process(product["id"])
        self.load_data()
        self.tabs.setCurrentWidget(self.table)

    def _edit_process(self, row, table=None):
        item = self._product_item(row, table)
        if not item:
            return
        product = item.data(Qt.ItemDataRole.UserRole)
        initial = {
            "quantity": _row_value(product, "process_quantity", 0) or 1,
            "deposit_amount": _row_value(product, "process_deposit", 0) or 0.01,
            "deposit_currency": _row_value(product, "process_deposit_currency", "UZS") or "UZS",
            "customer_name": _row_value(product, "process_customer_name", "") or "",
            "customer_phone": _row_value(product, "process_customer_phone", "") or "",
        }
        dlg = MoveToProcessDialog(
            self,
            product,
            product["stock"] or initial["quantity"],
            initial=initial,
            edit_mode=True,
        )
        if dlg.exec():
            data = dlg.get_data()
            try:
                db.update_product_process(
                    product["id"],
                    initial["quantity"],
                    data["deposit_amount"],
                    data["deposit_currency"],
                    data["customer_name"],
                    data["customer_phone"],
                )
                language = self.property("app_language") or "uz"
                QMessageBox.information(
                    self,
                    t("Yangilandi", language),
                    t("Jarayondagi mahsulot ma'lumotlari yangilandi.", language),
                )
                self.load_data()
                self.tabs.setCurrentWidget(self.process_table)
            except db.AppError as exc:
                QMessageBox.warning(self, t("Yangilanmadi", self.property("app_language") or "uz"), str(exc))

    def _complete_process_sale(self, row, table=None):
        language = self.property("app_language") or "uz"
        item = self._product_item(row, table)
        if not item:
            return
        product = item.data(Qt.ItemDataRole.UserRole)
        process_quantity = _row_value(product, "process_quantity", 0) or 0
        if process_quantity <= 0:
            QMessageBox.warning(
                self,
                t("Jarayonda emas", language),
                t("Bu mahsulot uchun jarayondagi miqdor yo'q.", language),
            )
            return
        reply = QMessageBox.question(
            self,
            t("Sotuvni yakunlash", language),
            t("Jarayondagi sotuv yakunlansinmi?", language),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        quantity = process_quantity
        try:
            subtotal = product["price"] * quantity
            sale_id = db.create_sale(
                customer_id=None,
                cashier_id=self.user["id"] if self.user else None,
                items=[{
                    "product_id": product["id"],
                    "quantity": quantity,
                    "price": product["price"],
                    "subtotal": subtotal,
                }],
                total=subtotal,
                discount=0,
                paid=subtotal,
                payment_method="naqd",
                currency_code=product["price_currency"] or "UZS",
                exchange_rate=product["price_exchange_rate"] or 1,
                paid_original=(product["price_original"] or product["price"]) * quantity,
            )
            db.reduce_product_process(product["id"], quantity)
            QMessageBox.information(
                self,
                t("Sotuv yakunlandi", language),
                f"{t('Sotuv', language)} #{sale_id} {t('saqlandi.', language)}",
            )
            self.load_data()
            self.tabs.setCurrentWidget(self.sold_table)
        except db.AppError as exc:
            QMessageBox.warning(self, t("Sotuv yakunlanmadi", language), str(exc))

    def _edit_product(self, row, table=None):
        language = self.property("app_language") or "uz"
        item = self._product_item(row, table)
        if not item:
            return
        product = item.data(Qt.ItemDataRole.UserRole)
        try:
            dlg = ProductDialog(self, product, section_id=self.current_section["id"] if self.current_section else None)
        except db.AppError as exc:
            QMessageBox.warning(self, t("Xatolik", language), f"{t('Mahsulot oynasi ochilmadi:', language)}\n{exc}")
            return
        if dlg.exec():
            try:
                db.update_product(product["id"], dlg.get_data())
                db.save_product_attributes(product["id"], dlg.get_attributes())
                self.load_data()
            except db.AppError as exc:
                QMessageBox.warning(self, t("Saqlanmadi", language), t(str(exc), language))

    def _delete_product(self, row, table=None):
        item = self._product_item(row, table)
        if not item:
            return
        product = item.data(Qt.ItemDataRole.UserRole)
        reply = QMessageBox.question(
            self, "O'chirish",
            f"'{product['name']}' ni o'chirishni tasdiqlaysizmi?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                db.delete_product(product["id"])
                self.load_data()
            except db.AppError as exc:
                QMessageBox.warning(self, "O'chirilmadi", str(exc))

    def _print_barcode(self, row, table=None):
        item = self._product_item(row, table)
        if not item:
            return
        product = item.data(Qt.ItemDataRole.UserRole)
        self._print_product_barcode(product)

    def _print_product_barcode_by_id(self, product_id):
        product = db.get_product_by_id(product_id)
        if product:
            self._print_product_barcode(dict(product))

    def _print_product_barcode(self, product):
        if not product.get("barcode"):
            QMessageBox.warning(self, "Barcode yo'q", "Avval mahsulotga shtrix-kod kiriting.")
            return
        try:
            _code128_values(product["barcode"])
        except ValueError as exc:
            QMessageBox.warning(self, "Barcode noto'g'ri", str(exc))
            return
        dlg = BarcodePrintDialog(self, product)
        dlg.exec()
