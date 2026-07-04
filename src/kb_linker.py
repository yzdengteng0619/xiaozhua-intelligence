"""Link newly ingested wiki pages to related existing KB pages.

The linker extracts metadata from a new markdown page, searches the FTS5 index
for related pages, records scored relationships in ``kb_links``, and appends a
single idempotent ``## 相关阅读`` section to the source page.
"""

import argparse
import os
import sqlite3
import sys

from kb_common import extract_metadata, get_db_path, now_iso, read_json
from kb_indexer import connect, init_db
from kb_retriever import build_match_query


try:
    sys.stdout.reconfigure(encoding="utf-8")
except AttributeError:
    pass


def split_tags(tags):
    """Normalize a tag string into a set."""
    return {item.strip() for item in str(tags or "").replace("，", ",").split(",") if item.strip()}


def tag_overlap(left, right):
    """Return overlap ratio using the smaller non-empty tag set as denominator."""
    a = split_tags(left)
    b = split_tags(right)
    if not a or not b:
        return 0.0
    return len(a & b) / float(min(len(a), len(b)))


def search_terms(meta):
    """Build related-search terms from tags, title, and industry."""
    terms = list(split_tags(meta.get("tags")))
    for item in [meta.get("title"), meta.get("industry")]:
        if item and item not in terms:
            terms.append(item)
    return terms


def _related_pages_from_conn(conn, page_path, top=5, meta=None):
    """Find related pages using an existing connection (internal helper)."""
    if meta is None:
        meta = extract_metadata(page_path)
    query = build_match_query(search_terms(meta))
    if not query:
        return []
    try:
        rows = conn.execute(
            """
            SELECT p.title, p.path, p.industry, p.tags, bm25(wiki_fts) AS rank
            FROM wiki_fts
            JOIN wiki_pages p ON p.id = wiki_fts.rowid
            WHERE wiki_fts MATCH ? AND p.path <> ?
            ORDER BY rank
            LIMIT ?
            """,
            (query, os.path.normpath(os.path.abspath(page_path)), max(int(top) * 3, int(top))),
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []

    results = []
    for index, row in enumerate(rows):
        overlap = tag_overlap(meta["tags"], row[3])
        rank_component = 1.0 / (1.0 + abs(float(row[4] or 0.0)))
        score = rank_component + overlap
        results.append(
            {
                "title": row[0],
                "path": row[1],
                "industry": row[2] or "",
                "tags": row[3] or "",
                "rank": row[4],
                "score": score,
                "link_type": "related",
            }
        )
    results.sort(key=lambda item: item["score"], reverse=True)
    for idx, item in enumerate(results):
        item["link_type"] = (
            "cited" if idx == 0
            else "same_topic" if item["score"] > 1.5
            else "same_industry" if item["industry"] and item["industry"] == meta.get("industry")
            else "related"
        )
    return results[: int(top)]


def _store_links_to_conn(conn, page_path, links):
    """Upsert links using an existing connection (internal helper)."""
    for link in links:
        conn.execute(
            """
            INSERT INTO kb_links(source_path, target_path, score, link_type, created_at)
            VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(source_path, target_path) DO UPDATE SET
                score = excluded.score,
                link_type = excluded.link_type
            """,
            (os.path.normpath(os.path.abspath(page_path)), link["path"], float(link["score"]), link["link_type"], now_iso()),
        )


def related_pages(page_path, db_path=None, top=5):
    """Find related pages for a source page, excluding the source itself."""
    page_path = os.path.normpath(os.path.abspath(page_path))
    conn = connect(os.path.abspath(os.path.expanduser(db_path or get_db_path())))
    try:
        init_db(conn)
        return _related_pages_from_conn(conn, page_path, top)
    finally:
        conn.close()


def store_links(page_path, db_path, links):
    """Upsert related-page links into ``kb_links``."""
    conn = connect(os.path.abspath(os.path.expanduser(db_path or get_db_path())))
    try:
        init_db(conn)
        _store_links_to_conn(conn, page_path, links)
        conn.commit()
    finally:
        conn.close()


def append_related_reading(page_path, links):
    """Append a related reading section unless the page already has one."""
    with open(page_path, "r", encoding="utf-8") as handle:
        content = handle.read()
    if "## 相关阅读" in content:
        return False
    if not links:
        return False
    lines = ["", "## 相关阅读", ""]
    for link in links:
        lines.append("- [%s](%s) - %s" % (link["title"], link["path"], link["link_type"]))
    with open(page_path, "a", encoding="utf-8") as handle:
        if content and not content.endswith("\n"):
            handle.write("\n")
        handle.write("\n".join(lines))
        handle.write("\n")
    return True


def link_page(page_path, db_path=None, top=5):
    """Link one wiki page to related existing pages (single transaction)."""
    page_path = os.path.normpath(os.path.abspath(page_path))
    conn = connect(os.path.abspath(os.path.expanduser(db_path or get_db_path())))
    try:
        init_db(conn)
        meta = extract_metadata(page_path)
        links = _related_pages_from_conn(conn, page_path, top, meta=meta)
        _store_links_to_conn(conn, page_path, links)
        conn.commit()
    finally:
        conn.close()
    append_related_reading(page_path, links)
    return links


def output_pages_from_job(job_path):
    """Find markdown outputs under common job output directories."""
    candidates = []
    for dirname in ["output", "outputs", "wiki", "pages"]:
        root = os.path.join(job_path, dirname)
        if not os.path.isdir(root):
            continue
        for dirpath, _, filenames in os.walk(root):
            for filename in filenames:
                if filename.lower().endswith(".md"):
                    candidates.append(os.path.join(dirpath, filename))
    manifest = os.path.join(job_path, "output_pages.json")
    if os.path.exists(manifest):
        data = read_json(manifest)
        values = data if isinstance(data, list) else data.get("pages", [])
        candidates.extend(str(item) for item in values)
    seen = set()
    unique = []
    for c in candidates:
        normed = os.path.normpath(os.path.abspath(c))
        if normed not in seen:
            seen.add(normed)
            unique.append(c)
    return unique


def link_job(job_path, db_path=None, top=5):
    """Link all markdown output pages found for a job (single connection)."""
    pages = output_pages_from_job(job_path)
    if not pages:
        return {}
    conn = connect(os.path.abspath(os.path.expanduser(db_path or get_db_path())))
    results = {}
    try:
        init_db(conn)
        for page in pages:
            normed = os.path.normpath(os.path.abspath(page))
            meta = extract_metadata(normed)
            links = _related_pages_from_conn(conn, normed, top, meta=meta)
            _store_links_to_conn(conn, normed, links)
            conn.commit()
            append_related_reading(normed, links)
            results[page] = links
    finally:
        conn.close()
    return results


def main(argv=None):
    """CLI entrypoint for KB page linking."""
    parser = argparse.ArgumentParser(description="Link new wiki pages to related KB pages")
    parser.add_argument("--page", help="New wiki page path")
    parser.add_argument("--job", help="Job directory with markdown outputs")
    parser.add_argument("--db", default=get_db_path())
    parser.add_argument("--top", type=int, default=5)
    args = parser.parse_args(argv)
    if args.page:
        links = link_page(args.page, args.db, args.top)
        print("%d links" % len(links))
        return 0
    if args.job:
        results = link_job(args.job, args.db, args.top)
        print("%d pages linked" % len(results))
        return 0
    parser.error("one of --page or --job is required")
    return 2


if __name__ == "__main__":
    sys.exit(main())
