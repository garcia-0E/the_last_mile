"""Partner-discovery query builder.

Translates the structured search criteria received from ``/suggester`` into
backend-agnostic filters consumed by the DB layer in ``server/db.py``.

User-typed criteria (``description``, ``themes``, ``target_communities``)
are concatenated to build the query embedding. DB-backed criteria are used
as exact-match (or ``match_text`` for the exclude lists) payload filters.

**Pre-requisite:** payload indexes on the filterable fields must exist.
Call ``ensure_partner_indexes()`` once before issuing queries.
"""

from typing import Any, Dict, List, Optional

from db import DBClient

COLLECTION_NAME = "tfm_leads"

# ---------------------------------------------------------------------------
# Field groups
# ---------------------------------------------------------------------------

# Fields the UI renders as single-value DB-backed selects that translate
# into exact-match ``must`` filters on the contact payload.
SINGLE_VALUE_FILTERS: Dict[str, str] = {
    "country": "country",
    "partnership_types": "partnership_types",
    "partnership_offer": "partnership_offer",
    "stage": "stage",
}

# Fields the UI renders as single-value DB-backed selects whose chosen
# value is folded into the query embedding text (so vector similarity
# captures them) rather than applied as a payload filter. Values come
# from the Qdrant payload, same as the regular SINGLE_VALUE_FILTERS.
QUERY_TEXT_FILTERS: Dict[str, str] = {
    "seniority": "seniority",
    "title": "title",
}

# Fields the UI renders as multi-value exclusion selects.
# Mapped to the underlying payload field used for the ``match_text`` filter.
EXCLUDE_FILTERS: Dict[str, str] = {
    "exclude_industries": "industry",
    "company_name_to_exclude": "company_name",
}


# ---------------------------------------------------------------------------
# Defaults / fallbacks
# ---------------------------------------------------------------------------

# Default exclusion industries used when the caller does not pass any
# ``exclude_industries``. Mirrors the original hard-coded list.
DEFAULT_EXCLUDE_INDUSTRIES: List[str] = [
    "domestic violence",
    "feminism",
    "local justice",
]

# Composite text used to generate the query embedding when the caller does
# not provide any free-text input.  Covers the original mission themes plus
# activation-related terms so the vector similarity captures semantic
# relevance.
MISSION_SEARCH_TEXT: str = (
    "Mental health trauma recovery "
    "refugee displacement advocacy "
    "media literacy journalism ethics platform responsibility "
    "cultural memory narrative justice civic storytelling "
    "youth education empowerment "
    "digital safety post-conflict policy engagement "
    "documentary film impact screening activation campaign"
)


# ---------------------------------------------------------------------------
# Index helper
# ---------------------------------------------------------------------------

async def ensure_partner_indexes(client: DBClient) -> None:
    """Create the text indexes needed by the partner-search filters.

    Only fields that are part of the ingestion payload today are indexed.
    Future fields (``stage``, ``partnership_types``, ``partnership_offer``)
    can be added once the ingestion pipeline starts persisting them.
    """
    await client.ensure_indexes(
        COLLECTION_NAME,
        ["industry", "title", "company_name", "email", "country", "seniority"],
        index_type="text",
    )

    await client.ensure_indexes(
        COLLECTION_NAME,
        ["already_contacted"],
        index_type="bool"
    )


# ---------------------------------------------------------------------------
# Filter options endpoint helper
# ---------------------------------------------------------------------------

async def get_partner_filters(client: DBClient) -> Dict[str, List[Any]]:

    """Return the available values for each DB-backed partner filter.

    Sources are split by where each field lives:

    * Filters whose allowed values are *not* present on the contact records
      in the vector store come from dedicated lookup tables in Postgres
      (see ``services.PARTNER_FILTER_LOOKUP_TABLES``).
    * Every other filter — ``country``, ``seniority``, ``title`` and the
      two exclude lists — is derived from the distinct payload values
      stored in Qdrant.
    """
    from services import PARTNER_FILTER_LOOKUP_TABLES, get_partner_filter_options

    response: Dict[str, List[Any]] = {}

    # 1. Filters whose values live in Postgres only.
    response.update(await get_partner_filter_options())

    # 2. Filters whose values live on the contact payload in Qdrant.
    all_qdrant_keys = {
        **SINGLE_VALUE_FILTERS,
        **QUERY_TEXT_FILTERS,
        **EXCLUDE_FILTERS,
    }
    qdrant_keys = {
        ui_key: payload_field
        for ui_key, payload_field in all_qdrant_keys.items()
        if ui_key not in PARTNER_FILTER_LOOKUP_TABLES
    }
    underlying_fields = sorted(set(qdrant_keys.values()))
    raw = await client.list_values(COLLECTION_NAME, underlying_fields)
    for ui_key, payload_field in qdrant_keys.items():
        response[ui_key] = raw.get(payload_field, [])

    return response


# ---------------------------------------------------------------------------
# Embedding text builder
# ---------------------------------------------------------------------------

