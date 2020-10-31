This code elicits preferences between trajectories from humans, distills those preferences into a
minimal alignment test, and then tests a number of randomly generated agents.

To run:
1. Run the following once if the ctrl_samples directory is empty or does not currently exist.
```python
	python input_sampler.py driver 500000
``` 
1. Run the following to start the inference procedure. You should press both `a` and `b` every iteration to watch the trajectories, and press either `1` or `2` to select the trajectory you like more. Press `space` to start each video, and press `Esc` to exit the video and return to the prompt.
```python
	python demos.py driver information weak --M=100 --outdir=human/[subject-id]
```

If at any time you get bored/want to give up, press `Ctrl-C` EXACTLY ONCE and wait a moment to save
your progress. I'd really appreciate if you could give 110 preferences, but any amount is fine.


This code is a fork of https://github.com/Stanford-ILIAD/easy-active-learning

The novel contributions are in post.py and run_test.py. 

---

Companion code to CoRL 2019 paper:  
E Bıyık, M Palan, NC Landolfi, DP Losey, D Sadigh. **"Asking Easy Questions: A User-Friendly Approach to Active Reward Learning"**. *3rd Conference on Robot Learning (CoRL)*, Osaka, Japan, Oct. 2019.

This code learns reward functions from human preferences in various tasks by actively generating queries to the human user based on maximum information gain. It also simulates maximum volume removal and random querying as baselines.

The codes for the physical Fetch robot is excluded, and only the simulation version is provided here.

## Dependencies
You need to have the following libraries with [Python3](http://www.python.org/downloads):
- [matplotlib](http://matplotlib.org/)
- [MuJoCo 2.0](http://www.mujoco.org/index.html)
- [NumPy](http://www.numpy.org/)
- [OpenAI Gym](http://gym.openai.com)
- [pyglet](http://bitbucket.org/pyglet/pyglet/wiki/Home)
- [SciPy](http://www.scipy.org/)
- [theano](http://deeplearning.net/software/theano/)

## Running
Throughout this demo,
- [task_name] should be selected as one of the following: LDS, Driver, Tosser, Fetch
- [criterion] should be selected as one of the following: information, volume, random
- [query_type] should be selected as one of the following: weak, strict
For the details and positive integer parameters epsilon, M, N; we refer to the publication.
You should run the codes in the following order:

### Sampling the input space
This is the preprocessing step, so you need to run it only once (subsequent runs will overwrite for each task). It is not interactive and necessary only if you will use discrete query database. If you want to try continuous optimization of queries instead, which may take too much time per query, please see the instructions in _volume_ and _information_ functions in _algos.py_. For continuous optimization, you can skip this step.

You simply run
```python
	python input_sampler.py [task_name] D
```
For quick (but highly suboptimal) results, we recommend D=1000. In the article, we used D=500000.

### Learning preference reward function
You can simply run
```python
	python run.py [task_name] [criterion] [query_type] epsilon M
```
where epsilon is the query-independent cost for optimal stopping, and M is the number of samples for Metropolis-Hastings. We recommend M=100. Setting epsilon=0 leads to infinitely many queries for the information gain formulation as information gain is always nonnegative.
After each query, the user will be showed the w-vector learned up to that point.

### Demonstration of learned parameters
This is just for demonstration purposes.

You simply run
```python
	python run_optimizer.py [task_name] k w
```
where k is the number of initial random points for the non-convex optimization, and w is the space-separated reward vector (it must have proper number of dimensions with respect to the environment: 6 for LDS; 4 for Driver, Tosser and Fetch).
