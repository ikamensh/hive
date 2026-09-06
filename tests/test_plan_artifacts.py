"""Plan documents are the same durable interface for every author and chief."""

from test_plans import app as app, FakeSpecRepo, _api_project
import pytest


def test_submitted_plan_can_be_retrieved_and_retried_without_duplicate_work(app, monkeypatch):
    """A lost submission response is safe: another client retrieves the exact accepted input."""
    client, _ = app
    monkeypatch.setattr("hive.api.SpecRepo", FakeSpecRepo)
    project = _api_project(client)
    source = "# CSV export\n\n## Export rows\nHandle commas and Unicode.\n"
    body = {"source": source, "start": True}
    first = client.post(f"/api/projects/{project}/plan/submit", json=body)
    assert first.status_code == 200, first.text
    accepted = first.json()
    plan_id = accepted["plan"]["id"]
    repeated = client.post(f"/api/projects/{project}/plan/submit", json=body)
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["plan"]["id"] == plan_id
    assert repeated.json()["plan"]["revision"] == 1
    snapshot = client.get(f"/api/plans/{plan_id}").raise_for_status().json()
    assert len(snapshot["tasks"]) == 1
    assert snapshot["tasks"][0]["plan_revision"] == 1
    assert client.get(f"/api/plans/{plan_id}/input?revision=1").text == source
    exported = client.get(f"/api/plans/{plan_id}/document").raise_for_status()
    from hive._workstreams.plan_document import parse_plan_document
    document = parse_plan_document(exported.text)
    assert document["plan_id"] == plan_id and document["revision"] == 1
    assert document["items"][0]["notes"] == "Handle commas and Unicode."


def test_amendment_preserves_active_input_and_requires_approval_for_changed_work(app, monkeypatch):
    """An edited export updates future work, rejects stale editors, and preserves input history."""
    from hive._workstreams.plan_document import parse_plan_document, render_plan_document
    client, _ = app
    monkeypatch.setattr("hive.api.SpecRepo", FakeSpecRepo)
    project = _api_project(client)
    source = "# CSV export\n\n## Export rows\nBuild A.\n\n## Document usage\nWrite B.\n"
    accepted = client.post(f"/api/projects/{project}/plan/submit", json={"source": source, "start": True}).json()
    plan_id = accepted["plan"]["id"]
    original_task = accepted["tasks"][0]
    draft = parse_plan_document(client.get(f"/api/plans/{plan_id}/document").text)
    draft["items"][1]["notes"] = "  Include a Unicode example.\n"
    amended_source = render_plan_document(draft)
    amended = client.post(f"/api/projects/{project}/plan/submit", json={"source": amended_source})
    assert amended.status_code == 200, amended.text
    assert amended.json()["plan"]["revision"] == 2
    assert amended.json()["items"][1]["status"] == "proposed"
    assert amended.json()["tasks"][0] == original_task
    assert client.get(f"/api/plans/{plan_id}/input?revision=1").text == source
    draft["items"][1]["notes"] = "A conflicting edit from revision 1."
    stale = client.post(f"/api/projects/{project}/plan/submit", json={"source": render_plan_document(draft)})
    assert stale.status_code == 409 and "current revision is 2" in stale.text
    restarted = client.post(f"/api/projects/{project}/plan/submit", json={"source": amended_source, "start": True})
    assert restarted.status_code == 200, restarted.text
    assert restarted.json()["items"][1]["status"] == "queued"
    assert restarted.json()["plan"]["revision"] == 2


