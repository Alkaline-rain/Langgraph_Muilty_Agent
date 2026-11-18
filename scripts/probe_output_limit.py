"""Fixed-code regression probe; never calls a model."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from code_assistant.utils.sandbox import execute_code
result = execute_code("", "def solve(payload):\n    print('x' * 200000)\n    return 1")
print(result.model_dump(exclude={"stdout", "stderr"}))
