"""
CLI tool to list, enable, or disable trading strategies.

Usage
-----
    python manage_strategies.py list
    python manage_strategies.py enable  ema_crossover_pro
    python manage_strategies.py disable ema_crossover_pro
"""

import sys
import strategies


def _print_list() -> None:
    all_strats = strategies.list_all()
    if not all_strats:
        print("No strategies registered.")
        return

    print(f"\n{'Strategy':<25} {'Status':<10} Description")
    print("-" * 75)
    for name, info in all_strats.items():
        status = "ENABLED" if info["enabled"] else "disabled"
        marker = "*" if info["enabled"] else " "
        print(f"  {marker} {name:<23} {status:<10} {info['description']}")
    print()


def main() -> None:
    args = sys.argv[1:]

    if not args or args[0] == "list":
        _print_list()
        return

    command = args[0]
    if command not in ("enable", "disable"):
        print(f"Unknown command: '{command}'")
        print("Usage: python manage_strategies.py [list | enable <name> | disable <name>]")
        sys.exit(1)

    if len(args) < 2:
        print(f"Missing strategy name. Usage: python manage_strategies.py {command} <name>")
        sys.exit(1)

    strategy_name = args[1]
    try:
        enabled = command == "enable"
        strategies.set_enabled(strategy_name, enabled)
        state = "enabled" if enabled else "disabled"
        print(f"Strategy '{strategy_name}' {state} successfully.")
        _print_list()
    except ValueError as exc:
        print(f"Error: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
