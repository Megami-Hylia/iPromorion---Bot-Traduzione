"""Comandi slash, instradamento tra canali e invio tramite webhook."""

import asyncio
from dataclasses import dataclass
import logging

import aiohttp
import discord
from discord import app_commands

from .config import LANGUAGES, Settings
from .storage import ChannelConfig, ChannelStore
from .text import split_message, webhook_username
from .translation import TranslationError, TranslationService

log = logging.getLogger(__name__)
WEBHOOK_NAME = "Language Bridge"


@dataclass(frozen=True)
class RelayJob:
    message_id: int
    source: ChannelConfig
    targets: tuple[ChannelConfig, ...]
    text: str
    attachments: tuple[str, ...]
    username: str
    avatar_url: str


class TranslatorBot(discord.Client):
    def __init__(self, settings: Settings):
        # Non servono gli intent privilegiati Members o Presence.
        intents = discord.Intents.none()
        intents.guilds = True
        intents.guild_messages = True
        intents.message_content = True
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none(),
                         max_messages=None)
        self.settings = settings
        self.tree = app_commands.CommandTree(self)
        self.store = ChannelStore(settings.database_path)
        self.session: aiohttp.ClientSession | None = None
        self.translator: TranslationService | None = None
        self.webhooks: dict[int, discord.Webhook] = {}
        self.guild_locks: dict[int, asyncio.Lock] = {}
        # Code limitate: un server finisce sempre nello stesso worker e conserva l'ordine.
        self.queues: list[asyncio.Queue[RelayJob]] = [
            asyncio.Queue(maxsize=settings.max_queue_size)
            for _ in range(settings.message_workers)
        ]
        self.workers: list[asyncio.Task] = []
        self.webhook_slots = asyncio.Semaphore(settings.webhook_concurrency)
        self._register_commands()

    def guild_lock(self, guild_id: int) -> asyncio.Lock:
        return self.guild_locks.setdefault(guild_id, asyncio.Lock())

    def _register_commands(self) -> None:
        @self.tree.command(name="translate", description="Attiva la traduzione in questo canale")
        @app_commands.guild_only()
        @app_commands.default_permissions(manage_channels=True)
        @app_commands.checks.has_permissions(manage_channels=True)
        @app_commands.describe(lingua="Lingua dei messaggi di questo canale")
        @app_commands.choices(lingua=[
            app_commands.Choice(name=f"{name} ({code})", value=code)
            for code, name in LANGUAGES.items()
        ])
        async def translate(interaction: discord.Interaction, lingua: app_commands.Choice[str]):
            channel = interaction.channel
            if not isinstance(channel, discord.TextChannel) or interaction.guild is None:
                await interaction.response.send_message(
                    "Usa il comando in un canale testuale del server, fuori dai thread.",
                    ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True, thinking=True)
            me = interaction.guild.me
            permissions = channel.permissions_for(me) if me else discord.Permissions.none()
            if not all((permissions.view_channel, permissions.send_messages,
                        permissions.manage_webhooks)):
                await interaction.followup.send(
                    "Mi servono Visualizza canale, Invia messaggi e Gestisci webhook qui.",
                    ephemeral=True)
                return
            async with self.guild_lock(interaction.guild.id):
                webhook = await self._get_webhook(channel)
                await self.store.set(interaction.guild.id, channel.id, lingua.value, webhook.id)
            await interaction.followup.send(
                f"Traduzione attiva: **{LANGUAGES[lingua.value]} ({lingua.value})**. "
                "I nuovi messaggi saranno condivisi con gli altri canali configurati "
                "di questo server. Gli originali restano qui.", ephemeral=True)

        @self.tree.command(name="remove", description="Disattiva la traduzione in questo canale")
        @app_commands.guild_only()
        @app_commands.default_permissions(manage_channels=True)
        @app_commands.checks.has_permissions(manage_channels=True)
        async def remove(interaction: discord.Interaction):
            channel = interaction.channel
            if not isinstance(channel, discord.TextChannel) or interaction.guild is None:
                await interaction.response.send_message(
                    "Usa il comando in un canale testuale del server, fuori dai thread.",
                    ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True, thinking=True)
            # Aspetta eventuali invii già partiti: dopo la conferma non ne iniziano altri.
            async with self.guild_lock(interaction.guild.id):
                removed = await self.store.remove(channel.id)
                self.webhooks.pop(channel.id, None)
            await interaction.followup.send(
                "Traduzione disattivata: il canale non invia né riceve più traduzioni."
                if removed else "Questo canale non aveva una traduzione attiva.",
                ephemeral=True)

        self.tree.error(self._command_error)

    async def _command_error(self, interaction: discord.Interaction,
                             error: app_commands.AppCommandError) -> None:
        original = getattr(error, "original", error)
        if isinstance(original, app_commands.MissingPermissions):
            text = "Per configurare la traduzione devi avere il permesso Gestisci canali."
        elif isinstance(original, discord.Forbidden):
            text = "Permessi Discord insufficienti: controlla Gestisci webhook e l'accesso al canale."
        else:
            text = "Configurazione non completata. Controlla i permessi e il log del bot."
        # Nessun corpo HTTP, contenuto dei messaggi o URL di webhook nei log.
        log.warning("Comando fallito: tipo=%s canale=%s", type(original).__name__,
                    interaction.channel_id)
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)

    async def setup_hook(self) -> None:
        await self.store.open()
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        self.translator = TranslationService(
            self.session, provider=self.settings.provider,
            libretranslate_url=self.settings.libretranslate_url,
            libretranslate_api_key=self.settings.libretranslate_api_key,
            deepl_api_key=self.settings.deepl_api_key,
            deepl_api_url=self.settings.deepl_api_url,
            concurrency=self.settings.translation_concurrency,
            cache_size=self.settings.cache_size, cache_ttl=self.settings.cache_ttl,
        )
        # Sincronizza una volta all'avvio, non ad ogni riconnessione al gateway.
        if self.settings.guild_id:
            guild = discord.Object(id=self.settings.guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()
        log.info("Comandi sincronizzati: ambito=%s", self.settings.guild_id or "globale")
        self.workers = [asyncio.create_task(self._worker(queue), name=f"relay-{index}")
                        for index, queue in enumerate(self.queues)]

    async def on_ready(self) -> None:
        log.info("Bot online: id=%s; provider=%s; canali configurati=%s",
                 self.user.id if self.user else "?", self.settings.provider,
                 len(self.store.channels))

    async def on_error(self, event_method: str, *args, **kwargs) -> None:
        # Il logger predefinito può includere eccezioni HTTP: qui omettiamo i dettagli.
        log.error("Evento fallito: %s. Verifica rete, permessi e database.", event_method)

    async def on_message(self, message: discord.Message) -> None:
        if (message.guild is None or message.author.bot or message.webhook_id is not None
                or not isinstance(message.channel, discord.TextChannel) or message.is_system()):
            return  # Anti-loop: ignora tutti i bot e tutti i webhook.
        source = self.store.get(message.channel.id)
        if source is None or source.guild_id != message.guild.id:
            return
        targets = self.store.targets(source)
        if not targets:
            return
        # clean_content rende leggibili le menzioni; nessun ping sarà inviato dal webhook.
        text = message.clean_content
        attachments = tuple(f"||{a.url}||" if a.is_spoiler() else a.url
                            for a in message.attachments)
        if not text.strip() and not attachments:
            return
        job = RelayJob(message.id, source, targets, text, attachments,
                       webhook_username(message.author.display_name, self.settings.webhook_suffix),
                       str(message.author.display_avatar.url))
        queue = self.queues[(message.guild.id >> 22) % len(self.queues)]
        try:
            queue.put_nowait(job)
        except asyncio.QueueFull:
            log.error("Coda piena: messaggio=%s server=%s non inoltrato; aumenta capacità o worker.",
                      message.id, message.guild.id)

    async def _worker(self, queue: asyncio.Queue[RelayJob]) -> None:
        await self.wait_until_ready()
        while True:
            job = await queue.get()
            try:
                await self._relay(job)
            except Exception as error:
                log.error("Inoltro fallito: messaggio=%s tipo=%s", job.message_id,
                          type(error).__name__)
            finally:
                queue.task_done()

    async def _relay(self, job: RelayJob) -> None:
        if not self.store.is_current(job.source):
            return
        # Difesa esplicita: un job già accodato non può mai tornare nel sorgente.
        targets = tuple(t for t in job.targets
                        if t.channel_id != job.source.channel_id and self.store.is_current(t))
        languages = sorted({t.language for t in targets})
        if not languages:
            return
        assert self.translator is not None
        # Una sola traduzione per lingua, anche se ci sono molti canali equivalenti.
        results = await asyncio.gather(*(
            self.translator.translate(job.text, job.source.language, language)
            for language in languages
        ), return_exceptions=True)
        translations = {}
        for language, result in zip(languages, results):
            if isinstance(result, BaseException):
                detail = str(result) if isinstance(result, TranslationError) else type(result).__name__
                log.warning("Traduzione fallita: messaggio=%s lingua=%s errore=%s",
                            job.message_id, language, detail)
            else:
                translations[language] = result

        # Durante la rete di traduzione /remove resta reattivo. Ricontrolla prima di inviare.
        async with self.guild_lock(job.source.guild_id):
            if not self.store.is_current(job.source):
                return
            await asyncio.gather(*(
                self._deliver(job, target, translations[target.language])
                for target in targets
                if target.channel_id != job.source.channel_id
                and self.store.is_current(target) and target.language in translations
            ))

    async def _get_webhook(self, channel: discord.TextChannel,
                           refresh: bool = False) -> discord.Webhook:
        # Il chiamante tiene il lock del server: niente creazioni duplicate.
        if not refresh and channel.id in self.webhooks:
            return self.webhooks[channel.id]
        assert self.user is not None
        config = self.store.get(channel.id)
        own_hooks = [hook for hook in await channel.webhooks()
                     if hook.type == discord.WebhookType.incoming and hook.token
                     and hook.user is not None and hook.user.id == self.user.id]
        hook = next((h for h in own_hooks if config and h.id == config.webhook_id), None)
        if hook is None:
            hook = next((h for h in own_hooks if h.name == WEBHOOK_NAME), None)
        if hook is None:
            hook = await channel.create_webhook(name=WEBHOOK_NAME,
                                                reason="Traduzione automatica dei canali")
        self.webhooks[channel.id] = hook
        if config and config.webhook_id != hook.id:
            await self.store.set_webhook(channel.id, hook.id)
        return hook

    async def _deliver(self, job: RelayJob, target: ChannelConfig, translated: str) -> None:
        # Non rimuovere questo controllo: impedisce qualsiasi echo nel canale d'origine.
        if target.channel_id == job.source.channel_id:
            return
        channel = self.get_channel(target.channel_id)
        if not isinstance(channel, discord.TextChannel) or channel.guild.id != job.source.guild_id:
            log.warning("Canale destinazione non disponibile: %s", target.channel_id)
            return
        content = "\n".join(part for part in (translated, *job.attachments) if part)
        try:
            async with self.webhook_slots:
                hook = await self._get_webhook(channel)
                for chunk in split_message(content):
                    if not chunk.strip():
                        continue
                    try:
                        await hook.send(chunk, username=job.username, avatar_url=job.avatar_url,
                                        allowed_mentions=discord.AllowedMentions.none(), wait=True)
                    except discord.NotFound as error:
                        if error.code != 10015:  # Unknown Webhook: cancellato manualmente.
                            raise
                        self.webhooks.pop(channel.id, None)
                        hook = await self._get_webhook(channel, refresh=True)
                        await hook.send(chunk, username=job.username, avatar_url=job.avatar_url,
                                        allowed_mentions=discord.AllowedMentions.none(), wait=True)
        except Exception as error:
            # Un canale senza permessi non deve bloccare gli altri destinatari.
            log.warning("Invio fallito: messaggio=%s canale=%s tipo=%s", job.message_id,
                        target.channel_id, type(error).__name__)

    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        async with self.guild_lock(channel.guild.id):
            await self.store.remove(channel.id)
            self.webhooks.pop(channel.id, None)

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        async with self.guild_lock(guild.id):
            for config in tuple(self.store.channels.values()):
                if config.guild_id == guild.id:
                    await self.store.remove(config.channel_id)
                    self.webhooks.pop(config.channel_id, None)

    async def close(self) -> None:
        for worker in self.workers:
            worker.cancel()
        if self.workers:
            await asyncio.gather(*self.workers, return_exceptions=True)
        if self.translator is not None:
            await self.translator.close()
        if self.session is not None:
            await self.session.close()
        await self.store.close()
        await super().close()
