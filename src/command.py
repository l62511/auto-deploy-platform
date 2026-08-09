from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


class CommandError(RuntimeError):
    def __init__(self, command: Sequence[str], returncode: int, output: str):
        self.command = list(command)
        self.returncode = returncode
        self.output = output
        super().__init__(
            f"Command failed with exit code {returncode}: {' '.join(command)}\n{output}"
        )


@dataclass(frozen=True)
class CommandResult:
    command: list[str]
    returncode: int
    output: str


def run_command(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    check: bool = True,
    timeout: int | None = None,
    logger: logging.Logger | None = None,
) -> CommandResult:
    command_list = [str(item) for item in command]
    if logger:
        logger.info("Running command: %s", " ".join(command_list))

    process_env = os.environ.copy()
    if env:
        process_env.update({key: str(value) for key, value in env.items()})

    completed = subprocess.run(
        command_list,
        cwd=str(cwd) if cwd else None,
        env=process_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
        check=False,
    )
    output = completed.stdout.strip()
    if output and logger:
        logger.debug("Command output:\n%s", output)
    if check and completed.returncode != 0:
        raise CommandError(command_list, completed.returncode, output)
    return CommandResult(command_list, completed.returncode, output)

