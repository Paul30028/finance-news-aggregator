"""
应用入口
--------
启动流程：
  1. 初始化日志
  2. 初始化数据库（建表）
  3. 启动后台调度器（周期性抓取任务）
  4. 挂载静态文件 / 路由

运行方式：
  uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.logging_conf import setup_logging
from app.scheduler import scheduler
from app.storage.db import init_db
from app.web.routes import router as web_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    logger.info("正在初始化数据库...")
    await init_db()
    logger.info("启动抓取调度器...")
    scheduler.start()
    yield
    logger.info("正在关闭调度器...")
    await scheduler.stop()


app = FastAPI(title="即时财经新闻爬取与聚合软件", lifespan=lifespan)

static_dir = Path(__file__).resolve().parent / "web" / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

app.include_router(web_router)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
