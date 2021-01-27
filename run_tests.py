""" Runs the test set generated by post.py by generating fake reward weights and seeing how many
are caught by the preferences."""

import logging
import pickle
from itertools import product
from math import log
from pathlib import Path
from typing import Dict, Generator, List, Optional, Sequence, Set, Tuple, Union

import argh  # type: ignore
import numpy as np
from argh import arg
from joblib import Parallel, delayed  # type: ignore
from numpy.linalg import norm  # type: ignore
from scipy.stats import multivariate_normal  # type: ignore
from sklearn.metrics import confusion_matrix  # type: ignore

from post import TestFactory

N_FEATURES = 4


def assert_normals(normals: np.ndarray, use_equiv: bool) -> None:
    """ Asserts the given array is an array of normal vectors defining half space constraints."""
    shape = normals.shape
    assert len(shape) == 2
    # Constant offset constraint adds one dimension to normal vectors.
    assert shape[1] == N_FEATURES + int(use_equiv)


def assert_reward(reward: np.ndarray, use_equiv: bool) -> None:
    """ Asserts the given array is might be a reward feature vector. """
    assert reward.shape == (N_FEATURES + int(use_equiv),)
    assert abs(norm(reward) - 1) < 0.000001


def normalize(vectors: np.ndarray) -> np.ndarray:
    """ Takes in a 2d array of row vectors and ensures each row vector has an L_2 norm of 1."""
    return (vectors.T / norm(vectors, axis=1)).T


def make_gaussian_rewards(
    n_rewards: int,
    use_equiv: bool,
    mean: Optional[np.ndarray] = None,
    cov: Union[np.ndarray, float, None] = None,
) -> np.ndarray:
    """ Makes n_rewards uniformly sampled reward vectors of unit length."""
    assert n_rewards > 0
    mean = mean if mean is not None else np.zeros(N_FEATURES)
    dist = multivariate_normal(mean=mean, cov=cov)

    rewards = normalize(dist.rvs(size=n_rewards))
    if use_equiv:
        rewards = np.stack(rewards, np.ones(rewards.shape[0]), axis=1)

    return rewards


def find_reward_boundary(
    normals: np.ndarray, n_rewards: int, reward: np.ndarray, epsilon: float, use_equiv: bool,
) -> Tuple[np.ndarray, np.ndarray]:
    """ Generates n_rewards reward vectors and determines which are aligned. """
    assert_normals(normals, use_equiv)
    assert n_rewards > 0
    assert epsilon >= 0.0
    assert_reward(reward, use_equiv)

    cov = 1.0

    rewards = make_gaussian_rewards(n_rewards, use_equiv, mean=reward, cov=cov)
    normals = normals[reward @ normals.T > epsilon]
    ground_truth_alignment = np.all(rewards @ normals.T > 0, axis=1)
    mean_agree = np.mean(ground_truth_alignment)

    while mean_agree > 0.55 or mean_agree < 0.45:
        if mean_agree > 0.55:
            cov *= 1.1
        else:
            cov /= 1.1
        rewards = make_gaussian_rewards(n_rewards, use_equiv, mean=reward, cov=cov)
        normals = normals[reward @ normals.T > epsilon]
        ground_truth_alignment = np.all(rewards @ normals.T > 0, axis=1)
        mean_agree = np.mean(ground_truth_alignment)

    assert ground_truth_alignment.shape == (n_rewards,)
    assert rewards.shape == (n_rewards, N_FEATURES)

    return rewards, ground_truth_alignment


def run_test(normals: np.ndarray, test_rewards: np.ndarray, use_equiv: bool) -> np.ndarray:
    """ Returns the predicted alignment of the fake rewards by the normals. """
    assert_normals(normals, use_equiv)
    results = np.all(np.dot(test_rewards, normals.T) > 0, axis=1)
    return results


def eval_test(
    normals: np.ndarray, rewards: np.ndarray, aligned: np.ndarray, use_equiv: bool
) -> np.ndarray:
    """ Makes a confusion matrix by evaluating a test on the fake rewards. """
    assert rewards.shape[0] == aligned.shape[0]

    for reward in rewards:
        assert_reward(reward, use_equiv)

    if normals.shape[0] > 0:
        results = run_test(normals, rewards, use_equiv)
        logging.info(
            f"predicted true={np.sum(results)}, predicted false={results.shape[0] - np.sum(results)}"
        )
        return confusion_matrix(y_true=aligned, y_pred=results, labels=[False, True])
    else:
        return confusion_matrix(
            y_true=aligned, y_pred=np.ones(aligned.shape, dtype=bool), labels=[False, True],
        )


