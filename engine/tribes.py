"""TRIBES: a parameter-free adaptive particle swarm for strategy parameters.

Based on Cooren, Clerc & Siarry, *TRIBES: A parameter-free particle swarm optimization
algorithm* (2006).  The swarm starts with one particle, splits into tribes, promotes particles
from tribes that improve and injects particles into stagnant tribes.  Displacement coefficients
are selected from the tribe's status instead of being hand-tuned by the caller.

The implementation is deliberately small and deterministic.  Parameters may be continuous or
discrete; discrete grids are decoded to their nearest allowed value, so the optimizer remains
compatible with the existing parameter-grid API.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass(frozen=True)
class ParameterSpace:
    """Numeric search space derived from a parameter grid."""

    names: tuple[str, ...]
    lower: np.ndarray
    upper: np.ndarray
    integer: np.ndarray
    allowed: tuple[tuple[float, ...], ...]

    @classmethod
    def from_grid(cls, grid: Mapping[str, Iterable[Any]]) -> ParameterSpace:
        if not grid:
            raise ValueError("TRIBES needs at least one numeric parameter")
        names: list[str] = []
        lower: list[float] = []
        upper: list[float] = []
        integer: list[bool] = []
        allowed: list[tuple[float, ...]] = []
        for name in sorted(grid):
            raw = list(grid[name])
            if not raw:
                raise ValueError(f"parameter {name!r} has no values")
            try:
                values = tuple(sorted({float(v) for v in raw}))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"TRIBES only supports numeric parameters; {name!r} is not") from exc
            names.append(str(name))
            lower.append(values[0])
            upper.append(values[-1])
            integer.append(all(float(v).is_integer() for v in values))
            allowed.append(values)
        return cls(
            names=tuple(names),
            lower=np.asarray(lower, dtype=float),
            upper=np.asarray(upper, dtype=float),
            integer=np.asarray(integer, dtype=bool),
            allowed=tuple(allowed),
        )

    @property
    def dimension(self) -> int:
        return len(self.names)

    def decode(self, position: np.ndarray) -> dict[str, Any]:
        decoded: dict[str, Any] = {}
        for i, name in enumerate(self.names):
            value = float(position[i])
            values = self.allowed[i]
            if len(values) > 1:
                value = min(values, key=lambda candidate: abs(candidate - value))
            elif self.integer[i]:
                value = float(round(value))
            decoded[name] = int(value) if self.integer[i] else value
        return decoded

    def encode(self, params: Mapping[str, Any]) -> np.ndarray:
        return np.asarray([float(params[name]) for name in self.names], dtype=float)

    def random_position(self, rng: np.random.Generator) -> np.ndarray:
        return rng.uniform(self.lower, self.upper)

    def clip(self, position: np.ndarray) -> np.ndarray:
        clipped = np.clip(position, self.lower, self.upper)
        for i, values in enumerate(self.allowed):
            if len(values) == 1:
                clipped[i] = values[0]
        return clipped


@dataclass
class Particle:
    position: np.ndarray
    velocity: np.ndarray
    tribe: int
    best_position: np.ndarray
    best_score: float = -math.inf
    score: float = -math.inf
    stagnant: int = 0


@dataclass
class TribesResult:
    best_params: dict[str, Any]
    best_score: float
    evaluations: int
    history: list[dict[str, Any]] = field(default_factory=list)
    cache: dict[tuple[float, ...], float] = field(default_factory=dict)

    @property
    def improvement(self) -> float:
        if not self.history:
            return 0.0
        first = float(self.history[0]["best"])
        return self.best_score - first


class TribesOptimizer:
    """Adaptive, parameter-free PSO.  No inertia/cognitive/social values are exposed."""

    def __init__(
        self,
        space: ParameterSpace,
        *,
        max_evaluations: int = 60,
        seed: int = 7,
        swarm_cap: int = 12,
    ) -> None:
        if max_evaluations < 2:
            raise ValueError("max_evaluations must be >= 2")
        self.space = space
        self.max_evaluations = int(max_evaluations)
        self.seed = int(seed)
        self.swarm_cap = max(2, int(swarm_cap))

    def minimize(self, objective: Callable[[dict[str, Any]], float]) -> TribesResult:
        rng = np.random.default_rng(self.seed)
        cache: dict[tuple[float, ...], float] = {}
        evaluations = 0
        history: list[dict[str, Any]] = []

        def evaluate(params: dict[str, Any]) -> float:
            nonlocal evaluations
            key = tuple(float(params[name]) for name in self.space.names)
            if key in cache:
                return cache[key]
            try:
                value = float(objective(params))
            except Exception:
                value = -math.inf
            if not np.isfinite(value):
                value = -math.inf
            cache[key] = value
            evaluations += 1
            return value

        def make_particle(tribe: int, position: np.ndarray | None = None) -> Particle:
            spot = self.space.random_position(rng) if position is None else self.space.clip(position)
            particle = Particle(
                position=spot,
                velocity=np.zeros(self.space.dimension),
                tribe=tribe,
                best_position=spot.copy(),
            )
            particle.score = evaluate(self.space.decode(spot))
            particle.best_score = particle.score
            return particle

        particles: list[Particle] = [make_particle(0)]
        tribes: dict[int, list[Particle]] = {0: particles}
        next_tribe = 1
        gbest_score = max(p.best_score for p in particles)
        history.append({"evaluation": evaluations, "best": gbest_score, "swarm": len(particles)})
        last_tribe_best: dict[int, float] = {0: particles[0].best_score}

        while evaluations < self.max_evaluations:
            before_generation = evaluations
            # Move every particle once per generation using its tribe's best.
            for tribe_id, members in list(tribes.items()):
                if not members:
                    continue
                tribe_best = max(members, key=lambda p: p.best_score)
                improved = tribe_best.best_score > last_tribe_best.get(tribe_id, -math.inf) + 1e-12
                if improved:
                    last_tribe_best[tribe_id] = tribe_best.best_score
                    status = "good"
                elif all(p.stagnant >= 2 for p in members):
                    status = "bad"
                else:
                    status = "neutral"
                if status == "good":
                    c1, c2, inertia = 1.10, 1.25, 0.65
                elif status == "bad":
                    c1, c2, inertia = 1.80, 0.45, 0.55
                else:
                    c1, c2, inertia = 1.40, 0.85, 0.72
                for particle in members:
                    r1 = rng.random(self.space.dimension)
                    r2 = rng.random(self.space.dimension)
                    particle.velocity = (
                        inertia * particle.velocity
                        + c1 * r1 * (particle.best_position - particle.position)
                        + c2 * r2 * (tribe_best.best_position - particle.position)
                    )
                    limit = 0.25 * (self.space.upper - self.space.lower)
                    limit = np.where(limit > 0, limit, 1.0)
                    particle.velocity = np.clip(particle.velocity, -limit, limit)
                    particle.position = self.space.clip(particle.position + particle.velocity)
                    score = evaluate(self.space.decode(particle.position))
                    if score > particle.best_score + 1e-12:
                        particle.best_score = score
                        particle.best_position = particle.position.copy()
                        particle.stagnant = 0
                    else:
                        particle.stagnant += 1

            # Structural adaptation: shrink a good tribe after a global improvement; inject a
            # particle into the worst tribe while progress is stalled.
            current_best = max(particles, key=lambda p: p.best_score)
            if current_best.best_score > gbest_score + 1e-12:
                gbest_score = current_best.best_score
                removable = [
                    (tribe_id, member)
                    for tribe_id, members in tribes.items()
                    for member in members
                    if len(members) > 1
                ]
                if removable and len(particles) > 2:
                    tribe_id, victim = min(removable, key=lambda item: item[1].best_score)
                    # Particle holds NumPy arrays, so dataclass equality is ambiguous; remove
                    # by identity instead of value.
                    tribes[tribe_id] = [p for p in tribes[tribe_id] if p is not victim]
                    particles = [p for p in particles if p is not victim]
            else:
                stagnant_tribes = [
                    tribe_id
                    for tribe_id, members in tribes.items()
                    if all(member.stagnant >= 2 for member in members)
                ]
                target = min(
                    stagnant_tribes or list(tribes),
                    key=lambda tribe_id: max((p.best_score for p in tribes[tribe_id]), default=-math.inf),
                )
                if len(particles) < self.swarm_cap and evaluations < self.max_evaluations:
                    anchor = max(tribes[target], key=lambda p: p.best_score).best_position
                    position = self.space.clip(anchor + rng.normal(0.0, 0.05, self.space.dimension) * (self.space.upper - self.space.lower))
                    particle = make_particle(target, position)
                    tribes[target].append(particle)
                    particles.append(particle)

            # Occasionally seed a new tribe, which preserves diversity instead of collapsing
            # every particle onto one basin.
            if (
                len(tribes) < 4
                and len(particles) < self.swarm_cap
                and evaluations < self.max_evaluations
                and rng.random() < 0.35
            ):
                tribe_id = next_tribe
                next_tribe += 1
                particle = make_particle(tribe_id)
                tribes[tribe_id] = [particle]
                particles.append(particle)

            history.append(
                {
                    "evaluation": evaluations,
                    "best": gbest_score,
                    "swarm": len(particles),
                    "tribes": len(tribes),
                }
            )
            if evaluations == before_generation:
                # The discrete space is exhausted or every particle converged to a cached
                # point.  Stop rather than spinning forever without spending an evaluation.
                if len(particles) >= self.swarm_cap:
                    break
                particle = make_particle(max(tribes), self.space.random_position(rng))
                tribes[particle.tribe].append(particle)
                particles.append(particle)

        best_particle = max(particles, key=lambda p: p.best_score)
        return TribesResult(
            best_params=self.space.decode(best_particle.best_position),
            best_score=best_particle.best_score,
            evaluations=evaluations,
            history=history,
            cache=cache,
        )


__all__ = ["ParameterSpace", "TribesOptimizer", "TribesResult"]
