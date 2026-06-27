#!/usr/bin/env python3
"""
Version Tracker — fetches service versions from HTTP endpoints across environments
and produces a color-coded terminal table + HTML report.

Usage:
    python version_tracker.py                         # uses services.yaml
    python version_tracker.py --config path/to/file  # custom config
    python version_tracker.py --output report.html   # custom output path
    python version_tracker.py --no-html              # terminal only
    python version_tracker.py --env dev,qa           # subset of environments
"""

import argparse
import concurrent.futures
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import requests
import yaml

ANSI = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "green": "\033[92m",
    "yellow": "\033[93m",
    "red": "\033[91m",
    "cyan": "\033[96m",
    "grey": "\033[90m",
}

ENV_COLORS = {
    "dev": "#6366f1",
    "qa": "#f59e0b",
    "staging": "#10b981",
    "prod": "#ef4444",
}


def color(text, *codes):
    return "".join(ANSI.get(c, "") for c in codes) + text + ANSI["reset"]


def get_nested(data, path):
    """Resolve dot-notation path in a dict. Returns None if missing."""
    keys = path.split(".")
    val = data
    for k in keys:
        if not isinstance(val, dict):
            return None
        val = val.get(k)
    return val


def fetch_version(service_name, env, url, version_path, timeout, auth_token=None, branch_path=None, availability_only=False):
    """Fetch a single endpoint and extract the version string."""
    headers = {}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    try:
        resp = requests.get(url, headers=headers, timeout=timeout, verify=False, allow_redirects=True)
        if availability_only:
            return {"status": "ok", "version": f"HTTP {resp.status_code}", "branch": None, "raw": None}
        resp.raise_for_status()
        data = resp.json()
        version = get_nested(data, version_path)
        branch = get_nested(data, branch_path) if branch_path else None
        if version is None:
            return {"status": "parse_error", "version": None, "branch": None, "raw": str(data)[:300]}
        return {"status": "ok", "version": str(version), "branch": str(branch) if branch else None, "raw": None}
    except requests.exceptions.ConnectionError:
        return {"status": "unreachable", "version": None, "branch": None, "raw": "Connection refused"}
    except requests.exceptions.Timeout:
        return {"status": "timeout", "version": None, "branch": None, "raw": f"Timed out after {timeout}s"}
    except requests.exceptions.HTTPError as e:
        return {"status": "http_error", "version": None, "branch": None, "raw": str(e)}
    except (ValueError, KeyError) as e:
        return {"status": "parse_error", "version": None, "branch": None, "raw": str(e)}
    except Exception as e:
        return {"status": "error", "version": None, "branch": None, "raw": str(e)}


def load_config(config_path):
    with open(config_path) as f:
        return yaml.safe_load(f)


def resolve_auth(config, env):
    env_tokens = config.get("env_auth_tokens", {}) or {}
    if env in env_tokens:
        return env_tokens[env]
    return config.get("global_auth_token")


def collect_versions(config, target_envs):
    environments = [e for e in config["environments"] if e in target_envs]
    services = config["services"]
    results = {}

    tasks = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
        for svc in services:
            svc_name = svc["name"]
            results[svc_name] = {}
            for env in environments:
                url = svc.get("endpoints", {}).get(env)
                if not url:
                    results[svc_name][env] = {"status": "not_configured", "version": None, "branch": None, "raw": None}
                    continue
                timeout = svc.get("timeout", 5)
                auth = resolve_auth(config, env)
                availability_only = svc.get("availability_only", False)
                version_path = svc.get("version_path", "version")
                branch_path = svc.get("branch_path")
                future = pool.submit(fetch_version, svc_name, env, url, version_path, timeout, auth, branch_path, availability_only)
                tasks.append((svc_name, env, future))

        for svc_name, env, future in tasks:
            results[svc_name][env] = future.result()

    return environments, results


