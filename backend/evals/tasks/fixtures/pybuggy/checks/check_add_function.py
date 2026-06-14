from pybuggy.math_tools import median_or_zero

assert median_or_zero([]) == 0.0
assert median_or_zero([3]) == 3
assert median_or_zero([3, 1, 2]) == 2
assert median_or_zero([4, 1, 2, 3]) == 2.5
