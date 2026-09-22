#!/usr/bin/env python3
"""End-to-end test of the gh-pages publish step, in an isolated clone.

Mirrors the workflow's "Publish to gh-pages" step command-for-command and asserts the
properties that matter:
  * gh-pages is created on the first run and reused afterwards
  * identical data  -> no new commit (no empty publish)
  * a dirty worktree must not break the branch switch
  * a new day of data -> new commit and the new CSV is served
  * data reverted     -> stale CSV is pruned from the served site
  * the served site is self-contained (index.html + site-data.json + data/ + .nojekyll)

Git runs with core.autocrlf=false to match ubuntu-latest, so this tests the workflow's
logic rather than Windows line-ending behaviour.

Run: python scripts/test_publish.py
"""

from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GIT = ["git", "-c", "core.autocrlf=false", "-c", "user.name=test", "-c", "user.email=test@example.com"]

passed = 0
failed = 0


def check(cond: bool, label: str, extra: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS :: {label}")
    else:
        failed += 1
        print(f"  FAIL :: {label}" + (f"  -> {extra}" if extra else ""))


def run(args, cwd, check_rc=True):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if check_rc and r.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} failed ({r.returncode})\nstdout:{r.stdout}\nstderr:{r.stderr}")
    return r


def build(work: Path) -> None:
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, "scripts/build_site.py"], cwd=work,
                       capture_output=True, text=True, env=env)
    if r.returncode != 0:
        raise RuntimeError(f"build failed: {r.stdout}\n{r.stderr}")


def snapshot(root: Path) -> str:
    """Same fingerprint scheme as the workflow: sorted 'path hash' lines, hashed again."""
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file() and ".git" not in p.parts):
        h.update(path.relative_to(root).as_posix().encode("utf-8"))
        h.update(b" ")
        h.update(hashlib.sha256(path.read_bytes()).hexdigest().encode("ascii"))
        h.update(b"\n")
    return h.hexdigest()


def publish(work: Path, tmpd: Path, label: str) -> None:
    """Mirror of the workflow's publish step, including the snapshot comparison."""
    # staging dir must be recreated from scratch every run
    staged_dir = tmpd / "site"
    shutil.rmtree(staged_dir, ignore_errors=True)
    (staged_dir / "data").mkdir(parents=True)
    for name in ("index.html", "site-data.json"):
        shutil.copy(work / name, staged_dir / name)
    for csv in (work / "data").glob("amount-*.csv"):
        shutil.copy(csv, staged_dir / "data" / csv.name)
    (staged_dir / ".nojekyll").touch()
    want = snapshot(staged_dir)

    # ls-remote is the authoritative check: actions/checkout only fetches the
    # default branch, so a local origin/gh-pages ref may not exist.
    has_remote = run(["git", "ls-remote", "--exit-code", "--heads", "origin", "gh-pages"],
                     work, check_rc=False).returncode == 0
    print(f"  [{label}] gh-pages on remote: {has_remote}")

    have = ""
    if has_remote:
        run(["git", "fetch", "--quiet", "--force", "origin",
             "refs/heads/gh-pages:refs/remotes/origin/gh-pages"], work)
        current = tmpd / "site-current"
        shutil.rmtree(current, ignore_errors=True)
        # materialise the existing gh-pages content without disturbing the main work tree
        run(["git", "worktree", "add", "--force", "--detach", str(current), "origin/gh-pages"], work)
        try:
            have = snapshot(current)
        finally:
            run(["git", "worktree", "remove", "--force", str(current)], work, check_rc=False)

    if have and have == want:
        print(f"  [{label}] SKIPPED - gh-pages already matches the desired content")
        return

    if has_remote:
        run(["git", "checkout", "--force", "-B", "gh-pages"], work)
        run(["git", "reset", "--hard", "origin/gh-pages"], work)
    else:
        run(["git", "checkout", "--force", "--orphan", "gh-pages"], work)

    run(["git", "rm", "-r", "--cached", "."], work, check_rc=False)
    for entry in work.iterdir():
        if entry.name == ".git":
            continue
        shutil.rmtree(entry) if entry.is_dir() else entry.unlink()
    for entry in staged_dir.iterdir():
        dst = work / entry.name
        if entry.is_dir():
            shutil.copytree(entry, dst)
        else:
            shutil.copy(entry, dst)

    # workflow self-check: csv count on the site must equal the number of days in the page
    page_days = len(json.loads((work / "site-data.json").read_text(encoding="utf-8"))["dates"])
    csv_count = len(list((work / "data").glob("amount-*.csv")))
    if page_days != csv_count:
        raise RuntimeError(f"stale data check failed: {csv_count} csv vs {page_days} day(s)")

    run(["git", "add", "-A"], work)
    staged = run(["git", "diff", "--cached", "--quiet"], work, check_rc=False).returncode != 0
    if not staged:
        print(f"  [{label}] nothing to publish (already up to date)")
    else:
        stat = run(["git", "diff", "--cached", "--stat"], work).stdout.strip()
        print(f"  [{label}] staged: " + "; ".join(l.strip() for l in stat.splitlines()[-1:]))
        # --allow-empty + --no-edit guards against the "nothing to commit" case: HEAD may
        # already match the staged tree (e.g. right after a hard reset to the same content),
        # which makes a plain `git commit` fail with exit 128. A publish that changes
        # nothing must simply be skipped, never crash the step.
        r = subprocess.run([*GIT, "commit", "--allow-empty", "-q", "-m", "publish: usage dashboard"],
                           cwd=work, capture_output=True, text=True)
        if r.returncode != 0:
            diag = {}
            for key, args in (
                ("status", ["git", "status", "--porcelain=v1"]),
                ("cached", ["git", "diff", "--cached", "--name-status"]),
                ("head", ["git", "log", "-1", "--oneline"]),
                ("branch", ["git", "rev-parse", "--abbrev-ref", "HEAD"]),
                ("identity", ["git", "config", "user.email"]),
            ):
                out = subprocess.run(args, cwd=work, capture_output=True, text=True)
                diag[key] = (out.stdout + out.stderr).strip().replace("\n", " | ")[:200]
            raise RuntimeError(f"git commit failed rc={r.returncode} stderr={r.stderr.strip()!r} diag={diag}")
        run(["git", "push", "-q", "--force", "origin", "gh-pages"], work)
        print(f"  [{label}] published")

    run(["git", "checkout", "--force", "master"], work)


