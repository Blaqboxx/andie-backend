from fastapi import APIRouter

router = APIRouter()

@router.get("/dashboard")
def trust_dashboard():
    return {
        "trust_score": 0.95,
        "status": "stable",
        "alerts": [],
        "message": "Trust system operational"
    }
