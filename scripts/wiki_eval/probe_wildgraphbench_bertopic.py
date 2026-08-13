#!/usr/bin/env python3
"""Run BERTopic on WildGraphBench source documents.

This uses BERTopic as the topic modeling framework, with open-source local
components only:

- scikit-learn TF-IDF + TruncatedSVD as precomputed document embeddings
- UMAP for reduction
- HDBSCAN for clustering
- BERTopic c-TF-IDF for topic representation

It avoids downloading sentence-transformer models so the probe can run in the
current environment.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import hdbscan
import numpy as np
import umap
from bertopic import BERTopic
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import CountVectorizer, TfidfVectorizer
from sklearn.preprocessing import Normalizer


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "data/wiki_eval/topic_probe_bertopic"
BASELINE_SCRIPT_DIR = ROOT / "scripts/wiki_eval"
sys.path.insert(0, str(BASELINE_SCRIPT_DIR))

from probe_wildgraphbench_topic_discovery import load_docs, tokenize  # noqa: E402


RANDOM_SEED = 13
SVD_COMPONENTS = 100
MAX_FEATURES = 12000
MIN_DF = 3
MAX_DF = 0.45


def build_text(doc: dict[str, Any]) -> str:
    return f"{doc['title']}\n{doc['text']}"


def build_embeddings(texts: list[str]):
    vectorizer = TfidfVectorizer(
        tokenizer=tokenize,
        token_pattern=None,
        lowercase=False,
        min_df=MIN_DF,
        max_df=MAX_DF,
        max_features=MAX_FEATURES,
        sublinear_tf=True,
    )
    tfidf = vectorizer.fit_transform(texts)
    n_components = min(SVD_COMPONENTS, tfidf.shape[1] - 1, tfidf.shape[0] - 1)
    svd = TruncatedSVD(n_components=n_components, random_state=RANDOM_SEED)
    normalizer = Normalizer(copy=False)
    embeddings = normalizer.fit_transform(svd.fit_transform(tfidf))
    return embeddings


def summarize_bertopic(
    docs: list[dict[str, Any]],
    model: BERTopic,
    topics: list[int],
    probabilities,
) -> dict[str, Any]:
    labels = np.array(topics)
    probs = np.asarray(probabilities) if probabilities is not None else np.ones(len(docs))
    label_counts = Counter(int(x) for x in labels)
    topic_ids = sorted(x for x in label_counts if x != -1)
    clusters = []
    for topic_id in topic_ids:
        idxs = np.where(labels == topic_id)[0]
        topic_words = [word for word, _ in model.get_topic(topic_id)[:10]]
        order = idxs[np.argsort(probs[idxs])[::-1]][:5]
        topic_counts = Counter(docs[i]["topic"] for i in idxs)
        domain_counts = Counter(docs[i]["domain"] for i in idxs)
        clusters.append(
            {
                "cluster_id": f"bertopic_t{topic_id:03d}",
                "raw_topic": int(topic_id),
                "size": int(len(idxs)),
                "top_terms": topic_words,
                "domain_distribution": domain_counts.most_common(6),
                "topic_distribution": topic_counts.most_common(6),
                "representative_docs": [
                    {
                        "doc_id": docs[i]["doc_id"],
                        "title": docs[i]["title"],
                        "domain": docs[i]["domain"],
                        "topic": docs[i]["topic"],
                        "local_path": docs[i]["local_path"],
                        "membership_probability": float(probs[i]) if probs.ndim == 1 else None,
                    }
                    for i in order
                ],
            }
        )
    clusters.sort(key=lambda c: c["size"], reverse=True)
    sizes = [c["size"] for c in clusters]
    dominant_ratios = [
        c["topic_distribution"][0][1] / c["size"]
        for c in clusters
        if c["topic_distribution"] and c["size"]
    ]
    return {
        "method": "BERTopic with local TF-IDF/SVD embeddings",
        "doc_count": len(docs),
        "topic_count_excluding_noise": len(clusters),
        "noise_doc_count": int(label_counts.get(-1, 0)),
        "noise_ratio": float(label_counts.get(-1, 0) / len(docs)),
        "size_min": int(min(sizes)) if sizes else 0,
        "size_median": float(np.median(sizes)) if sizes else 0.0,
        "size_max": int(max(sizes)) if sizes else 0,
        "pure_topics_dominant_topic_ge_0_8": sum(1 for r in dominant_ratios if r >= 0.8),
        "mixed_topics_dominant_topic_lt_0_5": sum(1 for r in dominant_ratios if r < 0.5),
        "mean_dominant_topic_ratio": float(np.mean(dominant_ratios)) if dominant_ratios else 0.0,
        "clusters": clusters,
    }


def write_report(docs: list[dict[str, Any]], report: dict[str, Any]) -> None:
    lines = [
        "# WildGraphBench BERTopic 实测",
        "",
        "本报告使用 `BERTopic` 包实际运行主题发现。为了避免外部模型下载，文档 embedding 使用本地开源 `scikit-learn TF-IDF + TruncatedSVD` 预计算，BERTopic 负责 UMAP、HDBSCAN 和 c-TF-IDF topic representation。输入文本已先做基础网页清洗，删除 URL、cookie/subscribe/share/nav/footer、明显脚本字段、重复行和符号占比过高的页面残留。",
        "",
        "## 语料",
        "",
        f"- sampled_docs: {len(docs)}",
        "- source: `data/wiki_eval/wildgraphbench_candidate_doc_sets.jsonl`",
        "",
        "## 指标",
        "",
        f"- topic_count_excluding_noise: {report['topic_count_excluding_noise']}",
        f"- noise_doc_count / noise_ratio: {report['noise_doc_count']} / {report['noise_ratio']:.3f}",
        f"- size_min / median / max: {report['size_min']} / {report['size_median']} / {report['size_max']}",
        f"- pure_topics_dominant_topic_ge_0_8: {report['pure_topics_dominant_topic_ge_0_8']}",
        f"- mixed_topics_dominant_topic_lt_0_5: {report['mixed_topics_dominant_topic_lt_0_5']}",
        f"- mean_dominant_topic_ratio: {report['mean_dominant_topic_ratio']:.3f}",
        "",
        "## Top Topics",
        "",
    ]
    for cluster in report["clusters"][:20]:
        lines.append(
            f"### {cluster['cluster_id']} | size={cluster['size']} | terms={', '.join(cluster['top_terms'][:8])}"
        )
        lines.append("")
        lines.append(
            "topics: "
            + "; ".join(f"{topic} ({count})" for topic, count in cluster["topic_distribution"][:4])
        )
        lines.append("")
        lines.append("representative_docs:")
        for doc in cluster["representative_docs"][:3]:
            prob = doc["membership_probability"]
            prefix = f"p={prob:.3f} " if prob is not None else ""
            lines.append(f"- {prefix}[{doc['domain']}/{doc['topic']}] {doc['title']}")
        lines.append("")

    (OUT_DIR / "wildgraphbench_bertopic_probe_summary.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    docs = load_docs()
    texts = [build_text(doc) for doc in docs]
    embeddings = build_embeddings(texts)

    vectorizer_model = CountVectorizer(
        tokenizer=tokenize,
        token_pattern=None,
        lowercase=False,
        min_df=MIN_DF,
        max_df=MAX_DF,
        max_features=MAX_FEATURES,
    )
    umap_model = umap.UMAP(
        n_neighbors=15,
        n_components=5,
        metric="cosine",
        random_state=RANDOM_SEED,
        low_memory=True,
    )
    hdbscan_model = hdbscan.HDBSCAN(
        min_cluster_size=10,
        min_samples=5,
        metric="euclidean",
        prediction_data=True,
    )
    topic_model = BERTopic(
        embedding_model=None,
        vectorizer_model=vectorizer_model,
        umap_model=umap_model,
        hdbscan_model=hdbscan_model,
        calculate_probabilities=False,
        verbose=False,
    )
    topics, probabilities = topic_model.fit_transform(texts, embeddings=embeddings)
    report = summarize_bertopic(docs, topic_model, topics, probabilities)

    (OUT_DIR / "bertopic_topics.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    topic_info = topic_model.get_topic_info()
    topic_info.to_csv(OUT_DIR / "bertopic_topic_info.csv", index=False)
    write_report(docs, report)

    print(f"sampled_docs={len(docs)}")
    print(
        "bertopic: topics={topic_count_excluding_noise} noise={noise_doc_count}/{noise_ratio:.3f} "
        "pure08={pure_topics_dominant_topic_ge_0_8} mixed05={mixed_topics_dominant_topic_lt_0_5} "
        "dominant={mean_dominant_topic_ratio:.3f}".format(**report)
    )
    print(f"wrote {OUT_DIR}")


if __name__ == "__main__":
    main()
