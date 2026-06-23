# Examples

In this directory you will find a collection of examples demonstrating tools and feature from the IsaacSim api client.

- assets (assets which can be used for the examples)
- nova_sdk (example related to the nova-sdk)

## Setup

To run the examples a python environment is needed. Use any dependency management you like. The NOVA-SDK uses [uv](https://docs.astral.sh/uv/), but you can also use e.g [poetry](https://python-poetry.org/) as well.

### Environment Variables
|Name|Example Value|
|-|-|
|ISAACSIM_API_URL|`http://127.0.0.1:8011/omniservice/api/v2`|

### Installing required packages

```
uv add wandelbots_isaacsim_api wandelbots-nova
```

```
poetry add wandelbots-isaacsim-api wandelbots-nova
```

## Run the example

If all dependencies are met, you can run any example. All examples are standalone python scripts which do not need any additional context

```
uv run ./nova_sdk/viewer.py
```

```
poetry run python3 ./nova_sdk/viewer.py
```