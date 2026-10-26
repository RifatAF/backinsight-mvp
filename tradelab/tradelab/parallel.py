"""Process pool for independent jobs (paths, folds, configs).

Default worker count is os.cpu_count() (12 on a Xeon E5-2620 v3, 6 cores / 12 threads);
override with TRADELAB_N_JOBS. BLAS threads are pinned to 1 per worker so 12 workers
do not oversubscribe the CPU. Results do not depend on the worker count: each job gets
its own seed from SeedSequence(seed).spawn(n), fixed by job index.
"""
import os

import numpy as np
from joblib import Parallel, delayed, parallel_config


def n_jobs():
    return int(os.environ.get("TRADELAB_N_JOBS", os.cpu_count() or 1))


def job_seeds(seed, n):
    return [int(s.generate_state(1)[0]) for s in np.random.SeedSequence(seed).spawn(n)]


def pmap(fn, items, jobs=None, verbose=0):
    jobs = n_jobs() if jobs is None else jobs
    if jobs == 1:
        return [fn(x) for x in items]
    with parallel_config(backend="loky", inner_max_num_threads=1):
        return Parallel(n_jobs=jobs, verbose=verbose)(delayed(fn)(x) for x in items)
