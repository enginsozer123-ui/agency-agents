"""input/ klasörünü izler. Kopyalaması biten videoları bir kez işler; durumları SQLite'ta tutar."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import Config
from .ffmpeg_tools import MediaError, probe
from .state import DONE, FAILED, JobStore, fingerprint

log = logging.getLogger("umbrella_vea.watcher")

TEMP_SUFFIXES = (".part", ".crdownload", ".download", ".tmp", ".partial")


@dataclass
class _Seen:
    size: int
    mtime_ns: int
    stable: int = 0


def is_candidate_file(p: Path, extensions: tuple[str, ...]) -> bool:
    name = p.name
    if not p.is_file() or name.startswith(".") or name.startswith("~"):
        return False
    if any(name.lower().endswith(s) for s in TEMP_SUFFIXES):
        return False
    return p.suffix.lower() in extensions


def validate_dirs(cfg: Config) -> None:
    """Sistemin yazdığı hiçbir klasör input/ içinde olamaz (orijinallerin yanına dosya yazılmaz)."""
    inp = cfg.input_dir
    for label, d in (("Çıktı", cfg.output_dir), ("Durum", cfg.state_dir), ("Log", cfg.log_dir)):
        if d == inp or inp in d.parents:
            raise ValueError(f"{label} klasörü ({d}) girdi klasörünün ({inp}) içinde olamaz.")


class Watcher:
    def __init__(self, cfg: Config, store: JobStore, processor: Callable[[Path, str], Path]):
        """processor(kaynak, parmak_izi) -> çıktı klasörü. Hata olursa istisna fırlatmalı."""
        validate_dirs(cfg)
        self.cfg = cfg
        self.store = store
        self.processor = processor
        self.seen: dict[Path, _Seen] = {}
        # Aynı dosyayı her turda yeniden hash'lememek için: yol -> (boyut, mtime, parmak izi)
        self.known: dict[Path, tuple[int, int, str]] = {}
        self._empty_logged = False

    def _stable_files(self) -> list[Path]:
        inp = self.cfg.input_dir
        inp.mkdir(parents=True, exist_ok=True)
        files = sorted(p for p in inp.iterdir() if is_candidate_file(p, self.cfg.watch.extensions))
        if not files:
            if not self._empty_logged:
                log.info("input klasörü boş, yeni video bekleniyor: %s", inp)
                self._empty_logged = True
            self.seen.clear()
            self.known.clear()
            return []
        self._empty_logged = False
        ready = []
        for p in files:
            try:
                st = p.stat()
            except FileNotFoundError:
                continue
            prev = self.seen.get(p)
            if prev and prev.size == st.st_size and prev.mtime_ns == st.st_mtime_ns and st.st_size > 0:
                prev.stable += 1
            else:
                if prev is None:
                    log.info("Yeni dosya algılandı: %s (kopyalamanın bitmesi bekleniyor)", p.name)
                self.seen[p] = prev = _Seen(st.st_size, st.st_mtime_ns)
            if prev.stable >= self.cfg.watch.stable_checks:
                ready.append(p)
        for gone in set(self.seen) - set(files):
            del self.seen[gone]
            self.known.pop(gone, None)
        return ready

    def _handle(self, path: Path) -> None:
        try:
            st = path.stat()
        except FileNotFoundError:
            return  # bu arada taşındı/silindi
        cached = self.known.get(path)
        if cached and cached[:2] == (st.st_size, st.st_mtime_ns):
            fp = cached[2]
        else:
            try:
                fp = fingerprint(path)
            except OSError as exc:
                log.warning("%s okunamadı, sonra tekrar denenecek: %s", path.name, exc)
                return
            self.known[path] = (st.st_size, st.st_mtime_ns, fp)
        job, new = self.store.register(fp, path)
        if not new and job.status == DONE:
            return  # aynı içerik daha önce işlendi (ad değişmiş olsa da)
        if not new and job.status == FAILED and job.attempts >= self.cfg.watch.max_attempts:
            return  # 'retry' komutu ile tekrar sıraya alınabilir
        if not new and job.source_path != str(path):
            log.info("%s daha önce %s adıyla kaydedilmiş (iş #%d)", path.name, Path(job.source_path).name, job.id)
        self._run(job.id, path, fp)

    def _run(self, job_id: int, path: Path, fp: str) -> None:
        try:
            probe(path)  # kopyalaması yarım kalmış/bozuk dosyayı erken yakala
        except MediaError as exc:
            self.store.start(job_id)
            self.store.fail(job_id, f"Geçersiz veya bozuk video: {exc}")
            log.error("İş #%d başarısız (%s): %s", job_id, path.name, exc)
            return
        self.store.start(job_id)
        log.info("İş #%d başladı: %s", job_id, path.name)
        try:
            out = self.processor(path, fp)
        except Exception as exc:
            self.store.fail(job_id, f"{type(exc).__name__}: {exc}")
            log.exception("İş #%d başarısız (%s): %s", job_id, path.name, exc)
            return
        self.store.finish(job_id, out)
        log.info("İş #%d tamamlandı → %s", job_id, out)

    def poll_once(self) -> None:
        for path in self._stable_files():
            self._handle(path)
        # 'process' ile input/ dışından eklenmiş ve 'retry' ile sıraya alınmış işler
        inp = self.cfg.input_dir
        for job in self.store.runnable(self.cfg.watch.max_attempts):
            src = Path(job.source_path)
            if inp in src.parents or not src.is_file():
                continue
            try:
                if fingerprint(src) != job.fingerprint:
                    self.store.fail(job.id, "Kaynak dosya değişmiş; yeniden 'process' ile ekleyin")
                    continue
            except OSError:
                continue
            self._run(job.id, src, job.fingerprint)

    def run(self, once: bool = False, max_wait_sec: float = 120.0) -> None:
        """once=True: klasördeki mevcut dosyalar kararlı hale gelip işlenince çıkar."""
        recovered = self.store.recover_interrupted()
        if recovered:
            log.warning("Yarıda kalmış %d iş tekrar sıraya alındı", recovered)
        log.info("İzleniyor: %s (her %.1f sn)", self.cfg.input_dir, self.cfg.watch.poll_interval_sec)
        started = time.time()
        while True:
            self.poll_once()
            if once:
                pending = [p for p, s in self.seen.items() if s.stable < self.cfg.watch.stable_checks]
                if not pending or time.time() - started > max_wait_sec:
                    return
            time.sleep(self.cfg.watch.poll_interval_sec)
