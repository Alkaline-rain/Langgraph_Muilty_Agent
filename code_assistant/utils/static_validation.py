"""统一预检。AST 规则仅缩小任务范围，Docker 才是执行隔离边界。"""
import ast

from schemas.models import ExecutionResult

ALLOWED_MODULES = frozenset({
    "math", "json", "re", "collections", "itertools", "functools",
    "statistics", "decimal", "fractions", "string", "typing",
    "dataclasses", "enum", "operator", "heapq", "bisect", "copy", "langchain_core",
})
FORBIDDEN_CALLS = frozenset({
    "open", "eval", "exec", "compile", "__import__", "input", "globals", "locals",
    "vars", "getattr", "setattr", "delattr", "breakpoint", "exit", "quit",
})


def validate_code(imports: str, code: str) -> ExecutionResult:
    source = imports + "\n" + code
    if len(source.encode("utf-8")) > 128 * 1024:
        return ExecutionResult(success=False, error_type="security_error",
                               error_message="代码超过 128 KiB 上限")
    try:
        tree = ast.parse(source)
        compile(tree, "candidate.py", "exec")
        import_tree = ast.parse(imports)
    except (SyntaxError, ValueError, RecursionError) as exc:
        return ExecutionResult(success=False, error_type="syntax_error", error_message=str(exc)[:1000])

    def reject(message):
        return ExecutionResult(success=False, error_type="security_error", error_message=message)

    if any(not isinstance(n, (ast.Import, ast.ImportFrom)) for n in import_tree.body):
        return reject("imports 字段只能包含 import/from import 语句")
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            if isinstance(node, ast.ImportFrom) and node.level:
                return reject("不支持相对导入")
            for name in names:
                if name.split(".")[0] not in ALLOWED_MODULES:
                    return reject(f"离线镜像不允许导入 {name}；允许：{', '.join(sorted(ALLOWED_MODULES))}")
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            return reject("不允许访问双下划线属性")
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_CALLS:
            return reject(f"不允许访问内建函数 {node.id}")
    return ExecutionResult(success=True)
