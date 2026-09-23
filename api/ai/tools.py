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


GET_PRODUCT_SPECS_DECLARATION = {
    "name": "get_product_specs",
    "description": (
        "Bitta mahsulotning texnik xarakteristikalarini qaytaradi (masalan "
        "protsessor, xotira, ekran, videokarta) - qaysi maydonlar borligi "
        "mahsulot turiga qarab farq qiladi. Mijoz aniq bir model haqida "
        "'xarakteristikasi qanday', 'xotirasi qancha' kabi savol berganda, "
        "avval search_products bilan topilgan nomni shu yerga ber."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Mahsulotning aniq nomi, search_products natijasidagi 'name' bilan bir xil.",
            },
        },
        "required": ["name"],
    },
}


SEARCH_PRODUCTS_DECLARATION = {
    "name": "search_products",
    "description": (
        "Do'kon omboridagi mahsulotlarni qidiradi. Nom, kategoriya va narx "
        "oralig'i bo'yicha filtrlash mumkin - bir nechtasini birga ishlatsa "
        "ham bo'ladi. Narx, qoldiq, kategoriya va (mavjud bo'lsa) 'specs' "
        "maydonida texnik xarakteristikalarni (masalan CPU, RAM) qaytaradi - "
        "ular bor mahsulotlar uchun bularni ham darhol ayt, alohida so'ralishini "
        "kutma. Mijoz mahsulot, narx yoki mavjudlik haqida so'raganda ishlat. "
        "Mijoz dollar yoki yevroda gapirsa, currency ni 'USD' yoki 'EUR' qilib "
        "ber - kurs o'zi hisoblanadi."
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


def _describe(row: dict, categories: dict, specs: dict) -> dict:
    """The few fields a customer actually asked about.

    ``price`` is the selling price. ``cost`` is what the shop paid and never
    leaves this function. ``specs`` (RAM, CPU, ...) is included only when the
    product's template actually has attribute values - most products have
    none, and an empty dict costs the model nothing to skip over.
    """
    category_id = row.get("category_id")
    described = {
        "name": row.get("name"),
        "price": row.get("price"),
        "currency": row.get("price_currency") or "UZS",
        "stock": row.get("stock"),
        "unit": row.get("unit") or "dona",
        "category": categories.get(category_id),
    }
    product_specs = specs.get(row.get("id")) or {}
    if product_specs:
        described["specs"] = product_specs
    return described


def _load_specs(session, email: str, product_ids: list[str]) -> dict:
    """product_id -> {field name: value}, for every id that has any.

    One join instead of one query per row: a list of results can be up to
    MAX_RESULTS products, and asking the database once scales the same
    whether that list holds one row or eight.
    """
    if not product_ids:
        return {}

    rows = session.execute(
        text(
            """
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
        ),
        {"email": email, "product_ids": product_ids},
    ).all()

    specs: dict = {}
    for product_id, field_name, value in rows:
        if not product_id or not field_name or not value:
            continue
        specs.setdefault(product_id, {})[field_name] = value
    return specs


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
            product_ids = [row.get("id") for row in rows if isinstance(row, dict) and row.get("id")]
            specs = _load_specs(session, email, product_ids)
    except Exception:
        logger.exception("product search failed: name=%r category=%r", name, category)
        return {"error": "lookup failed", "products": [], "count": 0}

    products = [_describe(row, categories, specs) for row in rows if isinstance(row, dict)]
    return {"products": products, "count": len(products)}


def get_product_specs(name: str = "") -> dict:
    """Technical fields for one product, looked up by its closest name match.

    Fields live apart from the product row: a template (e.g. "Noutbuk") lists
    which fields exist, and product_attributes holds each product's value for
    one of those fields. A template with no fields, or a product with no
    attribute rows, is not an error - most products simply have none.
    """
    email = (get_settings().shop_account_email or "").strip()
    if not email:
        return {"error": "shop account is not configured"}

    name = (name or "").strip()
    if not name:
        return {"error": "mahsulot nomi kerak", "specs": {}}

    try:
        with SessionLocal() as session:
            row = session.execute(
                text(
                    """
                    SELECT r.data
                    FROM user_records AS r
                    JOIN users AS u ON u.id = r.user_id
                    WHERE u.email = :email
                      AND r.table_name = 'products'
                      AND r.deleted_at IS NULL
                      AND COALESCE((r.data ->> 'is_deleted')::int, 0) = 0
                      AND (r.data ->> 'name') % :name
                    ORDER BY similarity(COALESCE(r.data ->> 'name', ''), :name) DESC
                    LIMIT 1
                    """
                ),
                {"email": email, "name": name},
            ).scalar()

            if not isinstance(row, dict):
                return {"specs": {}, "found": False}

            product_id = row.get("id")
            template_id = row.get("template_id")
            if not product_id or not template_id:
                return {"name": row.get("name"), "specs": {}, "found": True}

            fields = session.execute(
                text(
                    """
                    SELECT r.data ->> 'id', r.data ->> 'name'
                    FROM user_records AS r
                    JOIN users AS u ON u.id = r.user_id
                    WHERE u.email = :email
                      AND r.table_name = 'product_template_fields'
                      AND r.deleted_at IS NULL
                      AND r.data ->> 'template_id' = :template_id
                    """
                ),
                {"email": email, "template_id": template_id},
            ).all()
            field_names = {field_id: field_name for field_id, field_name in fields if field_id}

            if not field_names:
                return {"name": row.get("name"), "specs": {}, "found": True}

            values = session.execute(
                text(
                    """
                    SELECT r.data ->> 'field_id', r.data ->> 'value'
                    FROM user_records AS r
                    JOIN users AS u ON u.id = r.user_id
                    WHERE u.email = :email
                      AND r.table_name = 'product_attributes'
                      AND r.deleted_at IS NULL
                      AND r.data ->> 'product_id' = :product_id
                    """
                ),
                {"email": email, "product_id": product_id},
            ).all()
    except Exception:
        logger.exception("product specs lookup failed: name=%r", name)
        return {"error": "lookup failed", "specs": {}}

    specs = {
        field_names[field_id]: value
        for field_id, value in values
        if field_id in field_names and value
    }
    return {"name": row.get("name"), "specs": specs, "found": True}


# Name -> handler, as ai.gemini.generate expects it.
TOOLS = {"search_products": search_products, "get_product_specs": get_product_specs}

DECLARATIONS = [SEARCH_PRODUCTS_DECLARATION, GET_PRODUCT_SPECS_DECLARATION]
