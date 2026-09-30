import pytest
from PyQt6.QtWidgets import QApplication
import database as db
from ui.ai_widget import AIWidget, SecretInputField


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    return app


@pytest.fixture(autouse=True)
def isolated_db(tmp_path):
    old_path = db.DB_PATH
    test_db = str(tmp_path / "test_market_pos.db")
    if db._ENGINE is not None:
        db._ENGINE.dispose()
    db._ENGINE = None
    db._ENGINE_PATH = None
    db._SessionLocal = None
    db.DB_PATH = test_db
    db.init_db()
    yield
    if db._ENGINE is not None:
        db._ENGINE.dispose()
    db._ENGINE = None
    db._ENGINE_PATH = None
    db._SessionLocal = None
    db.DB_PATH = old_path


def test_secret_input_field_masking_and_reveal(qapp):
    field = SecretInputField(placeholder="token", is_secret=True)
    field.set_value("EAA123456789")

    # Initial state: first 3 chars visible, rest masked with bullets
    display_text = field.edit.text()
    assert display_text.startswith("EAA")
    assert "•" in display_text
    assert field.value() == "EAA123456789"

    # Toggle reveal: full text becomes visible
    field._toggle_reveal()
    assert field.edit.text() == "EAA123456789"
    assert field._is_revealed is True

    # Toggle back: masked again
    field._toggle_reveal()
    assert field.edit.text().startswith("EAA")
    assert "•" in field.edit.text()
    assert field._is_revealed is False


def test_secret_input_field_copy_clipboard(qapp):
    field = SecretInputField(placeholder="secret", is_secret=True)
    field.set_value("MySecretKey999")
    field._copy_to_clipboard()

    clipboard_text = QApplication.clipboard().text()
    assert clipboard_text == "MySecretKey999"


def test_instagram_database_settings_save_and_load(qapp):
    db.init_db()

    payload = {
        "instagram_account_id": "17841405822386821",
        "instagram_access_token": "EAA_test_token_abc_123",
        "instagram_app_secret": "meta_secret_xyz",
        "instagram_verify_token": "marketstore_token_test",
        "instagram_auto_reply": "1",
        "gemini_api_key": "AIzaSy_custom_key",
    }
    db.save_instagram_settings(payload)

    loaded = db.get_instagram_settings()
    assert loaded["instagram_account_id"] == "17841405822386821"
    assert loaded["instagram_access_token"] == "EAA_test_token_abc_123"
    assert loaded["instagram_app_secret"] == "meta_secret_xyz"
    assert loaded["instagram_verify_token"] == "marketstore_token_test"
    assert loaded["instagram_auto_reply"] == "1"
    assert loaded["gemini_api_key"] == "AIzaSy_custom_key"

    # Test widget integration
    widget = AIWidget()
    widget._load_instagram_connections(check_remote=False)

    assert widget.account_id_input.value() == "17841405822386821"
    assert widget.access_token_input.value() == "EAA_test_token_abc_123"
    assert widget.access_token_input.edit.text().startswith("EAA")
    assert "•" in widget.access_token_input.edit.text()
    assert widget.auto_reply_chk.isChecked() is True

    # Not verified yet -> must be disconnected (grey) even with filled fields
    assert widget.ig_status_dot.state == "disconnected"

    # When verified -> status dot turns active (green)
    widget._is_verified = True
    widget._baseline_verified = True
    widget._update_connection_status()
    assert widget.ig_status_dot.state == "active"

    # Test toggling auto reply switch to disabled -> status dot turns disabled (yellow)
    widget.auto_reply_chk.setChecked(False)
    assert widget.ig_status_dot.state == "disabled"

    # Test toggling auto reply back to enabled -> turns active (green)
    widget.auto_reply_chk.setChecked(True)
    assert widget.ig_status_dot.state == "active"

    # Editing input invalidates verification -> turns disconnected (grey)
    widget.account_id_input.edit.textEdited.emit("changed_id")
    assert widget._is_verified is False
    assert widget.ig_status_dot.state == "disconnected"
    assert widget.conn_save_btn.isEnabled() is True

    # Reverting to baseline disables Saqlash button again
    widget.account_id_input.edit.textEdited.emit("17841405822386821")
    assert widget.conn_save_btn.isEnabled() is False

    # Simulating save verification success
    widget._on_save_test_finished(True, "Aloqa o'rnatildi: @teststore", "17841405822386821")
    assert widget._is_verified is True
    assert widget.ig_status_dot.state == "active"
    assert widget.conn_save_btn.isEnabled() is False
    assert widget.conn_save_btn.text() == "Saqlash"

    # Simulating save verification failure
    widget._on_save_test_finished(False, "Xatolik: Token muddati tugagan", "")
    assert widget._is_verified is False
    assert widget.ig_status_dot.state == "disconnected"
    assert widget.conn_save_btn.isEnabled() is False


