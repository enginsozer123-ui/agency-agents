"""Sahne geçişi tespiti (PySceneDetect, ContentDetector)."""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("umbrella_vea.scenes")


def detect_scene_cuts(path: Path, threshold: float) -> list[float]:
    """Sahne geçişlerinin zamanlarını (saniye) döndürür. İlk sahnenin başı (0) dahil değildir."""
    from scenedetect import ContentDetector, detect

    scenes = detect(str(path), ContentDetector(threshold=threshold), show_progress=False)
    cuts = [round(float(start.seconds), 3) for start, _end in scenes[1:]]
    log.debug("%d sahne geçişi bulundu", len(cuts))
    return cuts
