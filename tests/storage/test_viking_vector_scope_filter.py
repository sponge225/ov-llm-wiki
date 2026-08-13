# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

import pytest

from openviking.core.namespace import uri_parts
from openviking.server.identity import RequestContext, Role
from openviking.storage.expr import And, Eq, In, Or, PathScope
from openviking.storage.viking_vector_index_backend import VikingVectorIndexBackend
from openviking_cli.session.user_id import UserIdentifier


def _ctx(*, role: Role = Role.USER, actor_peer_id: str | None = None) -> RequestContext:
    return RequestContext(
        user=UserIdentifier("acct", "alice"),
        role=role,
        actor_peer_id=actor_peer_id,
    )


def _build(
    ctx: RequestContext,
    targets: list[str] | None,
    *,
    context_type: str | None = "resource",
    extra_filter=None,
    level: list[int] | None = None,
):
    backend = object.__new__(VikingVectorIndexBackend)
    return backend._build_scope_filter(
        ctx=ctx,
        context_type=context_type,
        target_directories=targets,
        extra_filter=extra_filter,
        level=level,
    )


def _tenant_filter(ctx: RequestContext):
    return VikingVectorIndexBackend._tenant_filter(ctx, context_type="resource")


def _path_scopes(uri: str, *, depth: int = -1) -> list[PathScope]:
    canonical = "viking://" + uri.removeprefix("viking://").strip("/")
    index_path = "/" + canonical.removeprefix("viking://").strip("/")
    if canonical == index_path:
        return [PathScope("uri", canonical, depth=depth)]
    return [
        PathScope("uri", canonical, depth=depth),
        PathScope("uri", index_path, depth=depth),
    ]


def test_descendant_target_elides_only_visible_root_path_filter():
    ctx = _ctx()
    target = "viking://resources/wiki/physics"

    result = _build(
        ctx,
        [target],
        extra_filter=Eq("status", "ready"),
        level=[2],
    )

    assert result == And(
        [
            Eq("context_type", "resource"),
            Eq("account_id", "acct"),
            Or(_path_scopes(target)),
            Eq("status", "ready"),
            In("level", [2]),
        ]
    )


def test_equal_visible_root_elides_only_visible_root_path_filter():
    ctx = _ctx()

    result = _build(ctx, ["viking://resources"])

    assert result == And(
        [
            Eq("context_type", "resource"),
            Eq("account_id", "acct"),
            Or(_path_scopes("viking://resources")),
        ]
    )


def test_all_targets_may_be_under_different_visible_roots():
    ctx = _ctx()
    targets = [
        "viking://resources/wiki/physics",
        "viking://user/resources/private-notes",
        "viking://agent/skills/research",
    ]

    result = _build(ctx, targets)

    assert result == And(
        [
            Eq("context_type", "resource"),
            Eq("account_id", "acct"),
            Or(
                _path_scopes("viking://resources/wiki/physics")
                + _path_scopes("viking://user/alice/resources/private-notes")
                + _path_scopes("viking://agent/skills/research")
            ),
        ]
    )


def test_mixed_visible_and_outside_targets_keep_original_tenant_filter():
    ctx = _ctx()
    targets = ["viking://resources/wiki", "viking://upload/staged"]

    result = _build(ctx, targets)

    assert result == And(
        [
            Eq("context_type", "resource"),
            _tenant_filter(ctx),
            Or([scope for target in targets for scope in _path_scopes(target)]),
        ]
    )


@pytest.mark.asyncio
async def test_cross_user_targets_cannot_bypass_visible_roots_in_tenant_search():
    ctx = _ctx()
    own_uri = "viking://user/alice/resources/notes"
    cross_user_uri = "viking://user/bob/resources/notes"
    records = [
        {
            "id": "own",
            "uri": own_uri,
            "account_id": "acct",
            "context_type": "resource",
        },
        {
            "id": "cross-user",
            "uri": cross_user_uri,
            "account_id": "acct",
            "context_type": "resource",
        },
    ]
    observed_filters = []

    def matches(expr, record):
        if isinstance(expr, And):
            return all(matches(cond, record) for cond in expr.conds)
        if isinstance(expr, Or):
            return any(matches(cond, record) for cond in expr.conds)
        if isinstance(expr, Eq):
            return record.get(expr.field) == expr.value
        if isinstance(expr, PathScope):
            root = uri_parts(expr.path)
            path = uri_parts(str(record.get(expr.field, "")))
            if path[: len(root)] != root:
                return False
            return expr.depth == -1 or len(path) - len(root) <= expr.depth
        raise AssertionError(f"Unexpected filter expression in test: {expr!r}")

    async def fake_search(*, filter, **_kwargs):
        observed_filters.append(filter)
        return [record for record in records if matches(filter, record)]

    backend = object.__new__(VikingVectorIndexBackend)
    backend.search = fake_search

    cross_user_only = await backend.search_in_tenant(
        ctx=ctx,
        query_vector=[1.0],
        context_type="resource",
        target_directories=[cross_user_uri],
    )
    mixed = await backend.search_in_tenant(
        ctx=ctx,
        query_vector=[1.0],
        context_type="resource",
        target_directories=[own_uri, cross_user_uri],
    )

    assert cross_user_only == []
    assert [record["id"] for record in mixed] == ["own"]
    assert observed_filters == [
        And(
            [
                Eq("context_type", "resource"),
                _tenant_filter(ctx),
                Or(_path_scopes(cross_user_uri)),
            ]
        ),
        And(
            [
                Eq("context_type", "resource"),
                _tenant_filter(ctx),
                Or(
                    _path_scopes(own_uri)
                    + _path_scopes(cross_user_uri)
                ),
            ]
        ),
    ]


def test_segment_prefix_and_visible_root_ancestor_do_not_elide_tenant_filter():
    ctx = _ctx()

    segment_prefix = _build(ctx, ["viking://resources-other/wiki"])
    ancestor = _build(ctx, ["viking://agent"])

    assert segment_prefix == And(
        [
            Eq("context_type", "resource"),
            _tenant_filter(ctx),
            Or(_path_scopes("viking://resources-other/wiki")),
        ]
    )
    assert ancestor == And(
        [
            Eq("context_type", "resource"),
            _tenant_filter(ctx),
            Or(_path_scopes("viking://agent")),
        ]
    )


def test_no_target_keeps_original_tenant_filter():
    ctx = _ctx()

    assert _build(ctx, None) == And(
        [
            Eq("context_type", "resource"),
            _tenant_filter(ctx),
        ]
    )


def test_root_role_keeps_existing_target_only_behavior():
    ctx = _ctx(role=Role.ROOT)
    target = "viking://resources/wiki"

    assert _build(ctx, [target]) == And(
        [
            Eq("context_type", "resource"),
            Or(_path_scopes(target)),
        ]
    )


def test_actor_peer_target_retains_account_and_exact_target_scope():
    ctx = _ctx(actor_peer_id="visitor-a")
    target = "viking://user/alice/peers/visitor-a/resources/cases"

    result = _build(ctx, [target])

    assert result == And(
        [
            Eq("context_type", "resource"),
            Eq("account_id", "acct"),
            Or(_path_scopes(target)),
        ]
    )


def test_wiki_target_matches_canonical_and_index_path_records():
    ctx = _ctx()

    result = _build(ctx, ["viking://wiki/nodes"])

    assert result == And(
        [
            Eq("context_type", "resource"),
            Eq("account_id", "acct"),
            Or(_path_scopes("viking://wiki/nodes")),
        ]
    )
