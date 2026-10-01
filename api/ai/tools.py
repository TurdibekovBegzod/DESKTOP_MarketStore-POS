"""What the agent is allowed to look up, and how it reaches the shop's data.

Products are not a table here. The desktop app owns the schema and syncs whole
rows into ``user_records`` as JSONB, so a lookup is a query over that envelope
filtered to one account's ``products`` rows - see models.UserRecord.

The model never writes SQL. It fills in a fixed set of filters and this module
builds the statement, so no phrasing in a customer's DM can reach the query:
the account filter is always present and the parameters are always bound.

Read-only on purpose. Writing would have to go through /sync/push to take a
generation lock, and an Instagram DM is untrusted input: a customer must not be
able to talk the shop's stock into changing.
"""

from contextvars import ContextVar
from dataclasses import dataclass
import logging

from sqlalchemy import text

from app.config import get_settings
from app.database import SessionLocal


logger = logging.getLogger(__name__)


@dataclass
class StoreContext:
    user_id: int | None = None
    user_uid: str | None = None
    email: str | None = None


_store_context: ContextVar[StoreContext | None] = ContextVar("store_context", default=None)


def set_store_context(ctx: StoreContext | None):
    return _store_context.set(ctx)


def get_store_context() -> StoreContext | None:
    return _store_context.get()


def _resolve_target() -> tuple[str, str, dict]:
    """Return (target_type, target_val, params) where target_type is 'uid' or 'email'."""
    ctx = get_store_context()
    if ctx and ctx.user_uid:
        return "uid", ctx.user_uid, {"user_uid": ctx.user_uid}
    if ctx and ctx.email:
        return "email", ctx.email, {"email": ctx.email}
    email = (get_settings().shop_account_email or "").strip()
    if email:
        return "email", email, {"email": email}
    return "", "", {}

# The reply is three sentences on Instagram; more products than this only cost
# tokens and give the model room to pad the answer.
MAX_RESULTS = 8

# Rows read before identical ones are merged. A shop keeps one row per unit, so
# eight rows were often one laptop five times over; reading more and merging
# keeps the answer at MAX_RESULTS distinct products.
MAX_ROWS = 40

# What a product row may show a customer. Everything else - ids, cost, supplier,
# the process_* columns - is either internal or useless to them, and a model
# handed an id will sooner or later paste it into a DM. Only name, price and
# stock are always present; the rest only when they say something.
VISIBLE_FIELDS = ("name", "price", "stock", "unit", "category", "specs")


SEARCH_PRODUCTS_DECLARATION = {
    "name": "search_products",
    "description": (
        "Do'kon mahsulotlarini qidiradi. Har mahsulot: name, price (so'm), "
        "stock, bo'lsa specs (CPU, RAM ...), unit, category."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Nom yoki uning bir qismi."},
            "category": {"type": "string"},
            "barcode": {"type": "string"},
            "price_min": {"type": "number"},
            "price_max": {"type": "number"},
            "currency": {
                "type": "string",
                "description": "price_min/max valyutasi: UZS (default), USD, EUR.",
            },
            "in_stock_only": {"type": "boolean", "description": "Default true."},
        },
    },
}


def _rate_to_uzs(session, target_val: str, code: str, target_type: str = "email") -> float | None:
    """The shop's own rate for a currency, not the central bank's.

    The shop priced its stock with this number, so a filter that used any other
    rate would quietly disagree with the prices it is comparing against.
    """
    code = (code or "").strip().upper()
    if not code or code == "UZS":
        return 1.0

    if target_type == "uid":
        query = """
            SELECT r.data ->> 'rate_to_uzs'
            FROM user_records AS r
            WHERE r.user_uid = :user_uid
              AND r.table_name = 'currencies'
              AND r.deleted_at IS NULL
              AND upper(r.data ->> 'code') = :code
            LIMIT 1
        """
        params = {"user_uid": target_val, "code": code}
    else:
        query = """
            SELECT r.data ->> 'rate_to_uzs'
            FROM user_records AS r
            JOIN users AS u ON u.id = r.user_id
            WHERE u.email = :email
              AND r.table_name = 'currencies'
              AND r.deleted_at IS NULL
              AND upper(r.data ->> 'code') = :code
            LIMIT 1
        """
        params = {"email": target_val, "code": code}

    row = session.execute(text(query), params).scalar()
    try:
        rate = float(row)
    except (TypeError, ValueError):
        return None
    return rate if rate > 0 else None


