#!/usr/bin/env python3
"""本地开发启动脚本：等价于 `uvicorn app.main:app --reload`。"""
import uvicorn

from app.config import get_settings

if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run("app.main:app", host=settings.app.host, port=settings.app.port, reload=True)
