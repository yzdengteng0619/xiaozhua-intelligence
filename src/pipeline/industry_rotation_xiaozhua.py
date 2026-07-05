#!/usr/bin/env python3
"""
小爪行业轮动研究脚本 v1.1
============================
参考小虾行业轮动设计，适配小爪环境。

11个L1行业按序循环，每次cron触发跑1个行业的1个维度。
每次调用: 搜索5-8条查询 → MiniMax分析 → 写入wiki知识库

L2 深挖层（v1.1新增）:
  加 --deep 参数，L1完成后追加 FirstData 权威源查询 + 深度分析
  L2 产出存 wiki/{industry}/deep/，不覆盖L1
  FirstData 不可用时静默降级

运行方式:
  python3 industry_rotation_xiaozhua.py          # 自动运行（检查时间窗口），只跑L1
  python3 industry_rotation_xiaozhua.py --force   # 忽略时间检查，只跑L1
  python3 industry_rotation_xiaozhua.py --deep    # 跑L1+L2（建议每天1-2次）
  python3 industry_rotation_xiaozhua.py --force --deep  # 忽略时间检查，跑L1+L2

cron建议:
  每2h触发一次（只跑L1）: python3 industry_rotation_xiaozhua.py
  每天2次深挖（跑L1+L2）: python3 industry_rotation_xiaozhua.py --deep
  时间窗口 00:00-14:00 UTC（08:00-22:00 BJT）
"""

import os, sys, json, time, subprocess
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.expanduser("~/clawd/scripts"))
from ll_longcat import call_ll
from web_research_db import register_web_research
try:
    from wiki_inventory import get_scan_plan, build_inventory
except ImportError:
    get_scan_plan = None
    build_inventory = None

BJT = timezone(timedelta(hours=8))

BASE_DIR = os.path.expanduser("~/clawd")
CHECKPOINT_FILE = os.path.join(BASE_DIR, "checkpoints", "industry_rotation.json")
WIKI_DIR = os.path.join(BASE_DIR, "knowledge/web", "wiki")
SEARCH_SCRIPT = os.path.join(BASE_DIR, "scripts", "search_hub.py")

# ==================== 行业配置 ====================

L1_INDUSTRIES = [
    ("fmcg", "快消品", "快速消费品行业: 食品饮料、日化、美妆个护、母婴"),
    ("auto", "汽车", "汽车行业: 新能源车、传统车企、出行服务、汽车后市场"),
    ("finance", "金融", "金融行业: 银行、保险、证券、金融科技、支付"),
    ("tech", "互联网/科技", "互联网与科技行业: 平台经济、AI、SaaS、云计算"),
    ("durables", "耐用消费品", "耐用消费品: 家电、3C数码、家居家具"),
    ("retail", "零售", "零售行业: 电商、线下零售、新零售、即时零售"),
    ("health", "医疗健康", "医疗健康: 医药、医疗器械、健康管理、养老"),
    ("realestate", "房地产", "房地产: 住宅开发、商业地产、物业管理"),
    ("industrial", "B2B/工业", "B2B与工业: 制造业、企业服务、供应链、工业互联网"),
    ("luxury", "时尚/奢侈品", "时尚与奢侈品: 服装、珠宝、美妆、高端消费"),
    ("travel", "旅游", "旅游与出行: 酒店、航空、OTA、文旅"),
]

# 每个行业的研究维度（精简为5个，保证每次cron能完成1-2个）
RESEARCH_DIMENSIONS = [
    ("market_trend", "市场与趋势", "市场规模、增长趋势、行业格局变化"),
    ("competitive", "竞争动态", "主要玩家、市场份额、竞争策略变化"),
    ("consumer", "消费者洞察", "消费者行为变化、偏好迁移、新需求"),
    ("innovation", "创新与营销", "营销创新、内容策略、渠道变革、品牌案例"),
    ("policy", "政策与技术", "监管政策、技术变革、新赛道机会"),
]

# 每个维度的搜索查询数
QUERIES_PER_DIMENSION = 3


# ==================== Focus Mode ====================
FOCUS_CONFIG = os.path.join(BASE_DIR, "config", "industry_focus.json")

