"""The ``pg_bigm`` keyword backend: query-term expansion, arm SQL, and index DDL.

Asserts on generated SQL like ``test_knowledge_bm25_dispatch``; no live
extension is needed. The live behaviour (index use, CJK recall) is exercised
against a real Postgres with pg_bigm outside this suite.
"""

import pytest

from hindsight_api import migrations
from hindsight_api._text_search import memory_units_text_document, mental_models_text_document, pg_bigm_document
from hindsight_api.engine.sql.postgresql import PostgreSQLDialect, knowledge_bm25_arm, pg_bigm_query_terms

pytestmark = pytest.mark.memory_backend_incompatible


@pytest.mark.parametrize(
    ("query", "terms"),
    [
        # CJK runs longer than two chars become overlapping bigrams (OR'd), so a
        # question matches without occurring verbatim.
        ("數據庫備份", ["數據", "據庫", "庫備", "備份"]),
        # A two-char run is its own bigram; full-width punctuation separates runs.
        ("灰度，發佈", ["灰度", "發佈"]),
        # Identifiers keep inner punctuation and are lowercased (index is lower()).
        ("INC-4471 點樣處理?", ["inc-4471", "點樣", "樣處", "處理"]),
        ("ERR_CONN_RESET 係咩", ["err_conn_reset", "係咩"]),
        # Mixed-script token splits at the script boundary.
        ("redis快取", ["redis", "快取"]),
        # 1-char terms survive only when nothing longer exists.
        ("錯", ["錯"]),
        ("嘅 部署", ["部署"]),
        ("a b", ["a", "b"]),
        ("?!", []),
        # Repeated bigrams are searched once.
        ("備份備份", ["備份", "份備"]),
    ],
)
def test_query_terms(query, terms):
    assert pg_bigm_query_terms(query) == terms


def test_query_terms_cap():
    assert pg_bigm_query_terms("一二三四五六", max_query_terms=3) == ["一二", "二三", "三四"]


def test_prepare_bm25_text_joins_terms_with_spaces():
    d = PostgreSQLDialect()
    text = d.prepare_bm25_text(["x"], "數據庫 E1024", text_search_extension="pg_bigm", max_query_terms=16)
    assert text == "數據 據庫 e1024"


def test_memory_arm_repeats_the_indexed_expression_and_uses_like_any():
    arm = PostgreSQLDialect().build_bm25_arm(
        table="s.memory_units",
        cols="id",
        fact_type="world",
        bank_id_param="$2",
        limit_param="$3",
        text_param="$4",
        text_search_extension="pg_bigm",
        bm25_language="french",
    )
    doc = pg_bigm_document(memory_units_text_document())
    assert f"AND {doc} LIKE ANY (ARRAY(SELECT likequery(t) FROM unnest(string_to_array($4, ' '))" in arm
    assert f"bigm_similarity($4, {doc})" in arm
    assert "french" not in arm and "to_tsquery" not in arm


def test_knowledge_arm_repeats_the_indexed_expression():
    arm = knowledge_bm25_arm("pg_bigm", table_alias="mm", text_param="$3")
    doc = pg_bigm_document(mental_models_text_document("mm"))
    assert arm.match_filter.startswith(f"AND {doc} LIKE ANY (")
    assert arm.order_by.endswith(" DESC, mm.id")


class _Conn:
    """Fake connection: memory_units already on pg_bigm, mental_models on the migration-time tsvector."""

    def __init__(self, rows: int):
        self.rows = rows
        self.ddl: list[str] = []

    def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        if sql.startswith(("ALTER", "CREATE", "DROP")):
            self.ddl.append(sql)
        table = (params or {}).get("table_name")
        if "information_schema.tables" in sql:
            return _R(scalar=True)
        if "information_schema.columns" in sql:
            return _R(row=("USER-DEFINED", "text" if table == "memory_units" else "tsvector"))
        if "FROM pg_indexes" in sql:
            return _R(row=("gin", ""))
        if "SELECT COUNT(*)" in sql:
            return _R(scalar=self.rows)
        return _R()

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _R:
    def __init__(self, scalar=None, row=None):
        self._scalar, self._row = scalar, row

    def scalar(self):
        return self._scalar

    def fetchone(self):
        return self._row


def _reconcile(monkeypatch, conn):
    monkeypatch.setattr(migrations, "create_engine", lambda *a, **k: type("E", (), {"connect": lambda self: conn})())
    migrations.ensure_text_search_extension("postgresql://unused", text_search_extension="pg_bigm")


def test_populated_mental_models_converts_to_pg_bigm_without_backfill(monkeypatch):
    conn = _Conn(rows=5)
    _reconcile(monkeypatch, conn)
    assert conn.ddl == [
        "DROP INDEX IF EXISTS public.idx_mental_models_text_search",
        "ALTER TABLE public.mental_models DROP COLUMN IF EXISTS search_vector",
        "CREATE EXTENSION IF NOT EXISTS pg_bigm CASCADE",
        "ALTER TABLE public.mental_models ADD COLUMN IF NOT EXISTS search_vector TEXT",
        "CREATE INDEX IF NOT EXISTS idx_mental_models_text_search ON public.mental_models "
        f"USING gin ({pg_bigm_document(mental_models_text_document())} gin_bigm_ops)",
    ]
