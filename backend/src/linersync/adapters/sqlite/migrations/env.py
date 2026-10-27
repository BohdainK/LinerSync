from alembic import context
from sqlalchemy import Connection

from linersync.adapters.sqlite.schema import metadata

config = context.config


def run_migrations() -> None:
    connection: Connection = config.attributes["connection"]
    context.configure(connection=connection, target_metadata=metadata)
    with context.begin_transaction():
        context.run_migrations()


run_migrations()
