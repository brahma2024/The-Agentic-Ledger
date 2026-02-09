"""FFmpeg-based video rendering for YouTube (16:9 landscape)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from .config import settings
from .screenshot_capture import ScreenshotPair
from .utils import get_logger

if TYPE_CHECKING:
    from .card_renderer import RenderedCard

logger = get_logger(__name__)


class VideoRenderer:
    """Renders 16:9 landscape videos with audio and subtitles using FFmpeg."""

    def __init__(self):
        """Initialize the video renderer."""
        self.width = settings.video_width
        self.height = settings.video_height
        self.max_duration = settings.max_duration_seconds
        self.bg_color = settings.background_color

    def render(
        self,
        audio_path: Path,
        subtitle_path: Path,
        output_path: Path | None = None,
        background_video_path: Path | None = None,
        screenshot_pair: ScreenshotPair | None = None,
        scene_cards: list[RenderedCard] | None = None,
    ) -> Path:
        """
        Render final video with audio and subtitles.

        Priority: scene_cards > screenshots > background video > solid color.

        Args:
            audio_path: Path to podcast audio file
            subtitle_path: Path to ASS subtitle file
            output_path: Output path for final video
            background_video_path: Optional looping background video
            screenshot_pair: Optional pair of webpage screenshots
            scene_cards: Optional list of rendered scene card images

        Returns:
            Path to rendered video
        """
        output_path = output_path or settings.output_dir / "output.mp4"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Priority 1: Scene cards (visual storyboard)
        if scene_cards and len(scene_cards) >= 2:
            try:
                logger.info("Using scene card storyboard")
                return self._render_with_scene_cards(
                    scene_cards, audio_path, subtitle_path, output_path
                )
            except Exception as e:
                logger.warning(f"Scene card rendering failed: {e}, falling back")

        # Priority 2: Screenshots
        if screenshot_pair and (screenshot_pair.news_screenshot or screenshot_pair.paper_screenshot):
            logger.info("Using screenshot background")
            return self._render_with_screenshot_background(
                audio_path, subtitle_path, screenshot_pair, output_path
            )

        # Priority 3: Background video
        if background_video_path is None:
            background_video_path = settings.background_video_path

        if background_video_path.exists():
            logger.info("Using video background")
            return self._render_with_video_background(
                audio_path, subtitle_path, background_video_path, output_path
            )

        # Priority 4: Solid color
        logger.info("Using solid color background")
        return self._render_with_solid_background(
            audio_path, subtitle_path, output_path
        )

    def _render_with_scene_cards(
        self,
        rendered_cards: list[RenderedCard],
        audio_path: Path,
        subtitle_path: Path,
        output_path: Path,
    ) -> Path:
        """Render with scene card PNGs using FFmpeg xfade chain."""
        xf_dur = 0.5  # crossfade duration
        n = len(rendered_cards)

        # Build input arguments: each card looped for its duration + xfade padding
        inputs = []
        for card in rendered_cards:
            dur = card.duration_s + xf_dur
            inputs.extend(["-loop", "1", "-t", str(dur), "-i", str(card.image_path)])
        inputs.extend(["-i", str(audio_path)])

        audio_idx = n  # index of the audio input

        # Build xfade filter chain
        if n == 1:
            # Single card, no xfade needed
            filter_str = (
                f"[0:v]scale={self.width}:{self.height},"
                f"subtitles={self._escape_path(subtitle_path)}[outv]"
            )
        else:
            # Scale all inputs
            scale_parts = []
            for i in range(n):
                scale_parts.append(
                    f"[{i}:v]scale={self.width}:{self.height}[v{i}]"
                )

            # Chain xfades
            xfade_parts = []
            # offset[0] = card[0].duration - xf_dur
            offset = rendered_cards[0].duration_s - xf_dur
            prev_label = "v0"

            for i in range(1, n):
                out_label = f"xf{i - 1}" if i < n - 1 else "xfout"
                xfade_parts.append(
                    f"[{prev_label}][v{i}]xfade=transition=fade:duration={xf_dur}:offset={offset:.3f}[{out_label}]"
                )
                if i < n - 1:
                    offset += rendered_cards[i].duration_s - xf_dur
                prev_label = out_label

            # Subtitle overlay on final output
            subtitle_part = f"[xfout]subtitles={self._escape_path(subtitle_path)}[outv]"

            filter_str = ";".join(scale_parts + xfade_parts + [subtitle_part])

        cmd = [
            "ffmpeg", "-y",
            *inputs,
            "-filter_complex", filter_str,
            "-map", "[outv]",
            "-map", f"{audio_idx}:a",
            "-c:v", "libx264", "-preset", "medium", "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-profile:v", "main", "-level", "4.0",
            "-movflags", "+faststart",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest", "-t", str(self.max_duration),
            str(output_path),
        ]

        return self._run_ffmpeg(cmd, output_path)

    def _render_with_screenshot_background(
        self,
        audio_path: Path,
        subtitle_path: Path,
        screenshot_pair: ScreenshotPair,
        output_path: Path,
    ) -> Path:
        """Render with screenshot images as background (news first half, paper second half)."""
        duration = self._get_audio_duration(audio_path)
        duration = min(duration, self.max_duration)

        news = screenshot_pair.news_screenshot
        paper = screenshot_pair.paper_screenshot

        if news and paper and news.exists() and paper.exists():
            # Both screenshots: news for first half, paper for second half with crossfade
            midpoint = duration / 2
            xfade_dur = min(1.0, midpoint / 2)
            cmd = [
                "ffmpeg", "-y",
                "-loop", "1", "-t", str(midpoint + xfade_dur), "-i", str(news),
                "-loop", "1", "-t", str(duration - midpoint + xfade_dur), "-i", str(paper),
                "-i", str(audio_path),
                "-filter_complex",
                f"[0:v]scale={self.width}:{self.height}:force_original_aspect_ratio=increase,"
                f"crop={self.width}:{self.height}[v0];"
                f"[1:v]scale={self.width}:{self.height}:force_original_aspect_ratio=increase,"
                f"crop={self.width}:{self.height}[v1];"
                f"[v0][v1]xfade=transition=fade:duration={xfade_dur}:offset={midpoint},"
                f"subtitles={self._escape_path(subtitle_path)}[outv]",
                "-map", "[outv]", "-map", "2:a",
                "-c:v", "libx264", "-preset", "medium", "-crf", "23",
                "-pix_fmt", "yuv420p",
                "-profile:v", "main", "-level", "4.0",
                "-movflags", "+faststart",
                "-c:a", "aac", "-b:a", "192k",
                "-shortest", "-t", str(self.max_duration),
                str(output_path),
            ]
        else:
            # Only one screenshot available — use it for full duration
            single = news if (news and news.exists()) else paper
            cmd = [
                "ffmpeg", "-y",
                "-loop", "1", "-i", str(single),
                "-i", str(audio_path),
                "-filter_complex",
                f"[0:v]scale={self.width}:{self.height}:force_original_aspect_ratio=increase,"
                f"crop={self.width}:{self.height},"
                f"subtitles={self._escape_path(subtitle_path)}[outv]",
                "-map", "[outv]", "-map", "1:a",
                "-c:v", "libx264", "-preset", "medium", "-crf", "23",
                "-pix_fmt", "yuv420p",
                "-profile:v", "main", "-level", "4.0",
                "-movflags", "+faststart",
                "-c:a", "aac", "-b:a", "192k",
                "-shortest", "-t", str(self.max_duration),
                str(output_path),
            ]

        return self._run_ffmpeg(cmd, output_path)

    def _render_with_video_background(
        self,
        audio_path: Path,
        subtitle_path: Path,
        background_path: Path,
        output_path: Path,
    ) -> Path:
        """Render with looping video background."""
        # FFmpeg command for video background
        # -stream_loop -1: loop the background video indefinitely
        # -shortest: stop when the shortest input (audio) ends
        # -vf "subtitles=...": burn in ASS subtitles using libass
        cmd = [
            "ffmpeg",
            "-y",  # Overwrite output
            "-stream_loop", "-1",  # Loop background video
            "-i", str(background_path),  # Background video input
            "-i", str(audio_path),  # Audio input
            "-filter_complex",
            f"[0:v]scale={self.width}:{self.height}:force_original_aspect_ratio=increase,"
            f"crop={self.width}:{self.height},"
            f"subtitles={self._escape_path(subtitle_path)}[outv]",
            "-map", "[outv]",  # Use filtered video
            "-map", "1:a",  # Use audio from second input
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-profile:v", "main", "-level", "4.0",
            "-movflags", "+faststart",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",  # End when audio ends
            "-t", str(self.max_duration),  # Max duration limit
            str(output_path),
        ]

        return self._run_ffmpeg(cmd, output_path)

    def _render_with_solid_background(
        self,
        audio_path: Path,
        subtitle_path: Path,
        output_path: Path,
    ) -> Path:
        """Render with solid color background."""
        # Get audio duration first
        duration = self._get_audio_duration(audio_path)
        duration = min(duration, self.max_duration)

        # FFmpeg command for solid background
        # Use color source for background
        cmd = [
            "ffmpeg",
            "-y",  # Overwrite output
            "-f", "lavfi",
            "-i", f"color=c={self.bg_color.replace('#', '0x')}:s={self.width}x{self.height}:d={duration}",
            "-i", str(audio_path),  # Audio input
            "-filter_complex",
            f"[0:v]subtitles={self._escape_path(subtitle_path)}[outv]",
            "-map", "[outv]",
            "-map", "1:a",
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-profile:v", "main", "-level", "4.0",
            "-movflags", "+faststart",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            str(output_path),
        ]

        return self._run_ffmpeg(cmd, output_path)

    def _run_ffmpeg(self, cmd: list[str], output_path: Path) -> Path:
        """
        Execute FFmpeg command.

        Args:
            cmd: FFmpeg command arguments
            output_path: Expected output path

        Returns:
            Path to output file

        Raises:
            RuntimeError: If FFmpeg fails
        """
        logger.info(f"Running FFmpeg: {' '.join(cmd[:5])}...")
        logger.debug(f"Full command: {' '.join(cmd)}")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,  # 5 minute timeout
            )

            if result.returncode != 0:
                logger.error(f"FFmpeg stderr: {result.stderr}")
                raise RuntimeError(f"FFmpeg failed with code {result.returncode}")

            if not output_path.exists():
                raise RuntimeError(f"FFmpeg completed but output file not found: {output_path}")

            logger.info(f"Video rendered successfully: {output_path}")
            return output_path

        except subprocess.TimeoutExpired:
            raise RuntimeError("FFmpeg timed out after 5 minutes")
        except FileNotFoundError:
            raise RuntimeError("FFmpeg not found. Is it installed?")

    def _get_audio_duration(self, audio_path: Path) -> float:
        """Get duration of audio file in seconds."""
        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            return float(result.stdout.strip())
        except Exception as e:
            logger.warning(f"Could not get audio duration: {e}")
            return self.max_duration

    def _escape_path(self, path: Path) -> str:
        """
        Escape path for FFmpeg filter syntax.

        FFmpeg filter syntax requires escaping special characters.
        """
        # Convert to string and escape colons and backslashes
        path_str = str(path.absolute())
        # For the subtitles filter, we need to escape colons and backslashes
        path_str = path_str.replace("\\", "/")  # Use forward slashes
        path_str = path_str.replace(":", "\\:")  # Escape colons
        path_str = path_str.replace("'", "\\'")  # Escape single quotes
        return path_str

    def check_ffmpeg(self) -> bool:
        """Check if FFmpeg is installed and accessible."""
        try:
            result = subprocess.run(
                ["ffmpeg", "-version"],
                capture_output=True,
                timeout=10,
            )
            return result.returncode == 0
        except Exception:
            return False

    def get_video_info(self, video_path: Path) -> dict:
        """Get information about a video file."""
        cmd = [
            "ffprobe",
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height,duration",
            "-of", "json",
            str(video_path),
        ]

        try:
            import json
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            return json.loads(result.stdout)
        except Exception as e:
            logger.error(f"Failed to get video info: {e}")
            return {}
