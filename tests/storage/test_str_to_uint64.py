# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from openviking.storage.vectordb.utils.str_to_uint64 import str_to_uint64


def test_str_to_uint64_accepts_text_primary_keys():
    value = str_to_uint64("default:viking://resources/doc.md")

    assert isinstance(value, int)
    assert 0 <= value <= 2**64 - 1
    assert value == str_to_uint64("default:viking://resources/doc.md")


def test_str_to_uint64_accepts_non_ascii_text():
    value = str_to_uint64("default:viking://resources/中文.md")

    assert isinstance(value, int)
    assert 0 <= value <= 2**64 - 1
