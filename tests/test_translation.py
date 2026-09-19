"""Test HTTP locali: nessuna credenziale reale e nessuna richiesta esterna."""

import asyncio
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import socket
import unittest
from unittest.mock import patch

import aiohttp
from aiohttp import web

from translator_bot.translation import TranslationError, TranslationService


class TranslationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []
        self.responses = []
        self.block = None
        self.active = 0
        self.maximum_active = 0
        self.started = asyncio.Event()
        app = web.Application()
        app.router.add_post("/translate", self.handle_request)
        app.router.add_post("/v2/translate", self.handle_request)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        self.url = f"http://127.0.0.1:{sock.getsockname()[1]}"
        site = web.SockSite(self.runner, sock)
        await site.start()
        self.session = aiohttp.ClientSession()
        self.services = []

    async def asyncTearDown(self):
        if self.block:
            self.block.set()
        for service in self.services:
            await service.close()
        await self.session.close()
        await self.runner.cleanup()

    def service(self, **kwargs):
        options = {"libretranslate_url": self.url, "deepl_api_url": self.url}
        options.update(kwargs)
        service = TranslationService(self.session, **options)
        self.services.append(service)
        return service

    async def handle_request(self, request):
        self.calls.append((request.path, await request.json(), dict(request.headers)))
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        self.started.set()
        try:
            if self.block:
                await self.block.wait()
            if self.responses:
                status, data, *extra = self.responses.pop(0)
                return web.json_response(data, status=status, headers=extra[0] if extra else None)
            if request.path == "/v2/translate":
                return web.json_response({"translations": [{"text": "Hello"}]})
            return web.json_response({"translatedText": "Bonjour"})
        finally:
            self.active -= 1

    async def test_same_language_and_empty_text_make_no_request(self):
        service = self.service()
        self.assertEqual(await service.translate("ciao", "it", "it"), "ciao")
        self.assertEqual(await service.translate("  \n", "it", "fr"), "  \n")
        self.assertEqual(self.calls, [])

    async def test_libretranslate_payload_and_cache(self):
        service = self.service(libretranslate_api_key="fake-key")
        for _ in range(2):
            self.assertEqual(await service.translate("Ciao", "it", "fr"), "Bonjour")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][1], {
            "q": "Ciao", "source": "it", "target": "fr", "format": "text", "api_key": "fake-key"
        })

    async def test_deepl_payload_and_authentication(self):
        service = self.service(provider="deepl", deepl_api_key="fake-deepl-key")
        self.assertEqual(await service.translate("Ciao", "it", "en"), "Hello")
        path, payload, headers = self.calls[0]
        self.assertEqual(path, "/v2/translate")
        self.assertEqual(payload["text"], ["Ciao"])
        self.assertEqual(payload["source_lang"], "IT")
        self.assertEqual(payload["target_lang"], "EN-GB")
        self.assertEqual(headers["Authorization"], "DeepL-Auth-Key fake-deepl-key")

    async def test_concurrent_identical_requests_are_coalesced(self):
        self.block = asyncio.Event()
        service = self.service()
        tasks = [asyncio.create_task(service.translate("Ciao", "it", "fr")) for _ in range(8)]
        await asyncio.wait_for(self.started.wait(), 2)
        self.block.set()
        self.assertEqual(await asyncio.gather(*tasks), ["Bonjour"] * 8)
        self.assertEqual(len(self.calls), 1)

    async def test_cancelling_one_waiter_preserves_shared_request(self):
        self.block = asyncio.Event()
        service = self.service()
        first = asyncio.create_task(service.translate("Ciao", "it", "fr"))
        second = asyncio.create_task(service.translate("Ciao", "it", "fr"))
        await asyncio.wait_for(self.started.wait(), 2)
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        self.block.set()
        self.assertEqual(await second, "Bonjour")
        self.assertEqual(len(self.calls), 1)

    async def test_concurrency_is_bounded(self):
        self.block = asyncio.Event()
        service = self.service(concurrency=2)
        tasks = [asyncio.create_task(service.translate(str(i), "it", "fr")) for i in range(5)]
        await asyncio.wait_for(self.started.wait(), 2)
        # Yield finché i primi due handler sono entrati; gli altri restano in coda.
        for _ in range(30):
            if self.active == 2:
                break
            await asyncio.sleep(0.005)
        self.assertEqual(self.active, 2)
        self.block.set()
        await asyncio.gather(*tasks)
        self.assertEqual(self.maximum_active, 2)
        self.assertEqual(len(self.calls), 5)

    async def test_transient_errors_retry_then_succeed(self):
        self.responses = [(429, {}), (503, {}), (200, {"translatedText": "Bonjour"})]
        service = self.service()
        with patch.object(service, "_retry_delay", return_value=0):
            self.assertEqual(await service.translate("Ciao", "it", "fr"), "Bonjour")
        self.assertEqual(len(self.calls), 3)

    async def test_retries_are_bounded_and_error_is_sanitized(self):
        self.responses = [(500, {"error": "secret-text-key"})] * 3
        service = self.service()
        with patch.object(service, "_retry_delay", return_value=0):
            with self.assertRaises(TranslationError) as raised:
                await service.translate("private-message", "it", "fr")
        self.assertEqual(len(self.calls), 3)
        self.assertNotIn("secret-text-key", str(raised.exception))
        self.assertNotIn("private-message", str(raised.exception))

    async def test_long_retry_after_fails_without_retrying_early(self):
        service = self.service()
        for status in (429, 503):
            with self.subTest(status=status):
                self.responses = [(status, {}, {"Retry-After": "60"})]
                count = len(self.calls)
                with self.assertRaisesRegex(TranslationError, "oltre 30 secondi"):
                    await service.translate(f"retry-{status}", "it", "fr")
                self.assertEqual(len(self.calls), count + 1)

    def test_retry_after_short_delay_is_respected(self):
        self.assertEqual(TranslationService._retry_delay(0, "20"), 20)
        self.assertEqual(TranslationService._retry_delay(0, "30"), 30)

    def test_retry_after_supports_http_dates(self):
        short = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=20), usegmt=True)
        self.assertGreater(TranslationService._retry_delay(0, short), 18)
        long = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=60), usegmt=True)
        with self.assertRaises(TranslationError):
            TranslationService._retry_delay(0, long)

    async def test_forbidden_and_exhausted_quota_do_not_retry(self):
        service = self.service()
        for status in (403, 456):
            with self.subTest(status=status):
                self.responses = [(status, {"error": "private-secret"})]
                count = len(self.calls)
                with self.assertRaises(TranslationError):
                    await service.translate(f"text-{status}", "it", "fr")
                self.assertEqual(len(self.calls), count + 1)

    async def test_invalid_response_is_not_cached(self):
        self.responses = [(200, {"translatedText": 42})]
        service = self.service()
        with self.assertRaises(TranslationError):
            await service.translate("Ciao", "it", "fr")
        self.assertEqual(await service.translate("Ciao", "it", "fr"), "Bonjour")
        self.assertEqual(len(self.calls), 2)

    async def test_cache_ttl_and_lru_limit(self):
        service = self.service(cache_size=2, cache_ttl=60)
        await service.translate("one", "it", "fr")
        await service.translate("two", "it", "fr")
        await service.translate("one", "it", "fr")  # Mantiene one; two sarà espulso.
        await service.translate("three", "it", "fr")
        await service.translate("two", "it", "fr")
        self.assertEqual(len(self.calls), 4)
        # Scadenza deterministica senza modificare l'orologio dell'event loop.
        key = ("two", "it", "fr")
        service._cache[key] = (0, "Expired")
        await service.translate("two", "it", "fr")
        self.assertEqual(len(self.calls), 5)
        self.assertLessEqual(len(service._cache), 2)

    async def test_network_failure_is_retried_and_sanitized(self):
        service = self.service()
        with patch.object(self.session, "post", side_effect=aiohttp.ClientConnectionError("secret-url")) as post:
            with patch.object(service, "_retry_delay", return_value=0):
                with self.assertRaises(TranslationError) as raised:
                    await service.translate("Ciao", "it", "fr")
        self.assertEqual(post.call_count, 3)
        self.assertNotIn("secret-url", str(raised.exception))

    async def test_close_cancels_pending_work(self):
        self.block = asyncio.Event()
        service = self.service()
        caller = asyncio.create_task(service.translate("Ciao", "it", "fr"))
        await asyncio.wait_for(self.started.wait(), 2)
        await service.close()
        with self.assertRaises(asyncio.CancelledError):
            await caller
        with self.assertRaises(TranslationError):
            await service.translate("Ciao", "it", "fr")


if __name__ == "__main__":
    unittest.main()
