import hashlib
import json
import math
import os
import re
from datetime import datetime
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from manual_prepare_timeline import validate_prepare_timeline


MANUAL_PROFILES_DIR = "manual_profiles"
MANUAL_PROFILES_INDEX_PATH = os.path.join(MANUAL_PROFILES_DIR, "index.json")
MANUAL_PROFILES_PROFILE_DIR = os.path.join(MANUAL_PROFILES_DIR, "profiles")


@dataclass(frozen=True)
class ManualProfileResolution:
    """Result of resolving a profile for an active runtime.

    ``find_profile`` historically returned ``(profile, path)`` and silently
    skipped malformed candidates.  Runtime callers need to distinguish an
    absent profile from a profile that must not be bypassed, so the stricter
    resolver returns this explicit result instead.
    """

    status: str
    profile: Optional[Dict] = None
    path: Optional[str] = None
    error: Optional[str] = None
    candidates: Tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        return self.status == "VALID" and isinstance(self.profile, dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "path": self.path,
            "error": self.error,
            "candidates": list(self.candidates),
        }


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


def _runtime_url_identity(url: Optional[str]) -> str:
    """Normalize a URL for strict profile/runtime identity checks.

    ``canonicalize_url`` intentionally drops query strings for historical
    profile indexing.  A runtime profile must not accidentally be reused for
    a different query/version route, so this identity keeps the query while
    still normalizing scheme, host and trailing slashes.
    """
    if not url:
        return ""
    parsed = urlparse(str(url).strip())
    scheme = (parsed.scheme or "https").lower()
    netloc = parsed.netloc.lower()
    path = parsed.path or ""
    if path != "/":
        path = path.rstrip("/")
    query = f"?{parsed.query}" if parsed.query else ""
    return f"{scheme}://{netloc}{path}{query}"


def _runtime_profile_url(profile_data: Optional[Dict]) -> str:
    """Return the URL identity that should gate runtime profile use.

    Older profiles were normalized by ``canonicalize_url`` and therefore may
    have a query-less ``canonicalUrl`` while retaining the original query in
    ``sourceUrl``.  Prefer that source URL only in this precise case so a
    versioned/query route is not silently shared with another route.  A
    genuinely different source URL is still ignored when the canonical URL is
    the authoritative value.
    """
    if not isinstance(profile_data, dict):
        return ""
    canonical_url = str(profile_data.get("canonicalUrl") or "").strip()
    source_url = str(profile_data.get("sourceUrl") or "").strip()
    canonical_identity = _runtime_url_identity(canonical_url)
    source_identity = _runtime_url_identity(source_url)
    if (
        canonical_identity and source_identity and
        "?" not in canonical_identity and "?" in source_identity
    ):
        return source_url
    return canonical_url or source_url


def _finite_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def _validate_layout_entries(entries: Any, label: str) -> Tuple[bool, Optional[str]]:
    if not isinstance(entries, list) or not entries:
        return False, f"profile is missing {label}"

    seen_indices = set()
    for position, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            return False, f"invalid {label} entry:{position}"
        raw_index = entry.get("index")
        if isinstance(raw_index, bool):
            return False, f"invalid {label} index:{position}"
        try:
            numeric_index = float(raw_index)
            index = int(numeric_index)
        except (TypeError, ValueError, OverflowError):
            return False, f"invalid {label} index:{position}"
        if not math.isfinite(numeric_index) or numeric_index != index or index < 0:
            return False, f"invalid {label} index:{position}"
        if index in seen_indices:
            return False, f"duplicate {label} index:{index}"
        seen_indices.add(index)
        if not _finite_number(entry.get("x")) or not _finite_number(entry.get("y")):
            return False, f"invalid {label} coordinate:{position}"

    return True, None


def _validate_profile_viewport(profile_data: Dict) -> Tuple[bool, Optional[str]]:
    viewport = profile_data.get("viewport")
    if not isinstance(viewport, dict):
        return False, "profile is missing viewport"
    width = viewport.get("width")
    height = viewport.get("height")
    if not _finite_number(width) or not _finite_number(height):
        return False, "profile viewport is invalid"
    if float(width) <= 0 or float(height) <= 0:
        return False, "profile viewport must be positive"

    coordinate_space = str(profile_data.get("coordinateSpace") or "").strip()
    match = re.fullmatch(
        r"screen_(\d+)x(\d+)(?:_with_menu)?",
        coordinate_space,
        flags=re.IGNORECASE,
    )
    if not match:
        return False, "profile coordinateSpace is unsupported"
    space_width, space_height = int(match.group(1)), int(match.group(2))
    if int(round(float(width))) != space_width or int(round(float(height))) != space_height:
        return False, "profile viewport does not match coordinateSpace"
    return True, None


