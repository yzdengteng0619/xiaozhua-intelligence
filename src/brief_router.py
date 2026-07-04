"""Brief routing and job creation for Intelligence Center Phase 1.

This module turns Track A industry templates or Track B briefs into the
filesystem job contract used by the research pipeline. It can also process an
existing job directory by regenerating the backward-compatible ``direction.json``
from ``job_spec.json``.
"""

import argparse
import datetime
import hashlib
import json
import os
import sys
import time


try:
    sys.stdout.reconfigure(encoding="utf-8")
except AttributeError:
    pass


INDUSTRIES = [
    "美妆",
    "个护",
    "食品饮料",
    "母婴",
    "宠物",
    "服饰",
    "3C数码",
    "家电",
    "家居",
    "汽车",
    "金融",
    "教育",
    "医疗健康",
    "运动",
    "旅游",
    "餐饮",
    "珠宝",
    "房地产",
]

DEFAULT_OBJECTIVES = ["行业趋势", "竞争格局", "消费者搜索行为"]
OBJECTIVE_KEYWORDS = {
    "竞品梳理": ["竞品", "产品线"],
    "搜索行为": ["搜索", "需求", "关键词"],
    "KOL趋势": ["KOL", "达人", "内容趋势"],
    "行业趋势": ["行业趋势", "市场变化"],
    "竞争格局": ["竞争格局", "品牌"],
    "消费者搜索行为": ["消费者", "搜索行为"],
}


def log(message):
    """Print a UTF-8 timestamped log line."""
    print("%s %s" % (datetime.datetime.now().isoformat(timespec="seconds"), message), flush=True)


def project_root():
    """Return the project root inferred from this file location."""
    return os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def jobs_dir(root_dir=None):
    """Return the jobs directory for a project root and ensure it exists."""
    root = os.path.abspath(root_dir or project_root())
    path = os.path.join(root, "jobs")
    os.makedirs(path, exist_ok=True)
    return path


def utc_now():
    """Return current local ISO timestamp without microseconds."""
    return datetime.datetime.now().replace(microsecond=0).isoformat()


def read_json(path):
    """Read a UTF-8 JSON file."""
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path, data):
    """Write pretty UTF-8 JSON with stable Chinese output."""
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def dedup_key(project_id, keywords):
    """Build a deterministic dedup key from project id and keywords."""
    normalized = "%s|%s" % (project_id or "", "|".join(sorted(keywords or [])))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def make_job_id(root_dir=None):
    """Create a date-based job id that is unique under jobs/."""
    today = datetime.datetime.now().strftime("%Y%m%d")
    base = jobs_dir(root_dir)
    existing = [name for name in os.listdir(base) if name.startswith(today + "_")]
    numbers = []
    for name in existing:
        try:
            numbers.append(int(name.rsplit("_", 1)[1]))
        except (IndexError, ValueError):
            continue
    return "%s_%03d" % (today, (max(numbers) if numbers else 0) + 1)


