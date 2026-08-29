#!/usr/bin/env python3
"""Create anonymized eval candidates from a Telegram Desktop JSON export.

The source export is treated as untrusted data. This script only reads it,
extracts mirrored client messages from forum topics and writes a JSONL file
that still requires human review before it is committed as an eval fixture.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any


CLIENT_PREFIX = "👤 Клиент:"
PROFILE_CARD_RE = re.compile(r"(?:^|\s)🆔\s*\d+\s*📛\s*Имя:", re.IGNORECASE)
TRANSPORT_COMMAND_RE = re.compile(r"^/(?:start|help)(?:@\w+)?$", re.IGNORECASE)
URL_RE = re.compile(r"(?:https?://|t\.me/|www\.)\S+", re.IGNORECASE)
DOMAIN_RE = re.compile(
    r"(?<![@\w])(?:[a-zа-я0-9-]+\.)+(?:ru|com|org|net|io|рф)\b(?:/\S*)?",
    re.IGNORECASE,
)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-zА-Яа-я]{2,}")
USERNAME_RE = re.compile(r"(?<!\w)@[A-Za-z0-9_]{3,}")
INN_RE = re.compile(r"(?i)(ИНН\s*[:№]?\s*)\d{10,12}\b")
LONG_NUMBER_RE = re.compile(r"(?<!\d)\d{9,}(?!\d)")
PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d ()-]{8,}\d)(?!\w)")
NAME_INTRO_RE = re.compile(
    r"(?i)\b(меня зовут|мо[её] имя)\s+[А-ЯЁA-Z][А-ЯЁA-Za-zА-Яа-яё-]+"
)
QUOTED_COMPANY_RE = re.compile(
    r"\b(ООО|ОАО|ПАО|АО|АНО)\s+[«\"][^»\"]+[»\"]",
    re.IGNORECASE,
)
LEGAL_NAME_RE = re.compile(
    r"(?i)(Наименование\s*:\s*)(.+?)(?=\s+ИНН\s*:|$)"
)


def flatten_text(value: Any) -> str:
    """Flatten Telegram's string-or-rich-entity text representation."""
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for item in value:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            parts.append(str(item.get("text") or ""))
    return "".join(parts)


def anonymize_text(value: str) -> str:
    """Mask common identifiers while retaining the business intent."""
    text = value.strip()
    text = URL_RE.sub("[LINK]", text)
    text = DOMAIN_RE.sub("[LINK]", text)
    text = EMAIL_RE.sub("[EMAIL]", text)
    text = USERNAME_RE.sub("@user", text)
    text = INN_RE.sub(r"\1[INN]", text)
    text = PHONE_RE.sub("[PHONE_OR_ACCOUNT]", text)
    text = LONG_NUMBER_RE.sub("[LONG_NUMBER]", text)
    text = NAME_INTRO_RE.sub(r"\1 [NAME]", text)
    text = QUOTED_COMPANY_RE.sub(r"\1 «[COMPANY]»", text)
    text = LEGAL_NAME_RE.sub(r"\1[LEGAL_NAME]", text)
    return re.sub(r"\s+", " ", text).strip()


def _topic_root(
    message: dict[str, Any],
    messages_by_id: dict[Any, dict[str, Any]],
    topic_roots: set[Any],
) -> Any | None:
    current = message
    seen: set[Any] = set()
    while current and current.get("id") not in seen:
        seen.add(current.get("id"))
        parent_id = current.get("reply_to_message_id")
        if parent_id in topic_roots:
            return parent_id
        current = messages_by_id.get(parent_id)
    return None


def extract_dialogs(export: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract anonymized client turns, grouped by Telegram forum topic."""
    messages = [
        message
        for message in export.get("messages", [])
        if isinstance(message, dict)
    ]
    messages_by_id = {message.get("id"): message for message in messages}
    topic_roots = {
        message.get("id")
        for message in messages
        if message.get("type") == "service"
        and message.get("action") == "topic_created"
    }
    threads: dict[Any | None, list[dict[str, Any]]] = defaultdict(list)
    for message in messages:
        if message.get("type") != "message":
            continue
        root = _topic_root(message, messages_by_id, topic_roots)
        threads[root].append(message)

    dialogs: list[dict[str, Any]] = []
    for messages_in_topic in threads.values():
        turns: list[dict[str, str]] = []
        for message in messages_in_topic:
            body = flatten_text(message.get("text")).strip()
            if not body.startswith(CLIENT_PREFIX):
                continue
            client_text = body[len(CLIENT_PREFIX) :].strip()
            client_text = USERNAME_RE.sub("", client_text, count=1).strip()
            if (
                not client_text
                or PROFILE_CARD_RE.search(client_text)
                or TRANSPORT_COMMAND_RE.match(client_text)
            ):
                continue
            sanitized = anonymize_text(client_text)
            if sanitized:
                turns.append({"user": sanitized})
        if turns:
            dialogs.append(
                {
                    "id": f"telegram_real_{len(dialogs) + 1:03d}",
                    "source": "telegram_export_anonymized",
                    "turns": turns,
                }
            )
    return dialogs


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--max-dialogs",
        type=int,
        help="Optionally keep only the first N extracted topics.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    export = json.loads(args.input.read_text(encoding="utf-8"))
    if not isinstance(export, dict):
        raise ValueError("Telegram export root must be a JSON object")
    dialogs = extract_dialogs(export)
    if args.max_dialogs is not None:
        if args.max_dialogs < 1:
            raise ValueError("--max-dialogs must be positive")
        dialogs = dialogs[: args.max_dialogs]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as target:
        for dialog in dialogs:
            target.write(json.dumps(dialog, ensure_ascii=False) + "\n")
    turns = sum(len(dialog["turns"]) for dialog in dialogs)
    print(f"Extracted {len(dialogs)} dialogs / {turns} client turns")
    print(f"Review before committing: {args.output}")


if __name__ == "__main__":
    main()
