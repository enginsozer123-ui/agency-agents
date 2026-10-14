"""İş kayıtları (SQLite). Aynı içerik iki kez işlenmez; başarısız işler yeniden denenebilir."""

from __future__ import annotations

import hashlib
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

PENDING, PROCESSING, DONE, FAILED = "pending", "processing", "done", "failed"
_CHUNK = 8 * 1024 * 1024


def fingerprint(path: Path) -> str:
    """Boyut + ilk 8 MB + son 8 MB üzerinden SHA-256. Büyük dosyada tam hash'ten çok daha hızlı;
    dosya adı değişse de aynı içerik aynı parmak izini verir."""
    size = path.stat().st_size
    h = hashlib.sha256(str(size).encode())
    with path.open("rb") as f:
        h.update(f.read(_CHUNK))
        if size > _CHUNK:
            f.seek(max(size - _CHUNK, _CHUNK))
            h.update(f.read(_CHUNK))
    return h.hexdigest()


@dataclass
class Job:
    id: int
    fingerprint: str
    source_path: str
    status: str
    attempts: int
    error: str | None
    output_dir: str | None
    created_at: float
    updated_at: float


class InstanceLock:
    """Aynı durum klasörü için tek çalışan örnek (macOS/Linux, fcntl)."""

    def __init__(self, state_dir: Path):
        state_dir.mkdir(parents=True, exist_ok=True)
        self.path = state_dir / "vea.lock"
        self._fh = None

    def acquire(self) -> None:
        import fcntl

        self._fh = self.path.open("w")
        try:
            fcntl.flock(self._fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._fh.close()
            self._fh = None
            raise RuntimeError(f"Başka bir umbrella_vea örneği çalışıyor (kilit: {self.path}).")

    def release(self) -> None:
        if self._fh:
            self._fh.close()
            self._fh = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()


class JobStore:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fingerprint TEXT UNIQUE NOT NULL,
                source_path TEXT NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                output_dir TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )"""
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def _row(self, row: sqlite3.Row | None) -> Job | None:
        return Job(**dict(row)) if row else None

    def get(self, job_id: int) -> Job | None:
        return self._row(self.conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())

    def by_fingerprint(self, fp: str) -> Job | None:
        return self._row(self.conn.execute("SELECT * FROM jobs WHERE fingerprint=?", (fp,)).fetchone())

    def all(self) -> list[Job]:
        return [Job(**dict(r)) for r in self.conn.execute("SELECT * FROM jobs ORDER BY id")]

    def register(self, fp: str, source: Path) -> tuple[Job, bool]:
        """(iş, yeni_mi) döndürür. Aynı parmak izi varsa yeni kayıt açılmaz."""
        existing = self.by_fingerprint(fp)
        if existing:
            return existing, False
        now = time.time()
        try:
            cur = self.conn.execute(
                "INSERT INTO jobs (fingerprint, source_path, status, created_at, updated_at) VALUES (?,?,?,?,?)",
                (fp, str(source), PENDING, now, now),
            )
            self.conn.commit()
        except sqlite3.IntegrityError:  # aynı anda başka bir süreç kaydetti
            return self.by_fingerprint(fp), False  # type: ignore[return-value]
        return self.get(cur.lastrowid), True  # type: ignore[return-value]

    def _update(self, job_id: int, **values) -> None:
        values["updated_at"] = time.time()
        cols = ", ".join(f"{k}=?" for k in values)
        self.conn.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*values.values(), job_id))
        self.conn.commit()

    def start(self, job_id: int) -> None:
        job = self.get(job_id)
        self._update(job_id, status=PROCESSING, attempts=(job.attempts if job else 0) + 1, error=None)

    def finish(self, job_id: int, output_dir: Path) -> None:
        self._update(job_id, status=DONE, output_dir=str(output_dir), error=None)

    def fail(self, job_id: int, error: str, output_dir: Path | None = None) -> None:
        self._update(job_id, status=FAILED, error=error[:4000], output_dir=str(output_dir) if output_dir else None)

    def reset_for_retry(self, job_id: int) -> bool:
        job = self.get(job_id)
        if not job or job.status not in (FAILED, PROCESSING):
            return False
        self._update(job_id, status=PENDING, attempts=0, error=None)
        return True

    def recover_interrupted(self) -> int:
        """Önceki çalışma yarıda kesildiyse 'processing' kalan işleri tekrar sıraya alır."""
        cur = self.conn.execute(
            "UPDATE jobs SET status=?, updated_at=? WHERE status=?", (PENDING, time.time(), PROCESSING)
        )
        self.conn.commit()
        return cur.rowcount

    def runnable(self, max_attempts: int) -> list[Job]:
        rows = self.conn.execute(
            "SELECT * FROM jobs WHERE status=? OR (status=? AND attempts<?) ORDER BY id",
            (PENDING, FAILED, max_attempts),
        )
        return [Job(**dict(r)) for r in rows]
