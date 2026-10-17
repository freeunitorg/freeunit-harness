import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SHELL_CHECK = os.path.join(ROOT, "tools", "gates", "shell_check")


class ShellCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="g8-test-")
        self.addCleanup(shutil.rmtree, self.tmp)

    def git_repo(self, name="repo"):
        repo = os.path.join(self.tmp, name)
        os.mkdir(repo)
        subprocess.run(["git", "init", "-q", repo], check=True)
        subprocess.run(["git", "-C", repo, "config", "user.name", "t"], check=True)
        subprocess.run(["git", "-C", repo, "config", "user.email", "t@example.invalid"], check=True)
        return repo

    def run_check(self, repo, env=None):
        return subprocess.run(
            ["sh", SHELL_CHECK, repo],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )

    def test_non_git_directory_exits_2(self):
        nogit = os.path.join(self.tmp, "nogit")
        os.mkdir(nogit)
        with open(os.path.join(nogit, "a.sh"), "w") as f:
            f.write("#!/bin/sh\nexit 0\n")
        r = self.run_check(nogit)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("not a git checkout", r.stderr)

    def test_repo_without_sh_scripts_exits_2(self):
        repo = self.git_repo()
        with open(os.path.join(repo, "README"), "w") as f:
            f.write("hello\n")
        subprocess.run(["git", "-C", repo, "add", "README"], check=True)
        subprocess.run(["git", "-C", repo, "commit", "-q", "-m", "init"], check=True)
        r = self.run_check(repo)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("nothing to check", r.stderr)

    def test_symlinks_and_deleted_tracked_files_do_not_truncate_list(self):
        repo = self.git_repo()
        os.mkdir(os.path.join(repo, "pkg"))
        with open(os.path.join(repo, "pkg", "keep"), "w") as f:
            f.write("x\n")
        # Names chosen so the symlinks and deleted file sort before z-last.sh.
        with open(os.path.join(repo, "a-first.sh"), "w") as f:
            f.write("#!/bin/sh\nexit 0\n")
        os.symlink("pkg", os.path.join(repo, "b-dir-link"))
        os.symlink("missing-target", os.path.join(repo, "c-broken-link"))
        with open(os.path.join(repo, "d-deleted.sh"), "w") as f:
            f.write("#!/bin/sh\nexit 0\n")
        with open(os.path.join(repo, "z-last.sh"), "w") as f:
            f.write('#!/bin/sh\necho "$x" | jq .\n')
        os.symlink("z-last.sh", os.path.join(repo, "e-script-link.sh"))

        subprocess.run(
            ["git", "-C", repo, "add", "pkg/keep", "a-first.sh", "b-dir-link",
             "c-broken-link", "d-deleted.sh", "e-script-link.sh", "z-last.sh"],
            check=True,
        )
        subprocess.run(["git", "-C", repo, "commit", "-q", "-m", "init"], check=True)
        os.remove(os.path.join(repo, "d-deleted.sh"))

        r = self.run_check(repo)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("G8: 2 #!/bin/sh scripts\n", r.stdout)
        self.assertIn("WARN: z-last.sh:2:echo \"$x\" | jq . [strong: echo of a variable into a parser]\n", r.stdout)
        self.assertIn("G8: 1 warning(s);", r.stdout)

    def test_leading_dash_filename_and_trap_greps(self):
        repo = self.git_repo()
        with open(os.path.join(repo, "-dash.sh"), "w") as f:
            f.write(
                "#!/bin/sh\n"
                "if (set -o pipefail) 2>/dev/null; then\n"
                "    set -o pipefail\n"
                "fi\n"
                "trap 'rm -f /tmp/x' EXIT\n"
                "echo \"$v\" | cat | grep -q x\n"
            )
        subprocess.run(["git", "-C", repo, "add", "--", "-dash.sh"], check=True)
        subprocess.run(["git", "-C", repo, "commit", "-q", "-m", "init"], check=True)

        r = self.run_check(repo)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("G8: 1 #!/bin/sh scripts\n", r.stdout)
        self.assertIn("WARN: -dash.sh:6:echo \"$v\" | cat | grep -q x [echo of a variable into a pipe]\n", r.stdout)
        self.assertIn("WARN: -dash.sh:6:echo \"$v\" | cat | grep -q x [grep -q in a pipe under pipefail]\n", r.stdout)
        self.assertIn("WARN: -dash.sh:5:trap 'rm -f /tmp/x' EXIT [EXIT trap without HUP/INT/TERM traps]\n", r.stdout)
        self.assertIn("G8: 3 warning(s);", r.stdout)

    def test_docker_stderr_does_not_become_warn_lines(self):
        repo = self.git_repo()
        with open(os.path.join(repo, "ok.sh"), "w") as f:
            f.write("#!/bin/sh\nexit 0\n")
        subprocess.run(["git", "-C", repo, "add", "ok.sh"], check=True)
        subprocess.run(["git", "-C", repo, "commit", "-q", "-m", "init"], check=True)

        bin_dir = os.path.join(self.tmp, "bin")
        os.mkdir(bin_dir)
        for cmd in ("git", "sh", "dash", "mktemp", "wc", "tr", "grep", "head", "sed"):
            for d in os.environ.get("PATH", "/usr/bin:/bin").split(os.pathsep):
                p = os.path.join(d, cmd)
                if os.path.exists(p):
                    os.symlink(p, os.path.join(bin_dir, cmd))
                    break
        docker_stub = os.path.join(bin_dir, "docker")
        with open(docker_stub, "w") as f:
            f.write(
                "#!/bin/sh\n"
                "echo 'Unable to find image alpine:3.22 locally' >&2\n"
                "echo 'ok.sh: ash -n: ./ok.sh: line 2: fake syntax error'\n"
                "exit 0\n"
            )
        os.chmod(docker_stub, 0o755)

        env = dict(os.environ, PATH=bin_dir)
        r = self.run_check(repo, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("Unable to find image", r.stdout)
        self.assertIn("WARN: ok.sh: ash -n: ./ok.sh: line 2: fake syntax error\n", r.stdout)
        self.assertIn("G8: 1 warning(s);", r.stdout)


if __name__ == "__main__":
    unittest.main()
