"""Compatibility bootstrap only for historical `python code_assistant/foo.py` commands."""
def bootstrap():
    import sys
    from pathlib import Path
    root = str(Path(__file__).resolve().parents[1])
    if root not in sys.path:
        sys.path.insert(0, root)
