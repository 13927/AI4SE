# Contributing

Thanks for your interest in contributing to **aise**.

## Development setup

```bash
python -m pip install -e ".[dev]"
pytest -q
```

## Pull request checklist

- [ ] Tests pass locally (`pytest -q`)
- [ ] Any new/changed JSON assets have matching schema updates (if applicable)
- [ ] CLI behavior changes are reflected in `README.md`

