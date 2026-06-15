import hashlib
import json
import os
import secrets
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
    inspect,
    or_,
    select,
)
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, declarative_base, relationship, sessionmaker
from sqlalchemy.sql import text


DB_PATH = "market_pos.db"

Base = declarative_base()
_ENGINE = None
_ENGINE_PATH = None
_SessionLocal = None


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


@event.listens_for(Session, "after_flush")
def _mark_session_writes(session, _flush_context):
    session.info["has_writes"] = True


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
    return f"sqlite:///{DB_PATH}"


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


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
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


class ProductTemplate(Base):
    __tablename__ = "product_templates"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    created_at = Column(String, server_default=text("CURRENT_TIMESTAMP"))
    fields = relationship("ProductTemplateField", cascade="all, delete-orphan")


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
    name = Column(String, nullable=False)
    template_id = Column(Integer, ForeignKey("product_templates.id"))
    supplier_id = Column(Integer, ForeignKey("suppliers.id"))
    category_id = Column(Integer, ForeignKey("categories.id"))
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
    is_deleted = Column(Integer, default=0)
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
    created_at = Column(String, server_default=text("CURRENT_TIMESTAMP"))


class SaleItem(Base):
    __tablename__ = "sale_items"
    id = Column(Integer, primary_key=True)
    sale_id = Column(Integer, ForeignKey("sales.id"))
    product_id = Column(Integer, ForeignKey("products.id"))
    quantity = Column(Integer, nullable=False)
    returned_quantity = Column(Integer, default=0)
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


def _add_missing_columns():
    engine = _get_engine()
    inspector = inspect(engine)
    migrations = {
        "products": {
            "template_id": "ALTER TABLE products ADD COLUMN template_id INTEGER REFERENCES product_templates(id)",
            "supplier_id": "ALTER TABLE products ADD COLUMN supplier_id INTEGER REFERENCES suppliers(id)",
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
            "is_deleted": "ALTER TABLE products ADD COLUMN is_deleted INTEGER DEFAULT 0",
        },
        "sales": {
            "customer_name": "ALTER TABLE sales ADD COLUMN customer_name TEXT",
            "customer_phone": "ALTER TABLE sales ADD COLUMN customer_phone TEXT",
            "currency_code": "ALTER TABLE sales ADD COLUMN currency_code TEXT DEFAULT 'UZS'",
            "exchange_rate": "ALTER TABLE sales ADD COLUMN exchange_rate REAL DEFAULT 1",
            "paid_original": "ALTER TABLE sales ADD COLUMN paid_original REAL DEFAULT 0",
            "change_original": "ALTER TABLE sales ADD COLUMN change_original REAL DEFAULT 0",
        },
        "sale_items": {
            "returned_quantity": "ALTER TABLE sale_items ADD COLUMN returned_quantity INTEGER DEFAULT 0",
        },
        "suppliers": {
            "debt_currency": "ALTER TABLE suppliers ADD COLUMN debt_currency TEXT DEFAULT 'UZS'",
        },
        "inventory_check_items": {
            "checked_quantity": "ALTER TABLE inventory_check_items ADD COLUMN checked_quantity INTEGER DEFAULT 0",
        },
        "expenses": {
            "user_id": "ALTER TABLE expenses ADD COLUMN user_id INTEGER REFERENCES users(id)",
        },
    }
    with engine.begin() as conn:
        for table_name, table_migrations in migrations.items():
            if not inspector.has_table(table_name):
                continue
            columns = {column["name"] for column in inspector.get_columns(table_name)}
            for column, sql in table_migrations.items():
                if column not in columns:
                    conn.exec_driver_sql(sql)


