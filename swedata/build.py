"""Merge per-slide extractions into interview records and render the outputs.

Outputs (in ``output/``):
- ``index.html``          single-file searchable site: full-text search, company TOC, filters
- ``interview_guide.md``  doc with a company table of contents (imports cleanly into Google Docs)
- ``interviews.csv``      one row per interview, for spreadsheet filtering
- ``interviews.json``     the merged records
"""

from __future__ import annotations

import csv
import html
import json
import os
import re
from pathlib import Path

from .companies import GENERAL, UNKNOWN, CompanyNormalizer

TEMPLATE = Path(__file__).with_name("template.html")
LEVEL_LABELS = {"internship": "Internship", "new_grad": "New grad", "experienced": "Experienced", "unknown": ""}
OUTCOME_LABELS = {"offer": "Offer", "rejected": "Rejected", "ghosted": "Ghosted", "in_progress": "In progress", "withdrew": "Withdrew", "unknown": ""}


def load_slides(extracted_dir: Path) -> list[dict]:
    slides = [json.loads(p.read_text()) for p in extracted_dir.glob("*/*.json")]
    slides.sort(key=lambda s: (s["_source"]["folder"], s["_source"]["index"]))
    return slides


def _source(slide: dict) -> dict:
    src = slide["_source"]
    return {"image": src["image"], "highlight": src["highlight"], "index": src["index"]}


def _merge_into(record: dict, entry: dict, slide: dict) -> None:
    for field in ("role", "location", "timeframe", "compensation"):
        if not record[field] and entry.get(field):
            record[field] = entry[field]
    for field in ("level", "outcome"):
        if record[field] == "unknown" and entry.get(field, "unknown") != "unknown":
            record[field] = entry[field]
    record["stages"] += entry.get("stages", [])
    record["questions"] += entry.get("questions", [])
    record["tips"] += entry.get("tips", [])
    record["sources"].append(_source(slide))
    record["text"] += "\n\n" + slide.get("full_text", "")


def build_records(slides: list[dict]) -> tuple[list[dict], dict]:
    names = [e.get("company", "") for s in slides for e in s.get("entries", [])]
    normalize = CompanyNormalizer(names)
    records: list[dict] = []
    stats = {"slides": len(slides), "other": 0}
    last: dict | None = None  # record the previous slide contributed to, within the same highlight

    for slide in slides:
        category = slide.get("category")
        folder = slide["_source"]["folder"]
        if last is not None and last["_folder"] != folder:
            last = None

        if category == "other" or (category == "interview_experience" and not slide.get("entries")):
            stats["other"] += 1
            last = None
            continue

        if category == "general_advice":
            records.append(_new_record(GENERAL, {}, slide, summary=slide.get("slide_summary", "")))
            last = None
            continue

        current = None
        for entry in slide["entries"]:
            company = normalize(entry.get("company", ""))
            if (
                entry.get("continues_previous")
                and last is not None
                and (company == last["company"] or company == UNKNOWN)
            ):
                _merge_into(last, entry, slide)
                current = last
            else:
                current = _new_record(company, entry, slide, summary=slide.get("slide_summary", ""))
                records.append(current)
        last = current

    for i, rec in enumerate(records, start=1):
        rec["id"] = f"r{i}"
        rec["questions"] = list(dict.fromkeys(q for q in rec["questions"] if q.strip()))
        rec["tips"] = list(dict.fromkeys(t for t in rec["tips"] if t.strip()))
        rec["text"] = rec["text"].strip()
        del rec["_folder"]
    records.sort(key=lambda r: (r["company"] in (UNKNOWN, GENERAL), r["company"] == GENERAL, r["company"].casefold()))
    stats["records"] = len(records)
    stats["companies"] = len({r["company"] for r in records} - {UNKNOWN, GENERAL})
    return records, stats


def _new_record(company: str, entry: dict, slide: dict, summary: str) -> dict:
    return {
        "company": company,
        "role": entry.get("role", ""),
        "level": entry.get("level", "unknown"),
        "location": entry.get("location", ""),
        "timeframe": entry.get("timeframe", ""),
        "outcome": entry.get("outcome", "unknown"),
        "compensation": entry.get("compensation", ""),
        "stages": list(entry.get("stages", [])),
        "questions": list(entry.get("questions", [])),
        "tips": list(entry.get("tips", [])),
        "summary": summary,
        "sources": [_source(slide)],
        "text": slide.get("full_text", ""),
        "_folder": slide["_source"]["folder"],
    }


