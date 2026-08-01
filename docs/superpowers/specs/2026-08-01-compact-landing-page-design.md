# Compact ChatFit Landing Page Design

## Purpose

Redesign the GitHub Pages landing page so a new visitor immediately understands
that ChatFit makes fitness logging effortless through natural Telegram
conversation and turns accumulated records into useful personal insights.

ChatFit is not a public hosted bot. Visitors must create their own Telegram bot
and deploy the program, so the page must present self-hosting honestly and make
deployment—not instant chatting—the primary action.

## Goals

- Reduce the desktop page from roughly four viewport heights to no more than two.
- Use desktop width instead of stacking the value proposition above a tall phone
  mockup.
- Make effortless logging the first impression, followed by personal insight and
  a small amount of personality.
- Replace the current heavy neobrutalist styling with a clean, minimal cobalt
  theme.
- Use a friendly kettlebell mascot as the distinctive playful element.
- Keep the page entirely in English.
- Remove the Engineering Docs section from the landing page.
- Give visitors a clear and truthful route to the GitHub deployment instructions.

## Non-goals

- No public hosted ChatFit bot or “start chatting now” flow.
- No new backend behavior, deployment mechanism, or documentation system.
- No dashboard UI that implies ChatFit ships a web analytics dashboard.
- No removal of engineering Markdown files from the repository; only their
  landing-page section is removed.
- No strict two-viewport requirement on phones, where natural vertical scrolling
  is preferable to compressed content.

## Audience and message hierarchy

The primary audience is a technically comfortable fitness enthusiast willing to
self-host a Telegram bot. The page communicates these ideas in order:

1. Logging workouts and meals can feel as easy as sending a message.
2. ChatFit turns those messages into structured history and useful patterns.
3. ChatFit is source available, stores structured records locally, and is built
   with Gemini.
4. The visitor can create a Telegram bot and deploy ChatFit from GitHub.

The first-view headline is **“Just tell it what you did.”** Supporting copy
explains that ChatFit turns casual workout and meal messages into structured
history and helps the user notice patterns over time.

## Selected visual direction

The selected direction is **Crisp Cobalt** with the **Kettlebell Buddy** mascot.

### Visual tokens

- Canvas: white `#FFFFFF` fading subtly to cool white `#FAFBFF`
- Primary text: deep ink `#101828`
- Secondary text: slate `#667085`
- Primary accent: cobalt `#3563E9`
- Pale accent surface: `#EDF2FF`
- Dividers and borders: cool gray `#E4E8F0`
- Success indicator: green `#32B76C`
- Corner radius: 14–24px for conversational surfaces; pill radius for calls to
  action
- Shadows: soft, low-opacity depth used only for the Telegram conversation and
  insight example

The redesign removes thick borders, hard offset shadows, saturated yellow
panels, decorative rotations, and competing card treatments. It uses one accent
color, generous internal spacing, strong type hierarchy, and thin dividers.

Typography uses a native system sans-serif stack to avoid a render-blocking font
dependency. Headings are bold and tightly tracked; body copy remains open and
readable.

### Mascot usage

The mascot is a friendly cobalt kettlebell with a simple face and small energetic
gesture. It supplies the page's fun without requiring jokes, emoji decoration,
or multiple bright colors.

Use it only in two places:

- a small avatar beside the ChatFit wordmark; and
- one larger “peek” beside the hero conversation.

The production asset should retain the approved cheerful character while being
prepared on a transparent background and optimized for the web. It must remain
recognizable at approximately 36px and must not dominate the hero.

## Page structure

### Navigation

A compact navigation bar contains:

- ChatFit wordmark and kettlebell avatar;
- “How it works,” which scrolls to the conversation-to-insight explanation;
- “Why ChatFit,” which scrolls to the benefit row; and
- “GitHub,” which opens the repository in a new tab.

The navigation does not need to be sticky. On narrow screens, retain the brand
and GitHub link and hide the two in-page links if they cannot fit cleanly.

### Viewport one: product promise and proof

On desktop, the hero is a balanced two-column layout:

- **Left:** self-hosted product eyebrow, headline, short explanation, primary
  deployment CTA, secondary “See it in action” link, and one restrained trust
  line.
- **Right:** a compact Telegram-style conversation showing natural input, one
  follow-up question, and a plain save acknowledgment.

The primary CTA reads **“Deploy on GitHub”** and links to the repository setup
instructions. The secondary CTA scrolls to the next section. There is no dead
`href="#"` and no “Start Chatting” wording.

Below the two columns, a quiet horizontal flow explains:

