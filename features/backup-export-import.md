# Feature Proposal: Backup, Export & Import System

**Status:** Proposed
**Author:** Brain Agent Team
**Date:** 2026-03-20
**Effort:** ~5 days
**Priority:** Medium

---

## Problem Statement

Brain Agent accumulates valuable state over time: agent configurations, personality files
(soul.md), memories (markdown files indexed by QMD), chat history (SQLite), scheduled
tasks, installed skills, and MCP server configurations. This state is spread across
multiple files and databases with no unified way to:

1. **Back it up** — a disk failure, accidental deletion, or bad config change can
   destroy weeks of accumulated agent memory and configuration
2. **Migrate** — moving Brain Agent to a new machine means manually copying files,
   databases, and hoping nothing is missed
3. **Share agents** — no way to export a single agent (config + memories + skills)
   and share it with someone else
4. **Version control state** — no snapshots to roll back to after a bad change
5. **Disaster recovery** — no automated backups, no restore procedure

Current state is fragile:

```
agents/main/
  soul.md               <- hand-crafted, irreplaceable
  agent.json            <- configuration
  *.md                  <- memories accumulated over weeks
  skills/               <- installed and customized skills
  mcp.json              <- MCP connections
  chats.db              <- 2 weeks of chat history
  scheduler.db          <- scheduled tasks and history

config.json             <- provider URLs, API keys, server config
```

One `rm -rf agents/` or a corrupted SQLite file and everything is gone.

---

## Proposed Solution

A backup/export/import system that packages Brain Agent state into portable archives.
Supports full instance backup, per-agent export, and scheduled automatic backups.

### Archive Format

`.brain-backup.tar.gz` — standard gzip-compressed tar archive containing:

```
backup-2026-03-20T143052/
  manifest.json                      <- metadata, version, contents
  config.json                        <- server config (API keys REDACTED)
  agents/
    main/
      soul.md
      agent.json
      tools.md
      mcp.json
      skills/
        github/SKILL.md
        word-docx/SKILL.md
      memories/
        project_summary.md
        meeting-notes-2026-03.md
        ... (all .md files)
    Researcher/
      soul.md
      agent.json
      skills/
      memories/
        research-findings.md
        ...
    Reporter/
      soul.md
      agent.json
      memories/
        ...
  databases/
    chats.db                         <- full chat history
    scheduler.db                     <- scheduled tasks + history
  teams.json                         <- team structure snapshot
```

### Manifest Format

```json
{
  "version": "1.0",
  "brain_agent_version": "1.6.0",
  "created_at": "2026-03-20T14:30:52Z",
  "type": "full",
  "hostname": "alexs-macbook.local",
  "agents": [
    {
      "name": "main",
      "display_name": "Brain Agent",
      "memories": 12,
      "skills": 2,
      "chats": 47
    },
    {
      "name": "Researcher",
      "display_name": "Research Assistant",
      "memories": 8,
      "skills": 0,
      "chats": 15
    }
  ],
  "databases": {
    "chats": {"sessions": 62, "messages": 1843},
    "scheduler": {"tasks": 5, "history_entries": 234}
  },
  "teams": [
    {"name": "Research Team", "head": "Researcher", "members": ["crow"]}
  ],
  "total_files": 87,
  "total_size_bytes": 4521984,
  "config_redacted": true,
  "checksum": "sha256:a1b2c3d4..."
}
```

---

## Architecture

