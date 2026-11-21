# Clip Dashboard landing page

`site/index.html` is one static page with inline CSS and no JavaScript. It loads Manrope from Google Fonts and
uses the app screenshots in `docs/screenshots/` and the mark in `clipdash/web/logo.svg` by relative path
(`../docs/screenshots/...`, `../clipdash/web/logo.svg`). There is no build step.

## Preview locally

Serve from the repository root so the `../` paths resolve:

```
.venv/bin/python -m http.server 8000
```

Then open <http://127.0.0.1:8000/site/>. Opening `site/index.html` straight from disk (`file://`) works too.

## Publish on GitHub Pages

Pages serves one folder as the site root, so the page cannot reach `../docs` once published. Pick one option.

### Option A: `docs/` folder on `main`

1. Copy the page and its assets into `docs/` and rewrite the paths:

   ```
   cp site/index.html docs/index.html
   cp clipdash/web/logo.svg docs/logo.svg
   sed -i '' -e 's#\.\./docs/screenshots/#screenshots/#g' -e 's#\.\./clipdash/web/logo\.svg#logo.svg#g' docs/index.html
   ```

   (On Linux or Git Bash, use `sed -i` without the `''`.)
2. Commit, then in the repository go to Settings → Pages → Build and deployment → Deploy from a branch,
   branch `main`, folder `/docs`.
3. Add an empty `docs/.nojekyll` so Pages serves files as-is.

### Option B: `gh-pages` branch

```
git switch --orphan gh-pages
git checkout main -- site/index.html docs/screenshots clipdash/web/logo.svg
mkdir -p screenshots && git mv docs/screenshots/* screenshots/ && git mv site/index.html index.html && git mv clipdash/web/logo.svg logo.svg
sed -i '' -e 's#\.\./docs/screenshots/#screenshots/#g' -e 's#\.\./clipdash/web/logo\.svg#logo.svg#g' index.html
touch .nojekyll && git add -A && git commit -m "Publish landing page"
git push origin gh-pages
```

Then choose branch `gh-pages`, folder `/ (root)` under Settings → Pages. Repeat the copy and rewrite whenever
`site/index.html` or the screenshots change.

If the repository stays private, Pages needs a paid GitHub plan; otherwise host the same folder on any static
host (Netlify, Cloudflare Pages) after the same path rewrite.

## Swap the download links

Every download button points to `https://github.com/david-domanski1/clip-dashboard/releases`. To point them at
direct installers or a checkout page, replace that URL everywhere:

```
sed -i '' 's#https://github.com/david-domanski1/clip-dashboard/releases#https://example.com/download#g' site/index.html
```

To split macOS and Windows into separate buttons, edit the two "Download for macOS / Windows" links (hero and
closing section). "Get Pro" uses the same URL until there is a checkout page.

## Copy that still needs a decision

- Refund answer in the FAQ ("14-day refund, cancel anytime") is a draft policy.
- Pricing (Free / Pro $24 per month or $190 per year) and the Free vs Pro split.
- The "In the works" chips describe features still being built; remove a chip's "in the works" status when it ships.
