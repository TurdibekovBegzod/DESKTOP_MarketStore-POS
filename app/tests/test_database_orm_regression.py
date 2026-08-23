import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta

import database as db


class DatabaseOrmRegressionTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.old_path = db.DB_PATH
        db.DB_PATH = self.path
        db.init_db()

    def tearDown(self):
        try:
            db._get_engine().dispose()
        finally:
            db.DB_PATH = self.old_path
            for suffix in ("", "-shm", "-wal"):
                path = self.path + suffix
                if os.path.exists(path):
                    os.remove(path)
            for file_name in os.listdir(os.path.dirname(self.path) or "."):
                if file_name.startswith(os.path.basename(self.path) + ".backup_"):
                    os.remove(os.path.join(os.path.dirname(self.path), file_name))

    def test_init_db_migrates_old_database_without_losing_products(self):
        db._get_engine().dispose()
        for suffix in ("", "-shm", "-wal"):
            path = self.path + suffix
            if os.path.exists(path):
                os.remove(path)
        conn = sqlite3.connect(self.path)
        try:
            conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL, role TEXT)")
            conn.execute("CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT)")
            conn.execute("CREATE TABLE categories (id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL)")
            conn.execute("CREATE TABLE debtors (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
            conn.execute("""
                CREATE TABLE products (
                    id INTEGER PRIMARY KEY,
                    barcode TEXT UNIQUE,
                    name TEXT NOT NULL,
                    category_id INTEGER,
                    price REAL NOT NULL DEFAULT 0,
                    cost REAL NOT NULL DEFAULT 0,
                    stock INTEGER NOT NULL DEFAULT 0,
                    unit TEXT DEFAULT 'dona'
                )
            """)
            conn.execute(
                "INSERT INTO products (barcode, name, price, cost, stock, unit) VALUES (?, ?, ?, ?, ?, ?)",
                ("OLD1", "Old Product", 1000, 700, 5, "dona"),
            )
            conn.commit()
        finally:
            conn.close()

        db.init_db()

        product = db.get_product_by_barcode("OLD1")
        self.assertIsNotNone(product)
        self.assertEqual(product["name"], "Old Product")
        self.assertEqual(product["stock"], 5)
        self.assertIsNotNone(product["section_id"])
        self.assertEqual(product["is_deleted"], 0)

        applied_versions = {row["version"] for row in db.get_applied_migrations()}
        self.assertIn("001_create_missing_tables", applied_versions)
        self.assertIn("002_add_missing_columns", applied_versions)

        conn = sqlite3.connect(self.path)
        try:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(products)").fetchall()}
        finally:
            conn.close()
        self.assertIn("section_id", columns)
        self.assertIn("process_quantity", columns)
        self.assertIn("purge_after", columns)
        self.assertIn("created_by_user_id", columns)

        conn = sqlite3.connect(self.path)
        try:
            sale_item_columns = {row[1] for row in conn.execute("PRAGMA table_info(sale_items)").fetchall()}
        finally:
            conn.close()
        self.assertIn("returned_at", sale_item_columns)

        conn = sqlite3.connect(self.path)
        try:
            debtor_columns = {row[1] for row in conn.execute("PRAGMA table_info(debtors)").fetchall()}
        finally:
            conn.close()
        self.assertIn("user_id", debtor_columns)

    def test_settings_auth_users_and_login_history(self):
        settings = db.get_app_settings()
        self.assertEqual(settings["app_name"], "Market POS")
        db.save_app_settings({"app_name": "Test POS", "theme": "green", "language": "en", "currency": "USD"})
        self.assertEqual(db.get_app_settings()["app_name"], "Test POS")
        self.assertEqual(db.get_app_settings()["currency"], "USD")

        admin = db.authenticate("admin@gmail.com", "admin123")
        self.assertIsNotNone(admin)
        before = datetime.now()
        db.log_login(admin)
        log = db.get_login_logs(1)[0]
        self.assertEqual(log["username"], "admin@gmail.com")

        logged_at = datetime.strptime(log["logged_at"], "%Y-%m-%d %H:%M:%S")
        self.assertLess(abs((logged_at - before).total_seconds()), 120)
        self.assertEqual(db.get_recent_login_user()["email"], "admin@gmail.com")
        with db.session_scope() as session:
            latest = session.scalar(db.select(db.LoginLog).order_by(db.LoginLog.logged_at.desc()))
            latest.logged_at = (datetime.utcnow() - timedelta(minutes=16)).strftime("%Y-%m-%d %H:%M:%S")
        self.assertIsNone(db.get_recent_login_user())
        db.touch_user_activity(admin["id"])
        self.assertEqual(db.get_recent_activity_user()["email"], "admin@gmail.com")
        db.clear_user_activity(admin["id"])
        self.assertIsNone(db.get_recent_activity_user())
        db.touch_user_activity(admin["id"])
        with db.session_scope() as session:
            activity = session.get(db.UserSetting, {"user_id": admin["id"], "key": "last_activity_utc"})
            activity.value = (datetime.utcnow() - timedelta(minutes=16)).strftime("%Y-%m-%d %H:%M:%S")
        self.assertIsNone(db.get_recent_activity_user())
        db.clear_login_logs()
        self.assertEqual(db.get_login_logs(), [])

        db.add_user("cashier@gmail.com", "pass", "cashier")
        user = [row for row in db.get_users() if row["email"] == "cashier@gmail.com"][0]
        db.save_app_settings({"theme": "light_blue", "language": "ru"}, user["id"])
        self.assertEqual(db.get_app_settings(user["id"])["language"], "ru")
        db.touch_user_activity(user["id"])
        db.update_user(user["id"], "cashier2@gmail.com", "pass2", "admin")
        self.assertIsNone(db.get_recent_activity_user())
        self.assertIsNotNone(db.authenticate("cashier2@gmail.com", "pass2"))
        db.delete_user(user["id"])
        self.assertFalse([row for row in db.get_users() if row["email"] == "cashier2@gmail.com"])

    def test_return_timestamp_migration_backfills_existing_returns(self):
        admin = db.authenticate("admin@gmail.com", "admin123")
        product_id = db.add_product({
            "barcode": "RETURN-TIME-1",
            "name": "Return timestamp product",
            "template_id": None,
            "supplier_id": None,
            "category_id": None,
            "price": 1000,
            "cost": 600,
            "stock": 2,
            "unit": "dona",
        })
        db.create_sale(
            None,
            admin["id"],
            [{"product_id": product_id, "quantity": 1, "price": 1000, "subtotal": 1000}],
            1000,
            0,
            1000,
            "naqd",
        )
        sale_item_id = db.get_product_sales_archive("Return timestamp product")[0]["sale_item_id"]
        db.return_sale_item(sale_item_id, 1)

        db._get_engine().dispose()
        conn = sqlite3.connect(self.path)
        try:
            movement_time = conn.execute(
                "SELECT MAX(created_at) FROM stock_movements WHERE product_id=? AND type='qaytarish'",
                (product_id,),
            ).fetchone()[0]
            conn.execute("ALTER TABLE sale_items DROP COLUMN returned_at")
            conn.execute("DELETE FROM schema_migrations WHERE version='010_add_sale_item_returned_at'")
            conn.commit()
        finally:
            conn.close()

        db.run_migrations()
        conn = sqlite3.connect(self.path)
        try:
            returned_at = conn.execute(
                "SELECT returned_at FROM sale_items WHERE id=?",
                (sale_item_id,),
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(returned_at, movement_time)

    def test_products_templates_categories_currencies_and_stock(self):
        cat_id = db.add_category("Texnika")
        db.update_category(cat_id, "Elektronika")
        self.assertEqual(db.get_categories()[0]["name"], "Elektronika")

        template_id = db.add_template("Telefon", [
            {"name": "Brend", "required": True},
            {"name": "Model", "field_type": "text"},
        ])
        fields = db.get_template_fields(template_id)
        self.assertEqual(len(fields), 2)
        db.update_template(template_id, "Smartfon", [{"name": "Brend"}])
        self.assertEqual(len(db.get_template_fields(template_id)), 1)

        supplier_id = db.add_supplier("Supplier")
        db.save_currency("GBP", "Pound", 16000)
        self.assertEqual(db.get_currency("GBP")["rate_to_uzs"], 16000)
        db.delete_currency("GBP")
        self.assertIsNone(db.get_currency("GBP"))

        product_id = db.add_product({
            "barcode": "P100",
            "name": "Phone",
            "template_id": template_id,
            "supplier_id": supplier_id,
            "category_id": cat_id,
            "price": 1200,
            "cost": 800,
            "stock": 10,
            "unit": "dona",
        })
        db.save_product_attributes(product_id, {fields[0]["id"]: "Apple"})
        self.assertEqual(db.get_product_attributes(product_id)[fields[0]["id"]], "Apple")
        db.save_product_attributes(product_id, {fields[0]["id"]: "Lenovo"})
        self.assertEqual(db.get_product_attributes(product_id)[fields[0]["id"]], "Lenovo")
        db.save_product_attributes(product_id, {fields[0]["id"]: ""})
        self.assertEqual(db.get_product_attributes(product_id), {})
        db.save_product_attributes(product_id, {fields[0]["id"]: "Apple"})
        self.assertEqual(db.get_product_attribute_details(product_id)[0]["name"], "Brend")
        self.assertEqual(db.get_product_by_id(product_id)["name"], "Phone")
        product = db.get_product_by_barcode("P100")
        self.assertEqual(product["category_name"], "Elektronika")
        self.assertEqual(db.search_products("Pho")[0]["supplier_name"], "Supplier")

        db.add_stock(product_id, 5, "kirim")
        self.assertEqual(db.get_product_by_barcode("P100")["stock"], 15)
        with self.assertRaises(db.AppError):
            db.add_stock(product_id, 0, "bad")
        usd_product_id = db.add_product({
            "barcode": "USD1",
            "name": "USD Product",
            "template_id": None,
            "supplier_id": supplier_id,
            "category_id": cat_id,
            "price": 12000,
            "cost": 6000,
            "price_currency": "USD",
            "price_exchange_rate": 12000,
            "price_original": 1,
            "cost_currency": "USD",
            "cost_exchange_rate": 12000,
            "cost_original": 0.5,
            "stock": 1,
            "unit": "dona",
        })
        db.save_currency("USD", "US Dollar", 13000)
        usd_product = db.get_product_by_id(usd_product_id)
        self.assertEqual(usd_product["price"], 13000)
        self.assertEqual(usd_product["cost"], 6500)
        self.assertEqual(usd_product["price_exchange_rate"], 13000)
        db.update_product(product_id, {
            "barcode": "P101",
            "name": "Phone 2",
            "template_id": template_id,
            "supplier_id": supplier_id,
            "category_id": cat_id,
            "price": 1300,
            "cost": 850,
            "stock": 20,
            "unit": "dona",
        })
        self.assertEqual(db.get_product_by_barcode("P101")["name"], "Phone 2")
        second_id = db.add_product({
            "barcode": "P102",
            "name": "Phone duplicate target",
            "template_id": template_id,
            "supplier_id": supplier_id,
            "category_id": cat_id,
            "price": 1100,
            "cost": 700,
            "stock": 1,
            "unit": "dona",
        })
        with self.assertRaises(db.AppError):
            db.add_product({
                "barcode": "P102",
                "name": "Duplicate barcode",
                "template_id": template_id,
                "supplier_id": supplier_id,
                "category_id": cat_id,
                "price": 1100,
                "cost": 700,
                "stock": 1,
                "unit": "dona",
            })
        with self.assertRaises(db.AppError):
            db.update_product(second_id, {
                "barcode": "P101",
                "name": "Duplicate edit",
                "template_id": template_id,
                "supplier_id": supplier_id,
                "category_id": cat_id,
                "price": 1100,
                "cost": 700,
                "stock": 1,
                "unit": "dona",
            })
        db.put_product_in_process(product_id, 2, 100, "UZS", "Ali", "901")
        self.assertEqual(db.get_product_by_barcode("P101")["process_quantity"], 2)
        db.update_product_process(product_id, 4, 200, "USD", "Vali", "902")
        product = db.get_product_by_barcode("P101")
        self.assertEqual(product["stock"] - product["process_quantity"], 16)
        db.reduce_product_process(product_id, 1)
        self.assertEqual(db.get_product_by_barcode("P101")["process_quantity"], 3)
        db.clear_product_process(product_id)
        self.assertEqual(db.get_product_by_barcode("P101")["process_quantity"], 0)
        db.set_product_process_status(product_id, "process")
        self.assertEqual(db.get_product_by_barcode("P101")["process_status"], "process")
        db.delete_product(product_id)
        self.assertIsNone(db.get_product_by_barcode("P101"))

    def test_inventory_checking_flow(self):
        product_id = db.add_product({
            "barcode": "CHK1",
            "name": "Check Product",
            "template_id": None,
            "supplier_id": None,
            "category_id": None,
            "price": 100,
            "cost": 50,
            "stock": 3,
            "unit": "dona",
        })
        session_id = db.start_inventory_check()
        self.assertEqual(db.get_active_inventory_check()["id"], session_id)
        counts = db.get_inventory_check_counts(session_id)
        self.assertEqual(counts["total"], 1)
        item = db.mark_inventory_product_checked(session_id, "CHK1", 1)
        self.assertIsNone(item["checked_at"])
        item = db.mark_inventory_product_checked(session_id, "CHK1", 2)
        self.assertIsNotNone(item["checked_at"])
        self.assertEqual(len(db.get_inventory_check_items(session_id, True)), 1)
        result = db.finish_inventory_check(session_id)
        self.assertEqual(result["checked_quantity"], 3)
        self.assertIsNone(db.get_active_inventory_check())
        self.assertEqual(db.get_product_by_barcode("CHK1")["id"], product_id)

    def test_inventory_checking_section_and_template_filters(self):
        section_a = db.add_product_section("Check Section A")
        section_b = db.add_product_section("Check Section B")
        template_a = db.add_template("Check Template A", [{"name": "Brand"}], section_a)
        template_b = db.add_template("Check Template B", [{"name": "Brand"}], section_b)
        product_a = db.add_product({
            "barcode": "FCHK1",
            "name": "Filtered A",
            "section_id": section_a,
            "template_id": template_a,
            "price": 100,
            "cost": 50,
            "stock": 4,
            "unit": "dona",
        })
        db.add_product({
            "barcode": "FCHK2",
            "name": "Filtered B",
            "section_id": section_b,
            "template_id": template_b,
            "price": 100,
            "cost": 50,
            "stock": 7,
            "unit": "dona",
        })
        session_id = db.start_inventory_check()
        section_counts = db.get_inventory_check_counts(session_id, section_a)
        self.assertEqual(section_counts["total"], 1)
        self.assertEqual(section_counts["total_quantity"], 4)
        template_items = db.get_inventory_check_items(session_id, False, section_a, template_a)
        self.assertEqual([item["product_id"] for item in template_items], [product_a])
        with self.assertRaises(db.AppError):
            db.mark_inventory_product_checked(session_id, "FCHK2", 1, section_a, template_a)
        db.mark_inventory_product_checked(session_id, "FCHK1", 4, section_a, template_a)
        self.assertEqual(db.get_inventory_check_counts(session_id, section_a, template_a)["checked_quantity"], 4)
        db.finish_inventory_check(session_id)

    def test_sales_returns_reports_and_clear_history(self):
        customer_id = db.add_customer("Customer", "99890", "c@example.com")
        db.update_customer(customer_id, "Customer 2", "99891", "c2@example.com")
        self.assertEqual(db.get_all_customers()[0]["name"], "Customer 2")
        admin = db.authenticate("admin@gmail.com", "admin123")
        product_id = db.add_product({
            "barcode": "SALE1",
            "name": "Sale Product",
            "template_id": None,
            "supplier_id": None,
            "category_id": None,
            "price": 1000,
            "cost": 600,
            "stock": 10,
            "unit": "dona",
        })
        before_sale = datetime.now()
        sale_id = db.create_sale(
            customer_id,
            admin["id"],
            [{"product_id": product_id, "quantity": 3, "price": 1000, "subtotal": 3000}],
            3000,
            100,
            2900,
            "naqd",
            customer_name="Manual Customer",
            customer_phone="999",
        )
        sale_row = db.get_sales_today()[0]
        self.assertEqual(sale_row["id"], sale_id)
        sale_created_at = datetime.strptime(sale_row["created_at"], "%Y-%m-%d %H:%M:%S")
        self.assertLess(abs((sale_created_at - before_sale).total_seconds()), 120)
        self.assertEqual(db.get_sale_items(sale_id)[0]["product_name"], "Sale Product")
        archive = db.get_product_sales_archive("Manual")
        self.assertEqual(archive[0]["customer_phone"], "999")
        archive_created_at = datetime.strptime(archive[0]["created_at"], "%Y-%m-%d %H:%M:%S")
        self.assertLess(abs((archive_created_at - before_sale).total_seconds()), 120)
        self.assertEqual(db.get_sale_cost(sale_id), 1800)

        # Finalize sale so it is included in reports
        db.finalize_sale(sale_id)

        today = datetime.now().strftime("%Y-%m-%d")
        self.assertEqual(db.get_daily_report(today)["count"], 1)
        self.assertTrue(db.get_sales_by_date(today))
        self.assertTrue(db.get_cashier_report(today))
        self.assertTrue(db.get_cashier_sold_items(today))
        self.assertTrue(db.get_overall_period_series("2000-01-01", "2999-01-01"))
        self.assertTrue(db.get_cashier_period_summary("2000-01-01", "2999-01-01"))
        self.assertTrue(db.get_customer_period_summary("2000-01-01", "2999-01-01"))
        self.assertTrue(db.get_entity_period_series("cashier", admin["id"], "2000-01-01", "2999-01-01"))
        self.assertTrue(db.get_entity_period_series("customer", customer_id, "2000-01-01", "2999-01-01"))

        section_id = db.add_product_section("Report Section")
        section_product_id = db.add_product({
            "barcode": "SECSALE",
            "name": "Section Sale Product",
            "section_id": section_id,
            "template_id": None,
            "supplier_id": None,
            "category_id": None,
            "price": 500,
            "cost": 300,
            "stock": 5,
            "unit": "dona",
        })
        db.add_user("report.cashier@example.com", role="cashier", username="Report Cashier")
        report_cashier = next(user for user in db.get_users() if user["email"] == "report.cashier@example.com")
        sec_sale_id = db.create_sale(
            None,
            report_cashier["id"],
            [{"product_id": section_product_id, "quantity": 2, "price": 500, "subtotal": 1000}],
            1000,
            0,
            1000,
            "naqd",
        )
        pending_details = db.get_cashier_sales_details(
            report_cashier["id"],
            "2000-01-01",
            "2999-01-01",
            section_id,
            only_cashiers=True,
        )
        self.assertEqual(len(pending_details), 1)
        self.assertEqual(pending_details[0]["is_finalized"], 0)
        self.assertEqual(pending_details[0]["cashier_reward"], 0)
        db.finalize_sale(sec_sale_id, cashier_reward=150)
        section_rows = db.get_overall_period_series("2000-01-01", "2999-01-01", section_id)
        self.assertEqual(sum(row["sales_count"] for row in section_rows), 1)
        self.assertEqual(sum(row["product_count"] for row in section_rows), 2)
        self.assertEqual(sum(row["revenue"] for row in section_rows), 1000)
        self.assertEqual(sum(row["profit"] for row in section_rows), 400)
        self.assertEqual(sum(row["cashier_reward"] for row in section_rows), 150)
        cashier_rows = db.get_cashier_period_summary("2000-01-01", "2999-01-01", section_id)
        cashier_row = [row for row in cashier_rows if row["entity_id"] == report_cashier["id"]][0]
        self.assertEqual(cashier_row["product_count"], 2)
        salary_rows = db.get_cashier_salary_period_summary("2000-01-01", "2999-01-01", section_id)
        salary_row = [row for row in salary_rows if row["entity_id"] == report_cashier["id"]][0]
        self.assertEqual(salary_row["total_salary"], 150)
        salary_series = db.get_entity_period_series("cashier", report_cashier["id"], "2000-01-01", "2999-01-01", section_id)
        self.assertEqual(sum(row["total_salary"] for row in salary_series), 150)
        sale_details = db.get_cashier_sales_details(
            report_cashier["id"],
            "2000-01-01",
            "2999-01-01",
            section_id,
            only_cashiers=True,
        )
        self.assertEqual(len(sale_details), 1)
        self.assertEqual(sale_details[0]["product_name"], "Section Sale Product")
        self.assertEqual(sale_details[0]["net_quantity"], 2)
        self.assertEqual(sale_details[0]["item_total_after_discount"], 1000)
        self.assertEqual(sale_details[0]["is_finalized"], 1)
        self.assertEqual(sale_details[0]["cashier_reward"], 150)
        db.return_sale_item(sale_details[0]["sale_item_id"], 2, "full return")
        returned_details = db.get_cashier_sales_details(
            report_cashier["id"],
            "2000-01-01",
            "2999-01-01",
            section_id,
            only_cashiers=True,
        )
        self.assertEqual(len(returned_details), 1)
        self.assertEqual(returned_details[0]["sold_quantity"], 2)
        self.assertEqual(returned_details[0]["returned_quantity"], 2)
        self.assertIsNotNone(returned_details[0]["returned_at"])
        self.assertEqual(returned_details[0]["net_quantity"], 0)
        self.assertEqual(returned_details[0]["item_total_after_discount"], 0)
        self.assertEqual(returned_details[0]["cashier_reward"], 0)
        self.assertEqual(
            db.get_cashier_sales_details(admin["id"], "2000-01-01", "2999-01-01", only_cashiers=True),
            [],
        )

        db.return_sale_item(archive[0]["sale_item_id"], 1, "return")
        self.assertEqual(db.get_product_by_barcode("SALE1")["stock"], 8)
        db.clear_sales_history()
        self.assertEqual(db.get_product_sales_archive(), [])

    def test_delete_sale_item_restores_stock_and_tracks_sync_deletion(self):
        admin = db.authenticate("admin@gmail.com", "admin123")
        product_id = db.add_product({
            "barcode": "DEL-SALE-1",
            "name": "Delete sold item",
            "template_id": None,
            "supplier_id": None,
            "category_id": None,
            "price": 1200,
            "cost": 700,
            "stock": 5,
            "unit": "dona",
        })
        sale_id = db.create_sale(
            None,
            admin["id"],
            [{"product_id": product_id, "quantity": 2, "price": 1200, "subtotal": 2400}],
            2400,
            0,
            2400,
            "naqd",
        )
        archive_row = db.get_product_sales_archive("Delete sold item")[0]
        self.assertEqual(db.get_product_by_id(product_id)["stock"], 3)

        db.delete_sale_item(archive_row["sale_item_id"])

        self.assertEqual(db.get_product_by_id(product_id)["stock"], 5)
        self.assertEqual(db.get_product_sales_archive("Delete sold item"), [])
        with db.session_scope() as session:
            self.assertIsNotNone(session.get(db.SyncTombstone, {
                "table_name": "sale_items",
                "local_id": str(archive_row["sale_item_id"]),
            }))
            self.assertIsNotNone(session.get(db.SyncTombstone, {
                "table_name": "sales",
                "local_id": str(sale_id),
            }))

    def test_sale_respects_process_reserved_stock(self):
        admin = db.authenticate("admin@gmail.com", "admin123")
        product_id = db.add_product({
            "barcode": "RSV1",
            "name": "Reserved Product",
            "template_id": None,
            "supplier_id": None,
            "category_id": None,
            "price": 1000,
            "cost": 600,
            "stock": 5,
            "unit": "dona",
        })
        db.put_product_in_process(product_id, 4, 0, "UZS", "Ali", "901")
        with self.assertRaises(db.AppError):
            db.create_sale(
                None,
                admin["id"],
                [{"product_id": product_id, "quantity": 2, "price": 1000, "subtotal": 2000}],
                2000,
                0,
                2000,
                "naqd",
            )
        db.create_sale(
            None,
            admin["id"],
            [{"product_id": product_id, "quantity": 1, "price": 1000, "subtotal": 1000}],
            1000,
            0,
            1000,
            "naqd",
        )
        self.assertEqual(db.get_product_by_barcode("RSV1")["stock"], 4)

    def test_cashier_expense_is_deducted_from_salary(self):
        admin = db.authenticate("admin@gmail.com", "admin123")
        cashier_id = db.add_user(
            "salary.cashier@example.com",
            role="cashier",
            username="Salary Cashier",
        )
        product_id = db.add_product({
            "barcode": "SALARY-1",
            "name": "Salary product",
            "template_id": None,
            "supplier_id": None,
            "category_id": None,
            "price": 5000,
            "cost": 3000,
            "stock": 2,
            "unit": "dona",
        })
        sale_id = db.create_sale(
            None,
            cashier_id,
            [{"product_id": product_id, "quantity": 1, "price": 5000, "subtotal": 5000}],
            5000,
            0,
            5000,
            "naqd",
        )
        db.finalize_sale(sale_id, cashier_reward=1000)
        cashier_category_id = next(
            category["id"]
            for category in db.get_expense_categories()
            if db.is_cashier_expense_category_name(category["name"])
        )
        expense_id = db.add_expense(
            cashier_category_id,
            200,
            "UZS",
            "Salary advance",
            admin["id"],
            cashier_id,
        )

        salary_row = next(
            row for row in db.get_cashier_salary_period_summary("2000-01-01", "2999-01-01")
            if row["entity_id"] == cashier_id
        )
        self.assertEqual(salary_row["salary_deduction"], 200)
        self.assertEqual(salary_row["total_salary"], 800)
        series = db.get_entity_period_series(
            "cashier_salary", cashier_id, "2000-01-01", "2999-01-01"
        )
        self.assertEqual(sum(row["total_salary"] for row in series), 800)
        expense = next(row for row in db.get_expenses() if row["id"] == expense_id)
        self.assertEqual(expense["cashier_id"], cashier_id)
        self.assertEqual(expense["cashier_name"], "Salary Cashier")
        with self.assertRaises(db.AppError):
            db.add_expense(
                cashier_category_id,
                50,
                "UZS",
                "Invalid owner selection",
                admin["id"],
                admin["id"],
            )

    def test_discount_archive_stays_proportional_after_return(self):
        admin = db.authenticate("admin@gmail.com", "admin123")
        p1 = db.add_product({
            "barcode": "DISC1",
            "name": "Discount 1",
            "template_id": None,
            "supplier_id": None,
            "category_id": None,
            "price": 200,
            "cost": 100,
            "stock": 5,
            "unit": "dona",
        })
        p2 = db.add_product({
            "barcode": "DISC2",
            "name": "Discount 2",
            "template_id": None,
            "supplier_id": None,
            "category_id": None,
            "price": 300,
            "cost": 150,
            "stock": 5,
            "unit": "dona",
        })
        db.create_sale(
            None,
            admin["id"],
            [
                {"product_id": p1, "quantity": 1, "price": 200, "subtotal": 200},
                {"product_id": p2, "quantity": 1, "price": 300, "subtotal": 300},
            ],
            500,
            20,
            480,
            "naqd",
        )
        rows = {row["barcode"]: row for row in db.get_product_sales_archive()}
        self.assertAlmostEqual(rows["DISC1"]["item_discount"], 8)
        self.assertAlmostEqual(rows["DISC2"]["item_discount"], 12)
        db.return_sale_item(rows["DISC2"]["sale_item_id"], 1, "return")
        rows = {row["barcode"]: row for row in db.get_product_sales_archive()}
        self.assertNotIn("DISC2", rows)
        self.assertAlmostEqual(rows["DISC1"]["item_discount"], 8)
        self.assertAlmostEqual(rows["DISC1"]["item_total_after_discount"], 192)

    def test_product_trash_only_tracks_deleted_items(self):
        admin = db.authenticate("admin@gmail.com", "admin123")
        section_id = db.add_product_section("Trash Section")
        template_id = db.add_template("Trash Template", [{"name": "Brand"}], section_id)
        sold_product_id = db.add_product({
            "barcode": "TRSOLD",
            "name": "Sold Product",
            "section_id": section_id,
            "template_id": template_id,
            "price": 1000,
            "cost": 500,
            "stock": 5,
            "unit": "dona",
        })
        deleted_product_id = db.add_product({
            "barcode": "TRDEL",
            "name": "Deleted Product",
            "section_id": section_id,
            "template_id": template_id,
            "price": 1000,
            "cost": 500,
            "stock": 5,
            "unit": "dona",
        })
        db.create_sale(
            None,
            admin["id"],
            [{"product_id": sold_product_id, "quantity": 1, "price": 1000, "subtotal": 1000}],
            1000,
            0,
            1000,
            "naqd",
        )
        trash = db.get_product_trash()
        self.assertFalse(trash["sections"])
        self.assertFalse(trash["products"])

        db.delete_product(deleted_product_id)
        trash = db.get_product_trash()
        self.assertEqual({row["id"] for row in trash["products"]}, {deleted_product_id})

        db.delete_product_section(section_id)
        trash = db.get_product_trash()
        self.assertEqual({row["id"] for row in trash["sections"]}, {section_id})
        self.assertEqual(trash["sections"][0]["product_count"], 2)
        # Deleted products under the deleted section are grouped under the section card
        self.assertEqual(len(trash["products"]), 0)
        # Restoring section restores all its products at once
        db.restore_product_section(section_id)
        self.assertEqual(len(db.get_all_products(section_id=section_id)), 2)

    def test_section_template_fallback_and_product_creator_tracking(self):
        cashier_id = db.add_user(
            "creator.cashier@example.com",
            role="cashier",
            username="Creator Cashier",
        )
        section_id = db.add_product_section("Cashier Section")
        self.assertEqual(db.get_templates(section_id), [])

        template_id = db.ensure_product_template_for_section(section_id)
        self.assertEqual(db.ensure_product_template_for_section(section_id), template_id)
        templates = db.get_templates(section_id)
        self.assertEqual(len(templates), 1)
        self.assertEqual(templates[0]["id"], template_id)
        self.assertEqual(templates[0]["section_id"], section_id)

        product_id = db.add_product({
            "barcode": "CREATOR1",
            "name": "Creator Product",
            "section_id": section_id,
            "template_id": template_id,
            "created_by_user_id": cashier_id,
            "price": 1000,
            "cost": 500,
            "stock": 5,
            "unit": "dona",
        })
        product = db.get_product_by_barcode("CREATOR1")
        self.assertEqual(product["created_by_user_id"], cashier_id)
        self.assertEqual(product["created_by_name"], "Creator Cashier")

        sale_id = db.create_sale(
            None,
            cashier_id,
            [{"product_id": product_id, "quantity": 1, "price": 1000, "subtotal": 1000}],
            1000,
            0,
            1000,
            "naqd",
        )
        archive = db.get_product_sales_archive()
        self.assertEqual(archive[0]["sale_id"], sale_id)
        self.assertEqual(archive[0]["cashier_id"], cashier_id)

    def test_suppliers_debtors_and_expenses(self):
        supplier_id = db.add_supplier("Supplier", "1", "note", "USD")
        db.update_supplier(supplier_id, "Supplier 2", "2", "note2", "EUR")
        db.add_supplier_debt(supplier_id, 100, "debt")
        with self.assertRaises(db.AppError):
            db.pay_supplier_debt(supplier_id, 150, "too much")
        db.pay_supplier_debt(supplier_id, 40, "pay")
        self.assertEqual(db.get_all_suppliers()[0]["balance"], 60)
        self.assertEqual(len(db.get_supplier_debt_movements(supplier_id)), 2)

        debtor_id = db.add_debtor("Debtor", "1", "note", "USD")
        db.update_debtor(debtor_id, "Debtor 2", "2", "note2", "EUR")
        db.add_debtor_debt(debtor_id, 80, "debt")
        with self.assertRaises(db.AppError):
            db.pay_debtor_debt(debtor_id, 100, "too much")
        db.pay_debtor_debt(debtor_id, 30, "pay")
        self.assertEqual(db.get_all_debtors()[0]["balance"], 50)
        self.assertEqual(len(db.get_debtor_debt_movements(debtor_id)), 2)

        db.add_user("debt.cashier@example.com", role="cashier", username="Debt Cashier")
        cashier = next(user for user in db.get_users() if user["email"] == "debt.cashier@example.com")
        cashier_debtor_id = db.add_debtor("Debt Cashier", debt_currency="UZS", user_id=cashier["id"])
        cashier_debtor = next(row for row in db.get_all_debtors() if row["id"] == cashier_debtor_id)
        self.assertEqual(cashier_debtor["user_id"], cashier["id"])
        self.assertEqual(cashier_debtor["cashier_email"], "debt.cashier@example.com")
        with self.assertRaises(db.AppError):
            db.add_debtor("Duplicate", debt_currency="UZS", user_id=cashier["id"])

        category_id = db.add_expense_category("Office")
        db.update_expense_category(category_id, "Office 2")
        expense_id = db.add_expense(category_id, 25, "UZS", "paper")
        db.update_expense(expense_id, category_id, 30, "USD", "paper2")
        self.assertEqual(db.get_expenses()[0]["category_name"], "Office 2")
        self.assertEqual(db.get_expense_report("2000-01-01", "2999-01-01")[0]["amount"], 30)
        self.assertEqual(db.get_expense_category_report("2000-01-01", "2999-01-01")[0]["category_name"], "Office 2")
        db.delete_expense(expense_id)
        db.delete_expense_category(category_id)
        self.assertEqual(db.get_expenses(), [])

        db.delete_supplier(supplier_id)
        db.delete_debtor(debtor_id)
        db.delete_debtor(cashier_debtor_id)
        self.assertEqual(db.get_all_suppliers(), [])
        self.assertEqual(db.get_all_debtors(), [])


if __name__ == "__main__":
    unittest.main()
