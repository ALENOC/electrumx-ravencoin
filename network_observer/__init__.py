# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""Public Ravencoin Electrum server discovery, health and classification.

Discovery is not trust.  Everything this package learns from the network is a
candidate until it has been validated, and validation always ends with chain
comparison rather than with a server's own claims.
"""

__version__ = "1.0.0"
