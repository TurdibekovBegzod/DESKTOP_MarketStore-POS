import base64
import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from sqlalchemy import (
    Column,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    and_,
    case,
    create_engine,
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

Base = declarative_base()
_ENGINE = None
_ENGINE_PATH = None
_SessionLocal = None
_SYNC_SUSPENDED = False
_ACTIVE_ACCOUNT_UID = None

SYNC_TABLES = (
    "users",
    "categories",
    "currencies",
    "app_settings",
    "product_sections",
    "product_templates",
    "product_template_fields",
    "products",
    "product_attributes",
    "customers",
    "suppliers",
    "supplier_debt_movements",
    "debtors",
    "debtor_debt_movements",
    "expense_categories",
    "expenses",
    "sales",
    "sale_items",
    "stock_movements",
    "inventory_check_sessions",
    "inventory_check_items",
    "finance_manual_movements",
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
    if _SYNC_SUSPENDED:
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
    if _SYNC_SUSPENDED:
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
    with sqlite3.connect(source_path) as source, sqlite3.connect(target_path) as target:
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
            session.commit()
            entries = session.info.get("sync_outbox_entries")
            if not _SYNC_SUSPENDED:
                if entries:
                    _record_sync_outbox_entries(entries)
                _mark_sync_dirty()
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


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    email = Column(String, unique=True)
    password = Column(String, nullable=False)
    role = Column(String, default="cashier")
    created_at = Column(String, server_default=text("CURRENT_TIMESTAMP"))


class LoginLog(Base):
    __tablename__ = "login_logs"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    username = Column(String, nullable=False)
    role = Column(String, nullable=False)
    logged_at = Column(String, server_default=text("CURRENT_TIMESTAMP"))


class Category(Base):
    __tablename__ = "categories"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)


class Currency(Base):
    __tablename__ = "currencies"
    id = Column(Integer, primary_key=True)
    code = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    rate_to_uzs = Column(Float, nullable=False, default=1)
    is_base = Column(Integer, default=0)
    updated_at = Column(String, server_default=text("CURRENT_TIMESTAMP"))


class AppSetting(Base):
    __tablename__ = "app_settings"
    key = Column(String, primary_key=True)
    value = Column(String)


class UserSetting(Base):
    __tablename__ = "user_settings"
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    key = Column(String, primary_key=True)
    value = Column(String)


class SyncTombstone(Base):
    __tablename__ = "sync_tombstones"
    table_name = Column(String, primary_key=True)
    local_id = Column(String, primary_key=True)
    deleted_at = Column(String, nullable=False)


class ProductTemplate(Base):
    __tablename__ = "product_templates"
    id = Column(Integer, primary_key=True)
    section_id = Column(Integer, ForeignKey("product_sections.id", ondelete="CASCADE"))
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
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    original_name = Column(String)
    is_deleted = Column(Integer, default=0)
    deleted_at = Column(String)
    purge_after = Column(String)
    created_at = Column(String, server_default=text("CURRENT_TIMESTAMP"))


class ProductTemplateField(Base):
    __tablename__ = "product_template_fields"
    id = Column(Integer, primary_key=True)
    template_id = Column(Integer, ForeignKey("product_templates.id", ondelete="CASCADE"), nullable=False)
    name = Column(String, nullable=False)
    field_type = Column(String, default="text")
    required = Column(Integer, default=0)
    sort_order = Column(Integer, default=0)


class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True)
    barcode = Column(String, unique=True)
    original_barcode = Column(String)
    name = Column(String, nullable=False)
    section_id = Column(Integer, ForeignKey("product_sections.id"))
    template_id = Column(Integer, ForeignKey("product_templates.id"))
    supplier_id = Column(Integer, ForeignKey("suppliers.id"))
    category_id = Column(Integer, ForeignKey("categories.id"))
    created_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
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
    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    field_id = Column(Integer, ForeignKey("product_template_fields.id", ondelete="CASCADE"), nullable=False)
    value = Column(String)
    __table_args__ = (UniqueConstraint("product_id", "field_id"),)


class Customer(Base):
    __tablename__ = "customers"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    phone = Column(String)
    email = Column(String)
    balance = Column(Float, default=0)
    total_purchases = Column(Float, default=0)
    created_at = Column(String, server_default=text("CURRENT_TIMESTAMP"))


