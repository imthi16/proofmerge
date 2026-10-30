# Model backends

ProofMerge uses two model roles. **Spark** writes intent claims, failure
hypotheses and probes. **Wonder** cross-checks claims and probes. The default is
the Meta Model API with Muse Spark 1.3 and Muse Wonder 1.0, and every benchmark
number in this repository was measured with that default. With any other model
the verdict rules stay the same (BLOCK still needs base PASS 3/3 and head FAIL
3/3), but how good the probes are is untested.

Each `proof.json` records the endpoint and the model for each role under
`model_usage`, and the report shows them under Provenance.

## Settings

| Variable | Meaning | Default |
|---|---|---|
| `PROOFMERGE_BACKEND` | `api`, or a CLI backend below | `api` |
| `MODEL_API_KEY` | key for the `api` backend | none |
| `PROOFMERGE_BASE_URL` | any OpenAI-compatible endpoint for the `api` backend | `https://api.meta.ai/v1` |
| `PROOFMERGE_SPARK_MODEL` | model for the Spark role | `muse-spark-1.3`; a CLI backend uses its own default |
| `PROOFMERGE_WONDER_MODEL` | model for the Wonder role | `muse-wonder-1.0`; a CLI backend uses its own default |
| `PROOFMERGE_ALLOW_CONTRIBUTOR` | `1` allows `*-contributor` models | off |

Run `proofmerge smoke` after changing them: it makes one Spark-role call and
prints the model and endpoint it used.

## A plan instead of an API key

### OpenAI-compatible plans (for example OpenCode Go)

OpenCode's model catalog lists the OpenCode Go plan at
`https://opencode.ai/inference/go/v1`. Its only Muse model is
`muse-spark-1.3-contributor`, and the plan has no Muse Wonder:

```bash
export PROOFMERGE_BASE_URL=https://opencode.ai/inference/go/v1
export MODEL_API_KEY=...                       # your OpenCode Go key
export PROOFMERGE_SPARK_MODEL=muse-spark-1.3-contributor
export PROOFMERGE_ALLOW_CONTRIBUTOR=1          # read the warning below first
export PROOFMERGE_WONDER_MODEL=<another model on the plan>
```

> [!WARNING]
> Contributor models may use your prompts for training, and ProofMerge's prompts
> contain your repository's code and diff. ProofMerge refuses them unless
> `PROOFMERGE_ALLOW_CONTRIBUTOR=1` is set, and the report then says so. Use it
> only for code you are allowed to share.

This path is built and tested offline. It has not yet been run against the
OpenCode Go endpoint.

### Coding-agent CLIs you are signed in to

`PROOFMERGE_BACKEND=claude-cli` sends each call through `claude -p`, so it uses
the plan Claude Code is signed in to and needs no `MODEL_API_KEY`. Leave the
model variables unset to use Claude Code's default model, or set them to a
Claude Code model name such as `haiku`. Setting `PROOFMERGE_WONDER_MODEL` to a
different model from Spark keeps Wonder's cross-check independent; with both
unset, one model plays both roles.

Prompts contain untrusted repository text, so a CLI is supported only if it can
run with no tools at all. Each call runs in a new empty directory, with only
`PATH`, `HOME`, locale and config-directory variables passed on, and the CLI's
stderr is never echoed.

Canary check: an empty directory holds `canary.txt`, and the prompt asks the
CLI to read it with any tool it has. A CLI passes if the reply does not contain
the file's text.

| CLI | Flags tried | Result (2026-10-30) | Status |
|---|---|---|---|
| Claude Code 2.1.314 | `-p --tools "" --strict-mcp-config --setting-sources "" --no-session-persistence --json-schema` | could not read the file; only structured output was available | supported |
| Grok CLI | `-p --tools "" --disable-web-search --permission-mode default`, and again with `--permission-mode dontAsk` | read the file both times | refused |
| Codex CLI 0.165.0 | `exec` has no option that removes its shell tool; a read-only sandbox can still read files | not run | not supported |
| Gemini CLI, Cursor Agent, Kimi, Crush, Antigravity, OpenCode | none yet | not run | not supported |

A CLI moves to supported only after it passes the canary check with recorded
flags.
