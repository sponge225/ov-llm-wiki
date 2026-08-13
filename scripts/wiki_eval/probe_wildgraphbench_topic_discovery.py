#!/usr/bin/env python3
"""Lightweight topic discovery probe for WildGraphBench source documents.

This intentionally avoids heavy optional dependencies. It is a low-cost baseline
to check whether the selected corpus has clusterable topic signal before trying
BERTopic/UMAP/HDBSCAN.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
CANDIDATES = ROOT / "data/wiki_eval/wildgraphbench_candidate_doc_sets.jsonl"
OUT_DIR = ROOT / "data/wiki_eval/topic_probe"

MAX_DOCS = 900
MAX_WORDS_PER_DOC = 5000
MAX_VOCAB = 7000
MIN_DF = 3
MAX_DF_RATIO = 0.45
K_VALUES = [16, 24, 32]
KMEANS_ITERS = 35
RANDOM_SEED = 13


DOMAIN_ZH = {
    "culture": "文化",
    "geography": "地理/国家概况",
    "health": "健康/公共卫生",
    "human_activities": "人类活动",
    "people": "人物",
    "religion": "宗教",
    "society": "社会",
    "technology": "技术/平台",
    "philosophy": "哲学/政治思想",
    "history": "历史",
    "nature": "自然/灾害",
    "mathematics": "数学",
}


STOPWORDS = {
    "about",
    "above",
    "after",
    "again",
    "against",
    "also",
    "although",
    "among",
    "around",
    "because",
    "been",
    "before",
    "being",
    "between",
    "both",
    "could",
    "during",
    "each",
    "from",
    "further",
    "have",
    "having",
    "here",
    "into",
    "itself",
    "more",
    "most",
    "other",
    "over",
    "same",
    "should",
    "some",
    "such",
    "than",
    "that",
    "their",
    "them",
    "then",
    "there",
    "these",
    "they",
    "this",
    "those",
    "through",
    "under",
    "until",
    "very",
    "were",
    "what",
    "when",
    "where",
    "which",
    "while",
    "with",
    "within",
    "would",
    "www",
    "http",
    "https",
    "html",
    "utm",
    "taboola",
    "colorbox",
    "popup",
    "template",
    "wp-content",
    "uploads",
    "category",
    "tag",
    "php",
    "net",
    "gov",
    "doi",
    "scholar",
    "worldcat",
    "cookies",
    "gdpr",
    "tacker",
    "probe",
    "gif",
    "int",
    "htm",
    "aspx",
    "archived",
    "retrieved",
    "article",
    "page",
    "pages",
    "edit",
    "source",
    "references",
    "external",
    "links",
    "january",
    "february",
    "march",
    "april",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
    "nytimes",
    "usatoday",
    "theguardian",
    "guardian",
    "guim",
    "pcgamer",
    "forbes",
    "deadline",
    "variety",
    "cnn",
    "bbc",
    "npr",
    "pbs",
    "ncbi",
    "nlm",
    "nih",
    "pmc",
    "articles",
}


TOKEN_RE = re.compile(r"[a-z][a-z0-9][a-z0-9\-]{1,}")
URL_RE = re.compile(r"https?://\S+|www\.\S+")
MARKDOWN_LINK_RE = re.compile(r"!?\[[^\]]*\]\([^)]*\)")
HTML_TAG_RE = re.compile(r"<[^>]+>")

BOILERPLATE_LINE_TERMS = {
    "advertisement",
    "all rights reserved",
    "allow cookies",
    "already have an account",
    "back to top",
    "by continuing",
    "click here",
    "cookie policy",
    "copyright",
    "create account",
    "daily newsletter",
    "follow us",
    "for more information",
    "home page",
    "log in",
    "login",
    "newsletter",
    "privacy policy",
    "read more",
    "related articles",
    "share this",
    "sign in",
    "sign up",
    "skip to content",
    "subscribe",
    "terms of service",
    "updated at",
    "use cookies",
}


def word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9][A-Za-z0-9\-']*", text))


def is_boilerplate_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    lower = stripped.lower()
    if any(term in lower for term in BOILERPLATE_LINE_TERMS):
        return True
    if URL_RE.fullmatch(stripped):
        return True
    if len(stripped) <= 2:
        return True
    alpha_count = sum(ch.isalpha() for ch in stripped)
    if len(stripped) > 20 and alpha_count / len(stripped) < 0.35:
        return True
    tokens = tokenize(stripped)
    if len(stripped) < 80 and len(tokens) <= 1:
        return True
    return False


def clean_reference_text(text: str) -> str:
    """Remove obvious web boilerplate before topic discovery.

    This is intentionally conservative. It does not try to summarize or rewrite
    the document; it only removes page chrome that should not become topic terms.
    """

    cleaned_lines: list[str] = []
    seen: set[str] = set()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = HTML_TAG_RE.sub(" ", line)
        line = MARKDOWN_LINK_RE.sub(" ", line)
        line = URL_RE.sub(" ", line)
        line = re.sub(r"\b(x[0-9a-f]{2}|utm_[a-z_]+|wp-[a-z-]+)\b", " ", line, flags=re.I)
        line = re.sub(r"[_*#`|{}\[\]<>]+", " ", line)
        line = re.sub(r"\s+", " ", line).strip()
        if is_boilerplate_line(line):
            continue
        normalized = line.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        cleaned_lines.append(line)

    cleaned = " ".join(cleaned_lines)
    return " ".join(cleaned.split()[:MAX_WORDS_PER_DOC])


def tokenize(text: str) -> list[str]:
    tokens = []
    for token in TOKEN_RE.findall(text.lower()):
        token = token.strip("-")
        if len(token) < 3 or token in STOPWORDS:
            continue
        if token.isdigit():
            continue
        tokens.append(token)
    return tokens


def load_docs() -> list[dict[str, Any]]:
    docs_by_path: dict[str, dict[str, Any]] = {}
    for line in CANDIDATES.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        for doc in row.get("source_docs", []):
            local_path = doc.get("local_path")
            if not local_path or local_path in docs_by_path:
                continue
            path = ROOT / local_path
            if not path.exists():
                continue
            raw_text = path.read_text(encoding="utf-8", errors="ignore")
            cleaned_text = clean_reference_text(raw_text)
            raw_wc = word_count(raw_text)
            clean_wc = word_count(cleaned_text)
            if clean_wc < 300:
                continue
            docs_by_path[local_path] = {
                "doc_id": doc.get("doc_id", ""),
                "title": doc.get("title", path.stem),
                "url": doc.get("url", ""),
                "local_path": local_path,
                "domain": row.get("domain", ""),
                "topic": row.get("topic", ""),
                "word_count": clean_wc,
                "raw_word_count": raw_wc,
                "clean_word_count": clean_wc,
                "text": cleaned_text,
            }

    docs = list(docs_by_path.values())
    # Keep coverage broad: take the highest-word docs per domain/topic round-robin.
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for doc in docs:
        buckets[(doc["domain"], doc["topic"])].append(doc)
    for bucket in buckets.values():
        bucket.sort(key=lambda x: x["word_count"], reverse=True)

    selected: list[dict[str, Any]] = []
    while len(selected) < MAX_DOCS:
        progressed = False
        for key in sorted(buckets):
            bucket = buckets[key]
            if bucket:
                selected.append(bucket.pop(0))
                progressed = True
                if len(selected) >= MAX_DOCS:
                    break
        if not progressed:
            break
    return selected


def build_tfidf(docs: list[dict[str, Any]]) -> tuple[np.ndarray, list[str], list[Counter[str]]]:
    counters: list[Counter[str]] = []
    df: Counter[str] = Counter()
    for doc in docs:
        counts = Counter(tokenize(doc["text"]))
        counters.append(counts)
        df.update(counts.keys())

    n_docs = len(docs)
    max_df = int(n_docs * MAX_DF_RATIO)
    candidates = [
        (term, freq)
        for term, freq in df.items()
        if freq >= MIN_DF and freq <= max_df
    ]
    candidates.sort(key=lambda x: (x[1], x[0]), reverse=True)
    vocab = [term for term, _ in candidates[:MAX_VOCAB]]
    term_to_idx = {term: i for i, term in enumerate(vocab)}

    x = np.zeros((n_docs, len(vocab)), dtype=np.float64)
    idf = np.zeros(len(vocab), dtype=np.float64)
    for term, idx in term_to_idx.items():
        idf[idx] = math.log((1 + n_docs) / (1 + df[term])) + 1.0

    for row_idx, counts in enumerate(counters):
        total = sum(counts.values()) or 1
        for term, count in counts.items():
            idx = term_to_idx.get(term)
            if idx is None:
                continue
            x[row_idx, idx] = (count / total) * idf[idx]

    norms = np.linalg.norm(x, axis=1, keepdims=True)
    x = x / np.maximum(norms, 1e-9)
    return x, vocab, counters


def spherical_kmeans(x: np.ndarray, k: int, seed: int) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    n_docs = x.shape[0]
    centers = x[rng.choice(n_docs, size=k, replace=False)].copy()
    labels = np.zeros(n_docs, dtype=np.int32)
    for _ in range(KMEANS_ITERS):
        sims = np.einsum("ij,kj->ik", x, centers, optimize=True)
        new_labels = np.argmax(sims, axis=1)
        if np.array_equal(new_labels, labels):
            break
        labels = new_labels
        for cluster_id in range(k):
            members = x[labels == cluster_id]
            if len(members) == 0:
                centers[cluster_id] = x[rng.integers(0, n_docs)]
                continue
            center = members.mean(axis=0)
            norm = np.linalg.norm(center)
            centers[cluster_id] = center / max(norm, 1e-9)
    return labels, centers


def cluster_report(
    docs: list[dict[str, Any]],
    x: np.ndarray,
    vocab: list[str],
    labels: np.ndarray,
    centers: np.ndarray,
    k: int,
) -> dict[str, Any]:
    clusters = []
    for cluster_id in range(k):
        idxs = np.where(labels == cluster_id)[0]
        if len(idxs) == 0:
            continue
        center = centers[cluster_id]
        top_term_idxs = np.argsort(center)[-12:][::-1]
        top_terms = [vocab[i] for i in top_term_idxs if center[i] > 0][:10]
        sims = np.einsum("ij,j->i", x[idxs], center, optimize=True)
        rep_order = idxs[np.argsort(sims)[::-1]][:5]
        domain_counts = Counter(docs[i]["domain"] for i in idxs)
        topic_counts = Counter(docs[i]["topic"] for i in idxs)
        clusters.append(
            {
                "cluster_id": f"k{k}_c{cluster_id:02d}",
                "size": int(len(idxs)),
                "top_terms": top_terms,
                "domain_distribution": domain_counts.most_common(6),
                "topic_distribution": topic_counts.most_common(6),
                "representative_docs": [
                    {
                        "doc_id": docs[i]["doc_id"],
                        "title": docs[i]["title"],
                        "domain": docs[i]["domain"],
                        "topic": docs[i]["topic"],
                        "local_path": docs[i]["local_path"],
                    }
                    for i in rep_order
                ],
            }
        )
    clusters.sort(key=lambda c: c["size"], reverse=True)
    sizes = [c["size"] for c in clusters]
    singletons = sum(1 for s in sizes if s == 1)
    small = sum(1 for s in sizes if s < 3)
    dominant_topic = []
    for c in clusters:
        if not c["topic_distribution"]:
            continue
        dominant_topic.append(c["topic_distribution"][0][1] / c["size"])
    return {
        "k": k,
        "cluster_count": len(clusters),
        "singleton_clusters": singletons,
        "small_clusters_lt3": small,
        "size_min": min(sizes) if sizes else 0,
        "size_median": float(np.median(sizes)) if sizes else 0,
        "size_max": max(sizes) if sizes else 0,
        "mean_dominant_topic_ratio": float(np.mean(dominant_topic)) if dominant_topic else 0.0,
        "clusters": clusters,
    }


def write_markdown(docs: list[dict[str, Any]], vocab: list[str], reports: list[dict[str, Any]]) -> None:
    lines = [
        "# WildGraphBench 主题发现 Probe",
        "",
        "说明：这是低成本 baseline（基线实验），用纯 Python + numpy 实现 TF-IDF + spherical k-means（球面 k-means）。它不是 BERTopic 的替代品，只用于判断当前文档集是否存在可聚类信号。",
        "",
        "## 字段和指标翻译",
        "",
        "- `sampled_docs`：本次抽样参与聚类的文档数。",
        "- `vocab_size`：TF-IDF 词表大小。",
        "- `source`：输入候选文档集合文件。",
        "- `Domain distribution`：抽样文档在 WildGraphBench domain 下的分布。",
        "- `k`：k-means 预设聚类数量。",
        "- `cluster_count`：实际生成的聚类数量。",
        "- `singleton_clusters`：只包含 1 篇文档的簇数量；越多说明容易退化成“一篇文档一个 topic”。",
        "- `small_clusters_lt3`：少于 3 篇文档的簇数量；少于 3 篇通常不满足 Wiki node 的最小支撑阈值。",
        "- `size_min / median / max`：簇大小的最小值 / 中位数 / 最大值。",
        "- `mean_dominant_topic_ratio`：每个簇中占比最高的原始 topic 的平均占比；越高说明聚类越贴近数据集原有主题，但它不是 Wiki node 质量指标。",
        "- `terms`：该簇的高权重代表词，用来粗看簇语义。",
        "- `topics`：该簇中文档对应的原始 WildGraphBench topic 分布。",
        "- `representative_docs`：离簇中心最近的代表文档，用于人工判断簇是否可解释。",
        "",
        "## 语料概况",
        "",
        f"- sampled_docs: {len(docs)}",
        f"- vocab_size: {len(vocab)}",
        f"- source: `data/wiki_eval/wildgraphbench_candidate_doc_sets.jsonl`",
        "",
        "Domain distribution（领域分布）:",
        "",
    ]
    for domain, count in Counter(d["domain"] for d in docs).most_common():
        domain_label = DOMAIN_ZH.get(domain, domain)
        lines.append(f"- {domain}: {count}（{domain_label}）")
    lines.extend(["", "## 聚类运行结果", ""])
    for report in reports:
        lines.extend(
            [
                f"### k = {report['k']}",
                "",
                f"- cluster_count（聚类数量）: {report['cluster_count']}",
                f"- singleton_clusters（单文档簇数量）: {report['singleton_clusters']}",
                f"- small_clusters_lt3（少于 3 篇文档的簇数量）: {report['small_clusters_lt3']}",
                f"- size_min / median / max（簇大小最小值 / 中位数 / 最大值）: {report['size_min']} / {report['size_median']} / {report['size_max']}",
                f"- mean_dominant_topic_ratio（平均主导 topic 占比）: {report['mean_dominant_topic_ratio']:.3f}",
                "",
            ]
        )
        for cluster in report["clusters"][:12]:
            lines.append(
                f"#### {cluster['cluster_id']} | size（簇大小）={cluster['size']} | terms（代表词）={', '.join(cluster['top_terms'][:8])}"
            )
            lines.append("")
            lines.append(
                "topics（原始 topic 分布）: "
                + "; ".join(f"{topic} ({count})" for topic, count in cluster["topic_distribution"][:4])
            )
            lines.append("")
            lines.append("representative_docs（代表文档）:")
            for doc in cluster["representative_docs"][:3]:
                lines.append(f"- [{doc['domain']}/{doc['topic']}] {doc['title']}")
            lines.append("")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "wildgraphbench_topic_probe_summary.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    docs = load_docs()
    x, vocab, _ = build_tfidf(docs)

    doc_meta = [{k: v for k, v in d.items() if k != "text"} for d in docs]
    (OUT_DIR / "sampled_docs.jsonl").write_text(
        "\n".join(json.dumps(d, ensure_ascii=False) for d in doc_meta) + "\n",
        encoding="utf-8",
    )

    reports = []
    for k in K_VALUES:
        labels, centers = spherical_kmeans(x, k, RANDOM_SEED + k)
        report = cluster_report(docs, x, vocab, labels, centers, k)
        reports.append(report)
        (OUT_DIR / f"cluster_candidates_k{k}.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    write_markdown(docs, vocab, reports)
    print(f"sampled_docs={len(docs)}")
    print(f"vocab_size={len(vocab)}")
    for report in reports:
        print(
            "k={k} clusters={cluster_count} singleton={singleton_clusters} "
            "small_lt3={small_clusters_lt3} size_median={size_median} "
            "size_max={size_max} dominant_topic={mean_dominant_topic_ratio:.3f}".format(
                **report
            )
        )
    print(f"wrote {OUT_DIR}")


if __name__ == "__main__":
    main()
