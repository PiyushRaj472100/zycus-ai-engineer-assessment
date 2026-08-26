"""FastAPI application — Task 1: Intelligent Ticket Triage Agent."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import ValidationError

from .config import settings
from .retrieval import KnowledgeBase, load_kb
from .schemas import TicketRequest, TriageResponse
from .triage import triage_ticket

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Application lifecycle
# ---------------------------------------------------------------------------

_kb: KnowledgeBase | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _kb
    logger.info("Loading knowledge base from '%s'…", settings.kb_path)
    _kb = load_kb(settings.kb_path)
    logger.info("KB loaded: %d chunks from %s", _kb.total_chunks, settings.kb_path)
    yield
    _kb = None


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Ticket Triage Agent",
    description=(
        "Intelligent support ticket triage: classifies product area, issue "
        "category, urgency (P1–P4), surfaces matching KB documents, routes "
        "to the correct team, and drafts a first response."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------

@app.exception_handler(ValidationError)
async def pydantic_validation_handler(request: Request, exc: ValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors()},
    )


@app.exception_handler(RuntimeError)
async def runtime_error_handler(request: Request, exc: RuntimeError) -> JSONResponse:
    logger.error("Runtime error: %s", exc)
    return JSONResponse(
        status_code=502,
        content={"detail": str(exc)},
    )

@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")



@app.get("/health", tags=["Meta"])
def health() -> dict[str, Any]:
    """Returns service health status and KB statistics."""
    return {
        "status": "ok",
        "kb_chunks": _kb.total_chunks if _kb else 0,
        "model": settings.gemini_model,
    }


@app.post("/triage", response_model=TriageResponse, tags=["Triage"])
def triage(ticket: TicketRequest) -> TriageResponse:
    """
    Triage a support ticket.

    Accepts a `subject` and `body`, retrieves relevant knowledge-base context,
    and returns a structured triage result including classification, urgency,
    matched KB document, recommended responder team, and a draft first response.
    """
    if _kb is None:
        raise HTTPException(status_code=503, detail="Knowledge base not loaded.")

    # Retrieve relevant KB chunks.
    query = f"{ticket.subject} {ticket.body}"
    kb_chunks = _kb.retrieve(query, top_k=4)
    logger.info(
        "Retrieved %d KB chunks for ticket subject=%r", len(kb_chunks), ticket.subject
    )

    # Run LLM triage.
    try:
        result = triage_ticket(ticket.subject, ticket.body, kb_chunks)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return result
