# Incremental Snapshot Backup

Automated incremental backup system with snapshot history and quality report.  
Designed for weekly backup of SharePoint content to a local NAS or external drive.

**Engine:** [rclone](https://rclone.org/) (cloud sync) + [restic](https://restic.net/) (snapshot management)  
**Report:** self-contained HTML file, same format as the [file-transfer](https://github.com/Aspivix/file-transfer) project

---

## How It Works

```
SharePoint (rclone remote)
        │
        │  rclone sync  (1)
        ▼
Local staging directory  ──►  restic backup  (2)  ──►  Backup repository
                                                         (NAS / external drive)
```

1. **rclone sync** — downloads only changed files from the source remote into a fixed local staging directory.
2. **restic backup** — takes an incremental snapshot of the staging directory. Only new or modified blocks are stored; unchanged data is deduplicated.

The staging directory is derived deterministically from the source and destination paths so that restic can track changes (new, modified, deleted, unchanged files) across runs. It is kept between backups and never deleted automatically.

Retention policy (`--keep-weekly 13` by default) keeps one snapshot per week for ~3 months, then automatically prunes older ones.

---

## Prerequisites

| Tool | Install |
|------|---------|
| Python 3.8+ | system package |
| rclone | `curl https://rclone.org/install.sh \| sudo bash` |
| restic | `sudo apt install restic` or [restic.net/docs/installation](https://restic.net/docs/stable/020_installation.html) |
| python3-jinja2 | `sudo apt install python3-jinja2` |
| python3-rich | `sudo apt install python3-rich` |

Configure your rclone remotes before running (`rclone config`).

---

## Usage

```bash
python3 backup.py -s <source> -d <destination> -o <operator> -r <reference> [options]
```

### Parameters

| Short | Long | Required | Description |
|-------|------|----------|-------------|
| `-s` | `--source` | Yes | Source path — local path or rclone remote (e.g. `my_sharepoint:Docs`) |
| `-d` | `--destination` | Yes | Backup repository path — local mount (NAS, external drive) |
| `-o` | `--operateur` | Yes | Operator name (recorded in the report) |
| `-r` | `--reference` | Yes | Quality reference identifier (e.g. `BCK-2026-001`) |
| `-c` | `--commentaire` | No | Backup reason or context (free text) |
| `-n` | `--dry-run` | No | Simulation: sync and analyse without creating a snapshot |
| `-p` | `--password` | No | Restic repository password (default: none — no password). Also reads `RESTIC_PASSWORD` env var. |
| `-k` | `--keep-weekly` | No | Number of weekly snapshots to retain (default: `13` ≈ 3 months) |
| | `--staging-dir` | No | Override the staging directory (default: auto-derived from source + destination) |
| `-O` | `--output-dir` | No | Report output folder (default: `./reports`) |
| `-l` | `--log-level` | No | rclone log verbosity: `DEBUG`, `INFO`, `NOTICE`, `ERROR` (default: `NOTICE`) |

The `RESTIC_PASSWORD` environment variable takes precedence over `-p` if set.

### Examples

**First backup — SharePoint to external drive:**
```bash
python3 backup.py \
  -s my_sharepoint:Documents \
  -d /mnt/backup/aspivix \
  -o "Armand Polmard" \
  -r BCK-2026-001 \
  -c "Initial weekly backup"
```

**Dry-run simulation:**
```bash
python3 backup.py \
  -s my_sharepoint:Documents \
  -d /mnt/backup/aspivix \
  -o "Armand Polmard" \
  -r BCK-2026-001 \
  -n
```

**Custom retention (6 months):**
```bash
python3 backup.py \
  -s my_sharepoint:Documents \
  -d /mnt/nas/aspivix \
  -o "Armand Polmard" \
  -r BCK-2026-002 \
  -k 26
```

**With password protection:**
```bash
export RESTIC_PASSWORD="my_secure_password"
python3 backup.py -s my_sharepoint:Documents -d /mnt/backup/aspivix -o "Armand Polmard" -r BCK-2026-001
```

---

## Report

The script generates a self-contained HTML report in `./reports/` (or the folder specified by `-O`):

```
BACKUP_BCK-2026-001_20260414_083000.html
```

The report contains:

| Section | Content |
|---------|---------|
| Header | Reference, operator, date/time, source, destination, overall status |
| Snapshot | Restic snapshot ID, timestamp, hostname |
| Statistics | New files, modified files, deleted files, unchanged files, data added, total size, duration |
| Integrity check | Result of `restic check` on the repository |
| Snapshot history | Table of all retained snapshots with IDs, dates, and tags |
| Execution logs | rclone sync log (scrollable) + restic backup/forget/check log (scrollable) |

### Status levels

| Status | Meaning |
|--------|---------|
| **SUCCESS** | Snapshot created, integrity check passed, no errors |
| **SUCCESS — NO CHANGES** | Snapshot created, source unchanged since last backup |
| **FAILURE** | rclone sync or restic backup failed — no snapshot was created |
| **SIMULATION** | Dry-run mode — no snapshot created, report shows what would have been backed up |

### Deleted files counter

The "Deleted files" stat reflects files removed from the source since the last backup, as reported by `rclone sync`. It is parsed from the rclone log line `Deleted: N (files)`.

---

## Snapshot Management

Restic stores backups as an opaque deduplicated repository — **files are not directly accessible** on disk. The repository contains encrypted, chunked blocks indexed by content hash.

- **Incremental:** only changed file blocks are stored — subsequent backups are fast and space-efficient.
- **Deduplication:** identical blocks across snapshots are stored only once.
- **Retention:** `--keep-weekly 13` keeps the most recent snapshot per calendar week, up to 13 weeks (~3 months). Older snapshots are pruned automatically.
- **No encryption by default:** no password is set unless `-p` or `RESTIC_PASSWORD` is provided.

To list all existing snapshots manually:
```bash
RESTIC_PASSWORD="" restic -r /mnt/backup/aspivix snapshots
```

To restore a snapshot to a local directory:
```bash
RESTIC_PASSWORD="" restic -r /mnt/backup/aspivix restore <snapshot-id> --target /tmp/restore
```

> Restic restores files with their full staging path (e.g. `/tmp/restore/tmp/backup_staging_<hash>/`). To push a restored snapshot back to SharePoint, use rclone:
> ```bash
> rclone sync "/tmp/restore/tmp/backup_staging_<hash>/" "my_sharepoint:Documents" --progress
> ```

---

## Weekly Scheduling

To run the backup automatically every Monday at 06:00, add a cron job:

```bash
crontab -e
```

```
0 6 * * 1 cd /home/armand/Dev/Aspivix/backup && python3 backup.py \
  -s my_sharepoint:Documents \
  -d /mnt/backup/aspivix \
  -o "Scheduled" \
  -r "BCK-$(date +\%Y-\%V)" \
  -O /home/armand/Dev/Aspivix/backup/reports \
  >> /var/log/aspivix_backup.log 2>&1
```

---

## Project Structure

```
backup/
├── backup.py          # Main backup script
├── README.md          # This file
├── requirements.txt   # Python dependencies
└── reports/           # Generated HTML reports (auto-created)
```

---

## Dependencies

See [requirements.txt](requirements.txt).

External binaries: `rclone`, `restic`.
