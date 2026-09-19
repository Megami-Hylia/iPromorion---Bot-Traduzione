"""Avvio: python -m translator_bot (dalla cartella del progetto)."""

import argparse
import asyncio
import logging

import discord

from .bot import TranslatorBot
from .config import Settings


async def run(settings: Settings) -> None:
    async with TranslatorBot(settings) as bot:
        await bot.start(settings.discord_token)


def main() -> None:
    parser = argparse.ArgumentParser(description="Bot Discord di traduzione tra canali")
    parser.add_argument("--check-config", action="store_true",
                        help="Valida .env senza collegarsi a Discord o al traduttore")
    args = parser.parse_args()
    try:
        settings = Settings.from_env()
    except ValueError as error:
        parser.exit(2, f"Configurazione non valida: {error}\n")
    if args.check_config:
        print(f"Configurazione valida; provider: {settings.provider}. "
              "Credenziali e collegamenti non verificati.")
        return
    logging.basicConfig(level=settings.log_level,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        asyncio.run(run(settings))
    except KeyboardInterrupt:
        pass
    except discord.LoginFailure:
        parser.exit(1, "Login fallito: verifica DISCORD_TOKEN nel file .env.\n")
    except discord.PrivilegedIntentsRequired:
        parser.exit(1, "Abilita Message Content Intent nella pagina Bot del Developer Portal.\n")
    except Exception as error:
        # Evita di stampare eccezioni che possano contenere token o testi dei messaggi.
        parser.exit(1, f"Avvio interrotto ({type(error).__name__}). "
                       "Controlla rete, permessi del bot e percorso SQLite.\n")


if __name__ == "__main__":
    main()