```
                        Backup/Restore Pipeline
                        =======================

  BACKUP:
  -------
  Web UI / TUI / API
         |
         v
  POST /v1/backup
  {type: "full" | "agent", agent: "name", include_keys: false}
         |
         v
  BackupManager.create_backup()
    1. Lock databases (WAL checkpoint)
    2. Collect files per manifest
    3. Redact API keys (unless include_keys=true)
    4. Copy SQLite DBs (safe snapshot via .backup() API)
    5. Generate manifest.json with checksums
    6. Create .tar.gz archive
    7. Return file path or stream
         |
         v
  Response: binary stream (.brain-backup.tar.gz)


  RESTORE:
  --------
  Web UI / TUI / API
         |
         v
  POST /v1/restore
  {file: <upload>, strategy: "merge" | "replace", dry_run: false}
         |
         v
  BackupManager.restore_backup()
    1. Extract archive to temp directory
    2. Validate manifest + checksum
    3. If dry_run: return preview (what will change)
    4. If strategy=merge:
       - Add new agents, skip existing
       - Merge memories (skip duplicates by filename)
       - Merge chat history (skip duplicate session IDs)
    5. If strategy=replace:
       - Backup current state first (safety net)
       - Replace all files and databases
    6. Re-register QMD collections for new/changed agents
    7. Restart server to pick up changes
         |
         v
  Response: {restored: true, agents: [...], conflicts: [...]}
```

---

## Web UI Mockups

### Backup Section in Settings

```
+--------------------------------------------------------------------------+
|  Settings                                                         [x]    |
+--------------------------------------------------------------------------+
|  [Server] [QMD] [Models] [Telegram] [Providers] [Notifications]         |
|  [*Backup*]                                                              |
+--------------------------------------------------------------------------+
|                                                                          |
|  Backup & Restore                                                        |
|  ================================================================        |
|                                                                          |
|  --- Export ---                                                           |
|                                                                          |
|  Full Instance Backup                                                    |
|  Exports all agents, memories, chat history, schedules, and config.      |
|                                                                          |
|  [ ] Include API keys and credentials                                    |
|  [  Export All  ]                                                        |
|                                                                          |
|  Per-Agent Export                                                         |
|  +----------------------------------------------------------------+     |
|  | Agent            | Memories | Skills | Chats |                 |     |
|  |------------------+----------+--------+-------+-----------------|     |
|  | main             |    12    |   2    |  47   | [Export]        |     |
|  | Researcher       |     8    |   0    |  15   | [Export]        |     |
|  | Reporter         |     5    |   1    |   8   | [Export]        |     |
|  | crow             |     3    |   0    |   4   | [Export]        |     |
|  +----------------------------------------------------------------+     |
|                                                                          |
|  --- Import ---                                                          |
|                                                                          |
|  +- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -+       |
|  |                                                               |       |
|  |        Drop .brain-backup.tar.gz here to import              |       |
|  |                                                               |       |
|  |        or  [Choose File]                                      |       |
|  |                                                               |       |
|  +- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -+       |
|                                                                          |
|  --- Automatic Backups ---                                               |
|                                                                          |
|  [x] Enable scheduled backups                                            |
|  Schedule: [daily v] at [02:00]                                          |
|  Backup path: [/Volumes/Backup/brain-agent/          ]                   |
|  Keep last: [7  ] backups                                                |
|  [ ] Include API keys                                                    |
|  [Save Auto-Backup Settings]                                             |
|                                                                          |
|  Last backup: 2026-03-20 02:00:03 (4.3 MB)                              |
|  Next backup: 2026-03-21 02:00:00                                        |
|                                                                          |
+--------------------------------------------------------------------------+
```

### Import Preview Dialog

