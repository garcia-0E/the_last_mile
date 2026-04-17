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
from drafts import generate_drafts


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

@dataclass
class DraftsRequest:
    """Body for the /drafts endpoint."""
    leads: List[Dict[str, Any]]
    context: str

@dataclass
class DraftsResponse:
    message: str
    drafts: List[Dict[str, Any]] = field(default_factory=list)


app = Sanic("TheLastMileAPI")
app.config.HEALTH = True
app.config.HEALTH_ENDPOINT = True
app.config.CORS_ORIGINS = "https://garcia-0e.github.io,http://localhost:5173,http://localhost:4173"

app.ext.openapi.describe(
    "The Last Mile API",
    version="1.0.0",
    description="Lead enrichment, embedding, and partner discovery.",
)


@app.before_server_start
async def startup(_app):
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
@openapi.body({"multipart/form-data": {"schema": {"type": "object", "properties": {"file": {"type": "object", "format": "binary"}}}}})
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
        return json_response({"message": "File received and processed successfully!"})
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


@app.post("/drafts")
@openapi.summary("Generate email drafts")
@openapi.description(
    "Generate personalised outreach email drafts for a list of leads "
    "using Vertex AI."
)
@openapi.body({"application/json": DraftsRequest})
@openapi.response(200, {"application/json": DraftsResponse}, description="Email drafts generated")
@openapi.tag("drafts")
async def tfm_drafts(request: Request):
    leads = request.json.get("leads", [])
    context = request.json.get("context", "")

    if not leads:
        return json_response({"message": "No leads provided", "drafts": []}, status=400)
    if not context:
        return json_response({"message": "Campaign context is required", "drafts": []}, status=400)

    drafts = await generate_drafts(leads, context)
    return json_response({"message": f"Generated {len(drafts)} drafts", "drafts": drafts})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True, auto_reload=True)
