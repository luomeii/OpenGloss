"""R2 corpus coverage test: lemma cascade over real engineering English.
Baseline = lemma_map + rule suffix stripping (ECDICT exchange join comes later).
Pass bar for the BASELINE layer is intentionally lower than the full cascade bar (85%).
"""
import math
import pytest
from pae_core.lemmatize import load_lemma_map, candidates

CORPUS = [
    "CUDA out of memory. Tried to allocate 1.20 GiB. GPU 0 has a total capacity of 15.90 GiB of which 8.12 GiB is free. Including non-PyTorch memory, this process is using 7.78 GiB at peak. The torch.cuda.memory_allocated and torch.cuda.max_memory_allocated calls can help identify the cause.",
    "Idempotency is a fundamental property for building reliable distributed systems. When a client retries a request after a timeout, the server must be able to deduplicate the operation without violating invariants. This is typically achieved by attaching a unique key to each request and storing the outcome before responding.",
    "We propose a novel approach to retrieval-augmented generation that mitigates hallucination in large language models. Our method introduces a lightweight reranker trained on contrastive objectives, achieving substantial improvements on open-domain question answering benchmarks while reducing inference latency by forty percent.",
    "This library provides utilities for tokenizing text, encoding sequences, and batching examples efficiently. The API is designed to be framework-agnostic: you can plug it into any training pipeline without modifying your data loaders. Pretrained checkpoints are available for eight languages.",
    "Traceback (most recent call last): File 'train.py', line 42, in main. RuntimeError: expected scalar type Float but found Double. The mismatch occurred during the backward pass when gradients were accumulated across mixed precision boundaries.",
    "The contention on the scheduler lock intensified as worker threads multiplied, leading to degraded throughput. We considered lock-free alternatives such as compare-and-swap loops, but the complexity outweighed the benefits for our workload. Profiling revealed that the bottleneck was actually memory fragmentation rather than synchronization overhead.",
]

STOP = set('a an the of in on at to for and or but is are was were be been being it its this that these those we you they he she i as by with from has have had do does did not no can could will would should may might must there their our your his her them us me my mine'.split())

def content_words(text):
    return [w.strip(".,;:!?()[]{}\"'`").lower() for w in text.split()]

def test_baseline_coverage():
    from pathlib import Path
    m = load_lemma_map(str(Path(__file__).resolve().parents[2] / "resources" / "ECDICT" / "lemma.en.txt"))
    # 词典命中集合 = lemma_map 的 key 和 value 的并集
    known = set(m.keys()) | set(m.values())
    total, hit = 0, 0
    misses = []
    for text in CORPUS:
        for w in content_words(text):
            if not w or not w.isalpha() or w in STOP or len(w) < 3:
                continue
            total += 1
            cands = candidates(w, m)
            if any(c in known for c in cands):
                hit += 1
            else:
                misses.append(w)
    cov = hit / total
    print(f'baseline coverage: {cov:.3f} ({hit}/{total}), misses: {sorted(set(misses))[:20]}')
    # 基线层（无 exchange）门槛 60%：加 exchange 反查后全级联门槛提到 85%
    assert cov >= 0.60, f'baseline coverage {cov:.3f} < 0.60'