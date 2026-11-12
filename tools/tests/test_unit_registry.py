"""Tests of tools/unit-registry against a tiny C tree built in a temporary directory.

Nothing here needs ./src, a build, ast-grep or a compiler: the ABI half reads a
stand-in `nm` put first on PATH, and the cross-check runs a stand-in `ast-grep`
(index_xc) wherever verify must accept the index. self.index() passes
--no-crosscheck, which verify refuses on purpose.

Run: python3 -m unittest discover -s tools/tests -p test_unit_registry.py -v
"""
import os, shutil, subprocess, sys, tempfile, textwrap, unittest

sys.dont_write_bytecode = True
TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(TOOLS)
REGISTRY = os.path.join(TOOLS, "unit-registry")

FIXTURE = {
    "src/nxt_util.h": """
        #ifndef _NXT_UTIL_H_
        #define _NXT_UTIL_H_

        #define NXT_LIMIT  16
        #define nxt_max(a, b)  ((a) > (b) ? (a) : (b))

        int nxt_util_add(int a, int b);
        int nxt_util_unused(void);

        #endif
        """,
    "src/nxt_util.c": """
        #include <nxt_util.h>

        /* nxt_util_unused() is mentioned here, which is not a use. */

        static int
        helper(int v)
        {
            return v + 1;
        }


        int
        nxt_util_add(int a, int b)
        {
            return helper(nxt_max(a, b)) + NXT_LIMIT;
        }


        int
        nxt_util_unused(void)
        {
            return 0;
        }
        """,
    "src/nxt_dead.c": """
        #include <nxt_util.h>

        static int
        helper(int v)
        {
            return v - 1;
        }


        int
        nxt_dead_entry(void)
        {
            return nxt_util_add(1, 2);
        }
        """,
    "src/nxt_main.c": """
        #include <nxt_util.h>

        int nxt_dead_entry(void);


        int
        main(void)
        {
            return nxt_dead_entry();
        }
        """,
    "src/test/nxt_util_test.c": """
        #include <nxt_util.h>


        int
        nxt_util_test(void)
        {
            return nxt_util_add(2, 3) == 4;
        }
        """,
}

# Emits one definition per source root it is given, in ast-grep's stream format.
FAKE_ASTGREP = """#!/bin/sh
for a; do
    case $a in */src) printf '{"file":"%s/nxt_util.c","metaVariables":{"single":{"NAME":{"text":"nxt_util_add"}}}}\\n' "$a";; esac
done
"""

# Answers per file, like nm. It ignores its options, so the local `t` symbol
# is printed too: only global binding may count as an export.
FAKE_NM = """#!/bin/sh
for a; do f=$a; done
case $f in
*/libnxt.a)
    echo "0000000000000000 T nxt_util_unused";;
*)
    echo "0000000000001000 T nxt_util_add"
    echo "0000000000001010 T nxt_dead_entry"
    echo "0000000000001020 t helper";;
esac
"""


