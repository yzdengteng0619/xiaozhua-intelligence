"""Build and maintain the Intelligence Center wiki FTS5 index.

The indexer scans markdown pages under ``reports/wiki`` and ``web/wiki``,
stores page metadata in ``wiki_pages``, and mirrors searchable fields into an
external-content FTS5 table. Incremental runs use a metadata timestamp and only
reprocess files modified after the previous successful index pass.
"""

import argparse
import os
import sqlite3
import sys
import time

from kb_common import default_wiki_dir, ensure_dir, extract_metadata, get_db_path, log, now_iso


try:
    sys.stdout.reconfigure(encoding="utf-8")
except AttributeError:
    pass


def connect(db_path):
    """Open the KB SQLite database, creating parent directories."""
    ensure_dir(os.path.dirname(os.path.abspath(db_path)))
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(conn):
    """Create KB tables and the external-content FTS5 index."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS wiki_pages (
            id INTEGER PRIMARY KEY,
            path TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            industry TEXT,
            tags TEXT,
            mtime REAL NOT NULL,
            indexed_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS wiki_fts USING fts5(
            title,
            content,
            tags,
            content='wiki_pages',
            content_rowid='id',
            tokenize='trigram'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS kb_links (
            source_path TEXT NOT NULL,
            target_path TEXT NOT NULL,
            score REAL NOT NULL,
            link_type TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (source_path, target_path)
        )
        """
    )
    conn.commit()


def wiki_roots(wiki_dir):
    """Return the two supported wiki roots under a knowledge directory."""
    return [os.path.join(wiki_dir, "reports", "wiki"), os.path.join(wiki_dir, "web", "wiki")]


def iter_markdown_files(wiki_dir):
    """Yield markdown files from reports/wiki and web/wiki."""
    for root in wiki_roots(os.path.abspath(os.path.expanduser(wiki_dir))):
        if not os.path.isdir(root):
            log("skipping non-existent root: %s" % root)
            continue
        for dirpath, _, filenames in os.walk(root):
            for filename in filenames:
                if filename.lower().endswith(".md"):
                    yield os.path.join(dirpath, filename)


def metadata_value(conn, key):
    """Read one metadata value."""
    row = conn.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_metadata(conn, key, value):
    """Upsert one metadata value."""
    conn.execute(
        "INSERT INTO metadata(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )


def delete_existing(conn, path):
    """Remove an existing page and its FTS row."""
    row = conn.execute("SELECT id, title, content, tags FROM wiki_pages WHERE path = ?", (path,)).fetchone()
    if not row:
        return
    conn.execute(
        "INSERT INTO wiki_fts(wiki_fts, rowid, title, content, tags) VALUES('delete', ?, ?, ?, ?)",
        row,
    )
    conn.execute("DELETE FROM wiki_pages WHERE id = ?", (row[0],))


def upsert_page(conn, filepath):
    """Index or reindex one markdown page."""
    meta = extract_metadata(filepath)
    mtime = os.path.getmtime(filepath)
    delete_existing(conn, meta["path"])
    cursor = conn.execute(
        """
        INSERT INTO wiki_pages(path, title, content, industry, tags, mtime, indexed_at)
        VALUES(?, ?, ?, ?, ?, ?, ?)
        """,
        (meta["path"], meta["title"], meta["content"], meta["industry"], meta["tags"], mtime, now_iso()),
    )
    rowid = cursor.lastrowid
    conn.execute(
        "INSERT INTO wiki_fts(rowid, title, content, tags) VALUES(?, ?, ?, ?)",
        (rowid, meta["title"], meta["content"], meta["tags"]),
    )


def build_index(wiki_dir=None, db_path=None, full=False):
    """Build a full or incremental FTS5 index and return run statistics."""
    wiki_dir = os.path.abspath(os.path.expanduser(wiki_dir or default_wiki_dir()))
    db_path = os.path.abspath(os.path.expanduser(db_path or get_db_path()))
    start = time.time()
    conn = connect(db_path)
    try:
        init_db(conn)
        last_index = 0.0 if full else float(metadata_value(conn, "last_index_time") or 0.0)
        if full:
            conn.execute("DROP TABLE IF EXISTS wiki_fts")
            conn.execute("DELETE FROM wiki_pages")
            conn.execute("DELETE FROM kb_links")
            init_db(conn)
        indexed = 0
        scanned = 0
        for filepath in iter_markdown_files(wiki_dir):
            scanned += 1
            if full or os.path.getmtime(filepath) > last_index:
                upsert_page(conn, filepath)
                indexed += 1
                if indexed % 1000 == 0:
                    conn.commit()
                    log("indexed %d pages..." % indexed)
        set_metadata(conn, "last_index_time", start)
        set_metadata(conn, "last_index_at", now_iso())
        conn.commit()
        total = conn.execute("SELECT COUNT(*) FROM wiki_pages").fetchone()[0]
        return {"scanned": scanned, "indexed": indexed, "total": total, "db": db_path}
    finally:
        conn.close()


def index_status(db_path=None):
    """Return index statistics for CLI status output."""
    db_path = os.path.abspath(os.path.expanduser(db_path or get_db_path()))
    if not os.path.exists(db_path):
        return {"db": db_path, "exists": False, "pages": 0, "last_index_at": None}
    conn = connect(db_path)
    try:
        init_db(conn)
        pages = conn.execute("SELECT COUNT(*) FROM wiki_pages").fetchone()[0]
        industries = conn.execute("SELECT COUNT(DISTINCT industry) FROM wiki_pages").fetchone()[0]
        return {
            "db": db_path,
            "exists": True,
            "pages": pages,
            "industries": industries,
            "last_index_at": metadata_value(conn, "last_index_at"),
        }
    finally:
        conn.close()


def main(argv=None):
    """CLI entrypoint for wiki indexing."""
    parser = argparse.ArgumentParser(description="Build or inspect the wiki FTS5 index")
    parser.add_argument("--wiki-dir", default=default_wiki_dir())
    parser.add_argument("--db", default=get_db_path())
    parser.add_argument("--full", action="store_true", help="Force a complete reindex")
    parser.add_argument("--status", action="store_true", help="Print index statistics")
    args = parser.parse_args(argv)
    try:
        stats = index_status(args.db) if args.status else build_index(args.wiki_dir, args.db, full=args.full)
        for key in sorted(stats):
            print("%s: %s" % (key, stats[key]))
        return 0
    except Exception as exc:
        log("ERROR %s" % exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
