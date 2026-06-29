# Database Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate practices, logs, and sets data from `iron.db` to `chatfit.db`.

**Architecture:** Use SQLite `ATTACH DATABASE` to run an in-database migration via an executable `.sql` script, verifying counts afterward.

**Tech Stack:** SQLite3

---

### Task 1: Create and Run Migration Script

**Files:**
- Create: `migrate_db.sql`
- Modify: `chatfit.db`

- [ ] **Step 1: Write the migration SQL script**

```sql
ATTACH DATABASE 'iron.db' AS iron;

BEGIN TRANSACTION;

-- Migrate practices
INSERT INTO practices (id, name, type, created_at, active)
SELECT id, name, practice_type, created_at, active 
FROM iron.practices;

-- Migrate logs to training_sessions
INSERT INTO training_sessions (id, practice_id, created_at, note, warm_up, cool_down)
SELECT id, practice_id, logged_at, note, warm_up, cool_down 
FROM iron.logs;

-- Migrate sets to training_sets
INSERT INTO training_sets (id, training_session_id, set_number, weight, reps, distance, duration)
SELECT id, log_id, set_number, weight, reps, distance, duration 
FROM iron.sets;

COMMIT;

DETACH DATABASE iron;
```

- [ ] **Step 2: Execute the migration script**

Run: `sqlite3 chatfit.db < migrate_db.sql`
Expected: Silent output (success) or SQL errors if it fails.

- [ ] **Step 3: Verify the migration (Record counts)**

Run: `sqlite3 chatfit.db "SELECT 'practices:', count(*) FROM practices; SELECT 'sessions:', count(*) FROM training_sessions; SELECT 'sets:', count(*) FROM training_sets;"`
Expected: Non-zero counts for all tables matching `iron.db`.

- [ ] **Step 4: Cleanup script and commit**

```bash
rm migrate_db.sql
git add docs/superpowers/specs/2026-06-29-db-migration-design.md docs/superpowers/plans/2026-06-29-db-migration-plan.md
git commit -m "chore: migrate data from iron.db to chatfit.db"
```
