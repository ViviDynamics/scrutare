from src.app import allowed

assert not allowed('https://login.example.invalid.attacker.invalid/')
