"""本地启动入口：python run.py 或 uvicorn app.web.app:app --reload"""

from __future__ import annotations

import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.web.app:app",
        host=os.environ.get("APP_HOST", "127.0.0.1"),
        port=int(os.environ.get("APP_PORT", "8000")),
        reload=os.environ.get("APP_RELOAD", "1") == "1",
    )
