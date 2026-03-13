"""
Quick smoke-test for DOMScanner.find_element
Navigates to a real login page and tries to discover common elements by semantic name.
"""
import asyncio
from playwright.async_api import async_playwright
from core.discovery.dom_scanner import DOMScanner

TEST_URL = "https://the-internet.herokuapp.com/login"
SEMANTIC_NAMES = ["username", "password", "login"]


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()

        print(f"\nNavigating to {TEST_URL}\n")
        await page.goto(TEST_URL)
        await page.wait_for_load_state("domcontentloaded")

        scanner = DOMScanner(page)

        for name in SEMANTIC_NAMES:
            print(f"\nSearching for: '{name}'")
            result = await scanner.find_element(name)
            if result:
                print(f"   strategy: {result['strategy']}, value: {result['value']}")
            else:
                print(f"   NOT FOUND")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
