import requests

MEMORY_API_URL = "http://localhost:8000/memory"

def get_recent_memory(n=5):
    try:
        resp = requests.post(f"{MEMORY_API_URL}/query", json={"query": "recent", "top_k": n})
        resp.raise_for_status()
        return resp.json().get("results", [])
    except Exception as e:
        return []

def store_result(entry):
    try:
        requests.post(f"{MEMORY_API_URL}/store", json={"content": str(entry), "metadata": {"type": "result"}})
    except Exception:
        pass

def store_error_pattern(error, fix):
    try:
        requests.post(f"{MEMORY_API_URL}/store", json={"content": str(error), "metadata": {"type": "error_pattern", "fix": fix}})
    except Exception:
        pass

def find_similar_fix(error):
    try:
        resp = requests.post(f"{MEMORY_API_URL}/query", json={"query": error, "top_k": 1, "metadata": {"type": "error_pattern"}})
        resp.raise_for_status()
        results = resp.json().get("results", [])
        if results:
            return results[0].get("metadata", {}).get("fix")
    except Exception:
        pass
    return None
    return None