class Supplier(Base):
    __tablename__ = "suppliers"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    phone = Column(String)
    note = Column(String)
    debt_currency = Column(String, default="UZS")
    balance = Column(Float, default=0)
    total_received = Column(Float, default=0)
    created_at = Column(String, server_default=text("CURRENT_TIMESTAMP"))


class SupplierDebtMovement(Base):
    __tablename__ = "supplier_debt_movements"
    id = Column(Integer, primary_key=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=False)
    type = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    note = Column(String)
    created_at = Column(String, server_default=text("CURRENT_TIMESTAMP"))


class Debtor(Base):
    __tablename__ = "debtors"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    name = Column(String, nullable=False)
    phone = Column(String)
    note = Column(String)
    debt_currency = Column(String, default="UZS")
    balance = Column(Float, default=0)
    total_given = Column(Float, default=0)
    created_at = Column(String, server_default=text("CURRENT_TIMESTAMP"))


class DebtorDebtMovement(Base):
    __tablename__ = "debtor_debt_movements"
    id = Column(Integer, primary_key=True)
    debtor_id = Column(Integer, ForeignKey("debtors.id"), nullable=False)
    type = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    note = Column(String)
    created_at = Column(String, server_default=text("CURRENT_TIMESTAMP"))


class ExpenseCategory(Base):
    __tablename__ = "expense_categories"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)


class Expense(Base):
    __tablename__ = "expenses"
    id = Column(Integer, primary_key=True)
    category_id = Column(Integer, ForeignKey("expense_categories.id"))
    user_id = Column(Integer, ForeignKey("users.id"))
    cashier_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    amount = Column(Float, nullable=False)
    currency_code = Column(String, default="UZS")
    description = Column(String)
    created_at = Column(String, server_default=text("CURRENT_TIMESTAMP"))


class Sale(Base):
    __tablename__ = "sales"
    id = Column(Integer, primary_key=True)
    customer_id = Column(Integer, ForeignKey("customers.id"))
    cashier_id = Column(Integer, ForeignKey("users.id"))
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
    created_at = Column(String, server_default=text("CURRENT_TIMESTAMP"))


class SaleItem(Base):
    __tablename__ = "sale_items"
    id = Column(Integer, primary_key=True)
    sale_id = Column(Integer, ForeignKey("sales.id"))
    product_id = Column(Integer, ForeignKey("products.id"))
    quantity = Column(Integer, nullable=False)
    returned_quantity = Column(Integer, default=0)
    returned_at = Column(String)
    price = Column(Float, nullable=False)
    subtotal = Column(Float, nullable=False)


class StockMovement(Base):
    __tablename__ = "stock_movements"
    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey("products.id"))
    type = Column(String, nullable=False)
    quantity = Column(Integer, nullable=False)
    note = Column(String)
    created_at = Column(String, server_default=text("CURRENT_TIMESTAMP"))


class InventoryCheckSession(Base):
    __tablename__ = "inventory_check_sessions"
    id = Column(Integer, primary_key=True)
    started_by = Column(Integer, ForeignKey("users.id"))
    status = Column(String, nullable=False, default="active")
    started_at = Column(String, server_default=text("CURRENT_TIMESTAMP"))
    finished_at = Column(String)


