"""
Running the jobs of a design study several at a time.

A SimulationIterator evaluates the graph once per design point, each in its
own job_N directory. With max_workers > 1 those jobs run at the same time,
one process each: solve_parallel takes a batch of design points and returns
one result dict per point, in the order they were given.

The 'solver' here is a stand-in that sleeps, so the example runs anywhere,
with no solver installed. In a real study it would be a DynaAnalysis,
RadiossAnalysis or OpenFOAMAnalysis, and the design points would be
thicknesses, material parameters, or whatever is being varied.

Run it in a terminal to see the tqdm job bar (pip install kunene[progress]);
it needs example_actions.py next to it.
It writes its results into Study_1/ and Study_3/ in the current directory;
clean_start=True means a re-run starts from empty ones.
"""

import logging
logging.basicConfig(level=logging.WARNING)

import sys, time
from pathlib import Path

# Run against the kunene this example ships with, not another copy that
# happens to be installed in the environment.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kunene.actions import MathEvaluation
from kunene.graph_actions import WorkFlow
from kunene.simulation_iterator import SimulationIterator

# The stand-in solver action lives in a module, not in this script: a job
# running in a child process on Windows has to be able to import it.
from example_actions import SleepySolver

JOB_SECONDS = SleepySolver.seconds     # how long a job pretends to run


def make_iterator(max_workers):
    wf = WorkFlow(f"Study_{max_workers}")
    wf.add_action(SleepySolver('solver'))

    # A result computed from the solver's output, as in a real workflow.
    wf.add_action(MathEvaluation('margin', 'solver - 3.0'))

    return SimulationIterator(wf, max_workers=max_workers, clean_start=True)


def run_example():

    design_points = [{'K': k} for k in (4.0, 9.0, 16.0, 25.0, 36.0, 49.0)]

    print(f"{len(design_points)} design points, {JOB_SECONDS} s per job\n")

    # One job at a time -- what max_workers=1 (the default) does.
    itr = make_iterator(max_workers=1)
    start = time.time()
    serial = itr.solve_parallel(design_points)
    serial_time = time.time() - start
    print(f"max_workers=1: {serial_time:5.1f} s")

    # Three jobs at a time. In a terminal this reports itself as a job bar:
    #   Study_3_Iter:  50% 3/6 [00:02<00:02, job_3, job_4, job_5]
    itr = make_iterator(max_workers=3)
    start = time.time()
    parallel = itr.solve_parallel(design_points, groups='sweep')
    parallel_time = time.time() - start
    print(f"max_workers=3: {parallel_time:5.1f} s\n")

    # Same results, in the order the design points were given.
    for vals, out in zip(design_points, parallel):
        print(f"  {vals} -> solver={out['solver']:.1f}, margin={out['margin']:.1f}")

    assert [e['margin'] for e in parallel] == [e['margin'] for e in serial]
    print(f"\nSame results as the serial run, {serial_time/parallel_time:.1f}x faster.")

    # Each job kept its own directory, and the index knows what was run where.
    print("\nResults directory:")
    for job_dir in sorted(Path(itr.work_area_path).glob('job_*')):
        variables = itr.variables_of(job_dir.name)
        print(f"  {job_dir.name}/  {variables}  {sorted(f.name for f in job_dir.iterdir())}")

    # Past results are found by variable value, without running the graph.
    print("\nresults_for({'K': 25.0}):", itr.results_for({'K': 25.0})['margin'])


run_example()
