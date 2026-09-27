"""Text-search SQL shapes shared by index DDL and the queries that must hit it.

A PostgreSQL expression index is only selectable when the query repeats the
indexed expression verbatim, so the DDL (``migrations.py`` and the Alembic
versions) and the read arms (``engine/sql/postgresql.py``) cannot be allowed to
drift. Both sides call the helpers here — same idea as
``_pg_search.pg_search_bm25_columns``.
"""


def mental_models_text_document(alias: str | None = None) -> str:
    """The ``mental_models`` full-text document: model/page name + content.

    Mirrors the generating expression of the native tsvector column created by
    the ``n9i0j1k2l3m4`` (learnings / pinned_reflections) migration, so every
    backend indexes and queries the exact same document. ``content`` is NOT NULL,
    hence the deliberate lack of a ``COALESCE`` around it.

    ``alias`` qualifies the columns for queries that join the table (``mm``);
    leave it unset for DDL, where the expression is already table-scoped.
    """
    prefix = f"{alias}." if alias else ""
    return f"(COALESCE({prefix}name, '') || ' ' || {prefix}content)"


def memory_units_text_document() -> str:
    """The ``memory_units`` full-text document for backends that index base columns."""
    return "(COALESCE(text, '') || ' ' || COALESCE(context, '') || ' ' || COALESCE(text_signals, ''))"


def pg_bigm_document(document: str) -> str:
    """The expression a ``gin_bigm_ops`` index covers: the document lowercased.

    pg_bigm's ``LIKE`` is case-sensitive and it has no ``ILIKE`` index support,
    so the index and every query fold case on both sides (``lower`` leaves CJK
    untouched).
    """
    return f"lower({document})"
