"""Repository hygiene: committed data files parse, no secrets in examples."""

import json
import re
import unittest

from tests import support

ROOT = support.ROOT
JSON_DIRS = ("agent", "eval", "corpus")
SECRET_NAME = re.compile(r"(KEY|SECRET|TOKEN|PASSWORD)")
PLACEHOLDERS = ("", "<set-me>")


class RepoTest(unittest.TestCase):
    def test_json_files_parse(self):
        paths = [path for folder in JSON_DIRS for path in (ROOT / folder).rglob("*.json")]
        self.assertTrue(paths)
        for path in paths:
            with self.subTest(path=str(path.relative_to(ROOT))):
                json.loads(path.read_text(encoding="utf-8"))

    def test_env_example_has_no_secret_values(self):
        for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
            name, separator, value = line.partition("=")
            if line.startswith("#") or not separator or not SECRET_NAME.search(name):
                continue
            with self.subTest(name=name):
                self.assertIn(value.strip(), PLACEHOLDERS)

    def test_systemd_units_start_existing_modules(self):
        # ExecStart is `python -m kb.x` or `uvicorn kb.x:app`, run from WorkingDirectory=/home/ubuntu/cuc-kb.
        units = list((ROOT / "deploy").glob("*.service"))
        self.assertTrue(units)
        for unit in units:
            exec_start = re.search(r"^ExecStart=\S+ (?:-m )?([\w.]+)", unit.read_text(encoding="utf-8"), re.M)
            with self.subTest(unit=unit.name):
                self.assertIsNotNone(exec_start)
                module = exec_start.group(1).replace(".", "/") + ".py"
                self.assertTrue((ROOT / module).is_file(), module)

    def test_run_mcp_points_at_existing_script(self):
        script = re.search(r"/cuc-kb/(\S+\.py)", (ROOT / "deploy" / "run-mcp.sh").read_text(encoding="utf-8"))
        self.assertIsNotNone(script)
        self.assertTrue((ROOT / script.group(1)).is_file())


if __name__ == "__main__":
    unittest.main()
