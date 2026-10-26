"""Entry point `text2audio.covers: sd-webui` — covers from a local AUTOMATIC1111 WebUI.

    [cover.sd] dir → own WebUI process, API only, 127.0.0.1:<free port>, on the first
    GPU with enough free VRAM → wait for /sdapi/v1/sd-models → txt2img per book → stop

The WebUI checkout is shared (the owner's bot runs its own instance from it), so this
instance never writes there: `--data-dir` is a private directory under text2audio's
data dir, with a copy of the WebUI's `config.json` (its default checkpoint, unless
`[cover.sd] checkpoint` names another) and its own hash cache; checkpoints are read
through `--models-dir`. Extensions are disabled and the HF offline flags are set, so
start-up does not reach the network (§6.10 local-service exception, ADR-0009).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import signal
import socket
import struct
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import platformdirs

from text2audio.core import resources as res
from text2audio.core.config import CoverConfig, CoverSdConfig
from text2audio.core.contracts import CoverImage, CoverRequest
from text2audio.core.errors import CoverError
from text2audio.core.offline import OFFLINE_VARS

VRAM_MB = {"sdxl": 10000, "sd1": 5000}
SIZE = {"sdxl": 1024, "sd1": 512}  # SD 1.x draws at 512 and upscales ×2 (hires fix)
HOST = "127.0.0.1"


def private_dir() -> Path:
    return Path(platformdirs.user_data_dir("text2audio")) / "sd-webui"


def _python(sd: CoverSdConfig) -> Path:
    assert sd.dir is not None
    return sd.python or sd.dir / "venv" / "bin" / "python"


def checkpoint_title(sd: CoverSdConfig) -> str | None:
    """`[cover.sd] checkpoint`, else the WebUI's configured default."""
    if sd.checkpoint:
        return sd.checkpoint
    assert sd.dir is not None
    try:
        config = json.loads((sd.dir / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    title = config.get("sd_model_checkpoint")
    return str(title) if title else None


def checkpoint_file(sd: CoverSdConfig, title: str) -> Path:
    """'sub/name.safetensors [abc123]' → the file under models/Stable-diffusion."""
    assert sd.dir is not None
    return sd.dir / "models" / "Stable-diffusion" / title.split(" [")[0]


def family(path: Path) -> str:
    """'sdxl' or 'sd1' from a safetensors header (SDXL has a second text encoder)."""
    try:
        with path.open("rb") as f:
            (n,) = struct.unpack("<Q", f.read(8))
            if n > 100_000_000:
                return "sdxl"
            header = json.loads(f.read(n))
    except (OSError, ValueError, struct.error):
        return "sdxl"  # unknown: plan for the larger model
    return "sdxl" if any(k.startswith("conditioner.embedders.1") for k in header) else "sd1"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, 0))
        return int(s.getsockname()[1])


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


class SdWebUI:
    name = "sd-webui"

    def unavailable(self, settings: object) -> str | None:
        sd = settings.sd if isinstance(settings, CoverConfig) else settings
        assert isinstance(sd, CoverSdConfig)
        if sd.dir is None:
            return "no Stable Diffusion WebUI configured ([cover.sd] dir)"
        if not (sd.dir / "launch.py").is_file():
            return f"{sd.dir}: not a Stable Diffusion WebUI checkout (no launch.py)"
        if not _python(sd).is_file():
            return f"{_python(sd)}: WebUI interpreter not found ([cover.sd] python)"
        title = checkpoint_title(sd)
        if title is None:
            return "no checkpoint: set [cover.sd] checkpoint"
        if not checkpoint_file(sd, title).is_file():
            return f"checkpoint {title!r} not found in {sd.dir / 'models' / 'Stable-diffusion'}"
        need = VRAM_MB[family(checkpoint_file(sd, title))]
        if res.first_gpu_with(res.probe(), need) is None:
            return f"no GPU with {need} MiB free for the cover generator"
        return None

    def generate(self, requests: list[CoverRequest], settings: object) -> list[CoverImage]:
        sd = settings.sd if isinstance(settings, CoverConfig) else settings
        assert isinstance(sd, CoverSdConfig) and sd.dir is not None
        title = checkpoint_title(sd)
        if title is None:
            raise CoverError("no checkpoint configured")
        path = checkpoint_file(sd, title)
        kind = family(path)
        gpu = res.first_gpu_with(res.probe(), VRAM_MB[kind])
        if gpu is None:
            raise CoverError(f"no GPU with {VRAM_MB[kind]} MiB free")
        with Session(sd, title, gpu.index) as session:
            info = session.checkpoint(path)
            size = sd.size or SIZE[kind]
            out = []
            for req in requests:
                png = session.txt2img(req, size, sd.steps, hires=sd.size is None and kind == "sd1")
                final = size * 2 if sd.size is None and kind == "sd1" else size
                out.append(
                    CoverImage(
                        key=req.key,
                        png=png,
                        info={**info, "steps": sd.steps, "size": final, "gpu": gpu.index},
                    )
                )
            return out


