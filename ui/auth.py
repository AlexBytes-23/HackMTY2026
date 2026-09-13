"""Credential store for the LedgerLens interface.

Replaces the previous ``UsernamePassword.xlsx``, which held usernames and
passwords **in clear text** in a file committed to a public repository.  Anyone
who could read the repository could read every account's password.

This store never writes a password anywhere.  It writes a per-user random salt
and a PBKDF2-HMAC-SHA256 derivation, verifies with a constant-time comparison,
and lives in a file that ``.gitignore`` excludes.  A leaked store still costs an
attacker a brute-force per account rather than handing them the passwords.

It is deliberately NOT a migration of the old spreadsheet: those passwords are
public and must be considered burned, so the store starts empty and users
re-register.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import tempfile
from dataclasses import dataclass
from pathlib import Path

# 600,000 iterations is the OWASP floor for PBKDF2-HMAC-SHA256 at time of
# writing. Raising it is safe: existing records carry their own iteration count
# and keep verifying.
ITERATIONS = 600_000
SALT_BYTES = 16
MIN_USERNAME = 3
MIN_PASSWORD = 8

DEFAULT_STORE = Path(__file__).resolve().parent / "credentials.json"


class AuthError(RuntimeError):
    """Registration or verification was refused, with a reason for the user."""


@dataclass(frozen=True)
class Credential:
    username: str
    salt: str
    hash: str
    iterations: int

    def to_json(self) -> dict:
        return {"username": self.username, "salt": self.salt,
                "hash": self.hash, "iterations": self.iterations}


def _derive(password: str, salt: bytes, iterations: int) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations).hex()


def _load(store: Path) -> dict[str, Credential]:
    if not store.exists():
        return {}
    try:
        raw = json.loads(store.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AuthError(
            "The credential store is corrupt and cannot be read: %s" % exc) from exc
    out: dict[str, Credential] = {}
    for record in raw.get("users", []):
        try:
            credential = Credential(
                username=str(record["username"]), salt=str(record["salt"]),
                hash=str(record["hash"]), iterations=int(record["iterations"]))
        except (KeyError, TypeError, ValueError):
            continue  # skip a malformed row rather than fail every login
        out[credential.username.lower()] = credential
    return out


def _save(store: Path, credentials: dict[str, Credential]) -> None:
    payload = {
        "_about": "PBKDF2-HMAC-SHA256 password verifiers. No plaintext password "
                  "is stored here. Do not commit this file.",
        "users": [c.to_json() for c in
                  sorted(credentials.values(), key=lambda c: c.username.lower())],
    }
    store.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_path = tempfile.mkstemp(dir=str(store.parent), suffix=".tmp")
    with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(temp_path, store)
    try:
        os.chmod(store, 0o600)
    except OSError:
        pass  # best effort; Windows ACLs are not POSIX modes


def user_count(store: Path | None = None) -> int:
    return len(_load(Path(store) if store else DEFAULT_STORE))


def create_user(username: str, password: str,
                store: Path | None = None) -> None:
    """Register a new account. Raises :class:`AuthError` with a usable message."""
    store = Path(store) if store else DEFAULT_STORE
    username = (username or "").strip()
    password = password or ""

    if len(username) < MIN_USERNAME:
        raise AuthError("Username must contain at least %d characters."
                        % MIN_USERNAME)
    if len(password) < MIN_PASSWORD:
        raise AuthError("Password must contain at least %d characters."
                        % MIN_PASSWORD)

    credentials = _load(store)
    if username.lower() in credentials:
        raise AuthError("That username is already registered.")

    salt = secrets.token_bytes(SALT_BYTES)
    credentials[username.lower()] = Credential(
        username=username, salt=salt.hex(),
        hash=_derive(password, salt, ITERATIONS), iterations=ITERATIONS)
    _save(store, credentials)


def verify_user(username: str, password: str,
                store: Path | None = None) -> bool:
    """Check a login. Constant-time, and equally slow for unknown usernames."""
    store = Path(store) if store else DEFAULT_STORE
    credentials = _load(store)
    record = credentials.get((username or "").strip().lower())

    if record is None:
        # Spend the same work on an unknown user, so response time does not
        # reveal whether the username exists.
        _derive(password or "", b"\x00" * SALT_BYTES, ITERATIONS)
        return False

    candidate = _derive(password or "", bytes.fromhex(record.salt),
                        record.iterations)
    return hmac.compare_digest(candidate, record.hash)
