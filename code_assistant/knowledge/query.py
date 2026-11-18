import ast
import re


def build_query(state, focus=""):
    # No cases, candidate output or untrusted raw traceback included.
    symbols = []
    try:
        for node in ast.walk(ast.parse(state["code"])):
            if isinstance(node, ast.Import):
                symbols.extend(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                symbols.append(node.module or "")
                symbols.extend(a.name for a in node.names)
            elif isinstance(node, ast.Attribute):
                symbols.append(node.attr)
    except (SyntaxError, RecursionError):
        pass
    errors = [str(o.get("error_type", "")) for o in state.get("observations", [])[-3:]]
    value = state["goal"][:2000] + "\n" + " ".join(dict.fromkeys(symbols))[:1200] + "\n" + " ".join(errors) + "\n" + focus
    return re.sub(r"(?i)(bearer\s+|api[_-]?key\s*[:=]\s*)\S+|sk-[A-Za-z0-9_-]+", "[redacted]", value)[:4000]