def validate_profile(
    profile_data: Optional[Dict],
    expected_url: Optional[str] = None,
    *,
    require_prepare_actions: bool = False,
    require_runtime_metadata: bool = False,
) -> Tuple[bool, Optional[str]]:
    if not isinstance(profile_data, dict):
        return False, "profile is not a JSON object"

    layout = profile_data.get("layout") or {}
    icons = layout.get("icons") or []
    slots = layout.get("slots") or []
    if not icons:
        return False, "profile is missing icons"
    if not slots:
        return False, "profile is missing slots"

    if require_prepare_actions and not profile_data.get("prepareActions"):
        return False, "profile is missing prepareActions"

    if "prepareActions" in profile_data or require_prepare_actions:
        timeline_valid, timeline_reason = validate_prepare_timeline(
            profile_data.get("prepareActions") or [],
            profile_data.get("prepareTiming") or {},
        )
        if not timeline_valid:
            return False, f"invalid PREPARE timeline: {timeline_reason}"

    if expected_url:
        expected_canonical = canonicalize_url(expected_url)
        profile_canonical = canonicalize_url(
            profile_data.get("canonicalUrl") or profile_data.get("sourceUrl") or ""
        )
        if expected_canonical and profile_canonical and expected_canonical != profile_canonical:
            return False, "profile URL does not match expected URL"

    if require_runtime_metadata:
        expected_identity = _runtime_url_identity(expected_url)
        profile_identity = _runtime_url_identity(_runtime_profile_url(profile_data))
        if not profile_identity:
            return False, "profile is missing canonical URL"
        if expected_identity and profile_identity != expected_identity:
            return False, "profile URL does not exactly match expected URL"

        viewport_valid, viewport_reason = _validate_profile_viewport(profile_data)
        if not viewport_valid:
            return False, viewport_reason

        layout = profile_data.get("layout")
        if not isinstance(layout, dict):
            return False, "profile is missing layout"
        icons_valid, icons_reason = _validate_layout_entries(layout.get("icons"), "icons")
        if not icons_valid:
            return False, icons_reason
        slots_valid, slots_reason = _validate_layout_entries(layout.get("slots"), "slots")
        if not slots_valid:
            return False, slots_reason

        if require_prepare_actions and not isinstance(profile_data.get("prepareActions"), list):
            return False, "profile prepareActions must be a list"

    return True, None


def validate_runtime_profile(
    profile_data: Optional[Dict],
    expected_url: Optional[str] = None,
) -> Tuple[bool, Optional[str]]:
    """Validate the stricter contract required before manual input."""
    return validate_profile(
        profile_data,
        expected_url=expected_url,
        require_prepare_actions=True,
        require_runtime_metadata=True,
    )


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
    """Resolve index entries and generated paths relative to the project root.

    Index entries are stored as ``profiles/<file>.json`` while the generated
    suggested path already contains the ``manual_profiles/`` prefix.  Treating
    both forms as paths relative to ``MANUAL_PROFILES_DIR`` produced the
    invalid ``manual_profiles/manual_profiles/...`` candidate and made one
    valid profile look ambiguous at runtime.
    """
    raw_path = str(relative_or_absolute_path or "").strip()
    if not raw_path:
        return ""
    if os.path.isabs(raw_path):
        return os.path.abspath(raw_path)

    normalized = os.path.normpath(raw_path)
    profile_root = os.path.normpath(MANUAL_PROFILES_DIR)
    if normalized == profile_root or normalized.startswith(profile_root + os.sep):
        return os.path.abspath(normalized)
    return os.path.abspath(os.path.join(MANUAL_PROFILES_DIR, normalized))


