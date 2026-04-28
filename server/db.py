"""Database connection layer — backend-agnostic.

Provides a unified interface for storing and querying data against either
a Qdrant vector database or a PostgreSQL relational database.  The concrete
backend is selected via the ``DB_BACKEND`` environment variable
(``"qdrant"`` | ``"postgres"``).

Filter format
-------------
The ``filters`` parameter accepted by :meth:`DBClient.query` supports two
forms:

**Simple (flat dict)** — backward-compatible exact-match on every key::

    {"country": "US", "seniority": "director"}

**Rich (structured dict)** — boolean composition with text matching::

    {
        "must": [
            {"key": "country", "match": "US"},
        ],
        "should": [
            {"key": "industry", "match_text": "mental health"},
            {"key": "title",    "match_text": "advocacy"},
        ],
        "must_not": [
            {"key": "industry", "match_text": "domestic violence"},
        ],
    }

Condition keys:
    * ``match``      — exact value equality.
    * ``match_text`` — full-text / substring search (backend-dependent).

Usage:
    from db import get_client

    client = get_client()
    client.connect()
    client.upsert("my_collection", records)
    results = client.query("my_collection", vector=embedding, top_k=5)
    client.disconnect()
"""

import os
import uuid
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from sanic.log import logger

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DB_BACKEND = os.environ.get("DB_BACKEND", "qdrant")

QDRANT_HOST = os.environ.get("QDRANT_HOST", "e4b7056d-bf13-4771-bf21-aac0a0c5563f.europe-west3-0.gcp.cloud.qdrant.io")
QDRANT_PORT = int(os.environ.get("QDRANT_PORT", "6333"))
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhY2Nlc3MiOiJtIn0.OrmvGmATwHO4l2lqULhOYkmHpeXWL3Rr0752l8Mhqcc")
QDRANT_GRPC = os.environ.get("QDRANT_GRPC", "false").lower() == "true"

PG_HOST = os.environ.get("PG_HOST", "34.41.70.34")
PG_PORT = int(os.environ.get("PG_PORT", "5432"))
PG_USER = os.environ.get("PG_USER", "postgres")
PG_PASSWORD = os.environ.get("PG_PASSWORD", "{KrGC|X:yc/q#FmR")
PG_DATABASE = os.environ.get("PG_DATABASE", "postgres")


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------
class DBClient(ABC):
    """Common contract that every backend must implement."""

    @abstractmethod
    def connect(self) -> None:
        """Establish a connection to the database."""

    @abstractmethod
    def disconnect(self) -> None:
        """Tear down the connection."""

    @abstractmethod
    def upsert(self, collection: str, records: List[Dict[str, Any]]) -> None:
        """Insert or update *records* in *collection*.

        Each record is a dict with at least:
            - ``payload``: dict of metadata fields.
        For vector backends a ``vector`` key (list[float]) is also expected.
        """

    @abstractmethod
    def query(
        self,
        collection: str,
        *,
        vector: Optional[List[float]] = None,
        filters: Optional[Dict[str, Any]] = None,
        top_k: int = 10,
    ) -> List[Dict[str, Any]]:
        """Return the *top_k* most relevant records from *collection*.

        Vector backends use *vector* for similarity search; relational
        backends fall back to *filters* for a WHERE-style lookup.
        """

    @abstractmethod
    def ensure_indexes(
        self,
        collection: str,
        fields: List[str],
        *,
        index_type: str = "text",
    ) -> None:
        """Create payload / column indexes required for filtered queries.

        This is idempotent — calling it for already-indexed fields is a no-op.

        Args:
            collection: Target collection or table.
            fields: Field names to index.
            index_type: Hint for the kind of index (``"text"`` or
                ``"keyword"``).  Backends may ignore this if it is not
                applicable.
        """

    @abstractmethod
    def delete(self, collection: str, ids: List[str]) -> None:
        """Remove records by id from *collection*."""

    @abstractmethod
    def list_values(
        self,
        collection: str,
        fields: List[str],
        *,
        limit_per_field: int = 500,
        scan_limit: int = 5000,
    ) -> Dict[str, List[Any]]:
        """Return the distinct values present in *collection* for each field.

        Args:
            collection: Target collection or table.
            fields: Field names to enumerate.
            limit_per_field: Maximum number of distinct values returned per
                field. Excess values are dropped.
            scan_limit: Maximum number of records to scan for vector
                backends that need to iterate over points. Ignored by
                relational backends.

        Returns:
            Mapping ``{field: sorted_distinct_values}``. Fields absent from
            the data return an empty list.
        """


