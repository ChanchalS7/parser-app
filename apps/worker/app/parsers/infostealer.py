"""Infostealer-log aware extraction helpers.

Reference: Lexfo "infostealer-parser" write-up. Stealer logs store credentials
as repeated blocks whose lines are prefixed keys, and different families spell
the same field differently. These helpers normalize that grammar and add a few
IOC types on top of the base extractor, without changing any existing keys the
rest of the pipeline already depends on.
"""
from __future__ import annotations

import hashlib
import os
import re
from typing import Any

# --- Extra IOC patterns (additive to the base extractor) ------------------
SHA1_RE = re.compile(r"\b[a-fA-F0-9]{40}\b")
CVE_RE = re.compile(r"\bCVE-\d{4}-\d{4,7}\b", re.IGNORECASE)
BTC_RE = re.compile(r"\b(?:bc1[a-z0-9]{25,90}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})\b")

# --- Credential-block grammar ---------------------------------------------
# Leetspeak folding so "P455W0RD" normalizes to "password".
_L33T = str.maketrans({"4": "a", "5": "s", "0": "o", "1": "l", "3": "e"})

_CRED_FIELD_PREFIXES = {
    "application": {"soft", "software", "browser", "application", "app", "storage", "profile"},
    "url": {"url", "host", "hostname", "website", "web", "uri", "link"},
    "username": {"login", "user", "username", "usr", "email", "account"},
    "password": {"password", "pass", "passwd", "pwd"},
}
_PREFIX_TO_FIELD = {tok: field for field, toks in _CRED_FIELD_PREFIXES.items() for tok in toks}

# Well-known stealer artifact filenames / directories -> category tag.
_STEALER_FILENAMES = {
    "passwords.txt": "stealer_password",
    "passwords": "stealer_password",
    "all passwords.txt": "stealer_password",
    "_allpasswords_list.txt": "stealer_password",
    "userinformation.txt": "system_info",
    "information.txt": "system_info",
    "system.txt": "system_info",
    "installedsoftware.txt": "installed_software",
    "installedbrowsers.txt": "installed_browsers",
    "processlist.txt": "process_list",
    "domaindetects.txt": "domain_detects",
}
_STEALER_DIR_HINTS = {
    "cookies": "cookies",
    "autofills": "autofill",
    "autofill": "autofill",
    "filegrabber": "desktop_file",
}

_CRED_LINE_RE = re.compile(r"^\s*([A-Za-z][\w .\-]{0,24}?)\s*[:=]\s*(.*)$")


def stealer_category(rel_path: str) -> str | None:
    """Classify a file within a stealer log by name/parent dir, else None."""
    base = os.path.basename(rel_path).lower()
    if base in _STEALER_FILENAMES:
        return _STEALER_FILENAMES[base]
    parts = [p.lower() for p in re.split(r"[\\/]", rel_path)]
    for hint, cat in _STEALER_DIR_HINTS.items():
        if hint in parts:
            return cat
    return None


def _normalize_prefix(raw_key: str) -> str | None:
    token = raw_key.strip().lower().rstrip(":").strip()
    token = token.translate(_L33T)
    token = re.sub(r"[^a-z]", "", token)
    return _PREFIX_TO_FIELD.get(token)


def _hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()


def parse_credential_blocks(text: str) -> list[dict[str, Any]]:
    """Parse stealer-style credential blocks into unified records.

    Lines look like ``KEY: value`` where KEY is a family-specific spelling of
    application/url/username/password. Records are delimited by blank lines or
    by a repeated field. Secrets are hashed, never returned in plaintext.
    """
    records: list[dict[str, Any]] = []
    current: dict[str, Any] = {}

    def flush() -> None:
        nonlocal current
        if current.get("username") or current.get("secret_hash"):
            records.append(current)
        current = {}

    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        match = _CRED_LINE_RE.match(stripped)
        if not match:
            continue
        field = _normalize_prefix(match.group(1))
        value = match.group(2).strip().strip('"').strip("'")
        if not field or not value:
            continue
        marker = "secret_hash" if field == "password" else field
        if marker in current:
            flush()
        if field == "password":
            current["secret_hash"] = value
        elif field == "username":
            current["username"] = value
        else:
            current[field] = value  # application | url
    flush()
    return records
