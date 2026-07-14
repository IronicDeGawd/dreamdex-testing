// SPDX-License-Identifier: MIT
pragma solidity 0.8.20;

// RoundTrip7702 — EIP-7702 delegate for the DreamDEX taker.
//
// The trading EOA sets a 7702 delegation to this contract, then calls
// roundTrip(...) ON ITSELF. Because the code runs in the EOA's context, the
// IOC buy and IOC sell both settle from the EOA's own funds in ONE transaction
// — no inter-leg drift, no bag left if the sell fails, and the round-trip toll
// is capped on-chain. The EOA stays msg.sender for placeOrder, so contest
// volume is still attributed to the wallet.
//
// SECURITY: roundTrip is locked to self-calls (msg.sender == address(this)).
// Only a transaction signed by the wallet's own key can satisfy that, so no one
// else can drive trades through a delegated wallet (the reference delegate we
// copied the pattern from omits this guard — a griefing hole we close).

interface IERC20 {
    function balanceOf(address) external view returns (uint256);
    function allowance(address, address) external view returns (uint256);
    function approve(address, uint256) external returns (bool);
}

interface IPool {
    function placeOrder(
        bool isBid, uint64 userData, uint256 price, uint256 quantity,
        uint64 expireNs, uint8 orderType, uint8 selfMatch,
        address builder, uint96 builderFee
    ) external payable returns (bool success, uint128 orderId);
}

contract RoundTrip7702 {
    // spentQuote = net quote lost across the round trip (the realized toll);
    // gotBase = base received on the buy; soldBase = base sent on the sell
    // (< gotBase means an IOC partial — the caller flattens the residual).
    event Trip(uint256 spentQuote, uint256 gotBase, uint256 soldBase);

    uint8 constant IOC = 2;   // orderType: immediateOrCancel
    uint8 constant NO_SELF_MATCH = 0;

    function roundTrip(
        address base,
        address quote,
        address pool,
        uint256 buyPrice,
        uint256 sellPrice,
        uint256 qty,
        uint64  expireNs,
        uint256 maxTollQuote,
        uint256 lot
    ) external {
        // Only the wallet itself (via a self-call) may trade. Blocks anyone
        // else from calling a delegated EOA to move its funds.
        require(msg.sender == address(this), "self");
        require(buyPrice > 0 && sellPrice > 0 && qty > 0 && lot > 0, "args");

        // MAX-approve the pool once (approve is absolute, not additive — keep it
        // wide so the per-order path never overwrites it to a thin allowance).
        if (IERC20(quote).allowance(address(this), pool) < buyPrice * qty) {
            IERC20(quote).approve(pool, type(uint256).max);
        }
        if (IERC20(base).allowance(address(this), pool) < qty) {
            IERC20(base).approve(pool, type(uint256).max);
        }

        uint256 q0 = IERC20(quote).balanceOf(address(this));
        uint256 b0 = IERC20(base).balanceOf(address(this));

        // BUY (IOC). Fills synchronously against the book within this tx, so the
        // base delta below is the actual fill.
        (bool okBuy,) = IPool(pool).placeOrder(
            true, 0, buyPrice, qty, expireNs, IOC, NO_SELF_MATCH, address(0), 0);
        require(okBuy, "buy");

        uint256 got = IERC20(base).balanceOf(address(this)) - b0;
        require(got > 0, "nofill");

        // SELL the received base, snapped down to a whole lot (an off-lot
        // quantity can revert or mis-fill). Dust below one lot is left in the
        // wallet and swept by the caller.
        uint256 sellQty = (got / lot) * lot;
        require(sellQty > 0, "dust");
        (bool okSell,) = IPool(pool).placeOrder(
            false, 0, sellPrice, sellQty, expireNs, IOC, NO_SELF_MATCH, address(0), 0);
        require(okSell, "sell");

        // Atomic cost ceiling: net quote lost on the trip must be within budget.
        // If a bad sell would breach it, the WHOLE trip reverts — no capital
        // bleeds (only gas is spent, which the caller avoids via eth_call first).
        uint256 qEnd = IERC20(quote).balanceOf(address(this));
        uint256 spent = q0 > qEnd ? q0 - qEnd : 0;
        require(spent <= maxTollQuote, "toll");

        emit Trip(spent, got, sellQty);
    }

    // Delegated EOAs route every incoming transfer through this code; without a
    // payable receiver, plain SOMI transfers to the wallet would revert.
    receive() external payable {}
}
