"""Relational queries for the company / prompt tables.

These tables live in PostgreSQL regardless of the vector DB backend
selected via ``DB_BACKEND``.  A dedicated connection is used so the
vector-search singleton in ``db.py`` is not affected.

Usage:
    from services import get_companies, get_prompts

    companies = get_companies()
    prompts   = get_prompts(company_id=1)
"""

from typing import Any, Dict, List, Optional, Tuple

import psycopg2
from psycopg2.extras import RealDictCursor
from sanic.log import logger

from db import PG_HOST, PG_PORT, PG_USER, PG_PASSWORD, PG_DATABASE

_conn = None

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


def _get_conn():
    """Return a shared psycopg2 connection (lazy-initialised)."""
    global _conn
    if _conn is None or _conn.closed:
        logger.info("Opening relational PG connection for services")
        _conn = psycopg2.connect(
            host=PG_HOST,
            port=PG_PORT,
            user=PG_USER,
            password=PG_PASSWORD,
            dbname=PG_DATABASE,
        )
        _conn.autocommit = True
    return _conn


def get_companies() -> List[Dict[str, Any]]:
    """Return all companies ordered by name."""
    conn = _get_conn()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT id, name, description "
            "FROM company ORDER BY name"
        )
        return [dict(row) for row in cur.fetchall()]


def get_prompts(company_id: int) -> List[Dict[str, Any]]:
    """Return prompts for a given company, active ones first."""
    conn = _get_conn()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT id, company_id, name, template, is_active "
            "FROM prompt "
            "WHERE company_id = %s "
            "ORDER BY is_active DESC, created_at DESC",
            (company_id,),
        )
        return [dict(row) for row in cur.fetchall()]


def get_partner_filter_options() -> Dict[str, List[str]]:
    """Return the allowed values for each single-value partner filter.

    Each filter is backed by a dedicated lookup table in Postgres (see
    ``PARTNER_FILTER_LOOKUP_TABLES``).  Values are sorted alphabetically.
    Tables that do not exist yet (or are otherwise unreadable) return an
    empty list so the UI can still render the field.
    """
    conn = _get_conn()
    options: Dict[str, List[str]] = {}

    for filter_key, (table, column) in PARTNER_FILTER_LOOKUP_TABLES.items():
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {column} FROM {table} "
                    f"WHERE {column} IS NOT NULL AND {column} <> '' "
                    f"ORDER BY {column}"
                )
                options[filter_key] = [row[0] for row in cur.fetchall()]
        except Exception as exc:
            logger.warning(
                "get_partner_filter_options: skipping %s.%s (%s)",
                table, column, exc,
            )
            try:
                conn.rollback()
            except Exception:
                pass
            options[filter_key] = []

    return options


def update_prompt(prompt_id: int, template: str) -> Optional[Dict[str, Any]]:
    """Update the ``template`` field of a prompt.

    Returns:
        The updated prompt row as a dict, or ``None`` if no prompt with that
        ID exists.
    """
    conn = _get_conn()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "UPDATE prompt SET template = %s "
            "WHERE id = %s "
            "RETURNING id, company_id, name, template, is_active",
            (template, prompt_id),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def get_active_prompt(company_id: int, name: str) -> Optional[str]:
    """Return the template of the single active prompt for a company and name.

    Returns:
        The template string, or ``None`` if no active prompt is found.
    """
    conn = _get_conn()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT template FROM prompt "
            "WHERE company_id = %s AND name = %s AND is_active = TRUE "
            "LIMIT 1",
            (company_id, name),
        )
        row = cur.fetchone()
        return row["template"] if row else None
