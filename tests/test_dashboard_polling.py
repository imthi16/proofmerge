"""Exercise the dashboard effect with offline promises and a fake timer queue."""
import shutil
import subprocess
from pathlib import Path

import pytest

APP = Path(__file__).parents[1] / "src/proofmerge/dashboard_static/app.js"


def test_poll_retries_after_failure_and_cleanup_stops_retry() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is needed to exercise the browser polling callback")
    source = APP.read_text()
    effect = source.split('      let active = true;', 1)[1].split('\n    }, [job]);', 1)[0]
    script = '''
const assert = require('node:assert/strict');
let queue = [], calls = 0, result;
const setTimeout = fn => { queue.push(fn); return fn; };
const clearTimeout = fn => { queue = queue.filter(x => x !== fn); };
const authGet = () => ++calls === 1 ? Promise.reject(Error('offline'))
  : Promise.resolve({job: {status: 'done'}});
const setJob = job => { result = job; };
const cleanup = (() => { let active = true; EFFECT })();
(async () => {
  await queue.shift()();
  assert.equal(queue.length, 1, 'failed request must schedule a retry');
  await queue.shift()();
  assert.equal(result.status, 'done');
  cleanup();
  assert.equal(queue.length, 0);
})();
'''.replace('EFFECT', effect)
    subprocess.run([node, '-e', script], check=True, capture_output=True, text=True)


def test_config_language_can_start_without_python_runner_image() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is needed to evaluate the browser form validation")
    source = APP.read_text()
    expression = source.split('    const blocker = ', 1)[1].split(';\n', 1)[0]
    script = '''
const assert = require('node:assert/strict');
const settings = {project: '/shop'}, info = {branches: ['main', 'change']};
const head = 'change', base = 'main', needsSandbox = true;
const health = {model_key: true, docker_running: true, runner_image: false};
const ctx = {busy: false};
assert.equal(EXPRESSION, '');
'''.replace('EXPRESSION', expression)
    subprocess.run([node, '-e', script], check=True, capture_output=True, text=True)
