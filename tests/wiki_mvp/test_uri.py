from openviking.wiki_mvp.config import WikiMVPConfig
from openviking.wiki_mvp.uri import node_root_uri, sanitize_node_id


def test_node_uri_has_no_corpus_id_layer():
    config = WikiMVPConfig()

    assert node_root_uri(config, "question_answering") == "viking://wiki/nodes/question_answering/"
    assert "corpus" not in node_root_uri(config, "question_answering")


def test_sanitize_node_id_is_stable():
    assert sanitize_node_id("Question Answering & Reading-Comprehension") == (
        "question_answering_reading_comprehension"
    )
