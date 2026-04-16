from __future__ import annotations

from fastapi import APIRouter

from autonomy.explainer import explain_decision


router = APIRouter()


@router.get("/autonomy/explain")
def autonomy_explain_api():
    return explain_decision()
