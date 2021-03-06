import logging
from typing import Callable, Dict, Final, Literal, Optional, Tuple

import numpy as np
import scipy.optimize as opt  # type: ignore
import tensorflow as tf  # type: ignore
from driver.car import LegacyPlanCar, LegacyRewardCar
from driver.legacy.models import Driver  # type: ignore
from driver.simulation_utils import legacy_car_dynamics_step_tf
from driver.world import ThreeLaneCarWorld

from active import algos


def shape_compat(arr: np.ndarray, shape: Tuple[int, ...]) -> bool:
    if len(arr.shape) != len(shape):
        return False
    for i, j in zip(arr.shape, shape):
        if j != -1 and i != j:
            return False
    return True


def orient_normals(
    normals: np.ndarray,
    preferences: np.ndarray,
    use_equiv: bool = False,
    n_reward_features: int = 4,
) -> np.ndarray:
    """Orients halfplane normal vectors relative to preferences

    Args:
        normals (np.ndarray): (n, 4) array of difference of reward features
        preferences (np.ndarray): 0/1 or +-1 vector of preferences
        use_equiv (bool, optional): If equivalent preferences are allowed. Defaults to False.
        n_reward_features (int, optional): Number of reward features. Defaults to 4.

    Returns:
        np.ndarray: normals oriented so that the prefered trajectory comes first
    """

    """ Orients halfplane normal vectors relative to preferences. """
    assert_normals(normals, use_equiv, n_reward_features)
    assert preferences.shape == (normals.shape[0],)

    if np.all((preferences == 0) | (preferences == 1)):
        preferences = preferences * 2 - 1

    assert np.all((preferences == 1) | (preferences == -1))

    oriented_normals = (normals.T * preferences).T

    assert_normals(oriented_normals, use_equiv, n_reward_features)
    return oriented_normals


def make_normals(inputs: np.ndarray, sim: Driver, use_equiv: bool) -> Tuple[np.ndarray, np.ndarray]:
    """Converts pairs of car inputs to trajectory preference normal vectors.

    Args:
        inputs (np.ndarray): (n, 2, T, 2) array of pairs of 2-dimension actions for T timesteps
        sim (Driver): Driving simulation to get features from
        use_equiv (bool): Allow equivalent preferences?

    Returns:
        Tuple[np.ndarray, np.ndarray]: input features and normal vectors
    """
    if len(inputs.shape) == 3:
        shape_compat(inputs, (-1, 2, -1))
    elif len(inputs.shape) == 4:
        shape_compat(inputs, (-1, 2, -1, 2))

    normals = np.empty(shape=(inputs.shape[0], sim.num_of_features))
    input_features = np.empty(shape=(inputs.shape[0], 2, sim.num_of_features))
    for i, (input_a, input_b) in enumerate(inputs):
        sim.feed(input_a)
        phi_a = np.array(sim.get_features())

        sim.feed(input_b)
        phi_b = np.array(sim.get_features())

        input_features[i] = np.stack((phi_a, phi_b))

        normals[i] = phi_a - phi_b
    assert_normals(normals, use_equiv)
    return input_features, normals


def assert_normals(
    normals: np.ndarray, use_equiv: bool = False, n_reward_features: int = 4
) -> None:
    """ Asserts the given array is an array of normal vectors defining half space constraints."""
    shape = normals.shape
    assert len(shape) == 2, f"shape does not have 2 dimensions:{shape}"
    # Constant offset constraint adds one dimension to normal vectors.
    assert shape[1] == n_reward_features + int(use_equiv)


