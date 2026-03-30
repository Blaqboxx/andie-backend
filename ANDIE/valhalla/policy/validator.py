import ast

FORBIDDEN_NAMES = {
    "exec", "eval", "open", "__import__", "os", "sys", "subprocess"
}

class CodeValidator(ast.NodeVisitor):
    def __init__(self):
        self.violations = []

    def visit_Import(self, node):
        self.violations.append("Imports are not allowed")

    def visit_ImportFrom(self, node):
        self.violations.append("Import-from statements are not allowed")

    def visit_Call(self, node):
        if isinstance(node.func, ast.Name):
            if node.func.id in FORBIDDEN_NAMES:
                self.violations.append(f"Forbidden function: {node.func.id}")
        self.generic_visit(node)

    def visit_Name(self, node):
        if node.id in FORBIDDEN_NAMES:
            self.violations.append(f"Forbidden name: {node.id}")

def validate_code(code: str):
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return {"status": "BLOCKED", "reason": str(e)}

    validator = CodeValidator()
    validator.visit(tree)

    if validator.violations:
        return {
            "status": "BLOCKED",
            "violations": validator.violations
        }

    return {"status": "SAFE"}
