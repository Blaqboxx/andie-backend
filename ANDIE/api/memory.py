from fastapi import APIRouter
from core.memory_manager import get_memory

router = APIRouter()

@router.get("/memory")
def memory():
    return get_memory(50)
