"""POST /v1/parts/{part_key}/evaluate — judge a candidate against requirements.

A POST for what is a pure computation, because the requirements do not fit in a
query string. Nothing is written: the response is a report, and recording it is
a separate, deliberate call to the generic-part member review route -- so
evaluating a candidate can never quietly change an approval.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter(prefix="/v1", tags=["parts"])


class PredicateBody(BaseModel):
    """One checkable requirement. See domain/predicates.py for the semantics.

    `extra="forbid"` is load-bearing, not tidiness. Pydantic's default is to
    drop unknown keys, so a typo'd field name (`atribute`) would be discarded
    here and the surviving predicate would be evaluated as if the caller had
    never asked for it -- returning a cheerful `pass` for a requirement that
    was silently thrown away. Rejecting the request is the only safe answer.
    """

    model_config = ConfigDict(extra="forbid")

    attribute: str = ""
    op: str
    bound: str = "value"
    value: float | None = None
    unit: str = ""
    values: list[str] = Field(default_factory=list)
    package: str = ""
    qualifier: str = ""
    blocking: bool = True
    label: str = ""
    note: str = ""


class EvaluateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    predicates: list[PredicateBody] = Field(default_factory=list)
    # Omit to use the candidate's own package from the parts cache. Pass it
    # explicitly for a part not in inventory yet -- the usual case for a
    # proposed alternate.
    package: str | None = None
    # Which distributor to believe when several publish the same parametric.
    prefer: list[str] | None = None


@router.post("/parts/{part_key}/evaluate", operation_id="evaluate_part_predicates")
def evaluate_part_predicates(request: Request, part_key: str, body: EvaluateBody) -> dict:
    api = request.app.state.api
    try:
        return api.evaluate_part_predicates(
            part_key,
            [p.model_dump(exclude_none=True) for p in body.predicates],
            body.package,
            body.prefer,
        )
    except ValueError as exc:
        # A malformed predicate is a 400, never a dropped requirement: a
        # requirement that silently vanishes reads to the caller as a pass.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
