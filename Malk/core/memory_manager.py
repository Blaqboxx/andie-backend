import json
import os

_memory = []
MEMORY_FILE = "andie_memory.json"

def load_memory():
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, "r") as f:
            return json.load(f)
    return []

def save_memory(data):
    with open(MEMORY_FILE, "w") as f:
        json.dump(data, f)

def get_recent_memory(n=5):
    data = load_memory()
    return data[-n:]

def store_result(entry):
    data = load_memory()
    data.append(entry)
    save_memory(data)

def store_error_pattern(error, fix):
    data = load_memory()
    data.append({
        "type": "error_pattern",
        "error": error,
        "fix": fix
    })
    save_memory(data)

def find_similar_fix(error):
    data = load_memory()
    for entry in reversed(data):
        if entry.get("type") == "error_pattern":
            if error.split(":")[0] in entry.get("error", ""):
                return entry.get("fix")
    return None
