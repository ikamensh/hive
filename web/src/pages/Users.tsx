import { useState } from "react";
import { ago, api, usePoll } from "../api";
import type { WorkspaceMember, WorkspaceRole } from "../types";

const ROLE_LABEL: Record<WorkspaceRole, string> = {
  admin: "admin",
  resource_provider: "resource provider",
};

const ROLE_TITLE: Record<WorkspaceRole, string> = {
  admin: "Full control: projects, work, members, everything.",
  resource_provider:
    "Lends machines and licenses but cannot edit projects or work — manages only what they own and the todos assigned to them.",
};

function RoleControl({
  member,
  canEdit,
  onChanged,
}: {
  member: WorkspaceMember;
  canEdit: boolean;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (!canEdit) {
    return (
      <span className={`chip role-${member.role}`} title={ROLE_TITLE[member.role]}>
        {ROLE_LABEL[member.role]}
      </span>
    );
  }
  const change = async (role: WorkspaceRole) => {
    setBusy(true);
    setError("");
    try {
      await api.setUserRole(member.user.id, role);
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "could not change role");
    } finally {
      setBusy(false);
    }
  };
  return (
    <span className="role-control">
      <select
        value={member.role}
        disabled={busy}
        title={ROLE_TITLE[member.role]}
        onChange={(e) => change(e.target.value as WorkspaceRole)}
      >
        <option value="admin">admin</option>
        <option value="resource_provider">resource provider</option>
      </select>
      {error && <span className="form-error">{error}</span>}
    </span>
  );
}

export default function Users() {
  const { data: members, failed, refresh } = usePoll(() => api.users(), []);
  const { data: auth } = usePoll(() => api.me(), [], 30000);
  const isAdmin = auth?.role !== "resource_provider";

  return (
    <div className="page page-users">
      <div className="page-head">
        <h1>Users</h1>
      </div>
      <p className="muted">
        Everyone in this workspace, with the machines and licenses they bring. Admins run the
        show; a <b title={ROLE_TITLE.resource_provider}>resource provider</b> lends capacity
        without edit rights — Hive routes auth todos (CLI logins, restarts) to whoever owns the
        machine that needs hands.
      </p>
      {!members && <p className="muted">{failed ? "unreachable" : "loading…"}</p>}

      {members && (
        <table className="res-table users-table">
          <thead>
            <tr>
              <th>user</th>
              <th>role</th>
              <th>machines</th>
              <th>licenses</th>
              <th className="num">todos on them</th>
              <th>last seen</th>
            </tr>
          </thead>
          <tbody>
            {members.map((m) => (
              <tr key={m.user.id}>
                <td>
                  <span className="mono">@{m.user.github_login}</span>
                  {m.is_you && <span className="chip you-chip">you</span>}
                  {m.user.display_name && m.user.display_name !== m.user.github_login && (
                    <span className="muted"> {m.user.display_name}</span>
                  )}
                </td>
                <td>
                  <RoleControl member={m} canEdit={isAdmin} onChanged={refresh} />
                </td>
                <td>
                  {m.machines.length > 0
                    ? m.machines.map((machine) => (
                        <span key={machine.id} className="chip" title={machine.device_kind}>
                          {machine.name}
                        </span>
                      ))
                    : <span className="muted">—</span>}
                </td>
                <td>
                  {m.subscriptions.length > 0
                    ? m.subscriptions.map((s) => (
                        <span key={s.id} className="chip mono" title={s.plan || undefined}>
                          {s.provider}
                        </span>
                      ))
                    : <span className="muted">—</span>}
                </td>
                <td className="num">
                  {m.open_todos > 0 ? <span className="badge hot">{m.open_todos}</span> : "0"}
                </td>
                <td className="muted">{m.user.last_seen ? ago(m.user.last_seen) : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <p className="muted users-hint">
        Members join by signing in with GitHub; the allow-list is the chief&apos;s{" "}
        <code>HIVE_ALLOWED_GITHUB_USERS</code>. Machines are claimed on the machines page;
        licenses belong to whoever records them.
      </p>
    </div>
  );
}
