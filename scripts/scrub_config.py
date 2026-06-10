#!/usr/bin/env python3
"""scrub_config.py — regenerate the tracked, scrubbed `config.example.json`
from the per-machine (gitignored) `config.json`.

`config.json` carries live secrets (provider API keys, Telegram bot token,
auth.jwt_secret) and is gitignored. `config.example.json` is the tracked
mirror with the same STRUCTURE but redacted secrets, so the repo documents
the canonical config shape without leaking anything.

This script keeps the example in sync deterministically:
  - every secret-looking field (by key name) is replaced with a STABLE
    placeholder — stable so re-running produces byte-identical output (no
    churn, clean diffs, hook idempotency);
  - the redaction is by key-NAME pattern, not a hardcoded path list, so a
    newly-added secret field is scrubbed automatically (fail-safe: if in
    doubt, redact);
  - all non-secret structure (incl. the migrated tool_settings `state` shape)
    is copied verbatim.

Usage:
    python3 scripts/scrub_config.py            # write config.example.json
    python3 scripts/scrub_config.py --check    # exit 1 if example is stale
                                               # (for CI / hook dry-run)

The pre-commit hook (.githooks/pre-commit) runs this and `git add`s the result
on every commit, so the scrubbed sample can never drift from config.json.
"""
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "config.json")
DST = os.path.join(REPO, "config.example.json")

# Key-name substrings that mark a value as secret. Matched case-insensitively
# against the LEAF key. Broad on purpose — a false positive (redacting a
# non-secret) is harmless in an example file; a false negative (leaking a
# secret) is not.
SECRET_KEY_MARKERS = ("api_key", "apikey", "secret", "token", "password", "passwd")
# Inside provider `api_keys: [{name, key, usage}]` arrays the secret leaf is
# literally `key` — too generic to add to the markers globally (would redact
# innocuous "key" fields elsewhere), so handle it positionally.
API_KEYS_LIST_FIELD = "api_keys"

# Stable placeholders, matched to the conventions already in config.example.json
# so this script's first run doesn't reshuffle existing redactions.
PLACEHOLDER_DEFAULT = "REDACTED"
PLACEHOLDERS = {
    "api_key": "YOUR_API_KEY",
    "apikey": "YOUR_API_KEY",
    "bot_token": "YOUR_BOT_TOKEN",
    "token": "YOUR_TOKEN",
    "jwt_secret": "CHANGE_ME_RANDOM_SECRET",
    "secret": "CHANGE_ME_RANDOM_SECRET",
    "password": "YOUR_PASSWORD",
    "passwd": "YOUR_PASSWORD",
}


def _placeholder_for(key: str) -> str:
    kl = key.lower()
    # Exact-name wins (bot_token, jwt_secret) over substring.
    if kl in PLACEHOLDERS:
        return PLACEHOLDERS[kl]
    for marker, ph in PLACEHOLDERS.items():
        if marker in kl:
            return ph
    return PLACEHOLDER_DEFAULT


def _is_secret_key(key: str) -> bool:
    kl = key.lower()
    return any(m in kl for m in SECRET_KEY_MARKERS)


def scrub(node, *, parent_key: str = ""):
    """Return a deep-scrubbed copy of `node`. Only redacts string values whose
    key marks them secret; preserves everything else (structure + non-secret
    values) verbatim. Empty/absent secrets still get a placeholder so the
    example always documents the field."""
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if _is_secret_key(k) and isinstance(v, str):
                out[k] = _placeholder_for(k)
            elif k == API_KEYS_LIST_FIELD and isinstance(v, list):
                # Each element is {name, key, usage}; the secret leaf is the
                # bare `key` field, which is too generic to add to the global
                # markers (would redact innocuous "key" fields elsewhere) — so
                # redact it positionally here.
                redacted = []
                for elem in v:
                    if isinstance(elem, dict):
                        e = scrub(elem, parent_key=k)
                        if isinstance(e.get("key"), str):
                            e["key"] = PLACEHOLDERS["api_key"]
                        redacted.append(e)
                    else:
                        redacted.append(scrub(elem, parent_key=k))
                out[k] = redacted
            else:
                out[k] = scrub(v, parent_key=k)
        return out
    if isinstance(node, list):
        return [scrub(x, parent_key=parent_key) for x in node]
    # Scalar inside an api_keys element: the `key` leaf is redacted by the
    # dict branch above; nothing positional needed here.
    return node


def build_example() -> str:
    with open(SRC, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    scrubbed = scrub(cfg)
    # Deterministic serialisation: fixed indent, keys in source order (json
    # preserves insertion order), trailing newline. No sort_keys — preserving
    # source order keeps the example readable + diffs minimal.
    return json.dumps(scrubbed, indent=2, ensure_ascii=False) + "\n"


def main(argv):
    check = "--check" in argv
    if not os.path.exists(SRC):
        # No config.json on this machine (e.g. fresh clone, CI) — nothing to
        # mirror. Not an error; leave the tracked example as-is.
        print("scrub_config: no config.json present — skipping", file=sys.stderr)
        return 0
    new = build_example()
    old = ""
    if os.path.exists(DST):
        with open(DST, "r", encoding="utf-8") as f:
            old = f.read()
    if new == old:
        return 0  # already in sync
    if check:
        print("scrub_config: config.example.json is STALE — run "
              "`python3 scripts/scrub_config.py` and commit", file=sys.stderr)
        return 1
    with open(DST, "w", encoding="utf-8") as f:
        f.write(new)
    print("scrub_config: regenerated config.example.json")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
