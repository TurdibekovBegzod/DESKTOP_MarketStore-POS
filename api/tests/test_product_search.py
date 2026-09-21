"""Tests for the product search tool's filters, currency and output shape.

The statement itself is Postgres-only - trigram ``%``, ``::numeric`` casts and
``= ANY(...)`` - and there is no Postgres in this suite, so these tests drive
the real code with a fake session and assert on the SQL and parameters it
builds. What they prove is that the right query goes out with the right values
bound, and that nothing internal comes back. Whether Postgres accepts it is a
separate question, answered by running it against a live database.
"""

import unittest
from unittest.mock import patch

from ai import tools


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalar(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return self._rows

    def scalars(self):
        return self

    def __iter__(self):
        return iter(self._rows)


class FakeSession:
    """Answers the three queries the tool makes: rate, categories, products."""

    def __init__(self, rate="12000", categories=(), products=()):
        self.rate = rate
        self.categories = list(categories)
        self.products = list(products)
        self.calls = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, params or {}))
        if "rate_to_uzs" in sql:
            return FakeResult([self.rate])
        if "'categories'" in sql:
            return FakeResult(self.categories)
        return FakeResult(self.products)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


PRODUCT = {
    "id": "row-uuid-1",
    "name": "Lenovo IdeaPad 3",
    "price": 3_500_000,
    "cost": 2_900_000,
    "price_currency": "UZS",
    "stock": 2,
    "unit": "dona",
    "category_id": "cat-1",
    "supplier_id": "sup-9",
    "barcode": "4780001",
    "process_customer_phone": "+998901112233",
}


def run(**kwargs):
    """Call the tool with a fake session, returning (result, session)."""
    session = kwargs.pop("session", None) or FakeSession(
        categories=[("cat-1", "Noutbuklar")], products=[PRODUCT]
    )
    with patch.object(tools, "SessionLocal", return_value=session), patch.object(
        tools, "get_settings"
    ) as settings:
        settings.return_value.shop_account_email = kwargs.pop("email", "shop@example.com")
        return tools.search_products(**kwargs), session


def product_sql(session):
    return next(sql for sql, _ in session.calls if "'products'" in sql)


def product_params(session):
    return next(params for sql, params in session.calls if "'products'" in sql)


class ConfigurationTest(unittest.TestCase):
    def test_an_unconfigured_account_is_an_error_not_a_crash(self):
        result, _ = run(name="olma", email="")
        self.assertEqual(result, {"error": "shop account is not configured"})

    def test_a_search_with_no_filters_at_all_is_refused(self):
        result, _ = run()
        self.assertEqual(result["count"], 0)
        self.assertIn("error", result)

    def test_a_blank_name_is_not_treated_as_a_filter(self):
        result, _ = run(name="   ")
        self.assertIn("error", result)


class AccountScopingTest(unittest.TestCase):
    def test_every_query_is_filtered_to_the_configured_account(self):
        _, session = run(name="lenovo")
        for sql, params in session.calls:
            self.assertIn("u.email = :email", sql)
            self.assertEqual(params["email"], "shop@example.com")

    def test_the_account_filter_is_bound_never_interpolated(self):
        _, session = run(name="lenovo", email="a'; DROP TABLE users; --")
        sql = product_sql(session)
        self.assertNotIn("DROP TABLE", sql)
        self.assertEqual(product_params(session)["email"], "a'; DROP TABLE users; --")

    def test_only_product_rows_are_considered(self):
        _, session = run(name="lenovo")
        self.assertIn("r.table_name = 'products'", product_sql(session))

    def test_deleted_rows_are_excluded(self):
        _, session = run(name="lenovo")
        sql = product_sql(session)
        self.assertIn("r.deleted_at IS NULL", sql)
        self.assertIn("is_deleted", sql)


