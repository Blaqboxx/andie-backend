import sys

def safe_exec(code):
    safe_globals = {
        "__builtins__": {
            "print": print,
            "range": range,
            "len": len
        }
    }
    exec(code, safe_globals)

if __name__ == "__main__":
    code = sys.stdin.read()
    safe_exec(code)