@pytest.mark.parametrize(("field", "value"), [
    ("goal", "Deliver a different user outcome"),
    ("context_documents", {"constraints.md": "Preserve the existing storage interface."}),
    ("repo_baselines", {"https://github.com/o/r.git": "b" * 40}),
])
def test_shared_amendment_requires_renewed_item_approval(app, field, value):
    """Changing shared instructions invalidates prior item approval even
    when each item's own text is unchanged; replay cannot revoke new approval."""
    from hive._workstreams.plan_document import parse_plan_document, render_plan_document
    client, _ = app
    project = _api_project(client)
    initial = client.post(f"/api/projects/{project}/plan/submit", json={
        "source": "# Goal\n\n## A\nBuild A.\n\n## B\nBuild B.\n",
    }).raise_for_status().json()
    plan_id = initial["plan"]["id"]
    item_id = initial["items"][0]["id"]
    client.post(f"/api/plan-items/{item_id}/approve").raise_for_status()
    draft = parse_plan_document(client.get(f"/api/plans/{plan_id}/document").raise_for_status().text)
    draft[field] = value
    source = render_plan_document(draft)

    amended = client.post(f"/api/projects/{project}/plan/submit", json={"source": source}).raise_for_status().json()

    assert amended["plan"]["revision"] == 2
    assert [item["status"] for item in amended["items"]] == ["proposed", "proposed"]
    assert amended["tasks"] == []
    client.post(f"/api/plan-items/{item_id}/approve").raise_for_status()
    repeated = client.post(f"/api/projects/{project}/plan/submit", json={"source": source}).raise_for_status().json()
    assert repeated["plan"]["revision"] == 2
    assert [item["status"] for item in repeated["items"]] == ["approved", "proposed"]


def test_shared_amendment_reproposes_queued_work_and_preserves_parked_head(app, monkeypatch):
    """Shared context requires queued work to be approved again while the
    existing parked item's decision and reason remain pending."""
    from hive._workstreams.plan_document import parse_plan_document, render_plan_document
    client, _ = app
    monkeypatch.setattr("hive.api.SpecRepo", FakeSpecRepo)
    project = _api_project(client)
    started = client.post(f"/api/projects/{project}/plan/submit", json={
        "source": "# Goal\n\n## A\nBuild A.\n\n## B\nBuild B.\n", "start": True,
    }).raise_for_status().json()
    plan_id = started["plan"]["id"]
    client.post(f"/api/tasks/{started['tasks'][0]['id']}/cancel").raise_for_status()
    before = client.get(f"/api/plans/{plan_id}").raise_for_status().json()
    draft = parse_plan_document(client.get(f"/api/plans/{plan_id}/document").raise_for_status().text)
    draft["context_documents"] = {"constraints.md": "Preserve the existing storage interface."}

    amended = client.post(f"/api/projects/{project}/plan/submit", json={
        "source": render_plan_document(draft),
    }).raise_for_status().json()

    assert [item["status"] for item in amended["items"]] == ["blocked_clarity", "proposed"]
    assert amended["items"][0]["parked_reason"] == before["items"][0]["parked_reason"]
    assert amended["tasks"] == before["tasks"]


def test_interrupted_shared_amendment_reproposes_remaining_items_after_restart(tmp_path):
    """A crash after re-proposing one item cannot leave later items approved
    under old shared instructions when a fresh chief finishes the submission."""
    from fastapi.testclient import TestClient
    from hive.api import create_app
    from hive._control.supervisor import Supervisor
    from hive._workstreams.plan_document import parse_plan_document, render_plan_document
    from hive.models import PlanItem
    from hive.persistence import FileStore, LocalBlobStore
    from test_plans import make_config

    class InterruptedStore(FileStore):
        armed = False

        def update(self, model, id, mutate):
            if self.armed and model is PlanItem and self.get(model, id).title == "B":
                raise OSError("simulated interruption before second approval was invalidated")
            return super().update(model, id, mutate)

    def connect(store):
        return TestClient(create_app(store, Supervisor(store, lambda *_: None), make_config(tmp_path),
                                     blobs=LocalBlobStore(tmp_path / "blobs")))

    store_path = tmp_path / "store"
    interrupted = InterruptedStore(store_path)
    with connect(interrupted) as client:
        project = _api_project(client)
        initial = client.post(f"/api/projects/{project}/plan/submit", json={
            "source": "# Goal\n\n## A\nBuild A.\n\n## B\nBuild B.\n",
        }).raise_for_status().json()
        plan_id = initial["plan"]["id"]
        for item in initial["items"]:
            client.post(f"/api/plan-items/{item['id']}/approve").raise_for_status()
        draft = parse_plan_document(client.get(f"/api/plans/{plan_id}/document").raise_for_status().text)
        draft["context_documents"] = {"constraints.md": "Preserve the existing storage interface."}
        source = render_plan_document(draft)
        interrupted.armed = True
        with pytest.raises(OSError, match="second approval"):
            client.post(f"/api/projects/{project}/plan/submit", json={"source": source})

    with connect(FileStore(store_path)) as client:
        recovered = client.post(f"/api/projects/{project}/plan/submit", json={"source": source}).raise_for_status().json()
        assert recovered["plan"]["id"] == plan_id and recovered["plan"]["revision"] == 2
        assert [item["status"] for item in recovered["items"]] == ["proposed", "proposed"]
        assert recovered["tasks"] == []
        first_id = recovered["items"][0]["id"]
        client.post(f"/api/plan-items/{first_id}/approve").raise_for_status()
        repeated = client.post(f"/api/projects/{project}/plan/submit", json={"source": source}).raise_for_status().json()
        assert [item["status"] for item in repeated["items"]] == ["approved", "proposed"]
        assert client.get(f"/api/plans/{plan_id}/input?revision=2").raise_for_status().text == source


