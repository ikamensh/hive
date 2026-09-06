import { useState } from "react";
import {
  Link,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import { IconSettings } from "@tabler/icons-react";
import { useOverview } from "../App";
import { api, repoKey, repoShort, usePoll } from "../api";
import { StateBadge } from "../components/shared";
import {
  AnsweredQuestion,
  HumanTodoCard,
  QuestionCard,
  TaskCard,
} from "../features/project/activity";
import { GoalBanner, ProjectActions } from "../features/project/controls";
import { PlanPanel } from "../features/project/plan";
import { PlanSubmission } from "../features/project/planSubmission";
import { IssuesToolbar, IssuesView } from "../features/project/issues";
import {
  DirectiveComposer,
  DirectivesList,
  MachinesPanel,
  groupCheckouts,
} from "../features/project/launchpad";
import { ProjectSetup } from "../features/project/setup";
import {
  StoriesView,
  TestabilityPanel,
  TestingToolbar,
} from "../features/project/testing";
import { projectViewModel } from "../features/project/viewModel";
import { NewWorkButton } from "../features/work/NewWork";
import type { ProjectPatch } from "../types";
import "../features/project/workspace.css";

const tabs = [
  "work",
  "conversation",
  "activity",
  "history",
  "issues",
  "tests",
] as const;
type ProjectTab = (typeof tabs)[number];

function ProjectPause({
  paused,
  onChange,
}: {
  paused: boolean;
  onChange: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  return (
    <div>
      <button
        className="ghost"
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          setError("");
          try {
            await onChange();
          } catch (error) {
            setError(error instanceof Error ? error.message : String(error));
          } finally {
            setBusy(false);
          }
        }}
      >
        {busy ? "Saving…" : paused ? "Resume project" : "Pause project"}
      </button>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

function PlanningStatus({
  projectId,
  reason,
  onChanged,
}: {
  projectId: string;
  reason?: string;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [requested, setRequested] = useState(false);
  return (
    <div className="workspace-notice" role="status">
      <div>
        <strong>Waiting for a plan proposal</strong>
        <p>
          {reason ||
            "Your brief is accepted. The plan will appear here when preparation completes."}
        </p>
        {requested && (
          <p>
            Planning requested. Waiting for the proposal or a question from
            Hive.
          </p>
        )}
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
      </div>
      <button
        className="ghost"
        disabled={busy || requested}
        onClick={async () => {
          setBusy(true);
          setError("");
          try {
            await api.proposePlan(projectId);
            setRequested(true);
            onChanged();
          } catch (error) {
            setError(error instanceof Error ? error.message : String(error));
          } finally {
            setBusy(false);
          }
        }}
      >
        {busy ? "Requesting…" : "Retry plan preparation"}
      </button>
    </div>
  );
}

export default function ProjectPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedPlanId = searchParams.get("plan") ?? "";
  const requestedTab = searchParams.get("tab");
  const tab: ProjectTab = tabs.includes(requestedTab as ProjectTab)
    ? (requestedTab as ProjectTab)
    : "work";
  const newPlan = searchParams.get("new") === "plan";
  const [issueStreamId, setIssueStreamId] = useState("");
  const [issueNumbers, setIssueNumbers] = useState<number[]>([]);
  const [testingStreamId, setTestingStreamId] = useState("");
  const [storyKeys, setStoryKeys] = useState<string[]>([]);
  const [activityLimit, setActivityLimit] = useState(20);
  const [launchMessage, setLaunchMessage] = useState("");
  const projectPoll = usePoll(() => api.project(id), [id]);
  const selectedPlan = usePoll(
    () => api.getPlan(selectedPlanId),
    [id, selectedPlanId],
    4000,
    { enabled: !!selectedPlanId },
  );
  const history = usePoll(() => api.projectPlans(id), [id], 6000, {
    enabled: tab === "history",
  });
  const resources = usePoll(() => api.resources(), [], 8000, {
    enabled: tab === "activity",
  });
  const overview = useOverview();
  const { data, failed, refresh } = projectPoll;

  if (!data)
    return (
      <div className="page">
        {failed ? (
          <p role="alert">Project unavailable. Retrying…</p>
        ) : (
          <p className="muted">Loading project…</p>
        )}
      </div>
    );
  const vm = projectViewModel(data, { issueStreamId, testingStreamId });
  const { project, configured, intakeConversation } = vm;
  const refreshAll = (planId?: string) => {
    if (planId) setSearchParams({ plan: planId, tab: "work" });
    selectedPlan.refresh();
    refresh();
    history.refresh();
    overview.refresh();
  };
  const patch = async (value: ProjectPatch) => {
    await api.patchProject(id, value);
    if (value.archived) navigate("/projects");
    else refreshAll();
  };
  const tabUrl = (value: ProjectTab) => {
    const params = new URLSearchParams(searchParams);
    params.set("tab", value);
    params.delete("new");
    return `/p/${id}?${params}`;
  };
  const setup = (
    <ProjectSetup
      project={project}
      conversation={intakeConversation}
      availableScouts={vm.availableScouts}
      onSave={patch}
      onCreateRepo={async (name) => {
        await api.createProjectRepo(id, { name, private: true });
        refreshAll();
      }}
      onStartIntake={async (value, backend = "") => {
        await api.patchProject(id, value);
        await api.startIntake(id, backend);
        refreshAll();
      }}
      onFinalizeIntake={async (value) => {
        await api.patchProject(id, value);
        if (intakeConversation && intakeConversation.status !== "done")
          await api.conversationMessage(intakeConversation.id, {
            action: "approve",
          });
        else await api.finalizeIntake(id);
        refreshAll();
      }}
      onConversationMessage={async (conversationId, action, message = "") => {
        await api.conversationMessage(conversationId, { action, message });
        refreshAll();
      }}
    />
  );
  const attention = [
    ...vm.openQs.map((question) => ({
      id: question.id,
      text:
        question.text
          .replace(/[#*`]/g, "")
          .split("\n")
          .find((line) => line.trim()) || "Question needs an answer",
    })),
    ...vm.openTodos.map((todo) => ({ id: todo.id, text: todo.title })),
    ...vm.issueNeeds.map((item) => ({
      id: item.id,
      text: `Issue #${item.issue_number}: ${item.title}`,
    })),
  ];
  const activeIntake =
    intakeConversation && intakeConversation.status !== "done";
  const repos = [
    ...new Set([project.spec_repo, ...project.member_repos].filter(Boolean)),
  ];
  const groups = groupCheckouts(
    repos,
    data.checkouts ?? [],
    (machineId) => {
      const card = resources.data?.cards.find(
        (card) => card.machine.id === machineId,
      );
      return {
        name: card?.machine.name ?? machineId,
        online: card?.online ?? false,
      };
    },
    repoKey,
  );

  return (
    <div className="page project-workspace">
      <header className="workspace-header">
        <div className="workspace-heading">
          <Link className="workspace-breadcrumb" to="/projects">
            Projects /
          </Link>
          <h1>{project.name}</h1>
          <p>
            {configured
              ? repoShort(project.spec_repo)
              : "Connect a repository to start work."}
          </p>
        </div>
        <div className="workspace-actions">
          <ProjectPause
            paused={project.paused}
            onChange={() => patch({ paused: !project.paused })}
          />
          <ProjectActions project={project} onPatch={patch} />
          <Link className="ghost" to={`/p/${id}/settings`}>
            <IconSettings size={17} aria-hidden />
            Settings
          </Link>
          <NewWorkButton
            projectId={id}
            projectName={project.name}
            onStarted={() => refreshAll()}
          />
        </div>
      </header>
      <div className="workspace-status">
        {project.paused ? (
          <span className="chip">
            {vm.tasks.some((task) => task.status === "running")
              ? "Paused · finishing current work"
              : "Paused"}
          </span>
        ) : (
          <StateBadge state={project.state} attentionCount={vm.inboxCount} />
        )}
        <p>
          {data.state_reason ||
            (project.paused
              ? "Project paused. Current work may still be finishing."
              : "Open work to inspect the current plan and execution.")}
        </p>
      </div>
      {failed && (
        <p className="form-error" role="alert">
          Could not refresh the project. Current status is unknown; the
          information below is the last recorded snapshot.
        </p>
      )}
      <nav className="workspace-tabs" aria-label="Project sections">
        {tabs.map((value) => (
          <Link
            key={value}
            to={tabUrl(value)}
            aria-current={tab === value ? "page" : undefined}
          >
            {value.charAt(0).toUpperCase() + value.slice(1)}
            {value === "conversation" && vm.openQs.length > 0
              ? ` (${vm.openQs.length})`
              : ""}
          </Link>
        ))}
      </nav>
      <section className="project-content" aria-label="Project workspace">
        {!configured ? (
          setup
        ) : (
          <>
            {tab === "work" && (
              <>
                {attention.length > 0 && (
                  <section className="workspace-attention">
                    <header>
                      <h2>
                        Needs your attention{" "}
                        <span className="muted">{vm.inboxCount}</span>
                      </h2>
                      <Link to={`/inbox?project=${id}`}>Open Inbox</Link>
                    </header>
                    <ul>
                      {attention.slice(0, 3).map((item) => (
                        <li key={item.id}>
                          <Link to={tabUrl("conversation")}>{item.text}</Link>
                        </li>
                      ))}
                    </ul>
                  </section>
                )}
                {activeIntake && (
                  <div className="workspace-notice">
                    <div>
                      <strong>Designing the next step</strong>
                      <p>
                        {intakeConversation.status === "running"
                          ? "Hive is inspecting your project and preparing a brief."
                          : intakeConversation.status === "finalizing"
                            ? "Saving the agreed specification before preparing a plan."
                            : "Review the brief, clarify the goal, and continue designing."}
                      </p>
                    </div>
                    <Link to={tabUrl("conversation")}>Open conversation</Link>
                  </div>
                )}
                {project.state === "idle_goal_complete" && (
                  <GoalBanner project={project} onPatch={patch} />
                )}
                {newPlan ? (
                  <section className="plan-panel" aria-label="Submit prepared work">
                    <PlanSubmission projectId={id} onAccepted={refreshAll} />
                  </section>
                ) : (
                  <>
                    {selectedPlanId &&
                      selectedPlanId !== data.plan?.plan.id && (
                        <p>
                          <Link to={`/p/${id}`}>Back to current plan</Link>
                        </p>
                      )}
                    {selectedPlanId && selectedPlan.failed ? (
                      <p className="form-error" role="alert">
                        Could not load this plan: {String(selectedPlan.error)}
                      </p>
                    ) : selectedPlanId && !selectedPlan.data ? (
                      <p>Loading plan…</p>
                    ) : selectedPlan.data &&
                      selectedPlan.data.plan.project_id !== id ? (
                      <p className="form-error" role="alert">
                        This plan belongs to another project.
                      </p>
                    ) : (
                      <PlanPanel
                        key={`${id}:${selectedPlanId || data.plan?.plan.id || "new"}`}
                        project={project}
                        payload={selectedPlanId ? selectedPlan.data : data.plan}
                        onChanged={refreshAll}
                        selectedItemId={searchParams.get("item") ?? ""}
                        showEvidence={searchParams.get("evidence") === "1"}
                      />
                    )}
                  </>
                )}
                <details className="project-disclosure workspace-section">
                  <summary>Direct requests</summary>
                  <p className="muted">
                    Send a specific task directly through build and review. For
                    an open-ended goal, choose New work → Design with AI.
                  </p>
                  <DirectiveComposer
                    onSubmit={async (text) => {
                      await api.createDirective(id, text);
                      setLaunchMessage(
                        "Request accepted. Its current state appears below.",
                      );
                      refreshAll();
                    }}
                  />
                  {launchMessage && <p role="status">{launchMessage}</p>}
                  <DirectivesList directives={data.directives ?? []} />
                </details>
              </>
            )}
            {tab === "conversation" && (
              <>
                <div className="workspace-notice">
                  <div>
                    <strong>Goals, questions, and decisions</strong>
                    <p>
                      Use this space to shape the work. Code execution begins
                      after you approve a plan.
                    </p>
                  </div>
                  <Link to={`/p/${id}/decisions`}>Decision history</Link>
                </div>
                {(intakeConversation || vm.needsSetup) && setup}
                {intakeConversation?.status === "done" && !data.plan && (
                  <PlanningStatus
                    projectId={id}
                    reason={data.state_reason}
                    onChanged={() => refreshAll()}
                  />
                )}
                {data.plan && (
                  <div className="workspace-notice">
                    <div>
                      <strong>
                        {data.plan.plan.status === "draft"
                          ? "Your plan is ready for review"
                          : "View the current plan"}
                      </strong>
                      <p>{data.plan.plan.goal}</p>
                    </div>
                    <Link to={tabUrl("work")}>Open plan</Link>
                  </div>
                )}
                {vm.openQs.map((question) => (
                  <QuestionCard
                    key={question.id}
                    q={question}
                    onAnswered={refreshAll}
                  />
                ))}
                {vm.openTodos.map((todo) => (
                  <HumanTodoCard
                    key={todo.id}
                    task={todo}
                    onDone={refreshAll}
                  />
                ))}
                {!intakeConversation &&
                  !vm.needsSetup &&
                  vm.openQs.length === 0 && (
                    <p className="muted">
                      No open questions. Start a new design conversation from
                      New work.
                    </p>
                  )}
                {vm.answeredQs.length > 0 && (
                  <details className="project-disclosure workspace-section">
                    <summary>
                      Answered questions ({vm.answeredQs.length})
                    </summary>
                    {vm.answeredQs.map((question) => (
                      <AnsweredQuestion key={question.id} q={question} />
                    ))}
                  </details>
                )}
              </>
            )}
            {tab === "history" && (
              <>
                <h2>Plan history</h2>
                <p className="muted">
                  Each link retains its own execution and evidence across later
                  iterations.
                </p>
                {history.failed && (
                  <p className="form-error" role="alert">
                    Could not load plan history. {String(history.error)}
                  </p>
                )}
                {!history.data && !history.failed ? (
                  <p>Loading history…</p>
                ) : history.data?.plans.length === 0 ? (
                  <p>No plans yet.</p>
                ) : (
                  history.data && (
                    <div className="workspace-table-wrap">
                      <table className="workspace-table">
                        <thead>
                          <tr>
                            <th>Goal</th>
                            <th>Status</th>
                            <th>Created</th>
                          </tr>
                        </thead>
                        <tbody>
                          {history.data.plans.map((plan) => (
                            <tr key={plan.id}>
                              <td>
                                <Link to={`/p/${id}?plan=${plan.id}`}>
                                  {plan.goal}
                                </Link>
                                <small>{plan.id}</small>
                              </td>
                              <td>
                                {plan.status === "approved"
                                  ? "Executing"
                                  : plan.status === "complete"
                                    ? "Execution complete"
                                    : plan.status === "draft"
                                      ? "Draft"
                                      : "Abandoned"}
                              </td>
                              <td>
                                {new Date(
                                  plan.created_at * 1000,
                                ).toLocaleDateString()}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )
                )}
              </>
            )}
            {tab === "activity" && (
              <>
                <h2>
                  Agent activity{" "}
                  <span className="muted">{vm.tasks.length}</span>
                </h2>
                <p className="muted">
                  Execution attempts, reviews, and their recorded outputs.
                </p>
                {vm.sortedTasks.length === 0 && <p>No attempts yet.</p>}
                {vm.sortedTasks.slice(0, activityLimit).map((task) => (
                  <TaskCard
                    key={task.id}
                    task={task}
                    projectId={id}
                    onChanged={refresh}
                  />
                ))}
                {activityLimit < vm.sortedTasks.length && (
                  <button
                    className="ghost"
                    onClick={() => setActivityLimit((limit) => limit + 20)}
                  >
                    Show more activity
                  </button>
                )}
                <details className="project-disclosure workspace-section">
                  <summary>Repository checkouts</summary>
                  {resources.failed ? (
                    <p role="alert">Machine details unavailable.</p>
                  ) : (
                    <MachinesPanel groups={groups} />
                  )}
                </details>
              </>
            )}
            {tab === "issues" && (
              <>
                <IssuesToolbar
                  project={project}
                  issueStreams={vm.issueStreams}
                  selectedStreamId={vm.activeIssueStream?.id ?? ""}
                  onSelectedStream={(streamId) => {
                    setIssueStreamId(streamId);
                    setIssueNumbers([]);
                  }}
                  selectedNumbers={issueNumbers}
                  issueRuns={vm.issueRuns}
                  onChanged={refresh}
                />
                <IssuesView
                  workItems={vm.issueWorkItems}
                  selectedNumbers={issueNumbers}
                  onToggle={(number) =>
                    setIssueNumbers((values) =>
                      values.includes(number)
                        ? values.filter((value) => value !== number)
                        : [...values, number].sort((a, b) => a - b),
                    )
                  }
                />
              </>
            )}
            {tab === "tests" && (
              <>
                <TestingToolbar
                  project={project}
                  testingStreams={vm.testingStreams}
                  selectedStreamId={vm.activeTestingStream?.id ?? ""}
                  onSelectedStream={(streamId) => {
                    setTestingStreamId(streamId);
                    setStoryKeys([]);
                  }}
                  selectedStoryKeys={storyKeys}
                  activityVersion={vm.testingActivityVersion}
                  health={
                    vm.activeTestingStream
                      ? data.testing_health?.[vm.activeTestingStream.id]
                      : undefined
                  }
                  onChanged={refresh}
                />
                <TestabilityPanel
                  project={project}
                  stream={vm.activeTestingStream}
                  view={
                    vm.activeTestingStream
                      ? data.testability?.[vm.activeTestingStream.id]
                      : undefined
                  }
                  decisions={data.questions.filter(
                    (question) =>
                      question.workstream_id === vm.activeTestingStream?.id &&
                      (question.dedup_key ?? "").startsWith("testability:") &&
                      question.status === "open",
                  )}
                  onChanged={refresh}
                />
                <StoriesView
                  stories={vm.testingStories}
                  findings={vm.testingFindings}
                  episodes={vm.testingEpisodes}
                  selectedStoryKeys={storyKeys}
                  onToggle={(key) =>
                    setStoryKeys((keys) =>
                      keys.includes(key)
                        ? keys.filter((value) => value !== key)
                        : [...keys, key],
                    )
                  }
                />
              </>
            )}
          </>
        )}
      </section>
    </div>
  );
}
