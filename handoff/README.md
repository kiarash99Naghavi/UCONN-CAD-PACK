# handoff/

Curated, git-tracked outputs from the full 48-task benchmark run and the offline
selector study. Everything here is small and durable; the bulk artifacts
(`ourimplementation/results/runs/`, 5.8 GB of per-attempt STEP/STL/renders) stay
gitignored and are regenerable.

| file | what it is |
|---|---|
| `RESULTS.md` | headline numbers, per-difficulty breakdown, which fixes have causal evidence |
| `AGENT_HANDOFF.md` | **start here if you are picking this up** — failure taxonomy, rejected approaches, next steps |
| `scores/benchmark_48.csv` | per-task: ours vs `other human` vs `gpt-5.2` baseline, with the instruction text |
| `scores/ours_adk-router.json` | the sweep score file written by `ourimplementation/evaluate.py` |
| `mbr/mbr_report.md` | offline selector study — MBR vs what the pipeline shipped |
| `mbr/mbr_groups.csv` | per-group detail: candidates, ground-truth scores, edit voxel counts, each selector's pick |
| `mbr/mbr_summary.json` | machine-readable aggregates |
| `DATA.md` | **schema for the two files below** — read before consuming them |
| `runs.jsonl` | 48 rows: instruction, strategist plan, sub-goals + tags, scores, tokens |
| `candidates.jsonl` | 191 rows: every attempt — sub-goal text, tags, verdict/gate, issues, **full CadQuery source**, ground-truth score |
| `renders/` | 48 tasks x 7 views of the final shipped geometry (7.3 MB) |
| `REPLAY.md` | **how to run the executor alone with zero API calls**, and merge results |
| `prompts.jsonl` | 189 verbatim executor calls: system + prompt + source solid + reference score |

## Reproducing

```bash
# full 48-task aggregate + the sweep score file
cd sourcecode/IDETC26-Hackathon-Autodesk-neuralCAD-Edit
PYTHONPATH="$PWD:$(pwd)/../.." .venv/bin/python -m ourimplementation.evaluate \
    --user-id ours_adk-router

# the offline selector study (no API, no GPU)
cd /Users/kiarash/Downloads/2026ASMEHackathon
PYTHONPATH="$PWD/sourcecode/IDETC26-Hackathon-Autodesk-neuralCAD-Edit:$PWD" \
    sourcecode/IDETC26-Hackathon-Autodesk-neuralCAD-Edit/.venv/bin/python \
    tools/mbr_offline.py
```

```bash
# the verbatim executor prompts (for executor-only replay)
cd sourcecode/IDETC26-Hackathon-Autodesk-neuralCAD-Edit
PYTHONPATH="$PWD:$(pwd)/../.." .venv/bin/python ../../tools/export_prompts.py
```

```bash
# the plan / sub-goal / candidate export
cd sourcecode/IDETC26-Hackathon-Autodesk-neuralCAD-Edit
PYTHONPATH="$PWD:$(pwd)/../.." .venv/bin/python ../../tools/export_handoff.py
```

All three read only from `ourimplementation/results/`; none calls a model.

## Note on `.gitignore`

`ourimplementation/results` is excluded wholesale, which means the
`!ourimplementation/results/scores/` negations below it never take effect — git does
not descend into an excluded directory. That is why the scores are copied here rather
than tracked in place. To fix it properly, exclude the children instead of the parent:

```
ourimplementation/results/*
!ourimplementation/results/scores/
```
