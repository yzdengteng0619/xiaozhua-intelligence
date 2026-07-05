#!/usr/bin/env python3
"""FirstData GitHub同步 → 小爪本地SQLite（直接调GitHub API）"""
import json, os, sqlite3, sys, urllib.request
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))
DB_PATH = os.path.expanduser("~/migration/xiaoxia/hermes_data.db")
STATE_FILE = os.path.expanduser("~/logs/firstdata_sync_state.json")
LOG = os.path.expanduser("~/logs/firstdata_sync.log")
os.makedirs(os.path.dirname(LOG), exist_ok=True)

def log(msg):
    ts = datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG, "a") as f:
        f.write(line + "\n")

def get_latest_commit():
    req = urllib.request.Request("https://api.github.com/repos/MLT-OSS/FirstData/commits/main")
    req.add_header("Accept", "application/vnd.github.v3+json")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read()).get("sha", "")

def download_all_sources():
    req = urllib.request.Request("https://api.github.com/repos/MLT-OSS/FirstData/git/trees/main?recursive=1")
    req.add_header("Accept", "application/vnd.github.v3+json")
    with urllib.request.urlopen(req, timeout=30) as resp:
        tree = json.loads(resp.read())
    files = [i["path"] for i in tree.get("tree", [])
             if i["path"].startswith("firstdata/sources/") and i["path"].endswith(".json")]
    print(f"  文件数: {len(files)}")
    cache = {}
    for path in files:
        url = f"https://raw.githubusercontent.com/MLT-OSS/FirstData/main/{path}"
        req2 = urllib.request.Request(url)
        req2.add_header("Accept", "application/vnd.github.v3.raw")
        try:
            with urllib.request.urlopen(req2, timeout=15) as resp:
                src = json.loads(resp.read())
                sid = src.get("source_id", path.split("/")[-1].replace(".json", ""))
                cache[sid] = src
        except:
            pass
    return cache

def upsert_to_sqlite(sources):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    cur = conn.cursor()
    now = datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")
    ok, fail = 0, 0
    for sid, src in sources.items():
        try:
            no = src.get("name", {}) or {}
            do = src.get("description", {}) or {}
            cur.execute(
                "INSERT INTO firstdata_sources "
                "(id,name_en,name_zh,description_en,description_zh,website,data_url,api_url,"
                "authority_level,update_frequency,country,geographic_scope,domains,tags,"
                "has_api,raw_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "name_en=excluded.name_en, name_zh=excluded.name_zh, "
                "description_en=excluded.description_en, description_zh=excluded.description_zh, "
                "website=excluded.website, data_url=excluded.data_url, api_url=excluded.api_url, "
                "authority_level=excluded.authority_level, update_frequency=excluded.update_frequency, "
                "country=excluded.country, geographic_scope=excluded.geographic_scope, "
                "domains=excluded.domains, tags=excluded.tags, has_api=excluded.has_api, "
                "raw_json=excluded.raw_json, updated_at=excluded.updated_at",
                (sid, no.get("en", ""), no.get("zh", "") or no.get("native", ""),
                 do.get("en", ""), do.get("zh", ""), src.get("website", ""), src.get("data_url", ""),
                 src.get("api_url", ""), src.get("authority_level", ""), src.get("update_frequency", ""),
                 src.get("country", ""), src.get("geographic_scope", ""),
                 json.dumps(src.get("domains", []), ensure_ascii=False),
                 json.dumps(src.get("tags", []), ensure_ascii=False),
                 1 if src.get("api_url") else 0,
                 json.dumps(src, ensure_ascii=False), now, now))
            ok += 1
        except Exception as e:
            fail += 1
            if fail <= 3:
                log(f"  warn {sid}: {e}")
    conn.commit()
    cur.execute("SELECT COUNT(*) FROM firstdata_sources")
    total = cur.fetchone()[0]
    conn.close()
    return ok, fail, total

def main():
    log("=== FirstData GitHub同步开始 ===")
    last = ""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            last = json.load(f).get("last_commit", "")
    try:
        latest = get_latest_commit()
    except Exception as e:
        log(f"warn: 获取commit失败 {e}，强制同步")
        latest = ""
    if latest and latest == last:
        log(f"无变更 ({latest[:12]})")
        return
    log(f"commit: {last[:12] if last else '首次'} -> {latest[:12] if latest else '?'}")
    sources = download_all_sources()
    log(f"下载完成: {len(sources)}个数据源")
    ok, fail, total = upsert_to_sqlite(sources)
    log(f"UPSERT: {ok}成功/{fail}失败, 总{total}")
    if latest:
        with open(STATE_FILE, "w") as f:
            json.dump({"last_commit": latest, "last_sync": datetime.now(CST).isoformat(),
                        "total": total, "upserted": ok}, f, indent=2)
    log("=== 完成 ===")

if __name__ == "__main__":
    main()
