import os
import unittest
from unittest.mock import patch


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class PendingSalesBadgeTest(unittest.TestCase):
    def setUp(self):
        from PyQt6.QtWidgets import QApplication, QLabel, QPushButton
        from ui.main_window import MainWindow

        self.app = QApplication.instance() or QApplication([])

        class _Sidebar:
            labels = {"finalize_sales": "Sotishni yakunlash"}
            pending_sale_item_count = MainWindow.pending_sale_item_count
            _counter_badge_style = staticmethod(MainWindow._counter_badge_style)
            _refresh_pending_sales_badge = MainWindow._refresh_pending_sales_badge
            _position_pending_sales_badge = MainWindow._position_pending_sales_badge

            def __init__(self):
                button = QPushButton()
                button.setFixedSize(180, 36)
                self.nav_buttons = {"finalize_sales": button}
                self.finalize_sales_badge_lbl = QLabel(button)
                self.finalize_sales_badge_lbl.setFixedHeight(16)
                self.finalize_sales_badge_lbl.hide()
                group_button = QPushButton()
                group_button.setFixedSize(180, 40)
                self.nav_group_buttons = {"products_group": group_button}
                self.products_pending_dot_lbl = QLabel(group_button)
                self.products_pending_dot_lbl.setFixedSize(10, 10)
                self.products_pending_dot_lbl.hide()

        self.sidebar = _Sidebar()
        self.addCleanup(self.sidebar.nav_buttons["finalize_sales"].deleteLater)
        self.addCleanup(self.sidebar.nav_group_buttons["products_group"].deleteLater)

    def test_exact_pending_product_count_is_shown_and_zero_hides_it(self):
        button = self.sidebar.nav_buttons["finalize_sales"]
        badge = self.sidebar.finalize_sales_badge_lbl
        group_dot = self.sidebar.products_pending_dot_lbl

        with patch("ui.main_window.db.count_pending_sale_items", return_value=27) as count:
            self.sidebar._refresh_pending_sales_badge()

        count.assert_called_once_with(only_cashiers=True)
        self.assertFalse(badge.isHidden())
        self.assertEqual(badge.text(), "27")
        self.assertFalse(group_dot.isHidden())
        self.assertEqual(group_dot.text(), "")
        self.assertIn("27", button.toolTip())
        self.assertLessEqual(badge.x() + badge.width(), button.width())
        self.assertLessEqual(
            group_dot.x() + group_dot.width(),
            self.sidebar.nav_group_buttons["products_group"].width() - 32,
        )

        with patch("ui.main_window.db.count_pending_sale_items", return_value=0):
            self.sidebar._refresh_pending_sales_badge()

        self.assertTrue(badge.isHidden())
        self.assertTrue(group_dot.isHidden())
        self.assertEqual(button.toolTip(), "")


if __name__ == "__main__":
    unittest.main()
