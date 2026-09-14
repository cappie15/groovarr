"""Shared pytest fixtures.

Sets required/test-friendly environment variables *before* anything imports
`app.core.config` or `app.db.session`, since `Settings` requires
`GROOVARR_SECRET_KEY` and `get_engine()`/`get_settings()` are `lru_cache`d
process-wide singletons.
"""

import os
import tempfile

_tmp_config_dir = tempfile.mkdtemp(prefix="groovarr-test-config-")
os.environ.setdefault("GROOVARR_SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("CONFIG_DIR", _tmp_config_dir)
