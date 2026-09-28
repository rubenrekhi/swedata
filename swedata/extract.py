"""OCR + structuring of highlight screenshots with a vision model via OpenRouter.

Every image becomes one JSON file in ``data/extracted/<highlight>/<image>.json``
holding a verbatim transcription plus structured fields (company, stages,
questions, ...). Results are cached, so re-running only processes new images.

Slides inside a highlight are processed in order, and each request carries a
short summary of the previous slide: a single DM often spans several
screenshots, and only the first one names the company.
"""

from __future__ import annotations

import base64
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import openai

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
# Any OpenRouter model that accepts images and JSON-schema output works; see openrouter.ai/models.
DEFAULT_MODEL = "anthropic/claude-opus-5.5"
PROMPT_VERSION = 1
IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif"}

SYSTEM_PROMPT = """\
You turn screenshots from the Instagram story highlights of zero2sudo into structured data. \
The account shares followers' software-engineering recruiting experiences: mostly screenshots of \
DMs, notes or text posts describing how a company's interview process went.

For each screenshot:
- Transcribe all readable text into `full_text`, verbatim, keeping line breaks. Leave out Instagram \
UI chrome (timestamps, "Send message", reply bars, viewer counts).
- Set `category`: "interview_experience" when it describes a specific company's hiring process, \
"general_advice" when it is useful recruiting/interview information not tied to one company, \
"other" for anything else (memes, promos, polls, blank or unreadable slides).
- `slide_summary`: one plain sentence saying what the slide covers.
- `entries`: one per company whose process the slide describes (usually one; empty unless \
category is interview_experience).

Record only what the screenshot says. Use "" or [] (or "unknown") rather than guessing. Use the \
company's common name ("Meta", not "facebook"; "Jane Street", not "JS"). `stages` go in the order \
they happened (e.g. OA, recruiter call, technical phone screen, onsite rounds, team matching), \
each with what the candidate said about it: question types, difficulty, length, format. \
`questions` lists concrete problems mentioned (LeetCode titles or numbers, system-design prompts, \
specific behavioral questions). `tips` holds the candidate's advice.

Many experiences span several consecutive screenshots and only the first one names the company. \
When this slide reads as a continuation of the previous slide described in the message, set \
`continues_previous` to true and reuse that company."""

_STR = {"type": "string"}
_STR_LIST = {"type": "array", "items": _STR}
ENTRY_SCHEMA = {
    "type": "object",
    "properties": {
        "company": _STR,
        "role": {"type": "string", "description": 'Role title as written, e.g. "SWE Intern", "L4 Backend Engineer".'},
        "level": {"type": "string", "enum": ["internship", "new_grad", "experienced", "unknown"]},
        "location": _STR,
        "timeframe": {"type": "string", "description": 'When it happened, e.g. "Fall 2025", "Summer 2026 internship cycle".'},
        "outcome": {"type": "string", "enum": ["offer", "rejected", "ghosted", "in_progress", "withdrew", "unknown"]},
        "stages": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": _STR, "details": _STR},
                "required": ["name", "details"],
                "additionalProperties": False,
            },
        },
        "questions": _STR_LIST,
        "tips": _STR_LIST,
        "compensation": _STR,
        "continues_previous": {"type": "boolean"},
    },
    "required": [
        "company", "role", "level", "location", "timeframe", "outcome",
        "stages", "questions", "tips", "compensation", "continues_previous",
    ],
    "additionalProperties": False,
}
SLIDE_SCHEMA = {
    "type": "object",
    "properties": {
        "full_text": _STR,
        "category": {"type": "string", "enum": ["interview_experience", "general_advice", "other"]},
        "slide_summary": _STR,
        "entries": {"type": "array", "items": ENTRY_SCHEMA},
    },
    "required": ["full_text", "category", "slide_summary", "entries"],
    "additionalProperties": False,
}


def natural_key(path: Path) -> list:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", path.name)]


def list_images(folder: Path) -> list[Path]:
    return sorted((p for p in folder.iterdir() if p.suffix.lower() in IMAGE_TYPES), key=natural_key)


def highlight_title(folder: Path) -> str:
    meta = folder / "highlight.json"
    if meta.exists():
        return json.loads(meta.read_text()).get("title") or folder.name
    return folder.name


def previous_slide_context(prev: dict | None) -> str:
    if prev is None:
        return "none (this is the first slide of the highlight)"
    companies = ", ".join(e["company"] for e in prev.get("entries", []) if e.get("company")) or "none named"
    tail = prev.get("full_text", "")[-400:].replace("\n", " ")
    return f'{prev.get("slide_summary", "")} Companies: {companies}. It ended with: "{tail}"'


