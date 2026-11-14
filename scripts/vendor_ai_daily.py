#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
厂商AI产品进展日报（awesome-data-platform / topics/11-vendor-ai）

每天抓取国内外云/数据/AI 厂商的官方动态源，生成 日报/厂商AI进展-YYYY-MM-DD.md。

设计原则（诚实边界）：
- 每个源独立容错：抓不到就在"源健康度"表里如实标注，不影响其他源；
- 去重靠 scripts/state/vendor_ai_seen.json（链接级快照），二次运行只报新增；
- 同名日报不覆盖（保护人工撰写版）。

源分三类：
  rss  — 官方博客 RSS/Atom（AWS/Azure/Google Cloud/Snowflake 等）
  html — 官方博客列表页静态解析（Databricks/阿里云国际博客）
  hf   — Hugging Face 模型发布 API（国内厂商模型进展：Qwen/ByteDance/baidu/混元/DeepSeek）

运行环境差异：部分源（Google/Snowflake/HF）在本地网络不可达，在 GitHub Action
(美国 runner) 上正常——报告头部会记录运行环境，请结合"源健康度"表解读。
"""
import os
import re
import json
import glob
import time
import html as html_mod
import datetime
import urllib.request
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET

REPORT_DIR = os.environ.get("REPORT_DIR", "日报")
STATE = os.environ.get("STATE", "scripts/state/vendor_ai_seen.json")
TOPICS_DIR = os.environ.get("TOPICS_DIR", "topics")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
TIMEOUT = 20
WINDOW_DAYS = 14          # 首次运行回看的窗口（有快照后按增量）
HF_WINDOW_DAYS = 14
MAX_PER_SOURCE = 12       # 单源单次最多收录条数

# ---------- 源清单 ----------
# (厂商, 类型, 抓取方式, 地址, 备注说明)
SOURCES = [
    ("AWS", "云厂商", "rss", "https://aws.amazon.com/blogs/machine-learning/feed/", "AWS ML 官方博客"),
    ("AWS 中国区", "云厂商", "rss", "https://aws.amazon.com/cn/blogs/china/feed/", "AWS 中国区官方博客(中文)"),
    ("Microsoft Azure", "云厂商", "rss", "https://azure.microsoft.com/en-us/blog/feed/", "Azure 官方博客(筛AI相关)"),
    ("Google Cloud", "云厂商", "rss", "https://cloud.google.com/blog/rss", "Google Cloud 官方博客(筛AI相关)"),
    ("Databricks", "数据平台", "html", "https://www.databricks.com/blog", "Databricks 官方博客"),
    ("Snowflake", "数据平台", "rss", "https://www.snowflake.com/blog/feed/", "Snowflake 官方博客"),
    ("阿里云", "云厂商", "html", "https://www.alibabacloud.com/blog", "阿里云国际官方博客(英文,发布动态齐全)"),
    ("阿里 · Qwen", "模型", "hf", "Qwen", "通义千问模型发布(Hugging Face API)"),
    ("字节 · Seed/豆包", "模型", "hf", "ByteDance", "字节模型发布(Hugging Face API)"),
    ("百度 · ERNIE", "模型", "hf", "baidu", "文心模型发布(Hugging Face API)"),
    ("腾讯 · 混元", "模型", "hf", "tencent-hunyuan", "混元模型发布(Hugging Face API)"),
    ("DeepSeek", "模型", "hf", "deepseek-ai", "DeepSeek 模型发布(Hugging Face API)"),
]

# 官方博客标题相关性过滤（AI/数据方向）
KW_RE = re.compile(
    r"(AI|artificial intelligence|machine learning|ML\b|LLM|GPT|Claude|Gemini|agent|RAG|vector"
    r"|generative|model|inference|training|fine-tun|embedding|copilot|assistant"
    r"|data platform|lakehouse|warehouse|catalog|lineage|governance|query engine"
    r"|大模型|人工智能|智能体|模型|推理|训练|数据智能)",
    re.I,
)

PENDING_SOURCES = [
    "华为云：官网新闻/动态页为 JS 渲染，无稳定公开 RSS/静态通道（盘古模型未上 HF），待找替代源",
    "京东云：官网新闻页为 JS 渲染；京东 ChatRhino 等模型无公开 HF org，待补",
    "火山引擎/腾讯云/百度智能云官网产品动态页均为 JS 渲染；模型侧进展已分别走 HF 通道覆盖",
]

# ---------- 通用抓取 ----------
def fetch(url, as_text=True):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        raw = r.read()
    return raw.decode("utf-8", errors="ignore") if as_text else raw


def strip_tags(s):
    s = re.sub(r"<[^>]+>", "", s or "")
    return html_mod.unescape(s).strip()


def sld(host):
    parts = (host or "").split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


# ---------- RSS 解析 ----------
def parse_rss(text):
    items = []
    try:
        root = ET.fromstring(text.encode() if isinstance(text, str) else text)
    except ET.ParseError:
        return None  # 不是 XML（有些博客把 feed 做成了 HTML）
    for it in root.iter():
        if it.tag.endswith("}item") or it.tag == "item":
            d = {c.tag.split("}")[-1]: (c.text or "").strip() for c in it}
            link = d.get("link") or d.get("guid") or ""
            date = d.get("pubDate") or d.get("pubDate") or d.get("date") or ""
            if link:
                items.append({"title": strip_tags(d.get("title", "")), "link": link, "date": date})
        elif it.tag.endswith("}entry") or it.tag == "entry":
            d = {}
            for c in it:
                tag = c.tag.split("}")[-1]
                if tag == "link":
                    d.setdefault("link", c.attrib.get("href", ""))
                else:
                    d[tag] = (c.text or "").strip()
            link = d.get("link") or d.get("id") or ""
            date = d.get("published") or d.get("updated") or ""
            if link:
                items.append({"title": strip_tags(d.get("title", "")), "link": link, "date": date})
    return items


def parse_date(s):
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%a, %d %b %Y %H:%M:%S"):
        try:
            return datetime.datetime.strptime(s[:19] if "T" in s else s[:len(fmt) + 8] if fmt.startswith("%a") else s, fmt)
        except ValueError:
            continue
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return datetime.datetime(*map(int, m.groups()))
    try:
        import email.utils
        d = email.utils.parsedate_to_datetime(s)
        return d.replace(tzinfo=None) if d.tzinfo else d  # 统一 naive，避免与 now() 相减 TypeError
    except Exception:
        return None


# ---------- HTML 博客列表解析 ----------
def parse_html_blogs(text, base_url):
    items = []
    seen = set()
    for m in re.finditer(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', text, re.S):
        href, inner = m.group(1), m.group(2)
        title = strip_tags(re.sub(r"<[^>]+>", " ", inner))
        if len(title) < 15 or len(title) > 160:
            continue
        if len(title.split()) < 6:  # 短标签多为着陆页/导航，不是文章
            continue
        if href.startswith("/"):
            href = base_url.rstrip("/") + href
        if not re.match(r"https?://", href):
            continue
        if not (KW_RE.search(title)):
            continue
        # 只保留文章型链接（排除导航/标签/产品着陆页）
        if not re.search(r"/blog/|/post/|\d{4}/\d{2}", href):
            continue
        if re.search(r"/blog/(product|category|blog)/", href):
            continue
        key = href.split("?")[0]
        if key in seen:
            continue
        seen.add(key)
        items.append({"title": title, "link": key, "date": ""})
    return items[:40]


# ---------- Hugging Face 模型发布 ----------
def parse_hf(org):
    data = json.loads(fetch(f"https://huggingface.co/api/models?author={org}&sort=createdAt&direction=-1&limit=25"))
    items = []
    for m in data:
        items.append({
            "title": f"模型发布/更新：{m['id'].split('/', 1)[1]}（pipeline: {m.get('pipeline_tag', 'n/a')}，likes {m.get('likes', 0)}）",
            "link": f"https://huggingface.co/{m['id']}",
            "date": (m.get("createdAt") or "")[:10],
            "created": (m.get("createdAt") or "")[:10],
        })
    return items


# ---------- 主流程 ----------
def load_state():
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_state(state):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    cutoff = (datetime.date.today() - datetime.timedelta(days=90)).isoformat()
    state = {k: v for k, v in state.items() if v >= cutoff}
    json.dump(state, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    print(f"== 快照已回写：{len(state)} 条")


def main():
    today = datetime.date.today().isoformat()
    state = load_state()
    first_run = len(state) == 0
    env = "GitHub Action" if os.environ.get("GITHUB_ACTIONS") else "本地试跑"

    sections = []      # (厂商, 类型, 状态, 抓取数, 新增数, [items])
    new_total = 0
    today_str = time.strftime("%Y-%m-%d %H:%M")

    for vendor, vtype, kind, target, note in SOURCES:
        status, got, fresh, items_out = "ok", 0, 0, []
        try:
            if kind == "rss":
                text = fetch(target)
                items = parse_rss(text)
                if items is None:  # feed 返回的是 HTML 页面 → 走 HTML 解析
                    items = parse_html_blogs(text, target)
                # 日期过滤 + 相关性过滤
                keep = []
                feed_s = sld(urllib.parse.urlsplit(target).hostname)
                for it in items:
                    if not KW_RE.search(it["title"]):
                        continue
                    # 防外链噪音（赞助文/合作稿）：链接主域须与源一致
                    if sld(urllib.parse.urlsplit(it["link"]).hostname) != feed_s:
                        continue
                    d = parse_date(it["date"])
                    if d:
                        it["date"] = d.date().isoformat()  # 归一成 ISO，日报里可读
                        if (datetime.date.today() - d.date()).days > WINDOW_DAYS and first_run:
                            continue
                    keep.append(it)
                items = keep
            elif kind == "html":
                items = parse_html_blogs(fetch(target), target)
                if first_run:
                    items = items[:MAX_PER_SOURCE]
            elif kind == "hf":
                items = parse_hf(target)
                if items:
                    newest = items[0]
                    print(f"  [hf-diag] {vendor} 最新一条: {newest['link']} @ {newest.get('created','?')}")
                else:
                    status = "empty(API 无返回或窗口内无发布)"
                keep = []
                for it in items:
                    c = it.get("created") or ""
                    if c and (datetime.date.today() - datetime.date.fromisoformat(c)).days > HF_WINDOW_DAYS:
                        continue
                    keep.append(it)
                items = keep
            got = len(items)
            for it in items:
                link = it["link"]
                if link in state and not first_run:
                    continue
                items_out.append(it)
                state[link] = today
                fresh += 1
            items_out = items_out[:MAX_PER_SOURCE]
            if got == 0 and kind == "rss":
                status = "empty(窗口内无相关条目)"
        except urllib.error.HTTPError as e:
            status = f"http {e.code}"
        except Exception as e:
            status = f"unreachable ({type(e).__name__})"
        new_total += len(items_out)
        sections.append((vendor, vtype, status, got, len(items_out), items_out, note))
        print(f"  {vendor}: {status}, 抓取 {got}, 新增 {len(items_out)}")

    # ---------- 生成日报 ----------
    os.makedirs(REPORT_DIR, exist_ok=True)
    path = os.path.join(REPORT_DIR, f"厂商AI进展-{today}.md")
    c = [
        f"# 厂商AI产品进展日报 · {today}",
        "",
        f"> 运行环境：**{env}** ｜ 生成时间：{today_str} ｜ 当日新增：**{new_total}** 条",
        "> 口径：各厂商**官方**动态源（博客 RSS/静态页 + Hugging Face 模型发布 API）；链接级去重快照在 `scripts/state/vendor_ai_seen.json`。",
        "> ⚠️ 诚实边界：本地试跑网络不通的源（Google/Snowflake/HF）次日由 GitHub Action 补齐；标题是否真为'AI 产品进展'由关键词初筛，噪音请以点开原文为准。",
        "",
    ]
    if new_total == 0:
        c += ["## 今日无新增", "", "各源均有快照且无新条目（或源不可达，见文末源健康度表）。", ""]

    for vendor, vtype, status, got, fresh, items, note in sections:
        c.append(f"## {vendor}")
        c.append(f"> {vtype} · {note}")
        if items:
            for it in items:
                d = it.get("date") or ""
                dm = re.match(r"(\d{4}-\d{2}-\d{2})", d)
                dt = f"{dm.group(1)} " if dm else ""
                c.append(f"- {dt}[{it['title']}]({it['link']})")
        elif status.startswith(("http", "unreachable")):
            c.append(f"- ⚠️ 源本次不可达（{status}）。本地试跑时 Google/Snowflake/Hugging Face 常见网络不通，Action 环境正常。")
        else:
            c.append(f"- 窗口内无新增动态（{status}）。")
        c.append("")

    # 源健康度表
    c += ["## 源健康度", "", "| 厂商 | 类型 | 状态 | 抓取 | 新增 |", "|------|------|------|-----:|-----:|"]
    for vendor, vtype, status, got, fresh, items, note in sections:
        c.append(f"| {vendor} | {vtype} | {status} | {got} | {fresh} |")
    c += ["", "## 暂无稳定通道的源（待补）", ""]
    c += [f"- {p}" for p in PENDING_SOURCES]
    c += ["", "---", f"[← 返回厂商AI进展索引](../topics/11-vendor-ai/README.md) ｜ [← 仓库首页](../README.md)", ""]

    if os.path.exists(path):
        print(f"== 当日厂商日报已存在，不覆盖：{path}")
    else:
        open(path, "w", encoding="utf-8").write("\n".join(c))
        print(f"== 厂商AI日报已生成：{path}")

    # ---------- 分类页 topics/11-vendor-ai/README.md ----------
    topic_dir = os.path.join(TOPICS_DIR, "11-vendor-ai")
    os.makedirs(topic_dir, exist_ok=True)
    files = sorted(glob.glob(os.path.join(REPORT_DIR, "厂商AI进展-*.md")), reverse=True)[:30]
    t = [
        "# 📰 厂商AI产品进展（每日动态）",
        "",
        "> 跟踪国内外云/数据/AI 厂商的**官方**产品动态：博客发布 + 模型发布。",
        "> 每天北京时间凌晨 1 点随仓库自动刷新生成，新报告在最上面。",
        "",
        "## 覆盖厂商",
        "",
        "| 厂商 | 通道 | 说明 |",
        "|------|------|------|",
    ]
    for vendor, vtype, kind, target, note in SOURCES:
        ch = {"rss": "官方博客RSS", "html": "官方博客(静态页)", "hf": "模型发布(HF API)"}[kind]
        t.append(f"| {vendor} | {ch} | {note} |")
    t += ["", "## 最近报告", "", "| 日期 | 报告 |", "|------|------|"]
    for f in files:
        d = os.path.basename(f)[len("厂商AI进展-"):-3]
        t.append(f"| {d} | [查看 →](../../日报/{os.path.basename(f)}) |")
    t += ["", "> 待补源：华为云/京东云官网动态（JS 渲染无公开通道）。", ""]
    open(os.path.join(topic_dir, "README.md"), "w", encoding="utf-8").write("\n".join(t))
    print(f"== 分类页已更新：{topic_dir}/README.md（{len(files)} 篇报告）")

    save_state(state)


if __name__ == "__main__":
    main()
