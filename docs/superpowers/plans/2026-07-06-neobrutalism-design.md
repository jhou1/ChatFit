# ChatFit Neobrutalism Design Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply a Minimal Brutalist Neobrutalism design to the ChatFit landing page and fix the Node.js 20 GitHub Actions deprecation warning.

**Architecture:** We are updating the `docs/index.html` file using Tailwind CSS classes for thick borders, hard shadows, and solid colors. We are also creating `.github/workflows/pages.yml` to use the latest GitHub Actions.

**Tech Stack:** HTML, Tailwind CSS, GitHub Actions.

**Note:** The user requested NOT to commit any changes. Omit all `git commit` commands from execution.

---

### Task 1: Update HTML Head & Base Body Styling

**Files:**
- Modify: `docs/index.html`

- [ ] **Step 1: Update body and background classes**
Change `body` classes to use `#FBFBF9` and remove the background gradient from the Hero container.
```html
<!-- In docs/index.html around line 21 -->
<body class="bg-[#FBFBF9] text-[#1C293C] antialiased selection:bg-[#FDC800] selection:text-[#1C293C]">

<!-- Around line 24 -->
<div class="relative overflow-hidden bg-[#FBFBF9] pt-24 pb-32 border-b-4 border-[#1C293C]">
```

- [ ] **Step 2: Remove decorative blobs**
Delete lines 25-27 in `docs/index.html` which contain the `.blob` elements.

- [ ] **Step 3: Verify visually**
Run `open docs/index.html` in browser and confirm background is off-white and blobs are gone.

### Task 2: Update Hero Section Content & Buttons

**Files:**
- Modify: `docs/index.html`

- [ ] **Step 1: Update headings and text colors**
Change the text classes to use heavy weights and remove text gradients.
```html
<!-- Around line 38 -->
<h1 class="text-5xl font-black tracking-tight text-[#1C293C] sm:text-7xl mb-6">
    Your AI Fitness Assistant,<br/>
    Right in <span class="bg-[#FDC800] px-2 border-4 border-[#1C293C]">Telegram</span> 🚀
</h1>

<p class="mt-6 text-xl font-medium text-[#1C293C] max-w-2xl mx-auto mb-10 leading-relaxed">
    Tired of adding your sets and reps one by one? Track your training volume and meals through natural chat. Turn your habits into a data goldmine and uncover the hidden patterns holding you back.
</p>
```

- [ ] **Step 2: Update Hero buttons**
Change buttons to have solid colors, thick borders, and hard shadows.
```html
<!-- Around line 47 -->
<div class="flex flex-col sm:flex-row justify-center gap-6 px-4">
    <a href="#" class="bg-[#FDC800] text-[#1C293C] border-4 border-[#1C293C] shadow-[6px_6px_0_0_#1c293c] hover:shadow-[2px_2px_0_0_#1c293c] hover:translate-y-1 hover:translate-x-1 px-8 py-4 text-lg font-black transition-all duration-200">
        Start Chatting
    </a>
    <a href="#developer" class="bg-[#FBFBF9] text-[#1C293C] border-4 border-[#1C293C] shadow-[6px_6px_0_0_#1c293c] hover:shadow-[2px_2px_0_0_#1c293c] hover:translate-y-1 hover:translate-x-1 px-8 py-4 text-lg font-black transition-all duration-200">
        Deploy Your Own
    </a>
</div>
```

- [ ] **Step 3: Verify visually**
Run `open docs/index.html` in browser and confirm buttons have hard shadows and bold borders.

### Task 3: Update Phone Mockup Visual

**Files:**
- Modify: `docs/index.html`

