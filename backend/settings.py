import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".platform.env")


def database_url():
    return os.getenv("DATABASE_URL", "sqlite:///" + (ROOT / ".platform/tasks.sqlite").as_posix())


def checkpoint_path():
    return str(ROOT / ".platform/checkpoints.sqlite")


def token():
    value = os.getenv("PLATFORM_TOKEN", "")
    if len(value) < 24:
        raise RuntimeError("请运行 python -m backend.setup，生成本地平台访问令牌")
    return value
