import os
import webbrowser

def run_command(cmd):
    try:
        output = os.popen(cmd).read()
        return {
            "status": "success",
            "action": "run_command",
            "command": cmd,
            "output": output.strip()
        }
    except Exception as e:
        return {
            "status": "error",
            "action": "run_command",
            "command": cmd,
            "error": str(e)
        }

def open_browser(url):
    try:
        webbrowser.open(url)
        return {
            "status": "success",
            "action": "open_browser",
            "url": url,
            "message": f"Opened {url}"
        }
    except Exception as e:
        return {
            "status": "error",
            "action": "open_browser",
            "url": url,
            "error": str(e)
        }

def memory_tool(task):
    """ Import here to avoid circular import
    from andie_core import andie_core
    results = andie_core.memory.recall(task, n=5)
    return {
        "status": "success",
        "tool": "memory",
        "message": f"Found {len(results)} related memories",
        "data": results
    }

tools = {
    "run_command": run_command,
    "open_browser": open_browser,
    "memory": memory_tool,
}
