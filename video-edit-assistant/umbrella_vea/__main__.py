"""Komut satırı: python -m umbrella_vea <komut>"""

from __future__ import annotations

import argparse
import logging
import sys
from importlib import metadata
from pathlib import Path

from .config import Config, ConfigError, available_profiles, load_config, load_env
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


def cmd_review(cfg: Config, args) -> int:
    from .review import ReviewError, decide, load_report

    store = JobStore(cfg.state_dir / "jobs.sqlite3")
    jobs = store.all()
    store.close()
    if args.action == "list":
        shown = 0
        for j in jobs:
            if not j.output_dir:
                continue
            try:
                rep = load_report(Path(j.output_dir))
            except ReviewError:
                continue
            rv = rep.get("review", {})
            if args.all or rv.get("status") == "pending":
                shown += 1
                print(f"#{j.id:<4} inceleme={rv.get('status', '?'):<12} {Path(j.source_path).name}")
                for r in rep.get("renders", []):
                    print(f"       {r['candidate_id']}: {r.get('status')}  {r.get('file', '')}  {r.get('duration_sec', '')} sn")
        if not shown:
            print("İnceleme bekleyen taslak yok.")
        return 0
    job = next((j for j in jobs if j.id == args.job_id), None)
    if job is None or not job.output_dir:
        log.error("İş #%s bulunamadı veya çıktısı yok.", args.job_id)
        return 2
    clips = [c.strip() for c in args.clips.split(",")] if args.clips else None
    try:
        rv = decide(Path(job.output_dir), cfg, approve=args.action == "approve", clip_ids=clips, note=args.note or "")
    except ReviewError as exc:
        log.error("%s", exc)
        return 1
    if rv["status"] == "approved":
        print(f"Onaylandı. Teslim klasörü: {Path(job.output_dir) / rv['delivery_dir']} ({', '.join(rv['delivered'])})")
    else:
        print("Reddedildi. Teslim yapılmadı.")
    return 0


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
    print(f"profil      : {cfg.profile.id or '(yok)'}  (mevcut: {', '.join(available_profiles())})")
    print(f"çıktı       : {cfg.render.width}x{cfg.render.height} ({cfg.render.aspect_ratio}), süre modu={cfg.candidates.duration_mode} "
          f"{cfg.candidates.min_sec:.0f}–{cfg.candidates.max_sec:.0f} sn")
    print(f"kurgu       : sessizlik kesme={cfg.edit.remove_silence} sahne={cfg.analysis.detect_scenes} "
          f"akıllı kesit={cfg.edit.smart_cuts} punch-in={cfg.edit.punch_in_zooms} ({cfg.edit.zoom_intensity})")
    print(f"altyazı     : {cfg.captions.timing}/{cfg.captions.style} vurgu={cfg.captions.highlight_active_word} "
          f"işle={cfg.captions.burn} srt={cfg.captions.export_srt}")
    print(f"teslim      : inceleme zorunlu={cfg.delivery.require_review_before_delivery} otomatik yayın={cfg.delivery.auto_publish}")
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
    parser.add_argument("--profile", default=None, help=f"kurgu profili (dosyadakini ezer); mevcut: {', '.join(available_profiles())}")
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
    p = sub.add_parser("review", help="taslakları incele, onayla veya reddet (teslim öncesi)")
    p.add_argument("action", choices=["list", "approve", "reject"])
    p.add_argument("job_id", nargs="?", type=int)
    p.add_argument("--clips", help="yalnızca bu kesitleri teslim et (ör. clip_01,clip_03)")
    p.add_argument("--note", help="inceleme notu")
    p.add_argument("--all", action="store_true", help="list: incelenmiş olanları da göster")
    p = sub.add_parser("doctor", help="kurulumu kontrol et")
    p.add_argument("--check-model", action="store_true", help="Whisper modelini yüklemeyi dene (ilk seferde indirir)")
    args = parser.parse_args(argv)

    cfg_path = args.config or ("config.toml" if Path("config.toml").exists() else None)
    try:
        cfg = load_config(cfg_path, profile=args.profile)
    except (OSError, ValueError) as exc:
        print(exc if isinstance(exc, ConfigError) else f"Yapılandırma hatası: {exc}", file=sys.stderr)
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
    if args.cmd == "review" and args.action != "list" and args.job_id is None:
        parser.error("review approve/reject için iş numarası verin")
    handlers = {"watch": cmd_watch, "process": cmd_process, "status": cmd_status, "retry": cmd_retry,
                "review": cmd_review, "doctor": cmd_doctor}
    try:
        return handlers[args.cmd](cfg, args)
    except ValueError as exc:
        log.error("%s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