class InventoryCheckItem(Base):
    __tablename__ = "inventory_check_items"
    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("inventory_check_sessions.id", ondelete="CASCADE"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"))
    product_name = Column(String, nullable=False)
    barcode = Column(String)
    expected_stock = Column(Integer, default=0)
    checked_quantity = Column(Integer, default=0)
    checked_at = Column(String)
    __table_args__ = (UniqueConstraint("session_id", "product_id"),)


class FinanceManualMovement(Base):
    __tablename__ = "finance_manual_movements"
    id = Column(Integer, primary_key=True)
    movement_date = Column(String, nullable=False)
    kind = Column(String, nullable=False)
    operation = Column(String, nullable=False, default="+")
    amount = Column(Float, nullable=False)
    currency_code = Column(String, default="UZS")
    rate_to_uzs = Column(Float, default=1)
    created_at = Column(String, server_default=text("CURRENT_TIMESTAMP"))


class ActivityLog(Base):
    __tablename__ = "activity_logs"
    id = Column(Integer, primary_key=True)
    action = Column(String, nullable=False)
    title = Column(String, nullable=False)
    message = Column(String, nullable=False)
    level = Column(String, default="info")
    target = Column(String, default="products")
    badge = Column(String, default="Mahsulot")
    created_at = Column(String, server_default=text("CURRENT_TIMESTAMP"))


class NotificationRead(Base):
    __tablename__ = "notification_reads"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
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
            "section_id": "ALTER TABLE products ADD COLUMN section_id INTEGER REFERENCES product_sections(id)",
            "original_barcode": "ALTER TABLE products ADD COLUMN original_barcode TEXT",
            "template_id": "ALTER TABLE products ADD COLUMN template_id INTEGER REFERENCES product_templates(id)",
            "supplier_id": "ALTER TABLE products ADD COLUMN supplier_id INTEGER REFERENCES suppliers(id)",
            "created_by_user_id": "ALTER TABLE products ADD COLUMN created_by_user_id INTEGER REFERENCES users(id)",
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
            "section_id": "ALTER TABLE product_templates ADD COLUMN section_id INTEGER REFERENCES product_sections(id)",
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
            "user_id": "ALTER TABLE debtors ADD COLUMN user_id INTEGER",
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
            "started_by": "ALTER TABLE inventory_check_sessions ADD COLUMN started_by INTEGER REFERENCES users(id)",
            "status": "ALTER TABLE inventory_check_sessions ADD COLUMN status TEXT DEFAULT 'active'",
            "started_at": "ALTER TABLE inventory_check_sessions ADD COLUMN started_at TEXT",
            "finished_at": "ALTER TABLE inventory_check_sessions ADD COLUMN finished_at TEXT",
        },
        "inventory_check_items": {
            "checked_quantity": "ALTER TABLE inventory_check_items ADD COLUMN checked_quantity INTEGER DEFAULT 0",
            "checked_at": "ALTER TABLE inventory_check_items ADD COLUMN checked_at TEXT",
        },
        "expenses": {
            "user_id": "ALTER TABLE expenses ADD COLUMN user_id INTEGER REFERENCES users(id)",
            "cashier_id": "ALTER TABLE expenses ADD COLUMN cashier_id INTEGER REFERENCES users(id) ON DELETE SET NULL",
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
}


def run_migrations():
    engine = _get_engine()
    with engine.begin() as conn:
        _ensure_schema_migrations_table(conn)
        applied = {row[0] for row in conn.exec_driver_sql("SELECT version FROM schema_migrations").fetchall()}
        pending = [(version, description) for version, description in MIGRATIONS if version not in applied]
        if not pending:
            return []
        _backup_database_before_migration(conn)
        applied_now = []
        for version, description in pending:
            MIGRATION_FUNCTIONS[version](conn)
            _mark_migration_applied(conn, version, description)
            applied_now.append(version)
        return applied_now


def _default_product_section_id(session):
    section = session.scalar(
        select(ProductSection)
        .where(func.coalesce(ProductSection.is_deleted, 0) == 0)
        .order_by(ProductSection.id)
    )
    if section is None:
        section = ProductSection(name="Umumiy")
        session.add(section)
        session.flush()
    return section.id


def _reassign_user_references(session, source_user_id, target_user_id):
    if source_user_id == target_user_id:
        return
    session.execute(update(LoginLog).where(LoginLog.user_id == source_user_id).values(user_id=target_user_id))
    session.execute(update(Expense).where(Expense.user_id == source_user_id).values(user_id=target_user_id))
    session.execute(update(Sale).where(Sale.cashier_id == source_user_id).values(cashier_id=target_user_id))
    session.execute(
        update(InventoryCheckSession)
        .where(InventoryCheckSession.started_by == source_user_id)
        .values(started_by=target_user_id)
    )
    session.query(UserSetting).filter(UserSetting.user_id == source_user_id).delete(synchronize_session=False)


