"""Database connection layer — backend-agnostic.

Provides a unified interface for storing and querying data against either
a Qdrant vector database or a PostgreSQL relational database.  The concrete
backend is selected via the ``DB_BACKEND`` environment variable
(``"qdrant"`` | ``"postgres"``).

Environment variables (Postgres backend)
----------------------------------------
Production (Cloud Run + Cloud SQL Auth Proxy):
    INSTANCE_CONNECTION_NAME  ``project:region:instance`` — flips the
                              connection into Unix-socket mode and reads
                              from ``<DB_SOCKET_DIR>/<value>`` (default
                              ``/cloudsql``).
    PG_USER, PG_PASSWORD, PG_DATABASE
                              Application credentials (``PG_PASSWORD`` is
                              expected to come from Secret Manager).

Local development (Cloud SQL Auth Proxy on 127.0.0.1, or a local Postgres):
    PG_HOST, PG_PORT          TCP target, e.g. ``127.0.0.1:5432``.
    PG_USER, PG_PASSWORD, PG_DATABASE
                              As above.

If ``INSTANCE_CONNECTION_NAME`` is set it always wins; otherwise the TCP
branch is used.

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
    await client.connect()
    await client.upsert("my_collection", records)
    results = await client.query("my_collection", vector=embedding, top_k=5)
    await client.disconnect()
"""

import asyncio
import os
import uuid
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from sanic.log import logger

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DB_BACKEND = os.environ.get("DB_BACKEND", "qdrant")


def _require_env(name: str) -> str:
    """Return the value of ``name`` from the environment or raise.

    Used for credentials and connection-target variables that must never have
    a hard-coded fallback in source.
    """
    value = os.environ.get(name)
    if value is None or value == "":
        raise RuntimeError(
            f"Required environment variable '{name}' is not set"
        )
    return value


# ---------------------------------------------------------------------------
# Lazy config accessors
# ---------------------------------------------------------------------------
# These are looked up on demand so that a process which only uses one backend
# does not need to set the other backend's variables.

def _qdrant_config() -> Dict[str, Any]:
    return {
        "host": os.environ.get("QDRANT_HOST", "e4b7056d-bf13-4771-bf21-aac0a0c5563f.europe-west3-0.gcp.cloud.qdrant.io"),
        "port": int(os.environ.get("QDRANT_PORT", "6333")),
        "api_key": os.environ.get("QDRANT_API_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhY2Nlc3MiOiJtIn0.OrmvGmATwHO4l2lqULhOYkmHpeXWL3Rr0752l8Mhqcc"),
        "prefer_grpc": os.environ.get("QDRANT_GRPC", "false").lower() == "true",
    }


