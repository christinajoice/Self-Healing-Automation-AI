from playwright.async_api import Page
from typing import Optional, Dict, List
import re


class DOMScanner:
    def __init__(self, page: Page):
        self.page = page

    def normalize(self, text: str) -> str:
        """Lowercase and remove special characters"""
        return re.sub(r"[^a-z0-9 ]", "", text.lower())

    def tokenize(self, text: str):
        """Tokenize semantic name, removing common stop words"""
        # "icon" is excluded from meaningful tokens because it is a suffix used in
        # both real UI elements ("edit icon", "action icon") AND unrelated HTML
        # attributes (id="favicon", class="icon-wrapper") — keeping it causes
        # the fallback id/class scan to match completely unrelated elements.
        stop_words = {
            # UI element type words
            "field", "input", "box", "text", "value", "icon", "button", "btn",
            # Common English filler words that appear in any sentence and cause
            # false-positive whole-word matches (e.g. "the" in "in the dropdown"
            # matching "Please select a state to download the report")
            "a", "an", "the", "in", "on", "at", "to", "for", "of", "or",
            "and", "by", "from", "with", "that", "this", "is", "it",
        }
        tokens = self.normalize(text).split()
        return [t for t in tokens if t not in stop_words]

    def tokens_match(self, semantic: str, actual: str, min_matches: int = 1) -> bool:
        """
        Return True if at least min_matches tokens from semantic appear as whole words
        in the actual text.  Whole-word matching prevents "filter" from matching
        "Filters" (plural) or "filtering" which would cause false positives.
        """
        semantic_tokens = self.tokenize(semantic)
        actual_norm = self.normalize(actual)
        # Build word set from actual for O(1) whole-word lookup
        actual_words = set(actual_norm.split())
        matches = sum(1 for token in semantic_tokens if token in actual_words)
        return matches >= min_matches

    @staticmethod
    def _css_escape(text: str) -> str:
        """Escape single quotes and backslashes for safe use inside CSS attribute selectors."""
        return text.replace("\\", "\\\\").replace("'", "\\'")

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
                    safe_id = self._css_escape(el_id)
                    print(f"[FOUND] {semantic_name} via input id -> [id='{safe_id}']")
                    return {"strategy": "css", "value": f"[id='{safe_id}']"}

                if self.tokens_match(semantic_name, name, min_matches=1):
                    print(f"[FOUND] {semantic_name} via input name -> input[name='{name}']")
                    return {"strategy": "css", "value": f"input[name='{name}']"}

                # Also check if the name value itself appears as a token substring
                # (e.g. semantic="userid field" vs name="username" — "user" is shared).
                # Guard: skip when the semantic clearly describes a button/action, not a field
                # (e.g. "login button" contains "login" which is in EMAIL_SEMANTICS, but it
                # is not an input — this check would otherwise return the username input).
                BUTTON_WORDS = {"button", "btn", "submit", "click", "link"}
                sem_lower = semantic_name.lower()
                is_button_semantic = any(w in sem_lower for w in BUTTON_WORDS)
                if not is_button_semantic and name and name.lower() in EMAIL_SEMANTICS:
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
        # Also includes [role=button] for custom interactive components,
        # [role=menuitem] for MUI/Ant dropdown menu items (e.g. DataGrid column menu),
        # and elements with aria-label / tooltip for icon-only navigation buttons.
        clickables = self.page.locator(
            "button, a, input[type=submit], [role='button'], [role='menuitem'], [role='option']"
        )
        clickable_count = await clickables.count()

        for i in range(clickable_count):
            el = clickables.nth(i)
            try:
                el_id = (await el.get_attribute("id") or "").strip()
                aria_label = (await el.get_attribute("aria-label") or "").strip()
                title_attr = (await el.get_attribute("title") or "").strip()

                # inner_text() respects CSS visibility — it skips screen-reader-only
                # spans (display:none / visibility:hidden).  text_content() returns
                # the raw DOM text regardless of CSS, so visually-hidden labels
                # (e.g. <span class="sr-only">Network Adequacy</span>) are captured.
                visible_text = (await el.inner_text()).strip()
                raw_text = (await el.text_content() or "").strip()
                # Prefer visible text; fall back to raw DOM text for hidden labels.
                text = visible_text or raw_text

                # Tooltip / data attributes used by React-Tooltip, MUI Tooltip,
                # Ant Design, Tippy, etc. when the sidebar is in collapsed state.
                tooltip = (
                    await el.get_attribute("data-tooltip")
                    or await el.get_attribute("data-tip")
                    or await el.get_attribute("data-title")
                    or await el.get_attribute("data-original-title")
                    or ""
                ).strip()

                # Walk up one level to catch tooltip wrappers like
                # <div data-tooltip="Network Adequacy"><button>…</button></div>
                parent_tooltip = ""
                try:
                    parent_tooltip = (
                        await el.evaluate(
                            "el => el.parentElement ? ("
                            "  el.parentElement.getAttribute('data-tooltip') || "
                            "  el.parentElement.getAttribute('data-tip') || "
                            "  el.parentElement.getAttribute('data-title') || "
                            "  el.parentElement.getAttribute('title') || "
                            "  el.parentElement.getAttribute('aria-label') || ''"
                            ") : ''"
                        )
                    ).strip()
                except Exception:
                    pass

                # Match priority: visible text → aria-label → title → tooltip
                # attr → hidden DOM text → parent tooltip
                matched_signal = None
                if visible_text and self.tokens_match(semantic_name, visible_text, min_matches=1):
                    matched_signal = "text"
                elif aria_label and self.tokens_match(semantic_name, aria_label, min_matches=1):
                    matched_signal = "aria-label"
                elif title_attr and self.tokens_match(semantic_name, title_attr, min_matches=1):
                    matched_signal = "title"
                elif tooltip and self.tokens_match(semantic_name, tooltip, min_matches=1):
                    matched_signal = "tooltip"
                elif raw_text and raw_text != visible_text and self.tokens_match(semantic_name, raw_text, min_matches=1):
                    matched_signal = "raw-text"
                elif parent_tooltip and self.tokens_match(semantic_name, parent_tooltip, min_matches=1):
                    matched_signal = "parent-tooltip"

                if matched_signal:
                    if el_id:
                        safe_id = self._css_escape(el_id)
                        print(f"[FOUND] {semantic_name} via clickable id ({matched_signal}) -> [id='{safe_id}']")
                        return {"strategy": "css", "value": f"[id='{safe_id}']"}
                    if aria_label and matched_signal == "aria-label":
                        selector = f"[aria-label='{self._css_escape(aria_label)}']"
                        print(f"[FOUND] {semantic_name} via clickable aria-label -> {selector}")
                        return {"strategy": "css", "value": selector}
                    if title_attr and matched_signal == "title":
                        tag = await el.evaluate("el => el.tagName.toLowerCase()")
                        selector = f"{tag}[title='{self._css_escape(title_attr)}']"
                        print(f"[FOUND] {semantic_name} via clickable title -> {selector}")
                        return {"strategy": "css", "value": selector}
                    if tooltip and matched_signal == "tooltip":
                        attr_name = next(
                            a for a in ("data-tooltip", "data-tip", "data-title", "data-original-title")
                            if await el.get_attribute(a)
                        )
                        selector = f"[{attr_name}='{self._css_escape(tooltip)}']"
                        print(f"[FOUND] {semantic_name} via clickable {attr_name} -> {selector}")
                        return {"strategy": "css", "value": selector}
                    if parent_tooltip and matched_signal == "parent-tooltip":
                        # Scope the selector to the parent wrapper that carries the tooltip
                        tag = await el.evaluate("el => el.tagName.toLowerCase()")
                        selector = (
                            f"[data-tooltip='{self._css_escape(parent_tooltip)}'] {tag},"
                            f"[data-tip='{self._css_escape(parent_tooltip)}'] {tag},"
                            f"[title='{self._css_escape(parent_tooltip)}'] {tag},"
                            f"[aria-label='{self._css_escape(parent_tooltip)}'] {tag}"
                        )
                        print(f"[FOUND] {semantic_name} via parent tooltip wrapper -> {selector}")
                        return {"strategy": "css", "value": selector}
                    # raw-text or visible text match
                    tag = await el.evaluate("el => el.tagName.toLowerCase()")
                    use_text = visible_text or raw_text
                    selector = f"{tag}:has-text('{self._css_escape(use_text)}')"
                    print(f"[FOUND] {semantic_name} via clickable tag+text ({matched_signal}) -> {selector}")
                    return {"strategy": "css", "value": selector}

            except Exception:
                continue

        # 3.5️⃣ LABELLED NON-BUTTON ELEMENTS
        # MUI / Ant / custom nav items are often plain <div> or <span> elements
        # that carry their human-readable label via title="…", aria-label="…", or
        # a tooltip data-attribute — not as button/a tags, so step 3 misses them.
        # Scan every element that has one of these label attributes and match against
        # the semantic name.  Exclude known non-interactive head elements.
        NON_INTERACTIVE_TAGS_LABEL = {
            "link", "meta", "script", "style", "head", "title",
            "base", "noscript", "template", "slot", "html", "body",
        }
        LABEL_ATTR_SELECTORS = [
            "[title]",
            "[aria-label]",
            "[data-tooltip]",
            "[data-tip]",
            "[data-title]",
            "[data-original-title]",
        ]
        for attr_sel in LABEL_ATTR_SELECTORS:
            try:
                labelled = self.page.locator(attr_sel)
                labelled_count = await labelled.count()
                for i in range(labelled_count):
                    el = labelled.nth(i)
                    try:
                        tag = (await el.evaluate("el => el.tagName.toLowerCase()")).strip()
                        if tag in NON_INTERACTIVE_TAGS_LABEL:
                            continue
                        attr_name = attr_sel.strip("[]")
                        attr_val = (await el.get_attribute(attr_name) or "").strip()
                        if not attr_val:
                            continue
                        if not self.tokens_match(semantic_name, attr_val, min_matches=1):
                            continue
                        # Matched — build the most stable selector
                        el_id = (await el.get_attribute("id") or "").strip()
                        if el_id:
                            safe_id = self._css_escape(el_id)
                            print(f"[FOUND] {semantic_name} via labelled element id -> [id='{safe_id}']")
                            return {"strategy": "css", "value": f"[id='{safe_id}']"}
                        safe_val = self._css_escape(attr_val)
                        selector = f"[{attr_name}='{safe_val}']"
                        print(f"[FOUND] {semantic_name} via {attr_name} on <{tag}> -> {selector}")
                        return {"strategy": "css", "value": selector}
                    except Exception:
                        continue
            except Exception:
                continue

        # 4️⃣ FALLBACK: Search for elements with id or class attributes containing semantic tokens
        # This is a broader CSS search but can produce false positives; use carefully.
        #
        # NON_INTERACTIVE_TAGS are excluded because they are never visible/clickable
        # UI elements (e.g. <link id="favicon"> lives in <head> and is invisible).
        NON_INTERACTIVE_TAGS = {
            "link", "meta", "script", "style", "head", "title",
            "base", "noscript", "template", "slot",
        }

        try:
            elements_with_id = self.page.locator("[id]")
            count_id = await elements_with_id.count()
            for i in range(count_id):
                el = elements_with_id.nth(i)
                try:
                    tag = (await el.evaluate("el => el.tagName.toLowerCase()")).strip()
                    if tag in NON_INTERACTIVE_TAGS:
                        continue
                except Exception:
                    continue
                el_id = await el.get_attribute("id") or ""
                # Require at least 2 meaningful token matches to reduce false positives,
                # OR an exact id == semantic_name hit.
                sem_tokens = self.tokenize(semantic_name)
                min_hits = 2 if len(sem_tokens) >= 2 else 1
                if self.tokens_match(semantic_name, el_id, min_matches=min_hits) or (el_id == semantic_name):
                    safe_id = self._css_escape(el_id)
                    print(f"[FOUND] {semantic_name} via fallback id -> [id='{safe_id}']")
                    return {"strategy": "css", "value": f"[id='{safe_id}']"}

            # Generic prefixes from CSS frameworks — too broad to use as locators
            GENERIC_PREFIXES = (
                "mui", "css-", "sc-", "ant-", "chakra-", "v-", "el-",
                "ng-", "tw-", "bs-", "p-", "m-", "flex", "grid", "col-",
            )
            elements_with_class = self.page.locator("[class]")
            count_class = await elements_with_class.count()
            for i in range(count_class):
                el = elements_with_class.nth(i)
                try:
                    tag = (await el.evaluate("el => el.tagName.toLowerCase()")).strip()
                    if tag in NON_INTERACTIVE_TAGS:
                        continue
                except Exception:
                    continue
                class_attr = await el.get_attribute("class") or ""
                first_class = class_attr.split()[0] if class_attr else ""
                if not first_class:
                    continue
                # Skip generic framework class names that match too broadly
                if any(first_class.lower().startswith(p) for p in GENERIC_PREFIXES):
                    continue
                sem_tokens = self.tokenize(semantic_name)
                min_hits = 2 if len(sem_tokens) >= 2 else 1
                if self.tokens_match(semantic_name, first_class, min_matches=min_hits):
                    selector = f".{first_class}"
                    print(f"[FOUND] {semantic_name} via fallback class selector -> {selector}")
                    return {"strategy": "css", "value": selector}

        except Exception:
            pass

        # 5️⃣ DataGrid action-column probe
        # MUI / AG-Grid / custom grids render icon-only action buttons inside
        # [role='gridcell'][data-field='action'] (or similar field names).
        # These buttons carry no aria-label / title / text, so all prior scans
        # miss them. Match by: a semantic token appears in the data-field value
        # of the gridcell, OR the semantic explicitly mentions "action" / icon types.
        ACTION_SYNONYMS = {"action", "navigate", "open", "view", "launch", "detail", "link"}
        sem_tokens_set = set(self.tokenize(semantic_name))
        if sem_tokens_set & ACTION_SYNONYMS or "icon" in semantic_name.lower():
            try:
                gridcells = self.page.locator("[role='gridcell'][data-field]")
                gc_count = await gridcells.count()
                for i in range(gc_count):
                    cell = gridcells.nth(i)
                    field = (await cell.get_attribute("data-field") or "").lower()
                    # Match when any semantic token appears in the field name
                    # OR when field name is "action" and we're looking for action/navigate icons
                    field_tokens = set(self.normalize(field).split())
                    if not (sem_tokens_set & field_tokens or
                            (field in ("action", "actions") and sem_tokens_set & ACTION_SYNONYMS)):
                        continue
                    # Look for a button or link inside this cell
                    for probe in ("button", "a", "[role='button']"):
                        inner = cell.locator(probe)
                        if await inner.count() > 0:
                            btn = inner.first
                            el_id = (await btn.get_attribute("id") or "").strip()
                            aria = (await btn.get_attribute("aria-label") or "").strip()
                            title_a = (await btn.get_attribute("title") or "").strip()
                            if el_id:
                                sel = f"[id='{self._css_escape(el_id)}']"
                            elif aria:
                                sel = f"[role='gridcell'][data-field='{field}'] {probe}[aria-label='{self._css_escape(aria)}']"
                            elif title_a:
                                sel = f"[role='gridcell'][data-field='{field}'] {probe}[title='{self._css_escape(title_a)}']"
                            else:
                                sel = f"[role='gridcell'][data-field='{field}'] {probe}"
                            print(f"[FOUND] {semantic_name} via DataGrid action cell (data-field='{field}') -> {sel}")
                            return {"strategy": "css", "value": sel}
            except Exception:
                pass

        # 6️⃣ Nothing found
        print(f"[NOT FOUND] No locator discovered for: {semantic_name}")
        return None

    # ------------------------------------------------------------------
    # Context-Anchored Discovery
    # ------------------------------------------------------------------

    async def find_element_near_anchor(
        self, semantic_name: str, anchor_text: str
    ) -> Optional[Dict]:
        """
        Find an element (described by semantic_name) that is associated with
        a known anchor element identified by anchor_text.

        Works generically across all layout patterns:
          - Table rows        (tr, [role='row'])
          - List items        (li, [role='listitem'], [role='option'], [role='treeitem'])
          - Cards / tiles     (div[class*='card'], div[class*='tile'])
          - Named div rows    (div[class*='row'], div[class*='item'], div[class*='entry'])
          - ARIA grid cells   ([role='gridcell'] → nearest [role='row'])
          - Form fieldsets    (fieldset)
          - Semantic sections (section, article)

        Within each matching container the method searches for the target element
        using a priority-ordered probe list and a semantic scoring pass.

        Falls back to the global find_element() scan when no container matches.
        """
        safe_anchor = self._css_escape(anchor_text)

        # Ordered from tightest/most-specific to most-general containers.
        CONTAINER_SELECTORS: List[str] = [
            # Column-header cells — must come before 'tr' so the tight
            # <th> or [role='columnheader'] is preferred over the whole header row.
            "th",
            "[role='columnheader']",
            # Data grid cells — tighter than a full row; useful for finding
            # action icons anchored to a specific cell value (e.g. "PATHWAY").
            "[role='gridcell']",
            "tr",
            "li",
            "[role='row']",
            "[role='listitem']",
            "[role='option']",
            "[role='treeitem']",
            "[role='menuitem']",
            "[role='tab']",
            "div[class*='row']",
            "div[class*='item']",
            "div[class*='card']",
            "div[class*='entry']",
            "div[class*='record']",
            "div[class*='tile']",
            "div[class*='panel']",
            "div[class*='list-group']",
            "fieldset",
            "section",
            "article",
        ]

        for container_sel in CONTAINER_SELECTORS:
            try:
                # Primary: visible text match (works for most elements)
                containers = self.page.locator(
                    f"{container_sel}:has-text('{safe_anchor}')"
                )
                c_count = await containers.count()

                # Fallback: MUI DataGrid / truncated cells render values as
                # title/aria-label attributes rather than visible text.
                # Try attribute-based containment when text match finds nothing.
                if c_count == 0:
                    for attr in ("title", "aria-label"):
                        alt = self.page.locator(
                            f"{container_sel}:has([{attr}='{safe_anchor}'])"
                        )
                        alt_count = await alt.count()
                        if alt_count > 0:
                            containers = alt
                            c_count = alt_count
                            break

                if c_count == 0:
                    continue

                # Pick the tightest container: the one whose full text is shortest
                # while still containing the anchor.  This avoids outer wrappers.
                best_container = None
                best_len = float("inf")
                for i in range(c_count):
                    c = containers.nth(i)
                    try:
                        txt = await c.inner_text()
                        anchor_norm = self.normalize(anchor_text)
                        if anchor_norm in self.normalize(txt) and len(txt) < best_len:
                            best_len = len(txt)
                            best_container = c
                    except Exception:
                        pass

                if best_container is None:
                    best_container = containers.first

                # Initial scan: require a semantic score > 0 so that always-visible
                # sibling elements (e.g. the Sort button in a column header) don't
                # pre-empt hover-revealed targets (e.g. the column menu icon).
                result = await self._find_target_in_container(
                    best_container, semantic_name, container_sel, safe_anchor,
                    require_score=True,
                )
                if result:
                    return result

                # Nothing found — the target may only appear after hover
                # (e.g. column-menu icon hidden via CSS until parent is hovered).
                # Hover the container, wait for React/CSS to reveal the element,
                # then re-scan. Tag the result so click_and_wait re-hovers first.
                try:
                    await best_container.hover()
                    await self.page.wait_for_timeout(300)
                    # Hover scan: allow single-element heuristic — any newly
                    # interactive element in this container is the intended target.
                    result = await self._find_target_in_container(
                        best_container, semantic_name, container_sel, safe_anchor,
                        require_score=False,
                    )
                    if result:
                        hover_sel = f"{container_sel}:has-text('{anchor_text}')"
                        result["hover_before"] = hover_sel
                        print(
                            f"[HOVER-REVEAL] '{semantic_name}' found after hovering "
                            f"'{hover_sel}' — tagged for pre-click hover"
                        )
                        return result
                except Exception:
                    pass  # hover attempt failed — fall through to global scan

            except Exception as e:
                print(f"[WARN] Container scan failed for '{container_sel}': {e}")
                continue

        # Nothing found via container scoping — degrade gracefully to global scan.
        print(
            f"[ANCHOR FALLBACK] No container match for anchor '{anchor_text}'. "
            "Falling back to global element scan."
        )
        return await self.find_element(semantic_name)

    async def _find_target_in_container(
        self,
        container,
        semantic_name: str,
        container_sel: str,
        safe_anchor: str,
        require_score: bool = False,
    ) -> Optional[Dict]:
        """
        Search for the target element inside a scoped container locator.

        Probe order (most specific first):
          1. Buttons (catches <button><svg/>, icon buttons, text buttons)
          2. Anchor links
          3. Submit / button type inputs
          4. Checkboxes and radio buttons
          5. Text / number / date inputs and textareas
          6. Select dropdowns
          7. ARIA-role interactive elements
          8. Any keyboard-focusable element ([tabindex='0'])
        """
        PROBE_SELECTORS: List[str] = [
            "button",
            "a[href]",
            "a",
            "input[type='submit']",
            "input[type='button']",
            "input[type='checkbox']",
            "input[type='radio']",
            "input:not([type='hidden'])",
            "textarea",
            "select",
            "[role='button']",
            "[role='checkbox']",
            "[role='switch']",
            "[role='menuitem']",
            "[role='link']",
            "[tabindex='0']",
        ]

        for probe_sel in PROBE_SELECTORS:
            try:
                els = container.locator(probe_sel)
                el_count = await els.count()
                if el_count == 0:
                    continue

                # Score every element; keep the best match.
                # Skip elements that are not pointer-interactive (e.g. column-menu
                # icons hidden via opacity:0 / pointer-events:none until hover).
                # Those must be discovered via the hover-scan path so hover_before
                # gets set correctly; if we return them here click_and_wait will
                # try to click a non-interactive element and time out.
                best_el, best_score, best_idx = None, -1, 0
                interactive_count = 0
                for i in range(el_count):
                    el = els.nth(i)
                    if not await self._is_pointer_interactive(el):
                        continue
                    interactive_count += 1
                    score = await self._score_element_against_semantic(el, semantic_name)
                    if score > best_score:
                        best_score, best_el, best_idx = score, el, i

                # Accept if:
                #  - There is a semantic score match (always), OR
                #  - Only one interactive element exists AND we're in the hover scan
                #    (require_score=False). In the initial scan (require_score=True)
                #    we insist on a semantic signal so that always-visible siblings
                #    (e.g. Sort buttons) don't block hover-revealed targets from being
                #    discovered on the subsequent hover pass.
                if best_el and (best_score > 0 or (not require_score and interactive_count == 1)):
                    stable = await self._build_stable_container_selector(
                        best_el, container_sel, safe_anchor, probe_sel, best_idx, el_count
                    )
                    if stable:
                        print(
                            f"[FOUND] '{semantic_name}' near '{safe_anchor}' "
                            f"via '{probe_sel}' in '{container_sel}': {stable}"
                        )
                        return {"strategy": "css", "value": stable}

            except Exception:
                continue

        return None

    @staticmethod
    async def _is_pointer_interactive(el) -> bool:
        """
        Return True only if the element can actually receive pointer events right now.

        Filters out elements that are in the DOM but not yet interactive — the most
        common case being MUI/AG-Grid column menu icons that have
        ``opacity: 0; pointer-events: none`` until their parent header is hovered.
        Clicking such elements causes Playwright to wait the full default timeout
        (30 s) before raising an actionability error.
        """
        try:
            return await el.evaluate("""el => {
                const s = window.getComputedStyle(el);
                return (
                    s.pointerEvents !== 'none' &&
                    s.visibility    !== 'hidden' &&
                    s.display       !== 'none' &&
                    parseFloat(s.opacity) > 0
                );
            }""")
        except Exception:
            return True  # assume interactive when the check itself fails

    async def _score_element_against_semantic(self, el, semantic_name: str) -> int:
        """
        Score how well an element's attributes match the semantic_name.

        Scoring weights:
          +3 — exact attribute match (aria-label, title, id, name, data-testid)
          +2 — token match on a high-signal attribute
          +1 — token match on class name (lower signal, high false-positive risk)

        Returns 0 when no signal is found (element is still usable if it's the
        only candidate of its type in the container).
        """
        score = 0
        try:
            high_signal_attrs = [
                await el.get_attribute("aria-label") or "",
                await el.get_attribute("title") or "",
                await el.get_attribute("id") or "",
                await el.get_attribute("name") or "",
                await el.get_attribute("data-testid") or "",
                await el.get_attribute("data-test") or "",
                await el.get_attribute("data-cy") or "",
                # Tooltip attributes — collapsed sidebars and icon-only buttons
                # often carry their human-readable label only here.
                await el.get_attribute("data-tooltip") or "",
                await el.get_attribute("data-tip") or "",
                await el.get_attribute("data-title") or "",
                await el.get_attribute("data-original-title") or "",
            ]
            text_content = ""
            try:
                # text_content() picks up visually-hidden spans (sr-only labels)
                # that inner_text() misses because it respects CSS display:none.
                text_content = (await el.text_content() or "").strip()
            except Exception:
                pass

            # Exact match on any high-signal attribute → heavy bonus
            for attr_val in high_signal_attrs:
                if not attr_val:
                    continue
                if self.normalize(attr_val) == self.normalize(semantic_name):
                    score += 3
                elif self.tokens_match(semantic_name, attr_val, min_matches=1):
                    score += 2

            # Text content match
            if text_content and self.tokens_match(semantic_name, text_content, min_matches=1):
                score += 2

            # Class name match (lower confidence)
            class_attr = await el.get_attribute("class") or ""
            if class_attr and self.tokens_match(semantic_name, class_attr, min_matches=1):
                score += 1

        except Exception:
            pass

        return score

    async def _build_stable_container_selector(
        self,
        el,
        container_sel: str,
        safe_anchor: str,
        probe_sel: str,
        idx: int,
        total: int,
    ) -> Optional[str]:
        """
        Build the most stable CSS selector for an element inside an anchor container.

        Preference order (most → least stable):
          1. Global unique id          → #element-id
          2. aria-label                → container:has-text('anchor') probe[aria-label='...']
          3. data-testid / data-test   → container:has-text('anchor') [data-testid='...']
          4. title attribute           → container:has-text('anchor') probe[title='...']
          5. name attribute            → container:has-text('anchor') probe[name='...']
          6. Only child of type        → container:has-text('anchor') probe
          7. nth-of-type position      → container:has-text('anchor') probe:nth-of-type(n)
        """
        try:
            el_id = (await el.get_attribute("id") or "").strip()
            aria_label = (await el.get_attribute("aria-label") or "").strip()
            data_testid = (
                await el.get_attribute("data-testid")
                or await el.get_attribute("data-test")
                or await el.get_attribute("data-cy")
                or ""
            ).strip()
            title = (await el.get_attribute("title") or "").strip()
            name = (await el.get_attribute("name") or "").strip()

            base = f"{container_sel}:has-text('{safe_anchor}')"

            if el_id:
                # Use attribute selector instead of #id — handles React/framework IDs
                # like ":r1t:" that contain CSS pseudo-class characters.
                return f"[id='{self._css_escape(el_id)}']"

            if aria_label:
                return f"{base} {probe_sel}[aria-label='{self._css_escape(aria_label)}']"

            if data_testid:
                return f"{base} [data-testid='{self._css_escape(data_testid)}']"

            if title:
                return f"{base} {probe_sel}[title='{self._css_escape(title)}']"

            if name:
                return f"{base} {probe_sel}[name='{self._css_escape(name)}']"

            if total == 1:
                return f"{base} {probe_sel}"

            # Last resort: positional — still scoped to the anchor row/container
            return f"{base} {probe_sel}:nth-of-type({idx + 1})"

        except Exception:
            return None