def print_terminal(environments, results, services):
    col_w = 18
    svc_w = 30
    header = color(f"{'Service':<{svc_w}}", "bold", "cyan") + "  " + "  ".join(
        color(f"{e.upper():^{col_w}}", "bold") for e in environments
    )
    print("\n" + header)
    print("─" * (svc_w + 2 + (col_w + 2) * len(environments)))

    for svc in services:
        name = svc["name"]
        row = f"{name:<{svc_w}}  "
        env_data = results.get(name, {})
        versions = [env_data.get(e, {}).get("version") for e in environments]
        unique = {v for v in versions if v}

        for e in environments:
            d = env_data.get(e, {})
            v = d.get("version")
            branch = d.get("branch")
            status = d.get("status")
            if status == "ok":
                label = v or ""
                if branch:
                    label = f"{v} ({branch[:10]})"
                text = f"{label:^{col_w}}"
                if len(unique) > 1:
                    row += color(text, "yellow") + "  "
                else:
                    row += color(text, "green") + "  "
            elif status == "not_configured":
                row += color(f"{'—':^{col_w}}", "grey") + "  "
            else:
                row += color(f"{'✗ ' + status:^{col_w}}", "red") + "  "
        print(row)

    print()
    print(color("  ● green = consistent version   ● yellow = version mismatch   ● red = unreachable", "grey"))
    print()


