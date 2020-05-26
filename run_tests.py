""" Runs the test set generated by post.py by generating fake reward weights and seeing how many
are caught by the preferences."""

from pathlib import Path
from typing import List

import argh
import matplotlib.pyplot as plt
import numpy as np
from argh import arg
from scipy.stats import multivariate_normal

from post import filter_halfplanes


def run_test(reward, psi, s, reward_noise: float = 1.0, n_rewards: int = 100,) -> float:
    dist = multivariate_normal(mean=reward, cov=np.eye(reward.shape[0]) * reward_noise)

    fake_rewards = dist.rvs(n_rewards)
    fake_rewards = (fake_rewards.T / np.linalg.norm(fake_rewards, axis=1)).T

    for fake_reward in fake_rewards:
        assert np.abs(np.linalg.norm(fake_reward) - 1) < 0.0001

    frac_pass = np.mean(np.all(np.dot(fake_rewards, psi.T) * s > 0, axis=1))

    return frac_pass


@arg("--noises", nargs="+", type=float)
@arg("--samples", nargs="+", type=int)
def run_tests(
    *,
    noises: List[float] = [1.0],
    samples: List[int] = [1],
    n_rewards: int = 100,
    datadir: Path = Path("preferences"),
):
    true_reward = np.load(datadir / "reward.npy")
    psi = np.load(datadir / "psi.npy")
    s = np.load(datadir / "s.npy")
    for sample in samples:
        filtered_psi, filtered_s, _ = filter_halfplanes(
            psi=psi[:sample], s=s[:sample], n_samples=1000
        )

        frac_passes = np.array(
            [
                run_test(
                    psi=filtered_psi,
                    s=filtered_s,
                    reward=true_reward,
                    reward_noise=noise,
                    n_rewards=n_rewards,
                )
                for noise in noises
            ]
        )

        assert np.all((frac_passes <= 1.0) & (frac_passes >= 0.0))
        # print(frac_passes)

        plt.plot(noises, frac_passes, label=sample)
    plt.title(f"Pass rate of {n_rewards} reward functions vs variance")
    plt.xlabel("Variance of Guassian Generating Rewards")
    plt.ylabel("Pass rate of Rewards")
    plt.ylim((0, 1))
    plt.legend()
    plt.savefig("results.png")


if __name__ == "__main__":
    argh.dispatch_commands([run_test, run_tests])