def _unique_existing_candidate_paths(url: str, index_data: Dict) -> List[str]:
    """Collect indexed and scanned candidates without accepting fuzzy URLs."""
    canonical_url = canonicalize_url(url)
    raw_candidates = []

    if canonical_url:
        raw_candidates.extend([
            index_data.get("canonicalUrls", {}).get(canonical_url),
            index_data.get("canonicalUrls", {}).get(canonical_url.lower()),
        ])
        # The suggested filename is deterministic even when an older profile
        # was copied into the directory without updating index.json.  Include
        # it so a malformed/stale file is reported as INVALID instead of
        # silently turning into the automatic-flow ABSENT state.
        suggested_path = suggested_profile_path_for_url(url)
        if os.path.exists(suggested_path):
            raw_candidates.append(suggested_path)

    expected_identity = _runtime_url_identity(url)
    for profile_path, profile_data in scan_profiles():
        profile_url = _runtime_profile_url(profile_data)
        profile_identity = _runtime_url_identity(profile_url)
        if profile_identity == expected_identity:
            raw_candidates.append(profile_path)

    result = []
    seen = set()
    for raw_path in raw_candidates:
        if not raw_path:
            continue
        profile_path = _resolve_index_path(str(raw_path))
        normalized_path = os.path.normcase(os.path.abspath(profile_path))
        if normalized_path in seen:
            continue
        seen.add(normalized_path)
        result.append(profile_path)
    return result


def resolve_profile_for_runtime(
    url: str,
    explicit_path: Optional[str] = None,
) -> ManualProfileResolution:
    """Resolve a profile with fail-closed runtime semantics.

    ``explicit_path`` has strict precedence.  When it is absent, only a
    profile whose full runtime identity matches ``url`` can be accepted.  A
    candidate that exists but is malformed or mismatched is never converted
    into the ``ABSENT`` state, because that would incorrectly enable the
    automatic fallback path.
    """
    if explicit_path:
        resolved_path = os.path.abspath(explicit_path)
        if not os.path.exists(resolved_path):
            return ManualProfileResolution(
                "INVALID",
                path=resolved_path,
                error="manual profile path does not exist",
                candidates=(resolved_path,),
            )
        profile_data, loaded_path = load_profile_from_path(resolved_path)
        if not isinstance(profile_data, dict):
            return ManualProfileResolution(
                "INVALID",
                path=loaded_path or resolved_path,
                error="manual profile could not be loaded",
                candidates=(loaded_path or resolved_path,),
            )
        is_valid, error_message = validate_runtime_profile(profile_data, expected_url=url)
        if not is_valid:
            return ManualProfileResolution(
                "INVALID",
                path=loaded_path or resolved_path,
                error=error_message or "manual profile is invalid",
                candidates=(loaded_path or resolved_path,),
            )
        return ManualProfileResolution(
            "VALID",
            profile=profile_data,
            path=loaded_path or resolved_path,
            candidates=(loaded_path or resolved_path,),
        )

    if not url:
        return ManualProfileResolution("ABSENT", path=None)

    ensure_manual_profile_dirs()
    candidate_paths = _unique_existing_candidate_paths(url, load_index())
    if not candidate_paths:
        # Rebuild the index once to recover profiles created before an index
        # update or profiles copied into the profile directory manually.
        candidate_paths = _unique_existing_candidate_paths(url, rebuild_index())

    if not candidate_paths:
        return ManualProfileResolution(
            "ABSENT",
            path=suggested_profile_path_for_url(url),
        )

    valid_candidates = []
    invalid_candidates = []
    for profile_path in candidate_paths:
        profile_data, loaded_path = load_profile_from_path(profile_path)
        candidate_path = loaded_path or profile_path
        if not isinstance(profile_data, dict):
            invalid_candidates.append((candidate_path, "manual profile could not be loaded"))
            continue
        is_valid, error_message = validate_runtime_profile(profile_data, expected_url=url)
        if is_valid:
            valid_candidates.append((profile_data, candidate_path))
        else:
            invalid_candidates.append((candidate_path, error_message or "manual profile is invalid"))

    all_paths = tuple(path for path in candidate_paths)
    if len(valid_candidates) > 1:
        return ManualProfileResolution(
            "AMBIGUOUS",
            error="multiple valid manual profiles match the requested URL",
            candidates=all_paths,
        )
    if valid_candidates and invalid_candidates:
        return ManualProfileResolution(
            "AMBIGUOUS",
            error="both valid and invalid manual profiles match the requested URL",
            candidates=all_paths,
        )
    if valid_candidates:
        profile_data, profile_path = valid_candidates[0]
        return ManualProfileResolution(
            "VALID",
            profile=profile_data,
            path=profile_path,
            candidates=all_paths,
        )

    invalid_path, invalid_reason = invalid_candidates[0]
    return ManualProfileResolution(
        "INVALID",
        path=invalid_path,
        error=invalid_reason,
        candidates=all_paths,
    )


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
