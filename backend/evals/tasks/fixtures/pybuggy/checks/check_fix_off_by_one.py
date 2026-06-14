from pybuggy.stats import adjacent_pairs

assert adjacent_pairs([1, 2, 3, 4]) == [(1, 2), (2, 3), (3, 4)]
assert adjacent_pairs([1, 2]) == [(1, 2)]
