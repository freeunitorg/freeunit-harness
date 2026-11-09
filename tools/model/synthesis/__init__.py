"""Modelled checks of proposed high-performance C optimizations.

Provides executable models for:
- k1_doorbell_proof: bounded doorbell notification and baton-pass recovery.
- lockfree_shm_synth: per-engine SHM slab allocation that avoids a global mmaps->mutex.
"""

from tools.model.synthesis.k1_doorbell_proof import K1DoorbellProof, ProofResult
from tools.model.synthesis.lockfree_shm_synth import LockFreeShmPool, PerEngineSlab

__all__ = [
    "K1DoorbellProof",
    "ProofResult",
    "LockFreeShmPool",
    "PerEngineSlab",
]
