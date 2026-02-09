"""Parser for podcast scripts with speaker tags."""

import re
from dataclasses import dataclass
from typing import Literal

from .utils import get_logger

logger = get_logger(__name__)

# Pattern to match [SPEAKER:mood] or [SPEAKER] followed by text
# Captures: (speaker, optional_mood, dialogue_text)
DIALOGUE_PATTERN = re.compile(
    r"\[(QUANT|HUSTLER)(?::(\w+))?\]\s*(.+?)(?=\[(?:QUANT|HUSTLER)|$)",
    re.DOTALL | re.IGNORECASE,
)

# Valid moods for TTS instructions
VALID_MOODS = {
    "excited",
    "sarcastic",
    "laughing",
    "serious",
    "frustrated",
    "curious",
    "impressed",
    "deadpan",
    "cheerful",
    "neutral",
    "analytical",
    "skeptical",
    "confident",
    "urgent",
}

# Default moods for each speaker
DEFAULT_MOODS = {
    "QUANT": "analytical",
    "HUSTLER": "excited",
}

Speaker = Literal["QUANT", "HUSTLER"]


@dataclass
class DialogueLine:
    """Represents a single line of dialogue."""

    speaker: Speaker
    text: str
    mood: str
    estimated_duration_seconds: float = 0.0

    def __post_init__(self):
        """Validate and normalize the dialogue line."""
        self.speaker = self.speaker.upper()
        self.mood = self.mood.lower() if self.mood else DEFAULT_MOODS.get(self.speaker, "neutral")
        self.text = self.text.strip()

        # Validate mood
        if self.mood not in VALID_MOODS:
            logger.warning(f"Unknown mood '{self.mood}', using default")
            self.mood = DEFAULT_MOODS.get(self.speaker, "neutral")

        # Estimate duration (rough: ~150 words per minute = 2.5 words per second)
        word_count = len(self.text.split())
        self.estimated_duration_seconds = word_count / 2.5


class ScriptParser:
    """Parses podcast scripts into structured dialogue lines."""

    # Approximate speaking rate: words per second
    WORDS_PER_SECOND = 2.5

    def parse(self, script: str) -> list[DialogueLine]:
        """
        Parse a script into dialogue lines.

        Args:
            script: Raw script text with [SPEAKER:mood] tags

        Returns:
            List of DialogueLine objects
        """
        logger.info("Parsing script...")

        lines = []
        matches = DIALOGUE_PATTERN.findall(script)

        if not matches:
            logger.error("No dialogue patterns found in script")
            raise ValueError("Script does not contain valid [SPEAKER:mood] tags")

        for speaker, mood, text in matches:
            # Clean up the text
            text = text.strip()
            text = re.sub(r"\s+", " ", text)  # Normalize whitespace

            if not text:
                continue

            line = DialogueLine(
                speaker=speaker.upper(),
                text=text,
                mood=mood.lower() if mood else None,
            )
            lines.append(line)

        logger.info(f"Parsed {len(lines)} dialogue lines")
        self._log_summary(lines)

        return lines

    def _log_summary(self, lines: list[DialogueLine]) -> None:
        """Log a summary of the parsed script."""
        quant_count = sum(1 for line in lines if line.speaker == "QUANT")
        hustler_count = sum(1 for line in lines if line.speaker == "HUSTLER")
        total_duration = sum(line.estimated_duration_seconds for line in lines)
        total_words = sum(len(line.text.split()) for line in lines)

        logger.info(
            f"Script summary: QUANT={quant_count} lines, HUSTLER={hustler_count} lines, "
            f"~{total_words} words, ~{total_duration:.1f}s estimated"
        )

    def estimate_duration(self, lines: list[DialogueLine]) -> float:
        """
        Estimate total duration of the script.

        Args:
            lines: List of DialogueLine objects

        Returns:
            Estimated duration in seconds
        """
        return sum(line.estimated_duration_seconds for line in lines)

    def validate_duration(
        self, lines: list[DialogueLine], max_seconds: float = 59
    ) -> bool:
        """
        Check if script duration is within limits.

        Args:
            lines: List of DialogueLine objects
            max_seconds: Maximum allowed duration

        Returns:
            True if within limits, False otherwise
        """
        estimated = self.estimate_duration(lines)

        if estimated > max_seconds:
            logger.warning(
                f"Script too long: ~{estimated:.1f}s (max {max_seconds}s)"
            )
            return False

        return True

    def get_word_count(self, lines: list[DialogueLine]) -> int:
        """Get total word count of all dialogue lines."""
        return sum(len(line.text.split()) for line in lines)

    def format_for_display(self, lines: list[DialogueLine]) -> str:
        """Format dialogue lines for human-readable display."""
        output = []
        for line in lines:
            output.append(f"[{line.speaker}:{line.mood}] {line.text}")
        return "\n".join(output)
