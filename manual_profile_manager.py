import hashlib
import json
import os
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

from manual_prepare_timeline import validate_prepare_timeline


MANUAL_PROFILES_DIR = "manual_profiles"
MANUAL_PROFILES_INDEX_PATH = os.path.join(MANUAL_PROFILES_DIR, "index.json")
MANUAL_PROFILES_PROFILE_DIR = os.path.join(MANUAL_PROFILES_DIR, "profiles")


def ensure_manual_profile_dirs() -> None:
    os.makedirs(MANUAL_PROFILES_PROFILE_DIR, exist_ok=True)


def canonicalize_url(url: str) -> str:
    if not url:
        return ""

    parsed = urlparse(url.strip())
    scheme = (parsed.scheme or "https").lower()
    netloc = parsed.netloc.lower()
    path = parsed.path or ""
    if path != "/":
        path = path.rstrip("/")
    return f"{scheme}://{netloc}{path}"


def url_key_from_url(url: str) -> str:
    parsed = urlparse(url or "")
    path_parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(path_parts) >= 2 and path_parts[0].startswith("@"):
        return f"{path_parts[0][1:]}/{path_parts[1]}"
    if len(path_parts) >= 2:
        return f"{path_parts[-2]}/{path_parts[-1]}"
    if path_parts:
        return path_parts[-1]
    return ""


def profile_hash_for_url(url: str) -> str:
    canonical = canonicalize_url(url).lower()
    if not canonical:
        return ""
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:12]


def suggested_profile_filename(url: str) -> str:
    profile_hash = profile_hash_for_url(url)
    return f"{profile_hash}.json" if profile_hash else "unknown.json"


def suggested_profile_path_for_url(url: str) -> str:
    ensure_manual_profile_dirs()
    return os.path.join(MANUAL_PROFILES_PROFILE_DIR, suggested_profile_filename(url))


