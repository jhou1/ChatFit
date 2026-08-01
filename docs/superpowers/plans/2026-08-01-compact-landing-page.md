# Compact ChatFit Landing Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current four-viewport neobrutalist GitHub Pages landing page with an English-only, crisp-cobalt product page that fits within two desktop viewport heights and truthfully directs visitors to self-host ChatFit.

**Architecture:** Keep the site as one dependency-free static HTML document with semantic sections, inline maintainable CSS, and one optimized transparent mascot asset. Add Python acceptance tests that validate durable content, links, dependencies, responsive hooks, and asset properties; use browser rendering for the visual height and overflow acceptance checks that static tests cannot prove.

**Tech Stack:** HTML5, CSS3, Pillow-backed pytest checks, Python `http.server`, GitHub Pages

## Global Constraints

- At 1440×900, total rendered document height must be at most 1800 CSS pixels.
- At 1920×1080, total rendered document height must be at most 2160 CSS pixels.
- The complete desktop hero, including the three-step flow, must fit in the first viewport.
- The desktop hero uses two columns and stacks below approximately 900px.
- Mobile is allowed to exceed two viewport heights but must not overflow horizontally.
- Use English only and remove the Engineering Docs landing-page section.
- The primary CTA is “Deploy on GitHub”; do not claim a public hosted bot exists.
- Use `#3563E9` as the single primary accent and avoid thick borders, hard shadows, saturated yellow panels, and decorative rotations.
- Remove Tailwind CDN and Google Fonts; use semantic HTML, inline CSS, and a native system font stack.
- Keep technical engineering documentation in the repository and make no backend behavior changes.

## File structure

- Modify `docs/index.html`: complete semantic landing-page content, navigation, conversation example, benefits, deployment CTA, responsive CSS, accessibility states, and metadata.
- Create `docs/assets/chatfit-kettlebell-buddy.png`: optimized transparent mascot used by the wordmark and hero.
- Create `tests/test_landing_page.py`: static acceptance tests for product truthfulness, English-only copy, links, dependencies, responsive hooks, and mascot properties.

---

### Task 1: Replace the page hierarchy and copy

**Files:**
- Create: `tests/test_landing_page.py`
- Modify: `docs/index.html`

**Interfaces:**
- Consumes: implemented ChatFit capabilities documented in `README.md`—Telegram text, voice, OCR-assisted photo input, training and meal records, local structured storage, the current Gemini provider, and optional reviews.
- Produces: semantic anchors `#how-it-works` and `#why-chatfit`; reusable class hooks `.site-nav`, `.hero`, `.conversation`, `.process-flow`, `.benefits`, `.insight-example`, `.deploy-band`, and `.site-footer` for Task 3.

- [ ] **Step 1: Add failing copy and structure acceptance tests**

Create `tests/test_landing_page.py` with the following initial content:

```python
import re
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "docs" / "index.html"
GITHUB_URL = "https://github.com/hjw/ChatFit"


class StartTagCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self.tags.append((tag, dict(attrs)))


def page_html() -> str:
    return INDEX.read_text(encoding="utf-8")


def page_tags() -> list[tuple[str, dict[str, str | None]]]:
    parser = StartTagCollector()
    parser.feed(page_html())
    return parser.tags


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
    assert '<main' in html
    assert '<footer' in html
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
```

- [ ] **Step 2: Run the tests and confirm they fail against the old page**

Run:

```bash
uv run pytest tests/test_landing_page.py -v
```

Expected: failures for the new headline, primary CTA, section anchors, compact
class hooks, English-only requirement, and removed Engineering Docs section.

- [ ] **Step 3: Replace `docs/index.html` with the semantic page skeleton and exact product copy**

