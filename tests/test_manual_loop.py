import pathlib
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from stepper_control.app import App


class ManualLoopTests(unittest.TestCase):
    def app(self, loop=False, mode="MANUAL", steps="120"):
        app = App.__new__(App)
        app.ready, app.running = True, False
        app.config = Mock(return_value=[100, 800, 1000])
        app.mode = Mock(get=Mock(return_value=mode))
        app.manual_loop = Mock(get=Mock(return_value=loop))
        app.steps = Mock(get=Mock(return_value=steps))
        app.direction = Mock(get=Mock(return_value="CCW"))
        app.connection = Mock()
        app.update_controls = Mock()
        app.add_log = Mock()
        return app

    def test_loop_uses_manual_steps_independent_of_revolution_size(self):
        app = self.app(loop=True)
        app.start()
        self.assertEqual([call.args[0] for call in app.connection.send.call_args_list],
                         ["CONFIG 100 800 1000", "START MANUAL_LOOP 120"])

    def test_single_move_retains_selected_direction(self):
        app = self.app()
        app.start()
        app.connection.send.assert_called_with("START MANUAL 120 CCW")

    def test_continuous_ignores_manual_loop_checkbox(self):
        app = self.app(loop=True, mode="CONTINUOUS")
        app.start()
        app.connection.send.assert_called_with("START CONTINUOUS")

    def test_invalid_loop_count_sends_no_commands(self):
        app = self.app(loop=True, steps="0")
        with patch("stepper_control.app.messagebox.showerror") as dialog:
            app.start()
        dialog.assert_called_once()
        app.connection.send.assert_not_called()

    @unittest.skipUnless(shutil.which("g++"), "g++ unavailable for firmware simulation")
    def test_firmware_motion_simulation(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as folder:
            executable = pathlib.Path(folder) / "motion_test.exe"
            subprocess.run([shutil.which("g++"), "-std=c++11", "-Wall", "-Wextra",
                            "-I", str(root / "tests/firmware"),
                            str(root / "tests/firmware/test_motion.cpp"),
                            "-o", str(executable)], check=True, capture_output=True, text=True)
            subprocess.run([str(executable)], check=True, capture_output=True, text=True)


if __name__ == "__main__":
    unittest.main()
