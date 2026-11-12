"""A managed local llama.cpp process; no external inference services."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import socket
import subprocess
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse


class LocalModel:
    def __init__(self, endpoint, name="local-model", seed=2026):
        parsed = urlparse(endpoint)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1"} or parsed.username or parsed.password:
            raise ValueError("Only an HTTP loopback model endpoint is permitted")
        self.endpoint = endpoint.rstrip("/")
        self.name = name
        self.seed = seed
        self.adapter = False

    def chat(self, messages, timeout, *, temperature=0, seed=None, max_tokens=600, lora=False):
        payload = {"model": self.name, "messages": messages, "temperature": temperature, "seed": self.seed if seed is None else seed,
                   "max_tokens": max_tokens, "stream": False, "response_format": {"type": "json_object"},
                   "chat_template_kwargs": {"enable_thinking": False}, "cache_prompt": True}
        # The trained adapter loads switched off; only the calls it was trained for switch it on.
        if lora and self.adapter:
            payload["lora"] = [{"id": 0, "scale": 1.0}]
        request = urllib.request.Request(self.endpoint + "/chat/completions",
            json.dumps(payload, ensure_ascii=False).encode(), {"Content-Type": "application/json"})
        # Disable environment proxy settings: local inference must stay on loopback.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=timeout) as response:
            result = json.load(response)
        message = result["choices"][0]["message"]
        return message.get("content") or "", result.get("usage", {}), result["choices"][0].get("finish_reason")


@contextmanager
def serve(model_path, runtime, output, context=24576, threads=8, gpu_layers=0, embedding=False,
          embedding_pooling='cls', parallel=1, lora=None, load_timeout=900, log_name='model-server.log'):
    model_path, runtime = Path(model_path).resolve(), Path(runtime).resolve()
    if not model_path.is_file():
        raise FileNotFoundError("Model not found. Run scripts/setup_cpu.py before inference.")
    if lora is not None and not Path(lora).is_file():
        raise FileNotFoundError("LoRA adapter not found: " + str(lora))
    binaries = list(runtime.rglob("llama-server"))
    if len(binaries) != 1:
        raise FileNotFoundError("Expected one llama-server under runtime directory; install the matching runtime from README.md")
    if gpu_layers < 0:
        raise ValueError("GPU layers must be nonnegative; use 99 for full offload")
    devices = ""
    if gpu_layers:
        probe = subprocess.run([str(binaries[0]), "--list-devices"], capture_output=True, text=True, timeout=30)
        devices = probe.stdout + probe.stderr
        if probe.returncode or not re.search(r"(?m)^\s*CUDA\d+:", devices):
            raise RuntimeError("CUDA GPU unavailable to this runtime; refusing CPU fallback. "
                               "Use the CUDA runtime in a terminal with GPU access.\n" + devices.strip())
    with model_path.open("rb") as stream:
        sha = hashlib.file_digest(stream, "sha256").hexdigest()
    manifest_path = model_path.parent / "manifest.json"
    metadata = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    if metadata.get("sha256") and metadata["sha256"] != sha:
        raise ValueError("Model differs from its recorded checksum")
    runtime_manifest = runtime / "manifest.json"
    if runtime_manifest.exists():
        metadata.update(json.loads(runtime_manifest.read_text()))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    args = [str(binaries[0]), "--model", str(model_path), "--host", "127.0.0.1", "--port", str(port),
            "--ctx-size", str(context * parallel), "--threads", str(threads), "--threads-batch", str(threads),
            "--parallel", str(parallel), "--n-gpu-layers", str(gpu_layers)]
    if embedding:
        args += ["--embedding", "--pooling", embedding_pooling, "--batch-size", "512", "--ubatch-size", "512"]
    else:
        args += ["--cache-type-k", "q8_0", "--cache-type-v", "q8_0", "--flash-attn", "on",
                 "--jinja", "--chat-template-kwargs", '{"enable_thinking":false}']
    if lora is not None:
        args += ["--lora", str(Path(lora).resolve()), "--lora-init-without-apply"]
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    with (output / log_name).open("w") as log:
        process = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT)
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            # Reading 10 GB of weights from a cold disk can take minutes; the 6-hour budget easily allows it.
            deadline = time.monotonic() + load_timeout
            while True:
                if process.poll() is not None:
                    raise RuntimeError(f"Model server exited; see {log_name}")
                try:
                    with opener.open(f"http://127.0.0.1:{port}/health", timeout=1) as response:
                        if response.status == 200:
                            break
                except (OSError, urllib.error.URLError):
                    time.sleep(0.5)
                if time.monotonic() > deadline:
                    raise TimeoutError(f"Model server failed to load within {load_timeout} seconds")
            metadata.update({"actual_model_sha256": sha, "server_command": args,
                             "context": context, "threads": threads, "temperature": 0,
                             "seed": 2026, "thinking": False, "gpu_layers": gpu_layers,
                             "available_cuda_devices": devices.strip() if gpu_layers else None, "lora": str(lora) if lora else None})
            model = LocalModel(f"http://127.0.0.1:{port}/v1", metadata.get("model", model_path.name))
            model.adapter = lora is not None
            yield model, metadata
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
