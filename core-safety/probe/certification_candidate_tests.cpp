// Copyright (c) 2026, the ElectrumX-RVN community maintainers
//
// The MIT License (MIT).  See LICENCE for details.
//
// Candidate-specific probe for the RavenProject/Ravencoin certification
// pipeline (core-safety/scripts/certify_core.py).  Every case here calls a
// real, non-static, production entry point compiled from the exact candidate
// commit under test: ProcessNewBlockHeaders() (which internally invokes the
// static ContextualCheckBlockHeader()) and ContextualCheckTransferAsset().
// Nothing here reimplements consensus logic; it only assembles inputs and
// reads the CValidationState the candidate itself produced.

#include <test/test_raven.h>

#include <assets/assets.h>
#include <assets/assettypes.h>
#include <chainparams.h>
#include <chainparamsbase.h>
#include <chain.h>
#include <consensus/validation.h>
#include <pow.h>
#include <primitives/block.h>
#include <timedata.h>
#include <util.h>
#include <utiltime.h>
#include <validation.h>

#include <boost/test/unit_test.hpp>

#include <limits>

// Mine a header in place by searching nNonce64 until the candidate's own
// CheckProofOfWork() accepts it under the exact target the candidate itself
// computed. This is real mining against the real KAWPOW implementation
// shipped in this source tree (crypto/ethash), not a reimplementation of the
// rule under test (ContextualCheckBlockHeader). Every header built by this
// probe carries nTime at or after nKAWPOWActivationTime, so GetHashFull()
// always takes the KAWPOW branch (primitives/block.cpp), where nNonce64 (not
// the legacy nNonce) is part of the hash preimage; incrementing nNonce64
// alone is therefore sufficient here. The iteration cap turns a broken mining
// precondition into a reported FAIL instead of a hung test process.
static void MineHeader(CBlockHeader &header, const Consensus::Params &params)
{
    uint256 mix_hash;
    for (uint64_t attempt = 0; attempt < (UINT64_C(1) << 22); ++attempt) {
        if (CheckProofOfWork(header.GetHashFull(mix_hash), header.nBits, params)) {
            header.mix_hash = mix_hash;
            return;
        }
        ++header.nNonce64;
    }
    BOOST_FAIL("MineHeader: exhausted nonce search without finding valid proof of work");
}

BOOST_FIXTURE_TEST_SUITE(certification_candidate_tests, TestChain100Setup)

// Backs both nheight-binding-rejects-forged and post-boundary-valid-accepted
// in the certification profile: certify_core.py runs this single case for
// both test IDs and expects both properties to hold simultaneously.
BOOST_AUTO_TEST_CASE(certification_candidate_contextual_height_test)
{
    const CChainParams &chainparams = GetParams();
    const Consensus::Params &consensus = chainparams.GetConsensus();

    // nKAWPOWActivationTime on regtest is fixed far in the future so the
    // KAWPOW header format (and the declared-height field it carries) is
    // opt-in for tests. Move the node's clock past it so every header built
    // here is serialized and validated in the post-KAWPOW shape, exactly
    // like the real incident headers on mainnet. Every header below keeps
    // nTime at or after this activation time so none of them silently fall
    // back to the legacy pre-KAWPOW serialization/hash path.
    const int64_t activationTime = (int64_t)nKAWPOWActivationTime;
    SetMockTime(activationTime + 3600);

    const CBlockIndex *tip = chainActive.Tip();
    BOOST_REQUIRE(tip != nullptr);
    const int nextHeight = tip->nHeight + 1;

    auto buildHeader = [&](uint32_t declaredHeight, uint32_t timeOffset) {
        CBlockHeader header;
        header.nVersion = 4 | (1 << 28); // VERSIONBITS_TOP_BITS_ASSETS-compatible top bits
        header.hashPrevBlock = tip->GetBlockHash();
        header.hashMerkleRoot = GetRandHash(); // not checked by header-only validation
        header.nTime = (uint32_t)GetAdjustedTime() + timeOffset;
        header.nBits = GetNextWorkRequired(tip, &header, consensus);
        header.nHeight = declaredHeight;
        header.nNonce64 = 0;
        MineHeader(header, consensus);
        return header;
    };

    // --- Case 1: forged declared height must be rejected ---------------
    // The declared height is mined into the header (it feeds the KAWPOW
    // epoch and hash preimage), so this is a self-consistent forged block:
    // valid proof of work, wrong claimed height. Exactly the incident class.
    CBlockHeader forged = buildHeader((uint32_t)nextHeight + 1000, 0);
    {
        CValidationState state;
        std::vector<CBlockHeader> headers{forged};
        bool accepted = ProcessNewBlockHeaders(headers, state, chainparams);
        BOOST_CHECK(!accepted);
        BOOST_CHECK_EQUAL(state.GetRejectReason(), "bad-blk-height");
    }

    // Negative control: a header rejected for an unrelated reason must not
    // produce the same token, or "rejected" would not discriminate. This
    // control keeps the honest declared height and instead pushes nTime past
    // MAX_FUTURE_BLOCK_TIME (chain.h), which stays on the KAWPOW-format side
    // of nKAWPOWActivationTime (unlike reusing tip->nTime, which predates
    // regtest's activation timestamp and would silently drop into the legacy
    // pre-KAWPOW header shape that carries no declared-height field at all).
    {
        CBlockHeader badTime = buildHeader((uint32_t)nextHeight, (uint32_t)(3 * 60 * 60));
        CValidationState state;
        std::vector<CBlockHeader> headers{badTime};
        bool accepted = ProcessNewBlockHeaders(headers, state, chainparams);
        BOOST_CHECK(!accepted);
        BOOST_CHECK_EQUAL(state.GetRejectReason(), "time-too-new");
        BOOST_CHECK(state.GetRejectReason() != "bad-blk-height");
    }

    // --- Case 2: honest declared height at/after the boundary is accepted
    // Regtest's nHeightHeaderCheckActivation is 0, so enforcement is active
    // from genesis; mainnet's own compiled-in activation height is asserted
    // separately in certification_candidate_checkpoint_data_test below. This
    // regtest probe demonstrates the accept path once enforcement is active;
    // it does not by itself exercise mainnet's specific activation height.
    BOOST_REQUIRE_EQUAL(consensus.nHeightHeaderCheckActivation, 0);
    CBlockHeader honest = buildHeader((uint32_t)nextHeight, 0);
    {
        CValidationState state;
        std::vector<CBlockHeader> headers{honest};
        bool accepted = ProcessNewBlockHeaders(headers, state, chainparams);
        BOOST_CHECK(accepted);
        BOOST_CHECK(state.IsValid());
    }

    // One more honest header one block further out, to demonstrate the
    // accept path holds beyond the immediate next height too ("after").
    {
        CBlockHeader honestAfter = buildHeader((uint32_t)nextHeight + 1, 60);
        honestAfter.hashPrevBlock = honest.GetHash();
        honestAfter.nBits = GetNextWorkRequired(tip, &honestAfter, consensus);
        honestAfter.nNonce64 = 0;
        MineHeader(honestAfter, consensus);
        CValidationState state;
        std::vector<CBlockHeader> headers{honestAfter};
        bool accepted = ProcessNewBlockHeaders(headers, state, chainparams);
        BOOST_CHECK(accepted);
        BOOST_CHECK(state.IsValid());
    }

    SetMockTime(0);
}

