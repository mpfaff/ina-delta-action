# ina-delta-action

**THIS PROJECT IS LARGELY AI GENERATED. USE AT YOUR OWN RISK.**

Generates an [ina](https://github.com/accrescent/ina) delta between the artifact of one workflow
run and the same artifact of an earlier run. Works on GitHub Actions and Forgejo Actions.

Run it in its own workflow, after the build workflow has published its artifacts.

## How the base is chosen

The action takes the run that built the new artifact (`new-run-id`) and an `artifact-pattern`.
The pattern is a regular expression that must match the whole artifact name. It is used to find
the artifact in both the new run and the previous run.

If `previous-run-id` is set, that run is the base. Otherwise the action goes back through the
successful runs of the same workflow, newest first, and takes the first earlier run on the same
branch:

- If that run has an artifact matching the pattern, it is the base.
- If it does not, the chain starts after it. The action makes no delta and does not look further
  back.

The delta is written to `ina-delta/<file name>.ina`. Each artifact must contain exactly one file.

### GitHub

Copy [`examples/github/ina-delta.yml`](examples/github/ina-delta.yml) to `.github/workflows/`.
It runs automatically when the `Build image` workflow completes successfully, or manually with a
run ID.

### Forgejo

Copy [`examples/forgejo/ina-delta.yml`](examples/forgejo/ina-delta.yml) to `.forgejo/workflows/`.
Forgejo has no `workflow_run` trigger. Run the workflow manually, or start it from the end of the
build workflow, after the artifact upload:

```yaml
      - name: Start ina delta
        env:
          TOKEN: ${{ secrets.DISPATCH_TOKEN }}  # a token that can dispatch workflows
        run: >
          curl -fsS -X POST
          -H "Authorization: token $TOKEN" -H "Content-Type: application/json"
          "${{ forgejo.api_url }}/repos/${{ forgejo.repository }}/actions/workflows/ina-delta.yml/dispatches"
          -d '{"ref": "${{ forgejo.ref_name }}", "inputs": {"new-run-id": "${{ forgejo.run_id }}"}}'
```

The two workflow files differ only in their trigger and their upload step. On Forgejo, the
upstream `actions/upload-artifact@v4` fails, so the Forgejo example uses
`code.forgejo.org/forgejo/upload-artifact@v4`.

## Requirements

- Python 3.11 or later on the runner. No other packages are needed.
- A token that can read Actions runs and artifacts. On GitHub that is `actions: read`.
- Forgejo: a version with the Actions artifacts REST API (`/repos/{owner}/{repo}/actions/artifacts`).
- `ina diff` holds the whole base file in memory plus a 4-bytes-per-byte index. A 1 GB image needs
  about 6 GB of RAM.

## Inputs and outputs

| Input              | Description                                                       |
|--------------------|-------------------------------------------------------------------|
| `artifact-pattern` | Regular expression matching the full artifact name, in both runs. |
| `new-run-id`       | Run that produced the new artifact.                               |
| `previous-run-id`  | Optional. Run that produced the base artifact.                    |
| `ina-url`          | Release URL that holds the ina binaries.                          |
| `token`            | Default: the workflow token.                                      |

| Output            | Description                                     |
|-------------------|-------------------------------------------------|
| `delta-path`      | Path of the delta. Empty when there is no base. |
| `delta-name`      | File name of the delta.                         |
| `previous-run-id` | Run that the base came from.                    |
