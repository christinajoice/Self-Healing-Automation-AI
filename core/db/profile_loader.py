"""
DB Profile Loader
=================
Loads database connection profiles from three sources (highest priority wins):

  1. Environment variables  — for CI/CD, Docker, production
  2. db_profiles.yaml       — for local dev, persists across restarts
  3. Uploaded via API       — for users without file system access

Profile name convention for env vars:
  DB_<PROFILE_UPPER>_TYPE       e.g. DB_HILABS_RDS_TYPE=postgres
  DB_<PROFILE_UPPER>_HOST
  DB_<PROFILE_UPPER>_PORT
  DB_<PROFILE_UPPER>_DATABASE
  DB_<PROFILE_UPPER>_USERNAME
  DB_<PROFILE_UPPER>_PASSWORD
  DB_<PROFILE_UPPER>_ACCOUNT    (Snowflake only)
  DB_<PROFILE_UPPER>_WAREHOUSE  (Snowflake only)
  DB_<PROFILE_UPPER>_SCHEMA     (Snowflake only)

The file is hot-reloaded: if db_profiles.yaml changes on disk the next
call to load_profiles() will pick up the changes without a server restart.
"""

import os
import yaml
from pathlib import Path
from typing import Dict, List, Optional

PROFILES_FILE = Path("db_profiles.yaml")

_cache: Optional[Dict] = None
_file_mtime: float = 0.0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_env_refs(value: str) -> str:
    """Expand ${VAR_NAME} references using environment variables."""
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        env_var = value[2:-1]
        return os.environ.get(env_var, value)
    return value


def _load_from_yaml() -> Dict:
    if not PROFILES_FILE.exists():
        return {}
    with open(PROFILES_FILE, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    profiles = data.get("profiles", {})
    # Resolve ${VAR} references in all values
    for profile in profiles.values():
        for key, val in profile.items():
            profile[key] = _resolve_env_refs(val)
    return profiles


def _load_from_env(base: Dict) -> Dict:
    """
    Scan environment variables for DB_<PROFILE>_TYPE entries to discover
    profile names, then fill remaining fields.  Env vars override YAML.
    """
    profiles = {k: dict(v) for k, v in base.items()}  # deep copy

    # Discover profiles from *_TYPE env vars
    for key, val in os.environ.items():
        if key.startswith("DB_") and key.endswith("_TYPE"):
            name = key[3:-5].lower()  # DB_HILABS_RDS_TYPE → hilabs_rds
            profiles.setdefault(name, {})["type"] = val.lower()

    # Fill remaining fields
    FIELDS = [
        "host", "port", "database", "username", "password",
        "account", "warehouse", "schema", "region",
    ]
    for name in list(profiles.keys()):
        prefix = f"DB_{name.upper()}_"
        for field in FIELDS:
            env_val = os.environ.get(prefix + field.upper())
            if env_val:
                profiles[name][field] = env_val

    return profiles


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_profiles(force_reload: bool = False) -> Dict:
    """
    Return all profiles as a dict keyed by profile name.
    Result is cached; cache is invalidated when db_profiles.yaml changes.
    """
    global _cache, _file_mtime

    current_mtime = (
        PROFILES_FILE.stat().st_mtime if PROFILES_FILE.exists() else 0.0
    )
    if _cache is not None and not force_reload and current_mtime == _file_mtime:
        return _cache

    yaml_profiles = _load_from_yaml()
    merged = _load_from_env(yaml_profiles)

    _cache = merged
    _file_mtime = current_mtime
    return _cache


def get_profile(name: str) -> Dict:
    """Return a single profile by name, raising ValueError if not found."""
    profiles = load_profiles()
    if name not in profiles:
        available = list(profiles.keys())
        raise ValueError(
            f"DB profile '{name}' not found. "
            f"Available: {available}. "
            f"Add it to db_profiles.yaml or set DB_{name.upper()}_TYPE env var."
        )
    return profiles[name]


def list_profiles() -> List[str]:
    """Return names of all configured profiles."""
    return list(load_profiles().keys())


def save_uploaded_profiles(yaml_content: str) -> None:
    """
    Persist a user-uploaded YAML string to db_profiles.yaml and
    invalidate the in-memory cache so it is reloaded immediately.
    """
    global _cache
    data = yaml.safe_load(yaml_content)
    if not isinstance(data, dict) or "profiles" not in data:
        raise ValueError(
            "Invalid format: uploaded file must have a top-level 'profiles' key."
        )
    with open(PROFILES_FILE, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
    _cache = None  # force reload on next access
