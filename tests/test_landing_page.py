import re
from collections.abc import Iterable
from html.parser import HTMLParser
from pathlib import Path
from typing import cast

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "docs" / "index.html"
GITHUB_URL = "https://github.com/hjw/ChatFit"
MASCOT = ROOT / "docs" / "assets" / "chatfit-kettlebell-buddy.png"


class StartTagCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))


def page_html() -> str:
    return INDEX.read_text(encoding="utf-8")


def page_tags() -> list[tuple[str, dict[str, str | None]]]:
    parser = StartTagCollector()
    parser.feed(page_html())
    return parser.tags


def css_color_value(value: str, tokens: dict[str, str]) -> str:
    variable = re.fullmatch(r"var\(--([\w-]+)\)", value)
    return tokens[variable.group(1)] if variable else value


def relative_luminance(color: str) -> float:
    channels = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear_channels = [
        channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
        for channel in channels
    ]
    red, green, blue = linear_channels
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(foreground: str, background: str) -> float:
    lighter, darker = sorted(
        (relative_luminance(foreground), relative_luminance(background)),
        reverse=True,
    )
    return (lighter + 0.05) / (darker + 0.05)


def test_landing_page_leads_with_truthful_product_message() -> None:
    html = page_html()
    required_copy = (
        "Just tell it what you did.",
        "Deploy on GitHub",
        "Self-hosted AI fitness assistant",
        "training",
        "meal",
        "Telegram",
        "optional reviews",
    )
    for copy in required_copy:
        assert copy in html

    assert "Start Chatting" not in html
    assert 'href="#"' not in html


def test_landing_page_is_english_only_and_omits_engineering_section() -> None:
    html = page_html()
    assert "Engineering Docs" not in html
    assert re.search(r"[\u3400-\u4dbf\u4e00-\u9fff]", html) is None


def test_landing_page_has_semantic_navigation_targets() -> None:
    html = page_html()
    assert "<main" in html
    assert "<footer" in html
    assert 'id="how-it-works"' in html
    assert 'id="why-chatfit"' in html
    assert html.count(f'href="{GITHUB_URL}"') >= 2


def test_every_external_link_opens_a_safe_new_tab() -> None:
    external_links = [
        attrs
        for tag, attrs in page_tags()
        if tag == "a" and str(attrs.get("href", "")).startswith(("http://", "https://"))
    ]
    assert external_links

    for link in external_links:
        assert link.get("target") == "_blank"
        assert {"noopener", "noreferrer"} <= set(str(link.get("rel", "")).split())


def test_landing_page_uses_supported_public_claims_and_examples() -> None:
    html = page_html()
    for supported_copy in (
        "Source available",
        "Structured records stay local",
        "Built with Gemini",
        "Add Gemini key",
        "Saved to your training log.",
        "This week: 3 training sessions.",
        "Recorded totals:",
        "Average RPE: 7.",
    ):
        assert supported_copy in html

    unsupported_copy = (
        "open source",
        "open-source",
        "your data stays local",
        "choose your ai model",
        "configure model",
        "weekly training volume is trending up",
        "total volume increased",
        "mobility warm-up were reported as smoother",
    )
    lower_html = html.lower()
    for claim in unsupported_copy:
        assert claim not in lower_html


def test_ordered_lists_preserve_accessible_list_semantics() -> None:
    ordered_lists = [attrs for tag, attrs in page_tags() if tag == "ol"]
    assert len(ordered_lists) == 2
    assert all(attrs.get("role") == "list" for attrs in ordered_lists)

    process_flow = re.search(
        r'<ol\b[^>]*class="process-flow"[^>]*>(.*?)</ol>',
        page_html(),
        flags=re.DOTALL,
    )
    assert process_flow
    process_numbers = re.findall(
        r"<span\b([^>]*)>\s*([1-3])\s*</span>", process_flow.group(1)
    )
    assert [number for _, number in process_numbers] == ["1", "2", "3"]
    assert all('aria-hidden="true"' in attrs for attrs, _ in process_numbers)