```
+--------------------------------------------------------------+
|  Import Backup                                         [x]   |
+--------------------------------------------------------------+
|                                                              |
|  File: backup-2026-03-15T020003.brain-backup.tar.gz          |
|  Created: 2026-03-15 02:00:03                                |
|  Brain Agent Version: 1.5.3                                  |
|  Source: alexs-macbook.local                                 |
|  Size: 4.3 MB                                                |
|                                                              |
|  --- Contents ---                                            |
|                                                              |
|  Agents:                                                     |
|    [x] main        12 memories, 2 skills, 47 chats          |
|    [x] Researcher   8 memories, 0 skills, 15 chats          |
|    [x] Reporter     5 memories, 1 skill,   8 chats          |
|    [ ] crow          3 memories, 0 skills,  4 chats          |
|                                                              |
|  Databases:                                                  |
|    [x] Chat history  (62 sessions, 1,843 messages)           |
|    [x] Scheduler     (5 tasks, 234 history entries)          |
|                                                              |
|  Config:                                                     |
|    [x] Server config (API keys redacted)                     |
|    [x] Team structure                                        |
|                                                              |
|  --- Conflicts ---                                           |
|                                                              |
|  [!] Agent "main" already exists                             |
|      ( ) Skip  (*) Merge memories  ( ) Replace               |
|                                                              |
|  [!] Agent "Researcher" already exists                       |
|      ( ) Skip  (*) Merge memories  ( ) Replace               |
|                                                              |
|  [!] 12 chat sessions already exist                          |
|      (*) Skip duplicates  ( ) Replace all                    |
|                                                              |
|  Strategy: [Merge (recommended) v]                           |
|                                                              |
|                              [Cancel]  [Preview Changes]     |
|                                                 [Import]     |
+--------------------------------------------------------------+
```

### Import Confirmation (after Preview)

```
+--------------------------------------------------------------+
|  Import Preview                                        [x]   |
+--------------------------------------------------------------+
|                                                              |
|  The following changes will be made:                         |
|                                                              |
|  Agents:                                                     |
|    main        -> merge 3 new memories, skip 9 existing      |
|    Researcher  -> merge 2 new memories, skip 6 existing      |
|    Reporter    -> merge 5 new memories (all new)             |
|                                                              |
|  Databases:                                                  |
|    Chats       -> import 24 new sessions, skip 38 existing   |
|    Scheduler   -> import 2 new tasks, skip 3 existing        |
|                                                              |
|  Config:                                                     |
|    Server config -> skip (API keys redacted in backup)       |
|    Teams -> update team structure                             |
|                                                              |
|  A safety backup of current state will be created first.     |
|                                                              |
|                                     [Cancel]  [Confirm]      |
+--------------------------------------------------------------+
```

---

## TUI Commands

```
> /backup
Creating full backup...
  Collecting agents: main, Researcher, Reporter, crow
  Copying databases: chats.db (2.1 MB), scheduler.db (48 KB)
  Generating manifest...
  Compressing...

Backup saved: /Users/alexander/Documents/dev/cctest/backups/
  backup-2026-03-20T143052.brain-backup.tar.gz (4.3 MB)
  4 agents, 28 memories, 62 sessions, 5 scheduled tasks

> /backup agent Researcher
Creating agent backup for Researcher...

Backup saved: /Users/alexander/Documents/dev/cctest/backups/
  researcher-2026-03-20T143112.brain-backup.tar.gz (312 KB)
  8 memories, 0 skills, 15 sessions

> /restore /path/to/backup-2026-03-15.brain-backup.tar.gz
Analyzing backup...

  Source: alexs-macbook.local (2026-03-15 02:00)
  Agents: main (12 mem), Researcher (8 mem), Reporter (5 mem)
  Chats: 62 sessions, 1843 messages
  Scheduler: 5 tasks

  Conflicts:
    main       -> exists (merge memories)
    Researcher -> exists (merge memories)

  Strategy: merge (skip duplicates)

Proceed? [y/N]: y

  Creating safety backup... done
  Importing agents... 10 new memories added
  Importing chats... 24 new sessions imported
  Importing scheduler... 2 new tasks imported
  Re-indexing QMD collections... done

Restore complete. Server restart recommended: /restart

> /backup list
Saved backups:
  2026-03-20 14:30  full       4.3 MB  (4 agents, 28 memories)
  2026-03-20 14:31  agent      312 KB  (Researcher)
  2026-03-20 02:00  full/auto  4.1 MB  (4 agents, 26 memories)
  2026-03-19 02:00  full/auto  3.9 MB  (4 agents, 24 memories)
```

---

## API Endpoints

### POST /v1/backup

Create and download a backup archive.

**Request:**
```json
{
  "type": "full",
  "include_keys": false
}
```

