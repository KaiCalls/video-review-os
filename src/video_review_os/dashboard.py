from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from .approval import approval_status, assembly_approval_status, load_approvals
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
    captions = _read_optional(project_dir / "captions.json", {"captions": []})
    scenes = _read_optional(project_dir / "scenes.json", {"scenes": []})
    visuals = _read_optional(project_dir / "visuals.json", {"visuals": []})
    renders = _read_optional(project_dir / "renders.json", {"renders": []})
    post_queue = _read_optional(project_dir / "post_queue.json", {"items": []})
    assemblies_artifact = _read_optional(project_dir / "assemblies.json", {"assemblies": []})
    assembly_renders = _read_optional(project_dir / "assembly_renders.json", {"renders": []})
    approvals = load_approvals(project_dir)
    drafts_by_clip = {draft["clip_id"]: draft for draft in drafts.get("drafts", [])}
    captions_by_clip = {item["clip_id"]: item for item in captions.get("captions", [])}
    scenes_by_clip = {item["clip_id"]: _scene_with_uris(item) for item in scenes.get("scenes", [])}
    visuals_by_clip = {item["clip_id"]: _visual_with_uris(item) for item in visuals.get("visuals", [])}
    renders_by_clip = {item["clip_id"]: item for item in renders.get("renders", [])}
    post_by_clip = {item["clip_id"]: item for item in post_queue.get("items", [])}
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
                "repair_ops": candidate.get("quality_gate", {}).get("repair_ops", []),
                "text": candidate.get("text", ""),
                "draft": drafts_by_clip.get(candidate["clip_id"]),
                "caption": captions_by_clip.get(candidate["clip_id"]),
                "scenes": scenes_by_clip.get(candidate["clip_id"]),
                "visual": visuals_by_clip.get(candidate["clip_id"]),
                "render": renders_by_clip.get(candidate["clip_id"]),
                "post_queue": post_by_clip.get(candidate["clip_id"]),
                "approval_status": approval_status(candidate, approvals),
                "renders_by_default": candidate["decision"] == "keep",
            }
        )
    assembly_renders_by_id = {item["assembly_id"]: item for item in assembly_renders.get("renders", [])}
    assemblies = []
    for assembly in assemblies_artifact.get("assemblies", []):
        assemblies.append(
            {
                "assembly_id": assembly["assembly_id"],
                "kind": assembly.get("kind"),
                "rationale": assembly.get("rationale", ""),
                "source_clip_ids": assembly.get("source_clip_ids", []),
                "member_decisions": assembly.get("member_decisions", []),
                "segment_count": len(assembly.get("segments", [])),
                "total_duration": assembly.get("total_duration"),
                "renderable": assembly.get("renderable", False),
                "segments": assembly.get("segments", []),
                "applied_ops": assembly.get("applied_ops", []),
                "unresolved_ops": assembly.get("unresolved_ops", []),
                "approval_status": assembly_approval_status(assembly, approvals),
                "render": assembly_renders_by_id.get(assembly["assembly_id"]),
            }
        )

    return {
        "project_id": source["project_id"],
        "filename": source["source"]["filename"],
        "duration_seconds": source.get("media", {}).get("duration_seconds"),
        "project_dir": str(project_dir),
        "candidates": candidates,
        "assemblies": assemblies,
    }


