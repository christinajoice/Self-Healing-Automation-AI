"""
Windows-safe launcher for the FastAPI backend.
Sets ProactorEventLoop BEFORE uvicorn creates its event loop,
which is required for Playwright subprocess (browser) support on Windows.
"""
import asyncio
import sys

if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import uvicorn

if __name__ == "__main__":
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, reload=False)