def make_outname(
    skip_remove_duplicates: bool,
    skip_noise_filtering: bool,
    skip_epsilon_filtering: bool,
    skip_redundancy_filtering: bool,
    base: str = "out",
) -> str:
    outname = base
    if skip_remove_duplicates:
        outname += ".skip_duplicates"
    if skip_noise_filtering:
        outname += ".skip_noise"
    if skip_epsilon_filtering:
        outname += ".skip_epsilon"
    if skip_redundancy_filtering:
        outname += ".skip_lp"
    outname += ".pkl"
    return outname


def remove_equiv(preferences: np.ndarray, *arrays: np.ndarray) -> Tuple[np.ndarray, ...]:
    """ Finds equivalence preferences and removes them + the associated elements of *arrays. """
    indices = preferences != 0
    preferences = preferences[indices]
    out_arrays = list()
    for array in arrays:
        out_arrays.append(array[indices])
    return (preferences, *out_arrays)


def add_equiv_constraints(
    preferences: np.ndarray, normals: np.ndarray, equiv_prob: float
) -> np.ndarray:
    """ Adds equivalence constraints to a set of halspace constraints. """
    out_normals = list()
    for preference, normal in zip(preferences, normals):
        if preference == 0:
            max_return_diff = equiv_prob - log(2 * equiv_prob - 2)
            # w phi >= -max_return_diff
            # w phi + max_reutrn_diff >=0
            # w phi <= max_return diff
            # 0 <= max_return_diff - w phi
            out_normals.append(np.append(normal, [max_return_diff]))
            out_normals.append(np.append(-normals, [max_return_diff]))
        elif preference == 1 or preference == -1:
            out_normals.append(np.append(normal * preference, [0]))

    return np.ndarray(out_normals)


def load(path: Path, overwrite: bool) -> dict:
    if overwrite:
        return dict()
    if path.exists():
        return pickle.load(open(path, "rb"))
    return dict()


Experiment = Tuple[float, float, int]


def run_human_experiment(
    test_rewards: np.ndarray,
    normals: np.ndarray,
    input_features: np.ndarray,
    preferences: np.ndarray,
    epsilon: float,
    delta: float,
    n_human_samples: int,
    factory: TestFactory,
    use_equiv: bool,
) -> Tuple[np.ndarray, np.ndarray, Experiment]:
    """Distills a set of normals and preferences into a test using the factory, and runs that test on test_rewards

    Args:
        test_rewards (np.ndarray): Rewards to run test on
        normals (np.ndarray): normal vector of halfplane constraints defining test questions
        input_features (np.ndarray): reward features of trajectories in each question
        preferences (np.ndarray): Human provided preference over trajectories
        epsilon (float): Size of minimum value gap required for de-noising
        delta (float): How much of the reward posterior must be over the value gap
        n_human_samples (int): Number of preferences to prune down to
        factory (TestFactory): Factory to produce test questions
        use_equiv (bool): Allow equivalent preference labels?

    Returns:
        Tuple[np.ndarray, np.ndarray, Experiment]: indices of the selected test questions, test results for each reward, and experimental hyperparameters
    """
    if n_human_samples == -1:
        n_human_samples == normals.shape[0]
    filtered_normals = normals[:n_human_samples]
    filtered_normals, indices = factory.filter_halfplanes(
        inputs_features=input_features,
        normals=filtered_normals,
        preferences=preferences,
        epsilon=epsilon,
        delta=delta,
    )

    experiment = (epsilon, delta, n_human_samples)

    results = run_test(filtered_normals, test_rewards, use_equiv)

    return indices, results, experiment


def run_gt_experiment(
    normals: np.ndarray,
    n_rewards: int,
    reward: np.ndarray,
    epsilon: float,
    delta: float,
    use_equiv: bool,
    n_human_samples: int,
    factory: TestFactory,
    input_features: np.ndarray,
    preferences: np.ndarray,
    outdir: Path,
) -> Tuple[np.ndarray, np.ndarray, Experiment]:
    experiment = (epsilon, delta, n_human_samples)

    logdir = outdir / "logs"
    logdir.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        filename=logdir / f"{epsilon}.{delta}.{n_human_samples}.log", level=logging.INFO
    )

    logging.info(f"Working on epsilon={epsilon}, delta={delta}, n={n_human_samples}")

    # This takes 0.02-0.05 seconds on lovelace
    # TODO(joschnei): Really need to make this a fixed set common between comparisons.
    rewards, aligned = find_reward_boundary(normals, n_rewards, reward, epsilon, use_equiv)
    logging.info(f"aligned={np.sum(aligned)}, unaligned={aligned.shape[0] - np.sum(aligned)}")

    filtered_normals = normals[:n_human_samples]
    filtered_normals, indices = factory.filter_halfplanes(
        inputs_features=input_features,
        normals=filtered_normals,
        preferences=preferences,
        epsilon=epsilon,
        delta=delta,
    )

    confusion = eval_test(
        normals=filtered_normals, rewards=rewards, aligned=aligned, use_equiv=use_equiv
    )

    assert confusion.shape == (2, 2)

    return indices, confusion, experiment


