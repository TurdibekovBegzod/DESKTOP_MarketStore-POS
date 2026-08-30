import os
import unittest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class CashierLogoRefreshTest(unittest.TestCase):
    def test_engine_asset_import_repaints_the_cashier_logo(self):
        from ui.main_window import MainWindow

        class _CashierWindow:
            _on_sync_applied = MainWindow._on_sync_applied

            def __init__(self):
                self.logo_refreshes = 0
                self.reloaded_tables = None

            def _set_logo_icon(self):
                self.logo_refreshes += 1

            def _reload_current_page(self, tables):
                self.reloaded_tables = list(tables)

            def _settle_server_operations(self, _outcome):
                pass

            def _refresh_sync_status(self):
                pass

        window = _CashierWindow()
        window._on_sync_applied({"tables": ["account_assets"], "pulled": 0})

        self.assertEqual(window.logo_refreshes, 1)
        self.assertEqual(window.reloaded_tables, ["account_assets"])

    def test_zero_import_asset_refresh_still_repaints_from_session_cache(self):
        from ui.main_window import MainWindow

        class _CashierWindow:
            _on_remote_assets_applied = MainWindow._on_remote_assets_applied

            def __init__(self):
                self._pending_asset_generation = None
                self._pending_asset_check = None
                self.logo_refreshes = 0

            def _cleanup_sync_thread(self):
                pass

            def _set_logo_icon(self):
                self.logo_refreshes += 1

            def _refresh_sync_status(self):
                pass

            def show_toast(self, *_args, **_kwargs):
                self.fail("A zero-import cache repaint must not show a toast")

        window = _CashierWindow()
        window._on_remote_assets_applied({"imported": 0})

        self.assertEqual(window.logo_refreshes, 1)


if __name__ == "__main__":
    unittest.main()