def _pg_config() -> Dict[str, Any]:
    """Build asyncpg connection kwargs for the configured Postgres target.

    Two connection modes are supported:

    * **Cloud SQL via Unix socket** (preferred on Cloud Run / GAE / GKE
      with the Cloud SQL Auth Proxy sidecar).  Selected automatically when
      ``INSTANCE_CONNECTION_NAME`` is set, e.g.
      ``my-project:us-central1:my-instance``.  The host is set to the
      socket directory ``<DB_SOCKET_DIR>/<INSTANCE_CONNECTION_NAME>``
      (defaults to ``/cloudsql/...``); ``asyncpg`` switches to Unix-socket
      mode when the host starts with ``/``.  The port is still required
      because ``asyncpg`` uses it to locate the socket file
      ``<host>/.s.PGSQL.<port>`` (Cloud SQL Postgres always listens on
      5432).

    * **TCP** — used for local development and any environment where
      ``INSTANCE_CONNECTION_NAME`` is not present.  Requires ``PG_HOST``
      and ``PG_PORT``.

    ``PG_USER``, ``PG_PASSWORD`` and ``PG_DATABASE`` are required in both
    modes; ``PG_PASSWORD`` and (in TCP mode) ``PG_HOST`` have no defaults
    so misconfiguration fails loudly instead of silently targeting a
    stale public IP.
    """
    cfg: Dict[str, Any] = {
        "user": os.environ.get("PG_USER", "postgres"),
        "password": _require_env("PG_PASSWORD"),
        "database": os.environ.get("PG_DATABASE", "postgres"),
        "port": int(os.environ.get("PG_PORT", "5432")),
    }

    instance = os.environ.get("INSTANCE_CONNECTION_NAME")
    if instance:
        socket_dir = os.environ.get("DB_SOCKET_DIR", "/cloudsql")
        # asyncpg treats a host starting with '/' as a Unix-socket dir and
        # looks for '<host>/.s.PGSQL.<port>'.
        cfg["host"] = f"{socket_dir}/{instance}"
    else:
        cfg["host"] = _require_env("PG_HOST")

    return cfg


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------
class DBClient(ABC):
    """Common contract that every backend must implement."""

    @abstractmethod
    async def connect(self) -> None:
        """Establish a connection to the database."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Tear down the connection."""

    @abstractmethod
    async def upsert(self, collection: str, records: List[Dict[str, Any]]) -> None:
        """Insert or update *records* in *collection*.

        Each record is a dict with at least:
            - ``payload``: dict of metadata fields.
        For vector backends a ``vector`` key (list[float]) is also expected.
        """

    @abstractmethod
    async def query(
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
    async def ensure_indexes(
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
    async def delete(self, collection: str, ids: List[str]) -> None:
        """Remove records by id from *collection*."""

    @abstractmethod
    async def list_values(
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
    """Thin wrapper around the official ``qdrant-client`` package.

    The underlying client is synchronous; every method offloads its blocking
    calls with :func:`asyncio.to_thread` so the surrounding event loop stays
    responsive.
    """

    def __init__(self) -> None:
        self._client = None

    async def connect(self) -> None:
        if self._client is not None:
            return

        from qdrant_client import QdrantClient as _QdrantClient

        cfg = _qdrant_config()
        logger.info(f"Connecting to Qdrant at {cfg['host']}:{cfg['port']}")
        self._client = await asyncio.to_thread(
            _QdrantClient,
            host=cfg["host"],
            port=cfg["port"],
            api_key=cfg["api_key"] or None,
            prefer_grpc=cfg["prefer_grpc"],
        )
        logger.info("Qdrant connection established")

    async def disconnect(self) -> None:
        if self._client is not None:
            await asyncio.to_thread(self._client.close)
            self._client = None
            logger.info("Qdrant connection closed")

    async def ensure_indexes(
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
            await asyncio.to_thread(
                self._client.create_payload_index,
                collection_name=collection,
                field_name=field,
                field_schema=schema,
            )
            logger.info(
                "Ensured %s index on '%s' in collection '%s'",
                index_type, field, collection,
            )

    async def upsert(self, collection: str, records: List[Dict[str, Any]]) -> None:
        from qdrant_client.models import Distance, PointStruct, VectorParams

        if not records:
            return

        exists = await asyncio.to_thread(
            self._client.collection_exists, collection
        )
        if not exists:
            vector_size = len(records[0]["vector"])
            await asyncio.to_thread(
                self._client.create_collection,
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
        await asyncio.to_thread(
            self._client.upsert, collection_name=collection, points=points
        )
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

    async def query(
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

        results = await asyncio.to_thread(
            self._client.query_points,
            collection_name=collection,
            query=vector,
            query_filter=query_filter,
            limit=top_k,
        )
        return [
            {"id": hit.id, "score": hit.score, "payload": hit.payload}
            for hit in results.points
        ]

    async def delete(self, collection: str, ids: List[str]) -> None:
        from qdrant_client.models import PointIdsList

        await asyncio.to_thread(
            self._client.delete,
            collection_name=collection,
            points_selector=PointIdsList(points=ids),
        )
        logger.info(f"Deleted {len(ids)} points from Qdrant collection '{collection}'")

    async def list_values(
        self,
        collection: str,
        fields: List[str],
        *,
        limit_per_field: int = 500,
        scan_limit: int = 5000,
    ) -> Dict[str, List[Any]]:
        exists = await asyncio.to_thread(
            self._client.collection_exists, collection
        )
        if not exists:
            logger.warning("Collection '%s' does not exist; returning empty values", collection)
            return {field: [] for field in fields}

        seen: Dict[str, set] = {field: set() for field in fields}
        scanned = 0
        next_offset = None
        page_size = 256

        while scanned < scan_limit:
            batch_size = min(page_size, scan_limit - scanned)
            points, next_offset = await asyncio.to_thread(
                self._client.scroll,
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
    """Backend powered by ``asyncpg`` — exposes the relational store via an
    :class:`asyncpg.Pool` so multiple coroutines can share connections safely.
    """

    def __init__(self) -> None:
        self._pool = None  # type: Optional[Any]

    @property
    def pool(self):
        """Return the underlying ``asyncpg`` pool.

        Other modules (e.g. ``services``) can reuse this pool instead of
        opening their own connections to the same database.
        """
        if self._pool is None:
            raise RuntimeError("PostgresClient.connect() has not been called")
        return self._pool

    async def connect(self) -> None:
        if self._pool is not None:
            return

        import asyncpg

        cfg = _pg_config()
        # Unix-socket connections still carry a port (used to locate the
        # socket file) but it isn't part of a network address, so log it
        # differently from TCP for clarity.
        if cfg["host"].startswith("/"):
            target = f"{cfg['host']}/.s.PGSQL.{cfg['port']} ({cfg['database']})"
        else:
            target = f"{cfg['host']}:{cfg['port']}/{cfg['database']}"
        logger.info(f"Connecting to PostgreSQL at {target}")
        self._pool = await asyncpg.create_pool(
            min_size=1,
            max_size=10,
            **cfg,
        )
        logger.info("PostgreSQL connection pool established")

    async def disconnect(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            logger.info("PostgreSQL connection pool closed")

    async def ensure_indexes(
        self,
        collection: str,
        fields: List[str],
        *,
        index_type: str = "text",
    ) -> None:
        async with self._pool.acquire() as conn:
            for field in fields:
                idx_name = f"idx_{collection}_{field}"
                if index_type == "text":
                    await conn.execute(
                        f"CREATE INDEX IF NOT EXISTS {idx_name} "
                        f"ON {collection} USING gin (to_tsvector('english', {field}))"
                    )
                else:
                    await conn.execute(
                        f"CREATE INDEX IF NOT EXISTS {idx_name} "
                        f"ON {collection} ({field})"
                    )
                logger.info(
                    "Ensured %s index on '%s' in table '%s'",
                    index_type, field, collection,
                )

    async def upsert(self, collection: str, records: List[Dict[str, Any]]) -> None:
        if not records:
            return

        payload_keys = list(records[0].get("payload", {}).keys())
        columns = ["id"] + payload_keys
        placeholders = ", ".join(f"${i + 1}" for i in range(len(columns)))
        update_clause = ", ".join(f"{col} = EXCLUDED.{col}" for col in payload_keys)

        sql = (
            f"INSERT INTO {collection} ({', '.join(columns)}) "
            f"VALUES ({placeholders}) "
            f"ON CONFLICT (id) DO UPDATE SET {update_clause}"
        )

        rows = []
        for record in records:
            row_id = record.get("id", str(uuid.uuid4()))
            payload = record.get("payload", {})
            rows.append([row_id] + [payload.get(k) for k in payload_keys])

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.executemany(sql, rows)

        logger.info(f"Upserted {len(records)} rows into PostgreSQL table '{collection}'")

    # -- Filter helpers --------------------------------------------------------

    @staticmethod
    def _build_clause(cond: Dict[str, Any], next_idx: int) -> tuple:
        """Return ``(sql_fragment, param)`` for *cond* using ``$next_idx``."""
        key = cond["key"]
        if "match" in cond:
            return f"{key} = ${next_idx}", cond["match"]
        if "match_text" in cond:
            return f"{key} ILIKE ${next_idx}", f"%{cond['match_text']}%"
        raise ValueError(f"Unsupported condition format: {cond}")

    def _build_where(self, filters: Dict[str, Any]) -> tuple:
        """Translate a backend-agnostic filter dict into a SQL WHERE clause.

        Returns ``(where_sql, params)`` with positional ``$N`` placeholders.
        """
        parts: List[str] = []
        params: List[Any] = []

        # Simple flat dict — backward compat.
        if not any(k in filters for k in ("must", "should", "must_not")):
            for key, value in filters.items():
                params.append(value)
                parts.append(f"{key} = ${len(params)}")
            where = " AND ".join(parts)
            return (f" WHERE {where}" if where else ""), params

        # Rich structured dict.
        and_clauses: List[str] = []

        for cond in filters.get("must", []):
            if "should" in cond:
                or_parts = []
                for c in cond["should"]:
                    sql, param = self._build_clause(c, len(params) + 1)
                    or_parts.append(sql)
                    params.append(param)
                and_clauses.append(f"({' OR '.join(or_parts)})")
            else:
                sql, param = self._build_clause(cond, len(params) + 1)
                and_clauses.append(sql)
                params.append(param)

        if filters.get("should"):
            or_parts = []
            for c in filters["should"]:
                sql, param = self._build_clause(c, len(params) + 1)
                or_parts.append(sql)
                params.append(param)
            and_clauses.append(f"({' OR '.join(or_parts)})")

        for cond in filters.get("must_not", []):
            sql, param = self._build_clause(cond, len(params) + 1)
            and_clauses.append(f"NOT ({sql})")
            params.append(param)

        where = " AND ".join(and_clauses)
        return (f" WHERE {where}" if where else ""), params

    async def query(
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

        params.append(top_k)
        sql = f"SELECT * FROM {collection}{where_clause} LIMIT ${len(params)}"

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
            return [dict(row) for row in rows]

    async def delete(self, collection: str, ids: List[str]) -> None:
        if not ids:
            return
        placeholders = ", ".join(f"${i + 1}" for i in range(len(ids)))
        sql = f"DELETE FROM {collection} WHERE id IN ({placeholders})"

        async with self._pool.acquire() as conn:
            await conn.execute(sql, *ids)

        logger.info(f"Deleted {len(ids)} rows from PostgreSQL table '{collection}'")

    async def list_values(
        self,
        collection: str,
        fields: List[str],
        *,
        limit_per_field: int = 500,
        scan_limit: int = 5000,  # unused for SQL backend
    ) -> Dict[str, List[Any]]:
        result: Dict[str, List[Any]] = {field: [] for field in fields}

        async with self._pool.acquire() as conn:
            for field in fields:
                try:
                    rows = await conn.fetch(
                        f"SELECT DISTINCT {field} FROM {collection} "
                        f"WHERE {field} IS NOT NULL AND {field} <> '' "
                        f"ORDER BY {field} LIMIT $1",
                        limit_per_field,
                    )
                    result[field] = [row[0] for row in rows]
                except Exception as exc:
                    # Column may not exist yet — fall back to empty list.
                    logger.warning(
                        "list_values: skipping '%s.%s' (%s)",
                        collection, field, exc,
                    )
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
