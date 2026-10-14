"""Komut satırı: python -m umbrella_vea <komut>"""

from __future__ import annotations

import argparse
import logging
import sys
from importlib import metadata
from pathlib import Path

from .config import Config, load_config, load_env
from .ffmpeg_tools import MediaError, require_binaries, tool_version
from .logging_setup import setup_logging
from .state import DONE, InstanceLock, JobStore, fingerprint

log = logging.getLogger("umbrella_vea")


def _make_progress():
    last: dict[str, int] = {}

    def progress(stage: str, pct: float) -> None:
        step = int(pct * 100) // 10 * 10
        if last.get(stage) != step:
            last[stage] = step
            log.info("[%3d%%] %s", int(pct * 100), stage)

    return progress


def _processor(cfg: Config):
    from .pipeline import process_video
    from .transcribe import FasterWhisperTranscriber

    transcriber = FasterWhisperTranscriber(cfg.transcribe)  # model bir kez yüklenir, işler arasında paylaşılır

    def run(path: Path, fp: str) -> Path:
        out_dir, report = process_video(path, cfg, fp, transcriber=transcriber, progress=_make_progress())
        log.info("Rapor: %s (durum: %s, %d aday)", out_dir / "report.json", report["status"], len(report.get("candidates", [])))
        return out_dir

    return run


def cmd_watch(cfg: Config, args) -> int:
    from .watcher import Watcher

    try:
        with InstanceLock(cfg.state_dir):
            store = JobStore(cfg.state_dir / "jobs.sqlite3")
            try:
                Watcher(cfg, store, _processor(cfg)).run(once=args.once)
            except KeyboardInterrupt:
                log.info("Durduruldu.")
            finally:
                store.close()
    except RuntimeError as exc:
        log.error("%s", exc)
        return 2
    return 0


def cmd_process(cfg: Config, args) -> int:
    from .watcher import Watcher, validate_dirs

    validate_dirs(cfg)
    path = Path(args.file).resolve()
    if not path.is_file():
        log.error("Dosya bulunamadı: %s", path)
        return 2
    lock = InstanceLock(cfg.state_dir)
    try:
        lock.acquire()
    except RuntimeError as exc:
        log.error("%s", exc)
        return 2
    store = JobStore(cfg.state_dir / "jobs.sqlite3")
    try:
        fp = fingerprint(path)
        job, new = store.register(fp, path)
        if not new and job.status == DONE and not args.force:
            log.info("Bu video zaten işlenmiş (iş #%d): %s — yeniden işlemek için --force", job.id, job.output_dir)
            return 0
        w = Watcher(cfg, store, _processor(cfg))
        w._run(job.id, path, fp)
        return 0 if store.get(job.id).status == DONE else 1
    finally:
        store.close()
        lock.release()


def cmd_status(cfg: Config, args) -> int:
    store = JobStore(cfg.state_dir / "jobs.sqlite3")
    jobs = store.all()
    store.close()
    if not jobs:
        print("Kayıtlı iş yok.")
        return 0
    for j in jobs:
        print(f"#{j.id:<4} {j.status:<10} deneme={j.attempts}  {Path(j.source_path).name}")
        if j.output_dir:
            print(f"       çıktı: {j.output_dir}")
        if j.error:
            print(f"       hata : {j.error.splitlines()[0][:200]}")
    return 0


def cmd_retry(cfg: Config, args) -> int:
    store = JobStore(cfg.state_dir / "jobs.sqlite3")
    try:
        ids = [j.id for j in store.all() if j.status == "failed"] if args.all else [args.job_id]
        n = sum(1 for i in ids if i is not None and store.reset_for_retry(i))
        print(f"{n} iş yeniden sıraya alındı. İşlemek için: python -m umbrella_vea watch --once")
        return 0 if n else 1
    finally:
        store.close()


def cmd_doctor(cfg: Config, args) -> int:
    from .cloud.semantic_scoring import availability

    ok = True
    print(f"Python      : {sys.version.split()[0]}")
    for b in ("ffmpeg", "ffprobe"):
        v = tool_version(b)
        ok &= v != "bulunamadı"
        print(f"{b:<12}: {v}")
    for pkg in ("faster-whisper", "ctranslate2", "scenedetect", "opencv-python", "python-dotenv", "anthropic"):
        try:
            print(f"{pkg:<12}: {metadata.version(pkg)}")
        except metadata.PackageNotFoundError:
            print(f"{pkg:<12}: kurulu değil{' (isteğe bağlı)' if pkg == 'anthropic' else ''}")
            ok &= pkg == "anthropic"
    print(f"input       : {cfg.input_dir}")
    print(f"output      : {cfg.output_dir}")
    print(f"whisper     : model={cfg.transcribe.model} dil={cfg.transcribe.language} cihaz={cfg.transcribe.device}")
    print(f"bulut       : {availability(cfg.cloud)[1]}")
    if args.check_model:
        from .transcribe import FasterWhisperTranscriber, TranscriptionUnavailable

        try:
            FasterWhisperTranscriber(cfg.transcribe)._load()
            print("whisper     : model yüklendi ✔")
        except TranscriptionUnavailable as exc:
            ok = False
            print(f"whisper     : {exc}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="umbrella_vea", description="Umbrella Dijital AI Video Edit Assistant (MVP)")
    parser.add_argument("--config", default=None, help="TOML yapılandırma dosyası (varsayılan: ./config.toml varsa o)")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("watch", help="input/ klasörünü izle ve yeni videoları işle")
    p.add_argument("--once", action="store_true", help="mevcut dosyaları işle ve çık")
    p = sub.add_parser("process", help="tek bir dosyayı işle")
    p.add_argument("file")
    p.add_argument("--force", action="store_true", help="daha önce işlenmiş olsa da yeniden işle")
    sub.add_parser("status", help="iş durumlarını listele")
    p = sub.add_parser("retry", help="başarısız işi tekrar sıraya al")
    p.add_argument("job_id", nargs="?", type=int)
    p.add_argument("--all", action="store_true")
    p = sub.add_parser("doctor", help="kurulumu kontrol et")
    p.add_argument("--check-model", action="store_true", help="Whisper modelini yüklemeyi dene (ilk seferde indirir)")
    args = parser.parse_args(argv)

    cfg_path = args.config or ("config.toml" if Path("config.toml").exists() else None)
    try:
        cfg = load_config(cfg_path)
    except (OSError, ValueError) as exc:
        print(f"Yapılandırma hatası: {exc}", file=sys.stderr)
        return 2
    load_env(cfg.base_dir)
    setup_logging(cfg.log_dir, args.verbose)
    if args.cmd in ("watch", "process"):
        try:
            require_binaries()
        except MediaError as exc:
            log.error("%s", exc)
            return 2
    if args.cmd == "retry" and not args.all and args.job_id is None:
        parser.error("retry için iş numarası veya --all verin")
    handlers = {"watch": cmd_watch, "process": cmd_process, "status": cmd_status, "retry": cmd_retry, "doctor": cmd_doctor}
    try:
        return handlers[args.cmd](cfg, args)
    except ValueError as exc:
        log.error("%s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
