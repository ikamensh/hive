import { Fragment, useEffect, useState } from "react";
import { ago, api, repoShort } from "../../api";
import { Markdown } from "../../components/shared";
import type {
  Finding,
  Project,
  Question,
  Story,
  TestEpisode,
  TestabilityView,
  TestingHealth,
  ProjectWorkstream,
} from "../../types";

const STORY_GROUPS: { label: string; statuses: Story["status"][] }[] = [
  { label: "priority", statuses: ["untested", "stale", "failing", "blocked"] },
  { label: "green", statuses: ["passing"] },
  { label: "all", statuses: ["untested", "stale", "failing", "blocked", "passing"] },
];

export function TestingToolbar({
  project,
  testingStreams,
  selectedStreamId,
  onSelectedStream,
  selectedStoryKeys,
  activityVersion,
  health,
  onChanged,
}: {
  project: Project;
  testingStreams: ProjectWorkstream[];
  selectedStreamId: string;
  onSelectedStream: (id: string) => void;
  selectedStoryKeys: string[];
  activityVersion: string;
  health?: TestingHealth;
  onChanged: () => void;
}) {
  const [busyAction, setBusyAction] = useState<"refresh" | "run" | "">("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [scope, setScope] = useState<"priority" | "full" | "selected">("priority");
  const [maxStories, setMaxStories] = useState("5");
  const stream = testingStreams.find((w) => w.id === selectedStreamId) ?? testingStreams[0];
  const noRepo = !stream;
  const streamDisabled = Boolean(stream && (!stream.enabled || stream.status === "disabled"));
  const busy = busyAction !== "";

  useEffect(() => {
    setMessage("");
  }, [activityVersion]);

  const refresh = async () => {
    if (!stream) return;
    setBusyAction("refresh");
    setError("");
    setMessage("");
    try {
      await api.refreshTests(project.id, stream.id);
      setMessage("refresh task queued");
      onChanged();
    } catch (e) {
      setError((e as Error).message || "refresh failed");
    }
    setBusyAction("");
  };

  const run = async () => {
    if (!stream) return;
    setBusyAction("run");
    setError("");
    setMessage("");
    const max = parseInt(maxStories, 10);
    try {
      const response = await api.runTests(project.id, stream.id, {
        scope,
        story_keys: scope === "selected" ? selectedStoryKeys : [],
        max_stories: Number.isFinite(max) && max > 0 ? max : 0,
      });
      setMessage(`episode ${response.episode.status}`);
      setDrawerOpen(false);
      onChanged();
    } catch (e) {
      setError((e as Error).message || "episode failed to start");
    }
    setBusyAction("");
  };

  return (
    <section className="scan-bar test-bar reveal">
      <div className="scan-text">
        <h2>Testing</h2>
        <select
          value={stream?.id ?? ""}
          onChange={(event) => onSelectedStream(event.target.value)}
          disabled={testingStreams.length <= 1}
        >
          {testingStreams.length === 0 && <option value="">no testing workstream</option>}
          {testingStreams.map((w) => (
            <option value={w.id} key={w.id}>{repoShort(w.repo)}</option>
          ))}
        </select>
      </div>
      <div className="scan-actions">
        {streamDisabled && <span className="muted">disabled in settings</span>}
        <div className="scan-buttons">
          <button className="ghost" onClick={refresh} disabled={busy || noRepo || streamDisabled}>
            {busyAction === "refresh" ? "refreshing..." : "refresh stories"}
          </button>
          <button onClick={() => setDrawerOpen((v) => !v)} disabled={busy || noRepo || streamDisabled}>
            run episode
          </button>
        </div>
        {drawerOpen && (
          <div className="issue-run-drawer test-run-drawer">
            <label>
              <input type="radio" checked={scope === "priority"} onChange={() => setScope("priority")} />
              priority
            </label>
            <label>
              <input type="radio" checked={scope === "selected"} onChange={() => setScope("selected")} />
              selected ({selectedStoryKeys.length})
            </label>
            <label>
              <input type="radio" checked={scope === "full"} onChange={() => setScope("full")} />
              full
            </label>
            <label>
              top
              <input className="small-number" value={maxStories} onChange={(e) => setMaxStories(e.target.value)} />
            </label>
            <button onClick={run} disabled={busy || (scope === "selected" && selectedStoryKeys.length === 0)}>
              {busyAction === "run" ? "starting..." : "start"}
            </button>
          </div>
        )}
        {error && <span className="form-error">{error}</span>}
        {message && !error && <span className="scan-summary">{message}</span>}
      </div>
      {health && health.state !== "healthy" && (
        <div className={`test-health test-health-${health.state}`}>
          <span className={`chip chip-health-${health.state}`}>{health.state}</span>
          <span>{health.summary}</span>
          {health.offer && <span className="muted">{health.offer}</span>}
          {health.action === "refresh" && (
            <button className="ghost" onClick={refresh} disabled={busy || noRepo || streamDisabled}>
              {health.state === "weak" ? "let Hive rewrite them" : "let Hive draft stories"}
            </button>
          )}
          {health.action === "episode" && (
            <button className="ghost" onClick={() => setDrawerOpen(true)} disabled={busy || noRepo || streamDisabled}>
              run episode
            </button>
          )}
        </div>
      )}
    </section>
  );
}

/** The guided-setup surface for the testability contract: one glance says
 * whether Hive knows how to stand the app up, what Hive offers to do next
 * (one click), and which decisions only the human can make — answerable
 * right here. Everything else (drafting, probing, folding answers in) is
 * agent work chained server-side. */
export function TestabilityPanel({
  project,
  stream,
  view,
  decisions,
  onChanged,
}: {
  project: Project;
  stream?: ProjectWorkstream;
  view?: TestabilityView;
  decisions: Question[];
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState<"draft" | "probe" | "answer" | "">("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [showContract, setShowContract] = useState(false);
  if (!stream || !view) return null;
  const health = view.health;
  const contract = view.contract;

  const act = async (action: "draft" | "probe") => {
    setBusy(action);
    setError("");
    setMessage("");
    try {
      await (action === "draft"
        ? api.draftTestability(project.id, stream.id)
        : api.probeTestability(project.id, stream.id));
      setMessage(action === "draft" ? "draft task queued" : "probe task queued");
      onChanged();
    } catch (e) {
      setError((e as Error).message || `${action} failed`);
    }
    setBusy("");
  };

  const answer = async (question: Question) => {
    const text = (answers[question.id] ?? "").trim();
    if (!text) return;
    setBusy("answer");
    setError("");
    try {
      await api.answerQuestion(question.id, text);
      setMessage("answered — Hive folds it into the contract");
      setAnswers((prev) => ({ ...prev, [question.id]: "" }));
      onChanged();
    } catch (e) {
      setError((e as Error).message || "answer failed");
    }
    setBusy("");
  };

  return (
    <section className={`testability-panel reveal testability-${health.state}`}>
      <div className="testability-head">
        <h3>
          Testability contract <span className={`chip chip-testability-${health.state}`}>{health.state}</span>
        </h3>
        <div className="scan-buttons">
          {contract && contract.content && (
            <button className="ghost" onClick={() => setShowContract((v) => !v)}>
              {showContract ? "hide contract" : "view contract"}
            </button>
          )}
          {(health.action === "draft" || health.state === "verified") && (
            <button className="ghost" onClick={() => act("draft")} disabled={busy !== ""}>
              {busy === "draft"
                ? "queueing..."
                : health.state === "missing"
                  ? "let Hive draft it"
                  : health.state === "broken"
                    ? "let Hive repair it"
                    : "re-draft"}
            </button>
          )}
          {health.action === "probe" && (
            <button className="ghost" onClick={() => act("probe")} disabled={busy !== ""}>
              {busy === "probe" ? "queueing..." : "prove it on a runner"}
            </button>
          )}
        </div>
      </div>
      <p className="testability-summary">
        {health.summary}
        {health.offer && <span className="muted"> {health.offer}</span>}
      </p>
      {contract && contract.status === "broken" && contract.probe_problems.length > 0 && (
        <ul className="testability-problems">
          {contract.probe_problems.map((problem) => (
            <li key={problem}>
              <code>{problem}</code>
            </li>
          ))}
        </ul>
      )}
      {decisions.length > 0 && (
        <div className="testability-decisions">
          <p className="muted">
            {decisions.length === 1 ? "This decision needs" : "These decisions need"} you — everything else is
            Hive's job:
          </p>
          {decisions.map((question) => (
            <div className="testability-decision" key={question.id}>
              <Markdown className="issue-detail" text={question.text} />
              <div className="testability-answer">
                <input
                  placeholder="your decision (e.g. option A)"
                  value={answers[question.id] ?? ""}
                  onChange={(e) => setAnswers((prev) => ({ ...prev, [question.id]: e.target.value }))}
                  onKeyDown={(e) => e.key === "Enter" && answer(question)}
                />
                <button onClick={() => answer(question)} disabled={busy !== "" || !(answers[question.id] ?? "").trim()}>
                  {busy === "answer" ? "sending..." : "answer"}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
      {showContract && contract && <Markdown className="issue-detail testability-contract" text={contract.content} />}
      {error && <span className="form-error">{error}</span>}
      {message && !error && <span className="scan-summary">{message}</span>}
    </section>
  );
}

function storySort(a: Story, b: Story): number {
  const rank: Record<Story["status"], number> = {
    failing: 0,
    blocked: 1,
    stale: 2,
    untested: 3,
    passing: 4,
    archived: 5,
  };
  return (rank[a.status] ?? 9) - (rank[b.status] ?? 9) || a.order - b.order || a.key.localeCompare(b.key);
}

export function StoriesView({
  stories,
  findings,
  episodes,
  selectedStoryKeys,
  onToggle,
}: {
  stories: Story[];
  findings: Finding[];
  episodes: TestEpisode[];
  selectedStoryKeys: string[];
  onToggle: (key: string) => void;
}) {
  const [filter, setFilter] = useState("priority");
  const [openStory, setOpenStory] = useState<string | null>(null);
  const group = STORY_GROUPS.find((g) => g.label === filter) ?? STORY_GROUPS[0];
  const activeStories = stories.filter((s) => s.status !== "archived");
  const items = activeStories
    .filter((story) => group.label === "all" || group.statuses.includes(story.status))
    .sort(storySort);
  const latestEpisode = [...episodes].sort((a, b) => b.created_at - a.created_at)[0];

  return (
    <section className="issues-view stories-view">
      {activeStories.length === 0 && <p className="muted">no stories yet - refresh to build the acceptance backlog</p>}
      {activeStories.length > 0 && (
        <>
          <div className="issue-filter">
            {STORY_GROUPS.map((g) => {
              const count = activeStories.filter((s) => g.label === "all" || g.statuses.includes(s.status)).length;
              return (
                <button className={filter === g.label ? "active" : "ghost"} key={g.label} onClick={() => setFilter(g.label)}>
                  {g.label} <span className="col-count">{count}</span>
                </button>
              );
            })}
          </div>
          {latestEpisode && (
            <p className="muted test-episode-line">
              latest episode {latestEpisode.status} - {ago(latestEpisode.created_at)}
            </p>
          )}
          <div className="issue-table-wrap">
            <table className="issue-table story-table">
              <thead>
                <tr>
                  <th aria-label="select" />
                  <th>story</th>
                  <th>state</th>
                  <th>fidelity</th>
                  <th>tested</th>
                  <th>issue</th>
                </tr>
              </thead>
              <tbody>
                {items.map((story) => {
                  const open = openStory === story.key;
                  const checked = selectedStoryKeys.includes(story.key);
                  const storyFindings = findings.filter((finding) => finding.story_key === story.key);
                  return (
                    <Fragment key={story.id}>
                      <tr>
                        <td>
                          <input type="checkbox" checked={checked} onChange={() => onToggle(story.key)} />
                        </td>
                        <td>
                          <button className="issue-title" onClick={() => setOpenStory(open ? null : story.key)}>
                            {story.title || story.key}
                          </button>
                          <small>{story.key}</small>
                        </td>
                        <td><span className={`chip chip-story-${story.status}`}>{story.status}</span></td>
                        <td><span className={`chip chip-fidelity-${story.last_fidelity}`}>{story.last_fidelity}</span></td>
                        <td>{story.last_tested_at ? ago(story.last_tested_at) : "never"}</td>
                        <td>
                          {story.open_issue_url ? (
                            <a href={story.open_issue_url} target="_blank" rel="noreferrer">#{story.open_issue_number}</a>
                          ) : (
                            <span className="muted">-</span>
                          )}
                        </td>
                      </tr>
                      {open && (
                        <tr className="issue-detail-row">
                          <td />
                          <td colSpan={5}>
                            <Markdown className="issue-detail" text={`${story.intent}\n\n${story.acceptance}`} />
                            {storyFindings.length > 0 && (
                              <div className="story-findings">
                                {storyFindings.map((finding) => (
                                  <div className="story-finding" key={finding.id}>
                                    <p>
                                      <span className={`chip chip-find-${finding.status}`}>{finding.status}</span>
                                      <b>{finding.kind.replace(/_/g, " ")}</b> {finding.summary}
                                      {finding.issue_url && (
                                        <a href={finding.issue_url} target="_blank" rel="noreferrer">
                                          #{finding.issue_number}
                                        </a>
                                      )}
                                    </p>
                                    {(finding.detail || finding.oracle) && (
                                      <div className="finding-notes">
                                        {finding.detail && <p>{finding.detail}</p>}
                                        {finding.oracle && <p>{finding.oracle}</p>}
                                      </div>
                                    )}
                                    {finding.evidence_blobs.length > 0 && (
                                      <ul className="finding-evidence">
                                        {finding.evidence_blobs.map((name) => (
                                          <li key={name}>
                                            <code>{name}</code>
                                          </li>
                                        ))}
                                      </ul>
                                    )}
                                  </div>
                                ))}
                              </div>
                            )}
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}
