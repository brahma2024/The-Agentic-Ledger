"""Audio generation engine using OpenAI TTS and pydub."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from openai import OpenAI
from pydub import AudioSegment

from .config import settings
from .script_parser import DialogueLine
from .utils import get_logger, openai_retry

logger = get_logger(__name__)


@dataclass
class AudioSegmentInfo:
    """Information about a generated audio segment."""

    speaker: str
    text: str
    mood: str
    start_ms: int
    end_ms: int
    duration_ms: int
    file_path: Path


# Voice mapping for speakers
VOICE_MAP = {
    "QUANT": settings.quant_voice,  # "echo"
    "HUSTLER": settings.hustler_voice,  # "fable"
}

# Mood to TTS instruction mapping
MOOD_INSTRUCTIONS = {
    "excited": "Speak with high energy and enthusiasm, slightly faster pace",
    "sarcastic": "Speak with dry, ironic delivery and subtle eye-roll tone",
    "laughing": "Speak while chuckling, amused and light-hearted",
    "serious": "Speak in a straight, informative, matter-of-fact tone",
    "frustrated": "Speak with exasperation, slightly annoyed but not angry",
    "curious": "Speak with genuine interest and wonder, slightly questioning",
    "impressed": "Speak with genuine surprise and appreciation",
    "deadpan": "Speak completely flat and emotionless for comedic effect",
    "cheerful": "Speak with warmth and positivity",
    "neutral": "Speak in a natural, conversational tone",
    "analytical": "Speak with precision and measured pace, like presenting data",
    "skeptical": "Speak with doubt and questioning tone, slightly slower",
    "confident": "Speak with assurance and conviction, steady pace",
    "urgent": "Speak with time pressure, slightly faster, emphasize key words",
}


class AudioEngine:
    """Generates podcast audio from dialogue lines."""

    def __init__(
        self,
        client: OpenAI | None = None,
        temp_dir: Path | None = None,
    ):
        """
        Initialize the audio engine.

        Args:
            client: OpenAI client instance
            temp_dir: Directory for temporary audio files
        """
        self.client = client or OpenAI(api_key=settings.openai_api_key)
        self.temp_dir = temp_dir or settings.temp_dir
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    @openai_retry
    def _generate_voice_segment(
        self,
        text: str,
        voice: str,
        mood: str,
        output_path: Path,
    ) -> AudioSegment:
        """
        Generate a single voice segment using TTS.

        Args:
            text: Text to speak
            voice: Voice name (onyx, nova, etc.)
            mood: Mood for TTS instructions
            output_path: Path to save the audio file

        Returns:
            AudioSegment of the generated speech
        """
        instruction = MOOD_INSTRUCTIONS.get(mood, MOOD_INSTRUCTIONS["neutral"])

        logger.debug(f"Generating TTS: voice={voice}, mood={mood}, text={text[:50]}...")

        response = self.client.audio.speech.create(
            model=settings.tts_model,
            voice=voice,
            input=text,
            instructions=instruction,
        )

        # Save to file
        response.stream_to_file(str(output_path))

        # Load and return as AudioSegment
        return AudioSegment.from_mp3(str(output_path))

    def generate_podcast(
        self,
        lines: list[DialogueLine],
        bg_music_path: Path | None = None,
        output_path: Path | None = None,
    ) -> tuple[Path, list[AudioSegmentInfo]]:
        """
        Generate complete podcast audio from dialogue lines.

        Args:
            lines: List of DialogueLine objects
            bg_music_path: Optional path to background music
            output_path: Output path for final audio

        Returns:
            Tuple of (output_path, list of AudioSegmentInfo for timing)
        """
        logger.info(f"Generating podcast audio from {len(lines)} lines")

        if not lines:
            raise ValueError("No dialogue lines provided")

        output_path = output_path or settings.output_dir / "daily_podcast.mp3"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        segments: list[AudioSegment] = []
        segment_infos: list[AudioSegmentInfo] = []
        current_time_ms = 0

        # Generate each dialogue segment
        for i, line in enumerate(lines):
            voice = VOICE_MAP.get(line.speaker, "alloy")
            temp_file = self.temp_dir / f"segment_{i:03d}_{line.speaker.lower()}.mp3"

            logger.info(f"Generating segment {i+1}/{len(lines)}: [{line.speaker}:{line.mood}]")

            segment = self._generate_voice_segment(
                text=line.text,
                voice=voice,
                mood=line.mood,
                output_path=temp_file,
            )

            # Track timing info
            segment_duration = len(segment)
            segment_infos.append(
                AudioSegmentInfo(
                    speaker=line.speaker,
                    text=line.text,
                    mood=line.mood,
                    start_ms=current_time_ms,
                    end_ms=current_time_ms + segment_duration,
                    duration_ms=segment_duration,
                    file_path=temp_file,
                )
            )

            segments.append(segment)
            # Account for crossfade overlap
            if i < len(lines) - 1:
                current_time_ms += segment_duration - settings.crossfade_ms
            else:
                current_time_ms += segment_duration

        # Stitch segments with crossfade
        podcast = self._stitch_segments(segments)
        logger.info(f"Total podcast duration: {len(podcast) / 1000:.1f}s")

        # Add background music if available
        if bg_music_path and bg_music_path.exists():
            podcast = self._add_background_music(podcast, bg_music_path)
        elif settings.lofi_music_path.exists():
            podcast = self._add_background_music(podcast, settings.lofi_music_path)
        else:
            logger.warning("No background music found, exporting without music")

        # Export final audio
        podcast.export(str(output_path), format="mp3", bitrate="192k")
        logger.info(f"Exported podcast to: {output_path}")

        return output_path, segment_infos

    def _stitch_segments(self, segments: list[AudioSegment]) -> AudioSegment:
        """
        Stitch audio segments together with crossfade.

        Args:
            segments: List of AudioSegment objects

        Returns:
            Combined AudioSegment
        """
        if not segments:
            return AudioSegment.empty()

        if len(segments) == 1:
            return segments[0]

        # Start with first segment
        combined = segments[0]

        # Append remaining segments with crossfade
        for segment in segments[1:]:
            combined = combined.append(segment, crossfade=settings.crossfade_ms)

        return combined

    def _add_background_music(
        self,
        podcast: AudioSegment,
        music_path: Path,
    ) -> AudioSegment:
        """
        Add background music with intro jingle fade.

        The intro segment plays at a moderate volume for energy,
        then crossfades into a barely-audible background level.

        Args:
            podcast: Main podcast audio
            music_path: Path to background music file

        Returns:
            Podcast with background music
        """
        logger.info(f"Adding background music from: {music_path}")

        try:
            music = AudioSegment.from_mp3(str(music_path))
            podcast_length = len(podcast)

            # Loop music to match podcast length
            if len(music) < podcast_length:
                loops_needed = (podcast_length // len(music)) + 1
                music = music * loops_needed

            # Trim to podcast length
            music = music[:podcast_length]

            intro_dur = settings.bg_music_intro_duration_ms
            fade_dur = settings.bg_music_fade_duration_ms

            if podcast_length > intro_dur + fade_dur:
                # Split into intro and background portions
                intro_segment = music[:intro_dur]
                background_segment = music[intro_dur:]

                # Apply volume levels
                intro_segment = intro_segment + settings.bg_music_intro_volume_db
                background_segment = background_segment + settings.bg_music_background_volume_db

                # Crossfade between intro and background
                intro_segment = intro_segment.fade_out(fade_dur)
                background_segment = background_segment.fade_in(fade_dur)
                music = intro_segment.append(background_segment, crossfade=fade_dur)
            else:
                # Podcast too short for intro/background split — use intro volume
                music = music + settings.bg_music_intro_volume_db

            # Trim again in case crossfade changed length
            music = music[:podcast_length]

            # Fade out at the end
            end_fade = min(fade_dur, podcast_length // 4)
            music = music.fade_out(end_fade)

            return podcast.overlay(music)

        except Exception as e:
            logger.error(f"Failed to add background music: {e}")
            return podcast

    def generate_sample(
        self,
        output_path: Path | None = None,
    ) -> Path:
        """
        Generate a sample podcast with test dialogue.

        Args:
            output_path: Output path for sample audio

        Returns:
            Path to generated sample
        """
        sample_lines = [
            DialogueLine(
                speaker="QUANT",
                text="The correlation between Fed announcements and crypto volatility just hit 0.87.",
                mood="analytical",
            ),
            DialogueLine(
                speaker="HUSTLER",
                text="In English - when Powell talks, Bitcoin moves. And I've got a trade for that.",
                mood="confident",
            ),
        ]

        output_path = output_path or settings.output_dir / "sample_podcast.mp3"
        path, _ = self.generate_podcast(sample_lines, output_path=output_path)
        return path

    def cleanup_temp_files(self) -> None:
        """Remove temporary audio segment files."""
        for file in self.temp_dir.glob("segment_*.mp3"):
            try:
                file.unlink()
            except Exception as e:
                logger.warning(f"Failed to delete temp file {file}: {e}")
