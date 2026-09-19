"""Integrazione offline: eventi e comandi Discord simulati, SQLite reale."""

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, MagicMock

import discord
from discord import app_commands

from translator_bot.bot import TranslatorBot, WEBHOOK_NAME
from translator_bot.config import Settings
from translator_bot.translation import TranslationError


class BotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.bot = TranslatorBot(Settings(
            discord_token="test-token", database_path=Path(self.temp.name) / "channels.sqlite3",
            message_workers=1,
        ))
        await self.bot.store.open()
        # Non si apre alcuna connessione a Discord o a provider esterni.
        self.bot._connection.user = SimpleNamespace(id=900)
        self.bot.translator = SimpleNamespace(
            translate=AsyncMock(side_effect=lambda text, source, target: f"{target}: {text}"),
            close=AsyncMock(),
        )
        self.channels = {}
        self.hooks = {}
        self.bot.get_channel = Mock(side_effect=self.channels.get)

    async def asyncTearDown(self):
        await self.bot.close()
        self.temp.cleanup()

    def hook(self, hook_id, **overrides):
        fields = dict(id=hook_id, name=WEBHOOK_NAME, type=discord.WebhookType.incoming,
                      token="test-webhook-token", user=SimpleNamespace(id=900), send=AsyncMock())
        fields.update(overrides)
        return SimpleNamespace(**fields)

    async def channel(self, channel_id, language="it", guild_id=1):
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = channel_id
        channel.guild = SimpleNamespace(id=guild_id, me=SimpleNamespace(id=900))
        channel.permissions_for.return_value = discord.Permissions(
            view_channel=True, send_messages=True, manage_webhooks=True,
        )
        hook = self.hook(channel_id + 1000)
        channel.webhooks = AsyncMock(return_value=[hook])
        channel.create_webhook = AsyncMock(return_value=hook)
        self.channels[channel_id] = channel
        self.hooks[channel_id] = hook
        if language is not None:
            await self.bot.store.set(guild_id, channel_id, language, hook.id)
        return channel

    def message(self, channel_id=10, text="Ciao a tutti!"):
        message = MagicMock(spec=discord.Message)
        message.id = 555
        message.channel = self.channels[channel_id]
        message.guild = message.channel.guild
        message.author = SimpleNamespace(bot=False, display_name="Sara",
                                         display_avatar=SimpleNamespace(url="https://example.org/avatar.png"))
        message.webhook_id = None
        message.is_system.return_value = False
        message.clean_content = text
        message.attachments = []
        return message

    def interaction(self, channel_id):
        channel = self.channels[channel_id]
        return SimpleNamespace(
            channel=channel, channel_id=channel_id, guild=channel.guild,
            permissions=discord.Permissions(manage_channels=True),
            response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock(), is_done=Mock(return_value=False)),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    async def job(self, message=None):
        await self.bot.on_message(message or self.message())
        queue = self.bot.queues[0]
        job = queue.get_nowait()
        queue.task_done()
        return job

    async def test_slash_schema_and_actual_manage_channels_checks(self):
        await self.channel(10)
        interaction = self.interaction(10)
        commands = {command.name: command for command in self.bot.tree.get_commands()}
        self.assertEqual(set(commands), {"translate", "remove"})
        schema = commands["translate"].to_dict(self.bot.tree)
        option = schema["options"][0]
        self.assertEqual(option["name"], "lingua")
        self.assertTrue(option["required"])
        self.assertEqual({choice["value"] for choice in option["choices"]}, {"it", "fr", "en"})
        for command in commands.values():
            with self.subTest(command=command.name):
                self.assertTrue(command.guild_only)
                self.assertTrue(command.default_permissions.manage_channels)
                interaction.permissions = discord.Permissions.none()
                with self.assertRaises(app_commands.MissingPermissions):
                    await command._check_can_run(interaction)
                interaction.permissions = discord.Permissions(manage_channels=True)
                self.assertTrue(await command._check_can_run(interaction))

    async def test_translate_command_persists_configuration_and_reuses_hook(self):
        channel = await self.channel(10, language=None)
        interaction = self.interaction(10)
        command = self.bot.tree.get_command("translate")
        await command.callback(interaction, app_commands.Choice(name="Français", value="fr"))
        config = self.bot.store.get(10)
        self.assertEqual((config.guild_id, config.language, config.webhook_id), (1, "fr", 1010))
        channel.create_webhook.assert_not_awaited()
        interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        self.assertTrue(interaction.followup.send.await_args.kwargs["ephemeral"])

    async def test_translate_without_bot_webhook_permissions_does_not_activate(self):
        channel = await self.channel(10, language=None)
        channel.permissions_for.return_value = discord.Permissions(view_channel=True, send_messages=True)
        interaction = self.interaction(10)
        await self.bot.tree.get_command("translate").callback(
            interaction, app_commands.Choice(name="Français", value="fr"))
        self.assertIsNone(self.bot.store.get(10))
        channel.webhooks.assert_not_awaited()
        channel.create_webhook.assert_not_awaited()
        interaction.followup.send.assert_awaited_once()

    async def test_message_guards_prevent_loops_and_unsupported_sources(self):
        await self.channel(10)
        await self.channel(20, "fr")
        await self.channel(30, language=None)
        for reason in ("bot", "webhook", "dm", "system", "thread", "unconfigured", "wrong_guild", "blank"):
            with self.subTest(reason=reason):
                message = self.message()
                if reason == "bot":
                    message.author.bot = True
                elif reason == "webhook":
                    message.webhook_id = 123
                elif reason == "dm":
                    message.guild = None
                elif reason == "system":
                    message.is_system.return_value = True
                elif reason == "thread":
                    message.channel = MagicMock(spec=discord.Thread)
                elif reason == "unconfigured":
                    message.channel = self.channels[30]
                elif reason == "wrong_guild":
                    message.guild = SimpleNamespace(id=2)
                else:
                    message.clean_content = " \n "
                await self.bot.on_message(message)
                self.assertTrue(self.bot.queues[0].empty())
        self.bot.translator.translate.assert_not_awaited()

    async def test_routes_same_guild_once_per_language_and_preserves_original(self):
        for channel_id, language, guild_id in ((10, "it", 1), (20, "fr", 1), (21, "fr", 1),
                                               (30, "en", 1), (40, "it", 1), (50, "fr", 2)):
            await self.channel(channel_id, language, guild_id)
        message = self.message()
        await self.bot._relay(await self.job(message))
        calls = self.bot.translator.translate.await_args_list
        self.assertCountEqual([call.args for call in calls], [
            ("Ciao a tutti!", "it", "fr"), ("Ciao a tutti!", "it", "en"), ("Ciao a tutti!", "it", "it")])
        for channel_id in (20, 21, 30, 40):
            self.hooks[channel_id].send.assert_awaited_once()
        self.hooks[10].send.assert_not_awaited()
        self.hooks[50].send.assert_not_awaited()
        message.delete.assert_not_awaited()
        message.edit.assert_not_awaited()

    async def test_identity_mentions_and_spoiler_attachments_are_relayed(self):
        await self.channel(10)
        await self.channel(20, "fr")
        message = self.message(text="Ciao @Everyone")
        message.attachments = [SimpleNamespace(url="https://example.org/photo.png", is_spoiler=lambda: True)]
        await self.bot._relay(await self.job(message))
        call = self.hooks[20].send.await_args
        self.assertEqual(call.args[0], "fr: Ciao @Everyone\n||https://example.org/photo.png||")
        self.assertEqual(call.kwargs["username"], "Sara (tradotto)")
        self.assertEqual(call.kwargs["avatar_url"], "https://example.org/avatar.png")
        self.assertTrue(call.kwargs["wait"])
        self.assertEqual(call.kwargs["allowed_mentions"].to_dict(), {"parse": []})

    async def test_attachment_only_message_is_not_discarded(self):
        await self.channel(10)
        await self.channel(20, "fr")
        message = self.message(text="")
        message.attachments = [SimpleNamespace(url="https://example.org/photo.png", is_spoiler=lambda: False)]
        self.bot.translator.translate.return_value = ""
        self.bot.translator.translate.side_effect = None
        await self.bot._relay(await self.job(message))
        self.assertEqual(self.hooks[20].send.await_args.args[0], "https://example.org/photo.png")

    async def test_translation_failure_is_isolated_by_language(self):
        await self.channel(10)
        await self.channel(20, "fr")
        await self.channel(30, "en")

        async def translate(text, source, target):
            if target == "fr":
                raise TranslationError("Provider unavailable")
            return "Hello everyone!"

        self.bot.translator.translate.side_effect = translate
        with self.assertLogs("translator_bot.bot", level="WARNING"):
            await self.bot._relay(await self.job())
        self.hooks[20].send.assert_not_awaited()
        self.hooks[30].send.assert_awaited_once()

    async def test_destination_permission_failure_does_not_block_others(self):
        await self.channel(10)
        await self.channel(20, "fr")
        await self.channel(30, "en")
        response = SimpleNamespace(status=403, reason="Forbidden")
        self.hooks[20].send.side_effect = discord.Forbidden(response, {"code": 50013, "message": "Missing Permissions"})
        with self.assertLogs("translator_bot.bot", level="WARNING"):
            await self.bot._relay(await self.job())
        self.hooks[30].send.assert_awaited_once()

    async def test_remove_disables_channel_and_clears_cached_webhook(self):
        await self.channel(10)
        await self.channel(20, "fr")
        self.bot.webhooks[10] = self.hooks[10]
        interaction = self.interaction(10)
        await self.bot.tree.get_command("remove").callback(interaction)
        self.assertIsNone(self.bot.store.get(10))
        self.assertNotIn(10, self.bot.webhooks)
        await self.bot.on_message(self.message())
        self.assertTrue(self.bot.queues[0].empty())
        interaction.followup.send.assert_awaited_once()

    async def test_removed_or_readded_source_cancels_pending_translation(self):
        # Entrambe le revisioni devono essere invalidate, anche rimettendo la stessa lingua.
        for readd in (False, True):
            with self.subTest(readd=readd):
                await self.channel(10)
                await self.channel(20, "fr")
                started, resume = asyncio.Event(), asyncio.Event()

                async def translate(*args):
                    started.set()
                    await resume.wait()
                    return "Bonjour"

                self.bot.translator.translate.side_effect = translate
                task = asyncio.create_task(self.bot._relay(await self.job()))
                await asyncio.wait_for(started.wait(), 2)
                try:
                    # /remove resta eseguibile mentre la rete di traduzione è sospesa.
                    await asyncio.wait_for(self.bot.tree.get_command("remove").callback(self.interaction(10)), 2)
                    if readd:
                        await self.bot.store.set(1, 10, "it", 1010)
                finally:
                    resume.set()
                    await asyncio.wait_for(task, 2)
                self.hooks[20].send.assert_not_awaited()

    async def test_removed_or_readded_destination_is_skipped_while_others_receive(self):
        for readd in (False, True):
            with self.subTest(readd=readd):
                await self.channel(10)
                await self.channel(20, "fr")
                await self.channel(30, "en")
                started, resume = asyncio.Event(), asyncio.Event()

                async def translate(text, source, target):
                    started.set()
                    await resume.wait()
                    return target

                self.bot.translator.translate.side_effect = translate
                task = asyncio.create_task(self.bot._relay(await self.job()))
                await asyncio.wait_for(started.wait(), 2)
                try:
                    await asyncio.wait_for(self.bot.tree.get_command("remove").callback(self.interaction(20)), 2)
                    if readd:
                        await self.bot.store.set(1, 20, "fr", 1020)
                finally:
                    resume.set()
                    await asyncio.wait_for(task, 2)
                self.hooks[20].send.assert_not_awaited()
                self.hooks[30].send.assert_awaited_once()
                self.bot.webhooks.clear()

    async def test_existing_owned_webhook_is_reused_and_cached(self):
        channel = await self.channel(10)
        foreign = self.hook(1234, user=SimpleNamespace(id=999))
        tokenless = self.hook(1235, token=None)
        channel.webhooks.return_value = [foreign, tokenless, self.hooks[10]]
        self.assertIs(await self.bot._get_webhook(channel), self.hooks[10])
        self.assertIs(await self.bot._get_webhook(channel), self.hooks[10])
        channel.webhooks.assert_awaited_once()
        channel.create_webhook.assert_not_awaited()

    async def test_missing_owned_webhook_is_created_and_persisted(self):
        channel = await self.channel(10)
        before = self.bot.store.get(10)
        channel.webhooks.return_value = [self.hook(1234, user=SimpleNamespace(id=999))]
        replacement = self.hook(9999)
        channel.create_webhook.return_value = replacement
        self.assertIs(await self.bot._get_webhook(channel), replacement)
        channel.create_webhook.assert_awaited_once()
        self.assertEqual(self.bot.store.get(10).webhook_id, 9999)
        self.assertEqual(self.bot.store.get(10).revision, before.revision)

    async def test_deleted_webhook_is_recreated_and_failed_chunk_retried_once(self):
        await self.channel(10)
        channel = await self.channel(20, "fr")
        response = SimpleNamespace(status=404, reason="Not Found")
        self.hooks[20].send.side_effect = discord.NotFound(response, {"code": 10015, "message": "Unknown Webhook"})
        self.bot.webhooks[20] = self.hooks[20]
        channel.webhooks.return_value = []
        replacement = self.hook(9999)
        channel.create_webhook.return_value = replacement
        await self.bot._relay(await self.job())
        self.hooks[20].send.assert_awaited_once()
        replacement.send.assert_awaited_once()
        self.assertEqual(replacement.send.await_args.args, self.hooks[20].send.await_args.args)
        self.assertEqual(self.bot.store.get(20).webhook_id, 9999)

    async def test_long_translation_splits_without_losing_text_or_identity(self):
        await self.channel(10)
        await self.channel(20, "fr")
        translated = "Bonjour 😀 " * 650
        self.bot.translator.translate.side_effect = None
        self.bot.translator.translate.return_value = translated
        await self.bot._relay(await self.job())
        calls = self.hooks[20].send.await_args_list
        self.assertGreater(len(calls), 1)
        self.assertEqual("".join(call.args[0] for call in calls), translated)
        for call in calls:
            self.assertLessEqual(len(call.args[0].encode("utf-16-le")) // 2, 2000)
            self.assertEqual(call.kwargs["username"], "Sara (tradotto)")

    async def test_worker_survives_a_failed_job_and_preserves_queue_order(self):
        await self.channel(10)
        await self.channel(20, "fr")
        first = await self.job()
        second_message = self.message(text="Secondo messaggio")
        second_message.id = 556
        second = await self.job(second_message)
        self.bot.wait_until_ready = AsyncMock()
        self.bot._relay = AsyncMock(side_effect=[RuntimeError("test failure"), None])
        queue = self.bot.queues[0]
        queue.put_nowait(first)
        queue.put_nowait(second)
        with self.assertLogs("translator_bot.bot", level="ERROR"):
            worker = asyncio.create_task(self.bot._worker(queue))
            try:
                await asyncio.wait_for(queue.join(), 2)
            finally:
                worker.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await worker
        self.assertEqual([call.args[0].message_id for call in self.bot._relay.await_args_list], [555, 556])


if __name__ == "__main__":
    unittest.main()
