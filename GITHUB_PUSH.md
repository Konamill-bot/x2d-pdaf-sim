# How to push to GitHub when ready

DO NOT push until Hasselblad has responded to Letter 1. Reasoning is
in `LETTER_2_DRAFT.md` ("When to send" section).

## Decision tree

After Hasselblad replies, pick one of three scenarios:

| Their response          | Push timing                | Letter 2 timing            |
|-------------------------|----------------------------|----------------------------|
| Open / interested       | Push, then send Letter 2 with repo link | Send within 1-2 days |
| Cautious / non-committal| Push, but keep repo URL private until they reply to Letter 2 | Send 3-5 days later |
| Hard no / silence > 10d | Push public, send Letter 2 with repo link as last contact | Day 10-14            |

## One-time setup (only do this once you've decided to push)

1. Create a GitHub account if needed: https://github.com/signup
2. On GitHub: New repository
   - Name: `x2d-pdaf-sim`
   - Visibility: Public
   - DO NOT initialize with README / .gitignore / LICENSE
     (we already have all three locally)
   - Click "Create repository"
3. Copy your GitHub username -- you'll need it below.

## Push from this machine

Replace `<YOUR_USERNAME>` with your GitHub username:

```powershell
cd C:\Users\Konamill\x2d-pdaf-sim

# Configure your git identity (one time, can match your GitHub email)
git config user.name "Chan Kam Chi"
git config user.email "your-github-email@example.com"

# Add the GitHub remote and push
git remote add origin https://github.com/<YOUR_USERNAME>/x2d-pdaf-sim.git
git branch -M main
git push -u origin main
```

GitHub will ask for authentication. Use a Personal Access Token,
not your password (GitHub disabled password auth in 2021):
  https://github.com/settings/tokens
  -> Generate new token (classic)
  -> scope: `repo`
  -> copy and paste when git push prompts

## After push

1. Visit `https://github.com/<YOUR_USERNAME>/x2d-pdaf-sim` and confirm
   the README renders, the `out/stacked_comparison.png` displays.
2. Update Letter 2 draft: replace `<your-username>` placeholder with
   real GitHub username in `LETTER_2_DRAFT.md` (both English and
   Chinese sections).
3. Add the GitHub link to your own bio / `x2d-cim-notes` repo if you
   want to cross-link them.

## Subsequent commits

Whenever you add real-camera data (Test D/E/F results) or update
FINDINGS:

```powershell
cd C:\Users\Konamill\x2d-pdaf-sim
git add -A
git commit -m "Short description of change"
git push
```

## If you want to keep the repo private at first

You can create the GitHub repo as Private, push to it, and only flip
to Public later. This lets you check everything renders correctly
before anyone sees it. To flip:

  GitHub repo page -> Settings -> scroll to bottom -> "Change
  repository visibility" -> Public.

Keeping it private is also fine if Hasselblad's response makes you
want to keep things bilateral for now. The repo is yours; visibility
is your call.
