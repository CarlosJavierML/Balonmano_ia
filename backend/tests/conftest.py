import os
import tempfile

# Point the app at a throwaway database before anything imports app.config,
# so tests never read or pollute the real data/balonmano.db.
_tmp_dir = tempfile.mkdtemp(prefix="balonmano-tests-")
os.environ.setdefault("BALONMANO_DATABASE_URL", f"sqlite+aiosqlite:///{_tmp_dir}/test.db")
