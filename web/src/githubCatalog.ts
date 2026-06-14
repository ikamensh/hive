import { useEffect, useSyncExternalStore } from "react";
import { api } from "./api";
import type { GithubRepo } from "./types";

type CatalogSnap = {
  repos: GithubRepo[] | null;
  error: string;
  loading: boolean;
};

let snap: CatalogSnap = { repos: null, error: "", loading: false };
let inflight: Promise<GithubRepo[]> | null = null;
const listeners = new Set<() => void>();

function emit() {
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnap() {
  return snap;
}

export function resetGithubCatalog() {
  snap = { repos: null, error: "", loading: false };
  inflight = null;
  emit();
}

export function ensureGithubCatalog(): Promise<GithubRepo[]> {
  if (snap.repos) return Promise.resolve(snap.repos);
  if (inflight) return inflight;
  snap = { ...snap, loading: true, error: "" };
  emit();
  inflight = api
    .githubRepos()
    .then((repos) => {
      snap = { repos, error: "", loading: false };
      return repos;
    })
    .catch((err: Error & { status?: number }) => {
      snap = {
        repos: null,
        error: err.message || "could not load repos",
        loading: false,
      };
      inflight = null;
      throw err;
    })
    .finally(() => {
      inflight = null;
      emit();
    });
  return inflight;
}

/** One shared catalog for all repo pickers on the page. */
export function useGithubCatalog(open: boolean) {
  const state = useSyncExternalStore(subscribe, getSnap, getSnap);
  useEffect(() => {
    if (open) void ensureGithubCatalog().catch(() => {});
  }, [open]);
  return state;
}
