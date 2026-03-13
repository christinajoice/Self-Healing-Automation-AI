# backend/main.py
import sys
import asyncio
import shutil
import csv
import uuid
from datetime import datetime
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
        "execution_id": execution_id,
        "state": state,
        "message": message,
        "progress": progress,
        "error": error,
        "updated_at": datetime.utcnow().isoformat(),
        **execution_status.get(execution_id, {}),
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
executor = TestExecutor(headless=False)

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
    file_path = UPLOAD_DIR / file.filename
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with file_path.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        raise HTTPException(500, f"Failed to save uploaded file: {e}")
    # --------------------------------------------------
    # 🔹 Parse CSV (UNCHANGED)
    # --------------------------------------------------
    testcase_dict = {
        "testcase_id": file.filename,
        "steps": [],
    }

    try:
        with file_path.open(newline="", encoding="utf-8") as csvfile:
            reader = csv.DictReader(csvfile)

            if not reader.fieldnames:
                raise HTTPException(400, "Uploaded CSV has no headers")

            headers = [h.strip().lower() for h in reader.fieldnames]
            if not {"action", "target"}.issubset(headers):
                raise HTTPException(400, "CSV must contain action and target columns")

            for raw_row in reader:
                row = {k.strip().lower(): v for k, v in raw_row.items()}

                # 🔹 Credential injection (UNCHANGED)
                if row.get("action") == "enter":
                    if row.get("target") == "username field" and username:
                        row["data"] = username
                    elif row.get("target") == "password field" and password:
                        row["data"] = password

                testcase_dict["steps"].append(row)

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
    background_tasks.add_task(
        run_testcase_background,
        execution_id,
        testcase_dict,
        base_url,
        username,
        password,
    )

    return {
        "execution_id": execution_id,
        "status": "QUEUED",
    }

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
# 🔹 Reports (UNCHANGED)
# --------------------------------------------------
@app.get("/reports")
def get_reports():
    import glob
    import json

    report_files = glob.glob(str(BASE_DIR / "reports" / "*.json"))
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
    testcase_dict: dict,
    base_url: str,
    username: str,
    password: str,
):
    try:
        update_status(
            execution_id,
            state="RUNNING",
            message="Execution started",
            progress=10,
        )

        results = await executor.run_testcase(
            testcase=testcase_dict,
            base_url=base_url,
            credentials={
                "username": username,
                "password": password,
            }
            if username or password
            else None,
        )

        update_status(
            execution_id,
            state="COMPLETED",
            message="Execution completed successfully",
            progress=100,
        )

        execution_status[execution_id]["results"] = results

    except Exception as e:
        update_status(
            execution_id,
            state="FAILED",
            message="Execution failed",
            error=str(e),
        )