def write(path, text, mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    if mode:
        os.chmod(path, mode)


class UnitRegistryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="registry-test-"))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.src = os.path.join(self.tmp, "tree")
        for rel, text in FIXTURE.items():
            write(os.path.join(self.src, rel), textwrap.dedent(text).lstrip())
        # A working-tree build: the stand-in nm answers for any file.
        write(os.path.join(self.src, "build", "sbin", "unitd"), "")
        self.bin = os.path.join(self.tmp, "bin")
        write(os.path.join(self.bin, "nm"), FAKE_NM, 0o755)
        self.astgrep = os.path.join(self.bin, "ast-grep")
        write(self.astgrep, FAKE_ASTGREP, 0o755)
        self.scratch_tmp = os.path.join(self.tmp, "tmpdir")
        os.makedirs(self.scratch_tmp)
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("UNIT_", "GIT_"))}
        self.env.update(PATH=self.bin + os.pathsep + os.environ.get("PATH", ""),
                        TMPDIR=self.scratch_tmp, GIT_CONFIG_NOSYSTEM="1",
                        GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_AUTHOR_NAME="test", GIT_AUTHOR_EMAIL="test",
                        GIT_COMMITTER_NAME="test", GIT_COMMITTER_EMAIL="test")
        self.reg = os.path.join(self.tmp, "registry")

    def run_cmd(self, *cmd, env=None, cwd=None):
        return subprocess.run(list(cmd), capture_output=True, text=True,
                              env=dict(self.env, **(env or {})),
                              cwd=cwd or self.tmp, timeout=60,
                              stdin=subprocess.DEVNULL)

    def tool(self, *args, env=None):
        return self.run_cmd(sys.executable, REGISTRY, "--src", self.src,
                            "--registry", self.reg, *args, env=env)

    def index(self, *args, env=None):
        return self.tool("index", "--no-crosscheck", *args, env=env)

    def index_xc(self, *args):
        return self.tool("index", "--astgrep", self.astgrep, *args)

    def manifest(self):
        out = {}
        with open(os.path.join(self.reg, "MANIFEST")) as f:
            for line in f:
                if "\t" in line and not line.startswith("#"):
                    k, v = line.rstrip("\n").split("\t", 1)
                    out[k] = v
        return out

    def git_init(self):
        for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "fixture"]):
            r = self.run_cmd("git", "-C", self.src, *args)
            self.assertEqual(r.returncode, 0, r.stderr)

    # ---- index and queries

    def test_index_and_verify_are_green_on_the_fixture(self):
        r = self.index_xc()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("gates: OK", r.stdout)
        m = self.manifest()
        self.assertEqual(m["gate_failures"], "0")
        self.assertEqual(m["crosscheck"], "ast-grep")
        self.assertEqual(m["nm_symbols"], "2")
        r = self.tool("verify")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # The exact inventory: a check that disappears changes these numbers.
        self.assertIn("10 passed, 0 failed, 10 skipped", r.stdout)
        self.assertNotIn("FAIL", r.stdout)
        self.assertNotIn("conf-utf8-ingress", r.stdout)

    def test_uses_splits_by_context_and_ignores_comments(self):
        self.assertEqual(self.index().returncode, 0)
        r = self.tool("uses", "nxt_util_add")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("prod=1", r.stdout)
        self.assertIn("test=1", r.stdout)
        r = self.tool("uses", "nxt_util_unused")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("NO REFERENCES ANYWHERE", r.stdout)

    def test_unknown_names_exit_2_with_a_message(self):
        self.assertEqual(self.index().returncode, 0)
        for args in (["uses", "nxt_typo"], ["defs", "nxt_typo"], ["callers", "nxt_typo"],
                     ["const", "NXT_TYPO"], ["includes", "src/nxt_typo.c"]):
            r = self.tool(*args)
            self.assertEqual(r.returncode, 2, f"{args}: {r.stdout}")
            self.assertIn("is not in this registry", r.stderr, args)
            self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(self.tool("callers", "nxt_util_add").returncode, 0)
        self.assertEqual(self.tool("const", "NXT_LIMIT").returncode, 0)
        self.assertEqual(self.tool("includes", "src/nxt_util.c").returncode, 0)

    def test_define_fn_value_drops_the_parameter_list(self):
        self.assertEqual(self.index().returncode, 0)
        r = self.tool("const", "nxt_max")
        self.assertEqual(r.returncode, 0, r.stderr)
        line = next(x for x in r.stdout.splitlines() if x.startswith("define-fn"))
        self.assertTrue(line.endswith("((a) > (b) ? (a) : (b))"), line)
        self.assertNotIn("a, b)", line)

    def test_dead_scopes_a_static_to_its_own_file(self):
        # helper() in nxt_dead.c is dead; the live helper() in nxt_util.c must
        # not hide it.
        self.assertEqual(self.index().returncode, 0)
        r = self.tool("dead", "--mode", "everywhere")
        self.assertEqual(r.returncode, 0, r.stderr)
        dead = [x.split() for x in r.stdout.splitlines() if x.startswith("  ")]
        self.assertIn(("helper", "src/nxt_dead.c:4"), [(d[0], d[1]) for d in dead])
        self.assertNotIn("src/nxt_util.c:6", [d[1] for d in dead])
        self.assertIn("Approximate", r.stdout)

    def test_callers_reports_address_taken_references(self):
        # `s->h = &fn;` is how a parser dispatches (src/nxt_http_parse.c:127).
        # A function reached only that way must not read as "no callers".
        write(os.path.join(self.src, "src", "nxt_ptr.c"), textwrap.dedent("""\
            typedef struct {
                int  (*h)(int v);
            } nxt_ptr_holder_t;


            static int
            nxt_ptr_target(int v)
            {
                return v + 1;
            }


            static int
            nxt_ptr_only(int v)
            {
                return v - 1;
            }


            int
            nxt_ptr_setup(nxt_ptr_holder_t *s)
            {
                s->h = &nxt_ptr_only;
                s->h = &nxt_ptr_target;
                return nxt_ptr_target(1);
            }
            """))
        self.assertEqual(self.index().returncode, 0)
        r = self.tool("callers", "nxt_ptr_only")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("no callers", r.stdout)
        self.assertIn("address-taken (not a direct call):", r.stdout)
        self.assertIn("src/nxt_ptr.c:23", r.stdout)
        r = self.tool("callers", "nxt_ptr_target")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("src/nxt_ptr.c:25", r.stdout.split("address-taken")[0])
        self.assertIn("src/nxt_ptr.c:24", r.stdout.split("address-taken")[1])
        r = self.tool("callers", "--transitive", "nxt_ptr_only")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("address-taken references not followed: 1", r.stdout)
        r = self.tool("callers", "nxt_util_unused")
        self.assertIn("no callers of nxt_util_unused", r.stdout)

    # ---- review findings on PR #29

    def test_comment_markers_inside_a_define_literal_are_not_comments(self):
        # `/*` in a #define string opened a phantom comment that ate the file.
        write(os.path.join(self.src, "src", "nxt_glob.c"), textwrap.dedent("""\
            #define NXT_URL   "https://example.com/x"
            #define NXT_GLOB  "src/*.c"
            #define NXT_SLASH '/'


            int
            nxt_after_defines(void)
            {
                return 0;
            }


            int
            nxt_second_function(void)
            {
                return nxt_after_defines();
            }
            """))
        self.assertEqual(self.index().returncode, 0)
        for sym in ("nxt_after_defines", "nxt_second_function"):
            self.assertEqual(self.tool("defs", sym).returncode, 0, sym)
        r = self.tool("uses", "nxt_after_defines")
        self.assertIn("prod=1", r.stdout)
        for name, value in (("NXT_URL", '"https://example.com/x"'),
                            ("NXT_GLOB", '"src/*.c"'), ("NXT_SLASH", "'/'")):
            r = self.tool("const", name)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(r.stdout.splitlines()[1].endswith(value), r.stdout)

    def test_function_pointer_declarators_are_not_keywords(self):
        # `typedef void (*t)(void)` registered `void` as a declared function.
        write(os.path.join(self.src, "src", "nxt_fnptr.h"), textwrap.dedent("""\
            typedef void (*nxt_cb_t)(void);
            typedef long nxt_cdecl (*nxt_log_cb_t)(int level);

            int (*nxt_ret_fnptr(void))(void);
            """))
        write(os.path.join(self.src, "src", "nxt_fnptr.c"), textwrap.dedent("""\
            #include <nxt_fnptr.h>

            static nxt_cb_t  nxt_saved_cb;


            int
            (*nxt_ret_fnptr(void))(void)
            {
                return nxt_saved_cb ? 0 : 0;
            }
            """))
        self.assertEqual(self.index().returncode, 0)
        for kw in ("void", "int", "long"):
            self.assertEqual(self.tool("defs", kw).returncode, 2, kw)
            self.assertEqual(self.tool("uses", kw).returncode, 2, kw)
        r = self.tool("defs", "nxt_ret_fnptr")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("def ", r.stdout)
        self.assertIn("decl", r.stdout)
        for t in ("nxt_cb_t", "nxt_log_cb_t"):
            r = self.tool("uses", t)
            self.assertEqual(r.returncode, 0, t + r.stderr)
        self.assertIn("references: 1", self.tool("uses", "nxt_cb_t").stdout)

    def _usage_append(self, row):
        p = os.path.join(self.reg, "usage.tsv")
        with open(p, "a") as f:
            f.write("\t".join(row) + "\n")
        m = os.path.join(self.reg, "MANIFEST")
        with open(m) as f:
            text = f.read()
        n = int(self.manifest()["usage_rows"])
        with open(m, "w") as f:
            f.write(text.replace("usage_rows\t%d\n" % n,
                                 "usage_rows\t%d\n" % (n + 1)))

    def test_enum_member_definition_is_not_its_own_use(self):
        write(os.path.join(self.src, "src", "nxt_enum.c"), textwrap.dedent("""\
            typedef enum {
                NXT_E_USED = 0,
                NXT_E_UNUSED,
            } nxt_e_t;


            int
            nxt_enum_get(void)
            {
                return NXT_E_USED;
            }
            """))
        self.assertEqual(self.index_xc().returncode, 0)
        r = self.tool("const", "NXT_E_UNUSED")
        self.assertIn("uses: 0  (prod=0", r.stdout)
        r = self.tool("const", "NXT_E_USED")
        self.assertIn("uses: 1  (prod=1", r.stdout)
        self.assertEqual(self.tool("verify").returncode, 0)
        # A self row is what verify's invariant exists to catch.
        self._usage_append(["NXT_E_UNUSED", "src/nxt_enum.c", "3", "5", "prod",
                            "ref", "-"])
        r = self.tool("verify")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("FAIL  invariant: no definition or declaration counts as "
                      "its own use", r.stdout)

    def test_an_unreadable_source_fails_the_index(self):
        os.symlink("nxt_loop.c", os.path.join(self.src, "src", "nxt_loop.c"))
        r = self.index()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("read: cannot read src/nxt_loop.c", r.stdout)
        for args in (["verify"], ["dead"], ["uses", "nxt_util_add"]):
            self.assertEqual(self.tool(*args).returncode, 2, args)
        os.unlink(os.path.join(self.src, "src", "nxt_loop.c"))
        if os.geteuid() == 0:
            return  # root reads a mode-000 file
        os.chmod(os.path.join(self.src, "src", "nxt_main.c"), 0)
        self.addCleanup(os.chmod, os.path.join(self.src, "src", "nxt_main.c"),
                        0o644)
        r = self.index()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("read: cannot read src/nxt_main.c", r.stdout)
        self.assertEqual(self.tool("dead").returncode, 2)

    def test_verify_refuses_an_unverified_or_partial_index(self):
        r = self.index()
        self.assertIn("NONE, cross-check disabled", r.stdout)
        r = self.tool("verify")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("FAIL  invariant: the index was cross-checked", r.stdout)
        for fn in ("coverage.tsv", "includes.tsv", "public_abi.tsv",
                   "usage.tsv", "abi.tsv"):
            self.assertEqual(self.index_xc().returncode, 0)
            self.assertEqual(self.tool("verify").returncode, 0, fn)
            os.unlink(os.path.join(self.reg, fn))
            r = self.tool("verify")
            self.assertEqual(r.returncode, 1, fn + r.stdout)
            self.assertIn(fn + " missing", r.stdout)
        # `dead` must not answer without public_abi.tsv: it withholds from it.
        self.assertEqual(self.index_xc().returncode, 0)
        os.unlink(os.path.join(self.reg, "public_abi.tsv"))
        self.assertEqual(self.tool("dead").returncode, 2)
        self.assertEqual(self.index_xc().returncode, 0)
        self._usage_append(["nxt_util_add", "src/nxt_nowhere.c", "1", "1",
                            "prod", "call", "-"])
        r = self.tool("verify")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("FAIL  invariant: every reference is in an indexed file",
                      r.stdout)

    def test_go_cgo_calls_are_module_references(self):
        write(os.path.join(self.src, "go", "unit.go"), textwrap.dedent("""\
            package unit

            // C.nxt_util_unused() in a comment is not a call.
            func add() int {
                s := "C.nxt_util_unused()"
                _ = s
                return int(C.nxt_util_add(1, 2))
            }
            """))
        self.assertEqual(self.index_xc().returncode, 0)
        r = self.tool("uses", "nxt_util_add")
        self.assertIn("module=1", r.stdout)
        self.assertIn("go/unit.go:7  call", r.stdout)
        r = self.tool("uses", "nxt_util_unused")
        self.assertIn("NO REFERENCES ANYWHERE", r.stdout)
        self.assertEqual(self.tool("verify").returncode, 0)


    def test_minor_findings(self):
        write(os.path.join(self.src, "src", "nxt_minor.c"), textwrap.dedent("""\
            #define nxt_string(s)  { sizeof(s) - 1, (char *) s }

            #if 0
            #define NXT_DEAD_CONST  99
            #include <nxt_nonexistent.h>
            #endif

            static const nxt_str_t  nxt_prefix = nxt_string("/api");


            PHP_FUNCTION(nxt_php_cb)
            {
                return nxt_prefix.length;
            }
            """))
        self.assertEqual(self.index_xc().returncode, 0)
        # An object with a macro initialiser is not a prototype of the macro.
        self.assertEqual(self.tool("defs", "nxt_string").returncode, 2)
        self.assertEqual(self.tool("uses", "nxt_prefix").returncode, 0)
        self.assertIn("references: 1", self.tool("uses", "nxt_string").stdout)
        # Directives inside #if 0 are not indexed.
        self.assertEqual(self.tool("const", "NXT_DEAD_CONST").returncode, 2)
        r = self.tool("includes", "src/nxt_minor.c")
        self.assertNotIn("nxt_nonexistent.h", r.stdout)
        # The macro invocation is not a dead function; its argument is.
        r = self.tool("dead", "--mode", "everywhere")
        self.assertNotIn("PHP_FUNCTION", r.stdout)
        self.assertIn("nxt_php_cb", r.stdout)
        # A typo in --verdict is an error, not an empty answer.
        self.assertEqual(self.tool("abi", "--verdict", "bogus").returncode, 2)
        self.assertEqual(self.tool("abi", "--all").returncode, 0)
        # A working-tree build is assumed to match, never shown to.
        self.assertEqual(self.manifest()["build_matches_ref"], "assumed")
        # A tree inside another repository is not stamped with its HEAD.
        self.git_init()
        nested = os.path.join(self.src, "nested")
        write(os.path.join(nested, "src", "nxt_n.c"),
              "int\nnxt_n(void)\n{\n    return 0;\n}\n")
        r = self.run_cmd(sys.executable, REGISTRY, "--src", nested, "index",
                         "--no-crosscheck", "--out", self.reg)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.manifest()["sha"], "unknown")
        r = self.run_cmd(sys.executable, REGISTRY, "--src",
                         os.path.join(self.tmp, "no-such-tree"), "index",
                         "--no-crosscheck", "--out", self.reg)
        self.assertEqual(r.returncode, 2, r.stdout)


    def test_members_and_parameters_are_not_symbols(self):
        write(os.path.join(self.src, "src", "nxt_shapes.h"), textwrap.dedent("""\
            typedef struct {
                int  count, total;
                enum {
                    NXT_SHAPE_ROUND = 0,
                    NXT_SHAPE_FLAT,
                }    shape;
            } nxt_shape_t;

            typedef enum {
                NXT_COLOR_RED = 1,
                NXT_COLOR_BLUE,
            } nxt_color_t;

            int nxt_shape_parse(nxt_shape_t *shape, int error);
            """))
        write(os.path.join(self.src, "src", "nxt_shapes.c"), textwrap.dedent("""\
            #include <nxt_shapes.h>

            static int  error;


            int
            nxt_shape_parse(nxt_shape_t *shape, int error)
            {
                shape->count = NXT_SHAPE_FLAT;
                return NXT_COLOR_BLUE;
            }


            int
            nxt_shape_error(void)
            {
                return error;
            }
            """))
        self.assertEqual(self.index_xc().returncode, 0)
        with open(os.path.join(self.reg, "objects.tsv")) as f:
            objs = {line.split("\t")[0] for line in f}
        self.assertIn("nxt_shape_t", objs)
        self.assertIn("nxt_color_t", objs)
        for name in ("count", "total", "shape", "NXT_COLOR_RED"):
            self.assertNotIn(name, objs)
        for name in ("count", "total"):
            self.assertEqual(self.tool("uses", name).returncode, 2, name)
        # A member of an enum nested in a struct is a constant all the same.
        r = self.tool("const", "NXT_SHAPE_FLAT")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("uses: 1  (prod=1", r.stdout)
        # The parameter `error` in the prototype and in the definition's
        # header is not a use of the file-static `error`; the read is.
        r = self.tool("uses", "error")
        self.assertIn("references: 1  (prod=1", r.stdout)
        self.assertIn("src/nxt_shapes.c:17", r.stdout)
        self.assertEqual(self.tool("verify").returncode, 0)

    def test_dead_branches_and_macro_bodies(self):
        write(os.path.join(self.src, "src", "nxt_branches.c"), textwrap.dedent("""\
            #include <nxt_util.h>

            #define nxt_util_call(x)                                          \\
                /* a comment inside the macro does not end it */              \\
                nxt_util_add((x), 1)

            #if /* off */ 0
            int nxt_off1(void) { return nxt_util_unused(); }
            #endif

            #if 0 && defined(NXT_X)
            int nxt_off2(void) { return nxt_util_unused(); }
            #endif

            #if !1
            int nxt_off3(void) { return nxt_util_unused(); }
            #elif 0
            int nxt_off4(void) { return nxt_util_unused(); }
            #else
            #define NXT_BRANCH_LIVE  1
            #endif

            #if 1
            #define NXT_BRANCH_ON  1
            #else
            #define NXT_BRANCH_OFF  1
            int nxt_off5(void) { return nxt_util_unused(); }
            #endif


            int
            nxt_branch_entry(void)
            {
                return nxt_util_call(NXT_BRANCH_LIVE + NXT_BRANCH_ON);
            }
            """))
        self.assertEqual(self.index_xc().returncode, 0)
        r = self.tool("uses", "nxt_util_unused")
        self.assertIn("NO REFERENCES ANYWHERE", r.stdout)
        for name in ("nxt_off1", "nxt_off2", "nxt_off3", "nxt_off4", "nxt_off5"):
            self.assertEqual(self.tool("defs", name).returncode, 2, name)
        self.assertEqual(self.tool("const", "NXT_BRANCH_OFF").returncode, 2)
        for name in ("NXT_BRANCH_LIVE", "NXT_BRANCH_ON"):
            self.assertEqual(self.tool("const", name).returncode, 0, name)
        # The call inside the macro body is a use, attributed to the macro,
        # and the comment in the macro did not cut it off.
        r = self.tool("callers", "nxt_util_add")
        self.assertIn("src/nxt_branches.c:5  [prod]  in nxt_util_call", r.stdout)
        self.assertEqual(self.tool("verify").returncode, 0)

    def test_a_definition_hidden_by_a_conditional_brace_is_indexed(self):
        write(os.path.join(self.src, "src", "nxt_alt.c"), textwrap.dedent("""\
            #ifdef NXT_ALT
            static int nxt_alt_def(void) {
            #else
            static int nxt_norm_def(void) {
            #endif
                return 0;
            }
            """))
        astgrep = os.path.join(self.bin, "ast-grep-alt")
        write(astgrep, FAKE_ASTGREP.replace(
            'esac', 'esac\ncase $a in */src) printf \'{"file":"%s/nxt_alt.c",'
            '"metaVariables":{"single":{"NAME":{"text":"nxt_norm_def"}}}}\\n\' '
            '"$a";; esac'), 0o755)
        r = self.tool("index", "--astgrep", astgrep)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("read from another #if branch: 1 definition(s)", r.stdout)
        self.assertEqual(self.manifest()["conditional_alt_defs"], "1")
        self.assertEqual(self.manifest()["conditional_hidden_files"], "0")
        for name, line in (("nxt_alt_def", 2), ("nxt_norm_def", 4)):
            r = self.tool("defs", name)
            self.assertEqual(r.returncode, 0, name)
            self.assertIn("src/nxt_alt.c:%d" % line, r.stdout)
        self.assertEqual(self.tool("verify").returncode, 0)

    def test_a_definition_still_hidden_is_reported_not_fatal(self):
        # ast-grep finds a definition there that no branch of the scan opens:
        # it is listed, and the rest of the index still builds.
        write(os.path.join(self.src, "src", "nxt_alt.c"), textwrap.dedent("""\
            #ifdef NXT_ALT
            static int nxt_alt_def(void) {
            #else
            static int nxt_norm_def(void) {
            #endif
                return 0;
            }
            """))
        astgrep = os.path.join(self.bin, "ast-grep-ghost")
        write(astgrep, FAKE_ASTGREP.replace(
            'esac', 'esac\ncase $a in */src) printf \'{"file":"%s/nxt_alt.c",'
            '"metaVariables":{"single":{"NAME":{"text":"nxt_ghost_def"}}}}\\n\' '
            '"$a";; esac'), 0o755)
        r = self.tool("index", "--astgrep", astgrep)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("not indexed, hidden by an #if/#else around a brace", r.stdout)
        self.assertIn("src/nxt_alt.c (nxt_ghost_def)", r.stdout)
        self.assertEqual(self.manifest()["conditional_hidden_files"], "1")
        with open(os.path.join(self.reg, "disagreements.tsv")) as f:
            self.assertIn("nxt_ghost_def", f.read())
        self.assertEqual(self.tool("defs", "nxt_util_add").returncode, 0)

    def test_locals_are_not_uses_of_a_file_scope_name(self):
        write(os.path.join(self.src, "src", "nxt_scope.c"), textwrap.dedent("""\
            #include <nxt_util.h>

            #define NXT_ATTR

            typedef struct {
                int  error;
            } nxt_holder_t;

            static int  error;
            static int  tbl;


            int
            nxt_scope_param(int error)
            {
                return error + 1;
            }


            int
            nxt_scope_block(int v)
            {
                if (v) {
                    int  error = v;

                    return error;
                }

                return error;
            }


            int
            nxt_scope_for(int n)
            {
                int  s = 0;

                for (int error = 0; error < n; error++) {
                    s += error;
                }

                return s + error;
            }


            int
            nxt_scope_static(nxt_holder_t *h)
            {
                static const int  tbl[2] NXT_ATTR = { 1, 2 };

                return tbl[0] + h->error;
            }
            """))
        self.assertEqual(self.index_xc().returncode, 0)
        # Only the two reads outside every local's scope are the file-static:
        # after the inner block of nxt_scope_block, and after the loop.
        r = self.tool("uses", "error")
        self.assertIn("references: 2  (prod=2", r.stdout)
        self.assertIn("src/nxt_scope.c:29", r.stdout)
        self.assertIn("src/nxt_scope.c:42", r.stdout)
        # The local static `tbl` is not the file-scope `tbl`; the attribute
        # macro after its declarator is a use, not its name.
        r = self.tool("uses", "tbl")
        self.assertIn("NO REFERENCES ANYWHERE", r.stdout)
        r = self.tool("const", "NXT_ATTR")
        self.assertIn("uses: 1  (prod=1", r.stdout)
        self.assertEqual(self.tool("verify").returncode, 0)

    def test_cxx_detection_reads_code_not_comments(self):
        write(os.path.join(self.src, "src", "nxt_guarded.h"), textwrap.dedent("""\
            #ifdef __cplusplus
            extern "C" {
            #endif

            /* class Foo would be C++; this is a comment. */
            int nxt_guarded_fn(void);

            #ifdef __cplusplus
            }
            #endif
            """))
        write(os.path.join(self.src, "src", "nodejs", "nxt_addon.h"),
              "namespace nxt {\nclass Addon {\n};\n}\n")
        self.assertEqual(self.index_xc().returncode, 0)
        self.assertEqual(self.tool("defs", "nxt_guarded_fn").returncode, 0)
        self.assertEqual(self.manifest()["cxx_files"], "src/nodejs/nxt_addon.h")
        # A .c file that reads as C++ is not skipped quietly.
        write(os.path.join(self.src, "src", "nxt_odd.c"),
              "class Odd {\n};\n")
        r = self.index_xc()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("cxx: src/nxt_odd.c", r.stdout)

    def test_abi_reads_global_symbols_only(self):
        write(os.path.join(self.src, "build", "lib", "libnxt.a"), "")
        self.assertEqual(self.index_xc().returncode, 0)
        # The local `t helper` is not an export; libnxt.a's T symbol is.
        self.assertEqual(self.manifest()["nm_symbols"], "3")
        with open(os.path.join(self.reg, "abi.tsv")) as f:
            rows = {line.split("\t")[0]: line.rstrip("\n").split("\t")
                    for line in f if not line.startswith("#")}
        self.assertNotIn("helper", rows)
        self.assertEqual(rows["nxt_util_unused"][4], "libnxt.a")
        r = self.tool("abi", "--all")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("nxt_util_unused", r.stdout)
        self.assertNotIn(" helper ", r.stdout)

    # ---- second review of PR 29

    def test_a_macro_tested_in_a_conditional_is_a_pp_use(self):
        write(os.path.join(self.src, "src", "nxt_flag.h"), textwrap.dedent("""\
            #ifndef _NXT_FLAG_H_INCLUDED_
            #define _NXT_FLAG_H_INCLUDED_

            #define NXT_FLAG   1
            #define NXT_FLAG2  1
            #define NXT_FLAG3  1

            #endif
            """))
        write(os.path.join(self.src, "src", "nxt_flag.c"), textwrap.dedent("""\
            #include <nxt_flag.h>

            #if (NXT_FLAG)
            #define NXT_ON  1
            #endif

            #ifdef NXT_FLAG2
            #elif defined(NXT_FLAG3) \\
                  && 1
            #endif
            """))
        self.assertEqual(self.index().returncode, 0)
        for name in ("NXT_FLAG", "NXT_FLAG2", "NXT_FLAG3"):
            r = self.tool("const", name)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("uses: 1  (prod=1", r.stdout, name)
        r = self.tool("uses", "NXT_FLAG3")
        self.assertIn("src/nxt_flag.c:8  pp", r.stdout)
        # Not a call and not address-taken.
        self.assertIn("no callers of NXT_FLAG", self.tool("callers", "NXT_FLAG").stdout)
        # The guard's own #ifndef is not a use.
        r = self.tool("const", "_NXT_FLAG_H_INCLUDED_")
        self.assertIn("uses: 0", r.stdout)

    def test_a_core_file_named_in_a_module_comment_stays_prod(self):
        write(os.path.join(self.src, "auto", "sources"),
              "NXT_LIB_SRCS=\"\n    src/nxt_shared.c \\\n\"\n")
        write(os.path.join(self.src, "auto", "modules", "x"), textwrap.dedent("""\
            # The table is built in src/nxt_core.c, not here.
            NXT_X_SRCS="src/x/nxt_x.c src/nxt_shared.c"  # src/nxt_core2.c
            """))
        for rel in ("src/nxt_core.c", "src/nxt_core2.c", "src/nxt_shared.c",
                    "src/x/nxt_x.c"):
            write(os.path.join(self.src, rel), "int nxt_%s_v;\n"
                  % os.path.basename(rel)[4:-2])
        self.assertEqual(self.index().returncode, 0)
        with open(os.path.join(self.reg, "coverage.tsv")) as f:
            ctx = dict(line.split("\t")[:2] for line in f
                       if not line.startswith("#"))
        self.assertEqual(ctx["src/nxt_core.c"], "prod")
        self.assertEqual(ctx["src/nxt_core2.c"], "prod")
        self.assertEqual(ctx["src/nxt_shared.c"], "prod")
        self.assertEqual(ctx["src/x/nxt_x.c"], "module")

    def test_a_header_in_each_if_arm_is_not_a_call(self):
        write(os.path.join(self.src, "src", "nxt_arms.c"), textwrap.dedent("""\
            #ifdef NXT_ARM
            static int
            nxt_two_arm(int a)
            #else
            static long
            nxt_two_arm(long a)
            #endif
            {
                return 0;
            }


            #ifdef NXT_ARM
            static int
            nxt_two_arm_rec(int a)
            #else
            static long
            nxt_two_arm_rec(long a)
            #endif
            {
                return a ? nxt_two_arm_rec(a - 1) : 0;
            }
            """))
        self.assertEqual(self.index().returncode, 0)
        r = self.tool("callers", "nxt_two_arm")
        self.assertIn("no callers of nxt_two_arm", r.stdout)
        r = self.tool("dead", "--mode", "everywhere")
        self.assertIn("nxt_two_arm ", r.stdout)
        # The header of the first arm is not a call; the recursive call in
        # the body still is.
        r = self.tool("callers", "nxt_two_arm_rec")
        self.assertEqual(r.stdout.count("src/nxt_arms.c:"), 1, r.stdout)
        self.assertIn("src/nxt_arms.c:21  [prod]  in nxt_two_arm_rec", r.stdout)

    def test_constant_if_reads_like_c_and_deep_nesting_is_unknown(self):
        write(os.path.join(self.src, "src", "nxt_cmp.c"), textwrap.dedent("""\
            #if 3 > 2 > 1
            int nxt_c_false(void) { return 0; }
            #else
            int nxt_c_true(void) { return 1; }
            #endif
            """))
        write(os.path.join(self.src, "src", "nxt_deep.c"),
              "#if " + "(!" * 1500 + "1" + ")" * 1500
              + "\nint nxt_deep_def(void) { return 0; }\n#endif\n")
        r = self.index()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(self.tool("defs", "nxt_c_true").returncode, 0)
        self.assertEqual(self.tool("defs", "nxt_c_false").returncode, 2)
        self.assertEqual(self.tool("defs", "nxt_deep_def").returncode, 0)

    def test_output_has_no_double_space_or_empty_depth(self):
        self.assertEqual(self.index().returncode, 0)
        r = self.tool("dead", "--mode", "everywhere")
        self.assertIn(" symbol(s) with no reference", r.stdout)
        r = self.tool("callers", "--transitive", "nxt_util_add")
        self.assertIn("-- depth 2 --", r.stdout)
        self.assertNotIn("-- depth 3 --", r.stdout)
        r = self.tool("callers", "--transitive", "nxt_util_unused")
        self.assertNotIn("-- depth", r.stdout)

    def test_an_explicit_build_needs_the_dir_and_nm(self):
        r = self.index("--build", os.path.join(self.tmp, "no-such-build"))
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("is not a directory", r.stderr)
        empty = os.path.join(self.tmp, "empty-bin")
        os.makedirs(empty)
        r = self.index("--build", os.path.join(self.src, "build"),
                       env={"PATH": empty})
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("nm", r.stderr)

    # ---- gate failures

    def test_a_failed_gate_makes_queries_refuse(self):
        write(os.path.join(self.src, "src", "nxt_broken.c"),
              "int\nnxt_broken(void)\n{\n    if (1) {\n        return 0;\n}\n")
        r = self.index()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("GATE FAILURES", r.stdout)
        self.assertNotEqual(self.manifest()["gate_failures"], "0")
        for args in (["uses", "nxt_util_add"], ["dead"], ["verify"], ["callers", "nxt_util_add"]):
            r = self.tool(*args)
            self.assertEqual(r.returncode, 2, args)
            self.assertIn("gates failed", r.stderr)
        self.assertEqual(self.tool("stat").returncode, 0)

    # ---- --ref

    def test_ref_index_reads_the_ref_and_no_working_tree_binary(self):
        self.git_init()
        # A working-tree change the ref must not see.
        write(os.path.join(self.src, "src", "nxt_new.c"),
              "int\nnxt_new_fn(void)\n{\n    return nxt_util_add(0, 0);\n}\n")
        r = self.index("--ref", "HEAD")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        m = self.manifest()
        self.assertEqual(m["ref"], "HEAD")
        # build/ exists in the working tree, but it was not built from HEAD.
        self.assertEqual(m["nm_symbols"], "0")
        self.assertEqual(m["build_dir"], "-")
        self.assertEqual(self.tool("uses", "nxt_new_fn").returncode, 2)
        self.assertEqual(os.listdir(self.scratch_tmp), [])
        # Naming the build explicitly is the opt-in.
        r = self.index("--ref", "HEAD", "--build", os.path.join(self.src, "build"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.manifest()["build_matches_ref"], "assumed")

    def test_ref_starting_with_dash_is_refused(self):
        self.git_init()
        r = self.index("--ref=--output=x")
        self.assertEqual(r.returncode, 2)
        self.assertIn("cannot start with '-'", r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "x")))
        self.assertFalse(os.path.exists(os.path.join(self.src, "x")))

    def test_die_after_checkout_removes_the_temp_dir(self):
        self.git_init()
        write(os.path.join(self.bin, "tar"), "#!/bin/sh\necho broken >&2\nexit 1\n", 0o755)
        r = self.index("--ref", "HEAD")
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("tar extract failed", r.stderr)
        self.assertEqual(os.listdir(self.scratch_tmp), [])

    # ---- third review of PR 29

    def test_a_macro_body_before_an_if_is_not_a_call(self):
        write(os.path.join(self.src, "src", "nxt_alias.h"), textwrap.dedent("""\
            #define NXT_FOO  1
            #define NXT_U    1
            #define NXT_BAR  NXT_FOO
            #if (NXT_U)
            #endif
            """))
        self.assertEqual(self.index().returncode, 0)
        with open(os.path.join(self.reg, "usage.tsv")) as f:
            rows = [l.split("\t") for l in f if l.startswith("NXT_FOO\t")]
        self.assertEqual([(r[2], r[5]) for r in rows], [("3", "ref")])
        r = self.tool("callers", "NXT_FOO")
        self.assertEqual(r.stdout.split("address-taken")[0].count("src/"), 0,
                         r.stdout)
        self.assertIn("src/nxt_alias.h:3  [prod]  in NXT_BAR", r.stdout)

    def test_verify_self_use_covers_objects_and_allows_a_self_named_define(self):
        write(os.path.join(self.src, "src", "nxt_alias.c"), textwrap.dedent("""\
            #define NXT_ALIAS  NXT_ALIAS
            #define nxt_flagged(x)  (nxt_flagged && (x))

            int  nxt_flagged;
            int  nxt_obj;


            int
            nxt_alias_get(void)
            {
                return NXT_ALIAS + nxt_obj + nxt_flagged(1);
            }
            """))
        r = self.index_xc()
        self.assertEqual(r.returncode, 0, r.stdout)
        r = self.tool("verify")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("uses: 1  (prod=1", self.tool("const", "NXT_ALIAS").stdout)
        # The macro body names the object, which is a use of the object.
        r = self.tool("uses", "nxt_flagged")
        self.assertIn("src/nxt_alias.c:2  ref", r.stdout)
        # An object's own line is a definition site too.
        self._usage_append(["nxt_obj", "src/nxt_alias.c", "5", "6", "prod",
                            "ref", "-"])
        r = self.tool("verify")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("FAIL  invariant: no definition or declaration counts as "
                      "its own use", r.stdout)

    def _unreadable(self, rel):
        """Make rel mode 000; returns the call that restores it."""
        path = os.path.join(self.src, rel)
        mode = os.stat(path).st_mode & 0o777
        os.chmod(path, 0)
        self.addCleanup(os.chmod, path, mode)
        return lambda: os.chmod(path, mode)

    @unittest.skipIf(os.geteuid() == 0, "root reads a mode-000 path")
    def test_an_unreadable_directory_or_build_script_fails_the_index(self):
        write(os.path.join(self.src, "src", "blocked", "nxt_blocked.c"),
              "int\nnxt_blocked(void)\n{\n    return nxt_util_unused();\n}\n")
        write(os.path.join(self.src, "go", "sub", "x.go"), "package x\n")
        write(os.path.join(self.src, "auto", "make"),
              "libunit-install:\n\tinstall src/nxt_util.h\n\nother:\n")
        write(os.path.join(self.src, "auto", "sources"), "\n")
        write(os.path.join(self.src, "auto", "modules", "x"), "\n")
        self.assertEqual(self.index().returncode, 0)
        self.assertEqual(self.manifest()["public_headers"], "1")
        for what in ("src/blocked", "go/sub", "auto/make", "auto/sources",
                     "auto/modules/x", "auto/modules"):
            restore = self._unreadable(what)
            r = self.index()
            self.assertEqual(r.returncode, 1, what + r.stdout)
            self.assertIn("read: cannot read %s (" % what, r.stdout)
            self.assertEqual(r.stdout.count("cannot read %s (" % what), 1, what)
            self.assertEqual(self.tool("uses", "nxt_util_unused").returncode, 2)
            restore()

    def test_dead_lists_an_all_caps_function_but_not_a_macro_invocation(self):
        write(os.path.join(self.src, "src", "nxt_caps.c"), textwrap.dedent("""\
            int
            NXT_CMSG_TEST(struct msghdr *msgh, void *cmsg)
            {
                return 0;
            }


            PHP_FUNCTION(nxt_caps_cb)
            {
            }
            """))
        self.assertEqual(self.index().returncode, 0)
        r = self.tool("dead", "--mode", "everywhere")
        self.assertIn("NXT_CMSG_TEST ", r.stdout)
        self.assertIn("nxt_caps_cb ", r.stdout)
        self.assertNotIn("PHP_FUNCTION", r.stdout)

    def test_pp_uses_are_constants_and_literal_suffixes_are_not_names(self):
        write(os.path.join(self.src, "src", "nxt_ppx.c"), textwrap.dedent("""\
            #define L  2

            #if __STDC_VERSION__ >= 201112L
            #endif

            #ifdef nxt_ppx_fn
            #endif


            long
            nxt_ppx_fn(void)
            {
                return 1L + 16UL;
            }
            """))
        self.assertEqual(self.index().returncode, 0)
        self.assertIn("uses: 0", self.tool("const", "L").stdout)
        self.assertIn("references: 0", self.tool("uses", "nxt_ppx_fn").stdout)

    def test_unsigned_and_continued_if_conditions(self):
        write(os.path.join(self.src, "src", "nxt_cond.c"), textwrap.dedent("""\
            #if 0u - 1 > 0
            int nxt_cond_unsigned(void) { return 0; }
            #endif

            #if -1 > 0u
            int nxt_cond_mixed(void) { return 0; }
            #endif

            #if 0 \\
                && defined(NXT_CONT_X)
            int nxt_cond_continued(void) { return 0; }
            #endif
            """))
        self.assertEqual(self.index().returncode, 0)
        # Unsigned arithmetic is not modelled: unknown, so the branch stays.
        self.assertEqual(self.tool("defs", "nxt_cond_unsigned").returncode, 0)
        self.assertEqual(self.tool("defs", "nxt_cond_mixed").returncode, 0)
        self.assertEqual(self.tool("defs", "nxt_cond_continued").returncode, 2)

    def test_a_build_dir_without_artifacts_exits_2(self):
        empty = os.path.join(self.tmp, "empty-build")
        os.makedirs(empty)
        r = self.index("--build", empty)
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("holds no sbin/unitd", r.stderr)
        r = self.index("--no-build")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.manifest()["nm_symbols"], "0")

    def test_nm_options_select_global_and_dynamic_symbols(self):
        log = os.path.join(self.tmp, "nm.log")
        write(os.path.join(self.bin, "nm"), FAKE_NM.replace(
            "\n", '\necho "$*" >> "%s"\n' % log, 1), 0o755)
        write(os.path.join(self.src, "build", "lib", "unit", "modules",
                           "x.unit.so"), "")
        self.assertEqual(self.index().returncode, 0)
        with open(log) as f:
            calls = f.read().splitlines()
        unitd = [c for c in calls if c.endswith("/sbin/unitd")]
        so = [c for c in calls if c.endswith("/x.unit.so")]
        self.assertEqual(len(unitd), 1, calls)
        self.assertIn("--extern-only", unitd[0])
        self.assertNotIn("--dynamic", unitd[0])
        self.assertEqual(len(so), 1, calls)
        self.assertIn("--extern-only", so[0])
        self.assertIn("--dynamic", so[0])

    def test_abi_verdict_agree_and_long_constant_values(self):
        write(os.path.join(self.src, "src", "nxt_exp.h"),
              "NXT_EXPORT int nxt_util_add(int a, int b);\n")
        write(os.path.join(self.src, "src", "nxt_long.h"),
              "#define NXT_LONG_VALUE  " + " + ".join(["1"] * 100) + "\n")
        self.assertEqual(self.index().returncode, 0)
        r = self.tool("abi", "--verdict", "agree")
        self.assertIn("== agree (", r.stdout)
        self.assertIn("nxt_util_add", r.stdout)
        r = self.tool("const", "NXT_LONG_VALUE")
        value = r.stdout.splitlines()[1].split("nxt_long.h:1  ", 1)[1]
        self.assertEqual(len(value), 120, value)
        self.assertTrue(value.endswith("..."), value)

    def test_input_guards_and_an_empty_crosscheck(self):
        empty = os.path.join(self.tmp, "empty-tree")
        os.makedirs(empty)
        r = self.run_cmd(sys.executable, REGISTRY, "--src", empty, "index",
                         "--no-crosscheck", "--out", self.reg)
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("no C sources", r.stderr)
        r = self.run_cmd(sys.executable, REGISTRY, "--src",
                         os.path.join(self.src, "src", "nxt_util.c"), "index",
                         "--no-crosscheck", "--out", self.reg)
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("is not a directory", r.stderr)
        # An ast-grep that finds nothing is a broken cross-check.
        write(self.astgrep, "#!/bin/sh\nexit 0\n", 0o755)
        r = self.index_xc()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("crosscheck: ast-grep returned an empty result set",
                      r.stdout)

    def test_verify_fails_an_artifact_with_wrong_columns(self):
        self.assertEqual(self.index_xc().returncode, 0)
        p = os.path.join(self.reg, "objects.tsv")
        with open(p) as f:
            text = f.read()
        with open(p, "w") as f:
            f.write(text.replace("\tstorage\t", "\tstorage_class\t", 1))
        r = self.tool("verify")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("objects.tsv has columns", r.stdout)

    def test_cxx_detection_ignores_a_comment_line_that_reads_as_cxx(self):
        write(os.path.join(self.src, "src", "nxt_note.c"), textwrap.dedent("""\
            /*
               class Foo would be C++; this line is a comment.
             */

            int
            nxt_note_fn(void)
            {
                return 0;
            }
            """))
        self.assertEqual(self.index().returncode, 0)
        self.assertEqual(self.manifest()["cxx_files"], "-")

    def make_run(self, *args):
        r = self.run_cmd("make", "-s", "-C", REPO, "registry",
                         "UNIT_SRC=" + self.src, "REGISTRY_DIR=" + self.reg,
                         *args)
        return r, self.manifest()

    def test_makefile_names_the_build_dir_it_reads(self):
        # src/build exists, so a fallback to it would show nm symbols.
        r, m = self.make_run("BUILD_DIR=" + os.path.join(self.tmp, "nobuild"),
                             "REGISTRY_ARGS=--no-crosscheck")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("no BUILD_DIR", r.stderr)
        self.assertEqual((m["nm_symbols"], m["build_dir"]), ("0", "-"))
        # REGISTRY_ARGS reaches the tool: ast-grep is on PATH here.
        self.assertEqual(m["crosscheck"], "NONE")
        spaced = os.path.join(self.tmp, "build dir")
        write(os.path.join(spaced, "sbin", "unitd"), "")
        r, m = self.make_run("BUILD_DIR=" + spaced,
                             "REGISTRY_ARGS=--no-crosscheck")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(m["build_dir"], spaced)
        self.assertNotEqual(m["nm_symbols"], "0")
        # A ref is indexed with no binary at all.
        self.git_init()
        r, m = self.make_run("REGISTRY_REF=HEAD", "BUILD_DIR=" + spaced,
                             "REGISTRY_ARGS=--no-crosscheck")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((m["ref"], m["nm_symbols"]), ("HEAD", "0"))


if __name__ == "__main__":
    unittest.main()
