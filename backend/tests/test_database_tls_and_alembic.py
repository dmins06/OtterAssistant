import ssl
import subprocess
import sys

import pytest
from sqlalchemy import pool

from app.core.config import Settings
from app.database.session import (
    DEFAULT_SYSTEM_CA_BUNDLE,
    build_engine_kwargs,
    create_db_engine,
)
from app.main import app


def test_fastapi_app_imports_cleanly() -> None:
    assert app is not None
    assert app.title == "Otter Assistant"


def test_mysql_engine_without_tls_uses_tidb_friendly_pool_and_no_ssl() -> None:
    settings = Settings(
        database_url="mysql+pymysql://test_user:test_pass@127.0.0.1:3306/otter_assistant?charset=utf8mb4",
        database_ssl_required=False,
        database_ssl_ca="",
    )
    kwargs = build_engine_kwargs(settings)
    assert kwargs["pool_pre_ping"] is True
    assert kwargs["pool_recycle"] == 300
    assert kwargs["pool_size"] == 5
    assert kwargs["max_overflow"] == 10
    assert "connect_args" not in kwargs

    engine = create_db_engine(settings)
    try:
        assert engine.url.drivername == "mysql+pymysql"
    finally:
        engine.dispose()


def test_mysql_engine_with_tls_enforces_cert_and_hostname_verification() -> None:
    settings = Settings(
        database_url="mysql+pymysql://test_user:test_pass@gateway01.ap-southeast-1.prod.aws.tidbcloud.com:4000/otter_assistant?charset=utf8mb4",
        database_ssl_required=True,
        database_ssl_ca="",  # falls back to DEFAULT_SYSTEM_CA_BUNDLE
    )
    kwargs = build_engine_kwargs(settings)
    assert "connect_args" in kwargs
    ssl_ctx = kwargs["connect_args"]["ssl"]
    assert isinstance(ssl_ctx, ssl.SSLContext)
    assert ssl_ctx.verify_mode == ssl.CERT_REQUIRED
    assert ssl_ctx.check_hostname is True
    assert ssl_ctx.minimum_version >= ssl.TLSVersion.TLSv1_2

    # Ensure SSL params are NOT appended to DATABASE_URL
    engine = create_db_engine(settings)
    try:
        assert "ssl" not in engine.url.query
    finally:
        engine.dispose()


def test_mysql_tls_missing_ca_file_raises_clear_error() -> None:
    settings = Settings(
        database_url="mysql+pymysql://test_user:test_pass@127.0.0.1:4000/otter_assistant",
        database_ssl_required=True,
        database_ssl_ca="/nonexistent/path/ca.pem",
    )
    with pytest.raises(FileNotFoundError, match="DATABASE_SSL_CA"):
        build_engine_kwargs(settings)


def test_sqlite_engine_does_not_receive_mysql_pool_kwargs() -> None:
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        database_ssl_required=True,
    )
    kwargs = build_engine_kwargs(settings)
    assert "pool_size" not in kwargs
    assert "max_overflow" not in kwargs
    assert "connect_args" not in kwargs


def test_alembic_handles_percent_encoded_password_without_configparser_error() -> None:
    # Password contains %40 (@), %23 (#), %25 (%) which would crash ConfigParser set_main_option()
    encoded_url = "mysql+pymysql://test_user:p%40ss%23word%25123@127.0.0.1:4000/otter_assistant?charset=utf8mb4"
    settings = Settings(
        database_url=encoded_url,
        database_ssl_required=True,
        database_ssl_ca=DEFAULT_SYSTEM_CA_BUNDLE,
    )
    # 1. Online NullPool engine creation (same path as alembic/env.py run_migrations_online)
    engine = create_db_engine(settings, poolclass=pool.NullPool)
    try:
        assert engine.url.password == "p@ss#word%123"
    finally:
        engine.dispose()

    # 2. Offline alembic upgrade head --sql subprocess with percent-encoded DATABASE_URL
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
        capture_output=True,
        text=True,
        env={
            "DATABASE_URL": encoded_url,
            "DATABASE_SSL_REQUIRED": "true",
            "PATH": "/usr/bin:/bin",
        },
        check=False,
    )
    assert proc.returncode == 0, f"Alembic offline SQL failed: {proc.stderr}"
    assert "CREATE TABLE users" in proc.stdout
    assert "p%40ss" not in proc.stdout
    assert "p@ss#word%123" not in proc.stdout
