# ChatFit README and Landing Page Design Spec

## 1. Overview
Update the ChatFit project to include a professional English README introduction and a dedicated GitHub Pages landing page. The goal is to pitch the fitness agent to end-users while providing a clear off-ramp for developers who wish to self-host.

## 2. README Updates
Replace the current Chinese introduction with an English-only overview.

**Proposed Copy:**
> **ChatFit** is your personal, AI-powered fitness and nutrition coach on Telegram.
>
> It tracks your workouts, monitors your diet, and provides personalized insights to help you reach your goals. By leveraging structured data and your choice of LLM (OpenAI, Anthropic, DeepSeek, or local), ChatFit helps you identify blind spots in your training and securely remembers your preferences over time.

## 3. Landing Page Design (SaaS Classic)
A static, responsive single-page site designed to convert users.

### 3.1 Tech Stack
- Single `index.html` file (placed in a `docs` or `public` folder depending on GH Pages config, or root)
- Tailwind CSS (via CDN) for styling
- Hosted on GitHub Pages

### 3.2 Structure
**1. Hero Section**
- **Headline:** "Your AI Fitness Coach, Right in Telegram."
- **Sub-headline:** "Track workouts, monitor your diet, and hit your goals with a personalized AI agent that lets you own your data."
- **CTAs:** 
  - Primary: [Start Chatting] (Links to Telegram bot)
  - Secondary: [Deploy Your Own] (Anchors to developer section)
- **Visual:** A minimalist CSS/HTML mockup of a phone showing a Telegram chat with the bot.

**2. Features Grid**
- **Own Your Data:** Structured storage for workouts and diet.
- **Bring Your Own LLM:** Support for OpenAI, Anthropic, DeepSeek, or local models.
- **Smart Insights:** Automatically identifies blind spots in your training.
- **Persistent Memory:** Learns your preferences and adapts over time.

**3. Developer / Self-Hosting Section**
- "Built for Developers" heading.
- Code snippets showing how to run: `uv run python bot.py` and `podman compose up`.
- CTA: [View on GitHub]

## 4. Considerations
- The Telegram bot link will initially be a `#` placeholder until a public bot is registered.
- `index.html` will be created in the root of the project to allow serving from the `main` branch via GitHub Pages.