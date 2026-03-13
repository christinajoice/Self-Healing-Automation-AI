"""
AI-powered locator suggestion using a local Ollama model (free, no subscription).

Fallback chain:
  DOMScanner (rule-based) → AILocatorSuggester (LLM) → None

Setup:
  1. Install Ollama: https://ollama.com
  2. Pull a model: ollama pull llama3.2
  3. Start Ollama: ollama serve
  4. Set AI_MODEL in .env (default: llama3.2)
"""

import json
import os
import re
import requests
from playwright.async_api import Page
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

AI_BASE_URL = os.getenv("AI_BASE_URL", "http://localhost:11434/v1")
AI_MODEL = os.getenv("AI_MODEL", "llama3.2")
AI_API_KEY = os.getenv("AI_API_KEY", "ollama")  # Ollama ignores this but the client needs it


class AILocatorSuggester:
    """
    Uses a local LLM (via Ollama) to find a CSS/text locator for a semantic name.
    Extracts a compact DOM snapshot and asks the model to return a JSON locator dict.
    """

    def __init__(self, page: Page):
        self.page = page

    async def suggest(self, semantic_name: str) -> Optional[dict]:
        """
        Ask the LLM to find the best locator for the given semantic name.
        Returns {"strategy": ..., "value": ...} or None.
        """
        try:
            dom_snapshot = await self._extract_dom_snapshot()
            if not dom_snapshot:
                print("[AI] DOM snapshot empty, skipping AI suggestion")
                return None

            prompt = self._build_prompt(semantic_name, dom_snapshot)
            raw = self._call_llm(prompt)
            result = self._parse_response(raw)

            if result:
                print(f"[AI] Suggested locator for '{semantic_name}': {result}")
            else:
                print(f"[AI] No locator found for '{semantic_name}'")

            return result

        except requests.exceptions.ConnectionError:
            print("[AI] Ollama not running. Start it with: ollama serve")
            return None
        except Exception as e:
            print(f"[AI] Suggestion failed: {e}")
            return None

    async def _extract_dom_snapshot(self) -> list:
        """
        Extract a compact list of interactive elements from the page DOM.
        Capped at 60 elements to stay within model context limits.
        """
        return await self.page.evaluate("""() => {
            const elements = [];
            const selectors = [
                'input', 'button', 'a', 'select', 'textarea', 'label',
                '[role="button"]', '[role="textbox"]', '[role="link"]'
            ].join(', ');

            document.querySelectorAll(selectors).forEach(el => {
                const entry = {
                    tag:         el.tagName.toLowerCase(),
                    id:          el.id || null,
                    name:        el.getAttribute('name') || null,
                    type:        el.getAttribute('type') || null,
                    placeholder: el.getAttribute('placeholder') || null,
                    text:        (el.innerText || '').trim().slice(0, 80) || null,
                    for:         el.getAttribute('for') || null,
                    ariaLabel:   el.getAttribute('aria-label') || null,
                    class:       (el.className || '').split(' ').slice(0, 3).join(' ') || null,
                };
                // Only include elements with at least one identifying attribute
                const hasIdentifier = Object.entries(entry)
                    .some(([k, v]) => k !== 'tag' && v && v.trim() !== '');
                if (hasIdentifier) {
                    elements.push(entry);
                }
            });

            return elements.slice(0, 60);
        }""")

    def _build_prompt(self, semantic_name: str, dom_elements: list) -> str:
        return f"""You are a test automation expert. Your task is to find the best Playwright locator for a web element.

Semantic name to find: "{semantic_name}"

Interactive DOM elements on the page:
{json.dumps(dom_elements, indent=2)}

Rules:
- Prefer id-based selectors: {{"strategy": "css", "value": "#element-id"}}
- Use name-based selectors: {{"strategy": "css", "value": "input[name='fieldname']"}}
- Use placeholder: {{"strategy": "get_by_placeholder", "value": "placeholder text"}}
- Use visible text for buttons/links: {{"strategy": "get_by_text", "value": "Button Text"}}
- Return null if no suitable element found

Respond with ONLY a valid JSON object or the word null. No explanation, no markdown, no code blocks.

Examples:
{{"strategy": "css", "value": "#username"}}
{{"strategy": "get_by_placeholder", "value": "Enter your email"}}
null

Your response:"""

    def _call_llm(self, prompt: str) -> str:
        """Call the Ollama OpenAI-compatible API."""
        response = requests.post(
            f"{AI_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {AI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": AI_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "max_tokens": 128,
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"].strip()

    def _parse_response(self, text: str) -> Optional[dict]:
        """Parse the LLM response into a locator dict."""
        text = text.strip()

        if text.lower() in ("null", "none", ""):
            return None

        # Strip markdown code blocks if model added them
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()

        try:
            result = json.loads(text)
            if isinstance(result, dict) and "strategy" in result and "value" in result:
                return result
        except json.JSONDecodeError:
            # Try to extract JSON object from mixed text
            match = re.search(r'\{[^{}]+\}', text)
            if match:
                try:
                    result = json.loads(match.group())
                    if "strategy" in result and "value" in result:
                        return result
                except json.JSONDecodeError:
                    pass

        print(f"[AI] Unparseable response: {text[:100]}")
        return None