def _build_query_text(
    *,
    description: Optional[str],
    themes: Optional[str],
    target_communities: Optional[str],
    seniority: Optional[str],
    title: Optional[str],
) -> str:
    """Combine the free-text inputs and ``QUERY_TEXT_FILTERS`` selections
    into a single string used for the query embedding.

    Falls back to ``MISSION_SEARCH_TEXT`` when every input is empty.
    """
    parts = [
        (description or "").strip(),
        (themes or "").strip(),
        (target_communities or "").strip(),
        (seniority or "").strip(),
        (title or "").strip(),
    ]
    combined = " ".join(p for p in parts if p)
    return combined or MISSION_SEARCH_TEXT


# ---------------------------------------------------------------------------
# Filter builder
# ---------------------------------------------------------------------------

def build_partner_filter(
    *,
    country: Optional[str] = None,
    partnership_types: Optional[str] = None,
    partnership_offer: Optional[str] = None,
    stage: Optional[str] = None,
    exclude_industries: Optional[List[str]] = None,
    company_name_to_exclude: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    """Build a backend-agnostic filter dict for the structured criteria.

    * Single-value DB filters become exact-match ``must`` clauses.
    * ``exclude_industries`` (defaulting to ``DEFAULT_EXCLUDE_INDUSTRIES``
      when omitted) and ``company_name_to_exclude`` become ``match_text``
      ``must_not`` clauses.
    * ``seniority`` and ``title`` are *not* filters — they are folded into
      the query embedding text by ``_build_query_text``.

    Returns ``None`` when no filters are needed.
    """
    must_clauses: List[Dict[str, Any]] = []

    single_values = {
        "country": country,
        "partnership_types": partnership_types,
        "partnership_offer": partnership_offer,
        "stage": stage,
    }
    for ui_key, value in single_values.items():
        if value:
            payload_field = SINGLE_VALUE_FILTERS[ui_key]
            must_clauses.append({"key": payload_field, "match": value})

    # Exclusion lists. Use the supplied list if provided (even if empty no-op),
    # fall back to the default industries when None is passed.
    if exclude_industries is None:
        exclude_industries = DEFAULT_EXCLUDE_INDUSTRIES

    must_not_clauses: List[Dict[str, Any]] = [
        {"key": "already_contacted", "match": True},
    ]
    for kw in exclude_industries:
        if kw:
            must_not_clauses.append({
                "key": EXCLUDE_FILTERS["exclude_industries"],
                "match_text": kw,
            })
    for kw in company_name_to_exclude or []:
        if kw:
            must_not_clauses.append({
                "key": EXCLUDE_FILTERS["company_name_to_exclude"],
                "match_text": kw,
            })

    result: Dict[str, Any] = {}
    if must_clauses:
        result["must"] = must_clauses
    if must_not_clauses:
        result["must_not"] = must_not_clauses
    return result


# ---------------------------------------------------------------------------
# Search entry point
# ---------------------------------------------------------------------------

async def search_partners(
    client: DBClient,
    query_vector: List[float],
    *,
    country: Optional[str] = None,
    partnership_types: Optional[str] = None,
    partnership_offer: Optional[str] = None,
    stage: Optional[str] = None,
    exclude_industries: Optional[List[str]] = None,
    company_name_to_exclude: Optional[List[str]] = None,
    top_k: int = 20,
) -> List[Dict[str, Any]]:
    """Search for contacts matching the structured partner criteria."""
    filters = build_partner_filter(
        country=country,
        exclude_industries=exclude_industries,
        company_name_to_exclude=company_name_to_exclude,
    )

    return await client.query(
        COLLECTION_NAME,
        vector=query_vector,
        filters=filters,
        top_k=top_k,
    )


# ---------------------------------------------------------------------------
# Convenience: embed + search in one call
# ---------------------------------------------------------------------------

async def discover_partners(
    client: DBClient,
    *,
    description: Optional[str] = None,
    themes: Optional[str] = None,
    target_communities: Optional[str] = None,
    country: Optional[str] = None,
    partnership_types: Optional[str] = None,
    partnership_offer: Optional[str] = None,
    stage: Optional[str] = None,
    seniority: Optional[str] = None,
    title: Optional[str] = None,
    exclude_industries: Optional[List[str]] = None,
    company_name_to_exclude: Optional[List[str]] = None,
    top_k: int = 20,
) -> List[Dict[str, Any]]:
    """High-level helper: build the embedding and search.

    The free-text inputs are concatenated to produce the query embedding;
    the DB-backed criteria are passed straight through as filters.
    """
    from embedder import _get_model

    text = _build_query_text(
        description=description,
        themes=themes,
        target_communities=target_communities,
        seniority=seniority,
        title=title,
    )
    model = _get_model()
    vector = model.encode(text).tolist()

    return await search_partners(
        client,
        query_vector=vector,
        country=country,
        partnership_types=partnership_types,
        partnership_offer=partnership_offer,
        stage=stage,
        exclude_industries=exclude_industries,
        company_name_to_exclude=company_name_to_exclude,
        top_k=top_k,
    )
