from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    page = browser.new_page()
    page.goto("https://example.com")
    print("Page title:", page.title())
    page.wait_for_timeout(3000)  # keep browser open for 3 seconds
    browser.close()
