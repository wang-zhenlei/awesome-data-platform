#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
awesome-data-platform 每日洞察报告生成器。

在 scripts/refresh.py 之后运行：
  1. 解析本次刷新生成的 repo-out/topics/*.md，得到当前全量项目 {name: stars, theme...}
  2. 与上一次快照 scripts/state/snapshot.json 对比，计算 24h 变化：
       - 新进榜（昨天没有、今天有）
       - star 增幅 Top（聚焦与大数据平台主线相关的主题）
       - 跌出（仅报数量，star 搜索有抖动，避免噪音）
  3. 聚焦王振雷平台主线的几个主题，生成 日报/YYYY-MM-DD.md
  4. 若设置了 ANTHROPIC_API_KEY，用 AI 写“对你有什么用 + 指导建议”；否则降级为规则版
  5. 回写今日快照，并重建 日报/README.md 索引

设计为在 GitHub Action 中运行，仅依赖标准库（AI 调用走 urllib，无第三方依赖）。
"""
import os, re, json, glob, datetime, urllib.request, urllib.error

OUT = os.environ.get("OUT", "repo-out")                 # refresh.py 的产出目录
REPORT_DIR = os.environ.get("REPORT_DIR", "日报")
STATE = os.environ.get("STATE", "scripts/state/snapshot.json")
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
MODEL = os.environ.get("INSIGHT_MODEL", "claude-haiku-5-5").strip()

# 北京时间（UTC+8），文件名与报告日期按北京时间
NOW_BJ = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
TODAY = NOW_BJ.strftime("%Y-%m-%d")

# ---- 与大数据平台主线相关的主题（topics 文件名前缀）及其映射到自家产品线 ----
FOCUS = {
 "03": ("🧠 语义层与上下文 / Agent",   "语义中枢 / 企业上下文协议 v1（寻址+引用薄层）、DataAgent"),
 "04": ("✅ 数据质量与可观测性",        "“数语”治理 / 质量规则 / 血缘质量保障"),
 "05": ("🗄️ 数据中台与数据资产",       "资产平台（门户/注册审核/上架）、数据服务中枢 DataHub"),
 "06": ("📊 指标平台与 Headless BI",    "“极数字典”指标平台 / 指标物化加速（StarRocks）"),
 "07": ("🧬 数据本体与知识图谱",        "Ontology 落地 / GDB 血缘图叠加语义层"),
 "08": ("🔌 数据集成与 ELT/CDC",        "数据接入与同步链路"),
}
FOCUS_PREFIXES = set(FOCUS.keys())

# 平台画像（喂给 AI 做战略对齐；也用于规则版兜底话术）
PROFILE = (
 "读者：吉利汽车集团大数据平台负责人，19 人团队，统筹全球大数据平台、资产平台、"
 "指标平台（极数字典）、数据服务中枢（DataHub）、DataAgent、自研 bigdata-mcp-server。"
 "2027 主线：DataAgent 列为头号 P0（自助取数占比→60%）；指标口径一致率→95%；血缘覆盖率→90%；"
 "语义层/企业上下文协议 v1 reference 跑通可演示；预算只减不增、AI 增量单独打报告。"
 "风格：数据实证、诚实边界、调研→判断→最小切片、先诊断后方案。"
)

ROW = re.compile(
    r'^\|\s*([\d,]+)\s*\|\s*\[([^\]]+)\]\(([^)]+)\)\s*\|\s*([\d-]*)\s*\|\s*(.*?)\s*\|\s*$')


def parse_topics():
    """解析 repo-out/topics/NN-*.md -> {name: {stars,url,prefix,theme,desc}}（跳过 00 总览）。"""
    data = {}
    for f in sorted(glob.glob(os.path.join(OUT, "topics", "*.md"))):
        base = os.path.basename(f)
        m = re.match(r'(\d{2})-', base)
        if not m:
            continue
        prefix = m.group(1)
        if prefix == "00":
            continue
        theme = ""
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("# "):
                    theme = line[2:].strip()
                    continue
                rm = ROW.match(line.rstrip("\n"))
                if not rm:
                    continue
                stars = int(rm.group(1).replace(",", ""))
                name, url = rm.group(2), rm.group(3)
                desc = rm.group(5).replace("\\|", "|")
                if name not in data or stars > data[name]["stars"]:
                    data[name] = {"stars": stars, "url": url, "prefix": prefix,
                                  "theme": theme, "desc": desc}
    return data


def load_state():
    try:
        with open(STATE, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_state(today):
    os.makedirs(os.path.dirname(STATE) or ".", exist_ok=True)
    snap = {"date": TODAY, "stars": {n: d["stars"] for n, d in today.items()}}
    with open(STATE, "w", encoding="utf-8") as fh:
        json.dump(snap, fh, ensure_ascii=False, indent=0)


def compute_delta(today, prev_stars):
    """返回 (new_entrants, gainers, dropout_count)。仅聚焦主线主题。"""
    new_entrants, gainers, dropouts = [], [], 0
    for name, d in today.items():
        if d["prefix"] not in FOCUS_PREFIXES:
            continue
        if name not in prev_stars:
            new_entrants.append((name, d))
        else:
            delta = d["stars"] - prev_stars[name]
            if delta > 0:
                gainers.append((name, d, delta))
    for name in prev_stars:
        if name not in today:
            dropouts += 1
    new_entrants.sort(key=lambda x: -x[1]["stars"])
    gainers.sort(key=lambda x: -x[2])
    return new_entrants, gainers, dropouts


def fmt_rows(items, kind):
    out = []
    if kind == "new":
        out.append("| ⭐ Stars | 项目 | 主题 | 简介 |")
        out.append("|--------:|------|------|------|")
        for name, d in items:
            out.append(f"| {d['stars']:,} | [{name}]({d['url']}) | {d['theme']} | {d['desc']} |")
    else:  # gainers
        out.append("| 📈 24h 增幅 | ⭐ Stars | 项目 | 主题 |")
        out.append("|-----------:|--------:|------|------|")
        for name, d, delta in items:
            out.append(f"| +{delta:,} | {d['stars']:,} | [{name}]({d['url']}) | {d['theme']} |")
    return "\n".join(out)


def focus_snapshot(today, topn=5):
    """各主线主题 Top N 快照。"""
    by_prefix = {}
    for name, d in today.items():
        if d["prefix"] in FOCUS_PREFIXES:
            by_prefix.setdefault(d["prefix"], []).append((name, d))
    parts = []
    for pfx in sorted(FOCUS_PREFIXES):
        items = sorted(by_prefix.get(pfx, []), key=lambda x: -x[1]["stars"])[:topn]
        if not items:
            continue
        theme, product = FOCUS[pfx]
        parts.append(f"### {theme}\n> 对标自家：**{product}**\n")
        parts.append("| ⭐ Stars | 项目 | 简介 |")
        parts.append("|--------:|------|------|")
        for name, d in items:
            parts.append(f"| {d['stars']:,} | [{name}]({d['url']}) | {d['desc']} |")
        parts.append("")
    return "\n".join(parts)


def ai_insight(new_entrants, gainers, first_run):
    """有 API key 则调 Anthropic 写战略解读，失败返回 None。"""
    if not API_KEY:
        return None
    facts = []
    if first_run:
        facts.append("今日为基线建立首日，暂无昨日对比；请基于下方各主题 Top 项目给出战略解读。")
    for name, d in new_entrants[:12]:
        facts.append(f"[新进榜] {name}（{d['stars']:,}⭐，{d['theme']}）：{d['desc']}")
    for name, d, delta in gainers[:12]:
        facts.append(f"[star上涨 +{delta}] {name}（{d['stars']:,}⭐，{d['theme']}）：{d['desc']}")
    if not facts:
        facts.append("今日主线主题无明显新进榜或 star 变化。请简要提示：保持观察，无需动作。")
    facts_txt = "\n".join(facts)

    prompt = f"""你在为一位数据平台负责人写一份每日开源情报的"解读与建议"段落。

# 读者画像
{PROFILE}

# 今日主线主题（语义层/数据质量/中台资产/指标平台/本体图谱/数据集成）的变化事实
{facts_txt}

# 要求
用中文写，分三小节，务实、不浮夸、挂到他的 2027 主线：
## 💡 这些变化对你意味着什么
（3-6 条，每条点名具体项目，说清与他哪条产品线/主线相关，是机会还是需警惕）
## 🧭 指导建议
（2-4 条可落地动作，遵循"调研→判断→最小切片"，给周级可做的最小动作，不要泛泛而谈）
## ⚠️ 诚实边界
（1-2 句：本情报基于 GitHub star 搜索 + 机翻简介，star 日度抖动可能造成伪变化，仅作线索非结论）

只输出 markdown 正文，不要额外寒暄。"""

    body = json.dumps({
        "model": MODEL,
        "max_tokens": 1500,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body, method="POST",
        headers={"x-api-key": API_KEY, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            j = json.loads(resp.read().decode("utf-8"))
        return "".join(b.get("text", "") for b in j.get("content", [])).strip() or None
    except Exception as e:
        print(f"  AI insight failed, fallback to rule-based: {e}")
        return None


def rule_insight(new_entrants, gainers, first_run):
    """无 API key 的规则版解读。"""
    lines = ["## 💡 这些变化对你意味着什么\n"]
    hit = False
    for name, d in new_entrants[:8]:
        _, product = FOCUS[d["prefix"]]
        lines.append(f"- **{name}**（{d['stars']:,}⭐）进入「{d['theme']}」——对标自家 **{product}**，可快速扫一眼是否有可借鉴的设计。")
        hit = True
    for name, d, delta in gainers[:8]:
        _, product = FOCUS[d["prefix"]]
        lines.append(f"- **{name}** 近 24h +{delta:,}⭐（「{d['theme']}」，对标 **{product}**），社区热度上升，值得留意走向。")
        hit = True
    if not hit:
        lines.append("- 今日主线主题无明显新进榜或 star 波动，保持观察即可，无需动作。")
    lines += [
        "\n## 🧭 指导建议\n",
        "- 对上面点名的项目，先看 README 的架构图与 roadmap（10 分钟/个），判断是否值得排进调研清单，而非整体引入。",
        "- 若某项目与语义层/指标物化/血缘主线高度相关，按“调研→判断→最小切片”起一个周级 POC 对比口径。",
        "\n## ⚠️ 诚实边界\n",
        "- 本情报基于 GitHub star 搜索 + 机翻简介，star 存在日度抖动可能造成伪“新进榜/跌出”，仅作线索非结论。",
    ]
    return "\n".join(lines)


def build_report(today, new_entrants, gainers, dropouts, first_run, insight, ai_used):
    total_focus = sum(1 for d in today.values() if d["prefix"] in FOCUS_PREFIXES)
    hdr = [
        f"# 📰 Awesome 数据平台 · 每日洞察 · {TODAY}",
        "",
        f"> 面向大数据平台主线（语义层 / 数据质量 / 中台资产 / 指标平台 / 本体图谱 / 数据集成）的开源情报。",
        f"> 情报源：本仓库每日全量刷新结果（北京时间凌晨 1 点）。解读方式：{'AI 生成' if ai_used else '规则模板'}。",
        f"> [← 返回日报索引](./README.md) ｜ [← 仓库首页](../README.md)",
        "",
        "## 一页纸摘要",
        "",
    ]
    if first_run:
        hdr.append(f"- 📌 **今日建立基线**：主线相关主题共收录 **{total_focus}** 个项目，明日起开始输出 24h 变化。")
    else:
        hdr.append(f"- 🆕 主线新进榜：**{len(new_entrants)}** 个 ｜ 📈 star 上涨：**{len(gainers)}** 个 ｜ 📉 跌出：约 **{dropouts}** 个（含搜索抖动）")
        top_hint = []
        if new_entrants:
            top_hint.append(f"最亮新项目 **{new_entrants[0][0]}**（{new_entrants[0][1]['stars']:,}⭐）")
        if gainers:
            top_hint.append(f"最大涨幅 **{gainers[0][0]}**（+{gainers[0][2]:,}⭐）")
        if top_hint:
            hdr.append("- " + " ｜ ".join(top_hint))
    hdr.append("")
    hdr.append("---\n")

    body = []
    if not first_run:
        body.append("## 🆕 主线新进榜\n")
        body.append(fmt_rows(new_entrants, "new") if new_entrants else "_今日主线主题无新进榜项目。_")
        body.append("\n## 📈 24h star 增幅（主线主题）\n")
        body.append(fmt_rows(gainers[:20], "gainers") if gainers else "_今日主线主题无 star 上涨。_")
        body.append("\n---\n")

    body.append("## 🔭 解读与建议\n")
    body.append(insight)
    body.append("\n---\n")
    body.append("## 📊 主线主题当前 Top5 快照\n")
    body.append(focus_snapshot(today))

    return "\n".join(hdr + body) + "\n"


def rebuild_index():
    files = sorted(glob.glob(os.path.join(REPORT_DIR, "20*.md")), reverse=True)
    idx = [
        "# 📰 每日洞察报告",
        "",
        "> 每天北京时间凌晨 1 点，基于本仓库全量刷新结果，自动生成面向大数据平台主线的开源情报。",
        "> 覆盖主题：语义层 / 数据质量 / 中台资产 / 指标平台 / 本体图谱 / 数据集成。",
        "",
        "| 日期 | 报告 |",
        "|------|------|",
    ]
    for f in files:
        d = os.path.basename(f)[:-3]
        idx.append(f"| {d} | [查看 →](./{os.path.basename(f)}) |")
    idx.append("")
    with open(os.path.join(REPORT_DIR, "README.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(idx))


def main():
    os.makedirs(REPORT_DIR, exist_ok=True)
    today = parse_topics()
    if not today:
        print("!! 未解析到任何项目，检查 OUT/topics 是否存在。跳过日报。")
        return
    prev = load_state()
    prev_stars = prev.get("stars", {})
    first_run = not prev_stars

    path = os.path.join(REPORT_DIR, f"{TODAY}.md")
    if os.path.exists(path):
        # 当日日报已存在（例如人工撰写的深度版），不覆盖；仅更新快照与索引。
        print(f"== 当日日报已存在，保留现有，不覆盖：{path}")
    else:
        new_entrants, gainers, dropouts = compute_delta(today, prev_stars)
        insight = ai_insight(new_entrants, gainers, first_run)
        ai_used = insight is not None
        if not ai_used:
            insight = rule_insight(new_entrants, gainers, first_run)
        report = build_report(today, new_entrants, gainers, dropouts, first_run, insight, ai_used)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(report)
        print(f"== 日报已生成：{path}（AI={ai_used}, 新进榜={len(new_entrants)}, 上涨={len(gainers)}, 首日={first_run}）")

    save_state(today)
    rebuild_index()
    print("== 快照与索引已更新 ==")


if __name__ == "__main__":
    main()
