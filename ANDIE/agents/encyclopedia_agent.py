import json
import os

def search_encyclopedia(query: str):
    for file in os.listdir("knowledge/encyclopedia"):
        with open(f"knowledge/encyclopedia/{file}") as f:
            data = json.load(f)

            for key, value in data.items():
                if key in query.lower():
                    return value

    return None

