"""Delete the accidental Railway project via the GraphQL API."""

from __future__ import annotations

import json
import subprocess

RAILWAY = r"C:\Users\Administrator\AppData\Local\Programs\nodejs\railway.cmd"
PROJECT = "0f79aa7b-0604-49dd-a7d7-2e1de48f3732"

MUTATION = """
mutation projectDelete($projectId: String!) {
  projectDelete(projectId: $projectId)
}
"""


def main() -> None:
    payload = json.dumps({"projectId": PROJECT})
    result = subprocess.run(
        [RAILWAY, "api", MUTATION, "--variables", payload],
        cwd=PROJECT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    print((result.stdout or result.stderr)[:600])


if __name__ == "__main__":
    main()
