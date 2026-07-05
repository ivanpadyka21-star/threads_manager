# Mobile E2E Framework

A typed, Dockerised framework for end-to-end testing of mobile UIs through
Appium. This phase delivers the base architecture plus the session-management
module.

## Directory structure

```
mobile_e2e/
├── __init__.py            # public API: SessionManager, ProxyConfig
├── config/                # typed configuration
│   ├── settings.py        # AppiumSettings (pydantic-settings, env / .env)
│   └── capabilities.py    # build W3C capabilities + AppiumOptions
├── core/                  # framework core
│   ├── exceptions.py      # E2EFrameworkError hierarchy
│   ├── proxy.py           # ProxyConfig — parses IP:Port:Login:Password
│   └── session_manager.py # SessionManager — WebDriver lifecycle
├── workers/               # UI workers (page objects)
│   └── base_worker.py     # BaseWorker: find / tap / type_text / is_visible
├── scheduler/             # concurrent job scheduling
│   └── scheduler.py       # SessionScheduler, Job, JobResult
├── utils/                 # shared helpers
│   └── logger.py          # rich-based logging
├── tests/                 # pytest suite (proxy parsing has no server dep)
└── requirements.txt

docker/
├── Dockerfile             # test-runner image
└── docker-compose.yml     # appium service + runner (isolated env)
```

## Configuration

All settings come from `E2E_`-prefixed env vars or a `.env` file, so containers
are configured without code changes:

| Variable            | Default                 | Meaning                         |
|---------------------|-------------------------|---------------------------------|
| `E2E_SERVER_URL`    | `http://127.0.0.1:4723` | Appium server URL               |
| `E2E_PLATFORM_NAME` | `Android`               | `Android` or `iOS`              |
| `E2E_DEVICE_NAME`   | `emulator-5554`         | Target device                   |
| `E2E_AUTOMATION_NAME` | `UiAutomator2`        | Appium automation backend       |

## Usage

```python
from mobile_e2e import SessionManager

# Proxy passed as a single string: IP:Port:Login:Password (auth optional).
with SessionManager() as manager:
    driver = manager.create_session(proxy="10.0.0.1:8080:user:secret")
    # ... drive the UI via workers ...
# sessions are torn down automatically on exit
```

Proxy parsing is independent of Appium and can be used directly:

```python
from mobile_e2e import ProxyConfig

p = ProxyConfig.from_string("10.0.0.1:8080:user:secret")
p.url               # http://user:secret@10.0.0.1:8080  (credentials encoded)
p.as_capabilities() # {"proxyType": "manual", "httpProxy": ..., "sslProxy": ...}
str(p)              # http://user:***@10.0.0.1:8080     (password masked in logs)
```

Invalid strings raise `ProxyParseError`; a session that fails to start raises
`SessionStartupError` — both subclass `E2EFrameworkError`.

## Running

```bash
pip install -r mobile_e2e/requirements.txt

# Unit tests (proxy parsing needs no device/server)
pytest mobile_e2e/tests -v

# Full isolated environment
docker compose -f docker/docker-compose.yml up --build
```