def _read_optional(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    return read_json(path) if path.exists() else fallback


def _scene_with_uris(scene: dict[str, Any]) -> dict[str, Any]:
    copy = dict(scene)
    frames = []
    for frame in scene.get("frames", []):
        item = dict(frame)
        path = item.get("path")
        if path and item.get("status") == "written":
            local_path = Path(path)
            if local_path.exists():
                item["uri"] = local_path.resolve().as_uri()
        frames.append(item)
    copy["frames"] = frames
    return copy


def _visual_with_uris(visual: dict[str, Any]) -> dict[str, Any]:
    copy = dict(visual)
    for key in ["thumbnail_svg", "scene_card_svg"]:
        path = copy.get(key)
        if path and Path(path).exists():
            copy[f"{key}_uri"] = Path(path).resolve().as_uri()
    return copy


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
    h3 {{ font-size: 15px; margin: 18px 0 6px; color: #5b5d57; text-transform: uppercase; letter-spacing: 0.04em; }}
    .meta {{ color: #5b5d57; font-size: 14px; margin-bottom: 18px; }}
    .clip {{ background: #fff; border: 1px solid #d8d8d1; border-radius: 8px; padding: 14px; margin: 12px 0; }}
    .assembly {{ border-left: 4px solid #2563eb; }}
    .rejected {{ margin: 12px 0; opacity: 0.85; }}
    .rejected > summary {{ cursor: pointer; color: #8a3b30; font-size: 14px; }}
    .row {{ display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }}
    .pill {{ border-radius: 999px; border: 1px solid #c9c9c1; padding: 3px 8px; font-size: 12px; }}
    .frames {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 8px; margin: 10px 0; }}
    .frames img {{ width: 100%; aspect-ratio: 16 / 9; object-fit: cover; border-radius: 6px; border: 1px solid #d8d8d1; background: #eee; }}
    .frame-placeholder {{ display: grid; place-items: center; min-height: 78px; border: 1px dashed #c9c9c1; border-radius: 6px; color: #5b5d57; font-size: 12px; }}
    .visual {{ margin: 10px 0; }}
    .visual img {{ width: 100%; max-width: 520px; aspect-ratio: 16 / 9; object-fit: contain; border-radius: 6px; border: 1px solid #d8d8d1; background: #111827; }}
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
      .frames img {{ border-color: #44463f; background: #171816; }}
      .frame-placeholder {{ border-color: #44463f; color: #b8bbb2; }}
      .visual img {{ border-color: #44463f; }}
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
root.innerHTML = data.projects.map(project => {{
  const visible = project.candidates.filter(c => c.decision !== 'reject');
  const rejected = project.candidates.filter(c => c.decision === 'reject');
  const assemblies = project.assemblies || [];
  return `
  <section>
    <h2>${{escapeHtml(project.filename)}} <span class="pill">${{project.candidates.length}} candidates</span> <span class="pill">${{assemblies.length}} assemblies</span></h2>
    ${{assemblies.length ? `<h3>Auto-generated drafts</h3>${{assemblies.map(renderAssembly).join('')}}` : ''}}
    <h3>Clip candidates</h3>
    ${{visible.map(renderClip).join('')}}
    ${{rejected.length ? `<details class="rejected"><summary>Rejected (${{rejected.length}}) &mdash; never rendered, never queued</summary>${{rejected.map(renderClip).join('')}}</details>` : ''}}
  </section>
  `;
}}).join('');

function renderAssembly(a) {{
  return `
    <article class="clip assembly">
      <div class="row">
        <strong>${{escapeHtml(a.assembly_id)}}</strong>
        <span class="pill">${{escapeHtml(a.kind || 'single')}}</span>
        <span class="pill">${{a.segment_count}} segments</span>
        <span class="pill">${{Number(a.total_duration || 0).toFixed(1)}}s</span>
        <span class="pill">${{escapeHtml(a.approval_status)}}</span>
        ${{a.render ? `<span class="pill">render ${{escapeHtml(a.render.status)}}</span>` : ''}}
        <span class="pill">from ${{escapeHtml((a.source_clip_ids || []).join(', '))}}</span>
      </div>
      ${{a.rationale ? `<p>${{escapeHtml(a.rationale)}}</p>` : ''}}
      <details><summary>Edit decision list (${{a.segment_count}} segments)</summary><pre>${{escapeHtml(JSON.stringify(a.segments, null, 2))}}</pre></details>
      ${{(a.applied_ops || []).length ? `<details><summary>Applied repair ops</summary><pre>${{escapeHtml(JSON.stringify(a.applied_ops, null, 2))}}</pre></details>` : ''}}
      ${{(a.unresolved_ops || []).length ? `<details><summary>Needs a person (unresolved)</summary><pre>${{escapeHtml(JSON.stringify(a.unresolved_ops, null, 2))}}</pre></details>` : ''}}
    </article>
  `;
}}

function renderClip(clip) {{
  return `
      <article class="clip decision-${{escapeHtml(clip.decision)}}">
        <div class="row">
          <strong>${{escapeHtml(clip.clip_id)}}</strong>
          <span class="pill decision-${{escapeHtml(clip.decision)}}">${{escapeHtml(clip.decision)}}</span>
          <span class="pill">score ${{clip.score ?? 'n/a'}}</span>
          <span class="pill">${{Number(clip.start).toFixed(1)}}-${{Number(clip.end).toFixed(1)}}s</span>
          <span class="pill">${{escapeHtml(clip.approval_status)}}</span>
          ${{(clip.repair_ops || []).length ? `<span class="pill">repair ${{clip.repair_ops.length}}</span>` : ''}}
          ${{clip.caption ? `<span class="pill">captions ${{escapeHtml(clip.caption.status)}}</span>` : ''}}
          ${{clip.scenes ? `<span class="pill">frames ${{writtenFrames(clip.scenes)}}/${{clip.scenes.frames?.length || 0}}</span>` : ''}}
          ${{clip.visual ? `<span class="pill">visual ${{escapeHtml(clip.visual.status)}}</span>` : ''}}
          ${{clip.render ? `<span class="pill">render ${{escapeHtml(clip.render.status)}}</span>` : ''}}
          ${{clip.post_queue ? `<span class="pill">post ${{escapeHtml(clip.post_queue.status)}}</span>` : ''}}
        </div>
        ${{clip.visual?.thumbnail_svg_uri ? `<div class="visual"><img src="${{escapeHtml(clip.visual.thumbnail_svg_uri)}}" alt="${{escapeHtml(clip.clip_id)}} thumbnail draft"></div>` : ''}}
        ${{clip.scenes?.frames?.length ? `<div class="frames">${{clip.scenes.frames.map(frame => frame.uri ? `<img src="${{escapeHtml(frame.uri)}}" alt="${{escapeHtml(clip.clip_id)}} frame ${{frame.index}}">` : `<span class="frame-placeholder">${{Number(frame.timestamp).toFixed(1)}}s - ${{escapeHtml(frame.status)}}</span>`).join('')}}</div>` : ''}}
        <p>${{escapeHtml(clip.text || 'No transcript text available.')}}</p>
        ${{(clip.repair_ops || []).length ? `<details><summary>Repair plan (${{clip.repair_ops.length}})</summary><pre>${{escapeHtml(JSON.stringify(clip.repair_ops, null, 2))}}</pre></details>` : ''}}
        ${{clip.draft ? `<details><summary>Draft copy</summary><pre>${{escapeHtml(JSON.stringify(clip.draft, null, 2))}}</pre></details>` : ''}}
        ${{clip.caption ? `<details><summary>Caption files</summary><pre>${{escapeHtml(JSON.stringify(clip.caption, null, 2))}}</pre></details>` : ''}}
        ${{clip.visual ? `<details><summary>Visual drafts</summary><pre>${{escapeHtml(JSON.stringify(clip.visual, null, 2))}}</pre></details>` : ''}}
        ${{clip.post_queue ? `<details><summary>Post queue item</summary><pre>${{escapeHtml(JSON.stringify(clip.post_queue, null, 2))}}</pre></details>` : ''}}
        ${{clip.flags.length ? `<details><summary>Quality flags</summary><pre>${{escapeHtml(JSON.stringify(clip.flags, null, 2))}}</pre></details>` : ''}}
      </article>
  `;
}}
function escapeHtml(value) {{
  return String(value).replace(/[&<>"']/g, char => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[char]));
}}
function writtenFrames(scene) {{
  return (scene.frames || []).filter(frame => frame.status === 'written').length;
}}
</script>
</body>
</html>
"""
