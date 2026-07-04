#!/usr/bin/env python3
"""
search_api.py — Intelligence Center 知识库检索 HTTP API

暴露 kb_retriever.py 的 FTS5 检索能力为 HTTP 服务。

端点:
  GET /search?q=xxx&top=10   — 关键词检索，返回 JSON 结果列表
  GET /health                — 健康检查
  GET /stats                 — 知识库统计信息

启动:
  python3 search_api.py                      # 默认 0.0.0.0:8081
  python3 search_api.py --port 9000          # 自定义端口
  python3 search_api.py --db /path/to.db     # 自定义 DB 路径

依赖:
  pip install fastapi uvicorn
"""

import argparse
import os
import sys
import json

# 确保能 import 同目录下的 kb 模块
_SRC_DIR = os.path.dirname(os.path.abspath(__file__))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
import uvicorn

from kb_common import get_db_path, log
from kb_retriever import search as kb_search
from kb_indexer import connect, init_db, index_status

try:
    sys.stdout.reconfigure(encoding="utf-8")
except AttributeError:
    pass

app = FastAPI(title="Intelligence Center Search API", version="1.0.0")

# 全局 DB 路径，由 CLI 参数或环境变量设定
_DB_PATH: str = os.environ.get("IC_DB_PATH", "")


def _resolve_db() -> str:
    """Return the effective DB path."""
    return _DB_PATH or get_db_path()


@app.get("/health")
async def health():
    """健康检查 — 始终返回 200，附加 DB 存在性信息。"""
    db = _resolve_db()
    db_exists = os.path.isfile(db)
    return {"status": "ok", "db": db, "db_exists": db_exists}


@app.get("/stats")
async def stats():
    """知识库统计 — 页面数、行业数、最后索引时间。"""
    db = _resolve_db()
    info = index_status(db)
    return info


@app.get("/search")
async def search(
    q: str = Query(..., description="搜索关键词，逗号分隔多个"),
    top: int = Query(10, ge=1, le=100, description="返回结果数"),
):
    """FTS5 关键词检索，返回 BM25 排序结果列表。"""
    if not q or not q.strip():
        return JSONResponse(
            status_code=400,
            content={"error": "Query parameter 'q' must not be empty"},
        )
    db = _resolve_db()
    if not os.path.isfile(db):
        return JSONResponse(
            status_code=503,
            content={"error": "Database not found", "db": db},
        )
    results = kb_search(q, db_path=db, top=top)
    return {"query": q, "top": top, "count": len(results), "results": results}


def main(argv=None):
    global _DB_PATH
    parser = argparse.ArgumentParser(description="IC Knowledge Base Search API")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--db", default=None, help="SQLite DB path (default: auto-detect)")
    args = parser.parse_args(argv)

    if args.db:
        _DB_PATH = os.path.abspath(os.path.expanduser(args.db))

    db = _resolve_db()
    log("Starting IC Search API on %s:%d (db=%s)" % (args.host, args.port, db))
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
