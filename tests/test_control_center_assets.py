"""The Control Center's committed build and generated types match their sources."""

import json
from pathlib import Path

from proofmerge.dashboard import STATIC
from proofmerge.dashboard_models import api_schema

UI = Path(__file__).parent.parent / "ui"


def test_committed_schema_matches_the_api_models() -> None:
    # Regenerate with: python -m proofmerge.dashboard_models > ui/src/api/schema.json
    # then `npm run gen:types` in ui/.
    committed = json.loads((UI / "src" / "api" / "schema.json").read_text())
    assert committed == json.loads(json.dumps(api_schema(), sort_keys=True))


def test_built_app_is_packaged_and_self_contained() -> None:
    index = (STATIC / "app" / "index.html").read_text()
    assets = sorted((STATIC / "app" / "assets").iterdir())
    assert {p.suffix for p in assets} == {".js", ".css"}
    for asset in assets:
        assert f"/static/app/assets/{asset.name}" in index
    # Same-origin only: the page's CSP is default-src 'self'.
    assert "http://" not in index and "https://" not in index
    bundle = "".join(p.read_text() for p in assets)
    assert "dangerouslySetInnerHTML" not in "".join(
        p.read_text() for p in (UI / "src").rglob("*.tsx"))
    assert "MODEL_API_KEY" not in bundle
