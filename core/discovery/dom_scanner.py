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
                        print(f"[FOUND] {semantic_name} via label -> #{for_attr}")
                        return {"strategy": "css", "value": f"#{for_attr}"}
            except Exception:
                continue

        # 2️⃣ INPUT elements: check id, name, placeholder
        inputs = self.page.locator("input")
        input_count = await inputs.count()

        for i in range(input_count):
            inp = inputs.nth(i)
            try:
                el_id = await inp.get_attribute("id") or ""
                name = await inp.get_attribute("name") or ""
                placeholder = await inp.get_attribute("placeholder") or ""

                # Match on id or name, require at least 1 token match
                if self.tokens_match(semantic_name, el_id, min_matches=1):
                    print(f"[FOUND] {semantic_name} via input id -> #{el_id}")
                    return {"strategy": "css", "value": f"#{el_id}"}

                if self.tokens_match(semantic_name, name, min_matches=1):
                    print(f"[FOUND] {semantic_name} via input name -> input[name='{name}']")
                    return {"strategy": "css", "value": f"input[name='{name}']"}

                # Match on placeholder with at least 1 token
                if self.tokens_match(semantic_name, placeholder, min_matches=1):
                    print(f"[FOUND] {semantic_name} via input placeholder -> input[placeholder='{placeholder}']")
                    return {"strategy": "get_by_placeholder", "value": placeholder}

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
                        print(f"[FOUND] {semantic_name} via clickable text -> {text}")
                        return {"strategy": "get_by_text", "value": text}
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

            elements_with_class = self.page.locator("[class]")
            count_class = await elements_with_class.count()
            for i in range(count_class):
                el = elements_with_class.nth(i)
                class_attr = await el.get_attribute("class") or ""
                # Use only first class token for selector safety
                first_class = class_attr.split()[0] if class_attr else ""
                if first_class and self.tokens_match(semantic_name, first_class, min_matches=1):
                    selector = f".{first_class}"
                    print(f"[FOUND] {semantic_name} via fallback class selector -> {selector}")
                    return {"strategy": "css", "value": selector}

        except Exception:
            pass

        # 5️⃣ Nothing found
        print(f"[NOT FOUND] No locator discovered for: {semantic_name}")
        return None