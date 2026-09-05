# SPDX-License-Identifier: Apache-2.0
"""Doc-code drift guard: every user-facing surface must be mentioned in the user documentation."""

import re
from pathlib import Path

from pipeline.config import Settings

ROOT = Path(__file__).resolve().parent.parent
DOCS = (ROOT / "docs").glob("*.md")
DOC_TEXT = "\n".join(p.read_text(encoding="utf-8") for p in DOCS)
CONFIG_DOC = (ROOT / "docs" / "04-configuration.md").read_text(encoding="utf-8")


def test_every_api_route_is_documented():
    api = (ROOT / "pipeline" / "api.py").read_text(encoding="utf-8")
    routes = re.findall(r'@app\.(?:get|post)\("([^"]+)"', api)
    assert routes, "no routes found"
    for route in routes:
        generic = re.sub(r"\{\w+\}", "{id}", route)
        assert generic in CONFIG_DOC, f"route {generic} missing from docs/04-configuration.md"


def test_every_cli_command_is_documented():
    cli = (ROOT / "pipeline" / "cli.py").read_text(encoding="utf-8")
    names = re.findall(r'@app\.command\("([^"]+)"\)', cli)
    names += [f.replace("_", "-") for f in re.findall(r"@app\.command\(\)\ndef (\w+)\(", cli)]
    assert len(names) >= 8
    for name in names:
        assert f"`{name}" in CONFIG_DOC, f"CLI command {name} missing from docs/04-configuration.md"


def test_every_setting_is_documented():
    for field in Settings.model_fields:
        assert field.upper() in CONFIG_DOC, (
            f"setting {field.upper()} missing from docs/04-configuration.md"
        )


def test_workflow_node_names_match_docs_and_export():
    import json

    wf = json.loads((ROOT / "n8n" / "refresh-workflow.json").read_text(encoding="utf-8"))
    names = {n["name"] for n in wf["nodes"] if n["type"] != "n8n-nodes-base.stickyNote"}
    assert "Save summary" in names and "Fetch: Postbank Giro" in names
    assert "Save summary" in DOC_TEXT
