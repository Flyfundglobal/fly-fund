# Local CPU validation — 2026-09-15

Evidence: [raw result JSON](local-cpu-2026-09-15.json). This is a macOS ARM64 measurement, not a Vultr benchmark. Core package: `flybrain==0.1.0`; Python 3.12.14. Both official `brain-v1` files passed their SHA-256 checks. The full 166,700-neuron, 25,582,938-connection model was used.

| CPU thread limit | Mean step | Wall time per 2 simulated seconds | Peak RSS* |
| --- | --- | --- | --- |
| 4 | 2.557 ms | 0.252–0.266 s | 654.6 MiB |
| 1 | 4.134 ms | 0.394–0.435 s | 649.4 MiB |

*Memory is reported in binary MiB (`peak_rss_mib`). Peak RSS includes initialization. Four-thread initial loading took 0.207 s and initial JIT compilation 0.548 s; steady-state episode timings exclude both. The lab is a simplified 20 ms-step model, not a 2 ms or high-detail physics simulation.

Mean firing rate (Hz per neuron), three matched noise seeds:

| Stimulus, strength 0.8 | Escape left | Escape right | Steer left | Steer right |
| --- | --- | --- | --- | --- |
| Baseline | 0.00 | 0.00 | 0.22 | 0.22 |
| Loom left | 46.89 | 0.44 | 0.67 | 0.22 |
| Loom right | 0.67 | 44.00 | 0.44 | 0.00 |
| Chase left | 0.00 | 0.00 | 4.67 | 0.22 |
| Chase right | 0.00 | 0.00 | 0.00 | 2.89 |

API checks passed: zero-input matched control, identical readout rates when repeating the same seed, rejection of out-of-range strength. For seed 64, left looming at strength 0.3 produced 17.333 Hz in left DNp01; at 0.8 it produced 47.333 Hz. This is one encoding/parameter setting, not a universal proportional rule.

The upstream offline SSH Fighter example completed 300 fake-opponent frames in 1.5 s. No live game account, external opponent, wallet, or trading action was used. The short demo only confirms that the original application executes.

Browser verification: the local page loaded the real model metadata; selecting right looming and running the experiment returned 42.67 Hz on right DNp01 versus 1.33 Hz on left DNp01, with raw JSON available. This validates the stimulus-to-simulation-to-readout path. It does not validate text understanding, persistent memory, autonomous trading, or Linux/VPS performance.

For the first single-fly, on-demand service, 8 GB of RAM appears ample based on this run. Recheck timing and total website/worker memory on the actual VPS before treating 4 vCPU / 8 GB as a production capacity commitment.
