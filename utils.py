import numpy as np
from numpy.linalg import norm

from sampling import Sampler


def assert_normals(normals: np.ndarray, use_equiv: bool, n_reward_features: int = 4) -> None:
    """ Asserts the given array is an array of normal vectors defining half space constraints."""
    shape = normals.shape
    assert len(shape) == 2, f"shape does not have 2 dimensions:{shape}"
    # Constant offset constraint adds one dimension to normal vectors.
    assert shape[1] == n_reward_features + int(use_equiv)


def assert_reward(
    reward: np.ndarray, use_equiv: bool, n_reward_features: int = 4, eps: float = 0.000001
) -> None:
    """ Asserts the given array is might be a reward feature vector. """
    assert np.all(np.isfinite(reward))
    assert reward.shape == (n_reward_features + int(use_equiv),)
    assert abs(norm(reward) - 1) < eps


def normalize(vectors: np.ndarray) -> np.ndarray:
    """ Takes in a 2d array of row vectors and ensures each row vector has an L_2 norm of 1."""
    return (vectors.T / norm(vectors, axis=1)).T


def orient_normals(
    normals: np.ndarray,
    preferences: np.ndarray,
    use_equiv: bool = False,
    n_reward_features: int = 4,
) -> np.ndarray:
    assert_normals(normals, use_equiv, n_reward_features)
    assert preferences.shape == (normals.shape[0],)

    oriented_normals = (normals.T * preferences).T

    assert_normals(oriented_normals, use_equiv, n_reward_features)
    return oriented_normals


def get_mean_reward(
    elicited_input_features: np.ndarray,
    elicited_preferences: np.ndarray,
    M: int,
    query_type: str,
    delta: float,
):
    n_features = elicited_input_features.shape[2]
    w_sampler = Sampler(n_features)
    for (a_phi, b_phi), preference in zip(elicited_input_features, elicited_preferences):
        w_sampler.feed(a_phi, b_phi, [preference])
    reward_samples, _ = w_sampler.sample_given_delta(M, query_type, delta)
    mean_reward = np.mean(reward_samples, axis=0)
    assert len(mean_reward.shape) == 1 and mean_reward.shape[0] == n_features
    return mean_reward
