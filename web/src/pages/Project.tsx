import { useState } from "react";
import { useParams } from "react-router-dom";
import { api, repoShort, usePoll } from "../api";
import { Markdown, SegPicker, StateBadge } from "../components/shared";
import {
  AnsweredQuestion,
  HumanTodoCard,
  QuestionCard,
  TaskCard,
} from "../features/project/activity";
import {
  GoalBanner,
  ProjectSettings,
  TogglesBar,
  WorkstreamCard,
} from "../features/project/controls";
import { IssueCard, IssuesToolbar, IssuesView } from "../features/project/issues";
import { ProjectSetup } from "../features/project/setup";
import { StoriesView, TestingToolbar } from "../features/project/testing";
import type { ProjectPatch } from "../types";

const TEST_TASK_KINDS = new Set(["test_refresh", "test_sweep", "test_reproduce", "test_judge"]);

export default function ProjectPage() {
  const { id = "" } = useParams();
  const [primaryView, setPrimaryView] = useState<"work" | "issues" | "tests">("work");
  const [selectedIssueStreamId, setSelectedIssueStreamId] = useState("");
  const [selectedIssueNumbers, setSelectedIssueNumbers] = useState<number[]>([]);
  const [selectedTestingStreamId, setSelectedTestingStreamId] = useState("");
  const [selectedStoryKeys, setSelectedStoryKeys] = useState<string[]>([]);
  const { data, failed, refresh } = usePoll(() => api.project(id), [id]);
  const { data: resources } = usePoll(() => api.resources(), [], 8000);

  if (!data) {
    return <div className="page">{failed ? <p className="muted">project unreachable</p> : <p className="muted">loading...</p>}</div>;
  }

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
  const wsOrder: Record<string, number> = { active: 0, parked: 1, done: 2 };

  const patch = async (p: ProjectPatch) => {
    await api.patchProject(id, p);
    refresh();
  };

  const patchWorkstream = async (workstreamId: string, p: { enabled?: boolean }) => {
    await api.updateWorkstream(id, workstreamId, p);
    refresh();
  };

  const saveSetup = async (p: ProjectPatch) => {
    await api.patchProject(id, p);
    refresh();
  };

  const createRepo = async (repoName: string) => {
    await api.createProjectRepo(id, { name: repoName, private: true });
    refresh();
  };

  const startIntake = async (p: ProjectPatch) => {
    await api.patchProject(id, p);
    await api.startIntake(id);
    refresh();
  };

  const sendIntakeMessage = async (
    conversationId: string,
    action: "message" | "proceed" | "approve",
    message = "",
  ) => {
    await api.conversationMessage(conversationId, { action, message });
    refresh();
  };

  const configured = Boolean(project.spec_repo.trim());
  const issueStreams = workstreams.filter((w) => w.kind === "github_issues");
  const activeIssueStream = issueStreams.find((w) => w.id === selectedIssueStreamId) ?? issueStreams[0];
  const testingStreams = workstreams.filter((w) => w.kind === "testing");
  const activeTestingStream = testingStreams.find((w) => w.id === selectedTestingStreamId) ?? testingStreams[0];
  const manualWorkItems = work_items.filter((w) => (w.source ?? "manual") !== "issue");
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
  const testingNeeds = testingStories.filter((story) => story.status === "blocked");
  const inboxCount = openQs.length + openTodos.length + issueNeeds.length + testingNeeds.length;
  const nonIntakeTasks = tasks.filter((t) => !["intake", "probe", "preflight", "resolve", "review"].includes(t.kind));
  const intakeDone = intakeConversation?.status === "done";
  const hasProjectWork = manualWorkItems.length > 0 || issueWorkItems.length > 0 || testingStories.length > 0 || nonIntakeTasks.length > 0;
  const needsSetup = !configured || (!hasProjectWork && !intakeDone);
  const needsStart = false;
  const trustedScouts = (resources?.resources ?? []).filter((resource) =>
    resource.backend === "codex" || resource.backend === "claude",
  );

  const toggleIssueSelection = (issueNumber: number) => {
    setSelectedIssueNumbers((numbers) =>
      numbers.includes(issueNumber)
        ? numbers.filter((n) => n !== issueNumber)
        : [...numbers, issueNumber].sort((a, b) => a - b),
    );
  };

  const toggleStorySelection = (storyKey: string) => {
    setSelectedStoryKeys((keys) =>
      keys.includes(storyKey)
        ? keys.filter((key) => key !== storyKey)
        : [...keys, storyKey].sort(),
    );
  };

  const needsYouCol = (
    <section className="col col-inbox">
      <h2 className="col-title">
        needs you <span className="col-count">{inboxCount}</span>
      </h2>
      {inboxCount === 0 && <p className="muted">nothing needs you - the hive is unblocked</p>}
      {issueNeeds.map((w) => (
        <IssueCard key={w.id} ws={w} />
      ))}
      {testingNeeds.map((story) => (
        <article className="todo-card project-todo reveal" key={story.id}>
          <header>
            <h3>{story.title || story.key}</h3>
            <span className={`chip chip-story-${story.status}`}>{story.status}</span>
          </header>
          <p className="parked-reason">Testing is blocked for this story.</p>
          <Markdown text={story.intent || story.acceptance} />
        </article>
      ))}
      {openTodos.map((t) => (
        <HumanTodoCard key={t.id} task={t} onDone={refresh} />
      ))}
      {openQs.map((q) => (
        <QuestionCard key={q.id} q={q} onAnswered={refresh} />
      ))}
      {answeredQs.length > 0 && (
        <div className="answered-section">
          <h3>answered</h3>
          {answeredQs.map((q) => (
            <AnsweredQuestion key={q.id} q={q} />
          ))}
        </div>
      )}
    </section>
  );

  const activityCol = (
    <section className="col col-feed">
      <h2 className="col-title">
        activity <span className="col-count">{tasks.length}</span>
      </h2>
      {sortedTasks.length === 0 && <p className="muted">no tasks yet</p>}
      {sortedTasks.map((t) => (
        <TaskCard key={t.id} task={t} projectId={id} onChanged={refresh} />
      ))}
    </section>
  );

  return (
    <div className="page page-project">
      <div className="page-head">
        <h1>
          {project.name}
          {configured && <span className="head-repo">{repoShort(project.spec_repo)}</span>}
        </h1>
        <StateBadge state={project.state} attentionCount={inboxCount} />
      </div>

      {needsSetup || needsStart ? (
        <ProjectSetup
          project={project}
          conversation={intakeConversation}
          trustedScouts={trustedScouts}
          onSave={saveSetup}
          onCreateRepo={createRepo}
          onStartIntake={startIntake}
          onConversationMessage={sendIntakeMessage}
        />
      ) : (
        <>
          {project.goal_complete && <GoalBanner project={project} onPatch={patch} />}
          {configured && !needsStart && <TogglesBar project={project} onPatch={patch} />}
          {configured && !needsStart && (
            <ProjectSettings
              project={project}
              workstreams={workstreams}
              onPatch={patch}
              onPatchWorkstream={patchWorkstream}
            />
          )}

          {configured && !needsStart && (
            <div className="project-primary-switch">
              <SegPicker
                value={primaryView}
                options={[
                  { value: "work", label: "work" },
                  { value: "issues", label: "issues" },
                  { value: "tests", label: "tests" },
                ]}
                onChange={setPrimaryView}
              />
            </div>
          )}

          <div className={`columns ${primaryView === "issues" || primaryView === "tests" ? "columns-issues" : ""}`}>
            {primaryView === "issues" ? (
              <section className="col col-ws col-issues-main">
                <IssuesToolbar
                  project={project}
                  issueStreams={issueStreams}
                  selectedStreamId={activeIssueStream?.id ?? ""}
                  onSelectedStream={(streamId) => {
                    setSelectedIssueStreamId(streamId);
                    setSelectedIssueNumbers([]);
                  }}
                  selectedNumbers={selectedIssueNumbers}
                  onChanged={refresh}
                />
                <h2 className="col-title issues-title">
                  issues <span className="col-count">{issueWorkItems.length}</span>
                </h2>
                <IssuesView
                  workItems={issueWorkItems}
                  selectedNumbers={selectedIssueNumbers}
                  onToggle={toggleIssueSelection}
                />
              </section>
            ) : primaryView === "tests" ? (
              <section className="col col-ws col-issues-main">
                <TestingToolbar
                  project={project}
                  testingStreams={testingStreams}
                  selectedStreamId={activeTestingStream?.id ?? ""}
                  onSelectedStream={(streamId) => {
                    setSelectedTestingStreamId(streamId);
                    setSelectedStoryKeys([]);
                  }}
                  selectedStoryKeys={selectedStoryKeys}
                  activityVersion={testingActivityVersion}
                  onChanged={refresh}
                />
                <h2 className="col-title issues-title">
                  stories <span className="col-count">{testingStories.length}</span>
                </h2>
                <StoriesView
                  stories={testingStories}
                  findings={testingFindings}
                  episodes={testingEpisodes}
                  selectedStoryKeys={selectedStoryKeys}
                  onToggle={toggleStorySelection}
                />
              </section>
            ) : (
              <section className="col col-ws">
                <h2 className="col-title">
                  work items <span className="col-count">{manualWorkItems.length}</span>
                </h2>
                {manualWorkItems.length === 0 && <p className="muted">none yet - the supervisor will plan some</p>}
                {[...manualWorkItems]
                  .sort((a, b) => (wsOrder[a.status] ?? 9) - (wsOrder[b.status] ?? 9))
                  .map((w) => (
                    <WorkstreamCard key={w.id} ws={w} />
                  ))}
              </section>
            )}
            {needsYouCol}
            {activityCol}
          </div>
        </>
      )}
    </div>
  );
}
