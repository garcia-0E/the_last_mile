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


def _build_prompt(lead: Dict[str, Any], context: str) -> str:
    """Build the generation prompt for a single lead.

    Maps payload fields to the campaign template variables:
        first_name  → First_Name
        last_name   → Last_Name
        company_name → Company_Name
        title       → Job_Title
        industry    → Category

    Args:
        lead: A lead dict — either a full suggester result (with ``payload``
              key) or a flat payload dict.
        context: Free-text campaign context provided by the caller.

    Returns:
        The complete prompt string.
    """
    payload = lead.get("payload", lead)

    first_name = payload.get("first_name", "")
    last_name = payload.get("last_name", "")
    company = payload.get("company_name", "")
    job_title = payload.get("title", "")
    category = payload.get("industry", "")

    return (
        "Act as Alfredo La Corte, Impact Producer for 'The Longer You Bleed'. "
        f"Write a peer-to-peer outreach email to {first_name} {last_name} at {company}.\n\n"
        "Lead variables:\n"
        f"- First Name: {first_name}\n"
        f"- Last Name: {last_name}\n"
        f"- Company: {company}\n"
        f"- Job Title: {job_title}\n"
        f"- Category: {category}\n\n"
        f"Additional campaign context: {context}\n\n"
        "Guidelines:\n"
        "1. Subject line: 'Collaborating on digital resilience for 2026'\n"
        f"2. Opening: Lead directly with their role as {job_title}. State that given their "
        f"work at {company}, they are uniquely positioned to discuss the intersection of "
        "technical/programmatic policy and human safety.\n"
        "3. The Mission: Briefly explain 'The Longer You Bleed' documentary and the Digital "
        "Resilience Toolkit (built with JED Foundation and Tactical Tech).\n"
        "4. The Ask: Ask if they empathize with the problem of digital safety for vulnerable "
        "populations. Offer a private film link and a 15-minute call.\n"
        "5. The Referral: Add a closing line: \"If you aren't the direct lead for partnerships "
        "or ethics initiatives, I'd appreciate it if you could point me toward the right person "
        f"at {company}.\"\n"
        "6. Tone: Use the 'Unity' principle from Cialdini — position yourself as a fellow "
        "peer, not a vendor. Peer-to-peer, not pitch.\n\n"
        "Constraints:\n"
        "- Do NOT use placeholder brackets like [Name] — all lead data is already provided above.\n"
        "- Keep the email under 250 words.\n"
        "- Return ONLY the email (subject line + body), no extra commentary."
    )


async def generate_drafts(
    leads: List[Dict[str, Any]],
    context: str,
) -> List[Dict[str, Any]]:
    """Generate email drafts for a list of leads using Vertex AI.

    Args:
        leads: List of lead dicts (as returned by ``/suggester``).
        context: Campaign purpose / context for the outreach emails.

    Returns:
        List of dicts, each containing the original ``lead`` payload and
        the generated ``draft`` text (or ``None`` on failure).
    """
    model = _get_model()
    drafts: List[Dict[str, Any]] = []

    for lead in leads:
        payload = lead.get("payload", lead)
        prompt = _build_prompt(lead, context)

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
