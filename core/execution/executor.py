# test_executor.py
from playwright.async_api import async_playwright, TimeoutError
from typing import Dict, Optional
from datetime import datetime
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
        results = {
            "testcase_id": testcase["testcase_id"],
            "status": "PASS",
            "start_time": datetime.utcnow().isoformat(),
            "end_time": None,
            "steps": [],
            "error": None,
        }

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=self.headless)
                context = await browser.new_context()
                page = await context.new_page()

                for idx, step in enumerate(testcase["steps"], start=1):
                    step_id = step.get("id") or self._step_fingerprint(step)
                    if step_id in self._executed_steps:
                        print(f"⏭ Step {step_id} already executed, skipping")
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
                        "timestamp": datetime.utcnow().isoformat(),
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
                            healed=True,
                            soft_fail=False,
                            passed=False,
                        )
                        results["status"] = "FAIL"
                        results["error"] = str(e)

                    results["steps"].append(step_result)

                await browser.close()

        except Exception as e:
            results["status"] = "FAIL"
            results["error"] = str(e)

        results["end_time"] = datetime.utcnow().isoformat()
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

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                # Only resolve locator on first attempt or if previous healing occurred
                if action in ("click", "enter", "verify") or (
                    action == "validate" and intent == "locator"
                ):
                    if attempt == 1 or healed or locator_meta is None:
                        locator_meta = await discovery.resolve(target)
                    locator = discovery._build_locator(locator_meta)

                pre_url = page.url
                pre_dom = await page.content()

                if action == "open":
                    await page.goto(base_url)
                    await page.wait_for_load_state("domcontentloaded")
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
                    value = credentials.get(data, data) if credentials else data
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