import time

def monitor_execution(start_time, timeout=5):
    current_time = time.time()
    elapsed = current_time - start_time

    if elapsed > timeout:
        return {
            "status": "KILLED",
            "reason": f"Execution exceeded {timeout}s",
            "elapsed": elapsed
        }

    return {
        "status": "OK",
        "elapsed": elapsed
    }
