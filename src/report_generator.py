"""Generate project research reports for the Intelligence Center.

Reads a job's spec, research results, and KB retrieval context, then
assembles a structured Markdown report.  Optionally pushes to Feishu.
"""

import argparse
import glob
import os
import sys
import time

from kb_common import ensure_dir, log, now_iso, read_json, write_json

try:
    sys.stdout.reconfigure(encoding="utf-8")
except AttributeError:
    pass


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------

def collect_research_files(job_path):
    """Scan research output directories for markdown/text findings."""
    findings = []
    for dirname in ["output/research_results", "output", "research", "results"]:
        root = os.path.join(job_path, dirname)
        if not os.path.isdir(root):
            continue
        for dirpath, _, filenames in os.walk(root):
            for fname in sorted(filenames):
                if fname.endswith((".md", ".txt")):
                    fpath = os.path.join(dirpath, fname)
                    try:
                        with open(fpath, "r", encoding="utf-8") as fh:
                            content = fh.read()
                        if content.strip():
                            findings.append({
                                "path": fpath,
                                "name": fname,
                                "content": content,
                                "chars": len(content),
                            })
                    except (OSError, UnicodeDecodeError) as exc:
                        log("WARN: cannot read %s: %s" % (fpath, exc))
    return findings


def collect_kb_context(job_path):
    """Read the KB retrieval context file if it exists."""
    kb_path = os.path.join(job_path, "context", "kb_retrieval.md")
    if not os.path.exists(kb_path):
        return ""
    try:
        with open(kb_path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):
        return ""


def load_job_spec(job_path):
    """Load the job specification."""
    spec_path = os.path.join(job_path, "job_spec.json")
    if not os.path.exists(spec_path):
        log("WARN: job_spec.json not found in %s" % job_path)
        return {}
    return read_json(spec_path)


def count_kb_links(job_path):
    """Count KB links produced by the linker."""
    links_path = os.path.join(job_path, "context", "kb_links.json")
    if not os.path.exists(links_path):
        return 0
    try:
        data = read_json(links_path)
        if isinstance(data, list):
            return len(data)
        return len(data.get("links", []))
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def generate_report(job_path):
    """Assemble a Markdown report from job data. Returns the report string."""
    spec = load_job_spec(job_path)
    project_id = spec.get("project_id", os.path.basename(job_path.rstrip("/")))
    direction = spec.get("direction", "未指定方向")
    keywords = spec.get("keywords", [])
    scope = spec.get("scope", "全网")
    created_at = now_iso()

    findings = collect_research_files(job_path)
    kb_context = collect_kb_context(job_path)
    kb_link_count = count_kb_links(job_path)

    # Parse KB context for historical references
    kb_page_count = 0
    if kb_context:
        for line in kb_context.splitlines():
            if line.startswith("## 命中页面："):
                try:
                    kb_page_count = int(line.split("：")[1].replace("篇", "").strip())
                except (IndexError, ValueError):
                    pass

    # Build report
    lines = [
        "# %s — 调研报告" % project_id,
        "",
        "> 生成时间：%s" % created_at,
        "> 调研方向：%s" % direction,
        "> 关键词：%s" % ", ".join(keywords) if keywords else "",
        "",
        "---",
        "",
        "## 一、调研概要",
        "",
        "基于%s的调研，围绕「%s」方向，共检索%d个信息源，生成%d条研究发现。" % (
            scope, direction, len(findings), len(findings),
        ),
        "",
    ]

    # Core findings
    lines.extend(["## 二、核心发现", ""])
    if findings:
        for i, finding in enumerate(findings[:10], 1):
            # Extract first meaningful line as title
            title_lines = [
                l.strip().lstrip("#").strip()
                for l in finding["content"].splitlines()
                if l.strip() and not l.startswith(">")
            ]
            title = title_lines[0] if title_lines else finding["name"]
            if len(title) > 80:
                title = title[:77] + "..."
            lines.extend([
                "### 发现%d：%s" % (i, title),
                "",
                "- **来源文件**：%s" % finding["name"],
                "- **内容长度**：%d字符" % finding["chars"],
                "",
                "<details><summary>展开查看</summary>",
                "",
            ])
            # Include first 500 chars of content as preview
            preview = finding["content"][:500].strip()
            if len(finding["content"]) > 500:
                preview += "\n\n...(共%d字符)" % finding["chars"]
            lines.extend([preview, "", "</details>", ""])
    else:
        lines.extend(["暂无研究发现。", ""])

    # KB context comparison
    lines.extend(["## 三、历史知识对比", ""])
    if kb_page_count > 0:
        lines.extend([
            "基于知识库中%d篇相关历史页面的对比分析：" % kb_page_count,
            "",
            "| 维度 | 历史认知 | 本次新发现 | 增量 |",
            "|------|----------|------------|------|",
            "| （待填充） | （待LLM分析） | （待LLM分析） | （待LLM分析） |",
            "",
            "> 注：对比分析需人工或LLM补充。",
            "",
        ])
    else:
        lines.extend(["本次检索未找到匹配的历史知识。", ""])

    # Recommendations
    lines.extend(["## 四、建议方向", ""])
    if findings:
        lines.extend([
            "1. 基于调研结果，建议深入关注上述%d个发现方向" % len(findings),
            "2. 建议将本次发现录入知识库，丰富后续检索质量",
            "3. 建议与历史知识做交叉验证，确认数据一致性",
            "",
        ])
    else:
        lines.extend(["暂无建议。需先补充研究数据。", ""])

    # Data sources
    lines.extend([
        "## 五、数据来源",
        "",
        "- 本次调研：%d个来源" % len(findings),
        "- 历史引用：%d篇wiki页面" % kb_page_count,
        "- 知识库关联：%d个相关页面" % kb_link_count,
        "",
        "---",
        "",
        "*报告由 Intelligence Center 自动生成*",
    ])

    return "\n".join(lines)


def save_report(job_path, report_text):
    """Write the report to the job output directory."""
    output_dir = os.path.join(job_path, "output")
    ensure_dir(output_dir)
    report_path = os.path.join(output_dir, "report.md")
    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write(report_text)
        fh.write("\n")
    return report_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description="Generate project research report")
    parser.add_argument("--job", required=True, help="Job directory path")
    parser.add_argument("--dry-run", action="store_true", help="Print report instead of saving")
    args = parser.parse_args(argv)

    job_path = os.path.abspath(os.path.expanduser(args.job))
    if not os.path.isdir(job_path):
        log("ERROR: job directory not found: %s" % job_path)
        return 1

    report = generate_report(job_path)

    if args.dry_run:
        print(report)
    else:
        path = save_report(job_path, report)
        log("Report saved: %s (%d chars)" % (path, len(report)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
