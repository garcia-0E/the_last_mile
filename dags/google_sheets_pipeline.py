import dlt
from google_sheets import google_spreadsheet

def load_pipeline_with_sheets(spreadsheet_url_or_id: str) -> None:
    """
    Will load all the sheets in the spreadsheet, but it will not load any of the named ranges in the spreadsheet.
    """
    pipeline = dlt.pipeline(
        pipeline_name="google_sheets_pipeline",
        destination="bigquery",
        dev_mode=False,
        dataset_name="test_tlm_google_sheets",
    )
    data = google_spreadsheet(
        spreadsheet_url_or_id=spreadsheet_url_or_id,
        get_sheets=True,
        get_named_ranges=False
    )
    info = pipeline.run(data)
    print(info)

if __name__ == "__main__":
    url_or_id = "1RbQyiK471ha9UAfH7HlSAcr_ZBDYX79f3snj5m4t6f0"
    range_names = ["hidden_columns_merged_cells", "Blank Columns"]

    load_pipeline_with_sheets(url_or_id)