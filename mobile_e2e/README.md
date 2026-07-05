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
│   ├── base_worker.py     # BaseWorker: find / tap / type_text / is_visible
│   └── ui_worker.py       # UIWorker: read_screen_text / type_and_submit (retrying)
├── ai/                    # LLM test-data generation (no Appium dependency)
│   ├── agent.py           # AIAgent: generate_response(context) in a tone
│   └── settings.py        # AISettings (OpenAI or local Llama via base_url)
├── orchestrator/          # end-to-end pipeline + scheduling
│   ├── orchestrator.py    # TaskOrchestrator, Profile, WorkflowResult
│   └── example_schedule.py# asyncio recurring + parallel-profile examples
├── web/                   # Flask web control panel
│   ├── app.py             # Flask app factory + JSON routes
│   ├── service.py         # framework-independent wiring (unit tested)
│   ├── jobs.py            # in-memory background job runner
│   ├── templates/         # index.html
│   └── static/            # style.css, app.js
├── scheduler/             # concurrent job scheduling
│   └── scheduler.py       # SessionScheduler, Job, JobResult
├── utils/                 # shared helpers
│   ├── logger.py          # rich-based logging
│   └── retry.py           # retry_on_ui_error decorator (flaky-test resistance)
├── tests/                 # pytest suite (81 tests; no server/device needed)
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

### End-to-end pipeline

`TaskOrchestrator` chains session → read → generate → type and cleans up the
session no matter what. Failures are returned as `WorkflowResult`, not raised,
so a batch keeps going:

```python
from mobile_e2e.ai import AIAgent
from mobile_e2e.orchestrator import TaskOrchestrator

agent = AIAgent("You reply to chat messages.", tone="friendly, concise",
                fallback_response="Thanks for your message!")
orchestrator = TaskOrchestrator(agent, char_delay=0.05)

result = orchestrator.run_workflow(
    proxy_string="10.0.0.1:8080:user:pass",
    read_locator=("id", "incoming_message"),
    input_locator=("id", "reply_box"),
    submit_locator=("id", "send_button"),
)
print(result.ok, result.response)
```

Run many profiles (each with its own proxy and tone) sequentially or in
parallel, and put the batch on a recurring `asyncio` schedule — see
[orchestrator/example_schedule.py](orchestrator/example_schedule.py). For a
distributed setup, wrap `orchestrator.run_profile` in a Celery task instead; the
API is unchanged.

### Web UI

A small Flask site to configure and run a workflow from the browser:

```bash
python -m mobile_e2e.web        # then open http://127.0.0.1:5000
```

The page collects the Appium server, an optional `IP:Port:Login:Password` proxy,
the read/input/submit locators (each with a strategy dropdown: ID, Accessibility
ID, XPath, …) and the AI agent's role + tone. **Run workflow** starts a
background job and the page polls it, streaming framework logs and the final
result into the log pane. **Preview proxy** parses a proxy string (password
masked) without needing a device.

Endpoints:

| Method & path            | Purpose                                   |
|--------------------------|-------------------------------------------|
| `GET /`                  | The control panel page                    |
| `GET /api/strategies`    | Locator strategies                        |
| `POST /api/preview-proxy`| Masked proxy preview / 400 on bad input   |
| `POST /api/run`          | Start a workflow job → `{job_id}` (202)   |
| `GET /api/jobs/<id>`     | Job status, logs and result               |

All wiring lives in the Flask-independent `web/service.py`, unit tested along
with the routes (via the Flask test client) and the `JobManager`.

## Running

```bash
pip install -r mobile_e2e/requirements.txt

# Unit tests (no device/server needed — collaborators are mocked)
pytest mobile_e2e/tests -v

# Web control panel
python -m mobile_e2e.web

# Full isolated environment
docker compose -f docker/docker-compose.yml up --build
```
