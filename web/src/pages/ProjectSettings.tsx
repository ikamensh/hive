import { Link, useNavigate, useParams } from "react-router-dom";
import { api, repoShort, usePoll } from "../api";
import { StateBadge } from "../components/shared";
import { ProjectActions, ProjectSettings } from "../features/project/controls";
import type { ProjectPatch } from "../types";

export default function ProjectSettingsPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const { data, failed, refresh } = usePoll(() => api.project(id), [id]);

  if (!data) {
    return <div className="page">{failed ? <p className="muted">project unreachable</p> : <p className="muted">loading...</p>}</div>;
  }

  const { project, workstreams } = data;

  const patch = async (p: ProjectPatch) => {
    await api.patchProject(id, p);
    if (p.archived) {
      navigate("/");
      return;
    }
    refresh();
  };

  const patchWorkstream = async (workstreamId: string, p: { enabled?: boolean }) => {
    await api.updateWorkstream(id, workstreamId, p);
    refresh();
  };

  return (
    <div className="page page-project-settings">
      <div className="page-head">
        <div>
          <Link className="back-link" to={`/p/${id}`}>
            <i className="ti ti-arrow-left" aria-hidden /> Back to {project.name}
          </Link>
          <h1>
            Project settings
            <span className="head-repo">
              {project.name}
              {project.spec_repo ? ` - ${repoShort(project.spec_repo)}` : ""}
            </span>
          </h1>
        </div>
        <StateBadge state={project.state} />
        <ProjectActions project={project} onPatch={patch} />
      </div>
      <ProjectSettings
        project={project}
        workstreams={workstreams}
        onPatch={patch}
        onPatchWorkstream={patchWorkstream}
      />
    </div>
  );
}
