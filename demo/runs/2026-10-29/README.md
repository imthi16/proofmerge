# Final demo runs, 2026-10-29

These three evidence bundles are from real `proofmerge verify` runs on the demo
shop, on main `bcd0032`. They are the same runs shown in the README.

| Bundle | Verdict | Base | Head | Calls | Prompt | Completion |
|---|---|---|---|---|---|---|
| `tenant-cache-take2` | BLOCK | `9dcc104` | `32dff53` | 6 | 5702 | 9902 |
| `test-tamper` | BLOCK | `9dcc104` | `05b99e9` | 5 | 4091 | 3634 |
| `intended-change` | REVIEW | `9dcc104` | `ade6dc5` | 3 | 2185 | 3098 |

Check any bundle:

```bash
proofmerge replay demo/runs/2026-10-29/<bundle>/proof.json
```

Expected output: `evidence intact: N files`.