- [ ] **Step 1: Update the phone container and chat bubbles**
Make the mock phone use hard borders, sharp corners, and high-contrast bubble styling.
```html
<!-- Around line 57 -->
<div class="mt-20 flex justify-center px-4">
    <div class="w-full max-w-sm bg-[#FBFBF9] border-4 border-[#1C293C] shadow-[8px_8px_0_0_#432DD7] relative flex flex-col h-[500px]">
        <!-- Phone Top bar -->
        <div class="bg-[#FDC800] pt-4 pb-3 px-6 flex justify-between items-center border-b-4 border-[#1C293C]">
            <div class="flex items-center gap-3">
                <div class="font-black text-xl text-[#1C293C]">🤖 ChatFit bot</div>
            </div>
        </div>

        <!-- Chat Area -->
        <div class="flex-1 bg-[#FBFBF9] p-4 flex flex-col justify-end gap-4 overflow-hidden relative">
            <!-- User message 1 -->
            <div class="bg-[#432DD7] text-white border-2 border-[#1C293C] p-3 w-[90%] self-end text-sm font-bold shadow-[4px_4px_0_0_#1C293C]">
                Today I did kettlebell long cycle with 2x24KG for 60 reps 🏋️‍♂️
            </div>

            <!-- Bot message 1 -->
            <div class="bg-[#FBFBF9] border-2 border-[#1C293C] text-[#1C293C] p-3 w-[90%] self-start text-sm font-bold shadow-[4px_4px_0_0_#1C293C]">
                Impressive! How are you feeling now (your RPE)? What warm up and cool down did you do?
            </div>

            <!-- User message 2 -->
            <div class="bg-[#432DD7] text-white border-2 border-[#1C293C] p-3 w-[90%] self-end text-sm font-bold shadow-[4px_4px_0_0_#1C293C]">
                Mobility and a few rounds of swings as warm up, farmer's carry and walking in cool down. RPE 7
            </div>

            <!-- Bot message 2 -->
            <div class="bg-[#FDC800] border-2 border-[#1C293C] text-[#1C293C] p-3 w-[90%] self-start text-sm font-bold shadow-[4px_4px_0_0_#1C293C]">
                Great job! Your training logs are saved! ✅
            </div>
        </div>

        <!-- Phone Bottom bar -->
        <div class="bg-[#FBFBF9] p-4 border-t-4 border-[#1C293C] flex items-center gap-3">
            <div class="flex-1 bg-white border-2 border-[#1C293C] h-12 px-4 flex items-center text-[#1C293C] font-bold shadow-[4px_4px_0_0_#1C293C]">Message...</div>
        </div>
    </div>
</div>
```

- [ ] **Step 2: Verify visually**
Refresh `docs/index.html` to confirm phone mockup has sharp edges and brutalist chat bubbles.

### Task 4: Update Features Grid

**Files:**
- Modify: `docs/index.html`

- [ ] **Step 1: Update features section container and cards**
Replace rounded cards and soft shadows with brutalist boxes.
```html
<!-- Around line 108 -->
<section class="py-24 relative bg-[#FDC800] border-b-4 border-[#1C293C]">
    <div class="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8">
        <div class="text-center mb-16">
            <h2 class="text-3xl md:text-5xl font-black text-[#1C293C] mb-4">Why you'll love ChatFit</h2>
            <p class="text-xl font-bold text-[#1C293C] max-w-2xl mx-auto">Designed to be simple on the surface, but powerful under the hood.</p>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-2 gap-8">
            <!-- Feature 1 -->
            <div class="bg-[#FBFBF9] p-8 border-4 border-[#1C293C] shadow-[8px_8px_0_0_#1c293c]">
                <div class="text-4xl mb-6">💎</div>
                <h3 class="text-2xl font-black text-[#1C293C] mb-3">Your Data Goldmine</h3>
                <p class="text-[#1C293C] font-medium leading-relaxed">All your training volumes and meal logs are saved securely into a local SQLite database. Your data remains entirely yours.</p>
            </div>
            <!-- Feature 2 -->
            <div class="bg-[#FBFBF9] p-8 border-4 border-[#1C293C] shadow-[8px_8px_0_0_#1c293c]">
                <div class="text-4xl mb-6">🔍</div>
                <h3 class="text-2xl font-black text-[#1C293C] mb-3">Uncover Hidden Patterns</h3>
                <p class="text-[#1C293C] font-medium leading-relaxed">The true value of tracking lies in discovery. Identify performance breakthroughs, root causes of injuries, and how diet correlates with your lifestyle.</p>
            </div>
            <!-- Feature 3 -->
            <div class="bg-[#FBFBF9] p-8 border-4 border-[#1C293C] shadow-[8px_8px_0_0_#1c293c]">
                <div class="text-4xl mb-6">🤖</div>
                <h3 class="text-2xl font-black text-[#1C293C] mb-3">Unopinionated AI Choice</h3>
                <p class="text-[#1C293C] font-medium leading-relaxed">Connect to your favorite provider (OpenAI, Anthropic, DeepSeek) or use a local LLM when data privacy is your top concern.</p>
            </div>
            <!-- Feature 4 -->
            <div class="bg-[#FBFBF9] p-8 border-4 border-[#1C293C] shadow-[8px_8px_0_0_#1c293c]">
                <div class="text-4xl mb-6">💬</div>
                <h3 class="text-2xl font-black text-[#1C293C] mb-3">Seamless Telegram Integration</h3>
                <p class="text-[#1C293C] font-medium leading-relaxed">No extra apps to download. Just chat naturally with your bot on Telegram to log workouts, track meals, and ask for insights.</p>
            </div>
        </div>
    </div>
</section>
```