def init_db():
    engine = _get_engine()
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("PRAGMA journal_mode = WAL")
    except OperationalError:
        pass
    Base.metadata.create_all(engine)
    _add_missing_columns()

    with session_scope() as session:
        admin = session.scalar(select(User).where(User.username == "admin"))
        if admin is None:
            session.add(User(username="admin", password=_hash_password("admin123"), role="admin"))
        elif not str(admin.password).startswith("pbkdf2_sha256$"):
            admin.password = _hash_password(admin.password)

        if not session.get(AppSetting, "app_name"):
            session.add(AppSetting(key="app_name", value="Market POS"))

        for name in ["Oziq-ovqat", "Ichimliklar", "Gigiena", "Uy-ro'zg'or"]:
            if not session.scalar(select(Category).where(Category.name == name)):
                session.add(Category(name=name))

        for code, name, rate, is_base in [
            ("UZS", "O'zbek so'mi", 1, 1),
            ("USD", "AQSh dollari", 12500, 0),
            ("EUR", "Yevro", 13500, 0),
        ]:
            if not session.scalar(select(Currency).where(Currency.code == code)):
                session.add(Currency(code=code, name=name, rate_to_uzs=rate, is_base=is_base))

        for name in ["Ijara", "Transport", "Kommunal", "Ish haqi", "Boshqa"]:
            if not session.scalar(select(ExpenseCategory).where(ExpenseCategory.name == name)):
                session.add(ExpenseCategory(name=name))

        if session.scalar(select(func.count(ProductTemplate.id))) == 0:
            template = ProductTemplate(name="Umumiy mahsulot")
            session.add(template)
            session.flush()
            for order, field_name in enumerate(["Brend", "Model", "Rang"]):
                session.add(ProductTemplateField(template_id=template.id, name=field_name, sort_order=order))
    migrate_finance_manual_json()


def get_app_settings(user_id=None):
    defaults = {"app_name": "Market POS", "theme": "dark_blue", "language": "uz"}
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
    allowed = {"app_name", "theme", "language"}
    with session_scope() as session:
        for key, value in settings.items():
            if key not in allowed:
                continue
            if key == "app_name" or user_id is None:
                row = session.get(AppSetting, key) or AppSetting(key=key)
                row.value = str(value)
                session.merge(row)
            else:
                row = session.get(UserSetting, {"user_id": user_id, "key": key}) or UserSetting(user_id=user_id, key=key)
                row.value = str(value)
                session.merge(row)


def _product_row(product, category_name=None, template_name=None, supplier_name=None):
    return _row_from_model(product, category_name=category_name, template_name=template_name, supplier_name=supplier_name)


def _product_select():
    return (
        select(Product, Category.name, ProductTemplate.name, Supplier.name)
        .outerjoin(Category, Product.category_id == Category.id)
        .outerjoin(ProductTemplate, Product.template_id == ProductTemplate.id)
        .outerjoin(Supplier, Product.supplier_id == Supplier.id)
        .where(func.coalesce(Product.is_deleted, 0) == 0)
    )


def get_all_products(start_date=None, end_date=None):
    with session_scope() as session:
        stmt = _product_select()
        if start_date and end_date:
            stmt = stmt.where(_date_expr(Product.created_at).between(start_date, end_date))
        rows = session.execute(stmt.order_by(Product.name)).all()
        return [_product_row(p, c, t, s) for p, c, t, s in rows]


def search_products(query, start_date=None, end_date=None):
    pattern = f"%{query}%"
    with session_scope() as session:
        stmt = _product_select().where(or_(Product.name.like(pattern), Product.barcode.like(pattern)))
        if start_date and end_date:
            stmt = stmt.where(_date_expr(Product.created_at).between(start_date, end_date))
        rows = session.execute(stmt.order_by(Product.name)).all()
        return [_product_row(p, c, t, s) for p, c, t, s in rows]


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


def add_product(data: dict):
    data = _normalize_product_money(data)
    fields = {column.name for column in Product.__table__.columns}
    try:
        with session_scope() as session:
            product = Product(**{k: v for k, v in data.items() if k in fields and k != "id"})
            session.add(product)
            session.flush()
            return product.id
    except IntegrityError as exc:
        raise AppError("Bu shtrix-kod allaqachon mavjud.") from exc


