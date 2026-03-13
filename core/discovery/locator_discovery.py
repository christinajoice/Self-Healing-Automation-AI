# locatordiscovery.py
from core.cache.locator_cache import LocatorCache
from core.discovery.dom_scanner import DOMScanner
from core.ai.ai_locator_suggester import AILocatorSuggester
from playwright.async_api import TimeoutError
from datetime import datetime, timezone
from typing import Optional


class LocatorDiscovery:
    def __init__(self, page, app_url: str):
        self.page = page
        self.app_url = app_url
        self.cache = LocatorCache()
        self.scanner = DOMScanner(page)
        self.ai = AILocatorSuggester(page)

    async def resolve(self, semantic_name: str, validate_visible: bool = True) -> dict:
        """
        Resolve locator with self-healing:
        1. Use cached locators first (sorted by confidence)
        2. Validate locator exists and is visible (optional)
        3. If all cached locators fail, attempt rediscovery
        4. Return healing-safe locator dict
        """
        await self.page.wait_for_load_state("domcontentloaded")
        # SPAs (React/Angular/Vue) inject inputs after JS runs.
        # Wait for at least one interactive element before scanning.
        try:
            await self.page.wait_for_selector("input, button", timeout=8000)
        except Exception:
            pass

        raw_candidates = self.cache.get_all(self.app_url, semantic_name) or []
        candidates = [
            c for c in raw_candidates
            if isinstance(c, dict) and "strategy" in c and "value" in c
        ]

        # --- Try cached locators first ---
        for candidate in sorted(candidates, key=lambda x: -x.get("confidence", 1.0)):
            try:
                locator = self._build_locator(candidate)
                count = await locator.count()

                if count == 0:
                    raise TimeoutError("Locator not found in DOM")

                if validate_visible:
                    visible_count = await locator.evaluate_all(
                        "els => els.filter(e => e.offsetParent !== null).length"
                    )
                    if visible_count == 0:
                        raise TimeoutError("Locator exists but is not visible")

                # Success → update metadata
                candidate["failures"] = 0
                candidate["last_used"] = datetime.now(timezone.utc).isoformat()
                self.cache.set(self.app_url, semantic_name, candidates)
                print(f"[CACHED] Using cached locator for '{semantic_name}': {candidate}")
                return candidate

            except TimeoutError:
                candidate["failures"] = candidate.get("failures", 0) + 1
                candidate["confidence"] = candidate.get("confidence", 1.0) * 0.9
                print(f"[WARN] Cached locator failed: {candidate['strategy']} -> trying next")
            except ValueError as ve:
                print(f"[WARN] Cached locator rejected: {candidate} -> {ve}")

        # --- No cached locator worked → try rediscovery ---
        new_locator = await self._discover_and_cache(semantic_name)
        if new_locator:
            candidates.append(new_locator)
            self.cache.set(self.app_url, semantic_name, candidates)
            print(f"[REDISCOVERED] locator for '{semantic_name}': {new_locator}")
            return new_locator

        # --- Fallback if rediscovery fails ---
        if candidates:
            fallback = sorted(candidates, key=lambda x: -x.get("confidence", 0.0))[0]
            print(f"[WARN] Could not rediscover '{semantic_name}', using cached locator anyway")
            return fallback

        raise ValueError(f"Discovered locator invalid for '{semantic_name}': None")

    async def click_and_wait(self, locator_meta: dict, wait_for_selector: str = None, navigates: bool = False):
        """
        Click element and optionally wait for next page element.
        navigates=True → indicates page navigation happens, avoid retrying same locator.
        """
        try:
            locator = self._build_locator(locator_meta)
            await locator.click()
            print(f"[CLICK] '{locator_meta.get('value')}' using '{locator_meta.get('strategy')}'")

            if navigates:
                # Wait for either provided selector or page DOM load
                if wait_for_selector:
                    await self.page.wait_for_selector(wait_for_selector, timeout=10000)
                else:
                    await self.page.wait_for_load_state("domcontentloaded")
            elif wait_for_selector:
                # Click does not navigate but we want to ensure element appears
                await self.page.wait_for_selector(wait_for_selector, timeout=5000)

        except Exception as e:
            print(f"[ERROR] Click failed for {locator_meta}: {e}")
            raise

    def _build_locator(self, locator_meta: dict):
        """
        Convert locator metadata to a Playwright locator
        """
        if not isinstance(locator_meta, dict):
            raise ValueError("Invalid locator metadata (not a dict)")

        if "strategy" not in locator_meta or "value" not in locator_meta:
            raise ValueError(f"Incomplete locator metadata: {locator_meta}")

        strategy = locator_meta["strategy"].lower()
        value = locator_meta["value"]

        if strategy in ("get_by_label", "label"):
            return self.page.get_by_label(value)
        if strategy in ("get_by_placeholder", "placeholder"):
            return self.page.get_by_placeholder(value)
        if strategy in ("get_by_text", "text"):
            return self.page.get_by_text(value, exact=True)
        if strategy == "css":
            return self.page.locator(value)
        if strategy in ("get_by_role", "role"):
            raise ValueError("Role-based locators are disabled for self-healing")

        raise ValueError(f"Unsupported locator strategy: {strategy}")

    async def _discover_and_cache(self, semantic_name: str) -> Optional[dict]:
        """
        Two-stage locator discovery:
        1. DOMScanner  — fast rule-based token matching (no network, instant)
        2. AILocatorSuggester — local LLM via Ollama (free, no subscription)
                                only runs when DOMScanner returns None
        """
        # Stage 1: rule-based DOM scan
        new_locator = await self.scanner.find_element(semantic_name)

        # Stage 2: AI fallback when rule-based scan fails
        if not new_locator:
            print(f"[AI] DOMScanner could not find '{semantic_name}', asking LLM...")
            new_locator = await self.ai.suggest(semantic_name)
            if new_locator:
                new_locator["source"] = "ai"

        if new_locator:
            new_locator.setdefault("confidence", 1.0)
            new_locator.setdefault("failures", 0)
            new_locator["last_used"] = datetime.now(timezone.utc).isoformat()

        return new_locator