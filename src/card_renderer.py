"""Cinematic card renderer — HTML+Playwright with PIL fallback.

Renders 9 scene card types as 1920x1080 PNGs with glassmorphism,
gradients, SVG accents, and rich typography.
"""

import html
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import settings
from .scene_planner import CardType, SceneCard, SceneTimeline
from .utils import get_logger

logger = get_logger(__name__)

# Layout constants
WIDTH = 1920
HEIGHT = 1080

# Subtitle safe zone — bottom 15% is kept empty
SAFE_ZONE_PX = 162


@dataclass
class RenderedCard:
    """A rendered scene card image."""

    card_type: CardType
    image_path: Path
    start_s: float
    end_s: float

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


def _esc(text: str) -> str:
    """HTML-escape text for safe injection into templates."""
    return html.escape(str(text)) if text else ""


class CardRenderer:
    """Renders scene cards as PNG images using Playwright (HTML) or PIL fallback."""

    def __init__(self):
        self.output_dir = settings.temp_dir
        self.fonts_dir = settings.fonts_dir

    def render_all(self, timeline: SceneTimeline) -> list[RenderedCard]:
        """Render all scene cards. Tries Playwright, falls back to PIL."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        try:
            return self._render_all_playwright(timeline)
        except Exception as e:
            logger.warning(f"Playwright rendering failed: {e}, falling back to PIL")
            return self._render_all_pil(timeline)

    # ==================================================================
    # Playwright path (HTML+CSS → screenshot)
    # ==================================================================

    def _render_all_playwright(self, timeline: SceneTimeline) -> list[RenderedCard]:
        """Render cards via Playwright headless Chromium."""
        from playwright.sync_api import sync_playwright

        rendered = []
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT})

            for card in timeline.cards:
                html_content = self._generate_html(card)
                page.set_content(html_content, wait_until="load")
                out_path = self.output_dir / f"card_{card.card_type.value}.png"
                page.screenshot(path=str(out_path))
                logger.info(f"Rendered {card.card_type.value} card (Playwright): {out_path}")

                rendered.append(RenderedCard(
                    card_type=card.card_type,
                    image_path=out_path,
                    start_s=card.start_s,
                    end_s=card.end_s,
                ))

            browser.close()

        logger.info(f"Playwright rendered {len(rendered)} scene cards")
        return rendered

    # ------------------------------------------------------------------
    # HTML generation — dispatch per card type
    # ------------------------------------------------------------------

    def _generate_html(self, card: SceneCard) -> str:
        """Generate full HTML page for a card."""
        dispatch = {
            CardType.TITLE: self._html_title,
            CardType.CONTEXT: self._html_context,
            CardType.HEADLINE: self._html_headline,
            CardType.KEY_STAT: self._html_key_stat,
            CardType.BRIDGE: self._html_bridge,
            CardType.PAPER: self._html_paper,
            CardType.QUOTE: self._html_quote,
            CardType.ALPHA: self._html_alpha,
            CardType.SUMMARY: self._html_summary,
        }
        method = dispatch.get(card.card_type, self._html_title)
        return method(card.content)

    def _page(self, body: str, gradient: str = "linear-gradient(135deg, #0f0c29 0%, #1a1a3e 50%, #0d1b2a 100%)") -> str:
        """Wrap body content in a full HTML page with base CSS."""
        fonts_dir_url = self.fonts_dir.absolute().as_uri()
        return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
@font-face {{ font-family: 'Montserrat'; font-weight: 300; src: url('{fonts_dir_url}/Montserrat-Light.ttf') format('truetype'); }}
@font-face {{ font-family: 'Montserrat'; font-weight: 400; src: url('{fonts_dir_url}/Montserrat-Regular.ttf') format('truetype'); }}
@font-face {{ font-family: 'Montserrat'; font-weight: 700; src: url('{fonts_dir_url}/Montserrat-Bold.ttf') format('truetype'); }}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
  width: {WIDTH}px; height: {HEIGHT}px; overflow: hidden;
  background: {gradient};
  font-family: 'Montserrat', 'Segoe UI', sans-serif;
  color: #f0f0f0;
}}
.content-area {{
  position: absolute; top: 0; left: 0; right: 0;
  bottom: {SAFE_ZONE_PX}px;
  display: flex; flex-direction: column;
  padding: 80px 120px;
}}
.glass {{
  background: rgba(255,255,255,0.06);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  border: 1px solid rgba(255,255,255,0.10);
  border-radius: 16px;
  padding: 32px 40px;
}}
.gold {{ color: #FFD700; }}
.green {{ color: #00FF00; }}
.purple {{ color: #B482FF; }}
.dim {{ color: rgba(255,255,255,0.45); }}
.glow-gold {{ text-shadow: 0 0 40px rgba(255,215,0,0.2); }}
.glow-green {{ text-shadow: 0 0 40px rgba(0,255,0,0.2); }}
.glow-purple {{ text-shadow: 0 0 40px rgba(180,130,255,0.2); }}
.hero {{ font-size: 72px; font-weight: 700; line-height: 1.15; }}
.large {{ font-size: 48px; font-weight: 700; line-height: 1.25; }}
.medium {{ font-size: 36px; font-weight: 400; line-height: 1.35; }}
.body-text {{ font-size: 28px; font-weight: 400; line-height: 1.45; }}
.small {{ font-size: 22px; font-weight: 300; line-height: 1.5; }}
.tiny {{ font-size: 16px; font-weight: 300; line-height: 1.5; }}
.accent-bar {{ width: 60px; height: 4px; border-radius: 2px; margin-bottom: 16px; }}
.section-label {{ font-size: 20px; font-weight: 700; letter-spacing: 3px; text-transform: uppercase; margin-bottom: 24px; }}
</style></head><body>
<div class="content-area">{body}</div>
</body></html>"""

    # ------------------------------------------------------------------
    # Card type HTML generators
    # ------------------------------------------------------------------

    def _html_title(self, c: dict) -> str:
        brand = _esc(c.get("brand", "The Agentic Ledger"))
        topic = _esc(c.get("topic", ""))
        date = _esc(c.get("date", ""))
        body = f"""
<div style="flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;">
  <!-- Radial glow -->
  <div style="position:absolute;top:50%;left:50%;width:600px;height:600px;
              background:radial-gradient(circle,rgba(255,215,0,0.08) 0%,transparent 70%);
              transform:translate(-50%,-50%);pointer-events:none;"></div>
  <div style="font-size:36px;font-weight:300;letter-spacing:6px;color:rgba(255,255,255,0.6);margin-bottom:24px;">{brand}</div>
  <!-- Geometric accent lines -->
  <div style="display:flex;align-items:center;gap:20px;margin-bottom:32px;">
    <div style="width:80px;height:1px;background:linear-gradient(90deg,transparent,#FFD700);"></div>
    <div style="width:8px;height:8px;border:1px solid #FFD700;transform:rotate(45deg);"></div>
    <div style="width:80px;height:1px;background:linear-gradient(90deg,#FFD700,transparent);"></div>
  </div>
  <div class="hero gold glow-gold" style="max-width:1400px;">{topic}</div>
  <div style="margin-top:40px;font-size:22px;font-weight:300;color:rgba(255,255,255,0.4);">{date}</div>
</div>"""
        return self._page(body, "linear-gradient(135deg, #0f0c29 0%, #1a1a3e 40%, #16213e 100%)")

    def _html_context(self, c: dict) -> str:
        label = _esc(c.get("section_label", "WHY THIS MATTERS"))
        stat = _esc(c.get("stat", ""))
        explanation = _esc(c.get("explanation", ""))
        one_liner = _esc(c.get("one_liner", ""))
        body = f"""
<div class="accent-bar" style="background:#FFD700;"></div>
<div class="section-label gold">{label}</div>
<div style="flex:1;display:flex;flex-direction:column;justify-content:center;gap:32px;">
  <div class="glass" style="text-align:center;padding:48px;">
    <div style="font-size:96px;font-weight:700;color:#FFD700;text-shadow:0 0 60px rgba(255,215,0,0.25);line-height:1;">{stat}</div>
    <div style="margin-top:24px;font-size:28px;font-weight:400;color:rgba(255,255,255,0.7);">{explanation}</div>
  </div>
  <div class="small dim" style="text-align:center;">{one_liner}</div>
</div>"""
        return self._page(body, "linear-gradient(135deg, #0d1b2a 0%, #1b2838 50%, #0f0c29 100%)")

    def _html_headline(self, c: dict) -> str:
        label = _esc(c.get("section_label", "THE NEWS"))
        title = _esc(c.get("title", ""))
        source = _esc(c.get("source", ""))
        bullets = c.get("bullets", [])

        bullets_html = ""
        for b in bullets[:3]:
            b_esc = _esc(b)
            bullets_html += f"""
  <div style="display:flex;align-items:flex-start;gap:16px;margin-bottom:16px;">
    <svg width="24" height="24" style="flex-shrink:0;margin-top:4px;"><circle cx="12" cy="12" r="10" fill="none" stroke="#FFD700" stroke-width="2"/>
    <path d="M8 12l3 3 5-5" stroke="#FFD700" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>
    <span class="body-text">{b_esc}</span>
  </div>"""

        body = f"""
<div class="glass" style="max-width:1500px;">
  <div style="display:flex;align-items:center;gap:16px;margin-bottom:20px;">
    <div style="width:6px;height:48px;background:linear-gradient(180deg,#FFD700,rgba(255,215,0,0.2));border-radius:3px;"></div>
    <div class="section-label gold" style="margin-bottom:0;">{label}</div>
  </div>
  <div class="large" style="margin-bottom:12px;">{title}</div>
  <div style="display:inline-block;background:rgba(255,215,0,0.12);border:1px solid rgba(255,215,0,0.3);border-radius:6px;padding:4px 14px;margin-bottom:28px;">
    <span class="tiny gold">Source: {source}</span>
  </div>
  <div style="margin-top:8px;">{bullets_html}</div>
</div>"""
        return self._page(body, "linear-gradient(135deg, #0f0c29 0%, #1a1a3e 50%, #0d1b2a 100%)")

    def _html_key_stat(self, c: dict) -> str:
        number = _esc(c.get("number", "N/A"))
        context = _esc(c.get("context", ""))
        body = f"""
<div style="flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;">
  <div style="position:absolute;top:50%;left:50%;width:500px;height:500px;
              background:radial-gradient(circle,rgba(255,215,0,0.06) 0%,transparent 70%);
              transform:translate(-50%,-50%);pointer-events:none;"></div>
  <div style="font-size:140px;font-weight:700;color:#FFD700;line-height:1;
              text-shadow:0 0 80px rgba(255,215,0,0.3);letter-spacing:-2px;">{number}</div>
  <div style="width:120px;height:2px;background:linear-gradient(90deg,transparent,#FFD700,transparent);margin:32px auto;"></div>
  <div class="medium" style="color:rgba(255,255,255,0.65);max-width:900px;">{context}</div>
</div>"""
        return self._page(body, "linear-gradient(135deg, #0d1b2a 0%, #0f0c29 50%, #16213e 100%)")

    def _html_bridge(self, c: dict) -> str:
        label = _esc(c.get("section_label", "THE CONNECTION"))
        news = _esc(c.get("news_title", ""))
        insight = _esc(c.get("bridge_insight", ""))
        playbook = _esc(c.get("playbook_hint", ""))

        arrow_svg = """<svg width="40" height="40" style="margin:8px auto;display:block;">
  <line x1="20" y1="0" x2="20" y2="30" stroke="#B482FF" stroke-width="2" stroke-dasharray="4,4"/>
  <polygon points="12,28 20,38 28,28" fill="#B482FF"/>
</svg>"""

        body = f"""
<div class="accent-bar" style="background:#B482FF;"></div>
<div class="section-label purple">{label}</div>
<div style="flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:0;">
  <!-- Surface Event -->
  <div class="glass" style="text-align:center;max-width:1200px;width:100%;
              border-color:rgba(255,255,255,0.08);">
    <div class="tiny dim" style="letter-spacing:2px;margin-bottom:8px;">SURFACE EVENT</div>
    <div class="body-text">{news}</div>
  </div>
  {arrow_svg}
  <!-- Hidden Mechanism -->
  <div class="glass" style="text-align:center;max-width:1200px;width:100%;
              background:rgba(180,130,255,0.08);border-color:rgba(180,130,255,0.2);">
    <div class="tiny dim" style="letter-spacing:2px;margin-bottom:8px;">HIDDEN MECHANISM</div>
    <div class="medium purple glow-purple">{insight}</div>
  </div>
  {arrow_svg}
  <!-- The Playbook -->
  <div class="glass" style="text-align:center;max-width:1200px;width:100%;
              background:rgba(0,255,0,0.05);border-color:rgba(0,255,0,0.15);">
    <div class="tiny dim" style="letter-spacing:2px;margin-bottom:8px;">THE PLAYBOOK</div>
    <div class="body-text green glow-green">{playbook}</div>
  </div>
</div>"""
        return self._page(body, "linear-gradient(135deg, #0f0c29 0%, #1a1a3e 40%, #0d1b2a 100%)")

    def _html_paper(self, c: dict) -> str:
        label = _esc(c.get("section_label", "THE PAPER"))
        title = _esc(c.get("title", ""))
        authors = _esc(c.get("authors", ""))
        finding = _esc(c.get("key_finding", ""))
        arxiv_id = _esc(c.get("arxiv_id", ""))

        badge_html = ""
        if arxiv_id and arxiv_id != "placeholder":
            badge_html = f"""<div style="display:inline-block;background:rgba(255,215,0,0.10);
                border:1px solid rgba(255,215,0,0.25);border-radius:6px;padding:4px 14px;">
                <span class="tiny gold">arXiv: {arxiv_id}</span></div>"""

        body = f"""
<div class="glass" style="border-left:4px solid #FFD700;max-width:1500px;">
  <div class="section-label gold" style="margin-bottom:20px;">{label}</div>
  <div class="large" style="margin-bottom:12px;">{title}</div>
  <div class="small dim" style="margin-bottom:24px;">{authors}</div>
  <div style="background:rgba(255,215,0,0.06);border-radius:12px;padding:24px 32px;margin-bottom:20px;">
    <div class="tiny gold" style="letter-spacing:2px;margin-bottom:8px;">KEY FINDING</div>
    <div class="medium gold glow-gold">{finding}</div>
  </div>
  {badge_html}
</div>"""
        return self._page(body, "linear-gradient(135deg, #0d1b2a 0%, #1a1a3e 50%, #0f0c29 100%)")

    def _html_quote(self, c: dict) -> str:
        quote = _esc(c.get("quote", ""))
        attribution = _esc(c.get("attribution", ""))
        body = f"""
<div style="flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;position:relative;">
  <!-- Decorative quote marks -->
  <div style="position:absolute;top:20px;left:80px;font-size:120px;font-weight:700;
              color:rgba(180,130,255,0.08);line-height:1;font-family:Georgia,serif;">&ldquo;</div>
  <div style="position:absolute;bottom:{SAFE_ZONE_PX + 40}px;right:80px;font-size:120px;font-weight:700;
              color:rgba(180,130,255,0.08);line-height:1;font-family:Georgia,serif;">&rdquo;</div>
  <div class="large" style="font-style:italic;max-width:1200px;color:rgba(255,255,255,0.9);line-height:1.4;">{quote}</div>
  <div style="width:60px;height:3px;background:#B482FF;margin:28px auto 20px;border-radius:2px;"></div>
  <div class="small purple">{attribution}</div>
</div>"""
        return self._page(body, "linear-gradient(135deg, #0f0c29 0%, #1b1340 50%, #0d1b2a 100%)")

    def _html_alpha(self, c: dict) -> str:
        label = _esc(c.get("section_label", "THE ALPHA"))
        header = _esc(c.get("header", "ACTIONABLE INSIGHTS"))
        bullets = c.get("bullets", [])

        cards_html = ""
        for i, bullet in enumerate(bullets[:3], 1):
            b_esc = _esc(bullet)
            cards_html += f"""
  <div class="glass" style="flex:1;text-align:center;padding:40px 28px;">
    <div style="width:48px;height:48px;border-radius:50%;background:rgba(0,255,0,0.15);
                border:2px solid #00FF00;display:flex;align-items:center;justify-content:center;
                margin:0 auto 20px;font-size:24px;font-weight:700;color:#00FF00;">0{i}</div>
    <div class="body-text" style="color:rgba(255,255,255,0.85);">{b_esc}</div>
  </div>"""

        body = f"""
<div class="accent-bar" style="background:#00FF00;"></div>
<div class="section-label green">{label}</div>
<div class="medium" style="margin-bottom:32px;color:rgba(255,255,255,0.7);">{header}</div>
<div style="display:flex;gap:24px;flex:1;align-items:stretch;">
  {cards_html}
</div>"""
        return self._page(body, "linear-gradient(135deg, #0d1b2a 0%, #0f0c29 50%, #1a1a3e 100%)")

    def _html_summary(self, c: dict) -> str:
        brand = _esc(c.get("brand", "The Agentic Ledger"))
        topic = _esc(c.get("topic", ""))
        takeaway = _esc(c.get("key_takeaway", ""))
        body = f"""
<div style="flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;position:relative;">
  <!-- Brand watermark -->
  <div style="position:absolute;top:50%;left:50%;font-size:160px;font-weight:700;
              color:rgba(255,255,255,0.02);transform:translate(-50%,-50%) rotate(-10deg);
              white-space:nowrap;pointer-events:none;">{brand}</div>
  <div class="small dim" style="letter-spacing:4px;margin-bottom:16px;">{brand}</div>
  <div class="medium" style="color:rgba(255,255,255,0.6);margin-bottom:40px;">{topic}</div>
  <div class="large gold glow-gold" style="max-width:1300px;line-height:1.35;">{takeaway}</div>
  <!-- Bottom gradient fade -->
  <div style="position:absolute;bottom:0;left:0;right:0;height:80px;
              background:linear-gradient(transparent,rgba(13,27,42,0.6));pointer-events:none;"></div>
  <div style="margin-top:48px;font-size:24px;font-weight:300;color:rgba(255,255,255,0.3);">See you next episode.</div>
</div>"""
        return self._page(body, "linear-gradient(135deg, #0f0c29 0%, #0d1b2a 50%, #1a1a3e 100%)")

    # ==================================================================
    # PIL fallback path (original flat rendering)
    # ==================================================================

    def _render_all_pil(self, timeline: SceneTimeline) -> list[RenderedCard]:
        """Render all cards using PIL (flat dark-mode aesthetic)."""
        from PIL import Image, ImageDraw, ImageFont

        BG_COLOR = (26, 26, 46)
        TEXT_WHITE = (240, 240, 240)
        TEXT_DIM = (140, 140, 160)
        GOLD = (255, 215, 0)
        GREEN = (0, 255, 0)
        PURPLE = (180, 130, 255)
        MARGIN_X = 120
        MARGIN_TOP = 100
        LINE_SPACING = 1.4

        font_path = settings.font_path
        font_cache: dict[int, ImageFont.FreeTypeFont] = {}

        def _font(size: int) -> ImageFont.FreeTypeFont:
            if size not in font_cache:
                try:
                    font_cache[size] = ImageFont.truetype(str(font_path), size)
                except OSError:
                    font_cache[size] = ImageFont.load_default()
            return font_cache[size]

        def _word_wrap(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
            if not text:
                return []
            words = text.split()
            lines = []
            current_line = ""
            for word in words:
                test_line = f"{current_line} {word}".strip()
                bbox = font.getbbox(test_line)
                if (bbox[2] - bbox[0]) <= max_width:
                    current_line = test_line
                else:
                    if current_line:
                        lines.append(current_line)
                    current_line = word
            if current_line:
                lines.append(current_line)
            return lines

        def _draw_wrapped(draw, text, x, y, font, color, max_width):
            lines = _word_wrap(text, font, max_width)
            bbox = font.getbbox("Ay")
            lh = int((bbox[3] - bbox[1]) * LINE_SPACING)
            for line in lines:
                draw.text((x, y), line, font=font, fill=color)
                y += lh
            return y

        def _draw_centered(draw, text, cx, y, font, color):
            bbox = font.getbbox(text)
            tw = bbox[2] - bbox[0]
            draw.text((cx - tw // 2, y), text, font=font, fill=color)

        def _draw_accent_bar(draw, y, color, width=60):
            draw.rectangle([(MARGIN_X, y), (MARGIN_X + width, y + 4)], fill=color)

        def _draw_section_label(draw, label, y, accent_color):
            _draw_accent_bar(draw, y, accent_color)
            draw.text((MARGIN_X, y + 15), label, font=_font(24), fill=accent_color)
            return y + 60

        def _draw_arrow_down(draw, cx, y, color):
            draw.polygon([(cx - 12, y), (cx + 12, y), (cx, y + 20)], fill=color)

        cx = WIDTH // 2
        mw = WIDTH - 2 * MARGIN_X
        rendered = []

        for card in timeline.cards:
            img = Image.new("RGB", (WIDTH, HEIGHT), BG_COLOR)
            draw = ImageDraw.Draw(img)
            c = card.content

            if card.card_type == CardType.TITLE:
                brand = c.get("brand", "The Agentic Ledger")
                _draw_centered(draw, brand, cx, 320, _font(72), TEXT_WHITE)
                draw.line([(cx - 200, 420), (cx + 200, 420)], fill=GOLD, width=3)
                _draw_centered(draw, c.get("topic", ""), cx, 480, _font(48), GOLD)
                _draw_centered(draw, c.get("date", ""), cx, 600, _font(24), TEXT_DIM)

            elif card.card_type == CardType.CONTEXT:
                y = MARGIN_TOP
                y = _draw_section_label(draw, c.get("section_label", "WHY THIS MATTERS"), y, GOLD)
                _draw_centered(draw, c.get("stat", ""), cx, y + 60, _font(72), GOLD)
                _draw_centered(draw, c.get("explanation", ""), cx, y + 160, _font(28), TEXT_DIM)
                _draw_centered(draw, c.get("one_liner", ""), cx, y + 240, _font(22), TEXT_DIM)

            elif card.card_type == CardType.HEADLINE:
                y = MARGIN_TOP
                y = _draw_section_label(draw, c.get("section_label", "THE NEWS"), y, GOLD)
                y = _draw_wrapped(draw, c.get("title", ""), MARGIN_X, y + 30, _font(48), TEXT_WHITE, mw)
                draw.text((MARGIN_X, y + 20), f"Source: {c.get('source', '')}", font=_font(22), fill=TEXT_DIM)
                y += 70
                for bullet in c.get("bullets", [])[:3]:
                    draw.ellipse([(MARGIN_X, y + 8), (MARGIN_X + 14, y + 22)], fill=GOLD)
                    _draw_wrapped(draw, bullet, MARGIN_X + 30, y, _font(30), TEXT_WHITE, mw - 30)
                    y += 60

            elif card.card_type == CardType.KEY_STAT:
                _draw_centered(draw, c.get("number", "N/A"), cx, 340, _font(96), GOLD)
                draw.line([(cx - 60, 470), (cx + 60, 470)], fill=GOLD, width=2)
                wrapped = _word_wrap(c.get("context", ""), _font(32), mw)
                y = 510
                for line in wrapped[:3]:
                    _draw_centered(draw, line, cx, y, _font(32), TEXT_DIM)
                    y += 48

            elif card.card_type == CardType.BRIDGE:
                y = MARGIN_TOP
                y = _draw_section_label(draw, c.get("section_label", "THE CONNECTION"), y, PURPLE)
                y += 40
                _draw_centered(draw, "SURFACE EVENT", cx, y, _font(22), TEXT_DIM)
                y += 40
                for line in _word_wrap(c.get("news_title", ""), _font(32), mw)[:2]:
                    _draw_centered(draw, line, cx, y, _font(32), TEXT_WHITE)
                    y += 48
                y += 10
                _draw_arrow_down(draw, cx, y, PURPLE)
                y += 50
                _draw_centered(draw, "HIDDEN MECHANISM", cx, y, _font(22), TEXT_DIM)
                y += 40
                for line in _word_wrap(c.get("bridge_insight", ""), _font(40), mw)[:3]:
                    _draw_centered(draw, line, cx, y, _font(40), PURPLE)
                    y += 56
                y += 10
                _draw_arrow_down(draw, cx, y, PURPLE)
                y += 50
                _draw_centered(draw, "THE PLAYBOOK", cx, y, _font(22), TEXT_DIM)
                y += 40
                for line in _word_wrap(c.get("playbook_hint", ""), _font(32), mw)[:2]:
                    _draw_centered(draw, line, cx, y, _font(32), GREEN)
                    y += 48

            elif card.card_type == CardType.PAPER:
                y = MARGIN_TOP
                y = _draw_section_label(draw, c.get("section_label", "THE PAPER"), y, GOLD)
                y = _draw_wrapped(draw, c.get("title", ""), MARGIN_X, y + 30, _font(48), TEXT_WHITE, mw)
                draw.text((MARGIN_X, y + 20), c.get("authors", ""), font=_font(24), fill=TEXT_DIM)
                y += 70
                y = _draw_wrapped(draw, c.get("key_finding", ""), MARGIN_X, y, _font(36), GOLD, mw)
                arxiv_id = c.get("arxiv_id", "")
                if arxiv_id and arxiv_id != "placeholder":
                    draw.text((MARGIN_X, HEIGHT - 80), f"arXiv: {arxiv_id}", font=_font(20), fill=TEXT_DIM)

            elif card.card_type == CardType.QUOTE:
                quote = c.get("quote", "")
                attribution = c.get("attribution", "")
                wrapped = _word_wrap(quote, _font(40), mw)
                y = 350
                for line in wrapped[:4]:
                    _draw_centered(draw, line, cx, y, _font(40), TEXT_WHITE)
                    y += 56
                draw.line([(cx - 30, y + 20), (cx + 30, y + 20)], fill=PURPLE, width=3)
                _draw_centered(draw, attribution, cx, y + 50, _font(24), PURPLE)

            elif card.card_type == CardType.ALPHA:
                y = MARGIN_TOP
                y = _draw_section_label(draw, c.get("section_label", "THE ALPHA"), y, GREEN)
                draw.text((MARGIN_X, y + 30), c.get("header", "ACTIONABLE INSIGHTS"), font=_font(36), fill=TEXT_WHITE)
                y += 100
                for i, bullet in enumerate(c.get("bullets", [])[:3], 1):
                    draw.text((MARGIN_X, y), f"0{i}", font=_font(48), fill=GREEN)
                    _draw_wrapped(draw, bullet, MARGIN_X + 100, y + 8, _font(32), TEXT_WHITE, mw - 100)
                    y += 120

            elif card.card_type == CardType.SUMMARY:
                brand = c.get("brand", "The Agentic Ledger")
                _draw_centered(draw, brand, cx, 280, _font(36), TEXT_DIM)
                _draw_centered(draw, c.get("topic", ""), cx, 370, _font(32), TEXT_WHITE)
                wrapped = _word_wrap(c.get("key_takeaway", ""), _font(40), mw)
                y = 480
                for line in wrapped[:3]:
                    _draw_centered(draw, line, cx, y, _font(40), GOLD)
                    y += 56
                _draw_centered(draw, "See you next episode.", cx, 700, _font(28), TEXT_DIM)

            out_path = self.output_dir / f"card_{card.card_type.value}.png"
            img.save(out_path, "PNG")
            logger.info(f"Rendered {card.card_type.value} card (PIL): {out_path}")

            rendered.append(RenderedCard(
                card_type=card.card_type,
                image_path=out_path,
                start_s=card.start_s,
                end_s=card.end_s,
            ))

        logger.info(f"PIL rendered {len(rendered)} scene cards")
        return rendered
