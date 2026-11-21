"""向量的存取：float32 二进制，按模型名作废（SPEC-storage F14-04）。"""

from __future__ import annotations

import sqlite3
from array import array
from collections.abc import Iterable, Mapping, Sequence


def _pack(vector: Sequence[float]) -> bytes:
    return array("f", vector).tobytes()


def _unpack(data: bytes) -> list[float]:
    values = array("f")
    values.frombytes(data)
    return values.tolist()


def get(
    conn: sqlite3.Connection, model: str, space: str, keys: Iterable[str] | None = None
) -> dict[str, list[float]]:
    """只返回同一个模型算出的向量；keys 为 None 时返回这个空间里的全部。"""
    if keys is None:
        rows = conn.execute(
            "SELECT key, data FROM vectors WHERE model = ? AND space = ?", (model, space)
        ).fetchall()
        return {row["key"]: _unpack(row["data"]) for row in rows}
    wanted = list(dict.fromkeys(keys))
    found: dict[str, list[float]] = {}
    for start in range(0, len(wanted), 500):  # SQLite 一条语句的参数个数有上限
        chunk = wanted[start : start + 500]
        marks = ",".join("?" * len(chunk))
        rows = conn.execute(
            f"SELECT key, data FROM vectors WHERE model = ? AND space = ? AND key IN ({marks})",
            (model, space, *chunk),
        ).fetchall()
        found.update({row["key"]: _unpack(row["data"]) for row in rows})
    return found


def put(
    conn: sqlite3.Connection, model: str, space: str, vectors: Mapping[str, Sequence[float]]
) -> None:
    dims = {len(v) for v in vectors.values()}
    if len(dims) > 1:
        raise ValueError("同一个模型返回了不同维度的向量")
    conn.executemany(
        "INSERT OR REPLACE INTO vectors (model, space, key, dim, data) VALUES (?, ?, ?, ?, ?)",
        [(model, space, key, len(v), _pack(v)) for key, v in vectors.items()],
    )


def replace_space(
    conn: sqlite3.Connection, model: str, space: str, vectors: Mapping[str, Sequence[float]]
) -> None:
    """这个空间只保留给出的向量（其他模型的旧向量一起清掉）。"""
    conn.execute("DELETE FROM vectors WHERE space = ?", (space,))
    put(conn, model, space, vectors)
