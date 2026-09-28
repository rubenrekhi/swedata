# swedata

Turns the SWE interview-process screenshots in [@zero2sudo](https://www.instagram.com/zero2sudo/)'s
Instagram story highlights into a searchable guide, organized by company.

```
Instagram highlights ──fetch──▶ data/raw/<highlight>/*.jpg
                     ──extract (Claude vision)──▶ data/extracted/<highlight>/*.json
                     ──build──▶ output/
                                  index.html           searchable page: full-text search, company TOC, level/outcome filters
                                  interview_guide.md   doc with a company table of contents (import into Google Docs)
                                  interviews.csv       one row per interview (Sheets/Excel filtering)
                                  interviews.json      merged records
```

For each screenshot, Claude transcribes the text verbatim and pulls out the company, role, level
(intern / new grad / experienced), timeframe, outcome, ordered interview stages, specific
questions, tips and compensation. Write-ups that span several consecutive slides (where only the
first one names the company) are stitched into one record. Company spellings are normalized, so
"facebook", "Meta Platforms" and "META" all end up under **Meta**. Each record links back to its
original screenshots.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...   # or `ant auth login`
```

## 1. Get the screenshots

Instagram only shows highlights to logged-in accounts, so the fetch step uses **your** login
through [instaloader](https://instaloader.github.io/):

```bash
python -m swedata fetch --login YOUR_IG_USERNAME
```

The first run asks for your password (and 2FA code) and saves an instaloader session, so later
runs don't log in again. Each highlight is saved to `data/raw/<highlight title>/`. Pass
`--only "Google" "Meta"` to fetch specific highlights. Instagram rate-limits scraping, so a
throwaway or low-stakes account is safer than your main one.

**No-scraping alternative:** screenshot or save the slides yourself and drop them in
`data/raw/<any folder name>/`, one folder per highlight. Files are processed in filename order,
and the folder name is passed to Claude as a hint (e.g. `data/raw/Google/`). JPG, PNG, WebP and
GIF are supported. Convert iPhone HEIC files first.

## 2. Extract

```bash
python -m swedata extract
```

This sends each image to Claude (`claude-opus-5-5` by default, see `--model` and `--effort`) and
caches the result per image, so re-running only processes new screenshots (`--force` redoes
everything). Highlights are processed in parallel (`--workers`); slides within a highlight go in
order so each one can see the previous slide's context. `extract` also runs `build` when it's done.

Rough cost estimate: a few cents per screenshot, so on the order of $10 for 300 slides.

## 3. Build (and use) the outputs

```bash
python -m swedata build        # re-render outputs from the cache, no API calls
open output/index.html
```

- **`index.html`** is one self-contained file. Search matches every field and the raw
  transcript (e.g. `onsite graph`, `team match`, `OCaml`). The sidebar lists companies with live
  counts, and you can filter by level and outcome. Filters are saved in the URL hash, so views can
  be bookmarked. Screenshot thumbnails load from `data/raw/` via relative links, so keep the
  folders together.
- **Google Doc:** upload `output/interview_guide.md` to Google Drive and open it with Google Docs.
  Each company is a heading, so the document outline doubles as the TOC. You can also insert a
  real TOC with *Insert → Table of contents*.
- **Spreadsheet:** import `output/interviews.csv` into Google Sheets and filter on any column.

`python -m swedata all --login YOUR_IG_USERNAME` runs all three steps.

## Notes

- The content belongs to zero2sudo and the people who sent it in. `data/` and `output/` are
  gitignored. Keep the guide for personal use rather than republishing it.
- Extraction only records what's on the slide. Missing fields stay blank instead of being guessed,
  and the full transcript is always kept, so search still finds anything the structured fields miss.
- To merge more company aliases, add them to `ALIASES` in `swedata/companies.py` and re-run `build`.

## Tests

```bash
pip install pytest && python -m pytest
```

The tests run the whole extract → build pipeline against a stubbed Claude client, so they need
no network access or API key.
