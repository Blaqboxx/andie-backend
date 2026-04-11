def is_safe(action: str) -> bool:
    blocked = ["rm -rf", "shutdown", "mkfs"]
    return not any(cmd in action for cmd in blocked)

def run(action: str, agent_name: str = "?"):
    if is_safe(action):
        print(f"[Sentinel] Action allowed for {agent_name}: {action}")
        return True
    else:
        print(f"[Sentinel] BLOCKED unsafe action for {agent_name}: {action}")
        return False
