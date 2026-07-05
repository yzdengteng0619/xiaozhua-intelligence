#!/usr/bin/env python3
"""
industry_rotation_l2.py — 行业轮动 L2 深挖层
==============================================

在 L1 扫描完成后追加一步深挖：
1. 从 L1 分析中提取关键数据点（市场规模、增速等）
2. 调用 firstdata_adapter.search_sources() 找权威数据源
3. 标注来源可信度（government/international/research/market）
4. 用权威数据源重新分析，输出带来源标注的深度报告
5. 存到 wiki/{industry}/deep/，DB注册 source_type=industry_rotation_deep

设计原则:
  - L2 可开关：--deep 参数触发，不加只跑 L1
  - FirstData 调用失败时静默降级，不阻塞 L1
  - L2 产出单独存目录，不覆盖 L1
"""

import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta

# 小爪环境路径
SCRIPTS_DIR = os.path.expanduser("~/clawd/scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

BJT = timezone(timedelta(hours=8))
BASE_DIR = os.path.expanduser("~/clawd")
WIKI_DIR = os.path.join(BASE_DIR, "knowledge", "web", "wiki")


def log(msg):
    """带时间戳的日志输出"""
    ts = datetime.now(BJT).strftime("%Y-%m-%d %H:%M BJT")
    print(f"[{ts}] [L2] {msg}")


# ==================== 关键数据点提取 ====================

def extract_data_points(l1_analysis):
    """
    从 L1 分析文本中提取关键数据点。

    返回格式: [{"point": "快消品市场规模5万亿", "keyword": "快消品市场", "context": "..."}]
    """
    if not l1_analysis:
        return []

    points = []

    # 模式1: 数字 + 单位（万亿/亿/百万/%/增速/增长）
    # 匹配 "XX行业市场规模达X万亿" "XX增长X%" 等
    number_patterns = [
        # 中文数字 + 单位
        r'([\u4e00-\u9fff]{2,10}(?:行业|市场|领域|品类|赛道)[\u4e00-\u9fff]{0,6}(?:规模|达|为|约|超过)?\s*(?:\d+\.?\d*)\s*(?:万亿|亿|百万|万))',
        # 增长率
        r'([\u4e00-\u9fff]{2,8}(?:增速|增长|涨幅|下降|下滑)\s*(?:达|为|约)?\s*(?:\d+\.?\d*)\s*%)',
        # 纯数字+单位
        r'((?:\d+\.?\d*)\s*(?:万亿|亿|百万|万)\s*[\u4e00-\u9fff]{2,8})',
    ]

    for pattern in number_patterns:
        for match in re.finditer(pattern, l1_analysis):
            point_text = match.group(1).strip()
            # 提取搜索关键词（去掉数字和单位）
            keyword = re.sub(r'[\d\.]+\s*(?:万亿|亿|百万|万|%)', '', point_text).strip()
            keyword = re.sub(r'(达|为|约|超过|增速|增长|涨幅|下降|下滑|规模)', '', keyword).strip()
            if len(keyword) < 2:
                keyword = point_text[:15]

            # 提取上下文（前后各50字）
            start = max(0, match.start() - 50)
            end = min(len(l1_analysis), match.end() + 50)
            context = l1_analysis[start:end].replace('\n', ' ').strip()

            points.append({
                "point": point_text,
                "keyword": keyword,
                "context": context,
            })

    # 去重（按 keyword）
    seen_keywords = set()
    unique_points = []
    for p in points:
        if p["keyword"] not in seen_keywords:
            seen_keywords.add(p["keyword"])
            unique_points.append(p)

    return unique_points[:5]  # 最多取5个关键数据点


# ==================== FirstData 权威源查询 ====================

def query_authoritative_sources(data_points, limit_per_point=3):
    """
    对每个关键数据点调用 firstdata_adapter.search_sources() 找权威数据源。

    返回格式: [{
        "data_point": {...},
        "sources": [{...source_info..., "relevance": "..."}],
        "best_authority": "government" | "international" | "research" | "market"
    }]

    FirstData 不可用时返回空列表（静默降级）。
    """
    try:
        from firstdata_adapter import search_sources, authority_weight
    except ImportError:
        log("⚠️ firstdata_adapter 不可用，L2 跳过权威源查询")
        return []

    results = []

    for dp in data_points:
        keyword = dp["keyword"]
        try:
            sources = search_sources(keyword, country="CN", limit=limit_per_point)
        except Exception as e:
            log(f"  FirstData查询失败 [{keyword}]: {e}")
            sources = []

        if not sources:
            # 尝试用更短的关键词
            short_kw = keyword[:6] if len(keyword) > 6 else keyword
            try:
                sources = search_sources(short_kw, country="CN", limit=limit_per_point)
            except Exception:
                sources = []

        if not sources:
            continue

        # 确定最佳权威等级
        authority_order = {"government": 0, "international": 1, "research": 2, "market": 3, "commercial": 4, "other": 5}
        best_authority = min(
            (s.get("authority_level", "other") for s in sources),
            key=lambda a: authority_order.get(a, 9),
            default="other"
        )

        # 标注每条来源的可信度
        annotated_sources = []
        for s in sources:
            annotated_sources.append({
                "source_id": s.get("source_id", ""),
                "name_zh": s.get("name_zh", "") or s.get("name_en", ""),
                "name_en": s.get("name_en", ""),
                "authority_level": s.get("authority_level", ""),
                "authority_weight": authority_weight(s.get("authority_level", "")),
                "website": s.get("website", ""),
                "api_url": s.get("api_url", ""),
                "description_zh": s.get("description_zh", ""),
                "country": s.get("country", ""),
            })

        results.append({
            "data_point": dp,
            "sources": annotated_sources,
            "best_authority": best_authority,
        })

    return results


# ==================== 深度分析 ====================

def generate_deep_analysis(industry_name, dim_name, l1_analysis, source_results, call_model_fn=None):
    """
    用权威数据源重新分析，输出带来源标注的深度报告。

    Args:
        call_model_fn: 可选的模型调用函数 (prompt → (text, provider))
                       如果不提供，生成模板报告（不调 LLM）
    """
    # 构建来源标注信息
    source_annotations = []
    for sr in source_results:
        dp = sr["data_point"]
        sources_text = ", ".join(
            f"{s['name_zh']}({s['authority_level']})" for s in sr["sources"][:2]
        )
        source_annotations.append(f"- 数据点「{dp['point']}」→ 权威来源: {sources_text}")

    annotations_block = "\n".join(source_annotations) if source_annotations else "（未找到匹配权威源）"

    if call_model_fn:
        # 用 LLM 生成深度分析
        prompt = f"""你是一名资深行业分析师。请基于以下 L1 分析和权威数据源，生成一份深度分析报告。

## L1 分析摘要
{l1_analysis[:3000]}

## 匹配的权威数据源
{annotations_block}

## 要求
1. 对 L1 中的关键数据点，用权威数据源进行交叉验证
2. 标注每个数据的来源可信度等级（government/international/research/market）
3. 如果权威源数据与 L1 数据有出入，指出差异
4. 输出格式（中文，300-600字）:

## 数据验证
- 对每个关键数据点，标注是否被权威源证实

## 深度洞察
- 基于权威数据的深层分析

## 来源可信度
- 总结本次分析的数据可靠性"""

        text, provider = call_model_fn(prompt)
        if provider != "error":
            return text
        log(f"  LLM分析失败，降级为模板报告")

    # 模板报告（不调 LLM）
    return f"""## 数据验证

{annotations_block}

## 深度洞察

基于 L1 分析和权威数据源交叉验证，{industry_name} - {dim_name} 的关键数据点已标注来源可信度。

## 来源可信度

本次分析引用的权威数据源等级分布:
{_summarize_authority_levels(source_results)}"""


def _summarize_authority_levels(source_results):
    """汇总来源等级分布"""
    from collections import Counter
    levels = Counter()
    for sr in source_results:
        for s in sr["sources"]:
            levels[s["authority_level"]] += 1

    if not levels:
        return "（无权威源匹配）"

    lines = []
    for level, count in levels.most_common():
        lines.append(f"- {level}: {count}个来源")
    return "\n".join(lines)


# ==================== L2 产出存储 ====================

def save_deep_to_wiki(industry_id, industry_name, dim_name, deep_analysis, source_results, queries=None):
    """
    保存 L2 深度分析到 wiki/{industry_id}/deep/ 目录。

    返回 filepath。DB 注册用 source_type=industry_rotation_deep。
    """
    today = datetime.now(BJT).strftime("%Y-%m-%d")
    deep_dir = os.path.join(WIKI_DIR, industry_id, "deep")
    os.makedirs(deep_dir, exist_ok=True)

    filename = f"{today}-{industry_id}-{dim_name}-deep.md"
    filepath = os.path.join(deep_dir, filename)

    # 构建来源标注表
    source_table_lines = [
        "| 数据点 | 权威来源 | 可信度等级 |",
        "|--------|----------|------------|",
    ]
    for sr in source_results:
        dp_text = sr["data_point"]["point"][:30]
        for s in sr["sources"][:2]:
            source_table_lines.append(
                f"| {dp_text} | {s['name_zh']} | {s['authority_level']} |"
            )

    if len(source_table_lines) == 2:
        source_table_lines.append("| （无匹配） | - | - |")

    source_table = "\n".join(source_table_lines)

    content = f"""---
source: industry_rotation_deep
industry: {industry_id}
industry_name: {industry_name}
dimension: {dim_name}
date: {today}
confidence: high
l2_layer: true
---

# {industry_name} - {dim_name}（深度分析）

> 本文为 L2 深挖层产出，基于 L1 分析 + FirstData 权威数据源交叉验证。

{deep_analysis}

---

## 权威数据源标注

{source_table}

## 数据点详情

"""
    for sr in source_results:
        dp = sr["data_point"]
        content += f"### {dp['point']}\n"
        content += f"- **上下文**: {dp['context']}\n"
        content += f"- **最佳权威等级**: {sr['best_authority']}\n"
        for s in sr["sources"]:
            content += f"  - [{s['authority_level']}] {s['name_zh']} — {s['website']}\n"
        content += "\n"

    if queries:
        content += "## L1 搜索查询\n"
        for q in queries:
            content += f"- {q}\n"

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    return filepath


def register_deep_to_db(filepath, basename):
    """
    注册 L2 产出到 pipeline_checklist.db，source_type=industry_rotation_deep。

    如果 register_web_research 不可用，静默跳过。
    """
    try:
        sys.path.insert(0, SCRIPTS_DIR)
        from web_research_db import register_web_research
        rel_path = os.path.relpath(filepath, os.path.expanduser("~/clawd/knowledge"))
        register_web_research(basename, rel_path, "industry_rotation_deep")
        return True
    except ImportError:
        log("  ⚠️ web_research_db 不可用，跳过DB注册")
        return False
    except Exception as e:
        log(f"  ⚠️ DB注册失败: {e}")
        return False


# ==================== L2 主入口 ====================

def run_l2_deep(industry_id, industry_name, dim_name, l1_analysis, queries=None, call_model_fn=None):
    """
    L2 深挖层主入口。

    在 L1 run_one_dimension 完成后调用，执行:
    1. 从 L1 分析提取关键数据点
    2. FirstData 查询权威源
    3. 深度分析
    4. 存到 wiki/{industry}/deep/
    5. DB 注册 source_type=industry_rotation_deep

    任何步骤失败都静默降级，不抛异常。

    Returns:
        dict: {"status": "ok"|"skipped"|"error", "filepath": ..., "data_points": N, "sources_found": N}
    """
    result = {"status": "skipped", "filepath": None, "data_points": 0, "sources_found": 0}

    if not l1_analysis or l1_analysis.startswith("[ERROR]"):
        log("L1 分析为空或出错，跳过 L2")
        return result

    try:
        # Step 1: 提取关键数据点
        log(f"提取关键数据点...")
        data_points = extract_data_points(l1_analysis)
        result["data_points"] = len(data_points)

        if not data_points:
            log("未提取到关键数据点，跳过 L2")
            return result

        log(f"  提取到 {len(data_points)} 个数据点: {[dp['point'][:20] for dp in data_points]}")

        # Step 2: FirstData 权威源查询
        log(f"查询权威数据源...")
        source_results = query_authoritative_sources(data_points)
        result["sources_found"] = sum(len(sr["sources"]) for sr in source_results)

        if not source_results:
            log("未找到匹配权威源，L2 降级为模板报告")

        # Step 3: 深度分析
        log(f"生成深度分析...")
        deep_analysis = generate_deep_analysis(
            industry_name, dim_name, l1_analysis, source_results, call_model_fn
        )

        # Step 4: 存到 wiki/{industry}/deep/
        log(f"保存 L2 产出...")
        filepath = save_deep_to_wiki(
            industry_id, industry_name, dim_name, deep_analysis, source_results, queries
        )
        result["filepath"] = filepath

        # Step 5: DB 注册
        basename = os.path.basename(filepath).replace(".md", "")
        register_deep_to_db(filepath, basename)

        log(f"✅ L2 完成: {filepath}")
        result["status"] = "ok"
        return result

    except Exception as e:
        log(f"❌ L2 异常（静默降级）: {e}")
        result["status"] = "error"
        return result
