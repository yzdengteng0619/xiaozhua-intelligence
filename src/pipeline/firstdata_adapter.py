#!/usr/bin/env python3
"""
FirstData Adapter v2 — Pipeline集成层
===================================
三层数据源：MCP在线(有token) → RDS MySQL(持久化) → 本地JSON缓存(离线fallback)

部署位置: ~/clawd/scripts/firstdata_adapter.py (小爪端)
"""

import json
import os
import sys
from pathlib import Path

CACHE_DIR = Path.home() / ".firstdata_cache"
CACHE_INDEX = CACHE_DIR / "sources_index.json"

# =========== 数据层 ===========

# RDS连接配置（通过rds-tunnel SSH隧道，仅Hermes本地可用）
RDS_CONFIG = {
    "host": "127.0.0.1",
    "port": 3307,
    "user": "admin",
    "password": "Sunnydt0619",
    "database": "hermes_data",
    "connect_timeout": 3,
    "charset": "utf8mb4"
}


def _rds_available():
    """检查RDS是否可达"""
    try:
        import pymysql
        conn = pymysql.connect(**RDS_CONFIG)
        conn.close()
        return True
    except:
        return False


def _search_rds(query, country=None, authority=None, has_api=None, limit=10):
    """通过RDS全文检索搜索"""
    try:
        import pymysql
        conn = pymysql.connect(**RDS_CONFIG)
        cur = conn.cursor(pymysql.cursors.DictCursor)
        
        conditions = []
        params = []
        
        # MySQL ngram全文搜索
        search_terms = "+" + " +".join(query.split())
        conditions.append(
            "(MATCH(name_zh, description_zh) AGAINST (%s IN BOOLEAN MODE) "
            "OR MATCH(name_en, description_en) AGAINST (%s IN BOOLEAN MODE) "
            "OR id LIKE %s "
            "OR JSON_SEARCH(tags, 'one', %s) IS NOT NULL)"
        )
        params.extend([search_terms, search_terms, f"%{query}%", query])
        
        if country:
            conditions.append("country = %s")
            params.append(country)
        if authority:
            conditions.append("authority_level = %s")
            params.append(authority)
        if has_api is not None:
            conditions.append("has_api = %s")
            params.append(1 if has_api else 0)
        
        sql = f"""SELECT id, name_zh, name_en, authority_level, website, api_url,
                         description_zh, update_frequency, country, domains
                  FROM firstdata_sources 
                  WHERE {' AND '.join(conditions)}
                  ORDER BY FIELD(authority_level, 'government','international','research','market','commercial','other'),
                           MATCH(name_zh, description_zh) AGAINST (%s IN BOOLEAN MODE) DESC
                  LIMIT {limit}"""
        params.append(search_terms)
        
        cur.execute(sql, params)
        results = cur.fetchall()
        cur.close()
        conn.close()
        return results
    except Exception as e:
        print(f"[FirstData] RDS搜索失败: {e}", file=sys.stderr)
        return None


def _search_cache(query, country=None, authority=None, has_api=None, limit=10):
    """本地JSON缓存搜索（xiaozhua离线模式）"""
    if not CACHE_INDEX.exists():
        return []
    
    try:
        with open(CACHE_INDEX) as f:
            sources = json.load(f)
    except:
        return []
    
    query_lower = query.lower().strip()
    
    # 将长查询拆分为多个关键词（中文2-4字分词 + 英文单词）
    import re as _re
    chinese_words = _re.findall(r'[\u4e00-\u9fff]{2,4}', query_lower)
    english_words = _re.findall(r'[a-z]{2,}', query_lower)
    all_keywords = list(set(chinese_words + english_words + [query_lower]))
    
    matched = []
    
    for sid, src in sources.items():
        score = 0
        
        name_en = src.get("name", {}).get("en", "").lower()
        name_zh = src.get("name", {}).get("zh", "")
        desc_en = src.get("description", {}).get("en", "").lower()
        desc_zh = src.get("description", {}).get("zh", "")
        tags = [t.lower() for t in src.get("tags", [])]
        
        # 名称匹配（权重最高）- 检查所有关键词
        name_match = any(kw in name_en or kw in name_zh for kw in all_keywords)
        if name_match:
            score += 4
        # 描述匹配
        desc_match = any(kw in desc_en or kw in desc_zh for kw in all_keywords)
        if desc_match:
            score += 2
        # Tag匹配
        tag_match = any(any(kw in t for t in tags) for kw in all_keywords)
        if tag_match:
            score += 2
        # ID匹配
        id_match = any(kw in sid.lower() for kw in all_keywords)
        if id_match:
            score += 3
        
        # 过滤
        if country:
            src_country = src.get("country")
            if src_country and src_country.upper() != country.upper():
                continue
        if authority and src.get("authority_level", "") != authority:
            continue
        if has_api is not None:
            has = 1 if src.get("api_url") and str(src.get("api_url", "")).strip() else 0
            if has != (1 if has_api else 0):
                continue
        
        if score > 0:
            matched.append({
                "source_id": sid,
                "name_zh": name_zh,
                "name_en": name_en,
                "authority_level": src.get("authority_level", ""),
                "update_frequency": src.get("update_frequency", ""),
                "website": src.get("website", ""),
                "api_url": src.get("api_url", ""),
                "description_zh": desc_zh,
                "country": src.get("country", ""),
                "domains": src.get("domains", []),
                "_score": score
            })
    
    # 权威等级排序: government > international > research > market > commercial > other
    auth_order = {"government": 0, "international": 1, "research": 2, "market": 3, "commercial": 4, "other": 5}
    matched.sort(key=lambda x: (auth_order.get(x["authority_level"], 9), -x["_score"]))
    
    return matched[:limit]