def make_experiments(
    epsilons: Sequence[float],
    deltas: Sequence[float],
    n_human_samples: Sequence[int],
    overwrite: bool,
    experiments: Optional[Set[Experiment]] = None,
) -> Generator[Experiment, None, None]:
    if overwrite:
        # TODO(joschnei): This is stupid but I can't be bothered to cast an iterator to a generator.
        for experiment in product(epsilons, deltas, n_human_samples):
            yield experiment
    else:
        for experiment in product(epsilons, deltas, n_human_samples):
            if experiments is None or not (experiment in experiments):
                yield experiment


@arg("--epsilons", nargs="+", type=float)
@arg("--deltas", nargs="+", type=float)
@arg("--human-samples", nargs="+", type=int)
def gt(
    epsilons: List[float] = [0.0],
    deltas: List[float] = [0.05],
    n_rewards: int = 100,
    human_samples: List[int] = [1],
    n_model_samples: int = 1000,
    input_features_name: Path = Path("input_features.npy"),
    normals_name: Path = Path("normals.npy"),
    preferences_name: Path = Path("preferences.npy"),
    true_reward_name: Path = Path("true_reward.npy"),
    flags_name: Path = Path("flags.pkl"),
    datadir: Path = Path(),
    outdir: Path = Path(),
    use_equiv: bool = False,
    skip_remove_duplicates: bool = False,
    skip_noise_filtering: bool = False,
    skip_epsilon_filtering: bool = False,
    skip_redundancy_filtering: bool = False,
    replications: Optional[str] = None,
    overwrite: bool = False,
) -> None:
    """ Run tests with full data to determine how much reward noise gets"""
    logging.basicConfig(level="INFO")

    if replications is not None:
        # TODO(joschnei): Add better error handling for replications strings
        start, stop = replications.split("-")
        for replication in range(int(start), int(stop) + 1):
            gt(
                epsilons,
                deltas,
                n_rewards,
                human_samples,
                n_model_samples,
                input_features_name,
                normals_name,
                preferences_name,
                true_reward_name,
                flags_name,
                datadir / str(replication),
                outdir / str(replication),
                skip_remove_duplicates,
                skip_noise_filtering,
                skip_epsilon_filtering,
                skip_redundancy_filtering,
                overwrite,
            )
        exit()

    outdir.mkdir(parents=True, exist_ok=True)

    input_features = np.load(datadir / input_features_name)
    normals = np.load(datadir / normals_name)
    preferences = np.load(datadir / preferences_name)
    reward = np.load(datadir / true_reward_name)

    if not use_equiv:
        assert not np.any(preferences == 0)

    flags = pickle.load(open(datadir / flags_name, "rb"))
    query_type = flags["query_type"]
    equiv_probability = flags["delta"]

    factory = TestFactory(
        query_type=query_type,
        reward_dimension=normals.shape[1],
        equiv_probability=equiv_probability,
        n_reward_samples=n_model_samples,
        skip_remove_duplicates=skip_remove_duplicates,
        skip_noise_filtering=skip_noise_filtering,
        skip_epsilon_filtering=skip_epsilon_filtering,
        skip_redundancy_filtering=skip_redundancy_filtering,
    )

    assert input_features.shape[0] > 0
    assert preferences.shape[0] > 0
    assert normals.shape[0] > 0
    assert reward.shape == (N_FEATURES,)

    if use_equiv:
        normals = add_equiv_constraints(preferences, normals, equiv_prob=equiv_probability)
        reward = np.append(reward, [1])
    else:
        if query_type == "weak":
            preferences, input_features, normals = remove_equiv(
                preferences, input_features, normals
            )
        normals = (normals.T * preferences).T
    assert_normals(normals, use_equiv)

    confusion_path = outdir / make_outname(
        skip_remove_duplicates,
        skip_noise_filtering,
        skip_epsilon_filtering,
        skip_redundancy_filtering,
        base="confusion",
    )
    test_path = outdir / make_outname(
        skip_remove_duplicates,
        skip_noise_filtering,
        skip_epsilon_filtering,
        skip_redundancy_filtering,
        base="indices",
    )

    confusions: Dict[Experiment, np.ndarray] = load(confusion_path, overwrite)
    minimal_tests: Dict[Experiment, np.ndarray] = load(test_path, overwrite)

    experiments = make_experiments(
        epsilons, deltas, human_samples, overwrite, experiments=set(minimal_tests.keys())
    )

    for indices, confusion, experiment in Parallel(n_jobs=-2)(
        delayed(run_gt_experiment)(
            normals,
            n_rewards,
            reward,
            epsilon,
            delta,
            use_equiv,
            n,
            factory,
            input_features,
            preferences,
            outdir,
        )
        for epsilon, delta, n in experiments
    ):
        minimal_tests[experiment] = indices
        confusions[experiment] = confusion

    pickle.dump(confusions, open(confusion_path, "wb"))
    pickle.dump(minimal_tests, open(test_path, "wb"))