def init_db(account_owner=None, seed_defaults=True):
    global _SYNC_SUSPENDED
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

    previous_sync_state = _SYNC_SUSPENDED
    if account_owner:
        _SYNC_SUSPENDED = True
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
                if owner is None:
                    owner = User(
                        username=(account_owner.get("display_name") or owner_email).strip(),
                        email=owner_email,
                        password=_hash_password(secrets.token_urlsafe(32)),
                        role="admin",
                    )
                    session.add(owner)
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
                        session.add(Category(name=name))

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
                    session.add(Currency(code=code, name=name, rate_to_uzs=rate, is_base=is_base))

            if seed_defaults:
                for name in ["Ijara", "Transport", "Kommunal", "Ish haqi", "Kassir", "Boshqa"]:
                    if not session.scalar(select(ExpenseCategory).where(ExpenseCategory.name == name)):
                        session.add(ExpenseCategory(name=name))

            if seed_defaults and session.scalar(select(func.count(ProductTemplate.id))) == 0:
                template = ProductTemplate(name="Umumiy mahsulot", section_id=default_section_id)
                session.add(template)
                session.flush()
                for order, field_name in enumerate(["Brend", "Model", "Rang"]):
                    session.add(ProductTemplateField(template_id=template.id, name=field_name, sort_order=order))
        with engine.begin() as conn:
            conn.exec_driver_sql("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email ON users(email)")
        migrate_finance_manual_json()
    finally:
        _SYNC_SUSPENDED = previous_sync_state


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


def _mark_sync_dirty():
    try:
        with _get_engine().begin() as conn:
            _sync_state_set(conn, "last_dirty_at", _utc_now())
            _sync_state_set(conn, "pending_change_count", str(_sync_state_int(conn, "pending_change_count") + 1))
    except Exception:
        pass


def get_sync_device_key():
    with _get_engine().begin() as conn:
        key = _sync_state_get(conn, "device_key")
        if not key:
            key = "desktop-" + secrets.token_hex(12)
            _sync_state_set(conn, "device_key", key)
        return key


