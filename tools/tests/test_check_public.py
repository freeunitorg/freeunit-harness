"""Tests of tools/check-public.sh.

Run: python3 -m unittest discover -s tools/tests -v
Each test builds a temporary git repository and runs the script on it. The rules
and the allowlist come from the real tools/ directory. Fixtures that look like
secret keys are built from parts here, so that no scanner flags this file.
"""
import os, shutil, subprocess, sys, tempfile, unittest

sys.dont_write_bytecode = True
TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(TOOLS, "check-public.sh")
FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "check-public")

# Strings that the rules catch, built from parts so this file is itself clean.
IP = "8.8." + "4.4"
HOME = "/ho" + "me/someuser/x"
GHSA = "GHS" + "A-aaaa-bbbb-cccc"

SECRET_LINES = {
    "gh-token": "t=gh" + "p_" + "A" * 36 + "\n",
    "api-key": "k=AK" + "IA" + "A" * 16 + "\n",
    "private-key": "-----BEGIN " + "RSA PRIVATE KEY-----\n",
}


def run(repo, *args, env=None):
    e = dict(os.environ)
    e.pop("HARNESS_DENY_FILE", None)
    e.update(env or {})
    p = subprocess.run([SCRIPT, *args], cwd=repo, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, text=True, env=e)
    return p.returncode, p.stdout, p.stderr


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="checkpublic-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = os.path.join(self.tmp, "repo")
        os.mkdir(self.repo)
        self.git("init", "-q")

    def git(self, *a):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *a],
                       cwd=self.repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def put(self, name, text, add=True):
        path = os.path.join(self.repo, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
        if add:
            self.git("add", name)

    def put_fixture(self, rel, add=True):
        with open(os.path.join(FIX, rel)) as f:
            self.put("f.txt", f.read(), add)


class RuleTests(Base):
    def test_generic_rules_hit(self):
        for fname in sorted(os.listdir(os.path.join(FIX, "hit"))):
            rule = fname[:-4]
            with self.subTest(rule=rule):
                self.setUp()
                self.put_fixture("hit/" + fname)
                code, out, _ = run(self.repo)
                self.assertEqual(code, 1, out)
                self.assertEqual(out.split("\t")[2].strip(), rule)
                self.assertEqual(out.count("\n"), 1)

    def test_secret_shaped_rules_hit(self):
        for rule, line in SECRET_LINES.items():
            with self.subTest(rule=rule):
                self.setUp()
                self.put("f.txt", line)
                code, out, _ = run(self.repo)
                self.assertEqual(code, 1)
                self.assertEqual(out.split("\t")[2].strip(), rule)

    def test_every_deny_rule_has_a_fixture(self):
        with open(os.path.join(TOOLS, "public-deny.txt")) as f:
            ids = {l.split("\t")[0] for l in f if l.strip() and not l.startswith("#")}
        have = {f[:-4] for f in os.listdir(os.path.join(FIX, "hit"))} | set(SECRET_LINES)
        self.assertEqual(ids - have, set())

    def test_clean_passes(self):
        self.put_fixture("pass/clean.txt")
        self.assertEqual(run(self.repo)[0], 0)

    def test_allowlisted_strings_pass(self):
        self.put_fixture("pass/allowed.txt")
        self.assertEqual(run(self.repo)[0], 0)

    def test_four_part_version_is_not_in_the_allowlist(self):
        # Three-part versions pass by the regex. Only an IP-shaped string hits.
        self.put("f.txt", "v1.2.3." + "4 and 1.2.3\n")
        self.assertEqual(run(self.repo)[0], 0)
        self.put("f.txt", "see %s now\n" % IP)
        self.assertEqual(run(self.repo)[0], 1)

    def check_forms(self, rule, hits, passes):
        # One file per form, so a subtest names the form that broke.
        for text, want in [(t, 1) for t in hits] + [(t, 0) for t in passes]:
            with self.subTest(text=text):
                self.put("f.txt", text + "\n")
                code_, out, _ = run(self.repo)
                self.assertEqual((code_, out.split("\t")[2:]),
                                 (want, [rule + "\n"] if want else []))

    def test_private_dns_needs_a_host_shape(self):
        # Made-up names. A letters-only name is code unless a URL or a :port
        # marks it as a host. The email rule covers user@host. "^" stands
        # for the dot of a host name, so this file does not hit the rule.
        hosts = [t.replace("^", ".") for t in (
            "ssh to ws01^lan first", "db-1^internal:5432", "ci-7.corp^intranet",
            "http://cache^lan/x", "http://cache^lan:3142/x", "box^lan:8080",
            "nas.home^arpa", "on ws01^lan (arm64)", "WS01^LAN", "ssh build-box^lan")]
        code = ("self.internal = 1", "obj.lan=2", "import foo.internal",
                "from foo.internal import x", "return r.internal;",
                "obj.internal[0]", "a.b.internal = 3", "@pytest.mark.internal",
                "my_box.lan", "self.lan: int = 1", "foo.internal()",
                # Dotted code paths: letters only, so no label is host-shaped.
                "from foo.bar.internal import x", "import foo.bar.internal",
                "print(a.b.internal)", "return a.b.internal",
                "x = self.a.internal",
                # Each of these needs one part of the rule to pass.
                "Read ws01.lan.md now", "see ws01.lanes", "x2.internal(1)",
                "x2.lan = 1", "my_box1.lan",
                # Known losses: a letters-only name and a name with "_".
                "build.example.internal", "ssh build_box.lan")
        self.check_forms("private-dns", hosts, code)

    def test_ipv6_private_forms(self):
        # "~" stands for ":" so this file does not hit the rule.
        hosts = [t.replace("~", ":") for t in (
            "fd12~3456~789a~~1", "fe80~~1", "FE80~~1", "fe80~~",
            "fd12~3456~789a~~", "http://[fd12~3456~789a~1~2~3~~]~8080/")]
        other = ("ranges fe80::/10 and fd00::/8 and fc00::/7",
                 "2001:db8:fd12::1", "loopback ::1", "a hash cafe:1",
                 "fd12:3456")
        self.check_forms("ipv6-private", hosts, other)

    def test_output_is_masked(self):
        self.put("f.txt", "host %s and cd %s and %s\n" % (IP, HOME, GHSA))
        code, out, err = run(self.repo)
        self.assertEqual(code, 1)
        for secret in (IP, "someuser", "aaaa-bbbb"):
            self.assertNotIn(secret, out + err)
        for line in out.splitlines():
            self.assertEqual(len(line.split("\t")), 3)


class ScopeTests(Base):
    def test_untracked_needs_all(self):
        self.put("ok.txt", "fine\n")
        self.put_fixture("hit/ipv4.txt", add=False)
        self.assertEqual(run(self.repo)[0], 0)
        code, out, _ = run(self.repo, "--all")
        self.assertEqual((code, out.split("\t")[0]), (1, "f.txt"))

    def test_ignored_file_is_skipped_by_all(self):
        self.put(".gitignore", "f.txt\n")
        self.put_fixture("hit/ipv4.txt", add=False)
        self.assertEqual(run(self.repo, "--all")[0], 0)

    def test_staged_content_is_scanned(self):
        self.put("f.txt", "fine\n")
        self.git("commit", "-q", "-m", "x")
        self.put_fixture("hit/ipv4.txt")
        self.assertEqual(run(self.repo)[0], 1)

    def test_history_finds_removed_line_and_message(self):
        self.put_fixture("hit/ipv4.txt")
        self.git("commit", "-q", "-m", "add")
        self.put("f.txt", "fine\n")
        self.git("commit", "-q", "-m", "see %s in message" % IP)
        self.assertEqual(run(self.repo)[0], 0)
        code, out, _ = run(self.repo, "--history")
        self.assertEqual(code, 1)
        labels = [l.split("\t")[0] for l in out.splitlines()]
        self.assertTrue(any(l.endswith(":f.txt") for l in labels), out)
        self.assertTrue(any(l.endswith(":message") for l in labels), out)

    def test_fixture_dir_is_not_scanned(self):
        self.put_fixture("hit/ipv4.txt")
        self.put("tools/tests/check-public/hit/a.txt", IP + "\n")
        self.git("rm", "-q", "-f", "--cached", "f.txt")
        self.assertEqual(run(self.repo)[0], 0)


class DenyFileTests(Base):
    def test_private_list(self):
        self.put("f.txt", "the Zebrafish project\n")
        deny = os.path.join(self.tmp, "deny")
        with open(deny, "w") as f:
            f.write("# comment\nzebra[f]ish\n")
        self.assertEqual(run(self.repo)[0], 0)
        code, out, _ = run(self.repo, "--deny-file", deny)
        self.assertEqual(code, 1)
        self.assertNotIn("Zebra", out)
        self.assertEqual(run(self.repo, env={"HARNESS_DENY_FILE": deny})[0], 1)


# A made-up private host name. No real list holds it.
BOX = "zz-test-box-1"


class PrivateListTests(Base):
    def deny(self, *terms):
        path = os.path.join(self.tmp, "deny")
        with open(path, "w") as f:
            f.write("# private terms\n" + "".join(t + "\n" for t in terms))
        return path

    def check(self, *args, env=None):
        # The private term must never reach stdout or stderr.
        code, out, err = run(self.repo, *args, env=env)
        self.assertNotIn(BOX.lower(), (out + err).lower())
        return code, out, err

    def test_hit_in_a_doc(self):
        self.put("doc.md", "fine\nBuild it on %s tonight.\n" % BOX)
        code, out, _ = self.check("--deny-file", self.deny(BOX))
        self.assertEqual((code, out), (1, "doc.md\t2\tprivate-2\n"))
        code, out, _ = self.check(env={"HARNESS_DENY_FILE": self.deny(BOX)})
        self.assertEqual((code, out), (1, "doc.md\t2\tprivate-2\n"))

    def test_hit_in_a_path_or_host_port(self):
        for text in ("cd /srv/%s/build\n", "rsync out %s:x\n", "http://%s:8080/\n",
                     "see run/%s.log\n", "bench_%s.txt\n"):
            with self.subTest(text=text):
                self.setUp()
                self.put("doc.md", text % BOX)
                code, out, _ = self.check("--deny-file", self.deny(BOX))
                self.assertEqual((code, out), (1, "doc.md\t1\tprivate-2\n"))

    def test_case_variant(self):
        self.put("doc.md", "host ZZ-Test-Box-1 is up\n")
        code, out, _ = self.check("--deny-file", self.deny(BOX))
        self.assertEqual((code, out), (1, "doc.md\t1\tprivate-2\n"))

    def test_skipped_without_a_deny_file(self):
        self.put("doc.md", "Build it on %s tonight.\n" % BOX)
        self.assertEqual(self.check()[:2], (0, ""))
        self.assertEqual(self.check(env={"HARNESS_DENY_FILE": ""})[:2], (0, ""))


class UsageTests(Base):
    def test_exit_2(self):
        self.assertEqual(run(self.repo, "--bogus")[0], 2)
        self.assertEqual(run(self.repo, "--deny-file")[0], 2)
        self.assertEqual(run(self.repo, "--deny-file", "/nonexistent/x")[0], 2)
        self.assertEqual(run(self.tmp)[0], 2)  # not a git repository

    def test_deny_file_not_utf8(self):
        deny = os.path.join(self.tmp, "deny")
        with open(deny, "wb") as f:
            f.write(b"caf\xe9\n")
        code, out, err = run(self.repo, "--deny-file", deny)
        self.assertEqual((code, out), (2, ""))
        self.assertIn("not UTF-8", err)
        self.assertNotIn("Traceback", err)


if __name__ == "__main__":
    unittest.main()
