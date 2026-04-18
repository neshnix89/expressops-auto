# ExpressOPS Automation

Modular automation suite for Express Operations NPI at Pepperl+Fuchs Singapore.

## Quick Start (Company Laptop)

```
git clone <repo-url> expressops-auto
cd expressops-auto
scripts\setup_env.bat
```

Edit `config\config.yaml` with your credentials, then:

```
ops list              — see available tasks
ops test <task>       — run in mock mode (safe)
ops run <task>        — run against live systems
ops status            — see last run results
```

## Development (VPS with Claude Code)

1. Claude Code reads `CLAUDE.md` for full context.
2. New tasks: copy `docs/TASK_TEMPLATE.md` to `tasks/<name>/TASK.md`, fill in spec.
3. All development uses `--mock` mode with saved sample data.
4. Push to GitHub, pull on company laptop for live testing.

## Adding a New Task

1. Create `tasks/<task_name>/` directory
2. Copy and fill in `TASK.md` from the template
3. Implement `main.py` (entry point) and `logic.py` (business logic)
4. Capture mock data: `ops capture <task_name>` on company laptop
5. Test: `ops test <task_name>` → `ops run <task_name>`
6. Schedule if recurring: `ops schedule <task_name>` for Task Scheduler command

## Project Structure

See `CLAUDE.md` for full details on architecture, systems access, and coding standards.
