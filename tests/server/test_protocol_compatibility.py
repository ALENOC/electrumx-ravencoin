# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.

'''Regression tests: server.ravencoin_backend and other Ravencoin-specific
capabilities must be purely additive.  Legacy Electrum clients speaking the
oldest supported protocol tuple must negotiate normally and must never be
required to know about, or be blocked by, Ravencoin-only extensions.
'''

from unittest.mock import Mock

from electrumx.server.session import ElectrumX


class _AnyMethodStub:
    '''Stands in for a session object exposing every possible RPC handler
    as a Mock, without needing a fully constructed ElectrumX instance.'''

    def __getattr__(self, name):
        return Mock(name=name)


def _handlers_for(ptuple):
    stub = _AnyMethodStub()
    ElectrumX.set_request_handlers(stub, ptuple)
    return stub.request_handlers


def test_legacy_protocol_min_still_exposes_core_electrum_handlers():
    handlers = _handlers_for(ElectrumX.PROTOCOL_MIN)
    for method in (
        'server.version',
        'server.features',
        'server.ping',
        'server.banner',
        'server.peers.subscribe',
        'blockchain.scripthash.subscribe',
        'blockchain.scripthash.get_balance',
        'blockchain.transaction.broadcast',
        'blockchain.transaction.get',
        'blockchain.headers.subscribe',
    ):
        assert method in handlers, f'{method} missing at PROTOCOL_MIN'


def test_ravencoin_backend_capability_is_additive_not_gated_by_protocol():
    min_handlers = _handlers_for(ElectrumX.PROTOCOL_MIN)
    max_handlers = _handlers_for(ElectrumX.PROTOCOL_MAX)

    # A legacy (1, 4) client is never required to know about
    # server.ravencoin_backend, but it is still present and callable if the
    # client chooses to probe it - the extension does not depend on
    # negotiating a newer protocol tuple.
    assert 'server.ravencoin_backend' in min_handlers
    assert 'server.ravencoin_backend' in max_handlers


def test_handler_set_identical_across_supported_protocol_range():
    # The handler table is not filtered by negotiated protocol tuple, so no
    # capability - Ravencoin-specific or otherwise - can silently disappear
    # for legacy clients nor be withheld from clients that negotiated an
    # older tuple. This guards against a future regression that starts
    # gating handlers by ptuple and inadvertently drops legacy coverage.
    assert set(_handlers_for(ElectrumX.PROTOCOL_MIN)) == \
        set(_handlers_for(ElectrumX.PROTOCOL_MAX))


def test_protocol_min_is_still_1_4():
    # Locks in the documented legacy floor referenced throughout the
    # Ravencoin-specific compatibility guarantees; bumping PROTOCOL_MIN is a
    # breaking change and must be a deliberate, reviewed decision.
    assert ElectrumX.PROTOCOL_MIN == (1, 4)
