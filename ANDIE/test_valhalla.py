from valhalla.controller.sandbox_manager import run_in_sandbox

safe_code = """
print("SAFE EXECUTION")
"""

result = run_in_sandbox(safe_code)
print(result)
