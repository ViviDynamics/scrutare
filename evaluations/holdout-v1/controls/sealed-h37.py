from src.app import materialize
from src.contracts import stream

assert materialize(stream()) == [0, 1, 2]
