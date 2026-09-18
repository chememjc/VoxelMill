"""execution_limits must be enterable even when address-space or affinity APIs are absent."""
from voxelmill.contracts import ResourceBudget
from voxelmill.resources import execution_limits


def test_execution_limits_can_be_entered():
    with execution_limits(ResourceBudget(memory_gib=4, workers=1)):
        pass


def test_execution_limits_hard_memory_false_is_enterable():
    with execution_limits(ResourceBudget(memory_gib=1, workers=1), hard_memory=False):
        pass