def update_product(product_id, data: dict):
    data = _normalize_product_money(data)
    try:
        with session_scope() as session:
            product = session.get(Product, product_id)
            for key, value in data.items():
                if hasattr(product, key) and key != "id":
                    setattr(product, key, value)
    except IntegrityError as exc:
        raise AppError("Bu shtrix-kod allaqachon mavjud.") from exc


def delete_product(product_id):
    with session_scope() as session:
        product = session.get(Product, product_id)
        if product:
            product.is_deleted = 1


def set_product_process_status(product_id, status):
    if status not in ("available", "process"):
        raise AppError("Mahsulot holati noto'g'ri.")
    with session_scope() as session:
        product = session.get(Product, product_id)
        if product:
            product.process_status = status


def put_product_in_process(product_id, quantity, deposit_amount=0, deposit_currency="UZS", customer_name=None, customer_phone=None):
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


def update_product_process(product_id, quantity, deposit_amount=0, deposit_currency="UZS", customer_name=None, customer_phone=None):
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


def clear_product_process(product_id):
    with session_scope() as session:
        product = session.get(Product, product_id)
        if product:
            product.process_status = "available"
            product.process_quantity = 0
            product.process_deposit = 0
            product.process_deposit_currency = "UZS"
            product.process_customer_name = None
            product.process_customer_phone = None


def reduce_product_process(product_id, quantity):
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


def get_templates():
    with session_scope() as session:
        return _rows_from_models(session.scalars(select(ProductTemplate).order_by(ProductTemplate.name)).all())


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


def add_template(name, fields):
    try:
        with session_scope() as session:
            template = ProductTemplate(name=name)
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
    try:
        with session_scope() as session:
            template = session.get(ProductTemplate, template_id)
            if template:
                template.name = name
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
        in_use = session.scalar(select(func.count(Product.id)).where(Product.template_id == template_id))
        if in_use:
            raise AppError("Bu template mahsulotlarda ishlatilgan, uni o'chirib bo'lmaydi.")
        row = session.get(ProductTemplate, template_id)
        if row:
            session.delete(row)


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
    with session_scope() as session:
        product = session.get(Product, product_id)
        if product:
            product.stock = (product.stock or 0) + quantity
            session.add(StockMovement(product_id=product_id, type="kirim", quantity=quantity, note=note))


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
        return check.id


def get_inventory_check_items(session_id, checked=None):
    stmt = select(InventoryCheckItem).where(InventoryCheckItem.session_id == session_id)
    if checked is True:
        stmt = stmt.where(func.coalesce(InventoryCheckItem.checked_quantity, 0) > 0)
    elif checked is False:
        stmt = stmt.where(func.coalesce(InventoryCheckItem.checked_quantity, 0) < func.coalesce(InventoryCheckItem.expected_stock, 0))
    with session_scope() as session:
        rows = session.scalars(
            stmt.order_by(case((InventoryCheckItem.checked_at.is_(None), 0), else_=1), InventoryCheckItem.product_name)
        ).all()
        return _rows_from_models(rows)


def get_inventory_check_counts(session_id):
    with session_scope() as session:
        items = session.scalars(select(InventoryCheckItem).where(InventoryCheckItem.session_id == session_id)).all()
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


def mark_inventory_product_checked(session_id, barcode, quantity=1):
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
        return counts


