from pybuggy.cycle_a import combined
from pybuggy.cycle_b import reverse_combined

assert combined() == "AB"
assert reverse_combined() == "BA"
