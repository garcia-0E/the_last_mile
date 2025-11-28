from datetime import timedelta
from airflow.decorators import dag

import dlt
from dlt.common import pendulum
from dlt.helpers.airflow_helper import PipelineTasksGroup


# modify the default task arguments - all the tasks created for dlt pipeline will inherit it
# - set e-mail notifications
# - we set retries to 0 and recommend to use `PipelineTasksGroup` retry policies with tenacity library, you can also retry just extract and load steps
# - execution_timeout is set to 20 hours, tasks running longer that that will be terminated

default_task_args = {
    'owner': 'airflow',
    'depends_on_past': False,
    'email': 'test@test.com',
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 0,
    'execution_timeout': timedelta(hours=20),
}

# modify the default DAG arguments
# - the schedule below sets the pipeline to `@daily` be run each day after midnight, you can use crontab expression instead
# - start_date - a date from which to generate backfill runs
# - catchup is False which means that the daily runs from `start_date` will not be run, set to True to enable backfill
# - max_active_runs - how many dag runs to perform in parallel. you should always start with 1


@dag(
    schedule_interval='@daily',
    start_date=pendulum.datetime(2023, 7, 1),
    catchup=False,
    max_active_runs=1,
    default_args=default_task_args
)
def load_google_sheets_data(url_or_id: str):
    # set `use_data_folder` to True to store temporary data on the `data` bucket. Use only when it does not fit on the local storage
    tasks = PipelineTasksGroup("pipeline_decomposed", use_data_folder=False, wipe_local_data=True)

    # import your source from pipeline script
    from google_sheets_pipeline import load_pipeline_with_sheets

    # modify the pipeline parameters
    pipeline = dlt.pipeline(pipeline_name='google_sheets_pipeline',
                     dataset_name='dev_dataset',
                     destination='bigquery',
                     full_refresh=False
                     )
    # create the source, the "serialize" decompose option will converts dlt resources into Airflow tasks. use "none" to disable it
    tasks.add_run(pipeline, load_pipeline_with_sheets(url_or_id), decompose="serialize", trigger_rule="all_done", retries=0, provide_context=True)


load_google_sheets_data(url_or_id = "1RbQyiK471ha9UAfH7HlSAcr_ZBDYX79f3snj5m4t6f0")
