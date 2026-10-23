"""GUI entry point"""

import os
import sys


def run():
    from emu68hatcher import __version__
    from emu68hatcher.utils.logging import get_logger, start_app_log
    from emu68hatcher.utils.paths import get_app_log_path
    from emu68hatcher.utils.platform import get_platform_info

    start_app_log(get_app_log_path())
    get_logger().info(f"Emu68 Hatcher {__version__} on {get_platform_info().platform_string}")
    # the gtk3 theme in the linux bundle brings its own atk-bridge, which registers a second,
    # empty application on the a11y bus next to qt's; one registration per process is enough
    if sys.platform.startswith("linux"):
        os.environ.setdefault("NO_AT_BRIDGE", "1")

    try:  # frozen builds use fbs ApplicationContext
        from fbs_runtime.application_context.PySide6 import ApplicationContext
    except ImportError:
        ApplicationContext = None

    if ApplicationContext is None:
        from emu68hatcher.gui.main_window import launch_gui

        launch_gui()
        return

    class AppContext(ApplicationContext):
        def run(self):
            from emu68hatcher.gui.main_window import MainWindow

            self.window = MainWindow()
            self.window.show()
            return self.app.exec()

    sys.exit(AppContext().run())
