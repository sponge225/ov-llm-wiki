from openviking.wiki.config import WikiConfig
from openviking.wiki.uri import node_root_uri, sanitize_node_id, wiki_root


def test_node_uri_has_no_corpus_id_layer():
    config = WikiConfig()

    assert node_root_uri(config, "question_answering") == "viking://wiki/nodes/question_answering/"
    assert "corpus" not in node_root_uri(config, "question_answering")


def test_resource_root_uri_keeps_exact_input():
    config = WikiConfig(resource_root_uri="viking://resources/paper.md")

    assert config.resource_root_uri == "viking://resources/paper.md"


def test_wiki_root_uri_keeps_exact_input():
    config = WikiConfig(wiki_root_uri="viking://wiki/my_wiki")

    assert config.wiki_root_uri == "viking://wiki/my_wiki"


def test_wiki_root_helper_adds_trailing_slash_for_path_building():
    config = WikiConfig(wiki_root_uri="viking://wiki/my_wiki")

    assert wiki_root(config) == "viking://wiki/my_wiki/"


def test_sanitize_node_id_is_stable():
    assert sanitize_node_id("Question Answering & Reading-Comprehension") == (
        "question_answering_reading_comprehension"
    )