# ---------------------------------------------------------------------------
# Qdrant implementation
# ---------------------------------------------------------------------------
class QdrantClient(DBClient):
    """Thin wrapper around the official ``qdrant-client`` package."""

    def __init__(self) -> None:
        self._client = None

    def connect(self) -> None:
        if self._client is not None:
            return

        from qdrant_client import QdrantClient as _QdrantClient

        logger.info(f"Connecting to Qdrant at {QDRANT_HOST}:{QDRANT_PORT}")
        self._client = _QdrantClient(
            host=QDRANT_HOST,
            port=QDRANT_PORT,
            api_key=QDRANT_API_KEY or None,
            prefer_grpc=QDRANT_GRPC,
        )
        logger.info("Qdrant connection established")

    def disconnect(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
            logger.info("Qdrant connection closed")

    def ensure_indexes(
        self,
        collection: str,
        fields: List[str],
        *,
        index_type: str = "text",
    ) -> None:
        from qdrant_client.models import PayloadSchemaType

        schema_map = {
            "text": PayloadSchemaType.TEXT,
            "keyword": PayloadSchemaType.KEYWORD,
        }
        schema = schema_map.get(index_type, PayloadSchemaType.TEXT)

        for field in fields:
            self._client.create_payload_index(
                collection_name=collection,
                field_name=field,
                field_schema=schema,
            )
            logger.info(
                "Ensured %s index on '%s' in collection '%s'",
                index_type, field, collection,
            )

    def upsert(self, collection: str, records: List[Dict[str, Any]]) -> None:
        from qdrant_client.models import Distance, PointStruct, VectorParams

        if not records:
            return

        if not self._client.collection_exists(collection):
            vector_size = len(records[0]["vector"])
            self._client.create_collection(
                collection_name=collection,
                vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
            )
            logger.info(f"Created Qdrant collection '{collection}' (vector size={vector_size})")

        points = [
            PointStruct(
                id=record.get("id", str(uuid.uuid4())),
                vector=record["vector"],
                payload=record.get("payload", {}),
            )
            for record in records
        ]
        self._client.upsert(collection_name=collection, points=points)
        logger.info(f"Upserted {len(points)} points into Qdrant collection '{collection}'")

    # -- Filter helpers --------------------------------------------------------

    @staticmethod
    def _build_condition(cond: Dict[str, Any]):
        """Translate a single condition dict into a Qdrant FieldCondition."""
        from qdrant_client.models import FieldCondition, MatchText, MatchValue

        key = cond["key"]
        if "match" in cond:
            return FieldCondition(key=key, match=MatchValue(value=cond["match"]))
        if "match_text" in cond:
            return FieldCondition(key=key, match=MatchText(text=cond["match_text"]))
        raise ValueError(f"Unsupported condition format: {cond}")

    def _build_filter(self, filters: Dict[str, Any]):
        """Translate a backend-agnostic filter dict into a Qdrant Filter.

        Supports both the simple flat format (``{key: value}``) and the rich
        structured format with ``must`` / ``should`` / ``must_not`` lists.
        """
        from qdrant_client.models import Filter, FieldCondition, MatchValue

        # Simple flat dict — treat every key-value as an exact-match must.
        if not any(k in filters for k in ("must", "should", "must_not")):
            conditions = [
                FieldCondition(key=k, match=MatchValue(value=v))
                for k, v in filters.items()
            ]
            return Filter(must=conditions)

        # Rich structured dict.
        must = []
        for cond in filters.get("must", []):
            # A nested should group: {"should": [...]}
            if "should" in cond:
                nested = [self._build_condition(c) for c in cond["should"]]
                must.append(Filter(should=nested))
            else:
                must.append(self._build_condition(cond))

        should = [self._build_condition(c) for c in filters.get("should", [])]
        must_not = [self._build_condition(c) for c in filters.get("must_not", [])]

        return Filter(
            must=must or None,
            should=should or None,
            must_not=must_not or None,
        )

    def query(
        self,
        collection: str,
        *,
        vector: Optional[List[float]] = None,
        filters: Optional[Dict[str, Any]] = None,
        top_k: int = 10,
    ) -> List[Dict[str, Any]]:
        if vector is None:
            raise ValueError("Qdrant queries require a vector")

        query_filter = self._build_filter(filters) if filters else None

        results = self._client.query_points(
            collection_name=collection,
            query=vector,
            query_filter=query_filter,
            limit=top_k,
        )
        return [
            {"id": hit.id, "score": hit.score, "payload": hit.payload}
            for hit in results.points
        ]

    def delete(self, collection: str, ids: List[str]) -> None:
        from qdrant_client.models import PointIdsList

        self._client.delete(
            collection_name=collection,
            points_selector=PointIdsList(points=ids),
        )
        logger.info(f"Deleted {len(ids)} points from Qdrant collection '{collection}'")

    def list_values(
        self,
        collection: str,
        fields: List[str],
        *,
        limit_per_field: int = 500,
        scan_limit: int = 5000,
    ) -> Dict[str, List[Any]]:
        if not self._client.collection_exists(collection):
            logger.warning("Collection '%s' does not exist; returning empty values", collection)
            return {field: [] for field in fields}

        seen: Dict[str, set] = {field: set() for field in fields}
        scanned = 0
        next_offset = None
        page_size = 256

        while scanned < scan_limit:
            batch_size = min(page_size, scan_limit - scanned)
            points, next_offset = self._client.scroll(
                collection_name=collection,
                with_payload=fields,
                with_vectors=False,
                limit=batch_size,
                offset=next_offset,
            )
            if not points:
                break

            for point in points:
                payload = point.payload or {}
                for field in fields:
                    value = payload.get(field)
                    if value is None or value == "":
                        continue
                    if isinstance(value, list):
                        for item in value:
                            if item not in (None, ""):
                                seen[field].add(item)
                    else:
                        seen[field].add(value)

            scanned += len(points)
            if next_offset is None:
                break

        return {
            field: sorted(list(values))[:limit_per_field]
            for field, values in seen.items()
        }


# ---------------------------------------------------------------------------
# PostgreSQL implementation
# ---------------------------------------------------------------------------
class PostgresClient(DBClient):
    """Backend powered by ``psycopg2``."""

    def __init__(self) -> None:
        self._conn = None

    def connect(self) -> None:
        if self._conn is not None:
            return

        import psycopg2

        logger.info(f"Connecting to PostgreSQL at {PG_HOST}:{PG_PORT}/{PG_DATABASE}")
        self._conn = psycopg2.connect(
            host=PG_HOST,
            port=PG_PORT,
            user=PG_USER,
            password=PG_PASSWORD,
            dbname=PG_DATABASE,
        )
        self._conn.autocommit = True
        logger.info("PostgreSQL connection established")

    def disconnect(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
            logger.info("PostgreSQL connection closed")

    def ensure_indexes(
        self,
        collection: str,
        fields: List[str],
        *,
        index_type: str = "text",
    ) -> None:
        with self._conn.cursor() as cur:
            for field in fields:
                idx_name = f"idx_{collection}_{field}"
                if index_type == "text":
                    cur.execute(
                        f"CREATE INDEX IF NOT EXISTS {idx_name} "
                        f"ON {collection} USING gin (to_tsvector('english', {field}))"
                    )
                else:
                    cur.execute(
                        f"CREATE INDEX IF NOT EXISTS {idx_name} "
                        f"ON {collection} ({field})"
                    )
                logger.info(
                    "Ensured %s index on '%s' in table '%s'",
                    index_type, field, collection,
                )

    def upsert(self, collection: str, records: List[Dict[str, Any]]) -> None:
        if not records:
            return

        payload_keys = list(records[0].get("payload", {}).keys())
        columns = ["id"] + payload_keys
        placeholders = ", ".join(["%s"] * len(columns))
        update_clause = ", ".join(f"{col} = EXCLUDED.{col}" for col in payload_keys)

        sql = (
            f"INSERT INTO {collection} ({', '.join(columns)}) "
            f"VALUES ({placeholders}) "
            f"ON CONFLICT (id) DO UPDATE SET {update_clause}"
        )

        with self._conn.cursor() as cur:
            for record in records:
                row_id = record.get("id", str(uuid.uuid4()))
                values = [row_id] + [record.get("payload", {}).get(k) for k in payload_keys]
                cur.execute(sql, values)

        logger.info(f"Upserted {len(records)} rows into PostgreSQL table '{collection}'")

    # -- Filter helpers --------------------------------------------------------

    @staticmethod
    def _build_clause(cond: Dict[str, Any]) -> tuple:
        """Return a (sql_fragment, param) pair for a single condition dict."""
        key = cond["key"]
        if "match" in cond:
            return f"{key} = %s", cond["match"]
        if "match_text" in cond:
            return f"{key} ILIKE %s", f"%{cond['match_text']}%"
        raise ValueError(f"Unsupported condition format: {cond}")

    def _build_where(self, filters: Dict[str, Any]) -> tuple:
        """Translate a backend-agnostic filter dict into a SQL WHERE clause.

        Returns ``(where_sql, params)``.
        """
        parts: List[str] = []
        params: List[Any] = []

        # Simple flat dict — backward compat.
        if not any(k in filters for k in ("must", "should", "must_not")):
            for key, value in filters.items():
                parts.append(f"{key} = %s")
                params.append(value)
            where = " AND ".join(parts)
            return (f" WHERE {where}" if where else ""), params

        # Rich structured dict.
        and_clauses: List[str] = []

        for cond in filters.get("must", []):
            if "should" in cond:
                or_parts = []
                for c in cond["should"]:
                    sql, param = self._build_clause(c)
                    or_parts.append(sql)
                    params.append(param)
                and_clauses.append(f"({' OR '.join(or_parts)})")
            else:
                sql, param = self._build_clause(cond)
                and_clauses.append(sql)
                params.append(param)

        if filters.get("should"):
            or_parts = []
            for c in filters["should"]:
                sql, param = self._build_clause(c)
                or_parts.append(sql)
                params.append(param)
            and_clauses.append(f"({' OR '.join(or_parts)})")

        for cond in filters.get("must_not", []):
            sql, param = self._build_clause(cond)
            and_clauses.append(f"NOT ({sql})")
            params.append(param)

        where = " AND ".join(and_clauses)
        return (f" WHERE {where}" if where else ""), params

    def query(
        self,
        collection: str,
        *,
        vector: Optional[List[float]] = None,
        filters: Optional[Dict[str, Any]] = None,
        top_k: int = 10,
    ) -> List[Dict[str, Any]]:
        if filters:
            where_clause, params = self._build_where(filters)
        else:
            where_clause, params = "", []

        sql = f"SELECT * FROM {collection}{where_clause} LIMIT %s"
        params.append(top_k)

        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            col_names = [desc[0] for desc in cur.description]
            return [dict(zip(col_names, row)) for row in cur.fetchall()]

    def delete(self, collection: str, ids: List[str]) -> None:
        if not ids:
            return
        placeholders = ", ".join(["%s"] * len(ids))
        sql = f"DELETE FROM {collection} WHERE id IN ({placeholders})"

        with self._conn.cursor() as cur:
            cur.execute(sql, ids)

        logger.info(f"Deleted {len(ids)} rows from PostgreSQL table '{collection}'")

    def list_values(
        self,
        collection: str,
        fields: List[str],
        *,
        limit_per_field: int = 500,
        scan_limit: int = 5000,  # unused for SQL backend
    ) -> Dict[str, List[Any]]:
        result: Dict[str, List[Any]] = {field: [] for field in fields}

        with self._conn.cursor() as cur:
            for field in fields:
                try:
                    cur.execute(
                        f"SELECT DISTINCT {field} FROM {collection} "
                        f"WHERE {field} IS NOT NULL AND {field} <> '' "
                        f"ORDER BY {field} LIMIT %s",
                        (limit_per_field,),
                    )
                    result[field] = [row[0] for row in cur.fetchall()]
                except Exception as exc:
                    # Column may not exist yet — fall back to empty list.
                    logger.warning(
                        "list_values: skipping '%s.%s' (%s)",
                        collection, field, exc,
                    )
                    self._conn.rollback()
                    result[field] = []

        return result


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
_BACKENDS: Dict[str, type] = {
    "qdrant": QdrantClient,
    "postgres": PostgresClient,
}

_instance: Optional[DBClient] = None


def get_client() -> DBClient:
    """Return a lazily-initialised database client for the configured backend."""
    global _instance
    if _instance is None:
        backend = DB_BACKEND.lower()
        cls = _BACKENDS.get(backend)
        if cls is None:
            raise ValueError(
                f"Unknown DB_BACKEND '{backend}'. Choose from: {', '.join(_BACKENDS)}"
            )
        logger.info(f"Initialising database backend: {backend}")
        _instance = cls()
    return _instance