def test_saqlash_button_dirty_state_tracking(qapp):
    db.init_db()
    db.save_instagram_settings({
        "instagram_account_id": "111",
        "instagram_access_token": "EAA_test",
        "instagram_app_secret": "secret_abc",
        "instagram_auto_reply": "1",
    })

    widget = AIWidget()
    widget._load_instagram_connections(check_remote=False)

    # Initial state: Saqlash button is disabled
    assert widget.conn_save_btn.text() == "Saqlash"
    assert widget.conn_save_btn.isEnabled() is False

    # 1. Test Field 1: account_id_input
    widget.account_id_input.edit.textEdited.emit("111999")
    assert widget.conn_save_btn.isEnabled() is True
    # Revert to original
    widget.account_id_input.edit.textEdited.emit("111")
    assert widget.conn_save_btn.isEnabled() is False

    # 2. Test Field 2: access_token_input
    widget.access_token_input.edit.textEdited.emit("EAA_new_token")
    assert widget.conn_save_btn.isEnabled() is True
    # Revert to original
    widget.access_token_input.edit.textEdited.emit("EAA_test")
    assert widget.conn_save_btn.isEnabled() is False

    # 3. Test Field 3: app_secret_input
    widget.app_secret_input.edit.textEdited.emit("secret_new")
    assert widget.conn_save_btn.isEnabled() is True
    # Revert to original
    widget.app_secret_input.edit.textEdited.emit("secret_abc")
    assert widget.conn_save_btn.isEnabled() is False

    # 4. Test Field 4: auto_reply_chk (Toggle switch)
    widget.auto_reply_chk.setChecked(False)
    assert widget.conn_save_btn.isEnabled() is True
    # Revert to original
    widget.auto_reply_chk.setChecked(True)
    assert widget.conn_save_btn.isEnabled() is False


def test_clear_instagram_settings_wipes_credentials(qapp):
    """Credentials this shop may not use must not stay on the machine."""
    db.init_db()
    db.save_instagram_settings({
        "instagram_account_id": "29087822314156401",
        "instagram_access_token": "EAA_taken_token",
        "instagram_app_secret": "taken_secret",
        "instagram_verify_token": "keep_me",
        "instagram_auto_reply": "1",
        "gemini_api_key": "AIzaKeepMe",
    })

    db.clear_instagram_settings()

    loaded = db.get_instagram_settings()
    assert loaded["instagram_account_id"] == ""
    assert loaded["instagram_access_token"] == ""
    assert loaded["instagram_app_secret"] == ""
    # Auto-reply is switched off so the bot cannot keep answering.
    assert loaded["instagram_auto_reply"] == "0"
    # The webhook verify token and Gemini key are this shop's own, not the
    # other account's, so they survive.
    assert loaded["instagram_verify_token"] == "keep_me"
    assert loaded["gemini_api_key"] == "AIzaKeepMe"


