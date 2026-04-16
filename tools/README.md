# ANDIE Tools

- `self_improve.py`: Prototype script for self-improving workflow.
  - In production, this would:
    - Propose a change (via LLM)
    - Create a branch
    - Apply the change
    - Run tests
    - Open a PR if tests pass
