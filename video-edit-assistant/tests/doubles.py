"""Test dublörleri. Gerçek ASR değildir; yalnızca Whisper modeli indirilemeyen ortamlarda
boru hattının geri kalanını test etmek içindir. Rapor 'engine' alanında açıkça 'test-double' yazar."""

from __future__ import annotations

from pathlib import Path

from umbrella_vea.ffmpeg_tools import detect_silences
from umbrella_vea.transcribe import Segment, Transcript, Word


class ScriptedTranscriber:
    """Bilinen senaryo metnini, ses dosyasında GERÇEKTEN ölçülen konuşma aralıklarına yerleştirir."""

    def __init__(self, sentences: list[str]):
        self.sentences = sentences

    def transcribe(self, audio_path: Path, duration: float, progress=None) -> Transcript:
        silences = detect_silences(audio_path, duration, -35.0, 0.6)
        speech, cursor = [], 0.0
        for s in silences:
            if s["start"] - cursor > 0.3:
                speech.append((cursor, s["start"]))
            cursor = s["end"]
        if duration - cursor > 0.3:
            speech.append((cursor, duration))
        segments = []
        for (start, end), text in zip(speech, self.sentences):
            tokens = text.split()
            step = (end - start) / len(tokens)
            words = [Word(round(start + k * step, 3), round(start + (k + 1) * step, 3), t, 0.9) for k, t in enumerate(tokens)]
            segments.append(Segment(round(start, 3), round(end, 3), text, -0.2, 0.01, words))
            if progress:
                progress(end / duration)
        return Transcript("tr", 1.0, duration, "test-double", "scripted", segments)


class FakeCloudClient:
    """Anthropic istemcisinin yerine geçer; ağ çağrısı yapmaz, gönderilen içeriği kaydeder."""

    def __init__(self, response_text: str, stop_reason: str = "end_turn"):
        self.sent: list[dict] = []
        outer = self

        class _Block:
            type = "text"
            text = response_text

        class _Resp:
            content = [_Block()]

        _Resp.stop_reason = stop_reason

        class _Messages:
            def create(self, **kwargs):
                outer.sent.append(kwargs)
                return _Resp()

        class _Beta:
            messages = _Messages()

        self.beta = _Beta()
