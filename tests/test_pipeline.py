"""End-to-end test of extract -> build with a stubbed OpenRouter client (no network)."""

import csv
import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from swedata.build import build
from swedata.companies import GENERAL, UNKNOWN, CompanyNormalizer
from swedata.extract import SLIDE_SCHEMA, extract_all


def entry(company, **kw):
    base = {
        "company": company, "role": "", "level": "unknown", "location": "", "timeframe": "",
        "outcome": "unknown", "stages": [], "questions": [], "tips": [], "compensation": "",
        "continues_previous": False,
    }
    return {**base, **kw}


def slide(text, category="interview_experience", entries=(), summary=""):
    return {"full_text": text, "category": category, "slide_summary": summary, "entries": list(entries)}


# Canned model answers, keyed by image file name.
ANSWERS = {
    "01.png": slide("meta new grad: OA then 2 coding rounds", entries=[entry(
        "facebook", role="SWE New Grad", level="new_grad", timeframe="Fall 2025",
        stages=[{"name": "OA", "details": "2 LeetCode mediums"}, {"name": "Onsite", "details": "2 coding rounds"}],
        questions=["LRU Cache"],
    )], summary="Meta new grad process"),
    "02.png": slide("...and then team matching took 3 weeks. got the offer!", entries=[entry(
        "Meta", outcome="offer", continues_previous=True,
        stages=[{"name": "Team matching", "details": "3 weeks"}], tips=["Grind tagged questions"],
    )]),
    "03.png": slide("follow for more", category="other"),
    "10.png": slide("Always ask clarifying questions before coding.", category="general_advice",
                    summary="Clarify before coding"),
    "11.png": slide("Stripe intern: bug bash round + integration round", entries=[entry(
        "Stripe, Inc.", role="SWE Intern", level="internship", outcome="rejected",
        stages=[{"name": "Bug squash", "details": "debug an open-source repo"}],
    )]),
}


class FakeMessages:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        user_content = kwargs["messages"][1]["content"]
        assert user_content[0]["image_url"]["url"].startswith("data:image/png;base64,")
        assert kwargs["response_format"]["json_schema"]["schema"] is SLIDE_SCHEMA
        prompt = user_content[1]["text"]
        self.calls.append(prompt)
        name = FakeMessages.current_name(prompt)
        # Wrap one answer in a code fence, as some models do despite response_format.
        text = json.dumps(ANSWERS[name])
        if name == "11.png":
            text = f"```json\n{text}\n```"
        message = SimpleNamespace(content=text)
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=message)])

    @staticmethod
    def current_name(prompt):
        # Slide index -> file name within the fixture highlights below.
        title = prompt.split('"')[1]
        idx = int(prompt.split("Slide ")[1].split(" ")[0])
        return {"Big Tech": ["01.png", "02.png", "03.png"], "Misc": ["10.png", "11.png"]}[title][idx - 1]


def make_screens(raw: Path):
    for folder, names in {"Big Tech": ["01.png", "02.png", "03.png"], "Misc": ["10.png", "11.png"]}.items():
        (raw / folder).mkdir(parents=True)
        for n in names:
            Image.new("RGB", (108, 192), "white").save(raw / folder / n)


def test_pipeline(tmp_path):
    raw, extracted, out = tmp_path / "raw", tmp_path / "extracted", tmp_path / "output"
    make_screens(raw)
    fake = FakeMessages()
    client = SimpleNamespace(chat=SimpleNamespace(completions=fake))

    assert extract_all(raw, extracted, client=client, workers=2) == 0
    assert len(fake.calls) == 5
    # Second slide sees the first slide's context.
    second = next(c for c in fake.calls if "Slide 2 of 3" in c)
    assert "Meta new grad process" in second and "facebook" in second

    # Cached: a re-run makes no new calls.
    extract_all(raw, extracted, client=client)
    assert len(fake.calls) == 5

    stats = build(extracted, out)
    assert stats == {"slides": 5, "other": 1, "records": 3, "companies": 2}

    records = json.loads((out / "interviews.json").read_text())
    assert [r["company"] for r in records] == ["Meta", "Stripe", GENERAL]
    meta = records[0]
    assert [s["name"] for s in meta["stages"]] == ["OA", "Onsite", "Team matching"]
    assert meta["outcome"] == "offer" and meta["level"] == "new_grad"
    assert len(meta["sources"]) == 2 and "got the offer" in meta["text"]

    md = (out / "interview_guide.md").read_text()
    assert "- [Meta](#meta) (1)" in md and "## Stripe" in md and "1. **OA**: 2 LeetCode mediums" in md

    rows = list(csv.DictReader((out / "interviews.csv").open()))
    assert rows[0]["stages"] == "OA -> Onsite -> Team matching"

    page = (out / "index.html").read_text()
    assert "/*__DATA__*/" not in page and '"company": "Stripe"' in page
    assert "../raw/Big%20Tech" in page or "../raw/Big Tech/01.png" in page


def test_company_normalizer():
    norm = CompanyNormalizer(["openai", "OpenAI", "OpenAI", "Facebook", "Jane Street LLC"])
    assert norm("openai") == "OpenAI"
    assert norm("META platforms") == "Meta"
    assert norm("jane street") == "Jane Street"
    assert norm("") == UNKNOWN and norm("Unknown") == UNKNOWN


def test_schema_is_strict():
    def check(node):
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])
            for child in node["properties"].values():
                check(child)
        if node.get("type") == "array":
            check(node["items"])
    check(SLIDE_SCHEMA)
