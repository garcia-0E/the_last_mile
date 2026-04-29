"""Relational queries for the company / prompt tables.

These tables live in PostgreSQL regardless of the vector DB backend
selected via ``DB_BACKEND``.  They share the same ``asyncpg`` connection
pool used by the vector-search singleton in :mod:`db` to avoid opening a
second connection to the same database.

Usage:
    from services import init_pool, close_pool, get_companies, get_prompts

    await init_pool()
    companies = await get_companies()
    prompts   = await get_prompts(company_id=1)
    await close_pool()
"""

from typing import Any, Dict, List, Optional, Tuple

from sanic.log import logger

from db import _pg_config

# Mapping from the filter key exposed by the API/UI to the
# (table, column) pair holding its allowed values in Postgres.
# Only fields that are NOT persisted on the contact records in the vector
# store live here. Filters whose values can be derived from the contact
# payload (country, seniority, title, industry, company_name) are sourced
# from Qdrant instead.
PARTNER_FILTER_LOOKUP_TABLES: Dict[str, Tuple[str, str]] = {
    "partnership_types": ("partnership_type",  "name"),
    "partnership_offer": ("partnership_offer", "name"),
    "stage":             ("stage",             "name"),
}


# ---------------------------------------------------------------------------
# Pool management
# ---------------------------------------------------------------------------
# This module supports two modes:
#   1. Owned pool — call ``init_pool()`` at startup; closed by ``close_pool()``.
#   2. Shared pool — call ``set_pool(pool)`` with the pool from a
#      :class:`db.PostgresClient` instance to reuse the same connections.
_pool = None  # type: Optional[Any]


def set_pool(pool) -> None:
    """Reuse an externally-managed asyncpg pool (preferred for the server)."""
    global _pool
    _pool = pool


async def init_pool() -> None:
    """Open a dedicated asyncpg pool for relational queries.

    Use this when no shared pool is available (e.g. scripts or tests).

    Any failure (missing env var, network error, auth rejection, ...) is
    logged and re-raised so callers can decide whether to abort startup or
    retry. ``_pool`` is left as ``None`` on failure so a subsequent call to
    :func:`init_pool` can try again.
    """
    global _pool
    if _pool is not None:
        return

    import asyncpg

    try:
        cfg = _pg_config()
        logger.info(
            "Opening relational PG pool for services (host=%s)", cfg["host"],
        )
        _pool = await asyncpg.create_pool(
            min_size=1,
            max_size=5,
            **cfg,
        )
    except Exception as exc:
        # Make sure a half-initialised pool can never leak into module state.
        _pool = None
        logger.exception("Failed to initialise relational PG pool: %s", exc)
        raise


async def close_pool() -> None:
    """Close the pool opened by :func:`init_pool`. No-op for shared pools."""
    global _pool
    if _pool is None:
        return
    await _pool.close()
    _pool = None


def _get_pool():
    if _pool is None:
        raise RuntimeError(
            "services pool is not initialised — call init_pool() or set_pool()"
        )
    return _pool


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

async def get_companies() -> List[Dict[str, Any]]:
    """Return all companies ordered by name."""
    pool = _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, name, description "
            "FROM company ORDER BY name"
        )
        return [dict(row) for row in rows]


async def get_prompts(company_id: int) -> List[Dict[str, Any]]:
    """Return prompts for a given company, active ones first."""
    pool = _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, company_id, name, template, is_active "
            "FROM prompt "
            "WHERE company_id = $1 "
            "ORDER BY is_active DESC, created_at DESC",
            company_id,
        )
        return [dict(row) for row in rows]


async def get_partner_filter_options() -> Dict[str, List[str]]:
    """Return the allowed values for each single-value partner filter.

    Each filter is backed by a dedicated lookup table in Postgres (see
    ``PARTNER_FILTER_LOOKUP_TABLES``).  Values are sorted alphabetically.
    Tables that do not exist yet (or are otherwise unreadable) return an
    empty list so the UI can still render the field.
    """
    pool = _get_pool()
    options: Dict[str, List[str]] = {}

    async with pool.acquire() as conn:
        for filter_key, (table, column) in PARTNER_FILTER_LOOKUP_TABLES.items():
            try:
                rows = await conn.fetch(
                    f"SELECT {column} FROM {table} "
                    f"WHERE {column} IS NOT NULL AND {column} <> '' "
                    f"ORDER BY {column}"
                )
                options[filter_key] = [row[0] for row in rows]
            except Exception as exc:
                logger.warning(
                    "get_partner_filter_options: skipping %s.%s (%s)",
                    table, column, exc,
                )
                options[filter_key] = []

    return options


async def update_prompt(prompt_id: int, template: str) -> Optional[Dict[str, Any]]:
    """Update the ``template`` field of a prompt.

    Returns:
        The updated prompt row as a dict, or ``None`` if no prompt with that
        ID exists.
    """
    pool = _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE prompt SET template = $1 "
            "WHERE id = $2 "
            "RETURNING id, company_id, name, template, is_active",
            template, prompt_id,
        )
        return dict(row) if row else None


async def get_active_prompt(company_id: int, name: str) -> Optional[str]:
    """Return the template of the single active prompt for a company and name.

    Returns:
        The template string, or ``None`` if no active prompt is found.
    """
    pool = _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT template FROM prompt "
            "WHERE company_id = $1 AND name = $2 AND is_active = TRUE "
            "LIMIT 1",
            company_id, name,
        )
        return row["template"] if row else None