def get_effective_industries():
    if os.path.exists(FOCUS_CONFIG):
        with open(FOCUS_CONFIG) as f:
            fc = json.load(f)
        if fc.get("enabled") and fc.get("focus_industries"):
            focus_ids = [x["id"] for x in fc["focus_industries"]]
            focused = [ind for ind in L1_INDUSTRIES if ind[0] in focus_ids]
            return focused, True
    return L1_INDUSTRIES, False

# ==================== 核心逻辑 ====================

def log(msg):
    ts = datetime.now(BJT).strftime("%Y-%m-%d %H:%M BJT")
    print(f"[{ts}] {msg}")

def load_checkpoint():
    if os.path.exists(CHECKPOINT_FILE):
        with open(CHECKPOINT_FILE) as f:
            return json.load(f)
    return {
        "industry_idx": 0,      # 当前L1索引
        "dim_idx": 0,           # 当前维度索引
        "round": 0,             # 当前行业已跑轮数
        "total_rounds": 0,      # 总完成数
        "last_run": None,
        "completed_industries": [],
    }

def save_checkpoint(cp):
    os.makedirs(os.path.dirname(CHECKPOINT_FILE), exist_ok=True)
    cp["last_run"] = datetime.now(BJT).isoformat()
    with open(CHECKPOINT_FILE, "w") as f:
        json.dump(cp, f, indent=2, ensure_ascii=False)

def in_time_window():
    """检查当前是否在运行窗口内: 00:00-14:00 UTC = 08:00-22:00 BJT"""
    now_utc = datetime.now(timezone.utc)
    h = now_utc.hour
    return 0 <= h < 14  # UTC 00:00-14:00

def search(query, num=3):
    """mmx search (主力) → search_hub (fallback)"""
    # 主力: mmx search query (MiniMax web search)
    try:
        import subprocess
        r = subprocess.run(
            ["mmx", "search", "query", query, "--limit", str(num)],
            capture_output=True, text=True, timeout=15
        )
        if r.returncode == 0:
            data = json.loads(r.stdout)
            snippets = []
            for item in data.get("organic", [])[:num]:
                s = item.get("snippet", "")
                title = item.get("title", "")
                if s:
                    snippets.append(f"{title}: {s[:400]}")
            if snippets:
                return "\n".join(snippets)
    except Exception as e:
        log(f"  mmx搜索失败: {e}")
    
    # fallback: search_hub
    try:
        sys.path.insert(0, os.path.dirname(SEARCH_SCRIPT))
        from search_hub import multi_search
        results = multi_search(query, num=num, lang="zh")
        snippets = []
        for r in (results if isinstance(results, list) else results.get("results", [])):
            if isinstance(r, dict):
                s = r.get("snippet", "") or r.get("content", "") or r.get("text", "")
                if s:
                    snippets.append(s[:500])
        if snippets:
            return "\n".join(snippets[:3])
    except Exception as e:
        log(f"  search_hub也失败: {e}")
    return ""


def call_model(prompt, timeout=90):
    """LongCat主力 + SenseNova备选"""
    text, provider = call_ll(prompt, timeout=timeout)
    if provider == "error":
        return f"[ERROR] Both providers failed", "error"
    return text, provider
def generate_queries(industry_id, industry_name, dim_name, dim_desc):
    """生成搜索查询"""
    prompt = f"""你是一名行业分析师。请为以下研究任务生成{QUERIES_PER_DIMENSION}条中文搜索查询。

行业: {industry_name} ({industry_id})
研究维度: {dim_name} - {dim_desc}

要求:
1. 查询要具体、可搜索，能搜到2025-2026年的中文信息
2. 每条查询聚焦一个具体子方向
3. 包含行业关键词+趋势/变化/案例等动词
4. 输出格式：每行一条查询

请生成{QUERIES_PER_DIMENSION}条查询:"""
    
    text, mode = call_model(prompt)
    if mode == "error":
        log(f"  ❌ 模型调用失败: {text[:200]}")
        return []
    
    queries = [q.strip().lstrip("0123456789.").strip() for q in text.split("\n") if q.strip() and len(q.strip()) > 5]
    return queries[:QUERIES_PER_DIMENSION]