def load_profile_from_path(profile_path: str) -> Tuple[Optional[Dict], Optional[str]]:
    if not profile_path:
        return None, None
    if not os.path.isabs(profile_path):
        profile_path = os.path.abspath(profile_path)
    if not os.path.exists(profile_path):
        return None, profile_path
    try:
        with open(profile_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data, profile_path
    except Exception:
        return None, profile_path


def validate_profile(profile_data: Optional[Dict], expected_url: Optional[str] = None) -> Tuple[bool, Optional[str]]:
    if not isinstance(profile_data, dict):
        return False, "profile is not a JSON object"

    layout = profile_data.get("layout") or {}
    icons = layout.get("icons") or []
    slots = layout.get("slots") or []
    if not icons:
        return False, "profile is missing icons"
    if not slots:
        return False, "profile is missing slots"

    if "prepareActions" in profile_data:
        timeline_valid, timeline_reason = validate_prepare_timeline(
            profile_data.get("prepareActions") or [],
            profile_data.get("prepareTiming") or {},
        )
        if not timeline_valid:
            return False, f"invalid PREPARE timeline: {timeline_reason}"

    if expected_url:
        expected_canonical = canonicalize_url(expected_url)
        profile_canonical = canonicalize_url(profile_data.get("canonicalUrl") or profile_data.get("sourceUrl") or "")
        if expected_canonical and profile_canonical and expected_canonical != profile_canonical:
            return False, "profile URL does not match expected URL"

    return True, None


def load_index() -> Dict:
    ensure_manual_profile_dirs()
    default_index = {
        "version": 1,
        "updatedAt": None,
        "canonicalUrls": {},
        "urlKeys": {},
    }
    if not os.path.exists(MANUAL_PROFILES_INDEX_PATH):
        return default_index
    try:
        with open(MANUAL_PROFILES_INDEX_PATH, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            return default_index
        data.setdefault("version", 1)
        data.setdefault("updatedAt", None)
        data.setdefault("canonicalUrls", {})
        data.setdefault("urlKeys", {})
        return data
    except Exception:
        return default_index


def save_index(index_data: Dict) -> None:
    ensure_manual_profile_dirs()
    index_data = dict(index_data or {})
    index_data["version"] = 1
    index_data["updatedAt"] = datetime.utcnow().isoformat() + "Z"
    index_data.setdefault("canonicalUrls", {})
    index_data.setdefault("urlKeys", {})
    with open(MANUAL_PROFILES_INDEX_PATH, "w", encoding="utf-8") as handle:
        json.dump(index_data, handle, ensure_ascii=True, indent=2)


def _index_profile(index_data: Dict, profile_path: str, profile_data: Dict) -> None:
    canonical_url = canonicalize_url(profile_data.get("canonicalUrl") or profile_data.get("sourceUrl") or "")
    url_key = (profile_data.get("urlKey") or url_key_from_url(canonical_url)).strip()
    relative_path = os.path.relpath(profile_path, MANUAL_PROFILES_DIR).replace("\\", "/")
    if canonical_url:
        index_data.setdefault("canonicalUrls", {})[canonical_url] = relative_path
        index_data["canonicalUrls"][canonical_url.lower()] = relative_path
    if url_key:
        index_data.setdefault("urlKeys", {})[url_key] = relative_path
        index_data["urlKeys"][url_key.lower()] = relative_path


def scan_profiles() -> List[Tuple[str, Dict]]:
    ensure_manual_profile_dirs()
    profiles = []
    for filename in os.listdir(MANUAL_PROFILES_PROFILE_DIR):
        if not filename.lower().endswith(".json"):
            continue
        profile_path = os.path.join(MANUAL_PROFILES_PROFILE_DIR, filename)
        profile_data, loaded_path = load_profile_from_path(profile_path)
        if profile_data and loaded_path:
            profiles.append((loaded_path, profile_data))
    return profiles


def rebuild_index() -> Dict:
    index_data = {
        "version": 1,
        "updatedAt": None,
        "canonicalUrls": {},
        "urlKeys": {},
    }
    for profile_path, profile_data in scan_profiles():
        _index_profile(index_data, profile_path, profile_data)
    save_index(index_data)
    return index_data


def _resolve_index_path(relative_or_absolute_path: str) -> str:
    if os.path.isabs(relative_or_absolute_path):
        return relative_or_absolute_path
    return os.path.abspath(os.path.join(MANUAL_PROFILES_DIR, relative_or_absolute_path))


def find_profile(url: str) -> Tuple[Optional[Dict], Optional[str]]:
    ensure_manual_profile_dirs()
    canonical_url = canonicalize_url(url)
    url_key = url_key_from_url(canonical_url)
    index_data = load_index()

    candidates = []
    if canonical_url:
        candidates.extend([
            index_data.get("canonicalUrls", {}).get(canonical_url),
            index_data.get("canonicalUrls", {}).get(canonical_url.lower()),
        ])
    if url_key:
        candidates.extend([
            index_data.get("urlKeys", {}).get(url_key),
            index_data.get("urlKeys", {}).get(url_key.lower()),
        ])

    for candidate in candidates:
        if not candidate:
            continue
        profile_path = _resolve_index_path(candidate)
        profile_data, loaded_path = load_profile_from_path(profile_path)
        is_valid, _ = validate_profile(profile_data, expected_url=url)
        if profile_data and loaded_path and is_valid:
            return profile_data, loaded_path

    rebuilt_index = rebuild_index()
    candidates = []
    if canonical_url:
        candidates.extend([
            rebuilt_index.get("canonicalUrls", {}).get(canonical_url),
            rebuilt_index.get("canonicalUrls", {}).get(canonical_url.lower()),
        ])
    if url_key:
        candidates.extend([
            rebuilt_index.get("urlKeys", {}).get(url_key),
            rebuilt_index.get("urlKeys", {}).get(url_key.lower()),
        ])
    for candidate in candidates:
        if not candidate:
            continue
        profile_path = _resolve_index_path(candidate)
        profile_data, loaded_path = load_profile_from_path(profile_path)
        is_valid, _ = validate_profile(profile_data, expected_url=url)
        if profile_data and loaded_path and is_valid:
            return profile_data, loaded_path

    return None, suggested_profile_path_for_url(url)


def save_profile(profile_data: Dict) -> str:
    ensure_manual_profile_dirs()
    source_url = profile_data.get("canonicalUrl") or profile_data.get("sourceUrl") or ""
    canonical_url = canonicalize_url(source_url)
    url_key = profile_data.get("urlKey") or url_key_from_url(canonical_url)
    now = datetime.utcnow().isoformat() + "Z"

    existing_profile, existing_path = find_profile(canonical_url)
    profile_path = existing_path if existing_profile and existing_path and os.path.exists(existing_path) else suggested_profile_path_for_url(canonical_url)

    profile_copy = dict(profile_data)
    profile_copy["version"] = profile_copy.get("version", 1)
    profile_copy["sourceUrl"] = profile_copy.get("sourceUrl") or source_url
    profile_copy["canonicalUrl"] = canonical_url
    profile_copy["urlKey"] = url_key
    profile_copy["createdAt"] = profile_copy.get("createdAt") or now
    profile_copy["updatedAt"] = now

    with open(profile_path, "w", encoding="utf-8") as handle:
        json.dump(profile_copy, handle, ensure_ascii=True, indent=2)

    index_data = load_index()
    _index_profile(index_data, profile_path, profile_copy)
    save_index(index_data)
    return profile_path
