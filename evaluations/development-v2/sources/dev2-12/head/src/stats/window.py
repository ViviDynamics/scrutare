"""A non-empty sample window. Construction rejects empty input."""


class Window:
    def __init__(self, samples):
        samples = tuple(samples)
        if not samples:
            raise ValueError("window requires at least one sample")
        self._samples = samples

    @property
    def total(self):
        return len(self._samples)

    @property
    def hits(self):
        return sum(1 for sample in self._samples if sample)
