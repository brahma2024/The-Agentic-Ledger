"""The Agentic Ledger - Main orchestrator for the automated podcast pipeline."""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from .config import settings
from .utils import get_logger, setup_logging

logger = get_logger(__name__)


def _slugify(text: str, max_len: int = 50) -> str:
    """Convert text to a filesystem-safe slug."""
    slug = text.lower()
    slug = re.sub(r"[^a-z0-9]+", "_", slug)
    slug = slug.strip("_")
    return slug[:max_len].rstrip("_")


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="The Agentic Ledger - Automated financial AI podcast pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m src.main                    # Full pipeline
  python -m src.main --dry-run          # Generate script only, no audio/video
  python -m src.main --skip-rss         # Use cached RSS data
  python -m src.main --skip-arxiv       # Skip paper search (use placeholder)
  python -m src.main --skip-convergence # Skip convergence, use keyword extraction
  python -m src.main --debug            # Enable debug logging

Workflow Phases:
  1. THE SIEVE      - Fetch & rank news from bundled RSS feeds
  1.5 CONVERGENCE   - Match news to arXiv categories, find papers
  2. THE PIVOT      - Extract academic keywords (fallback)
  3. THE DEEP DIVE  - Search arXiv for papers (fallback)
  4. THE SYNTHESIS  - Generate QUANT/HUSTLER script
  5. VOICE GEN      - Audio, subtitles, video rendering
        """,
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate script only, skip audio/video generation",
    )
    parser.add_argument(
        "--skip-rss",
        action="store_true",
        help="Skip RSS fetching, use existing cached data",
    )
    parser.add_argument(
        "--skip-arxiv",
        action="store_true",
        help="Skip arXiv paper search, use placeholder paper",
    )
    parser.add_argument(
        "--skip-convergence",
        action="store_true",
        help="Skip convergence analysis, use traditional keyword extraction",
    )
    parser.add_argument(
        "--skip-upload",
        action="store_true",
        help="Skip upload (deferred feature)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    parser.add_argument(
        "--sample",
        action="store_true",
        help="Generate a sample podcast with test dialogue",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Override output directory",
    )
    parser.add_argument(
        "--lexicon",
        action="store_true",
        help="Generate category lexicons and output Google Alert phrases",
    )
    parser.add_argument(
        "--lexicon-categories",
        type=str,
        default="cs.AI,cs.LG,cs.CR,cs.CL,q-fin.TR,q-fin.PM,q-fin.RM",
        help="Comma-separated arXiv category codes for lexicon generation",
    )

    return parser.parse_args()


def run_pipeline(args: argparse.Namespace) -> int:
    """
    Run the full 5-phase podcast pipeline.

    Args:
        args: Parsed command line arguments

    Returns:
        Exit code (0 for success)
    """
    from .arxiv_client import ArxivClient, ArxivPaper
    from .convergence import ConvergenceEngine, ConvergenceResult
    from .keyword_extractor import KeywordExtractor
    from .news_ranker import NewsRanker, RankedNewsItem
    from .rss import BundleManager, RSSFetcher, BundledNewsItem
    from .scraper import NewsItem
    from .script_generator import ScriptGenerator
    from .script_parser import ScriptParser

    start_time = datetime.now()
    logger.info("=" * 60)
    logger.info(f"{settings.podcast_name} - Starting pipeline")
    logger.info("=" * 60)

    # Override output directory if specified
    if args.output_dir:
        settings.output_dir = args.output_dir

    # Ensure directories exist
    settings.ensure_directories()

    try:
        # =================================================================
        # PHASE 1: THE SIEVE - Fetch and rank news by financial impact
        # =================================================================
        logger.info("")
        logger.info("=" * 40)
        logger.info("PHASE 1: THE SIEVE")
        logger.info("=" * 40)

        # Initialize bundle manager from settings
        bundle_manager = BundleManager.from_settings(settings)
        bundled_items: list[BundledNewsItem] = []

        if not bundle_manager.bundles and not args.skip_rss:
            logger.warning("No RSS_FEED_BUNDLES configured. Set RSS_FEED_BUNDLES in .env")
            logger.info("Using demo mode with sample news item")
            bundled_items = [
                BundledNewsItem(
                    item=NewsItem(
                        title="AI Trading Firm Reports Record Profits Using Large Language Models",
                        source="Demo",
                        summary="A quantitative trading firm has reported unprecedented returns by "
                                "integrating GPT-4 into their trading strategies, raising questions "
                                "about the future of algorithmic trading and market efficiency.",
                    ),
                    bundle_name="demo",
                    bundle_arxiv_codes=["cs.AI", "q-fin.TR"],
                )
            ]
        elif args.skip_rss:
            logger.info("Phase 1: Skipping RSS fetch, loading cached data...")
            news_file = settings.temp_dir / "bundled_news.json"
            if news_file.exists():
                data = json.loads(news_file.read_text())
                bundled_items = [
                    BundledNewsItem(
                        item=NewsItem(
                            title=item["item"].get("title", ""),
                            source=item["item"].get("source", "Cached"),
                            summary=item["item"].get("summary"),
                            url=item["item"].get("url"),
                        ),
                        bundle_name=item.get("bundle_name", "unknown"),
                        bundle_arxiv_codes=item.get("bundle_arxiv_codes", []),
                    )
                    for item in data
                ]
            else:
                logger.error(f"No cached news file found at {news_file}")
                return 1
        else:
            logger.info(f"Fetching from {len(bundle_manager.bundles)} RSS bundles...")
            rss_fetcher = RSSFetcher(
                bundle_manager=bundle_manager,
                cache_dir=settings.temp_dir,
                cache_ttl_minutes=settings.rss_cache_ttl_minutes,
            )
            bundled_items = rss_fetcher.fetch_all_bundled(limit=settings.rss_fetch_limit)

            # Save for potential re-use
            news_file = settings.temp_dir / "bundled_news.json"
            news_data = [bi.to_dict() for bi in bundled_items]
            news_file.write_text(json.dumps(news_data, indent=2))

        if not bundled_items:
            logger.error("No news items found after Alert + Google News fallback. Aborting.")
            return 1

        logger.info(f"Fetched {len(bundled_items)} news items")

        # Extract raw items for ranking
        raw_items = [bi.item for bi in bundled_items]

        # Build mapping from item to bundle hints
        item_to_hints: dict[str, list[str]] = {}
        for bi in bundled_items:
            key = bi.item.url or bi.item.title
            item_to_hints[key] = bi.bundle_arxiv_codes

        # Rank by financial impact
        logger.info("Ranking news by financial impact...")
        ranker = NewsRanker()
        top_n = settings.convergence_top_n_news if settings.convergence_enabled else 1
        ranked_items = ranker.rank_by_financial_impact(raw_items, top_n=top_n)

        if not ranked_items:
            logger.error("Failed to rank news items. Aborting.")
            return 1

        top_news = ranked_items[0].item
        logger.info(f"Top story [{ranked_items[0].score:.1f}]: {top_news.title}")
        logger.info(f"Phase 1 complete: Selected top story from {len(raw_items)} items")

        # =================================================================
        # PHASE 1.5: THE CONVERGENCE ENGINE
        # =================================================================
        paper = None
        convergence_result = None

        if settings.convergence_enabled and not args.skip_convergence:
            logger.info("")
            logger.info("=" * 40)
            logger.info("PHASE 1.5: THE CONVERGENCE ENGINE")
            logger.info("=" * 40)

            try:
                engine = ConvergenceEngine()

                # Analyze each item with its bundle hints
                all_results = []
                for ranked_item in ranked_items:
                    key = ranked_item.item.url or ranked_item.item.title
                    hints = item_to_hints.get(key, [])
                    result = engine.analyze_news_item(ranked_item, hint_codes=hints)
                    all_results.append(result)

                # Sort by combined score and select best
                all_results.sort(key=lambda x: x.combined_score, reverse=True)
                best_result = all_results[0]
                convergence_result = best_result

                # Save results
                engine._save_results(best_result, all_results)

                # Update top_news to the convergence-selected story
                top_news = best_result.ranked_item.item
                logger.info(f"Convergence selected: {top_news.title[:50]}...")
                logger.info(f"  Impact score: {best_result.ranked_item.score:.1f}")
                logger.info(f"  Convergence score: {best_result.convergence_score:.3f}")
                logger.info(f"  Combined score: {best_result.combined_score:.3f}")

                # If convergence found a paper, use it
                if best_result.best_paper:
                    paper = best_result.best_paper.paper
                    logger.info(f"  Best paper: {paper.title[:50]}...")
                    logger.info(f"  Paper relevance: {best_result.best_paper.relevance:.3f}")

                    # Extract key finding for the paper
                    arxiv_client = ArxivClient()
                    paper.key_finding = arxiv_client.extract_key_finding(paper)

                logger.info("Phase 1.5 complete: Convergence analysis done")

            except Exception as e:
                logger.warning(f"Convergence engine failed: {e}, falling back to traditional flow")
                convergence_result = None
                paper = None

        # =================================================================
        # PHASE 2: THE PIVOT - Extract academic keywords
        # =================================================================
        # Skip if convergence already found a paper
        if paper is None:
            logger.info("")
            logger.info("=" * 40)
            logger.info("PHASE 2: THE PIVOT")
            logger.info("=" * 40)

            extractor = KeywordExtractor()
            keywords = extractor.extract(top_news, max_keywords=5)
            logger.info(f"Extracted keywords: {keywords}")
            logger.info("Phase 2 complete: Keywords extracted")

            # =================================================================
            # PHASE 3: THE DEEP DIVE - Search arXiv for papers
            # =================================================================
            logger.info("")
            logger.info("=" * 40)
            logger.info("PHASE 3: THE DEEP DIVE")
            logger.info("=" * 40)

            if args.skip_arxiv:
                logger.info("Skipping arXiv search, using placeholder paper...")
                paper = ArxivPaper.placeholder()
            else:
                logger.info(f"Searching arXiv with keywords: {keywords}")
                arxiv_client = ArxivClient(
                    categories=settings.arxiv_categories,
                    max_results=settings.arxiv_max_results,
                    lookback_days=settings.arxiv_lookback_days,
                )
                paper = arxiv_client.search(keywords)

                if paper is None:
                    logger.warning("No matching paper found, using placeholder")
                    paper = ArxivPaper.placeholder()
                else:
                    logger.info(f"Found paper: {paper.title}")
                    # Extract key finding
                    paper.key_finding = arxiv_client.extract_key_finding(paper)

            logger.info(f"Phase 3 complete: Paper - {paper.title[:50]}...")
        else:
            logger.info("")
            logger.info("=" * 40)
            logger.info("PHASE 2 & 3: SKIPPED (paper found via convergence)")
            logger.info("=" * 40)
            logger.info(f"Using paper from convergence: {paper.title[:50]}...")

        # =================================================================
        # PHASE 4: THE SYNTHESIS - Generate QUANT/HUSTLER script
        # =================================================================
        logger.info("")
        logger.info("=" * 40)
        logger.info("PHASE 4: THE SYNTHESIS")
        logger.info("=" * 40)

        logger.info("Generating podcast script...")
        script_gen = ScriptGenerator()
        script = script_gen.generate(
            top_news=top_news,
            paper_title=paper.title,
            paper_abstract=paper.abstract,
            paper_finding=paper.key_finding,
        )

        # Save script to file
        script_file = settings.output_dir / "script.txt"
        script_file.write_text(script)
        logger.info(f"Script saved to: {script_file}")

        # Parse and validate script
        parser = ScriptParser()
        dialogue_lines = parser.parse(script)

        word_count = parser.get_word_count(dialogue_lines)
        logger.info(f"Script word count: {word_count} (target: {settings.target_word_count})")

        if not parser.validate_duration(dialogue_lines, settings.max_duration_seconds):
            logger.warning("Script may exceed target duration")

        logger.info(f"Phase 4 complete: {len(dialogue_lines)} dialogue lines")

        if args.dry_run:
            logger.info("")
            logger.info("=" * 40)
            logger.info("DRY RUN COMPLETE")
            logger.info("=" * 40)
            logger.info("Skipping audio/video generation.")
            logger.info(f"\nScript preview:\n{parser.format_for_display(dialogue_lines[:6])}")
            if len(dialogue_lines) > 6:
                logger.info(f"... and {len(dialogue_lines) - 6} more lines")
            return 0

        # =================================================================
        # PHASE 5: VOICE GEN - Audio, subtitles, video
        # =================================================================
        from .audio_engine import AudioEngine
        from .card_renderer import CardRenderer
        from .scene_planner import ScenePlanner
        from .subtitle_generator import SubtitleGenerator
        from .video_renderer import VideoRenderer

        logger.info("")
        logger.info("=" * 40)
        logger.info("PHASE 5: VOICE GEN")
        logger.info("=" * 40)

        # Step 5a: Generate audio
        logger.info("Generating audio...")
        audio_engine = AudioEngine()
        audio_path, segment_infos = audio_engine.generate_podcast(dialogue_lines)
        logger.info(f"Audio generated: {audio_path}")

        # Step 5a.5: Whisper transcription for word-level timestamps
        transcriptions = None
        if settings.whisper_enabled:
            try:
                from .whisper_transcriber import WhisperTranscriber

                logger.info("Transcribing audio segments via Whisper for karaoke subtitles...")
                whisper = WhisperTranscriber()
                transcriptions = whisper.transcribe_all(segment_infos)
                logger.info(f"Whisper transcription complete: {len(transcriptions)} segments")
            except Exception as e:
                logger.warning(f"Whisper transcription failed: {e}, falling back to segment-level subtitles")
                transcriptions = None

        # Step 5b: Generate subtitles
        logger.info("Generating subtitles...")
        subtitle_gen = SubtitleGenerator()
        subtitle_path = subtitle_gen.generate(segment_infos, transcriptions=transcriptions)
        logger.info(f"Subtitles generated: {subtitle_path}")

        # Step 5c: Plan and render scene cards (visual storyboard)
        video_renderer = VideoRenderer()

        if not video_renderer.check_ffmpeg():
            logger.error("FFmpeg not found. Please install FFmpeg.")
            return 1

        rendered_cards = None
        try:
            # Get actual audio duration for scene timing
            audio_duration_s = video_renderer._get_audio_duration(audio_path)
            logger.info(f"Audio duration: {audio_duration_s:.1f}s")

            # Plan visual storyboard
            scene_planner = ScenePlanner()
            timeline = scene_planner.plan(
                script=script,
                top_news=top_news,
                paper=paper,
                audio_duration_s=audio_duration_s,
                convergence_result=convergence_result,
            )

            # Render scene card PNGs
            card_renderer = CardRenderer()
            rendered_cards = card_renderer.render_all(timeline)
            logger.info(f"Scene cards rendered: {len(rendered_cards)} cards")

        except Exception as e:
            logger.warning(f"Scene planning/rendering failed: {e}, falling back to screenshots")
            rendered_cards = None

        # Step 5d: Capture screenshots as fallback
        screenshot_pair = None
        if not rendered_cards:
            from .screenshot_capture import ScreenshotCapture

            try:
                sc = ScreenshotCapture()
                news_url = getattr(top_news, "url", None)
                arxiv_id = getattr(paper, "arxiv_id", None)
                screenshot_pair = sc.capture_news_and_paper(news_url, arxiv_id)
                logger.info(
                    f"Screenshots captured: news={screenshot_pair.news_screenshot is not None}, "
                    f"paper={screenshot_pair.paper_screenshot is not None}"
                )
            except Exception as e:
                logger.warning(f"Screenshot capture failed, will use fallback background: {e}")
                screenshot_pair = None

        # Step 5e: Render video
        logger.info("Rendering video...")

        # Build descriptive output filename: article_arxivpaper_timestamp.mp4
        article_slug = _slugify(top_news.title)
        paper_slug = _slugify(paper.arxiv_id if paper.arxiv_id != "placeholder" else paper.title)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_filename = f"{article_slug}_{paper_slug}_{timestamp}.mp4"
        output_path = settings.output_dir / output_filename

        output_video = video_renderer.render(
            audio_path,
            subtitle_path,
            output_path=output_path,
            screenshot_pair=screenshot_pair,
            scene_cards=rendered_cards,
        )
        logger.info(f"Video rendered: {output_video}")

        # Cleanup temp files
        audio_engine.cleanup_temp_files()

        # Cleanup card PNGs
        if rendered_cards:
            for card in rendered_cards:
                try:
                    card.image_path.unlink(missing_ok=True)
                except Exception:
                    pass

        # Summary
        elapsed = datetime.now() - start_time
        logger.info("")
        logger.info("=" * 60)
        logger.info("PIPELINE COMPLETE!")
        logger.info("=" * 60)
        logger.info(f"Output: {output_video}")
        logger.info(f"Duration: ~{settings.target_duration_seconds}s")
        logger.info(f"Elapsed time: {elapsed}")
        logger.info("=" * 60)

        return 0

    except Exception as e:
        logger.exception(f"Pipeline failed: {e}")
        return 1


def run_sample(args: argparse.Namespace) -> int:
    """Generate a sample podcast with test dialogue."""
    from .audio_engine import AudioEngine
    from .subtitle_generator import SubtitleGenerator
    from .video_renderer import VideoRenderer

    logger.info("Generating sample podcast...")

    settings.ensure_directories()

    try:
        # Generate sample audio
        audio_engine = AudioEngine()
        audio_path = audio_engine.generate_sample()

        # Generate sample subtitles
        from .audio_engine import AudioSegmentInfo

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

        subtitle_gen = SubtitleGenerator()
        subtitle_path = subtitle_gen.generate(
            sample_segments,
            settings.output_dir / "sample_subtitles.ass",
        )

        # Render sample video
        video_renderer = VideoRenderer()
        if video_renderer.check_ffmpeg():
            output_video = video_renderer.render(
                audio_path,
                subtitle_path,
                settings.output_dir / "sample_output.mp4",
            )
            logger.info(f"Sample video: {output_video}")
        else:
            logger.warning("FFmpeg not found, skipping video render")
            logger.info(f"Sample audio: {audio_path}")
            logger.info(f"Sample subtitles: {subtitle_path}")

        return 0

    except Exception as e:
        logger.exception(f"Sample generation failed: {e}")
        return 1


def run_lexicon(args: argparse.Namespace) -> int:
    """Generate category lexicons and output Google Alert phrases."""
    from .taxonomy import CategoryLexiconGenerator

    print("=" * 60)
    print("CATEGORY LEXICON GENERATOR")
    print("=" * 60)
    print()
    print("Generating news-friendly search phrases for Google Alerts...")
    print("These phrases help create RSS feeds aligned with arXiv categories.")
    print()

    settings.ensure_directories()

    try:
        generator = CategoryLexiconGenerator()
        categories = [c.strip() for c in args.lexicon_categories.split(",")]

        for code in categories:
            lexicon = generator.get_lexicon(code)
            if lexicon is None:
                print(f"WARNING: Unknown category '{code}', skipping")
                continue

            print(f"\n{'=' * 60}")
            print(f"CATEGORY: {code} - {lexicon.category_name}")
            print("=" * 60)

            # Top phrases for Google Alerts
            top_phrases = lexicon.get_top_phrases(15)
            print(f"\nTop {len(top_phrases)} phrases (by confidence):")
            for i, phrase in enumerate(top_phrases, 1):
                print(f"  {i:2}. \"{phrase}\"")

            # Google Alerts query format
            alert_phrases = generator.export_for_google_alerts(code, max_phrases=8)
            alert_query = " OR ".join(alert_phrases)
            print(f"\n📋 GOOGLE ALERTS QUERY (copy this):")
            print(f"   {alert_query}")

        print(f"\n{'=' * 60}")
        print("NEXT STEPS:")
        print("=" * 60)
        print("1. Go to https://www.google.com/alerts")
        print("2. Paste one of the queries above")
        print("3. Set: How often=As-it-happens, Sources=News, Deliver to=RSS feed")
        print("4. Copy the RSS feed URL")
        print("5. Add to RSS_FEED_BUNDLES in your .env file")
        print()

        return 0

    except Exception as e:
        logger.exception(f"Lexicon generation failed: {e}")
        return 1


def main() -> int:
    """Main entry point."""
    args = parse_args()

    # Setup logging
    log_level = "DEBUG" if args.debug else settings.log_level
    setup_logging(level=log_level, gcp_project_id=settings.gcp_project_id)

    logger.info(f"Arguments: {args}")

    if args.lexicon:
        return run_lexicon(args)
    elif args.sample:
        return run_sample(args)
    else:
        return run_pipeline(args)


if __name__ == "__main__":
    sys.exit(main())
