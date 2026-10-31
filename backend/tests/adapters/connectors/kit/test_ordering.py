import random

from linersync.adapters.connectors.kit.ordering import lis_indexes, occurrence_labels


def test_occurrence_labels_count_copies() -> None:
    assert occurrence_labels(["a", "b", "a"]) == [("a", 0), ("b", 0), ("a", 1)]


def test_lis_indexes_is_increasing_and_longest() -> None:
    rng = random.Random(7)
    for _ in range(200):
        seq = rng.sample(range(40), rng.randint(0, 40))
        found = lis_indexes(seq)
        values = [seq[i] for i in found]
        assert values == sorted(values) and len(set(values)) == len(values)
        assert found == sorted(found)
        best = [1] * len(seq)  # O(n^2) reference
        for i in range(len(seq)):
            for j in range(i):
                if seq[j] < seq[i]:
                    best[i] = max(best[i], best[j] + 1)
        assert len(found) == max(best, default=0)