Or for single agent:
```json
{
  "type": "agent",
  "agent": "Researcher",
  "include_chats": true
}
```

**Response:** Binary stream with `Content-Type: application/gzip` and
`Content-Disposition: attachment; filename="backup-2026-03-20T143052.brain-backup.tar.gz"`

### POST /v1/restore

Upload and restore a backup archive.

**Request:** `multipart/form-data` with:
- `file`: the .brain-backup.tar.gz file
- `strategy`: "merge" or "replace" (default: "merge")
- `dry_run`: "true" or "false" (default: "true")
- `agents`: JSON array of agent names to import (default: all)

**Response (dry_run=true):**
```json
{
  "valid": true,
  "manifest": { "...manifest contents..." },
  "changes": {
    "agents": {
      "main": {"action": "merge", "new_memories": 3, "skip_memories": 9},
      "Researcher": {"action": "merge", "new_memories": 2, "skip_memories": 6},
      "Reporter": {"action": "create", "new_memories": 5}
    },
    "chats": {"new_sessions": 24, "skip_sessions": 38},
    "scheduler": {"new_tasks": 2, "skip_tasks": 3}
  },
  "conflicts": [
    {"type": "agent_exists", "agent": "main"},
    {"type": "agent_exists", "agent": "Researcher"}
  ]
}
```

**Response (dry_run=false):**
```json
{
  "restored": true,
  "safety_backup": "backups/pre-restore-2026-03-20T144500.brain-backup.tar.gz",
  "imported": {
    "agents": ["main", "Researcher", "Reporter"],
    "memories": 10,
    "sessions": 24,
    "tasks": 2
  },
  "restart_needed": true
}
```

### GET /v1/backup/list

List available backups in the backup directory.

**Response:**
```json
{
  "backups": [
    {
      "filename": "backup-2026-03-20T143052.brain-backup.tar.gz",
      "created_at": "2026-03-20T14:30:52Z",
      "type": "full",
      "size_bytes": 4521984,
      "agents": ["main", "Researcher", "Reporter", "crow"],
      "auto": false
    }
  ],
  "auto_backup": {
    "enabled": true,
    "schedule": "daily 02:00",
    "path": "/Volumes/Backup/brain-agent/",
    "keep_last": 7,
    "last_run": "2026-03-20T02:00:03Z",
    "next_run": "2026-03-21T02:00:00Z"
  }
}
```

### POST /v1/backup/config

Configure automatic backups.

**Request:**
```json
{
  "enabled": true,
  "schedule": "daily 02:00",
  "path": "/Volumes/Backup/brain-agent/",
  "keep_last": 7,
  "include_keys": false
}
```

---

## Workflows

### Workflow 1: Manual Full Backup

1. Admin opens Settings > Backup tab in Web UI
2. Unchecks "Include API keys" (recommended for sharing/cloud storage)
3. Clicks "Export All"
4. Browser downloads `backup-2026-03-20T143052.brain-backup.tar.gz` (4.3 MB)
5. Admin copies to external drive / cloud storage
6. Archive contains manifest.json for easy inspection without extracting

### Workflow 2: Migration to New Machine

1. On old machine: create full backup with "Include API keys" checked
2. Transfer `.brain-backup.tar.gz` to new machine (USB, SCP, cloud)
3. On new machine: install Brain Agent, start server
4. Open Web UI > Settings > Backup > Import
5. Drop the backup file into the import zone
6. Preview shows all agents, memories, chats, schedules
7. Select strategy: "Replace" (fresh machine, no conflicts)
8. Click Import — everything restored
9. Server restarts, QMD re-indexes all collections
10. Verify: all agents present, chat history intact, schedules running

### Workflow 3: Share Single Agent