def _get_source_rds(source_id):
    """从RDS获取单个数据源"""
    try:
        import pymysql
        conn = pymysql.connect(**RDS_CONFIG)
        cur = conn.cursor(pymysql.cursors.DictCursor)
        cur.execute("SELECT * FROM firstdata_sources WHERE id = %s", (source_id,))
        result = cur.fetchone()
        cur.close()
        conn.close()
        return result
    except:
        return None


def _get_source_cache(source_id):
    """从本地缓存获取单个数据源"""
    if not CACHE_INDEX.exists():
        return None
    try:
        with open(CACHE_INDEX) as f:
            sources = json.load(f)
        return sources.get(source_id)
    except:
        return None


# =========== 公共API ===========

def search_sources(query, domains=None, country=None, authority_level=None, limit=5, has_api=None):
    """
    搜索权威数据源
    优先级: RDS(本地) → JSON缓存(xiaozhua)
    """
    # RDS优先（在Hermes本地运行时）
    if _rds_available():
        results = _search_rds(query, country, authority_level, has_api, limit)
        if results is not None:
            return [{
                "source_id": r["id"],
                "name_zh": r.get("name_zh", ""),
                "name_en": r.get("name_en", ""),
                "authority_level": r.get("authority_level", ""),
                "update_frequency": r.get("update_frequency", ""),
                "website": r.get("website", ""),
                "api_url": r.get("api_url", ""),
                "description_zh": r.get("description_zh", ""),
                "country": r.get("country", ""),
                "domains": json.loads(r.get("domains") or "[]") if isinstance(r.get("domains"), str) else (r.get("domains") or []),
            } for r in results]
    
    # 本地JSON缓存fallback
    return _search_cache(query, country, authority_level, has_api, limit)


def get_source(source_id):
    """获取单个数据源完整信息"""
    if _rds_available():
        result = _get_source_rds(source_id)
        if result:
            return result
    
    return _get_source_cache(source_id)


def verify_claim(claim_text, domain_hint=None):
    """
    为事实核查提供权威来源建议
    提取claim中的关键词，返回Top-3最相关的权威数据源
    """
    keywords = _extract_keywords(claim_text)
    
    all_results = []
    for kw in keywords:
        sources = search_sources(kw, country="CN", limit=3)
        for s in sources:
            s["relevance_note"] = f"关键词: {kw}"
            all_results.append(s)
    
    # 去重
    seen = set()
    unique = []
    for r in all_results:
        if r["source_id"] not in seen:
            seen.add(r["source_id"])
            unique.append(r)
    
    return unique[:3]


def _extract_keywords(text):
    """从claim文本中提取搜索关键词"""
    import re
    
    # 提取组织机构名（英文大写缩写）
    orgs = re.findall(r'\b[A-Z][A-Z.]{2,}(?:[-\s][A-Z][A-Z.]{2,})*\b', text)
    
    # 提取中文领域/行业关键词
    domains = re.findall(r'[\u4e00-\u9fff]{2,8}(?:行业|市场|数据|报告|领域|消费|零售|经济|金融|贸易|投资|科技|健康|能源|制造|教育|地产)', text)
    
    # 提取英文术语（如GDP, CPI, M2等）
    terms = re.findall(r'\b[A-Z]{2,5}\b', text)
    
    keywords = list(set(orgs + domains + terms))
    return keywords[:5] if keywords else [text[:30]]


