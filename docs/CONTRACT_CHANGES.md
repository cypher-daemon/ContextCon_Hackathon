# Contract Changes

Append one entry per change to a shared contract (`schemas.py`, `context.py`, `flags.py`, `policy_config.py`, `db/schema.sql`, config keys, CLI, output fields). Lane A applies the change in one `contract:` commit; the other lanes pull before relying on it. Additive changes are fine; renames and removals need every human's agreement.

## YYYY-MM-DD: short title
- **Requested by (lane):**
- **Reason:**
- **Change:** models / enums / signatures / DB / config / CLI / output
- **Affected modules and tests:**
- **Status:** requested / applied in `<commit>`