// Backs incident-checkpoint-hash: the candidate's own compiled-in mainnet
// checkpoint data, not a live chain, is the evidence (deployment presence on
// a synchronized node is a separate, already-flagged live-node gate). Also
// asserts mainnet's own compiled-in height-check activation height, since the
// regtest probe above runs with enforcement active from genesis and cannot by
// itself demonstrate the mainnet boundary.
BOOST_FIXTURE_TEST_CASE(certification_candidate_checkpoint_data_test, BasicTestingSetup)
{
    SelectParams(CBaseChainParams::MAIN);
    BOOST_CHECK_EQUAL(GetParams().GetConsensus().nHeightHeaderCheckActivation, 4487776);
    const CCheckpointData &checkpoints = GetParams().Checkpoints();
    auto it = checkpoints.mapCheckpoints.find(4487775);
    BOOST_REQUIRE(it != checkpoints.mapCheckpoints.end());
    BOOST_CHECK_EQUAL(
        it->second.GetHex(),
        "000000000002d64509e06e76ddbbe418c725291687ec62b41ecfc40386a091fd");
    SelectParams(CBaseChainParams::MAIN); // restore for any later fixtures in this run
}

// Backs transfer-overflow-deployment: calls the real consensus entry point
// ContextualCheckTransferAsset() with a canonical overflow fixture (an
// amount that wrapped to negative via unsigned-to-signed overflow, the same
// integer-overflow bug class the release gate exists to catch). Deployment
// ACTIVE state on mainnet is a separate, already-flagged runtime gate; this
// proves the candidate's own validation function rejects the fixture. The
// asset name below contains '.' (a sub-asset separator) so IsAssetNameValid
// classifies it as neither RESTRICTED ('$'-prefixed) nor QUALIFIER
// ('#'-prefixed); those are the only branches in ContextualCheckTransferAsset
// that dereference assetCache, so passing nullptr here is safe for both
// calls below and does not depend on cache/database state.
BOOST_AUTO_TEST_CASE(certification_candidate_transfer_overflow_test)
{
    // A CAmount (int64_t) constructed from a value that overflowed during
    // deserialization wraps around to a negative number. This is the
    // canonical overflow fixture: a value that is arithmetically "huge" but
    // observed by consensus code as negative.
    const CAmount overflowed = (CAmount)std::numeric_limits<uint64_t>::max();
    BOOST_REQUIRE(overflowed < 0);

    CAssetTransfer transfer("CERTIFY.OVERFLOW", overflowed);
    std::string strError;
    bool accepted = ContextualCheckTransferAsset(nullptr, transfer,
                                                  "mtNN7XeVUbKAdBiK6uxJ3fewsy6WPBqmc9",
                                                  strError);
    BOOST_CHECK(!accepted);
    BOOST_CHECK(!strError.empty());

    // Negative control: the same asset name and a canonical positive amount
    // must be accepted by this function (no cache/existence checks apply to
    // a non-restricted, non-qualifier asset with assetCache == nullptr), and
    // in particular must not be rejected for the same reason as the overflow
    // case above.
    CAssetTransfer sane("CERTIFY.OVERFLOW", (CAmount)1 * COIN);
    std::string saneError;
    bool saneAccepted = ContextualCheckTransferAsset(nullptr, sane,
                                                       "mtNN7XeVUbKAdBiK6uxJ3fewsy6WPBqmc9",
                                                       saneError);
    BOOST_CHECK(saneAccepted);
    BOOST_CHECK(saneError != strError);
}

BOOST_AUTO_TEST_SUITE_END()