def render_html(environments, results, services, generated_at):
    env_headers = "".join(
        f'<th style="background:{ENV_COLORS.get(e, "#64748b")}">{e.upper()}</th>'
        for e in environments
    )

    rows_html = ""
    for svc in services:
        name = svc["name"]
        env_data = results.get(name, {})
        versions = [env_data.get(e, {}).get("version") for e in environments if env_data.get(e, {}).get("status") == "ok"]
        unique = set(versions)
        mismatch = len(unique) > 1

        cells = ""
        for e in environments:
            d = env_data.get(e, {})
            v = d.get("version")
            status = d.get("status", "")
            raw = d.get("raw") or ""
            tooltip = raw[:200] if raw else ""

            branch = d.get("branch") or ""
            if status == "ok":
                cls = "mismatch" if mismatch else "ok"
                branch_html = f'<div class="branch">{branch}</div>' if branch else ""
                cells += f'<td class="{cls}" title="{tooltip}">{v}{branch_html}</td>'
            elif status == "not_configured":
                cells += '<td class="na">—</td>'
            else:
                cells += f'<td class="error" title="{tooltip}">✗ {status}</td>'

        rows_html += f"<tr><td class='svc-name'>{name}</td>{cells}</tr>\n"

    mismatch_count = sum(
        1 for svc in services
        for name in [svc["name"]]
        for versions in [set(
            results.get(name, {}).get(e, {}).get("version")
            for e in environments
            if results.get(name, {}).get(e, {}).get("status") == "ok"
        )]
        if len(versions) > 1
    )
    status_banner = (
        f'<div class="banner warn">⚠ {mismatch_count} service{"s" if mismatch_count != 1 else ""} with version mismatch across environments</div>'
        if mismatch_count else
        '<div class="banner ok">✓ All services consistent across environments</div>'
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Service Version Tracker</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0f172a; color: #e2e8f0; padding: 2rem; }}
  header {{ display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 0.25rem; }}
  h1 {{ font-size: 1.5rem; color: #f8fafc; }}
  .countdown {{ font-size: 0.8rem; color: #475569; }}
  .meta {{ color: #64748b; font-size: 0.85rem; margin-bottom: 1rem; }}
  .banner {{ padding: 0.6rem 1rem; border-radius: 6px; font-size: 0.85rem; font-weight: 600; margin-bottom: 1.5rem; }}
  .banner.ok {{ background: #14532d; color: #4ade80; }}
  .banner.warn {{ background: #451a03; color: #fbbf24; }}
  .legend {{ display: flex; gap: 1.5rem; margin-bottom: 1.2rem; font-size: 0.8rem; color: #94a3b8; flex-wrap: wrap; }}
  .legend span {{ display: flex; align-items: center; gap: 6px; }}
  .dot {{ width: 10px; height: 10px; border-radius: 50%; display: inline-block; }}
  table {{ width: 100%; border-collapse: collapse; background: #1e293b; border-radius: 10px; overflow: hidden; box-shadow: 0 4px 24px rgba(0,0,0,0.4); }}
  thead tr {{ background: #1e293b; }}
  th {{ padding: 0.9rem 1.2rem; text-align: center; font-size: 0.75rem; font-weight: 700; letter-spacing: 0.08em; color: #fff; }}
  th:first-child {{ text-align: left; background: #1e293b; }}
  td {{ padding: 0.75rem 1.2rem; text-align: center; font-size: 0.85rem; border-top: 1px solid #334155; }}
  td.svc-name {{ text-align: left; font-weight: 600; color: #cbd5e1; white-space: nowrap; }}
  td.ok {{ color: #4ade80; font-family: monospace; }}
  td.mismatch {{ color: #fbbf24; font-family: monospace; font-weight: 700; }}
  td.error {{ color: #f87171; font-size: 0.75rem; }}
  td.na {{ color: #475569; }}
  .branch {{ font-size: 0.7rem; color: #64748b; margin-top: 2px; font-family: monospace; }}
  tr:hover td {{ background: #263248; }}
  footer {{ margin-top: 1.5rem; display: flex; gap: 1rem; align-items: center; }}
  .refresh-btn {{ padding: 0.5rem 1.2rem; background: #3b82f6; color: #fff; border: none; border-radius: 6px; cursor: pointer; font-size: 0.85rem; }}
  .refresh-btn:hover {{ background: #2563eb; }}
  .footer-note {{ font-size: 0.75rem; color: #475569; }}
</style>
</head>
<body>
<header>
  <h1>Service Version Tracker</h1>
  <span class="countdown">Next refresh in <span id="timer">5:00</span></span>
</header>
<div class="meta">Last updated: {generated_at} UTC &nbsp;|&nbsp; {len(services)} services &nbsp;|&nbsp; {len(environments)} environments</div>
{status_banner}
<div class="legend">
  <span><span class="dot" style="background:#4ade80"></span> Consistent</span>
  <span><span class="dot" style="background:#fbbf24"></span> Version mismatch</span>
  <span><span class="dot" style="background:#f87171"></span> Unreachable / error</span>
  <span><span class="dot" style="background:#475569"></span> Not configured</span>
</div>
<table>
  <thead>
    <tr>
      <th>Service</th>
      {env_headers}
    </tr>
  </thead>
  <tbody>
    {rows_html}
  </tbody>
</table>
<footer>
  <button class="refresh-btn" onclick="window.location.reload()">↺ Refresh now</button>
  <span class="footer-note">Auto-refreshes every 5 min &nbsp;·&nbsp; Dashboard updated by GitHub Actions every 10 min</span>
</footer>
<script>
  // Countdown to next page reload (5 minutes)
  var seconds = 300;
  var el = document.getElementById('timer');
  setInterval(function() {{
    seconds--;
    if (seconds <= 0) {{ window.location.reload(); return; }}
    var m = Math.floor(seconds / 60), s = seconds % 60;
    el.textContent = m + ':' + (s < 10 ? '0' : '') + s;
  }}, 1000);
</script>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(description="Service Version Tracker")
    parser.add_argument("--config", default=str(Path(__file__).parent / "services.yaml"), help="Path to YAML config")
    parser.add_argument("--output", default="version-report.html", help="HTML output file path")
    parser.add_argument("--no-html", action="store_true", help="Skip HTML report, terminal only")
    parser.add_argument("--env", help="Comma-separated list of environments to check (default: all)")
    args = parser.parse_args()

    if not Path(args.config).exists():
        print(color(f"Config not found: {args.config}", "red"))
        sys.exit(1)

    config = load_config(args.config)
    all_envs = config["environments"]
    target_envs = set(args.env.split(",")) if args.env else set(all_envs)
    target_envs = [e for e in all_envs if e in target_envs]

    print(color(f"\nFetching versions for {len(config['services'])} services across {len(target_envs)} environments...", "cyan"))

    environments, results = collect_versions(config, set(target_envs))

    print_terminal(environments, results, config["services"])

    if not args.no_html:
        generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        html = render_html(environments, results, config["services"], generated_at)
        output_path = Path(args.output)
        output_path.write_text(html)
        print(color(f"HTML report saved: {output_path.resolve()}", "green"))


if __name__ == "__main__":
    # Suppress SSL warnings for internal endpoints with self-signed certs
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    main()
