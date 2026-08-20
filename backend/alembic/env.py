"""Alembic environment.

The database URL comes from app settings rather than alembic.ini so there is a
single source of truth for where the SQLite file lives.
"""

from __future__ import annotations

from logging.config import fileConfig

# Importing the package populates Base.metadata for autogenerate.
import app.models  # noqa: F401
from alembic import context
from app.core.config import get_settings
from app.core.db import Base, build_engine

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().database_url)
target_metadata = Base.metadata


def render_item(type_, obj, autogen_context):
    """Render app-defined column types as their underlying SQL type.

    ``UtcDateTime`` is a TypeDecorator over DateTime — identical in DDL, and
    only different in how Python values are marshalled. Rendering it as
    ``sa.DateTime()`` keeps migrations from importing application code, which
    would otherwise break the moment that code is renamed or removed.
    """
    if type_ == "type" and obj.__class__.__name__ == "UtcDateTime":
        return "sa.DateTime()"
    return False


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
        render_item=render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # Deliberately the app's own engine factory rather than engine_from_config:
    # the WAL / foreign-key pragmas are registered on its "connect" event, and a
    # separately-built engine would create the database without them.
    connectable = build_engine()
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # Required for SQLite: ALTER TABLE support is limited, so Alembic
            # rebuilds tables instead.
            render_as_batch=True,
            render_item=render_item,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
