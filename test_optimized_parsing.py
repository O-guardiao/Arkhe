
import sys
import pytest
from rlm.core import fast
from rlm.utils import parsing

# Monkey patch parsing module to use optimized functions
parsing.find_code_blocks = fast.find_code_blocks
parsing.find_final_answer = fast.find_final_answer

# Import existing tests
from tests.test_parsing import *

if __name__ == "__main__":
    sys.exit(pytest.main(["-v", __file__]))
