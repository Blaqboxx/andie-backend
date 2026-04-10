logs = []

def log_event(msg):
    logs.append(msg)

def get_logs():
    return logs[-100:]
