from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator
import sys
import dlt
from pathlib import Path

# Add the parent directory to the Python path to import the pipeline module
sys.path.append(str(Path(__file__).parent.parent))


def load_pipeline_with_sheets(spreadsheet_url_or_id: str) -> None:
    """
    Will load all the sheets in the spreadsheet, but it will not load any of the named ranges in the spreadsheet.
    """
    pipeline = dlt.pipeline(
        pipeline_name="google_sheets_pipeline",
        destination="bigquery",
        dev_mode=True,
        dataset_name="test_tlm_google_sheets",
    )
    data = google_spreadsheet(
        spreadsheet_url_or_id=spreadsheet_url_or_id,
        get_sheets=True,
        get_named_ranges=False
    )
    info = pipeline.run(data)

def run_google_sheets_pipeline(**context):
    """
    Wrapper function to run the Google Sheets pipeline.
    The spreadsheet URL can be passed via DAG config or default value.
    """
    # Get spreadsheet_url from DAG run config, or use default
    spreadsheet_url = context['dag_run'].conf.get(
        'spreadsheet_url_or_id',
        '1RbQyiK471ha9UAfH7HlSAcr_ZBDYX79f3snj5m4t6f0'
    )

    load_pipeline_with_sheets(spreadsheet_url_or_id=spreadsheet_url)


default_args = {
    'owner': 'airflow',
    'depends_on_past': False,
    'start_date': datetime(2024, 1, 1),
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
}

with DAG(
    'google_sheets_pipeline',
    default_args=default_args,
    description='Load data from Google Sheets to BigQuery using dlt',
    schedule_interval=None,  # Manual trigger only
    catchup=False,
    tags=['google_sheets', 'bigquery', 'dlt'],
) as dag:

    load_sheets_task = PythonOperator(
        task_id='load_google_sheets',
        python_callable=run_google_sheets_pipeline,
        provide_context=True,
    )

    load_sheets_task
