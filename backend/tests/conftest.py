import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["BOT_TOKEN"] = "123456:unit-test-only-token"
os.environ["PUBLIC_URL"] = "https://frontend.example.test/checker/"
os.environ["ALLOWED_ORIGINS"] = "https://frontend.example.test"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app


@pytest.fixture
def db_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    yield factory
    engine.dispose()


@pytest.fixture
def db(db_factory):
    with db_factory() as session:
        yield session


@pytest.fixture
def client(db_factory):
    def override_db():
        with db_factory() as session:
            yield session
    app.dependency_overrides[get_db] = override_db
    with TestClient(app, base_url="https://api.example.test") as client:
        yield client
    app.dependency_overrides.clear()
