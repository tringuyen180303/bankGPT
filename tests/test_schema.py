from bankgpt.artifact.store import capability_tool, list_capabilities, load_capability


def test_seed_capability_loads() -> None:
    cap = load_capability("lookup-member-savings")
    assert cap.metadata.id == "lookup-member-savings"
    assert cap.spec.parameters[0].name == "memberId"
    assert any(s.id == "submit_search" for s in cap.spec.steps)
    assert any(o.code == "MEMBER_NOT_FOUND" for o in cap.spec.outcomes)


def test_catalog_lists_lookup() -> None:
    ids = {c.metadata.id for c in list_capabilities()}
    assert "lookup-member-savings" in ids
    tool = capability_tool(load_capability("lookup-member-savings"))
    assert tool["function"]["name"] == "lookup-member-savings"
    assert "memberId" in tool["function"]["parameters"]["required"]
