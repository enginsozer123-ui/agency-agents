"""Uçtan uca test için kısa örnek video üretir.

Türkçe konuşma espeak-ng ile sentezlenir (kuruluysa); cümleler arasında sessizlik ve ortada bir
sahne geçişi (renk değişimi) bulunur. Yatay 1280x720 video üretilir; 9:16 kırpma böylece test edilir.

Kullanım: python tests/make_sample.py çıktı.mp4
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SENTENCES = [
    "Merhaba, bugün size kısa videoların en önemli sırrını anlatacağım.",
    "Peki neden ilk üç saniye bu kadar önemli?",
    "Çünkü izleyici kaydırıp kaydırmayacağına hemen karar verir.",
    "Bu yüzden kesinlikle güçlü bir cümleyle başlayın.",
]
PAUSE_SEC = 1.2


def has_tts() -> bool:
    return shutil.which("espeak-ng") is not None


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, capture_output=True)


def make_sample(dest: Path, speech: bool = True) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmpd = Path(tmp)
        audio = tmpd / "speech.wav"
        if speech and has_tts():
            parts = []
            for i, s in enumerate(SENTENCES):
                w = tmpd / f"s{i}.wav"
                _run(["espeak-ng", "-v", "tr", "-s", "150", "-w", str(w), s])
                parts.append(w)
            silence = tmpd / "sil.wav"
            _run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r=22050:cl=mono", "-t", str(PAUSE_SEC), str(silence)])
            listfile = tmpd / "list.txt"
            seq = [silence]
            for p in parts:
                seq += [p, silence]
            listfile.write_text("".join(f"file '{p}'\n" for p in seq))
            _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listfile), "-ar", "44100", "-ac", "1", str(audio)])
        else:
            _run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=12", "-ar", "44100", str(audio)])
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(audio)],
                                   capture_output=True, text=True, check=True).stdout.strip())
        half = dur / 2
        _run([
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=c=0x8B0000:s=1280x720:r=25:d={half:.3f}",
            "-f", "lavfi", "-i", f"color=c=0x00308F:s=1280x720:r=25:d={dur - half:.3f}",
            "-i", str(audio),
            "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(dest),
        ])
    return dest


if __name__ == "__main__":
    out = make_sample(Path(sys.argv[1] if len(sys.argv) > 1 else "sample_tr.mp4"))
    print(out)
