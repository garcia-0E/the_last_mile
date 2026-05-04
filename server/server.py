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
from db import get_client, PostgresClient
from query import COLLECTION_NAME, discover_partners, ensure_partner_indexes, get_partner_filters
from drafts import generate_drafts
import services
from services import get_companies, get_prompts, update_prompt


# --- OpenAPI request / response schemas ---------------------------------- #

@dataclass
class SuggesterRequest:
    """Body for the /suggester endpoint."""
    description: Optional[str] = None
    themes: Optional[str] = None
    target_communities: Optional[str] = None
    country: Optional[str] = None
    partnership_types: Optional[str] = None
    partnership_offer: Optional[str] = None
    stage: Optional[str] = None
    seniority: Optional[str] = None
    title: Optional[str] = None
    exclude_industries: List[str] = field(default_factory=list)
    company_name_to_exclude: List[str] = field(default_factory=list)
    top_k: int = 20

@dataclass
class FiltersResponse:
    """Response shape of the /filters endpoint — distinct DB values per filter."""
    country: List[str] = field(default_factory=list)
    partnership_types: List[str] = field(default_factory=list)
    partnership_offer: List[str] = field(default_factory=list)
    stage: List[str] = field(default_factory=list)
    seniority: List[str] = field(default_factory=list)
    title: List[str] = field(default_factory=list)
    exclude_industries: List[str] = field(default_factory=list)
    company_name_to_exclude: List[str] = field(default_factory=list)

@dataclass
class EnhancerResponse:
    message: str
    data: List[Dict[str, Any]] = field(default_factory=list)

@dataclass
class MarkContactedRequest:
    """Body for the /leads/mark-contacted endpoint."""
    ids: List[str]

@dataclass
class DraftsRequest:
    """Body for the /drafts endpoint."""
    leads: List[Dict[str, Any]]
    context: str
    company_id: int
    prompt_name: str

@dataclass
class DraftsResponse:
    message: str
    drafts: List[Dict[str, Any]] = field(default_factory=list)


app = Sanic("TheLastMileAPI")
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
    await client.connect()
    await ensure_partner_indexes(client)

    # The relational ``services`` module needs its own asyncpg pool. If the
    # configured DB backend is already Postgres, reuse its pool; otherwise
    # open a dedicated one.
    if isinstance(client, PostgresClient):
        services.set_pool(client.pool)
    else:
        await services.init_pool()


@app.before_server_stop
async def shutdown(_app):
    """Close DB connections cleanly on shutdown."""
    client = get_client()
    await services.close_pool()
    await client.disconnect()

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
        await get_client().upsert(COLLECTION_NAME, e_df)
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
    body = request.json or {}
    response = await discover_partners(
            client=get_client(),
            description=body.get("description"),
            themes=body.get("themes"),
            target_communities=body.get("target_communities"),
            country=body.get("country"),
            partnership_types=body.get("partnership_types"),
            partnership_offer=body.get("partnership_offer"),
            stage=body.get("stage"),
            seniority=body.get("seniority"),
            title=body.get("title"),
            exclude_industries=body.get("exclude_industries"),
            company_name_to_exclude=body.get("company_name_to_exclude"),
            top_k=int(body.get("top_k", 20)),
        )
    return json_response(response)


@app.get("/filters")
@openapi.summary("Available filter values")
@openapi.description(
    "Return the distinct values stored in the contacts collection for each "
    "DB-backed partner filter. Used to populate the search form on the UI."
)
@openapi.response(200, {"application/json": FiltersResponse}, description="Filter options")
@openapi.tag("partners")
async def tfm_filters(request: Request):
    return json_response(await get_partner_filters(get_client()))


@app.post("/leads/mark-contacted")
@openapi.summary("Mark leads as already contacted")
@openapi.description(
    "Set the already_contacted flag to true for the given lead IDs "
    "in the vector store."
)
@openapi.body({"application/json": MarkContactedRequest})
@openapi.response(200, description="Leads marked as contacted")
@openapi.tag("leads")
async def tfm_mark_contacted(request: Request):
    body = request.json or {}
    ids = body.get("ids", [])
    if not ids:
        return json_response({"message": "No lead IDs provided"}, status=400)

    await get_client().update_payload(
        COLLECTION_NAME,
        ids=ids,
        payload={"already_contacted": True},
    )
    return json_response({"message": f"Marked {len(ids)} leads as already contacted"})


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
    company_id = request.json.get("company_id")
    prompt_name = request.json.get("prompt_name", "")

    if not leads:
        return json_response({"message": "No leads provided", "drafts": []}, status=400)
    if not context:
        return json_response({"message": "Campaign context is required", "drafts": []}, status=400)
    if not company_id:
        return json_response({"message": "company_id is required", "drafts": []}, status=400)
    if not prompt_name:
        return json_response({"message": "prompt_name is required", "drafts": []}, status=400)

    drafts = await generate_drafts(leads, context, company_id, prompt_name)
    return json_response({"message": f"Generated {len(drafts)} drafts", "drafts": drafts})


@app.get("/companies")
@openapi.summary("List companies")
@openapi.description("Return all registered companies.")
@openapi.response(200, description="List of companies")
@openapi.tag("companies")
async def tfm_companies(request: Request):
    companies = await get_companies()
    return json_response(companies)


@app.get("/prompts/<company_id:int>")
@openapi.summary("List prompts for a company")
@openapi.description("Return all prompts belonging to the given company.")
@openapi.response(200, description="List of prompts")
@openapi.tag("prompts")
async def tfm_prompts(request: Request, company_id: int):
    return json_response(await get_prompts(company_id))


@dataclass
class PromptUpdateRequest:
    """Body for the PUT /prompts/<prompt_id> endpoint."""
    template: str


@app.put("/prompts/<prompt_id:int>")
@openapi.summary("Update a prompt's template")
@openapi.description("Update the template text of an existing prompt.")
@openapi.body({"application/json": PromptUpdateRequest})
@openapi.response(200, description="Updated prompt")
@openapi.tag("prompts")
async def tfm_update_prompt(request: Request, prompt_id: int):
    body = request.json or {}
    template = body.get("template")
    if template is None:
        return json_response({"message": "template is required"}, status=400)

    updated = await update_prompt(prompt_id, template)
    if updated is None:
        return json_response({"message": f"Prompt {prompt_id} not found"}, status=404)
    return json_response(updated)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True, auto_reload=True)
