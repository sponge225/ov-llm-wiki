import json

from openviking.wiki_mvp.oarel_input import load_oarel_mvp_documents


def test_oarel_loader_uses_only_source_docs(tmp_path):
    path = tmp_path / "oarel.jsonl"
    row = {
        "sample_id": "s1",
        "gold_related_work": "GOLD_SENTINEL",
        "target_paper": {"hierarchy": {"headline": "TARGET_SENTINEL"}},
        "source_docs": [
            {
                "doc_id": "OARW:541330",
                "title": "Who did What",
                "source_type": "full_paper_hierarchy",
                "year": 2016,
                "fields_of_study": ["NLP"],
                "hierarchy": {
                    "headline": "Who did What",
                    "content": [
                        {
                            "headline": "Abstract",
                            "content": [
                                {"headline": "[0]", "content": {"text": "SOURCE_ABSTRACT"}}
                            ],
                        }
                    ],
                },
            }
        ],
    }
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    docs = load_oarel_mvp_documents(str(path))

    assert len(docs) == 1
    assert docs[0].doc_id == "OARW_541330"
    assert docs[0].resource_uri == "viking://resources/OARW_541330/"
    assert "SOURCE_ABSTRACT" in docs[0].content_or_structure
    assert "GOLD_SENTINEL" not in docs[0].content_or_structure
    assert "TARGET_SENTINEL" not in docs[0].content_or_structure
