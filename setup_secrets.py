"""
Satori — Secret Manager Provisioning Script
=============================================
Creates secrets in GCP Secret Manager from your current settings.json.
Run this ONCE to migrate existing plaintext keys into Secret Manager.

Usage:
  python setup_secrets.py                # Create secrets from settings.json
  python setup_secrets.py --dry-run      # Preview what would be created
  python setup_secrets.py --delete       # Delete Satori secrets from Secret Manager

Requires:
  pip install google-cloud-secret-manager

Prerequisites:
  1. Secret Manager API must be enabled:
     gcloud services enable secretmanager.googleapis.com --project=ai-vertex-mahad
  2. You must have roles/secretmanager.admin on the project:
     gcloud projects add-iam-policy-binding ai-vertex-mahad \
       --member="user:YOUR_EMAIL" --role="roles/secretmanager.admin"
  3. The Cloud Run service account needs roles/secretmanager.secretAccessor:
     gcloud projects add-iam-policy-binding ai-vertex-mahad \
       --member="serviceAccount:PROJECT_NUMBER-compute@developer.gserviceaccount.com" \
       --role="roles/secretmanager.secretAccessor"
"""

import os
import sys
import json

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")
GCP_PROJECT = "ai-vertex-mahad"

# Maps settings.json key → Secret Manager secret ID
SECRETS_TO_CREATE = {
    "gemini_api_key":     "satori-gemini-api-key",
}


def load_current_settings():
    """Load settings.json to extract current key values."""
    if not os.path.exists(SETTINGS_FILE):
        print(f"ERROR: {SETTINGS_FILE} not found")
        sys.exit(1)
    with open(SETTINGS_FILE) as f:
        return json.load(f)


def create_secrets(dry_run=False):
    """Create or update secrets in Secret Manager."""
    from google.cloud import secretmanager
    from google.api_core import exceptions

    settings = load_current_settings()
    client = secretmanager.SecretManagerServiceClient()
    parent = f"projects/{GCP_PROJECT}"

    print(f"{'[DRY RUN] ' if dry_run else ''}Provisioning secrets in {parent}\n")

    for settings_key, secret_id in SECRETS_TO_CREATE.items():
        value = settings.get(settings_key, "")
        if not value:
            print(f"  SKIP  {secret_id} — no value in settings.json for '{settings_key}'")
            continue

        masked = value[:4] + "..." + value[-4:] if len(value) > 8 else "****"
        print(f"  {secret_id}")
        print(f"    Source: settings.json['{settings_key}'] = {masked}")

        if dry_run:
            print(f"    Action: would create/update secret\n")
            continue

        # Create the secret (or skip if it already exists)
        try:
            client.create_secret(
                request={
                    "parent": parent,
                    "secret_id": secret_id,
                    "secret": {"replication": {"automatic": {}}},
                }
            )
            print(f"    Created secret: {secret_id}")
        except exceptions.AlreadyExists:
            print(f"    Secret already exists: {secret_id}")

        # Add the secret version with the actual value
        secret_path = f"{parent}/secrets/{secret_id}"
        client.add_secret_version(
            request={
                "parent": secret_path,
                "payload": {"data": value.encode("UTF-8")},
            }
        )
        print(f"    Added new version with current value\n")

    print("\nDone! Secrets are now stored in Secret Manager.")
    print("\nNext steps:")
    print("  1. Remove plaintext keys from settings.json")
    print("  2. Ensure Secret Manager API is enabled:")
    print(f"     gcloud services enable secretmanager.googleapis.com --project={GCP_PROJECT}")
    print("  3. Grant the Cloud Run service account access:")
    print(f"     gcloud projects add-iam-policy-binding {GCP_PROJECT} \\")
    print(f"       --member=\"serviceAccount:$(gcloud projects describe {GCP_PROJECT} --format='value(projectNumber)')-compute@developer.gserviceaccount.com\" \\")
    print(f"       --role=\"roles/secretmanager.secretAccessor\"")


def delete_secrets(dry_run=False):
    """Delete Satori secrets from Secret Manager."""
    from google.cloud import secretmanager
    from google.api_core import exceptions

    client = secretmanager.SecretManagerServiceClient()

    print(f"{'[DRY RUN] ' if dry_run else ''}Deleting Satori secrets\n")

    for settings_key, secret_id in SECRETS_TO_CREATE.items():
        secret_path = f"projects/{GCP_PROJECT}/secrets/{secret_id}"
        print(f"  {secret_id}")

        if dry_run:
            print(f"    Action: would delete\n")
            continue

        try:
            client.delete_secret(request={"name": secret_path})
            print(f"    Deleted\n")
        except exceptions.NotFound:
            print(f"    Not found (already deleted)\n")

    print("Done!")


def strip_keys_from_settings():
    """Remove plaintext API keys from settings.json after provisioning."""
    settings = load_current_settings()
    changed = False
    for key in SECRETS_TO_CREATE:
        if settings.get(key):
            settings[key] = ""
            changed = True
            print(f"  Cleared '{key}' from settings.json")

    if changed:
        with open(SETTINGS_FILE, "w") as f:
            json.dump(settings, f, indent=2)
        print("  Settings saved (keys removed)")
    else:
        print("  No keys to clear")


if __name__ == "__main__":
    args = sys.argv[1:]
    dry_run = "--dry-run" in args

    if "--delete" in args:
        delete_secrets(dry_run=dry_run)
    elif "--strip-settings" in args:
        strip_keys_from_settings()
    else:
        create_secrets(dry_run=dry_run)

    print()
    input("Press Enter to close...")
