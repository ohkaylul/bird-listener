"""Fetches species facts (description, image, range map) from Wikipedia, with
range maps falling back to GBIF occurrence data when Wikipedia doesn't have a
usable one. Results are cached to disk."""
import io
import json
import re
import threading
import urllib.parse
import urllib.request

from PIL import Image

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


def _find_range_map_filename(title, scientific_name):
    """Looks at a Wikipedia page's images and returns the filename of one that
    looks like a range/distribution map, or None if it can't find one."""
    data = _fetch_json(
        "https://en.wikipedia.org/w/api.php?action=query&format=json"
        f"&titles={urllib.parse.quote(title)}&prop=images&imlimit=50"
    )
    sci_words = set(scientific_name.lower().split())
    pages = data.get("query", {}).get("pages", {})
    for page in pages.values():
        for img in page.get("images", []):
            filename = img.get("title", "")
            # Filenames use underscores/hyphens as word separators, so normalize
            # before checking for whole words -- a naive substring check would
            # false-positive on e.g. "Orange-crowned" containing "range".
            words = set(re.findall(r"[a-z]+", filename.lower().replace("_", " ").replace("-", " ")))
            if "range" in words or "distribution" in words:
                return filename
            # Species range maps are also commonly just "<Scientific name> map.svg",
            # so "map" alone counts too, but only alongside the species name --
            # otherwise it'd match unrelated generic map icons on the page.
            if "map" in words and sci_words <= words:
                return filename
    return None


def _fetch_range_map_bytes(filename):
    data = _fetch_json(
        "https://en.wikipedia.org/w/api.php?action=query&format=json"
        f"&titles={urllib.parse.quote(filename)}&prop=imageinfo&iiprop=url"
    )
    pages = data.get("query", {}).get("pages", {})
    for page in pages.values():
        imageinfo = page.get("imageinfo")
        if imageinfo and imageinfo[0].get("url"):
            return _fetch_image_bytes(imageinfo[0]["url"])
    return None


def _is_rasterizable(image_bytes):
    """Wikipedia range maps are frequently SVGs, which Pillow can't open --
    those aren't worth caching as a "map" since the GUI can't display them."""
    try:
        Image.open(io.BytesIO(image_bytes)).verify()
        return True
    except Exception:
        return False


def _fetch_gbif_range_map(scientific_name):
    """Renders a world basemap with an occurrence-density overlay for the
    species from GBIF -- a heatmap of real sighting records rather than a
    curated range polygon, but it's a free, keyless, always-raster source."""
    match = _fetch_json(
        f"https://api.gbif.org/v1/species/match?name={urllib.parse.quote(scientific_name)}"
    )
    taxon_key = match.get("usageKey")
    if not taxon_key:
        return None

    basemap_bytes = _fetch_image_bytes("https://tile.gbif.org/4326/omt/0/0/0@1x.png?style=gbif-natural")
    overlay_bytes = _fetch_image_bytes(
        f"https://api.gbif.org/v2/map/occurrence/density/0/0/0@1x.png?taxonKey={taxon_key}&srs=EPSG:4326"
    )

    basemap = Image.open(io.BytesIO(basemap_bytes)).convert("RGBA")
    overlay = Image.open(io.BytesIO(overlay_bytes)).convert("RGBA")
    combined = Image.alpha_composite(basemap, overlay)

    buf = io.BytesIO()
    combined.save(buf, format="PNG")
    return buf.getvalue()


def get_species_info(scientific_name, common_name):
    """Returns a dict {title, extract, page_url, image_bytes, map_bytes} for a
    species, fetching from Wikipedia on first lookup and caching the result
    (images are cached hex-encoded so the JSON cache stays self-contained).
    Raises on network/lookup failure; caller should handle and show a fallback."""
    cache = _load_cache()
    if scientific_name in cache:
        entry = cache[scientific_name]
        image_bytes = bytes.fromhex(entry["image_hex"]) if entry.get("image_hex") else None
        map_bytes = bytes.fromhex(entry["map_hex"]) if entry.get("map_hex") else None
        return {
            "title": entry["title"],
            "extract": entry["extract"],
            "page_url": entry["page_url"],
            "image_bytes": image_bytes,
            "map_bytes": map_bytes,
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

    map_bytes = None
    try:
        map_filename = _find_range_map_filename(title, scientific_name)
        if map_filename:
            candidate = _fetch_range_map_bytes(map_filename)
            if candidate and _is_rasterizable(candidate):
                map_bytes = candidate
    except Exception:
        map_bytes = None

    if map_bytes is None:
        try:
            map_bytes = _fetch_gbif_range_map(scientific_name)
        except Exception:
            map_bytes = None

    cache[scientific_name] = {
        "title": title,
        "extract": extract,
        "page_url": page_url,
        "image_hex": image_bytes.hex() if image_bytes else None,
        "map_hex": map_bytes.hex() if map_bytes else None,
    }
    _save_cache()

    return {
        "title": title,
        "extract": extract,
        "page_url": page_url,
        "image_bytes": image_bytes,
        "map_bytes": map_bytes,
    }
