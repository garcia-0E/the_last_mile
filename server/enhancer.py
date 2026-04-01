import io
import json
import os

import pandas as pd
from google.cloud import bigquery, pubsub_v1
from sanic.log import logger

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


def publish_file_message(file: bytes) -> str:
    """Publish a message to Pub/Sub when the 'file' property exists.

    Args:
        file: The request file body.

    Returns:
        The published message ID.
    """
    publisher = _get_publisher()
    topic_path = publisher.topic_path(PROJECT_NAME, PUBSUB_TOPIC)

    message_data = ("File received with size: " + str(len(file)) + " bytes").encode("utf-8")
    future = publisher.publish(topic_path, data=message_data)
    message_id = future.result()

    logger.info(f"Published message {message_id} to {topic_path}")
    return message_id


def normalize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize a raw Apollo contacts DataFrame.

    Applies column name normalization, text cleaning, email validation,
    URL formatting, numeric coercion, boolean mapping, deduplication,
    and appends a processing timestamp.

    New columns are automatically handled as text fields unless they are
    registered in one of the special-type sets below.

    Args:
        df: Raw DataFrame read from the source CSV.

    Returns:
        Cleaned and normalized DataFrame.
    """
    logger.info(f"Starting normalization on {len(df)} rows with {len(df.columns)} columns")

    # Normalize column names: lowercase, # -> num, non-alphanumeric -> _
    df.columns = (
        df.columns.str.strip()
        .str.lower()
        .str.replace('#', 'num', regex=False)
        .str.replace(r'[^a-z0-9]+', '_', regex=True)
        .str.strip('_')
    )

    # -- Column type definitions ------------------------------------------------
    # Only columns requiring *special* processing are listed here.
    # Every other string/object column receives generic text normalization
    # automatically, so new CSV columns won't require code changes.
    NUMERIC_COLUMNS = {'num_employees'}
    BOOLEAN_COLUMNS = {'replied'}
    URL_COLUMNS = {'website'}
    EMAIL_COLUMNS = {'email'}
    special_cols = NUMERIC_COLUMNS | BOOLEAN_COLUMNS | URL_COLUMNS | EMAIL_COLUMNS

    # -- Generic text normalization (all object columns not handled specially) ---
    text_cols = [
        col for col in df.columns
        if col not in special_cols and df[col].dtype == 'object'
    ]
    for col in text_cols:
        df[col] = df[col].astype(str).str.strip()
        df[col] = df[col].replace(['', 'nan', 'None'], None)

    # -- Email normalization & validation ---------------------------------------
    for col in EMAIL_COLUMNS & set(df.columns):
        df[col] = df[col].astype(str).str.strip().str.lower()
        df[col] = df[col].replace(['', 'nan', 'None'], None)
        email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        invalid = ~df[col].str.match(email_pattern, na=False)
        if invalid.any():
            logger.warning(f"Found {invalid.sum()} rows with invalid email format in '{col}'")

    # -- URL normalization ------------------------------------------------------
    for col in URL_COLUMNS & set(df.columns):
        df[col] = df[col].astype(str).str.strip()
        df[col] = df[col].replace(['', 'nan', 'None'], None)
        mask = df[col].notna() & ~df[col].str.match(r'^https?://', na=False)
        df.loc[mask, col] = 'https://' + df.loc[mask, col]

    # -- Numeric normalization --------------------------------------------------
    for col in NUMERIC_COLUMNS & set(df.columns):
        df[col] = pd.to_numeric(df[col], errors='coerce').astype('Int64')

    # -- Boolean normalization --------------------------------------------------
    for col in BOOLEAN_COLUMNS & set(df.columns):
        df[col] = (
            df[col].astype(str).str.lower()
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