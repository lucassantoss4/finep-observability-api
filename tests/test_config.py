import json
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]


def test_configuration_files_parse():
    for path in ROOT.rglob("*.yaml"):
        assert isinstance(yaml.safe_load(path.read_text()), dict), path
    dashboard = json.loads((ROOT / "observability/grafana/dashboards/overview.json").read_text())
    assert dashboard["uid"] == "finep-overview"
    assert len(dashboard["panels"]) == 6


def test_compose_mounts_exist():
    config = yaml.safe_load((ROOT / "compose.yaml").read_text())
    for service in config["services"].values():
        for mount in service.get("volumes", []):
            source = mount.split(":")[0]
            if source.startswith("./"):
                assert (ROOT / source).exists(), source
