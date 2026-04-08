# test_executor.py
from playwright.async_api import async_playwright, TimeoutError
from typing import Dict, Optional
from datetime import datetime, timezone
import json
import os
import hashlib
import asyncio
from threading import Lock
from pathlib import Path

from core.discovery.locator_discovery import LocatorDiscovery
from core.reporting.report_generator import ReportGenerator
from core.intent.intent_classifier import IntentClassifier
from core.db.profile_loader import get_profile
from core.db.connector import get_connector
from core.db.query_runner import run_query
from core.validation.data_validator import extract_ui_table, compare as validate_compare

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
        self._executed_steps = set()
        self._db_profile_name: str | None = None   # set per execution run
        self._db_connector = None                   # lazily created, reused across steps

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
            import tempfile
            path = Path(LEARNING_STORE)
            fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(self.learning, f, indent=2)
                os.replace(tmp_path, path)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

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
        cancel_flag=None,
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
                    # Check for cancellation before each step
                    if cancel_flag and cancel_flag.is_set():
                        print(f"[CANCELLED] Stop requested — skipping remaining steps from step {idx}")
                        remaining = testcase["steps"][idx - 1:]
                        for r_idx, r_step in enumerate(remaining, start=idx):
                            results["steps"].append({
                                "step": r_idx,
                                "action": r_step["action"],
                                "target": r_step.get("target"),
                                "data": r_step.get("data"),
                                "confidence": r_step.get("confidence", "high"),
                                "status": "SKIPPED",
                                "healed": False,
                                "error": "Skipped: execution cancelled by user",
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            })
                        results["status"] = "CANCELLED"
                        break

                    # Use step index as the within-run unique ID so that two steps
                    # with identical action/target/data (e.g. two "click apply" steps
                    # that apply different column filters) are never skipped.
                    step_id = step.get("id") or idx
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

                    if step_result["status"] == "FAIL" and confidence == "high":
                        print(
                            f"[SKIP] High-confidence step {idx} failed — skipping remaining steps in this test case"
                        )
                        remaining = testcase["steps"][idx:]  # idx is 1-based, so this is everything after current
                        for r_idx, r_step in enumerate(remaining, start=idx + 1):
                            results["steps"].append(
                                {
                                    "step": r_idx,
                                    "action": r_step["action"],
                                    "target": r_step.get("target"),
                                    "data": r_step.get("data"),
                                    "confidence": r_step.get("confidence", "high"),
                                    "status": "SKIPPED",
                                    "healed": False,
                                    "error": f"Skipped: step {idx} failed with high confidence",
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                }
                            )
                        break

                await browser.close()

        except Exception as e:
            results["status"] = "FAIL"
            results["error"] = str(e)

        results["end_time"] = datetime.now(timezone.utc).isoformat()
        self.reporter.generate(results)
        self._save_learning_store()

        return results

    # ------------------------------------------------------------------
    # 🔹 MULTI-TESTCASE RUNNER (single browser session)
    # ------------------------------------------------------------------
    async def run_all_testcases(
        self,
        testcases: list,
        base_url: str,
        credentials: Optional[Dict] = None,
        cancel_flag=None,
        db_profile: str | None = None,
    ) -> list:
        """
        Run a list of test cases in ONE shared browser session so that login
        state (cookies, session tokens) carries through from TC to TC.
        Generates a single combined HTML/JSON report at the end.
        """
        self._load_learning_store()
        self._db_profile_name = db_profile
        self._db_connector = None   # reset; will be lazily opened on first db_data step
        all_results = []

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=self.headless,
                    args=["--ignore-certificate-errors", "--disable-web-security"],
                )
                context = await browser.new_context(ignore_https_errors=True)
                page = await context.new_page()

                for testcase in testcases:
                    if cancel_flag and cancel_flag.is_set():
                        break
                    self._executed_steps = set()
                    tc_results = await self._execute_on_page(
                        page, testcase, base_url, credentials, cancel_flag
                    )
                    self.reporter.log_testcase(tc_results)
                    all_results.append(tc_results)

                    # If this TC failed on a high-confidence step, skip all
                    # subsequent TCs — they share the same browser session and
                    # are almost certainly in an unusable state (e.g. not logged in).
                    if tc_results.get("status") == "FAIL":
                        remaining_tcs = testcases[testcases.index(testcase) + 1:]
                        for skipped_tc in remaining_tcs:
                            skipped_result = {
                                "testcase_id": skipped_tc["testcase_id"],
                                "status": "SKIPPED",
                                "start_time": datetime.now(timezone.utc).isoformat(),
                                "end_time": datetime.now(timezone.utc).isoformat(),
                                "steps": [
                                    {
                                        "step": s.get("step", i + 1),
                                        "action": s["action"],
                                        "target": s.get("target"),
                                        "data": s.get("data"),
                                        "confidence": s.get("confidence", "high"),
                                        "status": "SKIPPED",
                                        "healed": False,
                                        "error": f"Skipped: {tc_results['testcase_id']} failed",
                                        "timestamp": datetime.now(timezone.utc).isoformat(),
                                    }
                                    for i, s in enumerate(skipped_tc.get("steps", []))
                                ],
                                "error": f"Skipped: {tc_results['testcase_id']} failed",
                            }
                            self.reporter.log_testcase(skipped_result)
                            all_results.append(skipped_result)
                            print(f"[SKIP] {skipped_tc['testcase_id']} skipped — {tc_results['testcase_id']} failed")
                        break

                await browser.close()

        except Exception as e:
            print(f"[ERROR] Browser session failed: {e}")
            if all_results and all_results[-1].get("end_time") is None:
                all_results[-1]["status"] = "FAIL"
                all_results[-1]["error"] = str(e)
                all_results[-1]["end_time"] = datetime.now(timezone.utc).isoformat()

        finally:
            # Close DB connection pool if one was opened during this run
            if self._db_connector is not None:
                try:
                    self._db_connector.close()
                except Exception:
                    pass
                self._db_connector = None

        # Write ONE combined report covering every test case
        self.reporter.generate_json()
        self.reporter.generate_html()
        self._save_learning_store()
        return all_results

    async def _execute_on_page(
        self,
        page,
        testcase: Dict,
        base_url: str,
        credentials: Optional[Dict],
        cancel_flag,
    ) -> Dict:
        """Execute all steps of one test case on an already-open browser page."""
        results = {
            "testcase_id": testcase["testcase_id"],
            "status": "PASS",
            "start_time": datetime.now(timezone.utc).isoformat(),
            "end_time": None,
            "steps": [],
            "error": None,
        }

        for idx, step in enumerate(testcase["steps"], start=1):
            if cancel_flag and cancel_flag.is_set():
                remaining = testcase["steps"][idx - 1:]
                for r_idx, r_step in enumerate(remaining, start=idx):
                    results["steps"].append({
                        "step": r_idx,
                        "action": r_step["action"],
                        "target": r_step.get("target"),
                        "data": r_step.get("data"),
                        "confidence": r_step.get("confidence", "high"),
                        "status": "SKIPPED",
                        "healed": False,
                        "error": "Skipped: execution cancelled by user",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
                results["status"] = "CANCELLED"
                break

            step_id = step.get("id") or idx
            if step_id in self._executed_steps:
                continue

            fingerprint = self._step_fingerprint(step)
            learned = self.learning.get(fingerprint, {})
            confidence = step.get(
                "confidence", learned.get("recommended_confidence", "high")
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
                    page, step, base_url, confidence, credentials
                )
                step_result["healed"] = healed
                self._executed_steps.add(step_id)
                self._record_learning(fingerprint, healed=healed, soft_fail=False, passed=True)

            except Exception as e:
                step_result["status"] = "FAIL"
                step_result["error"] = str(e)
                self._record_learning(fingerprint, healed=False, soft_fail=False, passed=False)
                results["status"] = "FAIL"
                results["error"] = str(e)
                results["failed_step"] = step_result

            results["steps"].append(step_result)

            if step_result["status"] == "FAIL" and confidence == "high":
                print(f"[SKIP] High-confidence step {idx} failed — skipping remaining steps in {testcase['testcase_id']}")
                remaining = testcase["steps"][idx:]
                for r_idx, r_step in enumerate(remaining, start=idx + 1):
                    results["steps"].append({
                        "step": r_idx,
                        "action": r_step["action"],
                        "target": r_step.get("target"),
                        "data": r_step.get("data"),
                        "confidence": r_step.get("confidence", "high"),
                        "status": "SKIPPED",
                        "healed": False,
                        "error": f"Skipped: step {idx} failed with high confidence",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
                break

        results["end_time"] = datetime.now(timezone.utc).isoformat()
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
                    action == "validate" and intent in ("locator", "count")
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
                    # If multiple elements match (e.g. two filter panels open at once),
                    # use the last one — it is always the most recently opened input.
                    if await locator.count() > 1:
                        locator = locator.last
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
                    # fill() works for most inputs (login, forms).
                    # For React controlled inputs (e.g. MUI filter panels), fill() sets
                    # the DOM value but React's internal state may not update because
                    # onChange is never fired. In that case, fall back to keystroke typing.
                    await locator.fill(value)
                    # Let React process the input event before checking
                    await asyncio.sleep(0.1)
                    actual = await locator.input_value()
                    if actual != value:
                        # React reset the value — DOM matches but state doesn't.
                        # Fall back to keystroke typing which fires per-key events.
                        print(f"[ENTER] fill() not accepted by React input, switching to press_sequentially")
                        await locator.click()
                        await locator.press("Control+a")
                        await locator.press_sequentially(value, delay=30)
                    else:
                        # Also dispatch change event so React's onChange fires even
                        # if it only listens to change (not input) events.
                        await locator.dispatch_event("change")

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
        # First wait — let the initial network burst settle.
        # Use a short timeout: SPAs with background polling never reach true
        # networkidle, so a long timeout just burns time on every action.
        try:
            await page.wait_for_load_state("networkidle", timeout=3000)
        except Exception:
            pass

        try:
            current_url = page.url

            # page.content() can raise a PlaywrightError when called mid-navigation
            # (e.g. immediately after a login button click that triggers an SSO
            # redirect).  Treat a failure here as "DOM changed" so we never raise
            # AssertionError and never cause a spurious step retry.
            try:
                dom_now = await page.content()
            except Exception:
                dom_now = ""  # navigation in progress — assume DOM changed

            if not navigates and current_url == pre_url and dom_now == pre_dom:
                raise AssertionError("No observable change after CTA")

            # URL changed → could be a multi-hop SSO redirect chain.
            # Poll until the URL is stable for two consecutive checks.
            if current_url != pre_url:
                await self._wait_for_url_stability(page, timeout=30)

            # Always wait for loading indicators after every click — covers both
            # same-page actions (filter Apply, tab switch) AND post-navigation
            # data fetches (e.g. progress bar that appears after page load).
            await self._wait_for_loading_done(page)

            # Re-check URL after loading: for login/SSO clicks the 3 s networkidle
            # window expires before the redirect starts, so `current_url` above still
            # shows the login URL.  By the time `_wait_for_loading_done` finishes the
            # redirect has completed but `_wait_for_url_stability` was never called.
            # Detect this late redirect and wait for full page stability now.
            post_load_url = page.url
            if post_load_url != pre_url and post_load_url != current_url:
                print(f"[POST-LOAD] Late redirect detected ({current_url} -> {post_load_url}), waiting for stability...")
                await self._wait_for_url_stability(page, timeout=20)

        except AssertionError:
            if confidence != "high":
                return
            raise
        except Exception as e:
            # Any other Playwright error during post-action checks (e.g. frame
            # detached, navigation interrupted) should NOT cause execute_step to
            # retry the click — the click already fired successfully.  Log and move on.
            print(f"[WARN] post-action check raised unexpected error (ignoring): {e!r}")

    async def _wait_for_loading_done(self, page, timeout: int = 10000):
        """Wait for common loading indicators to disappear after any click action.

        A short initial pause is intentional: progress bars and spinners are
        typically injected into the DOM ~100-300 ms after the triggering click.
        Checking immediately would miss them and race ahead to the next step.
        """
        # Brief pause so React/MUI has time to inject any loading indicator
        await asyncio.sleep(0.3)

        LOADING_SELECTORS = [
            "[role='progressbar']",
            ".MuiLinearProgress-root",
            ".MuiCircularProgress-root",
            # Use specific class names rather than [class*='loading'] — that wildcard
            # matches permanent wrapper elements (e.g. "data-loading-container") and
            # causes a spurious 10 s timeout on every click action.
            ".loading-overlay",
            ".loading-spinner",
            "[data-loading='true']",
            "[class*='skeleton']",
        ]
        for sel in LOADING_SELECTORS:
            try:
                if await page.locator(sel).count() > 0:
                    print(f"[WAIT] Loading indicator detected ({sel}), waiting for it to clear...")
                    await page.wait_for_selector(sel, state="hidden", timeout=timeout)
                    print(f"[WAIT] Loading indicator cleared.")
                    break
            except Exception:
                pass
        # Minimum settle time — lets React finish re-rendering after data loads
        await asyncio.sleep(0.3)

    async def _wait_for_url_stability(self, page, timeout: int = 30):
        """Wait until the page URL stops changing (all redirects complete).
        Requires two consecutive stable readings to avoid declaring stable
        mid-redirect during multi-hop SSO flows.
        """
        import time
        deadline = time.monotonic() + timeout
        prev_url = page.url
        stable_count = 0
        await asyncio.sleep(0.5)
        while time.monotonic() < deadline:
            try:
                curr_url = page.url
            except Exception:
                break
            if curr_url == prev_url:
                stable_count += 1
                if stable_count >= 2:
                    # URL stable for two consecutive checks — wait for page load
                    try:
                        await page.wait_for_load_state("domcontentloaded", timeout=8000)
                    except Exception:
                        pass
                    try:
                        await page.wait_for_load_state("networkidle", timeout=4000)
                    except Exception:
                        pass
                    return
            else:
                stable_count = 0
            prev_url = curr_url
            await asyncio.sleep(0.5)

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
            elif intent == "column_values":
                import re as _re
                # Strip trailing " column" / " col" from target to get the column header name
                col_name = _re.sub(r"\s+col(umn)?\s*$", "", target, flags=_re.IGNORECASE).strip()
                result = await page.evaluate(
                    """([colName, expected]) => {
                        const headers = Array.from(
                            document.querySelectorAll('[role="columnheader"]')
                        );
                        const headerEl = headers.find(
                            h => h.textContent.trim().toLowerCase().includes(colName.toLowerCase())
                        );
                        if (!headerEl) return { found: false, reason: 'column header not found' };

                        let cells = [];

                        // Strategy 1: MUI DataGrid data-field attribute (most reliable)
                        const dataField = headerEl.getAttribute('data-field');
                        if (dataField) {
                            cells = Array.from(document.querySelectorAll(
                                `[data-field="${dataField}"]`
                            )).filter(el => el.getAttribute('role') !== 'columnheader'
                                        && !el.closest('[role="columnheader"]'));
                        }

                        // Strategy 2: aria-colindex on role=cell or role=gridcell
                        if (cells.length === 0) {
                            const colIdx = headerEl.getAttribute('aria-colindex');
                            if (colIdx) {
                                cells = Array.from(document.querySelectorAll(
                                    `[aria-colindex="${colIdx}"]`
                                )).filter(el => el.getAttribute('role') !== 'columnheader'
                                            && !el.closest('[role="columnheader"]'));
                            }
                        }

                        // Strategy 3: positional index across data rows
                        if (cells.length === 0) {
                            const colPos = headers.indexOf(headerEl);
                            if (colPos >= 0) {
                                const dataRows = Array.from(
                                    document.querySelectorAll('[role="row"]')
                                ).filter(r => !r.querySelector('[role="columnheader"]'));
                                cells = dataRows.map(r => {
                                    const c = r.querySelectorAll(
                                        '[role="cell"], [role="gridcell"], .MuiDataGrid-cell'
                                    );
                                    return c[colPos] || null;
                                }).filter(Boolean);
                            }
                        }

                        if (cells.length === 0)
                            return { found: false, reason: 'no data cells found for column' };

                        const failing = cells
                            .map(c => c.textContent.trim())
                            .filter(t => t.length > 0 && !t.includes(expected));

                        return {
                            found: true,
                            total: cells.length,
                            failing: failing
                        };
                    }""",
                    [col_name, data],
                )
                if not result.get("found"):
                    raise AssertionError(
                        f"Column '{col_name}' not found on page: {result.get('reason')}"
                    )
                failing = result.get("failing", [])
                if failing:
                    raise AssertionError(
                        f"Filter validation failed: {len(failing)} cell(s) in '{col_name}' "
                        f"do not contain '{data}'. Sample: {failing[:3]}"
                    )
                print(
                    f"[VALIDATE] All {result['total']} visible '{col_name}' cells contain '{data}' OK"
                )
            elif intent == "url_contains":
                # validate, page url, /explorer/tin  → data must be substring of current URL
                current_url = page.url
                expected = (data or target or "").strip()
                if not expected:
                    raise AssertionError("url_contains validation: no expected value in 'data' or 'target'")
                if expected.lower() not in current_url.lower():
                    raise AssertionError(
                        f"URL validation failed: expected '{expected}' in '{current_url}'"
                    )
                print(f"[VALIDATE] URL contains '{expected}' OK  ({current_url})")

            elif intent == "map_data_match":
                await self._validate_map_data_match(page, target, data)

            elif intent == "map_loaded":
                # validate, map loaded,        → map canvas/container is visible
                # validate, map markers,        → at least one marker is present
                target_lower = target.lower()

                MAP_CONTAINER_SELECTORS = [
                    "canvas.mapboxgl-canvas",
                    ".mapboxgl-map",
                    ".leaflet-container",
                    "[class*='map-container']",
                    "[class*='mapContainer']",
                    "[id*='map']",
                    "canvas",
                ]

                # Try each selector; succeed on first visible match
                map_found = False
                for sel in MAP_CONTAINER_SELECTORS:
                    try:
                        count = await page.locator(sel).count()
                        if count > 0:
                            await page.locator(sel).first.wait_for(state="visible", timeout=5000)
                            map_found = True
                            print(f"[VALIDATE] Map container visible ({sel}) OK")
                            break
                    except Exception:
                        continue

                if not map_found:
                    raise AssertionError("Map validation failed: no visible map container found on page")

                # If target mentions "marker" also check at least one marker exists
                if "marker" in target_lower:
                    MARKER_SELECTORS = [
                        ".mapboxgl-marker",
                        ".leaflet-marker-icon",
                        "[class*='marker']",
                        "[class*='pin']",
                        "circle",          # SVG-based markers (Mapbox GL / D3)
                    ]
                    marker_found = False
                    for sel in MARKER_SELECTORS:
                        try:
                            cnt = await page.locator(sel).count()
                            if cnt > 0:
                                marker_found = True
                                print(f"[VALIDATE] {cnt} map marker(s) found ({sel}) OK")
                                break
                        except Exception:
                            continue
                    if not marker_found:
                        raise AssertionError("Map marker validation failed: no markers found on the map")

                # Optionally check expected count of markers when data is a number
                if data and str(data).strip().isdigit():
                    expected_count = int(str(data).strip())
                    for sel in [".mapboxgl-marker", ".leaflet-marker-icon", "[class*='marker']"]:
                        try:
                            cnt = await page.locator(sel).count()
                            if cnt > 0:
                                if cnt < expected_count:
                                    raise AssertionError(
                                        f"Map marker count validation failed: expected ≥{expected_count}, found {cnt}"
                                    )
                                print(f"[VALIDATE] Map marker count ≥{expected_count} ({cnt} found) OK")
                                break
                        except AssertionError:
                            raise
                        except Exception:
                            continue

            elif intent == "count":
                # validate, providers count, 3  → at least 3 visible matching elements
                expected_count = 0
                try:
                    expected_count = int(str(data).strip())
                except (ValueError, TypeError):
                    raise AssertionError(f"count validation: 'data' must be a number, got '{data}'")

                # Strip " count" from target to get the element description
                import re as _re2
                element_desc = _re2.sub(r"\s+count\s*$", "", target, flags=_re2.IGNORECASE).strip()

                # Try to count visible elements matching the description
                if locator is not None:
                    actual_count = await locator.count()
                else:
                    # Fallback: search by text / role
                    actual_count = await page.get_by_text(element_desc, exact=False).count()

                if actual_count < expected_count:
                    raise AssertionError(
                        f"Count validation failed for '{element_desc}': "
                        f"expected ≥{expected_count}, found {actual_count}"
                    )
                print(f"[VALIDATE] Count '{element_desc}' ≥{expected_count} ({actual_count} found) OK")

            elif intent == "db_data":
                await self._handle_db_validation(page, target, data)

            elif intent == "locator":
                await locator.wait_for(state="visible", timeout=5000)
            return True
        except Exception:
            if confidence == "low":
                return False
            raise

    # ------------------------------------------------------------------
    # 🔹 DB DATA VALIDATION
    # ------------------------------------------------------------------
    async def _handle_db_validation(self, page, target: str, data: str):
        """
        Validate UI table data against a DB query result.

        CSV usage:
            validate | db data   | gaps_summary
            validate | db data   | gaps_summary?state=INDIANA&network=PPO
            validate | database  | provider_detail

        Flow:
          1. Lazily open a DB connection using the profile set on this executor.
          2. Run the SQL template from queries/<data_field>.sql,
             injecting parameter values read from the current page.
          3. Extract the full visible UI table from the page.
          4. Compare row-by-row using the column mapping in queries/<data_field>.yaml.
          5. Raise AssertionError listing every mismatch found.
        """
        if not self._db_profile_name:
            raise AssertionError(
                "DB validation requires a DB profile. "
                "Select a profile in the test execution form or pass "
                "db_profile=<name> to run_all_testcases()."
            )

        # Lazily create and reuse the connector across all steps in the run
        if self._db_connector is None:
            profile = get_profile(self._db_profile_name)
            self._db_connector = get_connector(profile)
            self._db_connector.connect()
            print(f"[DB] Connected using profile '{self._db_profile_name}'")

        # Run the SQL query (data field = query key, optionally ?params)
        db_rows, config = await run_query(
            data_field=data,
            connector=self._db_connector,
            page=page,
        )

        # Extract the currently visible UI table rows
        ui_rows = await extract_ui_table(page)

        if not ui_rows:
            raise AssertionError(
                "DB validation: no table rows found on the page. "
                "Ensure the table is loaded and visible before this step."
            )

        print(
            f"[DB-VALIDATE] Comparing {len(ui_rows)} UI row(s) "
            f"against {len(db_rows)} DB row(s)"
        )

        mismatches = validate_compare(ui_rows, db_rows, config)

        if mismatches:
            raise AssertionError(
                f"DB validation failed — {len(mismatches)} mismatch(es):\n"
                + "\n".join(f"  • {m}" for m in mismatches)
            )

        print(
            f"[DB-VALIDATE] OK — all {len(ui_rows)} visible row(s) "
            f"match the DB"
        )

    # ------------------------------------------------------------------
    # 🔹 MAP DATA VALIDATION (generic)
    # ------------------------------------------------------------------
    async def _validate_map_data_match(self, page, target: str, data: str):
        """
        Generic map data validation — works with any map technology and any
        data column.  Not tied to a specific app, property name, or color scheme.

        CSV usage:
            validate | map data    | Gaps          ← cross-ref map vs 'Gaps' table column
            validate | map gradient| Gaps          ← same + legend-driven color check
            validate | map data    | Network Score ← any column name in the data field

        How it works:
          1. Extract feature data from the map using the best available strategy:
               a) Mapbox GL  — window.map / window._map queryRenderedFeatures()
               b) Leaflet    — iterate eachLayer() for GeoJSON features
               c) SVG paths  — read data-* attributes from <path> elements
               d) Tooltips   — hover each interactive map element and read popup text
          2. Read the table column specified in 'data' param.
          3. Auto-discover which feature property matches the table column by
             finding the property whose values best correlate with the table values
             (no hardcoded property names).
          4. Cross-reference every table row against the matching map feature.
          5. If target contains 'gradient'/'color': read the page's own color legend
             to determine the expected color range, then sample canvas pixels to
             verify each feature's color is consistent with its value position.
        """
        target_lower = target.lower()
        check_color = any(w in target_lower for w in ["gradient", "color", "colour"])
        col_header = (data or "").strip()

        # ── Step 1: Extract map feature data (multi-strategy) ─────────────
        feature_data = await self._extract_map_features(page)

        if not feature_data:
            raise AssertionError(
                "Map data validation: could not extract feature data from the map. "
                "Tried Mapbox GL, Leaflet, SVG paths, and hover tooltips."
            )
        print(f"[MAP-DATA] Extracted {len(feature_data)} map features")

        # ── Step 2: Read the table column ─────────────────────────────────
        if not col_header:
            raise AssertionError(
                "Map data validation: specify the table column name in the 'Data' field "
                "(e.g., validate | map data | Gaps)"
            )

        table_data = await self._read_table_column_data(page, col_header)

        if not table_data:
            raise AssertionError(
                f"Map data validation: no rows found for column '{col_header}'. "
                "Check the column header name in your CSV matches the table."
            )
        print(f"[MAP-DATA] Read {len(table_data)} rows from '{col_header}' column")

        # ── Step 3: Auto-discover matching property in map features ───────
        # feature_data structure: { region_name: { prop1: val, prop2: val, ... } }
        # Find which property's values best match the table column values.
        matched_prop = self._find_best_matching_property(feature_data, table_data)

        if matched_prop is None:
            # Fall back: use all numeric properties for reporting
            sample = list(feature_data.values())[:1]
            props = list(sample[0].keys()) if sample else []
            raise AssertionError(
                f"Map data validation: could not find a map feature property that "
                f"matches the '{col_header}' column values. "
                f"Available feature properties: {props}"
            )
        print(f"[MAP-DATA] Auto-matched map property: '{matched_prop}'")

        # ── Step 4: Cross-reference ────────────────────────────────────────
        mismatches = []
        matched_count = 0
        not_found = []

        for region_name, table_val in table_data.items():
            feature = feature_data.get(region_name)
            if feature is None:
                not_found.append(region_name)
                continue
            map_val = feature.get(matched_prop)
            if map_val is None:
                not_found.append(region_name)
                continue
            try:
                # Numeric comparison with ±1% tolerance or ±1 absolute
                t_num = float(str(table_val).replace(",", ""))
                m_num = float(str(map_val).replace(",", ""))
                tol = max(1, abs(t_num) * 0.01)
                if abs(t_num - m_num) > tol:
                    mismatches.append(
                        f"{region_name}: table={table_val}, map={map_val}"
                    )
                else:
                    matched_count += 1
            except (ValueError, TypeError):
                # String comparison
                if str(table_val).strip().lower() != str(map_val).strip().lower():
                    mismatches.append(
                        f"{region_name}: table='{table_val}', map='{map_val}'"
                    )
                else:
                    matched_count += 1

        if not_found:
            print(f"[MAP-DATA] {len(not_found)} region(s) not found in map features "
                  f"(may be off-screen): {not_found[:5]}")

        if mismatches:
            raise AssertionError(
                f"Map data mismatch for {len(mismatches)} region(s):\n"
                + "\n".join(mismatches)
            )

        print(f"[MAP-DATA] Cross-reference OK — {matched_count} region(s) verified")

        # ── Step 5: Optional legend-driven color check ────────────────────
        if check_color:
            await self._validate_map_colors_via_legend(page, feature_data, table_data, matched_prop)

    async def _extract_map_features(self, page) -> dict:
        """
        Try multiple strategies to extract map feature data.
        Returns: { REGION_NAME_UPPER: { prop: value, ... }, ... }
        """
        # Strategy A: Highcharts Maps — primary strategy when Highcharts is present.
        # Gives us structured data directly from the chart API, including the
        # rendered color per point, which makes color validation exact.
        features = await page.evaluate("""
            () => {
                if (typeof Highcharts === 'undefined' || !Highcharts.charts) return null;

                const MAP_SERIES_TYPES = new Set([
                    'map', 'mapbubble', 'mappoint', 'mapline',
                    'tiledwebmap', 'heatmap'
                ]);
                const result = {};

                for (const chart of Highcharts.charts) {
                    if (!chart || !chart.series) continue;

                    for (const series of chart.series) {
                        if (!MAP_SERIES_TYPES.has(series.type)) continue;

                        const points = series.points || [];
                        for (const point of points) {
                            // Name: prefer point.name, then hc-key, then options fields
                            const name = (
                                point.name ||
                                (point.options && point.options.name) ||
                                (point.options && point.options['hc-key']) ||
                                point['hc-key'] ||
                                ''
                            ).toString().trim().toUpperCase();

                            if (!name) continue;

                            const props = {};

                            // Primary numeric value from Highcharts
                            if (point.value !== undefined && point.value !== null) {
                                props.value = point.value;
                            }

                            // All scalar fields from point.options (custom data columns)
                            if (point.options) {
                                for (const [k, v] of Object.entries(point.options)) {
                                    if (v !== null && v !== undefined &&
                                        typeof v !== 'object' && !k.startsWith('_')) {
                                        props[k] = v;
                                    }
                                }
                            }

                            // Store the Highcharts-rendered fill color for gradient validation.
                            // This is exact — no canvas pixel sampling needed.
                            if (point.color) {
                                props._hc_color = point.color;
                            }

                            if (!result[name]) result[name] = {};
                            Object.assign(result[name], props);
                        }
                    }
                }
                return Object.keys(result).length ? result : null;
            }
        """)
        if features:
            print("[MAP-DATA] Highcharts Maps strategy succeeded")
            return features

        # Strategy B: Mapbox GL (window.map or any window property with queryRenderedFeatures)
        features = await page.evaluate("""
            () => {
                // Find a Mapbox GL map instance anywhere on window
                const mapObj = (
                    (typeof window.map !== 'undefined' && window.map &&
                     typeof window.map.queryRenderedFeatures === 'function')
                        ? window.map
                    : Object.values(window).find(
                        v => v && typeof v === 'object' &&
                             typeof v.queryRenderedFeatures === 'function'
                      )
                );
                if (!mapObj) return null;

                const raw = mapObj.queryRenderedFeatures();
                if (!raw || !raw.length) return null;

                const NAME_KEYS = [
                    'name','NAME','state','STATE','state_name','STATE_NAME',
                    'county','COUNTY','region','REGION','label','LABEL',
                    'title','TITLE','id','ID'
                ];

                const result = {};
                for (const f of raw) {
                    const props = f.properties || {};
                    let name = '';
                    for (const k of NAME_KEYS) {
                        if (props[k] && String(props[k]).trim()) {
                            name = String(props[k]).trim().toUpperCase();
                            break;
                        }
                    }
                    if (!name) continue;
                    if (!result[name]) result[name] = { _geometry: f.geometry };
                    // Collect all scalar properties
                    for (const [k, v] of Object.entries(props)) {
                        if (v !== null && v !== undefined && typeof v !== 'object') {
                            result[name][k] = v;
                        }
                    }
                }
                return Object.keys(result).length ? result : null;
            }
        """)
        if features:
            return features

        # Strategy B: Leaflet — iterate GeoJSON layers
        features = await page.evaluate("""
            () => {
                if (typeof window.L === 'undefined') return null;
                // Find all Leaflet map instances
                const maps = Object.values(window).filter(
                    v => v && v._layers && typeof v.eachLayer === 'function'
                );
                if (!maps.length) return null;

                const result = {};
                const NAME_KEYS = ['name','NAME','state','county','region','label','title','id'];

                for (const lmap of maps) {
                    lmap.eachLayer(layer => {
                        const data = layer.feature || layer.options;
                        if (!data) return;
                        const props = data.properties || data || {};
                        let name = '';
                        for (const k of NAME_KEYS) {
                            if (props[k]) { name = String(props[k]).trim().toUpperCase(); break; }
                        }
                        if (!name) return;
                        if (!result[name]) result[name] = {};
                        for (const [k, v] of Object.entries(props)) {
                            if (v !== null && v !== undefined && typeof v !== 'object') {
                                result[name][k] = v;
                            }
                        }
                    });
                }
                return Object.keys(result).length ? result : null;
            }
        """)
        if features:
            return features

        # Strategy C: SVG paths with data-* attributes or title/desc children
        features = await page.evaluate("""
            () => {
                const paths = Array.from(document.querySelectorAll('svg path, svg polygon, svg g[id]'));
                if (!paths.length) return null;

                const result = {};
                for (const el of paths) {
                    // Get name from id, data-name, title child, or aria-label
                    const name = (
                        el.querySelector('title')?.textContent ||
                        el.getAttribute('data-name') ||
                        el.getAttribute('aria-label') ||
                        el.getAttribute('id') || ''
                    ).trim().toUpperCase();
                    if (!name) continue;

                    const props = {};
                    // All data-* attributes become properties
                    for (const attr of el.attributes) {
                        if (attr.name.startsWith('data-') && attr.name !== 'data-name') {
                            const key = attr.name.slice(5);  // strip 'data-'
                            const num = parseFloat(attr.value);
                            props[key] = isNaN(num) ? attr.value : num;
                        }
                    }
                    if (Object.keys(props).length) result[name] = props;
                }
                return Object.keys(result).length ? result : null;
            }
        """)
        if features:
            return features

        # Strategy D: Hover-based tooltip extraction (universal fallback)
        features = await self._extract_features_via_hover(page)
        return features or {}

    async def _extract_features_via_hover(self, page) -> dict:
        """
        Hover over every interactive map element to trigger tooltips,
        parse the tooltip text into key-value pairs, and return feature data.
        Works with any map library.
        """
        INTERACTIVE_SELECTORS = [
            "svg path[class*='land']",
            "svg path[class*='region']",
            "svg path[class*='state']",
            "svg path[class*='county']",
            "svg path[id]",
            ".mapboxgl-map path",
            ".leaflet-interactive",
            "[class*='map'] [role='button']",
            "[class*='map'] [tabindex]",
        ]

        result = {}
        for sel in INTERACTIVE_SELECTORS:
            try:
                elements = page.locator(sel)
                count = await elements.count()
                if count == 0:
                    continue

                for i in range(min(count, 50)):  # cap at 50 to avoid excessive hovering
                    try:
                        el = elements.nth(i)
                        await el.hover(timeout=2000)
                        await asyncio.sleep(0.2)

                        # Read any visible tooltip/popup
                        tooltip_text = await page.evaluate("""
                            () => {
                                const TOOLTIP_SELECTORS = [
                                    '[role="tooltip"]',
                                    '.mapboxgl-popup-content',
                                    '.leaflet-popup-content',
                                    '[class*="tooltip"]',
                                    '[class*="popup"]',
                                    '[class*="hover-info"]',
                                ];
                                for (const sel of TOOLTIP_SELECTORS) {
                                    const el = document.querySelector(sel);
                                    if (el && el.offsetParent !== null) {
                                        return el.innerText.trim();
                                    }
                                }
                                return '';
                            }
                        """)

                        if not tooltip_text:
                            continue

                        # Parse tooltip: first non-empty line = name, rest = key: value pairs
                        lines = [l.strip() for l in tooltip_text.splitlines() if l.strip()]
                        if not lines:
                            continue
                        name = lines[0].upper()
                        props = {}
                        for line in lines[1:]:
                            if ":" in line:
                                k, _, v = line.partition(":")
                                v_clean = v.strip().replace(",", "")
                                num = None
                                try:
                                    num = float(v_clean)
                                except ValueError:
                                    pass
                                props[k.strip()] = num if num is not None else v.strip()
                        if props:
                            result[name] = props

                    except Exception:
                        continue

                if result:
                    print(f"[MAP-DATA] Hover strategy extracted {len(result)} features via '{sel}'")
                    return result

            except Exception:
                continue

        return result

    def _find_best_matching_property(self, feature_data: dict, table_data: dict) -> str | None:
        """
        Find which feature property best correlates with the table column values.
        Tries exact string match on property name first, then correlation on values.
        Returns the property name, or None if no match found.
        """
        if not feature_data or not table_data:
            return None

        # Collect all candidate properties (numeric ones) from features
        all_props: set = set()
        for feature in feature_data.values():
            for k, v in feature.items():
                if k.startswith("_"):
                    continue
                try:
                    float(str(v).replace(",", ""))
                    all_props.add(k)
                except (ValueError, TypeError):
                    all_props.add(k)

        # Build list of (region, table_val) pairs for regions present in features
        common = [(r, v) for r, v in table_data.items() if r in feature_data]
        if not common:
            return None

        best_prop = None
        best_score = -1

        for prop in all_props:
            matches = 0
            comparisons = 0
            for region, table_val in common:
                map_val = feature_data[region].get(prop)
                if map_val is None:
                    continue
                comparisons += 1
                try:
                    t_num = float(str(table_val).replace(",", ""))
                    m_num = float(str(map_val).replace(",", ""))
                    tol = max(1, abs(t_num) * 0.01)
                    if abs(t_num - m_num) <= tol:
                        matches += 1
                except (ValueError, TypeError):
                    if str(table_val).strip().lower() == str(map_val).strip().lower():
                        matches += 1

            if comparisons == 0:
                continue
            score = matches / comparisons
            if score > best_score:
                best_score = score
                best_prop = prop

        # Only accept if at least 50% of values match
        return best_prop if best_score >= 0.5 else None

    async def _read_table_column_data(self, page, col_header: str) -> dict:
        """
        Read a named column from any table on the page.
        Returns: { ROW_NAME_UPPER: value, ... }
        The first column (or any column labelled name/state/county/region) is used as the key.
        """
        return await page.evaluate(
            """
            (colHeader) => {
                const headerCells = Array.from(document.querySelectorAll(
                    '[role="columnheader"], th'
                ));

                // Find the target column
                const targetIdx = headerCells.findIndex(
                    h => h.textContent.trim().toLowerCase().includes(colHeader.toLowerCase())
                );
                if (targetIdx === -1) return {};

                // Find the name/label column
                const NAME_HINTS = ['state','county','region','name','label','title','area'];
                const nameIdx = (() => {
                    const i = headerCells.findIndex(h =>
                        NAME_HINTS.some(hint =>
                            h.textContent.trim().toLowerCase().includes(hint)
                        )
                    );
                    return i === -1 ? 0 : i;
                })();

                const rows = Array.from(document.querySelectorAll('[role="row"]'))
                    .filter(r => !r.querySelector('[role="columnheader"]'));

                const result = {};
                for (const row of rows) {
                    const cells = row.querySelectorAll('[role="cell"],[role="gridcell"],td');
                    const nameCell  = cells[nameIdx];
                    const valueCell = cells[targetIdx];
                    if (!nameCell || !valueCell) continue;
                    const name = nameCell.textContent.trim().toUpperCase();
                    const raw  = valueCell.textContent.trim();
                    if (name && raw) result[name] = raw;
                }
                return result;
            }
            """,
            col_header,
        )

    async def _validate_map_colors_via_legend(
        self, page, feature_data: dict, table_data: dict, matched_prop: str
    ):
        """
        Read the page's own color legend to determine the expected color range,
        then sample canvas pixels at each feature's geographic centre and verify
        the color is consistent with the feature's value position on the legend.

        This is fully generic — it reads the legend from the DOM rather than
        assuming any specific color scheme.
        """
        # ── Path A: Highcharts color axis (exact — no pixel sampling) ────
        # When the map is rendered by Highcharts we can get the expected color
        # for any value by interpolating through colorAxis.stops, and compare
        # it directly to point.color (already stored as _hc_color in feature_data).
        # ── Why relative ordering, not absolute color matching ──────────────
        # The legend shows Gap% (0–100) where:
        #   Gap% = gap_count / (total_counties × total_specialties) × 100
        #
        # The max threshold (total_counties × total_specialties) is computed
        # server-side and is not exposed on the page, so we cannot convert a
        # raw gap count from the table into an exact expected color.
        #
        # What we CAN verify without the threshold:
        #   "Regions with more gaps should be more red than regions with fewer gaps"
        #
        # This is the relative ordering check:
        #   Sort regions by table gap count → verify color 'redness' follows
        #   the same rank order.  A rank-order violation means the gradient is
        #   wrong regardless of what the threshold is.
        #
        # We additionally verify internal Highcharts consistency:
        #   point.color == interpolate(point.value, colorAxis.stops)
        # This proves Highcharts rendered the right color for the value it was
        # given — independent of how that value was calculated.
        hc_result = await page.evaluate(
            """
            ([tableData]) => {
                if (typeof Highcharts === 'undefined' || !Highcharts.charts) return null;

                const MAP_TYPES = new Set(['map','mapbubble','mappoint','mapline','heatmap']);
                let colorAxis = null;
                let allPoints = [];

                for (const chart of Highcharts.charts) {
                    if (!chart || !chart.series) continue;
                    for (const series of chart.series) {
                        if (!MAP_TYPES.has(series.type)) continue;
                        allPoints = allPoints.concat(series.points || []);
                    }
                    if (allPoints.length && chart.colorAxis && chart.colorAxis.length) {
                        colorAxis = chart.colorAxis[0];
                        break;
                    }
                }
                if (!allPoints.length) return null;

                // ── Helper: parse any CSS color string → {r,g,b} ──────────
                const _div = document.createElement('div');
                document.body.appendChild(_div);
                function parseColor(color) {
                    _div.style.color = color;
                    const cs = window.getComputedStyle(_div).color;
                    const m = cs.match(/rgb\\((\\d+),\\s*(\\d+),\\s*(\\d+)\\)/);
                    return m ? { r: +m[1], g: +m[2], b: +m[3] } : null;
                }

                // ── Helper: color → scalar "intensity" along the gradient ──
                // Works for any gradient: we measure how far the color sits
                // between the low-end color and the high-end color of the axis.
                // This is threshold-agnostic — it only uses the colorAxis stops.
                function colorIntensity(rgb) {
                    if (!colorAxis || !colorAxis.stops || !colorAxis.stops.length) {
                        // Fallback: use (r - g) as a generic red-vs-green score
                        return rgb.r - rgb.g;
                    }
                    const stops = colorAxis.stops;
                    const loColor = parseColor(stops[0][1]);
                    const hiColor = parseColor(stops[stops.length - 1][1]);
                    if (!loColor || !hiColor) return rgb.r - rgb.g;

                    // Project the sampled color onto the lo→hi axis using dot product
                    const dx = hiColor.r - loColor.r;
                    const dy = hiColor.g - loColor.g;
                    const dz = hiColor.b - loColor.b;
                    const len2 = dx*dx + dy*dy + dz*dz || 1;
                    return ((rgb.r - loColor.r)*dx +
                            (rgb.g - loColor.g)*dy +
                            (rgb.b - loColor.b)*dz) / len2;
                }

                // ── Check 1: Internal Highcharts consistency ───────────────
                // point.color should match interpolate(point.value, colorAxis).
                // This proves the rendering is correct for the value given,
                // without needing to know how that value was calculated.
                const internalMismatches = [];
                let internalChecked = 0;

                if (colorAxis && colorAxis.stops && colorAxis.stops.length) {
                    const stops = colorAxis.stops;
                    const axisMin = colorAxis.min ?? colorAxis.dataMin ?? 0;
                    const axisMax = colorAxis.max ?? colorAxis.dataMax ?? 1;
                    const axisSpan = axisMax - axisMin || 1;

                    function interpolate(norm) {
                        norm = Math.max(0, Math.min(1, norm));
                        let lo = stops[0], hi = stops[stops.length - 1];
                        for (let i = 0; i < stops.length - 1; i++) {
                            if (norm >= stops[i][0] && norm <= stops[i + 1][0]) {
                                lo = stops[i]; hi = stops[i + 1]; break;
                            }
                        }
                        const lc = parseColor(lo[1]);
                        const hc2 = parseColor(hi[1]);
                        if (!lc || !hc2) return null;
                        const span = hi[0] - lo[0];
                        const t = span === 0 ? 0 : (norm - lo[0]) / span;
                        return {
                            r: Math.round(lc.r + t * (hc2.r - lc.r)),
                            g: Math.round(lc.g + t * (hc2.g - lc.g)),
                            b: Math.round(lc.b + t * (hc2.b - lc.b)),
                        };
                    }

                    const TOLERANCE = 25;
                    for (const point of allPoints) {
                        const val = point.value;
                        if (val === null || val === undefined || !point.color) continue;
                        const norm = (val - axisMin) / axisSpan;
                        const expected = interpolate(norm);
                        if (!expected) continue;
                        const actual = parseColor(point.color);
                        if (!actual) continue;
                        internalChecked++;
                        if (Math.abs(actual.r - expected.r) > TOLERANCE ||
                            Math.abs(actual.g - expected.g) > TOLERANCE ||
                            Math.abs(actual.b - expected.b) > TOLERANCE) {
                            const name = (point.name || '').trim();
                            internalMismatches.push(
                                name + ': value=' + val.toFixed(1) +
                                ' expected rgb(' + expected.r + ',' + expected.g + ',' + expected.b + ')' +
                                ' actual rgb(' + actual.r + ',' + actual.g + ',' + actual.b + ')'
                            );
                        }
                    }
                }

                // ── Check 2: Relative ordering (table gap count vs color) ──
                // We cannot compute exact expected colors from raw gap counts
                // because the max threshold (counties × specialties) is server-side.
                // But we CAN verify that rank order is preserved:
                //   more gaps → higher color intensity (more toward the high end).
                // A rank inversion means the gradient is wrong.
                const orderViolations = [];
                let orderChecked = 0;

                const NAME_KEYS = ['name','NAME','state_name','STATE_NAME','county','region','label'];

                // Build a map of region → color intensity from Highcharts points
                const pointIntensity = {};
                for (const point of allPoints) {
                    const name = (
                        point.name ||
                        (point.options && point.options.name) ||
                        ''
                    ).toString().trim().toUpperCase();
                    if (!name || !point.color) continue;
                    const rgb = parseColor(point.color);
                    if (rgb) pointIntensity[name] = colorIntensity(rgb);
                }

                // Build sorted list of (region, gapCount) from table
                const tableEntries = Object.entries(tableData)
                    .map(([name, val]) => ({
                        name: name.toUpperCase(),
                        gap: parseFloat(String(val).replace(/,/g, ''))
                    }))
                    .filter(e => !isNaN(e.gap) && pointIntensity[e.name] !== undefined)
                    .sort((a, b) => a.gap - b.gap);  // ascending by gap count

                // Check that color intensity is also non-decreasing
                // (allow small ties within 0.02 intensity units)
                for (let i = 0; i < tableEntries.length - 1; i++) {
                    const a = tableEntries[i];
                    const b = tableEntries[i + 1];
                    if (a.gap === b.gap) continue;  // tied — skip
                    orderChecked++;
                    const ia = pointIntensity[a.name];
                    const ib = pointIntensity[b.name];
                    if (ib < ia - 0.02) {
                        orderViolations.push(
                            a.name + '(gaps=' + a.gap + ', intensity=' + ia.toFixed(3) + ')' +
                            ' should be less intense than ' +
                            b.name + '(gaps=' + b.gap + ', intensity=' + ib.toFixed(3) + ')'
                        );
                    }
                }

                document.body.removeChild(_div);
                return {
                    internalMismatches,
                    internalChecked,
                    orderViolations,
                    orderChecked,
                    axisMin: colorAxis ? (colorAxis.min ?? colorAxis.dataMin) : null,
                    axisMax: colorAxis ? (colorAxis.max ?? colorAxis.dataMax) : null,
                };
            }
            """,
            [table_data],
        )

        if hc_result is not None:
            print(
                f"[MAP-COLOR] colorAxis range=[{hc_result.get('axisMin')}, {hc_result.get('axisMax')}]"
            )

            # Report internal consistency failures
            if hc_result.get("internalMismatches"):
                raise AssertionError(
                    f"Highcharts internal color inconsistency "
                    f"({len(hc_result['internalMismatches'])} region(s)) — "
                    f"point.color does not match colorAxis interpolation:\n"
                    + "\n".join(hc_result["internalMismatches"])
                )
            if hc_result.get("internalChecked", 0) > 0:
                print(
                    f"[MAP-COLOR] Internal consistency OK — "
                    f"{hc_result['internalChecked']} points: point.color matches "
                    f"interpolate(point.value, colorAxis.stops)"
                )

            # Report ordering violations
            if hc_result.get("orderViolations"):
                raise AssertionError(
                    f"Map color ordering wrong for {len(hc_result['orderViolations'])} pair(s) — "
                    f"higher gap count should map to higher color intensity:\n"
                    + "\n".join(hc_result["orderViolations"])
                )
            if hc_result.get("orderChecked", 0) > 0:
                print(
                    f"[MAP-COLOR] Relative ordering OK — "
                    f"{hc_result['orderChecked']} adjacent pairs: "
                    f"higher gap count → higher color intensity"
                )
            return

        # ── Path B: Generic legend reading (non-Highcharts fallback) ──────
        # Read legend color stops ────────────────────────────────────────
        legend_info = await page.evaluate("""
            () => {
                // Find any element that looks like a color legend / gradient scale
                const LEGEND_HINTS = [
                    '[class*="legend"]', '[class*="scale"]', '[class*="gradient"]',
                    '[class*="colorbar"]', '[class*="color-bar"]', '[class*="choropleth"]',
                ];
                let legendEl = null;
                for (const hint of LEGEND_HINTS) {
                    const el = document.querySelector(hint);
                    if (el) { legendEl = el; break; }
                }
                if (!legendEl) return null;

                // Extract numeric labels from the legend
                const labels = Array.from(legendEl.querySelectorAll('*'))
                    .map(el => parseFloat(el.textContent.replace(/[^0-9.%]/g, '')))
                    .filter(n => !isNaN(n));

                // Extract color stops from a CSS linear-gradient background
                const style = window.getComputedStyle(legendEl);
                const bg = style.backgroundImage || '';
                const colorMatches = [...bg.matchAll(/rgb\\(\\s*(\\d+)\\s*,\\s*(\\d+)\\s*,\\s*(\\d+)\\s*\\)/g)];
                const colors = colorMatches.map(m => ({
                    r: parseInt(m[1]), g: parseInt(m[2]), b: parseInt(m[3])
                }));

                // If no gradient on container, check child elements for background-color
                if (!colors.length) {
                    const children = Array.from(legendEl.querySelectorAll('*'));
                    for (const child of children) {
                        const cs = window.getComputedStyle(child);
                        const bg2 = cs.backgroundImage || '';
                        const m2 = [...bg2.matchAll(/rgb\\(\\s*(\\d+)\\s*,\\s*(\\d+)\\s*,\\s*(\\d+)\\s*\\)/g)];
                        if (m2.length) {
                            m2.forEach(m => colors.push({
                                r: parseInt(m[1]), g: parseInt(m[2]), b: parseInt(m[3])
                            }));
                            break;
                        }
                    }
                }

                return {
                    minVal: labels.length ? Math.min(...labels) : null,
                    maxVal: labels.length ? Math.max(...labels) : null,
                    colorStops: colors,  // first = low end, last = high end
                };
            }
        """)

        if not legend_info or not legend_info.get("colorStops"):
            print("[MAP-COLOR] No legend found — skipping color validation")
            return

        color_stops = legend_info["colorStops"]
        min_val = legend_info.get("minVal")
        max_val = legend_info.get("maxVal")

        low_color  = color_stops[0]   if color_stops else None
        high_color = color_stops[-1]  if color_stops else None

        if not low_color or not high_color:
            print("[MAP-COLOR] Could not determine legend color range — skipping")
            return

        print(
            f"[MAP-COLOR] Legend range: {min_val}→{max_val} | "
            f"low=rgb({low_color['r']},{low_color['g']},{low_color['b']}) "
            f"high=rgb({high_color['r']},{high_color['g']},{high_color['b']})"
        )

        # Determine which channel varies most between low and high
        # This lets us validate any gradient (not just green→red)
        dr = high_color["r"] - low_color["r"]
        dg = high_color["g"] - low_color["g"]
        db = high_color["b"] - low_color["b"]
        dominant_channel = max(["r", "g", "b"], key=lambda c: abs({"r": dr, "g": dg, "b": db}[c]))

        # Compute table value range for normalisation
        try:
            all_vals = [float(str(v).replace(",", "")) for v in table_data.values()]
            v_min = min(all_vals)
            v_max = max(all_vals)
        except (ValueError, TypeError):
            print("[MAP-COLOR] Non-numeric table values — skipping color check")
            return

        color_failures = []

        for region_name, table_val in list(table_data.items())[:15]:  # sample up to 15
            feature = feature_data.get(region_name)
            if not feature:
                continue

            try:
                val_num = float(str(table_val).replace(",", ""))
            except (ValueError, TypeError):
                continue

            # Normalise value to 0–1 on the legend scale
            span = v_max - v_min if v_max != v_min else 1
            norm = (val_num - v_min) / span  # 0 = low end, 1 = high end

            # Get screen pixel for this feature
            pixel = await page.evaluate(
                """
                ([regionName, nameKeys]) => {
                    // Try Mapbox GL projection first
                    const mapObj = (
                        (typeof window.map !== 'undefined' && window.map &&
                         typeof window.map.project === 'function')
                            ? window.map
                        : Object.values(window).find(
                            v => v && typeof v === 'object' &&
                                 typeof v.project === 'function' &&
                                 typeof v.queryRenderedFeatures === 'function'
                          )
                    );
                    if (mapObj) {
                        const features = mapObj.queryRenderedFeatures().filter(f => {
                            const p = f.properties || {};
                            return nameKeys.some(k =>
                                p[k] && String(p[k]).trim().toUpperCase() === regionName
                            );
                        });
                        if (features.length) {
                            const geom = features[0].geometry;
                            if (!geom) return null;
                            let lng, lat;
                            if (geom.type === 'Point') {
                                [lng, lat] = geom.coordinates;
                            } else {
                                const coords = geom.type === 'Polygon'
                                    ? geom.coordinates[0]
                                    : geom.coordinates[0][0];
                                if (!coords || !coords.length) return null;
                                lng = coords.reduce((s, c) => s + c[0], 0) / coords.length;
                                lat = coords.reduce((s, c) => s + c[1], 0) / coords.length;
                            }
                            const pt = mapObj.project([lng, lat]);
                            return { x: Math.round(pt.x), y: Math.round(pt.y) };
                        }
                    }
                    // Fallback: find SVG element by name and get its bounding rect centre
                    const svgEl = Array.from(document.querySelectorAll(
                        'svg path[id], svg path[data-name], svg g[id]'
                    )).find(el => {
                        const n = (
                            el.getAttribute('data-name') ||
                            el.querySelector('title')?.textContent ||
                            el.getAttribute('id') || ''
                        ).trim().toUpperCase();
                        return n === regionName;
                    });
                    if (svgEl) {
                        const r = svgEl.getBoundingClientRect();
                        return { x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2) };
                    }
                    return null;
                }
                """,
                [
                    region_name,
                    ["name", "NAME", "state_name", "STATE_NAME", "county", "COUNTY",
                     "region", "REGION", "label", "LABEL", "title", "TITLE"],
                ],
            )

            if not pixel:
                continue

            # Sample the canvas at that pixel
            rgb = await page.evaluate(
                """
                ([x, y]) => {
                    // Try any canvas on the page (not just mapboxgl-canvas)
                    const canvases = Array.from(document.querySelectorAll('canvas'));
                    for (const canvas of canvases) {
                        try {
                            const ctx = canvas.getContext('2d');
                            if (!ctx) continue;
                            const d = ctx.getImageData(x, y, 1, 1).data;
                            if (d[3] === 0) continue;  // transparent — wrong canvas
                            return { r: d[0], g: d[1], b: d[2] };
                        } catch (e) { continue; }
                    }
                    return null;
                }
                """,
                [pixel["x"], pixel["y"]],
            )

            if not rgb:
                continue

            # Determine expected dominant channel value from the legend gradient
            lo = low_color[dominant_channel]
            hi = high_color[dominant_channel]
            expected_channel_val = lo + norm * (hi - lo)

            actual_channel_val = rgb[dominant_channel]
            # Allow ±30 tolerance (gradient interpolation is not perfectly linear)
            tolerance = 30
            if abs(actual_channel_val - expected_channel_val) > tolerance:
                color_failures.append(
                    f"{region_name}: value={val_num} (norm={norm:.2f}) "
                    f"expected {dominant_channel}≈{expected_channel_val:.0f} "
                    f"got rgb({rgb['r']},{rgb['g']},{rgb['b']})"
                )
            else:
                print(
                    f"[MAP-COLOR] {region_name}: val={val_num} norm={norm:.2f} "
                    f"rgb({rgb['r']},{rgb['g']},{rgb['b']}) OK"
                )

        if color_failures:
            raise AssertionError(
                f"Map color mismatch for {len(color_failures)} region(s):\n"
                + "\n".join(color_failures)
            )
        print(f"[MAP-COLOR] Legend-driven color check OK")

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