#!/usr/bin/env python3
"""Probe open-source topic discovery methods on WildGraphBench documents.

This script uses installed open-source libraries instead of the lightweight
handwritten baseline:

- scikit-learn TF-IDF + TruncatedSVD for document representations
- UMAP for nonlinear dimensionality reduction
- HDBSCAN for density-based topic candidate discovery

BERTopic is intentionally tested separately because importing the package can
pull transformer model dependencies and may be blocked by local environment
constraints.
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
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import Normalizer


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "data/wiki_eval/topic_probe_open_source"
BASELINE_SCRIPT_DIR = ROOT / "scripts/wiki_eval"
sys.path.insert(0, str(BASELINE_SCRIPT_DIR))

from probe_wildgraphbench_topic_discovery import load_docs, tokenize  # noqa: E402


RANDOM_SEED = 13
SVD_COMPONENTS = 100
MAX_FEATURES = 12000
MIN_DF = 3
MAX_DF = 0.45

RUNS = [
    {
        "run_id": "umap15_hdbscan10",
        "umap_n_neighbors": 15,
        "umap_n_components": 5,
        "hdbscan_min_cluster_size": 10,
        "hdbscan_min_samples": 5,
    },
    {
        "run_id": "umap30_hdbscan15",
        "umap_n_neighbors": 30,
        "umap_n_components": 5,
        "hdbscan_min_cluster_size": 15,
        "hdbscan_min_samples": 5,
    },
    {
        "run_id": "umap50_hdbscan20",
        "umap_n_neighbors": 50,
        "umap_n_components": 5,
        "hdbscan_min_cluster_size": 20,
        "hdbscan_min_samples": 10,
    },
]


def build_text(doc: dict[str, Any]) -> str:
    """Build a clustering text view from raw source docs.

    Text has already been cleaned in load_docs(). If this still produces noisy
    clusters, that is evidence that Step 2 should use Document Cards instead of
    source-page text.
    """

    return f"{doc['title']}\n{doc['text']}"


def top_terms_for_cluster(
    tfidf,
    feature_names: np.ndarray,
    labels: np.ndarray,
    cluster_id: int,
    top_n: int = 10,
) -> list[str]:
    idxs = np.where(labels == cluster_id)[0]
    if len(idxs) == 0:
        return []
    mean_vec = np.asarray(tfidf[idxs].mean(axis=0)).ravel()
    top_idxs = mean_vec.argsort()[-top_n:][::-1]
    return [str(feature_names[i]) for i in top_idxs if mean_vec[i] > 0]


def summarize_labels(
    docs: list[dict[str, Any]],
    tfidf,
    reduced: np.ndarray,
    labels: np.ndarray,
    probabilities: np.ndarray,
    feature_names: np.ndarray,
    run: dict[str, Any],
) -> dict[str, Any]:
    clusters = []
    label_counts = Counter(int(x) for x in labels)
    cluster_ids = sorted(x for x in label_counts if x != -1)

    for cluster_id in cluster_ids:
        idxs = np.where(labels == cluster_id)[0]
        probs = probabilities[idxs] if probabilities is not None else np.ones(len(idxs))
        order = idxs[np.argsort(probs)[::-1]][:5]
        topic_counts = Counter(docs[i]["topic"] for i in idxs)
        domain_counts = Counter(docs[i]["domain"] for i in idxs)
        clusters.append(
            {
                "cluster_id": f"{run['run_id']}_c{cluster_id:03d}",
                "raw_label": int(cluster_id),
                "size": int(len(idxs)),
                "top_terms": top_terms_for_cluster(tfidf, feature_names, labels, cluster_id),
                "domain_distribution": domain_counts.most_common(6),
                "topic_distribution": topic_counts.most_common(6),
                "representative_docs": [
                    {
                        "doc_id": docs[i]["doc_id"],
                        "title": docs[i]["title"],
                        "domain": docs[i]["domain"],
                        "topic": docs[i]["topic"],
                        "local_path": docs[i]["local_path"],
                        "membership_probability": float(probabilities[i]) if probabilities is not None else 1.0,
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
    pure_08 = sum(1 for ratio in dominant_ratios if ratio >= 0.8)
    mixed_05 = sum(1 for ratio in dominant_ratios if ratio < 0.5)

    return {
        "method": "scikit-learn TF-IDF + TruncatedSVD + UMAP + HDBSCAN",
        "run": run,
        "doc_count": len(docs),
        "cluster_count_excluding_noise": len(clusters),
        "noise_doc_count": int(label_counts.get(-1, 0)),
        "noise_ratio": float(label_counts.get(-1, 0) / len(docs)),
        "size_min": int(min(sizes)) if sizes else 0,
        "size_median": float(np.median(sizes)) if sizes else 0.0,
        "size_max": int(max(sizes)) if sizes else 0,
        "pure_clusters_dominant_topic_ge_0_8": pure_08,
        "mixed_clusters_dominant_topic_lt_0_5": mixed_05,
        "mean_dominant_topic_ratio": float(np.mean(dominant_ratios)) if dominant_ratios else 0.0,
        "clusters": clusters,
    }


def write_report(docs: list[dict[str, Any]], reports: list[dict[str, Any]]) -> None:
    lines = [
        "# WildGraphBench 开源主题发现方法实测",
        "",
        "本报告使用真实开源组件测试前面讨论的主题发现路线，而不是手写 baseline。",
        "",
        "使用方法：",
        "",
        "- `scikit-learn`：TF-IDF 和 TruncatedSVD，构造文档向量表示。",
        "- `umap-learn`：对文档向量降维。",
        "- `hdbscan`：基于密度发现候选主题簇，并允许噪声点 `-1`。",
        "",
        "注意：当前输入是经过基础清洗的 WildGraphBench reference page 文本加标题，还不是 Document Card。清洗会删除 URL、cookie/subscribe/share/nav/footer、明显脚本字段、重复行和符号占比过高的页面残留。如果仍出现网页模板、来源站点或引用格式噪声，说明后续 Step 2 更应该使用 Document Card，而不是 source-page 文本。",
        "",
        "## 语料",
        "",
        f"- sampled_docs: {len(docs)}",
        "- source: `data/wiki_eval/wildgraphbench_candidate_doc_sets.jsonl`",
        "",
        "Domain distribution:",
        "",
    ]
    for domain, count in Counter(d["domain"] for d in docs).most_common():
        lines.append(f"- {domain}: {count}")
    lines.extend(["", "## 指标说明", ""])
    lines.extend(
        [
            "- `cluster_count_excluding_noise`：不含噪声点的簇数量。",
            "- `noise_doc_count / noise_ratio`：HDBSCAN 判为噪声、不归入任何簇的文档数量和比例。",
            "- `pure_clusters_dominant_topic_ge_0_8`：主导原始 topic 占比不低于 0.8 的簇数量。",
            "- `mixed_clusters_dominant_topic_lt_0_5`：主导原始 topic 占比低于 0.5 的混合簇数量。",
            "- `mean_dominant_topic_ratio`：每个簇中最大原始 topic 占比的平均值，越高说明越贴近数据集原始 topic。",
            "",
            "这些指标只用于判断候选簇质量，不等于 Wiki node 质量。",
            "",
            "## Runs",
            "",
        ]
    )

    for report in reports:
        run = report["run"]
        lines.extend(
            [
                f"### {run['run_id']}",
                "",
                f"- method: {report['method']}",
                f"- UMAP: n_neighbors={run['umap_n_neighbors']}, n_components={run['umap_n_components']}",
                f"- HDBSCAN: min_cluster_size={run['hdbscan_min_cluster_size']}, min_samples={run['hdbscan_min_samples']}",
                f"- cluster_count_excluding_noise: {report['cluster_count_excluding_noise']}",
                f"- noise_doc_count / noise_ratio: {report['noise_doc_count']} / {report['noise_ratio']:.3f}",
                f"- size_min / median / max: {report['size_min']} / {report['size_median']} / {report['size_max']}",
                f"- pure_clusters_dominant_topic_ge_0_8: {report['pure_clusters_dominant_topic_ge_0_8']}",
                f"- mixed_clusters_dominant_topic_lt_0_5: {report['mixed_clusters_dominant_topic_lt_0_5']}",
                f"- mean_dominant_topic_ratio: {report['mean_dominant_topic_ratio']:.3f}",
                "",
            ]
        )
        for cluster in report["clusters"][:12]:
            lines.append(
                f"#### {cluster['cluster_id']} | size={cluster['size']} | terms={', '.join(cluster['top_terms'][:8])}"
            )
            lines.append("")
            lines.append(
                "topics: "
                + "; ".join(f"{topic} ({count})" for topic, count in cluster["topic_distribution"][:4])
            )
            lines.append("")
            lines.append("representative_docs:")
            for doc in cluster["representative_docs"][:3]:
                lines.append(
                    f"- p={doc['membership_probability']:.3f} [{doc['domain']}/{doc['topic']}] {doc['title']}"
                )
            lines.append("")

    (OUT_DIR / "wildgraphbench_open_source_topic_probe_summary.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    docs = load_docs()
    texts = [build_text(doc) for doc in docs]

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
    feature_names = vectorizer.get_feature_names_out()

    n_components = min(SVD_COMPONENTS, tfidf.shape[1] - 1, tfidf.shape[0] - 1)
    svd = TruncatedSVD(n_components=n_components, random_state=RANDOM_SEED)
    normalizer = Normalizer(copy=False)
    dense = normalizer.fit_transform(svd.fit_transform(tfidf))

    reports = []
    for run in RUNS:
        reducer = umap.UMAP(
            n_neighbors=run["umap_n_neighbors"],
            n_components=run["umap_n_components"],
            metric="cosine",
            random_state=RANDOM_SEED,
            low_memory=True,
        )
        reduced = reducer.fit_transform(dense)
        clusterer = hdbscan.HDBSCAN(
            min_cluster_size=run["hdbscan_min_cluster_size"],
            min_samples=run["hdbscan_min_samples"],
            metric="euclidean",
            prediction_data=True,
        )
        labels = clusterer.fit_predict(reduced)
        report = summarize_labels(
            docs=docs,
            tfidf=tfidf,
            reduced=reduced,
            labels=labels,
            probabilities=clusterer.probabilities_,
            feature_names=feature_names,
            run=run,
        )
        reports.append(report)
        (OUT_DIR / f"{run['run_id']}.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    write_report(docs, reports)
    print(f"sampled_docs={len(docs)}")
    for report in reports:
        print(
            "{run_id}: clusters={cluster_count_excluding_noise} noise={noise_doc_count}/{noise_ratio:.3f} "
            "pure08={pure_clusters_dominant_topic_ge_0_8} mixed05={mixed_clusters_dominant_topic_lt_0_5} "
            "dominant={mean_dominant_topic_ratio:.3f}".format(
                run_id=report["run"]["run_id"], **report
            )
        )
    print(f"wrote {OUT_DIR}")


if __name__ == "__main__":
    main()
