"""测试夹具：使用独立的临时数据目录与内存态 SQLite 文件。"""
from __future__ import annotations

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="bizportal-test-")
os.environ.setdefault("BIZPORTAL_DATA_DIR", _TMP)
os.environ.setdefault("BIZPORTAL_SECRET_KEY", "test-secret-key")
os.environ.setdefault("BIZPORTAL_ADMIN_EMAIL", "admin")
os.environ.setdefault("BIZPORTAL_ADMIN_PASSWORD", "Admin@123")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _prepare_database():
    init_db()
    yield


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c
