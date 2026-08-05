"""Build the interface into the wheel.

An installed Vizmith has no checkout to serve `web/dist` out of, so the built frontend has
to travel inside the package. It is built here rather than committed, because a committed
`dist` is a build artefact in review that nobody reads and that goes stale silently between
the source it came from and the commit that forgot to rebuild it.

The cost is that building a wheel needs Node, which building a wheel did not need before.
That is paid by whoever releases, not by whoever installs, and an editable install skips
this entirely: `pip install -e .` leaves the package where it is and `api.py` falls back to
`web/dist`, which is what a developer already builds with `npm run build`.
"""

import shutil
import subprocess
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

ROOT = Path(__file__).parent
WEB = ROOT / "web"
BUILT = WEB / "dist"
INTO = ROOT / "src" / "vizmith" / "web"


class BuildInterface(BuildHookInterface):
    PLUGIN_NAME = "vizmith-web"

    def initialize(self, version, build_data):
        # Editable installs run from the checkout, where the fallback finds web/dist. Only
        # a wheel that will be moved somewhere else needs its own copy.
        if self.target_name != "wheel" or version == "editable":
            return

        npm = shutil.which("npm")
        if npm is None:
            raise RuntimeError(
                "building a vizmith wheel needs npm, because the interface it serves is "
                "built into it. Install Node, or build from a checkout with pip install -e ."
            )
        subprocess.run([npm, "ci"], cwd=WEB, check=True)
        subprocess.run([npm, "run", "build"], cwd=WEB, check=True)

        if INTO.exists():
            shutil.rmtree(INTO)
        shutil.copytree(BUILT, INTO)
        build_data["artifacts"].append("/src/vizmith/web")
        self._built = True

    def finalize(self, version, build_data, artifact_path):
        # The copy exists to be packaged, not to sit in the source tree afterwards, where
        # the next editable install would serve a stale one out of the package instead of
        # the one npm run build writes.
        if getattr(self, "_built", False) and INTO.exists():
            shutil.rmtree(INTO)
