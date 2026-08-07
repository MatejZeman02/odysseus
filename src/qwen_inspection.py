"""Fail-closed readiness contract for G2A sandboxed project inspection."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass


PINNED_QWEN_SANDBOX_IMAGE = (
    "ghcr.io/qwenlm/qwen-code@"
    "sha256:216bd08d6ba6819245b78bffd9ef9ecb2442af6ec0e2ed26f9a8727386795946"
)
_DIGEST_IMAGE = re.compile(r"^[a-z0-9./_-]+@sha256:[a-f0-9]{64}$")


def _enabled() -> bool:
    return os.getenv("ODYSSEUS_QWEN_PROJECT_INSPECT", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _configured_image() -> str:
    return os.getenv("ODYSSEUS_QWEN_SANDBOX_IMAGE", "").strip()


def _podman_image_exists(podman: str, image: str) -> bool:
    try:
        result = subprocess.run(
            [podman, "image", "exists", image],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


@dataclass(frozen=True)
class InspectionReadiness:
    enabled: bool
    podman_ready: bool
    image_configured: bool
    image_ready: bool
    sandbox_probe_passed: bool
    ready: bool
    failure_code: str | None = None

    def public_payload(self) -> dict:
        return {
            "enabled": self.enabled,
            "podman": self.podman_ready,
            "pinned_image": self.image_configured,
            "image_ready": self.image_ready,
            "sandbox_probe": self.sandbox_probe_passed,
            "ready": self.ready,
        }


def inspection_readiness() -> InspectionReadiness:
    """Report only safe booleans; never start or pull a container.

    Qwen Code 0.21.3's Podman launcher was rejected by the G2A qualification:
    it containerizes the whole model runtime, forwards ModelBridge credentials,
    mounts the workspace writable, and may implicitly pull an image. Therefore
    inspection remains unavailable even when Podman and the reviewed image are
    installed. A future command-broker design must earn a new probe result.
    """
    enabled = _enabled()
    podman = shutil.which("podman")
    image = _configured_image()
    image_configured = bool(
        image
        and _DIGEST_IMAGE.fullmatch(image)
        and image == PINNED_QWEN_SANDBOX_IMAGE
    )
    image_ready = bool(
        podman and image_configured and _podman_image_exists(podman, image)
    )
    return InspectionReadiness(
        enabled=enabled,
        podman_ready=bool(podman),
        image_configured=image_configured,
        image_ready=image_ready,
        sandbox_probe_passed=False,
        ready=False,
        failure_code="sandbox_unavailable" if enabled else None,
    )


def effective_capability(requested: str) -> str:
    if requested == "project_inspect" and inspection_readiness().ready:
        return "project_inspect"
    return "project_read"
