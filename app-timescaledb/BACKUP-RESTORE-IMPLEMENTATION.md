# Backup and Restore Implementation Summary

## Overview

This implementation adds a resilient backup and restore mechanism for the TimescaleDB addon that addresses the problem of backing up databases while they're running.

## Changes Made

### 1. Configuration (`timescaledb/config.yaml`)

Added Home Assistant backup lifecycle hooks:

```yaml
backup_pre: /usr/share/timescaledb/backup_pre.sh
backup_post: /usr/share/timescaledb/backup_post.sh
backup_exclude:
  - /data/postgres/*
```

These hooks ensure that:

- Before backup: A SQL dump is created
- After backup: The SQL dump is cleaned up
- During backup: The PostgreSQL data directory is excluded (only the SQL dump is backed up)

### 2. Pre-Backup Script (`backup_pre.sh`)

**Location:** `/usr/share/timescaledb/backup_pre.sh`

**Functionality:**

- Checks if PostgreSQL is running
- Executes `pg_dumpall` to create a complete SQL dump
- Creates the file `/data/backup_db.sql`
- Sets proper permissions
- Logs the backup size for verification
- Gracefully handles cases where PostgreSQL isn't running

**Key Features:**

- Uses `pg_isready` to check PostgreSQL status
- Runs as the postgres user
- Includes `--clean --if-exists` flags for safe restore
- Won't fail the backup if database isn't running

### 3. Post-Backup Script (`backup_post.sh`)

**Location:** `/usr/share/timescaledb/backup_post.sh`

**Functionality:**

- Removes the temporary SQL dump file after backup completes
- Saves disk space
- Logs cleanup status

### 4. Restore Script (`restore_from_backup.sh`)

**Location:** `/usr/share/timescaledb/restore_from_backup.sh`

**Functionality:**

- Provides `restoreFromBackup()` function
- Starts PostgreSQL temporarily
- Waits for PostgreSQL to be ready (with timeout)
- Restores database from SQL dump using `psql`
- Logs detailed progress and any errors
- Cleans up the backup file after successful restore
- Stops PostgreSQL cleanly after restore

**Key Features:**

- Comprehensive error handling
- Progress logging for user visibility
- Retry logic with timeout for PostgreSQL startup
- Preserves backup file if restore fails (for manual recovery)
- Creates detailed restore log at `/var/log/timescaledb.restore.log`

### 5. Initialization Script Updates (`init-addon/run`)

**Enhanced Logic:**

1. **Fresh Installation with Backup:**
   - Detects if `backup_db.sql` exists on new install
   - Enables restore mode
   - Initializes fresh database
   - Automatically restores from SQL dump

2. **Corrupted Database Detection:**
   - Checks if PostgreSQL data directory is corrupted
   - Detects missing `PG_VERSION` file
   - If backup exists, moves corrupted data aside
   - Initializes fresh database and restores

3. **Automatic Recovery:**
   - Restores silently without user intervention
   - Skips firstrun setup after successful restore
   - Preserves backup file if restore fails

**New Variables:**

- `BACKUP_FILE`: Path to SQL dump file
- `RESTORE_MODE`: Flag indicating restore should occur

### 6. Dockerfile Updates

Added execution permissions for the new scripts:

```dockerfile
RUN chmod +x /usr/share/timescaledb/backup_pre.sh \
    && chmod +x /usr/share/timescaledb/backup_post.sh \
    && chmod +x /usr/share/timescaledb/restore_from_backup.sh
```

### 7. Documentation (`README.md`)

Added comprehensive "Backup and Restore" section covering:

- How backups work
- How restore works
- Manual backup procedures
- Troubleshooting guide
- Important notes and caveats

## How It Works

### Backup Flow

```
User triggers HA backup
    ↓
backup_pre.sh runs
    ↓
pg_dumpall creates /data/backup_db.sql
    ↓
HA backs up /data/* (excluding /data/postgres/*)
    ↓
backup_post.sh runs
    ↓
backup_db.sql is removed
    ↓
Backup complete
```

### Restore Flow

```
User restores HA backup
    ↓
Addon starts with backup_db.sql
    ↓
init-addon/run detects restore scenario
    ↓
Initializes fresh PostgreSQL database
    ↓
restore_from_backup.sh runs
    ↓
Starts PostgreSQL temporarily
    ↓
Restores from SQL dump
    ↓
Stops PostgreSQL
    ↓
Removes backup_db.sql
    ↓
Normal startup continues
```

## Benefits

1. **Consistency:** SQL dumps are transaction-consistent snapshots
2. **Safety:** No risk of backing up corrupted files
3. **Portability:** Can restore across PostgreSQL versions
4. **Size:** Excludes large data directory, only backs up SQL
5. **Automatic:** No user intervention required
6. **Resilient:** Handles corrupted databases automatically
7. **Recoverable:** Preserves backup file if restore fails

## Testing Recommendations

1. **Test normal backup/restore:**
   - Create some test data
   - Trigger Home Assistant backup
   - Delete database or corrupt it
   - Restore from backup
   - Verify all data is restored

2. **Test with PostgreSQL not running:**
   - Stop PostgreSQL
   - Trigger backup
   - Verify graceful handling

3. **Test corrupted database recovery:**
   - Corrupt `PG_VERSION` file
   - Place a valid `backup_db.sql` in /data/
   - Restart addon
   - Verify automatic recovery

4. **Test fresh install with backup:**
   - Delete PostgreSQL data directory
   - Place a valid `backup_db.sql` in /data/
   - Start addon
   - Verify restoration occurs

## Future Enhancements

Possible improvements:

- Add configuration option for backup retention
- Support for compressed SQL dumps
- Incremental backup support
- Backup verification/testing
- Email notifications on backup/restore events

## Compliance with Agent Guidelines

This implementation follows the AGENTS.md guidelines:

✅ Uses `bashio::log.*` for all logging
✅ Quotes all variables properly
✅ Includes comprehensive error handling
✅ Documents non-obvious logic
✅ Uses meaningful variable names
✅ Follows existing project patterns
✅ Maintains backward compatibility
✅ Adds user-facing documentation
✅ Uses `#!/command/with-contenv bashio` shebang
✅ Handles edge cases gracefully
