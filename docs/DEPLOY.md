# Deploying Milieu Labs / Energy Strategy

A FastAPI app (`ambient_loop.web.app:app`) with server-rendered pages. One container, one data volume.

## Run locally

```bash
uv sync
```

```bash
MILIEU_DATA=./milieu_data uv run ambient-loop web --port 8000
```

## Run in Docker

```bash
docker build -t milieu-labs-energy .
```

```bash
docker run -p 8000:8000 -v /srv/milieu-energy:/data -e ANTHROPIC_API_KEY=... milieu-labs-energy
```

Put it behind the Milieu Labs platform's authentication (SSO / reverse proxy). The app has **no login of its own**: anyone who can reach it can see every project.

## The data volume (`MILIEU_DATA`)

```
/data
├── weather/
│   ├── index.yaml          # list of weather files offered at project creation
│   └── London_LWC_DSY1_2020High50.epw   (CIBSE files: licensed, keep off public hosts)
├── rates.yaml              # Milieu rate table; copy library/milieu/rates.template.yaml and edit
└── projects/<id>/          # created by the app: project.json, uploads/, model/, outputs/
```

`weather/index.yaml`:

```yaml
- {id: london_try, label: "CIBSE London TRY 2016", file: London_TRY.epw, kind: TRY}
- {id: london_dsy1, label: "CIBSE London DSY1 2020s high 50%", file: London_DSY1.epw, kind: DSY}
```

EPW files are read directly (dry bulb and RH). CSV files need a `dry_bulb_C` column and optionally `RH_pct` or `wet_bulb_C`; 8,760 rows.

Until `rates.yaml` exists the app shows "Placeholder rates in use" and every cost and space figure is a placeholder (A-067).

## Claude

Set `ANTHROPIC_API_KEY` on the server to turn on: reading descriptions and documents into zones (step 1), terminal review (step 3) and report narrative (steps 5–6). Without a key the app runs fully on rules. Requests use server-side model fallback, so a request may be served by a fallback model if the primary is unavailable. `MILIEU_CLAUDE_MODEL` overrides the model.

Project descriptions and uploaded documents are sent to the Anthropic API when Claude is on. Check this is consistent with client confidentiality terms for each project.

## Health

`GET /healthz` returns whether Claude is on, how many weather files loaded, and whether rates are placeholders.
