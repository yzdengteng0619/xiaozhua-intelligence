#!/usr/bin/env python3
"""FirstData事实核查结果 → 小爪本地SQLite（文件已在本地）"""
import json, os, hashlib, glob, sqlite3, sys
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))
DB_PATH = os.path.expanduser("~/migration/xiaoxia/hermes_data.db")
LOG = os.path.expanduser("~/logs/firstdata_factcheck.log")
FACTCHECK_DIR = os.path.expanduser("~/clawd/reports/factchecks/")
SUMMARY_FILE = os.path.join(FACTCHECK_DIR, "daily_factcheck_summary.json")
os.makedirs(os.path.dirname(LOG), exist_ok=True)

def log(msg):
    ts = datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG, "a") as f: f.write(line + "\n")

def run():
    log("🔄 事实核查→SQLite开始")
    if not os.path.exists(SUMMARY_FILE):
        log("⚠️ 无summary文件，跳过")
        return
    with open(SUMMARY_FILE) as f: summary = json.load(f)
    date_key = summary["checked_at"][:10]
    log(f"📋 日期: {date_key} | 文件: {summary['total_files']} | 主张: {summary['total_claims']}")

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM firstdata_factcheck_daily WHERE date_key=?", (date_key,))
    if cur.fetchone()[0] > 0:
        log(f"⏭️ {date_key} 已存在，跳过")
        conn.close()
        return

    # 写daily汇总
    cur.execute("""INSERT OR REPLACE INTO firstdata_factcheck_daily
        (date_key,checked_at,total_rounds,total_files,total_claims,counts,avg_confidence,firstdata_matched,verified_rate,rounds)
        VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (date_key, summary["checked_at"], summary.get("total_rounds",0), summary["total_files"],
         summary["total_claims"], json.dumps(summary.get("counts",{}), ensure_ascii=False),
         summary.get("avg_confidence",0), summary.get("firstdata_matched",0),
         summary.get("verified_rate",0), json.dumps(summary.get("rounds",[]), ensure_ascii=False)))
    log(f"✅ Daily汇总写入: {date_key}")

    # 写明细
    files = sorted(glob.glob(os.path.join(FACTCHECK_DIR, "*_factcheck.json")))
    total_claims = 0
    for fp in files:
        with open(fp) as f: fc = json.load(f)
        fname = fc.get("file","")
        round_name = fname.replace(".md","") if fname else "unknown"
        checked_at = fc.get("checked_at", datetime.now(CST).isoformat())
        fconf = fc.get("confidence_score",0)
        for claim in fc.get("claims",[]):
            ct = claim.get("claim","")
            if not ct: continue
            ch = hashlib.sha256(ct.encode()).hexdigest()
            matched = claim.get("firstdata_sources",[])
            cur.execute("""INSERT OR REPLACE INTO firstdata_factchecks
                (claim_hash,claim_text,round_name,query_file,status,source_org,key_number,
                 confidence_score,evidence,firstdata_matched,matched_sources,verified_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (ch, ct, round_name, fname, claim.get("status","unverified"),
                 claim.get("source_org",""), str(claim.get("key_number","")) if claim.get("key_number") else "",
                 fconf, json.dumps(claim.get("evidence",[]), ensure_ascii=False),
                 len(matched), json.dumps(matched, ensure_ascii=False) if matched else "[]", checked_at))
            total_claims += 1
    conn.commit()
    cur.execute("SELECT COUNT(*) FROM firstdata_factchecks")
    total = cur.fetchone()[0]
    conn.close()
    log(f"✅ 明细: {total_claims}条 | 总{total}")

if __name__ == "__main__":
    run()
