"""
Sticker library (QQ→Discord 迁移 · GAP #9.11 — QQ 收藏表情库的 DC 平替).

Storage lives in the data volume (writable, survives rebuilds):

  data/stickers.json        manifest: {tag: {"file","name","uses","got"}}
  data/stickers/<tag>.<ext> the image bytes themselves

Flow:
  - collect()     : auto-steal images the girls actually see (≤ STICKER_MAX)
  - roster()      : a :tag: list injected into the system prompt so the
                    model knows which stickers she owns
  - reply [sticker:tag] placeholder → engine sends the file as an attachment
"""

import hashlib
import json
import logging
import os
import threading

from config import (
    logger, STICKER_DIR, STICKER_MANIFEST, STICKER_MAX, STICKER_ROSTER_SIZE,
)

_lock = threading.Lock()

_EXT_BY_TYPE = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}


def _load() -> dict:
    try:
        if os.path.isfile(STICKER_MANIFEST):
            with open(STICKER_MANIFEST, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
    except Exception as exc:
        logger.warning("Sticker manifest load failed: %s", exc)
    return {}


def _save(manifest: dict) -> None:
    try:
        os.makedirs(STICKER_DIR, exist_ok=True)
        tmp = STICKER_MANIFEST + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=1)
        os.replace(tmp, STICKER_MANIFEST)
    except Exception as exc:
        logger.warning("Sticker manifest save failed: %s", exc)


def load_manifest() -> dict:
    return _load()


def known_tags(manifest: dict | None = None) -> set:
    man = manifest if manifest is not None else _load()
    return {t for t, v in man.items()
            if isinstance(v, dict) and os.path.isfile(_path(t, v))}


def _path(tag: str, entry: dict) -> str:
    return os.path.join(STICKER_DIR, entry.get("file", tag + ".png"))


def path_for(tag: str, manifest: dict | None = None) -> str | None:
    man = manifest if manifest is not None else _load()
    entry = man.get(tag)
    if not isinstance(entry, dict):
        return None
    p = _path(tag, entry)
    return p if os.path.isfile(p) else None


def collect(data: bytes, content_type: str, name_hint: str = "") -> str | None:
    """
    Save an image into the library. Returns the new tag, or None when the
    library is full / the bytes are not a supported image / duplicate.
    """
    ext = _EXT_BY_TYPE.get((content_type or "").lower())
    if not ext or not data or len(data) > 4 * 1024 * 1024:
        return None
    digest = hashlib.sha1(data).hexdigest()[:16]
    with _lock:
        man = _load()
        for tag, v in man.items():
            if isinstance(v, dict) and v.get("sha") == digest:
                return None  # already own this meme
        if len(man) >= STICKER_MAX:
            return None
        tag = "st" + digest[:10]
        os.makedirs(STICKER_DIR, exist_ok=True)
        fname = tag + ext
        try:
            with open(os.path.join(STICKER_DIR, fname), "wb") as f:
                f.write(data)
        except OSError as exc:
            logger.warning("Sticker write failed: %s", exc)
            return None
        man[tag] = {"file": fname, "sha": digest, "type": content_type,
                    "name": (name_hint or "")[:40], "uses": 0}
        _save(man)
        logger.info("🖼 Sticker collected: %s (%s, %d bytes)", tag, content_type, len(data))
        return tag


def bump_uses(tags: list) -> None:
    if not tags:
        return
    with _lock:
        man = _load()
        changed = False
        for t in tags:
            if t in man:
                man[t]["uses"] = int(man[t].get("uses", 0)) + 1
                changed = True
        if changed:
            _save(man)


def roster(manifest: dict | None = None) -> str:
    """The tag list the model may reference with [sticker:tag]."""
    man = manifest if manifest is not None else _load()
    tags = sorted(t for t in known_tags(man))[:STICKER_ROSTER_SIZE]
    if not tags:
        return ""
    return ", ".join(f"[sticker:{t}]" for t in tags)
