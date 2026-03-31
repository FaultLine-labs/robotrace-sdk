# Contributing to robotrace-sdk

Thank you for your interest in contributing to RoboTrace!

## Development Setup

```bash
git clone https://github.com/FaultLine-labs/robotrace-sdk.git
cd robotrace-sdk
pip install -e ".[dev]"
```

## Running Tests

```bash
# All tests (242)
pytest

# Specific module
pytest tests/test_ros2_converters.py

# With verbose output
pytest -v --tb=short
```

## Code Style

- Python 3.10+ (uses `X | Y` union syntax)
- Type hints on all public methods
- Docstrings on all public classes and methods
- No external dependencies beyond what's in `pyproject.toml`

## Adding a New Data Type

1. Add the dataclass to `robotrace/types.py` following the existing pattern (`@dataclass(frozen=True, slots=True)`)
2. Implement `type_name` property and `to_dict()` method
3. Add MCAP schema to `robotrace/recorder.py` `_TYPE_SCHEMAS`
4. Export from `robotrace/__init__.py`
5. Add tests to `tests/test_types.py`
6. If it maps to a ROS2 message type, add a converter to `robotrace/ros2/converters.py`

## Adding a ROS2 Converter

1. Add the converter function to `robotrace/ros2/converters.py`
2. Export from `robotrace/ros2/__init__.py`
3. Add the type to `_TYPE_MAP` in `robotrace/ros2/bridge.py`
4. Add tests with duck-typed mock messages to `tests/test_ros2_converters.py`

## Pull Request Process

1. Fork the repo and create a feature branch
2. Make your changes with tests
3. Run the full test suite: `pytest`
4. Submit a PR with a clear description

## Reporting Issues

Use [GitHub Issues](https://github.com/FaultLine-labs/robotrace-sdk/issues) for bug reports and feature requests.

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
