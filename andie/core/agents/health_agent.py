import psutil
from ..logger import log_event

def run():
    cpu = psutil.cpu_percent(interval=1)
    mem = psutil.virtual_memory().percent
    disk = psutil.disk_usage('/').percent
    log_event("health.log", f"CPU: {cpu}% | MEM: {mem}% | DISK: {disk}%")
    print(f"[HealthAgent] CPU: {cpu}% | MEM: {mem}% | DISK: {disk}%")
    return {"cpu": cpu, "memory": mem, "disk": disk}