1. Admin has a well-configured "Researcher" agent with 8 memories and custom skills
2. Clicks "Export" next to Researcher in the per-agent table
3. Downloads `researcher-2026-03-20.brain-backup.tar.gz` (312 KB)
4. Sends to colleague via email/Slack
5. Colleague opens their Brain Agent, goes to Settings > Backup > Import
6. Drops the file — preview shows: "1 agent: Researcher (8 memories, 0 skills)"
7. No conflicts (they don't have a Researcher agent)
8. Clicks Import — Researcher agent created with all memories
9. QMD creates new collection and indexes the memories

### Workflow 4: Automatic Nightly Backup

1. Admin configures auto-backup in Settings > Backup:
   - Schedule: daily at 02:00
   - Path: `/Volumes/Backup/brain-agent/`
   - Keep last: 7
   - Exclude API keys
2. Server registers backup task in scheduler
3. Every night at 02:00, backup runs automatically
4. Older backups beyond the keep-last count are deleted
5. If backup path is unreachable (external drive disconnected), fires a
   notification via the notification system (see notifications.md)
6. Admin can see last backup status in the Backup settings tab

---

## What to Exclude

### Always Excluded

| Path / Pattern        | Reason                                         |
|-----------------------|------------------------------------------------|
| `*.pyc`, `__pycache__`| Python bytecode, regenerated automatically     |
| `.git/`              | Version control, not part of agent state        |
| `agents/*/uploads/`  | Image uploads (too large, optional include)     |
| `*.log`              | Log files, transient                            |
| `node_modules/`      | JS dependencies, if any                         |

### Conditionally Excluded

| Path / Pattern        | Include When                                    |
|-----------------------|-------------------------------------------------|
| `config.json` keys   | User checks "Include API keys"                  |
| `gmail.json`          | User checks "Include credentials"               |
| `*.db-wal`, `*.db-shm`| Never — WAL is checkpointed before backup      |
| QMD index             | User checks "Include search index" (can rebuild)|
| `agents/*/uploads/`  | User checks "Include uploaded images"            |

### API Key Redaction

When `include_keys: false` (default), the backup process:

1. Copies `config.json` to temp location
2. Replaces all `api_key` values with `"REDACTED"`
3. Replaces all `app_password` values in `gmail.json` with `"REDACTED"`
4. Replaces webhook URLs with `"REDACTED"` (they contain secrets)
5. Includes a `config_redacted: true` flag in manifest
6. On restore: if config is redacted, skip config import and warn user

```
+----------------------------------------------+
|  Warning: Imported backup has redacted        |
|  API keys. You will need to re-enter your     |
|  provider credentials in Settings.            |
|                                               |
|  [OK]                                         |
+----------------------------------------------+
```

---

## Conflict Resolution

### Merge Strategy (Default)

When importing into an instance that already has data:

| Conflict Type          | Resolution                                       |
|------------------------|--------------------------------------------------|
| Agent exists           | Keep existing config, merge new memories          |
| Memory file exists     | Skip (same filename = same content assumed)       |
| Memory file differs    | Import as `filename_imported_20260320.md`         |
| Chat session exists    | Skip (match by session ID)                       |
| Chat session new       | Import into existing chats.db                    |
| Scheduled task exists  | Skip (match by task name + agent)                |
| Skill exists           | Skip (keep existing version)                     |
| MCP config differs     | Keep existing, log difference                    |

### Replace Strategy

| Conflict Type          | Resolution                                       |
|------------------------|--------------------------------------------------|
| Any existing data      | Create safety backup first, then overwrite       |
| All agents             | Delete existing, restore from backup             |
| Databases              | Replace entirely                                 |
| Config                 | Replace (warn about API keys if redacted)        |

---

## Incremental vs Full Backups

### Phase 1: Full Backups Only

Every backup contains complete state. Simple, reliable, easy to restore.

- Pros: Self-contained, any single backup is a complete restore point
- Cons: Redundant data, larger files over time

### Phase 2: Incremental Backups (Future)

Track changes since last backup using file modification times.

```json
{
  "type": "incremental",
  "base_backup": "backup-2026-03-19T020003",
  "changes": {
    "added": ["agents/Researcher/new-finding.md"],
    "modified": ["agents/main/agent.json"],
    "deleted": []
  }
}
```

Restore requires base + all incrementals in order. More complex but much smaller.

---

## Implementation Plan

### Day 1: Backup Engine

- Create `BackupManager` class (in `claude_cli.py` or separate module)
- Implement `create_backup()`: collect files, snapshot DBs, generate manifest
- SQLite safe copy using `connection.backup()` API (WAL checkpoint first)
- API key redaction logic
- Checksum generation (SHA-256 of archive)
- `.tar.gz` archive creation with correct directory structure

### Day 2: Restore Engine

- Implement `restore_backup()`: extract, validate, conflict detection
- Dry-run mode: analyze and report without modifying anything
- Merge strategy: selective import with dedup
- Replace strategy: safety backup + full overwrite
- QMD collection re-registration after restore

### Day 3: API Endpoints & Auto-Backup

- `POST /v1/backup` — create and stream backup
- `POST /v1/restore` — upload and restore
- `GET /v1/backup/list` — list available backups
- `POST /v1/backup/config` — configure auto-backup
- Integrate auto-backup with existing scheduler
- Retention policy (delete old backups beyond keep_last)

### Day 4: Web UI

- Backup tab in Settings dashboard
- Export All button with options
- Per-agent export buttons
- Import drop zone and file picker
- Import preview dialog with conflict resolution
- Auto-backup configuration form
- Backup history list

### Day 5: TUI & Testing

- `/backup`, `/backup agent <name>`, `/backup list` commands
- `/restore <path>` command with interactive conflict resolution
- Test full backup/restore cycle
- Test per-agent export/import
- Test merge vs replace strategies
- Test redaction and credential handling
- Test with large databases (1000+ sessions)
- Test interrupted restore (safety backup should protect)

---

## Benefits

1. **Disaster recovery** — always have a restore point
2. **Easy migration** — move to new machine in minutes
3. **Agent sharing** — export and share individual agents
4. **Peace of mind** — automated nightly backups
5. **Version snapshots** — roll back after bad changes
6. **Portable format** — standard tar.gz, inspectable without Brain Agent

---

## Risks & Mitigations

| Risk                                 | Mitigation                                      |
|--------------------------------------|-------------------------------------------------|
| Backup during active writes          | WAL checkpoint + SQLite backup API              |
| Large backup files                   | Compression, exclude uploads by default         |
| Restore overwrites good data         | Safety backup created before every restore      |
| API keys leaked in backup            | Redacted by default, clear warning when enabled |
| Backup path fills up                 | Retention policy, disk space check before backup|
| Incompatible versions                | Version field in manifest, migration logic      |
| Corrupted archive                    | SHA-256 checksum validation on restore          |
| Concurrent backup/restore            | Lock file prevents simultaneous operations      |

---

## config.json Addition

```json
{
  "backup": {
    "auto": {
      "enabled": true,
      "schedule": "daily 02:00",
      "path": "/Volumes/Backup/brain-agent/",
      "keep_last": 7,
      "include_keys": false,
      "include_uploads": false
    }
  }
}
```

---

## File Changes Summary

| File              | Changes                                                    |
|-------------------|------------------------------------------------------------|
| `server.py`       | Backup/restore API endpoints, streaming download           |
| `claude_cli.py`   | BackupManager class, scheduler integration for auto-backup |
| `web/index.html`  | Backup tab in Settings, export/import UI, auto-backup form |
| `tui.py`          | /backup and /restore commands                              |
| `config.json`     | backup section for auto-backup config                      |

---

## Open Questions

1. Should backups be encrypted? (Phase 2 — AES-256 with user password)
2. Should we support cloud backup destinations (S3, GCS)? (Phase 2)
3. Maximum backup file size before warning? (Default 100 MB warning threshold)
4. Should restore trigger a full QMD re-embed? (Yes, memories may have changed)
5. Should we support selective restore (pick specific agents from a full backup)? (Yes, via the `agents` parameter in the restore API and checkboxes in the preview dialog)
