"""Retrieve relevant wiki knowledge from the Intelligence Center FTS5 index.

The retriever accepts keywords directly or from a Phase 1 ``job_spec.json``,
searches the wiki FTS table with OR logic and BM25 ranking, then writes a
markdown context file for downstream research engines while returning JSON-safe
result dictionaries for programmatic callers.
"""

import argparse
import json
import os
import sqlite3
import sys

from kb_common import ensure_dir, get_db_path, log, now_iso, read_json
from kb_indexer import connect, init_db


try:
    sys.stdout.reconfigure(encoding="utf-8")
except AttributeError:
    pass


def coerce_keywords(value):
    """Normalize comma-separated strings or lists into clean keywords."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value).replace("，", ",").split(",") if item.strip()]


def quote_match_term(term):
    """Quote one FTS5 phrase for safe MATCH usage."""
    return '"%s"' % str(term).replace('"', '""')


def build_match_query(keywords):
    """Build an OR-based FTS5 MATCH query."""
    terms = coerce_keywords(keywords)
    return " OR ".join(quote_match_term(term) for term in terms)


def summarize(content, limit=500):
    """Return a compact first-N-character summary."""
    return (content or "")[:limit].strip()


def _is_short_term(term):
    """Check if a term is too short for FTS5 trigram tokenizer (< 3 chars)."""
    clean = term.strip().strip('"').strip()
    return len(clean) < 3

def _fallback_like_search(conn, keywords, top):
    """LIKE-based fallback for terms too short for FTS5 trigram."""
    terms = coerce_keywords(keywords)
    if not terms:
        return []
    # Build LIKE conditions for short terms only
    conditions = []
    params = []
    for term in terms:
        if _is_short_term(term):
            clean = term.strip().strip('"')
            conditions.append("(p.title LIKE ? OR p.tags LIKE ?)")
            params.extend(["%" + clean + "%", "%" + clean + "%"])
    if not conditions:
        return []
    where = " OR ".join(conditions)
    params.append(int(top))
    rows = conn.execute(
        "SELECT p.title, p.path, p.industry, p.tags, p.content, 0.0 AS rank "
        "FROM wiki_pages p WHERE " + where + " LIMIT ?",
        params,
    ).fetchall()
    return rows

def search(keywords, db_path=None, top=10):
    """Search the KB index and return ranked result dictionaries."""
    terms = coerce_keywords(keywords)
    short = [t for t in terms if _is_short_term(t)]
    long = [t for t in terms if not _is_short_term(t)]
    conn = connect(os.path.abspath(os.path.expanduser(db_path or get_db_path())))
    try:
        init_db(conn)
        rows = []
        # FTS5 for long terms
        if long:
            query = build_match_query(long)
            try:
                rows = conn.execute(
                    "SELECT p.title, p.path, p.industry, p.tags, p.content, bm25(wiki_fts) AS rank "
                    "FROM wiki_fts JOIN wiki_pages p ON p.id = wiki_fts.rowid "
                    "WHERE wiki_fts MATCH ? ORDER BY rank LIMIT ?",
                    (query, int(top)),
                ).fetchall()
            except sqlite3.OperationalError as exc:
                log("WARN: FTS5 query failed: %s" % exc)
        # LIKE fallback for short terms
        if short:
            fallback_rows = _fallback_like_search(conn, keywords, top)
            existing_paths = {r[1] for r in rows}
            for fr in fallback_rows:
                if fr[1] not in existing_paths:
                    rows.append(fr)
        rows = rows[:int(top)]
    finally:
        conn.close()
    return [
        {
            "title": row[0],
            "path": row[1],
            "industry": row[2] or "",
            "tags": row[3] or "",
            "summary": summarize(row[4]),
            "rank": row[5],
        }
        for row in rows
    ]


def format_markdown(keywords, results):
    """Render retrieval results as markdown context (Chinese format per Phase 2 brief)."""
    lines = [
        "# 历史知识检索结果",
        "",
        "## 检索关键词：%s" % ", ".join(coerce_keywords(keywords)),
        "## 检索时间：%s" % now_iso(),
        "## 命中页面：%d篇" % len(results),
        "",
        "---",
        "",
    ]
    if not results:
        lines.extend(["未找到匹配的历史知识。", ""])
        return "\n".join(lines)
    for index, item in enumerate(results, 1):
        lines.extend(
            [
                "### %d. %s (BM25相关度: %s)" % (index, item["title"], item["rank"]),
                "",
                "- **路径**：%s" % item["path"],
                "- **行业**：%s" % item["industry"],
                "- **标签**：%s" % item["tags"],
                "",
                "**摘要**：",
                "> %s" % item["summary"],
                "",
            ]
        )
    return "\n".join(lines)


def retrieve(keywords, db_path=None, top=10, output_path=None):
    """Search keywords, optionally write markdown context, and return results."""
    keyword_list = coerce_keywords(keywords)
    results = search(keyword_list, db_path, top)
    if output_path:
        ensure_dir(os.path.dirname(os.path.abspath(output_path)))
        with open(output_path, "w", encoding="utf-8") as handle:
            handle.write(format_markdown(keyword_list, results))
            handle.write("\n")
    return results


def retrieve_for_job(job_path, db_path=None, top=10):
    """Read job keywords and write ``context/kb_retrieval.md`` under the job."""
    spec_path = os.path.join(job_path, "job_spec.json")
    if not os.path.exists(spec_path):
        log("WARN: job_spec.json not found in %s" % job_path)
        return []
    spec = read_json(spec_path)
    output_path = os.path.join(job_path, "context", "kb_retrieval.md")
    return retrieve(spec.get("keywords", []), db_path, top, output_path)


def main(argv=None):
    """CLI entrypoint for KB retrieval."""
    parser = argparse.ArgumentParser(description="Retrieve wiki context from the KB FTS5 index")
    parser.add_argument("--keywords", help="Comma-separated keyword list")
    parser.add_argument("--job", help="Job directory containing job_spec.json")
    parser.add_argument("--db", default=get_db_path())
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    if args.job:
        results = retrieve_for_job(args.job, args.db, args.top)
    else:
        results = retrieve(args.keywords, args.db, args.top, args.output)
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
