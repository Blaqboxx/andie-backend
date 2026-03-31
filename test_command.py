import requests
import json
from datetime import datetime

def test_command():
    url = "http://localhost:8000/command"
    payload = {"command": "status", "user_id": "test-user"}
    headers = {"Content-Type": "application/json"}
    response = requests.post(url, data=json.dumps(payload), headers=headers)
    print("Status Code:", response.status_code)
    print("Response:", response.json())

if __name__ == "__main__":
    print(f"[TEST] {datetime.now()} - Testing /command endpoint...")
    test_command()
