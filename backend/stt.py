import os
import tempfile
from faster_whisper import WhisperModel

_model: WhisperModel | None = None


def get_model() -> WhisperModel:
    global _model
    if _model is None:
        # "base" balances speed and accuracy; swap for "small" if accuracy matters more.
        # Requires ffmpeg on PATH for webm/opus decoding (winget install Gyan.FFmpeg).
        _model = WhisperModel("base", device="cpu", compute_type="int8")
    return _model


def transcribe(audio_bytes: bytes, mime_type: str = "audio/webm") -> str:
    suffix = ".webm" if "webm" in mime_type else ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(audio_bytes)
        tmp_path = f.name
    try:
        segments, _ = get_model().transcribe(tmp_path, beam_size=5)
        return " ".join(seg.text.strip() for seg in segments).strip()
    finally:
        os.unlink(tmp_path)
