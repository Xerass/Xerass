#!/usr/bin/env python3
import json
import os
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

USER = os.environ.get("GH_USER", "Xerass")
TOKEN = os.environ.get("GITHUB_TOKEN", "")
README = os.environ.get("README_PATH", "README.md")

BAR_W = 26
TOP_N = 8
FULL, EMPTY = "█", "░"

EXCLUDE_LANGS = {"Jupyter Notebook", "HTML", "CSS", "SCSS"}
EXCLUDE_REPOS = set()
INCLUDE_FORKS = False

START, END = "<!--STATS:START-->", "<!--STATS:END-->"
API = "https://api.github.com"


def _req(url, method="GET", body=None):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"{USER}-cli-stats",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    data = json.dumps(body).encode() if body is not None else None
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def rest(path):
    return _req(f"{API}{path}")


def rest_paged(path, per_page=100):
    out, page = [], 1
    while True:
        sep = "&" if "?" in path else "?"
        chunk = rest(f"{path}{sep}per_page={per_page}&page={page}")
        if not chunk:
            break
        out.extend(chunk)
        if len(chunk) < per_page:
            break
        page += 1
    return out


def graphql(query, variables):
    return _req(
        "https://api.github.com/graphql",
        method="POST",
        body={"query": query, "variables": variables},
    )


def bar(pct, width=BAR_W):
    filled = int(round(pct / 100 * width))
    filled = max(0, min(width, filled))
    return FULL * filled + EMPTY * (width - filled)


def human_bytes(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:,.0f} {unit}" if unit == "B" else f"{n:,.1f} {unit}"
        n /= 1024


def rule(label, width=64):
    label = f" {label} "
    return label + "─" * max(0, width - len(label))


def fetch_profile():
    return rest(f"/users/{USER}")


def fetch_repos():
    repos = rest_paged(f"/users/{USER}/repos?type=owner&sort=pushed")
    keep = []
    for r in repos:
        if r["name"] in EXCLUDE_REPOS:
            continue
        if r.get("fork") and not INCLUDE_FORKS:
            continue
        if r.get("archived"):
            continue
        keep.append(r)
    return keep


def fetch_languages(repos):
    totals = defaultdict(int)
    for r in repos:
        try:
            langs = rest(f"/repos/{USER}/{r['name']}/languages")
        except urllib.error.HTTPError as e:
            print(f"  ! languages {r['name']}: {e.code}", file=sys.stderr)
            continue
        for lang, size in langs.items():
            if lang in EXCLUDE_LANGS:
                continue
            totals[lang] += size
    return dict(sorted(totals.items(), key=lambda kv: -kv[1]))


CONTRIB_QUERY = """
query($login: String!) {
  user(login: $login) {
    contributionsCollection {
      totalCommitContributions
      totalPullRequestContributions
      totalIssueContributions
      totalRepositoriesWithContributedCommits
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount } }
      }
    }
  }
}
"""


def fetch_contributions():
    try:
        res = graphql(CONTRIB_QUERY, {"login": USER})
    except urllib.error.HTTPError as e:
        print(f"  ! graphql: {e.code} {e.read()[:200]!r}", file=sys.stderr)
        return None
    if res.get("errors"):
        print(f"  ! graphql: {res['errors']}", file=sys.stderr)
        return None
    user = (res.get("data") or {}).get("user")
    return user["contributionsCollection"] if user else None


def render_header(profile, repos):
    stars = sum(r.get("stargazers_count", 0) for r in repos)
    forks = sum(r.get("forks_count", 0) for r in repos)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    L = []
    L.append(f"{USER.lower()}@github:~$ gh profile --summary")
    L.append("")
    L.append(f"  user      {profile.get('login')}"
             + (f"  ({profile['name']})" if profile.get("name") else ""))
    L.append(f"  repos     {len(repos)} public (non-fork)")
    L.append(f"  stars     {stars:,}   forks {forks:,}")
    L.append(f"  followers {profile.get('followers', 0):,}   "
             f"following {profile.get('following', 0):,}")
    L.append(f"  synced    {now}")
    return L, now


def render_languages(langs):
    L = []
    L.append(f"{USER.lower()}@github:~$ gh langs --top {TOP_N} --exclude jupyter")
    L.append("")
    if not langs:
        L.append("  (no language data)")
        return L
    total = sum(langs.values())
    top = list(langs.items())[:TOP_N]
    width = max(len(k) for k, _ in top)
    for name, size in top:
        pct = size / total * 100
        L.append(f"  {name.ljust(width)}  {bar(pct)}  {pct:5.1f}%  "
                 f"{human_bytes(size):>9}")
    rest_size = total - sum(s for _, s in top)
    if rest_size > 0:
        pct = rest_size / total * 100
        L.append(f"  {'other'.ljust(width)}  {bar(pct)}  {pct:5.1f}%  "
                 f"{human_bytes(rest_size):>9}")
    L.append("")
    L.append(f"  excluded: {', '.join(sorted(EXCLUDE_LANGS))}")
    return L


def render_contributions(c):
    L = []
    L.append(f"{USER.lower()}@github:~$ gh contrib --since 12mo --graph monthly")
    L.append("")
    if not c:
        L.append("  (contribution data unavailable)")
        return L

    days = [d for w in c["contributionCalendar"]["weeks"]
            for d in w["contributionDays"]]
    monthly = defaultdict(int)
    for d in days:
        monthly[d["date"][:7]] += d["contributionCount"]

    months = sorted(monthly.items())[-12:]
    peak = max((v for _, v in months), default=1) or 1
    for ym, count in months:
        label = datetime.strptime(ym, "%Y-%m").strftime("%b %Y")
        L.append(f"  {label}  {bar(count / peak * 100)}  {count:>4}")

    best = max(days, key=lambda d: d["contributionCount"], default=None)
    total = c["contributionCalendar"]["totalContributions"]
    L.append("")
    L.append(f"  total {total:,} contributions   "
             f"commits {c['totalCommitContributions']:,}   "
             f"PRs {c['totalPullRequestContributions']:,}   "
             f"issues {c['totalIssueContributions']:,}")
    if best and best["contributionCount"]:
        L.append(f"  busiest day  {best['date']}  ({best['contributionCount']})")
    return L


def build_block(profile, repos, langs, contrib):
    head, now = render_header(profile, repos)
    lines = ["```console"]
    lines += head
    lines.append("")
    lines.append(rule("LANGUAGES"))
    lines.append("")
    lines += render_languages(langs)
    lines.append("")
    lines.append(rule("ACTIVITY"))
    lines.append("")
    lines += render_contributions(contrib)
    lines.append("```")
    return "\n".join(lines)


def inject(block):
    with open(README, encoding="utf-8") as f:
        content = f.read()
    if START not in content or END not in content:
        print(f"error: markers {START} / {END} not found in {README}",
              file=sys.stderr)
        sys.exit(1)
    pre = content.split(START)[0]
    post = content.split(END)[1]
    new = f"{pre}{START}\n{block}\n{END}{post}"
    if new == content:
        print("no change")
        return False
    with open(README, "w", encoding="utf-8") as f:
        f.write(new)
    print("README.md updated")
    return True


def main():
    print(f"fetching stats for {USER} ...")
    profile = fetch_profile()
    repos = fetch_repos()
    print(f"  {len(repos)} repos")
    langs = fetch_languages(repos)
    print(f"  {len(langs)} languages")
    contrib = fetch_contributions()
    inject(build_block(profile, repos, langs, contrib))


if __name__ == "__main__":
    main()
