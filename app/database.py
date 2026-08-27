import base64
import gzip
import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import sys
import threading
import uuid
from contextlib import closing, contextmanager, nullcontext
from datetime import datetime, timedelta, timezone

from sqlalchemy import (
    Column,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    and_,
    case,
    create_engine,
    delete,
    event,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, declarative_base, relationship, sessionmaker
from sqlalchemy.sql import text


def get_app_data_dir() -> str:
    """Returns writable user data directory across Windows, Linux, macOS, or local dev."""
    if getattr(sys, "frozen", False):
        # Running as packaged .exe / AppImage / .app binary
        if sys.platform.startswith("win"):
            base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
            app_dir = os.path.join(base, "MarketStore-POS", "data")
        elif sys.platform.startswith("darwin"):
            app_dir = os.path.expanduser("~/Library/Application Support/MarketStore-POS/data")
        else:
            base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
            app_dir = os.path.join(base, "marketstore-pos", "data")
        os.makedirs(app_dir, exist_ok=True)
        return app_dir
    else:
        # Development mode
        local_data = os.path.abspath("data")
        os.makedirs(local_data, exist_ok=True)
        return local_data


DATA_DIR = get_app_data_dir()
LEGACY_DB_PATH = os.path.join(DATA_DIR, "market_pos.db")
ACCOUNT_DB_ROOT = os.path.join(DATA_DIR, "accounts")
ACCOUNT_SESSION_PATH = os.path.join(DATA_DIR, "account_session.json")
DB_PATH = LEGACY_DB_PATH

# Expenses filed under this category are taken out of the selected cashier's
# salary instead of being a plain shop expense.
CASHIER_EXPENSE_CATEGORY_NAME = "Kassir"
CASHIER_EXPENSE_CATEGORY_ALIASES = frozenset({"kassir", "cashier", "кассир"})

Base = declarative_base()
_ENGINE = None
_ENGINE_PATH = None
_SessionLocal = None
# Sync suppression is per-thread, not per-process. The sync worker imports on
# its own thread while the GUI thread commits sales; a shared flag made those
# concurrent sales invisible to the outbox, so they were never pushed.
_SYNC_LOCAL = threading.local()


def _is_sync_suspended():
    return getattr(_SYNC_LOCAL, "suspended", False)


@contextmanager
def suspend_sync():
    """Stop THIS thread's writes from entering the sync outbox."""
    previous = getattr(_SYNC_LOCAL, "suspended", False)
    _SYNC_LOCAL.suspended = True
    try:
        yield
    finally:
        _SYNC_LOCAL.suspended = previous
_ACTIVE_ACCOUNT_UID = None

SYNC_TABLES = (
    "users",
    "categories",
    "currencies",
    "app_settings",
    "account_assets",
    "product_sections",
    "suppliers",
    "customers",
    "expense_categories",
    "product_templates",
    "product_template_fields",
    "products",
    "product_attributes",
    "supplier_debt_movements",
    "debtors",
    "debtor_debt_movements",
    "expenses",
    "sales",
    "sale_items",
    "sale_returns",
    "customer_debt_movements",
    "stock_movements",
    "inventory_check_sessions",
    "inventory_check_items",
    "finance_manual_movements",
    "activity_logs",
)


# app_settings is keyed by a setting name and account_assets by an asset name;
# every other synchronised table is keyed by a UUID.
UUID_KEYED_TABLES = frozenset(
    table for table in SYNC_TABLES if table not in ("app_settings", "account_assets")
)


# Money must be written where every device can see it. A sale rung up with no
# way to reach the server cannot be reconciled with what the other devices did
# in the meantime, so it is refused rather than written and argued about later.
# Unset means unrestricted, and "not known to be offline" counts as online.
_ONLINE_CHECK = None


def set_online_check(callback):
    """Tell the database how to find out whether the server is reachable."""
    global _ONLINE_CHECK
    _ONLINE_CHECK = callback


def is_online():
    if _ONLINE_CHECK is None:
        return True
    try:
        return bool(_ONLINE_CHECK())
    except Exception:
        return True


def require_online(action=None):
    if is_online():
        return
    what = f" ({action})" if action else ""
    raise AppError(
        "Internet aloqasi yo'q, shuning uchun bu amalni bajarib bo'lmaydi"
        f"{what}.\n\nAloqa tiklanganda qayta urinib ko'ring."
    )


class AppError(Exception):
    """User-facing application error."""


class Row(dict):
    """Small dict row that keeps the old row-style access working."""

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc


@event.listens_for(Session, "before_flush")
def _mark_session_deletes(session, _flush_context, _instances):
    if _is_sync_suspended():
        return
    try:
        entries = session.info.setdefault("sync_outbox_entries", set())
        for obj in session.deleted:
            table_name = getattr(obj, "__tablename__", None)
            if table_name and table_name in SYNC_TABLES:
                local_id = str(
                    getattr(obj, "id", None)
                    if getattr(obj, "id", None) is not None
                    else getattr(obj, "code", None)
                    if getattr(obj, "code", None) is not None
                    else getattr(obj, "key", None)
                )
                if local_id and local_id != "None":
                    entries.add((table_name, local_id, "delete"))
    except Exception:
        pass


@event.listens_for(Session, "after_flush")
def _mark_session_writes(session, _flush_context):
    session.info["has_writes"] = True
    if _is_sync_suspended():
        return
    try:
        entries = session.info.setdefault("sync_outbox_entries", set())
        for obj in session.new | session.dirty:
            table_name = getattr(obj, "__tablename__", None)
            if table_name and table_name in SYNC_TABLES:
                local_id = str(
                    getattr(obj, "id", None)
                    if getattr(obj, "id", None) is not None
                    else getattr(obj, "code", None)
                    if getattr(obj, "code", None) is not None
                    else getattr(obj, "key", None)
                )
                if local_id and local_id != "None":
                    entries.add((table_name, local_id, "upsert"))
    except Exception:
        pass


@event.listens_for(Session, "do_orm_execute")
def _mark_bulk_writes(execute_state):
    if execute_state.is_delete or execute_state.is_update:
        execute_state.session.info["has_writes"] = True


def _hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120000)
    return f"pbkdf2_sha256${salt}${digest.hex()}"


def _verify_password(password, stored_password):
    if not stored_password:
        return False
    if not stored_password.startswith("pbkdf2_sha256$"):
        return secrets.compare_digest(password, stored_password)
    try:
        _, salt, _ = stored_password.split("$", 2)
    except ValueError:
        return False
    return secrets.compare_digest(_hash_password(password, salt), stored_password)


def _database_url():
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    abs_path = os.path.abspath(DB_PATH).replace("\\", "/")
    return f"sqlite:///{abs_path}"


def _safe_account_uid(user_uid):
    value = str(user_uid or "").strip().lower()
    if value and all(char.isalnum() or char == "-" for char in value):
        return value
    if not value:
        raise AppError("Online account UID topilmadi.")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _email_account_key(email):
    normalized = _normalize_email(email)
    if not normalized:
        raise AppError("Online account emaili topilmadi.")
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"email-{digest[:32]}"


def account_database_path(user_uid, email=None, storage_root=None):
    root = storage_root or ACCOUNT_DB_ROOT
    account_key = _email_account_key(email) if email else _safe_account_uid(user_uid)
    return os.path.join(root, account_key, "market_pos.db")


def _account_migration_marker(email, storage_root=None):
    root = storage_root or ACCOUNT_DB_ROOT
    return os.path.join(root, ".migrations", _email_account_key(email) + ".done")


def _switch_database_path(path):
    global DB_PATH, _ENGINE, _ENGINE_PATH, _SessionLocal
    normalized = os.path.normpath(path)
    if _ENGINE is not None and _ENGINE_PATH != normalized:
        _ENGINE.dispose()
        _ENGINE = None
        _ENGINE_PATH = None
        _SessionLocal = None
    DB_PATH = normalized