def _ensure_sync_outbox_table(conn):
    conn.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS sync_outbox (
            table_name TEXT NOT NULL,
            local_id TEXT NOT NULL,
            action TEXT NOT NULL DEFAULT 'upsert',
            updated_at TEXT NOT NULL,
            PRIMARY KEY (table_name, local_id)
        )
    """)


def _record_sync_outbox_entries(entries):
    if not entries:
        return
    now = _utc_now()
    try:
        with _get_engine().begin() as conn:
            _ensure_sync_outbox_table(conn)
            for table_name, local_id, action in entries:
                conn.exec_driver_sql(
                    "INSERT OR REPLACE INTO sync_outbox (table_name, local_id, action, updated_at) VALUES (?, ?, ?, ?)",
                    (table_name, str(local_id), action, now),
                )
    except Exception:
        pass


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


def export_sync_records(incremental=False):
    now = _utc_now()
    device_key = get_sync_device_key()
    records = []
    with _get_engine().begin() as conn:
        _ensure_sync_outbox_table(conn)

        # Incremental mode: only export records that were created, modified or deleted
        if incremental and not is_server_reseed_required():
            outbox_rows = conn.exec_driver_sql(
                "SELECT table_name, local_id, action, updated_at FROM sync_outbox"
            ).mappings().all()
            for item in outbox_rows:
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
                    pk_col = "key" if table_name == "app_settings" else "code" if table_name == "currencies" else "id"
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
                    records.append({
                        "table_name": tombstone["table_name"],
                        "local_id": str(tombstone["local_id"]),
                        "data": {},
                        "local_updated_at": tombstone["deleted_at"],
                        "deleted_at": tombstone["deleted_at"],
                        "source_device_key": device_key,
                    })
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
                local_id = str(data.get("id") if "id" in data else data.get("key"))
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
    return records


def import_sync_records(records):
    global _SYNC_SUSPENDED
    if not records:
        return 0
    imported = 0
    engine = _get_engine()
    previous = _SYNC_SUSPENDED
    _SYNC_SUSPENDED = True
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
            for record in records:
                table_name = record.get("table_name")
                if table_name not in SYNC_TABLES or not _has_table(conn, table_name):
                    continue
                data = record.get("data") or {}
                if not isinstance(data, dict):
                    continue
                columns = _table_columns(conn, table_name)
                filtered = {key: value for key, value in data.items() if key in columns}
                if not filtered:
                    local_id = record.get("local_id")
                    if record.get("deleted_at") and local_id:
                        conn.exec_driver_sql(
                            f"DELETE FROM {_quote_identifier(table_name)} WHERE id=?",
                            (local_id,),
                        )
                        imported += 1
                    continue
                if record.get("deleted_at") and not data and "id" in filtered:
                    conn.exec_driver_sql(
                        f"DELETE FROM {_quote_identifier(table_name)} WHERE id=?",
                        (filtered["id"],),
                    )
                    imported += 1
                    continue
                quoted_table = _quote_identifier(table_name)
                quoted_columns = ", ".join(_quote_identifier(column) for column in filtered)
                placeholders = ", ".join("?" for _ in filtered)
                values = tuple(filtered.values())
                conn.exec_driver_sql(
                    f"INSERT OR REPLACE INTO {quoted_table} ({quoted_columns}) VALUES ({placeholders})",
                    values,
                )
                if _has_table(conn, "sync_tombstones"):
                    conn.exec_driver_sql(
                        "DELETE FROM sync_tombstones WHERE table_name=? AND local_id=?",
                        (table_name, str(record.get("local_id") or "")),
                    )
                imported += 1
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")
            _sync_state_set(conn, "last_pull_at", _utc_now())
    finally:
        _SYNC_SUSPENDED = previous
    return imported


def mark_sync_pushed():
    with _get_engine().begin() as conn:
        now = _utc_now()
        _ensure_sync_outbox_table(conn)
        _sync_state_set(conn, "last_push_at", now)
        _sync_state_set(conn, "last_dirty_at", "")
        _sync_state_set(conn, "pending_change_count", "0")
        conn.exec_driver_sql("DELETE FROM sync_outbox")
        if _has_table(conn, "sync_tombstones"):
            conn.exec_driver_sql("DELETE FROM sync_tombstones")


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
                .order_by(ProductSection.id)
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


def log_activity(action, title, message, level="info", target="products", badge="Mahsulot"):
    global _ACTIVITY_COUNTER
    _ACTIVITY_COUNTER += 1
    item = {
        "id": _ACTIVITY_COUNTER,
        "action": action,
        "title": title,
        "message": message,
        "level": level,
        "target": target,
        "badge": badge,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    _SESSION_ACTIVITIES.insert(0, item)
    if len(_SESSION_ACTIVITIES) > 300:
        _SESSION_ACTIVITIES.pop()

    for listener in list(_ACTIVITY_LISTENERS):
        try:
            listener(action, title, message, level, target, badge)
        except Exception:
            pass


def get_recent_activities(limit=50):
    return [Row(act) for act in _SESSION_ACTIVITIES[:limit]]


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
            for key, value in data.items():
                if hasattr(product, key) and key != "id":
                    setattr(product, key, value)
            session.flush()
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


def _cleanup_unfinalized_sales_for_product(session, product_id):
    items = session.execute(
        select(SaleItem)
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(SaleItem.product_id == product_id, func.coalesce(Sale.is_finalized, 0) == 0)
    ).scalars().all()
    affected_sale_ids = set()
    for item in items:
        affected_sale_ids.add(item.sale_id)
        session.merge(SyncTombstone(
            table_name="sale_items",
            local_id=item.id,
            deleted_at=_utc_now(),
        ))
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
                    local_id=sale.id,
                    deleted_at=_utc_now(),
                ))
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
            .order_by(ProductTemplate.id)
        )
        if existing:
            return existing.id

        source = session.scalar(
            select(ProductTemplate)
            .where(func.coalesce(ProductTemplate.is_deleted, 0) == 0)
            .order_by(ProductTemplate.id)
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
        target="stock",
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


def finish_inventory_check(session_id):
    counts = get_inventory_check_counts(session_id)
    with session_scope() as session:
        check = session.get(InventoryCheckSession, session_id)
        if not check or check.status != "active":
            raise AppError("Aktiv checking jarayoni topilmadi.")
        check.status = "finished"
        check.finished_at = _now()
    log_activity(
        "checking_finished",
        "Tekshiruv (Checking) yakunlandi",
        f"Tekshirildi: {counts.get('checked_count', 0)} ta | Qolgan: {counts.get('unchecked_count', 0)} ta",
        level="success",
        target="checking",
        badge="Tugallandi",
    )
    return counts


def create_sale(customer_id, cashier_id, items, total, discount, paid, payment_method, currency_code="UZS", exchange_rate=1, paid_original=None, customer_name=None, customer_phone=None, is_finalized=0):
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
            sale_item = SaleItem(
                sale_id=sale.id,
                product_id=product_id,
                quantity=quantity,
                price=item["price"],
                subtotal=item["subtotal"],
            )
            session.add(sale_item)
            session.add(StockMovement(product_id=product_id, type="sotuv", quantity=-quantity, note=f"Sotuv #{sale.id}"))
        if customer_id:
            customer = session.get(Customer, customer_id)
            if customer:
                customer.total_purchases = (customer.total_purchases or 0) + payable
                if payment_method == "qarz":
                    customer.balance = (customer.balance or 0) + payable
        sale_id = sale.id
    log_activity(
        "sale_created",
        f"Sotuv amalga oshirildi (#{sale_id})",
        f"{len(items)} xil mahsulot sotildi | Jami: {total:,.0f} {currency_code} ({payment_method})",
        level="success",
        target="sales",
        badge="Sotildi",
    )
    return sale_id


def finalize_sale(sale_id, cashier_reward=0.0):
    sale_ref = None
    with session_scope() as session:
        sale = session.get(Sale, sale_id)
        if not sale:
            raise AppError("Sotuv topilmadi.")
        if sale.is_finalized:
            return
        sale.is_finalized = 1
        sale.cashier_reward = float(cashier_reward or 0.0)
        sale.finalized_at = _utc_now()
        sale_ref = sale.id
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
            cost_val = (product.cost if product else 0) or 0
            price_val = item.price or 0
            sold_unit_price = (item_total_after_discount / active_quantity) if active_quantity > 0 else price_val
            result.append(Row(dict(
                sale_item_id=item.id,
                sale_id=item.sale_id,
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
        session.query(SaleItem).delete()
        session.query(Sale).delete()


def _return_sale_item_in_session(session, item, quantity, note=""):
    if quantity <= 0:
        raise AppError("Qaytarish miqdori 0 dan katta bo'lishi kerak.")
    sale = session.get(Sale, item.sale_id)
    if sale is None:
        raise AppError("Sotuv topilmadi.")
    available = item.quantity - (item.returned_quantity or 0)
    if quantity > available:
        raise AppError(f"Qaytarish miqdori ko'p. Qaytarish mumkin: {available}.")
    refund = item.price * quantity
    rate = sale.exchange_rate or 1
    active_sale_total = session.scalar(
        select(func.coalesce(func.sum((SaleItem.quantity - func.coalesce(SaleItem.returned_quantity, 0)) * SaleItem.price), 0))
        .where(SaleItem.sale_id == item.sale_id)
    ) or 0
    discount_refund = (sale.discount or 0) * (refund / active_sale_total) if active_sale_total > 0 else 0
    net_refund = max(0, refund - discount_refund)
    reward_refund = (sale.cashier_reward or 0.0) * (refund / active_sale_total) if active_sale_total > 0 else (sale.cashier_reward or 0.0)
    sale.cashier_reward = max(0.0, (sale.cashier_reward or 0.0) - reward_refund)
    item.returned_quantity = (item.returned_quantity or 0) + quantity
    item.returned_at = _utc_now()
    product = session.get(Product, item.product_id)
    if product:
        product.stock = (product.stock or 0) + quantity
        product.is_deleted = 0
    session.add(StockMovement(
        product_id=item.product_id,
        type="qaytarish",
        quantity=quantity,
        note=note or f"Sotuv #{item.sale_id} qaytarildi",
    ))
    sale.total = max((sale.total or 0) - refund, 0)
    sale.discount = min(max((sale.discount or 0) - discount_refund, 0), sale.total or 0)
    if sale.payment_method != "qarz":
        sale.paid = max((sale.paid or 0) - net_refund, 0)
        sale.paid_original = max((sale.paid_original or 0) - net_refund / rate, 0)
    if sale.customer_id:
        customer = session.get(Customer, sale.customer_id)
        if customer:
            customer.total_purchases = max((customer.total_purchases or 0) - net_refund, 0)
            if sale.payment_method == "qarz":
                customer.balance = max((customer.balance or 0) - net_refund, 0)


def return_sale_item(sale_item_id, quantity, note=""):
    with session_scope() as session:
        item = session.get(SaleItem, sale_item_id)
        if item is None:
            raise AppError("Sotuv arxivi topilmadi.")
        _return_sale_item_in_session(session, item, quantity, note)


def delete_sale_item(sale_item_id):
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
                note=f"Sotuv #{sale_id} yozuvi o'chirildi",
            )
        session.merge(SyncTombstone(
            table_name="sale_items",
            local_id=str(item.id),
            deleted_at=_utc_now(),
        ))
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
                session.delete(sale)


def _sale_cost(session, sale_id):
    return session.scalar(
        select(func.coalesce(func.sum((SaleItem.quantity - func.coalesce(SaleItem.returned_quantity, 0)) * Product.cost), 0))
        .select_from(SaleItem)
        .join(Product, Product.id == SaleItem.product_id)
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
            row["cost"] += qty * (product.cost or 0)
            row["profit"] += qty * (item.price - (product.cost or 0))
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
        section_cost += qty * (product.cost or 0)
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
                label=label, sales_count=0, product_count=0, revenue=0, profit=0, cashier_reward=0
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
                label=label, sales_count=0, product_count=0, revenue=0, profit=0, cashier_reward=0
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
        return [grouped[key] for key in sorted(grouped)]


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
                cost=product.cost if product else 0,
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
    if amount <= 0:
        raise AppError("Qarz summasi 0 dan katta bo'lishi kerak.")
    with session_scope() as session:
        row = session.get(Supplier, supplier_id)
        row.balance = (row.balance or 0) + amount
        row.total_received = (row.total_received or 0) + amount
        s_name = row.name
        s_curr = row.debt_currency or "UZS"
        s_balance = row.balance
        session.add(SupplierDebtMovement(supplier_id=supplier_id, type="qarz", amount=amount, note=note))
    log_activity(
        "supplier_debt_added",
        f"Ta'minotchi qarzi: {s_name}",
        f"+{amount:,.0f} {s_curr} qarz olindi | Jami qarz: {s_balance:,.0f} {s_curr} | Izoh: {note or '-'}",
        level="warning",
        target="supplier_debts",
        badge="Qarz olindi",
    )


def pay_supplier_debt(supplier_id, amount, note=""):
    if amount <= 0:
        raise AppError("To'lov summasi 0 dan katta bo'lishi kerak.")
    with session_scope() as session:
        row = session.get(Supplier, supplier_id)
        current_balance = row.balance or 0
        if amount > current_balance:
            raise AppError(f"To'lov joriy qarzdan oshib ketdi. Joriy qarz: {current_balance:,.2f}.")
        row.balance = current_balance - amount
        s_name = row.name
        s_curr = row.debt_currency or "UZS"
        s_rem = row.balance
        session.add(SupplierDebtMovement(supplier_id=supplier_id, type="tolov", amount=amount, note=note))
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
    if amount <= 0:
        raise AppError("Qarz summasi 0 dan katta bo'lishi kerak.")
    with session_scope() as session:
        row = session.get(Debtor, debtor_id)
        row.balance = (row.balance or 0) + amount
        row.total_given = (row.total_given or 0) + amount
        d_name = row.name
        d_curr = row.debt_currency or "UZS"
        d_balance = row.balance
        session.add(DebtorDebtMovement(debtor_id=debtor_id, type="qarz", amount=amount, note=note))
    log_activity(
        "debtor_debt_added",
        f"Mijozga qarz berildi: {d_name}",
        f"+{amount:,.0f} {d_curr} qarz | Jami qarz: {d_balance:,.0f} {d_curr} | Izoh: {note or '-'}",
        level="warning",
        target="supplier_debts",
        badge="Qarz berildi",
    )


def pay_debtor_debt(debtor_id, amount, note=""):
    if amount <= 0:
        raise AppError("To'lov summasi 0 dan katta bo'lishi kerak.")
    with session_scope() as session:
        row = session.get(Debtor, debtor_id)
        current_balance = row.balance or 0
        if amount > current_balance:
            raise AppError(f"To'lov joriy qarzdan oshib ketdi. Joriy qarz: {current_balance:,.2f}.")
        row.balance = current_balance - amount
        d_name = row.name
        d_curr = row.debt_currency or "UZS"
        d_rem = row.balance
        session.add(DebtorDebtMovement(debtor_id=debtor_id, type="tolov", amount=amount, note=note))
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
            cat_name = row.name
            for expense in session.scalars(select(Expense).where(Expense.category_id == category_id)):
                expense.category_id = None
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
    return str(name or "").strip().casefold() in {"kassir", "cashier", "кассир"}


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


def _first_admin_id(session):
    return session.scalar(select(User.id).where(User.role == "admin").order_by(User.id))


def _apply_expense_owner_filter(stmt, session, user_id=None, include_unassigned=False):
    if not user_id:
        return stmt
    if include_unassigned and user_id == _first_admin_id(session):
        return stmt.where(or_(Expense.user_id == user_id, Expense.user_id.is_(None)))
    return stmt.where(Expense.user_id == user_id)


def get_expense_report(start_date, end_date, category_id=None, user_id=None, include_unassigned=False):
    stmt = (
        select(_date_expr(Expense.created_at).label("label"), Expense.currency_code, func.coalesce(func.sum(Expense.amount), 0).label("amount"))
        .where(_date_expr(Expense.created_at).between(start_date, end_date))
        .group_by(_date_expr(Expense.created_at), Expense.currency_code)
        .order_by(_date_expr(Expense.created_at))
    )
    if category_id:
        stmt = stmt.where(Expense.category_id == category_id)
    with session_scope() as session:
        stmt = _apply_expense_owner_filter(stmt, session, user_id, include_unassigned)
        return [Row(dict(row._mapping)) for row in session.execute(stmt)]


def get_expense_hourly_report(date_str, category_id=None, user_id=None, include_unassigned=False):
    hour_label = func.substr(func.datetime(Expense.created_at, "localtime"), 12, 2).op("||")(":00").label("label")
    stmt = (
        select(hour_label, Expense.currency_code, func.coalesce(func.sum(Expense.amount), 0).label("amount"))
        .where(_date_expr(Expense.created_at) == date_str)
        .group_by(hour_label, Expense.currency_code)
        .order_by(hour_label)
    )
    if category_id:
        stmt = stmt.where(Expense.category_id == category_id)
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
    global _SYNC_SUSPENDED
    previous = _SYNC_SUSPENDED
    _SYNC_SUSPENDED = True
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
        _SYNC_SUSPENDED = previous


def log_login(user):
    global _SYNC_SUSPENDED
    previous = _SYNC_SUSPENDED
    _SYNC_SUSPENDED = True
    try:
        with session_scope() as session:
            session.add(LoginLog(user_id=user["id"], username=user.get("email") or user["username"], role=user["role"], logged_at=_utc_now()))
    finally:
        _SYNC_SUSPENDED = previous
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
    global _SYNC_SUSPENDED
    previous = _SYNC_SUSPENDED
    _SYNC_SUSPENDED = True
    try:
        with session_scope() as session:
            user = session.get(User, user_id)
            if not user:
                return
            row = session.get(UserSetting, {"user_id": user_id, "key": "last_activity_utc"}) or UserSetting(user_id=user_id, key="last_activity_utc")
            row.value = _utc_now()
            session.merge(row)
    finally:
        _SYNC_SUSPENDED = previous
    _touch_account_session(active=True)


def clear_user_activity(user_id):
    if not user_id:
        return
    global _SYNC_SUSPENDED
    previous = _SYNC_SUSPENDED
    _SYNC_SUSPENDED = True
    try:
        with session_scope() as session:
            row = session.get(UserSetting, {"user_id": user_id, "key": "last_activity_utc"})
            if row:
                session.delete(row)
    finally:
        _SYNC_SUSPENDED = previous
    _touch_account_session(active=False)


def remove_foreign_online_accounts(owner_user_id, account_uid):
    safe_uid = _safe_account_uid(account_uid)
    global _SYNC_SUSPENDED
    previous = _SYNC_SUSPENDED
    _SYNC_SUSPENDED = True
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
        _SYNC_SUSPENDED = previous


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
    if not notification_ids:
        return
    for nid in notification_ids:
        _SESSION_READ_IDS.add(str(nid))


def get_read_notification_ids(user_id=None):
    return set(_SESSION_READ_IDS)


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
            "message": f"Shtrix-kod: {p.get('barcode') or '-'} | Ombordagi qoldiq: {p.get('stock', 0)} {p.get('unit', 'dona')} | Sotish narxi: {p.get('price', 0):,.0f} {p.get('price_currency', 'UZS')}",
            "target": "stock",
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
