"""Tests of tools/check-trailers.py against tools/reviewers.txt."""
import importlib.util, os, subprocess, sys, unittest

sys.dont_write_bytecode = True
TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(TOOLS, "check-trailers.py")
spec = importlib.util.spec_from_file_location("check_trailers", SCRIPT)
ct = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ct)
CANON, ALIAS = ct.load()
ADDR = ct.ADDR


class TrailerTest(unittest.TestCase):
    def problems(self, text):
        return ct.check(text, CANON, ALIAS)

    def test_clean_commit(self):
        msg = "lib: fix\n\nBody.\n\nCo-Authored-By: Claude Opus 5.5 %s\nReviewed-by: Claude Fable 5.1 %s\nReviewed-by: DeepSeek V4.1 Flash\n" % (ADDR, ADDR)
        self.assertEqual(self.problems(msg), [])

    def test_alias_is_rejected_with_canonical_hint(self):
        p = self.problems("x\n\nReviewed-by: DeepSeek (deepseek-flash)\n")
        self.assertEqual(len(p), 1)
        self.assertIn("'DeepSeek V4.1 Flash'", p[0])

    def test_plain_deepseek_is_an_alias(self):
        self.assertIn("alias", self.problems("x\n\nReviewed-by: DeepSeek\n")[0])

    def test_claude_needs_address(self):
        self.assertIn("needs", self.problems("x\n\nCo-Authored-By: Claude Fable 5.1\n")[0])

    def test_non_claude_takes_no_address(self):
        self.assertIn("no address", self.problems("x\n\nReviewed-by: Codex %s\n" % ADDR)[0])

    def test_unknown_model(self):
        self.assertIn("unknown model", self.problems("x\n\nReviewed-by: Llama 9\n")[0])

    def test_claude_session_rejected(self):
        self.assertIn("Claude-Session", self.problems("x\n\nClaude-Session: abc\n")[0])

    def test_blank_line_inside_block(self):
        p = self.problems("x\n\nCo-Authored-By: Claude Opus 5.5 %s\n\nReviewed-by: Codex\n" % ADDR)
        self.assertTrue(any("blank line" in q for q in p), p)

    def test_key_spelling(self):
        self.assertIn("spell the key", self.problems("x\n\nCo-authored-by: Claude Opus 5.5 %s\n" % ADDR)[0])

    def test_comment_block_with_harness_line(self):
        self.assertEqual(self.problems("Review.\n\nReviewed-by: DeepSeek V4.1 Flash\nHarness: DeepSeek Harness\nTools: bash, read\n"), [])

    def test_harness_suffix_is_accepted(self):
        self.assertEqual(self.problems("x\n\nReviewed-by: DeepSeek V4.1 Flash (DSH)\n"), [])
        self.assertEqual(self.problems("x\n\nCo-Authored-By: Muse Spark 1.3 (Pi)\n"), [])
        self.assertEqual(self.problems("x\n\nReviewed-by: Claude Fable 5.1 (Claude Code) %s\n" % ADDR), [])

    def test_harness_suffix_still_checks_the_model(self):
        self.assertIn("unknown model", self.problems("x\n\nReviewed-by: Llama 9 (DSH)\n")[0])
        self.assertIn("alias", self.problems("x\n\nReviewed-by: DeepSeek (DSH)\n")[0])
        self.assertIn("needs", self.problems("x\n\nReviewed-by: Claude Fable 5.1 (Claude Code)\n")[0])

    def test_alias_with_parentheses_is_still_an_alias(self):
        p = self.problems("x\n\nReviewed-by: DeepSeek (deepseek-flash)\n")
        self.assertEqual(len(p), 1)
        self.assertIn("alias", p[0])

    def test_cli_file_and_exit_codes(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("x\n\nReviewed-by: OpenAI Codex\n")
        r = subprocess.run([sys.executable, SCRIPT, "-f", f.name], capture_output=True, text=True)
        os.unlink(f.name)
        self.assertEqual(r.returncode, 1)
        self.assertIn("'Codex'", r.stdout)
        r = subprocess.run([sys.executable, SCRIPT, "-"], input="x\n\nReviewed-by: Codex\n", capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_claude_version_stands_in_for_the_address(self):
        self.assertEqual(self.problems("x\n\nCo-Authored-By: Claude Opus 5.5 (claude 2.1.302)\n"), [])
        self.assertEqual(self.problems("x\n\nCo-Authored-By: Claude Opus 5.5 (claude 2.1.302) %s\n" % ADDR), [])

    def test_claude_version_must_be_full(self):
        self.assertIn("needs", self.problems("x\n\nCo-Authored-By: Claude Opus 5.5 (claude 2.1)\n")[0])
        self.assertIn("needs", self.problems("x\n\nCo-Authored-By: Claude Opus 5.5 (agy)\n")[0])

    def test_text_after_the_block(self):
        p = self.problems("x\n\nReviewed-by: Codex\n\nThanks.\n")
        self.assertEqual(p, ["line 5: text after the trailer block; trailers go last"])

    def comment(self, text):
        return ct.check(text, CANON, ALIAS, comment=True)

    def test_comment_rejects_an_address(self):
        self.assertIn("a comment takes no address", self.comment("x\n\nReviewed-by: Claude Fable 5.1 %s\n" % ADDR)[0])
        self.assertIn("a comment takes no address", self.comment("x\n\nReviewed-by: Claude Fable 5.1 noreply@anthropic.com\n")[0])

    def test_comment_rejects_an_address_in_parentheses(self):
        p = self.comment("x\n\nReviewed-by: Claude Fable 5.1 (%s)\n" % ADDR)
        self.assertEqual(p, ["line 3: a comment takes no address"])

    def test_comment_takes_no_address_for_others(self):
        self.assertIn("no address", self.comment("x\n\nReviewed-by: DeepSeek V4.1 Flash %s\n" % ADDR)[0])

    def test_comment_without_address(self):
        self.assertEqual(self.comment("x\n\nReviewed-by: Claude Fable 5.1\n"), [])
        self.assertEqual(self.comment("x\n\nReviewed-by: Claude Fable 5.1 (claude 2.1.302)\n"), [])
        self.assertEqual(self.comment("x\n\nReviewed-by: DeepSeek V4.1 Flash (DSH)\n"), [])

    def test_cli_comment_usage_and_missing_file(self):
        for args in (["-c"], ["-f"], ["-c", "/nonexistent/comment.md"], ["-f", "/nonexistent/msg.txt"]):
            r = subprocess.run([sys.executable, SCRIPT] + args, capture_output=True, text=True)
            self.assertEqual(r.returncode, 2, (args, r.stderr))
            self.assertNotIn("Traceback", r.stderr)

    def test_cli_missing_list(self):
        import shutil, tempfile
        with tempfile.TemporaryDirectory() as d:
            shutil.copy(SCRIPT, d)
            r = subprocess.run([sys.executable, os.path.join(d, "check-trailers.py"), "-"], input="x\n\nReviewed-by: Codex\n", capture_output=True, text=True)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("reviewers.txt", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_cli_comment_on_stdin(self):
        r = subprocess.run([sys.executable, SCRIPT, "-c", "-"], input="x\n\nReviewed-by: Claude Fable 5.1 %s\n" % ADDR, capture_output=True, text=True)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("a comment takes no address", r.stdout)
        r = subprocess.run([sys.executable, SCRIPT, "-c", "-"], input="x\n\nReviewed-by: Claude Fable 5.1\n", capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    # The harness agreement (2026-10-23): any harness string passes, an undisclosed
    # one may be empty, and only the model name is checked against the list.
    def test_empty_harness_is_undisclosed(self):
        self.assertEqual(self.comment("x\n\nReviewed-by: Codex ()\n"), [])
        self.assertEqual(self.problems("x\n\nReviewed-by: Codex ()\n"), [])
        self.assertEqual(self.problems("x\n\nCo-Authored-By: Claude Opus 5.5 () %s\n" % ADDR), [])

    def test_comment_harness_may_hold_an_at_sign(self):
        self.assertEqual(self.comment("x\n\nReviewed-by: Codex (@deepseek-ai/dsh 0.2.1-rc.2)\n"), [])
        self.assertEqual(self.comment("x\n\nReviewed-by: Codex (agent@lab 1.0)\n"), [])
        # The negative half: an email address, a "<...>" or an "@" in the model still fails.
        for v in ("Codex (agent@example.com 1.0)", "Codex noreply@anthropic.com",
                  "Claude Fable 5.1 (dsh <x>)", "Codex (<agent@lab>)", "Codex@lab (dsh 1.0)"):
            self.assertEqual(self.comment("x\n\nReviewed-by: %s\n" % v),
                             ["line 3: a comment takes no address"], v)

    def test_comment_rejects_every_email_form(self):
        # A quoted local part, a Unicode local part, Unicode domains and address literals.
        # "\u30c6\u30b9\u30c8" is the reserved IDN test TLD.
        for v in ('Codex ("agent name"@example.com 1.0)', "Codex (\u7528\u6237@example.com 1.0)",
                  "Codex (agent@b\u00fccher.example 1.0)", "Codex (agent@example.\u30c6\u30b9\u30c8 1.0)",
                  "Codex (jane@[192.0.2.1] 1.0)", "Codex (jane@[IPv6:2001:db8::1] 1.0)",
                  "Codex (mailto:agent@example.org)"):
            self.assertEqual(self.comment("x\n\nReviewed-by: %s\n" % v),
                             ["line 3: a comment takes no address"], v)
        # The positive half: an "@" with no email-shaped domain after it still passes.
        for v in ("Codex (agent@lab 1.0)", "Codex (x@y 1.0)", "Codex (@deepseek-ai/dsh 0.2.1-rc.2)",
                  "Codex (@deepseek-ai/dsh@0.2.1-rc.2)", "Codex (dsh@0.2.1)"):
            self.assertEqual(self.comment("x\n\nReviewed-by: %s\n" % v), [], v)

    def test_any_whitespace_before_the_harness(self):
        # A tab, a no-break space and two spaces all separate the model from the harness.
        for sep in ("\t", "\u00a0", "  "):
            v = "Codex%s(dsh 1.0)" % sep
            self.assertEqual(self.problems("x\n\nReviewed-by: %s\n" % v), [], repr(v))
            self.assertEqual(self.comment("x\n\nReviewed-by: %s\n" % v), [], repr(v))
            v = "DeepSeek V4.1 Flash%s(DSH) (dsh 0.2.1-rc.2)" % sep
            self.assertEqual(self.comment("x\n\nReviewed-by: %s\n" % v), [], repr(v))
            v = "Llama 9%s(my (fork) 1.0)" % sep
            self.assertEqual(self.comment("x\n\nReviewed-by: %s\n" % v),
                             ["line 3: unknown model 'Llama 9'; add it to tools/reviewers.txt"], repr(v))

    def test_harness_with_nested_parentheses(self):
        self.assertEqual(self.comment("x\n\nReviewed-by: DeepSeek V4.1 Flash (DSH) (dsh 0.2.1-rc.2)\n"), [])
        self.assertEqual(self.comment("x\n\nReviewed-by: Codex (my (fork) 1.0)\n"), [])
        self.assertEqual(self.problems("x\n\nReviewed-by: Codex (my (fork) 1.0)\n"), [])

    def test_nested_harness_still_checks_the_model(self):
        p = self.comment("x\n\nReviewed-by: DeepSeek (DSH) (dsh 0.2.1-rc.2)\n")
        self.assertEqual(p, ["line 3: 'DeepSeek' is an alias; use 'DeepSeek V4.1 Flash'"])
        p = self.comment("x\n\nReviewed-by: Llama 9 (my (fork) 1.0)\n")
        self.assertEqual(p, ["line 3: unknown model 'Llama 9'; add it to tools/reviewers.txt"])
        p = self.comment("x\n\nReviewed-by: codex (my (fork) 1.0)\n")
        self.assertEqual(p, ["line 3: 'codex' should be spelled 'Codex'"])

    def test_custom_harness_and_claude_address_in_a_commit(self):
        self.assertEqual(self.problems("x\n\nCo-Authored-By: Claude Opus 5.5 (custom-loop 7) %s\n" % ADDR), [])
        self.assertEqual(self.problems("x\n\nCo-Authored-By: Claude Opus 5.5 (my (fork) 1.0) %s\n" % ADDR), [])
        p = self.problems("x\n\nCo-Authored-By: Claude Opus 5.5 (custom-loop 7)\n")
        self.assertEqual(p, ["line 3: 'Claude Opus 5.5' needs ' %s' or a '(claude <version>)' suffix" % ADDR])
        p = self.problems("x\n\nCo-Authored-By: Claude Opus 5.5 (my (fork) 1.0)\n")
        self.assertEqual(p, ["line 3: 'Claude Opus 5.5' needs ' %s' or a '(claude <version>)' suffix" % ADDR])

    def test_gpt_sol_is_a_canonical_model(self):
        self.assertEqual(self.problems("x\n\nReviewed-by: GPT-6.1-Sol (codex-cli 0.9.1)\n"), [])
        self.assertIn("should be spelled 'GPT-6.1-Sol'", self.problems("x\n\nReviewed-by: gpt-6.1-sol\n")[0])

    def test_every_list_entry_is_self_consistent(self):
        for key, (name, addr) in CANON.items():
            line = "Reviewed-by: %s%s\n" % (name, " " + ADDR if addr else "")
            self.assertEqual(self.problems("x\n\n" + line), [], line)
