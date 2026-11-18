"""Initialize local credentials and schema. Does not print credentials."""
import argparse
import secrets
from alembic.config import Config
from alembic import command
from backend.settings import ROOT, database_url


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlite", action="store_true", help="本地单 Worker 开发模式；生产方案使用 PostgreSQL")
    args = parser.parse_args()
    (ROOT / ".platform").mkdir(exist_ok=True)
    settings = ROOT / ".platform.env"
    if not settings.exists():
        url = "sqlite:///" + (ROOT / ".platform/tasks.sqlite").as_posix() if args.sqlite else "postgresql+psycopg://agent:agent_local_dev@127.0.0.1:55432/agent"
        settings.write_text(f"DATABASE_URL={url}\nPLATFORM_TOKEN={secrets.token_urlsafe(32)}\n", encoding="utf-8")
    from dotenv import load_dotenv
    load_dotenv(settings, override=False)
    config = Config(str(ROOT / "alembic.ini"))
    command.upgrade(config, "head")
    print("平台初始化完成。访问令牌保存在 .platform.env（请勿提交）。")


if __name__ == "__main__":
    main()
