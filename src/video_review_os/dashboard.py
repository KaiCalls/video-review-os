from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from .approval import approval_status, load_approvals
from .config import VideoReviewConfig
from .utils import atomic_write_json, atomic_write_text, ensure_dir, read_json, utc_now_iso


def build_dashboard(config: VideoReviewConfig, project_dirs: list[Path] | None = None) -> tuple[Path, Path]:
    projects = project_dirs or sorted(path for path in config.paths.projects_dir.iterdir() if path.is_dir())
    data = {
        "schema_version": "video_review_os.dashboard.v1",
        "created_at": utc_now_iso(),
        "review_only": True,
        "projects": [_project_summary(project) for project in projects if (project / "source.json").exists()],
    }
    ensure_dir(config.paths.dashboard_dir)
    json_path = config.paths.dashboard_dir / "dashboard.json"
    html_path = config.paths.dashboard_dir / "index.html"
    atomic_write_json(json_path, data)
    atomic_write_text(html_path, dashboard_html(data))
    return json_path, html_path


def _project_summary(project_dir: Path) -> dict[str, Any]:
    source = read_json(project_dir / "source.json")
    clips = read_json(project_dir / "clips.json") if (project_dir / "clips.json").exists() else {"candidates": []}
    drafts = (
        read_json(project_dir / "drafts" / "copy.json")
        if (project_dir / "drafts" / "copy.json").exists()
        else {"drafts": []}
    )
    approvals = load_approvals(project_dir)
    drafts_by_clip = {draft["clip_id"]: draft for draft in drafts.get("drafts", [])}
    candidates = []
    for candidate in clips.get("candidates", []):
        candidates.append(
            {
                "clip_id": candidate["clip_id"],
                "start": candidate["start"],
                "end": candidate["end"],
                "decision": candidate["decision"],
                "score": candidate.get("quality_gate", {}).get("score"),
                "flags": candidate.get("quality_gate", {}).get("flags", []),
                "text": candidate.get("text", ""),
                "draft": drafts_by_clip.get(candidate["clip_id"]),
                "approval_status": approval_status(candidate, approvals),
                "renders_by_default": candidate["decision"] == "keep",
            }
        )
    return {
        "project_id": source["project_id"],
        "filename": source["source"]["filename"],
        "duration_seconds": source.get("media", {}).get("duration_seconds"),
        "project_dir": str(project_dir),
        "candidates": candidates,
    }


def dashboard_html(data: dict[str, Any]) -> str:
    payload = html.escape(json.dumps(data), quote=True)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Video Review OS Dashboard</title>
  <style>
    :root {{ color-scheme: light dark; font-family: Inter, system-ui, -apple-system, sans-serif; }}
    body {{ margin: 0; background: #f7f7f4; color: #1d1d1b; }}
    main {{ max-width: 1120px; margin: 0 auto; padding: 24px 14px 48px; }}
    h1 {{ font-size: 24px; margin: 0 0 6px; }}
    h2 {{ font-size: 18px; margin: 28px 0 10px; }}
    .meta {{ color: #5b5d57; font-size: 14px; margin-bottom: 18px; }}
    .clip {{ background: #fff; border: 1px solid #d8d8d1; border-radius: 8px; padding: 14px; margin: 12px 0; }}
    .row {{ display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }}
    .pill {{ border-radius: 999px; border: 1px solid #c9c9c1; padding: 3px 8px; font-size: 12px; }}
    .decision-keep {{ background: #e8f5ec; }}
    .decision-trim {{ background: #fff4d6; }}
    .decision-review {{ background: #e8eef8; }}
    .decision-reject {{ background: #f8e4e1; }}
    p {{ line-height: 1.45; }}
    details {{ margin-top: 8px; }}
    pre {{ white-space: pre-wrap; word-break: break-word; background: #f1f1ec; padding: 10px; border-radius: 6px; }}
    @media (prefers-color-scheme: dark) {{
      body {{ background: #171816; color: #f4f4ee; }}
      .meta {{ color: #b8bbb2; }}
      .clip {{ background: #22231f; border-color: #44463f; }}
      pre {{ background: #171816; }}
    }}
  </style>
</head>
<body>
<main>
  <h1>Video Review OS</h1>
  <div class="meta">Review-only dashboard. Nothing here publishes or moves files to a platform.</div>
  <div id="app" data-json="{payload}"></div>
</main>
<script>
const root = document.getElementById('app');
const data = JSON.parse(root.dataset.json);
root.innerHTML = data.projects.map(project => `
  <section>
    <h2>${{escapeHtml(project.filename)}} <span class="pill">${{project.candidates.length}} candidates</span></h2>
    ${{project.candidates.map(clip => `
      <article class="clip">
        <div class="row">
          <strong>${{escapeHtml(clip.clip_id)}}</strong>
          <span class="pill decision-${{escapeHtml(clip.decision)}}">${{escapeHtml(clip.decision)}}</span>
          <span class="pill">score ${{clip.score ?? 'n/a'}}</span>
          <span class="pill">${{Number(clip.start).toFixed(1)}}-${{Number(clip.end).toFixed(1)}}s</span>
          <span class="pill">${{escapeHtml(clip.approval_status)}}</span>
        </div>
        <p>${{escapeHtml(clip.text || 'No transcript text available.')}}</p>
        ${{clip.draft ? `<details><summary>Draft copy</summary><pre>${{escapeHtml(JSON.stringify(clip.draft, null, 2))}}</pre></details>` : ''}}
        ${{clip.flags.length ? `<details><summary>Quality flags</summary><pre>${{escapeHtml(JSON.stringify(clip.flags, null, 2))}}</pre></details>` : ''}}
      </article>
    `).join('')}}
  </section>
`).join('');
function escapeHtml(value) {{
  return String(value).replace(/[&<>"']/g, char => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[char]));
}}
</script>
</body>
</html>
"""