class InjectionTest(unittest.TestCase):
    def test_a_name_containing_sql_is_bound_as_a_value(self):
        evil = "'; DELETE FROM user_records; --"
        _, session = run(name=evil)
        self.assertNotIn("DELETE FROM", product_sql(session))
        self.assertEqual(product_params(session)["name"], evil)

    def test_a_barcode_containing_sql_is_bound_as_a_value(self):
        evil = "1 OR 1=1"
        _, session = run(barcode=evil)
        self.assertEqual(product_params(session)["barcode"], evil)
        self.assertNotIn("OR 1=1", product_sql(session))

    def test_a_category_containing_sql_cannot_reach_the_statement(self):
        _, session = run(category="'; DROP TABLE users; --")
        # No such category, so the tool answers before building a product query.
        self.assertFalse(any("'products'" in sql for sql, _ in session.calls))


class NameFilterTest(unittest.TestCase):
    def test_a_name_uses_the_trigram_operator(self):
        _, session = run(name="lenovo")
        self.assertIn("%", product_sql(session))

    def test_a_name_search_is_ranked_by_similarity(self):
        _, session = run(name="lenovo")
        self.assertIn("similarity", product_sql(session))

    def test_a_search_without_a_name_is_ordered_by_price(self):
        _, session = run(price_max=5_000_000)
        sql = product_sql(session)
        self.assertNotIn("similarity", sql)
        self.assertIn("ASC", sql)

    def test_a_barcode_is_matched_exactly(self):
        _, session = run(barcode="4780001")
        self.assertIn("r.data ->> 'barcode' = :barcode", product_sql(session))


class PriceFilterTest(unittest.TestCase):
    def test_a_minimum_price_is_applied(self):
        _, session = run(price_min=1_000_000)
        self.assertIn(">= :price_min", product_sql(session))
        self.assertEqual(product_params(session)["price_min"], 1_000_000)

    def test_a_maximum_price_is_applied(self):
        _, session = run(price_max=4_000_000)
        self.assertIn("<= :price_max", product_sql(session))
        self.assertEqual(product_params(session)["price_max"], 4_000_000)

    def test_both_bounds_can_be_used_together(self):
        _, session = run(price_min=1_000_000, price_max=4_000_000)
        params = product_params(session)
        self.assertEqual(params["price_min"], 1_000_000)
        self.assertEqual(params["price_max"], 4_000_000)

    def test_a_zero_bound_is_still_a_filter(self):
        _, session = run(price_min=0)
        self.assertIn(">= :price_min", product_sql(session))


class CurrencyTest(unittest.TestCase):
    def test_uzs_needs_no_rate_lookup(self):
        _, session = run(price_max=4_000_000, currency="UZS")
        self.assertFalse(any("rate_to_uzs" in sql for sql, _ in session.calls))

    def test_dollars_are_converted_with_the_shops_own_rate(self):
        session = FakeSession(rate="12000", categories=[], products=[])
        _, session = run(price_max=300, currency="USD", session=session)
        self.assertEqual(product_params(session)["price_max"], 300 * 12000)

    def test_euros_are_converted_too(self):
        session = FakeSession(rate="13500", categories=[], products=[])
        _, session = run(price_max=300, currency="EUR", session=session)
        self.assertEqual(product_params(session)["price_max"], 300 * 13500)

    def test_a_currency_the_shop_does_not_define_is_an_error(self):
        session = FakeSession(rate=None)
        result, _ = run(price_max=300, currency="GBP", session=session)
        self.assertIn("error", result)
        self.assertEqual(result["count"], 0)

    def test_a_nonsense_rate_is_rejected_rather_than_used(self):
        session = FakeSession(rate="not-a-number")
        result, _ = run(price_max=300, currency="USD", session=session)
        self.assertIn("error", result)

    def test_a_zero_rate_is_rejected(self):
        session = FakeSession(rate="0")
        result, _ = run(price_max=300, currency="USD", session=session)
        self.assertIn("error", result)

    def test_the_currency_code_is_matched_case_insensitively(self):
        session = FakeSession(rate="12000", products=[])
        _, session = run(price_max=300, currency="usd", session=session)
        rate_params = next(p for sql, p in session.calls if "rate_to_uzs" in sql)
        self.assertEqual(rate_params["code"], "USD")


