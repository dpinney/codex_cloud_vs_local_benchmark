# Codex Cloud vs. Local: A Quota Consumption Benchmark

David Pinney — November 25, 2026

**Codex Cloud consumed 33% less five-hour subscription quota than local Codex in one paired benchmark.** This runs counter to historical reports of a cloud usage premium.

## Method

Both environments received an identical prompt to implement a miniature SQL engine in Python, with correctness assessed against 640 deterministic SQLite reference queries (40 queries across 16 datasets).

- Model: GPT-6.2 Sol, High reasoning effort.
- Subscription: ChatGPT Plus.
- Measurement: Codex App Server quota readings before and after execution, with 45 seconds of post-run sampling.
- Order: local CLI, then a fresh Codex Cloud task.

## Results

| Metric | Local CLI | Codex Cloud |
|---|---:|---:|
| Five-hour quota consumed | 6 pp | 4 pp |
| Weekly quota consumed | 1 pp | 1 pp |
| Reported reference queries | 640/640 | 640/640 |
| Result digest | Correct | Correct |
| Relative five-hour quota consumption | 100% | 67% |

Accounting for integer percentage rounding, cloud consumed approximately 43–100% of local five-hour quota. Weekly consumption was inconclusive. An earlier, smaller pilot also found no measurable cloud premium, but had insufficient measurement resolution.

## Limitations

One substantial paired experiment. The rounding bounds assume consistent integer rounding and timely accounting, with no unrelated quota consumption. They are not statistical confidence intervals. Cloud token counts were unavailable; neither implementation was independently inspected. The returned digest matched an independent SQLite calculation, but does not establish how the agent produced it. The experiment measures subscription quota consumption, not computational cost.

Neither experiment found evidence of a cloud usage premium. The larger benchmark suggests a potential cloud advantage. These results do not support preferentially running Codex locally solely to conserve subscription quota, though further replications are needed.

## Reproduce

Requires Python 3.9+, the Codex CLI signed in with ChatGPT, and access to Codex Cloud. No external Python dependencies.

```sh
python3 benchmark.py --self-test
python3 benchmark.py
```

The runner computes its own SQLite reference digest, launches local Codex with a pinned model and effort, reads quota, and provides the identical prompt for manual cloud submission. Follow the printed instructions. Avoid unrelated Codex/Work activity and quota resets during the pair. Use `--cloud-first` to reverse the order on a replication, and `--output PATH` to avoid overwriting earlier results. Use `--help` for other options. Ensure sufficient included quota; the runner checks headroom but cannot guarantee that a task will not exhaust it.

`benchmark.py` is the original v2 runner used for the experiment. `results.json` contains a transcription of the reported v2 terminal output, not the original raw log. It excludes private account data. The runner's own output file may contain a purchased-credit balance and other private details; review before publishing.