def _number(value):
    """3060000.0 -> 3060000: a trailing .0 is a token on every price."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _describe(row: dict, categories: dict, specs: dict) -> dict:
    """The few fields a customer actually asked about, and nothing empty.

    ``price`` is the selling price and always in so'm: the desktop app stores
    price_original * rate there, so ``price_currency`` only says what the shop
    typed it in and is left out - next to a so'm figure it read as the wrong
    currency. ``cost`` is what the shop paid and never leaves this function.

    Every result is resent to the model on each later round of the same reply,
    so a field that carries no information - unit "dona", no category, no
    specs - is left out rather than sent as a default.
    """
    described = {
        "name": row.get("name"),
        "price": _number(row.get("price")),
        "stock": _number(row.get("stock")),
    }
    product_specs = specs.get(row.get("id")) or {}
    if product_specs:
        described["specs"] = product_specs
    unit = row.get("unit")
    if unit and unit != "dona":
        described["unit"] = unit
    category = categories.get(row.get("category_id"))
    if category:
        described["category"] = category
    return described


def _merge_identical(products: list[dict]) -> list[dict]:
    """One entry per distinct product, stock added up, first-seen order kept.

    Rows that differ only in id are the same thing to a customer: listing a
    laptop five times costs five times the tokens and reads as five models.
    """
    merged: dict = {}
    for product in products:
        key = (
            product.get("name"),
            product.get("price"),
            product.get("unit"),
            tuple(sorted((product.get("specs") or {}).items())),
        )
        if key in merged:
            first = merged[key]
            if isinstance(first.get("stock"), (int, float)) and isinstance(product.get("stock"), (int, float)):
                first["stock"] = _number(first["stock"] + product["stock"])
        else:
            merged[key] = dict(product)
    return list(merged.values())


def _load_specs(session, target_val: str, product_ids: list[str], target_type: str = "email") -> dict:
    """product_id -> {field name: value}, for every id that has any.

    One join instead of one query per row: a list of results can be up to
    MAX_ROWS rows, and asking the database once scales the same
    whether that list holds one row or eight.
    """
    if not product_ids:
        return {}

    if target_type == "uid":
        query = """
            SELECT a.data ->> 'product_id', f.data ->> 'name', a.data ->> 'value'
            FROM user_records AS a
            JOIN user_records AS f
              ON f.user_uid = a.user_uid
             AND f.table_name = 'product_template_fields'
             AND f.deleted_at IS NULL
             AND f.data ->> 'id' = a.data ->> 'field_id'
            WHERE a.user_uid = :user_uid
              AND a.table_name = 'product_attributes'
              AND a.deleted_at IS NULL
              AND a.data ->> 'product_id' = ANY(:product_ids)
        """
        params = {"user_uid": target_val, "product_ids": product_ids}
    else:
        query = """
            SELECT a.data ->> 'product_id', f.data ->> 'name', a.data ->> 'value'
            FROM user_records AS a
            JOIN users AS u ON u.id = a.user_id
            JOIN user_records AS f
              ON f.user_id = a.user_id
             AND f.table_name = 'product_template_fields'
             AND f.deleted_at IS NULL
             AND f.data ->> 'id' = a.data ->> 'field_id'
            WHERE u.email = :email
              AND a.table_name = 'product_attributes'
              AND a.deleted_at IS NULL
              AND a.data ->> 'product_id' = ANY(:product_ids)
        """
        params = {"email": target_val, "product_ids": product_ids}

    rows = session.execute(text(query), params).all()
    specs: dict = {}
    for product_id, field_name, value in rows:
        if not product_id or not field_name or not value:
            continue
        specs.setdefault(product_id, {})[field_name] = value
    return specs


def _load_categories(session, target_val: str, target_type: str = "email") -> dict:
    """id -> name for this account, so rows can name their category."""
    if target_type == "uid":
        query = """
            SELECT r.data ->> 'id', r.data ->> 'name'
            FROM user_records AS r
            WHERE r.user_uid = :user_uid
              AND r.table_name = 'categories'
              AND r.deleted_at IS NULL
        """
        params = {"user_uid": target_val}
    else:
        query = """
            SELECT r.data ->> 'id', r.data ->> 'name'
            FROM user_records AS r
            JOIN users AS u ON u.id = r.user_id
            WHERE u.email = :email
              AND r.table_name = 'categories'
              AND r.deleted_at IS NULL
        """
        params = {"email": target_val}

    rows = session.execute(text(query), params).all()
    return {row[0]: row[1] for row in rows if row[0]}


def search_products(
    name: str = "",
    category: str = "",
    barcode: str = "",
    price_min: float | None = None,
    price_max: float | None = None,
    currency: str = "UZS",
    in_stock_only: bool = True,
) -> dict:
    """Look up products for the account the bot answers for.

    Returns a dict rather than raising: the result goes back to the model as a
    functionResponse, and "nothing found" is an answer the model can relay,
    where an exception would cost the customer their reply entirely.
    """
    target_type, target_val, scope_params = _resolve_target()
    if not target_type:
        return {"error": "shop account is not configured"}

    name = (name or "").strip()
    category = (category or "").strip()
    barcode = (barcode or "").strip()

    if not any([name, category, barcode, price_min is not None, price_max is not None]):
        return {"error": "kamida bitta filtr kerak", "products": [], "count": 0}

    # Built up rather than one fixed string, but only from this function's own
    # fragments - the model's values reach the database solely as parameters.
    if target_type == "uid":
        clauses = [
            "r.user_uid = :user_uid",
            "r.table_name = 'products'",
            "r.deleted_at IS NULL",
            "COALESCE((r.data ->> 'is_deleted')::int, 0) = 0",
        ]
        from_sql = "FROM user_records AS r"
    else:
        clauses = [
            "u.email = :email",
            "r.table_name = 'products'",
            "r.deleted_at IS NULL",
            "COALESCE((r.data ->> 'is_deleted')::int, 0) = 0",
        ]
        from_sql = "FROM user_records AS r JOIN users AS u ON u.id = r.user_id"

    params: dict = dict(scope_params)
    params["limit"] = MAX_ROWS

    if barcode:
        clauses.append("r.data ->> 'barcode' = :barcode")
        params["barcode"] = barcode
    if name:
        # Parenthesised: pg_trgm's % binds tighter than ->>, so an unparenthesised
        # extraction gets read as 'name' % :name first - a text-vs-text similarity
        # test - and the whole clause becomes "jsonb ->> boolean", which Postgres
        # rejects outright.
        clauses.append("(r.data ->> 'name') % :name")
        params["name"] = name
    if in_stock_only:
        clauses.append("COALESCE((r.data ->> 'stock')::numeric, 0) > 0")

    try:
        with SessionLocal() as session:
            rate = _rate_to_uzs(session, target_val, currency, target_type)
            if rate is None:
                return {
                    "error": f"{currency} kursi bazada yo'q",
                    "products": [],
                    "count": 0,
                }

            if price_min is not None:
                clauses.append("COALESCE((r.data ->> 'price')::numeric, 0) >= :price_min")
                params["price_min"] = float(price_min) * rate
            if price_max is not None:
                clauses.append("COALESCE((r.data ->> 'price')::numeric, 0) <= :price_max")
                params["price_max"] = float(price_max) * rate

            categories = _load_categories(session, target_val, target_type)

            if category:
                wanted = [
                    cid
                    for cid, cname in categories.items()
                    if category.casefold() in (cname or "").casefold()
                ]
                if not wanted:
                    return {"products": [], "count": 0}
                clauses.append("r.data ->> 'category_id' = ANY(:category_ids)")
                params["category_ids"] = wanted

            order = (
                "ORDER BY similarity(COALESCE(r.data ->> 'name', ''), :name) DESC"
                if name
                else "ORDER BY COALESCE((r.data ->> 'price')::numeric, 0) ASC"
            )
            statement = text(
                f"SELECT r.data {from_sql} "
                f"WHERE {' AND '.join(clauses)} {order} LIMIT :limit"
            )
            rows = session.execute(statement, params).scalars().all()
            product_ids = [row.get("id") for row in rows if isinstance(row, dict) and row.get("id")]
            specs = _load_specs(session, target_val, product_ids, target_type)
    except Exception:
        logger.exception("product search failed: name=%r category=%r", name, category)
        return {"error": "lookup failed", "products": [], "count": 0}

    products = _merge_identical(
        [_describe(row, categories, specs) for row in rows if isinstance(row, dict)]
    )[:MAX_RESULTS]
    return {"products": products, "count": len(products)}


# Name -> handler, as ai.gemini.generate expects it. The shop's rules are not a
# tool: they go into the system prompt whole (see ai.agent.system_prompt_for).
TOOLS = {
    "search_products": search_products,
}

DECLARATIONS = [
    SEARCH_PRODUCTS_DECLARATION,
]
