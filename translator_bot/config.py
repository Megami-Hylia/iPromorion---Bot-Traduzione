"""Configurazione da .env o variabili d'ambiente; nessun segreto nel codice."""

from dataclasses import dataclass, field
import os
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

LANGUAGES = {"it": "Italiano", "fr": "Français", "en": "English"}


def positive_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        raise ValueError(f"{name} deve essere un intero positivo.") from None
    if value < 1:
        raise ValueError(f"{name} deve essere maggiore di zero.")
    return value


def api_url(name: str, default: str) -> str:
    value = os.getenv(name, default).strip().rstrip("/")
    parsed = urlparse(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError(f"{name} deve essere un URL HTTP(S) senza credenziali o query.")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError(f"{name}: usa HTTPS per i servizi remoti.")
    return value


@dataclass(frozen=True)
class Settings:
    discord_token: str = field(repr=False)
    guild_id: int | None = None
    provider: str = "libretranslate"
    libretranslate_url: str = "http://127.0.0.1:5000"
    libretranslate_api_key: str = field(default="", repr=False)
    deepl_api_key: str = field(default="", repr=False)
    deepl_api_url: str = "https://api-free.deepl.com"
    database_path: Path = Path("data/channels.sqlite3")
    webhook_suffix: str = " (tradotto)"
    translation_concurrency: int = 4
    cache_size: int = 1024
    cache_ttl: int = 600
    message_workers: int = 4
    max_queue_size: int = 200
    webhook_concurrency: int = 8
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Settings":
        # Il file è cercato nella cartella da cui si lancia il comando.
        load_dotenv(Path.cwd() / ".env")
        token = os.getenv("DISCORD_TOKEN", "").strip()
        if not token or token == "inserisci_il_token_del_bot":
            raise ValueError("Imposta DISCORD_TOKEN nel file .env.")
        guild = os.getenv("DISCORD_GUILD_ID", "").strip()
        if guild and (not guild.isdecimal() or int(guild) <= 0):
            raise ValueError("DISCORD_GUILD_ID deve essere un ID numerico o vuoto.")
        provider = os.getenv("TRANSLATION_PROVIDER", "libretranslate").strip().lower()
        if provider not in {"libretranslate", "deepl"}:
            raise ValueError("TRANSLATION_PROVIDER: scegli libretranslate oppure deepl.")
        deepl_key = os.getenv("DEEPL_API_KEY", "").strip()
        if provider == "deepl" and not deepl_key:
            raise ValueError("Imposta DEEPL_API_KEY per usare DeepL.")
        suffix = os.getenv("WEBHOOK_SUFFIX", " (tradotto)")
        if len(suffix) > 40:
            raise ValueError("WEBHOOK_SUFFIX può contenere al massimo 40 caratteri.")
        level = os.getenv("LOG_LEVEL", "INFO").upper()
        if level not in {"INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("LOG_LEVEL: INFO, WARNING, ERROR o CRITICAL.")
        return cls(
            discord_token=token, guild_id=int(guild) if guild else None,
            provider=provider,
            libretranslate_url=api_url("LIBRETRANSLATE_URL", "http://127.0.0.1:5000"),
            libretranslate_api_key=os.getenv("LIBRETRANSLATE_API_KEY", "").strip(),
            deepl_api_key=deepl_key,
            deepl_api_url=api_url("DEEPL_API_URL", "https://api-free.deepl.com"),
            database_path=Path(os.getenv("DATABASE_PATH", "data/channels.sqlite3")),
            webhook_suffix=suffix,
            translation_concurrency=positive_int("TRANSLATION_CONCURRENCY", 4),
            cache_size=positive_int("CACHE_SIZE", 1024),
            cache_ttl=positive_int("CACHE_TTL_SECONDS", 600),
            message_workers=positive_int("MESSAGE_WORKERS", 4),
            max_queue_size=positive_int("MAX_QUEUE_SIZE", 200),
            webhook_concurrency=positive_int("WEBHOOK_CONCURRENCY", 8),
            log_level=level,
        )
