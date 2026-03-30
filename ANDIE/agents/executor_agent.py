
from valhalla.controller.sandbox_manager import run_in_sandbox
import requests

class BaseExecutorAgent:
    def run_task(self, code: str):
        raise NotImplementedError

class LocalExecutorAgent(BaseExecutorAgent):
    def run_task(self, code: str):
        print("[LocalExecutorAgent] Executing task locally...")
        result = run_in_sandbox(code)
        return result

class RemoteExecutorAgent(BaseExecutorAgent):
    def __init__(self, endpoint):
        self.endpoint = endpoint

    def run_task(self, code: str):
        print(f"[RemoteExecutorAgent] Sending task to {self.endpoint} ...")
        try:
            response = requests.post(f"{self.endpoint}/run", json={"code": code}, timeout=30)
            return response.json()
        except Exception as e:
            return {"status": "ERROR", "details": str(e)}
