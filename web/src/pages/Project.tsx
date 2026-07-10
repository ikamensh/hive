import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useOverview } from "../App";
import { api, repoKey, repoShort, usePoll } from "../api";
import { Markdown, StateBadge } from "../components/shared";
import {
  AnsweredQuestion,
  HumanTodoCard,
  QuestionCard,
  TaskCard,
} from "../features/project/activity";
import {
  GoalBanner,
  ProjectActions,
  WorkstreamCard,
} from "../features/project/controls";
import { IssueCard, IssuesToolbar, IssuesView } from "../features/project/issues";
import {
  DirectiveComposer,
  DirectivesList,
  JobTiles,
  MachinesPanel,
  groupCheckouts,
  type JobKind,
  type JobTile,
} from "../features/project/launchpad";
import { ProjectSetup } from "../features/project/setup";
import { StoriesView, TestingToolbar } from "../features/project/testing";
import { projectViewModel } from "../features/project/viewModel";
import type { ProjectPatch } from "../types";

export default function ProjectPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const [drill, setDrill] = useState<"none" | "build" | "issues" | "tests">("none");
  const [selectedIssueStreamId, setSelectedIssueStreamId] = useState("");
  const [selectedIssueNumbers, setSelectedIssueNumbers] = useState<number[]>([]);
  const [selectedTestingStreamId, setSelectedTestingStreamId] = useState("");
  const [selectedStoryKeys, setSelectedStoryKeys] = useState<string[]>([]);
  const [launchingBuild, setLaunchingBuild] = useState(false);
  const [launchMessage, setLaunchMessage] = useState("");
  const [launchError, setLaunchError] = useState("");
  const { data, failed, refresh } = usePoll(() => api.project(id), [id]);
  const { data: resources } = usePoll(() => api.resources(), [], 8000);
  const overview = useOverview();

  if (!data) {
    return <div className="page">{failed ? <p className="muted">project unreachable</p> : <p className="muted">loading...</p>}</div>;
  }

  const {
    project,
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
    issueRuns,
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
  } = projectViewModel(data, {
    issueStreamId: selectedIssueStreamId,
    testingStreamId: selectedTestingStreamId,
    resources,
  });

  const patch = async (p: ProjectPatch) => {
    await api.patchProject(id, p);
    if (p.archived) {
      navigate("/");
      return;
    }
    refresh();
  };

  const refreshProjectAndOverview = () => {
    refresh();
    overview.refresh();
  };

  const saveSetup = async (p: ProjectPatch) => {
    await api.patchProject(id, p);
    refresh();
  };

  const createRepo = async (repoName: string) => {
    await api.createProjectRepo(id, { name: repoName, private: true });
    refresh();
  };

  const startIntake = async (p: ProjectPatch, backend = "") => {
    await api.patchProject(id, p);
    await api.startIntake(id, backend);
    refresh();
  };

  const writeMission = async (p: ProjectPatch) => {
    await api.patchProject(id, p);
    await api.writeMission(id);
    refresh();
  };

  const finalizeIntake = async (p: ProjectPatch) => {
    await api.patchProject(id, p);
    await api.finalizeIntake(id);
    refreshProjectAndOverview();
  };

  const sendIntakeMessage = async (
    conversationId: string,
    action: "message" | "proceed",
    message = "",
  ) => {
    await api.conversationMessage(conversationId, { action, message });
    refresh();
  };

  const submitDirective = async (text: string) => {
    await api.createDirective(id, text);
    setLaunchError("");
    setLaunchMessage("task request saved");
    refresh();
  };

  const startBuild = async () => {
    if (launchingBuild) return;
    setLaunchingBuild(true);
    setLaunchError("");
    setLaunchMessage("");
    try {
      await api.startProject(id);
      setLaunchMessage("build planning requested");
      refreshProjectAndOverview();
    } catch (e) {
      setLaunchError((e as Error).message || "could not start build");
    } finally {
      setLaunchingBuild(false);
    }
  };

  const needsStart = false;
  const showInbox = inboxCount > 0;
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

  // --- launchpad-derived data ---------------------------------------------
  const directives = data.directives ?? [];
  const checkouts = data.checkouts ?? [];
  const projectRepos = (() => {
    const seen = new Set<string>();
    const out: string[] = [];
    for (const r of [project.spec_repo, ...project.member_repos]) {
      const repo = (r ?? "").trim();
      if (repo && !seen.has(repoKey(repo))) {
        seen.add(repoKey(repo));
        out.push(repo);
      }
    }
    return out;
  })();
  const machineMeta = (machineId: string) => {
    const card = (resources?.cards ?? []).find((c) => c.machine.id === machineId);
    return { name: card?.machine.name ?? machineId.slice(0, 6), online: card?.online ?? false };
  };
  const checkoutGroups = groupCheckouts(projectRepos, checkouts, machineMeta, repoKey);
  const driftCount = checkouts.filter((c) => c.exists && (c.ahead > 0 || c.dirty)).length;

  const jobTiles: JobTile[] = [
    {
      kind: "issues",
      icon: "ti-bug",
      label: "Fix issues",
      hint: issueWorkItems.length > 0 ? `${issueWorkItems.length} issues` : "scan a repo",
    },
    {
      kind: "tests",
      icon: "ti-flask",
      label: "Run tests",
      hint: testingStories.length > 0 ? `${testingStories.length} stories` : "set up testing",
    },
    {
      kind: "build",
      icon: "ti-hammer",
      label: "Advance build",
      hint: launchingBuild ? "starting" : project.paused ? "paused" : `${manualWorkItems.length} work items`,
      disabled: project.paused || launchingBuild,
      busy: launchingBuild,
    },
    {
      kind: "sync",
      icon: "ti-git-merge",
      label: "Sync a machine",
      hint: driftCount > 0 ? `${driftCount} drifted` : "all in sync",
      disabled: driftCount === 0,
    },
  ];

  const onLaunch = (kind: JobKind) => {
    if (kind === "sync") {
      document.getElementById("machines-panel")?.scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    }
    if (kind === "build") {
      startBuild();
      return;
    }
    setDrill(kind);
  };

  const launchpadHome = (
    <section className="col col-launchpad">
      <div className="launch-console">
        <div className="launch-console-head">
          <h2>Start work</h2>
          {data.allowance?.limited && (
            <span
              className="muted allowance-line"
              title={`agent allowance (sessions/day) — ${data.allowance.sessions_today} used today`}
            >
              <i className="ti ti-ticket" aria-hidden /> {data.allowance.summary}
            </span>
          )}
          <span className="muted">{project.paused ? "paused" : "ready"}</span>
        </div>
        <div className="launch-row">
          <DirectiveComposer onSubmit={submitDirective} />
          <JobTiles tiles={jobTiles} onLaunch={onLaunch} />
        </div>
        {(launchMessage || launchError) && (
          <p className={launchError ? "form-error launch-status" : "launch-status muted"}>
            {launchError || launchMessage}
          </p>
        )}
      </div>
      {directives.length > 0 && (
        <div className="launch-block">
          <h2 className="col-title">
            directives <span className="col-count">{directives.length}</span>
          </h2>
          <DirectivesList directives={directives} />
        </div>
      )}
      <div className="launch-block" id="machines-panel">
        <h2 className="col-title">
          machines &amp; checkouts <span className="col-count">{checkoutGroups.length}</span>
        </h2>
        <MachinesPanel groups={checkoutGroups} />
      </div>
    </section>
  );

  const drillBackBar = (
    <button type="button" className="ghost drill-back" onClick={() => setDrill("none")}>
      <i className="ti ti-arrow-left" aria-hidden /> start work
    </button>
  );

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
        <HumanTodoCard key={t.id} task={t} onDone={refreshProjectAndOverview} />
      ))}
      {openQs.map((q) => (
        <QuestionCard key={q.id} q={q} onAnswered={refreshProjectAndOverview} />
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
        <div className="project-head-links">
          <Link className="project-settings-link ghost" to={`/p/${id}/decisions`}>
            <i className="ti ti-git-branch" aria-hidden /> Decisions
            {data.decision_ledger && (
              <span className="head-link-count">
                {data.decision_ledger.counts.operator_specified}/{data.decision_ledger.counts.hive_assumed}
              </span>
            )}
          </Link>
          <Link className="project-settings-link ghost" to={`/p/${id}/settings`}>
            <i className="ti ti-settings" aria-hidden /> Settings
          </Link>
        </div>
        <ProjectActions project={project} onPatch={patch} />
      </div>

      {needsSetup || needsStart ? (
        <ProjectSetup
          project={project}
          conversation={intakeConversation}
          availableScoutBackends={availableScoutBackends}
          onSave={saveSetup}
          onCreateRepo={createRepo}
          onStartIntake={startIntake}
          onWriteMission={writeMission}
          onFinalizeIntake={finalizeIntake}
          onConversationMessage={sendIntakeMessage}
        />
      ) : (
        <>
          {project.state === "idle_goal_complete" && <GoalBanner project={project} onPatch={patch} />}

          <div
            className={`columns ${
              drill === "issues" || drill === "tests"
                ? "columns-issues"
                : drill === "none"
                  ? "columns-launchpad"
                  : ""
            }${showInbox ? "" : " no-inbox"}`}
          >
            {drill === "issues" ? (
              <section className="col col-ws col-issues-main">
                {drillBackBar}
                <IssuesToolbar
                  project={project}
                  issueStreams={issueStreams}
                  selectedStreamId={activeIssueStream?.id ?? ""}
                  onSelectedStream={(streamId) => {
                    setSelectedIssueStreamId(streamId);
                    setSelectedIssueNumbers([]);
                  }}
                  selectedNumbers={selectedIssueNumbers}
                  issueRuns={issueRuns}
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
            ) : drill === "tests" ? (
              <section className="col col-ws col-issues-main">
                {drillBackBar}
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
                  health={activeTestingStream ? data.testing_health?.[activeTestingStream.id] : undefined}
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
            ) : drill === "build" ? (
              <section className="col col-ws">
                {drillBackBar}
                <h2 className="col-title">
                  work items <span className="col-count">{manualWorkItems.length}</span>
                </h2>
                {manualWorkItems.length === 0 && <p className="muted">none yet - the supervisor will plan some</p>}
                {manualWorkItems.map((w) => (
                  <WorkstreamCard key={w.id} ws={w} />
                ))}
              </section>
            ) : (
              launchpadHome
            )}
            {showInbox && needsYouCol}
            {activityCol}
          </div>
        </>
      )}
    </div>
  );
}