def _jwt_subject(token):
    try:
        payload = str(token).split(".", 2)[1]
        payload += "=" * (-len(payload) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8"))
        return str(decoded.get("sub") or "")
    except (ValueError, IndexError, TypeError, json.JSONDecodeError):
        return ""


def _legacy_database_matches_account(user_uid, email):
    if not os.path.exists(LEGACY_DB_PATH):
        return False
    try:
        with sqlite3.connect(LEGACY_DB_PATH) as conn:
            row = conn.execute(
                """
                SELECT user_settings.value
                FROM users
                JOIN user_settings ON user_settings.user_id = users.id
                WHERE lower(users.email) = lower(?) AND user_settings.key = 'api_access_token'
                ORDER BY users.id DESC
                LIMIT 1
                """,
                (email,),
            ).fetchone()
    except sqlite3.Error:
        return False
    if not row:
        return False
    legacy_uid = _jwt_subject(row[0])
    if legacy_uid == str(user_uid):
        return True
    recent_account = _read_account_session()
    return bool(
        legacy_uid
        and recent_account.get("user_uid") == legacy_uid
        and _normalize_email(recent_account.get("email")) == _normalize_email(email)
    )


def _copy_sqlite_database(source_path, target_path):
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    # sqlite3.Connection's context manager commits but does not close. Explicit
    # closing matters on Windows, where an open backup handle cannot be erased
    # by a later account-wide purge.
    with closing(sqlite3.connect(source_path)) as source, closing(sqlite3.connect(target_path)) as target:
        source.backup(target)


def activate_account_database(user_uid, email=None, allow_legacy_import=False, storage_root=None):
    global _ACTIVE_ACCOUNT_UID
    safe_uid = _safe_account_uid(user_uid)
    target_path = account_database_path(safe_uid, email=email, storage_root=storage_root)
    old_uid_path = account_database_path(safe_uid, storage_root=storage_root)
    migration_marker = _account_migration_marker(email, storage_root) if email else None
    can_import_old_database = not migration_marker or not os.path.exists(migration_marker)
    database_existed = os.path.exists(target_path)
    imported_legacy = False
    if (
        not database_existed
        and can_import_old_database
        and os.path.exists(old_uid_path)
        and old_uid_path != target_path
    ):
        _copy_sqlite_database(old_uid_path, target_path)
        imported_legacy = True
    elif (
        not database_existed
        and can_import_old_database
        and allow_legacy_import
        and email
        and _legacy_database_matches_account(safe_uid, email)
    ):
        _copy_sqlite_database(LEGACY_DB_PATH, target_path)
        imported_legacy = True
    if migration_marker:
        os.makedirs(os.path.dirname(migration_marker), exist_ok=True)
        if not os.path.exists(migration_marker):
            with open(migration_marker, "w", encoding="utf-8") as marker:
                marker.write("email-account-db-v1\n")
    _switch_database_path(target_path)
    _ACTIVE_ACCOUNT_UID = safe_uid
    try:
        _add_missing_columns()
    except Exception:
        pass
    clear_session_notifications()
    return Row(
        path=target_path,
        imported_legacy=imported_legacy,
        database_created=not database_existed and not imported_legacy,
    )


def _read_account_session():
    try:
        with open(ACCOUNT_SESSION_PATH, "r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_account_session(data):
    directory = os.path.dirname(ACCOUNT_SESSION_PATH)
    if directory:
        os.makedirs(directory, exist_ok=True)
    temporary_path = ACCOUNT_SESSION_PATH + ".tmp"
    with open(temporary_path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=True, indent=2)
    os.replace(temporary_path, ACCOUNT_SESSION_PATH)


def save_account_session(api_user, access_token):
    user_uid = _safe_account_uid((api_user or {}).get("user_uid") or (api_user or {}).get("uid"))
    email = _normalize_email((api_user or {}).get("email"))
    now = _utc_now()
    now_dt = datetime.now(timezone.utc)
    expires_at = (now_dt + timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")

    existing = _read_account_session()
    accounts = existing.get("accounts", {})
    if not isinstance(accounts, dict):
        accounts = {}

    account_entry = {
        "user_uid": user_uid,
        "email": email,
        "display_name": ((api_user or {}).get("display_name") or "").strip(),
        "api_user_id": (api_user or {}).get("id"),
        "api_access_token": access_token or "",
        "login_at_utc": now,
        "expires_at_utc": expires_at,
        "last_activity_utc": now,
    }
    if email:
        accounts[email] = account_entry

    data = {
        "user_uid": user_uid,
        "email": email,
        "display_name": ((api_user or {}).get("display_name") or "").strip(),
        "api_user_id": (api_user or {}).get("id"),
        "api_access_token": access_token or "",
        "login_at_utc": now,
        "expires_at_utc": expires_at,
        "last_activity_utc": now,
        "accounts": accounts,
    }
    _write_account_session(data)


def is_account_session_expired(data=None, max_days=7):
    session_data = data if data is not None else _read_account_session()
    if not session_data:
        return True
    expires_str = session_data.get("expires_at_utc")
    login_str = session_data.get("login_at_utc") or session_data.get("last_activity_utc")
    now_utc = datetime.now(timezone.utc)

    if expires_str:
        try:
            exp_dt = datetime.strptime(str(expires_str), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            return now_utc >= exp_dt
        except ValueError:
            pass

    if login_str:
        try:
            login_dt = datetime.strptime(str(login_str), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            return (now_utc - login_dt) > timedelta(days=max_days)
        except ValueError:
            pass

    return False


def clear_account_session(email=None):
    if not email:
        if os.path.exists(ACCOUNT_SESSION_PATH):
            try:
                os.remove(ACCOUNT_SESSION_PATH)
            except OSError:
                pass
        return
    data = _read_account_session()
    accounts = data.get("accounts", {})
    normalized = _normalize_email(email)
    if isinstance(accounts, dict) and normalized in accounts:
        del accounts[normalized]
    if _normalize_email(data.get("email")) == normalized:
        data["user_uid"] = ""
        data["email"] = ""
        data["api_access_token"] = ""
        data["login_at_utc"] = ""
        data["expires_at_utc"] = ""
    _write_account_session(data)


def _touch_account_session(active=True):
    if not _ACTIVE_ACCOUNT_UID:
        return
    data = _read_account_session()
    if data.get("user_uid") != _ACTIVE_ACCOUNT_UID:
        return
    now = _utc_now() if active else ""
    data["last_activity_utc"] = now
    email = data.get("email")
    if email and isinstance(data.get("accounts"), dict) and email in data["accounts"]:
        data["accounts"][email]["last_activity_utc"] = now
    _write_account_session(data)


def _get_engine():
    global _ENGINE, _ENGINE_PATH, _SessionLocal
    if _ENGINE is None or _ENGINE_PATH != DB_PATH:
        _ENGINE_PATH = DB_PATH
        _ENGINE = create_engine(
            _database_url(),
            future=True,
            connect_args={"timeout": 30, "check_same_thread": False},
        )

        @event.listens_for(_ENGINE, "connect")
        def _set_sqlite_pragmas(dbapi_connection, _):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.execute("PRAGMA busy_timeout = 30000")
            cursor.close()

        _SessionLocal = sessionmaker(bind=_ENGINE, autoflush=False, expire_on_commit=False, future=True)
    return _ENGINE


def _session_factory():
    _get_engine()
    return _SessionLocal


@contextmanager
def session_scope():
    session = _session_factory()()
    try:
        yield session
        if session.new or session.dirty or session.deleted or session.info.get("has_writes"):
            # Flush first so the after_flush listener has collected everything,
            # then write the outbox on the same connection, then commit once.
            # Data and queue now land together or not at all.
            session.flush()
            _flush_session_outbox(session)
            session.commit()
        else:
            session.rollback()
    except OperationalError as exc:
        session.rollback()
        _raise_database_busy(exc)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _raise_database_busy(exc):
    if "locked" in str(exc).lower():
        raise AppError(
            "Ma'lumotlar bazasi hozir band. Dasturning boshqa ochiq oynasi bo'lsa yoping va qayta urinib ko'ring."
        ) from exc
    raise exc


def _row_from_model(obj, **extra):
    if obj is None:
        return None
    data = {column.name: getattr(obj, column.name) for column in obj.__table__.columns}
    data.update(extra)
    return Row(data)


def _rows_from_models(items):
    return [_row_from_model(item) for item in items]


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _utc_to_local(value):
    if not value:
        return value
    if isinstance(value, datetime):
        source = value
    else:
        try:
            source = datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return value
    return source.replace(tzinfo=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _date_expr(column):
    return func.date(column, "localtime")


def _local_date_label(value):
    local_value = _utc_to_local(value)
    return local_value[:10] if local_value else ""


def _local_hour_label(value):
    local_value = _utc_to_local(value)
    hour = local_value[11:13] if local_value else "00"
    return f"{hour}:00"


def _trash_now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _trash_purge_after():
    return (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")


def _normalize_email(value):
    email = (value or "").strip().lower()
    if "@" not in email or "." not in email.rsplit("@", 1)[-1]:
        return ""
    return email


def _email_from_username(username):
    normalized = _normalize_email(username)
    if normalized:
        return normalized
    safe = "".join(ch.lower() if ch.isalnum() else "." for ch in str(username or "user")).strip(".") or "user"
    return f"{safe}@gmail.com"


# --- Row identity -----------------------------------------------------------
#
# Every synchronised row is identified by a UUID rather than by the SQLite
# rowid.  Two devices used to hand the same integer to two different sales,
# and the server -- which keys records by (account, table, local_id) -- kept
# only one of them.  A UUID cannot collide, so no sale can overwrite another.
#
# ``ROW_ID_NAMESPACE`` is fixed for the lifetime of the product: migration 012
# derives each existing row's UUID from it, so every device converts its own
# copy of the same row to exactly the same identifier without talking to the
# server first.  Changing this constant would re-scramble every installation.

ROW_ID_NAMESPACE = uuid.UUID("6d61726b-6574-7374-6f72-652d706f7300")
ROW_ID_LENGTH = 36


def new_row_id():
    """Identifier for a brand new row."""
    return str(uuid.uuid4())


def stable_row_id(table_name, legacy_id):
    """The UUID that a pre-migration row must get, on every device alike."""
    return str(uuid.uuid5(ROW_ID_NAMESPACE, f"{table_name}:{legacy_id}"))


def is_row_uuid(value):
    """True when a value looks like an identifier this schema can accept."""
    try:
        uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return False
    return True


def owner_row_id(account_uid):
    """The account owner's user row, identical on every device of the account."""
    key = str(account_uid or "").strip().lower()
    if not key:
        return None
    return str(uuid.uuid5(ROW_ID_NAMESPACE, f"account-owner:{key}"))


class User(Base):
    __tablename__ = "users"
    id = Column(String(ROW_ID_LENGTH), primary_key=True, default=new_row_id)
    username = Column(String, unique=True, nullable=False)
    email = Column(String, unique=True)
    password = Column(String, nullable=False)
    role = Column(String, default="cashier")
    created_at = Column(String, server_default=text("CURRENT_TIMESTAMP"))


class LoginLog(Base):
    __tablename__ = "login_logs"
    id = Column(String(ROW_ID_LENGTH), primary_key=True, default=new_row_id)
    user_id = Column(String(ROW_ID_LENGTH), ForeignKey("users.id"))
    username = Column(String, nullable=False)
    role = Column(String, nullable=False)
    logged_at = Column(String, server_default=text("CURRENT_TIMESTAMP"))


class Category(Base):
    __tablename__ = "categories"
    id = Column(String(ROW_ID_LENGTH), primary_key=True, default=new_row_id)
    name = Column(String, unique=True, nullable=False)


class Currency(Base):
    __tablename__ = "currencies"
    id = Column(String(ROW_ID_LENGTH), primary_key=True, default=new_row_id)
    code = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    rate_to_uzs = Column(Float, nullable=False, default=1)
    is_base = Column(Integer, default=0)
    updated_at = Column(String, server_default=text("CURRENT_TIMESTAMP"))


class AppSetting(Base):
    __tablename__ = "app_settings"
    key = Column(String, primary_key=True)
    value = Column(String)


class AccountAsset(Base):
    __tablename__ = "account_assets"
    id = Column(String, primary_key=True)
    media_type = Column(String, nullable=False, default="application/octet-stream")
    content_base64 = Column(Text, nullable=False)
    sha256 = Column(String, nullable=False)
    updated_at = Column(String, nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class UserSetting(Base):
    __tablename__ = "user_settings"
    user_id = Column(String(ROW_ID_LENGTH), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    key = Column(String, primary_key=True)
    value = Column(String)


class SyncTombstone(Base):
    __tablename__ = "sync_tombstones"
    table_name = Column(String, primary_key=True)
    local_id = Column(String, primary_key=True)
    deleted_at = Column(String, nullable=False)


class ProductTemplate(Base):
    __tablename__ = "product_templates"
    id = Column(String(ROW_ID_LENGTH), primary_key=True, default=new_row_id)
    section_id = Column(String(ROW_ID_LENGTH), ForeignKey("product_sections.id", ondelete="CASCADE"))
    name = Column(String, nullable=False)
    original_name = Column(String)
    is_deleted = Column(Integer, default=0)
    deleted_at = Column(String)
    purge_after = Column(String)
    created_at = Column(String, server_default=text("CURRENT_TIMESTAMP"))
    fields = relationship("ProductTemplateField", cascade="all, delete-orphan")
    __table_args__ = (UniqueConstraint("section_id", "name"),)


class ProductSection(Base):
    __tablename__ = "product_sections"
    id = Column(String(ROW_ID_LENGTH), primary_key=True, default=new_row_id)
    name = Column(String, unique=True, nullable=False)
    original_name = Column(String)
    is_deleted = Column(Integer, default=0)
    deleted_at = Column(String)
    purge_after = Column(String)
    created_at = Column(String, server_default=text("CURRENT_TIMESTAMP"))


class ProductTemplateField(Base):
    __tablename__ = "product_template_fields"
    id = Column(String(ROW_ID_LENGTH), primary_key=True, default=new_row_id)
    template_id = Column(String(ROW_ID_LENGTH), ForeignKey("product_templates.id", ondelete="CASCADE"), nullable=False)
    name = Column(String, nullable=False)
    field_type = Column(String, default="text")
    required = Column(Integer, default=0)
    sort_order = Column(Integer, default=0)


class Product(Base):
    __tablename__ = "products"
    id = Column(String(ROW_ID_LENGTH), primary_key=True, default=new_row_id)
    barcode = Column(String, unique=True)
    original_barcode = Column(String)
    name = Column(String, nullable=False)
    section_id = Column(String(ROW_ID_LENGTH), ForeignKey("product_sections.id"))
    template_id = Column(String(ROW_ID_LENGTH), ForeignKey("product_templates.id"))
    supplier_id = Column(String(ROW_ID_LENGTH), ForeignKey("suppliers.id"))
    category_id = Column(String(ROW_ID_LENGTH), ForeignKey("categories.id"))
    created_by_user_id = Column(String(ROW_ID_LENGTH), ForeignKey("users.id", ondelete="SET NULL"))
    price = Column(Float, nullable=False, default=0)
    cost = Column(Float, nullable=False, default=0)
    price_currency = Column(String, default="UZS")
    price_exchange_rate = Column(Float, default=1)
    price_original = Column(Float, default=0)
    cost_currency = Column(String, default="UZS")
    cost_exchange_rate = Column(Float, default=1)
    cost_original = Column(Float, default=0)
    stock = Column(Integer, nullable=False, default=0)
    unit = Column(String, default="dona")
    process_status = Column(String, default="available")
    process_quantity = Column(Integer, default=0)
    process_deposit = Column(Float, default=0)
    process_deposit_currency = Column(String, default="UZS")
    process_customer_name = Column(String)
    process_customer_phone = Column(String)
    process_cashier_name = Column(String)
    is_deleted = Column(Integer, default=0)
    deleted_at = Column(String)
    purge_after = Column(String)
    created_at = Column(String, server_default=text("CURRENT_TIMESTAMP"))


class ProductAttribute(Base):
    __tablename__ = "product_attributes"
    id = Column(String(ROW_ID_LENGTH), primary_key=True, default=new_row_id)
    product_id = Column(String(ROW_ID_LENGTH), ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    field_id = Column(String(ROW_ID_LENGTH), ForeignKey("product_template_fields.id", ondelete="CASCADE"), nullable=False)
    value = Column(String)
    __table_args__ = (UniqueConstraint("product_id", "field_id"),)


class Customer(Base):
    __tablename__ = "customers"
    id = Column(String(ROW_ID_LENGTH), primary_key=True, default=new_row_id)
    name = Column(String, nullable=False)
    phone = Column(String)
    email = Column(String)
    balance = Column(Float, default=0)
    total_purchases = Column(Float, default=0)
    created_at = Column(String, server_default=text("CURRENT_TIMESTAMP"))


class Supplier(Base):
    __tablename__ = "suppliers"
    id = Column(String(ROW_ID_LENGTH), primary_key=True, default=new_row_id)
    name = Column(String, nullable=False)
    phone = Column(String)
    note = Column(String)
    debt_currency = Column(String, default="UZS")
    balance = Column(Float, default=0)
    total_received = Column(Float, default=0)
    created_at = Column(String, server_default=text("CURRENT_TIMESTAMP"))


class SupplierDebtMovement(Base):
    __tablename__ = "supplier_debt_movements"
    id = Column(String(ROW_ID_LENGTH), primary_key=True, default=new_row_id)
    supplier_id = Column(String(ROW_ID_LENGTH), ForeignKey("suppliers.id"), nullable=False)
    type = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    note = Column(String)
    created_at = Column(String, server_default=text("CURRENT_TIMESTAMP"))


class Debtor(Base):
    __tablename__ = "debtors"
    id = Column(String(ROW_ID_LENGTH), primary_key=True, default=new_row_id)
    user_id = Column(String(ROW_ID_LENGTH), ForeignKey("users.id"))
    name = Column(String, nullable=False)
    phone = Column(String)
    note = Column(String)
    debt_currency = Column(String, default="UZS")
    balance = Column(Float, default=0)
    total_given = Column(Float, default=0)
    created_at = Column(String, server_default=text("CURRENT_TIMESTAMP"))


class DebtorDebtMovement(Base):
    __tablename__ = "debtor_debt_movements"
    id = Column(String(ROW_ID_LENGTH), primary_key=True, default=new_row_id)
    debtor_id = Column(String(ROW_ID_LENGTH), ForeignKey("debtors.id"), nullable=False)
    type = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    note = Column(String)
    created_at = Column(String, server_default=text("CURRENT_TIMESTAMP"))


class ExpenseCategory(Base):
    __tablename__ = "expense_categories"
    id = Column(String(ROW_ID_LENGTH), primary_key=True, default=new_row_id)
    name = Column(String, unique=True, nullable=False)


class Expense(Base):
    __tablename__ = "expenses"
    id = Column(String(ROW_ID_LENGTH), primary_key=True, default=new_row_id)
    category_id = Column(String(ROW_ID_LENGTH), ForeignKey("expense_categories.id"))
    user_id = Column(String(ROW_ID_LENGTH), ForeignKey("users.id"))
    cashier_id = Column(String(ROW_ID_LENGTH), ForeignKey("users.id", ondelete="SET NULL"))
    amount = Column(Float, nullable=False)
    currency_code = Column(String, default="UZS")
    description = Column(String)
    created_at = Column(String, server_default=text("CURRENT_TIMESTAMP"))


class Sale(Base):
    __tablename__ = "sales"
    id = Column(String(ROW_ID_LENGTH), primary_key=True, default=new_row_id)
    # Shown to the cashier as "Sotuv #12".  Display only: nothing looks a sale
    # up by this number, so two devices producing the same one is cosmetic.
    display_no = Column(Integer)
    customer_id = Column(String(ROW_ID_LENGTH), ForeignKey("customers.id"))
    cashier_id = Column(String(ROW_ID_LENGTH), ForeignKey("users.id"))
    customer_name = Column(String)
    customer_phone = Column(String)
    total = Column(Float, nullable=False)
    discount = Column(Float, default=0)
    paid = Column(Float, nullable=False)
    change = Column(Float, default=0)
    currency_code = Column(String, default="UZS")
    exchange_rate = Column(Float, default=1)
    paid_original = Column(Float, default=0)
    change_original = Column(Float, default=0)
    payment_method = Column(String, default="naqd")
    is_finalized = Column(Integer, default=0)
    finalized_at = Column(String)
    cashier_reward = Column(Float, default=0.0)
    # The amounts as first written. A return used to overwrite total, discount
    # and paid in place, so the sale forgot what it had been -- and the same
    # return applied twice could not be told apart from two real ones. These
    # are sealed at creation; everything above is recomputed from them and the
    # sale_returns rows.
    original_total = Column(Float)
    original_discount = Column(Float)
    original_paid = Column(Float)
    original_cashier_reward = Column(Float)
    created_at = Column(String, server_default=text("CURRENT_TIMESTAMP"))


class SaleItem(Base):
    __tablename__ = "sale_items"
    id = Column(String(ROW_ID_LENGTH), primary_key=True, default=new_row_id)
    sale_id = Column(String(ROW_ID_LENGTH), ForeignKey("sales.id"))
    product_id = Column(String(ROW_ID_LENGTH), ForeignKey("products.id"))
    quantity = Column(Integer, nullable=False)
    returned_quantity = Column(Integer, default=0)
    returned_at = Column(String)
    price = Column(Float, nullable=False)
    subtotal = Column(Float, nullable=False)
    # Sealed when the line is sold. Profit used to read the product's current
    # cost, so editing a cost rewrote the profit of every past month.
    cost_at_sale = Column(Float)
    created_at = Column(String, server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(String)


class SaleReturn(Base):
    """One return, as a row.

    A return used to be a counter on the sale line, which meant the same
    return could be applied twice without a trace. As a row it carries its own
    identifier, so replaying it -- from a sync, from a double click -- lands on
    the same row and changes nothing.
    """

    __tablename__ = "sale_returns"
    id = Column(String(ROW_ID_LENGTH), primary_key=True, default=new_row_id)
    sale_id = Column(String(ROW_ID_LENGTH), ForeignKey("sales.id"), nullable=False)
    # Nullable on purpose: deleting a sale line keeps the returns that were
    # already made against it, so the sale's amounts stay explainable.
    sale_item_id = Column(String(ROW_ID_LENGTH), ForeignKey("sale_items.id"))
    quantity = Column(Integer, nullable=False)
    refund = Column(Float, nullable=False, default=0)
    discount_refund = Column(Float, default=0)
    reward_refund = Column(Float, default=0)
    note = Column(String)
    created_at = Column(String, server_default=text("CURRENT_TIMESTAMP"))


class CustomerDebtMovement(Base):
    """What a customer owes, as a ledger rather than a running total.

    ``customers.balance`` was only ever added to and subtracted from, and
    nothing could check it. Suppliers and debtors already keep a ledger; this
    gives customers the same, and the balance becomes a cache of it.
    """

    __tablename__ = "customer_debt_movements"
    id = Column(String(ROW_ID_LENGTH), primary_key=True, default=new_row_id)
    customer_id = Column(String(ROW_ID_LENGTH), ForeignKey("customers.id"), nullable=False)
    sale_id = Column(String(ROW_ID_LENGTH), ForeignKey("sales.id"))
    type = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    note = Column(String)
    created_at = Column(String, server_default=text("CURRENT_TIMESTAMP"))


class StockMovement(Base):
    __tablename__ = "stock_movements"
    id = Column(String(ROW_ID_LENGTH), primary_key=True, default=new_row_id)
    product_id = Column(String(ROW_ID_LENGTH), ForeignKey("products.id"))
    type = Column(String, nullable=False)
    quantity = Column(Integer, nullable=False)
    note = Column(String)
    created_at = Column(String, server_default=text("CURRENT_TIMESTAMP"))


class InventoryCheckSession(Base):
    __tablename__ = "inventory_check_sessions"
    id = Column(String(ROW_ID_LENGTH), primary_key=True, default=new_row_id)
    started_by = Column(String(ROW_ID_LENGTH), ForeignKey("users.id"))
    status = Column(String, nullable=False, default="active")
    started_at = Column(String, server_default=text("CURRENT_TIMESTAMP"))
    finished_at = Column(String)


class InventoryCheckItem(Base):
    __tablename__ = "inventory_check_items"
    id = Column(String(ROW_ID_LENGTH), primary_key=True, default=new_row_id)
    session_id = Column(String(ROW_ID_LENGTH), ForeignKey("inventory_check_sessions.id", ondelete="CASCADE"), nullable=False)
    product_id = Column(String(ROW_ID_LENGTH), ForeignKey("products.id"))
    product_name = Column(String, nullable=False)
    barcode = Column(String)
    expected_stock = Column(Integer, default=0)
    checked_quantity = Column(Integer, default=0)
    checked_at = Column(String)
    __table_args__ = (UniqueConstraint("session_id", "product_id"),)


class FinanceManualMovement(Base):
    __tablename__ = "finance_manual_movements"
    id = Column(String(ROW_ID_LENGTH), primary_key=True, default=new_row_id)
    movement_date = Column(String, nullable=False)
    kind = Column(String, nullable=False)
    operation = Column(String, nullable=False, default="+")
    amount = Column(Float, nullable=False)
    currency_code = Column(String, default="UZS")
    rate_to_uzs = Column(Float, default=1)
    created_at = Column(String, server_default=text("CURRENT_TIMESTAMP"))


class ActivityLog(Base):
    """What happened, who did it and where.

    The table has existed since an early migration and was never written to:
    activity only lived in a Python list that emptied on every restart, which
    is why one device could never tell another what its cashier had just done.

    ``user_name`` is denormalised on purpose -- the entry has to stay readable
    after the person is removed -- and ``device_key`` is what keeps a device
    from announcing its own work back to itself.
    """

    __tablename__ = "activity_logs"
    id = Column(String(ROW_ID_LENGTH), primary_key=True, default=new_row_id)
    user_id = Column(String(ROW_ID_LENGTH), ForeignKey("users.id", ondelete="SET NULL"))
    user_name = Column(String)
    device_key = Column(String)
    action = Column(String, nullable=False)
    title = Column(String, nullable=False)
    message = Column(String, nullable=False)
    level = Column(String, default="info")
    target = Column(String, default="products")
    badge = Column(String, default="Mahsulot")
    created_at = Column(String, server_default=text("CURRENT_TIMESTAMP"))


class NotificationRead(Base):
    __tablename__ = "notification_reads"
    id = Column(String(ROW_ID_LENGTH), primary_key=True, default=new_row_id)
    user_id = Column(String(ROW_ID_LENGTH), ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    notification_id = Column(String, index=True, nullable=False)
    read_at = Column(String, nullable=False)


MIGRATIONS = (
    ("001_create_missing_tables", "Create any missing tables from the current SQLAlchemy models."),
    ("002_add_missing_columns", "Add columns introduced after earlier releases."),
    ("003_create_sync_state", "Create local sync state table."),
    ("004_create_sync_tombstones", "Track deleted rows that must be synchronized."),
    ("005_create_activity_logs", "Create activity_logs table for tracking product and sales actions."),
    ("006_create_notification_reads", "Create notification_reads table for tracking read notifications per user."),
    ("007_add_sale_finalization_columns", "Add is_finalized and finalized_at to sales table."),
    ("008_add_product_creator", "Track which user created each product."),
    ("009_add_expense_cashier", "Link salary deductions to the selected cashier."),
    ("010_add_sale_item_returned_at", "Track when sold items are returned."),
    ("011_create_account_assets", "Store account-specific synchronized branding assets."),
    ("012_uuid_row_identity", "Give every row a UUID so two devices can never claim the same id."),
    ("013_money_ledger", "Derive every money figure from rows that are never rewritten."),
    ("014_activity_feed", "Record what each device does so the others can be told."),
)


def _database_has_user_tables(conn):
    rows = conn.exec_driver_sql(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return any(row[0] != "schema_migrations" for row in rows)


def _backup_database_before_migration(conn):
    if not DB_PATH or DB_PATH == ":memory:" or not os.path.exists(DB_PATH) or os.path.getsize(DB_PATH) == 0:
        return None
    if not _database_has_user_tables(conn):
        return None
    try:
        conn.exec_driver_sql("PRAGMA wal_checkpoint(FULL)")
    except OperationalError:
        pass
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{DB_PATH}.backup_{timestamp}"
    shutil.copy2(DB_PATH, backup_path)
    return backup_path


def _ensure_schema_migrations_table(conn):
    conn.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            description TEXT,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)


def _ensure_sync_state_table(conn):
    conn.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS sync_state (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)


def get_applied_migrations():
    engine = _get_engine()
    with engine.begin() as conn:
        _ensure_schema_migrations_table(conn)
        rows = conn.exec_driver_sql("SELECT version, description, applied_at FROM schema_migrations ORDER BY version").fetchall()
    return [Row(version=row[0], description=row[1], applied_at=row[2]) for row in rows]


def _mark_migration_applied(conn, version, description):
    conn.exec_driver_sql(
        "INSERT OR IGNORE INTO schema_migrations(version, description, applied_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
        (version, description),
    )


def _migration_create_missing_tables(conn):
    Base.metadata.create_all(bind=conn)


def _migration_create_sync_state(conn):
    _ensure_sync_state_table(conn)


def _migration_create_sync_tombstones(conn):
    conn.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS sync_tombstones (
            table_name TEXT NOT NULL,
            local_id TEXT NOT NULL,
            deleted_at TEXT NOT NULL,
            PRIMARY KEY (table_name, local_id)
        )
    """)


def _table_columns(conn, table_name):
    return {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info('{table_name}')").fetchall()}


def _has_table(conn, table_name):
    row = conn.exec_driver_sql(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def _add_missing_columns(conn=None):
    engine = _get_engine()
    migrations = {
        "users": {
            "email": "ALTER TABLE users ADD COLUMN email TEXT",
            "created_at": "ALTER TABLE users ADD COLUMN created_at TEXT",
        },
        "currencies": {
            "updated_at": "ALTER TABLE currencies ADD COLUMN updated_at TEXT",
        },
        "products": {
            "section_id": "ALTER TABLE products ADD COLUMN section_id TEXT REFERENCES product_sections(id)",
            "original_barcode": "ALTER TABLE products ADD COLUMN original_barcode TEXT",
            "template_id": "ALTER TABLE products ADD COLUMN template_id TEXT REFERENCES product_templates(id)",
            "supplier_id": "ALTER TABLE products ADD COLUMN supplier_id TEXT REFERENCES suppliers(id)",
            "created_by_user_id": "ALTER TABLE products ADD COLUMN created_by_user_id TEXT REFERENCES users(id) ON DELETE SET NULL",
            "price_currency": "ALTER TABLE products ADD COLUMN price_currency TEXT DEFAULT 'UZS'",
            "price_exchange_rate": "ALTER TABLE products ADD COLUMN price_exchange_rate REAL DEFAULT 1",
            "price_original": "ALTER TABLE products ADD COLUMN price_original REAL DEFAULT 0",
            "cost_currency": "ALTER TABLE products ADD COLUMN cost_currency TEXT DEFAULT 'UZS'",
            "cost_exchange_rate": "ALTER TABLE products ADD COLUMN cost_exchange_rate REAL DEFAULT 1",
            "cost_original": "ALTER TABLE products ADD COLUMN cost_original REAL DEFAULT 0",
            "process_status": "ALTER TABLE products ADD COLUMN process_status TEXT DEFAULT 'available'",
            "process_quantity": "ALTER TABLE products ADD COLUMN process_quantity INTEGER DEFAULT 0",
            "process_deposit": "ALTER TABLE products ADD COLUMN process_deposit REAL DEFAULT 0",
            "process_deposit_currency": "ALTER TABLE products ADD COLUMN process_deposit_currency TEXT DEFAULT 'UZS'",
            "process_customer_name": "ALTER TABLE products ADD COLUMN process_customer_name TEXT",
            "process_customer_phone": "ALTER TABLE products ADD COLUMN process_customer_phone TEXT",
            "process_cashier_name": "ALTER TABLE products ADD COLUMN process_cashier_name TEXT",
            "is_deleted": "ALTER TABLE products ADD COLUMN is_deleted INTEGER DEFAULT 0",
            "deleted_at": "ALTER TABLE products ADD COLUMN deleted_at TEXT",
            "purge_after": "ALTER TABLE products ADD COLUMN purge_after TEXT",
            "created_at": "ALTER TABLE products ADD COLUMN created_at TEXT",
        },
        "product_templates": {
            "section_id": "ALTER TABLE product_templates ADD COLUMN section_id TEXT REFERENCES product_sections(id)",
            "original_name": "ALTER TABLE product_templates ADD COLUMN original_name TEXT",
            "is_deleted": "ALTER TABLE product_templates ADD COLUMN is_deleted INTEGER DEFAULT 0",
            "deleted_at": "ALTER TABLE product_templates ADD COLUMN deleted_at TEXT",
            "purge_after": "ALTER TABLE product_templates ADD COLUMN purge_after TEXT",
            "created_at": "ALTER TABLE product_templates ADD COLUMN created_at TEXT",
        },
        "product_sections": {
            "original_name": "ALTER TABLE product_sections ADD COLUMN original_name TEXT",
            "is_deleted": "ALTER TABLE product_sections ADD COLUMN is_deleted INTEGER DEFAULT 0",
            "deleted_at": "ALTER TABLE product_sections ADD COLUMN deleted_at TEXT",
            "purge_after": "ALTER TABLE product_sections ADD COLUMN purge_after TEXT",
            "created_at": "ALTER TABLE product_sections ADD COLUMN created_at TEXT",
        },
        "product_template_fields": {
            "field_type": "ALTER TABLE product_template_fields ADD COLUMN field_type TEXT DEFAULT 'text'",
            "required": "ALTER TABLE product_template_fields ADD COLUMN required INTEGER DEFAULT 0",
            "sort_order": "ALTER TABLE product_template_fields ADD COLUMN sort_order INTEGER DEFAULT 0",
        },
        "customers": {
            "email": "ALTER TABLE customers ADD COLUMN email TEXT",
            "balance": "ALTER TABLE customers ADD COLUMN balance REAL DEFAULT 0",
            "total_purchases": "ALTER TABLE customers ADD COLUMN total_purchases REAL DEFAULT 0",
            "created_at": "ALTER TABLE customers ADD COLUMN created_at TEXT",
        },
        "suppliers": {
            "phone": "ALTER TABLE suppliers ADD COLUMN phone TEXT",
            "note": "ALTER TABLE suppliers ADD COLUMN note TEXT",
            "debt_currency": "ALTER TABLE suppliers ADD COLUMN debt_currency TEXT DEFAULT 'UZS'",
            "balance": "ALTER TABLE suppliers ADD COLUMN balance REAL DEFAULT 0",
            "total_received": "ALTER TABLE suppliers ADD COLUMN total_received REAL DEFAULT 0",
            "created_at": "ALTER TABLE suppliers ADD COLUMN created_at TEXT",
        },
        "supplier_debt_movements": {
            "note": "ALTER TABLE supplier_debt_movements ADD COLUMN note TEXT",
            "created_at": "ALTER TABLE supplier_debt_movements ADD COLUMN created_at TEXT",
        },
        "debtors": {
            "user_id": "ALTER TABLE debtors ADD COLUMN user_id TEXT REFERENCES users(id)",
            "phone": "ALTER TABLE debtors ADD COLUMN phone TEXT",
            "note": "ALTER TABLE debtors ADD COLUMN note TEXT",
            "debt_currency": "ALTER TABLE debtors ADD COLUMN debt_currency TEXT DEFAULT 'UZS'",
            "balance": "ALTER TABLE debtors ADD COLUMN balance REAL DEFAULT 0",
            "total_given": "ALTER TABLE debtors ADD COLUMN total_given REAL DEFAULT 0",
            "created_at": "ALTER TABLE debtors ADD COLUMN created_at TEXT",
        },
        "debtor_debt_movements": {
            "note": "ALTER TABLE debtor_debt_movements ADD COLUMN note TEXT",
            "created_at": "ALTER TABLE debtor_debt_movements ADD COLUMN created_at TEXT",
        },
        "sales": {
            "customer_name": "ALTER TABLE sales ADD COLUMN customer_name TEXT",
            "customer_phone": "ALTER TABLE sales ADD COLUMN customer_phone TEXT",
            "currency_code": "ALTER TABLE sales ADD COLUMN currency_code TEXT DEFAULT 'UZS'",
            "exchange_rate": "ALTER TABLE sales ADD COLUMN exchange_rate REAL DEFAULT 1",
            "paid_original": "ALTER TABLE sales ADD COLUMN paid_original REAL DEFAULT 0",
            "change_original": "ALTER TABLE sales ADD COLUMN change_original REAL DEFAULT 0",
            "payment_method": "ALTER TABLE sales ADD COLUMN payment_method TEXT DEFAULT 'naqd'",
            "is_finalized": "ALTER TABLE sales ADD COLUMN is_finalized INTEGER DEFAULT 0",
            "finalized_at": "ALTER TABLE sales ADD COLUMN finalized_at TEXT",
            "cashier_reward": "ALTER TABLE sales ADD COLUMN cashier_reward REAL DEFAULT 0.0",
            "created_at": "ALTER TABLE sales ADD COLUMN created_at TEXT",
        },
        "sale_items": {
            "returned_quantity": "ALTER TABLE sale_items ADD COLUMN returned_quantity INTEGER DEFAULT 0",
            "returned_at": "ALTER TABLE sale_items ADD COLUMN returned_at TEXT",
        },
        "stock_movements": {
            "note": "ALTER TABLE stock_movements ADD COLUMN note TEXT",
            "created_at": "ALTER TABLE stock_movements ADD COLUMN created_at TEXT",
        },
        "inventory_check_sessions": {
            "started_by": "ALTER TABLE inventory_check_sessions ADD COLUMN started_by TEXT REFERENCES users(id)",
            "status": "ALTER TABLE inventory_check_sessions ADD COLUMN status TEXT DEFAULT 'active'",
            "started_at": "ALTER TABLE inventory_check_sessions ADD COLUMN started_at TEXT",
            "finished_at": "ALTER TABLE inventory_check_sessions ADD COLUMN finished_at TEXT",
        },
        "inventory_check_items": {
            "checked_quantity": "ALTER TABLE inventory_check_items ADD COLUMN checked_quantity INTEGER DEFAULT 0",
            "checked_at": "ALTER TABLE inventory_check_items ADD COLUMN checked_at TEXT",
        },
        "expenses": {
            "user_id": "ALTER TABLE expenses ADD COLUMN user_id TEXT REFERENCES users(id)",
            "cashier_id": "ALTER TABLE expenses ADD COLUMN cashier_id TEXT REFERENCES users(id) ON DELETE SET NULL",
            "currency_code": "ALTER TABLE expenses ADD COLUMN currency_code TEXT DEFAULT 'UZS'",
            "description": "ALTER TABLE expenses ADD COLUMN description TEXT",
            "created_at": "ALTER TABLE expenses ADD COLUMN created_at TEXT",
        },
        "finance_manual_movements": {
            "currency_code": "ALTER TABLE finance_manual_movements ADD COLUMN currency_code TEXT DEFAULT 'UZS'",
            "rate_to_uzs": "ALTER TABLE finance_manual_movements ADD COLUMN rate_to_uzs REAL DEFAULT 1",
            "created_at": "ALTER TABLE finance_manual_movements ADD COLUMN created_at TEXT",
        },
    }
    owns_connection = conn is None
    if owns_connection:
        with engine.begin() as owned_conn:
            _add_missing_columns(owned_conn)
        return

    for table_name, table_migrations in migrations.items():
        if not _has_table(conn, table_name):
            continue
        columns = _table_columns(conn, table_name)
        for column, sql in table_migrations.items():
            if column not in columns:
                conn.exec_driver_sql(sql)
                columns.add(column)
    if _has_table(conn, "product_templates"):
        index_rows = conn.exec_driver_sql("PRAGMA index_list('product_templates')").fetchall()
        has_global_name_unique = False
        for index_row in index_rows:
            index_name = index_row[1]
            is_unique = bool(index_row[2])
            if not is_unique:
                continue
            columns = [row[2] for row in conn.exec_driver_sql(f"PRAGMA index_info('{index_name}')").fetchall()]
            if columns == ["name"]:
                has_global_name_unique = True
                break
        if has_global_name_unique:
            conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
            conn.exec_driver_sql("""
                CREATE TABLE product_templates_new (
                    id INTEGER PRIMARY KEY,
                    section_id INTEGER REFERENCES product_sections(id) ON DELETE CASCADE,
                    name VARCHAR NOT NULL,
                    original_name VARCHAR,
                    is_deleted INTEGER DEFAULT 0,
                    deleted_at VARCHAR,
                    purge_after VARCHAR,
                    created_at VARCHAR DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(section_id, name)
                )
            """)
            conn.exec_driver_sql("""
                INSERT INTO product_templates_new (
                    id, section_id, name, original_name, is_deleted, deleted_at, purge_after, created_at
                )
                SELECT
                    id,
                    section_id,
                    name,
                    original_name,
                    COALESCE(is_deleted, 0),
                    deleted_at,
                    purge_after,
                    created_at
                FROM product_templates
            """)
            conn.exec_driver_sql("DROP TABLE product_templates")
            conn.exec_driver_sql("ALTER TABLE product_templates_new RENAME TO product_templates")
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")


def _migration_add_missing_columns(conn):
    _add_missing_columns(conn)


def _migration_create_activity_logs(conn):
    Base.metadata.create_all(bind=conn, tables=[ActivityLog.__table__])


def _migration_create_notification_reads(conn):
    Base.metadata.create_all(bind=conn, tables=[NotificationRead.__table__])


def _migration_add_sale_finalization_columns(conn):
    _add_missing_columns(conn)


def _migration_add_product_creator(conn):
    _add_missing_columns(conn)


def _migration_add_expense_cashier(conn):
    _add_missing_columns(conn)


def _migration_add_sale_item_returned_at(conn):
    _add_missing_columns(conn)
    if not (_has_table(conn, "sale_items") and _has_table(conn, "sales")):
        return
    movement_time = "NULL"
    if _has_table(conn, "stock_movements"):
        movement_time = """
            SELECT MAX(stock_movements.created_at)
            FROM stock_movements
            WHERE stock_movements.product_id = sale_items.product_id
              AND stock_movements.type = 'qaytarish'
              AND stock_movements.note LIKE '%#' || sale_items.sale_id || ' %'
        """
    conn.exec_driver_sql(f"""
        UPDATE sale_items
        SET returned_at = COALESCE(
            ({movement_time}),
            (SELECT sales.created_at FROM sales WHERE sales.id = sale_items.sale_id)
        )
        WHERE COALESCE(returned_quantity, 0) > 0
          AND returned_at IS NULL
    """)


def _migration_create_account_assets(conn):
    Base.metadata.create_all(bind=conn, tables=[AccountAsset.__table__])



def _ordered_table_columns(conn, table_name):
    return [row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info('{table_name}')").fetchall()]


def _legacy_primary_key_is_integer(conn, table_name):
    for row in conn.exec_driver_sql(f"PRAGMA table_info('{table_name}')").fetchall():
        if row[1] == "id":
            return str(row[2] or "").upper().startswith("INT")
    return False


def _account_uid_from_state(conn):
    if _has_table(conn, "sync_state"):
        row = conn.exec_driver_sql(
            "SELECT value FROM sync_state WHERE key='account_user_uid'"
        ).fetchone()
        if row and row[0]:
            return str(row[0])
    return _ACTIVE_ACCOUNT_UID


def _record_dangling_references(conn):
    """Note references that point at rows this device does not have.

    These are not created by the migration - they are older damage from two
    devices minting the same integer.  We keep the reference rather than
    clearing it, because the missing row now has a predictable UUID: when the
    device that owns it syncs, the link repairs itself.
    """
    findings = {}
    for table in Base.metadata.sorted_tables:
        if not _has_table(conn, table.name):
            continue
        for column in table.columns:
            for foreign_key in column.foreign_keys:
                parent = foreign_key.column.table.name
                if not _has_table(conn, parent):
                    continue
                count = conn.exec_driver_sql(
                    f"SELECT COUNT(*) FROM {_quote_identifier(table.name)} "
                    f"WHERE {_quote_identifier(column.name)} IS NOT NULL "
                    f"AND {_quote_identifier(column.name)} NOT IN "
                    f"(SELECT {_quote_identifier(foreign_key.column.name)} FROM {_quote_identifier(parent)})"
                ).scalar()
                if count:
                    findings[f"{table.name}.{column.name}"] = int(count)
    _sync_state_set(conn, "identity_dangling_refs", json.dumps(findings, sort_keys=True))
    return findings


def get_dangling_reference_report():
    """What the identity migration found pointing at rows we do not hold."""
    with _get_engine().begin() as conn:
        row = conn.exec_driver_sql(
            "SELECT value FROM sync_state WHERE key='identity_dangling_refs'"
        ).fetchone()
    try:
        return json.loads(row[0]) if row and row[0] else {}
    except (TypeError, ValueError):
        return {}


def _migration_uuid_row_identity(conn):
    """Rebuild the schema on UUID keys without discarding existing data.

    Two devices used to mint the same integer for two different sales, and the
    server -- which keys records by (account, table, local_id) -- kept only one
    of them.  A UUID cannot collide, so nothing can be silently lost again.

    Existing primary keys are translated deterministically and every foreign
    key follows the same map. The account owner receives ``owner_row_id`` so
    every device agrees on that identity. This keeps products, sales, cashiers,
    settings and history intact while moving the database to the new schema.

    The server still holds the old integer-keyed records, so the account is
    flagged for a full server reset on the next sync; without that, the next
    pull would put the obsolete integer rows straight back.
    """
    Base.metadata.create_all(bind=conn)
    if not _has_table(conn, "users") or not _legacy_primary_key_is_integer(conn, "users"):
        return

    account_uid = _account_uid_from_state(conn)
    owner_uuid = owner_row_id(account_uid)
    owner_legacy_id = None
    if owner_uuid and _has_table(conn, "user_settings"):
        row = conn.exec_driver_sql(
            "SELECT user_id FROM user_settings WHERE key='api_user_uid' AND value=?",
            (account_uid,),
        ).fetchone()
        if row and row[0] is not None:
            owner_legacy_id = str(row[0])

    tables = [table for table in Base.metadata.sorted_tables if _has_table(conn, table.name)]
    snapshots = {}
    for table in tables:
        columns = _ordered_table_columns(conn, table.name)
        if not columns:
            snapshots[table.name] = []
            continue
        quoted_columns = ", ".join(_quote_identifier(column) for column in columns)
        snapshots[table.name] = [
            dict(row)
            for row in conn.exec_driver_sql(
                f"SELECT {quoted_columns} FROM {_quote_identifier(table.name)}"
            ).mappings().all()
        ]

    id_maps = {}
    for table in tables:
        if "id" not in {column.name for column in table.columns}:
            continue
        mapping = {}
        for row in snapshots.get(table.name, []):
            old_id = row.get("id")
            if old_id is None:
                continue
            old_key = str(old_id)
            if is_row_uuid(old_id):
                mapped = old_key
            elif table.name == "users" and owner_uuid and old_key == owner_legacy_id:
                mapped = owner_uuid
            else:
                mapped = stable_row_id(table.name, old_key)
            mapping[old_key] = mapped
            row["id"] = mapped
        id_maps[table.name] = mapping

    # Rewrite every declared foreign key through its parent's id map. Composite
    # and string-keyed tables (settings/assets) keep their natural keys.
    for table in tables:
        for row in snapshots.get(table.name, []):
            for column in table.columns:
                value = row.get(column.name)
                if value is None:
                    continue
                for foreign_key in column.foreign_keys:
                    parent_table = foreign_key.column.table.name
                    mapped = id_maps.get(parent_table, {}).get(str(value))
                    if mapped:
                        row[column.name] = mapped
                        break

            if table.name == "sync_tombstones":
                target_table = str(row.get("table_name") or "")
                mapped = id_maps.get(target_table, {}).get(str(row.get("local_id")))
                if mapped:
                    row["local_id"] = mapped

    try:
        for table in reversed(tables):
            conn.exec_driver_sql(f"DROP TABLE {_quote_identifier(table.name)}")
        Base.metadata.create_all(bind=conn)

        for table in tables:
            current_columns = {column.name for column in table.columns}
            for source_row in snapshots.get(table.name, []):
                row = {key: value for key, value in source_row.items() if key in current_columns}
                if not row:
                    continue
                columns = list(row)
                placeholders = ", ".join("?" for _ in columns)
                conn.exec_driver_sql(
                    f"INSERT INTO {_quote_identifier(table.name)} "
                    f"({', '.join(_quote_identifier(column) for column in columns)}) "
                    f"VALUES ({placeholders})",
                    tuple(row[column] for column in columns),
                )

        if _has_table(conn, "sync_outbox"):
            conn.exec_driver_sql("DELETE FROM sync_outbox")

        # Local identifiers no longer match the server's legacy integers, so
        # the first upgraded device replaces the server copy with this intact
        # UUID snapshot.
        _sync_state_set(conn, "identity_reset_required", "1")
        # Settle with the server before trusting anything here: rows deleted on
        # the other devices may still be sitting in this copy.
        _sync_state_set(conn, "upgrade_reconcile_required", "1")
        _sync_state_set(conn, "server_reseed_required", "1")
        _sync_state_set(conn, "pending_change_count", "0")
        _sync_state_set(conn, "remote_generation", "0")
        _sync_state_set(conn, "remote_tables", "")
        _sync_state_set(conn, "last_dirty_at", "")
    finally:
        _record_dangling_references(conn)


def _migration_money_ledger(conn):
    """Make every money figure derivable from rows that are never rewritten.

    Three things were being kept as running totals with nothing to check them
    against: the sale's own amounts (a return overwrote them), the returned
    quantity (a counter), and the customer's debt balance. Each now has an
    immutable source -- the sealed original, the sale_returns rows, the
    customer_debt_movements rows -- and the old column becomes a cache.

    Existing rows are sealed at their current value. Anything already returned
    before this point cannot be reconstructed, because the old code discarded
    what it overwrote; from here on it can.
    """
    Base.metadata.create_all(bind=conn)

    additions = {
        "sales": {
            "original_total": "ALTER TABLE sales ADD COLUMN original_total REAL",
            "original_discount": "ALTER TABLE sales ADD COLUMN original_discount REAL",
            "original_paid": "ALTER TABLE sales ADD COLUMN original_paid REAL",
            "original_cashier_reward": "ALTER TABLE sales ADD COLUMN original_cashier_reward REAL",
        },
        "sale_items": {
            "cost_at_sale": "ALTER TABLE sale_items ADD COLUMN cost_at_sale REAL",
            "created_at": "ALTER TABLE sale_items ADD COLUMN created_at VARCHAR",
            "updated_at": "ALTER TABLE sale_items ADD COLUMN updated_at VARCHAR",
        },
    }
    for table_name, columns in additions.items():
        if not _has_table(conn, table_name):
            continue
        existing = _table_columns(conn, table_name)
        for column, statement in columns.items():
            if column not in existing:
                conn.exec_driver_sql(statement)

    if _has_table(conn, "sales"):
        conn.exec_driver_sql("""
            UPDATE sales SET
                original_total = COALESCE(original_total, total),
                original_discount = COALESCE(original_discount, discount, 0),
                original_paid = COALESCE(original_paid, paid),
                original_cashier_reward = COALESCE(original_cashier_reward, cashier_reward, 0)
        """)
    if _has_table(conn, "sale_items") and _has_table(conn, "products"):
        conn.exec_driver_sql("""
            UPDATE sale_items SET cost_at_sale = COALESCE(
                cost_at_sale,
                (SELECT products.cost FROM products WHERE products.id = sale_items.product_id),
                0
            )
        """)
        conn.exec_driver_sql("""
            UPDATE sale_items SET created_at = COALESCE(
                created_at,
                (SELECT sales.created_at FROM sales WHERE sales.id = sale_items.sale_id)
            )
        """)
    # Sales that existed before this point carry an inherited figure, not a
    # derived one: a return made under the old code overwrote what it reduced,
    # and nothing can bring that back. Mark where the ledger actually begins so
    # a figure from before it is never mistaken for a checked one.
    if _has_table(conn, "sales"):
        inherited = conn.exec_driver_sql("SELECT COUNT(*) FROM sales").scalar() or 0
        if inherited:
            _sync_state_set(conn, "ledger_baseline_at", _utc_now())
            _sync_state_set(conn, "ledger_inherited_sales", str(int(inherited)))

    # A customer who already owes something keeps owing it: the balance becomes
    # the ledger's opening entry rather than a number with no history.
    if _has_table(conn, "customers") and _has_table(conn, "customer_debt_movements"):
        owing = conn.exec_driver_sql(
            "SELECT id, balance FROM customers WHERE COALESCE(balance, 0) <> 0"
        ).fetchall()
        now = _utc_now()
        for customer_id, balance in owing:
            already = conn.exec_driver_sql(
                "SELECT COUNT(*) FROM customer_debt_movements WHERE customer_id = ?",
                (customer_id,),
            ).scalar()
            if already:
                continue
            conn.exec_driver_sql(
                "INSERT INTO customer_debt_movements (id, customer_id, sale_id, type, amount, note, created_at) "
                "VALUES (?, ?, NULL, 'boshlangich', ?, ?, ?)",
                (
                    stable_row_id("customer_debt_movements", f"opening:{customer_id}"),
                    customer_id,
                    float(balance or 0),
                    "Jurnal boshlanishidagi qoldiq",
                    now,
                ),
            )


def _migration_activity_feed(conn):
    """Give the activity log a writer, an author and a device.

    The table was created years ago and nothing ever inserted into it, so there
    is nothing to backfill -- only the three columns the entries need before
    they can travel between devices.
    """
    Base.metadata.create_all(bind=conn)
    if not _has_table(conn, "activity_logs"):
        return
    existing = _table_columns(conn, "activity_logs")
    for column, statement in (
        ("user_id", "ALTER TABLE activity_logs ADD COLUMN user_id TEXT REFERENCES users(id) ON DELETE SET NULL"),
        ("user_name", "ALTER TABLE activity_logs ADD COLUMN user_name VARCHAR"),
        ("device_key", "ALTER TABLE activity_logs ADD COLUMN device_key VARCHAR"),
    ):
        if column not in existing:
            conn.exec_driver_sql(statement)

    # An old build did leave a few entries behind. They were made here, so they
    # are stamped with this device -- otherwise they would read as another
    # device's work and be announced as news.
    row = conn.exec_driver_sql(
        "SELECT value FROM sync_state WHERE key='device_key'"
    ).fetchone()
    if row and row[0]:
        conn.exec_driver_sql(
            "UPDATE activity_logs SET device_key = ? "
            "WHERE device_key IS NULL OR device_key = ''",
            (row[0],),
        )
    # And whatever predates this migration is history, not news: nobody wants a
    # month of toasts the first time they open the new build.
    _sync_state_set(conn, "activity_seen_at", _utc_now())


MIGRATION_FUNCTIONS = {
    "001_create_missing_tables": _migration_create_missing_tables,
    "002_add_missing_columns": _migration_add_missing_columns,
    "003_create_sync_state": _migration_create_sync_state,
    "004_create_sync_tombstones": _migration_create_sync_tombstones,
    "005_create_activity_logs": _migration_create_activity_logs,
    "006_create_notification_reads": _migration_create_notification_reads,
    "007_add_sale_finalization_columns": _migration_add_sale_finalization_columns,
    "008_add_product_creator": _migration_add_product_creator,
    "009_add_expense_cashier": _migration_add_expense_cashier,
    "010_add_sale_item_returned_at": _migration_add_sale_item_returned_at,
    "011_create_account_assets": _migration_create_account_assets,
    "012_uuid_row_identity": _migration_uuid_row_identity,
    "013_money_ledger": _migration_money_ledger,
    "014_activity_feed": _migration_activity_feed,
}


def run_migrations():
    """Bring the database up to date, with referential checks stood down.

    A migration that rebuilds a table has to drop it and refill it, and for the
    moment in between every constraint pointing at that table is unsatisfied.
    SQLite ignores ``PRAGMA foreign_keys`` once a transaction is open, so it has
    to be the very first statement on this connection - before anything else
    makes SQLite emit a BEGIN.  Checks are handed back by disposing the pool,
    which forces the next connection through the engine's connect hook.
    """
    engine = _get_engine()
    applied_now = []
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        _ensure_schema_migrations_table(conn)
        applied = {row[0] for row in conn.exec_driver_sql("SELECT version FROM schema_migrations").fetchall()}
        pending = [(version, description) for version, description in MIGRATIONS if version not in applied]
        if pending:
            _backup_database_before_migration(conn)
            for version, description in pending:
                MIGRATION_FUNCTIONS[version](conn)
                _mark_migration_applied(conn, version, description)
                applied_now.append(version)
    engine.dispose()
    return applied_now


def _default_product_section_id(session):
    section = session.scalar(
        select(ProductSection)
        .where(func.coalesce(ProductSection.is_deleted, 0) == 0)
        .order_by(func.coalesce(ProductSection.created_at, ""), ProductSection.id)
    )
    if section is None:
        section = ProductSection(id=stable_row_id("product_sections", "Umumiy"), name="Umumiy")
        session.add(section)
        session.flush()
    return section.id


# Every synced column that points at a user. Missing one here means a merged
# account leaves rows attributed to a user id that no longer exists - which is
# how cashier salary and cashier-charged expenses silently lose their owner.
_USER_REFERENCE_COLUMNS = (
    (Sale, "cashier_id"),
    (Expense, "user_id"),
    (Expense, "cashier_id"),
    (Product, "created_by_user_id"),
    (Debtor, "user_id"),
    (InventoryCheckSession, "started_by"),
)


def _reassign_user_references(session, source_user_id, target_user_id):
    if source_user_id == target_user_id:
        return
    # Deliberately ORM loops, not bulk UPDATEs: a bulk statement never reaches
    # the flush listeners, so the rewritten rows would stay in the local file
    # and never be pushed. Other devices would keep the old attribution.
    for model, column_name in _USER_REFERENCE_COLUMNS:
        column = getattr(model, column_name)
        for row in session.scalars(select(model).where(column == source_user_id)):
            setattr(row, column_name, target_user_id)

    # login_logs is device-local history, not synced - a bulk update is fine.
    session.execute(update(LoginLog).where(LoginLog.user_id == source_user_id).values(user_id=target_user_id))
    session.query(UserSetting).filter(UserSetting.user_id == source_user_id).delete(synchronize_session=False)


def _rekey_user_identity(session, user, new_id):
    """Move a user row onto a new identifier without losing anything.

    The account owner must carry the same identifier on every device, because
    that is what ``sales.cashier_id`` and ``expenses.cashier_id`` point at; an
    owner row created before that rule existed is moved here.  The replacement
    is inserted under a placeholder name first: username and email are unique
    and both rows exist for the length of the swap.  Settings are copied by
    hand because reassigning references deliberately drops the old ones, and
    one of them is the API token that keeps this device signed in.
    """
    old_id = user.id
    if not new_id or old_id == new_id or session.get(User, new_id) is not None:
        return user
    settings = [
        (setting.key, setting.value)
        for setting in session.scalars(select(UserSetting).where(UserSetting.user_id == old_id))
    ]
    placeholder = f"__rekey_{new_id}"
    replacement = User(
        id=new_id,
        username=placeholder,
        email=f"{placeholder}@local",
        password=user.password,
        role=user.role,
        created_at=user.created_at,
    )
    session.add(replacement)
    session.flush()
    _reassign_user_references(session, old_id, new_id)
    session.flush()
    username, email = user.username, user.email
    session.delete(user)
    session.flush()
    replacement.username = username
    replacement.email = email
    for key, value in settings:
        session.add(UserSetting(user_id=new_id, key=key, value=value))
    session.flush()
    return replacement


def init_db(account_owner=None, seed_defaults=True):
    clear_session_notifications()
    engine = _get_engine()
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("PRAGMA journal_mode = WAL")
    except OperationalError:
        pass
    run_migrations()
    _add_missing_columns()
    Base.metadata.create_all(bind=engine)
    if account_owner:
        _bind_account_identity(
            account_owner.get("user_uid") or account_owner.get("uid"),
            account_owner.get("email"),
        )

    # Seeding an account's own rows must not look like a user edit.
    _sync_suspend_token = suspend_sync() if account_owner else nullcontext()
    _sync_suspend_token.__enter__()
    try:
        with session_scope() as session:
            if account_owner:
                owner_email = _normalize_email(account_owner.get("email"))
                if not owner_email:
                    raise AppError("Online account emaili topilmadi.")
                owner = session.scalar(select(User).where(func.lower(User.email) == owner_email))
                legacy_admin = session.scalar(
                    select(User).where(
                        User.username == "admin",
                        or_(User.email.is_(None), User.email == "", func.lower(User.email) == "admin@gmail.com"),
                    )
                )
                if owner is None and legacy_admin is not None:
                    owner = legacy_admin
                    owner.email = owner_email
                    owner.username = (account_owner.get("display_name") or owner_email).strip()
                elif owner is not None and legacy_admin is not None and owner.id != legacy_admin.id:
                    _reassign_user_references(session, legacy_admin.id, owner.id)
                    session.delete(legacy_admin)
                stable_owner_id = owner_row_id(_ACTIVE_ACCOUNT_UID)
                if owner is None:
                    owner = User(
                        id=stable_owner_id or new_row_id(),
                        username=(account_owner.get("display_name") or owner_email).strip(),
                        email=owner_email,
                        password=_hash_password(secrets.token_urlsafe(32)),
                        role="admin",
                    )
                    session.add(owner)
                    session.flush()
                elif stable_owner_id:
                    # Same person, different row on each device: that is what
                    # made cashier salary disagree. Name the row after the
                    # account so every device points at the same one.
                    owner = _rekey_user_identity(session, owner, stable_owner_id)
                owner.role = "admin"
                if not str(owner.password).startswith("pbkdf2_sha256$"):
                    owner.password = _hash_password(owner.password)
            else:
                admin = session.scalar(select(User).where(User.username == "admin"))
                if admin is None:
                    session.add(User(username="admin", email="admin@gmail.com", password=_hash_password("admin123"), role="admin"))
                elif not str(admin.password).startswith("pbkdf2_sha256$"):
                    admin.password = _hash_password(admin.password)
                if admin and not admin.email:
                    admin.email = "admin@gmail.com"

            used_emails = {
                str(email).lower()
                for email in session.scalars(select(User.email).where(User.email.is_not(None)))
                if email
            }
            for user in session.scalars(select(User).where(or_(User.email.is_(None), User.email == ""))).all():
                base_email = _email_from_username(user.username)
                candidate = base_email
                counter = 2
                while candidate in used_emails:
                    local, domain = base_email.split("@", 1)
                    candidate = f"{local}.{counter}@{domain}"
                    counter += 1
                user.email = candidate
                used_emails.add(candidate)

            if not session.get(AppSetting, "app_name"):
                session.add(AppSetting(key="app_name", value="Market POS"))

            if seed_defaults:
                for name in ["Oziq-ovqat", "Ichimliklar", "Gigiena", "Uy-ro'zg'or"]:
                    if not session.scalar(select(Category).where(Category.name == name)):
                        session.add(Category(id=stable_row_id("categories", name), name=name))

            has_orphan_products = session.scalar(select(func.count(Product.id)).where(Product.section_id.is_(None))) > 0
            has_orphan_templates = session.scalar(
                select(func.count(ProductTemplate.id)).where(ProductTemplate.section_id.is_(None))
            ) > 0
            default_section_id = None
            if seed_defaults or has_orphan_products or has_orphan_templates:
                default_section_id = _default_product_section_id(session)
                for product in session.scalars(select(Product).where(Product.section_id.is_(None))):
                    product.section_id = default_section_id
                for template in session.scalars(select(ProductTemplate).where(ProductTemplate.section_id.is_(None))):
                    template.section_id = default_section_id

            for code, name, rate, is_base in [
                ("UZS", "O'zbek so'mi", 1, 1),
                ("USD", "AQSh dollari", 12500, 0),
                ("EUR", "Yevro", 13500, 0),
            ]:
                if not session.scalar(select(Currency).where(Currency.code == code)):
                    session.add(Currency(
                        id=stable_row_id("currencies", code),
                        code=code,
                        name=name,
                        rate_to_uzs=rate,
                        is_base=is_base,
                    ))

            # An earlier build cached a password hash for offline sign-in.
            # Logging in is server-side only now, so drop any leftovers.
            session.execute(
                delete(UserSetting).where(UserSetting.key == "offline_password_hash")
            )

            existing_category_names = set(session.scalars(select(ExpenseCategory.name)).all())
            if seed_defaults:
                for name in ["Ijara", "Transport", "Kommunal", "Ish haqi", "Kassir", "Boshqa"]:
                    if name not in existing_category_names:
                        session.add(ExpenseCategory(id=stable_row_id("expense_categories", name), name=name))
                        existing_category_names.add(name)

            # The cashier category drives salary deductions, so it must exist in
            # databases created before the feature shipped as well.
            if not any(is_cashier_expense_category_name(name) for name in existing_category_names):
                session.add(ExpenseCategory(
                    id=stable_row_id("expense_categories", CASHIER_EXPENSE_CATEGORY_NAME),
                    name=CASHIER_EXPENSE_CATEGORY_NAME,
                ))
                existing_category_names.add(CASHIER_EXPENSE_CATEGORY_NAME)

            if seed_defaults and session.scalar(select(func.count(ProductTemplate.id))) == 0:
                template = ProductTemplate(
                    id=stable_row_id("product_templates", "Umumiy mahsulot"),
                    name="Umumiy mahsulot",
                    section_id=default_section_id,
                )
                session.add(template)
                session.flush()
                for order, field_name in enumerate(["Brend", "Model", "Rang"]):
                    session.add(ProductTemplateField(template_id=template.id, name=field_name, sort_order=order))
        with engine.begin() as conn:
            conn.exec_driver_sql("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email ON users(email)")
        migrate_finance_manual_json()
    finally:
        _sync_suspend_token.__exit__(None, None, None)


def _quote_identifier(name):
    return '"' + str(name).replace('"', '""') + '"'


def _sync_state_get(conn, key):
    _ensure_sync_state_table(conn)
    row = conn.exec_driver_sql("SELECT value FROM sync_state WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def _sync_state_set(conn, key, value):
    _ensure_sync_state_table(conn)
    conn.exec_driver_sql(
        """
        INSERT INTO sync_state(key, value, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP
        """,
        (key, value),
    )


def _bind_account_identity(user_uid, email):
    safe_uid = _safe_account_uid(user_uid)
    normalized_email = _normalize_email(email)
    if not normalized_email:
        raise AppError("Online account emaili topilmadi.")
    with _get_engine().begin() as conn:
        stored_email = _normalize_email(_sync_state_get(conn, "account_email"))
        stored_uid = _sync_state_get(conn, "account_user_uid")
        if stored_email and stored_email != normalized_email:
            raise AppError("Lokal baza boshqa email accountiga tegishli.")
        if stored_uid and stored_uid != safe_uid:
            _sync_state_set(conn, "server_reseed_required", "1")
            _sync_state_set(conn, "last_dirty_at", _utc_now())
            _sync_state_set(conn, "pending_change_count", "1")
        _sync_state_set(conn, "account_email", normalized_email)
        _sync_state_set(conn, "account_user_uid", safe_uid)


def mark_server_bootstrap_required():
    with _get_engine().begin() as conn:
        _sync_state_set(conn, "server_bootstrap_required", "1")


def mark_server_bootstrap_complete():
    with _get_engine().begin() as conn:
        _sync_state_set(conn, "server_bootstrap_required", "0")


def is_server_bootstrap_required():
    with _get_engine().begin() as conn:
        return _sync_state_get(conn, "server_bootstrap_required") == "1"


def is_server_reseed_required():
    with _get_engine().begin() as conn:
        return _sync_state_get(conn, "server_reseed_required") == "1"


def mark_server_reseed_complete():
    with _get_engine().begin() as conn:
        _sync_state_set(conn, "server_reseed_required", "0")


def _sync_state_int(conn, key, default=0):
    value = _sync_state_get(conn, key)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def get_sync_device_key():
    with _get_engine().begin() as conn:
        key = _sync_state_get(conn, "device_key")
        if not key:
            key = "desktop-" + secrets.token_hex(12)
            _sync_state_set(conn, "device_key", key)
        return key


def _ensure_sync_outbox_table(conn):
    # `seq` is what lets a push clear exactly what it uploaded. Without it, a
    # sale rung up while the upload was in flight got deleted from the queue
    # along with the rows that were actually sent, and never reached the server.
    conn.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS sync_outbox (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            table_name TEXT NOT NULL,
            local_id TEXT NOT NULL,
            action TEXT NOT NULL DEFAULT 'upsert',
            updated_at TEXT NOT NULL,
            UNIQUE (table_name, local_id)
        )
    """)
    columns = {
        row[1] for row in conn.exec_driver_sql("PRAGMA table_info(sync_outbox)").fetchall()
    }
    if "seq" in columns:
        return
    # Older builds created the table without `seq`. Rebuild it, keeping every
    # queued change - these are edits that have not reached the server yet.
    conn.exec_driver_sql("ALTER TABLE sync_outbox RENAME TO sync_outbox_legacy")
    conn.exec_driver_sql("""
        CREATE TABLE sync_outbox (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            table_name TEXT NOT NULL,
            local_id TEXT NOT NULL,
            action TEXT NOT NULL DEFAULT 'upsert',
            updated_at TEXT NOT NULL,
            UNIQUE (table_name, local_id)
        )
    """)
    conn.exec_driver_sql("""
        INSERT INTO sync_outbox (table_name, local_id, action, updated_at)
        SELECT table_name, local_id, action, updated_at FROM sync_outbox_legacy
    """)
    conn.exec_driver_sql("DROP TABLE sync_outbox_legacy")


def _sync_primary_key_column(table_name):
    return "key" if table_name == "app_settings" else "id"


def _write_outbox_entries(conn, entries):
    if not entries:
        return
    now = _utc_now()
    _ensure_sync_outbox_table(conn)
    for table_name, local_id, action in entries:
        conn.exec_driver_sql(
            "INSERT OR REPLACE INTO sync_outbox (table_name, local_id, action, updated_at) VALUES (?, ?, ?, ?)",
            (table_name, str(local_id), action, now),
        )


def _flush_session_outbox(session):
    """Persist this session's queued changes on the session's own connection.

    Writing them here rather than in a separate transaction after commit is the
    whole point: previously a crash, a lock, or the bare `except` between the
    two transactions dropped the change permanently, and the row was never
    pushed again because the data commit had already succeeded.
    """
    entries = session.info.get("sync_outbox_entries")
    if not entries or _is_sync_suspended():
        return
    connection = session.connection()
    _write_outbox_entries(connection, entries)
    _sync_state_set(connection, "last_dirty_at", _utc_now())
    _sync_state_set(
        connection,
        "pending_change_count",
        str(_sync_state_int(connection, "pending_change_count") + len(entries)),
    )
    session.info["sync_outbox_entries"] = set()


def get_sync_status():
    with _get_engine().begin() as conn:
        _ensure_sync_outbox_table(conn)
        last_dirty = _sync_state_get(conn, "last_dirty_at")
        last_push = _sync_state_get(conn, "last_push_at")
        last_pull = _sync_state_get(conn, "last_pull_at")
        outbox_count = conn.exec_driver_sql("SELECT COUNT(*) FROM sync_outbox").scalar() or 0
        tombstone_count = conn.exec_driver_sql("SELECT COUNT(*) FROM sync_tombstones").scalar() if _has_table(conn, "sync_tombstones") else 0
        pending_count = outbox_count + tombstone_count
        pending = bool(pending_count > 0 or (last_dirty and (not last_push or str(last_dirty) >= str(last_push))))
        record_count = 0
        for table_name in SYNC_TABLES:
            if not _has_table(conn, table_name):
                continue
            quoted = _quote_identifier(table_name)
            record_count += conn.exec_driver_sql(f"SELECT COUNT(*) FROM {quoted}").scalar() or 0
        return Row(
            pending=pending,
            last_dirty_at=last_dirty,
            last_push_at=last_push,
            last_pull_at=last_pull,
            pending_change_count=pending_count,
            record_count=record_count,
        )


def get_user_api_token(user_id):
    if not user_id:
        return None
    with session_scope() as session:
        row = session.get(UserSetting, {"user_id": user_id, "key": "api_access_token"})
        return row.value if row and row.value else None


def export_sync_records(incremental=False, with_watermark=False):
    """Collect the records to push.

    With ``with_watermark`` the caller also gets the highest outbox row and the
    tombstones included, so a successful push can clear exactly what it sent and
    leave anything queued in the meantime for the next round.
    """
    now = _utc_now()
    device_key = get_sync_device_key()
    records = []
    max_seq = None
    tombstone_ids = []
    with _get_engine().begin() as conn:
        _ensure_sync_outbox_table(conn)

        # Incremental mode: only export records that were created, modified or deleted
        if incremental and not is_server_reseed_required():
            outbox_rows = conn.exec_driver_sql(
                "SELECT seq, table_name, local_id, action, updated_at FROM sync_outbox ORDER BY seq"
            ).mappings().all()
            for item in outbox_rows:
                seq = item["seq"]
                if max_seq is None or seq > max_seq:
                    max_seq = seq
                table_name = item["table_name"]
                local_id = item["local_id"]
                action = item["action"]
                if table_name not in SYNC_TABLES or not _has_table(conn, table_name):
                    continue
                if action == "delete":
                    records.append({
                        "table_name": table_name,
                        "local_id": str(local_id),
                        "data": {},
                        "local_updated_at": item["updated_at"],
                        "deleted_at": item["updated_at"],
                        "source_device_key": device_key,
                    })
                else:
                    quoted = _quote_identifier(table_name)
                    pk_col = _sync_primary_key_column(table_name)
                    row = conn.exec_driver_sql(
                        f"SELECT * FROM {quoted} WHERE {pk_col} = ?", (local_id,)
                    ).mappings().first()
                    if row:
                        data = dict(row)
                        local_updated_at = data.get("updated_at") or data.get("created_at") or item["updated_at"] or now
                        records.append({
                            "table_name": table_name,
                            "local_id": str(local_id),
                            "data": data,
                            "local_updated_at": str(local_updated_at) if local_updated_at else now,
                            "deleted_at": str(data.get("deleted_at")) if data.get("deleted_at") else None,
                            "source_device_key": device_key,
                        })
            if _has_table(conn, "sync_tombstones"):
                tombstones = conn.exec_driver_sql(
                    "SELECT table_name, local_id, deleted_at FROM sync_tombstones"
                ).mappings().all()
                for tombstone in tombstones:
                    if tombstone["table_name"] not in SYNC_TABLES:
                        continue
                    tombstone_ids.append((tombstone["table_name"], str(tombstone["local_id"])))
                    records.append({
                        "table_name": tombstone["table_name"],
                        "local_id": str(tombstone["local_id"]),
                        "data": {},
                        "local_updated_at": tombstone["deleted_at"],
                        "deleted_at": tombstone["deleted_at"],
                        "source_device_key": device_key,
                    })
            if with_watermark:
                return records, {"up_to_seq": max_seq, "tombstone_ids": tombstone_ids}
            return records

        # Full export (when server reseed is required or explicitly requested)
        for table_name in SYNC_TABLES:
            if not _has_table(conn, table_name):
                continue
            quoted = _quote_identifier(table_name)
            if table_name == "users" and _ACTIVE_ACCOUNT_UID:
                rows = conn.exec_driver_sql(
                    """
                    SELECT users.*
                    FROM users
                    WHERE users.id NOT IN (
                        SELECT user_id FROM user_settings
                        WHERE key = 'api_user_uid' AND value = ?
                    )
                    """,
                    (_ACTIVE_ACCOUNT_UID,),
                ).mappings().all()
            else:
                rows = conn.exec_driver_sql(f"SELECT * FROM {quoted}").mappings().all()
            for row in rows:
                data = dict(row)
                pk_col = _sync_primary_key_column(table_name)
                local_id = str(data.get(pk_col))
                if not local_id or local_id == "None":
                    continue
                local_updated_at = data.get("updated_at") or data.get("created_at") or now
                records.append({
                    "table_name": table_name,
                    "local_id": local_id,
                    "data": data,
                    "local_updated_at": str(local_updated_at) if local_updated_at else now,
                    "deleted_at": str(data.get("deleted_at")) if data.get("deleted_at") else None,
                    "source_device_key": device_key,
                })
        if _has_table(conn, "sync_tombstones"):
            tombstones = conn.exec_driver_sql(
                "SELECT table_name, local_id, deleted_at FROM sync_tombstones"
            ).mappings().all()
            for tombstone in tombstones:
                if tombstone["table_name"] not in SYNC_TABLES:
                    continue
                records.append({
                    "table_name": tombstone["table_name"],
                    "local_id": str(tombstone["local_id"]),
                    "data": {},
                    "local_updated_at": tombstone["deleted_at"],
                    "deleted_at": tombstone["deleted_at"],
                    "source_device_key": device_key,
                })
    # A full export replaces everything, so the whole queue is superseded.
    if with_watermark:
        return records, {"up_to_seq": None, "tombstone_ids": None}
    return records


def _unique_index_columns(conn, table_name):
    """The column groups SQLite will refuse a duplicate on."""
    groups = []
    for row in conn.exec_driver_sql(f"PRAGMA index_list('{table_name}')").fetchall():
        if not row[2]:
            continue
        columns = [item[2] for item in conn.exec_driver_sql(f"PRAGMA index_info('{row[1]}')").fetchall()]
        if columns and all(columns):
            groups.append(columns)
    return groups


def _clear_conflicting_unique_rows(conn, table_name, filtered, pk_col):
    """Make room for an incoming row that clashes on a unique business key.

    Two devices can independently create a category called "Ichimliklar"; each
    gives it its own UUID, and neither row can sit beside the other because the
    name is unique.  The whole sync model is last-writer-wins, so the arriving
    row takes the name and the local one that held it is removed.  Without this
    a single such clash raises and abandons the entire download.
    """
    incoming_key = filtered.get(pk_col)
    removed = 0
    for columns in _unique_index_columns(conn, table_name):
        if pk_col in columns or not all(column in filtered for column in columns):
            continue
        where = " AND ".join(f"{_quote_identifier(column)} IS ?" for column in columns)
        clashing = conn.exec_driver_sql(
            f"SELECT {_quote_identifier(pk_col)} FROM {_quote_identifier(table_name)} WHERE {where}",
            tuple(filtered[column] for column in columns),
        ).fetchall()
        for row in clashing:
            if str(row[0]) == str(incoming_key):
                continue
            conn.exec_driver_sql(
                f"DELETE FROM {_quote_identifier(table_name)} WHERE {_quote_identifier(pk_col)} = ?",
                (row[0],),
            )
            removed += 1
    return removed


# How many rows one import transaction carries. A download of a few thousand
# rows used to be a single transaction, which held the database and the window
# for as long as it took; in bounded pieces the app stays answerable and a
# failure loses one piece rather than the whole download.
IMPORT_CHUNK_SIZE = 200


def import_sync_records(records, chunk_size=IMPORT_CHUNK_SIZE):
    if not records:
        return 0
    counters = {"imported": 0, "skipped_legacy": 0, "rejected": 0}
    touched_tables = set()
    touched_sales = set()
    engine = _get_engine()
    _sync_suspend_token = suspend_sync()
    _sync_suspend_token.__enter__()
    try:
        table_order = {table_name: index for index, table_name in enumerate(SYNC_TABLES)}
        ordered_records = sorted(
            records,
            key=lambda record: (
                1 if record.get("deleted_at") and not record.get("data") else 0,
                -table_order.get(record.get("table_name"), -1)
                if record.get("deleted_at") and not record.get("data")
                else table_order.get(record.get("table_name"), len(SYNC_TABLES)),
            ),
        )
        size = max(1, int(chunk_size or IMPORT_CHUNK_SIZE))
        for start in range(0, len(ordered_records), size):
          with engine.begin() as conn:
            for record in ordered_records[start:start + size]:
                table_name = record.get("table_name")
                if table_name not in SYNC_TABLES or not _has_table(conn, table_name):
                    continue
                if table_name == "account_assets":
                    _sync_state_set(conn, "account_assets_server_seen", "1")
                data = record.get("data") or {}
                if not isinstance(data, dict):
                    continue
                # A row still keyed by the old integers belongs to a device that
                # has not upgraded yet. Letting it in would put a colliding id
                # back into a table that just got rid of them, so it is dropped
                # rather than merged; that device will re-send it as a UUID once
                # it upgrades.
                if table_name in UUID_KEYED_TABLES:
                    incoming_id = data.get("id", record.get("local_id"))
                    if incoming_id is not None and not is_row_uuid(incoming_id):
                        counters["skipped_legacy"] += 1
                        continue
                columns = _table_columns(conn, table_name)
                filtered = {key: value for key, value in data.items() if key in columns}
                if not filtered:
                    local_id = record.get("local_id")
                    if record.get("deleted_at") and local_id:
                        pk_col = _sync_primary_key_column(table_name)
                        conn.exec_driver_sql(
                            f"DELETE FROM {_quote_identifier(table_name)} "
                            f"WHERE {_quote_identifier(pk_col)}=?",
                            (local_id,),
                        )
                        counters["imported"] += 1
                        touched_tables.add(table_name)
                        if table_name in ("sale_items", "sale_returns"):
                            touched_sales.add(str(record.get("local_id") or ""))
                    continue
                quoted_table = _quote_identifier(table_name)
                quoted_columns = ", ".join(_quote_identifier(column) for column in filtered)
                placeholders = ", ".join("?" for _ in filtered)
                values = tuple(filtered.values())
                pk_col = _sync_primary_key_column(table_name)
                update_columns = [column for column in filtered if column != pk_col]
                conflict_action = (
                    "DO UPDATE SET " + ", ".join(
                        f"{_quote_identifier(column)}=excluded.{_quote_identifier(column)}"
                        for column in update_columns
                    )
                    if update_columns else "DO NOTHING"
                )
                statement = (
                    f"INSERT INTO {quoted_table} ({quoted_columns}) VALUES ({placeholders}) "
                    f"ON CONFLICT({_quote_identifier(pk_col)}) {conflict_action}"
                )
                try:
                    with conn.begin_nested():
                        conn.exec_driver_sql(statement, values)
                except IntegrityError:
                    # Almost always a unique business key held by a different
                    # row. Clear the holder and let the newer copy win; if it
                    # still will not go in, skip this one record rather than
                    # losing the rest of the download.
                    try:
                        with conn.begin_nested():
                            _clear_conflicting_unique_rows(conn, table_name, filtered, pk_col)
                            conn.exec_driver_sql(statement, values)
                    except IntegrityError:
                        counters["rejected"] += 1
                        continue
                if _has_table(conn, "sync_tombstones"):
                    conn.exec_driver_sql(
                        "DELETE FROM sync_tombstones WHERE table_name=? AND local_id=?",
                        (table_name, str(record.get("local_id") or "")),
                    )
                counters["imported"] += 1
                touched_tables.add(table_name)
                if table_name in ("sales", "sale_items", "sale_returns"):
                    sale_key = filtered.get("sale_id") or filtered.get("id")
                    if sale_key:
                        touched_sales.add(str(sale_key))
        # A download can hand us a return without the sale row that quotes it,
        # so the cached figures are rebuilt from what actually arrived. Still
        # inside the suspension: this derives, it does not originate.
        _reconcile_imported_sales(touched_sales)
        with engine.begin() as conn:
            _sync_state_set(conn, "last_pull_at", _utc_now())
            _sync_state_set(conn, "last_pull_skipped_legacy", str(counters["skipped_legacy"]))
            _sync_state_set(conn, "last_pull_rejected", str(counters["rejected"]))
            _sync_state_set(conn, "last_pull_tables", ",".join(sorted(touched_tables)))
    finally:
        _sync_suspend_token.__exit__(None, None, None)
    return counters["imported"]


def _reconcile_imported_sales(sale_ids):
    for sale_id in sale_ids:
        if not sale_id:
            continue
        try:
            recalculate_sale_totals(sale_id)
        except Exception:
            # One unreadable sale must not abandon the rest of the download.
            continue


def mark_sync_pushed(up_to_seq=None, tombstone_ids=None):
    """Clear the queue after a successful push.

    ``up_to_seq`` is the highest outbox row the push actually carried. Anything
    queued after that - a sale rung up while the upload was in flight - stays
    queued for the next push instead of being deleted unsent.
    """
    with _get_engine().begin() as conn:
        now = _utc_now()
        _ensure_sync_outbox_table(conn)
        if up_to_seq is None:
            conn.exec_driver_sql("DELETE FROM sync_outbox")
        else:
            conn.exec_driver_sql("DELETE FROM sync_outbox WHERE seq <= ?", (int(up_to_seq),))
        if _has_table(conn, "sync_tombstones"):
            if tombstone_ids is None:
                conn.exec_driver_sql("DELETE FROM sync_tombstones")
            elif tombstone_ids:
                for table_name, local_id in tombstone_ids:
                    conn.exec_driver_sql(
                        "DELETE FROM sync_tombstones WHERE table_name = ? AND local_id = ?",
                        (table_name, str(local_id)),
                    )
        remaining = conn.exec_driver_sql("SELECT COUNT(*) FROM sync_outbox").scalar() or 0
        if _has_table(conn, "sync_tombstones"):
            remaining += conn.exec_driver_sql("SELECT COUNT(*) FROM sync_tombstones").scalar() or 0
        _sync_state_set(conn, "last_push_at", now)
        _sync_state_set(conn, "last_dirty_at", "" if not remaining else now)
        _sync_state_set(conn, "pending_change_count", str(remaining))


def get_app_settings(user_id=None):
    defaults = {"app_name": "Market POS", "theme": "dark_blue", "language": "uz", "currency": "UZS"}
    with session_scope() as session:
        settings = dict(defaults)
        for row in session.scalars(select(AppSetting)):
            if row.value is not None:
                settings[row.key] = row.value
        if user_id is not None:
            rows = session.scalars(select(UserSetting).where(UserSetting.user_id == user_id))
            for row in rows:
                if row.value is not None and row.key in {"theme", "language"}:
                    settings[row.key] = row.value
        return settings


def save_app_settings(settings, user_id=None):
    allowed = {"app_name", "theme", "language", "currency"}
    with session_scope() as session:
        for key, value in settings.items():
            if key not in allowed:
                continue
            if key in {"app_name", "currency"} or user_id is None:
                row = session.get(AppSetting, key) or AppSetting(key=key)
                row.value = str(value)
                session.merge(row)
            else:
                row = session.get(UserSetting, {"user_id": user_id, "key": key}) or UserSetting(user_id=user_id, key=key)
                row.value = str(value)
                session.merge(row)


MAX_ACCOUNT_ASSET_BYTES = 512 * 1024


def save_account_asset(asset_id, content, media_type="application/octet-stream"):
    asset_key = str(asset_id or "").strip()
    if not asset_key or len(asset_key) > 120:
        raise AppError("Account asset identifikatori noto'g'ri.")
    if not isinstance(content, (bytes, bytearray)):
        raise AppError("Account asset binary formatda bo'lishi kerak.")
    raw = bytes(content)
    if not raw:
        raise AppError("Bo'sh account asset saqlab bo'lmaydi.")
    if len(raw) > MAX_ACCOUNT_ASSET_BYTES:
        raise AppError("Logo hajmi 512 KB dan oshmasligi kerak.")
    digest = hashlib.sha256(raw).hexdigest()
    encoded = base64.b64encode(raw).decode("ascii")
    with session_scope() as session:
        row = session.get(AccountAsset, asset_key) or AccountAsset(id=asset_key)
        row.media_type = str(media_type or "application/octet-stream")[:120]
        row.content_base64 = encoded
        row.sha256 = digest
        row.updated_at = _utc_now()
        session.merge(row)
    return Row(id=asset_key, media_type=row.media_type, sha256=digest, size=len(raw))


def get_account_asset(asset_id):
    asset_key = str(asset_id or "").strip()
    if not asset_key:
        return None
    with session_scope() as session:
        row = session.get(AccountAsset, asset_key)
        if row is None:
            return None
        try:
            raw = base64.b64decode(row.content_base64 or "", validate=True)
        except (ValueError, TypeError):
            return None
        if not raw or len(raw) > MAX_ACCOUNT_ASSET_BYTES:
            return None
        digest = hashlib.sha256(raw).hexdigest()
        if row.sha256 and not secrets.compare_digest(digest, row.sha256):
            return None
        return Row(
            id=row.id,
            media_type=row.media_type,
            content=raw,
            sha256=digest,
            updated_at=row.updated_at,
        )


def delete_account_asset(asset_id):
    asset_key = str(asset_id or "").strip()
    if not asset_key:
        return False
    with session_scope() as session:
        row = session.get(AccountAsset, asset_key)
        if row is None:
            return False
        session.delete(row)
    return True


def has_pending_sync_for_table(table_name):
    if table_name not in SYNC_TABLES:
        return False
    with _get_engine().begin() as conn:
        _ensure_sync_outbox_table(conn)
        outbox = conn.exec_driver_sql(
            "SELECT 1 FROM sync_outbox WHERE table_name=? LIMIT 1",
            (table_name,),
        ).fetchone()
        if outbox:
            return True
        if not _has_table(conn, "sync_tombstones"):
            return False
        tombstone = conn.exec_driver_sql(
            "SELECT 1 FROM sync_tombstones WHERE table_name=? LIMIT 1",
            (table_name,),
        ).fetchone()
        return tombstone is not None


def has_seen_server_account_assets():
    with _get_engine().begin() as conn:
        return _sync_state_get(conn, "account_assets_server_seen") == "1"


# --------------------------------------------------------------------------
# Realtime sync state: server generation, remote-change flag, safety backups
# --------------------------------------------------------------------------

BACKUP_DIR = os.path.join(DATA_DIR, "backups")


def get_sync_generation():
    """Server change counter as of our last successful push/pull."""
    with _get_engine().begin() as conn:
        return _sync_state_int(conn, "server_generation", 0)


def set_sync_generation(value):
    try:
        generation = int(value)
    except (TypeError, ValueError):
        return
    with _get_engine().begin() as conn:
        _sync_state_set(conn, "server_generation", str(generation))


def get_ledger_baseline():
    """When the money ledger began, and how much predates it.

    Figures from before this moment were inherited from the previous scheme:
    a return used to overwrite the amount it reduced, so those sales cannot be
    recomputed from their own history. Everything after it can.
    """
    with _get_engine().begin() as conn:
        row = conn.exec_driver_sql(
            "SELECT value FROM sync_state WHERE key='ledger_baseline_at'"
        ).fetchone()
        inherited = _sync_state_int(conn, "ledger_inherited_sales", 0)
    return Row(dict(
        started_at=row[0] if row and row[0] else None,
        inherited_sales=inherited,
    ))


def get_pull_watermark():
    """How far into the server's history this device has already read.

    The server can hand back only what changed after a given moment, and it
    always has been able to -- the client simply never asked, so every download
    was a full copy of the account. This is the moment to ask from.
    """
    with _get_engine().begin() as conn:
        row = conn.exec_driver_sql(
            "SELECT value FROM sync_state WHERE key='pull_watermark'"
        ).fetchone()
    return row[0] if row and row[0] else None


def set_pull_watermark(value):
    """Only ever moved after a download that kept everything it was given."""
    if not value:
        return
    with _get_engine().begin() as conn:
        _sync_state_set(conn, "pull_watermark", str(value))


def clear_pull_watermark():
    with _get_engine().begin() as conn:
        _sync_state_set(conn, "pull_watermark", "")


def get_last_pull_stats():
    """What the most recent download had to leave behind."""
    with _get_engine().begin() as conn:
        row = conn.exec_driver_sql(
            "SELECT value FROM sync_state WHERE key='last_pull_tables'"
        ).fetchone()
        tables = [name for name in str((row[0] if row else "") or "").split(",") if name]
        return {
            "skipped_legacy": _sync_state_int(conn, "last_pull_skipped_legacy", 0),
            "rejected": _sync_state_int(conn, "last_pull_rejected", 0),
            "tables": tables,
        }


def is_upgrade_reconcile_required():
    """True until this device has settled with the server after the upgrade.

    A device that had been running for a while keeps rows that were deleted on
    the other devices long ago: deletions only travel as tombstones, and a
    tombstone the device never received leaves the row sitting there, ready to
    be uploaded again. The first sync after the upgrade therefore takes the
    server's copy as the truth rather than merging into it.
    """
    with _get_engine().begin() as conn:
        return _sync_state_int(conn, "upgrade_reconcile_required", 0) == 1


def mark_upgrade_reconcile_complete():
    with _get_engine().begin() as conn:
        _sync_state_set(conn, "upgrade_reconcile_required", "0")


def is_identity_reset_required():
    """True while the server still holds rows keyed by the old integers."""
    with _get_engine().begin() as conn:
        return _sync_state_int(conn, "identity_reset_required", 0) == 1


def mark_identity_reset_complete():
    with _get_engine().begin() as conn:
        _sync_state_set(conn, "identity_reset_required", "0")


def get_applied_purge_generation():
    """Newest server purge that this local account database has applied."""
    with _get_engine().begin() as conn:
        return _sync_state_int(conn, "applied_purge_generation", 0)


def mark_remote_change(generation, tables=None, device_key=None, changed_at=None):
    """Remember that the server moved ahead of us, so the badge survives restart."""
    try:
        generation = int(generation)
    except (TypeError, ValueError):
        return
    with _get_engine().begin() as conn:
        if generation <= _sync_state_int(conn, "server_generation", 0):
            return
        _sync_state_set(conn, "remote_generation", str(generation))
        _sync_state_set(conn, "remote_tables", ",".join(sorted({t for t in (tables or []) if t})))
        _sync_state_set(conn, "remote_device_key", str(device_key or ""))
        _sync_state_set(conn, "remote_changed_at", str(changed_at or _utc_now()))


def clear_remote_change():
    with _get_engine().begin() as conn:
        _sync_state_set(conn, "remote_generation", "0")
        _sync_state_set(conn, "remote_tables", "")
        _sync_state_set(conn, "remote_device_key", "")
        _sync_state_set(conn, "remote_changed_at", "")


def get_remote_change():
    with _get_engine().begin() as conn:
        known = _sync_state_int(conn, "server_generation", 0)
        remote = _sync_state_int(conn, "remote_generation", 0)
        tables = (_sync_state_get(conn, "remote_tables") or "").split(",")
        return Row(
            pending=bool(remote > known),
            generation=remote,
            known_generation=known,
            tables=[table for table in tables if table],
            device_key=_sync_state_get(conn, "remote_device_key") or "",
            changed_at=_sync_state_get(conn, "remote_changed_at") or "",
        )


def count_sync_records():
    """Number of rows across every synced table in the local database."""
    total = 0
    with _get_engine().begin() as conn:
        for table_name in SYNC_TABLES:
            if not _has_table(conn, table_name):
                continue
            total += conn.exec_driver_sql(
                f"SELECT COUNT(*) FROM {_quote_identifier(table_name)}"
            ).scalar() or 0
    return int(total)


def create_local_backup(tag="presync"):
    """Snapshot the active account database before a destructive sync choice."""
    if not DB_PATH or DB_PATH == ":memory:" or not os.path.exists(DB_PATH):
        return None
    os.makedirs(BACKUP_DIR, exist_ok=True)
    account = os.path.basename(os.path.dirname(DB_PATH)) or "account"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = os.path.join(BACKUP_DIR, f"{account}.{tag}_{timestamp}.db")
    try:
        with _get_engine().begin() as conn:
            conn.exec_driver_sql("PRAGMA wal_checkpoint(FULL)")
    except Exception:
        pass
    try:
        _copy_sqlite_database(DB_PATH, target)
    except Exception:
        return None
    return target


def save_server_snapshot_backup(records, tag="server_snapshot"):
    """Persist the server copy to disk before we overwrite it with ours."""
    if not records:
        return None
    os.makedirs(BACKUP_DIR, exist_ok=True)
    account = os.path.basename(os.path.dirname(DB_PATH)) or "account"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = os.path.join(BACKUP_DIR, f"{account}.{tag}_{timestamp}.json.gz")
    try:
        payload = json.dumps(
            {"saved_at": _utc_now(), "records": records},
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")
        with gzip.open(target, "wb") as handle:
            handle.write(payload)
    except Exception:
        return None
    return target


def set_known_release(version, tag=None, published_at=None):
    """Remember the newest published build we have been told about."""
    version = str(version or "").strip()
    if not version:
        return
    with _get_engine().begin() as conn:
        _sync_state_set(conn, "latest_release_version", version)
        _sync_state_set(conn, "latest_release_tag", str(tag or ""))
        _sync_state_set(conn, "latest_release_at", str(published_at or ""))


def get_known_release():
    with _get_engine().begin() as conn:
        return Row(
            version=_sync_state_get(conn, "latest_release_version") or "",
            tag=_sync_state_get(conn, "latest_release_tag") or "",
            published_at=_sync_state_get(conn, "latest_release_at") or "",
        )


def _protected_user_ids(conn):
    """Local user rows that must survive a wipe: the signed-in online account."""
    if not _ACTIVE_ACCOUNT_UID or not _has_table(conn, "user_settings"):
        return []
    rows = conn.exec_driver_sql(
        "SELECT user_id FROM user_settings WHERE key='api_user_uid' AND value=?",
        (_ACTIVE_ACCOUNT_UID,),
    ).fetchall()
    return [row[0] for row in rows if row[0] is not None]


def clear_sync_outbox():
    """Drop queued local changes (used when the user chooses the server copy)."""
    with _get_engine().begin() as conn:
        _ensure_sync_outbox_table(conn)
        conn.exec_driver_sql("DELETE FROM sync_outbox")
        if _has_table(conn, "sync_tombstones"):
            conn.exec_driver_sql("DELETE FROM sync_tombstones")
        _sync_state_set(conn, "last_dirty_at", "")
        _sync_state_set(conn, "pending_change_count", "0")


def wipe_sync_tables():
    """Empty every synced table, keeping the signed-in account's own user row.

    Deleting that row would take its ``user_settings`` (including the API token)
    with it via the cascade and lock the user out of their own account.
    """
    _sync_suspend_token = suspend_sync()
    _sync_suspend_token.__enter__()
    try:
        with _get_engine().begin() as conn:
            protected = _protected_user_ids(conn)
            # Children first: SYNC_TABLES is ordered parents-first for import.
            for table_name in reversed(SYNC_TABLES):
                if not _has_table(conn, table_name):
                    continue
                quoted = _quote_identifier(table_name)
                if table_name == "users" and protected:
                    placeholders = ", ".join("?" for _ in protected)
                    conn.exec_driver_sql(
                        f"DELETE FROM {quoted} WHERE id NOT IN ({placeholders})",
                        tuple(protected),
                    )
                else:
                    conn.exec_driver_sql(f"DELETE FROM {quoted}")
            if _has_table(conn, "sync_tombstones"):
                conn.exec_driver_sql("DELETE FROM sync_tombstones")
            _ensure_sync_outbox_table(conn)
            conn.exec_driver_sql("DELETE FROM sync_outbox")
    finally:
        _sync_suspend_token.__exit__(None, None, None)


def _remove_account_purge_artifacts():
    """Remove account-specific copies that could resurrect erased data."""
    removed = 0
    account_dir = os.path.dirname(DB_PATH)
    account_key = os.path.basename(account_dir) or "account"
    candidates = [
        os.path.join(account_dir, "custom_logo.png"),
        os.path.join(account_dir, f".{os.path.basename(DB_PATH)}.account_logo_migrated"),
    ]
    if os.path.isdir(BACKUP_DIR):
        try:
            candidates.extend(
                os.path.join(BACKUP_DIR, name)
                for name in os.listdir(BACKUP_DIR)
                if name.startswith(f"{account_key}.")
            )
        except OSError:
            pass
    for path in candidates:
        try:
            if os.path.isfile(path):
                os.remove(path)
                removed += 1
        except OSError:
            pass
    return removed


def apply_remote_purge(purge_generation, server_generation=None):
    """Erase this account locally exactly once for a server purge marker."""
    try:
        purge_generation = int(purge_generation or 0)
        server_generation = int(server_generation or purge_generation)
    except (TypeError, ValueError):
        return Row(applied=False, removed_records=0, removed_artifacts=0)
    if purge_generation <= 0 or purge_generation <= get_applied_purge_generation():
        return Row(applied=False, removed_records=0, removed_artifacts=0)

    removed_records = count_sync_records()
    wipe_sync_tables()
    removed_artifacts = _remove_account_purge_artifacts()
    with _get_engine().begin() as conn:
        _sync_state_set(conn, "applied_purge_generation", str(purge_generation))
        _sync_state_set(conn, "server_generation", str(max(server_generation, purge_generation)))
        _sync_state_set(conn, "remote_generation", "0")
        _sync_state_set(conn, "remote_tables", "")
        _sync_state_set(conn, "remote_device_key", "")
        _sync_state_set(conn, "remote_changed_at", "")
        _sync_state_set(conn, "last_dirty_at", "")
        _sync_state_set(conn, "pending_change_count", "0")
        _sync_state_set(conn, "server_bootstrap_required", "0")
        _sync_state_set(conn, "server_reseed_required", "0")
    return Row(
        applied=True,
        purge_generation=purge_generation,
        removed_records=removed_records,
        removed_artifacts=removed_artifacts,
    )


def replace_local_from_records(records):
    """Anki-style "download from server": local content is replaced wholesale."""
    wipe_sync_tables()
    imported = import_sync_records(records)
    with _get_engine().begin() as conn:
        _sync_state_set(conn, "last_dirty_at", "")
        _sync_state_set(conn, "pending_change_count", "0")
        _sync_state_set(conn, "server_bootstrap_required", "0")
        _sync_state_set(conn, "server_reseed_required", "0")
    return imported


def _product_row(product, category_name=None, template_name=None, supplier_name=None, creator_name=None, creator_role=None):
    return _row_from_model(
        product,
        category_name=category_name,
        template_name=template_name,
        supplier_name=supplier_name,
        created_by_name=creator_name,
        created_by_role=creator_role,
    )


def _product_select():
    return (
        select(Product, Category.name, ProductTemplate.name, Supplier.name, User.username, User.role)
        .outerjoin(Category, Product.category_id == Category.id)
        .outerjoin(ProductTemplate, Product.template_id == ProductTemplate.id)
        .outerjoin(ProductSection, Product.section_id == ProductSection.id)
        .outerjoin(Supplier, Product.supplier_id == Supplier.id)
        .outerjoin(User, Product.created_by_user_id == User.id)
        .where(func.coalesce(Product.is_deleted, 0) == 0)
        .where(or_(Product.section_id.is_(None), func.coalesce(ProductSection.is_deleted, 0) == 0))
        .where(or_(Product.template_id.is_(None), func.coalesce(ProductTemplate.is_deleted, 0) == 0))
    )


def get_product_sections():
    with session_scope() as session:
        return _rows_from_models(
            session.scalars(
                select(ProductSection)
                .where(func.coalesce(ProductSection.is_deleted, 0) == 0)
                .order_by(func.coalesce(ProductSection.created_at, ""), ProductSection.name)
            ).all()
        )


def _unique_section_name(session, desired, exclude_id=None):
    base = (desired or "Bo'lim").strip()
    candidate = base
    suffix = 1
    while session.scalar(
        select(ProductSection.id).where(
            ProductSection.name == candidate,
            ProductSection.id != exclude_id if exclude_id else text("1=1"),
        )
    ):
        suffix += 1
        candidate = f"{base} ({suffix})"
    return candidate


def _unique_template_name(session, section_id, desired, exclude_id=None):
    base = (desired or "Template").strip()
    candidate = base
    suffix = 1
    while session.scalar(
        select(ProductTemplate.id).where(
            ProductTemplate.section_id == section_id,
            ProductTemplate.name == candidate,
            ProductTemplate.id != exclude_id if exclude_id else text("1=1"),
        )
    ):
        suffix += 1
        candidate = f"{base} ({suffix})"
    return candidate


def _unique_deleted_label(prefix, row_id, value):
    clean = (value or "").strip() or "empty"
    return f"__deleted_{prefix}_{row_id}_{clean}"


def add_product_section(name):
    clean_name = name.strip()
    if not clean_name:
        raise AppError("Bo'lim nomini kiriting.")
    try:
        with session_scope() as session:
            section = ProductSection(name=clean_name)
            session.add(section)
            session.flush()
            sec_id = section.id
        log_activity(
            "section_added",
            f"Yangi bo'lim: {clean_name}",
            "Mahsulotlar bo'limi yaratildi",
            level="success",
            target="products",
            badge="Bo'lim",
        )
        return sec_id
    except IntegrityError as exc:
        raise AppError("Bu bo'lim allaqachon mavjud.") from exc


def update_product_section(section_id, name):
    clean_name = name.strip()
    if not clean_name:
        raise AppError("Bo'lim nomini kiriting.")
    try:
        with session_scope() as session:
            section = session.get(ProductSection, section_id)
            if section:
                old_name = section.name
                section.name = clean_name
                session.flush()
        log_activity(
            "section_updated",
            f"Bo'lim yangilandi: {clean_name}",
            f"Eski nomi: {old_name}",
            level="info",
            target="products",
            badge="Bo'lim",
        )
    except IntegrityError as exc:
        raise AppError("Bu bo'lim allaqachon mavjud.") from exc


def delete_product_section(section_id):
    sec_name = None
    with session_scope() as session:
        section = session.get(ProductSection, section_id)
        if not section or section.is_deleted:
            return
        sec_name = section.name
        now = _trash_now()
        purge_after = _trash_purge_after()
        section.original_name = section.original_name or section.name
        section.name = _unique_deleted_label("section", section.id, section.name)
        section.is_deleted = 1
        section.deleted_at = now
        section.purge_after = purge_after
        template_ids = [
            row.id for row in session.scalars(
                select(ProductTemplate).where(ProductTemplate.section_id == section_id, func.coalesce(ProductTemplate.is_deleted, 0) == 0)
            )
        ]
        for template in session.scalars(select(ProductTemplate).where(ProductTemplate.id.in_(template_ids))):
            template.original_name = template.original_name or template.name
            template.name = _unique_deleted_label("template", template.id, template.name)
            template.is_deleted = 1
            template.deleted_at = now
            template.purge_after = purge_after
        for product in session.scalars(select(Product).where(Product.section_id == section_id)):
            if product.is_deleted:
                continue
            product.is_deleted = 1
            product.deleted_at = now
            product.purge_after = purge_after
            if product.barcode:
                product.original_barcode = product.original_barcode or product.barcode
                product.barcode = _unique_deleted_label("barcode", product.id, product.barcode)
    if sec_name:
        log_activity(
            "section_deleted",
            f"Bo'lim o'chirildi: {sec_name}",
            "Bo'lim va uning shablonlari savatchaga ko'chirildi",
            level="danger",
            target="products",
            badge="Bo'lim",
        )


def get_product_trash():
    with session_scope() as session:
        cutoff = _trash_now()
        sections = session.scalars(
            select(ProductSection)
            .where(func.coalesce(ProductSection.is_deleted, 0) == 1)
            .where(or_(ProductSection.purge_after.is_(None), ProductSection.purge_after >= cutoff))
            .order_by(ProductSection.deleted_at.desc(), ProductSection.id.desc())
        ).all()
        section_rows = []
        for sec in sections:
            p_count = session.scalar(
                select(func.count(Product.id)).where(Product.section_id == sec.id, func.coalesce(Product.is_deleted, 0) == 1)
            ) or 0
            section_rows.append(_row_from_model(sec, product_count=p_count))

        products = session.execute(
            select(Product, ProductSection.original_name, ProductSection.name, ProductSection.is_deleted)
            .outerjoin(ProductSection, Product.section_id == ProductSection.id)
            .where(func.coalesce(Product.is_deleted, 0) == 1)
            .where(or_(ProductSection.id.is_(None), func.coalesce(ProductSection.is_deleted, 0) == 0))
            .where(or_(Product.purge_after.is_(None), Product.purge_after >= cutoff))
            .order_by(Product.deleted_at.desc(), Product.id.desc())
        ).all()
        return Row(dict(
            sections=section_rows,
            products=[
                _row_from_model(
                    product,
                    section_name=(section_original or section_name or ""),
                    section_deleted=section_deleted or 0,
                )
                for product, section_original, section_name, section_deleted in products
            ],
        ))


def restore_product_section(section_id):
    sec_name = None
    restored_count = 0
    with session_scope() as session:
        section = session.get(ProductSection, section_id)
        if not section or not section.is_deleted:
            return
        sec_name = section.original_name or section.name
        section.name = _unique_section_name(session, section.original_name or section.name, section.id)
        section.is_deleted = 0
        section.deleted_at = None
        section.purge_after = None
        section.original_name = None
        for template in session.scalars(select(ProductTemplate).where(ProductTemplate.section_id == section_id)):
            if not template.is_deleted:
                continue
            template.name = _unique_template_name(session, section_id, template.original_name or template.name, template.id)
            template.is_deleted = 0
            template.deleted_at = None
            template.purge_after = None
            template.original_name = None
        for product in session.scalars(select(Product).where(Product.section_id == section_id)):
            if not product.is_deleted:
                continue
            product.is_deleted = 0
            product.deleted_at = None
            product.purge_after = None
            restored_count += 1
            if product.original_barcode:
                conflict = session.scalar(
                    select(Product.id).where(Product.barcode == product.original_barcode, Product.id != product.id)
                )
                product.barcode = None if conflict else product.original_barcode
                product.original_barcode = None
    if sec_name:
        log_activity(
            "section_restored",
            f"Bo'lim qayta tiklandi: {sec_name}",
            f"Bo'lim va uning {restored_count} ta mahsuloti to'liq qayta tiklandi",
            level="success",
            target="products",
            badge="Bo'lim",
        )


def restore_product(product_id):
    p_name = None
    with session_scope() as session:
        product = session.get(Product, product_id)
        if not product or not product.is_deleted:
            return
        p_name = product.name
        section = session.get(ProductSection, product.section_id) if product.section_id else None
        if section and section.is_deleted:
            section.name = _unique_section_name(session, section.original_name or section.name, section.id)
            section.is_deleted = 0
            section.deleted_at = None
            section.purge_after = None
            section.original_name = None
        template = session.get(ProductTemplate, product.template_id) if product.template_id else None
        if template and template.is_deleted:
            template.name = _unique_template_name(session, template.section_id, template.original_name or template.name, template.id)
            template.is_deleted = 0
            template.deleted_at = None
            template.purge_after = None
            template.original_name = None
        product.is_deleted = 0
        product.deleted_at = None
        product.purge_after = None
        if product.original_barcode:
            conflict = session.scalar(select(Product.id).where(Product.barcode == product.original_barcode, Product.id != product.id))
            product.barcode = None if conflict else product.original_barcode
            product.original_barcode = None
    if p_name:
        log_activity(
            "product_restored",
            f"Mahsulot qayta tiklandi: {p_name}",
            "Mahsulot savatchadan muvaffaqiyatli tiklandi",
            level="success",
            target="products",
            badge="Mahsulot",
        )


def get_all_products(start_date=None, end_date=None, section_id=None):
    with session_scope() as session:
        stmt = _product_select()
        if section_id is not None:
            stmt = stmt.where(Product.section_id == section_id)
        if start_date and end_date:
            stmt = stmt.where(_date_expr(Product.created_at).between(start_date, end_date))
        rows = session.execute(stmt.order_by(Product.name)).all()
        return [_product_row(p, c, t, s, u, r) for p, c, t, s, u, r in rows]


def search_products(query, start_date=None, end_date=None, section_id=None):
    pattern = f"%{query}%"
    with session_scope() as session:
        stmt = _product_select().where(or_(Product.name.like(pattern), Product.barcode.like(pattern)))
        if section_id is not None:
            stmt = stmt.where(Product.section_id == section_id)
        if start_date and end_date:
            stmt = stmt.where(_date_expr(Product.created_at).between(start_date, end_date))
        rows = session.execute(stmt.order_by(Product.name)).all()
        return [_product_row(p, c, t, s, u, r) for p, c, t, s, u, r in rows]


def get_product_by_barcode(barcode):
    with session_scope() as session:
        row = session.execute(_product_select().where(Product.barcode == barcode)).first()
        return _product_row(*row) if row else None


def get_product_by_id(product_id):
    with session_scope() as session:
        row = session.execute(_product_select().where(Product.id == product_id)).first()
        return _product_row(*row) if row else None


def _normalize_product_money(data):
    normalized = dict(data)
    normalized.setdefault("price_currency", "UZS")
    normalized.setdefault("price_exchange_rate", 1)
    normalized.setdefault("price_original", normalized.get("price", 0))
    normalized.setdefault("cost_currency", "UZS")
    normalized.setdefault("cost_exchange_rate", 1)
    normalized.setdefault("cost_original", normalized.get("cost", 0))
    normalized.setdefault("supplier_id", None)
    normalized.setdefault("category_id", None)
    return normalized


_ACTIVITY_LISTENERS = []
_SESSION_ACTIVITIES = []
_SESSION_READ_IDS = set()
_ACTIVITY_COUNTER = 0


def clear_session_notifications():
    global _SESSION_ACTIVITIES, _SESSION_READ_IDS, _ACTIVITY_COUNTER
    _SESSION_ACTIVITIES.clear()
    _SESSION_READ_IDS.clear()
    _ACTIVITY_COUNTER = 0


def register_activity_listener(listener):
    if listener not in _ACTIVITY_LISTENERS:
        _ACTIVITY_LISTENERS.append(listener)


def unregister_activity_listener(listener):
    if listener in _ACTIVITY_LISTENERS:
        _ACTIVITY_LISTENERS.remove(listener)


# Who the entries are attributed to. The 41 places that call log_activity say
# what happened, not who did it, so the signed-in user is supplied here rather
# than threaded through every one of them.
_ACTIVITY_ACTOR = None

# How many entries the table keeps. Long enough to be a useful history, short
# enough that it never becomes the biggest thing being synchronised.
ACTIVITY_LOG_LIMIT = 500


def set_activity_actor(callback):
    """Tell the log who is signed in, as a callable returning {id, name}."""
    global _ACTIVITY_ACTOR
    _ACTIVITY_ACTOR = callback


def _current_actor():
    if _ACTIVITY_ACTOR is None:
        return {}
    try:
        return dict(_ACTIVITY_ACTOR() or {})
    except Exception:
        return {}


def _store_activity(item):
    """Write the entry so the other devices can be told about it.

    Best effort on purpose: an activity entry is a description of something
    that already happened, and failing to describe it must never undo it.
    """
    try:
        actor = _current_actor()
        with session_scope() as session:
            session.add(ActivityLog(
                id=item["id"],
                user_id=actor.get("id"),
                user_name=actor.get("name") or None,
                device_key=item.get("device_key"),
                action=item["action"],
                title=item["title"],
                message=item["message"],
                level=item["level"],
                target=item["target"],
                badge=item["badge"],
                created_at=item["created_at"],
            ))
            session.flush()
            stale = session.scalars(
                select(ActivityLog.id)
                .order_by(ActivityLog.created_at.desc(), text("rowid DESC"))
                .offset(ACTIVITY_LOG_LIMIT)
            ).all()
            for row_id in stale:
                row = session.get(ActivityLog, row_id)
                if row is not None:
                    session.delete(row)
        return actor
    except Exception:
        return {}


def log_activity(action, title, message, level="info", target="products", badge="Mahsulot"):
    device_key = ""
    try:
        device_key = get_sync_device_key()
    except Exception:
        device_key = ""
    item = {
        "id": new_row_id(),
        "action": action,
        "title": title,
        "message": message,
        "level": level,
        "target": target,
        "badge": badge,
        "device_key": device_key,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    actor = _store_activity(item)
    item["user_name"] = actor.get("name")
    _SESSION_ACTIVITIES.insert(0, item)
    if len(_SESSION_ACTIVITIES) > 300:
        _SESSION_ACTIVITIES.pop()

    for listener in list(_ACTIVITY_LISTENERS):
        try:
            listener(action, title, message, level, target, badge)
        except Exception:
            pass


def clear_activity_log():
    """Empty the feed. Used when a device starts a fresh account."""
    with session_scope() as session:
        for row in session.scalars(select(ActivityLog)).all():
            session.delete(row)


def get_recent_activities(limit=50):
    """The feed, from the table, so it survives a restart and other devices.

    Falls back to what this session did if the table cannot be read -- a
    database that is mid-migration should still show the person what they just
    did rather than an empty screen.
    """
    try:
        with session_scope() as session:
            rows = session.scalars(
                select(ActivityLog)
                .order_by(ActivityLog.created_at.desc(), text("rowid DESC"))
                .limit(limit)
            ).all()
            return [Row(dict(
                id=row.id,
                action=row.action,
                title=row.title,
                message=row.message,
                level=row.level,
                target=row.target,
                badge=row.badge,
                user_id=row.user_id,
                user_name=row.user_name,
                device_key=row.device_key,
                created_at=row.created_at,
            )) for row in rows]
    except Exception:
        return [Row(act) for act in _SESSION_ACTIVITIES[:limit]]


def take_new_remote_activities(limit=5):
    """What the other devices have done since we last looked.

    Reading them marks them seen, so the same sale is never announced twice --
    and this device's own work is skipped, because being told what you just did
    yourself is noise.
    """
    try:
        device_key = get_sync_device_key()
    except Exception:
        device_key = ""
    with _get_engine().begin() as conn:
        row = conn.exec_driver_sql(
            "SELECT value FROM sync_state WHERE key='activity_seen_at'"
        ).fetchone()
        seen_at = row[0] if row and row[0] else None
    with session_scope() as session:
        stmt = select(ActivityLog).order_by(ActivityLog.created_at, text("rowid"))
        if device_key:
            stmt = stmt.where(func.coalesce(ActivityLog.device_key, "") != device_key)
        if seen_at:
            stmt = stmt.where(ActivityLog.created_at > seen_at)
        rows = session.scalars(stmt).all()
        fresh = [Row(dict(
            id=row.id,
            title=row.title,
            message=row.message,
            level=row.level,
            target=row.target,
            user_name=row.user_name,
            created_at=row.created_at,
        )) for row in rows]
    newest = max((row["created_at"] or "" for row in fresh), default=None)
    if newest:
        with _get_engine().begin() as conn:
            _sync_state_set(conn, "activity_seen_at", newest)
    elif seen_at is None:
        # First look on a device that has nothing yet: start the marker now so
        # the whole existing history is not announced later.
        with _get_engine().begin() as conn:
            _sync_state_set(conn, "activity_seen_at", _utc_now())
    return fresh[-limit:] if limit else fresh


def add_product(data: dict):
    data = _normalize_product_money(data)
    fields = {column.name for column in Product.__table__.columns}
    try:
        with session_scope() as session:
            if not data.get("section_id"):
                data["section_id"] = _default_product_section_id(session)
            product = Product(**{k: v for k, v in data.items() if k in fields and k != "id"})
            session.add(product)
            session.flush()
            # Opening balance. Without it the ledger has no starting point and
            # `stock` can never be recomputed from `stock_movements`.
            opening = int(product.stock or 0)
            if opening:
                session.add(StockMovement(
                    product_id=product.id,
                    type="boshlangich",
                    quantity=opening,
                    note="Mahsulot qo'shildi",
                ))
            p_id = product.id
            p_name = product.name
            p_barcode = product.barcode or "-"
            p_price = float(product.price or 0)
            p_stock = product.stock or 0
            p_unit = product.unit or "dona"
        log_activity(
            "product_added",
            f"Yangi mahsulot: {p_name}",
            f"Shtrix-kod: {p_barcode} | Narxi: {p_price:,.0f} UZS | Qoldiq: {p_stock} {p_unit}",
            level="success",
            target="products",
            badge="Qo'shildi",
        )
        return p_id
    except IntegrityError as exc:
        raise AppError("Bu shtrix-kod allaqachon mavjud.") from exc


def update_product(product_id, data: dict):
    data = _normalize_product_money(data)
    try:
        with session_scope() as session:
            product = session.get(Product, product_id)
            if not product:
                return
            # A stock edit is an inventory correction, not a plain field write:
            # route it through the ledger so the change is explainable later.
            requested_stock = data.pop("stock", None)
            for key, value in data.items():
                if hasattr(product, key) and key != "id":
                    setattr(product, key, value)
            session.flush()
            if requested_stock is not None:
                delta = int(requested_stock) - int(product.stock or 0)
                if delta:
                    _apply_stock_delta(
                        session, product.id, delta,
                        movement_type="korrektirovka",
                        note="Mahsulot tahrirlashda qo'lda tuzatildi",
                    )
                    session.refresh(product)
            p_name = product.name
            p_barcode = product.barcode or "-"
            p_price = float(product.price or 0)
            p_stock = product.stock or 0
            p_unit = product.unit or "dona"
        log_activity(
            "product_updated",
            f"Mahsulot yangilandi: {p_name}",
            f"Shtrix-kod: {p_barcode} | Narxi: {p_price:,.0f} UZS | Qoldiq: {p_stock} {p_unit}",
            level="info",
            target="products",
            badge="Yangilandi",
        )
    except IntegrityError as exc:
        raise AppError("Bu shtrix-kod allaqachon mavjud.") from exc


def _apply_stock_delta(session, product_id, delta, movement_type, note=""):
    """Move stock and log the movement, always together.

    Every path that changes `products.stock` goes through here. A stock column
    that moves without a matching `stock_movements` row is a discrepancy nobody
    can explain afterwards, and it makes the balance impossible to recompute
    from the ledger.
    """
    delta = int(delta or 0)
    if delta == 0 or not product_id:
        return 0
    session.execute(
        update(Product)
        .where(Product.id == product_id)
        .values(stock=func.coalesce(Product.stock, 0) + delta)
    )
    # The bulk UPDATE above is invisible to the ORM flush listeners, so the row
    # has to be queued for sync by hand - same as create_sale does.
    session.info.setdefault("sync_outbox_entries", set()).add(
        ("products", str(product_id), "upsert")
    )
    session.add(StockMovement(
        product_id=product_id,
        type=movement_type,
        quantity=delta,
        note=note or None,
    ))
    return delta


def _restore_product_stock(session, product_id, quantity, movement_type, note=""):
    """Give stock back after a sale line is cancelled or returned."""
    quantity = int(quantity or 0)
    if quantity <= 0:
        return 0
    return _apply_stock_delta(session, product_id, quantity, movement_type, note)


def _cleanup_unfinalized_sales_for_product(session, product_id):
    items = session.execute(
        select(SaleItem)
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(SaleItem.product_id == product_id, func.coalesce(Sale.is_finalized, 0) == 0)
    ).scalars().all()
    affected_sale_ids = set()
    for item in items:
        affected_sale_ids.add(item.sale_id)
        # create_sale already took this quantity out of stock and logged a
        # negative "sotuv" movement. Deleting the item without putting it back
        # leaks inventory permanently and leaves an orphaned movement whose
        # parent no longer exists, so post the reversal here.
        outstanding = max(0, (item.quantity or 0) - (item.returned_quantity or 0))
        if outstanding and item.product_id:
            _restore_product_stock(
                session,
                item.product_id,
                outstanding,
                movement_type="bekor",
                note=f"Yakunlanmagan sotuv bekor qilindi (#{_sale_label(session, item.sale_id)})",
            )
        session.merge(SyncTombstone(
            table_name="sale_items",
            local_id=str(item.id),
            deleted_at=_utc_now(),
        ))
        _detach_sale_item_returns(session, item.id)
        session.delete(item)
    session.flush()
    for sale_id in affected_sale_ids:
        remaining_count = session.scalar(
            select(func.count(SaleItem.id)).where(SaleItem.sale_id == sale_id)
        ) or 0
        sale = session.get(Sale, sale_id)
        if sale:
            if remaining_count == 0:
                session.merge(SyncTombstone(
                    table_name="sales",
                    local_id=str(sale.id),
                    deleted_at=_utc_now(),
                ))
                _release_sale_ledger_rows(session, sale.id)
                session.delete(sale)
            else:
                new_total = session.scalar(
                    select(func.coalesce(func.sum(SaleItem.price * SaleItem.quantity), 0))
                    .where(SaleItem.sale_id == sale_id)
                ) or 0
                sale.total = new_total


def delete_product(product_id):
    p_name = None
    p_barcode = None
    with session_scope() as session:
        product = session.get(Product, product_id)
        if product:
            p_name = product.name
            p_barcode = product.original_barcode or product.barcode
            product.is_deleted = 1
            product.deleted_at = _trash_now()
            product.purge_after = _trash_purge_after()
            if product.barcode:
                product.original_barcode = product.original_barcode or product.barcode
                product.barcode = _unique_deleted_label("barcode", product.id, product.barcode)
            _cleanup_unfinalized_sales_for_product(session, product.id)
    if p_name:
        log_activity(
            "product_deleted",
            f"Mahsulot o'chirildi: {p_name}",
            f"Shtrix-kod: {p_barcode or '-'}",
            level="danger",
            target="products",
            badge="O'chirildi",
        )


def set_product_process_status(product_id, status):
    if status not in ("available", "process"):
        raise AppError("Mahsulot holati noto'g'ri.")
    with session_scope() as session:
        product = session.get(Product, product_id)
        if product:
            product.process_status = status


def put_product_in_process(product_id, quantity, deposit_amount=0, deposit_currency="UZS", customer_name=None, customer_phone=None, cashier_name=None):
    if quantity <= 0:
        raise AppError("Jarayonga o'tkazish miqdori 0 dan katta bo'lishi kerak.")
    with session_scope() as session:
        product = session.get(Product, product_id)
        if not product or product.is_deleted:
            raise AppError("Mahsulot topilmadi.")
        available = (product.stock or 0) - (product.process_quantity or 0)
        if quantity > available:
            raise AppError(f"Bor qoldiqdan ko'p kiritildi. Mavjud: {available}.")
        product.process_status = "process"
        product.process_quantity = (product.process_quantity or 0) + quantity
        product.process_deposit = (product.process_deposit or 0) + deposit_amount
        product.process_deposit_currency = deposit_currency
        product.process_customer_name = customer_name
        product.process_customer_phone = customer_phone
        product.process_cashier_name = cashier_name
        p_name = product.name
        p_unit = product.unit or "dona"
    log_activity(
        "product_in_process",
        f"Jarayonga o'tkazildi: {p_name}",
        f"Miqdor: {quantity} {p_unit} | Zakalad: {deposit_amount:,.0f} {deposit_currency} | Mijoz: {customer_name or '-'}",
        level="info",
        target="products",
        badge="Jarayonda",
    )


def update_product_process(product_id, quantity, deposit_amount=0, deposit_currency="UZS", customer_name=None, customer_phone=None, cashier_name=None):
    if quantity <= 0:
        raise AppError("Jarayondagi miqdor 0 dan katta bo'lishi kerak.")
    with session_scope() as session:
        product = session.get(Product, product_id)
        if not product or product.is_deleted:
            raise AppError("Mahsulot topilmadi.")
        if quantity > (product.stock or 0):
            raise AppError(f"Umumiy qoldiqdan ko'p kiritildi. Mavjud: {product.stock or 0}.")
        product.process_status = "process"
        product.process_quantity = quantity
        product.process_deposit = deposit_amount
        product.process_deposit_currency = deposit_currency
        product.process_customer_name = customer_name
        product.process_customer_phone = customer_phone
        if cashier_name is not None:
            product.process_cashier_name = cashier_name
        p_name = product.name
        p_unit = product.unit or "dona"
    log_activity(
        "product_process_updated",
        f"Jarayon yangilandi: {p_name}",
        f"Miqdor: {quantity} {p_unit} | Zakalad: {deposit_amount:,.0f} {deposit_currency} | Mijoz: {customer_name or '-'}",
        level="info",
        target="products",
        badge="Jarayonda",
    )


def clear_product_process(product_id):
    p_name = None
    with session_scope() as session:
        product = session.get(Product, product_id)
        if product:
            p_name = product.name
            product.process_status = "available"
            product.process_quantity = 0
            product.process_deposit = 0
            product.process_deposit_currency = "UZS"
            product.process_customer_name = None
            product.process_customer_phone = None
            product.process_cashier_name = None
            _cleanup_unfinalized_sales_for_product(session, product.id)
    if p_name:
        log_activity(
            "product_process_cleared",
            f"Jarayon bekor qilindi: {p_name}",
            "Mahsulot jarayondan chiqarildi",
            level="warning",
            target="products",
            badge="Jarayon",
        )


def reduce_product_process(product_id, quantity):
    p_name = None
    p_unit = "dona"
    with session_scope() as session:
        product = session.get(Product, product_id)
        if not product:
            raise AppError("Mahsulot topilmadi.")
        current_qty = product.process_quantity or 0
        if quantity > current_qty:
            raise AppError(f"Jarayondagi miqdordan ko'p. Jarayonda: {current_qty}.")
        remaining_qty = current_qty - quantity
        product.process_quantity = remaining_qty
        product.process_deposit = ((product.process_deposit or 0) * remaining_qty / current_qty) if current_qty and remaining_qty else 0
        product.process_status = "process" if remaining_qty > 0 else "available"
        if remaining_qty <= 0:
            product.process_deposit_currency = "UZS"
            product.process_customer_name = None
            product.process_customer_phone = None
            product.process_cashier_name = None
            _cleanup_unfinalized_sales_for_product(session, product.id)
        p_name = product.name
        p_unit = product.unit or "dona"
    if p_name:
        log_activity(
            "product_process_reduced",
            f"Jarayondan kamaytirildi: {p_name}",
            f"Kamaytirilgan miqdor: {quantity} {p_unit}",
            level="info",
            target="products",
            badge="Jarayon",
        )


def get_categories():
    with session_scope() as session:
        return _rows_from_models(session.scalars(select(Category).order_by(Category.name)).all())


def add_category(name):
    if not name.strip():
        raise AppError("Kategoriya nomini kiriting.")
    with session_scope() as session:
        try:
            row = Category(name=name.strip())
            session.add(row)
            session.flush()
            return row.id
        except IntegrityError as exc:
            raise AppError("Bu kategoriya allaqachon mavjud.") from exc


def update_category(category_id, name):
    if not name.strip():
        raise AppError("Kategoriya nomini kiriting.")
    with session_scope() as session:
        try:
            row = session.get(Category, category_id)
            if row:
                row.name = name.strip()
                session.flush()
        except IntegrityError as exc:
            raise AppError("Bu kategoriya allaqachon mavjud.") from exc


def delete_category(category_id):
    with session_scope() as session:
        for product in session.scalars(select(Product).where(Product.category_id == category_id)):
            product.category_id = None
        row = session.get(Category, category_id)
        if row:
            session.delete(row)


def get_templates(section_id=None):
    with session_scope() as session:
        stmt = select(ProductTemplate).where(func.coalesce(ProductTemplate.is_deleted, 0) == 0)
        if section_id is not None:
            stmt = stmt.where(ProductTemplate.section_id == section_id)
        return _rows_from_models(session.scalars(stmt.order_by(ProductTemplate.name)).all())


def ensure_product_template_for_section(section_id):
    """Return a section template, cloning a default when the section is empty."""
    if not section_id:
        raise AppError("Mahsulot bo'limi tanlanmagan.")

    with session_scope() as session:
        section = session.scalar(
            select(ProductSection).where(
                ProductSection.id == section_id,
                func.coalesce(ProductSection.is_deleted, 0) == 0,
            )
        )
        if not section:
            raise AppError("Mahsulot bo'limi topilmadi.")

        existing = session.scalar(
            select(ProductTemplate)
            .where(
                ProductTemplate.section_id == section_id,
                func.coalesce(ProductTemplate.is_deleted, 0) == 0,
            )
            .order_by(func.coalesce(ProductTemplate.created_at, ""), ProductTemplate.name)
        )
        if existing:
            return existing.id

        source = session.scalar(
            select(ProductTemplate)
            .where(func.coalesce(ProductTemplate.is_deleted, 0) == 0)
            .order_by(func.coalesce(ProductTemplate.created_at, ""), ProductTemplate.name)
        )
        template = ProductTemplate(
            section_id=section_id,
            name=source.name if source else "Umumiy mahsulot",
        )
        session.add(template)
        session.flush()

        if source:
            source_fields = session.scalars(
                select(ProductTemplateField)
                .where(ProductTemplateField.template_id == source.id)
                .order_by(ProductTemplateField.sort_order, ProductTemplateField.id)
            ).all()
            for field in source_fields:
                session.add(ProductTemplateField(
                    template_id=template.id,
                    name=field.name,
                    field_type=field.field_type,
                    required=field.required,
                    sort_order=field.sort_order,
                ))
        return template.id


def get_template_fields(template_id):
    if not template_id:
        return []
    with session_scope() as session:
        rows = session.scalars(
            select(ProductTemplateField)
            .where(ProductTemplateField.template_id == template_id)
            .order_by(ProductTemplateField.sort_order, ProductTemplateField.id)
        ).all()
        return _rows_from_models(rows)


def add_template(name, fields, section_id=None):
    if not name.strip():
        raise AppError("Template nomini kiriting.")
    try:
        with session_scope() as session:
            if section_id is None:
                section_id = _default_product_section_id(session)
            template = ProductTemplate(name=name.strip(), section_id=section_id)
            session.add(template)
            session.flush()
            for order, field in enumerate(fields):
                session.add(ProductTemplateField(
                    template_id=template.id,
                    name=field["name"],
                    field_type=field.get("field_type", "text"),
                    required=int(field.get("required", False)),
                    sort_order=order,
                ))
            return template.id
    except IntegrityError as exc:
        raise AppError("Bu template nomi allaqachon mavjud.") from exc


def update_template(template_id, name, fields):
    if not name.strip():
        raise AppError("Template nomini kiriting.")
    try:
        with session_scope() as session:
            template = session.get(ProductTemplate, template_id)
            if template:
                template.name = name.strip()
            existing = session.scalars(select(ProductTemplateField).where(ProductTemplateField.template_id == template_id)).all()
            existing_by_name = {row.name.lower(): row for row in existing}
            kept_ids = []
            for order, field in enumerate(fields):
                row = existing_by_name.get(field["name"].lower())
                if row is None:
                    row = ProductTemplateField(template_id=template_id, name=field["name"])
                    session.add(row)
                    session.flush()
                row.field_type = field.get("field_type", "text")
                row.required = int(field.get("required", False))
                row.sort_order = order
                kept_ids.append(row.id)
            for row in existing:
                if row.id not in kept_ids:
                    session.delete(row)
    except IntegrityError as exc:
        raise AppError("Bu template nomi allaqachon mavjud.") from exc


def delete_template(template_id):
    with session_scope() as session:
        in_use = session.scalar(
            select(func.count(Product.id)).where(
                Product.template_id == template_id,
                func.coalesce(Product.is_deleted, 0) == 0,
            )
        )
        if in_use:
            raise AppError("Bu template mahsulotlarda ishlatilgan, uni o'chirib bo'lmaydi.")
        row = session.get(ProductTemplate, template_id)
        if row:
            row.original_name = row.original_name or row.name
            row.name = _unique_deleted_label("template", row.id, row.name)
            row.is_deleted = 1
            row.deleted_at = _trash_now()
            row.purge_after = _trash_purge_after()


def get_product_attributes(product_id):
    with session_scope() as session:
        rows = session.execute(
            select(ProductAttribute.field_id, ProductAttribute.value)
            .join(ProductTemplateField, ProductTemplateField.id == ProductAttribute.field_id)
            .where(ProductAttribute.product_id == product_id)
            .order_by(ProductTemplateField.sort_order, ProductTemplateField.id)
        ).all()
        return {field_id: value for field_id, value in rows}


def get_product_attribute_details(product_id):
    with session_scope() as session:
        rows = session.execute(
            select(ProductTemplateField.name, ProductAttribute.value)
            .join(ProductAttribute, ProductAttribute.field_id == ProductTemplateField.id)
            .where(ProductAttribute.product_id == product_id)
            .order_by(ProductTemplateField.sort_order, ProductTemplateField.id)
        ).all()
        return [Row({"name": name, "value": value}) for name, value in rows]


def save_product_attributes(product_id, attributes):
    with session_scope() as session:
        existing = {
            row.field_id: row
            for row in session.scalars(select(ProductAttribute).where(ProductAttribute.product_id == product_id))
        }
        keep_ids = set()
        for field_id, value in attributes.items():
            if value is None or str(value).strip() == "":
                row = existing.get(field_id)
                if row:
                    session.delete(row)
                continue
            row = existing.get(field_id)
            if row:
                row.value = str(value).strip()
            else:
                row = ProductAttribute(product_id=product_id, field_id=field_id, value=str(value).strip())
                session.add(row)
            keep_ids.add(field_id)
        for field_id, row in existing.items():
            if field_id not in keep_ids and field_id not in attributes:
                session.delete(row)


def get_currencies():
    with session_scope() as session:
        return _rows_from_models(session.scalars(select(Currency).order_by(Currency.is_base.desc(), Currency.code)).all())


def get_currency(code):
    with session_scope() as session:
        return _row_from_model(session.scalar(select(Currency).where(Currency.code == code)))


def save_currency(code, name, rate_to_uzs):
    code = code.strip().upper()
    name = name.strip()
    if not code or not name:
        raise AppError("Valyuta kodi va nomini kiriting.")
    if rate_to_uzs <= 0:
        raise AppError("Kurs 0 dan katta bo'lishi kerak.")
    with session_scope() as session:
        row = session.scalar(select(Currency).where(Currency.code == code)) or Currency(code=code)
        row.name = name
        row.rate_to_uzs = rate_to_uzs
        row.is_base = 1 if code == "UZS" else 0
        row.updated_at = _now()
        session.add(row)
        for product in session.scalars(select(Product).where(Product.price_currency == code)):
            product.price_exchange_rate = rate_to_uzs
            product.price = (product.price_original or 0) * rate_to_uzs
        for product in session.scalars(select(Product).where(Product.cost_currency == code)):
            product.cost_exchange_rate = rate_to_uzs
            product.cost = (product.cost_original or 0) * rate_to_uzs


def delete_currency(code):
    if code == "UZS":
        raise AppError("Asosiy valyutani o'chirib bo'lmaydi.")
    with session_scope() as session:
        row = session.scalar(select(Currency).where(Currency.code == code))
        if row:
            session.delete(row)


def add_stock(product_id, quantity, note=""):
    if quantity <= 0:
        raise AppError("Miqdor 0 dan katta bo'lishi kerak.")
    with session_scope() as session:
        product = session.get(Product, product_id)
        if not product or product.is_deleted:
            raise AppError("Mahsulot topilmadi.")
        product.stock = (product.stock or 0) + quantity
        p_name = product.name
        p_unit = product.unit or "dona"
        p_stock = product.stock
        session.add(StockMovement(product_id=product_id, type="kirim", quantity=quantity, note=note))
    log_activity(
        "stock_added",
        f"Omborga kirim: {p_name}",
        f"+{quantity} {p_unit} kiritildi | Jami qoldiq: {p_stock} | Izoh: {note or '-'}",
        level="success",
        target="products",
        badge="Kirim",
    )


def get_active_inventory_check():
    with session_scope() as session:
        row = session.execute(
            select(InventoryCheckSession, User.username)
            .outerjoin(User, User.id == InventoryCheckSession.started_by)
            .where(InventoryCheckSession.status == "active")
            .order_by(InventoryCheckSession.started_at.desc(), InventoryCheckSession.id.desc())
            .limit(1)
        ).first()
        return _row_from_model(row[0], started_by_name=row[1]) if row else None


def start_inventory_check(user_id=None):
    with session_scope() as session:
        active = session.scalar(select(InventoryCheckSession.id).where(InventoryCheckSession.status == "active").limit(1))
        if active:
            raise AppError("Oldin boshlangan checking jarayoni bor. Avval uni tugating.")
        check = InventoryCheckSession(started_by=user_id, status="active")
        session.add(check)
        session.flush()
        products = session.scalars(
            select(Product)
            .where(and_(func.coalesce(Product.is_deleted, 0) == 0, func.coalesce(Product.stock, 0) > 0))
            .order_by(Product.name)
        ).all()
        for product in products:
            session.add(InventoryCheckItem(
                session_id=check.id,
                product_id=product.id,
                product_name=product.name,
                barcode=product.barcode,
                expected_stock=product.stock or 0,
            ))
        p_count = len(products)
        c_id = check.id
    log_activity(
        "checking_started",
        "Tekshiruv (Checking) boshlandi",
        f"Jami {p_count} ta mahsulot tekshiruvga olindi",
        level="warning",
        target="checking",
        badge="Checking",
    )
    return c_id


def _apply_inventory_product_filters(stmt, section_id=None, template_id=None):
    if section_id is not None or template_id is not None:
        stmt = stmt.join(Product, Product.id == InventoryCheckItem.product_id)
    if section_id is not None:
        stmt = stmt.where(Product.section_id == section_id)
    if template_id is not None:
        stmt = stmt.where(Product.template_id == template_id)
    return stmt


def get_inventory_check_items(session_id, checked=None, section_id=None, template_id=None):
    stmt = select(InventoryCheckItem).where(InventoryCheckItem.session_id == session_id)
    stmt = _apply_inventory_product_filters(stmt, section_id, template_id)
    if checked is True:
        stmt = stmt.where(func.coalesce(InventoryCheckItem.checked_quantity, 0) > 0)
    elif checked is False:
        stmt = stmt.where(func.coalesce(InventoryCheckItem.checked_quantity, 0) < func.coalesce(InventoryCheckItem.expected_stock, 0))
    with session_scope() as session:
        rows = session.scalars(
            stmt.order_by(case((InventoryCheckItem.checked_at.is_(None), 0), else_=1), InventoryCheckItem.product_name)
        ).all()
        return _rows_from_models(rows)


def get_inventory_check_counts(session_id, section_id=None, template_id=None):
    with session_scope() as session:
        stmt = select(InventoryCheckItem).where(InventoryCheckItem.session_id == session_id)
        stmt = _apply_inventory_product_filters(stmt, section_id, template_id)
        items = session.scalars(stmt).all()
        total = len(items)
        checked_count = sum(1 for item in items if item.checked_at)
        unchecked_count = sum(1 for item in items if (item.checked_quantity or 0) < (item.expected_stock or 0))
        total_quantity = sum(item.expected_stock or 0 for item in items)
        checked_quantity = sum(item.checked_quantity or 0 for item in items)
        unchecked_quantity = sum(max((item.expected_stock or 0) - (item.checked_quantity or 0), 0) for item in items)
        return Row(dict(
            total=total,
            checked_count=checked_count,
            unchecked_count=unchecked_count,
            total_quantity=total_quantity,
            checked_quantity=checked_quantity,
            unchecked_quantity=unchecked_quantity,
        ))


def mark_inventory_product_checked(session_id, barcode, quantity=1, section_id=None, template_id=None):
    barcode = (barcode or "").strip()
    if not barcode:
        raise AppError("Shtrix-kodni kiriting.")
    if quantity <= 0:
        raise AppError("Miqdor 0 dan katta bo'lishi kerak.")
    with session_scope() as session:
        check = session.get(InventoryCheckSession, session_id)
        if not check or check.status != "active":
            raise AppError("Aktiv checking jarayoni topilmadi.")
        product = session.scalar(select(Product).where(and_(Product.barcode == barcode, func.coalesce(Product.is_deleted, 0) == 0)))
        if not product:
            raise AppError("Bu shtrix-kodli mahsulot topilmadi.")
        if section_id is not None and product.section_id != section_id:
            raise AppError("Bu mahsulot tanlangan bo'limga tegishli emas.")
        if template_id is not None and product.template_id != template_id:
            raise AppError("Bu mahsulot tanlangan templatega tegishli emas.")
        item = session.scalar(
            select(InventoryCheckItem).where(and_(InventoryCheckItem.session_id == session_id, InventoryCheckItem.product_id == product.id))
        )
        if not item:
            raise AppError("Bu mahsulot checking ro'yxatida yo'q.")
        if item.checked_at:
            raise AppError("Bu mahsulot allaqachon tekshiruvdan o'tgan.")
        current_quantity = item.checked_quantity or 0
        expected_stock = item.expected_stock or 0
        new_quantity = current_quantity + quantity
        if new_quantity > expected_stock:
            raise AppError(f"Kiritilgan miqdor qoldiqdan oshib ketdi. Qolgan: {expected_stock - current_quantity}.")
        item.checked_quantity = new_quantity
        if new_quantity == expected_stock:
            item.checked_at = _now()
        session.flush()
        return _row_from_model(item)


def get_inventory_check_discrepancies(session_id):
    """Products counted short during a stocktake, with the missing quantity.

    Read-only: it reports what a correction *would* change, so the operator can
    see the write-off before agreeing to it.
    """
    with session_scope() as session:
        rows = session.execute(
            select(InventoryCheckItem, Product.name, Product.unit)
            .outerjoin(Product, Product.id == InventoryCheckItem.product_id)
            .where(InventoryCheckItem.session_id == session_id)
        ).all()
        result = []
        for item, product_name, unit in rows:
            expected = int(item.expected_stock or 0)
            counted = int(item.checked_quantity or 0)
            # Only items somebody actually scanned. An untouched row means "not
            # counted", not "counted as zero" - writing those off would destroy
            # the stock of every product the operator never reached.
            if not item.checked_at and counted == 0:
                continue
            if counted == expected:
                continue
            result.append(Row(dict(
                product_id=item.product_id,
                product_name=product_name or "-",
                unit=unit or "dona",
                expected_stock=expected,
                counted_quantity=counted,
                delta=counted - expected,
            )))
        return result


def finish_inventory_check(session_id, apply_corrections=False):
    """Close a stocktake, optionally writing the counted quantities into stock.

    ``apply_corrections`` defaults to False: a stocktake that silently wrote
    inventory off would destroy stock value without anyone agreeing to it. When
    it is on, every adjustment goes through the ledger like any other movement,
    so the write-off is explainable afterwards.
    """
    counts = get_inventory_check_counts(session_id)
    discrepancies = get_inventory_check_discrepancies(session_id) if apply_corrections else []
    corrected = 0
    with session_scope() as session:
        check = session.get(InventoryCheckSession, session_id)
        if not check or check.status != "active":
            raise AppError("Aktiv checking jarayoni topilmadi.")
        for row in discrepancies:
            if _apply_stock_delta(
                session,
                row["product_id"],
                row["delta"],
                movement_type="inventarizatsiya",
                note=f"Tekshiruv #{session_id}: {row['expected_stock']} -> {row['counted_quantity']}",
            ):
                corrected += 1
        check.status = "finished"
        check.finished_at = _now()
    detail = f"Tekshirildi: {counts.get('checked_count', 0)} ta | Qolgan: {counts.get('unchecked_count', 0)} ta"
    if corrected:
        detail += f" | Qoldiq tuzatildi: {corrected} ta"
    log_activity(
        "checking_finished",
        "Tekshiruv (Checking) yakunlandi",
        detail,
        level="success",
        target="checking",
        badge="Tugallandi",
    )
    counts = dict(counts)
    counts["corrected_count"] = corrected
    return Row(counts)


def _sale_label(session, sale_id):
    """How a sale is named in notes and messages.

    The identifier is a UUID and means nothing to a cashier, so anything a
    person reads uses the sale's display number instead.
    """
    sale = session.get(Sale, sale_id) if sale_id else None
    display_no = getattr(sale, "display_no", None)
    if display_no:
        return str(display_no)
    return str(sale_id or "")[:8]


def _next_sale_display_no(session):
    """The number the cashier sees on the receipt.

    Display only. Nothing ever looks a sale up by it, so two devices handing
    out the same number costs nothing but a repeated label -- unlike the
    primary key, which is why that one is a UUID.
    """
    highest = session.scalar(select(func.max(Sale.display_no)))
    try:
        return int(highest) + 1
    except (TypeError, ValueError):
        return 1


def create_sale(customer_id, cashier_id, items, total, discount, paid, payment_method, currency_code="UZS", exchange_rate=1, paid_original=None, customer_name=None, customer_phone=None, is_finalized=0):
    require_online("sotuvni yakunlash")
    if not items:
        raise AppError("Savat bo'sh.")
    if discount < 0 or discount > total:
        raise AppError("Chegirma jami summadan oshmasligi kerak.")
    if payment_method == "qarz" and not customer_id:
        raise AppError("Qarz savdo uchun mijoz tanlang.")
    if exchange_rate <= 0:
        raise AppError("Valyuta kursi noto'g'ri.")
    with session_scope() as session:
        product_names = {}
        for item in items:
            product = session.get(Product, item["product_id"])
            if product is None or product.is_deleted:
                raise AppError("Savatdagi mahsulot topilmadi.")
            if item["quantity"] <= 0:
                raise AppError("Miqdor noto'g'ri kiritilgan.")
            product_names[product.id] = product.name
        payable = total - discount
        change = max(0, paid - payable)
        paid_original = paid_original if paid_original is not None else paid / exchange_rate
        change_original = change / exchange_rate
        if customer_id and (not customer_name or customer_phone is None):
            customer = session.get(Customer, customer_id)
            if customer:
                customer_name = customer_name or customer.name
                customer_phone = customer.phone if customer_phone is None else customer_phone
        user = session.get(User, cashier_id) if cashier_id else None
        if user and getattr(user, "role", "") == "admin":
            is_finalized = 1
        now_str = _utc_now()
        sale = Sale(
            display_no=_next_sale_display_no(session),
            customer_id=customer_id,
            cashier_id=cashier_id,
            customer_name=customer_name,
            customer_phone=customer_phone,
            total=total,
            discount=discount,
            paid=paid,
            change=change,
            currency_code=currency_code,
            exchange_rate=exchange_rate,
            paid_original=paid_original,
            change_original=change_original,
            payment_method=payment_method,
            is_finalized=1 if is_finalized else 0,
            finalized_at=now_str if is_finalized else None,
            original_total=total,
            original_discount=discount,
            original_paid=paid,
            original_cashier_reward=0.0,
            created_at=now_str,
        )
        session.add(sale)
        session.flush()
        for item in items:
            product_id = item["product_id"]
            quantity = item["quantity"]
            product = session.get(Product, product_id)
            result = session.execute(
                update(Product)
                .where(
                    Product.id == product_id,
                    func.coalesce(Product.is_deleted, 0) == 0,
                    (func.coalesce(Product.stock, 0) - func.coalesce(Product.process_quantity, 0)) >= quantity,
                )
                .values(stock=Product.stock - quantity)
            )
            if result.rowcount != 1:
                available = (product.stock or 0) - (product.process_quantity or 0)
                raise AppError(f"Mahsulot qoldig'i yetarli emas: {product_names.get(product_id, '')} (Mavjud: {available})")
            session.info.setdefault("sync_outbox_entries", set()).add(("products", str(product_id), "upsert"))
            sale_item = SaleItem(
                sale_id=sale.id,
                product_id=product_id,
                quantity=quantity,
                price=item["price"],
                subtotal=item["subtotal"],
                # Sealed here: profit must keep reading the cost of the day,
                # not whatever the product costs when the report is opened.
                cost_at_sale=float((product.cost if product else 0) or 0),
                created_at=now_str,
            )
            session.add(sale_item)
            session.add(StockMovement(product_id=product_id, type="sotuv", quantity=-quantity, note=f"Sotuv #{sale.display_no}"))
        if customer_id:
            customer = session.get(Customer, customer_id)
            if customer:
                customer.total_purchases = (customer.total_purchases or 0) + payable
            if payment_method == "qarz":
                _add_customer_debt_movement(
                    session,
                    customer_id,
                    payable,
                    "qarz",
                    sale_id=sale.id,
                    note=f"Sotuv #{sale.display_no}",
                )
        sale_id = sale.id
        sale_display_no = sale.display_no
    log_activity(
        "sale_created",
        f"Sotuv amalga oshirildi (#{sale_display_no})",
        f"{len(items)} xil mahsulot sotildi | Jami: {total:,.0f} {currency_code} ({payment_method})",
        level="success",
        target="sales",
        badge="Sotildi",
    )
    return sale_id


def recalculate_sale_totals(sale_id):
    """Rebuild a sale's cached figures from its sealed originals and returns.

    Every cached number in a sale is derivable, so it can always be rebuilt --
    after a download, after a repair, after anything that wrote the rows
    without going through the return path.
    """
    with session_scope() as session:
        sale = session.get(Sale, sale_id)
        if sale is None:
            return None
        for item in session.scalars(select(SaleItem).where(SaleItem.sale_id == sale_id)):
            _recalculate_sale_item(session, item)
        _recalculate_sale(session, sale)
        if sale.customer_id:
            _recalculate_customer_balance(session, sale.customer_id)
        return Row(dict(
            total=sale.total,
            discount=sale.discount,
            paid=sale.paid,
            cashier_reward=sale.cashier_reward,
        ))


def get_customer_debt_movements(customer_id):
    with session_scope() as session:
        return _rows_from_models(session.scalars(
            select(CustomerDebtMovement)
            .where(CustomerDebtMovement.customer_id == customer_id)
            .order_by(CustomerDebtMovement.created_at, CustomerDebtMovement.id)
        ).all())


def get_sale_returns(sale_id=None):
    with session_scope() as session:
        stmt = select(SaleReturn).order_by(SaleReturn.created_at, SaleReturn.id)
        if sale_id:
            stmt = stmt.where(SaleReturn.sale_id == sale_id)
        return _rows_from_models(session.scalars(stmt).all())


def get_sale_display_no(sale_id):
    """The number shown to a person for this sale, not its identifier."""
    with session_scope() as session:
        return _sale_label(session, sale_id)


def finalize_sale(sale_id, cashier_reward=0.0):
    require_online("sotuvni tasdiqlash")
    sale_ref = None
    with session_scope() as session:
        sale = session.get(Sale, sale_id)
        if not sale:
            raise AppError("Sotuv topilmadi.")
        if sale.is_finalized:
            return
        sale.is_finalized = 1
        # Sealed once. Returns are taken off the sealed figure in
        # _recalculate_sale, so the reward never drifts by repeated subtraction.
        sale.original_cashier_reward = float(cashier_reward or 0.0)
        sale.finalized_at = _utc_now()
        _recalculate_sale(session, sale)
        sale_ref = sale.display_no or str(sale.id)[:8]
    if sale_ref:
        log_activity(
            "sale_finalized",
            f"Sotuv yakunlandi: #{sale_ref}",
            "Sotuv tasdiqlandi va hisobotlarga qo'shildi",
            level="success",
            target="reports",
            badge="Yakunlandi",
        )


def finalize_all_pending_sales():
    now_str = _utc_now()
    with session_scope() as session:
        sales = session.scalars(select(Sale).where(func.coalesce(Sale.is_finalized, 0) == 0)).all()
        count = len(sales)
        for sale in sales:
            sale.is_finalized = 1
            sale.finalized_at = now_str
    if count > 0:
        log_activity(
            "sale_finalized",
            "Barcha sotuvlar yakunlandi",
            f"{count} ta kutilayotgan sotuv tasdiqlandi va hisobotlarga qo'shildi",
            level="success",
            target="reports",
            badge="Yakunlandi",
        )
    return count


def get_sales_today():
    return get_sales_by_date(datetime.now().strftime("%Y-%m-%d"))


def get_sale_items(sale_id):
    with session_scope() as session:
        rows = session.execute(
            select(SaleItem, Product.name)
            .join(Product, Product.id == SaleItem.product_id)
            .where(SaleItem.sale_id == sale_id)
        ).all()
        return [_row_from_model(item, product_name=name) for item, name in rows]


def get_product_sales_archive(query="", start_date=None, end_date=None, only_cashiers=False, only_pending=False):
    pattern = f"%{query.strip()}%"
    with session_scope() as session:
        stmt = (
            select(SaleItem, Sale, Product, func.coalesce(User.username, User.email), User.role, Customer.name, Customer.phone)
            .join(Sale, Sale.id == SaleItem.sale_id)
            .outerjoin(Product, Product.id == SaleItem.product_id)
            .outerjoin(User, User.id == Sale.cashier_id)
            .outerjoin(Customer, Customer.id == Sale.customer_id)
            .where(SaleItem.quantity > func.coalesce(SaleItem.returned_quantity, 0))
        )
        if only_cashiers:
            stmt = stmt.where(or_(User.role == "cashier", and_(User.role != "admin", User.id.isnot(None))))
        if only_pending:
            stmt = stmt.where(func.coalesce(Sale.is_finalized, 0) == 0)
        if query and query.strip():
            stmt = stmt.where(or_(
                Product.name.like(pattern),
                Product.barcode.like(pattern),
                User.username.like(pattern),
                func.coalesce(Sale.customer_name, Customer.name).like(pattern),
                func.coalesce(Sale.customer_phone, Customer.phone).like(pattern),
            ))
        if start_date and end_date:
            stmt = stmt.where(_date_expr(Sale.created_at).between(start_date, end_date))
        rows = session.execute(stmt.order_by(Sale.created_at.desc(), SaleItem.id.desc()).limit(1000)).all()
        result = []
        for item, sale, product, cashier_name, cashier_role, customer_name, customer_phone in rows:
            active_quantity = item.quantity - (item.returned_quantity or 0)
            active_subtotal = active_quantity * (item.price or 0)
            active_sale_total = session.scalar(
                select(func.coalesce(func.sum((SaleItem.quantity - func.coalesce(SaleItem.returned_quantity, 0)) * SaleItem.price), 0))
                .where(SaleItem.sale_id == sale.id)
            ) or 0
            item_discount = (sale.discount or 0) * (active_subtotal / active_sale_total) if active_sale_total > 0 else 0
            item_total_after_discount = max(0, active_subtotal - item_discount)
            cost_val = _item_cost(item, product)
            price_val = item.price or 0
            sold_unit_price = (item_total_after_discount / active_quantity) if active_quantity > 0 else price_val
            result.append(Row(dict(
                sale_item_id=item.id,
                sale_id=item.sale_id,
                sale_display_no=sale.display_no,
                product_id=item.product_id,
                product_name=product.name if product else "-",
                barcode=(product.barcode if product else None) or "-",
                section_id=product.section_id if product else None,
                template_id=product.template_id if product else None,
                supplier_id=product.supplier_id if product else None,
                quantity=item.quantity,
                returned_quantity=item.returned_quantity or 0,
                cost=cost_val,
                price=price_val,
                sold_unit_price=sold_unit_price,
                subtotal=item.subtotal,
                active_subtotal=active_subtotal,
                discount=sale.discount,
                item_discount=item_discount,
                item_total_after_discount=item_total_after_discount,
                payment_method=sale.payment_method,
                currency_code=sale.currency_code,
                exchange_rate=sale.exchange_rate,
                cashier_reward=sale.cashier_reward or 0.0,
                is_finalized=sale.is_finalized or 0,
                finalized_at=_utc_to_local(sale.finalized_at) if sale.finalized_at else None,
                created_at=_utc_to_local(sale.created_at),
                cashier_id=sale.cashier_id,
                cashier_name=cashier_name,
                cashier_role=cashier_role or "",
                customer_name=sale.customer_name or customer_name,
                customer_phone=sale.customer_phone or customer_phone,
            )))
        return result


def get_finance_rows(start_date, end_date):
    with session_scope() as session:
        templates = session.scalars(select(ProductTemplate).order_by(ProductTemplate.name)).all()
        products = session.scalars(select(Product).where(Product.is_deleted == 0).order_by(Product.name)).all()
        first_date = _first_activity_date_in_session(session)
        labels = []
        current = datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.strptime(end_date, "%Y-%m-%d").date()
        if first_date:
            first = datetime.strptime(first_date, "%Y-%m-%d").date()
            if end < first:
                return Row(dict(
                    templates=[Row(dict(id=template.id, name=template.name)) for template in templates],
                    rows=[],
                ))
            current = max(current, first)
        while current <= end:
            labels.append(current.isoformat())
            current = current + timedelta(days=1)

        if not labels:
            return Row(dict(
                templates=[Row(dict(id=template.id, name=template.name)) for template in templates],
                rows=[],
            ))

        product_ids = [product.id for product in products]
        movements_by_product = {product.id: {} for product in products}
        activity_labels = set()
        if product_ids:
            movement_rows = session.execute(
                select(
                    StockMovement.product_id,
                    _date_expr(StockMovement.created_at).label("label"),
                    func.coalesce(func.sum(StockMovement.quantity), 0).label("quantity"),
                )
                .where(
                    StockMovement.product_id.in_(product_ids),
                    _date_expr(StockMovement.created_at) > labels[0],
                )
                .group_by(StockMovement.product_id, _date_expr(StockMovement.created_at))
            ).all()
            for product_id, label, quantity in movement_rows:
                movements_by_product.setdefault(product_id, {})[label] = quantity or 0
                if labels[0] <= label <= labels[-1]:
                    activity_labels.add(label)

        future_by_product = {}
        if product_ids:
            future_rows = session.execute(
                select(
                    StockMovement.product_id,
                    func.coalesce(func.sum(StockMovement.quantity), 0).label("quantity"),
                )
                .where(
                    StockMovement.product_id.in_(product_ids),
                    _date_expr(StockMovement.created_at) > labels[-1],
                )
                .group_by(StockMovement.product_id)
            ).all()
            future_by_product = {product_id: quantity or 0 for product_id, quantity in future_rows}

        supplier_debt_rows = session.execute(
            select(
                _date_expr(SupplierDebtMovement.created_at).label("label"),
                SupplierDebtMovement.type,
                func.coalesce(func.sum(SupplierDebtMovement.amount * func.coalesce(Currency.rate_to_uzs, 1)), 0).label("amount"),
            )
            .join(Supplier, Supplier.id == SupplierDebtMovement.supplier_id)
            .outerjoin(Currency, Currency.code == Supplier.debt_currency)
            .where(_date_expr(SupplierDebtMovement.created_at).between(labels[0], labels[-1]))
            .group_by(_date_expr(SupplierDebtMovement.created_at), SupplierDebtMovement.type)
        ).all()
        debtor_debt_rows = session.execute(
            select(
                _date_expr(DebtorDebtMovement.created_at).label("label"),
                DebtorDebtMovement.type,
                func.coalesce(func.sum(DebtorDebtMovement.amount * func.coalesce(Currency.rate_to_uzs, 1)), 0).label("amount"),
            )
            .join(Debtor, Debtor.id == DebtorDebtMovement.debtor_id)
            .outerjoin(Currency, Currency.code == Debtor.debt_currency)
            .where(_date_expr(DebtorDebtMovement.created_at).between(labels[0], labels[-1]))
            .group_by(_date_expr(DebtorDebtMovement.created_at), DebtorDebtMovement.type)
        ).all()
        supplier_opening_rows = session.execute(
            select(
                SupplierDebtMovement.type,
                func.coalesce(func.sum(SupplierDebtMovement.amount * func.coalesce(Currency.rate_to_uzs, 1)), 0).label("amount"),
            )
            .join(Supplier, Supplier.id == SupplierDebtMovement.supplier_id)
            .outerjoin(Currency, Currency.code == Supplier.debt_currency)
            .where(_date_expr(SupplierDebtMovement.created_at) < labels[0])
            .group_by(SupplierDebtMovement.type)
        ).all()
        debtor_opening_rows = session.execute(
            select(
                DebtorDebtMovement.type,
                func.coalesce(func.sum(DebtorDebtMovement.amount * func.coalesce(Currency.rate_to_uzs, 1)), 0).label("amount"),
            )
            .join(Debtor, Debtor.id == DebtorDebtMovement.debtor_id)
            .outerjoin(Currency, Currency.code == Debtor.debt_currency)
            .where(_date_expr(DebtorDebtMovement.created_at) < labels[0])
            .group_by(DebtorDebtMovement.type)
        ).all()
        debt_delta_by_label = {}
        opening_debt = 0
        for movement_type, amount in supplier_opening_rows:
            opening_debt += amount if movement_type == "qarz" else -amount
        for movement_type, amount in debtor_opening_rows:
            change = amount if movement_type == "qarz" else -amount
            opening_debt -= change
        for label, movement_type, amount in supplier_debt_rows:
            change = amount if movement_type == "qarz" else -amount
            debt_delta_by_label[label] = debt_delta_by_label.get(label, 0) + change
            activity_labels.add(label)
        for label, movement_type, amount in debtor_debt_rows:
            change = amount if movement_type == "qarz" else -amount
            debt_delta_by_label[label] = debt_delta_by_label.get(label, 0) - change
            activity_labels.add(label)
        debt_by_label = {}
        running_debt = opening_debt
        for label in labels:
            running_debt += debt_delta_by_label.get(label, 0)
            debt_by_label[label] = running_debt

        rows_by_label = {}
        for label in reversed(labels):
            row = {
                "label": label,
                "active": 1 if label in activity_labels else 0,
                "cash": 0,
                "card": 0,
                "other": 0,
                "debt": debt_by_label.get(label, 0),
                "total": 0,
                "templates": {template.id: 0 for template in templates},
            }
            for product in products:
                created_label = _local_date_label(product.created_at)
                if created_label and created_label > label:
                    continue
                if created_label == label:
                    row["active"] = 1
                future_quantity = future_by_product.get(product.id, 0)
                day_stock = max((product.stock or 0) - future_quantity, 0)
                value = day_stock * (product.price or 0)
                if product.template_id in row["templates"]:
                    row["templates"][product.template_id] += value
                else:
                    row["other"] += value
            row["total"] = sum(row["templates"].values()) + row["other"]
            rows_by_label[label] = Row(row)
            for product_id, quantity in movements_by_product.items():
                future_by_product[product_id] = future_by_product.get(product_id, 0) + (quantity.get(label, 0) or 0)

        return Row(dict(
            templates=[Row(dict(id=template.id, name=template.name)) for template in templates],
            rows=[rows_by_label[label] for label in labels],
        ))


def add_finance_manual_movement(movement_date, kind, operation, amount, currency_code="UZS", rate_to_uzs=1):
    if kind not in ("cash", "card", "other"):
        raise AppError("Mablag' turi noto'g'ri.")
    if operation not in ("+", "-"):
        raise AppError("Amal noto'g'ri.")
    if amount <= 0:
        raise AppError("Summa 0 dan katta bo'lishi kerak.")
    with session_scope() as session:
        row = FinanceManualMovement(
            movement_date=movement_date,
            kind=kind,
            operation=operation,
            amount=amount,
            currency_code=currency_code or "UZS",
            rate_to_uzs=rate_to_uzs or 1,
        )
        session.add(row)
        session.flush()
        return row.id


def get_finance_manual_movements(start_date=None, end_date=None):
    stmt = select(FinanceManualMovement).order_by(FinanceManualMovement.movement_date, FinanceManualMovement.id)
    if start_date:
        stmt = stmt.where(FinanceManualMovement.movement_date >= start_date)
    if end_date:
        stmt = stmt.where(FinanceManualMovement.movement_date <= end_date)
    with session_scope() as session:
        return _rows_from_models(session.scalars(stmt).all())


def get_finance_manual_values(start_date=None, end_date=None):
    values = {}
    for row in get_finance_manual_movements(start_date, end_date):
        day_values = values.setdefault(row["movement_date"], {})
        movements = day_values.setdefault(row["kind"], [])
        movements.append({
            "date": row["movement_date"],
            "kind": row["kind"],
            "operation": row["operation"] or "+",
            "amount": row["amount"] or 0,
            "currency": row["currency_code"] or "UZS",
            "rate_to_uzs": row["rate_to_uzs"] or 1,
        })
    return values


def get_finance_manual_total(movement_date, kind):
    total = 0
    for day_values in get_finance_manual_values(movement_date, movement_date).values():
        for movement in day_values.get(kind, []):
            sign = -1 if movement.get("operation") == "-" else 1
            total += sign * (movement.get("amount", 0) or 0) * (movement.get("rate_to_uzs", 1) or 1)
    return total


def migrate_finance_manual_json(path="finance_manual.json"):
    if not os.path.exists(path):
        return False
    with session_scope() as session:
        if session.scalar(select(func.count(FinanceManualMovement.id))) > 0:
            try:
                os.replace(path, f"{path}.migrated")
            except OSError:
                pass
            return False
    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False

    migrated = 0
    with session_scope() as session:
        for movement_date, day_values in data.items():
            if not isinstance(day_values, dict):
                continue
            for kind, value in day_values.items():
                if kind not in ("cash", "card", "other"):
                    continue
                items = value if isinstance(value, list) else [value]
                for item in items:
                    if isinstance(item, dict):
                        amount = item.get("amount", 0) or 0
                        if amount <= 0:
                            continue
                        session.add(FinanceManualMovement(
                            movement_date=movement_date,
                            kind=kind,
                            operation=item.get("operation") or "+",
                            amount=amount,
                            currency_code=item.get("currency") or item.get("currency_code") or "UZS",
                            rate_to_uzs=item.get("rate_to_uzs") or 1,
                        ))
                        migrated += 1
                    else:
                        try:
                            amount = float(item or 0)
                        except (TypeError, ValueError):
                            amount = 0
                        if amount:
                            session.add(FinanceManualMovement(
                                movement_date=movement_date,
                                kind=kind,
                                operation="+" if amount > 0 else "-",
                                amount=abs(amount),
                                currency_code="UZS",
                                rate_to_uzs=1,
                            ))
                            migrated += 1
    if migrated:
        try:
            os.replace(path, f"{path}.migrated")
        except OSError:
            pass
    return bool(migrated)

def _first_activity_date_in_session(session):
    date_columns = [
        Product.created_at,
        StockMovement.created_at,
        Sale.created_at,
        Expense.created_at,
        FinanceManualMovement.movement_date,
        SupplierDebtMovement.created_at,
        DebtorDebtMovement.created_at,
        LoginLog.logged_at,
    ]
    dates = []
    for column in date_columns:
        value = session.scalar(select(func.min(_date_expr(column))))
        if value:
            dates.append(value)
    return min(dates) if dates else None


def get_first_activity_date():
    with session_scope() as session:
        return _first_activity_date_in_session(session)


def clear_sales_history():
    with session_scope() as session:
        deleted_at = _utc_now()
        for item_id in session.scalars(select(SaleItem.id)):
            session.merge(SyncTombstone(
                table_name="sale_items", local_id=str(item_id), deleted_at=deleted_at
            ))
        for sale_id in session.scalars(select(Sale.id)):
            session.merge(SyncTombstone(
                table_name="sales", local_id=str(sale_id), deleted_at=deleted_at
            ))
        for return_id in session.scalars(select(SaleReturn.id)):
            session.merge(SyncTombstone(
                table_name="sale_returns", local_id=str(return_id), deleted_at=deleted_at
            ))
        for movement in session.scalars(
            select(CustomerDebtMovement).where(CustomerDebtMovement.sale_id.is_not(None))
        ).all():
            movement.sale_id = None
        session.flush()
        session.query(SaleReturn).delete()
        session.query(SaleItem).delete()
        session.query(Sale).delete()


def _detach_sale_item_returns(session, sale_item_id):
    """Keep a line's returns after the line itself is removed.

    The sale's amounts are computed from its returns, so throwing them away
    with the line would silently restore money that was already given back.
    """
    for row in session.scalars(
        select(SaleReturn).where(SaleReturn.sale_item_id == sale_item_id)
    ).all():
        row.sale_item_id = None
    session.flush()


def _release_sale_ledger_rows(session, sale_id):
    """A deleted sale takes its return rows with it, but never its debt.

    The returns describe that sale and mean nothing without it. The customer's
    debt is a different fact -- it was really incurred -- so those movements
    stay and only lose their link.
    """
    deleted_at = _utc_now()
    for row in session.scalars(select(SaleReturn).where(SaleReturn.sale_id == sale_id)).all():
        session.merge(SyncTombstone(
            table_name="sale_returns", local_id=str(row.id), deleted_at=deleted_at
        ))
        session.delete(row)
    for movement in session.scalars(
        select(CustomerDebtMovement).where(CustomerDebtMovement.sale_id == sale_id)
    ).all():
        movement.sale_id = None
    session.flush()


def _recalculate_debt_balance(session, party, movement_model, owner_column, lifetime_field):
    """A debt balance is a cache of its movements, never a running total.

    The balance used to be added to and subtracted from beside the movement
    rows, with nothing comparing the two -- so the debtors window and the
    finance report, which read different sides, could disagree and neither
    could be shown to be wrong.
    """
    session.flush()
    rows = session.execute(
        select(movement_model.type, func.coalesce(func.sum(movement_model.amount), 0))
        .where(owner_column == party.id)
        .group_by(movement_model.type)
    ).all()
    totals = {str(kind or ""): float(amount or 0) for kind, amount in rows}
    borrowed = totals.get("qarz", 0.0)
    repaid = totals.get("tolov", 0.0)
    party.balance = borrowed - repaid
    setattr(party, lifetime_field, borrowed)
    return party.balance


def _item_cost(item, product):
    """What this line cost us, as sealed on the day it was sold."""
    sealed = getattr(item, "cost_at_sale", None)
    if sealed is not None:
        return float(sealed)
    return float((product.cost if product else 0) or 0)


def _sale_original(sale, field, fallback):
    """The sealed amount, falling back to the live one for pre-ledger rows."""
    value = getattr(sale, field, None)
    return float(value) if value is not None else float(fallback or 0)


def _sale_return_totals(session, sale_id):
    row = session.execute(
        select(
            func.coalesce(func.sum(SaleReturn.refund), 0),
            func.coalesce(func.sum(SaleReturn.discount_refund), 0),
            func.coalesce(func.sum(SaleReturn.reward_refund), 0),
        ).where(SaleReturn.sale_id == sale_id)
    ).one()
    return float(row[0] or 0), float(row[1] or 0), float(row[2] or 0)


def _recalculate_sale(session, sale):
    """Rebuild a sale's amounts from what was sealed plus its returns.

    Nothing here subtracts from the previous value, which is what made a
    repeated return impossible to detect: the answer is computed from the
    sealed originals every time, so applying the same return twice lands on
    the same figures.
    """
    refund, discount_refund, reward_refund = _sale_return_totals(session, sale.id)
    original_total = _sale_original(sale, "original_total", sale.total)
    original_discount = _sale_original(sale, "original_discount", sale.discount)
    original_paid = _sale_original(sale, "original_paid", sale.paid)
    original_reward = _sale_original(sale, "original_cashier_reward", sale.cashier_reward)
    rate = sale.exchange_rate or 1

    sale.total = max(original_total - refund, 0)
    sale.discount = min(max(original_discount - discount_refund, 0), sale.total)
    net_refund = max(0.0, refund - discount_refund)
    if sale.payment_method != "qarz":
        remaining_paid = max(original_paid - net_refund, 0)
        sale.paid = remaining_paid
        sale.paid_original = remaining_paid / rate if rate else remaining_paid
    # A reward is earned on what the customer kept, so it shrinks with the
    # returns -- but from the sealed figure, never by repeated subtraction.
    sale.cashier_reward = max(0.0, original_reward - reward_refund)


def _recalculate_sale_item(session, item):
    returned = session.scalar(
        select(func.coalesce(func.sum(SaleReturn.quantity), 0))
        .where(SaleReturn.sale_item_id == item.id)
    ) or 0
    item.returned_quantity = int(returned)
    item.returned_at = session.scalar(
        select(func.max(SaleReturn.created_at)).where(SaleReturn.sale_item_id == item.id)
    )
    item.updated_at = _utc_now()


def _recalculate_customer_balance(session, customer_id):
    """The balance is a cache of the ledger, so it is never adjusted by hand."""
    if not customer_id:
        return
    customer = session.get(Customer, customer_id)
    if customer is None:
        return
    customer.balance = float(session.scalar(
        select(func.coalesce(func.sum(CustomerDebtMovement.amount), 0))
        .where(CustomerDebtMovement.customer_id == customer_id)
    ) or 0)


def _add_customer_debt_movement(session, customer_id, amount, movement_type, sale_id=None, note=None):
    if not customer_id or not amount:
        return None
    movement = CustomerDebtMovement(
        customer_id=customer_id,
        sale_id=sale_id,
        type=movement_type,
        amount=float(amount),
        note=note,
        created_at=_utc_now(),
    )
    session.add(movement)
    session.flush()
    _recalculate_customer_balance(session, customer_id)
    return movement


def _return_sale_item_in_session(session, item, quantity, note=""):
    if quantity <= 0:
        raise AppError("Qaytarish miqdori 0 dan katta bo'lishi kerak.")
    sale = session.get(Sale, item.sale_id)
    if sale is None:
        raise AppError("Sotuv topilmadi.")
    available = item.quantity - (item.returned_quantity or 0)
    if quantity > available:
        raise AppError(f"Qaytarish miqdori ko'p. Qaytarish mumkin: {available}.")

    refund = (item.price or 0) * quantity
    # The discount and the reward are shared out in proportion to the sale as
    # it was written, not as it stands now. Returning every line therefore
    # gives back exactly the whole discount, however many steps it took.
    original_total = _sale_original(sale, "original_total", sale.total)
    share = (refund / original_total) if original_total > 0 else 0.0
    discount_refund = _sale_original(sale, "original_discount", sale.discount) * share
    reward_refund = _sale_original(sale, "original_cashier_reward", sale.cashier_reward) * share
    net_refund = max(0.0, refund - discount_refund)

    session.add(SaleReturn(
        sale_id=sale.id,
        sale_item_id=item.id,
        quantity=quantity,
        refund=refund,
        discount_refund=discount_refund,
        reward_refund=reward_refund,
        note=note or None,
        created_at=_utc_now(),
    ))
    session.flush()

    _apply_stock_delta(
        session,
        item.product_id,
        quantity,
        "qaytarish",
        note or f"Sotuv #{_sale_label(session, item.sale_id)} qaytarildi",
    )
    product = session.get(Product, item.product_id)
    if product:
        product.is_deleted = 0

    _recalculate_sale_item(session, item)
    _recalculate_sale(session, sale)

    if sale.customer_id:
        customer = session.get(Customer, sale.customer_id)
        if customer:
            customer.total_purchases = max((customer.total_purchases or 0) - net_refund, 0)
        if sale.payment_method == "qarz":
            _add_customer_debt_movement(
                session,
                sale.customer_id,
                -net_refund,
                "qaytarish",
                sale_id=sale.id,
                note=note or f"Sotuv #{_sale_label(session, sale.id)} qaytarildi",
            )


def return_sale_item(sale_item_id, quantity, note=""):
    require_online("qaytarish")
    with session_scope() as session:
        item = session.get(SaleItem, sale_item_id)
        if item is None:
            raise AppError("Sotuv arxivi topilmadi.")
        _return_sale_item_in_session(session, item, quantity, note)


def delete_sale_item(sale_item_id):
    require_online("sotuv yozuvini o'chirish")
    with session_scope() as session:
        item = session.get(SaleItem, sale_item_id)
        if item is None:
            raise AppError("Sotuv arxivi topilmadi.")
        sale_id = item.sale_id
        available = item.quantity - (item.returned_quantity or 0)
        if available > 0:
            _return_sale_item_in_session(
                session,
                item,
                available,
                note=f"Sotuv #{_sale_label(session, sale_id)} yozuvi o'chirildi",
            )
        session.merge(SyncTombstone(
            table_name="sale_items",
            local_id=str(item.id),
            deleted_at=_utc_now(),
        ))
        _detach_sale_item_returns(session, item.id)
        session.delete(item)
        session.flush()
        has_other_items = session.scalar(
            select(func.count(SaleItem.id)).where(SaleItem.sale_id == sale_id)
        )
        if not has_other_items:
            sale = session.get(Sale, sale_id)
            if sale is not None:
                session.merge(SyncTombstone(
                    table_name="sales",
                    local_id=str(sale.id),
                    deleted_at=_utc_now(),
                ))
                _release_sale_ledger_rows(session, sale.id)
                session.delete(sale)


def _sale_cost(session, sale_id):
    # coalesce, not a plain read: rows written before the cost was sealed fall
    # back to the product's cost, which is the best they ever had.
    return session.scalar(
        select(func.coalesce(func.sum(
            (SaleItem.quantity - func.coalesce(SaleItem.returned_quantity, 0))
            * func.coalesce(SaleItem.cost_at_sale, Product.cost, 0)
        ), 0))
        .select_from(SaleItem)
        .outerjoin(Product, Product.id == SaleItem.product_id)
        .where(SaleItem.sale_id == sale_id)
    ) or 0


def get_sale_cost(sale_id):
    with session_scope() as session:
        return _sale_cost(session, sale_id)


def _sales_for_date(session, date_str):
    return session.scalars(
        select(Sale)
        .where(_date_expr(Sale.created_at) == date_str)
        .where(func.coalesce(Sale.is_finalized, 0) == 1)
        .order_by(Sale.created_at.desc())
    ).all()


def _sale_revenue(sale):
    return (sale.total or 0) - (sale.discount or 0)


def get_daily_report(date_str):
    with session_scope() as session:
        sales = _sales_for_date(session, date_str)
        revenues = [_sale_revenue(s) for s in sales]
        profit = sum(_sale_revenue(s) - _sale_cost(session, s.id) for s in sales)
        return Row(dict(count=sum(1 for r in revenues if r > 0), revenue=sum(revenues) if revenues else None, profit=profit if sales else None))


def get_sales_by_date(date_str, finalized_only=False):
    with session_scope() as session:
        stmt = (
            select(Sale, User.username, Customer.name)
            .outerjoin(User, User.id == Sale.cashier_id)
            .outerjoin(Customer, Customer.id == Sale.customer_id)
            .where(_date_expr(Sale.created_at) == date_str)
            .order_by(Sale.created_at.desc())
        )
        if finalized_only:
            stmt = stmt.where(func.coalesce(Sale.is_finalized, 0) == 1)
        rows = session.execute(stmt).all()
        result = []
        for sale, username, customer_name in rows:
            row = _row_from_model(sale, cashier_name=username, customer_name=sale.customer_name or customer_name)
            row["created_at"] = _utc_to_local(row["created_at"])
            result.append(row)
        return result


def get_cashier_report(date_str):
    with session_scope() as session:
        rows = session.execute(
            select(Sale, User.username)
            .outerjoin(User, User.id == Sale.cashier_id)
            .where(_date_expr(Sale.created_at) == date_str)
            .where(func.coalesce(Sale.is_finalized, 0) == 1)
        ).all()
        grouped = {}
        for sale, username in rows:
            key = sale.cashier_id
            item = grouped.setdefault(key, Row(dict(cashier_id=key, cashier_name=username, sales_count=0, revenue=0, profit=0)))
            revenue = _sale_revenue(sale)
            if revenue > 0:
                item["sales_count"] += 1
            item["revenue"] += revenue
            item["profit"] += revenue - _sale_cost(session, sale.id)
        return sorted(grouped.values(), key=lambda r: r["revenue"], reverse=True)


def get_cashier_sold_items(date_str, cashier_id=None):
    with session_scope() as session:
        stmt = (
            select(SaleItem, Product, Sale)
            .select_from(SaleItem)
            .join(Sale, Sale.id == SaleItem.sale_id)
            .join(Product, Product.id == SaleItem.product_id)
            .where(_date_expr(Sale.created_at) == date_str)
            .where(func.coalesce(Sale.is_finalized, 0) == 1)
        )
        if cashier_id:
            stmt = stmt.where(Sale.cashier_id == cashier_id)
        grouped = {}
        for item, product, _ in session.execute(stmt).all():
            qty = item.quantity - (item.returned_quantity or 0)
            key = (product.id, item.price)
            row = grouped.setdefault(key, Row(dict(product_name=product.name, barcode=product.barcode, quantity=0, price=item.price, revenue=0, cost=0, profit=0)))
            row["quantity"] += qty
            row["revenue"] += qty * item.price
            unit_cost = _item_cost(item, product)
            row["cost"] += qty * unit_cost
            row["profit"] += qty * ((item.price or 0) - unit_cost)
        return sorted(grouped.values(), key=lambda r: r["revenue"], reverse=True)


def _sale_section_totals(session, sale_id, section_id):
    stmt = (
        select(SaleItem, Product)
        .select_from(SaleItem)
        .join(Product, Product.id == SaleItem.product_id)
        .where(SaleItem.sale_id == sale_id, Product.section_id == section_id)
    )
    totals = Row(dict(sales_count=0, product_count=0, revenue=0, profit=0, cashier_reward=0))
    section_cost = 0
    for item, product in session.execute(stmt).all():
        qty = (item.quantity or 0) - (item.returned_quantity or 0)
        if qty <= 0:
            continue
        revenue = qty * (item.price or 0)
        totals["product_count"] += qty
        totals["revenue"] += revenue
        section_cost += qty * _item_cost(item, product)
    if totals["revenue"] > 0:
        totals["sales_count"] = 1
        sale = session.get(Sale, sale_id)
        full_subtotal = session.scalar(
            select(func.coalesce(func.sum(
                (SaleItem.quantity - func.coalesce(SaleItem.returned_quantity, 0)) * SaleItem.price
            ), 0)).where(SaleItem.sale_id == sale_id)
        ) or 0
        if sale and full_subtotal > 0:
            section_gross = totals["revenue"]
            discount_portion = (sale.discount or 0) * (section_gross / full_subtotal)
            totals["revenue"] = max(0.0, section_gross - discount_portion)
            if sale.is_finalized:
                totals["cashier_reward"] = (sale.cashier_reward or 0) * (section_gross / full_subtotal)
        totals["profit"] = totals["revenue"] - section_cost
    return totals


def get_overall_period_series(start_date, end_date, section_id=None):
    with session_scope() as session:
        sales = session.scalars(
            select(Sale)
            .where(_date_expr(Sale.created_at).between(start_date, end_date))
            .where(func.coalesce(Sale.is_finalized, 0) == 1)
        ).all()
        grouped = {}
        for sale in sales:
            label = _local_date_label(sale.created_at)
            row = grouped.setdefault(label, Row(dict(
                label=label, sales_count=0, product_count=0, revenue=0, profit=0,
                cashier_reward=0, salary_deduction=0,
            )))
            if section_id:
                totals = _sale_section_totals(session, sale.id, section_id)
                row["sales_count"] += totals["sales_count"]
                row["product_count"] += totals["product_count"]
                row["revenue"] += totals["revenue"]
                row["profit"] += totals["profit"]
                row["cashier_reward"] += totals["cashier_reward"]
                continue
            revenue = _sale_revenue(sale)
            if revenue > 0:
                row["sales_count"] += 1
            row["product_count"] += session.scalar(select(func.coalesce(func.sum(SaleItem.quantity - func.coalesce(SaleItem.returned_quantity, 0)), 0)).where(SaleItem.sale_id == sale.id)) or 0
            row["revenue"] += revenue
            row["profit"] += revenue - _sale_cost(session, sale.id)
            row["cashier_reward"] += sale.cashier_reward or 0
        if section_id is None:
            for label, deduction in _all_cashier_expense_deductions(
                session, start_date, end_date
            ).items():
                row = grouped.setdefault(label, Row(dict(
                    label=label, sales_count=0, product_count=0, revenue=0,
                    profit=0, cashier_reward=0, salary_deduction=0,
                )))
                row["salary_deduction"] = (row["salary_deduction"] or 0) + deduction
        return [grouped[key] for key in sorted(grouped)]


def get_overall_day_hourly_series(date_str, section_id=None):
    with session_scope() as session:
        sales = session.scalars(
            select(Sale)
            .where(_date_expr(Sale.created_at) == date_str)
            .where(func.coalesce(Sale.is_finalized, 0) == 1)
        ).all()
        grouped = {}
        for sale in sales:
            label = _local_hour_label(sale.created_at)
            row = grouped.setdefault(label, Row(dict(
                label=label, sales_count=0, product_count=0, revenue=0, profit=0,
                cashier_reward=0, salary_deduction=0,
            )))
            if section_id:
                totals = _sale_section_totals(session, sale.id, section_id)
                row["sales_count"] += totals["sales_count"]
                row["product_count"] += totals["product_count"]
                row["revenue"] += totals["revenue"]
                row["profit"] += totals["profit"]
                row["cashier_reward"] += totals["cashier_reward"]
                continue
            revenue = _sale_revenue(sale)
            if revenue > 0:
                row["sales_count"] += 1
            row["product_count"] += session.scalar(select(func.coalesce(func.sum(SaleItem.quantity - func.coalesce(SaleItem.returned_quantity, 0)), 0)).where(SaleItem.sale_id == sale.id)) or 0
            row["revenue"] += revenue
            row["profit"] += revenue - _sale_cost(session, sale.id)
            row["cashier_reward"] += sale.cashier_reward or 0
        if section_id is None:
            for label, deduction in _all_cashier_expense_deductions(
                session, date_str, date_str, hourly=True
            ).items():
                row = grouped.setdefault(label, Row(dict(
                    label=label, sales_count=0, product_count=0, revenue=0,
                    profit=0, cashier_reward=0, salary_deduction=0,
                )))
                row["salary_deduction"] = (row["salary_deduction"] or 0) + deduction
        return [grouped[key] for key in sorted(grouped)]


def _all_cashier_expense_deductions(session, start_date, end_date, hourly=False):
    """Cashier-charged expenses for every cashier, keyed by period label."""
    rows = session.execute(
        select(Expense, Currency.rate_to_uzs)
        .outerjoin(Currency, Currency.code == Expense.currency_code)
        .where(
            Expense.cashier_id.is_not(None),
            _date_expr(Expense.created_at).between(start_date, end_date),
        )
    ).all()
    deductions = {}
    for expense, rate_to_uzs in rows:
        label = _local_hour_label(expense.created_at) if hourly else _local_date_label(expense.created_at)
        deductions[label] = deductions.get(label, 0) + (expense.amount or 0) * (rate_to_uzs or 1)
    return deductions


def _cashier_expense_deductions(session, cashier_id, start_date, end_date, hourly=False):
    rows = session.execute(
        select(Expense, Currency.rate_to_uzs)
        .outerjoin(Currency, Currency.code == Expense.currency_code)
        .where(
            Expense.cashier_id == cashier_id,
            _date_expr(Expense.created_at).between(start_date, end_date),
        )
    ).all()
    deductions = {}
    for expense, rate_to_uzs in rows:
        label = _local_hour_label(expense.created_at) if hourly else _local_date_label(expense.created_at)
        deductions[label] = deductions.get(label, 0) + (expense.amount or 0) * (rate_to_uzs or 1)
    return deductions


def get_cashier_expense_deductions(start_date, end_date, cashier_id=None):
    """Cashier-charged expenses in a period, converted to UZS and grouped by cashier.

    These are the "Kassir" category expenses: money already handed to (or spent
    on behalf of) a cashier, which therefore comes off the salary the sales have
    earned them.
    """
    with session_scope() as session:
        stmt = (
            select(
                Expense.cashier_id,
                func.coalesce(User.username, User.email).label("cashier_name"),
                func.coalesce(func.sum(Expense.amount * func.coalesce(Currency.rate_to_uzs, 1)), 0).label("amount"),
                func.count(Expense.id).label("expense_count"),
            )
            .select_from(Expense)
            .outerjoin(Currency, Currency.code == Expense.currency_code)
            .outerjoin(User, User.id == Expense.cashier_id)
            .where(
                Expense.cashier_id.is_not(None),
                _date_expr(Expense.created_at).between(start_date, end_date),
            )
            .group_by(Expense.cashier_id, "cashier_name")
        )
        if cashier_id is not None:
            stmt = stmt.where(Expense.cashier_id == cashier_id)
        return [Row(dict(row._mapping)) for row in session.execute(stmt)]


def get_cashier_expense_total(start_date, end_date, cashier_id=None):
    """Single UZS figure for :func:`get_cashier_expense_deductions`."""
    return sum(
        row["amount"] or 0
        for row in get_cashier_expense_deductions(start_date, end_date, cashier_id)
    )


def get_cashier_expense_entries(start_date, end_date, cashier_id=None):
    """Individual cashier-charged expenses, newest first (for detail views)."""
    with session_scope() as session:
        stmt = (
            select(
                Expense,
                ExpenseCategory.name.label("category_name"),
                func.coalesce(User.username, User.email).label("cashier_name"),
                func.coalesce(Currency.rate_to_uzs, 1).label("rate_to_uzs"),
            )
            .select_from(Expense)
            .outerjoin(ExpenseCategory, ExpenseCategory.id == Expense.category_id)
            .outerjoin(User, User.id == Expense.cashier_id)
            .outerjoin(Currency, Currency.code == Expense.currency_code)
            .where(
                Expense.cashier_id.is_not(None),
                _date_expr(Expense.created_at).between(start_date, end_date),
            )
            .order_by(Expense.created_at.desc(), Expense.id.desc())
        )
        if cashier_id is not None:
            stmt = stmt.where(Expense.cashier_id == cashier_id)
        return [
            _row_from_model(
                expense,
                category_name=category_name,
                cashier_name=cashier_name,
                amount_uzs=(expense.amount or 0) * (rate_to_uzs or 1),
                # Local time, so these line up with the sales rows they are
                # shown next to in the details table.
                created_at=_utc_to_local(expense.created_at) if expense.created_at else None,
            )
            for expense, category_name, cashier_name, rate_to_uzs in session.execute(stmt)
        ]


def get_cashier_period_summary(start_date, end_date, section_id=None, only_cashiers=False):
    with session_scope() as session:
        users_stmt = select(User)
        if only_cashiers:
            users_stmt = users_stmt.where(User.role == "cashier")
        users = session.scalars(users_stmt.order_by(User.username)).all()
        rows = []
        for user in users:
            sales = session.scalars(
                select(Sale)
                .where(and_(
                    Sale.cashier_id == user.id,
                    _date_expr(Sale.created_at).between(start_date, end_date),
                    func.coalesce(Sale.is_finalized, 0) == 1,
                ))
            ).all()
            row = Row(dict(
                entity_id=user.id, entity_name=user.username, sales_count=0,
                product_count=0, revenue=0, profit=0, cashier_reward=0,
            ))
            for sale in sales:
                if section_id:
                    totals = _sale_section_totals(session, sale.id, section_id)
                    row["sales_count"] += totals["sales_count"]
                    row["product_count"] += totals["product_count"]
                    row["revenue"] += totals["revenue"]
                    row["profit"] += totals["profit"]
                    row["cashier_reward"] += totals["cashier_reward"]
                    continue
                revenue = _sale_revenue(sale)
                if revenue > 0:
                    row["sales_count"] += 1
                row["product_count"] += session.scalar(select(func.coalesce(func.sum(SaleItem.quantity - func.coalesce(SaleItem.returned_quantity, 0)), 0)).where(SaleItem.sale_id == sale.id)) or 0
                row["revenue"] += revenue
                row["profit"] += revenue - _sale_cost(session, sale.id)
                row["cashier_reward"] += sale.cashier_reward or 0
            rows.append(row)
        return sorted(rows, key=lambda r: (-r["revenue"], r["entity_name"]))


def get_cashier_sales_details(cashier_id=None, start_date=None, end_date=None, section_id=None, only_cashiers=False):
    with session_scope() as session:
        cashier_ids = None
        if only_cashiers:
            cashier_ids = set(session.scalars(select(User.id).where(User.role == "cashier")).all())
            if cashier_id and cashier_id not in cashier_ids:
                return []
            if not cashier_id and not cashier_ids:
                return []

        stmt = (
            select(SaleItem, Sale, Product)
            .select_from(SaleItem)
            .join(Sale, Sale.id == SaleItem.sale_id)
            .outerjoin(Product, Product.id == SaleItem.product_id)
            .where(
                _date_expr(Sale.created_at).between(start_date, end_date),
            )
        )
        if cashier_id is not None:
            stmt = stmt.where(Sale.cashier_id == cashier_id)
        elif only_cashiers and cashier_ids:
            stmt = stmt.where(Sale.cashier_id.in_(cashier_ids))
        if section_id is not None:
            stmt = stmt.where(Product.section_id == section_id)

        records = session.execute(
            stmt.order_by(func.coalesce(Sale.is_finalized, 0), Product.name, SaleItem.id).limit(5000)
        ).all()
        if not records:
            return []

        sale_ids = {sale.id for _, sale, _ in records}
        active_totals = dict(session.execute(
            select(
                SaleItem.sale_id,
                func.coalesce(func.sum(
                    (SaleItem.quantity - func.coalesce(SaleItem.returned_quantity, 0)) * SaleItem.price
                ), 0),
            )
            .where(SaleItem.sale_id.in_(sale_ids))
            .group_by(SaleItem.sale_id)
        ).all())

        result = []
        for item, sale, product in records:
            sold_quantity = item.quantity or 0
            returned_quantity = min(sold_quantity, item.returned_quantity or 0)
            net_quantity = max(0, sold_quantity - returned_quantity)
            active_subtotal = net_quantity * (item.price or 0)
            active_sale_total = active_totals.get(sale.id, 0) or 0
            item_discount = (
                (sale.discount or 0) * (active_subtotal / active_sale_total)
                if active_sale_total > 0 else 0
            )
            item_cashier_reward = (
                (sale.cashier_reward or 0) * (active_subtotal / active_sale_total)
                if sale.is_finalized and active_sale_total > 0 else 0
            )
            result.append(Row(dict(
                sale_id=sale.id,
                sale_display_no=sale.display_no,
                sale_item_id=item.id,
                product_id=item.product_id,
                product_name=product.name if product else "-",
                barcode=(product.barcode if product else None) or "-",
                section_id=product.section_id if product else None,
                sold_quantity=sold_quantity,
                returned_quantity=returned_quantity,
                returned_at=_utc_to_local(item.returned_at) if item.returned_at else None,
                net_quantity=net_quantity,
                price=item.price or 0,
                cost=_item_cost(item, product),
                active_subtotal=active_subtotal,
                item_discount=item_discount,
                item_total_after_discount=max(0, active_subtotal - item_discount),
                cashier_reward=item_cashier_reward,
                sale_cashier_reward=sale.cashier_reward or 0,
                payment_method=sale.payment_method or "",
                is_finalized=sale.is_finalized or 0,
                finalized_at=_utc_to_local(sale.finalized_at) if sale.finalized_at else None,
                created_at=_utc_to_local(sale.created_at),
            )))
        return result


def get_cashier_salary_period_summary(start_date, end_date, section_id=None):
    with session_scope() as session:
        users = session.scalars(
            select(User).where(User.role == "cashier").order_by(User.username)
        ).all()
        rows = []
        for user in users:
            sales = session.scalars(
                select(Sale)
                .where(and_(
                    Sale.cashier_id == user.id,
                    _date_expr(Sale.created_at).between(start_date, end_date),
                    func.coalesce(Sale.is_finalized, 0) == 1,
                ))
            ).all()
            row = Row(dict(
                entity_id=user.id,
                entity_name=user.username or user.email,
                sales_count=0,
                product_count=0,
                revenue=0,
                profit=0,
                cashier_reward=0.0,
                total_salary=0.0,
                salary_deduction=0.0,
            ))
            for sale in sales:
                if section_id:
                    totals = _sale_section_totals(session, sale.id, section_id)
                    if totals["revenue"] > 0:
                        row["sales_count"] += totals["sales_count"]
                        row["product_count"] += totals["product_count"]
                        row["revenue"] += totals["revenue"]
                        row["profit"] += totals["profit"]
                        row["cashier_reward"] += totals["cashier_reward"]
                        row["total_salary"] += totals["cashier_reward"]
                    continue
                revenue = _sale_revenue(sale)
                if revenue > 0:
                    row["sales_count"] += 1
                row["product_count"] += session.scalar(
                    select(func.coalesce(func.sum(SaleItem.quantity - func.coalesce(SaleItem.returned_quantity, 0)), 0))
                    .where(SaleItem.sale_id == sale.id)
                ) or 0
                row["revenue"] += revenue
                row["profit"] += revenue - _sale_cost(session, sale.id)
                row["cashier_reward"] += (sale.cashier_reward or 0.0)
                row["total_salary"] += (sale.cashier_reward or 0.0)
            if section_id is None:
                deduction = sum(
                    _cashier_expense_deductions(session, user.id, start_date, end_date).values()
                )
                row["salary_deduction"] = deduction
                row["total_salary"] -= deduction
            rows.append(row)
        return sorted(rows, key=lambda r: (-r["total_salary"], -r["revenue"], r["entity_name"]))


def get_customer_period_summary(start_date, end_date):
    with session_scope() as session:
        customers = session.scalars(select(Customer).order_by(Customer.name)).all()
        rows = []
        for customer in customers:
            sales = session.scalars(
                select(Sale)
                .where(and_(
                    Sale.customer_id == customer.id,
                    _date_expr(Sale.created_at).between(start_date, end_date),
                    func.coalesce(Sale.is_finalized, 0) == 1,
                ))
            ).all()
            row = Row(dict(entity_id=customer.id, entity_name=customer.name, sales_count=0, product_count=0, revenue=0, profit=0))
            for sale in sales:
                revenue = _sale_revenue(sale)
                if revenue > 0:
                    row["sales_count"] += 1
                row["product_count"] += session.scalar(select(func.coalesce(func.sum(SaleItem.quantity - func.coalesce(SaleItem.returned_quantity, 0)), 0)).where(SaleItem.sale_id == sale.id)) or 0
                row["revenue"] += revenue
                row["profit"] += revenue - _sale_cost(session, sale.id)
            rows.append(row)
        return sorted(rows, key=lambda r: (-r["revenue"], r["entity_name"]))


def get_entity_period_series(entity_type, entity_id, start_date, end_date, section_id=None):
    if entity_type not in ("cashier", "cashier_salary", "customer"):
        raise AppError("Hisobot turi noto'g'ri.")
    column = Sale.cashier_id if entity_type in ("cashier", "cashier_salary") else Sale.customer_id
    with session_scope() as session:
        sales = session.scalars(
            select(Sale)
            .where(and_(
                column == entity_id,
                _date_expr(Sale.created_at).between(start_date, end_date),
                func.coalesce(Sale.is_finalized, 0) == 1,
            ))
        ).all()
        grouped = {}
        for sale in sales:
            label = _local_date_label(sale.created_at)
            row = grouped.setdefault(label, Row(dict(
                label=label, sales_count=0, product_count=0, revenue=0,
                profit=0, salary=0, total_salary=0, cashier_reward=0, salary_deduction=0,
            )))
            if section_id:
                totals = _sale_section_totals(session, sale.id, section_id)
                if totals["revenue"] > 0:
                    row["salary"] += totals["cashier_reward"]
                    row["total_salary"] += totals["cashier_reward"]
                    row["cashier_reward"] += totals["cashier_reward"]
                row["sales_count"] += totals["sales_count"]
                row["product_count"] += totals["product_count"]
                row["revenue"] += totals["revenue"]
                row["profit"] += totals["profit"]
                continue
            row["salary"] += (sale.cashier_reward or 0)
            row["total_salary"] += (sale.cashier_reward or 0)
            row["cashier_reward"] += (sale.cashier_reward or 0)
            revenue = _sale_revenue(sale)
            if revenue > 0:
                row["sales_count"] += 1
            row["product_count"] += session.scalar(select(func.coalesce(func.sum(SaleItem.quantity - func.coalesce(SaleItem.returned_quantity, 0)), 0)).where(SaleItem.sale_id == sale.id)) or 0
            row["revenue"] += revenue
            row["profit"] += revenue - _sale_cost(session, sale.id)
        if entity_type == "cashier_salary" and section_id is None:
            for label, deduction in _cashier_expense_deductions(
                session, entity_id, start_date, end_date
            ).items():
                row = grouped.setdefault(label, Row(dict(
                    label=label, sales_count=0, product_count=0, revenue=0,
                    profit=0, salary=0, total_salary=0, cashier_reward=0, salary_deduction=0,
                )))
                row["salary_deduction"] += deduction
                row["salary"] -= deduction
                row["total_salary"] -= deduction
        return [grouped[key] for key in sorted(grouped)]


def get_entity_day_hourly_series(entity_type, entity_id, date_str, section_id=None):
    if entity_type not in ("cashier", "cashier_salary", "customer"):
        raise AppError("Hisobot turi noto'g'ri.")
    column = Sale.cashier_id if entity_type in ("cashier", "cashier_salary") else Sale.customer_id
    with session_scope() as session:
        sales = session.scalars(
            select(Sale)
            .where(and_(
                column == entity_id,
                _date_expr(Sale.created_at) == date_str,
                func.coalesce(Sale.is_finalized, 0) == 1,
            ))
        ).all()
        grouped = {}
        for sale in sales:
            label = _local_hour_label(sale.created_at)
            row = grouped.setdefault(label, Row(dict(
                label=label, sales_count=0, product_count=0, revenue=0,
                profit=0, salary=0, total_salary=0, cashier_reward=0, salary_deduction=0,
            )))
            if section_id:
                totals = _sale_section_totals(session, sale.id, section_id)
                if totals["revenue"] > 0:
                    row["salary"] += totals["cashier_reward"]
                    row["total_salary"] += totals["cashier_reward"]
                    row["cashier_reward"] += totals["cashier_reward"]
                row["sales_count"] += totals["sales_count"]
                row["product_count"] += totals["product_count"]
                row["revenue"] += totals["revenue"]
                row["profit"] += totals["profit"]
                continue
            row["salary"] += (sale.cashier_reward or 0)
            row["total_salary"] += (sale.cashier_reward or 0)
            row["cashier_reward"] += (sale.cashier_reward or 0)
            revenue = _sale_revenue(sale)
            if revenue > 0:
                row["sales_count"] += 1
            row["product_count"] += session.scalar(select(func.coalesce(func.sum(SaleItem.quantity - func.coalesce(SaleItem.returned_quantity, 0)), 0)).where(SaleItem.sale_id == sale.id)) or 0
            row["revenue"] += revenue
            row["profit"] += revenue - _sale_cost(session, sale.id)
        if entity_type == "cashier_salary" and section_id is None:
            for label, deduction in _cashier_expense_deductions(
                session, entity_id, date_str, date_str, hourly=True
            ).items():
                row = grouped.setdefault(label, Row(dict(
                    label=label, sales_count=0, product_count=0, revenue=0,
                    profit=0, salary=0, total_salary=0, cashier_reward=0, salary_deduction=0,
                )))
                row["salary_deduction"] += deduction
                row["salary"] -= deduction
                row["total_salary"] -= deduction
        return [grouped[key] for key in sorted(grouped)]


def get_all_customers():
    with session_scope() as session:
        return _rows_from_models(session.scalars(select(Customer).order_by(Customer.name)).all())


def add_customer(name, phone, email):
    with session_scope() as session:
        row = Customer(name=name, phone=phone, email=email)
        session.add(row)
        session.flush()
        return row.id


def update_customer(cid, name, phone, email):
    with session_scope() as session:
        row = session.get(Customer, cid)
        if row:
            row.name, row.phone, row.email = name, phone, email


def get_all_suppliers():
    with session_scope() as session:
        return _rows_from_models(session.scalars(select(Supplier).order_by(Supplier.name)).all())


def add_supplier(name, phone=None, note=None, debt_currency="UZS"):
    clean_name = name.strip()
    if not clean_name:
        raise AppError("Ta'minotchi nomini kiriting.")
    with session_scope() as session:
        row = Supplier(name=clean_name, phone=phone, note=note, debt_currency=debt_currency)
        session.add(row)
        session.flush()
        s_id = row.id
    log_activity(
        "supplier_added",
        f"Yangi ta'minotchi: {clean_name}",
        f"Tel: {phone or '-'} | Valyuta: {debt_currency}",
        level="success",
        target="supplier_debts",
        badge="Ta'minotchi",
    )
    return s_id


def update_supplier(supplier_id, name, phone=None, note=None, debt_currency=None):
    clean_name = name.strip()
    if not clean_name:
        raise AppError("Ta'minotchi nomini kiriting.")
    with session_scope() as session:
        row = session.get(Supplier, supplier_id)
        if row:
            row.name, row.phone, row.note = clean_name, phone, note
            if debt_currency:
                row.debt_currency = debt_currency
    log_activity(
        "supplier_updated",
        f"Ta'minotchi yangilandi: {clean_name}",
        f"Tel: {phone or '-'}",
        level="info",
        target="supplier_debts",
        badge="Yangilandi",
    )


def delete_supplier(supplier_id):
    s_name = None
    with session_scope() as session:
        row = session.get(Supplier, supplier_id)
        if row:
            s_name = row.name
            for product in session.scalars(select(Product).where(Product.supplier_id == supplier_id)):
                product.supplier_id = None
            deleted_at = _utc_now()
            movement_ids = session.scalars(
                select(SupplierDebtMovement.id).where(SupplierDebtMovement.supplier_id == supplier_id)
            ).all()
            for movement_id in movement_ids:
                session.merge(SyncTombstone(
                    table_name="supplier_debt_movements",
                    local_id=str(movement_id),
                    deleted_at=deleted_at,
                ))
            session.query(SupplierDebtMovement).filter(SupplierDebtMovement.supplier_id == supplier_id).delete()
            session.delete(row)
    if s_name:
        log_activity(
            "supplier_deleted",
            f"Ta'minotchi o'chirildi: {s_name}",
            "Ta'minotchi va uning qarz harakatlari o'chirildi",
            level="danger",
            target="supplier_debts",
            badge="O'chirildi",
        )


def add_supplier_debt(supplier_id, amount, note=""):
    require_online("ta'minotchi qarzi")
    if amount <= 0:
        raise AppError("Qarz summasi 0 dan katta bo'lishi kerak.")
    with session_scope() as session:
        row = session.get(Supplier, supplier_id)
        session.add(SupplierDebtMovement(supplier_id=supplier_id, type="qarz", amount=amount, note=note))
        s_balance = _recalculate_debt_balance(
            session, row, SupplierDebtMovement, SupplierDebtMovement.supplier_id, "total_received"
        )
        s_name = row.name
        s_curr = row.debt_currency or "UZS"
    log_activity(
        "supplier_debt_added",
        f"Ta'minotchi qarzi: {s_name}",
        f"+{amount:,.0f} {s_curr} qarz olindi | Jami qarz: {s_balance:,.0f} {s_curr} | Izoh: {note or '-'}",
        level="warning",
        target="supplier_debts",
        badge="Qarz olindi",
    )


def pay_supplier_debt(supplier_id, amount, note=""):
    require_online("ta'minotchiga to'lov")
    if amount <= 0:
        raise AppError("To'lov summasi 0 dan katta bo'lishi kerak.")
    with session_scope() as session:
        row = session.get(Supplier, supplier_id)
        current_balance = row.balance or 0
        if amount > current_balance:
            raise AppError(f"To'lov joriy qarzdan oshib ketdi. Joriy qarz: {current_balance:,.2f}.")
        session.add(SupplierDebtMovement(supplier_id=supplier_id, type="tolov", amount=amount, note=note))
        s_rem = _recalculate_debt_balance(
            session, row, SupplierDebtMovement, SupplierDebtMovement.supplier_id, "total_received"
        )
        s_name = row.name
        s_curr = row.debt_currency or "UZS"
    if s_rem <= 0:
        log_activity(
            "supplier_debt_cleared",
            f"Ta'minotchi qarzi to'liq uzildi: {s_name}",
            f"To'lov: {amount:,.0f} {s_curr}. Qarz butunlay yopildi (0 {s_curr})",
            level="success",
            target="supplier_debts",
            badge="Qarz uzildi",
        )
    else:
        log_activity(
            "supplier_debt_paid",
            f"Ta'minotchi qarz to'lovi: {s_name}",
            f"To'landi: {amount:,.0f} {s_curr} | Qoldiq qarz: {s_rem:,.0f} {s_curr} | Izoh: {note or '-'}",
            level="info",
            target="supplier_debts",
            badge="Qisman to'landi",
        )


def get_supplier_debt_movements(supplier_id=None):
    stmt = select(SupplierDebtMovement, Supplier.name).join(Supplier, Supplier.id == SupplierDebtMovement.supplier_id)
    if supplier_id:
        stmt = stmt.where(SupplierDebtMovement.supplier_id == supplier_id)
    with session_scope() as session:
        rows = session.execute(stmt.order_by(SupplierDebtMovement.created_at.desc(), SupplierDebtMovement.id.desc())).all()
        return [_row_from_model(m, supplier_name=name) for m, name in rows]


def get_all_debtors():
    with session_scope() as session:
        rows = session.execute(
            select(Debtor, User.username, User.email)
            .outerjoin(User, User.id == Debtor.user_id)
            .order_by(Debtor.name)
        ).all()
        return [
            _row_from_model(debtor, cashier_name=username, cashier_email=email)
            for debtor, username, email in rows
        ]


def add_debtor(name, phone=None, note=None, debt_currency="UZS", user_id=None):
    clean_name = name.strip()
    if not clean_name:
        raise AppError("Qarz oluvchi nomini kiriting.")
    with session_scope() as session:
        if user_id is not None:
            user = session.get(User, user_id)
            if user is None:
                raise AppError("Kassir topilmadi.")
            owner_uid = session.get(UserSetting, {"user_id": user_id, "key": "api_user_uid"})
            if owner_uid:
                raise AppError("Asosiy account owner hisoblanadi, uni kassir sifatida tanlab bo'lmaydi.")
            if session.scalar(select(Debtor.id).where(Debtor.user_id == user_id)):
                raise AppError("Bu kassir qarz oluvchilar ro'yxatida mavjud.")
            clean_name = (user.username or user.email or clean_name).strip()
            phone = None
        row = Debtor(
            user_id=user_id,
            name=clean_name,
            phone=phone,
            note=note,
            debt_currency=debt_currency,
        )
        session.add(row)
        session.flush()
        d_id = row.id
    log_activity(
        "debtor_added",
        f"Yangi qarzdor: {clean_name}",
        f"Tel: {phone or '-'} | Valyuta: {debt_currency}",
        level="success",
        target="supplier_debts",
        badge="Qarzdor",
    )
    return d_id


def update_debtor(debtor_id, name, phone=None, note=None, debt_currency=None):
    clean_name = name.strip()
    if not clean_name:
        raise AppError("Qarz oluvchi nomini kiriting.")
    with session_scope() as session:
        row = session.get(Debtor, debtor_id)
        if row:
            row.name, row.phone, row.note = clean_name, phone, note
            if debt_currency:
                row.debt_currency = debt_currency
    log_activity(
        "debtor_updated",
        f"Qarzdor yangilandi: {clean_name}",
        f"Tel: {phone or '-'}",
        level="info",
        target="supplier_debts",
        badge="Yangilandi",
    )


def delete_debtor(debtor_id):
    d_name = None
    with session_scope() as session:
        row = session.get(Debtor, debtor_id)
        if row:
            d_name = row.name
            deleted_at = _utc_now()
            movement_ids = session.scalars(
                select(DebtorDebtMovement.id).where(DebtorDebtMovement.debtor_id == debtor_id)
            ).all()
            for movement_id in movement_ids:
                session.merge(SyncTombstone(
                    table_name="debtor_debt_movements",
                    local_id=str(movement_id),
                    deleted_at=deleted_at,
                ))
            session.query(DebtorDebtMovement).filter(DebtorDebtMovement.debtor_id == debtor_id).delete()
            session.delete(row)
    if d_name:
        log_activity(
            "debtor_deleted",
            f"Qarzdor o'chirildi: {d_name}",
            "Qarzdor va uning qarz harakatlari o'chirildi",
            level="danger",
            target="supplier_debts",
            badge="O'chirildi",
        )


def add_debtor_debt(debtor_id, amount, note=""):
    require_online("qarz berish")
    if amount <= 0:
        raise AppError("Qarz summasi 0 dan katta bo'lishi kerak.")
    with session_scope() as session:
        row = session.get(Debtor, debtor_id)
        session.add(DebtorDebtMovement(debtor_id=debtor_id, type="qarz", amount=amount, note=note))
        d_balance = _recalculate_debt_balance(
            session, row, DebtorDebtMovement, DebtorDebtMovement.debtor_id, "total_given"
        )
        d_name = row.name
        d_curr = row.debt_currency or "UZS"
    log_activity(
        "debtor_debt_added",
        f"Mijozga qarz berildi: {d_name}",
        f"+{amount:,.0f} {d_curr} qarz | Jami qarz: {d_balance:,.0f} {d_curr} | Izoh: {note or '-'}",
        level="warning",
        target="supplier_debts",
        badge="Qarz berildi",
    )


def pay_debtor_debt(debtor_id, amount, note=""):
    require_online("qarz to'lovi")
    if amount <= 0:
        raise AppError("To'lov summasi 0 dan katta bo'lishi kerak.")
    with session_scope() as session:
        row = session.get(Debtor, debtor_id)
        current_balance = row.balance or 0
        if amount > current_balance:
            raise AppError(f"To'lov joriy qarzdan oshib ketdi. Joriy qarz: {current_balance:,.2f}.")
        session.add(DebtorDebtMovement(debtor_id=debtor_id, type="tolov", amount=amount, note=note))
        d_rem = _recalculate_debt_balance(
            session, row, DebtorDebtMovement, DebtorDebtMovement.debtor_id, "total_given"
        )
        d_name = row.name
        d_curr = row.debt_currency or "UZS"
    if d_rem <= 0:
        log_activity(
            "debtor_debt_cleared",
            f"Mijoz qarzi to'liq uzildi: {d_name}",
            f"To'lov: {amount:,.0f} {d_curr}. Qarz butunlay yopildi (0 {d_curr})",
            level="success",
            target="supplier_debts",
            badge="Qarz uzildi",
        )
    else:
        log_activity(
            "debtor_debt_paid",
            f"Mijoz qarz to'lovi: {d_name}",
            f"To'landi: {amount:,.0f} {d_curr} | Qoldiq qarz: {d_rem:,.0f} {d_curr} | Izoh: {note or '-'}",
            level="info",
            target="supplier_debts",
            badge="Qisman to'landi",
        )


def get_debtor_debt_movements(debtor_id=None):
    stmt = select(DebtorDebtMovement, Debtor.name).join(Debtor, Debtor.id == DebtorDebtMovement.debtor_id)
    if debtor_id:
        stmt = stmt.where(DebtorDebtMovement.debtor_id == debtor_id)
    with session_scope() as session:
        rows = session.execute(stmt.order_by(DebtorDebtMovement.created_at.desc(), DebtorDebtMovement.id.desc())).all()
        return [_row_from_model(m, debtor_name=name) for m, name in rows]


def get_expense_categories():
    with session_scope() as session:
        return _rows_from_models(session.scalars(select(ExpenseCategory).order_by(ExpenseCategory.name)).all())


def add_expense_category(name):
    clean_name = name.strip()
    if not clean_name:
        raise AppError("Kategoriya nomini kiriting.")
    with session_scope() as session:
        try:
            row = ExpenseCategory(name=clean_name)
            session.add(row)
            session.flush()
            cat_id = row.id
        except IntegrityError as exc:
            raise AppError("Bu kategoriya allaqachon mavjud.") from exc
    log_activity(
        "expense_category_added",
        f"Yangi xarajat kategoriyasi: {clean_name}",
        "Kategoriya yaratildi",
        level="success",
        target="expenses",
        badge="Kategoriya",
    )
    return cat_id


def update_expense_category(category_id, name):
    clean_name = name.strip()
    if not clean_name:
        raise AppError("Kategoriya nomini kiriting.")
    with session_scope() as session:
        try:
            row = session.get(ExpenseCategory, category_id)
            if row:
                if (
                    is_cashier_expense_category_name(row.name)
                    and not is_cashier_expense_category_name(clean_name)
                    and session.scalar(
                        select(func.count(Expense.id)).where(Expense.category_id == category_id)
                    )
                ):
                    raise AppError(
                        "Bu kategoriya kassir oyligiga bog'langan. "
                        "Nomini o'zgartirish uchun avval undagi harajatlarni o'chiring."
                    )
                row.name = clean_name
                session.flush()
        except IntegrityError as exc:
            raise AppError("Bu kategoriya allaqachon mavjud.") from exc
    log_activity(
        "expense_category_updated",
        f"Xarajat kategoriyasi yangilandi: {clean_name}",
        "Kategoriya nomi o'zgartirildi",
        level="info",
        target="expenses",
        badge="Kategoriya",
    )


def delete_expense_category(category_id):
    cat_name = None
    with session_scope() as session:
        row = session.get(ExpenseCategory, category_id)
        if row:
            if is_cashier_expense_category_name(row.name) and session.scalar(
                select(func.count(Expense.id)).where(Expense.category_id == category_id)
            ):
                raise AppError(
                    "Kassir kategoriyasida harajatlar bor. "
                    "Uni o'chirish kassirlar oyligini buzadi."
                )
            cat_name = row.name
            for expense in session.scalars(select(Expense).where(Expense.category_id == category_id)):
                expense.category_id = None
                expense.cashier_id = None
            session.delete(row)
    if cat_name:
        log_activity(
            "expense_category_deleted",
            f"Xarajat kategoriyasi o'chirildi: {cat_name}",
            "Kategoriya o'chirildi",
            level="danger",
            target="expenses",
            badge="Kategoriya",
        )


def get_expenses():
    with session_scope() as session:
        cashier_name = (
            select(User.username)
            .where(User.id == Expense.cashier_id)
            .correlate(Expense)
            .scalar_subquery()
        )
        rows = session.execute(
            select(Expense, ExpenseCategory.name, User.username, cashier_name)
            .outerjoin(ExpenseCategory, ExpenseCategory.id == Expense.category_id)
            .outerjoin(User, User.id == Expense.user_id)
            .order_by(Expense.created_at.desc(), Expense.id.desc())
        ).all()
        return [
            _row_from_model(
                expense,
                category_name=name,
                username=username,
                cashier_name=selected_cashier_name,
            )
            for expense, name, username, selected_cashier_name in rows
        ]


def is_cashier_expense_category_name(name):
    cleaned = str(name or "").strip().casefold()
    if not cleaned:
        return False
    if cleaned in CASHIER_EXPENSE_CATEGORY_ALIASES:
        return True
    # Also accept renamed variants such as "Kassir oyligi" or "Cashier advance",
    # so the feature keeps working if the shop relabels the category.
    return cleaned.split()[0] in CASHIER_EXPENSE_CATEGORY_ALIASES


def ensure_cashier_expense_category():
    """Create the cashier expense category if this database has none.

    init_db() already does this, but the expenses screen calls it too so the
    category can never be missing from the dialog on an older database.
    """
    with session_scope() as session:
        for name in session.scalars(select(ExpenseCategory.name)):
            if is_cashier_expense_category_name(name):
                return name
        session.add(ExpenseCategory(
            id=stable_row_id("expense_categories", CASHIER_EXPENSE_CATEGORY_NAME),
            name=CASHIER_EXPENSE_CATEGORY_NAME,
        ))
    return CASHIER_EXPENSE_CATEGORY_NAME


def get_cashier_expense_category():
    """Return the category rows that route an expense to a cashier's salary."""
    with session_scope() as session:
        return _rows_from_models([
            row for row in session.scalars(select(ExpenseCategory).order_by(ExpenseCategory.name)).all()
            if is_cashier_expense_category_name(row.name)
        ])


def _validated_expense_cashier(session, category_id, cashier_id):
    category = session.get(ExpenseCategory, category_id) if category_id else None
    if not is_cashier_expense_category_name(category.name if category else None):
        return None
    if not cashier_id:
        raise AppError("Kassirni tanlang!")
    cashier = session.get(User, cashier_id)
    if cashier is None or cashier.role != "cashier":
        raise AppError("Tanlangan foydalanuvchi kassir emas.")
    return cashier.id


def add_expense(category_id, amount, currency_code, description, user_id=None, cashier_id=None):
    require_online("harajat qo'shish")
    if amount <= 0:
        raise AppError("Harajat summasi 0 dan katta bo'lishi kerak.")
    with session_scope() as session:
        cashier_id = _validated_expense_cashier(session, category_id, cashier_id)
        row = Expense(
            category_id=category_id,
            amount=amount,
            currency_code=currency_code,
            description=description,
            user_id=user_id,
            cashier_id=cashier_id,
        )
        session.add(row)
        session.flush()
        exp_id = row.id
    log_activity(
        "expense_added",
        f"Xarajat qo'shildi: {amount:,.0f} {currency_code}",
        f"Izoh: {description or '-'}",
        level="warning",
        target="expenses",
        badge="Xarajat",
    )
    return exp_id


def update_expense(expense_id, category_id, amount, currency_code, description, user_id=None, cashier_id=None):
    if amount <= 0:
        raise AppError("Harajat summasi 0 dan katta bo'lishi kerak.")
    with session_scope() as session:
        row = session.get(Expense, expense_id)
        if row:
            cashier_id = _validated_expense_cashier(session, category_id, cashier_id)
            row.category_id, row.amount, row.currency_code, row.description = category_id, amount, currency_code, description
            row.cashier_id = cashier_id
            if user_id is not None:
                row.user_id = user_id
    log_activity(
        "expense_updated",
        f"Xarajat yangilandi: {amount:,.0f} {currency_code}",
        f"Izoh: {description or '-'}",
        level="info",
        target="expenses",
        badge="Yangilandi",
    )


def delete_expense(expense_id):
    exp_amount = None
    exp_curr = "UZS"
    exp_desc = ""
    with session_scope() as session:
        row = session.get(Expense, expense_id)
        if row:
            exp_amount = row.amount
            exp_curr = row.currency_code or "UZS"
            exp_desc = row.description or ""
            session.delete(row)
    if exp_amount is not None:
        log_activity(
            "expense_deleted",
            f"Xarajat o'chirildi: {exp_amount:,.0f} {exp_curr}",
            f"Izoh: {exp_desc or '-'}",
            level="danger",
            target="expenses",
            badge="O'chirildi",
        )


def _account_owner_id(session):
    """Whose view of the books counts as the shop's own.

    This used to be "the admin with the smallest id", which only worked while
    ids were handed out in order.  They are UUIDs now, so the account owner is
    named explicitly: the admin this device is signed in as, falling back to
    the earliest admin by creation time.
    """
    owner_id = owner_row_id(_ACTIVE_ACCOUNT_UID)
    if owner_id and session.get(User, owner_id) is not None:
        return owner_id
    return session.scalar(
        select(User.id)
        .where(User.role == "admin")
        .order_by(func.coalesce(User.created_at, ""), User.id)
    )


def _apply_expense_owner_filter(stmt, session, user_id=None, include_unassigned=False):
    if not user_id:
        return stmt
    if include_unassigned and user_id == _account_owner_id(session):
        return stmt.where(or_(Expense.user_id == user_id, Expense.user_id.is_(None)))
    return stmt.where(Expense.user_id == user_id)


def get_expense_report(start_date, end_date, category_id=None, user_id=None, include_unassigned=False, include_cashier=True):
    stmt = (
        select(_date_expr(Expense.created_at).label("label"), Expense.currency_code, func.coalesce(func.sum(Expense.amount), 0).label("amount"))
        .where(_date_expr(Expense.created_at).between(start_date, end_date))
        .group_by(_date_expr(Expense.created_at), Expense.currency_code)
        .order_by(_date_expr(Expense.created_at))
    )
    if category_id:
        stmt = stmt.where(Expense.category_id == category_id)
    if not include_cashier:
        # Expenses charged to a cashier are paid out of that cashier's salary,
        # not out of the shop's profit, so profit reports leave them out.
        stmt = stmt.where(Expense.cashier_id.is_(None))
    with session_scope() as session:
        stmt = _apply_expense_owner_filter(stmt, session, user_id, include_unassigned)
        return [Row(dict(row._mapping)) for row in session.execute(stmt)]


def get_expense_hourly_report(date_str, category_id=None, user_id=None, include_unassigned=False, include_cashier=True):
    hour_label = func.substr(func.datetime(Expense.created_at, "localtime"), 12, 2).op("||")(":00").label("label")
    stmt = (
        select(hour_label, Expense.currency_code, func.coalesce(func.sum(Expense.amount), 0).label("amount"))
        .where(_date_expr(Expense.created_at) == date_str)
        .group_by(hour_label, Expense.currency_code)
        .order_by(hour_label)
    )
    if category_id:
        stmt = stmt.where(Expense.category_id == category_id)
    if not include_cashier:
        stmt = stmt.where(Expense.cashier_id.is_(None))
    with session_scope() as session:
        stmt = _apply_expense_owner_filter(stmt, session, user_id, include_unassigned)
        return [Row(dict(row._mapping)) for row in session.execute(stmt)]


def get_expense_category_report(start_date, end_date, category_id=None):
    category_name = func.coalesce(ExpenseCategory.name, "Kategoriya yo'q").label("category_name")
    stmt = (
        select(category_name, Expense.currency_code, func.coalesce(func.sum(Expense.amount), 0).label("amount"))
        .outerjoin(ExpenseCategory, ExpenseCategory.id == Expense.category_id)
        .where(_date_expr(Expense.created_at).between(start_date, end_date))
        .group_by(category_name, Expense.currency_code)
        .order_by(func.sum(Expense.amount).desc())
    )
    if category_id:
        stmt = stmt.where(Expense.category_id == category_id)
    with session_scope() as session:
        return [Row(dict(row._mapping)) for row in session.execute(stmt)]


def authenticate(email, password):
    email = _normalize_email(email)
    if not email:
        return None
    with session_scope() as session:
        row = session.scalar(select(User).where(User.email == email))
        if row and _verify_password(password, row.password):
            if not str(row.password).startswith("pbkdf2_sha256$"):
                row.password = _hash_password(password)
            return _row_from_model(row)
        return None


def sync_online_user(email, display_name=None, role="admin", access_token=None, user_uid=None):
    email = _normalize_email(email)
    role = role if role in ("admin", "cashier") else "cashier"
    display_name = (display_name or "").strip()
    username = display_name or email
    if not email:
        raise AppError("Email kiriting.")
    safe_uid = _safe_account_uid(user_uid) if user_uid else None
    if safe_uid and _ACTIVE_ACCOUNT_UID and safe_uid != _ACTIVE_ACCOUNT_UID:
        raise AppError("Account bazasi boshqa userga tegishli.")
    _sync_suspend_token = suspend_sync()
    _sync_suspend_token.__enter__()
    try:
        with session_scope() as session:
            user = session.scalar(select(User).where(User.email == email))
            if user is None:
                user = User(username=username, email=email, password=_hash_password(secrets.token_urlsafe(32)), role=role)
                session.add(user)
                session.flush()
            else:
                if display_name:
                    duplicate_name = session.scalar(
                        select(User.id).where(User.username == display_name, User.id != user.id)
                    )
                    user.username = email if duplicate_name else display_name
                user.email = email
                user.role = role
            if access_token:
                row = session.get(UserSetting, {"user_id": user.id, "key": "api_access_token"}) or UserSetting(
                    user_id=user.id, key="api_access_token"
                )
                row.value = access_token
                session.merge(row)
            if safe_uid:
                uid_row = session.get(UserSetting, {"user_id": user.id, "key": "api_user_uid"}) or UserSetting(
                    user_id=user.id, key="api_user_uid"
                )
                uid_row.value = safe_uid
                session.merge(uid_row)
            return _row_from_model(user)
    finally:
        _sync_suspend_token.__exit__(None, None, None)


def log_login(user):
    _sync_suspend_token = suspend_sync()
    _sync_suspend_token.__enter__()
    try:
        with session_scope() as session:
            session.add(LoginLog(user_id=user["id"], username=user.get("email") or user["username"], role=user["role"], logged_at=_utc_now()))
    finally:
        _sync_suspend_token.__exit__(None, None, None)
    u_name = user.get("email") or user.get("username")
    u_role = str(user.get("role", "cashier")).title()
    log_activity(
        "user_login",
        f"Tizimga kirish: {u_name}",
        f"Foydalanuvchi roli: {u_role}",
        level="info",
        target="login_history",
        badge="Kirish",
    )


def touch_user_activity(user_id):
    if not user_id:
        return
    _sync_suspend_token = suspend_sync()
    _sync_suspend_token.__enter__()
    try:
        with session_scope() as session:
            user = session.get(User, user_id)
            if not user:
                return
            row = session.get(UserSetting, {"user_id": user_id, "key": "last_activity_utc"}) or UserSetting(user_id=user_id, key="last_activity_utc")
            row.value = _utc_now()
            session.merge(row)
    finally:
        _sync_suspend_token.__exit__(None, None, None)
    _touch_account_session(active=True)


def clear_user_activity(user_id):
    if not user_id:
        return
    _sync_suspend_token = suspend_sync()
    _sync_suspend_token.__enter__()
    try:
        with session_scope() as session:
            row = session.get(UserSetting, {"user_id": user_id, "key": "last_activity_utc"})
            if row:
                session.delete(row)
    finally:
        _sync_suspend_token.__exit__(None, None, None)
    _touch_account_session(active=False)


def remove_foreign_online_accounts(owner_user_id, account_uid):
    safe_uid = _safe_account_uid(account_uid)
    _sync_suspend_token = suspend_sync()
    _sync_suspend_token.__enter__()
    try:
        with session_scope() as session:
            token_rows = session.execute(
                select(UserSetting.user_id, UserSetting.value).where(UserSetting.key == "api_access_token")
            ).all()
            foreign_ids = {
                user_id
                for user_id, token in token_rows
                if user_id != owner_user_id and _jwt_subject(token) and _jwt_subject(token) != safe_uid
            }
            for foreign_id in foreign_ids:
                foreign_user = session.get(User, foreign_id)
                if not foreign_user:
                    continue
                _reassign_user_references(session, foreign_id, owner_user_id)
                session.delete(foreign_user)
    finally:
        _sync_suspend_token.__exit__(None, None, None)


def restore_recent_account_user(max_days=7, max_minutes=None):
    data = _read_account_session()
    user_uid = data.get("user_uid")
    email = _normalize_email(data.get("email"))
    if not user_uid or not email:
        return None

    if is_account_session_expired(data, max_days=max_days):
        clear_account_session()
        return None

    if max_minutes is not None:
        activity_value = data.get("last_activity_utc")
        if not activity_value:
            return None
        try:
            activity_at = datetime.strptime(str(activity_value), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - activity_at > timedelta(minutes=max_minutes):
                return None
        except ValueError:
            return None

    activation = activate_account_database(user_uid, email=email, allow_legacy_import=False)
    owner_data = {
        "user_uid": user_uid,
        "email": email,
        "display_name": data.get("display_name"),
    }
    init_db(account_owner=owner_data, seed_defaults=False)
    if activation.get("database_created"):
        mark_server_bootstrap_required()
    user = sync_online_user(
        email,
        display_name=data.get("display_name"),
        role="admin",
        access_token=data.get("api_access_token"),
        user_uid=user_uid,
    )
    remove_foreign_online_accounts(user["id"], user_uid)
    restored = dict(user)
    restored["role"] = "cashier"
    restored["api_access_token"] = data.get("api_access_token")
    restored["api_user_id"] = data.get("api_user_id")
    restored["api_user_uid"] = user_uid
    restored["local_database_created"] = bool(activation.get("database_created"))
    return Row(restored)


def get_recent_activity_user(max_minutes=15):
    with session_scope() as session:
        latest = session.scalar(
            select(UserSetting)
            .where(UserSetting.key == "last_activity_utc")
            .order_by(UserSetting.value.desc())
            .limit(1)
        )
        if not latest or not latest.user_id or not latest.value:
            return None
        try:
            activity_at = datetime.strptime(str(latest.value), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
        if datetime.now(timezone.utc) - activity_at > timedelta(minutes=max_minutes):
            return None
        user = session.get(User, latest.user_id)
        if not user:
            return None
        return _row_from_model(user)


def get_recent_login_user(max_minutes=15):
    with session_scope() as session:
        latest = session.scalar(select(LoginLog).order_by(LoginLog.logged_at.desc(), LoginLog.id.desc()).limit(1))
        if not latest or not latest.user_id or not latest.logged_at:
            return None
        try:
            logged_at = datetime.strptime(str(latest.logged_at), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
        if datetime.now(timezone.utc) - logged_at > timedelta(minutes=max_minutes):
            return None
        user = session.get(User, latest.user_id)
        if not user:
            return None
        return _row_from_model(user)


def get_login_logs(limit=500):
    with session_scope() as session:
        rows = _rows_from_models(
            session.scalars(select(LoginLog).order_by(LoginLog.logged_at.desc(), LoginLog.id.desc()).limit(limit)).all()
        )
        for row in rows:
            row["logged_at"] = _utc_to_local(row["logged_at"])
        return rows


def clear_login_logs():
    with session_scope() as session:
        session.query(LoginLog).delete()


def get_users():
    with session_scope() as session:
        rows = session.scalars(select(User).order_by(User.role, User.email, User.username)).all()
        return [Row(dict(id=u.id, username=u.username, email=u.email, role=u.role, created_at=u.created_at)) for u in rows]


def get_debt_cashiers():
    """Return staff users without the online account owner."""
    with session_scope() as session:
        owner_ids = select(UserSetting.user_id).where(UserSetting.key == "api_user_uid")
        rows = session.scalars(
            select(User)
            .where(User.id.not_in(owner_ids))
            .order_by(User.role, User.email, User.username)
        ).all()
        return [Row(dict(id=u.id, username=u.username, email=u.email, role=u.role, created_at=u.created_at)) for u in rows]


def add_user(email=None, password=None, role="cashier", username=None):
    email = _normalize_email(email) if email else None
    username = (username or email or "").strip()
    if not username:
        raise AppError("To'liq ismni kiriting.")
    if role not in ("admin", "cashier"):
        raise AppError("Role noto'g'ri.")
    password = password or secrets.token_urlsafe(32)
    user_id = None
    with session_scope() as session:
        try:
            user = User(username=username, email=email, password=_hash_password(password), role=role)
            session.add(user)
            session.flush()
            user_id = user.id
            tombstone = session.get(SyncTombstone, {"table_name": "users", "local_id": str(user.id)})
            if tombstone:
                session.delete(tombstone)
        except IntegrityError as exc:
            if email and "email" in str(exc).lower():
                raise AppError("Bu email allaqachon mavjud.") from exc
            raise AppError("Bu foydalanuvchi allaqachon mavjud.") from exc
    log_activity(
        "user_added",
        f"Yangi foydalanuvchi: {username}",
        f"Roli: {role.title()}" + (f" | Email: {email}" if email else ""),
        level="success",
        target="users",
        badge="Kassir" if role == "cashier" else "Admin",
    )
    return user_id


def update_user(user_id, email=None, password=None, role="cashier", username=None):
    email = _normalize_email(email) if email else None
    username = (username or email or "").strip()
    if not username:
        raise AppError("To'liq ismni kiriting.")
    if role not in ("admin", "cashier"):
        raise AppError("Role noto'g'ri.")
    with session_scope() as session:
        try:
            user = session.get(User, user_id)
            if user:
                owner_uid = session.get(UserSetting, {"user_id": user_id, "key": "api_user_uid"})
                if owner_uid and ((email and email != user.email) or role != "admin"):
                    raise AppError("Asosiy account emaili va admin holatini bu yerdan o'zgartirib bo'lmaydi.")
                user.username = username
                if email is not None:
                    user.email = email
                user.role = role
                if password:
                    user.password = _hash_password(password)
                    activity = session.get(UserSetting, {"user_id": user_id, "key": "last_activity_utc"})
                    if activity:
                        session.delete(activity)
                session.flush()
        except IntegrityError as exc:
            if email and "email" in str(exc).lower():
                raise AppError("Bu email allaqachon mavjud.") from exc
            raise AppError("Bu foydalanuvchi allaqachon mavjud.") from exc
    log_activity(
        "user_updated",
        f"Foydalanuvchi yangilandi: {username}",
        f"Roli: {role.title()}" + (f" | Email: {email}" if email else ""),
        level="info",
        target="users",
        badge="Yangilandi",
    )


def delete_user(user_id):
    u_name = None
    u_email = None
    u_role = "cashier"
    with session_scope() as session:
        admins = session.scalar(select(func.count(User.id)).where(User.role == "admin"))
        user = session.get(User, user_id)
        owner_uid = session.get(UserSetting, {"user_id": user_id, "key": "api_user_uid"})
        if owner_uid:
            raise AppError("Asosiy accountni kassirlar bo'limidan o'chirib bo'lmaydi.")
        if user and user.role == "admin" and admins <= 1:
            raise AppError("Oxirgi adminni o'chirib bo'lmaydi.")
        if user:
            u_name = user.username
            u_email = user.email
            u_role = user.role
            has_history = any((
                session.scalar(select(func.count(LoginLog.id)).where(LoginLog.user_id == user_id)),
                session.scalar(select(func.count(Expense.id)).where(Expense.user_id == user_id)),
                session.scalar(select(func.count(Sale.id)).where(Sale.cashier_id == user_id)),
                session.scalar(
                    select(func.count(InventoryCheckSession.id)).where(InventoryCheckSession.started_by == user_id)
                ),
            ))
            if has_history:
                raise AppError("Bu foydalanuvchi amallar tarixida bor, uni o'chirib bo'lmaydi.")
            session.merge(SyncTombstone(table_name="users", local_id=str(user.id), deleted_at=_utc_now()))
            session.delete(user)
    if u_name:
        log_activity(
            "user_deleted",
            f"Foydalanuvchi o'chirildi: {u_name}",
            f"Email: {u_email} | Roli: {u_role.title()}",
            level="danger",
            target="users",
            badge="O'chirildi",
        )


def get_low_stock_products(threshold=5):
    with session_scope() as session:
        stmt = _product_select().where(Product.is_deleted == 0, Product.stock <= threshold).order_by(Product.stock.asc(), Product.name.asc())
        rows = session.execute(stmt).all()
        return [_product_row(p, c, t, s, u, r) for p, c, t, s, u, r in rows]


def mark_notifications_as_read(notification_ids, user_id=None):
    """Remember what this person has already seen, past the next restart.

    Deliberately not synchronised: what one cashier has read says nothing
    about what another has, and the table is per-user for the same reason.
    """
    if not notification_ids:
        return
    now = _utc_now()
    for nid in notification_ids:
        _SESSION_READ_IDS.add(str(nid))
    if not user_id:
        return
    try:
        with session_scope() as session:
            for nid in notification_ids:
                key = str(nid)
                existing = session.scalar(
                    select(NotificationRead).where(
                        NotificationRead.user_id == user_id,
                        NotificationRead.notification_id == key,
                    )
                )
                if existing is None:
                    session.add(NotificationRead(
                        user_id=user_id, notification_id=key, read_at=now
                    ))
    except Exception:
        # Losing the record only means the badge lights up again later.
        pass


def get_read_notification_ids(user_id=None):
    known = set(_SESSION_READ_IDS)
    if not user_id:
        return known
    try:
        with session_scope() as session:
            known.update(
                str(value) for value in session.scalars(
                    select(NotificationRead.notification_id)
                    .where(NotificationRead.user_id == user_id)
                )
            )
    except Exception:
        pass
    return known


def get_unread_notifications_count(user_id=None, threshold=5):
    data = get_notifications_data(threshold=threshold, user_id=user_id)
    return data.get("summary", {}).get("unread_total", 0)


def get_notifications_data(threshold=5, user_id=None):
    read_ids = get_read_notification_ids(user_id=user_id)
    low_stock = get_low_stock_products(threshold=threshold)
    debtors = get_all_debtors()
    debtors_with_debt = [d for d in debtors if (d.get("balance") or 0) > 0]
    sync_status = get_sync_status()
    activities = get_recent_activities(limit=60)
    notifications = []
    unread_by_type = {}

    # 1. Activities across the whole system
    product_act_count = 0
    sales_act_count = 0
    for act in activities:
        target = act.get("target") or "products"
        if target == "products":
            product_act_count += 1
        elif target == "sales":
            sales_act_count += 1

        nid = f"act_{act['id']}"
        is_read = nid in read_ids
        if not is_read:
            unread_by_type[target] = unread_by_type.get(target, 0) + 1

        notifications.append({
            "id": nid,
            "type": target,
            "level": act.get("level", "info"),
            "title": act.get("title", ""),
            "message": act.get("message", ""),
            "target": target,
            "created_at": act.get("created_at") or "",
            "badge": act.get("badge", "Tizim"),
            "is_read": is_read,
        })

    # 2. Low stock
    for p in low_stock:
        is_empty = (p.get("stock") or 0) <= 0
        nid = f"stock_{p['id']}"
        is_read = nid in read_ids
        if not is_read:
            unread_by_type["stock"] = unread_by_type.get("stock", 0) + 1

        notifications.append({
            "id": nid,
            "type": "stock",
            "level": "danger" if is_empty else "warning",
            "title": f"Tugadi: {p['name']}" if is_empty else f"Kam qoldi: {p['name']}",
            "message": f"Shtrix-kod: {p.get('barcode') or '-'} | Qoldiq: {p.get('stock', 0)} {p.get('unit', 'dona')} | Sotish narxi: {p.get('price', 0):,.0f} {p.get('price_currency', 'UZS')}",
            "target": "products",
            "created_at": p.get("created_at") or "",
            "badge": "Tugagan" if is_empty else "Kam qolgan",
            "is_read": is_read,
        })

    # 3. Debtors
    for d in debtors_with_debt:
        nid = f"debt_{d['id']}"
        is_read = nid in read_ids
        if not is_read:
            unread_by_type["supplier_debts"] = unread_by_type.get("supplier_debts", 0) + 1

        notifications.append({
            "id": nid,
            "type": "supplier_debts",
            "level": "warning",
            "title": f"Qarzdor mijoz: {d['name']}",
            "message": f"Qarzdorlik summasi: {d.get('balance', 0):,.0f} {d.get('debt_currency', 'UZS')} | Tel: {d.get('phone') or 'Kiritilmagan'}",
            "target": "supplier_debts",
            "created_at": d.get("created_at") or "",
            "badge": "Qarz",
            "is_read": is_read,
        })

    # 4. Sync
    pending = sync_status.get("pending_change_count", 0) if sync_status else 0
    if pending > 0:
        nid = "sync_pending"
        is_read = nid in read_ids
        if not is_read:
            unread_by_type["system"] = unread_by_type.get("system", 0) + 1
        notifications.append({
            "id": nid,
            "type": "system",
            "level": "info",
            "title": "Sinxronizatsiya kutilmoqda",
            "message": f"Serverga yuborilmagan {pending} ta lokal o'zgarishlar mavjud. Server bilan sinxronlash tavsiya etiladi.",
            "target": "sales",
            "created_at": "",
            "badge": "Sync",
            "is_read": is_read,
        })

    unread_total = sum(1 for n in notifications if not n["is_read"])

    return {
        "notifications": notifications,
        "summary": {
            "total": len(notifications),
            "unread_total": unread_total,
            "unread_by_type": unread_by_type,
            "product_activity_count": product_act_count,
            "sales_activity_count": sales_act_count,
            "low_stock_count": len(low_stock),
            "debtors_count": len(debtors_with_debt),
            "pending_sync_count": pending,
        },
    }
