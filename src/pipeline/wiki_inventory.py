#!/usr/bin/env python3
"""
wiki_inventory.py — 知识库行业覆盖盘点
========================================

扫描 web/wiki + reports/wiki，按行业轮动的11个行业ID输出覆盖画像。
供 industry_rotation_xiaozhua.py 在扫描前调用，决定本轮策略。

用法:
  python3 wiki_inventory.py                    # 全量盘点JSON
  python3 wiki_inventory.py --industry fmcg    # 单行业详情
  python3 wiki_inventory.py --summary          # 人类可读摘要
"""

import os
import json
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import Counter

BJT = timezone(timedelta(hours=8))
BASE_DIR = Path.home() / "clawd" / "knowledge"
WEB_WIKI = BASE_DIR / "web" / "wiki"
REPORTS_WIKI = BASE_DIR / "reports" / "wiki"
INVENTORY_FILE = Path.home() / "clawd" / "checkpoints" / "wiki_inventory.json"

# ==================== 行业映射 ====================
# 行业轮动ID → wiki目录名（可能多个目录对应一个行业）
INDUSTRY_DIR_MAP = {
    "fmcg": {
        "name": "快消品",
        "web_dirs": ["fmcg", "fast-moving-consumer-goods", "food", "food-beverage", "cosmetics", "beauty-skincare"],
        "report_dirs": ["food", "market-research"],
        "keywords": ["快消", "食品", "饮料", "日化", "美妆", "个护", "母婴", "FMcG"],
    },
    "auto": {
        "name": "汽车",
        "web_dirs": ["auto", "automotive", "automotive-marketing", "automotive-industry", "automotive-digital-transformation", "nev-marketing-playbook"],
        "report_dirs": ["auto", "industry"],
        "keywords": ["汽车", "新能源车", "电动车", "车企", "出行", "后市场"],
    },
    "finance": {
        "name": "金融",
        "web_dirs": ["finance"],
        "report_dirs": ["finance"],
        "keywords": ["金融", "银行", "保险", "证券", "金融科技", "支付"],
    },
    "tech": {
        "name": "互联网/科技",
        "web_dirs": ["tech", "ai", "ai-tech", "digital-marketing", "marketing-technology"],
        "report_dirs": ["tech", "ai", "marketing"],
        "keywords": ["科技", "互联网", "AI", "SaaS", "云计算", "平台"],
    },
    "durables": {
        "name": "耐用消费品",
        "web_dirs": ["durables"],
        "report_dirs": ["industry"],
        "keywords": ["家电", "3C", "数码", "家居", "家具", "耐用"],
    },
    "retail": {
        "name": "零售",
        "web_dirs": ["retail", "ecommerce", "e-commerce"],
        "report_dirs": ["ecommerce", "market-research"],
        "keywords": ["零售", "电商", "新零售", "即时零售", "O2O"],
    },
    "health": {
        "name": "医疗健康",
        "web_dirs": ["health", "healthcare"],
        "report_dirs": ["healthcare"],
        "keywords": ["医疗", "健康", "医药", "器械", "养老"],
    },
    "realestate": {
        "name": "房地产",
        "web_dirs": ["realestate"],
        "report_dirs": ["industry"],
        "keywords": ["房地产", "住宅", "商业地产", "物业"],
    },
    "industrial": {
        "name": "B2B/工业",
        "web_dirs": ["industrial", "b2b-marketing"],
        "report_dirs": ["industry", "B2B-marketing"],
        "keywords": ["制造", "企业服务", "供应链", "工业互联网", "B2B"],
    },
    "luxury": {
        "name": "时尚/奢侈品",
        "web_dirs": ["luxury", "beauty", "skincare", "美容个护"],
        "report_dirs": ["beauty", "market-research"],
        "keywords": ["奢侈", "时尚", "服装", "珠宝", "高端消费"],
    },
    "travel": {
        "name": "旅游",
        "web_dirs": ["travel"],
        "report_dirs": ["market-research"],
        "keywords": ["旅游", "酒店", "航空", "OTA", "文旅"],
    },
}

# 行业轮动的5个研究维度
RESEARCH_DIMENSIONS = [
    ("market_trend", "市场与趋势"),
    ("competitive", "竞争动态"),
    ("consumer", "消费者洞察"),
    ("innovation", "创新与营销"),
    ("policy", "政策与技术"),
]


# ==================== 扫描逻辑 ====================

def scan_directory(dir_path, pattern="*.md"):
    """扫描目录，返回文件列表 [{path, mtime, size, basename}]"""
    files = []
    if not dir_path.exists():
        return files
    for f in dir_path.rglob(pattern):
        if f.name.startswith("_") or f.name.startswith("index"):
            continue
        try:
            stat = f.stat()
            files.append({
                "path": str(f),
                "mtime": datetime.fromtimestamp(stat.st_mtime, tz=BJT).strftime("%Y-%m-%d"),
                "size": stat.st_size,
                "basename": f.stem,
            })
        except Exception:
            continue
    return files


