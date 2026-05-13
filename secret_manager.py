"""
Satori — Secret Manager Integration
=====================================
Loads API keys from GCP Secret Manager instead of plaintext files.

Resolution order for each secret:
  1. Environment variable (for local dev: export SATORI_GEMINI_API_KEY=...)
  2. GCP Secret Manager (production)
  3. None (caller must handle missing keys gracefully)

Usage:
  from secret_manager import get_secret
  gemini_key = get_secret("gemini_api_key")
"""

import os
import logging

log = logging.getLogger("satori.secrets")

# ── Config ──────────────────────────────────────────────────────────────
GCP_PROJECT = os.environ.get("SATORI_GCP_PROJECT", "ai-vertex-mahad")

# Maps Satori secret names → Secret Manager secret IDs
SECRET_MAP = {
    "gemini_api_key":     "satori-gemini-api-key",
}

# Maps Satori secret names → environment variable overrides
ENV_MAP = {
    "gemini_api_key":     "SATORI_GEMINI_API_KEY",
}

# ── In-memory cache (avoids repeated API calls) ────────────────────────
_cache = {}
_sm_client = None
_sm_available = None


def _get_sm_client():
    """Lazily initialise the Secret Manager client."""
    global _sm_client, _sm_available
    if _sm_available is False:
        return None
    if _sm_client is not None:
        return _sm_client
    try:
        from google.cloud import secretmanager
        _sm_client = secretmanager.SecretManagerServiceClient()
        _sm_available = True
        log.info("Secret Manager client initialised")
        return _sm_client
    except Exception as e:
        _sm_available = False
        log.warning("Secret Manager unavailable (%s) — falling back to env vars", e)
        return None


def get_secret(name: str, default: str = "") -> str:
    """
    Retrieve a secret value by its Satori name.

    Args:
        name:    Satori secret name (e.g. "gemini_api_key")
        default: Value to return if the secret cannot be resolved

    Returns:
        The secret value, or default if not found.
    """
    # Return from cache if already resolved
    if name in _cache:
        return _cache[name]

    value = None

    # 1. Check environment variable
    env_var = ENV_MAP.get(name)
    if env_var:
        value = os.environ.get(env_var)
        if value:
            log.info("Secret '%s' loaded from env var %s", name, env_var)

    # 2. Check GCP Secret Manager
    if not value:
        secret_id = SECRET_MAP.get(name)
        if secret_id:
            client = _get_sm_client()
            if client:
                try:
                    resource = f"projects/{GCP_PROJECT}/secrets/{secret_id}/versions/latest"
                    response = client.access_secret_version(request={"name": resource})
                    value = response.payload.data.decode("UTF-8")
                    log.info("Secret '%s' loaded from Secret Manager (%s)", name, secret_id)
                except Exception as e:
                    log.warning("Failed to load secret '%s' from Secret Manager: %s", name, e)

    # 3. Fall back to default
    if not value:
        if default:
            log.info("Secret '%s' using provided default", name)
        else:
            log.warning("Secret '%s' not found in env or Secret Manager", name)
        value = default

    _cache[name] = value
    return value


def clear_cache():
    """Clear the in-memory secret cache (e.g. after settings change)."""
    _cache.clear()
    log.info("Secret cache cleared")


def is_secret_key(name: str) -> bool:
    """Check if a settings key is a secret that should not be stored in plaintext."""
    return name in SECRET_MAP


def get_all_secret_names():
    """Return a list of all managed secret names."""
    return list(SECRET_MAP.keys())


def mask_secret(value: str) -> str:
    """Return a masked version of a secret for safe display (e.g. 'AIza...7Cpw')."""
    if not value or len(value) < 8:
        return "****"
    return value[:4] + "..." + value[-4:]
