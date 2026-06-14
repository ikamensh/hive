import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { api, repoKey, repoShort } from "../api";
import { useGithubCatalog } from "../githubCatalog";
import type { GithubRepo } from "../types";

const MENU_LIMIT = 20;

function filterCatalog(catalog: GithubRepo[], query: string, excluded: Set<string>): GithubRepo[] {
  const needle = query.trim().toLowerCase();
  return catalog
    .filter((repo) => !excluded.has(repoKey(repo.ssh_url)))
    .filter((repo) => !needle || repo.full_name.toLowerCase().includes(needle))
    .slice(0, MENU_LIMIT);
}

function catalogHit(catalog: GithubRepo[] | null, ref: string): GithubRepo | undefined {
  if (!catalog) return undefined;
  const key = repoKey(ref);
  return catalog.find((repo) => repoKey(repo.ssh_url) === key || repo.full_name.toLowerCase() === key);
}

async function resolveRepo(ref: string, catalog: GithubRepo[] | null): Promise<GithubRepo> {
  const hit = catalogHit(catalog, ref);
  if (hit) return hit;
  return api.validateGithubRepo(ref.trim());
}

function RepoMenu({
  listId,
  loading,
  error,
  query,
  options,
  onPick,
  emptyHint,
  manualHint,
}: {
  listId: string;
  loading: boolean;
  error: string;
  query: string;
  options: GithubRepo[];
  onPick: (repo: GithubRepo) => void;
  emptyHint: string;
  manualHint: string;
}) {
  return (
    <div className="repo-ac-menu" id={listId} role="listbox">
      {loading && <p className="repo-ac-hint">loading repos…</p>}
      {!loading && error && <p className="repo-ac-hint repo-ac-error">{error}</p>}
      {!loading && !error && options.length === 0 && (
        <p className="repo-ac-hint">{query.trim() ? manualHint : emptyHint}</p>
      )}
      {options.map((repo) => (
        <button
          key={repo.full_name}
          type="button"
          role="option"
          className="repo-ac-option"
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => onPick(repo)}
        >
          <span className="repo-ac-name">{repo.full_name}</span>
          {repo.private && <span className="repo-ac-badge">private</span>}
          {repo.description && <span className="repo-ac-desc">{repo.description}</span>}
        </button>
      ))}
    </div>
  );
}

