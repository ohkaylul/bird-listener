"""Fetches species facts (description, image) from Wikipedia, cached to disk."""
import io
import json
import threading
import urllib.parse
import urllib.request

import config

CACHE_PATH = config._state_dir() / "species_cache.json"
USER_AGENT = "BirdListener/1.0 (https://github.com/; personal desktop app)"

_lock = threading.Lock()
_cache = None


def _load_cache():
    global _cache
    with _lock:
        if _cache is not None:
            return _cache
        if CACHE_PATH.exists():
            try:
                _cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                _cache = {}
        else:
            _cache = {}
        return _cache


def _save_cache():
    with _lock:
        try:
            CACHE_PATH.write_text(json.dumps(_cache, indent=2, sort_keys=True), encoding="utf-8")
        except OSError:
            pass


def _fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _fetch_image_bytes(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.read()


def get_species_info(scientific_name, common_name):
    """Returns a dict {title, extract, page_url, image_bytes} for a species,
    fetching from Wikipedia on first lookup and caching the result (image bytes
    are cached separately, hex-encoded, so the JSON cache stays reasonably small).
    Raises on network/lookup failure; caller should handle and show a fallback."""
    cache = _load_cache()
    if scientific_name in cache:
        entry = cache[scientific_name]
        image_bytes = bytes.fromhex(entry["image_hex"]) if entry.get("image_hex") else None
        return {
            "title": entry["title"],
            "extract": entry["extract"],
            "page_url": entry["page_url"],
            "image_bytes": image_bytes,
        }

    encoded = urllib.parse.quote(scientific_name.replace(" ", "_"))
    summary = _fetch_json(f"https://en.wikipedia.org/api/rest_v1/page/summary/{encoded}")

    title = summary.get("title", common_name)
    extract = summary.get("extract", "No description available.")
    page_url = summary.get("content_urls", {}).get("desktop", {}).get("page", "")

    image_bytes = None
    thumbnail = summary.get("thumbnail") or summary.get("originalimage")
    if thumbnail and thumbnail.get("source"):
        try:
            image_bytes = _fetch_image_bytes(thumbnail["source"])
        except Exception:
            image_bytes = None

    cache[scientific_name] = {
        "title": title,
        "extract": extract,
        "page_url": page_url,
        "image_hex": image_bytes.hex() if image_bytes else None,
    }
    _save_cache()

    return {"title": title, "extract": extract, "page_url": page_url, "image_bytes": image_bytes}
