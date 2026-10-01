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


def _load_specs(session, target_val: str, product_ids: list[str], target_type: str = "email") -> dict:
    """product_id -> {field name: value}, for every id that has any.

    One join instead of one query per row: a list of results can be up to
    MAX_RESULTS products, and asking the database once scales the same
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
    params["limit"] = MAX_RESULTS

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

    products = [_describe(row, categories, specs) for row in rows if isinstance(row, dict)]
    return {"products": products, "count": len(products)}


def get_product_specs(name: str = "") -> dict:
    """Technical fields for one product, looked up by its closest name match.

    A shop often keeps several rows under one name - one per unit, or an old
    sold-out row beside the new stock - and only some of them have their fields
    filled in. Taking the single closest row used to land on one of the empty
    ones, so the bot said it had no specs for a laptop whose specs were right
    there. Every row tied for the closest name is considered instead, and one
    that has specs wins, in-stock first.

    Only exact ties count: a row whose name is merely close is another model,
    and its specs would be a wrong answer, which is worse than none.

    Specs come from _load_specs, the same lookup search_products uses, so the
    two tools cannot disagree about one product.
    """
    target_type, target_val, scope_params = _resolve_target()
    if not target_type:
        return {"error": "shop account is not configured"}

    name = (name or "").strip()
    if not name:
        return {"error": "mahsulot nomi kerak", "specs": {}}

    if target_type == "uid":
        from_sql = "FROM user_records AS r"
        scope_sql = "r.user_uid = :user_uid"
    else:
        from_sql = "FROM user_records AS r JOIN users AS u ON u.id = r.user_id"
        scope_sql = "u.email = :email"

    statement = text(
        f"""
        SELECT r.data, similarity(COALESCE(r.data ->> 'name', ''), :name) AS score
        {from_sql}
        WHERE {scope_sql}
          AND r.table_name = 'products'
          AND r.deleted_at IS NULL
          AND COALESCE((r.data ->> 'is_deleted')::int, 0) = 0
          AND (r.data ->> 'name') % :name
        ORDER BY score DESC,
                 (COALESCE((r.data ->> 'stock')::numeric, 0) > 0) DESC
        LIMIT :limit
        """
    )
    params = {**scope_params, "name": name, "limit": MAX_RESULTS}

    try:
        with SessionLocal() as session:
            candidates = [
                (data, score)
                for data, score in session.execute(statement, params).all()
                if isinstance(data, dict)
            ]
            if not candidates:
                return {"specs": {}, "found": False, "count": 0}

            best_score = candidates[0][1]
            tied = [data for data, score in candidates if score == best_score]
            ids = [data.get("id") for data in tied if data.get("id")]
            specs = _load_specs(session, target_val, ids, target_type)
    except Exception:
        logger.exception("product specs lookup failed: name=%r", name)
        return {"error": "lookup failed", "specs": {}}

    chosen = next((data for data in tied if specs.get(data.get("id"))), tied[0])
    product_specs = specs.get(chosen.get("id")) or {}
    return {
        "name": chosen.get("name"),
        "specs": product_specs,
        "found": True,
        # Logged by ai.gemini next to found, so an empty answer shows up in the
        # logs as count=0 rather than hiding behind found=True.
        "count": len(product_specs),
    }


SEARCH_SHOP_RULES_DECLARATION = {
    "name": "search_shop_rules",
    "description": (
        "Do'kon egasi yozgan qoidalarni qidiradi - yetkazib berish, kafolat, "
        "to'lov, qaytarib berish, ish vaqti, chegirma, muddatli to'lov va shu "
        "kabi do'kon shartlari. Mijoz mahsulotning o'zi emas, balki do'kon "
        "sharti haqida so'raganda ishlat. Qidiruv ma'no bo'yicha, shuning "
        "uchun so'rovni mijoz savolining mazmuni bilan, o'zbek tilida yoz "
        "(masalan 'kafolat muddati', 'yetkazib berish narxi'). Natijada "
        "qaytgan qoidalar savolga o'xshashligi bo'yicha tanlanadi va "
        "saralanmaydi - ichida aloqasiz qoidalar ham bo'ladi, faqat savolga "
        "javob beradiganini ishlat. Bo'sh qaytsa yoki mos qoida bo'lmasa, "
        "do'kon bu shartni yozmagan - o'zingdan to'qima, ma'lumot yo'qligini "
        "ayt. Bir savolda bir nechta shart so'ralsa (masalan kafolat ham, "
        "yetkazib berish ham), har biri uchun alohida chaqir."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Nimani qidirayotganing, o'zbekcha va mazmunli. Mijoz "
                    "savolini qisqartirib yoz, bitta so'z bilan cheklanma."
                ),
            },
        },
        "required": ["query"],
    },
}


def search_shop_rules(query: str = "") -> dict:
    """The shop's own rules that come closest in meaning to ``query``.

    Imported lazily, inside the function: app.rules_service imports this module
    for the store context, so a module-level import here would be circular.

    Returns a dict rather than raising, like the other tools - "no rules" is an
    answer the model can act on, where an exception would cost the customer their
    reply. ``found`` is stated explicitly so an empty list cannot be mistaken for
    a lookup that failed.
    """
    from app import rules_service

    ctx = get_store_context()
    user_uid = getattr(ctx, "user_uid", None) if ctx else None
    if not user_uid:
        # The rules table is keyed by account and there is no email fallback for
        # it: answering out of the wrong shop's rules is worse than not
        # answering, so an unresolved account returns nothing.
        return {"error": "shop account is not configured", "rules": [], "found": False}

    question = (query or "").strip()
    if not question:
        return {"error": "qidiruv so'rovi kerak", "rules": [], "found": False}

    rules = rules_service.search_rules(user_uid, question)
    return {
        # Highest priority first, so a contradiction is read in the order the
        # shop meant it to be.
        "rules": [rule.text for rule in sorted(rules, key=lambda r: -r.priority)],
        "count": len(rules),
        "found": bool(rules),
    }


# Name -> handler, as ai.gemini.generate expects it.
#
# search_shop_rules is deliberately left out: the shop's rules now go into the
# system prompt whole (see ai.agent.system_prompt_for). The handler and its
# declaration stay above so the tool can be switched back on by adding it here.
TOOLS = {
    "search_products": search_products,
    "get_product_specs": get_product_specs,
}

DECLARATIONS = [
    SEARCH_PRODUCTS_DECLARATION,
    GET_PRODUCT_SPECS_DECLARATION,
]
