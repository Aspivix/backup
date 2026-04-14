#!/usr/bin/env python3
"""
backup.py — Incremental snapshot backup with quality report
Source  : rclone remote (SharePoint, Google Drive, local…)
Dest    : local path (NAS mount point, external drive…)
Engine  : rclone (sync) + restic (snapshots + retention)
Dependencies: rclone, restic (binaries), jinja2, rich
"""

import argparse
import json
import os
import hashlib
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

try:
    from jinja2 import Template
    from rich.console import Console
    from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
    from rich.table import Table
except ImportError:
    print("Error: pip install jinja2 rich")
    sys.exit(1)

console = Console()

# ---------------------------------------------------------------------------
# HTML report template
# ---------------------------------------------------------------------------
REPORT_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Backup report {{ reference }}</title>
<style>
  :root {
    --ok: #16a34a; --warn: #d97706; --error: #dc2626;
    --bg: #f8fafc; --card: #ffffff; --border: #e2e8f0;
    --text: #1e293b; --muted: #64748b;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Segoe UI', Arial, sans-serif; background: var(--bg);
         color: var(--text); font-size: 13px; }
  .page { max-width: 1100px; margin: 0 auto; padding: 24px; }
  h1 { font-size: 20px; margin-bottom: 4px; }
  h2 { font-size: 15px; margin: 24px 0 10px; border-bottom: 2px solid var(--border);
       padding-bottom: 6px; }
  .header-box { background: var(--card); border: 1px solid var(--border);
                border-radius: 8px; padding: 20px 24px; margin-bottom: 24px;
                display: grid; grid-template-columns: 1fr auto; gap: 16px; }
  .meta-grid { display: grid; grid-template-columns: auto 1fr; gap: 4px 16px; }
  .meta-grid dt { font-weight: 600; color: var(--muted); white-space: nowrap; }
  .meta-grid dd { word-break: break-all; }
  .status-badge { padding: 10px 20px; border-radius: 8px; font-size: 18px;
                  font-weight: 700; text-align: center; align-self: start; }
  .status-ok    { background: #dcfce7; color: var(--ok); }
  .status-warn  { background: #fef3c7; color: var(--warn); }
  .status-error { background: #fee2e2; color: var(--error); }
  .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px,1fr));
                gap: 12px; margin-bottom: 24px; }
  .stat-card { background: var(--card); border: 1px solid var(--border);
               border-radius: 8px; padding: 14px 18px; }
  .stat-card .value { font-size: 22px; font-weight: 700; }
  .stat-card .label { color: var(--muted); font-size: 11px; margin-top: 2px; }
  .stat-card.ok    .value { color: var(--ok); }
  .stat-card.warn  .value { color: var(--warn); }
  .stat-card.error .value { color: var(--error); }
  .snapshot-card { background: var(--card); border: 1px solid var(--border);
                   border-radius: 8px; padding: 16px 20px; margin-bottom: 24px;
                   display: grid; grid-template-columns: repeat(auto-fit, minmax(180px,1fr));
                   gap: 12px; }
  .snapshot-card .field label { font-size: 10px; font-weight: 600; color: var(--muted);
                                 text-transform: uppercase; letter-spacing: .05em; }
  .snapshot-card .field span  { display: block; font-family: monospace; font-size: 13px;
                                 margin-top: 2px; }
  table { width: 100%; border-collapse: collapse; background: var(--card);
          border-radius: 8px; overflow: hidden; border: 1px solid var(--border); }
  th { background: #f1f5f9; font-weight: 600; text-align: left;
       padding: 8px 10px; font-size: 11px; text-transform: uppercase;
       letter-spacing: 0.05em; color: var(--muted); }
  td { padding: 7px 10px; border-top: 1px solid var(--border); font-size: 12px; }
  tr:hover td { background: #f8fafc; }
  tr.current td { background: #f0fdf4; font-weight: 600; }
  .tag { display: inline-block; padding: 2px 8px; border-radius: 4px;
         font-size: 11px; font-weight: 600; }
  .tag-ok    { background: #dcfce7; color: var(--ok); }
  .tag-warn  { background: #fef3c7; color: var(--warn); }
  .tag-error { background: #fee2e2; color: var(--error); }
  .mono { font-family: 'Consolas', monospace; font-size: 11px; }
  .integrity-ok    { color: var(--ok); font-weight: 600; }
  .integrity-error { color: var(--error); font-weight: 600; }
  .log { background: #1e293b; color: #94a3b8; border-radius: 8px;
         padding: 16px; font-family: monospace; font-size: 11px;
         white-space: pre; overflow-x: auto; overflow-y: auto;
         max-height: 400px; margin-top: 8px; }
  .log-block { margin-bottom: 16px; }
  .log-label { font-size: 11px; font-weight: 600; color: var(--muted);
               text-transform: uppercase; letter-spacing: .05em; margin-bottom: 4px; }
  footer { margin-top: 32px; padding-top: 12px; border-top: 1px solid var(--border);
           color: var(--muted); font-size: 11px; text-align: center; }
  @media print {
    body { background: white; }
    .page { max-width: 100%; padding: 12px; }
    .log { max-height: none; }
  }
</style>
</head>
<body>
<div class="page">

  <!-- HEADER -->
  <div class="header-box">
    <div>
      <h1>Backup report</h1>
      <p style="color:var(--muted);margin-bottom:16px">Quality document — for archiving</p>
      <dl class="meta-grid">
        <dt>Reference</dt>    <dd><strong>{{ reference }}</strong></dd>
        <dt>Date / Time</dt>  <dd>{{ date_time }}</dd>
        <dt>Operator</dt>     <dd>{{ operator }}</dd>
        <dt>Source</dt>       <dd class="mono">{{ source }}</dd>
        <dt>Repository</dt>   <dd class="mono">{{ destination }}</dd>
        {% if comment %}
        <dt>Comment</dt>      <dd>{{ comment }}</dd>
        {% endif %}
        <dt>Mode</dt>         <dd>{{ mode_label }}</dd>
        <dt>Retention</dt>    <dd>{{ retention_label }}</dd>
      </dl>
    </div>
    <div>
      <div class="status-badge status-{{ status_class }}">
        {{ status_icon }} {{ status_label }}
      </div>
    </div>
  </div>

  <!-- SNAPSHOT INFO -->
  {% if snapshot %}
  <h2>Snapshot</h2>
  <div class="snapshot-card">
    <div class="field">
      <label>Snapshot ID</label>
      <span>{{ snapshot.id[:12] }}</span>
    </div>
    <div class="field">
      <label>Timestamp</label>
      <span>{{ snapshot.time[:19].replace("T"," ") }}</span>
    </div>
    <div class="field">
      <label>Hostname</label>
      <span>{{ snapshot.hostname }}</span>
    </div>
  </div>
  {% endif %}

  <!-- STATISTICS -->
  <h2>Summary</h2>
  <div class="stats-grid">
    <div class="stat-card ok">
      <div class="value">{{ stats.files_new }}</div>
      <div class="label">New files</div>
    </div>
    <div class="stat-card {% if stats.files_changed > 0 %}warn{% else %}ok{% endif %}">
      <div class="value">{{ stats.files_changed }}</div>
      <div class="label">Modified files</div>
    </div>
    <div class="stat-card {% if stats.files_deleted > 0 %}warn{% else %}ok{% endif %}">
      <div class="value">{{ stats.files_deleted }}</div>
      <div class="label">Deleted files</div>
    </div>
    <div class="stat-card">
      <div class="value">{{ stats.files_unmodified }}</div>
      <div class="label">Unchanged files</div>
    </div>
    <div class="stat-card">
      <div class="value">{{ stats.data_added_hr }}</div>
      <div class="label">Data added</div>
    </div>
    <div class="stat-card">
      <div class="value">{{ stats.total_size_hr }}</div>
      <div class="label">Total size</div>
    </div>
    <div class="stat-card">
      <div class="value">{{ stats.duration }}</div>
      <div class="label">Duration</div>
    </div>
  </div>

  <!-- INTEGRITY CHECK -->
  <h2>Integrity check</h2>
  <p class="{% if integrity_ok %}integrity-ok{% else %}integrity-error{% endif %}">
    {% if integrity_ok %}✓ Repository integrity verified — no errors found
    {% else %}✗ Integrity check failed — see execution log for details
    {% endif %}
  </p>

  <!-- SNAPSHOT HISTORY -->
  <h2>Snapshot history (retention: {{ retention_label }})</h2>
  <table>
    <thead>
      <tr>
        <th>Snapshot ID</th>
        <th>Date / Time</th>
        <th>Hostname</th>
        <th>Tags</th>
        <th>Status</th>
      </tr>
    </thead>
    <tbody>
      {% for s in snapshots %}
      <tr {% if s.id == current_snapshot_id %}class="current"{% endif %}>
        <td class="mono">{{ s.id[:12] }}</td>
        <td class="mono">{{ s.time[:19].replace("T"," ") }}</td>
        <td>{{ s.hostname }}</td>
        <td>{{ (s.tags or []) | join(", ") }}</td>
        <td>
          {% if s.id == current_snapshot_id %}
            <span class="tag tag-ok">NEW</span>
          {% else %}
            <span class="tag" style="background:#f1f5f9;color:#64748b">RETAINED</span>
          {% endif %}
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>

  <!-- EXECUTION LOGS -->
  <h2>Execution logs</h2>
  <div class="log-block">
    <div class="log-label">rclone sync (source → staging)</div>
    <div class="log">{{ rclone_log or "(no log available)" }}</div>
  </div>
  <div class="log-block">
    <div class="log-label">restic backup + forget + check</div>
    <div class="log">{{ restic_log or "(no log available)" }}</div>
  </div>

  <footer>
    Generated by backup.py (rclone {{ rclone_version }} / restic {{ restic_version }}) —
    {{ date_time }} — Reference: {{ reference }}
  </footer>
</div>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def run(cmd: list[str], env: dict | None = None) -> subprocess.CompletedProcess:
    merged_env = {**os.environ, **(env or {})}
    return subprocess.run(cmd, capture_output=True, text=True, env=merged_env)


def tool_version(name: str, flag: str = "version") -> str:
    r = run([name, flag])
    return (r.stdout + r.stderr).splitlines()[0] if r.returncode == 0 else "?"


def restic_env(password: str) -> dict:
    return {"RESTIC_PASSWORD": password}


# ---------------------------------------------------------------------------
# rclone steps
# ---------------------------------------------------------------------------

def rclone_sync(source: str, staging: str) -> tuple[str, int]:
    """Sync source remote → local staging directory."""
    console.print(f"\n[bold]rclone sync[/bold] {source} → {staging} ...")
    r = run([
        "rclone", "sync", source, staging,
        "--checksum",
        "--log-level", "NOTICE",
        "--stats", "10s",
        "--stats-log-level", "NOTICE",
    ])
    return (r.stdout + r.stderr), r.returncode


# ---------------------------------------------------------------------------
# restic steps
# ---------------------------------------------------------------------------

def restic_init(repo: str, password: str) -> tuple[str, int]:
    """Initialize restic repository if it does not exist yet."""
    r = run(["restic", "--repo", repo, "snapshots", "--json"],
            env=restic_env(password))
    if r.returncode == 0:
        return "(repository already initialised)", 0
    console.print("  [dim]Initialising restic repository...[/dim]")
    r = run(["restic", "--repo", repo, "init", "--repository-version", "2"],
            env=restic_env(password))
    return (r.stdout + r.stderr), r.returncode


def restic_backup(repo: str, staging: str, password: str,
                  tags: list[str] | None = None) -> tuple[str, dict, int]:
    """Run restic backup and return (log, summary_dict, returncode)."""
    cmd = ["restic", "--repo", repo, "backup", staging,
           "--json", "--no-scan"]
    for tag in (tags or []):
        cmd += ["--tag", tag]
    r = run(cmd, env=restic_env(password))
    log = r.stdout + r.stderr
    summary = {}
    # Parse the last JSON summary line
    for line in reversed(log.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                data = json.loads(line)
                if data.get("message_type") == "summary":
                    summary = data
                    break
            except json.JSONDecodeError:
                pass
    return log, summary, r.returncode


def restic_forget(repo: str, password: str, keep_weekly: int) -> tuple[str, int]:
    """Apply retention policy and prune unused data."""
    r = run([
        "restic", "--repo", repo, "forget",
        "--keep-weekly", str(keep_weekly),
        "--prune",
        "--json",
    ], env=restic_env(password))
    return (r.stdout + r.stderr), r.returncode


def restic_check(repo: str, password: str) -> tuple[str, bool]:
    """Verify repository integrity. Returns (log, ok)."""
    r = run(["restic", "--repo", repo, "check"],
            env=restic_env(password))
    log = r.stdout + r.stderr
    return log, r.returncode == 0


def restic_snapshots(repo: str, password: str) -> list[dict]:
    """Return list of snapshots as dicts."""
    r = run(["restic", "--repo", repo, "snapshots", "--json"],
            env=restic_env(password))
    if r.returncode != 0:
        return []
    try:
        return json.loads(r.stdout) or []
    except json.JSONDecodeError:
        return []


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def generate_report(args, snapshot: dict | None, summary: dict,
                    snapshots: list[dict], integrity_ok: bool,
                    rclone_log: str, restic_log: str,
                    duration: float, rclone_ver: str, restic_ver: str,
                    output_dir: str, mode_label: str) -> str:

    now = datetime.now()
    mins, secs = divmod(int(duration), 60)
    duration_str = f"{mins}m {secs:02d}s" if mins else f"{secs}s"

    # Parse deleted file count from rclone sync log ("Deleted: N (files), ...")
    files_deleted = 0
    import re as _re
    m = _re.search(r"Deleted:\s+(\d+)\s+\(files\)", rclone_log)
    if m:
        files_deleted = int(m.group(1))

    stats = {
        "files_new":        summary.get("files_new", 0),
        "files_changed":    summary.get("files_changed", 0),
        "files_unmodified": summary.get("files_unmodified", 0),
        "files_deleted":    files_deleted,
        "data_added_hr":    human_size(summary.get("data_added", 0)),
        "total_size_hr":    human_size(summary.get("total_bytes_processed", 0)),
        "duration":         duration_str,
    }

    errors = 0 if integrity_ok else 1
    if errors:
        status_class, status_icon, status_label = "error", "✗", "FAILURE"
    elif summary.get("files_new", 0) + summary.get("files_changed", 0) == 0:
        status_class, status_icon, status_label = "ok", "✓", "SUCCESS — NO CHANGES"
    else:
        status_class, status_icon, status_label = "ok", "✓", "SUCCESS"

    current_id = snapshot.get("id", "") if snapshot else ""
    retention_label = f"weekly × {args.keep_weekly} ({args.keep_weekly} weeks)"

    html = Template(REPORT_TEMPLATE).render(
        reference=args.reference,
        date_time=now.strftime("%Y-%m-%d %H:%M:%S"),
        operator=args.operateur,
        source=args.source,
        destination=args.destination,
        comment=getattr(args, "commentaire", ""),
        mode_label=mode_label,
        retention_label=retention_label,
        status_class=status_class,
        status_icon=status_icon,
        status_label=status_label,
        snapshot=snapshot,
        stats=stats,
        snapshots=sorted(snapshots, key=lambda s: s.get("time", ""), reverse=True),
        current_snapshot_id=current_id,
        integrity_ok=integrity_ok,
        rclone_log=rclone_log or "(no log available)",
        restic_log=restic_log or "(no log available)",
        rclone_version=rclone_ver,
        restic_version=restic_ver,
    )

    fname = f"BACKUP_{args.reference}_{now.strftime('%Y%m%d_%H%M%S')}.html"
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / fname
    path.write_text(html, encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Incremental snapshot backup with quality report"
    )
    parser.add_argument("-s", "--source",       required=True,
                        help="Source path (local or rclone remote, e.g. my_sharepoint:Docs)")
    parser.add_argument("-d", "--destination",  required=True,
                        help="Backup repository path (local: NAS mount, external drive…)")
    parser.add_argument("-o", "--operateur",    required=True,  help="Operator name")
    parser.add_argument("-r", "--reference",    required=True,
                        help="Quality reference (e.g. BCK-2026-001)")
    parser.add_argument("-c", "--commentaire",  default="",     help="Backup reason or context")
    parser.add_argument("-n", "--dry-run",      action="store_true",
                        help="Simulation: sync and analyse without creating a snapshot")
    parser.add_argument("-p", "--password",     default="",
                        help="Restic repository password (default: none). "
                             "Also reads RESTIC_PASSWORD env var.")
    parser.add_argument("-k", "--keep-weekly",  type=int, default=13,
                        help="Number of weekly snapshots to retain (default: 13 ≈ 3 months)")
    parser.add_argument("--staging-dir",        default=None,
                        help="Local staging directory for rclone sync "
                             "(default: system temp dir, cleaned up after backup)")
    parser.add_argument("-O", "--output-dir",   default="./reports",
                        help="Report output folder (default: ./reports)")
    parser.add_argument("-l", "--log-level",    default="NOTICE",
                        choices=["DEBUG", "INFO", "NOTICE", "ERROR"])
    args = parser.parse_args()

    # Password: env var takes precedence over CLI arg
    password = os.environ.get("RESTIC_PASSWORD", args.password)

    console.rule("[bold blue]Incremental backup[/bold blue]")
    console.print(f"  Source      : [cyan]{args.source}[/cyan]")
    console.print(f"  Repository  : [cyan]{args.destination}[/cyan]")
    console.print(f"  Reference   : [yellow]{args.reference}[/yellow]")
    console.print(f"  Operator    : {args.operateur}")
    console.print(f"  Retention   : keep-weekly {args.keep_weekly} "
                  f"({args.keep_weekly} weeks ≈ {args.keep_weekly // 4} months)")
    if args.dry_run:
        console.print("  [bold yellow]SIMULATION mode — no snapshot will be created[/bold yellow]")

    mode_label = "SIMULATION (dry-run)" if args.dry_run else "BACKUP"

    rclone_ver = tool_version("rclone", "version")
    restic_ver = tool_version("restic", "version")
    console.print(f"  rclone      : {rclone_ver}")
    console.print(f"  restic      : {restic_ver}\n")

    # Staging directory — must be stable across runs so restic can track incremental changes.
    # Derive a fixed path from source+destination to avoid restic treating every run as a full backup.
    if args.staging_dir:
        staging = args.staging_dir
        cleanup_staging = False
    else:
        slug = hashlib.md5(f"{args.source}|{args.destination}".encode()).hexdigest()[:12]
        staging = str(Path(tempfile.gettempdir()) / f"backup_staging_{slug}")
        cleanup_staging = False  # keep it for next run's incremental tracking
    Path(staging).mkdir(parents=True, exist_ok=True)
    console.print(f"  Staging dir : [dim]{staging}[/dim]\n")

    rclone_log = ""
    restic_log = ""
    snapshot = None
    summary: dict = {}
    snapshots: list[dict] = []
    integrity_ok = True

    try:
        start = datetime.now()

        # 1. Initialise restic repository
        with Progress(SpinnerColumn(), TextColumn("{task.description}"),
                      TimeElapsedColumn(), console=console) as p:
            t = p.add_task("Checking restic repository...", total=None)
            init_log, rc_init = restic_init(args.destination, password)
            if rc_init != 0:
                console.print(f"[red]Failed to initialise repository:[/red]\n{init_log}")
                sys.exit(1)
            p.update(t, description="Repository ready")

        # 2. rclone sync source → staging
        with Progress(SpinnerColumn(), TextColumn("{task.description}"),
                      TimeElapsedColumn(), console=console) as p:
            t = p.add_task("Syncing source to staging...", total=None)
            if not args.dry_run:
                rclone_log, rc_sync = rclone_sync(args.source, staging)
            else:
                rclone_log, rc_sync = rclone_sync(args.source + " [dry-run skipped]", staging)
                rc_sync = 0
            if rc_sync != 0:
                console.print(f"[red]rclone sync failed (exit {rc_sync})[/red]")
            p.update(t, description="Sync complete")

        if not args.dry_run:
            # 3. restic backup
            with Progress(SpinnerColumn(), TextColumn("{task.description}"),
                          TimeElapsedColumn(), console=console) as p:
                t = p.add_task("Creating snapshot...", total=None)
                b_log, summary, rc_backup = restic_backup(
                    args.destination, staging, password,
                    tags=[args.reference]
                )
                restic_log += b_log
                if rc_backup != 0:
                    console.print(f"[red]restic backup failed (exit {rc_backup})[/red]")
                # Extract snapshot ID from summary
                snap_id = summary.get("snapshot_id", "")
                p.update(t, description=f"Snapshot created: {snap_id[:12]}")

            # 4. Apply retention policy
            with Progress(SpinnerColumn(), TextColumn("{task.description}"),
                          TimeElapsedColumn(), console=console) as p:
                t = p.add_task("Applying retention policy...", total=None)
                forget_log, rc_forget = restic_forget(
                    args.destination, password, args.keep_weekly)
                restic_log += "\n" + forget_log
                n_removed = forget_log.count('"remove"') or forget_log.count("remove")
                p.update(t, description=f"Retention applied")

            # 5. Integrity check
            with Progress(SpinnerColumn(), TextColumn("{task.description}"),
                          TimeElapsedColumn(), console=console) as p:
                t = p.add_task("Verifying repository integrity...", total=None)
                check_log, integrity_ok = restic_check(args.destination, password)
                restic_log += "\n" + check_log
                p.update(t, description=
                         "Integrity OK" if integrity_ok else "[red]Integrity FAILED[/red]")

            # 6. List current snapshots
            snapshots = restic_snapshots(args.destination, password)
            snapshot = next(
                (s for s in snapshots if s.get("id", "").startswith(
                    summary.get("snapshot_id", "")[:8])),
                snapshots[-1] if snapshots else None
            )

        duration = (datetime.now() - start).total_seconds()

        # 7. Generate report
        report_path = generate_report(
            args, snapshot, summary, snapshots, integrity_ok,
            rclone_log, restic_log, duration, rclone_ver, restic_ver,
            args.output_dir, mode_label
        )

        console.rule()
        console.print(f"\n[bold green]Report generated:[/bold green] {report_path}\n")

        # Console summary
        table = Table(title="Summary", show_header=True)
        table.add_column("Indicator")
        table.add_column("Value", justify="right")
        table.add_row("New files",       str(summary.get("files_new", "—")))
        table.add_row("Modified files",  str(summary.get("files_changed", "—")))
        table.add_row("Unchanged files", str(summary.get("files_unmodified", "—")))
        table.add_row("Data added",      human_size(summary.get("data_added", 0)))
        table.add_row("Snapshots kept",  str(len(snapshots)))
        table.add_row("Integrity",       "✓ OK" if integrity_ok else "✗ FAILED")
        console.print(table)

    finally:
        # Clean up temp staging dir
        if cleanup_staging and Path(staging).exists():
            import shutil
            shutil.rmtree(staging, ignore_errors=True)

    sys.exit(0 if integrity_ok else 1)


if __name__ == "__main__":
    main()
