import io

import pandas as pd
from sanic import Sanic
from sanic.response import text, json as json_response
from sanic.request import Request
from enhancer import normalize_dataframe, load_to_bigquery, publish_file_message
from embedder import embed
from db import get_client
from query import discover_partners, ensure_partner_indexes


app = Sanic("TheLastMileAPI")


@app.before_server_start
async def startup(_app, _loop):
    """Connect to the DB and create indexes once at startup."""
    client = get_client()
    client.connect()
    ensure_partner_indexes(client)


@app.post("/health")
async def health_check(request: Request):
    file = request.files.get("file") if request.files else None
    # Process the request body as needed
    if file:
        df = pd.read_csv(io.BytesIO(file.body))
        n_df = normalize_dataframe(df)
        e_df = embed(n_df)
        # publish_file_message(embed(n_df))
        get_client().upsert("tfm_leads", e_df)
        return text("File received and processed successfully!")
    return text("Hello World from the Built image!")


@app.post("/suggester")
async def tfm_suggester(request: Request):
    response = discover_partners(
            client=get_client(),
            query_text=request.json["query"],
            country=request.json.get("country"),
            seniority=request.json.get("seniority"),
            top_k=int(request.json.get("top_k", 20)),
        )
    return json_response(response)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True, auto_reload=True)
