"""Docs/metadata contract: README, server.json and pyproject.toml discoverability fields.

These are plain text/structure checks, not behaviour tests: they pin the exact strings and
fields that Task 8's brief requires, so the README/server.json/pyproject.toml can't silently
drift away from what the code actually does (e.g. re-introducing a `python3`/`mm.py` mention,
or losing the MCP Registry ownership marker).
"""
import json
import sys
import unittest
from pathlib import Path

import muscle_memory_mcp

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
SERVER_JSON = ROOT / "server.json"
PYPROJECT = ROOT / "pyproject.toml"


class ReadmeTests(unittest.TestCase):
    def setUp(self):
        self.text = README.read_text(encoding="utf-8")

    def test_contains_required_strings(self):
        for needle in ("uvx muscle-memory-mcp", "MUSCLE_MEMORY_DIR", "MUSCLE_MEMORY_PROJECT_DIR",
                       "## Add to your AGENTS.md"):
            self.assertIn(needle, self.text, f"README missing {needle!r}")

    def test_excludes_python_bound_language(self):
        for banned in ("python3", "always Python", "mm.py"):
            self.assertNotIn(banned, self.text, f"README must not mention {banned!r}")

    def test_installer_first_readme_structure_and_safety_guidance(self):
        headings = [line for line in self.text.splitlines() if line.startswith("## ")]
        self.assertEqual(headings[:4], [
            "## Install", "## Add to your AGENTS.md", "## First run and storage",
            "## Tools",
        ])
        self.assertLess(self.text.index("## Install"), self.text.index("## Add to your AGENTS.md"))
        self.assertIn("run as you", self.text)
        self.assertEqual(headings[-2:], ["## Security and limits", "## License"])
        self.assertIn("no sandbox", self.text)
        self.assertIn("trust --project", self.text)
        self.assertIn("depends on the MCP client's working directory", self.text)
        self.assertIn("MUSCLE_MEMORY_PROJECT_DIR", self.text)
        self.assertNotIn("session limit", self.text)
        self.assertNotIn("safe isolated one-shot", self.text)
        self.assertNotIn("always runs the latest", self.text)

    def test_agents_snippet_is_short_and_names_the_tools(self):
        snippet = self.text.split("```text", 1)[1].split("```", 1)[0].strip().splitlines()
        self.assertLessEqual(len(snippet), 3)
        joined = "\n".join(snippet)
        for tool in ("mm_list", "mm_run", "mm_save"):
            self.assertIn(tool, joined)
        self.assertIn("Tool descriptions", self.text)

    def test_pitch_line_lives_in_registry_description(self):
        desc = json.loads(SERVER_JSON.read_text(encoding="utf-8"))["description"]
        self.assertEqual(desc, "MCP server that gives your agent a library of its own tested, reusable scripts.")
        self.assertLessEqual(len(desc), 100)

    def test_title_and_intro_name_the_server(self):
        first_heading = next(line for line in self.text.splitlines() if line.startswith("#"))
        self.assertEqual(first_heading, "# Muscle Memory MCP Server")
        self.assertIn("The Muscle Memory MCP server", self.text)

    def test_banner_is_committed_and_linked_absolutely(self):
        # Absolute URL so the image also renders on PyPI, where relative paths break.
        url = "https://raw.githubusercontent.com/johannesloor/muscle-memory/main/assets/banner.png"
        self.assertIn(f"]({url})", self.text)
        self.assertTrue((ROOT / "assets" / "banner.png").is_file())
        self.assertLess(self.text.index(url), self.text.index("# Muscle Memory MCP Server"))

    def test_install_buttons_carry_the_uvx_config(self):
        import base64, re
        from urllib.parse import parse_qs, urlparse
        want = {"command": "uvx", "args": ["muscle-memory-mcp"]}
        urls = re.findall(r"\]\((https://(?:insiders\.vscode\.dev|cursor\.com|lmstudio\.ai)/[^)\s]+)\)", self.text)
        hosts = sorted({urlparse(u).netloc for u in urls})
        self.assertEqual(hosts, ["cursor.com", "insiders.vscode.dev", "lmstudio.ai"])
        self.assertEqual(len(urls), 4)
        for u in urls:
            q = parse_qs(urlparse(u).query)
            self.assertEqual(q["name"], ["muscle-memory"])
            raw = q["config"][0]
            cfg = json.loads(raw if raw.startswith("{") else base64.b64decode(raw).decode("utf-8"))
            self.assertEqual(cfg, want, u)

    def test_status_badges_point_at_this_project(self):
        for needle in ("img.shields.io/pypi/v/muscle-memory-mcp",
                       "johannesloor/muscle-memory/ci.yml",
                       "license-MIT"):
            self.assertIn(needle, self.text)

    def test_contains_mcp_registry_ownership_marker(self):
        lines = [line.strip() for line in self.text.splitlines()]
        self.assertIn("<!-- mcp-name: io.github.johannesloor/muscle-memory -->", lines)


class ServerJsonTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(SERVER_JSON.read_text(encoding="utf-8"))

    def test_identity_and_schema(self):
        self.assertEqual(self.data["name"], "io.github.johannesloor/muscle-memory")
        self.assertEqual(
            self.data["$schema"],
            "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json",
        )
        self.assertEqual(self.data["repository"]["url"],
                         "https://github.com/johannesloor/muscle-memory")

    def test_pypi_package_entry(self):
        pkg = self.data["packages"][0]
        self.assertEqual(pkg["registryType"], "pypi")
        self.assertEqual(pkg["identifier"], "muscle-memory-mcp")
        self.assertEqual(pkg["transport"]["type"], "stdio")

    def test_version_matches_package_version(self):
        self.assertEqual(self.data["version"], muscle_memory_mcp.__version__)
        self.assertEqual(self.data["packages"][0]["version"], muscle_memory_mcp.__version__)


@unittest.skipIf(sys.version_info < (3, 11), "tomllib requires Python >= 3.11")
class PyprojectMetadataTests(unittest.TestCase):
    def setUp(self):
        import tomllib
        with open(PYPROJECT, "rb") as f:
            self.data = tomllib.load(f)

    def test_keywords_cover_discovery_terms(self):
        keywords = set(self.data["project"].get("keywords", []))
        required = {"mcp", "mcp-server", "model-context-protocol", "ai-agents",
                    "claude-code", "codex", "cursor", "automation"}
        missing = required - keywords
        self.assertFalse(missing, f"pyproject.toml keywords missing: {missing}")

    def test_project_urls(self):
        urls = self.data["project"].get("urls", {})
        for key in ("Homepage", "Repository", "Issues"):
            self.assertIn(key, urls, f"project.urls missing {key!r}")

    def test_readme_declared(self):
        self.assertEqual(self.data["project"].get("readme"), "README.md")


if __name__ == "__main__":
    unittest.main()
