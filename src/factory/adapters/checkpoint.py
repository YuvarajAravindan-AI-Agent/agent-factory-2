"""Checkpointer factories. Postgres is the real target; the in-memory saver serves unit tests."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from langgraph.checkpoint.postgres import PostgresSaver


@contextmanager
def postgres_saver(dsn: str) -> Iterator[PostgresSaver]:
    with PostgresSaver.from_conn_string(dsn) as saver:
        saver.setup()
        yield saver
