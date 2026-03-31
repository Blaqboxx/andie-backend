def store_execution(goal: str, result: dict):
    # Placeholder for memory storage logic
    print(f"[MEMORY_MANAGER] Storing: {goal} => {result}")
    event = {"type": "execution", "goal": goal, "result": result}
    _memory_bus.append(event)
    print(f"[MEMORY_MANAGER] Storing: {goal} => {result}")

# Shared memory bus (in-memory for now)
_memory_bus = []

def store_event(event: dict):
    _memory_bus.append(event)
    print(f"[MEMORY_MANAGER] Event: {event}")

def get_memory(last_n=20):
    return _memory_bus[-last_n:]
