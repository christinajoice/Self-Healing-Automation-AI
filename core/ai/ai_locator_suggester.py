from playwright.async_api import Page, Locator
from typing import Optional


class AILocatorSuggester:
    """
    AI-driven locator suggestion (conceptual).
    Async-safe version compatible with FastAPI + Playwright async executor.
    """

    def __init__(self, page: Page, action: str, target: str, data: str):
        self.page = page
        self.action = action
        self.target = target
        self.data = data

    async def suggest(self) -> Optional[Locator]:
        """
        Conceptual AI suggestion flow:
        1. Extract visible elements from the page.
        2. Compute semantic similarity of element text/attributes with 'target'.
        3. Rank suggestions and return the most probable Playwright locator.
        """

        all_elements = self.page.locator("body *")
        max_count = await all_elements.count()
        candidates = []

        for i in range(max_count):
            try:
                el = all_elements.nth(i)
                text = (await el.inner_text()).strip().lower()

                if self.target.lower() in text:
                    candidates.append(el)

            except Exception:
                # ignore detached / invisible / transient elements
                continue

        if candidates:
            return candidates[0]

        return None
