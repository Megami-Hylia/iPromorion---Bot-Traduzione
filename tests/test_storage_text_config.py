"""Verifiche offline di persistenza, isolamento, configurazione e limiti Discord."""

from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from translator_bot.config import Settings
from translator_bot.storage import ChannelStore
from translator_bot.text import split_message, webhook_username


class StorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ChannelStore(Path(self.temp.name) / "nested" / "channels.sqlite3")
        await self.store.open()

    async def asyncTearDown(self):
        await self.store.close()
        self.temp.cleanup()

    async def test_restart_preserves_config_and_webhook_without_tokens(self):
        expected = await self.store.set(1, 10, "it", 100)
        json_path = self.store.json_path
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["version"], 1)
        self.assertEqual(payload["channels"][0]["channel_id"], 10)
        await self.store.close()
        await self.store.open()
        self.assertEqual(self.store.get(10), expected)
        async with self.store.db.execute("PRAGMA table_info(channels)") as cursor:
            fields = {row[1] for row in await cursor.fetchall()}
        self.assertNotIn("webhook_token", fields)
        self.assertNotIn("webhook_url", fields)

    async def test_empty_database_recovers_channels_from_json(self):
        expected = await self.store.set(1, 10, "it", 100)
        await self.store.close()
        self.store.path.unlink()
        recovered = ChannelStore(self.store.path)
        await recovered.open()
        self.assertEqual(recovered.get(10), expected)
        await recovered.close()

    async def test_remove_updates_json_snapshot(self):
        await self.store.set(1, 10, "it", 100)
        self.assertTrue(await self.store.remove(10))
        payload = json.loads(self.store.json_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["channels"], [])

    async def test_routes_exclude_source_and_other_servers(self):
        source = await self.store.set(1, 10, "it", 100)
        french = await self.store.set(1, 20, "fr", 200)
        same_language = await self.store.set(1, 30, "it", 300)
        await self.store.set(2, 40, "en", 400)
        self.assertEqual(set(self.store.targets(source)), {french, same_language})

    async def test_remove_and_reactivate_invalidates_pending_jobs(self):
        old = await self.store.set(1, 10, "it", 100)
        self.assertTrue(await self.store.remove(10))
        self.assertFalse(await self.store.remove(10))
        self.assertFalse(self.store.is_current(old))
        new = await self.store.set(1, 10, "it", 100)
        self.assertFalse(self.store.is_current(old))
        self.assertTrue(self.store.is_current(new))
        await self.store.close()
        await self.store.open()
        self.assertEqual(self.store.get(10), new)

    async def test_remove_is_persistent(self):
        await self.store.set(1, 10, "it", 100)
        await self.store.remove(10)
        await self.store.close()
        await self.store.open()
        self.assertIsNone(self.store.get(10))

    async def test_webhook_replacement_does_not_invalidate_message(self):
        original = await self.store.set(1, 10, "it", 100)
        await self.store.set_webhook(10, 101)
        self.assertTrue(self.store.is_current(original))
        self.assertEqual(self.store.get(10).webhook_id, 101)


class TextTests(unittest.TestCase):
    def test_long_translation_preserves_text_and_emoji(self):
        for content in ["ciao " * 1400, "🙂" * 2100, "a" * 2001,
                        ("x\n" * 1999) + "test", "", "a" * 1999 + "🙂"]:
            with self.subTest(length=len(content)):
                chunks = split_message(content)
                self.assertEqual("".join(chunks), content)
                self.assertTrue(all(len(c.encode("utf-16-le")) // 2 <= 2000 for c in chunks))

    def test_name_and_suffix(self):
        self.assertEqual(webhook_username("Mario", " (tradotto)"), "Mario (tradotto)")
        self.assertEqual(webhook_username("Mario", ""), "Mario")
        self.assertLessEqual(len(webhook_username("x" * 100, " (tradotto)")), 80)
        for name in ["", "\n", "Discord User", "CLYDE", "discordclyde"]:
            result = webhook_username(name, " (tradotto)")
            self.assertTrue(result)
            self.assertNotIn("discord", result.lower())
            self.assertNotIn("clyde", result.lower())


class SettingsTests(unittest.TestCase):
    def read(self, env):
        with patch.dict("os.environ", env, clear=True), patch("translator_bot.config.load_dotenv"):
            return Settings.from_env()

    def test_defaults_and_no_secrets_in_repr(self):
        settings = self.read({"DISCORD_TOKEN": "secret-for-test", "DEEPL_API_KEY": "key-for-test"})
        self.assertEqual(settings.provider, "libretranslate")
        self.assertNotIn("secret-for-test", repr(settings))
        self.assertNotIn("key-for-test", repr(settings))

    def test_invalid_settings_report_variable(self):
        for key, value in [("MESSAGE_WORKERS", "0"), ("MAX_QUEUE_SIZE", "-1"),
                           ("TRANSLATION_PROVIDER", "invalid"), ("DISCORD_GUILD_ID", "abc"),
                           ("CACHE_TTL_SECONDS", "x"), ("LOG_LEVEL", "DEBUG"),
                           ("WEBHOOK_SUFFIX", "x" * 41),
                           ("LIBRETRANSLATE_URL", "http://remote.example"),
                           ("LIBRETRANSLATE_URL", "https://example.org?key=secret")]:
            with self.subTest(key=key, value=value):
                with self.assertRaisesRegex(ValueError, key):
                    self.read({"DISCORD_TOKEN": "testing", key: value})

    def test_token_and_deepl_key_required(self):
        with self.assertRaisesRegex(ValueError, "DISCORD_TOKEN"):
            self.read({})
        with self.assertRaisesRegex(ValueError, "DEEPL_API_KEY"):
            self.read({"DISCORD_TOKEN": "test", "TRANSLATION_PROVIDER": "deepl"})


if __name__ == "__main__":
    unittest.main()
