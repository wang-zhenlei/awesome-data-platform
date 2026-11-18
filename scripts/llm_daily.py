#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI大模型进展日报（awesome-data-platform / topics/12-llm-daily）

每天跟踪国内外主流大模型的新版本/新能力，生成 日报/大模型进展-YYYY-MM-DD.md。

四层信号：
  [官方]  OpenAI/Mistral 官方新闻 RSS + Hugging Face 模型发布（11 家 org）
  [上线]  OpenRouter 新模型上架（模型版本进入可服务状态的市场信号）
  [媒体]  量子位/IT之家 按模型家族关键词标题归口（发布/评测/能力解读，宽口径）
  [开源]  模型厂商 GitHub org 发布事件（限模型相关仓库名）

复用 vendor_ai_daily 的抓取/解析/时区基建；状态独立：scripts/state/llm_seen.json。
"""
import os
import re
import json
import glob
import time
import datetime
import urllib.error
import urllib.parse

from vendor_ai_daily import fetch, parse_rss, parse_date, parse_hf, strip_tags, TODAY

REPORT_DIR = os.environ.get("REPORT_DIR", "日报")
STATE = os.environ.get("STATE", "scripts/state/llm_seen.json")
TOPICS_DIR = os.environ.get("TOPICS_DIR", "topics")
NOW = datetime.datetime.now()
WINDOW_DAYS = 14
MEDIA_WINDOW_DAYS = 3
GH_WINDOW_DAYS = 7
MAX_PER_FAMILY_LAYER = 8

# ---------- 模型家族（媒体归口 + 报告排序） ----------
MODEL_FAMILIES = {
    # 国内
    "阿里 Qwen": ["Qwen", "千问", "通义", "QwQ", "QvQ"],
    "字节 豆包/Seed": ["豆包", "Doubao", "Seed"],
    "DeepSeek": ["DeepSeek", "深度求索"],
    "月之暗面 Kimi": ["Kimi", "月之暗面", "Moonshot"],
    "智谱 GLM": ["GLM", "智谱"],
    "MiniMax": ["MiniMax", "海螺"],
    "腾讯混元": ["混元", "Hunyuan"],
    "百度文心": ["文心", "ERNIE"],
    "零一万物 Yi": ["Yi", "零一万物", "01.AI"],
    # 国际
    "OpenAI GPT": ["GPT", "OpenAI", "ChatGPT", "Sora"],
    "Anthropic Claude": ["Claude", "Anthropic"],
    "Google Gemini": ["Gemini"],
    "Meta Llama": ["Llama"],
    "Mistral": ["Mistral", "Magistral"],
    "xAI Grok": ["Grok", "xAI"],
}
FAMILY_ORDER = ["阿里 Qwen", "字节 豆包/Seed", "DeepSeek", "月之暗面 Kimi", "智谱 GLM", "MiniMax",
                "腾讯混元", "百度文心", "零一万物 Yi",
                "OpenAI GPT", "Anthropic Claude", "Google Gemini", "Meta Llama", "Mistral", "xAI Grok"]

# ---------- L1 官方 ----------
OFFICIAL_RSS = [
    ("OpenAI GPT", "OpenAI 官方新闻", "https://openai.com/news/rss.xml"),
    ("Mistral", "Mistral 官方新闻", "https://mistral.ai/news/rss.xml"),
]
HF_ORGS = [
    ("OpenAI GPT", "openai"),
    ("Meta Llama", "meta-llama"),
    ("Mistral", "mistralai"),
    ("DeepSeek", "deepseek-ai"),
    ("阿里 Qwen", "Qwen"),
    ("字节 豆包/Seed", "ByteDance"),
    ("月之暗面 Kimi", "moonshotai"),
    ("智谱 GLM", "THUDM"),
    ("零一万物 Yi", "01-ai"),
    ("腾讯混元", "tencent"),
    ("百度文心", "baidu"),
]

# ---------- L2 媒体 ----------
MEDIA_FEEDS = [
    ("量子位", "https://www.qbitai.com/feed", True),
    ("IT之家", "https://www.ithome.com/rss/", False),
]
# IT之家非纯 AI 媒体，需模型/AI 关键词兜底
AI_RE = re.compile(r"(AI|大模型|LLM|模型|GPT|Claude|Gemini|智能)", re.I)
EXCLUDE_RE = re.compile(r"(退休|离职|去世|讣告|八卦|绯闻|回流政策)")

# ---------- L3 开源 ----------
GH_MODEL_ORGS = [
    ("Meta Llama", "meta-llama"),
    ("DeepSeek", "deepseek-ai"),
    ("阿里 Qwen", "QwenLM"),
    ("月之暗面 Kimi", "MoonshotAI"),
    ("智谱 GLM", "THUDM"),
    ("零一万物 Yi", "01-ai"),
    ("Mistral", "mistralai"),
]
GH_REPO_RE = re.compile(r"(llama|qwen|deepseek|kimi|glm|yi\b|seed|ernie|hunyuan|magistral|mixtral|devstral|agent|vl\b|video|omni|vlm|ocr|tts|asr|llm|lm\b|model)", re.I)

OPENROUTER_API = "https://openrouter.ai/api/v1/models"


def parse_gh_events(org):
    events = json.loads(fetch(f"https://api.github.com/orgs/{org}/events?per_page=60"))
    items = []
    for ev in events:
        repo_full = ev.get("repo", {}).get("name", "")
        repo_name = repo_full.split("/", 1)[1] if "/" in repo_full else repo_full
        created = (ev.get("created_at") or "")[:10]
        if ev.get("type") == "ReleaseEvent":
            rel = ev.get("payload", {}).get("release", {})
            if not GH_REPO_RE.search(repo_name):
                continue
            items.append({
                "title": f"开源发布 {repo_full} {rel.get('tag_name', '')} {rel.get('name', '')[:50]}".strip(),
                "link": rel.get("html_url") or f"https://github.com/{repo_full}",
                "date": created,
            })
        elif ev.get("type") == "CreateEvent" and ev.get("payload", {}).get("ref_type") == "repository":
            if not GH_REPO_RE.search(repo_name):
                continue
            items.append({
                "title": f"新开源仓库 {repo_full}",
                "link": f"https://github.com/{repo_full}",
                "date": created,
            })
    return items


def parse_openrouter():
    """OpenRouter 新上架模型（7 天内 created）→ 归口到模型家族。"""
    data = json.loads(fetch(OPENROUTER_API))
    items = []
    now = time.time()
    for m in data.get("data", []):
        created = m.get("created")
        if not isinstance(created, (int, float)) or now - created > 7 * 86400:
            continue
        name = m.get("name") or m.get("id", "")
        items.append({
            "title": f"新模型上架：{name}",
            "link": f"https://openrouter.ai/{m.get('id', '')}",
            "date": datetime.date.fromtimestamp(created).isoformat(),
            "name": name,
        })
    return items


def attribute(items, source, layer):
    """按模型家族关键词归口（标题匹配）。返回 [(family, item)]。"""
    out = []
    for it in items:
        title = it["title"]
        if EXCLUDE_RE.search(title):
            continue
        blob = title
        if source == "IT之家":
            if not AI_RE.search(blob):
                continue
        for family, kws in MODEL_FAMILIES.items():
            if any(kw in blob for kw in kws):
                out.append((family, {**it, "source": source, "layer": layer}))
                break  # 一个家族即可，避免重复
    return out


def load_state():
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_state(state):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    cutoff = (TODAY - datetime.timedelta(days=90)).isoformat()
    state = {k: v for k, v in state.items() if v >= cutoff}
    json.dump(state, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    print(f"== 快照已回写：{len(state)} 条")


def main():
    today = TODAY.isoformat()
    state = load_state()
    first_run = len(state) == 0
    env = "GitHub Action" if os.environ.get("GITHUB_ACTIONS") else "本地试跑"

    pool = {}
    pool_links = set()
    source_status = []

    def add(family, layer, it, source):
        k = (family, it["link"])
        if k in pool_links:
            return
        pool_links.add(k)
        pool.setdefault(family, []).append({**it, "layer": layer, "source": source})

    # ---- L1 官方 RSS ----
    for family, note, url in OFFICIAL_RSS:
        status, got = "ok", 0
        try:
            items = parse_rss(fetch(url)) or []
            keep = []
            for it in items:
                d = parse_date(it["date"])
                if d:
                    it["date"] = d.date().isoformat()
                    if (TODAY - d.date()).days > (WINDOW_DAYS if first_run else 7):
                        continue
                keep.append(it)
            got = len(keep)
            for it in keep:
                add(family, "官方", it, note)
        except urllib.error.HTTPError as e:
            status = f"http {e.code}"
        except Exception as e:
            status = f"unreachable ({type(e).__name__})"
        source_status.append((f"{family}·{note}", "L1 官方", status, got))
        print(f"  [L1-rss] {family}·{note}: {status}, {got}")

    # ---- L1 HF 模型发布 ----
    for family, org in HF_ORGS:
        status, got = "ok", 0
        try:
            items = parse_hf(org)
            if items:
                print(f"  [hf-diag] {family}/{org} 最新: {items[0]['link']} @ {items[0].get('created', '?')}")
            keep = []
            for it in items:
                c = it.get("created") or ""
                if c and (TODAY - datetime.date.fromisoformat(c)).days > (WINDOW_DAYS if first_run else 7):
                    continue
                keep.append(it)
            got = len(keep)
            for it in keep:
                add(family, "官方", it, "HF 模型发布")
        except urllib.error.HTTPError as e:
            status = f"http {e.code}"
        except Exception as e:
            status = f"unreachable ({type(e).__name__})"
        source_status.append((f"{family}·hf/{org}", "L1 官方", status, got))
        print(f"  [L1-hf] {family}·hf/{org}: {status}, {got}")

    # ---- 上线层：OpenRouter ----
    status, got = "ok", 0
    try:
        items = parse_openrouter()
        got = len(items)
        for it in items:
            for family, kws in MODEL_FAMILIES.items():
                if any(kw in (it.get("name") or "") for kw in kws):
                    add(family, "上线", it, "OpenRouter")
                    break
    except urllib.error.HTTPError as e:
        status = f"http {e.code}"
    except Exception as e:
        status = f"unreachable ({type(e).__name__})"
    source_status.append(("OpenRouter 新模型", "上线", status, got))
    print(f"  [上架] OpenRouter: {status}, {got}")

    # ---- L2 媒体 ----
    for feed_name, feed_url, ai_pure in MEDIA_FEEDS:
        status, got, hits = "ok", 0, 0
        try:
            items = parse_rss(fetch(feed_url)) or []
            got = len(items)
            norm = []
            for it in items:
                d = parse_date(it["date"])
                if d:
                    it["date"] = d.date().isoformat()
                    if first_run and (TODAY - d.date()).days > MEDIA_WINDOW_DAYS:
                        continue
                norm.append(it)
            attributed = attribute(norm, feed_name, "媒体")
            hits = len(attributed)
            for family, it in attributed:
                add(family, "媒体", it, feed_name)
        except urllib.error.HTTPError as e:
            status = f"http {e.code}"
        except Exception as e:
            status = f"unreachable ({type(e).__name__})"
        source_status.append((feed_name, "L2 媒体", status, got))
        print(f"  [L2] {feed_name}: {status}, 条目 {got}, 家族命中 {hits}")

    # ---- L3 开源 ----
    for family, org in GH_MODEL_ORGS:
        status, got = "ok", 0
        try:
            items = parse_gh_events(org)
            if first_run:
                items = [it for it in items if it["date"] >= (TODAY - datetime.timedelta(days=GH_WINDOW_DAYS)).isoformat()]
            got = len(items)
            for it in items:
                add(family, "开源", it, f"github/{org}")
        except urllib.error.HTTPError as e:
            status = f"http {e.code}"
        except Exception as e:
            status = f"unreachable ({type(e).__name__})"
        source_status.append((f"{family}·github/{org}", "L3 开源", status, got))
        print(f"  [L3] {family}·github/{org}: {status}, {got}")

    # ---- 去重 + 归并 ----
    def title_key(t):
        return re.sub(r"[^\w一-鿿]", "", t)[:18]

    new_total = 0
    output = {}
    for family, items in pool.items():
        items.sort(key=lambda x: x.get("date", ""), reverse=True)
        seen_keys = {}
        out_items = []
        for it in items:
            link = it["link"]
            if link in state and not first_run:
                continue
            state[link] = today
            k = title_key(it["title"])
            if k in seen_keys:
                existing = [seen_keys[k]["source"]] + seen_keys[k].get("extra_sources", [])
                if it["source"] not in existing:
                    seen_keys[k].setdefault("extra_sources", []).append(it["source"])
                continue
            it.setdefault("extra_sources", [])
            seen_keys[k] = it
            out_items.append(it)
        by_layer = {}
        final = []
        for it in out_items:
            by_layer[it["layer"]] = by_layer.get(it["layer"], 0) + 1
            if by_layer[it["layer"]] > MAX_PER_FAMILY_LAYER:
                continue
            final.append(it)
        if final:
            output[family] = final
            new_total += len(final)

    # ---------- 生成日报 ----------
    os.makedirs(REPORT_DIR, exist_ok=True)
    path = os.path.join(REPORT_DIR, f"大模型进展-{today}.md")
    c = [
        f"# AI大模型进展日报 · {today}",
        "",
        f"> 运行环境：**{env}** ｜ 生成时间：{NOW.strftime('%Y-%m-%d %H:%M')}（北京时间） ｜ 当日收录：**{new_total}** 条",
        "> 四层信号：**[官方]** 官方新闻/HF 模型发布 ｜ **[上线]** OpenRouter 新上架 ｜ **[媒体]** 量子位/IT之家按模型家族归口（宽口径） ｜ **[开源]** 厂商 GitHub 发布事件",
        "> 链接级去重 + 同家族同日标题归并。",
        "> ⚠️ 诚实边界：媒体层宽口径（含评测/观点/传闻，标题含'消息称'的为未官宣信息）；Anthropic 无公开 RSS，Claude 动态走媒体+开源通道。",
        "",
    ]
    if new_total == 0:
        c += ["## 今日无收录", "", "各源均有快照且无新条目（或源不可达，见文末源健康度表）。", ""]

    for family in FAMILY_ORDER:
        if not output.get(family):
            continue
        c.append(f"## {family}")
        for it in output[family]:
            d = it.get("date") or ""
            dm = re.match(r"(\d{4}-\d{2}-\d{2})", d)
            dt = f"{dm.group(1)} " if dm else ""
            src = it["source"]
            if it.get("extra_sources"):
                src = "/".join([src] + it["extra_sources"][:2])
            c.append(f"- {dt}**[{it['layer']}]** [{it['title']}]({it['link']})（{src}）")
        c.append("")

    c += ["## 源健康度", "", "| 源 | 层 | 状态 | 抓取 |", "|------|------|------|-----:|"]
    for name, layer, status, got in source_status:
        c.append(f"| {name} | {layer} | {status} | {got} |")
    c += ["", "## 待补通道", "",
          "- Anthropic 官方新闻页为 JS 渲染（无 RSS），Claude 动态目前靠媒体层覆盖",
          "- xAI/MiniMax 官方通道待补（Grok/海螺动态走媒体层）",
          "", "---", "[← 返回大模型日报索引](../topics/12-llm-daily/README.md) ｜ [← 仓库首页](../README.md)", ""]

    if os.path.exists(path):
        print(f"== 当日大模型日报已存在，不覆盖：{path}")
    else:
        open(path, "w", encoding="utf-8").write("\n".join(c))
        print(f"== 大模型日报已生成：{path}（收录 {new_total} 条）")

    # ---------- 分类页 ----------
    topic_dir = os.path.join(TOPICS_DIR, "12-llm-daily")
    os.makedirs(topic_dir, exist_ok=True)
    files = sorted(glob.glob(os.path.join(REPORT_DIR, "大模型进展-*.md")), reverse=True)[:30]
    t = [
        "# 🧠 AI大模型进展日报",
        "",
        "> 跟踪国内外主流大模型的新版本与最新能力：官方发布 + HF 模型 + OpenRouter 上架 + 媒体归口 + 开源事件。",
        "> 每天北京时间凌晨 1 点随仓库自动刷新生成。",
        "",
        "## 覆盖模型家族",
        "",
        "| 家族 | 通道 |",
        "|------|------|",
    ]
    for family in FAMILY_ORDER:
        t.append(f"| {family} | 官方/HF + 媒体关键词 + GitHub org |")
    t += ["", "## 最近报告", "", "| 日期 | 报告 |", "|------|------|"]
    for f in files:
        d = os.path.basename(f)[len("大模型进展-"):-3]
        t.append(f"| {d} | [查看 →](../../日报/{os.path.basename(f)}) |")
    t.append("")
    open(os.path.join(topic_dir, "README.md"), "w", encoding="utf-8").write("\n".join(t))
    print(f"== 分类页已更新：{topic_dir}/README.md（{len(files)} 篇报告）")

    save_state(state)


if __name__ == "__main__":
    main()
