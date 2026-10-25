from pathlib import Path
import unittest

from mfi_service import __version__


ROOT = Path(__file__).resolve().parents[1]


class DeploymentContractTest(unittest.TestCase):
    def test_default_build_is_binary_only_without_compiler(self):
        content = (ROOT / "Dockerfile").read_text()
        self.assertIn("--only-binary=:all:", content)
        self.assertNotIn("gcc libc6-dev", content)
        self.assertNotIn("AS builder", content)

    def test_armv7_uses_separate_multistage_build(self):
        self.assertIn("Dockerfile.armv7", (ROOT / "compose.armv7.yaml").read_text())
        content = (ROOT / "Dockerfile.armv7").read_text()
        self.assertIn("gcc libc6-dev libffi-dev", content)
        self.assertIn("COPY --from=builder", content)

    def test_single_compose_defaults_are_versioned_and_authenticated(self):
        content = (ROOT / "compose.yaml").read_text()
        self.assertIn("pull_policy: build", content)
        self.assertIn("0.0.0.0:8787:8787", content)
        self.assertIn('MFI_AUTH_TOKEN: "CHANGE_ME"', content)
        self.assertLess(len("CHANGE_ME"), 24)
        self.assertNotIn("${MFI_AUTH_TOKEN", content)
        self.assertIn("remote-mfi:" + __version__, content)


if __name__ == "__main__":
    unittest.main()