# ---------------------------------------------------------------- markdown


def _anchor(company: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", company.lower()).strip("-")


def _meta_line(rec: dict) -> str:
    bits = [
        rec["role"],
        LEVEL_LABELS.get(rec["level"], ""),
        rec["timeframe"],
        rec["location"],
        OUTCOME_LABELS.get(rec["outcome"], ""),
    ]
    return " · ".join(b for b in bits if b)


def render_markdown(records: list[dict], stats: dict) -> str:
    by_company: dict[str, list[dict]] = {}
    for rec in records:
        by_company.setdefault(rec["company"], []).append(rec)

    out = [
        "# SWE Interview Processes (from zero2sudo's highlights)",
        "",
        f"{stats['records']} write-ups across {stats['companies']} companies, "
        f"transcribed from {stats['slides']} story screenshots.",
        "",
        "## Companies",
        "",
    ]
    out += [f"- [{c}](#{_anchor(c)}) ({len(recs)})" for c, recs in by_company.items()]

    for company, recs in by_company.items():
        out += ["", f"## {company}", ""]
        for n, rec in enumerate(recs, start=1):
            title = _meta_line(rec) or rec["summary"] or "Write-up"
            out += [f"### {company} #{n}: {title}", ""]
            if rec["summary"] and title != rec["summary"]:
                out += [f"*{rec['summary']}*", ""]
            if rec["compensation"]:
                out += [f"**Compensation:** {rec['compensation']}", ""]
            if rec["stages"]:
                out += ["**Process**", ""]
                out += [f"{i}. **{s['name']}**" + (f": {s['details']}" if s["details"] else "") for i, s in enumerate(rec["stages"], start=1)]
                out.append("")
            if rec["questions"]:
                out += ["**Questions**", ""] + [f"- {q}" for q in rec["questions"]] + [""]
            if rec["tips"]:
                out += ["**Tips**", ""] + [f"- {t}" for t in rec["tips"]] + [""]
            if company == GENERAL or not (rec["stages"] or rec["questions"] or rec["tips"]):
                out += ["> " + line if line else ">" for line in rec["text"].splitlines()] + [""]
            srcs = ", ".join(f"{s['highlight']} #{s['index']}" for s in rec["sources"])
            out += [f"_Source: {srcs}_", ""]
    return "\n".join(out).rstrip() + "\n"


# ---------------------------------------------------------------- csv


def write_csv(records: list[dict], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["company", "role", "level", "timeframe", "location", "outcome", "compensation",
                         "stages", "questions", "tips", "summary", "sources"])
        for r in records:
            writer.writerow([
                r["company"], r["role"], r["level"], r["timeframe"], r["location"], r["outcome"], r["compensation"],
                " -> ".join(s["name"] for s in r["stages"]),
                "\n".join(r["questions"]),
                "\n".join(r["tips"]),
                r["summary"],
                "; ".join(f"{s['highlight']} #{s['index']}" for s in r["sources"]),
            ])


# ---------------------------------------------------------------- html


def render_html(records: list[dict], stats: dict, out_dir: Path) -> str:
    # Image links are relative to the output folder so the page works when opened from disk.
    view = []
    for rec in records:
        rec = dict(rec)
        rec["sources"] = [
            {**s, "image": Path(os.path.relpath(Path(s["image"]).resolve(), out_dir.resolve())).as_posix()}
            for s in rec["sources"]
        ]
        view.append(rec)
    payload = json.dumps({"records": view, "stats": stats}, ensure_ascii=False).replace("</", "<\\/")
    return TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/null", payload).replace(
        "__SUBTITLE__",
        html.escape(f"{stats['records']} write-ups · {stats['companies']} companies · {stats['slides']} screenshots"),
    )


def build(extracted_dir: Path, out_dir: Path) -> dict:
    slides = load_slides(extracted_dir)
    if not slides:
        raise SystemExit(f"No extracted slides in {extracted_dir} - run `extract` first.")
    records, stats = build_records(slides)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "interviews.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "interview_guide.md").write_text(render_markdown(records, stats), encoding="utf-8")
    write_csv(records, out_dir / "interviews.csv")
    (out_dir / "index.html").write_text(render_html(records, stats, out_dir), encoding="utf-8")
    print(
        f"Built {stats['records']} records for {stats['companies']} companies "
        f"({stats['other']} off-topic slides skipped) -> {out_dir}/"
    )
    return stats
