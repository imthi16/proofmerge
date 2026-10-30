# Go demo run, 2026-10-30

A real `proofmerge verify` run on the Go demo repository, built with
`python3 demo/build_go_demo.py /tmp/proofmerge-go-demo` (the build is
deterministic, so the commits below are the same on every build).

| Bundle | Verdict | Base | Head | Calls | Prompt | Completion |
|---|---|---|---|---|---|---|
| `go-tenant-cache` | BLOCK | `8aae692` | `672f3f5` | 5 | 4438 | 7449 |

The repository's own `go test ./pricing` passes on both commits. Muse Spark wrote
one Go probe: price a known tenant, then ask for an unknown tenant and expect an
error. It passed 3/3 on base and failed 3/3 on head, where the sku-only cache
returns a price instead.

Check the bundle, or re-run its experiment in Docker:

```bash
proofmerge replay demo/runs/2026-10-30-go/go-tenant-cache/proof.json
proofmerge replay demo/runs/2026-10-30-go/go-tenant-cache/proof.json --rerun --repo /tmp/proofmerge-go-demo
```

Expected: `evidence intact: 18 files`, then `H1 base PPP head FFF: reproduced`.
