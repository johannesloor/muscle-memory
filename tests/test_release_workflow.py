"""Security contract for the privileged MCP Registry release workflow."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github/workflows/release.yml"


class ReleaseWorkflowTests(unittest.TestCase):
    def test_publisher_is_pinned_and_verified_before_authentication_or_publish(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertNotRegex(text, r"releases/latest(?:/|\b)")
        self.assertRegex(text, r"MCP_PUBLISHER_VERSION:\s*v\d+\.\d+\.\d+")
        verify = text.index("cosign verify-blob")
        extract = text.index("tar xzf", verify)
        login = text.index("./mcp-publisher login")
        publish = text.index("./mcp-publisher publish")
        self.assertLess(verify, extract)
        self.assertLess(extract, login)
        self.assertLess(login, publish)
        self.assertIn("--certificate-identity", text)
        self.assertIn("--certificate-oidc-issuer", text)
        self.assertIn("https://token.actions.githubusercontent.com", text)
        self.assertIn("set -euo pipefail", text)
        self.assertRegex(text, r"releases/download/\$\{MCP_PUBLISHER_VERSION\}")


if __name__ == "__main__":
    unittest.main()
