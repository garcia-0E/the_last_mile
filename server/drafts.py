"""Email draft generation powered by Vertex AI (Gemini).

Accepts leads (as returned by ``/suggester``) and a campaign context string,
then generates a personalised outreach email for each lead.

Usage:
    from drafts import generate_drafts

    results = await generate_drafts(leads, context="...")
"""

import os
from typing import Any, Dict, List

from sanic.log import logger

PROJECT_NAME = os.environ.get("GOOGLE_CLOUD_PROJECT", "vast-formula-478020-a1")
LOCATION = os.environ.get("VERTEX_LOCATION", "us-central1")
MODEL_NAME = os.environ.get("VERTEX_MODEL", "gemini-2.5-flash")

_model = None


def _get_model():
    """Lazily initialise and return the Vertex AI GenerativeModel."""
    global _model
    if _model is None:
        import vertexai
        from vertexai.generative_models import GenerativeModel

        vertexai.init(project=PROJECT_NAME, location=LOCATION)
        logger.info(f"Initialising Vertex AI model: {MODEL_NAME}")
        _model = GenerativeModel(MODEL_NAME)
    return _model


def _build_prompt(
    lead: Dict[str, Any],
    context: str,
    company_id: int,
    prompt_name: str,
) -> str:
    """Build the generation prompt for a single lead.

    Fetches the active prompt template from the ``prompt`` table for the given
    *company_id* / *prompt_name* pair, then interpolates the following named
    placeholders with values extracted from *lead*:

        {first_name}, {last_name}, {company}, {job_title}, {category}, {context}

    Args:
        lead: A lead dict — either a full suggester result (with ``payload``
              key) or a flat payload dict.
        context: Free-text campaign context provided by the caller.
        company_id: ID of the company whose prompt template to use.
        prompt_name: The ``name`` column value of the desired prompt row.

    Returns:
        The complete prompt string with all placeholders filled in.

    Raises:
        ValueError: If no active prompt is found for the given company / name.
    """
    from services import get_active_prompt

    template = get_active_prompt(company_id, prompt_name)
    if template is None:
        raise ValueError(
            f"No active prompt found for company_id={company_id}, name={prompt_name!r}"
        )

    payload = lead.get("payload", lead)

    return template.format_map({
        "first_name": payload.get("first_name", ""),
        "last_name":  payload.get("last_name", ""),
        "company":    payload.get("company_name", ""),
        "job_title":  payload.get("title", ""),
        "category":   payload.get("industry", ""),
        "context":    context,
    })


async def generate_drafts(
    leads: List[Dict[str, Any]],
    context: str,
    company_id: int,
    prompt_name: str,
) -> List[Dict[str, Any]]:
    """Generate email drafts for a list of leads using Vertex AI.

    Args:
        leads: List of lead dicts (as returned by ``/suggester``).
        context: Campaign purpose / context for the outreach emails.
        company_id: ID of the company whose prompt template to use.
        prompt_name: The ``name`` of the active prompt row to retrieve.

    Returns:
        List of dicts, each containing the original ``lead`` payload and
        the generated ``draft`` text (or ``None`` on failure).
    """
    model = _get_model()
    drafts: List[Dict[str, Any]] = []

    for lead in leads:
        payload = lead.get("payload", lead)
        prompt = _build_prompt(lead, context, company_id, prompt_name)

        try:
            response = await model.generate_content_async(prompt)
            draft_text = response.text
        except Exception as exc:

            logger.error(
                "Failed to generate draft for %s: %s",
                payload.get("email", "unknown"),
                exc,
            )
            draft_text = None

        drafts.append({
            "lead": payload,
            "draft": draft_text,
        })

    logger.info(f"Generated {len(drafts)} email drafts")
    return drafts
