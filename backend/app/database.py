from collections.abc import Generator
import re

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
database_url = URL.create(
    drivername="mysql+pymysql",
    username=settings.mysql_user,
    password=settings.mysql_password,
    host=settings.mysql_host,
    port=settings.mysql_port,
    database=settings.database_name,
    query={"charset": "utf8mb4"},
).render_as_string(hide_password=False)


def _create_mysql_database_if_missing(url_string: str) -> None:
    url = make_url(url_string)
    if url.get_backend_name() != "mysql":
        return

    database_name = url.database
    if not database_name or not re.fullmatch(r"[A-Za-z0-9_]+", database_name):
        raise ValueError(
            "DATABASE_NAME hanya boleh berisi huruf, angka, dan underscore"
        )

    server_url = URL.create(
        url.drivername,
        username=url.username,
        password=url.password,
        host=url.host,
        port=url.port,
        query=dict(url.query),
    )
    bootstrap_engine = create_engine(server_url, isolation_level="AUTOCOMMIT")
    try:
        with bootstrap_engine.connect() as connection:
            connection.execute(
                text(
                    f"CREATE DATABASE IF NOT EXISTS `{database_name}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
            )
    finally:
        bootstrap_engine.dispose()


_create_mysql_database_if_missing(database_url)
engine_options: dict = {"pool_pre_ping": True}
if database_url.startswith("sqlite"):
    engine_options["connect_args"] = {"check_same_thread": False}

engine = create_engine(database_url, **engine_options)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