Use this document hierarchy and copy. Keep CSS limited to a small reset until
Task 3 so this task reviews content separately from visual polish.

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="ChatFit is a self-hosted AI fitness assistant that turns Telegram messages into useful training and meal history.">
  <title>ChatFit — Fitness logging that feels like chat</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; }
    html { scroll-behavior: smooth; }
    body { margin: 0; font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
  </style>
</head>
<body>
  <header class="site-nav" aria-label="Primary navigation">
    <a class="brand" href="#top" aria-label="ChatFit home"><span>ChatFit</span></a>
    <nav aria-label="Landing page">
      <a href="#how-it-works">How it works</a>
      <a href="#why-chatfit">Why ChatFit</a>
      <a href="https://github.com/hjw/ChatFit" target="_blank" rel="noopener noreferrer">GitHub</a>
    </nav>
  </header>

  <main id="top">
    <section class="hero" aria-labelledby="hero-title">
      <div class="hero-copy">
        <p class="eyebrow">Self-hosted AI fitness assistant</p>
        <h1 id="hero-title">Just tell it <span>what you did.</span></h1>
        <p class="hero-summary">ChatFit turns casual training and meal messages into structured history—then helps you notice the patterns that move you forward.</p>
        <div class="hero-actions">
          <a class="button button-primary" href="https://github.com/hjw/ChatFit" target="_blank" rel="noopener noreferrer">Deploy on GitHub <span aria-hidden="true">→</span></a>
          <a class="text-link" href="#how-it-works">See it in action <span aria-hidden="true">↓</span></a>
        </div>
        <p class="trust-line">Source available · Structured records stay local · Built with Gemini</p>
      </div>

      <div class="conversation" aria-label="Example ChatFit conversation">
        <div class="conversation-header"><span class="status-dot" aria-hidden="true"></span><strong>ChatFit bot</strong></div>
        <div class="messages">
          <p class="message message-user">Long cycle today: 2×24 kg, 60 reps.</p>
          <p class="message message-bot">Nice session! What was your RPE, and how did you warm up?</p>
          <p class="message message-user">RPE 7. Mobility and a few rounds of swings.</p>
          <p class="message message-insight">Saved to your training log.</p>
        </div>
        <div class="conversation-input" aria-hidden="true">Message ChatFit… <span>↑</span></div>
      </div>

      <ol id="how-it-works" class="process-flow" aria-label="How ChatFit works" role="list">
        <li><span aria-hidden="true">1</span><div><strong>Message naturally</strong><small>Text, voice, or OCR-assisted photo</small></div></li>
        <li><span aria-hidden="true">2</span><div><strong>ChatFit fills the gaps</strong><small>Helpful follow-up questions</small></div></li>
        <li><span aria-hidden="true">3</span><div><strong>Your history becomes useful</strong><small>Patterns, trends, and reviews</small></div></li>
      </ol>
    </section>

    <section id="why-chatfit" class="value-section" aria-labelledby="value-title">
      <header class="section-heading">
        <p>Simple in use, useful over time</p>
        <h2 id="value-title">Less form-filling. More understanding.</h2>
        <p>ChatFit keeps the interface conversational while quietly building the fitness history you never had time to maintain.</p>
      </header>

      <div class="benefits">
        <article><h3>Log your way</h3><p>Send training or meal details as text, voice, or OCR-assisted photos—directly in Telegram.</p></article>
        <article><h3>Keep clean records</h3><p>ChatFit asks for missing details, confirms the entry, and stores structured data locally.</p></article>
        <article><h3>Notice what matters</h3><p>Use accumulated history to uncover training, recovery, and nutrition patterns over time.</p></article>
      </div>

      <div class="insight-example">
        <div><p>The payoff</p><h3>Your data becomes a conversation, not a spreadsheet.</h3><p>Ask about recent progress or enable optional reviews. ChatFit turns your own history into context you can act on.</p></div>
        <article aria-label="Example optional weekly review"><small>Example optional weekly review</small><h3>This week: 3 training sessions.</h3><p>Recorded totals: 4,820 kg volume, 42 minutes, 5 km, and 18 sets. Average RPE: 7.</p></article>
      </div>

      <div class="deploy-band">
        <div><h2>Make ChatFit yours.</h2><p>Create a Telegram bot and deploy the source-available app.</p></div>
        <ol role="list"><li>Create bot</li><li>Add Gemini key</li><li>Run with Podman</li></ol>
        <a class="button button-secondary" href="https://github.com/hjw/ChatFit" target="_blank" rel="noopener noreferrer">View setup on GitHub <span aria-hidden="true">→</span></a>
      </div>
    </section>
  </main>

  <footer class="site-footer"><strong>ChatFit</strong><span>Source available · Built for fitness and data enthusiasts</span></footer>
</body>
</html>
```

- [ ] **Step 4: Run the landing-page tests**

Run:

```bash
uv run pytest tests/test_landing_page.py -v
```

Expected: all Task 1 tests pass.

- [ ] **Step 5: Commit the semantic redesign**

```bash
git add docs/index.html tests/test_landing_page.py
git commit -m "feat: restructure ChatFit landing page"
```

---

### Task 2: Add the approved kettlebell mascot

**Files:**
- Create: `docs/assets/chatfit-kettlebell-buddy.png`
- Modify: `docs/index.html`
- Modify: `tests/test_landing_page.py`

**Interfaces:**
- Consumes: the `.brand` and `.conversation` hooks created in Task 1.
- Produces: a square RGBA PNG at `docs/assets/chatfit-kettlebell-buddy.png`, used at 36px in the wordmark and as one larger decorative hero “peek.”

- [ ] **Step 1: Add failing asset and markup tests**

Append these imports and tests to `tests/test_landing_page.py`:

```python
from PIL import Image


MASCOT = ROOT / "docs" / "assets" / "chatfit-kettlebell-buddy.png"


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


def test_kettlebell_mascot_is_used_sparingly() -> None:
    html = page_html()
    assert html.count('src="assets/chatfit-kettlebell-buddy.png"') == 2
    assert 'alt="ChatFit kettlebell mascot"' in html
```

- [ ] **Step 2: Run the mascot tests and confirm they fail**

Run:

```bash
uv run pytest tests/test_landing_page.py::test_kettlebell_mascot_is_web_ready tests/test_landing_page.py::test_kettlebell_mascot_is_used_sparingly -v
```

Expected: failures because the asset and image elements do not exist.

- [ ] **Step 3: Generate and prepare the approved mascot asset**

Use the built-in ImageGen path with this production prompt:

```text
Use case: logo-brand
Asset type: small landing-page mascot and wordmark avatar
Primary request: Create the approved ChatFit kettlebell buddy: a compact friendly cobalt kettlebell character with a subtle smiling face, one tiny raised arm, and a confident welcoming pose.
Style/medium: polished flat vector-like brand illustration; very simple geometric shapes; modern minimal product identity; witty rather than childish.
Composition/framing: one centered full character, generous padding, readable at 36 pixels.
Color palette: crisp cobalt blue #3563E9, deep navy #111827, small white facial highlights, and no additional accent color.
Scene/backdrop: perfectly flat solid #00ff00 chroma-key background for removal, with no shadows, gradients, texture, floor, or lighting variation.
Constraints: keep every part of the character separated from the background; do not use #00ff00 in the subject; no text, letters, watermark, cast shadow, contact shadow, reflection, thick outline, or 3D rendering.
```

Copy the generated source into `/tmp/chatfit-kettlebell-source.png`, then use the
installed image-generation background-removal helper:

```bash
mkdir -p docs/assets
uv run python /Users/hjw/.codex/skills/.system/imagegen/scripts/remove_chroma_key.py \
  --input /tmp/chatfit-kettlebell-source.png \
  --out /tmp/chatfit-kettlebell-transparent.png \
  --auto-key border \
  --soft-matte \
  --transparent-threshold 12 \
  --opaque-threshold 220 \
  --despill
```

Resize and optimize the RGBA output deterministically:

```python
from pathlib import Path
from PIL import Image

source = Path("/tmp/chatfit-kettlebell-transparent.png")
destination = Path("docs/assets/chatfit-kettlebell-buddy.png")

with Image.open(source).convert("RGBA") as image:
    image.thumbnail((512, 512), Image.Resampling.LANCZOS)
    square = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    square.alpha_composite(image, ((512 - image.width) // 2, (512 - image.height) // 2))
    square.save(destination, optimize=True)
```

Inspect the final PNG and retry once with `--edge-contract 1` if a green fringe
is visible. Confirm transparent corners, clean cobalt edges, and readability at
36px.

- [ ] **Step 4: Add the mascot to the wordmark and hero**

Update the brand and conversation markup so the asset appears exactly twice:

```html
<a class="brand" href="#top" aria-label="ChatFit home">
  <img src="assets/chatfit-kettlebell-buddy.png" alt="ChatFit kettlebell mascot" width="36" height="36">
  <span>ChatFit</span>
</a>
```

Add this decorative instance immediately before `.conversation`:

```html
<img class="mascot-peek" src="assets/chatfit-kettlebell-buddy.png" alt="" width="128" height="128">
```

- [ ] **Step 5: Run the mascot tests**

Run:

```bash
uv run pytest tests/test_landing_page.py -v
```

Expected: all landing-page tests pass.

- [ ] **Step 6: Commit the mascot**

```bash
git add docs/assets/chatfit-kettlebell-buddy.png docs/index.html tests/test_landing_page.py
git commit -m "feat: add ChatFit kettlebell mascot"
```

---

### Task 3: Apply the crisp-cobalt responsive presentation

**Files:**
- Modify: `docs/index.html`
- Modify: `tests/test_landing_page.py`

**Interfaces:**
- Consumes: all semantic class hooks from Task 1 and the transparent mascot from Task 2.
- Produces: a dependency-free responsive layout with the CSS variables `--ink`, `--muted`, `--cobalt`, `--cobalt-soft`, `--line`, `--surface`, and `--success`.

- [ ] **Step 1: Add failing visual-contract tests**

Append these tests to `tests/test_landing_page.py`:

```python
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
```

- [ ] **Step 2: Run the visual-contract tests and confirm they fail**

Run:

```bash
uv run pytest tests/test_landing_page.py::test_landing_page_declares_selected_visual_tokens tests/test_landing_page.py::test_landing_page_has_responsive_and_accessible_css_hooks -v
```

Expected: failures because the complete design tokens and responsive CSS are not
yet present.

- [ ] **Step 3: Define the global visual system**

Replace the temporary styles with a single inline stylesheet. Begin with these
exact tokens and global rules:

```css
:root {
  --ink: #101828;
  --muted: #667085;
  --cobalt: #3563e9;
  --cobalt-soft: #edf2ff;
  --line: #e4e8f0;
  --surface: #fafbff;
  --success: #32b76c;
  --content: 1160px;
}

*, *::before, *::after { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  margin: 0;
  overflow-x: hidden;
  color: var(--ink);
  background: #fff;
  font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  -webkit-font-smoothing: antialiased;
}
a { color: inherit; text-decoration: none; }
a:focus-visible { outline: 3px solid var(--cobalt); outline-offset: 4px; }
```

- [ ] **Step 4: Implement the desktop two-viewport layout**

Use a 72px navigation, a first section sized to fit within the remaining initial
viewport, and a compact second section. The essential layout declarations are:

```css
.site-nav {
  width: min(calc(100% - 48px), var(--content));
  height: 72px;
  margin: 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.brand { display: inline-flex; align-items: center; gap: 10px; font-weight: 800; }
.brand img { width: 36px; height: 36px; object-fit: contain; }
.site-nav nav { display: flex; align-items: center; gap: 28px; color: var(--muted); }

.hero {
  min-height: calc(100vh - 72px);
  max-width: var(--content);
  margin: 0 auto;
  padding: clamp(42px, 6vh, 66px) 24px 38px;
  display: grid;
  grid-template-columns: minmax(0, 1.06fr) minmax(0, .94fr);
  grid-template-areas: "copy chat" "flow flow";
  column-gap: clamp(48px, 7vw, 82px);
  align-items: center;
}
.hero-copy { grid-area: copy; }
.conversation { grid-area: chat; position: relative; }
.process-flow { grid-area: flow; }
.hero h1 { margin: 0; max-width: 620px; font-size: clamp(52px, 5vw, 72px); line-height: .98; letter-spacing: -.06em; }
.hero h1 span { color: var(--cobalt); }

.process-flow,
.benefits {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
}
.value-section { padding: clamp(64px, 8vh, 88px) 24px 44px; }
.value-section > * { width: min(100%, var(--content)); margin-inline: auto; }
.insight-example { display: grid; grid-template-columns: .9fr 1.1fr; }
.deploy-band { display: grid; grid-template-columns: .85fr 1.25fr auto; }
```

Complete the selected design using the spec tokens:

- white-to-cool-white hero surface;
- 14–24px radii on the conversation, insight example, and deployment band;
- soft low-opacity shadows only on the conversation and sample review;
- cobalt primary pill CTA;
- three benefits separated by top rules rather than boxed cards;
- pale-cobalt insight surface;
- deep-ink deployment band; and
- compact footer immediately below the deployment band.

Keep total desktop vertical padding within the height budget. Do not reintroduce
a standalone phone frame, yellow section, thick border, or hard offset shadow.

- [ ] **Step 5: Implement mobile stacking and reduced motion**

Add the required narrow-screen behavior:

```css
@media (max-width: 899px) {
  .site-nav { width: min(calc(100% - 32px), var(--content)); }
  .site-nav nav a:not(:last-child) { display: none; }
  .hero {
    min-height: auto;
    padding: 44px 20px 56px;
    grid-template-columns: 1fr;
    grid-template-areas: "copy" "chat" "flow";
    gap: 38px;
  }
  .hero h1 { font-size: clamp(44px, 13vw, 60px); }
  .process-flow, .benefits, .insight-example, .deploy-band { grid-template-columns: 1fr; }
  .process-flow { gap: 20px; }
  .mascot-peek { width: 88px; height: 88px; }
  .value-section { padding: 64px 20px 40px; }
  .site-footer { padding-inline: 20px; }
}

@media (prefers-reduced-motion: reduce) {
  html { scroll-behavior: auto; }
  *, *::before, *::after { transition-duration: .01ms !important; }
}
```

Use fluid widths, `minmax(0, 1fr)`, and wrapping CTA/step containers so a 390px
viewport has no horizontal overflow.

- [ ] **Step 6: Run static landing-page tests**

Run:

```bash
uv run pytest tests/test_landing_page.py -v
```

Expected: all tests pass.

- [ ] **Step 7: Serve and inspect desktop and mobile renderings**

Start a local server:

```bash
python3 -m http.server 4173 --directory docs
```

Open `http://127.0.0.1:4173/` with the browser-control workflow and inspect at
1440×900, 1920×1080, and 390×844. At each size, evaluate:

```javascript
({
  width: window.innerWidth,
  height: window.innerHeight,
  scrollWidth: document.documentElement.scrollWidth,
  scrollHeight: document.documentElement.scrollHeight,
  viewportRatio: document.documentElement.scrollHeight / window.innerHeight
})
```

Expected:

- 1440×900: `viewportRatio <= 2` and `scrollWidth === width`.
- 1920×1080: `viewportRatio <= 2` and `scrollWidth === width`.
- 390×844: `scrollWidth === width`; all content is readable and ordered headline,
  actions, conversation, process, benefits, insight, deployment, footer.
- Desktop hero conversation and process flow are visible within the first
  viewport.
- The mascot is clear at 36px and does not obscure chat content at any size.

If either desktop exceeds the height limit, reduce section padding and gaps
before reducing body text below 15px or removing required content.

- [ ] **Step 8: Commit the completed visual redesign**

```bash
git add docs/index.html tests/test_landing_page.py
git commit -m "feat: apply compact cobalt landing page"
```

---

### Task 4: Run repository and independent verification

**Files:**
- Verify: `docs/index.html`
- Verify: `docs/assets/chatfit-kettlebell-buddy.png`
- Verify: `tests/test_landing_page.py`
- Reference: `docs/quality.md`

**Interfaces:**
- Consumes: the completed static page and acceptance tests from Tasks 1–3.
- Produces: a clean worktree with repository quality gates, backend tests, static landing-page tests, link checks, and visual acceptance checks all passing.

- [ ] **Step 1: Run focused and full automated verification**

```bash
uv run pytest tests/test_landing_page.py -v
make quality
make verify
git diff --check
```

Expected: every command exits zero, `make verify` reports all non-e2e tests
passing, and no formatter changes remain unstaged.

- [ ] **Step 2: Check the final static contract directly**

```bash
rg -n "Engineering Docs|Start Chatting|href=\"#\"|cdn.tailwindcss.com|fonts.googleapis.com|[一-龥]" docs/index.html
```

Expected: no matches.

```bash
rg -n "Deploy on GitHub|View setup on GitHub|id=\"how-it-works\"|id=\"why-chatfit\"|chatfit-kettlebell-buddy.png" docs/index.html
```

Expected: matches for both CTAs, both anchors, and exactly two mascot image uses.

- [ ] **Step 3: Dispatch the required independent verifier**

Spawn a verification subagent with this exact brief:

```text
Independently verify the compact ChatFit landing-page implementation in this worktree. Read AGENTS.md and docs/quality.md first. Run make quality and make verify, inspect docs/index.html against docs/superpowers/specs/2026-08-01-compact-landing-page-design.md, run tests/test_landing_page.py, and verify desktop height/overflow at 1440×900 and 1920×1080 plus mobile overflow at 390×844. Report every error, failure, or warning; do not modify files.
```

If the verifier reports any error, failure, or warning, fix it in the relevant
task scope, rerun the focused tests, and dispatch another independent verification
round. Continue until the verifier reports no findings.

- [ ] **Step 4: Confirm clean final branch state**

```bash
git status --short
git log --oneline --decorate -5
```

Expected: no uncommitted changes and the design plus implementation commits are
present on `codex/compact-landing-page`.
