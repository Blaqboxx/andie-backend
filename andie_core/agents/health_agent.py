import psutil

def run():
    cpu = psutil.cpu_percent(interval=1)
    mem = psutil.virtual_memory().percent
    disk = psutil.disk_usage('/').percent
    print(f"[HealthAgent] CPU: {cpu}% | MEM: {mem}% | DISK: {disk}%")
    return {"cpu": cpu, "memory": mem, "disk": disk}
