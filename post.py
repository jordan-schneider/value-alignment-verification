""" Post-process noise and consistency filtering. """

from typing import List, Optional, Tuple

import numpy as np
from scipy.spatial import distance  # type: ignore

from linear_programming import remove_redundant_constraints
from sampling import Sampler
from simulation_utils import create_env


def sample(
    reward_dimension: int,
    a_phis: np.ndarray,
    b_phis: np.ndarray,
    preferences: np.ndarray,
    n_samples: int,
    query_type: str,
    delta: float,
) -> np.ndarray:
    """ Samples n_samples rewards via MCMC. """
    w_sampler = Sampler(reward_dimension)
    for a_phi, b_phi, preference in zip(a_phis, b_phis, preferences):
        w_sampler.feed(a_phi, b_phi, [preference])
    rewards, _ = w_sampler.sample_given_delta(n_samples, query_type, delta)
    return rewards


def remove_duplicates(
    normals: np.ndarray, precision=0.0001
) -> Tuple[np.ndarray, np.ndarray]:
    """ Remove halfspaces that have small cosine similarity to another. """
    out: List[np.ndarray] = list()
    indices: List[int] = list()
    for i, normal in enumerate(normals):
        for accepted_normal in out:
            if distance.cosine(normal, accepted_normal) < precision:
                break
        out.append(normal)
        indices.append(i)
    return np.array(out).reshape(-1, normals.shape[1]), np.array(indices, dtype=int)


def filter_halfplanes(
    inputs_features: np.ndarray,
    normals: np.ndarray,
    preferences: np.ndarray,
    query_type: str,
    equiv_probability: float,
    noise_threshold: float = 0.7,
    epsilon: float = 0.0,
    delta: float = 0.05,
    n_samples: Optional[int] = None,
    rewards: Optional[np.ndarray] = None,
    deterministic: bool = False,
    skip_remove_duplicates: bool = False,
    skip_noise_filtering: bool = False,
    skip_epsilon_filtering: bool = False,
    skip_redundancy_filtering: bool = False,
) -> Tuple[np.ndarray, np.ndarray]:
    """ Filters test questions by removing noise answers, requiring answers have a gap of at
    least epsilon, and removing redundant questions via linear programming. """
    a_phis = inputs_features[:, 0]
    b_phis = inputs_features[:, 1]
    filtered_normals = normals
    indices = np.array(range(filtered_normals.shape[0]))

    if not skip_remove_duplicates:
        filtered_normals, indices = remove_duplicates(normals)

        print(f"After removing duplicates, there are {len(indices)} questions.")

    assert np.all(normals[indices] == filtered_normals)

    if not skip_noise_filtering:
        if rewards is None:
            if deterministic:
                raise ValueError("Must provide rewards to use deterministic mode.")
            if n_samples is None:
                raise ValueError("Must provide n_samples if reward is not provided")

            rewards = sample(
                reward_dimension=create_env("driver").num_of_features,
                n_samples=n_samples,
                a_phis=a_phis,
                b_phis=b_phis,
                preferences=preferences,
                query_type=query_type,
                delta=equiv_probability,
            )

        filtered_indices = (
            np.mean(np.dot(rewards, filtered_normals.T) > 0, axis=0) > noise_threshold
        )
        indices = indices[filtered_indices]
        assert all([row in filtered_normals for row in normals[indices]])
        filtered_normals = normals[indices].reshape(-1, normals.shape[1])

        print(f"After noise filtering there are {len(indices)} questions.")

    if not skip_epsilon_filtering and filtered_normals.shape[0] > 0:
        if not deterministic and n_samples is not None:
            # This reward generation logic is jank.
            rewards = sample(
                reward_dimension=create_env("driver").num_of_features,
                n_samples=n_samples,
                a_phis=a_phis,
                b_phis=b_phis,
                preferences=preferences,
                query_type=query_type,
                delta=equiv_probability,
            )

        opinions = np.dot(rewards, filtered_normals.T).T
        correct_opinions = opinions > epsilon

        # Filter halfspaces that don't have 1-d probability that the expected return gap is epsilon.
        filtered_indices = np.mean(correct_opinions, axis=1) > 1 - delta
        indices = indices[filtered_indices]
        assert all([row in filtered_normals for row in normals[indices]])
        filtered_normals = normals[indices].reshape(-1, normals.shape[1])

        print(f"After epsilon delta filtering there are {len(indices)} questions.")

    if not skip_redundancy_filtering and filtered_normals.shape[0] > 0:
        # Remove redundant halfspaces
        filtered_normals, constraint_indices = remove_redundant_constraints(
            filtered_normals
        )

        constraint_indices = np.array(constraint_indices, dtype=np.int)
        indices = indices[constraint_indices]
        assert np.all(normals[indices] == filtered_normals)

        print(f"After removing redundancies there are {len(indices)} questions.")

    return filtered_normals, indices