def test_account_taken_by_another_shop_is_refused_and_cleared(qapp):
    db.init_db()

    widget = AIWidget()
    widget.account_id_input.set_value("29087822314156401")
    widget.access_token_input.set_value("EAA_taken_token")
    widget.app_secret_input.set_value("taken_secret")
    widget.auto_reply_chk.setChecked(True)

    widget._on_claim_check_finished("taken", "be**********@gmail.com", True, "Aloqa o'rnatildi")

    # Nothing written, fields emptied, bot off
    loaded = db.get_instagram_settings()
    assert loaded["instagram_account_id"] == ""
    assert loaded["instagram_access_token"] == ""
    assert widget.account_id_input.value() == ""
    assert widget.access_token_input.value() == ""
    assert widget.app_secret_input.value() == ""
    assert widget.auto_reply_chk.isChecked() is False

    # Status reports the conflict and names the masked owner
    status_text = widget.conn_status_lbl.text()
    assert "boshqa do'konga ulangan" in status_text
    assert "be**********@gmail.com" in status_text

    assert widget._is_verified is False
    assert widget.ig_status_dot.state == "disconnected"
    # Cleared fields equal the cleared baseline, so there is nothing to re-save
    assert widget.conn_save_btn.isEnabled() is False
    assert widget.conn_save_btn.text() == "Saqlash"


def test_free_account_is_saved_normally(qapp):
    db.init_db()

    widget = AIWidget()
    widget.account_id_input.set_value("17841405822386821")
    widget.access_token_input.set_value("EAA_free_token")
    widget.app_secret_input.set_value("free_secret")
    widget.auto_reply_chk.setChecked(True)

    widget._on_claim_check_finished("free", "", True, "Aloqa o'rnatildi: @myshop")

    loaded = db.get_instagram_settings()
    assert loaded["instagram_account_id"] == "17841405822386821"
    assert loaded["instagram_access_token"] == "EAA_free_token"
    assert loaded["instagram_auto_reply"] == "1"
    assert widget._is_verified is True
    assert widget.ig_status_dot.state == "active"


def test_unreachable_server_still_saves_locally(qapp):
    """An offline save must not be blocked; the sync guard catches it later."""
    db.init_db()

    widget = AIWidget()
    widget.account_id_input.set_value("17841405822386821")
    widget.access_token_input.set_value("EAA_offline_token")
    widget.app_secret_input.set_value("offline_secret")

    widget._on_claim_check_finished("unknown", "", True, "Aloqa o'rnatildi")

    loaded = db.get_instagram_settings()
    assert loaded["instagram_account_id"] == "17841405822386821"
    assert loaded["instagram_access_token"] == "EAA_offline_token"


def test_claim_check_runs_before_saving_when_signed_in(qapp):
    """The save must not write anything until the server has answered."""
    db.init_db()

    widget = AIWidget(user={"id": 1, "api_access_token": "tok"})
    widget.account_id_input.set_value("29087822314156401")
    widget.access_token_input.set_value("EAA_pending_token")
    widget.app_secret_input.set_value("pending_secret")

    started = []

    class FakeClaimWorker:
        def __init__(self, user, account_id):
            self.account_id = account_id
            self.finished_signal = _FakeSignal()

        def start(self):
            started.append(self.account_id)

    import ui.ai_widget as ai_widget_module
    original = ai_widget_module.AccountClaimWorker
    ai_widget_module.AccountClaimWorker = FakeClaimWorker
    try:
        widget._on_save_test_finished(True, "Aloqa o'rnatildi", "29087822314156401")
    finally:
        ai_widget_module.AccountClaimWorker = original

    assert started == ["29087822314156401"]
    # Nothing stored yet: the answer has not come back
    loaded = db.get_instagram_settings()
    assert loaded["instagram_account_id"] == ""
    assert loaded["instagram_access_token"] == ""


class _FakeSignal:
    def connect(self, handler):
        self.handler = handler