def analyze_results(industry_name, dim_name, search_results):
    """用MiniMax分析搜索结果"""
    results_text = "\n\n".join([f"查询{i+1}:\n{r[:1500]}" for i, r in enumerate(search_results) if r])
    
    prompt = f"""你是一名行业分析师。请对以下关于「{industry_name} - {dim_name}」的搜索结果进行分析。

搜索结果:
{results_text[:6000]}

请输出结构化分析（中文，200-500字）:
## 关键发现
- 列出3-5条最重要的发现

## 趋势信号
- 识别2-3个值得关注的趋势

## 数据亮点
- 如有具体数字，提取关键数据点

## 置信度
- high/medium/low"""
    
    text, mode = call_model(prompt)
    return text

def wiki_path(industry_id):
    """确保wiki分类目录存在"""
    path = os.path.join(WIKI_DIR, industry_id)
    os.makedirs(path, exist_ok=True)
    return path

def save_to_wiki(industry_id, industry_name, dim_name, analysis, queries):
    """保存分析结果到wiki"""
    today = datetime.now(BJT).strftime("%Y-%m-%d")
    filename = f"{today}-{industry_id}-{dim_name}.md"
    filepath = os.path.join(wiki_path(industry_id), filename)
    
    content = f"""---
source: industry_rotation
industry: {industry_id}
industry_name: {industry_name}
dimension: {dim_name}
date: {today}
confidence: medium
---

# {industry_name} - {dim_name}

{analysis}

---
**搜索查询:**
"""
    for q in queries:
        content += f"- {q}\n"
    
    with open(filepath, "w") as f:
        f.write(content)
    
    # 注册到DB
    rel_path = os.path.relpath(filepath, os.path.expanduser("~/clawd/knowledge"))
    basename = os.path.basename(filepath).replace(".md", "")
    register_web_research(basename, rel_path, "industry_rotation")
    
    return filepath

def run_one_dimension(cp, deep=False):
    """运行一个行业的一个维度
    
    Args:
        cp: checkpoint dict
        deep: 是否在 L1 完成后跑 L2 深挖层
    """
    effective_industries, _ = get_effective_industries()
    industry_id, industry_name, industry_desc = effective_industries[cp["industry_idx"] % len(effective_industries)]
    dim_id, dim_name, dim_desc = RESEARCH_DIMENSIONS[cp["dim_idx"]]
    
    log(f"🎯 {industry_name} → {dim_name}")
    
    # Step 1: 生成查询
    log(f"  生成查询...")
    queries = generate_queries(industry_id, industry_name, dim_name, dim_desc)
    if not queries:
        log(f"  ⚠️ 查询生成为空，跳过")
        return False
    
    log(f"  查询: {len(queries)}条")
    for q in queries:
        log(f"    - {q}")
    
    # Step 2: 搜索
    search_results = []
    for qi, q in enumerate(queries):
        log(f"  搜索 [{qi+1}/{len(queries)}]: {q[:40]}...")
        result = search(q, num=3)
        if result:
            search_results.append(result)
        time.sleep(1)  # 避免搜索限流
    
    if not search_results:
        log(f"  ⚠️ 全部搜索无结果，跳过")
        return False
    
    # Step 3: MiniMax分析
    log(f"  分析 ({len(search_results)}条结果)...")
    analysis = analyze_results(industry_name, dim_name, search_results)
    if analysis.startswith("[ERROR]"):
        log(f"  ❌ 分析失败: {analysis}")
        return False
    
    # Step 4: 写入wiki
    filepath = save_to_wiki(industry_id, industry_name, dim_name, analysis, queries)
    log(f"  ✅ 已保存到 {filepath}")
    
    # Step 4.5: L2 深挖层（可选）
    if deep:
        try:
            from industry_rotation_l2 import run_l2_deep
            log(f"  🔬 L2 深挖层启动...")
            l2_result = run_l2_deep(
                industry_id=industry_id,
                industry_name=industry_name,
                dim_name=dim_name,
                l1_analysis=analysis,
                queries=queries,
                call_model_fn=call_model,
            )
            if l2_result["status"] == "ok":
                log(f"  🔬 L2 完成: {l2_result['data_points']}数据点, {l2_result['sources_found']}权威源")
            elif l2_result["status"] == "skipped":
                log(f"  🔬 L2 跳过: {l2_result.get('data_points', 0)}数据点")
            else:
                log(f"  🔬 L2 异常但L1不受影响")
        except ImportError:
            log(f"  ⚠️ industry_rotation_l2 模块不可用，跳过 L2")
        except Exception as e:
            log(f"  ⚠️ L2 异常（不阻塞L1）: {e}")
    
    # Step 5: 更新checkpoint
    cp["dim_idx"] += 1
    if cp["dim_idx"] >= len(RESEARCH_DIMENSIONS):
        cp["dim_idx"] = 0
        eff_ind, _ = get_effective_industries()
        cp["industry_idx"] = (cp["industry_idx"] + 1) % len(eff_ind)
        cp["round"] += 1
        cp["completed_industries"].append({
            "industry": industry_id,
            "name": industry_name,
            "completed_at": datetime.now(BJT).isoformat()
        })
    cp["total_rounds"] += 1
    save_checkpoint(cp)
    
    return True

