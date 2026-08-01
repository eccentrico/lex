import os
from pathlib import Path

import pytest


@pytest.fixture
def lex_home_tmp():
    return Path(os.environ["LEX_HOME"])