class Extractor:
    def __init__(self, client: openai.OpenAI, model: str = DEFAULT_MODEL, effort: str = "medium"):
        self.client = client
        self.model = model
        self.effort = effort

    def extract_image(self, image: Path, title: str, index: int, total: int, prev: dict | None) -> dict:
        data = base64.standard_b64encode(image.read_bytes()).decode("ascii")
        prompt = (
            f'Highlight title: "{title}". Slide {index} of {total}.\n'
            f"Previous slide: {previous_slide_context(prev)}"
        )
        response = self.client.chat.completions.create(
            model=self.model,
            max_tokens=16000,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{IMAGE_TYPES[image.suffix.lower()]};base64,{data}"}},
                    {"type": "text", "text": prompt},
                ]},
            ],
            response_format={"type": "json_schema", "json_schema": {"name": "slide", "strict": True, "schema": SLIDE_SCHEMA}},
            extra_body={
                "reasoning": {"effort": self.effort},
                # Only route to providers that honor response_format, so the reply is schema-valid JSON.
                "provider": {"require_parameters": True},
            },
        )
        if not response.choices:
            raise RuntimeError(f"empty response from OpenRouter: {response}")
        choice = response.choices[0]
        if choice.finish_reason == "length":
            raise RuntimeError("response hit max_tokens before the JSON was complete")
        content = choice.message.content
        if not content:
            raise RuntimeError(f"model returned no content (finish_reason={choice.finish_reason})")
        return parse_json(content)


def parse_json(text: str) -> dict:
    """Parse the model's JSON, tolerating a ```json fence some models add anyway."""
    text = text.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    return json.loads(fenced.group(1) if fenced else text)


def make_client() -> openai.OpenAI:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise SystemExit("Set OPENROUTER_API_KEY (create one at https://openrouter.ai/keys).")
    return openai.OpenAI(
        api_key=key,
        base_url=OPENROUTER_BASE_URL,
        max_retries=5,
        default_headers={"X-Title": "swedata"},
    )


def cache_path(extracted_dir: Path, image: Path) -> Path:
    return extracted_dir / image.parent.name / f"{image.name}.json"


def process_highlight(extractor: Extractor, folder: Path, extracted_dir: Path, force: bool) -> tuple[int, int, list[str]]:
    title = highlight_title(folder)
    images = list_images(folder)
    done = skipped = 0
    errors: list[str] = []
    prev: dict | None = None
    for i, image in enumerate(images, start=1):
        out = cache_path(extracted_dir, image)
        if out.exists() and not force:
            prev = json.loads(out.read_text())
            skipped += 1
            continue
        try:
            result = extractor.extract_image(image, title, i, len(images), prev)
        except (openai.APIError, RuntimeError, json.JSONDecodeError) as exc:
            errors.append(f"{image}: {exc}")
            prev = None
            continue
        result["_source"] = {
            "image": str(image.as_posix()),
            "highlight": title,
            "folder": folder.name,
            "index": i,
            "model": extractor.model,
            "prompt_version": PROMPT_VERSION,
        }
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        prev = result
        done += 1
        print(f"  [{folder.name}] {i}/{len(images)} {image.name}: {result['category']}")
    return done, skipped, errors


def extract_all(
    raw_dir: Path,
    extracted_dir: Path,
    model: str = DEFAULT_MODEL,
    effort: str = "medium",
    workers: int = 4,
    force: bool = False,
    client: openai.OpenAI | None = None,
) -> int:
    folders = sorted(p for p in raw_dir.glob("*") if p.is_dir() and list_images(p))
    if not folders:
        raise SystemExit(f"No images found under {raw_dir}/<highlight>/ - run `fetch` first or drop screenshots there.")

    extractor = Extractor(client or make_client(), model=model, effort=effort)
    total_done = total_skipped = 0
    all_errors: list[str] = []
    # Highlights run in parallel; slides within one highlight stay sequential for context.
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for done, skipped, errors in pool.map(lambda f: process_highlight(extractor, f, extracted_dir, force), folders):
            total_done += done
            total_skipped += skipped
            all_errors += errors

    print(f"Extracted {total_done} new slides ({total_skipped} cached) from {len(folders)} highlights.")
    for err in all_errors:
        print(f"  FAILED {err}")
    return len(all_errors)
