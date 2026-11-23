from collections.abc import Generator
from pathlib import Path
import ssl
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings

DEFAULT_SYSTEM_CA_BUNDLE = "/etc/ssl/certs/ca-certificates.crt"

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def build_mysql_ssl_context(settings: Settings) -> ssl.SSLContext:
    """
    Build a strict TLS 1.2+ SSLContext with certificate & hostname verification
    for PyMySQL connections (e.g., TiDB Cloud Serverless / Dedicated).
    """
    ca_path = settings.database_ssl_ca.strip() or DEFAULT_SYSTEM_CA_BUNDLE
    if not Path(ca_path).is_file():
        raise FileNotFoundError(
            f"Configured DATABASE_SSL_CA file does not exist: {ca_path}"
        )
    ctx = ssl.create_default_context(cafile=ca_path)
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


def build_engine_kwargs(
    settings: Settings,
    *,
    poolclass: Any = None,
) -> dict[str, Any]:
    """
    Build SQLAlchemy create_engine() kwargs shared by FastAPI and Alembic.
    - Passes PyMySQL TLS via connect_args (never embeds SSL params in DATABASE_URL).
    - Applies TiDB Cloud Serverless-friendly pool settings only for non-SQLite pooled engines.
    """
    url = make_url(settings.database_url)
    backend = url.get_backend_name()

    kwargs: dict[str, Any] = {"future": True}
    if poolclass is not None:
        kwargs["poolclass"] = poolclass
    elif backend != "sqlite":
        kwargs.update(
            {
                "pool_pre_ping": True,
                "pool_recycle": 300,
                "pool_size": 5,
                "max_overflow": 10,
            }
        )

    if backend == "mysql" and settings.database_ssl_required:
        kwargs["connect_args"] = {"ssl": build_mysql_ssl_context(settings)}

    return kwargs


def create_db_engine(
    settings: Settings | None = None,
    *,
    poolclass: Any = None,
) -> Engine:
    cfg = settings or get_settings()
    url = make_url(cfg.database_url)
    kwargs = build_engine_kwargs(cfg, poolclass=poolclass)
    return create_engine(url, **kwargs)


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_db_engine(get_settings())
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=get_engine(),
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        )
    return _session_factory


def get_db() -> Generator[Session, None, None]:
    db = get_session_factory()()
    try:
        yield db
    finally:
        db.close()


def check_database_health() -> tuple[bool, str]:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, "ok"
    except Exception as exc:  # pragma: no cover - depends on external MySQL
        return False, f"unavailable: {type(exc).__name__}"
