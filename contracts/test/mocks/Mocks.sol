// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";

contract MockToken is ERC20 {
    uint256 public taxBps;
    bool public falseReturn;
    bool public bonus;
    address public callbackTarget;
    bytes public callbackData;
    bool public callbackSucceeded;
    bytes public callbackResult;

    constructor() ERC20("Test token", "TEST") {}
    function mint(address to, uint256 amount) external { _mint(to, amount); }
    function setTax(uint256 bps) external { require(bps <= 10_000); taxBps = bps; }
    function setFalseReturn(bool value) external { falseReturn = value; }
    function setBonus(bool value) external { bonus = value; }
    function setCallback(address target, bytes calldata data) external { callbackTarget = target; callbackData = data; }

    function transferFrom(address from, address to, uint256 amount) public override returns (bool) {
        if (falseReturn) return false;
        if (callbackTarget != address(0)) {
            (callbackSucceeded, callbackResult) = callbackTarget.call(callbackData);
        }
        bool result = super.transferFrom(from, to, amount);
        if (bonus) _mint(to, 1);
        return result;
    }
    function _update(address from, address to, uint256 amount) internal override {
        uint256 tax = from != address(0) && to != address(0) ? amount * taxBps / 10_000 : 0;
        if (tax > 0) super._update(from, address(0), tax);
        super._update(from, to, amount - tax);
    }
}

contract MockWrapped is MockToken {
    function deposit() external payable { _mint(msg.sender, msg.value); }
}

// Legacy tokens returning no ABI value must still work with SafeERC20.
contract NoReturnToken {
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;
    function mint(address to, uint256 amount) external { balanceOf[to] += amount; }
    function approve(address spender, uint256 amount) external { allowance[msg.sender][spender] = amount; }
    function transferFrom(address from, address to, uint256 amount) external {
        allowance[from][msg.sender] -= amount;
        balanceOf[from] -= amount;
        balanceOf[to] += amount;
    }
}

contract MockRouter {
    using SafeERC20 for IERC20;
    uint256 public outputBps = 10_000;
    address public lastRecipient;
    address public divertTo;
    bool public skipInput;
    address public callbackTarget;
    bytes public callbackData;
    bool public callbackSucceeded;
    bytes public callbackResult;
    function setOutputBps(uint256 bps) external { outputBps = bps; }
    function setDivertTo(address to) external { divertTo = to; }
    function setSkipInput(bool value) external { skipInput = value; }
    function setCallback(address target, bytes calldata data) external { callbackTarget = target; callbackData = data; }
    function swapExactTokensForTokensSupportingFeeOnTransferTokens(
        uint256 amount, uint256, address[] calldata path, address to, uint256 deadline
    ) external {
        require(deadline >= block.timestamp);
        lastRecipient = to;
        uint256 previous = IERC20(path[0]).balanceOf(address(this));
        if (!skipInput) IERC20(path[0]).safeTransferFrom(msg.sender, address(this), amount);
        if (callbackTarget != address(0)) {
            (callbackSucceeded, callbackResult) = callbackTarget.call(callbackData);
        }
        uint256 received = IERC20(path[0]).balanceOf(address(this)) - previous;
        uint256 output = (skipInput ? amount : received) * outputBps / 10_000;
        // Deliberately ignores minOut: the vault must independently enforce its balance-delta bound.
        IERC20(path[path.length - 1]).safeTransfer(divertTo == address(0) ? to : divertTo, output);
    }
}
