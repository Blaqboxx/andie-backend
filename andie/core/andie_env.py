import sys
import subprocess
import os

REQUIRED_PACKAGES = ["fastapi", "uvicorn", "openai", "starlette", "pydantic", "psutil", "requests"]

def validate_and_fix_env():
    missing = []
    for pkg in REQUIRED_PACKAGES:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    in_venv = (
        hasattr(sys, 'real_prefix') or (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix)
    )
    if missing:
        if in_venv:
            print(f"[ANDIE ENV] Missing packages: {missing}. Installing...")
            subprocess.check_call([sys.executable, "-m", "pip", "install"] + missing)
        else:
            print(f"[ANDIE ENV] ERROR: Missing packages: {missing} but not running inside a virtual environment.\nActivate your venv and install manually:")
            print(f"    source ./venv/bin/activate && pip install {' '.join(missing)}")
            return
    else:
        print("[ANDIE ENV] All required packages present.")

    if not in_venv:
        print("[ANDIE ENV] WARNING: Not running inside a virtual environment!")
    else:
        print(f"[ANDIE ENV] Using Python: {sys.executable}")

    # Optionally, freeze requirements for reproducibility
    req_path = os.path.join(os.path.dirname(__file__), "../requirements.txt")
    try:
        with open(req_path, "w") as f:
            subprocess.run([sys.executable, "-m", "pip", "freeze"], stdout=f)
        print(f"[ANDIE ENV] requirements.txt updated at {req_path}")
    except Exception as e:
        print(f"[ANDIE ENV] Could not write requirements.txt: {e}")