def test_planner_and_file_edits_share_lossless_documents_and_frozen_repositories(app, tmp_path):
    """A planner's structured draft can be edited as a file without losing its multi-repo context."""
    from test_cli import cli
    from hive._workstreams.plan_document import parse_plan_document, render_plan_document
    client, _ = app
    project = _api_project(client)
    other_repo = "https://github.com/o/docs.git"
    client.patch(f"/api/projects/{project}", json={"member_repos": [other_repo]}).raise_for_status()
    created = client.post(f"/api/projects/{project}/plan", json={
        "goal": "  Export rows\n", "items": [
            {"title": "CSV", "story": " Unicode names survive.\n", "notes": "\nKeep commas.  ",
             "story_keys": ["csv-unicode"]},
            {"title": "Usage", "repo": other_repo, "constraints": "No new dependency."},
        ],
    }).raise_for_status().json()
    plan_id = created["plan"]["id"]
    path = tmp_path / "plan.toml"
    cli(client, "plan-export", plan_id, "--output", str(path))
    document = parse_plan_document(path.read_text())
    assert document["goal"] == "  Export rows\n"
    assert document["items"][0]["notes"] == "\nKeep commas.  "
    assert document["items"][0]["story_keys"] == ["csv-unicode"]
    assert document["items"][0]["repo"] == "https://github.com/o/r.git"
    assert document["items"][1]["repo"] == other_repo
    document["items"][1]["notes"] = "Include a runnable example."
    path.write_text(render_plan_document(document))
    updated = cli(client, "plan-import", project, str(path))
    assert updated["plan"]["revision"] == 2
    client.patch(f"/api/projects/{project}", json={"spec_repo": "https://github.com/o/new.git"}).raise_for_status()
    exported = parse_plan_document(client.get(f"/api/plans/{plan_id}/document").text)
    assert exported["items"][0]["repo"] == "https://github.com/o/r.git"
    historical = parse_plan_document(client.get(f"/api/plans/{plan_id}/document?revision=1").text)
    assert historical["items"][1]["notes"] == ""


def test_invalid_or_removed_items_do_not_partially_rewrite_a_plan(app):
    """A document is validated as a whole; omission is never an implicit cancel."""
    from hive._workstreams.plan_document import parse_plan_document, render_plan_document
    client, _ = app
    project = _api_project(client)
    source = "# Goal\n\n## A\nBuild A.\n\n## B\nBuild B.\n"
    original = client.post(f"/api/projects/{project}/plan/submit", json={"source": source}).json()
    plan_id = original["plan"]["id"]
    draft = parse_plan_document(client.get(f"/api/plans/{plan_id}/document").text)
    draft["items"].pop()
    draft["items"][0]["notes"] = "This change should not be applied."
    rejected = client.post(f"/api/projects/{project}/plan/submit", json={"source": render_plan_document(draft)})
    assert rejected.status_code == 400 and "omitted" in rejected.text
    current = client.get(f"/api/plans/{plan_id}").json()
    assert current["items"] == original["items"] and current["plan"]["revision"] == 1
    invalid = client.post(f"/api/projects/{project}/plan", json={
        "goal": "Invalid replacement", "items": [{"title": "A"}, {"title": "B", "unknown": True}],
    })
    assert invalid.status_code == 400
    assert client.get(f"/api/plans/{plan_id}").json()["plan"]["status"] == "draft"


