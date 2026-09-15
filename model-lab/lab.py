"""Local, stimulus-driven FlyBrain experiment. No language model or wallet access."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import resource
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
STIMULI = {
    "rest": ([], None),
    "loom_left": (["LC4", "LPLC2"], "L"),
    "loom_right": (["LC4", "LPLC2"], "R"),
    "chase_left": (["LC10a"], "L"),
    "chase_right": (["LC10a"], "R"),
}


def peak_memory_mib():
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(rss / (1024 ** 2 if platform.system() == "Darwin" else 1024), 1)


class Experiment:
    def __init__(self, data, threads):
        import numba
        import numpy as np
        from flybrain import FlyBrain

        self.np = np
        self.threads = threads
        numba.set_num_threads(threads)
        start = time.perf_counter()
        self.brain = FlyBrain(data=data, device="cpu", seed=64)
        self.load_seconds = time.perf_counter() - start
        self.readout = {
            f"{kind}_{side}": self.brain.cells([kind], side=side)
            for kind in ["DNp01", "DNa02", "DNg100", "MDN"] for side in "LR"
        }
        self.readout = {k: v for k, v in self.readout.items() if len(v)}
        if not all(k in self.readout for k in ["DNp01_L", "DNp01_R", "DNa02_L", "DNa02_R"]):
            raise RuntimeError("Required bilateral readout neurons are missing.")
        if self.brain.n != 166700 or len(self.brain.weights) != 25582938:
            raise RuntimeError("Unexpected connectome size; verify the source data.")
        # Compile numba once, separately from steady-state timing.
        start = time.perf_counter()
        self.brain.step()
        self.jit_seconds = time.perf_counter() - start
        self.lock = threading.Lock()
        self.latest = None

    def info(self):
        return {"neurons": self.brain.n, "connections": len(self.brain.weights),
                "device": "cpu", "threads": self.threads, "dt": self.brain.dt,
                "tonic": self.brain.tonic, "gain": self.brain.gain,
                "load_seconds": round(self.load_seconds, 3),
                "jit_seconds": round(self.jit_seconds, 3), "peak_rss_mib": peak_memory_mib(),
                "readout_neurons": {k: len(v) for k, v in self.readout.items()},
                "busy": self.lock.locked(), "latest": self.latest}

    def episode(self, stimulus, strength, seed):
        import numba

        numba.set_num_threads(self.threads)
        brain, np = self.brain, self.np
        brain.reset(seed)
        types, side = STIMULI[stimulus]
        target = brain.cells(types, side=side) if types else np.array([], dtype=int)
        steps, warm = round(2 / brain.dt), round(.5 / brain.dt)
        counts = {k: 0 for k in self.readout}
        hit = np.zeros(brain.n, bool)
        eye = np.full(len(brain.visual), .45, np.float32)
        timings, trace, total_spikes = [], [], 0
        start = time.perf_counter()
        for tick in range(steps):
            begin = time.perf_counter()
            fired = brain.step(eye, inject=[(target, strength)] if len(target) else [])
            timings.append((time.perf_counter() - begin) * 1000)
            total_spikes += len(fired)
            if tick >= warm:
                hit[:] = False
                hit[fired] = True
                for key, idx in self.readout.items():
                    counts[key] += int(hit[idx].sum())
                trace.append({"t": round((tick + 1) * brain.dt, 3),
                              "escape_left": int(hit[self.readout["DNp01_L"]].sum()),
                              "escape_right": int(hit[self.readout["DNp01_R"]].sum())})
        elapsed = time.perf_counter() - start
        measured = (steps - warm) * brain.dt
        return {"stimulus": stimulus, "strength": strength, "seed": seed,
                "input_neurons": len(target), "simulated_seconds": steps * brain.dt,
                "measured_seconds": measured, "wall_seconds": round(elapsed, 3),
                "mean_step_ms": round(float(np.mean(timings)), 3),
                "p95_step_ms": round(float(np.percentile(timings, 95)), 3),
                "simulation_speed": round(steps * brain.dt / elapsed, 3),
                "total_spikes": total_spikes,
                "rates_hz": {k: round(n / len(self.readout[k]) / measured, 3) for k, n in counts.items()},
                "escape_trace": trace}

    def compare(self, stimulus, strength, seed):
        baseline = self.episode("rest", 0, seed)
        observed = self.episode(stimulus, strength, seed)
        result = {"baseline": baseline, "stimulated": observed,
                  "delta_hz": {k: round(v - baseline["rates_hz"][k], 3) for k, v in observed["rates_hz"].items()},
                  "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                  "peak_rss_mib": peak_memory_mib()}
        self.latest = result
        return result


def serve(experiment, host, port):
    class Handler(BaseHTTPRequestHandler):
        def send_json(self, value, status=200):
            raw = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.path == "/api/status":
                return self.send_json(experiment.info())
            if self.path != "/":
                return self.send_json({"error": "Not found"}, 404)
            raw = Path(__file__).with_name("index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            if self.path != "/api/run":
                return self.send_json({"error": "Not found"}, 404)
            origin = self.headers.get("Origin")
            if origin and urlparse(origin).netloc != self.headers.get("Host"):
                return self.send_json({"error": "Cross-origin requests are disabled"}, 403)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 2048:
                    raise ValueError("Invalid request size")
                args = json.loads(self.rfile.read(length))
                stimulus, strength, seed = args["stimulus"], float(args.get("strength", .8)), int(args.get("seed", 64))
                if stimulus not in STIMULI or not 0 <= strength <= 1 or not 0 <= seed <= 10000:
                    raise ValueError("Invalid stimulus, strength or seed")
            except (ValueError, KeyError, TypeError):
                return self.send_json({"error": "Invalid experiment parameters"}, 400)
            if not experiment.lock.acquire(blocking=False):
                return self.send_json({"error": "An experiment is already running"}, 409)
            try:
                result = experiment.compare(stimulus, strength, seed)
            except Exception as error:
                print(f"Experiment failed: {error}", flush=True)
                return self.send_json({"error": "Experiment failed; see the local service log"}, 500)
            finally:
                experiment.lock.release()
            return self.send_json(result)

    print(f"FlyBrain local lab: http://{host}:{port}/", flush=True)
    ThreadingHTTPServer((host, port), Handler).serve_forever()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["benchmark", "serve"])
    parser.add_argument("--data", type=Path, default=Path(os.environ.get("FLY_DATA", ROOT / ".sites-runtime/flyai/data")))
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5187)
    parser.add_argument("--output", type=Path, default=ROOT / ".sites-runtime/flyai/benchmark.json")
    args = parser.parse_args()
    if not 1 <= args.threads <= (os.cpu_count() or 1):
        parser.error("Thread count must fit this machine")
    os.environ["NUMBA_NUM_THREADS"] = str(args.threads)
    os.environ["OPENBLAS_NUM_THREADS"] = "1"
    os.environ["OMP_NUM_THREADS"] = str(args.threads)
    experiment = Experiment(args.data, args.threads)
    print(json.dumps({"model": experiment.info()}), flush=True)
    if args.command == "serve":
        return serve(experiment, args.host, args.port)
    results = []
    for seed in [64, 65, 66]:
        for stimulus in STIMULI:
            row = experiment.episode(stimulus, .8 if stimulus != "rest" else 0, seed)
            results.append(row)
            print(json.dumps({k: v for k, v in row.items() if k != "escape_trace"}), flush=True)
    report = {"model": experiment.info(), "platform": platform.system(), "architecture": platform.machine(),
              "python": platform.python_version(), "episodes": results, "peak_rss_mib": peak_memory_mib()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Report saved: {args.output}", flush=True)


if __name__ == "__main__":
    main()