def test_landing_page_uses_compact_section_hooks() -> None:
    html = page_html()
    for class_name in (
        "site-nav",
        "hero",
        "conversation",
        "process-flow",
        "benefits",
        "insight-example",
        "deploy-band",
        "site-footer",
    ):
        assert class_name in html


def test_kettlebell_mascot_is_web_ready() -> None:
    assert MASCOT.exists()
    assert MASCOT.stat().st_size < 750_000

    with Image.open(MASCOT) as image:
        assert image.format == "PNG"
        assert image.mode == "RGBA"
        assert image.width == image.height
        assert 256 <= image.width <= 512
        alpha_min, alpha_max = image.getchannel("A").getextrema()
        assert alpha_min == 0
        assert alpha_max == 255


def test_kettlebell_mascot_uses_a_flat_approved_palette() -> None:
    approved_palette = {
        (53, 99, 233),
        (17, 24, 39),
        (255, 255, 255),
    }

    with Image.open(MASCOT).convert("RGBA") as image:
        pixels = cast(Iterable[tuple[int, int, int, int]], image.get_flattened_data())
        used_colors = {pixel[:3] for pixel in pixels if pixel[3] > 0}

    assert used_colors == approved_palette


def test_kettlebell_mascot_is_used_sparingly() -> None:
    html = page_html()
    assert html.count('src="assets/chatfit-kettlebell-buddy.png"') == 2
    assert 'alt="ChatFit kettlebell mascot"' in html


def test_decorative_mascot_precedes_conversation_with_empty_alt() -> None:
    html = page_html()
    assert re.search(
        r'<img class="mascot-peek"[^>]*alt=""[^>]*>\s*<div class="conversation"',
        html,
    )


def test_landing_page_references_local_mascot_as_favicon() -> None:
    html = page_html()
    assert (
        '<link rel="icon" type="image/png" '
        'href="assets/chatfit-kettlebell-buddy.png">'
    ) in html


def test_conversation_input_text_meets_wcag_aa_contrast() -> None:
    html = page_html()
    root_rule = re.search(r":root\s*\{([^}]*)\}", html, flags=re.DOTALL)
    input_rule = re.search(r"\.conversation-input\s*\{([^}]*)\}", html, flags=re.DOTALL)
    assert root_rule
    assert input_rule

    tokens = dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", root_rule.group(1)))
    declarations = dict(
        re.findall(
            r"([\w-]+):\s*(var\(--[\w-]+\)|#[0-9a-fA-F]{6})",
            input_rule.group(1),
        )
    )
    foreground = css_color_value(declarations["color"], tokens)
    background = css_color_value(declarations["background"], tokens)

    assert contrast_ratio(foreground, background) >= 4.5


def test_landing_page_has_no_runtime_style_dependencies() -> None:
    html = page_html()
    assert "cdn.tailwindcss.com" not in html
    assert "fonts.googleapis.com" not in html
    assert "fonts.gstatic.com" not in html


def test_landing_page_declares_selected_visual_tokens() -> None:
    html = page_html()
    for css in (
        "--ink: #101828",
        "--muted: #667085",
        "--cobalt: #3563e9",
        "--cobalt-soft: #edf2ff",
        "--line: #e4e8f0",
        "--surface: #fafbff",
        "--success: #32b76c",
    ):
        assert css in html.lower()


def test_landing_page_has_responsive_and_accessible_css_hooks() -> None:
    html = page_html()
    assert "grid-template-columns: minmax(0, 1.06fr) minmax(0, .94fr)" in html
    assert "@media (max-width: 899px)" in html
    assert "@media (prefers-reduced-motion: reduce)" in html
    assert ":focus-visible" in html
    assert "overflow-x: hidden" in html


def test_landing_page_reserves_desktop_height_headroom() -> None:
    desktop_css = page_html().split("@media (max-width: 899px)", maxsplit=1)[0]
    section_margins = {
        selector: int(value)
        for selector, value in re.findall(
            r"\.(benefits|insight-example|deploy-band)\s*\{[^}]*margin-top:\s*(\d+)px",
            desktop_css,
        )
    }

    assert section_margins.keys() == {
        "benefits",
        "insight-example",
        "deploy-band",
    }
    assert sum(section_margins.values()) <= 100
