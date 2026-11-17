#!/usr/bin/env python3
"""Generate an ina delta between the artifacts of two workflow runs.

Works with GitHub Actions and Forgejo Actions, whose REST APIs expose the same
endpoints with slightly different field names.

Configuration comes from environment variables set by action.yml.
"""

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Run:
    id: int
    workflow: str
    ref: str


@dataclass
class Artifact:
    id: int
    name: str


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Api:
    """Minimal client for the Actions endpoints shared by GitHub and Forgejo."""

    def __init__(self, api_url: str, repository: str, token: str):
        self.repo_url = f"{api_url}/repos/{repository}"
        self.token = token
        self.is_forgejo = api_url.endswith("/api/v1")
        self.opener = urllib.request.build_opener(NoRedirect)

    def open(self, path: str):
        request = urllib.request.Request(
            self.repo_url + path, headers={"Authorization": f"Bearer {self.token}"}
        )
        try:
            return self.opener.open(request)
        except urllib.error.HTTPError as error:
            if error.code not in (301, 302, 303, 307, 308):
                raise
            # GitHub redirects artifact downloads to blob storage.
            # Follow the redirect without sending our token there.
            return urllib.request.urlopen(error.headers["Location"])

    def get(self, path: str):
        with self.open(path) as response:
            return json.load(response)

    def to_run(self, data: dict) -> Run:
        if self.is_forgejo:
            return Run(data["id"], data["workflow_id"], data["prettyref"])
        return Run(data["id"], str(data["workflow_id"]), data["head_branch"])

    def get_run(self, run_id: str) -> Run:
        return self.to_run(self.get(f"/actions/runs/{run_id}"))

    def successful_runs(self, workflow: str):
        """Yield the successful runs of a workflow, newest first."""
        page = 1
        while True:
            if self.is_forgejo:
                query = urllib.parse.urlencode(
                    {"workflow_id": workflow, "status": "success", "limit": 50, "page": page}
                )
                runs = self.get(f"/actions/runs?{query}")["workflow_runs"]
            else:
                query = urllib.parse.urlencode({"status": "success", "per_page": 100, "page": page})
                runs = self.get(f"/actions/workflows/{workflow}/runs?{query}")["workflow_runs"]
            if not runs:
                return
            for run in runs:
                yield self.to_run(run)
            page += 1

    def artifacts(self, run: Run) -> list[Artifact]:
        if self.is_forgejo:
            artifacts = self.get(f"/actions/runs/{run.id}/artifacts?limit=50")
        else:
            artifacts = self.get(f"/actions/runs/{run.id}/artifacts?per_page=100")["artifacts"]
        return [Artifact(a["id"], a["name"]) for a in artifacts if not a["expired"]]

    def download(self, artifact: Artifact, directory: Path) -> Path:
        """Download an artifact into `directory` and return the single file it contains."""
        directory.mkdir(parents=True)
        archive = directory.with_suffix(".zip")
        with self.open(f"/actions/artifacts/{artifact.id}/zip") as response, archive.open("wb") as file:
            shutil.copyfileobj(response, file, 1024 * 1024)

        if not zipfile.is_zipfile(archive):
            # Artifacts uploaded with `archive: false` are served as the bare file.
            return archive.rename(directory / artifact.name)

        with zipfile.ZipFile(archive) as zip_file:
            files = [entry for entry in zip_file.infolist() if not entry.is_dir()]
            if len(files) != 1:
                sys.exit(f"Artifact '{artifact.name}' must contain exactly one file, found {len(files)}.")
            path = Path(zip_file.extract(files[0], directory))
        archive.unlink()
        return path


def find_artifact(api: Api, run: Run, pattern: re.Pattern) -> Artifact | None:
    matches = [artifact for artifact in api.artifacts(run) if pattern.fullmatch(artifact.name)]
    if len(matches) > 1:
        names = ", ".join(artifact.name for artifact in matches)
        sys.exit(f"Run {run.id} has several artifacts matching '{pattern.pattern}': {names}")
    return matches[0] if matches else None


def find_previous(api: Api, new_run: Run, pattern: re.Pattern) -> tuple[Run | None, Artifact | None]:
    """Find the newest successful earlier run of the same workflow and ref.

    If that run has no matching artifact, the chain starts after it, so there is no base.
    """
    for run in api.successful_runs(new_run.workflow):
        if run.id < new_run.id and run.ref == new_run.ref:
            return run, find_artifact(api, run, pattern)
    return None, None


def download_ina(release_url: str, directory: Path) -> Path:
    """Download the prebuilt ina binary for this machine and check it against SHA256SUMS."""
    name = f"ina-{platform.machine()}"
    with urllib.request.urlopen(f"{release_url}/{name}.sha256") as response:
        line = response.read().decode().rstrip()
        checksum, file = line.split()
        if file != name:
            sys.exit(f"Checksum filename mismatch for {name}: got {file}, expected {name}")

    path = directory / "ina"
    urllib.request.urlretrieve(f"{release_url}/{name}", path)
    with path.open("rb") as file:
        digest = hashlib.file_digest(file, "sha256").hexdigest()
    if digest != checksum:
        sys.exit(f"Checksum mismatch for {name}: got {digest}, expected {checksum}.")
    path.chmod(0o755)
    return path


def set_outputs(outputs: dict) -> None:
    with open(os.environ["GITHUB_OUTPUT"], "a") as file:
        for key, value in outputs.items():
            file.write(f"{key}={value}\n")


def main() -> None:
    api = Api(os.environ["API_URL"], os.environ["REPOSITORY"], os.environ["TOKEN"])
    pattern = re.compile(os.environ["ARTIFACT_PATTERN"])

    new_run = api.get_run(os.environ["NEW_RUN_ID"])
    new_artifact = find_artifact(api, new_run, pattern)
    if new_artifact is None:
        sys.exit(f"Run {new_run.id} has no artifact matching '{pattern.pattern}'.")

    if os.environ.get("PREVIOUS_RUN_ID"):
        previous_run = api.get_run(os.environ["PREVIOUS_RUN_ID"])
        previous_artifact = find_artifact(api, previous_run, pattern)
        if previous_artifact is None:
            sys.exit(f"Run {previous_run.id} has no artifact matching '{pattern.pattern}'.")
    else:
        previous_run, previous_artifact = find_previous(api, new_run, pattern)
        if previous_artifact is None:
            print(f"No previous artifact for {new_run.ref}; run {new_run.id} starts a new chain.")
            return

    print(f"New:      run {new_run.id}, artifact '{new_artifact.name}'")
    print(f"Previous: run {previous_run.id}, artifact '{previous_artifact.name}'")

    work = Path(tempfile.mkdtemp(dir=os.environ.get("RUNNER_TEMP")))
    new_file = api.download(new_artifact, work / "new")
    old_file = api.download(previous_artifact, work / "old")
    ina = download_ina(os.environ["INA_URL"], work)

    delta = Path("ina-delta", f"{new_file.name}.ina").resolve()
    delta.parent.mkdir(exist_ok=True)
    subprocess.run([ina, "diff", old_file, new_file, delta], check=True)
    print(f"Delta: {delta} ({delta.stat().st_size} bytes)")
    shutil.rmtree(work)

    set_outputs({"delta-path": delta, "delta-name": delta.name, "previous-run-id": previous_run.id})


if __name__ == "__main__":
    main()
