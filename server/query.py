"""Partner-discovery query builder.

Translates the ``analysis_prompt`` criteria defined in
``dags/knowledge_base_pipeline.py`` (lines 228-260) into backend-agnostic
filters consumed by the DB layer in ``server/db.py``.

Payload fields come from the normalized Apollo contacts
(``server/enhancer.py``) and are projected into the DB by
``server/embedder.py`` (_PAYLOAD_FIELDS):
    first_name, last_name, email, title, company_name,
    industry, city, state, country, seniority, website

**Pre-requisite:** text indexes on ``industry`` and ``title`` must exist.
Call ``ensure_partner_indexes()`` once before issuing queries.
"""

from typing import Any, Dict, List, Optional

from db import DBClient

COLLECTION_NAME = "tfm_leads"

# ---------------------------------------------------------------------------
# Criteria derived from the analysis_prompt
# ---------------------------------------------------------------------------

# Strategic Fit — exclusions (analysis_prompt criterion 3)
# Contacts whose *industry* matches any of these are filtered out.
# Mission alignment (criterion 1) and activation potential (criterion 2) are
# handled by vector similarity — the query embedding already captures those
# themes, so a hard keyword filter would only distort the candidate pool.
EXCLUSION_INDUSTRY_KEYWORDS: List[str] = [
    "domestic violence",
    "feminism",
    "local justice",
]

# Composite text used to generate the query embedding.  Covers all six
# mission themes plus activation-related terms so the vector similarity
# captures semantic relevance.
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

def ensure_partner_indexes(client: DBClient) -> None:
    """Create the text indexes needed by the partner-search filters.

    Delegates to :meth:`DBClient.ensure_indexes` so the right backend-
    specific index type is created regardless of the active backend.
    """
    client.ensure_indexes(
        COLLECTION_NAME,
        ["industry", "title"],
        index_type="text",
    )


# ---------------------------------------------------------------------------
# Filter builder
# ---------------------------------------------------------------------------

def build_partner_filter(
    *,
    country: Optional[str] = None,
    seniority: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Build a backend-agnostic filter dict for the analysis_prompt criteria.

    Mission alignment and activation potential are handled entirely by vector
    similarity (the query embedding already encodes those themes).  Filters
    are used only for:

        * ``must_not`` → exclude off-theme industries (strategic fit).
        * Optional exact-match ``must`` on ``country`` / ``seniority``.

    Returns:
        A plain dict compatible with the *rich filter format* from ``db.py``,
        or ``None`` when no filters are needed.
    """
    must_clauses: List[Dict[str, Any]] = []

    if country:
        must_clauses.append({"key": "country", "match": country})
    if seniority:
        must_clauses.append({"key": "seniority", "match": seniority})

    exclusion_conditions = [
        {"key": "industry", "match_text": kw}
        for kw in EXCLUSION_INDUSTRY_KEYWORDS
    ]

    # Return None when there is nothing to filter — lets the vector search
    # consider every point in the collection.
    if not must_clauses and not exclusion_conditions:
        return None

    result: Dict[str, Any] = {}
    if must_clauses:
        result["must"] = must_clauses
    if exclusion_conditions:
        result["must_not"] = exclusion_conditions
    return result


# ---------------------------------------------------------------------------
# Search entry point
# ---------------------------------------------------------------------------

def search_partners(
    client: DBClient,
    query_vector: List[float],
    *,
    country: Optional[str] = None,
    seniority: Optional[str] = None,
    top_k: int = 20,
) -> List[Dict[str, Any]]:
    """Search for contacts matching the campaign partner criteria.

    Combines vector similarity with payload filters derived from the
    ``analysis_prompt``.

    Args:
        client: A connected :class:`DBClient` instance.
        query_vector: Embedding of ``MISSION_SEARCH_TEXT`` (or a custom
            query) produced with the same model used during ingestion
            (``all-MiniLM-L6-v2``).
        country: Optional country exact-match filter.
        seniority: Optional seniority exact-match filter.
        top_k: Maximum number of results.

    Returns:
        List of result dicts (format depends on backend).
    """
    filters = build_partner_filter(country=country, seniority=seniority)

    return client.query(
        COLLECTION_NAME,
        vector=query_vector,
        filters=filters,
        top_k=top_k,
    )


# ---------------------------------------------------------------------------
# Convenience: embed + search in one call
# ---------------------------------------------------------------------------

def discover_partners(
    client: DBClient,
    *,
    query_text: Optional[str] = None,
    country: Optional[str] = None,
    seniority: Optional[str] = None,
    top_k: int = 20,
) -> List[Dict[str, Any]]:
    """High-level helper: embed the query text and search.

    Uses the same ``SentenceTransformer`` model as the ingestion pipeline
    (``embedder.py``) so vectors are comparable.

    Args:
        client: Connected :class:`DBClient`.
        query_text: Custom search text; defaults to ``MISSION_SEARCH_TEXT``.
        country: Optional country filter.
        seniority: Optional seniority filter.
        top_k: Number of results.

    Returns:
        Ranked list of partner candidates.
    """
    from embedder import _get_model

    text = query_text or MISSION_SEARCH_TEXT
    model = _get_model()
    vector = model.encode(text).tolist()

    return search_partners(
        client,
        query_vector=vector,
        country=country,
        seniority=seniority,
        top_k=top_k,
    )
