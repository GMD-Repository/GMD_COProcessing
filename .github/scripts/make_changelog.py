"""Write the release changelog into metadata.txt from the git history.

One block per release of the channel, newest first, with a brief "what's new":

    0.1.0-beta.3 - What's new:
        * Add [TEST] EARF Inconsistency Checks tool (#28) - <brief detail>

Each bullet is a pull request merged since the previous release of the same channel.
The brief detail is the start of the PR description (read with the GitHub CLI when
GH_TOKEN is set, as in the release workflow); without it, the PR's own commit messages.
  - Beta channel:   every release (betas and stables), so a beta shows what is new since
                    the last beta or stable.
  - Stable channel: stable releases only, so a stable shows everything since the last stable.

The release being built has no tag yet; it is passed as --version and covers up to HEAD.
"""
import argparse
import json
import os
import re
import shutil
import subprocess

TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(?:-beta\.(\d+))?$")
PR_RE = re.compile(r"^Merge pull request #(\d+) from ")
PREFIX_RE = re.compile(r"^(feat|fix|docs|ci|chore|refactor|perf|test|build|style)(\([^)]*\))?!?:\s*", re.I)
DETAIL_MAX = 220


def git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True,
                          text=True, encoding="utf-8").stdout


def sort_key(tag):
    maj, mnr, pat, beta = TAG_RE.match(tag).groups()
    # A beta comes before the stable of the same version.
    return (int(maj), int(mnr), int(pat), 0 if beta else 1, int(beta or 0))


def tidy(text):
    """Strip a conventional-commit prefix and capitalise."""
    text = PREFIX_RE.sub("", text.strip())
    return text[:1].upper() + text[1:]


def shorten(text, limit=DETAIL_MAX):
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:-")
    return cut + "..."


def pr_body(number, repo):
    """PR description from GitHub, or '' when the CLI/token is unavailable."""
    if not (repo and os.environ.get("GH_TOKEN") and shutil.which("gh")):
        return ""
    try:
        out = subprocess.run(["gh", "pr", "view", str(number), "--repo", repo, "--json", "body"],
                             check=True, capture_output=True, text=True, encoding="utf-8").stdout
        return json.loads(out).get("body") or ""
    except (subprocess.CalledProcessError, ValueError):
        return ""


def summarise_body(body):
    """First meaningful lines of a PR description: skip headings, template text, checkboxes."""
    body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
    parts = []
    for line in body.splitlines():
        s = line.strip()
        if not s or s.startswith(("#", "|", "```", "- [ ]", "- [x]", "* [ ]", "* [x]")) \
                or "Generated with" in s or s.lower().startswith(("co-authored", "closes", "fixes #")):
            if parts and not s:
                break  # end of the first paragraph / list
            continue
        parts.append(re.sub(r"^[-*+]\s+|\*\*|`", "", s))
    return "; ".join(parts)


def summarise_commits(merge, title):
    """The PR's own commit subjects (and bullet lines), minus the one repeating the title."""
    try:
        out = git("log", "--no-merges", "--reverse", "--format=%s%n%b", f"{merge}^1..{merge}^2")
    except subprocess.CalledProcessError:
        return ""
    seen, parts = {tidy(title).lower()}, []
    for line in out.splitlines():
        s = line.strip()
        if not s or s.lower().startswith(("co-authored", "signed-off")):
            continue
        s = tidy(re.sub(r"^[-*+]\s+", "", s))
        if s.lower() not in seen:
            seen.add(s.lower())
            parts.append(s)
    return "; ".join(parts)


def entries_for(prev, upto, repo):
    rng = f"{prev}..{upto}" if prev else upto
    out = git("log", "--first-parent", "--format=%H%x1f%s%x1f%b%x1e", rng)
    prs, commits = [], []
    for rec in out.split("\x1e"):
        rec = rec.strip()
        if not rec:
            continue
        sha, subject, body = (rec.split("\x1f") + ["", ""])[:3]
        m = PR_RE.match(subject)
        if m:
            num = m.group(1)
            title = next((l.strip() for l in body.splitlines() if l.strip()), subject)
            detail = summarise_body(pr_body(num, repo)) or summarise_commits(sha, title)
            line = f"{tidy(title)} (#{num})"
            if detail and detail.lower() != tidy(title).lower():
                line += " - " + shorten(detail)
            prs.append(line)
        elif not subject.startswith("Merge "):
            commits.append(tidy(subject))
    items = list(dict.fromkeys(reversed(prs or commits)))  # oldest first, repeats dropped
    return items or ["Maintenance release (no new features)"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--metadata", required=True)
    ap.add_argument("--version", required=True, help="version being released, e.g. 0.1.0-beta.4")
    ap.add_argument("--channel", required=True, choices=["beta", "stable"])
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""),
                    help="owner/name, to read PR descriptions")
    ap.add_argument("--max-releases", type=int, default=15)
    a = ap.parse_args()

    current = "v" + a.version
    tags = [t for t in git("tag", "-l", "v*").split() if TAG_RE.match(t) and t != current]
    if a.channel == "stable":
        tags = [t for t in tags if "-beta." not in t]
    tags = sorted((t for t in tags if sort_key(t) < sort_key(current)), key=sort_key)

    chain = tags + [current]
    blocks = []
    for i, tag in enumerate(chain):
        prev = chain[i - 1] if i else None
        items = entries_for(prev, "HEAD" if tag == current else tag, a.repo)
        blocks.append([f"{tag[1:]} - What's new:"] + [f"    * {it}" for it in items])
    blocks = list(reversed(blocks))[: a.max_releases]

    with open(a.metadata, encoding="utf-8") as f:
        lines = f.read().splitlines()
    out, skipping = [], False
    for line in lines:
        if skipping:
            if line.startswith((" ", "\t")) or not line.strip():
                continue  # old changelog continuation lines
            skipping = False
        if line.startswith("changelog="):
            out.append("changelog=")
            for block in blocks:
                out.extend("    " + l for l in block)
            out.append("")
            skipping = True
            continue
        out.append(line)
    with open(a.metadata, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out) + "\n")
    for block in blocks:
        print("\n".join(block))


if __name__ == "__main__":
    main()