- [ ] **Step 2: Verify visually**
Refresh `docs/index.html` to confirm features grid has brutalist boxes on yellow background.

### Task 5: Update Developer Section

**Files:**
- Modify: `docs/index.html`

- [ ] **Step 1: Update developer section styles**
Replace dark glowing background with a flat Violet background.
```html
<!-- Around line 145 -->
<section id="developer" class="py-24 px-4 bg-[#FBFBF9]">
    <div class="max-w-5xl mx-auto bg-[#432DD7] border-4 border-[#1C293C] shadow-[12px_12px_0_0_#1C293C] p-8 md:p-16 text-center relative">
        <h2 class="relative z-10 text-3xl md:text-5xl font-black text-[#FBFBF9] mb-6 tracking-tight">Getting Started</h2>
        <p class="relative z-10 text-lg md:text-xl font-bold text-[#FBFBF9] mb-12 max-w-2xl mx-auto">
            ChatFit is completely open-source. Clone the repository and run your own private fitness agent in seconds.
        </p>

        <div class="relative z-10 bg-[#1C293C] border-4 border-[#FBFBF9] shadow-[8px_8px_0_0_#FDC800] text-left p-6 md:p-8 font-mono text-sm md:text-base text-[#FBFBF9] max-w-3xl mx-auto">
            <p class="text-[#FBFBF9] mb-4 font-bold"># Spin up the services with Podman</p>
            <p class="text-[#FDC800] mb-2 flex items-center gap-3 font-bold"><span class="text-[#FDC800]">❯</span> podman compose up -d</p>
        </div>

        <div class="relative z-10 mt-12">
            <a href="https://github.com/hjw/ChatFit" target="_blank" class="inline-flex items-center justify-center gap-2 px-8 py-4 bg-[#FDC800] border-4 border-[#1C293C] text-[#1C293C] font-black shadow-[6px_6px_0_0_#1C293C] hover:shadow-[2px_2px_0_0_#1C293C] hover:translate-y-1 hover:translate-x-1 transition-all">
                View full setup on GitHub
            </a>
        </div>
    </div>
</section>
```

- [ ] **Step 2: Verify visually**
Refresh `docs/index.html` to confirm developer section matches the brutalist look.

### Task 6: Add GitHub Actions Pages Workflow

**Files:**
- Create: `.github/workflows/pages.yml`

- [ ] **Step 1: Write the GitHub Actions workflow**
Create `.github/workflows/pages.yml` to deploy the `docs` folder.
```yaml
name: Deploy GitHub Pages

on:
  push:
    branches:
      - main
    paths:
      - 'docs/**'
  workflow_dispatch:

permissions:
  contents: read
  pages: write
  id-token: write

concurrency:
  group: "pages"
  cancel-in-progress: false

jobs:
  deploy:
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4
      - name: Setup Pages
        uses: actions/configure-pages@v4
      - name: Upload artifact
        uses: actions/upload-pages-artifact@v3
        with:
          path: './docs'
      - name: Deploy to GitHub Pages
        id: deployment
        uses: actions/deploy-pages@v4
```

- [ ] **Step 2: Check syntax**
Run `cat .github/workflows/pages.yml` and ensure it's written properly.
