"""Client asincrono per LibreTranslate e DeepL, senza utilizzare LLM."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math
import random
import time
from typing import Any
from urllib.parse import urlsplit

import aiohttp


LANGUAGES = frozenset({"it", "fr", "en"})
CacheKey = tuple[str, str, str]


class TranslationError(Exception):
    """Errore pubblicabile nei log: non contiene testo, chiavi o body dell'API."""


class TranslationService:
    """Condivide richieste identiche e riutilizza risultati in una cache TTL/LRU.

    La sessione HTTP viene fornita dal bot e resta di sua proprietà. Ogni istanza
    deve essere utilizzata da un unico event loop asyncio.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        provider: str = "libretranslate",
        libretranslate_url: str = "http://127.0.0.1:5000",
        libretranslate_api_key: str = "",
        deepl_api_key: str = "",
        deepl_api_url: str = "https://api-free.deepl.com",
        concurrency: int = 4,
        cache_size: int = 1024,
        cache_ttl: float = 600,
    ) -> None:
        if provider not in {"libretranslate", "deepl"}:
            raise ValueError("Provider non supportato: usare libretranslate o deepl.")
        if concurrency < 1 or cache_size < 0 or cache_ttl < 0:
            raise ValueError("Concorrenza >= 1, dimensione e TTL della cache >= 0.")
        if not math.isfinite(cache_ttl):
            raise ValueError("Il TTL della cache deve essere finito.")
        if provider == "deepl" and not deepl_api_key:
            raise ValueError("DEEPL_API_KEY è obbligatoria con il provider deepl.")

        base_url = libretranslate_url if provider == "libretranslate" else deepl_api_url
        try:
            parsed = urlsplit(base_url)
            valid_url = (
                parsed.scheme in {"http", "https"}
                and parsed.hostname
                and not parsed.username
                and not parsed.password
                and not parsed.query
                and not parsed.fragment
            )
        except ValueError:
            valid_url = False
        if not valid_url:
            raise ValueError("L'endpoint deve essere un URL HTTP(S) senza credenziali o query.")

        self._session = session
        self._provider = provider
        self._url = base_url.rstrip("/") + (
            "/translate" if provider == "libretranslate" else "/v2/translate"
        )
        self._libretranslate_api_key = libretranslate_api_key
        self._deepl_api_key = deepl_api_key
        self._semaphore = asyncio.Semaphore(concurrency)
        self._cache_size = cache_size
        self._cache_ttl = cache_ttl
        self._cache: OrderedDict[CacheKey, tuple[float, str]] = OrderedDict()
        self._inflight: dict[CacheKey, asyncio.Task[str]] = {}
        self._closed = False

    async def translate(self, text: str, source: str, target: str) -> str:
        """Traduce una sola volta lo stesso testo per la stessa coppia di lingue."""
        if source not in LANGUAGES or target not in LANGUAGES:
            raise TranslationError("Lingua non supportata: usare it, fr oppure en.")
        if self._closed:
            raise TranslationError("Il servizio di traduzione è stato chiuso.")
        if not text.strip() or source == target:
            return text

        key = (text, source, target)
        cached = self._cache.get(key)
        if cached is not None:
            expires_at, result = cached
            if expires_at > time.monotonic():
                self._cache.move_to_end(key)
                return result
            del self._cache[key]

        # Non ci sono await tra lettura e inserimento: non occorre un lock.
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.create_task(self._translate_and_cache(key))
            self._inflight[key] = task
            task.add_done_callback(lambda done: self._forget_request(key, done))

        # La cancellazione di un destinatario non annulla gli altri destinatari.
        return await asyncio.shield(task)

    def _forget_request(self, key: CacheKey, task: asyncio.Task[str]) -> None:
        if self._inflight.get(key) is task:
            del self._inflight[key]
        # Recupera l'eventuale eccezione anche se tutti i chiamanti sono cancellati.
        if not task.cancelled():
            task.exception()

    async def close(self) -> None:
        """Annulla le richieste pendenti prima di chiudere la sessione HTTP."""
        self._closed = True
        tasks = list(self._inflight.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._inflight.clear()
        self._cache.clear()

    async def _translate_and_cache(self, key: CacheKey) -> str:
        text, source, target = key
        result = await self._request(text, source, target)
        if self._cache_size and self._cache_ttl:
            self._cache[key] = (time.monotonic() + self._cache_ttl, result)
            self._cache.move_to_end(key)
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)
        return result

    async def _request(self, text: str, source: str, target: str) -> str:
        headers: dict[str, str] = {}
        if self._provider == "libretranslate":
            payload: dict[str, Any] = {
                "q": text, "source": source, "target": target, "format": "text"
            }
            if self._libretranslate_api_key:
                payload["api_key"] = self._libretranslate_api_key
        else:
            headers["Authorization"] = f"DeepL-Auth-Key {self._deepl_api_key}"
            payload = {
                "text": [text],
                "source_lang": source.upper(),
                "target_lang": "EN-GB" if target == "en" else target.upper(),
                "preserve_formatting": True,
            }

        # Tre tentativi totali; autenticazione/quota esaurita non si ritentano.
        last_error = "Servizio di traduzione temporaneamente non disponibile."
        for attempt in range(3):
            retry_after: str | None = None
            try:
                async with self._semaphore:
                    async with self._session.post(
                        self._url,
                        json=payload,
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=30),
                        allow_redirects=False,
                    ) as response:
                        status = response.status
                        if status == 429 or 500 <= status < 600:
                            retry_after = response.headers.get("Retry-After")
                            last_error = f"Provider temporaneamente non disponibile (HTTP {status})."
                        elif status != 200:
                            if status in {401, 403}:
                                raise TranslationError("Accesso al provider negato: controllare la chiave API.")
                            if status == 456:
                                raise TranslationError("Quota DeepL esaurita: controllare il piano API.")
                            raise TranslationError(f"Richiesta rifiutata dal provider (HTTP {status}).")
                        else:
                            try:
                                data = await response.json()
                            except (ValueError, aiohttp.ContentTypeError):
                                raise TranslationError("Risposta JSON non valida dal provider.") from None
                            return self._extract_result(data)
            except (aiohttp.ClientError, asyncio.TimeoutError):
                # Non propagare URL, intestazioni o body presenti nelle eccezioni HTTP.
                last_error = "Connessione al provider fallita o timeout di 30 secondi."

            if attempt < 2:
                # Rilascia lo slot HTTP durante il backoff.
                await asyncio.sleep(self._retry_delay(attempt, retry_after))
        raise TranslationError(last_error) from None

    @staticmethod
    def _retry_delay(attempt: int, retry_after: str | None) -> float:
        delay = (2 ** attempt) + random.uniform(0, 0.25)
        if retry_after:
            requested = 0.0
            try:
                requested = float(retry_after)
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(retry_after)
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=timezone.utc)
                    requested = (retry_at - datetime.now(timezone.utc)).total_seconds()
                except (ValueError, TypeError, OverflowError):
                    pass
            if math.isfinite(requested):
                # Non anticipare Retry-After: per attese lunghe termina la richiesta.
                if requested > 30:
                    raise TranslationError(
                        "Il provider richiede oltre 30 secondi di attesa; riprovare più tardi."
                    )
                delay = max(delay, requested)
        return delay

    def _extract_result(self, data: Any) -> str:
        try:
            if self._provider == "libretranslate":
                result = data["translatedText"]
            else:
                result = data["translations"][0]["text"]
        except (KeyError, IndexError, TypeError):
            raise TranslationError("Risposta incompleta dal provider di traduzione.") from None
        if not isinstance(result, str) or not result.strip():
            raise TranslationError("Il provider ha restituito una traduzione vuota o non valida.")
        return result