class TrajOptimizer:
    """ Finds optimal trajectories in the Driver environment. """

    HORIZON: Final[int] = 50

    def __init__(
        self,
        n_planner_iters: int,
        optim: tf.keras.optimizers.Optimizer = tf.keras.optimizers.SGD(0.1),
        init_controls: Optional[np.ndarray] = None,
        log_best_init: bool = False,
    ):
        self.world = ThreeLaneCarWorld()

        self.optim = optim
        self.n_opt_iters: Final[int] = n_planner_iters

        self.init_controls = np.array(
            [
                [[0.0, 0.0]] * self.HORIZON,
                [[-5 * 0.13, 0]] * self.HORIZON,
                [[5 * 0.13, 0]] * self.HORIZON,
            ]
        )
        assert self.init_controls.shape == (3, self.HORIZON, 2)
        if init_controls is not None:
            self.init_controls = np.concatenate((self.init_controls, init_controls))

        self.tf_controls = tf.Variable(np.zeros((self.HORIZON, 2)), dtype=tf.float32)

        self.main_car = LegacyRewardCar(
            env=self.world,
            init_state=np.array([0.0, -0.3, np.pi / 2.0, 0.4], dtype=np.float32),
            weights=np.zeros(4),
        )
        self.other_car = LegacyPlanCar(env=self.world)

        self.log_best_init = log_best_init

        self.cache: Dict[Tuple[bytes, bytes], Tuple[np.ndarray, float]] = {}

    def make_loss(self) -> Callable[[], tf.Tensor]:
        other_actions = tf.constant(self.other_car.plan, dtype=tf.float32)

        main_init = self.main_car.init_state
        other_init = self.other_car.init_state

        @tf.function
        def loss() -> tf.Tensor:
            sum_reward = 0.0

            controls = tf.stack((self.tf_controls, other_actions), axis=1)

            main_car_state = main_init
            other_car_state = other_init

            for control in controls:
                main_control = control[0]
                other_control = control[1]
                # tf.print("state=", main_car_state, other_car_state)
                # tf.print("action=", main_control)
                assert main_control.shape == (2,)
                main_car_state = legacy_car_dynamics_step_tf(main_car_state, main_control)

                other_car_state = legacy_car_dynamics_step_tf(other_car_state, other_control)

                # tf.print("after step state=", main_car_state)

                reward = self.main_car.reward_fn(
                    (main_car_state, other_car_state), None
                )  # Action doesn't matter for reward

                sum_reward += reward
            return -sum_reward

        return loss

    def make_opt_traj(
        self, reward: np.ndarray, start_state: Optional[np.ndarray] = None, memorize: bool = False,
    ) -> Tuple[np.ndarray, float]:
        """ Finds the optimal sequence of actions under a given reward and starting state. """
        if start_state is not None:
            assert start_state.shape == (2, 4)
            self.main_car.init_state = start_state[0]
            self.other_car.set_init_state(start_state[1])
        else:
            start_state = np.stack(
                (self.main_car.init_state.numpy(), self.other_car.init_state.numpy())
            )

        if (reward.tobytes(), start_state.tobytes()) in self.cache.keys():
            return self.cache[reward.tobytes(), start_state.tobytes()]

        self.main_car.weights = reward

        loss = self.make_loss()

        best_loss = float("inf")
        best_init = -1
        for i, init_control in enumerate(self.init_controls):
            assert init_control.shape == (50, 2)
            self.tf_controls.assign(init_control)

            for _ in range(self.n_opt_iters):
                self.optim.minimize(loss, self.tf_controls)

            # TODO(joschnei): Figure out if this recomputation can be avoided. Or maybe the result
            # is cached and this is free.
            current_loss = loss()

            if current_loss < best_loss:
                best_loss = current_loss
                best_plan: np.ndarray = self.tf_controls.numpy()
                best_init = i

        assert i > 0

        if self.log_best_init:
            logging.info(f"Best traj found from init={best_init}")

        if memorize:
            self.cache[reward.tobytes(), start_state.tobytes()] = (best_plan, float(best_loss))

        return best_plan, best_loss


def get_simulated_feedback(
    simulation: Driver,
    input_A: np.ndarray,
    input_B: np.ndarray,
    query_type: str,
    true_reward: np.ndarray,
    delta: Optional[float] = None,
) -> Tuple[np.ndarray, np.ndarray, int]:
    """ Gets preference between trajectories from an agent simulated by true_reward """
    simulation.feed(input_A)
    phi_A = np.array(simulation.get_features())
    simulation.feed(input_B)
    phi_B = np.array(simulation.get_features())
    if query_type == "weak":
        # TODO(joschnei): Implement weak errors using delta. I think there's a model for this but I can't remember off hand.
        raise NotImplementedError("Simulated weak preferences not implemented.")
        if delta is None:
            raise ValueError("Must provide delta when using weak queries.")
    elif query_type == "strict":
        s = 1 if true_reward @ (phi_A - phi_B) > 0 else -1
    else:
        raise ValueError(f'query type {query_type} must be either "strict" or "weak"')
    return phi_A, phi_B, s


def get_feedback(simulation_object, input_A, input_B, query_type):
    """ Gets a preference between trajectories from a human user """
    simulation_object.feed(input_A)
    phi_A = np.array(simulation_object.get_features())
    simulation_object.feed(input_B)
    phi_B = np.array(simulation_object.get_features())
    s = -2
    while s == -2:
        if query_type == "weak":
            selection = input('A/B to watch, 1/2 to vote, 0 for "About Equal": ').lower()
        elif query_type == "strict":
            selection = input("A/B to watch, 1/2 to vote: ").lower()
        else:
            raise ValueError("There is no query type called " + query_type)
        if selection == "a":
            simulation_object.feed(input_A)
            simulation_object.watch(1)
        elif selection == "b":
            simulation_object.feed(input_B)
            simulation_object.watch(1)
        elif selection == "0" and query_type == "weak":
            s = 0
        elif selection == "1":
            s = 1
        elif selection == "2":
            s = -1
    return phi_A, phi_B, s


def run_algo(
    criterion: Literal["information", "volume", "random"],
    env: Driver,
    w_samples: np.ndarray,
    delta_samples: np.ndarray,
    continuous: bool = False,
):
    """ Gets next pair of trajectories to ask for a preference over. """
    shape_compat(w_samples, (-1, 4))
    shape_compat(delta_samples, (-1,))

    if criterion == "information":
        return algos.information(env, w_samples, delta_samples, continuous)
    if criterion == "volume":
        return algos.volume(env, w_samples, delta_samples, continuous)
    elif criterion == "random":
        return algos.random(env)
    else:
        raise ValueError("There is no criterion called " + criterion)


def play(sim: Driver, optimal_ctrl):
    """ Renders trajectory for user. """
    sim.set_ctrl(optimal_ctrl)
    keep_playing = "y"
    while keep_playing == "y":
        keep_playing = "u"
        sim.watch(1)
        while keep_playing != "n" and keep_playing != "y":
            keep_playing = input("Again? [y/n]: ").lower()
    return optimal_ctrl
