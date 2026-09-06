"""Every recorded landing remains reachable through a workspace-scoped listing."""

from test_plans import app as app

from hive.models import Plan, PlanItem, PlanItemStatus, PlanStatus, Project


def test_deliveries_paginates_beyond_the_overview_and_keeps_workspace_isolation(app):
    """Paging exposes all receipts exactly once without foreign or unlanded work."""
    client, store = app
    project = store.put(Project(name="Exports", spec_repo="https://github.com/acme/csv"))
    plan = store.put(Plan(project_id=project.id, goal="Export CSV", status=PlanStatus.complete))
    for index in range(27):
        store.put(PlanItem(project_id=project.id, plan_id=plan.id, title=f"Delivery {index}",
                           status=PlanItemStatus.done, merged_commit_sha="a" * 40, merged_at=index + 1))
    store.put(PlanItem(project_id=project.id, plan_id=plan.id, title="No receipt", status=PlanItemStatus.done))
    foreign = store.put(Project(workspace_id="private", name="Private"))
    store.put(PlanItem(workspace_id="private", project_id=foreign.id, plan_id="private-plan",
                       title="Secret", status=PlanItemStatus.done, merged_commit_sha="b" * 40, merged_at=100))

    first = client.get("/api/deliveries?offset=0&limit=20").raise_for_status().json()
    second = client.get("/api/deliveries?offset=20&limit=20").raise_for_status().json()
    overview = client.get("/api/overview").raise_for_status().json()

    assert first["count"] == second["count"] == overview["recent_deliveries"]["count"] == 27
    assert first["observed_at"] > 0 and first["offset"] == 0 and second["offset"] == 20
    assert first["limit"] == 20 and len(first["items"]) == 20 and len(second["items"]) == 7
    all_rows = first["items"] + second["items"]
    assert len({row["work_item_id"] for row in all_rows}) == 27
    assert [row["merged_at"] for row in all_rows] == list(range(27, 0, -1))
    assert all(row["plan_id"] == plan.id and row["project_name"] == project.name for row in all_rows)
    assert overview["recent_deliveries"]["items"] == first["items"][:len(overview["recent_deliveries"]["items"])]
    assert client.get("/api/deliveries?offset=27").json()["items"] == []
    for query in ("offset=-1", "limit=0", "limit=101"):
        assert client.get(f"/api/deliveries?{query}").status_code == 422
