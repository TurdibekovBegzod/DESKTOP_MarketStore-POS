"""What the agent is allowed to look up, and how it reaches the shop's data.

Products are not a table here. The desktop app owns the schema and syncs whole
rows into ``user_records`` as JSONB, so a lookup is a query over that envelope
filtered to one account's ``products`` rows - see models.UserRecord.

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
MAX_RESULTS = 5

# Below this, trigram similarity matches almost anything. Tuned for product
# names rather than prose.
MIN_SIMILARITY = 0.15


SEARCH_PRODUCTS_DECLARATION = {
    "name": "search_products",
    "description": (
        "Do'kon omboridan mahsulotni nomi yoki shtrix-kodi bo'yicha qidiradi. "
        "Narx, qoldiq va o'lchov birligini qaytaradi. "
        "Mijoz mahsulot, narx yoki mavjudlik haqida so'raganda ishlat."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Mahsulot nomi, uning bir qismi yoki shtrix-kod",
            },
        },
        "required": ["query"],
    },
}


# Ordering by similarity, not just filtering by it: "olma sharbati" should beat
# "olma" for the query "olma sharbat". The ``%`` operator is what the GIN index
# in migration 0013 answers, so the index does the filtering and the ranking
# only touches rows that already matched.
_SEARCH_SQL = text(
    """
    SELECT r.data
    FROM user_records AS r
    JOIN users AS u ON u.id = r.user_id
    WHERE u.email = :email
      AND r.table_name = 'products'
      AND r.deleted_at IS NULL
      AND COALESCE((r.data ->> 'is_deleted')::int, 0) = 0
      AND (
            r.data ->> 'name' %% :query
         OR r.data ->> 'barcode' = :exact
      )
    ORDER BY similarity(COALESCE(r.data ->> 'name', ''), :query) DESC
    LIMIT :limit
    """
)


def _describe(row: dict) -> dict:
    """The few fields a customer actually asked about.

    Cost, supplier and the process_* columns stay out: they are the shop's
    internal margin data and the model would happily repeat them in a DM.
    """
    return {
        "name": row.get("name"),
        "price": row.get("price"),
        "currency": row.get("price_currency") or "UZS",
        "stock": row.get("stock"),
        "unit": row.get("unit") or "dona",
    }


def search_products(query: str) -> dict:
    """Look up products for the account the bot answers for.

    Returns a dict rather than raising: the result goes back to the model as a
    functionResponse, and "nothing found" is an answer the model can relay,
    where an exception would cost the customer their reply entirely.
    """
    email = (get_settings().shop_account_email or "").strip()
    if not email:
        return {"error": "shop account is not configured"}

    text_query = (query or "").strip()
    if not text_query:
        return {"products": [], "count": 0}

    try:
        with SessionLocal() as session:
            rows = session.execute(
                _SEARCH_SQL,
                {
                    "email": email,
                    "query": text_query,
                    "exact": text_query,
                    "limit": MAX_RESULTS,
                },
            ).scalars().all()
    except Exception:
        logger.exception("product search failed for %r", text_query)
        return {"error": "lookup failed"}

    products = [_describe(row) for row in rows if isinstance(row, dict)]
    return {"products": products, "count": len(products)}


# Name -> handler, as ai.gemini.generate expects it.
TOOLS = {"search_products": search_products}

DECLARATIONS = [SEARCH_PRODUCTS_DECLARATION]
