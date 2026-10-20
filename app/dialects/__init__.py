"""SQL dialect registration for SQLVIP.

Importing this package registers the custom ``db2`` and ``dameng`` dialects
with SQLGlot. Keep the imports here so every conversion path gets them.
"""

from app.dialects.db2 import DB2
from app.dialects.oracle import Oracle
from app.dialects.dameng import Dameng

__all__ = ["DB2", "Oracle", "Dameng"]
