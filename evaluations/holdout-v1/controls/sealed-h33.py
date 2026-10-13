from src.app import description
from src.contracts import render

assert render(description({})) == ''
