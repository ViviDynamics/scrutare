from src.app import can_delete

assert not can_delete('nonadmin,viewer')
