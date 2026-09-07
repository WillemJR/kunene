"""
Discover graph inputs and outputs using parameters() and outputs().

Before running a graph it can be useful to inspect what variables it
expects as input and what results it will produce.  This example builds
a small workflow with Variable-parameterised actions, then calls:

  graph.parameters() -> set of Variable objects (inputs)
  graph.outputs()   -> dict of {action_name: (data_type, description)}

No simulation solver is required; the graph uses MathEvaluation actions
so the example can be run standalone.
"""

from kunene.variables import FloatVariable, IntSetVariable
from kunene.actions import MathEvaluation, WorkAction
from kunene.graph_actions import WorkFlow, DirectedGraph


# ---------------------------------------------------------------------------
# A simple custom action that uses Variable arguments
# ---------------------------------------------------------------------------

class ScaledOffset(WorkAction):
    """Computes  result = scale * value + offset."""

    @WorkAction.allow_variables_as_arguments
    def __init__(self, name, value=None, scale=1.0, offset=0.0):
        super().__init__(name)
        self.value = value
        self.scale = scale
        self.offset = offset
        self.description = "Scaled and offset value: scale * value + offset"
        self.data_type = float

    @WorkAction.assign_variables_values_to_members
    def solve(self, val_dict=None):
        return self.scale * self.value + self.offset


# ---------------------------------------------------------------------------
# Define Variables (graph inputs)
# ---------------------------------------------------------------------------

stiffness  = FloatVariable("K",  200.0, lower_bound=10.0, upper_bound=1000.0,
                            description="Spring stiffness [N/m]")
damping    = FloatVariable("C",    5.0, lower_bound=0.0,  upper_bound=50.0,
                            description="Damping coefficient [Ns/m]")
load_steps = IntSetVariable("N_steps", 10, allowable={5, 10, 20, 50},
                             description="Number of load steps")
scale_factor = FloatVariable("alpha", 0.5, description="Output scale factor")

# ---------------------------------------------------------------------------
# Build a WorkFlow using those variables
# ---------------------------------------------------------------------------

wf = WorkFlow("spring_analysis")

# Action 1: scale stiffness by a Variable scale factor
stiffness_step = ScaledOffset(
    "stiffness_per_step",
    value=stiffness,
    scale=scale_factor,
    offset=0.0,
)
wf.add_action(stiffness_step)

# Action 2: combine stiffness and damping into a simple scalar metric.
#           Uses the result of the previous action via its name in val_dict.
metric = MathEvaluation(
    "quality_metric",
    cmd = "stiffness_per_step / (C + 1.0)",
    description = "Stiffness-to-damping quality metric",
    data_type = float )
wf.add_action(metric)

# Action 3: another Variable-parameterised step using N_steps
response = ScaledOffset(
    "response",
    value=damping,
    scale=load_steps,   # IntSetVariable used as a scale
    offset=0.0,
)
wf.add_action(response)

# ---------------------------------------------------------------------------
# Discover: parameters() — what inputs does the graph need?
# ---------------------------------------------------------------------------

print("=" * 60)
print("Graph inputs  (variables)")
print("=" * 60)
variables = wf.parameters()
for v in sorted(variables, key=lambda x: x.name):
    type_name = v.type.__name__ if (v.type and hasattr(v.type, "__name__")) else str(v.type)
    print(f"  {v.name:15s}  type={type_name:6s}  default={v._value}  — {v.description}")

# ---------------------------------------------------------------------------
# Discover: outputs() — what results will the graph produce?
# ---------------------------------------------------------------------------

print()
print("=" * 60)
print("Graph outputs  (outputs)")
print("=" * 60)
outputs = wf.outputs()
for action_name, (data_type, description) in outputs.items():
    type_name = data_type.__name__ if (data_type and hasattr(data_type, "__name__")) else str(data_type)
    print(f"  {action_name:25s}  type={type_name:6s}  — {description}")

# ---------------------------------------------------------------------------
# Optional: actually run the graph to verify the results match the metadata
# ---------------------------------------------------------------------------

print()
print("=" * 60)
print("Running the graph")
print("=" * 60)
val_dict = {v.name: v._value for v in variables}
input_keys = set(val_dict)
print(f"  Input values: {val_dict}")
results = wf.solve(val_dict)
print("  Results:")
for k, v in results.items():
    if k not in input_keys:     # print only computed outputs
        print(f"    {k} = {v}")
