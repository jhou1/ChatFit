# Migration of iron.db to chatfit.db

## Goal
Migrate training data (practices, logs, sets) from `iron.db` to the new `chatfit.db` schema using SQLite's native `ATTACH DATABASE` functionality.

## Approach
**Pure SQL Migration**: We will connect to `chatfit.db`, attach `iron.db` as an external database, and execute `INSERT INTO ... SELECT` queries to move the data while renaming the columns to match the new schema. 

## Mappings
1. **Practices**: 
   - `id` -> `id`
   - `name` -> `name`
   - `practice_type` -> `type`
   - `created_at` -> `created_at`
   - `active` -> `active`

2. **Training Sessions (Logs)**:
   - `id` -> `id`
   - `practice_id` -> `practice_id`
   - `logged_at` -> `created_at`
   - `note` -> `note`
   - `warm_up` -> `warm_up`
   - `cool_down` -> `cool_down`
   - (rpe left as NULL)

3. **Training Sets (Sets)**:
   - `id` -> `id`
   - `log_id` -> `training_session_id`
   - `set_number` -> `set_number`
   - `weight` -> `weight`
   - `reps` -> `reps`
   - `distance` -> `distance`
   - `duration` -> `duration`

## Data Safety
We will verify the record counts before and after the migration to ensure all data has been successfully ported. Since `chatfit.db` is currently empty, we will preserve the exact IDs from `iron.db` to maintain referential integrity.