def create_sale(customer_id, cashier_id, items, total, discount, paid, payment_method, currency_code="UZS", exchange_rate=1, paid_original=None, customer_name=None, customer_phone=None):
    if not items:
        raise AppError("Savat bo'sh.")
    if discount < 0 or discount > total:
        raise AppError("Chegirma jami summadan oshmasligi kerak.")
    if payment_method == "qarz" and not customer_id:
        raise AppError("Qarz savdo uchun mijoz tanlang.")
    if exchange_rate <= 0:
        raise AppError("Valyuta kursi noto'g'ri.")
    with session_scope() as session:
        for item in items:
            product = session.get(Product, item["product_id"])
            if product is None:
                raise AppError("Savatdagi mahsulot topilmadi.")
            if item["quantity"] <= 0:
                raise AppError("Miqdor noto'g'ri kiritilgan.")
            if (product.stock or 0) < item["quantity"]:
                raise AppError(f"{product.name} uchun qoldiq yetarli emas. Mavjud: {product.stock}, so'ralgan: {item['quantity']}.")
        payable = total - discount
        change = max(0, paid - payable)
        paid_original = paid_original if paid_original is not None else paid / exchange_rate
        change_original = change / exchange_rate
        if customer_id and (not customer_name or customer_phone is None):
            customer = session.get(Customer, customer_id)
            if customer:
                customer_name = customer_name or customer.name
                customer_phone = customer.phone if customer_phone is None else customer_phone
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
            created_at=_utc_now(),
        )
        session.add(sale)
        session.flush()
        for item in items:
            session.add(SaleItem(
                sale_id=sale.id,
                product_id=item["product_id"],
                quantity=item["quantity"],
                price=item["price"],
                subtotal=item["subtotal"],
            ))
            product = session.get(Product, item["product_id"])
            product.stock = (product.stock or 0) - item["quantity"]
            session.add(StockMovement(product_id=product.id, type="sotuv", quantity=-item["quantity"], note=f"Sotuv #{sale.id}"))
        if customer_id:
            customer = session.get(Customer, customer_id)
            if customer:
                customer.total_purchases = (customer.total_purchases or 0) + payable
                if payment_method == "qarz":
                    customer.balance = (customer.balance or 0) + payable
        return sale.id


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


