// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/// @notice Shared feeding/accounting logic. Feedings are contributions, not deposits redeemable for shares.
/// @dev The concrete vault must define its custody policy before deployment.
abstract contract FlyFeeding is ReentrancyGuard {
    using SafeERC20 for IERC20;

    uint256 public constant MAX_MESSAGE_BYTES = 1024;
    IERC20 public immutable fly;
    uint256 public totalFeeds;
    uint256 public totalFed;
    mapping(address feeder => uint256 amount) public fedBy;

    error InvalidToken();
    error InvalidAmount();
    error InvalidMessageLength(uint256 length);
    error InvalidReceivedAmount(uint256 received);
    error BelowMinimumReceived(uint256 received, uint256 minimum);

    /// @dev Text is public transaction/event data. Index by chainId, contract, transactionHash and logIndex.
    event Fed(
        uint256 indexed feedId,
        address indexed feeder,
        bytes32 indexed messageHash,
        uint256 requestedAmount,
        uint256 receivedAmount,
        string message
    );

    constructor(address flyToken) {
        if (flyToken.code.length == 0 || flyToken == address(this)) revert InvalidToken();
        fly = IERC20(flyToken);
    }

    /// @param amount Amount in token base units, not a human-readable decimal amount.
    /// @param minReceived User's lower bound after any token transfer tax; must be nonzero.
    /// @param message Public UTF-8 content; limit is bytes, not characters. No private data.
    /// @return feedId Monotonically increasing successful feeding ID, starting at one.
    /// @return received Actual net tokens received by the vault.
    /// @dev Only msg.sender pays/receives credit. Direct ERC20 transfers are not feedings.
    function feed(uint256 amount, uint256 minReceived, string calldata message)
        external
        nonReentrant
        returns (uint256 feedId, uint256 received)
    {
        if (amount == 0 || minReceived == 0 || minReceived > amount) revert InvalidAmount();
        uint256 length = bytes(message).length;
        if (length == 0 || length > MAX_MESSAGE_BYTES) revert InvalidMessageLength(length);

        uint256 beforeBalance = fly.balanceOf(address(this));
        fly.safeTransferFrom(msg.sender, address(this), amount);
        uint256 afterBalance = fly.balanceOf(address(this));
        if (afterBalance <= beforeBalance) revert InvalidReceivedAmount(0);
        received = afterBalance - beforeBalance;
        // Reflection/rebasing during a transfer is deliberately unsupported.
        if (received > amount) revert InvalidReceivedAmount(received);
        if (received < minReceived) revert BelowMinimumReceived(received, minReceived);

        feedId = ++totalFeeds;
        totalFed += received;
        fedBy[msg.sender] += received;
        emit Fed(feedId, msg.sender, keccak256(bytes(message)), amount, received, message);
    }
}
