# Data Catalog Semantic Layer Integration

This repository can consume the governed semantic-layer draft produced by the
`data-catalog-semantic-layer` skill and export the normalized model to Apache
Ossie or AtScale SML-shaped objects.

## Package

- [`data-catalog-semantic-layer-skill.zip`](./data-catalog-semantic-layer-skill.zip)

The package contains the skill instructions, inference rules, output contract,
scaffold generator, and Semantic Designer integration guidance.

## Flow

```text
Data Catalog metadata
        |
        v
data-catalog-semantic-layer skill
        |
        v
tool-neutral semantic_layer YAML/JSON
        |
        v
load_catalog_semantic_layer()
        |
        +--> ossie_document()       -> Apache Ossie 0.3.0.dev0 document
        +--> write_sml_repository() -> AtScale SML-shaped object repository
```

The tool-neutral artifact remains the canonical source. Governance metadata,
catalog limitations, review status, and source metadata are retained in
round-trip extensions. Generated formats require their own validator and
golden-query checks before production use.

## Example

Run from the repository root:

```bash
PYTHONPATH=. python3 - <<'PY'
from pathlib import Path
import json
import server

model = server.load_catalog_semantic_layer("semantic-layer.yaml")
Path("/tmp/fubon-ossie.json").write_text(
    json.dumps(server.ossie_document(model), ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
server.write_sml_repository(model, "/tmp/fubon-sml")
PY
```

See the main [README](../README.md#catalog-semantic-layer-adapters) for API
details and limitations.
