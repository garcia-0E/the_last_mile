import json
import os
from typing import List

import pandas as pd
from google.cloud import pubsub_v1
from sanic.log import logger

PROJECT_NAME = os.environ.get("GOOGLE_CLOUD_PROJECT", "vast-formula-478020-a1")
PUBSUB_EMBEDDING_TOPIC = os.environ.get("PUBSUB_EMBEDDING_TOPIC", "embedding-processing-topic")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "all-mpnet-base-v2")

_model = None
_publisher = None

# Fields used to build the text representation for each contact
_TEXT_FIELDS = [
    "first_name",
    "last_name",
    "title",
    "company_name",
    "sub_departments",
    "industry",
    "city",
    "state",
    "country",
    "seniority",
]

# Fields carried as metadata payload alongside each embedding vector
_PAYLOAD_FIELDS = [
    "first_name",
    "last_name",
    "email",
    "title",
    "company_name",
    "industry",
    "city",
    "state",
    "country",
    "seniority",
    "website",
]


def _get_model():
    """Lazily initialise and return the sentence-transformer model."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        logger.info(f"Loading embedding model: {EMBEDDING_MODEL}")
        _model = SentenceTransformer(EMBEDDING_MODEL)
    return _model


def _get_publisher() -> pubsub_v1.PublisherClient:
    """Lazily initialise and return a shared PublisherClient."""
    global _publisher
    if _publisher is None:
        _publisher = pubsub_v1.PublisherClient()
    return _publisher


def _build_text_representations(df: pd.DataFrame) -> List[str]:
    """Combine relevant contact fields into a descriptive string per row.

    Example output:
        "John Doe | VP of Sales | Acme Corp | Software | New York | US | Senior"
    """
    texts = []
    for _, row in df.iterrows():
        parts = []
        for field in _TEXT_FIELDS:
            value = row.get(field)
            if pd.notna(value) and str(value).strip():
                parts.append(str(value).strip())
        texts.append(" | ".join(parts) if parts else "")
    return texts


def _build_payloads(df: pd.DataFrame) -> List[dict]:
    """Extract metadata payload for each row to accompany the embedding."""
    payloads = []
    for _, row in df.iterrows():
        payload = {"already_contacted": False}
        for field in _PAYLOAD_FIELDS:
            value = row.get(field)
            if pd.notna(value):
                payload[field] = str(value).strip()
        payloads.append(payload)
    return payloads


def embed(df: pd.DataFrame) -> list:
    """Generate embeddings for a normalized DataFrame and publish to Pub/Sub.

    The message payload contains a JSON array of objects, each with:
        - ``vector``: the embedding as a list of floats
        - ``payload``: contact metadata dict

    Args:
        df: Normalized contacts DataFrame.

    Returns:
        The published Pub/Sub message ID.
    """
    if df.empty:
        logger.warning("Empty DataFrame — skipping embedding")
        return ""

    logger.info(f"Generating embeddings for {len(df)} contacts")
    texts = _build_text_representations(df)
    model = _get_model()
    vectors = model.encode(texts, show_progress_bar=False)

    payloads = _build_payloads(df)

    points = [
        {"vector": vector.tolist(), "payload": payload}
        for vector, payload in zip(vectors, payloads)
    ]

    # message_data = json.dumps(points).encode("utf-8")

    # publisher = _get_publisher()
    # topic_path = publisher.topic_path(PROJECT_NAME, PUBSUB_EMBEDDING_TOPIC)

    # logger.info(f"Publishing {len(points)} embedding points to {topic_path}")
    # future = publisher.publish(topic_path, data=message_data)
    # message_id = future.result()

    # logger.info(f"Published embedding message {message_id} to {topic_path}")
    return points
