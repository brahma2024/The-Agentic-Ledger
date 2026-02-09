"""Whisper-based word-level transcription for karaoke subtitles."""

from dataclasses import dataclass
from pathlib import Path

from openai import OpenAI

from .config import settings
from .utils import get_logger, openai_retry

logger = get_logger(__name__)


@dataclass
class WordTimestamp:
    """A single word with timing relative to segment start."""

    word: str
    start_s: float
    end_s: float

    @property
    def duration_cs(self) -> int:
        """Duration in centiseconds (for ASS \\kf tags)."""
        return max(1, round((self.end_s - self.start_s) * 100))


@dataclass
class SegmentTranscription:
    """Whisper transcription result for one audio segment."""

    speaker: str
    segment_start_ms: int
    words: list[WordTimestamp]

    @property
    def text(self) -> str:
        return " ".join(w.word for w in self.words)


class WhisperTranscriber:
    """Transcribes audio segments via OpenAI Whisper for word-level timestamps."""

    def __init__(self, client: OpenAI | None = None):
        self.client = client or OpenAI(api_key=settings.openai_api_key)

    def transcribe_all(self, segment_infos) -> list[SegmentTranscription]:
        """
        Transcribe all audio segments for word-level timestamps.

        Args:
            segment_infos: List of AudioSegmentInfo from audio engine

        Returns:
            List of SegmentTranscription with word timestamps
        """
        results = []
        for seg in segment_infos:
            if not seg.file_path.exists():
                logger.warning(f"Segment file missing: {seg.file_path}, skipping")
                continue
            try:
                transcription = self._transcribe_segment(seg)
                results.append(transcription)
            except Exception as e:
                logger.warning(f"Whisper failed for segment '{seg.text[:30]}...': {e}")
                continue

        logger.info(f"Whisper transcribed {len(results)}/{len(segment_infos)} segments")
        return results

    @openai_retry
    def _transcribe_segment(self, seg) -> SegmentTranscription:
        """Transcribe a single audio segment via Whisper API."""
        with open(seg.file_path, "rb") as audio_file:
            response = self.client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                response_format="verbose_json",
                timestamp_granularities=["word"],
            )

        words = []
        for w in getattr(response, "words", []) or []:
            words.append(WordTimestamp(
                word=w.word.strip(),
                start_s=w.start,
                end_s=w.end,
            ))

        return SegmentTranscription(
            speaker=seg.speaker,
            segment_start_ms=seg.start_ms,
            words=words,
        )
