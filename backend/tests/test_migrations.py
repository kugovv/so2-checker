import os
from pathlib import Path
import subprocess
import sys

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from app.db import Base


def test_migrations_upgrade_match_models_and_downgrade(tmp_path):
    env = {**os.environ, "DATABASE_URL": "sqlite:///" + str(tmp_path / "migration.db")}
    cwd = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], env=env, cwd=cwd, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    engine = create_engine(env["DATABASE_URL"])
    with engine.connect() as connection:
        assert set(Base.metadata.tables) <= set(inspect(connection).get_table_names())
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    engine.dispose()
    result = subprocess.run([sys.executable, "-m", "alembic", "downgrade", "base"], env=env, cwd=cwd, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