def authority_weight(authority_level):
    """权威等级权重（用于评分）"""
    weights = {
        "government": 10,
        "international": 8,
        "research": 6,
        "market": 4,
        "commercial": 2,
        "other": 0
    }
    return weights.get(authority_level, 0)


# =========== CLI ===========

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="FirstData Adapter CLI")
    parser.add_argument("action", choices=["search", "get", "verify", "stats", "build_cache"],
                       help="操作")
    parser.add_argument("--query", "-q", help="搜索关键词")
    parser.add_argument("--source-id", "-s", help="数据源ID")
    parser.add_argument("--country", "-c", help="国家代码")
    parser.add_argument("--authority", "-a", help="权威等级过滤")
    parser.add_argument("--has-api", action="store_true", help="只显示有API的")
    parser.add_argument("--limit", "-l", type=int, default=5, help="返回数量")
    parser.add_argument("--json", action="store_true", help="JSON输出")
    
    args = parser.parse_args()
    
    if args.action == "search":
        if not args.query:
            print("需要 --query 参数")
            sys.exit(1)
        results = search_sources(args.query, country=args.country, 
                                authority_level=args.authority,
                                has_api=args.has_api if args.has_api else None,
                                limit=args.limit)
        data_source = "RDS" if _rds_available() else "本地缓存"
        print(f"[{data_source}] 搜索 '{args.query}': {len(results)} 个结果\n")
        if args.json:
            print(json.dumps(results, ensure_ascii=False, indent=2))
        else:
            for r in results:
                name = r.get('name_zh', '') or r.get('name_en', r['source_id'])
                print(f"  [{r['authority_level']:15s}] {name}")
                print(f"    ID: {r['source_id']}")
                print(f"    网站: {r.get('website', '-')}")
                if r.get('api_url'):
                    print(f"    API:  {r['api_url']}")
                print()
    
    elif args.action == "get":
        if not args.source_id:
            print("需要 --source-id 参数")
            sys.exit(1)
        src = get_source(args.source_id)
        if src:
            if args.json:
                print(json.dumps(src, ensure_ascii=False, indent=2))
            else:
                name = src.get('name_zh', '') or src.get('name_en', src['source_id'])
                print(f"名称: {name}")
                print(f"权威: {src.get('authority_level', '')}")
                print(f"网站: {src.get('website', '-')}")
                print(f"API:  {src.get('api_url', '-')}")
                print(f"更新: {src.get('update_frequency', '')}")
                print(f"国家: {src.get('country', '')}")
        else:
            print(f"未找到: {args.source_id}")
    
    elif args.action == "verify":
        if not args.query:
            print("需要 --query 参数（claim文本）")
            sys.exit(1)
        results = verify_claim(args.query)
        print(f"Claim: {args.query}")
        print(f"建议权威来源: {len(results)} 个\n")
        for r in results:
            name = r.get('name_zh', '') or r.get('name_en', r['source_id'])
            print(f"  [{r['authority_level']:15s}] {name}")
            print(f"    {r.get('relevance_note', '')}")
            print()
    
    elif args.action == "stats":
        if _rds_available():
            import pymysql
            conn = pymysql.connect(**RDS_CONFIG)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM firstdata_sources")
            total = cur.fetchone()[0]
            cur.execute("SELECT authority_level, COUNT(*) FROM firstdata_sources GROUP BY authority_level ORDER BY COUNT(*) DESC")
            auths = cur.fetchall()
            cur.execute("SELECT COUNT(*) FROM firstdata_sources WHERE has_api = 1")
            has_api = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM firstdata_sources WHERE country = 'CN'")
            cn = cur.fetchone()[0]
            cur.close()
            conn.close()
            print(f"FirstData 数据源统计 (RDS)")
            print(f"  总数: {total}")
            print(f"  有API: {has_api}")
            print(f"  中国: {cn}")
            print(f"\n  权威等级分布:")
            for a,c in auths: print(f"    {a}: {c}")
        elif CACHE_INDEX.exists():
            with open(CACHE_INDEX) as f:
                sources = json.load(f)
            from collections import Counter
            auth = Counter(s.get("authority_level","") for s in sources.values())
            print(f"FirstData 数据源统计 (本地缓存)")
            print(f"  总数: {len(sources)}")
            print(f"  有API: {sum(1 for s in sources.values() if s.get('api_url'))}")
            print(f"  中国: {sum(1 for s in sources.values() if s.get('country','')=='CN')}")
            print(f"\n  权威等级分布:")
            for k,v in auth.most_common(): print(f"    {k}: {v}")
        else:
            print("无可用数据源（未构建缓存）")
    
    elif args.action == "build_cache":
        print("请在Hermes本地运行: python3 /tmp/sync_firstdata_to_rds.py sync")
