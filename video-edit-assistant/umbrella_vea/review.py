"""Teslim öncesi inceleme. Profil inceleme istiyorsa taslaklar onaylanmadan teslim klasörüne kopyalanmaz.

Teslim = çıktı klasöründeki `delivery/` alt klasörüne kopyalama. Hiçbir platforma yükleme yapılmaz.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from .config import Config


class ReviewError(RuntimeError):
    pass


def load_report(out_dir: Path) -> dict:
    path = out_dir / "report.json"
    if not path.is_file():
        raise ReviewError(f"Rapor bulunamadı: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _save_report(out_dir: Path, report: dict) -> None:
    tmp = out_dir / "report.json.tmp"
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(out_dir / "report.json")


def deliver(out_dir: Path, report: dict, clip_ids: list[str], cfg: Config) -> list[str]:
    """Seçilen başarılı kesitleri (mp4 + varsa srt) delivery/ klasörüne kopyalar; kopyalananları döndürür."""
    ok = {r["candidate_id"]: r for r in report.get("renders", []) if r.get("status") == "ok"}
    unknown = [c for c in clip_ids if c not in ok]
    if unknown:
        raise ReviewError(f"Teslim edilemeyen kesit(ler): {', '.join(unknown)} (yok veya render başarısız)")
    dest = out_dir / cfg.delivery.delivery_dir_name
    dest.mkdir(exist_ok=True)
    delivered = []
    for cid in clip_ids:
        r = ok[cid]
        for key in ("file", "srt"):
            if r.get(key):
                shutil.copy2(out_dir / r[key], dest / Path(r[key]).name)
        delivered.append(cid)
    return delivered


def decide(out_dir: Path, cfg: Config, approve: bool, clip_ids: list[str] | None = None, note: str = "") -> dict:
    report = load_report(out_dir)
    if report.get("status") != "done":
        raise ReviewError(f"İş tamamlanmamış (durum: {report.get('status')}); önce sorunları giderin.")
    review = report.setdefault("review", {"required": True})
    if review.get("status") in ("approved", "rejected"):
        raise ReviewError(f"Bu iş zaten incelenmiş (durum: {review['status']}).")
    all_ok = [r["candidate_id"] for r in report.get("renders", []) if r.get("status") == "ok"]
    review.update({"status": "approved" if approve else "rejected",
                   "decided_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "note": note})
    if approve:
        review["delivered"] = deliver(out_dir, report, clip_ids or all_ok, cfg)
        review["delivery_dir"] = cfg.delivery.delivery_dir_name
    else:
        review["delivered"] = []
    _save_report(out_dir, report)
    return review