@pytest.mark.parametrize("failure_point", ["item", "revision"])
def test_interrupted_submission_recovers_after_a_fresh_chief(tmp_path, monkeypatch, failure_point):
    """A storage interruption cannot strand a half-import or duplicate work after restart."""
    from fastapi.testclient import TestClient
    from hive.api import create_app
    from hive._control.supervisor import Supervisor
    from hive.models import PlanItem, PlanRevision
    from hive.persistence import FileStore, LocalBlobStore
    from test_plans import make_config

    class InterruptedStore(FileStore):
        def put(self, obj):
            if ((failure_point == "item" and isinstance(obj, PlanItem) and obj.title == "B")
                    or (failure_point == "revision" and isinstance(obj, PlanRevision))):
                raise OSError("simulated storage interruption")
            return super().put(obj)

    def connect(store):
        return TestClient(create_app(store, Supervisor(store, lambda *_: None), make_config(tmp_path),
                                     blobs=LocalBlobStore(tmp_path / "blobs")))

    monkeypatch.setattr("hive.api.SpecRepo", FakeSpecRepo)
    source = "# Goal\n\n## A\nBuild A.\n\n## B\nBuild B.\n"
    with connect(InterruptedStore(tmp_path / "store")) as client:
        project = _api_project(client)
        with pytest.raises(OSError, match="storage interruption"):
            client.post(f"/api/projects/{project}/plan/submit", json={"source": source})
    with connect(FileStore(tmp_path / "store")) as client:
        recovered = client.post(f"/api/projects/{project}/plan/submit", json={"source": source, "start": True})
        assert recovered.status_code == 200, recovered.text
        payload = recovered.json()
        assert [item["title"] for item in payload["items"]] == ["A", "B"]
        assert payload["plan"]["revision"] == 1 and len(payload["tasks"]) == 1
        assert client.get(f"/api/plans/{payload['plan']['id']}/input").text == source


def test_append_rejects_shared_instructions_it_cannot_apply(app):
    """Append never silently discards new constraints or a mismatched revision."""
    from hive._workstreams.plan_document import render_plan_document
    client, _ = app
    project = _api_project(client)
    original = client.post(f"/api/projects/{project}/plan/submit", json={"source": "# Goal\n## A\nBuild A."}).json()
    extra = render_plan_document({"goal": "Goal", "items": [{"title": "B"}],
                                  "context_documents": {"constraints.md": "Do not change authentication."}})
    refused = client.post(f"/api/projects/{project}/plan/submit", json={"source": extra, "append": True})
    assert refused.status_code == 400 and "context_documents" in refused.text
    latest = client.get(f"/api/plans/{original['plan']['id']}").json()
    assert latest["items"] == original["items"] and latest["plan"]["revision"] == 1


def test_original_file_export_preserves_utf8_and_line_endings(app, tmp_path):
    """The submitted-file receipt retains authored bytes, including CRLF and Unicode."""
    from test_cli import cli
    client, _ = app
    source, exported = tmp_path / "source.md", tmp_path / "original.md"
    content = "# Unicode CSV\r\n\r\n## Export Zoë\r\nPreserve Unicode.\r\n".encode()
    source.write_bytes(content)
    result = cli(client, "plan-import", "bytes", str(source), "--repo", "https://github.com/o/r.git")
    cli(client, "plan-export", result["plan"]["id"], "--submitted", "-o", str(exported))
    assert exported.read_bytes() == content
