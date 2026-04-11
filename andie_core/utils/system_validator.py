import os

def validate_workspace(base="/mnt/andie_storage"):
    required = [
        "agents",
        "memory",
        "vector_db",
        "logs",
        "sentinel"
    ]
    results = {}
    for r in required:
        path = os.path.join(base, r)
        results[r] = os.path.exists(path)
    return results
