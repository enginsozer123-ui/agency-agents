"""İsteğe bağlı bulut modülü: kesit adaylarını transkript METNİNE göre anlamsal olarak puanlar.

Gizlilik kuralları:
- Video ve ses ASLA gönderilmez; bu modülde medya yükleyen bir kod yolu yoktur.
- Yalnızca transkript metni ve aday zaman aralıkları gönderilir.
- Çalışması için hem `cloud.enabled = true` hem `cloud.consent_send_transcript = true` gerekir,
  ayrıca ANTHROPIC_API_KEY ortamda (.env) tanımlı olmalıdır. Biri eksikse yerel mod sürer.
- API anahtarı loglanmaz ve rapora yazılmaz.
"""

from __future__ import annotations

import json
import logging
import os

from ..config import CloudConfig

log = logging.getLogger("umbrella_vea.cloud")

SYSTEM_PROMPT = (
    "Sen kısa sosyal medya videoları (Instagram Reels / YouTube Shorts) için bir video editörü asistanısın. "
    "Sana Türkçe bir konuşmanın transkriptinden alınmış kesit adayları verilecek. Her adayı yalnızca verilen "
    "metne dayanarak 0-1 arasında puanla: tek başına anlaşılır mı, güçlü bir giriş cümlesi var mı, net bir "
    "fikir/sonuç içeriyor mu. Metinde olmayan bir bağlam, iddia veya konuşma uydurma; emin değilsen düşük puan "
    "ver ve gerekçede belirt. Gerekçeyi Türkçe ve en fazla iki cümle yaz."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "scores": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "score": {"type": "number"},
                    "reason": {"type": "string"},
                },
                "required": ["id", "score", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["scores"],
    "additionalProperties": False,
}


def availability(cfg: CloudConfig) -> tuple[bool, str]:
    """(kullanılabilir_mi, neden). Neden metni rapora yazılır; gizli değer içermez."""
    if not cfg.enabled:
        return False, "Bulut modülü kapalı (cloud.enabled = false); yerel mod kullanıldı."
    if not cfg.consent_send_transcript:
        return False, "Transkriptin buluta gönderilmesi için onay verilmedi (cloud.consent_send_transcript = false)."
    if cfg.provider != "anthropic":
        return False, f"Desteklenmeyen sağlayıcı: {cfg.provider}"
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False, "ANTHROPIC_API_KEY tanımlı değil (.env); yerel mod kullanıldı."
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False, "anthropic paketi kurulu değil (pip install -r requirements-cloud.txt)."
    return True, "Bulut puanlama etkin (yalnızca transkript metni gönderildi)."


def build_payload(candidates: list[dict], max_chars: int) -> str:
    items, used = [], 0
    for c in candidates:
        text = c.get("text", "")
        if used + len(text) > max_chars:
            break
        used += len(text)
        items.append({"id": c["id"], "start": c["start"], "end": c["end"], "text": text})
    return json.dumps({"candidates": items}, ensure_ascii=False)


def score_candidates(candidates: list[dict], cfg: CloudConfig, client=None) -> dict[str, dict]:
    """{aday_id: {"score": float, "reason": str}} döndürür. Hata olursa istisna fırlatır; çağıran yerel moda döner."""
    transcript_candidates = [c for c in candidates if c.get("text")]
    if not transcript_candidates:
        return {}
    if client is None:
        import anthropic

        client = anthropic.Anthropic(timeout=cfg.timeout_sec)
    payload = build_payload(transcript_candidates, cfg.max_transcript_chars)
    log.info("Bulut puanlama: %d aday, %d karakter metin gönderiliyor (video/ses gönderilmiyor)",
             len(transcript_candidates), len(payload))
    response = client.beta.messages.create(
        model=cfg.model,
        max_tokens=4000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": payload}],
        output_config={"effort": cfg.effort, "format": {"type": "json_schema", "schema": SCHEMA}},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Model isteği reddetti (refusal); yerel puanlar kullanılacak.")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Yanıt yarıda kesildi (max_tokens); yerel puanlar kullanılacak.")
    text = next(b.text for b in response.content if b.type == "text")
    data = json.loads(text)
    known = {c["id"] for c in transcript_candidates}
    out: dict[str, dict] = {}
    for item in data.get("scores", []):
        if item["id"] in known:
            out[item["id"]] = {"score": round(max(0.0, min(1.0, float(item["score"]))), 3), "reason": item["reason"]}
    return out