@arg("--epsilons", nargs="+", type=float)
@arg("--deltas", nargs="+", type=float)
@arg("--human-samples", nargs="+", type=int)
def human(
    epsilons: List[float] = [0.0],
    deltas: List[float] = [0.05],
    n_rewards: int = 10000,
    human_samples: List[int] = [1],
    n_model_samples: int = 1000,
    input_features_name: Path = Path("input_features.npy"),
    normals_name: Path = Path("normals.npy"),
    preferences_name: Path = Path("preferences.npy"),
    flags_name: Path = Path("flags.pkl"),
    datadir: Path = Path("questions"),
    outdir: Path = Path("questions"),
    rewards_path: Optional[Path] = None,
    use_equiv: bool = False,
    skip_remove_duplicates: bool = False,
    skip_noise_filtering: bool = False,
    skip_epsilon_filtering: bool = False,
    skip_redundancy_filtering: bool = False,
    overwrite: bool = False,
):
    input_features = np.load(datadir / input_features_name)
    normals = np.load(datadir / normals_name)
    preferences = np.load(datadir / preferences_name)
    assert preferences.shape[0] > 0

    flags = pickle.load(open(datadir / flags_name, "rb"))
    query_type = flags["query_type"]
    equiv_probability = flags["delta"]

    factory = TestFactory(
        query_type=query_type,
        reward_dimension=normals.shape[1],
        equiv_probability=equiv_probability,
        n_reward_samples=n_model_samples,
        skip_remove_duplicates=skip_remove_duplicates,
        skip_noise_filtering=skip_noise_filtering,
        skip_epsilon_filtering=skip_epsilon_filtering,
        skip_redundancy_filtering=skip_redundancy_filtering,
    )

    if use_equiv:
        normals = add_equiv_constraints(preferences, normals, equiv_prob=equiv_probability)
    else:
        if query_type == "weak":
            preferences, input_features, normals = remove_equiv(
                preferences, input_features, normals
            )
        normals = (normals.T * preferences).T
    assert_normals(normals, use_equiv)

    test_path = outdir / make_outname(
        skip_remove_duplicates,
        skip_noise_filtering,
        skip_epsilon_filtering,
        skip_redundancy_filtering,
        base="indices",
    )
    test_results_path = outdir / make_outname(
        skip_remove_duplicates,
        skip_noise_filtering,
        skip_epsilon_filtering,
        skip_redundancy_filtering,
        base="test_results",
    )

    minimal_tests: Dict[Experiment, np.ndarray] = load(test_path, overwrite)
    results: Dict[Experiment, np.ndarray] = load(test_results_path, overwrite)

    if rewards_path is None:
        test_rewards = make_gaussian_rewards(n_rewards, use_equiv)
    else:
        test_rewards = np.load(open(rewards_path, "rb"))
    np.save(outdir / "test_rewards.npy", test_rewards)

    experiments = make_experiments(
        epsilons, deltas, human_samples, overwrite, experiments=set(minimal_tests.keys())
    )

    for indices, result, experiment in Parallel(n_jobs=-2)(
        delayed(run_human_experiment)(
            test_rewards,
            normals,
            input_features,
            preferences,
            epsilon,
            delta,
            n,
            factory,
            use_equiv,
        )
        for epsilon, delta, n in experiments
    ):
        minimal_tests[experiment] = indices
        results[experiment] = result

    pickle.dump(minimal_tests, open(test_path, "wb"))
    pickle.dump(results, open(test_results_path, "wb"))


if __name__ == "__main__":
    argh.dispatch_commands([gt, human])
