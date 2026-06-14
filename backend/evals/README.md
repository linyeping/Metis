# Metis Evals

Run the fixed smoke suite from the repository root. The smoke suite now includes two web capability tasks, so a real provider run needs outbound web access:

```powershell
python -m backend.evals.runner --suite smoke --repeat 3 --backend deepseek --model deepseek-chat --system-prompt-mode prod
```

Run a single offline harness sanity check with the fake backend:

```powershell
python -m backend.evals.runner --suite smoke --task answer-arch --repeat 1 --backend fake --model fake-eval --output-dir backend\var\evals-test
```

Compare a new result against a previous JSON report:

```powershell
python -m backend.evals.runner --suite smoke --repeat 3 --compare evals-results\OLD.json
```

Reports are written as JSON and Markdown under `evals-results/` by default.
`--system-prompt-mode prod` is the default and compiles the production runtime prompt for the fixture workspace; use `bare` only for harness control experiments.
