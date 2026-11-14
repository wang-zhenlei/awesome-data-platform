#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
awesome-data-platform 自动刷新脚本。
自包含：搜索 → 去重 → 分类(10主题) → 质量过滤 → 词典中文化 → 生成多文件结构。
设计为在 GitHub Action 中运行（依赖 gh CLI + GH_TOKEN）。
"""
import os, re, json, subprocess, base64, sys, urllib.request, urllib.error, urllib.parse, time

REPO = os.environ.get("REPO", "wwwzhadan-droid/awesome-data-platform")
OUT = os.environ.get("OUT", "repo")  # 输出目录
GH = os.environ.get("GH", "gh")
TOKEN_ENV = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")

# ---------- 搜索关键词 ----------
KEYWORDS = {
 "local_llm": ["ollama","local-llm","llama.cpp","llama-cpp","llm-inference","vllm","local-ai",
   "text-generation-webui","gguf","ggml","exllama","llamafile","sglang","huggingface","tgi","koboldcpp","unsloth","mlx"],
 "rag": ["langchain","llamaindex","rag","vector-database","embedding","chroma","milvus","qdrant",
   "weaviate","pgvector","faiss","haystack","retrieval-augmented","rerank","hybrid-search"],
 "semantic_context": ["semantic-layer","context-engineering","agent-memory","llm-context","langgraph",
   "mem0","prompt-engineering","mcp","model-context-protocol","agentic","llm-agent","agent-framework",
   "multi-agent","tool-calling","function-calling","coding-agent",
   "semantica","context-layer","accountable-ai","context-infrastructure","graph-native",
   "context-graph","semantic-graph","agent-context","context-aware","long-term-memory",
   "second-brain","knowledge-graph-agent","semantic-memory","neuro-symbolic"],
 "data_quality": ["data-quality","great-expectations","data-contracts","data-observability","data-monitoring",
   "anomaly-detection","data-profiling","data-validation","data-test","soda-core","data-diff",
   "data-cleaning","data-governance","pii-detection","data-masking","data-privacy","sensitive-data",
   "gdpr-compliance","data-cleansing","data-lineage","data-freshness","data-trust","data-reliability",
   "data-catalog","data-stewardship","monte-carlo","data-observability-platform","dbt-test"],
 "data_platform": ["data-platform","data-catalog","metadata","data-asset","datahub","openmetadata",
   "amundsen","data-engineering","data-pipeline","airflow","dagster","prefect","lakehouse","iceberg",
   "data-mesh","sqlmesh","polars","duckdb","spark","trino","presto","metastore","data-discovery",
   "data-governance","data-contract","metadata-platform","data-stewardship","lake-formation","unity-catalog"],
 "metrics_bi": ["metrics-platform","headless-bi","metrics-layer","cube","dbt-metrics","lightdash",
   "metabase","superset","evidence-bi","business-intelligence","analytics-platform","dashboarding",
   "redash","olap","bi-tool","reporting","apache-superset","metric-store","semantic-metrics",
   "metrics-as-code","transformation","dbt-core","cube-js","headless-analytics"],
 "ontology": ["knowledge-graph","ontology","graph-database","neo4j","gremlin","graphrag",
   "graph-neural-network","rdf","sparql","owl-ontology","entity-resolution","knowledge-representation",
   "tigergraph","property-graph","graph-rag","graph-native","semantic-graph","context-graph",
   "graph-llm","kg-llm","cypher","labeled-property-graph","graph-engine","graph-analytics"],
 "integration": ["airbyte","elt","etl","data-integration","singer-io","meltano","dlt","data-sync",
   "change-data-capture","debezium","cdc","estuary","data-pipeline","data-loader","ingestion","fivetran"],
 "stream": ["kafka","flink","spark-streaming","redpanda","pulsar","ksql","stream-processing",
   "event-streaming","risingwave","materialize","druid","clickhouse","real-time-analytics","samza","confluent"],
 "mlops": ["mlflow","kubeflow","metaflow","zenml","bentoml","feast","feature-store","model-serving",
   "kserve","seldon","model-registry","evidently","mlops","ml-ops","model-monitoring",
   "experiment-tracking","bento","model-deployment","clearml","dvc"],
}

# ---------- 主题分类正则 ----------
THEMES = [
 ("🦙 本地 LLM 与推理", re.compile(r"\b(ollama|llama\.?cpp|llama-cpp|local[- ]?llm|local.?ai|llm.?inference|vllm|text-generation|inference.?server|gguf|ggml|exllama|llamafile|sglang|transformers|huggingface|\btgi\b|kobold|gptq|awq|\bmlx\b|unsloth)\b", re.I)),
 ("🔎 RAG 与向量检索", re.compile(r"\b(\brag\b|retrieval[- ]?augmented|vector[- ]?(database|db|store|search|engine)|embedding|chroma|milvus|qdrant|weaviate|pgvector|faiss|pinecone|haystack|llamaindex|llama-index|langchain|knowledge[- ]?base|document.?chat|chat.?with.?pdf|unstructured|rerank|hybrid.?search)\b", re.I)),
 ("🧠 语义层与上下文 / Agent", re.compile(r"\b(semantic[- ]?layer|context[- ]?engineering|agent[- ]?memory|llm[- ]?context|langgraph|mem0|memory.?layer|context.?window|prompt.?engineering|mcp|model[- ]?context[- ]?protocol|agentic|llm[- ]?agent|agent.?framework|agent.?orchestr|tool.?calling|function.?calling|multi[- ]?agent|skill.?framework|coding.?agent|accountable.?ai|context[- ]?infrastructure|context[- ]?layer|context[- ]?graph|semantic[- ]?memory|long[- ]?term[- ]?memory|second[- ]?brain|neuro[- ]?symbolic)\b", re.I)),
 ("✅ 数据质量与可观测性", re.compile(r"\b(data[- ]?quality|great[- ]?expectations|data[- ]?contracts?|data[- ]?observability|data[- ]?monitoring|anomaly[- ]?detection|data[- ]?profiling|data[- ]?validation|data[- ]?test|\bsoda\b|data[- ]?diff|data[- ]?clean|data[- ]?cleansing|data[- ]?audit|expectations|profiler|monte[- ]?carlo|freshness|data[- ]?governance|\bpii\b|data[- ]?masking|data[- ]?privacy|sensitive[- ]?data|\bgdpr\b|lineage|data[- ]?steward|quality[- ]?gate)\b", re.I)),
 ("🗄️ 数据中台与数据资产", re.compile(r"\b(data[- ]?platform|data[- ]?catalog|metadata|data[- ]?asset|datahub|openmetadata|amundsen|data[- ]?engineering|data[- ]?pipeline|airflow|dagster|prefect|lakehouse|iceberg|data[- ]?govern|warehouse|sqlmesh|data[- ]?mesh|polars|duckdb|\bspark\b|trino|presto|catalog|metastore|glue|data[- ]?discovery|data[- ]?stewardship|lake)\b", re.I)),
 ("📊 指标平台与 Headless BI", re.compile(r"\b(metrics[- ]?platform|headless[- ]?bi|metrics[- ]?layer|metric[- ]?store|semantic[- ]?metrics|\bcube\b|dbt[- ]?metrics|lightdash|metabase|superset|evidence|business[- ]?intelligence|analytics[- ]?platform|dashboard|reporting|olap|redash|bi[- ]?tool|looker)\b", re.I)),
 ("🧬 数据本体与知识图谱", re.compile(r"\b(knowledge[- ]?graph|ontology|graph[- ]?database|neo4j|gremlin|tigergraph|graphrag|graph[- ]?neural|rdf|sparql|owl[- ]?ontology|entity[- ]?resolution|knowledge[- ]?representation|cypher|property[- ]?graph|link[- ]?prediction|graph[- ]?mining|named[- ]?entity|graph[- ]?rag|kg-?llm|graph[- ]?native|semantic[- ]?graph|context[- ]?graph|graph[- ]?engine|graph[- ]?analytics|graph[- ]?llm)\b", re.I)),
 ("🔌 数据集成与 ELT/CDC", re.compile(r"\b(airbyte|\belt\b|\betl\b|data[- ]?integration|singer|meltsno|meltano|dlt[- ]?hub|data[- ]?sync|change[- ]?data[- ]?capture|debezium|\bcdc\b|estuary|fivetran|tap[- ]|stream[- ]?loader|ingest|ingestion|extract[- ]?and[- ]?load|data[- ]?loader)\b", re.I)),
 ("⚡ 流处理与实时数据", re.compile(r"\b(kafka|flink|spark[- ]?streaming|redpanda|pulsar|ksql|stream[- ]?processing|event[- ]?streaming|risingwave|materialize|druid|clickhouse|real[- ]?time[- ]?analytics|samza|confluent|flink[- ]?sql|kafka[- ]?stream|time[- ]?series[- ]?database)\b", re.I)),
 ("🤖 AI/ML 工程与 MLOps", re.compile(r"\b(mlflow|kubeflow|metaflow|zenml|bentoml|feast|feature[- ]?store|model[- ]?serving|kserve|seldon|model[- ]?registry|evidently|ml[- ]?ops|mlops|weights[- ]?and[- ]?biases|model[- ]?monitoring|experiment[- ]?tracking|bento|model[- ]?deployment|inference[- ]?server|ray[- ]?serve|clearml|\bdvc\b)\b", re.I)),
]

# ---------- 质量过滤 ----------
SPAM = re.compile(r"(porn|casino|gambling|viagra|escort|crypto[- ]?trading[- ]?bot|onlyfans)", re.I)
def is_quality(r):
    """去垃圾：刷量/低质项目剔除。"""
    stars = r.get("stargazersCount",0)
    forks = r.get("forksCount",0)
    name = r.get("fullName","")
    d = r.get("description") or ""
    blob = name + " " + d
    if SPAM.search(blob): return False
    # 高 star 但 fork 极少 → 疑似刷量（fork<30 且 star>10000 典型异常）
    if stars > 10000 and forks < 30: return False
    # 太小：star<50 且 fork<10
    if stars < 50 and forks < 10: return False
    return True

# ---------- 词典中文化 ----------
TRANS = {
 "inference":"推理","serving":"服务","engine":"引擎","framework":"框架","platform":"平台",
 "database":"数据库","vector":"向量","retrieval":"检索","augmented":"增强","generation":"生成",
 "embedding":"嵌入","knowledge":"知识","graph":"图","ontology":"本体","semantic":"语义",
 "context":"上下文","agent":"智能体","memory":"记忆","prompt":"提示词","tool":"工具","call":"调用",
 "multi":"多","orchestration":"编排","data":"数据","pipeline":"流水线","catalog":"目录",
 "metadata":"元数据","lineage":"血缘","asset":"资产","quality":"质量","observability":"可观测性",
 "monitoring":"监控","anomaly":"异常","detection":"检测","validation":"校验","test":"测试",
 "integration":"集成","change":"变更","capture":"捕获","stream":"流","processing":"处理",
 "real":"实时","time":"时","event":"事件","model":"模型","registry":"注册表","feature":"特征",
 "store":"存储","experiment":"实验","tracking":"追踪","deployment":"部署","high-throughput":"高吞吐",
 "memory-efficient":"低内存","efficient":"高效","open-source":"开源","self-host":"自托管","private":"私有",
 "local":"本地","offline":"离线","interface":"界面","client":"客户端","server":"服务器","library":"库",
 "toolkit":"工具集","dashboard":"仪表盘","reporting":"报表","analytics":"分析","business":"商业",
 "intelligence":"智能","warehouse":"数仓","lakehouse":"湖仓","lake":"湖","query":"查询","distributed":"分布式",
 "scalable":"可扩展","document":"文档","image":"图像","audio":"音频","video":"视频","voice":"语音",
 "speech":"语音","vision":"视觉","multimodal":"多模态","chat":"聊天","chatbot":"聊天机器人","assistant":"助手",
 "automation":"自动化","workflow":"工作流","fast":"快速","lightweight":"轻量","powerful":"强大","simple":"简单",
 "build":"构建","deploy":"部署","run":"运行","train":"训练","fine-tune":"微调","fine-tuning":"微调",
 "quantization":"量化","optimization":"优化","performance":"性能","production":"生产","enterprise":"企业级",
 "privacy":"隐私","secure":"安全","security":"安全","compliance":"合规","governance":"治理","contract":"契约",
 "freshness":"新鲜度","profile":"剖析","profiling":"剖析","schema":"模式","web":"网页","scraping":"抓取",
 "crawler":"爬虫","transform":"转换","language":"语言","text":"文本","natural":"自然","extraction":"抽取",
 "summary":"摘要","summarization":"摘要","translation":"翻译","classification":"分类","rerank":"重排",
 "hybrid":"混合","search":"搜索","index":"索引","indexing":"索引","unstructured":"非结构化",
}
def translate(desc):
    if not desc: return "暂无简介"
    if re.search(r"[\u4e00-\u9fff]", desc) and len(desc) < 120: return desc
    s = desc
    for en, zh in sorted(TRANS.items(), key=lambda x: -len(x[0])):
        s = re.sub(r"\b"+re.escape(en)+r"\b", zh, s, flags=re.I)
    return s.strip()

def slug(tname):
    return re.sub(r"[^\w\u4e00-\u9fff]+","-",tname).strip("-").lower()[:40]

# ---------- 搜索 ----------
def search(keyword, limit=30):
    """带节流与限流重试的搜索。GitHub Search API 二级限流约 30 次/分钟。"""
    def once():
        out = subprocess.check_output([GH,"search","repos",keyword,"--sort","stars","--limit",str(limit),
            "--json","fullName,stargazersCount,forksCount,description,url,updatedAt"], stderr=subprocess.DEVNULL)
        return json.loads(out)
    for attempt in range(3):
        try:
            r = once()
            time.sleep(2.2)  # 节流，避免二级限流
            return r
        except subprocess.CalledProcessError as e:
            err = (e.stderr or b"").decode(errors="ignore")
            if "secondary rate limit" in err.lower() or "rate limit" in err.lower() or "429" in err or "403" in err:
                wait = 30 * (attempt + 1)
                print(f"  rate-limited on '{keyword}', waiting {wait}s (attempt {attempt+1})", file=sys.stderr)
                time.sleep(wait)
                continue
            print(f"  search fail {keyword}: {err[:120]}", file=sys.stderr)
            return []
        except Exception as e:
            print(f"  search fail {keyword}: {e}", file=sys.stderr)
            return []
    return []

def main():
    print("== 搜索 ==")
    all_repos = {}
    for theme_key, kws in KEYWORDS.items():
        for kw in kws:
            for r in search(kw):
                n = r["fullName"]
                if n not in all_repos or r["stargazersCount"] > all_repos[n]["stargazersCount"]:
                    all_repos[n] = r
        print(f"  {theme_key}: cumul {len(all_repos)}")
    print(f"原始去重: {len(all_repos)}")

    print("== 质量过滤 ==")
    clean = [r for r in all_repos.values() if is_quality(r)]
    print(f"过滤后: {len(clean)}")

    print("== 分类 ==")
    buckets = {name: {} for name,_ in THEMES}
    unmatched = {}
    for r in clean:
        name = r["fullName"]; d = (r.get("description") or "")[:260]
        blob = name + " " + d
        stars = r["stargazersCount"]; url = r.get("url","")
        placed = False
        for tname, rx in THEMES:
            if rx.search(blob):
                if name not in buckets[tname] or stars > buckets[tname][name][0]:
                    buckets[tname][name] = (stars, d, url)
                placed = True; break
        if not placed:
            unmatched[name] = (stars, d, url)
    for tname,_ in THEMES:
        buckets[tname] = sorted(buckets[tname].items(), key=lambda kv:-kv[1][0])
    tot = sum(len(b) for b in buckets.values())

    print("== 生成文件 ==")
    import shutil
    if os.path.exists(OUT): shutil.rmtree(OUT)
    os.makedirs(OUT+"/topics", exist_ok=True)

    # 索引
    idx = ["# Awesome 数据智能平台\n", '<div align="center">\n',
        f'<img src="https://img.shields.io/badge/projects-{tot}+-blue" alt="projects"/> ',
        f'<img src="https://img.shields.io/badge/themes-{len(THEMES)}-green" alt="themes"/>\n', '</div>\n',
        "> 数据智能 / AI 基础设施 / 数据中台 / 指标平台 / 知识图谱 领域高 star 开源项目精选。\n",
        "> ⚠️ 简介为**字典机翻中文化**参考，以原仓库 README 为准。所有项目版权归原作者所有。\n",
        f"> 最近刷新：{time.strftime('%Y-%m-%d')}（GitHub Action 自动）\n",
        "## 📊 统计\n",
        f"- 主题数：**{len(THEMES)}**",
        f"- 收录项目：**{tot}** 条（过滤后）",
        f"- 原始检索 {len(all_repos)} → 质量过滤后 {len(clean)} → 归类 {tot}",
        f"- 未命中主题：{len(unmatched)} 条\n",
        "## 📑 主题目录\n", "| # | 主题 | 项目数 | 链接 |", "|--:|------|------:|------|"]
    for i,(tname,_) in enumerate(THEMES,1):
        fname = f"topics/{i:02d}-{slug(tname)}.md"
        idx.append(f"| {i} | {tname} | {len(buckets[tname])} | [查看 →](./{fname}) |")
    # 静态分类：厂商AI产品进展（由 vendor_ai_daily.py 每日生成，不走仓库搜索管线）
    idx.append(f"| {len(THEMES)+1} | 📰 厂商AI产品进展（每日动态） | 动态 | [查看 →](./topics/11-vendor-ai/README.md) |")
    idx += ["\n---\n","## 🏆 [全局 Top 100 总览](./topics/00-overview.md)\n",
        "## 🤝 [贡献指南](./CONTRIBUTING.md)\n",
        "## ⚖️ 声明\n",
        "本项目仅作信息检索与学习用途，不托管任何被收录项目代码。"
        "所有链接指向原作者仓库，版权归原作者所有。星标/fork 请回到原仓库。\n"]
    open(f"{OUT}/README.md","w").write("\n".join(idx))

    # 主题文件
    for i,(tname,_) in enumerate(THEMES,1):
        items = buckets[tname]
        fname = f"{OUT}/topics/{i:02d}-{slug(tname)}.md"
        c = [f"# {tname}\n", f"> 共 **{len(items)}** 个项目，按 star 降序。[← 返回首页](../README.md)\n",
            "| ⭐ Stars | 仓库 | 最近更新 | 中文简介 |", "|--------:|------|----------|----------|"]
        for name,(stars,d,url) in items:
            dz = translate(d).replace("|","\\|").replace("\n"," ")
            nm = f"[{name}]({url})" if url else name
            # 最近更新从 all_repos 取
            upd = all_repos.get(name,{}).get("updatedAt","")[:10]
            c.append(f"| {stars:,} | {nm} | {upd} | {dz} |")
        c.append("")
        open(fname,"w").write("\n".join(c))

    # 全局 Top100
    allitems = []
    for tname,_ in THEMES:
        for name,(stars,d,url) in buckets[tname]:
            allitems.append((stars,name,d,url,tname))
    allitems.sort(key=lambda x:-x[0])
    ov = ["# 🏆 全局 Top 100 总览\n", "> 跨主题按 star 降序。[← 返回首页](../README.md)\n",
        "| ⭐ | 仓库 | 主题 | 中文简介 |", "|---:|------|------|----------|"]
    for stars,name,d,url,tname in allitems[:100]:
        dz = translate(d).replace("|","\\|").replace("\n"," ")
        nm = f"[{name}]({url})" if url else name
        ov.append(f"| {stars:,} | {nm} | {tname.split(' ',1)[1]} | {dz} |")
    open(f"{OUT}/topics/00-overview.md","w").write("\n".join(ov))

    print(f"== 完成：{tot} 项，10 主题 + 总览 ==")
    for tname,_ in THEMES:
        print(f"  {tname}: {len(buckets[tname])}")

if __name__ == "__main__":
    main()
