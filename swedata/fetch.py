"""Download every story highlight of an Instagram profile with instaloader.

Highlights are only visible to logged-in accounts, so this needs *your* Instagram
login. Each highlight lands in its own folder under ``data/raw/``; the folder name
is the highlight title, which later serves as a hint for the extractor (zero2sudo
tends to group highlights by company).
"""

from __future__ import annotations

import json
import re
from pathlib import Path


def safe_folder_name(title: str) -> str:
    name = re.sub(r"[^\w\-. &()+]", "", title, flags=re.UNICODE).strip(" .")
    return name or "untitled"


def fetch_highlights(
    login_user: str,
    target_user: str,
    raw_dir: Path,
    session_file: str | None = None,
    only: list[str] | None = None,
) -> None:
    import instaloader

    raw_dir.mkdir(parents=True, exist_ok=True)
    loader = instaloader.Instaloader(
        dirname_pattern=str(raw_dir / "{target}"),
        filename_pattern="{date_utc:%Y-%m-%d_%H-%M-%S}_{mediaid}",
        download_videos=False,
        download_video_thumbnails=True,  # keep a frame of video slides, still OCR-able
        save_metadata=False,
        storyitem_metadata_txt_pattern="",
    )

    try:
        loader.load_session_from_file(login_user, session_file)
        print(f"Loaded saved Instagram session for {login_user}.")
    except FileNotFoundError:
        print(f"No saved session for {login_user}; logging in interactively.")
        loader.interactive_login(login_user)
        loader.save_session_to_file(session_file)

    profile = instaloader.Profile.from_username(loader.context, target_user)
    wanted = {t.casefold() for t in only} if only else None
    used_names: set[str] = set()

    for highlight in loader.get_highlights(profile):
        if wanted and highlight.title.casefold() not in wanted:
            continue
        folder = safe_folder_name(highlight.title)
        if folder in used_names:
            folder = f"{folder}_{highlight.unique_id}"
        used_names.add(folder)

        print(f"Highlight '{highlight.title}' ({highlight.itemcount} items) -> {raw_dir / folder}")
        for item in highlight.get_items():
            loader.download_storyitem(item, folder)

        meta = {"title": highlight.title, "id": highlight.unique_id, "items": highlight.itemcount}
        (raw_dir / folder / "highlight.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
