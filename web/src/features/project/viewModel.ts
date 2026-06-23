import type { ProjectDetail, ResourcesPayload } from "../../types";

const TEST_TASK_KINDS = new Set(["test_refresh", "test_sweep", "test_reproduce", "test_judge"]);
const MANUAL_WORK_ORDER: Record<string, number> = { active: 0, parked: 1, done: 2 };

export function projectViewModel(
  data: ProjectDetail,
  selections: {
    issueStreamId: string;
    testingStreamId: string;
    resources?: ResourcesPayload | null;
  },
) {
  const { project, workstreams, work_items, tasks, questions, conversations, stories, findings, test_episodes } = data;
  const humanTodos = data.human_todos ?? data.human_tasks ?? [];
  const intakeConversation =
    conversations.find((c) => c.id === project.intake_conversation_id) ??
    [...conversations].sort((a, b) => b.created_at - a.created_at)[0] ??
    null;
  const openQs = questions.filter((q) => q.status === "open").sort((a, b) => b.created_at - a.created_at);
  const answeredQs = questions.filter((q) => q.status === "answered").sort((a, b) => b.answered_at - a.answered_at);
  const openTodos = humanTodos.filter((t) => t.status === "open").sort((a, b) => b.created_at - a.created_at);
  const sortedTasks = [...tasks].sort((a, b) => b.created_at - a.created_at);

  const configured = Boolean(project.spec_repo.trim());
  const issueStreams = workstreams.filter((w) => w.kind === "github_issues");
  const activeIssueStream = issueStreams.find((w) => w.id === selections.issueStreamId) ?? issueStreams[0];
  const testingStreams = workstreams.filter((w) => w.kind === "testing");
  const activeTestingStream = testingStreams.find((w) => w.id === selections.testingStreamId) ?? testingStreams[0];
  const manualWorkItems = work_items
    .filter((w) => (w.source ?? "manual") !== "issue")
    .sort((a, b) => (MANUAL_WORK_ORDER[a.status] ?? 9) - (MANUAL_WORK_ORDER[b.status] ?? 9));
  const issueWorkItems = work_items.filter((w) =>
    w.source === "issue" && (!activeIssueStream || !w.workstream_id || w.workstream_id === activeIssueStream.id)
  );
  const testingStories = stories.filter((story) =>
    !activeTestingStream || story.workstream_id === activeTestingStream.id
  );
  const testingFindings = findings.filter((finding) =>
    !activeTestingStream || finding.workstream_id === activeTestingStream.id
  );
  const testingEpisodes = test_episodes.filter((episode) =>
    !activeTestingStream || episode.workstream_id === activeTestingStream.id
  );
  const testingActivityVersion = [
    testingStories.map((story) => `${story.id}:${story.status}:${story.last_fidelity}:${story.last_tested_at}`).join("|"),
    testingEpisodes.map((episode) => `${episode.id}:${episode.status}:${episode.finished_at}`).join("|"),
    tasks.filter((task) => TEST_TASK_KINDS.has(task.kind))
      .map((task) => `${task.id}:${task.status}:${task.finished_at}:${task.cancel_requested}`)
      .join("|"),
  ].join("::");
  const issueNeeds = issueWorkItems.filter((w) => w.status === "blocked_clarity" || w.status === "rejected");
  const testingNeeds = [] as typeof testingStories;
  const inboxCount = openQs.length + openTodos.length + issueNeeds.length;
  const nonIntakeTasks = tasks.filter((t) => !["intake", "probe", "preflight", "resolve", "review"].includes(t.kind));
  const intakeDone = intakeConversation?.status === "done";
  const hasProjectWork = manualWorkItems.length > 0 || issueWorkItems.length > 0 || testingStories.length > 0 || nonIntakeTasks.length > 0;
  const needsSetup = !configured || (!hasProjectWork && !intakeDone);
  // Backends Hive trusts to run intake, in auto-pick preference order (mirror of
  // the chief's TRUSTED_SCOUTS). Setup only needs a yes/no readiness
  // signal per backend; per-machine agent state lives on the machines page.
  const SCOUT_PREFERENCE = ["codex", "claude"];
  const availableScoutBackends = SCOUT_PREFERENCE.filter((backend) =>
    (selections.resources?.resources ?? []).some(
      (resource) => resource.backend === backend && resource.available,
    ),
  );

  return {
    project,
    workstreams,
    tasks,
    intakeConversation,
    openQs,
    answeredQs,
    openTodos,
    sortedTasks,
    configured,
    issueStreams,
    activeIssueStream,
    issueWorkItems,
    issueNeeds,
    testingStreams,
    activeTestingStream,
    testingStories,
    testingFindings,
    testingEpisodes,
    testingActivityVersion,
    testingNeeds,
    manualWorkItems,
    inboxCount,
    needsSetup,
    availableScoutBackends,
  };
}
