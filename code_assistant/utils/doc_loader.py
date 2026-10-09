"""离线 LCEL 接口说明；生成流程不再依赖启动时抓取网页。"""
from pathlib import Path


def load_lcel_context() -> str:
    return (Path(__file__).resolve().parents[1] / "sandbox_runtime" / "offline_context.md").read_text(encoding="utf-8")
