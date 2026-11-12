"""Pilot tests for the Textual app in tools/model/tui. They skip without textual.

    uv run --no-project --with textual==8.2.8 python3 -m unittest tools.model.tests.test_tui_app -v
"""

import asyncio
import importlib.util
import unittest

HAVE_TEXTUAL = importlib.util.find_spec("textual") is not None

if HAVE_TEXTUAL:
    from textual.widgets import Checkbox, Input, ListView, Static

    from tools.model.tui.app import HelpScreen, ModelTui

SIZE = (140, 40)


@unittest.skipUnless(HAVE_TEXTUAL, "textual is not installed")
class AppTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # unittest runs the loop in debug mode; its slow-callback lines are noise here.
        asyncio.get_running_loop().set_debug(False)

    async def _finished(self, app, timeout=30):
        await asyncio.wait_for(app.run_done.wait(), timeout)
        return app.history[0]

    def _status(self, app):
        return str(app.query_one("#status", Static).content)

    async def test_select_builds_argv_and_help_opens(self):
        app = ModelTui()
        async with app.run_test(size=SIZE) as pilot:
            names = [c.name for c in app.commands]
            lv = app.query_one("#commands", ListView)
            lv.focus()
            lv.index = names.index("verify")
            await pilot.press("enter")
            await pilot.pause()
            self.assertEqual(app.current.name, "verify")
            self.assertEqual(app.current_argv(),
                             ["python3", "-m", "tools.model.cli", "verify", "--seed", "42"])
            app.query_one("#opt-chaos", Checkbox).value = True
            app.query_one("#opt-seed", Input).value = "7"
            await pilot.pause()
            self.assertEqual(app.current_argv()[-3:], ["--chaos", "--seed", "7"])
            self.assertIn("--chaos --seed 7", str(app.query_one("#argv", Static).content))
            await pilot.press("question_mark")
            self.assertIsInstance(app.screen, HelpScreen)
            await pilot.press("escape")
            self.assertNotIsInstance(app.screen, HelpScreen)

    async def test_run_verify_shows_exit_code(self):
        app = ModelTui()
        async with app.run_test(size=SIZE) as pilot:
            await app.select_command("verify")
            await pilot.press("r")
            rec = await self._finished(app)
            self.assertEqual(rec.code, 0, rec.lines)
            self.assertTrue(any("INVARIANTS SATISFIED" in ln for ln in rec.lines))
            self.assertIn("exit 0", self._status(app))

            # The chaos run reports violations and exits 1. ctrl+r runs it
            # while the seed field has focus; a plain r would go into the field.
            app.query_one("#opt-chaos", Checkbox).value = True
            app.query_one("#opt-seed", Input).focus()
            await pilot.pause()
            await pilot.press("ctrl+r")
            rec = await self._finished(app)
            self.assertEqual(rec.code, 1)
            self.assertEqual(app.query_one("#opt-seed", Input).value, "42")
            self.assertIn("exit 1", self._status(app))
            self.assertEqual(len(app.history), 2)

    async def test_cancel_stops_long_command(self):
        app = ModelTui()
        async with app.run_test(size=SIZE) as pilot:
            await app.select_command("simulate")
            # About 5 s of work. The test cancels after the first output line.
            app.query_one("#opt-requests", Input).value = "100000"
            await pilot.pause()
            await pilot.press("ctrl+r")
            for _ in range(200):
                if app.last and app.last.lines:
                    break
                await asyncio.sleep(0.05)
            self.assertTrue(app.runner.running)
            await pilot.press("escape")
            rec = await self._finished(app, timeout=10)
            self.assertTrue(rec.cancelled)
            self.assertEqual(rec.code, -15)
            self.assertLess(rec.seconds, 4)
            self.assertFalse(app.runner.running)
            self.assertIn("cancelled", self._status(app))


if __name__ == "__main__":
    unittest.main()
