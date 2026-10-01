"""Every migration must import and compile before it reaches a server.

Migration 0014 shipped with ``sa.dialects.postgresql.ARRAY`` in a column
definition. Importing ``sqlalchemy as sa`` does not pull in that submodule, so
the call raised AttributeError - but only when alembic actually ran it, which
happens inside the api container at start-up. Nothing in the test suite touched
the file, the deploy went out, the container crash-looped, ``account_rules`` was
never created, and the health check rolled the whole release back.

These tests run every migration's ``upgrade()`` against a recording stand-in for
``op``. No database is involved: what is being checked is that the module
imports, that the Python in it executes, and that the SQL it emits is the SQL the
rest of the code expects to find.
"""

import importlib.util
import pathlib
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch


VERSIONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "alembic" / "versions"
API_DIR = pathlib.Path(__file__).resolve().parent.parent


def _load(path):
    spec = importlib.util.spec_from_file_location(f"migration_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Running a migration in *this* process is not enough to prove it works on a
# server, and that is exactly how 0014 got through: 0001 does
# ``from sqlalchemy.dialects import postgresql``, which registers the submodule
# globally, so every migration loaded afterwards finds ``sa.dialects.postgresql``
# already there. A server upgrading from 0013 to 0014 loads only 0014, and the
# attribute does not exist. Each migration therefore gets its own interpreter.
_ISOLATED_RUNNER = """
import importlib.util, sys
from unittest.mock import MagicMock, patch

path, direction = sys.argv[1], sys.argv[2]
spec = importlib.util.spec_from_file_location("migration_under_test", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

fake_op = MagicMock()
with patch.object(module, "op", fake_op):
    getattr(module, direction)()
"""


def _run_isolated(path, direction="upgrade"):
    """Execute one migration in a fresh interpreter, as a real upgrade does.

    Returns (ok, output) rather than raising, so the subTest reports which
    migration failed and with what.
    """
    result = subprocess.run(
        [sys.executable, "-c", _ISOLATED_RUNNER, str(path), direction],
        capture_output=True,
        text=True,
        cwd=str(API_DIR),
    )
    return result.returncode == 0, (result.stderr or result.stdout)


def _run(module, direction="upgrade"):
    """Execute one migration against a fake op, returning what it emitted."""
    emitted = {"execute": [], "create_table": [], "create_index": [], "drop": []}

    fake_op = MagicMock()
    fake_op.execute.side_effect = lambda stmt, *a, **kw: emitted["execute"].append(str(stmt))
    fake_op.create_table.side_effect = lambda name, *cols, **kw: emitted["create_table"].append(name)
    fake_op.create_index.side_effect = lambda name, *a, **kw: emitted["create_index"].append(name)
    fake_op.drop_table.side_effect = lambda name, *a, **kw: emitted["drop"].append(name)
    fake_op.drop_index.side_effect = lambda name, *a, **kw: emitted["drop"].append(name)

    with patch.object(module, "op", fake_op):
        getattr(module, direction)()
    return emitted


class EveryMigrationRunsTest(unittest.TestCase):
    """The check that would have caught the 0014 failure before it deployed."""

    def test_every_migration_imports_and_upgrades(self):
        """Each one alone, in its own interpreter - see _run_isolated."""
        paths = sorted(p for p in VERSIONS_DIR.glob("0*.py"))
        self.assertGreater(len(paths), 10, "migrations directory looks wrong")

        for path in paths:
            with self.subTest(migration=path.name):
                ok, output = _run_isolated(path, "upgrade")
                self.assertTrue(ok, f"{path.name} upgrade() failed:\n{output}")

    def test_every_migration_downgrades(self):
        for path in sorted(VERSIONS_DIR.glob("0*.py")):
            with self.subTest(migration=path.name):
                ok, output = _run_isolated(path, "downgrade")
                self.assertTrue(ok, f"{path.name} downgrade() failed:\n{output}")

    def test_the_revision_chain_is_unbroken(self):
        """A missing link leaves the server on an older schema without saying so."""
        modules = [_load(p) for p in sorted(VERSIONS_DIR.glob("0*.py"))]
        revisions = {m.revision for m in modules}

        for module in modules:
            parent = module.down_revision
            if parent is None:
                continue
            self.assertIn(
                parent,
                revisions,
                f"{module.revision} builds on {parent}, which no migration defines",
            )

        heads = revisions - {m.down_revision for m in modules if m.down_revision}
        self.assertEqual(len(heads), 1, f"expected one head, found {sorted(heads)}")


class AccountRulesMigrationTest(unittest.TestCase):
    """0014 specifically: the vector column and the indexes search depends on."""

    def setUp(self):
        self.module = _load(VERSIONS_DIR / "0014_account_rules_vector.py")
        self.emitted = _run(self.module, "upgrade")
        self.sql = " ".join(" ".join(s.split()) for s in self.emitted["execute"])

    def test_the_vector_column_is_added_not_retyped(self):
        """The shape that broke the first deploy.

        0014 first created the column as ARRAY and then ran
        ``ALTER COLUMN embedding TYPE vector(384) USING NULL``. Postgres rejects
        that: the USING expression has to be derived from the column, and a bare
        NULL is not. Alembic stopped there, account_rules was never created, and
        every rules_service call afterwards raised UndefinedTable until the
        health check rolled the release back.

        Adding the column with its real type has no such step.
        """
        self.assertIn("ADD COLUMN embedding", self.sql)
        self.assertNotIn("USING NULL", self.sql)
        self.assertNotIn("ALTER COLUMN embedding TYPE", self.sql)

    def test_the_extension_is_created_before_the_column_needs_it(self):
        statements = [" ".join(s.split()) for s in self.emitted["execute"]]
        extension = next(i for i, s in enumerate(statements) if "CREATE EXTENSION" in s)
        column = next(i for i, s in enumerate(statements) if "ADD COLUMN embedding" in s)
        self.assertLess(extension, column)

    def test_the_table_is_created(self):
        self.assertIn("account_rules", self.emitted["create_table"])

    def test_the_account_filter_has_its_own_index(self):
        """Without it, filtered similarity search returns fewer rows than asked."""
        self.assertIn("ix_account_rules_user_uid", self.emitted["create_index"])

    def test_the_vector_index_is_created(self):
        self.assertIn("hnsw", self.sql)
        self.assertIn("vector_cosine_ops", self.sql)


class DropRuleEmbeddingsMigrationTest(unittest.TestCase):
    """0015: the embedding columns go, the rules themselves stay."""

    def setUp(self):
        self.module = _load(VERSIONS_DIR / "0015_drop_rule_embeddings.py")
        emitted = _run(self.module, "upgrade")
        self.sql = " ".join(" ".join(s.split()) for s in emitted["execute"])

    def test_the_embedding_columns_and_index_are_dropped(self):
        for column in ("embedding", "model", "text_hash"):
            self.assertIn(f"DROP COLUMN IF EXISTS {column}", self.sql)
        self.assertIn("DROP INDEX IF EXISTS ix_account_rules_embedding_hnsw", self.sql)

    def test_the_rules_themselves_are_not_touched(self):
        self.assertNotIn("raw_text", self.sql)
        self.assertNotIn("priority", self.sql)
        self.assertNotIn("DROP TABLE", self.sql)

    def test_the_extension_is_left_installed(self):
        self.assertNotIn("DROP EXTENSION", self.sql)


if __name__ == "__main__":
    unittest.main()
