# Service Version Tracker

Live dashboard showing deployed build versions across Dev / QA / Staging / Prod for all UltraTax services.

**Dashboard:** `https://<your-org>.github.io/<this-repo>/`

Auto-refreshes every 5 minutes in the browser. GitHub Actions updates the data every 10 minutes.

---

## How it works

```
GitHub Actions (every 10 min)
  └─ python version_tracker.py --output docs/index.html
      └─ fetches /statusCheck on each service × environment (parallel)
          └─ commits docs/index.html → GitHub Pages serves it
```

## Setup (one-time)

### 1. Create the GitHub repo

```bash
gh repo create ultratax-version-tracker --private
cd version-tracker
git init && git add . && git commit -m "initial"
git remote add origin https://github.com/<org>/ultratax-version-tracker.git
git push -u origin main
```

### 2. Enable GitHub Pages

Go to **Settings → Pages** in the repo:
- Source: **Deploy from a branch**
- Branch: `main` / `docs`

Your dashboard will be live at `https://<org>.github.io/ultratax-version-tracker/`

### 3. Grant Actions write permission

Go to **Settings → Actions → General → Workflow permissions**:
- Select **Read and write permissions**

That's it. The Action runs automatically every 10 minutes.

---

## Adding a service

Edit [`services.yaml`](services.yaml):

```yaml
- name: My New Service
  version_path: entries.self.data.build   # dot-path into JSON response
  branch_path: entries.self.data.branch
  timeout: 8
  endpoints:
    dev: https://dev.api.ultratax.com/myservice/statusCheck
    qa:  https://qa.api.ultratax.com/myservice/statusCheck
    staging: https://stage.api.ultratax.com/myservice/statusCheck
    prod: https://api.ultratax.com/myservice/statusCheck
```

Push the change — the next Action run picks it up automatically.

## Running locally

```bash
pip install -r requirements.txt
python version_tracker.py              # generates version-report.html
python version_tracker.py --no-html   # terminal table only
python version_tracker.py --env dev,qa  # subset of environments
```

## Health check response format

All services use ASP.NET `HealthChecks.UI` (`UIResponseWriter`):

```json
{
  "status": "Healthy",
  "entries": {
    "self": {
      "data": { "build": "20260626.2", "branch": "main" },
      "status": "Healthy"
    }
  }
}
```

Version is read from `entries.self.data.build` (configured per service in `services.yaml`).
