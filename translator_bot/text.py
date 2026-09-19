"""Piccole utilità per rispettare i limiti dei messaggi Discord."""

import re


def split_message(text: str, limit: int = 2000) -> list[str]:
    """Divide senza perdere testo; conta UTF-16 in modo conservativo per le emoji."""
    if limit < 2:
        raise ValueError("Il limite deve essere almeno 2.")
    chunks = []
    while text:
        units = 0
        end = 0
        for char in text:
            width = 2 if ord(char) > 0xFFFF else 1
            if units + width > limit:
                break
            units += width
            end += 1
        if end < len(text):
            boundary = max(text.rfind("\n", 0, end), text.rfind(" ", 0, end))
            if boundary >= end // 2:
                end = boundary + 1
        chunks.append(text[:end])
        text = text[end:]
    return chunks


def webhook_username(display_name: str, suffix: str) -> str:
    # Alcuni nomi riservati non vengono accettati da Discord nei webhook.
    name = re.sub(r"[\x00-\x1f\x7f]", "", display_name).strip() or "Utente"
    name = re.sub(r"discord|clyde", "Utente", name, flags=re.IGNORECASE)
    suffix = re.sub(r"[\x00-\x1f\x7f]", "", suffix)
    suffix = re.sub(r"discord|clyde", "Bot", suffix, flags=re.IGNORECASE)[:40]
    return name[:80 - len(suffix)] + suffix
