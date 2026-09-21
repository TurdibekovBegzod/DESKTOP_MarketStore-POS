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

import logging

from sqlalchemy import text

from app.config import get_settings
from app.database import SessionLocal


logger = logging.getLogger(__name__)

# The reply is three sentences on Instagram; more rows than this only cost
# tokens and give the model room to pad the answer.
MAX_RESULTS = 8

# What a product row may show a customer. Everything else - ids, cost, supplier,
# the process_* columns - is either internal or useless to them, and a model
# handed an id will sooner or later paste it into a DM.
VISIBLE_FIELDS = ("name", "price", "currency", "stock", "unit", "category")


SEARCH_PRODUCTS_DECLARATION = {
    "name": "search_products",
    "description": (
        "Do'kon omboridagi mahsulotlarni qidiradi. Nom, kategoriya va narx "
        "oralig'i bo'yicha filtrlash mumkin - bir nechtasini birga ishlatsa "
        "ham bo'ladi. Narx, qoldiq va kategoriyani qaytaradi. Mijoz mahsulot, "
        "narx yoki mavjudlik haqida so'raganda ishlat. Mijoz dollar yoki yevroda "
        "gapirsa, currency ni 'USD' yoki 'EUR' qilib ber - kurs o'zi hisoblanadi."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Mahsulot nomi yoki uning bir qismi. Bilmasang bo'sh qoldir.",
            },
            "category": {
                "type": "string",
                "description": "Kategoriya nomi, masalan 'noutbuk', 'telefon'.",
            },
            "barcode": {"type": "string", "description": "Shtrix-kod, aniq moslik."},
            "price_min": {"type": "number", "description": "Eng past narx."},
            "price_max": {"type": "number", "description": "Eng yuqori narx."},
            "currency": {
                "type": "string",
                "description": "price_min/price_max qaysi valyutada: 'UZS', 'USD' yoki 'EUR'. Default UZS.",
            },
            "in_stock_only": {
                "type": "boolean",
                "description": "Faqat qoldig'i bor mahsulotlar. Default true.",
            },
        },
    },
}


def _rate_to_uzs(session, email: str, code: str) -> float | None:
    """The shop's own rate for a currency, not the central bank's.

    The shop priced its stock with this number, so a filter that used any other
    rate would quietly disagree with the prices it is comparing against.
    """
    code = (code or "").strip().upper()
    if not code or code == "UZS":
        return 1.0

    row = session.execute(
        text(
            """
            SELECT r.data ->> 'rate_to_uzs'
            FROM user_records AS r
            JOIN users AS u ON u.id = r.user_id
            WHERE u.email = :email
              AND r.table_name = 'currencies'
              AND r.deleted_at IS NULL
              AND upper(r.data ->> 'code') = :code
            LIMIT 1
            """
        ),
        {"email": email, "code": code},
    ).scalar()

    try:
        rate = float(row)
    except (TypeError, ValueError):
        return None
    return rate if rate > 0 else None


def _describe(row: dict, categories: dict) -> dict:
    """The few fields a customer actually asked about.

    ``price`` is the selling price. ``cost`` is what the shop paid and never
    leaves this function.
    """
    category_id = row.get("category_id")
    return {
        "name": row.get("name"),
        "price": row.get("price"),
        "currency": row.get("price_currency") or "UZS",
        "stock": row.get("stock"),
        "unit": row.get("unit") or "dona",
        "category": categories.get(category_id),
    }


def _load_categories(session, email: str) -> dict:
    """id -> name for this account, so rows can name their category."""
    rows = session.execute(
        text(
            """
            SELECT r.data ->> 'id', r.data ->> 'name'
            FROM user_records AS r
            JOIN users AS u ON u.id = r.user_id
            WHERE u.email = :email
              AND r.table_name = 'categories'
              AND r.deleted_at IS NULL
            """
        ),
        {"email": email},
    ).all()
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
    email = (get_settings().shop_account_email or "").strip()
    if not email:
        return {"error": "shop account is not configured"}

    name = (name or "").strip()
    category = (category or "").strip()
    barcode = (barcode or "").strip()

    if not any([name, category, barcode, price_min is not None, price_max is not None]):
        return {"error": "kamida bitta filtr kerak", "products": [], "count": 0}

    # Built up rather than one fixed string, but only from this function's own
    # fragments - the model's values reach the database solely as parameters.
    clauses = [
        "u.email = :email",
        "r.table_name = 'products'",
        "r.deleted_at IS NULL",
        "COALESCE((r.data ->> 'is_deleted')::int, 0) = 0",
    ]
    params: dict = {"email": email, "limit": MAX_RESULTS}

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
            rate = _rate_to_uzs(session, email, currency)
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

            categories = _load_categories(session, email)

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
                f"SELECT r.data FROM user_records AS r "
                f"JOIN users AS u ON u.id = r.user_id "
                f"WHERE {' AND '.join(clauses)} {order} LIMIT :limit"
            )
            rows = session.execute(statement, params).scalars().all()
    except Exception:
        logger.exception("product search failed: name=%r category=%r", name, category)
        return {"error": "lookup failed", "products": [], "count": 0}

    products = [_describe(row, categories) for row in rows if isinstance(row, dict)]
    return {"products": products, "count": len(products)}


# Name -> handler, as ai.gemini.generate expects it.
TOOLS = {"search_products": search_products}

DECLARATIONS = [SEARCH_PRODUCTS_DECLARATION]
