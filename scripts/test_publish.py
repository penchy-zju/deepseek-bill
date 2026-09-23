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
import re
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


def publish(work: Path, tmpd: Path, label: str, reseat=None, reset_fixture: bool = True) -> None:
    """Mirror of the workflow's publish step, including the snapshot comparison.

    `reseat` restores the canonical fixture; pass reset_fixture=False for the runs whose whole
    point is to publish a deliberately different (or missing) data set.
    """
    if reseat is not None and reset_fixture:
        reseat()
        build(work)
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

    # The workflow's self-check compares the served csv count with the days in the page.
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


def source_dates() -> list[str]:
    """Dates actually present in the real repo's data/ (sorted)."""
    out = []
    for p in (REPO / "data").glob("amount-*.csv"):
        m = re.fullmatch(r"amount-(\d{4}-\d{2}-\d{2})\.csv", p.name)
        if m:
            out.append(m.group(1))
    return sorted(out)


def next_day(day: str) -> str:
    d = datetime.strptime(day, "%Y-%m-%d").date() + timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def expected_files(dates: list[str]) -> list[str]:
    return sorted([".nojekyll", "index.html", "site-data.json"] +
                  [f"data/amount-{d}.csv" for d in dates])


def main() -> int:
    src_dates = source_dates()
    if not src_dates:
        print("no data/amount-*.csv in the repo; nothing to test")
        return 0
    # Deterministic two-day fixture derived from a real file, so this test never depends on
    # how many days the repo happens to contain (an earlier version hardcoded two specific
    # dates and started failing in CI the moment a second day of data landed).
    day1 = src_dates[0]
    day2 = next_day(day1)
    template = (REPO / "data" / f"amount-{day1}.csv").read_text(encoding="utf-8")
    two_days = [day1, day2]

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
    work_git = ["git", "-c", "core.autocrlf=false", "-c", "user.name=test", "-c", "user.email=test@example.com"]

    def wrun(args, check_rc=True):
        r = subprocess.run([*work_git, *args], cwd=work, capture_output=True, text=True)
        if check_rc and r.returncode != 0:
            raise RuntimeError(f"{' '.join(args)} failed ({r.returncode})\n{r.stdout}\n{r.stderr}")
        return r

    wrun(["checkout", "-q", "-b", "master"])
    for entry in sorted(REPO.iterdir()):
        if entry.name == ".git" or entry.name.startswith("_"):
            continue
        dst = work / entry.name
        if entry.is_dir():
            shutil.copytree(entry, dst, ignore=shutil.ignore_patterns(".git"))
        else:
            shutil.copy(entry, dst)

    # Replace the copied data/ with a deterministic single-day fixture.
    fixture_dir = REPO / "data"
    (work / "data").mkdir(exist_ok=True)
    for p in (work / "data").glob("amount-*.csv"):
        p.unlink()
    (work / "data" / f"amount-{day1}.csv").write_text(template, encoding="utf-8", newline="\n")

    wrun(["add", "-A"])
    wrun(["commit", "-q", "-m", "seed: working tree snapshot"])
    wrun(["remote", "add", "origin", str(remote)])
    wrun(["push", "-q", "origin", "master"])
    # mimic actions/checkout: the local repo knows only the default branch
    wrun(["remote", "set-branches", "origin", "master"])

    def gh_files_expected():
        dates = json.loads((work / "site-data.json").read_text(encoding="utf-8"))["dates"]
        return expected_files(dates)

    def reseat() -> None:
        """Restore the deterministic single-day fixture in the sandbox."""
        (work / "data").mkdir(exist_ok=True)
        for p in (work / "data").glob("amount-*.csv"):
            p.unlink()
        (work / "data" / f"amount-{day1}.csv").write_text(template, encoding="utf-8", newline="\n")

    def make_day(day: str) -> None:
        (work / "data" / f"amount-{day}.csv").write_text(
            template.replace(day1, day), encoding="utf-8", newline="\n")

    try:
        print(f"fixture dates: {day1} (+{day2} when needed); repo has {len(src_dates)} day(s)")
        print("\n== run 1: first publish (no gh-pages anywhere) ==")
        build(work)
        expected = gh_files_expected()
        publish(work, tmpd, "run1", reseat)
        check(gh_count(work) == 1, "gh-pages created with 1 commit", str(gh_count(work)))
        check(gh_files(work) == expected, "gh-pages contains exactly the site files",
              f"{gh_files(work)} want {expected}")
        rem = run(["git", "--git-dir", str(remote), "for-each-ref", "--format=%(refname:short)", "refs/heads"],
                  tmp).stdout.split()
        check("gh-pages" in rem, "gh-pages exists on the remote", str(rem))

        print("\n== run 2: identical inputs (expect no new commit) ==")
        before = gh_head(work)
        build(work)
        publish(work, tmpd, "run2", reseat)
        check(gh_head(work) == before, "gh-pages HEAD unchanged (no empty publish)")
        check(gh_count(work) == 1, "still exactly 1 commit", str(gh_count(work)))

        print("\n== run 3: dirty worktree must not break the switch ==")
        with open(work / "index.html", "a", encoding="utf-8") as fh:
            fh.write("\n<!-- locally modified -->\n")
        # Rebuild first, exactly as the workflow does: the build regenerates index.html, so
        # the publish step only has to cope with *incidental* dirt (e.g. line-ending churn).
        build(work)
        publish(work, tmpd, "run3", reseat)
        check(gh_head(work) == before, "dirty worktree produced no spurious publish")
        check(run(["git", "rev-parse", "--abbrev-ref", "HEAD"], work).stdout.strip() == "master",
              "harness returned to master after publishing")

        print(f"\n== run 4: a new day of data arrives ({day2}) ==")
        make_day(day2)
        build(work)
        data = json.loads((work / "site-data.json").read_text(encoding="utf-8"))
        check(data["dates"] == two_days, "page spans 2 days", str(data["dates"]))
        today_cn = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
        expected_default = max(d for d in data["dates"] if d < today_cn) if any(d < today_cn for d in data["dates"]) \
            else max(data["dates"])
        check(data["default_date"] == expected_default,
              f"default_date is the newest non-future day ({expected_default})", str(data["default_date"]))
        publish(work, tmpd, "run4", reseat, reset_fixture=False)
        check(gh_count(work) == 2, "gh-pages got a 2nd commit", str(gh_count(work)))
        check(f"data/amount-{day2}.csv" in gh_files(work), "new day's CSV is served", str(gh_files(work)))

        print("\n== run 5: data reverted to one day (stale file must be pruned) ==")
        stale = work / "data" / f"amount-{day2}.csv"
        if not stale.exists():
            make_day(day2)
        build(work)
        if json.loads((work / "site-data.json").read_text(encoding="utf-8"))["dates"] != two_days:
            raise RuntimeError("harness lost the 2-day data set before the prune test")
        stale.unlink()
        build(work)
        publish(work, tmpd, "run5", reseat, reset_fixture=False)
        check(gh_files(work) == [".nojekyll", f"data/amount-{day1}.csv", "index.html", "site-data.json"],
              "stale CSV pruned from served site", str(gh_files(work)))
        check(gh_count(work) == 3, "gh-pages got a 3rd commit", str(gh_count(work)))

        print("\n== run 6: gh-pages left stale (regression: must self-heal) ==")
        # Reproduces the bug where the site silently stopped updating: gh-pages held older
        # content while master was current, and because no *commit* happened in the run,
        # the old "publish only if this run committed" gate skipped publishing forever.
        bogus = "2099-12-31"
        (work / "data" / f"amount-{bogus}.csv").write_text(
            template.replace(day1, bogus), encoding="utf-8", newline="\n")
        build(work)
        publish(work, tmpd, "run6-stale-inject", reseat, reset_fixture=False)
        check(f"data/amount-{bogus}.csv" in gh_files(work), "gh-pages now holds the bogus extra file",
              str(gh_files(work)))
        # now revert to the 1-day set WITHOUT making any new commit, so this run has no
        # "change flag" at all — exactly the situation that used to leave the site stale.
        (work / "data" / f"amount-{bogus}.csv").unlink(missing_ok=True)
        build(work)
        gh_before = gh_head(work)
        publish(work, tmpd, "run7-heal", reseat, reset_fixture=False)
        check(gh_head(work) != gh_before, "a publish happened even though this run made no commit")
        check(gh_files(work) == [".nojekyll", f"data/amount-{day1}.csv", "index.html", "site-data.json"],
              "gh-pages self-healed back to the desired content", str(gh_files(work)))
        count_before = gh_count(work)
        publish(work, tmpd, "run8-noop", reseat)
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
        served_dates = sd["dates"]
        check(served_dates == [day1], "served data matches the source of truth", str(served_dates))
        check((serve / "data" / f"amount-{sd['default_date']}.csv").is_file(),
              "CSV referenced by the page is served alongside it")

        print("\n== real repo untouched (only asserting what this test controls) ==")
        # NOTE: assert the git status of data/, not the number of days — the repo legitimately
        # gains a file per day, and asserting a count made this test fail as data accumulated.
        status = run(["git", "status", "--porcelain", "--", "data/"], REPO).stdout.strip()
        check(status == "", "real data/ has no uncommitted changes", status)
        check((REPO / "data" / f"amount-{day1}.csv").is_file(),
              "the fixture's source file still exists in the repo")
        check(sorted(p.name for p in (REPO / "data").glob("amount-*.csv")) ==
              [f"amount-{d}.csv" for d in src_dates],
              "real data/ file set is unchanged", str(sorted(p.name for p in (REPO / "data").glob("*.csv"))))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\nRESULT: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
