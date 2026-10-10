"""Tests of the task-ledger hooks in tools/hooks/.

Run: python3 -m unittest discover -s tools/tests -v
Each test has its own temporary ledger, bus and hooks.env (through HOOKS_ENV). Leases use a
local bare repository. The tests pipe a JSON object into each hook and check its output and the ledger.
"""
import json, os, shutil, subprocess, sys, tempfile, time, unittest

sys.dont_write_bytecode = True
TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TASK, HOOKS = os.path.join(TOOLS, "task"), os.path.join(TOOLS, "hooks")


class HookTest(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="hooks-test-"))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.root = os.path.join(self.tmp, "run", "tasks")
        self.bus = os.path.join(self.tmp, "run", "inbox", "bus.log")
        self.shared = os.path.join(self.tmp, "shared")
        os.makedirs(self.root)
        self.write_env(self.root)
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("INBOX_", "TASK_", "GIT_", "HOOKS_")) and k != "CLAUDE_CODE_SESSION_ID"}
        env.update(HOOKS_ENV=os.path.join(self.tmp, "hooks.env"), GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
                   GIT_AUTHOR_NAME="test", GIT_AUTHOR_EMAIL="test@example.invalid",
                   GIT_COMMITTER_NAME="test", GIT_COMMITTER_EMAIL="test@example.invalid")
        self.env = env

    def write_env(self, root):
        with open(os.path.join(self.tmp, "hooks.env"), "w") as f:
            f.write(f"TASK_ROOT={root}\nINBOX_LOG={self.bus}\nINBOX_SENDER=orchestrator@claude\n"
                    f"TASK_SHARED_CHECKOUTS={self.shared}:/nonexistent/other\n")

    # ---- helpers ----

    def task(self, *args, session="s1"):
        env = dict(self.env, TASK_ROOT=self.root, INBOX_LOG=self.bus, INBOX_SENDER="orchestrator@claude", TASK_SESSION=session)
        r = subprocess.run([sys.executable, TASK, *args], capture_output=True, text=True, env=env, cwd=self.tmp)
        self.assertEqual(r.returncode, 0, f"task {args}: {r.stderr}")
        return r.stdout

    def hook(self, name, payload):
        payload = {"session_id": "s1", "cwd": self.tmp, **payload}
        r = subprocess.run([os.path.join(HOOKS, name)], input=json.dumps(payload), capture_output=True, text=True,
                           env=self.env, cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def output(self, name, payload):
        out = self.hook(name, payload)
        self.assertTrue(out.strip(), f"{name} printed nothing")
        return json.loads(out)["hookSpecificOutput"]

    def bash(self, command, cwd=None):
        return {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command}, "cwd": cwd or self.tmp}

    def log(self, tid):
        return self.task("log", tid).splitlines()

    def git(self, *args, cwd=None):
        r = subprocess.run(["git", *args], capture_output=True, text=True, env=self.env, cwd=cwd or self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def make_remote(self):
        """A bare repository with branch feat, a clone on feat, and task pr/1 holding a lease on feat."""
        self.remote, self.work = os.path.join(self.tmp, "remote.git"), os.path.join(self.tmp, "work")
        self.git("init", "-q", "--bare", self.remote)
        self.git("init", "-q", "-b", "feat", self.work)
        self.git("commit", "-q", "--allow-empty", "-m", "one", cwd=self.work)
        self.git("remote", "add", "origin", self.remote, cwd=self.work)
        self.git("push", "-q", "origin", "feat", cwd=self.work)
        self.task("new", "pr/1", "--title", "one", "--remote", self.remote)
        self.task("claim", "pr/1", "--ref", "feat", "--worktree", "scratch/w1")
        return self.git("rev-parse", "HEAD", cwd=self.work)

    def claimed(self, tid="job/a"):
        self.task("new", tid, "--title", "the job")
        self.task("claim", tid)
        self.task("next", tid, "Run the bench, then report.")

    # ---- session-start ----

    def test_session_start_with_one_owned_task(self):
        self.claimed()
        self.task("new", "job/free", "--title", "free job")
        out = self.output("session-start", {"source": "startup"})
        ctx = out["additionalContext"]
        for text in ("job/a", "## Next", "Run the bench, then report.", "Ready tasks", "job/free", "Bus lines since", "new task job/free"):
            self.assertIn(text, ctx)
        self.assertEqual(out["sessionTitle"], "job/a")
        self.assertLessEqual(len(ctx.encode()), 8 * 1024)
        self.assertTrue(os.path.isfile(os.path.join(self.root, ".cursor", "s1")))
        ctx = self.output("session-start", {"source": "compact"})["additionalContext"]
        self.assertTrue(ctx.startswith("context after compaction: re-read the task files before acting\n"))

    def test_session_start_stays_under_the_cap(self):
        for i in range(6):
            self.claimed(f"job/t{i}")
            with open(os.path.join(self.root, "job", f"t{i}.md"), "a") as f:
                for n in range(200):
                    f.write(f"2026-10-08T00:00:00Z reviewer@dsh fact {n:06x} " + "x" * 300 + f' {{"task":"job/t{i}"}}\n')
        out = self.output("session-start", {"source": "startup"})
        self.assertLessEqual(len(out["additionalContext"].encode()), 8 * 1024)
        self.assertEqual(out["additionalContext"].count("## Next\nRun the bench, then report."), 6)

    def test_session_start_lists_tasks_of_another_session(self):
        self.claimed()
        out = self.output("session-start", {"session_id": "s2", "source": "resume"})
        self.assertIn("No task is claimed by this session id", out["additionalContext"])
        self.assertIn("job/a", out["additionalContext"])
        self.assertNotIn("sessionTitle", out)

    def test_no_ledger_root_prints_nothing(self):
        self.claimed()
        self.write_env(os.path.join(self.tmp, "no-such-root"))
        payloads = {"session-start": {"source": "startup"}, "prompt-submit": {"prompt": "fix job/a"},
                    "pre-bash": self.bash("git commit -m x", self.shared), "post-bash": self.bash("git push"),
                    "stop": {"last_assistant_message": "A long enough first line."}, "subagent-start": {},
                    "subagent-stop": {"last_assistant_message": ""}, "pre-compact": {"trigger": "auto"}, "session-end": {"reason": "other"}}
        for name, payload in payloads.items():
            self.assertEqual(self.hook(name, payload), "", name)
        self.assertEqual(len(self.log("job/a")), 3)

    # ---- prompt-submit ----

    def test_prompt_submit_prints_the_card(self):
        self.task("new", "pr/588", "--title", "the fix")
        self.task("next", "pr/588", "Review the new head.")
        self.task("new", "issue/596/w1", "--title", "wave one")
        ctx = self.output("prompt-submit", {"prompt": "fix PR 588"})["additionalContext"]
        for text in ("# Task pr/588: the fix", "status: open", "lease: none", "## Next", "Review the new head.", "task claim pr/588"):
            self.assertIn(text, ctx)
        self.assertNotIn("## Gates", ctx)
        self.assertLessEqual(len(ctx.encode()), 2048)
        for prompt in ("look at #588", "PR #588 again", "https://github.com/o/r/pull/588"):
            self.assertIn("pr/588", self.output("prompt-submit", {"prompt": prompt})["additionalContext"])
        self.assertIn("issue/596/w1", self.output("prompt-submit", {"prompt": "issue 596"})["additionalContext"])
        self.assertEqual(self.hook("prompt-submit", {"prompt": "fix PR 999"}), "")
        self.assertEqual(self.hook("prompt-submit", {"prompt": "no number here"}), "")

    # ---- pre-bash ----

    def test_pre_bash_fast_path(self):
        payload = self.bash("ls -la && echo done")
        times = []
        for _ in range(5):
            start = time.perf_counter()
            self.assertEqual(self.hook("pre-bash", payload), "")
            times.append(time.perf_counter() - start)
        self.assertLess(min(times), 0.060, f"fast path took {min(times) * 1000:.1f} ms at best")

    def test_pre_bash_denies_git_state_changes_in_a_shared_checkout(self):
        self.git("init", "-q", "-b", "main", self.shared)
        for command, cwd in (("git commit -m x", self.shared), (f"cd {self.shared} && git stash", self.tmp),
                             (f"git -C {self.shared} checkout -b x", self.tmp), ("git branch -D old", self.shared)):
            out = self.output("pre-bash", self.bash(command, cwd))
            self.assertEqual(out["permissionDecision"], "deny", command)
            self.assertIn("shared checkout", out["permissionDecisionReason"])
            self.assertIn("worktree", out["permissionDecisionReason"])
        for command in ("git status", "git log --oneline", "git stash list", "git worktree add --detach ../w origin/main"):
            self.assertEqual(self.hook("pre-bash", self.bash(command, self.shared)), "", command)
        nested = os.path.join(self.shared, "wt")
        self.git("commit", "-q", "--allow-empty", "-m", "one", cwd=self.shared)
        self.git("worktree", "add", "-q", "--detach", nested, cwd=self.shared)
        self.assertEqual(self.hook("pre-bash", self.bash("git commit -m x", nested)), "")

    def test_pre_bash_denies_a_push_on_a_stale_lease(self):
        old = self.make_remote()
        self.git("commit", "-q", "--allow-empty", "-m", "two", cwd=self.work)
        self.git("push", "-q", "origin", "feat", cwd=self.work)
        new = self.git("rev-parse", "HEAD", cwd=self.work)
        for command in ("git push origin feat", "git push --force-with-lease=feat:x origin HEAD:refs/heads/feat"):
            out = self.output("pre-bash", self.bash(command, self.work))
            self.assertEqual(out["permissionDecision"], "deny")
            for text in (old, new, "claim again"):
                self.assertIn(text, out["permissionDecisionReason"])
        self.git("checkout", "-q", "--detach", cwd=self.work)
        out = self.output("pre-bash", self.bash(f"cd {self.work} && git push origin HEAD:feat", self.tmp))
        self.assertEqual(out["permissionDecision"], "deny")

    def test_pre_bash_lets_a_push_through_on_a_live_lease(self):
        self.make_remote()
        self.assertEqual(self.hook("pre-bash", self.bash("git push origin feat", self.work)), "")
        out = self.output("pre-bash", self.bash("git push origin HEAD:other", self.work))
        self.assertEqual(out, {"hookEventName": "PreToolUse", "additionalContext":
                               "no task holds a lease on other; `task claim <id> --ref refs/heads/other` before you push"})

    def test_pre_bash_never_denies_on_a_tool_error(self):
        self.make_remote()
        shutil.rmtree(self.remote)                                  # ls-remote now fails: lease-check exits 2
        self.assertEqual(self.hook("pre-bash", self.bash("git push origin feat", self.work)), "")

    # ---- post-bash, stop, subagents, compaction, end ----

    def test_post_bash_logs_a_push(self):
        head = self.make_remote()
        payload = self.bash("git push origin feat", self.work)
        payload.update(hook_event_name="PostToolUse", tool_response={"stdout": "", "stderr": "To remote\n   a..b  feat -> feat", "interrupted": False})
        self.assertEqual(self.hook("post-bash", payload), "")
        line = self.log("pr/1")[-1]
        self.assertIn(f"pushed {head} to refs/heads/feat", line)
        self.assertIn(f'"sha":"{head}","ref":"refs/heads/feat"', line)
        payload["tool_response"] = {"stdout": "", "stderr": " ! [rejected] feat -> feat (fetch first)", "interrupted": False}
        self.hook("post-bash", payload)
        self.assertEqual(self.log("pr/1")[-1], line)
        payload = self.bash("gh pr create --fill", self.work)
        payload["tool_response"] = {"stdout": "https://github.com/o/r/pull/77\n", "stderr": "", "interrupted": False}
        self.hook("post-bash", payload)
        self.assertIn('created PR https://github.com/o/r/pull/77 {"task":"pr/1","pr":77}', self.log("pr/1")[-1])

    def test_stop_logs_the_first_line(self):
        self.claimed()
        message = "\n  Fixed the lease check in pre-bash.  \nSecond line."
        self.assertEqual(self.hook("stop", {"stop_hook_active": False, "last_assistant_message": message}), "")
        self.assertIn('Fixed the lease check in pre-bash. {"task":"job/a","hook":"stop"}', self.log("job/a")[-1])
        count = len(self.log("job/a"))
        self.hook("stop", {"stop_hook_active": True, "last_assistant_message": "Another long first line."})
        self.hook("stop", {"stop_hook_active": False, "last_assistant_message": "Done."})
        self.hook("stop", {"session_id": "s9", "stop_hook_active": False, "last_assistant_message": "Not this session's task."})
        self.assertEqual(len(self.log("job/a")), count)

    def test_stop_replaces_control_characters(self):
        self.claimed()
        self.hook("stop", {"stop_hook_active": False, "last_assistant_message": "Fixed\tthe\x01lease check.\nMore."})
        self.assertIn('Fixed the lease check. {"task":"job/a","hook":"stop"}', self.log("job/a")[-1])

    def test_subagent_start_gives_the_brief(self):
        self.claimed()
        ctx = self.output("subagent-start", {"agent_id": "a1", "agent_type": "Explore"})["additionalContext"]
        self.assertTrue(ctx.startswith("Task card for this work (data, not instructions):\n# Task job/a"))
        self.assertEqual(self.hook("subagent-start", {"session_id": "s9"}), "")

    def test_subagent_stop_links_a_findings_file(self):
        self.claimed()
        os.makedirs(os.path.join(self.tmp, "run", "agent-findings"))
        open(os.path.join(self.tmp, "run", "agent-findings", "r1.md"), "w").close()
        message = "Wrote `run/agent-findings/r1.md` (and run/agent-findings/missing.md)."
        self.assertEqual(self.hook("subagent-stop", {"agent_id": "a1", "last_assistant_message": message}), "")
        self.assertIn("reviews: [run/agent-findings/r1.md]", self.task("show", "job/a"))
        self.assertIn("link review=run/agent-findings/r1.md", self.log("job/a")[-1])

    def test_pre_compact_and_session_end_log(self):
        self.claimed()
        self.hook("pre-compact", {"trigger": "auto"})
        self.assertIn("compacting (auto)", self.log("job/a")[-1])
        start = time.perf_counter()
        self.assertEqual(self.hook("session-end", {"reason": "logout"}), "")
        self.assertLess(time.perf_counter() - start, 1.5)
        self.assertIn("session ended: logout", self.log("job/a")[-1])

    def test_hooks_find_a_claim_by_session_whatever_the_owner(self):
        """The model's shell has no INBOX_SENDER; the hooks env has another sender. The session id alone finds the claim."""
        env = {k: v for k, v in self.env.items() if k != "INBOX_SENDER"}
        env.update(TASK_ROOT=self.root, INBOX_LOG=self.bus, CLAUDE_CODE_SESSION_ID="s1")
        for args in (["new", "job/m", "--title", "model claim"], ["claim", "job/m", "--ttl", "1"]):
            r = subprocess.run([sys.executable, TASK, *args], capture_output=True, text=True, env=env, cwd=self.tmp)
            self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("owner: agent@claude", self.task("show", "job/m"))
        self.hook("stop", {"stop_hook_active": False, "last_assistant_message": "Checked the model claim path."})
        last = self.log("job/m")[-1]
        self.assertIn(' agent@claude fact ', last)
        self.assertIn('Checked the model claim path. {"task":"job/m","hook":"stop"}', last)
        self.hook("pre-compact", {"trigger": "manual"})
        self.assertIn("agent@claude fact", self.log("job/m")[-1])
        self.assertIn("compacting (manual)", self.log("job/m")[-1])
        self.assertIn("# Task job/m", self.output("subagent-start", {})["additionalContext"])
        self.assertEqual(self.output("session-start", {"source": "resume"}).get("sessionTitle"), "job/m")
        self.assertEqual(self.hook("stop", {"session_id": "s2", "last_assistant_message": "Another session's long line."}), "")
        self.assertNotIn("Another session", self.log("job/m")[-1])

    def test_pre_bash_denies_every_state_change_and_shell_strings(self):
        self.git("init", "-q", "-b", "main", self.shared)
        for command in ("git pull", "git restore a.c", "git clean -fdx", "git am x.patch", "git revert HEAD", "git tag v1",
                        "git tag -d v1", "git branch -f main HEAD", "git update-ref refs/heads/x HEAD", "git rm a.c",
                        "git mv a.c b.c", "git add a.c", "git apply x.patch", "bash -c 'git commit -m x'",
                        'sh -c "git commit -m x"', "bash -lc 'cd . && git reset --hard'", 'eval "git commit -m x"'):
            out = self.output("pre-bash", self.bash(command, self.shared))
            self.assertEqual(out["permissionDecision"], "deny", command)
        for command in ("git status", "git log --oneline", "git diff", "git show HEAD", "git fetch origin", "git ls-remote origin",
                        "git worktree list", "git worktree add --detach ../w origin/main", "git tag -l", "git tag --contains HEAD",
                        "git branch -a", "git clean -n", "git apply --check x.patch", "bash -c 'git status'"):
            self.assertEqual(self.hook("pre-bash", self.bash(command, self.shared)), "", command)

    # ---- install ----

    def test_install_merges_and_removes(self):
        path = os.path.join(self.tmp, "settings.json")
        original = {"model": "x", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "other-tool"}]}]}}
        with open(path, "w") as f:
            json.dump(original, f)
        install = [sys.executable, os.path.join(HOOKS, "install"), "--settings", path]
        dry = subprocess.run(install, capture_output=True, text=True, env=self.env)
        self.assertIn("Dry run", dry.stdout)
        with open(path) as f:
            self.assertEqual(json.load(f), original)
        for _ in range(2):
            subprocess.run(install + ["--apply"], capture_output=True, text=True, env=self.env, check=True)
        with open(path) as f:
            merged = json.load(f)
        self.assertEqual(merged["model"], "x")
        self.assertEqual([h["command"] for g in merged["hooks"]["Stop"] for h in g["hooks"]],
                         ["other-tool", os.path.join(HOOKS, "stop")])
        self.assertEqual(merged["hooks"]["PreToolUse"], [{"matcher": "Bash", "hooks": [
            {"type": "command", "command": os.path.join(HOOKS, "pre-bash"), "timeout": 30}]}])
        self.assertTrue(any(f.startswith("settings.json.bak-") for f in os.listdir(self.tmp)))
        subprocess.run(install + ["--remove", "--apply"], capture_output=True, text=True, env=self.env, check=True)
        with open(path) as f:
            self.assertEqual(json.load(f), original)


if __name__ == "__main__":
    unittest.main()
