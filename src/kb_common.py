"""Shared utilities for the Intelligence Center Knowledge Base layer.

The helpers in this module keep Phase 2 modules stdlib-only, UTF-8 safe, and
consistent with the filesystem job contract introduced in Phase 1.
"""

import datetime
import json
import os
import re
import sys


try:
    sys.stdout.reconfigure(encoding="utf-8")
except AttributeError:
    pass


def log(message):
    """Print a UTF-8 timestamped log line."""
    print("%s %s" % (datetime.datetime.now().isoformat(timespec="seconds"), message), flush=True)


def now_iso():
    """Return current local ISO timestamp without microseconds."""
    return datetime.datetime.now().replace(microsecond=0).isoformat()


def ensure_dir(path):
    """Create a directory path if it does not already exist."""
    if path:
        os.makedirs(path, exist_ok=True)
    return path


def read_json(path):
    """Read a UTF-8 JSON file."""
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path, data):
    """Write pretty UTF-8 JSON."""
    ensure_dir(os.path.dirname(os.path.abspath(path)))
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def project_root():
    """Return the project root inferred from this file location."""
    return os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def get_db_path():
    """Return the default SQLite FTS database path under data/."""
    return os.path.join(project_root(), "data", "wiki_fts.db")


def default_wiki_dir():
    """Return the default knowledge wiki root."""
    return os.path.expanduser(os.path.join("~", "clawd", "knowledge"))


def _split_values(value):
    value = value.strip().strip("[]")
    parts = re.split(r"[,，;；]", value)
    return [part.strip().strip("\"'") for part in parts if part.strip().strip("\"'")]


def _extract_tags_from_lines(lines):
    tags = []
    for line in lines:
        match = re.match(r"^\s*(keywords|tags)\s*:\s*(.+?)\s*$", line, flags=re.IGNORECASE)
        if match:
            for item in _split_values(match.group(2)):
                if item not in tags:
                    tags.append(item)
    return tags


def extract_metadata(filepath):
    """Extract title, industry, tags, keywords, and content from a markdown file.

    Title is the first ``# `` heading, falling back to the filename stem.
    Industry is the directory name directly under any ``wiki`` path segment.
    Tags and keywords are read from markdown lines or YAML frontmatter lines
    starting with ``tags:`` or ``keywords:``.
    """
    path = os.path.abspath(filepath)
    with open(path, "r", encoding="utf-8") as handle:
        content = handle.read()
    lines = content.splitlines()
    title = next((line[2:].strip() for line in lines if line.startswith("# ") and line[2:].strip()), None)
    if not title:
        title = os.path.splitext(os.path.basename(path))[0]

    parts = list(os.path.normpath(path).split(os.sep))
    industry = ""
    for index, part in enumerate(parts[:-1]):
        if part == "wiki" and index + 1 < len(parts):
            industry = parts[index + 1]
            break

    tags = _extract_tags_from_lines(lines)
    return {
        "path": path,
        "title": title,
        "content": content,
        "industry": industry,
        "tags": ", ".join(tags),
        "keywords": ", ".join(tags),
    }