def gh_files(work: Path) -> list[str]:
    return sorted(run(["git", "ls-tree", "-r", "--name-only", "gh-pages"], work).stdout.split())


def gh_head(work: Path) -> str:
    return run(["git", "rev-parse", "gh-pages"], work).stdout.strip()


def gh_count(work: Path) -> int:
    return int(run(["git", "rev-list", "--count", "gh-pages"], work).stdout.strip())


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="publishtest-"))
    remote = tmp / "remote.git"
    work = tmp / "work"
    tmpd = tmp / "runner"
    tmpd.mkdir()

    run(["git", "init", "--bare", "-q", str(remote)], tmp)
    # Build the sandbox as a brand-new repo populated from the checked-out working tree.
    #
    # Why not clone: in CI the checkout is *shallow*, and a shallow clone cannot be pushed
    # to a fresh remote ("shallow update not allowed"), nor reliably unshallowed
    # (actions/checkout leaves no local branch ref for `fetch --unshallow` to resolve).
    # Re-initialising from the working tree sidesteps all of that and keeps this test
    # independent of how the checkout was made.
    work.mkdir()
    run([*GIT, "init", "-q"], work)
    run([*GIT, "checkout", "-q", "-b", "master"], work)
    for entry in sorted(REPO.iterdir()):
        if entry.name == ".git" or entry.name.startswith("_"):
            continue
        dst = work / entry.name
        if entry.is_dir():
            shutil.copytree(entry, dst, ignore=shutil.ignore_patterns(".git"))
        else:
            shutil.copy(entry, dst)
    run([*GIT, "add", "-A"], work)
    run([*GIT, "commit", "-q", "-m", "seed: working tree snapshot"], work)
    run([*GIT, "remote", "add", "origin", str(remote)], work)
    run([*GIT, "push", "-q", "origin", "master"], work)
    # mimic actions/checkout: the local repo knows only the default branch
    run([*GIT, "remote", "set-branches", "origin", "master"], work)

    try:
        print("== run 1: first publish (no gh-pages anywhere) ==")
        build(work)
        publish(work, tmpd, "run1")
        check(gh_count(work) == 1, "gh-pages created with 1 commit", str(gh_count(work)))
        check(gh_files(work) == [".nojekyll", "data/amount-2026-09-21.csv", "index.html", "site-data.json"],
              "gh-pages contains exactly the site files", str(gh_files(work)))
        # the remote must actually have it
        rem = run(["git", "--git-dir", str(remote), "for-each-ref", "--format=%(refname:short)", "refs/heads"],
                  tmp).stdout.split()
        check("gh-pages" in rem, "gh-pages exists on the remote", str(rem))

        print("\n== run 2: identical inputs (expect no new commit) ==")
        before = gh_head(work)
        build(work)
        publish(work, tmpd, "run2")
        check(gh_head(work) == before, "gh-pages HEAD unchanged (no empty publish)")
        check(gh_count(work) == 1, "still exactly 1 commit", str(gh_count(work)))

        print("\n== run 3: dirty worktree must not break the switch ==")
        with open(work / "index.html", "a", encoding="utf-8") as fh:
            fh.write("\n<!-- locally modified -->\n")
        build(work)
        publish(work, tmpd, "run3")
        check(gh_head(work) == before, "dirty worktree produced no spurious publish")
        check(run(["git", "rev-parse", "--abbrev-ref", "HEAD"], work).stdout.strip() == "master",
              "harness returned to master after publishing")

        print("\n== run 4: a new day of data arrives ==")
        src_csv = work / "data" / "amount-2026-09-21.csv"
        text = src_csv.read_text(encoding="utf-8").replace("2026-09-21", "2026-09-22")
        (work / "data" / "amount-2026-09-22.csv").write_text(text, encoding="utf-8", newline="\n")
        build(work)
        data = json.loads((work / "site-data.json").read_text(encoding="utf-8"))
        check(data["dates"] == ["2026-09-21", "2026-09-22"], "page spans 2 days", str(data["dates"]))
        # default_date is "yesterday in Beijing": it must be a date that exists AND is not
        # in the future relative to Beijing today (a manual run must never default to today's
        # still-incomplete day). Depending on when this test runs, either day is valid.
        today_cn = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
        expected = max(d for d in data["dates"] if d < today_cn) if any(d < today_cn for d in data["dates"]) \
            else max(data["dates"])
        check(data["default_date"] == expected,
              f"default_date is the newest non-future day ({expected})", str(data["default_date"]))
        publish(work, tmpd, "run4")
        check(gh_count(work) == 2, "gh-pages got a 2nd commit", str(gh_count(work)))
        check("data/amount-2026-09-22.csv" in gh_files(work), "new day's CSV is served", str(gh_files(work)))

        print("\n== run 5: data reverted to one day (stale file must be pruned) ==")
        # The harness switches back to master between runs, and that day's CSV only ever
        # existed on gh-pages, so recreate it before removing it. (The real workflow
        # rebuilds the working tree from scratch each run, so it has no such gap.)
        stale = work / "data" / "amount-2026-09-22.csv"
        if not stale.exists():
            stale.write_text((work / "data" / "amount-2026-09-21.csv").read_text(encoding="utf-8")
                             .replace("2026-09-21", "2026-09-22"), encoding="utf-8", newline="\n")
        build(work)
        if json.loads((work / "site-data.json").read_text(encoding="utf-8"))["dates"] != ["2026-09-21", "2026-09-22"]:
            raise RuntimeError("harness lost the 2-day data set before the prune test")
        stale.unlink()
        build(work)
        publish(work, tmpd, "run5")
        check(gh_files(work) == [".nojekyll", "data/amount-2026-09-21.csv", "index.html", "site-data.json"],
              "stale CSV pruned from served site", str(gh_files(work)))
        check(gh_count(work) == 3, "gh-pages got a 3rd commit", str(gh_count(work)))

        print("\n== run 6: gh-pages left stale (regression: must self-heal) ==")
        # Reproduces the bug where the site silently stopped updating: gh-pages held older
        # content while master was current, and because no *commit* happened in the run,
        # the old "publish only if this run committed" gate skipped publishing forever.
        stale_work = work / "data" / "amount-2026-09-99.csv"
        stale_work.write_text((work / "data" / "amount-2026-09-21.csv").read_text(encoding="utf-8")
                              .replace("2026-09-21", "2026-09-99"), encoding="utf-8", newline="\n")
        build(work)
        publish(work, tmpd, "run6-stale-inject")
        check("data/amount-2026-09-99.csv" in gh_files(work), "gh-pages now holds the bogus 4th file",
              str(gh_files(work)))
        # now revert master to the 1-day set WITHOUT making any new commit, so this run has no
        # "change flag" at all — exactly the situation that used to leave the site stale.
        stale_work.unlink(missing_ok=True)
        build(work)
        gh_before = gh_head(work)
        publish(work, tmpd, "run7-heal")
        check(gh_head(work) != gh_before, "a publish happened even though this run made no commit")
        check(gh_files(work) == [".nojekyll", "data/amount-2026-09-21.csv", "index.html", "site-data.json"],
              "gh-pages self-healed back to the desired content", str(gh_files(work)))
        # and once healed, further publishes must be skipped again
        count_before = gh_count(work)
        publish(work, tmpd, "run8-noop")
        check(gh_count(work) == count_before, "after healing, no further publish commits are made",
              f"{count_before} -> {gh_count(work)}")

        print("\n== serve the published site like GitHub Pages would ==")
        serve = tmp / "serve"
        run(["git", "clone", "-q", "--branch", "gh-pages", str(remote), str(serve)], tmp)
        check((serve / "index.html").is_file(), "index.html served at site root")
        check((serve / ".nojekyll").is_file(), ".nojekyll served")
        served = (serve / "index.html").read_text(encoding="utf-8")
        check("__SITE_DATA__" not in served, "no unreplaced placeholder in served HTML")
        check(served.count("<title>") == 1, "served HTML has a title")
        check("const SITE = {" in served, "data is embedded in served HTML")
        sd = json.loads((serve / "site-data.json").read_text(encoding="utf-8"))
        check(sd["default_date"] == "2026-09-21", "served data matches the source of truth", str(sd["default_date"]))
        # served page must be self-contained: the CSV link target must exist
        check((serve / "data" / f"amount-{sd['default_date']}.csv").is_file(),
              "CSV referenced by the page is served alongside it")

        print("\n== real repo untouched ==")
        status = run(["git", "status", "--porcelain", "--", "data/"], REPO).stdout.strip()
        check(status == "", "real data/ has no uncommitted changes", status)
        check(sorted(p.name for p in (REPO / "data").glob("*.csv")) == ["amount-2026-09-21.csv"],
              "real data/ still holds exactly one file")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\nRESULT: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
