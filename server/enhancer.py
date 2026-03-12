import io
import json
import logging
import os

import pandas as pd
from google.cloud import bigquery, pubsub_v1

logger = logging.getLogger(__name__)

PROJECT_NAME = os.environ.get("GOOGLE_CLOUD_PROJECT", "vast-formula-478020-a1")
PUBSUB_TOPIC = os.environ.get("PUBSUB_TOPIC", "file-processing-topic")
DATASET_NAME = "tfm_dataset_apollo_contacts"
TABLE_NAME = "apollo_contacts_normalized"

_publisher = None


def _get_publisher() -> pubsub_v1.PublisherClient:
    """Lazily initialise and return a shared PublisherClient."""
    global _publisher
    if _publisher is None:
        _publisher = pubsub_v1.PublisherClient()
    return _publisher


def publish_file_message(payload: dict) -> str:
    """Publish a message to Pub/Sub when the payload contains a 'file' property.

    Args:
        payload: The request payload dict. Must contain a 'file' key.

    Returns:
        The published message ID.
    """
    publisher = _get_publisher()
    topic_path = publisher.topic_path(PROJECT_NAME, PUBSUB_TOPIC)

    message_data = json.dumps({"file": payload["file"]}).encode("utf-8")
    future = publisher.publish(topic_path, data=message_data)
    message_id = future.result()

    logger.info(f"Published message {message_id} to {topic_path}")
    return message_id


def normalize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize a raw Apollo contacts DataFrame.

    Applies column name normalization, text cleaning, email validation,
    URL formatting, numeric coercion, boolean mapping, deduplication,
    and appends a processing timestamp.

    Args:
        df: Raw DataFrame read from the source CSV.

    Returns:
        Cleaned and normalized DataFrame.
    """
    logger.info(f"Starting normalization on {len(df)} rows with {len(df.columns)} columns")

    # Normalize column names: lowercase, replace spaces with underscores
    df.columns = (
        df.columns.str.strip()
        .str.lower()
        .str.replace(' ', '_')
        .str.replace('#', 'num')
    )

    # Normalize text fields: strip whitespace, convert empty strings to None
    text_columns = [
        'first_name', 'last_name', 'title', 'company_name', 'email',
        'email_status', 'seniority', 'mobile_phone', 'stage', 'industry',
        'city', 'state', 'country', 'company_city', 'company_state',
    ]
    for col in text_columns:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()
            df[col] = df[col].replace(['', 'nan', 'None'], None)

    # Normalize email addresses: lowercase + basic format validation
    if 'email' in df.columns:
        df['email'] = df['email'].str.lower()
        email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        invalid_emails = ~df['email'].str.match(email_pattern, na=False)
        if invalid_emails.any():
            logger.warning(f"Found {invalid_emails.sum()} rows with invalid email format")

    # Normalize URLs: strip, ensure https:// prefix, remove bare 'nan'
    if 'website' in df.columns:
        df['website'] = df['website'].astype(str).str.strip()
        df['website'] = df['website'].replace(['', 'nan', 'None'], None)
        mask = df['website'].notna() & ~df['website'].str.match(r'^https?://', na=False)
        df.loc[mask, 'website'] = 'https://' + df.loc[mask, 'website']

    # Normalize employee count: coerce to nullable integer
    if 'num_employees' in df.columns:
        df['num_employees'] = pd.to_numeric(df['num_employees'], errors='coerce').astype('Int64')

    # Normalize boolean field
    if 'replied' in df.columns:
        df['replied'] = (
            df['replied'].astype(str).str.lower()
            .map({'true': True, 'false': False, '1': True, '0': False})
            .astype('boolean')
        )

    # Add processing timestamp
    df['processed_at'] = pd.Timestamp.now(tz='UTC')

    # Deduplicate on email
    if 'email' in df.columns:
        original_count = len(df)
        df = df.drop_duplicates(subset=['email'], keep='first')
        removed = original_count - len(df)
        if removed:
            logger.info(f"Removed {removed} duplicate rows")

    logger.info(f"Normalization complete. Final dataset: {len(df)} rows")
    return df


def load_to_bigquery(df: pd.DataFrame) -> str:
    """Load a normalized DataFrame to BigQuery (truncate-and-replace).

    Args:
        df: Normalized DataFrame to load.

    Returns:
        Fully-qualified BigQuery table ID that was written to.
    """
    client = bigquery.Client(project=PROJECT_NAME)
    table_id = f"{PROJECT_NAME}.{DATASET_NAME}.{TABLE_NAME}"

    job_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        create_disposition=bigquery.CreateDisposition.CREATE_IF_NEEDED,
        autodetect=True,
    )

    logger.info(f"Loading {len(df)} rows to {table_id}")
    load_job = client.load_table_from_dataframe(df, table_id, job_config=job_config)
    load_job.result()  # block until complete

    table = client.get_table(table_id)
    logger.info(f"Successfully loaded {table.num_rows} rows to {table_id}")
    return table_id