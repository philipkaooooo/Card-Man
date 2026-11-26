# CardMan operating workflow

Two clones, one line, one server per job. Follow this exactly; deviations are what
caused the two-Characters-UIs and disk-full incidents.

## Repo layout

- `~/Desktop/CardMan/Card-Man` — dev clone. All editing and commits happen here.
- `~/card-man` — the live app directory. Servers run from here. Do not edit here
  except emergency fixes; pull instead.
- Branch: `claude/mac-sold-import` is the only line. Push from dev, `git pull --ff-only`
  in live when its tree is clean.

## Processes

| job | command | port |
|---|---|---|
| review UI (the app preview) | `python -m cardman.ui --store data/store/evidence.sqlite --port 8755 --engine-every 0` | 8755 |
| engine loop | `python -m cardman.engine run --store data/store/evidence.sqlite --interval 600` | — |

- Exactly one engine loop. The UI never ticks (`--engine-every 0`) so it can answer
  pages while the engine writes.
- Before starting a UI on 8755, check nothing holds it: `lsof -nP -iTCP:8755 -sTCP:LISTEN`.
- Sandbox instances (e.g. another agent session) use other ports and say so here:
  8765 currently runs a second UI while its session work is uncommitted.
- Only the session that started a process restarts it. Never kill a server you did
  not start; check `ps -ax | grep cardman.ui` first.

## Disk guard

- Volume runs at 100%; keep ≥ 200 MB free or SQLite write fails and pages 500.
- Image/icon caches live in `data/cache/`. The bulk backfill
  (`/tmp/cardman-image-backfill.sh`) must pause below 200 MB free.
- Reclaimable: `~/.cache/uv`. Not without asking: anything under Library, Documents,
  Desktop, Downloads.

## Data safety

- `data/store/evidence.sqlite` is the only copy of live evidence. Never symlink caches
  into it, never rebuild paths relative to a clone root.
- Store opens in WAL mode; readers and the engine loop can share it, two engine loops
  cannot.
