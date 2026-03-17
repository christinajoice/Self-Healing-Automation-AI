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

    async def resolve_with_context(
        self, semantic_name: str, context_text: str
    ) -> dict:
        """
        Resolve a locator for semantic_name anchored to a known context_text.

        This is the general-purpose mechanism for finding an element that is
        associated with (or lives near) another element you already know.

        Typical use-cases
        -----------------
        - Click the action icon in the row that contains "PATHWAY"
        - Check the checkbox next to the label "Enable notifications"
        - Click the Edit button inside the card titled "Account Settings"
        - Fill the input that appears beside the "Start Date" label
        - Click the delete icon in the list item "John Doe"

        Test-case CSV convention
        ------------------------
        For ``click`` steps the ``Data`` column carries the context anchor text::

            TC_01,6,click,action icon,PATHWAY,high
                                      ^target  ^anchor (Data)

        Discovery order
        ---------------
        1. Cached context-keyed locators (fast path, healed automatically)
        2. DOMScanner.find_element_near_anchor()  — rule-based, instant
        3. AILocatorSuggester.suggest()           — LLM fallback
        4. Global resolve()                       — anchor-free last resort
        """
        await self.page.wait_for_load_state("domcontentloaded")
        try:
            await self.page.wait_for_selector("input, button, a", timeout=8000)
        except Exception:
            pass

        # Cache key encodes both the target element and its anchor context so
        # context-specific locators are stored independently from global ones.
        cache_key = f"{semantic_name} @ {context_text}"
        raw_candidates = self.cache.get_all(self.app_url, cache_key) or []
        candidates = [
            c for c in raw_candidates
            if isinstance(c, dict) and "strategy" in c and "value" in c
        ]

        # --- Try cached context-keyed locators first ---
        for candidate in sorted(candidates, key=lambda x: -x.get("confidence", 1.0)):
            try:
                locator = self._build_locator(candidate)
                count = await locator.count()
                if count == 0:
                    raise TimeoutError("Locator not found in DOM")

                visible_count = await locator.evaluate_all(
                    "els => els.filter(e => e.offsetParent !== null).length"
                )
                if visible_count == 0:
                    raise TimeoutError("Locator not visible")

                candidate["failures"] = 0
                candidate["last_used"] = datetime.now(timezone.utc).isoformat()
                self.cache.set(self.app_url, cache_key, candidates)
                print(f"[CACHED] Context locator for '{cache_key}': {candidate}")
                return candidate

            except TimeoutError:
                candidate["failures"] = candidate.get("failures", 0) + 1
                candidate["confidence"] = candidate.get("confidence", 1.0) * 0.9
                print(f"[WARN] Context cached locator failed: {candidate['strategy']} → trying next")
            except ValueError as ve:
                print(f"[WARN] Context cached locator rejected: {candidate} → {ve}")

        # --- Rediscover using anchor-aware scan ---
        new_locator = await self.scanner.find_element_near_anchor(semantic_name, context_text)

        # --- AI fallback when rule-based scan fails ---
        if not new_locator:
            print(f"[AI] Anchor scan failed for '{semantic_name}' near '{context_text}', asking LLM...")
            new_locator = await self.ai.suggest(f"{semantic_name} near {context_text}")
            if new_locator:
                new_locator["source"] = "ai"

        if new_locator:
            # Ensure the discovered locator is unique for this context.
            # Generic selectors like [aria-label='Action'] match every row's icon —
            # if multiple elements match we must scope the selector to the anchor
            # container so subsequent runs click the right row.
            new_locator = await self._ensure_unique_in_context(new_locator, context_text)

            new_locator.setdefault("confidence", 1.0)
            new_locator.setdefault("failures", 0)
            new_locator["last_used"] = datetime.now(timezone.utc).isoformat()
            candidates.append(new_locator)
            self.cache.set(self.app_url, cache_key, candidates)
            print(f"[REDISCOVERED] Context locator for '{cache_key}': {new_locator}")
            return new_locator

        # --- Final fallback: drop context and search globally ---
        print(f"[WARN] Could not find '{semantic_name}' near '{context_text}'. Falling back to global resolve.")
        return await self.resolve(semantic_name)

    async def _ensure_unique_in_context(self, locator_meta: dict, context_text: str) -> dict:
        """
        Guarantee that a discovered locator resolves to exactly ONE element by
        scoping it to the closest ancestor that contains context_text.

        This prevents generic selectors like [aria-label='Action'] — which match
        every row's action button — from being cached without the anchor scope.

        Strategy
        --------
        1. Count how many elements the raw locator matches.
        2. If count == 1, the locator is already unique — return as-is.
        3. If count > 1, try prepending progressively broader ancestor containers
           (tightest first) with :has-text('context') until count == 1.
        4. If nothing makes it unique, still return the tightest scoped version
           so at minimum we land inside the right row/section.
        """
        try:
            raw_locator = self._build_locator(locator_meta)
            count = await raw_locator.count()
            if count <= 1:
                return locator_meta   # Already unique

            print(
                f"[SCOPE] Locator '{locator_meta['value']}' matches {count} elements. "
                f"Scoping to anchor '{context_text}'…"
            )

            safe_ctx = DOMScanner._css_escape(context_text)
            base_val = locator_meta["value"]

            # Try ancestor containers from tightest to broadest
            ANCESTOR_SCOPES = [
                "tr",
                "li",
                "[role='row']",
                "[role='listitem']",
                "[role='option']",
                "div[class*='row']",
                "div[class*='item']",
                "div[class*='card']",
                "div[class*='entry']",
                "div[class*='record']",
                "tbody",
                "ul",
                "ol",
                "table",
                "section",
                "article",
            ]

            for ancestor_sel in ANCESTOR_SCOPES:
                scoped_val = f"{ancestor_sel}:has-text('{safe_ctx}') {base_val}"
                try:
                    scoped_count = await self.page.locator(scoped_val).count()
                    if scoped_count == 1:
                        print(f"[SCOPED] Unique locator via '{ancestor_sel}': {scoped_val}")
                        return {**locator_meta, "strategy": "css", "value": scoped_val}
                    if scoped_count > 1:
                        # Still multiple but narrowing — save as best candidate so far
                        # (continue looking for a tighter scope)
                        pass
                except Exception:
                    continue

            # No ancestor yielded exactly 1 match — use the tightest row scope anyway
            # so at least we don't click a random row.
            fallback_val = f"tr:has-text('{safe_ctx}') {base_val}"
            print(f"[SCOPE FALLBACK] Using row-scoped selector: {fallback_val}")
            return {**locator_meta, "strategy": "css", "value": fallback_val}

        except Exception as e:
            print(f"[WARN] _ensure_unique_in_context failed: {e}")
            return locator_meta

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