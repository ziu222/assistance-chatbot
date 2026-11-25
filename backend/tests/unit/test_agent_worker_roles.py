import pytest

from app.entrypoints.agent_worker import ALL_ROLES, parse_roles, routes_for_roles


def test_parse_roles_defaults_and_deduplicates():
    assert parse_roles(None) == ALL_ROLES
    assert parse_roles("  ") == ALL_ROLES
    assert parse_roles("judge, agent, judge") == ("judge", "agent")


def test_parse_roles_rejects_unknown_or_empty():
    with pytest.raises(ValueError, match="unknown worker role"):
        parse_roles("agent,unknown")
    with pytest.raises(ValueError, match="at least one"):
        parse_roles(", ,")


def test_routes_for_roles_only_includes_enabled_roles():
    judge_only = routes_for_roles(("judge",))
    assert judge_only == {"judge": {"judge"}}

    authoring_only = routes_for_roles(("authoring",))
    assert authoring_only == {"authoring": {"authoring"}}

    agent_only = routes_for_roles(("agent",))
    assert "judge" not in agent_only and "authoring" not in agent_only
    assert "learning" not in agent_only
    assert agent_only, "agent role must include template routes"

    learning_only = routes_for_roles(("learning",))
    assert learning_only == {"learning": {"learning"}}
