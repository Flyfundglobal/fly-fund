// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {FlyFeeding} from "./FlyFeeding.sol";

interface IV2Router {
    function swapExactTokensForTokensSupportingFeeOnTransferTokens(
        uint256 amountIn,
        uint256 amountOutMin,
        address[] calldata path,
        address to,
        uint256 deadline
    ) external;
}

interface IWrappedNative is IERC20 {
    function deposit() external payable;
}

/// @notice A contribution vault with a fixed, constrained trading key. Not a share or yield token.
/// @dev No owner, proxy, upgrade, arbitrary call, withdrawal, rescue, or mutable allowlist.
/// The executor is an off-chain signer; this contract does NOT attest to AI decision provenance.
contract FlyFund is FlyFeeding {
    using SafeERC20 for IERC20;

    uint256 public constant BPS = 10_000;
    uint256 public constant MAX_DEADLINE_DELAY = 10 minutes;
    uint256 public constant MAX_EXTRA_ASSETS = 14;

    address public immutable executor;
    IV2Router public immutable router;
    IWrappedNative public immutable wrappedNative;
    uint256 public immutable maxTradeBps;
    uint256 public immutable dailySpendBps;
    mapping(address asset => bool allowed) public allowedAsset;
    mapping(bytes32 decisionHash => bool executed) public executedDecision;

    struct DailyBudget {
        uint256 day;
        uint256 openingBalance;
        uint256 spent;
    }
    mapping(address asset => DailyBudget budget) public dailyBudget;

    error InvalidConfiguration();
    error NotExecutor();
    error InvalidPath();
    error AssetNotAllowed(address asset);
    error InvalidDeadline();
    error MissingDecisionHash();
    error DecisionAlreadyExecuted();
    error TradeLimitExceeded(uint256 amount, uint256 limit);
    error DailyLimitExceeded(uint256 amount, uint256 remaining);
    error UnexpectedInputSpent(uint256 spent, uint256 expected);
    error InvalidOutputReceived();
    error NativeWrapFailed();

    event NativeReceived(address indexed sender, uint256 amount);
    event NativeWrapped(uint256 amount);
    event AssetAllowed(address indexed asset);
    event Swapped(
        bytes32 indexed decisionHash,
        address indexed tokenIn,
        address indexed tokenOut,
        uint256 amountIn,
        uint256 amountOut,
        address[] path
    );

    /// @param extraAssets Additional fixed BEP20 assets, e.g. verified BNCB. FLY/WBNB are included automatically.
    /// @param tradeBps Maximum input as a fraction of the current input-token balance per trade.
    /// @param dayBps Daily cumulative input cap, based on balance before the first trade of that UTC day.
    constructor(
        address flyToken,
        address executorAddress,
        address routerAddress,
        address wrappedNativeAddress,
        address[] memory extraAssets,
        uint256 tradeBps,
        uint256 dayBps
    ) FlyFeeding(flyToken) {
        if (
            executorAddress == address(0) || executorAddress == address(this)
                || routerAddress.code.length == 0 || wrappedNativeAddress.code.length == 0
                || routerAddress == address(this) || wrappedNativeAddress == flyToken
                || tradeBps == 0 || tradeBps > BPS || dayBps < tradeBps || dayBps > BPS
                || extraAssets.length > MAX_EXTRA_ASSETS
        ) revert InvalidConfiguration();
        executor = executorAddress;
        router = IV2Router(routerAddress);
        wrappedNative = IWrappedNative(wrappedNativeAddress);
        maxTradeBps = tradeBps;
        dailySpendBps = dayBps;
        _allow(flyToken);
        _allow(wrappedNativeAddress);
        for (uint256 i; i < extraAssets.length; ++i) _allow(extraAssets[i]);
    }

    /// @notice Accept BNB donations/revenue. They do not count as FLY feedings.
    receive() external payable {
        emit NativeReceived(msg.sender, msg.value);
    }

    /// @notice Anyone may wrap the vault's BNB into its fixed WBNB, always held by this vault.
    /// @dev Does not send BNB to an arbitrary recipient or grant the caller any claim.
    function wrapBNB() external nonReentrant returns (uint256 amount) {
        amount = address(this).balance;
        if (amount == 0) revert InvalidAmount();
        uint256 beforeBalance = wrappedNative.balanceOf(address(this));
        wrappedNative.deposit{value: amount}();
        if (wrappedNative.balanceOf(address(this)) != beforeBalance + amount) revert NativeWrapFailed();
        emit NativeWrapped(amount);
    }

    /// @notice Exchange allowed BEP20 assets through the fixed V2 router. Proceeds cannot leave the vault.
    /// @dev minAmountOut is chosen off-chain. This is NOT an oracle-based fair-price guarantee.
    /// Fee-on-transfer tokens are accounted by balance deltas; rebasing/reflection tokens are unsupported.
    function swapExactTokens(
        uint256 amountIn,
        uint256 minAmountOut,
        address[] calldata path,
        uint256 deadline,
        bytes32 decisionHash
    ) external nonReentrant returns (uint256 amountOut) {
        if (msg.sender != executor) revert NotExecutor();
        if (amountIn == 0 || minAmountOut == 0) revert InvalidAmount();
        if (decisionHash == bytes32(0)) revert MissingDecisionHash();
        if (executedDecision[decisionHash]) revert DecisionAlreadyExecuted();
        if (deadline < block.timestamp || deadline > block.timestamp + MAX_DEADLINE_DELAY) {
            revert InvalidDeadline();
        }
        _validatePath(path);
        executedDecision[decisionHash] = true;
        amountOut = _exchange(amountIn, minAmountOut, path, deadline);
        emit Swapped(decisionHash, path[0], path[path.length - 1], amountIn, amountOut, path);
    }

    function _exchange(uint256 amountIn, uint256 minAmountOut, address[] calldata path, uint256 deadline)
        private
        returns (uint256 amountOut)
    {
        IERC20 tokenIn = IERC20(path[0]);
        IERC20 tokenOut = IERC20(path[path.length - 1]);
        uint256 beforeIn = tokenIn.balanceOf(address(this));
        uint256 beforeOut = tokenOut.balanceOf(address(this));
        _consumeBudget(address(tokenIn), amountIn, beforeIn);

        tokenIn.forceApprove(address(router), amountIn);
        router.swapExactTokensForTokensSupportingFeeOnTransferTokens(
            amountIn, minAmountOut, path, address(this), deadline
        );
        tokenIn.forceApprove(address(router), 0);

        uint256 afterIn = tokenIn.balanceOf(address(this));
        if (afterIn > beforeIn) revert UnexpectedInputSpent(0, amountIn);
        if (beforeIn - afterIn != amountIn) revert UnexpectedInputSpent(beforeIn - afterIn, amountIn);
        uint256 afterOut = tokenOut.balanceOf(address(this));
        if (afterOut <= beforeOut) revert InvalidOutputReceived();
        amountOut = afterOut - beforeOut;
        if (amountOut < minAmountOut) revert BelowMinimumReceived(amountOut, minAmountOut);
    }

    function _allow(address asset) private {
        if (asset.code.length == 0 || asset == address(this) || allowedAsset[asset]) {
            revert InvalidConfiguration();
        }
        allowedAsset[asset] = true;
        emit AssetAllowed(asset);
    }

    function _validatePath(address[] calldata path) private view {
        if (path.length < 2 || path.length > 3) revert InvalidPath();
        for (uint256 i; i < path.length; ++i) {
            if (!allowedAsset[path[i]]) revert AssetNotAllowed(path[i]);
            for (uint256 j; j < i; ++j) if (path[i] == path[j]) revert InvalidPath();
        }
    }

    function _consumeBudget(address asset, uint256 amount, uint256 balance) private {
        uint256 tradeLimit = _fraction(balance, maxTradeBps);
        if (amount > tradeLimit) revert TradeLimitExceeded(amount, tradeLimit);
        DailyBudget storage budget = dailyBudget[asset];
        uint256 day = block.timestamp / 1 days;
        if (budget.day != day || budget.openingBalance == 0) {
            budget.day = day;
            budget.openingBalance = balance;
            budget.spent = 0;
        }
        uint256 remaining = _fraction(budget.openingBalance, dailySpendBps) - budget.spent;
        if (amount > remaining) revert DailyLimitExceeded(amount, remaining);
        budget.spent += amount;
    }

    /// @dev Equivalent to amount * bps / 10_000 without overflowing intermediate multiplication.
    function _fraction(uint256 amount, uint256 bps) private pure returns (uint256) {
        return (amount / BPS) * bps + ((amount % BPS) * bps) / BPS;
    }
}
