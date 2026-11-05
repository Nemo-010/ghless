"""ghre: ledger-first recovery for suspended or deleted GitHub accounts.

The tool is pointed at an account name and nothing else. It enumerates every
thing the account ever exposed, records it in a ledger, and only then tries to
recover bodies. Recovery is a separate phase because what you can recover is
decided by what the ledger proves existed.
"""

__version__ = "1.2.1"
