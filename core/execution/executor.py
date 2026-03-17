# test_executor.py
from playwright.async_api import async_playwright, TimeoutError
from typing import Dict, Optional
from datetime import datetime, timezone
import json
import os
import hashlib
import asyncio
from threading import Lock

from core.discovery.locator_discovery import LocatorDiscovery
from core.reporting.report_generator import ReportGenerator
from core.intent.intent_classifier import IntentClassifier

LEARNING_STORE = "learning/step_memory.json"


class TestExecutor:
    """
    Async-first executor
    SAFE for:
    - FastAPI
    - asyncio
    - UI triggers
    """

    _learning_lock = Lock()

    def __init__(self, headless: bool = True):
        self.headless = headless
        self.reporter = ReportGenerator()
        self._load_learning_store()
        self._executed_steps = set()  # Track executed steps to prevent duplicates

    # ------------------------------------------------------------------
    # 🔹 Learning Store
    # ------------------------------------------------------------------
    def _load_learning_store(self):
        os.makedirs("learning", exist_ok=True)
        if os.path.exists(LEARNING_STORE):
            with open(LEARNING_STORE, "r") as f:
                self.learning = json.load(f)
        else:
            self.learning = {}

    def _save_learning_store(self):
        with self._learning_lock:
            with open(LEARNING_STORE, "w") as f:
                json.dump(self.learning, f, indent=2)

    def _step_fingerprint(self, step: Dict) -> str:
        raw = f"{step.get('action')}|{step.get('target')}|{step.get('data')}"
        return hashlib.md5(raw.encode()).hexdigest()

    # ------------------------------------------------------------------
    # 🔹 PUBLIC ENTRY POINT
    # ------------------------------------------------------------------
    async def run_testcase(
        self,
        testcase: Dict,
        base_url: str,
        credentials: Optional[Dict] = None,
    ):
        # Reset per-run state so re-submitting the same test case without
        # restarting the server always executes all steps fresh.
        self._executed_steps = set()
        self._load_learning_store()

        results = {
            "testcase_id": testcase["testcase_id"],
            "status": "PASS",
            "start_time": datetime.now(timezone.utc).isoformat(),
            "end_time": None,
            "steps": [],
            "error": None,
        }

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=self.headless,
                    args=["--ignore-certificate-errors", "--disable-web-security"],
                )
                context = await browser.new_context(ignore_https_errors=True)
                page = await context.new_page()

                for idx, step in enumerate(testcase["steps"], start=1):
                    step_id = step.get("id") or self._step_fingerprint(step)
                    if step_id in self._executed_steps:
                        print(f"[SKIP] Step {step_id} already executed, skipping")
                        continue

                    fingerprint = self._step_fingerprint(step)
                    learned = self.learning.get(fingerprint, {})

                    confidence = step.get(
                        "confidence",
                        learned.get("recommended_confidence", "high"),
                    )

                    step_result = {
                        "step": idx,
                        "action": step["action"],
                        "target": step.get("target"),
                        "data": step.get("data"),
                        "confidence": confidence,
                        "status": "PASS",
                        "healed": False,
                        "error": None,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }

                    try:
                        healed = await self.execute_step(
                            page,
                            step,
                            base_url,
                            confidence,
                            credentials,
                        )

                        step_result["healed"] = healed
                        self._executed_steps.add(step_id)

                        self._record_learning(
                            fingerprint,
                            healed=healed,
                            soft_fail=False,
                            passed=True,
                        )

                    except Exception as e:
                        step_result["status"] = "FAIL"
                        step_result["error"] = str(e)
                        self._record_learning(
                            fingerprint,
                            healed=False,
                            soft_fail=False,
                            passed=False,
                        )
                        results["status"] = "FAIL"
                        results["error"] = str(e)
                        results["failed_step"] = step_result

                    results["steps"].append(step_result)

                await browser.close()

        except Exception as e:
            results["status"] = "FAIL"
            results["error"] = str(e)

        results["end_time"] = datetime.now(timezone.utc).isoformat()
        self.reporter.generate(results)
        self._save_learning_store()

        return results

    # ------------------------------------------------------------------
    # 🔹 STEP EXECUTION
    # ------------------------------------------------------------------
    async def execute_step(
        self,
        page,
        step: Dict,
        base_url: str,
        confidence: str,
        credentials: Optional[Dict],
    ) -> bool:

        action = step["action"].lower()
        target = step.get("target", "")
        data = step.get("data", "")
        healed = False
        navigates = step.get("navigates", False)
        wait_for_selector = step.get("wait_for")

        MAX_RETRIES = 3
        WAIT = 0.8

        discovery = LocatorDiscovery(page, base_url)
        intent = IntentClassifier.classify(action, target, data)

        locator_meta = None
        locator = None

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                # Only resolve locator on first attempt or if previous healing occurred.
                #
                # Context-anchored resolution:
                #   When the ``data`` field is set on a non-``open`` action it is
                #   treated as an *anchor text* — a piece of content that uniquely
                #   identifies the surrounding container (e.g. a table-row value,
                #   a list-item label, a card title).  The resolver will scope its
                #   DOM search to the element closest to that anchor rather than
                #   scanning the whole page.
                #
                #   Exception: ``enter`` uses ``data`` as the text to type, so the
                #   anchor context is encoded in the target name itself for that
                #   action (e.g. target = "quantity input in Cart row").
                #
                # Examples:
                #   click  | action icon      | PATHWAY       → clicks icon in PATHWAY row
                #   click  | delete button    | John Doe      → clicks delete in John Doe list item
                #   click  | edit icon        | Account Card  → clicks edit inside Account Card
                #   verify | status badge     | Order #1042   → verifies badge in that order row
                needs_locator = action in ("click", "enter", "verify") or (
                    action == "validate" and intent == "locator"
                )
                uses_data_as_anchor = (
                    action in ("click", "verify")
                    or (action == "validate" and intent == "locator")
                )

                if needs_locator:
                    if attempt == 1 or healed or locator_meta is None:
                        context = (data or "").strip()
                        if context and uses_data_as_anchor:
                            locator_meta = await discovery.resolve_with_context(target, context)
                        else:
                            locator_meta = await discovery.resolve(target)
                    locator = discovery._build_locator(locator_meta)

                pre_url = page.url
                pre_dom = await page.content()

                if action == "open":
                    # URL resolution priority:
                    # 1. data column has a full URL → use it (e.g., open,login page,https://app.com/login)
                    # 2. target has a full URL      → use target directly
                    # 3. target is a path (/login)  → base_url + path
                    # 4. anything else              → use base_url from the form (original behavior)
                    data_val = (data or "").strip()
                    if data_val.startswith(("http://", "https://")):
                        nav_url = data_val
                    elif target and target.strip().startswith(("http://", "https://")):
                        nav_url = target.strip()
                    elif target and target.strip().startswith("/"):
                        nav_url = base_url.rstrip("/") + target.strip()
                    else:
                        nav_url = base_url
                    print(f"[OPEN] Navigating to: {nav_url}")
                    try:
                        # First attempt: fast load (works for server-rendered apps)
                        await page.goto(nav_url, timeout=30000, wait_until="domcontentloaded")
                        await page.wait_for_load_state("domcontentloaded", timeout=15000)
                    except Exception:
                        # Second attempt: networkidle (needed for SPAs like React/Angular/Vue)
                        print(f"[OPEN] domcontentloaded failed, retrying with networkidle...")
                        await page.goto(nav_url, timeout=45000, wait_until="networkidle")
                    print(f"[OPEN] Page loaded. Title: {await page.title()}")
                    return healed

                elif action == "click":
                    await discovery.click_and_wait(
                        locator_meta,
                        wait_for_selector=wait_for_selector,
                        navigates=navigates
                    )
                    await self._post_action_check(
                        page, pre_url, pre_dom, confidence, navigates=navigates
                    )

                elif action == "enter":
                    await locator.wait_for(state="visible", timeout=5000)
                    if credentials:
                        key = (data or "").strip().lower()
                        value = (
                            credentials.get(data)
                            or credentials.get(key)
                            or credentials.get(key.split()[0] if key else "", data)
                            or data
                        )
                    else:
                        value = data
                    await locator.fill(value)

                elif action == "verify":
                    await locator.wait_for(state="visible", timeout=5000)
                    text = await locator.inner_text(timeout=3000)
                    if data not in text:
                        raise AssertionError("Verification failed")

                elif action == "validate":
                    valid = await self._handle_validation(
                        page, intent, target, data, locator, confidence
                    )
                    if not valid:
                        healed = True

                else:
                    raise ValueError(f"Unsupported action: {action}")

                return healed

            except Exception as e:
                healed = True
                if attempt == MAX_RETRIES:
                    raise
                await asyncio.sleep(WAIT)

        return healed

    # ------------------------------------------------------------------
    # 🔹 POST ACTION CHECK
    # ------------------------------------------------------------------
    async def _post_action_check(self, page, pre_url, pre_dom, confidence, navigates=False):
        try:
            await page.wait_for_load_state("networkidle", timeout=5000)
            if not navigates and page.url == pre_url and await page.content() == pre_dom:
                raise AssertionError("No observable change after CTA")
        except Exception:
            if confidence != "high":
                return
            raise

    # ------------------------------------------------------------------
    # 🔹 VALIDATION ENGINE
    # ------------------------------------------------------------------
    async def _handle_validation(
        self, page, intent, target, data, locator, confidence
    ) -> bool:
        try:
            if intent == "message":
                assert data.lower() in (await page.content()).lower()
            elif intent == "url":
                assert page.url
            elif intent == "locator":
                await locator.wait_for(state="visible", timeout=5000)
            return True
        except Exception:
            if confidence == "low":
                return False
            raise

    # ------------------------------------------------------------------
    # 🔹 LEARNING ENGINE
    # ------------------------------------------------------------------
    def _record_learning(self, fingerprint, healed, soft_fail, passed):
        entry = self.learning.get(
            fingerprint,
            {
                "runs": 0,
                "healed": 0,
                "soft_fail": 0,
                "pass": 0,
                "fail": 0,
                "recommended_confidence": "high",
            },
        )

        entry["runs"] += 1
        entry["healed"] += int(healed)
        entry["soft_fail"] += int(soft_fail)
        entry["pass"] += int(passed)
        entry["fail"] += int(not passed)

        if entry["soft_fail"] > 2 or entry["healed"] > 2:
            entry["recommended_confidence"] = "low"
        elif entry["fail"] > 0:
            entry["recommended_confidence"] = "medium"

        self.learning[fingerprint] = entry