def extract_keywords_from_file(filepath, max_chars=2000):
    """从文件头部提取关键词（frontmatter + 前几段）"""
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read(max_chars)
    except Exception:
        return set()

    keywords = set()
    # 中文词（2-8字）
    cn_words = re.findall(r'[\u4e00-\u9fff]{2,8}', content)
    keywords.update(cn_words)
    # 英文术语
    en_words = re.findall(r'\b[A-Za-z]{3,}\b', content)
    keywords.update(w.lower() for w in en_words)
    return keywords


def check_dimension_coverage(files, dimension_keyword):
    """检查文件列表中是否有覆盖某个维度的内容"""
    count = 0
    latest_date = None
    for f in files:
        name_lower = f["basename"].lower()
        # 文件名包含维度关键词
        if dimension_keyword.lower() in name_lower:
            count += 1
            if not latest_date or f["mtime"] > latest_date:
                latest_date = f["mtime"]
    return {"count": count, "latest": latest_date}


def scan_industry(industry_id):
    """扫描单个行业的知识库覆盖情况"""
    config = INDUSTRY_DIR_MAP.get(industry_id)
    if not config:
        return None

    result = {
        "industry_id": industry_id,
        "industry_name": config["name"],
        "web_files": [],
        "report_files": [],
        "web_total": 0,
        "report_total": 0,
        "total": 0,
        "latest_date": None,
        "dimensions": {},
        "coverage_level": "empty",  # empty / shallow / moderate / deep
    }

    # 扫描 web/wiki
    for d in config["web_dirs"]:
        dir_path = WEB_WIKI / d
        files = scan_directory(dir_path)
        result["web_files"].extend(files)
    result["web_total"] = len(result["web_files"])

    # 扫描 reports/wiki
    for d in config["report_dirs"]:
        dir_path = REPORTS_WIKI / d
        files = scan_directory(dir_path)
        result["report_files"].extend(files)
    result["report_total"] = len(result["report_files"])

    result["total"] = result["web_total"] + result["report_total"]

    # 找最新日期
    all_files = result["web_files"] + result["report_files"]
    if all_files:
        result["latest_date"] = max(f["mtime"] for f in all_files)

    # 检查每个研究维度的覆盖
    for dim_id, dim_name in RESEARCH_DIMENSIONS:
        dim_cn = dim_name
        coverage = check_dimension_coverage(result["web_files"], dim_cn)
        result["dimensions"][dim_id] = {
            "name": dim_name,
            "web_count": coverage["count"],
            "latest": coverage["latest"],
        }

    # 维度覆盖统计
    dims_with_content = sum(1 for d in result["dimensions"].values() if d["web_count"] > 0)
    result["dimension_coverage"] = f"{dims_with_content}/{len(RESEARCH_DIMENSIONS)}"
    
    # 覆盖等级以web/wiki维度覆盖为核心（reports是通用库不单独驱动策略）
    if result["web_total"] == 0 and dims_with_content == 0:
        result["coverage_level"] = "empty"
    elif dims_with_content <= 1:
        result["coverage_level"] = "shallow"
    elif dims_with_content <= 3:
        result["coverage_level"] = "moderate"
    else:
        result["coverage_level"] = "deep"

    # 清理大字段（不返回文件详情到JSON）
    del result["web_files"]
    del result["report_files"]

    return result


# ==================== 覆盖诊断 ====================

def diagnose_coverage(inventory):
    """基于盘点结果给出每个行业的调度建议"""
    for ind_id, data in inventory.items():
        level = data["coverage_level"]
        total = data["total"]
        latest = data.get("latest_date", "unknown")

        # 判断数据新鲜度
        if latest and latest != "unknown":
            days_old = (datetime.now(BJT) - datetime.strptime(latest, "%Y-%m-%d").replace(tzinfo=BJT)).days
        else:
            days_old = 999

        # 调度建议
        if level == "empty":
            strategy = "build"      # 从零建设
            scan_depth = "full"     # 全量5维度
        elif level == "shallow":
            strategy = "fill"       # 补充覆盖
            scan_depth = "full"     # 全量5维度
        elif level == "moderate":
            if days_old > 30:
                strategy = "refresh"    # 内容过时需刷新
                scan_depth = "priority"  # 优先补缺失维度
            else:
                strategy = "deepen"     # 已有基础，深挖
                scan_depth = "gap"      # 只补空缺维度
        else:  # deep
            if days_old > 60:
                strategy = "refresh"
                scan_depth = "l2_only"  # 只跑L2深挖
            else:
                strategy = "maintain"   # 维护状态
                scan_depth = "l2_only"

        data["strategy"] = strategy
        data["scan_depth"] = scan_depth
        data["days_since_update"] = days_old

    return inventory


# ==================== 智能查询 ====================

