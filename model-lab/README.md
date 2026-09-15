# FLY FUND local neural experiment

This lab runs the independent [alextitonis/fly.ai](https://github.com/alextitonis/fly.ai) project's `flybrain==0.1.0` CPU engine. It is separate from the public website and does not connect to a wallet, an LLM, or a trading executor.

## Setup

Use Python 3.12. From the repository root:

```sh
python3.12 -m venv .sites-runtime/flyai/venv
.sites-runtime/flyai/venv/bin/python -m pip install -r model-lab/requirements.txt
.sites-runtime/flyai/venv/bin/python model-lab/download.py
.sites-runtime/flyai/venv/bin/python model-lab/lab.py serve --threads 4
```

Open http://127.0.0.1:5187/. The service loads one brain, then only simulates when an experiment is requested. The first startup includes Numba compilation. No always-running external LLM is required for this lab.

`download.py` fetches the upstream `brain-v1` GitHub release using resumable byte ranges, verifies each range's offset and length, then checks the final SHA-256 against the official package's constants. It never substitutes a smaller or fabricated model. Data stays under `.sites-runtime/flyai/data`; set `FLY_DATA` to use another location. The upstream alternative is `python -m flybrain download`.

## Experiment

The model has 166,700 neurons and 25,582,938 connections. The simulation uses the package defaults: CPU, `dt=0.020`, `tonic=0.14`, `gain=3.0`, and one fly. Both comparison episodes reset to the same noise seed and receive the same constant photoreceptor drive (0.45). An episode simulates 2 seconds; rates exclude its first 0.5 seconds.

- Baseline: no additional injection.
- Left/right looming: inject LC4 + LPLC2 on the selected side.
- Left/right moving target: inject LC10a on the selected side.
- Readout: per-neuron firing rates for DNp01 (escape), DNa02 (steering), DNg100 (forward) and MDN (backward), where side annotations exist.

The UI reports **neural rates**, not guaranteed physical movement. Stimulus strength is an interface parameter, not a token amount. This is a simplified connectome simulation, not proof of cognition, financial understanding, or investment skill. The biological wiring stays fixed; mapping text to stimuli and neural output to Fund actions is additional application code that is not implemented here.

Reproduce all five stimuli across three noise seeds:

```sh
.sites-runtime/flyai/venv/bin/python model-lab/lab.py benchmark --threads 4
```

The JSON report defaults to `.sites-runtime/flyai/benchmark.json`. Timings exclude initial model loading and JIT compilation, which are recorded separately. Peak RSS includes initialization. macOS hardware with four software threads is not equivalent to a four-vCPU VPS benchmark.

## VPS migration

Copy this lab and its requirements, recreate the Linux virtual environment, and copy the two verified data files or rerun the downloader. Do not copy the macOS virtual environment. Keep the model API on loopback or a private container network and let the website backend call it. Never expose this unauthenticated diagnostic API to the public Internet.

The website and model should be separate processes. A future production model worker should accept queued feedings, save state and decision evidence, and return results asynchronously. This lab deliberately resets the brain for comparisons, so it is not yet a persistent personal-fly memory service. The site's current Wrangler preview also needs a production hosting decision before moving both services to a VPS.

No VPS resources are provisioned by these scripts. No wallet keys are needed. Retain the upstream package's MIT license and attribution when distributing the runtime.
