# ChatFit Landing Page Neobrutalism Design

## 1. Visual Style Update (docs/index.html)

We are applying a "Minimal Brutalist" (Clean & High Contrast) design based on the TypeUI Neobrutalism pattern.

### 1.1 Colors & Tokens
- **Background/Surface**: Off-white `#FBFBF9`
- **Text/Borders/Shadows**: Deep navy-black `#1C293C`
- **Primary Accent**: Yellow `#FDC800` (used for primary buttons, highlights)
- **Secondary Accent**: Violet `#432DD7` (used for secondary buttons or alternative hard shadows)
- **Success/Warning/Danger**: `#16A34A` (Green), `#D97706` (Orange), `#DC2626` (Red)

### 1.2 Component Styling Rules
- **No Gradients or Blurs**: Remove all tailwind gradients (`bg-gradient-to-*`), soft shadows (`shadow-sm`, `shadow-lg`, `shadow-2xl`), blurs, and glassmorphism.
- **Borders**: Interactive elements (buttons, cards) get thick 3px-4px solid borders (`border-[#1C293C] border-4`).
- **Shadows**: Use hard, unblurred offset box shadows (e.g. `shadow-[6px_6px_0_0_#1c293c]` or `shadow-[8px_8px_0_0_#1c293c]`).
- **Typography**: Inter font. Heavy, impactful headings (`font-black`, `tracking-tight`).

### 1.3 Specific Section Changes
- **Hero**: Solid off-white background. The gradient text changes to solid color or highlighted with a yellow background block. 
- **Buttons**:
  - Primary (Start Chatting): Yellow background, thick border, hard shadow.
  - Secondary (Deploy Your Own): Off-white background, thick border, hard shadow.
- **Mockup (Phone)**: Remove the rounded, soft shadow look. Use thick borders, sharp edges, and solid colors. Chat bubbles get hard borders and flat colors.
- **Features Grid**: Cards have off-white backgrounds, thick borders, and hard shadows. Icons use solid accent colors.
- **Developer Section**: Remove the dark gradient/glow. Use a solid Violet or Navy background with high contrast Yellow/White text and heavy borders for the code block.

## 2. GitHub Actions Fix

The repository relies on GitHub's implicit/classic Pages action which triggers a Node 20 deprecation warning on Node 24 runners.

### 2.1 Workflow Implementation
Create a custom GitHub Actions workflow to deploy the `docs` folder explicitly, bypassing the deprecated internal action.

- **File**: `.github/workflows/pages.yml`
- **Triggers**: On push to `main` branch.
- **Permissions**: Read contents, write pages, write id-token.
- **Steps**:
  1. `actions/checkout@v4` (latest, compatible)
  2. `actions/upload-pages-artifact@v3` (configured to upload the `docs/` path)
  3. `actions/deploy-pages@v4` (deploys the uploaded artifact)

This will resolve the `Node.js 20 is deprecated` warnings.
