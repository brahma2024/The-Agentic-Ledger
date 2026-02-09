"""ASS subtitle generation with karaoke word-by-word highlighting."""

import re
from pathlib import Path
from typing import Optional

from .audio_engine import AudioSegmentInfo
from .config import settings
from .utils import get_logger

logger = get_logger(__name__)

# ASS file header template — karaoke-ready styles
# BorderStyle 3 = opaque box, BackColour = 75% opacity dark strip
# SecondaryColour = dim "not yet spoken" color for \kf karaoke fill
ASS_HEADER = """[Script Info]
Title: The Agentic Ledger
ScriptType: v4.00+
WrapStyle: 0
PlayResX: {width}
PlayResY: {height}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Quant,{font},{fontsize},{quant_color},{quant_dim_color},&H00000000,&HC0000000,1,0,0,0,100,100,0,0,3,0,0,2,50,50,{margin_v},1
Style: Hustler,{font},{fontsize},{hustler_color},{hustler_dim_color},&H00000000,&HC0000000,1,0,0,0,100,100,0,0,3,0,0,2,50,50,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

# Max visible characters per line before wrapping
MAX_VISIBLE_CHARS_PER_LINE = 50


def ms_to_ass_time(ms: int) -> str:
    """Convert milliseconds to ASS timestamp format (H:MM:SS.cc)."""
    total_seconds = ms / 1000
    hours = int(total_seconds // 3600)
    minutes = int((total_seconds % 3600) // 60)
    seconds = total_seconds % 60
    return f"{hours}:{minutes:02d}:{seconds:05.2f}"


def _strip_kf_tags(text: str) -> str:
    """Remove {\\kf...} tags to get visible text only."""
    return re.sub(r"\{\\kf\d+\}", "", text)


class SubtitleGenerator:
    """Generates ASS format subtitles with optional karaoke highlighting."""

    def __init__(self):
        self.width = settings.video_width
        self.height = settings.video_height
        self.font = settings.subtitle_font
        self.font_size = settings.subtitle_font_size
        self.margin_v = settings.subtitle_margin_v
        self.quant_color = settings.quant_color
        self.hustler_color = settings.hustler_color
        self.quant_dim_color = settings.quant_dim_color
        self.hustler_dim_color = settings.hustler_dim_color

    def generate(
        self,
        segment_infos: list[AudioSegmentInfo],
        output_path: Path | None = None,
        transcriptions: Optional[list] = None,
    ) -> Path:
        """
        Generate ASS subtitle file.

        If transcriptions are provided, generates karaoke subtitles with
        word-by-word \\kf highlighting. Otherwise falls back to segment-level
        subtitles (still uses the new smaller font and bottom positioning).

        Args:
            segment_infos: List of AudioSegmentInfo with timing data
            output_path: Path to save the subtitle file
            transcriptions: Optional list of SegmentTranscription from Whisper

        Returns:
            Path to generated subtitle file
        """
        logger.info(f"Generating subtitles for {len(segment_infos)} segments")

        output_path = output_path or settings.output_dir / "subtitles.ass"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        header = ASS_HEADER.format(
            width=self.width,
            height=self.height,
            font=self.font,
            fontsize=self.font_size,
            quant_color=self.quant_color,
            hustler_color=self.hustler_color,
            quant_dim_color=self.quant_dim_color,
            hustler_dim_color=self.hustler_dim_color,
            margin_v=self.margin_v,
        )

        if transcriptions:
            events = self._generate_karaoke_events(transcriptions, segment_infos)
            logger.info(f"Generated karaoke subtitles ({len(events)} events)")
        else:
            events = self._generate_segment_events(segment_infos)
            logger.info(f"Generated segment-level subtitles ({len(events)} events)")

        content = header + "\n".join(events) + "\n"
        output_path.write_text(content)

        logger.info(f"Generated subtitles: {output_path}")
        return output_path

    # ------------------------------------------------------------------
    # Karaoke path (Whisper word timestamps → \kf tags)
    # ------------------------------------------------------------------

    def _generate_karaoke_events(
        self,
        transcriptions: list,
        segment_infos: list[AudioSegmentInfo],
    ) -> list[str]:
        """Generate karaoke dialogue events from Whisper transcriptions."""
        events = []

        for trans in transcriptions:
            if not trans.words:
                continue

            style = "Quant" if trans.speaker == "QUANT" else "Hustler"
            seg_offset_ms = trans.segment_start_ms

            # Build karaoke-tagged text for this segment
            kf_words = []
            for w in trans.words:
                duration_cs = w.duration_cs
                # Escape ASS special chars in word text
                word_text = self._escape_ass(w.word)
                kf_words.append((duration_cs, word_text))

            # Wrap into lines respecting MAX_VISIBLE_CHARS_PER_LINE
            lines = self._wrap_karaoke_words(kf_words)

            # Compute segment start/end from word timestamps
            first_word_start_ms = seg_offset_ms + int(trans.words[0].start_s * 1000)
            last_word_end_ms = seg_offset_ms + int(trans.words[-1].end_s * 1000)

            start_time = ms_to_ass_time(first_word_start_ms)
            end_time = ms_to_ass_time(last_word_end_ms)

            text = "\\N".join(lines)
            events.append(
                f"Dialogue: 0,{start_time},{end_time},{style},,0,0,0,,{text}"
            )

        return events

    def _wrap_karaoke_words(
        self, kf_words: list[tuple[int, str]]
    ) -> list[str]:
        """
        Wrap karaoke-tagged words into lines.

        Only counts visible characters (not {\\kf...} tags) toward the
        line-length limit.

        Args:
            kf_words: List of (duration_cs, word_text) tuples

        Returns:
            List of line strings with {\\kf} tags and space separators
        """
        lines = []
        current_line_parts: list[str] = []
        current_visible_len = 0

        for duration_cs, word_text in kf_words:
            word_visible_len = len(word_text)
            # +1 for the space between words (if not first word on line)
            needed = word_visible_len + (1 if current_line_parts else 0)

            if current_visible_len + needed > MAX_VISIBLE_CHARS_PER_LINE and current_line_parts:
                lines.append(" ".join(current_line_parts))
                current_line_parts = []
                current_visible_len = 0

            tagged = f"{{\\kf{duration_cs}}}{word_text}"
            current_line_parts.append(tagged)
            current_visible_len += word_visible_len + (1 if len(current_line_parts) > 1 else 0)

        if current_line_parts:
            lines.append(" ".join(current_line_parts))

        return lines

    # ------------------------------------------------------------------
    # Segment-level fallback (no Whisper)
    # ------------------------------------------------------------------

    def _generate_segment_events(
        self, segment_infos: list[AudioSegmentInfo]
    ) -> list[str]:
        """Generate segment-level dialogue events (no word highlighting)."""
        events = []
        for segment in segment_infos:
            start_time = ms_to_ass_time(segment.start_ms)
            end_time = ms_to_ass_time(segment.end_ms)
            style = "Quant" if segment.speaker == "QUANT" else "Hustler"
            text = self._format_text(segment.text)
            events.append(
                f"Dialogue: 0,{start_time},{end_time},{style},,0,0,0,,{text}"
            )
        return events

    # ------------------------------------------------------------------
    # Text helpers
    # ------------------------------------------------------------------

    def _escape_ass(self, text: str) -> str:
        """Escape special ASS characters in text."""
        text = text.replace("\\", "\\\\")
        text = text.replace("{", "\\{")
        text = text.replace("}", "\\}")
        return text

    def _format_text(self, text: str) -> str:
        """Format and word-wrap plain text for ASS display."""
        text = self._escape_ass(text)
        text = text.replace("\n", "\\N")

        words = text.split()
        lines = []
        current_line: list[str] = []
        current_length = 0

        for word in words:
            if current_length + len(word) + 1 > MAX_VISIBLE_CHARS_PER_LINE and current_line:
                lines.append(" ".join(current_line))
                current_line = [word]
                current_length = len(word)
            else:
                current_line.append(word)
                current_length += len(word) + 1

        if current_line:
            lines.append(" ".join(current_line))

        return "\\N".join(lines)

    # ------------------------------------------------------------------
    # Sample / testing
    # ------------------------------------------------------------------

    def generate_sample(self, output_path: Path | None = None) -> Path:
        """Generate sample subtitles for testing."""
        sample_segments = [
            AudioSegmentInfo(
                speaker="QUANT",
                text="The correlation between Fed announcements and crypto volatility just hit 0.87.",
                mood="analytical",
                start_ms=0,
                end_ms=4000,
                duration_ms=4000,
                file_path=Path("sample.mp3"),
            ),
            AudioSegmentInfo(
                speaker="HUSTLER",
                text="In English - when Powell talks, Bitcoin moves. And I've got a trade for that.",
                mood="confident",
                start_ms=3800,
                end_ms=7500,
                duration_ms=3700,
                file_path=Path("sample.mp3"),
            ),
        ]

        output_path = output_path or settings.output_dir / "sample_subtitles.ass"
        return self.generate(sample_segments, output_path)