def main():
    force = "--force" in sys.argv
    deep = "--deep" in sys.argv

    # 时间窗口检查
    if not force and not in_time_window():
        utc_now = datetime.now(timezone.utc)
        log(f"⏰ 不在运行窗口 (当前UTC {utc_now.hour}:00, 窗口00:00-14:00)，跳过")
        return
    
    # 加载checkpoint
    cp = load_checkpoint()
    effective_industries, is_focus = get_effective_industries()
    industry_id, industry_name, _ = effective_industries[cp["industry_idx"] % len(effective_industries)]
    
    # ===== 知识库覆盖诊断 =====
    scan_plan = None
    if get_scan_plan:
        try:
            scan_plan = get_scan_plan(industry_id)
        except Exception as e:
            log(f"⚠️ 覆盖诊断失败: {e}")
    
    if scan_plan:
        strategy = scan_plan.get("strategy", "build")
        coverage = scan_plan.get("coverage_level", "unknown")
        total = scan_plan.get("total_existing", 0)
        log(f"📋 行业轮动启动")
        log(f"   当前: {industry_name} (维度{cp['dim_idx']+1}/{len(RESEARCH_DIMENSIONS)})")
        log(f"   覆盖: {coverage} ({total}页) → 策略: {strategy}")
        log(f"   已完成: {cp['total_rounds']}轮")
        
        # 覆盖诊断决策
        if strategy == "maintain" and not deep:
            log(f"   ✅ 覆盖充足，跳过L1扫描（加 --deep 强制L2深挖）")
            # 仍推进checkpoint到下一行业
            cp["dim_idx"] += 1
            if cp["dim_idx"] >= len(RESEARCH_DIMENSIONS):
                cp["dim_idx"] = 0
                cp["industry_idx"] = (cp["industry_idx"] + 1) % len(effective_industries)
            save_checkpoint(cp)
            return
        elif strategy in ("fill", "build"):
            log(f"   🔧 覆盖不足，全量5维度扫描")
            cp["dim_idx"] = 0  # 从第1维度开始
        elif strategy in ("deepen", "refresh"):
            focus = scan_plan.get("focus_dimensions", [])
            skip = scan_plan.get("skip_dimensions", [])
            log(f"   🔬 重点维度: {focus} | 跳过: {skip}")
    else:
        log(f"📋 行业轮动启动（无覆盖诊断）")
        log(f"   当前: {industry_name} (维度{cp['dim_idx']+1}/{len(RESEARCH_DIMENSIONS)})")
        log(f"   已完成: {cp['total_rounds']}轮")
    
    # 跑一个维度
    start = time.time()
    ok = run_one_dimension(cp, deep=deep)
    elapsed = time.time() - start
    
    if ok:
        log(f"✨ 完成 (耗时{elapsed:.0f}s)")
    else:
        log(f"⚠️ 未完成 (耗时{elapsed:.0f}s)")
    
    # 状态汇总
    eff_ind, _ = get_effective_industries()
    current = eff_ind[cp["industry_idx"] % len(eff_ind)]
    log(f"📊 下次触发 → {current[1]} (维度{cp['dim_idx']+1}/{len(RESEARCH_DIMENSIONS)})")

if __name__ == "__main__":
    main()