function RepoAutocomplete({
  value,
  onChange,
  placeholder,
  exclude = [],
}: {
  value: string;
  onChange: (url: string) => void;
  placeholder?: string;
  exclude?: string[];
}) {
  const listId = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const [draft, setDraft] = useState(value);
  const [open, setOpen] = useState(false);
  const [validating, setValidating] = useState(false);
  const [fieldError, setFieldError] = useState("");
  const { repos: catalog, loading, error: catalogError } = useGithubCatalog(open);
  const excluded = useMemo(() => new Set(exclude.map(repoKey)), [exclude]);
  const options = useMemo(
    () => (catalog ? filterCatalog(catalog, draft, excluded) : []),
    [catalog, draft, excluded],
  );

  useEffect(() => {
    setDraft(value);
  }, [value]);

  useEffect(() => {
    const onDoc = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  const pick = (repo: GithubRepo) => {
    onChange(repo.ssh_url);
    setDraft(repo.ssh_url);
    setFieldError("");
    setOpen(false);
  };

  const commitDraft = async () => {
    const trimmed = draft.trim();
    if (!trimmed) return;
    const hit = catalogHit(catalog, trimmed);
    if (hit) {
      pick(hit);
      return;
    }
    setValidating(true);
    setFieldError("");
    try {
      const repo = await resolveRepo(trimmed, catalog);
      pick(repo);
    } catch (err) {
      setFieldError(err instanceof Error ? err.message : "repo not accessible");
    } finally {
      setValidating(false);
    }
  };

  return (
    <div className="repo-ac" ref={rootRef}>
      <input
        value={draft}
        onChange={(e) => {
          setDraft(e.target.value);
          onChange(e.target.value);
          setFieldError("");
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => {
          if (draft.trim() && draft.trim() !== value.trim()) void commitDraft();
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            void commitDraft();
          }
        }}
        placeholder={placeholder}
        autoComplete="off"
        aria-expanded={open}
        aria-controls={listId}
        disabled={validating}
      />
      {validating && <p className="repo-ac-hint">checking repo…</p>}
      {fieldError && <p className="repo-ac-hint repo-ac-error">{fieldError}</p>}
      {open && (
        <RepoMenu
          listId={listId}
          loading={loading}
          error={catalogError}
          query={draft}
          options={options}
          onPick={pick}
          emptyHint="type to search your repos"
          manualHint="no matches — finish typing owner/repo or URL, blur to validate"
        />
      )}
    </div>
  );
}

export function RepoUrlInput({
  value,
  onChange,
  placeholder,
}: {
  value: string;
  onChange: (url: string) => void;
  placeholder?: string;
}) {
  return <RepoAutocomplete value={value} onChange={onChange} placeholder={placeholder} />;
}

export function RepoListEditor({
  repos,
  onChange,
}: {
  repos: string[];
  onChange: (repos: string[]) => void;
}) {
  const [draft, setDraft] = useState("");
  const [open, setOpen] = useState(false);
  const [validating, setValidating] = useState(false);
  const [fieldError, setFieldError] = useState("");
  const rootRef = useRef<HTMLDivElement>(null);
  const listId = useId();
  const { repos: catalog, loading, error: catalogError } = useGithubCatalog(open);
  const excluded = useMemo(() => new Set(repos.map(repoKey)), [repos]);
  const options = useMemo(
    () => (catalog ? filterCatalog(catalog, draft, excluded) : []),
    [catalog, draft, excluded],
  );

  const addRepo = useCallback(
    async (ref: string) => {
      const trimmed = ref.trim();
      if (!trimmed) return;
      const key = repoKey(trimmed);
      if (repos.some((repo) => repoKey(repo) === key)) {
        setDraft("");
        setOpen(false);
        return;
      }
      setValidating(true);
      setFieldError("");
      try {
        const repo = await resolveRepo(trimmed, catalog);
        if (repos.some((item) => repoKey(item) === repoKey(repo.ssh_url))) return;
        onChange([...repos, repo.ssh_url]);
        setDraft("");
        setOpen(false);
      } catch (err) {
        setFieldError(err instanceof Error ? err.message : "repo not accessible");
      } finally {
        setValidating(false);
      }
    },
    [catalog, onChange, repos],
  );

  useEffect(() => {
    const onDoc = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  return (
    <div className="repo-list-editor">
      {repos.length > 0 && (
        <ul className="repo-chips">
          {repos.map((repo) => (
            <li key={repoKey(repo)} className="repo-chip">
              <span title={repo}>{repoShort(repo)}</span>
              <button
                type="button"
                className="repo-chip-remove"
                aria-label={`remove ${repoShort(repo)}`}
                onClick={() => onChange(repos.filter((item) => repoKey(item) !== repoKey(repo)))}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="repo-add" ref={rootRef}>
        <input
          value={draft}
          onChange={(e) => {
            setDraft(e.target.value);
            setFieldError("");
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              void addRepo(draft);
            }
          }}
          placeholder="search or type owner/repo, Enter to add"
          autoComplete="off"
          aria-expanded={open}
          aria-controls={listId}
          disabled={validating}
        />
        <button
          type="button"
          className="ghost repo-add-btn"
          disabled={validating || !draft.trim()}
          onClick={() => void addRepo(draft)}
        >
          {validating ? "…" : "add"}
        </button>
        {fieldError && <p className="repo-ac-hint repo-ac-error repo-add-error">{fieldError}</p>}
        {open && (
          <RepoMenu
            listId={listId}
            loading={loading}
            error={catalogError}
            query={draft}
            options={options}
            onPick={(repo) => void addRepo(repo.ssh_url)}
            emptyHint="type to search your repos"
            manualHint="no matches — type owner/repo or URL, then Enter to validate"
          />
        )}
      </div>
    </div>
  );
}