class Session:
    """One private WebUI process; stopped (with its whole process group) on exit."""

    def __init__(self, sd: CoverSdConfig, title: str, gpu: int) -> None:
        assert sd.dir is not None
        self.sd = sd
        self.title = title
        self.gpu = gpu
        self.port = _free_port()
        self.base = f"http://{HOST}:{self.port}"
        self.proc: subprocess.Popen[bytes] | None = None

    def __enter__(self) -> Session:
        assert self.sd.dir is not None
        data = private_dir()
        data.mkdir(parents=True, exist_ok=True)
        try:
            config = json.loads((self.sd.dir / "config.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            config = {}
        config["sd_model_checkpoint"] = self.title
        (data / "config.json").write_text(json.dumps(config, indent=1), encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k not in ("COMMANDLINE_ARGS", "VIRTUAL_ENV")}
        env.update(dict.fromkeys(OFFLINE_VARS, "1"))
        env.update(
            CUDA_DEVICE_ORDER="PCI_BUS_ID",
            CUDA_VISIBLE_DEVICES=str(self.gpu),
            SD_WEBUI_CACHE_DIR=str(data / "cache"),
            SD_WEBUI_CACHE_FILE=str(data / "cache.json"),
        )
        cmd = [
            str(_python(self.sd)),
            "-u",
            str(self.sd.dir / "launch.py"),
            "--nowebui",
            "--api",
            "--port",
            str(self.port),
            "--skip-prepare-environment",
            "--skip-install",
            "--skip-version-check",
            "--skip-torch-cuda-test",
            "--data-dir",
            str(data),
            "--models-dir",
            str(self.sd.dir / "models"),
            "--no-download-sd-model",
            "--disable-all-extensions",
            "--no-prompt-history",
        ]
        log = (data / "webui.log").open("wb")
        self.proc = subprocess.Popen(
            cmd,
            cwd=data,  # relative paths in the copied config land in the private dir
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # its own process group: stopped as a whole
        )
        log.close()
        try:
            self._wait()
        except BaseException:
            self.__exit__()
            raise
        return self

    def __exit__(self, *exc: object) -> None:
        if self.proc is None or self.proc.poll() is not None:
            return
        os.killpg(self.proc.pid, signal.SIGTERM)
        try:
            self.proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            os.killpg(self.proc.pid, signal.SIGKILL)
            self.proc.wait()

    def _wait(self) -> None:
        deadline = time.monotonic() + self.sd.timeout_s
        while True:
            assert self.proc is not None
            if self.proc.poll() is not None:
                raise CoverError(
                    f"the WebUI exited with code {self.proc.returncode}; "
                    f"see {private_dir() / 'webui.log'}"
                )
            try:
                self._get("/sdapi/v1/sd-models")
                return
            except (OSError, CoverError):
                pass
            if time.monotonic() > deadline:
                raise CoverError(f"the WebUI did not start in {self.sd.timeout_s:.0f} s")
            time.sleep(1)

    def _request(self, path: str, body: dict[str, Any] | None = None, timeout: float = 30) -> Any:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(
            self.base + path, data=data, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            raise CoverError(f"{path}: HTTP {exc.code} {exc.read()[:300]!r}") from exc

    def _get(self, path: str) -> Any:
        return self._request(path)

    def checkpoint(self, path: Path) -> dict[str, Any]:
        """The loaded checkpoint's name and SHA-256 (hashed here if the WebUI has not)."""
        loaded = str(self._get("/sdapi/v1/options").get("sd_model_checkpoint") or self.title)
        models = self._get("/sdapi/v1/sd-models")
        entry = next((m for m in models if m.get("title") == loaded), {})
        sha = entry.get("sha256") or _sha256(path)
        name = entry.get("model_name") or loaded.split(" [")[0]
        return {"checkpoint": name, "checkpoint_sha256": sha}

    def txt2img(self, req: CoverRequest, size: int, steps: int, hires: bool) -> bytes:
        body: dict[str, Any] = {
            "prompt": req.prompt,
            "negative_prompt": req.negative_prompt,
            "seed": req.seed,
            "steps": steps,
            "width": size,
            "height": size,
            "cfg_scale": 7,
            "sampler_name": "DPM++ 2M",
            "scheduler": "Karras",
            "send_images": True,
            "save_images": False,
        }
        if hires:
            body |= {
                "enable_hr": True,
                "hr_scale": 2,
                "hr_upscaler": "Latent",
                "denoising_strength": 0.55,
                "hr_second_pass_steps": max(1, steps // 2),
            }
        reply = self._request("/sdapi/v1/txt2img", body, timeout=600)
        images = reply.get("images") or []
        if not images:
            raise CoverError(f"txt2img returned no image for {req.key}")
        return base64.b64decode(images[0])
