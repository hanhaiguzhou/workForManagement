"""数据库连接与会话工厂，外加一个最小的幂等列升级。

项目不引入 Alembic，但表结构会随工单演进（比如 `users.last_parse_notice`）。
`init_db` 在 `create_all` 之后做一次"缺列就补"的迁移，让旧库文件原地可用。
新增列一律用 `ALTER TABLE ... ADD COLUMN`，只允许加可空或有默认值的列。
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base

# 需要补列的表：表名 -> {列名: DDL 片段}
_MIGRATIONS = {
    "users": {
        "last_parse_notice": "ALTER TABLE users ADD COLUMN last_parse_notice VARCHAR(1024) NOT NULL DEFAULT ''",
        "jiaowu_cookies": "ALTER TABLE users ADD COLUMN jiaowu_cookies VARCHAR(2048) NOT NULL DEFAULT ''",
    },
    "schedule_settings": {
        "template_path": "ALTER TABLE schedule_settings ADD COLUMN template_path VARCHAR(512) NOT NULL DEFAULT ''",
        "template_texts": "ALTER TABLE schedule_settings ADD COLUMN template_texts TEXT NOT NULL DEFAULT ''",
    },
}


def make_engine(db_path: Path):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    return engine


def make_session_factory(engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def init_db(engine) -> None:
    Base.metadata.create_all(engine)
    _apply_migrations(engine)


def _apply_migrations(engine) -> None:
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, columns in _MIGRATIONS.items():
            if table not in existing_tables:
                continue
            existing = {c["name"] for c in inspector.get_columns(table)}
            for column, ddl in columns.items():
                if column not in existing:
                    conn.execute(text(ddl))
