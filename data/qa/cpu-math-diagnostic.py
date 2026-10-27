"""Small independent CPU arithmetic checks; no model assets or network are used."""
import argparse
import json
from pathlib import Path
import time

import numpy as np
import torch
import torch.nn.functional as F
from torch.nn.attention import SDPBackend, sdpa_kernel


def compare(output, expected):
    output = output.detach().cpu().numpy().astype(np.float64)
    finite = bool(np.isfinite(output).all())
    difference = float(np.max(np.abs(output - expected))) if finite else None
    relative = difference / max(1, float(np.max(np.abs(expected)))) if finite else None
    return {'finite': finite, 'peak': float(np.max(np.abs(output))) if finite else None,
            'max_absolute_error': difference, 'relative_to_reference_peak': relative,
            'pass': finite and relative < 1e-4}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=Path(__file__).with_suffix('.json'))
    args = parser.parse_args()
    torch.set_num_interop_threads(1)
    rng = np.random.default_rng(897301)
    a = rng.normal(size=(128, 64)).astype(np.float32)
    b = rng.normal(size=(64, 128)).astype(np.float32)
    q, k, v = [rng.normal(size=(1, 2, 128, 64)).astype(np.float32) for _ in range(3)]
    logits = (q.astype(np.float64) @ k.astype(np.float64).swapaxes(-1, -2)) / 8
    weights = np.exp(logits - logits.max(axis=-1, keepdims=True))
    weights /= weights.sum(axis=-1, keepdims=True)
    attention = weights @ v.astype(np.float64)
    cq = rng.normal(size=(1, 8, 256)).astype(np.float32)
    cw = rng.normal(size=(16, 8, 3)).astype(np.float32)
    windows = np.lib.stride_tricks.sliding_window_view(np.pad(cq.astype(np.float64), ((0, 0), (0, 0), (1, 1))), 3, axis=-1)
    convolution = np.einsum('bitk,oik->bot', windows, cw.astype(np.float64))
    qt, kt, vt = map(torch.from_numpy, [q, k, v])
    records = []
    started = time.perf_counter()
    for threads in [1, 2, 4, 6]:
        torch.set_num_threads(threads)
        for mkldnn in [True, False]:
            torch.backends.mkldnn.enabled = mkldnn
            results = {}
            results['matmul'] = compare(torch.from_numpy(a) @ torch.from_numpy(b), a.astype(np.float64) @ b.astype(np.float64))
            results['bmm'] = compare(torch.bmm(qt[0], kt[0].transpose(-1, -2)), q[0].astype(np.float64) @ k[0].astype(np.float64).swapaxes(-1, -2))
            results['conv1d'] = compare(F.conv1d(torch.from_numpy(cq), torch.from_numpy(cw), padding=1), convolution)
            results['manual_attention'] = compare(torch.softmax(qt @ kt.transpose(-1, -2) / 8, dim=-1) @ vt, attention)
            for repeat in range(3):
                results[f'sdpa_default_{repeat}'] = compare(F.scaled_dot_product_attention(qt, kt, vt), attention)
            with sdpa_kernel(SDPBackend.MATH):
                results['sdpa_math'] = compare(F.scaled_dot_product_attention(qt, kt, vt), attention)
            records.append({'threads': threads, 'mkldnn': mkldnn, 'results': results})
    report = {'torch_version': torch.__version__, 'numpy_version': np.__version__,
              'cpu_capability': torch.backends.cpu.get_cpu_capability(), 'build': torch.__config__.show(),
              'elapsed_seconds': round(time.perf_counter() - started, 3),
              'method': 'fixed NumPy inputs; float64 NumPy manual reference; relative error tolerance1e-4',
              'all_pass': all(item['pass'] for record in records for item in record['results'].values()), 'records': records}
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps({'torch_version': report['torch_version'], 'all_pass': report['all_pass'],
                      'failures': [{'threads': record['threads'], 'mkldnn': record['mkldnn'],
                                    'operations': [name for name, result in record['results'].items() if not result['pass']]}
                                   for record in records if any(not item['pass'] for item in record['results'].values())],
                      'output': str(args.output), 'elapsed_seconds': report['elapsed_seconds']}, allow_nan=False))


if __name__ == '__main__':
    main()
