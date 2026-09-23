import sys
import os
import traceback
from pathlib import Path

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QFont, QIcon
import database as db
import sync_service
from ui.login_dialog import LoginDialog
from ui.main_window import MainWindow


def resource_path(relative_path):
    base_path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return str(base_path / relative_path)


APP_ICON_PATH = resource_path(
    "images/desktop.png" if sys.platform == "darwin" else "images/desktop_icon.ico"
)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("MarketStore POS")
    app.setOrganizationName("MarketStore")
    app.setOrganizationDomain("marketstore.uz")
    app.setWindowIcon(QIcon(APP_ICON_PATH))

    # Release CI uses this to verify that the packaged Qt runtime can start.
    if os.environ.get("MARKETSTORE_PACKAGING_SMOKE_TEST") == "1":
        from ssl_support import verify_ca_bundle

        ca_source = verify_ca_bundle()
        print(f"MARKETSTORE_PACKAGING_SMOKE_OK CA={ca_source}")
        return 0

    # Global font
    font = QFont("Segoe UI", 10)
    app.setFont(font)

    # Global stylesheet
    app.setStyleSheet("""
        QScrollBar:vertical {
            border: none; background: #f1f5f9; width: 8px; border-radius: 4px;
        }
        QScrollBar::handle:vertical {
            background: #cbd5e1; border-radius: 4px; min-height: 20px;
        }
        QScrollBar::handle:vertical:hover { background: #94a3b8; }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        QToolTip { background: #1e293b; color: white; border: none; padding: 4px 8px; border-radius: 4px; }
    """)

    try:
        recent_user = db.restore_recent_account_user(max_days=7)
    except Exception:
        # A broken/partial session file must never block the login screen.
        traceback.print_exc()
        recent_user = None
    if recent_user:
        recent_user["role"] = "cashier"
        db.touch_user_activity(recent_user["id"])
        # Entering without the login screen is still an entry into the system,
        # so it belongs in the login history like any other. A failure here is
        # reported rather than swallowed: a silently missing entry is exactly
        # the gap that made the history look empty before.
        try:
            db.log_login(dict(recent_user), event="session_restored")
        except Exception as exc:
            traceback.print_exc()
            try:
                db.log_activity(
                    "user_login",
                    "Kirish tarixiga yozib bo'lmadi",
                    f"Saqlangan sessiya bilan kirish yozilmadi: {exc}",
                    level="warning",
                    target="login_history",
                    badge="Xato",
                )
            except Exception:
                traceback.print_exc()
        window = MainWindow(dict(recent_user))
        window.showMaximized()
        sys.exit(app.exec())

    login = LoginDialog()
    if login.exec():
        db.touch_user_activity(login.logged_user["id"])
        window = MainWindow(login.logged_user)
        window.showMaximized()
        sys.exit(app.exec())
    sys.exit(0)


if __name__ == "__main__":
    main()
