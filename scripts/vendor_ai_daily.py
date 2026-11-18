#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
厂商AI产品进展日报（awesome-data-platform / topics/11-vendor-ai）· v2 三层信号架构

每天抓取国内外云/数据/AI 厂商动态，按厂商归口生成 日报/厂商AI进展-YYYY-MM-DD.md。

三层信号（每条标注来源层级）：
  L1 官方  — 厂商官方博客 RSS/静态页 + Hugging Face 模型发布（权威但低频）
  L2 媒体  — 量子位/IT之家等中文媒体 RSS，按厂商关键词归口（主力，高密度，宽口径）
  L3 开源  — 厂商官方 GitHub org 公开事件（Release/新仓库，限 AI 相关仓库名）

设计原则（诚实边界）：
- 每源独立容错，抓不到就在"源健康度"表如实标注；
- 链接级去重快照 scripts/state/vendor_ai_seen.json + 同厂同日标题归并（多源报同一事件合并显示）；
- 同名日报不覆盖（保护人工撰写版）。

运行环境差异：Google/Snowflake/HF 在本地网络常不通，GitHub Action（美国 runner）正常，
报告头部记录运行环境，请结合源健康度表解读。
"""
import os
import re
import json
import glob
import time
import datetime
import html as html_mod
import urllib.request
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET

REPORT_DIR = os.environ.get("REPORT_DIR", "日报")
STATE = os.environ.get("STATE", "scripts/state/vendor_ai_seen.json")
TOPICS_DIR = os.environ.get("TOPICS_DIR", "topics")
GH_TOKEN = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")

# 报告日期用北京时间（读者口径）：GitHub 调度有延迟且 runner 用 UTC，
# 若按 UTC 计日期，北京早晨打开会看到"昨天"的报告
try:
    from zoneinfo import ZoneInfo
    NOW = datetime.datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
except Exception:
    NOW = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
TODAY = NOW.date()
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
TIMEOUT = 20
WINDOW_DAYS = 14          # L1 首次回看窗口（有快照后按增量）
MEDIA_WINDOW_DAYS = 3     # L2 媒体首次回看窗口
GH_WINDOW_DAYS = 7        # L3 开源事件首次回看窗口
MAX_PER_VENDOR_LAYER = 8 # 单厂商单层单日上限

# ---------- 厂商归口关键词（L2 媒体层 + 报告排序） ----------
VENDOR_KEYWORDS = {
    "阿里云": ["阿里云", "通义", "千问", "Qwen", "百炼", "魔搭", "ModelScope", "阿里", "灵骏", "PAI"],
    "字节跳动": ["字节", "豆包", "扣子", "Coze", "火山引擎", "方舟", "Seed", "即梦", "Trae", "抖音"],
    "百度": ["百度", "文心", "千帆", "ERNIE", "萝卜快跑", "Apollo"],
    "腾讯": ["腾讯", "混元", "元宝", " ima", "腾讯云"],
    "华为云": ["华为", "盘古", "昇腾", "ModelArts", "MindSpore", "昇思", "CloudMatrix", "鸿蒙"],
    "京东": ["京东", "言犀", "京慧", "京东云"],
    "DeepSeek": ["DeepSeek", "深度求索"],
    "AWS": ["AWS", "亚马逊云"],
    "Microsoft Azure": ["Azure", "微软云", "微软 Azure", "Copilot"],
    "Google Cloud": ["谷歌云", "Google Cloud", "Gemini", "Vertex"],
    "Databricks": ["Databricks"],
    "Snowflake": ["Snowflake"],
}
VENDOR_ORDER = ["阿里云", "字节跳动", "百度", "腾讯", "华为云", "京东", "DeepSeek",
                "AWS", "AWS 中国区", "Microsoft Azure", "Google Cloud", "Databricks", "Snowflake"]

# ---------- L1 官方源 ----------
# (厂商, 类型, 抓取方式, 地址, 备注)
OFFICIAL_SOURCES = [
    ("AWS", "官方博客", "rss", "https://aws.amazon.com/blogs/machine-learning/feed/", "AWS ML 官方博客"),
    ("AWS 中国区", "官方博客", "rss", "https://aws.amazon.com/cn/blogs/china/feed/", "AWS 中国区官方博客(中文)"),
    ("Microsoft Azure", "官方博客", "rss", "https://azure.microsoft.com/en-us/blog/feed/", "Azure 官方博客(筛AI相关)"),
    ("Google Cloud", "官方博客", "rss", "https://cloud.google.com/blog/rss", "Google Cloud 官方博客(筛AI相关)"),
    ("Databricks", "官方博客", "html", "https://www.databricks.com/blog", "Databricks 官方博客"),
    ("Snowflake", "官方博客", "rss", "https://www.snowflake.com/blog/feed/", "Snowflake 官方博客"),
    ("阿里云", "官方博客", "html", "https://www.alibabacloud.com/blog", "阿里云国际官方博客(英文)"),
    ("阿里云", "模型发布", "hf", "Qwen", "通义千问模型发布(Hugging Face API)"),
    ("字节跳动", "模型发布", "hf", "ByteDance", "字节模型发布(Hugging Face API)"),
    ("百度", "模型发布", "hf", "baidu", "文心模型发布(Hugging Face API)"),
    ("腾讯", "模型发布", "hf", "tencent", "混元模型发布(Hugging Face API)"),
    ("DeepSeek", "模型发布", "hf", "deepseek-ai", "DeepSeek 模型发布(Hugging Face API)"),
]

# ---------- L2 媒体源 ----------
# (媒体名, feed, 是否纯AI媒体(纯AI则免AI关键词过滤))
MEDIA_FEEDS = [
    ("量子位", "https://www.qbitai.com/feed", True),
    ("IT之家", "https://www.ithome.com/rss/", False),
]

# ---------- L3 开源源 ----------
# (厂商, GitHub orgs)
GH_ORGS = [
    ("阿里云", ["QwenLM", "aliyun"]),
    ("字节跳动", ["bytedance", "volcengine"]),
    ("百度", ["PaddlePaddle", "baidu"]),
    ("腾讯", ["Tencent-Hunyuan"]),
    ("华为云", ["mindspore-ai", "huaweicloud"]),
    ("京东", ["jd-opensource"]),
    ("DeepSeek", ["deepseek-ai"]),
]
# 开源事件只保留 AI 相关仓库名
GH_REPO_RE = re.compile(
    r"(ai|llm|lm\b|agent|model|hunyuan|qwen|seed|ernie|paddle|mindspore|ocr|embed|r"
    r"|rag|diffus|vl\b|asr|tts|speech|video-?gen|thinking|reason|omni|copilot|cua\b|gui\b|os\b|memory)", re.I)

# 媒体层明显非"产品进展"的人物/人事八卦，排除（宽口径不等于什么都收）
EXCLUDE_RE = re.compile(r"(退休|离职|去世|讣告|八卦|绯闻|回流政策)")

# 官方博客标题相关性过滤（AI/数据方向）
KW_RE = re.compile(
    r"(AI|artificial intelligence|machine learning|ML\b|LLM|GPT|Claude|Gemini|agent|RAG|vector"
    r"|generative|model|inference|training|fine-tun|embedding|copilot|assistant"
    r"|data platform|lakehouse|warehouse|catalog|lineage|governance|query engine"
    r"|大模型|人工智能|智能体|模型|推理|训练|数据智能)",
    re.I,
)

PENDING_SOURCES = [
    "厂商微信公众号：国内厂商官方发布主阵地，公开 RSS 通道不稳定，待接入 RSSHub 自建实例后补",
    "火山引擎/腾讯云/百度智能云/华为云/京东云官网产品动态页均为 JS 渲染，无静态抓取通道",
]

# ---------- 通用抓取 ----------
def fetch(url, headers=None):
    h = {"User-Agent": UA, "Accept": "*/*"}
    if headers:
        h.update(headers)
    if GH_TOKEN and "api.github.com" in url:
        h["Authorization"] = f"Bearer {GH_TOKEN}"
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8", errors="ignore")


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
        return None
    for it in root.iter():
        if it.tag.endswith("}item") or it.tag == "item":
            d = {c.tag.split("}")[-1]: (c.text or "").strip() for c in it}
            link = d.get("link") or d.get("guid") or ""
            date = d.get("pubDate") or d.get("date") or ""
            desc = strip_tags(d.get("description", ""))[:300]
            if link:
                items.append({"title": strip_tags(d.get("title", "")), "link": link, "date": date, "desc": desc})
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
            desc = strip_tags(d.get("summary", ""))[:300]
            if link:
                items.append({"title": strip_tags(d.get("title", "")), "link": link, "date": date, "desc": desc})
    return items


def parse_date(s):
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(s[:len(fmt) + 2], fmt)
        except ValueError:
            continue
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return datetime.datetime(*map(int, m.groups()))
    try:
        import email.utils
        d = email.utils.parsedate_to_datetime(s)
        return d.replace(tzinfo=None) if d.tzinfo else d
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
        if len(title.split()) < 6:
            continue
        if href.startswith("/"):
            href = base_url.rstrip("/") + href
        if not re.match(r"https?://", href):
            continue
        if not KW_RE.search(title):
            continue
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


# ---------- GitHub org 公开事件（L3） ----------
def parse_gh_org(org):
    url = f"https://api.github.com/orgs/{org}/events?per_page=60"
    events = json.loads(fetch(url))
    items = []
    for ev in events:
        repo_full = ev.get("repo", {}).get("name", "")
        repo_name = repo_full.split("/", 1)[1] if "/" in repo_full else repo_full
        created = (ev.get("created_at") or "")[:10]
        et = ev.get("type")
        if et == "ReleaseEvent":
            rel = ev.get("payload", {}).get("release", {})
            if not GH_REPO_RE.search(repo_name):
                continue
            items.append({
                "title": f"开源发布 {repo_full} {rel.get('tag_name', '')} {rel.get('name', '')[:50]}".strip(),
                "link": rel.get("html_url") or f"https://github.com/{repo_full}",
                "date": created,
            })
        elif et == "CreateEvent" and ev.get("payload", {}).get("ref_type") == "repository":
            if not GH_REPO_RE.search(repo_name):
                continue
            items.append({
                "title": f"新开源仓库 {repo_full}",
                "link": f"https://github.com/{repo_full}",
                "date": created,
            })
    return items


# ---------- L2 媒体归口 ----------
def attribute_media_items(items, feed_name, ai_pure):
    """把媒体条目归口到厂商。归口只看标题（防止正文顺带提及造成误归口）；
    非纯 AI 媒体的条目还须标题命中 AI 关键词（防无关厂商八卦）。"""
    out = []
    for it in items:
        title = it["title"]
        if EXCLUDE_RE.search(title):
            continue
        if not ai_pure and not KW_RE.search(title):
            continue
        for vendor, kws in VENDOR_KEYWORDS.items():
            if any(kw in title for kw in kws):
                out.append({**it, "vendor": vendor, "source": feed_name})
    return out


# ---------- 状态 ----------
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


# ---------- 主流程 ----------
def main():
    today = TODAY.isoformat()
    state = load_state()
    first_run = len(state) == 0
    env = "GitHub Action" if os.environ.get("GITHUB_ACTIONS") else "本地试跑"

    pool = {}
    source_status = []  # (源名, 层, 状态, 抓取, 命中/新增)
    pool_links = set()  # (vendor, link) 去重，防同一 feed 重复条目

    def add(vendor, layer, it, source):
        k = (vendor, it["link"])
        if k in pool_links:
            return
        pool_links.add(k)
        pool.setdefault(vendor, []).append({**it, "vendor": vendor, "layer": layer, "source": source})

    # ---- L1 官方层 ----
    for vendor, stype, kind, target, note in OFFICIAL_SOURCES:
        status, got = "ok", 0
        try:
            if kind == "rss":
                text = fetch(target)
                items = parse_rss(text)
                if items is None:
                    items = parse_html_blogs(text, target)
                keep = []
                feed_s = sld(urllib.parse.urlsplit(target).hostname)
                for it in items:
                    if not KW_RE.search(it["title"]):
                        continue
                    if sld(urllib.parse.urlsplit(it["link"]).hostname) != feed_s:
                        continue
                    d = parse_date(it["date"])
                    if d:
                        it["date"] = d.date().isoformat()
                        age = (TODAY - d.date()).days
                        if age > (WINDOW_DAYS if first_run else 7):  # 增量模式也限窗，防陈旧条目混入
                            continue
                    keep.append(it)
                items = keep
            elif kind == "html":
                items = parse_html_blogs(fetch(target), target)
                if first_run:
                    items = items[:MAX_PER_VENDOR_LAYER]
            elif kind == "hf":
                items = parse_hf(target)
                if items:
                    print(f"  [hf-diag] {vendor}/{target} 最新一条: {items[0]['link']} @ {items[0].get('created', '?')}")
                keep = []
                for it in items:
                    c = it.get("created") or ""
                    if c and (TODAY - datetime.date.fromisoformat(c)).days > WINDOW_DAYS:
                        continue
                    keep.append(it)
                items = keep
            got = len(items)
            for it in items:
                add(vendor, "官方", it, stype)
        except urllib.error.HTTPError as e:
            status = f"http {e.code}"
        except Exception as e:
            status = f"unreachable ({type(e).__name__})"
        source_status.append((f"{vendor}·{note}", "L1 官方", status, got))
        print(f"  [L1] {vendor}·{note}: {status}, 抓取 {got}")

    # ---- L2 媒体层 ----
    for feed_name, feed_url, ai_pure in MEDIA_FEEDS:
        status, got, hits = "ok", 0, 0
        try:
            items = parse_rss(fetch(feed_url)) or []
            got = len(items)
            # 日期归一化（始终执行），首跑再叠加窗口过滤
            norm = []
            for it in items:
                d = parse_date(it["date"])
                if d:
                    it["date"] = d.date().isoformat()
                    if first_run and (TODAY - d.date()).days > MEDIA_WINDOW_DAYS:
                        continue
                norm.append(it)
            items = norm
            attributed = attribute_media_items(items, feed_name, ai_pure)
            hits = len(attributed)
            for it in attributed:
                add(it["vendor"], "媒体", it, feed_name)
        except urllib.error.HTTPError as e:
            status = f"http {e.code}"
        except Exception as e:
            status = f"unreachable ({type(e).__name__})"
        source_status.append((feed_name, "L2 媒体", status, got))
        print(f"  [L2] {feed_name}: {status}, 条目 {got}, 厂商命中 {hits}")

    # ---- L3 开源层 ----
    for vendor, orgs in GH_ORGS:
        for org in orgs:
            status, got = "ok", 0
            try:
                items = parse_gh_org(org)
                if first_run:
                    items = [it for it in items if it["date"] >= (TODAY - datetime.timedelta(days=GH_WINDOW_DAYS)).isoformat()]
                got = len(items)
                for it in items:
                    add(vendor, "开源", it, f"github/{org}")
            except urllib.error.HTTPError as e:
                status = f"http {e.code}"
            except Exception as e:
                status = f"unreachable ({type(e).__name__})"
            source_status.append((f"{vendor}·github/{org}", "L3 开源", status, got))
            print(f"  [L3] {vendor}·github/{org}: {status}, 事件 {got}")

    # ---- 去重 + 归并 ----
    def title_key(t):
        return re.sub(r"[^\w一-鿿]", "", t)[:18]

    new_total = 0
    output = {}
    for vendor, items in pool.items():
        items.sort(key=lambda x: x.get("date", ""), reverse=True)
        seen_keys = {}
        for it in items:
            link = it["link"]
            if link in state and not first_run:
                continue
            state[link] = today
            k = title_key(it["title"])
            if k in seen_keys:
                # 同厂同事件多源 → 归并来源（同名来源不重复列）
                existing = [seen_keys[k]["source"]] + seen_keys[k].get("extra_sources", [])
                if it["source"] not in existing:
                    seen_keys[k].setdefault("extra_sources", []).append(it["source"])
                continue
            it.setdefault("extra_sources", [])
            seen_keys[k] = it
            output.setdefault(vendor, []).append(it)
        # 层内上限
        by_layer = {}
        final = []
        for it in output.get(vendor, []):
            by_layer[it["layer"]] = by_layer.get(it["layer"], 0) + 1
            if by_layer[it["layer"]] > MAX_PER_VENDOR_LAYER:
                continue
            final.append(it)
        output[vendor] = final
        new_total += len(final)

    # ---------- 生成日报 ----------
    os.makedirs(REPORT_DIR, exist_ok=True)
    path = os.path.join(REPORT_DIR, f"厂商AI进展-{today}.md")
    c = [
        f"# 厂商AI产品进展日报 · {today}",
        "",
        f"> 运行环境：**{env}** ｜ 生成时间：{NOW.strftime('%Y-%m-%d %H:%M')}（北京时间） ｜ 当日收录：**{new_total}** 条",
        "> 三层信号：**[官方]** 厂方博客/HF 模型发布 ｜ **[媒体]** 量子位/IT之家按厂商归口（宽口径） ｜ **[开源]** 厂商 GitHub org 发布事件",
        "> 链接级去重 + 同厂同日标题归并（多源报同一事件合并为一条，来源并列标注）。",
        "> ⚠️ 诚实边界：媒体层为宽口径，含发布/更新/观点/融资等一切命中厂商关键词的报道；本地试跑网络不通的源次日由 Action 补齐。",
        "",
    ]
    if new_total == 0:
        c += ["## 今日无收录", "", "各源均有快照且无新条目（或源不可达，见文末源健康度表）。", ""]

    for vendor in VENDOR_ORDER:
        if not output.get(vendor):
            continue
        items = output[vendor]
        c.append(f"## {vendor}")
        for it in items:
            d = it.get("date") or ""
            dm = re.match(r"(\d{4}-\d{2}-\d{2})", d)
            dt = f"{dm.group(1)} " if dm else ""
            src = it["source"]
            if it.get("extra_sources"):
                src = "/".join([src] + it["extra_sources"][:2])
            c.append(f"- {dt}**[{it['layer']}]** [{it['title']}]({it['link']})（{src}）")
        c.append("")

    # 源健康度表
    c += ["## 源健康度", "", "| 源 | 层 | 状态 | 抓取 |", "|------|------|------|-----:|"]
    for name, layer, status, got in source_status:
        c.append(f"| {name} | {layer} | {status} | {got} |")
    c += ["", "## 暂无稳定通道的源（待补）", ""]
    c += [f"- {p}" for p in PENDING_SOURCES]
    c += ["", "---", "[← 返回厂商AI进展索引](../topics/11-vendor-ai/README.md) ｜ [← 仓库首页](../README.md)", ""]

    if os.path.exists(path):
        print(f"== 当日厂商日报已存在，不覆盖：{path}")
    else:
        open(path, "w", encoding="utf-8").write("\n".join(c))
        print(f"== 厂商AI日报已生成：{path}（收录 {new_total} 条）")

    # ---------- 分类页 ----------
    topic_dir = os.path.join(TOPICS_DIR, "11-vendor-ai")
    os.makedirs(topic_dir, exist_ok=True)
    files = sorted(glob.glob(os.path.join(REPORT_DIR, "厂商AI进展-*.md")), reverse=True)[:30]
    t = [
        "# 📰 厂商AI产品进展（每日动态）",
        "",
        "> 跟踪国内外云/数据/AI 厂商动态，**三层信号架构**：官方发布（博客/HF 模型）+ 媒体归口（量子位/IT之家，宽口径）+ 开源事件（GitHub org）。",
        "> 每天北京时间凌晨 1 点随仓库自动刷新生成，新报告在最上面。",
        "",
        "## 覆盖厂商",
        "",
        "| 厂商 | 通道 |",
        "|------|------|",
    ]
    for vendor, kws in VENDOR_KEYWORDS.items():
        t.append(f"| {vendor} | 官方博客/HF模型 + 媒体关键词（{'/'.join(kws[:4])}…）+ GitHub org |")
    t += ["", "## 最近报告", "", "| 日期 | 报告 |", "|------|------|"]
    for f in files:
        d = os.path.basename(f)[len("厂商AI进展-"):-3]
        t.append(f"| {d} | [查看 →](../../日报/{os.path.basename(f)}) |")
    t += ["", "> 待补：厂商微信公众号通道（RSSHub 自建实例）。", ""]
    open(os.path.join(topic_dir, "README.md"), "w", encoding="utf-8").write("\n".join(t))
    print(f"== 分类页已更新：{topic_dir}/README.md（{len(files)} 篇报告）")

    save_state(state)


if __name__ == "__main__":
    main()
