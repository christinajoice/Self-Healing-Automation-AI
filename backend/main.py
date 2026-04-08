# backend/main.py
import sys
import os
import asyncio
import shutil
import csv
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Windows asyncio fix
if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import (
    FastAPI,
    UploadFile,
    Form,
    File,
    HTTPException,
    BackgroundTasks,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from core.execution.executor import TestExecutor
from core.db.profile_loader import (
    list_profiles,
    save_uploaded_profiles,
    load_profiles,
)
from dotenv import load_dotenv
load_dotenv()

# --------------------------------------------------
# 🔹 Execution Status Store (IN-MEMORY)
# --------------------------------------------------
execution_status = {}


def update_status(
    execution_id: str,
    state: str,
    message: str,
    progress: Optional[int] = None,
    error: Optional[str] = None,
):
    execution_status[execution_id] = {
        **execution_status.get(execution_id, {}),
        "execution_id": execution_id,
        "state": state,
        "message": message,
        "progress": progress,
        "error": error,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


# --------------------------------------------------
# 🔹 App Init
# --------------------------------------------------
app = FastAPI()

# --------------------------------------------------
# 🔹 CORS
# --------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in prod
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --------------------------------------------------
# 🔹 Paths
# --------------------------------------------------
BASE_DIR = Path.home() / "SelfHealingAutomationAI"
UPLOAD_DIR = BASE_DIR / "testspecs"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# --------------------------------------------------
# 🔹 Executor (SINGLE INSTANCE)
# --------------------------------------------------
HEADLESS = os.getenv("BROWSER_HEADLESS", "true").lower() != "false"
executor = TestExecutor(headless=HEADLESS)

# Per-execution cancellation flags
_cancel_flags: dict = {}

# --------------------------------------------------
# 🔹 Health Check
# --------------------------------------------------
@app.get("/")
def health():
    return {"status": "Self-Healing Automation API is running"}

# --------------------------------------------------
# 🔹 Upload & Execute Testcase (ASYNC + LIVE STATUS)
# --------------------------------------------------
@app.post("/upload_testcase", response_model=dict)
async def upload_testcase(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    base_url: str = Form(...),
    username: str = Form(None),
    password: str = Form(None),
    db_profile: str = Form(None),
):
    # 🔹 Execution ID
    execution_id = f"exec_{uuid.uuid4().hex}"

    update_status(
        execution_id,
        state="QUEUED",
        message="Test case received",
        progress=0,
    )

    # --------------------------------------------------
    # 🔹 Save CSV (UNCHANGED)
    # --------------------------------------------------
    safe_filename = Path(file.filename).name
    file_path = UPLOAD_DIR / safe_filename
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with file_path.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        raise HTTPException(500, f"Failed to save uploaded file: {e}")
    # --------------------------------------------------
    # 🔹 Parse CSV — group rows by TestCaseID
    # --------------------------------------------------
    testcase_list = []

    try:
        with file_path.open(newline="", encoding="utf-8") as csvfile:
            reader = csv.DictReader(csvfile)

            if not reader.fieldnames:
                raise HTTPException(400, "Uploaded CSV has no headers")

            headers = [h.strip().lower() for h in reader.fieldnames]
            if not {"action", "target"}.issubset(headers):
                raise HTTPException(400, "CSV must contain action and target columns")

            testcases_by_id: dict = {}
            for raw_row in reader:
                row = {k.strip().lower(): (v or "").strip() for k, v in raw_row.items()}

                # Determine which test case this row belongs to
                tc_id = row.get("testcaseid", "").strip() or file.filename

                # Credential injection
                if row.get("action") == "enter":
                    if row.get("target") == "username input text field" and username:
                        row["data"] = username
                    elif row.get("target") == "password input text field" and password:
                        row["data"] = password

                step = {
                    "step": int(row.get("step") or 0),
                    "action": row.get("action", ""),
                    "target": row.get("target", ""),
                    "data": row.get("data") if row.get("data", "") != "" else None,
                    "confidence": row.get("confidence", "high") or "high",
                }
                testcases_by_id.setdefault(tc_id, []).append(step)

            testcase_list = [
                {
                    "testcase_id": tc_id,
                    "steps": sorted(steps, key=lambda x: x["step"]),
                }
                for tc_id, steps in testcases_by_id.items()
            ]

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Failed to parse test case: {e}")

    # --------------------------------------------------
    # 🔹 Guardrails (UNCHANGED)
    # --------------------------------------------------
    if not base_url or base_url.strip().lower() == "string":
        raise HTTPException(
            status_code=400,
            detail="Invalid base_url provided",
        )

    if not base_url.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=400,
            detail="base_url must start with http:// or https://",
        )

    # --------------------------------------------------
    # 🔹 Background Execution (NEW)
    # --------------------------------------------------
    cancel_flag = asyncio.Event()
    _cancel_flags[execution_id] = cancel_flag

    background_tasks.add_task(
        run_testcase_background,
        execution_id,
        testcase_list,
        base_url,
        username,
        password,
        cancel_flag,
        db_profile,
    )

    return {
        "execution_id": execution_id,
        "status": "QUEUED",
    }

# --------------------------------------------------
# 🔹 Cancel Execution
# --------------------------------------------------
@app.post("/cancel_execution/{execution_id}")
def cancel_execution(execution_id: str):
    flag = _cancel_flags.get(execution_id)
    if not flag:
        raise HTTPException(status_code=404, detail="Execution not found")
    flag.set()
    update_status(execution_id, state="CANCELLED", message="Execution cancelled by user", progress=None)
    return {"execution_id": execution_id, "status": "CANCELLED"}

# --------------------------------------------------
# 🔹 Execution Status API (Polling)
# --------------------------------------------------
@app.get("/execution_status/{execution_id}")
def get_execution_status(execution_id: str):
    return execution_status.get(
        execution_id,
        {
            "execution_id": execution_id,
            "state": "UNKNOWN",
            "message": "Invalid execution ID",
            "progress": 0,
        },
    )

# --------------------------------------------------
# 🔹 DB Profile Management
# --------------------------------------------------

@app.get("/db_profiles")
def get_db_profiles():
    """Return the list of configured DB profile names."""
    try:
        profiles = list_profiles()
        return {"profiles": profiles}
    except Exception as e:
        return {"profiles": [], "error": str(e)}


@app.post("/upload_db_config")
async def upload_db_config(file: UploadFile = File(...)):
    """
    Upload a db_profiles.yaml file.
    Replaces the existing file and reloads profiles immediately.
    No server restart needed.
    """
    if not file.filename.endswith((".yaml", ".yml")):
        raise HTTPException(400, "Only .yaml / .yml files are accepted")

    content = (await file.read()).decode("utf-8")

    try:
        save_uploaded_profiles(content)
    except ValueError as e:
        raise HTTPException(400, str(e))

    profiles = list_profiles()
    return {
        "message": "DB profiles loaded successfully",
        "profiles": profiles,
    }


@app.post("/test_db_connection/{profile_name}")
def test_db_connection(profile_name: str):
    """Test connectivity for a named DB profile."""
    try:
        from core.db.profile_loader import get_profile
        from core.db.connector import get_connector
        profile = get_profile(profile_name)
        conn = get_connector(profile)
        ok = conn.test()
        conn.close()
        if ok:
            return {"profile": profile_name, "status": "OK"}
        raise HTTPException(500, f"Connection test failed for profile '{profile_name}'")
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(500, str(e))


# --------------------------------------------------
# 🔹 Reports (UNCHANGED)
# --------------------------------------------------
@app.get("/reports")
def get_reports():
    import glob
    import json

    report_files = glob.glob(str(Path("reports") / "*.json"))
    reports = []

    for f in report_files:
        with open(f, "r", encoding="utf-8") as rf:
            reports.append(json.load(rf))

    return {"reports": reports}

# --------------------------------------------------
# 🔹 Background Runner
# --------------------------------------------------
async def run_testcase_background(
    execution_id: str,
    testcase_list: list,
    base_url: str,
    username: str,
    password: str,
    cancel_flag: asyncio.Event = None,
    db_profile: str = None,
):
    try:
        update_status(
            execution_id,
            state="RUNNING",
            message="Execution started",
            progress=10,
        )

        all_results = await executor.run_all_testcases(
            testcases=testcase_list,
            base_url=base_url,
            credentials={
                "username": username,
                "password": password,
            }
            if username or password
            else None,
            cancel_flag=cancel_flag,
            db_profile=db_profile or None,
        )

        final_state = "CANCELLED" if (cancel_flag and cancel_flag.is_set()) else "COMPLETED"
        final_msg   = "Execution cancelled by user" if final_state == "CANCELLED" else "Execution completed successfully"
        update_status(execution_id, state=final_state, message=final_msg, progress=100)

        # Store per-TC breakdown plus an overall status
        overall = "PASS" if all(r.get("status") == "PASS" for r in all_results) else "FAIL"
        execution_status[execution_id]["results"] = {
            "status": overall,
            "testcases": all_results,
        }

    except Exception as e:
        update_status(
            execution_id,
            state="FAILED",
            message="Execution failed",
            error=str(e),
        )
