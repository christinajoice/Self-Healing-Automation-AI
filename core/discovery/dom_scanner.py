from playwright.async_api import Page
from typing import Optional, Dict
import re


class DOMScanner:
    def __init__(self, page: Page):
        self.page = page

    def normalize(self, text: str) -> str:
        """Lowercase and remove special characters"""
        return re.sub(r"[^a-z0-9 ]", "", text.lower())

    def tokenize(self, text: str):
        """Tokenize semantic name, removing common stop words"""
        stop_words = {"field", "input", "box", "text", "value"}
        tokens = self.normalize(text).split()
        return [t for t in tokens if t not in stop_words]

    def tokens_match(self, semantic: str, actual: str, min_matches: int = 1) -> bool:
        """
        Return True if at least min_matches tokens from semantic are present in actual text.
        This avoids false positives by requiring multiple token matches.
        """
        semantic_tokens = self.tokenize(semantic)
        actual_norm = self.normalize(actual)
        matches = sum(1 for token in semantic_tokens if token in actual_norm)
        return matches >= min_matches

    async def find_element(self, semantic_name: str) -> Optional[Dict]:
        """
        Discover a locator for the given semantic name.
        Returns a dict with healing-safe strategy ('css', 'get_by_text', 'get_by_placeholder', 'get_by_label') or None.
        """

        # 1️⃣ LABEL → INPUT via 'for' attribute
        labels = self.page.locator("label")
        label_count = await labels.count()

        for i in range(label_count):
            label = labels.nth(i)
            try:
                label_text = (await label.inner_text()).strip()
                if self.tokens_match(semantic_name, label_text, min_matches=1):
                    for_attr = await label.get_attribute("for")
                    if for_attr:
                        # React/MUI auto-generates IDs like ':r0:' which are invalid in
                        # CSS id selectors (#:r0:). Use attribute selector instead.
                        if for_attr.startswith(":") or ":" in for_attr or "." in for_attr:
                            selector = f"[id='{for_attr}']"
                        else:
                            selector = f"#{for_attr}"
                        print(f"[FOUND] {semantic_name} via label -> {selector}")
                        return {"strategy": "css", "value": selector}
            except Exception:
                continue

        # 2️⃣ INPUT elements: check id, name, placeholder, aria-label, type
        inputs = self.page.locator("input")
        input_count = await inputs.count()

        # Semantic groups for type-based matching (e.g. "user id field" → type="email")
        EMAIL_SEMANTICS = {"email", "user", "username", "userid", "login", "id"}
        PASSWORD_SEMANTICS = {"password", "pass", "pwd", "secret"}

        for i in range(input_count):
            inp = inputs.nth(i)
            try:
                el_id = await inp.get_attribute("id") or ""
                name = await inp.get_attribute("name") or ""
                placeholder = await inp.get_attribute("placeholder") or ""
                aria_label = await inp.get_attribute("aria-label") or ""
                input_type = (await inp.get_attribute("type") or "").lower()

                if self.tokens_match(semantic_name, el_id, min_matches=1):
                    print(f"[FOUND] {semantic_name} via input id -> #{el_id}")
                    return {"strategy": "css", "value": f"#{el_id}"}

                if self.tokens_match(semantic_name, name, min_matches=1):
                    print(f"[FOUND] {semantic_name} via input name -> input[name='{name}']")
                    return {"strategy": "css", "value": f"input[name='{name}']"}

                # Also check if the name value itself appears as a token substring
                # (e.g. semantic="userid field" vs name="username" — "user" is shared)
                if name and name.lower() in EMAIL_SEMANTICS:
                    sem_norm_tokens = set(self.normalize(semantic_name).split())
                    if sem_norm_tokens & EMAIL_SEMANTICS:
                        print(f"[FOUND] {semantic_name} via input name semantic match -> input[name='{name}']")
                        return {"strategy": "css", "value": f"input[name='{name}']"}

                if self.tokens_match(semantic_name, placeholder, min_matches=1):
                    print(f"[FOUND] {semantic_name} via input placeholder -> input[placeholder='{placeholder}']")
                    return {"strategy": "get_by_placeholder", "value": placeholder}

                # Compound placeholder match: "User ID" → tokens ["user","id"],
                # semantic "userid" → also split to detect shared sub-words
                if placeholder:
                    ph_tokens = set(self.normalize(placeholder).split())
                    sem_tok = self.normalize(semantic_name).replace(" ", "")
                    if ph_tokens and all(sem_tok.find(t) >= 0 for t in ph_tokens if len(t) > 1):
                        print(f"[FOUND] {semantic_name} via compound placeholder match -> get_by_placeholder '{placeholder}'")
                        return {"strategy": "get_by_placeholder", "value": placeholder}

                if aria_label and self.tokens_match(semantic_name, aria_label, min_matches=1):
                    print(f"[FOUND] {semantic_name} via input aria-label -> input[aria-label='{aria_label}']")
                    return {"strategy": "css", "value": f"input[aria-label='{aria_label}']"}

                # autocomplete attribute (e.g. autocomplete="email" or autocomplete="username")
                autocomplete = (await inp.get_attribute("autocomplete") or "").lower()
                if autocomplete and self.tokens_match(semantic_name, autocomplete, min_matches=1):
                    print(f"[FOUND] {semantic_name} via input autocomplete='{autocomplete}'")
                    return {"strategy": "css", "value": f"input[autocomplete='{autocomplete}']"}

                # Type-based semantic match for frameworks like Material UI
                # that don't always set id/name/placeholder
                sem_norm = self.normalize(semantic_name)
                sem_tokens = set(sem_norm.split())
                if input_type == "email" and sem_tokens & EMAIL_SEMANTICS:
                    print(f"[FOUND] {semantic_name} via input type=email")
                    return {"strategy": "css", "value": "input[type='email']"}
                if input_type == "password" and sem_tokens & PASSWORD_SEMANTICS:
                    print(f"[FOUND] {semantic_name} via input type=password")
                    return {"strategy": "css", "value": "input[type='password']"}
                # MUI TextFields for email/username often use type="text" — check autocomplete
                # or fall back to "first visible text input" heuristic for email semantics
                if input_type == "text" and autocomplete in ("email", "username", "user") and sem_tokens & EMAIL_SEMANTICS:
                    print(f"[FOUND] {semantic_name} via input type=text+autocomplete='{autocomplete}'")
                    return {"strategy": "css", "value": f"input[autocomplete='{autocomplete}']"}

            except Exception:
                continue

        # 3️⃣ CLICKABLE ELEMENTS: buttons, links, submit inputs
        clickables = self.page.locator("button, a, input[type=submit]")
        clickable_count = await clickables.count()

        for i in range(clickable_count):
            el = clickables.nth(i)
            try:
                text = (await el.inner_text()).strip()
                if self.tokens_match(semantic_name, text, min_matches=1):
                    el_id = await el.get_attribute("id")
                    if el_id:
                        print(f"[FOUND] {semantic_name} via clickable id -> #{el_id}")
                        return {"strategy": "css", "value": f"#{el_id}"}
                    else:
                        tag = await el.evaluate("el => el.tagName.toLowerCase()")
                        # Use tag-scoped :has-text() to avoid strict mode violations
                        # in component frameworks (MUI, Ant, etc.) where inner spans
                        # also contain the same text.
                        selector = f"{tag}:has-text('{text}')"
                        print(f"[FOUND] {semantic_name} via clickable tag+text -> {selector}")
                        return {"strategy": "css", "value": selector}
            except Exception:
                continue

        # 4️⃣ FALLBACK: Search for elements with id or class attributes containing semantic tokens
        # This is a broader CSS search but can produce false positives; use carefully.
        try:
            elements_with_id = self.page.locator("[id]")
            count_id = await elements_with_id.count()
            for i in range(count_id):
                el = elements_with_id.nth(i)
                el_id = await el.get_attribute("id") or ""
                # fallback: token match or exact id match
                if self.tokens_match(semantic_name, el_id) or (el_id == semantic_name):
                    print(f"[FOUND] {semantic_name} via fallback id -> #{el_id}")
                    return {"strategy": "css", "value": f"#{el_id}"}

            # Generic prefixes from CSS frameworks — too broad to use as locators
            GENERIC_PREFIXES = (
                "mui", "css-", "sc-", "ant-", "chakra-", "v-", "el-",
                "ng-", "tw-", "bs-", "p-", "m-", "flex", "grid", "col-",
            )
            elements_with_class = self.page.locator("[class]")
            count_class = await elements_with_class.count()
            for i in range(count_class):
                el = elements_with_class.nth(i)
                class_attr = await el.get_attribute("class") or ""
                first_class = class_attr.split()[0] if class_attr else ""
                if not first_class:
                    continue
                # Skip generic framework class names that match too broadly
                if any(first_class.lower().startswith(p) for p in GENERIC_PREFIXES):
                    continue
                if self.tokens_match(semantic_name, first_class, min_matches=1):
                    selector = f".{first_class}"
                    print(f"[FOUND] {semantic_name} via fallback class selector -> {selector}")
                    return {"strategy": "css", "value": selector}

        except Exception:
            pass

        # 5️⃣ Nothing found
        print(f"[NOT FOUND] No locator discovered for: {semantic_name}")
        return None