1. Message naturally—text, voice, or OCR-assisted photo.
2. ChatFit asks for missing details.
3. Structured history becomes patterns, trends, and reviews.

This replaces the current 600px standalone phone mockup and uses the available
horizontal space.

### Viewport two: benefits, insight payoff, and deployment

The second viewport opens with the message **“Less form-filling. More
understanding.”** Three lightweight columns replace the five-card feature wall:

- **Log your way:** training or meal input through Telegram text, voice, or
  OCR-assisted photos.
- **Keep clean records:** follow-up, confirmation, and local structured storage.
- **Notice what matters:** patterns across training, recovery, and nutrition
  history.

A single example optional weekly-review panel demonstrates the long-term payoff
with supported current-week summaries such as session count, recorded volume,
duration, distance, sets, and average RPE. It does not infer correlations or
claim comparisons that the example data does not establish. It is visually
presented as a Telegram-style response rather than a dashboard, so it does not
promise an interface the product does not provide.

The page ends with a compact dark deployment band:

1. Create a bot with BotFather.
2. Add the Gemini API key.
3. Run the services with Podman.

Its CTA reads **“View setup on GitHub.”** A minimal footer follows immediately.
There is no separate full-screen developer section.

## Height and responsive constraints

“Two scrolls” is defined as at most two viewport heights of total document
content on a common desktop viewport, including the footer:

- at 1440×900, total rendered document height should be no more than 1800 CSS
  pixels;
- at 1920×1080, total rendered document height should be no more than 2160 CSS
  pixels; and
- the complete hero, including its three-step flow, should fit within the first
  viewport at these sizes.

Desktop content uses a maximum width around 1160px and a two-column hero at
approximately 54/46. The layout stacks below roughly 900px. On mobile, content
order is headline and CTAs, conversation, three-step flow, benefits, insight,
deployment, and footer. Mobile content must remain comfortable and complete even
when it exceeds two viewport heights.

No viewport may produce horizontal scrolling.

## Content requirements

- English only; remove the current Chinese feature and deployment paragraphs.
- Keep claims aligned with implemented capabilities in the README and code.
- Describe the repository as source available rather than open source unless a
  license is added in the future.
- Say that structured records are stored locally; do not imply Telegram or
  Gemini processing keeps all user content local.
- Describe Gemini as the current provider; do not advertise model choice as a
  deployment setting.
- Mention training and meal logging rather than presenting ChatFit as only a
  strength-training tool.
- Describe optional reviews as optional; do not imply they are enabled by
  default.
- Do not claim deployment takes “seconds” or promise a public service.
- Keep technical architecture, evaluation, observability, and quality material
  in GitHub documentation rather than on the landing page.

## Technical approach

The change is isolated to the static GitHub Pages surface:

- rewrite `docs/index.html` with semantic HTML and small, maintainable CSS;
- remove the Tailwind CDN and Google Fonts dependencies;
- add one optimized mascot asset under `docs/assets/`;
- use no framework and no application JavaScript beyond optional native smooth
  in-page navigation; and
- preserve the existing GitHub Pages deployment mechanism.

This keeps the page fast, self-contained, and independent of backend services.

## Accessibility and resilience

- Maintain at least WCAG AA text contrast.
- Use one `h1`, ordered section headings, semantic links, sections, and footer.
- Provide meaningful alt text for the mascot when it conveys identity and empty
  alt text if a repeated instance is purely decorative.
- Provide visible keyboard focus states for all links.
- Respect `prefers-reduced-motion`; do not require animation to understand the
  page.
- If the mascot fails to load, the wordmark and all content remain usable.
- External GitHub links use safe new-tab attributes when opened in a new tab.
- Native fonts provide an immediate fallback without layout shift.

## Verification

Implementation verification must include:

1. Run the repository-required `make quality` gate and the full test suite.
2. Render the page at 1440×900 and 1920×1080 and verify the total height is at
   most two viewport heights.
3. Render at a representative phone size such as 390×844 and confirm there is no
   horizontal overflow, clipped content, or illegible copy.
4. Confirm the hero uses two columns on desktop and stacks in the specified order
   on mobile.
5. Confirm all CTAs and in-page links work and no `href="#"` placeholder remains.
6. Confirm “Engineering Docs” and the mixed-language paragraphs are absent.
7. Confirm the mascot remains clear at wordmark size and does not obscure the
   conversation.
8. Confirm keyboard focus visibility and reduced-motion behavior.

No backend or agent behavior should change, so existing backend tests must remain
green.
