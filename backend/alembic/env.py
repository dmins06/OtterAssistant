from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine.url import make_url

from app.core.config import get_settings
from app.database.base import Base
from app.database.session import create_db_engine
import app.models  # noqa: F401 — register all ORM models on Base.metadata

config = context.config
settings = get_settings()

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    # Pass SQLAlchemy URL directly instead of ConfigParser set_main_option()
    # so percent-encoded characters (e.g. %40, %23) in passwords never crash interpolation.
    url = make_url(settings.database_url)
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # Reuse the exact same TLS / URL configuration as FastAPI (with NullPool for migrations)
    connectable = create_db_engine(settings, poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