def get_product_sales_archive(query="", start_date=None, end_date=None):
    pattern = f"%{query.strip()}%"
    with session_scope() as session:
        stmt = (
            select(SaleItem, Sale, Product, User.username, Customer.name, Customer.phone)
            .join(Sale, Sale.id == SaleItem.sale_id)
            .join(Product, Product.id == SaleItem.product_id)
            .outerjoin(User, User.id == Sale.cashier_id)
            .outerjoin(Customer, Customer.id == Sale.customer_id)
            .where(SaleItem.quantity > func.coalesce(SaleItem.returned_quantity, 0))
        )
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
        for item, sale, product, cashier_name, customer_name, customer_phone in rows:
            active_quantity = item.quantity - (item.returned_quantity or 0)
            active_subtotal = active_quantity * (item.price or 0)
            active_sale_total = session.scalar(
                select(func.coalesce(func.sum((SaleItem.quantity - func.coalesce(SaleItem.returned_quantity, 0)) * SaleItem.price), 0))
                .where(SaleItem.sale_id == sale.id)
            ) or 0
            item_discount = (sale.discount or 0) * (active_subtotal / active_sale_total) if active_sale_total > 0 else 0
            item_total_after_discount = max(0, active_subtotal - item_discount)
            result.append(Row(dict(
                sale_item_id=item.id,
                sale_id=item.sale_id,
                product_id=item.product_id,
                product_name=product.name,
                barcode=product.barcode,
                template_id=product.template_id,
                supplier_id=product.supplier_id,
                quantity=item.quantity,
                returned_quantity=item.returned_quantity or 0,
                price=item.price,
                subtotal=item.subtotal,
                active_subtotal=active_subtotal,
                discount=sale.discount,
                item_discount=item_discount,
                item_total_after_discount=item_total_after_discount,
                payment_method=sale.payment_method,
                currency_code=sale.currency_code,
                exchange_rate=sale.exchange_rate,
                created_at=_utc_to_local(sale.created_at),
                cashier_name=cashier_name,
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


def return_sale_item(sale_item_id, quantity, note=""):
    if quantity <= 0:
        raise AppError("Qaytarish miqdori 0 dan katta bo'lishi kerak.")
    with session_scope() as session:
        item = session.get(SaleItem, sale_item_id)
        if item is None:
            raise AppError("Sotuv arxivi topilmadi.")
        sale = session.get(Sale, item.sale_id)
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
        item.returned_quantity = (item.returned_quantity or 0) + quantity
        product = session.get(Product, item.product_id)
        product.stock = (product.stock or 0) + quantity
        product.is_deleted = 0
        session.add(StockMovement(product_id=item.product_id, type="qaytarish", quantity=quantity, note=note or f"Sotuv #{item.sale_id} qaytarildi"))
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
    return session.scalars(select(Sale).where(_date_expr(Sale.created_at) == date_str).order_by(Sale.created_at.desc())).all()


def _sale_revenue(sale):
    return (sale.total or 0) - (sale.discount or 0)


def get_daily_report(date_str):
    with session_scope() as session:
        sales = _sales_for_date(session, date_str)
        revenues = [_sale_revenue(s) for s in sales]
        profit = sum(_sale_revenue(s) - _sale_cost(session, s.id) for s in sales)
        return Row(dict(count=sum(1 for r in revenues if r > 0), revenue=sum(revenues) if revenues else None, profit=profit if sales else None))


def get_sales_by_date(date_str):
    with session_scope() as session:
        rows = session.execute(
            select(Sale, User.username, Customer.name)
            .outerjoin(User, User.id == Sale.cashier_id)
            .outerjoin(Customer, Customer.id == Sale.customer_id)
            .where(_date_expr(Sale.created_at) == date_str)
            .order_by(Sale.created_at.desc())
        ).all()
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


def get_overall_period_series(start_date, end_date):
    with session_scope() as session:
        sales = session.scalars(select(Sale).where(_date_expr(Sale.created_at).between(start_date, end_date))).all()
        grouped = {}
        for sale in sales:
            label = _local_date_label(sale.created_at)
            row = grouped.setdefault(label, Row(dict(label=label, sales_count=0, product_count=0, revenue=0, profit=0)))
            revenue = _sale_revenue(sale)
            if revenue > 0:
                row["sales_count"] += 1
            row["product_count"] += session.scalar(select(func.coalesce(func.sum(SaleItem.quantity - func.coalesce(SaleItem.returned_quantity, 0)), 0)).where(SaleItem.sale_id == sale.id)) or 0
            row["revenue"] += revenue
            row["profit"] += revenue - _sale_cost(session, sale.id)
        return [grouped[key] for key in sorted(grouped)]


def get_overall_day_hourly_series(date_str):
    with session_scope() as session:
        sales = session.scalars(select(Sale).where(_date_expr(Sale.created_at) == date_str)).all()
        grouped = {}
        for sale in sales:
            label = _local_hour_label(sale.created_at)
            row = grouped.setdefault(label, Row(dict(label=label, sales_count=0, product_count=0, revenue=0, profit=0)))
            revenue = _sale_revenue(sale)
            if revenue > 0:
                row["sales_count"] += 1
            row["product_count"] += session.scalar(select(func.coalesce(func.sum(SaleItem.quantity - func.coalesce(SaleItem.returned_quantity, 0)), 0)).where(SaleItem.sale_id == sale.id)) or 0
            row["revenue"] += revenue
            row["profit"] += revenue - _sale_cost(session, sale.id)
        return [grouped[key] for key in sorted(grouped)]


def get_cashier_period_summary(start_date, end_date):
    with session_scope() as session:
        users = session.scalars(select(User).order_by(User.username)).all()
        rows = []
        for user in users:
            sales = session.scalars(select(Sale).where(and_(Sale.cashier_id == user.id, _date_expr(Sale.created_at).between(start_date, end_date)))).all()
            row = Row(dict(entity_id=user.id, entity_name=user.username, sales_count=0, product_count=0, revenue=0, profit=0))
            for sale in sales:
                revenue = _sale_revenue(sale)
                if revenue > 0:
                    row["sales_count"] += 1
                row["product_count"] += session.scalar(select(func.coalesce(func.sum(SaleItem.quantity - func.coalesce(SaleItem.returned_quantity, 0)), 0)).where(SaleItem.sale_id == sale.id)) or 0
                row["revenue"] += revenue
                row["profit"] += revenue - _sale_cost(session, sale.id)
            rows.append(row)
        return sorted(rows, key=lambda r: (-r["revenue"], r["entity_name"]))


def get_customer_period_summary(start_date, end_date):
    with session_scope() as session:
        customers = session.scalars(select(Customer).order_by(Customer.name)).all()
        rows = []
        for customer in customers:
            sales = session.scalars(select(Sale).where(and_(Sale.customer_id == customer.id, _date_expr(Sale.created_at).between(start_date, end_date)))).all()
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


def get_entity_period_series(entity_type, entity_id, start_date, end_date):
    if entity_type not in ("cashier", "customer"):
        raise AppError("Hisobot turi noto'g'ri.")
    column = Sale.cashier_id if entity_type == "cashier" else Sale.customer_id
    with session_scope() as session:
        sales = session.scalars(select(Sale).where(and_(column == entity_id, _date_expr(Sale.created_at).between(start_date, end_date)))).all()
        grouped = {}
        for sale in sales:
            label = _local_date_label(sale.created_at)
            row = grouped.setdefault(label, Row(dict(label=label, sales_count=0, product_count=0, revenue=0, profit=0)))
            revenue = _sale_revenue(sale)
            if revenue > 0:
                row["sales_count"] += 1
            row["product_count"] += session.scalar(select(func.coalesce(func.sum(SaleItem.quantity - func.coalesce(SaleItem.returned_quantity, 0)), 0)).where(SaleItem.sale_id == sale.id)) or 0
            row["revenue"] += revenue
            row["profit"] += revenue - _sale_cost(session, sale.id)
        return [grouped[key] for key in sorted(grouped)]


def get_entity_day_hourly_series(entity_type, entity_id, date_str):
    if entity_type not in ("cashier", "customer"):
        raise AppError("Hisobot turi noto'g'ri.")
    column = Sale.cashier_id if entity_type == "cashier" else Sale.customer_id
    with session_scope() as session:
        sales = session.scalars(select(Sale).where(and_(column == entity_id, _date_expr(Sale.created_at) == date_str))).all()
        grouped = {}
        for sale in sales:
            label = _local_hour_label(sale.created_at)
            row = grouped.setdefault(label, Row(dict(label=label, sales_count=0, product_count=0, revenue=0, profit=0)))
            revenue = _sale_revenue(sale)
            if revenue > 0:
                row["sales_count"] += 1
            row["product_count"] += session.scalar(select(func.coalesce(func.sum(SaleItem.quantity - func.coalesce(SaleItem.returned_quantity, 0)), 0)).where(SaleItem.sale_id == sale.id)) or 0
            row["revenue"] += revenue
            row["profit"] += revenue - _sale_cost(session, sale.id)
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
    if not name.strip():
        raise AppError("Ta'minotchi nomini kiriting.")
    with session_scope() as session:
        row = Supplier(name=name.strip(), phone=phone, note=note, debt_currency=debt_currency)
        session.add(row)
        session.flush()
        return row.id


def update_supplier(supplier_id, name, phone=None, note=None, debt_currency=None):
    if not name.strip():
        raise AppError("Ta'minotchi nomini kiriting.")
    with session_scope() as session:
        row = session.get(Supplier, supplier_id)
        if row:
            row.name, row.phone, row.note = name.strip(), phone, note
            if debt_currency:
                row.debt_currency = debt_currency


def delete_supplier(supplier_id):
    with session_scope() as session:
        for product in session.scalars(select(Product).where(Product.supplier_id == supplier_id)):
            product.supplier_id = None
        session.query(SupplierDebtMovement).filter(SupplierDebtMovement.supplier_id == supplier_id).delete()
        row = session.get(Supplier, supplier_id)
        if row:
            session.delete(row)


def add_supplier_debt(supplier_id, amount, note=""):
    if amount <= 0:
        raise AppError("Qarz summasi 0 dan katta bo'lishi kerak.")
    with session_scope() as session:
        row = session.get(Supplier, supplier_id)
        row.balance = (row.balance or 0) + amount
        row.total_received = (row.total_received or 0) + amount
        session.add(SupplierDebtMovement(supplier_id=supplier_id, type="qarz", amount=amount, note=note))


def pay_supplier_debt(supplier_id, amount, note=""):
    if amount <= 0:
        raise AppError("To'lov summasi 0 dan katta bo'lishi kerak.")
    with session_scope() as session:
        row = session.get(Supplier, supplier_id)
        current_balance = row.balance or 0
        if amount > current_balance:
            raise AppError(f"To'lov joriy qarzdan oshib ketdi. Joriy qarz: {current_balance:,.2f}.")
        row.balance = current_balance - amount
        session.add(SupplierDebtMovement(supplier_id=supplier_id, type="tolov", amount=amount, note=note))


def get_supplier_debt_movements(supplier_id=None):
    stmt = select(SupplierDebtMovement, Supplier.name).join(Supplier, Supplier.id == SupplierDebtMovement.supplier_id)
    if supplier_id:
        stmt = stmt.where(SupplierDebtMovement.supplier_id == supplier_id)
    with session_scope() as session:
        rows = session.execute(stmt.order_by(SupplierDebtMovement.created_at.desc(), SupplierDebtMovement.id.desc())).all()
        return [_row_from_model(m, supplier_name=name) for m, name in rows]


def get_all_debtors():
    with session_scope() as session:
        return _rows_from_models(session.scalars(select(Debtor).order_by(Debtor.name)).all())


def add_debtor(name, phone=None, note=None, debt_currency="UZS"):
    if not name.strip():
        raise AppError("Qarz oluvchi nomini kiriting.")
    with session_scope() as session:
        row = Debtor(name=name.strip(), phone=phone, note=note, debt_currency=debt_currency)
        session.add(row)
        session.flush()
        return row.id


def update_debtor(debtor_id, name, phone=None, note=None, debt_currency=None):
    if not name.strip():
        raise AppError("Qarz oluvchi nomini kiriting.")
    with session_scope() as session:
        row = session.get(Debtor, debtor_id)
        if row:
            row.name, row.phone, row.note = name.strip(), phone, note
            if debt_currency:
                row.debt_currency = debt_currency


def delete_debtor(debtor_id):
    with session_scope() as session:
        session.query(DebtorDebtMovement).filter(DebtorDebtMovement.debtor_id == debtor_id).delete()
        row = session.get(Debtor, debtor_id)
        if row:
            session.delete(row)


def add_debtor_debt(debtor_id, amount, note=""):
    if amount <= 0:
        raise AppError("Qarz summasi 0 dan katta bo'lishi kerak.")
    with session_scope() as session:
        row = session.get(Debtor, debtor_id)
        row.balance = (row.balance or 0) + amount
        row.total_given = (row.total_given or 0) + amount
        session.add(DebtorDebtMovement(debtor_id=debtor_id, type="qarz", amount=amount, note=note))


def pay_debtor_debt(debtor_id, amount, note=""):
    if amount <= 0:
        raise AppError("To'lov summasi 0 dan katta bo'lishi kerak.")
    with session_scope() as session:
        row = session.get(Debtor, debtor_id)
        current_balance = row.balance or 0
        if amount > current_balance:
            raise AppError(f"To'lov joriy qarzdan oshib ketdi. Joriy qarz: {current_balance:,.2f}.")
        row.balance = current_balance - amount
        session.add(DebtorDebtMovement(debtor_id=debtor_id, type="tolov", amount=amount, note=note))


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
    if not name.strip():
        raise AppError("Kategoriya nomini kiriting.")
    with session_scope() as session:
        try:
            row = ExpenseCategory(name=name.strip())
            session.add(row)
            session.flush()
            return row.id
        except IntegrityError as exc:
            raise AppError("Bu kategoriya allaqachon mavjud.") from exc


def update_expense_category(category_id, name):
    if not name.strip():
        raise AppError("Kategoriya nomini kiriting.")
    with session_scope() as session:
        try:
            row = session.get(ExpenseCategory, category_id)
            if row:
                row.name = name.strip()
                session.flush()
        except IntegrityError as exc:
            raise AppError("Bu kategoriya allaqachon mavjud.") from exc


def delete_expense_category(category_id):
    with session_scope() as session:
        for expense in session.scalars(select(Expense).where(Expense.category_id == category_id)):
            expense.category_id = None
        row = session.get(ExpenseCategory, category_id)
        if row:
            session.delete(row)


def get_expenses():
    with session_scope() as session:
        rows = session.execute(
            select(Expense, ExpenseCategory.name, User.username)
            .outerjoin(ExpenseCategory, ExpenseCategory.id == Expense.category_id)
            .outerjoin(User, User.id == Expense.user_id)
            .order_by(Expense.created_at.desc(), Expense.id.desc())
        ).all()
        return [_row_from_model(expense, category_name=name, username=username) for expense, name, username in rows]


def add_expense(category_id, amount, currency_code, description, user_id=None):
    if amount <= 0:
        raise AppError("Harajat summasi 0 dan katta bo'lishi kerak.")
    with session_scope() as session:
        row = Expense(category_id=category_id, amount=amount, currency_code=currency_code, description=description, user_id=user_id)
        session.add(row)
        session.flush()
        return row.id


def update_expense(expense_id, category_id, amount, currency_code, description, user_id=None):
    if amount <= 0:
        raise AppError("Harajat summasi 0 dan katta bo'lishi kerak.")
    with session_scope() as session:
        row = session.get(Expense, expense_id)
        if row:
            row.category_id, row.amount, row.currency_code, row.description = category_id, amount, currency_code, description
            if user_id is not None:
                row.user_id = user_id


def delete_expense(expense_id):
    with session_scope() as session:
        row = session.get(Expense, expense_id)
        if row:
            session.delete(row)


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


def authenticate(username, password):
    with session_scope() as session:
        row = session.scalar(select(User).where(User.username == username))
        if row and _verify_password(password, row.password):
            if not str(row.password).startswith("pbkdf2_sha256$"):
                row.password = _hash_password(password)
            return _row_from_model(row)
        return None


def log_login(user):
    with session_scope() as session:
        session.add(LoginLog(user_id=user["id"], username=user["username"], role=user["role"], logged_at=_utc_now()))


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
        rows = session.scalars(select(User).order_by(User.role, User.username)).all()
        return [Row(dict(id=u.id, username=u.username, role=u.role, created_at=u.created_at)) for u in rows]


def add_user(username, password, role="cashier"):
    username = username.strip()
    if not username or not password:
        raise AppError("Username va parol kiriting.")
    if role not in ("admin", "cashier"):
        raise AppError("Role noto'g'ri.")
    with session_scope() as session:
        try:
            session.add(User(username=username, password=_hash_password(password), role=role))
            session.flush()
        except IntegrityError as exc:
            raise AppError("Bu username allaqachon mavjud.") from exc


def update_user(user_id, username, password=None, role="cashier"):
    username = username.strip()
    if not username:
        raise AppError("Username kiriting.")
    if role not in ("admin", "cashier"):
        raise AppError("Role noto'g'ri.")
    with session_scope() as session:
        try:
            user = session.get(User, user_id)
            if user:
                user.username = username
                user.role = role
                if password:
                    user.password = _hash_password(password)
                session.flush()
        except IntegrityError as exc:
            raise AppError("Bu username allaqachon mavjud.") from exc


def delete_user(user_id):
    with session_scope() as session:
        admins = session.scalar(select(func.count(User.id)).where(User.role == "admin"))
        user = session.get(User, user_id)
        if user and user.role == "admin" and admins <= 1:
            raise AppError("Oxirgi adminni o'chirib bo'lmaydi.")
        if user:
            try:
                session.delete(user)
            except IntegrityError as exc:
                raise AppError("Bu foydalanuvchi sotuv tarixida bor, uni o'chirib bo'lmaydi.") from exc


def get_low_stock_products():
    return []