def coerce_list(value):
    """Normalize a scalar or list-like value into a clean string list."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value).replace("，", ",").split(",") if item.strip()]


def parse_jsonish(value):
    """Parse strict JSON, with a small fallback for shell-friendly examples."""
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return parse_loose_value(value)


def split_loose_items(value):
    """Split comma-separated loose JSON while respecting [] and {} nesting."""
    items = []
    start = 0
    depth = 0
    for index, char in enumerate(value):
        if char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
        elif char in ",，" and depth == 0:
            items.append(value[start:index].strip())
            start = index + 1
    tail = value[start:].strip()
    if tail:
        items.append(tail)
    return items


def split_loose_pair(item):
    """Split one key:value pair while respecting nested values."""
    depth = 0
    for index, char in enumerate(item):
        if char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
        elif char == ":" and depth == 0:
            return item[:index].strip(), item[index + 1 :].strip()
    raise ValueError("Invalid brief pair: %s" % item)


def parse_loose_value(value):
    """Parse a minimal JSON-like value used by CLI brief examples."""
    text = value.strip()
    if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
        return text[1:-1]
    if text.startswith("{") and text.endswith("}"):
        body = text[1:-1].strip()
        result = {}
        if not body:
            return result
        for item in split_loose_items(body):
            key, raw = split_loose_pair(item)
            result[key.strip("\"'")] = parse_loose_value(raw)
        return result
    if text.startswith("[") and text.endswith("]"):
        body = text[1:-1].strip()
        return [parse_loose_value(item) for item in split_loose_items(body)] if body else []
    if text.isdigit():
        return int(text)
    return text


def parse_natural_language(text):
    """Parse a natural-language brief using deterministic keyword fallback.

    MiniMax integration is intentionally conservative in Phase 1: if
    ``MINIMAX_API_KEY`` exists, the module logs that the key was detected, then
    still returns the local extraction unless a future API adapter is added.
    This keeps the router stdlib-only and offline-testable.
    """
    if os.environ.get("MINIMAX_API_KEY"):
        log("MINIMAX_API_KEY detected; using local fallback parser in Phase 1")
    keywords = []
    for token in ["抗初老", "早C晚A", "敏感肌", "竞品", "KOL", "搜索", "美妆", "个护"]:
        if token in text and token not in keywords:
            keywords.append(token)
    if not keywords:
        keywords = [part for part in text.replace("，", " ").replace(",", " ").split()[:5]]
    industry = next((item for item in INDUSTRIES if item in text), "美妆")
    return {
        "project_id": text[:20] or "自然语言Brief",
        "direction": text,
        "objective": ["竞品梳理", "搜索行为", "KOL趋势"],
        "keywords": keywords,
        "constraints": {"scope": "近3个月", "max_sources": 50},
        "industry": industry,
        "output_format": "project_report",
        "created_by": "feishu",
    }


def build_track_a_spec(industry):
    """Build a Track A template job spec for one of the 18 industries."""
    if industry not in INDUSTRIES:
        raise ValueError("Unsupported industry: %s" % industry)
    return {
        "track": "A",
        "project_id": "%s行业趋势监测" % industry,
        "direction": "%s行业趋势与媒介机会" % industry,
        "objective": DEFAULT_OBJECTIVES,
        "keywords": [industry, "行业趋势", "消费者洞察", "媒介投放"],
        "constraints": {"scope": "近3个月", "max_sources": 50},
        "industry": industry,
        "output_format": "industry_report",
        "created_by": "template",
    }


def normalize_spec(data, track, job_id):
    """Normalize user input into the full job_spec.json contract."""
    constraints = data.get("constraints") or {}
    keywords = coerce_list(data.get("keywords"))
    objective = coerce_list(data.get("objective") or data.get("objectives") or DEFAULT_OBJECTIVES)
    spec = {
        "job_id": job_id,
        "track": track,
        "project_id": data.get("project_id") or data.get("project") or job_id,
        "direction": data.get("direction") or data.get("brief") or "",
        "objective": objective,
        "keywords": keywords,
        "constraints": {
            "scope": constraints.get("scope", "近3个月"),
            "max_sources": int(constraints.get("max_sources", constraints.get("max_results", 50))),
            "deadline": constraints.get("deadline"),
        },
        "output_format": data.get("output_format", "project_report"),
        "created_by": data.get("created_by", "feishu" if track == "B" else "template"),
        "created_at": data.get("created_at", utc_now()),
        "industry": data.get("industry", "美妆"),
    }
    spec["dedup_key"] = dedup_key(spec["project_id"], spec["keywords"])
    return spec


def direction_from_spec(spec):
    """Convert a full job spec to the existing engine direction contract."""
    directions = []
    industry = spec.get("industry") or "美妆"
    base_keywords = coerce_list(spec.get("keywords"))
    for objective in coerce_list(spec.get("objective")) or ["综合研究"]:
        extra = OBJECTIVE_KEYWORDS.get(objective, [objective])
        topic = "%s%s" % (spec.get("direction") or spec.get("project_id"), objective)
        merged = []
        for item in base_keywords + extra:
            if item and item not in merged:
                merged.append(item)
        directions.append({"topic": topic, "keywords": merged, "industry": industry})
    constraints = spec.get("constraints") or {}
    return {
        "directions": directions,
        "scope": constraints.get("scope", "近3个月"),
        "max_results": int(constraints.get("max_sources", constraints.get("max_results", 50))),
    }


def active_dedup_exists(key, root_dir=None):
    """Return True when a pending/running job already has the same dedup key."""
    base = jobs_dir(root_dir)
    for name in os.listdir(base):
        job_path = os.path.join(base, name)
        spec_path = os.path.join(job_path, "job_spec.json")
        status_path = os.path.join(job_path, "status.json")
        if not os.path.isdir(job_path) or not os.path.exists(spec_path) or not os.path.exists(status_path):
            continue
        try:
            spec = read_json(spec_path)
            status = read_json(status_path)
        except (OSError, json.JSONDecodeError):
            continue
        if spec.get("dedup_key") == key and status.get("state") in ("pending", "running"):
            return True
    return False


def create_job(data, track="B", root_dir=None):
    """Create a job directory with job_spec.json, direction.json, status.json."""
    if track == "A":
        data = build_track_a_spec(data["industry"] if isinstance(data, dict) else data)
    job_id = make_job_id(root_dir)
    spec = normalize_spec(data, track, job_id)
    if active_dedup_exists(spec["dedup_key"], root_dir):
        raise ValueError("Duplicate pending/running job rejected: %s" % spec["project_id"])
    job_path = os.path.join(jobs_dir(root_dir), job_id)
    os.makedirs(job_path, exist_ok=False)
    write_json(os.path.join(job_path, "job_spec.json"), spec)
    write_json(os.path.join(job_path, "direction.json"), direction_from_spec(spec))
    write_json(
        os.path.join(job_path, "status.json"),
        {
            "job_id": job_id,
            "state": "pending",
            "started_at": None,
            "completed_at": None,
            "timeout": 3600,
            "error": None,
            "retry_count": 0,
        },
    )
    log("created job %s track=%s" % (job_id, track))
    return job_path


def process_existing_job(job_path):
    """Regenerate direction.json for an existing job directory."""
    spec_path = os.path.join(job_path, "job_spec.json")
    if not os.path.exists(spec_path):
        raise FileNotFoundError("Missing job_spec.json: %s" % spec_path)
    spec = read_json(spec_path)
    write_json(os.path.join(job_path, "direction.json"), direction_from_spec(spec))
    log("processed existing job %s" % spec.get("job_id", job_path))
    return os.path.join(job_path, "direction.json")


def load_brief(args):
    """Load brief input from CLI JSON string, file, natural language, or Track A."""
    if args.track == "A":
        return {"industry": args.industry}
    if args.brief_file:
        return read_json(args.brief_file)
    if args.brief:
        return parse_jsonish(args.brief)
    if args.text:
        return parse_natural_language(args.text)
    raise ValueError("Track B requires --brief, --brief-file, or --text")


def main(argv=None):
    """CLI entrypoint for creating or processing Intelligence Center jobs."""
    parser = argparse.ArgumentParser(description="Create or process Intelligence Center jobs")
    parser.add_argument("--track", choices=["A", "B"], default="B")
    parser.add_argument("--industry", choices=INDUSTRIES)
    parser.add_argument("--brief")
    parser.add_argument("--brief-file")
    parser.add_argument("--text")
    parser.add_argument("--job", help="Existing job directory to process")
    parser.add_argument("--root", default=None, help="Project root; defaults to parent of src/")
    args = parser.parse_args(argv)
    try:
        if args.job:
            process_existing_job(args.job)
        else:
            if args.track == "A" and not args.industry:
                raise ValueError("Track A requires --industry")
            job_path = create_job(load_brief(args), track=args.track, root_dir=args.root)
            print(job_path)
        return 0
    except Exception as exc:
        log("ERROR %s" % exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
