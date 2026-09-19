"""Persistenza dei canali in SQLite e snapshot JSON leggibile."""

from dataclasses import dataclass, replace
import json
from pathlib import Path
from uuid import uuid4

import aiosqlite

from .config import LANGUAGES


@dataclass(frozen=True)
class ChannelConfig:
    guild_id: int
    channel_id: int
    language: str
    webhook_id: int
    revision: str


class ChannelStore:
    def __init__(self, path: Path):
        self.path = path
        self.json_path = path.with_suffix(".json")
        self.db: aiosqlite.Connection | None = None
        self.channels: dict[int, ChannelConfig] = {}

    async def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = await aiosqlite.connect(self.path)
        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.execute("PRAGMA busy_timeout=5000")
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS channels (
                channel_id INTEGER PRIMARY KEY,
                guild_id INTEGER NOT NULL,
                language TEXT NOT NULL CHECK(language IN ('it','fr','en')),
                webhook_id INTEGER NOT NULL,
                revision TEXT NOT NULL
            )
        """)
        await self.db.commit()
        async with self.db.execute(
            "SELECT guild_id, channel_id, language, webhook_id, revision FROM channels"
        ) as cursor:
            self.channels = {row[1]: ChannelConfig(*row) for row in await cursor.fetchall()}

        # SQLite resta la persistenza principale; il JSON rende la configurazione
        # visibile e consente il recupero anche se il database viene ricreato.
        if not self.channels:
            configs = self._read_json()
            if configs:
                await self.db.executemany(
                    """
                    INSERT INTO channels(channel_id, guild_id, language, webhook_id, revision)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    ((c.channel_id, c.guild_id, c.language, c.webhook_id, c.revision)
                     for c in configs),
                )
                await self.db.commit()
                self.channels = {config.channel_id: config for config in configs}
        self._write_json()

    def _read_json(self) -> tuple[ChannelConfig, ...]:
        if not self.json_path.exists():
            return ()
        try:
            payload = json.loads(self.json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"Configurazione JSON non leggibile: {self.json_path}") from error

        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise ValueError(f"Versione JSON non supportata: {self.json_path}")
        rows = payload.get("channels")
        if not isinstance(rows, list):
            raise ValueError(f"Elenco channels non valido: {self.json_path}")

        configs: list[ChannelConfig] = []
        seen: set[int] = set()
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError(f"Voce canale non valida: {self.json_path}")
            try:
                guild_id = row["guild_id"]
                channel_id = row["channel_id"]
                language = row["language"]
                webhook_id = row["webhook_id"]
                revision = row["revision"]
            except KeyError as error:
                raise ValueError(f"Campo mancante nel JSON: {error.args[0]}") from None
            if (type(guild_id) is not int or guild_id < 1
                    or type(channel_id) is not int or channel_id < 1
                    or type(webhook_id) is not int or webhook_id < 1
                    or language not in LANGUAGES
                    or not isinstance(revision, str) or not revision
                    or channel_id in seen):
                raise ValueError(f"Valori canale non validi: {self.json_path}")
            seen.add(channel_id)
            configs.append(ChannelConfig(guild_id, channel_id, language,
                                         webhook_id, revision))
        return tuple(configs)

    def _write_json(self) -> None:
        payload = {
            "version": 1,
            "channels": [
                {
                    "guild_id": config.guild_id,
                    "channel_id": config.channel_id,
                    "language": config.language,
                    "webhook_id": config.webhook_id,
                    "revision": config.revision,
                }
                for config in sorted(self.channels.values(), key=lambda item: item.channel_id)
            ],
        }
        temporary = self.json_path.with_name(self.json_path.name + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
        temporary.replace(self.json_path)

    def get(self, channel_id: int) -> ChannelConfig | None:
        return self.channels.get(channel_id)

    def targets(self, source: ChannelConfig) -> tuple[ChannelConfig, ...]:
        # Mai inoltrare messaggi a un altro server o al canale di origine.
        return tuple(c for c in self.channels.values()
                     if c.guild_id == source.guild_id and c.channel_id != source.channel_id)

    def is_current(self, config: ChannelConfig) -> bool:
        current = self.get(config.channel_id)
        return current is not None and current.revision == config.revision

    async def set(self, guild_id: int, channel_id: int, language: str,
                  webhook_id: int) -> ChannelConfig:
        if language not in LANGUAGES:
            raise ValueError("Lingua non supportata.")
        assert self.db is not None
        # La revisione invalida anche messaggi accodati prima di remove + translate.
        config = ChannelConfig(guild_id, channel_id, language, webhook_id, uuid4().hex)
        await self.db.execute("""
            INSERT INTO channels(channel_id, guild_id, language, webhook_id, revision)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(channel_id) DO UPDATE SET
                guild_id=excluded.guild_id, language=excluded.language,
                webhook_id=excluded.webhook_id, revision=excluded.revision
        """, (channel_id, guild_id, language, webhook_id, config.revision))
        await self.db.commit()
        self.channels[channel_id] = config
        self._write_json()
        return config

    async def set_webhook(self, channel_id: int, webhook_id: int) -> None:
        assert self.db is not None
        await self.db.execute("UPDATE channels SET webhook_id=? WHERE channel_id=?",
                              (webhook_id, channel_id))
        await self.db.commit()
        if channel_id in self.channels:
            self.channels[channel_id] = replace(self.channels[channel_id], webhook_id=webhook_id)
            self._write_json()

    async def remove(self, channel_id: int) -> bool:
        assert self.db is not None
        await self.db.execute("DELETE FROM channels WHERE channel_id=?", (channel_id,))
        await self.db.commit()
        removed = self.channels.pop(channel_id, None) is not None
        if removed:
            self._write_json()
        return removed

    async def close(self) -> None:
        if self.db is not None:
            await self.db.close()
            self.db = None