class CategoryTest(unittest.TestCase):
    def test_a_known_category_becomes_an_id_filter(self):
        _, session = run(category="noutbuk")
        self.assertIn("category_id", product_sql(session))
        self.assertEqual(product_params(session)["category_ids"], ["cat-1"])

    def test_a_category_is_matched_case_insensitively(self):
        _, session = run(category="NOUTBUK")
        self.assertEqual(product_params(session)["category_ids"], ["cat-1"])

    def test_an_unknown_category_returns_empty_without_querying(self):
        result, session = run(category="kosmik kema")
        self.assertEqual(result, {"products": [], "count": 0})
        self.assertFalse(any("'products'" in sql for sql, _ in session.calls))

    def test_several_matching_categories_are_all_included(self):
        session = FakeSession(
            categories=[("c1", "Noutbuklar"), ("c2", "Noutbuk sumkalari")], products=[]
        )
        _, session = run(category="noutbuk", session=session)
        self.assertEqual(sorted(product_params(session)["category_ids"]), ["c1", "c2"])


class OutputTest(unittest.TestCase):
    def test_the_selling_price_is_always_present(self):
        result, _ = run(name="lenovo")
        self.assertEqual(result["products"][0]["price"], 3_500_000)

    def test_the_purchase_cost_is_never_returned(self):
        result, _ = run(name="lenovo")
        self.assertNotIn("cost", result["products"][0])

    def test_no_uuid_or_internal_id_is_returned(self):
        result, _ = run(name="lenovo")
        product = result["products"][0]
        for field in ("id", "category_id", "supplier_id", "template_id", "section_id"):
            self.assertNotIn(field, product)

    def test_only_whitelisted_fields_are_returned(self):
        result, _ = run(name="lenovo")
        self.assertEqual(set(result["products"][0]), set(tools.VISIBLE_FIELDS))

    def test_internal_process_columns_are_not_returned(self):
        result, _ = run(name="lenovo")
        self.assertNotIn("process_customer_phone", result["products"][0])

    def test_the_category_is_named_not_numbered(self):
        result, _ = run(name="lenovo")
        self.assertEqual(result["products"][0]["category"], "Noutbuklar")

    def test_a_product_with_no_category_still_returns(self):
        row = dict(PRODUCT, category_id=None)
        session = FakeSession(categories=[], products=[row])
        result, _ = run(name="lenovo", session=session)
        self.assertIsNone(result["products"][0]["category"])

    def test_a_missing_unit_falls_back_to_dona(self):
        row = dict(PRODUCT)
        row.pop("unit")
        session = FakeSession(categories=[], products=[row])
        result, _ = run(name="lenovo", session=session)
        self.assertEqual(result["products"][0]["unit"], "dona")

    def test_nothing_found_is_a_count_of_zero_not_an_error(self):
        session = FakeSession(categories=[], products=[])
        result, _ = run(name="yo'q narsa", session=session)
        self.assertEqual(result, {"products": [], "count": 0})

    def test_results_are_capped(self):
        _, session = run(name="lenovo")
        self.assertEqual(product_params(session)["limit"], tools.MAX_RESULTS)


class StockTest(unittest.TestCase):
    def test_out_of_stock_rows_are_excluded_by_default(self):
        _, session = run(name="lenovo")
        self.assertIn("'stock')::numeric, 0) > 0", product_sql(session))

    def test_out_of_stock_rows_can_be_included(self):
        _, session = run(name="lenovo", in_stock_only=False)
        self.assertNotIn("'stock')::numeric, 0) > 0", product_sql(session))


class FailureTest(unittest.TestCase):
    def test_a_database_error_is_reported_not_raised(self):
        class Broken(FakeSession):
            def execute(self, *a, **k):
                raise RuntimeError("connection refused")

        result, _ = run(name="lenovo", session=Broken())
        self.assertEqual(result["error"], "lookup failed")
        self.assertEqual(result["count"], 0)


if __name__ == "__main__":
    unittest.main()
