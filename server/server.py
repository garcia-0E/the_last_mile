import io
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

import pandas as pd
from sanic import Sanic
from sanic.response import text, json as json_response
from sanic.request import Request
from sanic_ext import openapi
from sanic.worker.manager import WorkerManager
from enhancer import normalize_dataframe
from embedder import embed
from db import get_client
from query import discover_partners, ensure_partner_indexes


# --- OpenAPI request / response schemas ---------------------------------- #

@dataclass
class SuggesterRequest:
    """Body for the /suggester endpoint."""
    query: str
    country: Optional[str] = None
    seniority: Optional[str] = None
    top_k: int = 20

@dataclass
class EnhancerResponse:
    message: str
    data: List[Dict[str, Any]] = field(default_factory=list)


app = Sanic("TheLastMileAPI")
app.config.HEALTH = True
app.config.HEALTH_ENDPOINT = True
WorkerManager.THRESHOLD = 600

app.ext.openapi.describe(
    "The Last Mile API",
    version="1.0.0",
    description="Lead enrichment, embedding, and partner discovery.",
)


@app.before_server_start
async def startup(_app, _loop):
    """Connect to the DB and create indexes once at startup."""
    client = get_client()
    client.connect()
    ensure_partner_indexes(client)

@app.post("/enhancer")
@openapi.summary("Enhance & embed leads")
@openapi.description(
    "Upload a CSV file of leads. The file is normalised, embedded, "
    "and upserted into the vector store."
)
@openapi.body({"multipart/form-data": {"schema": {"type": "object", "properties": {"file": {"type": "string", "format": "binary"}}}}})
@openapi.response(200, {"application/json": EnhancerResponse}, description="File processed successfully")
@openapi.tag("leads")
async def tfm_enhancer(request: Request):
    file = request.files.get("file") if request.files else None
    # Process the request body as needed
    if file:
        df = pd.read_csv(io.BytesIO(file.body))
        n_df = normalize_dataframe(df)
        e_df = embed(n_df)
        # publish_file_message(e_df)
        get_client().upsert("tfm_leads", e_df)
        return json_response({"message": "File received and processed successfully!", "data": e_df.to_dict(orient="records")})
    return text("Hello World from the Built image!")


@app.post("/suggester")
@openapi.summary("Discover partners")
@openapi.description(
    "Semantic search over stored leads to find the best-matching partners."
)
@openapi.body({"application/json": SuggesterRequest})
@openapi.response(200, description="List of matching partners")
@openapi.tag("partners")
async def tfm_suggester(request: Request):
    response = discover_partners(
            client=get_client(),
            query_text=request.json.get("query"),
            country=request.json.get("country"),
            seniority=request.json.get("seniority"),
            top_k=int(request.json.get("top_k", 20)),
        )
    return json_response(response)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True, auto_reload=True)