def get_scan_plan(industry_id, inventory=None):
    """为行业轮动提供本轮扫描计划
    
    Returns:
        dict: {
            "industry_id": str,
            "strategy": "build"|"fill"|"refresh"|"deepen"|"maintain",
            "scan_depth": "full"|"priority"|"gap"|"l2_only",
            "skip_dimensions": [dim_ids],  # 已有覆盖的维度（可跳过）
            "focus_dimensions": [dim_ids],  # 需要重点补的维度
            "existing_reports": [paths],    # 相关的PDF报告摘要（供L2引用）
            "total_existing": int,
        }
    """
    if inventory is None:
        inventory = build_inventory()

    data = inventory.get(industry_id)
    if not data:
        return {"error": f"Unknown industry: {industry_id}"}

    skip_dims = []
    focus_dims = []

    for dim_id, dim_info in data.get("dimensions", {}).items():
        if dim_info["web_count"] > 3:
            skip_dims.append(dim_id)
        elif dim_info["web_count"] == 0:
            focus_dims.append(dim_id)

    plan = {
        "industry_id": industry_id,
        "industry_name": data["industry_name"],
        "strategy": data.get("strategy", "build"),
        "scan_depth": data.get("scan_depth", "full"),
        "skip_dimensions": skip_dims,
        "focus_dimensions": focus_dims,
        "total_existing": data["total"],
        "coverage_level": data["coverage_level"],
        "latest_date": data.get("latest_date"),
        "dimension_coverage": data.get("dimension_coverage"),
    }

    # 找相关的reports/wiki摘要（供L2引用）
    config = INDUSTRY_DIR_MAP.get(industry_id, {})
    report_dirs = config.get("report_dirs", [])
    related_reports = []
    for d in report_dirs:
        dir_path = REPORTS_WIKI / d
        if dir_path.exists():
            for f in list(dir_path.glob("*.md"))[:5]:  # 最多取5个
                if not f.name.startswith("_"):
                    related_reports.append(str(f))
    plan["related_reports"] = related_reports[:5]

    return plan


# ==================== 主入口 ====================

def build_inventory():
    """全量盘点"""
    inventory = {}
    for ind_id in INDUSTRY_DIR_MAP:
        data = scan_industry(ind_id)
        if data:
            inventory[ind_id] = data
    return diagnose_coverage(inventory)


def save_inventory(inventory):
    """保存盘点结果"""
    INVENTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(INVENTORY_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "generated_at": datetime.now(BJT).isoformat(),
            "industries": inventory,
        }, f, ensure_ascii=False, indent=2)
    return INVENTORY_FILE


def print_summary(inventory):
    """人类可读摘要"""
    print("=" * 60)
    print("知识库行业覆盖盘点")
    print(f"生成时间: {datetime.now(BJT).strftime('%Y-%m-%d %H:%M BJT')}")
    print("=" * 60)
    
    strategy_emoji = {
        "build": "🆕", "fill": "🔧", "refresh": "🔄",
        "deepen": "🔬", "maintain": "✅",
    }
    level_emoji = {
        "empty": "❌", "shallow": "🔴", "moderate": "🟡", "deep": "🟢",
    }

    for ind_id, data in inventory.items():
        emoji = level_emoji.get(data["coverage_level"], "?")
        strat = strategy_emoji.get(data.get("strategy", ""), "?")
        print(f"\n{emoji} {data['industry_name']} ({ind_id})")
        print(f"   总量: {data['total']} 页 (web:{data['web_total']} + reports:{data['report_total']})")
        print(f"   覆盖: {data.get('dimension_coverage', '?')} 维度 | 最新: {data.get('latest_date', 'N/A')}")
        print(f"   策略: {strat} {data.get('strategy', '?')} | 扫描: {data.get('scan_depth', '?')}")
        
        # 维度详情
        dims = data.get("dimensions", {})
        dim_str = " | ".join(
            f"{d['name']}:{d['web_count']}" for d in dims.values()
        )
        print(f"   维度: {dim_str}")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="知识库行业覆盖盘点")
    parser.add_argument("--industry", "-i", help="指定行业ID")
    parser.add_argument("--summary", "-s", action="store_true", help="人类可读摘要")
    parser.add_argument("--plan", "-p", help="获取行业扫描计划")
    parser.add_argument("--save", action="store_true", help="保存到文件")
    args = parser.parse_args()

    if args.industry:
        inventory = {}
        data = scan_industry(args.industry)
        if data:
            inventory[args.industry] = diagnose_coverage({args.industry: data})[args.industry]
            print(json.dumps(inventory[args.industry], ensure_ascii=False, indent=2))
        else:
            print(f"Unknown industry: {args.industry}")
    elif args.plan:
        plan = get_scan_plan(args.plan)
        print(json.dumps(plan, ensure_ascii=False, indent=2))
    else:
        inventory = {}
        for ind_id in INDUSTRY_DIR_MAP:
            data = scan_industry(ind_id)
            if data:
                inventory[ind_id] = data
        inventory = diagnose_coverage(inventory)

        if args.summary:
            print_summary(inventory)
        else:
            print(json.dumps(inventory, ensure_ascii=False, indent=2))

        if args.save:
            path = save_inventory(inventory)
            print(f"\n已保存到 {path}", file=sys.stderr)
